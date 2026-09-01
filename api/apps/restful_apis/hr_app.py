"""人事模块 REST API（/api/v1/hr/*）。

权限模型：
- 员工自助接口（打卡/日历/我的档案）：login_required，且必须有 hr_employee 档案
- HR 管理接口（建档/全员汇总/补卡/月度归档/规则配置写）：@permission_required("hr_manage")
"""
import json
import logging
import math
import re
from datetime import date, datetime
from io import BytesIO
from urllib.parse import quote

from openpyxl import Workbook
from quart import Blueprint, Response, request

from api.apps import current_user, login_required
from api.db.db_models import (
    HrAttendanceDay,
    HrAttendanceMonth,
    HrEmployee,
    HrLeaveStep,
    HrPayslip,
    HrPayslipAdjust,
    HrSalaryProfile,
    User,
)
from api.db.services.hr_calculator import (
    derive_day_status,
    leave_status_for_date,
    normalize_holidays,
)
from api.db.services.hr_service import (
    QUOTA_KEY,
    HrAttendanceDayService,
    HrAttendanceImportService,
    HrAttendanceMonthService,
    HrAttendanceRecordService,
    HrEmployeeService,
    HrLeaveBalanceService,
    HrLeaveRequestService,
    HrPayslipService,
    HrRuleConfigService,
    HrSalaryProfileService,
    HrVoucherService,
)
from api.utils.api_utils import get_data_error_result, get_json_result
from api.utils.permission_utils import (
    get_cached_user_permissions,
    permission_allowed,
    permission_required,
)
from common.time_utils import current_timestamp

logger = logging.getLogger(__name__)

manager = Blueprint("rest_hr_app", __name__)


def _require_employee():
    """当前登录用户必须有在职员工档案；返回 (employee, None) 或 (None, 响应)。"""
    emp = HrEmployeeService.get_by_user(current_user.id)
    if not emp or emp.status != "active":
        return None, get_json_result(code=1004, message="未开通人事功能：请联系HR建档")
    return emp, None


def _client_ip() -> str:
    fwd = request.headers.get("X-Forwarded-For", "")
    return (fwd.split(",")[0].strip() if fwd else request.remote_addr) or ""


_MONTH_RE = re.compile(r"^(\d{4})-(0[1-9]|1[0-2])$")


def _parse_month(month: str):
    """'YYYY-MM'（严格零填充）-> (year, month)；非法返回 None。"""
    m = _MONTH_RE.match(month or "")
    return (int(m.group(1)), int(m.group(2))) if m else None


def _emp_dict(emp) -> dict:
    return {
        "id": emp.id, "user_id": emp.user_id, "emp_no": emp.emp_no,
        "department": emp.department, "position": emp.position,
        "entry_date": str(emp.entry_date or ""), "status": emp.status,
    }


def _day_dict(row) -> dict:
    return {
        "work_date": str(row.work_date), "status": row.status,
        "first_in": str(row.first_in or ""), "last_out": str(row.last_out or ""),
        "late_minutes": row.late_minutes, "locked": row.locked,
    }


def _today_payload(emp) -> dict:
    today = date.today()
    records = HrAttendanceRecordService.list_day(emp.id, today)
    rule = HrRuleConfigService.get_config()
    leaves = HrLeaveRequestService.approved_requests(emp.id, today, today)
    derived = derive_day_status(records, today, rule,
                                leave_status=leave_status_for_date(leaves, today))
    return {
        "work_date": str(today),
        "status": derived["status"],
        "first_in": str(derived["first_in"] or ""),
        "last_out": str(derived["last_out"] or ""),
        "late_minutes": derived["late_minutes"],
        "records": [{"punch_time": str(r.punch_time), "source": r.source,
                     "ip_address": r.ip_address} for r in records],
    }


# ── 员工自助 ──

@manager.route("/hr/employee/me", methods=["GET"])
@login_required
async def hr_employee_me():
    emp = HrEmployeeService.get_by_user(current_user.id)
    if not emp:
        return get_json_result(data={"profile": None})
    return get_json_result(data={"profile": _emp_dict(emp)})


@manager.route("/hr/attendance/punch", methods=["POST"])
@login_required
async def hr_attendance_punch():
    emp, err = _require_employee()
    if err:
        return err
    try:
        rec = HrAttendanceRecordService.punch(emp.id, source="web", ip=_client_ip())
    except ValueError as e:
        return get_data_error_result(message=str(e))
    return get_json_result(data={
        "punch_time": str(rec.punch_time),
        "today": _today_payload(emp),
    })


@manager.route("/hr/attendance/today", methods=["GET"])
@login_required
async def hr_attendance_today():
    emp, err = _require_employee()
    if err:
        return err
    return get_json_result(data=_today_payload(emp))


@manager.route("/hr/attendance/calendar", methods=["GET"])
@login_required
async def hr_attendance_calendar():
    emp, err = _require_employee()
    if err:
        return err
    month = request.args.get("month", "")  # YYYY-MM
    parsed = _parse_month(request.args.get("month", ""))
    if not parsed:
        return get_data_error_result(message="month 格式应为 YYYY-MM")
    year, mon = parsed
    if year < 1:
        return get_data_error_result(message="month 格式应为 YYYY-MM")
    # 已落盘行优先；今日及之前的无行日期实时推导（不落盘）
    rows = {str(r.work_date): _day_dict(r)
            for r in HrAttendanceDayService.month_days(emp.id, month)}
    import calendar as _cal
    today = date.today()
    rule = HrRuleConfigService.get_config()
    # 当月 approved 假单一次查全，逐日判断覆盖状态
    month_start, month_end = date(year, mon, 1), date(year, mon, _cal.monthrange(year, mon)[1])
    month_leaves = HrLeaveRequestService.approved_requests(emp.id, month_start, month_end)
    days = []
    for d in range(1, _cal.monthrange(year, mon)[1] + 1):
        wd = date(year, mon, d)
        key = str(wd)
        if key in rows:
            days.append(rows[key])
        elif wd <= today:
            records = HrAttendanceRecordService.list_day(emp.id, wd)
            derived = derive_day_status(records, wd, rule,
                                        leave_status=leave_status_for_date(month_leaves, wd))
            days.append({"work_date": key, "status": derived["status"],
                         "first_in": str(derived["first_in"] or ""),
                         "last_out": str(derived["last_out"] or ""),
                         "late_minutes": derived["late_minutes"], "locked": False})
        else:
            days.append({"work_date": key, "status": "future",
                         "first_in": "", "last_out": "", "late_minutes": 0,
                         "locked": False})
    return get_json_result(data={"month": month, "days": days})


# ── HR 管理 ──

@manager.route("/hr/employee", methods=["GET"])
@permission_required("hr_manage")
async def hr_employee_list():
    keyword = (request.args.get("keyword") or "").strip()
    department = (request.args.get("department") or "").strip()
    query = HrEmployee.select()
    if keyword:
        # 关键词同时匹配用户昵称（user 表）与工号/部门/职位
        matched_uids = [u.id for u in User.select(User.id).where(
            User.nickname.contains(keyword))]
        conds = [HrEmployee.emp_no.contains(keyword),
                 HrEmployee.department.contains(keyword),
                 HrEmployee.position.contains(keyword)]
        if matched_uids:
            conds.append(HrEmployee.user_id.in_(matched_uids))
        query = query.where(*conds)
    if department:
        query = query.where(HrEmployee.department == department)
    emps = list(query.order_by(HrEmployee.emp_no))
    nick_map = {u.id: (u.nickname or "") for u in User.select(User.id, User.nickname).where(
        User.id.in_([e.user_id for e in emps]))} if emps else {}
    emp_list = []
    for e in emps:
        d = _emp_dict(e)
        d["nickname"] = nick_map.get(e.user_id, "")
        emp_list.append(d)
    return get_json_result(data={"list": emp_list, "total": len(emp_list)})


@manager.route("/hr/employee", methods=["POST"])
@permission_required("hr_manage")
async def hr_employee_create():
    body = await request.get_json(silent=True) or {}
    user_id = str(body.get("user_id") or "").strip()
    emp_no = str(body.get("emp_no") or "").strip()
    if not user_id or not emp_no:
        return get_data_error_result(message="请选择用户并填写工号")
    entry = None
    if body.get("entry_date"):
        if not isinstance(body.get("entry_date"), str):
            return get_data_error_result(message="entry_date 格式应为 YYYY-MM-DD")
        try:
            entry = datetime.strptime(body["entry_date"], "%Y-%m-%d").date()
        except ValueError:
            return get_data_error_result(message="entry_date 格式应为 YYYY-MM-DD")
    try:
        emp = HrEmployeeService.create_employee(
            user_id, emp_no, department=str(body.get("department") or ""),
            position=str(body.get("position") or ""), entry_date=entry)
    except ValueError as e:
        return get_data_error_result(message=str(e))
    return get_json_result(data=_emp_dict(emp))


@manager.route("/hr/attendance/repair", methods=["POST"])
@permission_required("hr_manage")
async def hr_attendance_repair():
    body = await request.get_json(silent=True) or {}
    emp = HrEmployee.get_or_none(HrEmployee.id == body.get("employee_id", ""))
    if not emp:
        return get_data_error_result(message="员工不存在")
    if body.get("punch_time") is not None and not isinstance(body.get("punch_time"), str):
        return get_data_error_result(message="punch_time 格式应为 YYYY-MM-DD HH:MM:SS")
    try:
        pt = datetime.strptime(body.get("punch_time") or "", "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return get_data_error_result(message="punch_time 格式应为 YYYY-MM-DD HH:MM:SS")
    try:
        rec = HrAttendanceRecordService.punch(
            emp.id, source="repair", punch_time=pt,
            remark=str(body.get("reason") or "")[:255])
    except ValueError as e:
        return get_data_error_result(message=str(e))
    return get_json_result(data={"id": rec.id, "punch_time": str(rec.punch_time)})


@manager.route("/hr/attendance/day-list", methods=["GET"])
@permission_required("hr_manage")
async def hr_attendance_day_list():
    month = request.args.get("month", "")
    work_date = request.args.get("date", "")  # 可选：查某天全员
    parsed = _parse_month(month)
    if not parsed:
        return get_data_error_result(message="month 格式应为 YYYY-MM")
    year, mon = parsed
    if year < 1:
        return get_data_error_result(message="month 格式应为 YYYY-MM")
    query = HrAttendanceDay.select().join(
        HrEmployee, on=(HrAttendanceDay.employee_id == HrEmployee.id))
    from datetime import date as _date
    start = _date(year, mon, 1)
    nxt = _date(year + 1, 1, 1) if mon == 12 else _date(year, mon + 1, 1)
    query = query.where(HrAttendanceDay.work_date >= start,
                        HrAttendanceDay.work_date < nxt)
    if work_date:
        try:
            wd = datetime.strptime(work_date, "%Y-%m-%d").date()
        except ValueError:
            return get_data_error_result(message="date 格式应为 YYYY-MM-DD")
        query = query.where(HrAttendanceDay.work_date == wd)
    rows = list(query.order_by(HrAttendanceDay.work_date))
    emp_names = {e.id: e.emp_no for e in HrEmployee.select()}
    return get_json_result(data={
        "list": [{**_day_dict(r), "employee_id": r.employee_id,
                  "emp_no": emp_names.get(r.employee_id, "")} for r in rows],
        "total": len(rows)})


@manager.route("/hr/attendance/month-close", methods=["POST"])
@permission_required("hr_manage")
async def hr_attendance_month_close():
    body = await request.get_json(silent=True) or {}
    month = str(body.get("month") or "").strip()
    parsed = _parse_month(month)
    if not parsed:
        return get_data_error_result(message="month 格式应为 YYYY-MM")
    year, mon = parsed
    if year < 1:
        return get_data_error_result(message="month 格式应为 YYYY-MM")
    month = f"{year:04d}-{mon:02d}"
    try:
        stats = HrAttendanceMonthService.close_month(month, current_user.id)
    except Exception as e:
        logger.exception("month-close failed")
        return get_data_error_result(message=f"月度汇总失败：{e}")
    return get_json_result(data=stats)


@manager.route("/hr/attendance/month/<month>", methods=["GET"])
@permission_required("hr_manage")
async def hr_attendance_month_summary(month: str):
    rows = list(HrAttendanceMonth.select().where(HrAttendanceMonth.month == month))
    emp_names = {e.id: e.emp_no for e in HrEmployee.select()}
    return get_json_result(data={
        "list": [{"employee_id": r.employee_id, "emp_no": emp_names.get(r.employee_id, ""),
                  "month": r.month, "attend_days": float(r.attend_days),
                  "late_count": r.late_count, "late_minutes": r.late_minutes,
                  "absent_days": r.absent_days, "missing_days": r.missing_days,
                  "status": r.status} for r in rows]})


@manager.route("/hr/rule-config", methods=["GET"])
@login_required
async def hr_rule_config_get():
    return get_json_result(data=HrRuleConfigService.get_config())


_NUMERIC_RULE_KEYS = {
    "late_threshold_minutes", "late_deduction", "absent_deduction_multiplier",
    "overtime_rate_weekday", "overtime_rate_weekend",
    "holiday_overtime_multiplier", "pay_days", "social_rate", "fund_rate",
}


def _sanitize_rule_payload(body: dict) -> dict:
    """数值键强转 float（失败拒绝），其余键只接受 str，防毒化配置导致全线500。

    holidays 键额外做格式校验（逗号分隔 YYYY-MM-DD，M6）。
    """
    clean = {}
    for k, v in (body or {}).items():
        if k == "holidays":
            normalized = normalize_holidays(v)
            if normalized is None:
                raise ValueError("holidays 应为逗号分隔的 YYYY-MM-DD 列表")
            clean[k] = normalized
        elif k in _NUMERIC_RULE_KEYS:
            try:
                value = float(v)
                if not math.isfinite(value):
                    raise ValueError
                clean[k] = value
            except (TypeError, ValueError):
                raise ValueError(f"规则项 {k} 必须是数字")
        else:
            clean[k] = str(v)
    return clean


@manager.route("/hr/rule-config", methods=["PUT"])
@permission_required("hr_manage")
async def hr_rule_config_put():
    body = await request.get_json(silent=True) or {}
    try:
        payload = _sanitize_rule_payload(body)
    except ValueError as e:
        return get_data_error_result(message=str(e))
    return get_json_result(data=HrRuleConfigService.save_config(payload))


# ── 请假 / 补卡申请（P2）──

_LEAVE_TYPES = {"personal", "sick", "annual", "marriage", "maternity",
                "business_trip", "other", "repair"}
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _parse_date_str(v):
    if not isinstance(v, str) or not _DATE_RE.match(v):
        return None
    try:
        return datetime.strptime(v, "%Y-%m-%d").date()
    except ValueError:
        return None


def _req_dict(r, nick_map=None) -> dict:
    emp = HrEmployee.get_or_none(HrEmployee.id == r.employee_id)
    nickname = (nick_map or {}).get(emp.user_id, "") if emp else ""
    return {"id": r.id, "employee_id": r.employee_id, "nickname": nickname,
            "leave_type": r.leave_type, "start_date": str(r.start_date),
            "end_date": str(r.end_date), "duration_days": r.duration_days,
            "reason": r.reason, "status": r.status, "current_step": r.current_step}


def _steps_dict(request_id: str) -> list:
    from api.db.services.hr_service import HrLeaveStepService
    rows = list(HrLeaveStepService.model.select().where(
        HrLeaveStepService.model.request_id == request_id).order_by(
        HrLeaveStepService.model.step_no))
    uids = [r.approver_id for r in rows]
    nicks = {u.id: (u.nickname or "") for u in User.select(User.id, User.nickname).where(
        User.id.in_(uids))} if uids else {}
    return [{"step_no": r.step_no, "approver_id": r.approver_id,
             "approver_name": nicks.get(r.approver_id, ""),
             "status": r.status, "comment": r.comment,
             "action_time": str(r.action_time or "")} for r in rows]


@manager.route("/hr/leave", methods=["POST"])
@login_required
async def hr_leave_submit():
    emp, err = _require_employee()
    if err:
        return err
    body = await request.get_json(silent=True) or {}
    lt = str(body.get("leave_type") or "")
    if lt not in _LEAVE_TYPES:
        return get_data_error_result(message="无效的假单类型")
    sd, ed = _parse_date_str(body.get("start_date")), _parse_date_str(body.get("end_date"))
    if not sd or not ed:
        return get_data_error_result(message="日期格式应为 YYYY-MM-DD")
    try:
        req = HrLeaveRequestService.submit(
            emp, lt, sd, ed, str(body.get("reason") or ""),
            current_user.id, HrRuleConfigService.get_config())
    except ValueError as e:
        return get_data_error_result(message=str(e))
    return get_json_result(data={**_req_dict(req), "steps": _steps_dict(req.id)})


@manager.route("/hr/leave/my", methods=["GET"])
@login_required
async def hr_leave_my():
    emp, err = _require_employee()
    if err:
        return err
    rows = list(HrLeaveRequestService.model.select().where(
        HrLeaveRequestService.model.employee_id == emp.id).order_by(
        HrLeaveRequestService.model.create_time.desc()))
    # 携带 steps 供前端渲染审批进度（含审批人名/状态/意见）
    return get_json_result(data={
        "list": [{**_req_dict(r), "steps": _steps_dict(r.id)} for r in rows],
        "total": len(rows)})


@manager.route("/hr/leave/pending", methods=["GET"])
@login_required
async def hr_leave_pending():
    rows = HrLeaveRequestService.pending_for_approver(current_user.id)
    return get_json_result(data={"list": [_req_dict(r) for r in rows],
                                 "total": len(rows)})


@manager.route("/hr/leave/<rid>", methods=["GET"])
@login_required
async def hr_leave_detail(rid: str):
    r = HrLeaveRequestService.model.get_or_none(
        HrLeaveRequestService.model.id == rid)
    if not r:
        return get_data_error_result(message="假单不存在")
    emp = HrEmployeeService.get_by_user(current_user.id)
    # 不能用 @permission_required（普通员工也要能查自己单子），函数内复用统一权限判定：
    # 超管或持有 hr_manage 权限者视为 HR
    is_hr = permission_allowed(
        bool(current_user.is_superuser),
        get_cached_user_permissions(current_user.id), "hr_manage")
    step_mine = HrLeaveStep.get_or_none(
        HrLeaveStep.request_id == rid, HrLeaveStep.approver_id == current_user.id)
    if not is_hr and not step_mine and (not emp or emp.id != r.employee_id):
        return get_data_error_result(message="无权查看该假单")
    return get_json_result(data={**_req_dict(r), "steps": _steps_dict(rid)})


@manager.route("/hr/leave/<rid>/approve", methods=["POST"])
@login_required
async def hr_leave_approve(rid: str):
    body = await request.get_json(silent=True) or {}
    action = str(body.get("action") or "")
    if action not in ("approved", "rejected"):
        return get_data_error_result(message="action 应为 approved/rejected")
    try:
        r = HrLeaveRequestService.act(rid, current_user.id, action,
                                      str(body.get("comment") or ""),
                                      HrRuleConfigService.get_config())
    except ValueError as e:
        return get_data_error_result(message=str(e))
    return get_json_result(data={**_req_dict(r), "steps": _steps_dict(rid)})


@manager.route("/hr/leave/<rid>/cancel", methods=["POST"])
@login_required
async def hr_leave_cancel(rid: str):
    try:
        r = HrLeaveRequestService.cancel(rid, current_user.id,
                                         HrRuleConfigService.get_config())
    except ValueError as e:
        return get_data_error_result(message=str(e))
    return get_json_result(data=_req_dict(r))


@manager.route("/hr/leave/balance", methods=["GET"])
@login_required
async def hr_leave_balance_get():
    emp, err = _require_employee()
    if err:
        return err
    year = request.args.get("year", "")
    y = int(year) if year.isdigit() else date.today().year
    rows = HrLeaveBalanceService.list_balance(emp.id, y)
    return get_json_result(data={"year": y, "list": [
        {"leave_type": r.leave_type, "total_days": r.total_days,
         "used_days": r.used_days, "frozen_days": r.frozen_days} for r in rows]})


@manager.route("/hr/leave/balance", methods=["PUT"])
@permission_required("hr_manage")
async def hr_leave_balance_put():
    """HR 调整年度额度总额（不动 used/frozen）。"""
    body = await request.get_json(silent=True) or {}
    emp = HrEmployee.get_or_none(HrEmployee.id == body.get("employee_id", ""))
    if not emp:
        return get_data_error_result(message="员工不存在")
    lt = str(body.get("leave_type") or "")
    if lt not in QUOTA_KEY:
        return get_data_error_result(message="无效的假期类型")
    year = body.get("year")
    if not isinstance(year, int):
        return get_data_error_result(message="year 应为整数")
    total = body.get("total_days")
    if not isinstance(total, int) or total < 0:
        return get_data_error_result(message="total_days 应为非负整数")
    row = HrLeaveBalanceService.get_or_init(
        emp.id, year, lt, HrRuleConfigService.get_config())
    row.total_days = total
    row.update_time = current_timestamp()
    row.save()
    return get_json_result(data={"employee_id": emp.id, "year": year,
                                 "leave_type": lt, "total_days": total})


# ── 薪资（P3）──

_AMOUNT_KEYS = ("base_salary", "post_allowance", "meal_allowance",
                "transport_allowance", "social_base", "fund_base", "special_deduction")
_OVERRIDE_KEYS = {"social", "fund", "tax"}


def _valid_amount(v):
    """金额：数字、非负、有限，且不超过 DecimalField(10,2) 上限 9999_9999（M4，
    防超限落库 DataError）。"""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return False
    return math.isfinite(v) and 0 <= v <= 99_999_999


def _profile_dict(p, nick_map=None, emp_map=None) -> dict:
    emp = (emp_map or {}).get(p.employee_id)
    nickname = (nick_map or {}).get(emp.user_id, "") if emp else ""
    try:
        overrides = json.loads(p.manual_overrides or "{}")
    except ValueError:
        overrides = {}
    return {
        "id": p.id, "employee_id": p.employee_id, "nickname": nickname,
        "emp_no": emp.emp_no if emp else "",
        "base_salary": float(p.base_salary), "post_allowance": float(p.post_allowance),
        "meal_allowance": float(p.meal_allowance),
        "transport_allowance": float(p.transport_allowance),
        "social_base": float(p.social_base), "fund_base": float(p.fund_base),
        "special_deduction": float(p.special_deduction),
        "social_rate": float(p.social_rate) if p.social_rate is not None else None,
        "fund_rate": float(p.fund_rate) if p.fund_rate is not None else None,
        "manual_overrides": overrides if isinstance(overrides, dict) else {},
    }


def _payslip_dict(r, nick_map=None, emp_map=None) -> dict:
    emp = (emp_map or {}).get(r.employee_id)
    nickname = (nick_map or {}).get(emp.user_id, "") if emp else ""
    return {
        "id": r.id, "employee_id": r.employee_id, "nickname": nickname,
        "emp_no": emp.emp_no if emp else "", "month": r.month,
        "attend_days": float(r.attend_days), "late_count": r.late_count,
        "late_minutes": r.late_minutes, "absent_days": r.absent_days,
        "overtime_hours": float(r.overtime_hours), "leave_days": float(r.leave_days),
        "base_salary": float(r.base_salary), "allowances": float(r.allowances),
        "overtime_pay": float(r.overtime_pay), "gross_pay": float(r.gross_pay),
        "attendance_deduction": float(r.attendance_deduction),
        "social_insurance": float(r.social_insurance),
        "housing_fund": float(r.housing_fund), "income_tax": float(r.income_tax),
        "net_pay": float(r.net_pay), "status": r.status,
        "published_at": str(r.published_at or "") or None,
    }


def _nick_emp_maps(emps):
    uids = [e.user_id for e in emps]
    nicks = {u.id: (u.nickname or "") for u in User.select(User.id, User.nickname).where(
        User.id.in_(uids))} if uids else {}
    return nicks, {e.id: e for e in emps}


@manager.route("/hr/salary-profile", methods=["GET"])
@permission_required("hr_manage")
async def hr_salary_profile_list():
    keyword = (request.args.get("keyword") or "").strip()
    query = HrSalaryProfile.select()
    if keyword:
        # 关键词匹配昵称或工号，命中员工集合为空时直接返回空列表
        matched_uids = [u.id for u in User.select(User.id).where(
            User.nickname.contains(keyword))]
        emp_ids = {e.id for e in HrEmployee.select(HrEmployee.id).where(
            HrEmployee.emp_no.contains(keyword))}
        if matched_uids:
            emp_ids.update(e.id for e in HrEmployee.select(HrEmployee.id).where(
                HrEmployee.user_id.in_(matched_uids)))
        if not emp_ids:
            return get_json_result(data={"list": [], "total": 0})
        query = query.where(HrSalaryProfile.employee_id.in_(emp_ids))
    rows = list(query)
    emps = list(HrEmployee.select().where(
        HrEmployee.id.in_([p.employee_id for p in rows]))) if rows else []
    nicks, emp_map = _nick_emp_maps(emps)
    return get_json_result(data={
        "list": [_profile_dict(p, nicks, emp_map) for p in rows], "total": len(rows)})


@manager.route("/hr/salary-profile", methods=["PUT"])
@permission_required("hr_manage")
async def hr_salary_profile_put():
    body = await request.get_json(silent=True) or {}
    emp = HrEmployee.get_or_none(HrEmployee.id == body.get("employee_id", ""))
    if not emp:
        return get_data_error_result(message="员工不存在")
    vals = {}
    for k in _AMOUNT_KEYS:
        if k in body:
            if not _valid_amount(body[k]):
                return get_data_error_result(message=f"{k} 必须是非负数字")
            vals[k] = body[k]
    for k in ("social_rate", "fund_rate"):
        if k not in body:
            continue
        v = body[k]
        if v is None:
            # 显式 null = 清除个人费率，回退全局费率（未提供该键时仍不改动旧值）
            vals[k] = None
            continue
        if isinstance(v, bool) or not isinstance(v, (int, float)) \
                or not math.isfinite(v) or not (0 <= v <= 0.3):
            return get_data_error_result(message=f"{k} 应在 [0, 0.3] 区间内")
        vals[k] = v
    if "manual_overrides" in body:
        ov = body.get("manual_overrides")
        if not isinstance(ov, dict) or set(ov.keys()) - _OVERRIDE_KEYS:
            return get_data_error_result(message="manual_overrides 仅接受 social/fund/tax 键")
        clean_ov = {}
        for ok_key, ov_val in ov.items():
            if ov_val is None:
                continue
            if not _valid_amount(ov_val):
                return get_data_error_result(message=f"覆盖项 {ok_key} 必须是非负数字")
            clean_ov[ok_key] = ov_val
        vals["manual_overrides"] = json.dumps(clean_ov, ensure_ascii=False)
    try:
        row = HrSalaryProfileService.upsert_profile(emp.id, vals)
    except ValueError as e:
        return get_data_error_result(message=str(e))
    nicks, emp_map = _nick_emp_maps([emp])
    return get_json_result(data=_profile_dict(row, nicks, emp_map))


def _trial_targets(employee_id):
    """待核算员工集合：指定 id 时单查，否则全体在职。"""
    if employee_id:
        emp = HrEmployee.get_or_none(HrEmployee.id == employee_id)
        return [emp] if emp else []
    return list(HrEmployee.select().where(HrEmployee.status == "active"))


# trial 响应白名单（M8）：只透出金额明细 + tax_snapshot（保留：HR 需核对个税累计
# 预扣中间量）；_soc_calculated/_fund_calculated/_mrow 为内部字段，不透出前端
_TRIAL_KEYS = ("base_salary", "allowances", "overtime_pay", "gross_pay",
               "attendance_deduction", "social_insurance", "housing_fund",
               "income_tax", "net_pay", "tax_snapshot")


@manager.route("/hr/salary/trial", methods=["POST"])
@permission_required("hr_manage")
async def hr_salary_trial():
    body = await request.get_json(silent=True) or {}
    month = str(body.get("month") or "").strip()
    if not _parse_month(month):
        return get_data_error_result(message="month 格式应为 YYYY-MM")
    targets = _trial_targets(str(body.get("employee_id") or "").strip())
    rule = HrRuleConfigService.get_config()
    nicks, _ = _nick_emp_maps(targets)
    results = []
    for emp in targets:
        try:
            r = HrPayslipService.compute_for_employee(emp.id, month, rule)
        except ValueError as e:
            results.append({"employee_id": emp.id, "nickname": nicks.get(emp.user_id, ""),
                            "emp_no": emp.emp_no, "ok": False, "reason": str(e)})
            continue
        results.append({"employee_id": emp.id, "nickname": nicks.get(emp.user_id, ""),
                        "emp_no": emp.emp_no, "ok": True,
                        **{k: r[k] for k in _TRIAL_KEYS}})
    return get_json_result(data={"month": month, "list": results, "total": len(results)})


@manager.route("/hr/salary/calc", methods=["POST"])
@permission_required("hr_manage")
async def hr_salary_calc():
    body = await request.get_json(silent=True) or {}
    month = str(body.get("month") or "").strip()
    if not _parse_month(month):
        return get_data_error_result(message="month 格式应为 YYYY-MM")
    rule = HrRuleConfigService.get_config()
    ok_count, failed = 0, []
    for emp in _trial_targets(""):
        try:
            r = HrPayslipService.compute_for_employee(emp.id, month, rule)
            mrow = r.pop("_mrow")  # compute 时随结果带回，省一次重复查询（I3）
            HrPayslipService.save_draft(emp.id, month, r, mrow)
            ok_count += 1
        except ValueError as e:  # 单人失败不中断全员
            failed.append({"employee_id": emp.id, "reason": str(e)})
        except Exception:  # M11：不回显原始异常细节，仅日志留痕
            logger.exception("salary calc failed for %s", emp.id)
            failed.append({"employee_id": emp.id, "reason": "核算异常，请联系管理员"})
    return get_json_result(data={"month": month, "ok": ok_count, "failed": failed})


@manager.route("/hr/salary/publish", methods=["POST"])
@permission_required("hr_manage")
async def hr_salary_publish():
    body = await request.get_json(silent=True) or {}
    month = str(body.get("month") or "").strip()
    if not _parse_month(month):
        return get_data_error_result(message="month 格式应为 YYYY-MM")
    # 条件更新防并发/防重复发布：仅 draft 行推进，published 不受影响。
    # published_at 为 DateTimeField，须存 datetime（current_timestamp() 返回毫秒 int
    # 是 BigIntegerField(update_time) 专用，写入 DATETIME 列会类型错乱），noqa 保留
    n = (HrPayslip.update(status="published", published_at=datetime.now())  # noqa: DTZ005
         .where(HrPayslip.month == month, HrPayslip.status == "draft").execute())
    return get_json_result(data={"month": month, "published": n})


@manager.route("/hr/salary/payslips", methods=["GET"])
@permission_required("hr_manage")
async def hr_salary_payslips():
    month = request.args.get("month", "")
    if not _parse_month(month):
        return get_data_error_result(message="month 格式应为 YYYY-MM")
    rows = list(HrPayslip.select().where(
        HrPayslip.month == month).order_by(HrPayslip.employee_id))
    emps = list(HrEmployee.select().where(
        HrEmployee.id.in_([r.employee_id for r in rows]))) if rows else []
    nicks, emp_map = _nick_emp_maps(emps)
    return get_json_result(data={
        "month": month,
        "list": [_payslip_dict(r, nicks, emp_map) for r in rows],
        "total": len(rows)})


@manager.route("/hr/payslip/my", methods=["GET"])
@login_required
async def hr_payslip_my():
    emp, err = _require_employee()
    if err:
        return err
    month = request.args.get("month", "")
    if not _parse_month(month):
        month = date.today().strftime("%Y-%m")  # noqa: DTZ011
    row = HrPayslip.get_or_none(
        HrPayslip.employee_id == emp.id, HrPayslip.month == month,
        HrPayslip.status == "published")
    if not row:
        return get_json_result(data={"month": month, "payslip": None})
    return get_json_result(data={"month": month, "payslip": _payslip_dict(row)})


# ── 财务凭证 / 报表 / 归档 / 考勤机导入（P4）──

def _voucher_dict(v) -> dict:
    try:
        entries = json.loads(v.entries or "[]")
    except ValueError:
        entries = []
    return {"id": v.id, "month": v.month, "voucher_type": v.voucher_type,
            "entries": entries if isinstance(entries, list) else [],
            "total_amount": float(v.total_amount), "status": v.status,
            "create_time": str(v.create_time or "")}


def _adjust_dict(r, nick_map=None) -> dict:
    return {"id": r.id, "payslip_id": r.payslip_id, "employee_id": r.employee_id,
            "month": r.month, "field": r.field, "old_value": float(r.old_value),
            "new_value": float(r.new_value), "reason": r.reason,
            "operator_id": r.operator_id,
            "operator_name": (nick_map or {}).get(r.operator_id, ""),
            "create_time": str(r.create_time or "")}


def _build_xlsx(rows: list, headers: list) -> bytes:
    """openpyxl 内存构建 xlsx：headers = [(字段键, 表头中文), ...]，逐行 append。"""
    wb = Workbook()
    ws = wb.active
    ws.append([title for _, title in headers])
    for row in rows:
        ws.append([row.get(k) for k, _ in headers])
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _xlsx_response(data: bytes, file_name: str) -> Response:
    return Response(data, mimetype=(
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
        headers={"Content-Disposition":
                 f"attachment; filename*=UTF-8''{quote(file_name)}"})


_ADJUSTABLE_API_FIELDS = ("attendance_deduction", "social_insurance",
                          "housing_fund", "income_tax")


@manager.route("/hr/salary/payslip/<pid>/adjust", methods=["POST"])
@permission_required("hr_manage")
async def hr_salary_payslip_adjust(pid: str):
    body = await request.get_json(silent=True) or {}
    field = str(body.get("field") or "").strip()
    if field not in _ADJUSTABLE_API_FIELDS:
        return get_data_error_result(
            message=f"field 仅支持：{'/'.join(_ADJUSTABLE_API_FIELDS)}")
    new_value = body.get("new_value")
    if isinstance(new_value, bool) or not isinstance(new_value, (int, float)) \
            or not math.isfinite(new_value) or new_value < 0:
        return get_data_error_result(message="new_value 必须是非负数字")
    reason = str(body.get("reason") or "").strip()
    if not reason:
        return get_data_error_result(message="请填写调整原因")
    try:
        row, stale = HrPayslipService.adjust(pid, field, new_value, reason,
                                             current_user.id)
    except ValueError as e:
        return get_data_error_result(message=str(e))
    emps = list(HrEmployee.select().where(
        HrEmployee.id == row.employee_id)) if row.employee_id else []
    nicks, emp_map = _nick_emp_maps(emps)
    return get_json_result(data={"payslip": _payslip_dict(row, nicks, emp_map),
                                 "voucher_stale": stale})


@manager.route("/hr/salary/adjustments", methods=["GET"])
@permission_required("hr_manage")
async def hr_salary_adjustments():
    payslip_id = request.args.get("payslip_id", "").strip()
    month = request.args.get("month", "").strip()
    employee_id = request.args.get("employee_id", "").strip()
    if not (payslip_id or month or employee_id):
        return get_data_error_result(message="请至少提供一个查询条件")
    if month and not _parse_month(month):
        return get_data_error_result(message="month 格式应为 YYYY-MM")
    query = HrPayslipAdjust.select()
    if payslip_id:
        query = query.where(HrPayslipAdjust.payslip_id == payslip_id)
    if month:
        query = query.where(HrPayslipAdjust.month == month)
    if employee_id:
        query = query.where(HrPayslipAdjust.employee_id == employee_id)
    rows = list(query.order_by(HrPayslipAdjust.create_time.desc()))
    op_ids = [r.operator_id for r in rows if r.operator_id]
    nicks = {u.id: (u.nickname or "") for u in User.select(User.id, User.nickname).where(
        User.id.in_(op_ids))} if op_ids else {}
    return get_json_result(data={"list": [_adjust_dict(r, nicks) for r in rows],
                                 "total": len(rows)})


_VOUCHER_TYPES = ("accrue", "pay")


@manager.route("/hr/voucher/generate", methods=["POST"])
@permission_required("hr_manage")
async def hr_voucher_generate():
    body = await request.get_json(silent=True) or {}
    month = str(body.get("month") or "").strip()
    if not _parse_month(month):
        return get_data_error_result(message="month 格式应为 YYYY-MM")
    vt = str(body.get("voucher_type") or "").strip()
    if vt not in _VOUCHER_TYPES:
        return get_data_error_result(message="voucher_type 应为 accrue/pay")
    try:
        row = HrVoucherService.generate(month, vt, current_user.id)
    except ValueError as e:
        return get_data_error_result(message=str(e))
    except Exception:
        logger.exception("voucher generate failed")
        return get_data_error_result(message="凭证生成失败，请联系管理员")
    return get_json_result(data=_voucher_dict(row))


@manager.route("/hr/voucher/list", methods=["GET"])
@permission_required("hr_manage")
async def hr_voucher_list():
    month = request.args.get("month", "").strip()
    if not _parse_month(month):
        return get_data_error_result(message="month 格式应为 YYYY-MM")
    rows = HrVoucherService.list_month(month)
    return get_json_result(data={"list": [_voucher_dict(r) for r in rows],
                                 "total": len(rows)})


def _att_headers() -> list:
    return [("emp_no", "工号"), ("nickname", "姓名"), ("department", "部门"),
            ("month", "月份"), ("attend_days", "出勤天数"), ("late_count", "迟到次数"),
            ("late_minutes", "迟到分钟"), ("absent_days", "旷工天数"),
            ("missing_days", "缺卡天数"), ("leave_days", "请假天数"),
            ("overtime_hours", "加班时长(h)"), ("status", "状态")]


def _payroll_headers() -> list:
    return [("emp_no", "工号"), ("nickname", "姓名"), ("department", "部门"),
            ("month", "月份"), ("attend_days", "出勤天数"),
            ("base_salary", "基本工资"), ("allowances", "津贴补贴"),
            ("overtime_pay", "加班费"), ("gross_pay", "应发工资"),
            ("attendance_deduction", "考勤扣款"), ("social_insurance", "社保个人"),
            ("housing_fund", "公积金个人"), ("income_tax", "个税"),
            ("net_pay", "实发工资"), ("status", "状态")]


def _insurance_headers() -> list:
    return [("emp_no", "工号"), ("nickname", "姓名"), ("department", "部门"),
            ("month", "月份"), ("social_base", "社保基数"), ("fund_base", "公积金基数"),
            ("social_insurance", "社保个人"), ("housing_fund", "公积金个人"),
            ("income_tax", "个税")]


def _emp_maps_for(rows) -> dict:
    """批量查员工+昵称，返回 {employee_id: (emp_no, nickname, department)}。"""
    emp_ids = {r.employee_id for r in rows}
    if not emp_ids:
        return {}
    emps = list(HrEmployee.select().where(HrEmployee.id.in_(emp_ids)))
    uids = [e.user_id for e in emps]
    nicks = {u.id: (u.nickname or "") for u in User.select(User.id, User.nickname).where(
        User.id.in_(uids))} if uids else {}
    return {e.id: (e.emp_no, nicks.get(e.user_id, ""), e.department) for e in emps}


@manager.route("/hr/report/export", methods=["GET"])
@permission_required("hr_manage")
async def hr_report_export():
    rtype = request.args.get("type", "").strip()
    month = request.args.get("month", "").strip()
    if rtype not in ("attendance", "payroll", "insurance"):
        return get_data_error_result(message="type 应为 attendance/payroll/insurance")
    if not _parse_month(month):
        return get_data_error_result(message="month 格式应为 YYYY-MM")
    if rtype == "attendance":
        rows = list(HrAttendanceMonth.select().where(HrAttendanceMonth.month == month))
        info = _emp_maps_for(rows)
        data_rows = [dict(info.get(r.employee_id, ("", "", "")), month=r.month,
                          attend_days=float(r.attend_days), late_count=r.late_count,
                          late_minutes=r.late_minutes, absent_days=r.absent_days,
                          missing_days=r.missing_days, leave_days=float(r.leave_days),
                          overtime_hours=float(r.overtime_hours), status=r.status)
                     for r in rows]
        headers, name = _att_headers(), f"考勤月汇总-{month}.xlsx"
    elif rtype == "payroll":
        rows = list(HrPayslip.select().where(HrPayslip.month == month))
        info = _emp_maps_for(rows)
        data_rows = [dict(info.get(r.employee_id, ("", "", "")), month=r.month,
                          attend_days=float(r.attend_days),
                          base_salary=float(r.base_salary),
                          allowances=float(r.allowances),
                          overtime_pay=float(r.overtime_pay),
                          gross_pay=float(r.gross_pay),
                          attendance_deduction=float(r.attendance_deduction),
                          social_insurance=float(r.social_insurance),
                          housing_fund=float(r.housing_fund),
                          income_tax=float(r.income_tax), net_pay=float(r.net_pay),
                          status=r.status) for r in rows]
        headers, name = _payroll_headers(), f"工资发放明细-{month}.xlsx"
    else:
        rows = list(HrPayslip.select().where(HrPayslip.month == month))
        info = _emp_maps_for(rows)
        profiles = {p.employee_id: p for p in HrSalaryProfile.select().where(
            HrSalaryProfile.employee_id.in_({r.employee_id for r in rows}))} if rows else {}
        data_rows = []
        for r in rows:
            prof = profiles.get(r.employee_id)
            data_rows.append(dict(info.get(r.employee_id, ("", "", "")), month=r.month,
                                  social_base=float(prof.social_base) if prof else "",
                                  fund_base=float(prof.fund_base) if prof else "",
                                  social_insurance=float(r.social_insurance),
                                  housing_fund=float(r.housing_fund),
                                  income_tax=float(r.income_tax)))
        headers, name = _insurance_headers(), f"社保公积金个税汇总-{month}.xlsx"
    return _xlsx_response(_build_xlsx(data_rows, headers), name)


@manager.route("/hr/archive/search", methods=["GET"])
@permission_required("hr_manage")
async def hr_archive_search():
    month = request.args.get("month", "").strip()
    department = request.args.get("department", "").strip()
    keyword = request.args.get("keyword", "").strip()
    if not (month or department or keyword):
        return get_data_error_result(message="请至少提供一个查询条件")
    if month and not _parse_month(month):
        return get_data_error_result(message="month 格式应为 YYYY-MM")
    query = HrAttendanceMonth.select()
    if month:
        query = query.where(HrAttendanceMonth.month == month)
    emp_conds = [HrEmployee.department.contains(department)] if department else []
    if keyword:
        matched_uids = [u.id for u in User.select(User.id).where(
            User.nickname.contains(keyword))]
        emp_conds.append(HrEmployee.emp_no.contains(keyword))
        if matched_uids:
            emp_conds.append(HrEmployee.user_id.in_(matched_uids))
    if emp_conds:
        emp_ids = {e.id for e in HrEmployee.select(HrEmployee.id).where(*emp_conds)}
        if not emp_ids:
            return get_json_result(data={"list": [], "total": 0})
        query = query.where(HrAttendanceMonth.employee_id.in_(emp_ids))
    rows = list(query.order_by(HrAttendanceMonth.month.desc(), HrAttendanceMonth.employee_id))
    info = _emp_maps_for(rows)
    # 批量取相关月份 payslip，内存 join 出工资单状态/实发
    months = {r.month for r in rows}
    slips = {}
    if rows and months:
        emp_ids = {r.employee_id for r in rows}
        for p in HrPayslip.select().where(HrPayslip.month.in_(months),
                                          HrPayslip.employee_id.in_(emp_ids)):
            slips[(p.employee_id, p.month)] = p
    data_rows = []
    for r in rows:
        emp_no, nickname, dept = info.get(r.employee_id, ("", "", ""))
        slip = slips.get((r.employee_id, r.month))
        data_rows.append({"employee_id": r.employee_id, "emp_no": emp_no,
                          "nickname": nickname, "department": dept, "month": r.month,
                          "attend_days": float(r.attend_days),
                          "late_count": r.late_count, "absent_days": r.absent_days,
                          "overtime_hours": float(r.overtime_hours),
                          "payslip_status": slip.status if slip else None,
                          "net_pay": float(slip.net_pay) if slip else None})
    return get_json_result(data={"list": data_rows, "total": len(data_rows)})


@manager.route("/hr/attendance/sync-api", methods=["POST"])
@permission_required("hr_manage")
async def hr_attendance_sync_api():
    body = await request.get_json(silent=True) or {}
    try:
        result = HrAttendanceImportService.batch_punch(
            body.get("records"), "api_sync", current_user.id)
    except ValueError as e:
        return get_data_error_result(message=str(e))
    return get_json_result(data=result)


@manager.route("/hr/attendance/import", methods=["POST"])
@permission_required("hr_manage")
async def hr_attendance_import():
    body = await request.get_json(silent=True) or {}
    try:
        result = HrAttendanceImportService.batch_punch(
            body.get("records"), "manual_excel", current_user.id,
            file_name=str(body.get("file_name") or ""))
    except ValueError as e:
        return get_data_error_result(message=str(e))
    return get_json_result(data=result)
