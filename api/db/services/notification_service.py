"""智能采集通知系统的 DB 服务层。

3 个 Service：
  - NotificationService：通知主体 CRUD + 订阅匹配
  - NotificationUserService：用户未读记录 fan-out + 已阅查询
  - NotificationSubscriptionService：订阅偏好 get_or_default / upsert
"""
import logging
import time
from typing import List, Optional, Tuple

from peewee import IntegrityError, fn

from api.db.db_models import (
    CrawlerResult, DB, Notification, NotificationUser, NotificationSubscription,
)
from api.db.services.common_service import CommonService
from common.misc_utils import get_uuid

_logger = logging.getLogger(__name__)


class NotificationService(CommonService):
    model = Notification

    @classmethod
    @DB.connection_context()
    def create_notification(
        cls, *, site_id: str, site_display: str, category: str, batch_key: str,
        title: str, summary: str, result_ids: List[str], result_count: int,
        publish_range: str, created_at: int,
    ) -> Optional[str]:
        """幂等创建。返回 id；batch_key 冲突返回 None。其他异常向上传播。"""
        new_id = get_uuid().replace("-", "")
        try:
            cls.insert(
                id=new_id,
                tenant_id="system",
                site_id=site_id, site_display=site_display, category=category,
                batch_key=batch_key, title=title, summary=summary,
                result_ids=result_ids, result_count=result_count,
                publish_range=publish_range, created_at=created_at,
            )
            return new_id
        except IntegrityError:
            return None

    @classmethod
    @DB.connection_context()
    def get_by_id(cls, notification_id: str) -> Optional[dict]:
        row = cls.model.select().where(cls.model.id == notification_id).first()
        return row.to_dict() if row else None

    @classmethod
    @DB.connection_context()
    def get_detail_with_source(cls, notification_id: str) -> Optional[dict]:
        """Return notification dict enriched with source_url / markdown from the
        first CrawlerResult referenced by ``result_ids``.

        Notification 本身不存 source_url / markdown，二者位于 crawler_result 表。
        result_ids 指向 crawler_result.id；这里 JOIN 第一条取原文信息。
        """
        n = cls.get_by_id(notification_id)
        if not n:
            return None
        result_ids = n.get("result_ids") or []
        if result_ids:
            try:
                cr = (
                    CrawlerResult
                    .select()
                    .where(CrawlerResult.id == result_ids[0])
                    .first()
                )
                if cr:
                    n["source_url"] = getattr(cr, "source_url", "") or ""
                    n["markdown"] = getattr(cr, "markdown", "") or ""
            except Exception as e:
                _logger.warning(
                    "enrich source for notification %s failed: %s",
                    notification_id, e,
                )
        return n

    @classmethod
    @DB.connection_context()
    def get_results_for_notification(cls, notification_id: str) -> List[dict]:
        """展开 notification.result_ids → 完整 CrawlerResult 列表。

        返回字段精简到前端展示所需：id / title / source_url / publish_date /
        markdown（详情三级弹框直接用，无需再请求）。
        """
        n = cls.get_by_id(notification_id)
        if not n:
            return []
        result_ids = n.get("result_ids") or []
        if not result_ids:
            return []
        rows = []
        for rid in result_ids:
            try:
                cr = (
                    CrawlerResult
                    .select(
                        CrawlerResult.id,
                        CrawlerResult.title,
                        CrawlerResult.source_url,
                        CrawlerResult.publish_date,
                        CrawlerResult.markdown,
                        CrawlerResult.crawled_at,
                    )
                    .where(CrawlerResult.id == rid)
                    .first()
                )
                if cr:
                    rows.append({
                        "id": cr.id,
                        "title": cr.title or "",
                        "source_url": cr.source_url or "",
                        "publish_date": cr.publish_date or "",
                        "markdown": cr.markdown or "",
                        "crawled_at": cr.crawled_at or 0,
                    })
            except Exception as e:
                _logger.warning(
                    "fetch result %s for notification %s failed: %s",
                    rid, notification_id, e,
                )
        return rows

    @classmethod
    @DB.connection_context()
    def get_max_created_at_for_site(cls, site_id: str) -> int:
        """watermark 兜底：从 notification 表查 site_id 的最大 created_at。"""
        row = (
            cls.model
            .select(fn.MAX(cls.model.created_at))
            .where(cls.model.site_id == site_id)
            .scalar()
        )
        return int(row or 0)

    @classmethod
    @DB.connection_context()
    def get_recent_for_site(
        cls, site_id: str, within_ms: int,
    ) -> Optional[dict]:
        """返回 site_id 在最近 within_ms 毫秒内的最新一条 notification；无则 None。

        用于站点级冷却合并：cooldown 内的多批新结果合并进这条通知，
        而不是每批新建（避免高频采集站点在通知列表刷屏）。
        """
        cutoff = int(time.time() * 1000) - within_ms
        row = (
            cls.model
            .select()
            .where(
                (cls.model.site_id == site_id)
                & (cls.model.created_at >= cutoff)
            )
            .order_by(cls.model.created_at.desc())
            .first()
        )
        return row.to_dict() if row else None

    @classmethod
    @DB.connection_context()
    def append_results(
        cls, notification_id: str, *,
        new_result_ids: List[str], new_publish_dates: List[str],
    ) -> int:
        """把新一批 result_ids 合并进已存在的 notification。

        - result_ids 保序去重 union
        - result_count 重算为 union 长度
        - title 用新 count 重算（"xxx 检测到 N 条新结果"）
        - publish_range 扩展到覆盖新日期
        - created_at 不刷新（保留首次时间，避免 watermark fallback 语义混乱）

        返回合并后的 result_count；notification 不存在返回 0。
        """
        n = cls.model.select().where(cls.model.id == notification_id).first()
        if not n:
            return 0
        existing = list(n.result_ids or [])
        union = list(dict.fromkeys(existing + new_result_ids))
        new_count = len(union)
        site_display = n.site_display or n.site_id
        # publish_range 合并：拆出原区间端点 + 新日期，重新取 min/max
        prev_dates = [d for d in (n.publish_range or "").split(" ~ ") if d]
        all_dates = sorted(set(prev_dates + [d for d in new_publish_dates if d]))
        if len(all_dates) >= 2:
            publish_range = f"{all_dates[0]} ~ {all_dates[-1]}"
        elif all_dates:
            publish_range = all_dates[0]
        else:
            publish_range = n.publish_range or ""
        cls.model.update(
            result_ids=union,
            result_count=new_count,
            title=f"{site_display} 检测到 {new_count} 条新结果",
            publish_range=publish_range,
        ).where(cls.model.id == notification_id).execute()
        return new_count

    @classmethod
    @DB.connection_context()
    def match_subscribers(
        cls, site_id: str, category: str, candidate_user_ids: List[str],
    ) -> List[str]:
        """匹配订阅用户。

        - 无 subscription 记录的用户 = 全订阅（包含）
        - 有 subscription 记录的用户：site_ids 为空或包含 site_id，且 categories 为空或包含 category
        """
        if not candidate_user_ids:
            return []
        subs = (
            NotificationSubscription
            .select()
            .where(NotificationSubscription.user_id.in_(candidate_user_ids))
        )
        explicit_map = {s.user_id: s for s in subs}
        result = []
        for uid in candidate_user_ids:
            sub = explicit_map.get(uid)
            if sub is None:
                result.append(uid)  # 无记录 = 全订阅
                continue
            site_match = (not sub.site_ids) or (site_id in sub.site_ids)
            cat_match = (not sub.categories) or (category in sub.categories)
            if site_match and cat_match:
                result.append(uid)
        return result


class NotificationUserService(CommonService):
    model = NotificationUser

    @classmethod
    @DB.connection_context()
    def fan_out(cls, notification_id: str, user_ids: List[str]) -> int:
        """通知 fan-out 到多个用户。已存在的 (user_id, notification_id) 跳过。

        返回成功插入数。
        """
        inserted = 0
        for uid in user_ids:
            try:
                cls.insert(
                    id=get_uuid().replace("-", ""),
                    notification_id=notification_id,
                    user_id=uid, tenant_id="system",
                    is_read=False,
                )
                inserted += 1
            except IntegrityError:
                continue  # (user_id, notification_id) 已存在
        return inserted

    @classmethod
    @DB.connection_context()
    def get_unread(
        cls, user_id: str, page: int = 1, page_size: int = 20,
    ) -> Tuple[List[dict], int]:
        """未读列表（JOIN notification 取主体字段）。"""
        base_where = (
            (NotificationUser.user_id == user_id)
            & (NotificationUser.is_read == False)  # noqa: E712
        )
        total = (
            NotificationUser
            .select()
            .join(Notification, on=(NotificationUser.notification_id == Notification.id))
            .where(base_where)
            .count()
        )
        items = []
        for nu in (
            NotificationUser
            .select(NotificationUser, Notification)
            .join(Notification, on=(NotificationUser.notification_id == Notification.id))
            .where(base_where)
            .order_by(Notification.created_at.desc())
            .paginate(page, page_size)
        ):
            notif = nu.notification
            d = {}
            for fname in Notification._meta.fields.keys():
                d[fname] = getattr(notif, fname)
            d["is_read"] = nu.is_read
            d["read_at"] = nu.read_at
            items.append(d)
        return items, total

    @classmethod
    @DB.connection_context()
    def get_unread_count(cls, user_id: str) -> int:
        return (
            NotificationUser
            .select()
            .where(
                (NotificationUser.user_id == user_id)
                & (NotificationUser.is_read == False)  # noqa: E712
            )
            .count()
        )

    @classmethod
    @DB.connection_context()
    def mark_read(cls, user_id: str, notification_ids: List[str]) -> int:
        """批量标已阅。返回更新行数。"""
        if not notification_ids:
            return 0
        return (
            cls.model
            .update({cls.model.is_read: True, cls.model.read_at: int(time.time() * 1000)})
            .where(
                (cls.model.user_id == user_id)
                & (cls.model.notification_id.in_(notification_ids))
            )
            .execute()
        )

    @classmethod
    @DB.connection_context()
    def mark_all_read(cls, user_id: str) -> int:
        return (
            cls.model
            .update({cls.model.is_read: True, cls.model.read_at: int(time.time() * 1000)})
            .where(
                (cls.model.user_id == user_id)
                & (cls.model.is_read == False)  # noqa: E712
            )
            .execute()
        )


class NotificationSubscriptionService(CommonService):
    model = NotificationSubscription

    @classmethod
    @DB.connection_context()
    def get_or_default(cls, user_id: str) -> dict:
        row = cls.model.select().where(cls.model.user_id == user_id).first()
        if not row:
            return {
                "site_ids": [],
                "categories": [],
                "browser_push": True,
                "force_modal": True,
            }
        d = row.to_dict()
        return {
            "site_ids": d.get("site_ids") or [],
            "categories": d.get("categories") or [],
            "browser_push": d.get("browser_push", True),
            "force_modal": d.get("force_modal", True),
        }

    @classmethod
    @DB.connection_context()
    def upsert(
        cls, user_id: str, *, site_ids: List[str], categories: List[str],
        browser_push: bool = True, force_modal: bool = True,
    ) -> str:
        """Upsert 订阅偏好。返回 id。"""
        existing = cls.model.select().where(cls.model.user_id == user_id).first()
        if existing:
            cls.model.update(
                site_ids=site_ids, categories=categories,
                browser_push=browser_push, force_modal=force_modal,
            ).where(cls.model.id == existing.id).execute()
            return existing.id
        new_id = get_uuid().replace("-", "")
        cls.insert(
            id=new_id, user_id=user_id, tenant_id="system",
            site_ids=site_ids, categories=categories,
            browser_push=browser_push, force_modal=force_modal,
        )
        return new_id
