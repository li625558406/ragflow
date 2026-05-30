"""Match failed KB docs to local files and generate report."""
import json
import os
import re
import subprocess
from pathlib import Path

# Load failed docs from server
result = subprocess.run(
    ["ssh", "-i", "D:/AI/konus-key.pem", "root@47.98.102.55", "cat /tmp/failed_docs.json"],
    capture_output=True, text=True,
)
failed = json.loads(result.stdout)

# Build local file index
source_dir = Path(r"F:\投标项目\AI_资料")
local_files = {}
for root, dirs, files in os.walk(source_dir):
    for fname in files:
        full = Path(root) / fname
        local_files[fname.lower()] = str(full)


def find_local(failed_name, parent_path):
    """Match failed doc to local file path."""
    base = failed_name
    # Remove RAGFlow dedup suffix like (1), (3), (4) before extension
    m = re.match(r"^(.+?)\((\d+)\)\.(.+)$", base)
    if m:
        base = f"{m.group(1)}.{m.group(3)}"

    # Try exact match
    if base.lower() in local_files:
        return local_files[base.lower()]

    # Try with parent_path
    if parent_path:
        for lf, lf_path in local_files.items():
            # Check if file is in the right subdirectory
            norm_lf = lf_path.replace("\\", "/").lower()
            if parent_path.lower() in norm_lf and base.lower() in norm_lf:
                return lf_path

    # Fuzzy: match by filename only (ignoring path)
    for lf, lf_path in local_files.items():
        orig_base = os.path.basename(lf_path).lower()
        if base.lower() == orig_base:
            return lf_path

    # Last resort: substring match
    for lf, lf_path in local_files.items():
        short = base.lower().replace(".docx", "").replace(".doc", "").replace(".pptx", "").replace(".pdf", "").replace(".xlsx", "")
        lf_short = lf.replace(".docx", "").replace(".doc", "").replace(".pptx", "").replace(".pdf", "").replace(".xlsx", "")
        if short in lf_short or lf_short in short:
            return lf_path

    return "NOT FOUND"


# Match
matched = []
for fd in failed:
    local = find_local(fd["name"], fd["parent_path"])
    error_type = ""
    msg = fd.get("error", "")
    if "not a zip file" in msg:
        error_type = "伪docx(实际为旧版doc格式)"
    elif "abandoned" in msg:
        error_type = "重试3次后放弃"
    elif "tika.parser got empty" in msg:
        error_type = "PPTX空白内容(可能是纯图片PPT)"
    elif "思维导图" in fd["name"]:
        error_type = "PDF解析异常"
    else:
        error_type = "未知"

    matched.append({
        "kb_name": fd["name"],
        "local_path": local,
        "parent_path": fd["parent_path"],
        "error_type": error_type,
        "doc_id": fd["doc_id"],
    })

# Save human-readable report
out_path = Path(r"D:\AI\ragflow2\scripts\failed_docs_list.txt")
with open(out_path, "w", encoding="utf-8") as f:
    f.write(f"KB解析失败文档清单 --- 共 {len(matched)} 个\n")
    f.write("知识库ID: f494f9d255de11f1b4e835d43f94478d\n")
    f.write("=" * 80 + "\n\n")

    for i, m in enumerate(matched, 1):
        f.write(f"{i}. KB名称: {m['kb_name']}\n")
        f.write(f"   本地路径: {m['local_path']}\n")
        f.write(f"   失败原因: {m['error_type']}\n")
        f.write(f"   KB目录: {m['parent_path']}\n")
        f.write("\n")

    # Summary
    f.write("=" * 80 + "\n")
    f.write("按失败原因分类:\n")
    types = {}
    for m in matched:
        t = m["error_type"]
        types[t] = types.get(t, 0) + 1
    for t, c in sorted(types.items(), key=lambda x: -x[1]):
        f.write(f"  {t}: {c}个\n")

    f.write("\n建议:\n")
    f.write("  - 伪docx文件: 用Word/WPS另存为真正的.docx格式，或改名为.doc后再上传\n")
    f.write("  - PPTX空白: 可能是纯图片PPT，需转换为可提取文字的格式\n")
    f.write("  - 重试放弃: 文件可能损坏或格式异常，手动检查\n")
    f.write("  - PDF异常: 思维导图PDF可能为扫描图片，需OCR优化\n")

print(f"Saved {len(matched)} failed docs to {out_path}")

# Save doc IDs for deletion
ids_path = Path(r"D:\AI\ragflow2\scripts\failed_doc_ids.json")
with open(ids_path, "w") as f:
    json.dump([m["doc_id"] for m in matched], f)
print(f"Saved doc IDs to {ids_path}")

# Print summary
for m in matched:
    print(f"  [{m['error_type']}] {m['kb_name']}")
    print(f"    -> {m['local_path']}")
