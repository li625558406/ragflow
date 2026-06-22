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
import logging
import os
import re
from abc import ABC
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

from agent.tools.base import ToolParamBase, ToolBase, ToolMeta
from common.connection_utils import timeout

_EXEC_TIMEOUT = int(os.environ.get("URL_FETCH_TIMEOUT", 30))
_MAX_CONTENT_CHARS = int(os.environ.get("URL_FETCH_MAX_CHARS", 8000))


class URLFetchParam(ToolParamBase):
    """Parameters for the URLFetch tool."""

    def __init__(self):
        self.meta: ToolMeta = {
            "name": "url_fetch",
            "description": """
使用时机：当你需要直接获取某个具体网页的完整文本内容时使用（如招标公告详情页、政策文件页面等）。

与 crawl_fetch 的区别：
- crawl_fetch：从已配置的站点列表中批量爬取最新数据，适合"搜索最新招标信息"
- url_fetch：直接获取单个指定 URL 的页面文本内容，适合"这个原文链接里写了什么"

典型场景：
- 用户说"打开这个链接看看详细内容" → 使用 url_fetch
- bid_get_detail 返回的信息不够完整，需要从原文获取更多细节 → 使用 url_fetch
- 用户提供了一个具体的政策文件/公告通知 URL → 使用 url_fetch

不适用场景：
- 搜索招标信息 → 使用 bid_search 或 bid_search_ai
- 获取项目结构化数据 → 使用 bid_get_detail
- 搜索知识库 → 使用知识库检索工具

注意事项：
- URL 必须是完整的 http/https 地址（内网/本地地址会被拦截）
- 返回纯文本内容，最多 {} 字。PDF 或二进制文件无法解析
- 某些网站有反爬机制，可能返回 403/503 等错误
""".format(_MAX_CONTENT_CHARS),
            "parameters": {
                "url": {
                    "type": "string",
                    "description": "要获取内容的完整 URL 地址。必须是 http:// 或 https:// 开头。",
                    "required": True,
                },
            },
        }
        super().__init__()


class URLFetch(ToolBase, ABC):
    """Agent tool — fetch text content from an arbitrary URL."""

    component_name = "URLFetch"

    @timeout(_EXEC_TIMEOUT)
    def _invoke(self, **kwargs):
        if self.check_if_canceled("URLFetch"):
            return

        url = str(kwargs.get("url", "")).strip()
        if not url:
            self.set_output("_ERROR", "url is required")
            return "Error: url is required."

        if not url.startswith(("http://", "https://")):
            self.set_output("_ERROR", f"Invalid URL scheme: {url}")
            return "Error: URL must start with http:// or https://"

        parsed = urlparse(url)
        domain = parsed.hostname or "unknown"

        # ── SSRF guard ──
        try:
            from common.ssrf_guard import assert_url_is_safe
            hostname, resolved_ip = assert_url_is_safe(url)
            logging.info("[URLFetch] SSRF check passed: %s → %s", hostname, resolved_ip)
        except ImportError:
            logging.warning("[URLFetch] ssrf_guard not available, skipping check")
        except ValueError as e:
            logging.warning("[URLFetch] SSRF blocked: %s — %s", url, e)
            self.set_output("_ERROR", str(e))
            return f"Error: URL blocked by security policy: {e}"

        # ── Fetch ──
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        }
        try:
            resp = requests.get(url, headers=headers, timeout=20, allow_redirects=True)
            resp.raise_for_status()
        except requests.Timeout:
            return f"Error: Request timed out after 20s for {domain}"
        except requests.HTTPError as e:
            return f"Error: HTTP {resp.status_code} from {domain}"
        except requests.RequestException as e:
            return f"Error: Failed to fetch {domain}: {e}"

        # ── Extract ──
        title = self._extract_title(resp.text)
        content = self._extract_text(resp.text)

        actual_len = len(resp.text)
        content_len = len(content)
        truncated = actual_len > _MAX_CONTENT_CHARS and content_len >= _MAX_CONTENT_CHARS

        result = []
        if title:
            result.append(f"# {title}")
        result.append(f"URL: {url}")
        result.append(f"Content-Type: {resp.headers.get('Content-Type', 'unknown')}")
        if truncated:
            result.append(f"(内容已截断: 原文约 {actual_len} 字符, 提取 {content_len} 字符)")
        result.append("")
        result.append(content)

        output_text = "\n".join(result)

        self.set_output("formalized_content", output_text)
        self.set_output("json", {
            "url": url,
            "domain": domain,
            "status_code": resp.status_code,
            "title": title,
            "content_length": content_len,
            "original_length": actual_len,
            "truncated": truncated,
        })
        return output_text

    def _extract_text(self, html: str) -> str:
        """Extract readable text from HTML, removing noise elements."""
        try:
            soup = BeautifulSoup(html, "lxml")
            # Remove noise
            for tag in soup(["script", "style", "nav", "header", "footer",
                             "noscript", "iframe", "aside", "form"]):
                tag.decompose()
            text = soup.get_text(separator="\n", strip=True)
            lines = [ln.strip() for ln in text.split("\n") if ln.strip()]
            return "\n".join(lines)[:_MAX_CONTENT_CHARS]
        except Exception:
            # Fallback: regex-based extraction
            text = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL | re.IGNORECASE)
            text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL | re.IGNORECASE)
            text = re.sub(r"<[^>]+>", " ", text)
            text = re.sub(r"&[a-z]+;", " ", text)
            text = re.sub(r"\s+", " ", text).strip()
            return text[:_MAX_CONTENT_CHARS]

    def _extract_title(self, html: str) -> str:
        try:
            soup = BeautifulSoup(html, "lxml")
            return soup.title.string.strip() if soup.title and soup.title.string else ""
        except Exception:
            match = re.search(r"<title[^>]*>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
            return match.group(1).strip() if match else ""

    def thoughts(self) -> str:
        url = self.get_input().get("url", "-")
        return f"Fetching URL: {url}..."
