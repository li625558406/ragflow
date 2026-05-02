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
from api.db.services.document_analysis_service import DocumentAnalysisService
from api.db.services.analysis_template_service import AnalysisTemplateService
from api.db.services.document_service import DocumentService
from api.db.services.knowledgebase_service import KnowledgebaseService
from api.db.services.tenant_llm_service import TenantLLMService
from api.db.joint_services.tenant_model_service import get_model_config_by_type_and_name
from api.utils.api_utils import (
    get_request_json,
    get_result,
    get_error_data_result,
)
from api.lib.analysis.chunk_merger import ChunkMerger
from api.lib.analysis.section_analyzer import SectionAnalyzer
from common.misc_utils import get_uuid
from common import settings
from common.constants import LLMType
from rag.nlp import search

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
        query = {
            "doc_ids": [document_id],
            "page": 1,
            "size": 10000,
            "fields": ["content_with_weight", "docnm_kwd", "kb_id"],
        }

        if not settings.docStoreConn.index_exist(search.index_name(tenant_id), kb_id):
            DocumentAnalysisService.update_status(result_id, 'failed', error_message='文档索引不存在')
            return

        sres = await settings.retriever.search(query, search.index_name(tenant_id), [kb_id])

        if not sres or sres.total == 0:
            DocumentAnalysisService.update_status(result_id, 'failed', error_message='文档内容为空')
            return

        # 构建 chunks 数据
        chunks = []
        for chunk_id in sres.ids:
            chunk_data = {
                "id": chunk_id,
                "content": sres.field[chunk_id].get("content_with_weight", ""),
                "metadata": {}
            }
            chunks.append(chunk_data)

        # 合并章节
        merger = ChunkMerger()
        sections = merger.merge_chunks(chunks)

        if not sections:
            DocumentAnalysisService.update_status(result_id, 'failed', error_message='无法识别章节')
            return

        # 获取 LLM 配置
        llm_config = get_model_config_by_type_and_name(tenant_id, LLMType.CHAT, llm_id)
        if not llm_config:
            DocumentAnalysisService.update_status(result_id, 'failed', error_message='未配置模型')
            return

        # 创建 LLM 客户端
        from api.db.services.llm_service import LLMBundle
        llm_client = LLMBundle(tenant_id, llm_config)

        # 创建分析器
        doc_type = template.doc_type or "bid"
        analyzer = SectionAnalyzer(llm_client, doc_type=doc_type)

        # 定义进度回调
        def progress_callback(current, total, section_title):
            progress = int((current / total) * 100)
            DocumentAnalysisService.update_status(result_id, 'running', progress=progress)
            logger.info(f"Analysis progress: {progress}% - {section_title}")

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

        # 更新结果
        DocumentAnalysisService.update_status(
            result_id,
            'completed',
            progress=100,
            result=result_data
        )

    except Exception as e:
        logger.error(f"Analysis task failed: {e}", exc_info=True)
        DocumentAnalysisService.update_status(result_id, 'failed', error_message=str(e))


@manager.route('/documents/<document_id>/analyze', methods=['POST'])  # noqa: F821
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


@manager.route('/documents/<document_id>/analysis', methods=['GET'])  # noqa: F821
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


@manager.route('/documents/<document_id>/analysis', methods=['DELETE'])  # noqa: F821
@login_required
async def delete_document_analysis(document_id):
    """删除文档分析结果"""
    count = DocumentAnalysisService.delete_by_document(document_id)
    return get_result(message=f'已删除 {count} 条分析记录')
