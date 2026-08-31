# 文件审核 Word 工具栏（格式保真落盘）— 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 文件审核弹框增加 Word ribbon 简化风工具栏（全面控件 + 高级质感），格式（粗/斜/颜色/字号/字体/对齐等）保存后经扩展契约写入 docx 新版本。

**Architecture:** 前端新增 docx-format-utils（纯函数）+ docx-toolbar（LexicalComposer 内 portal 到吸顶容器）；readEditorBlocks 抽 runs、diffBlocks 升级「文本+格式签名」双比较；后端 /document/edit 增加可选 runs 字段，python-docx 逐 run 写入（含 w:eastAsia 中文字体），无 runs 走原整段替换路径，契约向后兼容。

**Tech Stack:** lexical 0.23.1 + @lexical/react（已有）；@lexical/list 需确认（Task 3 Step 1 校验，缺则装 0.23.1）；shadcn dropdown-menu/popover/tooltip（已有）；python-docx（后端已有）。

**设计文档:** `docs/superpowers/specs/2026-08-31-docx-word-toolbar-design.md`

**约束提醒:**
- 只读静态渲染分支不动、不出现工具栏
- 前端单测跑法：`cd web && npx jest --config ../.scratch/jest-docx-diff.config.js <测试文件>`（web 全局 jest 配置损坏，既有问题）
- 禁止自动部署：Task 7 部署前必须获用户明确确认
- Lexical 已知差异：`@lexical/react/LexicalHistoryPlugin` 导出名是 `HistoryPlugin`；TextNode 格式判断用 `hasFormat('bold')` 等字符串 API，不用位常量

---

### Task 1: 格式纯函数 docx-format-utils.ts（TDD）

**Files:**
- Create: `web/src/pages/c-chat/docx-format-utils.ts`
- Test: `web/src/pages/c-chat/docx-format-utils.test.ts`

- [ ] **Step 1: 写失败测试**

```ts
// web/src/pages/c-chat/docx-format-utils.test.ts
import { mergeStyle, parseStyle, runsFmtSig, stripBgFromStyle } from './docx-format-utils';

describe('parseStyle', () => {
  it('空串/undefined → 空对象', () => {
    expect(parseStyle('')).toEqual({});
    expect(parseStyle(undefined as any)).toEqual({});
  });
  it('解析 font-size/color/background-color', () => {
    expect(parseStyle('font-size: 14pt;color:#FF0000;background-color: #FFF100;')).toEqual({
      'font-size': '14pt',
      color: '#FF0000',
      'background-color': '#FFF100',
    });
  });
  it('容忍多余分号与非法片段', () => {
    expect(parseStyle('color:red;; ;;font-weight: bold')).toEqual({
      color: 'red',
      'font-weight': 'bold',
    });
  });
});

describe('mergeStyle', () => {
  it('设置新键、覆盖已有键、删除键', () => {
    expect(mergeStyle('color:#111;font-size:12pt', { color: '#222' }, ['font-size'])).toBe(
      'color:#222',
    );
  });
  it('patch 与 remove 同键时以 remove 为准', () => {
    expect(mergeStyle('color:#111', { color: '#222' }, ['color'])).toBe('');
  });
});

describe('stripBgFromStyle', () => {
  it('剔除 background-color，其余保留（批注高亮 UI 色不落盘）', () => {
    expect(stripBgFromStyle('background-color:#FFF1B8;color:#333')).toBe('color:#333');
  });
});

describe('runsFmtSig', () => {
  it('相同样式序列 → 相同签名；文本不同不影响签名', () => {
    const a = [{ text: '甲', bold: true, color: '#FF0000' }, { text: '乙' }];
    const b = [{ text: '丙', bold: true, color: '#FF0000' }, { text: '丁' }];
    expect(runsFmtSig(a)).toBe(runsFmtSig(b));
  });
  it('样式不同 → 签名不同', () => {
    const a = [{ text: 'x', bold: true }];
    const b = [{ text: 'x', italic: true }];
    expect(runsFmtSig(a)).not.toBe(runsFmtSig(b));
  });
  it('undefined → undefined', () => {
    expect(runsFmtSig(undefined)).toBeUndefined();
  });
});
```

- [ ] **Step 2: 运行确认失败**

Run: `cd web && npx jest --config ../.scratch/jest-docx-diff.config.js src/pages/c-chat/docx-format-utils.test.ts 2>&1 | tail -4`
Expected: FAIL（Cannot find module './docx-format-utils'）

- [ ] **Step 3: 实现**

```ts
// web/src/pages/c-chat/docx-format-utils.ts
// 文档格式纯函数：Lexical TextNode style 串的解析/合并/剔除，与 runs
// 格式签名（diff 判断「纯改格式」用）。零依赖，可单测。

export interface DocxRun {
  text: string;
  bold?: boolean;
  italic?: boolean;
  underline?: boolean;
  strike?: boolean;
  superscript?: boolean;
  subscript?: boolean;
  /** #RRGGBB */
  color?: string;
  /** #RRGGBB，run 背景底纹 */
  bg_color?: string;
  /** 字体族名（同时写入 ascii/eastAsia） */
  font?: string;
  /** pt 值 */
  size?: number;
}

/** 解析 CSS 内联 style 串 → 键值对（键全小写，值 trim） */
export function parseStyle(style: string): Record<string, string> {
  const out: Record<string, string> = {};
  if (!style) return out;
  for (const decl of style.split(';')) {
    const i = decl.indexOf(':');
    if (i <= 0) continue;
    const key = decl.slice(0, i).trim().toLowerCase();
    const val = decl.slice(i + 1).trim();
    if (key && val) out[key] = val;
  }
  return out;
}

/** 序列化键值对 → style 串 */
function stringifyStyle(map: Record<string, string>): string {
  return Object.entries(map)
    .map(([k, v]) => `${k}:${v}`)
    .join(';');
}

/** 在原 style 基础上设置 set 中的键、删除 removeKeys 中的键（remove 优先） */
export function mergeStyle(
  style: string,
  set: Record<string, string>,
  removeKeys: string[] = [],
): string {
  const map = parseStyle(style);
  for (const k of removeKeys) delete map[k];
  for (const [k, v] of Object.entries(set)) {
    if (v) map[k] = v;
    else delete map[k];
  }
  return stringifyStyle(map);
}

/** 剔除 background-color（批注高亮 UI 色不参与落盘导出） */
export function stripBgFromStyle(style: string): string {
  return mergeStyle(style, {}, ['background-color']);
}

/** runs 样式签名（不含 text）：文本相同时比较签名判断「纯改格式」 */
export function runsFmtSig(runs: DocxRun[] | undefined): string | undefined {
  if (!runs) return undefined;
  return JSON.stringify(
    runs.map((r) => {
      const { text: _text, ...rest } = r;
      return rest;
    }),
  );
}
```

- [ ] **Step 4: 运行确认通过**

Run: `cd web && npx jest --config ../.scratch/jest-docx-diff.config.js src/pages/c-chat/docx-format-utils.test.ts 2>&1 | tail -4`
Expected: PASS（全部用例绿）

- [ ] **Step 5: Commit**

```bash
git add web/src/pages/c-chat/docx-format-utils.ts web/src/pages/c-chat/docx-format-utils.test.ts
git commit -m "feat(flow): 格式纯函数 docx-format-utils（style 解析/合并 + runs 签名）+ 单测"
```

---

### Task 2: docx-diff.ts 扩展 runs 与双比较（TDD）

**Files:**
- Modify: `web/src/pages/c-chat/docx-diff.ts`
- Test: `web/src/pages/c-chat/docx-diff.test.ts`

- [ ] **Step 1: 追加失败测试（追加到文件末尾的 describe 之后）**

```ts
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
      { paraIndex: 0, kind: 'text' as const, text: '原文', runs, fmtSig: '[{"bold":true}]' },
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
    expect('error' in ops).toBe(false);
    expect(ops.count).toBe(0);
  });

  it('文本+格式同时变化 → edit 带 runs', () => {
    const paragraphs = [para(0, '原文')];
    const runs = [{ text: '改后文字', color: '#FF0000' }];
    const blocks = [
      { paraIndex: 0, kind: 'text' as const, text: '改后文字', runs, fmtSig: '[{"color":"#FF0000"}]' },
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
```

同时在文件顶部 import 处补 `type DocxRun`（实现后）——测试本身只引用字面量对象，无需改 import。

- [ ] **Step 2: 运行确认失败**

Run: `cd web && npx jest --config ../.scratch/jest-docx-diff.config.js src/pages/c-chat/docx-diff.test.ts 2>&1 | tail -4`
Expected: FAIL（新用例不通过：edits 项无 runs 字段 / 纯格式变化不产生 edit）

- [ ] **Step 3: 实现（docx-diff.ts 修改）**

顶部新增 import 与类型：

```ts
import type { DocxRun } from './docx-format-utils';
```

`EditorBlock` 扩展：

```ts
export interface EditorBlock {
  paraIndex?: number;
  kind: 'text' | 'table' | 'image';
  text: string;
  /** run 级格式抽取结果；undefined = 全默认格式（保持旧整段替换行为） */
  runs?: DocxRun[];
  /** runsFmtSig(runs) 缓存，文本相同但签名不同 = 纯改格式 */
  fmtSig?: string;
}
```

`DocxDiffOps` 的 edits/inserts 项扩展 `runs?: DocxRun[]`：

```ts
export type DocxDiffOps =
  | { error: string }
  | {
      error?: undefined;
      edits: Array<{ paraIndex: number; newText: string; runs?: DocxRun[] }>;
      deletes: number[];
      inserts: Array<{ afterParaIndex: number; newText: string; runs?: DocxRun[] }>;
      count: number;
    };
```

`diffBlocks` 循环体中，edit 判断改为双比较（原 `else if (text !== orig.text.trim())` 分支）：

```ts
      const text = b.text.trim();
      if (!text) {
        deletes.push(idx);
      } else if (text !== orig.text.trim()) {
        edits.push({ paraIndex: idx, newText: text, runs: b.runs });
      } else if (b.fmtSig && b.fmtSig !== JSON.stringify([{ text: orig.text.trim() }]) && b.fmtSig !== '[]') {
        // 纯改格式：文本相同但样式签名与「无格式」基线不同
        edits.push({ paraIndex: idx, newText: text, runs: b.runs });
      }
```

insert 分支：

```ts
      if (text) {
        inserts.push({
          afterParaIndex: lastIdx == null ? -1 : lastIdx,
          newText: text,
          runs: b.runs,
        });
      }
```

- [ ] **Step 4: 运行确认全部通过（含旧 14 用例）**

Run: `cd web && npx jest --config ../.scratch/jest-docx-diff.config.js src/pages/c-chat/docx-diff.test.ts 2>&1 | tail -4`
Expected: PASS（旧 14 + 新 4 = 18 用例全绿）

- [ ] **Step 5: 类型检查**

Run: `cd web && npx tsc --noEmit 2>&1 | grep -E "docx-diff|docx-format"; echo done`
Expected: 只输出 done。注意：readEditorBlocks 尚未产出 runs，编译应无错（字段可选）。

- [ ] **Step 6: Commit**

```bash
git add web/src/pages/c-chat/docx-diff.ts web/src/pages/c-chat/docx-diff.test.ts
git commit -m "feat(flow): diff 升级「文本+格式签名」双比较，edits/inserts 携带 runs"
```

---

### Task 3: readEditorBlocks 抽 runs + 块类型替换助手（docx-paragraph-editor.tsx）

**Files:**
- Modify: `web/src/pages/c-chat/docx-paragraph-editor.tsx`
- 可能新增依赖：`@lexical/list`（Step 1 校验）

- [ ] **Step 1: 校验 @lexical/list 可用性**

Run: `ls web/node_modules/@lexical/list/package.json && node -e "console.log(require('D:/AI/ragflow2/web/node_modules/@lexical/list/package.json').version)"`
Expected: 存在且 0.23.x。

若不存在：`cd web && npm i @lexical/list@0.23.1 --save`（若网络超时先 `export https_proxy=socks5://127.0.0.1:10808`）。

- [ ] **Step 2: import 调整**

文件顶部新增：

```ts
import {
  INSERT_ORDERED_LIST_COMMAND,
  INSERT_UNORDERED_LIST_COMMAND,
  ListItemNode,
  ListNode,
} from '@lexical/list';
import { $isElementNode, $isRootNode, $isTextNode } from 'lexical';
import { mergeStyle, parseStyle, stripBgFromStyle, DocxRun } from './docx-format-utils';
```

- [ ] **Step 3: initialConfig.nodes 注册列表节点**

`initialConfig`（`DocxParagraphEditor` 组件内）的 `nodes` 数组追加 `ListNode, ListItemNode`。

- [ ] **Step 4: runs 抽取函数（放 readEditorBlocks 上方）**

```ts
/** 抽取一个文本块的 run 序列：format 位 + style 串 → DocxRun；
 * HighlightTextNode 的 background 是批注 UI 色不落盘（其余 format 正常）；
 * 相邻同样式 run 合并；全默认格式返回 undefined（保持旧整段替换行为） */
function $extractRuns(block: ElementNode): DocxRun[] | undefined {
  const runs: DocxRun[] = [];
  const visit = (n: LexicalNode) => {
    if ($isElementNode(n)) {
      for (const c of n.getChildren()) visit(c);
      return;
    }
    if (!$isTextNode(n)) return;
    const isHl = n instanceof HighlightTextNode;
    const styleMap = parseStyle(isHl ? stripBgFromStyle(n.getStyle()) : n.getStyle());
    const run: DocxRun = { text: n.getTextContent() };
    if (n.hasFormat('bold')) run.bold = true;
    if (n.hasFormat('italic')) run.italic = true;
    if (n.hasFormat('underline')) run.underline = true;
    if (n.hasFormat('strikethrough')) run.strike = true;
    if (n.hasFormat('superscript')) run.superscript = true;
    if (n.hasFormat('subscript')) run.subscript = true;
    if (styleMap['color']) run.color = styleMap['color'];
    if (styleMap['background-color']) run.bg_color = styleMap['background-color'];
    if (styleMap['font-family']) run.font = styleMap['font-family'];
    if (styleMap['font-size']) {
      const pt = parseFloat(styleMap['font-size']);
      if (Number.isFinite(pt)) run.size = pt;
    }
    runs.push(run);
  };
  for (const c of block.getChildren()) visit(c);

  const merged: DocxRun[] = [];
  for (const r of runs) {
    if (!r.text) continue;
    const prev = merged[merged.length - 1];
    const styleOf = (x: DocxRun) => JSON.stringify({ ...x, text: undefined });
    if (prev && styleOf(prev) === styleOf(r)) prev.text += r.text;
    else merged.push({ ...r });
  }
  const hasFmt = merged.some((r) => Object.keys(r).some((k) => k !== 'text'));
  return hasFmt ? merged : undefined;
}
```

- [ ] **Step 5: readEditorBlocks 接入 runs**

文本块返回处改为：

```ts
        return {
          paraIndex,
          kind: 'text',
          text: child.getTextContent(),
          runs: $extractRuns(child),
          fmtSig: undefined as unknown as never, // 占位防误用，下方立即覆盖
        };
```

随后把 map 改为普通 for 循环更清晰（替换整个 readEditorBlocks 实现）：

```ts
export function readEditorBlocks(editor: LexicalEditor): EditorBlock[] {
  return editor.read(() => {
    const out: EditorBlock[] = [];
    for (const child of $getRoot().getChildren()) {
      if (child instanceof AtomicBlockNode) {
        out.push({ paraIndex: child.__paraIndex, kind: child.__kind, text: '' });
        continue;
      }
      const paraIndex =
        child instanceof DocxParagraphNode || child instanceof DocxHeadingNode
          ? child.__paraIndex
          : undefined;
      const runs = $extractRuns(child);
      out.push({
        paraIndex,
        kind: 'text',
        text: child.getTextContent(),
        runs,
        fmtSig: runs ? JSON.stringify(runs.map((r) => ({ ...r, text: '' }))) : undefined,
      });
    }
    return out;
  });
}
```

注意 fmtSig 与 runsFmtSig 的签名算法必须一致：Task 1 的 `runsFmtSig` 是剔除 text 键，此处 `{...r, text: ''}` 留键但置空——两种 JSON 不相等。**统一改为调用 `runsFmtSig(runs)`**（import 自 docx-format-utils），删除本函数内的手写实现：

```ts
        runs,
        fmtSig: runsFmtSig(runs),
```

（import 行补 `runsFmtSig`。）

- [ ] **Step 6: 块类型替换助手（放 readEditorBlocks 之后）**

```ts
/** 把选区涉及的顶级块替换为目标类型（正文↔标题），保留原 paraIndex 语义：
 * 改原文段标题层级仍 edit 原段；新增段保持无 index 记 insert */
export function $applyDocxBlockType(
  make: (paraIndex: number | undefined) => ElementNode,
): void {
  const selection = $getSelection();
  if (!$isRangeSelection(selection)) return;
  const blocks = new Set<ElementNode>();
  for (const node of selection.getNodes()) {
    let cur: LexicalNode | null = node;
    while (cur && !$isRootNode(cur.getParent())) cur = cur.getParent();
    if (cur && cur !== $getRoot()) blocks.add(cur);
  }
  for (const b of blocks) {
    const paraIndex =
      b instanceof DocxParagraphNode || b instanceof DocxHeadingNode
        ? b.__paraIndex
        : undefined;
    const nb = make(paraIndex);
    for (const c of b.getChildren()) nb.append(c);
    b.replace(nb);
  }
}
```

- [ ] **Step 7: 类型检查 + Commit**

Run: `cd web && npx tsc --noEmit 2>&1 | grep -E "docx-paragraph-editor"; echo done`
Expected: 只输出 done。

```bash
git add web/src/pages/c-chat/docx-paragraph-editor.tsx web/package.json web/package-lock.json
git commit -m "feat(flow): 编辑器抽取 run 级格式 + 块类型替换助手 + 列表节点注册"
```

---

### Task 4: 工具栏组件 docx-toolbar.tsx

**Files:**
- Create: `web/src/pages/c-chat/docx-toolbar.tsx`

- [ ] **Step 1: 写入完整文件**

复用 shadcn 标准导出（实现前先读 `web/src/components/ui/dropdown-menu.tsx`、`popover.tsx`、`tooltip.tsx`、`button.tsx` 确认导出名，均为 shadcn 标准 API）。

```tsx
// web/src/pages/c-chat/docx-toolbar.tsx
import { useLexicalComposerContext } from '@lexical/react/LexicalComposerContext';
import {
  FORMAT_ELEMENT_COMMAND,
  FORMAT_TEXT_COMMAND,
} from 'lexical';
import {
  $createTextNode,
  $getSelection,
  $isRangeSelection,
  COMMAND_PRIORITY_EDITOR,
  ElementNode,
  LexicalNode,
} from 'lexical';
import { INSERT_ORDERED_LIST_COMMAND, INSERT_UNORDERED_LIST_COMMAND } from '@lexical/list';
import { ReactNode, useEffect, useState } from 'react';
import { createPortal } from 'react-dom';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover';
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from '@/components/ui/tooltip';
import { ChevronDown } from 'lucide-react';
import { mergeStyle } from './docx-format-utils';
import {
  $applyDocxBlockType,
  DocxHeadingNode,
  DocxParagraphNode,
} from './docx-paragraph-editor';

// ── 常量 ──
const FONT_FAMILIES = ['宋体', '黑体', '楷体', '仿宋', '微软雅黑', 'Times New Roman', 'Arial'];
const FONT_SIZES: Array<{ label: string; pt: number }> = [
  { label: '初号 42', pt: 42 },
  { label: '小初 36', pt: 36 },
  { label: '一号 26', pt: 26 },
  { label: '二号 22', pt: 22 },
  { label: '小二 18', pt: 18 },
  { label: '三号 16', pt: 16 },
  { label: '四号 14', pt: 14 },
  { label: '小四 12', pt: 12 },
  { label: '五号 10.5', pt: 10.5 },
  { label: '九号 9', pt: 9 },
];
const TEXT_COLORS = ['#000000', '#595959', '#8C8C8C', '#FF4D4F', '#FA8C16', '#FADB14', '#52C41A', '#13C2C2', '#1890FF', '#722ED1'];
const BG_COLORS = ['#FFF1B8', '#FFA39E', '#B7EB8F', '#91D5FF', '#D3ADF7'];

// ── 小控件 ──
function TBtn({
  label, active, disabled, onClick, children,
}: {
  label: string; active?: boolean; disabled?: boolean; onClick: () => void; children: ReactNode;
}) {
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <button
          type="button"
          disabled={disabled}
          onMouseDown={(e) => e.preventDefault()} /* 防点击丢选区 */
          onClick={onClick}
          className={`flex h-7 min-w-7 items-center justify-center rounded px-1 text-[13px] transition-colors disabled:opacity-40 ${
            active
              ? 'bg-[#E8F0FF] text-[#1a66fb]'
              : 'text-[#444] hover:bg-[#F0F2F5]'
          }`}
        >
          {children}
        </button>
      </TooltipTrigger>
      <TooltipContent side="bottom" className="text-xs">{label}</TooltipContent>
    </Tooltip>
  );
}

function GroupDivider() {
  return <div className="mx-1 h-5 w-px shrink-0 bg-[#E5E5E5]" />;
}

// ── 主组件 ──
export default function DocxToolbar({
  portal,
  dirty,
  saving,
  onSave,
  onDiscard,
}: {
  /** 吸顶容器 DOM（review-panel 提供）；null 时不渲染 */
  portal: HTMLElement | null;
  dirty: number;
  saving: boolean;
  onSave: () => void;
  onDiscard: () => void;
}) {
  const [editor] = useLexicalComposerContext();
  const [fmt, setFmt] = useState(0);          // 当前选区 format 位
  const [blockKind, setBlockKind] = useState<'p' | 'h'>('p');
  const [align, setAlign] = useState('left');
  const [hasSel, setHasSel] = useState(false);

  useEffect(() => {
    // 选区/内容变化 → 刷新激活态（轻量，直接同步读）
    return editor.registerUpdateListener(({ editorState }) => {
      editorState.read(() => {
        const sel = $getSelection();
        if (!$isRangeSelection(sel)) {
          setHasSel(false);
          return;
        }
        setHasSel(true);
        setFmt(sel.format);
        setAlign(sel.format & 0b11111 ? ['left', 'center', 'right', 'justify'][(sel.format & 3) as number] : 'left');
        // 顶级块类型
        let cur: LexicalNode | null = sel.anchor.getNode();
        while (cur && !(cur instanceof ElementNode && cur.getParent()?.getType?.() === 'root')) {
          cur = cur.getParent();
        }
        setBlockKind(cur instanceof DocxHeadingNode ? 'h' : 'p');
      });
    });
  }, [editor]);

  // 历史/格式命令
  const cmd = (type: string, payload?: any) => editor.dispatchCommand(type as any, payload);
  const applyFont = (font: string) => editor.update(() => styleSel({ 'font-family': font }));
  const applySize = (pt: number) => editor.update(() => styleSel({ 'font-size': `${pt}pt` }));
  const applyColor = (c?: string) => editor.update(() => styleSel(c ? { color: c } : {}, ['color']));
  const applyBg = (c?: string) => editor.update(() => styleSel(c ? { 'background-color': c } : {}, ['background-color']));
  const clearFormat = () =>
    editor.update(() => {
      const sel = $getSelection();
      if (!$isRangeSelection(sel)) return;
      for (const n of sel.extract()) {
        const anyN = n as any;
        if (anyN.setStyle) anyN.setStyle('');
        if (anyN.setFormat) anyN.setFormat(0);
      }
      editor.dispatchCommand(FORMAT_ELEMENT_COMMAND, 'left');
    });

  if (!portal) return null;
  return createPortal(
    <TooltipProvider delayDuration={300}>
      <div className="flex flex-wrap items-center gap-0.5 rounded-lg border border-[#E5E7EB] bg-white px-2 py-1 shadow-[0_1px_6px_rgba(0,0,0,0.06)]">
        {/* 历史 */}
        <TBtn label="撤销 (Ctrl+Z)" onClick={() => cmd('UNDO_COMMAND')}>
          ↩
        </TBtn>
        <TBtn label="重做 (Ctrl+Shift+Z)" onClick={() => cmd('REDO_COMMAND')}>
          ↪
        </TBtn>
        <GroupDivider />
        {/* 块类型 */}
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <button
              type="button"
              onMouseDown={(e) => e.preventDefault()}
              className="flex h-7 items-center gap-0.5 rounded px-1.5 text-[13px] text-[#444] hover:bg-[#F0F2F5]"
            >
              {blockKind === 'h' ? '标题' : '正文'}
              <ChevronDown className="h-3 w-3 opacity-60" />
            </button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="start">
            <DropdownMenuItem onMouseDown={(e) => e.preventDefault()}
              onClick={() => editor.update(() => $applyDocxBlockType((i) => new DocxParagraphNode(i)))}>
              正文
            </DropdownMenuItem>
            {[2, 3, 4].map((lv) => (
              <DropdownMenuItem key={lv}
                onMouseDown={(e) => e.preventDefault()}
                onClick={() =>
                  editor.update(() =>
                    $applyDocxBlockType(
                      (i) => new DocxHeadingNode(`h${lv}` as any, i),
                    ),
                  )
                }>
              标题 {lv}
              </DropdownMenuItem>
            ))}
          </DropdownMenuContent>
        </DropdownMenu>
        <GroupDivider />
        {/* 字体/字号 */}
        <FontDropdown label="字体" items={FONT_FAMILIES} onPick={applyFont} />
        <FontDropdown label="字号" items={FONT_SIZES.map((s) => s.label)} onPick={(lb) => {
          const hit = FONT_SIZES.find((s) => s.label === lb);
          if (hit) applySize(hit.pt);
        }} />
        <GroupDivider />
        {/* B/I/U/S + 上下标 */}
        <TBtn label="加粗" active={!!(fmt & 1)} disabled={!hasSel} onClick={() => cmd(FORMAT_TEXT_COMMAND, 'bold')}>
          <b>B</b>
        </TBtn>
        <TBtn label="斜体" active={!!(fmt & 2)} disabled={!hasSel} onClick={() => cmd(FORMAT_TEXT_COMMAND, 'italic')}>
          <i>I</i>
        </TBtn>
        <TBtn label="下划线" active={!!(fmt & 8)} disabled={!hasSel} onClick={() => cmd(FORMAT_TEXT_COMMAND, 'underline')}>
          <u>U</u>
        </TBtn>
        <TBtn label="删除线" active={!!(fmt & 4)} disabled={!hasSel} onClick={() => cmd(FORMAT_TEXT_COMMAND, 'strikethrough')}>
          <s>S</s>
        </TBtn>
        <TBtn label="上标" active={!!(fmt & 64)} disabled={!hasSel} onClick={() => cmd(FORMAT_TEXT_COMMAND, 'superscript')}>
          x²
        </TBtn>
        <TBtn label="下标" active={!!(fmt & 32)} disabled={!hasSel} onClick={() => cmd(FORMAT_TEXT_COMMAND, 'subscript')}>
          x₂
        </TBtn>
        {/* 颜色 */}
        <ColorPopover label="字体颜色" colors={TEXT_COLORS} onPick={applyColor} />
        <ColorPopover label="背景高亮" colors={BG_COLORS} onPick={applyBg} />
        <GroupDivider />
        {/* 对齐 */}
        {([['left', '左对齐'], ['center', '居中'], ['right', '右对齐'], ['justify', '两端对齐']] as const).map(
          ([v, lb]) => (
            <TBtn key={v} label={lb} active={align === v} onClick={() => cmd(FORMAT_ELEMENT_COMMAND, v)}>
              {ALIGN_ICON[v]}
            </TBtn>
          ),
        )}
        <GroupDivider />
        {/* 列表/缩进/清除 */}
        <TBtn label="无序列表" onClick={() => cmd(INSERT_UNORDERED_LIST_COMMAND, undefined)}>
          • ≡
        </TBtn>
        <TBtn label="有序列表" onClick={() => cmd(INSERT_ORDERED_LIST_COMMAND, undefined)}>
          1. ≡
        </TBtn>
        <TBtn label="增加缩进" onClick={() => cmd(FORMAT_ELEMENT_COMMAND, 'indent')}>
          ⇒
        </TBtn>
        <TBtn label="减少缩进" onClick={() => cmd(FORMAT_ELEMENT_COMMAND, 'outdent')}>
          ⇐
        </TBtn>
        <TBtn label="清除格式" disabled={!hasSel} onClick={clearFormat}>
          ⌫ᶠ
        </TBtn>
        <div className="flex-1" />
        {/* 右侧：改动计数 + 保存/放弃 */}
        {dirty > 0 && (
          <span className="mx-1 rounded-full bg-[#F0F5FF] px-2 py-0.5 text-xs text-[#1a66fb]">
            已修改 {dirty} 处
          </span>
        )}
        <button
          type="button"
          disabled={saving || dirty === 0}
          onClick={onDiscard}
          className="h-7 rounded-md border border-[#D9D9D9] px-2.5 text-xs text-[#555] transition-colors hover:bg-[#F5F5F5] disabled:opacity-40"
        >
          放弃修改
        </button>
        <button
          type="button"
          disabled={saving || dirty === 0}
          onClick={onSave}
          className="h-7 rounded-md bg-[#1a66fb] px-3 text-xs font-medium text-white transition-colors hover:bg-[#0f56e0] disabled:opacity-40"
        >
          {saving ? '保存中…' : '保存'}
        </button>
      </div>
    </TooltipProvider>,
    portal,
  );
}

// ── 辅助 ──
const ALIGN_ICON: Record<string, string> = { left: '⯇', center: '≡', right: '⯈', justify: '☰' };

function FontDropdown({ label, items, onPick }: { label: string; items: string[]; onPick: (v: string) => void }) {
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <button
          type="button"
          onMouseDown={(e) => e.preventDefault()}
          className="flex h-7 items-center gap-0.5 rounded px-1.5 text-[13px] text-[#444] hover:bg-[#F0F2F5]"
        >
          {label}
          <ChevronDown className="h-3 w-3 opacity-60" />
        </button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start" className="max-h-72 overflow-auto">
        {items.map((it) => (
          <DropdownMenuItem key={it} onMouseDown={(e) => e.preventDefault()} onClick={() => onPick(it)}>
            {it}
          </DropdownMenuItem>
        ))}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

function ColorPopover({ label, colors, onPick }: { label: string; colors: string[]; onPick: (c?: string) => void }) {
  return (
    <Popover>
      <PopoverTrigger asChild>
        <button
          type="button"
          onMouseDown={(e) => e.preventDefault()}
          className="flex h-7 items-center gap-1 rounded px-1.5 text-[13px] text-[#444] hover:bg-[#F0F2F5]"
        >
          <span className="inline-block h-3.5 w-3.5 rounded-sm border border-[#ccc]" />
          {label}
        </button>
      </PopoverTrigger>
      <PopoverContent align="start" className="w-auto p-2">
        <div className="grid grid-cols-5 gap-1">
          {colors.map((c) => (
            <button
              key={c}
              type="button"
              onMouseDown={(e) => e.preventDefault()}
              onClick={() => onPick(c)}
              className="h-5 w-5 rounded-sm border border-[#0000001a] transition-transform hover:scale-110"
              style={{ backgroundColor: c }}
              title={c}
            />
          ))}
        </div>
        <button
          type="button"
          onMouseDown={(e) => e.preventDefault()}
          onClick={() => onPick(undefined)}
          className="mt-1.5 w-full rounded text-xs text-[#888] hover:bg-[#F5F5F5]"
        >
          清除
        </button>
      </PopoverContent>
    </Popover>
  );
}

/** 把 style patch 应用到选区内所有 TextNode（在 editor.update 内调用） */
function styleSel(set: Record<string, string>, removeKeys: string[] = []) {
  const sel = $getSelection();
  if (!$isRangeSelection(sel)) return;
  for (const n of sel.extract()) {
    const anyN = n as any;
    if (anyN.setStyle) anyN.setStyle(mergeStyle(anyN.getStyle(), set, removeKeys));
  }
}
```

**实现说明（写给实现者）**：
- `UNDO_COMMAND`/`REDO_COMMAND`/`FORMAT_TEXT_COMMAND`/`FORMAT_ELEMENT_COMMAND` 均为 `lexical` 具名导出，直接 import 替换 `cmd(...)` 中的字符串（除 `UNDO_COMMAND`/`REDO_COMMAND` 用 `editor.dispatchCommand(UNDO_COMMAND, undefined)`）。**禁止字符串命令名**——上面 `cmd('UNDO_COMMAND')` 写法仅示意布局，实现时改为 `editor.dispatchCommand(UNDO_COMMAND, undefined)`。
- format 位常量（1=bold, 2=italic, 4=strikethrough, 8=underline, 32=subscript, 64=superscript）来自 lexical `TEXT_TYPE_TO_FORMAT`；激活态如果对不上，改用 `editor.read(() => { const sel = $getSelection(); ... })` + `sel.anchor.getNode()` 取 TextNode 逐个 `hasFormat()` 求交集，以实测为准（E2E 会验证激活态）。
- 对齐激活态：RangeSelection 的 align 存在 `sel.format` 的高位段，取法以上面为准，E2E 校验；判断不出就先不亮对齐按钮（记为观察项，不阻塞）。
- 所有触发格式命令的 button 必须 `onMouseDown preventDefault`，否则点击瞬间选区丢失。
- `styleSel` 中 `sel.extract()` 返回选区切分后的节点数组，TextNode 才有 setStyle/getStyle——用鸭子判断 `anyN.setStyle`。

- [ ] **Step 2: 类型检查**

Run: `cd web && npx tsc --noEmit 2>&1 | grep "docx-toolbar"; echo done`
Expected: 只输出 done（组件尚未被引用，不应有错误）。

- [ ] **Step 3: Commit**

```bash
git add web/src/pages/c-chat/docx-toolbar.tsx
git commit -m "feat(flow): Word 工具栏组件（历史/块类型/字体字号/BIUSS/颜色/对齐/列表/保存区）"
```

---

### Task 5: review-panel 接线 + flow-service runs 透传

**Files:**
- Modify: `web/src/pages/c-chat/review-panel.tsx`
- Modify: `web/src/services/flow-service.ts`

- [ ] **Step 1: flow-service.ts 类型与透传**

找到 `FlowDocEditOps` 类型定义（`editFlowDocument` 上方），扩展 runs 字段：

```ts
export interface FlowDocRun {
  text: string;
  bold?: boolean;
  italic?: boolean;
  underline?: boolean;
  strike?: boolean;
  superscript?: boolean;
  subscript?: boolean;
  color?: string;
  bg_color?: string;
  font?: string;
  size?: number;
}
```

`FlowDocEditOps` 中 edits 项加 `runs?: FlowDocRun[]`、inserts 项加 `runs?: FlowDocRun[]`（读原类型按现有形状改）。

`editFlowDocument` 的 body 组装改为透传 runs（Run 字段已是 snake_case 契约，无需转换）：

```ts
      edits: ops.edits.map((e) => ({
        para_index: e.paraIndex,
        new_text: e.newText,
        ...(e.runs ? { runs: e.runs } : {}),
      })),
      inserts: ops.inserts.map((i) => ({
        after_para_index: i.afterParaIndex,
        new_text: i.newText,
        ...(i.runs ? { runs: i.runs } : {}),
      })),
```

- [ ] **Step 2: review-panel 吸顶容器改造**

原吸顶保存栏 JSX（`{dirty > 0 && (<div className="sticky top-0 ...">...保存/放弃按钮...</div>)}`，约 L1202-1228）整块替换为**始终渲染的吸顶容器**（编辑模式才有）：

```tsx
              {canEdit && onEditDocument && loadedFileId === fileId && (
                <div
                  ref={toolbarHostRef}
                  className="sticky top-0 z-10 mx-auto mb-2 max-w-[794px]"
                />
              )}
```

组件体内新增 ref（原 diffTimer 附近）：

```ts
  const [toolbarHost, setToolbarHost] = useState<HTMLElement | null>(null);
  const toolbarHostRef = useCallback((el: HTMLDivElement | null) => {
    setToolbarHost(el);
  }, []);
```

（用回调 ref + state 触发重渲染，保证 portal 拿到真实 DOM。）

- [ ] **Step 3: 编辑器挂载工具栏**

`<DocxParagraphEditor .../>` 增加 props：

```tsx
                    toolbarPortal={toolbarHost}
                    dirty={dirty}
                    saving={savingEdits}
                    onSave={handleSaveEdits}
                    onDiscard={handleDiscardEdits}
```

- [ ] **Step 4: docx-paragraph-editor.tsx 接受 props 并挂 DocxToolbar**

`DocxParagraphEditor` props 增加 `toolbarPortal, dirty, saving, onSave, onDiscard`（类型：`HTMLElement | null` / `number` / `boolean` / `() => void` / `() => void`），JSX 中 `RichTextPlugin` 之前加：

```tsx
        <DocxToolbar
          portal={toolbarPortal}
          dirty={dirty}
          saving={saving}
          onSave={onSave}
          onDiscard={onDiscard}
        />
```

并 import `DocxToolbar`。清理 review-panel 中不再使用的旧保存栏代码（`Button` 若他处仍用则保留 import）。

- [ ] **Step 5: 类型检查 + eslint**

Run: `cd web && npx tsc --noEmit 2>&1 | grep -E "review-panel|docx-toolbar|docx-paragraph-editor|flow-service"; echo done`
Expected: 只输出 done。

Run: `cd web && npx eslint src/pages/c-chat/review-panel.tsx src/pages/c-chat/docx-toolbar.tsx src/pages/c-chat/docx-paragraph-editor.tsx src/services/flow-service.ts 2>&1 | tail -3`
Expected: 无 error。

- [ ] **Step 6: Commit**

```bash
git add web/src/pages/c-chat/review-panel.tsx web/src/pages/c-chat/docx-paragraph-editor.tsx web/src/pages/c-chat/docx-toolbar.tsx web/src/services/flow-service.ts
git commit -m "feat(flow): 文件审核接入 Word 工具栏，保存链路携带 runs"
```

---

### Task 6: 后端 runs 解析与写入（flow_app.py）

**Files:**
- Modify: `api/apps/restful_apis/flow_app.py`

- [ ] **Step 1: 确认现有 import**

查看文件头部 docx 相关 import（应有 `DocxDocument`/`DocxParagraph`），补充：

```python
import re
from docx.shared import Pt, RGBColor
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
```

（若 `re`/`Pt` 等已存在则不重复。）

- [ ] **Step 2: runs 解析函数（`_replace_para_text` 上方新增）**

```python
_COLOR_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")
_RUN_BOOL_KEYS = ("bold", "italic", "underline", "strike", "superscript", "subscript")


def _parse_runs(raw):
    """解析并校验可选 runs 字段：None → None（走旧整段替换）；
    非法结构/颜色/字号抛 ValueError，由调用方转 400。"""
    if raw is None:
        return None
    if not isinstance(raw, list) or not raw:
        raise ValueError("runs 必须是非空数组或省略")
    parsed = []
    for r in raw:
        if not isinstance(r, dict):
            raise ValueError("runs 项格式非法")
        text = str(r.get("text") or "")
        if not text:
            raise ValueError("runs 片段文本不能为空")
        item = {"text": text}
        for k in _RUN_BOOL_KEYS:
            if r.get(k):
                item[k] = True
        for k in ("color", "bg_color"):
            v = r.get(k)
            if v:
                v = str(v)
                if not _COLOR_RE.match(v):
                    raise ValueError(f"{k} 颜色值非法：{v}")
                item[k] = v
        font = r.get("font")
        if font:
            item["font"] = str(font)[:50]
        size = r.get("size")
        if size is not None:
            try:
                size = float(size)
            except (TypeError, ValueError):
                raise ValueError("size 字号必须是数字")
            if not (1 <= size <= 200):
                raise ValueError("size 字号超出范围（1-200pt）")
            item["size"] = size
        parsed.append(item)
    return parsed
```

- [ ] **Step 3: run 写入函数（`_replace_para_text` 下方新增）**

```python
def _set_run_font(run, name: str):
    """同时设置西文（ascii/hAnsi）与中文（eastAsia）字体，否则中文不生效。"""
    run.font.name = name
    r_pr = run._element.get_or_add_rPr()
    r_fonts = r_pr.find(qn("w:rFonts"))
    if r_fonts is None:
        r_fonts = OxmlElement("w:rFonts")
        r_pr.append(r_fonts)
    r_fonts.set(qn("w:eastAsia"), name)


def _apply_runs(p: DocxParagraph, runs):
    """按 runs 重写段落文本 run（保留段落级 style/对齐）。
    runs 经 _parse_runs 校验。bg_color 用 w:shd 底纹实现。"""
    for r in list(p.runs):
        r._element.getparent().remove(r._element)
    for item in runs:
        run = p.add_run(item["text"])
        if item.get("bold"):
            run.bold = True
        if item.get("italic"):
            run.italic = True
        if item.get("underline"):
            run.underline = True
        if item.get("strike"):
            run.font.strike = True
        if item.get("superscript"):
            run.font.superscript = True
        if item.get("subscript"):
            run.font.subscript = True
        if item.get("color"):
            run.font.color.rgb = RGBColor.from_string(item["color"].lstrip("#"))
        if item.get("bg_color"):
            shd = OxmlElement("w:shd")
            shd.set(qn("w:val"), "clear")
            shd.set(qn("w:fill"), item["bg_color"].lstrip("#"))
            run._element.get_or_add_rPr().append(shd)
        if item.get("font"):
            _set_run_font(run, item["font"])
        if item.get("size"):
            run.font.size = Pt(item["size"])
```

- [ ] **Step 4: 端点接入 runs**

`edit_document` 中：

1. edits 解析循环（`parsed_edits.append((para_index, new_text))` 处）改为：

```python
            try:
                runs = _parse_runs(e.get("runs"))
            except ValueError as ve:
                return _err(f"段落 {para_index} runs 格式非法：{ve}", 101)
            parsed_edits.append((para_index, new_text, runs))
```

2. inserts 解析循环同理：`parsed_inserts.append((after, new_text, runs))`。

3. 定位与应用段同步改元组结构：
   - `located_edits.append((entry[2], new_text, runs))`
   - 应用处 `for target, new_text, runs in located_edits:` → `runs is not None ? _apply_runs(target, runs) : _replace_para_text(target, new_text)`（Python 写法：`if runs is not None: _apply_runs(target, runs) else: _replace_para_text(target, new_text)`）
   - `located_inserts` 元组追加 runs（mode, ref, new_text, style_src, runs），应用处插入段落成功后 `if runs is not None: _apply_runs(new_p, runs)`
   - 单段字数校验（20000）保持对 `new_text` 生效不变；runs 总文本与 new_text 不一致时以 runs 为准拼接校验：解析后加一行：

```python
            if runs and "".join(x["text"] for x in runs) != new_text:
                return _err(f"段落 {para_index} runs 文本与 new_text 不一致", 101)
```

（inserts 同理，文案换成「新段落 runs 文本与 new_text 不一致」。）

- [ ] **Step 5: 本地语法检查**

Run: `python -c "import ast; ast.parse(open('api/apps/restful_apis/flow_app.py', encoding='utf-8').read()); print('syntax OK')"`
Expected: `syntax OK`

- [ ] **Step 6: Commit**

```bash
git add api/apps/restful_apis/flow_app.py
git commit -m "feat(flow): /document/edit 支持可选 runs 字段，python-docx 逐 run 写入格式"
```

---

### Task 7: 后端部署 + 冒烟（需用户确认后执行）

**Files:** 无代码改动

- [ ] **Step 1: 询问用户确认部署**（禁止自动部署）。确认后：

```bash
scp -i "D:\AI\konus-key.pem" -o StrictHostKeyChecking=no api/apps/restful_apis/flow_app.py root@47.98.102.55:/home/bid-agent-konus/ragflow2/api/apps/restful_apis/
ssh -i "D:\AI\konus-key.pem" -o StrictHostKeyChecking=no root@47.98.102.55 "docker restart docker-ragflow-cpu-1"
```

- [ ] **Step 2: 冒烟**（容器内）

```bash
ssh -i "D:\AI\konus-key.pem" -o StrictHostKeyChecking=no root@47.98.102.55 "docker exec docker-ragflow-cpu-1 python -c '
from docx.shared import Pt, RGBColor
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from api.apps.restful_apis.flow_app import _parse_runs, _apply_runs
print(\"backend smoke OK\")
' 2>&1 | tail -1"
```

Expected: `backend smoke OK`

**补充冒烟（runs 落盘核验）**：在服务器容器内用 python-docx 构造最小文档 + `_apply_runs` 写入 bold/color/size/font/superscript → 重新读取断言属性——脚本落盘 `.scratch/_smoke_runs.py` 后 `docker cp` 进容器执行。

- [ ] **Step 3: 若用户暂不部署**：Task 8 的「格式落盘」部分降级为观察项，仅验证前端编辑器/工具栏交互 + 旧纯文本保存回归，并在报告中注明。

---

### Task 8: 浏览器 E2E 验证（dev 9222）

**Files:** 无代码改动（发现问题则修复后单独 commit）

- [ ] **Step 1:** 硬刷新 `http://localhost:9222/home?e2e=<ts>` → 登录 lg18629285296@163.com/12345678 →「流程」→「测试2」→「文件审核」，确认编辑器上方出现工具栏（吸顶、分组、分隔线）。
- [ ] **Step 2: 交互断言**（browser_evaluate + snapshot）
  1. 选一段文字 → 点 B/I → 文本加粗斜体；再点取消 → 恢复（激活态同步亮灭）
  2. 字体颜色选红色、背景高亮选黄、字号选「四号 14」、字体选黑体 → 视觉生效
  3. 光标置段落 → 点居中对齐 → 段落居中；无序列表按钮 → 列表出现
  4. 正文↔标题切换（原文段）→ 段落样式变化
  5. 撤销/重做全类型混合操作回退恢复
- [ ] **Step 3: 保存断言** — 等防抖 →「已修改 N 处」胶囊 → 保存 → 新版本生成（source=manual_edit）→ 编辑器重挂载、工具栏仍在、格式视觉保留
- [ ] **Step 4: 格式落盘核验**（后端已部署时）— 下载新版本 docx（.scratch 落盘）→ python-docx 断言：bold/italic、color RGB、size Pt、font ascii+eastAsia、shd 底纹、superscript、段落 alignment
- [ ] **Step 5: 回归** — 只读模式无工具栏；批注选字→添加批注→高亮联动正常；批注高亮（HighlightTextNode）不被保存为 bg_color（下载 docx 检查无批注底纹 run）；旧纯文本编辑保存正常
- [ ] **Step 6: 发现的 bug 修复后单独 commit**

---

### Task 9: CHANGE.md + 收尾

**Files:**
- Modify: `CHANGE.md`、`CLAUDE.md`（参考表 CHANGE.md 简介按需）

- [ ] **Step 1:** CHANGE.md 顶部追加条目（日期 2026-08-31，主题「文件审核：Word 工具栏 + 格式保真落盘」；核心变更：工具栏组件/契约扩展/python-docx 逐 run 写入/diff 双比较；E2E 结果；遗留：列表/缩进落盘为普通段落、字号用 pt 值、后端未部署则格式不落盘）
- [ ] **Step 2:** CLAUDE.md 参考表 CHANGE.md 简介同步
- [ ] **Step 3:** 按全局收尾规则输出四项总结（完成/遗留/需要你做/效果）；禁止自动部署；前端生产构建等用户指示

---

## 自审记录

- **Spec 覆盖**：§4 工具栏（Task 4+5：全部分组控件、portal 挂载、激活态、保存区整合）、§5 runs 抽取与双比较（Task 1 fmtSig + Task 2 diff + Task 3 抽取）、§6 契约与 python-docx（Task 5 透传 + Task 6 后端）、§7 错误处理（Task 6 _parse_runs 校验 + 前端现有 editError）、§8 测试（Task 1/2 单测 + Task 7 冒烟 + Task 8 E2E）
- **占位符**：无 TBD；Task 4「实现说明」中对 format 位/命令名的校准指引是给实现者的实测要求（Lexical 位常量需 E2E 校准），非占位
- **类型一致性**：`DocxRun`（Task 1 定义，snake_case）= Task 2 EditorBlock.runs = Task 3 $extractRuns 产出 = Task 5 FlowDocRun 透传 = Task 6 后端契约键名逐一对应（text/bold/italic/underline/strike/superscript/subscript/color/bg_color/font/size）；`runsFmtSig` 在 Task 3 中被 readEditorBlocks 与 docx-diff 共用，签名算法单点
- **已知风险前置**：Task 3 Step 1 校验 @lexical/list；Task 4 format 位常量需 E2E 校准；Task 7 部署需用户确认
