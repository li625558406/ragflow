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
            "请合并以下分析结果：\n{batch_results}\n\n请输出合并后的结果："
        )

        # 构建批次结果文本
        batch_text = "\n".join([
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
            return "\n".join(batch_results)

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
