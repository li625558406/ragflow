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

from api.db.db_models import DB, TplFillTask, TplTemplate, TplTemplateVersion
from api.db.services.common_service import CommonService
from common import settings
from common.misc_utils import get_uuid

logger = logging.getLogger(__name__)

# 模板状态机白名单：任何写库路径只允许这三种状态（防任意字符串落库）
TEMPLATE_STATUSES = ("draft", "published", "disabled")

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


def _storage_get(bucket: str, obj_name: str):
    """读取 MinIO 对象（与 _storage_put 对称）：对象不存在/读取失败返回 None，
    由调用方决定兜底语义（升版返回错误、草稿改写抛异常）。"""
    return settings.STORAGE_IMPL.get(bucket, obj_name)


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
    def get_owned(cls, template_id: str, tenant_id: str, for_update: bool = False):
        """取租户内模板，不存在/越权返回 None。

        peewee 的 Model.get_or_none 支持位置条件参数，但用 `&` 合并成
        单表达式最稳妥（与 common_service.get_by_id 的单条件位置传参惯例一致）。
        for_update=True 时加行锁（SELECT ... FOR UPDATE）——调用方须在 DB.atomic()
        事务内使用，锁到事务结束；SQLite 方言下为静默 no-op，不影响单测。
        """
        q = cls.model.select().where((cls.model.id == template_id) & (cls.model.tenant_id == tenant_id))
        if for_update:
            q = q.for_update()
        return q.first()

    @classmethod
    @DB.connection_context()
    def set_status(cls, template_id: str, tenant_id: str, status: str):
        # 白名单前置：非法状态值不触库直接拒绝，防任意字符串写入 status 列
        if status not in TEMPLATE_STATUSES:
            return False
        return cls.model.update(status=status).where(
            cls.model.id == template_id, cls.model.tenant_id == tenant_id).execute() > 0

    @classmethod
    @DB.connection_context()
    def set_latest_version(cls, template_id: str, version: int):
        """升版后同步模板 latest_version 指针（仅内部升版链路调用）。"""
        return cls.model.update(latest_version=version).where(
            cls.model.id == template_id).execute() > 0

    @classmethod
    @DB.connection_context()
    def has_tasks(cls, template_id: str) -> bool:
        """该模板是否存在填写任务记录（有任务即拒删：历史任务下载依赖其 bucket 对象）。"""
        return TplFillTask.select().where(
            TplFillTask.template_id == template_id).exists()

    @classmethod
    @DB.connection_context()
    def delete_template(cls, template_id: str, tenant_id: str) -> tuple:
        """删除模板（仅 draft/disabled 且无填写任务记录）。

        清理顺序：逐版本删 MinIO 对象（bucket=template_id）→ 事务内删版本行 → 删主表行。
        对象删除失败仅告警不阻塞（DB 行残留引用比对象残留危害小，且站点存储故障
        不应永久卡死模板删除）；返回 (ok, msg)。

        事务与锁：has_tasks 复查 → 版本行 DELETE → 主表行 DELETE 包进 DB.atomic()，
        且复查用 for_update 行锁锁住模板行——否则守卫通过后、删除前若并发新建填写
        任务，会删掉仍被历史任务引用的模板（TOCTOU）；两条 DELETE 同事务，也杜绝
        版本行删掉而主表行删失败留下的「零版本模板」损坏态。MinIO rm 循环刻意留在
        事务外、DB 删除之前：rm 失败不回滚 DB 的语义不变。
        """
        tpl = cls.get_owned(template_id, tenant_id)
        if not tpl:
            return False, "模板不存在"
        if tpl.status not in ("draft", "disabled"):
            return False, "已发布模板不可删除，请先停用"
        if cls.has_tasks(template_id):
            return False, "该模板已有填写任务记录，不可删除（历史任务需保留可下载）"
        for ver in TplTemplateVersion.select().where(TplTemplateVersion.template_id == template_id):
            for obj in (ver.original_file_id, ver.render_file_id):
                if obj:
                    try:
                        settings.STORAGE_IMPL.rm(template_id, obj)
                    except Exception:  # noqa: BLE001 — 各存储实现异常类型不一，删除失败只告警不阻塞
                        logger.warning("template fill: delete storage obj failed: %s/%s",
                                       template_id, obj)
        with DB.atomic():
            # 事务内复查（TOCTOU 闭合）：守卫与删除之间的并发写入已由行锁串行化
            if cls.has_tasks(template_id):
                return False, "该模板已有填写任务记录，不可删除（历史任务需保留可下载）"
            if not cls.get_owned(template_id, tenant_id, for_update=True):
                return False, "模板不存在"
            TplTemplateVersion.delete().where(
                TplTemplateVersion.template_id == template_id).execute()
            cls.model.delete().where(cls.model.id == template_id,
                                     cls.model.tenant_id == tenant_id).execute()
        return True, ""


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
    def replace_anchor_to_placeholder(cls, file_type: str, blob: bytes, items: list) -> bytes:
        """锚文本替换为 {{key}}，生成 render blob（纯转换，不做任何 IO）。

        docx/xlsx 的 apply 函数对脏输入（anchor 不存在、非法 sheet/coord）为静默跳过语义，
        本层不做占位符校验（API 层 validate_placeholders 负责）。
        """
        if file_type == "docx":
            from rag.svr.template_fill.docx_utils import apply_docx_placeholders
            return apply_docx_placeholders(blob, items)
        from rag.svr.template_fill.xlsx_utils import apply_xlsx_placeholders
        return apply_xlsx_placeholders(blob, items)

    @classmethod
    def _save_as_new_version(cls, template: dict, ver, placeholders: list) -> tuple:
        """published 模板保存填写点：不改旧版本行，新建 v{N+1} 版本行 + 新 MinIO 对象。

        历史填写任务按当时版本对象复现，published 后再改填写点必须开新版本，
        否则旧任务 render/original 被覆盖后无法追溯。
        """
        tpl_id = template["id"]
        version = ver.version + 1
        ext = template["file_type"]
        # 原件快照：从旧版本原件复制一份作为新版本原件（副本，不移动旧对象）
        blob = _storage_get(tpl_id, ver.original_file_id)
        if not blob:
            return False, "模板原件缺失"
        orig_name = f"v{version}_original.{ext}"
        _storage_put(tpl_id, orig_name, blob)
        render = cls.replace_anchor_to_placeholder(ext, blob, placeholders)
        render_name = f"v{version}_render.{ext}"
        _storage_put(tpl_id, render_name, render)
        try:
            cls.insert(id=get_uuid(), template_id=tpl_id, version=version,
                       original_file_id=orig_name, render_file_id=render_name,
                       placeholders=placeholders,
                       original_filename=getattr(ver, "original_filename", ""))
        except Exception:
            logger.exception("template fill: insert upgraded version failed after storage put, "
                             "template=%s version=%s (orphan objects left in storage)", tpl_id, version)
            raise
        TplTemplateService.set_latest_version(tpl_id, version)
        return True, version

    @classmethod
    @DB.connection_context()
    def save_placeholders(cls, template: dict, placeholders: list) -> tuple:
        """人工确认后保存填写点：生成带 {{key}} 的 render 副本入 MinIO 并落库。

        返回 (ok, info)：ok=False 时 info 为错误文案；ok=True 时 info 为版本行 dict
        （draft 改写）或新版本号（published 升版）。

        draft/disabled 模板：原逻辑，原地改写当前版本行；
        published 模板：新建 v{N+1} 版本行（历史任务可按当时版本复现）。
        本层不做占位符校验（API 层 validate_placeholders 负责）；租户校验由调用方负责。
        """
        tpl_id = template["id"]
        ver = cls.latest(tpl_id)
        if ver is None:
            raise RuntimeError(f"no version found for template {tpl_id}")
        if template.get("status") == "published":
            return cls._save_as_new_version(template, ver, placeholders)
        # MinIO conn.get 返回 r.read() 即 bytes；对象不存在/读取失败返回 None，此处显式兜底
        blob = _storage_get(tpl_id, ver.original_file_id)
        if not blob:
            raise RuntimeError(f"original file missing in storage: bucket={tpl_id}, object={ver.original_file_id}")
        ext = template["file_type"]
        render = cls.replace_anchor_to_placeholder(ext, blob, placeholders)
        render_name = f"v{ver.version}_render.{ext}"
        _storage_put(tpl_id, render_name, render)
        ver.render_file_id = render_name
        ver.placeholders = placeholders  # ListField.db_value 在 save 时 json 序列化，直接赋 list 即可
        ver.save()
        return True, ver.to_dict()
