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
"""模板填写：模板与版本 Service。

存储约定：MinIO bucket=template_id，object name 带版本前缀
（v1_original_xxx / v{N}_render.docx|xlsx）。
"""
import logging

from api.db.db_models import DB, TplTemplate, TplTemplateVersion
from api.db.services.common_service import CommonService
from common import settings

logger = logging.getLogger(__name__)

# object name 中用户文件名片段的最大长度（MinIO object 名总长上限远大于此，
# 截断主要为防极端超长文件名 + 保留扩展名可读性）
_FILENAME_MAX_LEN = 128
# original_filename 列 CharField(max_length=256)，入库前必须截断避免 strict mode 报错
_DB_FILENAME_MAX_LEN = 256


def _sanitize_filename(name: str, max_len: int = _FILENAME_MAX_LEN) -> str:
    """清洗文件名为安全的 MinIO object 名片段。

    - 统一反斜杠后取 base 名：Windows 下 os.path.basename 不识别 '/'，
      直接用 replace + rsplit 跨平台取最后一段，避免 '/' 被 MinIO 当目录前缀；
    - 去首尾空白；清洗后为空则兜底 'template'；
    - 超长截断时保留扩展名。
    """
    base = (name or "").replace("\\", "/").rsplit("/", 1)[-1].strip()
    if not base:
        return "template"
    if len(base) <= max_len:
        return base
    stem, dot, ext = base.rpartition(".")
    if dot and stem and 0 < len(ext) <= 10:
        return stem[: max_len - len(ext) - 1] + "." + ext
    return base[:max_len]


def _storage_put(bucket: str, obj_name: str, blob: bytes):
    """写入 MinIO 并校验结果：put 失败时多数实现返回 None，此时禁止继续
    落库（否则 DB 行指向不存在的对象，错误延迟到下游才暴露）。"""
    r = settings.STORAGE_IMPL.put(bucket, obj_name, blob)
    if r is None:
        logger.error("template fill: storage put failed, bucket=%s obj=%s", bucket, obj_name)
        raise RuntimeError(f"storage put failed: bucket={bucket}, object={obj_name}")
    return r


class TplTemplateService(CommonService):
    model = TplTemplate

    @classmethod
    @DB.connection_context()
    def get_list_page(cls, tenant_id: str, keyword: str = "", status: str = "", page: int = 1, size: int = 20):
        # 分页参数下界钳制：peewee paginate 对 page=0 静默当第 1 页，page<0 会生成
        # 负 OFFSET → MySQL 1064 → 500；size<=0 同理。上界防单次拉全表。
        page = max(1, int(page or 1))
        size = min(max(1, int(size or 20)), 100)
        q = cls.model.select().where(cls.model.tenant_id == tenant_id)
        if keyword:
            q = q.where(cls.model.name.contains(keyword))
        if status:
            q = q.where(cls.model.status == status)
        total = q.count()
        rows = q.order_by(cls.model.create_time.desc()).paginate(page, size)
        return [r.to_dict() for r in rows], total

    @classmethod
    @DB.connection_context()
    def get_owned(cls, template_id: str, tenant_id: str):
        """取租户内模板，不存在/越权返回 None。

        peewee 的 Model.get_or_none 支持位置条件参数，但用 `&` 合并成
        单表达式最稳妥（与 common_service.get_by_id 的单条件位置传参惯例一致）。
        """
        return cls.model.get_or_none((cls.model.id == template_id) & (cls.model.tenant_id == tenant_id))

    @classmethod
    @DB.connection_context()
    def set_status(cls, template_id: str, tenant_id: str, status: str):
        return cls.model.update(status=status).where(
            cls.model.id == template_id, cls.model.tenant_id == tenant_id).execute()


class TplTemplateVersionService(CommonService):
    model = TplTemplateVersion

    @classmethod
    @DB.connection_context()
    def latest(cls, template_id: str):
        return (cls.model.select()
                .where(cls.model.template_id == template_id)
                .order_by(cls.model.version.desc())
                .first())

    @classmethod
    def create_initial_version(cls, template_id: str, filename: str, blob: bytes):
        """上传后建 v1，原件入 MinIO。

        已知不对称：先 put MinIO 再 insert DB，insert 失败时 MinIO 中会残留
        孤儿对象（不回滚）——对象无 DB 行引用，仅占存储，不影响业务读取。
        """
        safe_name = _sanitize_filename(filename)
        obj_name = f"v1_original_{safe_name}"
        _storage_put(template_id, obj_name, blob)
        # DB 列 max_length=256，入库前截断（MinIO object name 已单独清洗截断）
        db_filename = (filename or "")[:_DB_FILENAME_MAX_LEN]
        try:
            return cls.insert(template_id=template_id, version=1,
                              original_filename=db_filename, original_file_id=obj_name, placeholders=[])
        except Exception:
            logger.exception("template fill: insert initial version failed after storage put, "
                             "template=%s object=%s (orphan object left in storage)", template_id, obj_name)
            raise

    @classmethod
    @DB.connection_context()
    def save_placeholders(cls, template: dict, placeholders: list) -> dict:
        """人工确认后：生成带 {{key}} 的 render 副本入 MinIO，与 placeholders 原子落库。

        docx/xlsx 的 apply 函数对脏输入（anchor 不存在、非法 sheet/coord）为静默跳过语义，
        本层不做占位符校验（API 层 validate_placeholders 负责）；租户校验由调用方负责。
        """
        tpl_id = template["id"]
        ver = cls.latest(tpl_id)
        if ver is None:
            raise RuntimeError(f"no version found for template {tpl_id}")
        # MinIO conn.get 返回 r.read() 即 bytes；对象不存在/读取失败返回 None，此处显式兜底
        blob = settings.STORAGE_IMPL.get(tpl_id, ver.original_file_id)
        if not blob:
            raise RuntimeError(f"original file missing in storage: bucket={tpl_id}, object={ver.original_file_id}")
        if template["file_type"] == "docx":
            from rag.svr.template_fill.docx_utils import apply_docx_placeholders
            render = apply_docx_placeholders(blob, placeholders)
            render_name = f"v{ver.version}_render.docx"
        else:
            from rag.svr.template_fill.xlsx_utils import apply_xlsx_placeholders
            render = apply_xlsx_placeholders(blob, placeholders)
            render_name = f"v{ver.version}_render.xlsx"
        _storage_put(tpl_id, render_name, render)
        ver.render_file_id = render_name
        ver.placeholders = placeholders  # ListField.db_value 在 save 时 json 序列化，直接赋 list 即可
        ver.save()
        return ver.to_dict()
