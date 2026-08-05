"""ORM 模型基础测试：表存在、字段存在、复合唯一索引可命中。"""
import pytest
from api.db.db_models import (
    DB, Notification, NotificationUser, NotificationSubscription,
)


@pytest.fixture(scope="module", autouse=True)
def _ensure_tables():
    """启动前确保 3 张表已建。"""
    DB.connect(reuse_if_open=True)
    for m in (Notification, NotificationUser, NotificationSubscription):
        if not m.table_exists():
            m.create_table(safe=True)
    yield
    DB.close()


def test_notification_table_exists():
    assert Notification.table_exists()


def test_notification_user_table_exists():
    assert NotificationUser.table_exists()


def test_notification_subscription_table_exists():
    assert NotificationSubscription.table_exists()


def test_notification_batch_key_unique():
    """同 batch_key 第二次插入应抛 IntegrityError。"""
    from peewee import IntegrityError
    Notification.create(
        id="t1", tenant_id="system", site_id="x", category="news",
        batch_key="x::1", title="t", summary="", result_ids=[], result_count=1,
        publish_range="", created_at=1,
    )
    with pytest.raises(IntegrityError):
        Notification.create(
            id="t2", tenant_id="system", site_id="x", category="news",
            batch_key="x::1", title="t2", summary="", result_ids=[], result_count=1,
            publish_range="", created_at=2,
        )


def test_notification_user_user_notif_unique():
    from peewee import IntegrityError
    NotificationUser.create(
        id="u1", notification_id="t1", user_id="userA",
        tenant_id="system", is_read=False,
    )
    with pytest.raises(IntegrityError):
        NotificationUser.create(
            id="u2", notification_id="t1", user_id="userA",
            tenant_id="system", is_read=False,
        )
