# 标讯工具调用指南 (LLM Agent)

> 详细参数说明在各工具类的 `meta.description` 中，本文档仅提供调用流程和工具选择指引。

## 工具速查表

| 工具名 | 用途 | 一句话触发 |
|--------|------|-----------|
| `lookup_bid_code` | 地名/行业名→代码 | "广东" → `44`, "建筑" → `E` |
| `bid_search` | 搜索招标/采购/中标项目 | 查项目列表 |
| `bid_get_detail` | 项目详情 + 自动导入知识库 | 深入看某个项目 |
| `bid_search_contract` | 搜索合同/中标结果 | 查合同、中标 |
| `bid_get_contract_detail` | 合同正文 + 结构化数据 | 看合同详情 |
| `bid_search_ai` | AI轻量搜索(带参与方信息) | 快速了解谁中标 |
| `bid_enterprise_profile` | 企业画像(基本信息+投标统计) | 查某家公司 |
| `bid_enterprise_contacts` | 企业联系人 | 找联系方式 |
| `bid_enterprise_customers` | 企业客户列表 | 这家公司服务过谁 |
| `bid_enterprise_suppliers` | 企业供应商列表 | 这家公司采购过谁 |
| `bid_rewrite_query` | 自然语言→结构化搜索条件 | 复杂条件提取 |
| `bid_industry_tag` | 关键词→行业编码 | 确定行业分类 |
| `bid_get_source` | 原始来源网址 | 看官网公告 |
| `bid_construction_search` | 拟在建项目 | 查规划中的项目 |
| `bid_import_to_kb` | 手动导入知识库 | 单独触发导入 |
| `bid_check_import_status` | 查询导入进度 | 轮询解析状态 |

## 典型调用流程

### 场景1: 用户想搜索标讯
```
用户: "广州有什么医疗设备的招标？"
  → lookup_bid_code("广州", "area")     → code: "440100"
  → lookup_bid_code("医疗设备", "industry") → code: "C358"
  → bid_search(keyword="医疗设备", city_code="440100", industry_code="C358")
```

### 场景2: 用户想看项目详情并分析
```
用户: "帮我看一下第3个项目"
  → bid_get_detail(project_id=xxx, publish_time="2026-06-01")  ← 一步到位(详情+自动导入KB)
  → 等待 bid_check_import_status(project_id=xxx) → "done"
  → 基于KB内容回答用户问题
```

### 场景3: 用户想查合同/中标
```
用户: "广东省安防系统的中标合同"
  → lookup_bid_code("广东", "area") → "44"
  → bid_search_contract(keyword="安防", provice_code="44") → 合同列表
  → bid_get_contract_detail(project_id=xxx, publish_time="xxx") → 合同正文
```

### 场景4: 用户想了解某家企业
```
用户: "查一下海康威视"
  → bid_enterprise_profile("海康威视") → 基本信息 + 投标统计 + 关系概览
  → bid_enterprise_contacts("海康威视") → 联系人列表
  → bid_enterprise_customers("海康威视") → 客户/中标项目
  → bid_enterprise_suppliers("海康威视") → 供应商/采购项目
```

## 关键规则

1. **搜索前先查代码**: 用地名/行业名搜索时，先用 `lookup_bid_code` 转换，不要自己猜编码
2. **详情即导入**: `bid_get_detail` 会自动触发知识库导入，返回 `kb_import.status` 为 "parsing" 表示后台处理中
3. **保存 id + publish_time**: 搜索结果中的 `id` 和 `publish_time` 是后续查详情/导KB的唯一凭据
4. **缓存透明**: 工具自动处理缓存，`from_cache: true` 表示免费命中，`stale: true` 表示API故障降级
5. **企业钻取分层**: `profile`(概览) → `contacts`(联系人) → `customers`/`suppliers`(项目列表)，按需逐层深入
