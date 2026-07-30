"""
Trigger fujian_fgw_zwgk crawler_task from inside the container,
mirroring crawl4ai_app.trigger_task._run() behavior. Bypasses HTTP/JWT.

用法（容器内）:
  docker cp _trigger_fujian_fgw_zwgk.py docker-ragflow-cpu-1:/tmp/
  docker exec docker-ragflow-cpu-1 python /tmp/_trigger_fujian_fgw_zwgk.py <TASK_ID>

首次回溯：不带 date_filter（首页多栏目全量）
后续触发：date_filter=today（仅取当天发布数据）
"""
import json
import logging
import subprocess
import sys
import time

sys.path.insert(0, "/ragflow")

from api.db.services.crawler_service import CrawlerTaskService

DEFAULT_TASK_ID = "e9681f7d8a2511f184daea173a0699f4"
TASK_ID = (sys.argv[1] if len(sys.argv) > 1 else "").strip() or DEFAULT_TASK_ID

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


def main():
    if not TASK_ID:
        print("ERROR: 缺少 task_id。用法: python _trigger_fujian_fgw_zwgk.py <TASK_ID>")
        sys.exit(2)

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
    print(proc.stdout[-1500:])
    if proc.returncode != 0:
        print("---stderr tail---")
        print(proc.stderr[-2000:])

    status = "success" if proc.returncode == 0 else "fail"
    items_new = 0
    for line in proc.stdout.splitlines():
        if "Done:" in line and "new items" in line:
            try:
                items_new = int(line.split("Done:")[1].split("new items")[0].strip().split()[0])
            except Exception:
                pass
    summary = {
        "status": status,
        "pages": 1,
        "items_found": items_new,
        "items_new": items_new,
        "kb_uploaded": items_new,
        "attachments_uploaded": 0,
        "errors": [] if status == "success" else [(proc.stderr or "fail")[-300:]],
    }
    CrawlerTaskService.update_by_id(TASK_ID, {
        "last_run_status": status,
        "last_run_time": int(time.time() * 1000),
        "last_run_summary": summary,
    })
    print(f"updated last_run_status={status} items_new={items_new}")


if __name__ == "__main__":
    main()
