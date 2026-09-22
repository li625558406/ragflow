// web/src/pages/c-chat/docx-fidelity-edit.ts
// docx-preview 保真树上段落级编辑（路线 D，2026-09-22）：
// 严格对齐层——DOM 顶层流元素与 content.paragraphs 做 1:1 顺序对齐，任一不符
// 整体回退旧 Lexical 编辑视图（宁可不编辑也不能改错段，paraIndex 错位会静默
// 改坏后端段落）。对齐规则复刻后端 _build_para_map 的遍历口径：空文本且无图
// 的 w:p 不占 index、整表占一个 index、图片段占 index。

import type { DocxSourceParagraph } from './docx-diff';

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
