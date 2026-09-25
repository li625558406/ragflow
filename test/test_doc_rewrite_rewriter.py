# test/test_doc_rewrite_rewriter.py
# -*- coding: utf-8 -*-
"""LLM 重写对抗测试：合法JSON/围栏包裹/尾部花括号杂文/非法JSON重试/LLM调用异常重试/
空段落/超限拒绝/输出体量闸/超长节报错。"""
import pytest

from rag.svr.document_rewrite.rewriter import (
    _MAX_PARAGRAPHS,
    _MIN_OUTPUT_RATIO,
    _parse_paragraphs,
    rewrite_section,
)


class FakeMdl:
    """outputs 中放 str 则依次返回；放 Exception 实例则该次调用抛出（模拟网络/限流）。"""

    def __init__(self, outputs: list):
        self.outputs = list(outputs)
        self.calls = 0

    async def async_chat(self, system, messages):
        self.calls += 1
        out = self.outputs[min(self.calls, len(self.outputs)) - 1]
        if isinstance(out, Exception):
            raise out
        return out


def test_parse_plain_json():
    assert _parse_paragraphs('{"paragraphs": ["a", "b"]}') == ["a", "b"]


def test_parse_fenced_json_with_noise():
    txt = '好的，以下是重写结果：\n```json\n{"paragraphs": ["段落一", "段落二"]}\n```\n请查收。'
    assert _parse_paragraphs(txt) == ["段落一", "段落二"]


def test_parse_json_with_trailing_brace_noise():
    """I1 回归：合法 JSON 后跟带 `}` 的杂文，贪婪匹配整体解析失败，
    非贪婪兜底抽第一个对象须成功。"""
    assert _parse_paragraphs('{"paragraphs": ["a"]} 结束 }') == ["a"]
    assert _parse_paragraphs('{"paragraphs": ["a", "b"]}）以上，{完}') == ["a", "b"]


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
    assert mdl.calls == 2


@pytest.mark.asyncio
async def test_rewrite_recovers_when_llm_call_raises_once():
    """I3：async_chat 第一次抛 Exception（网络/限流），第二次返回合法 JSON → 成功。"""
    mdl = FakeMdl([RuntimeError("connection reset by peer"), '{"paragraphs": ["ok after err"]}'])
    out = await rewrite_section("t1", "改", "节", "内容", "", "", mdl=mdl)
    assert out == ["ok after err"]
    assert mdl.calls == 2


@pytest.mark.asyncio
async def test_rewrite_both_llm_errors_raise_valueerror_with_cause():
    """I3 对抗：两轮均抛 LLM 异常 → 统一 ValueError，且 `from` 原异常保留根因。"""
    boom = RuntimeError("rate limited")
    mdl = FakeMdl([boom, boom])
    with pytest.raises(ValueError, match="重写失败") as exc_info:
        await rewrite_section("t1", "改", "节", "内容", "", "", mdl=mdl)
    assert mdl.calls == 2
    assert exc_info.value.__cause__ is boom


@pytest.mark.asyncio
async def test_section_too_long_raises_before_llm():
    mdl = FakeMdl([])
    with pytest.raises(ValueError, match="过长"):
        await rewrite_section("t1", "改", "节", "字" * 30001, "", "", mdl=mdl)
    assert mdl.calls == 0


def test_paragraph_over_limit_rejected_not_truncated():
    """事故回归（2026-09-25）：段数超限曾是静默截断 92→50，整节尾部内容被销毁仍照常落版本。
    现契约：超限一律拒绝（返回 None 触发重试），绝不静默丢段。"""
    paras = [f"段{i}" for i in range(_MAX_PARAGRAPHS + 10)]
    out = _parse_paragraphs('{"paragraphs": ' + repr(paras).replace("'", '"') + "}")
    assert out is None


def test_paragraph_overlong_rejected_not_truncated():
    """单段超长同理：拒绝（None 触发重试），不再截断到 2000 字静默丢文。"""
    long_out = _parse_paragraphs('{"paragraphs": ["' + "长" * 3000 + '"]}')
    assert long_out is None


@pytest.mark.asyncio
async def test_output_volume_below_ratio_rejects_and_fails_with_clear_message():
    """事故回归：LLM 重生成 92 段被截到 50 段后体量只有源文约一半，仍照常落版本。
    现契约：输出总字数低于源文 _MIN_OUTPUT_RATIO → 拒绝保存（重试一次仍不足则报错，
    文案须讲明「防内容丢失已放弃」，不得落版本）。"""
    src = "源" * 1000
    bad = '{"paragraphs": ["' + "短" * (int(1000 * _MIN_OUTPUT_RATIO) - 10) + '"]}'
    mdl = FakeMdl([bad, bad])
    with pytest.raises(ValueError, match="内容丢失"):
        await rewrite_section("t1", "改", "节", src, "", "", mdl=mdl)
    assert mdl.calls == 2


@pytest.mark.asyncio
async def test_output_volume_recovers_on_second_try():
    mdl = FakeMdl(
        ['{"paragraphs": ["太短"]}',
         '{"paragraphs": ["' + "够" * int(1000 * _MIN_OUTPUT_RATIO + 100) + '"] }']
    )
    out = await rewrite_section("t1", "改", "节", "源" * 1000, "", "", mdl=mdl)
    assert len(out[0]) == int(1000 * _MIN_OUTPUT_RATIO + 100)
    assert mdl.calls == 2


@pytest.mark.asyncio
async def test_output_volume_gate_not_applied_to_tiny_sections():
    """微小源文（引导词/空节）不做体量闸，正常改写不被误拒。"""
    mdl = FakeMdl(['{"paragraphs": ["一段简短的新正文。"]}'])
    out = await rewrite_section("t1", "改", "节", "原文", "", "", mdl=mdl)
    assert out == ["一段简短的新正文。"]
    assert mdl.calls == 1


@pytest.mark.asyncio
async def test_rewrite_llm_error_final_message_unchanged():
    """LLM 异常路径文案保持「重写失败」不变（体量闸文案只属于体量失败）。"""
    boom = RuntimeError("x")
    mdl = FakeMdl([boom, boom])
    with pytest.raises(ValueError, match="重写失败"):
        await rewrite_section("t1", "改", "节", "内容", "", "", mdl=mdl)
