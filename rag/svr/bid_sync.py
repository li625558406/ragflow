#!/usr/bin/env python3
#
#  Copyright 2026 The InfiniFlow Authors. All Rights Reserved.
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
"""
世舶科技标讯同步脚本 — 由定时任务系统调度。

从 searchProjectApi 拉取招标信息并存入 bid_project 表。
支持增量 / 全量两种模式。

UI 定时任务配置示例:
  name:        世舶标讯同步
  script_path: rag/svr/bid_sync.py
  script_args: --script-args {"days":1,"sync_type":"incremental"}
  cron:        0 */6 * * *
"""

import argparse
import json
import logging
import os
import random
import sys
import time
from datetime import datetime, timedelta

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.abspath(os.path.join(_SCRIPT_DIR, "..", ".."))
sys.path.insert(0, _PROJECT_ROOT)

from common import settings
from common.log_utils import init_root_logger
from common.misc_utils import get_uuid

PAGE_SIZE = 50          # 每页条数（最大 50）
MAX_PAGES = 10000       # 翻页上限（实际由 hasNext 控制终止）
MAX_RETRIES = 3         # API 请求重试次数
RETRY_DELAY = 5         # 重试间隔（秒）
PAGE_DELAY = 0.5        # 页间间隔（秒），避免触发网关限流


# ============================================================================
# 工具函数
# ============================================================================

def _strip_html(text: str) -> str:
    import re
    return re.sub(r"<[^>]+>", "", text) if text else ""


def _parse_dt(dt_str: str):
    if not dt_str:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(dt_str, fmt)
        except ValueError:
            continue
    return None


def _gen_log_id() -> int:
    """生成唯一的同步日志 ID（微秒时间戳 + 随机后缀，适配 BIGINT）。"""
    base = int(datetime.now().timestamp() * 1_000_000)
    suffix = random.randint(0, 999)
    return base * 1000 + suffix


def _retry_request(fn, label: str):
    """带重试的 API 请求封装。"""
    last_err = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            return fn()
        except Exception as e:
            last_err = e
            if attempt < MAX_RETRIES:
                logging.warning("%s 失败(第%d次), %ds 后重试...", label, attempt, RETRY_DELAY)
                time.sleep(RETRY_DELAY)
    raise last_err


def _safe_str(val) -> str | None:
    """安全转为字符串，None / 空 返回 None。"""
    if val is None or val == "":
        return None
    return str(val)


def _safe_json(val) -> str:
    """安全序列化为 JSON 字符串。"""
    if val is None:
        return "[]"
    return json.dumps(val, ensure_ascii=False)


# ============================================================================
# 解析命令行参数（与 task_executor 传参保持一致）
# ============================================================================

def parse_args():
    parser = argparse.ArgumentParser(description="世舶科技标讯同步脚本")
    parser.add_argument("--tenant-id", default="", help="租户ID")
    parser.add_argument("--task-name", default="bid_sync", help="任务名称")
    parser.add_argument("--target-url", default="", help="预留参数")
    parser.add_argument("--kb-id", default="", help="知识库ID")
    parser.add_argument("--llm-id", default="", help="预留参数")
    parser.add_argument("--llm-model", default="", help="预留参数")
    parser.add_argument("--access-token", default="", help="预留参数")
    parser.add_argument("--script-args", default="", help="JSON 格式扩展参数: days/sync_type/class_id")
    return parser.parse_args()


# ============================================================================
# 主流程
# ============================================================================

def _diag(msg):
    print(f"[BID_SYNC] {msg}", flush=True)


def main():
    args = parse_args()
    task_name = args.task_name or "bid_sync"

    _diag(f"脚本启动, task_name={task_name}")
    _diag(f"script_args 原始值: {repr(args.script_args)}")

    settings.init_settings()
    _diag("settings.init_settings() 完成")

    from api.db.db_models import DB
    from api.db.services.bid_service import BidProjectService, BidSyncLogService
    from api.utils.bid_api_client import BidApiClient
    _diag("所有 import 完成")

    # ---- 解析扩展参数 ----
    extra = {}
    if args.script_args:
        try:
            extra = json.loads(args.script_args)
            _diag(f"script_args 解析成功: {extra}")
        except json.JSONDecodeError as e:
            _diag(f"script_args 解析失败: {e}, raw={repr(args.script_args)}")
            logging.warning("无法解析 script-args: %s", args.script_args)

    days = int(extra.get("days", 1))
    sync_type = extra.get("sync_type", "incremental")
    class_id = extra.get("class_id", "")
    province_code = extra.get("province_code", "")
    city_code = extra.get("city_code", "")

    # ---- 获取分布式锁（防止并发重复执行） ----
    _diag("正在获取数据库锁 bid_sync_task (timeout=30s) ...")
    lock = DB.lock("bid_sync_task", timeout=30)
    try:
        lock.lock()
        _diag("数据库锁已获取")
    except Exception as e:
        _diag(f"获取锁失败: {e}")
        logging.error("获取锁失败，可能有其他同步任务正在执行: %s", e)
        sys.exit(1)

    now = datetime.now()
    start_dt = now - timedelta(days=days)
    start_date_str = start_dt.strftime("%Y-%m-%d 00:00:00")
    end_date_str = now.strftime("%Y-%m-%d %H:%M:%S")
    batch_id = get_uuid()
    log_id = _gen_log_id()
    sync_log = None
    failed = False

    try:
        _diag(f"同步开始: 类型={sync_type}, 时间范围={start_date_str} ~ {end_date_str}")

        # ---- 创建同步日志 ----
        try:
            sync_log = BidSyncLogService.save(
                id=log_id,
                batch_id=batch_id,
                api_name="searchProjectApi",
                sync_type=sync_type,
                date_range_start=start_dt,
                date_range_end=now,
                total_fetched=0,
                total_new=0,
                total_updated=0,
                status="running",
                started_at=now,
            )
            _diag(f"同步日志已创建: log_id={log_id}, batch_id={batch_id}")
        except Exception as e:
            _diag(f"创建同步日志失败(非致命): {e}")
            logging.error("创建同步日志失败: %s", e)

        # ---- 构建 area_code 参数 ----
        area_code = None
        if province_code:
            area_code = {
                "proviceCodeList": [province_code],
                "cityCodeList": [city_code] if city_code else [],
                "countyCodeList": [],
            }
            logging.info("地区筛选: 省=%s 市=%s", province_code, city_code or "全部")

        # ---- 翻页拉取 ----
        client = BidApiClient()
        _diag(f"BidApiClient 初始化: base_url={client.base_url}, app_code={client.app_code[:8]}...")
        page = 1
        total_fetched = 0
        total_new = 0
        total_updated = 0

        while page <= MAX_PAGES:
            logging.info("第 %d 页 ...", page)

            try:
                resp = _retry_request(
                    lambda p=page: client.search_project(
                        start_date=start_date_str,
                        end_date=end_date_str,
                        page_id=p,
                        page_number=PAGE_SIZE,
                        project_class_id=class_id,
                        area_code=area_code,
                        search_type=1,
                    ),
                    f"searchProjectApi 第{page}页",
                )
            except Exception as e:
                _diag(f"API 请求最终失败(第{page}页): {e}")
                logging.error("API 请求最终失败(第%d页): %s", page, e)
                failed = True
                break

            _diag(f"API 返回: code={resp.get('code')}, msg={resp.get('msg', '')[:80]}")
            data = resp.get("data", {})
            items = data.get("data", [])
            has_next = data.get("hasNext", False)
            page_se_keywords = _safe_str(data.get("seKeyWords"))

            if not items:
                _diag(f"第 {page} 页无数据, 同步完成")
                logging.info("第 %d 页无数据, 同步完成", page)
                break

            _diag(f"第 {page} 页: 获取 {len(items)} 条, hasNext={has_next}")

            # ---- 逐条入库 ----
            for item in items:
                try:
                    project_id = item.get("id")
                    if not project_id:
                        continue
                    if not project_id:
                        continue

                    project_data = {
                        "id": project_id,
                        "title": _strip_html(item.get("title", "")),
                        "title_html": item.get("title", ""),
                        "content": item.get("content", ""),
                        "publish_time": _parse_dt(item.get("publishTime")),
                        "news_type_id": item.get("newsTypeID"),
                        "project_class_id": _safe_str(item.get("projectClassID")),
                        "purchase_type_id": _safe_str(item.get("purchaseTypeID")),
                        "project_money": item.get("projectMoney", ""),
                        "provice_code": item.get("proviceCode", ""),
                        "city_code": item.get("cityCode", ""),
                        "county_code": item.get("countyCode", ""),
                        "industry_codes": _safe_json(item.get("industryCodeList", [])),
                        "part_a_names": _safe_json(item.get("partANameList", [])),
                        "part_b_names": _safe_json(item.get("partBNameList", [])),
                        "has_file": item.get("hasFile", 0),
                        "contract_end_date": item.get("contractEndDate", ""),
                        "se_keywords": page_se_keywords,
                        "score": item.get("score"),
                        "sync_batch_id": batch_id,
                    }

                    is_new, _ = BidProjectService.upsert_project(project_data)
                    if is_new:
                        total_new += 1
                    else:
                        total_updated += 1
                    total_fetched += 1

                except Exception as e:
                    _diag(f"保存项目失败(id={item.get('id')}): {e}")
                    logging.error("保存项目失败(id=%s): %s", item.get("id"), e)
                    failed = True

            _diag(f"第 {page} 页完成: 累计 新增={total_new} 更新={total_updated}")
            logging.info("第 %d 页完成: 新增%d 更新%d", page, total_new, total_updated)
            page += 1

            if not has_next:
                logging.info("API 返回 hasNext=false, 同步完成")
                break

            time.sleep(PAGE_DELAY)

        if page > MAX_PAGES:
            _diag(f"已翻 {MAX_PAGES} 页，达到上限，强制停止")

        # ---- 更新同步日志 ----
        if sync_log is not None:
            try:
                BidSyncLogService.update_by_id(
                    log_id,
                    {
                        "total_fetched": total_fetched,
                        "total_new": total_new,
                        "total_updated": total_updated,
                        "status": "fail" if failed else "success",
                        "completed_at": datetime.now(),
                    },
                )
                _diag(f"同步日志已更新: status={'fail' if failed else 'success'}")
            except Exception as e:
                _diag(f"更新同步日志失败: {e}")
                logging.error("更新同步日志失败: %s", e)

        status_str = "FAIL" if failed else "SUCCESS"
        _diag(f"同步完成 [{status_str}]: 总数={total_fetched} 新增={total_new} 更新={total_updated}")

    finally:
        try:
            lock.unlock()
            _diag("数据库锁已释放")
        except Exception as e:
            logging.warning("释放锁失败: %s", e)

    if failed:
        sys.exit(1)


if __name__ == "__main__":
    CONSUMER_NAME = "bid_sync"
    init_root_logger(CONSUMER_NAME)
    main()
