// 文档视图共享工具：表格 HTML 清洗与高亮 + 批注文本匹配（review-panel 静态渲染与
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

// ── 批注文本匹配（从 review-panel.tsx 迁出：单文件私有改共享，可单测） ──

/** 批注的最小匹配面：review-panel 的 Annotation（带索引签名）天然兼容 */
export interface MatchableAnnotation {
  matched_text?: string;
  text?: string;
  quote?: string;
  status?: string;
  patch?: { find: string; replace: string } | null;
  [key: string]: unknown;
}

/** 卡片展示用摘录（含字段名兼容） */
export function getMatchedText(ann: MatchableAnnotation): string {
  return (ann.matched_text || ann.text || ann.quote || '').trim();
}

/** 定位/高亮目标文本：已修复或已确认保留且有补丁时，成稿里该处已是修复后内容
 * （patch.replace），matched_text 在成稿中已不存在——卡片展示仍用 matched_text，
 * 仅定位/高亮切到 replace。回退后（open+无 patch）自然回到 matched_text，
 * 与恢复原文的新版本重新对上。 */
export function getLocateText(ann: MatchableAnnotation): string {
  if (
    (ann.status === 'fixed' || ann.status === 'resolved') &&
    ann.patch?.replace
  ) {
    return ann.patch.replace;
  }
  return getMatchedText(ann);
}

/**
 * 单段匹配（target 为定位文本）。策略依次：整串 → 去标签 → 归一化全文 →
 * 关键词块 ≥2。target 传空串（长度 <2）一律 false。
 */
export function matchAnnotation(
  paragraphText: string,
  target: string,
): boolean {
  if (!target || target.length < 2) return false;
  // Strategy 1: exact match
  if (paragraphText.includes(target)) return true;
  // Strategy 2: HTML-stripped match (for table paragraphs)
  const cleanPara = paragraphText.replace(/<[^>]+>/g, '');
  if (cleanPara.includes(target)) return true;
  // Strategy 3: normalized full match (strip all punctuation)
  const normPara = normalizeForMatch(paragraphText);
  const normTarget = normalizeForMatch(target);
  if (normTarget.length >= 4 && normPara.includes(normTarget)) return true;
  // Strategy 4: keyword match — extract 2-3 key phrases (8+ chars) from target
  // and check if at least 2 appear in the paragraph
  const keywords = [];
  const chunks = target
    .split(/[，。、；：的且在持有满足进行评价以下含]/)
    .filter((c) => c.length >= 6);
  for (const chunk of chunks.slice(0, 4)) {
    const normChunk = normalizeForMatch(chunk);
    if (normChunk.length >= 4 && normPara.includes(normChunk)) {
      keywords.push(chunk);
    }
  }
  return keywords.length >= 2;
}

/** 跨行摘录的分行归一化（matched_text 含 \n 且非空归一化行 ≥2 才可走序列通道） */
export function multilineLineNorms(matchedText: string): string[] {
  if (!matchedText || !matchedText.includes('\n')) return [];
  return matchedText
    .split('\n')
    .map((l) => normalizeForMatch(l))
    .filter((l) => l.length >= 2);
}

/**
 * 跨行摘录序列匹配：LLM 摘录常为多行拼接（matched_text 含 \n），单段 includes
 * 必然失配。按行（过滤空行）在段落序列上滑窗：连续 N 段中第 i 段包含第 i 行，
 * 与后端修复轮 _apply_multiline_patch 连续段落序列定位同构。
 *
 * 有序降级（唯命中优先，减少「文档里明明有却未定位」）：
 * ① 序列唯命中 → 该基段；
 * ② 序列多命中 / 零命中 → 首行通道：最长行 norm 命中的文档序第一个段落
 *    （与单行 dup 定位首现同口径——dup 本就定位首现，多行不应反而更差）；
 * ③ 全部失败 → -1（诚实未定位，如归一化后为空的纯符号摘录）。
 * 注：原设计的「拼接通道」（某段 norm 含全部行 norm 依次拼接）被首行通道
 * **严格覆盖**——拼接命中蕴含每行都含于该段，最长行必先命中（find 同取文档
 * 序第一个），单独实现只会是不可达死代码，故不设。
 * 返回基段落（首行所在段）index。
 */
export function matchMultiline(
  paragraphs: { index: number; text: string }[],
  matchedText: string,
): number {
  const lines = multilineLineNorms(matchedText);
  if (lines.length < 2) return -1;
  const paraNorms = paragraphs.map((p) => ({
    index: p.index,
    norm: normalizeForMatch(p.text),
  }));
  // 通道 1：连续段落序列（唯命中闸）
  let base = -1;
  let hits = 0;
  for (let b = 0; b + lines.length <= paraNorms.length; b++) {
    let ok = true;
    for (let i = 0; i < lines.length; i++) {
      if (!paraNorms[b + i].norm.includes(lines[i])) {
        ok = false;
        break;
      }
    }
    if (ok) {
      hits++;
      if (hits > 1) break;
      base = paraNorms[b].index;
    }
  }
  if (hits === 1) return base;
  // 通道 2：首行通道（最长行，文档序第一个命中段）
  const longest = [...lines].sort((a, b) => b.length - a.length)[0];
  if (longest) {
    const first = paraNorms.find((p) => p.norm.includes(longest));
    if (first) return first.index;
  }
  return -1;
}

/**
 * 批注状态 overlay 合并：props 批注是父组件的会话内存快照，而确认保留/回退
 * mutation 只 invalidate state 查询、无人刷新 props。按 annotation id 把服务端
 * state 里的最新 {status, patch} 覆盖到 props 批注上（不改父组件）。state 里
 * 找不到的批注（新到的 props / 已删行）原样保留；无变化（status 相同且 patch
 * 值相等）返回原引用，避免 state 轮询期无谓的重渲染。
 */
export function mergeAnnotationOverlays<
  T extends MatchableAnnotation & { id?: unknown },
>(
  annotations: T[],
  latest: Array<{
    id: unknown;
    status?: string;
    patch?: { find: string; replace: string } | null;
  }>,
): T[] {
  if (!latest.length) return annotations;
  const byId = new Map(latest.map((a) => [String(a.id), a]));
  let changed = false;
  const out = annotations.map((ann) => {
    const newer = ann.id != null ? byId.get(String(ann.id)) : undefined;
    if (!newer) return ann;
    const samePatch =
      (newer.patch?.find || '') === (ann.patch?.find || '') &&
      (newer.patch?.replace || '') === (ann.patch?.replace || '');
    if (newer.status === ann.status && samePatch) return ann;
    changed = true;
    return { ...ann, status: newer.status, patch: newer.patch };
  });
  // 全部无变化时返回原数组：useMemo 链路按引用比较，state 轮询重放相同数据
  // 不能触发下游 annotationMap / rail 全量重算
  return changed ? out : annotations;
}
