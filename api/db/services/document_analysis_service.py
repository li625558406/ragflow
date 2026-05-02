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
from api.db.db_models import DB, DocumentAnalysisResult
from api.db.services.common_service import CommonService
from common.misc_utils import get_uuid


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
    @DB.connection_context()
    def update_status(cls, result_id, status, progress=None, result=None, error_message=None):
        """更新分析状态"""
        update_data = {'status': status}
        if progress is not None:
            update_data['progress'] = progress
        if result is not None:
            update_data['result'] = result
        if error_message is not None:
            update_data['error_message'] = error_message

        return cls.model.update(**update_data).where(cls.model.id == result_id).execute() > 0

    @classmethod
    @DB.connection_context()
    def delete_by_document(cls, document_id):
        """删除文档的所有分析结果"""
        return cls.model.delete().where(cls.model.document_id == document_id).execute()
