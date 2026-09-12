# test/test_doc_rewrite_rewriter.py
# -*- coding: utf-8 -*-
"""LLM 重写对抗测试：合法JSON/围栏包裹/非法JSON重试/空段落/超限截断/超长节报错。"""
import pytest

from rag.svr.document_rewrite.rewriter import (
    _MAX_PARAGRAPHS,
    _parse_paragraphs,
    rewrite_section,
)


class FakeMdl:
    def __init__(self, outputs: list[str]):
        self.outputs = list(outputs)
        self.calls = 0

    async def async_chat(self, system, messages):
        self.calls += 1
        return self.outputs[min(self.calls, len(self.outputs)) - 1]


def test_parse_plain_json():
    assert _parse_paragraphs('{"paragraphs": ["a", "b"]}') == ["a", "b"]


def test_parse_fenced_json_with_noise():
    txt = '好的，以下是重写结果：\n```json\n{"paragraphs": ["段落一", "段落二"]}\n```\n请查收。'
    assert _parse_paragraphs(txt) == ["段落一", "段落二"]


def test_parse_rejects_bad_shapes():
    assert _parse_paragraphs("不是JSON") is None
    assert _parse_paragraphs('{"paragraphs": []}') is None
    assert _parse_paragraphs('{"paragraphs": ["", "   "]}') is None
    assert _parse_paragraphs('{"other": 1}') is None
    assert _parse_paragraphs('{"paragraphs": "一段文字"}') is None


def test_parse_drops_empty_keeps_nonempty():
    assert _parse_paragraphs('{"paragraphs": ["", "有效段", null, "第二段"]}') == ["有效段", "第二段"]


@pytest.mark.asyncio
async def test_rewrite_success_first_try():
    mdl = FakeMdl(['{"paragraphs": ["新1", "新2"]}'])
    out = await rewrite_section(
        "t1", "补充进度", "进度安排", "原内容", "第1节 A", "第3节 C", mdl=mdl)
    assert out == ["新1", "新2"]
    assert mdl.calls == 1


@pytest.mark.asyncio
async def test_rewrite_retries_once_on_bad_json_then_fails():
    mdl = FakeMdl(["瞎写的", "还是不行"])
    with pytest.raises(ValueError, match="重写失败"):
        await rewrite_section("t1", "改", "节", "内容", "", "", mdl=mdl)
    assert mdl.calls == 2


@pytest.mark.asyncio
async def test_rewrite_recovers_on_second_try():
    mdl = FakeMdl(["垃圾输出", '{"paragraphs": [" recovered "]}'])
    out = await rewrite_section("t1", "改", "节", "内容", "", "", mdl=mdl)
    assert out == ["recovered"]


@pytest.mark.asyncio
async def test_section_too_long_raises_before_llm():
    mdl = FakeMdl([])
    with pytest.raises(ValueError, match="过长"):
        await rewrite_section("t1", "改", "节", "字" * 30001, "", "", mdl=mdl)
    assert mdl.calls == 0


def test_paragraph_limits():
    paras = [f"段{i}" for i in range(_MAX_PARAGRAPHS + 10)]
    out = _parse_paragraphs('{"paragraphs": ' + repr(paras).replace("'", '"') + "}")
    assert out is not None
    assert len(out) <= _MAX_PARAGRAPHS
    long_out = _parse_paragraphs('{"paragraphs": ["' + "长" * 3000 + '"]}')
    assert long_out is not None and len(long_out[0]) == 2000
