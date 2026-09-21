/**
 * highlightDocxRanges 跨行序列通道（C端审核跨行摘录）：
 * LLM 批注 matched_text 常为多行拼接（含 \n），单段匹配必然失配且被同段
 * 校验拒绝 —— lines 分行后在连续段落序列上滑窗，唯命中才插 mark，各行
 * mark 共用同一 data-anchor-key。
 */
import { highlightDocxRanges } from './docx-highlight';

const COLOR = '#f59e0b';

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

describe('highlightDocxRanges 跨行序列通道', () => {
  it('唯一命中：窗口内逐行插 mark，各行共用同一 key', () => {
    const root = buildDoc([
      '前文段落，与批注无关。',
      '3.1.1 构成投标文件的其他资料',
      '（1）投标函附录',
      '（2）法定代表人身份证明',
      '后文段落。',
    ]);
    const marked = highlightDocxRanges(root, [
      {
        text: '3.1.1 构成投标文件的其他资料',
        lines: [
          '3.1.1 构成投标文件的其他资料',
          '（1）投标函附录',
          '（2）法定代表人身份证明',
        ],
        key: 'k1',
        color: COLOR,
      },
    ]);
    expect(marked.has('k1')).toBe(true);
    const marks = root.querySelectorAll('mark[data-anchor-key="k1"]');
    expect(marks.length).toBe(3);
    const ps = root.querySelectorAll('p');
    expect(ps[1].contains(marks[0])).toBe(true);
    expect(ps[2].contains(marks[1])).toBe(true);
    expect(ps[3].contains(marks[2])).toBe(true);
    expect(marks[0].textContent).toContain('构成投标文件的其他资料');
    expect(marks[2].textContent).toBe('（2）法定代表人身份证明');
  });

  it('多命中（重复文本序列）：序列通道拒绝，仅剩 text 首行兜底单段命中', () => {
    const dup = ['条目甲：内容一', '条目乙：内容二'];
    const root = buildDoc(['引言', ...dup, '间隔', ...dup]);
    const marked = highlightDocxRanges(root, [
      {
        text: '条目甲：内容一',
        lines: ['条目甲：内容一', '条目乙：内容二'],
        key: 'dup',
        color: COLOR,
      },
    ]);
    // 序列唯命中闸拒绝（两处重复）；首行「条目甲：内容一」本身也重复，
    // 兜底按常规通道取全文首处 —— 只标首行一处，不再逐行标
    expect(marked.has('dup')).toBe(true);
    const marks = root.querySelectorAll('mark[data-anchor-key="dup"]');
    expect(marks.length).toBe(1);
    expect(root.querySelectorAll('p')[1].contains(marks[0])).toBe(true);
  });

  it('空行过滤：lines 内空行/纯空白行被剔出序列（不要求文档存在空段）', () => {
    const root = buildDoc(['第一节 标题行', '第一节 正文内容']);
    const marked = highlightDocxRanges(root, [
      {
        text: '第一节 标题行',
        lines: ['第一节 标题行', '', '   ', '第一节 正文内容'],
        key: 'gap',
        color: COLOR,
      },
    ]);
    expect(marked.has('gap')).toBe(true);
    const marks = root.querySelectorAll('mark[data-anchor-key="gap"]');
    expect(marks.length).toBe(2);
    const ps = root.querySelectorAll('p');
    expect(ps[0].contains(marks[0])).toBe(true);
    expect(ps[1].contains(marks[1])).toBe(true);
  });

  it('序列要求连续段落：中间空段断裂则序列不命中（仅首行兜底）', () => {
    const root = buildDoc(['第一节 标题行', '', '   ', '第一节 正文内容']);
    const marked = highlightDocxRanges(root, [
      {
        text: '第一节 标题行',
        lines: ['第一节 标题行', '第一节 正文内容'],
        key: 'broken',
        color: COLOR,
      },
    ]);
    // 序列通道失败；text 首行兜底命中第 0 段
    expect(marked.has('broken')).toBe(true);
    const marks = root.querySelectorAll('mark[data-anchor-key="broken"]');
    expect(marks.length).toBe(1);
    expect(root.querySelectorAll('p')[0].contains(marks[0])).toBe(true);
  });

  it('单非空行 lines：不进序列通道，走 text 首行兜底常规匹配', () => {
    const root = buildDoc(['只有一行正文', '别的段落']);
    const marked = highlightDocxRanges(root, [
      {
        text: '只有一行正文',
        lines: ['只有一行正文', ''],
        key: 'single',
        color: COLOR,
      },
    ]);
    expect(marked.has('single')).toBe(true);
    const marks = root.querySelectorAll('mark[data-anchor-key="single"]');
    expect(marks.length).toBe(1);
    expect(root.querySelectorAll('p')[0].contains(marks[0])).toBe(true);
  });

  it('序列内容不存在于文档：跳过该 item，不抛错', () => {
    const root = buildDoc(['无关段落一', '无关段落二']);
    const marked = highlightDocxRanges(root, [
      {
        text: '不存在行一',
        lines: ['不存在行一', '不存在行二'],
        key: 'miss',
        color: COLOR,
      },
    ]);
    expect(marked.size).toBe(0);
  });

  it('页眉段落不参与序列匹配（存在正文段时排除 HF）', () => {
    const root = document.createElement('div');
    const header = document.createElement('header');
    const hp = document.createElement('p');
    hp.textContent = '页眉行';
    header.appendChild(hp);
    root.appendChild(header);
    for (const t of ['正文行一', '正文行二']) {
      const p = document.createElement('p');
      p.textContent = t;
      root.appendChild(p);
    }
    document.body.appendChild(root);
    const marked = highlightDocxRanges(root, [
      {
        text: '正文行一',
        lines: ['页眉行', '正文行一'],
        key: 'hf',
        color: COLOR,
      },
    ]);
    // 「页眉行+正文行一」序列跨 HF 边界：排除 HF 后序列不命中，
    // 仅剩 text 首行「正文行一」兜底单段命中
    expect(marked.has('hf')).toBe(true);
    const marks = root.querySelectorAll('mark[data-anchor-key="hf"]');
    expect(marks.length).toBe(1);
    expect(root.querySelectorAll('p')[1].contains(marks[0])).toBe(true);
  });

  it('页眉页脚全被排除时仍可命中纯正文序列', () => {
    const root = document.createElement('div');
    const header = document.createElement('header');
    const hp = document.createElement('p');
    hp.textContent = '页眉行';
    header.appendChild(hp);
    root.appendChild(header);
    for (const t of ['正文行一', '正文行二']) {
      const p = document.createElement('p');
      p.textContent = t;
      root.appendChild(p);
    }
    document.body.appendChild(root);
    const marked = highlightDocxRanges(root, [
      {
        text: '正文行一',
        lines: ['正文行一', '正文行二'],
        key: 'body',
        color: COLOR,
      },
    ]);
    expect(marked.has('body')).toBe(true);
    const marks = root.querySelectorAll('mark[data-anchor-key="body"]');
    expect(marks.length).toBe(2);
  });
});
