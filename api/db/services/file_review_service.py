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
唯一例外是**长度**：varchar 列超长会被 MySQL 静默截成半截值（非严格模式下不报错），
故本层用 _clamp_str 主动按列宽钳制，杜绝「写进去的枚举值只有一半」这种隐性腐坏。
"""

import json

from api.db.db_models import DB, FileReviewAnnotation, FileReviewRound, FileReviewTemplate, json_dumps
from api.db.services.common_service import CommonService
from common.misc_utils import get_uuid

# 「已完成」轮次口径：只有这两态算「本轮跑完、可在其上续下一轮」。
# failed 刻意不计入 —— 否则 T9 fix 端点会在失败轮次之上续接，而失败轮次没有可用
# 的 file_version 产物，新轮次会建在空基线上。
COMPLETED_ROUND_STATUSES = ("done", "annotated")

# 「待处理」标注口径：下一轮 LLM prompt 需要引用的未闭环问题。
PENDING_ANNOTATION_STATUSES = ("open", "new")


def _clamp_str(model, field_name: str, value):
    """按模型列 max_length 钳制字符串，防 MySQL 静默截断出半截枚举值。

    枚举白名单校验由上层（T4 patcher / T6 执行器）负责，此处不做值域判断。
    背景：本机 MySQL 当前 sql_mode 非严格，超长值既不报错也不回滚，而是被截成
    varchar 长度上限的**半截字符串**（如 severity 列 varchar(16) 收到
    "critical_blocking_需要人工确认" 静默落库为 "critical_blockin"）。严格模式下
    同一输入会抛 DataError，炸在 T6 守护线程里让轮次永久卡 reviewing。
    这里主动截断保证「写进去的就是上层给的完整值或被明确钳制的值」，不静默半截。

    未知列名（如未来新增但 model 尚未登记的 extra 键）与 TextField（无 max_length）
    原样返回，不抛异常；非字符串（None/0）原样透传。
    """
    if not isinstance(value, str):
        return value
    field = model._meta.fields.get(field_name)
    max_len = getattr(field, "max_length", None) if field is not None else None
    return value[:max_len] if max_len else value


def _normalize_kb_ids(kb_ids) -> str | None:
    """把 kb_ids 统一成 JSON 文本落库（None / 空 → None）。

    接受三种入参并归一，是因为三个调用方（T7 节点 / T8 工具 / T9 API）拿到的形态不同：
    节点来自画布 DSL（list）、工具来自 LLM 参数解析（可能是 JSON 文本）、API 来自
    request body。归一放在 Service 层，避免每个调用方各写一遍、口径漂移。
    """
    if kb_ids is None:
        return None
    if isinstance(kb_ids, str):
        raw = kb_ids.strip()
        if not raw:
            return None
        try:
            val = json.loads(raw)
        except Exception:  # noqa: BLE001 — 入参可能只是裸 id 文本（非 JSON），有明确降级分支
            return json_dumps([raw])          # 裸的单个 id 文本
        return _normalize_kb_ids(val)
    if isinstance(kb_ids, (list, tuple, set)):
        ids = [str(x) for x in kb_ids if x]
        return json_dumps(ids) if ids else None
    return None


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
                     status: str, tenant_id: str = "", created_by: str = "",
                     kb_ids=None) -> str:
        """新建一轮审核，返回轮次 id。

        tenant_id / created_by / kb_ids 由调用方（T7 节点、T8 工具、T9 API）从会话
        上下文透传，本层不猜。kb_ids 归一为 JSON 文本（见 _normalize_kb_ids）：修复轮
        与重试都要用同一批知识库，故必须随轮次持久化，不能只活在当次请求里。
        其余契约不变（必填列为 None 时不吞异常，时间字段全部由框架写）。
        """
        rid = get_uuid()
        cls.model.create(
            id=rid, task_id=task_id, file_id=file_id, round_no=round_no,
            template_id=template_id, user_query=user_query,
            file_version=_clamp_str(cls.model, "file_version", file_version),
            status=_clamp_str(cls.model, "status", status),
            tenant_id=tenant_id, created_by=created_by,
            kb_ids=_normalize_kb_ids(kb_ids),
        )
        return rid

    @classmethod
    @DB.connection_context()
    def update_status(cls, rid: str, status: str, **extra) -> bool:
        """就地改状态并合入 extra（summary / llm_raw / error / minio_path 等）。

        update_time 由 _normalize_data 自动刷新（不得手写）。返回是否真的改到行：
        False 表示该 id 不存在，供 T9 区分 404 与 200，避免「任务丢了却静默 200」。
        字符串字段按模型列宽钳制（见 _clamp_str），extra 中的未知键原样透传。
        """
        fields = {"status": _clamp_str(cls.model, "status", status)}
        for k, v in extra.items():
            fields[k] = _clamp_str(cls.model, k, v)
        if cls.model.update(**fields).where(cls.model.id == rid).execute() > 0:
            return True
        # affected rows = 实际变化行数（连接未开 CLIENT_FOUND_ROWS），不是匹配行数：
        # 同毫秒 + 同值更新时整行无净变化 -> 0，但行仍存在。用存在性复核避免误判 404。
        return cls.model.select().where(cls.model.id == rid).exists()

    @classmethod
    @DB.connection_context()
    def max_completed_round_no(cls, task_id: str) -> int:
        """task_id 下**已完成**（done/annotated）的最大轮次号；无完成轮次返回 0。

        T9 fix 端点用它算 new_no = max + 1。reviewing/fixing/failed 一律不计入：
        否则一次失败或中断的轮次会把下一轮编号推高，还可能让新轮次建在没有产物的
        版本基线上。

        task_id 为空串/None 时短路返回 0：列无 NOT NULL 之外的值域约束，空串行可
        落库，不短路会让这类脏行被当成「某个任务的轮次」参与聚合。
        """
        if not task_id:
            return 0
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
            file_version=_clamp_str(cls.model, "file_version", file_version),
            anchor=anchor, matched_text=matched_text,
            type=_clamp_str(cls.model, "type", type),
            severity=_clamp_str(cls.model, "severity", severity),
            issue=issue, suggestion=suggestion,
            source=_clamp_str(cls.model, "source", source),
            status=_clamp_str(cls.model, "status", status),
            prev_annotation_id=prev_annotation_id,
            tenant_id=tenant_id, created_by=created_by,
        )
        return aid

    @classmethod
    @DB.connection_context()
    def update_status(cls, aid: str, status: str) -> bool:
        """改标注状态（T4 patcher 判 fixed/wontfix、T9 手动标 wontfix）；
        返回是否命中行（存在性复核口径同 RoundService.update_status）。update_time
        由框架刷新。status 按列宽钳制（见 _clamp_str）。"""
        status = _clamp_str(cls.model, "status", status)
        if cls.model.update(status=status).where(cls.model.id == aid).execute() > 0:
            return True
        # affected rows = 实际变化行数（连接未开 CLIENT_FOUND_ROWS）；同毫秒同值写
        # 整行无净变化会得 0，但行仍存在，用存在性复核避免误判「行不存在」。
        return cls.model.select().where(cls.model.id == aid).exists()

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

        task_id 为空串/None 时短路返回 []（同 max_completed_round_no）：空串行可
        落库，不短路会把这类脏行当成本任务的数据引用进下一轮 prompt。
        """
        if not task_id:
            return []
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

    @classmethod
    @DB.connection_context()
    def list_pending_by_task(cls, task_id: str) -> list:
        """该 task **全部轮次**中 status ∈ {open, new} 的标注，按创建时间升序。

        与 list_open_or_new_for_next_round 的区别是**不按轮次圈定**，因为修复轮本身
        不产标注：第 3 轮修复要处理的是第 1 轮 review 留下的 open 项，按 round_no
        圈定会取到空集，结果是一轮「什么都不修」的静默空转。

        task_id 为空串/None 时短路返回 []（同 max_completed_round_no）：空串行可落库，
        不短路会把这类脏行当成某个任务的待修复项。
        """
        if not task_id:
            return []
        return list(cls.model.select().where(
            (cls.model.task_id == task_id)
            & cls.model.status.in_(PENDING_ANNOTATION_STATUSES)
        ).order_by(cls.model.create_time.asc()))

    @classmethod
    @DB.connection_context()
    def delete_by_round(cls, round_id: str, source: str | None = "ai") -> int:
        """删除该轮次的 AI 标注，返回删除行数。

        用途是**保重试幂等**：进程被杀后轮次会滞留在 reviewing，重试会重跑整轮，
        若不清旧标注就会把每条问题再写一遍，面板上出现成对重复。范围严格限定在
        round_id（不按 task_id），避免把历史轮次的人工批注一起抹掉。

        source 默认只删 'ai'：同一轮次上可能存在用户手写批注（source='manual'），
        它们不是本方法要清的对象 —— 「重跑 AI 审核」不该抹掉用户的活。传 None 退化
        为只按 round_id 清空整轮，给确实需要清空整轮的场景留出口。
        """
        if not round_id:
            return 0
        expr = cls.model.round_id == round_id
        if source is not None:
            expr = expr & (cls.model.source == source)
        return cls.model.delete().where(expr).execute()
