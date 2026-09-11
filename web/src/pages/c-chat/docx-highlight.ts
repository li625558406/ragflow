// docx-preview 渲染结果的占位符高亮后处理：在保真 DOM 上把 {{key}} 替换为
// 蓝色填入值 / 虚线待填槽位。Word 会把占位符拆成多段 run（渲染后为多个相邻
// 内联元素），必须跨节点合并匹配 —— 先拼接全文定位匹配区间，再用 Range
// 跨节点 deleteContents + insertNode 替换。从后往前处理保证前置偏移不失效。
// 约束：docxtpl 占位符不跨段落/单元格，只存在同段落内跨 run 的情形。

const PLACEHOLDER_RE = /\{\{([a-z][a-z0-9_]*)\}\}/g;

interface Match {
  start: number;
  end: number;
  key: string;
}

function makeHighlightSpan(values: Record<string, string>, key: string) {
  const span = document.createElement('span');
  const v = values[key];
  // 公共样式：继承 Word 上下文字号（不硬编码字号），保真优先
  span.style.backgroundColor = '#EFF4FF';
  span.style.color = '#1a66fb';
  span.style.borderRadius = '2px';
  span.style.padding = '0 2px';
  if (v !== undefined && v !== '') {
    span.textContent = v;
    span.title = key;
  } else {
    // 未填：虚线槽位显示 key；key 较长，略缩字号避免撑版
    span.textContent = key;
    span.title = `${key}（等待 AI 填入）`;
    span.style.border = '1px dashed rgba(26, 102, 251, 0.6)';
    span.style.fontSize = '0.85em';
  }
  return span;
}

export function applyDocxHighlight(
  container: HTMLElement,
  values: Record<string, string>,
): void {
  // 1. 有序收集全部文本节点
  const walker = document.createTreeWalker(container, NodeFilter.SHOW_TEXT);
  const textNodes: Text[] = [];
  while (walker.nextNode()) textNodes.push(walker.currentNode as Text);
  if (textNodes.length === 0) return;

  // 2. 拼接全文 + 每个节点的偏移区间快照
  const spans: Array<{ node: Text; start: number; end: number }> = [];
  let offset = 0;
  for (const node of textNodes) {
    spans.push({ node, start: offset, end: offset + node.data.length });
    offset += node.data.length;
  }
  const full = textNodes.map((n) => n.data).join('');

  // 3. 全文扫描占位符
  const matches: Match[] = [];
  PLACEHOLDER_RE.lastIndex = 0;
  let m: RegExpExecArray | null;
  while ((m = PLACEHOLDER_RE.exec(full)) !== null) {
    matches.push({ start: m.index, end: m.index + m[0].length, key: m[1] });
  }
  if (matches.length === 0) return;

  // 绝对偏移 → (文本节点, 节点内偏移)；落在边界时归入包含它的节点
  const locate = (pos: number): { node: Text; off: number } | null => {
    for (const s of spans) {
      if (pos >= s.start && pos < s.end) {
        return { node: s.node, off: pos - s.start };
      }
    }
    // pos === 全文末尾（或空节点串联处）：归入最后一个节点末尾
    for (let i = spans.length - 1; i >= 0; i--) {
      if (spans[i].end === pos) {
        return { node: spans[i].node, off: spans[i].node.data.length };
      }
    }
    return null;
  };

  // 4. 从后往前替换：删右侧区间不影响左侧节点的偏移有效性
  for (let i = matches.length - 1; i >= 0; i--) {
    const { start, end, key } = matches[i];
    const from = locate(start);
    const to = locate(end);
    if (!from || !to) continue;
    try {
      const range = document.createRange();
      range.setStart(from.node, from.off);
      range.setEnd(to.node, to.off);
      range.deleteContents();
      range.insertNode(makeHighlightSpan(values, key));
    } catch {
      // 单个匹配替换失败（如节点已被上层修改）不影响其余匹配
    }
  }
}
