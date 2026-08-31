# 人事模块 P1（员工档案 + 打卡考勤）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** C端新增「人事」页签，交付员工档案管理 + 网页打卡（含IP）+ 考勤数据清洗推导 + 个人考勤日历 + HR全员月度汇总（功能点1/3/5全量 + 2的web部分 + 4的汇总部分）。

**Architecture:** 独立人事模块：`db_models.py` 加 5 张 `hr_*` 表（`init_database_tables` 启动时自动建表），`hr_calculator.py` 纯函数推导引擎（TDD），`hr_service.py` Service 层（继承 CommonService），`hr_app.py` Blueprint（自动被 `api/apps/__init__.py` 扫描注册，前缀 `/api/v1/hr`）。前端走 flow-service.ts 的 fetch 模式，页面挂 `c-chat/index.tsx` 的 `mainView='hr'`。HR管理接口用 `@permission_required('hr_manage')`，员工自助接口仅 `login_required`。

**Tech Stack:** Python Quart + Peewee + MySQL；React 18 + TS + Tailwind；pytest（纯函数单测）。

**设计依据:** `docs/superpowers/specs/2026-08-31-hr-module-design.md`

**范围说明（相对设计文档的一处微调）:** 员工自助补卡申请需要审批引擎（P2 建设内容），P1 先实现 **HR 直接补卡**（`POST /hr/attendance/repair`，source='repair'），员工自助申请版随 P2 假单审批引擎一起交付。

---

## File Structure

```
新建:
  api/db/services/hr_calculator.py        # 纯函数：打卡去重 + 日状态推导（无DB依赖，可单测）
  api/db/services/hr_service.py           # Service 层
  api/apps/restful_apis/hr_app.py         # REST Blueprint（/api/v1/hr/*）
  test/hr/test_hr_calculator.py           # 推导引擎对抗性单测
  web/src/pages/c-chat/hr/hr-types.ts     # 前端类型
  web/src/pages/c-chat/hr/attendance-view.tsx  # 考勤视图（员工+HR）
  web/src/pages/c-chat/hr/index.tsx       # 人事主页（子页签壳）
修改:
  api/constants.py                        # MODULE_PERMISSIONS 加 hr_manage/hr_finance
  api/db/db_models.py                     # 末尾加 5 张 hr_* 表
  web/src/constants/permission.ts         # 同步前端权限点
  web/src/services/hr-service.ts          # API 调用层（新建，fetch 模式）
  web/src/pages/c-chat/index.tsx          # mainView 联合类型 + 页签按钮 + 渲染分支
```

---

### Task 1: 权限点常量（后端 + 前端）

**Files:**
- Modify: `api/constants.py:37-50`
- Modify: `web/src/constants/permission.ts:6-34`

- [ ] **Step 1: 后端 MODULE_PERMISSIONS 加两个权限点**

在 `api/constants.py` 的 `MODULE_PERMISSIONS` 字典 `"permission_manage": "权限管理",` 之后追加：

```python
    "hr_manage": "人事管理",
    "hr_finance": "薪资财务",
```

（`hr_finance` P3 薪资阶段使用，此处与前端一次性对齐，避免 /permission 页面前后端 key 不一致。超管经 `permission_allowed` 的 is_superuser 直通，无需 seed 数据库。）

- [ ] **Step 2: 前端 permission.ts 同步**

`web/src/constants/permission.ts` 的 `ModulePermissionKey` 联合类型 `'permission_manage';` 后追加：

```typescript
  | 'hr_manage'
  | 'hr_finance';
```

`MODULE_PERMISSIONS` 映射 `permission_manage: '权限管理',` 后追加：

```typescript
  hr_manage: '人事管理',
  hr_finance: '薪资财务',
```

- [ ] **Step 3: 语法验证**

Run: `uv run python -m py_compile api/constants.py`
Expected: 无输出（成功）

- [ ] **Step 4: Commit**

```bash
git add api/constants.py web/src/constants/permission.ts
git commit -m "feat(hr): 权限点新增 hr_manage/hr_finance（前后端常量对齐）"
```

---

### Task 2: 5 张 hr_* ORM 表

**Files:**
- Modify: `api/db/db_models.py`（`FlowAiChat` 类之后、文件末尾的表定义区追加）

- [ ] **Step 1: 追加表定义**

在 `db_models.py` 中最后一个流程相关模型（`FlowAiChat`）之后追加：

```python
class HrEmployee(DataBaseModel):
    """员工档案：HR 建档，关联现有 user 表。未建档用户看不到人事自助功能。"""
    id = CharField(max_length=32, primary_key=True)
    user_id = CharField(max_length=32, null=False, unique=True, index=True, help_text="FK -> user.id")
    emp_no = CharField(max_length=32, null=False, unique=True, help_text="工号")
    department = CharField(max_length=64, null=False, default="", help_text="部门")
    position = CharField(max_length=64, null=False, default="", help_text="职位")
    entry_date = DateField(null=True, help_text="入职日期")
    status = CharField(max_length=16, null=False, default="active", index=True,
                       help_text="active|resigned")

    class Meta:
        db_table = "hr_employee"


class HrRuleConfig(DataBaseModel):
    """考勤/薪资全局规则配置：单行（id='global'），config 为 JSON 文本。"""
    id = CharField(max_length=32, primary_key=True, default="global")
    config = TextField(null=False, default="{}", help_text="规则 JSON，见 hr_calculator.DEFAULT_RULE")

    class Meta:
        db_table = "hr_rule_config"


class HrAttendanceRecord(DataBaseModel):
    """打卡流水（原始，只增不改）：清洗/去重发生在推导时。"""
    id = CharField(max_length=32, primary_key=True)
    employee_id = CharField(max_length=32, null=False, index=True, help_text="FK -> hr_employee.id")
    punch_time = DateTimeField(null=False, index=True, help_text="打卡时间")
    source = CharField(max_length=16, null=False, default="web",
                       help_text="web|api_sync|import|repair")
    ip_address = CharField(max_length=64, null=False, default="", help_text="打卡IP")
    remark = CharField(max_length=255, null=False, default="", help_text="备注（补卡原因等）")

    class Meta:
        db_table = "hr_attendance_record"


class HrAttendanceDay(DataBaseModel):
    """考勤日汇总（清洗后）：由流水推导生成，月度确认后锁定。"""
    id = CharField(max_length=32, primary_key=True)
    employee_id = CharField(max_length=32, null=False, index=True, help_text="FK -> hr_employee.id")
    work_date = DateField(null=False, index=True)
    status = CharField(max_length=16, null=False, default="missing", index=True,
                       help_text="normal|late|absent|leave|business_trip|rest|missing|abnormal")
    first_in = DateTimeField(null=True, help_text="当日最早打卡")
    last_out = DateTimeField(null=True, help_text="当日最晚打卡")
    late_minutes = IntegerField(null=False, default=0)
    overtime_hours = DecimalField(max_digits=6, decimal_places=2, null=False, default=0)
    leave_id = CharField(max_length=32, null=False, default="", help_text="关联假单（P2）")
    remark = CharField(max_length=255, null=False, default="")
    locked = BooleanField(null=False, default=False, help_text="月度确认后锁定")

    class Meta:
        db_table = "hr_attendance_day"
        indexes = ((("employee_id", "work_date"), True),)


class HrAttendanceMonth(DataBaseModel):
    """考勤月汇总：一键汇总生成，确认后归档。"""
    id = CharField(max_length=32, primary_key=True)
    employee_id = CharField(max_length=32, null=False, index=True, help_text="FK -> hr_employee.id")
    month = CharField(max_length=7, null=False, index=True, help_text="YYYY-MM")
    attend_days = DecimalField(max_digits=5, decimal_places=1, null=False, default=0,
                               help_text="出勤天数（normal+late）")
    late_count = IntegerField(null=False, default=0)
    late_minutes = IntegerField(null=False, default=0)
    absent_days = IntegerField(null=False, default=0)
    missing_days = IntegerField(null=False, default=0, help_text="缺卡天数（未转旷工的提醒数）")
    leave_days = DecimalField(max_digits=5, decimal_places=1, null=False, default=0)
    overtime_hours = DecimalField(max_digits=7, decimal_places=2, null=False, default=0)
    status = CharField(max_length=16, null=False, default="draft", index=True,
                       help_text="draft|confirmed")
    confirmed_by = CharField(max_length=32, null=False, default="")

    class Meta:
        db_table = "hr_attendance_month"
        indexes = ((("employee_id", "month"), True),)
```

- [ ] **Step 2: 验证导入不报错（本地无 DB，验证语法与字段名）**

Run: `uv run python -c "import ast; ast.parse(open('api/db/db_models.py', encoding='utf-8').read()); print('syntax OK')"`
Expected: `syntax OK`

- [ ] **Step 3: Commit**

```bash
git add api/db/db_models.py
git commit -m "feat(hr): 新增5张人事P1表 hr_employee/hr_rule_config/hr_attendance_record/day/month"
```

（表结构由 `init_database_tables` 在服务启动时自动创建，无需手写迁移；符合 CLAUDE.md「新表进初始化脚本」约束。）

---

### Task 3: hr_calculator.py 推导引擎（TDD）

**Files:**
- Create: `api/db/services/hr_calculator.py`
- Test: `test/hr/test_hr_calculator.py`

- [ ] **Step 1: 写失败测试**

创建 `test/hr/test_hr_calculator.py`：

```python
"""hr_calculator 对抗性单测：边界值 / 非法输入 / 状态耦合。"""
from datetime import datetime, date

import pytest

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
    records = [punch(9, 0), punch(9, 0, ), punch(9, 0)]
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
```

- [ ] **Step 2: 运行验证失败**

Run: `uv run pytest test/hr/test_hr_calculator.py -v`
Expected: FAIL（`ModuleNotFoundError: api.db.services.hr_calculator`）

- [ ] **Step 3: 实现 hr_calculator.py**

创建 `api/db/services/hr_calculator.py`：

```python
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
    "abnormal_end": "05:00",        # 半夜异常窗口止（含 04:59，05:00 本身不含→按 05:00 边界用 <）
    "late_deduction": 20,           # 迟到扣款 元/次（P3 使用）
    "absent_deduction_multiplier": 3,  # 旷工扣 日薪倍数（P3 使用）
    "overtime_rate_weekday": 15.0,  # 工作日加班 元/小时（P3 使用）
    "overtime_rate_weekend": 20.0,  # 周末加班 元/小时（P3 使用）
    "holiday_overtime_multiplier": 3.0,  # 法定节假日 日薪倍数（P3 使用）
    "pay_days": 21.75,              # 月标准计薪天数（P3 使用）
}


def load_rule(config_json: str | None) -> dict:
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


def _parse_hm(value, fallback: time) -> time:
    """'09:00' -> time(9,0)；非法输入回退 fallback。"""
    try:
        parts = str(value).split(":")
        return time(int(parts[0]), int(parts[1]))
    except (ValueError, IndexError, TypeError):
        return fallback


def dedup_punch_records(records: list) -> list:
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


def _in_abnormal_window(pt: datetime, rule: dict) -> bool:
    start = _parse_hm(rule.get("abnormal_start"), time(22, 0))
    end = _parse_hm(rule.get("abnormal_end"), time(5, 0))
    t = pt.time()
    if start > end:  # 跨午夜窗口 22:00-05:00
        return t >= start or t < end
    return start <= t <= end


def derive_day_status(records: list, work_date: date, rule: dict,
                      leave_status: str | None = None) -> dict:
    """推导某员工某天的考勤状态。

    返回 {status, first_in, last_out, late_minutes, punch_count}。
    status ∈ normal|late|rest|missing|abnormal|leave|business_trip（absent 由月度确认时从 missing 转换，P2 追加 leave 类）。
    优先级：有效假单 > abnormal > 迟到/正常判定 > rest > missing。
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
    work_start = _parse_hm(rule.get("work_start", DEFAULT_RULE["work_start"]), time(9, 0))
    threshold = int(rule.get("late_threshold_minutes", 10) or 0)
    limit = datetime.combine(work_date, work_start) + timedelta(minutes=threshold)
    first_in = status["first_in"]
    if first_in > limit:
        status["status"] = "late"
        status["late_minutes"] = int((first_in - datetime.combine(work_date, work_start)).total_seconds() // 60)
    else:
        status["status"] = "normal"
    return status
```

- [ ] **Step 4: 运行验证通过**

Run: `uv run pytest test/hr/test_hr_calculator.py -v`
Expected: 全部 PASS

- [ ] **Step 5: Commit**

```bash
git add api/db/services/hr_calculator.py test/hr/test_hr_calculator.py
git commit -m "feat(hr): 考勤推导引擎（去重/迟到阈值/半夜异常/假单优先）+对抗性单测"
```

---

### Task 4: hr_service.py Service 层

**Files:**
- Create: `api/db/services/hr_service.py`

- [ ] **Step 1: 实现 Service**

创建 `api/db/services/hr_service.py`：

```python
"""人事模块 P1 Service：员工档案 / 规则配置 / 考勤流水 / 日月汇总。"""
import calendar
import json
import logging
from datetime import date, datetime, timedelta

from peewee import fn

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

logger = logging.getLogger(__name__)


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
        return cls.insert(user_id=user_id, emp_no=emp_no, department=department,
                          position=position, entry_date=entry_date, status="active")


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
        return cls.insert(employee_id=employee_id, punch_time=pt, source=source,
                          ip_address=ip, remark=remark)

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
                   leave_status: str | None = None, locked: bool = False) -> object:
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
            row.update_time = current_timestamp()
            row.save()
            return row
        return cls.insert(id=get_uuid(), employee_id=employee_id, work_date=work_date,
                          locked=locked, **fields)

    @classmethod
    def month_days(cls, employee_id: str, month: str) -> list:
        return list(cls.model.select().where(
            cls.model.employee_id == employee_id,
            fn.date_format(cls.model.work_date, "%Y-%m") == month,
        ).order_by(cls.model.work_date))


class HrAttendanceMonthService(CommonService):
    model = HrAttendanceMonth

    @classmethod
    def close_month(cls, month: str, operator_id: str) -> dict:
        """月度一键汇总：逐员工逐日推导落盘 → missing 转 absent → 聚合月行并锁定。

        幂等：已 confirmed 的月份拒绝重跑（需先人工处理）。
        """
        prev = cls.model.select().where(cls.model.status == "confirmed",
                                        cls.model.month == month).exists()
        if prev:
            raise ValueError(f"{month} 已确认归档，不能重复汇总")

        rule = HrRuleConfigService.get_config()
        employees = list(HrEmployee.select().where(HrEmployee.status == "active"))
        year, mon = int(month[:4]), int(month[5:7])
        last_day = calendar.monthrange(year, mon)[1]
        today = date.today()
        month_end = date(year, mon, last_day)
        stats = {"employees": 0, "days": 0}

        for emp in employees:
            entry = emp.entry_date or date(year, mon, 1)
            stats["employees"] += 1
            agg = {"attend": 0.0, "late": 0, "late_min": 0, "absent": 0,
                   "missing": 0, "leave": 0.0, "ot": 0.0}
            d = max(date(year, mon, 1), entry)
            while d <= min(month_end, today):
                records = HrAttendanceRecordService.list_day(emp.id, d)
                derived = derive_day_status(records, d, rule)
                if derived["status"] == "missing":
                    derived["status"] = "absent"  # 确认时缺卡转旷工
                    agg["missing"] += 1
                HrAttendanceDayService.upsert_day(emp.id, d, derived, locked=True)
                stats["days"] += 1
                if derived["status"] in ("normal", "late"):
                    agg["attend"] += 1
                if derived["status"] == "late":
                    agg["late"] += 1
                    agg["late_min"] += derived["late_minutes"]
                elif derived["status"] == "absent":
                    agg["absent"] += 1
                elif derived["status"] in ("leave", "business_trip"):
                    agg["leave"] += 1
                d += timedelta(days=1)

            mrow = cls.model.get_or_none(
                cls.model.employee_id == emp.id, cls.model.month == month)
            vals = dict(attend_days=agg["attend"], late_count=agg["late"],
                        late_minutes=agg["late_min"], absent_days=agg["absent"],
                        missing_days=agg["missing"], leave_days=agg["leave"],
                        overtime_hours=agg["ot"], status="confirmed",
                        confirmed_by=operator_id)
            if mrow:
                for k, v in vals.items():
                    setattr(mrow, k, v)
                mrow.update_time = current_timestamp()
                mrow.save()
            else:
                cls.insert(id=get_uuid(), employee_id=emp.id, month=month, **vals)
        return stats
```

- [ ] **Step 2: 语法验证**

Run: `uv run python -m py_compile api/db/services/hr_service.py`
Expected: 无输出

- [ ] **Step 3: Commit**

```bash
git add api/db/services/hr_service.py
git commit -m "feat(hr): Service层——员工档案/规则配置/打卡流水/日月汇总+month-close幂等"
```

---

### Task 5: hr_app.py Blueprint

**Files:**
- Create: `api/apps/restful_apis/hr_app.py`

说明：`api/apps/__init__.py` 自动扫描 `restful_apis/*_app.py` 注册 Blueprint，url_prefix=`/api/v1`；文件内路由自带 `/hr` 前缀。

- [ ] **Step 1: 实现 Blueprint**

创建 `api/apps/restful_apis/hr_app.py`：

```python
"""人事模块 REST API（/api/v1/hr/*）。

权限模型：
- 员工自助接口（打卡/日历/我的档案）：login_required，且必须有 hr_employee 档案
- HR 管理接口（建档/全员汇总/补卡/月度归档/规则配置）：@permission_required("hr_manage")
"""
import logging
from datetime import date, datetime

from quart import Blueprint, request

from api.apps import current_user, login_required
from api.db.db_models import HrEmployee
from api.db.services.hr_service import (
    HrAttendanceDayService,
    HrAttendanceMonthService,
    HrAttendanceRecordService,
    HrEmployeeService,
    HrRuleConfigService,
)
from api.db.services.hr_calculator import derive_day_status
from api.utils.api_utils import get_data_error_result, get_json_result
from api.utils.permission_utils import permission_required

logger = logging.getLogger(__name__)

manager = Blueprint("rest_hr_app", __name__)


def _require_employee():
    """当前登录用户必须有员工档案；返回 (employee, None) 或 (None, 响应)。"""
    emp = HrEmployeeService.get_by_user(current_user.id)
    if not emp or emp.status != "active":
        return None, get_json_result(code=1004, message="未开通人事功能：请联系HR建档")
    return emp, None


def _client_ip() -> str:
    fwd = request.headers.get("X-Forwarded-For", "")
    return (fwd.split(",")[0].strip() if fwd else request.remote_addr) or ""


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
    body = await request.get_json(silent=True) or {}
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


@manager.route("/hr/attendance/calendar", methods=["GET"])  # noqa: F821
@login_required
async def hr_attendance_calendar():
    emp, err = _require_employee()
    if err:
        return err
    month = request.args.get("month", "")  # YYYY-MM
    try:
        year, mon = int(month[:4]), int(month[5:7])
    except (ValueError, TypeError):
        return get_data_error_result(message="month 格式应为 YYYY-MM")
    # 已落盘行优先；当日实时推导（含今天未汇总的打卡）
    rows = {str(r.work_date): _day_dict(r)
            for r in HrAttendanceDayService.month_days(emp.id, month)}
    import calendar as _cal
    today = date.today()
    days = []
    for d in range(1, _cal.monthrange(year, mon)[1] + 1):
        wd = date(year, mon, d)
        key = str(wd)
        if key in rows:
            days.append(rows[key])
        elif wd <= today:
            records = HrAttendanceRecordService.list_day(emp.id, wd)
            derived = derive_day_status(records, wd, HrRuleConfigService.get_config())
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
    user_id = (body.get("user_id") or "").strip()
    emp_no = (body.get("emp_no") or "").strip()
    if not user_id or not emp_no:
        return get_data_error_result(message="user_id 和 emp_no 必填")
    entry = None
    if body.get("entry_date"):
        try:
            entry = datetime.strptime(body["entry_date"], "%Y-%m-%d").date()
        except ValueError:
            return get_data_error_result(message="entry_date 格式应为 YYYY-MM-DD")
    try:
        emp = HrEmployeeService.create_employee(
            user_id, emp_no, department=body.get("department", ""),
            position=body.get("position", ""), entry_date=entry)
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
    try:
        pt = datetime.strptime(body.get("punch_time", ""), "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return get_data_error_result(message="punch_time 格式应为 YYYY-MM-DD HH:MM:SS")
    try:
        rec = HrAttendanceRecordService.punch(
            emp.id, source="repair", punch_time=pt,
            remark=(body.get("reason") or "")[:255])
    except ValueError as e:
        return get_data_error_result(message=str(e))
    return get_json_result(data={"id": rec.id, "punch_time": str(rec.punch_time)})


@manager.route("/hr/attendance/day-list", methods=["GET"])  # noqa: F821
@permission_required("hr_manage")
async def hr_attendance_day_list():
    month = request.args.get("month", "")
    if len(month) != 7:
        return get_data_error_result(message="month 格式应为 YYYY-MM")
    work_date = request.args.get("date", "")  # 可选：查某天全员
    query = HrAttendanceDay.select().join(
        HrEmployee, on=(HrAttendanceDay.employee_id == HrEmployee.id))
    from peewee import fn as _fn
    query = query.where(_fn.date_format(HrAttendanceDay.work_date, "%Y-%m") == month)
    if work_date:
        query = query.where(HrAttendanceDay.work_date == work_date)
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
    month = (body.get("month") or "").strip()
    if len(month) != 7:
        return get_data_error_result(message="month 格式应为 YYYY-MM")
    try:
        stats = HrAttendanceMonthService.close_month(month, current_user.id)
    except ValueError as e:
        return get_data_error_result(message=str(e))
    return get_json_result(data=stats)


@manager.route("/hr/attendance/month/<month>", methods=["GET"])  # noqa: F821
@permission_required("hr_manage")
async def hr_attendance_month_summary(month: str):
    rows = list(HrAttendanceMonthService.model.select().where(
        HrAttendanceMonthService.model.month == month))
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


@manager.route("/hr/rule-config", methods=["PUT"])  # noqa: F821
@permission_required("hr_manage")
async def hr_rule_config_put():
    body = await request.get_json(silent=True) or {}
    return get_json_result(data=HrRuleConfigService.save_config(body))
```

- [ ] **Step 2: 语法验证**

Run: `uv run python -m py_compile api/apps/restful_apis/hr_app.py`
Expected: 无输出

- [ ] **Step 3: Commit**

```bash
git add api/apps/restful_apis/hr_app.py
git commit -m "feat(hr): hr_app Blueprint——打卡/日历/建档/补卡/月度汇总/规则配置 12个端点"
```

---

### Task 6: 前端类型 + API 调用层

**Files:**
- Create: `web/src/pages/c-chat/hr/hr-types.ts`
- Create: `web/src/services/hr-service.ts`

- [ ] **Step 1: 类型定义**

创建 `web/src/pages/c-chat/hr/hr-types.ts`：

```typescript
export interface HrEmployeeProfile {
  id: string;
  user_id: string;
  emp_no: string;
  department: string;
  position: string;
  entry_date: string;
  status: 'active' | 'resigned';
}

export type DayStatus =
  | 'normal'
  | 'late'
  | 'absent'
  | 'leave'
  | 'business_trip'
  | 'rest'
  | 'missing'
  | 'abnormal'
  | 'future';

export interface AttendanceRecordItem {
  punch_time: string;
  source: string;
  ip_address: string;
}

export interface TodayPunch {
  work_date: string;
  status: DayStatus;
  first_in: string;
  last_out: string;
  late_minutes: number;
  records: AttendanceRecordItem[];
}

export interface CalendarDay {
  work_date: string;
  status: DayStatus;
  first_in: string;
  last_out: string;
  late_minutes: number;
  locked: boolean;
}

export interface HrRuleConfig {
  work_start: string;
  work_end: string;
  late_threshold_minutes: number;
  [key: string]: string | number;
}
```

- [ ] **Step 2: API 调用层（flow-service.ts 的 fetch 模式）**

创建 `web/src/services/hr-service.ts`：

```typescript
import type {
  CalendarDay,
  HrEmployeeProfile,
  HrRuleConfig,
  TodayPunch,
} from '@/pages/c-chat/hr/hr-types';

const BASE = '/api/v1';

function authHeaders(): HeadersInit {
  const token = localStorage.getItem('Authorization') || '';
  return { Authorization: token };
}

function getUserInfo(): { id?: string } {
  try {
    return JSON.parse(localStorage.getItem('userInfo') || '{}');
  } catch {
    return {};
  }
}

async function apiFetch<T>(path: string, init: RequestInit = {}): Promise<T> {
  const uid = getUserInfo().id || '';
  const url = `${BASE}${path}${path.includes('?') ? '&' : '?'}user_id=${encodeURIComponent(uid)}`;
  const resp = await fetch(url, {
    ...init,
    headers: { 'Content-Type': 'application/json', ...authHeaders(), ...(init.headers || {}) },
  });
  if (resp.status === 401) {
    localStorage.removeItem('Authorization');
    localStorage.removeItem('userInfo');
    window.location.href = '/login';
    throw new Error('unauthorized');
  }
  if (!resp.ok) throw new Error(`hr api ${resp.status}`);
  const body = await resp.json();
  if (body && typeof body === 'object' && 'code' in body) {
    if (body.code !== 0) throw new Error(body.message || `hr api code ${body.code}`);
    return body.data as T;
  }
  return body as T;
}

// ── 员工自助 ──

export async function fetchMyProfile(): Promise<{ profile: HrEmployeeProfile | null }> {
  return apiFetch('/hr/employee/me');
}

export async function punch(action: 'in' | 'out'): Promise<{ punch_time: string; today: TodayPunch }> {
  return apiFetch('/hr/attendance/punch', { method: 'POST', body: JSON.stringify({ action }) });
}

export async function fetchToday(): Promise<TodayPunch> {
  return apiFetch('/hr/attendance/today');
}

export async function fetchCalendar(month: string): Promise<{ month: string; days: CalendarDay[] }> {
  return apiFetch(`/hr/attendance/calendar?month=${encodeURIComponent(month)}`);
}

// ── HR 管理 ──

export async function listEmployees(params: { keyword?: string; department?: string } = {}) {
  const qs = new URLSearchParams(
    Object.entries(params).filter(([, v]) => v) as [string, string][],
  ).toString();
  return apiFetch<{ list: HrEmployeeProfile[]; total: number }>(
    `/hr/employee${qs ? `?${qs}` : ''}`,
  );
}

export async function createEmployee(data: {
  user_id: string;
  emp_no: string;
  department?: string;
  position?: string;
  entry_date?: string;
}): Promise<HrEmployeeProfile> {
  return apiFetch('/hr/employee', { method: 'POST', body: JSON.stringify(data) });
}

export async function repairPunch(data: {
  employee_id: string;
  punch_time: string;
  reason: string;
}) {
  return apiFetch('/hr/attendance/repair', { method: 'POST', body: JSON.stringify(data) });
}

export async function fetchDayList(month: string, date?: string) {
  const qs = new URLSearchParams({ month, ...(date ? { date } : {}) }).toString();
  return apiFetch<{ list: Record<string, unknown>[]; total: number }>(
    `/hr/attendance/day-list?${qs}`,
  );
}

export async function monthClose(month: string): Promise<{ employees: number; days: number }> {
  return apiFetch('/hr/attendance/month-close', {
    method: 'POST',
    body: JSON.stringify({ month }),
  });
}

export async function fetchMonthSummary(month: string) {
  return apiFetch<{ list: Record<string, unknown>[] }>(
    `/hr/attendance/month/${encodeURIComponent(month)}`,
  );
}

export async function fetchRuleConfig(): Promise<HrRuleConfig> {
  return apiFetch('/hr/rule-config');
}

export async function saveRuleConfig(data: Partial<HrRuleConfig>): Promise<HrRuleConfig> {
  return apiFetch('/hr/rule-config', { method: 'PUT', body: JSON.stringify(data) });
}
```

- [ ] **Step 3: 类型检查**

Run: `cd web && npx tsc --noEmit -p tsconfig.json 2>&1 | head -20`
Expected: 无 hr 相关报错（其他既有报错忽略）

- [ ] **Step 4: Commit**

```bash
git add web/src/pages/c-chat/hr/hr-types.ts web/src/services/hr-service.ts
git commit -m "feat(hr): 前端类型定义 + hr-service API调用层"
```

---

### Task 7: 前端考勤视图（员工打卡 + 日历 + HR管理）

**Files:**
- Create: `web/src/pages/c-chat/hr/attendance-view.tsx`

- [ ] **Step 1: 实现组件**

创建 `web/src/pages/c-chat/hr/attendance-view.tsx`：

```tsx
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { CalendarDays, Clock, LogIn, LogOut, Wrench } from 'lucide-react';
import { useMemo, useState } from 'react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import {
  usePermission,
} from '@/hooks/use-permission';
import {
  createEmployee,
  fetchCalendar,
  fetchDayList,
  fetchMonthSummary,
  fetchMyProfile,
  fetchToday,
  monthClose,
  punch,
  repairPunch,
} from '@/services/hr-service';
import type { CalendarDay, DayStatus, TodayPunch } from './hr-types';

const STATUS_STYLE: Record<DayStatus, { bg: string; label: string }> = {
  normal: { bg: 'bg-emerald-500/80 text-white', label: '正常' },
  late: { bg: 'bg-amber-500/80 text-white', label: '迟到' },
  absent: { bg: 'bg-red-500/80 text-white', label: '旷工' },
  leave: { bg: 'bg-blue-500/80 text-white', label: '请假' },
  business_trip: { bg: 'bg-purple-500/80 text-white', label: '出差' },
  rest: { bg: 'bg-slate-300/60 text-slate-600', label: '休' },
  missing: { bg: 'bg-gray-400/70 text-white', label: '缺卡' },
  abnormal: { bg: 'bg-orange-600/80 text-white', label: '异常' },
  future: { bg: 'bg-transparent text-[#94A3B8]', label: '' },
};

function fmtTime(iso: string) {
  return iso ? iso.slice(11, 16) : '--:--';
}

function todayMonth() {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`;
}

// ── 员工打卡卡片 ──

function PunchCard({ today, onPunching }: { today: TodayPunch; onPunching: () => void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const doPunch = async (action: 'in' | 'out') => {
    setBusy(true);
    setError('');
    try {
      await punch(action);
      onPunching();
    } catch (e) {
      setError(e instanceof Error ? e.message : '打卡失败');
    } finally {
      setBusy(false);
    }
  };
  const st = STATUS_STYLE[today.status] ?? STATUS_STYLE.missing;
  return (
    <div className="rounded-xl border border-[#E2E8F0] bg-white p-5 shadow-sm">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2 text-sm text-[#475569]">
          <Clock className="size-4" />
          今日 · {today.work_date}
        </div>
        <span className={`rounded-full px-3 py-0.5 text-xs font-medium ${st.bg}`}>
          {st.label || '—'}
        </span>
      </div>
      <div className="mt-4 flex items-center gap-8">
        <div>
          <div className="text-xs text-[#94A3B8]">签到</div>
          <div className="text-2xl font-semibold text-[#0F172A]">{fmtTime(today.first_in)}</div>
        </div>
        <div>
          <div className="text-xs text-[#94A3B8]">签退</div>
          <div className="text-2xl font-semibold text-[#0F172A]">{fmtTime(today.last_out)}</div>
        </div>
        {today.late_minutes > 0 && (
          <div className="text-sm text-amber-600">迟到 {today.late_minutes} 分钟</div>
        )}
      </div>
      <div className="mt-4 flex gap-3">
        <Button
          disabled={busy || !!today.first_in}
          onClick={() => doPunch('in')}
          className="flex-1 gap-2 bg-[#1a66fb] text-white hover:bg-[#1554d6]"
        >
          <LogIn className="size-4" /> 签到
        </Button>
        <Button
          disabled={busy || !today.first_in || !!today.last_out}
          onClick={() => doPunch('out')}
          className="flex-1 gap-2 bg-[#1a66fb] text-white hover:bg-[#1554d6]"
        >
          <LogOut className="size-4" /> 签退
        </Button>
      </div>
      {error && <div className="mt-2 text-sm text-red-500">{error}</div>}
      {today.records.length > 0 && (
        <div className="mt-3 space-y-1 border-t border-[#F1F5F9] pt-3">
          {today.records.map((r, i) => (
            <div key={i} className="flex justify-between text-xs text-[#94A3B8]">
              <span>{r.punch_time.slice(11, 19)} · {r.source}</span>
              <span>IP {r.ip_address || '—'}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ── 员工考勤日历 ──

function AttendanceCalendar({ month }: { month: string }) {
  const { data } = useQuery({
    queryKey: ['hr-calendar', month],
    queryFn: () => fetchCalendar(month),
  });
  const byDate = useMemo(() => {
    const m = new Map<string, CalendarDay>();
    (data?.days ?? []).forEach((d) => m.set(d.work_date, d));
    return m;
  }, [data]);
  const [y, mo] = month.split('-').map(Number);
  const firstWeekday = new Date(y, mo - 1, 1).getDay();
  const daysInMonth = new Date(y, mo, 0).getDate();
  const cells: (CalendarDay | null)[] = [
    ...Array.from({ length: (firstWeekday + 6) % 7 }, () => null),
    ...Array.from({ length: daysInMonth }, (_, i) => ({
      work_date: `${month}-${String(i + 1).padStart(2, '0')}`,
      status: 'future' as DayStatus,
      first_in: '', last_out: '', late_minutes: 0, locked: false,
    })),
  ];
  return (
    <div className="rounded-xl border border-[#E2E8F0] bg-white p-5 shadow-sm">
      <div className="mb-3 flex items-center gap-2 text-sm font-medium text-[#0F172A]">
        <CalendarDays className="size-4" /> 考勤日历（{month}）
      </div>
      <div className="grid grid-cols-7 gap-1.5 text-center text-xs text-[#94A3B8]">
        {['一', '二', '三', '四', '五', '六', '日'].map((w) => (
          <div key={w} className="py-1">{w}</div>
        ))}
        {cells.map((c, i) => {
          if (!c) return <div key={`empty-${i}`} />;
          const day = byDate.get(c.work_date);
          const st = STATUS_STYLE[day?.status ?? 'future'] ?? STATUS_STYLE.future;
          return (
            <div
              key={c.work_date}
              title={`${day?.status ?? ''} ${fmtTime(day?.first_in ?? '')}-${fmtTime(day?.last_out ?? '')}`}
              className={`flex h-12 flex-col items-center justify-center rounded-lg text-xs ${st.bg}`}
            >
              <span className="font-medium">{Number(c.work_date.slice(8))}</span>
              {st.label && <span className="text-[10px] leading-3">{st.label}</span>}
            </div>
          );
        })}
      </div>
    </div>
  );
}

// ── HR 管理面板 ──

function HrAdminPanel({ month, onRefresh }: { month: string; onRefresh: () => void }) {
  const qc = useQueryClient();
  const [keyword, setKeyword] = useState('');
  const [newEmp, setNewEmp] = useState({ user_id: '', emp_no: '', department: '', entry_date: '' });
  const [msg, setMsg] = useState('');
  const employees = useQuery({
    queryKey: ['hr-employees', keyword],
    queryFn: () => listEmployees({ keyword }),
  });
  const dayList = useQuery({
    queryKey: ['hr-daylist', month],
    queryFn: () => fetchDayList(month),
  });
  const summary = useQuery({
    queryKey: ['hr-month-summary', month],
    queryFn: () => fetchMonthSummary(month),
  });

  const addEmployee = async () => {
    setMsg('');
    try {
      await createEmployee(newEmp);
      setMsg('建档成功');
      setNewEmp({ user_id: '', emp_no: '', department: '', entry_date: '' });
      qc.invalidateQueries({ queryKey: ['hr-employees'] });
    } catch (e) {
      setMsg(e instanceof Error ? e.message : '建档失败');
    }
  };

  const doMonthClose = async () => {
    setMsg('');
    try {
      const s = await monthClose(month);
      setMsg(`汇总完成：${s.employees} 名员工 / ${s.days} 天`);
      qc.invalidateQueries({ queryKey: ['hr-month-summary', month] });
      qc.invalidateQueries({ queryKey: ['hr-daylist', month] });
      onRefresh();
    } catch (e) {
      setMsg(e instanceof Error ? e.message : '汇总失败');
    }
  };

  const doRepair = async (employeeId: string) => {
    const pt = window.prompt('补卡时间（YYYY-MM-DD HH:MM:SS）：');
    if (!pt) return;
    const reason = window.prompt('补卡原因：') || '';
    try {
      await repairPunch({ employee_id: employeeId, punch_time: pt, reason });
      setMsg('补卡成功');
      qc.invalidateQueries({ queryKey: ['hr-daylist', month] });
    } catch (e) {
      setMsg(e instanceof Error ? e.message : '补卡失败');
    }
  };

  return (
    <div className="rounded-xl border border-[#E2E8F0] bg-white p-5 shadow-sm">
      <div className="mb-3 flex items-center justify-between">
        <div className="flex items-center gap-2 text-sm font-medium text-[#0F172A]">
          <Wrench className="size-4" /> HR 管理（{month}）
        </div>
        <Button onClick={doMonthClose} className="bg-[#1a66fb] text-white hover:bg-[#1554d6]">
          一键月度汇总
        </Button>
      </div>
      {msg && <div className="mb-2 text-sm text-[#1a66fb]">{msg}</div>}

      <div className="mb-3 flex flex-wrap items-end gap-2">
        <div>
          <div className="text-xs text-[#94A3B8]">搜索（工号/部门/职位）</div>
          <Input value={keyword} onChange={(e) => setKeyword(e.target.value)} className="w-56" />
        </div>
        <div className="text-xs text-[#94A3B8]">
          <div>建档：user_id / 工号 / 部门 / 入职日期</div>
          <div className="flex gap-1">
            <Input value={newEmp.user_id} placeholder="user_id"
              onChange={(e) => setNewEmp({ ...newEmp, user_id: e.target.value })} className="w-40" />
            <Input value={newEmp.emp_no} placeholder="工号"
              onChange={(e) => setNewEmp({ ...newEmp, emp_no: e.target.value })} className="w-24" />
            <Input value={newEmp.department} placeholder="部门"
              onChange={(e) => setNewEmp({ ...newEmp, department: e.target.value })} className="w-24" />
            <Input value={newEmp.entry_date} placeholder="2026-09-01"
              onChange={(e) => setNewEmp({ ...newEmp, entry_date: e.target.value })} className="w-32" />
            <Button onClick={addEmployee} className="bg-[#1a66fb] text-white hover:bg-[#1554d6]">建档</Button>
          </div>
        </div>
      </div>

      <div className="text-xs text-[#94A3B8]">员工档案（{employees.data?.total ?? 0}）</div>
      <div className="mb-3 max-h-40 overflow-auto rounded-lg border border-[#F1F5F9]">
        {(employees.data?.list ?? []).map((e) => (
          <div key={e.id} className="flex items-center justify-between border-b border-[#F8FAFC] px-3 py-1.5 text-xs">
            <span>{e.emp_no} · {e.department || '—'} · {e.position || '—'} · {e.status}</span>
            <button onClick={() => doRepair(e.id)} className="text-[#1a66fb] hover:underline">
              补卡
            </button>
          </div>
        ))}
      </div>

      <div className="text-xs text-[#94A3B8]">月度汇总（{summary.data?.list.length ?? 0} 人）</div>
      <div className="overflow-auto rounded-lg border border-[#F1F5F9]">
        <table className="w-full text-xs">
          <thead className="bg-[#F8FAFC] text-[#64748B]">
            <tr>
              <th className="px-2 py-1.5 text-left">工号</th>
              <th className="px-2 py-1.5">出勤</th>
              <th className="px-2 py-1.5">迟到</th>
              <th className="px-2 py-1.5">旷工</th>
              <th className="px-2 py-1.5">缺卡</th>
              <th className="px-2 py-1.5">状态</th>
            </tr>
          </thead>
          <tbody>
            {(summary.data?.list ?? []).map((r) => (
              <tr key={String(r.employee_id)} className="border-t border-[#F8FAFC] text-center">
                <td className="px-2 py-1.5 text-left">{String(r.emp_no)}</td>
                <td>{String(r.attend_days)}</td>
                <td>{String(r.late_count)}次/{String(r.late_minutes)}分</td>
                <td>{String(r.absent_days)}</td>
                <td>{String(r.missing_days)}</td>
                <td>{r.status === 'confirmed' ? '已归档' : '草稿'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {dayList.data && (
        <div className="mt-1 text-[10px] text-[#CBD5E1]">日汇总明细共 {dayList.data.total} 条</div>
      )}
    </div>
  );
}

// ── 主视图 ──

export default function AttendanceView() {
  const { hasPermission } = usePermission();
  const isHr = hasPermission('hr_manage');
  const [month, setMonth] = useState(todayMonth());
  const profile = useQuery({ queryKey: ['hr-my-profile'], queryFn: fetchMyProfile });
  const today = useQuery({
    queryKey: ['hr-today'],
    queryFn: fetchToday,
    enabled: !!profile.data?.profile,
  });
  const refreshAll = () => {
    today.refetch();
  };

  if (profile.isLoading) {
    return <div className="p-6 text-sm text-[#94A3B8]">加载中…</div>;
  }
  if (!profile.data?.profile) {
    return (
      <div className="p-6 text-sm text-[#94A3B8]">
        未开通人事功能：请联系 HR 在「人事管理」中为你建档。
      </div>
    );
  }
  if (!today.data) {
    return <div className="p-6 text-sm text-[#94A3B8]">加载考勤数据…</div>;
  }

  return (
    <div className="mx-auto w-full max-w-3xl space-y-4 p-4">
      <PunchCard today={today.data!} onPunching={refreshAll} />
      <AttendanceCalendar month={month} />
      <div className="flex items-center gap-2 text-xs text-[#94A3B8]">
        <span>月份</span>
        <Input
          type="month"
          value={month}
          onChange={(e) => setMonth(e.target.value)}
          className="w-40"
        />
      </div>
      {isHr && <HrAdminPanel month={month} onRefresh={refreshAll} />}
    </div>
  );
}
```

注意：`listEmployees`/`fetchDayList` 等已在 Task 6 的 hr-service.ts 中导出，需在文件顶部 import（上面代码已包含）。

- [ ] **Step 2: 类型检查**

Run: `cd web && npx tsc --noEmit -p tsconfig.json 2>&1 | head -30`
Expected: 无 attendance-view 相关报错

- [ ] **Step 3: Commit**

```bash
git add web/src/pages/c-chat/hr/attendance-view.tsx
git commit -m "feat(hr): 考勤视图——打卡卡片/考勤日历/HR建档补卡月度汇总面板"
```

---

### Task 8: 人事主页 + C端页签注册

**Files:**
- Create: `web/src/pages/c-chat/hr/index.tsx`
- Modify: `web/src/pages/c-chat/index.tsx`

- [ ] **Step 1: 人事主页（子页签壳）**

创建 `web/src/pages/c-chat/hr/index.tsx`：

```tsx
import { useState } from 'react';
import AttendanceView from './attendance-view';

// P2 追加「请假」、P3 追加「薪资」、P4 追加「报表」子页签（见设计文档 §7 阶段拆分）
const SUB_TABS = [{ key: 'attendance', label: '考勤' }] as const;

type SubTabKey = (typeof SUB_TABS)[number]['key'];

export default function HrView() {
  const [sub, setSub] = useState<SubTabKey>('attendance');
  return (
    <div className="flex h-full flex-col">
      <div className="flex justify-center gap-1 border-b border-[#E2E8F0] bg-white px-4 py-2">
        {SUB_TABS.map((t) => (
          <button
            key={t.key}
            onClick={() => setSub(t.key)}
            className={`rounded-lg px-4 py-1.5 text-sm font-medium transition-colors ${
              sub === t.key
                ? 'bg-[#1a66fb] text-white'
                : 'text-[#475569] hover:bg-[#F1F5F9]'
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>
      <div className="min-h-0 flex-1 overflow-auto">
        {sub === 'attendance' && <AttendanceView />}
      </div>
    </div>
  );
}
```

- [ ] **Step 2: c-chat/index.tsx 注册页签**

三处修改（`web/src/pages/c-chat/index.tsx`）：

① 第 566-568 行 `mainView` 联合类型加 `'hr'`：

```typescript
  const [mainView, setMainView] = useState<
    'chat' | 'collaboration' | 'tools' | 'bid' | 'favorites' | 'flow' | 'hr'
  >('chat');
```

② 第 1455-1473 行页签数组末尾（`bid` 项后）追加：

```typescript
                  {
                    key: 'hr',
                    label: '人事',
                    icon: 'users',
                  },
```

③ 第 2893 行 `{mainView === 'favorites' && ...}` 渲染区之后、与 flow 渲染分支并列处追加：

```tsx
                {mainView === 'hr' && (
                  <HrView />
                )}
```

并在文件顶部 import 区加入：

```tsx
import HrView from '@/pages/c-chat/hr';
```

（先 `Grep -n "mainView === 'flow'"` 定位 flow 渲染分支的确切行号，把 hr 分支插在它旁边，保持渲染分支聚在一起。）

- [ ] **Step 3: 类型检查**

Run: `cd web && npx tsc --noEmit -p tsconfig.json 2>&1 | head -30`
Expected: 无 hr 相关报错

- [ ] **Step 4: Commit**

```bash
git add web/src/pages/c-chat/hr/index.tsx web/src/pages/c-chat/index.tsx
git commit -m "feat(hr): C端顶部导航新增「人事」页签 + 人事主页子页签壳"
```

---

### Task 9: 全量验证 + CHANGE.md

**Files:**
- Modify: `CHANGE.md`

- [ ] **Step 1: 后端全量单测**

Run: `uv run pytest test/hr/ -v`
Expected: 全部 PASS

- [ ] **Step 2: 前端构建验证**

Run: `cd web && npx tsc --noEmit -p tsconfig.json 2>&1 | tail -5`
Expected: 无新增报错

- [ ] **Step 3: 后端 ruff**

Run: `uv run ruff check api/db/services/hr_calculator.py api/db/services/hr_service.py api/apps/restful_apis/hr_app.py api/constants.py`
Expected: 无报错（line-length 200 已在 pyproject 配置）

- [ ] **Step 4: 更新 CHANGE.md（增量追加，不覆盖已有记录）**

在 `CHANGE.md` 最新条目上方追加：

```markdown
## 2026-08-31 C端「人事」页签 P1：员工档案 + 打卡考勤

**主题**：C端新增「人事」页签，交付人事模块第一阶段（4模块20功能点中的 P1）。

**核心变更**：
- 设计文档：docs/superpowers/specs/2026-08-31-hr-module-design.md（4模块分4阶段）
- 新增5张表：hr_employee / hr_rule_config / hr_attendance_record / hr_attendance_day / hr_attendance_month（启动自动建表）
- 推导引擎 hr_calculator.py：打卡去重 / 迟到阈值 / 半夜异常窗口 / 假单优先，含对抗性单测
- hr_app.py：12个端点（打卡/今日/日历/建档/列表/补卡/日明细/月汇总/一键汇总/规则配置）
- RBAC 新增 hr_manage / hr_finance 权限点（前后端常量对齐）
- 前端：c-chat 顶部「人事」页签 + 考勤视图（打卡卡片/考勤日历/HR管理面板），中文硬编码
- 范围微调：员工自助补卡申请依赖审批引擎，移至 P2 与假单审批一起交付；P1 先支持 HR 直接补卡

**遗留**：待部署联调；请假/薪资/报表见 P2-P4。
```

并同步 `CLAUDE.md` 参考表中「人事模块设计」一行的状态描述（追加"P1已实施待部署"）。

- [ ] **Step 5: Commit**

```bash
git add CHANGE.md CLAUDE.md
git commit -m "docs: CHANGE.md 记录人事模块P1（员工档案+打卡考勤），CLAUDE.md 状态同步"
```

---

## 部署提醒（实施完成后，需用户确认才执行）

后端涉及 `db_models.py`（新表）+ 新增 3 个 Python 文件 + `constants.py`，按「成套 SCP」规范部署；首次启动后端服务时 `init_database_tables` 自动建表。前端走 `npm run build` + dist SCP 流程。**部署属用户明确指示的操作，本计划不包含自动部署步骤。**
