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
  /** run 级格式抽取结果；undefined = 全默认格式（保持旧整段替换行为）。
   * 不变量：各 run.text 拼接必须等于 block text（trim 前），后端按 runs
   * 重建段落时以此校验一致性 */
  runs?: DocxRun[];
  /** runsFmtSig(runs) 缓存，文本相同但签名不同 = 纯改格式 */
  fmtSig?: string;
  /** 块级属性（Word 段落级落盘）：对齐 format type（''=默认左）、
   * 缩进级 0-8、标题级别（null=正文，1-3=Heading 2-4；非 Docx 块 undefined） */
  align?: string;
  indent?: number;
  headingLevel?: number | null;
  /** kind='table' 时的 docx 逻辑网格坐标（readEditorBlocks 按 colSpan 累加） */
  cell?: { row: number; col: number };
}

/** 表格基线单元格（review-panel 用 parseTableCells 从初始 HTML 解析，与灌入同源） */
export interface BaselineCell {
  row: number;
  col: number;
  colSpan: number;
  header: boolean;
  text: string;
}

export type DocxDiffOps =
  | { error: string }
  | {
      error?: undefined;
      edits: Array<{
        paraIndex: number;
        newText: string;
        runs?: DocxRun[];
        align?: string;
        indent?: number;
        headingLevel?: number | null;
      }>;
      deletes: number[];
      inserts: Array<{
        afterParaIndex: number;
        newText: string;
        runs?: DocxRun[];
        align?: string;
        indent?: number;
        headingLevel?: number;
      }>;
      /** 单元格级改动：与 edits/deletes/inserts 并列，计入 count 与 200 上限 */
      tableEdits: Array<{
        paraIndex: number;
        row: number;
        col: number;
        /** 允许空串（清空单元格） */
        newText: string;
        runs?: DocxRun[];
      }>;
      count: number;
    };

/** 原文段落的块级基线：编辑器初始灌入即此状态（对齐''/缩进0/标题按类型派生，
 * heading_level 1-3 → 级别 1-3，>3 灌入降级 h3 → 级别 2），用户未动块级属性时
 * 当前值 === 基线 → 不产生块级 edit，后端保留原段落 pPr */
function baseHeadingOf(p: DocxSourceParagraph): number | null {
  if (p.type !== 'heading') return null;
  const lvl = p.heading_level ?? 1;
  return lvl <= 3 ? lvl : 2;
}

export function diffBlocks(
  blocks: EditorBlock[],
  paragraphs: DocxSourceParagraph[],
  /** 表格基线；缺省/缺某表基线 → 该表改动全部跳过（保护，不报错） */
  tableBaselines?: Map<number, BaselineCell[]>,
): DocxDiffOps {
  const byIdx = new Map(paragraphs.map((p) => [p.index, p]));
  const seen = new Set<number>();
  const edits: Array<{
    paraIndex: number;
    newText: string;
    runs?: DocxRun[];
    align?: string;
    indent?: number;
    headingLevel?: number | null;
  }> = [];
  const deletes: number[] = [];
  const inserts: Array<{
    afterParaIndex: number;
    newText: string;
    runs?: DocxRun[];
    align?: string;
    indent?: number;
    headingLevel?: number;
  }> = [];
  const tableEdits: Array<{
    paraIndex: number;
    row: number;
    col: number;
    newText: string;
    runs?: DocxRun[];
  }> = [];
  let lastIdx: number | null = null;

  for (const b of blocks) {
    if (b.paraIndex != null) {
      const idx = b.paraIndex;
      seen.add(idx);
      lastIdx = idx;
      if (b.kind === 'table' && b.cell) {
        // 单元格 diff：基线缺失/网格错位 → 跳过（保护）；空文本是清空格，不是 delete
        const base = tableBaselines?.get(idx);
        if (base) {
          const bc = base.find(
            (x) => x.row === b.cell!.row && x.col === b.cell!.col,
          );
          if (bc) {
            const text = b.text.trim();
            const pureFmt =
              text === bc.text.trim() &&
              b.runs &&
              b.fmtSig &&
              b.fmtSig !== '[]';
            if (text !== bc.text.trim() || pureFmt) {
              tableEdits.push({
                paraIndex: idx,
                row: b.cell.row,
                col: b.cell.col,
                newText: text,
                ...(pureFmt ? { runs: b.runs } : {}),
              });
            }
          }
        }
        continue;
      }
      if (b.kind !== 'text') continue; // 图片原子块只记 seen
      const orig = byIdx.get(idx);
      if (!orig) continue;
      const text = b.text.trim();
      // 块级属性相对基线的变化（文本/run 格式/块级可同时变，需合并进同一条 edit）；
      // 仅在确实变化时携带对应字段，后端只应用已提供的键、其余 pPr 原样保留。
      // headingLevel undefined（旧调用方/非 Docx 块）视作与基线一致不产生 edit
      const alignChanged = !!b.align;
      const indentChanged = (b.indent ?? 0) !== 0;
      const baseHeading = baseHeadingOf(orig);
      const curHeading =
        b.headingLevel === undefined ? baseHeading : b.headingLevel;
      const headingChanged = curHeading !== baseHeading;
      const blockAttrs = {
        ...(alignChanged ? { align: b.align } : {}),
        ...(indentChanged ? { indent: b.indent } : {}),
        ...(headingChanged ? { headingLevel: curHeading } : {}),
      };
      const hasBlockAttrs = Object.keys(blockAttrs).length > 0;
      if (!text) {
        deletes.push(idx);
      } else if (text !== orig.text.trim()) {
        edits.push({
          paraIndex: idx,
          newText: text,
          runs: b.runs,
          ...blockAttrs,
        });
      } else if ((b.runs && b.fmtSig && b.fmtSig !== '[]') || hasBlockAttrs) {
        // 纯改格式（run 签名变化或块级属性变化）：文本相同。run 格式变化
        // 但 runs 缺失时不产生 edit，避免整段替换把原文档已有格式抹成默认
        // 格式（'[]' 为空 runs 兜底，条件里已排除该情形）。
        edits.push({
          paraIndex: idx,
          newText: text,
          ...(b.runs && b.fmtSig && b.fmtSig !== '[]' ? { runs: b.runs } : {}),
          ...blockAttrs,
        });
      }
    } else {
      const text = b.text.trim();
      if (text) {
        inserts.push({
          afterParaIndex: lastIdx == null ? -1 : lastIdx,
          newText: text,
          runs: b.runs,
          align: b.align || undefined,
          indent: b.indent || undefined,
          headingLevel: b.headingLevel ?? undefined,
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
    tableEdits,
    count: edits.length + deletes.length + inserts.length + tableEdits.length,
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
