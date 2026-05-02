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
