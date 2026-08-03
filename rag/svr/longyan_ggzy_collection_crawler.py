"""
龙岩市公共资源交易中心 — 交易信息采集 (custom_runner)
====================================================

Target: https://ggzy.longyan.gov.cn/lyztb/

6 大栏目 51 个页签 (TABS 矩阵经首页导航 + getInfoMationList AllCount 双重验证):
  工程建设(30) / 政府采购(6) / 企业采购(4) / 产权交易(4) / 土地矿业(4, 12位码) / 林权水权(3)

Site characteristics
────────────────────
Epoint WebBuilder 平台。列表 API getInfoMationList 为 form-urlencoded POST,
第 1 页无需 token/验证码; 响应 {AllCount, custom:[{title,title2,infourl,
infodate,zhuanzai,strcomment,index}]}, 按 infodate 倒序, 无 id 字段。

详情页 = SITE_ROOT + infourl (服务端渲染 HTML):
  标题 <h3 class="bigtitle"> / 信息时间 / <p class="sub-cp"> 信息来源 /
  正文 div#mainContent / 附件 ztbfjyz('...downloadztbattach?attachGuid=<guid>...')

附件直连: ztbAttachDownloadAction.action?cmd=getContent&attachGuid=..&
appUrlFlag=ztb001&siteGuid=.. — 可能被 pageVerify 验证码拦截(实测拦截体为
117 字节小 JSON 而非 HTML 网关页), 代码优雅降级(记录 captcha_blocked_count,
附件 URL 仍写入 markdown/extracted_json)。

反爬: WAF 短时 ~30 快速请求→403(~20s 自愈); 深分页(8+)出验证码。
策略: 请求间隔 0.8-2.0s; 无论 full 还是日常(date_filter)模式, 每个页签均只抓
列表第 1 页(pageSize=30, 按日期倒序); 日常模式仅保留目标日条目,
遇早于目标日的条目即停止本页签。

Call entry: unified_crawler.py dispatches via custom_runner → run()
"""

import html as html_mod
import json
import logging
import os
import random
import re
import shutil
import ssl
import sys
import tempfile
import time
import urllib.parse
import urllib.request
import zipfile
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.abspath(os.path.join(_THIS_DIR, "..", ".."))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from rag.svr.crawler_engine.models import item_from_dict

try:
    from rag.utils.redis_conn import REDIS_CONN, RedisDistributedLock
except ImportError:
    REDIS_CONN = None  # type: ignore
    RedisDistributedLock = None  # type: ignore

logger = logging.getLogger("longyan_ggzy_collection_crawler")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SITE_ID = "longyan_ggzy"
SITE_DISPLAY = "龙岩市公共资源交易中心 ggzy.longyan.gov.cn"
CATEGORY = "bid"
INFO_TYPE = "龙岩市-交易信息"          # 需求#10: type 字段按列表主题统一填写
KB_ID_DEFAULT = "3b4f619c85c211f198269135a1db216c"

_SITE_ROOT = "https://ggzy.longyan.gov.cn"
_TAG_PREFIX = "[LY-GGZY]"

_PAGE_SIZE = 30
_REQUEST_DELAY_MIN = 0.8
_REQUEST_DELAY_MAX = 2.0
_LISTING_RETRIES = 2          # 列表请求重试次数 (WAF 瞬时 403 自愈约 20s)
_RETRY_BACKOFF_MIN = 15       # 重试退避下限 (秒)
_RETRY_BACKOFF_MAX = 25       # 重试退避上限 (秒)
_LOCK_TIMEOUT = 10800                 # 3h: 首次全量 51 页签耗时可能超 30min 默认 TTL

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/125.0.0.0 Safari/537.36"
)

_SITE_GUID = "7eb5f7f1-9041-43ad-8e13-8fcb82ea831a"
_PROJECT_NAME = "/EpointWebBuilder"
_APP_URL_FLAG = "ztb001"

# WAF 验证码拦截标记 — 拦截体字段名/文案 (JSON 拦截体与网关 HTML 均可能出现)
_CAPTCHA_MARKERS = ("pageVerify", "validateVerificationCode", "验证码")

_API_LISTING = _SITE_ROOT + _PROJECT_NAME + "/rest/GgSearchAction/getInfoMationList"
_API_ATTACH = _SITE_ROOT + _PROJECT_NAME + "/webbuildermis/attach/ztbAttachDownloadAction.action"

# ---------------------------------------------------------------------------
# TABS 矩阵 — (categoryNum, section_name, tab_path)
# section_name = 6 大栏目(需求#4 确认口径); tab_path 进 extracted_json
# ---------------------------------------------------------------------------

_SEC_GCJS = "龙岩市-工程建设"
_SEC_ZFCG = "龙岩市-政府采购"
_SEC_QYCG = "龙岩市-企业采购"
_SEC_CQJY = "龙岩市-产权交易"
_SEC_TDKY = "龙岩市-土地矿业"
_SEC_LKSQ = "龙岩市-林权水权交易"

TABS: List[Tuple[str, str, str]] = []

# 工程建设: 房建/市政/交通/水利 各 5 页签
for _cat, _cname in (("007004", "房屋建筑"), ("007005", "市政基础设施"),
                     ("007006", "交通"), ("007007", "水利")):
    for _tab, _tname in (("001", "招标公告信息"), ("002", "答疑结果"),
                         ("003", "中标候选人公示"), ("004", "中标结果公示"),
                         ("005", "合同签署")):
        TABS.append((_cat + _tab, _SEC_GCJS, "{}/{}".format(_cname, _tname)))
# 信息化: 仅 招标+答疑
for _tab, _tname in (("001", "招标公告信息"), ("002", "答疑结果")):
    TABS.append(("007008" + _tab, _SEC_GCJS, "信息化/{}".format(_tname)))
# 工业项目: 招标+答疑+中标结果公示(站点实际存在 004, 为需求超集, 按站点采集)
for _tab, _tname in (("001", "招标公告信息"), ("002", "答疑结果"), ("004", "中标结果公示")):
    TABS.append(("007010" + _tab, _SEC_GCJS, "工业项目/{}".format(_tname)))
# 其他工程: 全 5 页签
for _tab, _tname in (("001", "招标公告信息"), ("002", "答疑结果"),
                     ("003", "中标候选人公示"), ("004", "中标结果公示"),
                     ("005", "合同签署")):
    TABS.append(("007009" + _tab, _SEC_GCJS, "其他工程/{}".format(_tname)))

# 政府采购: 008001 政府采购 / 008003 其他采购 各 3 页签
for _cat, _cname in (("008001", "政府采购"), ("008003", "其他采购")):
    for _tab, _tname in (("001", "预公告"), ("002", "招标公告"), ("003", "中标、成交公告")):
        TABS.append((_cat + _tab, _SEC_ZFCG, "{}/{}".format(_cname, _tname)))

# 企业采购 (008005)
for _tab, _tname in (("001", "招标公告"), ("002", "变更公告"),
                     ("003", "中标、成交公告"), ("004", "预公告")):
    TABS.append(("008005" + _tab, _SEC_QYCG, _tname))

# 产权交易 (011006)
for _tab, _tname in (("001", "企业和行政事业单位产权资产交易"), ("002", "无形资产交易"),
                     ("003", "其他项目"), ("004", "成交结果公告")):
    TABS.append(("011006" + _tab, _SEC_CQJY, _tname))

# 土地矿业 (011007) — 12 位叶码
for _tab, _tname in (("011007001001", "土地/出让公告"), ("011007001002", "土地/出让结果"),
                     ("011007002001", "矿业/出让公告"), ("011007002002", "矿业/出让结果")):
    TABS.append((_tab, _SEC_TDKY, _tname))

# 林权水权 — 注意林权编号反转: 011010002=交易公告, 011010001=成交公告
TABS.append(("011010002", _SEC_LKSQ, "林权/交易公告"))
TABS.append(("011010001", _SEC_LKSQ, "林权/成交公告"))
TABS.append(("011011001", _SEC_LKSQ, "水权/交易公告"))

_HEADERS_JSON = {
    "User-Agent": _USER_AGENT,
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    "X-Requested-With": "XMLHttpRequest",
}

_HEADERS_HTML = {
    "User-Agent": _USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}

_HEADERS_DOWNLOAD = {
    "User-Agent": _USER_AGENT,
    "Accept": "*/*",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}

_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _safe_print(msg: str) -> None:
    try:
        print(msg)
    except UnicodeEncodeError:
        print(msg.encode("gbk", errors="replace").decode("gbk"))


def _request_delay() -> None:
    time.sleep(random.uniform(_REQUEST_DELAY_MIN, _REQUEST_DELAY_MAX))


def _sanitize_filename(name: str, max_len: int = 120) -> str:
    if not name:
        return "unnamed"
    safe = re.sub(r'[\\/:*?"<>|\r\n\t]+', "_", name)
    safe = re.sub(r"_+", "_", safe)
    safe = safe.strip("._ ")
    if len(safe) > max_len:
        base, ext = os.path.splitext(safe)
        safe = base[: max_len - len(ext)] + (ext or "")
    return safe or "unnamed"


def _normalize_date(date_str: str) -> str:
    if not date_str:
        return ""
    m = re.search(r"(\d{4}-\d{1,2}-\d{1,2})", str(date_str))
    if not m:
        return str(date_str)[:10]
    y, mo, d = m.group(1).split("-")
    return "{}-{:02d}-{:02d}".format(y, int(mo), int(d))


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

def _http_post_form(url: str, form: dict, extra_headers: Optional[dict] = None) -> Optional[str]:
    """form-urlencoded POST. Returns response text or None."""
    hdrs = dict(_HEADERS_JSON)
    if extra_headers:
        hdrs.update(extra_headers)
    body = urllib.parse.urlencode(form).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers=hdrs)
    try:
        with urllib.request.urlopen(req, timeout=30, context=_SSL_CTX) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except Exception as e:
        logging.warning("POST %s failed: %s", url, e)
        return None


def _http_get_html(url: str, referer: Optional[str] = None) -> Optional[str]:
    """GET HTML page. Returns response text or None."""
    hdrs = dict(_HEADERS_HTML)
    if referer:
        hdrs["Referer"] = referer
    req = urllib.request.Request(url, headers=hdrs)
    try:
        with urllib.request.urlopen(req, timeout=30, context=_SSL_CTX) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except Exception as e:
        logging.warning("GET %s failed: %s", url, e)
        return None


def _http_download(url: str, referer: Optional[str] = None) -> Optional[bytes]:
    """GET binary download. Returns bytes or None."""
    hdrs = dict(_HEADERS_DOWNLOAD)
    if referer:
        hdrs["Referer"] = referer
    req = urllib.request.Request(url, headers=hdrs)
    try:
        with urllib.request.urlopen(req, timeout=60, context=_SSL_CTX) as resp:
            return resp.read()
    except Exception as e:
        logging.warning("Download %s failed: %s", url, e)
        return None


# ---------------------------------------------------------------------------
# Listing API
# ---------------------------------------------------------------------------

def _fetch_tab_rows(category_num: str, page_index: int) -> List[dict]:
    """Fetch one page (0-based) of a tab listing. Returns row dicts.

    验证码/反爬响应不是 JSON → 返回 [] (上层按空页处理, 不崩)。
    网络/WAF 瞬时失败(text 为空)重试一次: WAF 约 20s 自愈, 退避 15-25s。
    """
    form = {
        "siteGuid": _SITE_GUID,
        "categoryNum": category_num,
        "kw": "",
        "pageIndex": str(page_index),
        "pageSize": str(_PAGE_SIZE),
        "YZM": "",
        "ImgGuid": "",
    }
    text = None
    for attempt in range(_LISTING_RETRIES):
        text = _http_post_form(
            _API_LISTING, form, extra_headers={"Referer": _SITE_ROOT + "/lyztb/"}
        )
        if text:
            break
        if attempt == 0:
            logging.warning(
                "longyan listing empty response, retrying: cat=%s page=%d",
                category_num, page_index,
            )
            time.sleep(random.uniform(_RETRY_BACKOFF_MIN, _RETRY_BACKOFF_MAX))
    if not text:
        logging.warning(
            "longyan listing still empty after %d attempts, treating as empty page: cat=%s page=%d",
            _LISTING_RETRIES, category_num, page_index,
        )
        return []
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        logging.warning(
            "longyan listing not JSON (captcha/WAF?) cat=%s page=%d head=%r",
            category_num, page_index, text[:80],
        )
        return []
    if not isinstance(data, dict):
        logging.warning(
            "longyan listing JSON root not object: cat=%s page=%d type=%s",
            category_num, page_index, type(data).__name__,
        )
        return []
    rows = data.get("custom")
    if not isinstance(rows, list):
        logging.warning(
            "longyan listing missing 'custom' list: cat=%s page=%d keys=%s",
            category_num, page_index, sorted(data.keys()),
        )
        return []
    return [r for r in rows if isinstance(r, dict)]


# ---------------------------------------------------------------------------
# Detail page
# ---------------------------------------------------------------------------

def _html_to_text(html_str: str) -> str:
    """Strip HTML tags and decode entities, return clean text."""
    if not html_str:
        return ""
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", html_str, flags=re.DOTALL | re.I)
    text = re.sub(
        r"</?(?:div|p|tr|li|h[1-6]|table|hr|section|article|header|footer)[^>]*>",
        "\n", text, flags=re.I,
    )
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    text = re.sub(r"</td>", " ", text, flags=re.I)
    text = re.sub(r"</tr>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    text = html_mod.unescape(text)
    text = text.replace("\xa0", " ").replace("　", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _extract_attachments_from_html(html_str: str, page_url: str) -> List[dict]:
    """Extract attachment links from detail page HTML.

    两种来源:
      1. ztbfjyz('...downloadztbattach?attachGuid=<guid>...') onclick —
         构造 ztbAttachDownloadAction 直连 URL (appUrlFlag=ztb001)
      2. 直接文件链接 (.pdf/.doc/.xls/.zip/.rar/.ppt)
    Returns [{name, guid, url}] — url 均为绝对地址(需求#13: 原文链接完整可访问)。
    """
    attachments: List[dict] = []
    seen: set = set()

    for m in re.finditer(
        r"ztbfjyz\('([^']+/downloadztbattach\?attachGuid="
        r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})[^']*)'",
        html_str, re.I,
    ):
        guid = m.group(2)
        if guid in seen:
            continue
        seen.add(guid)
        name = "{}.unknown".format(guid)
        post_context = html_str[m.end():m.end() + 500]
        name_m = re.search(r'title="([^"]+)"', post_context)
        if not name_m:
            name_m = re.search(r'>([^<]{3,120})</a>', post_context)
        if name_m:
            name = html_mod.unescape(name_m.group(1).strip())
        real_url = (
            "{}?cmd=getContent&attachGuid={}&appUrlFlag={}&siteGuid={}".format(
                _API_ATTACH, guid, _APP_URL_FLAG, _SITE_GUID
            )
        )
        attachments.append({
            "name": _sanitize_filename(name),
            "guid": guid,
            "url": real_url,
        })

    for m in re.finditer(
        r'<a[^>]*href=["\']([^"\']*(?:\.pdf|\.doc[x]?|\.xls[x]?|\.rar|\.zip|\.ppt[x]?)'
        r'[^"\']*)["\'][^>]*>([^<]*)</a>',
        html_str, re.I,
    ):
        href = m.group(1).strip()
        link_text = html_mod.unescape(m.group(2).strip())
        if not href:
            continue
        if not href.startswith("http"):
            if href.startswith("//"):
                href = "https:" + href
            elif href.startswith("/"):
                href = _SITE_ROOT + href
            else:
                href = page_url.rsplit("/", 1)[0] + "/" + href
        if href in seen:
            continue
        seen.add(href)
        fname = link_text or os.path.basename(
            urllib.parse.urlparse(href).path.split("?")[0]
        ) or "attachment"
        attachments.append({"name": _sanitize_filename(fname), "guid": "", "url": href})

    return attachments


def _fetch_detail(full_url: str) -> Optional[dict]:
    """Fetch + parse one detail page. Returns None on network failure.

    Returns {title, info_time, source, content_text, attachments}.
    """
    html = _http_get_html(full_url, referer=_SITE_ROOT + "/lyztb/")
    if not html:
        return None

    result: Dict[str, Any] = {
        "title": "", "info_time": "", "source": "",
        "content_text": "", "attachments": [],
    }

    m = re.search(r'<h3\s+class="bigtitle">(.+?)</h3>', html, re.DOTALL)
    if m:
        result["title"] = html_mod.unescape(re.sub(r"<[^>]+>", "", m.group(1))).strip()

    m = re.search(r"信息时间：(\d{4}-\d{2}-\d{2})", html)
    if m:
        result["info_time"] = m.group(1)

    m = re.search(r'<p\s+class="sub-cp">(.*?)</p>', html, re.DOTALL)
    if m:
        src_m = re.search(r'信息来源：<span[^>]*>([^<]*)</span>', m.group(1))
        if src_m:
            result["source"] = src_m.group(1).strip()

    idx = html.find('id="mainContent"')
    if idx >= 0:
        content_start = html.find(">", idx) + 1
        content_end = len(html)
        for marker in ("ewb-page-lookup", "footrt", "subfooter"):
            tag_m = re.search(
                r"<[^>]*" + re.escape(marker) + r"[^>]*>", html[content_start:]
            )
            if tag_m:
                content_end = content_start + tag_m.start()
                break
        content_html = html[content_start:content_end].strip()
        content_html = re.sub(r"<[^>]*\Z", "", content_html)
        no_chain = re.sub(
            r'<div\s+class="chain"[^>]*>.*?</div>\s*</div>\s*</div>\s*</div>\s*</div>',
            "", content_html, flags=re.DOTALL,
        )
        if no_chain.strip():
            content_html = no_chain
        result["content_text"] = _html_to_text(content_html)

    result["attachments"] = _extract_attachments_from_html(html, full_url)
    if not result["title"] or not result["content_text"]:
        logging.warning(
            "longyan detail parse incomplete (title=%r content_len=%d): %s",
            result["title"][:40], len(result["content_text"]), full_url,
        )
    return result


# ---------------------------------------------------------------------------
# Attachment download
# ---------------------------------------------------------------------------

def _classify_downloaded_head(data: bytes) -> Tuple[bool, str]:
    """对下载到的字节做类型判定: 真实附件 or WAF/网关拦截页。纯函数, 无网络 IO。

    真实附件必为二进制 (PDF/ZIP/DOC/XLS...), 绝不会是 JSON/HTML 文本。
    WAF 验证码拦截实测返回 117 字节 JSON:
      {"custom":{"validateVerificationCode":"false","pageVerify":"true",
                 "content":"验证码验证失败！","status":"1"}}

    Returns (is_real_file, reason), reason ∈
      captcha_json / json_error / captcha_html / html_page / file
    """
    head = data.lstrip()
    # JSON 响应 = 验证码拦截体或接口报错, 都不是真实文件
    if head[:1] in (b"{", b"["):
        low = head.decode("utf-8", errors="replace").lower()
        if any(mk.lower() in low for mk in _CAPTCHA_MARKERS):
            return False, "captcha_json"
        return False, "json_error"
    # HTML 响应 = 网关页或验证码页, 不是真实文件
    low_head = head.lower()
    if low_head.startswith(b"<!doctype") or low_head.startswith(b"<html"):
        text = head.decode("utf-8", errors="replace")
        low = text.lower()
        if "pageverify" in low or "验证码" in text:
            return False, "captcha_html"
        return False, "html_page"
    return True, "file"


def _download_attachments(attachments: List[dict], download_dir: str) -> Tuple[List[str], int]:
    """Download attachments to local dir.

    Returns (local_file_paths, captcha_blocked_count).
    WAF 验证码拦截(实测为 117 字节 JSON)或网关/验证码 HTML 响应 → 写入后校验,
    发现即删除垃圾文件并计数跳过(附件 URL 仍保留在 markdown/extracted_json)。
    """
    os.makedirs(download_dir, exist_ok=True)
    local_files: List[str] = []
    captcha_blocked = 0
    claimed: set = set()  # 本批次已占用的文件路径 — 同名附件加序号, 防覆盖/防重复路径

    for att in attachments:
        url = att.get("url", "")
        if not url:
            continue

        fname = _sanitize_filename(att.get("name", "attachment"), max_len=120)
        ext = os.path.splitext(urllib.parse.urlparse(url).path.split("?")[0])[1].lower()
        # API 端点 URL 的伪扩展名不能追加到文件名 (ztbAttachDownloadAction.action)
        if ext in (".action", ".do", ".aspx", ".jsp", ".php", ".html", ".htm"):
            ext = ""
        if ext and not fname.lower().endswith(ext):
            fname += ext

        filepath = os.path.join(download_dir, fname)
        if os.path.exists(filepath) and os.path.getsize(filepath) > 0 and filepath not in claimed:
            local_files.append(filepath)
            claimed.add(filepath)
            continue

        data = _http_download(url, referer=_SITE_ROOT + "/lyztb/")
        if not data:
            continue

        # magic bytes 修正扩展名
        if data[:4] == b"%PDF" and not fname.lower().endswith(".pdf"):
            fname += ".pdf"
        elif data[:4] == b"PK\x03\x04" and \
                not any(fname.lower().endswith(e) for e in (".zip", ".docx", ".xlsx", ".pptx", ".doc", ".ppt", ".xls")):
            fname += ".zip"

        filepath = os.path.join(download_dir, fname)
        n = 1
        while filepath in claimed:
            base, f_ext = os.path.splitext(fname)
            filepath = os.path.join(download_dir, "{}_{}{}".format(base, n, f_ext))
            n += 1
        with open(filepath, "wb") as f:
            f.write(data)

        # ▶ 写入后校验内容: 真实附件必为二进制; 验证码拦截 JSON / 接口报错 JSON /
        #   网关 HTML 均不是真实文件 — 删除垃圾文件并计数, 继续下一个附件
        is_real, reason = _classify_downloaded_head(data)
        if not is_real:
            try:
                os.remove(filepath)
            except OSError:
                pass
            captcha_blocked += 1
            if reason == "captcha_json":
                logging.info("Attachment captcha-blocked (JSON): %s | %s", url[:100], fname)
            elif reason == "captcha_html":
                logging.warning("Attachment captcha-blocked: %s | %s", url[:100], fname)
            elif reason == "json_error":
                logging.warning(
                    "Attachment download returned JSON error, discarded: %s | %s | head=%r",
                    url[:100], fname, data.lstrip()[:200],
                )
            else:  # html_page — 真实附件绝不会是全页 HTML
                logging.warning(
                    "Attachment download returned HTML page, discarded: %s | %s",
                    url[:100], fname,
                )
            continue

        claimed.add(filepath)
        local_files.append(filepath)
        _request_delay()

    return local_files, captcha_blocked


def _extract_zip(filepath: str) -> List[str]:
    """Extract ZIP members (需求#9: ZIP 解压后入库/入KB)。返回解压文件路径列表。"""
    extracted: List[str] = []
    extract_dir = os.path.splitext(filepath)[0] + "_extracted"
    os.makedirs(extract_dir, exist_ok=True)
    try:
        with zipfile.ZipFile(filepath, "r") as zf:
            for name in zf.namelist():
                if name.endswith("/"):
                    continue
                safe_name = _sanitize_filename(os.path.basename(name), max_len=120)
                out_path = os.path.join(extract_dir, safe_name)
                # 同名冲突加序号, 避免覆盖
                n = 1
                while os.path.exists(out_path):
                    base, ext = os.path.splitext(safe_name)
                    out_path = os.path.join(extract_dir, "{}_{}{}".format(base, n, ext))
                    n += 1
                with zf.open(name) as src, open(out_path, "wb") as dst:
                    dst.write(src.read())
                extracted.append(out_path)
    except Exception as e:
        logging.warning("ZIP extraction failed for %s: %s", filepath, e)
    return extracted


def _extract_text_from_file(filepath: str) -> str:
    """Extract text from TXT/PDF/DOCX/XLSX for the markdown appendix.

    扫描件 PDF 由 KB naive parser 的 OCR 兜底(需求 ocr 口径)。
    """
    ext = os.path.splitext(filepath)[1].lower()
    try:
        if ext == ".txt":
            with open(filepath, "r", encoding="utf-8", errors="replace") as f:
                return f.read()
        elif ext == ".pdf":
            import pdfplumber

            parts: List[str] = []
            with pdfplumber.open(filepath) as pdf:
                for pg in pdf.pages:
                    text = pg.extract_text()
                    if text:
                        parts.append(text)
            return "\n\n".join(parts)
        elif ext in (".docx", ".doc"):
            import docx

            doc = docx.Document(filepath)
            return "\n".join(p.text for p in doc.paragraphs if p.text.strip())
        elif ext in (".xlsx", ".xls"):
            import openpyxl

            wb = openpyxl.load_workbook(filepath, read_only=True)
            parts = []
            for ws in wb.worksheets:
                rows: List[str] = []
                for row in ws.iter_rows(values_only=True):
                    rows.append(" | ".join(str(c) if c is not None else "" for c in row))
                if rows:
                    parts.append("### {}\n".format(ws.title) + "\n".join(rows))
            return "\n\n".join(parts)
    except Exception as e:
        logging.warning("Text extraction failed for %s: %s", filepath, e)
    return ""


# ---------------------------------------------------------------------------
# Markdown builder
# ---------------------------------------------------------------------------

def _build_markdown(title: str, detail_url: str, section_name: str, tab_path: str,
                    info_date: str, info_time: str, source: str,
                    content_text: str, attachments: List[dict]) -> str:
    """Build the KB markdown document for one item."""
    lines: List[str] = [
        "# {}".format(title or "无标题"),
        "",
        "**数据来源:** 龙岩市公共资源交易中心",
        "**栏目:** {} — {}".format(section_name, tab_path),
        "**页面地址:** {}".format(detail_url),
        "**抓取时间:** {}".format(datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
    ]
    if info_time or info_date:
        lines.append("**信息时间:** {}".format(info_time or info_date))
    if source:
        lines.append("**信息来源:** {}".format(source))
    lines += ["", "---", ""]

    if content_text:
        content_clean = re.sub(r"\n{3,}", "\n\n", content_text)
        if len(content_clean) > 100000:
            content_clean = content_clean[:100000] + "\n\n（内容过长，已截断）"
        lines.append(content_clean)
    else:
        lines.append("*(本文无文字内容)*")

    if attachments:
        lines += ["", "---", "", "## 附件", ""]
        for att in attachments:
            lines.append("- [{}]({})".format(att.get("name", "附件"), att.get("url", "")))

    return "\n".join(lines)


def _build_attachment_appendix(local_files: List[str]) -> str:
    """Appendix markdown: extracted text of each downloaded attachment file."""
    if not local_files:
        return ""
    lines: List[str] = ["### 附件内容", ""]
    for fp in local_files:
        if not os.path.exists(fp) or os.path.getsize(fp) == 0:
            continue
        lines.append("#### {}".format(os.path.basename(fp)))
        lines.append("")
        extracted_text = _extract_text_from_file(fp)
        if extracted_text and extracted_text.strip():
            if len(extracted_text) > 50000:
                extracted_text = extracted_text[:50000] + "\n\n（内容过长，已截断）"
            lines.append(extracted_text)
        else:
            lines.append("（无法提取文本内容）")
        lines.append("")
    return "\n".join(lines) if len(lines) > 2 else ""


# ---------------------------------------------------------------------------
# Per-item processing
# ---------------------------------------------------------------------------

def _process_item(row: dict, category_num: str, section_name: str, tab_path: str,
                  writer, pipeline, tenant_id: str, kb_id: str,
                  category: str, task_id: str, errors: List[str]) -> Tuple[int, int, int]:
    """Fetch detail + attachments, write DB + KB for one listing row.

    Returns (stored_count, kb_doc_count, attachment_upload_count).
    """
    infourl = (row.get("infourl") or "").strip()
    full_url = _SITE_ROOT + infourl
    list_title = html_mod.unescape(re.sub(r"<[^>]+>", "", row.get("title", "") or "")).strip()
    infodate = _normalize_date(row.get("infodate", ""))

    detail = _fetch_detail(full_url)
    if detail is None:
        errors.append("detail fetch failed: {}".format(full_url))
        return 0, 0, 0

    title = detail.get("title") or list_title or "(no title)"
    attachments = detail.get("attachments", [])

    # ── Download attachments + unzip ────────────────────────────────
    local_files: List[str] = []
    captcha_blocked = 0
    tmp_dir = ""
    if attachments:
        tmp_dir = tempfile.mkdtemp(prefix="lyggzy_")
        try:
            local_files, captcha_blocked = _download_attachments(attachments, tmp_dir)
            for fp in list(local_files):
                is_zip = fp.lower().endswith(".zip")
                # ▶ docx/xlsx/pptx/doc/xls/ppt 同为 PK 魔数, 不能按 zip 解压(会碎成 XML)
                if (not is_zip and os.path.exists(fp) and os.path.getsize(fp) >= 4
                        and not any(fp.lower().endswith(e) for e in
                                    (".docx", ".xlsx", ".pptx", ".doc", ".xls", ".ppt"))):
                    with open(fp, "rb") as f:
                        is_zip = f.read(4) == b"PK\x03\x04"
                if is_zip:
                    extracted = _extract_zip(fp)
                    if extracted:
                        if fp in local_files:
                            local_files.remove(fp)
                        local_files.extend(extracted)
        except Exception as e:
            errors.append("attachment stage failed for {}: {}".format(full_url, e))

    try:
        # ── Build markdown ───────────────────────────────────────────
        md_content = _build_markdown(
            title, full_url, section_name, tab_path, infodate,
            detail.get("info_time", ""), detail.get("source", ""),
            (detail.get("content_text", "") or "").replace("\r\n", "\n"),  # ▶ CRLF 归一
            attachments,
        )
        if local_files:
            appendix = _build_attachment_appendix(local_files)
            if appendix:
                md_content += "\n" + appendix + "\n"

        item_dict: Dict[str, Any] = {
            "title": title,
            "url": full_url,
            "date": infodate or detail.get("info_time", ""),
            "content": md_content,
            "content_markdown": md_content,
            "site_id": SITE_ID,
            "site_display": SITE_DISPLAY,
            "section_name": section_name,
            "info_type": INFO_TYPE,
            "tab_path": tab_path,
            "category_num": category_num,
            "info_source": detail.get("source", ""),
            "zhuanzai": row.get("zhuanzai", ""),
            "strcomment": row.get("strcomment", ""),
            "attachment_count": len(local_files),
            "captcha_blocked_count": captcha_blocked,
            "attachments": [
                {"file_name": a.get("name", ""), "file_url": a.get("url", "")}
                for a in attachments
            ],
        }

        # ── 1. crawler_result via CollectionWriter (去重+日期过滤) ───
        result_id = writer.write_all(
            item=item_dict,
            site_id=SITE_ID,
            category=category,
            task_id=task_id,
            site_display=SITE_DISPLAY,
        )
        if not result_id:
            return 0, 0, 0

        # ── 2. KB markdown upload via StoragePipeline ───────────────
        kb_docs = 0
        try:
            store_result = pipeline.store(item_from_dict(item_dict, site_id=SITE_ID))
            if store_result.get("doc_id"):
                kb_docs += 1
        except Exception as e:
            logger.warning("Pipeline store failed for %s: %s", full_url[:80], e)

        # ── 3. Local attachment files → KB (网关下载, pipeline 无法走 URL) ──
        att_docs = 0
        if local_files and kb_id:
            try:
                from rag.svr.crawler_engine.kb_uploader import KBUploader

                kb_uploader = KBUploader(kb_id, tenant_id)
                for fp in local_files:
                    if os.path.exists(fp) and os.path.getsize(fp) > 0:
                        try:
                            doc_ids = kb_uploader.upload_file(fp)
                            if doc_ids:
                                att_docs += 1
                        except Exception as e:
                            logger.warning(
                                "Attachment KB upload failed for %s: %s",
                                os.path.basename(fp), e,
                            )
            except Exception as e:
                logger.warning("KBUploader init failed: %s", e)

        _safe_print("{}   OK ({} chars, {} files, captcha_blocked={})".format(
            _TAG_PREFIX, len(md_content), len(local_files), captcha_blocked))
        sys.stdout.flush()
        return 1, kb_docs, att_docs
    finally:
        if tmp_dir and os.path.isdir(tmp_dir):
            shutil.rmtree(tmp_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# run() — custom_runner entry point
# ---------------------------------------------------------------------------

def _run_locked(tenant_id: str, kb_id: str, task_name: str, task_id: str,
                category: str, date_filter: str, full_crawl: bool,
                output_dir: str) -> dict:
    from rag.svr.crawler_engine.collection_writer import CollectionWriter
    from rag.svr.crawler_engine.storage_pipeline import StoragePipeline

    writer = CollectionWriter(kb_id=kb_id, tenant_id=tenant_id, date_filter=date_filter)
    pipeline = StoragePipeline(
        kb_id=kb_id,
        tenant_id=tenant_id,
        site_id=SITE_ID,
        site_display=SITE_DISPLAY,
        task_name=task_name,
        output_dir=output_dir or None,
        skip_attachments=True,
        writer_mode="collection",
        category=category,
        task_id=task_id,
        date_filter=date_filter,
    )

    # 目标日期: date_filter=today/具体日期 → 只保留该日条目; 无 date_filter(full) → 不过滤
    # ▶ 用户口径: 无论 full/daily, 每个页签只抓列表第 1 页 (按日期倒序, 第一页即覆盖当日)
    target_date = ""
    if date_filter:
        df = date_filter.strip().lower()
        if df == "today":
            target_date = datetime.now().strftime("%Y-%m-%d")
        else:
            target_date = _normalize_date(date_filter)

    total_items = 0
    total_kb = 0
    total_att = 0
    pages_scanned = 0
    errors: List[str] = []
    seen_urls: set = set()

    _safe_print("{} Mode: {} | target_date={!r} | tabs={} (first page only)".format(
        _TAG_PREFIX, "full(first-page-per-tab)" if full_crawl else "daily(first-page-per-tab)",
        target_date, len(TABS)))
    sys.stdout.flush()

    for tab_idx, (category_num, section_name, tab_path) in enumerate(TABS, 1):
        _safe_print("{} [{}/{}] {} {} ...".format(_TAG_PREFIX, tab_idx, len(TABS),
                                                  section_name, tab_path))
        sys.stdout.flush()

        rows = _fetch_tab_rows(category_num, 0)
        pages_scanned += 1
        if not rows:
            continue

        for row in rows:
            infourl = (row.get("infourl") or "").strip()
            if not infourl:
                continue
            infodate = _normalize_date(row.get("infodate", ""))
            # 列表按日期倒序: 早于目标日 → 本页剩余条目必然更旧, 直接结束本页签
            if target_date and infodate and infodate < target_date:
                break
            if target_date and infodate and infodate != target_date:
                continue
            if infourl in seen_urls:
                continue
            seen_urls.add(infourl)

            _safe_print("{}   -> {}".format(_TAG_PREFIX,
                                            html_mod.unescape(row.get("title") or "")[:60]))
            sys.stdout.flush()
            _request_delay()
            try:
                stored, kb_n, att_n = _process_item(
                    row, category_num, section_name, tab_path,
                    writer, pipeline, tenant_id, kb_id, category, task_id, errors,
                )
                total_items += stored
                total_kb += kb_n
                total_att += att_n
            except Exception as e:
                msg = "Error processing {}: {}".format(infourl, e)
                logger.exception(msg)
                errors.append(msg)

    wstats = writer.stats
    total_new = wstats.get("results_new", 0)

    _safe_print("\n" + "=" * 60)
    _safe_print("DONE  pages={}  items_stored={}  new={}  kb={}  att={}  errors={}".format(
        pages_scanned, total_items, total_new, total_kb, total_att, len(errors)))
    _safe_print("=" * 60)
    sys.stdout.flush()

    try:
        pipeline.cleanup()
    except Exception:
        pass

    # status 契约: unified_crawler 仅在 status=="error" 时判 fail
    if errors and total_items == 0:
        status = "error"
    elif errors:
        status = "partial"
    else:
        status = "success"

    return {
        "status": status,
        "pages": pages_scanned,
        "items_found": len(seen_urls),
        "items_new": total_new,
        "kb_uploaded": total_kb,
        "attachments_uploaded": total_att,
        "errors": errors,
    }


def run(
    tenant_id: str = "",
    kb_id: str = "",
    task_name: str = "",
    task_id: str = "",
    writer_mode: str = "collection",
    category: str = CATEGORY,
    date_filter: str = "",
    full_crawl: bool = False,
    force_run: bool = False,
    site_config: Any = None,
    output_dir: str = "",
) -> dict:
    """Custom runner entry point called by unified_crawler.py."""
    _kb_id = kb_id or KB_ID_DEFAULT

    _safe_print("=" * 60)
    _safe_print("龙岩市公共资源交易中心 — 交易信息采集 (custom_runner)")
    _safe_print("Tenant: {}  KB: {}".format(tenant_id, _kb_id))
    _safe_print("Date filter: {}  Category: {}".format(date_filter or "none", category))
    _safe_print("Full crawl: {}  Force run: {}".format(full_crawl, force_run))
    _safe_print("=" * 60)
    sys.stdout.flush()

    # ── 分布式锁: 与 engine/detector 共用 key ──
    # SiteDetector.detect() 启动时检查 crawler_engine:{tenant}:{site}, 持锁期间探针
    # 直接跳过 (reason="locked"), 避免采集进行中造成签名抖动; 探测器调度器
    # (crawler_detector.py) 另有独立的探针并发锁 detector:lock:{tenant}:{site}。
    # TTL 3h 覆盖一次全量采集 (1-2.5h); 进程中途崩溃时锁残留至 TTL, 用 --force 清除。
    lock = None
    lock_key = "crawler_engine:{}:{}".format(tenant_id, SITE_ID)
    if RedisDistributedLock is not None:
        if force_run and REDIS_CONN is not None:
            try:
                REDIS_CONN.delete(lock_key)
                logging.info("longyan_ggzy: force mode, cleared lock %s", lock_key)
            except Exception:
                pass
        lock = RedisDistributedLock(lock_key, timeout=_LOCK_TIMEOUT, blocking_timeout=0)
        if not lock.acquire():
            msg = "another crawl is running (lock {})".format(lock_key)
            _safe_print("{} SKIPPED: {}".format(_TAG_PREFIX, msg))
            return {
                "status": "skipped",
                "pages": 0, "items_found": 0, "items_new": 0,
                "kb_uploaded": 0, "attachments_uploaded": 0,
                "errors": [msg],
            }

    try:
        return _run_locked(tenant_id, _kb_id, task_name, task_id,
                           category or CATEGORY, date_filter, full_crawl, output_dir)
    finally:
        if lock is not None:
            try:
                lock.release()
            except Exception:
                pass
