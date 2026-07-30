"""
Trigger jdhy crawler_task from inside the container,
mirroring crawl4ai_app.trigger_task._run() behavior. Bypasses HTTP/JWT.
"""
import json
import logging
import subprocess
import sys
import time

sys.path.insert(0, "/ragflow")

from api.db.services.crawler_service import CrawlerTaskService

TASK_ID = "20f843508a3211f184daea173a0699f4"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


def main():
    ok, task = CrawlerTaskService.get_by_id(TASK_ID)
    if not ok or not task:
        print(f"task {TASK_ID} not found")
        sys.exit(1)
    print(f"task: name={task.name} site_id={task.site_id} kb_id={task.kb_id} "
          f"last_run_status={task.last_run_status!r}")

    is_first_run = not (task.last_run_status or "").strip()
    script_args_dict = {
        "site_id": task.site_id,
        "writer": "collection",
        "category": "policy",
        "task_id": TASK_ID,
    }
    if not is_first_run:
        script_args_dict["date_filter"] = "today"
    script_args = json.dumps(script_args_dict, ensure_ascii=False)
    print(f"is_first_run={is_first_run} script_args={script_args}")

    cmd = [
        "python", "/ragflow/rag/svr/unified_crawler.py",
        "--tenant-id", task.tenant_id,
        "--kb-id", task.kb_id or "",
        "--task-name", f"manual-{task.site_id}",
        "--writer", "collection",
        "--script-args", script_args,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)
    print(f"returncode={proc.returncode}")
    print("---stdout tail---")
    print(proc.stdout[-800:])
    if proc.returncode != 0:
        print("---stderr tail---")
        print(proc.stderr[-1500:])

    status = "success" if proc.returncode == 0 else "fail"
    summary = {
        "status": status,
        "pages": 0,
        "items_found": 0,
        "items_new": 0,
        "kb_uploaded": 0,
        "attachments_uploaded": 0,
        "errors": [] if status == "success" else [(proc.stderr or "fail")[-300:]],
    }
    CrawlerTaskService.update_by_id(TASK_ID, {
        "last_run_status": status,
        "last_run_time": int(time.time() * 1000),
        "last_run_summary": summary,
    })
    print(f"updated last_run_status={status}")


if __name__ == "__main__":
    main()
