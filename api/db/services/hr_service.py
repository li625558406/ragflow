"""人事模块 P1 Service：员工档案 / 规则配置 / 考勤流水 / 日月汇总。"""
import calendar
import json
from datetime import date, datetime, timedelta

from api.db.db_models import (
    DB,
    HrAttendanceDay,
    HrAttendanceMonth,
    HrAttendanceRecord,
    HrEmployee,
    HrRuleConfig,
    User,
)
from api.db.services.common_service import CommonService
from api.db.services.hr_calculator import derive_day_status, load_rule
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
                       "missing": 0, "leave": 0.0}
                d = max(date(year, mon, 1), entry)
                while d <= min(month_end, today):
                    existing = HrAttendanceDayService.model.get_or_none(
                        HrAttendanceDayService.model.employee_id == emp.id,
                        HrAttendanceDayService.model.work_date == d)
                    if existing and existing.locked:
                        # 锁定行按存量值聚合，不重新推导
                        status = existing.status
                        late_min = existing.late_minutes
                    else:
                        records = HrAttendanceRecordService.list_day(emp.id, d)
                        derived = derive_day_status(records, d, rule)
                        if derived["status"] == "missing":
                            derived["status"] = "absent"  # 确认时缺卡转旷工
                            agg["missing"] += 1
                        row = HrAttendanceDayService.upsert_day(
                            emp.id, d, derived, locked=True)
                        status = row.status
                        late_min = row.late_minutes
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
                            overtime_hours=0.0, status="confirmed",
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
