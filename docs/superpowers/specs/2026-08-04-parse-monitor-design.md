# 解析监控 Tab 设计

**日期**: 2026-08-04
**定位**: 长期健康看板（permanent），日常监控 Document 解析吞吐 / 失败 / 积压。
**入口**: 智能采集 → crawl4ai 页面第 4 个 Tab「解析监控」。

---

## 背景

当前 `task_executor` 单进程串行解析文档，存在以下监控盲区：
- 无可视化的文档状态分布，需 SSH 进容器跑 SQL 才知道积压多少
- `bulk_reparse_zombies.py` 定时脚本的运行结果只在日志文件中（`docker/bulk_reparse.log`）
- 失败文档无聚合视图，排查需要直接查表

需要一个 Web 看板，让用户直接在浏览器观察解析恢复进度与系统健康度。

---

## 架构

### 前端
- 新组件：`web/src/pages/crawl4ai/parse-monitor-tab.tsx`
- 挂载点：`web/src/pages/crawl4ai/index.tsx` 新增第 4 个 Tab，value=`parse-monitor`，label=`crawl4ai.parse.tab`（"解析监控"）
- 国际化：仅 `web/src/locales/zh.ts` 新增 key（项目规范，不同步 en.ts）

### 后端
- 复用 `api/apps/restful_apis/collection_app.py`（与探测监控共用 blueprint，不新开）
- 新增 3 个端点：
  - `GET /collection/parse-monitor/overview` — 状态分布 + 吞吐 + ETA
  - `GET /collection/parse-monitor/reparse-batches` — 最近 20 批 bulk_reparse 摘要
  - `GET /collection/parse-monitor/failed-docs` — 失败/卡死文档分页

### 缓存
- Redis 键 `parse_monitor:overview`，TTL 60s（与前端轮询节奏一致）
- Redis List `parse_monitor:batches`，LTRIM 保留最近 20 条
- Redis 异常时降级直查 DB，不报错

---

## 信息块

### 1. 顶部 4 张概览卡片

| 运行中（run=1） | 已完成（run=3） | 失败（run=4） | 积压（run=1 且 update_time<now-30min） |
|---|---|---|---|
| 数字 + 占比 | 数字 + 占比 | 数字 + 占比 | 数字（红色高亮，>0 时） |

- 数据源：`SELECT run, COUNT(*) FROM document GROUP BY run`
- 积压额外条件：与 `_bulk_reparse_zombies.py` 的筛选一致，避免和 task_executor 当前在跑的文档冲突

### 2. 吞吐 + ETA 区

- **最近 1 小时完成数**：`COUNT(*) WHERE run='3' AND update_time > now-1h`
- **平均速率**：docs/min = 完成数 / 60
- **ETA**：`积压数 / 速率`，格式化为 "约 X 小时 Y 分钟"
- 速率=0 时显示 "暂无吞吐数据"

### 3. bulk_reparse 批次表

- 改 `_bulk_reparse_zombies.py`：`main()` 末尾用 `REDIS_CONN.lpush + ltrim` 写一行 JSON 到 `parse_monitor:batches`
- 字段：
  ```json
  {
    "ts": 1722861600,
    "total": 663,
    "success": 663,
    "failed": 0,
    "skipped": 0,
    "duration_sec": 21.4,
    "first_errors": ["doc_id: msg", "..."]
  }
  ```
- 前端表格列：批次时间 / 扫描数 / 成功 / 失败 / 跳过 / 耗时 / 错误摘要（hover 展开）
- 空列表显示 "暂无批次记录"

### 4. 失败/卡死文档表（分页）

- 查询：`WHERE run='4' OR (run='1' AND update_time < now - 30min)`
- JOIN `knowledgebase` 取 KB 名
- 列：文件名 / KB名 / 状态 / 进度% / 错误摘要 / 更新时间
- 筛选：状态下拉（失败/积压/全部）、KB 下拉
- 分页：默认 20，最大 100
- 行点击：跳转 `/datasets/{kb_id}?doc={doc_id}`（沿用现有 URL 约定）
- 不返回字段：`content_hash`、`location`、`content_html` 等内容字段（脱敏）

---

## 数据流

```
前端 useQuery(60s 轮询，可关闭)
  → GET /collection/parse-monitor/overview
    → 检查 Redis parse_monitor:overview
      ├─ 命中 → 返回缓存
      └─ 未命中 → DB 聚合查询 → 计算吞吐/ETA → 写 Redis(TTL=60s) → 返回
```

后端响应结构（`overview`）：
```json
{
  "now": 1722861600,
  "states": {
    "unstart": 12, "running": 52318, "cancel": 0,
    "done": 154000, "fail": 205
  },
  "total": 206535,
  "backlog": 41200,
  "done_last_1h": 1020,
  "rate_per_min": 17.0,
  "eta_sec": 145200,
  "cached_at": 1722861540
}
```

---

## 安全与权限

- 已登录用户均可访问（与探测监控、采集任务列表一致，沿用 `_SHARED_TENANT = "system"` 语义）
- `failed-docs` 端点不返回内容字段（仅元数据 + 错误摘要）
- 分页 size 上限 100，防止恶意大页
- Redis 操作全部 try/except 降级，不影响主流程

---

## 不做（YAGNI）

- 历史趋势图（C 方案预留，未来可加 `system_health_snapshot` 表 + cron 采样）
- 邮件 / 钉钉 / 告警通知
- 看板内"一键重解析"按钮（已有 `/datasets/<id>/documents/parse` 端点，在 KB 文档列表页操作即可）
- 多租户隔离（全局视图，所有 KB 汇总）
- 实时推送（SSE/WebSocket）—— 60s 轮询足够

---

## 部署清单（成套 SCP）

| 类型 | 路径 |
|------|------|
| 后端 API | `api/apps/restful_apis/collection_app.py` |
| 脚本改动 | `rag/svr/_bulk_reparse_zombies.py` |
| 前端组件 | `web/src/pages/crawl4ai/parse-monitor-tab.tsx` (新) |
| 前端入口 | `web/src/pages/crawl4ai/index.tsx` |
| 前端 service | `web/src/services/collection-service.ts` |
| 前端 i18n | `web/src/locales/zh.ts` |
| API 路由表 | `web/src/utils/api.ts` |

部署顺序：后端 → 前端 build → SCP dist → nginx reload（不自动，需用户触发）。

---

## 验收标准

1. 进入 智能采集 → 解析监控 Tab，60s 内看到 4 张卡片有数据
2. 卡片数值与 `SELECT run, COUNT(*) FROM document GROUP BY run` 一致
3. ETA 在有积压时显示，无积压时显示 "无积压"
4. bulk_reparse 批次表显示最近 20 批，包含时间/成功/失败
5. 失败文档表可翻页、可按状态筛选、可跳转 KB
6. 手动关闭自动刷新开关后，60s 内不再发请求
