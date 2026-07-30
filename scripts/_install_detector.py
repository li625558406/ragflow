"""
Install detector meta-task for tenant. Idempotent.
"""
import sys
sys.path.insert(0, "/ragflow")

from rag.svr.crawler_engine.register_detector_task import ensure_detector_task

TENANT_ID = "7ab771d4dec84f23b2c1fb5f4e453ff9"

row = ensure_detector_task(
    tenant_id=TENANT_ID,
    interval_seconds=600,
    enabled=True,
)
print(f"detector task installed: {row}")
