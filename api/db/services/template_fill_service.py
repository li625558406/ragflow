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
from common.time_utils import current_timestamp

logger = logging.getLogger(__name__)

# 模板状态机白名单：任何写库路径只允许这三种状态（防任意字符串落库）
TEMPLATE_STATUSES = ("draft", "published", "disabled")

# 填写任务状态全集（白名单，防任意字符串落库）
TASK_STATUSES = ("pending", "retrieving", "generating", "rendering", "done", "partial", "failed", "cancelled")
# 填写任务状态机：只允许沿 pipeline 顺序推进或进入 failed；终态（done/partial/failed）无出边
# 注意：cancel_running 允许 rendering→cancelled（渲染期取消无需等渲染完成），与白名单口径有意旁路
_TASK_TRANSITS = {
    "pending": {"retrieving", "failed", "cancelled"},
    "retrieving": {"generating", "failed", "cancelled"},
    "generating": {"rendering", "failed", "cancelled"},
    "rendering": {"done", "partial", "failed"},
    "done": set(), "partial": set(), "failed": set(), "cancelled": set(),
}

# 终态集合（含 cancelled）：progress 端点 stalled 判定、画布工具轮询退出等共用口径，
# 各处自行定义易漂移（历史上 tool 侧漏 cancelled），统一导入此常量
TERMINAL_TASK_STATUSES = ("done", "partial", "failed", "cancelled")

# object name 中用户文件名片段的最大长度（MinIO object 名总长上限远大于此，
# 截断主要为防极端超长文件名 + 保留扩展名可读性）
_FILENAME_MAX_LEN = 128

# find_running 复用中间态行的年龄窗口（毫秒）：docker restart 部署等场景下执行中
# 任务行可能永久停中间态（后台线程已死、无人强置终态），超窗的中间态行视为僵尸
# 不再复用观察（否则画布节点每 1.5s 轮询到天荒地老）
_RUNNING_REUSE_WINDOW_MS = 2 * 3600 * 1000
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


# 公开别名：跨模块（如 template_api 桥接下载）统一用公开名导入，避免依赖下划线私有实现
sanitize_filename = _sanitize_filename


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
    def get_list_page(cls, tenant_id: str, keyword: str = "", status: str = "", page: int = 1, size: int = 10):
        # 分页参数下界钳制：peewee paginate 对 page=0 静默当第 1 页，page<0 会生成
        # 负 OFFSET → MySQL 1064 → 500；size<=0 同理。上界防单次拉全表。
        page = max(1, int(page or 1))
        size = min(max(1, int(size or 10)), 100)
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
    def set_detect_status(cls, template_id: str, status: str, error: str = "") -> bool:
        """后台 AI 识别状态流转（detect-async 端点专用，不校验租户——调用方已核权）。"""
        if status not in ("none", "running", "done", "failed"):
            return False
        return cls.model.update(detect_status=status,
                                detect_error=(error or "")[:512]).where(
            cls.model.id == template_id).execute() > 0

    @classmethod
    @DB.connection_context()
    def delete_template(cls, template_id: str, tenant_id: str) -> tuple:
        """删除模板（仅 draft/disabled）：级联删除其全部填写任务与成稿对象。

        2026-09-17 语义变更：原先「有填写任务记录即拒删」（历史成稿下载依赖
        bucket=template_id 的对象），但 C端流程删除（flow_service.delete_flow
        硬删）从不回收 tpl_fill_task，且任务行与流程无可靠外键（flow_instance_id
        2026-09-17 前全库空串、之后存 canvas session id 亦非 flow id）——
        「流程删掉范本就能删」在数据上只能靠删范本时级联清理任务实现；范本
        删除后历史成稿本就无从下载（bucket 随范本一起清），任务行与成稿对象
        一并删除才自洽：
          - 真源成稿 {template_id}/{result_file_id}
          - 派生下载副本 {tenant_id}-downloads/tplfill-{task_id}（仅真源存在时才可能存在）

        清理顺序：逐版本+逐任务删 MinIO 对象（失败仅告警不阻塞）→ 事务内
        锁模板行 → 删任务行 → 删版本行 → 删主表行（同事务，杜绝任务/版本行
        删掉而主表行删失败留下的损坏态）。MinIO rm 刻意留在事务外、DB 删除
        之前：rm 失败不回滚 DB 的语义不变。

        ⚠️ 事务内必须用裸查询（不得调 get_owned 等带 @DB.connection_context
        的方法）：该装饰器退出时无条件 db.close()，而连接上有 atomic 开着的事务时
        close 会抛 OperationalError('Attempting to close database while transaction
        is open.')——真实 MySQL 已踩坑（线上删除模板 500），SQLite/单测桩不触发。
        """
        tpl = cls.get_owned(template_id, tenant_id)
        if not tpl:
            return False, "模板不存在"
        if tpl.status not in ("draft", "disabled"):
            return False, "已发布模板不可删除，请先停用"
        tasks = list(TplFillTask.select().where(
            TplFillTask.template_id == template_id))
        for ver in TplTemplateVersion.select().where(TplTemplateVersion.template_id == template_id):
            for obj in (ver.original_file_id, ver.render_file_id):
                if obj:
                    try:
                        settings.STORAGE_IMPL.rm(template_id, obj)
                    except Exception:  # noqa: BLE001 — 各存储实现异常类型不一，删除失败只告警不阻塞
                        logger.warning("template fill: delete storage obj failed: %s/%s",
                                       template_id, obj)
        for t in tasks:
            if not t.result_file_id:
                continue  # 无成稿：真源与派生副本都不存在
            for bucket, obj in (
                (template_id, t.result_file_id),
                (f"{t.tenant_id}-downloads", f"tplfill-{t.id}"),
            ):
                try:
                    settings.STORAGE_IMPL.rm(bucket, obj)
                except Exception:  # noqa: BLE001 — 同上，派生副本可能本就不存在
                    logger.warning("template fill: delete fill-task obj failed: %s/%s",
                                   bucket, obj)
        with DB.atomic():
            # 行锁锁住模板行（TOCTOU 闭合）；以下删除全为裸查询（复用事务连接），
            # 理由见 docstring ⚠️ 段
            if not cls.model.select().where(
                    (cls.model.id == template_id) & (cls.model.tenant_id == tenant_id)
            ).for_update().first():
                return False, "模板不存在"
            TplFillTask.delete().where(
                TplFillTask.template_id == template_id).execute()
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
    @DB.connection_context()
    def get_by_id_checked(cls, template_id: str, version_id: str):
        """按版本行主键取版本：template_id + id 双条件（防跨模板引用脏数据），
        不存在/入参为空返回 None。填写任务钉版本用（历史任务按当时版本复现）。"""
        if not version_id:
            return None
        return cls.model.select().where(
            (cls.model.id == version_id) & (cls.model.template_id == template_id)).first()

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

    @staticmethod
    def _merge_defaults(items: list, prev_placeholders) -> list:
        """占位符默认值合并（就地修改合并，save_placeholders 落库前调用）。优先级：
        1. 条目显式携带 default_value 字段（前端回显/默认值编辑链路）→ strip + 截断
           后原样保留（含"显式清空"：value 为 null/空串 = 用户清了默认值，不得再派生）；
        2. 同 key 旧版本含 default_value 键 → 原样继承（含显式清空态，跨保存持久：
           用户清空默认值落库后，即使下次保存前端不回显该字段也不会"复活"）；
        3. 否则从 anchor 派生（已填范本识别时零成本提取现值，source=detected）。
        就地修改并返回 items。"""
        from rag.svr.template_fill.detector import MAX_ANCHOR_LEN, derive_default_from_anchor
        prev_map = {it.get("key"): it for it in (prev_placeholders or [])
                    if isinstance(it, dict) and it.get("key")}
        for it in items:
            if not isinstance(it, dict):
                continue
            key = it.get("key")
            if not key:
                continue
            if "default_value" in it:
                val = str(it.get("default_value") or "").strip()[:MAX_ANCHOR_LEN]
                it["default_value"], it["default_source"] = val, (
                    str(it.get("default_source") or "") or ("manual" if val else ""))
                continue
            prev = prev_map.get(key) or {}
            if "default_value" in prev:
                it["default_value"] = str(prev.get("default_value") or "").strip()
                it["default_source"] = str(prev.get("default_source") or "")
                if it["default_value"] and not it["default_source"]:
                    it["default_source"] = "manual"
                continue
            derived = derive_default_from_anchor(it.get("anchor"))
            it["default_value"] = derived
            it["default_source"] = "detected" if derived else ""
        return items

    @staticmethod
    def _sediment_into_placeholders(placeholders: list, values: dict,
                                    only_keys: set,
                                    override_keys: set | None = None) -> bool:
        """产值沉淀纯逻辑：**只处理 key ∈ only_keys** 的非空值，写入 default_value
        （source=auto）。manual 不覆盖，除非 key ∈ override_keys（用户在确认卡片显式直填）。

        `only_keys` 为**必填**：调用方必须明确交出「本轮用户显式确认/改动的 key 集合」。
        刻意不给 None 默认值——`None`（不限制）与 `set()`（一个都不写）语义天差地别，
        留默认值就等于给「静默全量写」留后门（本接口的存在意义正是根除该缺陷）。
        `only_keys` 含不存在的 key 不报错（按 placeholders 侧遍历，天然忽略）。

        空值（渲染留空）不沉淀——不得抹掉历史默认值。返回是否有变更。"""
        from rag.svr.template_fill.detector import MAX_ANCHOR_LEN  # 与 _merge_defaults 同源同写法
        only = only_keys or set()
        override = override_keys or set()
        changed = False
        for it in placeholders:
            key = it.get("key") if isinstance(it, dict) else None
            if not key:
                continue
            if key not in only:
                continue
            val = (values or {}).get(key)
            if val in (None, ""):
                continue
            if str(it.get("default_source") or "") == "manual" and key not in override:
                continue
            new_val = str(val).strip()[:MAX_ANCHOR_LEN]  # LLM 长输出截断，对齐 save 层入库口径
            if not new_val:
                continue  # 剥空白后为空 = 空值，不沉淀（不得抹掉历史默认值）
            if it.get("default_value") == new_val and it.get("default_source") == "auto":
                continue
            it["default_value"] = new_val
            it["default_source"] = "auto"
            changed = True
        return changed

    @classmethod
    @DB.connection_context()
    def sediment_defaults(cls, template_id: str, version_id: str, values: dict,
                          only_keys: set, override_keys: set | None = None) -> bool:
        """把产值沉淀为该版本默认值——**仅当用户显式点「写回范本库」按钮时调用**
        （填写成功不再自动沉淀）。只写 `only_keys` 内的 key。失败由调用方兜底。
        返回是否有变更；版本行不存在返回 False。

        已知竞态（未加行锁，本次不修）：select → 内存改 → save() 整列覆盖，无
        `for_update()`。两个任务对同一版本并发点按钮时，后写者的 placeholders 快照
        会盖掉先写者的新键（丢更新）。概率低、后果自愈（再点一次即可补），故保持
        与改造前同形；若要修，在 DB.atomic() 内对版本行 for_update() 后再改。"""
        ver = cls.model.select().where(
            (cls.model.id == version_id) & (cls.model.template_id == template_id)).first()
        if ver is None:
            return False
        placeholders = ver.placeholders or []
        if not cls._sediment_into_placeholders(placeholders, values, only_keys, override_keys):
            return False
        ver.placeholders = placeholders
        ver.save()
        return True

    @staticmethod
    def _apply_defaults_edits(placeholders: list, defaults: dict) -> tuple:
        """B端默认值编辑纯逻辑：{key: value}，空串=清空。未知 key 整体拒绝
        （防手改请求错别字静默丢编辑）。就地修改。返回 (ok, error_msg)。

        对抗点说明：defaults 的 key 理论上可能是非字符串（自定义对象等），
        `k not in keys` 是纯哈希成员判断（keys 为字符串集合），非 hashable 的
        key 会抛 TypeError 而非静默通过，非字符串但 hashable 的 key 必然不在
        keys 中 → 走 unknown 拒绝路径，两种情况都不会误改占位符。

        非字符串 falsy 值（0/false）视为清空，与文本语义一致。"""
        from rag.svr.template_fill.detector import MAX_ANCHOR_LEN  # 与 _merge_defaults 同源同写法
        keys = {it.get("key") for it in placeholders if isinstance(it, dict)}
        unknown = [k for k in defaults if k not in keys]
        if unknown:
            return False, f"未知填写点 key: {', '.join(str(k) for k in unknown[:5])}"
        for it in placeholders:
            if not isinstance(it, dict):  # 非 dict 项防御：keys 侧已过滤，循环侧对称兜底
                continue
            k = it.get("key")
            if k not in defaults:
                continue
            # 截断对齐 MAX_ANCHOR_LEN，维持「DB 中 default_value ≤ 500」不变量
            val = str(defaults[k] or "").strip()[:MAX_ANCHOR_LEN]
            it["default_value"] = val
            it["default_source"] = "manual" if val else ""
        return True, ""

    @classmethod
    @DB.connection_context()
    def update_defaults(cls, template_id: str, defaults: dict) -> tuple:
        """B端默认值编辑：只改 latest 版本 placeholders 内的 default_value/default_source，
        不动 MinIO 文件、不升版本（默认值是元数据，render/original 无关）。
        返回 (ok, msg)。"""
        ver = cls.latest(template_id)
        if ver is None:
            return False, "模板版本不存在"
        placeholders = ver.placeholders or []
        ok, msg = cls._apply_defaults_edits(placeholders, defaults or {})
        if not ok:
            return False, msg
        ver.placeholders = placeholders
        ver.save()
        return True, ""

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
        placeholders = cls._merge_defaults(placeholders, ver.placeholders)
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


class TplFillTaskService(CommonService):
    model = TplFillTask

    @classmethod
    def can_transit(cls, cur, nxt) -> bool:
        """状态机白名单判定（纯函数，不触库）：nxt 必须是 cur 的合法后继。"""
        return nxt in _TASK_TRANSITS.get(cur, set())

    @classmethod
    @DB.connection_context()
    def get_owned(cls, task_id: str, tenant_id: str):
        """取租户内填写任务，不存在/越权返回 None（id+tenant_id 双条件）。"""
        return cls.model.select().where(
            (cls.model.id == task_id) & (cls.model.tenant_id == tenant_id)).first()

    @classmethod
    @DB.connection_context()
    def get_list_page(cls, tenant_id: str, status: str = "", page: int = 1, size: int = 10):
        # 与 TplTemplateService.get_list_page 同款钳制：防负 OFFSET 500、防单次拉全表
        page = max(1, int(page or 1))
        size = min(max(1, int(size or 10)), 100)
        q = cls.model.select().where(cls.model.tenant_id == tenant_id)
        if status:
            q = q.where(cls.model.status == status)
        total = q.count()
        rows = q.order_by(cls.model.create_time.desc()).paginate(page, size)
        return [r.to_dict() for r in rows], total

    @classmethod
    @DB.connection_context()
    def exists_for(cls, template_id: str) -> bool:
        """该模板是否存在填写任务记录（limit(1) 存在性，删除守卫用）。"""
        return cls.model.select().where(
            cls.model.template_id == template_id).limit(1).exists()

    @classmethod
    @DB.connection_context()
    def update_status(cls, task_id: str, cur: str, nxt: str, **extra) -> bool:
        """乐观状态转移：can_transit 白名单前置 + where 带 status==cur（CAS 语义），
        防两执行器并发接管或终态被覆盖；extra（error/values/evidence/result_file_id 等）
        合入同一笔 update。转移被拒/行已被他人改走 → False，调用方必须放弃。"""
        if not cls.can_transit(cur, nxt):
            return False
        fields = {"status": nxt}
        fields.update(extra)
        return cls.model.update(**fields).where(
            cls.model.id == task_id, cls.model.status == cur).execute() > 0

    @classmethod
    @DB.connection_context()
    def cancel_running(cls, task_id: str) -> bool:
        """把未终态任务置 cancelled（画布停止/取消路径用）。where 不带 status==cur
        （取消可能发生在任一中间态），但用 in_ 白名单限定中间态，终态行不受影响。
        注：rendering→cancelled 与 _TASK_TRANSITS 白名单口径有意旁路——渲染期取消
        无需等渲染完成，直接置 cancelled（渲染稿成孤儿对象由人工/TTL 兜底）。
        返回 False 表示行已是终态（未被本次取消改动），调用方据此决定快照口径。"""
        return cls.model.update(status="cancelled", error="画布已停止，任务被取消").where(
            cls.model.id == task_id,
            cls.model.status.in_(("pending", "retrieving", "generating", "rendering"))).execute() > 0

    @classmethod
    @DB.connection_context()
    def find_running(cls, template_id: str, tenant_id: str):
        """该范本在租户内是否已有**画布发起的**执行中任务（画布重复发起时复用观察，
        不重复起线程）。取最新一条；无则 None。只复用最近 _RUNNING_REUSE_WINDOW_MS
        内创建的中间态行：超龄中间态行是部署重启遗留的僵尸（线程已死无人收口），
        复用会导致节点无限轮询，改为新建任务重跑。

        必须限定 source="canvas"：只有画布节点写入保留键（_changed_keys/_direct_values），
        executor 的 is_canvas 门控据此启用直填覆盖/检索收窄。若复用了同范本的对话或
        B端任务，那些用户在确认卡上的勾选/直填决策会被整批丢弃（按全量 LLM 重跑），
        且该行终态后成稿行的「写回范本库」按钮必然报「缺少确认记录」。"""
        return cls.model.select().where(
            (cls.model.template_id == template_id)
            & (cls.model.tenant_id == tenant_id)
            & (cls.model.source == "canvas")
            & cls.model.status.in_(("pending", "retrieving", "generating", "rendering"))
            & (cls.model.create_time >= current_timestamp() - _RUNNING_REUSE_WINDOW_MS)
        ).order_by(cls.model.create_time.desc()).first()

    @classmethod
    @DB.connection_context()
    def latest_done(cls, template_id: str, tenant_id: str):
        """该范本在租户内最新一条 done 填写任务（对话 modify 就地修改的定位目标）。
        只认 done：failed/cancelled 没有可用成稿，就地修改无从谈起。无则 None。

        口径刻意是「租户+范本」的宽匹配：它服务的是**定位**（用户说「把 XX 改成 YY」
        时找最近那份成稿），跨会话找到用户刚填的稿正是期望行为。**不要**拿它当
        增量填写的基线来源——基线必须同工作上下文，见 latest_done_in_context。"""
        return cls.model.select().where(
            (cls.model.template_id == template_id)
            & (cls.model.tenant_id == tenant_id)
            & (cls.model.status == "done")
        ).order_by(cls.model.create_time.desc()).first()

    @classmethod
    @DB.connection_context()
    def latest_done_in_context(cls, template_id: str, tenant_id: str, context_id: str):
        """同范本 + **同一工作上下文**最新一条 done 成稿——增量填写的基线来源。

        context_id 是本次运行的工作上下文（会话 id：流程页即该流程的影子会话 id）。
        与 latest_done 的分工是刻意的：latest_done 服务于「定位」，本方法服务于
        「继承」。

        **Why 必须收窄**：增量的语义是「在这份成稿上继续改」。而 latest_done 只有
        租户+范本两个维度，`tpl_fill_task.flow_instance_id` 又长期写空串，于是
        2026-09-17 实测事故：demo02 里用户手动 modify 出的值（写回该行
        values.render）成了全新流程 demo03 的基线 → 该字段既不进 LLM 白名单也不
        检索（被塞进 _retrieve_skip_keys）→ 新流程产出的是上一个流程的手改值。
        这与「自动沉淀默认值」是同一类缺陷（把上一轮输出自动变成下一轮输入）。

        **无上下文信号（context_id 为空）一律不做增量**：宁走全量重填，也不跨
        一个无法证明的边界继承（全量是安全默认，见增量设计文档「无 done baseline
        → 全量重填」）。"""
        if not context_id:
            return None
        return cls.model.select().where(
            (cls.model.template_id == template_id)
            & (cls.model.tenant_id == tenant_id)
            & (cls.model.flow_instance_id == context_id)
            & (cls.model.status == "done")
        ).order_by(cls.model.create_time.desc()).first()

    @classmethod
    @DB.connection_context()
    def patch_values(cls, task_id: str, values: dict) -> bool:
        """终态任务 values 字段就地更新（对话 modify 就地改字段后回写合并结果）。
        只按 id 定位、不改 status（终态行无并发执行器，无 CAS 需要）。"""
        return cls.model.update(values=values).where(
            cls.model.id == task_id).execute() > 0
