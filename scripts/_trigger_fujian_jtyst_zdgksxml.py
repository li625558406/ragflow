"""
Trigger fujian_jtyst_zdgksxml crawler_task from inside the container.
Mirrors crawl4ai_app.trigger_task._run() — bypasses HTTP/JWT.

用法（容器内）:
  docker cp _trigger_fujian_jtyst_zdgksxml.py docker-ragflow-cpu-1:/tmp/
  docker exec docker-ragflow-cpu-1 python /tmp/_trigger_fujian_jtyst_zdgksxml.py <TASK_ID>

特点（与公告通知/规范性文件类站点不同）:
  - 列表 API 返回嵌套树（matterList → children → children），无日期字段
  - 因此每次触发都跑全量（不带 date_filter），靠 md5(site_id|source_url) 幂等去重
  - unified_crawler 会按 YAML 的 custom_runner 字段调度到 fujian_jtyst_zdgksxml_crawler.run()
"""
import json
import logging
import subprocess
import sys
import time

sys.path.insert(0, "/ragflow")

from api.db.services.crawler_service import CrawlerTaskService

DEFAULT_TASK_ID = ""
TASK_ID = (sys.argv[1] if len(sys.argv) > 1 else "").strip() or DEFAULT_TASK_ID

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


def main():
    if not TASK_ID:
        print("ERROR: 缺少 task_id。用法: python _trigger_fujian_jtyst_zdgksxml.py <TASK_ID>")
        sys.exit(2)

    ok, task = CrawlerTaskService.get_by_id(TASK_ID)
    if not ok or not task:
        print(f"task {TASK_ID} not found")
        sys.exit(1)
    print(f"task: name={task.name} site_id={task.site_id} kb_id={task.kb_id} "
          f"last_run_status={task.last_run_status!r}")

    # 全量模式：不传 date_filter，custom_runner 内部会展开全部叶子并 upsert 去重
    script_args_dict = {
        "site_id": task.site_id,
        "writer": "collection",
        "category": "zdgksxml",
        "task_id": TASK_ID,
        "full": True,
    }
    script_args = json.dumps(script_args_dict, ensure_ascii=False)
    print(f"script_args={script_args}")

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
    print(proc.stdout[-2000:])
    if proc.returncode != 0:
        print("---stderr tail---")
        print(proc.stderr[-3000:])

    status = "success" if proc.returncode == 0 else "fail"
    # 解析 [CRAWLER] Done: N new items, M updated, K kb uploaded
    items_new = 0
    items_updated = 0
    kb_uploaded = 0
    done_line = next((ln for ln in proc.stdout.splitlines() if "[CRAWLER] Done:" in ln), "")
    if done_line:
        import re as _re
        m = _re.search(r"Done:\s*(\d+)\s*new items?,\s*(\d+)\s*updated,?\s*(\d+)\s*kb\s*uploaded",
                       done_line)
        if m:
            items_new = int(m.group(1))
            items_updated = int(m.group(2))
            kb_uploaded = int(m.group(3))

    summary = {
        "status": status,
        "pages": 1,
        "items_found": items_new + items_updated,
        "items_new": items_new,
        "items_updated": items_updated,
        "kb_uploaded": kb_uploaded,
        "attachments_uploaded": 0,
        "errors": [] if status == "success" else [(proc.stderr or "fail")[-500:]],
    }
    CrawlerTaskService.update_by_id(TASK_ID, {
        "last_run_status": status,
        "last_run_time": int(time.time() * 1000),
        "last_run_summary": summary,
    })
    print(f"updated last_run_status={status} items_new={items_new} items_updated={items_updated} kb={kb_uploaded}")


if __name__ == "__main__":
    main()
