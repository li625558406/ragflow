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
`compose_fix_query` / `severity_cn` / `severity_summary` 是纯文本助手，与 `fix_rounds_left`
/ `is_stale_running` 同属「不触库的判定逻辑」，落在本层是因为两个发起方（T8 工具 /
T9 API）都已 import 本模块 —— 各写一份就是等着两处措辞与口径漂移、行为分叉。

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
import threading
import time
from typing import NamedTuple

from api.db.db_models import DB, FileReviewAnnotation, FileReviewRound, FileReviewTemplate, json_dumps
from api.db.services.common_service import CommonService
from common.misc_utils import get_uuid

# 「待处理」标注口径：下一轮 LLM prompt 需要引用的未闭环问题。
PENDING_ANNOTATION_STATUSES = ("open", "new")

# 「最多三轮修复」的权威口径。DB 无该列，由本层判定。T7 节点参数面板里的 max_rounds
# 只是给用户看的 UI 字段（见 agent/component/file_review.py 的注释「后端不消费」），
# 后端唯一的轮数上限在这里。
MAX_FIX_ROUNDS = 3

# 轮次状态口径的**唯一实现**（T6 executor 写、T7 节点 / T8 工具 / T9 API 读）。
# 提到本层而不是各调用点各写一份：受理闸门 admit_fix_round 也要用它，两个发起方
# （T8 工具 / T9 API）都 import 本模块，放这里不新增任何依赖边。
#
# RUNNING_ROUND_STATUSES 只列「线程还会回来写这一行」的两态 —— 它们与 spawn 的
# _running_tasks 是同一件事的两个视角，是受理闸门必须拒的两个前置条件。
RUNNING_ROUND_STATUSES = ("reviewing", "fixing")

ROUND_STATUS_CN = {
    "reviewing": "审核中", "annotated": "审核完成", "fixing": "修复中",
    # 刻意用中性词：done 不总意味着「改好了」——executor 在「没有待修复的问题」
    # 与「该文件类型不支持自动修复」时同样以 done 收口，写「修复完成」会让用户
    # 误以为文档已被改动。
    "done": "已收口", "failed": "失败",
}


def fix_rounds_left(rounds: list) -> int:
    """还剩几轮修复机会 = MAX_FIX_ROUNDS - 已发生的修复轮数（下限 0）。

    修复轮 = 该 task 中 round_no > 1 的轮次（第 1 轮按契约恒为 review）。
    **失败轮也计入**：用户烧掉的是一次尝试机会，不是「什么都没发生」；不计入会让失败的
    retry 次数无上限，与「最大三轮重试」的口径相悖。
    round_no 为 0/None 的脏行不算修复轮（不短路会让一条脏行白吃一次机会）。
    """
    used = sum(1 for r in rounds if (r.round_no or 0) > 1)
    return max(0, MAX_FIX_ROUNDS - used)


# 触发 stale 判定的宽限期（秒）。create_round(status="fixing") 与 spawn_review_task
# 之间存在一个微秒级窗口：此刻轮次行已是 running 态、线程却尚未注册进 _running_tasks，
# 该窗口内把它判成「已中断」是误报。给足宽限即可 —— 进程真正被杀远慢于此。
STALE_GRACE_SECONDS = 60


def _now_ms() -> int:
    """当前时间（13 位毫秒整数），与 create_time 列同单位。"""
    return int(time.time() * 1000)


def is_stale_running(row, now_ms: int | None = None) -> bool:
    """轮次行自称「还在跑」，但**没有任何线程会回来写它** —— 判定为已中断。

    这是进程被 kill / 崩溃后唯一的自救出口。轮次状态由 T6 executor 在后台线程里推进，
    线程消失时状态**不会**回落：spawn._force_fail_round 只在「线程启动失败」与「线程内部
    抛异常」两条分支里 CAS 置 failed，进程整体消失时它没有执行机会。于是该行永久停在
    reviewing/fixing，而 spawn._running_tasks 是**进程内**集合、重启即空 —— 两个视角从此
    永久背离。用户侧的后果是三重：进度卡一直转圈、该 task 之后所有 fix 被受理闸门挡死、
    而卡片在 running 态又隐藏修复入口（无自助出口）。

    判据（三者同时成立才算 stale）：
      1. status ∈ RUNNING_ROUND_STATUSES —— 终态行不参与；
      2. spawn_mod.is_running(task_id) 为 False —— 线程真在跑就绝不判 stale。这一条也
         保证了工具侧「先查轮次状态、后查线程」的既有顺序不被本判定打乱；
      3. 行龄 > STALE_GRACE_SECONDS —— 避开判据 2 覆盖不到的建轮→起线程窗口。

    **直接访问 row.create_time，不用 getattr 兜底**：三个模型都继承 DataBaseModel，
    create_time 必然存在。缺列属于「模型契约坏了」，应当响亮报错，而不是 getattr(None) →
    算术报错前的静默误判 —— 把一个正常在跑的轮次判死会让用户白等不到结果。
    注意爆炸半径是**整张卡片一起挂**而非「该轮显红」：本函数在 _round_payload 里被逐轮调用，
    而 _round_payload 在 review_state 的同一个 try 内，一行脏数据会让 annotations / doc /
    fix_rounds_left 一并丢失、前端退化成「加载失败」（无数据时轮询亦停，与改造前一致）。
    这是有意接受的：能触发它的是模型契约损坏，此时给半张卡比给 500 更容易误导。

    **部署契约：本判定要求 HTTP 服务与 review 线程在同一个进程内。** 判据②读的是
    spawn._running_tasks（进程内集合），跨进程即恒空 —— 若将来把 ragflow_server 改成
    多 worker（hypercorn workers>1）或多进程部署，**所有活轮次都会在 60s 后被误判「已中断」**
    （前端停轮询、修复入口消失）。改多 worker 之前必须先改本判定（例如换成跨进程可见的
    心跳键），不能只改部署方式。今天成立：app.run 单进程、thread_pool_exec 是进程内池、
    WS 只作用于 task_executor 子进程数。

    row 可以是 ORM 行，也可以是测试里的 SimpleNamespace（需 status / task_id / create_time）。
    """
    if row.status not in RUNNING_ROUND_STATUSES:
        return False
    # 函数内延迟 import：本模块顶层不新增对 spawn 的依赖边（与 admit_fix_round 同款取向）。
    from rag.svr.file_review import spawn as spawn_mod

    if spawn_mod.is_running(row.task_id):
        return False
    return ((_now_ms() if now_ms is None else now_ms) - row.create_time) > STALE_GRACE_SECONDS * 1000


# R-1 自愈的落库文案——唯一实现，state 端点与受理闸门共用。
STALE_HEAL_ERROR = "服务重启或异常退出，本轮审核已中断"


def heal_stale_round(row, now_ms: int | None = None) -> bool:
    """「自称在跑却无人会回来写」的中断轮次就地回落为 failed（R-1 自愈落库单点）。

    判据完全复用 is_stale_running（三判据 + 60s 宽限；**部署契约同它**：要求 HTTP
    服务与 review 线程同进程，改多 worker 前必须先改判定）。命中即把轮次行落为
    failed + error 文案，并**同步刷新传入 row 的内存 status/error**——调用方随后
    构建 payload / 走后续闸门时读到的就是回落后的值，无需重查库。

    幂等由谓词天然保证：heal 后 status='failed' ∉ RUNNING_ROUND_STATUSES，谓词不再
    成立，第二次调用直接返回 False。并发双 heal 最终行值相同，无害。

    「读路径触发写」是设计核心而非副作用：中断轮次的唯一消费场景就是被读取
    （state 端点）与被受理（admit_fix_round），两个消费点接入即覆盖全部出口，
    无需启动期扫描或后台线程。写的是确定性事实（线程已不存在、行永远等不到结果），
    与用户输入无关，state 端点「读不限」策略不受影响。

    返回是否真的发生了回落（非中断行 / 幂等重入返回 False）。
    """
    if not is_stale_running(row, now_ms=now_ms):
        return False
    FileReviewRoundService.update_status(row.id, "failed", error=STALE_HEAL_ERROR)
    row.status = "failed"
    row.error = STALE_HEAL_ERROR
    return True


# ── 修复轮「只修 X 级」指令的唯一实现 ────────────────────────────────
# 级别过滤在 executor 里**只能软表达**：_run_fix_round 的 chosen = pending[:MAX_FIX_ITEMS]
# 没有 severity 谓词，LLM 收到的级别约束全部来自 round_row.user_query 被 _build_fix_prompt
# 原样写进「用户需求：」。所以这句中文措辞就是过滤机制本身，两个发起方（T8 对话工具 /
# T9 REST 端点）必须是同一份实现 —— 各写一份就是等着两处措辞漂移、行为分叉。
# 放在本层（而非某一层调用方）是因为两处都要 import 本模块，不新增任何依赖边。
SEVERITY_CN = {"high": "严重", "medium": "一般", "low": "提示"}

# 严重度排序口径（高→低）。未登记的值排最后（不丢弃）—— 排障时「有一条谁都不认识的
# 级别」比「它被悄悄塞进 high 里」显眼得多。
SEVERITY_RANK = {"high": 0, "medium": 1, "low": 2}


def severity_cn(sev) -> str:
    """severity 英文 → 中文（展示口径的唯一实现）。

    只把**空值**（None / 空串）折成「未知」；不认识的**字符串**原样透出 —— 它表达的不是
    「没有级别」而是「有一条谁也不认识的级别」，抹成「未知」就把排障线索丢了
    （SEVERITY_RANK 也恰好把未知串排在最后）。
    唯一硬约束：不许把字面 None 渲染进给用户 / LLM 的文案。
    """
    return SEVERITY_CN.get(sev) or (sev if isinstance(sev, str) and sev else "未知")


def severity_summary(items) -> str:
    """标注集合的严重度分布，形如「共 7 条（严重 3 条、一般 4 条）」。

    两个发起方（T8 工具 / T9 API）的拒绝与进度文案必须用同一份措辞：这是用户看到的
    **最后一句**话——「所选级别没有待修复的问题」若不带全貌，用户无从知道该改选哪个级别，
    只能靠猜。空集返回「无」（不留空让 LLM 或用户去猜）。
    排序按 SEVERITY_RANK；**多个未登记级别之间无稳定序**（同为 99，靠 sorted 稳定性取
    首次出现顺序），不承诺可复现 —— 它们本就是异常数据，只保证不被吞掉、排在最后。
    """
    if not items:
        return "无"
    counts = {}
    for a in items:
        counts[a.severity] = counts.get(a.severity, 0) + 1
    parts = [f"{severity_cn(s)} {counts[s]} 条"
             for s in sorted(counts, key=lambda x: SEVERITY_RANK.get(x, 99))]
    return f"共 {len(items)} 条（{'、'.join(parts)}）"


def compose_fix_query(base_query: str, levels: list) -> str:
    """把级别选择编进本轮 user_query。

    `base_query` 必须是**首轮原始需求**，不是上一轮：连轮修复时上一轮本身就是修复轮，
    其 user_query 里带着**上一轮**已作废的级别指令，拿它当基准会拼出「只修【严重】…」
    +「只修【一般】…」两条互相排斥的指令，LLM 同时收到后可能该修的不修、或越界改了
    用户本次没选中的级别——与本函数「按用户本次选定级别修复」的目标正好相反。
    （T8 实测踩过：test_fix_second_round_does_not_stack_level_directives 锁定该口径。）

    级别中英双写（如「严重/high」）：待修清单里每条写的是英文 `级别=high`（severity 已
    被 _norm_severity 归一成英文），只给中文会多出一层「严重 ⇔ high」的映射不确定性。

    刻意**不**把未选中级别的标注置 wontfix 来硬过滤：wontfix 的语义是「用户决定永不
    修复此条」，自动置位后用户改口「把中等的也修了」会静默失效（list_pending_by_task
    不再看到它），而用户没有任何办法从对话里发现这件事。

    levels 为空时只返回 base_query、不加任何指令：「修全部级别」这种假指令会让
    executor 修掉用户本次没要的范围。调用方应先用自己的白名单把空集挡在外面。
    """
    base = (base_query or "").strip()
    if not levels:
        return base
    chosen = "、".join(f"{SEVERITY_CN.get(s, s)}/{s}" for s in levels)
    head = f"本次只修复【{chosen}】级别的问题，其余级别的问题请保持原样、不要改动。"
    return f"{base}\n{head}" if base else head


def _fix_base_query(rounds: list, extra="") -> str:
    """修复轮的 user_query 基准 = **首轮**原始需求（可选追加本次补充说明）。

    基准只能取首轮：修复轮的 user_query 里已经带着上一轮写进去的级别指令，拿它当基准会把
    历轮指令叠起来（见 compose_fix_query 的 docstring，T8 实测踩过）。

    extra 只接受 str：REST 端点的 body["user_query"] 是任意 JSON（dict/list/数字都可能），
    直接 .strip() 会 AttributeError → 500。非字符串一律按「没提补充要求」忽略，而不是
    str() 成 repr —— 把 `{'a': 1}` 的原样文本拼进给 LLM 的 user_query 只是换了一种脏数据，
    用户同样无从修正。
    """
    base = (rounds[0].user_query or "").strip() if rounds else ""
    extra = extra.strip() if isinstance(extra, str) else ""
    if not extra:
        return base
    return f"{base}\n本次补充要求：{extra}" if base else f"本次补充要求：{extra}"


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
    def get_owned_task(cls, task_id: str, tenant_id: str) -> list:
        """越权闸门：返回该 task 的轮次列表，**仅当轮次全部归属 tenant_id**；否则 []。

        [] 同时覆盖四种「不许继续」的情形，调用方一律按「空 = 拒绝」处理、**不区分**：
        task_id 为空 / 无轮次行 / 轮次归他人 / 轮次 tenant 为空（历史脏行）。区分会把
        「他人的 task 是否存在」这一信息泄露给攻击者。

        为什么必须有这一层：`execute_task(task_id)` / `spawn_review_task(task_id)` /
        `_force_fail_round(task_id)` 全链路只按 task_id 圈定、**不含任何 tenant 谓词**
        （round_row.tenant_id 仅用于选 bucket）。入口不做归属校验 = 任何人拿到 task_id
        就能触发、读取、收口他人的审核（T6 → T9 交接契约第 5 条，T8/T9 共用本闸门）。

        刻意**不做** task_id 的格式白名单（如「必须 32 位十六进制」）：格式不匹配时本
        就查不到行、结果同为 []，白名单不增加任何安全边界；反过来，一旦 id 生成方式
        （现在是 uuid1().hex）变动而白名单没同步，闸门会静默拒绝**所有**合法请求 ——
        用一个更隐蔽的故障换一个不存在的收益。
        """
        if not task_id or not tenant_id:
            return []
        rounds = cls.get_by_task(task_id)
        if not rounds:
            return []
        if any((r.tenant_id or "") != tenant_id for r in rounds):
            return []
        return rounds

    @classmethod
    @DB.connection_context()
    def next_round(cls, task_id: str) -> tuple[int, str]:
        """下一轮的 (round_no, file_version)：max(**全部**轮次 round_no) + 1。

        刻意**不**用「只数 done/annotated」的口径 —— 那个口径会让
        **失败轮的编号被复用**，后果有三（T6 审查已实测确认）：
          ① 产物对象名是 frv-{task_id}-{file_version}，同号即同名：新轮会覆盖失败轮
             **已经落盘**的成稿（交接契约第 2 条：failed 轮次可能带 minio_path）；
          ② _latest_version_name 按 round_no 取基线、同号后者胜，新轮会把同号的失败
             兄弟（或它自己）选成输入基线 —— 自引用，补丁 find 全数落空；
          ③ 面板上两条同号轮次，用户分不清哪条是哪次。
        rounds 为空时返回 (1, "v1")，与 T7 节点首轮口径一致（首轮即原件）。
        """
        rounds = cls.get_by_task(task_id)
        no = max((r.round_no or 0) for r in rounds) + 1 if rounds else 1
        return no, f"v{no}"

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

    @classmethod
    @DB.connection_context()
    def get_by_file(cls, file_id: str) -> list:
        """该文件**最近一次审核任务**的全部轮次，按轮次号升序；从未审核返回 []。

        面板/进度卡只拿得到 file_id（对话与流程都以「上传的文件」为中心），task_id 是
        审核过程内部的编号 —— 让前端去「发现」它就得再开一条链路（节点输出 / 工具返回
        文本都不可靠：用户刷新一次就没了）。故按 file_id 反查 task_id：取该文件**最新
        的一行**轮次（跨任务按 create_time 取新），再用它的 task_id 取全轮次。

        「最新一行」的 tie-break 是 create_time（13 位毫秒）。同毫秒内插入的两行无法
        区分先后，调用方不应依赖绝对稳定的结果 —— 与 get_by_task 的次级排序同款取舍。

        刻意**不按 tenant_id 过滤**：读路径遵循本项目「文件所有人可见」的口径
        （docs/superpowers/specs/2026-09-09-remove-team-permission-design.md），且流程场景
        下轮次行的 tenant_id 是发起人/画布所有者的，按调用者过滤会让协作者看不到审核结果。
        写路径（T9 fix 端点）仍必须走 get_owned_task 严格校验（交接契约第 5 条）——
        读不限、写严格，是有意的不对称。

        脏数据兜底：最新一行没有 task_id（不该发生，列 NOT NULL）时返回 []，而不是拿
        空 task_id 去 get_by_task（那会把 task_id="" 的脏行当成「一个任务的全部轮次」）。
        """
        if not file_id:
            return []
        last = cls.model.select().where(
            cls.model.file_id == file_id
        ).order_by(cls.model.create_time.desc()).first()
        if last is None or not last.task_id:
            return []
        return cls.get_by_task(last.task_id)


class FileReviewAnnotationService(FileReviewServiceBase):
    model = FileReviewAnnotation

    @classmethod
    @DB.connection_context()
    def create(cls, *, round_id: str, task_id: str, file_id: str, file_version: str,
               anchor: str, matched_text: str, ann_type: str, severity: str, issue: str,
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
            type=_clamp_str(cls.model, "type", ann_type),
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
        list_open_or_new_for_next_round（同本模块其余聚合方法的短路口径）的强隔离是
        相反语义，改前请确认调用方（T9 annotations 端点 / T4 patcher）确实需要哪一种。
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

        task_id 为空串/None 时短路返回 []（同本模块其余聚合方法的短路口径）：空串行可
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

        task_id 为空串/None 时短路返回 []（同本模块其余聚合方法的短路口径）：空串行可落库，
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
    def list_by_file(cls, file_id: str) -> list:
        """该文件的**全部**标注（跨轮次、跨版本、跨任务），按创建时间升序。

        面板必须看得到全部，**不能**按 file_version 过滤：只有审查轮产标注，而审查轮的
        file_version 恒为首轮版本（v1），修复轮只改文档、不产新标注，成稿是 v2/v3/v4 ——
        按「成稿版本」过滤会一条都查不到（T6 executor 实测：_persist_annotations 只在
        _run_review_round 里被调用，用的就是 `round_row.file_version`）。
        同一文件被重复审核（新 task）时也必须合并展示，否则用户看不到上一轮的批注。
        """
        if not file_id:
            return []
        return list(cls.model.select().where(
            cls.model.file_id == file_id
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


# ── fix 受理闸门：两个发起入口共用的进程级互斥 ──────────────────────────
# 为什么必须有：受理序列「校验 → 定轮号 → 建轮次 → 起线程」本身不是原子的。
#   * REST 端点（T9 file_review_api.fix_review）跑在 quart 单进程单事件循环里，「临界区
#     内不许 await」就能互斥 —— 但它只挡得住并发 HTTP 请求；
#   * 对话工具（T8 FileReviewTool._fix）被 common.connection_utils.timeout 丢进**独立
#     daemon 线程**执行，与事件循环是两个线程，「无 await」这个机制对它完全无效。
# 两个入口并发打同一 task_id 时的必死路径（不可逆，T6/T8 实测）：双方都观测到
# spawn.is_running()==False → 各自 next_round() 拿到**同一个轮号**（(task_id, round_no)
# 无唯一约束）→ 建出两条同号轮次 → 后到者的 spawn_review_task 命中 spawn.py:53 的
# _running_tasks 后**静默 return** → 那条轮次永远等不到线程消费，永久卡在 fixing →
# 此后该 task 的所有 fix/review 被前置闸门挡死，只能手工改库才能恢复。
# threading.Lock 是进程级状态，跨线程天然共享，是本问题唯一有效的同步原语。
#
# 刻意**不做** per-task 锁字典：会引入无界 dict 增长（task_id 由 uuid 生成、用完不回收），
# 而受理是低频、短临界区（几次同步 DB 往返，无 IO 等待），全局单锁的竞争概率可忽略。
_ADMIT_LOCK = threading.Lock()

# 获取受理锁的最长等待（秒）。持锁者只做几次同步 DB 往返，真发生竞争应在毫秒级结束；
# 5s 是「对端卡在异常分支」时给用户的等待上限。超时按 busy 拒绝而不是排队：排在后面的
# 请求拿到锁时状态可能已变，早拒绝、让用户重试比让它排队后拿到一个过期结论更省事。
ADMIT_LOCK_TIMEOUT = 5.0


class FixAdmissionDenied(Exception):
    """受理闸门拒绝。reason 机器可读（调用方按它映射错误码），message 面向用户（中文）。

    reason 取值集合是固定契约，调用方按它分发：
      busy / not_found / invalid_levels / running / closing / no_quota / no_pending
    """

    def __init__(self, reason: str, message: str):
        super().__init__(message)
        self.reason = reason
        self.message = message


class AdmitResult(NamedTuple):
    """受理成功后的轮次快照。fix_rounds_left 是**本轮建完之后**的余额（已扣掉 1）。"""
    round_id: str
    round_no: int
    fix_rounds_left: int


def admit_fix_round(*, task_id: str, tenant_id: str, levels, user_query_override="") -> AdmitResult:
    """受理一轮修复：校验 → 定轮号 → 建轮次 → spawn，**全程持进程级锁**。

    同步函数；REST 侧必须经 `asyncio.to_thread` 调用（R-6：持锁段的锁等待与 DB
    往返不得阻塞事件循环），对话工具线程直接同步调用。锁本身是跨线程的
    threading.Lock，工作线程与事件循环线程被同一把锁串行化。
    返回 AdmitResult 或抛 FixAdmissionDenied —— 不返回「拒绝码」，避免调用方漏判。

    前置条件：levels 必须是 list（R-7 判型闸在本函数第一行，取锁/触库之前）。

    闸门顺序不是随意的，每一条都在挡一条实测过的坏路径：
      1. not_found：task_id 不存在 / 不归 tenant（沿用 get_owned_task 的口径，同一句话，
         不泄露「他人 task 是否存在」）；
      2. stale→heal：本轮自称在跑但已无线程会回来写它 —— 受理点上就地 heal 为 failed
         （R-1 自愈），随后与崩溃 failed 轮**同权走后续闸门**：有余额即可直接发起下一轮
         修复，不再要求「重新发起审核」。heal 输给并发消费者（返回 False 但谓词刚成立）
         时行在库里已是 failed，同样按 failed 继续评估；
      3. running  ：本轮（rounds[-1]）还在 reviewing/fixing，线程还会回来写这一行；
      4. closing  ：轮次行已终态但 spawn 线程仍在收尾 —— executor._run_fix_round **先**
         置轮次终态、**再**逐条写最多 MAX_FIX_ITEMS(20) 条标注，中间隔着 20 次 DB
         往返；此刻 spawn_review_task 会静默 no-op，新轮次永远等不到线程去消费它；
      5. no_quota ：修复轮余额用尽（failed 轮同样计入，见 fix_rounds_left）；
      6. no_pending：所选级别没有待修项 —— 建出来的轮次会去修**别的**级别（把用户没
         选中的问题改掉），或空转一轮白烧一次机会。
    顺序约束：2 必须在 3 之前（原因同前版）；3 必须在 4 之前；4 在 6 之前是取舍；
      从 is_running 检查到 spawn_review_task 之间不得插入其它逻辑。

    levels 的取值白名单仍由调用方归一（T8 工具宽松、T9 API 严格拒绝整批非法值，
    这是既有差异）；本层只判「是不是 list」并做 pending 的 severity 过滤，不校验级别取值。
    """
    if not isinstance(levels, list):
        raise FixAdmissionDenied("invalid_levels", "修复级别参数不合法")
    if not _ADMIT_LOCK.acquire(timeout=ADMIT_LOCK_TIMEOUT):
        # 超时路径在这里直接抛，**不能**落进下面的 try/finally 去 release —— 那会把别人
        # 正持有的锁放掉（一次「拿不到却释放」就能让后续所有互斥失效）。
        raise FixAdmissionDenied("busy", "上一轮操作正在处理中，请稍后重试")
    try:
        # 函数内延迟 import：本模块顶层不新增对 spawn 的依赖边（spawn.py 自身也是延迟
        # import service 的，同一取向，避免 import 顺序耦合成为新坑）。
        from rag.svr.file_review import spawn as spawn_mod

        rounds = FileReviewRoundService.get_owned_task(task_id, tenant_id)
        if not rounds:
            # 「不存在」与「不是你的」共用同一句文案（get_owned_task 的 docstring 同款口径）。
            raise FixAdmissionDenied("not_found", "文件审核任务不存在或无权访问")

        cur = rounds[-1]
        # stale 必须在 running 之前判：两者的 status 同样在 RUNNING_ROUND_STATUSES 里，
        # 顺序反了会把「永远不会好」错答成「等一会就好」。反过来不会误伤活轮次 ——
        # is_stale_running 的判据②要求 is_running 为 False。
        # R-1 自愈：中断轮就地回落（幂等）后放行走后续闸门；heal 内部已同步刷新
        # cur.status，这里再赋一次是防御并发下 heal 输给别的消费者的残影。
        if is_stale_running(cur):
            heal_stale_round(cur)
            cur.status = "failed"

        if cur.status in RUNNING_ROUND_STATUSES:
            raise FixAdmissionDenied(
                "running",
                f"第 {cur.round_no} 轮（{ROUND_STATUS_CN.get(cur.status, cur.status)}）"
                "仍在进行中，请等它结束后再发起修复")

        if spawn_mod.is_running(task_id):
            raise FixAdmissionDenied("closing", "上一轮审核正在收尾，请稍等片刻后重试")

        left = fix_rounds_left(rounds)
        if left <= 0:
            raise FixAdmissionDenied(
                "no_quota",
                f"已达到最大修复轮次（{MAX_FIX_ROUNDS} 轮），未修复的问题请按批注手动处理")

        pending = FileReviewAnnotationService.list_pending_by_task(task_id)
        if not [a for a in pending if a.severity in levels]:
            # 带全貌而不是只说「没有」：用户无从知道该改选哪个级别，只能靠猜或反复试。
            # 措辞恒定为「没有【X】级别的待修复问题。待修复问题：…」，两个发起方共用。
            chosen = "、".join(SEVERITY_CN.get(lv, lv) for lv in levels)
            raise FixAdmissionDenied(
                "no_pending",
                f"没有【{chosen}】级别的待修复问题。待修复问题：{severity_summary(pending)}")

        no, version = FileReviewRoundService.next_round(task_id)
        rid = FileReviewRoundService.create_round(
            task_id=task_id,
            file_id=cur.file_id,
            round_no=no,
            template_id=cur.template_id,
            # 基准取首轮原始需求，不用上一轮（见 _fix_base_query 的说明）。
            user_query=compose_fix_query(_fix_base_query(rounds, user_query_override), levels),
            file_version=version,
            status="fixing",
            # tenant 必须来自当前用户，不能留空：get_owned_task 要求「任一轮 tenant 不符
            # 即拒绝」，写空串的轮次连自己都过不了闸门；且 executor 用 `{tenant}-downloads`
            # 选桶，空串会让成稿落进 `-downloads` 桶、谁都取不到。
            tenant_id=tenant_id,
            created_by=tenant_id,
            # 继承本轮 KB 配置：修复轮不做检索，但 kb_ids 是「这一轮用了哪些知识库」的
            # 可追溯配置，留空会让它无从知晓。
            kb_ids=cur.kb_ids,
        )
        spawn_mod.spawn_review_task(task_id)
        return AdmitResult(round_id=rid, round_no=no, fix_rounds_left=left - 1)
    finally:
        _ADMIT_LOCK.release()
