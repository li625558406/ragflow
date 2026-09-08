"""KBUploader PDF 页数解析上限回归：小体积多页 PDF 必须只上传不排队解析。

背景：PARSE_SIZE_LIMIT=5MiB 挡不住「高压缩文本型 PDF」——1.3MB 可达 ~100 页，
每 12 页一个分片任务跑 DeepDOC 布局识别（CPU 密集），批量入库时拖垮共享 task
队列（实测 2026-09-08 lag 积压 110）。页数超限的文件照常上传，用户可在 KB UI
手动触发解析。
"""
import sys
import types

sys.path.insert(0, ".")


class _FakeUploader:
    """绕过 __init__（不连 KB），复用真实 _upload_blob 逻辑的被测实例。"""

    def __new__(cls):
        from rag.svr.crawler_engine.kb_uploader import KBUploader

        inst = KBUploader.__new__(KBUploader)
        inst._kb_id = "kb-1"
        inst._tenant_id = "tenant-1"
        inst._parser_id = "naive"
        return inst


def _fake_kb_and_docmonk(monkeypatch, captured):
    """桩掉 FileService.upload_document 与 _set_parser/_queue_parsing。"""

    class _Doc:
        def __init__(self, doc_id):
            self.id = doc_id

    def fake_upload(kb, file_objs, tenant_id):
        return [], [({"id": f"doc-{fo.filename}"}, None) for fo in file_objs]

    from api.db.services import file_service

    monkeypatch.setattr(file_service, "FileService", types.SimpleNamespace(upload_document=fake_upload))
    inst = _FakeUploader()
    monkeypatch.setattr(inst, "_set_parser", lambda did: captured["set_parser"].append(did))
    monkeypatch.setattr(inst, "_queue_parsing", lambda doc, did: captured["queued"].append(did))
    return inst


def _fake_kb():
    return types.SimpleNamespace(id="kb-1")


def test_pdf_over_page_limit_skips_parsing(monkeypatch):
    """60 页（>50 上限）的 PDF：上传但不排队解析。"""
    captured = {"set_parser": [], "queued": []}
    inst = _fake_kb_and_docmonk(monkeypatch, captured)
    monkeypatch.setattr(type(inst), "_pdf_page_count",
                        staticmethod(lambda blob: 60), raising=False)
    ids = inst._upload_blob(_fake_kb(), b"%PDF-fake", "招标文件.pdf")
    assert ids == ["doc-招标文件.pdf"]
    assert captured["set_parser"] == [] and captured["queued"] == []


def test_pdf_within_page_limit_parses(monkeypatch):
    """50 页（== 上限，边界）的 PDF：正常排队解析。"""
    captured = {"set_parser": [], "queued": []}
    inst = _fake_kb_and_docmonk(monkeypatch, captured)
    monkeypatch.setattr(type(inst), "_pdf_page_count",
                        staticmethod(lambda blob: 50), raising=False)
    inst._upload_blob(_fake_kb(), b"%PDF-fake", "清单.pdf")
    assert captured["set_parser"] == ["doc-清单.pdf"]
    assert captured["queued"] == ["doc-清单.pdf"]


def test_pdf_page_count_failure_fails_open(monkeypatch):
    """页数统计失败（损坏 PDF 返回 None）：保持原行为，照常排队。"""
    captured = {"set_parser": [], "queued": []}
    inst = _fake_kb_and_docmonk(monkeypatch, captured)
    monkeypatch.setattr(type(inst), "_pdf_page_count",
                        staticmethod(lambda blob: None), raising=False)
    inst._upload_blob(_fake_kb(), b"garbage", "扫描件.pdf")
    assert captured["queued"] == ["doc-扫描件.pdf"]


def test_non_pdf_large_page_count_ignored(monkeypatch):
    """非 PDF 文件不做页数检查（md 即使桩返回 60 页也照常解析）。"""
    captured = {"set_parser": [], "queued": []}
    inst = _fake_kb_and_docmonk(monkeypatch, captured)
    monkeypatch.setattr(type(inst), "_pdf_page_count",
                        staticmethod(lambda blob: 60), raising=False)
    inst._upload_blob(_fake_kb(), b"# md", "公告.md")
    assert captured["queued"] == ["doc-公告.md"]


def test_size_limit_still_wins_for_small_pdf(monkeypatch):
    """体积超 5MiB 的 PDF（即使只有 10 页）：依旧只上传不解析，且不触发页数检查。"""
    captured = {"set_parser": [], "queued": []}
    inst = _fake_kb_and_docmonk(monkeypatch, captured)
    monkeypatch.setattr(type(inst), "_pdf_page_count",
                        staticmethod(lambda blob: 10), raising=False)
    inst._upload_blob(_fake_kb(), b"x" * (5 * 1024 * 1024 + 1), "大附件.pdf")
    assert captured["queued"] == []
