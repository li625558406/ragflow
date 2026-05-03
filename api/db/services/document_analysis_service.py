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

from api.db.db_models import DB, DocumentAnalysisResult
from api.db.services.common_service import CommonService
from common.misc_utils import get_uuid
from rag.utils.redis_conn import REDIS_CONN

logger = logging.getLogger(__name__)


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
    def update_status(cls, result_id, status, progress=None, result=None, error_message=None):
        """更新分析状态

        使用 atomic() 确保在多线程环境下事务正确提交。
        """
        update_data = {'status': status}
        if progress is not None:
            update_data['progress'] = progress
        if result is not None:
            update_data['result'] = result
        if error_message is not None:
            update_data['error_message'] = error_message

        try:
            with DB.atomic():
                updated = cls.model.update(**update_data).where(cls.model.id == result_id).execute() > 0
                logger.debug(f"Updated analysis status: {result_id} -> {status}, progress: {progress}")
                return updated
        except Exception as e:
            logger.error(f"Failed to update analysis status for {result_id}: {e}")
            return False

    @classmethod
    @DB.connection_context()
    def delete_by_document(cls, document_id):
        """删除文档的所有分析结果"""
        return cls.model.delete().where(cls.model.document_id == document_id).execute()

    @classmethod
    def cancel_analysis(cls, result_id):
        """取消分析任务"""
        try:
            REDIS_CONN.set(f"{result_id}-cancel", "x")
            logger.info(f"Analysis task {result_id} marked as canceled")
            return True
        except Exception as e:
            logger.error(f"Failed to cancel analysis {result_id}: {e}")
            return False

    @classmethod
    def has_canceled(cls, result_id):
        """检查分析任务是否被取消"""
        try:
            if REDIS_CONN.get(f"{result_id}-cancel"):
                logger.info(f"Analysis task {result_id} has been canceled")
                return True
        except Exception as e:
            logger.exception(f"Failed to check cancel status for {result_id}: {e}")
        return False

    @classmethod
    def clear_cancel_flag(cls, result_id):
        """清除取消标记"""
        try:
            REDIS_CONN.delete(f"{result_id}-cancel")
        except Exception as e:
            logger.warning(f"Failed to clear cancel flag for {result_id}: {e}")

    @classmethod
    @DB.connection_context()
    def get_analysis_results_as_kb_chunks(cls, tenant_id, document_ids=None, template_ids=None, result_ids=None):
        """将分析结果转换为知识库 chunk 格式

        Args:
            tenant_id: 租户 ID
            document_ids: 可选的文档 ID 列表，过滤特定文档的分析结果
            template_ids: 可选的模板 ID 列表，过滤特定模板的分析结果
            result_ids: 可选的分析结果 ID 列表，过滤特定的分析结果

        Returns:
            类似知识库 chunk 的字典列表，可用于检索
        """
        import json
        from rag.nlp import rag_tokenizer
        from api.db.services.analysis_template_service import AnalysisTemplateService

        query = cls.model.select().where(cls.model.status == 'completed')

        # 直接使用 DocumentAnalysisResult 的 tenant_id 字段过滤
        query = query.where(cls.model.tenant_id == tenant_id)

        if document_ids:
            query = query.where(cls.model.document_id.in_(document_ids))
        if template_ids:
            query = query.where(cls.model.template_id.in_(template_ids))
        if result_ids:
            query = query.where(cls.model.id.in_(result_ids))

        results = list(query.order_by(cls.model.create_time.desc()))

        logger.info(f"[get_analysis_results_as_kb_chunks] Found {len(results)} analysis results for tenant {tenant_id}")

        # 批量获取模板名称，避免在循环中频繁查询
        template_ids_in_results = list(set([r.template_id for r in results if r.template_id]))
        template_names = {}
        for tid in template_ids_in_results:
            template = AnalysisTemplateService.get_by_id(tid)
            template_names[tid] = template.name if template else ""

        chunks = []
        for result in results:
            try:
                template_name = template_names.get(result.template_id, "")
                logger.debug(f"[get_analysis_results_as_kb_chunks] Processing result {result.id}, doc_name: {result.doc_name}, template: {template_name}, result: {bool(result.result)}")

                # result.result 是 JSONField，已经自动解析为 Python 对象
                # 存储格式: [{"section_title": "...", "analyses": [...]}]
                sections = []
                if result.result:
                    if isinstance(result.result, list):
                        sections = result.result
                    elif isinstance(result.result, dict):
                        # 如果是 dict，可能有 "sections" 键
                        sections = result.result.get("sections", [])
                    elif isinstance(result.result, str):
                        try:
                            parsed = json.loads(result.result)
                            if isinstance(parsed, list):
                                sections = parsed
                            elif isinstance(parsed, dict):
                                sections = parsed.get("sections", [])
                        except json.JSONDecodeError:
                            # 字符串但不是 JSON，当作原始文本处理
                            sections = [{"section_title": "分析结果", "analyses": [{"analysis_type": "raw", "result": result.result}]}]
                for section_idx, section in enumerate(sections):
                    section_title = section.get("section_title", "")
                    analyses = section.get("analyses", [])

                    # 为每个分析类型创建一个 chunk
                    for analysis_idx, analysis in enumerate(analyses):
                        analysis_type = analysis.get("analysis_type", "")
                        analysis_result = analysis.get("result", "")

                        if not analysis_result:
                            continue

                        # 确保 analysis_result 是字符串
                        if not isinstance(analysis_result, str):
                            analysis_result = str(analysis_result) if analysis_result is not None else ""

                        # 构建内容
                        content_parts = []
                        if result.doc_name:
                            content_parts.append(f"文档: {result.doc_name}")
                        if template_name:
                            content_parts.append(f"模板: {template_name}")
                        if section_title:
                            content_parts.append(f"章节: {section_title}")
                        if analysis_type:
                            content_parts.append(f"类型: {analysis_type}")

                        content_prefix = " | ".join(content_parts)
                        content = f"{content_prefix}\n{analysis_result}"

                        # 确保 content 是非空字符串
                        if not content or not content.strip():
                            continue

                        # 安全地创建 token
                        try:
                            content_ltks = rag_tokenizer.tokenize(content)
                            content_sm_ltks = rag_tokenizer.fine_grained_tokenize(content_ltks)
                        except Exception as e:
                            logger.warning(f"Failed to tokenize content for result {result.id}: {e}")
                            content_ltks = []
                            content_sm_ltks = []

                        # 创建 chunk
                        chunk = {
                            "id": f"analysis_{result.id}_{section_idx}_{analysis_idx}",  # 唯一 ID
                            "chunk_id": f"analysis_{result.id}_{section_idx}_{analysis_idx}",  # 兼容字段
                            "content_with_weight": content,
                            "content_ltks": content_ltks,
                            "content_sm_ltks": content_sm_ltks,
                            # 分析结果特有字段
                            "doc_id": result.document_id,
                            "doc_name": result.doc_name or "",
                            "template_id": result.template_id or "",
                            "template_name": template_name or "",
                            "section_title": section_title or "",
                            "analysis_type": analysis_type or "",
                            "analysis_result_id": result.id,
                            # 兼容字段
                            "kb_id": f"analysis_{result.id}",  # 虚拟知识库 ID
                            "important_keywords": [],
                            "image_id": None,
                            "img_id": None,  # 兼容字段
                            "available_int": 1,
                            "display_qa": "",
                        }
                        chunks.append(chunk)

                # 如果没有 sections 结构，直接使用 raw result
                if not sections and result.result:
                    content = f"文档: {result.doc_name or '未知'}"
                    if template_name:
                        content += f" | 模板: {template_name}"

                    # 处理不同类型的 result.result
                    raw_content = ""
                    if result.result is None:
                        raw_content = ""
                    elif isinstance(result.result, str):
                        raw_content = result.result
                    elif isinstance(result.result, (dict, list)):
                        raw_content = json.dumps(result.result, ensure_ascii=False, indent=2)
                    else:
                        raw_content = str(result.result)

                    content += f"\n{raw_content}"

                    # 确保 content 非空
                    if not content.strip():
                        continue

                    # 安全地创建 token
                    try:
                        content_ltks = rag_tokenizer.tokenize(content)
                        content_sm_ltks = rag_tokenizer.fine_grained_tokenize(content_ltks)
                    except Exception as e:
                        logger.warning(f"Failed to tokenize fallback content for result {result.id}: {e}")
                        content_ltks = []
                        content_sm_ltks = []

                    chunk = {
                        "id": f"analysis_{result.id}_0_0",  # 唯一 ID
                        "chunk_id": f"analysis_{result.id}_0_0",  # 兼容字段
                        "content_with_weight": content,
                        "content_ltks": content_ltks,
                        "content_sm_ltks": content_sm_ltks,
                        "doc_id": result.document_id,
                        "doc_name": result.doc_name or "",
                        "template_id": result.template_id or "",
                        "template_name": template_name or "",
                        "analysis_result_id": result.id,
                        "kb_id": f"analysis_{result.id}",
                        "important_keywords": [],
                        "image_id": None,
                        "img_id": None,  # 兼容字段
                        "available_int": 1,
                        "display_qa": "",
                    }
                    chunks.append(chunk)

            except Exception as e:
                logger.warning(f"Failed to convert analysis result {result.id} to chunk: {e}")
                continue

        logger.info(f"Converted {len(chunks)} analysis result chunks for tenant {tenant_id}")
        return chunks

