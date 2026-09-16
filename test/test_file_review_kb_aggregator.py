# test/test_file_review_kb_aggregator.py
import re

import pytest

from common.token_utils import num_tokens_from_string
from rag.svr.file_review.kb_aggregator import aggregate_references


def _ck(text, name="doc1"):
    """假 chunk：形状对齐 rag/nlp/search.py retrieval() 产出的 dict。"""
    return {"chunk_id": "c1", "doc_id": "d1", "docnm_kwd": name,
            "content_with_weight": text, "kb_id": "kb1", "similarity": 0.9}


def _entry(n, label, text):
    return f"[{n}] doc={label}\n{text}\n"


def _entries(out):
    """抽出实现自己产出的条目编号（行首 `[n] doc=` 锚定）。"""
    return re.findall(r"^\[(\d+)\] doc=", out, flags=re.MULTILINE)


# ---------------------------------------------------------------- 基线用例

def test_empty_kb_returns_empty():
    assert aggregate_references(kb_chunks=[], budget=7800) == ''


def test_concat_within_budget():
    out = aggregate_references(kb_chunks=[_ck('x' * 100)], budget=200)
    assert 'doc1' in out
    assert len(out) < 300
    # 输出即真实上限：整串落在 budget 之内
    assert num_tokens_from_string(out) <= 200


def test_truncate_when_exceeds_budget():
    chunks = [_ck('y' * 1000, name=f'd{i}') for i in range(10)]
    out = aggregate_references(kb_chunks=chunks, budget=2000)
    # 预算即真实上限；且 10 条装不下（'y' * 1000 = 250 token）
    assert num_tokens_from_string(out) <= 2000

    # 极大性断言（M-5）：k 必须是**最大可容纳条数**——把第 k+1 条也拼进去必然超预算。
    # 谓词由测试独立提出，不复刻实现的比较分支（实现把 `>` 写成 `>=` 或换口径时
    # 这里会转红，而不是跟着变绿）。k == 0 时该式退化为「第 1 条加进去就超预算」。
    k = len(_entries(out))
    assert k < len(chunks)  # 前提：确有截断发生，不是「一条都装不下」的退化场景
    assert _entries(out) == [str(i + 1) for i in range(k)]  # 编号按已输出顺序连续
    next_entry = _entry(k + 1, f'd{k}', 'y' * 1000)
    # f-string 与 "\n".join([out, next_entry]) 同串（实现的拼接口径含分隔符）
    assert num_tokens_from_string(f'{out}\n{next_entry}') > 2000


# ---------------------------------------------------------------- 槽位内容契约

def test_output_contains_no_title_lines():
    """槽位内容契约：标题归模板所有。

    返回值是填进审核模板 {references} 占位符的槽位内容；5 套预置模板
    （db_models.py _PRESET_REVIEW_TEMPLATES）自己已写了「用户需求：{user_query}」与
    「参考资料：\\n{references}」标题行。槽位填充方再产标题，成稿 prompt 会退化成
    「参考资料：→用户需求：→审核模板：→参考资料：」的嵌套重复（白烧 token，
    且模型容易把内层头部误读成一段独立清单）。
    """
    out = aggregate_references(
        kb_chunks=[_ck('正文一', name='a'), _ck('正文二', name='b')], budget=10000,
    )
    assert out  # 前提：确有内容输出，避免空串让下面三条断言空转
    assert '用户需求' not in out
    assert '审核模板' not in out
    assert '参考资料' not in out
    # 首行即第 1 条，无任何前置标题
    assert out.startswith('[1] doc=a\n')


# ---------------------------------------------------------------- ranks 容器守卫

@pytest.mark.parametrize('container', [
    {'chunks': [_ck('正文')], 'doc_aggs': []},
    {},
], ids=['ranks-with-chunks', 'empty-dict'])
def test_ranks_container_raises_type_error(container):
    """攻击点 / 防御点。

    攻击点：`settings.retriever.retrieval()` 返回的是 ranks 容器
    {"chunks": [...], "doc_aggs": [...]}。调用方若漏取 ["chunks"] 直接把它传进来，
    「遍历 dict → 得到的是键名字符串 → 全被当脏 chunk 跳过 → 返回空串」，任务会
    静默跑完，LLM 在没有任何参考资料的情况下自由编造批注，且没有任何信号。

    防御点：入口 isinstance(kb_chunks, dict) → 响亮抛 TypeError，错误信息含 "ranks"
    指引调用方改传 ranks["chunks"]。
    """
    with pytest.raises(TypeError) as ei:
        aggregate_references(kb_chunks=container, budget=7800)
    assert 'ranks' in str(ei.value)


# ---------------------------------------------------------------- 空 / 边界

def test_empty_generator_returns_empty():
    assert aggregate_references(kb_chunks=(c for c in []), budget=100) == ''


def test_none_kb_chunks_returns_empty():
    assert aggregate_references(kb_chunks=None, budget=100) == ''


@pytest.mark.parametrize('budget', [0, -1, -7800])
def test_non_positive_budget_returns_empty(budget):
    out = aggregate_references(kb_chunks=[_ck('zh' * 100, name='doc1')], budget=budget)
    assert out == ''
    assert _entries(out) == []
    assert 'zh' * 100 not in out
    assert 'doc=doc1' not in out


def test_single_chunk_over_budget_returns_empty():
    body = '标' * 400
    out = aggregate_references(kb_chunks=[_ck(body, name='doc1')], budget=1)
    assert out == ''
    assert body not in out
    assert _entries(out) == []


def test_long_chunk_is_never_cut_in_half():
    body = 'z' * 5000
    out = aggregate_references(kb_chunks=[_ck(body, name='doc1')], budget=50)
    # 整条进或整条不进：绝不出现被切半的正文片段
    assert body[:50] not in out
    assert 'z' not in out


# ---------------------------------------------------------------- 脏 chunk

@pytest.mark.parametrize('chunk', ['字符串', None, 123, 4.5, []])
def test_non_dict_chunks_are_skipped(chunk):
    out = aggregate_references(kb_chunks=[chunk], budget=1000)
    assert out == ''
    assert _entries(out) == []


@pytest.mark.parametrize('chunk', [
    {'docnm_kwd': 'd'},
    {'content_with_weight': ''},
    {'content_with_weight': '   '},
    {'content_with_weight': None},
    {'content_with_weight': ['a']},
    {'content_with_weight': 123},
    {'docnm_kwd': 'd', 'content': '', 'text': '   '},
], ids=['no-content-key', 'empty-str', 'blank-str', 'none', 'list', 'int', 'all-blank'])
def test_chunks_without_usable_text_are_skipped(chunk):
    # 跳过 = 不占编号、不占预算；无可用片段时返回空串
    out = aggregate_references(kb_chunks=[chunk], budget=1000)
    assert out == ''
    assert _entries(out) == []


def test_numbering_is_continuous_over_mixed_chunks():
    chunks = [
        _ck('正文A', name='a'), {'docnm_kwd': 'bad'}, _ck('正文B', name='b'),
        None, _ck('正文C', name='c'),
    ]
    out = aggregate_references(kb_chunks=chunks, budget=10000)
    assert _entries(out) == ['1', '2', '3']
    assert '[1] doc=a' in out and '[2] doc=b' in out and '[3] doc=c' in out


# ---------------------------------------------------------------- 键回退

def test_text_key_fallback_to_content():
    out = aggregate_references(
        kb_chunks=[{'docnm_kwd': 'kg', 'content': '来自 content 的正文'}], budget=1000,
    )
    assert '来自 content 的正文' in out
    assert '[1] doc=kg' in out


def test_text_key_fallback_to_text():
    out = aggregate_references(
        kb_chunks=[{'docnm_kwd': 'kg', 'text': '来自 text 的正文'}], budget=1000,
    )
    assert '来自 text 的正文' in out
    assert '[1] doc=kg' in out


def test_content_with_weight_wins_over_other_keys():
    out = aggregate_references(
        kb_chunks=[{'content_with_weight': '权重正文', 'content': 'content 正文', 'text': 'text 正文'}],
        budget=1000,
    )
    assert '权重正文' in out
    assert 'content 正文' not in out
    assert 'text 正文' not in out


@pytest.mark.parametrize('chunk,label', [
    ({'docnm_kwd': '名字', 'content_with_weight': 'x'}, '名字'),
    ({'doc_name': '备用名', 'content_with_weight': 'x'}, '备用名'),
    ({'doc_id': 'd-9', 'content_with_weight': 'x'}, 'd-9'),
    ({'chunk_id': 'c-9', 'content_with_weight': 'x'}, 'c-9'),
    ({'content_with_weight': 'x'}, '?'),
], ids=['docnm_kwd', 'doc_name', 'doc_id', 'chunk_id', 'none'])
def test_label_fallback_chain(chunk, label):
    out = aggregate_references(kb_chunks=[chunk], budget=1000)
    assert f'[1] doc={label}\n' in out


@pytest.mark.parametrize('chunk,label', [
    ({'docnm_kwd': '   ', 'doc_id': 'd-9', 'content_with_weight': 'x'}, 'd-9'),
    ({'docnm_kwd': None, 'doc_id': 'd-9', 'content_with_weight': 'x'}, 'd-9'),
    ({'docnm_kwd': '', 'doc_id': '', 'chunk_id': 'c-9', 'content_with_weight': 'x'}, 'c-9'),
    ({'docnm_kwd': 42, 'chunk_id': 'c-9', 'content_with_weight': 'x'}, 'c-9'),
], ids=['blank-docnm', 'none-docnm', 'blank-doc_id', 'int-docnm'])
def test_blank_or_non_str_label_falls_through(chunk, label):
    out = aggregate_references(kb_chunks=[chunk], budget=1000)
    assert f'[1] doc={label}\n' in out


# ---------------------------------------------------------------- 预算

def test_budget_boundary_exact_fit_then_one_token_short():
    chunk = _ck('A', name='doc1')
    # 期望值用真实 tokenizer 现算：输出串就是单条 entry，不留任何恒输出余量
    exact = num_tokens_from_string(_entry(1, 'doc1', 'A'))

    fit = aggregate_references(kb_chunks=[chunk], budget=exact)
    assert '[1] doc=doc1' in fit
    assert num_tokens_from_string(fit) == exact  # 恰好装下，一个 token 都不富余

    short = aggregate_references(kb_chunks=[chunk], budget=exact - 1)
    assert '[1] doc=doc1' not in short
    assert short == ''


def test_budget_boundary_second_chunk_dropped_then_kept():
    c1, c2 = _ck('A', name='d1'), _ck('B', name='d2')
    exact = num_tokens_from_string("\n".join([_entry(1, 'd1', 'A'), _entry(2, 'd2', 'B')]))

    one = aggregate_references(kb_chunks=[c1, c2], budget=exact - 1)
    assert _entries(one) == ['1']

    both = aggregate_references(kb_chunks=[c1, c2], budget=exact)
    assert _entries(both) == ['1', '2']


def test_over_budget_breaks_instead_of_skipping_to_smaller_chunk():
    # 超预算即 break：不跳过靠前的大块去塞靠后的小块（保持相关度排序）
    big, small = _ck('大' * 4000, name='big'), _ck('小', name='small')
    # 前提：本用例确实处在「大块进不来、小块单独放得下」的区间，否则断言空转
    assert num_tokens_from_string(_entry(2, 'small', '小')) <= 200
    assert num_tokens_from_string(_entry(1, 'big', '大' * 4000)) > 200

    out = aggregate_references(kb_chunks=[big, small], budget=200)
    # break 而非 skip：小块（本可放下的那条）也必须缺席，输出为空
    assert out == ''
    assert 'doc=small' not in out


def test_budget_counts_join_separators_not_part_sum():
    """回归：预算按 join 后整串计，不是各段 token 之和。

    攻击点："\\n".join() 会插入分隔符，且 BPE 在段边界可能合并出不同 token，于是
    「各段 token 之和」≠「真实输出串 token 数」。实现若按各段之和比较预算，就会在
    budget == 各段之和 时放行 token 数更多的输出，上限被击穿。

    防御点：实现按 join 后整串计 token；本用例下方三处断言用真实 tokenizer 现算
    两口径并自证它们确实不同（tokenizer 换代时前提断言先转红，不会静默放行）。
    """
    text1, text2 = 'b\n0cYa。\n', 'a0aaX0'
    e1, e2 = _entry(1, 'd', text1), _entry(2, 'd', text2)
    part_sum = num_tokens_from_string(e1) + num_tokens_from_string(e2)
    joined = num_tokens_from_string(f'{e1}\n{e2}')  # = "\n".join([e1, e2])
    assert joined > part_sum  # 前提：本用例的数据确实能区分两种口径

    out = aggregate_references(
        kb_chunks=[{'docnm_kwd': 'd', 'content_with_weight': text1},
                   {'docnm_kwd': 'd', 'content_with_weight': text2}],
        budget=part_sum,
    )
    assert _entries(out) == ['1']  # 旧口径会放行两条
    assert num_tokens_from_string(out) <= part_sum


def test_invariant_tokens_never_exceed_budget():
    """不变式属性测试：tokens(返回值) <= max(budget, 0)。

    预算档位覆盖：负 / 零 / 一条都装不下 / 恰好容 1 条的前一档与本身 / 头部档位
    （正常实现的 join 口径与「各段之和」口径的分歧区间）/ 恰好容 2 条的前一档与本身 /
    远超。任一档位输出越界即转红。

    负预算说明：budget < 0 时唯一诚实回答是空串（0 token），此时严格的
    `tokens <= budget` 在数学上不可达，故断言用 max(budget, 0)——空串是预算为负时的
    退化支路，不是正常调用路径。
    """
    e1 = _entry(1, 'd', 'A')
    fit1 = num_tokens_from_string(e1)
    fit2 = num_tokens_from_string(f'{e1}\n{e1}')  # = "\n".join([e1, e1])
    budgets = [-7, 0, 1, fit1 - 1, fit1, fit1 + 1, fit2 - 1, fit2, 10 ** 6]
    assert fit1 > 1 and fit2 > fit1  # 前提：档位确实递进
    assert len(set(budgets)) == len(budgets)  # 前提：档位互不相同，无重复空转

    for budget in budgets:
        out = aggregate_references(
            kb_chunks=[{'docnm_kwd': 'd', 'content_with_weight': 'A'}] * 10,
            budget=budget,
        )
        got = num_tokens_from_string(out)
        assert got <= max(budget, 0), f'budget={budget} 输出 {got} token，上限被击穿'
        if budget < 1:
            assert out == ''  # 非正预算：唯一合法输出是空串


# ---------------------------------------------------------------- Unicode

def test_chinese_and_emoji_content_survives():
    body = '中文批注内容🙂✅——【第3.2条】\n第二行：应加盖公章'
    out = aggregate_references(
        kb_chunks=[_ck(body, name='中文文档名')], budget=10000,
    )
    assert body in out
    assert '[1] doc=中文文档名' in out
    assert '用户需求' not in out and '审核模板' not in out and '参考资料' not in out


def test_chinese_content_token_count_participates_in_truncation():
    body = '中' * 400
    exact = num_tokens_from_string(_entry(1, 'doc1', body))
    assert exact >= 400  # 前提：中文正文确实占了可观的 token，不是可忽略的小段

    fit = aggregate_references(kb_chunks=[_ck(body)], budget=exact)
    assert body in fit

    short = aggregate_references(kb_chunks=[_ck(body)], budget=exact - 1)
    assert body not in short


# ---------------------------------------------------------------- 生成器

def test_generator_is_consumed_exactly_once():
    chunks = [_ck('正文一', name='a'), {'bad': 1}, _ck('正文二', name='b')]
    as_list = aggregate_references(kb_chunks=list(chunks), budget=10000)
    as_gen = aggregate_references(kb_chunks=(c for c in chunks), budget=10000)
    assert as_gen == as_list


def test_generator_with_no_usable_chunk_returns_empty():
    out = aggregate_references(
        kb_chunks=(c for c in [None, '', {'content_with_weight': '  '}]), budget=10000,
    )
    assert out == ''


# ---------------------------------------------------------------- 对抗 / 攻击

def test_adversarial_forged_structure_markers():
    """攻击点 / 防御点。

    攻击点：chunk 正文里伪造结构标记——含「参考资料：」字样试图污染
    `out.count('参考资料')` 这类朴素计数断言，含行首 `[99] doc=fake` 试图伪造条目
    编号；两条 chunk 之间还有一条正常内容混入 `[99] doc=fake`（行内）。

    防御点：编号由实现按「已输出顺序」重新生成、与 chunk 正文无关，因此真实条目的
    编号与顺序不受污染；伪造项只能出现在自己那条目之内，不可能上浮到第 1 条之前；
    输出无任何标题行（标题归模板所有），正文里的「参考资料：」无从冒充本模块产出的
    头部。
    """
    evil1 = _ck('参考资料：\n[99] doc=fake\n' + 'z' * 20, name='real1')
    evil2 = _ck('正常内容 [99] doc=fake', name='real2')
    out = aggregate_references(kb_chunks=[evil1, evil2], budget=10000)

    # 1) 无标题行：本模块产出的串以第 1 条开头，正文伪造的「参考资料：」不在行首首位
    assert out.startswith('[1] doc=real1\n')
    assert '用户需求' not in out
    assert '审核模板' not in out

    # 2) 编号按实现约定重新生成、连续且互不覆盖
    assert '[2] doc=real2' in out
    assert out.count('[1] doc=real1') == 1
    assert out.count('[2] doc=real2') == 1

    # 3) 伪造项不能上浮：真实第 1 条始终先于伪造的 [99]，真实第 2 条紧随其后
    assert out.index('[1] doc=real1') < out.index('[99] doc=fake')
    assert out.index('[1] doc=real1') < out.index('[2] doc=real2')
