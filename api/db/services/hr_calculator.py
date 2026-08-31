"""人事考勤推导引擎：纯函数、无 DB 依赖，可独立单测。

数据流：hr_attendance_record（原始流水）→ dedup_punch_records → derive_day_status
       → hr_attendance_day（日汇总）。月度汇总在 hr_service 层聚合日汇总。
"""
import json
import logging
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


def derive_day_status(records, work_date, rule, leave_status=None):
    """推导某员工某天的考勤状态。

    返回 {status, first_in, last_out, late_minutes, punch_count}。
    status ∈ normal|late|rest|missing|abnormal|leave|business_trip
    （absent 由月度确认时从 missing 转换，不在此函数产生）。
    优先级：有效假单 > abnormal > rest > missing > 迟到/正常判定。
    """
    status = {
        "status": "missing", "first_in": None, "last_out": None,
        "late_minutes": 0, "punch_count": 0,
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

    # 3) 休息日（周末）：不缺卡概念，打卡照记（供 P3 加班统计）
    if work_date.weekday() >= 5:
        status["status"] = "rest"
        return status

    # 4) 工作日无打卡 → 缺卡（月度确认时 missing→absent）
    if not punches:
        status["status"] = "missing"
        return status

    # 5) 迟到判定：最早打卡时间 > 上班时间 + 阈值
    work_start = _parse_hm(rule.get("work_start"), time(9, 0))
    try:
        threshold = max(0, int(rule.get("late_threshold_minutes", 10) or 0))
    except (ValueError, TypeError):
        threshold = 10
    limit = datetime.combine(work_date, work_start) + timedelta(minutes=threshold)
    first_in = status["first_in"]
    if first_in > limit:
        status["status"] = "late"
        status["late_minutes"] = int(
            (first_in - datetime.combine(work_date, work_start)).total_seconds() // 60)
    else:
        status["status"] = "normal"
    return status
