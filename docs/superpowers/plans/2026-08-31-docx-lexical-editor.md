# 文件审核编辑器升级：Lexical 整篇编辑 — 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 用 Lexical（项目已有依赖）替换 review-panel 手写 contentEditable，实现 Word 式整篇编辑（撤销/重做、IME 安全、回车分段、退格并段），保存契约与后端 `/flow/<id>/document/edit` 零改动。

**Architecture:** 4 个 Lexical 自定义节点（段落/标题/只读原子块/高亮文本）+ 编辑器组件与插件（高亮、点击联动、脏检查、粘贴降级）。para_index 存节点属性、仅初始加载赋值，diff 从「DOM 遍历」改为「编辑器模型遍历」，核心 diff 抽成纯函数 `diffBlocks` 可单测。

**Tech Stack:** lexical 0.23.1 + @lexical/react（均在 web/package.json 已有，@lexical/rich-text 经 @lexical/react 传递依赖已在 node_modules 且项目已有 import 先例）+ Jest（已有配置）。

**设计文档:** `docs/superpowers/specs/2026-08-31-docx-lexical-editor-design.md`

**约束提醒:**
- 只改前端；`flow-service.ts`、`flow-ai-panel.tsx`、后端**零改动**
- 只读模式（非负责人/手动附件）静态渲染路径**不动**
- 禁止自动部署；开发验证用 dev 服务器 9222

---

### Task 1: 抽共享视图工具 docx-view-utils.ts

表格高亮/清洗函数从 review-panel.tsx 原样迁出，供静态渲染与编辑器 AtomicBlockNode 共用。

**Files:**
- Create: `web/src/pages/c-chat/docx-view-utils.ts`
- Modify: `web/src/pages/c-chat/review-panel.tsx`

- [ ] **Step 1: 新建 docx-view-utils.ts（内容从 review-panel.tsx 迁出，highlightInTableHtml 第二参数从 Annotation 改为字符串）**

```ts
// 文档视图共享工具：表格 HTML 清洗与高亮（review-panel 静态渲染与
// docx-paragraph-editor AtomicBlockNode 共用，从 review-panel.tsx 迁出）

/** 与 review-panel.tsx 的 normalizeForMatch 相同实现（迁移副本） */
export function normalizeForMatch(text: string): string {
  return text
    .replace(/<[^>]+>/g, '')
    .replace(
      /[\s\u2460-\u24ff\u3000-\u303f\uff00-\uffef.,;:!?()[\]{}'"，。、；：！？（）【】《》""''—…·•°≥≤/\\-]/g,
      '',
    );
}

export function sanitizeTableHtml(html: string): string {
  return html
    .replace(/<script[\s\S]*?<\/script>/gi, '')
    .replace(/<iframe[\s\S]*?<\/iframe>/gi, '')
    .replace(/<object[\s\S]*?<\/object>/gi, '')
    .replace(/<embed[\s\S]*?>/gi, '')
    .replace(/\s+on\w+\s*=\s*"[^"]*"/gi, '')
    .replace(/\s+on\w+\s*=\s*'[^']*'/gi, '')
    .replace(/<noscript[\s\S]*?<\/noscript>/gi, '');
}

/** 注：第二参数为 matched 文本字符串（旧签名是 Annotation，解耦类型便于编辑器复用） */
export function highlightInTableHtml(
  html: string,
  target: string,
  color: string,
  anchorKey?: string,
): string {
  if (!target) return html;
  const markStyle = `background:${color}22;border-bottom:2px solid ${color};border-radius:2px;padding:0 1px;`;
  const wrap = (text: string) =>
    anchorKey
      ? `<a href="#${anchorKey}" data-anchor-key="${anchorKey}" style="text-decoration:none;color:inherit;"><mark style="${markStyle}">${text}</mark></a>`
      : `<mark style="${markStyle}">${text}</mark>`;

  if (html.includes(target)) {
    return html.replace(target, wrap(target));
  }

  const chunks = target
    .split(/[，。、；：的且在持有满足进行评价以下含\n]/)
    .filter((c) => c.length >= 5);
  let result = html;
  let replaced = false;
  chunks.sort((a, b) => b.length - a.length);
  for (const chunk of chunks) {
    if (result.includes(chunk)) {
      result = result.replace(chunk, wrap(chunk));
      replaced = true;
    }
  }
  if (replaced) return result;

  const normTarget = normalizeForMatch(target);
  if (normTarget.length >= 6) {
    const shortTarget = normTarget.substring(0, 15);
    if (shortTarget.length >= 5) {
      const chunks2 = target
        .split(/[,，。、；：\s]/)
        .filter((c) => c.length >= 4);
      for (const chunk of chunks2) {
        if (result.includes(chunk)) {
          result = result.replace(chunk, wrap(chunk));
          replaced = true;
        }
      }
    }
  }

  return replaced ? result : html;
}

export function highlightInTableByAnchor(
  html: string,
  anchorText: string,
  anchorStart: number | null | undefined,
  color: string,
  anchorKey: string,
): string {
  const target = anchorText.replace(/\s+/g, '');
  if (!target) return html;
  const host = document.createElement('div');
  host.innerHTML = html;
  const walker = document.createTreeWalker(host, NodeFilter.SHOW_TEXT);
  const nodes: { node: Text; start: number }[] = [];
  let acc = '';
  let n = walker.nextNode() as Text | null;
  while (n) {
    nodes.push({ node: n, start: acc.length });
    acc += (n.nodeValue || '').replace(/\s+/g, '');
    n = walker.nextNode() as Text | null;
  }
  let idx =
    anchorStart != null && anchorStart > 4
      ? acc.indexOf(target, anchorStart - 4)
      : -1;
  if (idx < 0) idx = acc.indexOf(target);
  if (idx < 0) return html;
  const endIdx = idx + target.length;
  const markStyle = `background:${color}22;border-bottom:2px solid ${color};border-radius:2px;padding:0 1px;cursor:pointer;`;
  for (const { node, start } of nodes) {
    const raw = node.nodeValue || '';
    const nodeEnd = start + raw.replace(/\s+/g, '').length;
    if (nodeEnd <= idx || start >= endIdx) continue;
    const mapRaw = (normOff: number) => {
      let cnt = 0;
      for (let j = 0; j < raw.length; j++) {
        if (cnt >= normOff - start) return j;
        if (!/\s/.test(raw[j])) cnt++;
      }
      return raw.length;
    };
    const rs = mapRaw(Math.max(idx, start));
    const re = mapRaw(Math.min(endIdx, nodeEnd));
    let seg: Text = node;
    if (rs > 0) seg = node.splitText(rs);
    if (re - rs < (seg.nodeValue || '').length) seg.splitText(re - rs);
    const mark = document.createElement('mark');
    mark.setAttribute('data-anchor-key', anchorKey);
    mark.style.cssText = markStyle;
    seg.parentNode?.insertBefore(mark, seg);
    mark.appendChild(seg);
  }
  return host.innerHTML;
}
```

- [ ] **Step 2: review-panel.tsx 删除这 4 个函数定义并改为 import**

删除 review-panel.tsx 中的 `sanitizeTableHtml`、`normalizeForMatch`、`highlightInTableHtml`、`highlightInTableByAnchor` 四个函数定义，顶部加：

```ts
import {
  highlightInTableByAnchor,
  highlightInTableHtml,
  normalizeForMatch,
  sanitizeTableHtml,
} from './docx-view-utils';
```

- [ ] **Step 3: 更新 highlightInTableHtml 调用点（第二参数改传字符串）**

review-panel.tsx 中唯一调用点改为：

```tsx
if (firstAi?.ann) {
  tableHtml = highlightInTableHtml(
    tableHtml,
    getMatchedText(firstAi.ann),
    firstAi.color,
    firstAi.key,
  );
}
```

- [ ] **Step 4: 类型检查**

Run: `cd web && npx tsc --noEmit 2>&1 | grep -E "review-panel|docx-view-utils"; echo done`
Expected: 只输出 `done`（无新错误）

- [ ] **Step 5: Commit**

```bash
git add web/src/pages/c-chat/docx-view-utils.ts web/src/pages/c-chat/review-panel.tsx
git commit -m "refactor(flow): 表格高亮/清洗函数抽到 docx-view-utils 供编辑器复用"
```

---

### Task 2: TDD 纯 diff 函数 docx-diff.ts

模型级 diff 与高亮分段拆成纯函数，不依赖 Lexical，可直接单测。

**Files:**
- Create: `web/src/pages/c-chat/docx-diff.ts`
- Test: `web/src/pages/c-chat/__tests__/docx-diff.test.ts`

- [ ] **Step 1: 先写失败测试**

```ts
import { diffBlocks, splitIntoSegments } from '../docx-diff';

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
    expect(diffBlocks(blocks, paragraphs)).toMatchObject({ error: expect.any(String) });
  });

  it('表格原样在场不参与 diff 也不误报', () => {
    const paragraphs = [src(0, '文本'), src(1, '<table/>', 'table')];
    const blocks = [
      { paraIndex: 0, kind: 'text' as const, text: '文本' },
      { paraIndex: 1, kind: 'table' as const, text: '' },
    ];
    const ops = diffBlocks(blocks, paragraphs);
    expect('error' in ops).toBe(false);
    expect(ops.count).toBe(0);
  });

  it('空文档（全部被删）含表格时报 error', () => {
    const paragraphs = [src(0, '<table/>', 'table'), src(1, 'A')];
    const blocks: any[] = [];
    expect(diffBlocks(blocks, paragraphs)).toMatchObject({ error: expect.any(String) });
  });
});

describe('splitIntoSegments', () => {
  it('无匹配 → 单个普通片段', () => {
    expect(splitIntoSegments('abc', [])).toEqual([{ text: 'abc' }]);
    expect(splitIntoSegments('abc', [{ text: 'x', color: '#f00', key: 'k' }])).toEqual([
      { text: 'abc' },
    ]);
  });

  it('单目标命中 → 三段，中间带 key/color', () => {
    expect(
      splitIntoSegments('abcdef', [{ text: 'cd', color: '#f00', key: 'k1' }]),
    ).toEqual([{ text: 'ab' }, { text: 'cd', key: 'k1', color: '#f00' }, { text: 'ef' }]);
  });

  it('多次出现全部包裹', () => {
    const segs = splitIntoSegments('xx-xx', [{ text: 'xx', color: '#f00', key: 'k' }]);
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
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd web && npx jest src/pages/c-chat/__tests__/docx-diff.test.ts 2>&1 | tail -5`
Expected: FAIL（`Cannot find module '../docx-diff'`）

- [ ] **Step 3: 实现 docx-diff.ts**

```ts
// 文档模型 diff：编辑器块描述 vs 原文段落，产出后端 /flow/<id>/document/edit
// 契约的三类操作（edits/deletes/inserts）。纯函数、零依赖，可单测。

export interface DocxSourceParagraph {
  index: number;
  text: string;
  type: 'heading' | 'paragraph' | 'table' | 'image';
  heading_level?: number;
}

/** 编辑器内一个顶级块的最小描述（由 readEditorBlocks 从 Lexical 模型抽取） */
export interface EditorBlock {
  /** 原文段落 index；undefined = 用户新增（回车/拆分产生） */
  paraIndex?: number;
  kind: 'text' | 'table' | 'image';
  text: string;
}

export type DocxDiffOps =
  | { error: string }
  | {
      error?: undefined;
      edits: Array<{ paraIndex: number; newText: string }>;
      deletes: number[];
      inserts: Array<{ afterParaIndex: number; newText: string }>;
      count: number;
    };

export function diffBlocks(
  blocks: EditorBlock[],
  paragraphs: DocxSourceParagraph[],
): DocxDiffOps {
  const byIdx = new Map(paragraphs.map((p) => [p.index, p]));
  const seen = new Set<number>();
  const edits: Array<{ paraIndex: number; newText: string }> = [];
  const deletes: number[] = [];
  const inserts: Array<{ afterParaIndex: number; newText: string }> = [];
  let lastIdx: number | null = null;

  for (const b of blocks) {
    if (b.paraIndex != null) {
      const idx = b.paraIndex;
      seen.add(idx);
      lastIdx = idx;
      if (b.kind !== 'text') continue; // 表格/图片原子块只记 seen，不参与文本 diff
      const orig = byIdx.get(idx);
      if (!orig) continue;
      const text = b.text.trim();
      if (!text) {
        deletes.push(idx);
      } else if (text !== orig.text.trim()) {
        edits.push({ paraIndex: idx, newText: text });
      }
    } else {
      const text = b.text.trim();
      if (text) {
        inserts.push({ afterParaIndex: lastIdx == null ? -1 : lastIdx, newText: text });
      }
    }
  }

  for (const p of paragraphs) {
    if (!seen.has(p.index)) {
      if (p.type === 'table' || p.type === 'image') {
        return { error: '不支持删除表格/图片，请撤销该操作后保存' };
      }
      deletes.push(p.index);
    }
  }

  return {
    edits,
    deletes,
    inserts,
    count: edits.length + deletes.length + inserts.length,
  };
}

export interface HighlightSegment {
  text: string;
  key?: string;
  color?: string;
}

/** 把段落文本按高亮目标拆成片段（带 key 的段不再被后续目标拆分，先到先得） */
export function splitIntoSegments(
  text: string,
  targets: Array<{ text: string; color: string; key: string }>,
): HighlightSegment[] {
  let segments: HighlightSegment[] = [{ text }];
  for (const t of targets) {
    if (!t.text) continue;
    const next: HighlightSegment[] = [];
    for (const seg of segments) {
      if (seg.key || !seg.text.includes(t.text)) {
        next.push(seg);
        continue;
      }
      const parts = seg.text.split(t.text);
      parts.forEach((p, i) => {
        if (p) next.push({ text: p });
        if (i < parts.length - 1) {
          next.push({ text: t.text, key: t.key, color: t.color });
        }
      });
    }
    segments = next;
  }
  return segments;
}
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd web && npx jest src/pages/c-chat/__tests__/docx-diff.test.ts 2>&1 | tail -5`
Expected: PASS（14 个用例全绿）

- [ ] **Step 5: Commit**

```bash
git add web/src/pages/c-chat/docx-diff.ts web/src/pages/c-chat/__tests__/docx-diff.test.ts
git commit -m "feat(flow): 文档模型 diff 纯函数 diffBlocks/splitIntoSegments + 单测"
```

---

### Task 3: Lexical 编辑器组件 docx-paragraph-editor.tsx

4 个自定义节点 + 编辑器组件 + 4 个插件 + `collectEditorOps`。

**Files:**
- Create: `web/src/pages/c-chat/docx-paragraph-editor.tsx`

- [ ] **Step 1: 写入完整文件**

```tsx
import { HeadingNode, HeadingTagType } from '@lexical/rich-text';
import { useLexicalComposerContext } from '@lexical/react/LexicalComposerContext';
import { ContentEditable } from '@lexical/react/LexicalContentEditable';
import { InitialConfigType, LexicalComposer } from '@lexical/react/LexicalComposer';
import { LexicalErrorBoundary } from '@lexical/react/LexicalErrorBoundary';
import { LexicalHistoryPlugin } from '@lexical/react/LexicalHistoryPlugin';
import { RichTextPlugin } from '@lexical/react/LexicalRichTextPlugin';
import {
  $applyNodeReplacement,
  $createTextNode,
  $getRoot,
  CLICK_COMMAND,
  COMMAND_PRIORITY_HIGH,
  DecoratorNode,
  ElementNode,
  LexicalEditor,
  PASTE_COMMAND,
  ParagraphNode,
  RangeSelection,
  TextNode,
} from 'lexical';
import { ReactNode, useEffect, useMemo } from 'react';
import {
  diffBlocks,
  DocxDiffOps,
  DocxSourceParagraph,
  EditorBlock,
  splitIntoSegments,
} from './docx-diff';

// ── 自定义节点 ──────────────────────────────────────────────
// paraIndex 仅在初始加载（buildInitialContent）时赋值；Lexical 内部
// 克隆（回车分段等）走 insertNewAfter/无参构造 → 新实例 paraIndex 为
// undefined，天然区隔「原文段落」与「新增段落」，diff 无需解析 DOM。

/** 文档正文段（对应 source paragraph type=paragraph） */
export class DocxParagraphNode extends ParagraphNode {
  __paraIndex: number | undefined;

  constructor(paraIndex?: number, key?: NodeKey) {
    super(key);
    this.__paraIndex = paraIndex;
  }

  static getType(): string {
    return 'docx-paragraph';
  }

  static clone(node: DocxParagraphNode): DocxParagraphNode {
    return new DocxParagraphNode(node.__paraIndex, node.__key);
  }

  static importJSON(json: Record<string, unknown>): DocxParagraphNode {
    const node = new DocxParagraphNode(json.paraIndex as number | undefined);
    node.setFormat(typeof json.format === 'number' ? json.format : 0);
    node.setIndent(typeof json.indent === 'number' ? json.indent : 0);
    if (typeof json.direction === 'string') node.setDirection(json.direction);
    if (typeof json.style === 'string') node.setStyle(json.style);
    return node;
  }

  exportJSON(): Record<string, unknown> {
    return {
      ...super.exportJSON(),
      type: 'docx-paragraph',
      paraIndex: this.__paraIndex,
    };
  }

  // 回车分段：新段为本类（无 paraIndex → diff 记 insert）
  insertNewAfter(
    _selection: RangeSelection,
    restoreSelection = true,
  ): DocxParagraphNode {
    const node = $applyNodeReplacement(new DocxParagraphNode(undefined));
    node.__indent = this.__indent;
    node.__direction = this.__direction;
    node.__format = this.__format;
    node.__style = this.__style;
    this.insertAfter(node, restoreSelection);
    return node;
  }

  createDOM(config: EditorConfig): HTMLElement {
    const dom = super.createDOM(config);
    if (this.__paraIndex != null) {
      dom.setAttribute('data-para-index', String(this.__paraIndex));
    }
    dom.className += ' text-[14px] leading-[2] text-justify text-[#333333]';
    dom.style.textIndent = '2em';
    return dom;
  }
}

/** 文档标题段（对应 source paragraph type=heading，tag 由 heading_level 派生） */
export class DocxHeadingNode extends HeadingNode {
  __paraIndex: number | undefined;

  constructor(tag: HeadingTagType, paraIndex?: number, key?: NodeKey) {
    super(tag, key);
    this.__paraIndex = paraIndex;
  }

  static getType(): string {
    return 'docx-heading';
  }

  static clone(node: DocxHeadingNode): DocxHeadingNode {
    return new DocxHeadingNode(node.__tag, node.__paraIndex, node.__key);
  }

  static importJSON(json: Record<string, unknown>): DocxHeadingNode {
    const node = new DocxHeadingNode(
      (json.tag as HeadingTagType) || 'h3',
      json.paraIndex as number | undefined,
    );
    node.setFormat(typeof json.format === 'number' ? json.format : 0);
    node.setIndent(typeof json.indent === 'number' ? json.indent : 0);
    return node;
  }

  exportJSON(): Record<string, unknown> {
    return {
      ...super.exportJSON(),
      type: 'docx-heading',
      paraIndex: this.__paraIndex,
    };
  }

  insertNewAfter(
    _selection: RangeSelection,
    restoreSelection = true,
  ): DocxHeadingNode {
    const node = $applyNodeReplacement(new DocxHeadingNode(this.__tag, undefined));
    node.__indent = this.__indent;
    node.__direction = this.__direction;
    node.__format = this.__format;
    this.insertAfter(node, restoreSelection);
    return node;
  }

  createDOM(config: EditorConfig): HTMLElement {
    const dom = super.createDOM(config);
    if (this.__paraIndex != null) {
      dom.setAttribute('data-para-index', String(this.__paraIndex));
    }
    dom.className += ' text-[15px] font-bold text-[#1A1A1A] mt-5 mb-2';
    return dom;
  }
}

/** 批注/标注高亮文本片段：带 data-anchor-key 供引线锚定与点击联动 */
export class HighlightTextNode extends TextNode {
  __anchorKey: string;

  constructor(text: string, anchorKey: string, key?: NodeKey) {
    super(text, key);
    this.__anchorKey = anchorKey;
  }

  static getType(): string {
    return 'highlight-text';
  }

  static clone(node: HighlightTextNode): HighlightTextNode {
    return new HighlightTextNode(node.__text, node.__anchorKey, node.__key);
  }

  static importJSON(json: Record<string, unknown>): HighlightTextNode {
    return $applyNodeReplacement(
      new HighlightTextNode(
        (json.text as string) || '',
        (json.anchorKey as string) || '',
      ),
    );
  }

  exportJSON(): Record<string, unknown> {
    return {
      ...super.exportJSON(),
      type: 'highlight-text',
      anchorKey: this.__anchorKey,
    };
  }

  createDOM(config: EditorConfig): HTMLElement {
    const dom = super.createDOM(config);
    dom.setAttribute('data-anchor-key', this.__anchorKey);
    return dom;
  }
}

// 表格/图片原子块的 React 渲染函数由父级经 Context 注入（闭包持有
// review-panel 的高亮/批注状态），decorate 时从 Context 取
const AtomicRenderContext = React.createContext<
  (p: { paraIndex: number; kind: 'table' | 'image'; html: string }) => ReactNode
>(() => null);

/** 表格/图片只读原子块：contenteditable=false，不可编辑、不可改写 */
export class AtomicBlockNode extends DecoratorNode<ReactNode> {
  __paraIndex: number;
  __kind: 'table' | 'image';
  __html: string;

  constructor(
    paraIndex: number,
    kind: 'table' | 'image',
    html: string,
    key?: NodeKey,
  ) {
    super(key);
    this.__paraIndex = paraIndex;
    this.__kind = kind;
    this.__html = html;
  }

  static getType(): string {
    return 'atomic-block';
  }

  static clone(node: AtomicBlockNode): AtomicBlockNode {
    return new AtomicBlockNode(
      node.__paraIndex,
      node.__kind,
      node.__html,
      node.__key,
    );
  }

  static importJSON(json: Record<string, unknown>): AtomicBlockNode {
    return new AtomicBlockNode(
      (json.paraIndex as number) ?? -1,
      (json.kind as 'table' | 'image') || 'table',
      (json.html as string) || '',
    );
  }

  exportJSON(): Record<string, unknown> {
    return {
      type: 'atomic-block',
      version: 1,
      paraIndex: this.__paraIndex,
      kind: this.__kind,
      html: this.__html,
    };
  }

  createDOM(): HTMLElement {
    const div = document.createElement('div');
    div.setAttribute('data-para-index', String(this.__paraIndex));
    div.contentEditable = 'false';
    div.className = 'relative py-0.5';
    return div;
  }

  updateDOM(): false {
    return false;
  }

  decorate(): ReactNode {
    return (
      <AtomicRenderContext.Consumer>
        {(render) =>
          render({ paraIndex: this.__paraIndex, kind: this.__kind, html: this.__html })
        }
      </AtomicRenderContext.Consumer>
    );
  }
}

// ── 模型抽取与 diff ─────────────────────────────────────────

export function readEditorBlocks(editor: LexicalEditor): EditorBlock[] {
  return editor.read(() =>
    $getRoot().getChildren().map((child): EditorBlock => {
      if (child instanceof AtomicBlockNode) {
        return { paraIndex: child.__paraIndex, kind: child.__kind, text: '' };
      }
      const paraIndex =
        child instanceof DocxParagraphNode || child instanceof DocxHeadingNode
          ? child.__paraIndex
          : undefined;
      return { paraIndex, kind: 'text', text: child.getTextContent() };
    }),
  );
}

export function collectEditorOps(
  editor: LexicalEditor,
  paragraphs: DocxSourceParagraph[],
): DocxDiffOps {
  return diffBlocks(readEditorBlocks(editor), paragraphs);
}

// ── 插件 ────────────────────────────────────────────────────

/** 批注高亮：targetsByPara 变化时（挂载/标注批注增删）重建高亮片段；
 * 打字过程绝不重拆，避免光标跳动 */
function HighlightPlugin({
  targetsByPara,
}: {
  targetsByPara: Map<number, Array<{ text: string; color: string; key: string }>>;
}) {
  const [editor] = useLexicalComposerContext();
  useEffect(() => {
    editor.update(
      () => {
        const root = $getRoot();
        for (const child of root.getChildren()) {
          const paraIndex =
            child instanceof DocxParagraphNode || child instanceof DocxHeadingNode
              ? child.__paraIndex
              : null;
          const targets = paraIndex != null ? targetsByPara.get(paraIndex) : undefined;
          if (!targets || !targets.length) continue;
          const segments = splitIntoSegments(child.getTextContent(), targets);
          child.clear();
          for (const seg of segments) {
            if (seg.key) {
              const node = new HighlightTextNode(seg.text, seg.key);
              node.setStyle(`background-color:${seg.color}22;`);
              child.append(node);
            } else {
              child.append($createTextNode(seg.text));
            }
          }
        }
      },
      { tag: 'history-merge' },
    );
  }, [editor, targetsByPara]);
  return null;
}

/** 点击高亮 → 联动右侧卡片（复用 review-panel 的 handleAnchorClick） */
function ClickPlugin({ onAnchorClick }: { onAnchorClick: (key: string) => void }) {
  const [editor] = useLexicalComposerContext();
  useEffect(() => {
    return editor.registerCommand(
      CLICK_COMMAND,
      (event: MouseEvent) => {
        const target = event.target as HTMLElement | null;
        const el = target?.closest('[data-anchor-key]');
        if (el) {
          event.preventDefault();
          onAnchorClick(el.getAttribute('data-anchor-key') || '');
          return true;
        }
        return false;
      },
      COMMAND_PRIORITY_HIGH,
    );
  }, [editor, onAnchorClick]);
  return null;
}

/** 粘贴降级纯文本：换行替换为空格（单段语义，防外部富文本破坏结构） */
function PastePlugin() {
  const [editor] = useLexicalComposerContext();
  useEffect(() => {
    return editor.registerCommand(
      PASTE_COMMAND,
      (event: ClipboardEvent) => {
        const text = event.clipboardData?.getData('text/plain');
        if (text == null) return false;
        event.preventDefault();
        editor.update(() => {
          const selection = $getSelection();
          if (selection) selection.insertText(text.replace(/\r?\n/g, ' '));
        });
        return true;
      },
      COMMAND_PRIORITY_HIGH,
    );
  }, [editor]);
  return null;
}

/** 脏检查：任何编辑器更新后抽块描述抛给父级（父级防抖后 diff 计数） */
function DirtyPlugin({ onBlocksChange }: { onBlocksChange: (blocks: EditorBlock[]) => void }) {
  const [editor] = useLexicalComposerContext();
  useEffect(() => {
    return editor.registerUpdateListener(() => {
      onBlocksChange(readEditorBlocks(editor));
    });
  }, [editor, onBlocksChange]);
  return null;
}

/** 把 LexicalEditor 实例抛给父级（保存时调 collectEditorOps 用） */
function EditorRefPlugin({
  editorRef,
}: {
  editorRef: { current: LexicalEditor | null };
}) {
  const [editor] = useLexicalComposerContext();
  useEffect(() => {
    editorRef.current = editor;
    return () => {
      editorRef.current = null;
    };
  }, [editor, editorRef]);
  return null;
}

// ── 组件 ────────────────────────────────────────────────────

function buildInitialContent(editor: LexicalEditor, paragraphs: DocxSourceParagraph[]) {
  editor.update(() => {
    const root = $getRoot();
    for (const para of paragraphs) {
      if (para.type === 'table' || para.type === 'image') {
        root.append(new AtomicBlockNode(para.index, para.type, para.text));
      } else if (para.type === 'heading') {
        const tag = (
          para.heading_level && para.heading_level <= 3
            ? `h${para.heading_level + 1}`
            : 'h3'
        ) as HeadingTagType;
        const h = new DocxHeadingNode(tag, para.index);
        h.append($createTextNode(para.text));
        root.append(h);
      } else {
        const p = new DocxParagraphNode(para.index);
        p.append($createTextNode(para.text));
        root.append(p);
      }
    }
  });
}

export default function DocxParagraphEditor({
  paragraphs,
  targetsByPara,
  onAnchorClick,
  renderAtomic,
  editorRef,
  onBlocksChange,
}: {
  paragraphs: DocxSourceParagraph[];
  targetsByPara: Map<number, Array<{ text: string; color: string; key: string }>>;
  onAnchorClick: (key: string) => void;
  renderAtomic: (p: { paraIndex: number; kind: 'table' | 'image'; html: string }) => ReactNode;
  editorRef: { current: LexicalEditor | null };
  onBlocksChange: (blocks: EditorBlock[]) => void;
}) {
  // paragraphs/targetsByPara 固定于挂载时刻；文档刷新/放弃修改由父级换 key 重挂载
  const initialConfig = useMemo<InitialConfigType>(
    () => ({
      namespace: 'docx-review-editor',
      nodes: [
        DocxParagraphNode,
        DocxHeadingNode,
        AtomicBlockNode,
        HighlightTextNode,
        HeadingNode,
        ParagraphNode,
      ],
      theme: {},
      onError: (error: Error) => console.error('[docx-editor]', error),
      initialEditorState: (editor: LexicalEditor) => {
        buildInitialContent(editor, paragraphs);
      },
    }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [],
  );

  return (
    <LexicalComposer initialConfig={initialConfig}>
      <AtomicRenderContext.Provider value={renderAtomic}>
        <RichTextPlugin
          contentEditable={
            <ContentEditable className="space-y-2 outline-none focus:outline-none" />
          }
          placeholder={null}
          ErrorBoundary={LexicalErrorBoundary}
        />
        <LexicalHistoryPlugin />
        <HighlightPlugin targetsByPara={targetsByPara} />
        <ClickPlugin onAnchorClick={onAnchorClick} />
        <PastePlugin />
        <DirtyPlugin onBlocksChange={onBlocksChange} />
        <EditorRefPlugin editorRef={editorRef} />
      </AtomicRenderContext.Provider>
    </LexicalComposer>
  );
}
```

补充：文件顶部还需要 `import React from 'react';`（`React.createContext` 引用）与 `$getSelection`（PastePlugin 用）——把 `lexical` 的 import 列表加上 `$getSelection`，react import 改为 `import React, { useEffect, useMemo } from 'react';`。

- [ ] **Step 2: 类型检查**

Run: `cd web && npx tsc --noEmit 2>&1 | grep "docx-paragraph-editor"; echo done`
Expected: 只输出 `done`

- [ ] **Step 3: Commit**

```bash
git add web/src/pages/c-chat/docx-paragraph-editor.tsx
git commit -m "feat(flow): Lexical 文档编辑器组件（4 自定义节点+插件+模型 diff）"
```

---

### Task 4: review-panel 接入编辑器

canEdit 分支替换为编辑器；删除 DOM diff（collectPaperOps/handleDocInput/paperRef）；保存链路改模型 diff。

**Files:**
- Modify: `web/src/pages/c-chat/review-panel.tsx`

- [ ] **Step 1: imports 调整**

```ts
import type { LexicalEditor } from 'lexical';   // 新增（类型）
import DocxParagraphEditor, { collectEditorOps } from './docx-paragraph-editor';
import { diffBlocks, EditorBlock } from './docx-diff';
```

- [ ] **Step 2: state 调整（组件体内）**

删除：
```ts
const paperRef = useRef<HTMLDivElement>(null);
const handleDocInput = useCallback(...)   // 整个函数删除
const collectPaperOps = useCallback(...)  // 整个函数删除
```
新增（原 paperRef 位置）：
```ts
const editorRef = useRef<LexicalEditor | null>(null);
```

- [ ] **Step 3: 新增 handleEditorDirty（替代 handleDocInput，放原 handleDocInput 位置）**

```ts
// 编辑器内容变化 → 防抖后 diff 出改动处数（模型级，不碰 DOM）
const handleEditorDirty = useCallback(
  (blocks: EditorBlock[]) => {
    if (!canEdit || !content) return;
    window.clearTimeout(diffTimer.current);
    diffTimer.current = window.setTimeout(() => {
      const ops = diffBlocks(blocks, content.paragraphs);
      if ('error' in ops) {
        setDirty(0);
        setEditError(ops.error || '当前改动无法保存');
        return;
      }
      setDirty(ops.count);
    }, 250);
  },
  [canEdit, content],
);
```

- [ ] **Step 4: handleSaveEdits 改为模型 diff**

```ts
// 保存：模型 diff 全部改动提交父级写新版本，成功后由新内容重挂载编辑器
const handleSaveEdits = useCallback(async () => {
  if (!onEditDocument || savingEdits || !editorRef.current || !content) return;
  const ops = collectEditorOps(editorRef.current, content.paragraphs);
  if ('error' in ops) {
    setEditError(ops.error || '当前改动无法保存');
    return;
  }
  if (!ops.count) {
    setDirty(0);
    return;
  }
  setSavingEdits(true);
  setEditError('');
  try {
    await onEditDocument(ops);
    setDirty(0);
  } catch (e: any) {
    setEditError(e?.message || '保存失败，请稍后重试');
  } finally {
    setSavingEdits(false);
  }
}, [content, onEditDocument, savingEdits]);
```

- [ ] **Step 5: 新增 renderAtomicBlock（放 renderAtomic 相关位置，handleSelectTableAnn 定义之后）**

```ts
// 编辑器内表格/图片原子块的渲染（与静态分支同一套高亮/批注逻辑）
const renderAtomicBlock = useCallback(
  ({ paraIndex, kind, html }: { paraIndex: number; kind: 'table' | 'image'; html: string }) => {
    if (kind === 'image') {
      return <div className="py-1 text-[13px] italic text-[#8A8A8A]">{html}</div>;
    }
    const firstAi = (railByPara.get(paraIndex) || []).find((i) => i.kind === 'ai');
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
        it.comment?.anchor_start,
        it.color,
        it.key,
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

- [ ] **Step 6: JSX 替换——纸张 div 变普通容器 + 条件渲染编辑器/静态**

纸张外层 div（原带 `key/ref/contentEditable/suppressContentEditableWarning/spellCheck/onInput`）改为普通容器：

```tsx
<div
  className="mx-auto w-full max-w-[794px] border border-[#C9C9C9] bg-white px-[72px] py-[64px] shadow-[0_4px_24px_rgba(0,0,0,0.14)]"
  style={{ fontFamily: "'SimSun', '宋体', 'Times New Roman', serif" }}
>
  {canEdit && onEditDocument ? (
    <DocxParagraphEditor
      key={`${fileId}-${resetKey}`}
      paragraphs={content.paragraphs}
      targetsByPara={targetsByPara}
      onAnchorClick={handleAnchorClick}
      renderAtomic={renderAtomicBlock}
      editorRef={editorRef}
      onBlocksChange={handleEditorDirty}
    />
  ) : (
    <div className="space-y-2">
      {content.paragraphs.map((para) => { /* 原有静态渲染 map 原样保留 */ })}
    </div>
  )}
</div>
```

静态分支清理：段落 map 原样保留，但删除表格 div 与 image div 上的 `contentEditable={canEdit ? false : undefined}`（静态分支永远只读）。

- [ ] **Step 7: 类型检查**

Run: `cd web && npx tsc --noEmit 2>&1 | grep -E "review-panel|docx-paragraph-editor|docx-diff"; echo done`
Expected: 只输出 `done`

- [ ] **Step 8: Commit**

```bash
git add web/src/pages/c-chat/review-panel.tsx
git commit -m "feat(flow): 文件审核可编辑模式接入 Lexical 编辑器，删除手写 DOM diff"
```

---

### Task 5: 浏览器 E2E 验证（dev 9222）

**Files:** 无代码改动（发现问题则修复后重新 commit）

- [ ] **Step 1: 打开页面** — `http://localhost:9222/home` → 顶部「流程」→ 打开「测试2」→ 点「文件审核」
- [ ] **Step 2: 编辑器挂载断言**（browser_evaluate）

```js
() => {
  const root = document.querySelector('[data-lexical-editor]');
  return {
    hasEditor: !!root,
    paras: document.querySelectorAll('[data-para-index]').length,
    highlights: document.querySelectorAll('[data-anchor-key]').length,
  };
}
```
Expected: `hasEditor: true`，paras 与原文档段落数一致，highlights ≥ 0

- [ ] **Step 3: 三类编辑**（browser_evaluate，execCommand 走 beforeinput 通道）
  1. 改字：光标置某段末尾 `execCommand('insertText', false, '（Lexical测试）')`
  2. 加段：`execCommand('insertParagraph')` 后 `insertText` 新段文字
  3. 并段：跨两段设 Range 后 `execCommand('delete')`
- [ ] **Step 4: 撤销/重做** — `browser_press_key` `Control+z` 两次再 `Control+Shift+z` 两次，断言文本回退又恢复
- [ ] **Step 5: 等防抖 ≥1s，断言吸顶栏「已修改 N 处」出现；点击「保存」**
- [ ] **Step 6: 断言保存成功** — 保存栏消失、无 editError；页面 fetch `/api/v1/flow/<id>` 确认新版本 `source=manual_edit`、version_no 递增
- [ ] **Step 7: 下载新版本**（fetch + base64 落盘 .scratch）→ `python -c` 用 python-docx 核验三类操作落盘正确
- [ ] **Step 8: 回归断言** — 只读模式（非负责人流程或手动附件）仍是静态渲染（无 `[data-lexical-editor]`）；编辑模式选字后悬浮「添加批注」按钮可出现并提交
- [ ] **Step 9: 发现的 bug 修复后单独 commit**

---

### Task 6: CHANGE.md + 收尾

**Files:**
- Modify: `CHANGE.md`
- Modify: `CLAUDE.md`（参考表中 CHANGE.md 一句话简介如需更新）

- [ ] **Step 1: CHANGE.md 顶部追加条目**（日期 2026-08-31，主题「文件审核：Lexical 编辑器替换手写 contentEditable」；核心变更 4-5 条：编辑器组件、diff 模型级迁移、撤销/IME 收益、表格辅助函数抽共享、E2E 结果；遗留：run 级格式仍不保真、生产 dist 未部署）

- [ ] **Step 2: 按全局收尾规则输出四项总结（完成/遗留/需要你做/效果）**

- [ ] **Step 3: 禁止自动部署** — 后端零改动无需 SCP；前端生产构建等用户明确指示

---

## 自审记录

- **Spec 覆盖**：§3 四节点+组件（Task 3）、§4 渲染分工（Task 4 条件分支，只读零改动）、§5 高亮/点击/选字/引线/粘贴（Task 3 插件 + Task 4 不动现有 mouseUp 逻辑）、§6 diff 细则（Task 2+3）、§7 错误处理（handleEditorDirty/handleSaveEdits 的 error 分支 + RichTextPlugin 自带 LexicalErrorBoundary）、§8 测试（Task 2 单测 + Task 5 E2E）、§9 文件清单一致（spec 中 docx-view-utils.tsx 落地为 .ts，无 JSX）
- **占位符**：无 TBD/「类似 Task N」；Task 4 Step 6 静态分支标注「原样保留」指现有代码不动，非占位
- **类型一致性**：`EditorBlock`/`DocxDiffOps`/`DocxSourceParagraph` 定义于 docx-diff.ts，Task 3/4 引用一致；`collectEditorOps(editor, paragraphs)` 签名 Task 3 导出 = Task 4 调用；`renderAtomic` 参数 `{paraIndex, kind, html}` Task 3 Context = Task 4 闭包一致
