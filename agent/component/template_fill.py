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

检索/产值复用 rag.svr.template_fill.executor 既有函数（与填写任务同一条
确定性 pipeline，不走 tpl_fill_task 表、不建任务行）。类名刻意取 TemplateFill
而非 FillTemplate：Agent 节点的工具（agent/tools/template_fill.py 的 C 端
FillTemplate 工具）同样经 component_class 解析且 agent.component 优先，
组件类若与工具类同名会遮蔽工具、破坏存量画布，两包不得重名。
"""
import asyncio
import json
import logging
import os
import re
from functools import partial

from agent.component.base import ComponentBase, ComponentParamBase
from api.db.services.template_fill_service import (
    TplTemplateService,
    TplTemplateVersionService,
    _sanitize_filename,
)
from common import settings
from common.misc_utils import get_uuid
from rag.svr.template_fill import executor
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
# 单次画布运行的 LLM 并发总闸：多范本并行 × 产值批次并发共用这一个信号量
# （executor.generate_values 接收外部 sem），防止多范本时并发调用数相乘打爆 provider
_FILL_CONCURRENCY = 4
# 画布侧 KB 检索并发闸：与产值总闸同理，防止多范本共享检索的槽级并发打爆 ES
# （executor.retrieve_all_shared 接收外部 sem；B 端任务管道不传 sem，走
# executor 自建 RETRIEVAL_CONCURRENCY，行为零变化）。env 坏值（非数字/"0"）降级
# 默认而非炸 import / Semaphore(0) 永久阻塞。
def _parse_concurrency(default: int) -> int:
    raw = os.getenv("TEMPLATE_FILL_RETRIEVAL_CONCURRENCY", "")
    try:
        return max(1, int(raw))
    except (TypeError, ValueError):
        if raw:
            logger.warning("invalid TEMPLATE_FILL_RETRIEVAL_CONCURRENCY=%r, fallback to %d", raw, default)
        return default


_RETRIEVAL_CONCURRENCY = _parse_concurrency(2)

# P2 暂停确认：预判后挂起等待用户在对话侧确认（confirm_pending SSE → 前端确认卡片
# → POST /template/fill/confirm 写 Redis 键 → 本节点轮询读取）。
_CONFIRM_TIMEOUT = 600          # 确认等待超时（秒），超时按预判结果自动继续
_CONFIRM_POLL_INTERVAL = 1.5    # Redis 轮询间隔（与 cancel 探针同量级）


class _FillCancelled(Exception):
    """画布取消中断填写：不落 failed 事件，由 invoke 统一推 cancelled。"""


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
        """P2 暂停确认：预判各范本疑似变化字段 → 推 confirm_pending 事件挂起等待。
        返回 {template_id: {"changed": set, "values": dict}}；全部选中范本均无
        默认值字段时返回 {}（跳过确认，行为与现状一致）。
        超时/Redis 异常/预判失败 → 按预判结果（或空集）自动继续。"""
        d_map: dict[str, list[dict]] = {}
        for c in chosen:
            items = [it for it in c["_placeholders"]
                     if executor._norm_fill_mode(it) == "llm" and it.get("key")
                     and str(it.get("default_value") or "")]
            if items:
                d_map[c["template_id"]] = items
        if not d_map:
            return {}
        task_id = getattr(self._canvas, "task_id", "") or ""
        background = dict(begin_fields)
        if query:
            background["用户需求描述"] = query[:_BEGIN_FIELD_PROMPT_MAX]
        predicted: dict[str, set] = {}
        try:
            for tid, items in d_map.items():
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
        decisions = {tid: {"changed": set(predicted.get(tid) or set()), "values": {}}
                     for tid in d_map}
        if not task_id:
            return decisions
        waited = 0.0
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
        self._push_progress({"stage": "confirm_timeout"})
        return decisions

    async def _fill_one(self, tenant_id: str, cand: dict, chunks_by_key: dict, query: str,
                        begin_fields: dict, user_file_text: str, sem: asyncio.Semaphore,
                        on_progress=None, should_cancel=None,
                        decision: dict | None = None) -> tuple[dict, dict, int]:
        """对单个选中范本走完整填写 pipeline：LLM 产值 → param 直取 → 渲染。
        检索已由 _invoke_async 跨范本共享完成（chunks_by_key 传入）；sem 为全局
        LLM 并发闸（多范本并行 × 批次并发共用）；on_progress 透传 executor
        批次产值进度回调 (done, total)；should_cancel 为取消探针，透传给
        executor.generate_values（命中抛 GenerateCancelled，由调用方转 _FillCancelled）；
        decision 为暂停确认产物（None = 未走确认，行为与现状一致）：
        {"changed": set, "values": dict}，values 为用户直填值（空串=明确清空）。
        返回 (download_info, cell_status, filled_count)。"""
        placeholders = cand["_placeholders"]
        decision = decision or {}
        direct_values = decision.get("values") or {}
        changed_keys = decision.get("changed") or set()

        # ③ 用户上传文件作为填写证据：预置片段插到每槽证据首位（优先于 KB 片段）
        if user_file_text:
            for it in placeholders:
                if executor._norm_fill_mode(it) != "llm" or not it.get("key"):
                    continue
                slot = chunks_by_key.setdefault(it["key"], {"chunks": [], "query": ""})
                slot["chunks"].insert(0, {
                    "content": f"[用户上传文件] {user_file_text}",
                    "doc_id": "", "doc_name": "用户上传文件", "similarity": 1.0})

        # ④ LLM 产值（背景信息 = Begin 表单字段 + 需求描述）+ param 模式直取 Begin 字段
        # P2 条件执行：llm 槽与检索一致收窄——用户直填直取（不进 LLM）；
        # 有默认值且预判未变化（D−C）不进 LLM，直接走 _merge_default_values 兜底
        llm_placeholders = [it for it in placeholders
                            if executor._norm_fill_mode(it) == "llm" and it.get("key")
                            and it["key"] not in direct_values
                            and not (str(it.get("default_value") or "")
                                     and it["key"] not in changed_keys)]
        llm_chunks = {it["key"]: chunks_by_key.get(it["key"], {"chunks": [], "query": ""})
                      for it in llm_placeholders}
        background = dict(begin_fields)
        if query:
            background["用户需求描述"] = query[:_BEGIN_FIELD_PROMPT_MAX]
        generated, missing = await executor.generate_values(
            tenant_id, llm_placeholders, llm_chunks, background, sem=sem,
            on_progress=on_progress, should_cancel=should_cancel)
        executor._merge_param_values(placeholders, generated, missing, begin_fields)
        # P2 用户直填值直取（空串=明确清空，渲染为空）；随后作为沉淀 override。
        # 先摘出 missing，防 _merge_default_values 把默认值回填覆盖用户直填
        for k, v in direct_values.items():
            generated[k] = v
            missing.discard(k)
        executor._merge_default_values(placeholders, generated, missing)
        values, cell_status = executor.build_values(placeholders, generated)

        # ⑤ 渲染（工作副本缺失/渲染失败向上抛，由 invoke_async 统一落 _ERROR）
        from rag.svr.template_fill import renderer
        blob = settings.STORAGE_IMPL.get(cand["template_id"], cand["_ver"].render_file_id)
        if not blob:
            raise ValueError("范本工作副本缺失，请重新上传或识别填写点")
        addr_by_key = None
        if cand["file_type"] == "xlsx":
            addr_by_key = {it["key"]: it.get("addr") for it in placeholders if it.get("key")}
        out = renderer.render(cand["file_type"], blob, values, addr_by_key)

        # 产值沉淀为默认值（auto）：失败仅告警，不影响成稿交付
        # （TplTemplateVersionService 已在模块顶部导入；同步 DB 调用，与文件内
        #  _load_candidates 等既有同步调用惯例一致）
        try:
            TplTemplateVersionService.sediment_defaults(
                cand["template_id"], cand["_ver"].id, values,
                override_keys=set(direct_values.keys()))
        except Exception:  # noqa: BLE001 — 沉淀失败不阻断成稿交付
            logger.warning("sediment_defaults failed, template=%s",
                           cand["template_id"], exc_info=True)

        # ⑥ 落稿：bucket 用 {tenant_id}-downloads（/agents/download 与 /files/{id}/content
        # 两个端点的既有读取契约都是这个 bucket，前端下载与在线预览因此都可直接用）
        doc_id = get_uuid()
        settings.STORAGE_IMPL.put(f"{tenant_id}-downloads", doc_id, out)
        ext = cand["file_type"]
        filename = f"{_sanitize_filename(cand['name'])}.{ext}"
        filled = sum(1 for s in cell_status.values() if s == "filled")
        return ({
            "doc_id": doc_id, "filename": filename,
            "mime_type": _MIME_BY_TYPE.get(ext, "application/octet-stream"),
            "size": len(out),
            "url": f"/api/v1/agents/download?id={doc_id}&created_by={tenant_id}",
            "name": filename}, cell_status, filled)

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
        self._push_progress({"stage": "selected", "templates": [
            {"template_id": c["template_id"], "name": c["name"],
             "slot_count": len(c["_placeholders"])} for c in chosen]})
        begin_fields = self._begin_fields()
        user_file_text = self._user_file_evidence()

        # P2 预判 + 暂停确认：有默认值字段才触发；返回 {} = 无基线，行为同现状
        decisions = await self._confirm_changed_fields(chosen, query, begin_fields)

        def _llm_fill_items(c: dict) -> list[dict]:
            """确认后该范本真正要走检索+LLM 的字段：N（无默认值）∪ C∩D（预判变化）
            ∪ 用户直填之外的兜底排除 D−C（直用默认值，免检索免 LLM）。"""
            d = decisions.get(c["template_id"]) or {}
            changed, direct = d.get("changed") or set(), set((d.get("values") or {}).keys())
            out = []
            for it in c["_placeholders"]:
                k = it.get("key")
                if not k or executor._norm_fill_mode(it) != "llm" or k in direct:
                    continue
                if str(it.get("default_value") or "") and k not in changed:
                    continue
                out.append(it)
            return out

        # ② 跨范本共享检索：所有选中范本的填写点按 (top_k, 检索词) 去重，
        # 同一检索词只查一次 ES（多范本重合场景 ES 压力骤减）；单槽失败降级空证据；
        # 槽级并发钉在 _RETRIEVAL_CONCURRENCY + 取消探针（批次级取消，命中即中断检索）
        retrieval_sem = asyncio.Semaphore(_RETRIEVAL_CONCURRENCY)
        cancelled = lambda: self.check_if_canceled("TemplateFill retrieval/filling")
        try:
            chunks_list = await executor.retrieve_all_shared(
                tenant_id, [_llm_fill_items(c) for c in chosen], kb_ids,
                task_id=f"canvas:{self._id}",
                should_cancel=cancelled, sem=retrieval_sem)
        except executor.GenerateCancelled:
            # 检索阶段取消信号不外逸：_FillCancelled 只在 gather 之后被判定，
            # 此处转抛会逸出 invoke_async 被吞成 _ERROR（str 为空串 → canvas
            # 误判正常），UI 悬挂 + 下游空输出。就地与 gather 后分支同行为：
            # 推 cancelled 终态事件后返回（不落 failed/done、不写输出）。
            logger.info("TemplateFill %s cancelled during shared retrieval", self._id)
            self._push_progress({"stage": "cancelled"})
            return

        # ③④ 多范本并行填写（LLM 总并发钉在 _FILL_CONCURRENCY）；单范本失败
        # 不拖死整节点，降级为汇总行提示，其余范本照常产出；每范本推
        # filling/filled/failed 进度事件（filling total 只数 llm 槽，param 不计）
        sem = asyncio.Semaphore(_FILL_CONCURRENCY)

        async def _fill_and_notify(cand: dict, chunks: dict):
            tid, name = cand["template_id"], cand["name"]
            if self.check_if_canceled("TemplateFill filling"):
                raise _FillCancelled()
            llm_total = len(_llm_fill_items(cand))
            self._push_progress({"stage": "filling", "template_id": tid, "name": name,
                                 "done": 0, "total": llm_total})

            def _on_gen_progress(done: int, total: int):
                self._push_progress({"stage": "filling", "template_id": tid,
                                     "name": name, "done": done, "total": total})

            try:
                dl, cell_status, filled = await self._fill_one(
                    tenant_id, cand, chunks, query, begin_fields,
                    user_file_text, sem, on_progress=_on_gen_progress,
                    should_cancel=cancelled,
                    decision=decisions.get(cand["template_id"]))
            except _FillCancelled:
                raise
            except executor.GenerateCancelled:
                # executor 产值/检索批次级取消信号 → 转抛 _FillCancelled()，与
                # 入口检查同路，由 gather 后统一判定推 cancelled（不落 failed）
                raise _FillCancelled() from None
            except Exception as e:
                logger.warning("TemplateFill %s fill failed: %s", tid, e)
                self._push_progress({"stage": "failed", "template_id": tid,
                                     "name": name, "error": str(e)})
                return (cand, None, {}, 0, e)
            self._push_progress({"stage": "filled", "template_id": tid,
                                 "name": name, "download": dl})
            return (cand, dl, cell_status, filled, None)

        results = await asyncio.gather(
            *[_fill_and_notify(cand, chunks)
              for cand, chunks in zip(chosen, chunks_list)],
            return_exceptions=True)

        if self.check_if_canceled("TemplateFill after gather") or any(
                isinstance(r, _FillCancelled) for r in results):
            self._push_progress({"stage": "cancelled"})
            return

        # download 输出为列表（Message._extract_downloads 原生支持 list 契约，
        # 前端逐条渲染下载/预览）
        downloads: list[dict] = []
        summary_lines: list[str] = []
        for cand, res in zip(chosen, results):
            if isinstance(res, BaseException):
                summary_lines.append(f"《{cand['name']}》：填写失败（{res}）。")
                continue
            _cand, dl, _cell_status, filled, _err = res
            if dl is None:
                summary_lines.append(f"《{cand['name']}》：填写失败。")
                continue
            downloads.append(dl)
            total = len(cand["_placeholders"])
            summary_lines.append(
                f"《{cand['name']}》：共 {total} 个填写点，AI 填充 {filled} 个，"
                f"{total - filled} 个未检索到值已留空。")
        if not downloads:
            # 失败详情：_fill_and_notify 已捕获的异常在五元组第 5 位，
            # 未捕获异常（return_exceptions=True）是 BaseException 元素
            errs = [str(r) if isinstance(r, BaseException) else str(r[4])
                    for r in results]
            raise ValueError("所有范本填写均失败：" + "；".join(errs))
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
