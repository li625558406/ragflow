# test/test_template_api_routes.py
"""template_api 冒烟测试：不依赖 Quart 运行时上下文/DB/MinIO。

说明：直接 `import api.apps.restful_apis.template_api` 会触发 api/apps/__init__.py
的 `settings.init_settings()`，进而需要本机 Redis/ES（非单元测试环境）。故照
test_flow_doc_table_edit.py 的模式：从源文件加载模块并注入最小桩（仅 api.apps，
避免 init_settings；api_utils / template_fill_service / common / detector 均可真实
导入——test_template_fill_utils.py 已验证该 import 链不会立即触库）。
"""

import inspect
import io
import os
import sys
import types
import zipfile
from typing import ClassVar

import pytest
from importlib.util import module_from_spec, spec_from_file_location

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


def _make_stub_module(name, **attrs):
    mod = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(mod, k, v)
    sys.modules[name] = mod
    return mod


def _noop_decorator(f=None, *a, **kw):
    """login_required 透传桩：必须原样返回被装饰函数。

    注意不能写成「返回内部 deco」的形式——@login_required 无括号直接作用于
    端点函数，若返回 deco 会丢失原函数（名字变 deco → blueprint 端点名冲突、
    iscoroutinefunction/getsource 全失效）。
    """
    return f


def _load_template_api():
    # current_user 用 SimpleNamespace：端点内部会取 .id（如 _load_template → get_owned(tid, current_user.id)），
    # 桩成 None 会让守卫路径直调时 NoneType.id 崩溃
    user_stub = types.SimpleNamespace(id="u1")
    _make_stub_module("api.apps", current_user=user_stub, login_required=_noop_decorator)
    path = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "api", "apps", "restful_apis", "template_api.py"))
    spec = spec_from_file_location("template_api_under_test", path)
    mod = module_from_spec(spec)
    sys.modules["template_api_under_test"] = mod
    spec.loader.exec_module(mod)
    return mod


_template_api = _load_template_api()

EXPECTED_ENDPOINTS = (
    "upload_template",
    "list_templates",
    "detect_placeholders",
    "detect_placeholders_async",
    "save_placeholders",
    "publish_template",
    "disable_template",
    "get_template",
    "delete_template_endpoint",
    "batch_delete_templates",
    "preview_template",
    "download_template",
    "create_fill_task",
    "list_fill_tasks",
    "get_fill_task",
    "retry_fill_task",
    "download_fill_result",
    "test_fill_template",
)


def test_template_api_module_loads_and_registers_routes():
    mod = _template_api
    for name in EXPECTED_ENDPOINTS:
        fn = getattr(mod, name, None)
        assert fn is not None, f"缺少端点函数 {name}"
        assert inspect.iscoroutinefunction(fn), f"{name} 必须是 async 函数"


def test_all_routes_registered_on_blueprint():
    """10 个端点必须全部挂到 blueprint 上，且 methods 正确（防止漏装饰器/漏路径）。"""
    from quart import Quart

    app = Quart(__name__)
    app.register_blueprint(_template_api.manager)
    rules = {}
    for r in app.url_map.iter_rules():
        if r.rule == "/static/<path:filename>":
            continue
        # 同一路径可挂多条 Rule（如 GET+DELETE 分两个端点函数），必须并集合并而非覆盖
        rules.setdefault(r.rule, set()).update(r.methods)
    expected = {
        "/template/fill/upload": {"POST"},
        "/template/fill/list": {"GET"},
        "/template/fill/detect": {"POST"},
        "/template/fill/detect-async": {"POST"},
        "/template/fill/<template_id>/save-placeholders": {"POST"},
        "/template/fill/<template_id>/publish": {"POST"},
        "/template/fill/<template_id>/disable": {"POST"},
        "/template/fill/<template_id>": {"GET", "DELETE"},
        "/template/fill/batch-delete": {"POST"},
        "/template/fill/<template_id>/preview": {"GET"},
        "/template/fill/<template_id>/test-fill": {"POST"},
        "/template/fill/<template_id>/file": {"GET"},
        "/template/fill/fill-task": {"POST"},
        "/template/fill/fill-task/list": {"GET"},
        "/template/fill/fill-task/<task_id>": {"GET"},
        "/template/fill/fill-task/<task_id>/retry": {"POST"},
        "/template/fill/fill-task/<task_id>/download": {"GET"},
    }
    for rule, methods in expected.items():
        assert rule in rules, f"路由未注册: {rule}（现有: {sorted(rules)}）"
        assert methods <= rules[rule], f"{rule} methods 不符: {rules[rule]}"


def test_file_type_of_boundaries():
    """_file_type_of 纯函数边界：None/空/无扩展名/大写/伪扩展名。"""
    f = _template_api._file_type_of
    assert f(None) is None
    assert f("") is None
    assert f("template") is None
    assert f("模板.docx") == "docx"
    assert f("模板.DOCX") == "docx"  # 大写扩展名按 lower 归一
    assert f("模板.Xlsx") == "xlsx"
    assert f("a.b.docx") == "docx"
    assert f("evil.docx.exe") is None  # 伪扩展名
    assert f("template.docxx") is None  # endswith 前缀误伤
    assert f("template.doc") is None  # 旧版 doc 不支持


def test_is_legacy_doc_boundaries():
    """_is_legacy_doc 纯函数边界：大小写/.docx 不误伤/无扩展名/伪扩展名/None 安全。"""
    f = _template_api._is_legacy_doc
    assert f("a.doc") is True
    assert f("a.DOC") is True  # 大写扩展名按 lower 归一
    assert f("a.docx") is False  # endswith(".doc") 不误伤 .docx
    assert f("noext") is False
    assert f(".doc.exe") is False  # 伪扩展名
    assert f("") is False
    assert f(None) is False  # None 安全（or 空串兜底）


def test_extract_candidates_dispatches_by_file_type():
    """_extract_candidates 按 file_type 分发：docx 走段落提取、xlsx 走单元格提取。"""
    import io

    from docx import Document
    from openpyxl import Workbook

    doc = Document()
    doc.add_paragraph("项目名称：____")
    buf = io.BytesIO()
    doc.save(buf)
    cands = _template_api._extract_candidates("docx", buf.getvalue())
    assert any("项目名称" in c["text"] for c in cands)

    wb = Workbook()
    ws = wb.active
    ws["A1"] = "合计：____元"
    xbuf = io.BytesIO()
    wb.save(xbuf)
    cands = _template_api._extract_candidates("xlsx", xbuf.getvalue())
    assert any("合计" in c["text"] for c in cands)


def test_list_endpoint_accepts_garbage_pagination_via_type_int():
    """D 项回归：args.get(..., type=int) 转换失败回默认值——这里直接验证
    werkzeug MultiDict 语义与端点取参写法一致，防有人改回 int(args.get(...))。"""
    from werkzeug.datastructures import MultiDict

    args = MultiDict([("page", "abc"), ("size", "-3")])
    assert args.get("page", 1, type=int) == 1  # 转换失败回默认，不抛异常
    assert args.get("size", 20, type=int) == -3  # 转换成功原样返回，负值由 service 层钳制
    # 端点源码必须使用 type=int 取参（防回退成 int(args.get(...)) 导致 500）
    import inspect as _inspect

    src = _inspect.getsource(_template_api.list_templates)
    assert "type=int" in src and "int(args.get" not in src


# ---------- 守卫路径：_load_latest_blob / _load_template ----------
# 无 Quart app 上下文时 _safe_jsonify 回退为纯 dict，错误返回即 {"code": RetCode.DATA_ERROR(102), "message": ...}

import asyncio

DATA_ERROR_CODE = 102


def _err_dict(resp):
    """本测试无 Quart app 上下文，_safe_jsonify 回退为纯 dict。"""
    assert resp is not None, "守卫路径必须返回错误响应"
    assert isinstance(resp, dict), f"错误返回应为 dict，实际: {type(resp)}"
    return resp


def test_load_latest_blob_missing_version(monkeypatch):
    """ver 缺失（latest 返回 None）→ 错误 dict，不抛异常、不透传 None blob 给下游。"""
    mod = _template_api
    fake_ver_svc = types.SimpleNamespace(latest=lambda tid: None)
    monkeypatch.setattr(mod, "TplTemplateVersionService", fake_ver_svc)
    blob, err = mod._load_latest_blob("tpl-no-ver")
    assert blob is None
    assert _err_dict(err)["code"] == DATA_ERROR_CODE
    assert "版本缺失" in _err_dict(err)["message"]


def test_load_latest_blob_missing_blob(monkeypatch):
    """存储对象缺失（STORAGE_IMPL.get 返回 None）→ 错误 dict。"""
    mod = _template_api
    fake_ver = types.SimpleNamespace(original_file_id="fid-1", render_file_id=None)
    monkeypatch.setattr(mod, "TplTemplateVersionService",
                        types.SimpleNamespace(latest=lambda tid: fake_ver))
    fake_settings = types.SimpleNamespace(
        STORAGE_IMPL=types.SimpleNamespace(get=lambda tenant, name: None))
    monkeypatch.setattr(mod, "settings", fake_settings)
    blob, err = mod._load_latest_blob("tpl-no-blob")
    assert blob is None
    assert _err_dict(err)["code"] == DATA_ERROR_CODE
    assert "文件缺失" in _err_dict(err)["message"]


def test_load_latest_blob_success_returns_blob(monkeypatch):
    """对照组：版本与存储对象都在 → 原样返回 blob，err 为 None。"""
    mod = _template_api
    fake_ver = types.SimpleNamespace(original_file_id="fid-1", render_file_id="fid-2")
    monkeypatch.setattr(mod, "TplTemplateVersionService",
                        types.SimpleNamespace(latest=lambda tid: fake_ver))
    fake_settings = types.SimpleNamespace(
        STORAGE_IMPL=types.SimpleNamespace(get=lambda tenant, name: b"blob-bytes"))
    monkeypatch.setattr(mod, "settings", fake_settings)
    blob, err = mod._load_latest_blob("tpl-ok")
    assert err is None
    assert blob == b"blob-bytes"


def test_load_template_not_found(monkeypatch):
    """模板不存在 / 非本人模板（get_owned 返回 None）→ 错误 dict。"""
    mod = _template_api
    monkeypatch.setattr(mod, "TplTemplateService",
                        types.SimpleNamespace(get_owned=lambda tid, uid: None))
    tpl, err = asyncio.run(mod._load_template("tpl-gone"))
    assert tpl is None
    assert _err_dict(err)["code"] == DATA_ERROR_CODE
    assert "模板不存在" in _err_dict(err)["message"]


def test_corrupt_template_returns_error_not_500(monkeypatch):
    """I-1 回归：伪 zip 字节在候选提取阶段必须抛异常（由端点 try/except 兜底为错误 dict）。"""
    mod = _template_api

    raised = False
    try:
        mod._extract_candidates("docx", b"this is not a zip file")
    except Exception:  # noqa: BLE001 — 断言目标就是「任意解析异常都被端点兜底」
        raised = True
    assert raised, "伪 zip 必须在提取阶段抛异常（由端点 try/except 兜底）"

    # 端点源码必须存在兜底 try/except（防回退）
    import inspect as _inspect2
    for fn in (mod.detect_placeholders, mod.save_placeholders, mod.preview_template):
        src = _inspect2.getsource(fn)
        assert "模板文件损坏或无法解析" in src, f"{fn.__name__} 缺少损坏模板兜底"
    # upload 必须有 zip 内容校验 + read 前探大小
    up_src = _inspect2.getsource(mod.upload_template)
    assert "is_zipfile" in up_src and "seek(0, 2)" in up_src
    assert "文件已损坏或不是有效的 docx/xlsx 文件" in up_src


# ---------- 需求①：旧版 .doc 上传转 docx ----------

def test_convert_doc_to_docx_success(monkeypatch):
    """monkeypatch subprocess.run 伪造 LibreOffice 转换成功产物（不真跑 soffice）。"""
    mod = _template_api
    calls = {}

    def fake_run(cmd, capture_output, timeout, check=True, env=None):
        calls["cmd"] = list(cmd)
        calls["timeout"] = timeout
        calls["env"] = env
        # soffice 语义：--outdir <dir> 后跟源文件路径，产物为 <dir>/input.docx
        outdir = cmd[cmd.index("--outdir") + 1]
        with open(os.path.join(outdir, "input.docx"), "wb") as f:
            f.write(b"converted-docx-bytes")
        return types.SimpleNamespace(returncode=0, stderr=b"")

    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    out = mod._convert_doc_to_docx(b"legacy-doc-bytes")
    assert out == b"converted-docx-bytes"
    assert calls["timeout"] == 60
    assert "--convert-to" in calls["cmd"] and "docx" in calls["cmd"]
    assert "--headless" in calls["cmd"] and "--norestore" in calls["cmd"]
    # 独立 UserInstallation profile 必须携带，防并发/首启锁冲突
    assert any(str(a).startswith("-env:UserInstallation=") for a in calls["cmd"])
    # 容器 soffice 包装脚本不自设库路径，必须显式注入 LD_LIBRARY_PATH（否则 rc=127）
    assert calls["env"] is not None
    assert "libreoffice/program" in calls["env"].get("LD_LIBRARY_PATH", "")


def test_convert_doc_to_docx_missing_output_raises(monkeypatch):
    """soffice 返回 0 但产物缺失（转换实际失败）→ RuntimeError，由端点兜底。"""
    mod = _template_api
    monkeypatch.setattr(mod.subprocess, "run",
                        lambda *a, **kw: types.SimpleNamespace(returncode=0, stderr=b""))
    raised = False
    try:
        mod._convert_doc_to_docx(b"legacy-doc-bytes")
    except RuntimeError:
        raised = True
    assert raised, "转换产物缺失必须抛 RuntimeError"


class _FakeUploadFile:
    """模拟 werkzeug FileStorage 的 seek/tell/read 协议（upload 端点探大小用）。"""

    def __init__(self, filename, blob=b""):
        self.filename = filename
        self._blob = blob
        self._pos = 0

    def seek(self, pos, whence=0):
        if whence == 0:
            self._pos = pos
        elif whence == 2:
            self._pos = len(self._blob) + pos
        else:
            self._pos += pos
        return self._pos

    def tell(self):
        return self._pos

    def read(self):
        data = self._blob[self._pos:]
        self._pos = len(self._blob)
        return data


class _FakeRequest:
    """quart request 桩：端点内 `await request.files` / `await request.form`。"""

    def __init__(self, file=None, form=None):
        self._file = file
        self._form = form or {}

    @property
    def files(self):
        async def _get():
            return {"file": self._file} if self._file else {}
        return _get()

    @property
    def form(self):
        async def _get():
            return self._form
        return _get()


def _fake_docx_bytes():
    """最小合法 zip 字节（模拟转换产物 docx，能过端点 is_zipfile 校验）。"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("[Content_Types].xml", "<Types/>")
    return buf.getvalue()


def _patch_upload_deps(monkeypatch, mod, request_stub, convert_result=None):
    convert_result = convert_result or _fake_docx_bytes()
    inserted, versions = {}, {}
    monkeypatch.setattr(mod, "request", request_stub)
    monkeypatch.setattr(mod, "TplTemplateService", types.SimpleNamespace(
        insert=lambda **kw: (inserted.update(kw), "tpl-id")[1]))
    monkeypatch.setattr(mod, "TplTemplateVersionService", types.SimpleNamespace(
        create_initial_version=lambda tid, fn, blob: versions.update(
            tid=tid, fn=fn, blob=blob)))
    monkeypatch.setattr(mod, "_convert_doc_to_docx", lambda blob: convert_result)
    return inserted, versions


def test_upload_legacy_doc_converts_and_stores_docx(monkeypatch):
    """.doc 上传走转换路径：入库 file_type='docx'，存的是转换产物 blob。"""
    mod = _template_api
    req = _FakeRequest(file=_FakeUploadFile("legacy.doc", b"legacy-bytes"),
                       form={"name": "测试模板"})
    inserted, versions = _patch_upload_deps(monkeypatch, mod, req)
    resp = asyncio.run(mod.upload_template())
    assert resp["code"] == 0
    assert inserted["file_type"] == "docx"  # 以 docx 形态入库
    assert versions["blob"] == _fake_docx_bytes()  # 存的是转换产物而非原始 .doc
    assert versions["fn"] == "legacy.doc"  # filename 原样传给 service


def test_upload_legacy_doc_uppercase_ext(monkeypatch):
    """大写 .DOC 同样走转换路径。"""
    mod = _template_api
    req = _FakeRequest(file=_FakeUploadFile("模板.DOC", b"legacy-bytes"), form={})
    inserted, _versions = _patch_upload_deps(monkeypatch, mod, req)
    resp = asyncio.run(mod.upload_template())
    assert resp["code"] == 0
    assert inserted["file_type"] == "docx"


def test_upload_legacy_doc_convert_failure_returns_friendly_error(monkeypatch):
    """转换抛异常 → 端点兜底为友好错误 dict，不 500、不写库。"""
    mod = _template_api
    req = _FakeRequest(file=_FakeUploadFile("legacy.doc", b"legacy-bytes"),
                       form={"name": "测试模板"})
    inserted, versions = _patch_upload_deps(monkeypatch, mod, req)

    def boom(blob):
        raise RuntimeError("doc convert failed")

    monkeypatch.setattr(mod, "_convert_doc_to_docx", boom)
    resp = asyncio.run(mod.upload_template())
    d = _err_dict(resp)
    assert d["code"] == DATA_ERROR_CODE
    assert "另存为 .docx" in d["message"]
    assert not inserted, "转换失败不得写库"
    assert not versions, "转换失败不得建版本"


def test_upload_unsupported_ext_still_rejected(monkeypatch):
    """回归：.txt 等不支持后缀仍被拒绝，且不触发转换。"""
    mod = _template_api
    req = _FakeRequest(file=_FakeUploadFile("notes.txt", b"text"), form={})
    inserted, versions = _patch_upload_deps(monkeypatch, mod, req)
    resp = asyncio.run(mod.upload_template())
    d = _err_dict(resp)
    assert d["code"] == DATA_ERROR_CODE
    assert "仅支持 .docx / .doc / .xlsx" in d["message"]
    assert not inserted and not versions


# ---------- P2 遗留债：published 自动升 v2 + 状态机白名单 ----------

def test_set_status_whitelist():
    """非法状态值直接拒绝，不落库。"""
    from api.db.services.template_fill_service import TplTemplateService
    ok = TplTemplateService.set_status("tpl_x", "tenant_x", "hacked")
    assert ok is False


def test_save_placeholders_published_upgrades_version(monkeypatch):
    """published 模板保存填写点 → 新建 v{N+1}，不改旧版本行。"""
    from api.db.services import template_fill_service as svc
    calls = {}
    monkeypatch.setattr(svc.TplTemplateVersionService, "latest", classmethod(
        lambda cls, tid: types.SimpleNamespace(version=3, original_file_id="v3_original")))
    monkeypatch.setattr(svc.TplTemplateService, "get_by_id", classmethod(
        lambda cls, tid: types.SimpleNamespace(id="tpl_x", status="published",
                                               file_type="docx", to_dict=lambda: {"id": "tpl_x"})))
    monkeypatch.setattr(svc, "_storage_get", lambda bucket, name: b"original-blob")
    monkeypatch.setattr(svc, "_storage_put", lambda bucket, name, blob: None)
    monkeypatch.setattr(svc.TplTemplateVersionService, "replace_anchor_to_placeholder",
                        classmethod(lambda cls, *a, **kw: b"rendered"))
    monkeypatch.setattr(svc.TplTemplateVersionService, "insert", classmethod(
        lambda cls, **kw: calls.update(version=kw.get("version"))))
    monkeypatch.setattr(svc.TplTemplateService, "set_latest_version", classmethod(
        lambda cls, *a, **kw: calls.update(bumped=True)))
    ok, msg = svc.TplTemplateVersionService.save_placeholders(
        {"id": "tpl_x", "status": "published", "file_type": "docx"},
        [{"key": "a", "addr": "x", "anchor": "a"}])
    assert ok, msg
    assert calls["version"] == 4 and calls["bumped"], "published 必须升版而非改写 v3"


# ---------- P2 遗留债④：模板删除 ----------

def test_delete_template_refuses_when_tasks_exist(monkeypatch):
    """有填写任务记录的模板拒删（历史任务下载依赖其 bucket=template_id 的对象）。"""
    from api.db.services.template_fill_service import TplTemplateService
    monkeypatch.setattr(TplTemplateService, "get_owned", classmethod(
        lambda cls, tid, uid, **kw: types.SimpleNamespace(id="tpl_x", status="disabled")))
    monkeypatch.setattr(TplTemplateService, "has_tasks", classmethod(lambda cls, tid: True))
    ok, msg = TplTemplateService.delete_template("tpl_x", "tenant_x")
    assert not ok and "填写任务" in msg


def test_delete_template_refuses_published(monkeypatch):
    """published 必须先停用才能删（防误删线上可用模板）。"""
    from api.db.services.template_fill_service import TplTemplateService
    monkeypatch.setattr(TplTemplateService, "get_owned", classmethod(
        lambda cls, tid, uid, **kw: types.SimpleNamespace(id="tpl_x", status="published")))
    ok, msg = TplTemplateService.delete_template("tpl_x", "tenant_x")
    assert not ok and "停用" in msg


class _FakeDeleteQuery:
    """peewee delete().where().execute() 桩，记录 where 条件。"""

    def __init__(self, recorder, tag):
        self._recorder = recorder
        self._tag = tag

    def where(self, *exprs):
        self._recorder[self._tag] = list(exprs)
        return self

    def execute(self):
        self._recorder[self._tag + "_executed"] = True
        return 1


class _FakeVersionModel:
    """TplTemplateVersion 桩：select().where() 可迭代出给定版本行，delete() 可记录。"""

    # service 里 where(TplTemplateVersion.template_id == ...) 的哨兵表达式对象
    template_id = object()

    def __init__(self, rows):
        self._rows = rows
        self.recorder = {}

    def select(self):
        return self

    def where(self, *exprs):
        self.recorder["select_where"] = list(exprs)
        return self

    def __iter__(self):
        return iter(self._rows)

    def delete(self):
        return _FakeDeleteQuery(self.recorder, "version_delete")


class _FakeMainModel:
    # service 里 where(cls.model.id == ..., cls.model.tenant_id == ...) 的哨兵表达式对象
    id = object()
    tenant_id = object()

    def __init__(self, row=None):
        self.recorder = {}
        self._row = row

    def select(self):
        """事务内裸查询链：select().where(...).for_update().first()。
        first() 返回构造时给定的行（None 表示模板已被并发删除）。"""
        return self

    def where(self, *exprs):
        return self

    def for_update(self):
        return self

    def first(self):
        return self._row

    def delete(self):
        return _FakeDeleteQuery(self.recorder, "main_delete")


def test_delete_template_success_cleans_versions_and_storage(monkeypatch):
    """happy path：逐版本删 MinIO 对象（rm）→ 删版本行 → 删主表行。"""
    from api.db.services import template_fill_service as svc
    monkeypatch.setattr(svc.TplTemplateService, "get_owned", classmethod(
        lambda cls, tid, uid, **kw: types.SimpleNamespace(id="tpl_x", status="draft")))
    monkeypatch.setattr(svc.TplTemplateService, "has_tasks", classmethod(lambda cls, tid: False))
    vers = _FakeVersionModel([
        types.SimpleNamespace(original_file_id="v1_original_a.docx", render_file_id="v1_render.docx"),
        types.SimpleNamespace(original_file_id="v2_original.docx", render_file_id=None),
    ])
    monkeypatch.setattr(svc, "TplTemplateVersion", vers)
    # 事务内裸查询 for_update().first() 返回行（行锁复查通过）
    main = _FakeMainModel(row=types.SimpleNamespace(id="tpl_x", status="draft"))
    monkeypatch.setattr(svc.TplTemplateService, "model", main)
    removed = []
    monkeypatch.setattr(svc.settings, "STORAGE_IMPL", types.SimpleNamespace(
        rm=lambda bucket, fnm: removed.append((bucket, fnm))))
    ok, msg = svc.TplTemplateService.delete_template("tpl_x", "tenant_x")
    assert ok, msg
    assert sorted(removed) == [("tpl_x", "v1_original_a.docx"), ("tpl_x", "v1_render.docx"),
                               ("tpl_x", "v2_original.docx")], "render_file_id 为空的版本不得传空对象名"
    assert vers.recorder.get("version_delete_executed"), "版本行必须删除"
    assert main.recorder.get("main_delete_executed"), "主表行必须删除"


def test_delete_template_storage_rm_failure_does_not_block(monkeypatch):
    """对抗性：MinIO rm 抛异常只告警，版本行/主表行仍删除（DB 行清理不被存储故障卡死）。"""
    from api.db.services import template_fill_service as svc
    monkeypatch.setattr(svc.TplTemplateService, "get_owned", classmethod(
        lambda cls, tid, uid, **kw: types.SimpleNamespace(id="tpl_x", status="disabled")))
    monkeypatch.setattr(svc.TplTemplateService, "has_tasks", classmethod(lambda cls, tid: False))
    vers = _FakeVersionModel([
        types.SimpleNamespace(original_file_id="v1_original_a.docx", render_file_id=None),
    ])
    monkeypatch.setattr(svc, "TplTemplateVersion", vers)
    main = _FakeMainModel(row=types.SimpleNamespace(id="tpl_x", status="disabled"))
    monkeypatch.setattr(svc.TplTemplateService, "model", main)

    def boom(bucket, fnm):
        raise RuntimeError("minio down")

    monkeypatch.setattr(svc.settings, "STORAGE_IMPL", types.SimpleNamespace(rm=boom))
    ok, msg = svc.TplTemplateService.delete_template("tpl_x", "tenant_x")
    assert ok, msg
    assert vers.recorder.get("version_delete_executed") and main.recorder.get("main_delete_executed")


def test_delete_template_missing_returns_error_without_side_effects(monkeypatch):
    """对抗性：get_owned 返回 None（不存在/越权）→ 拒删，且不触发任何 rm/DELETE。"""
    from api.db.services import template_fill_service as svc
    monkeypatch.setattr(svc.TplTemplateService, "get_owned", classmethod(
        lambda cls, tid, uid, **kw: None))
    vers = _FakeVersionModel([])
    monkeypatch.setattr(svc, "TplTemplateVersion", vers)
    main = _FakeMainModel()
    monkeypatch.setattr(svc.TplTemplateService, "model", main)
    removed = []
    monkeypatch.setattr(svc.settings, "STORAGE_IMPL", types.SimpleNamespace(
        rm=lambda bucket, fnm: removed.append((bucket, fnm))))
    ok, msg = svc.TplTemplateService.delete_template("tpl_x", "tenant_x")
    assert not ok and msg == "模板不存在"
    assert not removed, "模板不存在时不得触碰 MinIO 对象"
    assert not vers.recorder.get("version_delete_executed"), "模板不存在时不得删版本行"
    assert not main.recorder.get("main_delete_executed"), "模板不存在时不得删主表行"


def test_delete_template_atomic_block_uses_bare_queries():
    """回归（线上 500 根因）：DB.atomic() 事务块内禁止调用带 @DB.connection_context
    装饰器的方法（has_tasks/get_owned）——装饰器退出时无条件 db.close()，事务开着时
    close 抛 OperationalError('Attempting to close database while transaction is open.')。
    单测桩/SQLite 不触发，只有真实 MySQL 暴露，故用源码断言防回退。"""
    from api.db.services import template_fill_service as svc
    src = inspect.getsource(svc.TplTemplateService.delete_template)
    head, sep, atomic_block = src.partition("with DB.atomic():")
    assert sep, "delete_template 必须包含 with DB.atomic(): 事务块"
    assert "cls.has_tasks(" not in atomic_block, \
        "事务内不得调用装饰器版 has_tasks（connection_context 退出 close 与 atomic 冲突）"
    assert "cls.get_owned(" not in atomic_block, \
        "事务内不得调用装饰器版 get_owned（connection_context 退出 close 与 atomic 冲突）"


# ---------- P2+P3 Task 8：填写任务 REST 端点 ----------

import contextlib

from quart import Response


class _FakeJsonRequest:
    """quart request 桩：端点内 `await request.get_json()`。"""

    def __init__(self, body=None):
        self._body = body

    async def get_json(self, *a, **kw):
        # 兼容 quart 真实签名（如 get_json(silent=True)）
        return self._body


class _FakeUpdateQuery:
    """peewee update().where().execute() 桩，记录 update 字段与 where 条件。"""

    def __init__(self, recorder, ret):
        self._recorder = recorder
        self._ret = ret

    def where(self, *exprs):
        self._recorder["where"] = list(exprs)
        return self

    def execute(self):
        self._recorder["executed"] = True
        return self._ret


class _FakeColumn:
    """peewee 列桩：in_(...) 返回哨兵表达式对象。"""

    def __init__(self, recorder, name):
        self._recorder = recorder
        self._name = name

    def in_(self, values):
        self._recorder[f"{self._name}_in"] = tuple(values)
        return object()


class _FakeFillModel:
    """TplFillTask 模型桩：update() 记录字段，execute 返回指定行数。"""

    def __init__(self, ret=1):
        self.recorder = {}
        self._ret = ret
        self.id = object()
        self.status = _FakeColumn(self.recorder, "status")

    def update(self, **kw):
        self.recorder["update"] = kw
        return _FakeUpdateQuery(self.recorder, self._ret)


def _make_fill_tpl(status="published", file_type="docx"):
    return types.SimpleNamespace(id="tpl_x", status=status, file_type=file_type)


_UNSET = object()


def _make_ver(version_id="ver-1", render_file_id="v1_render.docx", placeholders=_UNSET):
    return types.SimpleNamespace(id=version_id, render_file_id=render_file_id,
                                 placeholders=[{"key": "a", "addr": "x"}]
                                 if placeholders is _UNSET else placeholders)


def _patch_fill_deps(monkeypatch, mod, body, tpl=None, ver="default"):
    """create_fill_task 公共桩：json body + 模板/版本/任务 service + spawn 捕获。
    ver 传 None 表示 latest 返回 None；传 "default" 用标准已配置版本。"""
    tpl = tpl if tpl is not None else _make_fill_tpl()
    if ver == "default":
        ver = _make_ver()
    inserted, spawned = {}, []
    monkeypatch.setattr(mod, "request", _FakeJsonRequest(body))
    monkeypatch.setattr(mod, "TplTemplateService", types.SimpleNamespace(
        get_owned=lambda tid, uid, **kw: tpl))
    monkeypatch.setattr(mod, "TplTemplateVersionService", types.SimpleNamespace(
        latest=lambda tid: ver))
    monkeypatch.setattr(mod, "TplFillTaskService", types.SimpleNamespace(
        insert=lambda **kw: (inserted.update(kw), types.SimpleNamespace(**kw))[1]))
    monkeypatch.setattr(mod, "_spawn_fill_task", lambda tid: spawned.append(tid))
    return inserted, spawned


def test_create_fill_task_requires_template_id(monkeypatch):
    mod = _template_api
    inserted, spawned = _patch_fill_deps(monkeypatch, mod, {"kb_ids": ["kb1"]})
    resp = asyncio.run(mod.create_fill_task())
    d = _err_dict(resp)
    assert d["code"] == DATA_ERROR_CODE
    assert "template_id" in d["message"]
    assert not inserted and not spawned, "缺 template_id 不得写库/起线程"


def test_create_fill_task_rejects_draft_template(monkeypatch):
    """draft（未发布）模板拒绝发起填写。"""
    mod = _template_api
    inserted, spawned = _patch_fill_deps(
        monkeypatch, mod, {"template_id": "tpl_x", "kb_ids": ["kb1"]},
        tpl=_make_fill_tpl(status="draft"))
    resp = asyncio.run(mod.create_fill_task())
    d = _err_dict(resp)
    assert d["code"] == DATA_ERROR_CODE
    assert "已发布" in d["message"]
    assert not inserted and not spawned


def test_create_fill_task_rejects_empty_or_missing_kb_ids(monkeypatch):
    """kb_ids 缺失 / 空数组 / 全是无效项 → 拒绝。"""
    mod = _template_api
    for body in ({"template_id": "tpl_x"},
                 {"template_id": "tpl_x", "kb_ids": []},
                 {"template_id": "tpl_x", "kb_ids": ["", None, 123, "   "]}):
        inserted, spawned = _patch_fill_deps(monkeypatch, mod, body)
        resp = asyncio.run(mod.create_fill_task())
        d = _err_dict(resp)
        assert d["code"] == DATA_ERROR_CODE, f"body={body} 应拒绝"
        assert "kb_ids" in d["message"]
        assert not inserted and not spawned, f"body={body} 不得写库/起线程"


def test_create_fill_task_rejects_non_dict_params(monkeypatch):
    """对抗性：params 传数组/字符串 → 拒绝，不得透传进 JSONField。"""
    mod = _template_api
    for bad in (["a"], "str", 42):
        inserted, spawned = _patch_fill_deps(
            monkeypatch, mod, {"template_id": "tpl_x", "kb_ids": ["kb1"], "params": bad})
        resp = asyncio.run(mod.create_fill_task())
        d = _err_dict(resp)
        assert d["code"] == DATA_ERROR_CODE, f"params={bad!r} 应拒绝"
        assert not inserted and not spawned


def test_create_fill_task_rejects_when_placeholders_missing(monkeypatch):
    """版本缺失 / 无 render 副本 / 填写点为空 三种情况都拒绝。"""
    mod = _template_api
    cases = [
        (_make_ver(render_file_id="", placeholders=[{"key": "a"}]), "无 render"),
        (_make_ver(placeholders=[]), "填写点为空"),
        (_make_ver(placeholders=None), "填写点为 None"),
        (None, "版本缺失"),
    ]
    for ver, tag in cases:
        inserted, spawned = _patch_fill_deps(
            monkeypatch, mod, {"template_id": "tpl_x", "kb_ids": ["kb1"]}, ver=ver)
        resp = asyncio.run(mod.create_fill_task())
        d = _err_dict(resp)
        assert d["code"] == DATA_ERROR_CODE, f"case={tag} 应拒绝"
        assert "填写点" in d["message"], f"case={tag} 文案应提示填写点"
        assert not inserted and not spawned, f"case={tag} 不得写库/起线程"


def test_create_fill_task_success_pins_version_and_spawns(monkeypatch):
    """成功路径：insert 必须带 template_version_id=ver.id（pipeline 钉版本），
    status=pending、tenant_id=current_user.id，且 _spawn_fill_task 被调用。"""
    mod = _template_api
    inserted, spawned = _patch_fill_deps(
        monkeypatch, mod,
        {"template_id": "tpl_x", "kb_ids": ["kb1", "kb2"], "params": {"tone": "正式"},
         "source": "web"})
    resp = asyncio.run(mod.create_fill_task())
    assert resp["code"] == 0
    assert inserted["template_version_id"] == "ver-1", "必须写入模板版本 id 钉住版本"
    assert inserted["template_id"] == "tpl_x"
    assert inserted["kb_ids"] == ["kb1", "kb2"]
    assert inserted["params"] == {"tone": "正式"}
    assert inserted["status"] == "pending"
    assert inserted["tenant_id"] == "u1"
    assert spawned == [inserted["id"]], "spawn 必须收到任务 id"
    assert resp["data"]["task_id"] == inserted["id"]
    assert resp["data"]["status"] == "pending"


def test_create_fill_task_filters_kb_ids_and_truncates_source(monkeypatch):
    """kb_ids 混入非字符串/空白项被过滤后仍可成功；source 超 16 字截断。"""
    mod = _template_api
    inserted, _spawned = _patch_fill_deps(
        monkeypatch, mod,
        {"template_id": "tpl_x", "kb_ids": ["kb1", "", None, "  ", 7],
         "source": "x" * 30})
    resp = asyncio.run(mod.create_fill_task())
    assert resp["code"] == 0
    assert inserted["kb_ids"] == ["kb1"]
    assert inserted["source"] == "x" * 16, "source 必须截断到 16 字"


def test_create_fill_task_source_defaults_to_web(monkeypatch):
    mod = _template_api
    inserted, _spawned = _patch_fill_deps(
        monkeypatch, mod, {"template_id": "tpl_x", "kb_ids": ["kb1"]})
    resp = asyncio.run(mod.create_fill_task())
    assert resp["code"] == 0
    assert inserted["source"] == "web"


def _make_task(status="failed", result_file_id="", template_id="tpl_x"):
    return types.SimpleNamespace(id="task-1", status=status,
                                 result_file_id=result_file_id,
                                 template_id=template_id,
                                 to_dict=lambda: {"id": "task-1", "status": status})


def test_get_fill_task_not_found(monkeypatch):
    mod = _template_api
    monkeypatch.setattr(mod, "TplFillTaskService", types.SimpleNamespace(
        get_owned=lambda tid, uid: None))
    resp = asyncio.run(mod.get_fill_task("task-gone"))
    d = _err_dict(resp)
    assert d["code"] == DATA_ERROR_CODE and "任务不存在" in d["message"]


def test_get_fill_task_success(monkeypatch):
    mod = _template_api
    monkeypatch.setattr(mod, "TplFillTaskService", types.SimpleNamespace(
        get_owned=lambda tid, uid: _make_task()))
    resp = asyncio.run(mod.get_fill_task("task-1"))
    assert resp["code"] == 0
    assert resp["data"]["id"] == "task-1"


def test_list_fill_tasks_uses_service_pagination(monkeypatch):
    mod = _template_api
    from werkzeug.datastructures import MultiDict
    calls = {}

    def fake_page(tenant_id, status="", page=1, size=20):
        calls.update(tenant_id=tenant_id, status=status, page=page, size=size)
        return [{"id": "t1"}], 7

    monkeypatch.setattr(mod, "request", types.SimpleNamespace(args=MultiDict([
        ("status", "failed"), ("page", "2"), ("size", "5")])))
    monkeypatch.setattr(mod, "TplFillTaskService", types.SimpleNamespace(
        get_list_page=fake_page))
    resp = asyncio.run(mod.list_fill_tasks())
    assert resp["code"] == 0
    assert resp["data"] == [{"id": "t1"}] and resp["total_datasets"] == 7
    assert calls == {"tenant_id": "u1", "status": "failed", "page": 2, "size": 5}


def _patch_retry_deps(monkeypatch, mod, task, update_ret=1):
    """retry 公共桩：任务 service + 裸 DB 桩（记录 connection_context 是否被进入）。"""
    model = _FakeFillModel(ret=update_ret)
    entered = []

    @contextlib.contextmanager
    def fake_ctx():
        entered.append(True)
        yield

    spawned = []
    monkeypatch.setattr(mod, "TplFillTaskService", types.SimpleNamespace(
        get_owned=lambda tid, uid: task, model=model))
    monkeypatch.setattr(mod, "DB", types.SimpleNamespace(connection_context=fake_ctx))
    monkeypatch.setattr(mod, "_spawn_fill_task", lambda tid: spawned.append(tid))
    return model, spawned, entered


def test_retry_rejects_non_terminal_running_status(monkeypatch):
    """仅 failed/partial 可重试：pending/done 等一律拒绝。"""
    mod = _template_api
    for status in ("pending", "retrieving", "generating", "rendering", "done"):
        model, spawned, _entered = _patch_retry_deps(
            monkeypatch, mod, _make_task(status=status))
        resp = asyncio.run(mod.retry_fill_task("task-1"))
        d = _err_dict(resp)
        assert d["code"] == DATA_ERROR_CODE, f"status={status} 应拒绝"
        assert "失败或部分完成" in d["message"]
        assert not model.recorder.get("executed"), f"status={status} 不得写库"
        assert not spawned


def test_retry_rejects_when_task_already_running(monkeypatch):
    """防重入：task_id 在运行集合内 → 拒绝，不重复复位/起线程。"""
    mod = _template_api
    model, spawned, _entered = _patch_retry_deps(
        monkeypatch, mod, _make_task(status="failed"))
    monkeypatch.setattr(mod, "_running_tasks", {"task-1"})
    resp = asyncio.run(mod.retry_fill_task("task-1"))
    d = _err_dict(resp)
    assert d["code"] == DATA_ERROR_CODE and "执行中" in d["message"]
    assert not model.recorder.get("executed") and not spawned


def test_retry_rejects_when_cas_update_loses_race(monkeypatch):
    """对抗性：复位 update 命中 0 行（并发被改走）→ 报错且不起线程。"""
    mod = _template_api
    model, spawned, entered = _patch_retry_deps(
        monkeypatch, mod, _make_task(status="failed"), update_ret=0)
    monkeypatch.setattr(mod, "_running_tasks", set())
    resp = asyncio.run(mod.retry_fill_task("task-1"))
    d = _err_dict(resp)
    assert d["code"] == DATA_ERROR_CODE and "状态变更失败" in d["message"]
    assert entered, "裸 update 必须包 DB.connection_context()"
    assert not spawned
    assert model.recorder["update"] == {"status": "pending", "error": ""}
    assert model.recorder["status_in"] == ("failed", "partial"), \
        "where 必须带 status 白名单 CAS 条件"
    assert len(model.recorder["where"]) == 2, "where 必须带 id + status 两个条件"


def test_retry_success_resets_and_spawns(monkeypatch):
    mod = _template_api
    model, spawned, entered = _patch_retry_deps(
        monkeypatch, mod, _make_task(status="partial"))
    monkeypatch.setattr(mod, "_running_tasks", set())
    resp = asyncio.run(mod.retry_fill_task("task-1"))
    assert resp["code"] == 0
    assert resp["data"] == {"task_id": "task-1", "status": "pending"}
    assert model.recorder["update"] == {"status": "pending", "error": ""}
    assert entered and spawned == ["task-1"]


def test_download_fill_result_not_found(monkeypatch):
    mod = _template_api
    monkeypatch.setattr(mod, "TplFillTaskService", types.SimpleNamespace(
        get_owned=lambda tid, uid: None))
    resp = asyncio.run(mod.download_fill_result("task-gone"))
    d = _err_dict(resp)
    assert d["code"] == DATA_ERROR_CODE and "任务不存在" in d["message"]


def test_download_fill_result_rejects_without_file(monkeypatch):
    """无 result_file_id / 存储对象缺失 → 各自的友好错误，不 500。"""
    mod = _template_api
    monkeypatch.setattr(mod, "TplFillTaskService", types.SimpleNamespace(
        get_owned=lambda tid, uid: _make_task(status="done", result_file_id="")))
    resp = asyncio.run(mod.download_fill_result("task-1"))
    d = _err_dict(resp)
    assert "尚未产出" in d["message"]

    fetched = []
    monkeypatch.setattr(mod, "TplFillTaskService", types.SimpleNamespace(
        get_owned=lambda tid, uid: _make_task(status="done", result_file_id="f.docx")))
    monkeypatch.setattr(mod, "settings", types.SimpleNamespace(
        STORAGE_IMPL=types.SimpleNamespace(
            get=lambda bucket, name: fetched.append((bucket, name)) or None)))
    resp = asyncio.run(mod.download_fill_result("task-1"))
    d = _err_dict(resp)
    assert "生成稿文件缺失" in d["message"]
    assert fetched == [("tpl_x", "f.docx")], "bucket 必须是 task.template_id"


def test_download_fill_success_returns_blob_attachment(monkeypatch):
    mod = _template_api
    monkeypatch.setattr(mod, "TplFillTaskService", types.SimpleNamespace(
        get_owned=lambda tid, uid: _make_task(status="done", result_file_id="f.docx")))
    monkeypatch.setattr(mod, "TplTemplateService", types.SimpleNamespace(
        get_by_id=lambda tid: (True, _make_fill_tpl(file_type="docx"))))
    monkeypatch.setattr(mod, "settings", types.SimpleNamespace(
        STORAGE_IMPL=types.SimpleNamespace(get=lambda bucket, name: b"result-bytes")))
    resp = asyncio.run(mod.download_fill_result("task-1"))
    assert isinstance(resp, Response)
    assert asyncio.run(resp.get_data()) == b"result-bytes"  # quart Response.get_data 是协程
    assert "attachment" in resp.headers["Content-Disposition"]
    assert resp.mimetype == mod.DOCX_MIME
    assert resp.headers["Content-Disposition"].endswith(".docx")


def test_download_fill_xlsx_tpl_uses_xlsx_mime(monkeypatch):
    """xlsx 模板的生成稿 → XLSX_MIME + .xlsx 文件名（get_by_id 元组契约 True 分支）。"""
    mod = _template_api
    monkeypatch.setattr(mod, "TplFillTaskService", types.SimpleNamespace(
        get_owned=lambda tid, uid: _make_task(status="done", result_file_id="f.xlsx")))
    monkeypatch.setattr(mod, "TplTemplateService", types.SimpleNamespace(
        get_by_id=lambda tid: (True, _make_fill_tpl(file_type="xlsx"))))
    monkeypatch.setattr(mod, "settings", types.SimpleNamespace(
        STORAGE_IMPL=types.SimpleNamespace(get=lambda bucket, name: b"xlsx-bytes")))
    resp = asyncio.run(mod.download_fill_result("task-1"))
    assert isinstance(resp, Response)
    assert resp.mimetype == mod.XLSX_MIME
    assert resp.headers["Content-Disposition"].endswith(".xlsx")


def test_download_fill_tpl_missing_falls_back_docx(monkeypatch):
    """对抗性：模板行已被删（get_by_id → (False, None)）→ ext 兜底 docx，仍可下载。"""
    mod = _template_api
    monkeypatch.setattr(mod, "TplFillTaskService", types.SimpleNamespace(
        get_owned=lambda tid, uid: _make_task(status="done", result_file_id="f.bin")))
    monkeypatch.setattr(mod, "TplTemplateService", types.SimpleNamespace(
        get_by_id=lambda tid: (False, None)))
    monkeypatch.setattr(mod, "settings", types.SimpleNamespace(
        STORAGE_IMPL=types.SimpleNamespace(get=lambda bucket, name: b"raw-bytes")))
    resp = asyncio.run(mod.download_fill_result("task-1"))
    assert isinstance(resp, Response)
    assert asyncio.run(resp.get_data()) == b"raw-bytes"
    assert resp.headers["Content-Disposition"].endswith(".docx")


# ---------- spawn 自愈 + source 类型校验 ----------

def test_spawn_thread_start_failure_self_heals(monkeypatch):
    """对抗性：Thread.start 抛异常（线程数耗尽）→ 接口不 500、不落库中断，
    _running_tasks 不残留（否则 retry 恒报「任务正在执行中」），
    任务行 CAS 置 failed（where 带 id + status=pending）供后续重试。"""
    mod = _template_api
    inserted = {}
    model = _FakeFillModel(ret=1)
    monkeypatch.setattr(mod, "request", _FakeJsonRequest(
        {"template_id": "tpl_x", "kb_ids": ["kb1"]}))
    monkeypatch.setattr(mod, "TplTemplateService", types.SimpleNamespace(
        get_owned=lambda tid, uid, **kw: _make_fill_tpl()))
    monkeypatch.setattr(mod, "TplTemplateVersionService", types.SimpleNamespace(
        latest=lambda tid: _make_ver()))
    monkeypatch.setattr(mod, "TplFillTaskService", types.SimpleNamespace(
        insert=lambda **kw: (inserted.update(kw), types.SimpleNamespace(**kw))[1],
        model=model))
    entered = []

    @contextlib.contextmanager
    def fake_ctx():
        entered.append(True)
        yield

    monkeypatch.setattr(mod, "DB", types.SimpleNamespace(connection_context=fake_ctx))
    monkeypatch.setattr(mod, "_running_tasks", set())

    class _BoomThread:
        def __init__(self, target=None, daemon=None, name=None):
            pass

        def start(self):
            raise RuntimeError("can't start new thread")

    monkeypatch.setattr(mod.threading, "Thread", _BoomThread)
    resp = asyncio.run(mod.create_fill_task())
    assert resp["code"] == 0, "线程启动失败不得 500（任务行已落库，接口应正常返回）"
    assert inserted["status"] == "pending"
    assert mod._running_tasks == set(), "spawn 失败后 _running_tasks 不得残留"
    assert entered, "失败复位必须包 DB.connection_context()"
    assert model.recorder["update"]["status"] == "failed"
    assert "任务调度失败" in model.recorder["update"]["error"]
    assert len(model.recorder["where"]) == 2, "where 必须带 id + status=pending 两个 CAS 条件"


def test_create_fill_task_rejects_non_string_source(monkeypatch):
    """对抗性：source 传非字符串（int/float/list/dict）→ 友好错误，不 500、不写库。"""
    mod = _template_api
    for bad in (42, 3.14, ["web"], {"s": 1}):
        inserted, spawned = _patch_fill_deps(
            monkeypatch, mod,
            {"template_id": "tpl_x", "kb_ids": ["kb1"], "source": bad})
        resp = asyncio.run(mod.create_fill_task())
        d = _err_dict(resp)
        assert d["code"] == DATA_ERROR_CODE, f"source={bad!r} 应拒绝"
        assert "source" in d["message"]
        assert not inserted and not spawned, f"source={bad!r} 不得写库/起线程"


# ---------- P2 Task 12：测试填写端点（试跑出值+证据，不落任务） ----------

def _patch_test_fill_deps(monkeypatch, mod, body, tpl=None,
                          dry_result=None, dry_exc=None):
    """test-fill 公共桩：json body + 模板 service + executor.dry_run 替身（捕获入参）。
    dry_run 在端点内延迟 `from ... import`，monkeypatch executor 模块属性即可生效。"""
    from rag.svr.template_fill import executor as executor_mod
    tpl = tpl if tpl is not None else _make_fill_tpl()
    calls = {}

    async def fake_dry_run(tenant_id, template_id, kb_ids, params):
        calls["args"] = (tenant_id, template_id, kb_ids, params)
        if dry_exc is not None:
            raise dry_exc
        return dry_result if dry_result is not None else {
            "values": {"k1": "v"}, "cells": {"k1": "filled"},
            "evidence": {"k1": {"query": "q", "chunks": []}}, "partial": False}

    monkeypatch.setattr(executor_mod, "dry_run", fake_dry_run)
    monkeypatch.setattr(mod, "request", _FakeJsonRequest(body))
    monkeypatch.setattr(mod, "TplTemplateService", types.SimpleNamespace(
        get_owned=lambda tid, uid, **kw: tpl))
    return calls


def test_test_fill_rejects_draft_template(monkeypatch):
    """非 published（draft/disabled）拒绝试跑，dry_run 不被调用。"""
    mod = _template_api
    for status in ("draft", "disabled"):
        calls = _patch_test_fill_deps(
            monkeypatch, mod, {"kb_ids": ["kb1"]},
            tpl=_make_fill_tpl(status=status))
        resp = asyncio.run(mod.test_fill_template("tpl_x"))
        d = _err_dict(resp)
        assert d["code"] == DATA_ERROR_CODE, f"status={status} 应拒绝"
        assert "发布" in d["message"]
        assert "args" not in calls, f"status={status} 不得调 dry_run"


def test_test_fill_rejects_empty_kb_ids(monkeypatch):
    """kb_ids 缺失/空数组/全无效项 → 拒绝，dry_run 不被调用。"""
    mod = _template_api
    for body in ({}, {"kb_ids": []}, {"kb_ids": ["", None, 123, "   "]}):
        calls = _patch_test_fill_deps(monkeypatch, mod, body)
        resp = asyncio.run(mod.test_fill_template("tpl_x"))
        d = _err_dict(resp)
        assert d["code"] == DATA_ERROR_CODE, f"body={body} 应拒绝"
        assert "知识库" in d["message"]
        assert "args" not in calls, f"body={body} 不得调 dry_run"


def test_test_fill_permission_error(monkeypatch):
    """KB 越权（PermissionError）→「知识库不属于当前租户」，不 500。"""
    mod = _template_api
    calls = _patch_test_fill_deps(
        monkeypatch, mod, {"kb_ids": ["kb1"]}, dry_exc=PermissionError("kb1"))
    resp = asyncio.run(mod.test_fill_template("tpl_x"))
    d = _err_dict(resp)
    assert d["code"] == DATA_ERROR_CODE and "不属于当前租户" in d["message"]
    assert "args" in calls


def test_test_fill_value_error_passthrough(monkeypatch):
    """ValueError（如模板版本不存在/未配置填写点）→ str(e) 原样透传为中文错误。"""
    mod = _template_api
    calls = _patch_test_fill_deps(
        monkeypatch, mod, {"kb_ids": ["kb1"]}, dry_exc=ValueError("模板版本不存在"))
    resp = asyncio.run(mod.test_fill_template("tpl_x"))
    d = _err_dict(resp)
    assert d["code"] == DATA_ERROR_CODE and d["message"] == "模板版本不存在"
    assert "args" in calls


def test_test_fill_unknown_error_fallback(monkeypatch):
    """未知异常（检索/LLM 崩溃等）→ 兜底「试跑失败，请重试」，不 500。"""
    mod = _template_api
    calls = _patch_test_fill_deps(
        monkeypatch, mod, {"kb_ids": ["kb1"]}, dry_exc=RuntimeError("boom"))
    resp = asyncio.run(mod.test_fill_template("tpl_x"))
    d = _err_dict(resp)
    assert d["code"] == DATA_ERROR_CODE and "试跑失败" in d["message"]
    assert "args" in calls


def test_test_fill_success_returns_data_and_args(monkeypatch):
    """成功路径：返回 dry_run 的 data 结构；入参 tenant=current_user、kb 过滤、params 直取。"""
    mod = _template_api
    dry_data = {"values": {"k1": "产值"}, "cells": {"k1": "filled"},
                "evidence": {"k1": {"query": "q", "chunks": []}}, "partial": True}
    calls = _patch_test_fill_deps(
        monkeypatch, mod,
        {"kb_ids": ["kb1", "", None, 7], "params": {"tone": "正式"}},
        dry_result=dry_data)
    resp = asyncio.run(mod.test_fill_template("tpl_x"))
    assert resp["code"] == 0
    assert resp["data"] == dry_data
    assert calls["args"] == ("u1", "tpl_x", ["kb1"], {"tone": "正式"})


def test_test_fill_non_dict_params_coerced_empty(monkeypatch):
    """对抗性：params 传数组/字符串/数字 → 归一为 {}，不透传脏类型给 pipeline。"""
    mod = _template_api
    for bad in (["a"], "str", 42, None):
        calls = _patch_test_fill_deps(monkeypatch, mod,
                                      {"kb_ids": ["kb1"], "params": bad})
        resp = asyncio.run(mod.test_fill_template("tpl_x"))
        assert resp["code"] == 0, f"params={bad!r} 应宽松归一而非拒绝"
        assert calls["args"][3] == {}, f"params={bad!r} 必须归一为空 dict"


# ---------- 批量删除模板端点 ----------

def _patch_batch_delete(monkeypatch, mod, body, results):
    """batch_delete 公共桩：json body + delete_template 逐 id 返回 results[id]=(ok,msg)。
    返回 calls 记录每次调用的 (template_id, tenant_id)。"""
    calls = []
    monkeypatch.setattr(mod, "request", _FakeJsonRequest(body))
    monkeypatch.setattr(mod, "TplTemplateService", types.SimpleNamespace(
        delete_template=lambda tid, uid: (calls.append((tid, uid)),
                                          results.get(tid, (True, "")))[1]))
    return calls


def test_batch_delete_route_and_endpoint_exist():
    mod = _template_api
    assert inspect.iscoroutinefunction(mod.batch_delete_templates)


@pytest.mark.parametrize("body", [
    None, {}, {"ids": None}, {"ids": []}, {"ids": "tpl_x"},
    {"ids": [1, 2]}, {"ids": ["", "tpl_a"]}, {"ids": ["tpl_a", None]},
])
def test_batch_delete_rejects_invalid_ids(monkeypatch, body):
    """对抗性：ids 缺失/空/非数组/混入非串/空串 → 102 拒绝，且不触碰 service。"""
    mod = _template_api
    calls = _patch_batch_delete(monkeypatch, mod, body, {})
    resp = asyncio.run(mod.batch_delete_templates())
    d = _err_dict(resp)
    assert d["code"] == DATA_ERROR_CODE, f"body={body!r} 应拒绝"
    assert "ids" in d["message"]
    assert not calls, f"body={body!r} 不得触发任何删除"


def test_batch_delete_caps_at_50(monkeypatch):
    """单次上限 50：51 个 id 直接拒绝，service 零调用。"""
    mod = _template_api
    calls = _patch_batch_delete(monkeypatch, mod,
                                {"ids": [f"tpl_{i}" for i in range(51)]}, {})
    resp = asyncio.run(mod.batch_delete_templates())
    d = _err_dict(resp)
    assert d["code"] == DATA_ERROR_CODE and "50" in d["message"]
    assert not calls


def test_batch_delete_mixed_success_and_failure(monkeypatch):
    """部分失败不影响其余：deleted 只含成功 id，failed 逐条带原因。"""
    mod = _template_api
    calls = _patch_batch_delete(
        monkeypatch, mod, {"ids": ["tpl_a", "tpl_b", "tpl_c"]},
        {"tpl_b": (False, "该模板已有填写任务记录，不可删除（历史任务需保留可下载）"),
         "tpl_c": (False, "已发布模板不可删除，请先停用")})
    resp = asyncio.run(mod.batch_delete_templates())
    assert resp["code"] == 0
    assert resp["data"]["deleted"] == ["tpl_a"]
    failed = {f["id"]: f["message"] for f in resp["data"]["failed"]}
    assert set(failed) == {"tpl_b", "tpl_c"}
    assert "填写任务" in failed["tpl_b"] and "停用" in failed["tpl_c"]
    assert calls == [("tpl_a", "u1"), ("tpl_b", "u1"), ("tpl_c", "u1")], \
        "每个 id 都必须尝试且以 current_user.id 归属租户"


def test_batch_delete_all_success(monkeypatch):
    mod = _template_api
    calls = _patch_batch_delete(
        monkeypatch, mod, {"ids": ["tpl_a", "tpl_b"]}, {})
    resp = asyncio.run(mod.batch_delete_templates())
    assert resp["code"] == 0
    assert resp["data"]["deleted"] == ["tpl_a", "tpl_b"]
    assert resp["data"]["failed"] == []
    assert len(calls) == 2


# ---------- 后台 AI 识别（detect-async + 状态落库） ----------

class _FakeThread:
    """threading.Thread 桩：不真起线程，捕获 target/args 供断言。"""

    instances: ClassVar[list] = []

    def __init__(self, target=None, daemon=False, name=None, args=()):
        self.target, self.args, self.name = target, args, name
        _FakeThread.instances.append(self)

    def start(self):
        self.started = True


def _patch_detect_async(monkeypatch, mod, body, tpl=None, ver="default",
                        thread_cls=_FakeThread, reset=True):
    """detect-async 公共桩：json body + get_owned/latest/set_detect_status + Thread 捕获。
    ver 传 None 表示 latest 返回 None；"empty" 表示无填写点版本；
    reset=False 跳过 _detecting 清理（幂等测试模拟「上一个线程还在跑」）。"""
    if reset:
        mod._detecting.clear()
    _FakeThread.instances = []
    tpl = tpl if tpl is not None else types.SimpleNamespace(id="tpl_x")
    if ver == "default":
        ver = _make_ver()  # 带填写点（默认：应被拒绝）
    elif ver == "empty":
        ver = _make_ver(placeholders=[])
    statuses = []
    monkeypatch.setattr(mod, "request", _FakeJsonRequest(body))
    monkeypatch.setattr(mod, "TplTemplateService", types.SimpleNamespace(
        get_owned=lambda tid, uid, **kw: tpl,
        set_detect_status=lambda tid, st, err="": statuses.append((tid, st, err))))
    monkeypatch.setattr(mod, "TplTemplateVersionService", types.SimpleNamespace(
        latest=lambda tid: ver))
    if thread_cls is not None:
        monkeypatch.setattr(mod.threading, "Thread", thread_cls)
    return statuses


def test_detect_async_route_and_endpoint_exist():
    mod = _template_api
    assert inspect.iscoroutinefunction(mod.detect_placeholders_async)


def test_detect_async_rejects_template_with_placeholders(monkeypatch):
    """已有填写点的模板拒绝（防后台自动覆盖人工配置）。"""
    mod = _template_api
    statuses = _patch_detect_async(
        monkeypatch, mod, {"template_id": "tpl_x"}, ver="default")
    resp = asyncio.run(mod.detect_placeholders_async())
    d = _err_dict(resp)
    assert d["code"] == DATA_ERROR_CODE
    assert "已配置填写点" in d["message"]
    assert not statuses and not _FakeThread.instances


def test_detect_async_accepts_empty_template_and_returns_running(monkeypatch):
    """无填写点 → 置 running + 起线程 + 返回 running；线程参数钉住模板/租户。"""
    mod = _template_api
    statuses = _patch_detect_async(
        monkeypatch, mod, {"template_id": "tpl_x"}, ver="empty")
    resp = asyncio.run(mod.detect_placeholders_async())
    assert resp["code"] == 0 and resp["data"]["status"] == "running"
    assert statuses == [("tpl_x", "running", "")]
    assert len(_FakeThread.instances) == 1
    assert _FakeThread.instances[0].args == ("tpl_x", "u1"), \
        "线程必须拿到 template_id + current_user.id"


def test_detect_async_accepts_missing_version(monkeypatch):
    """版本行缺失（latest=None）也放行——worker 内会以「模板文件缺失」收口。"""
    mod = _template_api
    statuses = _patch_detect_async(
        monkeypatch, mod, {"template_id": "tpl_x"}, ver=None)
    resp = asyncio.run(mod.detect_placeholders_async())
    assert resp["code"] == 0 and resp["data"]["status"] == "running"
    assert statuses == [("tpl_x", "running", "")]


def test_detect_async_idempotent_while_running(monkeypatch):
    """同一模板重复触发：幂等返回 running，不重复置状态/起线程。"""
    mod = _template_api
    _patch_detect_async(monkeypatch, mod, {"template_id": "tpl_x"}, ver="empty")
    resp1 = asyncio.run(mod.detect_placeholders_async())
    assert resp1["data"]["status"] == "running"
    # 模拟第二个请求进来时首个线程仍在跑（_detecting 未清理）
    _patch_detect_async(monkeypatch, mod, {"template_id": "tpl_x"}, ver="empty",
                        reset=False)
    resp2 = asyncio.run(mod.detect_placeholders_async())
    assert resp2["code"] == 0 and resp2["data"]["status"] == "running"
    assert not _FakeThread.instances, "幂等路径不得重复起线程"


def test_detect_async_thread_spawn_failure_marks_failed(monkeypatch):
    """对抗性：线程启动失败 → 回收标记 + 置 failed + 返回错误，防卡 running。"""
    mod = _template_api

    class _BoomThread:
        def __init__(self, *a, **kw):
            raise RuntimeError("no threads")

    statuses = _patch_detect_async(
        monkeypatch, mod, {"template_id": "tpl_x"}, ver="empty",
        thread_cls=_BoomThread)
    resp = asyncio.run(mod.detect_placeholders_async())
    d = _err_dict(resp)
    assert d["code"] == DATA_ERROR_CODE and "启动失败" in d["message"]
    assert statuses and statuses[-1][1] == "failed"
    assert "tpl_x" not in mod._detecting, "失败后必须回收防重入标记"


def _patch_detect_worker(monkeypatch, mod, *, tpl="exists", blob=b"blob",
                         candidates_err=False, detect_err=False,
                         suggestions=None, validate_ok=True, save_ok=True):
    """_run_detect_task 公共桩：拦截存储/LLM/校验/保存全链路，记录调用。"""
    calls = {"save": [], "statuses": []}
    tpl_obj = None
    if tpl == "exists":
        tpl_obj = types.SimpleNamespace(
            id="tpl_x", file_type="docx", status="draft",
            to_dict=lambda: {"id": "tpl_x", "file_type": "docx", "status": "draft"})
    monkeypatch.setattr(mod, "TplTemplateService", types.SimpleNamespace(
        get_owned=lambda tid, uid, **kw: tpl_obj,
        set_detect_status=lambda tid, st, err="": calls["statuses"].append((st, err))))
    monkeypatch.setattr(mod, "TplTemplateVersionService", types.SimpleNamespace(
        latest=lambda tid: _make_ver(),
        save_placeholders=lambda tpl_dict, items: calls["save"].append(
            (tpl_dict["id"], items)) or (save_ok, "storage down" if not save_ok
                                         else {"id": "ver1"})))
    monkeypatch.setattr(mod, "_load_latest_blob",
                        lambda tid: (None, {"code": 102}) if blob is None else (blob, None))
    if candidates_err:
        def _boom(*a, **kw):
            raise ValueError("bad zip")
        monkeypatch.setattr(mod, "_extract_candidates", _boom)
    else:
        monkeypatch.setattr(mod, "_extract_candidates",
                            lambda ft, b: [{"text": "项目名称"}])
    if detect_err:
        async def _boom_async(*a, **kw):
            raise RuntimeError("llm down")
        monkeypatch.setattr(mod, "detect_fill_points", _boom_async)
    else:
        async def _ok_async(*a, **kw):
            return suggestions if suggestions is not None else []
        monkeypatch.setattr(mod, "detect_fill_points", _ok_async)
    monkeypatch.setattr(mod, "validate_placeholders",
                        lambda items, cands: (validate_ok, "key 重复" if not validate_ok else ""))
    return calls


def _worker_suggestions():
    return [{"key": "project_name", "name": "项目名称", "fill_mode": "llm",
             "required": "true", "anchor": "项目名称", "addr": "1"}]


def test_detect_worker_full_success(monkeypatch):
    """全绿路径：规整 required → 校验 → 自动保存 → done。"""
    mod = _template_api
    mod._detecting.add("tpl_x")
    calls = _patch_detect_worker(monkeypatch, mod, suggestions=_worker_suggestions())
    mod._run_detect_task("tpl_x", "u1")
    assert calls["statuses"] == [("done", "")], calls["statuses"]
    assert len(calls["save"]) == 1
    saved = calls["save"][0][1][0]
    assert saved["required"] is True, "字符串 'true' 必须规整为 bool"


def test_detect_worker_template_missing_marks_failed(monkeypatch):
    mod = _template_api
    mod._detecting.add("tpl_x")
    calls = _patch_detect_worker(monkeypatch, mod, tpl=None)
    mod._run_detect_task("tpl_x", "u1")
    assert calls["statuses"] == [("failed", "模板不存在")]
    assert "tpl_x" not in mod._detecting, "finally 必须回收防重入标记"


def test_detect_worker_blob_missing_marks_failed(monkeypatch):
    mod = _template_api
    mod._detecting.add("tpl_x")
    calls = _patch_detect_worker(monkeypatch, mod, blob=None)
    mod._run_detect_task("tpl_x", "u1")
    assert calls["statuses"][0][0] == "failed"


def test_detect_worker_candidates_crash_marks_failed(monkeypatch):
    mod = _template_api
    mod._detecting.add("tpl_x")
    calls = _patch_detect_worker(monkeypatch, mod, candidates_err=True)
    mod._run_detect_task("tpl_x", "u1")
    assert calls["statuses"][0][0] == "failed"
    assert "损坏" in calls["statuses"][0][1]


def test_detect_worker_llm_crash_marks_failed(monkeypatch):
    mod = _template_api
    mod._detecting.add("tpl_x")
    calls = _patch_detect_worker(monkeypatch, mod, detect_err=True)
    mod._run_detect_task("tpl_x", "u1")
    assert calls["statuses"][0][0] == "failed"
    assert "重试" in calls["statuses"][0][1]


def test_detect_worker_zero_suggestions_marks_failed(monkeypatch):
    """0 条识别结果 → failed（列表显示「识别完成」但详情空白比显式失败更误导）。"""
    mod = _template_api
    mod._detecting.add("tpl_x")
    calls = _patch_detect_worker(monkeypatch, mod, suggestions=[])
    mod._run_detect_task("tpl_x", "u1")
    st, err = calls["statuses"][0]
    assert st == "failed" and "未识别到填写点" in err


def test_detect_worker_validate_reject_marks_failed(monkeypatch):
    mod = _template_api
    mod._detecting.add("tpl_x")
    calls = _patch_detect_worker(monkeypatch, mod,
                                 suggestions=_worker_suggestions(), validate_ok=False)
    mod._run_detect_task("tpl_x", "u1")
    st, err = calls["statuses"][0]
    assert st == "failed" and "校验" in err
    assert not calls["save"], "校验不过不得落保存"


def test_detect_worker_save_failure_marks_failed(monkeypatch):
    mod = _template_api
    mod._detecting.add("tpl_x")
    calls = _patch_detect_worker(monkeypatch, mod,
                                 suggestions=_worker_suggestions(), save_ok=False)
    mod._run_detect_task("tpl_x", "u1")
    st, err = calls["statuses"][0]
    assert st == "failed" and "storage down" in err


def test_detect_worker_never_leaves_detecting_marker(monkeypatch):
    """对抗性：任意失败路径后 _detecting 标记必须被回收（幂等重触发才可用）。"""
    mod = _template_api
    for kwargs in ({"tpl": None}, {"blob": None}, {"candidates_err": True},
                   {"detect_err": True}, {"suggestions": []}):
        mod._detecting.add("tpl_x")
        _patch_detect_worker(monkeypatch, mod, **kwargs)
        mod._run_detect_task("tpl_x", "u1")
        assert "tpl_x" not in mod._detecting, f"kwargs={kwargs} 未回收标记"
