"""
Knowledge base upload utilities.

Encapsulates the RAGFlow KB upload + document parsing pipeline:
1. Upload markdown file via FileService.upload_document()
2. Set parser_id on the document
3. Queue document for chunking/parsing via task_service.queue_tasks()
"""

import logging
import os
from typing import List, Optional, Tuple

from common.misc_utils import get_uuid


class KBUploader:
    """Upload crawler output files to RAGFlow knowledge bases."""

    def __init__(self, kb_id: str, tenant_id: str, parser_id: str = "naive"):
        self._kb_id = kb_id
        self._tenant_id = tenant_id
        self._parser_id = parser_id

    def upload_file(self, filepath: str) -> List[str]:
        """Upload a single file to the KB. Returns list of document IDs."""
        from api.db.services.knowledgebase_service import KnowledgebaseService
        from api.db.services.file_service import FileService

        ok, kb = KnowledgebaseService.get_by_id(self._kb_id)
        if not ok:
            raise LookupError(f"Knowledge base {self._kb_id} not found")

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
        errs, pairs = FileService.upload_document(kb, [fo], self._tenant_id)
        if errs:
            logging.warning("KBUploader: upload errors for %s: %s", filepath, errs)

        doc_ids = []
        for doc, _ in pairs:
            did = doc["id"]
            doc_ids.append(did)
            self._set_parser(did)
            self._queue_parsing(doc, did)

        return doc_ids

    def _set_parser(self, doc_id: str) -> None:
        """Set the parser type on a document."""
        try:
            from api.db.services.document_service import DocumentService
            DocumentService.update_by_id(doc_id, {"parser_id": self._parser_id})
        except Exception as e:
            logging.warning("KBUploader: set parser failed for %s: %s", doc_id, e)

    def _queue_parsing(self, doc: dict, doc_id: str) -> None:
        """Queue a document for parsing/chunking."""
        try:
            from api.db.services.task_service import queue_tasks
            from api.db.services.file2document_service import File2DocumentService
            bucket, name = File2DocumentService.get_storage_address(doc_id=doc_id)
            queue_tasks(doc, bucket, name, 0)
        except Exception as e:
            logging.error("KBUploader: queue parsing failed for %s: %s", doc_id, e)

    def upload_batch_files(self, filepaths: List[str]) -> List[str]:
        """Upload multiple files to KB. Returns all document IDs."""
        all_ids = []
        for fp in filepaths:
            try:
                ids = self.upload_file(fp)
                all_ids.extend(ids)
            except Exception as e:
                logging.error("KBUploader: failed to upload %s: %s", fp, e)
        return all_ids
