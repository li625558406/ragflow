"""Audit detail APIs for all crawler sites with empty content.

For each problem site:
1. Fetch one listing item
2. Try the detail fetch (following the YAML config)
3. Report: does the detail API work? What does it return?
"""
import sys
import json
import logging
import traceback

sys.path.insert(0, "/ragflow")
import requests
requests.packages.urllib3.disable_warnings()

from crawler_engine.config import ConfigLoader

logging.basicConfig(level=logging.WARNING)

SITE_IDS = [
    # 100% empty content
    "cebpubservice", "ccgp_agency", "ggzyfw_fujian", "ggzyfw_policies",
    "longyan", "slbgb", "quanzhou_zcfg", "pingtan_fjsz",
    # High empty rate
    "zhangzhou", "zjt_jdhy",
    # Minor issues
    "ccgp_jdcf_gg_cr", "jdhy", "ncha", "etrading_statute",
    "czt", "czt_jdhy", "fycbid",
]

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

loader = ConfigLoader("/ragflow/rag/svr/crawler_sites.yaml")
loader.load()


def safe_json(resp):
    try:
        return resp.json()
    except Exception:
        return None


def test_site(site_id):
    try:
        cfg = loader.get(site_id)
    except KeyError:
        return f"NOT IN YAML"

    detail = cfg.detail
    dt = detail.type if detail else "NONE"

    # Step 1: Get one listing item
    listing = cfg.listing
    url = listing.url
    method = (listing.method or "GET").upper()
    params = dict(listing.params)

    # Resolve template params
    for k, v in params.items():
        if isinstance(v, str):
            params[k] = v.replace("{{ page }}", "1").replace("{{ page_size }}", str(cfg.pagination.page_size or 5))

    try:
        if method == "POST":
            if listing.body_type == "json":
                resp = requests.post(url, json=params, headers=HEADERS, timeout=15, verify=False)
            else:
                resp = requests.post(url, data=params, headers=HEADERS, timeout=15, verify=False)
        else:
            resp = requests.get(url, params=params, headers=HEADERS, timeout=15, verify=False)
    except Exception as e:
        return f"LISTING ERROR: {e}"

    if resp.status_code != 200:
        return f"LISTING HTTP {resp.status_code}"

    data = safe_json(resp)
    if not data:
        return f"LISTING not JSON: CT={resp.headers.get('Content-Type','')}"

    # Extract first item
    items = None
    items_path = cfg.pagination.items_field or cfg.extract.items_path
    if items_path:
        for key in items_path.split("."):
            if isinstance(data, dict) and key in data:
                data = data[key]
            else:
                break
        if isinstance(data, list) and data:
            items = data
        elif isinstance(data, dict):
            for k in ("rows", "data", "dataList", "list", "resultList"):
                if k in data and isinstance(data[k], list):
                    items = data[k]
                    break
    if not items:
        # Try extract.fields
        ext = cfg.extract
        if ext.fields:
            return f"LISTING: items_path '{items_path}' not found. Top keys: {list(data.keys())[:10]}"

    item = items[0] if items else None
    if not item:
        return f"LISTING: empty items list"

    item_keys = list(item.keys())[:10]

    # Check if item has url/id for detail
    has_id = "id" in item or any(
        v and str(v) for k, v in item.items()
        if k in ("id", "infoID", "uuid", "bulletinId", "noticeId", "newsId", "sourceId", "ID")
    )

    # Step 2: Try detail fetch
    detail_url = detail.url or ""
    detail_method = (detail.method or "GET").upper()
    detail_params = dict(detail.params) if detail.params else {}

    # Resolve {{ field }} placeholders
    for key, val in item.items():
        detail_url = detail_url.replace("{" + key + "}", str(val) if val else "")
        for pk, pv in detail_params.items():
            if isinstance(pv, str):
                detail_params[pk] = pv.replace("{" + key + "}", str(val) if val else "")
                detail_params[pk] = pv.replace("{{ " + key + " }}", str(val) if val else "")

    detail_status = "NOT TESTED"
    detail_content_len = 0
    detail_keys = []

    if dt == "inline" or dt == "none" or not detail_url:
        detail_status = f"SKIPPED (type={dt}, no url)"
    elif dt == "api_request":
        try:
            if detail_method == "POST":
                if detail.body_type == "json":
                    dr = requests.post(detail_url, json=detail_params, headers=HEADERS, timeout=15, verify=False)
                else:
                    dr = requests.post(detail_url, data=detail_params, headers=HEADERS, timeout=15, verify=False)
            else:
                dr = requests.get(detail_url, params=detail_params, headers=HEADERS, timeout=15, verify=False)

            if dr.status_code != 200:
                detail_status = f"DETAIL HTTP {dr.status_code}"
            else:
                dd = safe_json(dr)
                if dd is None:
                    detail_status = f"DETAIL not JSON: CT={dr.headers.get('Content-Type','')} body_len={len(dr.text)}"
                else:
                    code = dd.get("code", "")
                    detail_status = f"code={code}"
                    d_data = dd.get("data") or dd
                    if isinstance(d_data, dict):
                        detail_keys = [k for k, v in d_data.items() if isinstance(v, str) and len(v) > 100][:5]
                    elif isinstance(d_data, str) and len(d_data) > 50:
                        detail_content_len = len(d_data)
                        detail_status += f" data_is_str({detail_content_len})"
                    cf = detail.content_field
                    if cf:
                        # Try to extract content_field
                        from crawler_engine.adapters.rest_api import _get_json_value
                        content = _get_json_value(dd, cf)
                        if content:
                            detail_content_len = len(str(content))
                            detail_status += f" content_len={detail_content_len}"
                    if not detail_keys and not detail_content_len:
                        detail_status += f" no_long_text_fields keys={list(d_data.keys())[:10] if isinstance(d_data, dict) else type(d_data)}"
        except Exception as e:
            detail_status = f"DETAIL ERROR: {e}"
    elif dt == "css_selector":
        detail_status = f"CSS_SELECTOR (needs url in item, has_url={has_id})"
    else:
        detail_status = f"UNKNOWN type={dt}"

    return json.dumps({
        "site_id": site_id,
        "listing_status": "OK",
        "item_keys": item_keys,
        "has_id": has_id,
        "detail_type": dt,
        "detail_url": detail_url[:80],
        "content_field": detail.content_field or "",
        "detail_status": detail_status,
        "detail_keys": detail_keys,
    }, ensure_ascii=False)


for sid in SITE_IDS:
    result = test_site(sid)
    try:
        data = json.loads(result)
        print(f"[{data['site_id']}] listing=OK, detail={data['detail_type']}, status={data['detail_status']}")
    except:
        print(f"[{sid}] {result[:120]}")
