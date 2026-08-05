# 宁德市公共资源交易中心-工程建设 智能采集设计

> 日期: 2026-07-31 | 站点: `ningde_gcjs` | KB: `3b4f619c85c211f198269135a1db216c`

## 1. 背景

将宁德市公共资源交易中心工程建设栏目从老脚本体系(`ningde_gcjs_crawler.py` → 本地文件+直接KB)迁移到新智能采集系统(`crawler_result` + `CollectionWriter`)。

**站点特征**: Epoint WebBuilder 平台, OAuth2 匿名 token, 多阶段详情 API, 附件下载网关, ZIP 压缩包。反爬等级: 🟢 一级(开放 JSON API)。

## 2. 方案: 独立采集脚本 + YAML 探测

基于老脚本改造, 输出层替换为 `CollectionWriter`, YAML 管探测监控+任务管理。

### 2.1 文件变更

| 类型 | 文件 | 改动 |
|------|------|------|
| 新建 | `rag/svr/ningde_gcjs_collection.py` | 独立采集脚本 |
| 修改 | `rag/svr/crawler_sites.yaml` | `ningde_gcjs` 加 category/writer/script/detect_enabled |
| 修改 | `rag/svr/crawler_engine/config.py` | SiteConfig 加 writer/script 字段 |
| 修改 | `api/apps/restful_apis/crawl4ai_app.py` | trigger 路由支持 script 模式 |
| DB | `crawler_task` | INSERT 任务记录 |

### 2.2 Trigger 路由 (新增 script 分支)

```
YAML site + script=xxx.py → subprocess: python /ragflow/rag/svr/xxx.py
YAML site + writer=collection (无 script) → unified_crawler --writer collection
YAML site + category=bid (无 writer) → unified_crawler --writer bid (不变)
非 YAML → crawl4ai_executor (不变)
```

### 2.3 脚本数据流

```
OAuth2 token → 列表 API (当天, 分页)
  → 逐条: 详情 API (getDetails, Bearer) → N 个阶段
    → GET visiturl → 正文 HTML + 附件链接
      → 附件下载 → ZIP 解压
        → CollectionWriter.write_item() → crawler_result
        → KB 上传 (markdown + 附件) → 解析
```

### 2.4 去重

`crawler_result.id = md5(site_id|source_url)` 主键 upsert, 天然幂等。不需要文件状态。

### 2.5 结构化字段

每条结果写入 `extracted_json`:
- `category_name`: 公告子类别 (招标公告/中标结果/合同签订等)
- `project_type`: 项目类型 (xmlx)
- `bid_type`: 招标类型 (zblx)
- `stages`: 阶段列表 [{categoryname, title, infodate}]
- `attachment_count`: 附件数

### 2.6 YAML 配置

```yaml
ningde_gcjs:
  name: "宁德市公共资源交易中心-工程建设"
  site_url: "https://ggzyjy.xgw.ningde.gov.cn/"
  category: bid
  writer: collection
  script: ningde_gcjs_collection.py
  enabled: true
  detect_enabled: true
  # transport/listing/extract/detail 保持不变
```

### 2.7 原文地址

`https://ggzyjy.xgw.ningde.gov.cn/projectDetail.html?categorynum={categorynum}&infoid={infoid}`

### 2.8 类型标签

- `crawler_result.category` = `bid`
- `crawler_result.site_display` = `宁德市-工程建设`
- YAML `name` = `宁德市公共资源交易中心-工程建设` → API 自动派生 `site_name`

## 3. 部署清单

SCP: ningde_gcjs_collection.py + crawler_sites.yaml + config.py + crawl4ai_app.py
冒烟: 容器内 import 验证
测试: CLI 单站 → DB 行数 → KB 上传数
任务: crawler_task INSERT (utf8mb4)
探测: detect_enabled: true 自动生效
