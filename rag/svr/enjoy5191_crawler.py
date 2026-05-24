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
Crawler for www.enjoy5191.com — 招标范本 (bidding template) section.

Site is a Vue.js SPA with AES-encrypted API responses.  All content extraction
is done via Playwright DOM rendering (API responses cannot be used directly).

Target section:
  - 招标范本  (typeid=6C4073D7-8C93-4FAC-9E3D-937D975A5BC0)

Checkpoint/resume: articles are processed in batches of 10.  After each batch
markdown is saved to KB and state is persisted.  The 3600s task timeout is
handled via --max-runtime (default 3300s).

Usage:
    python enjoy5191_crawler.py \
        --tenant-id <TENANT_ID> \
        --target-url https://www.enjoy5191.com/ \
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
import zipfile
from datetime import datetime, timedelta

import requests
from bs4 import BeautifulSoup

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.abspath(os.path.join(_SCRIPT_DIR, "..", ".."))
sys.path.insert(0, _PROJECT_ROOT)

from common import settings
from common.log_utils import init_root_logger
from common.misc_utils import get_uuid

try:
    from playwright.sync_api import sync_playwright

    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_SITE_ROOT = "https://www.enjoy5191.com"

_LIST_URL_TEMPLATE = (
    "{site}/views/public/news.html"
    "?pid=067CF385-AD64-43FD-AFC4-D1D3FB41D726"
    "&typeid=6C4073D7-8C93-4FAC-9E3D-937D975A5BC0"
)

_DETAIL_URL_TEMPLATE = (
    "{site}/views/public/article.html"
    "?id={id}"
    "&typeid=6C4073D7-8C93-4FAC-9E3D-937D975A5BC0"
    "&pid=067CF385-AD64-43FD-AFC4-D1D3FB41D726"
)

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

_CHROME_PATHS = [
    "C:/Program Files/Google/Chrome/Application/chrome.exe",
    "C:/Program Files (x86)/Google/Chrome/Application/chrome.exe",
    "/opt/chrome/chrome",
    "/usr/bin/google-chrome",
    "/usr/bin/chromium-browser",
    "/usr/bin/chromium",
]

# Anti-crawling: random delays
_REQUEST_DELAY_MIN = 1.0
_REQUEST_DELAY_MAX = 2.5

_BATCH_SIZE = 10

# Attachment download extensions
_ATTACHMENT_EXTENSIONS = (
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".zip", ".rar",
    ".ppt", ".pptx", ".txt", ".csv", ".jpg", ".png",
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _safe_print(msg):
    try:
        print(msg)
    except UnicodeEncodeError:
        print(msg.encode("gbk", errors="replace").decode("gbk"))


def _find_chrome():
    for p in _CHROME_PATHS:
        if os.path.exists(p):
            return p
    return None


def _request_delay():
    time.sleep(random.uniform(_REQUEST_DELAY_MIN, _REQUEST_DELAY_MAX))


# ---------------------------------------------------------------------------
# Playwright: listing page
# ---------------------------------------------------------------------------

def _extract_list_items(page):
    """Extract article items from the rendered listing page DOM.

    Returns list[dict]: {id, title, date_str, url}.
    """
    items = page.evaluate("""() => {
        const results = [];
        const seen = new Set();
        const links = document.querySelectorAll('a[href*="article.html"]');
        for (const a of links) {
            const href = a.href || '';
            if (!href || seen.has(href)) continue;
            seen.add(href);

            const text = (a.textContent || '').trim();
            if (text.length < 2) continue;

            let artId = '';
            try {
                artId = new URL(href).searchParams.get('id') || '';
            } catch(e) {}

            // Find date in parent hierarchy
            let dateStr = '';
            let parent = a.closest('li');
            if (!parent) {
                // Walk up to find date in nearby elements
                parent = a.parentElement;
                for (let i = 0; i < 5 && parent; i++) {
                    const spans = parent.querySelectorAll('span');
                    for (const s of spans) {
                        const t = s.textContent.trim();
                        if (t.match(/\\d{4}[-/]\\d{1,2}[-/]\\d{1,2}/)) {
                            dateStr = t;
                            break;
                        }
                    }
                    if (dateStr) break;
                    parent = parent.parentElement;
                }
            } else {
                const timeEls = parent.querySelectorAll('span, time, label');
                for (const el of timeEls) {
                    const t = el.textContent.trim();
                    if (t.match(/\\d{4}[-/]\\d{1,2}[-/]\\d{1,2}/)) {
                        dateStr = t;
                        break;
                    }
                }
            }

            results.push({
                id: artId,
                title: text,
                date_str: dateStr,
                url: href
            });
        }
        return results;
    }""")
    return items or []


def _click_next_page(page):
    """Click the '下一页' link; return True if successful, False if on last page."""
    try:
        next_links = page.query_selector_all("a.pageNum")
        for link in next_links:
            text = (link.text_content() or "").strip()
            if text == "下一页":
                cls = link.get_attribute("class") or ""
                if "disable" in cls:
                    return False
                link.click()
                page.wait_for_timeout(2000)
                return True
        return False
    except Exception:
        return False


def _get_total_pages(page):
    """Extract total page count from pagination info."""
    try:
        info_el = page.query_selector("span.pageInfo")
        if info_el:
            text = (info_el.text_content() or "").strip()
            m = re.search(r"共(\d+)页", text)
            if m:
                return int(m.group(1))
    except Exception:
        pass
    return 1


def _crawl_listing(context):
    """Crawl the listing page (all pages via sequential 'next' clicks)
    → list[dict] of articles {id, title, date_str, url}.
    """
    all_articles = []
    seen_ids = set()

    page = context.new_page()
    page.set_default_timeout(60000)

    try:
        list_url = _LIST_URL_TEMPLATE.format(site=_SITE_ROOT)
        _safe_print("[ENJOY] Loading listing page ...")
        sys.stdout.flush()
        page.goto(list_url, wait_until="load", timeout=60000)
        page.wait_for_timeout(3000)

        total_pages = _get_total_pages(page)
        _safe_print("[ENJOY] Total pages: {}".format(total_pages))
        sys.stdout.flush()

        for pg in range(1, total_pages + 1):
            items = _extract_list_items(page)
            count_before = len(all_articles)
            for item in items:
                art_id = item.get("id", "")
                if not art_id or art_id in seen_ids:
                    continue
                seen_ids.add(art_id)
                all_articles.append(item)

            new_on_page = len(all_articles) - count_before
            _safe_print("[ENJOY] Page {:d}: {:d} articles (total: {:d})".format(
                pg, new_on_page, len(all_articles)))
            sys.stdout.flush()

            if pg >= total_pages:
                break
            if not _click_next_page(page):
                _safe_print("[ENJOY]   (no more pages)")
                break

            _request_delay()

    except Exception as e:
        logging.error("Listing crawl failed: %s", e)
    finally:
        try:
            page.close()
        except Exception:
            pass

    return all_articles


# ---------------------------------------------------------------------------
# Playwright: detail page
# ---------------------------------------------------------------------------

def _extract_detail_from_page(page):
    """Extract article content + attachment info from a rendered detail page.

    Returns dict: {title, date_str, source, content_html, attachments}.
      attachments: list[{name, url}]
    """
    result = page.evaluate("""() => {
        const res = {
            title: '',
            date_str: '',
            source: '',
            content_html: '',
            attachments: []
        };

        // Title — extract from the main heading
        const h1 = document.querySelector('h1');
        if (!h1) {
            // Try finding title in body text before '发布时间'
            const bodyText = document.body.innerText || '';
            const m = bodyText.match(/^[\\u4e00-\\u9fa5].+?(?=\\n发布时间)/m);
            if (m) res.title = m[0].trim().substring(0, 200);
        } else {
            res.title = h1.textContent.trim();
        }

        // Date and source from '发布时间：... 来源：...'
        const bodyText2 = document.body.innerText || '';
        const timeM = bodyText2.match(/发布时间[：:]\\s*(\\S+)/);
        if (timeM) res.date_str = timeM[1];
        const srcM = bodyText2.match(/来源[：:]\\s*(\\S+)/);
        if (srcM) res.source = srcM[1];

        // Content: try to find the main article body
        // Strategy: get text between "来源：" line and "附件：" line
        let contentStart = -1, contentEnd = -1;
        const lines = bodyText2.split('\\n');
        for (let i = 0; i < lines.length; i++) {
            const line = lines[i].trim();
            if (line.startsWith('来源') && contentStart < 0) {
                contentStart = i + 1;
            }
            if (line.startsWith('附件') && contentEnd < 0 && contentStart >= 0) {
                contentEnd = i;
                break;
            }
            // Also stop at footer
            if (line.includes('Copyright') || line.includes('闽ICP备')) {
                if (contentEnd < 0 && contentStart >= 0) contentEnd = i;
                break;
            }
        }
        if (contentStart >= 0) {
            if (contentEnd < 0) contentEnd = lines.length;
            const contentLines = lines.slice(contentStart, contentEnd)
                .filter(l => l.trim())
                .join('\\n');
            res.content_html = contentLines;
        }

        // If we couldn't parse structured content, fallback to all body text minus chrome
        if (!res.content_html || res.content_html.length < 50) {
            // Remove header/footer noise
            const skipPatterns = ['搜索', '首页', '招标信息', '云竞网', '场地预约',
                '专家管理', '客户指南', '关于我们', '当前位置', '平台首页',
                '行业动态', '招标范本', '新闻动态', '政策法规',
                'Copyright', '闽ICP备', '闽公网安备', '随行软件',
                '登录/注册', 'CA办理', '咨询热线', '在线客服', '公众号',
                '中国电子招标投标', '三星检测认证平台'];
            const allLines = bodyText2.split('\\n');
            const filtered = [];
            for (const l of allLines) {
                const t = l.trim();
                if (!t) continue;
                let skip = false;
                for (const p of skipPatterns) {
                    if (t.startsWith(p)) { skip = true; break; }
                }
                if (!skip) filtered.push(t);
            }
            res.content_html = filtered.join('\\n');
        }

        // Attachments: find links that look like file downloads
        const allLinks = document.querySelectorAll('a[href]');
        for (const a of allLinks) {
            const href = a.href || '';
            const text = (a.textContent || '').trim();
            if (!href || href.startsWith('javascript')) continue;

            const isAttachment = (
                /\.(pdf|doc|docx|xls|xlsx|zip|rar|ppt|pptx|txt|png|jpg)(\\?|$)/i.test(href) ||
                href.includes('download') ||
                href.includes('UploadFile') ||
                href.includes('upload') ||
                text.includes('附件')
            );
            if (!isAttachment) continue;

            res.attachments.push({name: text.substring(0, 200), url: href});
        }

        return res;
    }""")
    return result


def _fetch_detail(context, article):
    """Load a detail page and extract content; with retry+backoff.

    Returns dict: {title, date_str, source, content_text, attachments}.
    """
    detail_url = _DETAIL_URL_TEMPLATE.format(site=_SITE_ROOT, id=article["id"])

    for attempt in range(3):
        detail_page = context.new_page()
        detail_page.set_default_timeout(60000)
        try:
            detail_page.goto(detail_url, wait_until="load", timeout=60000)
            detail_page.wait_for_timeout(2000)

            detail = _extract_detail_from_page(detail_page)
            content = detail.get("content_html", "")
            if content and len(content) > 20:
                return detail

            if attempt < 2:
                logging.warning("Detail empty for %s, retry %d/3...",
                                article["id"], attempt + 2)
                _request_delay()
        except Exception as e:
            logging.warning("Detail fetch failed for %s (attempt %d/3): %s",
                            article["id"], attempt + 1, e)
            if attempt < 2:
                time.sleep((2 ** attempt) + random.uniform(1, 3))
        finally:
            try:
                detail_page.close()
            except Exception:
                pass
    return {"title": "", "date_str": "", "source": "", "content_html": "",
            "attachments": []}


# ---------------------------------------------------------------------------
# Attachment download & decompression
# ---------------------------------------------------------------------------

def _download_attachment(url, output_dir):
    """Download an attachment file, return (file_bytes, filename) or (None, None)."""
    headers = {"User-Agent": _USER_AGENT}
    try:
        r = requests.get(url, headers=headers, timeout=60, stream=True)
        r.raise_for_status()
        content = r.content

        # Extract filename from URL or Content-Disposition
        filename = ""
        cd = r.headers.get("Content-Disposition", "")
        m = re.search(r'filename[^;=\n]*=((["\']).*?\2|[^;\n]*)', cd)
        if m:
            filename = m.group(1).strip('"\'')
        if not filename:
            filename = os.path.basename(url.split("?")[0])
        if not filename:
            filename = "attachment"
        # Sanitize
        filename = re.sub(r'[<>:"/\\|?*]', '_', filename)

        return content, filename
    except Exception as e:
        logging.warning("Attachment download failed for %s: %s", url, e)
        return None, None


def _process_attachment_bytes(file_bytes, filename, output_dir):
    """Process attachment bytes.  If ZIP, decompress and return list of
    (name, content_bytes).  Otherwise return [(filename, file_bytes)].

    For non-binary files (txt etc), content remains as bytes.
    """
    if filename.lower().endswith(".zip"):
        files = []
        try:
            import io
            z = zipfile.ZipFile(io.BytesIO(file_bytes))
            for info in z.infolist():
                if info.is_dir():
                    continue
                inner_name = os.path.basename(info.filename)
                if not inner_name:
                    continue
                inner_bytes = z.read(info)
                files.append((inner_name, inner_bytes))
            z.close()
            logging.info("ZIP '%s' extracted: %d files", filename, len(files))
            return files
        except (zipfile.BadZipFile, Exception) as e:
            logging.warning("ZIP extraction failed for '%s': %s", filename, e)
            return [(filename, file_bytes)]
    else:
        return [(filename, file_bytes)]


def _save_attachment_file(content_bytes, filename, output_dir):
    """Save attachment bytes to disk, return filepath."""
    downloads_dir = os.path.join(output_dir, "downloads")
    os.makedirs(downloads_dir, exist_ok=True)
    path = os.path.join(downloads_dir, filename)
    # Avoid overwrites
    base, ext = os.path.splitext(filename)
    counter = 1
    while os.path.exists(path):
        path = os.path.join(downloads_dir, "{}_{:d}{}".format(base, counter, ext))
        counter += 1
    with open(path, "wb") as f:
        f.write(content_bytes)
    return path


# ---------------------------------------------------------------------------
# HTML → Markdown
# ---------------------------------------------------------------------------

def _html_to_markdown(html_text):
    """Convert HTML or plain text to clean markdown."""
    if not html_text or not html_text.strip():
        return ""

    # If it's already plain text (no HTML tags), return as-is
    if "<" not in html_text and ">" not in html_text:
        # Clean up excessive whitespace
        lines = [l.strip() for l in html_text.split("\n") if l.strip()]
        return "\n\n".join(lines)

    soup = BeautifulSoup(html_text, "lxml")
    for tag in soup.find_all(["script", "style", "noscript"]):
        tag.decompose()

    lines = []
    seen = set()
    for el in soup.find_all(["p", "h1", "h2", "h3", "h4", "h5", "h6",
                             "li", "blockquote", "pre", "div", "span"]):
        text = el.get_text(strip=True)
        if not text or text in seen:
            continue
        seen.add(text)
        tn = el.name
        if tn == "h1":
            lines.append("\n# {}\n".format(text))
        elif tn == "h2":
            lines.append("\n## {}\n".format(text))
        elif tn in ("h3", "h4", "h5", "h6"):
            lines.append("\n**{}**\n".format(text))
        elif tn == "blockquote":
            lines.append("> {}".format(text))
        elif tn == "li":
            lines.append("- {}".format(text))
        elif tn == "pre":
            lines.append("```\n{}\n```".format(text))
        else:
            lines.append(text)

    result = "\n\n".join(lines)
    result = re.sub(r"\n{3,}", "\n\n", result)
    return result


# ---------------------------------------------------------------------------
# Persistence & state
# ---------------------------------------------------------------------------

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
    logging.info("State saved (%d IDs, completed=%s)",
                 len(state.get("processed_ids", [])), state.get("completed"))


def _save_article_markdown(content, output_dir, article_id):
    articles_dir = os.path.join(output_dir, "articles")
    os.makedirs(articles_dir, exist_ok=True)
    path = os.path.join(articles_dir, "{}.md".format(article_id))
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return path


# ---------------------------------------------------------------------------
# KB upload
# ---------------------------------------------------------------------------

def _upload_to_kb(filepath, kb_id, tenant_id, parser_id="laws"):
    from api.db.services.knowledgebase_service import KnowledgebaseService
    from api.db.services.file_service import FileService
    from api.db.services.document_service import DocumentService

    ok, kb = KnowledgebaseService.get_by_id(kb_id)
    if not ok:
        raise LookupError("Knowledge base {} not found".format(kb_id))

    with open(filepath, "rb") as f:
        blob = f.read()

    class _FileObj:
        def __init__(self, fn, b):
            self.id = get_uuid()
            self.filename = fn
            self.blob = b

        def read(self):
            return self.blob

    fo = _FileObj(os.path.basename(filepath), blob)
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


# ---------------------------------------------------------------------------
# Batch processing
# ---------------------------------------------------------------------------

def _process_batch(context, output_dir, kb_id, tenant_id,
                   batch_articles, processed_ids, state,
                   batch_num, total_new, max_runtime, crawl_start):
    """Process one batch of articles: fetch details → build markdown →
    download attachments → save → upload → checkpoint.

    Returns: (articles_processed, should_stop_early).
    """
    md_parts = []
    batch_ids = []
    total_attachments = 0
    fail_count = 0

    for idx, art in enumerate(batch_articles, 1):
        # ── Time-bounded check ──
        elapsed = time.time() - crawl_start
        remaining = max_runtime - elapsed
        if remaining < 120:
            _safe_print("\n[ENJOY] Runtime {:.0f}s approaching limit ({}s), "
                       "stopping early".format(elapsed, max_runtime))
            sys.stdout.flush()
            return len(batch_ids), True

        global_idx = (batch_num - 1) * _BATCH_SIZE + idx
        _safe_print("[ENJOY] [{}/{}] {}".format(
            global_idx, total_new, art["title"][:70]))
        sys.stdout.flush()

        # Fetch detail
        detail = _fetch_detail(context, art)

        content_text = detail.get("content_html", "")
        if not content_text:
            fail_count += 1
            content_text = "标题: {}\n日期: {}".format(
                art["title"], art.get("date_str", ""))

        content_md = _html_to_markdown(content_text)

        # Build article markdown
        title = detail.get("title") or art["title"]
        date_str = detail.get("date_str") or art.get("date_str", "")
        source = detail.get("source", "")
        detail_url = _DETAIL_URL_TEMPLATE.format(site=_SITE_ROOT, id=art["id"])

        md_lines = [
            "# {}".format(title),
            "**栏目:** 招标范本",
            "**日期:** {}".format(date_str),
            "**来源:** {}".format(source),
            "**URL:** {}".format(detail_url),
            "",
            content_md,
            "",
        ]

        # ── Attachment processing ──
        attachments = detail.get("attachments", [])
        if attachments:
            md_lines.append("---")
            md_lines.append("## 附件列表")
            md_lines.append("")

            for att_idx, att in enumerate(attachments, 1):
                att_url = att["url"]
                att_name = att.get("name", "附件{}".format(att_idx))

                md_lines.append("### {}. {}".format(att_idx, att_name))
                md_lines.append("**下载链接:** {}".format(att_url))
                md_lines.append("")

                _safe_print("[ENJOY]     Downloading: {}".format(att_name[:60]))
                sys.stdout.flush()

                file_bytes, filename = _download_attachment(att_url, output_dir)
                if file_bytes:
                    processed_files = _process_attachment_bytes(
                        file_bytes, filename, output_dir
                    )
                    for pf_name, pf_bytes in processed_files:
                        saved_path = _save_attachment_file(pf_bytes, pf_name, output_dir)
                        md_lines.append("**附件文件:** {}".format(pf_name))
                        md_lines.append("")

                        # Upload each file to KB (linked to parent article)
                        try:
                            _upload_to_kb(saved_path, kb_id, tenant_id,
                                         parser_id="naive")
                            total_attachments += 1
                        except Exception as e:
                            logging.error("Upload attachment '%s': %s", pf_name, e)
                else:
                    md_lines.append("*(下载失败)*")
                    md_lines.append("")

        md_part = "\n".join(md_lines)
        md_parts.append(md_part)
        batch_ids.append(art["id"])

        # Save per-article markdown for crash safety
        _save_article_markdown(md_part, output_dir, art["id"])

        _request_delay()

    # ── Save batch markdown & update state ──
    if md_parts:
        batch_path = os.path.join(
            output_dir, "batch_{:03d}.md".format(batch_num))
        with open(batch_path, "w", encoding="utf-8") as f:
            f.write("\n\n---\n\n".join(md_parts))

        processed_ids.update(batch_ids)
        state["processed_ids"] = list(processed_ids)
        _save_state(output_dir, state)

        if kb_id:
            try:
                _upload_to_kb(batch_path, kb_id, tenant_id,
                             parser_id="laws")
            except Exception as e:
                _safe_print("[ENJOY] Batch {} upload failed: {}".format(batch_num, e))
                logging.error("Upload failed for batch %d: %s", batch_num, e)

        _safe_print("[ENJOY] Batch {} uploaded: {} articles, {} attachments "
                   "(fail: {})".format(batch_num, len(md_parts),
                                       total_attachments, fail_count))
        sys.stdout.flush()

    return len(batch_ids), False


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args():
    p = argparse.ArgumentParser(
        description="enjoy5191.com 招标范本 crawler"
    )
    p.add_argument("--tenant-id", required=True)
    p.add_argument("--target-url",
                   default="https://www.enjoy5191.com/",
                   help="Site URL (kept for task_executor compatibility)")
    p.add_argument("--kb-id", required=True)
    p.add_argument("--task-name", required=True)
    p.add_argument("--output-dir", default=None)
    p.add_argument("--full", action="store_true",
                   help="Ignore state, re-crawl all")
    p.add_argument("--max-runtime", type=int, default=3300,
                   help="Max runtime in seconds before graceful stop "
                        "(default: 3300 = 55 min)")
    p.add_argument("--max-articles", type=int, default=0,
                   help="Max articles to fetch (0 = unlimited)")
    for opt in ("--section", "--max-days", "--hours",
                "--llm-id", "--llm-model", "--access-token"):
        p.add_argument(opt, default=None)
    return p.parse_args()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    args = parse_args()

    _safe_print("\n" + "=" * 60)
    _safe_print("[ENJOY] enjoy5191.com 招标范本 crawler")
    _safe_print("[ENJOY] KB: {}".format(args.kb_id))
    _safe_print("[ENJOY] Max runtime: {}s".format(args.max_runtime))
    if args.max_articles:
        _safe_print("[ENJOY] Max articles: {}".format(args.max_articles))
    _safe_print("=" * 60 + "\n")
    sys.stdout.flush()

    settings.init_settings()
    logging.info("=== Enjoy5191 crawler started ===")

    if not PLAYWRIGHT_AVAILABLE:
        _safe_print("[ENJOY] ERROR: playwright not installed.")
        sys.stdout.flush()
        sys.exit(1)

    output_dir = args.output_dir or os.path.join(
        _PROJECT_ROOT, "rag", args.task_name.strip()
    )
    os.makedirs(output_dir, exist_ok=True)
    _safe_print("[ENJOY] Output: {}\n".format(output_dir))
    sys.stdout.flush()

    # ── State ──
    if args.full:
        state = {"processed_ids": [], "completed": False}
    else:
        state = _load_state(output_dir)
    processed_ids = set(state.get("processed_ids", []))
    completed = state.get("completed", False)
    _safe_print("[ENJOY] Previously processed: {} IDs, completed={}\n".format(
        len(processed_ids), completed))
    sys.stdout.flush()

    if completed:
        _safe_print("[ENJOY] Already completed, nothing to do.\n")
        sys.stdout.flush()
        return

    crawl_start = time.time()

    with sync_playwright() as pw:
        chrome_path = _find_chrome()
        launch_opts = {
            "headless": True,
            "args": ["--disable-blink-features=AutomationControlled",
                     "--no-sandbox"],
        }
        if chrome_path:
            launch_opts["executable_path"] = chrome_path

        # Clean stale Chrome profiles to avoid launch timeout
        import glob as _glob
        import shutil as _shutil
        for _d in _glob.glob("/tmp/playwright_chromiumdev_profile-*"):
            try:
                _shutil.rmtree(_d)
            except Exception:
                pass
        for _d in _glob.glob("/tmp/.org.chromium.*"):
            try:
                _shutil.rmtree(_d)
            except Exception:
                pass

        try:
            browser = pw.chromium.launch(timeout=60000, **launch_opts)
        except Exception as e:
            _safe_print("[ENJOY] Browser launch failed: {}, retrying...".format(e))
            sys.stdout.flush()
            time.sleep(3)
            browser = pw.chromium.launch(timeout=60000, **launch_opts)

        context = browser.new_context(
            user_agent=_USER_AGENT,
            viewport={"width": 1920, "height": 1080},
            locale="zh-CN",
        )

        # ── Step 1: Crawl listing ──
        _safe_print("[ENJOY] === Step 1: Crawling listing pages ===\n")
        sys.stdout.flush()
        all_articles = _crawl_listing(context)
        _safe_print("[ENJOY] Total articles from listing: {}\n".format(
            len(all_articles)))
        sys.stdout.flush()

        if not all_articles:
            _safe_print("[ENJOY] No articles found.\n")
            browser.close()
            return

        # Deduplicate
        new_articles = [a for a in all_articles if a["id"] not in processed_ids]
        _safe_print("[ENJOY] {} new ({} skipped already processed)\n".format(
            len(new_articles), len(all_articles) - len(new_articles)))
        sys.stdout.flush()

        if not new_articles:
            _safe_print("[ENJOY] All articles already processed.\n")
            # Mark completed
            state["completed"] = True
            _save_state(output_dir, state)
            browser.close()
            return

        if args.max_articles and args.max_articles > 0:
            new_articles = new_articles[:args.max_articles]
            _safe_print("[ENJOY] Limited to {} articles by --max-articles\n".format(
                len(new_articles)))
            sys.stdout.flush()

        # ── Step 2: Batch processing ──
        _safe_print("[ENJOY] === Step 2: Processing {} articles in batches "
                   "of {} ===\n".format(len(new_articles), _BATCH_SIZE))
        sys.stdout.flush()

        total_processed = 0
        stopped_early = False

        for batch_start in range(0, len(new_articles), _BATCH_SIZE):
            # Time check before batch
            elapsed = time.time() - crawl_start
            remaining = args.max_runtime - elapsed
            if remaining < 120:
                _safe_print("\n[ENJOY] Runtime {:.0f}s approaching limit, "
                           "stopping early.".format(elapsed))
                sys.stdout.flush()
                stopped_early = True
                break

            batch = new_articles[batch_start:batch_start + _BATCH_SIZE]
            batch_num = batch_start // _BATCH_SIZE + 1
            _safe_print("[ENJOY] --- Batch {} ({:d} articles) ---".format(
                batch_num, len(batch)))
            sys.stdout.flush()

            n, early = _process_batch(
                context, output_dir, args.kb_id, args.tenant_id,
                batch, processed_ids, state, batch_num,
                len(new_articles), args.max_runtime, crawl_start,
            )
            total_processed += n
            if early:
                stopped_early = True
                break

        # ── Done ──
        if not stopped_early:
            state["completed"] = True
            _save_state(output_dir, state)

        browser.close()

    _safe_print("\n" + "=" * 60)
    if stopped_early:
        _safe_print("[ENJOY] Partial run: {} articles processed "
                   "(incomplete — will resume on next trigger)".format(total_processed))
    else:
        _safe_print("[ENJOY] Complete: {} articles processed".format(total_processed))
    _safe_print("[ENJOY] Output: {}".format(output_dir))
    _safe_print("=" * 60 + "\n")
    sys.stdout.flush()
    logging.info("=== Enjoy5191 crawler finished: %d articles ===",
                 total_processed)


if __name__ == "__main__":
    CONSUMER_NAME = "enjoy5191_crawler"
    init_root_logger(CONSUMER_NAME)
    main()
