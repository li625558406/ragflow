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
Section Analyzer - 章节分析器

该模块提供调用LLM分析文档章节内容的功能，
支持多种分析类型和批量处理。
"""
import logging
import os
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional

import yaml

logger = logging.getLogger(__name__)


class AnalysisType(Enum):
    """分析类型枚举"""
    KEY_POINTS = "key_points"          # 关键条款摘要
    TIME_NODES = "time_nodes"          # 时间节点
    RISKS = "risks"                    # 风险分析
    COMMERCIAL = "commercial"          # 商务条件
    RIGHTS = "rights"                  # 权利义务


@dataclass
class AnalysisResult:
    """分析结果"""
    section_title: str
    analysis_type: str
    result: str
    success: bool = True
    error_message: str = ""

    def to_dict(self) -> dict:
        return {
            "section_title": self.section_title,
            "analysis_type": self.analysis_type,
            "result": self.result,
            "success": self.success,
            "error_message": self.error_message
        }


@dataclass
class SectionAnalysisResult:
    """章节分析结果"""
    section_title: str
    analyses: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "section_title": self.section_title,
            "analyses": [a.to_dict() for a in self.analyses]
        }


class SectionAnalyzer:
    """章节分析器

    调用LLM对文档章节进行结构化分析。
    """

    # Prompt模板缓存
    _prompt_templates = {}

    def __init__(self, llm_client, doc_type: str = "bid"):
        """初始化分析器

        Args:
            llm_client: LLM客户端实例
            doc_type: 文档类型，支持 'bid'(招标文件) 和 'contract'(合同)
        """
        self.llm_client = llm_client
        self.doc_type = doc_type
        self._load_prompts()

    def _load_prompts(self):
        """加载Prompt模板"""
        if self.doc_type in self._prompt_templates:
            return

        # 查找prompt文件
        prompt_file = Path(__file__).parent / "prompts" / f"{self.doc_type}_analysis.yaml"

        if not prompt_file.exists():
            logger.warning(f"Prompt file not found: {prompt_file}, using default")
            prompt_file = Path(__file__).parent / "prompts" / "bid_analysis.yaml"

        if prompt_file.exists():
            with open(prompt_file, "r", encoding="utf-8") as f:
                self._prompt_templates[self.doc_type] = yaml.safe_load(f)
        else:
            # 使用默认模板
            self._prompt_templates[self.doc_type] = self._get_default_prompts()

    def _get_default_prompts(self) -> dict:
        """获取默认Prompt模板"""
        return {
            "key_points": {
                "name": "关键内容摘要",
                "prompt": "请提取以下内容的关键要点：\n{content}"
            },
            "risks": {
                "name": "风险分析",
                "prompt": "请分析以下内容中的风险点：\n{content}"
            }
        }

    def get_prompt(self, analysis_type: str) -> Optional[dict]:
        """获取指定分析类型的Prompt

        Args:
            analysis_type: 分析类型

        Returns:
            Prompt字典，包含name和prompt字段
        """
        templates = self._prompt_templates.get(self.doc_type, {})
        return templates.get(analysis_type)

    def analyze_section(
        self,
        section_content: str,
        section_title: str,
        analysis_types: list[str] = None
    ) -> SectionAnalysisResult:
        """分析单个章节

        Args:
            section_content: 章节内容
            section_title: 章节标题
            analysis_types: 分析类型列表，默认使用所有可用类型

        Returns:
            章节分析结果
        """
        result = SectionAnalysisResult(section_title=section_title)

        if not analysis_types:
            analysis_types = list(self._prompt_templates.get(self.doc_type, {}).keys())

        for analysis_type in analysis_types:
            prompt_info = self.get_prompt(analysis_type)
            if not prompt_info:
                logger.warning(f"Unknown analysis type: {analysis_type}")
                continue

            try:
                prompt = prompt_info["prompt"].format(content=section_content)
                analysis_result = self._call_llm(prompt)

                result.analyses.append(AnalysisResult(
                    section_title=section_title,
                    analysis_type=analysis_type,
                    result=analysis_result,
                    success=True
                ))
            except Exception as e:
                logger.error(f"Analysis failed for {section_title}/{analysis_type}: {e}")
                result.analyses.append(AnalysisResult(
                    section_title=section_title,
                    analysis_type=analysis_type,
                    result="",
                    success=False,
                    error_message=str(e)
                ))

        return result

    def analyze_sections(
        self,
        sections: list,
        analysis_types: list[str] = None,
        progress_callback=None
    ) -> list[SectionAnalysisResult]:
        """批量分析多个章节

        Args:
            sections: 章节列表，每个章节应包含title和content字段
            analysis_types: 分析类型列表
            progress_callback: 进度回调函数 callback(current, total, section_title)

        Returns:
            所有章节的分析结果列表
        """
        results = []
        total = len(sections)

        for i, section in enumerate(sections):
            section_title = section.title if hasattr(section, 'title') else section.get('title', '未命名章节')
            section_content = section.content if hasattr(section, 'content') else section.get('content', '')

            if progress_callback:
                progress_callback(i + 1, total, section_title)

            result = self.analyze_section(section_content, section_title, analysis_types)
            results.append(result)

        return results

    def _call_llm(self, prompt: str) -> str:
        """调用LLM

        Args:
            prompt: 输入prompt

        Returns:
            LLM响应文本
        """
        try:
            # 使用同步chat方法
            response = self.llm_client.chat(
                [{"role": "user", "content": prompt}],
                gen_conf={"temperature": 0.3, "max_tokens": 2000}
            )

            # 处理响应
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

    def analyze_with_template(
        self,
        section_content: str,
        section_title: str,
        custom_prompt: str
    ) -> AnalysisResult:
        """使用自定义prompt分析章节

        Args:
            section_content: 章节内容
            section_title: 章节标题
            custom_prompt: 自定义prompt模板，使用{content}作为内容占位符

        Returns:
            分析结果
        """
        try:
            prompt = custom_prompt.format(content=section_content)
            result = self._call_llm(prompt)

            return AnalysisResult(
                section_title=section_title,
                analysis_type="custom",
                result=result,
                success=True
            )
        except Exception as e:
            return AnalysisResult(
                section_title=section_title,
                analysis_type="custom",
                result="",
                success=False,
                error_message=str(e)
            )


def get_available_analysis_types(doc_type: str) -> list[dict]:
    """获取指定文档类型的可用分析类型

    Args:
        doc_type: 文档类型

    Returns:
        可用分析类型列表，每项包含type和name字段
    """
    prompt_file = Path(__file__).parent / "prompts" / f"{doc_type}_analysis.yaml"

    if not prompt_file.exists():
        return [
            {"type": "key_points", "name": "关键内容摘要"},
            {"type": "risks", "name": "风险分析"}
        ]

    with open(prompt_file, "r", encoding="utf-8") as f:
        templates = yaml.safe_load(f)

    return [
        {"type": key, "name": value.get("name", key)}
        for key, value in templates.items()
    ]