"""标书附件下载与解压工具"""
import logging
import os
import tempfile
import zipfile
import requests
from pathlib import Path


def download_file(url: str, dest_dir: str) -> str:
    """从URL下载文件到指定目录，返回本地路径

    Raises:
        RuntimeError: 下载内容被替换为 HTML 页面（CDN 反爬/预览壳），非真实附件
    """
    resp = requests.get(url, timeout=120, stream=True)
    resp.raise_for_status()

    # 先读一小段检查是否被替换为 HTML 预览页面
    # CDN 可能对非浏览器请求返回 PDF.js viewer 壳而非真实文件
    peek = resp.iter_content(chunk_size=256, decode_unicode=False).__next__()
    if peek[:15] == b"<!DOCTYPE html>" or peek[:9] == b"<!DOCTYPE" or peek[:6] == b"<html>" or peek[:5] == b"<html":
        raise RuntimeError(
            f"Downloaded content is a web page, not a real file. "
            f"URL may be behind a CDN or PDF preview wrapper: {url}"
        )

    # 尝试从 Content-Disposition 或 URL 提取文件名
    filename = None
    cd = resp.headers.get("Content-Disposition", "")
    if "filename=" in cd:
        import re
        match = re.search(r'filename[^;=\n]*=["\']?([^"\'\n]*)["\']?', cd)
        if match:
            filename = match.group(1)

    if not filename:
        from urllib.parse import unquote
        filename = unquote(os.path.basename(url.split("?")[0]))
    if not filename or "." not in filename:
        # 从 Content-Type 推导后缀
        ct = resp.headers.get("Content-Type", "")
        ext_map = {
            "application/pdf": ".pdf",
            "application/msword": ".doc",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
            "application/vnd.ms-excel": ".xls",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
            "application/zip": ".zip",
            "application/x-rar-compressed": ".rar",
            "application/x-zip-compressed": ".zip",
            "image/jpeg": ".jpg",
            "image/png": ".png",
        }
        suffix = ext_map.get(ct, "")
        filename = f"downloaded{suffix}"

    local_path = os.path.join(dest_dir, filename)
    with open(local_path, "wb") as f:
        f.write(peek)
        for chunk in resp.iter_content(chunk_size=8192):
            f.write(chunk)
    logging.info("Downloaded: %s -> %s", url, local_path)
    return local_path


def extract_archive(file_path: str, dest_dir: str) -> list:
    """解压 zip/rar 压缩包，返回解压后的文件路径列表"""
    extracted = []
    lower = file_path.lower()

    if lower.endswith(".zip"):
        with zipfile.ZipFile(file_path, "r") as zf:
            for member in zf.namelist():
                # 跳过目录
                if member.endswith("/"):
                    continue
                # 处理中文文件名编码
                try:
                    name = member.encode("cp437").decode("gbk")
                except (UnicodeDecodeError, UnicodeEncodeError):
                    name = member
                basename = os.path.basename(name)
                if not basename:
                    continue
                dest = os.path.join(dest_dir, basename)
                # 避免覆盖
                if os.path.exists(dest):
                    stem, ext = os.path.splitext(basename)
                    dest = os.path.join(dest_dir, f"{stem}_extracted{ext}")
                with zf.open(member) as src, open(dest, "wb") as dst:
                    dst.write(src.read())
                extracted.append(dest)
                logging.info("Extracted: %s -> %s", member, dest)

    elif lower.endswith(".rar"):
        try:
            import rarfile
            with rarfile.RarFile(file_path, "r") as rf:
                for member in rf.namelist():
                    if member.endswith("/"):
                        continue
                    basename = os.path.basename(member)
                    if not basename:
                        continue
                    dest = os.path.join(dest_dir, basename)
                    if os.path.exists(dest):
                        stem, ext = os.path.splitext(basename)
                        dest = os.path.join(dest_dir, f"{stem}_extracted{ext}")
                    with rf.open(member) as src, open(dest, "wb") as dst:
                        dst.write(src.read())
                    extracted.append(dest)
                    logging.info("Extracted: %s -> %s", member, dest)
        except ImportError:
            logging.warning("rarfile not installed, skipping RAR extraction: %s", file_path)
        except Exception as e:
            logging.error("Failed to extract RAR %s: %s", file_path, e)

    return extracted
