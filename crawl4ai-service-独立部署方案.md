# Crawl4AI 独立爬虫服务 — 部署与实施方案

> **状态**: 待执行 | **创建日期**: 2026-07-16 | **目标**: 废弃现有定时任务爬虫体系，独立部署 crawl4ai Docker + 自研调度服务
>
> **关键路径**:
> - crawl4ai 项目源码: `D:\AI\ragflow2\crawl4ai\`
> - 本方案文档: `D:\AI\ragflow2\crawl4ai-service-独立部署方案.md`
> - crawl4ai Docker 部署配置: `D:\AI\ragflow2\crawl4ai\deploy\docker\`

---

## 一、概述与目标

### 1.1 背景

当前 RAGFlow 项目中的定时任务爬虫存在以下问题：

- 调度器（`scheduled_task_executor.py`）、执行器（`task_executor.py`）、Redis 队列耦合在一起
- 20+ 个独立爬虫脚本（`rag/svr/ccgp_crawler.py`、`zjfw_crawler.py` 等）各自维护，风格不统一
- 爬取逻辑和入库逻辑混在一起，难以测试和维护
- 不支持 LLM 结构化提取，复杂页面的数据抽取靠正则和 CSS 手写

### 1.2 目标

构建一套**完全独立**的爬虫服务系统：

1. **crawl4ai Docker** — 负责网页抓取和内容提取（Markdown / 结构化 JSON）
2. **自研调度服务** — FastAPI + APScheduler，负责定时调度、结果入库、KB 上传
3. **完全独立部署** — 不依赖 RAGFlow 现有调度体系，独立进程/容器运行
4. **以 API 作为边界** — 调度服务暴露 REST API 供外部触发，内部通过 API 调用 crawl4ai 和 RAGFlow

### 1.3 与现有系统的关系

```
【废弃】                                【新建】
scheduled_task_executor.py      →      crawl4ai_service/scheduler.py
task_executor.py                →      crawl4ai_service/services/crawl_executor.py
rag/svr/ccgp_crawler.py 等20+   →      crawl4ai Docker (统一抓取引擎)
crawler_sites.yaml              →      crawl4ai_service/sites/*.yaml (提取规则迁移)
scheduled_task 表               →      crawler_task 表 (新表, 独立管理)
scheduled_task_log 表           →      crawler_task_log 表 (新表)
crawler_state 表                →      crawler_state 表 (复用/新建)
```

---

## 二、架构设计

### 2.1 整体架构图

```
                           ┌──────────────────────────────────────┐
                           │       Monitoring Stack                │
                           │  ┌────────────┐  ┌─────────────────┐ │
                           │  │ Prometheus │  │    Grafana       │ │
                           │  │ scrape 15s │→│  Dashboard       │ │
                           │  │ :9090      │  │  :3000           │ │
                           │  └─────┬──────┘  └────────┬────────┘ │
                           │        │                  │          │
                           │  ┌─────┴──────────────────┴───────┐  │
                           │  │    Crawl4AI Service Built-in    │  │
                           │  │    Dashboard (:8001/dashboard)  │  │
                           │  │    - Real-time task status       │  │
                           │  │    - Crawl throughput chart      │  │
                           │  │    - Error rate & alerting       │  │
                           │  │    - KB upload status            │  │
                           │  └─────────────────────────────────┘  │
                           └──────────────────────────────────────┘
                                       ↑ metrics
┌─────────────────────────────────────────────────────────────┐
│                    Crawl4AI Service (自研)                    │
│                                                              │
│  ┌──────────┐   ┌──────────────┐   ┌────────────────────┐   │
│  │ FastAPI   │   │ APScheduler  │   │  Crawl Executor    │   │
│  │ REST API  │   │ (cron/intval)│   │  (async HTTP)      │   │
│  │ :8001     │   │              │   │                    │   │
│  └─────┬─────┘   └──────┬───────┘   └─────────┬──────────┘   │
│        │                │                      │              │
│        │    ┌───────────┴──────────────────────┘              │
│        │    │                                                 │
│  ┌─────┴────┴──────────────────────────────────────────┐     │
│  │                   MySQL (独立表)                      │     │
│  │  crawler_task │ crawler_task_log │ crawler_result    │     │
│  │  crawler_site_config │ crawler_state                │     │
│  └─────────────────────────────────────────────────────┘     │
│                                                               │
│  ┌──────────────────────────────────────────────────────┐    │
│  │                   Adapter Layer                        │    │
│  │  ┌──────────────┐  ┌──────────────┐  ┌─────────────┐ │    │
│  │  │ KB Adapter   │  │ Bid Adapter  │  │ Ent Adapter  │ │    │
│  │  │ → RAGFlow KB │  │ → bid_project│  │ → enterprise │ │    │
│  │  └──────┬───────┘  └──────┬───────┘  └──────┬──────┘ │    │
│  └─────────┼─────────────────┼─────────────────┼────────┘    │
└────────────┼─────────────────┼─────────────────┼─────────────┘
             │                 │                  │
             ▼                 ▼                  ▼
    ┌────────────┐   ┌──────────────────────────────────┐
    │ RAGFlow    │   │        RAGFlow MySQL               │
    │ KB API     │   │  bid_project, bid_project_detail,  │
    │ (upload)   │   │  bid_enterprise_business, etc.     │
    └────────────┘   └──────────────────────────────────┘

┌──────────────────────────────────────────────────────┐
│     crawl4ai Docker (unclecode/crawl4ai)             │
│     Port: 11235                                       │
│                                                       │
│  REST API:         自带 UI:                           │
│  POST /crawl       GET /dashboard  ← 引擎实时监控     │
│  POST /crawl/stream GET /playground ← 交互式调试       │
│  POST /crawl/job   GET /schema     ← OpenAPI 文档     │
│  GET  /health      GET /metrics    ← Prometheus       │
└──────────────────────────────────────────────────────┘
```

### 2.2 核心流程

```
1. 调度触发
   APScheduler → 到期任务 → CrawlExecutor.run(task)

2. 爬取阶段
   CrawlExecutor → POST /crawl → crawl4ai Docker
   ← markdown + cleaned_html + extracted_content

3. 结构化提取（三种策略可选）
   ├── CSS/XPath 提取（免费, 快）: JsonCssExtractionStrategy / JsonXPathExtractionStrategy
   ├── LLM 提取（付费, 准）: LLMExtractionStrategy + Pydantic Schema
   └── 混合模式: CSS 提取基础字段 + LLM 提取复杂字段

4. 结果入库
   ├── 结构化 JSON → BidAdapter → bid_project / bid_project_detail 表
   ├── Markdown 正文 → KBAdapter → RAGFlow 知识库文档上传
   ├── 附件 URL → 下载 + KB 上传
   └── 原始结果 → crawler_result 表（保留溯源）

5. 状态更新
   crawler_task_log (success/fail) + crawler_state (断点续爬)
```

---

## 三、技术选型

| 组件 | 选型 | 理由 |
|------|------|------|
| **Web 框架** | FastAPI (Python 3.12+) | async 原生支持，自动生成 OpenAPI 文档，性能优异 |
| **调度器** | APScheduler 4.0 | 内置 cron/interval/date 触发器，支持持久化到数据库 |
| **ORM** | Peewee | 与 RAGFlow 项目一致，减少认知负担 |
| **HTTP 客户端** | httpx (async) | 异步调用 crawl4ai Docker API + RAGFlow API |
| **数据库** | MySQL 8.0（复用现有） | 新建独立表，与 RAGFlow 共用实例 |
| **配置管理** | Pydantic Settings | 类型安全的环境变量/.env 加载 |
| **日志** | structlog + 文件轮转 | 结构化日志，方便接入监控 |
| **指标** | Prometheus + prometheus-fastapi-instrumentator | `/metrics` 端点，自动统计请求量/延迟/错误率 |
| **监控 UI** | Grafana (仪表盘) + 自建内置 Dashboard | Grafana 预置面板 + FastAPI 内嵌监控页面 (`/dashboard`) |
| **容器化** | Docker + Docker Compose | crawl4ai + 本服务 + Prometheus + Grafana 统一编排 |

---

## 四、目录结构

```
D:\AI\ragflow2\crawl4ai_service\
├── main.py                       # FastAPI 应用入口
├── config.py                     # Pydantic Settings 配置定义
├── scheduler.py                  # APScheduler 初始化 + Job 管理
│
├── api/
│   ├── __init__.py
│   ├── router.py                 # 统一路由注册
│   ├── tasks.py                  # 任务 CRUD (POST/GET/PUT/DELETE /tasks)
│   ├── triggers.py               # 手动触发 (POST /tasks/{id}/trigger)
│   ├── results.py                # 结果查询 (GET /results)
│   ├── sites.py                  # 站点配置管理 (GET/POST /sites)
│   ├── health.py                 # 健康检查 + Prometheus /metrics
│   └── dashboard.py              # ★ 内置监控仪表盘 API + HTML 页面
│
├── models/
│   ├── __init__.py
│   ├── task.py                   # CrawlerTask ORM
│   ├── task_log.py               # CrawlerTaskLog ORM
│   ├── crawl_result.py           # CrawlerResult ORM
│   ├── site_config.py            # CrawlerSiteConfig ORM
│   └── state.py                  # CrawlerState ORM (断点续爬)
│
├── services/
│   ├── __init__.py
│   ├── crawl4ai_client.py        # crawl4ai Docker HTTP 客户端封装
│   ├── crawl_executor.py         # 爬取执行引擎（编排逻辑）
│   ├── extraction.py             # 提取策略管理（CSS/XPath/LLM）
│   └── state_manager.py          # 状态管理（断点续爬）
│
├── adapters/
│   ├── __init__.py
│   ├── kb_adapter.py             # RAGFlow 知识库上传适配器
│   ├── bid_adapter.py            # 标讯数据 → bid_project 等表
│   └── enterprise_adapter.py    # 企业数据 → bid_enterprise_business 表
│
├── sites/                        # 站点提取配置
│   ├── ccgp_example.yaml
│   ├── zjfw_example.yaml
│   └── ...
│
├── migrations/
│   └── 001_initial.sql           # 初始建表 SQL
│
├── tests/
│   ├── test_api/
│   ├── test_services/
│   └── test_adapters/
│
├── dashboard/                     # 监控面板
│   ├── templates/
│   │   └── dashboard.html        # ★ 自建监控页面 (Jinja2)
│   └── static/
│       ├── dashboard.css
│       └── dashboard.js          # 实时轮询 + ECharts 图表
│
├── config/                        # 部署配置
│   ├── crawl4ai-config.yml       # crawl4ai 引擎配置
│   ├── prometheus.yml            # Prometheus 抓取规则
│   └── grafana/
│       ├── datasources.yml       # Grafana 数据源
│       └── dashboards/
│           └── crawler-dashboard.json  # ★ 预置面板 JSON
│
├── docker-compose.yml            # crawl4ai + 本服务 + Prometheus + Grafana
├── Dockerfile                    # 本服务镜像
├── requirements.txt
├── .env.example
└── README.md
```

---

## 五、数据库设计（独立表，与 RAGFlow 共用 MySQL）

### 5.1 表结构

```sql
-- 任务定义表
CREATE TABLE crawler_task (
    id          VARCHAR(32)  PRIMARY KEY,
    tenant_id   VARCHAR(32)  NOT NULL DEFAULT 'system',
    name        VARCHAR(255) NOT NULL COMMENT '任务名称',
    description TEXT         COMMENT '任务描述',

    -- 爬取目标
    target_url          TEXT         COMMENT '起始 URL',
    site_config_id      VARCHAR(32)  COMMENT '关联站点配置',

    -- 调度配置
    schedule_type       VARCHAR(16)  NOT NULL DEFAULT 'interval' COMMENT 'cron|interval|manual',
    cron_expression     VARCHAR(64)  COMMENT 'cron 表达式',
    interval_seconds    INT          DEFAULT 3600 COMMENT '间隔秒数',
    enabled             BOOLEAN      DEFAULT TRUE,

    -- 提取配置
    extraction_type     VARCHAR(16)  DEFAULT 'markdown' COMMENT 'markdown|css|llm_schema',
    extraction_schema   JSON         COMMENT 'CSS/XPath schema 或 Pydantic schema',
    llm_provider        VARCHAR(64)  COMMENT 'LLM 提供商 (仅 llm_schema 模式)',
    llm_model           VARCHAR(64)  COMMENT 'LLM 模型',

    -- 输出配置
    output_targets      JSON         COMMENT '["kb","bid_table","enterprise_table"]',
    kb_id               VARCHAR(32)  COMMENT '目标知识库 ID',

    -- 执行参数
    timeout             INT          DEFAULT 3600 COMMENT '超时秒数',
    max_retries         INT          DEFAULT 3,
    max_pages           INT          DEFAULT 10 COMMENT '单次最大翻页数',
    headers             JSON         COMMENT '自定义请求头',
    cookies             JSON         COMMENT '认证 Cookie',

    -- 时间戳
    last_run_time    BIGINT,
    last_run_status  VARCHAR(16),
    next_run_time    BIGINT,
    created_at       DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at       DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,

    INDEX idx_tenant (tenant_id),
    INDEX idx_enabled_next (enabled, next_run_time)
);


-- 执行日志表
CREATE TABLE crawler_task_log (
    id          VARCHAR(32)  PRIMARY KEY,
    task_id     VARCHAR(32)  NOT NULL,
    tenant_id   VARCHAR(32)  NOT NULL,
    status      VARCHAR(16)  NOT NULL DEFAULT 'running' COMMENT 'running|success|fail|cancelled',

    -- 执行信息
    start_time  BIGINT,
    end_time    BIGINT,
    duration    FLOAT        COMMENT '耗时(秒)',
    pages_crawled INT        DEFAULT 0,
    items_found   INT        DEFAULT 0,
    items_new     INT        DEFAULT 0 COMMENT '去重后新增数',

    -- 输出
    output      LONGTEXT     COMMENT 'stdout 日志摘要',
    error_msg   LONGTEXT     COMMENT '错误详情',

    INDEX idx_task (task_id),
    INDEX idx_task_start (task_id, start_time)
);


-- 爬取结果表（原始结果 + 结构化结果）
CREATE TABLE crawler_result (
    id          VARCHAR(32)  PRIMARY KEY,
    task_id     VARCHAR(32)  NOT NULL,
    log_id      VARCHAR(32)  NOT NULL,

    -- 来源信息
    source_url  TEXT         NOT NULL,
    site_id     VARCHAR(128),

    -- 内容
    markdown    LONGTEXT     COMMENT 'Markdown 格式正文',
    cleaned_html LONGTEXT    COMMENT '清洗后的 HTML',
    extracted_json JSON      COMMENT '结构化提取结果',

    -- 状态
    status      VARCHAR(16)  DEFAULT 'raw' COMMENT 'raw|structured|kb_uploaded|archived',

    -- 关联
    kb_doc_id   VARCHAR(32)  COMMENT 'RAGFlow KB 文档 ID',
    bid_project_id VARCHAR(32) COMMENT '关联的 bid_project.id',

    -- 时间
    crawled_at  BIGINT,
    created_at  DATETIME DEFAULT CURRENT_TIMESTAMP,

    INDEX idx_task (task_id),
    INDEX idx_log (log_id),
    INDEX idx_status (status)
);


-- 站点提取配置表
CREATE TABLE crawler_site_config (
    id          VARCHAR(32)  PRIMARY KEY,
    tenant_id   VARCHAR(32)  NOT NULL DEFAULT 'system',
    site_id     VARCHAR(128) NOT NULL COMMENT '站点标识',
    site_name   VARCHAR(255) NOT NULL,
    site_url    TEXT         NOT NULL,

    -- 分页配置
    pagination_type     VARCHAR(32) DEFAULT 'page_param' COMMENT 'page_param|offset|scroll|click',
    pagination_config   JSON        COMMENT '分页参数配置',

    -- 列表提取
    list_base_selector  VARCHAR(255) COMMENT '列表项 CSS 选择器',
    list_fields         JSON         COMMENT '列表字段映射 [{"name":"title","selector":"h2","type":"text"}]',

    -- 详情提取（如果有详情页）
    has_detail_page     BOOLEAN DEFAULT FALSE,
    detail_link_selector VARCHAR(255) COMMENT '详情链接选择器',
    detail_fields       JSON COMMENT '详情字段映射',

    -- 提取策略
    extraction_type     VARCHAR(16) DEFAULT 'css' COMMENT 'css|xpath|llm_schema',
    llm_instruction     TEXT COMMENT 'LLM 提取指令 (仅 llm_schema 模式)',

    -- 输出
    output_targets      JSON COMMENT '["kb","bid_table"]',
    kb_id               VARCHAR(32),
    kb_parser_config    JSON COMMENT 'KB 解析器配置',

    -- 反爬
    request_delay       FLOAT DEFAULT 1.0 COMMENT '请求间隔(秒)',
    use_proxy           BOOLEAN DEFAULT FALSE,
    headers             JSON,

    created_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at  DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,

    UNIQUE INDEX idx_site_tenant (site_id, tenant_id)
);


-- 爬取状态表（断点续爬）
CREATE TABLE crawler_state (
    id          VARCHAR(32)  PRIMARY KEY,
    site_id     VARCHAR(128) NOT NULL,
    task_id     VARCHAR(32)  NOT NULL,

    processed_urls  JSON COMMENT '已处理的 URL 列表',
    last_page       INT  DEFAULT 0,
    last_offset     INT  DEFAULT 0,
    extra_state     JSON COMMENT '额外状态',

    updated_at  DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,

    UNIQUE INDEX idx_site_task (site_id, task_id)
);
```

### 5.2 与 RAGFlow 表的写入关系

| 数据场景 | 本服务表 | → 写入 RAGFlow 表 | 写入方式 |
|---------|---------|-------------------|---------|
| 标讯搜索结果 | `crawler_result` | `bid_project` | MySQL 直写 |
| 标讯详情(正文) | `crawler_result.markdown` | `bid_project_detail` | MySQL 直写 |
| 标讯结构化数据 | `crawler_result.extracted_json` | `bid_project_structure` | MySQL 直写 |
| 企业工商信息 | `crawler_result.extracted_json` | `bid_enterprise_business` | MySQL 直写 |
| 正文 Markdown | `crawler_result.markdown` | RAGFlow Knowledge Base | REST API 上传 |
| 附件文件 | 下载后存 MinIO | `bid_project_file` + RAGFlow KB | REST API 上传 |

---

## 六、API 设计

### 6.1 任务管理

```
POST   /api/v1/tasks              创建任务
GET    /api/v1/tasks               任务列表（支持分页、筛选）
GET    /api/v1/tasks/{id}          任务详情
PUT    /api/v1/tasks/{id}          编辑任务
DELETE /api/v1/tasks/{id}          删除任务
POST   /api/v1/tasks/{id}/enable   启用任务
POST   /api/v1/tasks/{id}/disable  禁用任务
```

**创建任务请求示例**：

```json
{
  "name": "政府采购网-招标公告",
  "target_url": "https://www.ccgp.gov.cn/cggg/zygg/",
  "site_config_id": "site_ccgp_001",
  "schedule_type": "interval",
  "interval_seconds": 3600,
  "extraction_type": "css",
  "output_targets": ["kb", "bid_table"],
  "kb_id": "abc123def456",
  "max_pages": 5,
  "timeout": 1800
}
```

### 6.2 手动触发

```
POST   /api/v1/tasks/{id}/trigger       触发单次执行
POST   /api/v1/tasks/{id}/stop          停止正在运行的任务
POST   /api/v1/tasks/batch-trigger      批量触发（按 site_id 或 tag）
```

### 6.3 执行日志

```
GET    /api/v1/tasks/{id}/logs          某任务的执行日志列表
GET    /api/v1/logs/{log_id}            单条日志详情（含输出摘要）
```

### 6.4 结果查询

```
GET    /api/v1/results                  爬取结果列表（支持按 task_id/site_id/status 筛选）
GET    /api/v1/results/{id}             单条结果详情
POST   /api/v1/results/{id}/reprocess  重新处理（重新入库/KB上传）
```

### 6.5 站点配置

```
GET    /api/v1/sites                    站点配置列表
POST   /api/v1/sites                    创建站点配置
PUT    /api/v1/sites/{id}               编辑站点配置
POST   /api/v1/sites/{id}/test          测试提取规则（发一次爬取，返回提取结果）
```

### 6.6 健康检查 / 监控

```
GET    /api/v1/health                   健康状态
GET    /api/v1/metrics                  Prometheus 指标
GET    /api/v1/stats                    统计概览（任务数/成功率/今日采集量）
```

### 6.7 监控体系

crawl4ai **自带**引擎级监控 UI 和交互式 Playground，我们只需要补业务级监控。

#### 监控总览

```
┌──────────────────────────────────────────────────────────────────────┐
│                         监控入口一览                                   │
│  域名: bidagent.konusmumu.com (Nginx 反代)                            │
├──────────────────────┬─────────────────────┬─────────────────────────┤
│ 内部端口              │ 生产 URL (Nginx)     │ 用途                     │
├──────────────────────┼─────────────────────┼─────────────────────────┤
│  :11235/dashboard    │ 不公开 (内网/VPN)    │ crawl4ai 引擎实时监控      │
│  :11235/playground   │ 不公开 (内网/VPN)    │ 提取策略可视化调试         │
│  :8001/dashboard     │ /crawler/           │ ★ 业务监控: 任务/采集/KB   │
│  :8001/docs          │ /crawler/docs       │ ★ Swagger API 文档        │
│  :3000               │ /monitor/           │ ★ Grafana 统一仪表盘      │
│  :11235/crawl        │ /crawl4ai/          │ 爬取 API (内部调用)        │
└──────────────────────┴─────────────────────┴─────────────────────────┘
```

#### 6.7.1 crawl4ai 自带 Dashboard (`:11235/dashboard`)

crawl4ai v0.7.7+ 内置了实时监控面板，**零开发成本**：

- 系统健康：CPU、内存、网络、运行时间
- 浏览器池管理：PERMANENT / HOT / COLD 三级池实时状态
- 请求跟踪：活跃请求 + 已完成请求历史
- Janitor 清理事件
- 错误监控（含完整上下文）

部署 crawl4ai Docker 后直接访问 `http://<server>:11235/dashboard` 即可使用。

#### 6.7.2 crawl4ai 自带 Playground (`:11235/playground`)

交互式 Web 工具，用于**开发调试阶段**：

- 可视化配置 `CrawlerRunConfig` + `BrowserConfig`
- 点选式构建 CSS/XPath 提取策略，实时预览选中元素
- 测试爬取操作，生成对应 JSON payload
- 多种提取策略的并行对比

**这大幅降低了站点配置的开发成本** — 不需要手写 CSS selector，在 Playground 里点点就能生成。

#### 6.7.3 我们自建 — 业务级 Dashboard (`:8001/dashboard`)

自建部分只关注 crawl4ai 没有的业务指标：

```
GET    /dashboard                        内置监控面板页面 (HTML)
GET    /api/v1/dashboard/overview        概览 (任务数/今日采集/成功率/错误数)
GET    /api/v1/dashboard/task-status     各任务运行状态 (实时)
GET    /api/v1/dashboard/throughput      采集吞吐量 (按小时/天)
GET    /api/v1/dashboard/errors          最近错误列表 (top 20)
GET    /api/v1/dashboard/kb-upload       KB 上传状态统计
```

**页面布局**（轻量，只关注业务指标）：

```
┌──────────────────────────────────────────────────────────────┐
│  Crawl4AI Service Dashboard              [自动刷新 10s]      │
│  [crawl4ai引擎监控] [Playground调试]  ← 链接到 crawl4ai 自带UI │
├────────┬────────┬────────┬────────┬────────┬────────────────┤
│ 任务总数│ 运行中  │ 成功率  │ 今日采集│ KB上传  │  crawl4ai健康  │
│   12   │   3    │ 94.2%  │  1,247 │  89/124│   ● 正常       │
├────────┴────────┴────────┴────────┴────────┴────────────────┤
│  ┌─────────────────────────────┐  ┌───────────────────────┐  │
│  │  采集吞吐量 (近24h)         │  │  任务状态分布          │  │
│  │  (ECharts 柱状图)           │  │  运行/等待/错误        │  │
│  └─────────────────────────────┘  └───────────────────────┘  │
│  ┌─────────────────────────────────────────────────────────┐ │
│  │  最近错误 (Top 10)                            [查看日志]  │ │
│  └─────────────────────────────────────────────────────────┘ │
│  ┌─────────────────────────────────────────────────────────┐ │
│  │  任务实时列表 (状态/上次执行/采集量/下次执行)             │ │
│  └─────────────────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────────────┘
```

#### 6.7.4 Grafana 统一仪表盘 (`:3000`)

预置 Dashboard JSON，导入即用。核心面板：

| 面板 | 数据源 | 展示内容 |
|------|--------|---------|
| Task Execution Overview | Prometheus (本服务) | 各任务执行次数/成功率/耗时趋势 |
| Crawl Throughput | Prometheus (本服务) | 每小时/每天采集量折线图 |
| Error Rate Panel | Prometheus (本服务) | 错误率 + 按错误类型分类 |
| KB Upload Status | Prometheus (本服务) | KB 上传成功/失败/重试数 |
| crawl4ai Engine Health | Prometheus (crawl4ai) | CPU/内存/请求延迟/浏览器池状态 |
| Service Health | Prometheus (本服务) | 本服务 CPU/内存/请求延迟 |

---

## 七、核心代码设计

### 7.1 crawl4ai HTTP 客户端

```python
# services/crawl4ai_client.py
import httpx
from typing import List, Optional, Dict, Any

class Crawl4aiClient:
    """Async HTTP client for crawl4ai Docker REST API."""

    def __init__(self, base_url: str = "http://localhost:11235", api_token: str = ""):
        self.base_url = base_url
        self.headers = {"Authorization": f"Bearer {api_token}"} if api_token else {}

    async def health(self) -> bool:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(f"{self.base_url}/health")
            return r.status_code == 200

    async def crawl(
        self,
        urls: List[str],
        headless: bool = True,
        cache_mode: str = "bypass",
        extraction_strategy: Optional[Dict] = None,
        word_count_threshold: int = 1,
        page_timeout: int = 60000,
    ) -> List[Dict[str, Any]]:
        """Execute a batch crawl. Returns list of CrawlResult dicts."""
        payload = {
            "urls": urls,
            "browser_config": {"type": "BrowserConfig", "params": {"headless": headless}},
            "crawler_config": {
                "type": "CrawlerRunConfig",
                "params": {
                    "cache_mode": cache_mode,
                    "word_count_threshold": word_count_threshold,
                    "page_timeout": page_timeout,
                },
            },
        }
        if extraction_strategy:
            payload["crawler_config"]["params"]["extraction_strategy"] = extraction_strategy

        async with httpx.AsyncClient(timeout=300) as client:
            r = await client.post(
                f"{self.base_url}/crawl",
                json=payload,
                headers=self.headers,
            )
            r.raise_for_status()
            return r.json()

    async def crawl_stream(self, urls: List[str], **kwargs) -> List[Dict]:
        """Streaming crawl for large batch."""
        payload = {
            "urls": urls,
            "browser_config": {"type": "BrowserConfig", "params": {"headless": True}},
            "crawler_config": {
                "type": "CrawlerRunConfig",
                "params": {"cache_mode": "bypass", "stream": True, **kwargs},
            },
        }
        async with httpx.AsyncClient(timeout=600) as client:
            r = await client.post(
                f"{self.base_url}/crawl/stream",
                json=payload,
                headers=self.headers,
            )
            r.raise_for_status()
            return r.json()

    async def submit_job(self, url: str, **config) -> str:
        """Submit async background job, return job_id for polling."""
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(
                f"{self.base_url}/crawl/job",
                json={"url": url, **config},
                headers=self.headers,
            )
            r.raise_for_status()
            return r.json()["job_id"]

    async def get_job_status(self, job_id: str) -> Dict:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(
                f"{self.base_url}/crawl/job/{job_id}",
                headers=self.headers,
            )
            r.raise_for_status()
            return r.json()
```

### 7.2 爬取执行引擎

```python
# services/crawl_executor.py
import logging
from datetime import datetime
from typing import List, Dict, Any
from common.time_utils import current_timestamp
from common.misc_utils import get_uuid
from services.crawl4ai_client import Crawl4aiClient
from services.extraction import build_extraction_strategy
from services.state_manager import StateManager
from adapters.kb_adapter import KBAdapter
from adapters.bid_adapter import BidAdapter

logger = logging.getLogger(__name__)

class CrawlExecutor:
    """Orchestrates a full crawl cycle: crawl → extract → store."""

    def __init__(self, task: Dict, site_config: Dict = None):
        self.task = task
        self.site_config = site_config
        self.client = Crawl4aiClient()
        self.state_mgr = StateManager(task["id"], site_config["site_id"] if site_config else "")
        self.log_id = get_uuid()

    async def run(self) -> Dict[str, Any]:
        """Execute the full crawl cycle. Returns summary dict."""
        start_time = current_timestamp()
        summary = {"status": "running", "pages": 0, "items_new": 0, "error": None}

        try:
            # 1. Build extraction strategy from site config
            strategy = build_extraction_strategy(
                self.task.get("extraction_type"),
                self.site_config,
            )

            # 2. Pagination loop
            page_url = self.task["target_url"]
            for page_num in range(1, self.task.get("max_pages", 10) + 1):
                # 2a. Crawl current page
                results = await self.client.crawl(
                    urls=[page_url],
                    extraction_strategy=strategy,
                )
                if not results or not results[0].get("success"):
                    break

                result = results[0]

                # 2b. Save raw result
                await self._save_raw_result(result)

                # 2c. Extract list items + detail pages
                items = await self._extract_items(result, strategy)

                # 2d. Process each item
                for item in items:
                    is_new = await self.state_mgr.check_and_mark(item.get("id") or item.get("url"))
                    if not is_new:
                        continue

                    # 2e. Crawl detail page if configured
                    if self.site_config and self.site_config.get("has_detail_page"):
                        detail = await self._crawl_detail(item)
                        if detail:
                            item["_detail"] = detail

                    # 2f. Store via adapters
                    await self._store_item(item)
                    summary["items_new"] += 1

                summary["pages"] += 1

                # 2g. Get next page URL
                page_url = self._get_next_page_url(result, page_num)
                if not page_url:
                    break

            summary["status"] = "success"

        except Exception as e:
            logger.exception(f"Crawl task {self.task['id']} failed")
            summary["status"] = "fail"
            summary["error"] = str(e)

        finally:
            duration = (current_timestamp() - start_time) / 1000.0
            await self._save_log(summary, duration)

        return summary

    async def _store_item(self, item: Dict):
        """Route item to configured output targets."""
        targets = self.task.get("output_targets", [])

        if "kb" in targets:
            await KBAdapter(self.task["kb_id"]).upload_markdown(
                title=item.get("title", ""),
                markdown=item.get("markdown", item.get("_detail", {}).get("markdown", "")),
                metadata={"source_url": item.get("url"), "task_id": self.task["id"]},
            )

        if "bid_table" in targets:
            await BidAdapter().upsert_project(item)
```

### 7.3 提取策略管理

```python
# services/extraction.py
from typing import Dict, Optional, Any

def build_extraction_strategy(
    extraction_type: str,
    site_config: Optional[Dict] = None,
) -> Optional[Dict[str, Any]]:
    """
    Build crawl4ai-compatible extraction strategy dict.

    Three modes:
    - markdown: no extraction strategy, just get clean markdown
    - css: JsonCssExtractionStrategy (free, fast)
    - llm_schema: LLMExtractionStrategy (accurate, costs tokens)
    """
    if extraction_type == "markdown":
        return None

    if extraction_type == "css" and site_config:
        return {
            "type": "JsonCssExtractionStrategy",
            "params": {
                "schema": {
                    "name": site_config.get("site_name", "Items"),
                    "baseSelector": site_config["list_base_selector"],
                    "fields": site_config["list_fields"],
                },
                "verbose": True,
            },
        }

    if extraction_type == "llm_schema" and site_config:
        return {
            "type": "LLMExtractionStrategy",
            "params": {
                "llm_config": {
                    "provider": site_config.get("llm_provider", "openai/gpt-4o-mini"),
                },
                "schema": site_config.get("extraction_schema"),
                "extraction_type": "schema",
                "instruction": site_config.get("llm_instruction", ""),
                "extra_args": {"temperature": 0, "max_tokens": 4096},
            },
        }

    return None
```

### 7.4 RAGFlow KB 适配器

```python
# adapters/kb_adapter.py
import httpx
from typing import Optional, Dict

class KBAdapter:
    """Upload crawled content to RAGFlow knowledge base via REST API."""

    def __init__(self, kb_id: str, ragflow_api_base: str = "http://localhost:9380"):
        self.kb_id = kb_id
        self.api_base = ragflow_api_base

    async def upload_markdown(
        self,
        title: str,
        markdown: str,
        metadata: Optional[Dict] = None,
        access_token: str = "",
    ) -> Optional[str]:
        """
        Upload a markdown document to RAGFlow KB.

        Returns doc_id if successful, None otherwise.
        """
        headers = {}
        if access_token:
            headers["Authorization"] = f"Bearer {access_token}"

        # RAGFlow document upload API
        # POST /api/v1/datasets/{dataset_id}/documents
        async with httpx.AsyncClient(timeout=120) as client:
            # Step 1: Upload file content as multipart
            files = {
                "file": (f"{title}.md", markdown.encode("utf-8"), "text/markdown")
            }
            r = await client.post(
                f"{self.api_base}/api/v1/datasets/{self.kb_id}/documents",
                files=files,
                headers=headers,
            )
            if r.status_code != 200:
                return None

            doc_id = r.json().get("data", {}).get("id") or r.json().get("data", [{}])[0].get("id")

            # Step 2: Trigger parsing
            if doc_id:
                await client.post(
                    f"{self.api_base}/api/v1/datasets/{self.kb_id}/documents/{doc_id}/parse",
                    headers=headers,
                )

            return doc_id

    async def upload_file(
        self,
        file_path: str,
        file_name: str,
        access_token: str = "",
    ) -> Optional[str]:
        """Upload a binary file (PDF/Word/Image attachment) to RAGFlow KB."""
        headers = {}
        if access_token:
            headers["Authorization"] = f"Bearer {access_token}"

        async with httpx.AsyncClient(timeout=300) as client:
            with open(file_path, "rb") as f:
                files = {"file": (file_name, f)}
                r = await client.post(
                    f"{self.api_base}/api/v1/datasets/{self.kb_id}/documents",
                    files=files,
                    headers=headers,
                )
            if r.status_code != 200:
                return None
            return r.json().get("data", {}).get("id")
```

### 7.5 标讯数据适配器

```python
# adapters/bid_adapter.py
import json
import hashlib
from typing import Dict, Optional
from common.misc_utils import get_uuid

class BidAdapter:
    """
    Map crawled structured data to RAGFlow bid_* tables.

    Uses the existing DB connection and ORM models.
    CamelCase (crawl4ai output) → snake_case (DB columns).
    """

    async def upsert_project(self, item: Dict) -> str:
        """Insert or update a bid_project record from crawled item."""
        from api.db.db_models import BidProject

        project_id = item.get("id") or get_uuid()
        data = {
            "id": project_id,
            "project_number": item.get("project_number", item.get("projectNumber", "")),
            "title": item.get("title", ""),
            "project_type": item.get("project_type", item.get("projectType", "")),
            "publish_date": item.get("publish_date", item.get("publishDate", "")),
            "source_url": item.get("url", item.get("source_url", "")),
            "content_html": item.get("cleaned_html", item.get("cleanedHtml", "")),
            "content_text": item.get("markdown", ""),
            # ... more field mappings
        }

        BidProject.insert(**data).on_conflict(
            conflict_target=[BidProject.id],
            update=data,
        ).execute()

        return project_id

    async def upsert_detail(self, project_id: str, detail: Dict):
        """Upsert bid_project_detail with full content."""
        from api.db.db_models import BidProjectDetail

        data = {
            "id": project_id,
            "content_html": detail.get("cleaned_html", ""),
            "content_markdown": detail.get("markdown", ""),
            "extracted_data": json.dumps(detail.get("extracted_json", {}), ensure_ascii=False),
        }

        BidProjectDetail.insert(**data).on_conflict(
            conflict_target=[BidProjectDetail.id],
            update=data,
        ).execute()

    async def upsert_enterprise(self, item: Dict) -> str:
        """Upsert bid_enterprise_business from crawled company data."""
        from api.db.db_models import BidEnterpriseBusiness

        keyword = item.get("company_name", item.get("companyName", ""))
        data = {
            "keyword": keyword,
            "business_data": json.dumps(item, ensure_ascii=False),
        }

        BidEnterpriseBusiness.insert(**data).on_conflict(
            conflict_target=[BidEnterpriseBusiness.keyword],
            update=data,
        ).execute()
        return keyword
```

### 7.6 监控仪表盘模块

```python
# api/dashboard.py
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from datetime import datetime, timedelta
from common.time_utils import current_timestamp

router = APIRouter()
templates = Jinja2Templates(directory="dashboard/templates")


# ── HTML 页面 ────────────────────────────────────────────

@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard_page(request: Request):
    """Built-in monitoring dashboard."""
    return templates.TemplateResponse("dashboard.html", {"request": request})


# ── 数据 API ─────────────────────────────────────────────

@router.get("/api/v1/dashboard/overview")
async def dashboard_overview():
    """Overview statistics for the dashboard."""
    from models.task import CrawlerTask
    from models.task_log import CrawlerTaskLog
    from models.crawl_result import CrawlerResult

    total_tasks = CrawlerTask.select().count()
    running_tasks = CrawlerTask.select().where(
        CrawlerTask.last_run_status == "running"
    ).count()

    # 今日统计
    today_start = int(datetime.now().replace(hour=0, minute=0, second=0).timestamp() * 1000)
    today_logs = CrawlerTaskLog.select().where(CrawlerTaskLog.start_time >= today_start)
    today_total = today_logs.count()
    today_success = today_logs.where(CrawlerTaskLog.status == "success").count()
    success_rate = round(today_success / today_total * 100, 1) if today_total else 0

    today_results = CrawlerResult.select().where(
        CrawlerResult.crawled_at >= today_start
    ).count()

    return {
        "total_tasks": total_tasks,
        "running_tasks": running_tasks,
        "today_executions": today_total,
        "success_rate": success_rate,
        "today_items": today_results,
        "timestamp": current_timestamp(),
    }


@router.get("/api/v1/dashboard/task-status")
async def dashboard_task_status():
    """Real-time status of all enabled tasks."""
    from models.task import CrawlerTask

    tasks = CrawlerTask.select().where(CrawlerTask.enabled == True)
    return [
        {
            "id": t.id,
            "name": t.name,
            "status": t.last_run_status or "idle",
            "last_run": t.last_run_time,
            "next_run": t.next_run_time,
            "schedule_type": t.schedule_type,
        }
        for t in tasks
    ]


@router.get("/api/v1/dashboard/throughput")
async def dashboard_throughput(hours: int = 24):
    """Crawl throughput by hour for the last N hours."""
    from models.crawl_result import CrawlerResult

    since = int((datetime.now() - timedelta(hours=hours)).timestamp() * 1000)
    results = (
        CrawlerResult.select()
        .where(CrawlerResult.crawled_at >= since)
        .order_by(CrawlerResult.crawled_at)
    )

    # Bucket by hour
    buckets = {}
    for r in results:
        hour_key = datetime.fromtimestamp(r.crawled_at / 1000).strftime("%Y-%m-%d %H:00")
        buckets[hour_key] = buckets.get(hour_key, 0) + 1

    return [{"hour": k, "count": v} for k, v in sorted(buckets.items())]


@router.get("/api/v1/dashboard/errors")
async def dashboard_errors(limit: int = 20):
    """Recent errors across all tasks."""
    from models.task_log import CrawlerTaskLog

    logs = (
        CrawlerTaskLog.select()
        .where(CrawlerTaskLog.status == "fail")
        .order_by(CrawlerTaskLog.start_time.desc())
        .limit(limit)
    )
    return [
        {
            "log_id": l.id,
            "task_id": l.task_id,
            "start_time": l.start_time,
            "duration": l.duration,
            "error_msg": (l.error_msg or "")[:200],
        }
        for l in logs
    ]


@router.get("/api/v1/dashboard/kb-upload")
async def dashboard_kb_upload():
    """KB upload status statistics."""
    from models.crawl_result import CrawlerResult

    total = CrawlerResult.select().count()
    uploaded = CrawlerResult.select().where(
        CrawlerResult.status == "kb_uploaded"
    ).count()
    pending = CrawlerResult.select().where(
        CrawlerResult.status.in_(["raw", "structured"])
    ).count()

    return {
        "total_results": total,
        "kb_uploaded": uploaded,
        "pending": pending,
        "upload_rate": round(uploaded / total * 100, 1) if total else 0,
    }
```

```html
<!-- dashboard/templates/dashboard.html -->
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <title>Crawl4AI Service Dashboard</title>
    <script src="https://cdn.jsdelivr.net/npm/echarts@5.5.0/dist/echarts.min.js"></script>
    <link rel="stylesheet" href="/static/dashboard.css">
    <meta http-equiv="refresh" content="30">
</head>
<body>
    <div class="header">
        <h1>Crawl4AI Service Dashboard</h1>
        <span class="refresh-badge">Auto-refresh 30s</span>
    </div>

    <div class="stat-cards" id="overview">
        <div class="card"><div class="label">任务总数</div><div class="value" id="totalTasks">-</div></div>
        <div class="card"><div class="label">运行中</div><div class="value" id="runningTasks">-</div></div>
        <div class="card"><div class="label">成功率(今日)</div><div class="value" id="successRate">-</div></div>
        <div class="card"><div class="label">今日采集</div><div class="value" id="todayItems">-</div></div>
        <div class="card"><div class="label">KB上传率</div><div class="value" id="kbUploadRate">-</div></div>
        <div class="card"><div class="label">crawl4ai</div><div class="value" id="c4aiHealth">-</div></div>
    </div>

    <div class="charts-row">
        <div id="throughputChart" style="width:65%;height:300px;"></div>
        <div id="statusPie" style="width:35%;height:300px;"></div>
    </div>

    <div class="section">
        <h2>最近错误</h2>
        <table id="errorsTable">
            <thead><tr><th>时间</th><th>任务</th><th>错误</th><th>操作</th></tr></thead>
            <tbody></tbody>
        </table>
    </div>

    <div class="section">
        <h2>任务列表</h2>
        <table id="tasksTable">
            <thead><tr><th>状态</th><th>任务名</th><th>调度</th><th>上次执行</th><th>下次执行</th></tr></thead>
            <tbody></tbody>
        </table>
    </div>

    <script src="/static/dashboard.js"></script>
</body>
</html>
```

```javascript
// dashboard/static/dashboard.js
// Polling every 10s, ECharts charts for throughput + status distribution

const POLL_INTERVAL = 10000;

async function fetchJSON(url) {
    const r = await fetch(url);
    return r.json();
}

async function refreshOverview() {
    const data = await fetchJSON('/api/v1/dashboard/overview');
    document.getElementById('totalTasks').textContent = data.total_tasks;
    document.getElementById('runningTasks').textContent = data.running_tasks;
    document.getElementById('successRate').textContent = data.success_rate + '%';
    document.getElementById('todayItems').textContent = data.today_items;
}

async function refreshThroughputChart() {
    const data = await fetchJSON('/api/v1/dashboard/throughput?hours=24');
    const chart = echarts.init(document.getElementById('throughputChart'));
    chart.setOption({
        title: { text: '采集吞吐量 (近24h)' },
        xAxis: { data: data.map(d => d.hour.slice(-5)) },
        yAxis: {},
        series: [{ type: 'bar', data: data.map(d => d.count) }],
    });
}

async function refreshErrors() {
    const data = await fetchJSON('/api/v1/dashboard/errors?limit=10');
    const tbody = document.querySelector('#errorsTable tbody');
    tbody.innerHTML = data.map(e => `
        <tr>
            <td>${new Date(e.start_time).toLocaleTimeString()}</td>
            <td>${e.task_id}</td>
            <td class="error">${e.error_msg}</td>
            <td><a href="/api/v1/logs/${e.log_id}">查看</a></td>
        </tr>
    `).join('');
}

async function refreshAll() {
    await Promise.allSettled([
        refreshOverview(),
        refreshThroughputChart(),
        refreshErrors(),
        refreshTasks(),
    ]);
}

refreshAll();
setInterval(refreshAll, POLL_INTERVAL);
```

#### Grafana 面板 JSON 示例（摘录）

```json
{
  "dashboard": {
    "title": "Crawl4AI Service",
    "panels": [
      {
        "title": "Task Execution Rate",
        "type": "graph",
        "targets": [
          {
            "expr": "rate(crawl4ai_service_task_executions_total[5m])",
            "legendFormat": "{{task_name}}"
          }
        ]
      },
      {
        "title": "Crawl Throughput (items/min)",
        "type": "stat",
        "targets": [
          {
            "expr": "rate(crawl4ai_service_items_crawled_total[1m]) * 60"
          }
        ]
      },
      {
        "title": "KB Upload Success Rate",
        "type": "gauge",
        "targets": [
          {
            "expr": "crawl4ai_service_kb_upload_success / crawl4ai_service_kb_upload_total * 100"
          }
        ]
      },
      {
        "title": "crawl4ai Engine Latency (p95)",
        "type": "graph",
        "targets": [
          {
            "expr": "histogram_quantile(0.95, rate(crawl4ai_request_duration_seconds_bucket[5m]))"
          }
        ]
      }
    ]
  }
}
```

---

## 八、站点配置示例（sites/ccgp_example.yaml）

```yaml
# 中国政府采购网 — 招标公告
site_id: ccgp_zygg
site_name: 中国政府采购网-招标公告
site_url: https://www.ccgp.gov.cn/cggg/zygg/

# 分页
pagination:
  type: page_param          # URL query param: &page=2
  param_name: page
  start_page: 1
  max_pages: 5

# 列表提取 (CSS, 免费)
list_extraction:
  type: css
  base_selector: "ul.cggg-list li"
  fields:
    - name: title
      selector: "a"
      type: text
    - name: url
      selector: "a"
      type: attribute
      attribute: href
    - name: publish_date
      selector: "span.date"
      type: text
    - name: region
      selector: "span.region"
      type: text

# 详情页 (CSS + LLM 混合)
detail_page:
  enabled: true
  link_selector: "a"       # 从列表项中的链接进入详情

  # 先用 CSS 提取固定字段
  css_fields:
    - name: project_number
      selector: ".proj-number"
      type: text
    - name: purchaser
      selector: ".purchaser"
      type: text
    - name: budget
      selector: ".budget"
      type: text

  # 再用 LLM 提取复杂字段（可选，按需开启）
  llm_fields:
    enabled: false
    provider: "deepseek/deepseek-chat"
    instruction: |
      从正文中提取以下信息：
      - 采购需求（完整描述）
      - 资格要求（列表）
      - 评审办法（如有）
      - 合同履行期限
    schema:
      type: object
      properties:
        procurement_requirements: {type: string}
        qualification_requirements: {type: array, items: {type: string}}
        evaluation_method: {type: string}
        contract_period: {type: string}

# 输出
output:
  targets: [kb, bid_table]
  kb_id: "target_kb_uuid_here"
  kb_parser: "manual"       # 手动分段，保留 Markdown 结构

# 反爬
anti_crawl:
  request_delay: 2.0        # 请求间隔(秒)
  use_proxy: false
  headers:
    User-Agent: "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    Referer: "https://www.ccgp.gov.cn/"
```

---

## 九、Docker Compose 部署

```yaml
# crawl4ai_service/docker-compose.yml
version: '3.8'

services:
  # ============================================================
  # crawl4ai 官方 Docker — 爬取引擎
  # ============================================================
  crawl4ai:
    image: unclecode/crawl4ai:latest
    container_name: crawl4ai-engine
    restart: unless-stopped
    ports:
      - "11235:11235"
    environment:
      - OPENAI_API_KEY=${OPENAI_API_KEY}
      - DEEPSEEK_API_KEY=${DEEPSEEK_API_KEY}
    volumes:
      - ./config/crawl4ai-config.yml:/app/config.yml
    shm_size: 2g
    deploy:
      resources:
        limits:
          memory: 8G
          cpus: '4'

  # ============================================================
  # Crawl4AI Service — 调度 + 适配层
  # ============================================================
  crawl4ai-service:
    build: .
    container_name: crawl4ai-service
    restart: unless-stopped
    ports:
      - "8001:8001"
    environment:
      - CRAWL4AI_BASE_URL=http://crawl4ai:11235
      - RAGFLOW_API_BASE=http://ragflow-server:9380
      # ★ 数据库 — 复用 RAGFlow 的 MySQL
      - MYSQL_HOST=mysql
      - MYSQL_PORT=3306
      - MYSQL_USER=root
      - MYSQL_PASSWORD=infini_rag_flow
      - MYSQL_DATABASE=rag_flow
      # ★ Redis — 复用 RAGFlow 的 Valkey
      - REDIS_HOST=redis
      - REDIS_PORT=6379
      - REDIS_PASSWORD=infini_rag_flow
      - LOG_LEVEL=INFO
    volumes:
      - ./sites:/app/sites
      - ./logs:/app/logs
      - ./data:/app/data
    depends_on:
      - crawl4ai
    command: >
      sh -c "python main.py"

  # ============================================================
  # Prometheus — 指标采集
  # ============================================================
  prometheus:
    image: prom/prometheus:latest
    container_name: crawl4ai-prometheus
    restart: unless-stopped
    ports:
      - "9090:9090"
    volumes:
      - ./config/prometheus.yml:/etc/prometheus/prometheus.yml
      - prometheus_data:/prometheus
    command:
      - '--config.file=/etc/prometheus/prometheus.yml'
      - '--storage.tsdb.path=/prometheus'
      - '--web.console.libraries=/usr/share/prometheus/console_libraries'
      - '--web.console.templates=/usr/share/prometheus/consoles'

  # ============================================================
  # Grafana — 监控仪表盘
  # ============================================================
  grafana:
    image: grafana/grafana:latest
    container_name: crawl4ai-grafana
    restart: unless-stopped
    ports:
      - "3000:3000"
    environment:
      - GF_SECURITY_ADMIN_USER=admin
      - GF_SECURITY_ADMIN_PASSWORD=${GRAFANA_PASSWORD:-admin}
      - GF_INSTALL_PLUGINS=grafana-clock-panel
      # ★ 支持 Nginx 子路径反代: /monitor/ → grafana:3000
      - GF_SERVER_ROOT_URL=%(protocol)s://%(domain)s/monitor/
      - GF_SERVER_SERVE_FROM_SUB_PATH=true
    volumes:
      - ./config/grafana/datasources.yml:/etc/grafana/provisioning/datasources/datasources.yml
      - ./config/grafana/dashboards:/etc/grafana/provisioning/dashboards
      - grafana_data:/var/lib/grafana
    depends_on:
      - prometheus

volumes:
  prometheus_data:
  grafana_data:
```

### 9.1 配套配置文件

```yaml
# config/prometheus.yml
global:
  scrape_interval: 15s
  evaluation_interval: 15s

scrape_configs:
  - job_name: 'crawl4ai-service'
    static_configs:
      - targets: ['crawl4ai-service:8001']
    metrics_path: '/api/v1/metrics'

  - job_name: 'crawl4ai-engine'
    static_configs:
      - targets: ['crawl4ai:11235']
    metrics_path: '/metrics'
```

```yaml
# config/grafana/datasources.yml
apiVersion: 1
datasources:
  - name: Prometheus
    type: prometheus
    access: proxy
    url: http://prometheus:9090
    isDefault: true
    editable: false
```

```yaml
# config/grafana/dashboards/dashboards.yml
apiVersion: 1
providers:
  - name: 'Crawl4AI'
    orgId: 1
    folder: ''
    type: file
    disableDeletion: false
    editable: true
    options:
      path: /etc/grafana/provisioning/dashboards
```

```yaml
# config/crawl4ai-config.yml — crawl4ai 引擎配置
crawler:
  browser:
    extra_args:
      - "--disable-gpu"
      - "--disable-dev-shm-usage"
      - "--no-sandbox"
    kwargs:
      headless: true
      text_mode: true       # 减少 30-40% 内存
  memory_threshold_percent: 90
  pool:
    idle_ttl_sec: 300
  rate_limiter:
    enabled: true
    base_delay: [1.0, 3.0]
```

### 9.2 crawl4ai 存储架构分析

**关键结论：crawl4ai 本身不用 MySQL，只用到 SQLite 和 Redis。**

```
crawl4ai 存储架构:
┌─────────────────────────────────────────────┐
│  SQLite (~/.crawl4ai/crawl4ai.db)           │
│  ├── crawled_data 表                        │
│  │   └── URL → html/markdown/extracted 缓存  │
│  ├── 内容文件 (hash → disk)                  │
│  │   ├── html_content/{hash}                │
│  │   ├── cleaned_html/{hash}                │
│  │   ├── markdown_content/{hash}            │
│  │   └── extracted_content/{hash}           │
│  └── 作用: 本地缓存，避免重复爬取             │
│      (不可配置为 MySQL, 硬编码 aiosqlite)     │
├─────────────────────────────────────────────┤
│  Redis (config.yml → redis:6379)            │
│  ├── 任务队列 (background jobs)              │
│  ├── Rate Limiting 存储                     │
│  ├── Session 状态                            │
│  └── 作用: 运行时状态，TTL 自动过期           │
│      (可配置 → RAGFlow 的 Valkey 8)          │
└─────────────────────────────────────────────┘

真正的数据持久化 → 由我们的 crawl4ai_service 负责:
  ├── 结构化数据 → RAGFlow MySQL (bid_* 表)
  └── 内容文档 → RAGFlow KB API (知识库上传)
```

### 9.3 crawl4ai config.yml — 适配 RAGFlow 后的完整配置

基于研究 `crawl4ai/deploy/docker/config.yml`，以下是适配 RAGFlow 基础设施的配置：

```yaml
# D:\AI\ragflow2\crawl4ai\deploy\docker\config.yml
# 适配 RAGFlow 服务器基础设施

app:
  title: "Crawl4AI API - RAGFlow"
  version: "1.0.0"
  host: "0.0.0.0"           # Docker 内部监听，Nginx 反代对外
  port: 11235
  reload: False
  workers: 1
  timeout_keep_alive: 300

# LLM — 使用 RAGFlow 已有的 API Key
llm:
  provider: "deepseek/deepseek-chat"
  # api_key 通过环境变量 DEEPSEEK_API_KEY 注入

# ★ Redis — 指向 RAGFlow 的 Valkey 8
redis:
  host: "redis"               # RAGFlow Docker 网络中的容器名
  port: 6379
  db: 0
  password: "infini_rag_flow" # 与 RAGFlow REDIS_PASSWORD 一致
  task_ttl_seconds: 3600
  ssl: False

# 资源限制
limits:
  max_body_bytes: 10485760
  max_pages: 100
  max_depth: 5
  wall_clock_s: 600            # 单次爬取超时 10 分钟
  queue:
    maxsize: 1000
    workers: 4
    per_principal: 0

# 速率限制 — 使用 Redis 存储（多实例共享）
rate_limiting:
  enabled: True
  default_limit: "1000/minute"
  storage_uri: "redis://:infini_rag_flow@redis:6379/0"

# 安全
security:
  enabled: true
  jwt_enabled: false           # 内网部署，Nginx 做认证
  api_token: ""
  https_redirect: false
  trusted_hosts: ["*"]
  cors_allow_origins: []

# 爬虫引擎
crawler:
  memory_threshold_percent: 90.0
  rate_limiter:
    enabled: true
    base_delay: [1.0, 2.0]
  timeouts:
    stream_init: 30.0
    batch_process: 300.0
  pool:
    max_pages: 40
    idle_ttl_sec: 300
  browser:
    kwargs:
      headless: true
      text_mode: true         # 减少 30-40% 内存
    extra_args:
      - "--no-sandbox"
      - "--disable-dev-shm-usage"
      - "--disable-gpu"

logging:
  level: "INFO"
  format: "%(asctime)s - %(name)s - %(levelname)s - %(message)s"

observability:
  prometheus:
    enabled: True
    endpoint: "/metrics"
  health_check:
    endpoint: "/health"

webhooks:
  enabled: true
  retry:
    max_attempts: 5
    initial_delay_ms: 1000
    max_delay_ms: 32000
    timeout_ms: 30000
```

### 9.4 环境变量对照表

| 变量 | RAGFlow (.env) | crawl4ai (config.yml 或 .llm.env) |
|------|---------------|-----------------------------------|
| Redis 地址 | `REDIS_HOST=redis` | `redis.host: "redis"` |
| Redis 端口 | `REDIS_PORT=6379` | `redis.port: 6379` |
| Redis 密码 | `REDIS_PASSWORD=infini_rag_flow` | `redis.password: "infini_rag_flow"` |
| MySQL 地址 | `MYSQL_HOST=mysql` | *(crawl4ai 不用 MySQL)* |
| MySQL 密码 | `MYSQL_PASSWORD=infini_rag_flow` | *(由 crawl4ai_service 使用)* |
| DeepSeek Key | *(在 agent 配置中)* | `.llm.env: DEEPSEEK_API_KEY=sk-xxx` |

### 9.5 部署时需要注意的网络配置

crawl4ai 容器需要加入 RAGFlow 的 Docker 网络才能访问 `redis` 和 `mysql` 容器：

```yaml
# docker-compose.yml 中的网络配置
services:
  crawl4ai:
    networks:
      - ragflow_network    # 加入 RAGFlow 的 Docker 网络

  crawl4ai-service:
    networks:
      - ragflow_network

networks:
  ragflow_network:
    external: true          # 使用 RAGFlow docker-compose 创建的网络
    name: docker_ragflow    # RAGFlow 默认网络名
```

---

## 十、实施步骤

### Phase 1: 基础设施搭建（1-2 天）

| 步骤 | 内容 | 验证标准 |
|------|------|---------|
| 1.1 | 在服务器上 `docker pull unclecode/crawl4ai:latest` | `curl localhost:11235/health` 返回 200 |
| 1.2 | 配置 crawl4ai `config.yml`（浏览器池、速率限制、内存阈值） | 爬取一个测试 URL 成功返回 markdown |
| 1.3 | 在开发机创建 `crawl4ai_service/` 目录结构 | 目录 + `__init__.py` 全部就位 |
| 1.4 | 编写 `requirements.txt` 并安装依赖 | `uv pip install -r requirements.txt` 无报错 |

### Phase 2: 核心服务开发（3-5 天）

| 步骤 | 内容 | 验证标准 |
|------|------|---------|
| 2.1 | 实现 `models/` — Peewee ORM 模型（5 张表） | `python -c "from models import *"` 无报错 |
| 2.2 | 编写 `migrations/001_initial.sql`，在开发数据库执行 | 表创建成功 |
| 2.3 | 实现 `services/crawl4ai_client.py` | 异步调用 crawl4ai Docker 成功返回结果 |
| 2.4 | 实现 `services/extraction.py` — 三种提取策略 | CSS 提取测试通过，LLM 提取测试通过 |
| 2.5 | 实现 `services/crawl_executor.py` — 分页 + 去重 + 适配器调用 | 完整爬取一个站点，结果写入 `crawler_result` 表 |
| 2.6 | 实现 `adapters/kb_adapter.py` — RAGFlow KB 上传 | Markdown 文件上传到 KB 并解析成功 |
| 2.7 | 实现 `adapters/bid_adapter.py` — bid_* 表写入 | 结构化数据正确写入 `bid_project` 表 |

### Phase 3: API + 调度器 + 监控 UI（3-4 天）

| 步骤 | 内容 | 验证标准 |
|------|------|---------|
| 3.1 | 实现 `api/tasks.py` — 任务 CRUD 端点 | Swagger UI 可创建/查询/编辑/删除任务 |
| 3.2 | 实现 `api/triggers.py` — 手动触发 + 停止 | 手动触发后日志表有执行记录 |
| 3.3 | 实现 `api/results.py` — 结果查询 | 可按 task_id/status/site_id 筛选 |
| 3.4 | 实现 `api/sites.py` — 站点配置管理 | 上传 YAML 配置 → 写入 `crawler_site_config` 表 |
| 3.5 | 实现 `scheduler.py` — APScheduler 集成 | 创建 cron 任务 → 等待 → 日志表确认执行 |
| 3.6 | 实现 `api/health.py` — 健康检查 + Prometheus metrics | `/health` 200, `/metrics` 有数据 |
| 3.7 | ★ 实现 `api/dashboard.py` — 内置监控 API | `/api/v1/dashboard/overview` 等 5 个端点可返回数据 |
| 3.8 | ★ 编写 `dashboard/` — 监控 HTML 页面 + ECharts 图表 | `/dashboard` 页面展示概览/吞吐量图/错误列表/任务列表 |
| 3.9 | ★ 编写 `config/grafana/dashboards/crawler-dashboard.json` | Grafana 导入后面板正常展示 |

### Phase 4: 站点配置（暂不迁移旧站点）

| 步骤 | 内容 | 验证标准 |
|------|------|---------|
| 4.1 | 新建 1-2 个测试站点 YAML 配置 | CSS 提取 + 分页正常 |
| 4.2 | 验证增量采集 + 去重逻辑 | 两次执行只采集新增项 |
| 4.3 | 验证 KB 入库 + structured data 入库 | RAGFlow KB 可检索 + bid_* 表有数据 |

### Phase 5: 部署 + 监控上线（1-2 天）

| 步骤 | 内容 | 验证标准 |
|------|------|---------|
| 5.1 | 编写 `Dockerfile` | `docker build -t crawl4ai-service .` 成功 |
| 5.2 | 服务器部署 `docker-compose.yml`（4 容器: crawl4ai + service + prometheus + grafana） | 4 个容器全部 healthy |
| 5.3 | 配置 Nginx 反向代理 `/dashboard` 和 Grafana 端口 | 浏览器访问正常 |
| 5.4 | 导入 Grafana 预置 Dashboard JSON | 面板展示实时数据 |
| 5.5 | 配置日志轮转 + 磁盘监控 | 日志文件自动清理 |
| 5.6 | 配置告警（企业微信/邮件通知爬取失败） | 模拟失败 → 收到告警 |

### Phase 6: 废弃旧系统（1 天）

| 步骤 | 内容 |
|------|------|
| 6.1 | 禁用 `scheduled_task_executor.py` 进程 |
| 6.2 | 标记旧 `scheduled_task` 表中的任务为 disabled |
| 6.3 | 归档旧爬虫脚本 `rag/svr/*_crawler.py` |
| 6.4 | 观察一周，确认新系统稳定 |

---

## 十一、风险与应对

| 风险 | 概率 | 影响 | 应对措施 |
|------|------|------|---------|
| crawl4ai Docker 内存泄漏导致 OOM | 中 | 高 | 设置 `--memory=8g` + restart=unless-stopped + 内存告警 |
| CSS 选择器失效（站点改版） | 高 | 中 | 站点配置「测试」端点 + 连续 3 次 0 结果自动告警 |
| LLM 提取质量不稳定 | 中 | 中 | 优先用 CSS，LLM 仅用于复杂页面；结果需校验 schema |
| RAGFlow KB API 变更 | 低 | 中 | KB Adapter 封装，只改适配器 |
| 服务器资源不足（2个新容器） | 中 | 高 | 先评估现有服务器资源；可降级为单进程部署 |
| crawl4ai 无法处理国内网站反爬 | 中 | 中 | 配置 headers/cookies/proxy；复杂反爬站点保留旧脚本做 fallback |

---

## 十二、未来扩展

1. **爬虫市场** — 预置 50+ 常见招投标站点配置模板
2. **AI 自动适配** — 用户输入 URL + 目标字段描述 → LLM 自动生成 CSS selector
3. **多渠道通知** — 钉钉/飞书/企业微信/邮件的新增标讯推送
4. **高级任务编排** — 多站点链式爬取、条件触发、依赖管理等

---

## 附录 A: 环境变量清单（.env.example）

```bash
# crawl4ai Docker
OPENAI_API_KEY=sk-xxx
DEEPSEEK_API_KEY=sk-xxx
ANTHROPIC_API_KEY=sk-xxx

# Crawl4AI Service
CRAWL4AI_BASE_URL=http://localhost:11235
CRAWL4AI_API_TOKEN=           # 如果 crawl4ai 开启了 JWT 认证

# RAGFlow
RAGFLOW_API_BASE=http://localhost:9380
RAGFLOW_ACCESS_TOKEN=         # RAGFlow API 访问令牌

# MySQL (与 RAGFlow 共用实例)
MYSQL_HOST=mysql
MYSQL_PORT=3306
MYSQL_USER=root
MYSQL_PASSWORD=infini_rag_flow
MYSQL_DATABASE=rag_flow

# Service
LOG_LEVEL=INFO
SERVICE_PORT=8001
```

## 附录 B: 与 RAGFlow 的字段映射速查表

| crawl4ai 输出路径 | JSON 字段(camelCase) | RAGFlow DB 字段(snake_case) |
|---|---|---|
| `result.markdown` | — | `bid_project_detail.content_markdown` |
| `result.cleaned_html` | — | `bid_project_detail.content_html` |
| `result.extracted_content[].title` | title | `bid_project.title` |
| `result.extracted_content[].projectNumber` | projectNumber | `bid_project.project_number` |
| `result.extracted_content[].publishDate` | publishDate | `bid_project.publish_date` |
| `result.extracted_content[].url` | url | `bid_project.source_url` |
| `result.extracted_content[].fileUrl` | fileUrl | `bid_project_file.file_url` |
| `result.extracted_content[].fileName` | fileName | `bid_project_file.file_name` |
| `result.extracted_content[].companyName` | companyName | `bid_enterprise_business.keyword` |
