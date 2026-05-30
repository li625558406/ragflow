"""
Server-side batch upload script.
Run on the RAGFlow server: python3 /tmp/server_upload.py

Reads files from /tmp/ai_ziliao/AI_资料/ and uploads them to the RAGFlow KB
via localhost API. Much faster than uploading over WAN.
"""

import json
import os
import sys
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests

# --- Config ---
KB_ID = "f494f9d255de11f1b4e835d43f94478d"
API_BASE = "http://127.0.0.1/api/v1"
TOKEN = "ragflow-Cl_F9XyMjsIzYAz6rkkEfNMQGzCz4CYQ"
SOURCE_DIR = "/tmp/ai_ziliao/AI_资料"

# Concurrency
UPLOAD_WORKERS = 10  # parallel file uploads (higher since localhost)
PARSE_BATCH_SIZE = 64  # max doc IDs per parse request

HEADERS = {"Authorization": f"Bearer {TOKEN}"}

# Track results
uploaded = []  # {id, name, parent_path}
failed_uploads = []  # {name, error}
total_files = 0


def collect_files():
    """Walk source directory and return list of {path, name, parent_path}."""
    files = []
    for root, dirs, filenames in os.walk(SOURCE_DIR):
        rel_dir = Path(root).relative_to(SOURCE_DIR)
        for fname in filenames:
            # Skip non-file entries
            full_path = os.path.join(root, fname)
            if not os.path.isfile(full_path):
                continue
            parent_path = rel_dir.as_posix()
            if parent_path == ".":
                parent_path = ""
            files.append({
                "path": full_path,
                "name": fname,
                "parent_path": parent_path,
            })
    return files


def upload_one(file_info):
    """Upload a single file. Returns {id, name, parent_path} or raises."""
    url = f"{API_BASE}/datasets/{KB_ID}/documents"
    try:
        with open(file_info["path"], "rb") as f:
            files_payload = {"file": (file_info["name"], f)}
            data = {}
            if file_info["parent_path"]:
                data["parent_path"] = file_info["parent_path"]
            resp = requests.post(url, headers=HEADERS, files=files_payload,
                               data=data, timeout=120)
    except requests.exceptions.Timeout:
        raise Exception(f"Timeout uploading {file_info['name']}")
    except requests.exceptions.ConnectionError:
        raise Exception(f"Connection error uploading {file_info['name']}")

    if resp.status_code != 200:
        raise Exception(f"HTTP {resp.status_code}: {resp.text[:300]}")

    result = resp.json()
    if result.get("code") != 0:
        raise Exception(f"API error: {result.get('message', result)}")

    data_arr = result.get("data", [])
    if not data_arr:
        raise Exception("No document data in response")

    doc = data_arr[0]
    return {
        "id": doc.get("id"),
        "name": file_info["name"],
        "parent_path": file_info["parent_path"],
    }


def trigger_parse(doc_ids):
    """Trigger parsing for a batch of document IDs."""
    url = f"{API_BASE}/datasets/{KB_ID}/documents/parse"
    payload = {"document_ids": doc_ids}
    max_retries = 3
    for attempt in range(max_retries):
        try:
            resp = requests.post(url, headers=HEADERS, json=payload, timeout=120)
            if resp.status_code == 200:
                result = resp.json()
                if result.get("code") == 0:
                    return True
            if attempt < max_retries - 1:
                time.sleep(2)
        except requests.exceptions.RequestException:
            if attempt < max_retries - 1:
                time.sleep(2)
    return False


def check_all_document_status(doc_ids):
    """Check run status of all documents. Returns {done, fail, running, unstart, total}."""
    url = f"{API_BASE}/datasets/{KB_ID}/documents"
    params = {"page": 1, "page_size": min(len(doc_ids) + 10, 1024)}
    try:
        resp = requests.get(url, headers=HEADERS, params=params, timeout=30)
    except requests.exceptions.RequestException:
        return {"done": 0, "fail": 0, "running": 0, "unstart": len(doc_ids), "total": len(doc_ids)}

    if resp.status_code != 200:
        return {"done": 0, "fail": 0, "running": 0, "unstart": len(doc_ids), "total": len(doc_ids)}

    result = resp.json()
    all_docs = result.get("data", {}).get("docs", [])
    doc_map = {d["id"]: d for d in all_docs}

    counts = {"done": 0, "fail": 0, "running": 0, "unstart": 0, "total": len(doc_ids)}
    for did in doc_ids:
        d = doc_map.get(did, {})
        run_status = d.get("run", "UNSTART")
        if run_status == "DONE":
            counts["done"] += 1
        elif run_status == "FAIL":
            counts["fail"] += 1
        elif run_status == "RUNNING":
            counts["running"] += 1
        else:
            counts["unstart"] += 1
    return counts


def delete_documents(doc_ids):
    """Delete documents by IDs."""
    url = f"{API_BASE}/datasets/{KB_ID}/documents"
    payload = {"ids": doc_ids}
    try:
        resp = requests.delete(url, headers=HEADERS, json=payload, timeout=60)
        return resp.status_code == 200
    except requests.exceptions.RequestException:
        return False


def main():
    global total_files

    print("=" * 60)
    print("Server-Side Batch Upload to RAGFlow KB")
    print(f"Source: {SOURCE_DIR}")
    print(f"KB ID: {KB_ID}")
    print(f"API: {API_BASE}")
    print("=" * 60)

    # Step 1: Collect files
    if not os.path.isdir(SOURCE_DIR):
        print(f"ERROR: Source directory not found: {SOURCE_DIR}")
        sys.exit(1)

    files = collect_files()
    total_files = len(files)
    print(f"\nFound {total_files} files in {SOURCE_DIR}")

    if total_files == 0:
        print("No files found. Exiting.")
        return

    # Print directory distribution
    dirs = {}
    for f in files:
        d = f["parent_path"] or "(root)"
        dirs[d] = dirs.get(d, 0) + 1
    for d, count in sorted(dirs.items()):
        print(f"  {d}: {count} files")

    # Step 2: Upload files in parallel
    print(f"\n[Phase 1] Uploading {total_files} files ({UPLOAD_WORKERS} concurrent)...")
    upload_start = time.time()

    with ThreadPoolExecutor(max_workers=UPLOAD_WORKERS) as executor:
        futures = {executor.submit(upload_one, f): f for f in files}
        done_count = 0
        for future in as_completed(futures):
            file_info = futures[future]
            try:
                result = future.result()
                uploaded.append(result)
            except Exception as e:
                failed_uploads.append({
                    "name": file_info["name"],
                    "parent_path": file_info["parent_path"],
                    "error": str(e),
                })

            done_count += 1
            if done_count % 50 == 0 or done_count == total_files:
                elapsed = time.time() - upload_start
                print(f"  Progress: {done_count}/{total_files} "
                      f"({len(uploaded)} ok, {len(failed_uploads)} fail) "
                      f"[{elapsed:.0f}s]")

    upload_elapsed = time.time() - upload_start
    print(f"\nUpload phase complete in {upload_elapsed:.0f}s")
    print(f"  Success: {len(uploaded)}")
    print(f"  Failed: {len(failed_uploads)}")

    if failed_uploads:
        print("\nFailed uploads:")
        for f in failed_uploads[:20]:  # Show first 20
            print(f"  - [{f['parent_path']}] {f['name']}: {f['error']}")

    if not uploaded:
        print("\nNo files uploaded successfully. Exiting.")
        return

    # Save uploaded IDs to file (for recovery)
    with open("/tmp/uploaded_doc_ids.json", "w", encoding="utf-8") as fp:
        json.dump(uploaded, fp, ensure_ascii=False, indent=2)
    print(f"\nSaved {len(uploaded)} document IDs to /tmp/uploaded_doc_ids.json")

    # Step 3: Trigger parse in batches
    doc_ids = [d["id"] for d in uploaded]
    batches = [doc_ids[i:i + PARSE_BATCH_SIZE]
               for i in range(0, len(doc_ids), PARSE_BATCH_SIZE)]
    print(f"\n[Phase 2] Triggering parse for {len(doc_ids)} documents "
          f"in {len(batches)} batches...")

    parse_ok = 0
    parse_fail = 0
    for i, batch in enumerate(batches):
        success = trigger_parse(batch)
        if success:
            parse_ok += len(batch)
        else:
            parse_fail += len(batch)
            print(f"  WARNING: Parse trigger failed for batch {i+1}/{len(batches)}")
        if (i + 1) % 10 == 0 or (i + 1) == len(batches):
            print(f"  Triggered: {parse_ok}/{len(doc_ids)} "
                  f"({i+1}/{len(batches)} batches)")
        time.sleep(0.3)  # Small delay between batches

    # Step 4: Poll until all parsing is complete
    print(f"\n[Phase 3] Polling parse status...")
    max_wait = 7200  # 2 hours max
    poll_interval = 20  # seconds
    start = time.time()
    last_report = 0

    while (time.time() - start) < max_wait:
        time.sleep(poll_interval)
        status = check_all_document_status(doc_ids)
        finished = status["done"] + status["fail"]
        remaining = status["running"] + status["unstart"]
        elapsed = time.time() - start

        if elapsed - last_report >= 60 or remaining == 0:  # Report every minute
            print(f"  [{elapsed:.0f}s] DONE: {status['done']}, FAIL: {status['fail']}, "
                  f"RUNNING: {status['running']}, UNSTART: {status['unstart']} "
                  f"({finished}/{len(doc_ids)} finished)")
            last_report = elapsed

        if remaining == 0:
            print("  All documents finished parsing!")
            break

    if (time.time() - start) >= max_wait:
        print("  WARNING: Max wait time reached. Some documents may still be parsing.")

    # Step 5: Final report
    print(f"\n[Phase 4] Final report...")
    final_status = check_all_document_status(doc_ids)
    print(f"  Total uploaded: {len(doc_ids)}")
    print(f"  Parse DONE:     {final_status['done']}")
    print(f"  Parse FAIL:     {final_status['fail']}")
    print(f"  Parse RUNNING:  {final_status['running']}")
    print(f"  Parse UNSTART:  {final_status['unstart']}")

    # Step 6: Clean up failed documents
    if final_status["fail"] > 0:
        print(f"\n[Phase 5] Cleaning up failed documents...")
        url = f"{API_BASE}/datasets/{KB_ID}/documents"
        params = {"page": 1, "page_size": len(doc_ids) + 10}
        try:
            resp = requests.get(url, headers=HEADERS, params=params, timeout=30)
            result = resp.json()
            all_docs = result.get("data", {}).get("docs", [])
            doc_map = {d["id"]: d for d in all_docs}

            failed_ids = [
                d["id"] for d in uploaded
                if doc_map.get(d["id"], {}).get("run") == "FAIL"
            ]

            if failed_ids:
                print(f"  Deleting {len(failed_ids)} failed documents...")
                for i in range(0, len(failed_ids), PARSE_BATCH_SIZE):
                    batch = failed_ids[i:i + PARSE_BATCH_SIZE]
                    ok = delete_documents(batch)
                    print(f"    Batch {i//PARSE_BATCH_SIZE + 1}/"
                          f"{(len(failed_ids)-1)//PARSE_BATCH_SIZE + 1}: "
                          f"{'OK' if ok else 'FAIL'}")
                    time.sleep(0.3)
        except Exception as e:
            print(f"  Cleanup error: {e}")

    # Save final report
    report = {
        "total_uploaded": len(uploaded),
        "parse_done": final_status["done"],
        "parse_fail": final_status["fail"],
        "parse_running": final_status["running"],
        "parse_unstart": final_status["unstart"],
        "upload_failures": len(failed_uploads),
    }
    with open("/tmp/upload_report.json", "w", encoding="utf-8") as fp:
        json.dump(report, fp, ensure_ascii=False, indent=2)

    print(f"\nDone! Report saved to /tmp/upload_report.json")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
