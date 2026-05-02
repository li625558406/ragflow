# 文档智能分析功能设计

## 1. 概述

### 1.1 背景
用户上传招标文件等文档到 RAGFlow 知识库后，需要 AI 自动分析文档内容，分段输出重点内容和注意点。

### 1.2 目标
- 支持不同文档类型（招标文件、合同、法律文书等）使用不同分析模板
- 按章节/段落合并分析，输出结构化结果
- 前端页面分段展示分析结果

### 1.3 用户场景
1. 用户在知识库文档列表点击「分析」按钮
2. 选择分析模板（或使用默认模板）
3. 系统自动合并章节、调用 LLM 分析
4. 前端展示分段分析结果（关键条款、时间节点、风险提示、商务条件）

---

## 2. 数据模型

### 2.1 文档分析模板表 `DocumentAnalysisTemplate`

| 字段 | 类型 | 说明 |
|------|------|------|
| id | VARCHAR(32) PK | 主键 |
| name | VARCHAR(255) | 模板名称 |
| doc_type | VARCHAR(64) | 文档类型：bid/contract/law/general |
| description | TEXT | 模板说明 |
| dimensions | JSON | 分析维度配置 |
| prompt_templates | JSON | Prompt模板（按维度） |
| chunk_merge_rule | JSON | 章节合并规则 |
| is_default | BOOLEAN | 是否为该类型默认模板 |
| is_system | BOOLEAN | 系统预置模板不可删除 |
| tenant_id | VARCHAR(32) | 租户ID（空=全局模板） |

### 2.2 文档分析结果表 `DocumentAnalysisResult`

| 字段 | 类型 | 说明 |
|------|------|------|
| id | VARCHAR(32) PK | 主键 |
| document_id | VARCHAR(32) | 文档ID |
| template_id | VARCHAR(32) | 模板ID |
| status | VARCHAR(16) | 状态：pending/running/completed/failed |
| progress | INT | 进度 0-100 |
| result | JSON | 分析结果（按章节） |
| error_message | TEXT | 错误信息 |
| doc_name | VARCHAR(255) | 文档名称 |
| kb_id | VARCHAR(32) | 知识库ID |
| tenant_id | VARCHAR(32) | 租户ID |
| llm_id | VARCHAR(64) | 使用的模型 |

---

## 3. API 接口

### 3.1 分析模板管理

```
GET    /api/v1/analysis-templates              # 获取模板列表
GET    /api/v1/analysis-templates/{id}         # 获取模板详情
POST   /api/v1/analysis-templates              # 创建自定义模板
PUT    /api/v1/analysis-templates/{id}         # 更新模板
DELETE /api/v1/analysis-templates/{id}         # 删除模板
```

### 3.2 文档分析

```
POST   /api/v1/documents/{id}/analyze          # 触发分析
GET    /api/v1/documents/{id}/analysis         # 获取分析结果
DELETE /api/v1/documents/{id}/analysis         # 删除分析结果
GET    /api/v1/documents/{id}/analysis/stream  # SSE流式输出（可选）
```

### 3.3 分析结果结构

```json
{
  "status": "completed",
  "progress": 100,
  "template_name": "招标文件分析",
  "sections": [
    {
      "section_id": "sec_001",
      "section_name": "第一章 招标公告",
      "page_range": [1, 3],
      "analysis": {
        "key_points": {"label": "关键条款摘要", "content": "..."},
        "time_nodes": {"label": "时间节点提醒", "content": "..."},
        "risks": {"label": "风险/注意事项", "content": "..."},
        "commercial": {"label": "商务条件分析", "content": "..."}
      }
    }
  ]
}
```

---

## 4. 核心逻辑

### 4.1 章节合并

1. 获取文档所有 chunks（按 position 排序）
2. 识别章节标题（正则匹配：第X章、第X节、一、二、三等）
3. 相邻 chunks 合并条件：
   - 同属一个章节标题下
   - 合并后字数 < max_chars（默认2000）
   - 或 chunks 数量 < max_chunks（默认10）
4. 特殊处理：表格不拆分，关键章节独立

### 4.2 LLM 调用

1. 构造 prompt = prompt_templates[dimension] + 章节内容
2. 调用租户配置的 LLM
3. 解析返回结果
4. 错误处理：超时重试

### 4.3 异步处理

1. 触发分析时创建记录（status=pending）
2. 后台线程执行分析
3. 更新进度和结果
4. 前端轮询或 SSE 获取进度

---

## 5. 前端交互

### 5.1 入口
知识库详情页 → 文档列表 → 「分析」按钮

### 5.2 分析结果页
- 左侧：章节目录
- 右侧：Tab 切换维度，Markdown 渲染内容
- 状态：进度条、错误提示、重试按钮

---

## 6. 文件结构

```
api/
├── apps/restful_apis/
│   ├── analysis_template_api.py
│   └── document_analysis_api.py
├── db/
│   ├── db_models.py                    # 新增 Model
│   └── services/
│       ├── analysis_template_service.py
│       └── document_analysis_service.py
├── lib/analysis/
│   ├── chunk_merger.py
│   ├── section_analyzer.py
│   └── prompts/
│       ├── bid_analysis.yaml
│       ├── contract_analysis.yaml
│       └── law_analysis.yaml

web/src/
├── pages/document-analysis/
│   ├── index.tsx
│   └── components/
│       ├── SectionNav.tsx
│       └── AnalysisContent.tsx

docker/
├── init.sql                             # 新增预置模板数据
```

---

## 7. 数据库初始化

修改 `docker/init.sql` 添加预置模板：

```sql
-- 招标文件分析模板
INSERT INTO document_analysis_template
(id, name, doc_type, description, dimensions, prompt_templates, chunk_merge_rule, is_default, is_system)
VALUES
('tpl_bid_default', '招标文件分析', 'bid', '适用于招标文件、投标文件分析',
 '["关键条款摘要", "时间节点提醒", "风险/注意事项", "商务条件分析"]',
 '{"key_points": "请提取以下招标文件章节的关键条款摘要...", "time_nodes": "请提取以下招标文件章节的时间节点...", "risks": "请分析以下招标文件章节的风险点和注意事项...", "commercial": "请分析以下招标文件章节的商务条件..."}',
 '{"max_chars": 2000, "max_chunks": 10}',
 true, true);

-- 合同分析模板
INSERT INTO document_analysis_template
(id, name, doc_type, description, dimensions, prompt_templates, chunk_merge_rule, is_default, is_system)
VALUES
('tpl_contract_default', '合同分析', 'contract', '适用于合同文书分析',
 '["核心条款摘要", "风险/注意事项", "权利义务分析"]',
 '{"key_points": "请提取以下合同章节的核心条款...", "risks": "请分析以下合同章节的风险点...", "rights": "请分析以下合同章节中甲乙双方的权利义务..."}',
 '{"max_chars": 2000, "max_chunks": 10}',
 true, true);

-- 法律文书分析模板
INSERT INTO document_analysis_template
(id, name, doc_type, description, dimensions, prompt_templates, chunk_merge_rule, is_default, is_system)
VALUES
('tpl_law_default', '法律文书分析', 'law', '适用于法律法规、法律文书分析',
 '["核心条款摘要", "适用范围", "风险提示"]',
 '{"key_points": "请提取以下法律文书的核心条款...", "scope": "请分析以下法律文书的适用范围...", "risks": "请提示以下法律文书的风险注意点..."}',
 '{"max_chars": 2000, "max_chunks": 10}',
 true, true);
```

---

## 8. 实现优先级

1. **P0 - MVP**
   - 数据模型 + 预置模板
   - 文档分析 API（触发 + 获取结果）
   - 后端分析逻辑（章节合并 + LLM调用）
   - 前端基础展示页

2. **P1 - 增强**
   - 自定义模板管理
   - SSE 流式输出
   - 错误重试机制

3. **P2 - 优化**
   - 分析结果缓存
   - 批量分析
   - 导出分析报告
