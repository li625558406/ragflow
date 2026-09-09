"""模板填写任务取消端点单测（POST /agents/tasks/<task_id>/cancel → Redis 取消键）。

覆盖：
  1. task_service.cancel_task(task_id) 写入 Redis 键 `{task_id}-cancel`，返回 True；
  2. Redis 异常时记日志并返回 False（不向上抛）；
  3. agent_api 注册路由 POST /agents/tasks/<task_id>/cancel 且带 @login_required，
     端点委托 cancel_task 并幂等返回成功（不校验 task 是否存在/已结束）。

说明：直接 `from api.apps.restful_apis.agent_api import ...` 会触发 api/apps/__init__.py
的 `settings.init_settings()`，需要本机 Redis/ES。故照 test_agents_webhook_unit.py 的
模式：从源文件 importlib 加载模块，注入最小桩依赖。
"""

import asyncio
import importlib.util
import json
import logging
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

REPO_ROOT = Path(__file__).resolve().parents[1]


def _register(monkeypatch, name, **attrs):
    mod = ModuleType(name)
    mod.__path__ = []
    for k, v in attrs.items():
        setattr(mod, k, v)
    monkeypatch.setitem(sys.modules, name, mod)
    return mod


class FakeRedisConn:
    def __init__(self, exc=None):
        self.set_calls = []
        self._exc = exc

    def set(self, key, value):
        if self._exc is not None:
            raise self._exc
        self.set_calls.append((key, value))
        return True

    def get(self, _key):
        return None

    def delete(self, _key):
        return True


def _load_task_service(monkeypatch, redis_stub):
    """importlib 加载真实 task_service.py，重依赖打桩，REDIS_CONN 换桩。"""
    # common.settings 打桩：真实 common/settings.py 会拉起 minio/azure/es_conn 等重依赖
    common_pkg = _register(monkeypatch, "common")
    common_pkg.__path__ = [str(REPO_ROOT / "common")]
    settings_stub = ModuleType("common.settings")
    settings_stub.DATABASE_TYPE = "mysql"
    settings_stub.docStoreConn = SimpleNamespace(index_exist=lambda *a, **k: False)
    monkeypatch.setitem(sys.modules, "common.settings", settings_stub)
    common_pkg.settings = settings_stub

    api = _register(monkeypatch, "api")
    api_db = _register(monkeypatch, "api.db", FileType=type("FileType", (), {}))
    api.db = api_db

    def _fake_connection_context(*_a, **_k):
        def deco(func):
            return func

        return deco

    db_models = _register(
        monkeypatch,
        "api.db.db_models",
        DB=type("DB", (), {"connection_context": staticmethod(_fake_connection_context)}),
        File2Document=type("File2Document", (), {}),
        File=type("File", (), {}),
        Task=type("Task", (), {}),
        Document=type("Document", (), {}),
        Knowledgebase=type("Knowledgebase", (), {}),
        Tenant=type("Tenant", (), {}),
    )
    api_db.db_models = db_models
    db_utils = _register(monkeypatch, "api.db.db_utils", bulk_insert_into_db=lambda *a, **k: None)
    api_db.db_utils = db_utils
    services = _register(monkeypatch, "api.db.services")
    services.db_models = db_models
    services.db_utils = db_utils
    common_service = _register(
        monkeypatch,
        "api.db.services.common_service",
        CommonService=type("CommonService", (), {}),
    )
    services.common_service = common_service
    document_service = _register(
        monkeypatch,
        "api.db.services.document_service",
        DocumentService=type("DocumentService", (), {}),
    )
    services.document_service = document_service

    deepdoc = _register(monkeypatch, "deepdoc")
    deepdoc_parser = _register(monkeypatch, "deepdoc.parser", PdfParser=type("PdfParser", (), {}))
    deepdoc_parser.excel_parser = _register(
        monkeypatch, "deepdoc.parser.excel_parser", RAGFlowExcelParser=type("RAGFlowExcelParser", (), {})
    )
    deepdoc.parser = deepdoc_parser

    redis_mod = _register(monkeypatch, "rag.utils.redis_conn", REDIS_CONN=redis_stub)
    _register(monkeypatch, "rag.utils", redis_conn=redis_mod)
    _register(monkeypatch, "rag.nlp", search=ModuleType("rag.nlp.search"))

    path = REPO_ROOT / "api" / "db" / "services" / "task_service.py"
    spec = importlib.util.spec_from_file_location("task_service_under_test_cancel", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


class RecordingManager:
    """记录 @manager.route 注册信息，便于断言路由与方法。"""

    def __init__(self):
        self.routes = []

    def route(self, rule, **options):
        def deco(func):
            self.routes.append((rule, options, func))
            return func

        return deco


def _load_agent_api(monkeypatch, cancel_task_stub):
    """importlib 加载真实 agent_api.py（照 test_agents_webhook_unit.py 的桩集）。"""
    common_pkg = _register(monkeypatch, "common")
    common_pkg.__path__ = [str(REPO_ROOT / "common")]
    settings_mod = ModuleType("common.settings")
    settings_mod.DATABASE_TYPE = "mysql"
    settings_mod.docStoreConn = SimpleNamespace(
        index_exist=lambda *_a, **_k: False,
        delete=lambda *_a, **_k: None,
    )
    monkeypatch.setitem(sys.modules, "common.settings", settings_mod)
    common_pkg.settings = settings_mod

    agent_pkg = _register(monkeypatch, "agent")
    canvas_mod = _register(monkeypatch, "agent.canvas", Canvas=type("_StubCanvas", (), {}))
    component_mod = _register(monkeypatch, "agent.component", LLM=type("_StubLLM", (), {}))
    dsl_migration_mod = _register(monkeypatch, "agent.dsl_migration", normalize_chunker_dsl=lambda dsl: dsl)
    agent_pkg.canvas = canvas_mod
    agent_pkg.component = component_mod
    agent_pkg.dsl_migration = dsl_migration_mod

    services = _register(monkeypatch, "api.db.services")
    db_models = _register(
        monkeypatch,
        "api.db.db_models",
        Task=type("_StubTask", (), {"doc_id": "doc_id"}),
        APIToken=type("_StubAPIToken", (), {"query": staticmethod(lambda **_k: [])}),
    )
    services.db_models = db_models

    canvas_service_mod = _register(
        monkeypatch,
        "api.db.services.canvas_service",
        UserCanvasService=type(
            "_StubUserCanvasService",
            (),
            {
                "query": staticmethod(lambda **_k: []),
                "get_by_tenant_ids": staticmethod(lambda *a, **k: ([], 0)),
                "get_by_id": staticmethod(lambda _id: (False, None)),
                "get_by_canvas_id": staticmethod(lambda _id: (False, None)),
                "accessible": staticmethod(lambda *a, **k: True),
                "save": staticmethod(lambda **k: True),
                "update_by_id": staticmethod(lambda *a, **k: True),
                "delete_by_id": staticmethod(lambda _id: True),
            },
        ),
        CanvasTemplateService=type("_StubCanvasTemplateService", (), {}),
        completion=lambda *a, **k: None,
        completion_openai=lambda *a, **k: None,
    )
    services.canvas_service = canvas_service_mod

    api_service_mod = _register(
        monkeypatch,
        "api.db.services.api_service",
        API4ConversationService=type(
            "_StubAPI4ConversationService",
            (),
            {
                "get_names": staticmethod(lambda *a, **k: []),
                "get_list": staticmethod(lambda *a, **k: (0, [])),
                "get_by_id": staticmethod(lambda _id: (False, None)),
                "save": staticmethod(lambda **k: True),
                "delete_by_id": staticmethod(lambda _id: True),
            },
        ),
    )
    services.api_service = api_service_mod

    document_service_mod = _register(
        monkeypatch,
        "api.db.services.document_service",
        DocumentService=type(
            "_StubDocumentService",
            (),
            {
                "clear_chunk_num_when_rerun": staticmethod(lambda *a, **k: True),
                "update_by_id": staticmethod(lambda *a, **k: True),
            },
        ),
    )
    services.document_service = document_service_mod

    file_service_mod = _register(
        monkeypatch,
        "api.db.services.file_service",
        FileService=type("_StubFileService", (), {}),
    )
    services.file_service = file_service_mod

    kb_service_mod = _register(
        monkeypatch,
        "api.db.services.knowledgebase_service",
        KnowledgebaseService=type("_StubKnowledgebaseService", (), {"query": staticmethod(lambda **k: [])}),
    )
    services.knowledgebase_service = kb_service_mod

    pipeline_log_mod = _register(
        monkeypatch,
        "api.db.services.pipeline_operation_log_service",
        PipelineOperationLogService=type(
            "_StubPipelineOperationLogService",
            (),
            {
                "get_documents_info": staticmethod(lambda *a, **k: []),
                "update_by_id": staticmethod(lambda *a, **k: True),
            },
        ),
    )
    services.pipeline_operation_log_service = pipeline_log_mod

    task_service_mod = _register(
        monkeypatch,
        "api.db.services.task_service",
        CANVAS_DEBUG_DOC_ID="debug-doc-id",
        TaskService=type("_StubTaskService", (), {"filter_delete": staticmethod(lambda *a, **k: True)}),
        queue_dataflow=lambda *a, **k: (True, ""),
        cancel_task=cancel_task_stub,
    )
    services.task_service = task_service_mod

    tenant_llm_mod = _register(
        monkeypatch,
        "api.db.services.tenant_llm_service",
        LLMFactoriesService=type("_StubLLMFactoriesService", (), {"get_api_key": staticmethod(lambda *a, **k: None)}),
    )
    services.tenant_llm_service = tenant_llm_mod

    permission_service_mod = _register(
        monkeypatch,
        "api.db.services.permission_service",
        get_user_permission_keys=lambda *a, **k: set(),
    )
    services.permission_service = permission_service_mod

    canvas_version_mod = _register(
        monkeypatch,
        "api.db.services.user_canvas_version",
        UserCanvasVersionService=type(
            "_StubUserCanvasVersionService",
            (),
            {
                "insert": staticmethod(lambda **k: True),
                "save_or_replace_latest": staticmethod(lambda *a, **k: True),
                "build_version_title": staticmethod(lambda *a, **k: "stub_title"),
                "list_by_canvas_id": staticmethod(lambda _id: []),
            },
        ),
    )
    services.user_canvas_version = canvas_version_mod

    user_service_mod = _register(
        monkeypatch,
        "api.db.services.user_service",
        TenantService=type("_StubTenantService", (), {"get_joined_tenants_by_user_id": staticmethod(lambda _t: [])}),
        UserService=type("_StubUserService", (), {}),
    )
    services.user_service = user_service_mod

    # api.apps 打桩（避免 api/apps/__init__.py 触发 settings.init_settings）
    def _login_required_stub(func):
        func.__login_required__ = True
        return func

    api_apps_pkg = _register(
        monkeypatch,
        "api.apps",
        current_user=SimpleNamespace(id="tenant-1"),
        login_required=_login_required_stub,
    )
    api_apps_services = _register(monkeypatch, "api.apps.services")
    api_apps_pkg.services = api_apps_services
    replica_mod = _register(
        monkeypatch,
        "api.apps.services.canvas_replica_service",
        CanvasReplicaService=type(
            "_StubCanvasReplicaService",
            (),
            {
                "normalize_dsl": classmethod(lambda cls, dsl: dsl),
                "bootstrap": classmethod(lambda cls, *a, **k: {}),
                "load_for_run": classmethod(lambda cls, *a, **k: None),
                "commit_after_run": classmethod(lambda cls, *a, **k: True),
                "replace_for_set": classmethod(lambda cls, *a, **k: True),
                "create_if_absent": classmethod(lambda cls, *a, **k: {}),
            },
        ),
    )
    api_apps_services.canvas_replica_service = replica_mod

    redis_stub = FakeRedisConn()
    redis_mod = _register(monkeypatch, "rag.utils.redis_conn", REDIS_CONN=redis_stub)
    rag_pkg = _register(monkeypatch, "rag")
    rag_utils = _register(monkeypatch, "rag.utils", redis_conn=redis_mod)
    rag_pkg.utils = rag_utils
    rag_flow = _register(monkeypatch, "rag.flow")
    rag_pkg.flow = rag_flow
    rag_flow.pipeline = _register(monkeypatch, "rag.flow.pipeline", Pipeline=type("_StubPipeline", (), {}))
    rag_nlp = _register(monkeypatch, "rag.nlp", search=ModuleType("rag.nlp.search"))
    rag_pkg.nlp = rag_nlp

    path = REPO_ROOT / "api" / "apps" / "restful_apis" / "agent_api.py"
    spec = importlib.util.spec_from_file_location("agent_api_under_test_cancel", path)
    module = importlib.util.module_from_spec(spec)
    module.manager = RecordingManager()
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


# ---------- ① cancel_task 写取消键 ----------


def test_cancel_task_sets_redis_key(monkeypatch):
    redis_stub = FakeRedisConn()
    mod = _load_task_service(monkeypatch, redis_stub)
    assert mod.cancel_task("task-abc") is True
    assert redis_stub.set_calls == [("task-abc-cancel", "x")]


def test_cancel_task_redis_error_returns_false(monkeypatch, caplog):
    redis_stub = FakeRedisConn(exc=ConnectionError("redis down"))
    mod = _load_task_service(monkeypatch, redis_stub)
    with caplog.at_level(logging.ERROR):
        result = mod.cancel_task("task-xyz")  # 不应向上抛
    assert result is False
    assert redis_stub.set_calls == []
    assert any("redis down" in rec.message for rec in caplog.records)


# ---------- ③ 端点路由 + login_required + 幂等 ----------


def test_cancel_endpoint_route_registered(monkeypatch):
    captured = {}

    def fake_cancel_task(task_id):
        captured["task_id"] = task_id
        return True

    module = _load_agent_api(monkeypatch, fake_cancel_task)

    matched = [(rule, options, func) for rule, options, func in module.manager.routes if rule == "/agents/tasks/<task_id>/cancel"]
    assert matched, f"route not registered, got: {[r for r, _, _ in module.manager.routes]}"
    _rule, options, func = matched[0]
    assert "POST" in options.get("methods", []), options
    assert getattr(func, "__login_required__", False) is True

    # 幂等：任意 task_id（含不存在/已结束）都委托 cancel_task 并返回成功
    res = asyncio.run(func("whatever-task-id"))
    assert captured["task_id"] == "whatever-task-id"
    # 无 quart app context 时 _safe_jsonify 落回 dict；有 context 时为 Response
    body = res if isinstance(res, dict) else json.loads(res.get_data(as_text=True))
    assert body["code"] == 0, body
    assert body["data"] is True, body


def test_cancel_route_no_conflict_with_existing_rules(monkeypatch):
    """/agents/tasks/<task_id>/cancel 是 4 段静态前缀路由，不应与现有
    /agents/<agent_id>/... （3 段）或 /agents/<agent_id>（1 段）互相吞噬。"""
    module = _load_agent_api(monkeypatch, lambda _tid: True)
    target_segments = "/agents/tasks/<task_id>/cancel".strip("/").split("/")
    for rule, _, _ in module.manager.routes:
        if rule == "/agents/tasks/<task_id>/cancel":
            continue
        segs = rule.strip("/").split("/")
        # 同段数且首段同为 converters 覆盖 "tasks" 的路由才可能冲突
        if len(segs) == len(target_segments) and segs[0] == "<agent_id>" and segs[1] == "tasks":
            pytest_fail = f"conflicting route: {rule}"
            raise AssertionError(pytest_fail)
