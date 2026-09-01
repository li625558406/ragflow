"""人事模块 REST API（/api/v1/hr/*）。

权限模型：
- 员工自助接口（打卡/日历/我的档案）：login_required，且必须有 hr_employee 档案
- HR 管理接口（建档/全员汇总/补卡/月度归档/规则配置写）：@permission_required("hr_manage")
"""
import logging
import math
import re
from datetime import date, datetime

from quart import Blueprint, request

from api.apps import current_user, login_required
from api.db.db_models import (
    HrAttendanceDay,
    HrAttendanceMonth,
    HrEmployee,
    HrLeaveStep,
    User,
)
from api.db.services.hr_calculator import derive_day_status, leave_status_for_date
from api.db.services.hr_service import (
    HrAttendanceDayService,
    HrAttendanceMonthService,
    HrAttendanceRecordService,
    HrEmployeeService,
    HrLeaveBalanceService,
    HrLeaveRequestService,
    HrRuleConfigService,
    QUOTA_KEY,
)
from api.utils.api_utils import get_data_error_result, get_json_result
from api.utils.permission_utils import permission_required
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

@manager.route("/hr/employee/me", methods=["GET"])  # noqa: F821
@login_required
async def hr_employee_me():
    emp = HrEmployeeService.get_by_user(current_user.id)
    if not emp:
        return get_json_result(data={"profile": None})
    return get_json_result(data={"profile": _emp_dict(emp)})


@manager.route("/hr/attendance/punch", methods=["POST"])  # noqa: F821
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


@manager.route("/hr/attendance/today", methods=["GET"])  # noqa: F821
@login_required
async def hr_attendance_today():
    emp, err = _require_employee()
    if err:
        return err
    return get_json_result(data=_today_payload(emp))


@manager.route("/hr/attendance/calendar", methods=["GET"])  # noqa: F821
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

@manager.route("/hr/employee", methods=["GET"])  # noqa: F821
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


@manager.route("/hr/employee", methods=["POST"])  # noqa: F821
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


@manager.route("/hr/attendance/repair", methods=["POST"])  # noqa: F821
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


@manager.route("/hr/attendance/day-list", methods=["GET"])  # noqa: F821
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


@manager.route("/hr/attendance/month-close", methods=["POST"])  # noqa: F821
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


@manager.route("/hr/attendance/month/<month>", methods=["GET"])  # noqa: F821
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


@manager.route("/hr/rule-config", methods=["GET"])  # noqa: F821
@login_required
async def hr_rule_config_get():
    return get_json_result(data=HrRuleConfigService.get_config())


_NUMERIC_RULE_KEYS = {
    "late_threshold_minutes", "late_deduction", "absent_deduction_multiplier",
    "overtime_rate_weekday", "overtime_rate_weekend",
    "holiday_overtime_multiplier", "pay_days",
}


def _sanitize_rule_payload(body: dict) -> dict:
    """数值键强转 float（失败拒绝），其余键只接受 str，防毒化配置导致全线500。"""
    clean = {}
    for k, v in (body or {}).items():
        if k in _NUMERIC_RULE_KEYS:
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


@manager.route("/hr/rule-config", methods=["PUT"])  # noqa: F821
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


@manager.route("/hr/leave", methods=["POST"])  # noqa: F821
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


@manager.route("/hr/leave/my", methods=["GET"])  # noqa: F821
@login_required
async def hr_leave_my():
    emp, err = _require_employee()
    if err:
        return err
    rows = list(HrLeaveRequestService.model.select().where(
        HrLeaveRequestService.model.employee_id == emp.id).order_by(
        HrLeaveRequestService.model.create_time.desc()))
    return get_json_result(data={"list": [_req_dict(r) for r in rows],
                                 "total": len(rows)})


@manager.route("/hr/leave/pending", methods=["GET"])  # noqa: F821
@login_required
async def hr_leave_pending():
    rows = HrLeaveRequestService.pending_for_approver(current_user.id)
    return get_json_result(data={"list": [_req_dict(r) for r in rows],
                                 "total": len(rows)})


@manager.route("/hr/leave/<rid>", methods=["GET"])  # noqa: F821
@login_required
async def hr_leave_detail(rid: str):
    r = HrLeaveRequestService.model.get_or_none(
        HrLeaveRequestService.model.id == rid)
    if not r:
        return get_data_error_result(message="假单不存在")
    emp = HrEmployeeService.get_by_user(current_user.id)
    is_hr = current_user.is_superuser  # 审批人/HR 均可看详情，其余仅本人
    step_mine = HrLeaveStep.get_or_none(
        HrLeaveStep.request_id == rid, HrLeaveStep.approver_id == current_user.id)
    if not is_hr and not step_mine and (not emp or emp.id != r.employee_id):
        return get_data_error_result(message="无权查看该假单")
    return get_json_result(data={**_req_dict(r), "steps": _steps_dict(rid)})


@manager.route("/hr/leave/<rid>/approve", methods=["POST"])  # noqa: F821
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


@manager.route("/hr/leave/<rid>/cancel", methods=["POST"])  # noqa: F821
@login_required
async def hr_leave_cancel(rid: str):
    try:
        r = HrLeaveRequestService.cancel(rid, current_user.id,
                                         HrRuleConfigService.get_config())
    except ValueError as e:
        return get_data_error_result(message=str(e))
    return get_json_result(data=_req_dict(r))


@manager.route("/hr/leave/balance", methods=["GET"])  # noqa: F821
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


@manager.route("/hr/leave/balance", methods=["PUT"])  # noqa: F821
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
