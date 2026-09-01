"""人事考勤推导引擎：纯函数、无 DB 依赖，可独立单测。

数据流：hr_attendance_record（原始流水）→ dedup_punch_records → derive_day_status
       → hr_attendance_day（日汇总）。月度汇总在 hr_service 层聚合日汇总。
"""
import json
import logging
import re
from datetime import date, datetime, time, timedelta

logger = logging.getLogger(__name__)

# 默认规则（hr_rule_config.config 的 JSON 结构；缺键逐项回退到这里）
DEFAULT_RULE = {
    "work_start": "09:00",          # 上班时间
    "work_end": "18:00",            # 下班时间
    "late_threshold_minutes": 10,   # 迟到阈值（分钟），恰好等于不算迟到
    "abnormal_start": "22:00",      # 半夜异常窗口起（含）
    "abnormal_end": "05:00",        # 半夜异常窗口止（不含 05:00 本身）
    "late_deduction": 20,           # 迟到扣款 元/次（P3 使用）
    "absent_deduction_multiplier": 3,  # 旷工扣 日薪倍数（P3 使用）
    "overtime_rate_weekday": 15.0,  # 工作日加班 元/小时（P3 使用）
    "overtime_rate_weekend": 20.0,  # 周末加班 元/小时（P3 使用）
    "holiday_overtime_multiplier": 3.0,  # 法定节假日 日薪倍数（P3 使用）
    "pay_days": 21.75,              # 月标准计薪天数（P3 使用）
    "social_rate": 0.105,           # 社保个人费率默认（养老8%+医疗2%+失业0.5%）
    "fund_rate": 0.12,              # 公积金个人费率默认
    "holidays": "",                 # 法定节假日列表，逗号分隔 YYYY-MM-DD（P3 加班/加班费使用）
    "annual_quota": 5,              # 年假默认额度 天/年（P2 使用）
    "sick_quota": 15,               # 病假默认额度 天/年（P2 使用）
    "marriage_quota": 3,            # 婚假默认额度 天（P2 使用）
    "maternity_quota": 98,          # 产假默认额度 天（P2 使用）
    "approval_chain": "",           # 审批链 user_id 逗号分隔；空回退超管列表（P2 使用）
    "approval_chain_long": "",      # ≥3天假单审批链；空则同 approval_chain（P2 使用）
}


def load_rule(config_json):
    """解析 rule_config JSON，缺键回退 DEFAULT_RULE；坏 JSON 整体回退。"""
    rule = dict(DEFAULT_RULE)
    if not config_json:
        return rule
    try:
        data = json.loads(config_json)
        if isinstance(data, dict):
            rule.update({k: v for k, v in data.items() if k in DEFAULT_RULE})
    except (ValueError, TypeError):
        logger.warning("hr rule config 解析失败，使用默认规则")
    return rule


def _parse_hm(value, fallback):
    """'09:00' -> time(9,0)；非法输入回退 fallback。"""
    try:
        parts = str(value).split(":")
        return time(int(parts[0]), int(parts[1]))
    except (ValueError, IndexError, TypeError):
        return fallback


def dedup_punch_records(records):
    """同一分钟多条打卡只保留最早一条；返回按 punch_time 升序的新列表。

    records 元素需含 'punch_time' (datetime)。非法元素（缺键/None）直接丢弃。
    """
    cleaned = []
    for r in records or []:
        pt = (r or {}).get("punch_time")
        if isinstance(pt, datetime):
            cleaned.append(pt)
    cleaned.sort()
    out, seen_minutes = [], set()
    for pt in cleaned:
        key = pt.replace(second=0, microsecond=0)
        if key in seen_minutes:
            continue
        seen_minutes.add(key)
        out.append({"punch_time": pt})
    return out


def _in_abnormal_window(pt, rule):
    start = _parse_hm(rule.get("abnormal_start"), time(22, 0))
    end = _parse_hm(rule.get("abnormal_end"), time(5, 0))
    t = pt.time()
    if start > end:  # 跨午夜窗口 22:00-05:00
        return t >= start or t < end
    return start <= t <= end


def _is_holiday(work_date, rule):
    return str(work_date) in [d.strip() for d in str(rule.get("holidays") or "").split(",") if d.strip()]


_HOLIDAY_ITEM_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def normalize_holidays(value):
    """规范化 holidays 规则值：逗号分隔逐项 strip。

    空值返回 ""；每项须为合法 YYYY-MM-DD 日期，否则返回 None（由调用方拒绝）。
    """
    s = str(value or "").strip()
    if not s:
        return ""
    items = [i.strip() for i in s.split(",")]
    for i in items:
        if not _HOLIDAY_ITEM_RE.match(i):
            return None
        try:
            date.fromisoformat(i)
        except ValueError:
            return None
    return ",".join(items)


def _calc_overtime_hours(punches, work_date, rule, day_status):
    """工作日: last_out 超过 work_end 的时长；休息日/节假日: 首末打卡跨度。
    非 normal/late/rest 状态不计加班。保留 2 位小数。"""
    if day_status not in ("normal", "late", "rest") or not punches:
        return 0.0
    last = punches[-1]["punch_time"]
    if work_date.weekday() < 5 and not _is_holiday(work_date, rule):
        work_end = _parse_hm(rule.get("work_end"), time(18, 0))
        delta = last - datetime.combine(work_date, work_end)
        return round(max(0.0, delta.total_seconds() / 3600), 2)
    first = punches[0]["punch_time"]
    return round(max(0.0, (last - first).total_seconds() / 3600), 2)


def derive_day_status(records, work_date, rule, leave_status=None):
    """推导某员工某天的考勤状态。

    返回 {status, first_in, last_out, late_minutes, punch_count}。
    status ∈ normal|late|rest|missing|abnormal|leave|business_trip
    （absent 由月度确认时从 missing 转换，不在此函数产生）。
    优先级：有效假单 > abnormal > rest > missing > 迟到/正常判定。
    """
    status = {
        "status": "missing", "first_in": None, "last_out": None,
        "late_minutes": 0, "punch_count": 0, "overtime_hours": 0.0,
    }
    punches = dedup_punch_records(records)
    status["punch_count"] = len(punches)
    if punches:
        status["first_in"] = punches[0]["punch_time"]
        status["last_out"] = punches[-1]["punch_time"]

    # 1) 有效假单优先（P2 起由日汇总回写时传入）
    if leave_status in ("leave", "business_trip"):
        status["status"] = leave_status
        return status

    # 2) 半夜异常打卡（只要有异常窗口内的打卡即整日标记待人工确认）
    if any(_in_abnormal_window(p["punch_time"], rule) for p in punches):
        status["status"] = "abnormal"
        return status

    # 3) 休息日（周末）与法定节假日：不缺卡概念、不判迟到/旷工（C1 修复：
    #    工作日法定节假日无打卡曾走 missing→absent 被误扣 3 倍日薪），
    #    打卡照记（供 P3 加班统计，节假日按首末跨度计加班）
    if work_date.weekday() >= 5 or _is_holiday(work_date, rule):
        status["status"] = "rest"
        status["overtime_hours"] = _calc_overtime_hours(punches, work_date, rule, "rest")
        return status

    # 4) 工作日无打卡 → 缺卡（月度确认时 missing→absent）
    if not punches:
        status["status"] = "missing"
        return status

    # 5) 迟到判定：最早打卡时间 > 上班时间 + 阈值
    work_start = _parse_hm(rule.get("work_start"), time(9, 0))
    try:
        threshold = max(0, int(rule.get("late_threshold_minutes", 10) or 0))
    except (ValueError, TypeError, OverflowError):
        threshold = 10
    limit = datetime.combine(work_date, work_start) + timedelta(minutes=threshold)
    first_in = status["first_in"]
    if first_in > limit:
        status["status"] = "late"
        status["late_minutes"] = int(
            (first_in - datetime.combine(work_date, work_start)).total_seconds() // 60)
    else:
        status["status"] = "normal"
    status["overtime_hours"] = _calc_overtime_hours(punches, work_date, rule, status["status"])
    return status


def leave_status_for_date(leave_requests, work_date):
    """当日被 approved 的假单覆盖时返回 'leave'/'business_trip'，否则 None。

    business_trip → 'business_trip'；其余所有非 repair 类型（事假/病假/年假/
    婚假/产假/other，含历史 'leave' 类型）→ 'leave'；repair 不参与日状态推导
    （它修正打卡流水而非状态）。列表序优先：同日多张假单取第一张命中。
    """
    for r in leave_requests or []:
        try:
            lt = r.get("leave_type")
            if r.get("status") != "approved" or lt in (None, "repair"):
                continue
            if r["start_date"] <= work_date <= r["end_date"]:
                return "business_trip" if lt == "business_trip" else "leave"
        except (KeyError, TypeError):
            continue
    return None
