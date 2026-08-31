// 文档视图共享工具：表格 HTML 清洗与高亮（review-panel 静态渲染与
// docx-paragraph-editor AtomicBlockNode 共用，从 review-panel.tsx 迁出）

/** 去除 HTML 标签与标点/空白/特殊符号，用于批注文本的宽松匹配 */
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

/**
 * 在表格 HTML 中给 target 文本包上高亮 <mark>（整串命中优先，失败降级为
 * 按标点切块逐个包裹）。color 为 6 位 hex（不带 #），追加 '22' 作浅底色。
 * anchorKey 传入时外层包 <a data-anchor-key> 供点击联动。
 */
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
  // 函数式 replacement：target 可能含 $& 等特殊替换序列，不能用字符串替换
  const rep = (s: string) => s.replace(target, () => wrap(target));

  if (html.includes(target)) {
    return rep(html);
  }

  const chunks = target
    .split(/[，。、；：的且在持有满足进行评价以下含\n]/)
    .filter((c) => c.length >= 5);
  let result = html;
  let replaced = false;
  chunks.sort((a, b) => b.length - a.length);
  for (const chunk of chunks) {
    if (result.includes(chunk)) {
      result = result.replace(chunk, () => wrap(chunk));
      replaced = true;
    }
  }
  if (replaced) return result;

  const normTarget = normalizeForMatch(target);
  if (normTarget.length >= 6) {
    const chunks2 = target
      .split(/[,，。、；：\s]/)
      .filter((c) => c.length >= 4);
    for (const chunk of chunks2) {
      if (result.includes(chunk)) {
        result = result.replace(chunk, () => wrap(chunk));
        replaced = true;
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
