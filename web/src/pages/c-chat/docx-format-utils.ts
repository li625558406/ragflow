// web/src/pages/c-chat/docx-format-utils.ts
// 文档格式纯函数：Lexical TextNode style 串的解析/合并/剔除，与 runs
// 格式签名（diff 判断「纯改格式」用）。零依赖，可单测。

export interface DocxRun {
  text: string;
  bold?: boolean;
  italic?: boolean;
  underline?: boolean;
  strike?: boolean;
  superscript?: boolean;
  subscript?: boolean;
  /** #RRGGBB */
  color?: string;
  /** #RRGGBB，run 背景底纹 */
  bg_color?: string;
  /** 字体族名（同时写入 ascii/eastAsia） */
  font?: string;
  /** pt 值 */
  size?: number;
}

/** 解析 CSS 内联 style 串 → 键值对（键全小写，值 trim） */
export function parseStyle(style: string): Record<string, string> {
  const out: Record<string, string> = {};
  if (!style) return out;
  for (const decl of style.split(';')) {
    const i = decl.indexOf(':');
    if (i <= 0) continue;
    const key = decl.slice(0, i).trim().toLowerCase();
    const val = decl.slice(i + 1).trim();
    if (key && val) out[key] = val;
  }
  return out;
}

/** 序列化键值对 → style 串 */
function stringifyStyle(map: Record<string, string>): string {
  return Object.entries(map)
    .map(([k, v]) => `${k}:${v}`)
    .join(';');
}

/** 在原 style 基础上设置 set 中的键、删除 removeKeys 中的键（set 与 remove 同键时以 remove 为准） */
export function mergeStyle(
  style: string,
  set: Record<string, string>,
  removeKeys: string[] = [],
): string {
  const map = parseStyle(style);
  for (const [k, v] of Object.entries(set)) {
    if (v) map[k] = v;
    else delete map[k];
  }
  for (const k of removeKeys) delete map[k];
  return stringifyStyle(map);
}

/** 剔除 background-color（批注高亮 UI 色不参与落盘导出） */
export function stripBgFromStyle(style: string): string {
  return mergeStyle(style, {}, ['background-color']);
}

/** runs 样式签名（不含 text）：文本相同时比较签名判断「纯改格式」 */
export function runsFmtSig(runs: DocxRun[] | undefined): string | undefined {
  if (!runs) return undefined;
  return JSON.stringify(
    runs.map((r) => {
      const rest = { ...r };
      delete rest.text;
      return rest;
    }),
  );
}
