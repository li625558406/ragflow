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


class _FillCancelled(Exception):
    """画布取消中断填写：不落 failed 事件，由 invoke 统一推 cancelled。"""


def _canvas_task_params(begin_fields: dict, query: str, decision: dict | None,
                        llm_item_keys: set, placeholders: list[dict],
                        user_file_text: str) -> dict:
    """组装委托给 executor.execute_task 的任务 params：
    背景（Begin 字段+需求描述，与节点内 background 同构）+ 下划线保留键
    （直填值/LLM 白名单键/检索跳过键/用户文件证据），executor 侧 split_canvas_params 拆解。
    llm_item_keys 为确认后仍要走检索+LLM 的字段 key 集合（白名单），同时写入
    _changed_keys 供 executor 收窄；其余 llm 槽（默认值兜底/留空与直填）跳过检索，
    由 executor 的 missing 显式纳入 → _merge_default_values 兜底。"""
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
    return params


def _unfilled_of(placeholders: list[dict], row) -> list[dict] | None:
    """终态行的成稿留空填写点（executor.derive_unfilled 包装）：值源 DB 行
    values.render（终态权威，不依赖 Redis 快照存活）。无留空或值结构异常
    返回 None——filled 事件不下发该字段，前端不渲染汇总条。"""
    values = getattr(row, "values", None)
    if not isinstance(values, dict):
        return None
    return executor.derive_unfilled(placeholders, values.get("render") or {}) or None


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

    async def _confirm_changed_fields(self, chosen: list[dict], query: str,
                                      begin_fields: dict) -> dict:
        """P2 暂停确认（全量展示）：候选 = 各范本全部 llm 填写点（含无默认值字段），
        AI 预判只覆盖默认值子集；勾选 = 交给检索+LLM，不勾 = 有默认值用默认值、
        无默认值留空。返回 {template_id: {"changed": set, "values": dict}}；
        全部选中范本均无默认值字段时返回 {}（跳过确认，触发条件与现状一致）。
        超时/Redis 异常/预判失败 → 按预判∪无默认值字段自动继续（同现状全填）。"""
        d_map: dict[str, list[dict]] = {}        # 全部 llm 填写点（候选 + valid 校验）
        default_map: dict[str, list[dict]] = {}  # 默认值子集（触发 + 预判）
        for c in chosen:
            items = [it for it in c["_placeholders"]
                     if executor._norm_fill_mode(it) == "llm" and it.get("key")]
            if items:
                d_map[c["template_id"]] = items
                defaults = [it for it in items if str(it.get("default_value") or "")]
                if defaults:
                    default_map[c["template_id"]] = defaults
        if not default_map:
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
        self._push_progress({
            "stage": "confirm_pending", "task_id": task_id,
            "confirm_nonce": nonce,
            "confirm_templates": [{"template_id": tid, "name": name_of.get(tid, ""),
                           "candidates": [{"key": it["key"],
                                           "name": it.get("name") or it["key"],
                                           "default_value": it.get("default_value")}
                                          for it in d_map[tid]],
                           "predicted": sorted(predicted.get(tid) or set())}
                          for tid in d_map]})
        # 兜底（未确认/无 task_id）：changed = 预判 ∪ 全部无默认值字段——与确认卡
        # 初始勾选一致，无默认值字段照旧走检索+LLM（全量展示白名单语义下的现状保持）
        decisions = {}
        for tid, items in d_map.items():
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
                    valid = {it["key"] for it in d_map[tid]}
                    # 载荷结构防御：非官方写键可能塞标量（values 非 dict / changed 非 list），
                    # isinstance 兜底按空处理，不炸 run
                    changed_raw = d.get("changed") if isinstance(d.get("changed"), list) else []
                    values_raw = d.get("values") if isinstance(d.get("values"), dict) else {}
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
        self._push_progress({
            "stage": "select_pending", "task_id": task_id,
            "select_nonce": nonce,
            "select_candidates": [{"template_id": c["template_id"],
                                   "name": c["name"],
                                   "slot_count": len(c["_placeholders"]),
                                   "description": c.get("description") or ""}
                                  for c in chosen],
            "ai_selected": [c["template_id"] for c in chosen],
        })
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
        self._push_progress({"stage": "selected", "templates": [
            {"template_id": c["template_id"], "name": c["name"],
             "slot_count": len(c["_placeholders"])} for c in chosen]})
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
        begin_fields = self._begin_fields()
        user_file_text = self._user_file_evidence()

        # P2 预判 + 暂停确认：有默认值字段才触发；返回 {} = 无基线，行为同现状
        decisions = await self._confirm_changed_fields(chosen, query, begin_fields)

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
            row = TplFillTaskService.find_running(tid, tenant_id)
            if row is not None:
                task_of[tid] = row.id
                continue
            task_id = get_uuid()
            TplFillTaskService.insert(
                id=task_id, template_id=tid, template_version_id=c["_ver"].id,
                kb_ids=kb_ids,
                params=_canvas_task_params(
                    begin_fields, query, decisions.get(tid),
                    {it["key"] for it in _llm_fill_items(c)},
                    c["_placeholders"], user_file_text),
                status="pending", source="canvas", flow_instance_id="",
                tenant_id=tenant_id, created_by=tenant_id)
            spawn_fill_task(task_id)
            task_of[tid] = task_id

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
                        unfilled = _unfilled_of(cand["_placeholders"], row)
                        if unfilled:
                            ev["unfilled"] = unfilled
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
        logger.info("TemplateFill done, canvas=%s templates=%s",
                    self._id, [c["template_id"] for c in chosen])

    def thoughts(self) -> str:
        return "正在根据需求选择范本并检索知识库填写..."
