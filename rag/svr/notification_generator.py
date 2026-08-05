"""智能采集通知系统 — 扫描器主循环。

每 120s 跑一次（由 register_notification_task 注册为 meta-task）：
  1. 拉启用站点列表（从 crawler_result distinct site_id）
  2. for each site：读 Redis watermark（兜底从 notification 表 MAX(created_at) 恢复）
  3. 查 crawler_result WHERE crawled_at > watermark
  4. 按分钟桶聚合 → 生成 notification → fan-out 给订阅用户
  5. 推进 watermark
  6. 清理 30 天前的 notification/notification_user

幂等：Notification.batch_key 唯一索引保护。
并发：Redis 锁 notif:scan:lock（TTL 110s）防双进程。
"""
import logging
import time
from collections import Counter, defaultdict
from typing import List

from api.db.db_models import (
    DB, CrawlerResult, Notification, NotificationUser,
)
from api.db.services.notification_service import (
    NotificationService, NotificationUserService,
)
from rag.utils.redis_conn import REDIS_CONN

_logger = logging.getLogger(__name__)

WM_KEY = "notif:wm:{site_id}"
LOCK_KEY = "notif:scan:lock"
LOCK_TTL = 110  # 秒
RETENTION_MS = 30 * 86400 * 1000


def _get_candidate_user_ids() -> List[str]:
    """所有 active 用户 id。"""
    try:
        from api.db.db_models import User
        with DB.connection_context():
            return [u.id for u in User.select(User.id)]
    except Exception as e:
        _logger.exception("get candidate users failed: %s", e)
        return []


def _get_watermark(site_id: str) -> int:
    """Redis 取，失败/不存在则从 notification 表 MAX 恢复，写回 Redis。"""
    key = WM_KEY.format(site_id=site_id)
    try:
        v = REDIS_CONN.get(key)
        if v:
            return int(v)
    except Exception:
        pass
    wm = NotificationService.get_max_created_at_for_site(site_id)
    try:
        REDIS_CONN.set(key, wm)
    except Exception:
        pass
    return wm


def _set_watermark(site_id: str, val: int) -> None:
    try:
        REDIS_CONN.set(WM_KEY.format(site_id=site_id), val)
    except Exception as e:
        _logger.warning("set watermark failed: %s", e)


def _acquire_lock() -> bool:
    """SET NX 原子抢锁。Redis 故障时退化为不锁，靠幂等保护。"""
    try:
        # 直接打 valkey 客户端拿 nx 语义（REDIS_CONN.set 不支持 nx 形参）
        ok = REDIS_CONN.REDIS.set(LOCK_KEY, "scanner", ex=LOCK_TTL, nx=True)
        return bool(ok)
    except Exception as e:
        _logger.warning("acquire lock failed: %s", e)
        return True  # Redis 故障时退化为不锁，靠 batch_key 幂等保护


def _release_lock() -> None:
    try:
        REDIS_CONN.delete(LOCK_KEY)
    except Exception:
        pass


def _bucket_key(site_id: str, crawled_at_ms: int) -> str:
    minute_ts = crawled_at_ms // 60_000
    return f"{site_id}::{minute_ts}"


def _cleanup_old() -> int:
    cutoff = int(time.time() * 1000) - RETENTION_MS
    with DB.connection_context():
        old_ids = [
            n.id
            for n in Notification.select(Notification.id).where(Notification.created_at < cutoff)
        ]
        if not old_ids:
            return 0
        NotificationUser.delete().where(NotificationUser.notification_id.in_(old_ids)).execute()
        Notification.delete().where(Notification.id.in_(old_ids)).execute()
        return len(old_ids)


def scan_once(candidate_user_ids: List[str] = None) -> dict:
    """跑一轮扫描。返回统计字典。"""
    stats = {
        "notifications_created": 0,
        "skipped_lock": False,
        "sites_scanned": 0,
        "errors": 0,
        "old_deleted": 0,
    }
    if not _acquire_lock():
        stats["skipped_lock"] = True
        return stats

    try:
        if candidate_user_ids is None:
            candidate_user_ids = _get_candidate_user_ids()

        with DB.connection_context():
            site_ids = [r.site_id for r in CrawlerResult.select(CrawlerResult.site_id).distinct()]

        stats["sites_scanned"] = len(site_ids)

        for site_id in site_ids:
            try:
                _scan_site(site_id, candidate_user_ids, stats)
            except Exception as e:
                stats["errors"] += 1
                _logger.exception("scan site %s failed: %s", site_id, e)

        stats["old_deleted"] = _cleanup_old()
        return stats
    finally:
        _release_lock()


def _scan_site(site_id: str, candidates: List[str], stats: dict) -> None:
    wm = _get_watermark(site_id)
    with DB.connection_context():
        rows = list(
            CrawlerResult
            .select()
            .where(
                (CrawlerResult.site_id == site_id)
                & (CrawlerResult.crawled_at > wm)
            )
            .order_by(CrawlerResult.crawled_at.asc())
        )
    if not rows:
        return

    buckets = defaultdict(list)
    for r in rows:
        buckets[_bucket_key(site_id, r.crawled_at)].append(r)

    max_crawled = wm
    for bk, items in buckets.items():
        cat_counter = Counter(it.category or "other" for it in items)
        main_cat = cat_counter.most_common(1)[0][0]
        site_display = items[0].site_display or ""
        titles = [it.title for it in items[:3]]
        publish_dates = sorted({it.publish_date for it in items if it.publish_date})
        publish_range = publish_dates[0] if publish_dates else ""
        if len(publish_dates) > 1:
            publish_range = f"{publish_dates[0]} ~ {publish_dates[-1]}"

        created_at = max(it.crawled_at for it in items)
        nid = NotificationService.create_notification(
            site_id=site_id, site_display=site_display, category=main_cat,
            batch_key=bk,
            title=f"{site_display or site_id} 检测到 {len(items)} 条新结果",
            summary="\n".join(titles),
            result_ids=[it.id for it in items],
            result_count=len(items), publish_range=publish_range,
            created_at=created_at,
        )
        if nid is None:
            _logger.info("batch_key %s already exists, skip", bk)
        else:
            stats["notifications_created"] += 1
            matched = NotificationService.match_subscribers(site_id, main_cat, candidates)
            if matched:
                NotificationUserService.fan_out(nid, matched)

        max_crawled = max(max_crawled, max(it.crawled_at for it in items))

    if max_crawled > wm:
        _set_watermark(site_id, max_crawled)


def main():
    """CLI 入口：单跑测试。"""
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true", help="跑一轮后退出")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    if args.once:
        stats = scan_once()
        print(f"[notification_generator] {stats}")
        return
    while True:
        try:
            stats = scan_once()
            _logger.info("scan done: %s", stats)
        except Exception as e:
            _logger.exception("scan loop failed: %s", e)
        time.sleep(120)


if __name__ == "__main__":
    main()
