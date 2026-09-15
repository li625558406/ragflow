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

  // 归一化（去全部空白）全文 + 节点偏移快照（同时保留原始偏移，供纯空白 anchor 走原文精确匹配）
  const walker = document.createTreeWalker(container, NodeFilter.SHOW_TEXT);
  const entries: Array<{
    node: Text;
    normStart: number;
    normEnd: number;
    rawStart: number;
    rawEnd: number;
  }> = [];
  let acc = 0;
  let rawAcc = 0;
  let n = walker.nextNode() as Text | null;
  while (n) {
    const raw = n.nodeValue || '';
    const len = raw.replace(/\s+/g, '').length;
    entries.push({
      node: n,
      normStart: acc,
      normEnd: acc + len,
      rawStart: rawAcc,
      rawEnd: rawAcc + raw.length,
    });
    acc += len;
    rawAcc += raw.length;
    n = walker.nextNode() as Text | null;
  }
  const parts: string[] = [];
  for (const e of entries) {
    parts.push((e.node.nodeValue || '').replace(/\s+/g, ''));
  }
  const full = parts.join('');
  const fullRaw = entries.map((e) => e.node.nodeValue || '').join('');
  if (!full && !fullRaw) return marked;

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

  // 纯空白 anchor 专用：原始偏移 → (节点, 节点内偏移)。原文不含归一化，
  // 纯算术映射；preferEnd 语义与 locate 一致（节点终点优先于下一节点起点）
  const locateRaw = (
    pos: number,
    preferEnd = false,
  ): { node: Text; off: number } | null => {
    for (const e of entries) {
      if (preferEnd && pos > e.rawStart && pos === e.rawEnd) {
        return { node: e.node, off: e.rawEnd - e.rawStart };
      }
      if (pos >= e.rawStart && pos < e.rawEnd) {
        return { node: e.node, off: pos - e.rawStart };
      }
    }
    return null;
  };

  // raw 偏移换算表：tryResolve 尾部空白收缩与 sortKey 计算都需要
  const nodeRawStart = new Map<Text, number>();
  for (const e of entries) nodeRawStart.set(e.node, e.rawStart);

  // 定位 + 同段校验 + 去重（同 key 只锚一处；同位置不重复插）。
  // 同形 anchor 分配：范本填写点大量同形留白（同串下划线/空格），
  // 若都取首处则只有第一项显示 —— 归一化文本相同的多项按 addr 文档序
  // 依次分配第 1/2/…次出现（occ 语义）；单项/无 addr 组保持首处匹配。
  // 候选有效性校验（同段、边界可解析）前移到选择阶段：无效候选跳过并
  // 继续向后找 —— 旧实现先占位再在插入阶段丢弃，同段失败的候选白占
  // 出现位置，该 key 也直接丢失不再重试。
  const resolved: Array<{
    from: { node: Text; off: number };
    to: { node: Text; off: number };
    sortKey: number;
    item: DocxHighlightItem;
  }> = [];
  // 同一匹配位置只允许一个锚点；norm/raw 偏移空间不同，键加前缀隔离
  const seenPos = new Set<string>();
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

  // 按文本分组，组内带上原顺序。两类匹配通道：
  // - norm（默认）：归一化文本 ≥2 字符，走去空白归一化匹配
  // - raw：纯空白 anchor（同形留白空格串）归一化后为空串，归一化通道永远
  //   匹配不上 —— 改走原文精确匹配（docx-preview 的 textNode 保留原始空格，
  //   fullRaw.indexOf 可定位）；raw 偏移空间与 norm 不同，须严格隔离
  const groups = new Map<
    string,
    Array<{
      item: DocxHighlightItem;
      seq: number;
      text: string;
      mode: 'norm' | 'raw';
    }>
  >();
  let seqCounter = 0;
  for (const item of items) {
    const rawText = item.text || '';
    const text = rawText.replace(/\s+/g, '');
    let mode: 'norm' | 'raw';
    let groupKey: string;
    if (text.length === 0 && rawText.length >= 2) {
      mode = 'raw';
      groupKey = 'R:' + rawText;
    } else if (text.length < 2) {
      continue; // 既非纯空白也过短 → 任何通道都无法稳定匹配
    } else {
      mode = 'norm';
      groupKey = 'N:' + text;
    }
    const arr = groups.get(groupKey) || [];
    arr.push({ item, seq: seqCounter++, text: rawText, mode });
    groups.set(groupKey, arr);
  }

  for (const [, group] of groups) {
    const { mode, text } = group[0];
    // norm 通道必须用归一化文本搜索：anchor 原文含内部空白时（如
    // 「( 批文名称及编号)」括号后有空格），原文对去空白全文做 indexOf
    // 永远失配，整组全军覆没。
    const searchText = mode === 'raw' ? text : text.replace(/\s+/g, '');
    // raw 通道额外要求「完整空白 run」：候选窗口前后必须是非空白字符或
    // 文档边界 —— 留白段由可见文本分隔，更长的空白串内部不存在更短
    // anchor 的合法出现（如 10 空格里嵌不下 8 空格填写位）。
    const fullText = mode === 'raw' ? fullRaw : full;
    // 候选区间解析：起止映射到 (node, off) 并做同段校验，失败返回 null。
    // norm 终点做尾部空白收缩 —— preferEnd 会解析到节点原始末尾，把紧随
    // 的空白 run 吞进区间，与 raw 空白组的占用区间物理重叠：先插入的一方
    // deleteContents 截断共享 textNode，另一方偏移越界被静默丢弃。
    const tryResolve = (
      s: number,
      e: number,
    ): {
      from: { node: Text; off: number };
      to: { node: Text; off: number };
      sortKey: number;
    } | null => {
      const from = mode === 'raw' ? locateRaw(s) : locate(s);
      const to = mode === 'raw' ? locateRaw(e, true) : locate(e, true);
      if (!from || !to) return null;
      const p1 = from.node.parentElement?.closest('p');
      const p2 = to.node.parentElement?.closest('p');
      // 防跨块误删：起止必须在同一 <p>（docx-preview 段落/单元格内容均在
      // p 内），跨段匹配若直接 deleteContents 会破坏块级结构 —— 跳过
      if (!p1 || p1 !== p2) return null;
      if (mode === 'norm') {
        const toStart = nodeRawStart.get(to.node);
        if (toStart !== undefined) {
          let pos = toStart + to.off;
          while (pos > 0 && /\s/.test(fullRaw[pos - 1])) pos--;
          const to2 = locateRaw(pos);
          if (to2 && to2.node.parentElement?.closest('p') === p1) {
            to.node = to2.node;
            to.off = to2.off;
          }
        }
      }
      const nodeStart = nodeRawStart.get(from.node);
      if (nodeStart === undefined) return null;
      return { from, to, sortKey: nodeStart + from.off };
    };
    const attempt = (from: number, item: DocxHighlightItem): number => {
      let i = fullText.indexOf(searchText, Math.max(0, from));
      while (i >= 0) {
        const runExact =
          mode !== 'raw' ||
          ((i === 0 || !/\s/.test(fullText[i - 1])) &&
            (i + searchText.length >= fullText.length ||
              !/\s/.test(fullText[i + searchText.length])));
        if (runExact && !seenPos.has(mode + ':' + i)) {
          const r = tryResolve(i, i + searchText.length);
          if (r) {
            r.item = item;
            resolved.push(r);
            return i;
          }
        }
        i = fullText.indexOf(searchText, i + 1);
      }
      return -1;
    };
    const multi = group.length > 1;
    const ordered = multi
      ? [...group].sort(
          (a, b) =>
            cmpAddr(a.item.addr || '', b.item.addr || '') || a.seq - b.seq,
        )
      : group;
    // 多项组：从上一次命中之后继续找下一次出现；单项组保留 start 提示
    //（仅 norm 通道 —— raw 偏移空间不同，start 提示不可复用），
    // 提示窗口找不到再回退全文扫描。
    let searchFrom = 0;
    for (const { item } of ordered) {
      if (seenKey.has(item.key)) continue;
      const hint =
        !multi && mode === 'norm' && item.start != null && item.start > 4
          ? Math.max(0, item.start - 4)
          : -1;
      let found = attempt(hint >= 0 ? hint : multi ? searchFrom : 0, item);
      if (found < 0 && hint >= 0) {
        found = attempt(multi ? searchFrom : 0, item);
      }
      if (found < 0) continue;
      searchFrom = found + 1;
      seenPos.add(mode + ':' + found);
      seenKey.add(item.key);
    }
  }

  // 已在选择阶段全部解析为具体 (节点, 节点内偏移)，按文档序从后往前插入：
  // 删除右侧区间不影响左侧节点的偏移有效性。norm/raw 两个偏移空间不可
  // 直接比较排序 —— 统一换算成 raw 空间起点作 sortKey。
  resolved.sort((a, b) => b.sortKey - a.sortKey);
  for (const v of resolved) {
    const { from, to } = v;
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
