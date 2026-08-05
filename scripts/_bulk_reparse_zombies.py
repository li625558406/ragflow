#!/usr/bin/env python3
"""
批量重新解析僵尸 RUNNING 文档。

筛选条件: Document.run='1' AND update_time < (now - 30min)
    (30 min 内更新的不处理，避免和 task_executor 当前在跑的文档冲突)

每个文档执行 RAGFlow 标准重新解析流程
(参考 api/apps/restful_apis/document_api.py:1426 /parse 端点 + reset_document_for_reparse):
    1. DocumentService.clear_chunk_num_when_rerun  (倒计 KB token_num/chunk_num)
    2. update_by_id(run='1', progress=0, chunk_num=0, token_num=0, progress_msg='')
    3. TaskService.filter_delete([Task.doc_id == doc.id])
    4. docStoreConn.delete({"doc_id": doc.id}, ...)  (清 ES chunks)
    5. DocumentService.run(tenant_id, doc_dict, {})  (重新入队 Redis Stream)

幂等: 重复执行只会再清一遍 (chunk_num 已为 0 时 clear_chunk_num_when_rerun 是 no-op，
      Task 表无残留时 filter_delete 也安全)。

CLI:
    docker exec docker-ragflow-cpu-1 python /ragflow/scripts/_bulk_reparse_zombies.py
"""
import os
import sys
import time
import logging
from datetime import datetime

sys.path.insert(0, '/ragflow')

from common import settings
from common.constants import TaskStatus
from common.log_utils import init_root_logger
from api.db.db_models import DB, Document, Task, Knowledgebase
from api.db.services.document_service import DocumentService
from api.db.services.task_service import TaskService
from rag.nlp import search

settings.init_settings()
init_root_logger("bulk_reparse")

# 30 min 内的 RUNNING 文档可能正在被 task_executor 处理，跳过
FRESH_CUTOFF_MS = 30 * 60 * 1000
BATCH_LOG_EVERY = 100  # 每处理 N 个打印一次进度


def _ts(t):
    return datetime.fromtimestamp(t / 1000).strftime('%Y-%m-%d %H:%M:%S') if t else '-'


@DB.connection_context()
def get_kb_tenant_map():
    """返回 {kb_id: tenant_id}"""
    m = {}
    for kb in Knowledgebase.select(Knowledgebase.id, Knowledgebase.tenant_id):
        m[kb.id] = kb.tenant_id
    return m


@DB.connection_context()
def get_stuck_doc_ids():
    """返回 [(doc_id, kb_id, update_time_ms), ...]，按 update_time 升序"""
    cutoff = int(time.time() * 1000) - FRESH_CUTOFF_MS
    return list(
        Document
        .select(Document.id, Document.kb_id, Document.update_time)
        .where((Document.run == '1') & (Document.update_time < cutoff))
        .order_by(Document.update_time.asc())
        .tuples()
    )


def reparse_one(doc_id, kb_id, tenant_id):
    """单文档标准重新解析流程。返回 (ok: bool, msg: str)"""
    try:
        e, doc = DocumentService.get_by_id(doc_id)
        if not e or not doc:
            return False, f"doc not found: {doc_id}"

        idx_name = search.index_name(tenant_id)

        # 1. 倒计 KB 计数 (chunk_num/token_num 为 0 时 no-op)
        try:
            DocumentService.clear_chunk_num_when_rerun(doc.id)
        except Exception as ex:
            logging.warning("clear_chunk_num_when_rerun failed for %s: %s", doc.id, ex)

        # 2. 重置 doc 状态 (run=1=RUNNING，progress=0，清计数器)
        DocumentService.update_by_id(doc.id, {
            "run": str(TaskStatus.RUNNING.value),
            "progress": 0,
            "progress_msg": "",
            "chunk_num": 0,
            "token_num": 0,
        })

        # 3. 删旧 Task 行
        TaskService.filter_delete([Task.doc_id == doc.id])

        # 4. 清 ES chunks (如果索引存在)
        try:
            if settings.docStoreConn.index_exist(idx_name, doc.kb_id):
                settings.docStoreConn.delete({"doc_id": doc.id}, idx_name, doc.kb_id)
        except Exception as ex:
            logging.warning("docStore delete failed for %s: %s", doc.id, ex)

        # 5. 重新入队
        doc_dict = doc.to_dict()
        DocumentService.run(tenant_id, doc_dict, {})
        return True, "ok"
    except Exception as ex:
        import traceback
        tb = traceback.format_exc()[-300:]
        return False, f"{repr(ex)[:200]} | tb={tb}"


_PARSE_MONITOR_BATCHES_KEY = "parse_monitor:batches"
_PARSE_MONITOR_BATCHES_MAX = 20


def _push_batch_summary(total, success, failed, skipped, duration_sec, errors):
    """bulk_reparse 结束后将摘要 LPUSH 到 Redis List, LTRIM 保留最近 20 条.

    Redis 异常不影响主流程 (脚本仍正常退出).
    """
    try:
        from rag.utils.redis_conn import REDIS_CONN
        if REDIS_CONN is None or getattr(REDIS_CONN, "REDIS", None) is None:
            return
        import json as _json
        import time as _time
        payload = {
            "ts": int(_time.time()),
            "total": int(total),
            "success": int(success),
            "failed": int(failed),
            "skipped": int(skipped),
            "duration_sec": round(float(duration_sec), 2),
            "first_errors": [
                {"doc_id": d[:8], "msg": m[:200]} for d, m in errors[:5]
            ],
        }
        REDIS_CONN.REDIS.lpush(_PARSE_MONITOR_BATCHES_KEY, _json.dumps(payload, ensure_ascii=False))
        REDIS_CONN.REDIS.ltrim(_PARSE_MONITOR_BATCHES_KEY, 0, _PARSE_MONITOR_BATCHES_MAX - 1)
        logging.info("bulk_reparse: pushed batch summary to redis (total=%d success=%d)", total, success)
    except Exception as e:
        logging.warning("bulk_reparse: push batch summary failed: %s", e)


def main():
    logging.info("=== bulk_reparse started ===")
    logging.info("scan filter: run='1' AND update_time < now-%dmin", FRESH_CUTOFF_MS // 60000)

    stuck = get_stuck_doc_ids()
    total = len(stuck)
    logging.info("found %d stuck docs to reparse", total)
    if total == 0:
        logging.info("nothing to do, exit")
        _push_batch_summary(0, 0, 0, 0, 0.0, [])
        return

    kb_tenant = get_kb_tenant_map()
    logging.info("loaded %d KB -> tenant mappings", len(kb_tenant))

    success = 0
    failed = 0
    skipped_no_tenant = 0
    errors = []
    t0 = time.time()

    for i, (doc_id, kb_id, upd_time) in enumerate(stuck, 1):
        tenant_id = kb_tenant.get(kb_id)
        if not tenant_id:
            skipped_no_tenant += 1
            errors.append((doc_id, f"no tenant for kb_id={kb_id}"))
            continue

        ok, msg = reparse_one(doc_id, kb_id, tenant_id)
        if ok:
            success += 1
        else:
            failed += 1
            errors.append((doc_id, msg))

        if i % BATCH_LOG_EVERY == 0 or i == total:
            elapsed = time.time() - t0
            rate = i / elapsed if elapsed > 0 else 0
            eta_sec = (total - i) / rate if rate > 0 else 0
            logging.info(
                "[%d/%d %.1f%%] ok=%d fail=%d skip=%d | rate=%.1f/s eta=%.0fs (%.1fh) | last doc=%s kb=%s upd=%s",
                i, total, 100 * i / total,
                success, failed, skipped_no_tenant,
                rate, eta_sec, eta_sec / 3600,
                doc_id[:8], kb_id[:8], _ts(upd_time),
            )

    logging.info("=== done | total=%d success=%d failed=%d skipped=%d | elapsed=%.1fs ===",
                 total, success, failed, skipped_no_tenant, time.time() - t0)
    if errors:
        logging.warning("=== first 10 errors ===")
        for d, m in errors[:10]:
            logging.warning("  doc=%s: %s", d[:8], m[:300][:200])

    # 写批次摘要到 Redis, 供 /collection/parse-monitor/reparse-batches 端点读取
    _push_batch_summary(total, success, failed, skipped_no_tenant,
                        time.time() - t0, errors)


if __name__ == "__main__":
    main()
