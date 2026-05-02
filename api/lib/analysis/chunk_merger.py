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
Chunk Merger - 将文档chunks合并为章节的工具类

该模块提供将文档解析后的chunks按照章节结构合并的功能，
用于后续的文档分析处理。
"""
import logging
import re
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class Section:
    """章节结构"""
    title: str
    level: int
    content: str = ""
    start_page: Optional[int] = None
    end_page: Optional[int] = None
    children: list = field(default_factory=list)

    def to_dict(self) -> dict:
        """转换为字典格式"""
        return {
            "title": self.title,
            "level": self.level,
            "content": self.content,
            "start_page": self.start_page,
            "end_page": self.end_page,
            "children": [child.to_dict() for child in self.children]
        }


class ChunkMerger:
    """Chunk合并器

    将文档解析后的chunks按照章节结构合并，支持多种合并策略。
    """

    # 章节标题正则模式
    SECTION_PATTERNS = [
        # 一级标题: 第X章/部分/篇
        re.compile(r'^第[一二三四五六七八九十百千万零\d]+[章节部分篇]\s*(.*)', re.MULTILINE),
        # 二级标题: X.X 或 第X节
        re.compile(r'^(\d+\.)+\d*\s+(.+)', re.MULTILINE),
        re.compile(r'^第[一二三四五六七八九十百千万零\d]+节\s*(.*)', re.MULTILINE),
        # 三级标题: (一) (二) 或 1. 2. 或 （一）（二）
        re.compile(r'^[（\(][一二三四五六七八九十\d]+[）\)]\s*(.+)', re.MULTILINE),
        re.compile(r'^\d+[\.\、]\s*(.+)', re.MULTILINE),
    ]

    # 最小合并字符数
    MIN_MERGE_LENGTH = 500
    # 最大合并字符数
    MAX_MERGE_LENGTH = 8000

    def __init__(self, min_merge_length: int = None, max_merge_length: int = None):
        """初始化合并器

        Args:
            min_merge_length: 最小合并长度，小于此长度的章节会被合并
            max_merge_length: 最大合并长度，超过此长度会被分割
        """
        self.min_merge_length = min_merge_length or self.MIN_MERGE_LENGTH
        self.max_merge_length = max_merge_length or self.MAX_MERGE_LENGTH

    def merge_chunks(self, chunks: list[dict]) -> list[Section]:
        """将chunks合并为章节

        Args:
            chunks: 文档chunks列表，每个chunk应包含content和可选的metadata

        Returns:
            合并后的章节列表
        """
        if not chunks:
            return []

        # 1. 识别章节边界
        sections = self._identify_sections(chunks)

        # 2. 合并过小的章节
        sections = self._merge_small_sections(sections)

        # 3. 分割过大的章节
        sections = self._split_large_sections(sections)

        logger.info(f"Chunk merger: merged {len(chunks)} chunks into {len(sections)} sections")
        return sections

    def _identify_sections(self, chunks: list[dict]) -> list[Section]:
        """识别章节边界"""
        sections = []
        current_section = None
        current_content = []

        for chunk in chunks:
            content = chunk.get("content", "") or chunk.get("content_with_weight", "")
            if not content:
                continue

            # 检查是否为新章节开始
            section_match = self._match_section_title(content)

            if section_match:
                # 保存前一个章节
                if current_section:
                    current_section.content = "\n".join(current_content)
                    sections.append(current_section)

                # 开始新章节
                title, level = section_match
                current_section = Section(
                    title=title,
                    level=level,
                    start_page=chunk.get("metadata", {}).get("page")
                )
                current_content = [content]
            else:
                # 追加到当前章节
                current_content.append(content)
                if current_section and chunk.get("metadata", {}).get("page"):
                    current_section.end_page = chunk["metadata"]["page"]

        # 保存最后一个章节
        if current_section:
            current_section.content = "\n".join(current_content)
            sections.append(current_section)
        elif current_content:
            # 没有识别到章节，创建默认章节
            sections.append(Section(
                title="文档内容",
                level=0,
                content="\n".join(current_content)
            ))

        return sections

    def _match_section_title(self, content: str) -> Optional[tuple]:
        """匹配章节标题

        Returns:
            (title, level) 或 None
        """
        # 只检查内容开头部分
        header = content[:200]

        for level, pattern in enumerate(self.SECTION_PATTERNS):
            match = pattern.search(header)
            if match:
                # 提取标题文本
                title = match.group(1) if match.lastindex else match.group(0)
                title = title.strip()[:100]  # 限制标题长度
                return (title, level + 1)

        return None

    def _merge_small_sections(self, sections: list[Section]) -> list[Section]:
        """合并过小的章节"""
        if len(sections) <= 1:
            return sections

        merged = []
        pending = None

        for section in sections:
            if len(section.content) < self.min_merge_length:
                if pending:
                    # 合并到pending
                    pending.content += "\n\n" + section.content
                    pending.end_page = section.end_page
                    pending.children.extend(section.children)
                else:
                    pending = section
            else:
                if pending:
                    if len(pending.content) >= self.min_merge_length:
                        merged.append(pending)
                    else:
                        # 合并到下一个章节
                        section.content = pending.content + "\n\n" + section.content
                        section.start_page = pending.start_page
                        section.children = pending.children + section.children
                    pending = None
                merged.append(section)

        # 处理最后的pending
        if pending:
            if merged:
                # 合并到最后一个章节
                merged[-1].content += "\n\n" + pending.content
                merged[-1].end_page = pending.end_page
                merged[-1].children.extend(pending.children)
            else:
                merged.append(pending)

        return merged

    def _split_large_sections(self, sections: list[Section]) -> list[Section]:
        """分割过大的章节"""
        result = []

        for section in sections:
            if len(section.content) <= self.max_merge_length:
                result.append(section)
            else:
                # 按段落分割
                paragraphs = section.content.split("\n\n")
                current_content = []
                current_length = 0
                part_num = 1

                for para in paragraphs:
                    if current_length + len(para) > self.max_merge_length and current_content:
                        # 保存当前部分
                        result.append(Section(
                            title=f"{section.title} (第{part_num}部分)",
                            level=section.level,
                            content="\n\n".join(current_content),
                            start_page=section.start_page,
                            end_page=section.start_page  # 无法精确确定
                        ))
                        part_num += 1
                        current_content = [para]
                        current_length = len(para)
                    else:
                        current_content.append(para)
                        current_length += len(para)

                # 保存最后一部分
                if current_content:
                    result.append(Section(
                        title=f"{section.title} (第{part_num}部分)" if part_num > 1 else section.title,
                        level=section.level,
                        content="\n\n".join(current_content),
                        start_page=section.start_page,
                        end_page=section.end_page
                    ))

        return result

    def merge_by_pages(self, chunks: list[dict], pages_per_section: int = 5) -> list[Section]:
        """按页码范围合并chunks

        Args:
            chunks: 文档chunks列表
            pages_per_section: 每个章节包含的页数

        Returns:
            合并后的章节列表
        """
        if not chunks:
            return []

        # 按页码分组
        page_groups = {}
        for chunk in chunks:
            content = chunk.get("content", "") or chunk.get("content_with_weight", "")
            page = chunk.get("metadata", {}).get("page", 0)

            if page not in page_groups:
                page_groups[page] = []
            page_groups[page].append(content)

        if not page_groups:
            return []

        # 合并为章节
        sections = []
        sorted_pages = sorted(page_groups.keys())
        current_section_pages = []
        current_page_start = sorted_pages[0]

        for page in sorted_pages:
            current_section_pages.extend(page_groups[page])

            if page - current_page_start + 1 >= pages_per_section:
                sections.append(Section(
                    title=f"第{current_page_start}-{page}页",
                    level=1,
                    content="\n".join(current_section_pages),
                    start_page=current_page_start,
                    end_page=page
                ))
                current_section_pages = []
                current_page_start = page + 1

        # 处理剩余内容
        if current_section_pages:
            sections.append(Section(
                title=f"第{current_page_start}-{sorted_pages[-1]}页",
                level=1,
                content="\n".join(current_section_pages),
                start_page=current_page_start,
                end_page=sorted_pages[-1]
            ))

        return sections