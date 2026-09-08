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
① 拉取本租户已发布范本清单 → ② LLM 按用户需求（query，默认 {sys.query}）选最
合适的一个 → ③ 按其填写点逐条 KB 检索 → ④ LLM 批量产值（缺值留空待人工二次
加工，语义与模板填写引擎 2026-09-08 改造后一致）→ ⑤ docxtpl/openpyxl 渲染 →
⑥ 产物入 STORAGE_IMPL 并输出 download JSON（下游 Message 节点 _extract_downloads
识别后渲染下载按钮，契约同 DocGenerator）。

检索/产值复用 rag.svr.template_fill.executor 既有函数（与填写任务同一条
确定性 pipeline，不走 tpl_fill_task 表、不建任务行）。类名刻意取 TemplateFill
而非 FillTemplate：Agent 节点的工具（agent/tools/template_fill.py 的 C 端
FillTemplate 工具）同样经 component_class 解析且 agent.component 优先，
组件类若与工具类同名会遮蔽工具、破坏存量画布，两包不得重名。
"""
import json
import logging
import re
from functools import partial

from agent.component.base import ComponentParamBase, ComponentBase
from api.db.services.template_fill_service import (
    TplTemplateService,
    TplTemplateVersionService,
    _sanitize_filename,
)
from common import settings
from common.misc_utils import get_uuid
from rag.svr.template_fill import executor

logger = logging.getLogger(__name__)

# 拉已发布范本的分页上限（get_list_page size 硬顶 100，一次拉全量足够选型）
_LIST_SIZE = 100

_MIME_BY_TYPE = {
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}

_SELECT_SYSTEM = (
    "你是范本选择器。根据用户需求，从候选范本中选出最合适的一个。\n"
    "只输出一个 JSON 对象：{\"template_id\": \"选中的范本id\"}，不要输出任何其他文字。")


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


def parse_selection(text: str, candidate_ids: list[str]) -> str:
    """解析选型 LLM 输出并校验：非法 JSON / id 不在候选内 → ValueError（调用方
    兜底报错，不静默换第一个——选错范本产出的成稿比失败更有害）。"""
    raw = executor._extract_json(text or "")
    tpl_id = raw.get("template_id")
    if not tpl_id or tpl_id not in candidate_ids:
        raise ValueError("AI 未能从已发布范本中选出合适的范本，请补充需求描述或检查范本库")
    return tpl_id


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
                except Exception:
                    ans = str(val)
            text = re.sub(r"\{%s\}" % re.escape(k), ans, text)
        return text.strip()

    def _load_candidates(self, tenant_id: str) -> list[dict]:
        rows, _total = TplTemplateService.get_list_page(
            tenant_id, status="published", page=1, size=_LIST_SIZE)
        return build_candidates(rows, TplTemplateVersionService.latest)

    async def _select_template(self, tenant_id: str, candidates: list[dict], query: str) -> dict:
        """唯一候选直接命中（省一次 LLM）；多个走 LLM 选型。"""
        if len(candidates) == 1:
            return candidates[0]
        mdl = executor._build_chat_mdl(tenant_id)
        catalog = [{"template_id": c["template_id"], "name": c["name"],
                    "description": c["description"], "填写点": c["slot_names"]}
                   for c in candidates]
        user_msg = ("## 候选范本\n" + json.dumps(catalog, ensure_ascii=False) +
                    "\n\n## 用户需求\n" + (query or "（未提供）"))
        ans = await mdl.async_chat(_SELECT_SYSTEM, [{"role": "user", "content": user_msg}])
        chosen = parse_selection(ans, [c["template_id"] for c in candidates])
        return next(c for c in candidates if c["template_id"] == chosen)

    async def _invoke_async(self, **kwargs):
        if self.check_if_canceled("TemplateFill processing"):
            return
        tenant_id = self._canvas.get_tenant_id() if self._canvas else ""
        if not tenant_id:
            raise ValueError("无法确定画布租户")
        kb_ids = [k for k in (self._param.dataset_ids or []) if k]
        if not kb_ids:
            raise ValueError("请先在节点配置中选择知识库")
        query = self._resolve_query()

        candidates = self._load_candidates(tenant_id)
        if not candidates:
            raise ValueError("暂无可用的已发布范本，请先在范本库发布并配置填写点")
        cand = await self._select_template(tenant_id, candidates, query)
        placeholders = cand["_placeholders"]

        # ② ③ ④ 与模板填写引擎同款确定性 pipeline（params 为空：节点形态无任务参数，
        # param 模式字段自然留空；单槽检索失败在 _retrieve_all 内降级为空证据）
        chunks_by_key, _evidence = await executor._retrieve_all(
            tenant_id, placeholders, kb_ids, {}, task_id=f"canvas:{self._id}")
        llm_placeholders = [it for it in placeholders
                            if executor._norm_fill_mode(it) == "llm" and it.get("key")]
        llm_chunks = {it["key"]: chunks_by_key.get(it["key"], {"chunks": [], "query": ""})
                      for it in llm_placeholders}
        generated, missing = await executor.generate_values(tenant_id, llm_placeholders, llm_chunks, {})
        executor._merge_param_values(placeholders, generated, missing, {})
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

        # ⑥ 落稿 + 下载输出（契约同 DocGenerator：下游 Message 渲染下载按钮）
        doc_id = get_uuid()
        settings.STORAGE_IMPL.put(tenant_id, doc_id, out)
        ext = cand["file_type"]
        filename = f"{_sanitize_filename(cand['name'])}.{ext}"
        self.set_output("download", json.dumps({
            "doc_id": doc_id, "filename": filename,
            "mime_type": _MIME_BY_TYPE.get(ext, "application/octet-stream"),
            "size": len(out)}))
        filled = sum(1 for s in cell_status.values() if s == "filled")
        self.set_output(
            "content",
            f"已选用范本《{cand['name']}》：共 {len(placeholders)} 个填写点，"
            f"AI 填充 {filled} 个，{len(placeholders) - filled} 个未检索到值已留空，"
            "待人工审核补充。成稿见附件。")
        logger.info("TemplateFill done, canvas=%s template=%s slots=%s filled=%s",
                    self._id, cand["template_id"], len(placeholders), filled)

    def thoughts(self) -> str:
        return "正在根据需求选择范本并检索知识库填写..."
