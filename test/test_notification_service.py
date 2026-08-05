"""Service 层：CRUD + 未读查询 + 订阅匹配。"""
import time
import pytest
from api.db.db_models import DB, Notification, NotificationUser, NotificationSubscription
from api.db.services.notification_service import (
    NotificationService, NotificationUserService, NotificationSubscriptionService,
)


@pytest.fixture(autouse=True)
def _clean():
    DB.connect(reuse_if_open=True)
    NotificationUser.delete().execute()
    Notification.delete().execute()
    NotificationSubscription.delete().execute()
    yield


def _make_notif(site_id="x", minute_ts=1, count=3):
    return NotificationService.create_notification(
        site_id=site_id, site_display="测试站 example.com",
        category="news", batch_key=f"{site_id}::{minute_ts}",
        title=f"{site_id} 检测到 {count} 条新结果",
        summary="标题1\n标题2", result_ids=["a", "b", "c"],
        result_count=count, publish_range="2026-08-05 ~ 2026-08-05",
        created_at=minute_ts * 60_000,
    )


def test_create_notification_returns_id():
    nid = _make_notif()
    assert Notification.get(Notification.id == nid).result_count == 3


def test_batch_key_collision_returns_none():
    _make_notif(minute_ts=1)
    nid2 = NotificationService.create_notification(
        site_id="x", site_display="", category="news",
        batch_key="x::1", title="dup", summary="", result_ids=[],
        result_count=0, publish_range="", created_at=1,
    )
    assert nid2 is None  # 幂等：同 batch_key 不重复


def test_unread_list_for_user():
    nid = _make_notif(minute_ts=1)
    NotificationUserService.fan_out(nid, ["userA", "userB"])
    rows, total = NotificationUserService.get_unread("userA", page=1, page_size=20)
    assert total == 1
    assert rows[0]["id"] == nid


def test_mark_read():
    nid = _make_notif(minute_ts=1)
    NotificationUserService.fan_out(nid, ["userA"])
    NotificationUserService.mark_read("userA", [nid])
    rows, total = NotificationUserService.get_unread("userA", page=1, page_size=20)
    assert total == 0


def test_subscription_default_full_subscribe():
    """无 subscription 记录的用户 = 全订阅。"""
    sub = NotificationSubscriptionService.get_or_default("userA")
    assert sub["site_ids"] == []
    assert sub["categories"] == []
    assert sub["browser_push"] is True


def test_subscription_upsert():
    NotificationSubscriptionService.upsert(
        "userA", site_ids=["x"], categories=["news"], browser_push=False,
    )
    sub = NotificationSubscriptionService.get_or_default("userA")
    assert sub["site_ids"] == ["x"]
    assert sub["browser_push"] is False


def test_match_subscribers_full_subscribe_user_included():
    """无 subscription 记录的用户应被匹配到（全订阅语义）。"""
    _make_notif(minute_ts=1)
    # 模拟两个用户：一个有 subscription 全订阅，一个无记录
    NotificationSubscriptionService.upsert("userA", site_ids=[], categories=[])
    # userB 不建 subscription 记录
    users = NotificationService.match_subscribers("x", "news", ["userA", "userB"])
    assert "userA" in users
    assert "userB" in users


def test_match_subscribers_filtered_out():
    """显式不订阅 site_x 的用户被排除。"""
    _make_notif(minute_ts=1)
    NotificationSubscriptionService.upsert("userA", site_ids=["y"], categories=[])
    users = NotificationService.match_subscribers("x", "news", ["userA", "userB"])
    assert "userA" not in users  # 显式订阅了 y，排除 x
    assert "userB" in users  # 无记录 = 全订阅
