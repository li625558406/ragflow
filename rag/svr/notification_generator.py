"""智能采集通知系统 — 扫描器主循环。

每 120s 跑一次（由 register_notification_task 注册为 meta-task）：
  1. 拉启用站点列表（从 crawler_result distinct site_id）
  2. for each site：读 Redis watermark（兜底从 notification 表 MAX(created_at) 恢复）
  3. 查 crawler_result WHERE crawled_at > watermark
  4. 按 5 分钟桶聚合 (BUCKET_WINDOW_MS) → 生成 notification → fan-out 给订阅用户
  5. 推进 watermark
  6. 清理 30 天前的 notification/notification_user

幂等：Notification.batch_key 唯一索引保护。
并发：Redis 锁 notif:scan:lock（TTL 110s）防双进程。
"""
import logging
import os
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

# 聚合窗口大小 (毫秒). 同站点在此时长内的所有新结果合并为一条通知.
# 历史 1 分钟桶导致长爬取 (如 mohurd 35 分钟走 700 篇归档) 产生 35 条
# 通知刷屏; 5 分钟桶降到 7 条, 同时不丢失任何 result_id.
BUCKET_WINDOW_MS = 5 * 60_000

# 站点级冷却 (毫秒). 同一站点在该时长内的新结果合并进最近一条通知,
# 而不是按 5 分钟桶各建一条. 解决高频采集站点 (如 cebpubservice 一天
# 被反复触发 10+ 次, 每次只爬 1-2 条) 导致通知列表同一站点刷屏的问题.
# 可用环境变量 NOTIF_SITE_COOLDOWN_MS 覆盖.
SITE_COOLDOWN_MS = int(os.environ.get("NOTIF_SITE_COOLDOWN_MS", 6 * 3600 * 1000))


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
    """Redis 取，失败/不存在则从 notification 表 MAX 恢复，写回 Redis。

    注意: 必须用 REDIS_CONN.REDIS (原生 redis-py 客户端) 而非 REDIS_CONN.set,
    因为后者默认带 1h TTL (redis_conn.py:200 exp=3600). watermark 1h 后过期,
    fallback 查空 notification 表 (清理/截断后) 返回 0 → 下次扫描把全量
    crawler_result 重新打包成通知, 一次生成几十上百条刷屏.
    """
    key = WM_KEY.format(site_id=site_id)
    try:
        v = REDIS_CONN.REDIS.get(key)
        if v:
            # 顺便去掉老 key 的 TTL (历史用 REDIS_CONN.set 写入会带 1h 过期),
            # 防止 1h 后再次触发 fallback 全量重打包.
            REDIS_CONN.REDIS.persist(key)
            return int(v)
    except Exception:
        pass
    wm = NotificationService.get_max_created_at_for_site(site_id)
    try:
        REDIS_CONN.REDIS.set(key, wm)  # 无 TTL, 持久进度
    except Exception:
        pass
    return wm


def _set_watermark(site_id: str, val: int) -> None:
    """无 TTL 写入 watermark (见 _get_watermark 注释)."""
    try:
        REDIS_CONN.REDIS.set(WM_KEY.format(site_id=site_id), val)
    except Exception as e:
        _logger.warning("set watermark failed: %s", e)


_LOCK_TOKEN: str = None  # set when lock acquired

# Lua CAS: release only if value matches token
_RELEASE_LOCK_LUA = """
if redis.call('get', KEYS[1]) == ARGV[1] then
    return redis.call('del', KEYS[1])
else
    return 0
end
"""


def _acquire_lock() -> bool:
    """SET NX 原子抢锁，记录本进程 token 供 CAS 释放。

    Redis 故障时退化为不锁（返回 True），但不设置 _LOCK_TOKEN，因此 _release_lock 为 no-op，
    避免误删其他 scanner 持有的锁。靠 batch_key 幂等保护。
    """
    global _LOCK_TOKEN
    try:
        _LOCK_TOKEN = os.urandom(8).hex()
        # 直接打 valkey 客户端拿 nx 语义（REDIS_CONN.set 不支持 nx 形参）
        ok = REDIS_CONN.REDIS.set(LOCK_KEY, _LOCK_TOKEN, ex=LOCK_TTL, nx=True)
        if not ok:
            _LOCK_TOKEN = None
        return bool(ok)
    except Exception as e:
        _logger.warning("acquire lock failed: %s", e)
        _LOCK_TOKEN = None
        return True  # Redis 故障时退化为不锁，靠 batch_key 幂等保护


def _release_lock() -> None:
    """Lua CAS：仅当 lock value == 本进程 token 时才 DEL，避免误删他人锁。"""
    tok = _LOCK_TOKEN
    if not tok:
        return
    try:
        REDIS_CONN.REDIS.eval(_RELEASE_LOCK_LUA, 1, LOCK_KEY, tok)
    except Exception as e:
        _logger.debug("release lock failed: %s", e)


def _bucket_key(site_id: str, crawled_at_ms: int) -> str:
    window_ts = crawled_at_ms // BUCKET_WINDOW_MS
    return f"{site_id}::{window_ts}"


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
        "notifications_merged": 0,
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

    # 站点级冷却：cooldown 内已有通知则本轮所有 buckets 都合并进它，
    # 不新建。这避免高频采集站点 (cebpubservice 等) 一天内产 N 条通知。
    recent = NotificationService.get_recent_for_site(site_id, SITE_COOLDOWN_MS)
    if recent:
        _logger.info(
            "site %s in cooldown (last notif %s, within %ds), will merge",
            site_id, recent.get("id"), SITE_COOLDOWN_MS // 1000,
        )

    max_crawled = wm
    for bk, items in buckets.items():
        cat_counter = Counter(it.category or "other" for it in items)
        main_cat = cat_counter.most_common(1)[0][0]
        site_display = items[0].site_display or ""
        titles = [it.title for it in items[:3]]
        publish_dates = sorted({it.publish_date for it in items if it.publish_date})
        new_result_ids = [it.id for it in items]

        if recent:
            # 合并进 cooldown 内的最近通知：append result_ids + 重算 count
            NotificationService.append_results(
                recent["id"],
                new_result_ids=new_result_ids,
                new_publish_dates=publish_dates,
            )
            stats["notifications_merged"] = stats.get("notifications_merged", 0) + 1
        else:
            publish_range = publish_dates[0] if publish_dates else ""
            if len(publish_dates) > 1:
                publish_range = f"{publish_dates[0]} ~ {publish_dates[-1]}"
            created_at = max(it.crawled_at for it in items)
            nid = NotificationService.create_notification(
                site_id=site_id, site_display=site_display, category=main_cat,
                batch_key=bk,
                title=f"{site_display or site_id} 检测到 {len(items)} 条新结果",
                summary="\n".join(titles),
                result_ids=new_result_ids,
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
                # 本轮该站点新建了一条，后续 buckets 合并进它而非再新建
                recent = {"id": nid}

        max_crawled = max(max_crawled, max(it.crawled_at for it in items))

    if max_crawled > wm:
        _set_watermark(site_id, max_crawled)


def main():
    """CLI 入口：单跑测试。

    兼容 task_executor 无条件追加的参数（与 crawler_detector.py 同套路），
    避免 argparse 拒绝导致 scheduled_task 卡 running。
    """
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true", help="跑一轮后退出")
    # task_executor 兼容参数（此处均忽略，scanner 自己拉全量用户）
    parser.add_argument("--tenant-id", default="", help="Compatibility (ignored)")
    parser.add_argument("--task-name", default="", help="Compatibility (ignored)")
    parser.add_argument("--kb-id", default="", help="Compatibility (ignored)")
    parser.add_argument("--target-url", default="", help="Compatibility (ignored)")
    parser.add_argument("--access-token", default="", help="Compatibility (ignored)")
    parser.add_argument("--llm-id", default="", help="Compatibility (ignored)")
    parser.add_argument("--llm-model", default="", help="Compatibility (ignored)")
    parser.add_argument("--script-args", default="", help="Compatibility (ignored)")
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
