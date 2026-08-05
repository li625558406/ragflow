"""扫描器主循环测试：幂等、watermark 恢复、订阅过滤。

测试时间戳使用接近当前时间（而非历史的 1_700_000_000_000），因为扫描器
有 30 天保留清理：created_at < now-30d 的 notification 会被立即清理掉。
"""
import hashlib
import time

import pytest

from api.db.db_models import (
    DB, CrawlerResult, Notification, NotificationUser, NotificationSubscription,
)
from api.db.services.crawler_service import CrawlerResultService
from api.db.services.notification_service import (
    NotificationService, NotificationUserService, NotificationSubscriptionService,
)
from rag.utils.redis_conn import REDIS_CONN
from rag.svr.notification_generator import (
    scan_once, WM_KEY, LOCK_KEY,
)

# 使用当前毫秒时间戳作为基准（避免被 30 天清理逻辑误删）
BASE_TS = int(time.time() * 1000)


def _is_redis_available() -> bool:
    """检测 Redis 是否可连接。dev 环境无 Redis 时部分测试需跳过。"""
    try:
        REDIS_CONN.REDIS.ping()
        return True
    except Exception:
        return False


@pytest.fixture(autouse=True)
def _clean():
    DB.connect(reuse_if_open=True)
    NotificationUser.delete().execute()
    Notification.delete().execute()
    NotificationSubscription.delete().execute()
    CrawlerResult.delete().execute()
    try:
        for k in REDIS_CONN.REDIS.keys("notif:wm:*"):
            REDIS_CONN.delete(k)
        REDIS_CONN.delete(LOCK_KEY)
    except Exception:
        pass
    yield


def _insert_result(site_id="x", crawled_at=None, title="t"):
    rid = f"{site_id}|http://example.com/{crawled_at}"
    hid = hashlib.md5(rid.encode()).hexdigest()
    CrawlerResultService.upsert_result({
        "id": hid, "task_id": "t1", "tenant_id": "system",
        "site_id": site_id, "site_display": "测试站 example.com",
        "category": "news", "title": title,
        "source_url": f"http://example.com/{crawled_at}",
        "publish_date": "2026-08-05", "markdown": "",
        "extracted_json": {}, "attachments": [],
        "status": "raw", "crawled_at": crawled_at,
    })
    return hid


def test_scan_empty_no_notification():
    """无新结果不写 notification。"""
    stats = scan_once(candidate_user_ids=["userA"])
    assert stats["notifications_created"] == 0


def test_scan_generates_one_per_bucket():
    """同一分钟内 3 条结果聚合成 1 条通知。"""
    base_ts = BASE_TS
    for i in range(3):
        _insert_result(site_id="x", crawled_at=base_ts + i * 1000, title=f"标题{i}")
    stats = scan_once(candidate_user_ids=["userA"])
    assert stats["notifications_created"] == 1
    n = Notification.select().first()
    assert n is not None
    assert n.result_count == 3


def test_scan_idempotent():
    """跑两次不产生重复。"""
    _insert_result(site_id="x", crawled_at=BASE_TS, title="t")
    scan_once(candidate_user_ids=["userA"])
    stats2 = scan_once(candidate_user_ids=["userA"])
    assert stats2["notifications_created"] == 0
    assert Notification.select().count() == 1


def test_watermark_recovery_from_db():
    """Redis watermark 被删后，从 notification 表 MAX(created_at) 恢复。

    需要 Redis 可用（否则 delete watermark 不会真正生效，skip 后无法验证恢复路径）。
    """
    if not _is_redis_available():
        pytest.skip("Redis unavailable in dev env")
    _insert_result(site_id="x", crawled_at=BASE_TS, title="第一轮")
    scan_once(candidate_user_ids=["userA"])
    REDIS_CONN.delete(WM_KEY.format(site_id="x"))
    _insert_result(site_id="x", crawled_at=BASE_TS, title="同分钟追加")
    stats = scan_once(candidate_user_ids=["userA"])
    assert stats["notifications_created"] == 0


def test_subscription_filter():
    """不订阅 site_x 的用户不应收到 notification_user。"""
    _insert_result(site_id="x", crawled_at=BASE_TS, title="t")
    NotificationSubscriptionService.upsert("userA", site_ids=["y"], categories=[])
    stats = scan_once(candidate_user_ids=["userA", "userB"])
    assert stats["notifications_created"] == 1
    unread_a = NotificationUserService.get_unread_count("userA")
    unread_b = NotificationUserService.get_unread_count("userB")
    assert unread_a == 0
    assert unread_b == 1


def test_concurrent_scan_lock():
    """第二个扫描器实例拿不到锁时直接跳过。需要 Redis 可用。"""
    if not _is_redis_available():
        pytest.skip("Redis unavailable in dev env")
    REDIS_CONN.REDIS.set(LOCK_KEY, "held-by-other", ex=110)
    _insert_result(site_id="x", crawled_at=BASE_TS, title="t")
    stats = scan_once(candidate_user_ids=["userA"])
    assert stats["skipped_lock"] is True
    assert stats["notifications_created"] == 0
