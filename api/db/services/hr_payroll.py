"""薪资核算引擎：纯函数、无 DB 依赖。金额一律 float 进 round(2) 出。

个税累计预扣：cum_* 累计口径 = 上月 payslip.tax_snapshot（prev_snap）+ 当月值；
上月无快照时 cum 从 0 起算（按当月首月起算，见计划偏差注释）。
"""
import math

TAX_BRACKETS = [  # (累计应税所得额上限, 税率, 速算扣除数)
    (36000.0, 0.03, 0.0), (144000.0, 0.10, 2520.0), (300000.0, 0.20, 16920.0),
    (420000.0, 0.25, 31920.0), (660000.0, 0.30, 52920.0),
    (960000.0, 0.35, 85920.0), (float("inf"), 0.45, 181920.0),
]
MONTH_EXEMPTION = 5000.0  # 起征点/月


def _num(value, default=0.0):
    """宽松转 float：None/非法/非有限值回退 default，负值取 0。"""
    try:
        f = float(value)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(f):  # NaN/Inf
        return default
    return max(0.0, f)


def calc_attendance_deduction(stats, base_salary, rule):
    """迟到 late_deduction/次 + 旷工 absent_deduction_multiplier 倍日薪/天。"""
    pay_days = _num(rule.get("pay_days"), 21.75) or 21.75
    daily = _num(base_salary) / pay_days
    late = _num(stats.get("late_count")) * _num(rule.get("late_deduction"), 20.0)
    absent = (_num(stats.get("absent_days"))
              * _num(rule.get("absent_deduction_multiplier"), 3.0) * daily)
    return round(late + absent, 2)


def calc_overtime_pay(ot_breakdown, base_salary, rule):
    """weekday 小时*单价 + weekend 小时*单价 + holiday 小时*倍数*日薪。"""
    pay_days = _num(rule.get("pay_days"), 21.75) or 21.75
    daily = _num(base_salary) / pay_days
    wd = _num(ot_breakdown.get("weekday")) * _num(rule.get("overtime_rate_weekday"), 15.0)
    we = _num(ot_breakdown.get("weekend")) * _num(rule.get("overtime_rate_weekend"), 20.0)
    ho = (_num(ot_breakdown.get("holiday"))
          * _num(rule.get("holiday_overtime_multiplier"), 3.0) * daily)
    return round(wd + we + ho, 2)


def _tax_on_cumulative(cum_taxable):
    for limit, rate, quick in TAX_BRACKETS:
        if cum_taxable <= limit:
            return max(0.0, cum_taxable * rate - quick)
    return 0.0


def calc_tax_this_month(gross, social, fund, prev_tax_paid, month_idx,
                        special_deduction=0.0, prev_snap=None):
    """个税累计预扣：返回 {this_tax, cum_taxable, cum_tax, cum_gross, cum_social,
    cum_fund, cum_special}（cum_* 供下月 snapshot 串联）。

    cum_taxable = cum_gross - 5000*month_idx - cum_social - cum_fund - cum_special（下限 0）；
    prev_snap 为上月 tax_snapshot（缺键按 0）；None 时累计从 0 起算（当月首月口径）。
    """
    prev = prev_snap or {}
    cum_gross = _num(prev.get("cum_gross")) + _num(gross)
    cum_social = _num(prev.get("cum_social")) + _num(social)
    cum_fund = _num(prev.get("cum_fund")) + _num(fund)
    cum_special = _num(prev.get("cum_special")) + _num(special_deduction)
    months = max(1, int(_num(month_idx, 1)))
    cum_taxable = max(0.0, cum_gross - MONTH_EXEMPTION * months
                      - cum_social - cum_fund - cum_special)
    total = round(_tax_on_cumulative(cum_taxable), 2)
    this = round(max(0.0, total - _num(prev_tax_paid)), 2)
    return {"this_tax": this, "cum_taxable": round(cum_taxable, 2), "cum_tax": total,
            "cum_gross": round(cum_gross, 2), "cum_social": round(cum_social, 2),
            "cum_fund": round(cum_fund, 2), "cum_special": round(cum_special, 2)}


def _insurance(base, profile_rate, rule_rate_key, rule, override):
    """社保/公积金月缴额：覆盖值优先；否则 档案个人费率 > 全局 rule 费率（默认兜底）。"""
    if override is not None:
        return _num(override), False
    rate = profile_rate if profile_rate is not None else _num(rule.get(rule_rate_key), 0.105)
    rate = _num(rate, 0.105)
    return round(_num(base) * rate, 2), True


def calc_payslip(profile, stats, base_salary, rule, prev_snap=None, month_idx=1):
    """应发实发核算：返回金额明细 dict + tax_snapshot（dict，落盘由调用方 json.dumps）。

    手工覆盖（profile["overrides"] 的 social/fund/tax 数值键）命中时实发用覆盖值，
    计算原值仍记入 tax_snapshot 留痕（overridden=true）。
    """
    overrides = profile.get("overrides") or {}
    base = round(_num(base_salary), 2)
    allowances = round(_num(profile.get("post_allowance"))
                       + _num(profile.get("meal_allowance"))
                       + _num(profile.get("transport_allowance")), 2)
    ot_pay = calc_overtime_pay(stats.get("overtime") or {}, base, rule)
    gross_pay = round(base + allowances + ot_pay, 2)

    soc_override = overrides.get("social")
    social, soc_calc = _insurance(profile.get("social_base"), profile.get("social_rate"),
                                  "social_rate", rule, soc_override)
    fund, fund_calc = _insurance(profile.get("fund_base"), profile.get("fund_rate"),
                                 "fund_rate", rule, overrides.get("fund"))
    social = round(_num(social), 2)
    fund = round(_num(fund), 2)

    deduction = calc_attendance_deduction(stats, base, rule)
    special = _num(profile.get("special_deduction"))
    tax_r = calc_tax_this_month(gross_pay, social, fund,
                                (prev_snap or {}).get("cum_tax", 0.0),
                                month_idx, special_deduction=special, prev_snap=prev_snap)
    tax_snapshot = dict(tax_r)
    if overrides.get("tax") is not None:
        tax_snapshot["overridden"] = True
        income_tax = round(_num(overrides.get("tax")), 2)
    else:
        income_tax = tax_r["this_tax"]

    net_pay = round(gross_pay - deduction - social - fund - income_tax, 2)
    return {
        "base_salary": base, "allowances": allowances, "overtime_pay": ot_pay,
        "gross_pay": gross_pay, "attendance_deduction": deduction,
        "social_insurance": social, "housing_fund": fund,
        "income_tax": income_tax, "net_pay": net_pay,
        "tax_snapshot": tax_snapshot,
        "_soc_calculated": soc_calc, "_fund_calculated": fund_calc,
    }


# ── P4: 财务凭证 entries + 工资手工调整 ──

VOUCHER_TYPES = ("accrue", "pay")
ADJUSTABLE_FIELDS = ("attendance_deduction", "social_insurance", "housing_fund", "income_tax")


def build_voucher_entries(payslip_rows, voucher_type):
    """全月汇总凭证 entries：[[摘要,科目,借,贷],...]。借贷平衡由构造保证。
    accrue: 借 管理费用—工资 = Σgross / 贷 应付职工薪酬
    pay:    借 应付职工薪酬 = Σgross / 贷 个税+社保+公积金+银行存款(=Σnet+Σatt)"""
    if voucher_type not in VOUCHER_TYPES:
        raise ValueError(f"无效凭证类型：{voucher_type}")
    if not payslip_rows:
        return {"entries": [], "total_amount": 0.0}

    def tot(k):
        return round(sum(_num(p.get(k)) for p in payslip_rows), 2)

    gross = tot("gross_pay")
    social, fund = tot("social_insurance"), tot("housing_fund")
    tax = tot("income_tax")
    if voucher_type == "accrue":
        entries = [[f"计提{len(payslip_rows)}人月度工资", "管理费用—工资", gross, 0.0],
                   ["结转应付职工薪酬", "应付职工薪酬", 0.0, gross]]
    else:
        # 发放：net = gross − tax − social − fund − att（calc_payslip 恒等式），
        # 银行存款 = gross − tax − social − fund = Σnet + Σatt，与贷方合计恒等。
        # 脏数据防御：逐行显式校验恒等式（round(2) 后比较；原 assert 恒真且 -O 下被剥离）
        for idx, p in enumerate(payslip_rows):
            expect = round(_num(p.get("gross_pay")) - _num(p.get("attendance_deduction"))
                           - _num(p.get("social_insurance")) - _num(p.get("housing_fund"))
                           - _num(p.get("income_tax")), 2)
            if abs(round(_num(p.get("net_pay")), 2) - expect) >= 0.01:
                who = f" {p.get('employee_id')}" if p.get("employee_id") else f" 第 {idx + 1} 行"
                raise ValueError(f"工资单{who}金额不平衡，请先核算修正")
        bank = round(gross - tax - social - fund, 2)
        entries = [["发放月度工资", "应付职工薪酬", gross, 0.0],
                   ["代扣个人所得税", "应交税费—应交个人所得税", 0.0, tax],
                   ["代扣社保个人部分", "其他应付款—社保", 0.0, social],
                   ["代扣公积金个人部分", "其他应付款—公积金", 0.0, fund],
                   ["实发工资", "银行存款", 0.0, bank]]
    return {"entries": entries, "total_amount": gross}


def apply_adjustment(payslip_dict, field, new_value):
    """手工调整重算：field 白名单 + 非负校验；net = gross - 四项扣款。返回新 dict（不改入参）。

    注意不能用 _num(new_value, None) 直接判负——_num 会把负值钳成 0 静默通过，
    须先对原始值做有限性/非负校验（对抗性用例：-5 必须显式拒绝）。
    上限 99_999_999 对齐 DecimalField(10,2)，防超限落库 DataError（bool 会命中
    isinstance int 分支，须先行排除）。
    """
    if field not in ADJUSTABLE_FIELDS:
        raise ValueError(f"不可调整的字段：{field}")
    if isinstance(new_value, bool) or not isinstance(new_value, (int, float)):
        # 类型错误统一走 ValueError：Service 层仅捕获 ValueError 转 400 文案
        raise ValueError("调整值必须是非负数字")  # noqa: TRY004
    v = float(new_value)
    if not math.isfinite(v) or v < 0:
        raise ValueError("调整值必须是非负数字")
    if v > 99_999_999:
        raise ValueError("调整值超出上限")
    r = dict(payslip_dict)
    r[field] = round(v, 2)
    r["net_pay"] = round(float(r["gross_pay"]) - r["attendance_deduction"]
                         - r["social_insurance"] - r["housing_fund"] - r["income_tax"], 2)
    return r
