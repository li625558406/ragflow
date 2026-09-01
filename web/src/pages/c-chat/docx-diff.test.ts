import {
  diffBlocks,
  splitIntoSegments,
  type DocxSourceParagraph,
  type EditorBlock,
} from './docx-diff';

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

describe('diffBlocks 格式（runs/fmtSig）', () => {
  const para = (index: number, text: string, type: any = 'paragraph') => ({
    index,
    text,
    type,
  });

  it('纯改格式（文本同、fmtSig 变）→ edit 且带 runs', () => {
    const paragraphs = [para(0, '原文')];
    const runs = [{ text: '原文', bold: true }];
    const blocks = [
      {
        paraIndex: 0,
        kind: 'text' as const,
        text: '原文',
        runs,
        fmtSig: '[{"bold":true}]',
      },
    ];
    const ops = diffBlocks(blocks, paragraphs);
    expect(ops).toMatchObject({
      count: 1,
      edits: [{ paraIndex: 0, newText: '原文', runs }],
    });
  });

  it('无格式的块（无 runs/fmtSig）行为与旧版一致（不产生 edit）', () => {
    const paragraphs = [para(0, '原文')];
    const blocks = [{ paraIndex: 0, kind: 'text' as const, text: '原文' }];
    const ops = diffBlocks(blocks, paragraphs);
    if ('error' in ops) throw new Error('不应返回 error');
    expect(ops.count).toBe(0);
  });

  it('文本+格式同时变化 → edit 带 runs', () => {
    const paragraphs = [para(0, '原文')];
    const runs = [{ text: '改后文字', color: '#FF0000' }];
    const blocks = [
      {
        paraIndex: 0,
        kind: 'text' as const,
        text: '改后文字',
        runs,
        fmtSig: '[{"color":"#FF0000"}]',
      },
    ];
    const ops = diffBlocks(blocks, paragraphs);
    expect(ops).toMatchObject({
      count: 1,
      edits: [{ paraIndex: 0, newText: '改后文字', runs }],
    });
  });

  it('insert 带 runs 原样透传', () => {
    const paragraphs = [para(0, 'A')];
    const runs = [{ text: '新增段', size: 14 }];
    const blocks = [
      { paraIndex: 0, kind: 'text' as const, text: 'A' },
      { kind: 'text' as const, text: '新增段', runs, fmtSig: '[{"size":14}]' },
    ];
    const ops = diffBlocks(blocks, paragraphs);
    expect(ops).toMatchObject({
      inserts: [{ afterParaIndex: 0, newText: '新增段', runs }],
    });
  });
});

describe('diffBlocks 纯格式分支边界', () => {
  const para = (index: number, text: string, type: any = 'paragraph') => ({
    index,
    text,
    type,
  });

  it("fmtSig='[]'（空 runs 签名）不产生 edit", () => {
    const paragraphs = [para(0, '原文')];
    const blocks = [
      {
        paraIndex: 0,
        kind: 'text' as const,
        text: '原文',
        runs: [],
        fmtSig: '[]',
      },
    ];
    const ops = diffBlocks(blocks, paragraphs);
    if ('error' in ops) throw new Error(ops.error);
    expect(ops.count).toBe(0);
    expect(ops.edits).toHaveLength(0);
  });

  it('有 fmtSig 但缺 runs（不一致输入）→ 退化为不产生 edit（防静默格式丢失）', () => {
    const paragraphs = [para(0, '原文')];
    const blocks = [
      {
        paraIndex: 0,
        kind: 'text' as const,
        text: '原文',
        fmtSig: '[{"bold":true}]',
      },
    ];
    const ops = diffBlocks(blocks, paragraphs);
    if ('error' in ops) throw new Error(ops.error);
    expect(ops.count).toBe(0);
    expect(ops.edits).toHaveLength(0);
  });
});

describe('diffBlocks 块级属性（对齐/缩进/标题级别）', () => {
  const para = (
    index: number,
    text: string,
    type: any = 'paragraph',
    heading_level?: number,
  ) => ({ index, text, type, heading_level });

  it('纯改对齐（文本/run 均未变）→ edit 只带 align', () => {
    const paragraphs = [para(0, '正文内容')];
    const blocks = [
      {
        paraIndex: 0,
        kind: 'text' as const,
        text: '正文内容',
        align: 'center',
        indent: 0,
        headingLevel: null,
      },
    ];
    const ops = diffBlocks(blocks, paragraphs);
    if ('error' in ops) throw new Error(ops.error);
    expect(ops.count).toBe(1);
    expect(ops.edits[0]).toMatchObject({ paraIndex: 0, align: 'center' });
    expect(ops.edits[0].runs).toBeUndefined();
    expect(ops.edits[0].indent).toBeUndefined();
    expect(ops.edits[0].headingLevel).toBeUndefined();
  });

  it('纯改缩进 → edit 只带 indent', () => {
    const paragraphs = [para(0, '正文内容')];
    const blocks = [
      {
        paraIndex: 0,
        kind: 'text' as const,
        text: '正文内容',
        align: '',
        indent: 2,
        headingLevel: null,
      },
    ];
    const ops = diffBlocks(blocks, paragraphs);
    if ('error' in ops) throw new Error(ops.error);
    expect(ops.edits[0]).toMatchObject({ paraIndex: 0, indent: 2 });
  });

  it('正文 → 标题 2（headingLevel 1）→ edit 带 headingLevel', () => {
    const paragraphs = [para(0, '正文内容')];
    const blocks = [
      {
        paraIndex: 0,
        kind: 'text' as const,
        text: '正文内容',
        align: '',
        indent: 0,
        headingLevel: 1,
      },
    ];
    const ops = diffBlocks(blocks, paragraphs);
    if ('error' in ops) throw new Error(ops.error);
    expect(ops.edits[0]).toMatchObject({ paraIndex: 0, headingLevel: 1 });
  });

  it('标题 → 正文（headingLevel null）→ edit 带 headingLevel null', () => {
    const paragraphs = [para(0, '标题文字', 'heading', 1)];
    const blocks = [
      {
        paraIndex: 0,
        kind: 'text' as const,
        text: '标题文字',
        align: '',
        indent: 0,
        headingLevel: null,
      },
    ];
    const ops = diffBlocks(blocks, paragraphs);
    if ('error' in ops) throw new Error(ops.error);
    expect(ops.edits[0]).toMatchObject({ paraIndex: 0, headingLevel: null });
  });

  it('原标题段未动（headingLevel 与基线一致）→ 不产生 edit', () => {
    const paragraphs = [para(0, '标题文字', 'heading', 1)];
    const blocks = [
      {
        paraIndex: 0,
        kind: 'text' as const,
        text: '标题文字',
        align: '',
        indent: 0,
        headingLevel: 1,
      },
    ];
    const ops = diffBlocks(blocks, paragraphs);
    if ('error' in ops) throw new Error(ops.error);
    expect(ops.count).toBe(0);
  });

  it('heading_level>3 源段灌入降级 h3（基线 clamp 后一致）→ 不产生 edit', () => {
    const paragraphs = [para(0, '深级标题', 'heading', 5)];
    const blocks = [
      {
        paraIndex: 0,
        kind: 'text' as const,
        text: '深级标题',
        align: '',
        indent: 0,
        headingLevel: 2,
      },
    ];
    const ops = diffBlocks(blocks, paragraphs);
    if ('error' in ops) throw new Error(ops.error);
    expect(ops.count).toBe(0);
  });

  it('文本变化 + 对齐变化合并为同一条 edit', () => {
    const paragraphs = [para(0, '原文')];
    const blocks = [
      {
        paraIndex: 0,
        kind: 'text' as const,
        text: '改后文本',
        align: 'right',
        indent: 0,
        headingLevel: null,
      },
    ];
    const ops = diffBlocks(blocks, paragraphs);
    if ('error' in ops) throw new Error(ops.error);
    expect(ops.count).toBe(1);
    expect(ops.edits[0]).toMatchObject({
      paraIndex: 0,
      newText: '改后文本',
      align: 'right',
    });
  });

  it('块级属性未变的段（基线对齐态）不产生 edit', () => {
    const paragraphs = [para(0, '正文内容')];
    const blocks = [
      {
        paraIndex: 0,
        kind: 'text' as const,
        text: '正文内容',
        align: '',
        indent: 0,
        headingLevel: null,
      },
    ];
    const ops = diffBlocks(blocks, paragraphs);
    if ('error' in ops) throw new Error(ops.error);
    expect(ops.count).toBe(0);
  });

  it('新增段（无 paraIndex）带块级属性透传到 inserts', () => {
    const paragraphs = [para(0, 'A')];
    const blocks = [
      { paraIndex: 0, kind: 'text' as const, text: 'A' },
      {
        kind: 'text' as const,
        text: '新标题',
        align: '',
        indent: 0,
        headingLevel: 2,
      },
    ];
    const ops = diffBlocks(blocks, paragraphs);
    if ('error' in ops) throw new Error(ops.error);
    expect(ops.inserts[0]).toMatchObject({
      afterParaIndex: 0,
      newText: '新标题',
      headingLevel: 2,
    });
  });

  it('headingLevel undefined（旧调用方）视作与基线一致 → 不产生 edit', () => {
    const paragraphs = [para(0, '正文内容')];
    const blocks = [{ paraIndex: 0, kind: 'text' as const, text: '正文内容' }];
    const ops = diffBlocks(blocks, paragraphs);
    if ('error' in ops) throw new Error(ops.error);
    expect(ops.count).toBe(0);
  });
});

// ── 表格单元格 diff ──
const tablePara: DocxSourceParagraph = {
  index: 3,
  text: '<table><tr><td>甲</td><td>乙</td></tr></table>',
  type: 'table',
};
const tableBaseline = new Map([
  [
    3,
    [
      { row: 0, col: 0, colSpan: 1, header: false, text: '甲' },
      { row: 0, col: 1, colSpan: 1, header: false, text: '乙' },
    ],
  ],
]);

function cellBlock(row: number, col: number, text: string): EditorBlock {
  return { paraIndex: 3, kind: 'table', cell: { row, col }, text };
}

describe('diffBlocks 表格', () => {
  it('格文本变 → tableEdits；未变的格不产生 op', () => {
    const ops = diffBlocks(
      [cellBlock(0, 0, '甲改'), cellBlock(0, 1, '乙')],
      [tablePara],
      tableBaseline,
    );
    expect('error' in ops).toBe(false);
    if (!('error' in ops)) {
      expect(ops.tableEdits).toEqual([
        { paraIndex: 3, row: 0, col: 0, newText: '甲改' },
      ]);
      expect(ops.count).toBe(1);
    }
  });

  it('清空单元格 → newText 空串合法（不是 delete）', () => {
    const ops = diffBlocks(
      [cellBlock(0, 0, ''), cellBlock(0, 1, '乙')],
      [tablePara],
      tableBaseline,
    );
    expect('error' in ops).toBe(false);
    if (!('error' in ops)) {
      expect(ops.tableEdits).toEqual([
        { paraIndex: 3, row: 0, col: 0, newText: '' },
      ]);
      expect(ops.deletes).toEqual([]);
    }
  });

  it('纯改格式：文本同、runs 签名变 → 带 runs 的 tableEdit', () => {
    const b = {
      ...cellBlock(0, 0, '甲'),
      runs: [{ text: '甲', bold: true }],
      fmtSig: '[{"bold":true}]',
    };
    const ops = diffBlocks(
      [b, cellBlock(0, 1, '乙')],
      [tablePara],
      tableBaseline,
    );
    expect('error' in ops).toBe(false);
    if (!('error' in ops)) {
      expect(ops.tableEdits).toEqual([
        {
          paraIndex: 3,
          row: 0,
          col: 0,
          newText: '甲',
          runs: [{ text: '甲', bold: true }],
        },
      ]);
    }
  });

  it('文本与格式同时变 → runs 携带（对齐正文段 edits 语义）', () => {
    const b = {
      ...cellBlock(0, 0, '甲改'),
      runs: [{ text: '甲改', bold: true }],
      fmtSig: '[{"bold":true}]',
    };
    const ops = diffBlocks(
      [b, cellBlock(0, 1, '乙')],
      [tablePara],
      tableBaseline,
    );
    expect('error' in ops).toBe(false);
    if (!('error' in ops)) {
      expect(ops.tableEdits).toEqual([
        {
          paraIndex: 3,
          row: 0,
          col: 0,
          newText: '甲改',
          runs: [{ text: '甲改', bold: true }],
        },
      ]);
    }
  });

  it('基线 text 带首尾空格 → 对称 trim 不产生幻影 edit', () => {
    const baseline: typeof tableBaseline = new Map([
      [
        3,
        [
          { row: 0, col: 0, colSpan: 1, header: false, text: ' 甲 ' },
          { row: 0, col: 1, colSpan: 1, header: false, text: '乙' },
        ],
      ],
    ]);
    const ops = diffBlocks(
      [cellBlock(0, 0, '甲'), cellBlock(0, 1, '乙')],
      [tablePara],
      baseline,
    );
    expect('error' in ops).toBe(false);
    if (!('error' in ops)) expect(ops.count).toBe(0);
  });

  it('基线缺失（解析失败）→ 该表不产生任何改动（保护）', () => {
    const ops = diffBlocks([cellBlock(0, 0, '甲改')], [tablePara], new Map());
    expect('error' in ops).toBe(false);
    if (!('error' in ops)) expect(ops.count).toBe(0);
  });

  it('网格错位（基线无此格位）→ 跳过该格', () => {
    const ops = diffBlocks(
      [cellBlock(5, 5, '错位')],
      [tablePara],
      tableBaseline,
    );
    expect('error' in ops).toBe(false);
    if (!('error' in ops)) expect(ops.count).toBe(0);
  });

  it('删除整个表格（table 块消失）→ error 保护不变', () => {
    const ops = diffBlocks(
      [{ paraIndex: 0, kind: 'text', text: '只有正文' }],
      [tablePara],
      tableBaseline,
    );
    expect('error' in ops).toBe(true);
  });

  it('正文段落与表格格改动混合出现', () => {
    const ops = diffBlocks(
      [
        { paraIndex: 0, kind: 'text', text: '正文改' },
        cellBlock(0, 0, '甲改'),
        cellBlock(0, 1, '乙'),
      ],
      [{ index: 0, text: '正文', type: 'paragraph' }, tablePara],
      tableBaseline,
    );
    expect('error' in ops).toBe(false);
    if (!('error' in ops)) {
      expect(ops.edits).toHaveLength(1);
      expect(ops.tableEdits).toHaveLength(1);
      expect(ops.count).toBe(2);
    }
  });

  it('不传基线参数（旧调用方）→ 表格格直接跳过、不报错', () => {
    const ops = diffBlocks(
      [cellBlock(0, 0, '甲改'), cellBlock(0, 1, '乙')],
      [tablePara],
    );
    expect('error' in ops).toBe(false);
    if (!('error' in ops)) expect(ops.count).toBe(0);
  });
});
