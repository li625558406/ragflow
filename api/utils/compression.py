#
#  Copyright 2025 The InfiniFlow Authors. All Rights Reserved.
#
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.
#
import base64
import gzip
import json
import logging

# Only compress message arrays whose serialized JSON exceeds this size (bytes).
# Below this threshold the compression overhead outweighs the benefit.
COMPRESS_THRESHOLD = 10 * 1024  # 10 KB
COMPRESS_LEVEL = 6  # balanced: decent compression, reasonable CPU cost
VERSION = 1


def compress_json(obj, threshold: int = COMPRESS_THRESHOLD) -> object:
    """Compress a JSON-serializable object with gzip+base64.

    Returns the original object unchanged if its serialized size is below
    *threshold*.  Otherwise returns a sentinel dict ``{"_v": …, "_z": "…"}``
    whose ``_z`` value is the base64-encoded gzip payload.
    """
    if obj is None:
        return obj
    try:
        raw = json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if len(raw) < threshold:
            return obj
        compressed = gzip.compress(raw, compresslevel=COMPRESS_LEVEL)
        encoded = base64.b64encode(compressed).decode("ascii")
        logging.debug(
            "Compressed %d bytes → %d bytes (%.0f%%)",
            len(raw), len(encoded), len(encoded) / len(raw) * 100,
        )
        return {"_v": VERSION, "_z": encoded}
    except Exception:
        logging.warning("Failed to compress JSON payload, storing uncompressed")
        return obj


def decompress_json(obj: object) -> object:
    """Reverse ``compress_json`` — restore the original object if compressed."""
    if not isinstance(obj, dict) or "_z" not in obj or "_v" not in obj:
        return obj
    try:
        compressed = base64.b64decode(obj["_z"])
        raw = gzip.decompress(compressed)
        return json.loads(raw)
    except Exception:
        logging.warning("Failed to decompress JSON payload, returning as-is")
        return obj
