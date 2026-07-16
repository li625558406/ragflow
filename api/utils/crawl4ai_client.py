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
"""crawl4ai Docker 引擎 HTTP 客户端 (同步, 在后台线程中调用)

引擎 REST API 契约 (crawl4ai/deploy/docker/server.py):
  POST /crawl  Body: {urls, browser_config, crawler_config}
  Response:    {"success": true, "results": [CrawlResult.model_dump(), ...]}
"""
import json
import logging
import os
from typing import Any, Dict, List, Optional

import requests

CRAWL4AI_BASE_URL = os.environ.get("CRAWL4AI_BASE_URL", "http://crawl4ai:11235")
CRAWL4AI_API_TOKEN = os.environ.get("CRAWL4AI_API_TOKEN", "konus-crawler-token-2026")


class Crawl4aiError(Exception):
    pass


class Crawl4aiClient:
    """Synchronous client for the crawl4ai Docker REST API."""

    def __init__(self, base_url: str = "", api_token: str = "", timeout: int = 300):
        self.base_url = (base_url or CRAWL4AI_BASE_URL).rstrip("/")
        token = api_token or CRAWL4AI_API_TOKEN
        self.headers = {"Authorization": f"Bearer {token}"} if token else {}
        self.timeout = timeout

    def health(self) -> bool:
        try:
            r = requests.get(f"{self.base_url}/health", timeout=10)
            return r.status_code == 200
        except Exception:
            return False

    def crawl(
        self,
        urls: List[str],
        extraction_strategy: Optional[Dict] = None,
        css_selector: str = "",
        browser_headers: Optional[Dict] = None,
        page_timeout: int = 60000,
    ) -> List[Dict[str, Any]]:
        """Crawl URLs, return list of CrawlResult dicts (one per URL).

        Args:
            urls: URLs to crawl.
            extraction_strategy: e.g. {"type": "JsonCssExtractionStrategy",
                "params": {"schema": {...}}}. None = markdown only.
            css_selector: scope content extraction to this CSS region.
            browser_headers: extra HTTP headers for the browser.
            page_timeout: per-page timeout in ms.
        """
        browser_params: Dict[str, Any] = {"headless": True}
        if browser_headers:
            browser_params["headers"] = browser_headers

        crawler_params: Dict[str, Any] = {
            "cache_mode": "bypass",
            "page_timeout": page_timeout,
            "word_count_threshold": 1,
        }
        if css_selector:
            crawler_params["css_selector"] = css_selector
        if extraction_strategy:
            crawler_params["extraction_strategy"] = extraction_strategy

        payload = {
            "urls": urls,
            "browser_config": {"type": "BrowserConfig", "params": browser_params},
            "crawler_config": {"type": "CrawlerRunConfig", "params": crawler_params},
        }

        try:
            r = requests.post(
                f"{self.base_url}/crawl",
                json=payload,
                headers=self.headers,
                timeout=self.timeout,
            )
        except requests.RequestException as e:
            raise Crawl4aiError(f"crawl4ai engine unreachable: {e}") from e

        if r.status_code != 200:
            raise Crawl4aiError(f"crawl4ai HTTP {r.status_code}: {r.text[:300]}")

        body = r.json()
        if not body.get("success"):
            raise Crawl4aiError(f"crawl4ai returned failure: {str(body)[:300]}")
        return body.get("results", [])


def get_markdown(result: Dict[str, Any]) -> str:
    """Extract markdown text from a CrawlResult dict (handles str or object form)."""
    md = result.get("markdown")
    if isinstance(md, str):
        return md
    if isinstance(md, dict):
        return md.get("fit_markdown") or md.get("raw_markdown") or ""
    return ""


def get_extracted_items(result: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Parse extracted_content (JSON string) from a CrawlResult dict."""
    raw = result.get("extracted_content")
    if not raw:
        return []
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
    except (json.JSONDecodeError, TypeError) as e:
        logging.warning("crawl4ai extracted_content parse failed: %s", e)
        return []
    if isinstance(data, dict):
        return [data]
    return [d for d in data if isinstance(d, dict)] if isinstance(data, list) else []
