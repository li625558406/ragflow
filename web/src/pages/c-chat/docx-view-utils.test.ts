/**
 * 批注文本匹配与 overlay 合并纯函数（review-panel 与 docx 视图共用）。
 * 对抗性视角：不只测 happy path——多命中、重复段、纯符号摘录、脏 patch、
 * state 缺行 / 空列表 / 引用稳定性（防轮询期无谓重渲染）都要击穿验证。
 */
import {
  getLocateText,
  getMatchedText,
  matchAnnotation,
  matchMultiline,
  mergeAnnotationOverlays,
  multilineLineNorms,
} from './docx-view-utils';

const paras = (texts: string[]) =>
  texts.map((text, index) => ({ index, text }));

describe('matchMultiline 有序降级', () => {
  it('序列唯命中：连续段落窗口命中返回基段 index', () => {
    const ps = paras([
      '前文段落，与批注无关。',
      '第二章 投标人须知',
      '投标函附录',
      '法定代表人身份证明',
      '后文段落。',
    ]);
    expect(matchMultiline(ps, '第二章 投标人须知\n投标函附录')).toBe(1);
  });

  it('序列多命中：降级首行通道，取文档序第一个（dup 不再未定位）', () => {
    // 目录 + 正文两处都有相同行序列 → 序列通道 2 hits
    const ps = paras([
      '目录',
      '第二章 投标人须知',
      '投标函附录',
      '正文其他部分',
      '第二章 投标人须知',
      '投标函附录',
    ]);
    expect(matchMultiline(ps, '第二章 投标人须知\n投标函附录')).toBe(1);
  });

  it('序列零命中：首行通道按最长行定位表格段（表格整体是一个 HTML 段落）', () => {
    const ps = paras([
      '评分前言',
      '<table><tr><td>项目</td><td>分值</td><td>是否客观项</td><td>描述</td></tr></table>',
    ]);
    // 行间在段落里夹着其他单元格文本 → 序列通道结构性失配
    expect(matchMultiline(ps, '项目\n分值\n是否客观项\n描述')).toBe(1);
  });

  it('首行通道取最长行：最长行命中多个段落时取首个（目录首现口径）', () => {
    const ps = paras([
      '第一章 招标公告内容很长很长很长',
      '第二章 投标人须知',
      '第二章 投标人须知',
    ]);
    // LLM 复制行「第二章 投标人须知\n第二章 投标人须知」：序列唯命中
    expect(matchMultiline(ps, '第二章 投标人须知\n第二章 投标人须知')).toBe(1);
  });

  it('全部失败：诚实未定位（LLM 复制行在单段文档中序列不成立时按首行）', () => {
    // 单段落文档：序列通道窗口不足，首行通道命中该段
    const ps = paras(['第二章 投标人须知']);
    expect(matchMultiline(ps, '第二章 投标人须知\n第二章 投标人须知')).toBe(0);
  });

  it('纯符号摘录归一化后为空：诚实未定位', () => {
    const ps = paras(['/；', '正常段落']);
    expect(matchMultiline(ps, '/；\n。')).toBe(-1);
  });

  it('对抗输入：空串 / 单行 / 空段落列表 → -1 不抛错', () => {
    expect(matchMultiline([], 'a\nb')).toBe(-1);
    expect(matchMultiline(paras(['a', 'b']), '')).toBe(-1);
    expect(matchMultiline(paras(['a', 'b']), '单行文本')).toBe(-1);
    expect(matchMultiline(paras(['a', 'b']), '\n\n')).toBe(-1);
  });

  it('multilineLineNorms：过滤归一化后过短的行', () => {
    expect(multilineLineNorms('ab。\n/；\ncdef')).toEqual(['ab', 'cdef']);
    expect(multilineLineNorms('no newline')).toEqual([]);
  });
});

describe('getMatchedText / getLocateText', () => {
  it('getMatchedText 字段名兼容', () => {
    expect(getMatchedText({ matched_text: ' a ' })).toBe('a');
    expect(getMatchedText({ text: 'b' })).toBe('b');
    expect(getMatchedText({ quote: 'c' })).toBe('c');
    expect(getMatchedText({})).toBe('');
  });

  it('fixed/resolved + patch → 用 replace 定位（成稿里 matched_text 已不存在）', () => {
    const patch = { find: '旧文案', replace: '新文案' };
    expect(
      getLocateText({ matched_text: '旧文案', status: 'fixed', patch }),
    ).toBe('新文案');
    expect(
      getLocateText({ matched_text: '旧文案', status: 'resolved', patch }),
    ).toBe('新文案');
  });

  it('对抗：open+wontfix 即使带 patch 也用 matched_text；replace 为空串回退', () => {
    const patch = { find: '旧文案', replace: '' };
    expect(
      getLocateText({ matched_text: '旧文案', status: 'open', patch }),
    ).toBe('旧文案');
    expect(
      getLocateText({ matched_text: '旧文案', status: 'wontfix', patch }),
    ).toBe('旧文案');
    expect(
      getLocateText({
        matched_text: '旧文案',
        status: 'fixed',
        patch,
      }),
    ).toBe('旧文案');
    expect(getLocateText({ matched_text: '旧文案', status: 'fixed' })).toBe(
      '旧文案',
    );
  });
});

describe('matchAnnotation 单段匹配', () => {
  it('整串 / 去标签 / 归一化 / 关键词块 ≥2 依次生效', () => {
    expect(matchAnnotation('正文包含旧文案一处', '旧文案')).toBe(true);
    expect(matchAnnotation('<td>旧文案</td>', '旧文案')).toBe(true);
    expect(
      matchAnnotation('正文包含投标文件、签章页一处', '投标文件签章页'),
    ).toBe(true);
    // 关键词块≥2：分词器把 持/有/的/在/且 当分隔符，两块各 ≥6 字且中间隔着
    // 非连续原文（「并」），整串/去标签/归一化三通道都打不中 → 只能靠关键词块
    expect(
      matchAnnotation(
        '投标人须提供近三年财务审计报告并提交投标保证金凭证',
        '提供近三年财务审计报告；提交投标保证金凭证',
      ),
    ).toBe(true);
  });

  it('对抗：空串 / 长度<2 / 完全无关文本 → false', () => {
    expect(matchAnnotation('任意段落', '')).toBe(false);
    expect(matchAnnotation('任意段落', 'a')).toBe(false);
    expect(
      matchAnnotation(' completely unrelated ', '完全无关的批注文本'),
    ).toBe(false);
    // keyword 闸：只有 1 个块命中不算（宁缺勿错）
    expect(
      matchAnnotation(
        '只提到投标人须持有有效资质',
        '持有效的资质；在响应文件中澄清',
      ),
    ).toBe(false);
  });
});

describe('mergeAnnotationOverlays overlay 合并', () => {
  const propsAnns = [
    { id: 'a1', matched_text: '旧文案', status: 'fixed' },
    { id: 'a2', matched_text: '另一条', status: 'open' },
    { id: null, matched_text: '无 id', status: 'open' },
  ];

  it('state 新 status+patch 覆盖 props 旧值（弹框确认保留即时翻转的根修）', () => {
    const latest = [
      {
        id: 'a1',
        status: 'resolved',
        patch: { find: '旧文案', replace: '新文案' },
      },
    ];
    const merged = mergeAnnotationOverlays(propsAnns as any, latest as any);
    expect(merged[0].status).toBe('resolved');
    expect(merged[0].patch).toEqual({ find: '旧文案', replace: '新文案' });
    // 未涉及的行原样保留
    expect(merged[1]).toBe(propsAnns[1]);
    expect(merged[2]).toBe(propsAnns[2]);
  });

  it('无变化（status 相同且 patch 值相等）返回原引用——防轮询期全量重渲染', () => {
    const latest = [
      { id: 'a1', status: 'fixed', patch: { find: 'x', replace: 'y' } },
    ];
    const anns = [
      {
        id: 'a1',
        matched_text: 'm',
        status: 'fixed',
        patch: { find: 'x', replace: 'y' },
      },
    ];
    expect(mergeAnnotationOverlays(anns as any, latest as any)).toBe(anns);
  });

  it('对抗：state 空列表 / 缺该 id / patch undefined vs null 视为相等', () => {
    expect(mergeAnnotationOverlays(propsAnns as any, [])).toBe(propsAnns);
    const latest = [{ id: 'ghost', status: 'resolved' }];
    const merged = mergeAnnotationOverlays(propsAnns as any, latest as any);
    expect(merged[0]).toBe(propsAnns[0]);
    // patch: undefined 与 null 值相等判定（(null?.find||')===(undefined?.find||')）
    const a = [{ id: 'a1', status: 'open', patch: undefined }];
    const b = [{ id: 'a1', status: 'open', patch: null }];
    expect(mergeAnnotationOverlays(a as any, b as any)[0]).toBe(a[0]);
  });

  it('id 为数字/字符串混合时按 String 归一匹配', () => {
    const anns = [{ id: 123, matched_text: 'm', status: 'open' }];
    const latest = [{ id: '123', status: 'resolved' }];
    const merged = mergeAnnotationOverlays(anns as any, latest as any);
    expect(merged[0].status).toBe('resolved');
  });
});
