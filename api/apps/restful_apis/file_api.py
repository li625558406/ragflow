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
import re
import io
import json
import struct
import subprocess
import tempfile
import os
import zipfile

from lxml import etree
from quart import request, make_response
from api.apps import login_required
from api.db import FileType
from api.db.services.file2document_service import File2DocumentService
from api.utils.api_utils import (
    add_tenant_id_to_kwargs,
    get_error_argument_result,
    get_error_data_result,
    get_json_result,
    get_result,
)
from common.constants import RetCode
from api.utils.validation_utils import (
    CreateFolderReq,
    DeleteFileReq,
    ListFileReq,
    MoveFileReq,
    validate_and_parse_json_request,
    validate_and_parse_request_args,
)
from api.utils.web_utils import CONTENT_TYPE_MAP, apply_safe_file_response_headers
from common import settings
from common.misc_utils import thread_pool_exec
from api.apps.services import file_api_service


@manager.route("/files", methods=["POST"])  # noqa: F821
@login_required
@add_tenant_id_to_kwargs
async def create_or_upload(tenant_id: str = None):
    """
    Upload files or create a folder.
    ---
    tags:
      - Files
    security:
      - ApiKeyAuth: []
    parameters:
      - in: header
        name: Authorization
        type: string
        required: true
        description: Bearer token for authentication.
    responses:
      200:
        description: Successful operation.
    """
    content_type = request.content_type or ""
    try:
        if "multipart/form-data" in content_type:
            form = await request.form
            pf_id = form.get("parent_id")
            files = await request.files
            if 'file' not in files:
                return get_error_argument_result("No file part!")
            file_objs = files.getlist('file')
            for file_obj in file_objs:
                if file_obj.filename == '':
                    return get_error_argument_result("No file selected!")

            success, result = await file_api_service.upload_file(tenant_id, pf_id, file_objs)
            if success:
                return get_result(data=result)
            else:
                return get_error_data_result(message=result)
        else:
            req, err = await validate_and_parse_json_request(request, CreateFolderReq)
            if err is not None:
                return get_error_argument_result(err)

            success, result = await file_api_service.create_folder(
                tenant_id, req["name"], req.get("parent_id"), req.get("type")
            )
            if success:
                return get_result(data=result)
            else:
                return get_error_data_result(message=result)
    except Exception as e:
        logging.exception(e)
        return get_error_data_result(message="Internal server error")


@manager.route("/files", methods=["GET"])  # noqa: F821
@login_required
@add_tenant_id_to_kwargs
async def list_files(tenant_id: str = None):
    """
    List files under a folder.
    ---
    tags:
      - Files
    security:
      - ApiKeyAuth: []
    parameters:
      - in: query
        name: parent_id
        type: string
        description: Folder ID to list files from.
      - in: query
        name: keywords
        type: string
        description: Search keyword filter.
      - in: query
        name: page
        type: integer
        default: 1
      - in: query
        name: page_size
        type: integer
        default: 15
      - in: query
        name: orderby
        type: string
        default: "create_time"
      - in: query
        name: desc
        type: boolean
        default: true
    responses:
      200:
        description: Successful operation.
    """
    args, err = validate_and_parse_request_args(request, ListFileReq)
    if err is not None:
        return get_error_argument_result(err)

    try:
        success, result = file_api_service.list_files(tenant_id, args)
        if success:
            return get_result(data=result)
        else:
            return get_error_data_result(message=result)
    except Exception as e:
        logging.exception(e)
        return get_error_data_result(message="Internal server error")


@manager.route("/files", methods=["DELETE"])  # noqa: F821
@login_required
@add_tenant_id_to_kwargs
async def delete(tenant_id: str = None):
    """
    Delete files.
    ---
    tags:
      - Files
    security:
      - ApiKeyAuth: []
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required:
            - ids
          properties:
            ids:
              type: array
              items:
                type: string
              description: List of file IDs to delete.
    responses:
      200:
        description: Successful operation.
    """
    req, err = await validate_and_parse_json_request(request, DeleteFileReq)
    if err is not None:
        return get_error_argument_result(err)

    try:
        # Get Authorization header to pass to Go backend
        auth_header = request.headers.get("Authorization", "")
        success, result = await file_api_service.delete_files(tenant_id, req["ids"], auth_header)
        if success:
            return get_result(data=result)
        else:
            if isinstance(result, dict):
                success_count = result.get("success_count", 0)
                errors = result.get("errors", [])
                return get_json_result(
                    code=RetCode.DATA_ERROR,
                    message=f"Partially deleted {success_count} files with {len(errors)} errors"
                    if success_count > 0
                    else f"Deleted files failed with {len(errors)} errors",
                    data=result,
                )
            return get_error_data_result(message=result)
    except Exception as e:
        logging.exception(e)
        return get_error_data_result(message="Internal server error")



@manager.route("/files/move", methods=["POST"])  # noqa: F821
@login_required
@add_tenant_id_to_kwargs
async def move(tenant_id: str = None):
    """
    Move and/or rename files. Follows Linux mv semantics:
    at least one of dest_file_id or new_name must be provided.
    - dest_file_id only: move files to a new folder (names unchanged).
    - new_name only: rename a single file in place (no storage operation).
    - both: move and rename simultaneously.
    ---
    tags:
      - Files
    security:
      - ApiKeyAuth: []
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required:
            - src_file_ids
          properties:
            src_file_ids:
              type: array
              items:
                type: string
              description: List of source file IDs. Required.
            dest_file_id:
              type: string
              description: Destination folder ID. Optional; omit to rename in place.
            new_name:
              type: string
              description: New file name. Optional; only valid for a single source file.
    responses:
      200:
        description: Successful operation.
    """
    req, err = await validate_and_parse_json_request(request, MoveFileReq)
    if err is not None:
        return get_error_argument_result(err)

    try:
        success, result = await file_api_service.move_files(
            tenant_id, req["src_file_ids"], req.get("dest_file_id"), req.get("new_name")
        )
        if success:
            return get_result(data=result)
        else:
            return get_error_data_result(message=result)
    except Exception as e:
        logging.exception(e)
        return get_error_data_result(message="Internal server error")


@manager.route("/files/<file_id>", methods=["GET"])  # noqa: F821
@login_required
@add_tenant_id_to_kwargs
async def download(tenant_id: str = None, file_id: str = None):
    """
    Download a file.
    ---
    tags:
      - Files
    security:
      - ApiKeyAuth: []
    produces:
      - application/octet-stream
    parameters:
      - in: path
        name: file_id
        type: string
        required: true
        description: File ID to download.
    responses:
      200:
        description: File stream.
    """
    try:
        success, result = file_api_service.get_file_content(tenant_id, file_id)
        if not success:
            return get_error_data_result(message=result)

        file = result
        blob = await thread_pool_exec(settings.STORAGE_IMPL.get, file.parent_id, file.location)
        if not blob:
            b, n = File2DocumentService.get_storage_address(file_id=file_id)
            blob = await thread_pool_exec(settings.STORAGE_IMPL.get, b, n)

        response = await make_response(blob)
        ext = re.search(r"\.([^.]+)$", file.name.lower())
        ext = ext.group(1) if ext else None
        content_type = None
        if ext:
            fallback_prefix = "image" if file.type == FileType.VISUAL.value else "application"
            content_type = CONTENT_TYPE_MAP.get(ext, f"{fallback_prefix}/{ext}")
        apply_safe_file_response_headers(response, content_type, ext)
        return response
    except Exception as e:
        logging.exception(e)
        return get_error_data_result(message="Internal server error")


@manager.route("/files/<file_id>/parent", methods=["GET"])  # noqa: F821
@login_required
@add_tenant_id_to_kwargs
async def parent_folder(tenant_id: str = None, file_id: str = None):
    """
    Get parent folder of a file.
    ---
    tags:
      - Files
    security:
      - ApiKeyAuth: []
    parameters:
      - in: path
        name: file_id
        type: string
        required: true
    responses:
      200:
        description: Parent folder information.
    """
    try:
        success, result = file_api_service.get_parent_folder(file_id)
        if success:
            return get_result(data=result)
        else:
            return get_error_data_result(message=result)
    except Exception as e:
        logging.exception(e)
        return get_error_data_result(message="Internal server error")


@manager.route("/files/<file_id>/ancestors", methods=["GET"])  # noqa: F821
@login_required
@add_tenant_id_to_kwargs
async def ancestors(tenant_id: str = None, file_id: str = None):
    """
    Get all ancestor folders of a file.
    ---
    tags:
      - Files
    security:
      - ApiKeyAuth: []
    parameters:
      - in: path
        name: file_id
        type: string
        required: true
    responses:
      200:
        description: List of ancestor folders.
    """
    try:
        success, result = file_api_service.get_all_parent_folders(file_id)
        if success:
            return get_result(data=result)
        else:
            return get_error_data_result(message=result)
    except Exception as e:
        logging.exception(e)
        return get_error_data_result(message="Internal server error")



# ── .doc (OLE2) text extraction ──

OLE2_MAGIC = b'\xd0\xcf\x11\xe0'


def _is_doc_file(blob: bytes, filename: str = "") -> bool:
    """Check whether a file is an old-format .doc (OLE2 compound document)."""
    if filename.lower().endswith(".doc") and not filename.lower().endswith(".docx"):
        return True
    if blob and blob[:4] == OLE2_MAGIC:
        return True
    return False


def _doc_to_docx_via_libreoffice(binary: bytes) -> bytes | None:
    """Convert .doc binary to .docx binary using LibreOffice headless.

    Returns the converted .docx bytes, or None if LibreOffice is unavailable
    or conversion fails.
    """
    import shutil

    soffice = shutil.which("libreoffice") or shutil.which("soffice")
    if not soffice:
        return None

    tmp_in_dir = tempfile.mkdtemp(prefix="doc2docx_in_")
    tmp_out_dir = tempfile.mkdtemp(prefix="doc2docx_out_")
    try:
        in_path = os.path.join(tmp_in_dir, "input.doc")
        with open(in_path, "wb") as f:
            f.write(binary)

        result = subprocess.run(
            [soffice, "--headless", "--norestore", "--nologo",
             "--convert-to", "docx", "--outdir", tmp_out_dir, in_path],
            capture_output=True, timeout=60,
        )

        out_path = os.path.join(tmp_out_dir, "input.docx")
        if result.returncode == 0 and os.path.exists(out_path):
            with open(out_path, "rb") as f:
                return f.read()
    except (subprocess.TimeoutExpired, OSError) as e:
        logging.warning(f"[.doc] LibreOffice conversion failed: {e}")
    finally:
        import shutil as shutil_mod
        shutil_mod.rmtree(tmp_in_dir, ignore_errors=True)
        shutil_mod.rmtree(tmp_out_dir, ignore_errors=True)
    return None


def _try_subprocess_extractor(binary: bytes, cmd: list[str]) -> str | None:
    """Write binary to temp file, run a text extraction command, return stdout."""
    try:
        fd, tmp_path = tempfile.mkstemp(suffix=".doc")
        try:
            os.write(fd, binary)
            os.close(fd)
            result = subprocess.run(
                cmd + [tmp_path],
                capture_output=True, text=True, timeout=30,
            )
            if result.returncode == 0 and result.stdout.strip():
                return result.stdout
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        pass
    return None


def _extract_text_from_doc(binary: bytes) -> list[dict]:
    """Extract paragraph-level text from a .doc binary (OLE2 format).

    Tries antiword, then catdoc, then olefile FIB parsing as last resort.
    Returns list of {"index", "text", "type"} dicts.
    """
    text = (
        _try_subprocess_extractor(binary, ["antiword", "-m", "UTF-8.txt"])
        or _try_subprocess_extractor(binary, ["catdoc"])
    )

    if not text:
        try:
            text = _extract_text_from_doc_olefile(binary)
        except Exception as e:
            logging.warning(f"[.doc] olefile fallback failed: {e}")

    if not text:
        return []

    # Clean and split into paragraphs
    text = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]', '', text)
    lines = text.split("\n")
    paragraphs = []
    idx = 0
    for ln in lines:
        stripped = ln.strip()
        if stripped:
            paragraphs.append({"index": idx, "text": stripped, "type": "paragraph", "page": 0})
            idx += 1
    return paragraphs


def _extract_text_from_doc_olefile(binary: bytes) -> str:
    """Fallback .doc extraction using olefile + FIB header parsing.

    Implements the key parts of [MS-DOC] to locate the document text
    within the WordDocument OLE2 stream.
    """
    import olefile

    ole = olefile.OleFileIO(io.BytesIO(binary))
    try:
        word_stream = ole.openstream("WordDocument").read()

        if len(word_stream) < 0x20 or word_stream[:2] != b'\xec\xa5':
            return ""

        flags = struct.unpack_from('<H', word_stream, 0x000A)[0]
        fComplex = bool(flags & 0x0004)
        ccpText = struct.unpack_from('<I', word_stream, 0x0018)[0]

        if ccpText == 0:
            return ""

        # Walk FIB → fibRgFcLcbBlob → fcClx
        # Isolate FIB walk: malformed csw/cslw could push offset past buffer
        try:
            offset = 0x0020
            csw = struct.unpack_from('<H', word_stream, offset)[0]
            offset += 2 + csw * 2
            cslw = struct.unpack_from('<H', word_stream, offset)[0]
            offset += 2 + cslw * 4
            cbRgFcLcb = struct.unpack_from('<H', word_stream, offset)[0]
            offset += 2
            blob_start = offset
        except struct.error:
            logging.warning("[.doc] FIB walk failed: struct.error")
            return _doc_heuristic_fallback(word_stream, fComplex)

        # fcClx is at pair index 33 in FibRgFcLcb97 (byte offset 0x01A2 in FIB,
        # relative to blob_start 0x009A for standard csw=14/cslw=22)
        fcClx_idx = 33
        text_result = None
        if cbRgFcLcb > fcClx_idx:
            try:
                entry_off = blob_start + fcClx_idx * 8
                if entry_off + 8 <= len(word_stream):
                    fcClx = struct.unpack_from('<I', word_stream, entry_off)[0]
                    lcbClx = struct.unpack_from('<I', word_stream, entry_off + 4)[0]

                    table_stream = b""
                    try:
                        table_stream = ole.openstream("1Table").read()
                    except Exception:
                        try:
                            table_stream = ole.openstream("0Table").read()
                        except Exception:
                            pass

                    if table_stream and fcClx + lcbClx <= len(table_stream) and lcbClx > 0:
                        text_result = _parse_clx_to_text(
                            table_stream[fcClx:fcClx + lcbClx],
                            word_stream,
                        )
            except Exception as e:
                logging.warning(f"[.doc] Pcdt parse error: {e}")

        if text_result:
            return text_result

        return _doc_heuristic_fallback(word_stream, fComplex)
    finally:
        ole.close()


def _parse_clx_to_text(clx: bytes, word_stream: bytes) -> str:
    """Parse a Clx structure from the table stream and extract document text.

    Clx = sequence of Prc (type 0x01) entries followed by one Pcdt (type 0x02).
    Pcdt contains a PlcPcd: CP array (n+1 uint32) + Pcd array (n × 8 bytes).
    Each Pcd has fc (FcCompressed) at byte offset 2.
    """
    pos = 0
    while pos < len(clx):
        clxt = clx[pos]
        if clxt == 0x01:  # Prc — skip
            if pos + 3 > len(clx):
                break
            cbGrpprl = struct.unpack_from('<H', clx, pos + 1)[0]
            pos += 3 + cbGrpprl
        elif clxt == 0x02:  # Pcdt
            if pos + 5 > len(clx):
                break
            lcb_pcdt = struct.unpack_from('<I', clx, pos + 1)[0]
            pos += 5
            pcdt_data = clx[pos:pos + lcb_pcdt]
            if not pcdt_data:
                break

            # PlcPcd: (n+1) CPs (uint32) + n Pcds (8 bytes each)
            # Total size = (n+1)*4 + n*8 = 12n + 4
            n = (len(pcdt_data) - 4) // 12
            if n <= 0:
                break

            cp_end = (n + 1) * 4
            pcd_start = cp_end

            text_parts = []
            for i in range(n):
                cp_off = i * 4
                cp_next_off = (i + 1) * 4
                if cp_next_off + 4 > len(pcdt_data):
                    break
                cp_start = struct.unpack_from('<I', pcdt_data, cp_off)[0]
                cp_end_val = struct.unpack_from('<I', pcdt_data, cp_next_off)[0]
                char_count = cp_end_val - cp_start
                if char_count <= 0:
                    continue

                pcd_off = pcd_start + i * 8
                if pcd_off + 8 > len(pcdt_data):
                    break
                # fc (FcCompressed) at Pcd byte offset 2
                fc_c = struct.unpack_from('<I', pcdt_data, pcd_off + 2)[0]
                fc_raw = fc_c & 0x3FFFFFFF
                is_compressed = (fc_c >> 30) & 1

                if is_compressed:
                    # CP1252: byte offset = fc_raw / 2, 1 byte per char
                    byte_offset = fc_raw // 2
                    byte_count = char_count
                    encoding = "cp1252"
                else:
                    # UTF-16LE: byte offset = fc_raw, 2 bytes per char
                    byte_offset = fc_raw
                    byte_count = char_count * 2
                    encoding = "utf-16-le"

                if byte_offset + byte_count > len(word_stream):
                    byte_count = max(0, len(word_stream) - byte_offset)
                if byte_count > 0:
                    chunk = word_stream[byte_offset:byte_offset + byte_count]
                    text_parts.append(chunk.decode(encoding, errors="replace"))

            return "".join(text_parts)
        else:
            break
    return ""


def _doc_heuristic_fallback(word_stream: bytes, fComplex: bool) -> str:
    """Last-resort heuristic: scan WordDocument stream tail for readable text."""
    start = min(len(word_stream), 0x800)
    tail = word_stream[start:]

    if fComplex:
        decoded = tail.decode("utf-16-le", errors="ignore")
    else:
        decoded = tail.decode("cp1252", errors="ignore")

    # Keep runs of printable text (Chinese, alphanumeric, common punctuation)
    chunks = re.findall(
        r'[\u4e00-\u9fff\u3000-\u303f\uff00-\uffefa-zA-Z0-9\s.,;:!?()\[\]{}\-\'"@#$%^&*+=/\\<>|~`]{4,}',
        decoded,
    )
    return "\n".join(chunks)


@manager.route("/files/<file_id>/content", methods=["GET"])  # noqa: F821
@login_required
@add_tenant_id_to_kwargs
async def get_content(tenant_id: str = None, file_id: str = None):
    """
    Get structured content of a file (paragraph-level for docx).
    ---
    tags:
      - Files
    security:
      - ApiKeyAuth: []
    produces:
      - application/json
    parameters:
      - in: path
        name: file_id
        type: string
        required: true
        description: File ID.
    responses:
      200:
        description: Structured file content with paragraph-level detail.
    """
    try:
        filename = file_id  # fallback
        blob = None
        file = None

        # Try DB lookup first (knowledge-base files)
        success, result = file_api_service.get_file_content(tenant_id, file_id)
        if success:
            file = result
            filename = file.name
            blob = await thread_pool_exec(settings.STORAGE_IMPL.get, file.parent_id, file.location)
            if not blob:
                b, n = File2DocumentService.get_storage_address(file_id=file_id)
                blob = await thread_pool_exec(settings.STORAGE_IMPL.get, b, n)

        # Fallback: chat-uploaded files (stored directly in MinIO, no DB record)
        if not blob:
            bname = f"{tenant_id}-downloads"
            blob = await thread_pool_exec(settings.STORAGE_IMPL.get, bname, file_id)

        if not blob:
            return get_error_data_result(message="File content not found in storage")

        # Detect docx by magic bytes (ZIP) when filename has no extension (chat uploads)
        is_docx = (filename or "").lower().endswith(".docx")
        if not is_docx and blob[:2] == b'PK':
            is_docx = True
            filename = (filename or file_id) + ".docx"

        if is_docx:
            try:
                from rag.app.naive import Docx
                d = Docx()
                paragraphs = await thread_pool_exec(d.to_paragraphs, binary=blob)
                return get_result(data={
                    "filename": filename,
                    "file_type": "docx",
                    "paragraphs": paragraphs,
                })
            except Exception as e:
                logging.exception(e)
                return get_error_data_result(message=f"Failed to parse docx: {e}")

        # Detect .doc (OLE2 compound document)
        if _is_doc_file(blob, filename):
            try:
                # Best path: convert .doc → .docx via LibreOffice (full formatting)
                docx_blob = await thread_pool_exec(_doc_to_docx_via_libreoffice, blob)
                if docx_blob:
                    from rag.app.naive import Docx
                    d = Docx()
                    paragraphs = await thread_pool_exec(d.to_paragraphs, binary=docx_blob)
                    return get_result(data={
                        "filename": filename,
                        "file_type": "doc",
                        "paragraphs": paragraphs,
                    })
                # Fallback: plain text extraction (no formatting)
                paragraphs = await thread_pool_exec(_extract_text_from_doc, blob)
                return get_result(data={
                    "filename": filename,
                    "file_type": "doc",
                    "paragraphs": paragraphs,
                })
            except Exception as e:
                logging.exception(e)
                return get_error_data_result(message=f"Failed to parse .doc: {e}")

        # Fallback: plain text split by paragraphs
        try:
            text = blob.decode("utf-8", errors="replace")
        except Exception:
            text = str(blob)
        lines = text.split("\n")
        paragraphs = [
            {"index": i, "text": ln.strip(), "type": "paragraph", "page": 0}
            for i, ln in enumerate(lines) if ln.strip()
        ]
        return get_result(data={
            "filename": filename,
            "file_type": "text",
            "paragraphs": paragraphs,
        })
    except Exception as e:
        logging.exception(e)
        return get_error_data_result(message="Internal server error")


@manager.route("/files/<file_id>/annotate", methods=["POST"])  # noqa: F821
@login_required
@add_tenant_id_to_kwargs
async def annotate_file(tenant_id: str = None, file_id: str = None):
    """
    Generate a .docx file with Word comments injected from structured annotations.
    ---
    tags:
      - Files
    security:
      - ApiKeyAuth: []
    produces:
      - application/vnd.openxmlformats-officedocument.wordprocessingml.document
    parameters:
      - in: path
        name: file_id
        type: string
        required: true
        description: File ID to annotate.
      - in: body
        name: body
        required: true
        schema:
          type: object
          required:
            - annotations
          properties:
            annotations:
              type: array
              items:
                type: object
              description: List of annotation objects with matched_text, type, severity, issue, suggestion.
    responses:
      200:
        description: Annotated .docx file stream for download.
    """
    try:
        blob = None
        filename = file_id  # fallback

        # Try DB lookup first (knowledge-base files)
        success, result = file_api_service.get_file_content(tenant_id, file_id)
        if success:
            file = result
            filename = file.name
            ext = (filename or "").lower()
            if not (ext.endswith(".docx") or ext.endswith(".doc")):
                return get_error_data_result(message="Only .docx and .doc files are supported for annotation download")
            blob = await thread_pool_exec(settings.STORAGE_IMPL.get, file.parent_id, file.location)
            if not blob:
                b, n = File2DocumentService.get_storage_address(file_id=file_id)
                blob = await thread_pool_exec(settings.STORAGE_IMPL.get, b, n)

        # Fallback: chat-uploaded files (stored directly in MinIO, no DB record)
        if not blob:
            bname = f"{tenant_id}-downloads"
            blob = await thread_pool_exec(settings.STORAGE_IMPL.get, bname, file_id)
            if blob and blob[:2] != b'PK':
                if not _is_doc_file(blob):
                    return get_error_data_result(message="Only .docx and .doc files are supported for annotation download")
            if blob:
                filename = file_id + ".docx"

        if not blob:
            return get_error_data_result(message="File content not found in storage")

        req_data = await request.get_json()
        if not req_data:
            return get_error_argument_result("Request body is required")

        annotations = req_data.get("annotations", [])
        if not annotations:
            return get_error_argument_result("annotations is required and cannot be empty")

        # If .doc (not ZIP/PK), convert to .docx via LibreOffice first
        if blob[:2] != b'PK':
            docx_blob = await thread_pool_exec(_doc_to_docx_via_libreoffice, blob)
            if not docx_blob:
                return get_error_data_result(
                    message="Failed to convert .doc to .docx for annotation. "
                            "LibreOffice may be unavailable. Please use .docx format."
                )
            blob = docx_blob

        new_docx = await thread_pool_exec(_inject_docx_comments, blob, annotations)

        response = await make_response(new_docx)
        response.headers["Content-Type"] = (
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        )
        safe_filename = filename if filename.endswith(".docx") else filename + ".docx"
        response.headers["Content-Disposition"] = (
            f'attachment; filename="annotated_{safe_filename}"'
        )
        return response
    except Exception as e:
        logging.exception(e)
        return get_error_data_result(message="Internal server error")


# ── OOXML namespace constants ──
_W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_CT_NS = "http://schemas.openxmlformats.org/package/2006/content-types"


def _inject_docx_comments(docx_bytes: bytes, annotations: list) -> bytes:
    """
    Inject Word comments into a .docx file based on matched_text annotations.

    Opens the docx as a ZIP, finds paragraphs matching each annotation's
    matched_text, injects commentRangeStart/End/Reference markers, creates
    word/comments.xml with the annotation content, and updates relationships
    and content types.
    """
    input_buf = io.BytesIO(docx_bytes)
    output_buf = io.BytesIO()

    with zipfile.ZipFile(input_buf, "r") as in_z, zipfile.ZipFile(output_buf, "w", zipfile.ZIP_DEFLATED) as out_z:
        # ── Load document.xml ──
        doc_xml_raw = in_z.read("word/document.xml")
        if doc_xml_raw[:3] == b"\xef\xbb\xbf":
            doc_xml_raw = doc_xml_raw[3:]
        doc_xml = etree.fromstring(doc_xml_raw)

        body = doc_xml.find(f"{{{_W_NS}}}body")
        if body is None:
            raise ValueError("Invalid docx: no body element found")

        # ── Load or create comments.xml ──
        next_id = 0
        try:
            comments_raw = in_z.read("word/comments.xml")
            if comments_raw[:3] == b"\xef\xbb\xbf":
                comments_raw = comments_raw[3:]
            comments_et = etree.fromstring(comments_raw)
            for c in comments_et.findall(f"{{{_W_NS}}}comment"):
                cid = int(c.get(f"{{{_W_NS}}}id", 0))
                if cid >= next_id:
                    next_id = cid + 1
        except KeyError:
            comments_et = etree.Element(
                f"{{{_W_NS}}}comments",
                nsmap={"w": _W_NS, "r": _R_NS},
            )

        # ── Collect all paragraphs once ──
        all_paras = body.findall(f".//{{{_W_NS}}}p")

        for ann in annotations:
            matched_text = (ann.get("matched_text") or "").strip()
            if not matched_text:
                continue

            comment_id = next_id
            next_id += 1

            # Build comment content
            severity = (ann.get("severity") or "").upper()
            ann_type = ann.get("type", "")
            issue = ann.get("issue", "")
            suggestion = ann.get("suggestion", "")

            comment_lines = []
            if severity:
                comment_lines.append(f"[{severity}] {ann_type}: {issue}")
            else:
                comment_lines.append(f"{ann_type}: {issue}")
            if suggestion:
                comment_lines.append(f"建议: {suggestion}")

            # ── Build comment XML element ──
            cmt = etree.SubElement(comments_et, f"{{{_W_NS}}}comment")
            cmt.set(f"{{{_W_NS}}}id", str(comment_id))
            cmt.set(f"{{{_W_NS}}}author", "AI Reviewer")

            for line in comment_lines:
                cp = etree.SubElement(cmt, f"{{{_W_NS}}}p")
                cp_r = etree.SubElement(cp, f"{{{_W_NS}}}r")
                rpr = etree.SubElement(cp_r, f"{{{_W_NS}}}rPr")
                if severity == "HIGH":
                    etree.SubElement(rpr, f"{{{_W_NS}}}color").set(f"{{{_W_NS}}}val", "FF0000")
                    etree.SubElement(rpr, f"{{{_W_NS}}}b")
                elif severity == "MEDIUM":
                    etree.SubElement(rpr, f"{{{_W_NS}}}color").set(f"{{{_W_NS}}}val", "FF8C00")
                cp_t = etree.SubElement(cp_r, f"{{{_W_NS}}}t")
                cp_t.text = line
                cp_t.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")

            # ── Find matching paragraph and inject markers ──
            best_para = None
            best_text = ""
            for p in all_paras:
                runs = p.findall(f".//{{{_W_NS}}}r")
                p_text = "".join(
                    "".join(t.text or "" for t in r.findall(f"{{{_W_NS}}}t"))
                    for r in runs
                )
                if matched_text in p_text:
                    # Prefer the shortest text that contains matched_text (most precise paragraph)
                    if best_para is None or len(p_text) < len(best_text):
                        best_para = p
                        best_text = p_text

            if best_para is not None:
                # Inject commentRangeStart before first child
                crs = etree.Element(f"{{{_W_NS}}}commentRangeStart")
                crs.set(f"{{{_W_NS}}}id", str(comment_id))
                best_para.insert(0, crs)

                # Inject commentRangeEnd after last child
                cre = etree.Element(f"{{{_W_NS}}}commentRangeEnd")
                cre.set(f"{{{_W_NS}}}id", str(comment_id))
                best_para.append(cre)

                # Inject commentReference as a new run at end
                ref_run = etree.SubElement(best_para, f"{{{_W_NS}}}r")
                ref_rpr = etree.SubElement(ref_run, f"{{{_W_NS}}}rPr")
                hl = etree.SubElement(ref_rpr, f"{{{_W_NS}}}highlight")
                hl.set(f"{{{_W_NS}}}val", "yellow")
                ref = etree.SubElement(ref_run, f"{{{_W_NS}}}commentReference")
                ref.set(f"{{{_W_NS}}}id", str(comment_id))

        # ── Copy all items except the ones we modify ──
        for item in in_z.infolist():
            if item.filename in ("word/document.xml", "word/comments.xml",
                                 "[Content_Types].xml", "word/_rels/document.xml.rels"):
                continue
            out_z.writestr(item, in_z.read(item.filename))

        # ── Write modified document.xml ──
        out_z.writestr("word/document.xml",
                       etree.tostring(doc_xml, xml_declaration=True, encoding="UTF-8", standalone=True))

        # ── Write comments.xml ──
        out_z.writestr("word/comments.xml",
                       etree.tostring(comments_et, xml_declaration=True, encoding="UTF-8", standalone=True))

        # ── Fix [Content_Types].xml ──
        try:
            ct_raw = in_z.read("[Content_Types].xml")
            if ct_raw[:3] == b"\xef\xbb\xbf":
                ct_raw = ct_raw[3:]
        except KeyError:
            ct_raw = (
                b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                b'<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                b'<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                b'<Default Extension="xml" ContentType="application/xml"/>'
                b'<Override PartName="/word/document.xml" '
                b'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
                b"</Types>"
            )

        ct_et = etree.fromstring(ct_raw)
        ct_ns = _CT_NS
        # Check if comments override already exists
        comments_ct_exists = any(
            ov.get("PartName") == "/word/comments.xml"
            for ov in ct_et.findall(f"{{{ct_ns}}}Override")
        )
        if not comments_ct_exists:
            ov = etree.SubElement(ct_et, f"{{{ct_ns}}}Override")
            ov.set("PartName", "/word/comments.xml")
            ov.set("ContentType",
                   "application/vnd.openxmlformats-officedocument.wordprocessingml.comments+xml")

        out_z.writestr("[Content_Types].xml",
                       etree.tostring(ct_et, xml_declaration=True, encoding="UTF-8", standalone=True))

        # ── Fix word/_rels/document.xml.rels ──
        try:
            rels_raw = in_z.read("word/_rels/document.xml.rels")
            if rels_raw[:3] == b"\xef\xbb\xbf":
                rels_raw = rels_raw[3:]
        except KeyError:
            rels_raw = (
                b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                b'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                b"</Relationships>"
            )

        rels_et = etree.fromstring(rels_raw)
        rels_ns = "http://schemas.openxmlformats.org/package/2006/relationships"
        # Check if comments relationship already exists
        comments_rel_exists = any(
            r.get("Type") == (_R_NS + "/comments")
            for r in rels_et.findall(f"{{{rels_ns}}}Relationship")
        )
        if not comments_rel_exists:
            # Find max existing rId number
            max_rid = 0
            for r in rels_et.findall(f"{{{rels_ns}}}Relationship"):
                rid = r.get("Id", "")
                if rid.startswith("rId"):
                    try:
                        num = int(rid[3:])
                        if num > max_rid:
                            max_rid = num
                    except ValueError:
                        pass
            new_rid = f"rId{max_rid + 1}"
            rel = etree.SubElement(rels_et, f"{{{rels_ns}}}Relationship")
            rel.set("Id", new_rid)
            rel.set("Type", _R_NS + "/comments")
            rel.set("Target", "comments.xml")

        out_z.writestr("word/_rels/document.xml.rels",
                       etree.tostring(rels_et, xml_declaration=True, encoding="UTF-8", standalone=True))

    return output_buf.getvalue()
