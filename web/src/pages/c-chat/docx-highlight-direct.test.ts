/**
 * highlightDocxRanges 段落哈希直定位通道（B端范本预览）：
 * 后端 compute_anchor_positions 附加 pIdx/pHash/aOcc/p_total 后，同形留白
 * 不再靠全文顺序分配 —— 按段落指纹精确到段、段内序号精确到第几个留白。
 */
import { fnvHash32x2, highlightDocxRanges } from './docx-highlight';

const COLOR = '#f59e0b';
// 测试段落均为普通空格，显式空白类与 \s 等价
const H = (paraText: string) => fnvHash32x2(paraText.replace(/\s+/g, ''));

function buildDoc(paragraphTexts: string[]): HTMLElement {
  const root = document.createElement('div');
  for (const t of paragraphTexts) {
    const p = document.createElement('p');
    p.textContent = t;
    root.appendChild(p);
  }
  document.body.appendChild(root);
  return root;
}

function markKeys(root: HTMLElement): string[] {
  return Array.from(root.querySelectorAll('mark[data-anchor-key]')).map(
    (m) => m.dataset.anchorKey!,
  );
}

afterEach(() => {
  document.body.innerHTML = '';
});

describe('highlightDocxRanges 段落哈希直定位', () => {
  it('唯一指纹段落：mark 落在指纹对应段落而非全文首处', () => {
    const root = buildDoc(['干扰段：＿＿＿', '投标人名称：＿＿＿']);
    const marked = highlightDocxRanges(root, [
      {
        text: '＿＿＿',
        key: 'k',
        color: COLOR,
        pHash: H('投标人名称：＿＿＿'),
        pIdx: 1,
        aOcc: 1,
        pTotal: 2,
      },
    ]);
    // 全文顺序匹配会命中第 0 段的干扰留白；直定位必须落在第 1 段
    expect(marked.has('k')).toBe(true);
    const mark = root.querySelector('mark[data-anchor-key="k"]')!;
    expect(root.querySelectorAll('p')[1].contains(mark)).toBe(true);
    expect(root.querySelectorAll('p')[0].querySelector('mark')).toBeNull();
  });

  it('同形留白同文段落 1:1 对齐 + 段内 aOcc 区分多个留白', () => {
    const para = '甲：＿＿＿ 电话：＿＿＿';
    const root = buildDoc([para, para, '独立文本']);
    const marked = highlightDocxRanges(
      root,
      [
        {
          text: '＿＿＿',
          key: 'name1',
          color: COLOR,
          pIdx: 0,
          aOcc: 1,
          pTotal: 3,
          pHash: H(para),
        },
        {
          text: '＿＿＿',
          key: 'phone1',
          color: COLOR,
          pIdx: 0,
          aOcc: 2,
          pTotal: 3,
          pHash: H(para),
        },
        {
          text: '＿＿＿',
          key: 'name2',
          color: COLOR,
          pIdx: 1,
          aOcc: 1,
          pTotal: 3,
          pHash: H(para),
        },
        {
          text: '＿＿＿',
          key: 'phone2',
          color: COLOR,
          pIdx: 1,
          aOcc: 2,
          pTotal: 3,
          pHash: H(para),
        },
      ],
      { showKeyBadge: true },
    );
    expect(marked.size).toBe(4);
    // 文档序：段0 的 name1/phone1，段1 的 name2/phone2 —— 全文顺序分配会把
    // phone1 错标到段1，只有直定位能全部正确
    expect(markKeys(root)).toEqual(['name1', 'phone1', 'name2', 'phone2']);
    const marks = root.querySelectorAll('mark');
    expect(marks[0].textContent).toContain('{{name1}}');
    expect(marks[1].textContent).toContain('{{phone1}}');
    expect(marks[2].textContent).toContain('{{name2}}');
    expect(marks[3].textContent).toContain('{{phone2}}');
  });

  it('存在未注册同文段（计数不等）：p_idx 比例就近选段', () => {
    // 3 个同文段，只有第 2/3 个是注册项（pIdx 2/4，pTotal 5）
    const root = buildDoc(['＿＿＿', '前文', '＿＿＿', '后文', '＿＿＿']);
    const marked = highlightDocxRanges(root, [
      {
        text: '＿＿＿',
        key: 'm1',
        color: COLOR,
        pIdx: 2,
        aOcc: 1,
        pTotal: 5,
        pHash: H('＿＿＿'),
      },
      {
        text: '＿＿＿',
        key: 'm2',
        color: COLOR,
        pIdx: 4,
        aOcc: 1,
        pTotal: 5,
        pHash: H('＿＿＿'),
      },
    ]);
    expect(marked.size).toBe(2);
    const ps = root.querySelectorAll('p');
    expect(
      ps[2].contains(root.querySelector('mark[data-anchor-key="m1"]')),
    ).toBe(true);
    expect(
      ps[4].contains(root.querySelector('mark[data-anchor-key="m2"]')),
    ).toBe(true);
  });

  it('纯空白 anchor 直定位（raw 通道 aOcc）', () => {
    const blankDoc = '网址：            （必填）'; // 12 空格留白
    const root = buildDoc([blankDoc]);
    const marked = highlightDocxRanges(
      root,
      [
        {
          text: '            ',
          key: 'blank',
          color: COLOR,
          pHash: H(blankDoc),
          pIdx: 0,
          aOcc: 1,
          pTotal: 1,
        },
      ],
      { showKeyBadge: true },
    );
    expect(marked.has('blank')).toBe(true);
    const mark = root.querySelector('mark[data-anchor-key="blank"]')!;
    expect(mark.childNodes[0].textContent).toBe('            ');
    expect(mark.querySelector('span')?.textContent).toBe('{{blank}}');
  });

  it('w:tab 渲染为 \\u00a0（canon 等长回退通道）：raw 精确失败仍定位到段内', () => {
    // docx-preview 将 w:tab 渲染为 \u00a0；后端 anchor 是 \t 形态
    const root = buildDoc(['安全目标：\u00a0        \u00a0。']);
    const marked = highlightDocxRanges(
      root,
      [
        {
          text: '\t        \t',
          key: 'tab',
          color: COLOR,
          pHash: H('安全目标：\t        \t。'),
          pIdx: 0,
          aOcc: 1,
          pTotal: 1,
        },
      ],
      { showKeyBadge: true },
    );
    expect(marked.has('tab')).toBe(true);
    const mark = root.querySelector('mark[data-anchor-key="tab"]')!;
    expect(mark.childNodes[0].textContent).toBe('\u00a0        \u00a0');
    expect(mark.querySelector('span')?.textContent).toBe('{{tab}}');
  });

  it('指纹未命中（渲染差异）：回退全文顺序匹配，行为不变', () => {
    const root = buildDoc(['目标：＿＿＿']);
    const marked = highlightDocxRanges(root, [
      {
        text: '＿＿＿',
        key: 'k',
        color: COLOR,
        pHash: '0'.repeat(16),
        pIdx: 0,
        aOcc: 1,
        pTotal: 1,
      },
    ]);
    expect(marked.has('k')).toBe(true);
    const mark = root.querySelector('mark[data-anchor-key="k"]')!;
    expect(root.querySelectorAll('p')[0].contains(mark)).toBe(true);
  });

  it('无直定位元数据（C端路径）：不进入直定位通道', () => {
    const root = buildDoc(['甲：＿＿＿', '乙：＿＿＿']);
    const marked = highlightDocxRanges(root, [
      { text: '＿＿＿', key: 'k', color: COLOR },
    ]);
    expect(marked.has('k')).toBe(true);
    // 老管线取全文首处
    expect(
      root.querySelectorAll('p')[0].contains(root.querySelector('mark')!),
    ).toBe(true);
  });

  it('aOcc 超出段内出现数：该项不命中不抛错（回退顺序匹配兜底）', () => {
    const root = buildDoc(['只有一个：＿＿＿']);
    const marked = highlightDocxRanges(root, [
      {
        text: '＿＿＿',
        key: 'k',
        color: COLOR,
        pHash: H('只有一个：＿＿＿'),
        pIdx: 0,
        aOcc: 3,
        pTotal: 1,
      },
    ]);
    // 直定位失败后回退老管线，仍标到首处
    expect(marked.has('k')).toBe(true);
  });
});

it('空白 anchor 落在更长空白 run 内：宽容回退仍段内定位（不再全文错位）', () => {
  // 事故形态：anchor 8 空格、渲染 run 12 空格——runExact（完整空白 run）
  // 与 canon 等长均失败，此前 return null 回退全文顺序匹配分到别的段落。
  // 修复后按非重叠 indexOf 回退口径就地命中。
  const blankDoc = '网址：            （必填）'; // 12 空格留白
  const root = buildDoc([blankDoc]);
  const marked = highlightDocxRanges(
    root,
    [
      {
        text: '        ', // 8 空格
        key: 'partial',
        color: COLOR,
        pHash: H(blankDoc),
        pIdx: 0,
        aOcc: 1,
        pTotal: 1,
      },
    ],
    { showKeyBadge: true },
  );
  expect(marked.has('partial')).toBe(true);
  const mark = root.querySelector('mark[data-anchor-key="partial"]')!;
  expect(mark.childNodes[0].textContent).toBe('        ');
});
