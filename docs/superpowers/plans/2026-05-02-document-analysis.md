# 文档智能分析功能实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现文档智能分析功能，支持招标文件、合同、法律文书等不同文档类型的分段分析。

**Architecture:** 后端使用 Peewee ORM 新增数据模型，Service 层封装业务逻辑，Quart API 暴露接口；核心逻辑包括章节合并和 LLM 调用；前端新增分析结果展示页。

**Tech Stack:** Python/Peewee/Quart, React/TypeScript, LLM API

---

## Task 1: 数据模型定义

**Files:**
- Modify: `api/db/db_models.py`

- [ ] **Step 1: 在 db_models.py 末尾添加 DocumentAnalysisTemplate 模型**

在文件末尾（`class Meta` 之后）添加：

```python
class DocumentAnalysisTemplate(DataBaseModel):
    id = CharField(max_length=32, primary_key=True)
    name = CharField(max_length=255, null=False, help_text="模板名称")
    doc_type = CharField(max_length=64, null=False, index=True, help_text="文档类型: bid/contract/law/general")
    description = TextField(null=True, help_text="模板说明")
    dimensions = JSONField(default=list, help_text="分析维度配置")
    prompt_templates = JSONField(default=dict, help_text="Prompt模板")
    chunk_merge_rule = JSONField(default=dict, help_text="章节合并规则")
    is_default = BooleanField(default=False, index=True, help_text="是否默认模板")
    is_system = BooleanField(default=False, index=True, help_text="是否系统模板")
    tenant_id = CharField(max_length=32, null=True, index=True, help_text="租户ID")

    class Meta:
        db_table = "document_analysis_template"
```

- [ ] **Step 2: 添加 DocumentAnalysisResult 模型**

紧接上一步，添加：

```python
class DocumentAnalysisResult(DataBaseModel):
    id = CharField(max_length=32, primary_key=True)
    document_id = CharField(max_length=32, null=False, index=True, help_text="文档ID")
    template_id = CharField(max_length=32, null=False, index=True, help_text="模板ID")
    status = CharField(max_length=16, null=False, default="pending", index=True, help_text="状态: pending/running/completed/failed")
    progress = IntegerField(default=0, help_text="进度 0-100")
    result = JSONField(default=list, help_text="分析结果")
    error_message = TextField(null=True, help_text="错误信息")
    doc_name = CharField(max_length=255, null=True, help_text="文档名称")
    kb_id = CharField(max_length=32, null=False, index=True, help_text="知识库ID")
    tenant_id = CharField(max_length=32, null=False, index=True, help_text="租户ID")
    llm_id = CharField(max_length=64, null=True, help_text="使用的模型ID")

    class Meta:
        db_table = "document_analysis_result"
```

- [ ] **Step 3: 验证模型定义无误**

启动后端服务，观察是否有语法错误：

```bash
cd D:/AI/ragflow2 && PYTHONPATH=D:/AI/ragflow2 .venv/Scripts/python.exe -c "from api.db.db_models import DocumentAnalysisTemplate, DocumentAnalysisResult; print('Models OK')"
```

Expected: `Models OK`

- [ ] **Step 4: 提交代码**

```bash
git add api/db/db_models.py
git commit -m "feat: 添加文档分析模板和结果数据模型

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

## Task 2: 数据库初始化脚本

**Files:**
- Modify: `docker/init.sql`

- [ ] **Step 1: 修改 init.sql 添加表创建语句**

在文件末尾添加：

```sql
-- 文档分析模板表
CREATE TABLE IF NOT EXISTS document_analysis_template (
    id VARCHAR(32) PRIMARY KEY,
    f_name VARCHAR(255) NOT NULL,
    f_doc_type VARCHAR(64) NOT NULL,
    f_description TEXT,
    f_dimensions JSON,
    f_prompt_templates JSON,
    f_chunk_merge_rule JSON,
    f_is_default TINYINT(1) DEFAULT 0,
    f_is_system TINYINT(1) DEFAULT 0,
    f_tenant_id VARCHAR(32),
    f_create_time BIGINT,
    f_create_date DATETIME,
    f_update_time BIGINT,
    f_update_date DATETIME,
    INDEX idx_doc_type (f_doc_type),
    INDEX idx_is_default (f_is_default),
    INDEX idx_tenant_id (f_tenant_id)
);

-- 文档分析结果表
CREATE TABLE IF NOT EXISTS document_analysis_result (
    id VARCHAR(32) PRIMARY KEY,
    f_document_id VARCHAR(32) NOT NULL,
    f_template_id VARCHAR(32) NOT NULL,
    f_status VARCHAR(16) NOT NULL DEFAULT 'pending',
    f_progress INT DEFAULT 0,
    f_result JSON,
    f_error_message TEXT,
    f_doc_name VARCHAR(255),
    f_kb_id VARCHAR(32) NOT NULL,
    f_tenant_id VARCHAR(32) NOT NULL,
    f_llm_id VARCHAR(64),
    f_create_time BIGINT,
    f_create_date DATETIME,
    f_update_time BIGINT,
    f_update_date DATETIME,
    INDEX idx_document_id (f_document_id),
    INDEX idx_template_id (f_template_id),
    INDEX idx_status (f_status),
    INDEX idx_kb_id (f_kb_id),
    INDEX idx_tenant_id (f_tenant_id)
);
```

- [ ] **Step 2: 添加预置模板数据**

继续在 init.sql 末尾添加：

```sql
-- 预置分析模板
INSERT INTO document_analysis_template
(id, f_name, f_doc_type, f_description, f_dimensions, f_prompt_templates, f_chunk_merge_rule, f_is_default, f_is_system, f_create_time, f_create_date, f_update_time, f_update_date)
VALUES
('tpl_bid_default', '招标文件分析', 'bid', '适用于招标文件、投标文件分析',
 '["关键条款摘要", "时间节点提醒", "风险/注意事项", "商务条件分析"]',
 '{"key_points": "请提取以下招标文件章节的关键条款摘要，列出最重要的3-5条内容：\n\n{content}", "time_nodes": "请提取以下招标文件章节中的所有时间节点，包括投标截止时间、开标时间、质疑截止时间等，按时间顺序列出：\n\n{content}", "risks": "请分析以下招标文件章节中投标人需要特别注意的风险点和注意事项，列出3-5条：\n\n{content}", "commercial": "请分析以下招标文件章节的商务条件，包括报价要求、付款方式、保证金、质保期等：\n\n{content}"}',
 '{"max_chars": 2000, "max_chunks": 10}',
 1, 1, UNIX_TIMESTAMP(), NOW(), UNIX_TIMESTAMP(), NOW()),

('tpl_contract_default', '合同分析', 'contract', '适用于合同文书分析',
 '["核心条款摘要", "风险/注意事项", "权利义务分析"]',
 '{"key_points": "请提取以下合同章节的核心条款摘要：\n\n{content}", "risks": "请分析以下合同章节的风险点和注意事项：\n\n{content}", "rights": "请分析以下合同章节中甲乙双方的权利和义务：\n\n{content}"}',
 '{"max_chars": 2000, "max_chunks": 10}',
 1, 1, UNIX_TIMESTAMP(), NOW(), UNIX_TIMESTAMP(), NOW()),

('tpl_law_default', '法律文书分析', 'law', '适用于法律法规、法律文书分析',
 '["核心条款摘要", "适用范围", "风险提示"]',
 '{"key_points": "请提取以下法律文书的核心条款：\n\n{content}", "scope": "请分析以下法律文书的适用范围：\n\n{content}", "risks": "请提示以下法律文书的风险注意点：\n\n{content}"}',
 '{"max_chars": 2000, "max_chunks": 10}',
 1, 1, UNIX_TIMESTAMP(), NOW(), UNIX_TIMESTAMP(), NOW());
```

- [ ] **Step 3: 提交代码**

```bash
git add docker/init.sql
git commit -m "feat: 添加文档分析表和预置模板初始化脚本

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

## Task 3: Service 层 - 分析模板服务

**Files:**
- Create: `api/db/services/analysis_template_service.py`

- [ ] **Step 1: 创建 analysis_template_service.py**

```python
#
#  Copyright 2026 The InfiniFlow Authors. All Rights Reserved.
#
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.
#
from peewee import fn

from api.db.db_models import DB, DocumentAnalysisTemplate
from api.db.services.common_service import CommonService
from common.misc_utils import get_uuid


class AnalysisTemplateService(CommonService):
    model = DocumentAnalysisTemplate

    @classmethod
    @DB.connection_context()
    def get_list(cls, doc_type=None, tenant_id=None, page=1, page_size=20):
        """获取模板列表"""
        query = cls.model.select()

        if doc_type:
            query = query.where(cls.model.doc_type == doc_type)

        # 获取全局模板或租户自己的模板
        if tenant_id:
            query = query.where((cls.model.tenant_id.is_null()) | (cls.model.tenant_id == tenant_id))
        else:
            query = query.where(cls.model.tenant_id.is_null())

        total = query.count()
        templates = query.order_by(cls.model.is_default.desc(), cls.model.create_time.desc()) \
            .paginate(page, page_size)

        return list(templates.dicts()), total

    @classmethod
    @DB.connection_context()
    def get_by_id(cls, template_id):
        """根据ID获取模板"""
        try:
            return cls.model.get_by_id(template_id)
        except:
            return None

    @classmethod
    @DB.connection_context()
    def get_default_by_type(cls, doc_type):
        """获取指定类型的默认模板"""
        try:
            return cls.model.get(
                (cls.model.doc_type == doc_type) &
                (cls.model.is_default == True) &
                (cls.model.tenant_id.is_null())
            )
        except:
            return None

    @classmethod
    @DB.connection_context()
    def create(cls, data):
        """创建模板"""
        if 'id' not in data or not data['id']:
            data['id'] = get_uuid()
        return cls.model.create(**data)

    @classmethod
    @DB.connection_context()
    def update(cls, template_id, data):
        """更新模板"""
        data['id'] = template_id
        return cls.model.update(**data).where(cls.model.id == template_id).execute() > 0

    @classmethod
    @DB.connection_context()
    def delete(cls, template_id):
        """删除模板（系统模板不可删除）"""
        template = cls.get_by_id(template_id)
        if template and template.is_system:
            return False, "系统模板不可删除"
        return cls.model.delete().where(cls.model.id == template_id).execute() > 0, None
```

- [ ] **Step 2: 提交代码**

```bash
git add api/db/services/analysis_template_service.py
git commit -m "feat: 添加分析模板服务层

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

## Task 4: Service 层 - 分析结果服务

**Files:**
- Create: `api/db/services/document_analysis_service.py`

- [ ] **Step 1: 创建 document_analysis_service.py**

```python
#
#  Copyright 2026 The InfiniFlow Authors. All Rights Reserved.
#
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.
#
from api.db.db_models import DB, DocumentAnalysisResult
from api.db.services.common_service import CommonService
from common.misc_utils import get_uuid


class DocumentAnalysisService(CommonService):
    model = DocumentAnalysisResult

    @classmethod
    @DB.connection_context()
    def get_by_document(cls, document_id):
        """根据文档ID获取最新分析结果"""
        try:
            return cls.model.select().where(
                cls.model.document_id == document_id
            ).order_by(cls.model.create_time.desc()).first()
        except:
            return None

    @classmethod
    @DB.connection_context()
    def get_by_id(cls, result_id):
        """根据ID获取分析结果"""
        try:
            return cls.model.get_by_id(result_id)
        except:
            return None

    @classmethod
    @DB.connection_context()
    def create(cls, data):
        """创建分析记录"""
        if 'id' not in data or not data['id']:
            data['id'] = get_uuid()
        return cls.model.create(**data)

    @classmethod
    @DB.connection_context()
    def update_status(cls, result_id, status, progress=None, result=None, error_message=None):
        """更新分析状态"""
        update_data = {'status': status}
        if progress is not None:
            update_data['progress'] = progress
        if result is not None:
            update_data['result'] = result
        if error_message is not None:
            update_data['error_message'] = error_message

        return cls.model.update(**update_data).where(cls.model.id == result_id).execute() > 0

    @classmethod
    @DB.connection_context()
    def delete_by_document(cls, document_id):
        """删除文档的所有分析结果"""
        return cls.model.delete().where(cls.model.document_id == document_id).execute()
```

- [ ] **Step 2: 提交代码**

```bash
git add api/db/services/document_analysis_service.py
git commit -m "feat: 添加文档分析结果服务层

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

## Task 5: 核心逻辑 - Prompt 模板文件

**Files:**
- Create: `api/lib/analysis/__init__.py`
- Create: `api/lib/analysis/prompts/__init__.py`
- Create: `api/lib/analysis/prompts/bid_analysis.yaml`

- [ ] **Step 1: 创建目录和 __init__.py**

```bash
mkdir -p D:/AI/ragflow2/api/lib/analysis/prompts
```

创建 `api/lib/analysis/__init__.py`：
```python
# 文档分析模块
```

创建 `api/lib/analysis/prompts/__init__.py`：
```python
# Prompt 模板
```

- [ ] **Step 2: 创建招标文件分析 prompt 模板**

创建 `api/lib/analysis/prompts/bid_analysis.yaml`：

```yaml
# 招标文件分析 Prompt 模板

key_points:
  name: 关键条款摘要
  prompt: |
    请提取以下招标文件章节的关键条款摘要，列出最重要的3-5条内容。
    每条用简短的句子概括，保留关键数字和时间。

    章节内容：
    {content}

    请按以下格式输出：
    1. [条款摘要]
    2. [条款摘要]
    ...

time_nodes:
  name: 时间节点提醒
  prompt: |
    请提取以下招标文件章节中的所有时间节点。
    包括投标截止时间、开标时间、质疑截止时间、踏勘时间等。

    章节内容：
    {content}

    请按以下格式输出：
    - [时间节点名称]：[具体日期时间]

risks:
  name: 风险/注意事项
  prompt: |
    请分析以下招标文件章节中投标人需要特别注意的风险点和注意事项。
    关注：资质要求、技术参数、评分标准、废标条款等。

    章节内容：
    {content}

    请列出3-5条风险/注意事项：
    1. [风险点]
    2. [风险点]
    ...

commercial:
  name: 商务条件分析
  prompt: |
    请分析以下招标文件章节的商务条件。
    关注：报价要求、付款方式、保证金、质保期、验收标准等。

    章节内容：
    {content}

    请输出商务条件摘要：
    - [商务条件项]：[具体内容]
```

- [ ] **Step 3: 创建合同分析 prompt 模板**

创建 `api/lib/analysis/prompts/contract_analysis.yaml`：

```yaml
# 合同分析 Prompt 模板

key_points:
  name: 核心条款摘要
  prompt: |
    请提取以下合同章节的核心条款摘要，列出最重要的内容。

    章节内容：
    {content}

    请按以下格式输出：
    1. [条款摘要]
    2. [条款摘要]
    ...

risks:
  name: 风险/注意事项
  prompt: |
    请分析以下合同章节中的风险点和注意事项。
    关注：违约责任、免责条款、争议解决、知识产权等。

    章节内容：
    {content}

    请列出风险/注意事项：
    1. [风险点]
    2. [风险点]
    ...

rights:
  name: 权利义务分析
  prompt: |
    请分析以下合同章节中甲乙双方的权利和义务。

    章节内容：
    {content}

    请分别列出：
    【甲方权利】
    - [权利内容]

    【甲方义务】
    - [义务内容]

    【乙方权利】
    - [权利内容]

    【乙方义务】
    - [义务内容]
```

- [ ] **Step 4: 提交代码**

```bash
git add api/lib/analysis/
git commit -m "feat: 添加文档分析 Prompt 模板

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

## Task 6: 核心逻辑 - 章节合并器

**Files:**
- Create: `api/lib/analysis/chunk_merger.py`

- [ ] **Step 1: 创建 chunk_merger.py**

```python
#
#  Copyright 2026 The InfiniFlow Authors. All Rights Reserved.
#
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied,
#  See the License for the specific language governing permissions and
#  limitations under the License.
#
import re
from typing import List, Dict, Any
from dataclasses import dataclass, field


@dataclass
class Section:
    """章节数据结构"""
    section_id: str
    section_name: str
    content: str
    chunk_ids: List[str] = field(default_factory=list)
    page_range: List[int] = field(default_factory=list)
    char_count: int = 0


class ChunkMerger:
    """Chunk 合并器 - 将文档 chunks 合并为章节"""

    # 章节标题正则模式
    SECTION_PATTERNS = [
        r'^第[一二三四五六七八九十百零]+[章节篇部]',  # 第一章、第一节
        r'^[一二三四五六七八九十]+[、.．]',  # 一、二、三、
        r'^\d+[、.．]\s*[\u4e00-\u9fa5]+',  # 1、xxx 2、xxx
        r'^[（(][一二三四五六七八九十]+[)）]',  # （一）（二）
        r'^第\d+[章节条]',  # 第1章、第2节
        r'^[A-Z][、.．]\s*',  # A、B、C
    ]

    def __init__(self, max_chars: int = 2000, max_chunks: int = 10):
        self.max_chars = max_chars
        self.max_chunks = max_chunks

    def is_section_title(self, text: str) -> bool:
        """判断是否为章节标题"""
        text = text.strip()
        if len(text) > 50:  # 标题通常较短
            return False

        for pattern in self.SECTION_PATTERNS:
            if re.match(pattern, text):
                return True
        return False

    def extract_section_name(self, text: str) -> str:
        """提取章节名称"""
        text = text.strip()
        # 移除常见的格式符号
        text = re.sub(r'^[第\s]+', '', text)
        text = re.sub(r'[章节篇部条]\s*', ' ', text)
        return text.strip() or text[:20]

    def merge(self, chunks: List[Dict[str, Any]]) -> List[Section]:
        """
        合并 chunks 为章节

        Args:
            chunks: chunk 列表，每个 chunk 包含 content, id, positions 等字段

        Returns:
            章节列表
        """
        if not chunks:
            return []

        sections = []
        current_section = None
        section_counter = 0

        for chunk in chunks:
            content = chunk.get('content', '') or chunk.get('content_with_weight', '')
            chunk_id = chunk.get('id', chunk.get('chunk_id', ''))
            positions = chunk.get('positions', chunk.get('position_int', []))

            # 获取页码
            page_num = 0
            if positions and len(positions) > 0:
                page_num = positions[0][0] if isinstance(positions[0], list) else positions[0]

            # 检查是否为新章节开头
            first_line = content.split('\n')[0] if content else ''
            is_new_section = self.is_section_title(first_line)

            # 判断是否需要创建新章节
            should_create_new = (
                current_section is None or
                is_new_section or
                current_section.char_count >= self.max_chars or
                len(current_section.chunk_ids) >= self.max_chunks
            )

            if should_create_new:
                # 保存当前章节
                if current_section:
                    sections.append(current_section)

                # 创建新章节
                section_counter += 1
                section_name = self.extract_section_name(first_line) if is_new_section else f"第{section_counter}段"

                current_section = Section(
                    section_id=f"sec_{section_counter:03d}",
                    section_name=section_name,
                    content=content,
                    chunk_ids=[chunk_id],
                    page_range=[page_num, page_num],
                    char_count=len(content)
                )
            else:
                # 追加到当前章节
                current_section.content += '\n\n' + content
                current_section.chunk_ids.append(chunk_id)
                current_section.char_count += len(content)
                current_section.page_range[1] = max(current_section.page_range[1], page_num)

        # 保存最后一个章节
        if current_section:
            sections.append(current_section)

        return sections


def merge_chunks_to_sections(chunks: List[Dict], merge_rule: Dict = None) -> List[Dict]:
    """
    合并 chunks 为章节（便捷函数）

    Args:
        chunks: chunk 列表
        merge_rule: 合并规则，包含 max_chars, max_chunks

    Returns:
        章节字典列表
    """
    if not merge_rule:
        merge_rule = {}

    merger = ChunkMerger(
        max_chars=merge_rule.get('max_chars', 2000),
        max_chunks=merge_rule.get('max_chunks', 10)
    )

    sections = merger.merge(chunks)

    return [
        {
            'section_id': s.section_id,
            'section_name': s.section_name,
            'content': s.content,
            'chunk_ids': s.chunk_ids,
            'page_range': s.page_range,
            'char_count': s.char_count
        }
        for s in sections
    ]
```

- [ ] **Step 2: 提交代码**

```bash
git add api/lib/analysis/chunk_merger.py
git commit -m "feat: 添加章节合并器

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

## Task 7: 核心逻辑 - 章节分析器

**Files:**
- Create: `api/lib/analysis/section_analyzer.py`

- [ ] **Step 1: 创建 section_analyzer.py**

```python
#
#  Copyright 2026 The InfiniFlow Authors. All Rights Reserved.
#
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.
#
import logging
import json
from typing import Dict, List, Any, Optional

from rag.llm.chat_model import ChatModel

logger = logging.getLogger(__name__)


class SectionAnalyzer:
    """章节分析器 - 调用 LLM 分析章节内容"""

    def __init__(self, llm_config: Dict, prompt_templates: Dict, dimensions: List[str]):
        """
        Args:
            llm_config: LLM 配置，包含 api_key, model_name, base_url 等
            prompt_templates: 各维度的 prompt 模板
            dimensions: 需要分析的维度列表
        """
        self.llm_config = llm_config
        self.prompt_templates = prompt_templates
        self.dimensions = dimensions

        # 初始化 ChatModel
        self.chat_model = ChatModel[llm_config.get('factory', 'OpenAI')](
            key=llm_config.get('api_key', ''),
            model_name=llm_config.get('model_name', 'gpt-3.5-turbo'),
            base_url=llm_config.get('base_url')
        )

    async def analyze_section(self, section: Dict) -> Dict[str, Any]:
        """
        分析单个章节

        Args:
            section: 章节数据，包含 section_id, section_name, content 等

        Returns:
            分析结果字典
        """
        content = section.get('content', '')
        if not content:
            return {}

        analysis_result = {}

        for dimension in self.dimensions:
            prompt_template = self.prompt_templates.get(dimension, '')
            if not prompt_template:
                logger.warning(f"Missing prompt template for dimension: {dimension}")
                continue

            # 构造 prompt
            prompt = prompt_template.format(content=content)

            try:
                # 调用 LLM
                response = await self._call_llm(prompt)
                analysis_result[dimension] = {
                    'label': self._get_dimension_label(dimension),
                    'content': response
                }
            except Exception as e:
                logger.error(f"Failed to analyze dimension {dimension}: {e}")
                analysis_result[dimension] = {
                    'label': self._get_dimension_label(dimension),
                    'content': f'分析失败: {str(e)}'
                }

        return analysis_result

    async def _call_llm(self, prompt: str) -> str:
        """调用 LLM 获取响应"""
        messages = [{'role': 'user', 'content': prompt}]

        # 使用 chat_model 的 chat 方法
        response = self.chat_model.chat('', messages, {})

        # 收集响应
        result = ''
        async for chunk in response:
            if chunk and isinstance(chunk, str):
                result += chunk

        return result.strip()

    def _get_dimension_label(self, dimension: str) -> str:
        """获取维度的中文标签"""
        labels = {
            'key_points': '关键条款摘要',
            'time_nodes': '时间节点提醒',
            'risks': '风险/注意事项',
            'commercial': '商务条件分析',
            'scope': '适用范围',
            'rights': '权利义务分析'
        }
        return labels.get(dimension, dimension)


async def analyze_document_sections(
    sections: List[Dict],
    llm_config: Dict,
    prompt_templates: Dict,
    dimensions: List[str],
    progress_callback=None
) -> List[Dict]:
    """
    分析文档所有章节

    Args:
        sections: 章节列表
        llm_config: LLM 配置
        prompt_templates: Prompt 模板
        dimensions: 分析维度
        progress_callback: 进度回调函数

    Returns:
        分析结果列表
    """
    analyzer = SectionAnalyzer(llm_config, prompt_templates, dimensions)
    results = []
    total = len(sections)

    for i, section in enumerate(sections):
        analysis = await analyzer.analyze_section(section)

        results.append({
            'section_id': section.get('section_id'),
            'section_name': section.get('section_name'),
            'page_range': section.get('page_range', []),
            'analysis': analysis
        })

        # 进度回调
        if progress_callback:
            progress = int((i + 1) / total * 100)
            await progress_callback(progress, section.get('section_name'))

    return results
```

- [ ] **Step 2: 提交代码**

```bash
git add api/lib/analysis/section_analyzer.py
git commit -m "feat: 添加章节分析器

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

## Task 8: API 层 - 分析模板接口

**Files:**
- Create: `api/apps/restful_apis/analysis_template_api.py`

- [ ] **Step 1: 创建 analysis_template_api.py**

```python
#
#  Copyright 2026 The InfiniFlow Authors. All Rights Reserved.
#
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.
#
from quart import request

from api.apps import manager, login_required
from api.db.services.analysis_template_service import AnalysisTemplateService
from api.utils.api_utils import (
    get_request_json,
    get_result,
    get_error_data_result,
    add_tenant_id_to_kwargs,
)
from common.misc_utils import get_uuid


@manager.route('/analysis-templates', methods=['GET'])
@login_required
async def get_analysis_templates():
    """获取分析模板列表"""
    tenant_id = request.args.get('tenant_id')
    doc_type = request.args.get('doc_type')
    page = int(request.args.get('page', 1))
    page_size = int(request.args.get('page_size', 20))

    templates, total = AnalysisTemplateService.get_list(
        doc_type=doc_type,
        tenant_id=tenant_id,
        page=page,
        page_size=page_size
    )

    return get_result(data=templates, total=total)


@manager.route('/analysis-templates/<template_id>', methods=['GET'])
@login_required
async def get_analysis_template(template_id):
    """获取分析模板详情"""
    template = AnalysisTemplateService.get_by_id(template_id)

    if not template:
        return get_error_data_result(message='模板不存在')

    return get_result(data=template.to_dict())


@manager.route('/analysis-templates', methods=['POST'])
@login_required
async def create_analysis_template():
    """创建自定义分析模板"""
    data = await get_request_json()

    # 验证必填字段
    if not data.get('name'):
        return get_error_data_result(message='模板名称不能为空')
    if not data.get('doc_type'):
        return get_error_data_result(message='文档类型不能为空')

    # 设置ID
    data['id'] = data.get('id') or get_uuid()

    try:
        template = AnalysisTemplateService.create(data)
        return get_result(data=template.to_dict())
    except Exception as e:
        return get_error_data_result(message=f'创建失败: {str(e)}')


@manager.route('/analysis-templates/<template_id>', methods=['PUT'])
@login_required
async def update_analysis_template(template_id):
    """更新分析模板"""
    data = await get_request_json()

    template = AnalysisTemplateService.get_by_id(template_id)
    if not template:
        return get_error_data_result(message='模板不存在')

    if template.is_system:
        return get_error_data_result(message='系统模板不可修改')

    try:
        AnalysisTemplateService.update(template_id, data)
        return get_result(message='更新成功')
    except Exception as e:
        return get_error_data_result(message=f'更新失败: {str(e)}')


@manager.route('/analysis-templates/<template_id>', methods=['DELETE'])
@login_required
async def delete_analysis_template(template_id):
    """删除分析模板"""
    success, error = AnalysisTemplateService.delete(template_id)

    if not success:
        return get_error_data_result(message=error or '删除失败')

    return get_result(message='删除成功')
```

- [ ] **Step 2: 提交代码**

```bash
git add api/apps/restful_apis/analysis_template_api.py
git commit -m "feat: 添加分析模板 API 接口

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

## Task 9: API 层 - 文档分析接口

**Files:**
- Create: `api/apps/restful_apis/document_analysis_api.py`

- [ ] **Step 1: 创建 document_analysis_api.py**

```python
#
#  Copyright 2026 The InfiniFlow Authors. All Rights Reserved.
#
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.
#
import asyncio
import logging
from quart import request

from api.apps import manager, login_required
from api.db.db_models import TenantLLM
from api.db.services.document_analysis_service import DocumentAnalysisService
from api.db.services.analysis_template_service import AnalysisTemplateService
from api.db.services.document_service import DocumentService
from api.db.services.knowledgebase_service import KnowledgebaseService
from api.db.services.tenant_llm_service import TenantLLMService
from api.utils.api_utils import (
    get_request_json,
    get_result,
    get_error_data_result,
)
from api.lib.analysis.chunk_merger import merge_chunks_to_sections
from api.lib.analysis.section_analyzer import analyze_document_sections
from common.misc_utils import get_uuid
from common.doc_store import DocStore

logger = logging.getLogger(__name__)


async def run_analysis_task(
    result_id: str,
    document_id: str,
    template_id: str,
    kb_id: str,
    tenant_id: str,
    llm_id: str = None
):
    """后台分析任务"""
    try:
        # 更新状态为运行中
        DocumentAnalysisService.update_status(result_id, 'running', progress=0)

        # 获取模板
        template = AnalysisTemplateService.get_by_id(template_id)
        if not template:
            DocumentAnalysisService.update_status(result_id, 'failed', error_message='模板不存在')
            return

        # 获取文档信息
        doc = DocumentService.get_by_id(document_id)
        if not doc:
            DocumentAnalysisService.update_status(result_id, 'failed', error_message='文档不存在')
            return

        # 获取文档 chunks
        doc_store = DocStore()
        chunks = doc_store.search(
            kb_id,
            {'doc_ids': [document_id]},
            page=1,
            page_size=10000
        )

        if not chunks:
            DocumentAnalysisService.update_status(result_id, 'failed', error_message='文档内容为空')
            return

        # 合并章节
        sections = merge_chunks_to_sections(chunks, template.chunk_merge_rule)

        if not sections:
            DocumentAnalysisService.update_status(result_id, 'failed', error_message='无法识别章节')
            return

        # 获取 LLM 配置
        llm_config = TenantLLMService.get_model_config(tenant_id, llm_id)
        if not llm_config:
            DocumentAnalysisService.update_status(result_id, 'failed', error_message='未配置模型')
            return

        # 定义进度回调
        async def progress_callback(progress, section_name):
            DocumentAnalysisService.update_status(result_id, 'running', progress=progress)
            logger.info(f"Analysis progress: {progress}% - {section_name}")

        # 执行分析
        results = await analyze_document_sections(
            sections=sections,
            llm_config=llm_config,
            prompt_templates=template.prompt_templates,
            dimensions=template.dimensions,
            progress_callback=progress_callback
        )

        # 更新结果
        DocumentAnalysisService.update_status(
            result_id,
            'completed',
            progress=100,
            result=results
        )

    except Exception as e:
        logger.error(f"Analysis task failed: {e}", exc_info=True)
        DocumentAnalysisService.update_status(result_id, 'failed', error_message=str(e))


@manager.route('/documents/<document_id>/analyze', methods=['POST'])
@login_required
async def analyze_document(document_id):
    """触发文档分析"""
    data = await get_request_json() or {}

    # 获取文档信息
    doc = DocumentService.get_by_id(document_id)
    if not doc:
        return get_error_data_result(message='文档不存在')

    # 获取模板
    template_id = data.get('template_id')
    if template_id:
        template = AnalysisTemplateService.get_by_id(template_id)
    else:
        # 根据知识库配置获取默认模板
        kb = KnowledgebaseService.get_by_id(doc.kb_id)
        doc_type = kb.parser_id if kb else 'general'
        template = AnalysisTemplateService.get_default_by_type(doc_type)

    if not template:
        return get_error_data_result(message='未找到合适的分析模板')

    # 获取租户信息
    tenant_id = data.get('tenant_id', doc.created_by)

    # 创建分析记录
    result_id = get_uuid()
    DocumentAnalysisService.create({
        'id': result_id,
        'document_id': document_id,
        'template_id': template.id,
        'status': 'pending',
        'doc_name': doc.name,
        'kb_id': doc.kb_id,
        'tenant_id': tenant_id,
        'llm_id': data.get('llm_id')
    })

    # 启动后台任务
    asyncio.create_task(run_analysis_task(
        result_id=result_id,
        document_id=document_id,
        template_id=template.id,
        kb_id=doc.kb_id,
        tenant_id=tenant_id,
        llm_id=data.get('llm_id')
    ))

    return get_result(data={'task_id': result_id, 'status': 'pending'})


@manager.route('/documents/<document_id>/analysis', methods=['GET'])
@login_required
async def get_document_analysis(document_id):
    """获取文档分析结果"""
    task_id = request.args.get('task_id')

    if task_id:
        result = DocumentAnalysisService.get_by_id(task_id)
    else:
        result = DocumentAnalysisService.get_by_document(document_id)

    if not result:
        return get_error_data_result(message='分析结果不存在')

    # 获取模板名称
    template = AnalysisTemplateService.get_by_id(result.template_id)

    response = {
        'status': result.status,
        'progress': result.progress,
        'template_name': template.name if template else '',
        'sections': result.result or [],
        'error_message': result.error_message
    }

    return get_result(data=response)


@manager.route('/documents/<document_id>/analysis', methods=['DELETE'])
@login_required
async def delete_document_analysis(document_id):
    """删除文档分析结果"""
    count = DocumentAnalysisService.delete_by_document(document_id)
    return get_result(message=f'已删除 {count} 条分析记录')
```

- [ ] **Step 2: 提交代码**

```bash
git add api/apps/restful_apis/document_analysis_api.py
git commit -m "feat: 添加文档分析 API 接口

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

## Task 10: 注册 API 路由

**Files:**
- Modify: `api/apps/__init__.py`

- [ ] **Step 1: 查看现有路由注册方式**

```bash
grep -n "import.*_api\|manager.route" D:/AI/ragflow2/api/apps/__init__.py | head -20
```

- [ ] **Step 2: 添加新 API 模块导入**

在 `api/apps/__init__.py` 中添加：

```python
# 在现有 import 语句后添加
import api.apps.restful_apis.analysis_template_api
import api.apps.restful_apis.document_analysis_api
```

- [ ] **Step 3: 提交代码**

```bash
git add api/apps/__init__.py
git commit -m "feat: 注册文档分析 API 路由

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

## Task 11: 前端 - 分析结果页面

**Files:**
- Create: `web/src/pages/document-analysis/index.tsx`
- Create: `web/src/pages/document-analysis/components/SectionNav.tsx`
- Create: `web/src/pages/document-analysis/components/AnalysisContent.tsx`

- [ ] **Step 1: 创建页面目录**

```bash
mkdir -p D:/AI/ragflow2/web/src/pages/document-analysis/components
```

- [ ] **Step 2: 创建 SectionNav.tsx**

```tsx
import React from 'react';
import { ScrollArea } from '@/components/ui/scroll-area';

interface Section {
  section_id: string;
  section_name: string;
  page_range: number[];
}

interface SectionNavProps {
  sections: Section[];
  activeId: string;
  onSelect: (id: string) => void;
}

export const SectionNav: React.FC<SectionNavProps> = ({ sections, activeId, onSelect }) => {
  return (
    <div className="w-64 border-r bg-gray-50">
      <div className="p-4 border-b">
        <h3 className="font-semibold">章节目录</h3>
      </div>
      <ScrollArea className="h-[calc(100vh-120px)]">
        <div className="p-2">
          {sections.map((section) => (
            <div
              key={section.section_id}
              className={`p-3 rounded cursor-pointer mb-1 ${
                activeId === section.section_id
                  ? 'bg-blue-100 text-blue-700'
                  : 'hover:bg-gray-100'
              }`}
              onClick={() => onSelect(section.section_id)}
            >
              <div className="font-medium truncate">{section.section_name}</div>
              <div className="text-xs text-gray-500">
                第 {section.page_range[0]}-{section.page_range[1]} 页
              </div>
            </div>
          ))}
        </div>
      </ScrollArea>
    </div>
  );
};
```

- [ ] **Step 3: 创建 AnalysisContent.tsx**

```tsx
import React, { useState } from 'react';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import ReactMarkdown from 'react-markdown';

interface AnalysisData {
  [key: string]: {
    label: string;
    content: string;
  };
}

interface AnalysisContentProps {
  section: {
    section_name: string;
    page_range: number[];
    analysis: AnalysisData;
  } | null;
}

export const AnalysisContent: React.FC<AnalysisContentProps> = ({ section }) => {
  const [activeTab, setActiveTab] = useState('key_points');

  if (!section) {
    return (
      <div className="flex items-center justify-center h-full text-gray-500">
        请选择左侧章节查看分析结果
      </div>
    );
  }

  const dimensions = Object.keys(section.analysis);

  return (
    <div className="flex-1 p-6">
      <div className="mb-4">
        <h2 className="text-xl font-bold">{section.section_name}</h2>
        <p className="text-sm text-gray-500">
          第 {section.page_range[0]}-{section.page_range[1]} 页
        </p>
      </div>

      <Tabs value={activeTab} onValueChange={setActiveTab}>
        <TabsList>
          {dimensions.map((key) => (
            <TabsTrigger key={key} value={key}>
              {section.analysis[key]?.label || key}
            </TabsTrigger>
          ))}
        </TabsList>

        {dimensions.map((key) => (
          <TabsContent key={key} value={key} className="mt-4">
            <div className="prose max-w-none">
              <ReactMarkdown>
                {section.analysis[key]?.content || '暂无内容'}
              </ReactMarkdown>
            </div>
          </TabsContent>
        ))}
      </Tabs>
    </div>
  );
};
```

- [ ] **Step 4: 创建主页面 index.tsx**

```tsx
import React, { useState, useEffect } from 'react';
import { useParams, useSearchParams } from 'react-router-dom';
import { Progress } from '@/components/ui/progress';
import { Button } from '@/components/ui/button';
import { SectionNav } from './components/SectionNav';
import { AnalysisContent } from './components/AnalysisContent';
import api from '@/utils/api';

interface Section {
  section_id: string;
  section_name: string;
  page_range: number[];
  analysis: Record<string, { label: string; content: string }>;
}

export const DocumentAnalysisPage: React.FC = () => {
  const { documentId } = useParams<{ documentId: string }>();
  const [searchParams] = useSearchParams();
  const taskId = searchParams.get('task_id');

  const [status, setStatus] = useState<string>('pending');
  const [progress, setProgress] = useState<number>(0);
  const [sections, setSections] = useState<Section[]>([]);
  const [activeSectionId, setActiveSectionId] = useState<string>('');
  const [errorMessage, setErrorMessage] = useState<string>('');
  const [templateName, setTemplateName] = useState<string>('');

  useEffect(() => {
    if (!documentId) return;

    const fetchResult = async () => {
      try {
        const params = taskId ? `?task_id=${taskId}` : '';
        const response = await api.get(`/documents/${documentId}/analysis${params}`);
        const data = response.data.data;

        setStatus(data.status);
        setProgress(data.progress);
        setSections(data.sections || []);
        setTemplateName(data.template_name);
        setErrorMessage(data.error_message || '');

        if (data.sections?.length && !activeSectionId) {
          setActiveSectionId(data.sections[0].section_id);
        }
      } catch (error) {
        console.error('Failed to fetch analysis result:', error);
      }
    };

    fetchResult();

    // 如果正在分析，轮询更新
    if (status === 'pending' || status === 'running') {
      const interval = setInterval(fetchResult, 2000);
      return () => clearInterval(interval);
    }
  }, [documentId, taskId, status]);

  const triggerAnalysis = async () => {
    try {
      const response = await api.post(`/documents/${documentId}/analyze`, {});
      setStatus('pending');
      setProgress(0);
    } catch (error) {
      console.error('Failed to trigger analysis:', error);
    }
  };

  const activeSection = sections.find((s) => s.section_id === activeSectionId);

  if (status === 'failed') {
    return (
      <div className="flex flex-col items-center justify-center h-full">
        <p className="text-red-500 mb-4">分析失败: {errorMessage}</p>
        <Button onClick={triggerAnalysis}>重试</Button>
      </div>
    );
  }

  if (status === 'pending' || status === 'running') {
    return (
      <div className="flex flex-col items-center justify-center h-full">
        <div className="w-64 mb-4">
          <Progress value={progress} />
        </div>
        <p className="text-gray-500">正在分析文档... {progress}%</p>
      </div>
    );
  }

  return (
    <div className="flex h-[calc(100vh-64px)]">
      <SectionNav
        sections={sections}
        activeId={activeSectionId}
        onSelect={setActiveSectionId}
      />
      <AnalysisContent section={activeSection || null} />
    </div>
  );
};

export default DocumentAnalysisPage;
```

- [ ] **Step 5: 提交代码**

```bash
git add web/src/pages/document-analysis/
git commit -m "feat: 添加文档分析结果前端页面

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

## Task 12: 前端 - 路由配置

**Files:**
- Modify: `web/src/routes.tsx`

- [ ] **Step 1: 添加路由配置**

在 `routes.tsx` 中添加新路由：

```tsx
// 在 import 部分添加
import DocumentAnalysisPage from '@/pages/document-analysis';

// 在 routes 数组中添加
{
  path: '/document/:documentId/analysis',
  element: <DocumentAnalysisPage />,
}
```

- [ ] **Step 2: 提交代码**

```bash
git add web/src/routes.tsx
git commit -m "feat: 添加文档分析页面路由

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

## Task 13: 集成测试

- [ ] **Step 1: 重启后端服务**

```bash
# 停止现有服务
ps aux | grep python | grep ragflow_server | awk '{print $2}' | xargs kill 2>/dev/null

# 启动新服务
cd D:/AI/ragflow2 && PYTHONPATH=D:/AI/ragflow2 .venv/Scripts/python.exe api/ragflow_server.py &
```

- [ ] **Step 2: 测试获取模板列表 API**

```bash
curl -s http://localhost:9380/api/v1/analysis-templates | python -m json.tool
```

Expected: 返回预置模板列表

- [ ] **Step 3: 测试触发分析 API**

```bash
curl -s -X POST http://localhost:9380/api/v1/documents/{document_id}/analyze \
  -H "Content-Type: application/json" \
  -d '{}' | python -m json.tool
```

Expected: 返回 `{"data": {"task_id": "xxx", "status": "pending"}}`

- [ ] **Step 4: 测试获取分析结果 API**

```bash
curl -s "http://localhost:9380/api/v1/documents/{document_id}/analysis?task_id={task_id}" | python -m json.tool
```

Expected: 返回分析进度和结果

- [ ] **Step 5: 验证前端页面**

访问 `http://localhost:9222/document/{document_id}/analysis`，确认页面正常显示。

---

## 实现完成检查清单

- [ ] 数据模型已创建并自动建表
- [ ] 预置模板已通过 init.sql 插入
- [ ] 模板管理 API 可正常调用
- [ ] 文档分析 API 可触发分析
- [ ] 分析结果可正常获取
- [ ] 前端页面可正常展示
- [ ] 代码已全部提交
