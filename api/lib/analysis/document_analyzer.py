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
