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

from api.apps import login_required
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
from api.lib.analysis.document_analyzer import DocumentAnalyzer
from common.misc_utils import get_uuid, thread_pool_exec
from common import settings
from common.constants import LLMType
from rag.nlp import search

logger = logging.getLogger(__name__)


def _run_analysis_task_sync(
    result_id: str,
    document_id: str,
    template_id: str,
    kb_id: str,
    tenant_id: str,
    llm_id: str = None
):
    """同步包装函数，用于在新线程中运行分析任务

    在新线程中创建独立的事件循环来运行异步任务，
    避免阻塞主事件循环，同时确保数据库操作正常工作。
    """
    import asyncio
    try:
        asyncio.run(run_analysis_task(
            result_id=result_id,
            document_id=document_id,
            template_id=template_id,
            kb_id=kb_id,
            tenant_id=tenant_id,
            llm_id=llm_id
        ))
    except Exception as e:
        logger.error(f"Analysis task failed in thread: {e}", exc_info=True)
        # 更新状态为失败
        DocumentAnalysisService.update_status(result_id, 'failed', error_message=str(e))


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
        success, doc = DocumentService.get_by_id(document_id)
        if not success or not doc:
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

        # 确定 LLM：优先使用模板中的 llm_id，否则使用传入的 llm_id
        actual_llm_id = template.llm_id or llm_id

        # 如果没有指定模型，尝试获取租户的默认 Chat 模型
        if not actual_llm_id:
            from api.db.services.tenant_llm_service import TenantService
            tenant_result = TenantService.get_by_id(tenant_id)
            if isinstance(tenant_result, tuple):
                t_success, tenant = tenant_result
            else:
                tenant = tenant_result
            if tenant and hasattr(tenant, 'llm_id') and tenant.llm_id:
                actual_llm_id = tenant.llm_id

        if not actual_llm_id:
            DocumentAnalysisService.update_status(result_id, 'failed', error_message='未配置 Chat 模型，请在模板中选择模型或配置系统默认模型')
            return

        # 获取 LLM 配置
        try:
            llm_config = get_model_config_by_type_and_name(tenant_id, LLMType.CHAT, actual_llm_id)
        except Exception as e:
            DocumentAnalysisService.update_status(result_id, 'failed', error_message=f'获取模型配置失败: {str(e)}')
            return

        if not llm_config:
            DocumentAnalysisService.update_status(result_id, 'failed', error_message='未配置模型')
            return

        # 创建 LLM 客户端
        from api.db.services.llm_service import LLMBundle
        llm_client = LLMBundle(tenant_id, llm_config)

        # 创建分析器 - 使用数据库中配置的 prompt_templates
        doc_type = template.doc_type or "bid"
        prompt_templates = template.prompt_templates or {}
        analyzer = DocumentAnalyzer(llm_client, doc_type=doc_type, prompt_templates=prompt_templates)

        # 定义进度回调
        def progress_callback(current, total, stage, message):
            # 检查是否被取消
            if DocumentAnalysisService.has_canceled(result_id):
                logger.info(f"Analysis task {result_id} was canceled by user")
                raise Exception("用户取消分析")

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
    success, doc = DocumentService.get_by_id(document_id)
    if not success or not doc:
        return get_error_data_result(message='文档不存在')

    # 获取模板
    template_id = data.get('template_id')
    if template_id:
        template = AnalysisTemplateService.get_by_id(template_id)
    else:
        # 根据知识库配置获取默认模板
        kb_success, kb = KnowledgebaseService.get_by_id(doc.kb_id)
        doc_type = kb.parser_id if kb_success and kb else 'general'
        template = AnalysisTemplateService.get_default_by_type(doc_type)

    if not template:
        return get_error_data_result(message='未找到合适的分析模板')

    # 获取租户信息
    tenant_id = data.get('tenant_id') or doc.created_by

    # 删除该文档的旧分析结果（如果存在）
    # 这样确保同一文档只有最新的分析结果
    try:
        deleted_count = DocumentAnalysisService.delete_by_document(document_id)
        if deleted_count > 0:
            logger.info(f"Deleted {deleted_count} old analysis results for document {document_id}")
    except Exception as e:
        logger.warning(f"Failed to delete old analysis results for document {document_id}: {e}")
        # 继续执行，不阻塞新分析的创建

    # 创建新的分析记录
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

    # 启动后台任务（在线程池中执行，避免阻塞事件循环）
    # 不等待结果，fire-and-forget 模式
    asyncio.create_task(thread_pool_exec(_run_analysis_task_sync,
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


@manager.route('/documents/<document_id>/analysis/cancel', methods=['POST'])  # noqa: F821
@login_required
async def cancel_document_analysis(document_id):
    """取消正在进行的文档分析"""
    data = await get_request_json() or {}
    task_id = data.get('task_id')

    if not task_id:
        # 如果没有指定 task_id，尝试获取当前正在进行的分析
        result = DocumentAnalysisService.get_by_document(document_id)
        if result and result.status in ['pending', 'running']:
            task_id = result.id

    if not task_id:
        return get_error_data_result(message='没有找到正在进行的分析任务')

    # 检查任务状态
    result = DocumentAnalysisService.get_by_id(task_id)
    if not result:
        return get_error_data_result(message='分析任务不存在')

    if result.status not in ['pending', 'running']:
        return get_error_data_result(message=f'任务状态为 {result.status}，无法取消')

    # 设置取消标记
    DocumentAnalysisService.cancel_analysis(task_id)
    DocumentAnalysisService.update_status(task_id, 'failed', error_message='用户取消分析')

    return get_result(message='已取消分析任务')


@manager.route('/documents/<document_id>/analysis', methods=['DELETE'])  # noqa: F821
@login_required
async def delete_document_analysis(document_id):
    """删除文档分析结果"""
    count = DocumentAnalysisService.delete_by_document(document_id)
    return get_result(message=f'已删除 {count} 条分析记录')


@manager.route('/analysis-results', methods=['GET'])  # noqa: F821
@login_required
async def list_analysis_results():
    """获取租户下所有已完成的文档分析列表

    Query参数:
    - page: 页码，默认 1
    - page_size: 每页数量，默认 20
    - kb_id: 可选，过滤特定知识库的分析结果
    """
    from api.apps import current_user
    from api.db.db_models import DocumentAnalysisResult

    page = int(request.args.get('page', 1))
    page_size = int(request.args.get('page_size', 20))
    kb_id = request.args.get('kb_id')

    # 获取当前租户 ID (在 RAGFlow 中用户和租户是 1:1 关系)
    tenant_id = current_user.id

    try:
        # 构建查询
        query = (DocumentAnalysisResult
                .select()
                .where(DocumentAnalysisResult.status == 'completed')
                .where(DocumentAnalysisResult.tenant_id == tenant_id))

        # 可选的知识库过滤
        if kb_id:
            query = query.where(DocumentAnalysisResult.kb_id == kb_id)

        # 获取总数（用于分页）
        total_count = query.count()

        # 分页查询
        results = (query
                  .order_by(DocumentAnalysisResult.create_time.desc())
                  .paginate(page, page_size))

        # 转换为响应格式
        data = []
        for result in results:
            # 获取模板名称
            template = AnalysisTemplateService.get_by_id(result.template_id)

            # 处理时间戳：create_time 是 BigIntegerField，需要转换为 datetime
            create_time_str = None
            if result.create_time:
                try:
                    from datetime import datetime
                    # 假设是毫秒级时间戳
                    create_time_str = datetime.fromtimestamp(result.create_time / 1000).isoformat()
                except:
                    # 如果转换失败，直接使用原始值
                    create_time_str = str(result.create_time)

            data.append({
                'id': result.id,
                'document_id': result.document_id,
                'doc_name': result.doc_name,
                'template_id': result.template_id,
                'template_name': template.name if template else '未知模板',
                'kb_id': result.kb_id,
                'create_time': create_time_str,
                'progress': result.progress,
                'status': result.status
            })

        return get_result(data={
            'total': total_count,
            'page': page,
            'page_size': page_size,
            'data': data
        })

    except Exception as e:
        logger.error(f"Failed to list analysis results: {e}", exc_info=True)
        return get_error_data_result(message=f'获取分析结果列表失败: {str(e)}')
