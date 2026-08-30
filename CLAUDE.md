# CLAUDE.md

可用账号：lg18629285296@163.com
密码：12345678


核心注意点：
1、新增和修改的代码，禁止私自部署服务器
2、前端文案只用中文，不做 i18n 多语言翻译（新增 key 只在 zh.ts 加，不同步 en.ts）
3、名词约定：我说的「用户管理」一律指**顶部导航「智能采集」下一个的「用户管理」** = 路由 `/permission`（Web 权限管理页，`web/src/pages/permission/index.tsx`）。它**不是** user-setting 设置左导航里的旧「用户管理」（`/user-setting/user-management` → 旧 admin/users），也**不是** `/admin/users`。涉及该名词时按此定位，勿删改错。

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

【核心注意：新增的数据库要放到ragflow项目的初始化脚本中，要考虑到迁移部署，不会导致问题】



修改代码和新增代码，需要注意一下规范规则：

1. 原有功能是否被意外移除、更改返回值、修改核心流程。
  2. 新增/修改的代码是否正确实现了目标功能。
  3. 边界条件：空值、零值、极限输入、并发访问、资源耗尽等情况是否安全。
  4. 多场景适配：是否考虑了不同角色、不同配置、不同环境、多语言等场景下的行为。
  5. 异常处理与日志：错误路径是否有兜底，异常是否被正确处理和记录。
  6. 安全性：SQL注入、XSS、权限绕过、敏感信息暴露等。
  7. 性能隐患：循环内 IO、无索引查询、内存泄漏、未释放连接等。
  8. 代码质量：可读性、重复代码、魔法值、命名规范。



### 参考文档（需要时读取原文）

| 文档 | 路径 | 包含内容 |
|------|------|----------|
| 部署服务器 | `D:\AI\ragflow2\本地部署服务器.md` | 服务器SSH连接、Docker部署、前端/后端/Flutter热更新、Nginx配置、常见问题排查 |
| 第三方接口 | `D:\AI\ragflow2\接口文档_2026-06-04.md` | 标讯API、企业画像API、合同API等第三方接口的请求/响应字段定义和鉴权方式 |
| 项目架构 | `D:\AI\ragflow2\项目架构.md` | 系统整体架构、模块间调用关系、数据流、技术选型决策背景 |
| 二次开发功能汇总 | `D:\AI\ragflow2\二次开发功能汇总.md` | 全部二次开发功能清单：标讯系统、企业查询、Agent画布、爬虫引擎、MCP Server等 |
| 协作功能二开方案 | `D:\AI\ragflow2\协作功能二开方案.md` | 协作页签25个功能的现状分析、差距评估、适配方案、数据库/前端组件清单、分4个Phase的优先级规划 |
| Crawl4AI 独立爬虫服务方案 | `D:\AI\ragflow2\crawl4ai-service-独立部署方案.md` | ★ 下一代爬虫架构：独立部署 crawl4ai Docker + FastAPI调度服务 + RAGFlow KB/DB适配器，替代现有定时任务爬虫体系 |
| 智能采集系统设计 | `D:\AI\ragflow2\智能采集系统设计.md` | ★ 新智能采集系统：基于 crawler_result + 扩展表的多类别采集（bid/policy/personnel/news/other），与 bid_* 解耦，手动触发+定时调度，YAML category 字段驱动 |
| 权限管控 RBAC | `D:\AI\ragflow2\docs\superpowers\specs\2026-08-25-permission-rbac-design.md` | 角色+权限点+用户角色三表、@permission_required、前端菜单过滤/路由守卫、B端权限管理页、存量用户默认普通用户 |
| 反爬能力等级 | `D:\AI\ragflow2\反爬能力等级.md` | 爬虫三级反爬能力清单：🟢 一级(rest_api基础) / 🟡 二级(加密/浏览器指纹) / 🔴 三级(SPA/Stealth)，含 YAML 配置模板、选型决策树、排查流程 |
| 踩坑问题清单 | `D:\AI\ragflow2\踩坑问题清单.md` | 30 个实战踩坑：Docker/Nginx 部署、SPA 爬虫调试、ORM 迁移、成套 SCP 清单、队列重复任务去重（#30）等 |
| A2 detector 旁路方案 | `D:\AI\ragflow2\A2-detector-inprocess-旁路方案.md` | ★ Detector 改为 scheduled_task_executor 进程内执行，绕过 task_executor 主队列。诊断数据、4 文件改动清单、验证流程、回滚、后续优化方向 |
| crawl-dedup 爬虫排队去重方案 | `D:\AI\ragflow2\crawl-dedup-爬虫排队去重方案.md` | ★ crawl:queued:{site} 标记：入队前 SET NX、task_executor 跑完 DEL，每站最多一条排队/运行中爬虫。含 TTL 自愈被否决的教训、队列清理脚本、验证与回滚 |
| 流程页签设计 | `D:\AI\ragflow2\docs\superpowers\specs\2026-08-30-flow-workflow-design.md` | ★ C端新增「流程」页签：文件为主视图的多角色串行工作流（发起人→领导→处理人→汇总→归档），4张 flow_* 表、文件版本时间线、复用对话智能体，设计已确认待实施 |



## Project Overview

基于 [RAGFlow](https://github.com/infiniflow/ragflow) v0.25.1 深度二次开发，聚焦**标讯（招投标）数据采集、存储、检索、知识库解析**和**投标书自动写作**。

- **GitHub**: `li625558406/ragflow` | **上游**: `infiniflow/ragflow` v0.25.1
- **本地路径**: `D:\AI\ragflow2` | **服务器**: `root@47.98.102.55` (SSH密钥: `D:\AI\konus-key.pem`)
- **详细架构**: `F:\投标项目\AI\项目架构.md` | **部署文档**: `F:\投标项目\AI\本地部署服务器.md`
- **MCP/Hermes对接**: `F:\投标项目\AI\对接API-tools.md` | **DSL画布**: `F:\投标项目\AI\分析招标文件内容 (6).json`

### 技术栈

| 层 | 技术 |
|---|---|
| 后端框架 | Python Quart (async Flask-compatible) |
| ORM | Peewee + MySQL 8.0 |
| 搜索 | Elasticsearch 8.11 (向量+全文) | MinIO (文件) | Redis Valkey 8 (缓存/队列) |
| LLM | 自研 LLMBundle 抽象层，支持 30+ 模型 (OpenAI/Qwen/DeepSeek) |
| 前端 | React 18 + Vite 7 + TypeScript 5, Radix UI + Tailwind CSS, Zustand + TanStack Query |
| Agent | JSON DSL 画布 DAG + 动态组件发现 + asyncio 并发 |
| 移动端 | Flutter 3.44 + Dart 3.12 + Riverpod + Dio + go_router |
| 部署 | Docker Compose, Bind Mount 热更新 |

---

## 目录结构与职责

```
ragflow2/
├── api/                    # Python 后端 (Quart)
│   ├── apps/restful_apis/  # 25+ REST API Blueprint (含 ★ bid_app.py)
│   ├── db/
│   │   ├── db_models.py    # Peewee ORM 模型 (~1600行, 含7张标讯表)
│   │   └── services/       # 31个 Service 文件 (含 ★ bid_service.py)
│   └── utils/              # ★ bid_tool_service.py (缓存服务层) + bid_api_client.py
│
├── agent/                  # Agent 画布编排引擎
│   ├── canvas.py           # Canvas 核心 (~800行)
│   ├── component/          # 21个画布节点 (含 ★ FanOut, Loop, Agent, LLM)
│   ├── tools/              # 30个工具插件 (含 ★ bid.py — 12个标讯工具)
│   └── templates/          # 预置 Agent 模板 (JSON DSL)
│
├── rag/                    # RAG 核心引擎
│   ├── llm/                # Chat/Embedding/Rerank/CV/OCR 模型抽象
│   ├── svr/
│   │   ├── mcp_server.py              # ★ MCP JSON-RPC Server (11个Tools)
│   │   ├── unified_crawler.py         # ★ 统一爬虫入口
│   │   ├── crawler_sites.yaml         # ★ 78个站点 YAML 配置
│   │   ├── crawler_engine/            # ★ 爬虫引擎核心包 (v2.1 三层架构)
│   │   ├── wechat_mp_crawler.py       # 微信文章采集
│   │   ├── wechat_mp/                 # 微信公众号采集模块
│   │   └── bid_sync.py                # 标讯定时同步
│   ├── flow/               # 文档处理: Parser → Chunker → Embedding
│   └── graphrag/           # 知识图谱构建与查询
│
├── web/                    # React 前端
│   └── src/
│       ├── pages/
│       │   ├── home/       # ★ B端标讯管理
│       │   ├── c-chat/     # ★ C端对话主页 (投标助手)
│       │   ├── agent/      # B端 Agent 画布编辑器
│       │   └── ...
│       ├── components/bid/ # ★ 标讯组件 (contract-list.tsx等)
│       └── services/       # ★ bid-service.ts (标讯API调用层)
│
├── bidding_app/            # ★ Flutter 移动端 (标书分析助手 App)
└── docker/                 # Docker Compose + Nginx 配置
```

---

## 投标系统 (Bid System) — 核心二次开发

### 数据库表 (7张)

| 表 | 主键 | 用途 | TTL |
|---|---|---|---|
| `bid_project` | `id` (BigInt) | 标讯/中标/合同搜索缓存 | 1h |
| `bid_project_detail` | `id`=project_id | 项目详情 + content_html | 30天 |
| `bid_project_structure` | `id`=project_id | 结构化数据 (JSON) | 30天 |
| `bid_project_file` | auto id | 附件元数据 | 永久(覆盖) |
| `bid_project_parse` | `project_id` | 知识库解析状态 | 永久 |
| `bid_enterprise_cache` | (company_name, cache_type) | 企业画像/联系人/客户/供应商 (旧, Agent工具用) | 7天/3天/1天 |
| `bid_enterprise_business` | `keyword` | ★ 企业工商信息全量缓存 (新接口, 阿里云API市场) | 7天 |
| `bid_tender_search` | `id`=sha256(projectNumber\|title) | ★ 标讯搜索 v4 缓存 (新接口, 10次API额度) | 24h |
| `bid_enterprise_parse` | `company_name` | 企业知识库解析状态 | 永久 |
| `bid_sync_log` | `id` | API同步日志 | 永久 |

### 缓存优先策略

第三方 API 按次收费，所有端点必须走 `DB 缓存 → API 降级`：

```
搜索请求 → DB 查询 (filter_valid_cache 过滤未过期)
  ├── 命中且足够 → 直接返回 (免API费)
  └── 未命中/不足 → 调第三方API
       ├── 成功 → upsert (id为主键) → 设TTL → 返回DB数据
       └── 失败 → stale fallback (返回DB过期数据)
```

**关键规则**：
- 以 `id` 为主键 upsert，API为权威源覆盖DB
- API返回camelCase (`fileUrl`)，DB存储snake_case (`file_url`)
- 返回前端**必须用DB数据**（snake_case字段名），**不能直接返raw API** — 否则前端字段匹配失败

### 20+ REST API 端点 (`bid_app.py`)

```
GET    /bid/projects                          # 列表 (缓存优先)
GET    /bid/projects/{id}/detail              # 详情v1
GET    /bid/projects/{id}/detail-v2           # ★ 详情v2 (合同正文+结构化, 30天缓存)
GET    /bid/projects/{id}/structure           # 结构化数据
GET    /bid/projects/{id}/files               # 附件列表
GET    /bid/projects/{id}/collect-url         # 原始采集源网址
POST   /bid/projects/{id}/parse               # 触发解析→KB
GET    /bid/projects/{id}/parse-status        # 解析进度
GET    /bid/projects/by-number                # 项目编号查询
GET    /bid/stats                             # 统计
GET    /bid/sync-logs                         # 同步日志
POST   /bid/trigger-sync                      # 手动触发同步
GET    /bid/areas                             # 省市联动
GET    /bid/industries                        # 行业分类
GET    /bid/contracts                         # ★ 合同/中标搜索 (1h缓存)
GET    /bid/enterprises/business              # ★ 企业工商信息全量查询 (新, 阿里云API市场, 7天缓存)
POST   /bid/tender-search                     # ★ 标讯搜索 v4 (新, 10次API额度, 24h缓存, 缓存优先)
GET    /bid/construction/projects              # ★ 拟在建项目搜索
GET    /bid/construction/projects/{id}/detail  # ★ 拟在建项目详情
```

### API 字段名约定 (CRITICAL)

```
第三方API (camelCase)  →  DB (snake_case)  →  前端 (两种都兼容)
─────────────────────────────────────────────────────────────
projectFileID          →  project_file_id   →  f.projectFileID ?? f.project_file_id
fileUrl / url          →  file_url          →  f.fileUrl ?? f.file_url ?? f.url
name                   →  file_name         →  f.name ?? f.file_name
partAInfo              →  part_a_names      →  (数组→JSON string)
```

缓存 upsert 时必须做 camelCase→snake_case 映射；返回前端时必须从 DB 查（snake_case），不能直接透传 API 响应。

---

## Agent 画布系统

### 核心组件 (21个)

| 类型 | 组件 | 用途 |
|---|---|---|
| 入口 | `Begin` | 接收用户输入，注入 sys.query |
| 核心 | `Agent` | LLM + Function Calling 工具调用，迭代推理 |
| 核心 | `LLM`/`Generate` | 直接 LLM 调用 |
| 并行 | ★ `FanOut` | asyncio.gather 多lane并发，绕过canvas直接调LLM |
| 串行 | `Iteration` | 通过canvas path循环迭代 |
| 条件 | `Switch`, `Categorize` | 条件分支/LLM分类路由 |
| 循环 | ★ `Loop` | while条件循环，子节点输出透传 (覆盖写) |
| 输出 | `Message` | 最终输出格式化 (Markdown) |
| 数据 | `VariableAssigner`, `VariableAggregator`, `DataOperations` | 变量/数据处理 |

### Agent 工具 (30个，标讯12个)

**标讯工具** (`agent/tools/bid.py`):
`BidLookupCode`, `BidSearch`, `BidSearchAI`, `BidGetDetail`, `BidGetSource`, `BidImportToKb`, `BidCheckImportStatus`, `BidSearchContract`, `BidRewriteQuery`, `BidIndustryTag`, `BidEnterpriseProfile`, `BidConstructionSearch`

**通用工具**: `Retrieval`(KB检索), `CodeExec`, `Tavily`, `DuckDuckGo`, `Wikipedia`, `PubMed`, `GitHub`, `ArXiv`, `Email`, `Crawler`, `ExeSQL`, `AKShare`, `TuShare`, `QWeather`, `Deepl` 等

### MCP Server (11个Tools, JSON-RPC over stdio)

`rag/svr/mcp_server.py` — 零外部依赖，与 Hermes 桌面应用集成。Tools: `ask_agent`, `lookup_bid_code`, `search_bid_projects`, `get_bid_detail`, `import_bid_to_kb`, `check_bid_import_status`, `search_contracts`, `get_bid_detail_v2`, `enterprise_contacts`, `enterprise_customers`, `enterprise_suppliers`

### DSL 画布 (`F:\投标项目\AI\分析招标文件内容 (6).json`)

25个节点的投标分析流水线：6 Agent + 6 Tool + 2 Categorize + 2 CodeExec + 1 FanOut + 1 Loop。使用 DeepSeek V4 Flash 模型，系统提示词定义了12个工具的使用策略和调用规范。

---

## 爬虫引擎 (Crawler v2.1)

三层架构：**Crawl → Dedup → Storage**，YAML 配置驱动，78个站点。

```
Crawl Layer: Adapter → Paginator → Extractor → AntiCrawler → List[Dict]
Dedup Layer: DedupChecker (内存+DB双层去重, O(1))
Storage Layer: bid_writer (标讯入库) + kb_uploader (KB上传) + attachment_handler (附件下载解析)
```

三层通过 `NormalizedItem` 数据结构传递，每层可独立测试。支持4种HTTP适配器 (REST API, SM4/AES加密, SPA渲染, Playwright HTTP) + 6种分页策略 + 3种数据提取器 (JSONPath, CSS, AI)。

### SPA 爬虫开发调试流程（必读）

接入 Vue/React SPA 站点（如政府标讯平台）时，按此顺序排查，避免反复试错。详细踩坑案例见 `D:\AI\ragflow2\踩坑问题清单.md` #23-#28。

**Step 0 — 先看老脚本**：搜索 `rag/svr/*_crawler.py` 同域脚本，复用其选择器/加密/字段映射，不要重新逆向。

**Step 1 — 独立 debug 脚本验证页面是否真的渲染**（在容器内执行）：
```python
# rag/svr/_debug_xxx.py
from playwright.sync_api import sync_playwright
with sync_playwright() as pw:
    browser = pw.chromium.launch(headless=True, executable_path="/opt/chrome/chrome",
                                  args=["--disable-blink-features=AutomationControlled", "--no-sandbox"])
    page = browser.new_context(user_agent="Mozilla/5.0 ... Chrome/125.0.0.0 Safari/537.36",
                                locale="zh-CN").new_page()
    try:
        page.goto(URL, wait_until="load", timeout=30000)
    except Exception as e:
        print(f"goto failed: {e}")  # ← 超时不代表页面没渲染
    page.wait_for_timeout(5000)
    print("HTML len:", len(page.content()))
    for sel in ["a.list-item", ".case-list a", "a[href*='detail']"]:
        print(f"  {sel}: {len(page.query_selector_all(sel))} hits")
```
若 HTML 长度 >30KB 且选择器命中 → DOM 已就绪，问题在 Playwright 等待策略或 extractor，而不是站点本身。

**Step 2 — 三选一排查 Playwright 等待策略**：
- `wait_until="load"` 超时 → 站点有 analytics/长连接（hm.baidu.com）阻止 load 事件
- `wait_until="networkidle"` 超时 → 同上，网络永不空闲
- ✅ 改用 `wait_until="domcontentloaded"` + YAML 配置 `transport.network_idle: false` + spa_render.py goto try/except 容错

**Step 3 — User-Agent 检查**：
- Chromium headless 默认 UA 含 "HeadlessChrome" → 被反爬识别
- `BrowserPool.new_context()` 必须显式设真实 UA（已硬编码）
- YAML `transport.headers.User-Agent` 只作用 HTTP 请求头，**不影响** `navigator.userAgent`

**Step 4 — 数据提取策略选择**：

| 场景 | 用什么 |
|------|--------|
| API 返回 JSON | `extract.type: json_path` + `fields` 映射 |
| 列表项是 `<tr>` / `<li>` 等容器，字段在后代元素 | `extract.type: css_selector` + BS4 语法 (`"a@href"` / `"@data-id"` / `".title-col"`) |
| **列表项就是 `<a>` 本身，需取自身 text + 子元素 date** | ✅ `extract.js_extract` (JS evaluate，本项目专属) |
| 详情正文用 Markdown | `detail.type: css_selector` + `content_field` |

**❌ 不要用** Scrapy 语法 `::text` / `::attr(href)` — 本项目 BS4 extractor 不支持（见踩坑 #25）。

`js_extract` 模板（参考 `rag/svr/ggzyjd_crawler.py::_extract_list_items`）：
```yaml
extract:
  type: css_selector
  items_path: "a.list-item"     # 仅用于 wait_for_selector 提示
  js_extract: |
    () => {
      const results = [];
      for (const a of document.querySelectorAll('a.list-item')) {
        const text = (a.textContent || '').trim();
        const em = a.querySelector('em, .time');
        results.push({
          title: text.replace(/\d{4}[-/]\d{1,2}[-/]\d{1,2}/g, '').trim(),
          url: a.href,
          date: em ? (em.textContent || '').trim() : '',
          id: new URL(a.href).searchParams.get('MGUID') || '',
        });
      }
      return results;
    }
```
spa_render 优先级：API captures → `js_extract` → `_extract_from_dom`。

**Step 5 — 容器内单站测试 + 入库验证**（详见下方"部署流程"）：
```bash
docker exec docker-ragflow-cpu-1 python /ragflow/rag/svr/unified_crawler.py \
  --tenant-id <TID> --kb-id <KID> --task-name test_xxx \
  --writer collection --category news \
  --date-filter 2026-07-16 \
  --script-args '{"site_id":"xxx"}'
```
验证三件事：① `[CRAWLER] Done: N new items` ② `SELECT COUNT(*) FROM crawler_result WHERE site_id='xxx'` 行数对得上 ③ KB 上传数 ≥ DB 写入数 × 80%。

**Step 6 — 开发完成后必须调用 `konus-code-review` 审查。**

### 智能采集系统部署清单（成套 SCP，不能单文件）

智能采集系统横跨 8+ 文件，部署时**必须成套 SCP**，否则会连环报错（见踩坑 #28）。改动任一文件，以下相关文件一起部署：

| 类型 | 路径 |
|------|------|
| ORM 模型 | `api/db/db_models.py`（末尾 CollectionPolicyExt / CollectionPersonnelExt + migrate_db） |
| 主表 Service | `api/db/services/crawler_service.py` |
| 扩展表 Service | `api/db/services/collection_ext_service.py` |
| REST API | `api/apps/restful_apis/collection_app.py` |
| Writer | `rag/svr/crawler_engine/collection_writer.py` |
| Storage 管道 | `rag/svr/crawler_engine/storage_pipeline.py` |
| Engine | `rag/svr/crawler_engine/engine.py` |
| Config | `rag/svr/crawler_engine/config.py` |
| Adapter | `rag/svr/crawler_engine/adapters/spa_render.py` |
| BrowserPool | `rag/svr/crawler_engine/browser_pool.py` |
| CLI 入口 | `rag/svr/unified_crawler.py` |
| YAML 配置 | `rag/svr/crawler_sites.yaml` |

**部署后冒烟测试**（所有改动文件必须能 import）：
```bash
docker exec docker-ragflow-cpu-1 python -c '
from api.db.db_models import CollectionPolicyExt, CollectionPersonnelExt, CrawlerResult
from api.db.services.crawler_service import CrawlerResultService
from api.db.services.collection_ext_service import CollectionPolicyExtService
from rag.svr.crawler_engine.collection_writer import CollectionWriter
print("all imports OK")
'
```
冒烟通过后再跑单站测试。`collection_writer.py` 顶层 try/except import 拖累问题（踩坑 #26）：若见 `CrawlerResultService not available`，单独 import 每个模块定位真正失败点。

### Worktree ↔ 主仓同步流程

默认在 `.worktrees/crawler-dev` 开发并 commit；"同步到主仓"指在主仓 `D:/AI/ragflow2/` 把 worktree 分支 merge 进当前分支。

```bash
# 1. Worktree 内提交
cd D:/AI/ragflow2/.worktrees/crawler-dev
git add <files> && git commit -m "..."

# 2. 主仓 fast-forward merge（worktree 分支 base 与主仓 HEAD 相同时）
cd D:/AI/ragflow2
# 2a. 若主仓 WT 有与即将 merge 的文件冲突的残留改动 (内容一致也算冲突):
git checkout HEAD -- <overlapping files>
# 2b. Fast-forward
git merge --ff-only feat/crawler-dev

# 3. 若有 doc 改动只在主仓 WT (如 D:\AI\ragflow2\踩坑问题清单.md):
cd D:/AI/ragflow2
git add <doc files> && git commit -m "docs: ..."

# 4. 反向同步 worktree 分支 (doc commit 在主仓产生)
cd D:/AI/ragflow2/.worktrees/crawler-dev
git merge --ff-only feat/unified-crawler-framework
```

最终 `feat/unified-crawler-framework` 与 `feat/crawler-dev` 指向同一 HEAD，worktree 与主仓工作树（除未跟踪文件）一致。**禁止自动 push、禁止自动重启 Docker。**

---

## 前端架构

### 路由分割 (CRITICAL)

```
C端 (未匹配 ADMIN_PREFIX /5d41402abc4b2a76b9719d911017c592/)
  / → c-landing (着陆页)
  /login → c-login
  /home → ★ c-chat (投标助手对话页)

B端 (匹配 ADMIN_PREFIX)
  /5d41402abc4b2a76b9719d911017c592/...
  home → ★ 标讯管理 | agent → Agent画布编辑器 | datasets → 知识库
```

C端和B端是完全独立的两套代码，修改前务必确认路由归属。

### 标讯前端关键文件
- `web/src/pages/home/bid-detail-view.tsx` — 详情页 (正文/结构化/附件三页签)
- `web/src/components/bid/contract-list.tsx` — 中标/合同列表 + 详情面板
- `web/src/components/bid/enterprise-search.tsx` — ★ 企业查询 (5 Tab: 工商信息/股东高管/变更记录/经营风险/资质信息)
- `web/src/components/bid/tender-search.tsx` — ★ 标讯搜索 v4 (搜索表单 + 无限滚动结果列表, 详情见 `D:\AI\ragflow2\二次开发功能汇总.md` 第3节)
- `web/src/services/bid-service.ts` — 所有标讯 API 调用

### 前端字段名兼容
```tsx
const fileUrl = f.fileUrl || f.file_url || f.url || '';
const fileName = f.name || f.file_name || '';
```

### 企业查询 (Enterprise Query)
- **新接口**: `GET /bid/enterprises/business?keyword=xxx` — 阿里云API市场全量工商信息
- **缓存**: `bid_enterprise_business` 表, keyword 主键, TTL=7天
- **缓存**: DB优先, TTL=7天
- **前端**: 页面初始化只展示搜索表单, 输入查询条件后触发查询
- **旧端点已移除**: `/bid/enterprises/profile`, `/contacts`, `/customers`, `/suppliers`
- **Agent工具保留**: `agent/tools/bid.py` 中旧企业画像工具继续使用 v2 API (项目关系数据)

---

## 部署

### 服务器信息
- **IP**: `47.98.102.55` | **用户**: `root` | **密钥**: `D:\AI\konus-key.pem`
- **项目路径**: `/home/bid-agent-konus/ragflow2/` | **容器**: `docker-ragflow-cpu-1`
- **SCP**: `scp -i "D:\AI\konus-key.pem" -o StrictHostKeyChecking=no <local> root@47.98.102.55:<remote>`
- **SSH**: `ssh -i "D:\AI\konus-key.pem" -o StrictHostKeyChecking=no root@47.98.102.55 "<cmd>"`

### Bind Mount 映射 (宿主机→容器)
`rag/`, `api/`, `agent/`, `common/`, `deepdoc/`, `web/dist/` — SCP到宿主机即热更新

### ⚠️ Inode 陷阱
`rm -rf dist/*` (删内容，保留inode) ✓ | `mv dist dist.old && tar` (新inode，bind mount断开) ✗

### 前端部署
```bash
cd D:\AI\ragflow2\web && npm run build
tar -czf dist.tar.gz dist/
scp ... dist.tar.gz root@47.98.102.55:/home/bid-agent-konus/ragflow2/web/
ssh ... "cd /home/bid-agent-konus/ragflow2/web && rm -rf dist/* dist/.[!.]* dist/..?* 2>/dev/null; tar -xzf dist.tar.gz && rm -f dist.tar.gz"
ssh ... "docker exec docker-ragflow-cpu-1 nginx -s reload"
```

### 后端部署
SCP 修改的 Python 文件到服务器对应路径 → `docker restart docker-ragflow-cpu-1`

### Flutter 本地开发 (Android 模拟器)

**环境变量** (已写入 `~/.bashrc`，新终端自动生效):

```bash
export ANDROID_HOME="F:/code"
export JAVA_HOME="F:/androidstudio/jbr"      # Android Studio 自带 JDK 21
PATH=$(echo "$PATH" | sed 's#/c/Program Files (x86)/Common Files/Oracle/Java/javapath:##')
export PATH="$JAVA_HOME/bin:$ANDROID_HOME/platform-tools:$ANDROID_HOME/cmdline-tools/latest/bin:/c/Users/lg186/flutter/bin:$PATH"
```

| 组件 | 路径 |
|---|---|
| Flutter 3.44.2 | `C:/Users/lg186/flutter` |
| JDK 21 | `F:/androidstudio/jbr` |
| Android SDK | `F:/code` |
| AVD | `flutter_emulator` (Pixel 6, API 36) |

**快速启动**:
```bash
emulator -avd flutter_emulator -no-snapshot-load &  # 或从 AS AVD Manager 启动
cd D:/AI/ragflow2/bidding_app
flutter run -d emulator-5554
```

**API 地址** (`lib/core/api/api_client.dart`):
- 本机无 Docker: `http://47.98.102.55:9380` (远程服务器)
- 本机有 Docker: `http://10.0.2.2:9380` (模拟器→宿主机)

### Flutter APK 构建
需要中国镜像环境变量 + Java 17 + NDK 28.2 + lucide_icons 兼容性修复。详见 `F:\投标项目\AI\本地部署服务器.md`。

---

## 关键约束

1. **禁止自动部署** — 部署/Docker重启需用户明确指示
2. **禁止修改上游核心文件** — `ragflow_server.py`, `db_models.py`, `pipeline.py`，除非用户确认
3. **禁止重启 Docker** — 用户桌面 Docker 可能未运行
4. **API 按次收费** — 必须走 DB→API→fallback，不能直接调API
5. **前端热部署** — Vite bind mount，TS修改自动生效；仅 `node_modules` 变更或热部署不生效时才 `npm run build`
6. **rm -rf dist/\*** — 绝不能用 `mv dist` 破坏 bind mount
7. **C端≠B端** — 两套独立代码，修改前确认路由
8. **字段名映射** — 缓存返回必须用DB数据(snake_case)，不能透传API raw data(camelCase)

## Konus Skills

| 技能 | 用途 |
|------|------|
| `konus` | 意图路由器 + 项目上下文 |
| `konus-code-review` | ★ 代码审查 (任何开发完成后强制调用) |
| `konus-backend-dev` | 后端 API/ORM/Service 开发 |
| `konus-frontend-dev` | 前端页面/组件开发 |
| `konus-crawler-dev` | 爬虫站点 YAML 配置/修复 |
| `konus-deploy-guide` | 部署规范 |

## 常用命令

### 开发
```bash
# 后端
source .venv/bin/activate && export PYTHONPATH=$(pwd)
bash docker/launch_backend_service.sh
uv run pytest && ruff check && ruff format

# 前端
cd web && npm run dev      # 热部署
cd web && npm run build    # 生产构建

# Docker
docker compose -f docker/docker-compose-base.yml up -d   # 仅基础服务
docker compose -f docker/docker-compose.yml up -d         # 全栈
docker logs -f ragflow-server
```

### 环境要求
Python 3.12–3.14, Node.js >=18.20.4, Docker & Docker Compose, uv, 16GB+ RAM

### 单个测试 & 覆盖率
```bash
uv run pytest test/path/test_file.py::TestClass::test_method -v   # 单个测试
uv run pytest -k "test_name" -v                                    # 按名称匹配
uv run pytest --cov=api/db/services --cov-report=html              # 覆盖率报告
```

### Ruff 配置 (pyproject.toml)
line-length=200, lint: ASYNC/ASYNC1 enabled, E402 ignored
