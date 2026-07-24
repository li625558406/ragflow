"""
Attachment handler — downloads, extracts, uploads, and links attachments.

Handles the complete lifecycle for crawled attachments:
1. Download file from URL
2. Handle ZIP archives (extract contents)
3. Upload to RAGFlow knowledge base
4. Link uploaded document to bid_project_file record

Error-isolated: one attachment failure does not block others.
"""

import logging
import os
import re
import tempfile
import zipfile
from datetime import datetime
from io import BytesIO
from typing import Any, Dict, List, Optional

from .models import AttachmentMeta


class AttachmentHandler:
    """Download, parse, upload, and link crawled attachments.

    Usage:
        handler = AttachmentHandler(kb_id="xxx", tenant_id="yyy", parser_id="general")
        results = handler.handle(attachments, project_id=12345)
    """

    _SUPPORTED_SUFFIXES = frozenset({
        ".pdf", ".doc", ".docx", ".xls", ".xlsx",
        ".ppt", ".pptx", ".zip", ".rar", ".wps", ".et",
        ".jpg", ".jpeg", ".png", ".gif", ".bmp",
        ".txt", ".csv", ".xml", ".json",
    })

    _MAX_FILE_SIZE = 200 * 1024 * 1024  # 200MB
    _DOWNLOAD_TIMEOUT = 120  # seconds

    def __init__(self, kb_id: str, tenant_id: str,
                 parser_id: str = "general",
                 download_dir: Optional[str] = None):
        self._kb_id = kb_id
        self._tenant_id = tenant_id
        self._parser_id = parser_id
        self._download_dir = download_dir or os.path.join(
            tempfile.gettempdir(), "crawler_attachments"
        )
        self._stats = {
            "total": 0,
            "downloaded": 0,
            "uploaded": 0,
            "extracted_zips": 0,
            "failed": 0,
            "skipped": 0,
        }
        self._kb_uploader = None  # lazy init

    def handle(self, attachments: List[AttachmentMeta],
               project_id: Any,
               link_to_bid: bool = True) -> List[Dict[str, Any]]:
        """Process all attachments for a project.

        Args:
            attachments: List of attachment metadata.
            project_id: Identifier of the owning record. In bid mode this is an
                int bid_project id; in collection mode it is a str result_id.
                Only used for logging and (when link_to_bid=True) linking to
                the bid_project_file table.
            link_to_bid: When True (default, bid mode), call _link_to_project
                to upsert bid_project_file. When False (collection mode),
                skip the linking step — collection mode has no
                bid_project_file counterpart; attachments are tracked via
                CrawlerResult.attachments JSON.

        Returns list of result dicts, one per attachment.
        """
        results = []
        for att in attachments:
            self._stats["total"] += 1
            if not self._should_process(att):
                self._stats["skipped"] += 1
                results.append({
                    "file_name": att.file_name,
                    "status": "skipped",
                    "reason": "unsupported or empty URL",
                })
                continue
            try:
                result = self._process_one(att, project_id, link_to_bid=link_to_bid)
                results.append(result)
            except Exception as e:
                logging.error("AttachmentHandler: failed %s: %s", att.file_name, e)
                self._stats["failed"] += 1
                results.append({
                    "file_name": att.file_name,
                    "status": "failed",
                    "error": str(e)[:200],
                })
        return results

    @property
    def stats(self) -> Dict[str, int]:
        return dict(self._stats)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _should_process(self, att: AttachmentMeta) -> bool:
        """Check if attachment should be downloaded."""
        if not att.file_url or not att.file_url.startswith("http"):
            return False
        suffix = att.file_suffix.lower()
        if suffix and suffix not in self._SUPPORTED_SUFFIXES:
            logging.debug("AttachmentHandler: skipping unsupported %s", att.file_suffix)
            return False
        return True

    def _process_one(self, att: AttachmentMeta,
                     project_id: Any,
                     link_to_bid: bool = True) -> Dict[str, Any]:
        """Process a single attachment: download → extract(ZIP) → upload → link."""
        # 1. Download
        local_path = self._download(att.file_url, att.file_name)
        if not local_path:
            return {"file_name": att.file_name, "status": "download_failed"}

        self._stats["downloaded"] += 1

        # 2. ZIP extraction
        if att.file_suffix.lower() == ".zip":
            return self._handle_zip(local_path, att.file_name, project_id,
                                    link_to_bid=link_to_bid)

        # 3. Upload to KB
        doc_id = self._upload_to_kb(local_path, att.file_name)
        if doc_id:
            self._stats["uploaded"] += 1
        else:
            return {"file_name": att.file_name, "status": "upload_failed"}

        # 4. Link to bid_project_file (bid mode only)
        if link_to_bid:
            self._link_to_project(project_id, att, doc_id)

        # 5. Clean up temp file
        try:
            os.remove(local_path)
        except OSError:
            pass

        return {
            "file_name": att.file_name,
            "status": "done",
            "doc_id": doc_id,
            "project_id": project_id,
        }

    def _download(self, url: str, file_name: str) -> Optional[str]:
        """Download a file to a temp directory."""
        os.makedirs(self._download_dir, exist_ok=True)

        try:
            import requests
            resp = requests.get(
                url,
                timeout=self._DOWNLOAD_TIMEOUT,
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0"},
                stream=True,
                verify=False,
            )
            if resp.status_code != 200:
                logging.warning("AttachmentHandler: HTTP %d for %s", resp.status_code, url)
                return None

            # Check size limit
            content_length = int(resp.headers.get("Content-Length", 0))
            if content_length > self._MAX_FILE_SIZE:
                logging.warning("AttachmentHandler: file too large (%d bytes): %s",
                               content_length, url)
                return None

            local_path = os.path.join(self._download_dir,
                                      _safe_filename(file_name))
            with open(local_path, "wb") as f:
                downloaded = 0
                for chunk in resp.iter_content(chunk_size=8192):
                    downloaded += len(chunk)
                    if downloaded > self._MAX_FILE_SIZE:
                        logging.warning("AttachmentHandler: stream exceeded %d bytes: %s",
                                       self._MAX_FILE_SIZE, url)
                        f.close()
                        os.remove(local_path)
                        return None
                    f.write(chunk)

            return local_path
        except Exception as e:
            logging.error("AttachmentHandler: download failed %s: %s", url, e)
            return None

    def _handle_zip(self, zip_path: str, zip_name: str,
                    project_id: Any,
                    link_to_bid: bool = True) -> Dict[str, Any]:
        """Extract ZIP and upload each contained file to KB."""
        self._stats["extracted_zips"] += 1
        doc_ids = []
        extract_dir = zip_path.replace(".zip", "_extracted")
        os.makedirs(extract_dir, exist_ok=True)

        try:
            with zipfile.ZipFile(zip_path, "r") as zf:
                for name in zf.namelist():
                    # Skip hidden files and directories
                    if name.startswith("__MACOSX") or name.startswith("."):
                        continue
                    if name.endswith("/"):
                        continue
                    # Only extract supported file types
                    suffix = os.path.splitext(name)[1].lower()
                    if suffix and suffix not in self._SUPPORTED_SUFFIXES:
                        continue

                    extracted_path = os.path.join(extract_dir, name)
                    os.makedirs(os.path.dirname(extracted_path), exist_ok=True)
                    with zf.open(name) as src, open(extracted_path, "wb") as dst:
                        dst.write(src.read())

                    # Upload extracted file
                    base_name = os.path.basename(name)
                    doc_id = self._upload_to_kb(extracted_path, base_name)
                    if doc_id:
                        doc_ids.append(doc_id)
                        self._stats["uploaded"] += 1

                    # Clean up extracted file
                    try:
                        os.remove(extracted_path)
                    except OSError:
                        pass
        except zipfile.BadZipFile:
            logging.error("AttachmentHandler: bad ZIP file: %s", zip_name)
            return {"file_name": zip_name, "status": "bad_zip", "project_id": project_id}
        except Exception as e:
            logging.error("AttachmentHandler: ZIP extraction failed %s: %s", zip_name, e)
            return {"file_name": zip_name, "status": "zip_error", "error": str(e)[:200],
                    "project_id": project_id}

        # Clean up
        try:
            os.remove(zip_path)
            import shutil
            shutil.rmtree(extract_dir, ignore_errors=True)
        except OSError:
            pass

        return {
            "file_name": zip_name,
            "status": "extracted",
            "doc_ids": doc_ids,
            "file_count": len(doc_ids),
            "project_id": project_id,
        }

    def _upload_to_kb(self, filepath: str, display_name: str) -> Optional[str]:
        """Upload a file to the knowledge base. Returns doc_id or None."""
        try:
            uploader = self._get_kb_uploader()
            doc_ids = uploader.upload_file(filepath, kb_filename=display_name)
            return doc_ids[0] if doc_ids else None
        except Exception as e:
            logging.error("AttachmentHandler: KB upload failed for %s: %s", display_name, e)
            return None

    def _link_to_project(self, project_id: int, att: AttachmentMeta,
                         doc_id: Optional[str]) -> None:
        """Update bid_project_file record with KB document ID."""
        if not doc_id:
            return
        try:
            from api.db.services.bid_service import BidProjectFileService
            from .bid_writer import gen_file_id

            file_id = gen_file_id(att.file_url, project_id)
            BidProjectFileService.upsert_file({
                "project_file_id": file_id,
                "project_id": project_id,
                "file_name": att.file_name[:500],
                "file_url": att.file_url[:1000],
                "file_suffix": att.file_suffix[:20],
                "file_size": att.file_size,
                "state": "1",
                "kb_document_id": doc_id,
                "downloaded_at": datetime.now(),
                "created_at": datetime.now(),
            })
            logging.debug("AttachmentHandler: linked %s → doc %s", att.file_name, doc_id)
        except Exception as e:
            logging.warning("AttachmentHandler: failed to link %s: %s", att.file_name, e)

    def _get_kb_uploader(self):
        """Lazy-init KB uploader."""
        if self._kb_uploader is None:
            from .kb_uploader import KBUploader
            self._kb_uploader = KBUploader(
                self._kb_id, self._tenant_id, self._parser_id
            )
        return self._kb_uploader

    def cleanup(self) -> None:
        """Clean up temp download directory."""
        try:
            import shutil
            if os.path.exists(self._download_dir):
                shutil.rmtree(self._download_dir)
        except Exception as e:
            logging.debug("AttachmentHandler: cleanup failed: %s", e)


def _safe_filename(name: str) -> str:
    """Convert a filename to a safe local filename."""
    # Remove path separators and problematic chars
    safe = re.sub(r'[\\/:*?"<>|]', '_', name)
    safe = safe.strip(". ")
    if not safe:
        safe = "attachment"
    return safe[:200]
