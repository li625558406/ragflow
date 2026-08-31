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
from api.db.db_models import HrAttendanceDay, HrAttendanceMonth, HrEmployee
from api.db.services.hr_calculator import derive_day_status
from api.db.services.hr_service import (
    HrAttendanceDayService,
    HrAttendanceMonthService,
    HrAttendanceRecordService,
    HrEmployeeService,
    HrRuleConfigService,
)
from api.utils.api_utils import get_data_error_result, get_json_result
from api.utils.permission_utils import permission_required

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
    derived = derive_day_status(records, today, rule)
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
    days = []
    for d in range(1, _cal.monthrange(year, mon)[1] + 1):
        wd = date(year, mon, d)
        key = str(wd)
        if key in rows:
            days.append(rows[key])
        elif wd <= today:
            records = HrAttendanceRecordService.list_day(emp.id, wd)
            derived = derive_day_status(records, wd, rule)
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
        query = query.where(
            HrEmployee.emp_no.contains(keyword)
            | HrEmployee.department.contains(keyword)
            | HrEmployee.position.contains(keyword))
    if department:
        query = query.where(HrEmployee.department == department)
    emps = list(query.order_by(HrEmployee.emp_no))
    return get_json_result(data={"list": [_emp_dict(e) for e in emps],
                                 "total": len(emps)})


@manager.route("/hr/employee", methods=["POST"])  # noqa: F821
@permission_required("hr_manage")
async def hr_employee_create():
    body = await request.get_json(silent=True) or {}
    user_id = str(body.get("user_id") or "").strip()
    emp_no = str(body.get("emp_no") or "").strip()
    if not user_id or not emp_no:
        return get_data_error_result(message="user_id 和 emp_no 必填")
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
