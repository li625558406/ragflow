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
  /** 目录形态（sdt 展平或 sdt 外 TOC 条目）：见 isTocLikePara */
  tocLike: boolean;
}

/** 目录形态判定：后端 to_paragraphs 只遍历 body 直子级 w:p/w:tbl，w:sdt（内容
 * 控件，实践中几乎都是目录 TOC）整块跳过不占 index；docx-preview parseSdt 把
 * sdtContent 展平成普通段落、DOM 无结构痕迹，无法区分「sdt 内」与「恰好同款
 * 样式的 sdt 外目录条目」（后者后端占 index）。因此不做收集期跳过，只标记形态
 * （样式类 docx_toc{N}，或段内全部文本位于内部锚点链接 a[href^="#"] 中），
 * 由 mapDocxParas 的模型驱动对齐裁决：多余目录形态段跳过，匹配上的强制只读。 */
export function isTocLikePara(el: HTMLElement): boolean {
  if (/^docx_toc\d+$/i.test(el.className || '')) return true;
  const a = el.querySelector('a[href^="#"]');
  if (!a) return false;
  return (
    normalizeParaText(el.textContent || '') ===
    normalizeParaText(a.textContent || '')
  );
}

/** 收集 docx-preview 树的顶层流元素（文档序）。
 * 正文容器 = section 的直子 article（当前 docx-preview 版本 section 直子级是
 * header/article/footer，正文段全在 article 里）；无 article 的旧结构退回
 * section 本身。只认正文容器直子级 P/TABLE：页眉页脚在 header/footer 容器里、
 * 表格单元格段落嵌在 table 里，天然排除——与后端 body 直子级遍历同口径。 */
export function collectDocxFlowEls(wrap: HTMLElement): DomFlowEntry[] {
  const out: DomFlowEntry[] = [];
  for (const sec of Array.from(wrap.querySelectorAll('section'))) {
    const body = sec.querySelector(':scope > article') || (sec as HTMLElement);
    for (const el of Array.from(body.children)) {
      const tag = el.tagName;
      if (tag === 'TABLE') {
        out.push({
          el: el as HTMLElement,
          kind: 'table',
          normText: '',
          tocLike: false,
        });
      } else if (tag === 'P') {
        const host = el as HTMLElement;
        const normText = normalizeParaText(host.textContent || '');
        const hasImg = !!host.querySelector('img');
        if (!normText && !hasImg) continue; // 空段不占 index（与后端 skip 规则一致）
        out.push({
          el: host,
          kind: hasImg && !normText ? 'image' : 'text',
          normText,
          tocLike: isTocLikePara(host),
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
      /** 目录形态但与模型匹配上的段（sdt 内/外无法区分）：映射有效但禁止编辑 */
      readOnlyEls: Set<HTMLElement>;
    };

/** 模型驱动顺序对齐：模型段必须按序在 DOM 中找到同类型且归一化文本相等的段；
 * DOM 多余段（模型没有的）只允许目录形态（w:sdt 展平的目录——后端不占 index）
 * 被跳过，其余一律整体回退（宁可不编辑也不能改错段）。目录形态段即便与模型
 * 匹配（sdt 外目录条目后端占 index）也进 readOnlyEls，由 editify 拒绝开启编辑
 * ——目录段不可编辑，杜绝把改正文写到目录条目上的歧义。 */
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
  const pByEl = new Map<HTMLElement, number>();
  const tableByEl = new Map<HTMLElement, number>();
  const readOnlyEls = new Set<HTMLElement>();
  let j = 0;
  const fail = (reason: string): FidelityMap => ({ ok: false, reason });
  for (let i = 0; i < model.length; i++) {
    const m = model[i];
    if (j >= dom.length) return fail(`第 ${i + 1} 个模型段无对应 DOM 流元素`);
    const d = dom[j];
    const matched =
      d.kind === m.kind && (m.kind === 'table' || d.normText === m.normText);
    if (!matched && !d.tocLike) {
      return fail(`第 ${i + 1} 段与 DOM 不一致（类型 ${d.kind}/${m.kind}）`);
    }
    if (!matched) {
      j++; // 模型没有的目录形态段：跳过（后端 sdt 口径）
      i--;
      continue;
    }
    if (d.kind === 'table') tableByEl.set(d.el, m.index);
    else {
      pByEl.set(d.el, m.index);
      if (d.tocLike) readOnlyEls.add(d.el);
    }
    j++;
  }
  for (; j < dom.length; j++) {
    if (!dom[j].tocLike) {
      return fail(`DOM 末尾多出 ${dom.length - j} 个非目录流元素`);
    }
  }
  return { ok: true, pByEl, tableByEl, readOnlyEls };
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
        const bcText = bc?.text || '';
        const domRaw = cellDomText(td);
        // vMerge 幻影抑制：python-docx r.cells 对垂直合并 continue 位置返回
        // restart 格（同文本），基线 HTML 表头逐行重复；docx-preview 把
        // continue 渲染为空 td。DOM 格空 + 基线同列多行同文本 → 视为未改动
        // （取基线文本），否则空 DOM 会被误报「清空单元格」。用户改字（DOM
        // 非空）不受影响；代价是放弃「清空 vMerge 疑似格」操作（对 vMerge 表
        // 按 row/col 落地本就有歧义）。
        let text: string;
        if (!normalizeParaText(domRaw) && bcText && base) {
          const dupSameCol = base.some(
            (x) => x.col === bc!.col && x.row !== ri && x.text === bcText,
          );
          text = dupSameCol ? bcText : canonicalCellText(domRaw, bcText);
        } else {
          text = canonicalCellText(domRaw, bcText);
        }
        blocks.push({
          paraIndex: idx,
          kind: 'table',
          cell: { row: ri, col },
          text,
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
  /** 目录形态等映射有效但禁止编辑的段（如 sdt 内外目录条目，改正文 vs 改目录有歧义） */
  readOnlyEls?: Set<HTMLElement>;
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
    if (opts.readOnlyEls?.has(el)) continue;
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
