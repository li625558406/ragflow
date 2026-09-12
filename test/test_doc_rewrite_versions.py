# -*- coding: utf-8 -*-
"""版本链纯函数 + flow switch_current 参数测试。

不依赖真实 MySQL/Redis/MinIO：
- 纯函数直接断言；
- add_version 通过 __wrapped__ 绕开 connection_context（本地无 DB），
  DB/FlowInstance/insert 全部 stub；
- register_chat_version 通过 monkeypatch 模块属性命中延迟 import。
"""
import contextlib
from types import SimpleNamespace
from unittest.mock import patch

from peewee import IntegrityError

from rag.svr.document_rewrite.versions import (
    SOURCE_TYPES,
    _root_id_from_doc_id,
    root_id_for_doc,
)


class _FakeField:
    """peewee 列占位：表达式不求值，desc()/== 返回真值即可。"""

    def desc(self):
        return "DESC"

    def __eq__(self, other):
        return self


class _FakeQuery:
    def __init__(self, first_result=None):
        self._first = first_result

    def where(self, *a):
        return self

    def order_by(self, *a):
        return self

    def first(self):
        return self._first


class TestPureFunctions:
    def test_root_id_from_tplfill_doc_id(self):
        assert _root_id_from_doc_id("tplfill-abc123") == "abc123"

    def test_root_id_passthrough(self):
        assert _root_id_from_doc_id("some-other-obj") == "some-other-obj"
        assert _root_id_from_doc_id("") == ""
        assert _root_id_from_doc_id(None) == ""

    def test_source_types_enum(self):
        assert set(SOURCE_TYPES) == {"chat_fill", "flow_version", "rewrite", "rollback"}


class TestRootIdForDoc:
    """链感知锚解析：rewrite- 产物对象按 DB 反查 root_id，查不到退化剥前缀。"""

    def test_rewrite_prefix_db_hit(self, monkeypatch):
        import api.db.db_models as db_models

        row = SimpleNamespace(root_id="task1")

        class FakeModel:
            obj = _FakeField()

            @classmethod
            def select(cls):
                return _FakeQuery(first_result=row)

        monkeypatch.setattr(db_models, "DocRewriteVersion", FakeModel)
        assert root_id_for_doc("rewrite-abc123") == "task1"

    def test_rewrite_prefix_db_miss_falls_back(self, monkeypatch):
        """对抗性：孤儿 rewrite- 对象（DB 无行）不得抛错，退化按原样当锚。"""
        import api.db.db_models as db_models

        class FakeModel:
            obj = _FakeField()

            @classmethod
            def select(cls):
                return _FakeQuery(first_result=None)

        monkeypatch.setattr(db_models, "DocRewriteVersion", FakeModel)
        assert root_id_for_doc("rewrite-orphan") == "rewrite-orphan"

    def test_non_rewrite_prefix_skips_db(self):
        """非 rewrite- 前缀纯路径，不触碰 DB（无需 patch 即可通过）。"""
        assert root_id_for_doc("tplfill-task1") == "task1"
        assert root_id_for_doc("some-obj") == "some-obj"
        assert root_id_for_doc("") == ""
        assert root_id_for_doc(None) == ""


class TestFlowAddVersionSwitchCurrent:
    """flow add_version 的 switch_current 开关：False 不得更新 current_version_id。"""

    def _make_fakes(self):
        from api.db.services import flow_service

        svc = flow_service.FlowVersionService

        class FakeVersionModel:
            flow_id = _FakeField()
            version_no = _FakeField()

            @classmethod
            def select(cls):
                return _FakeQuery(first_result=None)

        inserted = {}

        class FakeInserted:
            id = "vid"

            def __init__(self, data):
                self.__data__ = data

        def fake_insert(**kw):
            inserted.update(kw)
            return FakeInserted(kw)

        return svc, FakeVersionModel, fake_insert, inserted

    def test_switch_current_false_skips_flow_update(self):
        from api.db.services import flow_service

        svc, FakeVersionModel, fake_insert, inserted = self._make_fakes()

        class _Boom:
            @classmethod
            def update(cls, **kw):
                raise AssertionError("switch_current=False 不得更新 current_version_id")

        class FakeDB:
            @staticmethod
            @contextlib.contextmanager
            def atomic():
                yield

        orig_model = svc.model
        svc.model = FakeVersionModel
        try:
            with patch.object(flow_service, "DB", FakeDB), \
                    patch.object(svc, "insert", side_effect=fake_insert), \
                    patch.object(flow_service, "FlowInstance", _Boom):
                row = svc.add_version.__wrapped__(
                    svc,
                    {"id": "f1", "status": "running"},
                    object_name="obj1", file_name="a.docx", file_type="docx",
                    file_size=10, source="ai_rewrite", created_by="u1",
                    switch_current=False,
                )
        finally:
            svc.model = orig_model
        assert row["version_no"] == 1
        assert inserted["flow_id"] == "f1"
        assert inserted["source"] == "ai_rewrite"

    def test_switch_current_true_updates_flow(self):
        from api.db.services import flow_service

        svc, FakeVersionModel, fake_insert, _ = self._make_fakes()

        updates = {}

        class _Chain:
            @classmethod
            def where(cls, *a):
                return cls

            @classmethod
            def execute(cls):
                return 1

        class FakeFlowInstance:
            id = _FakeField()

            @classmethod
            def update(cls, **kw):
                updates.update(kw)
                return _Chain

        class FakeDB:
            @staticmethod
            @contextlib.contextmanager
            def atomic():
                yield

        orig_model = svc.model
        svc.model = FakeVersionModel
        try:
            with patch.object(flow_service, "DB", FakeDB), \
                    patch.object(svc, "insert", side_effect=fake_insert), \
                    patch.object(flow_service, "FlowInstance", FakeFlowInstance):
                svc.add_version.__wrapped__(
                    svc,
                    {"id": "f1", "status": "running"},
                    object_name="obj1", file_name="a.docx", file_type="docx",
                    file_size=10, source="ai_rewrite", created_by="u1",
                )  # 不传 switch_current → 默认 True，B端既有行为不变
        finally:
            svc.model = orig_model
        assert updates.get("current_version_id") == "vid"

    def test_switch_current_true_propagates_explicit(self):
        """显式 switch_current=True 与默认路径同效（防参数误拼错名静默降级）。"""
        from api.db.services import flow_service

        svc, FakeVersionModel, fake_insert, _ = self._make_fakes()

        called = []

        class _Chain:
            @classmethod
            def where(cls, *a):
                return cls

            @classmethod
            def execute(cls):
                return 1

        class FakeFlowInstance:
            id = _FakeField()

            @classmethod
            def update(cls, **kw):
                called.append(kw)
                return _Chain

        class FakeDB:
            @staticmethod
            @contextlib.contextmanager
            def atomic():
                yield

        orig_model = svc.model
        svc.model = FakeVersionModel
        try:
            with patch.object(flow_service, "DB", FakeDB), \
                    patch.object(svc, "insert", side_effect=fake_insert), \
                    patch.object(flow_service, "FlowInstance", FakeFlowInstance):
                svc.add_version.__wrapped__(
                    svc,
                    {"id": "f1", "status": "running"},
                    object_name="obj1", file_name="a.docx", file_type="docx",
                    file_size=10, source="ai_rewrite", created_by="u1",
                    switch_current=True,
                )
        finally:
            svc.model = orig_model
        assert len(called) == 1


class TestRegisterChatVersion:
    """取号撞唯一索引 → 重试（最多3次）；对象名与行 uuid 1:1。"""

    def _patch_env(self, monkeypatch, model, puts):
        import api.db.db_models as db_models
        import api.db.services.file_service as file_service

        class FakeDB:
            @staticmethod
            @contextlib.contextmanager
            def atomic():
                yield

        monkeypatch.setattr(db_models, "DB", FakeDB)
        monkeypatch.setattr(db_models, "DocRewriteVersion", model)
        monkeypatch.setattr(file_service.FileService, "put_blob",
                            staticmethod(lambda tenant, obj, blob: puts.append((tenant, obj))))

    def test_retry_on_version_no_conflict(self, monkeypatch):
        from rag.svr.document_rewrite import versions as vmod

        inserts = []

        class FakeModel:
            root_id = _FakeField()
            version_no = _FakeField()

            @classmethod
            def select(cls):
                return _FakeQuery(first_result=None)

            @classmethod
            def create(cls, **kw):
                inserts.append(kw)
                if len(inserts) == 1:
                    raise IntegrityError("Duplicate entry (并发撞线)")
                row = type("R", (), {})()
                row.__data__ = kw
                return row

        puts = []
        self._patch_env(monkeypatch, FakeModel, puts)
        out = vmod.register_chat_version(
            "u1", "task9", b"blob", "docx", "报告",
            source_type="rewrite", instruction="改第二节",
        )
        # 第一次 IntegrityError → 重试后成功，version_no 仍取 1（first()=None）；
        # 对象名与行 uuid 1:1（rewrite-{行id}）：每次尝试各写各的对象，
        # 败者重试不会覆盖胜者已提交版本的对象内容（防跨请求 blob 污染）
        assert len(inserts) == 2
        assert out["version_no"] == 1
        assert out["obj"] == f"rewrite-{out['id']}"
        assert [obj for _, obj in puts] == [f"rewrite-{r['id']}" for r in inserts]
        assert len({obj for _, obj in puts}) == 2  # 两次尝试对象名互不相同
        assert out["root_id"] == "task9"
        assert out["source_type"] == "rewrite"
        assert out["file_name"] == "报告.docx"

    def test_exhausted_retry_raises(self, monkeypatch):
        from rag.svr.document_rewrite import versions as vmod

        class AlwaysConflictModel:
            root_id = _FakeField()
            version_no = _FakeField()

            @classmethod
            def select(cls):
                return _FakeQuery(first_result=None)

            @classmethod
            def create(cls, **kw):
                raise IntegrityError("dup forever")

        puts = []
        self._patch_env(monkeypatch, AlwaysConflictModel, puts)
        try:
            vmod.register_chat_version("u1", "r", b"b", "docx", "f", source_type="rewrite")
            raise SystemError("应当抛 RuntimeError 而非静默成功")
        except RuntimeError as e:
            assert "并发冲突" in str(e)
