"""人事模块 P1 Service：员工档案 / 规则配置 / 考勤流水 / 日月汇总。"""
import calendar
import json
from datetime import date, datetime, time, timedelta

from peewee import IntegrityError

from api.db.db_models import (
    DB,
    HrAttendanceDay,
    HrAttendanceImport,
    HrAttendanceMonth,
    HrAttendanceRecord,
    HrEmployee,
    HrLeaveBalance,
    HrLeaveRequest,
    HrLeaveStep,
    HrPayslip,
    HrPayslipAdjust,
    HrRuleConfig,
    HrSalaryProfile,
    HrVoucher,
    User,
)
from api.db.services.common_service import CommonService
from api.db.services.hr_calculator import derive_day_status, leave_status_for_date, load_rule
from api.db.services.hr_payroll import apply_adjustment, build_voucher_entries, calc_payslip
from common.misc_utils import get_uuid
from common.time_utils import current_timestamp


class HrEmployeeService(CommonService):
    model = HrEmployee

    @classmethod
    def get_by_user(cls, user_id: str):
        return cls.model.get_or_none(cls.model.user_id == user_id)

    @classmethod
    def create_employee(cls, user_id: str, emp_no: str, department: str = "",
                        position: str = "", entry_date=None):
        if cls.get_by_user(user_id):
            raise ValueError("该用户已建档")
        if cls.model.get_or_none(cls.model.emp_no == emp_no):
            raise ValueError("工号已存在")
        if not User.get_or_none(User.id == user_id):
            raise ValueError("用户不存在")
        # CommonService.insert 返回 Peewee save() 的受影响行数（int），须回查实例
        cls.insert(user_id=user_id, emp_no=emp_no, department=department,
                   position=position, entry_date=entry_date, status="active")
        return cls.get_by_user(user_id)


class HrRuleConfigService(CommonService):
    model = HrRuleConfig

    @classmethod
    def get_config(cls) -> dict:
        row = cls.model.get_or_none(cls.model.id == "global")
        return load_rule(row.config if row else None)

    @classmethod
    def save_config(cls, config: dict) -> dict:
        merged = cls.get_config()
        merged.update({k: v for k, v in (config or {}).items() if k in merged})
        row = cls.model.get_or_none(cls.model.id == "global")
        payload = json.dumps(merged, ensure_ascii=False)
        if row:
            row.config = payload
            row.update_time = current_timestamp()
            row.save()
        else:
            cls.insert(id="global", config=payload)
        return merged


class HrAttendanceRecordService(CommonService):
    model = HrAttendanceRecord

    @classmethod
    def punch(cls, employee_id: str, source: str = "web", ip: str = "",
              punch_time: datetime | None = None, remark: str = ""):
        pt = punch_time or datetime.now()
        # 同分钟去重：任何来源的同分钟重复打卡拒绝
        dup = cls.model.select().where(
            cls.model.employee_id == employee_id,
            cls.model.punch_time >= pt.replace(second=0, microsecond=0),
            cls.model.punch_time < pt.replace(second=0, microsecond=0) + timedelta(minutes=1),
        ).exists()
        if dup:
            raise ValueError("重复打卡：该分钟已有打卡记录")
        # insert 返回 int（受影响行数），显式生成 id 后回查实例供端点取 rec.id/punch_time
        rid = get_uuid()
        cls.insert(id=rid, employee_id=employee_id, punch_time=pt, source=source,
                   ip_address=ip, remark=remark)
        return cls.model.get_by_id(rid)

    @classmethod
    def list_day(cls, employee_id: str, work_date: date) -> list:
        start = datetime.combine(work_date, datetime.min.time())
        end = start + timedelta(days=1)
        return list(cls.model.select().where(
            cls.model.employee_id == employee_id,
            cls.model.punch_time >= start,
            cls.model.punch_time < end,
        ).order_by(cls.model.punch_time))


class HrAttendanceDayService(CommonService):
    model = HrAttendanceDay

    @classmethod
    def upsert_day(cls, employee_id: str, work_date: date, derived: dict,
                   locked: bool = False):
        row = cls.model.get_or_none(
            cls.model.employee_id == employee_id, cls.model.work_date == work_date)
        fields = dict(
            status=derived["status"],
            first_in=derived["first_in"],
            last_out=derived["last_out"],
            late_minutes=derived["late_minutes"],
            overtime_hours=derived.get("overtime_hours", 0.0),
        )
        if row:
            if row.locked:
                return row  # 锁定行不覆盖
            for k, v in fields.items():
                setattr(row, k, v)
            row.locked = locked or row.locked
            row.update_time = current_timestamp()
            row.save()
            return row
        rid = get_uuid()
        cls.insert(id=rid, employee_id=employee_id, work_date=work_date,
                   locked=locked, **fields)
        return cls.model.get_by_id(rid)

    @classmethod
    def month_days(cls, employee_id: str, month: str) -> list:
        return list(cls.model.select().where(
            cls.model.employee_id == employee_id,
            cls.model.work_date >= date(int(month[:4]), int(month[5:7]), 1),
            cls.model.work_date < _next_month(int(month[:4]), int(month[5:7])),
        ).order_by(cls.model.work_date))


def _next_month(year: int, month: int) -> date:
    return date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)


class HrAttendanceMonthService(CommonService):
    model = HrAttendanceMonth

    @classmethod
    def close_month(cls, month: str, operator_id: str) -> dict:
        """月度一键汇总：逐员工逐日推导落盘 → missing 转 absent → 聚合月行并锁定。

        幂等语义：已确认的员工跳过，未确认的补跑；整批包裹在事务中，失败整体回滚。
        """
        if month == date.today().strftime("%Y-%m"):
            raise ValueError("当月尚未结束，不能汇总归档")
        confirmed_ids = {
            r.employee_id for r in cls.model.select().where(
                cls.model.status == "confirmed", cls.model.month == month)
        }

        rule = HrRuleConfigService.get_config()
        employees = list(HrEmployee.select().where(HrEmployee.status == "active"))
        year, mon = int(month[:4]), int(month[5:7])
        last_day = calendar.monthrange(year, mon)[1]
        month_end = date(year, mon, last_day)
        today = date.today()
        stats = {"employees": 0, "days": 0}

        with DB.atomic():
            for emp in employees:
                if emp.id in confirmed_ids:
                    continue
                entry = emp.entry_date or date(year, mon, 1)
                stats["employees"] += 1
                agg = {"attend": 0.0, "late": 0, "late_min": 0, "absent": 0,
                       "missing": 0, "leave": 0.0, "ot": 0.0}
                d = max(date(year, mon, 1), entry)
                while d <= min(month_end, today):
                    existing = HrAttendanceDayService.model.get_or_none(
                        HrAttendanceDayService.model.employee_id == emp.id,
                        HrAttendanceDayService.model.work_date == d)
                    if existing and existing.locked:
                        # 锁定行按存量值聚合，不重新推导
                        status = existing.status
                        late_min = existing.late_minutes
                        agg["ot"] += float(existing.overtime_hours or 0)
                    else:
                        records = HrAttendanceRecordService.list_day(emp.id, d)
                        leaves = HrLeaveRequestService.approved_requests(emp.id, d, d)
                        derived = derive_day_status(records, d, rule,
                            leave_status=leave_status_for_date(leaves, d))
                        if derived["status"] == "missing":
                            derived["status"] = "absent"  # 确认时缺卡转旷工
                            agg["missing"] += 1
                        row = HrAttendanceDayService.upsert_day(
                            emp.id, d, derived, locked=True)
                        status = row.status
                        late_min = row.late_minutes
                        agg["ot"] += float(derived.get("overtime_hours", 0.0) or 0)
                    stats["days"] += 1
                    if status in ("normal", "late"):
                        agg["attend"] += 1
                    if status == "late":
                        agg["late"] += 1
                        agg["late_min"] += late_min
                    elif status == "absent":
                        agg["absent"] += 1
                    elif status in ("leave", "business_trip"):
                        agg["leave"] += 1
                    d += timedelta(days=1)

                vals = dict(attend_days=agg["attend"], late_count=agg["late"],
                            late_minutes=agg["late_min"], absent_days=agg["absent"],
                            missing_days=agg["missing"], leave_days=agg["leave"],
                            overtime_hours=round(agg["ot"], 2), status="confirmed",
                            confirmed_by=operator_id)
                mrow = cls.model.get_or_none(
                    cls.model.employee_id == emp.id, cls.model.month == month)
                if mrow:
                    for k, v in vals.items():
                        setattr(mrow, k, v)
                    mrow.update_time = current_timestamp()
                    mrow.save()
                else:
                    cls.insert(id=get_uuid(), employee_id=emp.id, month=month, **vals)
        return stats


# 有额度假型 -> DEFAULT_RULE 配额键
QUOTA_KEY = {"annual": "annual_quota", "sick": "sick_quota",
             "marriage": "marriage_quota", "maternity": "maternity_quota"}


class HrLeaveBalanceService(CommonService):
    model = HrLeaveBalance

    @classmethod
    def get_or_init(cls, employee_id: str, year: int, leave_type: str, rule: dict):
        row = cls.model.get_or_none(
            cls.model.employee_id == employee_id, cls.model.year == year,
            cls.model.leave_type == leave_type)
        if row:
            return row
        total = int(rule.get(QUOTA_KEY[leave_type], 0) or 0)
        rid = get_uuid()
        cls.insert(id=rid, employee_id=employee_id, year=year,
                   leave_type=leave_type, total_days=total)
        return cls.model.get_by_id(rid)

    @classmethod
    def freeze(cls, employee_id: str, year: int, leave_type: str, days: int, rule: dict):
        """冻结额度；不足抛 ValueError。personal/business_trip/other/repair 不占额度直接返回。"""
        if leave_type not in QUOTA_KEY:
            return None
        row = cls.get_or_init(employee_id, year, leave_type, rule)
        if row.frozen_days + days > row.total_days - row.used_days:
            raise ValueError(
                f"{leave_type} 假期余额不足：剩余 {row.total_days - row.used_days - row.frozen_days} 天")
        row.frozen_days += days
        row.update_time = current_timestamp()
        row.save()
        return row

    @classmethod
    def release(cls, employee_id: str, year: int, leave_type: str, days: int):
        if leave_type not in QUOTA_KEY:
            return
        row = cls.model.get_or_none(
            cls.model.employee_id == employee_id, cls.model.year == year,
            cls.model.leave_type == leave_type)
        if row:
            row.frozen_days = max(0, row.frozen_days - days)
            row.update_time = current_timestamp()
            row.save()

    @classmethod
    def confirm(cls, employee_id: str, year: int, leave_type: str, days: int):
        """冻结转已用（终审通过）。"""
        if leave_type not in QUOTA_KEY:
            return
        row = cls.model.get_or_none(
            cls.model.employee_id == employee_id, cls.model.year == year,
            cls.model.leave_type == leave_type)
        if row:
            row.frozen_days = max(0, row.frozen_days - days)
            row.used_days += days
            row.update_time = current_timestamp()
            row.save()

    @classmethod
    def list_balance(cls, employee_id: str, year: int) -> list:
        return list(cls.model.select().where(
            cls.model.employee_id == employee_id, cls.model.year == year))


class HrLeaveStepService(CommonService):
    model = HrLeaveStep


class HrLeaveRequestService(CommonService):
    model = HrLeaveRequest

    @classmethod
    def resolve_approval_chain(cls, duration_days: int, rule: dict) -> list:
        """审批链 user_id 列表：≥3天用 approval_chain_long（空则回退 approval_chain），
        配置空回退超管列表。"""
        chain = str(rule.get("approval_chain") or "")
        if duration_days >= 3:
            chain = str(rule.get("approval_chain_long") or "") or chain
        uids = [u.strip() for u in chain.split(",") if u.strip()]
        if not uids:
            uids = [u.id for u in User.select(User.id).where(User.is_superuser == True)]
        return uids

    @classmethod
    def submit(cls, employee, leave_type: str, start_date: date, end_date: date,
               reason: str, applicant_id: str, rule: dict):
        if end_date < start_date:
            raise ValueError("结束日期不能早于开始日期")
        if leave_type == "repair" and start_date != end_date:
            raise ValueError("补卡申请只能选择单日")
        duration = (end_date - start_date).days + 1
        # freeze + 假单 + 审批步骤 原子化：任一步失败整体回滚（含冻结额度），杜绝孤儿 pending 单
        with DB.atomic():
            HrLeaveBalanceService.freeze(
                employee.id, start_date.year, leave_type, duration, rule)
            chain = cls.resolve_approval_chain(duration, rule)
            rid = get_uuid()
            cls.insert(id=rid, employee_id=employee.id, leave_type=leave_type,
                       start_date=start_date, end_date=end_date,
                       duration_days=duration, reason=reason[:500],
                       status="pending", current_step=1, applicant_id=applicant_id)
            for i, uid in enumerate(chain, start=1):
                HrLeaveStepService.insert(
                    id=get_uuid(), request_id=rid, step_no=i, approver_id=uid,
                    status="pending" if i == 1 else "waiting")
        return cls.model.get_by_id(rid)

    @classmethod
    def act(cls, request_id: str, approver_id: str, action: str, comment: str, rule: dict):
        """action ∈ approved|rejected。仅当前步骤审批人可操作；并发双击只成功一次。"""
        req = cls.model.get_by_id(request_id)
        if not req or req.status != "pending":
            raise ValueError("假单不存在或已结束")
        step = HrLeaveStep.get_or_none(
            HrLeaveStep.request_id == request_id,
            HrLeaveStep.step_no == req.current_step)
        if not step or step.status != "pending":
            raise ValueError("当前步骤已处理")
        if step.approver_id != approver_id:
            raise ValueError("你不是当前步骤审批人")
        emp = HrEmployee.get_by_id(req.employee_id)

        if action == "rejected":
            # 条件更新防并发：仅 status=="pending" 时推进一次，并发第二个请求 rowcount==0 直接拒绝
            n = (HrLeaveStep.update(
                    status="rejected", comment=comment[:255],
                    action_time=datetime.now())
                 .where(HrLeaveStep.id == step.id, HrLeaveStep.status == "pending")
                 .execute())
            if n == 0:
                raise ValueError("当前步骤已处理")
            n = (HrLeaveRequest.update(status="rejected")
                 .where(HrLeaveRequest.id == req.id, HrLeaveRequest.status == "pending")
                 .execute())
            if n == 0:
                raise ValueError("假单已结束")
            req.status = "rejected"  # 同步内存对象供端点返回最新状态
            HrLeaveBalanceService.release(emp.id, req.start_date.year,
                                          req.leave_type, req.duration_days)
            _rewrite_days(emp, req, rule, revert=True)
            return req

        nxt = HrLeaveStep.get_or_none(
            HrLeaveStep.request_id == request_id,
            HrLeaveStep.step_no == req.current_step + 1)
        is_final = not (nxt and nxt.status == "waiting")
        if req.leave_type == "repair":
            repair_time = datetime.combine(req.start_date, _parse_work_start(rule))
        if is_final and req.leave_type == "repair":
            # 终审前预检同分钟撞卡：在写入任何审批状态之前拦截，
            # 避免"审批已通过但补卡失败"，也避免步骤已推进导致卡单无法重试
            minute_start = repair_time.replace(second=0, microsecond=0)
            dup = HrAttendanceRecordService.model.select().where(
                HrAttendanceRecordService.model.employee_id == emp.id,
                HrAttendanceRecordService.model.punch_time >= minute_start,
                HrAttendanceRecordService.model.punch_time < minute_start + timedelta(minutes=1),
            ).exists()
            if dup:
                raise ValueError("该日期该分钟已有打卡记录，无法补卡")
        # 条件更新防并发：步骤 pending→approved 仅成功一次，终审不会双扣额度
        n = (HrLeaveStep.update(
                status="approved", comment=comment[:255],
                action_time=datetime.now())
             .where(HrLeaveStep.id == step.id, HrLeaveStep.status == "pending")
             .execute())
        if n == 0:
            raise ValueError("当前步骤已处理")
        if not is_final:
            n = (HrLeaveStep.update(status="pending")
                 .where(HrLeaveStep.id == nxt.id, HrLeaveStep.status == "waiting")
                 .execute())
            if n == 0:
                raise ValueError("当前步骤已处理")
            n = (HrLeaveRequest.update(current_step=req.current_step + 1)
                 .where(HrLeaveRequest.id == req.id, HrLeaveRequest.status == "pending")
                 .execute())
            if n == 0:
                raise ValueError("假单已结束")
            req.current_step += 1  # 同步内存对象供端点返回最新状态
            return req
        # 终审通过
        n = (HrLeaveRequest.update(status="approved")
             .where(HrLeaveRequest.id == req.id, HrLeaveRequest.status == "pending")
             .execute())
        if n == 0:
            raise ValueError("假单已结束")
        req.status = "approved"  # 同步内存对象供端点返回最新状态
        HrLeaveBalanceService.confirm(emp.id, req.start_date.year,
                                      req.leave_type, req.duration_days)
        if req.leave_type == "repair":
            HrAttendanceRecordService.punch(
                emp.id, source="repair", punch_time=repair_time,
                remark=f"补卡申请通过 {request_id}")
        else:
            _rewrite_days(emp, req, rule, revert=False)
        return req

    @classmethod
    def cancel(cls, request_id: str, operator_id: str, rule: dict):
        """仅申请人本人且 pending 可撤销。"""
        req = cls.model.get_by_id(request_id)
        if not req or req.status != "pending":
            raise ValueError("假单不存在或已结束")
        if req.applicant_id != operator_id:
            raise ValueError("只能撤销本人提交的假单")
        req.status = "cancelled"
        req.save()
        emp = HrEmployee.get_by_id(req.employee_id)
        HrLeaveBalanceService.release(emp.id, req.start_date.year,
                                      req.leave_type, req.duration_days)
        # 释放冻结时同步把 waiting/pending 步骤终结
        for s in HrLeaveStep.select().where(HrLeaveStep.request_id == request_id,
                                            HrLeaveStep.status.in_(["pending", "waiting"])):
            s.status = "rejected"
            s.comment = "申请人撤销"
            s.action_time = datetime.now()
            s.save()
        return req

    @classmethod
    def approved_requests(cls, employee_id: str, start: date, end: date) -> list:
        """与 [start,end] 有交集的 approved 假单（供日推导；repair 除外）。"""
        return list(cls.model.select().where(
            cls.model.employee_id == employee_id,
            cls.model.status == "approved",
            cls.model.leave_type != "repair",
            cls.model.start_date <= end,
            cls.model.end_date >= start).order_by(
            cls.model.start_date, cls.model.id))

    @classmethod
    def pending_for_approver(cls, approver_id: str) -> list:
        steps = HrLeaveStep.select().where(
            HrLeaveStep.approver_id == approver_id, HrLeaveStep.status == "pending")
        req_ids = [s.request_id for s in steps]
        if not req_ids:
            return []
        return list(cls.model.select().where(
            cls.model.id.in_(req_ids), cls.model.status == "pending"))


def _parse_work_start(rule: dict) -> time:
    try:
        parts = str(rule.get("work_start", "09:00")).split(":")
        return time(int(parts[0]), int(parts[1]))
    except (ValueError, IndexError, TypeError):
        return time(9, 0)


def _rewrite_days(emp, req, rule: dict, revert: bool):
    """审批终态后回写区间内未锁定日；revert=True 时重新推导恢复。
    若区间日已有其他 approved 假单覆盖则保持假单状态。"""
    d = req.start_date
    while d <= req.end_date:
        row = HrAttendanceDay.get_or_none(
            HrAttendanceDay.employee_id == emp.id, HrAttendanceDay.work_date == d)
        if row and not row.locked:
            if revert:
                records = HrAttendanceRecordService.list_day(emp.id, d)
                others = [r for r in HrLeaveRequestService.approved_requests(
                    emp.id, d, d) if r.id != req.id]
                ls = leave_status_for_date(others, d)
                derived = derive_day_status(records, d, rule, leave_status=ls)
                row.status = derived["status"]
                row.leave_id = others[0].id if others else ""
            else:
                row.status = ("business_trip" if req.leave_type == "business_trip"
                              else "leave")
                row.leave_id = req.id
            row.update_time = current_timestamp()
            row.save()
        d += timedelta(days=1)


# ── 薪资（P3）──

class HrSalaryProfileService(CommonService):
    model = HrSalaryProfile

    @classmethod
    def upsert_profile(cls, employee_id: str, data: dict):
        row = cls.model.get_or_none(cls.model.employee_id == employee_id)
        allowed = {"base_salary", "post_allowance", "meal_allowance",
                   "transport_allowance", "social_base", "fund_base",
                   "special_deduction", "social_rate", "fund_rate"}
        vals = {k: data[k] for k in allowed if k in data}
        if not row:
            rid = get_uuid()
            try:
                # CommonService.insert 返回受影响行数（int），显式生成 id 后回查实例
                cls.insert(id=rid, employee_id=employee_id, **vals)
                return cls.model.get_by_id(rid)
            except IntegrityError:
                # 并发插入撞 employee_id 唯一约束 → 回查已有行改走更新分支
                row = cls.model.get_or_none(cls.model.employee_id == employee_id)
                if not row:
                    raise  # 非唯一约束冲突，原样上抛
        for k, v in vals.items():
            setattr(row, k, v)
        row.update_time = current_timestamp()
        row.save()
        return row


class HrPayslipService(CommonService):
    model = HrPayslip

    @classmethod
    def _prev_snapshot(cls, employee_id: str, month: str):
        """本人 month 之前最近一条 payslip 的有效 snapshot（单条倒序查询）。

        month 为零填充 'YYYY-MM'，字典序即时间序。无历史行 / snapshot 坏 JSON /
        被手工覆盖(overridden) → None（累计链断点，按首月重算）。
        """
        row = (cls.model.select().where(
               cls.model.employee_id == employee_id, cls.model.month < month)
               .order_by(cls.model.month.desc()).first())
        if not row:
            return None
        try:
            snap = json.loads(row.tax_snapshot or "{}")
        except ValueError:
            return None
        if snap and not snap.get("overridden"):
            return snap
        return None  # 最近一条被手工覆盖，累计链断点，按首月重算

    @classmethod
    def compute_for_employee(cls, emp_id: str, month: str, rule: dict) -> dict:
        """核算单人：读 confirmed 月汇总 + 薪资档案 + 上月 snapshot → calc_payslip。
        抛 ValueError 当月未归档/无薪资档案。"""
        mrow = HrAttendanceMonth.get_or_none(
            HrAttendanceMonth.employee_id == emp_id, HrAttendanceMonth.month == month)
        if not mrow or mrow.status != "confirmed":
            raise ValueError(f"员工 {emp_id} 的 {month} 考勤未月度归档，请先执行月度汇总")
        prof = HrSalaryProfile.get_or_none(HrSalaryProfile.employee_id == emp_id)
        if not prof:
            raise ValueError(f"员工 {emp_id} 未建薪资档案")
        try:
            overrides = json.loads(prof.manual_overrides or "{}")
        except ValueError:
            overrides = {}
        if not isinstance(overrides, dict):
            overrides = {}
        # 加班分类：按日汇总行归类 weekday/weekend/holiday
        mon = int(month[5:7])
        days = HrAttendanceDayService.month_days(emp_id, month)
        ot = {"weekday": 0.0, "weekend": 0.0, "holiday": 0.0}
        hset = {d.strip() for d in str(rule.get("holidays") or "").split(",") if d.strip()}
        for d in days:
            if d.overtime_hours and d.overtime_hours > 0:
                if str(d.work_date) in hset:
                    ot["holiday"] += float(d.overtime_hours)
                elif d.status == "rest" or d.work_date.weekday() >= 5:
                    ot["weekend"] += float(d.overtime_hours)
                else:
                    ot["weekday"] += float(d.overtime_hours)
        profile_dict = {
            "base_salary": float(prof.base_salary), "post_allowance": float(prof.post_allowance),
            "meal_allowance": float(prof.meal_allowance),
            "transport_allowance": float(prof.transport_allowance),
            "social_base": float(prof.social_base), "fund_base": float(prof.fund_base),
            "special_deduction": float(prof.special_deduction),
            "social_rate": float(prof.social_rate) if prof.social_rate is not None else None,
            "fund_rate": float(prof.fund_rate) if prof.fund_rate is not None else None,
            "overrides": overrides,
        }
        stats = {"attend_days": float(mrow.attend_days), "late_count": mrow.late_count,
                 "absent_days": mrow.absent_days, "overtime": ot}
        prev = cls._prev_snapshot(emp_id, month)
        month_idx = mon
        result = calc_payslip(profile_dict, stats, profile_dict["base_salary"], rule,
                              prev_snap=prev, month_idx=month_idx)
        # 附带月汇总行供 calc 端点复用（省一次重复查询）；trial 端点白名单输出不会透出
        result["_mrow"] = mrow
        return result

    @classmethod
    def _update_draft(cls, row, vals: dict, emp_id: str, month: str):
        """条件更新已存在行：仅 status=="draft" 可覆盖（I1 修复：与 publish 端点
        竞态时 get-then-save 窗口曾可篡改已发布工资单）。rowcount==0 时重查行定位原因。"""
        n = (cls.model.update(**vals, update_time=current_timestamp())
             .where(cls.model.id == row.id, cls.model.status == "draft").execute())
        if n == 0:
            fresh = cls.model.get_or_none(cls.model.id == row.id)
            if fresh and fresh.status == "published":
                raise ValueError(f"员工 {emp_id} 的 {month} 工资单已发布，不可覆盖")
            raise ValueError(f"员工 {emp_id} 的 {month} 工资单保存失败：状态已变化，请重试")
        return cls.model.get_by_id(row.id)

    @classmethod
    def save_draft(cls, emp_id: str, month: str, r: dict, mrow) -> object:
        """upsert draft（published 拒绝覆盖）。

        并发防护：更新走「条件更新+rowcount」；插入撞 (employee_id, month) 唯一
        约束时回查已有行改走条件更新路径（仍受 published 拒绝保护）。
        """
        vals = {k: r[k] for k in ("base_salary", "allowances", "overtime_pay", "gross_pay",
                                  "attendance_deduction", "social_insurance",
                                  "housing_fund", "income_tax", "net_pay")}
        vals.update(overtime_hours=float(mrow.overtime_hours),
                    tax_snapshot=json.dumps(r.get("tax_snapshot", {}),
                                            ensure_ascii=False))
        row = cls.model.get_or_none(
            cls.model.employee_id == emp_id, cls.model.month == month)
        if not row:
            rid = get_uuid()
            try:
                cls.insert(id=rid, employee_id=emp_id, month=month,
                           attend_days=float(mrow.attend_days), late_count=mrow.late_count,
                           late_minutes=mrow.late_minutes, absent_days=mrow.absent_days,
                           leave_days=float(mrow.leave_days), status="draft", **vals)
                return cls.model.get_by_id(rid)
            except IntegrityError:
                row = cls.model.get_or_none(
                    cls.model.employee_id == emp_id, cls.model.month == month)
                if not row:
                    raise  # 非唯一约束冲突（如字段超长），原样上抛
        return cls._update_draft(row, vals, emp_id, month)

    @classmethod
    def adjust(cls, payslip_id: str, field: str, new_value, reason: str,
               operator_id: str) -> tuple:
        """published 工资单手工调整：重算 net、条件更新落盘、写调整日志。
        返回 (最新 payslip 实例, pay 凭证是否已过期 bool)。非法入参/状态抛 ValueError。"""
        row = cls.model.get_or_none(cls.model.id == payslip_id)
        if not row:
            raise ValueError("工资单不存在")
        if row.status != "published":
            raise ValueError("仅已发布的工资单可手工调整")
        old = {k: float(getattr(row, k) or 0) for k in
               ("gross_pay", "attendance_deduction", "social_insurance",
                "housing_fund", "income_tax", "net_pay")}
        new = apply_adjustment(old, field, new_value)
        # 条件更新防发布/调整竞态：仅仍为 published 时生效
        n = (cls.model.update(
                attendance_deduction=new["attendance_deduction"],
                social_insurance=new["social_insurance"],
                housing_fund=new["housing_fund"], income_tax=new["income_tax"],
                net_pay=new["net_pay"], update_time=current_timestamp())
             .where(cls.model.id == payslip_id, cls.model.status == "published").execute())
        if n == 0:
            raise ValueError("工资单状态已变化，请刷新后重试")
        HrPayslipAdjust.insert(id=get_uuid(), payslip_id=payslip_id,
                               employee_id=row.employee_id, month=row.month,
                               field=field, old_value=old[field],
                               new_value=new[field], reason=str(reason or "")[:255],
                               operator_id=operator_id)
        # 该月 pay 凭证若已生成则提示过期（仅提示，不自动重生成）
        stale = HrVoucher.get_or_none(HrVoucher.month == row.month,
                                      HrVoucher.voucher_type == "pay") is not None
        return cls.model.get_by_id(payslip_id), stale


# ── 财务凭证 / 考勤机导入（P4）──

_PSLIP_FLOAT_KEYS = ("gross_pay", "social_insurance", "housing_fund",
                     "income_tax", "attendance_deduction", "net_pay")


class HrVoucherService(CommonService):
    model = HrVoucher

    @classmethod
    def generate(cls, month: str, voucher_type: str, operator_id: str) -> object:
        """从该月 published payslips 汇总生成凭证；(month, voucher_type) 唯一，重生成覆盖。"""
        rows = list(HrPayslip.select().where(HrPayslip.month == month,
                                             HrPayslip.status == "published"))
        if not rows:
            raise ValueError(f"{month} 无已发布工资单，无法生成凭证")
        r = build_voucher_entries(
            [{k: float(getattr(p, k) or 0) for k in _PSLIP_FLOAT_KEYS} for p in rows],
            voucher_type)
        payload = {"entries": json.dumps(r["entries"], ensure_ascii=False),
                   "total_amount": r["total_amount"], "created_by": operator_id,
                   "update_time": current_timestamp()}
        row = cls.model.get_or_none(cls.model.month == month,
                                    cls.model.voucher_type == voucher_type)
        if row:
            for k, v in payload.items():
                setattr(row, k, v)
            row.save()
            return row
        rid = get_uuid()
        try:
            cls.insert(id=rid, month=month, voucher_type=voucher_type, **payload)
            return cls.model.get_by_id(rid)
        except IntegrityError:
            # 并发生成同一 (month, type) 撞唯一约束 → 回查改走覆盖分支
            row = cls.model.get_or_none(cls.model.month == month,
                                        cls.model.voucher_type == voucher_type)
            if not row:
                raise
            for k, v in payload.items():
                setattr(row, k, v)
            row.save()
            return row

    @classmethod
    def list_month(cls, month: str) -> list:
        return list(cls.model.select().where(cls.model.month == month))


class HrAttendanceImportService(CommonService):
    model = HrAttendanceImport

    @classmethod
    def batch_punch(cls, records: list, source: str, operator_id: str,
                    file_name: str = "") -> dict:
        """考勤机批量导入：逐条按 emp_no/employee_id 定位 + 复用同分钟去重 punch。
        失败行收集不中断（detail 只存前 50 条，fail_rows 记全部失败数）；批次落盘留痕。
        source ∈ api_sync|manual_excel。"""
        if source not in ("api_sync", "manual_excel"):
            raise ValueError("无效的导入来源")
        if not isinstance(records, list) or not records:
            raise ValueError("records 应为非空数组")
        if len(records) > 2000:
            raise ValueError("单批次最多 2000 条")
        ok, fails, fail_total = 0, [], 0
        for i, rec in enumerate(records):
            try:
                if not isinstance(rec, dict):
                    raise ValueError("记录须为对象")  # noqa: TRY004
                emp = None
                if rec.get("employee_id"):
                    emp = HrEmployee.get_or_none(HrEmployee.id == rec["employee_id"])
                elif rec.get("emp_no"):
                    emp = HrEmployee.get_or_none(HrEmployee.emp_no == rec["emp_no"])
                if not emp:
                    raise ValueError("员工不存在")
                pt = datetime.strptime(str(rec.get("punch_time") or ""),  # noqa: DTZ007
                                       "%Y-%m-%d %H:%M:%S")
                HrAttendanceRecordService.punch(emp.id, source=source, punch_time=pt)
                ok += 1
            except (ValueError, TypeError) as e:
                fail_total += 1
                if len(fails) < 50:
                    fails.append({"row": i, "emp": str(rec.get("emp_no")
                                                       or rec.get("employee_id") or ""),
                                  "punch_time": str(rec.get("punch_time") or ""),
                                  "error": str(e)})
        cls.insert(id=get_uuid(), source=source, file_name=str(file_name or "")[:255],
                   total_rows=len(records), success_rows=ok, fail_rows=fail_total,
                   detail=json.dumps(fails, ensure_ascii=False), operator_id=operator_id)
        return {"total": len(records), "success": ok, "failed": fails,
                "fail_total": fail_total}
