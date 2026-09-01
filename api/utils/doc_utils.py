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
"""老格式 .doc 识别与 LibreOffice 转 .docx 共享工具。

供 file_api（文档解析）与 flow_app（流程文档编辑）共用——restful_apis 蓝图模块
的 `manager` 由应用动态加载器注入，蓝图之间不能直接 import，故抽到此处。
"""
import logging
import os
import subprocess
import tempfile

OLE2_MAGIC = b"\xd0\xcf\x11\xe0"


def is_doc_file(blob: bytes, filename: str = "") -> bool:
    """Check whether a file is an old-format .doc (OLE2 compound document)."""
    if filename.lower().endswith(".doc") and not filename.lower().endswith(".docx"):
        return True
    if blob and blob[:4] == OLE2_MAGIC:
        return True
    return False


def doc_to_docx_via_libreoffice(binary: bytes) -> bytes | None:
    """Convert .doc binary to .docx binary using LibreOffice headless.

    Returns the converted .docx bytes, or None if LibreOffice is unavailable
    or conversion fails.
    """
    import shutil
    import time as time_mod

    soffice = shutil.which("libreoffice") or shutil.which("soffice")
    if not soffice:
        logging.warning("[doc2docx] LibreOffice not found in PATH")
        return None

    logging.info(f"[doc2docx] starting conversion, binary_size={len(binary)}")

    tmp_in_dir = tempfile.mkdtemp(prefix="doc2docx_in_")
    tmp_out_dir = tempfile.mkdtemp(prefix="doc2docx_out_")
    # Unique profile dir per invocation to avoid concurrent lock conflicts
    profile_dir = tempfile.mkdtemp(prefix="lo_profile_")
    try:
        in_path = os.path.join(tmp_in_dir, "input.doc")
        with open(in_path, "wb") as f:
            f.write(binary)

        lo_env = {
            **os.environ,
            "LD_LIBRARY_PATH": "/usr/lib/libreoffice/program:" + os.environ.get("LD_LIBRARY_PATH", ""),
            "HOME": profile_dir,  # LibreOffice needs writable HOME for profile
        }
        cmd = [
            soffice, "--headless", "--norestore", "--nologo",
            f"-env:UserInstallation=file://{profile_dir}",
            "--convert-to", "docx", "--outdir", tmp_out_dir, in_path,
        ]

        # Retry up to 2 times: first run may fail while creating profile
        for attempt in range(2):
            result = subprocess.run(cmd, capture_output=True, timeout=60, env=lo_env)
            logging.info(
                f"[doc2docx] attempt={attempt + 1} returncode={result.returncode} "
                f"stdout={result.stdout[:200] if result.stdout else 'empty'} "
                f"stderr={result.stderr[:200] if result.stderr else 'empty'}"
            )
            out_path = os.path.join(tmp_out_dir, "input.docx")
            if result.returncode == 0 and os.path.exists(out_path):
                with open(out_path, "rb") as f:
                    docx_blob = f.read()
                logging.info(f"[doc2docx] conversion OK, docx_size={len(docx_blob)}")
                return docx_blob
            # Clean output dir for retry
            import shutil as shutil_mod
            shutil_mod.rmtree(tmp_out_dir, ignore_errors=True)
            os.makedirs(tmp_out_dir, exist_ok=True)
            if attempt == 0:
                time_mod.sleep(2)

        logging.warning("[doc2docx] all attempts failed")
    except subprocess.TimeoutExpired:
        logging.warning("[doc2docx] conversion timed out (60s)")
    except Exception as e:
        logging.warning(f"[doc2docx] conversion error: {e}", exc_info=True)
    finally:
        import shutil as shutil_mod
        shutil_mod.rmtree(tmp_in_dir, ignore_errors=True)
        shutil_mod.rmtree(tmp_out_dir, ignore_errors=True)
        shutil_mod.rmtree(profile_dir, ignore_errors=True)
    return None
