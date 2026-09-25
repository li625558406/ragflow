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
"""C端对话 DocumentRewrite 工具：成稿 docx 按节 LLM 局部重写（设计 §3/§8）。

照 agent/tools/template_fill.py 插件结构：DocumentRewriteParam(ToolParamBase) 声明
meta，DocumentRewrite(ToolBase) 提供 _invoke，被 component 按类名自动发现。
DB/存储层（FileService / FlowVersionService / LLMBundle）全部延迟 import，
避免工具注册期触发 DB 依赖。

文档来源：显式 doc_id 参数 > sys.recent_downloads 最新成稿卡（含范本填写成稿
tplfill- 对象）> flow 场景 sys.flow_version_id（前端保证有成稿卡时不传该变量，
由前端控制「最新产物优先」）。rewrite/rollback 成功后把 download 契约 append
到 canvas 全局 sys.pending_downloads，由 Message 组件合并输出成稿卡。

versions / rewriter 执行层自身零 DB 依赖（DB/存储 import 全在函数体内），
此处顶层 import 既保注册期纯净，又让测试可按模块属性 patch。
"""
import asyncio
import io
import logging
import mimetypes
import os
from abc import ABC

from agent.tools.base import ToolBase, ToolMeta, ToolParamBase
from common.connection_utils import timeout
from rag.svr.document_rewrite.rewriter import rewrite_section
from rag.svr.document_rewrite.versions import (
    ensure_base_version,
    get_version,
    list_versions,
    load_flow_version_blob,
    register_chat_version,
    root_id_for_doc,
    save_flow_version,
)

logger = logging.getLogger(__name__)

# 执行超时：与 template_fill.py 同款 env 驱动
_EXEC_TIMEOUT = int(os.environ.get("COMPONENT_EXEC_TIMEOUT", "60"))

_MIME_BY_TYPE = {
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}


class DocumentRewriteParam(ToolParamBase):
    """DocumentRewrite 工具参数声明。"""

    def __init__(self):
        self.meta: ToolMeta = {
            "name": "DocumentRewrite",
            "description": """文档局部修改工具。对当前会话最近生成的成稿文档（Word）做精准替换或按节重写。五个 action：

1. outline：返回文档的带编号章节目录。当不确定节号，或没有指明操作文档时，先调用它确认。
2. replace：精准替换。把文档中某句/某条原文逐字替换为指定文本，文档其余内容一字不动。需要 find_text（要被替换的原文片段，必须与文档正文逐字一致，尽量给完整句子或条款）和 replace_text（新文本）。
   当用户说「把XX内容改成/换成/替换成YY」且能从对话上下文或 outline 确认原文片段时，优先用 replace 而不是 rewrite——replace 零重生成、绝不损伤文档其他内容。
3. rewrite：重写某一节（整节正文由 LLM 按要求重新编写）。需要 section_no（节号，来自 outline）和 instruction（用户对该节的重写要求，原样转述用户的补充要求）。
   只在需要按语义改写整节（润色/扩写/调整表述/补充内容）时使用，不要用它做单句替换。完成后返回新版本说明，用户会看到新的成稿卡片。多次重写请逐节顺序进行，请勿在同一轮并行发起多个 rewrite。
4. versions：列出该文档的全部历史版本（版本号/来源/说明）。
5. rollback：回退到某个历史版本。需要 version_no（versions 返回的版本号）。回退会生成一个新版本（内容为历史版），不会丢失任何版本。

使用时机：用户对已生成的成稿说「把XX改成YY/把第N节重写/回退到上一版」时使用。文档默认取本会话最近一张成稿卡（含范本填写生成的成稿）；本会话没有成稿卡时才落到流程版本文档，均无需传 doc_id。用户明确要求修改流程版本文档而本会话有成稿卡时，说明请到「流程」页签的版本时间线操作。""",
            "parameters": {
                "action": {
                    "type": "string",
                    "description": "操作类型：outline（目录）/ replace（精准替换原文）/ rewrite（重写某节）/ versions（版本列表）/ rollback（回退）。",
                    "enum": ["outline", "replace", "rewrite", "versions", "rollback"],
                    "required": True,
                },
                "find_text": {
                    "type": "string",
                    "description": "要被替换的原文片段，必须与文档正文逐字一致（含标点），尽量给完整句子或条款。action=replace 时必填。",
                    "default": "",
                    "required": False,
                },
                "replace_text": {
                    "type": "string",
                    "description": "替换后的新文本。action=replace 时必填。",
                    "default": "",
                    "required": False,
                },
                "section_no": {
                    "type": "string",
                    "description": "节号（outline 返回的第N节中的 N）。action=rewrite 时必填。",
                    "default": "",
                    "required": False,
                },
                "instruction": {
                    "type": "string",
                    "description": "用户对本节的重写要求（如「重点补充进度安排」）。action=rewrite 时必填。",
                    "default": "",
                    "required": False,
                },
                "version_no": {
                    "type": "string",
                    "description": "目标历史版本号。action=rollback 时必填。",
                    "default": "",
                    "required": False,
                },
                "doc_id": {
                    "type": "string",
                    "description": "可选。目标成稿对象 id；不传则用本会话最近一张成稿卡。",
                    "default": "",
                    "required": False,
                },
            },
        }
        super().__init__()


class DocumentRewrite(ToolBase, ABC):
    """C端对话成稿文档按节重写工具。"""

    component_name = "DocumentRewrite"

    # ---------- 入口 ----------

    @timeout(_EXEC_TIMEOUT)
    def _invoke(self, **kwargs):
        if self.check_if_canceled("DocumentRewrite"):
            return
        try:
            action = str(kwargs.get("action") or "").strip()
            if action == "outline":
                return self._outline(kwargs)
            if action == "replace":
                return self._replace(kwargs)
            if action == "rewrite":
                return self._rewrite(kwargs)
            if action == "versions":
                return self._versions(kwargs)
            if action == "rollback":
                return self._rollback(kwargs)
            return (
                f"不支持的 action：{action or '(空)'}。"
                "可用 action：outline（目录）/ replace（精准替换原文）/ rewrite（重写某节）"
                "/ versions（版本列表）/ rollback（回退）。"
            )
        except ValueError as e:
            # 引导类失败（无文档来源/文件缺失/LLM 重写失败）直接把说明给 LLM 转述
            return str(e)
        except Exception:
            logger.exception("DocumentRewrite invoke failed")
            self.set_output("_ERROR", "文档重写执行失败")
            return "文档重写执行失败，请稍后重试。"

    # ---------- 目标解析 ----------

    def _read_sys(self, name):
        """读 sys 变量：canvas.run 白名单空值不写入，缺失/异常一律容错为 None。"""
        try:
            return self._canvas.get_variable_value(name)
        except KeyError:
            return None
        except Exception:  # noqa: BLE001 — canvas 实现异常类型不一，读取容错
            return None

    def _resolve_doc_id(self, explicit_doc_id: str) -> str:
        """chat 场景目标：显式 doc_id > sys.recent_downloads 最新一张；都没有引导报错。"""
        doc_id = str(explicit_doc_id or "").strip()
        if doc_id:
            return doc_id
        recent = self._read_sys("sys.recent_downloads")
        if isinstance(recent, list) and recent:
            first = recent[0]
            if isinstance(first, dict) and first.get("doc_id"):
                return str(first["doc_id"])
        raise ValueError(
            "无法确定要重写的文档：本会话没有可用的成稿卡片。"
            "请先用范本填写生成成稿，或明确指定要重写的文档。"
        )

    def _load_chat_blob(self, doc_id: str) -> tuple[bytes, str, str]:
        """读 chat 成稿 blob。返回 (blob, doc_id, 原文件名去扩展名)。

        chat 成稿存于 {tenant_id}-downloads 桶，对象名即 doc_id；桶按租户隔离，
        取不到即视为无权/已删。FileService 延迟 import 防注册期依赖。"""
        from api.db.services.file_service import FileService

        tenant_id = self._canvas.get_tenant_id()
        blob = FileService.get_blob(tenant_id, doc_id)
        if not blob:
            raise ValueError(f"成稿文件不存在或已被清理（doc_id={doc_id[:24]}），无法重写。")
        recent = self._read_sys("sys.recent_downloads")
        base_name = doc_id
        if isinstance(recent, list):
            for item in recent:
                if isinstance(item, dict) and item.get("doc_id") == doc_id and item.get("filename"):
                    base_name = str(item["filename"])
                    break
        # 链感知：doc_id 可能是链上任一对象（原始成稿或 rewrite 产物）。
        # 重写必须基于链内最新版内容，否则连续重写会丢掉上一次的改写。
        # list_versions 按 version_no 升序，末位即最新版；切到最新版后
        # root_id 不变（版本链锚），链不会断。
        latest_obj = ""
        rows = list_versions(root_id_for_doc(doc_id))
        if rows and rows[-1]["obj"] != doc_id:
            latest_blob = FileService.get_blob(tenant_id, rows[-1]["obj"])
            if latest_blob:
                blob, latest_obj = latest_blob, rows[-1]["obj"]
            else:
                logger.warning(
                    "[rewrite] chain latest blob missing root=%s obj=%s, "
                    "fallback to requested obj=%s",
                    root_id_for_doc(doc_id), rows[-1]["obj"], doc_id)
        # recent 未命中（连续重写的 rewrite-{uuid} 产物对象）时展示名退化成
        # 对象名，不可读——用链内最新版本的 file_name 兜底。
        if base_name == doc_id and rows:
            base_name = str(rows[-1].get("file_name") or base_name)
        if base_name.lower().endswith(".docx"):
            base_name = base_name[:-5]
        return blob, (latest_obj or doc_id), base_name

    def _load_flow_target(self) -> tuple[bytes | None, dict | None, dict | None]:
        """flow 场景目标：sys.flow_version_id → (blob, flow_version行, flow行)。
        未处于 flow 场景（sys 变量缺失）返回 (None, None, None)。"""
        fv_id = str(self._read_sys("sys.flow_version_id") or "").strip()
        if not fv_id:
            return None, None, None
        from api.db.services.flow_service import FlowVersionService

        ok, row = FlowVersionService.get_by_id(fv_id)
        if not ok or not row:
            raise ValueError("流程版本文档不存在或已被删除，无法重写。")
        data = row if isinstance(row, dict) else row.__data__
        blob, flow = load_flow_version_blob(data)
        return blob, data, flow

    def _build_chat_mdl(self, tenant_id: str):
        """与 rag/svr/template_fill/executor.py _build_chat_mdl 同源的租户默认对话模型。"""
        from api.db.joint_services.tenant_model_service import get_tenant_default_model_by_type
        from api.db.services.llm_service import LLMBundle
        from common.constants import LLMType as _LT
        return LLMBundle(tenant_id, get_tenant_default_model_by_type(tenant_id, _LT.CHAT))

    # ---------- 公共 ----------

    def _doc_for_action(self, kwargs) -> tuple[object, str, str, str, bytes, dict | None]:
        """统一取 (Document, root_id, mode, base_name, 源blob, flow行)。
        mode: chat（root_id=doc_id 剥 tplfill- 前缀）/ flow（root_id=flow_id）。"""
        from docx import Document as DocxDocument

        blob, fv_row, flow_row = self._load_flow_target()
        if blob is not None:
            mode = "flow"
            root_id = str(fv_row.get("flow_id") or "")
            base_name = (fv_row.get("file_name") or "流程文档").rsplit(".", 1)[0]
        else:
            mode = "chat"
            doc_id = self._resolve_doc_id(kwargs.get("doc_id"))
            blob, doc_id, base_name = self._load_chat_blob(doc_id)
            root_id = root_id_for_doc(doc_id)
        doc = DocxDocument(io.BytesIO(blob))
        return doc, root_id, mode, base_name, blob, flow_row

    def _make_download(self, tenant_id: str, doc_id: str, filename: str, size: int) -> dict:
        ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
        mime = _MIME_BY_TYPE.get(ext) or mimetypes.guess_type(filename)[0] or "application/octet-stream"
        return {
            "doc_id": doc_id,
            "filename": filename,
            "name": filename,
            "mime_type": mime,
            "size": size,
            "url": f"/api/v1/agents/download?id={doc_id}&created_by={tenant_id}",
        }

    def _emit_download(self, tenant_id: str, doc_id: str, filename: str, size: int) -> dict:
        dl = self._make_download(tenant_id, doc_id, filename, size)
        pending = self._canvas.globals.setdefault("sys.pending_downloads", [])
        if isinstance(pending, list):
            pending.append(dl)
        else:
            # 队列被污染为非 list 时不得静默丢弃契约（返回文本承诺的成稿卡会落空）：
            # 记日志并强制重置为仅含本次契约的 list，保住下载卡。
            logger.error(
                "sys.pending_downloads 被污染为非 list（type=%s），已强制重置保住本次成稿卡。",
                type(pending).__name__)
            self._canvas.globals["sys.pending_downloads"] = [dl]
        return dl

    @staticmethod
    def _save_docx_bytes(doc) -> bytes:
        buf = io.BytesIO()
        doc.save(buf)
        return buf.getvalue()

    @staticmethod
    def _to_int(v):
        try:
            return int(str(v).strip())
        except (TypeError, ValueError):
            return None

    def _sections_safe(self, doc):
        """切节；无 heading 文档把 NoSectionError 转成引导文本（建议全文重新生成）。"""
        from rag.svr.document_rewrite.sections import NoSectionError, split_sections

        try:
            return split_sections(doc), None
        except NoSectionError as e:
            return None, str(e)

    # ---------- action: outline ----------

    def _outline(self, kwargs):
        from rag.svr.document_rewrite.sections import build_outline

        _doc, _root, _mode, _base, _blob, _flow = self._doc_for_action(kwargs)
        sections, err = self._sections_safe(_doc)
        if err:
            return err
        return "文档章节目录：\n" + build_outline(sections)

    # ---------- action: replace ----------

    def _replace(self, kwargs):
        """精准替换：find_text 逐字替换为 replace_text，其余内容一字不动。
        零 LLM 重生成（2026-09-25 事故教训：单句替换曾被设计成整章重写，
        定位错节 + 输出截断静默丢内容）。找不到原文零改动并给引导。"""
        from rag.svr.document_rewrite.docx_edit import find_and_replace

        tenant_id = self._canvas.get_tenant_id()
        find_text = str(kwargs.get("find_text") or "").strip()
        replace_text = str(kwargs.get("replace_text") or "").strip()
        if not find_text:
            return "缺少 find_text（要被替换的原文片段，须与文档正文逐字一致）。"
        if not replace_text:
            return "缺少 replace_text（替换后的新文本）。"
        if len(find_text) < 2:
            return "find_text 过短（少于2字），逐字全篇替换极易误伤其他内容，请提供更完整的原文片段。"

        doc, root_id, mode, base_name, src_blob, flow_row = self._doc_for_action(kwargs)
        count, _hits = find_and_replace(doc, find_text, replace_text)
        if count == 0:
            return (
                "文档中未找到与该原文逐字一致的内容，未做任何修改。"
                "常见原因：提供的原文与正文不完全一致（多字/少字/标点差异）。"
                "可先用 action=outline 确认章节并核对原文后重试，"
                "或改用 action=rewrite 按节重写。"
            )

        new_blob = self._save_docx_bytes(doc)
        if mode == "flow":
            row = save_flow_version(flow_row, new_blob, "docx", f"{base_name}.docx", tenant_id)
            obj_id = row["file_path"]
        else:
            ensure_base_version(tenant_id, root_id, src_blob, "docx", base_name)
            row = register_chat_version(
                tenant_id, root_id, new_blob, "docx", base_name,
                source_type="replace",
                instruction=f"「{find_text[:50]}」→「{replace_text[:50]}」",
            )
            obj_id = row["obj"]

        version_no = row["version_no"]
        self._emit_download(
            tenant_id, obj_id, f"{base_name}_v{version_no}.docx", len(new_blob))
        return (
            f"已完成精准替换（共 {count} 处）：「{find_text[:50]}」→「{replace_text[:50]}」。"
            f"新版本 v{version_no} 已生成，请查看新的成稿卡片；文档其余内容原样未动。"
        )

    # ---------- action: rewrite ----------

    def _rewrite(self, kwargs):
        from rag.svr.document_rewrite.docx_edit import replace_section_paragraphs
        from rag.svr.document_rewrite.sections import build_outline, get_section

        tenant_id = self._canvas.get_tenant_id()
        section_no = self._to_int(kwargs.get("section_no"))
        instruction = str(kwargs.get("instruction") or "").strip()
        if section_no is None:
            return "缺少有效的 section_no（节号）。请先用 action=outline 获取章节目录。"
        if not instruction:
            return "缺少 instruction（重写要求）。请说明要把这一节改成什么样。"

        doc, root_id, mode, base_name, src_blob, flow_row = self._doc_for_action(kwargs)
        sections, err = self._sections_safe(doc)
        if err:
            return err
        sec = get_section(sections, section_no)
        if sec is None:
            return (
                f"第{section_no}节不存在。该文档共 {len(sections)} 节：\n"
                + build_outline(sections)
            )

        # 提取该节正文（标题段之后的区间），空行丢弃
        paras = list(doc.paragraphs)
        body_text = "\n".join(
            (p.text or "").strip()
            for p in paras[sec["para_start"] + 1:sec["para_end"] + 1]
            if (p.text or "").strip()
        )
        prev_title = sections[section_no - 2]["title"] if section_no >= 2 else ""
        next_title = sections[section_no]["title"] if section_no < len(sections) else ""

        # 工具在线程池跑、无运行中的 loop，asyncio.run 安全
        new_paras = asyncio.run(rewrite_section(
            tenant_id, instruction, sec["title"], body_text,
            build_outline(sections), prev_title, next_title,
            mdl=self._build_chat_mdl(tenant_id),
        ))

        replace_section_paragraphs(doc, sec, new_paras)
        new_blob = self._save_docx_bytes(doc)
        new_file_name = f"{base_name}.docx"

        if mode == "flow":
            row = save_flow_version(flow_row, new_blob, "docx", new_file_name, tenant_id)
            obj_id = row["file_path"]
        else:
            ensure_base_version(tenant_id, root_id, src_blob, "docx", base_name)
            row = register_chat_version(
                tenant_id, root_id, new_blob, "docx", base_name,
                source_type="rewrite", instruction=instruction,
                section_title=sec["title"],
            )
            obj_id = row["obj"]

        version_no = row["version_no"]
        self._emit_download(
            tenant_id, obj_id, f"{base_name}_v{version_no}.docx", len(new_blob))
        return (
            f"已完成第{section_no}节《{sec['title']}》重写（新版本 v{version_no}）：{instruction}。"
            f"共 {len(new_paras)} 个段落。新版本已生成，请查看新的成稿卡片；"
            f"如不满意可以说「回退到上一版」。"
        )

    # ---------- action: versions ----------

    def _versions(self, kwargs):
        # flow 场景提前引导：不进 _doc_for_action（flow 已删时会抛错而非引导）
        if str(self._read_sys("sys.flow_version_id") or "").strip():
            return "流程文档的版本请到「流程」页签的版本时间线查看。"
        _doc, root_id, mode, _base, _blob, _flow = self._doc_for_action(kwargs)
        rows = list_versions(root_id)
        if not rows:
            return ("该文档还没有重写版本记录（当前成稿即原始版本）。"
                    "重写或回退后会自动生成版本链。")
        lines = [f"共 {len(rows)} 个版本："]
        for r in rows:
            source = r.get("source_type", "")
            tag = "（原始成稿）" if source == "chat_fill" else ("（回退）" if source == "rollback" else "")
            note = f"，改「{r.get('section_title', '')}」" if r.get("section_title") else ""
            ins = r.get("instruction") or ""
            lines.append(
                f"- v{r.get('version_no')}{tag}{note} {r.get('file_name') or ''}"
                + (f"，指令：{ins[:30]}" if ins else ""))
        lines.append("可以说「回退到第N版」恢复历史版本。")
        return "\n".join(lines)

    # ---------- action: rollback ----------

    def _rollback(self, kwargs):
        tenant_id = self._canvas.get_tenant_id()
        version_no = self._to_int(kwargs.get("version_no"))
        if version_no is None:
            return "缺少有效的 version_no。请先用 action=versions 查看版本列表。"
        # flow 场景提前引导：不进 _doc_for_action（flow 已删时会抛错而非引导）
        if str(self._read_sys("sys.flow_version_id") or "").strip():
            return "流程文档版本请到「流程」页签操作回退。"

        _doc, root_id, mode, base_name, _blob, _flow = self._doc_for_action(kwargs)
        hist = get_version(root_id, version_no)
        if hist is None:
            rows = list_versions(root_id)
            chain = "、".join(f"v{r.get('version_no')}" for r in rows) or "（空）"
            return f"版本 v{version_no} 不存在。现有版本：{chain}。"

        from api.db.services.file_service import FileService

        blob = FileService.get_blob(tenant_id, hist["obj"])
        if not blob:
            return f"版本 v{version_no} 的文件已丢失，无法回退。"
        row = register_chat_version(
            tenant_id, root_id, blob, hist.get("file_type", "docx"), base_name,
            source_type="rollback", instruction=f"回退到 v{version_no}",
            section_title=hist.get("section_title", ""),
        )
        new_no = row["version_no"]
        self._emit_download(
            tenant_id, row["obj"], f"{base_name}_v{new_no}.docx", len(blob))
        return (
            f"已回退到 v{version_no} 的内容，登记为新版本 v{new_no}。"
            "请查看新的成稿卡片；原版本仍保留，可再次回退。"
        )
