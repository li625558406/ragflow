#!/usr/bin/env python3
"""Ack pending messages in the correct group."""
import sys, os, json
sys.path.insert(0, "/ragflow")
os.environ.setdefault("rag_project_base", "/ragflow")

from common import settings
settings.init_settings()
from rag.utils.redis_conn import REDIS_CONN
from common import settings as cfg

queue_name = cfg.get_svr_queue_name(0)
r = REDIS_CONN.REDIS
group = "rag_flow_svr_task_broker"

pending = r.xpending_range(queue_name, group, "-", "+", 100)
print(f"Pending: {len(pending) if pending else 0}")

for item in (pending or []):
    msg_id = item["message_id"]
    consumer = item.get("consumer", "?")
    print(f"  ACK: {msg_id} consumer={consumer}")
    r.xack(queue_name, group, msg_id)

print("Done.")
