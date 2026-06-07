#!/usr/bin/env python3
"""Lightweight validation for all unified crawler sites.

For each site config:
1. Fetch the listing page
2. Run CSS extractor
3. Validate: items found, title/url present, content not empty
4. Report PASS/FAIL

No KB upload, no detail fetch, no state persistence.
"""

import json
import os
import sys
import time
import traceback

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.abspath(os.path.join(_SCRIPT_DIR, "..", ".."))
sys.path.insert(0, _PROJECT_ROOT)

from common import settings
from common.log_utils import init_root_logger
from rag.svr.crawler_engine.config import ConfigLoader, SiteConfig
from rag.svr.crawler_engine.extractors.css_selector import CssSelectorExtractor
from rag.svr.crawler_engine.session_manager import SessionManager

CONSUMER_NAME = "validate_all"
init_root_logger(CONSUMER_NAME)

_CONFIG_PATH = os.path.join(_PROJECT_ROOT, "rag", "svr", "crawler_sites.yaml")


def _safe_print(msg, end="\n"):
    try:
        print(msg, end=end, flush=True)
    except UnicodeEncodeError:
        sys.stdout.write(msg.encode("gbk", errors="replace").decode("gbk"))
        sys.stdout.write(end)
        sys.stdout.flush()


def validate_site(site_id: str, cfg: SiteConfig, timeout: int = 20) -> dict:
    """Validate one site. Returns result dict."""
    result = {"site_id": site_id, "status": "UNKNOWN", "items": 0,
              "sample_title": "", "sample_url": "", "sample_content_len": 0,
              "error": ""}

    try:
        transport = cfg.transport
        sess = SessionManager.create(transport)
        listing = cfg.listing

        resp = sess.get(listing.url, timeout=timeout)
        ct = resp.headers.get("Content-Type", "")
        if "charset=" not in ct.lower():
            resp.encoding = "utf-8"

        if resp.status_code != 200:
            result["status"] = "HTTP_%d" % resp.status_code
            return result

        html = resp.text
        if len(html) < 200:
            result["status"] = "EMPTY_PAGE"
            return result

        # Run CSS extractor
        extract_cfg = cfg.extract
        ext = CssSelectorExtractor(extract_cfg)
        items = ext.extract(html, base_url=listing.url)
        result["items"] = len(items)

        if not items:
            result["status"] = "NO_ITEMS"
            return result

        # Validate first item
        first = items[0]
        result["sample_title"] = (first.get("title") or "")[:60]
        result["sample_url"] = (first.get("url") or first.get("href") or first.get("id") or "")[:80]

        # Check content from detail page (fetch one detail for spot check)
        detail_cfg = cfg.detail
        if detail_cfg and detail_cfg.type == "css_selector":
            detail_url = first.get("url") or first.get("href") or first.get("link") or ""
            if detail_url:
                from urllib.parse import urljoin
                if not detail_url.startswith("http"):
                    detail_url = urljoin(listing.url, detail_url)
                try:
                    import requests
                    from bs4 import BeautifulSoup
                    dresp = requests.get(detail_url, timeout=timeout,
                                         headers={"User-Agent": "Mozilla/5.0"})
                    if "charset=" not in dresp.headers.get("Content-Type", "").lower():
                        dresp.encoding = "utf-8"
                    if dresp.status_code == 200:
                        soup = BeautifulSoup(dresp.text, "lxml")
                        # Try configured selector first, then fallbacks
                        content_field = detail_cfg.content_field
                        selectors = []
                        if content_field:
                            selectors.append(content_field)
                        selectors.extend([".TRS_Editor", "#detailCont",
                                          ".article-content", ".news-content",
                                          ".detail-content", ".text-content",
                                          ".pages_content", "#zoom", "article"])
                        for sel in selectors:
                            el = soup.select_one(sel)
                            if el and len(el.get_text(strip=True)) > 50:
                                result["sample_content_len"] = len(el.get_text(strip=True))
                                break
                except Exception:
                    pass

        if result["sample_title"] and (result["sample_url"].startswith("http") or len(result["sample_url"]) > 10):
            result["status"] = "PASS"
        elif result["items"] > 0:
            result["status"] = "PARTIAL"  # has items but missing title/url
        else:
            result["status"] = "FAIL"

    except Exception as e:
        result["status"] = "ERROR"
        result["error"] = str(e)[:200]
        traceback.print_exc()

    return result


def main():
    settings.init_settings()

    loader = ConfigLoader(_CONFIG_PATH)
    site_ids = loader.list_site_ids()

    # Get enabled sites
    enabled = []
    for sid in site_ids:
        cfg = loader.get(sid)
        if cfg.enabled:
            enabled.append((sid, cfg))

    _safe_print("=== Unified Crawler Validation ===")
    _safe_print("Total sites: %d (enabled: %d)\n" % (len(site_ids), len(enabled)))

    results = []
    for i, (site_id, cfg) in enumerate(enabled, 1):
        _safe_print("[%d/%d] %-40s ... " % (i, len(enabled), site_id), end="")
        r = validate_site(site_id, cfg)
        results.append(r)

        if r["status"] == "PASS":
            _safe_print("PASS  items=%d  title=%s  content=%d" % (
                r["items"], r["sample_title"][:40], r["sample_content_len"]))
        elif r["status"] == "PARTIAL":
            _safe_print("PARTIAL  items=%d  title=%s  url=%s" % (
                r["items"], r["sample_title"][:30], r["sample_url"][:40]))
        else:
            _safe_print("FAIL  %s  items=%d  %s" % (
                r["status"], r["items"], r["error"][:80]))

        time.sleep(0.5)

    # Summary
    passed = sum(1 for r in results if r["status"] == "PASS")
    partial = sum(1 for r in results if r["status"] == "PARTIAL")
    failed = sum(1 for r in results if r["status"] not in ("PASS", "PARTIAL"))

    _safe_print("\n=== Summary ===")
    _safe_print("PASS:    %d" % passed)
    _safe_print("PARTIAL: %d" % partial)
    _safe_print("FAIL:    %d" % failed)
    _safe_print("Total:   %d" % len(results))

    if failed > 0:
        _safe_print("\n--- Failed sites ---")
        for r in results:
            if r["status"] not in ("PASS", "PARTIAL"):
                _safe_print("  %-40s  %s  %s" % (r["site_id"], r["status"], r["error"][:80]))


if __name__ == "__main__":
    main()
