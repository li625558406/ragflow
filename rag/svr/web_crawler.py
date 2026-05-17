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
Web crawler for scheduled tasks.

Crawls a target website, extracts articles published from 2023 onwards,
analyzes embedded images via an LLM vision model, generates a Markdown
document, and uploads it to a RAGFlow knowledge base.

Usage (typically spawned by task_executor):
    python web_crawler.py \\
        --tenant-id <TENANT_ID> \\
        --target-url <URL> \\
        --llm-id <FACTORY> \\
        --llm-model <MODEL_NAME> \\
        --kb-id <KB_ID> \\
        --task-name <NAME>
"""

import argparse
import json
import logging
import os
import re
import sys
from datetime import datetime
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from common import settings
from common.log_utils import init_root_logger
from common.misc_utils import get_uuid
from rag.svr.crawler_utils import PlaywrightHttpClient


def parse_args():
    parser = argparse.ArgumentParser(description="Web crawler for scheduled tasks")
    parser.add_argument("--tenant-id", required=True, help="Tenant ID for model/API key lookup")
    parser.add_argument("--target-url", required=True, help="Homepage URL to crawl")
    parser.add_argument("--llm-id", required=True, help="LLM factory name, e.g. OpenAI / Tongyi-Qianwen")
    parser.add_argument("--llm-model", required=True, help="LLM model name, e.g. gpt-4o / qwen-vl-max")
    parser.add_argument("--kb-id", required=True, help="Target knowledge-base ID")
    parser.add_argument("--task-name", required=True, help="Task name used as output sub-directory")
    parser.add_argument("--output-dir", default=None, help="Output root directory (default: project root)")
    parser.add_argument("--full", action="store_true", help="Ignore saved state and re-crawl all articles")
    return parser.parse_args()


# ---------------------------------------------------------------------------
# Initialisation
# ---------------------------------------------------------------------------

def _init():
    settings.init_settings()
    logging.info("Project settings initialised")


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}


def _fetch(url, timeout=30, client=None):
    try:
        if client is not None:
            resp = client.get(url, headers=_HEADERS, timeout=timeout)
            resp.raise_for_status()
            return resp.text if resp.text else None
        resp = requests.get(url, headers=_HEADERS, timeout=timeout)
        resp.raise_for_status()
        raw = resp.content
        import chardet
        detected = chardet.detect(raw)
        enc = detected.get("encoding", "") or ""
        # Fallback chain: detected -> apparent -> utf-8 -> gbk
        if enc.upper() in ("EUC-JP", "EUC-KR", "SHIFT_JIS", "ISO-8859-1"):
            enc = resp.apparent_encoding or "utf-8"
        if not enc or enc.upper() in ("ASCII", "ISO-8859-1"):
            enc = "utf-8"
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            return raw.decode("gbk", errors="replace")
    except Exception as e:
        logging.error("Failed to fetch %s: %s", url, e)
        return None


# ---------------------------------------------------------------------------
# Article extraction & date filtering
# ---------------------------------------------------------------------------

_DATE_PATTERNS = [
    "%Y-%m-%d",
    "%Y/%m/%d",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%dT%H:%M:%S%z",
    "%Y-%m-%d %H:%M:%S",
    "%B %d, %Y",
    "%d %B %Y",
    "%b %d, %Y",
    "%d %b %Y",
    "%Y年%m月%d日",
]


def _parse_date(text):
    if not text:
        return None
    text = text.strip()
    for fmt in _DATE_PATTERNS:
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    m = re.search(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})", text)
    if m:
        try:
            return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            pass
    return None


def _date_near(link_tag):
    """Walk up from an <a> tag searching for a date element / time tag."""
    for ancestor in (link_tag.parent, link_tag.parent, link_tag.parent):
        if ancestor is None:
            break
        time_tag = ancestor.find("time")
        if time_tag:
            return time_tag.get("datetime") or time_tag.text.strip()
        for cls_pat in ("date", "time", "post-meta", "publish", "meta"):
            el = ancestor.find(class_=re.compile(cls_pat, re.I))
            if el:
                return el.get("datetime") or el.text.strip()
    return ""


def _abs_url(href, base):
    if href.startswith("http"):
        return href
    if href.startswith("/"):
        p = urlparse(base)
        return f"{p.scheme}://{p.netloc}{href}"
    return urljoin(base, href)


# ---------------------------------------------------------------------------
# Nuxt.js SPA article extraction
# ---------------------------------------------------------------------------

def _parse_nuxt_state(html):
    """Parse Nuxt.js SSR serialized state from <script> tags.

    Returns the deserialized data list, or None if the page is not a Nuxt SPA.
    """
    import json
    scripts = re.findall(r'<script[^>]*>([\s\S]*?)</script>', html)
    for s in scripts:
        s = s.strip()
        if s.startswith("[") and '"Reactive"' in s:
            try:
                return json.loads(s)
            except json.JSONDecodeError:
                continue
    return None


def _nuxt_ref(data, v):
    """Resolve a single-level Nuxt data reference."""
    if isinstance(v, int) and 0 <= v < len(data):
        return data[v]
    return v


def extract_nuxt_articles(html, base_url):
    """Extract articles from a Nuxt.js SPA page via its embedded state.

    Returns list[dict] with title, url, date, date_str, content, images,
    or None if the page does not contain Nuxt state.
    """
    data = _parse_nuxt_state(html)
    if data is None:
        return None

    payload_indices = data[2] if len(data) > 2 else {}

    # Article categories commonly found on Chinese industry-association Nuxt sites
    ARTICLE_CATEGORIES = [
        "协会动态", "行业资讯", "通知公告",
        "协会党建", "招投标业务培训",
    ]

    articles = []
    seen_codes = set()

    for cat in ARTICLE_CATEGORIES:
        if cat not in payload_indices:
            continue

        cat_idx = payload_indices[cat]
        if not isinstance(cat_idx, int) or cat_idx >= len(data):
            continue

        cat_data = data[cat_idx]
        if not isinstance(cat_data, dict):
            continue

        list_ref = cat_data.get("list")
        if not isinstance(list_ref, int) or list_ref >= len(data):
            continue

        article_refs = data[list_ref]
        if not isinstance(article_refs, list):
            continue

        for art_ref in article_refs:
            if not isinstance(art_ref, int) or art_ref >= len(data):
                continue

            art = data[art_ref]
            if not isinstance(art, dict):
                continue

            # -- newsCode (dedup key) --
            news_code = _nuxt_ref(data, art.get("newsCode", ""))
            if not news_code or news_code in seen_codes:
                continue
            seen_codes.add(news_code)

            # -- title --
            title = _nuxt_ref(data, art.get("newsTitle", "")) or ""

            # -- date --
            pub_date_str = str(_nuxt_ref(data, art.get("publishTimeWeb", "")) or "")
            dt = _parse_date(pub_date_str) if pub_date_str else None

            # -- HTML content --
            content_raw = _nuxt_ref(data, art.get("newsContent", "")) or ""
            content_html = str(content_raw)

            # -- article URL (detail page) --
            article_url = f"/news-detail?code={news_code}"
            full_url = _abs_url(article_url, base_url)

            # -- extract images from content HTML --
            images = []
            if content_html.strip():
                img_soup = BeautifulSoup(content_html, "lxml")
                for img in img_soup.find_all("img"):
                    src = img.get("src") or img.get("data-src") or ""
                    if src:
                        images.append(_abs_url(src, base_url))

            # -- date filter --
            if dt and dt.year < 2023:
                logging.info("Skipped %s (date: %s)", str(title)[:60], dt.date())
                continue

            articles.append({
                "title": str(title).strip(),
                "url": full_url,
                "date": dt,
                "date_str": pub_date_str,
                "content": content_html,
                "images": images,
            })

    if articles:
        logging.info("Found %d unique articles via Nuxt state", len(articles))
    return articles


# ---------------------------------------------------------------------------
# Traditional (non-SPA) article extraction via BeautifulSoup
# ---------------------------------------------------------------------------

def extract_articles(html, base_url):
    """Return list[dict] of articles with title, url, date (parsed or None).

    Works on traditional server-rendered HTML pages.  For JS-heavy SPA sites
    (e.g. Nuxt.js) :func:`extract_nuxt_articles` is preferred.
    """
    soup = BeautifulSoup(html, "lxml")
    articles = []
    seen = set()

    # Collect candidate containers
    containers = soup.find_all(
        ["article", "li", "div"],
        class_=re.compile(r"post|item|entry|article|news|list", re.I),
    )
    if not containers:
        containers = [soup]

    # Debug: count total <a> tags on the page
    all_links = soup.find_all("a", href=True)
    print(f"[CRAWLER] DEBUG: Total <a> tags with href: {len(all_links)}")
    for i, a in enumerate(all_links[:5]):
        print(f"[CRAWLER] DEBUG:   sample [{i}] href={a['href'][:80]} text={a.text.strip()[:50]!r}")
    sys.stdout.flush()

    for container in containers:
        for a in container.find_all("a", href=True):
            href = a["href"].strip()
            title = (a.get("title") or a.text or "").strip()
            if not title or len(title) < 2:
                continue
            # Allow short (2-4 char) Chinese titles; require >=5 for non-Chinese
            if len(title) < 5 and not re.search(r'[\u4e00-\u9fff]', title):
                continue
            if any(k in href for k in ("#", "javascript:", "tag/", "category/", "author/")):
                continue

            url = _abs_url(href, base_url)
            if url in seen:
                continue
            seen.add(url)

            date_str = _date_near(a)
            dt = _parse_date(date_str)

            if dt and dt.year < 2023:
                logging.info("Skipped %s (date: %s)", title, dt.date())
                continue

            articles.append({"title": title, "url": url, "date": dt, "date_str": date_str or ""})

    logging.info("Found %d unique articles after filtering", len(articles))
    return articles


# ---------------------------------------------------------------------------
# HTML → Markdown conversion
# ---------------------------------------------------------------------------

def _html_to_markdown(html_content, base_url):
    """Convert HTML to a rough Markdown string, extracting image URLs."""
    if not html_content or not html_content.strip():
        return "", []

    soup = BeautifulSoup(html_content, "lxml")

    # Strip clutter
    for tag in soup.find_all(["script", "style", "nav", "header", "footer", "aside", "noscript"]):
        tag.decompose()

    # Collect images
    images = []
    for img in soup.find_all("img"):
        src = img.get("src") or img.get("data-src") or ""
        if not src:
            continue
        src = _abs_url(src, base_url)
        if src.startswith(("http://", "https://")):
            images.append(src)

    # Convert block elements to rough markdown
    lines = []
    for el in soup.find_all(["p", "h1", "h2", "h3", "h4", "h5", "h6", "li", "blockquote", "pre"]):
        text = el.get_text(strip=True)
        if not text:
            continue
        tn = el.name
        if tn == "h1":
            lines.append(f"\n# {text}\n")
        elif tn == "h2":
            lines.append(f"\n## {text}\n")
        elif tn == "h3":
            lines.append(f"\n### {text}\n")
        elif tn in ("h4", "h5", "h6"):
            lines.append(f"\n**{text}**\n")
        elif tn == "blockquote":
            lines.append(f"> {text}")
        elif tn == "li":
            lines.append(f"- {text}")
        elif tn == "pre":
            lines.append(f"```\n{text}\n```")
        else:
            lines.append(text)

    return "\n\n".join(lines), images


# ---------------------------------------------------------------------------
# Detail-page scraping (traditional HTML sites)
# ---------------------------------------------------------------------------

def fetch_article_content(url, client=None):
    """Return (markdown_text, list_of_image_urls) for a traditional HTML page."""
    html = _fetch(url, client=client)
    if not html:
        return "", []

    soup = BeautifulSoup(html, "lxml")

    # Locate the main content area
    content = None
    for sel in (
        "article",
        "[role='main']",
        ".post-content",
        ".article-content",
        ".entry-content",
        ".content",
        "#content",
        "main",
        "body",
    ):
        content = soup.select_one(sel)
        if content:
            break

    if content is None:
        return "", []

    return _html_to_markdown(str(content), url)


# ---------------------------------------------------------------------------
# Image analysis via LLM vision model
# ---------------------------------------------------------------------------

def _download_image(url, client=None):
    try:
        if client is not None:
            resp = client.get(url, headers=_HEADERS, timeout=15)
            resp.raise_for_status()
            return resp.content
        resp = requests.get(url, timeout=15, headers=_HEADERS)
        resp.raise_for_status()
        return resp.content
    except Exception as e:
        logging.warning("Failed to download image %s: %s", url, e)
        return None


def _create_cv_model(tenant_id, llm_factory, llm_model_name):
    """Instantiate the vision LLM model for this tenant."""
    from api.db.services.tenant_llm_service import TenantLLMService
    from common.constants import LLMType

    # Try several lookup strategies
    full_name = f"{llm_model_name}@{llm_factory}"
    model_obj = (
        TenantLLMService.get_api_key(tenant_id, full_name, LLMType.IMAGE2TEXT.value)
        or TenantLLMService.get_api_key(tenant_id, full_name, LLMType.CHAT.value)
        or TenantLLMService.get_api_key(tenant_id, llm_model_name, LLMType.IMAGE2TEXT.value)
        or TenantLLMService.get_api_key(tenant_id, llm_model_name, LLMType.CHAT.value)
    )
    if not model_obj:
        raise LookupError(
            f"Model {llm_model_name}@{llm_factory} not found for tenant {tenant_id}"
        )

    config = model_obj.to_dict()
    mdl = TenantLLMService.model_instance(config)
    if not mdl:
        raise RuntimeError(f"Cannot instantiate model {llm_factory}/{llm_model_name}")
    return mdl


def _analyze_image(image_bytes, cv_mdl):
    """Return a text description of the image content."""
    try:
        desc, _ = cv_mdl.describe(image_bytes)
        return desc
    except Exception as e:
        logging.warning("Image analysis error: %s", e)
        return None


# ---------------------------------------------------------------------------
# Markdown persistence
# ---------------------------------------------------------------------------

_STATE_FILENAME = "_crawler_state.json"


def _load_state(output_dir):
    """Load crawler state (set of already-processed article URLs)."""
    path = os.path.join(output_dir, _STATE_FILENAME)
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logging.warning("Failed to load crawler state: %s", e)
    return {"processed_urls": []}


def _save_state(output_dir, state):
    """Save crawler state to disk."""
    path = os.path.join(output_dir, _STATE_FILENAME)
    os.makedirs(output_dir, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False)
    logging.info("Crawler state saved (%d processed URLs)", len(state.get("processed_urls", [])))


def _save_markdown(content, output_dir):
    os.makedirs(output_dir, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = os.path.join(output_dir, f"{ts}.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    logging.info("Saved markdown to %s", path)
    return path


# ---------------------------------------------------------------------------
# Knowledge-base upload
# ---------------------------------------------------------------------------

def _upload_to_kb(filepath, kb_id, tenant_id):
    from api.db.services.knowledgebase_service import KnowledgebaseService
    from api.db.services.file_service import FileService
    from api.db.services.document_service import DocumentService

    ok, kb = KnowledgebaseService.get_by_id(kb_id)
    if not ok:
        raise LookupError(f"Knowledge base {kb_id} not found")

    with open(filepath, "rb") as f:
        blob = f.read()

    class _FileObj:
        def __init__(self, filename, blob):
            self.id = get_uuid()
            self.filename = filename
            self.blob = blob

        def read(self):
            return self.blob

    file_obj = _FileObj(os.path.basename(filepath), blob)
    errs, doc_pairs = FileService.upload_document(kb, [file_obj], tenant_id)
    if errs:
        logging.warning("Upload errors: %s", errs)
    for doc, _ in doc_pairs:
        logging.info("Document %s uploaded to KB %s", doc["id"], kb_id)
        # Trigger document parsing (chunking) immediately after upload
        try:
            DocumentService.begin2parse(doc["id"])
            DocumentService.run(tenant_id, doc, {})
            logging.info("Parsing task queued for document %s", doc["id"])
        except Exception as e:
            logging.error("Failed to queue parsing for document %s: %s", doc["id"], e)
    return doc_pairs


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    args = parse_args()
    print(f"\n{'='*60}")
    print(f"[CRAWLER] Starting web crawler")
    print(f"[CRAWLER] Target URL: {args.target_url}")
    print(f"[CRAWLER] Task name: {args.task_name}")
    print(f"[CRAWLER] LLM: {args.llm_id} / {args.llm_model}")
    print(f"[CRAWLER] Target KB: {args.kb_id}")
    print(f"{'='*60}\n")
    sys.stdout.flush()

    _init()
    logging.info("=== Web crawler started for %s ===", args.target_url)

    client = PlaywrightHttpClient()
    client.start()
    try:

        # 1. Fetch homepage
        print(f"[CRAWLER] Step 1/7: Fetching homepage...")
        sys.stdout.flush()
        html = _fetch(args.target_url, client=client)
        if not html:
            print(f"[CRAWLER] ERROR: Homepage fetch failed, exiting")
            sys.stdout.flush()
            logging.error("Homepage fetch failed, exiting")
            sys.exit(1)
        print(f"[CRAWLER] Step 1/7: Homepage fetched successfully ({len(html)} bytes)\n")
        sys.stdout.flush()

        # 2. Extract articles (filters < 2023)
        print(f"[CRAWLER] Step 2/7: Extracting articles...")
        sys.stdout.flush()

        # Try Nuxt.js state parsing first (SPA sites like Vue/Nuxt)
        articles = extract_nuxt_articles(html, args.target_url)
        is_nuxt = articles is not None
        if is_nuxt:
            print(f"[CRAWLER] Step 2/7: Found {len(articles)} articles via Nuxt state parsing\n")
        else:
            # Fall back to traditional BeautifulSoup HTML parsing
            articles = extract_articles(html, args.target_url)
            print(f"[CRAWLER] Step 2/7: Found {len(articles)} articles via HTML parsing\n")
        sys.stdout.flush()
        if not articles:
            print(f"[CRAWLER] No articles found, exiting")
            sys.stdout.flush()
            logging.warning("No articles found, exiting")
            sys.exit(0)

        # 3. Initialise vision model
        print(f"[CRAWLER] Step 3/7: Initialising vision model ({args.llm_id} / {args.llm_model})...")
        sys.stdout.flush()
        logging.info("Initialising vision model: %s / %s", args.llm_id, args.llm_model)
        try:
            cv_mdl = _create_cv_model(args.tenant_id, args.llm_id, args.llm_model)
            print(f"[CRAWLER] Step 3/7: Vision model ready\n")
            sys.stdout.flush()
        except Exception as e:
            print(f"[CRAWLER] ERROR: Vision model init failed: {e}")
            sys.stdout.flush()
            logging.error("Vision model init failed: %s", e)
            sys.exit(1)

        # 4. Output directory
        output_dir = args.output_dir or os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), args.task_name.strip()
        )
        print(f"[CRAWLER] Output directory: {output_dir}\n")
        sys.stdout.flush()

        # 5. Load state for incremental crawling
        state = _load_state(output_dir) if not args.full else {"processed_urls": []}
        processed_urls = set(state.get("processed_urls", []))
        if processed_urls:
            print(f"[CRAWLER] Loaded {len(processed_urls)} previously processed URLs, skipping them\n")
            sys.stdout.flush()
            # Filter out already-processed articles
            new_articles = [a for a in articles if a["url"] not in processed_urls]
            skipped = len(articles) - len(new_articles)
            if skipped:
                print(f"[CRAWLER] Skipping {skipped} already-processed article(s)\n")
                sys.stdout.flush()
            articles = new_articles

        # 6. Process each article
        print(f"[CRAWLER] Step 4/7: Processing {len(articles)} articles...\n")
        sys.stdout.flush()
        md_parts = []
        for idx, art in enumerate(articles, 1):
            print(f"[CRAWLER] Article [{idx}/{len(articles)}]: {art['title']}")
            sys.stdout.flush()
            logging.info("[%d/%d] %s", idx, len(articles), art["title"])

            if is_nuxt:
                # Nuxt: content + images already extracted from page state
                content_html = art.get("content", "")
                if not content_html:
                    print(f"[CRAWLER]   -> Empty content, skipped")
                    sys.stdout.flush()
                    logging.warning("Empty content for %s, skipped", art["title"])
                    continue
                content, images = _html_to_markdown(content_html, art["url"])
            else:
                # Traditional: fetch detail page and extract content
                content, images = fetch_article_content(art["url"], client=client)
            if not content:
                print(f"[CRAWLER]   -> Empty content, skipped")
                sys.stdout.flush()
                logging.warning("Empty content for %s, skipped", art["title"])
                continue

            print(f"[CRAWLER]   -> Content: {len(content)} chars, Images: {len(images)}")
            sys.stdout.flush()

            # Build article markdown section
            article_date_str = art["date"].strftime("%Y-%m-%d") if art.get("date") else art.get("date_str", "")
            lines = [
                f"# {art['title']}",
                f"**Date:** {article_date_str}",
                "",
                content,
            ]

            if images:
                print(f"[CRAWLER]   -> Analyzing {min(len(images), 5)} images via LLM...")
                sys.stdout.flush()
                lines.append("")
                lines.append("## Image Analysis\n")
                for i, img_url in enumerate(images[:5], 1):
                    print(f"[CRAWLER]   -> Image {i}/{min(len(images), 5)}: {img_url[:80]}")
                    sys.stdout.flush()
                    img_bytes = _download_image(img_url, client=client)
                    if img_bytes:
                        desc = _analyze_image(img_bytes, cv_mdl)
                        if desc:
                            lines.append(f"![]({img_url})\n\n{desc}\n")
                            print(f"[CRAWLER]   -> Analysis: {desc[:100]}...")
                        else:
                            print(f"[CRAWLER]   -> Analysis returned empty")
                    else:
                        print(f"[CRAWLER]   -> Download failed, skipped")
                    sys.stdout.flush()

            lines.append("")
            lines.append("---")
            md_parts.append("\n".join(lines))

        if not md_parts:
            print(f"[CRAWLER] No articles processed successfully, exiting")
            sys.stdout.flush()
            logging.warning("No articles processed successfully")
            sys.exit(0)

        # 6. Save combined markdown
        print(f"[CRAWLER] Step 5/7: Saving markdown...")
        sys.stdout.flush()
        combined = "\n".join(md_parts)
        filepath = _save_markdown(combined, output_dir)
        print(f"[CRAWLER] Step 5/7: Saved to {filepath} ({len(combined)} chars)\n")
        sys.stdout.flush()

        # Save state so next run skips processed articles
        new_urls = [a["url"] for a in articles]
        if new_urls:
            processed_urls.update(new_urls)
            _save_state(output_dir, {"processed_urls": list(processed_urls)})

        # 7. Upload to KB
        print(f"[CRAWLER] Step 6/7: Uploading to KB {args.kb_id}...")
        sys.stdout.flush()
        logging.info("Uploading to KB %s …", args.kb_id)
        try:
            _upload_to_kb(filepath, args.kb_id, args.tenant_id)
            print(f"[CRAWLER] Step 7/7: Upload complete!\n")
            sys.stdout.flush()
            logging.info("Upload complete")
        except Exception as e:
            print(f"[CRAWLER] ERROR: Upload failed: {e}")
            sys.stdout.flush()
            logging.error("Upload failed: %s", e)
            sys.exit(1)

        print(f"{'='*60}")
        print(f"[CRAWLER] All done! Task completed successfully.")
        print(f"{'='*60}")
        sys.stdout.flush()
        logging.info("=== Web crawler finished successfully ===")
    finally:
        client.stop()


if __name__ == "__main__":
    CONSUMER_NAME = "web_crawler"
    init_root_logger(CONSUMER_NAME)
    main()
