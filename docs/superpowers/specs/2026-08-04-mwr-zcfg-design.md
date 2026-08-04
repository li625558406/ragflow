水利部 — 政策法规智能采集 设计文档
======================================

- **日期**: 2026-08-04
- **分支**: `feat/unified-crawler-framework`
- **系统**: 智能采集系统（System C，`unified_crawler.py` + YAML → `crawler_result` + 扩展表）
- **目标页**: `http://www.mwr.gov.cn/zw/zcfg/`（中华人民共和国水利部 — 政策法规）4 个子列表：
  - `/zw/zcfg/fl/`         → 法律
  - `/zw/zcfg/xzfghfgxwj/` → 行政法规和法规性文件
  - `/zw/zcfg/bmgz/`       → 部门规章
  - `/zw/zcfg/gfxwj/`      → 规范性文件
- **KB**: `3b4f619c85c211f198269135a1db216c`（tenant `7ab771d4dec84f23b2c1fb5f4e453ff9`）
- **site_id**: `mwr_zcfg`
- **类型字段（前端"类型"列显示）**: 4 选 1 — **国家水利部-政策法规-法律 / -行政法规和法规性文件 / -部门规章 / -规范性文件**
- **category**: `policy`

---

## 0. 第一性原理推导

**本质问题**：把 mwr.gov.cn 政策法规 4 个子列表的"列表 → 详情 → 附件"可靠地落到 `crawler_result` + KB，且重复触发不产生脏数据、原文地址可在浏览器直接打开、4 类内容在前端可区分。

**硬约束**：

1. 4 个子列表共用同一套 TRS CMS 模板（与 mwr_zcjd 同系），静态 HTML，开放 HTTP，无加密、无 SPA、无 token — 不需要 spa_render / encrypted_api / playwright。
2. 列表项 `<a href>` 有两类：相对路径（`./YYYYMM/t...html`，真实政策原文）和绝对路径（`http://www.mwr.gov.cn/hd/zxft/...`，新闻发布会迷你站，被聚合到列表）。两类**详情页结构不同**，脚本必须自适应。
3. 用户只要每个列表第 1 页；后续按 publish_date 过滤当天。
4. 反爬等级 🟢 L1：UA + Referer + 延迟 + 重试即可，无 Cookie/Cloudflare。
5. 重复触发必须幂等 → 主键 `md5(site_id "|" source_url)`，URL 规范化（去 fragment、统一绝对化）后哈希。
6. 4 类内容必须在前端"类型"列区分 → `extracted_json.section_name` 按来源 section 写不同值。
7. KB 已有附件管道（`AttachmentHandler` 原生支持 ZIP 解压、嵌套 ZIP、文件类型白名单），**不要再在脚本里手写 zip 解压**。

**为何不用纯 YAML**：详情页有多套模板（TRS_UEDITOR 旧版、xlcontainer 新版、新闻发布会迷你站），YAML extractor 无法表达回退链 + meta 标签优先 + 正文清洗 + 4 section 循环。custom_runner 是最小代价方案。

**为何单 site_id 而非 4 个**：4 个子列表共享提取逻辑、共享 KB、共享 dedup 上下文、用户视角是"水利部政策法规一个站"。拆 4 个 site_id 会把 1 套逻辑复制 4 份、探测器监控行变成 4 行、状态分散。单 site_id + 4 section 内部循环是高内聚低耦合的唯一解。

---

## 1. 站点特性（实测 2026-08-04）

| 维度 | 描述 |
|------|------|
| 渲染 | 静态 HTML + jQuery（仅导航），无 SPA / 加密 / token |
| 列表结构 | 若干 `<ul class="slnewsconlist">`，每个 5 个 `<li>`，第 1 页 ~5/23/24/25 条 |
| 列表 li 结构 | `<li><span>YYYY-MM-DD</span><a href="...">标题</a></li>` |
| href 形态 | ① 相对 `./YYYYMM/tYYYYMMDD_NNNNNNN.html`（详情正文）② 绝对 `http://www.mwr.gov.cn/hd/zxft/.../index.html`（新闻发布会迷你站） |
| 分页 | 列表页有多页（fl 1页 / xzfghfgxwj 多页 / bmgz 3页 / gfxwj 16页），**仅抓第 1 页** |
| 详情元数据 | TRS CMS meta 标签：`ArticleTitle / PubDate / ContentSource / ColumnName / ArticleAuthor` |
| 详情正文容器 | ① `div.TRS_UEDITOR.trs_paper_default.trs_word`（旧模板）② `div.xlcontainer`（新模板）③ 新闻发布会迷你站：`div.newsfbh1art` 等 |
| 附件块 | `div.fujian`（TRS 标准，存在时含 `<a>` 直链或 `fjdzapp` 网关链接） |
| 编码 | UTF-8 |
| 反爬 | 🟢 L1 — 开放 HTTP，UA + Referer 即可 200 |

---

## 2. 技术选型

- **适配器**: `rest_api`（urllib 引擎，无 ssl 校验）
- **执行模式**: `custom_runner: rag.svr.mwr_zcfg_crawler`（YAML extract 仅供探测器算签名）
- **分页**: `single_page`（每个列表只抓第 1 页）
- **附件**: `AttachmentHandler` 原生处理（脚本只填 `attachments` 字段，含 ZIP 时自动解压）

---

## 3. 文件清单

| 类型 | 路径 |
|------|------|
| 新建 | `rag/svr/mwr_zcfg_crawler.py` |
| 追加 | `rag/svr/crawler_sites.yaml`（`mwr_zcfg` 站点块） |
| 存档 | `docs/superpowers/specs/2026-08-04-mwr-zcfg-design.md`（本文件） |

不动：`db_models.py`、`collection_app.py`、`crawler_engine/*.py`、前端代码。

---

## 4. 数据流

```
unified_crawler.py
  └─ 读 YAML mwr_zcfg，发现 custom_runner: rag.svr.mwr_zcfg_crawler
       └─ run(tenant_id, kb_id, task_name, task_id, writer_mode="collection",
              category="policy", date_filter, full_crawl, force_run, ...)
            ├─ for section_url, section_name in SECTIONS:   # 4 节循环
            │    ├─ GET section_url → BS4 解析 ul.slnewsconlist li
            │    ├─ 25 items → urljoin 绝对化 URL（含 http↓、相对两类）
            │    ├─ 运行内 seen_urls 去重（跨 section 保险）
            │    ├─ date_filter 过滤（空=全量 / today=当日 / YYYY-MM-DD=指定日）
            │    └─ 每条 item:
            │         ├─ GET 详情页 → 提取 title/PubDate/ContentSource/正文/附件
            │         ├─ 正文容器回退: TRS_UEDITOR → xlcontainer → newscontain
            │         ├─ 附件: div.fujian 内 <a href> 直链 + 全页 file_ext 链接双通道
            │         ├─ item.extracted_json.section_name = section_name（4 值之一）
            │         ├─ CollectionWriter.write_all(item, site_id="mwr_zcfg",
            │         │     category="policy", site_display="国家水利部 mwr.gov.cn")
            │         │   → upsert crawler_result（id=md5(mwr_zcfg|source_url)）
            │         │   → collection_policy_ext 扩展行（doc_number/issuing_authority/...）
            │         └─ StoragePipeline.store(normalized_item)
            │             → markdown 入 KB + parse
            │             → AttachmentHandler 自动下载 + ZIP 解压 + 上传 KB + 解析
            └─ return {status, pages=4, items_found, items_new, items_updated,
                       kb_uploaded, attachments_uploaded, errors}

探测器: crawler_detector.py 读 YAML listing.urls（4 URL）抓第1页算拼合签名
       变更 → 入队 unified_crawler --writer collection --category policy --date-filter today
       （若探测器不支持 listing.urls 多 URL，回退到只用 /fl/ 第1页作签名探针；
        custom_runner 仍抓全 4 列表，仅探测灵敏度略降，不影响采集完整性）
```

---

## 5. 4 个 section 配置

```python
SECTIONS = [
    ("http://www.mwr.gov.cn/zw/zcfg/fl/",         "国家水利部-政策法规-法律"),
    ("http://www.mwr.gov.cn/zw/zcfg/xzfghfgxwj/", "国家水利部-政策法规-行政法规和法规性文件"),
    ("http://www.mwr.gov.cn/zw/zcfg/bmgz/",       "国家水利部-政策法规-部门规章"),
    ("http://www.mwr.gov.cn/zw/zcfg/gfxwj/",      "国家水利部-政策法规-规范性文件"),
]
```

- 每条 item 的 `extracted_json.section_name` = 该 section 中文名（4 值之一）
- `extracted_json.topic_category` = "政策法规"
- `issuing_authority` = "中华人民共和国水利部"
- 其它字段（doc_number/content_source/column_name/publish_date/source_url）与 mwr_zcjd 同源同逻辑
- 同一详情 URL 跨 section 重复（理论不会，但保险）→ seen_urls + DB 主键双层

---

## 6. 结构化字段（extracted_json）

```json
{
  "section_name": "国家水利部-政策法规-法律 | -行政法规和法规性文件 | -部门规章 | -规范性文件",
  "topic_category": "政策法规",
  "issuing_authority": "中华人民共和国水利部",
  "content_source": "水利部网站",
  "column_name": "<meta ColumnName>",
  "doc_number": "<正则匹配的文号，如 水资管〔2026〕46号；无则空>",
  "publish_date": "YYYY-MM-DD",
  "source_url": "<详情页绝对 URL>",
  "site_id": "mwr_zcfg",
  "_category": "policy"
}
```

`collection_policy_ext` 由 CollectionWriter 按 category=policy 自动建行：
- `doc_number` ← extracted_json.doc_number
- `issuing_authority` ← extracted_json.issuing_authority
- `topic_category` ← 政策法规
- `status` 默认 "有效"

---

## 7. 去重

`crawler_result.id = md5("mwr_zcfg|" + source_url_normalized)`

`source_url_normalized`：
- urljoin 成绝对 URL
- 去 fragment（`#xxx`）
- 去无用查询参数（页面是静态 `.html`，正常无 query）
- 不强制小写 host（站点已统一小写）

**双层去重**：
- 运行内：`seen_urls` 集合，跨 4 section 跳重复
- 跨运行：DB 主键 upsert，同 URL 重复触发只更新 `last_seen_at`，不产生重复行

---

## 8. 反爬与稳定性

| 风险 | 对策 |
|------|------|
| 偶发 503/超时 | `max_retries=3` + 2s/3s 退避 |
| IP 限速 | delay 0.8–2.0s + 真实 Chrome UA + Referer（列表页 URL） |
| 详情页 301 跳转 | urllib 默认跟随重定向；最终 URL 记入 source_url（用户点原文链接不会 404） |
| gzip 编码 | 自动解压 |
| 编码 | UTF-8 优先，GBK 兜底 |
| 新闻发布会迷你站结构异常 | 正文回退到 `div.newsfbh1art` / `<title>` / 列表标题；不抛错 |
| 某子列表临时 503 | 该 section 跳过记错，不中断后续 section |
| 4 列表间 href 重复 | seen_urls 去重 |

---

## 9. YAML 配置（追加 mwr_zcfg）

```yaml
  mwr_zcfg:
    name: "国家水利部-政策法规"
    site_url: "http://www.mwr.gov.cn"
    category: policy
    enabled: true
    detect_enabled: true
    detect_interval: 3600
    detect_quiet_hours: "22-6"
    custom_runner: "rag.svr.mwr_zcfg_crawler"
    transport:
      type: rest_api
      engine: urllib
      headers:
        User-Agent: "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
        Accept-Language: "zh-CN,zh;q=0.9"
        Referer: "http://www.mwr.gov.cn/zw/zcfg/"
      verify_ssl: false
      timeout: 30
    listing:
      urls:
        - "http://www.mwr.gov.cn/zw/zcfg/fl/"
        - "http://www.mwr.gov.cn/zw/zcfg/xzfghfgxwj/"
        - "http://www.mwr.gov.cn/zw/zcfg/bmgz/"
        - "http://www.mwr.gov.cn/zw/zcfg/gfxwj/"
      method: GET
    pagination:
      type: single_page
    extract:
      type: css_selector
      items_path: "ul.slnewsconlist li"
      fields:
        title: "a"
        url: "a@href"
        date: "span"
    detail:
      type: none
    anti_crawler:
      delay_min: 0.8
      delay_max: 2.0
      max_retries: 3
      empty_page_stop: 3
    format:
      parser_id: "general"
      upload_batch_size: 5
```

`extract` 配置真实可工作 — 探测器依赖它抓第 1 页算签名（探测器**不走** custom_runner）。

---

## 10. date_filter 语义

| 场景 | date_filter | 行为 |
|------|------------|------|
| 首次回填（手动 CLI） | `""` | 4 列表全量入库（~77 条） |
| 探测器触发 | `today` | 大多数日 0 条；有新发布时 1–N 条 |
| 指定日补采 | `YYYY-MM-DD` | 仅该日发布（按 `<meta PubDate>` 过滤） |

过滤逻辑：列表 `<span>` 日期 → 过滤；进入详情后再用 `<meta PubDate>` 校准（详情页日期更准）。

---

## 11. 任务列表与探测器

1. INSERT 一行 `crawler_task`：site_id=`mwr_zcfg`、kb_id=`3b4f619c85c211f198269135a1db216c`、enabled=true、cron 留空（探测器驱动）。
2. YAML `detect_enabled: true / detect_interval: 3600 / detect_quiet_hours: "22-6"` — 探测器每 3600s 扫一次签名，变更即入队。
3. 探测器元任务由 `crawler_detector.py` 60s 周期触发（已有全局机制，无需新建）。
4. 采集结果页 → 探测监控 Tab 自动出现 `mwr_zcfg` 行（前端按 crawler_task.enabled 渲染）。

---

## 12. 采集结果列表"类型"列显示

- 落库：`crawler_result.extracted_json.section_name` ∈ 4 值之一
- 前端：按 `extracted_json.section_name || category_label` 渲染 → 列显示对应中文后缀

---

## 13. 详情页"原文地址"完整性

- `source_url` = urllib 跟随重定向后的最终 URL（去 fragment）。
- 不做模板拼接（避免拼错 ID 导致 404）。
- 抽 5 条 source_url 用 curl 验证全部 200。

---

## 14. 部署与验收

**改动仅 2 文件，均在 rag/ bind-mount 热更新、子进程加载 → 无需重启 Docker**：
1. `rag/svr/crawler_sites.yaml`（追加 mwr_zcfg）
2. `rag/svr/mwr_zcfg_crawler.py`（新建）

**步骤**：
1. SCP 两个文件到服务器 `/home/bid-agent-konus/ragflow2/rag/svr/`
2. 冒烟测试：
   ```bash
   docker exec docker-ragflow-cpu-1 python -c "from rag.svr.mwr_zcfg_crawler import run; print('OK')"
   docker exec docker-ragflow-cpu-1 python rag/svr/validate_all_crawlers.py
   ```
3. 小样本端到端（先全量抓首跑）：
   ```bash
   docker exec docker-ragflow-cpu-1 python rag/svr/unified_crawler.py \
     --tenant-id 7ab771d4dec84f23b2c1fb5f4e453ff9 \
     --kb-id 3b4f619c85c211f198269135a1db216c \
     --task-name first_backfill_mwr_zcfg \
     --writer collection --category policy \
     --script-args '{"site_id":"mwr_zcfg"}'
   ```
4. INSERT `crawler_task` 行（utf8mb4 charset）。
5. 跑一轮探测器元任务，验证 mwr_zcfg 被纳管。
6. 等用户验证数据完整性。

**验收 9 项**：
- [ ] `crawler_result` 行数 ≈ 77（fl~5 + xzfghfgxwj~23 + bmgz~24 + gfxwj~25）
- [ ] 每行 `title / publish_date / source_url` 非空
- [ ] `extracted_json.section_name` 100% 命中 4 值之一
- [ ] `collection_policy_ext` 关联行数 = `crawler_result` 行数
- [ ] KB 文档数增量 ≥ DB 行数 × 80%
- [ ] KB 中 ZIP 附件已解压（若有）
- [ ] 抽 5 条 source_url 全部 HTTP 200
- [ ] 重跑脚本 `items_new=0`（幂等）
- [ ] 探测监控 Tab 出现 `mwr_zcfg` 行

**回滚**：YAML 设新站 enabled=false / 删 crawler_task 行；脚本保留无副作用。

---

## 15. 对抗性测试用例（per CLAUDE.md）

| 用例 | 预期防御 |
|------|---------|
| href 是 `javascript:void(0)` 或 `mailto:` | 跳过，不入 item |
| href 是相对 `./xxx.html` 与绝对 `http://...` 混合 | urljoin 统一绝对化 |
| 某子列表 503 / 超时 | 跳过该 section，记错，继续后续 section |
| 详情页 404 / 超时 | 记错、上传 metadata-only markdown，不中断后续 |
| 详情页 meta 缺失 / 正文容器缺失 | 回退 `<title>`、`<div.newscontain>` 文本 |
| 文件名含 `..` 或特殊字符 | 落地前 sanitize（AttachmentHandler 已做） |
| ZIP 解压超过 `_MAX_ZIP_DEPTH` 或包含恶意路径穿越 | AttachmentHandler 已防 |
| 同一详情 URL 跨 section 重复（理论不会） | seen_urls + DB upsert 双层去重 |
| 4 列表全空 / 站点临时 503 | return success + 0 items，不报错 |
| 反复触发同一天 | DB 层 upsert 幂等，items_new=0 |
