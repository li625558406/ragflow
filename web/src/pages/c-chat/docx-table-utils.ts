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

/** 收集单元格文本：跳过嵌套 <table> 子树（嵌套表格按纯文本忽略）、<br> 归 \n、&nbsp; 归空格 */
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
      const colSpan = Number.isFinite(n)
        ? Math.max(1, Math.min(MAX_COLSPAN, n))
        : 1;
      out.push({ row, col, colSpan, header: tag === 'th', text: cellText(td) });
      col += colSpan;
    }
  });
  return out;
}
