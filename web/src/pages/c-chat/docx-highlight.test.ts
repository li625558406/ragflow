/**
 * highlightDocxRanges 对抗性测试：
 * 重点覆盖同形 anchor（同串留白）多项的顺序分配（occ 语义）与既有首处匹配回归。
 */
import { highlightDocxRanges } from './docx-highlight';

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

afterEach(() => {
  document.body.innerHTML = '';
});

describe('highlightDocxRanges 同形 anchor 顺序分配', () => {
  it('同形文本多项按 addr 文档序分配第 1/2/3 次出现', () => {
    const root = buildDoc(['姓名：＿＿＿ 电话：＿＿＿ 邮箱：＿＿＿']);
    const marked = highlightDocxRanges(root, [
      { text: '＿＿＿', key: 'email', color: '#f59e0b', addr: 'para:0' },
      { text: '＿＿＿', key: 'name', color: '#f59e0b', addr: 'para:0' },
      { text: '＿＿＿', key: 'phone', color: '#f59e0b', addr: 'para:0' },
    ]);
    // addr 相同退化为输入顺序：email→1st, name→2nd, phone→3rd
    expect(marked.size).toBe(3);
    const keys = Array.from(root.querySelectorAll('mark[data-anchor-key]')).map(
      (m) => m.dataset.anchorKey,
    );
    expect(keys).toEqual(['email', 'name', 'phone']);
  });

  it('段落末尾 anchor（留白串收尾）：同段校验不再误判跨段', () => {
    const root = buildDoc(['投标人：＿＿＿', '日期：＿＿＿']);
    const marked = highlightDocxRanges(root, [
      { text: '＿＿＿', key: 't', color: '#f59e0b', addr: 'para:0' },
      { text: '＿＿＿', key: 'd', color: '#f59e0b', addr: 'para:1' },
    ]);
    expect(marked.size).toBe(2);
    const keys = Array.from(root.querySelectorAll('mark[data-anchor-key]')).map(
      (m) => m.dataset.anchorKey,
    );
    expect(keys).toEqual(['t', 'd']);
    // 第一处 mark 内容保留完整 anchor 文本
    expect(root.querySelectorAll('mark')[0].textContent).toBe('＿＿＿');
  });

  it('同形文本多项按 addr 数值序跨段分配（para:10 不排在 para:9 前）', () => {
    const paras = Array.from({ length: 11 }, (_, i) => `第${i}段：＿＿＿`);
    const root = buildDoc(paras);
    const marked = highlightDocxRanges(root, [
      { text: '＿＿＿', key: 'b', color: '#f59e0b', addr: 'para:10' },
      { text: '＿＿＿', key: 'a', color: '#f59e0b', addr: 'para:9' },
    ]);
    expect(marked.size).toBe(2);
    const keys = Array.from(root.querySelectorAll('mark[data-anchor-key]')).map(
      (m) => m.dataset.anchorKey,
    );
    expect(keys).toEqual(['a', 'b']);
  });

  it('注册项多于文档出现次数：多余项不命中不抛错', () => {
    const root = buildDoc(['只有一处：＿＿＿']);
    const marked = highlightDocxRanges(root, [
      { text: '＿＿＿', key: 'k1', color: '#f59e0b', addr: 'para:0' },
      { text: '＿＿＿', key: 'k2', color: '#f59e0b', addr: 'para:0' },
      { text: '＿＿＿', key: 'k3', color: '#f59e0b', addr: 'para:0' },
    ]);
    expect(marked.size).toBe(1);
    expect(root.querySelectorAll('mark').length).toBe(1);
  });

  it('单项组回归：仍取全文首处，start 提示生效', () => {
    const root = buildDoc(['甲：×××', '乙：×××']);
    const marked = highlightDocxRanges(root, [
      { text: '×××', key: 'k', color: '#f59e0b' },
    ]);
    expect(marked.has('k')).toBe(true);
    const mark = root.querySelector('mark')!;
    expect(mark.textContent).toBe('×××');
    // 第一处（甲段）被包进 mark，mark 位于第一个 p 内
    expect(root.querySelectorAll('p')[0].contains(mark)).toBe(true);
  });

  it('同 key 重复项只锚一处', () => {
    const root = buildDoc(['＿＿＿ ＿＿＿']);
    const marked = highlightDocxRanges(root, [
      { text: '＿＿＿', key: 'dup', color: '#f59e0b', addr: 'para:0' },
      { text: '＿＿＿', key: 'dup', color: '#f59e0b', addr: 'para:0' },
    ]);
    expect(marked.size).toBe(1);
    expect(root.querySelectorAll('mark').length).toBe(1);
  });

  it('跨段文本不插 mark（防块结构破坏）', () => {
    // 段1结尾+段2开头拼出的归一化匹配必须被同段校验拦下
    const root = buildDoc(['结尾ＡＢ', 'ＣＤ开头']);
    const marked = highlightDocxRanges(root, [
      { text: 'ＡＢＣＤ', key: 'cross', color: '#f59e0b' },
    ]);
    expect(marked.size).toBe(0);
    expect(root.querySelectorAll('mark').length).toBe(0);
  });

  it('anchor 归一化空白后不足 2 字符的项跳过', () => {
    const root = buildDoc(['a b c']);
    const marked = highlightDocxRanges(root, [
      { text: ' ', key: 'blank', color: '#f59e0b' },
    ]);
    expect(marked.size).toBe(0);
  });

  it('空 items / 空容器安全返回', () => {
    const root = buildDoc(['内容']);
    expect(highlightDocxRanges(root, []).size).toBe(0);
    const empty = document.createElement('div');
    expect(
      highlightDocxRanges(empty, [{ text: '内容', key: 'k', color: '#f59e0b' }])
        .size,
    ).toBe(0);
  });

  it('showKeyBadge：mark 末尾追加 {{key}} 徽标，不含 key 的项不追加', () => {
    const root = buildDoc(['姓名：＿＿＿']);
    highlightDocxRanges(
      root,
      [
        { text: '＿＿＿', key: 'name', color: '#f59e0b' },
        { text: '姓名', key: '', color: '#f59e0b' },
      ],
      { showKeyBadge: true },
    );
    const badge = root.querySelector('mark[data-anchor-key="name"] span');
    expect(badge?.textContent).toBe('{{name}}');
    // 空 key 项不加徽标（也不应有任何其他 {{}} 残留）
    const badges = Array.from(root.querySelectorAll('mark span'));
    expect(badges.length).toBe(1);
  });

  it('不带 showKeyBadge（C端路径回归）：不产生徽标', () => {
    const root = buildDoc(['＿＿＿']);
    highlightDocxRanges(root, [{ text: '＿＿＿', key: 'k', color: '#f59e0b' }]);
    expect(root.querySelector('mark span')).toBeNull();
  });

  it('混合：同形组与独形组互不干扰', () => {
    const root = buildDoc(['项目：＿＿＿ 金额：＿＿＿', '备注：独特文本锚点']);
    const marked = highlightDocxRanges(root, [
      { text: '＿＿＿', key: 'project', color: '#f59e0b', addr: 'para:0' },
      { text: '＿＿＿', key: 'amount', color: '#f59e0b', addr: 'para:0' },
      { text: '独特文本锚点', key: 'remark', color: '#f59e0b', addr: 'para:1' },
    ]);
    expect(marked.size).toBe(3);
    const keys = Array.from(root.querySelectorAll('mark[data-anchor-key]')).map(
      (m) => m.dataset.anchorKey,
    );
    expect(keys).toEqual(['project', 'amount', 'remark']);
  });
});
