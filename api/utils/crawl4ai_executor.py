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
"""crawl4ai 采集执行器 — 列表页 → 详情页 → 结构化入库 → KB 上传解析

在后台线程中同步执行完整采集周期:
  1. 逐页爬取列表页 (JsonCssExtractionStrategy 提取 title/url/date)
  2. 按 md5(site_id|url) 去重
  3. 爬取详情页 → markdown 正文 + 附件链接
  4. 写 crawler_result 表
  5. 可选: 正文/附件上传 RAGFlow KB 并触发解析
"""
import logging
import os
import re
import tempfile
import time
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin

from api.db.services.crawler_service import (
    CrawlerResultService,
    CrawlerTaskService,
    gen_result_id,
)
from api.utils.crawl4ai_client import (
    Crawl4aiClient,
    Crawl4aiError,
    get_extracted_items,
    get_markdown,
)
from common.time_utils import current_timestamp

DEFAULT_ATTACHMENT_EXTS = [
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".zip", ".rar",
]
_REQUEST_DELAY = 1.0  # seconds between detail page crawls


class Crawl4aiExecutor:
    """Execute one full crawl cycle for a crawler_task."""

    def __init__(self, task: Dict[str, Any]):
        self.task = task
        self.client = Crawl4aiClient()
        self.detail_cfg = task.get("detail_config") or {}
        self.headers = task.get("headers") or None
        self.summary = {
            "status": "running",
            "pages": 0,
            "items_found": 0,
            "items_new": 0,
            "kb_uploaded": 0,
            "attachments_uploaded": 0,
            "errors": [],
        }

    # ------------------------------------------------------------------
    # Entry
    # ------------------------------------------------------------------

    def run(self) -> Dict[str, Any]:
        task_id = self.task["id"]
        CrawlerTaskService.update_by_id(task_id, {
            "last_run_status": "running",
            "last_run_time": current_timestamp(),
        })
        try:
            for page_url in self._page_urls():
                items = self._crawl_listing(page_url)
                if items is None:  # page-level failure, stop paging
                    break
                self.summary["pages"] += 1
                self.summary["items_found"] += len(items)
                if not items:
                    break  # empty page = end of listing
                for item in items:
                    self._process_item(item, page_url)
            # 有产出即算成功（页级错误已记录在 errors 中）
            if self.summary["pages"] > 0:
                self.summary["status"] = "success"
            else:
                self.summary["status"] = "fail"
        except Exception as e:
            logging.exception("Crawl4aiExecutor: task %s failed", task_id)
            self.summary["status"] = "fail"
            self.summary["errors"].append(str(e)[:300])
        finally:
            self.summary["errors"] = self.summary["errors"][:20]
            CrawlerTaskService.update_by_id(task_id, {
                "last_run_status": self.summary["status"],
                "last_run_summary": self.summary,
            })
        return self.summary

    # ------------------------------------------------------------------
    # Listing
    # ------------------------------------------------------------------

    def _page_urls(self) -> List[str]:
        urls = [self.task["target_url"]]
        template = (self.task.get("page_url_template") or "").strip()
        max_pages = max(1, int(self.task.get("max_pages") or 1))
        start = int(self.task.get("start_page") or 1)
        if template and "{page}" in template:
            for p in range(start + 1, start + max_pages):
                urls.append(template.replace("{page}", str(p)))
        return urls

    def _crawl_listing(self, page_url: str) -> Optional[List[Dict]]:
        schema = self.task.get("extraction_schema") or {}
        if not schema.get("baseSelector") or not schema.get("fields"):
            raise Crawl4aiError("extraction_schema 缺少 baseSelector/fields")
        strategy = {
            "type": "JsonCssExtractionStrategy",
            "params": {"schema": schema},
        }
        try:
            results = self.client.crawl(
                [page_url],
                extraction_strategy=strategy,
                browser_headers=self.headers,
            )
        except Crawl4aiError as e:
            self.summary["errors"].append(f"listing {page_url}: {e}")
            return None
        if not results or not results[0].get("success"):
            err = (results[0].get("error_message") if results else "no result") or "unknown"
            self.summary["errors"].append(f"listing {page_url}: {err[:200]}")
            return None
        return get_extracted_items(results[0])

    # ------------------------------------------------------------------
    # Item processing
    # ------------------------------------------------------------------

    def _process_item(self, item: Dict, page_url: str):
        url_field = self.detail_cfg.get("url_field", "url")
        rel_url = (item.get(url_field) or "").strip()
        title = (item.get("title") or "").strip()
        if not rel_url or not title:
            return
        base = self.detail_cfg.get("base_url") or page_url
        detail_url = urljoin(base, rel_url)

        site_id = self.task["site_id"]
        rid = gen_result_id(site_id, detail_url)
        if CrawlerResultService.exists_id(rid):
            return  # dedup

        record = {
            "id": rid,
            "task_id": self.task["id"],
            "tenant_id": self.task["tenant_id"],
            "site_id": site_id,
            "title": title[:1024],
            "source_url": detail_url,
            "publish_date": (item.get("date") or item.get("publish_date") or "")[:64],
            "extracted_json": item,
            "attachments": [],
            "status": "raw",
            "markdown": "",
            "error_msg": "",
            "crawled_at": current_timestamp(),
        }

        # Crawl detail page for content + attachments
        markdown, attachments = "", []
        if self.detail_cfg.get("enabled", True):
            time.sleep(_REQUEST_DELAY)
            markdown, attachments, err = self._crawl_detail(detail_url)
            record["markdown"] = markdown
            record["attachments"] = attachments
            if err:
                record["error_msg"] = err[:500]
                self.summary["errors"].append(f"detail {detail_url}: {err[:200]}")

        CrawlerResultService.upsert_result(record)
        self.summary["items_new"] += 1

        # KB upload
        targets = self.task.get("output_targets") or ["db"]
        if "kb" in targets and self.task.get("kb_id"):
            self._upload_to_kb(rid, record, attachments)

    def _crawl_detail(self, detail_url: str):
        """Returns (markdown, attachments, error_msg)."""
        try:
            results = self.client.crawl(
                [detail_url],
                css_selector=self.detail_cfg.get("content_selector", ""),
                browser_headers=self.headers,
            )
        except Crawl4aiError as e:
            return "", [], str(e)
        if not results or not results[0].get("success"):
            err = (results[0].get("error_message") if results else "no result") or "unknown"
            return "", [], err
        result = results[0]
        markdown = get_markdown(result)
        attachments = self._extract_attachments(result, detail_url)
        return markdown, attachments, ""

    def _extract_attachments(self, result: Dict, detail_url: str) -> List[Dict]:
        exts = tuple(
            e.lower() for e in
            (self.detail_cfg.get("attachment_extensions") or DEFAULT_ATTACHMENT_EXTS)
        )
        seen, attachments = set(), []
        links = result.get("links") or {}
        for group in ("internal", "external"):
            for link in links.get(group) or []:
                href = (link.get("href") or "").strip() if isinstance(link, dict) else ""
                if not href:
                    continue
                path = href.split("?")[0].split("#")[0]
                if not path.lower().endswith(exts):
                    continue
                abs_url = urljoin(detail_url, href)
                if abs_url in seen:
                    continue
                seen.add(abs_url)
                text = (link.get("text") or "").strip()
                file_name = text or os.path.basename(path) or "attachment"
                suffix = os.path.splitext(path)[1].lower()
                if suffix and not file_name.lower().endswith(suffix):
                    file_name += suffix
                attachments.append({
                    "file_name": file_name[:255],
                    "file_url": abs_url,
                    "kb_doc_id": "",
                    "status": "pending",
                })
        return attachments

    # ------------------------------------------------------------------
    # KB upload
    # ------------------------------------------------------------------

    def _upload_to_kb(self, rid: str, record: Dict, attachments: List[Dict]):
        from rag.svr.crawler_engine.kb_uploader import KBUploader

        uploader = KBUploader(
            kb_id=self.task["kb_id"],
            tenant_id=self.task["tenant_id"],
            parser_id=self.task.get("parser_id") or "naive",
        )
        update: Dict[str, Any] = {}

        # 1. Markdown content
        if record.get("markdown"):
            content = (
                f"# {record['title']}\n\n"
                f"**发布日期:** {record.get('publish_date') or '-'}\n\n"
                f"**来源:** {record['source_url']}\n\n---\n\n"
                f"{record['markdown']}"
            )
            try:
                doc_id = uploader.upload_content(
                    content, display_name=_safe_filename(record["title"]) + ".md"
                )
                if doc_id:
                    update["kb_doc_id"] = doc_id
                    update["status"] = "kb_uploaded"
                    self.summary["kb_uploaded"] += 1
            except Exception as e:
                logging.error("Crawl4aiExecutor: KB upload failed for %s: %s", rid, e)
                self.summary["errors"].append(f"kb upload {record['title'][:50]}: {str(e)[:150]}")

        # 2. Attachments
        for att in attachments:
            local_path = self._download_file(att["file_url"], att["file_name"])
            if not local_path:
                att["status"] = "download_failed"
                continue
            try:
                doc_ids = uploader.upload_file(local_path, kb_filename=att["file_name"])
                if doc_ids:
                    att["kb_doc_id"] = doc_ids[0]
                    att["status"] = "uploaded"
                    self.summary["attachments_uploaded"] += 1
                else:
                    att["status"] = "upload_failed"
            except Exception as e:
                logging.error("Crawl4aiExecutor: attachment upload failed %s: %s", att["file_name"], e)
                att["status"] = "upload_failed"
            finally:
                try:
                    os.remove(local_path)
                except OSError:
                    pass
        if attachments:
            update["attachments"] = attachments

        if update:
            CrawlerResultService.update_by_id(rid, update)

    def _download_file(self, url: str, file_name: str, max_size: int = 200 * 1024 * 1024) -> Optional[str]:
        import requests

        download_dir = os.path.join(tempfile.gettempdir(), "crawl4ai_attachments")
        os.makedirs(download_dir, exist_ok=True)
        try:
            resp = requests.get(
                url, timeout=120, stream=True, verify=False,
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0"},
            )
            if resp.status_code != 200:
                logging.warning("Crawl4aiExecutor: HTTP %d downloading %s", resp.status_code, url)
                return None
            local_path = os.path.join(download_dir, _safe_filename(file_name))
            downloaded = 0
            with open(local_path, "wb") as f:
                for chunk in resp.iter_content(chunk_size=8192):
                    downloaded += len(chunk)
                    if downloaded > max_size:
                        f.close()
                        os.remove(local_path)
                        logging.warning("Crawl4aiExecutor: file too large: %s", url)
                        return None
                    f.write(chunk)
            return local_path
        except Exception as e:
            logging.error("Crawl4aiExecutor: download failed %s: %s", url, e)
            return None


def _safe_filename(name: str) -> str:
    safe = re.sub(r'[\\/:*?"<>|]', "_", name).strip(". ")
    return (safe or "document")[:200]


def run_task(task_id: str) -> Dict[str, Any]:
    """Load a crawler_task by ID and execute it. Thread-safe entrypoint."""
    ok, task = CrawlerTaskService.get_by_id(task_id)
    if not ok:
        return {"status": "fail", "errors": [f"task {task_id} not found"]}
    return Crawl4aiExecutor(task.to_dict()).run()
