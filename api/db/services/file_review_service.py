#
#  Copyright 2026 The InfiniFlow Authors. All Rights Reserved.
#
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.
#
"""文件审核 Service 层：模板 / 轮次 / 标注的 CRUD + 多轮判定 + 标注状态流转。

形态对齐 template_fill_service：继承 CommonService，类方法 + @DB.connection_context()
（每个方法一个连接作用域）。本层只做单条 INSERT/UPDATE/SELECT，不开显式事务 —— 没有
跨行不变量需要保证（新建轮次只写一行，改状态只改一行）。

上游 T1：api/db/db_models.py 的三个模型 + migrate_db 里的建表/补预置。
下游 T4 patcher / T6 executor / T7 节点 / T8 工具 / T9 API 消费本层。

────────────────────────────────────────────────────────────────────
审计字段契约（T1 审查踩坑，务必遵守）
────────────────────────────────────────────────────────────────────
FileReview* 三模型继承 DataBaseModel，因此：
  * create_time / update_time 是 BigIntegerField（13 位**毫秒整数**），由
    BaseModel.insert（写 create_time）与 _normalize_data（每次 insert/update 刷新
    update_time，并从 *_time 派生 *_date）自动维护；
  * 本层**禁止**手写 create_time= / update_time= / create_date= / update_date=，
    也不得把 datetime 对象塞进 update() —— BIGINT 列收到 datetime 会退化成零值
    或直接 DataError。需要按时间排序时直接 order_by(cls.model.create_time)。

────────────────────────────────────────────────────────────────────
状态枚举：本层不强制
────────────────────────────────────────────────────────────────────
  round.status      ∈ reviewing | annotated | fixing | failed | done
  annotation.status ∈ open | new | fixed | wontfix
两列均为 varchar、无 DB 约束，本层也不做白名单校验：非法值会照写。理由是「这个状态
合不合法」需要上下文 —— 同一个 "done"，在 T6 executor 里是正常收口、在 T9 API 里是
越权重放、在 T4 patcher 里是无意义写；判定连同拒绝策略（报错 / 降级 / 标 failed）
都应在那一层实现。本层返回是否命中行（bool），把「行不存在」交给上层区分 404 与 200。
test_update_status_writes_arbitrary_value_locks_contract 用例锁定了这一分工。
"""

from api.db.db_models import DB, FileReviewAnnotation, FileReviewRound, FileReviewTemplate
from api.db.services.common_service import CommonService
from common.misc_utils import get_uuid

# 「已完成」轮次口径：只有这两态算「本轮跑完、可在其上续下一轮」。
# failed 刻意不计入 —— 否则 T9 fix 端点会在失败轮次之上续接，而失败轮次没有可用
# 的 file_version 产物，新轮次会建在空基线上。
COMPLETED_ROUND_STATUSES = ("done", "annotated")

# 「待处理」标注口径：下一轮 LLM prompt 需要引用的未闭环问题。
PENDING_ANNOTATION_STATUSES = ("open", "new")


class FileReviewServiceBase(CommonService):
    """文件审核三个 Service 的公共基类：统一 get_by_id 的返回契约。"""

    @classmethod
    @DB.connection_context()
    def get_by_id(cls, pid):
        """按主键取模型实例；不存在或入参为空返回 None（不抛异常）。

        有意**不沿用** CommonService.get_by_id 的 `(ok, obj)` 元组返回：本模块调用方
        （T7 节点 / T9 API / T4 patcher）直接访问 `.status` / `.template_id` / `.file_id`，
        元组会让这些属性访问走到 AttributeError，或被 `if row:` 误判为真。
        """
        if not pid:
            return None  # 短路：避免把 None/"" 变成 `id = NULL` 这类恒假条件去打库
        try:
            return cls.model.get(cls.model.id == pid)
        except cls.model.DoesNotExist:
            return None


class FileReviewTemplateService(FileReviewServiceBase):
    model = FileReviewTemplate

    @classmethod
    @DB.connection_context()
    def list_enabled(cls, tenant_id: str = "") -> list:
        """可用模板 = 系统预置（tenant_id == ""）+ 本租户自有，且 enabled == 1。

        括号不可省：Python 里 `|` 的优先级低于 `==`，`a == x | b == y` 会先算
        `x | b` 而非两个比较结果做或运算。peewee 的 `.where(a, b)` 语义是 `a AND b`，
        故最终条件为 `((tenant_id = ?) OR (tenant_id = '')) AND enabled = 1`。
        tenant_id 传 "" 时两个或分支退化为同一条件，等价于「只看系统预置」，语义正确。
        """
        return list(cls.model.select().where(
            ((cls.model.tenant_id == tenant_id) | (cls.model.tenant_id == "")),
            cls.model.enabled == 1,
        ).order_by(cls.model.id))


class FileReviewRoundService(FileReviewServiceBase):
    model = FileReviewRound

    @classmethod
    @DB.connection_context()
    def create_round(cls, *, task_id: str, file_id: str, round_no: int,
                     template_id: str, user_query: str, file_version: str,
                     status: str, tenant_id: str = "", created_by: str = "") -> str:
        """新建一轮审核，返回轮次 id。

        tenant_id / created_by 由调用方（T7 节点、T8 工具、T9 API）从会话上下文透传，
        本层不猜。不传任何时间字段，四个审计列全部由框架写（见模块 docstring）。
        必填列为 None 时不吞异常：MySQL NOT NULL 违例被 peewee 转成 IntegrityError
        向上抛，让调用方自己判断该回 400 还是 500。
        """
        rid = get_uuid()
        cls.model.create(
            id=rid, task_id=task_id, file_id=file_id, round_no=round_no,
            template_id=template_id, user_query=user_query,
            file_version=file_version, status=status,
            tenant_id=tenant_id, created_by=created_by,
        )
        return rid

    @classmethod
    @DB.connection_context()
    def update_status(cls, rid: str, status: str, **extra) -> bool:
        """就地改状态并合入 extra（summary / llm_raw / error / minio_path 等）。

        update_time 由 _normalize_data 自动刷新（不得手写）。返回是否真的改到行：
        False 表示该 id 不存在，供 T9 区分 404 与 200，避免「任务丢了却静默 200」。
        """
        fields = {"status": status}
        fields.update(extra)
        return cls.model.update(**fields).where(cls.model.id == rid).execute() > 0

    @classmethod
    @DB.connection_context()
    def max_completed_round_no(cls, task_id: str) -> int:
        """task_id 下**已完成**（done/annotated）的最大轮次号；无完成轮次返回 0。

        T9 fix 端点用它算 new_no = max + 1。reviewing/fixing/failed 一律不计入：
        否则一次失败或中断的轮次会把下一轮编号推高，还可能让新轮次建在没有产物的
        版本基线上。
        """
        row = cls.model.select(cls.model.round_no).where(
            (cls.model.task_id == task_id)
            & cls.model.status.in_(COMPLETED_ROUND_STATUSES)
        ).order_by(cls.model.round_no.desc()).first()
        return row.round_no if row else 0

    @classmethod
    @DB.connection_context()
    def get_by_task(cls, task_id: str) -> list:
        """该 task 的全部轮次，按轮次号升序（T9 用 [-1] 取当前轮）。

        次级 create_time 排序只作同 (task_id, round_no) 重复行的兜底 —— 该组合没有
        唯一约束（见 test_create_round_allows_duplicate_task_round_no），毫秒同刻插入
        的两行仍可能并列，调用方不应依赖绝对稳定序。
        """
        return list(cls.model.select().where(
            cls.model.task_id == task_id
        ).order_by(cls.model.round_no.asc(), cls.model.create_time.asc()))


class FileReviewAnnotationService(FileReviewServiceBase):
    model = FileReviewAnnotation

    @classmethod
    @DB.connection_context()
    def create(cls, *, round_id: str, task_id: str, file_id: str, file_version: str,
               anchor: str, matched_text: str, type: str, severity: str, issue: str,
               suggestion: str, source: str, status: str = "open",
               prev_annotation_id: str | None = None, tenant_id: str = "",
               created_by: str = "") -> str:
        """新建一条标注，返回标注 id。

        anchor 是**已序列化**的 JSON 字符串（docx: p_hash/offset/run_index；
        xlsx: sheet/cell）。序列化留在调用方：同一份 anchor 既要落库又要回传前端，
        由调用方统一口径可避免两处序列化结果漂移。
        prev_annotation_id 把新一轮的同类问题指回上一轮（多轮问题追踪）。
        时间字段同样全部交给框架写（见模块 docstring）。
        """
        aid = get_uuid()
        cls.model.create(
            id=aid, round_id=round_id, task_id=task_id, file_id=file_id,
            file_version=file_version, anchor=anchor, matched_text=matched_text,
            type=type, severity=severity, issue=issue, suggestion=suggestion,
            source=source, status=status, prev_annotation_id=prev_annotation_id,
            tenant_id=tenant_id, created_by=created_by,
        )
        return aid

    @classmethod
    @DB.connection_context()
    def update_status(cls, aid: str, status: str) -> bool:
        """改标注状态（T4 patcher 判 fixed/wontfix、T9 手动标 wontfix）；
        返回是否命中行。update_time 由框架刷新。"""
        return cls.model.update(status=status).where(cls.model.id == aid).execute() > 0

    @classmethod
    @DB.connection_context()
    def list_by_file_version(cls, file_id: str, file_version: str, task_id: str | None = None) -> list:
        """review-panel 加载用：某文件某版本的**全部**标注（AI + manual），按创建时间升序。

        task_id 为 None/空时**刻意不按任务隔离**：审核面板要展示同一文件版本上所有
        历史标注（含用户手动补充的），按 task 过滤会把跨任务的批注漏掉。这与
        max_completed_round_no / list_open_or_new_for_next_round 的强隔离是相反语义，
        改前请确认调用方（T9 annotations 端点 / T4 patcher）确实需要哪一种。
        """
        q = cls.model.select().where(
            (cls.model.file_id == file_id) & (cls.model.file_version == file_version))
        if task_id:
            q = q.where(cls.model.task_id == task_id)
        return list(q.order_by(cls.model.create_time.asc()))

    @classmethod
    @DB.connection_context()
    def list_open_or_new_for_next_round(cls, task_id: str, current_round_no: int) -> list:
        """下一轮 LLM prompt 引用用：**本轮**（同 task、round_no == current_round_no）
        中 status ∈ {open, new} 的标注，按创建时间升序。

        与「该 task 全部轮次的 open/new」的区别（原实现只按 task_id 取 round_id，
        current_round_no 是死参数）：
          * 上一轮已 resolved 的问题不该再喂给下一轮，否则 LLM 会重复标注同一处；
          * 更早轮次遗留的 open/new 也已在后续轮次的 prompt 里给过，重复引用既放大
            token，又会让 LLM 误判「这个问题一直没人修」。
        故先定位本轮的轮次行，再按 round_id 取标注；本轮不存在时返回 []（不是 None）。
        同 (task_id, round_no) 可能有多行（无唯一约束），统一用 round_id.in_() 覆盖。
        额外带上 task_id 条件：round_id 已隐含归属，这里是对「标注行挂错 round_id」
        这类脏数据的二次防御。
        """
        round_ids = [r.id for r in FileReviewRound.select(FileReviewRound.id).where(
            (FileReviewRound.task_id == task_id)
            & (FileReviewRound.round_no == current_round_no))]
        if not round_ids:
            return []
        return list(cls.model.select().where(
            (cls.model.task_id == task_id)
            & cls.model.round_id.in_(round_ids)
            & cls.model.status.in_(PENDING_ANNOTATION_STATUSES)
        ).order_by(cls.model.create_time.asc()))
