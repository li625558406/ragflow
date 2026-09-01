/** @jest-environment jsdom */
import { parseTableCells } from './docx-table-utils';

describe('parseTableCells', () => {
  it('解析普通 2x2 表格', () => {
    const html =
      '<table><tr><td>甲</td><td>乙</td></tr><tr><td>丙</td><td>丁</td></tr></table>';
    expect(parseTableCells(html)).toEqual([
      { row: 0, col: 0, colSpan: 1, header: false, text: '甲' },
      { row: 0, col: 1, colSpan: 1, header: false, text: '乙' },
      { row: 1, col: 0, colSpan: 1, header: false, text: '丙' },
      { row: 1, col: 1, colSpan: 1, header: false, text: '丁' },
    ]);
  });

  it('colspan 占据多个网格列（被覆盖格位不产生独立格）', () => {
    const html = "<table><tr><td colspan='2'>跨列</td><td>右</td></tr></table>";
    expect(parseTableCells(html)).toEqual([
      { row: 0, col: 0, colSpan: 2, header: false, text: '跨列' },
      { row: 0, col: 2, colSpan: 1, header: false, text: '右' },
    ]);
  });

  it('忽略 caption；th 记 header=true（鲁棒性，naive.py 不产出 th）', () => {
    const html =
      '<table><caption>Table Location: 第一章</caption><tr><th>表头</th></tr></table>';
    const cells = parseTableCells(html);
    expect(cells).toEqual([
      { row: 0, col: 0, colSpan: 1, header: true, text: '表头' },
    ]);
  });

  it('单元格内 \\n 保留（python-docx cell.text 多段落以 \\n 连接）', () => {
    const html = '<table><tr><td>第一段\n第二段</td></tr></table>';
    expect(parseTableCells(html)[0].text).toBe('第一段\n第二段');
  });

  it('嵌套 table 文本被忽略、&nbsp; 归一为空格（对抗性）', () => {
    const html =
      '<table><tr><td>外层<table><tr><td>内层</td></tr></table></td><td>a&nbsp;b</td></tr></table>';
    const cells = parseTableCells(html);
    expect(cells).toHaveLength(2);
    expect(cells[0].text).toBe('外层');
    expect(cells[1].text).toBe('a b');
  });

  it('嵌套表不推移外层行号', () => {
    const html =
      '<table><tr><td>第一行<table><tr><td>内层A</td></tr><tr><td>内层B</td></tr></table></td></tr><tr><td>第二行</td></tr></table>';
    const cells = parseTableCells(html);
    expect(cells).toHaveLength(2);
    expect(cells[1]).toEqual({
      row: 1,
      col: 0,
      colSpan: 1,
      header: false,
      text: '第二行',
    });
  });

  it('畸形输入：空串 / 无 table / 未闭合标签 / 非法 colspan / 超大 colspan', () => {
    expect(parseTableCells('')).toEqual([]);
    expect(parseTableCells('<div>not a table</div>')).toEqual([]);
    expect(parseTableCells('<table><tr><td>未闭合')).toEqual([
      { row: 0, col: 0, colSpan: 1, header: false, text: '未闭合' },
    ]);
    expect(
      parseTableCells("<table><tr><td colspan='abc'>x</td></tr></table>")[0]
        .colSpan,
    ).toBe(1);
    expect(
      parseTableCells("<table><tr><td colspan='99999'>x</td></tr></table>")[0]
        .colSpan,
    ).toBe(50);
  });

  it('空单元格 text 为空串', () => {
    const html = '<table><tr><td></td><td>有字</td></tr></table>';
    expect(parseTableCells(html)[0].text).toBe('');
  });
});
