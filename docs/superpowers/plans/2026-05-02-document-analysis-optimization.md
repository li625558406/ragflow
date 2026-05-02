# 文档分析逻辑优化实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将文档分析从"分章节分析"优化为"整体文档分析"，支持超长文档分批处理和结果合并。

**Architecture:** 新增三个组件：TextSplitter（按段落切割）、DocumentAnalyzer（整体分析）、ResultMerger（合并结果）。通过 tokens 计算判断是否分批，分批结果最终通过 LLM 合并为完整报告。

**Tech Stack:** Python, dataclasses, 现有 LLMBundle

---

## 文件结构

### 新增文件
- `api/lib/analysis/text_splitter.py` - 按段落切割文本
- `api/lib/analysis/document_analyzer.py` - 整体文档分析器
- `api/lib/analysis/result_merger.py` - 结果合并器
- `api/lib/analysis/prompts/merge_analysis.yaml` - 合并 prompt 模板

### 修改文件
- `api/apps/restful_apis/document_analysis_api.py` - 使用新的 DocumentAnalyzer
- `api/lib/analysis/prompts/bid_analysis.yaml` - 优化分析 prompt

---

### Task 1: 创建 TextSplitter 组件

**Files:**
- Create: `api/lib/analysis/text_splitter.py`

- [ ] **Step 1: 创建 text_splitter.py 文件**

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
"""
Text Splitter - 文本切割器

按段落边界切割文本，确保每批不超过上下文限制。
"""
import logging
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)

# 关键参数
MAX_CONTEXT_TOKENS = 8000      # 模型上下文限制
RESERVED_TOKENS = 2000         # 预留给系统提示和输出
MAX_CONTENT_TOKENS = 6000      # 实际可用于文档内容的 tokens


@dataclass
class TextBatch:
    """文本批次"""
    content: str                    # 批次内容
    tokens: int                     # tokens 数量
    start_page: Optional[int] = None  # 起始页码
    end_page: Optional[int] = None    # 结束页码


class TextSplitter:
    """文本切割器

    按段落边界切割文本，确保每批不超过上下文限制。
    """

    def __init__(self, max_tokens: int = MAX_CONTENT_TOKENS):
        """初始化切割器

        Args:
            max_tokens: 每批最大 tokens 数
        """
        self.max_tokens = max_tokens

    def count_tokens(self, text: str) -> int:
        """估算文本的 tokens 数量

        使用简化估算：中文约 1.5 字符/token，英文约 4 字符/token
        综合估算：约 2 字符/token

        Args:
            text: 输入文本

        Returns:
            估算的 tokens 数量
        """
        if not text:
            return 0
        # 简化估算：平均 2 字符/token
        return len(text) // 2

    def split_by_paragraphs(self, text: str) -> list[TextBatch]:
        """按段落切割文本

        Args:
            text: 输入文本

        Returns:
            文本批次列表
        """
        if not text:
            return []

        # 按双换行分割段落
        paragraphs = text.split("\n\n")
        batches = []
        current_batch = []
        current_tokens = 0

        for para in paragraphs:
            para = para.strip()
            if not para:
                continue

            para_tokens = self.count_tokens(para)

            # 如果单个段落就超过限制，需要进一步切割
            if para_tokens > self.max_tokens:
                # 先保存当前批次
                if current_batch:
                    batches.append(TextBatch(
                        content="\n\n".join(current_batch),
                        tokens=current_tokens
                    ))
                    current_batch = []
                    current_tokens = 0

                # 按单换行切割超长段落
                sub_batches = self._split_long_paragraph(para)
                batches.extend(sub_batches)
                continue

            # 检查是否需要开始新批次
            if current_tokens + para_tokens > self.max_tokens and current_batch:
                batches.append(TextBatch(
                    content="\n\n".join(current_batch),
                    tokens=current_tokens
                ))
                current_batch = [para]
                current_tokens = para_tokens
            else:
                current_batch.append(para)
                current_tokens += para_tokens

        # 保存最后一批
        if current_batch:
            batches.append(TextBatch(
                content="\n\n".join(current_batch),
                tokens=current_tokens
            ))

        logger.info(f"TextSplitter: split text into {len(batches)} batches")
        return batches

    def _split_long_paragraph(self, para: str) -> list[TextBatch]:
        """切割超长段落

        Args:
            para: 超长段落文本

        Returns:
            文本批次列表
        """
        # 按单换行切割
        lines = para.split("\n")
        batches = []
        current_lines = []
        current_tokens = 0

        for line in lines:
            line = line.strip()
            if not line:
                continue

            line_tokens = self.count_tokens(line)

            if current_tokens + line_tokens > self.max_tokens and current_lines:
                batches.append(TextBatch(
                    content="\n".join(current_lines),
                    tokens=current_tokens
                ))
                current_lines = [line]
                current_tokens = line_tokens
            else:
                current_lines.append(line)
                current_tokens += line_tokens

        if current_lines:
            batches.append(TextBatch(
                content="\n".join(current_lines),
                tokens=current_tokens
            ))

        return batches
```

- [ ] **Step 2: 验证文件创建成功**

Run: `ls -la D:/AI/ragflow2/api/lib/analysis/text_splitter.py`
Expected: 文件存在

---

### Task 2: 创建 ResultMerger 组件

**Files:**
- Create: `api/lib/analysis/result_merger.py`
- Create: `api/lib/analysis/prompts/merge_analysis.yaml`

- [ ] **Step 1: 创建合并 prompt 模板**

```yaml
# 合并分析结果的 prompt 模板
key_points:
  name: "关键要点合并"
  prompt: |
    以下是同一份文档分批次分析得到的关键要点结果。
    请将这些结果合并为一份完整、连贯的分析报告。

    要求：
    1. 去除重复内容
    2. 补充遗漏的重要信息
    3. 保持逻辑连贯
    4. 按重要性排序
    5. 保留关键数字和时间信息

    {batch_results}

    请输出合并后的完整关键要点分析报告：

risks:
  name: "风险分析合并"
  prompt: |
    以下是同一份文档分批次分析得到的风险分析结果。
    请将这些结果合并为一份完整、连贯的风险分析报告。

    要求：
    1. 去除重复风险点
    2. 补充遗漏的风险
    3. 按风险严重程度排序
    4. 保持描述清晰准确

    {batch_results}

    请输出合并后的完整风险分析报告：

time_nodes:
  name: "时间节点合并"
  prompt: |
    以下是同一份文档分批次分析得到的时间节点结果。
    请将这些结果合并为一份完整的时间节点列表。

    要求：
    1. 去除重复时间节点
    2. 补充遗漏的时间节点
    3. 按时间顺序排序
    4. 保留完整的日期和事件描述

    {batch_results}

    请输出合并后的完整时间节点列表：

commercial:
  name: "商务条件合并"
  prompt: |
    以下是同一份文档分批次分析得到的商务条件结果。
    请将这些结果合并为一份完整的商务条件分析报告。

    要求：
    1. 去除重复条款
    2. 补充遗漏的重要商务条件
    3. 保持条款完整性
    4. 保留关键金额和条件

    {batch_results}

    请输出合并后的完整商务条件分析报告：

rights:
  name: "权利义务合并"
  prompt: |
    以下是同一份文档分批次分析得到的权利义务结果。
    请将这些结果合并为一份完整的权利义务分析报告。

    要求：
    1. 去除重复条款
    2. 补充遗漏的权利义务
    3. 区分甲方和乙方权利义务
    4. 保持条款完整性

    {batch_results}

    请输出合并后的完整权利义务分析报告：
```

- [ ] **Step 2: 创建 result_merger.py 文件**

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
"""
Result Merger - 结果合并器

合并多批次分析结果为一份完整报告。
"""
import logging
import yaml
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class ResultMerger:
    """结果合并器

    调用 LLM 将多批次分析结果合并为一份完整报告。
    """

    # 合并 prompt 缓存
    _merge_prompts = None

    def __init__(self, llm_client):
        """初始化合并器

        Args:
            llm_client: LLM 客户端实例
        """
        self.llm_client = llm_client
        self._load_merge_prompts()

    def _load_merge_prompts(self):
        """加载合并 prompt 模板"""
        if ResultMerger._merge_prompts is not None:
            return

        prompt_file = Path(__file__).parent / "prompts" / "merge_analysis.yaml"

        if prompt_file.exists():
            with open(prompt_file, "r", encoding="utf-8") as f:
                ResultMerger._merge_prompts = yaml.safe_load(f)
        else:
            # 使用默认模板
            ResultMerger._merge_prompts = {
                "key_points": {
                    "name": "结果合并",
                    "prompt": "请合并以下分析结果，去除重复，保持连贯：\n\n{batch_results}\n\n请输出合并后的结果："
                }
            }

    def merge_results(
        self,
        batch_results: list[str],
        analysis_type: str
    ) -> str:
        """合并多批次结果

        Args:
            batch_results: 各批次的分析结果列表
            analysis_type: 分析类型

        Returns:
            合并后的完整结果
        """
        if not batch_results:
            return ""

        # 如果只有一批，直接返回
        if len(batch_results) == 1:
            return batch_results[0]

        # 获取合并 prompt
        merge_template = self._merge_prompts.get(analysis_type, {})
        merge_prompt_template = merge_template.get(
            "prompt",
            "请合并以下分析结果：\n\n{batch_results}\n\n请输出合并后的结果："
        )

        # 构建批次结果文本
        batch_text = "\n\n".join([
            f"=== 批次 {i + 1} 结果 ===\n{result}"
            for i, result in enumerate(batch_results)
        ])

        # 构建完整 prompt
        merge_prompt = merge_prompt_template.format(batch_results=batch_text)

        # 调用 LLM 合并
        try:
            merged_result = self._call_llm(merge_prompt)
            logger.info(f"ResultMerger: merged {len(batch_results)} batches for {analysis_type}")
            return merged_result
        except Exception as e:
            logger.error(f"ResultMerger: merge failed: {e}")
            # 合并失败时，直接拼接返回
            return "\n\n".join(batch_results)

    def _call_llm(self, prompt: str) -> str:
        """调用 LLM

        Args:
            prompt: 输入 prompt

        Returns:
            LLM 响应文本
        """
        try:
            response = self.llm_client.chat(
                [{"role": "user", "content": prompt}],
                gen_conf={"temperature": 0.3, "max_tokens": 3000}
            )

            if isinstance(response, str):
                return response.strip()
            elif isinstance(response, dict):
                return response.get("content", "").strip()
            elif hasattr(response, "content"):
                return response.content.strip()
            else:
                return str(response).strip()

        except Exception as e:
            logger.error(f"LLM call failed: {e}")
            raise
```

- [ ] **Step 3: 验证文件创建成功**

Run: `ls -la D:/AI/ragflow2/api/lib/analysis/result_merger.py D:/AI/ragflow2/api/lib/analysis/prompts/merge_analysis.yaml`
Expected: 两个文件都存在

---

### Task 3: 创建 DocumentAnalyzer 组件

**Files:**
- Create: `api/lib/analysis/document_analyzer.py`

- [ ] **Step 1: 创建 document_analyzer.py 文件**

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
"""
Document Analyzer - 整体文档分析器

对整个文档进行分析，支持超长文档分批处理。
"""
import logging
import yaml
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Callable

from api.lib.analysis.text_splitter import TextSplitter, TextBatch, MAX_CONTENT_TOKENS
from api.lib.analysis.result_merger import ResultMerger

logger = logging.getLogger(__name__)


@dataclass
class AnalysisItem:
    """单项分析结果"""
    analysis_type: str
    result: str
    success: bool = True
    error_message: str = ""

    def to_dict(self) -> dict:
        return {
            "analysis_type": self.analysis_type,
            "result": self.result,
            "success": self.success,
            "error_message": self.error_message
        }


@dataclass
class DocumentAnalysisResult:
    """文档分析结果"""
    doc_name: str
    analyses: list = field(default_factory=list)
    total_batches: int = 1
    success: bool = True
    error_message: str = ""

    def to_dict(self) -> dict:
        return {
            "doc_name": self.doc_name,
            "analyses": [a.to_dict() for a in self.analyses],
            "total_batches": self.total_batches,
            "success": self.success,
            "error_message": self.error_message
        }


class DocumentAnalyzer:
    """整体文档分析器

    对整个文档进行分析，支持超长文档分批处理和结果合并。
    """

    # 分析 prompt 缓存
    _prompt_templates = {}

    def __init__(self, llm_client, doc_type: str = "bid"):
        """初始化分析器

        Args:
            llm_client: LLM 客户端实例
            doc_type: 文档类型
        """
        self.llm_client = llm_client
        self.doc_type = doc_type
        self.text_splitter = TextSplitter()
        self.result_merger = ResultMerger(llm_client)
        self._load_prompts()

    def _load_prompts(self):
        """加载分析 prompt 模板"""
        if self.doc_type in self._prompt_templates:
            return

        prompt_file = Path(__file__).parent / "prompts" / f"{self.doc_type}_analysis.yaml"

        if not prompt_file.exists():
            prompt_file = Path(__file__).parent / "prompts" / "bid_analysis.yaml"

        if prompt_file.exists():
            with open(prompt_file, "r", encoding="utf-8") as f:
                self._prompt_templates[self.doc_type] = yaml.safe_load(f)
        else:
            self._prompt_templates[self.doc_type] = self._get_default_prompts()

    def _get_default_prompts(self) -> dict:
        """获取默认 prompt 模板"""
        return {
            "key_points": {
                "name": "关键内容摘要",
                "prompt": "请提取以下内容的关键要点：\n{content}"
            }
        }

    def get_prompt(self, analysis_type: str) -> Optional[dict]:
        """获取指定分析类型的 prompt"""
        templates = self._prompt_templates.get(self.doc_type, {})
        return templates.get(analysis_type)

    def analyze_document(
        self,
        chunks: list[dict],
        doc_name: str = "",
        analysis_types: list[str] = None,
        progress_callback: Callable = None
    ) -> DocumentAnalysisResult:
        """分析整个文档

        Args:
            chunks: 文档 chunks 列表
            doc_name: 文档名称
            analysis_types: 分析类型列表
            progress_callback: 进度回调函数 callback(current, total, stage, message)

        Returns:
            文档分析结果
        """
        result = DocumentAnalysisResult(doc_name=doc_name)

        try:
            # 1. 合并 chunks 为完整文本
            full_text = self._merge_chunks(chunks)

            if not full_text:
                result.success = False
                result.error_message = "文档内容为空"
                return result

            # 2. 计算 tokens
            tokens = self.text_splitter.count_tokens(full_text)
            logger.info(f"DocumentAnalyzer: document '{doc_name}' has ~{tokens} tokens")

            # 3. 获取分析类型
            if not analysis_types:
                analysis_types = list(self._prompt_templates.get(self.doc_type, {}).keys())

            # 4. 判断是否需要分批
            if tokens <= MAX_CONTENT_TOKENS:
                # 单批次分析
                result.total_batches = 1
                result.analyses = self._analyze_single(full_text, analysis_types, progress_callback)
            else:
                # 多批次分析
                batches = self.text_splitter.split_by_paragraphs(full_text)
                result.total_batches = len(batches)
                logger.info(f"DocumentAnalyzer: splitting into {len(batches)} batches")

                result.analyses = self._analyze_batched(
                    batches, analysis_types, progress_callback
                )

        except Exception as e:
            logger.error(f"DocumentAnalyzer: analysis failed: {e}")
            result.success = False
            result.error_message = str(e)

        return result

    def _merge_chunks(self, chunks: list[dict]) -> str:
        """合并 chunks 为完整文本"""
        contents = []
        for chunk in chunks:
            content = chunk.get("content", "") or chunk.get("content_with_weight", "")
            if content:
                contents.append(content)
        return "\n\n".join(contents)

    def _analyze_single(
        self,
        text: str,
        analysis_types: list[str],
        progress_callback: Callable = None
    ) -> list[AnalysisItem]:
        """单批次分析"""
        results = []

        if progress_callback:
            progress_callback(1, 1, "analyzing", "正在分析文档...")

        for analysis_type in analysis_types:
            prompt_info = self.get_prompt(analysis_type)
            if not prompt_info:
                logger.warning(f"Unknown analysis type: {analysis_type}")
                continue

            try:
                prompt = prompt_info["prompt"].format(content=text)
                analysis_result = self._call_llm(prompt)

                results.append(AnalysisItem(
                    analysis_type=analysis_type,
                    result=analysis_result,
                    success=True
                ))
            except Exception as e:
                logger.error(f"Analysis failed for {analysis_type}: {e}")
                results.append(AnalysisItem(
                    analysis_type=analysis_type,
                    result="",
                    success=False,
                    error_message=str(e)
                ))

        return results

    def _analyze_batched(
        self,
        batches: list[TextBatch],
        analysis_types: list[str],
        progress_callback: Callable = None
    ) -> list[AnalysisItem]:
        """多批次分析"""
        results = []
        total_batches = len(batches)

        for analysis_type in analysis_types:
            prompt_info = self.get_prompt(analysis_type)
            if not prompt_info:
                logger.warning(f"Unknown analysis type: {analysis_type}")
                continue

            batch_results = []
            failed_batches = []

            # 分析每个批次
            for i, batch in enumerate(batches):
                if progress_callback:
                    progress_callback(
                        i + 1,
                        total_batches,
                        "analyzing",
                        f"正在分析第 {i + 1}/{total_batches} 批..."
                    )

                try:
                    prompt = prompt_info["prompt"].format(content=batch.content)
                    result = self._call_llm(prompt)
                    batch_results.append(result)
                except Exception as e:
                    logger.error(f"Batch {i + 1} analysis failed: {e}")
                    failed_batches.append(i + 1)
                    batch_results.append(f"[批次 {i + 1} 分析失败: {str(e)}]")

            # 合并结果
            if progress_callback:
                progress_callback(1, 1, "merging", "正在合并分析结果...")

            if len(batch_results) > 1:
                merged_result = self.result_merger.merge_results(batch_results, analysis_type)
            else:
                merged_result = batch_results[0] if batch_results else ""

            results.append(AnalysisItem(
                analysis_type=analysis_type,
                result=merged_result,
                success=len(failed_batches) == 0,
                error_message=f"批次 {failed_batches} 失败" if failed_batches else ""
            ))

        return results

    def _call_llm(self, prompt: str) -> str:
        """调用 LLM"""
        try:
            response = self.llm_client.chat(
                [{"role": "user", "content": prompt}],
                gen_conf={"temperature": 0.3, "max_tokens": 3000}
            )

            if isinstance(response, str):
                return response.strip()
            elif isinstance(response, dict):
                return response.get("content", "").strip()
            elif hasattr(response, "content"):
                return response.content.strip()
            else:
                return str(response).strip()

        except Exception as e:
            logger.error(f"LLM call failed: {e}")
            raise
```

- [ ] **Step 2: 验证文件创建成功**

Run: `ls -la D:/AI/ragflow2/api/lib/analysis/document_analyzer.py`
Expected: 文件存在

---

### Task 4: 修改 document_analysis_api.py 使用新组件

**Files:**
- Modify: `api/apps/restful_apis/document_analysis_api.py`

- [ ] **Step 1: 修改导入和 run_analysis_task 函数**

找到 `run_analysis_task` 函数，将 `SectionAnalyzer` 替换为 `DocumentAnalyzer`。

修改导入部分（约第 32-33 行）：

```python
from api.lib.analysis.chunk_merger import ChunkMerger
from api.lib.analysis.section_analyzer import SectionAnalyzer
```

改为：

```python
from api.lib.analysis.document_analyzer import DocumentAnalyzer
```

- [ ] **Step 2: 修改 run_analysis_task 函数中的分析逻辑**

找到 `run_analysis_task` 函数中创建分析器和执行分析的部分（约第 136-156 行）：

```python
        # 创建分析器
        doc_type = template.doc_type or "bid"
        analyzer = SectionAnalyzer(llm_client, doc_type=doc_type)

        # 定义进度回调
        def progress_callback(current, total, section_title):
            progress = int((current / total) * 100)
            DocumentAnalysisService.update_status(result_id, 'running', progress=progress)
            logger.debug(f"Analysis progress: {progress}% - {section_title}")

        # 获取分析类型
        analysis_types = None
        if template.dimensions:
            analysis_types = template.dimensions

        # 执行分析
        results = analyzer.analyze_sections(
            sections=sections,
            analysis_types=analysis_types,
            progress_callback=progress_callback
        )

        # 转换结果为可序列化格式
        result_data = [r.to_dict() for r in results]
```

替换为：

```python
        # 创建分析器
        doc_type = template.doc_type or "bid"
        analyzer = DocumentAnalyzer(llm_client, doc_type=doc_type)

        # 定义进度回调
        def progress_callback(current, total, stage, message):
            if stage == "analyzing":
                progress = int((current / total) * 80)
            elif stage == "merging":
                progress = 80 + int((current / total) * 20)
            else:
                progress = int((current / total) * 100)
            DocumentAnalysisService.update_status(result_id, 'running', progress=progress)
            logger.debug(f"Analysis progress: {progress}% - {stage} - {message}")

        # 获取分析类型
        analysis_types = None
        if template.dimensions:
            analysis_types = template.dimensions

        # 执行分析
        result = analyzer.analyze_document(
            chunks=chunks,
            doc_name=doc.name,
            analysis_types=analysis_types,
            progress_callback=progress_callback
        )

        # 转换结果为可序列化格式
        result_data = [{
            "section_title": "整体分析",
            "analyses": [a.to_dict() for a in result.analyses]
        }]
```

- [ ] **Step 3: 删除不再需要的 ChunkMerger 相关代码**

删除以下代码（约第 95-101 行）：

```python
        # 合并章节
        merger = ChunkMerger()
        sections = merger.merge_chunks(chunks)

        if not sections:
            DocumentAnalysisService.update_status(result_id, 'failed', error_message='无法识别章节')
            return
```

- [ ] **Step 4: 验证修改**

Run: `cd D:/AI/ragflow2 && python -c "from api.apps.restful_apis.document_analysis_api import run_analysis_task; print('Import OK')"`
Expected: 输出 "Import OK"

---

### Task 5: 优化分析 prompt 模板

**Files:**
- Modify: `api/lib/analysis/prompts/bid_analysis.yaml`

- [ ] **Step 1: 查看现有 prompt 文件**

Run: `cat D:/AI/ragflow2/api/lib/analysis/prompts/bid_analysis.yaml`

- [ ] **Step 2: 优化 prompt 模板（如果需要）**

根据实际内容，确保 prompt 模板适合整体文档分析场景。主要检查：
- prompt 中使用 `{content}` 占位符
- 输出格式清晰明确
- 适合长文档分析

---

### Task 6: 测试验证

- [ ] **Step 1: 重启后端服务**

重启 RAGFlow 后端服务，确保新代码生效。

- [ ] **Step 2: 测试小文档分析**

上传一个小文档（< 6000 tokens），点击分析按钮，验证：
- 分析正常完成
- 结果显示在弹框中
- 日志显示 "splitting into N batches" 或单批次分析

- [ ] **Step 3: 测试大文档分析**

上传一个大文档（> 6000 tokens），点击分析按钮，验证：
- 文档被正确分批
- 进度正常更新
- 结果被正确合并
- 最终显示完整的分析报告

---

### Task 7: 提交代码

- [ ] **Step 1: 提交所有更改**

```bash
cd D:/AI/ragflow2
git add api/lib/analysis/text_splitter.py
git add api/lib/analysis/result_merger.py
git add api/lib/analysis/document_analyzer.py
git add api/lib/analysis/prompts/merge_analysis.yaml
git add api/apps/restful_apis/document_analysis_api.py
git commit -m "feat: 优化文档分析逻辑，支持整体文档分析和分批处理

- 新增 TextSplitter: 按段落切割文本
- 新增 DocumentAnalyzer: 整体文档分析器
- 新增 ResultMerger: 合并多批次分析结果
- 支持超长文档分批分析和结果合并
- 保留原有章节分析组件供其他场景使用

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

## 自检清单

**1. Spec 覆盖检查：**
- ✅ 整体文档分析：Task 3 DocumentAnalyzer
- ✅ 按段落切割：Task 1 TextSplitter
- ✅ 分批处理：Task 3 _analyze_batched
- ✅ 结果合并：Task 2 ResultMerger
- ✅ tokens 计算：Task 1 count_tokens
- ✅ 进度回调：Task 4 progress_callback
- ✅ API 集成：Task 4 document_analysis_api.py

**2. Placeholder 扫描：**
- ✅ 无 TBD、TODO
- ✅ 所有代码步骤都有完整代码
- ✅ 所有命令都有预期输出

**3. 类型一致性：**
- ✅ TextBatch 在 text_splitter.py 定义
- ✅ DocumentAnalysisResult 在 document_analyzer.py 定义
- ✅ AnalysisItem 在 document_analyzer.py 定义
- ✅ to_dict() 方法在各数据类中一致
