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
from peewee import fn

from api.db.db_models import DB, DocumentAnalysisTemplate
from api.db.services.common_service import CommonService
from common.misc_utils import get_uuid


class AnalysisTemplateService(CommonService):
    model = DocumentAnalysisTemplate

    @classmethod
    @DB.connection_context()
    def get_list(cls, doc_type=None, tenant_id=None, page=1, page_size=20):
        """获取模板列表"""
        query = cls.model.select()

        if doc_type:
            query = query.where(cls.model.doc_type == doc_type)

        # 获取全局模板或租户自己的模板
        if tenant_id:
            query = query.where((cls.model.tenant_id.is_null()) | (cls.model.tenant_id == tenant_id))
        else:
            query = query.where(cls.model.tenant_id.is_null())

        total = query.count()
        templates = query.order_by(cls.model.is_default.desc(), cls.model.create_time.desc()) \
            .paginate(page, page_size)

        return list(templates.dicts()), total

    @classmethod
    @DB.connection_context()
    def get_by_id(cls, template_id):
        """根据ID获取模板"""
        try:
            return cls.model.get_by_id(template_id)
        except:
            return None

    @classmethod
    @DB.connection_context()
    def get_default_by_type(cls, doc_type):
        """获取指定类型的默认模板"""
        try:
            return cls.model.get(
                (cls.model.doc_type == doc_type) &
                (cls.model.is_default == True) &
                (cls.model.tenant_id.is_null())
            )
        except:
            return None

    @classmethod
    @DB.connection_context()
    def create(cls, data):
        """创建模板"""
        if 'id' not in data or not data['id']:
            data['id'] = get_uuid()
        return cls.model.create(**data)

    @classmethod
    @DB.connection_context()
    def update(cls, template_id, data):
        """更新模板"""
        data['id'] = template_id
        return cls.model.update(**data).where(cls.model.id == template_id).execute() > 0

    @classmethod
    @DB.connection_context()
    def delete(cls, template_id):
        """删除模板（系统模板不可删除）"""
        template = cls.get_by_id(template_id)
        if template and template.is_system:
            return False, "系统模板不可删除"
        return cls.model.delete().where(cls.model.id == template_id).execute() > 0, None
