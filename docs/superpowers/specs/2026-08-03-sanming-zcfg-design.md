# 三明市公共资源交易网 · 政策法规智能采集 — 设计文档

- **日期**: 2026-08-03
- **系统**: 智能采集系统（System C，`unified_crawler.py` + YAML → `crawler_result` + 扩展表）
- **目标页**: `https://smggzy.sm.gov.cn/smwz/zcfg/`（Epoint WebBuilder CMS，SSR 渲染）
- **KB**: `3b4f619c85c211f198269135a1db216c`（tenant `7ab771d4dec84f23b2c1fb5f4e453ff9`）

## 1. 需求与决策记录

| # | 需求 | 决策 |
|---|------|------|
| 1 | 满足智能采集系统设计.md（System C） | custom_runner 模式，非旧 A/B 系统 |
| 2 | 目标页 `/smwz/zcfg/` | SSR 站点，rest_api 适配器即可 |
| 4 | 6 页签：综合类/工程建设/政府采购/土地矿产/产权交易/其他 | CategoryNum 003001-003006；首次全量（约116条）、后续仅当天（探测器注入 date_filter=today） |
| 5 | 重复数据去重 | `crawler_result.id = md5(site_id\|source_url)` 主键 upsert |
| 6 | 结构化解析入扩展表 | category=policy → `collection_policy_ext` |
| 9 | 标题/正文/附件/ZIP 全入库+上传KB解析 | 脚本内附件双通道识别 + zipfile 解压 + 逐文件上传 KB |
| 10 | 类型列=【三明市-政策法规】 | `extracted_json.section_name="三明市-政策法规"`（前端 `section_name \|\| category_label` 机制）；页签名存 `extracted_json.tab_name` 次级字段 |
| 11 | 建 crawler_task 行 | INSERT（utf8mb4），kb_id 绑定目标 KB |
| 12 | 接入探测监控 | YAML `detect_enabled: true, detect_interval: 600` + crawler_task.enabled=true（探测器唯一数据源） |
| 13 | 原文地址完整有效 | 直接使用列表页 href 规整为绝对 URL（实测 200），不做模板拼接 |
| 14 | 反爬检查 | 实测弱反爬：UA+Referer、verify_ssl=false、0.8-2.0s 延迟、会话预热、3 次重试 |

**site_id 决策**：新建 `smggzy_zcfg`；旧 `smzcfg`（bid 模式、仅 3 页签）标记 disabled，不删除。
**调度决策**：无独立 cron。采集频率由探测监控驱动——探测器每 detect_interval（600s 基础值，无变更时指数退避拉长）探测列表第 1 页签名，变更则自动入队采集（date_filter=today）。

## 2. 线上实测结论（2026-08-03）

| 页签 | CategoryNum | 条数 | 页数 |
|---|---|---|---|
| 综合类 | 003001 | 25 | 2 |
| 工程建设 | 003002 | 20 | 1 |
| 政府采购 | 003003 | 21 | 2 |
| 土地矿产 | 003004 | 17 | 1 |
| 产权交易 | 003005 | 18 | 1 |
| 其他 | 003006 | 15 | 1 |

- 列表 URL 必须带尾斜杠（无斜杠 301）；分页 `?pageing={N}`，实测第 2 页无重叠
- `totalPageNums` JS 变量仅多页页签存在；缺失=单页
- 列表项结构：`ul.ewb-notice-items > li`（`a.l` 标题+链接、`span.r.ewb-ndate` 日期）
- 详情页 `/smwz/InfoDetail/?InfoID={UUID}&CategoryNum={code}`：
  - 标题 `.ewb-show-title`；日期 `【信息时间： YYYY-MM-DD`；正文 `div.ewb-show-con#mainContent`
  - 部分含文号（如 `发改法规〔2026〕195号`）；抽查 6 条未见附件，但历史确认存在 bqpoint 网关附件，附件逻辑保留
- 反爬：本地 curl 直接 200，无验证码/加密/IP 拦截

## 3. 架构

```
crawler_task 表新增 1 行 (site_id=smggzy_zcfg, kb_id=3b4f619c..., enabled=true)
 ├─ 采集执行: unified_crawler.py → YAML custom_runner → rag/svr/smggzy_zcfg_crawler.py::run()
 │    首次=全量(6页签全翻页) / 后续=date_filter当天
 │    → CollectionWriter(category=policy): crawler_result + collection_policy_ext + KB上传
 └─ 探测监控: crawler_detector.py(60s元任务) 读 YAML listing 抓各页签第1页算签名
      变更→入队采集(date_filter=today)；detect_interval=600s+指数退避
旧站 smzcfg → disabled
```

## 4. YAML 配置（新增 smggzy_zcfg）

```yaml
  smggzy_zcfg:
    name: "三明市公共资源交易中心-政策法规"
    site_url: "https://smggzy.sm.gov.cn"
    category: policy
    enabled: true
    detect_enabled: true
    detect_interval: 600
    custom_runner: "rag.svr.smggzy_zcfg_crawler"
    transport:
      type: rest_api
      verify_ssl: false
      timeout: 30
      headers:
        User-Agent: "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
        Referer: "https://smggzy.sm.gov.cn/smwz/zcfg/"
    listing:
      url: "https://smggzy.sm.gov.cn/smwz/zcfg/003001/"
      method: GET
    sections:
      zhfl:  { name: "综合类",   listing: { url: "https://smggzy.sm.gov.cn/smwz/zcfg/003001/" } }
      gcjs:  { name: "工程建设", listing: { url: "https://smggzy.sm.gov.cn/smwz/zcfg/003002/" } }
      zfcg:  { name: "政府采购", listing: { url: "https://smggzy.sm.gov.cn/smwz/zcfg/003003/" } }
      tdkc:  { name: "土地矿产", listing: { url: "https://smggzy.sm.gov.cn/smwz/zcfg/003004/" } }
      cqjy:  { name: "产权交易", listing: { url: "https://smggzy.sm.gov.cn/smwz/zcfg/003005/" } }
      other: { name: "其他",     listing: { url: "https://smggzy.sm.gov.cn/smwz/zcfg/003006/" } }
    pagination:
      type: html_regex
      page_pattern: "?pageing={}"
      total_regex: "var\\s+totalPageNums\\s*=\\s*(\\d+)"
      max_pages: 50
    extract:
      type: css_selector
      items_path: "ul.ewb-notice-items li"
      fields:
        title: "a"
        url: "a@href"
        date: "span.ewb-ndate"
        id: "a@href"
    anti_crawler:
      delay_min: 0.8
      delay_max: 2.0
      max_retries: 3
      empty_page_stop: 2
```

extract 配置真实可工作——探测器依赖它抓第 1 页算签名（探测器不走 custom_runner）。

## 5. custom_runner 脚本（rag/svr/smggzy_zcfg_crawler.py）

模板 = `zhangzhou_zwgk_crawler.py`；逆向成果复用自废弃的 `smzcfg_crawler.py`。

```
run(tenant_id, kb_id, task_name, task_id, writer_mode, category,
    date_filter, full_crawl, force_run, site_config, output_dir) -> summary
  · date_filter "today" → ISO；非 full_crawl 且空 → 默认 today

crawl():
 1. _init_session(): Chrome 全头 + 预热 GET 站点根
 2. 每页签: 第1页SSR → totalPageNums(缺省1) → ?pageing=N 翻页
    条目: InfoID/CategoryNum/title/list_date；增量模式仅留 list_date==date_filter
    运行内 seen_urls 去重；连续2空页停止；延迟0.8~2.0s
 3. 详情: 原文URL=列表href规整绝对URL（含InfoID+CategoryNum双参数）
    标题多级回退(.ewb-show-title→信息时间行上溯→面包屑→h1/h2)
    信息时间正则；正文 #mainContent→markdown
    文号正则 [\u4e00-\u9fa5]{2,10}[〔\[（]\d{4}[〕\]）]\d+号
    发文机关=标题前缀启发式
    附件双通道: ①href含 download.bqpoint.com/downloaddetail ②直链文件后缀
 4. 附件下载(同域session/外部urllib) → ZIP zipfile.BytesIO 解压
    (≤200成员/≤100MB/加密包跳过记错) → pdf/docx/xlsx 文本提取 → 全部上传KB
 5. CollectionWriter.write_all(item, site_id, category="policy", task_id,
      site_display="三明市公共资源交易中心-政策法规 smggzy.sm.gov.cn")
 6. KB 上传: md文档+附件+ZIP成员，文件名去重，FileService.upload_document+queue_tasks
 7. max_runtime≈50min 安全收尾
```

### 数据落库映射

| 落点 | 值 |
|---|---|
| crawler_result.category | policy |
| crawler_result.site_display | 三明市公共资源交易中心-政策法规 smggzy.sm.gov.cn |
| crawler_result.title / publish_date / source_url | 详情标题 / 信息时间(回退列表日期) / 完整详情URL |
| extracted_json.section_name | **三明市-政策法规**（类型列显示） |
| extracted_json.tab_name | 页签中文名（次级字段） |
| extracted_json.wenhao / fawenjiguan | 文号正则 / 标题启发式 |
| collection_policy_ext | doc_number←wenhao，issuing_authority←fawenjiguan，topic_category←页签名，status默认"有效" |
| crawler_result.attachments | [{file_name,file_url,kb_doc_id,status}] 含ZIP成员 |

## 6. 部署与验收

**改动仅 2 文件，均在 rag/ bind-mount 热更新、子进程加载 → 无需重启 Docker**：
1. `rag/svr/crawler_sites.yaml`（新增站点 + 旧站 disabled）
2. `rag/svr/smggzy_zcfg_crawler.py`（新建）

**步骤**：SCP 上传 → 冒烟（import + ConfigLoader + `--date-filter 2026-02-11` 小样本端到端）→ INSERT crawler_task（utf8mb4）→ trigger_task 触发首次全量 → 手动跑一轮 crawler_detector 验证纳管 → 检查旧 smzcfg 的 crawler_task 行并禁用。

**验收 9 项**：总条数≈116 / 6页签全覆盖 / markdown 无空 / 类型列=三明市-政策法规 / 扩展表同数 / KB 增量≥80%且解析正常 / 抽5条 source_url 全 200 / 重跑 items_new=0 / 探测监控面板可见。

**回滚**：YAML 恢复旧站 enabled、新站 disabled，删 crawler_task 行；脚本保留无副作用。
