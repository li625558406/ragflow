"""
Migrate old crawler state files (_crawler_state.json) to the new
crawler_state DB table.

Usage:
    # Dry-run: list state files and counts
    python rag/svr/crawler_engine/migrate_state.py --dry-run --tenant-id <TID>

    # Migrate a specific old state file
    python rag/svr/crawler_engine/migrate_state.py \
        --state-file rag/svr/fgw_crawler/_crawler_state.json \
        --site-id fgw_zwgk --tenant-id <TID>

    # Auto-discover and migrate all found state files
    python rag/svr/crawler_engine/migrate_state.py \
        --auto --tenant-id <TID>

    # Auto-discover with custom search directory
    python rag/svr/crawler_engine/migrate_state.py \
        --auto --scan-dir rag/svr/ --tenant-id <TID>
"""

import argparse
import json
import logging
import os
import sys
from typing import Dict, List, Optional, Set, Tuple

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.abspath(os.path.join(_SCRIPT_DIR, "..", "..", ".."))
sys.path.insert(0, _PROJECT_ROOT)

from common import settings
from common.log_utils import init_root_logger
from api.db.db_models import DB, CrawlerState
from common.misc_utils import get_uuid

# ---------------------------------------------------------------------------
# Mapping: old script basename → new site_id
# Each key matches the script name stem (without _crawler.py suffix if present)
# or the task_name directory name that contains _crawler_state.json.
# ---------------------------------------------------------------------------
SCRIPT_TO_SITE: Dict[str, str] = {
    # Fujian DRC / 发改委
    "fgw": "fgw_zwgk",
    "fgw_crawler": "fgw_zwgk",

    # CCGP (政府采购)
    "ccgp": "ccgp_agency",
    "ccgp_crawler": "ccgp_agency",
    "ccgp_jdcf_gg_cr": "ccgp_jdcf_gg_cr",
    "ccgp_jdcf_gg_cr_crawler": "ccgp_jdcf_gg_cr",
    "ccgp_zcfg": "ccgp_zcfg",
    "ccgp_zcfg_crawler": "ccgp_zcfg",
    "ccgp_zjlwbcbz": "ccgp_zjlwbcbz",
    "ccgp_zjlwbcbz_crawler": "ccgp_zjlwbcbz",

    # 住建部
    "mohurd": "mohurd",
    "mohurd_crawler": "mohurd",

    # 发改委-招投标监管
    "jdhy": "jdhy",
    "jdhy_crawler": "jdhy",
    "jdhy_zhangzhou": "jdhy_zhangzhou",
    "jdhy_zhangzhou_crawler": "jdhy_zhangzhou",

    # 交通厅
    "jtyst_zwgk": "jtyst_zwgk",
    "jtyst_zwgk_crawler": "jtyst_zwgk",
    "jtyst_jdhy": "jtyst_jdhy",
    "jtyst_jdhy_crawler": "jtyst_jdhy",

    # 水利厅
    "slt_xxgk": "slt_xxgk",
    "slt_xxgk_crawler": "slt_xxgk",
    "slt_zcjd": "slt_zcjd",
    "slt_zcjd_crawler": "slt_zcjd",

    # 财政厅
    "czt": "czt",
    "czt_crawler": "czt",
    "czt_jdhy": "czt_jdhy",
    "czt_jdhy_crawler": "czt_jdhy",

    # 住建厅
    "zjt_xxgk": "zjt_xxgk",
    "zjt_xxgk_crawler": "zjt_xxgk",
    "zjt_jdhy": "zjt_jdhy",
    "zjt_jdhy_crawler": "zjt_jdhy",

    # 公共资源交易
    "ggzyfw_business": "ggzyfw_business",
    "ggzyfw_business_crawler": "ggzyfw_business",
    "ggzyfw_policies": "ggzyfw_policies",
    "ggzyfw_policies_crawler": "ggzyfw_policies",
    "ggzyfw_guide": "ggzyfw_guide",
    "ggzyfw_guide_crawler": "ggzyfw_guide",
    "ggzyfw_guide_trade": "ggzyfw_guide_trade",
    "ggzyfw_guide_trade_crawler": "ggzyfw_guide_trade",
    "ggzyfw_fujian": "ggzyfw_fujian",
    "ggzyfw_fujian_crawler": "ggzyfw_fujian",
    "ggzyfw_fujian_business": "ggzyfw_fujian_business",
    "ggzyfw_fujian_business_crawler": "ggzyfw_fujian_business",
    "ggzy_policy": "ggzy_policy",
    "ggzy_policy_crawler": "ggzy_policy",
    "ggzy_deal": "ggzy_deal",
    "ggzy_deal_crawler": "ggzy_deal",

    # 工程项目招投标
    "gcjyzx_jyxx": "gcjyzx_jyxx",
    "gcjyzx_jyxx_crawler": "gcjyzx_jyxx",
    "gcjyzx_zcfg": "gcjyzx_zcfg",
    "gcjyzx_zcfg_crawler": "gcjyzx_zcfg",
    "gcjyzx_wgtb": "gcjyzx_wgtb",
    "gcjyzx_wgtb_crawler": "gcjyzx_wgtb",

    # 政府采购
    "zfcg": "zfcg",
    "zfcg_crawler": "zfcg",
    "zfcg_zcfg": "zfcg_zcfg",
    "zfcg_zcfg_crawler": "zfcg_zcfg",
    "zfcg_jdgl": "zfcg_jdgl",
    "zfcg_jdgl_crawler": "zfcg_jdgl",
    "zfcg_xmgg": "zfcg_xmgg",
    "zfcg_xmgg_crawler": "zfcg_xmgg",

    # 中介服务
    "zjfw": "zjfw",
    "zjfw_crawler": "zjfw",
    "zjfw_score_sort": "zjfw_score_sort",
    "zjfw_score_sort_crawler": "zjfw_score_sort",

    # 招标投标公共服务平台
    "cebpubservice": "cebpubservice",
    "cebpubservice_crawler": "cebpubservice",
    "cebpubservice_consult": "cebpubservice_consult",
    "cebpubservice_consult_crawler": "cebpubservice_consult",

    # 电子交易平台
    "etrading": "etrading",
    "etrading_crawler": "etrading",
    "etrading_statute": "etrading_statute",
    "etrading_statute_crawler": "etrading_statute",

    # 云竞网
    "enjoy5191": "enjoy5191",
    "enjoy5191_crawler": "enjoy5191",
    "enjoy5191_trade": "enjoy5191_trade",
    "enjoy5191_trade_crawler": "enjoy5191_trade",
    "enjoy5191_policy": "enjoy5191_policy",
    "enjoy5191_policy_crawler": "enjoy5191_policy",

    # 福易采
    "fycbid": "fycbid",
    "fycbid_crawler": "fycbid",

    # 易采购
    "easy_prt_bidding": "easy_prt_bidding",
    "easy_prt_bidding_crawler": "easy_prt_bidding",
    "easy_prt_trading": "easy_prt_trading",
    "easy_prt_trading_crawler": "easy_prt_trading",
    "easy_prt_policy": "easy_prt_policy",
    "easy_prt_policy_crawler": "easy_prt_policy",

    # 漳州地区站点
    "zhangzhou": "zhangzhou",
    "zhangzhou_crawler": "zhangzhou",
    "zhangzhou_fgw": "zhangzhou_fgw",
    "zhangzhou_fgw_crawler": "zhangzhou_fgw",
    "zz_zhangzhou": "zz_zhangzhou",
    "zz_zhangzhou_crawler": "zz_zhangzhou",
    "jsj_zhangzhou": "jsj_zhangzhou",
    "jsj_zhangzhou_crawler": "jsj_zhangzhou",

    # 南靖
    "fjnj": "fjnj",
    "fjnj_crawler": "fjnj",
    "fjnj_jdhy": "fjnj_jdhy",
    "fjnj_jdhy_crawler": "fjnj_jdhy",

    # 莆田
    "putian_zcwj": "putian_zcwj",
    "putian_zcwj_crawler": "putian_zcwj",
    "putian_fwzx": "putian_fwzx",
    "putian_fwzx_crawler": "putian_fwzx",

    # 泉州
    "quanzhou": "quanzhou",
    "quanzhou_crawler": "quanzhou",
    "quanzhou_zcfg": "quanzhou_zcfg",
    "quanzhou_zcfg_crawler": "quanzhou_zcfg",

    # 龙岩
    "longyan": "longyan",
    "longyan_crawler": "longyan",

    # 三明
    "smzcfg": "smzcfg",
    "smzcfg_crawler": "smzcfg",
    "smjy": "smjy",
    "smjy_crawler": "smjy",
    "smzx_notice": "smzx_notice",
    "smzx_notice_crawler": "smzx_notice",

    # 宁德
    "ningde_gcjs": "ningde_gcjs",
    "ningde_gcjs_crawler": "ningde_gcjs",

    # 厦门
    "xmzyjy": "xmzyjy",
    "xmzyjy_crawler": "xmzyjy",
    "xmzyjy_zcfg": "xmzyjy_zcfg",
    "xmzyjy_zcfg_crawler": "xmzyjy_zcfg",

    # 平潭
    "pingtan_fjsz": "pingtan_fjsz",
    "pingtan_fjsz_crawler": "pingtan_fjsz",

    # 张家口
    "zjk_zffg": "zjk_zffg",
    "zjk_zffg_crawler": "zjk_zffg",
    "zjk_notice": "zjk_notice",
    "zjk_notice_crawler": "zjk_notice",

    # 其他
    "ncha": "ncha",
    "ncha_crawler": "ncha",
    "mwr": "mwr",
    "mwr_crawler": "mwr",
    "slbgb": "slbgb",
    "slbgb_crawler": "slbgb",
    "ndrc_fzggwl": "ndrc_fzggwl",
    "ndrc_fzggwl_crawler": "ndrc_fzggwl",
    "fjtba": "fjtba",
    "fjtba_crawler": "fjtba",
    "fjtba_wfwg": "fjtba_wfwg",
    "fjtba_wfwg_crawler": "fjtba_wfwg",
    "fjtba_pxzx": "fjtba_pxzx",
    "fjtba_pxzx_crawler": "fjtba_pxzx",
    "fjcia_edu": "fjcia_edu",
    "fjcia_edu_crawler": "fjcia_edu",
    "hwdms_policy": "hwdms_policy",
    "hwdms_policy_crawler": "hwdms_policy",
    "ygcg_engineering": "ygcg_engineering",
    "ygcg_engineering_crawler": "ygcg_engineering",
    "zycg_notice": "zycg_notice",
    "zycg_notice_crawler": "zycg_notice",
    "zycg_zdfg": "zycg_zdfg",
    "zycg_zdfg_crawler": "zycg_zdfg",
    "ggzyjd": "ggzyjd",
    "ggzyjd_crawler": "ggzyjd",
    "ggzyjd_policies": "ggzyjd_policies",
    "ggzyjd_policies_crawler": "ggzyjd_policies",

    # 微信（排除但保留映射）
    "wechat_mp": "wechat_mp",
    "wechat_mp_crawler": "wechat_mp",

    # Chinese task name → site_id (servers use Chinese directory names)
    "【解读回应】南靖县人民政府": "fjnj",
    "【政务公开】南靖县人民政府  ": "fjnj",
    "【政务公开】国家文物局": "ncha",
    "【政策法规】全国公路建设市场监督管理系统": "gcjyzx_zcfg",
    "【政务公开】漳州市发展和改革委员会 ": "zhangzhou_fgw",
    "【业务培训】福建省建筑业协会 ": "fjcia_edu",
}

# Mapping: task_name → section (for sites where task_name distinguishes sections)
SCRIPT_TO_SECTION: Dict[str, str] = {
    "【解读回应】南靖县人民政府": "解读回应",
    "【政务公开】南靖县人民政府  ": "政务公开",
    "【政务公开】漳州市发展和改革委员会 ": "政务公开",
    "【政策法规】全国公路建设市场监督管理系统": "政策法规",
    "【政务公开】国家文物局": "政务公开",
    "【业务培训】福建省建筑业协会 ": "业务培训",
}


def find_state_files(scan_dir: str) -> List[Tuple[str, str]]:
    """Find all _crawler_state.json files under scan_dir.

    Returns list of (state_file_path, directory_name).
    """
    results = []
    if not os.path.isdir(scan_dir):
        logging.warning("Scan directory not found: %s", scan_dir)
        return results

    for root, dirs, files in os.walk(scan_dir):
        if "_crawler_state.json" in files:
            state_path = os.path.join(root, "_crawler_state.json")
            dir_name = os.path.basename(root)
            results.append((state_path, dir_name))
    return results


def load_state(state_path: str) -> Optional[dict]:
    """Load a legacy _crawler_state.json file."""
    try:
        with open(state_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logging.warning("Failed to load state file %s: %s", state_path, e)
        return None


def resolve_site_id(task_name: str) -> str:
    """Map a task_name (directory name) to a new site_id."""
    # Try exact match first
    if task_name in SCRIPT_TO_SITE:
        return SCRIPT_TO_SITE[task_name]
    # Try stripping _crawler suffix
    clean = task_name.replace("_crawler", "")
    if clean in SCRIPT_TO_SITE:
        return SCRIPT_TO_SITE[clean]
    # Return the task name itself as fallback
    return task_name


@DB.connection_context()
def migrate_one(
    state_path: str,
    site_id: str,
    tenant_id: str,
    section: str = "default",
    dry_run: bool = False,
) -> Tuple[int, bool]:
    """Migrate a single state file to crawler_state table.

    Returns (count_of_ids, was_merged).
    """
    state = load_state(state_path)
    if not state:
        return 0, False

    processed_ids = state.get("processed_ids", [])
    if not processed_ids:
        print(f"  [SKIP] {site_id}: no processed_ids in state file")
        return 0, False

    count = len(processed_ids)
    if dry_run:
        print(f"  [DRY-RUN] {site_id}: would migrate {count} IDs (section={section})")
        return count, False

    # Idempotent: get existing row or create new one
    try:
        existing = CrawlerState.get_or_none(
            (CrawlerState.site_id == site_id) &
            (CrawlerState.tenant_id == tenant_id) &
            (CrawlerState.section == section)
        )
    except Exception as e:
        logging.error("DB query failed for %s: %s", site_id, e)
        return 0, False

    if existing:
        # Merge: union of old and new processed IDs
        old_ids = set(existing.processed_ids or [])
        new_ids = set(str(pid) for pid in processed_ids)
        merged = old_ids | new_ids
        added = len(merged) - len(old_ids)

        if added == 0:
            print(f"  [OK] {site_id}: already up-to-date ({len(old_ids)} IDs)")
            return len(old_ids), True

        existing.processed_ids = list(merged)
        existing.save()
        print(f"  [MERGE] {site_id}: {len(old_ids)} existing + {added} new = {len(merged)} total")
        return len(merged), True
    else:
        # Create new row
        CrawlerState.create(
            id=get_uuid(),
            site_id=site_id,
            tenant_id=tenant_id,
            section=section,
            processed_ids=[str(pid) for pid in processed_ids],
            last_page=state.get("last_page", 0),
            last_offset=0,
            extra_state={},
        )
        print(f"  [NEW] {site_id}: {count} IDs imported (section={section})")
        return count, True


def migrate_auto(
    scan_dir: str,
    tenant_id: str,
    dry_run: bool = False,
) -> Dict[str, Tuple[int, bool]]:
    """Auto-discover and migrate all state files."""
    state_files = find_state_files(scan_dir)
    if not state_files:
        print(f"No _crawler_state.json files found under {scan_dir}")
        return {}

    print(f"Found {len(state_files)} state file(s):")
    for path, task_name in state_files:
        site_id = resolve_site_id(task_name)
        print(f"  {task_name} → {site_id}")
    print()

    results = {}
    for path, task_name in state_files:
        site_id = resolve_site_id(task_name)
        section = SCRIPT_TO_SECTION.get(task_name, "default")
        print(f"[{site_id}] {path} (section={section})")
        count, merged = migrate_one(path, site_id, tenant_id,
                                    section=section, dry_run=dry_run)
        results[site_id] = (count, merged)
    return results


def main():
    settings.init_settings()
    init_root_logger("migrate_crawler_state")

    parser = argparse.ArgumentParser(
        description="Migrate old crawler state files to new crawler_state DB table"
    )
    parser.add_argument("--tenant-id", required=True, help="Tenant ID for the migrated state")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would be done without writing to DB")
    parser.add_argument("--section", default="default",
                        help="Section label (default: 'default')")

    # Mode 1: single file
    parser.add_argument("--state-file", default=None,
                        help="Path to a single _crawler_state.json file")
    parser.add_argument("--site-id", default=None,
                        help="Site ID for the single file mode")

    # Mode 2: auto-discover
    parser.add_argument("--auto", action="store_true",
                        help="Auto-discover state files under scan-dir")
    parser.add_argument("--scan-dir", default=None,
                        help="Directory to scan (default: rag/svr/)")

    args = parser.parse_args()

    # Validate
    if not args.auto and not args.state_file:
        parser.error("Must specify --auto or --state-file")

    # Determine scan dir
    if args.auto:
        scan_dir = args.scan_dir or os.path.join(_PROJECT_ROOT, "rag", "svr")
        if not os.path.isdir(scan_dir):
            print(f"ERROR: scan directory not found: {scan_dir}")
            sys.exit(1)

        if args.dry_run:
            print("=== DRY RUN ===\n")

        results = migrate_auto(scan_dir, args.tenant_id, dry_run=args.dry_run)

        print(f"\n{'='*60}")
        if args.dry_run:
            total_ids = sum(c for c, _ in results.values())
            print(f"DRY-RUN: {len(results)} site(s), {total_ids} total IDs would be migrated")
        else:
            total_ids = sum(c for c, _ in results.values())
            print(f"Done: {len(results)} site(s), {total_ids} total IDs in crawler_state")
        print(f"{'='*60}")
    else:
        # Single file mode
        if not args.site_id:
            # Try to guess from path
            dir_name = os.path.basename(os.path.dirname(args.state_file))
            args.site_id = resolve_site_id(dir_name)
            print(f"Auto-resolved site_id: {args.site_id}")

        if args.dry_run:
            print("=== DRY RUN ===\n")
        migrate_one(args.state_file, args.site_id, args.tenant_id,
                    section=args.section, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
