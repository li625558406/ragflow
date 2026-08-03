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
appUrlFlag=ztb001&siteGuid=.. — 可能被 pageVerify 验证码拦截, 代码优雅降级
(记录 captcha_blocked_count, 附件 URL 仍写入 markdown/extracted_json)。

反爬: WAF 短时 ~30 快速请求→403(~20s 自愈); 深分页(8+)出验证码。
策略: 请求间隔 0.8-2.0s; full 模式每页签只取第 1 页(pageSize=30);
日常(date_filter)模式每页签最多翻 5 页且遇日期早于目标日即停。

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
_MAX_DAILY_PAGES = 5                  # date_filter 模式每页签最大翻页数(深分页有验证码)
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
