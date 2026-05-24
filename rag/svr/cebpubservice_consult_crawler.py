#!/usr/bin/env python3
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
"""
Crawler for bulletin.cebpubservice.com — 问题清单 (Q&A / FAQ) section.

Target:
  - List:   https://bulletin.cebpubservice.com/consult.html
  - Detail: https://bulletin.cebpubservice.com/answer/{id}.html

The list page is server-rendered HTML with inline jQuery + Layer.js popups.
Each Q&A item opens an iframe popup pointing to a static HTML answer page.
No pagination — all ~34 items are on one page.
Answer pages contain plain Q&A text (no attachments).

Checkpoint/resume: articles are processed in batches of 10. After each batch,
state is saved and content is uploaded to KB.

Usage:
    python cebpubservice_consult_crawler.py \
        --tenant-id <TENANT_ID> \
        --target-url https://bulletin.cebpubservice.com/ \
        --kb-id <KB_ID> \
        --task-name <NAME>
"""

import argparse
import json
import logging
import os
import random
import re
import sys
import time
from datetime import datetime
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.abspath(os.path.join(_SCRIPT_DIR, "..", ".."))
sys.path.insert(0, _PROJECT_ROOT)

from common import settings
from common.log_utils import init_root_logger
from common.misc_utils import get_uuid
from rag.svr.crawler_utils import PlaywrightHttpClient

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_SITE_ROOT = "https://bulletin.cebpubservice.com"
_LIST_URL = "{}/consult.html".format(_SITE_ROOT)

_SECTION_LABEL = "问题清单"
_SECTION_KEY = "cebpubservice_consult"

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

_HEADERS = {
    "User-Agent": _USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate",
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
}

# Anti-crawling: random delays between requests
_REQUEST_DELAY_MIN = 1.0
_REQUEST_DELAY_MAX = 2.5

# Batch checkpoint
BATCH_SIZE = 10

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _safe_print(msg):
    try:
        print(msg)
    except UnicodeEncodeError:
        print(msg.encode("gbk", errors="replace").decode("gbk"))


def _request_delay():
    """Random delay between requests to avoid rate limiting."""
    time.sleep(random.uniform(_REQUEST_DELAY_MIN, _REQUEST_DELAY_MAX))


def _sanitize_filename(text, max_len=150):
    if not text:
        return "untitled"
    name = re.sub(r'[\\/:*?"<>|]', "_", str(text).strip())
    name = re.sub(r'\s+', "_", name)
    name = name.strip("._ ")
    return (name or "untitled")[:max_len]


def _fetch_html(url, client=None, retries=3):
    """Fetch an HTML page. PlaywrightHttpClient first, requests fallback."""
    for attempt in range(retries):
        try:
            if client:
                resp = client.get(url, headers=_HEADERS, timeout=30)
                if resp.status_code == 200 and len(resp.text) > 100:
                    return resp.text
            else:
                resp = requests.get(url, headers=_HEADERS, timeout=30)
                if resp.status_code == 200 and len(resp.text) > 100:
                    return resp.text
        except Exception as e:
            logging.warning("Fetch %s attempt %d: %s", url, attempt + 1, e)
        if attempt < retries - 1:
            time.sleep((2 ** attempt) + random.uniform(1, 3))
    return None


# ===================================================================
# Listing extraction
# ===================================================================

# Layer.js click handler pattern: $('#{id}').on('click', ...)
# JavaScript in consult.html maps each anchor ID to an answer URL
_ANSWER_JS_RE = re.compile(
    r"\$\(['\"]#([a-z]+?\d+)['\"]\)\.on\(['\"]click['\"]\s*,\s*function\s*\(\)\s*\{"
    r"\s*layer\.open\(\{.*?"
    r"content:\s*['\"]([^'\"]+)['\"]",
    re.DOTALL,
)


def _extract_list_items(html):
    """Extract Q&A items from the consult.html listing page.

    Parses both the HTML structure (sections, anchor IDs, question text) and
    the JavaScript block (Layer.js answer URL mappings).

    Returns list[dict]: {id, question, answer_url, section}
    """
    soup = BeautifulSoup(html, "lxml")

    # First, parse the JS block to map anchor IDs → answer URLs
    id_to_answer = {}
    for m in _ANSWER_JS_RE.finditer(html):
        anchor_id = m.group(1)
        answer_url = m.group(2)
        if answer_url.startswith("http"):
            id_to_answer[anchor_id] = answer_url
        else:
            id_to_answer[anchor_id] = urljoin(_SITE_ROOT, answer_url)

    # Parse HTML: find all section headers and their Q&A items
    items = []
    seen_ids = set()

    # Find all section blocks: mian_consulting_right containers
    for section_div in soup.find_all("div", class_="mian_consulting_right"):
        # Get section title from h3
        h3 = section_div.find("h3")
        section_name = h3.get_text(strip=True) if h3 else "常见问题"

        # Find all anchor tags inside
        for a_tag in section_div.find_all("a", id=True, href=True):
            anchor_id = a_tag.get("id", "").strip()
            if not anchor_id or anchor_id in seen_ids:
                continue
            seen_ids.add(anchor_id)

            # Question text
            question = (a_tag.get_text() or "").strip()
            question = re.sub(r'\s+', ' ', question)

            # Answer URL from JS mapping, or construct from pattern
            answer_url = id_to_answer.get(
                anchor_id,
                "{}/answer/{}.html".format(_SITE_ROOT, anchor_id),
            )

            items.append({
                "id": anchor_id,
                "question": question,
                "answer_url": answer_url,
                "section": section_name,
            })

    return items


# ===================================================================
# Answer extraction
# ===================================================================

def _extract_answer(html):
    """Extract Q&A text from an answer page HTML.

    Answer pages have a simple structure:
        Q: <question text>
        A: <answer text>

    Returns dict: {question, answer}
    """
    soup = BeautifulSoup(html, "lxml")
    for tag in soup.find_all(["script", "style", "noscript"]):
        tag.decompose()

    full_text = soup.get_text(separator="\n", strip=False)

    # Split on Q: and A: markers
    q_match = re.search(r'Q:\s*(.*?)\s*A:', full_text, re.DOTALL)
    if not q_match:
        # Try body text directly
        body = soup.body.get_text(separator="\n", strip=True) if soup.body else full_text
        return {"question": "", "answer": body.strip()}

    question = q_match.group(1).strip()
    answer = full_text[q_match.end():].strip()

    # Clean up
    question = re.sub(r'\n{3,}', '\n\n', question)
    answer = re.sub(r'\n{3,}', '\n\n', answer)
    question = re.sub(r'[ \t]{2,}', ' ', question)
    answer = re.sub(r'[ \t]{2,}', ' ', answer)

    return {"question": question, "answer": answer}


# ===================================================================
# State management
# ===================================================================

_STATE_FILENAME = "_crawler_state.json"


def _load_state(output_dir):
    path = os.path.join(output_dir, _STATE_FILENAME)
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logging.warning("Failed to load crawler state: %s", e)
    return {"processed_ids": [], "completed": False}


def _save_state(output_dir, state):
    path = os.path.join(output_dir, _STATE_FILENAME)
    os.makedirs(output_dir, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False)


def _save_markdown(content, output_dir, item_id):
    d = os.path.join(output_dir, "articles")
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, "{}.md".format(item_id))
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return path


# ===================================================================
# KB upload
# ===================================================================

def _upload_to_kb(filepath, kb_id, tenant_id, parser_id="general"):
    from api.db.services.knowledgebase_service import KnowledgebaseService
    from api.db.services.file_service import FileService
    from api.db.services.document_service import DocumentService

    ok, kb = KnowledgebaseService.get_by_id(kb_id)
    if not ok:
        raise LookupError("Knowledge base {} not found".format(kb_id))

    with open(filepath, "rb") as f:
        blob = f.read()

    class _FO:
        def __init__(self, fn, b):
            self.id = get_uuid()
            self.filename = fn
            self.blob = b
        def read(self):
            return self.blob

    fo = _FO(os.path.basename(filepath), blob)
    errs, pairs = FileService.upload_document(kb, [fo], tenant_id)
    if errs:
        logging.warning("Upload errors: %s", errs)
    for doc, _ in pairs:
        did = doc["id"]
        try:
            DocumentService.update_by_id(did, {"parser_id": parser_id})
        except Exception:
            pass
        try:
            DocumentService.begin2parse(did)
            DocumentService.run(tenant_id, doc, {})
        except Exception as e:
            logging.error("Queue parse for %s: %s", did, e)


# ===================================================================
# CLI
# ===================================================================

def parse_args():
    p = argparse.ArgumentParser(
        description="bulletin.cebpubservice.com consult (Q&A) crawler"
    )
    p.add_argument("--tenant-id", required=True)
    p.add_argument("--target-url",
                   default="https://bulletin.cebpubservice.com/",
                   help="Site root (kept for task_executor compatibility)")
    p.add_argument("--kb-id", required=True)
    p.add_argument("--task-name", required=True)
    p.add_argument("--output-dir", default=None)
    p.add_argument("--full", action="store_true",
                   help="Ignore state, re-crawl all")
    p.add_argument("--max-runtime", type=int, default=3300,
                   help="Max runtime in seconds before graceful stop (default: 3300)")
    for opt in ("--section", "--max-articles", "--max-days", "--hours",
                "--llm-id", "--llm-model", "--access-token"):
        p.add_argument(opt, default=None)
    return p.parse_args()


# ===================================================================
# Main
# ===================================================================

def main():
    args = parse_args()

    _safe_print("\n" + "=" * 60)
    _safe_print("[CEB-CONSULT] bulletin.cebpubservice.com 问题清单 crawler")
    _safe_print("[CEB-CONSULT] KB: {}".format(args.kb_id))
    _safe_print("=" * 60 + "\n")
    sys.stdout.flush()

    settings.init_settings()
    logging.info("=== CEB-CONSULT crawler started ===")

    output_dir = args.output_dir or os.path.join(
        _PROJECT_ROOT, "rag", args.task_name.strip()
    )
    os.makedirs(output_dir, exist_ok=True)
    _safe_print("[CEB-CONSULT] Output: {}\n".format(output_dir))
    sys.stdout.flush()

    # -- State ---------------------------------------------------------------
    state = _load_state(output_dir) if not args.full else {
        "processed_ids": [], "completed": False
    }
    processed_ids = set(state.get("processed_ids", []))
    if state.get("completed") and not args.full:
        _safe_print("[CEB-CONSULT] Already completed, use --full to re-crawl.\n")
        sys.stdout.flush()
        return
    _safe_print("[CEB-CONSULT] Previously processed: {}\n".format(len(processed_ids)))
    sys.stdout.flush()

    # -- Start client --------------------------------------------------------
    pw_client = PlaywrightHttpClient()
    pw_client.start()

    # -- Timeout tracking ----------------------------------------------------
    start_time = time.time()
    max_runtime = args.max_runtime

    try:
        # ===================================================================
        # Step 1: Fetch listing page + extract Q&A items
        # ===================================================================
        _safe_print("[CEB-CONSULT] Step 1/3: Fetching listing page...")
        sys.stdout.flush()

        list_html = _fetch_html(_LIST_URL, client=pw_client)
        if not list_html:
            _safe_print("[CEB-CONSULT] ERROR: Failed to fetch listing page.")
            sys.stdout.flush()
            return

        all_items = _extract_list_items(list_html)
        _safe_print("[CEB-CONSULT]   Found {} Q&A items".format(len(all_items)))
        sys.stdout.flush()

        # Show section breakdown
        section_counts = {}
        for item in all_items:
            sec = item.get("section", "Unknown")
            section_counts[sec] = section_counts.get(sec, 0) + 1
        for sec, count in section_counts.items():
            _safe_print("[CEB-CONSULT]     {}: {} items".format(sec, count))
        sys.stdout.flush()

        if not all_items:
            _safe_print("[CEB-CONSULT] No items found. Check page structure.")
            return

        # Filter already processed
        new_items = [a for a in all_items if a["id"] not in processed_ids]
        _safe_print("[CEB-CONSULT]   {} new (skipped {} already processed)".format(
            len(new_items), len(all_items) - len(new_items)))
        sys.stdout.flush()

        if not new_items:
            _safe_print("[CEB-CONSULT] Nothing new. Marking complete.")
            state["completed"] = True
            _save_state(output_dir, state)
            return

        # ===================================================================
        # Step 2: Fetch answer pages in batches
        # ===================================================================
        _safe_print("[CEB-CONSULT] Step 2/3: Fetching {} answers in batches of {}...".format(
            len(new_items), BATCH_SIZE))
        sys.stdout.flush()

        total = len(new_items)
        success_count = 0
        fail_count = 0
        batch_num = 0
        stopped_early = False

        for batch_start in range(0, total, BATCH_SIZE):
            # ── Time-bounded check ──
            elapsed = time.time() - start_time
            if elapsed > max_runtime - 120:
                _safe_print(
                    "\n[CEB-CONSULT] Runtime {:.0f}s, stopping gracefully "
                    "({} / {} done). Next run will resume.".format(
                        elapsed, success_count, total))
                sys.stdout.flush()
                stopped_early = True
                break

            batch = new_items[batch_start:batch_start + BATCH_SIZE]
            batch_num += 1
            md_parts = []
            batch_ids = []
            batch_files = []

            for idx, item in enumerate(batch, 1):
                global_idx = batch_start + idx
                _safe_print("[CEB-CONSULT]   [{}/{}] {}".format(
                    global_idx, total,
                    item.get("question", item["id"])[:60]))
                sys.stdout.flush()

                # Fetch answer page
                answer_html = _fetch_html(item["answer_url"], client=pw_client)
                if not answer_html:
                    fail_count += 1
                    answer_text = "（获取答案失败）"
                    question_text = item.get("question", "")
                else:
                    result = _extract_answer(answer_html)
                    answer_text = result.get("answer", "")
                    question_text = result.get("question") or item.get("question", "")

                if not answer_text or len(answer_text.strip()) < 5:
                    fail_count += 1
                    answer_text = answer_text or "（无内容）"

                # ---- Build markdown ----
                lines = [
                    "# {}".format(question_text or item.get("id", "无标题")),
                    "**栏目:** {} > {}".format(_SECTION_LABEL, item.get("section", "")),
                    "**URL:** {}".format(item["answer_url"]),
                    "",
                    "## 问题",
                    "",
                    question_text or item.get("question", ""),
                    "",
                    "## 答案",
                    "",
                    answer_text,
                    "",
                ]

                md_content = "\n".join(lines)
                _save_markdown(md_content, output_dir, item["id"])
                md_parts.append(md_content)
                batch_ids.append(item["id"])

                md_path = os.path.join(
                    output_dir, "articles", "{}.md".format(item["id"]))
                batch_files.append((md_path, "general"))

                success_count += 1
                _request_delay()

            # ── Checkpoint ──
            if md_parts:
                batch_path = os.path.join(output_dir,
                    "batch_{:03d}.md".format(batch_num))
                with open(batch_path, "w", encoding="utf-8") as f:
                    f.write("\n\n---\n\n".join(md_parts))

                processed_ids.update(batch_ids)
                state["processed_ids"] = list(processed_ids)
                _save_state(output_dir, state)

                if args.kb_id:
                    try:
                        _upload_to_kb(batch_path, args.kb_id, args.tenant_id)
                        for fp, parser in batch_files:
                            if os.path.exists(fp):
                                _upload_to_kb(fp, args.kb_id, args.tenant_id,
                                             parser_id=parser)
                    except Exception as e:
                        _safe_print("[CEB-CONSULT]   batch {} upload failed: {}".format(
                            batch_num, e))
                        logging.error("Upload batch %d: %s", batch_num, e)

                _safe_print("[CEB-CONSULT]   batch {} uploaded ({}/{} done)\n".format(
                    batch_num, success_count, total))
                sys.stdout.flush()

    finally:
        pw_client.stop()

    # -- Mark complete if all done -------------------------------------------
    if not stopped_early:
        state["completed"] = True
        _save_state(output_dir, state)

    _safe_print("\n" + "=" * 60)
    _safe_print("[CEB-CONSULT] Done: {} answers ({} no-detail)".format(
        success_count, fail_count))
    _safe_print("[CEB-CONSULT] Total found on listing: {}".format(len(all_items)))
    if stopped_early:
        _safe_print("[CEB-CONSULT] Stopped early (timeout), resume next run.")
    _safe_print("=" * 60 + "\n")
    sys.stdout.flush()
    logging.info("=== CEB-CONSULT crawler finished: %d items ===", success_count)


if __name__ == "__main__":
    CONSUMER_NAME = "cebpubservice_consult_crawler"
    init_root_logger(CONSUMER_NAME)
    main()
