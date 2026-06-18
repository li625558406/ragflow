/* ── Shared markdown → Word HTML conversion ── */

function escapeHtml(s: string): string {
  return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

/* ── Inline markdown → HTML ── */

function processInlineMarkdown(text: string): string {
  let s = escapeHtml(text);

  // Inline code — protect first via placeholders so formatting inside
  // backticks is preserved as-is
  const codePlaceholders: string[] = [];
  s = s.replace(/`([^`]+)`/g, (_, code: string) => {
    const idx = codePlaceholders.length;
    codePlaceholders.push(
      `<code style="font-family:Consolas,monospace;background:#f5f5f5;padding:1pt 4pt;border-radius:3pt;">${code}</code>`,
    );
    return `\x00CODE${idx}\x00`;
  });

  // Images: ![alt](url) → remove
  s = s.replace(/!\[([^\]]*)\]\([^)]+\)/g, '');

  // Links: [text](url) → <a> tag
  s = s.replace(
    /\[([^\]]+)\]\(([^)]+)\)/g,
    '<a href="$2" style="color:#2563EB;text-decoration:underline;">$1</a>',
  );

  // Bold + Italic
  s = s.replace(/\*\*\*(.+?)\*\*\*/g, '<strong><em>$1</em></strong>');

  // Bold
  s = s.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
  s = s.replace(/__(.+?)__/g, '<strong>$1</strong>');

  // Italic
  s = s.replace(/\*(.+?)\*/g, '<em>$1</em>');
  s = s.replace(/_(.+?)_/g, '<em>$1</em>');

  // Strikethrough
  s = s.replace(/~~(.+?)~~/g, '<del>$1</del>');

  // Restore code placeholders
  // eslint-disable-next-line no-control-regex
  s = s.replace(/\x00CODE(\d+)\x00/g, (_, idx: string) => {
    return codePlaceholders[parseInt(idx, 10)] || '';
  });

  return s;
}

/* ── Markdown table parsing ── */

function parseMarkdownTable(tableText: string): {
  headers: string[];
  rows: string[][];
  aligns: string[];
} | null {
  const lines = tableText.trim().split('\n');
  if (lines.length < 2) return null;

  const parseRow = (line: string) =>
    line
      .replace(/^\|/, '')
      .replace(/\|$/, '')
      .split('|')
      .map((c) => c.trim());

  const headers = parseRow(lines[0]);
  if (headers.length === 0) return null;

  const aligns = parseRow(lines[1]).map((cell) => {
    const left = cell.startsWith(':');
    const right = cell.endsWith(':');
    if (left && right) return 'center';
    if (right) return 'right';
    return 'left';
  });

  const rows = lines.slice(2).map(parseRow);

  return { headers, rows, aligns };
}

function renderWordTable(
  headers: string[],
  rows: string[][],
  aligns: string[],
): string {
  const border = 'border:1px solid #999;';
  const td = (a: string) =>
    `padding:3pt 6pt;${border}font-size:10pt;text-align:${a};`;
  const th = (a: string) =>
    `padding:3pt 6pt;${border}font-size:10pt;font-weight:bold;text-align:${a};background-color:#f5f5f5;`;

  const thead = `<tr>${headers
    .map(
      (h, i) =>
        `<th style="${th(aligns[i] || 'left')}">${processInlineMarkdown(h)}</th>`,
    )
    .join('')}</tr>`;

  const tbody = rows
    .map(
      (row) =>
        `<tr>${row
          .map(
            (cell, i) =>
              `<td style="${td(aligns[i] || 'left')}">${processInlineMarkdown(cell)}</td>`,
          )
          .join('')}</tr>`,
    )
    .join('');

  return `<table style="border-collapse:collapse;margin:10pt 0;width:100%;${border}">
<thead>${thead}</thead>
<tbody>${tbody}</tbody>
</table>`;
}

/* ── Markdown → Word body HTML (no document wrapper) ── */

export function markdownToBodyHtml(text: string): string {
  text = text.replace(/\r\n/g, '\n');

  const parts: string[] = [];
  const lines = text.split('\n');
  let tableLines: string[] = [];
  let inTable = false;
  let paraLines: string[] = [];
  let inCodeBlock = false;
  let codeBlockContent: string[] = [];

  function flushPara() {
    if (paraLines.length > 0) {
      const content = paraLines
        .map((l) => (l.trim() ? processInlineMarkdown(l) : ''))
        .filter(Boolean)
        .join('<br/>');
      if (content) {
        parts.push(
          `<p style="font-size:11pt;line-height:1.8;margin:0 0 8pt 0;text-align:justify;">${content}</p>`,
        );
      }
      paraLines = [];
    }
  }

  function flushTable() {
    if (tableLines.length >= 2) {
      const table = parseMarkdownTable(tableLines.join('\n'));
      if (table) {
        parts.push(renderWordTable(table.headers, table.rows, table.aligns));
      }
    }
    tableLines = [];
    inTable = false;
  }

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    const trimmed = line.trim();

    // Code block fences
    if (/^```/.test(trimmed)) {
      if (inCodeBlock) {
        flushPara();
        flushTable();
        parts.push(
          `<pre style="font-family:Consolas,monospace;font-size:9pt;background:#f5f5f5;padding:8pt 12pt;border-radius:4pt;line-height:1.6;white-space:pre-wrap;word-break:break-all;">${escapeHtml(codeBlockContent.join('\n'))}</pre>`,
        );
        codeBlockContent = [];
        inCodeBlock = false;
      } else {
        flushPara();
        flushTable();
        inCodeBlock = true;
      }
      continue;
    }

    if (inCodeBlock) {
      codeBlockContent.push(line);
      continue;
    }

    // Role labels → H2
    if (/^【(?:用户|助手)】$/.test(trimmed)) {
      flushPara();
      flushTable();
      parts.push(
        `<h2 style="font-size:14pt;font-weight:bold;margin-top:14pt;margin-bottom:6pt;color:#1a1a1a;">${escapeHtml(trimmed.replace(/【(.+)】/, '$1'))}</h2>`,
      );
      continue;
    }

    // Horizontal rules
    if (/^[-*_]{3,}\s*$/.test(trimmed)) {
      flushPara();
      flushTable();
      parts.push(
        '<hr style="border:none;border-top:1px solid #ccc;margin:12pt 0;" />',
      );
      continue;
    }

    // Table row detection
    if (/^\|.+\|$/.test(trimmed)) {
      flushPara();
      if (!inTable) {
        inTable = true;
        tableLines = [trimmed];
      } else {
        tableLines.push(trimmed);
      }
      continue;
    } else if (inTable) {
      flushTable();
    }

    // Headings
    const headingMatch = trimmed.match(/^(#{1,6})\s+(.+)$/);
    if (headingMatch) {
      flushPara();
      const level = headingMatch[1].length;
      const headingText = processInlineMarkdown(headingMatch[2]);
      const sizes: Record<number, string> = {
        1: '18pt',
        2: '15pt',
        3: '13pt',
        4: '12pt',
        5: '11pt',
        6: '10pt',
      };
      parts.push(
        `<h${level} style="font-size:${sizes[level]};font-weight:bold;margin-top:12pt;margin-bottom:6pt;color:#1a1a1a;">${headingText}</h${level}>`,
      );
      continue;
    }

    // Empty line
    if (!trimmed) {
      flushPara();
      continue;
    }

    // Regular paragraph text
    paraLines.push(line);
  }

  // Flush remaining
  flushTable();
  flushPara();

  return parts.join('\n');
}

/* ── Wrap body HTML in Word document template (for download) ── */

export function wrapWordDocument(bodyHtml: string, title: string): string {
  return `<!DOCTYPE html>
<html xmlns:o="urn:schemas-microsoft-com:office:office"
      xmlns:w="urn:schemas-microsoft-com:office:word"
      xmlns="http://www.w3.org/TR/REC-html40">
<head>
<meta charset="utf-8">
<meta http-equiv="Content-Type" content="text/html; charset=utf-8">
<title>${escapeHtml(title)}</title>
<!--[if gte mso 9]><xml>
<w:WordDocument>
  <w:View>Print</w:View>
  <w:Zoom>100</w:Zoom>
  <w:DoNotOptimizeForBrowser/>
</w:WordDocument>
</xml><![endif]-->
<style>
@page { size: A4; margin: 2cm; }
body {
  font-family: "Microsoft YaHei", "宋体", SimSun, sans-serif;
  font-size: 11pt;
  color: #333;
  line-height: 1.8;
}
</style>
</head>
<body>
<h1 style="font-size:18pt;font-weight:bold;margin-bottom:12pt;color:#1a1a1a;">${escapeHtml(title)}</h1>
${bodyHtml}
</body>
</html>`;
}

/* ── Download helper ── */

export function downloadWord(title: string, bodyHtml: string) {
  const html = wrapWordDocument(bodyHtml, title);
  const blob = new Blob(['\ufeff' + html], {
    type: 'application/msword;charset=utf-8',
  });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = `${title}.doc`;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}
