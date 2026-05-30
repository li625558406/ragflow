"""
Batch upload files from F:\投标项目\AI_资料 to RAGFlow KB f494f9d255de11f1b4e835d43f94478d.

Usage: uv run python scripts/upload_to_kb.py
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
API_BASE = "http://47.98.102.55/api/v1"
TOKEN = "ragflow-Cl_F9XyMjsIzYAz6rkkEfNMQGzCz4CYQ"
SOURCE_DIR = Path(r"F:\投标项目\AI_资料")

# Concurrency
UPLOAD_WORKERS = 5  # parallel file uploads
PARSE_BATCH_SIZE = 64  # max doc IDs per parse request

HEADERS = {"Authorization": f"Bearer {TOKEN}"}

# Track results
uploaded: list[dict] = []  # {id, name, parent_path}
failed_uploads: list[dict] = []  # {name, error}
total_files = 0


def collect_files() -> list[dict]:
    """Walk source directory and return list of {path, name, parent_path}."""
    files = []
    for root, dirs, filenames in os.walk(SOURCE_DIR):
        rel_dir = Path(root).relative_to(SOURCE_DIR)
        for fname in filenames:
            full_path = Path(root) / fname
            parent_path = rel_dir.as_posix()  # '/' separators
            if parent_path == ".":
                parent_path = ""
            files.append({
                "path": str(full_path),
                "name": fname,
                "parent_path": parent_path,
            })
    return files


def upload_one(file_info: dict) -> dict:
    """Upload a single file. Returns {id, name, parent_path} or raises."""
    url = f"{API_BASE}/datasets/{KB_ID}/documents"
    try:
        with open(file_info["path"], "rb") as f:
            files_payload = {"file": (file_info["name"], f)}
            data = {}
            if file_info["parent_path"]:
                data["parent_path"] = file_info["parent_path"]
            resp = requests.post(url, headers=HEADERS, files=files_payload, data=data, timeout=120)
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


def trigger_parse(doc_ids: list[str]) -> bool:
    """Trigger parsing for a batch of document IDs. Returns True on success."""
    url = f"{API_BASE}/datasets/{KB_ID}/documents/parse"
    payload = {"document_ids": doc_ids}
    try:
        resp = requests.post(url, headers=HEADERS, json=payload, timeout=30)
    except requests.exceptions.RequestException as e:
        print(f"  [ERROR] Parse trigger failed: {e}")
        return False

    if resp.status_code != 200:
        print(f"  [ERROR] Parse HTTP {resp.status_code}: {resp.text[:200]}")
        return False

    result = resp.json()
    if result.get("code") != 0:
        print(f"  [ERROR] Parse API error: {result.get('message', result)}")
        return False
    return True


def check_document_status(doc_ids: list[str]) -> dict:
    """Check status of documents. Returns {done, fail, running, unstart} counts."""
    url = f"{API_BASE}/datasets/{KB_ID}/documents"
    params = {"page": 1, "page_size": len(doc_ids) + 10}
    try:
        resp = requests.get(url, headers=HEADERS, params=params, timeout=30)
    except requests.exceptions.RequestException:
        return {"done": 0, "fail": 0, "running": 0, "unstart": len(doc_ids)}

    if resp.status_code != 200:
        return {"done": 0, "fail": 0, "running": 0, "unstart": len(doc_ids)}

    result = resp.json()
    all_docs = result.get("data", {}).get("docs", [])
    doc_map = {d["id"]: d for d in all_docs}

    counts = {"done": 0, "fail": 0, "running": 0, "unstart": 0}
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


def delete_document(doc_id: str) -> bool:
    """Delete a single document by ID."""
    url = f"{API_BASE}/datasets/{KB_ID}/documents"
    payload = {"ids": [doc_id]}
    try:
        resp = requests.delete(url, headers=HEADERS, json=payload, timeout=30)
        return resp.status_code == 200
    except requests.exceptions.RequestException:
        return False


def main():
    global total_files

    print("=" * 60)
    print("Batch Upload to RAGFlow Knowledge Base")
    print(f"Source: {SOURCE_DIR}")
    print(f"KB ID: {KB_ID}")
    print(f"API: {API_BASE}")
    print("=" * 60)

    # Step 1: Collect files
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
                failed_uploads.append({"name": file_info["name"], "parent_path": file_info["parent_path"], "error": str(e)})

            done_count += 1
            if done_count % 20 == 0 or done_count == total_files:
                elapsed = time.time() - upload_start
                print(f"  Progress: {done_count}/{total_files} "
                      f"({uploaded.__len__()} ok, {failed_uploads.__len__()} fail) "
                      f"[{elapsed:.0f}s]")

    upload_elapsed = time.time() - upload_start
    print(f"\nUpload complete in {upload_elapsed:.0f}s")
    print(f"  Success: {len(uploaded)}")
    print(f"  Failed: {len(failed_uploads)}")

    if failed_uploads:
        print("\nFailed uploads:")
        for f in failed_uploads:
            print(f"  - [{f['parent_path']}] {f['name']}: {f['error']}")

    if not uploaded:
        print("\nNo files uploaded successfully. Exiting.")
        return

    # Save uploaded IDs to file (for recovery)
    id_file = Path(__file__).parent / "uploaded_doc_ids.json"
    with open(id_file, "w", encoding="utf-8") as fp:
        json.dump(uploaded, fp, ensure_ascii=False, indent=2)
    print(f"\nSaved document IDs to {id_file}")

    # Step 3: Trigger parse in batches
    doc_ids = [d["id"] for d in uploaded]
    batches = [doc_ids[i:i + PARSE_BATCH_SIZE] for i in range(0, len(doc_ids), PARSE_BATCH_SIZE)]
    print(f"\n[Phase 2] Triggering parse for {len(doc_ids)} documents in {len(batches)} batches...")

    parse_ok = 0
    parse_fail = 0
    for i, batch in enumerate(batches):
        success = trigger_parse(batch)
        if success:
            parse_ok += len(batch)
        else:
            parse_fail += len(batch)
        if (i + 1) % 5 == 0 or (i + 1) == len(batches):
            print(f"  Parsing triggered: {parse_ok}/{len(doc_ids)} "
                  f"({i + 1}/{len(batches)} batches)")
        time.sleep(0.5)  # Small delay between batches

    # Step 4: Poll until all parsing is complete
    print(f"\n[Phase 3] Waiting for parsing to complete...")
    print("  (This may take a while. Press Ctrl+C to stop polling but parsing will continue.)")
    all_ids = set(doc_ids)
    max_wait = 3600  # 1 hour max
    poll_interval = 15  # seconds
    start = time.time()
    last_report = 0

    while all_ids and (time.time() - start) < max_wait:
        try:
            time.sleep(poll_interval)
            # Check a subset each time to avoid huge queries
            check_ids = list(all_ids)[:200]
            status = check_document_status(check_ids)

            if status["done"] > 0:
                done_now = {d for d in check_ids if _is_status(d, "DONE")}
                all_ids -= done_now
            if status["fail"] > 0:
                fail_now = {d for d in check_ids if _is_status(d, "FAIL")}
                all_ids -= fail_now

            elapsed = time.time() - start
            if elapsed - last_report > 30:
                remaining = len(all_ids)
                print(f"  [{elapsed:.0f}s] Remaining: {remaining}, "
                      f"Done: {status['done']}, Fail: {status['fail']}, "
                      f"Running: {status['running']}, Unstart: {status['unstart']}")
                last_report = elapsed

            if remaining == 0:
                break
        except KeyboardInterrupt:
            print("\n  Polling interrupted. Parsing continues on server.")
            break

    # Step 5: Final report
    print(f"\n[Phase 4] Final report...")
    final_status = check_document_status(doc_ids)
    print(f"  Total uploaded: {len(doc_ids)}")
    print(f"  Parse DONE: {final_status['done']}")
    print(f"  Parse FAIL: {final_status['fail']}")
    print(f"  Parse RUNNING: {final_status['running']}")
    print(f"  Parse UNSTART: {final_status['unstart']}")

    # Clean up: delete failed documents
    if final_status["fail"] > 0:
        print(f"\n[Phase 5] Cleaning up {final_status['fail']} failed documents...")
        # Get the list of failed document IDs
        url = f"{API_BASE}/datasets/{KB_ID}/documents"
        params = {"page": 1, "page_size": len(doc_ids) + 10}
        resp = requests.get(url, headers=HEADERS, params=params, timeout=30)
        result = resp.json()
        all_docs = result.get("data", {}).get("docs", [])
        doc_map = {d["id"]: d for d in all_docs}

        failed_ids = []
        for d in uploaded:
            doc = doc_map.get(d["id"], {})
            if doc.get("run") == "FAIL":
                failed_ids.append(d["id"])

        if failed_ids:
            print(f"  Deleting {len(failed_ids)} failed documents...")
            # Delete in batches
            for i in range(0, len(failed_ids), PARSE_BATCH_SIZE):
                batch = failed_ids[i:i + PARSE_BATCH_SIZE]
                url = f"{API_BASE}/datasets/{KB_ID}/documents"
                payload = {"ids": batch}
                try:
                    resp = requests.delete(url, headers=HEADERS, json=payload, timeout=30)
                    print(f"    Batch {i // PARSE_BATCH_SIZE + 1}: {resp.status_code}")
                except Exception as e:
                    print(f"    Batch {i // PARSE_BATCH_SIZE + 1} failed: {e}")
                time.sleep(0.3)

    print("\nDone!")


def _is_status(doc_id: str, status: str) -> bool:
    """Check if a specific document has a given run status."""
    url = f"{API_BASE}/datasets/{KB_ID}/documents"
    params = {"page": 1, "page_size": 1, "id": doc_id}
    try:
        resp = requests.get(url, headers=HEADERS, params=params, timeout=10)
        if resp.status_code != 200:
            return False
        result = resp.json()
        docs = result.get("data", {}).get("docs", [])
        return docs[0].get("run") == status if docs else False
    except Exception:
        return False


if __name__ == "__main__":
    main()
