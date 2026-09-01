# 文件审核弹框表格可编辑 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** C端文件审核弹框（Lexical 编辑器）中表格单元格可改字、可改 run 级格式，批注高亮共存，保存走单元格级 `table_edits` 契约，docx 原表样式零破坏。

**Architecture:** 把表格从只读 `AtomicBlockNode`（HTML 直渲）换成 `@lexical/table` 的 TableNode 体系（新子类 `DocxTableNode` 携带 paraIndex），格内即普通 Lexical 段落 → 编辑/工具栏/undo/高亮全部复用现有机制。diff 新增 `table_edits` 操作（单元格级），后端 python-docx `table.cell(r,c)` 改写首段、清空多余段。基线（row/col/text）由前端用同一 HTML 解析器从初始灌入的表格 HTML 派生，与灌入同源、零后端改动。

**Tech Stack:** `lexical@0.23.1` + `@lexical/table@0.23.1` + `@lexical/react@0.23.1`（`TablePlugin` 已确认导出）、Jest+jsdom（前端纯函数单测）、python-docx（后端，pytest 直测纯 helper）。

**Spec:** `docs/superpowers/specs/2026-09-01-table-editable-design.md`

**关键事实（实现者必读）：**
- 表格 HTML 由 `rag/app/naive.py` 生成：`<table>[<caption>…</caption>]<tr><td[ colspan='n']>纯文本</td>…</tr>…</table>`。**只有 `<td>` 没有 `<th>`**；colspan 来自「相邻同文本」启发式（近似 docx gridSpan）；单元格文本是 python-docx `cell.text`（多段落以 `\n` 连接）；**永远不会出现 rowspan 与嵌套表格 HTML**，但解析器仍按 spec 容错。
- 现有契约：前端 `DocxRun`（`docx-format-utils.ts`）↔ 后端 `_parse_runs`（`flow_app.py:389`）；diff 纯函数在 `docx-diff.ts`（零依赖可单测）；后端 `_build_para_map`（`flow_app.py:354`）把表格段存为 `("table", None)`——本计划改为存 DocxTable 实例。
- 前端测试与源码同目录（`docx-diff.test.ts` 等），命令 `cd web && npx jest <file> --coverage=false`；后端 `uv run pytest test/<file> -v`（Ruff line-length=200）。
- 提交纪律：每个 Task 一次 commit，消息用本仓风格（中文，feat/refactor/test 前缀）。

---

### Task 1: `docx-table-utils.ts` — 表格 HTML 解析（基线与灌入共用）

**Files:**
- Create: `web/src/pages/c-chat/docx-table-utils.ts`
- Test: `web/src/pages/c-chat/docx-table-utils.test.ts`

- [ ] **Step 1: 写失败测试**

```ts
// web/src/pages/c-chat/docx-table-utils.test.ts
/** @jest-environment jsdom */
import { parseTableCells } from './docx-table-utils';

describe('parseTableCells', () => {
  it('解析普通 2x2 表格', () => {
    const html = "<table><tr><td>甲</td><td>乙</td></tr><tr><td>丙</td><td>丁</td></tr></table>";
    expect(parseTableCells(html)).toEqual([
      { row: 0, col: 0, colSpan: 1, header: false, text: '甲' },
      { row: 0, col: 1, colSpan: 1, header: false, text: '乙' },
      { row: 1, col: 0, colSpan: 1, header: false, text: '丙' },
      { row: 1, col: 1, colSpan: 1, header: false, text: '丁' },
    ]);
  });

  it('colspan 占据多个网格列（被覆盖格位不产生独立格）', () => {
    const html = "<table><tr><td colspan='2'>跨列</td><td>右</td></tr></table>";
    expect(parseTableCells(html)).toEqual([
      { row: 0, col: 0, colSpan: 2, header: false, text: '跨列' },
      { row: 0, col: 2, colSpan: 1, header: false, text: '右' },
    ]);
  });

  it('忽略 caption；th 记 header=true（鲁棒性，naive.py 不产出 th）', () => {
    const html =
      "<table><caption>Table Location: 第一章</caption><tr><th>表头</th></tr></table>";
    const cells = parseTableCells(html);
    expect(cells).toEqual([
      { row: 0, col: 0, colSpan: 1, header: true, text: '表头' },
    ]);
  });

  it('单元格内 \\n 保留（python-docx cell.text 多段落以 \\n 连接）', () => {
    const html = "<table><tr><td>第一段\n第二段</td></tr></table>";
    expect(parseTableCells(html)[0].text).toBe('第一段\n第二段');
  });

  it('嵌套 table 文本被忽略、&nbsp; 归一为空格（对抗性）', () => {
    const html =
      "<table><tr><td>外层<table><tr><td>内层</td></tr></table></td><td>a&nbsp;b</td></tr></table>";
    const cells = parseTableCells(html);
    expect(cells[0].text).toBe('外层');
    expect(cells[1].text).toBe('a b');
  });

  it('畸形输入：空串 / 无 table / 未闭合标签 / 非法 colspan / 超大 colspan', () => {
    expect(parseTableCells('')).toEqual([]);
    expect(parseTableCells('<div>not a table</div>')).toEqual([]);
    // DOMParser 容错未闭合标签，仍应解析出单元格
    expect(parseTableCells('<table><tr><td>未闭合')).toEqual([
      { row: 0, col: 0, colSpan: 1, header: false, text: '未闭合' },
    ]);
    expect(
      parseTableCells("<table><tr><td colspan='abc'>x</td></tr></table>")[0]
        .colSpan,
    ).toBe(1);
    expect(
      parseTableCells("<table><tr><td colspan='99999'>x</td></tr></table>")[0]
        .colSpan,
    ).toBe(50); // 封顶防炸
  });

  it('空单元格 text 为空串', () => {
    const html = "<table><tr><td></td><td>有字</td></tr></table>";
    expect(parseTableCells(html)[0].text).toBe('');
  });
});
```

- [ ] **Step 2: 运行确认失败**

Run: `cd web && npx jest src/pages/c-chat/docx-table-utils.test.ts --coverage=false`
Expected: FAIL（模块不存在）

- [ ] **Step 3: 实现**

```ts
// web/src/pages/c-chat/docx-table-utils.ts
// 表格 HTML → 逻辑网格单元格：初始灌入（docx-paragraph-editor）与 diff 基线
// （review-panel）共用同一解析器，保证「基线与灌入同源」。纯 DOM 函数，jsdom 可测。

export interface TableCellInfo {
  /** docx 逻辑网格坐标（colspan 展开后；与 python-docx table.cell(r,c) 对齐） */
  row: number;
  col: number;
  colSpan: number;
  /** th=true（naive.py 实际只产出 td，此处为鲁棒性） */
  header: boolean;
  /** 单元格纯文本，多段落以 \n 连接（与 python-docx cell.text 一致） */
  text: string;
}

const MAX_COLSPAN = 50;

/** 收集单元格文本：跳过嵌套 <table> 子树（spec：嵌套表格按纯文本忽略）、<br> 归 \n、&nbsp; 归空格 */
function cellText(td: Element): string {
  let s = '';
  const walk = (node: Node) => {
    if (node.nodeType === node.ELEMENT_NODE) {
      const el = node as Element;
      const tag = el.tagName.toLowerCase();
      if (tag === 'table') return;
      if (tag === 'br') {
        s += '\n';
        return;
      }
      el.childNodes.forEach(walk);
    } else if (node.nodeType === node.TEXT_NODE) {
      s += node.nodeValue || '';
    }
  };
  td.childNodes.forEach(walk);
  return s.replace(/\u00a0/g, ' ');
}

/** 解析表格 HTML → 单元格列表（caption 忽略；colspan 展开进 col 累加；
 * 非法/超大 colspan 封底 1 封顶 50；无 table 返回 []，畸形输入尽力解析） */
export function parseTableCells(html: string): TableCellInfo[] {
  if (!html) return [];
  const doc = new DOMParser().parseFromString(html, 'text/html');
  const table = doc.querySelector('table');
  if (!table) return [];
  const out: TableCellInfo[] = [];
  Array.from(table.querySelectorAll('tr')).forEach((tr, row) => {
    let col = 0;
    for (const td of Array.from(tr.children)) {
      const tag = td.tagName.toLowerCase();
      if (tag !== 'td' && tag !== 'th') continue;
      const n = parseInt(td.getAttribute('colspan') || '1', 10);
      const colSpan = Number.isFinite(n) ? Math.max(1, Math.min(MAX_COLSPAN, n)) : 1;
      out.push({ row, col, colSpan, header: tag === 'th', text: cellText(td) });
      col += colSpan;
    }
  });
  return out;
}
```

- [ ] **Step 4: 运行确认通过**

Run: `cd web && npx jest src/pages/c-chat/docx-table-utils.test.ts --coverage=false`
Expected: PASS（7 用例全绿）

- [ ] **Step 5: Commit**

```bash
git add web/src/pages/c-chat/docx-table-utils.ts web/src/pages/c-chat/docx-table-utils.test.ts
git commit -m "feat(flow): docx-table-utils 表格 HTML→逻辑网格解析（灌入与 diff 基线共用）"
```

---

### Task 2: `docx-diff.ts` — table_edits 契约

**Files:**
- Modify: `web/src/pages/c-chat/docx-diff.ts`
- Test: `web/src/pages/c-chat/docx-diff.test.ts`

- [ ] **Step 1: 写失败测试**（在 `docx-diff.test.ts` 末尾追加）

```ts
import { diffBlocks, type DocxSourceParagraph, type EditorBlock } from './docx-diff';

// ── 表格单元格 diff ──
const tablePara: DocxSourceParagraph = {
  index: 3,
  text: "<table><tr><td>甲</td><td>乙</td></tr></table>",
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
      [
        cellBlock(0, 0, '甲改'),
        cellBlock(0, 1, '乙'),
      ],
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
    const ops = diffBlocks([cellBlock(0, 0, ''), cellBlock(0, 1, '乙')], [tablePara], tableBaseline);
    expect('error' in ops).toBe(false);
    if (!('error' in ops)) {
      expect(ops.tableEdits).toEqual([{ paraIndex: 3, row: 0, col: 0, newText: '' }]);
      expect(ops.deletes).toEqual([]);
    }
  });

  it('纯改格式：文本同、runs 签名变 → 带 runs 的 tableEdit', () => {
    const b = { ...cellBlock(0, 0, '甲'), runs: [{ text: '甲', bold: true }], fmtSig: '[{"bold":true}]' };
    const ops = diffBlocks([b, cellBlock(0, 1, '乙')], [tablePara], tableBaseline);
    expect('error' in ops).toBe(false);
    if (!('error' in ops)) {
      expect(ops.tableEdits).toEqual([
        { paraIndex: 3, row: 0, col: 0, newText: '甲', runs: [{ text: '甲', bold: true }] },
      ]);
    }
  });

  it('基线缺失（解析失败）→ 该表不产生任何改动（保护）', () => {
    const ops = diffBlocks([cellBlock(0, 0, '甲改')], [tablePara], new Map());
    expect('error' in ops).toBe(false);
    if (!('error' in ops)) expect(ops.count).toBe(0);
  });

  it('网格错位（基线无此格位）→ 跳过该格', () => {
    const ops = diffBlocks([cellBlock(5, 5, '错位')], [tablePara], tableBaseline);
    expect('error' in ops).toBe(false);
    if (!('error' in ops)) expect(ops.count).toBe(0);
  });

  it('删除整个表格（table 块消失）→ error 保护不变', () => {
    const ops = diffBlocks([{ paraIndex: 0, kind: 'text', text: '只有正文' }], [tablePara], tableBaseline);
    expect('error' in ops).toBe(true);
  });

  it('正文段落与表格格改动混合出现', () => {
    const ops = diffBlocks(
      [
        { paraIndex: 0, kind: 'text', text: '正文改' },
        cellBlock(0, 0, '甲改'),
        cellBlock(0, 1, '乙'),
      ],
      [
        { index: 0, text: '正文', type: 'paragraph' },
        tablePara,
      ],
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
    const ops = diffBlocks([cellBlock(0, 0, '甲改'), cellBlock(0, 1, '乙')], [tablePara]);
    expect('error' in ops).toBe(false);
    if (!('error' in ops)) expect(ops.count).toBe(0);
  });
});
```

- [ ] **Step 2: 运行确认失败**

Run: `cd web && npx jest src/pages/c-chat/docx-diff.test.ts --coverage=false`
Expected: FAIL（`cell` 字段与 `tableEdits` 不存在 / TS 编译错误）

- [ ] **Step 3: 实现**（`docx-diff.ts` 四处修改）

3a. `EditorBlock` 增加字段（`kind` 注释同步更新）：

```ts
export interface EditorBlock {
  paraIndex?: number;
  kind: 'text' | 'table' | 'image';
  text: string;
  /** kind='table' 时的 docx 逻辑网格坐标（readEditorBlocks 按 colSpan 累加） */
  cell?: { row: number; col: number };
  runs?: DocxRun[];
  fmtSig?: string;
  align?: string;
  indent?: number;
  headingLevel?: number | null;
}
```

3b. `DocxDiffOps` 增加 `tableEdits`：

```ts
export type DocxDiffOps =
  | { error: string }
  | {
      error?: undefined;
      edits: Array<{/* 原样不动 */}>;
      deletes: number[];
      inserts: Array<{/* 原样不动 */}>;
      /** 单元格级改动：与 edits/deletes/inserts 并列，计入 count 与 200 上限 */
      tableEdits: Array<{
        paraIndex: number;
        row: number;
        col: number;
        /** 允许空串（清空单元格） */
        newText: string;
        runs?: DocxRun[];
      }>;
      count: number;
    };
```

3c. `diffBlocks` 签名与实现：

```ts
export interface BaselineCell {
  row: number;
  col: number;
  colSpan: number;
  header: boolean;
  text: string;
}

export function diffBlocks(
  blocks: EditorBlock[],
  paragraphs: DocxSourceParagraph[],
  /** 表格基线（review-panel 用 parseTableCells 从初始 HTML 解析，与灌入同源）；
   * 缺省/缺某表基线 → 该表改动全部跳过（保护，不报错） */
  tableBaselines?: Map<number, BaselineCell[]>,
): DocxDiffOps {
```

函数体内：`edits/deletes/inserts` 声明后追加 `const tableEdits: DocxDiffOps extends { error?: undefined } ? never : never` —— 不要用条件类型，直接写具体类型：

```ts
  const tableEdits: Array<{
    paraIndex: number;
    row: number;
    col: number;
    newText: string;
    runs?: DocxRun[];
  }> = [];
```

主循环 `if (b.paraIndex != null)` 分支内，在 `seen.add(idx); lastIdx = idx;` 之后、原 `if (b.kind !== 'text') continue;` 处替换为：

```ts
      if (b.kind === 'table' && b.cell) {
        // 单元格 diff：基线缺失/网格错位 → 跳过（保护）；空文本是清空格，不是 delete
        const base = tableBaselines?.get(idx);
        if (base) {
          const bc = base.find((x) => x.row === b.cell!.row && x.col === b.cell!.col);
          if (bc) {
            const text = b.text.trim();
            // 有有效 run 签名（非空、非空数组）即携带 runs：文本变或纯格式变都算改动，
            // 与正文段落 edits 行为一致（文本+格式同时变时格式不丢失）
            const hasRuns = !!(b.runs && b.fmtSig && b.fmtSig !== '[]');
            if (text !== bc.text.trim() || hasRuns) {
              tableEdits.push({
                paraIndex: idx,
                row: b.cell.row,
                col: b.cell.col,
                newText: text,
                ...(hasRuns ? { runs: b.runs } : {}),
              });
            }
          }
        }
        continue;
      }
      if (b.kind !== 'text') continue; // 图片原子块只记 seen
```

返回值（两处 return 都要改）：

```ts
  return {
    edits,
    deletes,
    inserts,
    tableEdits,
    count: edits.length + deletes.length + inserts.length + tableEdits.length,
  };
// error 分支保持 { error: '...' } 不带 tableEdits（现有调用方用 'error' in ops 判断）
```

注意：删除表格的 error 保护（函数尾部 `p.type === 'table' || p.type === 'image'` 循环）**原样保留**。

- [ ] **Step 4: 运行确认通过**

Run: `cd web && npx jest src/pages/c-chat/docx-diff.test.ts --coverage=false`
Expected: PASS（旧用例不回归 + 新表格用例全绿）

- [ ] **Step 5: Commit**

```bash
git add web/src/pages/c-chat/docx-diff.ts web/src/pages/c-chat/docx-diff.test.ts
git commit -m "feat(flow): diff 契约新增 tableEdits 单元格级操作（基线由调用方传入）"
```

---

### Task 3: `DocxTableNode` 节点 + 初始灌入

**Files:**
- Modify: `web/src/pages/c-chat/docx-paragraph-editor.tsx`

- [ ] **Step 1: 新增 import**

```ts
import {
  $createTableCellNode,
  $createTableRowNode,
  $isTableCellNode,
  TableCellHeaderStates,
  TableCellNode,
  TableNode,
  TableRowNode,
} from '@lexical/table';
import { parseTableCells } from './docx-table-utils';
```

（`$createTableNode` 不需要——直接 `new DocxTableNode()`。）

- [ ] **Step 2: 新增 DocxTableNode**（放在 AtomicBlockNode 之前）

```ts
/** 文档表格块（对应 source paragraph type=table）：继承 @lexical/table TableNode，
 * 携带 paraIndex 对齐原文段落号。行列结构由 TableRowNode/TableCellNode 表达，
 * 格内为普通段落 → 编辑/工具栏/undo/批注高亮全部复用正文机制。 */
export class DocxTableNode extends TableNode {
  __paraIndex: number | undefined;

  constructor(paraIndex?: number, key?: NodeKey) {
    super(key);
    this.__paraIndex = paraIndex;
  }

  static getType(): string {
    return 'docx-table';
  }

  static clone(node: DocxTableNode): DocxTableNode {
    return new DocxTableNode(node.__paraIndex, node.__key);
  }

  static importJSON(json: Record<string, unknown>): DocxTableNode {
    const node = new DocxTableNode(json.paraIndex as number | undefined);
    node.updateFromJSON(json as LexicalUpdateJSON<SerializedTableNode>);
    return node;
  }

  exportJSON(): SerializedTableNode & { paraIndex: number | undefined } {
    return {
      ...super.exportJSON(),
      type: 'docx-table',
      paraIndex: this.__paraIndex,
    };
  }

  createDOM(config: EditorConfig): HTMLElement {
    const dom = super.createDOM(config);
    if (this.__paraIndex != null) {
      dom.setAttribute('data-para-index', String(this.__paraIndex));
    }
    return dom;
  }
}
```

顶部类型 import 增加 `SerializedTableNode`（来自 `@lexical/table` 的 `export type { SerializedTableNode }`）。

- [ ] **Step 3: buildInitialContent 表格分支替换**（`docx-paragraph-editor.tsx:633` 附近）

```ts
function buildDocxTable(paraIndex: number, html: string): DocxTableNode | null {
  const cells = parseTableCells(html);
  if (!cells.length) return null;
  const rowMap = new Map<number, typeof cells>();
  for (const c of cells) {
    const arr = rowMap.get(c.row) || [];
    arr.push(c);
    rowMap.set(c.row, arr);
  }
  const table = new DocxTableNode(paraIndex);
  for (const rowCells of [...rowMap.entries()].sort((a, b) => a[0] - b[0]).map(([, v]) => v)) {
    const tr = $createTableRowNode();
    for (const c of rowCells) {
      const td = $createTableCellNode(
        c.header ? TableCellHeaderStates.COLUMN : TableCellHeaderStates.NO_HEADER,
        c.colSpan > 1 ? c.colSpan : 1,
      );
      // 单元格文本按 \n 拆多段（与 python-docx cell.text / getTextContent 的 \n join 对齐）；
      // 空单元格也保留一个空段（Lexical 要求 cell 非空）
      for (const line of c.text.split('\n')) {
        const p = new ParagraphNode();
        p.append($createTextNode(line));
        td.append(p);
      }
      tr.append(td);
    }
    table.append(tr);
  }
  return table;
}
```

`buildInitialContent` 中原分支：

```ts
        if (para.type === 'table' || para.type === 'image') {
          root.append(new AtomicBlockNode(para.index, para.type, para.text));
        } else if ...
```

改为：

```ts
        if (para.type === 'table') {
          const t = buildDocxTable(para.index, para.text);
          if (t) {
            root.append(t);
            continue;
          }
          // 解析失败降级只读原子块（spec 4.1：不阻塞文档打开）
          root.append(new AtomicBlockNode(para.index, 'table', para.text));
        } else if (para.type === 'image') {
          root.append(new AtomicBlockNode(para.index, 'image', para.text));
        } else if (para.type === 'heading') {
          // ...原 heading 分支不动
        } else {
          // ...原正文分支不动
        }
```

（注意：原代码是 if/else-if 链，改成 table 分支内 `continue` 或保持链式均可，语义为「table 解析成功走 DocxTableNode，失败走 AtomicBlockNode」。）

- [ ] **Step 4: 注册节点 + TablePlugin**

`initialConfig.nodes` 数组中 `AtomicBlockNode` 之后插入：

```ts
            DocxTableNode,
            TableNode,
            TableRowNode,
            TableCellNode,
```

组件 JSX 中 `<HistoryPlugin />` 之后加：

```tsx
        <TablePlugin />
```

import：`import { TablePlugin } from '@lexical/react/LexicalTablePlugin';`

- [ ] **Step 5: 类型检查**

Run: `cd web && npx tsc --noEmit -p tsconfig.json 2>&1 | grep -E "docx-paragraph-editor|docx-diff|docx-table-utils" || echo OK`
Expected: `OK`（其他历史报错不在本任务范围）

- [ ] **Step 6: Commit**

```bash
git add web/src/pages/c-chat/docx-paragraph-editor.tsx
git commit -m "feat(flow): DocxTableNode 表格节点 + HTML→Lexical 初始灌入（失败降级只读原子块）"
```

---

### Task 4: 编辑器抽取与格内高亮

**Files:**
- Modify: `web/src/pages/c-chat/docx-paragraph-editor.tsx`

- [ ] **Step 1: readEditorBlocks 表格分支**

在 `readEditorBlocks`（`docx-paragraph-editor.tsx:358`）的 root children 循环中，`if (child instanceof AtomicBlockNode)` 之后加：

```ts
      if (child instanceof DocxTableNode) {
        const paraIndex = child.__paraIndex;
        if (paraIndex == null) continue;
        let ri = 0;
        for (const row of child.getChildren()) {
          if (!(row instanceof TableRowNode)) continue;
          let col = 0;
          for (const cell of row.getChildren()) {
            if (!(cell instanceof TableCellNode)) continue;
            // runs 抽取复用 $extractRuns（TableCellNode 是 ElementNode，visit 递归下降
            // 覆盖格内所有段落；HighlightTextNode 底色已在函数内剔除）
            const runs = $extractRuns(cell);
            out.push({
              paraIndex,
              kind: 'table',
              cell: { row: ri, col },
              text: cell.getTextContent(),
              runs,
              fmtSig: runsFmtSig(runs),
            });
            col += cell.getColSpan() || 1;
          }
          ri += 1;
        }
        continue;
      }
```

注意：`cell.getTextContent()` 把格内多段以 `\n` 连接——与 `parseTableCells` 基线一致。

- [ ] **Step 2: HighlightPlugin 走进单元格**

把现有段落高亮重建逻辑抽成局部函数并扩展到表格（`HighlightPlugin` 的 `editor.update` 回调内）：

```ts
      editor.update(
        () => {
          const rebuild = (p: ElementNode, targets: Array<{ text: string; color: string; key: string }>) => {
            const hasStale = p.getChildren().some((n) => n instanceof HighlightTextNode);
            if (!targets.length && !hasStale) return;
            const segments = splitIntoSegments(p.getTextContent(), targets);
            p.clear();
            for (const seg of segments) {
              if (seg.key) {
                const node = new HighlightTextNode(seg.text, seg.key);
                node.setStyle(`background-color:${seg.color}22;`);
                p.append(node);
              } else {
                p.append($createTextNode(seg.text));
              }
            }
          };
          const root = $getRoot();
          for (const child of root.getChildren()) {
            if (
              child instanceof DocxParagraphNode ||
              child instanceof DocxHeadingNode
            ) {
              const paraIndex = child.__paraIndex;
              if (paraIndex == null) continue;
              rebuild(child, targetsByPara.get(paraIndex) || []);
            } else if (child instanceof DocxTableNode) {
              const paraIndex = child.__paraIndex;
              if (paraIndex == null) continue;
              const targets = targetsByPara.get(paraIndex) || [];
              for (const row of child.getChildren()) {
                if (!(row instanceof TableRowNode)) continue;
                for (const cell of row.getChildren()) {
                  if (!(cell instanceof TableCellNode)) continue;
                  for (const p of cell.getChildren()) {
                    if (p instanceof ParagraphNode) rebuild(p, targets);
                  }
                }
              }
            }
          }
        },
        { tag: 'history-merge' },
      );
```

（原实现里的 `hasStale`/`splitIntoSegments` 逻辑原样移入 `rebuild`，行为对正文段不变。）

- [ ] **Step 3: 类型检查**

Run: `cd web && npx tsc --noEmit -p tsconfig.json 2>&1 | grep -E "docx-paragraph-editor" || echo OK`
Expected: `OK`

- [ ] **Step 4: Commit**

```bash
git add web/src/pages/c-chat/docx-paragraph-editor.tsx
git commit -m "feat(flow): readEditorBlocks 单元格抽取（colSpan 累加网格坐标）+ 格内批注高亮重建"
```

---

### Task 5: review-panel 接线 + 编辑器表格样式

**Files:**
- Modify: `web/src/pages/c-chat/review-panel.tsx`

- [ ] **Step 1: import 与基线 memo**

顶部 import 增加：

```ts
import { parseTableCells, type TableCellInfo } from './docx-table-utils';
```

`targetsByPara` memo（review-panel.tsx:584）之后新增：

```ts
  // 表格 diff 基线：与初始灌入共用 parseTableCells（同源）；解析失败的表不出现在
  // Map 里 → diffBlocks 自动跳过该表改动（保护）
  const tableBaselines = useMemo(() => {
    const m = new Map<number, TableCellInfo[]>();
    if (!content) return m;
    for (const p of content.paragraphs) {
      if (p.type !== 'table') continue;
      const cells = parseTableCells(p.text);
      if (cells.length) m.set(p.index, cells);
    }
    return m;
  }, [content]);
```

- [ ] **Step 2: diff/保存接线**

`handleEditorDirty`（review-panel.tsx:866）：

```ts
        const ops = diffBlocks(blocks, content.paragraphs, tableBaselines);
```

deps 数组加 `tableBaselines`。

`handleSaveEdits`（review-panel.tsx:885）：

```ts
    const ops = collectEditorOps(editorRef.current, content.paragraphs, tableBaselines);
```

（`collectEditorOps` 在 Task 2 之后需同步签名——它内部只是转发 `diffBlocks`，在 `docx-paragraph-editor.tsx:430` 加第三参即可：

```ts
export function collectEditorOps(
  editor: LexicalEditor,
  paragraphs: DocxSourceParagraph[],
  tableBaselines?: Map<number, BaselineCell[]>,
): DocxDiffOps {
  return diffBlocks(readEditorBlocks(editor), paragraphs, tableBaselines);
}
```

import `BaselineCell` 类型来自 `./docx-diff`。）

- [ ] **Step 3: renderAtomicBlock 去掉表格分支**

`renderAtomicBlock`（review-panel.tsx:990）只保留 image；表格不再经 Atomic 渲染：

```ts
  const renderAtomicBlock = useCallback(
    ({
      kind,
      html,
    }: {
      paraIndex: number;
      kind: 'table' | 'image';
      html: string;
    }) => {
      if (kind === 'image') {
        return (
          <div className="py-1 text-[13px] italic text-[#8A8A8A]">{html}</div>
        );
      }
      // 表格已改为 DocxTableNode 可编辑渲染，只有解析失败降级时才会走到这里
      const firstAi = (railByPara.get(paraIndex) || []).find(
        (i) => i.kind === 'ai',
      );
      let tableHtml = html;
      if (firstAi?.ann) {
        tableHtml = highlightInTableHtml(
          tableHtml,
          getMatchedText(firstAi.ann),
          firstAi.color,
          firstAi.key,
        );
      }
      for (const it of railByPara.get(paraIndex) || []) {
        if (it.kind !== 'comment') continue;
        const at = (it.comment?.anchor_text || '').trim();
        if (!at) continue;
        tableHtml = highlightInTableByAnchor(
          tableHtml,
          at,
          it.color ?? '#1a66fb',
          it.key,
          getAnchorStart(it.comment),
        );
      }
      return (
        <div
          className="text-xs overflow-x-auto [&_table]:w-full [&_table]:border-collapse [&_th]:border [&_th]:border-[#D4D4D4] [&_th]:bg-[#F5F5F5] [&_th]:px-2 [&_th]:py-1 [&_th]:text-[#1A1A1A] [&_td]:border [&_td]:border-[#D4D4D4] [&_td]:px-2 [&_td]:py-1 [&_td]:text-[#333333]"
          onClick={handleSelectTableAnn}
          dangerouslySetInnerHTML={{ __html: sanitizeTableHtml(tableHtml) }}
        />
      );
    },
    [railByPara, handleSelectTableAnn],
  );
```

（保留降级只读渲染；`highlightInTableByAnchor(...)` 调用以文件内现有参数为准——实现时对照 review-panel.tsx:1022-1026 原样搬移，本步骤不得改变其参数。）

- [ ] **Step 4: 编辑模式表格样式**

纸张容器（review-panel.tsx:1219 的 `div.mx-auto.w-full.max-w-[794px]`）className 追加：

```
 [&_table]:my-2 [&_table]:w-full [&_table]:border-collapse [&_td]:border [&_td]:border-[#D4D4D4] [&_td]:px-2 [&_td]:py-1 [&_td]:text-[13px] [&_td]:text-[#333333] [&_td]:align-top [&_th]:border [&_th]:border-[#D4D4D4] [&_th]:bg-[#F5F5F5] [&_th]:px-2 [&_th]:py-1 [&_th]:font-bold
```

（只作用于编辑器分支；只读静态分支的 `[&_td]` 样式在各自渲染处，不动。）

- [ ] **Step 5: 类型检查**

Run: `cd web && npx tsc --noEmit -p tsconfig.json 2>&1 | grep -E "review-panel" || echo OK`
Expected: `OK`

- [ ] **Step 6: Commit**

```bash
git add web/src/pages/c-chat/review-panel.tsx web/src/pages/c-chat/docx-paragraph-editor.tsx
git commit -m "feat(flow): 文件审核表格接入可编辑链路（基线 memo + diff/保存接线 + 编辑态表格样式）"
```

---

### Task 6: 保存链路透传（flow-service + flow-ai-panel）

**Files:**
- Modify: `web/src/services/flow-service.ts:164-217`
- Modify: `web/src/pages/c-chat/flow/flow-ai-panel.tsx:560-577`

- [ ] **Step 1: FlowDocEditOps 增加 tableEdits**

```ts
export interface FlowDocEditOps {
  edits: Array<{/* 原样 */}>;
  deletes: number[];
  inserts: Array<{/* 原样 */}>;
  /** 单元格级改动（后端 table.cell(r,c) 改写首段、清空多余段） */
  tableEdits: Array<{
    paraIndex: number;
    row: number;
    col: number;
    newText: string;
    runs?: FlowDocRun[];
  }>;
}
```

- [ ] **Step 2: editFlowDocument payload 映射**（`flow-service.ts:192` body 内 deletes 之前加）

```ts
      table_edits: (ops.tableEdits || []).map((t) => ({
        para_index: t.paraIndex,
        row: t.row,
        col: t.col,
        new_text: t.newText,
        ...(t.runs ? { runs: t.runs } : {}),
      })),
```

- [ ] **Step 3: flow-ai-panel handleEditDocument ops 类型**（flow-ai-panel.tsx:561）加字段：

```ts
      tableEdits: Array<{
        paraIndex: number;
        row: number;
        col: number;
        newText: string;
        runs?: FlowDocRun[];
      }>;
```

（`FlowDocRun` 已在该文件可用；若未 import 则从 `@/services/flow-service` 补。该回调只透传 `ops` 给 `editFlowDocument`，无逻辑改动。）

- [ ] **Step 4: 类型检查**

Run: `cd web && npx tsc --noEmit -p tsconfig.json 2>&1 | grep -E "flow-service|flow-ai-panel" || echo OK`
Expected: `OK`

- [ ] **Step 5: Commit**

```bash
git add web/src/services/flow-service.ts web/src/pages/c-chat/flow/flow-ai-panel.tsx
git commit -m "feat(flow): 编辑保存契约透传 table_edits 到后端"
```

---

### Task 7: 后端 `table_edits` 解析与应用

**Files:**
- Modify: `api/apps/restful_apis/flow_app.py`（`_build_para_map`:354、`edit_document`:552）
- Test: `test/test_flow_doc_table_edit.py`（新建）

- [ ] **Step 1: 写失败测试**

```python
# test/test_flow_doc_table_edit.py
"""表格单元格编辑纯 helper 单测：不依赖 Quart/DB，直接构造 python-docx 文档。"""
import io

import pytest
from docx import Document

from api.apps.restful_apis.flow_app import (
    _apply_cell_text,
    _build_para_map,
    _parse_table_edits,
)


def _doc_with_table():
    doc = Document()
    doc.add_paragraph("前置段落")
    t = doc.add_table(rows=2, cols=2)
    t.cell(0, 0).paragraphs[0].add_run("甲")
    t.cell(0, 1).paragraphs[0].add_run("乙")
    t.cell(1, 0).paragraphs[0].add_run("丙")
    t.cell(1, 1).paragraphs[0].add_run("丁")
    doc.add_paragraph("后置段落")
    return doc


def test_para_map_table_entry_is_docx_table():
    doc = _doc_with_table()
    pm = _build_para_map(doc)
    assert pm[0][0] == "p"
    assert pm[1][0] == "table"
    table = pm[1][1]
    assert table.cell(0, 0).text == "甲"
    assert pm[2][0] == "p"


def test_apply_cell_text_replace_and_clear():
    doc = _doc_with_table()
    table = _build_para_map(doc)[1][1]
    _apply_cell_text(table.cell(0, 0), "甲改", None)
    assert table.cell(0, 0).text == "甲改"
    # 清空：new_text 允许空串
    _apply_cell_text(table.cell(0, 1), "", None)
    assert table.cell(0, 1).text == ""


def test_apply_cell_text_runs_bold():
    doc = _doc_with_table()
    table = _build_para_map(doc)[1][1]
    runs = [{"text": "加粗", "bold": True}]
    _apply_cell_text(table.cell(1, 0), "加粗", runs)
    cell = table.cell(1, 0)
    assert cell.text == "加粗"
    assert cell.paragraphs[0].runs[0].bold is True


def test_apply_cell_text_multiline_writes_br_and_clears_extra_paras():
    doc = Document()
    t = doc.add_table(rows=1, cols=1)
    cell = t.cell(0, 0)
    cell.add_paragraph("第二段")
    assert len(cell.paragraphs) == 2  # 前置确认：单元格本就两段
    _apply_cell_text(cell, "一行\n两行", None)
    assert len(cell.paragraphs) == 2
    texts = [p.text for p in cell.paragraphs]
    assert texts[0].startswith("一行") and texts[1] == ""


def test_parse_table_edits_ok_and_empty_text_allowed():
    items = _parse_table_edits([
        {"para_index": 1, "row": 0, "col": 0, "new_text": "改"},
        {"para_index": 1, "row": 0, "col": 1, "new_text": ""},
        {"para_index": 1, "row": 1, "col": 0, "new_text": "加粗", "runs": [{"text": "加粗", "bold": True}]},
    ])
    assert len(items) == 3
    assert items[1]["new_text"] == ""


@pytest.mark.parametrize("bad", [
    {"para_index": 1, "row": -1, "col": 0, "new_text": "x"},   # 负数
    {"para_index": 1, "row": 0, "col": "a", "new_text": "x"},  # 非整数
    {"para_index": 1, "row": 0, "col": 0, "new_text": "x" * 20001},  # 超长
    {"para_index": 1, "row": 0, "col": 0, "new_text": "x", "runs": [{"text": "y"}]},  # runs 不一致
    {"para_index": 1, "row": 0, "col": 0, "new_text": "", "runs": [{"text": "y"}]},   # 清空带 runs
    "not-a-dict",
])
def test_parse_table_edits_rejects_bad_input(bad):
    with pytest.raises(ValueError):
        _parse_table_edits([bad])
```

- [ ] **Step 2: 运行确认失败**

Run: `uv run pytest test/test_flow_doc_table_edit.py -v`
Expected: FAIL（ImportError: `_parse_table_edits`/`_apply_cell_text` 不存在）

- [ ] **Step 3: 实现**

3a. import 区（flow_app.py:48 附近）加：

```python
from docx.table import Table as DocxTable
```

3b. `_build_para_map`（flow_app.py:379-381）表格分支改为存 DocxTable 实例：

```python
        elif block.tag.endswith("tbl"):
            para_map[idx] = ("table", DocxTable(block, doc))
            idx += 1
```

（`_entry_of` 无需改动：edits/deletes 落在表格段仍走 `kind != "p"` → atomic 拒绝路径。）

3c. 新增两个 helper（放在 `_apply_block_attrs` 之后、`edit_document` 之前）：

```python
def _parse_table_edits(raw):
    """解析并校验 table_edits：[{para_index,row,col,new_text,runs?}]。
    与正文 edits 的差异：new_text 允许空串（清空单元格）；空串不得携带 runs。
    非法抛 ValueError（消息带格位），由调用方转 400。"""
    if not isinstance(raw, list):
        raise ValueError("table_edits 必须是数组")
    parsed = []
    for t in raw:
        if not isinstance(t, dict):
            raise ValueError("table_edits 项格式非法")
        try:
            para_index = int(t.get("para_index"))
            row = int(t.get("row"))
            col = int(t.get("col"))
        except (TypeError, ValueError):
            raise ValueError("table_edits 的 para_index/row/col 必须是整数")
        if para_index < 0 or row < 0 or col < 0:
            raise ValueError(f"表格单元格 ({row},{col}) 行列号不能为负")
        new_text = _CTRL_CHARS.sub("", str(t.get("new_text") or ""))
        if len(new_text) > 20000:
            raise ValueError(f"表格单元格 ({row},{col}) 内容不能超过 20000 字")
        runs = _parse_runs(t.get("runs"))
        if not new_text and runs:
            raise ValueError(f"表格单元格 ({row},{col}) 清空时不能携带 runs")
        if runs and "".join(x["text"] for x in runs).strip() != new_text.strip():
            raise ValueError(f"表格单元格 ({row},{col}) runs 文本与 new_text 不一致")
        parsed.append({
            "para_index": para_index, "row": row, "col": col,
            "new_text": new_text, "runs": runs,
        })
    return parsed


def _apply_cell_text(cell, new_text: str, runs):
    """写 python-docx 单元格：runs/整段替换写入首段，其余段落清空
    （保留段落对象——docx 单元格至少需要一个段落）。\\n 由 run.text setter
    自动转 <w:br/>。"""
    paras = cell.paragraphs
    first = paras[0]
    if runs is not None:
        _apply_runs(first, runs)
    else:
        _replace_para_text(first, new_text)
    for p in paras[1:]:
        _replace_para_text(p, "")
```

3d. `edit_document` 集成（五处）：

计数与空检查（flow_app.py:574-577）：

```python
        table_edits_raw = body.get("table_edits") or []
        if not isinstance(table_edits_raw, list):
            return _err("table_edits 必须是数组", 101)
        if not edits and not deletes and not inserts and not table_edits_raw:
            return _err("没有需要保存的改动", 101)
        if len(edits) + len(deletes) + len(inserts) + len(table_edits_raw) > 200:
            return _err("单次最多修改 200 处", 101)
```

解析（parsed_inserts 循环之后）：

```python
        try:
            parsed_table_edits = _parse_table_edits(table_edits_raw)
        except ValueError as ve:
            return _err(str(ve), 101)
```

定位（located_edits/located_deletes 之后、`_apply_ops` 定义之前；此处 para_map 已在作用域）：

```python
        located_table_edits = []
        for t in parsed_table_edits:
            entry = para_map.get(t["para_index"])
            if entry is None:
                return _err(f"段落 {t['para_index']} 定位失败，文档可能已变化，请刷新后重试", 101)
            if entry[0] != "table":
                return _err(f"段落 {t['para_index']} 不是表格，table_edits 定位非法", 101)
            table = entry[1]
            if t["row"] >= len(table.rows) or t["col"] >= len(table.columns):
                return _err(
                    f"表格 {t['para_index']} 单元格 ({t['row']},{t['col']}) 超出范围", 101
                )
            located_table_edits.append((table.cell(t["row"], t["col"]), t["new_text"], t["runs"]))
```

应用（`_apply_ops` 内、正文 edits 循环之后追加）：

```python
            for cell, text, rns in located_table_edits:
                _apply_cell_text(cell, text, rns)
```

docstring（flow_app.py:555-559）同步更新：`表格/图片为原子块，不可改写、不可删除` →
`表格支持 table_edits 单元格级改写（不可删除整表/不可增删行列）；图片为原子块不可改写`。

- [ ] **Step 4: 运行确认通过**

Run: `uv run pytest test/test_flow_doc_table_edit.py -v`
Expected: PASS（9+ 用例全绿）

- [ ] **Step 5: Ruff**

Run: `uv run ruff check api/apps/restful_apis/flow_app.py test/test_flow_doc_table_edit.py && uv run ruff format --check api/apps/restful_apis/flow_app.py test/test_flow_doc_table_edit.py`
Expected: 无报错（有则 `uv run ruff format` 后复跑）

- [ ] **Step 6: Commit**

```bash
git add api/apps/restful_apis/flow_app.py test/test_flow_doc_table_edit.py
git commit -m "feat(flow): document/edit 支持 table_edits 单元格级改写（_build_para_map 存 DocxTable）"
```

---

### Task 8: 全量验证 + 手动 E2E + CHANGE.md

**Files:**
- Modify: `CHANGE.md`

- [ ] **Step 1: 前端全量测试 + 类型检查**

```bash
cd web && npx jest src/pages/c-chat --coverage=false && npx tsc --noEmit -p tsconfig.json
```
Expected: 全绿（存量 c-chat 测试不回归；tsc 无新增报错）

- [ ] **Step 2: 后端测试**

```bash
uv run pytest test/test_flow_doc_table_edit.py test/test_flow_logic.py -v
```
Expected: 全绿

- [ ] **Step 3: 手动 E2E（本地 npm run dev + 后端起服务，账号 lg18629285296@163.com/12345678）**

1. C端 → 流程页签 → 有 docx（含表格）版本的流程 → 「文件审核」
2. 单元格内点击出现光标 → 改字 → 工具栏加粗 → 保存 → 提示成功，版本时间线出现新版本
3. 重新打开新版本：改字与加粗在、表格行列/列宽未变、其他单元格原样
4. 格内命中批注目标的文字有高亮底色，点击联动右侧批注卡片；改字后高亮不残留错位
5. 图片仍只读；尝试删除整张表格 → 保存报「不支持删除表格/图片」
6. 清空某单元格 → 保存 → 重开后该格为空
7. 只读查看版（非负责人）不受影响：表格仍为静态 HTML 渲染

- [ ] **Step 4: CHANGE.md 增量记录 + commit**

```bash
git add CHANGE.md
git commit -m "docs: CHANGE.md 记录文件审核表格可编辑迭代"
```

（CHANGE.md 条目：日期 2026-09-01、主题「文件审核表格可编辑」、核心变更点 4 条（DocxTableNode/table_edits 契约/后端 cell 改写/降级保护）、遗留事项「合并单元格 false-positive colspan 场景下只写首格」）

---

## Known Limitations（实现后随 CHANGE.md 记录）

1. **colspan 启发式误合并**：naive.py 用「相邻同文本」推断 colspan，两个内容相同的独立 docx 单元格会被解析成一个跨列格——此时编辑只写入首个网格列，第二个 docx 格保留旧文本。极低频场景，接受。
2. 嵌套表格文本在灌入时被忽略（spec 约定），其内容改动不参与 diff。
3. 表格内不支持增删行列（spec 明确排除）。
