// 文档模型 diff：编辑器块描述 vs 原文段落，产出后端 /flow/<id>/document/edit
// 契约的三类操作（edits/deletes/inserts）。纯函数、零依赖，可单测。

import type { DocxRun } from './docx-format-utils';

export interface DocxSourceParagraph {
  index: number;
  text: string;
  type: 'heading' | 'paragraph' | 'table' | 'image';
  heading_level?: number;
}

/** 编辑器内一个顶级块的最小描述（由 readEditorBlocks 从 Lexical 模型抽取） */
export interface EditorBlock {
  /** 原文段落 index；undefined = 用户新增（回车/拆分产生） */
  paraIndex?: number;
  kind: 'text' | 'table' | 'image';
  text: string;
  /** run 级格式抽取结果；undefined = 全默认格式（保持旧整段替换行为） */
  runs?: DocxRun[];
  /** runsFmtSig(runs) 缓存，文本相同但签名不同 = 纯改格式 */
  fmtSig?: string;
}

export type DocxDiffOps =
  | { error: string }
  | {
      error?: undefined;
      edits: Array<{ paraIndex: number; newText: string; runs?: DocxRun[] }>;
      deletes: number[];
      inserts: Array<{
        afterParaIndex: number;
        newText: string;
        runs?: DocxRun[];
      }>;
      count: number;
    };

export function diffBlocks(
  blocks: EditorBlock[],
  paragraphs: DocxSourceParagraph[],
): DocxDiffOps {
  const byIdx = new Map(paragraphs.map((p) => [p.index, p]));
  const seen = new Set<number>();
  const edits: Array<{ paraIndex: number; newText: string; runs?: DocxRun[] }> =
    [];
  const deletes: number[] = [];
  const inserts: Array<{
    afterParaIndex: number;
    newText: string;
    runs?: DocxRun[];
  }> = [];
  let lastIdx: number | null = null;

  for (const b of blocks) {
    if (b.paraIndex != null) {
      const idx = b.paraIndex;
      seen.add(idx);
      lastIdx = idx;
      if (b.kind !== 'text') continue; // 表格/图片原子块只记 seen，不参与文本 diff
      const orig = byIdx.get(idx);
      if (!orig) continue;
      const text = b.text.trim();
      if (!text) {
        deletes.push(idx);
      } else if (text !== orig.text.trim()) {
        edits.push({ paraIndex: idx, newText: text, runs: b.runs });
      } else if (
        b.fmtSig &&
        b.fmtSig !== JSON.stringify([{ text: orig.text.trim() }]) &&
        b.fmtSig !== '[]'
      ) {
        // 纯改格式：文本相同但样式签名与「无格式」基线不同
        edits.push({ paraIndex: idx, newText: text, runs: b.runs });
      }
    } else {
      const text = b.text.trim();
      if (text) {
        inserts.push({
          afterParaIndex: lastIdx == null ? -1 : lastIdx,
          newText: text,
          runs: b.runs,
        });
      }
    }
  }

  for (const p of paragraphs) {
    if (!seen.has(p.index)) {
      if (p.type === 'table' || p.type === 'image') {
        return { error: '不支持删除表格/图片，请撤销该操作后保存' };
      }
      deletes.push(p.index);
    }
  }

  return {
    edits,
    deletes,
    inserts,
    count: edits.length + deletes.length + inserts.length,
  };
}

export interface HighlightSegment {
  text: string;
  key?: string;
  color?: string;
}

/** 把段落文本按高亮目标拆成片段（先到先得：与已染色区间重叠的后续命中只切分边界、不再染色） */
export function splitIntoSegments(
  text: string,
  targets: Array<{ text: string; color: string; key: string }>,
): HighlightSegment[] {
  if (!text) return [{ text: '' }];
  const n = text.length;
  // 每个字符的归属（先到先得）；undefined = 普通文本
  const owner: Array<{ key: string; color: string } | undefined> = new Array(n);
  // 切分点：所有目标命中区间的起止位置（已被先前目标占用的命中只产生切分点）
  const cuts = new Set<number>([0, n]);

  for (const t of targets) {
    if (!t.text) continue;
    let pos = text.indexOf(t.text);
    while (pos !== -1) {
      const end = pos + t.text.length;
      cuts.add(pos);
      cuts.add(end);
      let free = true;
      for (let i = pos; i < end; i++) {
        if (owner[i]) {
          free = false;
          break;
        }
      }
      if (free) {
        for (let i = pos; i < end; i++)
          owner[i] = { key: t.key, color: t.color };
      }
      pos = text.indexOf(t.text, end);
    }
  }

  const sorted = [...cuts].sort((a, b) => a - b);
  const segments: HighlightSegment[] = [];
  for (let c = 0; c + 1 < sorted.length; c++) {
    const start = sorted[c];
    const end = sorted[c + 1];
    if (end <= start) continue; // 空区间
    const o = owner[start]; // 切分点对齐命中边界，区间内 owner 必然一致
    const segText = text.slice(start, end);
    const prev = segments[segments.length - 1];
    if (o && prev && prev.key === o.key && prev.color === o.color) {
      prev.text += segText; // 相邻同 key 区间合并（如同一目标跨切分点）
    } else {
      segments.push(
        o ? { text: segText, key: o.key, color: o.color } : { text: segText },
      );
    }
  }
  return segments;
}
