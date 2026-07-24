"""
Storage pipeline — orchestrates all storage targets for crawled items.

Separates the "what to store" concern from "how to crawl":
- StructStore: bid_* database tables (via BidWriter)
- ContentStore: KB upload + parsing (via KBUploader)
- AttachmentStore: download + KB upload + link (via AttachmentHandler)

Each target is error-isolated: one failure does not block others.
"""

import logging
import os
import tempfile
from datetime import datetime
from typing import Any, Dict, List, Optional

from .models import NormalizedItem, AttachmentMeta

# Minimum content length to upload to KB.
# Items with shorter content are title-only stubs that waste parsing resources.
MIN_CONTENT_LENGTH_FOR_KB = 50


class StoragePipeline:
    """Storage pipeline — write crawled items to all targets.

    Usage:
        pipeline = StoragePipeline(kb_id="xxx", tenant_id="yyy",
                                   parser_id="general", site_id="mohurd",
                                   task_name="mohurd_爬取")
        result = pipeline.store(item)

    writer_mode:
        - "bid" (default):        写 bid_* 表族（老系统，BidWriter）
        - "collection":           写 crawler_result + 扩展表（新系统，CollectionWriter）
    """

    def __init__(self, kb_id: str, tenant_id: str, parser_id: str = "naive",
                 site_id: str = "", task_name: str = "",
                 output_dir: Optional[str] = None,
                 skip_kb: bool = False,
                 skip_attachments: bool = False,
                 writer_mode: str = "bid",
                 category: str = "bid",
                 task_id: str = "",
                 date_filter: str = "",
                 site_display: str = ""):
        self._kb_id = kb_id
        self._tenant_id = tenant_id
        self._parser_id = parser_id
        self._site_id = site_id
        self._site_display = site_display
        self._task_name = task_name
        self._output_dir = output_dir or os.path.join(
            tempfile.gettempdir(), "crawler_output", site_id
        )
        self._skip_kb = skip_kb
        self._skip_attachments = skip_attachments
        self._writer_mode = writer_mode  # bid | collection
        self._category = category        # bid|policy|personnel|news|other
        self._task_id = task_id          # CrawlerTask.id for collection writer
        self._date_filter = date_filter  # "" | "today" | "YYYY-MM-DD"

        # Lazy-initialized components
        self._bid_writer = None
        self._collection_writer = None
        self._kb_uploader = None
        self._attach_handler = None
        self._formatter = None

        self._stats = {
            "items_stored": 0,
            "bid_written": 0,
            "bid_failed": 0,
            "kb_uploaded": 0,
            "kb_failed": 0,
            "attachments_processed": 0,
            "attachments_uploaded": 0,
            "attachments_failed": 0,
        }

    def store(self, item: NormalizedItem) -> Dict[str, Any]:
        """Store one crawled item to all targets.

        Returns a result dict with project_id, doc_id, attachment results.
        """
        result = {
            "item_id": item.item_id,
            "title": item.title[:80],
            "project_id": None,
            "doc_id": None,
            "attachment_results": [],
        }

        # 1. Write to bid_* tables (structured storage)
        project_id = self._write_to_bid(item)
        result["project_id"] = project_id

        # 2. Upload content to KB + parse (knowledge base)
        #    Only upload if the DB write succeeded — this ties KB upload
        #    to the writer's date filter, dedup, and other checks.
        has_content = len(item.content or "") >= MIN_CONTENT_LENGTH_FOR_KB
        if not self._skip_kb and has_content and project_id:
            doc_id = self._upload_content_to_kb(item)
            result["doc_id"] = doc_id
        elif not has_content:
            logging.debug("StoragePipeline: skipping KB upload for '%s' (content < %d chars)",
                         item.title[:60], MIN_CONTENT_LENGTH_FOR_KB)

        # 3. Handle attachments (download + KB upload)
        # Skipped when project_id is None — that means the writer filtered the
        # item out (e.g. collection mode's date_filter dropped it).  Uploading
        # attachments for an item that wasn't stored to DB would orphan the KB
        # docs (no crawler_result row to anchor them).
        # - bid mode:        download → upload → link to bid_project_file
        # - collection mode: download → upload only (no bid_project_file coupling;
        #                    URLs are already in CrawlerResult.attachments JSON)
        if (not self._skip_attachments
                and project_id
                and item.has_attachments()):
            attach_results = self._handle_attachments(
                item, project_id,
                link_to_bid=(self._writer_mode != "collection"),
            )
            result["attachment_results"] = attach_results

        self._stats["items_stored"] += 1
        return result

    def store_batch(self, items: List[NormalizedItem]) -> List[Dict[str, Any]]:
        """Store multiple items. Returns list of result dicts."""
        return [self.store(item) for item in items]

    def store_batch_to_file(self, items: List[NormalizedItem],
                            filename: str = "") -> Optional[str]:
        """Format items as markdown and save to a file (batch mode).

        Returns the file path, or None if no items.
        """
        if not items:
            return None

        formatter = self._get_formatter()
        os.makedirs(self._output_dir, exist_ok=True)
        filepath = os.path.join(self._output_dir, filename or f"batch_{datetime.now():%Y%m%d_%H%M%S}.md")

        # Convert NormalizedItem to raw dict for formatter
        raw_items = [item.raw_data for item in items]
        formatter.write_batch(raw_items, filepath)
        return filepath

    @property
    def stats(self) -> Dict[str, int]:
        return dict(self._stats)

    # ------------------------------------------------------------------
    # Internal: bid table writes
    # ------------------------------------------------------------------

    def _write_to_bid(self, item: NormalizedItem) -> Optional[str]:
        """Write item to database tables.

        writer_mode='bid' (default)        → BidWriter → bid_* 表
        writer_mode='collection'           → CollectionWriter → crawler_result + 扩展表
        """
        if self._writer_mode == "collection":
            return self._write_to_collection(item)
        return self._write_to_bid_legacy(item)

    def _write_to_bid_legacy(self, item: NormalizedItem) -> Optional[int]:
        """Legacy writer: bid_* tables via BidWriter."""
        writer = self._get_bid_writer()
        try:
            project_id = writer.write_all(item.raw_data, self._site_id,
                                          url=item.url)
            if project_id:
                self._stats["bid_written"] += 1
            else:
                self._stats["bid_failed"] += 1
            return project_id
        except Exception as e:
            logging.error("StoragePipeline: bid write failed for %s: %s",
                          item.title[:60], e)
            self._stats["bid_failed"] += 1
            return None

    def _write_to_collection(self, item: NormalizedItem) -> Optional[str]:
        """New system writer: crawler_result + extension tables via CollectionWriter."""
        writer = self._get_collection_writer()
        try:
            result_id = writer.write_all(
                item.raw_data,
                site_id=self._site_id,
                category=self._category,
                task_id=self._task_id,
                url=item.url,
                site_display=self._site_display,
            )
            if result_id:
                self._stats["bid_written"] += 1
            else:
                self._stats["bid_failed"] += 1
            return result_id
        except Exception as e:
            logging.error("StoragePipeline: collection write failed for %s: %s",
                          item.title[:60], e)
            self._stats["bid_failed"] += 1
            return None

    # ------------------------------------------------------------------
    # Internal: KB content upload
    # ------------------------------------------------------------------

    def _upload_content_to_kb(self, item: NormalizedItem) -> Optional[str]:
        """Upload item content to KB as markdown."""
        if not item.has_content() and not item.title:
            return None

        uploader = self._get_kb_uploader()

        # Format content as markdown
        md = self._format_content_md(item)

        # Use task name as display name for first batch, item title otherwise
        display_name = item.title[:80] or "untitled"

        try:
            doc_id = uploader.upload_content(md, display_name=display_name)
            if doc_id:
                self._stats["kb_uploaded"] += 1
            else:
                self._stats["kb_failed"] += 1
            return doc_id
        except Exception as e:
            logging.error("StoragePipeline: KB upload failed for %s: %s",
                          item.title[:60], e)
            self._stats["kb_failed"] += 1
            return None

    def _format_content_md(self, item: NormalizedItem) -> str:
        """Format item content as markdown."""
        lines = []
        lines.append(f"# {item.title}")
        lines.append("")

        if item.date:
            lines.append(f"**日期:** {item.date}")
            lines.append("")

        if item.url:
            lines.append(f"**来源:** {item.url}")
            lines.append("")

        if item.source_site:
            lines.append(f"**站点:** {item.source_site}")
            lines.append("")

        if item.section:
            lines.append(f"**栏目:** {item.section}")
            lines.append("")

        # Main content (cleaned)
        if item.content:
            from .content_converter import clean_content
            cleaned = clean_content(item.content)
            if cleaned:
                lines.append(cleaned)
                lines.append("")

        # Attachment links (as references)
        if item.attachments:
            lines.append("---")
            lines.append("")
            lines.append("**附件**")
            lines.append("")
            for att in item.attachments:
                lines.append(f"- [{att.file_name}]({att.file_url})")
            lines.append("")

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Internal: attachment handling
    # ------------------------------------------------------------------

    def _handle_attachments(self, item: NormalizedItem,
                            project_id: Any,
                            link_to_bid: bool = True) -> List[Dict[str, Any]]:
        """Process all attachments for an item.

        link_to_bid=False (collection mode) skips the bid_project_file upsert.
        """
        handler = self._get_attach_handler()
        prev_uploaded = handler.stats.get("uploaded", 0)
        prev_failed = handler.stats.get("failed", 0)
        results = handler.handle(item.attachments, project_id,
                                 link_to_bid=link_to_bid)
        self._stats["attachments_processed"] += len(results)
        self._stats["attachments_uploaded"] += handler.stats.get("uploaded", 0) - prev_uploaded
        self._stats["attachments_failed"] += handler.stats.get("failed", 0) - prev_failed
        return results

    # ------------------------------------------------------------------
    # Lazy initialization
    # ------------------------------------------------------------------

    def _get_bid_writer(self):
        if self._bid_writer is None:
            from .bid_writer import BidWriter
            self._bid_writer = BidWriter(self._kb_id, self._tenant_id)
        return self._bid_writer

    def _get_collection_writer(self):
        if self._collection_writer is None:
            from .collection_writer import CollectionWriter
            self._collection_writer = CollectionWriter(
                kb_id=self._kb_id, tenant_id=self._tenant_id,
                date_filter=self._date_filter,
            )
        return self._collection_writer

    def _get_kb_uploader(self):
        if self._kb_uploader is None:
            from .kb_uploader import KBUploader
            self._kb_uploader = KBUploader(
                self._kb_id, self._tenant_id, self._parser_id
            )
        return self._kb_uploader

    def _get_attach_handler(self):
        if self._attach_handler is None:
            from .attachment_handler import AttachmentHandler
            self._attach_handler = AttachmentHandler(
                self._kb_id, self._tenant_id, self._parser_id,
                download_dir=os.path.join(self._output_dir, "attachments")
            )
        return self._attach_handler

    def _get_formatter(self):
        if self._formatter is None:
            from .formatter import MarkdownFormatter
            self._formatter = MarkdownFormatter(
                parser_id=self._parser_id,
            )
        return self._formatter

    def cleanup(self) -> None:
        """Release resources."""
        if self._attach_handler:
            self._attach_handler.cleanup()
