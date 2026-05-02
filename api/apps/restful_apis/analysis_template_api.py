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

from api.apps import login_required
from api.db.services.analysis_template_service import AnalysisTemplateService
from api.utils.api_utils import (
    get_request_json,
    get_result,
    get_error_data_result,
    add_tenant_id_to_kwargs,
)
from common.misc_utils import get_uuid


@manager.route('/analysis-templates', methods=['GET'])  # noqa: F821  # noqa: F821
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


@manager.route('/analysis-templates/<template_id>', methods=['GET'])  # noqa: F821
@login_required
async def get_analysis_template(template_id):
    """获取分析模板详情"""
    template = AnalysisTemplateService.get_by_id(template_id)

    if not template:
        return get_error_data_result(message='模板不存在')

    return get_result(data=template.to_dict())


@manager.route('/analysis-templates', methods=['POST'])  # noqa: F821
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


@manager.route('/analysis-templates/<template_id>', methods=['PUT'])  # noqa: F821
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


@manager.route('/analysis-templates/<template_id>', methods=['DELETE'])  # noqa: F821
@login_required
async def delete_analysis_template(template_id):
    """删除分析模板"""
    success, error = AnalysisTemplateService.delete(template_id)

    if not success:
        return get_error_data_result(message=error or '删除失败')

    return get_result(message='删除成功')
