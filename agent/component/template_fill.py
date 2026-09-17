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
"""「范本填写」画布节点：配置时只选知识库（不选范本），运行时节点内部
① 拉取本租户已发布范本清单 → ② LLM 按用户需求（query，默认 {sys.query}）选出
一个或多个适配范本（每个各产一份成稿）→ ③ 按其填写点逐条 KB 检索，用户上传
文件（sys.file_content）与 Begin 表单字段一并注入证据/参数 → ④ LLM 批量产值
（缺值留空待人工二次加工，语义与模板填写引擎 2026-09-08 改造后一致）→
⑤ docxtpl/openpyxl 渲染 → ⑥ 产物入 {tenant_id}-downloads bucket 并输出
download 列表 JSON（下游 Message 节点 _extract_downloads 识别后渲染下载/预览，
契约同 DocGenerator）。

检索/产值/渲染经 tpl_fill_task 后台执行器执行（与 B端填写任务同 pipeline），
节点只做任务创建与进度观察。类名刻意取 TemplateFill
而非 FillTemplate：Agent 节点的工具（agent/tools/template_fill.py 的 C 端
FillTemplate 工具）同样经 component_class 解析且 agent.component 优先，
组件类若与工具类同名会遮蔽工具、破坏存量画布，两包不得重名。
"""
import asyncio
import json
import logging
import re
import time
from functools import partial

from agent.component.base import ComponentBase, ComponentParamBase
from api.db.services.template_fill_service import (
    TplFillTaskService,
    TplTemplateService,
    TplTemplateVersionService,
    _sanitize_filename,
)
from common import settings
from common.misc_utils import get_uuid
from rag.svr.template_fill import executor
from rag.svr.template_fill.spawn import spawn_fill_task
from rag.utils.redis_conn import REDIS_CONN

logger = logging.getLogger(__name__)

# 拉已发布范本的分页上限（get_list_page size 硬顶 100，一次拉全量足够选型）
_LIST_SIZE = 100

_MIME_BY_TYPE = {
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}

_SELECT_SYSTEM = (
    "你是范本选择器。根据用户需求，从候选范本中选出所有适合的范本（一个或多个；"
    "拿不准时只选最合适的一个，不要把不相关的范本也选进来）。\n"
    "只输出一个 JSON 对象：{\"template_ids\": [\"范本id\", ...]}，不要输出任何其他文字。")

# 用户上传文件内容作为填写证据的单槽截断上限（generate_values 每槽证据预算
# 6 片 × 800 字，预置片段占前 2000 字，避免挤出 KB 检索证据）
_USER_FILE_EVIDENCE_MAX = 2000
# Begin 表单字段进产值 LLM 背景信息的单字段截断上限
_BEGIN_FIELD_PROMPT_MAX = 200

# P2 暂停确认：预判后挂起等待用户在对话侧确认（confirm_pending SSE → 前端确认卡片
# → POST /template/fill/confirm 写 Redis 键 → 本节点轮询读取）。
_CONFIRM_TIMEOUT = 600          # 确认等待超时（秒），超时按预判结果自动继续
_CONFIRM_POLL_INTERVAL = 1.5    # Redis 轮询间隔（与 cancel 探针同量级）
# 等待期 SSE 心跳间隔：挂起等待最长 600s 全程无数据流，Nginx 等反代空闲超时
# （默认 60s）会掐断 SSE → 前端事件流冻结、增量落库停摆，刷新后回放缺事件。
# 每 30s 推一条 heartbeat 保活（前端 reducer 对无 template_id 事件直接忽略）
_CONFIRM_HEARTBEAT_INTERVAL = 30.0

# 观察者轮询总 deadline：防僵尸任务无限轮询。docker restart 部署等场景下执行中
# 任务行可能永久停中间态（后台线程已死、无人强置终态），若无总上限节点会每
# 1.5s 轮询到天荒地老。超时后写取消键 + 按 failed 收口，走正常汇总输出。
_OBSERVE_TIMEOUT_S = 3600

# 运行快照键（快照化恢复，设计 2026-09-16）：画布运行全程权威状态的唯一落点，
# 刷新恢复端点 /template/fill/fill-run/<canvas_task_id>/snapshot 按它组装全量状态。
# 键名含 canvas_task_id（与 fill task_id 是两个 id 空间，tasks 映射维护对应关系）。
_RUN_SNAPSHOT_KEY = "tpl_fill:run:{task_id}"
_RUN_SNAPSHOT_TTL = 7200      # 过程态：覆盖最长观察窗口（_OBSERVE_TIMEOUT_S）+ 余量
_RUN_SNAPSHOT_DONE_TTL = 600  # 终态：收尾 10 分钟内刷新仍可权威恢复，之后自然过期


class _FillCancelled(Exception):
    """画布取消中断填写：不落 failed 事件，由 invoke 统一推 cancelled。"""


def _canvas_task_params(begin_fields: dict, query: str, decision: dict | None,
                        llm_item_keys: set, placeholders: list[dict],
                        user_file_text: str,
                        *, baseline_values: dict | None = None) -> dict:
    """组装委托给 executor.execute_task 的任务 params：
    背景（Begin 字段+需求描述，与节点内 background 同构）+ 下划线保留键
    （直填值/LLM 白名单键/检索跳过键/用户文件证据/基线），executor 侧 split_canvas_params 拆解。
    llm_item_keys 为确认后仍要走检索+LLM 的字段 key 集合（白名单），同时写入
    _changed_keys 供 executor 收窄；其余 llm 槽（默认值兜底/留空与直填）跳过检索，
    由 executor 的 missing 显式纳入 → _merge_default_values 兜底。

    baseline_values：增量填写场景下由调用方传入的「上次已落成稿的真实填写值」，
    仅对 missing 字段兜底（优先级 default_value 之前），让文档保留上次内容。
    非增量路径传 None 或空 dict，executor 端兜底不触发。"""
    decision = decision or {}
    direct = decision.get("values") or {}
    skip = [it["key"] for it in placeholders
            if it.get("key") and executor._norm_fill_mode(it) == "llm"
            and it["key"] not in llm_item_keys]
    params: dict = dict(begin_fields)
    if query and query.strip():
        params["用户需求描述"] = query.strip()[:_BEGIN_FIELD_PROMPT_MAX]
    params["_direct_values"] = {str(k): str(v) for k, v in direct.items()}
    params["_changed_keys"] = sorted(str(k) for k in llm_item_keys)
    params["_retrieve_skip_keys"] = skip
    params["_user_file_text"] = user_file_text or ""
    params["_baseline_values"] = dict(baseline_values or {})
    return params


def _render_of(row) -> dict | None:
    """终态行的产值映射（values.render）归一，返回值语义：
      None —— 行整体没有 values / values 不是 dict（结构异常，调用方返回 None，
              不下发 unfilled/filled 字段）；
      {}   —— values 是 dict 但 render 缺失/为 None/为畸形真值（按「全部留空」
              处理）。对 falsy render 与旧写法 `values.get("render") or {}` 等价，
              **但对 truthy 非 dict（render 存成 "abc"/123）不等价**——旧写法会在
              下游 .get 上抛 AttributeError，这里把它降级成「全部留空」。
    为什么必须在这里挡：derive_* 系列对非 dict 真值会在 .get 上抛（既有弱点，
    见 derive_unfilled 注释），而这里跑在观察者轮询循环内——抛出去会连坐整个
    多范本填写轮次，代价远大于少渲染一条汇总。写侧本来只落 dict，但历史行不可信，
    故不能只靠写侧约束。"""
    values = getattr(row, "values", None)
    if not isinstance(values, dict):
        return None
    render = values.get("render")
    if not isinstance(render, dict):
        return {}
    return render


def _unfilled_of(placeholders: list[dict], row) -> list[dict] | None:
    """终态行的成稿留空填写点（executor.derive_unfilled 包装）：值源 DB 行
    values.render（终态权威，不依赖 Redis 快照存活）。无留空或值结构异常
    返回 None——filled 事件不下发该字段，前端不渲染汇总条。"""
    render = _render_of(row)
    if render is None:
        return None
    return executor.derive_unfilled(placeholders, render) or None


def _filled_of(placeholders: list[dict], row) -> list[dict] | None:
    """终态行的成稿已填填写点（executor.derive_filled 包装），与 _unfilled_of 逐字镜像：
    同一值源（DB 行 values.render）、同一异常口径。已填清单与留空清单按判空口径
    穷尽且互斥，前端合并两者即得全量 key→中文名映射——用户填完一轮后才看得到
    字段叫什么、点得动、能在对话里准确引用（否则只能回落英文 key）。"""
    render = _render_of(row)
    if render is None:
        return None
    return executor.derive_filled(placeholders, render) or None


def build_candidates(rows: list[dict], latest_of) -> list[dict]:
    """已发布范本行 → 选型候选（纯逻辑，latest_of 注入便于单测）。
    最新版本缺失或未配置填写点的范本跳过（选了也填不了）。"""
    candidates = []
    for row in rows or []:
        ver = latest_of(row.get("id"))
        placeholders = (getattr(ver, "placeholders", None) or []) if ver else []
        if not placeholders:
            continue
        candidates.append({
            "template_id": row.get("id"),
            "name": row.get("name") or "",
            "description": row.get("description") or "",
            "file_type": row.get("file_type") or "docx",
            "slot_names": [it.get("name") or it.get("key") or "" for it in placeholders if it.get("key")],
            "_ver": ver,
            "_placeholders": placeholders,
        })
    return candidates


def parse_selection(text: str, candidate_ids: list[str]) -> list[str]:
    """解析选型 LLM 输出并校验：非法 JSON / 空 / 含不在候选内的 id → ValueError
    （调用方兜底报错，不静默换第一个——选错范本产出的成稿比失败更有害）。
    兼容 {"template_ids": [...]} 与旧版单选 {"template_id": "..."}；去重保序。"""
    raw = executor._extract_json(text or "")
    ids = raw.get("template_ids")
    if ids is None:
        ids = [raw["template_id"]] if raw.get("template_id") else []
    if not isinstance(ids, list):
        ids = [ids]
    picked: list[str] = []
    for tid in ids:
        if tid not in candidate_ids:
            raise ValueError("AI 未能从已发布范本中选出合适的范本，请补充需求描述或检查范本库")
        if tid not in picked:
            picked.append(tid)
    if not picked:
        raise ValueError("AI 未能从已发布范本中选出合适的范本，请补充需求描述或检查范本库")
    return picked


class TemplateFillParam(ComponentParamBase):

    def __init__(self):
        super().__init__()
        self.query = ""          # 需求描述，支持 {sys.query} 等变量引用，空则回退 {sys.query}
        self.dataset_ids = []    # 检索知识库（复用 KB 表单字段默认 name）
        self.top_k = executor.TOP_K_DEFAULT
        self.outputs = {
            "content": {"value": "", "type": "string"},
            "download": {"value": "", "type": "string"},
        }

    def check(self) -> bool:
        return True


class TemplateFill(ComponentBase):
    component_name = "TemplateFill"

    def __init__(self, canvas, id, param: ComponentParamBase):
        super().__init__(canvas, id, param)
        # 进度事件队列：canvas.run 心跳循环 drain 后经 decorate 变同名 SSE
        # （FanOut 同款机制；canvas.run 心跳循环 drain——泛化到任意组件由 canvas 侧改动提供）
        self._event_queue: asyncio.Queue = asyncio.Queue()

    def _push_progress(self, data: dict) -> None:
        self._event_queue.put_nowait({"event": "template_fill_progress", "data": data})

    def _write_run_snapshot(self, stage: str, *, templates: list[dict] | None = None,
                            tasks: dict | None = None, pending: dict | None = None,
                            ttl: int = _RUN_SNAPSHOT_TTL) -> None:
        """写运行快照（快照化恢复）：画布运行全程权威状态，刷新恢复端点按
        canvas_task_id 读取。Redis 故障只告警——快照是恢复增强，不是执行权威
        （执行权威在 DB 任务行）。无 canvas task_id（SSE 回传不可用）静默跳过。"""
        canvas_task_id = getattr(self._canvas, "task_id", "") or ""
        if not canvas_task_id:
            return
        payload = {"stage": stage, "templates": templates or [], "tasks": tasks or {},
                   "pending": pending, "updated_at": int(time.time() * 1000)}
        try:
            REDIS_CONN.set(_RUN_SNAPSHOT_KEY.format(task_id=canvas_task_id),
                           json.dumps(payload, ensure_ascii=False), exp=ttl)
        except Exception:
            logger.warning("write run snapshot failed, canvas=%s stage=%s",
                           canvas_task_id, stage, exc_info=True)

    def _resolve_query(self) -> str:
        """需求描述解析：按变量引用替换（UserFillUp 同款 partial 适配），空回退 sys.query。"""
        text = (self._param.query or "").strip() or "{sys.query}"
        for k, v in self.get_input_elements_from_text(text).items():
            val = v.get("value")
            if isinstance(val, partial):
                ans = "".join(str(chunk) for chunk in val())
            elif isinstance(val, list):
                ans = ",".join(str(item) for item in val)
            elif val is None or isinstance(val, str):
                ans = val or ""
            else:
                try:
                    ans = json.dumps(val, ensure_ascii=False)
                except Exception:  # noqa: BLE001 — 不可序列化值降级 str
                    ans = str(val)
            text = re.sub(r"\{" + re.escape(k) + r"\}", ans, text)
        return text.strip()

    def _load_candidates(self, tenant_id: str) -> list[dict]:
        rows, _total = TplTemplateService.get_list_page(
            tenant_id, status="published", page=1, size=_LIST_SIZE)
        return build_candidates(rows, TplTemplateVersionService.latest)

    async def _select_templates(self, tenant_id: str, candidates: list[dict], query: str) -> list[dict]:
        """唯一候选直接命中（省一次 LLM）；多个走 LLM 选出一个或多个。"""
        if len(candidates) == 1:
            return list(candidates)
        mdl = executor._build_chat_mdl(tenant_id)
        catalog = [{"template_id": c["template_id"], "name": c["name"],
                    "description": c["description"], "填写点": c["slot_names"]}
                   for c in candidates]
        user_msg = ("## 候选范本\n" + json.dumps(catalog, ensure_ascii=False) +
                    "\n\n## 用户需求\n" + (query or "（未提供）"))
        ans = await mdl.async_chat(_SELECT_SYSTEM, [{"role": "user", "content": user_msg}])
        chosen = parse_selection(ans, [c["template_id"] for c in candidates])
        by_id = {c["template_id"]: c for c in candidates}
        return [by_id[tid] for tid in chosen]

    def _begin_fields(self) -> dict:
        """收集 Begin 表单字段输出（标量字段，键名可与填写点 key 同名实现 param
        直取；同时作为产值 LLM 的背景信息）。无 Begin / 无输出返回空 dict。"""
        try:
            for cpn in (self._canvas.components or {}).values():
                obj = cpn.get("obj") if isinstance(cpn, dict) else None
                if obj is not None and getattr(obj, "component_name", "").lower() == "begin":
                    outs = obj.output() or {}
                    return {k: v for k, v in outs.items()
                            if isinstance(v, (str, int, float, bool)) and v != ""}
        except Exception:
            logger.warning("TemplateFill._begin_fields failed", exc_info=True)
        return {}

    def _user_file_evidence(self) -> str:
        """用户上传文件的解析文本（canvas.run 已解析为 sys.file_content），截断备用。"""
        try:
            text = self._canvas.globals.get("sys.file_content") or ""
        except Exception:  # noqa: BLE001
            return ""
        return text.strip()[:_USER_FILE_EVIDENCE_MAX]

    def _work_context_id(self) -> str:
        """本次运行的工作上下文 id（`sys.session_id`，即会话 id）。

        增量填写的基线**只能**在同一上下文内继承（见
        TplFillTaskService.latest_done_in_context）。流程页的影子会话即该流程实例，
        对话页即本次会话，由 canvas_service.completion 在每轮 run 前写入 globals。
        取不到（SDK/自建画布等旁路）→ 空串 → 不做增量、走全量。
        """
        try:
            return str(self._canvas.globals.get("sys.session_id") or "").strip()
        except Exception:  # noqa: BLE001
            return ""

    async def _confirm_changed_fields(self, chosen: list[dict], query: str,
                                      begin_fields: dict,
                                      *, incremental_overrides: dict | None = None
                                      ) -> dict:
        """P2 暂停确认（全量展示）：候选 = 各范本全部 llm 填写点（含无默认值字段），
        AI 预判只覆盖默认值子集；勾选 = 交给检索+LLM，不勾 = 有默认值用默认值、
        无默认值留空。返回 {template_id: {"changed": set, "values": dict}}；
        全部选中范本均无默认值字段时返回 {}（跳过确认，触发条件与现状一致）。
        超时/Redis 异常/预判失败 → 按预判∪无默认值字段自动继续（同现状全填）。

        incremental_overrides: tid -> {"candidates": [...], "predicted": [...],
                                       "fallback_changed": set, "fallback_values": dict}
            增量填写场景由调用方提供：候选只列本次要改的项（不是全量 244 项），
            兜底 changed = patch keys，兜底 values = LLM 抽出的 direct 值。"""
        d_map: dict[str, list[dict]] = {}        # 全部 llm 填写点（候选 + valid 校验）
        default_map: dict[str, list[dict]] = {}  # 默认值子集（触发 + 预判）
        for c in chosen:
            items = [it for it in c["_placeholders"]
                     if executor._norm_fill_mode(it) == "llm" and it.get("key")]
            if items:
                d_map[c["template_id"]] = items
            # 增量填写模式：该范本走 overrides（候选已固定为 patch items），跳过 default_map
            if incremental_overrides and c["template_id"] in incremental_overrides:
                continue
            defaults = [it for it in items if str(it.get("default_value") or "")]
            if defaults:
                default_map[c["template_id"]] = defaults
        # 增量填写模式：该范本已走 overrides，跳过 default_map 与 predict_changed_fields
        if not default_map and not incremental_overrides:
            return {}
        task_id = getattr(self._canvas, "task_id", "") or ""
        background = dict(begin_fields)
        if query:
            background["用户需求描述"] = query[:_BEGIN_FIELD_PROMPT_MAX]
        predicted: dict[str, set] = {}
        try:
            for tid, items in default_map.items():
                predicted[tid] = await executor.predict_changed_fields(
                    self._canvas.get_tenant_id(), items, background,
                    should_cancel=lambda: self.check_if_canceled("TemplateFill predict"))
        except executor.GenerateCancelled:
            # 预判期取消信号不外逸：与检索/产值阶段同路转 _FillCancelled，
            # 由 invoke 统一收口为 cancelled 终态（不落 failed）
            raise _FillCancelled() from None
        name_of = {c["template_id"]: c["name"] for c in chosen}
        # 运行级 nonce：task_id 即 agent_id（跨运行不变），确认键必须带本次运行的
        # 随机 nonce——孤儿键（重复点击/超时后才确认/取消残留/delete 失败）在新运行
        # nonce 不同时永不命中，由 700s TTL 自然过期，防旧 decisions 被立即消费跳过
        # 确认 + 旧直填值经 sediment 沉淀污染默认值基线
        nonce = get_uuid()
        # 字段名用 confirm_templates：selected 事件的 templates 已被前端归约占用
        confirm_templates: list[dict] = []
        for c in chosen:
            tid = c["template_id"]
            ov = (incremental_overrides or {}).get(tid)
            if ov:
                # 增量填写：候选只列 patch items（不是全量填写点），predicted 全部预勾选
                confirm_templates.append({
                    "template_id": tid,
                    "name": name_of.get(tid, ""),
                    "candidates": ov["candidates"],
                    "predicted": ov["predicted"],
                    "incremental": True,
                })
                continue
            confirm_templates.append({
                "template_id": tid,
                "name": name_of.get(tid, ""),
                "candidates": [{"key": it["key"],
                                "name": it.get("name") or it["key"],
                                "default_value": it.get("default_value")}
                               for it in d_map[tid]],
                "predicted": sorted(predicted.get(tid) or set())})
        self._push_progress({
            "stage": "confirm_pending", "task_id": task_id,
            "confirm_nonce": nonce,
            "confirm_templates": confirm_templates})
        # 运行快照：挂起进入（挂起态权威落点，刷新恢复据此判定卡是否活着）
        self._write_run_snapshot("confirm_pending", pending={
            "type": "confirm", "nonce": nonce, "confirm_templates": confirm_templates})
        # 兜底（未确认/无 task_id）：全量范本 changed = 预判 ∪ 全部无默认值字段；
        # 增量范本 changed = patch keys（不是全量填写点）——与确认卡初始勾选一致
        decisions: dict = {}
        for c in chosen:
            tid = c["template_id"]
            ov = (incremental_overrides or {}).get(tid)
            if ov:
                decisions[tid] = {
                    "changed": set(ov.get("fallback_changed") or list(ov.get("predicted") or [])),
                    "values": dict(ov.get("fallback_values") or {}),
                }
                continue
            items = d_map.get(tid, [])
            decisions[tid] = {
                "changed": (set(predicted.get(tid) or set())
                            | {it["key"] for it in items
                               if not str(it.get("default_value") or "")}),
                "values": {}}
        if not task_id:
            return decisions
        waited = 0.0
        hb = 0.0
        # 确认键带运行级 nonce（与 confirm_pending 事件下发给前端的一致）
        confirm_key = f"tpl_fill:confirm:{task_id}:{nonce}"
        while waited < _CONFIRM_TIMEOUT:
            if self.check_if_canceled("TemplateFill confirm wait"):
                raise _FillCancelled()
            try:
                raw = REDIS_CONN.get(confirm_key)
            except Exception:
                logger.warning("confirm poll failed; fallback to predicted", exc_info=True)
                break
            if raw:
                try:
                    REDIS_CONN.delete(confirm_key)
                    data = json.loads(raw)
                except Exception:
                    logger.warning("confirm payload unparsable; fallback to predicted")
                    break
                for tid, d in (data or {}).items():
                    if tid not in decisions or not isinstance(d, dict):
                        continue
                    # 增量范本 valid = overrides 候选 key 集合（不是全量填写点）；
                    # 全量范本 valid = d_map 的 key 集合
                    ov = (incremental_overrides or {}).get(tid)
                    if ov:
                        valid = {c["key"] for c in ov.get("candidates") or []}
                    else:
                        valid = {it["key"] for it in d_map.get(tid, [])}
                    # 载荷结构防御：非官方写键可能塞标量（values 非 dict / changed 非 list），
                    # isinstance 兜底按空处理，不炸 run
                    changed_raw = d.get("changed") if isinstance(d.get("changed"), list) else []
                    values_raw = d.get("values") if isinstance(d.get("values"), dict) else {}
                    if ov:
                        # 增量补丁：fallback_values 是 LLM 从用户原话抽出的 direct 值
                        # （如「approval_doc 填写成 港里」→ {approval_doc: 港里}）。
                        # 前端确认卡 inputs 默认空，用户只点「确认并继续填写」不打字
                        # 时 values_raw={}——这种情况下必须保留 fallback_values，否则
                        # 用户在对话里给的直填值被静默丢弃、补丁字段留空。
                        # 用户在输入框显式改了值（非空字符串）才覆盖；空字符串=未动=用 fallback。
                        fb_values = ov.get("fallback_values") or {}
                        final_values: dict = dict(fb_values)
                        for k, v in values_raw.items():
                            if k not in valid:
                                continue
                            if v is not None and str(v).strip():
                                final_values[k] = str(v)
                        decisions[tid] = {
                            "changed": {k for k in changed_raw if k in valid},
                            "values": final_values}
                    else:
                        decisions[tid] = {
                            "changed": {k for k in changed_raw if k in valid},
                            "values": {k: v for k, v in values_raw.items()
                                       if k in valid}}
                return decisions
            await asyncio.sleep(_CONFIRM_POLL_INTERVAL)
            waited += _CONFIRM_POLL_INTERVAL
            hb += _CONFIRM_POLL_INTERVAL
            if hb >= _CONFIRM_HEARTBEAT_INTERVAL:
                hb = 0.0
                self._push_progress({"stage": "heartbeat", "task_id": task_id})
        self._push_progress({"stage": "confirm_timeout"})
        return decisions

    async def _confirm_template_selection(self, task_id: str,
                                          chosen: list[dict]) -> list[dict]:
        """多范本命中暂停询问（智能折中）：先推 selected（调用方已推），此处推
        select_pending 确认卡 → 用户勾选（≥1 个）→ POST /template/fill/select-confirm
        写 Redis 键 → 本方法轮询读取。返回最终 chosen 子集（保 AI 选择顺序）。
        空/全非法提交、payload 异常、Redis 故障 → 按 AI 选择继续（与超时同兜底）；
        无 task_id（无法回传）由调用方短路跳过询问。"""
        nonce = get_uuid()
        select_candidates = [{"template_id": c["template_id"],
                              "name": c["name"],
                              "slot_count": len(c["_placeholders"]),
                              "description": c.get("description") or ""}
                             for c in chosen]
        self._push_progress({
            "stage": "select_pending", "task_id": task_id,
            "select_nonce": nonce,
            "select_candidates": select_candidates,
            "ai_selected": [c["template_id"] for c in chosen],
        })
        # 运行快照：选择挂起进入
        self._write_run_snapshot("select_pending", pending={
            "type": "select", "nonce": nonce, "select_candidates": select_candidates,
            "ai_selected": [c["template_id"] for c in chosen]})
        valid_ids = {c["template_id"] for c in chosen}
        by_id = {c["template_id"]: c for c in chosen}
        # 运行级 nonce（与字段确认同语义）：task_id 跨运行不变，孤儿键靠 nonce 错开
        # + 700s TTL 自然过期，防旧运行的选择被新运行立即消费
        select_key = f"tpl_fill:select:{task_id}:{nonce}"
        waited = 0.0
        hb = 0.0
        while waited < _CONFIRM_TIMEOUT:
            if self.check_if_canceled("TemplateFill select wait"):
                raise _FillCancelled()
            try:
                raw = REDIS_CONN.get(select_key)
            except Exception:
                logger.warning("select poll failed; fallback to AI selection",
                               exc_info=True)
                break
            if raw:
                try:
                    REDIS_CONN.delete(select_key)
                    data = json.loads(raw)
                except Exception:  # noqa: BLE001 — 与字段确认同款，坏载荷兜底不炸 run
                    logger.warning("select payload unparsable; fallback to AI selection")
                    break
                ids_raw = data.get("template_ids") if isinstance(data, dict) else None
                if not isinstance(ids_raw, list):
                    break
                # 未知 id 过滤 + 去重保序；空/全非法 → 兜底走 AI 选择（不炸 run）
                ids = list(dict.fromkeys(
                    t for t in ids_raw
                    if isinstance(t, str) and t in valid_ids))
                if not ids:
                    break
                return [by_id[t] for t in ids]
            await asyncio.sleep(_CONFIRM_POLL_INTERVAL)
            waited += _CONFIRM_POLL_INTERVAL
            hb += _CONFIRM_POLL_INTERVAL
            if hb >= _CONFIRM_HEARTBEAT_INTERVAL:
                hb = 0.0
                self._push_progress({"stage": "heartbeat", "task_id": task_id})
        self._push_progress({"stage": "select_timeout"})
        return chosen

    def _bridge_download(self, tenant_id: str, cand: dict, row) -> dict | None:
        """成稿 bucket 桥接：fill-task 稿件在 {template_id} bucket，拷入
        {tenant_id}-downloads（既有 /agents/download 与 /files/{id}/content 契约）。
        确定性对象名 tplfill-{task_id} 幂等覆盖。失败返回 None（降级为 failed 事件）。"""
        try:
            blob = settings.STORAGE_IMPL.get(cand["template_id"], row.result_file_id)
            if not blob:
                return None
            doc_id = f"tplfill-{row.id}"
            settings.STORAGE_IMPL.put(f"{tenant_id}-downloads", doc_id, blob)
            ext = cand["file_type"]
            filename = f"{_sanitize_filename(cand['name'])}.{ext}"
            return {"doc_id": doc_id, "filename": filename, "name": filename,
                    "mime_type": _MIME_BY_TYPE.get(ext, "application/octet-stream"),
                    "size": len(blob),
                    "url": f"/api/v1/agents/download?id={doc_id}&created_by={tenant_id}"}
        except Exception:  # 桥接失败降级为 failed 事件，不炸观察循环
            logger.warning("bridge download failed, task=%s", row.id, exc_info=True)
            return None

    async def _invoke_async(self, **kwargs):
        if self.check_if_canceled("TemplateFill processing"):
            return
        tenant_id = self._canvas.get_tenant_id() if self._canvas else ""
        if not tenant_id:
            raise ValueError("无法确定画布租户")
        kb_ids = list(dict.fromkeys(k for k in (self._param.dataset_ids or []) if k))
        if not kb_ids:
            raise ValueError("请先在节点配置中选择知识库")
        query = self._resolve_query()

        candidates = self._load_candidates(tenant_id)
        if not candidates:
            raise ValueError("暂无可用的已发布范本，请先在范本库发布并配置填写点")
        chosen = await self._select_templates(tenant_id, candidates, query)
        # selected 先推（flow 面板 templates.length>0 才挂进度组件，范本行须先可见）
        selected_templates = [{"template_id": c["template_id"], "name": c["name"],
             "slot_count": len(c["_placeholders"])} for c in chosen]
        self._push_progress({"stage": "selected", "templates": selected_templates})
        # 运行快照：运行起点（后续选择/确认/任务映射逐阶段覆盖写）
        self._write_run_snapshot("selected", templates=selected_templates)
        # 智能折中：LLM 选出多个范本时暂停询问用户勾选（≥1 个）；单选不打断。
        # 无 task_id（SSE 回传不可用）跳过询问直接按 AI 选择填——与字段确认同兜底。
        if len(chosen) > 1:
            select_task_id = getattr(self._canvas, "task_id", "") or ""
            if select_task_id:
                confirmed = await self._confirm_template_selection(
                    select_task_id, chosen)
                if [c["template_id"] for c in confirmed] != \
                        [c["template_id"] for c in chosen]:
                    chosen = confirmed
                    # 二次 selected：reducer 整体替换范本行（用户勾选子集生效）
                    self._push_progress({"stage": "selected", "templates": [
                        {"template_id": c["template_id"], "name": c["name"],
                         "slot_count": len(c["_placeholders"])} for c in chosen]})
                # 选择已消费（用户提交或超时按 AI 初选）：快照重写为选定态并清 pending。
                # 否则后续 LLM 预判/确认等待期刷新时，恢复端点仍读到 select_pending，
                # 会重放一张已失效的选择卡（僵尸卡）。selected_templates 同步换为
                # 确认后子集，供 filling/done 阶段快照沿用
                selected_templates = [{"template_id": c["template_id"], "name": c["name"],
                     "slot_count": len(c["_placeholders"])} for c in chosen]
                self._write_run_snapshot("selected", templates=selected_templates)
        begin_fields = self._begin_fields()
        user_file_text = self._user_file_evidence()

        # 增量填写判定（同范本已有 done 成稿 + 模板版本未变）：本次必须幂等增量，
        # 否则会重跑全流程并把上次成果覆盖回 default_value/空——这是用户反馈的
        # 「又从头开始 + 改了之后成稿清空」根因。逐范本独立判定（混合时各自走各自路径）。
        baselines: dict[str, dict] = {}    # tid -> {"values": dict, "task": row}
        ctx_id = self._work_context_id()
        for c in chosen:
            # 必须同工作上下文（会话）：跨流程继承会把上一个流程的手改值当既有结论
            # 搬过来，且因进 skip 名单而永不回检索（2026-09-17 demo03 事故）。
            # ctx 取不到 → 返回 None → 本范本走全量。
            base = TplFillTaskService.latest_done_in_context(
                c["template_id"], tenant_id, ctx_id)
            if not base or base.template_version_id != c["_ver"].id:
                continue
            base_values = base.values if isinstance(base.values, dict) else None
            render = base_values.get("render") if base_values else None
            if not isinstance(render, dict) or not render:
                continue
            baselines[c["template_id"]] = {"values": render, "task": base}
        # 逐范本抽取增量意图：用户 query → {intent, direct, changed}。
        # intent=refill 退回全量；intent=noop 保留 baseline 走「轻量复用」（不重抽）；
        # patch/fill_unfilled 走增量确认（只列本次要改的项）。
        incremental_overrides: dict[str, dict] = {}
        noop_tids: set[str] = set()
        for c in chosen:
            tid = c["template_id"]
            if tid not in baselines:
                continue
            try:
                patch = await executor.extract_patch_values(
                    tenant_id, c["_placeholders"], baselines[tid]["values"], query,
                    should_cancel=lambda: self.check_if_canceled("TemplateFill patch extract"))
            except executor.GenerateCancelled:
                raise _FillCancelled() from None
            intent = patch.get("intent") or "noop"
            if intent == "refill":
                # 用户明确要求全部重新填写 → 不增量
                baselines.pop(tid, None)
                continue
            if intent == "noop":
                # query 不涉及该范本：保留 baseline 让 executor 兜回所有 missing，
                # 渲染产物与上次同值（轻量重复，省检索/重抽）。
                noop_tids.add(tid)
                continue
            patch_keys = set(patch.get("changed") or [])
            # patch/fill_unfilled 都用 overrides 覆盖 default_map + 预判
            phs = [it for it in c["_placeholders"]
                   if executor._norm_fill_mode(it) == "llm" and it.get("key")]
            by_key = {it["key"]: it for it in phs}
            # 候选只列 patch keys（用户可继续勾选/取消/直填），
            # default_value = 基线当前值（用作 UI 提示，非提交值）；
            # direct_value = LLM 从用户原话抽出的 direct 值（如「approval_doc 填写成
            # 港里」→ {approval_doc: 港里}），用于前端确认卡输入框预填，让用户在
            # 提交前能看到/编辑 AI 抽取结果——避免空 values 时只点「确认并继续
            # 填写」也能让 fallback_values 生效（旧 bug：前端 inputs 默认空
            # → 用户不打字 → values_raw={} → 后端保留 fallback 即可，但用户
            # 看不到 AI 抽了啥就点了确认；预填解决"看得见 + 可改"）。
            base_values = baselines[tid]["values"]
            direct = dict(patch.get("direct") or {})
            cands = []
            for k in sorted(patch_keys):
                if k not in by_key:
                    continue
                it = by_key[k]
                cur = base_values.get(k)
                cur_str = "" if cur is None else str(cur)
                dval = direct.get(k)
                cands.append({"key": k, "name": it.get("name") or k,
                              "default_value": cur_str,
                              "direct_value": "" if dval is None else str(dval)})
            incremental_overrides[tid] = {
                "candidates": cands,
                "predicted": [c["key"] for c in cands],
                # 兜底：用户没改时按 patch 全量走（changed=patch_keys, values=LLM 抽出的 direct）
                "fallback_changed": patch_keys,
                "fallback_values": direct,
            }

        # P2 预判 + 暂停确认：有默认值字段才触发；返回 {} = 无基线，行为同现状
        # 增量填写范本走 incremental_overrides：候选只列 patch 项，predicted 全勾选，
        # 兜底按 LLM 抽出的 patch 自动继续（与全量范本同口径）
        decisions = await self._confirm_changed_fields(
            chosen, query, begin_fields, incremental_overrides=incremental_overrides)
        # noop 范本：补一份空 decision → _llm_fill_items 返回 [] → executor 仅
        # 用 baseline_values 兜回所有 missing，渲染产物与上次成稿同值（轻量复用）。
        for tid in noop_tids:
            decisions.setdefault(tid, {"changed": set(), "values": {}})

        def _llm_fill_items(c: dict) -> list[dict]:
            """确认后该范本真正要走检索+LLM 的字段（白名单语义）：changed 即用户/AI
            拍板要 LLM 填的 key 集合，不勾 = 有默认值用默认值、无默认值留空。
            decision 缺失（该范本无默认值字段未进确认）→ 全部照旧走检索+LLM。"""
            d = decisions.get(c["template_id"])
            if d is None:
                return [it for it in c["_placeholders"]
                        if it.get("key") and executor._norm_fill_mode(it) == "llm"]
            changed, direct = d.get("changed") or set(), set((d.get("values") or {}).keys())
            return [it for it in c["_placeholders"]
                    if it.get("key") and executor._norm_fill_mode(it) == "llm"
                    and it["key"] not in direct and it["key"] in changed]

        # ② 委托后台执行器：每范本一个 tpl_fill_task 行 + daemon 线程（复用 B端
        # _spawn_fill_task 同一调度路径）。执行与连接解耦——断连/刷新后任务照常
        # 跑完落库；节点降级为观察者。同一范本已有执行中任务则复用观察（不重复起线程）。
        task_of: dict[str, str] = {}   # template_id -> task_id
        total_of: dict[str, int] = {}  # template_id -> llm 槽数（filling 事件 total 回落，建任务时算一次）
        for c in chosen:
            tid = c["template_id"]
            total_of[tid] = len(_llm_fill_items(c))
            # 增量兜底：如果 confirm 后用户没改（user-submitted 等于 fallback）且 patch
            # 实质无变化（fallback_changed 为空 + fallback_values 为空），跳过该范本——
            # 不浪费一次检索+渲染，直接复用 baseline 成稿即可。当前实现：仍 spawn 但
            # executor 端 baseline 会兜回所有 missing，渲染产物与 baseline 同值（轻量重复）。
            row = TplFillTaskService.find_running(tid, tenant_id)
            if row is not None:
                task_of[tid] = row.id
                continue
            task_id = get_uuid()
            base_for_t = baselines.get(tid)
            TplFillTaskService.insert(
                id=task_id, template_id=tid, template_version_id=c["_ver"].id,
                kb_ids=kb_ids,
                params=_canvas_task_params(
                    begin_fields, query, decisions.get(tid),
                    {it["key"] for it in _llm_fill_items(c)},
                    c["_placeholders"], user_file_text,
                    baseline_values=(base_for_t["values"] if base_for_t else None)),
                status="pending", source="canvas", flow_instance_id=ctx_id,
                tenant_id=tenant_id, created_by=tenant_id)
            spawn_fill_task(task_id)
            task_of[tid] = task_id
        # 运行快照：进入填写阶段（确认挂起随此写清除 pending；tasks 映射落定，
        # 刷新恢复端点据此把 fill task 行与运行关联起来）
        self._write_run_snapshot("filling", templates=selected_templates, tasks=task_of)

        # ③ 观察者轮询（1.5s，与确认轮询同量级）：读 Redis 快照 + DB 状态，组装
        # 与既有完全同形的 filling/filled/failed 事件（新增可选 task_id 字段）。
        # values 只在键数增长时推送（SSE 体积控制；前端合并幂等）。
        pending_tasks = dict(task_of)
        results: dict[str, tuple[dict | None, str | None]] = {}  # tid -> (dl, err)
        pushed_values_len: dict[str, int] = {}
        started = time.monotonic()
        while pending_tasks:
            if self.check_if_canceled("TemplateFill observing"):
                # 画布被停止：未终态任务写取消键（executor 批次/检索探针既有），
                # 线程自行收口置 cancelled——结果不丢，用户可重连查看
                for tid, t in list(pending_tasks.items()):
                    executor.write_cancel_key(t)
                self._push_progress({"stage": "cancelled"})
                return
            await asyncio.sleep(1.5)
            if time.monotonic() - started >= _OBSERVE_TIMEOUT_S:
                # 僵尸任务兜底：超总 deadline 仍中间态（典型为部署重启后线程已死）
                # → 写取消键（线程若尚存则促其自行收口）+ 按 failed 收口走正常汇总
                logger.warning("TemplateFill observe timeout (%ss), canvas=%s tasks=%s",
                               _OBSERVE_TIMEOUT_S, self._id, list(pending_tasks.values()))
                for cand in chosen:
                    tid = cand["template_id"]
                    if tid not in pending_tasks:
                        continue
                    task_id = task_of[tid]
                    try:
                        executor.write_cancel_key(task_id)
                    except Exception:  # 收口不因 Redis 抖动中断
                        logger.warning("write_cancel_key failed, task=%s", task_id,
                                       exc_info=True)
                    pending_tasks.pop(tid)
                    results[tid] = (None, "任务超时未完成")
                    self._push_progress({"stage": "failed", "template_id": tid,
                                         "name": cand["name"],
                                         "error": "任务超时未完成", "task_id": task_id})
                break
            for cand in chosen:
                tid = cand["template_id"]
                if tid not in pending_tasks:
                    continue
                task_id = task_of[tid]
                # 瞬时 DB/Redis 抖动不应炸整轮画布运行：本轮跳过保持 pending，下轮重试
                try:
                    row = TplFillTaskService.get_or_none(id=task_id)
                    if row is None:
                        pending_tasks.pop(tid)
                        results[tid] = (None, "任务行不存在")
                        self._push_progress({"stage": "failed", "template_id": tid,
                                             "name": cand["name"],
                                             "error": "任务行不存在", "task_id": task_id})
                        continue
                    snap = executor.read_progress_snapshot(task_id)
                    if row.status in ("pending", "retrieving", "generating", "rendering"):
                        done = (snap or {}).get("done") or 0
                        total = (snap or {}).get("total")
                        if total is None:
                            total = total_of.get(tid, 0)
                        ev = {"stage": "filling", "template_id": tid, "name": cand["name"],
                              "done": done, "total": total, "task_id": task_id}
                        vals = (snap or {}).get("values")
                        if isinstance(vals, dict) and len(vals) > pushed_values_len.get(tid, 0):
                            ev["values"] = vals
                            pushed_values_len[tid] = len(vals)
                        self._push_progress(ev)
                        continue
                    # 终态
                    pending_tasks.pop(tid)
                    if row.status == "done" and row.result_file_id:
                        dl = self._bridge_download(tenant_id, cand, row)
                        if dl is None:
                            results[tid] = (None, "成稿对象读取失败")
                            self._push_progress({"stage": "failed", "template_id": tid,
                                                 "name": cand["name"],
                                                 "error": "成稿对象读取失败", "task_id": task_id})
                            continue
                        results[tid] = (dl, None)
                        ev = {"stage": "filled", "template_id": tid,
                              "name": cand["name"], "download": dl,
                              "task_id": task_id}
                        # 终态补推 values（与 filling 阶段同口径，从 row.values.render 取）：
                        # 不带 values → 前端"查看填写内容"按钮渲染条件
                        # `t.values && Object.keys(t.values).length > 0` 不满足 → 按钮消失
                        render_vals = (row.values or {}).get("render") if isinstance(row.values, dict) else None
                        if isinstance(render_vals, dict) and render_vals:
                            ev["values"] = render_vals
                        unfilled = _unfilled_of(cand["_placeholders"], row)
                        if unfilled:
                            ev["unfilled"] = unfilled
                        # 已填清单（{key,name}，不带值——值已在同一事件的 values 里）：
                        # 与 unfilled 互补，前端并集得全量中文名映射
                        filled = _filled_of(cand["_placeholders"], row)
                        if filled:
                            ev["filled"] = filled
                        self._push_progress(ev)
                    elif row.status == "cancelled":
                        results[tid] = (None, "任务已取消")
                        self._push_progress({"stage": "failed", "template_id": tid,
                                             "name": cand["name"],
                                             "error": "任务已取消", "task_id": task_id})
                    else:
                        err = row.error or (snap or {}).get("error") or "填写失败"
                        results[tid] = (None, err)
                        self._push_progress({"stage": "failed", "template_id": tid,
                                             "name": cand["name"], "error": err,
                                             "task_id": task_id})
                except Exception:  # 单任务本轮观察失败不影响其余任务与画布
                    logger.warning("observe task %s failed", task_id, exc_info=True)
                    continue

        # ④ 汇总输出（契约与改造前一致：download 列表 + content 汇总）
        downloads: list[dict] = []
        summary_lines: list[str] = []
        for cand in chosen:
            tid = cand["template_id"]
            dl, err = results.get(tid, (None, "填写失败"))
            if dl is None:
                summary_lines.append(f"《{cand['name']}》：填写失败（{err}）。")
                continue
            downloads.append(dl)
            total = len(cand["_placeholders"])
            if tid in noop_tids:
                summary_lines.append(
                    f"《{cand['name']}》：本次未涉及，沿用上次填写值"
                    f"（共 {total} 个填写点）。")
            elif tid in incremental_overrides:
                n = len(incremental_overrides[tid]["candidates"])
                summary_lines.append(
                    f"《{cand['name']}》：本次增量更新 {n} 个字段"
                    f"（其余沿用上次填写值，共 {total} 个填写点）。")
            else:
                summary_lines.append(
                    f"《{cand['name']}》：共 {total} 个填写点，AI 填充完成，"
                    f"未检索到值的填写点已留空。")
        if not downloads:
            raise ValueError("所有范本填写均失败：" + "；".join(
                str(results.get(c["template_id"], ("", "未知"))[1]) for c in chosen))
        self.set_output("download", json.dumps(downloads, ensure_ascii=False))
        suffix = "，可在上方预览或下载成稿。" if len(downloads) == 1 else "，可在上方逐份预览或下载成稿。"
        head = (f"已选用 {len(downloads)} 份范本：\n" if len(downloads) > 1 else "已选用范本")
        self.set_output("content", head + "\n".join(
            ("- " + ln for ln in summary_lines) if len(downloads) > 1 else summary_lines
        ) + suffix)
        self._push_progress({"stage": "done"})
        # 运行快照终态（短 TTL）：收尾 10 分钟内刷新仍可权威恢复完整成稿卡
        self._write_run_snapshot("done", templates=selected_templates, tasks=task_of,
                                 ttl=_RUN_SNAPSHOT_DONE_TTL)
        logger.info("TemplateFill done, canvas=%s templates=%s",
                    self._id, [c["template_id"] for c in chosen])

    def thoughts(self) -> str:
        return "正在根据需求选择范本并检索知识库填写..."
