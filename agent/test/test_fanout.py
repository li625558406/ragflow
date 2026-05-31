"""
Standalone FanOut integration test.

Runs a minimal DSL (Begin → FanOut → Message) that processes a small
test array from ``test_items`` globals — no Outline component needed.
Use this to verify FanOut correctness and diagnose "no output" issues
quickly.

Usage (from RAGFlow root)::

    source .venv/bin/activate
    export PYTHONPATH=$(pwd)
    # optional:
    export FANOUT_TEST_LLM_ID="deepseek-chat"
    export FANOUT_TEST_TENANT_ID="<your-tenant-id>"
    python agent/test/test_fanout.py

Requirements:
    - RAGFlow backend services running (MySQL, Redis, etc.)
    - A valid LLM model configured for the test tenant
"""

import asyncio
import json
import logging
import os
import sys
import time

_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

# ── config ──────────────────────────────────────────────────────────
LLM_ID = os.environ.get("FANOUT_TEST_LLM_ID", "deepseek-chat")
TENANT_ID = os.environ.get("FANOUT_TEST_TENANT_ID", "")
MAX_CONCURRENCY = 2
TEST_ITEMS = ["apples", "bananas", "cherries"]
# ─────────────────────────────────────────────────────────────────────


def _discover_tenant_id() -> str:
    """Try to find any valid tenant_id from the database."""
    try:
        from api.db.db_models import init_database_tables
        init_database_tables()
        from api.db.db_models import Tenant
        rows = Tenant.select(Tenant.id).limit(1)
        if rows:
            return rows[0].id
    except Exception as e:
        logging.warning("Could not auto-discover tenant_id: %s", e)
    return ""


FANOUT_DSL: dict = {
    "components": {
        "begin": {
            "obj": {"component_name": "Begin", "params": {"mode": "task"}},
            "downstream": ["fanout:0"],
            "upstream": [],
        },
        "fanout:0": {
            "obj": {
                "component_name": "FanOut",
                "params": {
                    "items_ref": "test_items",
                    "llm_id": LLM_ID,
                    "system_prompt": "You are a helpful assistant. Be concise.",
                    "prompt_template": "Write one short sentence about: {item}",
                    "max_concurrency": MAX_CONCURRENCY,
                    "error_strategy": "skip",
                },
            },
            "downstream": ["message:0"],
            "upstream": ["begin"],
        },
        "message:0": {
            "obj": {
                "component_name": "Message",
                "params": {"content": "{fanout:0@results}"},
            },
            "downstream": [],
            "upstream": ["fanout:0"],
        },
    },
    "history": [],
    "path": [],
    "retrieval": {"chunks": [], "doc_aggs": []},
    "globals": {
        "test_items": TEST_ITEMS,
        "sys.query": "",
        "sys.user_id": "",
        "sys.conversation_turns": 0,
        "sys.files": [],
    },
}


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    tenant_id = TENANT_ID or _discover_tenant_id()
    if not tenant_id:
        print("ERROR: No tenant_id provided. Set FANOUT_TEST_TENANT_ID env var.")
        print("You can find your tenant_id in the RAGFlow database, table 'tenant'.")
        sys.exit(1)

    print(f"Tenant: {tenant_id}  |  LLM: {LLM_ID}")
    print(f"Items: {TEST_ITEMS}  |  Concurrency: {MAX_CONCURRENCY}")
    print("=" * 60)

    from agent.canvas import Canvas

    dsl_str = json.dumps(FANOUT_DSL)
    canvas = Canvas(dsl_str, tenant_id=tenant_id)

    t_start = time.perf_counter()
    event_count = 0
    message_count = 0
    error_seen = False

    async for event in canvas.run():
        event_count += 1
        ev_type = event.get("event", "?")

        if ev_type == "message":
            content = event.get("data", {}).get("content", "")
            if content:
                print(content, end="", flush=True)
                message_count += 1
        elif ev_type == "node_started":
            name = event.get("data", {}).get("component_name", "?")
            print(f"\n──▶ {name} started")
        elif ev_type == "node_finished":
            name = event.get("data", {}).get("component_name", "?")
            err = event.get("data", {}).get("error")
            dur = event.get("data", {}).get("elapsed_time", 0)
            if err:
                error_seen = True
                print(f"\n──▶ {name} FAILED ({dur:.1f}s): {err}")
            else:
                print(f"\n──▶ {name} done ({dur:.1f}s)")
        elif ev_type == "workflow_finished":
            outputs = event.get("data", {}).get("outputs", {})
            print("\n" + "=" * 60)
            print("RESULTS:")
            print(json.dumps(outputs, indent=2, ensure_ascii=False, default=str))

    elapsed = time.perf_counter() - t_start
    print(f"\n{'=' * 60}")
    print(f"Total time: {elapsed:.1f}s  |  Events: {event_count}  |  Messages: {message_count}")
    if error_seen:
        print("⚠ One or more components had errors. Check the server log for details.")
    else:
        print("✓ All components completed without errors.")


if __name__ == "__main__":
    asyncio.run(main())
