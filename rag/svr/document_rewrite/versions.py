# rag/svr/document_rewrite/versions.py
# -*- coding: utf-8 -*-
"""重写版本链：chat 场景 doc_rewrite_version 表 + {tenant}-downloads 桶；
flow 场景复用 flow_version 表（source=ai_rewrite，不切 current_version_id）。

DB/存储依赖全部延迟 import（工具注册期安全）。设计 §7：
- (root_id, version_no) 唯一索引 + 取号，并发撞线 IntegrityError → 重试（最多3次）；
- 回退=复制式：历史版 blob 登记为新 version_no，append-only；
- 首次对某成稿重写时先登记 version_no=1（原始成稿，source_type=chat_fill）。
"""
from __future__ import annotations

import logging
import time
from datetime import datetime

logger = logging.getLogger(__name__)

SOURCE_TYPES = ("chat_fill", "flow_version", "rewrite", "rollback")
_FLOW_REWRITE_SOURCE = "ai_rewrite"  # flow_version.source 枚举扩展值
_MAX_REGISTER_RETRY = 3


def _root_id_from_doc_id(doc_id: str) -> str:
    """chat 成稿对象名 → 版本链锚：tplfill-{task_id} → task_id；其余原样。"""
    doc_id = (doc_id or "").strip()
    if doc_id.startswith("tplfill-"):
        return doc_id[len("tplfill-"):]
    return doc_id


def root_id_for_doc(doc_id: str) -> str:
    """doc_id/对象名 → 版本链锚。rewrite-{uuid} 产物对象按 DB 反查 root_id；
    查不到（无链/未知对象）退化为剥 tplfill- 前缀。"""
    doc_id = (doc_id or "").strip()
    if doc_id.startswith("rewrite-"):
        from api.db.db_models import DocRewriteVersion

        row = (
            DocRewriteVersion.select()
            .where(DocRewriteVersion.obj == doc_id)
            .first()
        )
        if row:
            return row.root_id
    return _root_id_from_doc_id(doc_id)


def _chat_bucket(tenant_id: str) -> str:
    return f"{tenant_id}-downloads"


def list_versions(root_id: str) -> list[dict]:
    from api.db.db_models import DocRewriteVersion

    return [
        r.__data__ for r in DocRewriteVersion.select()
        .where(DocRewriteVersion.root_id == root_id)
        .order_by(DocRewriteVersion.version_no.asc())
    ]


def get_version(root_id: str, version_no: int) -> dict | None:
    from api.db.db_models import DocRewriteVersion

    row = (
        DocRewriteVersion.select()
        .where(
            (DocRewriteVersion.root_id == root_id)
            & (DocRewriteVersion.version_no == version_no)
        )
        .first()
    )
    return row.__data__ if row else None


def register_chat_version(tenant_id: str, root_id: str, blob: bytes, file_type: str,
                          base_file_name: str, source_type: str, instruction: str = "",
                          section_title: str = "", force_version_no: int | None = None) -> dict:
    """登记一个 chat 版本：MinIO 写 {tenant}-downloads + doc_rewrite_version 行。

    force_version_no 用于首次登记原始成稿（=1）；正常重写事务取号自动递增。
    对象名与行身份 1:1（rewrite-{行uuid}）：并发撞号的败者重试后写自己的新对象，
    不会覆盖胜者已提交版本的对象内容；失败尝试遗留的孤儿对象无害。
    返回版本行 dict。
    契约：不得在外层 DB.atomic() 事务内调用——内层 atomic 退化为 savepoint，
    重试取号的 SELECT MAX 复用外层快照（REPEATABLE READ）会取到旧号导致假性冲突。
    注意：peewee 的 Model.insert() 返回 Insert 查询而非行实例，此处用 create()
    并显式填 id/时间戳（对齐 CommonService.insert 的行为）。
    """
    from peewee import IntegrityError

    from api.db.db_models import DB, DocRewriteVersion
    from api.db.services.file_service import FileService
    from common.misc_utils import get_uuid
    from common.time_utils import current_timestamp, datetime_format

    if not base_file_name.lower().endswith(f".{file_type}"):
        file_name = f"{base_file_name}.{file_type}"
    else:
        file_name = base_file_name

    last_err: Exception | None = None
    for _attempt in range(_MAX_REGISTER_RETRY):
        try:
            with DB.atomic():
                if force_version_no is not None:
                    version_no = force_version_no
                else:
                    last = (
                        DocRewriteVersion.select()
                        .where(DocRewriteVersion.root_id == root_id)
                        .order_by(DocRewriteVersion.version_no.desc())
                        .first()
                    )
                    version_no = (last.version_no + 1) if last else 1
                row_id = get_uuid()
                obj = f"rewrite-{row_id}"
                FileService.put_blob(tenant_id, obj, blob)
                now_ts = current_timestamp()
                now_dt = datetime_format(datetime.now())
                row = DocRewriteVersion.create(
                    id=row_id,
                    root_id=root_id,
                    version_no=version_no,
                    source_type=source_type,
                    bucket=_chat_bucket(tenant_id),
                    obj=obj,
                    file_name=file_name or obj,
                    file_type=file_type,
                    instruction=instruction or "",
                    section_title=section_title or "",
                    created_by=tenant_id,
                    create_time=now_ts,
                    create_date=now_dt,
                    update_time=now_ts,
                    update_date=now_dt,
                )
                return row.__data__
        except IntegrityError as e:  # 并发取号撞唯一索引 → 退避后重试
            last_err = e
            time.sleep(0.05 * (_attempt + 1))
            logger.warning("[rewrite] version_no conflict root=%s retry=%s", root_id, _attempt)
    raise RuntimeError(f"版本登记并发冲突，请重试：{last_err}")


def ensure_base_version(tenant_id: str, root_id: str, blob: bytes, file_type: str,
                        base_file_name: str) -> None:
    """首次对该成稿重写时补登记 version_no=1（原始成稿），保证链完整可回退。
    已有链则空操作；并发下唯一索引兜底，冲突视为已登记。"""
    if list_versions(root_id):
        return
    try:
        register_chat_version(
            tenant_id, root_id, blob, file_type, base_file_name,
            source_type="chat_fill", force_version_no=1,
        )
    except Exception as e:
        if not list_versions(root_id):
            raise
        logger.warning("[rewrite] base version race resolved root=%s: %s", root_id, e)


def save_flow_version(flow: dict, blob: bytes, file_type: str, file_name: str,
                      created_by: str) -> dict:
    """flow 场景：blob 写 flow 桶（initiator_id）+ flow_version 行（不切 current）。
    返回 flow_version 行 dict（含 id/file_path/version_no）。"""
    from common import settings

    from api.db.services.flow_service import FlowVersionService

    bucket = flow["initiator_id"]
    obj = f"ai-rewrite-{flow['id']}-{file_name}"
    settings.STORAGE_IMPL.put(bucket, obj, blob)
    return FlowVersionService.add_version(
        flow, object_name=obj, file_name=file_name, file_type=file_type,
        file_size=len(blob), source=_FLOW_REWRITE_SOURCE, created_by=created_by,
        switch_current=False,
    )


def load_flow_version_blob(version_row: dict) -> tuple[bytes, dict]:
    """按 flow_version 行取 blob。返回 (blob, flow_row)。flow 已删时明确报错。"""
    from common import settings

    from api.db.services.flow_service import FlowInstanceService

    ok, flow = FlowInstanceService.get_by_id(version_row["flow_id"])
    if not ok or not flow:
        raise ValueError("该版本所属的流程已被删除，无法重写。")
    flow_data = flow if isinstance(flow, dict) else flow.__data__
    bucket = flow_data["initiator_id"]
    blob = settings.STORAGE_IMPL.get(bucket, version_row["file_path"])
    if not blob:
        raise ValueError("版本文件已丢失，无法重写。")
    return blob, flow_data
