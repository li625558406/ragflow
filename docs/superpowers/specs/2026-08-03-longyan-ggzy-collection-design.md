# 龙岩市公共资源交易中心 · 交易信息智能采集 — 设计文档

- **日期**: 2026-08-03
- **系统**: 智能采集系统（System C，`unified_crawler.py` + YAML → `crawler_result`）
- **目标页**: `https://ggzy.longyan.gov.cn/lyztb/`（Epoint WebBuilder 平台，siteGuid 驱动的 REST API + 静态详情页）
- **KB**: `3b4f619c85c211f198269135a1db216c`（与宁德/三明同一采集 KB）
- **方案**: A — custom_runner 模块（仿宁德 `ningde_gcjs` 同平台先例）

## 1. 需求与决策记录

用户 14 条需求 + 3 个澄清问题的落地决策：

| # | 需求 | 决策 |
|---|------|------|
| 1 | 满足智能采集系统设计.md（System C） | custom_runner 模式，写 `crawler_result`，非旧 bid_* 链路 |
| 2 | 目标页 `https://ggzy.longyan.gov.cn/lyztb/` | Epoint WebBuilder，列表走 `getInfoMationList` REST API |
| 3 | 列表接口同 URL | `POST /EpointWebBuilder/rest/GgSearchAction/getInfoMationList`（form-urlencoded，第 1 页免鉴权免验证码） |
| 4 | 多列表；首爬=页面列表当前所有数据，后续=仅当天 | **澄清结论：OCR + 每个页签列表的第一页即可**。首爬 = 53 页签 × 第 1 页（≤30 条/页）≈ 1100-1600 条；后续触发 date_filter=today 只存当天条目（当天条目多时最多翻 5 页，仍在验证码阈值内） |
| 5 | 去重 | `crawler_result.id = md5(site_id\|source_url)` 主键 upsert，天然幂等 |
| 6 | 结构化解析，每条有正文；页面结构可能不同；关键字段入扩展存储 | **澄清结论：存 `crawler_result.extracted_json`**（bid 类别标准做法，免建表免迁移）；正文统一取 `#mainContent`，结构差异用多选择器兜底 + getInfoByID API 降级；按页签类型提取关键字段（宁缺勿造） |
| 7 | 开发完自部署服务器 + 冒烟测试 | 用户显式授权（覆盖 CLAUDE.md 禁止自动部署默认值）；SCP 2 个文件 + 冒烟 |
| 8 | 验证后建采集任务 + 触发，等用户验收 | INSERT crawler_task（utf8mb4）→ 手动 trigger → 等用户验收数据完整性 |
| 9 | 详情页标题/正文/附件/ZIP（解压文件）全入库 + 上传 KB 解析 | 附件网关 `ztbAttachDownloadAction.action` + OCR 验证码破解（ddddocr）+ ZIP 解压 + 文本提取；文件本体 + 文本附录都进 KB |
| 10 | 采集结果 type 字段按列表主题填【龙岩市-交易信息】 | **澄清结论：按 6 大栏目区分** — section_name ∈ {龙岩市-工程建设, 龙岩市-政府采购, 龙岩市-企业采购, 龙岩市-产权交易, 龙岩市-土地矿业, 龙岩市-林权水权交易}；具体页签路径存 `extracted_json.tab_path` |
| 11 | 建采集任务列表任务数据 | crawler_task INSERT 一行，enabled=1，script_args={"site_id":"longyan"} |
| 12 | 接入探测监控列表 | YAML `detect_enabled: true` + `detect_interval: 3600`；YAML 保留 6 个大栏目 section 的 listing 配置供探测器 page-1 签名探测 |
| 13 | 详情弹窗原文地址完整，无 404/断链 | source_url = 详情页静态 HTML 的**绝对 URL**（infourl 相对路径前缀补全 `https://ggzy.longyan.gov.cn/lyztb`），浏览器可直开 |
| 14 | 检查目标站反爬机制 | 实测：WAF 限速（~30 次快速请求触发 403，~20s 自愈）；深层翻页（第 8 页起）一次性 4 位图形验证码；附件下载可能需验证码（开发时实测）；无签名无加密 |

**site_id 决策**：重写已禁用的旧 `longyan` 条目（旧配置 listing URL 为空、OAuth 流程已失效），保留 site_id=`longyan` 与旧 `bid_project.source_site='longyan'` 数据连续；不新建重复条目。
**调度决策**：无独立 cron。采集频率由探测监控驱动 —— 探测器每 detect_interval（3600s 基础值，无变更指数退避拉长）探测 6 个大栏目第 1 页签名，变更则入队采集（date_filter=today）。

## 2. 线上实测结论（2026-08-03）

### 列表 API

```
POST https://ggzy.longyan.gov.cn/EpointWebBuilder/rest/GgSearchAction/getInfoMationList
Content-Type: application/x-www-form-urlencoded

siteGuid=7eb5f7f1-9041-43ad-8e13-8fcb82ea831a   # 固定站点 GUID
categoryNum=007                                  # 栏目编码（支持父级聚合）
kw=                                              # 关键词（空=全部）
pageIndex=0                                      # 0-based
pageSize=30                                      # 服务端上限 30
YZM=                                             # 验证码（第 1 页为空）
ImgGuid=                                         # 验证码图片 GUID
```

响应：`{AllCount: N, custom: [{index, title, title2, infourl(相对), infodate, zhuanzai, strcomment}]}`
- pageIndex=0 免 cookie/免鉴权/免验证码 ✅
- 父级 categoryNum 可用（如 "007" 返回全部工程建设，AllCount 48153），但响应无逐项 categoryNum —— 可从 infourl 路径解析
- infourl 为相对路径 → 前缀 `https://ggzy.longyan.gov.cn/lyztb` 补全

### 栏目编码（老脚本参考 + 实测确认）

| 大栏目 | 子类 categoryNum | 页签 tab code |
|---|---|---|
| 工程建设 007 | 007004 房屋建筑工程 / 007005 市政基础设施工程 / 007006 交通工程 / 007007 水利工程 | 001 招标公告信息 / 002 答疑结果 / 003 中标候选人公示 / 004 中标结果公示 / 005 合同签订 |
| | 007008 信息化工程 / 007010 工业项目 | 仅 001 + 002 |
| | 007009 其他工程 | 全部 5 个页签 |
| 政府采购 008 | 008001 / 008003 / 008005（政府采购+其他采购） | 01 预公告 / 02 招标公告 / 03 中标 / 04 成交公告（具体编码开发时实测校准） |
| 企业采购 009 | 待实测确认 | 招标公告 / 预公告 / 变更公告 / 中标 / 成交公告 |
| 产权交易 010 | 待实测确认 | 企业和行政事业单位产权资产交易 / 无形资产交易 / 其他项目 / 成交结果公告 |
| 土地矿业 011 | 011006 土地（001-004）/ 011007001 矿业出让公告 / 011007002 矿业出让结果 | 出让公告 / 出让结果 |
| 林权水权交易 | 011010002 林权交易公告 / 林权成交公告 / 011011001 水权交易公告 | 交易公告 / 成交公告 |

页签总数约 53（开发时以站点实际渲染为准，矩阵常量集中一处，校准只改一处）。
用户需求中「中标/成交公告」分列，站点可能合并为「中标、成交结果」（老脚本 003），开发时实测确认。

### 详情页

- 静态 HTML：`/lyztb/<seg>/<catpath>/<YYYYMMDD>/<uuid>.html`
- 标题 `h3.bigtitle`；日期 `信息时间：`；正文 `#mainContent`；元信息 `sub-cp`（信息来源/阅读次数）
- 备用 API：`POST /EpointWebBuilder/rest/frontAppNotNeedLoginAction/getInfoByID`（params=JSON{siteGuid, infoID}）→ custom.infoContent

### 附件

- 详情页 HTML 内 `ztbfjyz('...downloadztbattach?attachGuid=<uuid>...')` onclick 正则提取
- 直接下载 URL：`{ROOT}/EpointWebBuilder/webbuildermis/attach/ztbAttachDownloadAction.action?cmd=getContent&attachGuid={guid}&appUrlFlag=ztb001&siteGuid={siteGuid}`
- 老脚本警告：龙岩附件下载需图形验证码（宁德同平台 appUrlFlag=TP001 实测免验证码直连）—— **开发第一步实测**：免码直连则 OCR 分支休眠，有码则 ddddocr 破解

### 反爬

| 机制 | 实测 | 对策 |
|---|---|---|
| WAF 限速 | ~30 次快速请求 → 403，~20s 自愈 | 串行 + 1-2s 随机延迟；403 → 指数退避 20-60s；连续 3 次 403 → 本轮安全终止（已采数据不丢） |
| 深层翻页验证码 | 第 8 页起每页一次性 4 位图形验证码 | 首爬只爬第 1 页；增量最多翻 5 页 —— 永不触碰验证码区 |
| 附件验证码 | 待实测 | ddddocr OCR；不可用/失败降级为仅存 file_url 引用 |
| 签名/加密 | 无 | — |
| robots.txt | 404（无声明） | — |

## 3. 架构

```
crawler_task 表新增 1 行 (site_id=longyan, kb_id=3b4f619c..., enabled=1)
 ├─ 采集执行: unified_crawler.py → YAML longyan.custom_runner
 │    → rag/svr/longyan_ggzy_collection_crawler.py::run()
 │         列表层: 53 页签 × getInfoMationList pageIndex=0 (免验证码)
 │         详情层: GET 静态HTML → h3.bigtitle/#mainContent (多选择器兜底 + getInfoByID 降级)
 │         附件层: 网关下载 → [验证码? ddddocr OCR] → ZIP解压 → pdfplumber/docx/openpyxl 文本提取
 │         存储层: CollectionWriter.write_all() + StoragePipeline → crawler_result + KB
 │    首跑=date_filter空(页签第1页全收) / 后续=date_filter=today
 └─ 探测监控: crawler_detector.py 读 YAML 6 个大栏目 section 各探 1 次第 1 页 API 算签名
      签名变更 → 入队采集(date_filter=today)；detect_interval=3600s + 指数退避
旧 longyan YAML 条目: 重写启用（原 disabled，listing URL 为空）
```

**文件变更清单（2 文件 + 1 条 DB 记录，不动 engine.py / unified_crawler.py / collection_writer.py）**：

| 文件 | 操作 |
|---|---|
| `rag/svr/crawler_sites.yaml` | 重写 `longyan` 条目（enabled: true，custom_runner 模式） |
| `rag/svr/longyan_ggzy_collection_crawler.py` | 新建 custom_runner 模块（~1000 行，仿宁德模板） |
| crawler_task 表 | INSERT 一行（--default-character-set=utf8mb4） |

## 4. YAML 配置（重写 longyan）

```yaml
longyan:
  name: "龙岩市公共资源交易中心"
  site_url: "https://ggzy.longyan.gov.cn/lyztb/"
  category: bid                    # 交易信息 → bid 类别（CATEGORY_LABELS 已支持）
  detect_enabled: true             # 纳入探测监控（需求 #12）
  detect_interval: 3600
  custom_runner: "rag.svr.longyan_ggzy_collection_crawler"
  transport:
    type: rest_api
    timeout: 30
    verify_ssl: false
    headers:
      User-Agent: "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
      Referer: "https://ggzy.longyan.gov.cn/lyztb/"
  listing:                          # ↓ 仅供探测器 page-1 签名探测；实际采集在 custom_runner
    url: "https://ggzy.longyan.gov.cn/EpointWebBuilder/rest/GgSearchAction/getInfoMationList"
    method: POST
    body_type: form
    body:
      siteGuid: "7eb5f7f1-9041-43ad-8e13-8fcb82ea831a"
      kw: ""
      pageIndex: "0"
      pageSize: "30"
      YZM: ""
      ImgGuid: ""
  sections:                         # 6 个大栏目父级 categoryNum；探测仅 6 请求，WAF 安全
    gcjs:
      name: "工程建设"
      listing:
        body: { categoryNum: "007" }
    zqcg:
      name: "政府采购"
      listing:
        body: { categoryNum: "008" }
    qycg:
      name: "企业采购"
      listing:
        body: { categoryNum: "009" }   # 开发时实测校准
    cqjy:
      name: "产权交易"
      listing:
        body: { categoryNum: "010" }   # 开发时实测校准
    tdky:
      name: "土地矿业"
      listing:
        body: { categoryNum: "011" }
    lqsq:
      name: "林权水权交易"
      listing:
        body: { categoryNum: "011010" }  # 开发时实测校准（林权/水权父级）
  # 注: 011010/011011(林权水权) 可能同属 011 父级 —— 若实测不存在能拆分
  # 土地矿业与林权水权的独立父级码, 则删除 lqsq section, tdky 探 "011"
  # 即覆盖两者（站点级签名是所有 section 合并计算的, 少一个 section 不影响变更检测）
  pagination:
    type: single_page              # 探测器只探第 1 页
  extract:
    type: json_path
    items_path: "custom"
    fields:
      title: "title"
      url: "infourl"
      date: "infodate"
  detail:
    type: none                     # custom_runner 自己处理详情
```

**section body 合并验证点**：section 只覆盖 `body` 字段时必须与父级 body 深合并（踩坑：section params 合并问题 —— `engine.py` 已改用 `dataclasses.replace`，detector 走同一 merge 路径；实现后需在探测器实测中确认 siteGuid 等父级字段不丢失）。

## 5. custom_runner 模块设计

`rag/svr/longyan_ggzy_collection_crawler.py`：

```python
SITE_ID = "longyan"
SITE_GUID = "7eb5f7f1-9041-43ad-8e13-8fcb82ea831a"
SITE_ROOT = "https://ggzy.longyan.gov.cn"
LIST_BASE = f"{SITE_ROOT}/lyztb"
APP_URL_FLAG = "ztb001"
KB_ID_DEFAULT = "3b4f619c85c211f198269135a1db216c"
CATEGORY = "bid"

# 页签矩阵：大栏目(section_name 后缀) → [(categoryNum, 子类名, [tab_code...])]
TABS = { "工程建设": [...], "政府采购": [...], "企业采购": [...],
         "产权交易": [...], "土地矿业": [...], "林权水权交易": [...] }

def run(tenant_id="", kb_id="", task_name="", task_id="",
        writer_mode="collection", category=CATEGORY, date_filter="",
        full_crawl=False, force_run=False, site_config=None, output_dir="") -> dict:
    """返回扁平 summary: {status, pages, items_found, items_new,
    kb_uploaded, attachments_uploaded, errors}（custom_runner 约定 shape）"""
```

**运行语义**：
- `date_filter` 空（首跑/手动全量）：53 页签 × 第 1 页全收
- `date_filter=today`（探测器/定时触发）：逐页签爬第 1 页，仅存当天条目；若页末日期仍 ≥ 今天则继续翻页（上限 5 页）
- Redis 锁 `crawler_engine:{tenant}:longyan` 防并发（force_run 绕过）

**主循环**：
```
for 大栏目, 子类列表 in TABS:
    section_name = f"龙岩市-{大栏目}"
    for categoryNum, 子类名, tabs in 子类列表:
        for tab_code in tabs:
            items = fetch_list_page(categoryNum, tab_code, page=0)   # API
            for item in items:
                # 不做 early-break：列表日期降序只是实测趋势, 不保证严格有序;
                # 第 1 页 ≤30 条, 全页扫描+过滤的成本可忽略, 换取乱序容错
                if date_filter and item.date != today: continue
                if dedup 命中(md5(site_id|url) 已存在): continue
                detail = fetch_detail(abs_url)                        # 静态HTML + 兜底
                attachments = extract_attach_guids(detail.html)
                files = download_attachments(attachments)             # OCR 分支
                content_md = build_markdown(detail, files)
                extracted = extract_structured(tab_code, detail)      # 关键词+正则
                writer.write_all(item_dict)                           # crawler_result upsert
                pipeline.store(normalized)                            # KB 上传
```

**详情页解析**（BS4）：
1. 标题：`h3.bigtitle` → 兜底 `<title>` 清洗
2. 日期：`信息时间：` 文本 → 兜底 infourl 路径中 YYYYMMDD → 兜底列表 infodate
3. 正文：`#mainContent` → 兜底 `div.sub-cp` 前的主容器 → 兜底 getInfoByID API 的 infoContent
4. 元信息：信息来源/阅读次数（sub-cp）入 extracted_json

**结构化提取 extracted_json**（需求 #6）：
```json
{
  "tab_path": "工程建设/房屋建筑工程/招标公告信息",
  "category_num": "007004", "tab_code": "001",
  "purchaser": "...", "agency": "...", "budget": "...",
  "winner": "...", "win_amount": "...", "duration": "...",
  "info_source": "...", "...": "按页签类型提取，未命中不填（宁缺勿造）"
}
```
提取规则：关键词 + 正则（预算金额/中标人/采购人/代理机构/工期等），命中才写键；不用 LLM（成本+延迟，53 页签 × 每日增量规模下不划算）。

## 6. 附件链路（OCR）

```
详情页 HTML → 正则 ztbfjyz\('...attachGuid=([0-9a-f-]{36})'\) + downloadztbattach 直链 + 常见文件后缀链接
   ▼
GET ztbAttachDownloadAction.action?cmd=getContent&attachGuid={guid}&appUrlFlag=ztb001&siteGuid={GUID}
   ▼
响应判定:
  二进制魔数(%PDF/PK/OFD/.doc…) → 直接保存 ✓
  HTML 且含验证码表单 → ddddocr OCR 识别 → 带 YZM/ImgGuid 重试（≤3 次）
  ddddocr 不可用 / 3 次失败 → 降级：DB 存 file_url 引用，KB 跳过该文件，errors 记录
   ▼
ZIP → zipfile 解压成员（路径穿越过滤）→ 逐文件文本提取（pdfplumber/docx/openpyxl/txt）
   ▼
附件文本 → content_markdown 附录（随正文上传 KB）；附件文件本体 → KBUploader 上传解析
临时目录 finally shutil.rmtree 清理
```

**开发第 0 步**：容器内实测附件网关 —— 若免验证码直连（如宁德），OCR 分支自动休眠（代码保留不删）。

## 7. 存储语义

| 字段 | 值 |
|---|---|
| `crawler_result.id` | `md5(site_id\|source_url)` upsert —— 幂等去重（需求 #5） |
| `source_url` | 详情页绝对 URL（infourl 前缀补全），浏览器直开不 404（需求 #13） |
| `section_name` | 龙岩市-{工程建设\|政府采购\|企业采购\|产权交易\|土地矿业\|林权水权交易}（需求 #10） |
| `category` | `bid`（CATEGORY_LABELS 已支持 bid 英文码） |
| `extracted_json` | tab_path + 结构化关键字段（需求 #6） |
| KB | `3b4f619c85c211f198269135a1db216c`；content_markdown 模板（标题/日期/来源/栏目路径/正文/附件文本附录）+ 附件文件本体（需求 #9） |

## 8. 任务创建 + 触发 + 探测器集成（需求 #11/#12）

1. `crawler_task` INSERT（--default-character-set=utf8mb4）：site_id=longyan、tenant_id、kb_id、enabled=1、script_args=`{"site_id":"longyan"}`
2. 首爬：手动 trigger → last_run_status 空 → 全量（无 date_filter）
3. 探测监控：crawler_detector 每 60s 元任务扫 `crawler_task WHERE enabled=1` + YAML detect_enabled → longyan 纳入；6 个 section 各探 1 次第 1 页 API 签名；变更入队；自适应退避
4. 前端：采集任务列表 + 探测监控列表自动显示（现有 UI，零前端改动）

**分发链路（已核实 crawl4ai_app.py:232-281）**：
- YAML `category=bid` → trigger_task 传 `--writer bid` + script_args `{writer:"bid", category:"bid", task_id}`
- unified_crawler custom_runner 分发 → `mod.run(writer_mode="bid", category="bid", ...)`
- **run() 内部无视传入的 writer_mode，硬编码 `writer_mode="collection"` 走 CollectionWriter → crawler_result**（宁德先例同款：ningde_gcjs_collection_crawler.py:906）
- date_filter 注入：首跑（last_run_status 空）不注入 → 全量；后续注入 `date_filter="today"` → 增量

## 9. 部署与验证（用户已授权）

1. 容器内实测附件网关验证码行为（决定 OCR 分支激活）
2. SCP 2 个文件到 `/home/bid-agent-konus/ragflow2/rag/svr/`（bind mount，无需重启容器）
3. 冒烟：`docker exec docker-ragflow-cpu-1 python -c "import rag.svr.longyan_ggzy_collection_crawler"` + validate_all_crawlers.py
4. 单站试跑：`unified_crawler.py --writer collection --script-args '{"site_id":"longyan"}'`，验证 ① N new items ② `crawler_result WHERE site_id='longyan'` 行数对账 ③ KB 上传数 ≥ DB 写入 × 80%
5. INSERT crawler_task → 前端手动触发 → 等用户验收数据完整性
6. 确认探测监控列表出现龙岩站点

## 10. 对抗性测试用例（CLAUDE.md 强制）

| 用例 | 预期防御 |
|---|---|
| 重复触发同一任务 | upsert 幂等，第二次 items_new=0 |
| 403 WAF 中途拦截 | 指数退避 20-60s 恢复；连续 3 次 → 安全终止，已采数据不丢 |
| 附件 OCR 连续失败 | 降级仅存 file_url，errors 记录，不阻断正文入库 |
| 空页签（AllCount=0） | 跳过，不计错误 |
| 详情页 404/畸形 HTML | getInfoByID API 降级；仍失败 → 仅存列表元数据 + errors |
| 正文含控制字符/超长 HTML | BS4 容错；content 长度不设硬截断（KB 解析器自处理） |
| ZIP 成员路径穿越（`../evil`） | 路径规整过滤，拒绝解压到临时目录外 |
| 附件文件名含特殊字符 | 文件名清洗（与宁德模板一致） |
| 列表 API 返回畸形 JSON/缺 custom 键 | 跳过该页签 + errors，不抛崩全局 |
| 时钟回退/date_filter 边界（23:59 发布条目） | date 比较用 YYYY-MM-DD 字符串相等，不用时间戳 |
| Redis 锁残留（上次崩溃） | force_run 清锁；锁带 TTL |

## 11. 关键依赖确认

- rest_api 适配器支持 `body_type: form`（form-urlencoded POST）✅ 已验证（rest_api.py:140）
- detector 对 custom_runner 站点：用 YAML listing/sections 做 page-1 探测 ✅（detector.py 不感知 custom_runner）
- `unified_crawler.py` custom_runner 分发签名匹配（tenant_id/kb_id/task_name/task_id/writer_mode/category/date_filter/full_crawl/force_run/site_config/output_dir）✅（unified_crawler.py:313-325）
- ddddocr 可用性：容器内 `pip show ddddocr`，缺失则 pip install（服务器容器内操作）
