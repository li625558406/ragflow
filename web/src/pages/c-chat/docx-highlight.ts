// docx-preview 渲染结果的高亮后处理：在保真 DOM 上做两类文本替换/标注。
// Word 会把一段文字拆成多段 run（渲染后为多个相邻内联元素），必须跨节点合并
// 匹配 —— 先拼接全文定位匹配区间，再用 Range 跨节点 deleteContents + insertNode
// 替换。从后往前处理保证前置偏移不失效。
// 约束：docxtpl 占位符不跨段落/单元格；文本标注起止不同 <p> 时跳过（防
// deleteContents 破坏块级结构）。

const PLACEHOLDER_RE = /\{\{([a-z][a-z0-9_]*)\}\}/g;

interface Match {
  start: number;
  end: number;
  key: string;
}

/** key → 该 key 在文档中的全部占位符 span（同 key 多处出现时整体更新） */
export type DocxPlaceholderSpans = Map<string, HTMLSpanElement[]>;

function stylePlaceholderSpan(
  span: HTMLSpanElement,
  values: Record<string, string>,
  key: string,
): void {
  span.dataset.phKey = key;
  const v = values[key];
  // 公共样式：继承 Word 上下文字号（不硬编码字号），保真优先
  span.style.backgroundColor = '#EFF4FF';
  span.style.color = '#1a66fb';
  span.style.borderRadius = '2px';
  span.style.padding = '0 2px';
  if (v !== undefined && v !== '') {
    span.textContent = v;
    span.title = key;
    span.style.border = '';
    span.style.fontSize = '';
  } else {
    // 未填：虚线槽位显示 key；key 较长，略缩字号避免撑版
    span.textContent = key;
    span.title = `${key}（等待 AI 填入）`;
    span.style.border = '1px dashed rgba(26, 102, 251, 0.6)';
    span.style.fontSize = '0.85em';
  }
}

/**
 * 扫描容器内全部 {{key}} 占位符并替换为高亮 span。
 * 返回 key → span[] 映射，供 updateDocxHighlight 增量更新（values 变化零 DOM 重建）。
 */
export function applyDocxHighlight(
  container: HTMLElement,
  values: Record<string, string>,
): DocxPlaceholderSpans {
  const groups: DocxPlaceholderSpans = new Map();
  // 1. 有序收集全部文本节点
  const walker = document.createTreeWalker(container, NodeFilter.SHOW_TEXT);
  const textNodes: Text[] = [];
  while (walker.nextNode()) textNodes.push(walker.currentNode as Text);
  if (textNodes.length === 0) return groups;

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
  if (matches.length === 0) return groups;

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
      const span = document.createElement('span');
      stylePlaceholderSpan(span, values, key);
      range.deleteContents();
      range.insertNode(span);
      const arr = groups.get(key) || [];
      arr.push(span);
      groups.set(key, arr);
    } catch {
      // 单个匹配替换失败（如节点已被上层修改）不影响其余匹配
    }
  }
  return groups;
}

/**
 * 增量更新 applyDocxHighlight 产出的占位符 span（values 变化时调用）：
 * 只改已有 span 的文本/样式，不做任何 DOM 重建 —— 大文档下 filling 事件
 * 高频到达也不卡顿。
 */
export function updateDocxHighlight(
  groups: DocxPlaceholderSpans,
  values: Record<string, string>,
): void {
  for (const [key, spans] of groups) {
    for (const span of spans) stylePlaceholderSpan(span, values, key);
  }
}

/**
 * 大文档滚动优化：给 docx-preview 渲染出的每个分页 <section> 设
 * content-visibility: auto，屏外页面跳过布局与绘制。
 */
export function applyDocxPageLazy(container: HTMLElement): void {
  container.querySelectorAll('section').forEach((sec) => {
    const el = sec as HTMLElement;
    el.style.contentVisibility = 'auto';
    el.style.containIntrinsicSize = '794px 1123px';
  });
}

// ── 文本标注高亮（review-panel 只读保真用）──────────────────────────

export interface DocxHighlightItem {
  /** 要高亮的文本（按去空白归一化匹配，与 review-panel findTextEndRect 同口径） */
  text: string;
  /** 锚定 key，写入 mark 的 data-anchor-key（批注栏 measure/点击定位依据） */
  key: string;
  /** 高亮主色（底色取 color+'22'、下边框取 color） */
  color: string;
  /** 锚点选段在归一化文本中的起始偏移（消歧重复文本；可空） */
  start?: number | null;
  /** 填写点段落定位（B端范本预览用）：同形 anchor 多项按 addr 文档序分配第 1/2/…次出现 */
  addr?: string;
}

/**
 * 在 docx DOM 上按文本定位并插入 mark[data-anchor-key]。
 * 返回成功插入 mark 的 key 集合 —— 未命中的 key 由调用方归入未定位兜底列表。
 * opts.showKeyBadge：mark 末尾追加 {{key}} 内联徽标（B端范本保真预览用，
 * C端审核预览不传则行为不变）。
 */
export function highlightDocxRanges(
  container: HTMLElement,
  items: DocxHighlightItem[],
  opts?: { showKeyBadge?: boolean },
): Set<string> {
  const marked = new Set<string>();
  if (!items.length) return marked;

  // 归一化（去全部空白）全文 + 节点偏移快照
  const walker = document.createTreeWalker(container, NodeFilter.SHOW_TEXT);
  const entries: Array<{ node: Text; normStart: number; normEnd: number }> = [];
  let acc = 0;
  let n = walker.nextNode() as Text | null;
  while (n) {
    const raw = n.nodeValue || '';
    const len = raw.replace(/\s+/g, '').length;
    entries.push({ node: n, normStart: acc, normEnd: acc + len });
    acc += len;
    n = walker.nextNode() as Text | null;
  }
  const parts: string[] = [];
  for (const e of entries) {
    parts.push((e.node.nodeValue || '').replace(/\s+/g, ''));
  }
  const full = parts.join('');
  if (!full) return marked;

  // 归一化偏移 → 原始 (文本节点, 节点内偏移)。
  // preferEnd：pos 恰为某节点归一化终点时优先解析为该节点末尾而非下一节点
  // 开头 —— Range 语义上二者等价，但同段校验取 node.parentElement，
  // 解析到下一节点会误判跨段（段落末尾留白 anchor 全军覆没的根因）。
  const locate = (
    pos: number,
    preferEnd = false,
  ): { node: Text; off: number } | null => {
    for (const e of entries) {
      const inFwd = pos >= e.normStart && pos < e.normEnd;
      const atEnd = pos > e.normStart && pos === e.normEnd;
      if (!inFwd && !(preferEnd && atEnd)) continue;
      const raw = e.node.nodeValue || '';
      if (atEnd) return { node: e.node, off: raw.length };
      let cnt = e.normStart;
      for (let j = 0; j < raw.length; j++) {
        if (cnt >= pos) return { node: e.node, off: j };
        if (!/\s/.test(raw[j])) cnt++;
      }
      return { node: e.node, off: raw.length };
    }
    for (let i = entries.length - 1; i >= 0; i--) {
      if (entries[i].normEnd === pos) {
        return {
          node: entries[i].node,
          off: (entries[i].node.nodeValue || '').length,
        };
      }
    }
    return null;
  };

  // 定位 + 同段校验 + 去重（同 key 只锚一处；同位置不重复插）。
  // 同形 anchor 分配：范本填写点大量同形留白（同串下划线/空格），
  // 若都取首处则只有第一项显示 —— 归一化文本相同的多项按 addr 文档序
  // 依次分配第 1/2/…次出现（occ 语义）；单项/无 addr 组保持首处匹配。
  const valid: Array<{ s: number; e: number; item: DocxHighlightItem }> = [];
  const seenPos = new Set<number>();
  const seenKey = new Set<string>();

  const cmpAddr = (a: string, b: string): number => {
    const at = a.split(':');
    const bt = b.split(':');
    const len = Math.max(at.length, bt.length);
    for (let i = 0; i < len; i++) {
      const x = at[i];
      const y = bt[i];
      if (x === undefined) return -1;
      if (y === undefined) return 1;
      if (x === y) continue;
      if (/^\d+$/.test(x) && /^\d+$/.test(y)) return Number(x) - Number(y);
      return x < y ? -1 : 1;
    }
    return 0;
  };

  // 按归一化文本分组，组内带上原顺序
  const groups = new Map<
    string,
    Array<{ item: DocxHighlightItem; seq: number }>
  >();
  let seqCounter = 0;
  for (const item of items) {
    const text = (item.text || '').replace(/\s+/g, '');
    if (text.length < 2) continue;
    const arr = groups.get(text) || [];
    arr.push({ item, seq: seqCounter++ });
    groups.set(text, arr);
  }

  for (const [text, group] of groups) {
    const multi = group.length > 1;
    const ordered = multi
      ? [...group].sort(
          (a, b) =>
            cmpAddr(a.item.addr || '', b.item.addr || '') || a.seq - b.seq,
        )
      : group;
    // 多项组：从上一次命中之后继续找下一次出现；单项组保留 start 提示
    let searchFrom = 0;
    for (const { item } of ordered) {
      if (seenKey.has(item.key)) continue;
      let idx =
        !multi && item.start != null && item.start > 4
          ? full.indexOf(text, Math.max(0, item.start - 4))
          : -1;
      if (idx < 0) {
        idx = multi ? full.indexOf(text, searchFrom) : full.indexOf(text);
      }
      if (idx < 0 || seenPos.has(idx)) continue;
      searchFrom = idx + 1;
      seenPos.add(idx);
      seenKey.add(item.key);
      valid.push({ s: idx, e: idx + text.length, item });
    }
  }

  // 从后往前插入：删右侧区间不影响左侧偏移有效性
  valid.sort((a, b) => b.s - a.s);
  for (const v of valid) {
    const from = locate(v.s);
    const to = locate(v.e, true);
    if (!from || !to) continue;
    // 防跨块误删：起止必须在同一 <p>（docx-preview 段落/单元格内容均在 p 内），
    // 跨段匹配若直接 deleteContents 会破坏块级结构 —— 跳过留给未定位兜底
    const p1 = from.node.parentElement?.closest('p');
    const p2 = to.node.parentElement?.closest('p');
    if (!p1 || p1 !== p2) continue;
    try {
      const range = document.createRange();
      range.setStart(from.node, from.off);
      range.setEnd(to.node, to.off);
      const mark = document.createElement('mark');
      mark.dataset.anchorKey = v.item.key;
      // 保留原始内容（含空白），不做归一化文本回写
      mark.appendChild(range.cloneContents());
      mark.style.backgroundColor = `${v.item.color}22`;
      mark.style.borderBottom = `2px solid ${v.item.color}`;
      mark.style.color = 'inherit';
      mark.style.cursor = 'pointer';
      mark.style.borderRadius = '1px';
      mark.style.padding = '0 1px';
      if (opts?.showKeyBadge && v.item.key) {
        const badge = document.createElement('span');
        badge.textContent = `{{${v.item.key}}}`;
        badge.style.display = 'inline-block';
        badge.style.fontSize = '10px';
        badge.style.lineHeight = '1.5';
        badge.style.backgroundColor = v.item.color;
        badge.style.color = '#fff';
        badge.style.borderRadius = '3px';
        badge.style.padding = '0 4px';
        badge.style.marginLeft = '3px';
        badge.style.verticalAlign = '2px';
        mark.appendChild(badge);
      }
      range.deleteContents();
      range.insertNode(mark);
      marked.add(v.item.key);
    } catch {
      // 单个标注失败不影响其余
    }
  }
  return marked;
}
