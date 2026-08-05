# 水利部公报采集脚本 — 设计文档

> **日期**：2026-08-04
> **分支**：`feat/unified-crawler-framework`
> **目标站**：`http://www.mwr.gov.cn/zw/slbgb/`（中华人民共和国水利部 — 水利部公报）
> **KB ID**：`3b4f619c85c211f198269135a1db216c`
> **site_id**：`mwr_slbgb`
> **类型**：`other`（公报不属于 policy/bid/personnel/news）

---

## 1. 站点特性

| 维度 | 描述 |
|------|------|
| 渲染 | 静态 HTML，jQuery 仅用于导航菜单，无 SPA/加密/token |
| 列表结构 | `ul.slnewsconlist > li`，每个 `<li>` 含 `<span>日期</span><a href="PDF">标题</a>` |
| 详情 | **无 HTML 详情页**，`<a href>` 直指 PDF 文件 |
| 发布频率 | 季刊式，~5 期/年 |
| 反爬等级 | 🟢 一级（开放 HTTP，无 Cloudflare/Cookie/Referer 强校验） |
| 编码 | UTF-8 |
| 分页 | 共 3 页，但**只抓第 1 页**（用户要求） |

---

## 2. 技术选型

采用 **custom_runner 模式**（非纯 YAML），理由：

1. PDF 既是详情又是附件 — 需手工组装 `item.attachments`，storage_pipeline 才会触发 PDF 下载与 KB 上传
2. YAML extractor 不支持 regex 解析期号（issue_year/issue_no/total_no）
3. 探测器仍读 YAML `listing + extract`（css_selector）算签名，与 runner 解耦

参考范本：`rag/svr/ningde_gcjs_collection_crawler.py`。

---

## 3. 文件清单

| 类型 | 路径 |
|------|------|
| 新建 | `rag/svr/mwr_slbgb_crawler.py` |
| 追加 | `rag/svr/crawler_sites.yaml`（`mwr_slbgb` 站点块） |
| 存档 | `docs/superpowers/specs/2026-08-04-mwr-slbgb-design.md`（本文件） |

不动：`db_models.py`、`collection_app.py`、`crawler_engine/*.py`、前端代码。

---

## 4. 数据流

```
unified_crawler.py
  └─ 读 YAML mwr_slbgb，发现 custom_runner: "rag.svr.mwr_slbgb_crawler"
       └─ mod.run(..., category="other", date_filter, ...)
            ├─ GET http://www.mwr.gov.cn/zw/slbgb/  → BS4 parse → 25 items
            ├─ date_filter 过滤
            └─ 每条 item:
                 ├─ regex 解析期号
                 ├─ CollectionWriter.write_all → upsert crawler_result
                 ├─ 下载 PDF → 若 zip 解压 → KBUploader.upload_file
                 └─ StoragePipeline.store → markdown 上传 KB
```

---

## 5. 结构化字段

写入 `crawler_result.extracted_json`：
```json
{
  "issue_year": 2026,
  "issue_no": 1,
  "total_no": 75,
  "issuing_authority": "中华人民共和国水利部",
  "topic_category": "水利部公报",
  "section_name": "国家水利部-水利部公报",
  "site_id": "mwr_slbgb",
  "_category": "other"
}
```

---

## 6. 去重

`crawler_result.id = md5("mwr_slbgb|" + pdf_abs_url)`，同 URL 永远映射到同一主键，upsert 幂等。

---

## 7. 反爬与稳定性

| 风险 | 对策 |
|------|------|
| 偶发 503/超时 | `max_retries=3` + 2s/3s 退避 |
| IP 封禁 | delay 0.8–2.0s + 真实 UA |
| 重定向到错误页 | `urljoin` 绝对化 + PDF 魔数 `%PDF-` 校验 |
| 实为 zip 伪装 | `PK\x03\x04` 魔数检测 → 解压 |
| gzip 编码 | `Content-Encoding: gzip` 自动解压 |

---

## 8. 探测器

YAML `detect_enabled: true / detect_interval: 3600 / detect_quiet_hours: "22-6"`。

签名基于 PDF URL（稳定且每期唯一）。新期发布 → 签名变 → 入队 `unified_crawler --writer collection --category other --date-filter today`。

---

## 9. date_filter 语义

| 场景 | date_filter | 行为 |
|------|------------|------|
| 首次回填（CLI 手动） | `""` | 抓第 1 页全部 ~25 期 |
| 探测器触发 | `today` | 大多数日 0 条；发布日 1 条 |
| 指定日补采 | `YYYY-MM-DD` | 仅该日发布的期 |

---

## 10. 验证清单

- [ ] `docker exec ... python -c "from rag.svr.mwr_slbgb_crawler import run"` 通过
- [ ] 单站测试：`unified_crawler --writer collection --category other --script-args '{"site_id":"mwr_slbgb"}'`
- [ ] DB: `SELECT COUNT(*) FROM crawler_result WHERE site_id='mwr_slbgb'` ≈ 25
- [ ] KB: PDF 文档数 + 25
- [ ] extracted_json 含 issue_year / total_no
- [ ] 采集任务列表出现 "国家水利部-水利部公报"
- [ ] 探测 Tab 出现该站点行
