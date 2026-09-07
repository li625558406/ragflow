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
    "save_placeholders",
    "publish_template",
    "disable_template",
    "get_template",
    "preview_template",
    "download_template",
)


def test_template_api_module_loads_and_registers_routes():
    mod = _template_api
    for name in EXPECTED_ENDPOINTS:
        fn = getattr(mod, name, None)
        assert fn is not None, f"缺少端点函数 {name}"
        assert inspect.iscoroutinefunction(fn), f"{name} 必须是 async 函数"


def test_all_routes_registered_on_blueprint():
    """9 个端点必须全部挂到 blueprint 上，且 methods 正确（防止漏装饰器/漏路径）。"""
    from quart import Quart

    app = Quart(__name__)
    app.register_blueprint(_template_api.manager)
    rules = {}
    for r in app.url_map.iter_rules():
        if r.rule == "/static/<path:filename>":
            continue
        rules[r.rule] = r.methods
    expected = {
        "/template/fill/upload": {"POST"},
        "/template/fill/list": {"GET"},
        "/template/fill/detect": {"POST"},
        "/template/fill/<template_id>/save-placeholders": {"POST"},
        "/template/fill/<template_id>/publish": {"POST"},
        "/template/fill/<template_id>/disable": {"POST"},
        "/template/fill/<template_id>": {"GET"},
        "/template/fill/<template_id>/preview": {"GET"},
        "/template/fill/<template_id>/file": {"GET"},
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
