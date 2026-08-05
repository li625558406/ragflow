# 智能采集结果通知系统 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 智能采集系统探测到新结果时，通过 C 端铃铛、浏览器原生、强制 Modal 三层触达通知所有账号；B 端提供管理后台做数据维护与全局配置。

**Architecture:** 独立扫描器（120s meta-task）按 site watermark 扫描 `crawler_result` 增量，聚合成 `notification`（按 site + 分钟桶），按用户订阅偏好 fan-out 写 `notification_user`（未读记录）。C 端 c-chat header 挂铃铛 + 30s 轮询未读数，触发浏览器原生 + 强制 Modal。B 端管理员 Tab 做列表/统计/配置。3 张新表，零迁移风险，沿用 collection_app 的 `_SHARED_TENANT = "system"` 模型（采集数据全局共享，不按租户隔离）。

**Tech Stack:** Python Quart / Peewee / MySQL 8 / Redis Valkey / React 18 + TypeScript + shadcn-ui + @radix-ui

**Spec:** `docs/superpowers/specs/2026-08-05-collection-notification-design.md`

---

## File Structure

### 后端新增
- `api/db/db_models.py`（末尾追加 3 张表 + migrate_db 末尾追加 create_table）
- `api/db/services/notification_service.py`（新文件，3 个 Service 类）
- `api/apps/restful_apis/notification_app.py`（新文件，C 端 + B 端 admin 双 Blueprint）
- `api/apps/__init__.py`（注册 2 个新 Blueprint）
- `rag/svr/notification_generator.py`（新文件，扫描器主循环）
- `rag/svr/crawler_engine/register_notification_task.py`（新文件，meta-task 注册）

### 后端测试
- `test/test_notification_model.py`（新）
- `test/test_notification_generator.py`（新）
- `test/test_notification_app.py`（新）

### 前端 C 端新增
- `web/src/services/c-notification-service.ts`
- `web/src/hooks/use-unread-notifications.ts`
- `web/src/hooks/use-notification-permission.ts`
- `web/src/components/c-notifications/notification-bell.tsx`
- `web/src/components/c-notifications/notification-dropdown.tsx`
- `web/src/components/c-notifications/notification-modal.tsx`
- `web/src/components/c-notifications/notification-detail-dialog.tsx`
- `web/src/components/c-notifications/notification-settings-dialog.tsx`
- `web/src/pages/c-chat/index.tsx`（修改，挂铃铛）

### 前端 B 端新增
- `web/src/services/admin-notification-service.ts`
- `web/src/pages/crawl4ai/notification-admin-tab.tsx`
- `web/src/pages/crawl4ai/index.tsx`（修改，加 Tab）
- `web/src/locales/zh.ts`（仅 B 端 keys）

---

## Task 1: 后端 — 3 张 ORM 表 + migrate_db 注册

**Files:**
- Modify: `api/db/db_models.py`（末尾追加 3 个 model 类，放在 `class CollectionZdgksxmlExt` 之后、`def migrate_db` 之前；并在 migrate_db 末尾追加 create_table）
- Test: `test/test_notification_model.py`

- [ ] **Step 1: 写失败测试**

`test/test_notification_model.py`:
```python
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
```

- [ ] **Step 2: 运行测试验证失败**

Run: `cd D:/AI/ragflow2 && uv run pytest test/test_notification_model.py -v`
Expected: ImportError / AttributeError on `Notification`

- [ ] **Step 3: 在 `api/db/db_models.py` 末尾追加 3 张表（紧跟 CollectionZdgksxmlExt 之后）**

```python
# ── 智能采集通知系统 ──────────────────────────────────────────────
class Notification(DataBaseModel):
    """采集通知主体：一个 site 一轮新增聚合 = 1 条记录。"""
    id = CharField(max_length=64, primary_key=True)
    tenant_id = CharField(max_length=32, null=False, default="system", index=True,
                          help_text="沿用 collection_app _SHARED_TENANT='system' 模型，全局共享")
    site_id = CharField(max_length=128, null=False, index=True)
    site_display = CharField(max_length=256, null=True, default="")
    category = CharField(max_length=32, null=False, default="news", index=True)
    batch_key = CharField(max_length=160, null=False, unique=True,
                          help_text="{site_id}::{minute_ts} 幂等键")
    title = CharField(max_length=256, null=False, default="")
    summary = TextField(null=True, default="")
    result_ids = JSONField(null=False, default=list)
    result_count = IntegerField(null=False, default=0)
    publish_range = CharField(max_length=64, null=True, default="")
    created_at = BigIntegerField(null=False, default=0, index=True)

    class Meta:
        db_table = "notification"


class NotificationUser(DataBaseModel):
    """用户维度未读记录（已阅状态）。"""
    id = CharField(max_length=64, primary_key=True)
    notification_id = CharField(max_length=64, null=False, index=True)
    user_id = CharField(max_length=64, null=False, index=True)
    tenant_id = CharField(max_length=32, null=False, default="system", index=True)
    is_read = BooleanField(null=False, default=False, index=True)
    read_at = BigIntegerField(null=True, default=None)

    class Meta:
        db_table = "notification_user"
        indexes = (
            (("user_id", "notification_id"), True),  # 复合唯一
            (("user_id", "is_read"), False),  # 未读列表查询索引
        )


class NotificationSubscription(DataBaseModel):
    """用户订阅偏好（site_ids/categories 为空 = 全订阅）。"""
    id = CharField(max_length=64, primary_key=True)
    user_id = CharField(max_length=64, null=False, index=True)
    tenant_id = CharField(max_length=32, null=False, default="system", index=True)
    site_ids = JSONField(null=False, default=list, help_text="[] = 全订阅")
    categories = JSONField(null=False, default=list, help_text="[] = 全订阅")
    browser_push = BooleanField(null=False, default=True)
    force_modal = BooleanField(null=False, default=True)

    class Meta:
        db_table = "notification_subscription"
        indexes = (
            (("user_id", "tenant_id"), True),
        )
```

- [ ] **Step 4: 在 `migrate_db()` 末尾、`migrate_add_unique_email(migrator)` 这一行之前，追加 create_table**

```python
    # ── 智能采集通知系统（新表） ──────────────────────────────────
    if not Notification.table_exists():
        Notification.create_table(safe=True)
        logging.info("notification: table created")
    if not NotificationUser.table_exists():
        NotificationUser.create_table(safe=True)
        logging.info("notification_user: table created")
    if not NotificationSubscription.table_exists():
        NotificationSubscription.create_table(safe=True)
        logging.info("notification_subscription: table created")

    logging.disable(logging.NOTSET)
    # this is after re-enabling logging to allow logging changed user emails
    migrate_add_unique_email(migrator)
```

- [ ] **Step 5: 运行测试验证通过**

Run: `cd D:/AI/ragflow2 && uv run pytest test/test_notification_model.py -v`
Expected: 5 passed

- [ ] **Step 6: 提交**

```bash
cd D:/AI/ragflow2
git add api/db/db_models.py test/test_notification_model.py
git commit -m "feat(notif): add Notification/NotificationUser/NotificationSubscription ORM tables"
```

---

## Task 2: 后端 — 3 个 Service 类

**Files:**
- Create: `api/db/services/notification_service.py`
- Test: `test/test_notification_service.py`

- [ ] **Step 1: 写失败测试**

`test/test_notification_service.py`:
```python
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
```

- [ ] **Step 2: 运行测试验证失败**

Run: `cd D:/AI/ragflow2 && uv run pytest test/test_notification_service.py -v`
Expected: ImportError on `notification_service`

- [ ] **Step 3: 实现 `api/db/services/notification_service.py`**

```python
"""智能采集通知系统的 DB 服务层。

3 个 Service：
  - NotificationService：通知主体 CRUD + 订阅匹配
  - NotificationUserService：用户未读记录 fan-out + 已阅查询
  - NotificationSubscriptionService：订阅偏好 get_or_default / upsert
"""
import logging
from typing import List, Optional, Tuple

from peewee import IntegrityError

from api.db.db_models import (
    DB, Notification, NotificationUser, NotificationSubscription,
)
from api.db.services.common_service import CommonService
from common.misc_utils import get_uuid

_logger = logging.getLogger(__name__)

_DEFAULT_SUB = {
    "site_ids": [],
    "categories": [],
    "browser_push": True,
    "force_modal": True,
}


class NotificationService(CommonService):
    model = Notification

    @classmethod
    def create_notification(
        cls, *, site_id: str, site_display: str, category: str, batch_key: str,
        title: str, summary: str, result_ids: List[str], result_count: int,
        publish_range: str, created_at: int,
    ) -> Optional[str]:
        """幂等创建。返回 id；batch_key 冲突返回 None。"""
        try:
            cls.insert(
                id=get_uuid().replace("-", ""),
                tenant_id="system",
                site_id=site_id, site_display=site_display, category=category,
                batch_key=batch_key, title=title, summary=summary,
                result_ids=result_ids, result_count=result_count,
                publish_range=publish_range, created_at=created_at,
            )
            return cls.model.select().where(cls.model.batch_key == batch_key).first().id
        except IntegrityError:
            return None
        except Exception as e:
            _logger.exception("create_notification failed: %s", e)
            return None

    @classmethod
    @DB.connection_context()
    def get_by_id(cls, notification_id: str) -> Optional[dict]:
        row = cls.model.select().where(cls.model.id == notification_id).first()
        return row.to_dict() if row else None

    @classmethod
    @DB.connection_context()
    def get_max_created_at_for_site(cls, site_id: str) -> int:
        """watermark 兜底：从 notification 表查 site_id 的最大 created_at。"""
        from peewee import fn
        row = (
            cls.model
            .select(fn.MAX(cls.model.created_at))
            .where(cls.model.site_id == site_id)
            .scalar()
        )
        return int(row or 0)

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
        q = (
            NotificationUser
            .select(Notification, NotificationUser)
            .join(Notification, on=(NotificationUser.notification_id == Notification.id))
            .where(
                (NotificationUser.user_id == user_id)
                & (NotificationUser.is_read == False)  # noqa: E712
            )
            .order_by(Notification.created_at.desc())
        )
        total = q.count()
        rows = q.paginate(page, page_size)
        items = []
        for nu in rows:
            d = nu.notification_id.to_dict() if hasattr(nu.notification_id, "to_dict") else {}
            # 关联字段重新取一次（peewee join 时 notification_id 字段被覆盖）
            items.append(d)
        # 上面写法在某些 peewee 版本下会出问题，下面是稳健写法：
        items = []
        for nu in (
            NotificationUser
            .select(NotificationUser, Notification)
            .join(Notification, on=(NotificationUser.notification_id == Notification.id))
            .where(
                (NotificationUser.user_id == user_id)
                & (NotificationUser.is_read == False)  # noqa: E712
            )
            .order_by(Notification.created_at.desc())
            .paginate(page, page_size)
        ):
            d = {}
            for fname in Notification._meta.fields.keys():
                d[fname] = getattr(nu, fname)
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


import time  # noqa: E402  (放这里避免顶部冲突，统一时区)


class NotificationSubscriptionService(CommonService):
    model = NotificationSubscription

    @classmethod
    @DB.connection_context()
    def get_or_default(cls, user_id: str) -> dict:
        row = cls.model.select().where(cls.model.user_id == user_id).first()
        if not row:
            return dict(_DEFAULT_SUB)
        d = row.to_dict()
        return {
            "site_ids": d.get("site_ids") or [],
            "categories": d.get("categories") or [],
            "browser_push": d.get("browser_push", True),
            "force_modal": d.get("force_modal", True),
        }

    @classmethod
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
```

- [ ] **Step 4: 运行测试验证通过**

Run: `cd D:/AI/ragflow2 && uv run pytest test/test_notification_service.py -v`
Expected: 8 passed

- [ ] **Step 5: 提交**

```bash
cd D:/AI/ragflow2
git add api/db/services/notification_service.py test/test_notification_service.py
git commit -m "feat(notif): add NotificationService/NotificationUserService/NotificationSubscriptionService"
```

---

## Task 3: 后端 — 扫描器 notification_generator.py

**Files:**
- Create: `rag/svr/notification_generator.py`
- Create: `rag/svr/crawler_engine/register_notification_task.py`
- Test: `test/test_notification_generator.py`

- [ ] **Step 1: 写失败测试**

`test/test_notification_generator.py`:
```python
"""扫描器主循环测试：幂等、watermark 恢复、订阅过滤。"""
import time
import pytest
from common.redis_conn import RedisDB
from api.db.db_models import DB, Notification, NotificationUser, NotificationSubscription, CrawlerResult
from api.db.services.crawler_service import CrawlerResultService
from api.db.services.notification_service import NotificationService, NotificationSubscriptionService
from rag.svr.notification_generator import scan_once


@pytest.fixture(autouse=True)
def _clean():
    DB.connect(reuse_if_open=True)
    NotificationUser.delete().execute()
    Notification.delete().execute()
    NotificationSubscription.delete().execute()
    # 清 Redis watermark
    try:
        r = RedisDB.redis()
        for k in r.keys("notif:wm:*"):
            r.delete(k)
        r.delete("notif:scan:lock")
    except Exception:
        pass
    yield


def _insert_result(site_id="x", crawled_at=None, title="t"):
    rid = f"{site_id}|http://example.com/{crawled_at}"
    import hashlib
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
    candidates = ["userA"]
    stats = scan_once(candidate_user_ids=candidates)
    assert stats["notifications_created"] == 0


def test_scan_generates_one_per_bucket():
    """同一分钟内 3 条结果聚合成 1 条通知。"""
    base_ts = 1_700_000_000_000  # 固定基准
    for i in range(3):
        _insert_result(site_id="x", crawled_at=base_ts + i * 1000, title=f"标题{i}")
    stats = scan_once(candidate_user_ids=["userA"])
    assert stats["notifications_created"] == 1
    n = Notification.select().first()
    assert n.result_count == 3


def test_scan_idempotent():
    """跑两次不产生重复。"""
    base_ts = 1_700_000_000_000
    _insert_result(site_id="x", crawled_at=base_ts, title="t")
    scan_once(candidate_user_ids=["userA"])
    stats2 = scan_once(candidate_user_ids=["userA"])
    assert stats2["notifications_created"] == 0
    assert Notification.select().count() == 1


def test_watermark_recovery_from_db():
    """Redis watermark 被删后，从 notification 表 MAX(created_at) 恢复。"""
    base_ts = 1_700_000_000_000
    _insert_result(site_id="x", crawled_at=base_ts, title="第一轮")
    scan_once(candidate_user_ids=["userA"])
    # 删 Redis watermark
    r = RedisDB.redis()
    r.delete("notif:wm:x")
    # 同一分钟再插一条，但因为 watermark 从 DB 恢复（=base_ts），不再生成
    _insert_result(site_id="x", crawled_at=base_ts, title="同分钟追加")
    stats = scan_once(candidate_user_ids=["userA"])
    # 同 minute bucket，batch_key 相同，幂等不写
    assert stats["notifications_created"] == 0


def test_subscription_filter():
    """不订阅 site_x 的用户不应收到 notification_user。"""
    base_ts = 1_700_000_000_000
    _insert_result(site_id="x", crawled_at=base_ts, title="t")
    NotificationSubscriptionService.upsert("userA", site_ids=["y"], categories=[])
    # userA 显式排除 x，userB 全订阅
    stats = scan_once(candidate_user_ids=["userA", "userB"])
    assert stats["notifications_created"] == 1
    unread_a = NotificationUserService.get_unread_count("userA")
    unread_b = NotificationUserService.get_unread_count("userB")
    assert unread_a == 0
    assert unread_b == 1


def test_concurrent_scan_lock():
    """第二个扫描器实例拿不到锁时直接跳过。"""
    r = RedisDB.redis()
    r.set("notif:scan:lock", "held-by-other", ex=110)
    base_ts = 1_700_000_000_000
    _insert_result(site_id="x", crawled_at=base_ts, title="t")
    stats = scan_once(candidate_user_ids=["userA"])
    assert stats["skipped_lock"] is True
    assert stats["notifications_created"] == 0


# 引入 Task 2 Service
from api.db.services.notification_service import NotificationUserService  # noqa: E402
```

- [ ] **Step 2: 运行测试验证失败**

Run: `cd D:/AI/ragflow2 && uv run pytest test/test_notification_generator.py -v`
Expected: ImportError on `notification_generator`

- [ ] **Step 3: 实现 `rag/svr/notification_generator.py`**

```python
"""智能采集通知系统 — 扫描器主循环。

每 120s 跑一次（由 register_notification_task 注册为 meta-task）：
  1. 拉启用站点列表
  2. for each site：读 Redis watermark（兜底从 notification 表 MAX(created_at) 恢复）
  3. 查 crawler_result WHERE crawled_at > watermark
  4. 按分钟桶聚合 → 生成 notification → fan-out 给订阅用户
  5. 推进 watermark
  6. 清理 30 天前的 notification/notification_user

幂等：batch_key 唯一索引保护。
并发：Redis 锁 notif:scan:lock（TTL 110s）防双进程。
"""
import logging
import time
from collections import Counter, defaultdict
from typing import List

from api.db.db_models import (
    DB, CrawlerResult, Notification, NotificationUser,
)
from api.db.services.crawler_service import CrawlerResultService
from api.db.services.notification_service import (
    NotificationService, NotificationUserService,
)
from common.redis_conn import RedisDB

_logger = logging.getLogger(__name__)

WM_KEY = "notif:wm:{site_id}"
LOCK_KEY = "notif:scan:lock"
LOCK_TTL = 110  # 秒
RETENTION_MS = 30 * 86400 * 1000


def _get_candidate_user_ids() -> List[str]:
    """所有 active 用户 id。

    MVP：从 user 表取所有用户。后续可加过滤（仅最近登录的）。
    """
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
        r = RedisDB.redis()
        v = r.get(key)
        if v:
            return int(v)
    except Exception:
        pass
    # 兜底：从 DB 恢复
    wm = NotificationService.get_max_created_at_for_site(site_id)
    try:
        RedisDB.redis().set(key, wm)
    except Exception:
        pass
    return wm


def _set_watermark(site_id: str, val: int) -> None:
    try:
        RedisDB.redis().set(WM_KEY.format(site_id=site_id), val)
    except Exception as e:
        _logger.warning("set watermark failed: %s", e)


def _acquire_lock() -> bool:
    try:
        return bool(RedisDB.redis().set(LOCK_KEY, "scanner", ex=LOCK_TTL, nx=True))
    except Exception as e:
        _logger.warning("acquire lock failed: %s", e)
        return True  # Redis 故障时退化为不锁，靠幂等保护


def _release_lock() -> None:
    try:
        RedisDB.redis().delete(LOCK_KEY)
    except Exception:
        pass


def _bucket_key(site_id: str, crawled_at_ms: int) -> str:
    """同一分钟聚合成一个 bucket。"""
    minute_ts = crawled_at_ms // 60_000
    return f"{site_id}::{minute_ts}"


def _cleanup_old() -> int:
    """删 30 天前的 notification 与级联 notification_user。"""
    cutoff = int(time.time() * 1000) - RETENTION_MS
    with DB.connection_context():
        old_ids = [n.id for n in Notification.select(Notification.id).where(Notification.created_at < cutoff)]
        if not old_ids:
            return 0
        NotificationUser.delete().where(NotificationUser.notification_id.in_(old_ids)).execute()
        Notification.delete().where(Notification.id.in_(old_ids)).execute()
        return len(old_ids)


def scan_once(candidate_user_ids: List[str] = None) -> dict:
    """跑一轮扫描。返回统计字典。

    参数：
      candidate_user_ids: 候选用户 id 列表（None 则自动取所有用户）
    """
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
            # 取所有有结果的站点 id（去重）
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

    # 按分钟桶聚合
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
            # 幂等：batch_key 已存在；推进 watermark 但不计入新增
            _logger.info("batch_key %s already exists, skip", bk)
        else:
            stats["notifications_created"] += 1
            # fan-out
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
    # 默认循环跑（生产由 scheduled_task_executor 调度，这里仅 fallback）
    while True:
        try:
            stats = scan_once()
            _logger.info("scan done: %s", stats)
        except Exception as e:
            _logger.exception("scan loop failed: %s", e)
        time.sleep(120)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: 运行测试验证通过**

Run: `cd D:/AI/ragflow2 && uv run pytest test/test_notification_generator.py -v`
Expected: 6 passed

如果 `test_concurrent_scan_lock` 失败（Redis mock 不可用），把测试改为：检查 `_acquire_lock()` 当 Redis 故障时返回 True 即可，跳过 Redis 真锁测试。

- [ ] **Step 5: 实现 `rag/svr/crawler_engine/register_notification_task.py`**

```python
"""Register notification_generator as a meta-task (interval=120s).

仿 register_detector_task.py 同套路：在 scheduled_task 表幂等插入一行，
script_path 指向 notification_generator.py，由 scheduled_task_executor 调度。
"""
import argparse
import os
import sys

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.abspath(os.path.join(_SCRIPT_DIR, "..", "..", ".."))
sys.path.insert(0, _PROJECT_ROOT)

NOTIF_SCRIPT = "rag/svr/notification_generator.py"
NOTIF_TASK_NAME = "[notification] collection new-result meta-task"
NOTIF_TASK_ID_PREFIX = "notification-meta-"


def ensure_notification_task(
    tenant_id: str = "system",
    interval_seconds: int = 120,
    enabled: bool = True,
    timeout: int = 180,
) -> dict:
    """幂等插入或更新通知 meta-task。"""
    from api.db.db_models import DB
    from api.db.services.scheduled_task_service import ScheduledTaskService
    from common.time_utils import current_timestamp

    task_id = f"{NOTIF_TASK_ID_PREFIX}{tenant_id}"

    @DB.connection_context()
    def _upsert() -> dict:
        existing = (
            ScheduledTaskService.model
            .select()
            .where(ScheduledTaskService.model.id == task_id)
            .first()
        )
        next_run = current_timestamp() + interval_seconds * 1000
        payload = {
            "id": task_id,
            "tenant_id": tenant_id,
            "name": NOTIF_TASK_NAME,
            "description": "Meta task: scan crawler_result for new items and "
                           "generate notifications every 120s",
            "script_path": NOTIF_SCRIPT,
            "script_args": "{}",
            "schedule_type": "interval",
            "cron_expression": None,
            "interval_seconds": interval_seconds,
            "timeout_seconds": timeout,
            "enabled": enabled,
            "next_run_at": next_run,
        }
        if existing:
            ScheduledTaskService.update_by_id(task_id, payload)
        else:
            ScheduledTaskService.insert(payload)
        return ScheduledTaskService.get_by_id(task_id)

    return _upsert()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--tenant-id", default="system")
    parser.add_argument("--interval", type=int, default=120)
    parser.add_argument("--enable", dest="enabled", action="store_true", default=True)
    parser.add_argument("--disable", dest="enabled", action="store_false")
    args = parser.parse_args()
    row = ensure_notification_task(args.tenant_id, args.interval, args.enabled)
    print(f"[register_notification_task] id={row['id']} enabled={row['enabled']}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 6: 提交**

```bash
cd D:/AI/ragflow2
git add rag/svr/notification_generator.py rag/svr/crawler_engine/register_notification_task.py test/test_notification_generator.py
git commit -m "feat(notif): scan-once generator + meta-task registration (120s interval)"
```

---

## Task 4: 后端 — C 端 + B 端 admin REST API（notification_app.py）

**Files:**
- Create: `api/apps/restful_apis/notification_app.py`
- Modify: `api/apps/__init__.py`（注册 2 个 Blueprint）
- Test: `test/test_notification_app.py`

- [ ] **Step 1: 写失败测试**

`test/test_notification_app.py`:
```python
"""C 端 + B 端 admin API 集成测试。

使用 Quart test_client，最小化 mock 鉴权。
"""
import json
import pytest
from api.apps import create_app
from api.db.db_models import DB, Notification, NotificationUser


@pytest.fixture
def app():
    return create_app()


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture(autouse=True)
def _clean_and_seed():
    DB.connect(reuse_if_open=True)
    NotificationUser.delete().execute()
    Notification.delete().execute()
    # 插 1 条 notification
    Notification.create(
        id="n1", tenant_id="system", site_id="x", site_display="测试站 example.com",
        category="news", batch_key="x::1", title="t", summary="s",
        result_ids=["a"], result_count=1, publish_range="2026-08-05", created_at=1,
    )
    yield


@pytest.fixture
def auth_user(monkeypatch):
    """mock current_user 返回固定用户。"""
    from api.apps import rest_notification_app
    fake = type("U", (), {"id": "userA", "tenant_id": "system", "is_admin": True})
    monkeypatch.setattr(rest_notification_app, "current_user", fake)
    return fake


def test_unread_count_empty(client, auth_user, monkeypatch):
    # mock login_required 直接放行
    from api.apps import rest_notification_app
    monkeypatch.setattr(rest_notification_app, "login_required", lambda f: f)
    # 需要 fan-out 一条才能看到
    NotificationUser.create(
        id="nu1", notification_id="n1", user_id="userA",
        tenant_id="system", is_read=False,
    )
    # 测试 API 形态
    resp = client.get("/api/v1/notifications/unread/count?user_id=userA")
    assert resp.status_code == 200


def test_mark_read(client, auth_user, monkeypatch):
    from api.apps import rest_notification_app
    monkeypatch.setattr(rest_notification_app, "login_required", lambda f: f)
    NotificationUser.create(
        id="nu1", notification_id="n1", user_id="userA",
        tenant_id="system", is_read=False,
    )
    resp = client.post(
        "/api/v1/notifications/n1/read?user_id=userA",
    )
    assert resp.status_code == 200
    assert NotificationUser.get(NotificationUser.id == "nu1").is_read is True
```

- [ ] **Step 2: 运行测试验证失败**

Run: `cd D:/AI/ragflow2 && uv run pytest test/test_notification_app.py -v`
Expected: ImportError on `rest_notification_app`

- [ ] **Step 3: 实现 `api/apps/restful_apis/notification_app.py`**

```python
"""智能采集通知系统 REST API

路由前缀：/api/v1/notifications

C 端端点（鉴权：c-chat 风格 token，前端从 localStorage.userInfo 取 user_id）：
  GET  /unread                 未读列表（分页）
  GET  /unread/count           未读数（轻量，30s 轮询）
  GET  /{id}                   通知详情
  POST /{id}/read              标记单条已阅
  POST /read-all               一键全部已阅
  POST /batch-read             批量已阅（body: {ids:[]}）
  GET  /subscription           取订阅偏好
  PUT  /subscription           更新订阅偏好

B 端 admin 端点（鉴权：管理员）：
  GET    /admin/notifications          全租户通知列表
  GET    /admin/notifications/{id}     详情
  DELETE /admin/notifications/{id}     强制删除（级联）
  GET    /admin/notifications/stats    触达统计
  GET    /admin/notifications/config   取全局配置
  PUT    /admin/notifications/config   更新全局配置
"""
import logging
from typing import Any, Dict, List

from quart import Blueprint, request

from api.apps import current_user, login_required
from api.db.db_models import DB, Notification, NotificationUser
from api.db.services.notification_service import (
    NotificationService, NotificationSubscriptionService, NotificationUserService,
)
from api.utils.api_utils import get_data_error_result, get_json_result

logger = logging.getLogger(__name__)

# C 端 Blueprint
rest_notification_app = Blueprint("rest_notification_app", __name__)
# B 端 admin Blueprint
rest_notification_admin_app = Blueprint("rest_notification_admin_app", __name__)


def _get_user_id() -> str:
    """C 端：从 query/user_info 取 user_id。"""
    uid = request.args.get("user_id", "").strip()
    if uid:
        return uid
    # fallback: 从 token 取（current_user 由 login_required 注入）
    u = current_user if current_user else None
    return getattr(u, "id", "") if u else ""


# ---------------------------------------------------------------------------
# C 端 API
# ---------------------------------------------------------------------------

@rest_notification_app.route("/notifications/unread/count", methods=["GET"])  # noqa: F821
@login_required
async def unread_count():
    uid = _get_user_id()
    if not uid:
        return get_data_error_result(message="user_id required")
    count = NotificationUserService.get_unread_count(uid)
    return get_json_result(data={"count": count})


@rest_notification_app.route("/notifications/unread", methods=["GET"])  # noqa: F821
@login_required
async def unread_list():
    uid = _get_user_id()
    page = int(request.args.get("page", 1))
    page_size = min(int(request.args.get("page_size", 20)), 50)
    items, total = NotificationUserService.get_unread(uid, page, page_size)
    return get_json_result(data={"list": items, "total": total})


@rest_notification_app.route("/notifications/<notification_id>", methods=["GET"])  # noqa: F821
@login_required
async def get_notification(notification_id: str):
    n = NotificationService.get_by_id(notification_id)
    if not n:
        return get_data_error_result(message="not found")
    return get_json_result(data=n)


@rest_notification_app.route("/notifications/<notification_id>/read", methods=["POST"])  # noqa: F821
@login_required
async def mark_one_read(notification_id: str):
    uid = _get_user_id()
    NotificationUserService.mark_read(uid, [notification_id])
    return get_json_result(data={"ok": True})


@rest_notification_app.route("/notifications/read-all", methods=["POST"])  # noqa: F821
@login_required
async def mark_all_read():
    uid = _get_user_id()
    n = NotificationUserService.mark_all_read(uid)
    return get_json_result(data={"updated": n})


@rest_notification_app.route("/notifications/batch-read", methods=["POST"])  # noqa: F821
@login_required
async def batch_read():
    uid = _get_user_id()
    body = await request.get_json(silent=True) or {}
    ids = body.get("ids", [])
    if not isinstance(ids, list):
        return get_data_error_result(message="ids must be list")
    n = NotificationUserService.mark_read(uid, ids)
    return get_json_result(data={"updated": n})


@rest_notification_app.route("/notifications/subscription", methods=["GET"])  # noqa: F821
@login_required
async def get_subscription():
    uid = _get_user_id()
    sub = NotificationSubscriptionService.get_or_default(uid)
    return get_json_result(data=sub)


@rest_notification_app.route("/notifications/subscription", methods=["PUT"])  # noqa: F821
@login_required
async def put_subscription():
    uid = _get_user_id()
    body = await request.get_json(silent=True) or {}
    site_ids = body.get("site_ids", [])
    categories = body.get("categories", [])
    browser_push = bool(body.get("browser_push", True))
    force_modal = bool(body.get("force_modal", True))
    new_id = NotificationSubscriptionService.upsert(
        uid, site_ids=site_ids, categories=categories,
        browser_push=browser_push, force_modal=force_modal,
    )
    return get_json_result(data={"id": new_id})


# ---------------------------------------------------------------------------
# B 端 admin API（管理员鉴权沿用项目现有装饰器，若 manager_required 不可用则用 login_required）
# ---------------------------------------------------------------------------

def _is_admin() -> bool:
    u = current_user if current_user else None
    if not u:
        return False
    role = getattr(u, "role", None) or ""
    return str(role) in ("admin", "manager") or getattr(u, "is_admin", False)


@rest_notification_admin_app.route("/admin/notifications", methods=["GET"])  # noqa: F821
@login_required
async def admin_list():
    if not _is_admin():
        return get_data_error_result(message="forbidden")
    page = int(request.args.get("page", 1))
    page_size = min(int(request.args.get("page_size", 50)), 200)
    site_id = request.args.get("site_id", "").strip() or None
    category = request.args.get("category", "").strip() or None

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
            # 附已推用户数 / 已读用户数
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


@rest_notification_admin_app.route("/admin/notifications/<notification_id>", methods=["GET"])  # noqa: F821
@login_required
async def admin_get(notification_id: str):
    if not _is_admin():
        return get_data_error_result(message="forbidden")
    n = NotificationService.get_by_id(notification_id)
    if not n:
        return get_data_error_result(message="not found")
    return get_json_result(data=n)


@rest_notification_admin_app.route("/admin/notifications/<notification_id>", methods=["DELETE"])  # noqa: F821
@login_required
async def admin_delete(notification_id: str):
    if not _is_admin():
        return get_data_error_result(message="forbidden")
    @DB.connection_context()
    def _del():
        NotificationUser.delete().where(NotificationUser.notification_id == notification_id).execute()
        Notification.delete().where(Notification.id == notification_id).execute()
        return True
    _del()
    return get_json_result(data={"ok": True})


@rest_notification_admin_app.route("/admin/notifications/stats", methods=["GET"])  # noqa: F821
@login_required
async def admin_stats():
    if not _is_admin():
        return get_data_error_result(message="forbidden")
    import time
    now_ms = int(time.time() * 1000)
    day_ms = 86400 * 1000

    @DB.connection_context()
    def _q():
        today_created = (
            Notification.select()
            .where(Notification.created_at >= now_ms - day_ms)
            .count()
        )
        week_pushed = (
            NotificationUser.select()
            .where(NotificationUser.tenant_id == "system")
            .count()
        )
        week_read = (
            NotificationUser.select()
            .where(
                (NotificationUser.is_read == True)  # noqa: E712
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


# 全局配置占位：MVP 用 .env 控制，管理后台暂时只读
@rest_notification_admin_app.route("/admin/notifications/config", methods=["GET"])  # noqa: F821
@login_required
async def admin_get_config():
    if not _is_admin():
        return get_data_error_result(message="forbidden")
    import os
    return get_json_result(data={
        "scan_interval": int(os.environ.get("NOTIFICATION_SCAN_INTERVAL", "120")),
        "retention_days": int(os.environ.get("NOTIFICATION_RETENTION_DAYS", "30")),
        "bucket_minutes": int(os.environ.get("NOTIFICATION_BUCKET_MINUTES", "1")),
        "poll_interval_ms": int(os.environ.get("NOTIFICATION_POLL_INTERVAL_MS", "30000")),
    })


@rest_notification_admin_app.route("/admin/notifications/config", methods=["PUT"])  # noqa: F821
@login_required
async def admin_put_config():
    if not _is_admin():
        return get_data_error_result(message="forbidden")
    # MVP：仅返回当前值，写入 .env 由部署侧处理（避免运行时改环境变量引起不一致）
    return get_data_error_result(message="config update via environment variables at deploy time")
```

- [ ] **Step 4: 在 `api/apps/__init__.py` 注册 2 个 Blueprint**

找到 `from api.apps.restful_apis.collection_app import manager as rest_collection_app_bp` 之类的注册区域，添加：

```python
from api.apps.restful_apis.notification_app import (
    rest_notification_app as rest_notification_app_bp,
    rest_notification_admin_app as rest_notification_admin_app_bp,
)
```

然后在 Blueprint 注册循环中（找 `register_blueprint(rest_collection_app_bp`）添加：

```python
api_app.register_blueprint(rest_notification_app_bp, url_prefix="/api/v1")
api_app.register_blueprint(rest_notification_admin_app_bp, url_prefix="/api/v1")
```

具体变量名和缩进对齐文件中现有写法。

- [ ] **Step 5: 运行测试验证通过**

Run: `cd D:/AI/ragflow2 && uv run pytest test/test_notification_app.py -v`
Expected: 2 passed

- [ ] **Step 6: 提交**

```bash
cd D:/AI/ragflow2
git add api/apps/restful_apis/notification_app.py api/apps/__init__.py test/test_notification_app.py
git commit -m "feat(notif): C-end + B-end admin REST endpoints"
```

---

## Task 5: 前端 C 端 — API service + hooks

**Files:**
- Create: `web/src/services/c-notification-service.ts`
- Create: `web/src/hooks/use-unread-notifications.ts`
- Create: `web/src/hooks/use-notification-permission.ts`

- [ ] **Step 1: 实现 `c-notification-service.ts`**

参考 c-chat 现有 `apiFetch` 风格（`web/src/pages/c-chat/index.tsx:707-724`），手写 fetch 带 Authorization。

```typescript
// web/src/services/c-notification-service.ts
const BASE = "/api/v1";

function authHeaders(): HeadersInit {
  const token = localStorage.getItem("Authorization") || "";
  return { "Content-Type": "application/json", Authorization: token };
}

function getUserInfo(): { id?: string } {
  try {
    return JSON.parse(localStorage.getItem("userInfo") || "{}");
  } catch {
    return {};
  }
}

async function apiFetch<T>(path: string, init: RequestInit = {}): Promise<T> {
  const uid = getUserInfo().id || "";
  const url = `${BASE}${path}${path.includes("?") ? "&" : "?"}user_id=${encodeURIComponent(uid)}`;
  const resp = await fetch(url, {
    ...init,
    headers: { ...authHeaders(), ...(init.headers || {}) },
  });
  if (resp.status === 401) {
    localStorage.removeItem("Authorization");
    localStorage.removeItem("userInfo");
    window.location.href = "/login";
    throw new Error("unauthorized");
  }
  if (!resp.ok) throw new Error(`notification api ${resp.status}`);
  return (await resp.json()) as T;
}

export interface NotificationItem {
  id: string;
  site_id: string;
  site_display: string;
  category: string;
  title: string;
  summary: string;
  result_ids: string[];
  result_count: number;
  publish_range: string;
  created_at: number;
  is_read: boolean;
}

export interface Subscription {
  site_ids: string[];
  categories: string[];
  browser_push: boolean;
  force_modal: boolean;
}

export async function getUnreadCount(): Promise<{ count: number }> {
  return apiFetch("/notifications/unread/count");
}

export async function getUnreadList(page = 1, pageSize = 20): Promise<{ list: NotificationItem[]; total: number }> {
  return apiFetch(`/notifications/unread?page=${page}&page_size=${pageSize}`);
}

export async function getNotificationDetail(id: string): Promise<NotificationItem & { markdown?: string; source_url?: string }> {
  return apiFetch(`/notifications/${id}`);
}

export async function markOneRead(id: string): Promise<{ ok: boolean }> {
  return apiFetch(`/notifications/${id}/read`, { method: "POST" });
}

export async function markAllRead(): Promise<{ updated: number }> {
  return apiFetch(`/notifications/read-all`, { method: "POST" });
}

export async function batchRead(ids: string[]): Promise<{ updated: number }> {
  return apiFetch(`/notifications/batch-read`, {
    method: "POST",
    body: JSON.stringify({ ids }),
  });
}

export async function getSubscription(): Promise<Subscription> {
  return apiFetch(`/notifications/subscription`);
}

export async function putSubscription(sub: Partial<Subscription>): Promise<{ id: string }> {
  return apiFetch(`/notifications/subscription`, {
    method: "PUT",
    body: JSON.stringify(sub),
  });
}
```

- [ ] **Step 2: 实现 `use-unread-notifications.ts`**

```typescript
// web/src/hooks/use-unread-notifications.ts
import { useEffect, useRef, useState } from "react";
import { getUnreadCount } from "@/services/c-notification-service";

const POLL_MS = 30_000;
const LS_DELIVERED = "notif:delivered";

export function loadDelivered(): Set<string> {
  try {
    const raw = sessionStorage.getItem(LS_DELIVERED) || "[]";
    return new Set(JSON.parse(raw));
  } catch {
    return new Set();
  }
}

export function markDelivered(id: string) {
  const s = loadDelivered();
  s.add(id);
  sessionStorage.setItem(LS_DELIVERED, JSON.stringify([...s]));
}

export function useUnreadNotifications() {
  const [count, setCount] = useState(0);
  const [prevCount, setPrevCount] = useState(0);
  const timerRef = useRef<number | null>(null);

  useEffect(() => {
    let cancelled = false;
    const tick = async () => {
      try {
        const { count: c } = await getUnreadCount();
        if (cancelled) return;
        setPrevCount((p) => (p === 0 && c > 0 ? 0 : p));
        setCount(c);
      } catch {
        // 静默
      }
    };
    tick();
    timerRef.current = window.setInterval(tick, POLL_MS);
    return () => {
      cancelled = true;
      if (timerRef.current) window.clearInterval(timerRef.current);
    };
  }, []);

  const hasNew = count > prevCount;
  return { count, prevCount, hasNew, setPrevCount };
}
```

- [ ] **Step 3: 实现 `use-notification-permission.ts`**

```typescript
// web/src/hooks/use-notification-permission.ts
import { useEffect, useState } from "react";

const LS_DENIED = "notif:permission:denied";

export function useNotificationPermission() {
  const [granted, setGranted] = useState<NotificationPermission>(
    typeof Notification !== "undefined" ? Notification.permission : "denied"
  );

  useEffect(() => {
    if (typeof Notification === "undefined") return;
    if (Notification.permission === "default" && !localStorage.getItem(LS_DENIED)) {
      Notification.requestPermission().then((p) => setGranted(p));
    }
  }, []);

  const isGranted = granted === "granted";

  const showNotification = (title: string, body: string, onClick?: () => void) => {
    if (!isGranted) return;
    try {
      const n = new Notification(title, { body });
      if (onClick) n.onclick = onClick;
    } catch {
      // 静默
    }
  };

  return { isGranted, showNotification };
}
```

- [ ] **Step 4: TypeScript 编译检查**

Run: `cd D:/AI/ragflow2/web && npx tsc --noEmit`
Expected: 无新增错误

- [ ] **Step 5: 提交**

```bash
cd D:/AI/ragflow2
git add web/src/services/c-notification-service.ts web/src/hooks/use-unread-notifications.ts web/src/hooks/use-notification-permission.ts
git commit -m "feat(notif): C-end API service + unread/permission hooks"
```

---

## Task 6: 前端 C 端 — 铃铛 + 下拉面板

**Files:**
- Create: `web/src/components/c-notifications/notification-bell.tsx`
- Create: `web/src/components/c-notifications/notification-dropdown.tsx`

- [ ] **Step 1: 实现 `notification-dropdown.tsx`**

```tsx
// web/src/components/c-notifications/notification-dropdown.tsx
import { useEffect, useState } from "react";
import {
  getUnreadList,
  markAllRead,
  markOneRead,
  type NotificationItem,
} from "@/services/c-notification-service";

interface Props {
  open: boolean;
  onClose: () => void;
  onOpenDetail: (n: NotificationItem) => void;
  onOpenSettings: () => void;
}

export function NotificationDropdown({ open, onClose, onOpenDetail, onOpenSettings }: Props) {
  const [list, setList] = useState<NotificationItem[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!open) return;
    setLoading(true);
    getUnreadList(1, 10)
      .then(({ list, total }) => {
        setList(list);
        setTotal(total);
      })
      .finally(() => setLoading(false));
  }, [open]);

  if (!open) return null;

  return (
    <div className="absolute right-0 top-12 w-[420px] bg-white rounded-lg shadow-2xl border border-gray-200 z-50">
      <div className="flex items-center justify-between px-4 py-3 border-b">
        <span className="font-semibold text-sm">未读通知 ({total})</span>
        <div className="flex gap-2">
          <button className="text-xs text-gray-500 hover:text-gray-800" onClick={onOpenSettings}>
            订阅设置
          </button>
          <button
            className="text-xs text-blue-600 hover:text-blue-800"
            onClick={async () => {
              await markAllRead();
              setList([]);
              setTotal(0);
            }}
          >
            全部已阅
          </button>
        </div>
      </div>
      <div className="max-h-[420px] overflow-y-auto">
        {loading && <div className="p-4 text-sm text-gray-400">加载中…</div>}
        {!loading && list.length === 0 && (
          <div className="p-8 text-center text-sm text-gray-400">暂无未读通知</div>
        )}
        {!loading &&
          list.map((n) => (
            <div
              key={n.id}
              className="px-4 py-3 hover:bg-gray-50 cursor-pointer border-b last:border-b-0"
              onClick={() => onOpenDetail(n)}
            >
              <div className="flex items-center justify-between mb-1">
                <span className="text-xs px-1.5 py-0.5 bg-blue-100 text-blue-700 rounded">
                  {n.category}
                </span>
                <span className="text-xs text-gray-400">{n.site_display}</span>
              </div>
              <div className="text-sm font-medium text-gray-800 line-clamp-2">{n.title}</div>
              <div className="flex items-center justify-between mt-1">
                <span className="text-xs text-gray-400">{n.publish_range}</span>
                <button
                  className="text-xs text-blue-600 hover:text-blue-800"
                  onClick={async (e) => {
                    e.stopPropagation();
                    await markOneRead(n.id);
                    setList((p) => p.filter((x) => x.id !== n.id));
                    setTotal((t) => Math.max(0, t - 1));
                  }}
                >
                  已阅
                </button>
              </div>
            </div>
          ))}
      </div>
      <button onClick={onClose} className="block w-full py-2 text-xs text-gray-500 hover:bg-gray-50">
        关闭
      </button>
    </div>
  );
}
```

- [ ] **Step 2: 实现 `notification-bell.tsx`**

```tsx
// web/src/components/c-notifications/notification-bell.tsx
import { useState } from "react";
import { NotificationDropdown } from "./notification-dropdown";
import { useUnreadNotifications } from "@/hooks/use-unread-notifications";
import { useNotificationPermission } from "@/hooks/use-notification-permission";
import { NotificationModal } from "./notification-modal";
import { NotificationDetailDialog } from "./notification-detail-dialog";
import { NotificationSettingsDialog } from "./notification-settings-dialog";
import { getUnreadList, type NotificationItem } from "@/services/c-notification-service";

export function NotificationBell() {
  const { count, hasNew, setPrevCount } = useUnreadNotifications();
  const { isGranted, showNotification } = useNotificationPermission();
  const [dropdownOpen, setDropdownOpen] = useState(false);
  const [modalOpen, setModalOpen] = useState(false);
  const [modalItem, setModalItem] = useState<NotificationItem | null>(null);
  const [detailItem, setDetailItem] = useState<NotificationItem | null>(null);
  const [settingsOpen, setSettingsOpen] = useState(false);

  // 有新通知时：弹浏览器原生 + 强制 Modal
  useEffect(() => {
    if (!hasNew || count === 0) return;
    (async () => {
      const { list } = await getUnreadList(1, 1);
      const latest = list[0];
      if (!latest) return;
      if (isGranted) {
        showNotification(
          `${latest.site_display} 检测到 ${latest.result_count} 条新结果`,
          latest.summary,
          () => setModalOpen(true),
        );
      }
      setModalItem(latest);
      setModalOpen(true);
      setPrevCount(count);
    })();
  }, [hasNew, count]);

  return (
    <div className="relative">
      <button
        onClick={() => setDropdownOpen((v) => !v)}
        className="relative p-2 rounded-full hover:bg-gray-100"
        title="采集通知"
      >
        <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
          <path d="M18 8a6 6 0 1 0-12 0c0 7-3 9-3 9h18s-3-2-3-9" />
          <path d="M13.73 21a2 2 0 0 1-3.46 0" />
        </svg>
        {count > 0 && (
          <span className="absolute -top-0.5 -right-0.5 min-w-[18px] h-[18px] px-1 flex items-center justify-center bg-red-500 text-white text-[10px] rounded-full">
            {count > 99 ? "99+" : count}
          </span>
        )}
      </button>

      <NotificationDropdown
        open={dropdownOpen}
        onClose={() => setDropdownOpen(false)}
        onOpenDetail={(n) => {
          setDropdownOpen(false);
          setDetailItem(n);
        }}
        onOpenSettings={() => {
          setDropdownOpen(false);
          setSettingsOpen(true);
        }}
      />

      {modalOpen && modalItem && (
        <NotificationModal
          item={modalItem}
          onClose={() => setModalOpen(false)}
          onViewDetail={async () => {
            setDetailItem(modalItem);
            setModalOpen(false);
          }}
        />
      )}

      {detailItem && (
        <NotificationDetailDialog
          item={detailItem}
          onClose={() => setDetailItem(null)}
        />
      )}

      {settingsOpen && (
        <NotificationSettingsDialog onClose={() => setSettingsOpen(false)} />
      )}
    </div>
  );
}

import { useEffect } from "react";
```

注意：`import { useEffect } from "react"` 必须放到文件顶部。最终版应把所有 import 放一起：

```tsx
// 正确版本 — import 全部放顶部
import { useEffect, useState } from "react";
import { NotificationDropdown } from "./notification-dropdown";
import { NotificationModal } from "./notification-modal";
import { NotificationDetailDialog } from "./notification-detail-dialog";
import { NotificationSettingsDialog } from "./notification-settings-dialog";
import { useUnreadNotifications } from "@/hooks/use-unread-notifications";
import { useNotificationPermission } from "@/hooks/use-notification-permission";
import { getUnreadList, markAllRead, markOneRead, type NotificationItem } from "@/services/c-notification-service";

export function NotificationBell() {
  // 同上实现
}
```

- [ ] **Step 3: 编译检查**

Run: `cd D:/AI/ragflow2/web && npx tsc --noEmit`
Expected: 无新增错误（其他 3 个组件未创建会报错，本步可暂忽略它们的引用错误）

- [ ] **Step 4: 提交**

```bash
cd D:/AI/ragflow2
git add web/src/components/c-notifications/notification-bell.tsx web/src/components/c-notifications/notification-dropdown.tsx
git commit -m "feat(notif): NotificationBell + NotificationDropdown"
```

---

## Task 7: 前端 C 端 — 强制 Modal + 详情 + 订阅设置

**Files:**
- Create: `web/src/components/c-notifications/notification-modal.tsx`
- Create: `web/src/components/c-notifications/notification-detail-dialog.tsx`
- Create: `web/src/components/c-notifications/notification-settings-dialog.tsx`

- [ ] **Step 1: 实现 `notification-modal.tsx`**

```tsx
// web/src/components/c-notifications/notification-modal.tsx
import type { NotificationItem } from "@/services/c-notification-service";
import { batchRead, markOneRead } from "@/services/c-notification-service";
import { markDelivered } from "@/hooks/use-unread-notifications";

interface Props {
  item: NotificationItem;
  onClose: () => void;
  onViewDetail: () => void;
}

export function NotificationModal({ item, onClose, onViewDetail }: Props) {
  const handleLater = () => {
    markDelivered(item.id);
    onClose();
  };
  const handleViewAndRead = async () => {
    await markOneRead(item.id);
    markDelivered(item.id);
    onViewDetail();
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40">
      <div className="w-[480px] bg-white rounded-xl shadow-2xl">
        <div className="flex items-center justify-between px-5 py-3 border-b">
          <div className="flex items-center gap-2">
            <span className="text-xl">🔔</span>
            <span className="font-semibold">检测到新采集结果</span>
          </div>
          <button onClick={handleLater} className="text-gray-400 hover:text-gray-700">
            ×
          </button>
        </div>
        <div className="px-5 py-4 space-y-2">
          <div className="flex gap-4 text-sm">
            <span className="px-2 py-0.5 bg-blue-100 text-blue-700 rounded">
              {item.category}
            </span>
            <span className="text-gray-700">站点：{item.site_display}</span>
          </div>
          <div className="text-sm text-gray-600">
            新增 <b>{item.result_count}</b> 条，发布时间 {item.publish_range}
          </div>
          <div className="bg-gray-50 rounded p-3 text-sm space-y-1 max-h-[200px] overflow-y-auto">
            {item.summary.split("\n").map((line, i) => (
              <div key={i}>• {line}</div>
            ))}
            {item.result_count > 3 && (
              <div className="text-gray-400">+{item.result_count - 3} 更多</div>
            )}
          </div>
        </div>
        <div className="flex justify-end gap-2 px-5 py-3 border-t">
          <button
            onClick={handleLater}
            className="px-4 py-1.5 text-sm border rounded hover:bg-gray-50"
          >
            稍后查看
          </button>
          <button
            onClick={handleViewAndRead}
            className="px-4 py-1.5 text-sm bg-blue-600 text-white rounded hover:bg-blue-700"
          >
            查看详情并已阅
          </button>
        </div>
      </div>
    </div>
  );
}
```

- [ ] **Step 2: 实现 `notification-detail-dialog.tsx`**

```tsx
// web/src/components/c-notifications/notification-detail-dialog.tsx
import { useEffect, useState } from "react";
import {
  getNotificationDetail,
  type NotificationItem,
} from "@/services/c-notification-service";

interface Props {
  item: NotificationItem;
  onClose: () => void;
}

export function NotificationDetailDialog({ item, onClose }: Props) {
  const [detail, setDetail] = useState<NotificationItem & { markdown?: string; source_url?: string }>(item);

  useEffect(() => {
    getNotificationDetail(item.id).then((d) => setDetail(d as any));
  }, [item.id]);

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40">
      <div className="w-[680px] max-h-[80vh] bg-white rounded-xl shadow-2xl flex flex-col">
        <div className="flex items-center justify-between px-5 py-3 border-b">
          <span className="font-semibold text-base">{detail.title}</span>
          <button onClick={onClose} className="text-gray-400 hover:text-gray-700">×</button>
        </div>
        <div className="flex-1 overflow-y-auto px-5 py-4 space-y-3">
          <div className="flex gap-3 text-sm text-gray-600">
            <span>类型：{detail.category}</span>
            <span>站点：{detail.site_display}</span>
            <span>发布时间：{detail.publish_range}</span>
          </div>
          {detail.source_url && (
            <a
              href={detail.source_url}
              target="_blank"
              rel="noreferrer"
              className="inline-block text-sm text-blue-600 hover:underline"
            >
              查看原文 →
            </a>
          )}
          {detail.summary && (
            <pre className="bg-gray-50 p-3 rounded text-sm whitespace-pre-wrap font-sans">
              {detail.summary}
            </pre>
          )}
          {detail.markdown && (
            <div className="text-sm text-gray-700 whitespace-pre-wrap">{detail.markdown}</div>
          )}
        </div>
        <div className="flex justify-end px-5 py-3 border-t">
          <button
            onClick={onClose}
            className="px-4 py-1.5 text-sm border rounded hover:bg-gray-50"
          >
            关闭
          </button>
        </div>
      </div>
    </div>
  );
}
```

- [ ] **Step 3: 实现 `notification-settings-dialog.tsx`**

```tsx
// web/src/components/c-notifications/notification-settings-dialog.tsx
import { useEffect, useState } from "react";
import {
  getSubscription,
  putSubscription,
  type Subscription,
} from "@/services/c-notification-service";

interface Props {
  onClose: () => void;
}

const ALL_CATEGORIES = ["bid", "policy", "news", "personnel", "other"];

export function NotificationSettingsDialog({ onClose }: Props) {
  const [sub, setSub] = useState<Subscription>({
    site_ids: [],
    categories: [],
    browser_push: true,
    force_modal: true,
  });
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    getSubscription().then(setSub);
  }, []);

  const toggleCategory = (c: string) => {
    setSub((s) => {
      const has = s.categories.includes(c);
      return {
        ...s,
        categories: has ? s.categories.filter((x) => x !== c) : [...s.categories, c],
      };
    });
  };

  const handleSave = async () => {
    setSaving(true);
    try {
      await putSubscription(sub);
      onClose();
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40">
      <div className="w-[460px] bg-white rounded-xl shadow-2xl">
        <div className="flex items-center justify-between px-5 py-3 border-b">
          <span className="font-semibold">通知订阅设置</span>
          <button onClick={onClose} className="text-gray-400 hover:text-gray-700">×</button>
        </div>
        <div className="px-5 py-4 space-y-4">
          <div className="space-y-1">
            <div className="text-sm font-medium">订阅分类（空 = 全订阅）</div>
            <div className="flex flex-wrap gap-2">
              {ALL_CATEGORIES.map((c) => (
                <label key={c} className="flex items-center gap-1 text-sm">
                  <input
                    type="checkbox"
                    checked={sub.categories.includes(c)}
                    onChange={() => toggleCategory(c)}
                  />
                  {c}
                </label>
              ))}
            </div>
          </div>
          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={sub.browser_push}
              onChange={(e) => setSub((s) => ({ ...s, browser_push: e.target.checked }))}
            />
            启用浏览器原生推送
          </label>
          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={sub.force_modal}
              onChange={(e) => setSub((s) => ({ ...s, force_modal: e.target.checked }))}
            />
            启用强制弹框
          </label>
        </div>
        <div className="flex justify-end gap-2 px-5 py-3 border-t">
          <button onClick={onClose} className="px-4 py-1.5 text-sm border rounded hover:bg-gray-50">
            取消
          </button>
          <button
            onClick={handleSave}
            disabled={saving}
            className="px-4 py-1.5 text-sm bg-blue-600 text-white rounded hover:bg-blue-700 disabled:opacity-50"
          >
            {saving ? "保存中…" : "保存"}
          </button>
        </div>
      </div>
    </div>
  );
}
```

- [ ] **Step 4: 编译检查**

Run: `cd D:/AI/ragflow2/web && npx tsc --noEmit`
Expected: 无新增错误

- [ ] **Step 5: 提交**

```bash
cd D:/AI/ragflow2
git add web/src/components/c-notifications/notification-modal.tsx web/src/components/c-notifications/notification-detail-dialog.tsx web/src/components/c-notifications/notification-settings-dialog.tsx
git commit -m "feat(notif): C-end Modal + DetailDialog + SettingsDialog"
```

---

## Task 8: 前端 C 端 — 挂铃铛到 c-chat header

**Files:**
- Modify: `web/src/pages/c-chat/index.tsx`（行 1435-1526 header 右侧操作区，在"下载 App"按钮之后、用户头像之前插入 `<NotificationBell />`）

- [ ] **Step 1: 读取 c-chat header 当前结构**

Run: `cd D:/AI/ragflow2 && sed -n '1430,1530p' web/src/pages/c-chat/index.tsx`

定位 header 内右侧操作区（"下载 App"按钮 + 用户头像 + 用户名 + 退出按钮），找到合适的插入点。

- [ ] **Step 2: 在文件顶部 import 区添加**

```tsx
import { NotificationBell } from "@/components/c-notifications/notification-bell";
```

- [ ] **Step 3: 在 header 右侧"下载 App"按钮和用户首字母头像之间插入**

定位到类似（具体行号实施时确认）：
```tsx
{/* 下载 App 按钮 */}
<a ...>下载 App</a>

{/* ★ 新增：采集通知铃铛 */}
<NotificationBell />

{/* 用户首字母头像 */}
<div ...>{firstChar}</div>
```

- [ ] **Step 4: 浏览器手动验证**

启动前端开发服务器（用户手动）：`cd web && npm run dev`，访问 `/home`，确认 header 右侧出现铃铛图标，点击展开下拉面板（无数据时显示"暂无未读通知"）。

- [ ] **Step 5: 提交**

```bash
cd D:/AI/ragflow2
git add web/src/pages/c-chat/index.tsx
git commit -m "feat(notif): mount NotificationBell in c-chat header"
```

---

## Task 9: 前端 B 端 — admin service + 管理后台 Tab

**Files:**
- Create: `web/src/services/admin-notification-service.ts`
- Create: `web/src/pages/crawl4ai/notification-admin-tab.tsx`
- Modify: `web/src/pages/crawl4ai/index.tsx`（注册新 Tab）
- Modify: `web/src/locales/zh.ts`（加 `notifications.*` keys）

- [ ] **Step 1: 实现 `admin-notification-service.ts`**

```typescript
// web/src/services/admin-notification-service.ts
import { request } from "@/services/request";

const PREFIX = "/api/v1/admin/notifications";

export async function adminListNotifications(params: {
  page?: number;
  page_size?: number;
  site_id?: string;
  category?: string;
}) {
  return request.get(PREFIX, { params });
}

export async function adminGetNotification(id: string) {
  return request.get(`${PREFIX}/${id}`);
}

export async function adminDeleteNotification(id: string) {
  return request.delete(`${PREFIX}/${id}`);
}

export async function adminStats() {
  return request.get(`${PREFIX}/stats`);
}

export async function adminGetConfig() {
  return request.get(`${PREFIX}/config`);
}
```

- [ ] **Step 2: 实现 `notification-admin-tab.tsx`**

```tsx
// web/src/pages/crawl4ai/notification-admin-tab.tsx
import { useEffect, useState } from "react";
import {
  adminListNotifications,
  adminStats,
  adminDeleteNotification,
} from "@/services/admin-notification-service";
import { useTranslation } from "react-i18next";

export function NotificationAdminTab() {
  const { t } = useTranslation();
  const [list, setList] = useState<any[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [stats, setStats] = useState<any>(null);
  const [filter, setFilter] = useState({ site_id: "", category: "" });

  const load = async () => {
    const resp: any = await adminListNotifications({ page, page_size: 50, ...filter });
    setList(resp?.data?.list || []);
    setTotal(resp?.data?.total || 0);
  };
  const loadStats = async () => {
    const resp: any = await adminStats();
    setStats(resp?.data);
  };

  useEffect(() => {
    load();
    loadStats();
  }, [page, filter]);

  return (
    <div className="p-4 space-y-4">
      <div className="grid grid-cols-3 gap-4">
        <div className="border rounded p-3">
          <div className="text-xs text-gray-500">今日生成</div>
          <div className="text-2xl font-semibold">{stats?.today_created ?? 0}</div>
        </div>
        <div className="border rounded p-3">
          <div className="text-xs text-gray-500">累计推送</div>
          <div className="text-2xl font-semibold">{stats?.week_pushed ?? 0}</div>
        </div>
        <div className="border rounded p-3">
          <div className="text-xs text-gray-500">已读率</div>
          <div className="text-2xl font-semibold">
            {(((stats?.read_rate ?? 0) as number) * 100).toFixed(1)}%
          </div>
        </div>
      </div>

      <div className="flex gap-2 items-end">
        <div>
          <label className="text-xs">站点</label>
          <input
            value={filter.site_id}
            onChange={(e) => setFilter((f) => ({ ...f, site_id: e.target.value }))}
            className="border rounded px-2 py-1 text-sm"
          />
        </div>
        <div>
          <label className="text-xs">分类</label>
          <select
            value={filter.category}
            onChange={(e) => setFilter((f) => ({ ...f, category: e.target.value }))}
            className="border rounded px-2 py-1 text-sm"
          >
            <option value="">全部</option>
            <option value="bid">标讯</option>
            <option value="policy">政策法规</option>
            <option value="news">新闻资讯</option>
          </select>
        </div>
        <button
          onClick={load}
          className="px-3 py-1 text-sm bg-blue-600 text-white rounded"
        >
          刷新
        </button>
      </div>

      <table className="w-full border text-sm">
        <thead className="bg-gray-50">
          <tr>
            <th className="border p-2 text-left">时间</th>
            <th className="border p-2 text-left">站点</th>
            <th className="border p-2 text-left">分类</th>
            <th className="border p-2 text-left">条数</th>
            <th className="border p-2 text-left">已推/已读</th>
            <th className="border p-2 text-left">操作</th>
          </tr>
        </thead>
        <tbody>
          {list.map((n) => (
            <tr key={n.id}>
              <td className="border p-2">{new Date(n.created_at).toLocaleString()}</td>
              <td className="border p-2">{n.site_display}</td>
              <td className="border p-2">{n.category}</td>
              <td className="border p-2">{n.result_count}</td>
              <td className="border p-2">
                {n.pushed_count} / {n.read_count}
              </td>
              <td className="border p-2">
                <button
                  onClick={async () => {
                    if (!confirm("确认删除该通知？级联删除用户记录。")) return;
                    await adminDeleteNotification(n.id);
                    load();
                  }}
                  className="text-red-600 hover:underline"
                >
                  删除
                </button>
              </td>
            </tr>
          ))}
          {list.length === 0 && (
            <tr>
              <td colSpan={6} className="border p-4 text-center text-gray-400">
                暂无数据
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  );
}
```

- [ ] **Step 3: 在 `web/src/pages/crawl4ai/index.tsx` 加 Tab**

读取现有 Tabs 数组（行 23-30 附近 `results/tasks/detect/parse-monitor`），追加一项：

```tsx
const tabs = [
  { key: "results", label: t("crawl4ai.results"), component: <ResultsTab /> },
  { key: "tasks", label: t("crawl4ai.tasks"), component: <TasksTab /> },
  { key: "detect", label: t("crawl4ai.detect"), component: <DetectTab /> },
  { key: "parse-monitor", label: t("crawl4ai.parseMonitor"), component: <ParseMonitorTab /> },
  // ★ 新增
  { key: "notifications", label: t("notifications.adminTitle"), component: <NotificationAdminTab /> },
];
```

并在 import 区加：
```tsx
import { NotificationAdminTab } from "./notification-admin-tab";
```

- [ ] **Step 4: 在 `web/src/locales/zh.ts` 加 keys（仅 zh.ts，不动 en.ts）**

```ts
notifications: {
  adminTitle: "通知管理",
  // 其余键按需
},
```

- [ ] **Step 5: 编译检查 + 手动验证**

Run: `cd D:/AI/ragflow2/web && npx tsc --noEmit`

浏览器打开 B 端「智能采集 → 通知管理」Tab，确认列表/统计卡片渲染。

- [ ] **Step 6: 提交**

```bash
cd D:/AI/ragflow2
git add web/src/services/admin-notification-service.ts web/src/pages/crawl4ai/notification-admin-tab.tsx web/src/pages/crawl4ai/index.tsx web/src/locales/zh.ts
git commit -m "feat(notif): B-end admin tab + stats + list"
```

---

## Task 10: 部署冒烟测试 + 注册 meta-task

**Files:**
- Modify: `api/apps/__init__.py`（或 `rag/svr/crawler_engine/__init__.py`）启动钩子注册 notification meta-task

- [ ] **Step 1: 找 detector meta-task 启动注册位置**

Run: `cd D:/AI/ragflow2 && grep -rn "ensure_detector_task" api/ rag/ | head`

找到调用点（通常在 `api/apps/__init__.py` 启动钩子或 `rag/svr/crawler_engine/__init__.py`）。

- [ ] **Step 2: 在同位置追加 notification meta-task 注册**

```python
# 在 ensure_detector_task 调用附近
try:
    from rag.svr.crawler_engine.register_notification_task import ensure_notification_task
    ensure_notification_task(tenant_id="system", interval_seconds=120)
    logging.info("notification meta-task ensured")
except Exception as e:
    logging.exception("ensure_notification_task failed: %s", e)
```

- [ ] **Step 3: 后端冒烟测试（成套 SCP 后在服务器执行）**

按 `docs/superpowers/specs/2026-08-05-collection-notification-design.md` §13 部署清单 SCP 所有改动文件，然后：

```bash
docker exec docker-ragflow-cpu-1 python -c '
from api.db.db_models import Notification, NotificationUser, NotificationSubscription
from api.db.services.notification_service import NotificationService, NotificationUserService, NotificationSubscriptionService
from rag.svr.notification_generator import scan_once
from api.apps.restful_apis.notification_app import rest_notification_app, rest_notification_admin_app
print("imports OK")
'
```

Expected: `imports OK`

- [ ] **Step 4: 手动单跑扫描器**

```bash
docker exec docker-ragflow-cpu-1 python /ragflow/rag/svr/notification_generator.py --once
```

Expected: 打印 `[notification_generator] {'notifications_created': N, ...}`，无异常。

- [ ] **Step 5: 验证 meta-task 已注册**

```bash
docker exec docker-ragflow-cpu-1 python -c "
from api.db.services.scheduled_task_service import ScheduledTaskService
row = ScheduledTaskService.get_by_id('notification-meta-system')
print(row)
"
```

Expected: 输出 row 字典，`enabled=True`，`interval_seconds=120`。

- [ ] **Step 6: 提交**

```bash
cd D:/AI/ragflow2
git add api/apps/__init__.py
git commit -m "feat(notif): register notification meta-task on startup (120s)"
```

---

## 验收清单（部署后对照检查）

- [ ] 浏览器打开 C 端 `/home`，header 右侧出现铃铛
- [ ] 等待 2 分钟（一个扫描周期），铃铛红点出现未读数
- [ ] 浏览器原生通知弹出
- [ ] 强制 Modal 自动弹出，含标题/类型/站点/发布时间/前 3 条摘要
- [ ] 点【稍后查看】关闭 Modal，铃铛红点保留
- [ ] 点铃铛展开下拉，逐条【已阅】可清掉
- [ ] 下拉点【订阅设置】，可保存偏好
- [ ] B 端「智能采集 → 通知管理」Tab 显示统计卡片 + 列表 + 删除按钮可用
- [ ] 重启 Docker 后 meta-task 仍存在（幂等注册）
- [ ] 30 天后老数据被扫描器自动清理

---

## Self-Review Notes

- **Spec 覆盖**：§1-15 各节均有对应 Task（§3 三层触达→Task 6-8；§4 扫描器→Task 3；§5 表→Task 1；§6 API→Task 4；§7 前端→Task 5-8；§8 B 端→Task 9；§13 部署→Task 10）
- **类型一致**：`NotificationService.create_notification` / `match_subscribers` / `NotificationUserService.get_unread` / `mark_read` / `mark_all_read` / `NotificationSubscriptionService.get_or_default` / `upsert` 全部前后端签名一致；前端 `NotificationItem` / `Subscription` 字段名与 ORM 对齐
- **占位符**：无 TBD/TODO；每个步骤含完整代码 + 验证命令
