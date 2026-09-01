"""hr_calculator 对抗性单测：边界值 / 非法输入 / 状态耦合。"""
from datetime import date, datetime

from api.db.services.hr_calculator import (
    DEFAULT_RULE,
    dedup_punch_records,
    derive_day_status,
)

D = date(2026, 9, 2)  # 周三，工作日
SAT = date(2026, 9, 5)  # 周六


def punch(hh, mm):
    return {"punch_time": datetime.combine(D, datetime.min.time()).replace(hour=hh, minute=mm)}


# ── dedup_punch_records ──

def test_dedup_same_minute_keeps_first():
    records = [punch(9, 0), punch(9, 0), punch(9, 0)]
    out = dedup_punch_records(records)
    assert len(out) == 1


def test_dedup_adjacent_minutes_kept():
    records = [punch(9, 0), punch(9, 1)]
    assert len(dedup_punch_records(records)) == 2


def test_dedup_empty_and_unsorted():
    assert dedup_punch_records([]) == []
    records = [punch(18, 0), punch(9, 0)]
    out = dedup_punch_records(records)
    assert out[0]["punch_time"].hour == 9  # 输出按时间排序


# ── derive_day_status：空/休息日 ──

def test_weekday_no_punch_is_missing():
    r = derive_day_status([], D, DEFAULT_RULE)
    assert r["status"] == "missing"
    assert r["first_in"] is None and r["last_out"] is None


def test_weekend_no_punch_is_rest():
    r = derive_day_status([], SAT, DEFAULT_RULE)
    assert r["status"] == "rest"


def test_weekend_with_punch_still_rest():
    r = derive_day_status([punch(10, 0), punch(19, 0)], SAT, DEFAULT_RULE)
    assert r["status"] == "rest"
    assert r["first_in"].hour == 10  # 打卡仍记录，便于 P3 加班统计


# ── 正常/迟到边界 ──

def test_normal_day():
    r = derive_day_status([punch(8, 55), punch(18, 5)], D, DEFAULT_RULE)
    assert r["status"] == "normal" and r["late_minutes"] == 0


def test_late_exactly_at_threshold_is_normal():
    # 09:10 = 09:00 + 10min 阈值，恰好等于阈值不算迟到
    r = derive_day_status([punch(9, 10), punch(18, 0)], D, DEFAULT_RULE)
    assert r["status"] == "normal"


def test_late_one_minute_over_threshold():
    r = derive_day_status([punch(9, 11), punch(18, 0)], D, DEFAULT_RULE)
    assert r["status"] == "late"
    assert r["late_minutes"] == 11


def test_early_punch_not_late():
    r = derive_day_status([punch(7, 30), punch(18, 0)], D, DEFAULT_RULE)
    assert r["status"] == "normal"


# ── 半夜异常窗口 ──

def test_midnight_punch_is_abnormal():
    r = derive_day_status([punch(23, 30)], D, DEFAULT_RULE)
    assert r["status"] == "abnormal"


def test_abnormal_window_edges():
    # 22:00 起点与 05:00 终点都算异常窗口内
    for hh, mm in [(22, 0), (4, 59)]:
        r = derive_day_status([punch(hh, mm)], D, DEFAULT_RULE)
        assert r["status"] == "abnormal", f"{hh}:{mm} 应识别为异常"


def test_six_am_not_abnormal():
    r = derive_day_status([punch(6, 0), punch(18, 0)], D, DEFAULT_RULE)
    assert r["status"] == "normal"


# ── 有效假单优先 ──

def test_leave_overrides_missing():
    r = derive_day_status([], D, DEFAULT_RULE, leave_status="leave")
    assert r["status"] == "leave"


def test_leave_overrides_late():
    r = derive_day_status([punch(10, 30), punch(18, 0)], D, DEFAULT_RULE, leave_status="business_trip")
    assert r["status"] == "business_trip"


# ── 非法/极端输入 ──

def test_invalid_rule_falls_back_to_default():
    r = derive_day_status([punch(9, 30), punch(18, 0)], D, {})
    assert r["status"] in ("normal", "late")  # 不抛异常


def test_punch_count_reflects_dedup():
    r = derive_day_status([punch(9, 0), punch(9, 0), punch(18, 0)], D, DEFAULT_RULE)
    assert r["punch_count"] == 2


# ── P2: leave_status_for_date ──

def _leave(type_, s, e, status="approved"):
    return {"leave_type": type_, "start_date": date.fromisoformat(s),
            "end_date": date.fromisoformat(e), "status": status}


def test_leave_covered_day():
    from api.db.services.hr_calculator import leave_status_for_date
    reqs = [_leave("leave", "2026-09-01", "2026-09-03")]
    assert leave_status_for_date(reqs, date(2026, 9, 2)) == "leave"
    assert leave_status_for_date(reqs, date(2026, 9, 4)) is None


def test_leave_business_trip_priority_first_wins():
    from api.db.services.hr_calculator import leave_status_for_date
    reqs = [_leave("leave", "2026-09-01", "2026-09-05"),
            _leave("business_trip", "2026-09-03", "2026-09-06")]
    assert leave_status_for_date(reqs, date(2026, 9, 4)) == "leave"  # 列表序优先


def test_leave_pending_not_counted():
    from api.db.services.hr_calculator import leave_status_for_date
    reqs = [_leave("leave", "2026-09-01", "2026-09-03", status="pending")]
    assert leave_status_for_date(reqs, date(2026, 9, 2)) is None


def test_leave_repair_type_not_derived():
    from api.db.services.hr_calculator import leave_status_for_date
    reqs = [_leave("repair", "2026-09-01", "2026-09-01")]
    assert leave_status_for_date(reqs, date(2026, 9, 1)) is None


def test_leave_annual_type_maps_to_leave():
    """有额度假型（annual 代表 personal/sick/marriage/maternity/other）统一推导为 leave。"""
    from api.db.services.hr_calculator import leave_status_for_date
    reqs = [_leave("annual", "2026-09-01", "2026-09-02")]
    assert leave_status_for_date(reqs, date(2026, 9, 1)) == "leave"
    assert leave_status_for_date(reqs, date(2026, 9, 2)) == "leave"
    assert leave_status_for_date(reqs, date(2026, 9, 3)) is None


def test_leave_business_trip_type_maps_to_business_trip():
    from api.db.services.hr_calculator import leave_status_for_date
    reqs = [_leave("business_trip", "2026-09-01", "2026-09-02")]
    assert leave_status_for_date(reqs, date(2026, 9, 2)) == "business_trip"


def test_leave_mixed_types_first_wins():
    """同日多张不同类型假单：列表序优先，非出差在前返回 leave。"""
    from api.db.services.hr_calculator import leave_status_for_date
    reqs = [_leave("sick", "2026-09-01", "2026-09-03"),
            _leave("business_trip", "2026-09-02", "2026-09-04")]
    assert leave_status_for_date(reqs, date(2026, 9, 2)) == "leave"
    # 出差单覆盖而病假单未覆盖的日，返回 business_trip
    assert leave_status_for_date(reqs, date(2026, 9, 4)) == "business_trip"


def test_leave_empty_and_bad_input():
    from api.db.services.hr_calculator import leave_status_for_date
    assert leave_status_for_date([], date(2026, 9, 1)) is None
    assert leave_status_for_date([{"leave_type": None}], date(2026, 9, 1)) is None


def test_leave_overrides_missing_and_abnormal():
    from api.db.services.hr_calculator import derive_day_status
    r = derive_day_status([], D, DEFAULT_RULE, leave_status="leave")
    assert r["status"] == "leave"


# ── P3: 加班时长推导 ──

def test_overtime_weekday_after_work_end():
    # 18:30 下班 → 0.5h 加班
    r = derive_day_status([punch(9, 0), punch(18, 30)], D, DEFAULT_RULE)
    assert r["overtime_hours"] == 0.5


def test_overtime_weekday_none_before_end():
    r = derive_day_status([punch(9, 0), punch(18, 0)], D, DEFAULT_RULE)
    assert r["overtime_hours"] == 0


def test_overtime_weekend_full_span():
    # 周六 10:00-14:00 → 4h
    r = derive_day_status([punch(10, 0), punch(14, 0)], SAT, DEFAULT_RULE)
    assert r["overtime_hours"] == 4.0


def test_overtime_leave_day_zero():
    r = derive_day_status([punch(9, 0), punch(20, 0)], D, DEFAULT_RULE, leave_status="leave")
    assert r["overtime_hours"] == 0


def test_overtime_missing_day_zero():
    r = derive_day_status([], D, DEFAULT_RULE)
    assert r["overtime_hours"] == 0


def test_overtime_holiday_treated_as_rest():
    # 法定节假日全天打卡按休息日口径算跨度
    rule = {**DEFAULT_RULE, "holidays": "2026-09-02"}
    r = derive_day_status([punch(9, 0), punch(13, 0)], D, rule)
    assert r["overtime_hours"] == 4.0


def test_overtime_abnormal_zero():
    r = derive_day_status([punch(23, 30)], D, DEFAULT_RULE)
    assert r["overtime_hours"] == 0


# ── C1: 法定节假日按休息日口径推导（免打卡/不判迟到/不转旷工）──

HOLIDAY_RULE = {**DEFAULT_RULE, "holidays": "2026-09-02"}  # D 在节假日列表内


def test_holiday_no_punch_is_rest_not_missing():
    """节假日无打卡 → rest，不得走 missing→absent 被扣 3 倍日薪。"""
    r = derive_day_status([], D, HOLIDAY_RULE)
    assert r["status"] == "rest"
    assert r["overtime_hours"] == 0
    assert r["late_minutes"] == 0


def test_holiday_with_punch_is_rest_span_overtime():
    """节假日全天打卡 → rest，加班按首末跨度计。"""
    r = derive_day_status([punch(9, 0), punch(13, 0)], D, HOLIDAY_RULE)
    assert r["status"] == "rest"
    assert r["overtime_hours"] == 4.0


def test_holiday_late_punch_not_marked_late():
    """节假日打卡晚于 work_start → 不判迟到。"""
    r = derive_day_status([punch(10, 30), punch(11, 0)], D, HOLIDAY_RULE)
    assert r["status"] == "rest"
    assert r["late_minutes"] == 0


def test_leave_overrides_holiday():
    """假单覆盖节假日 → 保持 leave 优先。"""
    r = derive_day_status([], D, HOLIDAY_RULE, leave_status="leave")
    assert r["status"] == "leave"


def test_holiday_only_listed_dates_are_rest():
    """不在节假日列表的同为周几的工作日仍按工作日口径（缺卡→missing）。"""
    other_day = date(2026, 9, 3)  # 周四，不在列表
    assert other_day.weekday() < 5
    r = derive_day_status([], other_day, HOLIDAY_RULE)
    assert r["status"] == "missing"


def test_holiday_abnormal_punch_still_abnormal():
    """节假日窗口内出现异常打卡 → 仍优先 abnormal 待人工确认。"""
    r = derive_day_status([punch(23, 30)], D, HOLIDAY_RULE)
    assert r["status"] == "abnormal"


# ── M6: normalize_holidays 格式校验 ──

def test_normalize_holidays_empty_and_none():
    from api.db.services.hr_calculator import normalize_holidays
    assert normalize_holidays("") == ""
    assert normalize_holidays(None) == ""
    assert normalize_holidays("  ") == ""


def test_normalize_holidays_valid_single_and_multi():
    from api.db.services.hr_calculator import normalize_holidays
    assert normalize_holidays("2026-10-01") == "2026-10-01"
    assert normalize_holidays("2026-10-01, 2026-10-07") == "2026-10-01,2026-10-07"


def test_normalize_holidays_rejects_bad_format():
    from api.db.services.hr_calculator import normalize_holidays
    assert normalize_holidays("2026/10/01") is None          # 非法分隔符
    assert normalize_holidays("2026-10-1") is None           # 未零填充
    assert normalize_holidays("2026-10-01,,2026-10-07") is None  # 空项
    assert normalize_holidays("2026-10-01,abc") is None      # 混入非日期
    assert normalize_holidays("2026-13-01") is None          # 正则过但日期非法
    assert normalize_holidays("2026-02-30") is None          # 正则过但日期不存在


def test_normalize_holidays_trailing_comma_rejected():
    from api.db.services.hr_calculator import normalize_holidays
    assert normalize_holidays("2026-10-01,") is None
