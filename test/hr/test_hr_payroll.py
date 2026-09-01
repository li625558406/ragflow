"""hr_payroll 对抗性单测：个税临界点/跨月续算/覆盖/扣款边界。"""
from api.db.services.hr_payroll import (
    calc_attendance_deduction,
    calc_overtime_pay,
    calc_payslip,
    calc_tax_this_month,
)

RULE = {"late_deduction": 20, "absent_deduction_multiplier": 3,
        "overtime_rate_weekday": 15.0, "overtime_rate_weekend": 20.0,
        "holiday_overtime_multiplier": 3.0, "pay_days": 21.75,
        "social_rate": 0.105, "fund_rate": 0.12}

# ── 考勤扣款 ──

def test_deduction_late_and_absent():
    # 日薪 = 6525/21.75 = 300；迟到2次40 + 旷工1天900
    d = calc_attendance_deduction({"late_count": 2, "absent_days": 1}, 6525.0, RULE)
    assert abs(d - 940.0) < 0.01


def test_deduction_zero_when_clean():
    assert calc_attendance_deduction({"late_count": 0, "absent_days": 0}, 6525.0, RULE) == 0


def test_deduction_bad_inputs_safe():
    # 缺键/None/负数/非法类型均不抛异常，负值按 0 处理
    assert calc_attendance_deduction({}, 0.0, RULE) == 0
    assert calc_attendance_deduction({"late_count": None, "absent_days": None}, 6525.0, RULE) == 0
    assert calc_attendance_deduction({"late_count": -3, "absent_days": -1}, 6525.0, RULE) == 0
    # pay_days 配置为 0/None 时回退 21.75，不抛 ZeroDivisionError
    d = calc_attendance_deduction({"absent_days": 1}, 6525.0, {**RULE, "pay_days": 0})
    assert abs(d - 900.0) < 0.01
    d = calc_attendance_deduction({"absent_days": 1}, 6525.0, {**RULE, "pay_days": None})
    assert abs(d - 900.0) < 0.01


# ── 加班费 ──

def test_overtime_pay_mixed_types():
    d = calc_overtime_pay({"weekday": 2.0, "weekend": 1.0, "holiday": 1.0}, 6525.0, RULE)
    # 2*15 + 1*20 + 1*300*3 = 950
    assert abs(d - 950.0) < 0.01


def test_overtime_pay_empty_and_bad():
    assert calc_overtime_pay({}, 6525.0, RULE) == 0
    assert calc_overtime_pay({"weekday": None, "weekend": "abc"}, 6525.0, RULE) == 0


# ── 个税累计预扣 ──

def test_tax_first_month_low_income():
    # 月薪 5000 应税 0 → 0 税
    r = calc_tax_this_month(5000.0, 0.0, 0.0, 0.0, 1)
    assert r["this_tax"] == 0


def test_tax_bracket_boundary_36000():
    # 累计应税恰好 36000 → 3% 全档 1080；超 0.01 元进入 10% 档
    r1 = calc_tax_this_month(41000.0, 0.0, 0.0, 0.0, 1)    # taxable 36000
    assert abs(r1["this_tax"] - 1080.0) < 0.01
    r2 = calc_tax_this_month(41001.0, 0.0, 0.0, 0.0, 1)    # taxable 36001 → 10% 档 1080.1
    assert abs(r2["this_tax"] - 1080.1) < 0.01


def test_tax_high_bracket_144000():
    # 累计应税恰好 144000 → 144000*10%-2520 = 11880
    r = calc_tax_this_month(149000.0, 0.0, 0.0, 0.0, 1)
    assert abs(r["this_tax"] - 11880.0) < 0.01


def test_tax_first_month_deductions_subtracted():
    # 35000 - 5000 免税额 - 5000 社保 = 25000 → 750
    r = calc_tax_this_month(35000.0, 5000.0, 0.0, 0.0, 1)
    assert abs(r["this_tax"] - 750.0) < 0.01


def test_tax_cumulative_second_month_with_prev_snapshot():
    # 上月快照 cum_gross 40000 / cum_social 5000；本月 gross 35000
    # 累计应税 = (40000+35000) - 5000*2 - 5000 = 60000 → 总税 3480，本月 = 3480 - 900 = 2580
    prev = {"cum_gross": 40000.0, "cum_social": 5000.0,
            "cum_fund": 0.0, "cum_special": 0.0}
    r = calc_tax_this_month(35000.0, 0.0, 0.0, 900.0, 2, prev_snap=prev)
    assert abs(r["this_tax"] - 2580.0) < 0.01
    assert abs(r["cum_taxable"] - 60000.0) < 0.01
    assert abs(r["cum_gross"] - 75000.0) < 0.01
    assert abs(r["cum_tax"] - 3480.0) < 0.01


def test_tax_no_prev_snapshot_defaults_zero():
    # 上月无快照：cum 各项从 0 起算，仅扣当月社保/专项
    r = calc_tax_this_month(35000.0, 5000.0, 0.0, 0.0, 1, special_deduction=1000.0)
    # taxable = 35000 - 5000 - 5000 - 1000 = 24000 → 720
    assert abs(r["this_tax"] - 720.0) < 0.01


def test_tax_never_negative():
    r = calc_tax_this_month(5000.0, 0.0, 0.0, 999.0, 1)
    assert r["this_tax"] == 0  # 已缴>应缴 不退不补，本月 0


def test_tax_negative_taxable_clamps_zero():
    # 扣完社保/专项后应税为负 → 按 0 计
    r = calc_tax_this_month(5000.0, 3000.0, 1000.0, 0.0, 1, special_deduction=3000.0)
    assert r["this_tax"] == 0
    assert r["cum_taxable"] == 0


# ── 应发实发 ──

def test_payslip_full_formula():
    profile = {"base_salary": 6525.0, "post_allowance": 500, "meal_allowance": 300,
               "transport_allowance": 200, "social_base": 6000, "fund_base": 6000,
               "special_deduction": 1000, "overrides": {}}
    stats = {"attend_days": 20, "late_count": 1, "absent_days": 0,
             "overtime": {"weekday": 2.0, "weekend": 0.0, "holiday": 0.0}}
    r = calc_payslip(profile, stats, 6525.0, RULE, prev_snap=None, month_idx=1)
    # ot=30；gross=6525+1000+30=7555；deduct=20 + 630 + 720 + tax
    # 应税 = gross - 5000 - special 1000 - social 630 - fund 720 = 205 → 税 6.15
    assert abs(r["overtime_pay"] - 30.0) < 0.01
    assert abs(r["attendance_deduction"] - 20.0) < 0.01
    assert abs(r["net_pay"] - (7555 - 20 - 630 - 720 - 6.15)) < 0.05
    assert abs(r["gross_pay"] - 7555.0) < 0.01
    assert abs(r["social_insurance"] - 630.0) < 0.01
    assert abs(r["housing_fund"] - 720.0) < 0.01
    snap = r["tax_snapshot"]
    assert abs(snap["this_tax"] - 6.15) < 0.01
    assert not snap.get("overridden")


def test_payslip_overrides_take_effect():
    profile = {"base_salary": 6525.0, "post_allowance": 0, "meal_allowance": 0,
               "transport_allowance": 0, "social_base": 6000, "fund_base": 6000,
               "special_deduction": 0, "overrides": {"social": 500.0}}
    r = calc_payslip(profile, {"attend_days": 21, "late_count": 0, "absent_days": 0,
                               "overtime": {"weekday": 0, "weekend": 0, "holiday": 0}},
                     6525.0, RULE, prev_snap=None, month_idx=1)
    assert abs(r["social_insurance"] - 500.0) < 0.01


def test_payslip_overrides_tax_and_fund():
    profile = {"base_salary": 6525.0, "post_allowance": 0, "meal_allowance": 0,
               "transport_allowance": 0, "social_base": 6000, "fund_base": 6000,
               "special_deduction": 0, "overrides": {"tax": 111.0, "fund": 88.0}}
    r = calc_payslip(profile, {"attend_days": 21, "late_count": 0, "absent_days": 0,
                               "overtime": {"weekday": 0, "weekend": 0, "holiday": 0}},
                     6525.0, RULE, prev_snap=None, month_idx=1)
    assert abs(r["income_tax"] - 111.0) < 0.01
    assert abs(r["housing_fund"] - 88.0) < 0.01
    # 覆盖时 snapshot 留痕：overridden 标记 + 计算原值
    assert r["tax_snapshot"].get("overridden") is True
    assert "this_tax" in r["tax_snapshot"]


def test_payslip_profile_rate_overrides_rule():
    # 档案 individual 费率优先于全局 rule
    profile = {"base_salary": 6525.0, "post_allowance": 0, "meal_allowance": 0,
               "transport_allowance": 0, "social_base": 10000, "fund_base": 10000,
               "special_deduction": 0, "social_rate": 0.08, "fund_rate": None,
               "overrides": {}}
    r = calc_payslip(profile, {"attend_days": 21, "late_count": 0, "absent_days": 0,
                               "overtime": {"weekday": 0, "weekend": 0, "holiday": 0}},
                     6525.0, RULE, prev_snap=None, month_idx=1)
    assert abs(r["social_insurance"] - 800.0) < 0.01     # 10000*0.08
    assert abs(r["housing_fund"] - 1200.0) < 0.01        # 回退 rule 0.12


def test_payslip_absent_deduction_and_rounding():
    profile = {"base_salary": 3000.0, "post_allowance": 0, "meal_allowance": 0,
               "transport_allowance": 0, "social_base": 0, "fund_base": 0,
               "special_deduction": 0, "overrides": {}}
    stats = {"attend_days": 18, "late_count": 0, "absent_days": 1,
             "overtime": {"weekday": 0, "weekend": 0, "holiday": 0}}
    r = calc_payslip(profile, stats, 3000.0, RULE, prev_snap=None, month_idx=1)
    # 旷工扣 3000/21.75*3 = 413.79；无社保公积金；应税 3000-5000 → 0 税
    assert abs(r["attendance_deduction"] - 413.79) < 0.01
    assert r["income_tax"] == 0
    assert abs(r["net_pay"] - (3000.0 - 413.79)) < 0.01
