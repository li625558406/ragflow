"""
Trigger fujian_czt_zwgk crawler_task from inside the container,
mirroring crawl4ai_app.trigger_task._run() behavior. Bypasses HTTP/JWT.

用法（容器内）:
  docker cp _trigger_fujian_czt_zwgk.py docker-ragflow-cpu-1:/tmp/
  docker exec docker-ragflow-cpu-1 python /tmp/_trigger_fujian_czt_zwgk.py <TASK_ID>

首次回溯：不带 date_filter（6 栏目首页全量，约 100~150 条）
后续触发：date_filter=today（仅取当天发布数据）
"""
import json
import logging
import re
import subprocess
import sys
import time

sys.path.insert(0, "/ragflow")

from api.db.services.crawler_service import CrawlerTaskService

# 默认 task_id：执行 SQL 后用 SELECT 查到的实际 id 覆盖（也可作 argv[1] 传入）
DEFAULT_TASK_ID = ""
TASK_ID = (sys.argv[1] if len(sys.argv) > 1 else "").strip() or DEFAULT_TASK_ID

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


def main():
    if not TASK_ID:
        print("ERROR: 缺少 task_id。用法: python _trigger_fujian_czt_zwgk.py <TASK_ID>")
        sys.exit(2)

    ok, task = CrawlerTaskService.get_by_id(TASK_ID)
    if not ok or not task:
        print(f"task {TASK_ID} not found")
        sys.exit(1)
    print(f"task: name={task.name} site_id={task.site_id} kb_id={task.kb_id} "
          f"last_run_status={task.last_run_status!r}")

    # category=policy → collection_writer 走 crawler_result + collection_policy_ext
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
    items_updated = 0
    kb_uploaded = 0
    for line in proc.stdout.splitlines():
        if "Done:" in line and "new items" in line:
            # 形如: [CRAWLER] Done: 270 new items, 0 updated, 270 kb uploaded
            try:
                tail = line.split("Done:", 1)[1]
                m_n = re.search(r"(\d+)\s+new items", tail)
                m_u = re.search(r"(\d+)\s+updated", tail)
                m_k = re.search(r"(\d+)\s+kb uploaded", tail)
                if m_n: items_new = int(m_n.group(1))
                if m_u: items_updated = int(m_u.group(1))
                if m_k: kb_uploaded = int(m_k.group(1))
            except Exception:
                pass
    summary = {
        "status": status,
        "pages": 6,                # 6 栏目各 1 页
        "items_found": items_new + items_updated,
        "items_new": items_new,
        "kb_uploaded": kb_uploaded,
        "attachments_uploaded": 0,
        "errors": [] if status == "success" else [(proc.stderr or "crawler failed")[-300:]],
    }
    now_ms = int(time.time() * 1000)
    try:
        CrawlerTaskService.update_by_id(TASK_ID, {
            "last_run_status": status,
            "last_run_time": now_ms,
            "last_run_summary": summary,
        })
        print(f"updated crawler_task.last_run_status={status}")
    except Exception as e:
        print(f"update crawler_task failed: {e}")


if __name__ == "__main__":
    main()
