"""executor 单测：外部依赖（retriever/LLMBundle/STORAGE_IMPL）全部 monkeypatch，不真调 KB/LLM。"""
import types

import pytest


def test_validate_kbs_tenant_mismatch_rejected(monkeypatch):
    """KB 不属于任务租户 → 拒绝（跨租户数据泄露防线）。"""
    from rag.svr.template_fill import executor
    monkeypatch.setattr(executor.KnowledgebaseService, "get_by_ids",
                        staticmethod(lambda ids: [types.SimpleNamespace(
                            id="kb1", tenant_id="OTHER", embd_id="bge-m3")]))
    with pytest.raises(PermissionError):
        executor._load_and_check_kbs("tenant_me", ["kb1"])


def test_validate_kbs_empty_rejected(monkeypatch):
    """kb_ids 全无效/查不到 → ValueError。"""
    from rag.svr.template_fill import executor
    monkeypatch.setattr(executor.KnowledgebaseService, "get_by_ids", staticmethod(lambda ids: []))
    with pytest.raises(ValueError):
        executor._load_and_check_kbs("t", [""])


def test_validate_kbs_mixed_embedding_rejected(monkeypatch):
    """混合 Embedding 模型的 KB → ValueError（检索会错位）。"""
    from rag.svr.template_fill import executor
    monkeypatch.setattr(executor.KnowledgebaseService, "get_by_ids",
                        staticmethod(lambda ids: [
                            types.SimpleNamespace(id="kb1", tenant_id="t", embd_id="bge"),
                            types.SimpleNamespace(id="kb2", tenant_id="t", embd_id="other")]))
    with pytest.raises(ValueError):
        executor._load_and_check_kbs("t", ["kb1", "kb2"])


def test_retrieve_slot_chunks_shape(monkeypatch):
    """检索返回裁剪为 [{content, doc_id, doc_name, similarity}]，content 截 800 字，内部字段剥离。"""
    from rag.svr.template_fill import executor

    class FakeRetriever:
        async def retrieval(self, *a, **kw):
            return {"chunks": [{
                "content_with_weight": "x" * 2000, "doc_id": "d1",
                "docnm_kwd": "招标文件.pdf", "similarity": 0.87,
                "vector": [1], "content_ltks": "junk"}]}

    monkeypatch.setattr(executor.settings, "retriever", FakeRetriever())
    monkeypatch.setattr(executor, "_load_and_check_kbs", lambda tenant, kbs: [
        types.SimpleNamespace(id="kb1", tenant_id=tenant, embd_id="bge")])
    monkeypatch.setattr(executor, "_build_embd_mdl", lambda tenant, kbs: object())
    monkeypatch.setattr(executor, "label_question", lambda q, kbs: None)
    out = executor._run_async(executor.retrieve_slot("t", ["kb1"], "查询词", top_k=4))
    assert out[0]["content"] == "x" * 800
    assert set(out[0]) == {"content", "doc_id", "doc_name", "similarity"}


def test_retrieve_slot_topk_clamped(monkeypatch):
    """top_k 钳制到 [1, 20]，非法值（0/负数/超大）不炸。"""
    from rag.svr.template_fill import executor
    captured = {}

    class FakeRetriever:
        async def retrieval(self, question, embd_mdl, tenant_ids, kb_ids, page, page_size, *a, **kw):
            captured["page_size"] = page_size
            return {"chunks": []}

    monkeypatch.setattr(executor.settings, "retriever", FakeRetriever())
    monkeypatch.setattr(executor, "_load_and_check_kbs", lambda tenant, kbs: [
        types.SimpleNamespace(id="kb1", tenant_id=tenant, embd_id="bge")])
    monkeypatch.setattr(executor, "_build_embd_mdl", lambda tenant, kbs: object())
    monkeypatch.setattr(executor, "label_question", lambda q, kbs: None)
    for bad in (0, -5, 999):
        executor._run_async(executor.retrieve_slot("t", ["kb1"], "q", top_k=bad))
        assert 1 <= captured["page_size"] <= 20
