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
"""智能采集通知系统 REST API

路由前缀：/api/v1（由 register_page 自动注册）

C 端端点（鉴权：login_required，user_id 以 current_user.id 为准；query.user_id 仅作防御性兜底）：
  GET  /notifications/unread/count
  GET  /notifications/unread
  GET  /notifications/{id}
  POST /notifications/{id}/read
  POST /notifications/read-all
  POST /notifications/batch-read
  GET  /notifications/subscription
  PUT  /notifications/subscription

B 端 admin 端点（鉴权：admin = is_superuser；User.role 字段不存在，租户级 owner 角色不参与此 admin 门控）：
  GET    /admin/notifications
  GET    /admin/notifications/{id}
  DELETE /admin/notifications/{id}
  POST   /admin/notifications/batch-delete
  GET    /admin/notifications/stats
  GET    /admin/notifications/config
  PUT    /admin/notifications/config
"""
import datetime
import logging
import os
import time

from quart import Blueprint, request

from api.apps import current_user, login_required
from api.db.db_models import DB, Notification, NotificationUser
from api.db.services.notification_service import (
    NotificationService,
    NotificationSubscriptionService,
    NotificationUserService,
)
from api.utils.api_utils import get_data_error_result, get_json_result

logger = logging.getLogger(__name__)

# 单个 Blueprint，承载 C 端 + admin 全部路由。
# register_page 约定：模块必须暴露 `manager` 变量为 Blueprint 实例，
# 文件名 *_app.py 位于 restful_apis 目录即自动注册到 /api/v1 前缀下。
manager = Blueprint("rest_notification_app", __name__)


def _get_user_id() -> str:
    """C 端：以服务端鉴权 current_user.id 为准；仅在 current_user 不可用时
    回退到 query.user_id（防御性兜底，绝不信任客户端超过服务端鉴权）。"""
    uid = getattr(current_user, "id", None) if current_user else None
    if uid:
        return str(uid)
    # defensive fallback only; do NOT trust client over server auth
    return (request.args.get("user_id") or "").strip()


def _is_admin() -> bool:
    """B 端：admin = is_superuser。
    注意：User 模型无 role 字段（role 位于 UserTenant），
    租户级 owner 角色不参与此 admin 门控。"""
    u = current_user if current_user else None
    if not u:
        return False
    return bool(getattr(u, "is_superuser", False))


# ---------------------------------------------------------------------------
# C 端 API
# ---------------------------------------------------------------------------

@manager.route("/notifications/unread/count", methods=["GET"])
@login_required
async def unread_count():
    uid = _get_user_id()
    if not uid:
        return get_data_error_result(message="user_id required")
    count = NotificationUserService.get_unread_count(uid)
    return get_json_result(data={"count": count})


@manager.route("/notifications/unread", methods=["GET"])
@login_required
async def unread_list():
    uid = _get_user_id()
    if not uid:
        return get_data_error_result(message="user_id required")
    try:
        page = max(int(request.args.get("page", 1)), 1)
        page_size = min(max(int(request.args.get("page_size", 20)), 1), 50)
    except (TypeError, ValueError):
        page, page_size = 1, 20
    items, total = NotificationUserService.get_unread(uid, page, page_size)
    return get_json_result(data={"list": items, "total": total})


@manager.route("/notifications/<notification_id>", methods=["GET"])
@login_required
async def get_notification(notification_id: str):
    n = NotificationService.get_detail_with_source(notification_id)
    if not n:
        return get_data_error_result(message="not found")
    return get_json_result(data=n)


@manager.route("/notifications/<notification_id>/results", methods=["GET"])
@login_required
async def get_notification_results(notification_id: str):
    """展开 notification.result_ids → 完整 CrawlerResult 列表（二级弹框数据源）。"""
    results = NotificationService.get_results_for_notification(notification_id)
    return get_json_result(data={"list": results, "total": len(results)})


@manager.route("/notifications/<notification_id>/read", methods=["POST"])
@login_required
async def mark_one_read(notification_id: str):
    uid = _get_user_id()
    if not uid:
        return get_data_error_result(message="user_id required")
    NotificationUserService.mark_read(uid, [notification_id])
    return get_json_result(data={"ok": True})


@manager.route("/notifications/read-all", methods=["POST"])
@login_required
async def mark_all_read():
    uid = _get_user_id()
    if not uid:
        return get_data_error_result(message="user_id required")
    n = NotificationUserService.mark_all_read(uid)
    return get_json_result(data={"updated": n})


@manager.route("/notifications/batch-read", methods=["POST"])
@login_required
async def batch_read():
    uid = _get_user_id()
    if not uid:
        return get_data_error_result(message="user_id required")
    body = await request.get_json(silent=True) or {}
    ids = body.get("ids", [])
    if not isinstance(ids, list):
        return get_data_error_result(message="ids must be list")
    n = NotificationUserService.mark_read(uid, ids)
    return get_json_result(data={"updated": n})


@manager.route("/notifications/subscription", methods=["GET"])
@login_required
async def get_subscription():
    uid = _get_user_id()
    if not uid:
        return get_data_error_result(message="user_id required")
    sub = NotificationSubscriptionService.get_or_default(uid)
    return get_json_result(data=sub)


@manager.route("/notifications/subscription", methods=["PUT"])
@login_required
async def put_subscription():
    uid = _get_user_id()
    if not uid:
        return get_data_error_result(message="user_id required")
    body = await request.get_json(silent=True) or {}
    site_ids = body.get("site_ids", [])
    categories = body.get("categories", [])
    if not isinstance(site_ids, list) or not isinstance(categories, list):
        return get_data_error_result(message="site_ids/categories must be list")
    browser_push = bool(body.get("browser_push", True))
    force_modal = bool(body.get("force_modal", True))
    new_id = NotificationSubscriptionService.upsert(
        uid, site_ids=site_ids, categories=categories,
        browser_push=browser_push, force_modal=force_modal,
    )
    return get_json_result(data={"id": new_id})


# ---------------------------------------------------------------------------
# B 端 admin API
# ---------------------------------------------------------------------------

@manager.route("/admin/notifications", methods=["GET"])
@login_required
async def admin_list():
    if not _is_admin():
        return get_data_error_result(message="forbidden")
    try:
        page = max(int(request.args.get("page", 1)), 1)
        page_size = min(max(int(request.args.get("page_size", 50)), 1), 200)
    except (TypeError, ValueError):
        page, page_size = 1, 50
    site_id = (request.args.get("site_id") or "").strip() or None
    category = (request.args.get("category") or "").strip() or None

    @DB.connection_context()
    def _q():
        q = Notification.select()
        if site_id:
            q = q.where(Notification.site_id == site_id)
        if category:
            q = q.where(Notification.category == category)
        total = q.count()
        rows = q.order_by(Notification.created_at.desc()).paginate(page, page_size)
        items = []
        for n in rows:
            d = n.to_dict()
            d["pushed_count"] = (
                NotificationUser.select()
                .where(NotificationUser.notification_id == n.id)
                .count()
            )
            d["read_count"] = (
                NotificationUser.select()
                .where(
                    (NotificationUser.notification_id == n.id)
                    & (NotificationUser.is_read == True)  # noqa: E712
                )
                .count()
            )
            items.append(d)
        return {"list": items, "total": total}

    return get_json_result(data=_q())


@manager.route("/admin/notifications/<notification_id>", methods=["GET"])
@login_required
async def admin_get(notification_id: str):
    if not _is_admin():
        return get_data_error_result(message="forbidden")
    n = NotificationService.get_detail_with_source(notification_id)
    if not n:
        return get_data_error_result(message="not found")
    return get_json_result(data=n)


@manager.route("/admin/notifications/<notification_id>", methods=["DELETE"])
@login_required
async def admin_delete(notification_id: str):
    if not _is_admin():
        return get_data_error_result(message="forbidden")

    @DB.connection_context()
    def _del():
        with DB.atomic():
            NotificationUser.delete().where(
                NotificationUser.notification_id == notification_id
            ).execute()
            Notification.delete().where(Notification.id == notification_id).execute()
        return True

    _del()
    return get_json_result(data={"ok": True})


@manager.route("/admin/notifications/batch-delete", methods=["POST"])
@login_required
async def admin_batch_delete():
    """批量删除通知: 一次清掉多条及其 NotificationUser 关联.

    Request JSON: {"ids": ["uuid1", "uuid2", ...]}
    返回 {"deleted": N, "missing": M} — missing 是 DB 里查不到的条数 (幂等删除).
    """
    if not _is_admin():
        return get_data_error_result(message="forbidden")

    body = await request.get_json(silent=True) or {}
    raw_ids = body.get("ids") or []
    if not isinstance(raw_ids, list) or not raw_ids:
        return get_data_error_result(message="ids required")

    # 防御: 去重 + 去空 + 截断长度 (避免一条 DELETE IN 万条锁表)
    ids = list(dict.fromkeys(str(i).strip() for i in raw_ids if str(i).strip()))
    if not ids:
        return get_data_error_result(message="ids required")
    if len(ids) > 500:
        return get_data_error_result(message="batch size exceeds 500")

    @DB.connection_context()
    def _del():
        with DB.atomic():
            existing = (
                Notification.select(Notification.id)
                .where(Notification.id.in_(ids))
                .execute()
            )
            existing_ids = [str(r.id) for r in existing]
            missing = len(ids) - len(existing_ids)

            if existing_ids:
                NotificationUser.delete().where(
                    NotificationUser.notification_id.in_(existing_ids)
                ).execute()
                Notification.delete().where(
                    Notification.id.in_(existing_ids)
                ).execute()
            return len(existing_ids), missing

    deleted, missing = _del()
    logger.info(
        "admin_batch_delete by %s: deleted=%d missing=%d total_req=%d",
        _get_user_id(), deleted, missing, len(ids),
    )
    return get_json_result(data={"deleted": deleted, "missing": missing})


@manager.route("/admin/notifications/stats", methods=["GET"])
@login_required
async def admin_stats():
    if not _is_admin():
        return get_data_error_result(message="forbidden")
    now_ms = int(time.time() * 1000)
    day_ms = 86400 * 1000

    week_ms = 7 * day_ms

    # "今日" = 北京时间当天 00:00:00 起, 而非过去 24h 滚动窗口.
    # created_at 是 UTC epoch ms, 需用北京时区计算当日零点对应的 epoch ms.
    beijing_tz = datetime.timezone(datetime.timedelta(hours=8))
    start_of_today = datetime.datetime.now(beijing_tz).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    today_start_ms = int(start_of_today.timestamp() * 1000)

    @DB.connection_context()
    def _q():
        today_created = (
            Notification.select()
            .where(Notification.created_at >= today_start_ms)
            .count()
        )
        week_nids = Notification.select(Notification.id).where(
            Notification.created_at >= now_ms - week_ms
        )
        week_pushed = (
            NotificationUser.select()
            .where(NotificationUser.notification_id.in_(week_nids))
            .count()
        )
        week_read = (
            NotificationUser.select()
            .where(
                (NotificationUser.notification_id.in_(week_nids))
                & (NotificationUser.is_read == True)  # noqa: E712
            )
            .count()
        )
        return {
            "today_created": today_created,
            "week_pushed": week_pushed,
            "week_read": week_read,
            "read_rate": round(week_read / week_pushed, 3) if week_pushed else 0,
        }

    return get_json_result(data=_q())


@manager.route("/admin/notifications/config", methods=["GET"])
@login_required
async def admin_get_config():
    if not _is_admin():
        return get_data_error_result(message="forbidden")
    return get_json_result(data={
        "scan_interval": int(os.environ.get("NOTIFICATION_SCAN_INTERVAL", "120")),
        "retention_days": int(os.environ.get("NOTIFICATION_RETENTION_DAYS", "30")),
        "bucket_minutes": int(os.environ.get("NOTIFICATION_BUCKET_MINUTES", "1")),
        "poll_interval_ms": int(os.environ.get("NOTIFICATION_POLL_INTERVAL_MS", "30000")),
    })


@manager.route("/admin/notifications/config", methods=["PUT"])
@login_required
async def admin_put_config():
    if not _is_admin():
        return get_data_error_result(message="forbidden")
    # 配置项通过环境变量在部署时设定，运行时不可变更。
    return get_data_error_result(
        message="config update via environment variables at deploy time"
    )
