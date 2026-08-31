"""hr_calculator 对抗性单测：边界值 / 非法输入 / 状态耦合。"""
from datetime import datetime, date

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
