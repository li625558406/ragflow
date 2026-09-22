// web/src/pages/c-chat/docx-fidelity-edit.ts
// docx-preview 保真树上段落级编辑（路线 D，2026-09-22）：
// 严格对齐层——DOM 顶层流元素与 content.paragraphs 做 1:1 顺序对齐，任一不符
// 整体回退旧 Lexical 编辑视图（宁可不编辑也不能改错段，paraIndex 错位会静默
// 改坏后端段落）。对齐规则复刻后端 _build_para_map 的遍历口径：空文本且无图
// 的 w:p 不占 index、整表占一个 index、图片段占 index。

import type {
  BaselineCell,
  DocxSourceParagraph,
  EditorBlock,
} from './docx-diff';

export type { DocxSourceParagraph };

/** 归一化：剥全部空白（含 \u00a0/\t/\n）与控制符，仅用于相等比较，不改变原文 */
export function normalizeParaText(s: string): string {
  return (
    (s || '')
      .replace(/[\s\u00a0]+/g, '')
      // eslint-disable-next-line no-control-regex
      .replace(/[\x00-\x1f\x7f]/g, '')
  );
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
    kind: (p.type === 'table'
      ? 'table'
      : p.type === 'image'
        ? 'image'
        : 'text') as DomFlowEntry['kind'],
    normText:
      p.type === 'table' || p.type === 'image' ? '' : normalizeParaText(p.text),
  }));
  if (dom.length !== model.length) {
    return {
      ok: false,
      reason: `流元素数不一致 DOM=${dom.length} 模型=${model.length}`,
    };
  }
  const pByEl = new Map<HTMLElement, number>();
  const tableByEl = new Map<HTMLElement, number>();
  for (let i = 0; i < dom.length; i++) {
    const d = dom[i];
    const m = model[i];
    if (d.kind !== m.kind) {
      return {
        ok: false,
        reason: `第 ${i + 1} 个流元素类型不一致 DOM=${d.kind} 模型=${m.kind}`,
      };
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
      if (tag === 'br') {
        s += '\n';
        return;
      }
      if (tag === 'img' || tag === 'svg' || tag === 'script' || tag === 'style')
        return;
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
      if (tag === 'br') {
        s += '\n';
        return;
      }
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
  if (normalizeParaText(domRaw) === normalizeParaText(modelRaw))
    return modelRaw;
  return domRaw.replace(/\s+/g, ' ').trim();
}

/** 单元格 canonical：与正文段不同，换行是合法语义（基线 cellText 与后端
 * cell.text 都按 \n 分段），改写后的格文本只 trim、不压平换行 */
function canonicalCellText(domRaw: string, modelRaw: string): string {
  if (normalizeParaText(domRaw) === normalizeParaText(modelRaw))
    return modelRaw;
  return domRaw.trim();
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
          text: canonicalCellText(cellDomText(td), bc?.text || ''),
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
      kind:
        p.type === 'table' ? 'table' : p.type === 'image' ? 'image' : 'text',
      text: p.type === 'table' || p.type === 'image' ? '' : p.text,
    });
  }
  return blocks;
}

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
function editableHostOf(
  node: Node | null,
  root: HTMLElement,
): HTMLElement | null {
  let n: HTMLElement | null =
    node?.nodeType === Node.TEXT_NODE
      ? node.parentElement
      : (node as HTMLElement | null);
  while (n && n !== root) {
    if (n.getAttribute?.('contenteditable') === 'true') return n;
    n = n.parentElement;
  }
  return null;
}

/** 在保真树上开启段落级编辑：映射段与表格 td 开 contentEditable，
 * beforeinput 拦截一切结构性变更（分段/并段/跨段删除/粘贴换行/拖放）。
 * 返回 dispose（还原 contentEditable、摘监听）。 */
export function editifyDocx(
  wrap: HTMLElement,
  opts: EditifyOptions,
): () => void {
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
      const clean = raw.replace(/\s+/g, ' ');
      if (clean.trim()) document.execCommand('insertText', false, clean);
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
