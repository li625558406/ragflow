# 流程版本保真编辑（路线 D）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 流程「编辑文档」编辑态从 Lexical 纯文本旧段落视图换成 docx-preview 保真树上的段落级 contentEditable 编辑，格式所见即所得；保存复用既有 `diffBlocks → onEditDocument → docx_edit.py` 全链路。

**Architecture:** docx-preview 渲染产物与后端 `_build_para_map` 遵循同一「空段跳过、整表占一个 index」的遍历规则，DOM 顶层流元素（section 直子级 p/table）与 `content.paragraphs` 可做**严格顺序对齐**（数量+类型+归一化文本三重校验，任一不符整体回退旧 Lexical 视图——宁可不编辑也不能改错段）。编辑期间在保真树上按段落开 contentEditable，`beforeinput` 拦截结构性变更（回车分段/边界退格并段/跨界删除/粘贴换行/拖放），`input` 防抖后从 DOM 抽取 `EditorBlock[]` 交给既有纯函数 `diffBlocks` 产出 `{edits, tableEdits}`（deletes/inserts 出现即拦截报错）。渲染缓存（docx-render-cache）加**突变闸**：被编辑过的树永不入缓存，放弃修改 bump nonce 强制干净重放/重渲，根除「脏树回放」。

**Tech Stack:** React 18 + TypeScript、docx-preview（已依赖）、vitest + jsdom（既有配置）、既有 `docx-diff.ts` / `docx_edit.py` 后端零改动。

---

## 设计定案（来自用户已确认的路线 D + 侦察结论）

1. **后端零改动**：`POST /flow/<id>/document/edit` 与 `POST /files/<file_id>/edit` 的 `para_index` 定位契约、`docx_edit.py` 内核全部复用；前端 ops 契约 `FlowDocEditOps`（`flow-service.ts:183`）不变。
2. **对齐规则**（核心安全闸）：`mapDocxParas` 严格 1:1 —— DOM 流元素序列（跳过空文本且无图的 p，与后端 `_build_para_map` 的 skip 规则一致）对 `content.paragraphs` 序列，要求长度相等、逐位类型相同（text/image/table）、text 位归一化文本相等。任一不满足 → `ok:false` → 整体回退旧 Lexical 编辑视图（能力无损，只是格式简化）。
3. **v1 编辑范围**：段内文字编辑 + 表格单元格文字编辑（tableEdits 契约既有）。**不支持**：回车分段、退格/删除并段、新增段、删整段（清空整段文字在保存/输油闸报错）、run 级格式（保存的段落 run 格式按既有「无 runs 整段替换」语义简化——与 Lexical 引入 runs 支持前的行为一致，记入遗留）。
4. **空段对齐陷阱**：后端 `_build_para_map`（`api/utils/docx_edit.py:64`）跳过空段，docx-preview 会渲染空 `<p>`，因此**纯位置对齐不成立**，必须双侧同规则跳过空段后再对齐。
5. **懒渲染陷阱**：抽取文本必须用 `textContent`/TreeWalker，**严禁 `innerText`**（content-visibility 屏外页 visibility:hidden 会被漏计）。
6. **渲染缓存一致性**：`input` 事件置 `docxMutatedRef=true`；渲染 effect cleanup 仅在未突变时 `stashDocxRender`；放弃修改 = bump `fidelityNonce`（进渲染 effect deps）→ 脏树不入缓存 → miss → 全量重渲干净树 → ready tick 重触 editify。
7. **editify 触发时机**：必须等渲染完成——新增 `docxReadyTick` state，在缓存回放分支（同步）与 `renderAsync.then`（异步）两处 bump，作为 editify effect 的依赖；否则全量渲染路径下 editify effect 跑在空容器上必然对齐失败误回退。
8. **归一化 canonical 技巧**（防幻影 diff）：DOM 文本与模型文本归一化（剥全部空白+控制符）相等时，block.text 取**模型原文**——归一化差异（tab/\u00a0/br）不产生幻影 edit；不等（用户改过）取 DOM 文本并压平空白。

## 文件结构

| 文件 | 动作 | 职责 |
|---|---|---|
| `web/src/pages/c-chat/docx-fidelity-edit.ts` | 新建 | 纯逻辑：归一化/DOM 流元素收集/严格对齐/块抽取/editify 守卫 |
| `web/src/pages/c-chat/docx-fidelity-edit.test.ts` | 新建 | vitest+jsdom 单测（含对抗用例） |
| `web/src/pages/c-chat/docx-edit-bar.tsx` | 新建 | 保真编辑工具条（改动数/保存/放弃/提示），无 Lexical 依赖 |
| `web/src/pages/c-chat/review-panel.tsx` | 修改 | 分支重构 + editify/save/discard 接线 + 缓存突变闸 + 文案 |
| `CHANGE.md` / `CLAUDE.md` | 修改 | 迭代记录 + 参考表登记 |

既有 `docx-paragraph-editor.tsx`（Lexical）**保留为回退路径**：非 docx / 降级 / 超大未强制 / 保真渲染失败 / 对齐失败时仍走它，能力零回退。

---

### Task 1: 对齐层——normalizeParaText / collectDocxFlowEls / mapDocxParas

**Files:**
- Create: `web/src/pages/c-chat/docx-fidelity-edit.ts`
- Test: `web/src/pages/c-chat/docx-fidelity-edit.test.ts`

- [ ] **Step 1: 写失败测试**

```ts
// web/src/pages/c-chat/docx-fidelity-edit.test.ts
import { describe, expect, it } from 'vitest';
import {
  collectDocxFlowEls,
  mapDocxParas,
  normalizeParaText,
  type DocxSourceParagraph,
} from './docx-fidelity-edit';

const P = (index: number, text: string): DocxSourceParagraph => ({
  index,
  text,
  type: 'paragraph',
});

/** 搭一个 docx-preview 形状的容器：section 直子级 p/table，页眉页脚在 header/footer 里 */
function buildWrap(items: Array<
  | { kind: 'p'; text: string; img?: boolean }
  | { kind: 'empty-p' }
  | { kind: 'table' }
  | { kind: 'header' }
>): HTMLElement {
  const wrap = document.createElement('div');
  wrap.className = 'docx-wrapper';
  const sec = document.createElement('section');
  for (const it of items) {
    if (it.kind === 'header') {
      const h = document.createElement('header');
      h.innerHTML = '<p>页眉文本</p>';
      sec.appendChild(h);
    } else if (it.kind === 'table') {
      sec.appendChild(document.createElement('table'));
    } else if (it.kind === 'empty-p') {
      sec.appendChild(document.createElement('p'));
    } else {
      const p = document.createElement('p');
      p.textContent = it.text;
      if (it.img) p.appendChild(document.createElement('img'));
      sec.appendChild(p);
    }
  }
  wrap.appendChild(sec);
  document.body.appendChild(wrap);
  return wrap;
}

describe('normalizeParaText', () => {
  it('剥全部空白与控制符', () => {
    expect(normalizeParaText(' 第一\u00a0章\t标题\n')).toBe('第一章标题');
    expect(normalizeParaText('a\x00\x08b')).toBe('ab');
    expect(normalizeParaText('')).toBe('');
  });
});

describe('collectDocxFlowEls', () => {
  it('按文档序收集 p/table，跳过空段与页眉页脚', () => {
    const wrap = buildWrap([
      { kind: 'header' },
      { kind: 'p', text: '第一段' },
      { kind: 'empty-p' },
      { kind: 'table' },
      { kind: 'p', text: '第二段', img: true },
    ]);
    const els = collectDocxFlowEls(wrap);
    expect(els).toHaveLength(3);
    expect(els[0].kind).toBe('text');
    expect(els[0].normText).toBe('第一段');
    expect(els[1].kind).toBe('table');
    expect(els[2].kind).toBe('image'); // 有图无文本
    wrap.remove();
  });
});

describe('mapDocxParas', () => {
  it('全量对齐：空段双侧跳过后逐位匹配', () => {
    const wrap = buildWrap([
      { kind: 'p', text: '第一段' },
      { kind: 'empty-p' },
      { kind: 'table' },
      { kind: 'p', text: '第 二\u00a0段' }, // 空白差异归一化后相等
    ]);
    const r = mapDocxParas(wrap, [P(0, '第一段'), P(1, '第二段'), { index: 2, text: '<table><tr><td>x</td></tr></table>', type: 'table' }]);
    expect(r.ok).toBe(true);
    if (r.ok) {
      expect(r.pByEl.size).toBe(2);
      expect(r.tableByEl.size).toBe(1);
    }
    wrap.remove();
  });

  it('对抗：数量不一致 → ok:false（不给出任何部分映射）', () => {
    const wrap = buildWrap([{ kind: 'p', text: 'a' }, { kind: 'p', text: 'b' }]);
    expect(mapDocxParas(wrap, [P(0, 'a')]).ok).toBe(false);
    wrap.remove();
  });

  it('对抗：文本不一致 → ok:false，绝不带病映射', () => {
    const wrap = buildWrap([{ kind: 'p', text: '实际渲染' }]);
    expect(mapDocxParas(wrap, [P(0, '模型文本')]).ok).toBe(false);
    wrap.remove();
  });

  it('对抗：类型错位（DOM 表 vs 模型段）→ ok:false', () => {
    const wrap = buildWrap([{ kind: 'table' }]);
    expect(mapDocxParas(wrap, [P(0, 'x')]).ok).toBe(false);
    wrap.remove();
  });
});
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd web && npx vitest run src/pages/c-chat/docx-fidelity-edit.test.ts`
Expected: FAIL（模块不存在）

- [ ] **Step 3: 实现对齐层**

```ts
// web/src/pages/c-chat/docx-fidelity-edit.ts
// docx-preview 保真树上段落级编辑（路线 D，2026-09-22）：
// 严格对齐层——DOM 顶层流元素与 content.paragraphs 做 1:1 顺序对齐，任一不符
// 整体回退旧 Lexical 编辑视图（宁可不编辑也不能改错段，paraIndex 错位会静默
// 改坏后端段落）。对齐规则复刻后端 _build_para_map 的遍历口径：空文本且无图
// 的 w:p 不占 index、整表占一个 index、图片段占 index。

import type { BaselineCell, DocxSourceParagraph, EditorBlock } from './docx-diff';

/** 归一化：剥全部空白（含 \u00a0/\t/\n）与控制符，仅用于相等比较，不改变原文 */
export function normalizeParaText(s: string): string {
  return (s || '')
    .replace(/[\s\u00a0]+/g, '')
    // eslint-disable-next-line no-control-regex
    .replace(/[\x00-\x1f\x7f]/g, '');
}

export interface DomFlowEntry {
  el: HTMLElement;
  kind: 'text' | 'image' | 'table';
  normText: string;
}

/** 收集 docx-preview 树的顶层流元素（各 section 直子级，文档序）。
 * 只认 P/TABLE 直子级：页眉页脚在 header/footer 容器里、表格单元格段落嵌在
 * table 里，天然排除——与后端 body 直子级遍历同口径。 */
export function collectDocxFlowEls(wrap: HTMLElement): DomFlowEntry[] {
  const out: DomFlowEntry[] = [];
  for (const sec of Array.from(wrap.querySelectorAll('section'))) {
    for (const el of Array.from(sec.children)) {
      const tag = el.tagName;
      if (tag === 'TABLE') {
        out.push({ el: el as HTMLElement, kind: 'table', normText: '' });
      } else if (tag === 'P') {
        const host = el as HTMLElement;
        const normText = normalizeParaText(host.textContent || '');
        const hasImg = !!host.querySelector('img');
        if (!normText && !hasImg) continue; // 空段不占 index（与后端 skip 规则一致）
        out.push({
          el: host,
          kind: hasImg && !normText ? 'image' : 'text',
          normText,
        });
      }
    }
  }
  return out;
}

export type FidelityMap =
  | { ok: false; reason: string }
  | {
      ok: true;
      /** 顶层文本/图片段 el → paraIndex（插入序即文档序） */
      pByEl: Map<HTMLElement, number>;
      /** 顶层 table el → paraIndex */
      tableByEl: Map<HTMLElement, number>;
    };

/** 严格 1:1 对齐：长度、逐位类型、text 位归一化文本三重校验。
 * 模型侧 table/image 条目 normText 记 ''，与 DOM table/image 对位。 */
export function mapDocxParas(
  wrap: HTMLElement,
  paragraphs: DocxSourceParagraph[],
): FidelityMap {
  const dom = collectDocxFlowEls(wrap);
  const model = paragraphs.map((p) => ({
    index: p.index,
    kind: (p.type === 'table' ? 'table' : p.type === 'image' ? 'image' : 'text') as DomFlowEntry['kind'],
    normText: p.type === 'table' || p.type === 'image' ? '' : normalizeParaText(p.text),
  }));
  if (dom.length !== model.length) {
    return { ok: false, reason: `流元素数不一致 DOM=${dom.length} 模型=${model.length}` };
  }
  const pByEl = new Map<HTMLElement, number>();
  const tableByEl = new Map<HTMLElement, number>();
  for (let i = 0; i < dom.length; i++) {
    const d = dom[i];
    const m = model[i];
    if (d.kind !== m.kind) {
      return { ok: false, reason: `第 ${i + 1} 个流元素类型不一致 DOM=${d.kind} 模型=${m.kind}` };
    }
    if (d.kind === 'table') {
      tableByEl.set(d.el, m.index);
    } else {
      if (d.normText !== m.normText) {
        return { ok: false, reason: `第 ${i + 1} 段文本与模型不一致` };
      }
      pByEl.set(d.el, m.index);
    }
  }
  return { ok: true, pByEl, tableByEl };
}
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd web && npx vitest run src/pages/c-chat/docx-fidelity-edit.test.ts`
Expected: PASS（全部用例绿）

- [ ] **Step 5: Commit**

```bash
git add web/src/pages/c-chat/docx-fidelity-edit.ts web/src/pages/c-chat/docx-fidelity-edit.test.ts
git commit -m "feat(review): 保真编辑对齐层——DOM 流元素与段落模型严格 1:1 映射"
```

---

### Task 2: 块抽取——collectFidelityBlocks（canonical 防幻影 + 表格单元格）

**Files:**
- Modify: `web/src/pages/c-chat/docx-fidelity-edit.ts`
- Test: `web/src/pages/c-chat/docx-fidelity-edit.test.ts`（追加）

- [ ] **Step 1: 追加失败测试**

```ts
// 追加到 docx-fidelity-edit.test.ts
import { collectFidelityBlocks } from './docx-fidelity-edit';
import { diffBlocks, type BaselineCell } from './docx-diff';

describe('collectFidelityBlocks', () => {
  it('未改动段落取模型原文（canonical）：diff 零幻影', () => {
    const wrap = buildWrap([{ kind: 'p', text: '第一  段\t文本' }]);
    const model = [P(0, '第一 段 文本')];
    const r = mapDocxParas(wrap, model);
    if (!r.ok) throw new Error('map failed');
    const blocks = collectFidelityBlocks(model, r);
    const ops = diffBlocks(blocks, model);
    expect('error' in ops && ops.error).toBeFalsy();
    if (!('error' in ops)) expect(ops.count).toBe(0);
    wrap.remove();
  });

  it('改动段落产出 edits；diffBlocks 只含 edits（无 deletes/inserts）', () => {
    const wrap = buildWrap([{ kind: 'p', text: '原文A' }, { kind: 'p', text: '原文B' }]);
    const model = [P(0, '原文A'), P(1, '原文B')];
    const r = mapDocxParas(wrap, model);
    if (!r.ok) throw new Error('map failed');
    (Array.from(r.pByEl.keys())[1] as HTMLElement).textContent = '改成B';
    const blocks = collectFidelityBlocks(model, r);
    const ops = diffBlocks(blocks, model);
    if ('error' in ops) throw new Error(ops.error);
    expect(ops.edits).toEqual([{ paraIndex: 1, newText: '改成B', runs: undefined }]);
    expect(ops.deletes).toHaveLength(0);
    expect(ops.inserts).toHaveLength(0);
    wrap.remove();
  });

  it('表格单元格抽取：colspan 累加坐标、多段格 \\n 连接、格改动产 tableEdits', () => {
    const wrap = document.createElement('div');
    wrap.className = 'docx-wrapper';
    const sec = document.createElement('section');
    const table = document.createElement('table');
    table.innerHTML =
      '<tr><td>甲</td><td colspan="2">乙一段<br>乙二段</td><td>丙</td></tr>';
    sec.appendChild(table);
    wrap.appendChild(sec);
    document.body.appendChild(wrap);
    const model = [{ index: 0, text: '<table/>', type: 'table' as const }];
    const r = mapDocxParas(wrap, model);
    if (!r.ok) throw new Error('map failed');
    const baselines = new Map<number, BaselineCell[]>([
      [0, [
        { row: 0, col: 0, colSpan: 1, header: false, text: '甲' },
        { row: 0, col: 1, colSpan: 2, header: false, text: '乙一段\n乙二段' },
        { row: 0, col: 3, colSpan: 1, header: false, text: '丙' },
      ]],
    ]);
    // 用户改了合并格
    const tds = (Array.from(r.tableByEl.keys())[0] as HTMLTableElement).rows[0].cells;
    tds[1].innerHTML = '乙新一段<br>乙二段';
    const blocks = collectFidelityBlocks(model, r, baselines);
    const ops = diffBlocks(blocks, model, baselines);
    if ('error' in ops) throw new Error(ops.error);
    expect(ops.tableEdits).toEqual([
      { paraIndex: 0, row: 0, col: 1, newText: '乙新一段\n乙二段', runs: undefined },
    ]);
    expect(ops.count).toBe(1);
    wrap.remove();
  });

  it('对抗：清空整段 → diff 产 deletes，由上层拦截', () => {
    const wrap = buildWrap([{ kind: 'p', text: '原文A' }]);
    const model = [P(0, '原文A')];
    const r = mapDocxParas(wrap, model);
    if (!r.ok) throw new Error('map failed');
    (Array.from(r.pByEl.keys())[0] as HTMLElement).textContent = '';
    const ops = diffBlocks(collectFidelityBlocks(model, r), model);
    if ('error' in ops) throw new Error(ops.error);
    expect(ops.deletes).toEqual([0]);
    wrap.remove();
  });
});
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd web && npx vitest run src/pages/c-chat/docx-fidelity-edit.test.ts`
Expected: FAIL（collectFidelityBlocks 不存在）

- [ ] **Step 3: 实现块抽取（追加到 docx-fidelity-edit.ts）**

```ts
// ── 块抽取 ─────────────────────────────────────────────────
// 严禁 innerText：content-visibility 屏外页 visibility:hidden 会被漏计，
// 一律 textContent/TreeWalker。

/** 顶层段文本：textContent + br→\n，跳过 img/svg（模型侧图片段文本为空） */
function blockDomText(el: HTMLElement): string {
  let s = '';
  const walk = (node: Node) => {
    if (node.nodeType === Node.ELEMENT_NODE) {
      const e = node as Element;
      const tag = e.tagName.toLowerCase();
      if (tag === 'br') { s += '\n'; return; }
      if (tag === 'img' || tag === 'svg' || tag === 'script' || tag === 'style') return;
      e.childNodes.forEach(walk);
    } else if (node.nodeType === Node.TEXT_NODE) {
      s += node.nodeValue || '';
    }
  };
  walk(el);
  return s;
}

/** 单元格文本：与基线 parseTableCells 的 cellText 同语义（嵌套表跳过、br→\n） */
function cellDomText(td: Element): string {
  let s = '';
  const walk = (node: Node) => {
    if (node.nodeType === Node.ELEMENT_NODE) {
      const e = node as Element;
      const tag = e.tagName.toLowerCase();
      if (tag === 'table' || tag === 'script' || tag === 'style') return;
      if (tag === 'br') { s += '\n'; return; }
      e.childNodes.forEach(walk);
    } else if (node.nodeType === Node.TEXT_NODE) {
      s += (node.nodeValue || '').replace(/\u00a0/g, ' ');
    }
  };
  walk(td);
  return s;
}

/** canonical 技巧：归一化相等 → 取模型原文（tab/\u00a0/br 等渲染差异不产生幻影
 * edit）；不等（用户改过）→ 取 DOM 文本并压平空白（换行/制表符不进后端） */
function canonicalText(domRaw: string, modelRaw: string): string {
  if (normalizeParaText(domRaw) === normalizeParaText(modelRaw)) return modelRaw;
  return domRaw.replace(/\s+/g, ' ').trim();
}

/** 从保真树抽取 EditorBlock[]（文档序）：text 段 + 表格单元格（colspan 累加坐标，
 * 与 readEditorBlocks/parseTableCells 同口径）。未覆盖的模型段落补虚拟块防误删
 * （strict 对齐下不可达，防御性保留）。 */
export function collectFidelityBlocks(
  paragraphs: DocxSourceParagraph[],
  map: Extract<FidelityMap, { ok: true }>,
  tableBaselines?: Map<number, BaselineCell[]>,
): EditorBlock[] {
  const byIdx = new Map(paragraphs.map((p) => [p.index, p]));
  const blocks: EditorBlock[] = [];
  const covered = new Set<number>();
  for (const [el, idx] of map.pByEl) {
    const orig = byIdx.get(idx);
    if (!orig) continue;
    covered.add(idx);
    blocks.push({
      paraIndex: idx,
      kind: 'text',
      text: canonicalText(blockDomText(el), orig.text),
    });
  }
  for (const [tbl, idx] of map.tableByEl) {
    covered.add(idx);
    const base = tableBaselines?.get(idx);
    let ri = 0;
    for (const tr of Array.from((tbl as HTMLTableElement).rows)) {
      let col = 0;
      for (const td of Array.from(tr.cells)) {
        const n = parseInt(td.getAttribute('colspan') || '1', 10);
        const colSpan = Number.isFinite(n) && n > 0 ? n : 1;
        const bc = base?.find((x) => x.row === ri && x.col === col);
        blocks.push({
          paraIndex: idx,
          kind: 'table',
          cell: { row: ri, col },
          text: canonicalText(cellDomText(td), bc?.text || ''),
        });
        col += colSpan;
      }
      ri += 1;
    }
  }
  // 防御：对齐 strict 契约下模型全覆盖；万一出现空洞，补虚拟块让 diffBlocks
  // 记 seen，避免被当 delete 送后端
  for (const p of paragraphs) {
    if (covered.has(p.index)) continue;
    blocks.push({
      paraIndex: p.index,
      kind: p.type === 'table' ? 'table' : p.type === 'image' ? 'image' : 'text',
      text: p.type === 'text' || p.type === 'paragraph' ? p.text : '',
    });
  }
  return blocks;
}
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd web && npx vitest run src/pages/c-chat/docx-fidelity-edit.test.ts`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add web/src/pages/c-chat/docx-fidelity-edit.ts web/src/pages/c-chat/docx-fidelity-edit.test.ts
git commit -m "feat(review): 保真编辑块抽取——canonical 防幻影 diff + 表格单元格坐标"
```

---

### Task 3: 编辑守卫——editifyDocx（contentEditable + beforeinput 拦截）

**Files:**
- Modify: `web/src/pages/c-chat/docx-fidelity-edit.ts`
- Test: `web/src/pages/c-chat/docx-fidelity-edit.test.ts`（追加）

- [ ] **Step 1: 追加失败测试**

```ts
// 追加到 docx-fidelity-edit.test.ts
import { editifyDocx } from './docx-fidelity-edit';

function fireBeforeInput(el: Element, inputType: string): boolean {
  const ev = new Event('beforeinput', { bubbles: true, cancelable: true }) as InputEvent;
  Object.defineProperty(ev, 'inputType', { value: inputType });
  el.dispatchEvent(ev);
  return ev.defaultPrevented;
}

describe('editifyDocx', () => {
  it('对映射段落与表格 td 开 contentEditable；dispose 还原', () => {
    const wrap = buildWrap([{ kind: 'p', text: '甲' }, { kind: 'table' }]);
    const r = mapDocxParas(wrap, [P(0, '甲'), { index: 1, text: '', type: 'table' }]);
    if (!r.ok) throw new Error('map failed');
    const blocked: string[] = [];
    const dispose = editifyDocx(wrap, {
      pEls: r.pByEl,
      tableByEl: r.tableByEl,
      onInput: () => {},
      onStructBlocked: (m) => blocked.push(m),
    });
    const p = Array.from(r.pByEl.keys())[0];
    const table = Array.from(r.tableByEl.keys())[0] as HTMLTableElement;
    expect(p.getAttribute('contenteditable')).toBe('true');
    expect(table.rows[0].cells[0].getAttribute('contenteditable')).toBe('true');
    dispose();
    expect(p.getAttribute('contenteditable')).toBeNull();
    expect(table.rows[0].cells[0].getAttribute('contenteditable')).toBeNull();
    wrap.remove();
  });

  it('回车（insertParagraph）被拦截并提示', () => {
    const wrap = buildWrap([{ kind: 'p', text: '甲' }]);
    const r = mapDocxParas(wrap, [P(0, '甲')]);
    if (!r.ok) throw new Error('map failed');
    const blocked: string[] = [];
    const dispose = editifyDocx(wrap, {
      pEls: r.pByEl, tableByEl: r.tableByEl,
      onInput: () => {}, onStructBlocked: (m) => blocked.push(m),
    });
    const p = Array.from(r.pByEl.keys())[0];
    expect(fireBeforeInput(p, 'insertParagraph')).toBe(true);
    expect(blocked).toHaveLength(1);
    dispose();
    wrap.remove();
  });

  it('段中间退格放行；段首退格（会并段）拦截', () => {
    const wrap = buildWrap([{ kind: 'p', text: '甲乙' }]);
    const r = mapDocxParas(wrap, [P(0, '甲乙')]);
    if (!r.ok) throw new Error('map failed');
    const blocked: string[] = [];
    const dispose = editifyDocx(wrap, {
      pEls: r.pByEl, tableByEl: r.tableByEl,
      onInput: () => {}, onStructBlocked: (m) => blocked.push(m),
    });
    const p = Array.from(r.pByEl.keys())[0] as HTMLElement;
    p.innerHTML = '<span>甲</span><span>乙</span>';
    const mid = p.querySelector('span')!.firstChild!;

    // 段中间（"乙"开头前，有"甲"可删）→ 放行
    const sel = window.getSelection()!;
    const r1 = document.createRange();
    r1.setStartBefore(p.querySelector('span')!);
    r1.collapse(true);
    sel.removeAllRanges();
    sel.addRange(r1);
    // 注意：光标在 span 边界、其前有文本"甲"——用文本节点定位更贴近真实
    const second = p.childNodes[1].firstChild!;
    const r2 = document.createRange();
    r2.setStart(second, 0);
    r2.collapse(true);
    sel.removeAllRanges();
    sel.addRange(r2);
    expect(fireBeforeInput(p, 'deleteContentBackward')).toBe(false);
    expect(blocked).toHaveLength(0);

    // 段首（"甲"前无字符）→ 拦截
    const first = p.childNodes[0].firstChild!;
    const r3 = document.createRange();
    r3.setStart(first, 0);
    r3.collapse(true);
    sel.removeAllRanges();
    sel.addRange(r3);
    expect(fireBeforeInput(p, 'deleteContentBackward')).toBe(true);
    expect(blocked).toHaveLength(1);
    dispose();
    wrap.remove();
  });

  it('拖放被拦截', () => {
    const wrap = buildWrap([{ kind: 'p', text: '甲' }]);
    const r = mapDocxParas(wrap, [P(0, '甲')]);
    if (!r.ok) throw new Error('map failed');
    const dispose = editifyDocx(wrap, {
      pEls: r.pByEl, tableByEl: r.tableByEl,
      onInput: () => {}, onStructBlocked: () => {},
    });
    const p = Array.from(r.pByEl.keys())[0];
    const ev = new Event('drop', { bubbles: true, cancelable: true });
    p.dispatchEvent(ev);
    expect(ev.defaultPrevented).toBe(true);
    dispose();
    wrap.remove();
  });
});
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd web && npx vitest run src/pages/c-chat/docx-fidelity-edit.test.ts`
Expected: FAIL（editifyDocx 不存在）

- [ ] **Step 3: 实现 editifyDocx（追加到 docx-fidelity-edit.ts）**

```ts
// ── 编辑守卫 ───────────────────────────────────────────────

export interface EditifyOptions {
  pEls: Map<HTMLElement, number>;
  tableByEl: Map<HTMLElement, number>;
  /** 任意 input（含守卫放行的删除/输入）后触发，调用方防抖 diff */
  onInput: () => void;
  /** 结构性变更被拦截时提示（调用方做短暂浮现） */
  onStructBlocked: (msg: string) => void;
}

/** 光标是否贴在 host 的起始/末尾边界（Range toString 判空，跨文本节点） */
function caretAtEdge(host: HTMLElement, atStart: boolean): boolean {
  const sel = window.getSelection();
  if (!sel || !sel.rangeCount || !sel.isCollapsed) return false;
  const caret = sel.getRangeAt(0);
  const probe = document.createRange();
  probe.selectNodeContents(host);
  try {
    if (atStart) probe.setEnd(caret.startContainer, caret.startOffset);
    else probe.setStart(caret.endContainer, caret.endOffset);
  } catch {
    return false;
  }
  return probe.toString().length === 0;
}

/** 光标所在（或选区起点所在）的可编辑宿主 */
function editableHostOf(node: Node | null, root: HTMLElement): HTMLElement | null {
  let n = node?.nodeType === Node.TEXT_NODE ? node.parentElement : (node as HTMLElement | null);
  while (n && n !== root) {
    if (n.getAttribute?.('contenteditable') === 'true') return n;
    n = n.parentElement;
  }
  return null;
}

/** 在保真树上开启段落级编辑：映射段与表格 td 开 contentEditable，
 * beforeinput 拦截一切结构性变更（分段/并段/跨段删除/粘贴换行/拖放）。
 * 返回 dispose（还原 contentEditable、摘监听）。幂等：重复 apply 前先 dispose。 */
export function editifyDocx(wrap: HTMLElement, opts: EditifyOptions): () => void {
  const editables: HTMLElement[] = [];
  for (const el of opts.pEls.keys()) {
    if (el.getAttribute('contenteditable') !== 'true') {
      el.setAttribute('contenteditable', 'true');
      editables.push(el);
    }
  }
  for (const t of opts.tableByEl.keys()) {
    for (const tr of Array.from((t as HTMLTableElement).rows)) {
      for (const td of Array.from(tr.cells)) {
        if (td.getAttribute('contenteditable') !== 'true') {
          td.setAttribute('contenteditable', 'true');
          editables.push(td);
        }
      }
    }
  }

  const STRUCT_MSG = '暂不支持分段/删除整段等结构调整，请只修改段内文字';
  const onBeforeInput = (ev: Event) => {
    const e = ev as InputEvent;
    const t = e.inputType;
    if (t === 'insertParagraph' || t === 'insertLineBreak') {
      e.preventDefault();
      opts.onStructBlocked(STRUCT_MSG);
      return;
    }
    if (t === 'insertFromPaste') {
      // 纯文本手动插入并剥换行：粘贴多段会拆出新段落
      e.preventDefault();
      const raw = e.dataTransfer?.getData('text/plain') ?? e.data ?? '';
      const clean = raw.replace(/[\r\n]+/g, ' ').replace(/\s+/g, ' ');
      if (clean) document.execCommand('insertText', false, clean);
      return;
    }
    if (!t.startsWith('delete')) return;
    const sel = window.getSelection();
    if (!sel || !sel.rangeCount) return;
    const range = sel.getRangeAt(0);
    const startHost = editableHostOf(range.startContainer, wrap);
    const endHost = editableHostOf(range.endContainer, wrap);
    // 选区跨段（剪切/退格跨段）→ 拦
    if (startHost && endHost && startHost !== endHost && !range.collapsed) {
      e.preventDefault();
      opts.onStructBlocked(STRUCT_MSG);
      return;
    }
    if (!range.collapsed || !startHost) return;
    if (
      (t === 'deleteContentBackward' || t === 'deleteWordBackward') &&
      caretAtEdge(startHost, true)
    ) {
      e.preventDefault();
      opts.onStructBlocked(STRUCT_MSG);
      return;
    }
    if (
      (t === 'deleteContentForward' || t === 'deleteWordForward') &&
      caretAtEdge(startHost, false)
    ) {
      e.preventDefault();
      opts.onStructBlocked(STRUCT_MSG);
    }
  };
  const onDrop = (e: DragEvent) => {
    e.preventDefault();
    opts.onStructBlocked(STRUCT_MSG);
  };
  const onInput = () => opts.onInput();
  wrap.addEventListener('beforeinput', onBeforeInput, true);
  wrap.addEventListener('drop', onDrop, true);
  wrap.addEventListener('input', onInput, true);

  return () => {
    wrap.removeEventListener('beforeinput', onBeforeInput, true);
    wrap.removeEventListener('drop', onDrop, true);
    wrap.removeEventListener('input', onInput, true);
    for (const el of editables) el.removeAttribute('contenteditable');
  };
}
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd web && npx vitest run src/pages/c-chat/docx-fidelity-edit.test.ts`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add web/src/pages/c-chat/docx-fidelity-edit.ts web/src/pages/c-chat/docx-fidelity-edit.test.ts
git commit -m "feat(review): 保真编辑守卫——beforeinput 拦截分段/并段/跨段删除/拖放"
```

---

### Task 4: 工具条组件 docx-edit-bar.tsx

**Files:**
- Create: `web/src/pages/c-chat/docx-edit-bar.tsx`

- [ ] **Step 1: 实现组件（纯展示，无状态；Lexical 工具条格式按钮与保真编辑无关，不搬运）**

```tsx
// web/src/pages/c-chat/docx-edit-bar.tsx
// 保真编辑工具条：portal 进父级吸顶容器（toolbarHost）。改动数/结构提示/错误
// + 保存/放弃。与 DocxToolbar（Lexical 格式按钮）无关——保真编辑 v1 只产文本
// edits/tableEdits，无 run 级格式操作。
import { Button } from '@/components/ui/button';

export default function DocxEditBar({
  dirty,
  saving,
  error,
  hint,
  onSave,
  onDiscard,
}: {
  dirty: number;
  saving: boolean;
  error: string;
  hint: string;
  onSave: () => void;
  onDiscard: () => void;
}) {
  return (
    <div className="flex items-center gap-2 rounded-b-md border border-t-0 border-[#E5E5E5] bg-white px-3 py-1.5 shadow-[0_2px_8px_rgba(0,0,0,0.06)]">
      <span className="text-xs text-[#8A8A8A]">保真编辑 · 仅支持段内文字修改</span>
      {dirty > 0 && (
        <span className="text-xs font-medium text-[#FA8C16]">改动 {dirty} 处</span>
      )}
      {hint && !error && <span className="text-xs text-[#FAAD14]">{hint}</span>}
      {error && <span className="truncate text-xs text-[#FF4D4F]">{error}</span>}
      <div className="ml-auto flex items-center gap-2">
        <Button size="sm" variant="outline" onClick={onDiscard} disabled={saving}>
          放弃
        </Button>
        <Button size="sm" onClick={onSave} disabled={saving || dirty === 0}>
          {saving ? '保存中…' : '保存为新版本'}
        </Button>
      </div>
    </div>
  );
}
```

- [ ] **Step 2: tsc 校验**

Run: `cd web && npx tsc --noEmit -p tsconfig.json`
Expected: 无错误（若全量 tsc 本就有既有报错，则限定本文件无新增错误）

- [ ] **Step 3: Commit**

```bash
git add web/src/pages/c-chat/docx-edit-bar.tsx
git commit -m "feat(review): 保真编辑工具条组件"
```

---

### Task 5: review-panel 接线

**Files:**
- Modify: `web/src/pages/c-chat/review-panel.tsx`

接线点（行号以当前文件为准，编辑前重新 Read）：

- [ ] **Step 1: 状态与派生**

在 `userEditing` state 附近（~line 700）新增：

```tsx
// ── 保真编辑（路线 D）：编辑态留在 docx-preview 树上，见 docx-fidelity-edit.ts ──
const [fidelityEditBlocked, setFidelityEditBlocked] = useState(false);
// 放弃修改 → bump 强制干净重放/重渲（脏树因突变闸不入缓存，必然 miss → 全量 renderAsync）
const [fidelityNonce, setFidelityNonce] = useState(0);
// 渲染完成信号：缓存回放分支（同步）与 renderAsync.then（异步）两处 bump，
// editify effect 以它为依赖，保证跑在已渲染的树上
const [docxReadyTick, setDocxReadyTick] = useState(0);
const fidMapRef = useRef<Extract<FidelityMap, { ok: true }> | null>(null);
const fidDisposeRef = useRef<(() => void) | null>(null);
// 突变闸：编辑过的树绝不允许 stashDocxRender 入缓存（否则脏树会被后续只读回放）
const docxMutatedRef = useRef(false);
const fidTimer = useRef<number | undefined>(undefined);
// 结构拦截提示（短暂浮现自动消失）
const [structHint, setStructHint] = useState('');
const structHintTimer = useRef<number | undefined>(undefined);
```

`docxFidelityCandidate` 去掉 `&& !editing`（编辑态保持 blob 与保真分支在场）：

```tsx
const docxFidelityCandidate = content?.file_type === 'docx';
```

新增派生（放在 `docxFidelity` 之后）：

```tsx
// 保真编辑可用 = 编辑态 + 保真在场 + 对齐未被拒；否则回退旧 Lexical 编辑视图
const editingFidelity = editing && docxFidelity && !fidelityEditBlocked;
```

- [ ] **Step 2: 渲染 effect 加突变闸 + nonce + ready tick**

渲染 effect（~line 967）四处修改：

1. effect body 开头（`setMarkedKeys(new Set())` 之后）加：

```tsx
docxMutatedRef.current = false; // 新一轮渲染产物视为干净
```

2. 缓存回放分支 `takeDocxRender(...)` 命中后、`setMarkedKeys(...)` 之前加：

```tsx
setDocxReadyTick((n) => n + 1);
```

3. `renderAsync(...).then(...)` 成功回调内、`setMarkedKeys(...)` 之前加：

```tsx
setDocxReadyTick((n) => n + 1);
```

4. cleanup 中 stash 加突变闸：

```tsx
if (settled && !failed && !docxMutatedRef.current) stashDocxRender(docxBlob, el);
```

5. deps 数组追加 `fidelityNonce`。

- [ ] **Step 3: blocked 复位 effect**

```tsx
// 换文件/换 blob/放弃重渲后，对齐拒绝态复位（新树允许重新尝试对齐）
useEffect(() => {
  setFidelityEditBlocked(false);
}, [docxBlob, fidelityNonce, fileId]);
```

- [ ] **Step 4: editify effect（放在渲染 effect 之后）**

```tsx
// 进入保真编辑：对齐 → 开 contentEditable + 结构守卫。依赖 docxReadyTick 保证
// 跑在渲染完成的树上（全量 renderAsync 路径异步完成，仅靠 editingFidelity 会
// 撞空容器误判对齐失败）。dispose 先行保证 StrictMode 双跑幂等。
useEffect(() => {
  fidDisposeRef.current?.();
  fidDisposeRef.current = null;
  fidMapRef.current = null;
  if (!editingFidelity || !docxWrapRef.current || !content) return;
  const wrap = docxWrapRef.current;
  const map = mapDocxParas(wrap, content.paragraphs);
  if (!map.ok) {
    // 宁回退不可错改：对齐失败整体转旧 Lexical 编辑视图
    setFidelityEditBlocked(true);
    return;
  }
  fidMapRef.current = map;
  fidDisposeRef.current = editifyDocx(wrap, {
    pEls: map.pByEl,
    tableByEl: map.tableByEl,
    onInput: () => {
      docxMutatedRef.current = true;
      window.clearTimeout(fidTimer.current);
      fidTimer.current = window.setTimeout(handleFidDirty, 250);
    },
    onStructBlocked: (msg) => {
      setStructHint(msg);
      window.clearTimeout(structHintTimer.current);
      structHintTimer.current = window.setTimeout(() => setStructHint(''), 2500);
    },
  });
  return () => {
    fidDisposeRef.current?.();
    fidDisposeRef.current = null;
    fidMapRef.current = null;
    window.clearTimeout(fidTimer.current);
  };
  // eslint-disable-next-line react-hooks/exhaustive-deps
}, [editingFidelity, docxEpoch, docxReadyTick, fidelityNonce, content]);
```

`handleFidDirty` / `handleFidSave` / `handleFidDiscard`（放在编辑 handlers 区，`useCallback`）：

```tsx
// 保真树 → blocks → diff；deletes/inserts 出现即拦截（v1 只许段内文字与单元格修改）
const runFidDiff = useCallback((): DocxDiffOps | null => {
  if (!content || !fidMapRef.current) return null;
  const blocks = collectFidelityBlocks(content.paragraphs, fidMapRef.current, tableBaselines);
  const ops = diffBlocks(blocks, content.paragraphs, tableBaselines);
  if ('error' in ops) {
    setDirty(0);
    setEditError(ops.error || '当前改动无法保存');
    return null;
  }
  if (ops.deletes.length || ops.inserts.length) {
    setDirty(0);
    setEditError('暂不支持新增或删除段落，请只修改段内文字');
    return null;
  }
  return ops;
}, [content, tableBaselines]);

const handleFidDirty = useCallback(() => {
  if (!editingFidelity) return;
  const ops = runFidDiff();
  if (!ops) return;
  setDirty(ops.count);
  setEditError('');
}, [editingFidelity, runFidDiff]);

const handleFidSave = useCallback(async () => {
  if (!onEditDocument || savingEdits) return;
  const ops = runFidDiff();
  if (!ops) return;
  if (!ops.count) {
    setDirty(0);
    return;
  }
  setSavingEdits(true);
  setEditError('');
  try {
    await onEditDocument(ops);
    setDirty(0); // 成功后 content/版本由父级刷新，editing 随 contentIsVersion 自动退出
  } catch (e: any) {
    setEditError(e?.message || '保存失败，请稍后重试');
  } finally {
    setSavingEdits(false);
  }
}, [onEditDocument, savingEdits, runFidDiff]);

const handleFidDiscard = useCallback(() => {
  window.clearTimeout(fidTimer.current);
  docxMutatedRef.current = true; // 脏树禁止入缓存
  setDirty(0);
  setEditError('');
  setStructHint('');
  setFidelityNonce((n) => n + 1); // 重放/重渲干净树，editify 随 ready tick 重挂
}, []);
```

需要的 import（文件头部）：`docx-fidelity-edit` 的 `mapDocxParas / editifyDocx / collectFidelityBlocks / type FidelityMap`；`docx-diff` 的 `type DocxDiffOps`（`diffBlocks` 已 import 的话不重复）；`react-dom` 的 `createPortal`。

- [ ] **Step 5: JSX 分支重构（~line 2086-2152）**

分支顺序改为（键名保留，防 React 就地复用泄漏 DOM 的既有 comment 同步更新）：

```tsx
{showFidelity ? (
  /* 保真分支：只读审阅 或 保真编辑（同一棵 docx-preview 树，进编辑不换分支不重挂）。
     key 必须保留：……（原文注释保留） */
  <div key="view-fidelity" className="min-w-0 flex-1 overflow-auto">
    <div ref={docxWrapRefCb} onClick={handleSelectTableAnn} className="mx-auto w-full max-w-[900px]" />
  </div>
) : editing ? (
  /* 回退编辑：非 docx / 降级 / 对齐失败 → 旧 Lexical 段落视图（原分支整体保留） */
  <div key="view-editing" ……原样……>
    ……原 DocxParagraphEditor JSX 原样……
  </div>
) : docxFidelityCandidate && docxBlobLoading ? (
  …loading 原样…
) : (
  …降级 fallback 原样…
)}
```

其中：

```tsx
const showFidelity = docxFidelity && (!editing || !fidelityEditBlocked);
```

原 `editing ? Lexical : docxFidelity ? …` 的第一、二分支互换位置即可，`docxFidelityCandidate` 已不再排除 editing。

- [ ] **Step 6: 工具条**

1. 吸顶宿主条件从 `{editing && (...)}` 改为 `{(editingFidelity || editing) && (...)}`——原条件 `editing` 已覆盖两者，**实际无需改**，确认即可（宿主在正文列顶部 sticky，两分支共用）。

2. 保真编辑时 portal 工具条（放在保真分支 JSX 内或其前）：

```tsx
{editingFidelity &&
  toolbarHost &&
  createPortal(
    <DocxEditBar
      dirty={dirty}
      saving={savingEdits}
      error={editError}
      hint={structHint}
      onSave={handleFidSave}
      onDiscard={handleFidDiscard}
    />,
    toolbarHost,
  )}
```

- [ ] **Step 7: 文案更新**

1. line ~698-699 注释改为：

```
// 可编辑文件默认也进保真预览（原色审阅），点「编辑文档」进入保真编辑（路
// 线 D：docx-preview 树上直接改字，格式所见即所见即所得）；对齐失败/降级/
// 非 docx 回退旧 Lexical 段落编辑视图（格式会简化）。
```

2. 全文 grep `格式` 相关用户可见文案（如「编辑视图…格式丢失/简化」提示），保真路径不再展示该警示；Lexical 回退路径保留。

- [ ] **Step 8: 全量验证**

```bash
cd web
npx vitest run            # 全量 vitest，期望既有 312+ 全绿 + 新套件绿
npx tsc --noEmit          # 无新增错误
npm run build             # 构建通过
```

- [ ] **Step 9: Commit**

```bash
git add web/src/pages/c-chat/review-panel.tsx
git commit -m "feat(review): 编辑态接入保真树段落编辑——对齐失败回退 Lexical，脏树禁入渲染缓存"
```

---

### Task 6: E2E 手工验证 + 文档登记

- [ ] **Step 1: 本地 dev E2E（dev 端口 9222，按 memory 惯例浏览器验证）**

冒烟路径（flow 页签 + 有版本 docx 的流程）：
1. 打开流程详情 → 版本行「编辑文档」→ **不发生视图切换**（仍在保真树上），出现吸顶工具条「保真编辑」。
2. 改一段文字 → 工具条出现「改动 1 处」→ 保存 → 新版本产生、预览切到新版本。
3. 放弃修改 → 树恢复原文（无残留编辑）、contentEditable 移除。
4. 回车 → 拦截提示浮现，不产生新段。
5. 段首退格 → 拦截提示；段中间退格 → 正常删字。
6. 粘贴含换行文本 → 换行被剥成空格。
7. 表格单元格改字 → 保存 → tableEdits 落地。
8. 批注高亮/边栏卡/引线在编辑态仍在场；退出编辑后照常。
9. **缓存一致性**：编辑几处 → 放弃 → 关闭面板 → 重开同文件 → 树为原文（无残留改动）。
10. 超大文件（>2.5MB）文本降级态点「编辑文档」→ 走旧 Lexical 视图（回退路径可用）。

- [ ] **Step 2: CHANGE.md 追加条目（日期 2026-09-22，主题「流程版本保真编辑（路线 D）」）**

按项目惯例写：症状/根因（Lexical 纯文本模型格式必丢）、方案（严格对齐+守卫+canonical 防幻影+缓存突变闸）、测试与部署清单（纯前端：build+dist+nginx reload；后端零改动）。

- [ ] **Step 3: CLAUDE.md 参考表登记**（绝对路径 + 一句话简介，与 CHANGE.md 条目互相引用；含工作区在途的「删上传修改版」小活一并说明）

- [ ] **Step 4: 最终 commit**

```bash
git add CHANGE.md CLAUDE.md
git commit -m "docs: 2026-09-22 流程版本保真编辑（路线 D）迭代记录与登记"
```

**禁止自动部署**；push 前征求用户确认。

---

## Self-Review 结论

- **规格覆盖**：保真格式 ✓（树上编辑）、UI 适配 ✓（Task 4/5 工具条）、段内限制 ✓（Task 3 守卫）、保存链路复用 ✓（diffBlocks 契约不变）、回退能力无损 ✓（Lexical 保留）、缓存一致性 ✓（突变闸+nonce）。
- **类型一致性**：`FidelityMap`/`EditifyOptions`/`collectFidelityBlocks(paragraphs, map, tableBaselines)` 各任务签名一致；`EditorBlock`/`DocxDiffOps`/`BaselineCell` 均引自既有 `docx-diff.ts`。
- **已知风险（实现时注意）**：① docx-preview 表格单元格标签名假定为 `<td>` + `colspan` 属性，Task 5 接线前先在浏览器 console 用真实文档核对一次（若为自定义标签，改 `collectDocxFlowEls`/单元格遍历的选择器即可，对齐层结构不变）；② `document.execCommand('insertText')` 已废弃但全浏览器仍支持，jsdom 下为 no-op（单测不依赖粘贴插入行为）；③ 含文本框/SDT 的复杂文档会因文本不一致触发对齐拒绝 → 自动回退 Lexical，属设计内安全行为。
