import { diffBlocks, splitIntoSegments } from './docx-diff';

const src = (index: number, text: string, type: any = 'paragraph') => ({
  index,
  text,
  type,
});

describe('diffBlocks', () => {
  it('无改动时 count 为 0', () => {
    const paragraphs = [src(0, '第一段'), src(1, '第二段')];
    const blocks = [
      { paraIndex: 0, kind: 'text' as const, text: '第一段' },
      { paraIndex: 1, kind: 'text' as const, text: '第二段' },
    ];
    expect(diffBlocks(blocks, paragraphs)).toMatchObject({ count: 0 });
  });

  it('改写段落文本 → edits', () => {
    const paragraphs = [src(0, '原文')];
    const blocks = [{ paraIndex: 0, kind: 'text' as const, text: '改后' }];
    const ops = diffBlocks(blocks, paragraphs);
    expect(ops).toMatchObject({
      count: 1,
      edits: [{ paraIndex: 0, newText: '改后' }],
    });
  });

  it('段落清空 → deletes', () => {
    const paragraphs = [src(0, '原文')];
    const blocks = [{ paraIndex: 0, kind: 'text' as const, text: '' }];
    expect(diffBlocks(blocks, paragraphs)).toMatchObject({
      count: 1,
      deletes: [0],
    });
  });

  it('整段被删（块消失）→ deletes', () => {
    const paragraphs = [src(0, 'A'), src(1, 'B')];
    const blocks = [{ paraIndex: 0, kind: 'text' as const, text: 'A' }];
    expect(diffBlocks(blocks, paragraphs)).toMatchObject({
      count: 1,
      deletes: [1],
    });
  });

  it('回车新段（无 paraIndex）插在中间 → inserts 锚定前段', () => {
    const paragraphs = [src(0, 'A'), src(1, 'B')];
    const blocks = [
      { paraIndex: 0, kind: 'text' as const, text: 'A' },
      { kind: 'text' as const, text: '新增' },
      { paraIndex: 1, kind: 'text' as const, text: 'B' },
    ];
    expect(diffBlocks(blocks, paragraphs)).toMatchObject({
      count: 1,
      inserts: [{ afterParaIndex: 0, newText: '新增' }],
    });
  });

  it('文档开头插入新段 → afterParaIndex 为 -1', () => {
    const paragraphs = [src(0, 'A')];
    const blocks = [
      { kind: 'text' as const, text: '头部新段' },
      { paraIndex: 0, kind: 'text' as const, text: 'A' },
    ];
    expect(diffBlocks(blocks, paragraphs)).toMatchObject({
      inserts: [{ afterParaIndex: -1, newText: '头部新段' }],
    });
  });

  it('并段：前段合并后段文本 + 后段块消失', () => {
    const paragraphs = [src(6, 'A'), src(7, 'B')];
    const blocks = [{ paraIndex: 6, kind: 'text' as const, text: 'A B' }];
    const ops = diffBlocks(blocks, paragraphs);
    expect(ops).toMatchObject({
      count: 2,
      edits: [{ paraIndex: 6, newText: 'A B' }],
      deletes: [7],
    });
  });

  it('表格原子块消失 → error', () => {
    const paragraphs = [src(0, '文本'), src(1, '<table/>', 'table')];
    const blocks = [{ paraIndex: 0, kind: 'text' as const, text: '文本' }];
    expect(diffBlocks(blocks, paragraphs)).toMatchObject({
      error: expect.any(String),
    });
  });

  it('表格原样在场不参与 diff 也不误报', () => {
    const paragraphs = [src(0, '文本'), src(1, '<table/>', 'table')];
    const blocks = [
      { paraIndex: 0, kind: 'text' as const, text: '文本' },
      { paraIndex: 1, kind: 'table' as const, text: '' },
    ];
    const ops = diffBlocks(blocks, paragraphs);
    if ('error' in ops) throw new Error('不应返回 error');
    expect(ops.count).toBe(0);
  });

  it('空文档（全部被删）含表格时报 error', () => {
    const paragraphs = [src(0, '<table/>', 'table'), src(1, 'A')];
    const blocks: any[] = [];
    expect(diffBlocks(blocks, paragraphs)).toMatchObject({
      error: expect.any(String),
    });
  });
});

describe('splitIntoSegments', () => {
  it('无匹配 → 单个普通片段', () => {
    expect(splitIntoSegments('abc', [])).toEqual([{ text: 'abc' }]);
    expect(
      splitIntoSegments('abc', [{ text: 'x', color: '#f00', key: 'k' }]),
    ).toEqual([{ text: 'abc' }]);
  });

  it('单目标命中 → 三段，中间带 key/color', () => {
    expect(
      splitIntoSegments('abcdef', [{ text: 'cd', color: '#f00', key: 'k1' }]),
    ).toEqual([
      { text: 'ab' },
      { text: 'cd', key: 'k1', color: '#f00' },
      { text: 'ef' },
    ]);
  });

  it('多次出现全部包裹', () => {
    const segs = splitIntoSegments('xx-xx', [
      { text: 'xx', color: '#f00', key: 'k' },
    ]);
    expect(segs).toEqual([
      { text: 'xx', key: 'k', color: '#f00' },
      { text: '-' },
      { text: 'xx', key: 'k', color: '#f00' },
    ]);
  });

  it('多目标按顺序套用，已带 key 的段不再被拆', () => {
    const segs = splitIntoSegments('abcdef', [
      { text: 'bc', color: '#f00', key: 'k1' },
      { text: 'cde', color: '#0f0', key: 'k2' },
    ]);
    expect(segs).toEqual([
      { text: 'a' },
      { text: 'bc', key: 'k1', color: '#f00' },
      { text: 'de' },
      { text: 'f' },
    ]);
  });
});
