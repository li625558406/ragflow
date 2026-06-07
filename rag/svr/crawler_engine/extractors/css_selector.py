"""
CSS selector extractor — extracts data from HTML pages.

Uses BeautifulSoup (lxml parser) with CSS selectors to find and
extract structured data from HTML.

Fallback strategy (mirrors patterns from 80+ production crawler scripts):
1. Try the configured ``items_path`` CSS selector directly.
2. If no results, try common container-then-li patterns.
3. If still empty, scan all div/ul/ol for dense link clusters.
"""

import logging
import re
from typing import Any, Dict, List, Optional

from .base import BaseExtractor


class CssSelectorExtractor(BaseExtractor):
    """Extracts data from HTML using CSS selectors."""

    def __init__(self, config):
        super().__init__(config)
        self._base_url = ""

    def extract(self, raw_data: Any, base_url: str = "") -> List[Dict[str, Any]]:
        """Extract items from HTML string or BeautifulSoup object."""
        try:
            from bs4 import BeautifulSoup
        except ImportError:
            logging.warning("BeautifulSoup not available for CSS extraction")
            return []

        if isinstance(raw_data, str):
            soup = BeautifulSoup(raw_data, "lxml")
        else:
            soup = BeautifulSoup(str(raw_data), "lxml")

        items = []
        self._base_url = base_url or ""

        container_sel = self._config.items_path
        containers = soup.select(container_sel) if container_sel else []

        # Fallback: if configured selector found nothing, auto-discover
        if not containers:
            containers = self._auto_discover_items(soup)
            if containers:
                logging.info("CssExtractor: items_path '%s' matched 0, "
                             "fallback found %d items", container_sel, len(containers))

        for el in containers:
            item = self._extract_item(el)
            if item:
                items.append(item)

        return items

    def _extract_item(self, el) -> Optional[Dict[str, Any]]:
        """Extract fields from one list item element."""
        if not self._config.fields:
            link = el.select_one("a[href]")
            if link:
                return {"title": link.get_text(strip=True),
                        "url": self._resolve_url(link.get("href", "")),
                        "date": self._extract_date(el, link.get("href", ""))}
            text = el.get_text(strip=True)
            return {"text": text} if text else None

        item = {}
        has_value = False
        for internal_name, source_field in self._config.fields.items():
            val = self._extract_field(el, source_field, base_url=self._base_url)
            if val is not None:
                item[internal_name] = val
                if val.strip():
                    has_value = True

        # If date field is empty, try auto-extraction from text/URL
        if "date" in (self._config.fields or {}) and not item.get("date"):
            url_val = item.get("url") or item.get("href") or item.get("id") or ""
            item["date"] = self._extract_date(el, url_val)

        # Skip empty items (no title, no url, nothing useful)
        if not has_value:
            return None
        return item

    def _resolve_url(self, href: str) -> str:
        """Resolve a possibly-relative URL against the base URL."""
        if not href or href.startswith("http") or href.startswith("#") or href.startswith("javascript"):
            return href
        if self._base_url:
            from urllib.parse import urljoin
            return urljoin(self._base_url, href)
        return href

    @staticmethod
    def _extract_date(el, url: str = "") -> str:
        """Auto-extract date from element text or URL, supporting multiple formats.

        Mirrors date extraction from old crawler scripts:
        - YYYY-MM-DD in text
        - YYYY年MM月DD日 in text
        - /tYYYYMMDD_ embedded in URL
        - /YYYYMM/ path segment in URL
        """
        text = el.get_text(strip=True)

        # YYYY-MM-DD
        m = re.search(r'(\d{4}-\d{1,2}-\d{1,2})', text)
        if m:
            return m.group(1)

        # Chinese date: "2026年5月20日"
        m = re.search(r'(\d{4})年(\d{1,2})月(\d{1,2})日', text)
        if m:
            return "%s-%02d-%02d" % (m.group(1), int(m.group(2)), int(m.group(3)))

        # URL-embedded: /tYYYYMMDD_
        if url:
            m = re.search(r'/t(\d{8})_', url)
            if m:
                d = m.group(1)
                return "%s-%s-%s" % (d[:4], d[4:6], d[6:8])

            # URL path segment: /YYYYMM/
            m = re.search(r'/(\d{4})(\d{2})/', url)
            if m and 2020 <= int(m.group(1)) <= 2030:
                return "%s-%s" % (m.group(1), m.group(2))

        return ""

    # ------------------------------------------------------------------
    # Auto-discovery: find article list items when selector fails
    # ------------------------------------------------------------------

    # Container class patterns from gov/news CMS platforms
    _CONTAINER_CLASSES = re.compile(
        r"list_base|list_box|news_list|info_list|viewlist|"
        r"article-list|news-list|info-list|catelist|"
        r"pages_content|content_list|main_list|data_list"
    )

    @staticmethod
    def _auto_discover_items(soup) -> List:
        """Heuristic: find the li/a elements that form an article listing.

        Mirrors the discovery logic used in the old per-site crawler scripts:
        1. Find a container div with a list-like class, then grab its <li> children.
        2. Find any <ul>/<ol> with many direct <li> children (each having an <a>).
        3. Find div containers with a dense cluster of <a> links.
        """
        container_re = CssSelectorExtractor._CONTAINER_CLASSES

        # --- Strategy 1: container div + direct li children ---
        list_div = soup.find("div", class_=container_re)
        if not list_div:
            list_div = soup.find("div", attrs={"role": "viewlist"})
        if not list_div:
            list_div = soup.find("div", class_=re.compile(
                r"main|content_area|list_wrap|right_content|article_area"))

        if list_div:
            lis = list_div.find_all("li", recursive=False)
            if not lis:
                lis = list_div.find_all("li")
            # Filter: keep only li with an <a> that has a real title
            lis = [li for li in lis if li.find("a", href=True)
                   and len(li.find("a").get_text(strip=True)) >= 4]
            if len(lis) >= 2:
                return lis

        # --- Strategy 2: dense ul/ol with many li children ---
        best_ul = None
        best_count = 0
        for ul in soup.find_all(["ul", "ol"]):
            direct_lis = ul.find_all("li", recursive=False)
            link_lis = [li for li in direct_lis
                        if li.find("a", href=True)
                        and len(li.find("a").get_text(strip=True)) >= 4]
            if len(link_lis) > best_count:
                best_count = len(link_lis)
                best_ul = ul

        if best_ul and best_count >= 3:
            return [li for li in best_ul.find_all("li", recursive=False)
                    if li.find("a", href=True)]

        # --- Strategy 3: div with dense a-link cluster (no ul/li wrapper) ---
        best_div = None
        best_count = 0
        for div in soup.find_all("div"):
            links = div.find_all("a", href=True, recursive=False)
            title_links = [a for a in links if len(a.get_text(strip=True)) >= 4]
            if len(title_links) > best_count:
                best_count = len(title_links)
                best_div = div

        if best_div and best_count >= 3:
            return best_div.find_all("a", href=True, recursive=False)

        return []

    # ------------------------------------------------------------------
    # Field extraction
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_field(el, source_field: str, base_url: str = ""):
        """Extract a value from a BeautifulSoup element.

        Supports the following syntax:
        - ``"a"`` — get text content of first matching child element
        - ``"a@href"`` — get an attribute of a child element
        - ``"@data-id"`` — get an attribute of the container element itself
        - ``"td:last-child"`` — CSS pseudo-classes work on the selector part

        Returns the extracted value (stripped), or None if not found.
        """
        if not source_field:
            return None

        if '@' in source_field:
            # Attribute extraction
            parts = source_field.rsplit('@', 1)
            selector = parts[0] if parts[0] else None
            attr = parts[1]
            if selector:
                found = el.select_one(selector)
                if found:
                    val = found.get(attr, '')
                    val = val.strip() if isinstance(val, str) else str(val) if val else ''
                    # Resolve relative URLs (href, src) to absolute
                    if val and attr in ("href", "src") and base_url and not val.startswith("http"):
                        from urllib.parse import urljoin
                        val = urljoin(base_url, val)
                    return val
            else:
                # "@attr" means attribute on the container element itself
                val = el.get(attr, '')
                return val.strip() if isinstance(val, str) else str(val) if val else ''
            return None
        else:
            # Text content extraction
            found = el.select_one(source_field)
            if found:
                return found.get_text(strip=True)
            return None
