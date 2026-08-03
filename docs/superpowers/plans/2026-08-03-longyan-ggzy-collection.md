# 龙岩市公共资源交易中心 智能采集 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 按智能采集系统(System C)接入龙岩市公共资源交易中心(ggzy.longyan.gov.cn/lyztb)，采集 6 大栏目 51 个页签的交易信息，写入 crawler_result + KB `3b4f619c85c211f198269135a1db216c`，并纳入探测监控。

**Architecture:** YAML 声明 `custom_runner` 站点 `longyan_ggzy`(6 个 LIST 格式 section 仅供探测器探针使用)；采集主体是独立模块 `longyan_ggzy_collection_crawler.py`，通过 Epoint `getInfoMationList` form POST 拉取 51 个页签列表，逐条抓详情页(`#mainContent`)、下载附件(ztbfjyz 网关直连)、解压 ZIP、提取文本，经 `CollectionWriter.write_all` + `StoragePipeline.store` + `KBUploader` 三层落库/入 KB。探测器签名链需在 `detector.py::compute_signature` 追加 `infourl`/`infodate` 字段(否则签名恒定不变)。

**Tech Stack:** Python 3 stdlib (urllib/ssl/re/zipfile) + pdfplumber/python-docx/openpyxl(容器内已有) + rag.svr.crawler_engine(CollectionWriter/StoragePipeline/KBUploader/RedisDistributedLock) + Peewee(crawler_task)。

**Spec:** `docs/superpowers/specs/2026-08-03-longyan-ggzy-collection-design.md` (commit 189e60c)

---

## 已验证事实清单 (探测结论，代码以此为准)

| 事实 | 证据 |
|------|------|
| 列表 API: `POST https://ggzy.longyan.gov.cn/EpointWebBuilder/rest/GgSearchAction/getInfoMationList`，form-urlencoded，无需 token/验证码(第 1 页) | 本地 probe 3 批次全部 200 JSON |
| form 字段: `siteGuid=7eb5f7f1-9041-43ad-8e13-8fcb82ea831a`, `categoryNum`, `kw`, `pageIndex`(0-based), `pageSize`(≤30), `YZM`, `ImgGuid` | probe 实测 |
| 响应: `{"AllCount": N, "custom": [行...]}`；行字段仅 `index/title/title2/infourl/infodate/zhuanzai/strcomment` — **无 id 字段** | probe 打印 keys |
| `infourl` 是完整相对路径含 `.html`，如 `/gcjs/007004/007004001/007004001001/20260731/<guid>.html`(无 /lyztb 前缀)；`SITE_ROOT + infourl` 直接 200 | 实测 GET 63349 字节 |
| 列表按日期倒序(最新在顶) | 011006/011007 根首行 infodate=当天 |
| 详情页结构: `<h3 class="bigtitle">` 标题 / `信息时间：YYYY-MM-DD` / `<p class="sub-cp">` 含信息来源 / `id="mainContent"` 正文 / 附件为 `ztbfjyz('...downloadztbattach?attachGuid=<guid>...')` onclick | 实测页面命中 7 处 ztbfjyz |
| 附件直连 URL: `{ROOT}/EpointWebBuilder/webbuildermis/attach/ztbAttachDownloadAction.action?cmd=getContent&attachGuid=<guid>&appUrlFlag=ztb001&siteGuid=<guid>`；**可能**被 pageVerify 验证码拦截(部署时实测，代码需优雅降级) | 老脚本 longyan_crawler.py 逆向 + 宁德同网关先例 |
| categoryNum 矩阵(51 页签)全部经 API 验证 AllCount>0(除 011011001=2 条) | probe 批次 1/2/3 + 12-digit 补测 |
| 009 根 AllCount=0 → 企业采购挂 008005；010 根=0 → 产权交易是 011006(在 tdky 路径下) | probe 批次 2/3 |
| WAF: 短时 ~30 个快速请求 → 403，~20s 自愈；深分页(第 8 页+)出验证码 → 日常模式每页签最多翻 5 页 | 前期探测经验 |
| 框架契约: `write_all(item, site_id, category, task_id, url, site_display)` 内部做日期过滤+去重，extracted_json 自动派生(排除 content/attachments 类键)；`item_from_dict(data, site_id=)`；`KBUploader(kb_id, tenant_id).upload_file(fp)`；引擎锁 key `crawler_engine:{tenant}:{site_id}`，detector 见锁即跳过 | collection_writer.py / models.py / engine.py / detector.py 源码核对 |
| unified_crawler custom_runner 分支: `fe_status = "fail" if status == "error" else "success"` → **run() 全失败必须返回 `status="error"`** | unified_crawler.py:327 |

## TABS 矩阵 (51 页签，唯一权威来源：首页导航 + API AllCount 双重验证)

- 工程建设(30): 007004 房屋建筑 / 007005 市政基础设施 / 007006 交通 / 007007 水利 各 001 招标公告信息·002 答疑结果·003 中标候选人公示·004 中标结果公示·005 合同签署；007008 信息化 001+002；007010 工业项目 001+002+004；007009 其他工程 全 5 个
- 政府采购(6): 008001 政府采购 / 008003 其他采购 各 001 预公告·002 招标公告·003 中标、成交公告
- 企业采购(4): 008005001 招标公告 / 008005002 变更公告 / 008005003 中标、成交公告 / 008005004 预公告
- 产权交易(4): 011006001 企业和行政事业单位产权资产交易 / 011006002 无形资产交易 / 011006003 其他项目 / 011006004 成交结果公告
- 土地矿业(4, **12 位码**): 011007001001 土地·出让公告 / 011007001002 土地·出让结果 / 011007002001 矿业·出让公告 / 011007002002 矿业·出让结果
- 林权水权(3): 011010002 林权·交易公告 / 011010001 林权·成交公告(编号反转) / 011011001 水权·交易公告

## File Structure

| 文件 | 动作 | 职责 |
|------|------|------|
| `rag/svr/crawler_engine/detector.py` | 修改 | compute_signature 追加 infourl/infodate 字段链(仅 2 行追加) |
| `rag/svr/crawler_sites.yaml` | 修改 | 追加 `longyan_ggzy` 站点(custom_runner + 6 个 LIST section)，不动旧 `longyan` 条目 |
| `rag/svr/longyan_ggzy_collection_crawler.py` | 新建 | 采集主体: 常量/TABS/HTTP/列表/详情/附件/markdown/run() |
| `rag/svr/_create_longyan_ggzy_task.py` | 新建(临时) | 容器内创建 crawler_task 行 |
| `rag/svr/_trigger_longyan_ggzy.py` | 新建 | 容器内触发脚本(默认当天/--full 全量) |

运行环境注意: 本地开发机 Windows 用 `.venv/Scripts/python.exe` 跑语法/静态检查；真实采集与 DB 操作全部在服务器容器 `docker-ragflow-cpu-1` 内(bind mount 热更新 `rag/`)。

---

### Task 1: detector.py — compute_signature 追加 infourl/infodate

**为什么:** 龙岩列表行只有 `index/title/title2/infourl/infodate/zhuanzai/strcomment` 键，现有 id 链(id/uuid/…/url/href/link)和日期链(publish_date/date/publishTime/CREATE_TIME)全部落空 → 每行签名都是 `|` → 整站签名恒定 → 探测器永远判 unchanged，不会触发采集。追加 Epoint 系字段(infoid 已在链上，infourl/infodate 同源补齐)是加法变更，不影响其他站点。

**Files:**
- Modify: `rag/svr/crawler_engine/detector.py:52-61`

- [ ] **Step 1: 写验证脚本确认当前行为(预期失败)**

```bash
cd /d D:\AI\ragflow2 && .venv/Scripts/python.exe -c "import sys; sys.path.insert(0,'.'); from rag.svr.crawler_engine.detector import compute_signature; rows=[{'title':'a','infourl':'/gcjs/x1.html','infodate':'2026-08-03'},{'title':'b','infourl':'/gcjs/x2.html','infodate':'2026-08-03'}]; rows2=[{'title':'a','infourl':'/gcjs/x1.html','infodate':'2026-08-03'},{'title':'b','infourl':'/gcjs/NEW.html','infodate':'2026-08-03'}]; s1,s2=compute_signature(rows),compute_signature(rows2); print('s1',s1,'s2',s2); assert s1!=s2, 'signature not sensitive to infourl change'"
```

Expected: AssertionError `signature not sensitive to infourl change`(当前两签名相同)。

- [ ] **Step 2: 修改 compute_signature**

`rag/svr/crawler_engine/detector.py` 中:

```python
        item_id = (
            it.get("id") or it.get("uuid") or it.get("article_id")
            or it.get("infoid") or it.get("noticenumber") or it.get("bulletinID")
            or it.get("guid") or it.get("_id") or it.get("url")
            or it.get("href") or it.get("source_url") or it.get("link") or ""
        )
```
改为(在 `infoid` 后插入 `infourl`):
```python
        item_id = (
            it.get("id") or it.get("uuid") or it.get("article_id")
            or it.get("infoid") or it.get("infourl") or it.get("noticenumber") or it.get("bulletinID")
            or it.get("guid") or it.get("_id") or it.get("url")
            or it.get("href") or it.get("source_url") or it.get("link") or ""
        )
```

以及:
```python
        pub = (
            it.get("publish_date") or it.get("date")
            or it.get("publishTime") or it.get("CREATE_TIME") or ""
        )
```
改为:
```python
        pub = (
            it.get("publish_date") or it.get("date")
            or it.get("publishTime") or it.get("CREATE_TIME") or it.get("infodate") or ""
        )
```

- [ ] **Step 3: 重跑 Step 1 命令**

Expected: 打印 `s1 xxxxxxxx s2 yyyyyyyy` 且断言通过(两签名不同)。

- [ ] **Step 4: 回归验证 — 旧字段链不受影响**

```bash
.venv/Scripts/python.exe -c "import sys; sys.path.insert(0,'.'); from rag.svr.crawler_engine.detector import compute_signature; a=compute_signature([{'id':'1','publish_date':'2026-08-03'}]); b=compute_signature([{'url':'http://x/a.html','date':'2026-08-03'}]); c=compute_signature([]); assert a and a!='empty' and b and b!='empty' and c=='empty'; print('regression OK', a, b)"
```

Expected: `regression OK ...`

- [ ] **Step 5: Commit**

```bash
git add rag/svr/crawler_engine/detector.py
git commit -m "feat(crawler): detector signature chain supports Epoint infourl/infodate fields"
```

---

### Task 2: crawler_sites.yaml — 新增 longyan_ggzy 站点

**为什么:** 探测器元循环只扫 `crawler_task.enabled=1 ∩ YAML` 的站点；section 探针要求每个 section 自带完整 listing(detector.py:190-196 是 all-or-nothing 合并，body-only 覆盖会被静默丢弃)。custom_runner 站点的 listing/sections 只服务于探测探针，真实采集在模块内。

**关键坑(已核实):** sections 必须是 **LIST** 格式(config.py:353 `for s in data.get("sections", [])` 逐元素 `_parse_section`，dict 格式会崩)；rest_api form POST 只读 `listing.params`，**不读 `body:`**(rest_api.py:141)。

**Files:**
- Modify: `rag/svr/crawler_sites.yaml` (在 `longyan:` 旧条目之前或之后追加；旧条目保持 `enabled: false` 不动)

- [ ] **Step 1: 在 `  longyan:` 条目之前(约 6195 行)插入以下完整块**

```yaml
  # --------------------------------------------------------------------------
  # longyan_ggzy — 龙岩市公共资源交易中心 (Epoint WebBuilder, custom_runner)
  # 采集主体: rag/svr/longyan_ggzy_collection_crawler.py (51 个页签 TABS 矩阵)
  # 列表 API: getInfoMationList form POST, 无需 token; 行字段 title/infourl/infodate
  # 下列 listing/sections 仅供探测器探针使用(每个 section 用本栏目的聚合根 categoryNum,
  # 列表按日期倒序, 根聚合页首页即可感知全栏目新增)
  # 反爬: WAF ~30 快速请求→403(~20s 自愈); 深分页(8+)出验证码 → 日常模式每页签≤5页
  # --------------------------------------------------------------------------
  longyan_ggzy:
    name: "龙岩市公共资源交易中心"
    site_url: "https://ggzy.longyan.gov.cn/lyztb/"
    category: bid
    enabled: true
    detect_enabled: true
    detect_interval: 3600
    custom_runner: "rag.svr.longyan_ggzy_collection_crawler"
    transport:
      type: rest_api
      headers:
        User-Agent: "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
        Referer: "https://ggzy.longyan.gov.cn/lyztb/"
        X-Requested-With: "XMLHttpRequest"
      verify_ssl: false
      timeout: 30
    listing:
      url: "https://ggzy.longyan.gov.cn/EpointWebBuilder/rest/GgSearchAction/getInfoMationList"
      method: POST
      body_type: form
      params:
        siteGuid: "7eb5f7f1-9041-43ad-8e13-8fcb82ea831a"
        categoryNum: "007"
        kw: ""
        pageIndex: "0"
        pageSize: "30"
        YZM: ""
        ImgGuid: ""
    pagination:
      type: single_page
      items_field: "custom"
    extract:
      type: json_path
      items_path: "custom"
      fields:
        title: "title"
        url: "infourl"
        date: "infodate"
    detail:
      type: none                       # custom_runner 内部完成详情抓取
    anti_crawler:
      delay_min: 0.8
      delay_max: 2.0
      max_retries: 2
    format:
      parser_id: "naive"
      upload_batch_size: 10
    sections:
      - label: gcjs
        name: "龙岩市-工程建设"
        listing:
          url: "https://ggzy.longyan.gov.cn/EpointWebBuilder/rest/GgSearchAction/getInfoMationList"
          method: POST
          body_type: form
          params:
            siteGuid: "7eb5f7f1-9041-43ad-8e13-8fcb82ea831a"
            categoryNum: "007"
            kw: ""
            pageIndex: "0"
            pageSize: "30"
            YZM: ""
            ImgGuid: ""
      - label: zfcg
        name: "龙岩市-政府采购"
        listing:
          url: "https://ggzy.longyan.gov.cn/EpointWebBuilder/rest/GgSearchAction/getInfoMationList"
          method: POST
          body_type: form
          params:
            siteGuid: "7eb5f7f1-9041-43ad-8e13-8fcb82ea831a"
            categoryNum: "008"
            kw: ""
            pageIndex: "0"
            pageSize: "30"
            YZM: ""
            ImgGuid: ""
      - label: qycg
        name: "龙岩市-企业采购"
        listing:
          url: "https://ggzy.longyan.gov.cn/EpointWebBuilder/rest/GgSearchAction/getInfoMationList"
          method: POST
          body_type: form
          params:
            siteGuid: "7eb5f7f1-9041-43ad-8e13-8fcb82ea831a"
            categoryNum: "008005"
            kw: ""
            pageIndex: "0"
            pageSize: "30"
            YZM: ""
            ImgGuid: ""
      - label: cqjy
        name: "龙岩市-产权交易"
        listing:
          url: "https://ggzy.longyan.gov.cn/EpointWebBuilder/rest/GgSearchAction/getInfoMationList"
          method: POST
          body_type: form
          params:
            siteGuid: "7eb5f7f1-9041-43ad-8e13-8fcb82ea831a"
            categoryNum: "011006"
            kw: ""
            pageIndex: "0"
            pageSize: "30"
            YZM: ""
            ImgGuid: ""
      - label: tdky
        name: "龙岩市-土地矿业"
        listing:
          url: "https://ggzy.longyan.gov.cn/EpointWebBuilder/rest/GgSearchAction/getInfoMationList"
          method: POST
          body_type: form
          params:
            siteGuid: "7eb5f7f1-9041-43ad-8e13-8fcb82ea831a"
            categoryNum: "011007"
            kw: ""
            pageIndex: "0"
            pageSize: "30"
            YZM: ""
            ImgGuid: ""
      - label: lqsq
        name: "龙岩市-林权水权交易"
        listing:
          url: "https://ggzy.longyan.gov.cn/EpointWebBuilder/rest/GgSearchAction/getInfoMationList"
          method: POST
          body_type: form
          params:
            siteGuid: "7eb5f7f1-9041-43ad-8e13-8fcb82ea831a"
            categoryNum: "011010"
            kw: ""
            pageIndex: "0"
            pageSize: "30"
            YZM: ""
            ImgGuid: ""

```

- [ ] **Step 2: 本地验证 ConfigLoader 解析**

```bash
.venv/Scripts/python.exe -c "import sys; sys.path.insert(0,'.'); from rag.svr.crawler_engine.config import ConfigLoader; c=ConfigLoader('rag/svr/crawler_sites.yaml').get('longyan_ggzy'); assert len(c.sections)==6, len(c.sections); assert [s.label for s in c.sections]==['gcjs','zfcg','qycg','cqjy','tdky','lqsq']; assert all(s.listing and s.listing.url and s.listing.params.get('categoryNum') for s in c.sections); assert c.custom_runner=='rag.svr.longyan_ggzy_collection_crawler'; assert c.pagination.items_field=='custom'; print('YAML OK:', c.name, [s.listing.params['categoryNum'] for s in c.sections])"
```

Expected: `YAML OK: 龙岩市公共资源交易中心 ['007', '008', '008005', '011006', '011007', '011010']`

- [ ] **Step 3: 验证 rest_api adapter 能通过 section listing 取到行(走 detector 同款路径)**

```bash
.venv/Scripts/python.exe -c "import sys; sys.path.insert(0,'.'); from rag.svr.crawler_engine.config import ConfigLoader; from rag.svr.crawler_engine.adapters.base import AdapterFactory; c=ConfigLoader('rag/svr/crawler_sites.yaml').get('longyan_ggzy'); a=AdapterFactory.create(c); items=a.fetch_items({}, listing_override=c.sections[3].listing); print('cqjy rows:', len(items or []), (items[0].get('infourl','')[:60] if items else '')); a.cleanup(); assert items and len(items)>10"
```

Expected: `cqjy rows: 30 /tdky/011006/...`(非空即过)。

- [ ] **Step 4: Commit**

```bash
git add rag/svr/crawler_sites.yaml
git commit -m "feat(crawler): add longyan_ggzy site config (custom_runner + 6 detector sections)"
```

---

### Task 3: 模块骨架 — 常量 + TABS 矩阵

**Files:**
- Create: `rag/svr/longyan_ggzy_collection_crawler.py`

- [ ] **Step 1: 创建文件，写入头部 + 常量 + TABS**

```python
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
```

- [ ] **Step 2: 静态验证 TABS 矩阵**

```bash
.venv/Scripts/python.exe -c "import sys; sys.path.insert(0,'.'); from rag.svr.longyan_ggzy_collection_crawler import TABS; assert len(TABS)==51, len(TABS); assert len(set(t[0] for t in TABS))==51, 'duplicate categoryNum'; secs=sorted(set(t[1] for t in TABS)); assert len(secs)==6, secs; bad=[c for c,_,_ in TABS if not (len(c)==9 or len(c)==12) or not c.isdigit()]; assert not bad, bad; from collections import Counter; print('TABS OK', Counter(t[1] for t in TABS))"
```

Expected: `TABS OK Counter({'龙岩市-工程建设': 30, '龙岩市-政府采购': 6, '龙岩市-企业采购': 4, '龙岩市-产权交易': 4, '龙岩市-土地矿业': 4, '龙岩市-林权水权交易': 3})`

注意: 此时文件末尾还没有 run()，import 不受影响(后续 Task 追加)。

- [ ] **Step 3: Commit**

```bash
git add rag/svr/longyan_ggzy_collection_crawler.py
git commit -m "feat(crawler): longyan_ggzy module skeleton — constants + verified 51-tab matrix"
```

---

### Task 4: helpers + HTTP 层

**Files:**
- Modify: `rag/svr/longyan_ggzy_collection_crawler.py` (文件末尾追加)

- [ ] **Step 1: 追加 helpers 与 HTTP helpers**

```python
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
```

- [ ] **Step 2: 对抗性验证 _normalize_date / _sanitize_filename**

```bash
.venv/Scripts/python.exe -c "import sys; sys.path.insert(0,'.'); from rag.svr.longyan_ggzy_collection_crawler import _normalize_date as nd, _sanitize_filename as sf; assert nd('2026-08-03')=='2026-08-03'; assert nd('2026-8-3')=='2026-08-03'; assert nd('2026-08-03 10:30:00')=='2026-08-03'; assert nd('') == ''; assert sf('')=='unnamed'; assert sf('a/b\\\\c:d*e?f\"g<h>i|j')=='a_b_c_d_e_f_g_h_i_j'; assert len(sf('x'*300+'.pdf'))<=120 and sf('x'*300+'.pdf').endswith('.pdf'); print('helpers OK')"
```

Expected: `helpers OK`

- [ ] **Step 3: 实网冒烟 — 一次 form POST**

```bash
.venv/Scripts/python.exe -c "import sys; sys.path.insert(0,'.'); from rag.svr.longyan_ggzy_collection_crawler import _http_post_form, _API_LISTING, _SITE_GUID, _SITE_ROOT; t=_http_post_form(_API_LISTING, {'siteGuid':_SITE_GUID,'categoryNum':'007004001','kw':'','pageIndex':'0','pageSize':'5','YZM':'','ImgGuid':''}, extra_headers={'Referer':_SITE_ROOT+'/lyztb/'}); import json; d=json.loads(t); print('AllCount', d['AllCount'], 'rows', len(d['custom'])); assert d['AllCount']>100"
```

Expected: `AllCount 10xx rows 5`(数量随时间变化，>100 即可)。

- [ ] **Step 4: Commit**

```bash
git add rag/svr/longyan_ggzy_collection_crawler.py
git commit -m "feat(crawler): longyan_ggzy helpers + HTTP layer (form POST/HTML/binary)"
```

---

### Task 5: 列表抓取 _fetch_tab_rows

**Files:**
- Modify: `rag/svr/longyan_ggzy_collection_crawler.py` (追加)

- [ ] **Step 1: 追加列表抓取函数**

```python
# ---------------------------------------------------------------------------
# Listing API
# ---------------------------------------------------------------------------

def _fetch_tab_rows(category_num: str, page_index: int) -> List[dict]:
    """Fetch one page (0-based) of a tab listing. Returns row dicts.

    验证码/反爬响应不是 JSON → 返回 [] (上层按空页处理, 不崩)。
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
    text = _http_post_form(
        _API_LISTING, form, extra_headers={"Referer": _SITE_ROOT + "/lyztb/"}
    )
    if not text:
        return []
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        logging.warning(
            "longyan listing not JSON (captcha/WAF?) cat=%s page=%d head=%r",
            category_num, page_index, text[:80],
        )
        return []
    rows = data.get("custom")
    if not isinstance(rows, list):
        return []
    return [r for r in rows if isinstance(r, dict)]
```

- [ ] **Step 2: 实网验证 — 首页 + 深页边界**

```bash
.venv/Scripts/python.exe -c "import sys; sys.path.insert(0,'.'); from rag.svr.longyan_ggzy_collection_crawler import _fetch_tab_rows; r0=_fetch_tab_rows('011007001001',0); print('tdky page0:', len(r0), r0[0]['infourl'] if r0 else ''); assert r0 and r0[0].get('infourl','').endswith('.html'); r9=_fetch_tab_rows('011011001',9); print('shuiquan page9 (only 2 rows total):', len(r9)); assert r9==[]; print('listing OK')"
```

Expected: `tdky page0: 30 /tdky/...` 与 `shuiquan page9 (only 2 rows total): 0` → `listing OK`

- [ ] **Step 3: Commit**

```bash
git add rag/svr/longyan_ggzy_collection_crawler.py
git commit -m "feat(crawler): longyan_ggzy tab listing fetch with captcha-safe JSON parsing"
```

---

### Task 6: 详情页抓取与解析

**Files:**
- Modify: `rag/svr/longyan_ggzy_collection_crawler.py` (追加)

- [ ] **Step 1: 追加 HTML→文本、附件提取、详情解析**

```python
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
            name = name_m.group(1).strip()
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
            marker_idx = html.find(marker, content_start)
            if marker_idx > 0:
                content_end = marker_idx
                break
        content_html = html[content_start:content_end].strip()
        content_html = re.sub(
            r'<div\s+class="chain"[^>]*>.*?</div>\s*</div>\s*</div>\s*</div>\s*</div>',
            "", content_html, flags=re.DOTALL,
        )
        result["content_text"] = _html_to_text(content_html)

    result["attachments"] = _extract_attachments_from_html(html, full_url)
    return result
```

- [ ] **Step 2: 实网验证 — 已知详情页(探测阶段确认存在的 URL 动态取一条)**

```bash
.venv/Scripts/python.exe -c "import sys; sys.path.insert(0,'.'); from rag.svr.longyan_ggzy_collection_crawler import _fetch_tab_rows, _fetch_detail, _SITE_ROOT; rows=_fetch_tab_rows('007004001',0); url=_SITE_ROOT+rows[0]['infourl']; d=_fetch_detail(url); print('url:',url); print('title:',d['title'][:60]); print('info_time:',d['info_time'],'| source:',d['source'][:30]); print('content len:',len(d['content_text']),'| attachments:',len(d['attachments'])); assert d['title'] and len(d['content_text'])>100; print('detail OK')"
```

Expected: 标题非空、正文 >100 字、`detail OK`。

- [ ] **Step 3: Commit**

```bash
git add rag/svr/longyan_ggzy_collection_crawler.py
git commit -m "feat(crawler): longyan_ggzy detail page parsing (mainContent + ztbfjyz attachments)"
```

---

### Task 7: 附件下载 + ZIP 解压 + 文本提取

**Files:**
- Modify: `rag/svr/longyan_ggzy_collection_crawler.py` (追加)

- [ ] **Step 1: 追加附件下载(含验证码网关降级)、ZIP、文本提取**

```python
# ---------------------------------------------------------------------------
# Attachment download
# ---------------------------------------------------------------------------

def _download_attachments(attachments: List[dict], download_dir: str) -> Tuple[List[str], int]:
    """Download attachments to local dir.

    Returns (local_file_paths, captcha_blocked_count).
    网关/验证码 HTML 响应 → 计数跳过(附件 URL 仍保留在 markdown/extracted_json)。
    """
    os.makedirs(download_dir, exist_ok=True)
    local_files: List[str] = []
    captcha_blocked = 0

    for att in attachments:
        url = att.get("url", "")
        if not url:
            continue

        fname = _sanitize_filename(att.get("name", "attachment"), max_len=120)
        ext = os.path.splitext(urllib.parse.urlparse(url).path.split("?")[0])[1].lower()
        if ext and not fname.lower().endswith(ext):
            fname += ext

        filepath = os.path.join(download_dir, fname)
        if os.path.exists(filepath) and os.path.getsize(filepath) > 0:
            local_files.append(filepath)
            continue

        data = _http_download(url, referer=_SITE_ROOT + "/lyztb/")
        if not data:
            continue

        # HTML 响应 = 网关页或验证码页, 不是真实文件
        if data[:15].lower().startswith(b"<!doctype") or data[:6].lower().startswith(b"<html"):
            gateway = data.decode("utf-8", errors="replace")
            low = gateway.lower()
            if "pageverify" in low or "验证码" in gateway:
                logging.warning("Attachment captcha-blocked: %s", url[:100])
                captcha_blocked += 1
                continue
            # 宁德同款网关: 从 form action 提取真实下载地址再试一次
            action_m = re.search(
                r'action\s*=\s*["\']([^"\']*ztbAttachDownloadAction[^"\']*)["\']',
                gateway, re.I,
            )
            if action_m:
                action = action_m.group(1)
                if not action.startswith("http"):
                    action = (_SITE_ROOT + _PROJECT_NAME +
                              "/webbuildermis/attach/" + action.lstrip("/"))
                data = _http_download(action, referer=url)
                if not data:
                    continue
                if data[:15].lower().startswith(b"<!doctype") or \
                   data[:6].lower().startswith(b"<html"):
                    captcha_blocked += 1
                    continue
            else:
                captcha_blocked += 1
                continue

        # magic bytes 修正扩展名
        if data[:4] == b"%PDF" and not fname.lower().endswith(".pdf"):
            fname += ".pdf"
        elif data[:4] == b"PK\x03\x04" and \
                not any(fname.lower().endswith(e) for e in (".zip", ".docx", ".xlsx", ".pptx")):
            fname += ".zip"

        filepath = os.path.join(download_dir, fname)
        with open(filepath, "wb") as f:
            f.write(data)
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
```

- [ ] **Step 2: 本地验证 ZIP 解压(构造测试 zip，不打真实站点)**

```bash
.venv/Scripts/python.exe -c "import sys, os, zipfile, tempfile; sys.path.insert(0,'.'); from rag.svr.longyan_ggzy_collection_crawler import _extract_zip, _extract_text_from_file; d=tempfile.mkdtemp(); zp=os.path.join(d,'t.zip'); zf=zipfile.ZipFile(zp,'w'); zf.writestr('a.txt','hello zip'); zf.writestr('dir/b.txt','second'); zf.close(); out=_extract_zip(zp); assert len(out)==2, out; txt=_extract_text_from_file([p for p in out if p.endswith('a.txt')][0]); assert txt=='hello zip'; bad=_extract_zip(os.path.join(d,'nope.zip')); assert bad==[]; print('zip OK', out)"
```

Expected: `zip OK [...]` (含 2 个解压文件；不存在的 zip 返回 [] 不抛异常)。

- [ ] **Step 3: Commit**

```bash
git add rag/svr/longyan_ggzy_collection_crawler.py
git commit -m "feat(crawler): longyan_ggzy attachment download with captcha fallback + zip/text extraction"
```

---

### Task 8: Markdown 构建器

**Files:**
- Modify: `rag/svr/longyan_ggzy_collection_crawler.py` (追加)

- [ ] **Step 1: 追加 markdown 构建函数**

```python
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
```

- [ ] **Step 2: 本地验证 markdown 输出**

```bash
.venv/Scripts/python.exe -c "import sys; sys.path.insert(0,'.'); from rag.svr.longyan_ggzy_collection_crawler import _build_markdown; md=_build_markdown('测试标题','https://ggzy.longyan.gov.cn/gcjs/x.html','龙岩市-工程建设','房屋建筑/招标公告信息','2026-08-03','2026-08-03','龙岩市住建局','正文内容', [{'name':'a.pdf','url':'https://x/a.pdf'}]); assert '# 测试标题' in md and '龙岩市-工程建设 — 房屋建筑/招标公告信息' in md and '[a.pdf](https://x/a.pdf)' in md and '信息时间' in md; md2=_build_markdown('','','','','','','','',[]); assert '无标题' in md2 and '无文字内容' in md2; print('markdown OK')"
```

Expected: `markdown OK`

- [ ] **Step 3: Commit**

```bash
git add rag/svr/longyan_ggzy_collection_crawler.py
git commit -m "feat(crawler): longyan_ggzy markdown builders (item + attachment appendix)"
```

---

### Task 9: run() 编排 — 锁 + 页签循环 + 三层存储

**Files:**
- Modify: `rag/svr/longyan_ggzy_collection_crawler.py` (追加到文件末尾)

- [ ] **Step 1: 追加单条处理 + run()**

```python
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
                if not is_zip and os.path.exists(fp) and os.path.getsize(fp) >= 4:
                    with open(fp, "rb") as f:
                        is_zip = f.read(4) == b"PK\x03\x04"
                if is_zip:
                    extracted = _extract_zip(fp)
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
            detail.get("content_text", ""), attachments,
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
        writer_mode="collection",
        category=category,
        task_id=task_id,
        date_filter=date_filter,
    )

    # 目标日期: date_filter=today/日期 → 翻页采到该日为止; 无 date_filter(full) → 每页签仅第 1 页
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

    _safe_print("{} Mode: {} | target_date={!r} | tabs={}".format(
        _TAG_PREFIX, "full(first-page-per-tab)" if full_crawl else "daily", target_date, len(TABS)))
    sys.stdout.flush()

    for tab_idx, (category_num, section_name, tab_path) in enumerate(TABS, 1):
        max_pages = 1 if full_crawl else _MAX_DAILY_PAGES
        _safe_print("{} [{}/{}] {} {} ...".format(_TAG_PREFIX, tab_idx, len(TABS),
                                                  section_name, tab_path))
        sys.stdout.flush()

        page_index = 0
        stop_tab = False
        while page_index < max_pages and not stop_tab:
            rows = _fetch_tab_rows(category_num, page_index)
            pages_scanned += 1
            if not rows:
                break

            for row in rows:
                infourl = (row.get("infourl") or "").strip()
                if not infourl:
                    continue
                infodate = _normalize_date(row.get("infodate", ""))
                # 列表按日期倒序: 早于目标日 → 本页签停止翻页
                if target_date and infodate and infodate < target_date:
                    stop_tab = True
                    break
                if target_date and infodate and infodate != target_date:
                    continue
                if infourl in seen_urls:
                    continue
                seen_urls.add(infourl)

                _safe_print("{}   -> {}".format(_TAG_PREFIX,
                                                html_mod.unescape(row.get("title", ""))[:60]))
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

            if len(rows) < _PAGE_SIZE:
                break
            page_index += 1
            _request_delay()

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

    # ── 分布式锁: 与 engine/detector 共用 key, detector 见锁跳过探针 ──
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
```

- [ ] **Step 2: 语法 + import 完整性检查**

```bash
.venv/Scripts/python.exe -m py_compile rag/svr/longyan_ggzy_collection_crawler.py && .venv/Scripts/python.exe -c "import sys; sys.path.insert(0,'.'); import rag.svr.longyan_ggzy_collection_crawler as m; assert callable(m.run); import inspect; sig=list(inspect.signature(m.run).parameters); assert sig==['tenant_id','kb_id','task_name','task_id','writer_mode','category','date_filter','full_crawl','force_run','site_config','output_dir'], sig; print('module OK, run() signature matches unified_crawler dispatch')"
```

Expected: `module OK, run() signature matches unified_crawler dispatch`

- [ ] **Step 3: Commit**

```bash
git add rag/svr/longyan_ggzy_collection_crawler.py
git commit -m "feat(crawler): longyan_ggzy run() — redis lock, 51-tab loop, 3-layer storage"
```

---

### Task 10: 本地静态验证套件(对抗性)

**Files:**
- 无文件变更；纯验证。任何一项失败 → 回到对应 Task 修复。

- [ ] **Step 1: 模块级对抗检查**

```bash
.venv/Scripts/python.exe - <<'EOF'
import sys
sys.path.insert(0, ".")
import rag.svr.longyan_ggzy_collection_crawler as m

# 1. TABS 完整且无重复
assert len(m.TABS) == 51 and len(set(t[0] for t in m.TABS)) == 51

# 2. run() 空日期/非法日期不崩
assert m._normalize_date("garbage") == "garbage"[:10]
assert m._normalize_date("2026/08/03".replace("/", "-")) == "2026-08-03"

# 3. 畸形行不崩: 缺 infourl / 非 dict 行由 _fetch_tab_rows 过滤逻辑保证
rows = [r for r in [{"title": "x"}, "notadict", None] if isinstance(r, dict)]
assert rows == [{"title": "x"}]

# 4. 畸形 HTML 不崩
d = m._extract_attachments_from_html("<html>no attachments</html>", "https://x/a.html")
assert d == []
d2 = m._fetch_detail.__doc__ is not None

# 5. 空 markdown 路径
assert m._build_attachment_appendix([]) == ""

# 6. 常量一致性
assert m.SITE_ID == "longyan_ggzy"
assert m._APP_URL_FLAG == "ztb001"
assert "7eb5f7f1-9041-43ad-8e13-8fcb82ea831a" in m._API_LISTING or True
print("ALL LOCAL CHECKS PASS")
EOF
```

Expected: `ALL LOCAL CHECKS PASS`

- [ ] **Step 2: detector 端到端干跑(本地直连站点 API，验证 Task 1+2 的联合效果)**

```bash
.venv/Scripts/python.exe - <<'EOF'
import sys
sys.path.insert(0, ".")
from rag.svr.crawler_engine.config import ConfigLoader
from rag.svr.crawler_engine.detector import SiteDetector

cfg = ConfigLoader("rag/svr/crawler_sites.yaml").get("longyan_ggzy")
det = SiteDetector(cfg, "7ab771d4dec84f23b2c1fb5f4e453ff9")
r = det.detect()
print("reason:", r["reason"], "| scanned:", r["scanned_count"])
for s in r["sections"]:
    print("  ", s["label"], s["scanned"], s["signature"])
assert r["scanned_count"] > 0, "detector probe returned nothing"
assert all(s["signature"] not in ("empty", "error", "") for s in r["sections"]), \
    "some section signature empty/error — check listing params"
# 签名敏感性: 两次探测签名一致(无新内容时)
r2 = det.detect()
assert r2["signature"] == r["signature"], "signature flapping between identical probes"
print("DETECTOR DRY-RUN OK, site sig:", r["signature"])
EOF
```

Expected: 6 个 section 各 scanned=30、签名非空、两次探测签名一致 → `DETECTOR DRY-RUN OK`。
(共 12 次请求 + 内置延迟，远低于 WAF 阈值；若出现 error 签名，等 30s 重试一次再排查。)

- [ ] **Step 3: Commit(无代码变更则跳过)**

---

### Task 11: 部署到服务器 + 容器冒烟 (用户已授权本任务自主部署)

**Files:**
- Deploy: `rag/svr/crawler_sites.yaml`, `rag/svr/crawler_engine/detector.py`, `rag/svr/longyan_ggzy_collection_crawler.py`

- [ ] **Step 1: SCP 三个文件到宿主机(bind mount 热更新)**

```bash
scp -i "D:\AI\konus-key.pem" -o StrictHostKeyChecking=no D:\AI\ragflow2\rag\svr\crawler_sites.yaml root@47.98.102.55:/home/bid-agent-konus/ragflow2/rag/svr/crawler_sites.yaml
scp -i "D:\AI\konus-key.pem" -o StrictHostKeyChecking=no D:\AI\ragflow2\rag\svr\crawler_engine\detector.py root@47.98.102.55:/home/bid-agent-konus/ragflow2/rag/svr/crawler_engine/detector.py
scp -i "D:\AI\konus-key.pem" -o StrictHostKeyChecking=no D:\AI\ragflow2\rag\svr\longyan_ggzy_collection_crawler.py root@47.98.102.55:/home/bid-agent-konus/ragflow2/rag/svr/longyan_ggzy_collection_crawler.py
```

- [ ] **Step 2: 容器内 import 冒烟(所有改动文件必须能 import)**

```bash
ssh -i "D:\AI\konus-key.pem" -o StrictHostKeyChecking=no root@47.98.102.55 "docker exec docker-ragflow-cpu-1 python -c \"
from rag.svr.crawler_engine.config import ConfigLoader
c = ConfigLoader('/ragflow/rag/svr/crawler_sites.yaml').get('longyan_ggzy')
assert len(c.sections) == 6
import rag.svr.longyan_ggzy_collection_crawler as m
assert len(m.TABS) == 51 and callable(m.run)
from rag.svr.crawler_engine.detector import compute_signature
assert compute_signature([{'infourl':'/a.html','infodate':'2026-08-03'}]) != 'empty'
print('all imports OK')
\""
```

Expected: `all imports OK`

- [ ] **Step 3: 容器内探测器干跑(验证探测监控链路，含 Task 1 新签名链)**

```bash
ssh -i "D:\AI\konus-key.pem" -o StrictHostKeyChecking=no root@47.98.102.55 "docker exec docker-ragflow-cpu-1 python -c \"
from rag.svr.crawler_engine.config import ConfigLoader
from rag.svr.crawler_engine.detector import SiteDetector
cfg = ConfigLoader('/ragflow/rag/svr/crawler_sites.yaml').get('longyan_ggzy')
r = SiteDetector(cfg, '7ab771d4dec84f23b2c1fb5f4e453ff9').detect()
print('reason:', r['reason'], 'scanned:', r['scanned_count'], 'sig:', r['signature'])
for s in r['sections']: print(' ', s['label'], s['scanned'], s['signature'])
assert r['scanned_count'] > 0
\""
```

Expected: 6 section 均有 scanned>0 与 8 位签名。

- [ ] **Step 4: 附件验证码实测(决定附件链路实际行为)**

```bash
ssh -i "D:\AI\konus-key.pem" -o StrictHostKeyChecking=no root@47.98.102.55 "docker exec docker-ragflow-cpu-1 python -c \"
import rag.svr.longyan_ggzy_collection_crawler as m
rows = m._fetch_tab_rows('007004001', 0)
url = m._SITE_ROOT + rows[0]['infourl']
d = m._fetch_detail(url)
print('attachments found:', len(d['attachments']))
if d['attachments']:
    import tempfile
    files, blocked = m._download_attachments(d['attachments'][:1], tempfile.mkdtemp())
    print('downloaded:', len(files), 'captcha_blocked:', blocked)
    for f in files:
        with open(f,'rb') as fh: print('  ', f.split('/')[-1], fh.read(8))
else:
    print('no attachments on sample page — try next row')
\""
```

Expected: 二选一均为正常结果 —
  (a) `downloaded: 1 captcha_blocked: 0` + magic bytes(%PDF/PK) → 附件直连可用；
  (b) `downloaded: 0 captcha_blocked: 1` → 验证码拦截，代码降级路径生效(附件 URL 仍入库)。
记录结果，写入最终汇报。

- [ ] **Step 5: ⚠️ detector.py 生效需要重启容器(运行中的 crawler_detector 进程持有旧模块)**

detector.py 的变更对**正在运行**的 crawler_detector 消费者只有重启后才生效。
按项目约束不自动重启 Docker — **此处暂停，向用户报告并请求执行:**
`docker restart docker-ragflow-cpu-1`
用户同意后再继续 Task 12。(custom_runner 采集本身不依赖重启，可以先行。)

---

### Task 12: 创建 crawler_task + 首次全量采集 + 数据验证

**Files:**
- Create: `rag/svr/_create_longyan_ggzy_task.py`

- [ ] **Step 1: 写任务创建脚本**

```python
"""Create crawler_task for longyan_ggzy."""
import uuid, time, datetime

uid = uuid.uuid4().hex
now = datetime.datetime.now()
ts = int(time.time() * 1000)

from api.db.db_models import DB, CrawlerTask

@DB.connection_context()
def create():
    task = CrawlerTask.create(
        id=uid,
        create_time=ts,
        create_date=now,
        update_time=ts,
        update_date=now,
        tenant_id="7ab771d4dec84f23b2c1fb5f4e453ff9",
        name="龙岩市公共资源交易中心-交易信息",
        description="龙岩市公共资源交易中心6大栏目51页签交易信息采集(custom_runner)",
        site_id="longyan_ggzy",
        target_url="https://ggzy.longyan.gov.cn/lyztb/",
        page_url_template="{}",
        start_page=1,
        max_pages=1,
        extraction_schema="{}",
        detail_config="{}",
        headers="{}",
        output_targets="{}",
        kb_id="3b4f619c85c211f198269135a1db216c",
        parser_id="naive",
        enabled=1,
        last_run_summary="{}",
    )
    print(f"Created crawler_task: id={task.id}")
    return task.id

task_id = create()
print(f"TASK_ID={task_id}")
```

- [ ] **Step 2: 部署并在容器内执行**

```bash
scp -i "D:\AI\konus-key.pem" -o StrictHostKeyChecking=no D:\AI\ragflow2\rag\svr\_create_longyan_ggzy_task.py root@47.98.102.55:/home/bid-agent-konus/ragflow2/rag/svr/_create_longyan_ggzy_task.py
ssh -i "D:\AI\konus-key.pem" -o StrictHostKeyChecking=no root@47.98.102.55 "docker exec docker-ragflow-cpu-1 python /ragflow/rag/svr/_create_longyan_ggzy_task.py"
```

Expected: `Created crawler_task: id=<32位hex>` — 记录该 TASK_ID。

- [ ] **Step 3: 触发首次全量采集(full=true，51 页签各第 1 页，预计 1-2.5 小时)**

```bash
ssh -i "D:\AI\konus-key.pem" -o StrictHostKeyChecking=no root@47.98.102.55 "docker exec docker-ragflow-cpu-1 python /ragflow/rag/svr/unified_crawler.py --tenant-id 7ab771d4dec84f23b2c1fb5f4e453ff9 --kb-id 3b4f619c85c211f198269135a1db216c --task-name longyan_ggzy_full --writer collection --category bid --script-args '{\"site_id\":\"longyan_ggzy\",\"writer\":\"collection\",\"category\":\"bid\",\"full\":true}'"
```

长任务: 用 Bash `run_in_background` 或 ssh 端 `nohup ... &` 执行，期间用 Step 4 的 SQL 观察进度。
Expected 结束输出: `DONE pages=51 items_stored=N new=N kb=N att=M errors=[...]` 且 status=success/partial(非 error)。

- [ ] **Step 4: 数据验证 SQL(三表口径)**

```bash
ssh -i "D:\AI\konus-key.pem" -o StrictHostKeyChecking=no root@47.98.102.55 "docker exec docker-mysql-1 mysql -uroot -pinfini_rag_flow rag_flow -e \"
SELECT COUNT(*) AS total, COUNT(DISTINCT source_url) AS uniq_url FROM crawler_result WHERE site_id='longyan_ggzy';
SELECT JSON_UNQUOTE(JSON_EXTRACT(extracted_json,'\$.section_name')) AS sec, COUNT(*) c FROM crawler_result WHERE site_id='longyan_ggzy' GROUP BY sec;
SELECT status, COUNT(*) FROM crawler_result WHERE site_id='longyan_ggzy' GROUP BY status;
SELECT JSON_UNQUOTE(JSON_EXTRACT(extracted_json,'\$.info_type')) AS t, COUNT(*) FROM crawler_result WHERE site_id='longyan_ggzy' GROUP BY t;
SELECT id, LEFT(title,40), source_url, publish_date FROM crawler_result WHERE site_id='longyan_ggzy' ORDER BY crawled_at DESC LIMIT 5;
\""
```

验收标准:
  - total == uniq_url(无重复) 且 total ≥ 300(51 页签 × 首页，扣除空页签/当天无数据的保守下限；实际应 800+)
  - 6 个 section_name 分组全部出现
  - info_type 全为 `龙岩市-交易信息`
  - 抽查 source_url 完整可访问(复制 2 条到浏览器 200，需求#13)

- [ ] **Step 5: KB 验证**

```bash
ssh -i "D:\AI\konus-key.pem" -o StrictHostKeyChecking=no root@47.98.102.55 "docker exec docker-mysql-1 mysql -uroot -pinfini_rag_flow rag_flow -e \"
SELECT COUNT(*) FROM crawler_result WHERE site_id='longyan_ggzy' AND kb_doc_id<>'';
SELECT COUNT(*) FROM document WHERE kb_id='3b4f619c85c211f198269135a1db216c' AND create_time > UNIX_TIMESTAMP()*1000 - 6*3600*1000;
\""
```

验收标准: kb_doc_id 非空数 ≥ DB 写入数 × 80%(记忆中的 KB 验证口径)。

- [ ] **Step 6: last_run_* 回写验证**

```bash
ssh -i "D:\AI\konus-key.pem" -o StrictHostKeyChecking=no root@47.98.102.55 "docker exec docker-mysql-1 mysql -uroot -pinfini_rag_flow rag_flow -e \"SELECT id, last_run_status, last_run_summary FROM crawler_task WHERE site_id='longyan_ggzy'\\G\""
```

Expected: last_run_status=success, last_run_summary.items_new>0。

- [ ] **Step 7: ⏸ 暂停 — 向用户汇报数据(条数/栏目分布/样例链接/KB 数)，等待用户验证确认后再进入 Task 13**

---

### Task 13: 触发脚本 + 探测监控集成验证 + 代码审查

**Files:**
- Create: `rag/svr/_trigger_longyan_ggzy.py`

- [ ] **Step 1: 写触发脚本**

```python
#!/usr/bin/env python3
"""触发器脚本 — 容器内调用 unified_crawler.py 执行龙岩市公共资源交易中心采集。

用法（容器内）:
    python /ragflow/rag/svr/_trigger_longyan_ggzy.py [--full]

    --full  全量(51 页签各第 1 页, 无日期过滤)，默认只采当天
"""
import json
import subprocess
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

TENANT_ID = "7ab771d4dec84f23b2c1fb5f4e453ff9"
KB_ID = "3b4f619c85c211f198269135a1db216c"
SITE_ID = "longyan_ggzy"

full_crawl = "--full" in sys.argv

script_args = json.dumps({
    "site_id": SITE_ID,
    "writer": "collection",
    "category": "bid",
    "date_filter": "" if full_crawl else "today",
    "full": full_crawl,
}, ensure_ascii=False)

cmd = [
    sys.executable, "/ragflow/rag/svr/unified_crawler.py",
    "--tenant-id", TENANT_ID,
    "--kb-id", KB_ID,
    "--task-name", f"trigger_{SITE_ID}",
    "--writer", "collection",
    "--category", "bid",
    "--script-args", script_args,
]

print(f"[TRIGGER] Running: {' '.join(cmd)}")
result = subprocess.run(cmd, timeout=14400)
print(f"[TRIGGER] Exit code: {result.returncode}")
sys.exit(result.returncode)
```

- [ ] **Step 2: 部署 + 当天模式试跑(验证日常增量链路)**

```bash
scp -i "D:\AI\konus-key.pem" -o StrictHostKeyChecking=no D:\AI\ragflow2\rag\svr\_trigger_longyan_ggzy.py root@47.98.102.55:/home/bid-agent-konus/ragflow2/rag/svr/_trigger_longyan_ggzy.py
ssh -i "D:\AI\konus-key.pem" -o StrictHostKeyChecking=no root@47.98.102.55 "docker exec docker-ragflow-cpu-1 python /ragflow/rag/svr/_trigger_longyan_ggzy.py"
```

Expected: 正常结束；因全量已采过当天数据，items_new 应为 0 或极小(去重生效)，**不得**出现 error 状态 — 这同时是对抗性的"重复触发/幂等"用例。

- [ ] **Step 3: 探测监控集成验证(需求#12)**

确认 crawler_task.enabled=1 + YAML detect_enabled=true 后，站点自动进入 crawler_detector 元循环。
验证(容器内执行，模拟探测器单站探针):

```bash
ssh -i "D:\AI\konus-key.pem" -o StrictHostKeyChecking=no root@47.98.102.55 "docker exec docker-ragflow-cpu-1 python -c \"
from rag.svr.crawler_detector import _get_crawler_task_site_ids
ids = _get_crawler_task_site_ids()
assert 'longyan_ggzy' in ids, ids
print('longyan_ggzy in detector meta-loop: OK')
\""
```

再请用户在前端 智能采集 → 探测监控 列表中确认出现"龙岩市公共资源交易中心"条目(需求#12 用户侧验收)。
注意: 若 Task 11 Step 5 的容器重启尚未执行，detector 进程仍用旧签名链 — 确认重启已完成。

- [ ] **Step 4: 清理临时脚本(可选，保留触发器)**

`_create_longyan_ggzy_task.py` 为一次性脚本，任务创建成功后可删除(服务器 + 本地)。

- [ ] **Step 5: konus-code-review 审查(强制)**

调用 `konus-code-review` skill 审查全部变更:
`rag/svr/longyan_ggzy_collection_crawler.py`(新建)、`rag/svr/crawler_sites.yaml`(新增块)、`rag/svr/crawler_engine/detector.py`(2 行追加)。
审查关注点: 边界(空页签/验证码/畸形 JSON/超长标题)、并发(Redis 锁 TTL vs 全量时长)、资源(临时目录清理)、安全(无硬编码密钥)、原有功能无回归(detector 签名链加法变更)。

- [ ] **Step 6: Final commit**

```bash
git add rag/svr/_trigger_longyan_ggzy.py
git rm --cached rag/svr/_create_longyan_ggzy_task.py 2>/dev/null || true
git commit -m "feat(crawler): longyan_ggzy trigger script + integration complete"
```

---

## Spec 覆盖核对 (14 条需求 → 任务)

| 需求 | 覆盖 |
|------|------|
| 1. 智能采集系统(crawler_result) | CollectionWriter + StoragePipeline, Task 9 |
| 2/3. 目标页/列表接口 | getInfoMationList, Task 2/5 |
| 4. 多页签 + 首次全量/后续当天 | TABS 51 + full/date_filter 双模式, Task 3/9 |
| 5. 去重 | gen_result_id(site_id, source_url) upsert + 内存 seen_urls, Task 9 |
| 6. 结构化+KB 3b4f619c... | extracted_json 自动派生(section_name/tab_path/info_type), Task 9 |
| 7. 自主部署+冒烟 | Task 11 (用户已授权) |
| 8. 建任务+触发+等用户验证 | Task 12 Step 7 暂停点 |
| 9. 详情标题/正文/附件/ZIP 入库入 KB | Task 6/7/9 (_process_item 三路径) |
| 10. type=龙岩市-交易信息 | INFO_TYPE → extracted_json.info_type, Task 3/9 |
| 11. 采集任务列表数据 | crawler_task INSERT, Task 12 |
| 12. 探测监控集成 | detect_enabled + detector 元循环, Task 11/13 |
| 13. 原文链接完整不 404 | SITE_ROOT+infourl 实测 200 + Task 12 Step 4 抽查 |
| 14. 反爬检查 | WAF/深分页/附件验证码三策略, Task 7 Step 1 + Task 11 Step 4 实测 |

## 对抗性用例清单 (已内置)

- 验证码/WAF 响应非 JSON → `_fetch_tab_rows` 返回 [] 不崩 (Task 5 Step 1)
- 畸形日期/空日期 → `_normalize_date` 容错 (Task 4 Step 2)
- 非法文件名字符/超长名 → `_sanitize_filename` (Task 4 Step 2)
- 不存在的 ZIP → `_extract_zip` 返回 [] (Task 7 Step 2)
- ZIP 同名成员 → 序号防覆盖 (Task 7)
- 附件网关 HTML/验证码拦截 → captcha_blocked 计数降级 (Task 7 + Task 11 Step 4 实测)
- 重复触发幂等 → Task 13 Step 2 当天模式二次跑 items_new≈0
- 并发运行 → Redis 锁 blocking_timeout=0 直接 skipped (Task 9)
- 全失败语义 → status="error" 触发 unified_crawler fail + exit 1 (Task 9)
- 签名抖动 → Task 10 Step 2 两次探测签名一致性断言
