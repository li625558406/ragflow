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
"""Browser extension / Bookmarklet web-clip API.

Endpoints:
    GET  /kb_list             — list current user's knowledge bases
    POST /kb/{kb_id}/clip     — upload clipped web content to a KB
"""
import io
import logging
import os
import re
import zipfile

from quart import Blueprint, request, send_file

from api.apps import current_user, login_required
from api.db.services.knowledgebase_service import KnowledgebaseService
from api.utils.api_utils import get_data_error_result, get_json_result
from common.constants import RetCode

manager = Blueprint("rest_kb_clip_app", __name__)


@manager.route("/kb_list", methods=["GET"])
@login_required
async def kb_list():
    """List knowledge bases for the current user.

    Returns compact list suitable for a browser extension popup:
    [{kb_id, name, parser_id, chunk_num}]
    """
    try:
        tenant_id = current_user.id
        kbs = KnowledgebaseService.model.select().where(
            KnowledgebaseService.model.tenant_id == tenant_id
        )
        items = [
            {
                "kb_id": kb.id,
                "name": kb.name,
                "parser_id": getattr(kb, "parser_id", "naive"),
                "chunk_num": getattr(kb, "chunk_num", 0),
            }
            for kb in kbs
        ]
        return get_json_result(data=items)
    except Exception as e:
        logging.exception("kb_list failed")
        return get_data_error_result(message=str(e), code=RetCode.EXCEPTION_ERROR)


@manager.route("/kb/<kb_id>/clip", methods=["POST"])
@login_required
async def kb_clip(kb_id):
    """Upload clipped web content to a knowledge base.

    Body (JSON):
        title      — page title
        url        — source URL (optional)
        html       — raw HTML (used when content is empty)
        content    — pre-processed text/markdown (optional, takes priority)
        parse_mode — "auto" | "llm" | "naive"  (default: "naive")

    Reuses KBUploader from the crawler engine for upload + parse queueing.
    """
    try:
        data = await request.get_json(silent=True) or {}
    except Exception:
        data = {}

    title = (data.get("title") or "").strip()
    url = (data.get("url") or "").strip()
    html = (data.get("html") or "").strip()
    content = (data.get("content") or "").strip()
    parse_mode = data.get("parse_mode", "naive")

    if not title:
        return get_data_error_result(message="title is required")

    if not content and not html:
        return get_data_error_result(message="content or html is required")

    tenant_id = current_user.id

    # Resolve KB and verify ownership
    ok, kb = KnowledgebaseService.get_by_id(kb_id)
    if not ok:
        return get_data_error_result(message=f"KB {kb_id} not found")

    if getattr(kb, "tenant_id", None) != tenant_id:
        return get_data_error_result(message="Access denied", code=RetCode.FORBIDDEN)

    # Resolve raw input: prefer pre-processed content, fallback to HTML
    raw_text = content or html
    if not raw_text:
        return get_data_error_result(message="content is empty after processing")

    if parse_mode == "llm":
        # LLM mode: pass the richest source available (raw HTML > cleaned text)
        llm_input = html or content
        content = await _llm_structured_parse(llm_input, title, url)
    elif not content:
        # Naive mode: strip HTML to plain text
        content = _html_to_text(html)

    if not content:
        return get_data_error_result(message="content is empty after processing")

    # Build a filename with timestamp to avoid duplicate names
    from datetime import datetime

    safe_title = _safe_filename(title, max_len=80)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    display_name = f"{safe_title}_{timestamp}.md"

    if url:
        content = f"> 来源: {url}\n\n{content}"

    # Upload via KBUploader (reuses crawler engine pattern)
    from rag.svr.crawler_engine.kb_uploader import KBUploader

    parser_id = getattr(kb, "parser_id", "naive") or "naive"
    uploader = KBUploader(kb_id=kb_id, tenant_id=tenant_id, parser_id=parser_id)
    doc_id = uploader.upload_content(content, display_name=display_name)

    if not doc_id:
        return get_data_error_result(message="Upload failed")

    return get_json_result(data={"doc_id": doc_id, "status": "parsing"})


@manager.route("/extension/download", methods=["GET"])
async def extension_download():
    """Download the browser extension as a zip file.

    Zips the browser-extension/ directory at the repo root on-the-fly
    and returns it as a downloadable attachment.
    """
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    # Try api/browser-extension first (server bind-mount), fall back to repo root (local dev)
    ext_dir = os.path.join(root, "browser-extension")
    if not os.path.isdir(ext_dir):
        ext_dir = os.path.join(root, "..", "browser-extension")
        ext_dir = os.path.abspath(ext_dir)

    if not os.path.isdir(ext_dir):
        return get_data_error_result(message="Extension directory not found")

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for dirpath, dirnames, filenames in os.walk(ext_dir):
            for fname in filenames:
                full = os.path.join(dirpath, fname)
                arcname = os.path.relpath(full, ext_dir)
                zf.write(full, arcname)
    buf.seek(0)

    return await send_file(
        buf,
        mimetype="application/zip",
        as_attachment=True,
        attachment_filename="ragflow-web-clipper.zip",
    )


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _safe_filename(text: str, max_len: int = 80) -> str:
    """Turn arbitrary text into a safe filename fragment."""
    s = text.strip()
    s = re.sub(r"[\\/:*?\"<>|]", "_", s)
    s = re.sub(r"\s+", "_", s)
    return s[:max_len]


def _html_to_text(html: str) -> str:
    """Strip HTML tags and return clean text."""
    # Remove script/style blocks
    text = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL | re.IGNORECASE)
    # Replace block-level tags with newlines
    text = re.sub(r"</?(?:div|p|h[1-6]|li|tr|br|hr|table|section|article|header|footer)[^>]*>", "\n", text, flags=re.IGNORECASE)
    # Strip remaining tags
    text = re.sub(r"<[^>]+>", "", text)
    # Decode common entities
    text = text.replace("&nbsp;", " ").replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", "\"")
    # Collapse whitespace
    text = re.sub(r"[\t ]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r" +\n", "\n", text)
    return text.strip()


async def _llm_structured_parse(raw_text: str, title: str, url: str = "") -> str:
    """Use LLM to extract structured markdown from messy web content."""
    try:
        from rag.prompts.generator import load_prompt

        prompt = load_prompt("web_clip_parser")
        prompt = prompt.replace("{{title}}", title).replace("{{url}}", url)

        # Truncate to avoid token overflow (keep ~6K chars for input)
        truncated = raw_text[:6144] if len(raw_text) > 6144 else raw_text

        from api.db.joint_services.tenant_model_service import get_tenant_default_model_by_type
        from api.db.services.llm_service import LLMBundle
        from common.constants import LLMType

        chat_model_config = get_tenant_default_model_by_type(current_user.id, LLMType.CHAT)
        chat_mdl = LLMBundle(current_user.id, chat_model_config)
        ans = await chat_mdl.async_chat(
            system=prompt,
            history=[{"role": "user", "content": truncated}],
        )
        if ans and ans.strip():
            return ans.strip()
    except Exception:
        logging.exception("LLM structured parse failed, falling back to raw text")
    return raw_text
