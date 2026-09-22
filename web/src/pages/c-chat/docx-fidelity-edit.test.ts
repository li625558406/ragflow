// web/src/pages/c-chat/docx-fidelity-edit.test.ts
import { describe, expect, it } from 'vitest';
import { diffBlocks, type BaselineCell } from './docx-diff';
import {
  collectDocxFlowEls,
  collectFidelityBlocks,
  mapDocxParas,
  normalizeParaText,
  type DocxSourceParagraph,
} from './docx-fidelity-edit';

const P = (index: number, text: string): DocxSourceParagraph => ({
  index,
  text,
  type: 'paragraph',
});

/** 搭一个 docx-preview 形状的容器：section 直子级 p/table，页眉页脚在 header/footer 里 */
function buildWrap(
  items: Array<
    | { kind: 'p'; text: string; img?: boolean }
    | { kind: 'empty-p' }
    | { kind: 'table' }
    | { kind: 'header' }
  >,
): HTMLElement {
  const wrap = document.createElement('div');
  wrap.className = 'docx-wrapper';
  const sec = document.createElement('section');
  for (const it of items) {
    if (it.kind === 'header') {
      const h = document.createElement('header');
      h.innerHTML = '<p>页眉文本</p>';
      sec.appendChild(h);
    } else if (it.kind === 'table') {
      sec.appendChild(document.createElement('table'));
    } else if (it.kind === 'empty-p') {
      sec.appendChild(document.createElement('p'));
    } else {
      const p = document.createElement('p');
      p.textContent = it.text;
      if (it.img) p.appendChild(document.createElement('img'));
      sec.appendChild(p);
    }
  }
  wrap.appendChild(sec);
  document.body.appendChild(wrap);
  return wrap;
}

describe('normalizeParaText', () => {
  it('剥全部空白与控制符', () => {
    expect(normalizeParaText(' 第一\u00a0章\t标题\n')).toBe('第一章标题');
    expect(normalizeParaText('a\x00\x08b')).toBe('ab');
    expect(normalizeParaText('')).toBe('');
  });
});

describe('collectDocxFlowEls', () => {
  it('按文档序收集 p/table，跳过空段与页眉页脚', () => {
    const wrap = buildWrap([
      { kind: 'header' },
      { kind: 'p', text: '第一段' },
      { kind: 'empty-p' },
      { kind: 'table' },
      { kind: 'p', text: '第二段', img: true }, // 有图有文本 = 文本段
      { kind: 'p', text: '', img: true }, // 空文本且有图 = 图片段
    ]);
    const els = collectDocxFlowEls(wrap);
    expect(els).toHaveLength(4);
    expect(els[0].kind).toBe('text');
    expect(els[0].normText).toBe('第一段');
    expect(els[1].kind).toBe('table');
    expect(els[2].kind).toBe('text');
    expect(els[3].kind).toBe('image');
    wrap.remove();
  });
});

describe('mapDocxParas', () => {
  it('全量对齐：空段双侧跳过后逐位匹配', () => {
    const wrap = buildWrap([
      { kind: 'p', text: '第一段' },
      { kind: 'empty-p' },
      { kind: 'table' },
      { kind: 'p', text: '第 二\u00a0段' }, // 空白差异归一化后相等
    ]);
    const r = mapDocxParas(wrap, [
      P(0, '第一段'),
      { index: 1, text: '<table><tr><td>x</td></tr></table>', type: 'table' },
      P(2, '第二段'),
    ]);
    expect(r.ok).toBe(true);
    if (r.ok) {
      expect(r.pByEl.size).toBe(2);
      expect(r.tableByEl.size).toBe(1);
    }
    wrap.remove();
  });

  it('对抗：数量不一致 → ok:false（不给出任何部分映射）', () => {
    const wrap = buildWrap([
      { kind: 'p', text: 'a' },
      { kind: 'p', text: 'b' },
    ]);
    expect(mapDocxParas(wrap, [P(0, 'a')]).ok).toBe(false);
    wrap.remove();
  });

  it('对抗：文本不一致 → ok:false，绝不带病映射', () => {
    const wrap = buildWrap([{ kind: 'p', text: '实际渲染' }]);
    expect(mapDocxParas(wrap, [P(0, '模型文本')]).ok).toBe(false);
    wrap.remove();
  });

  it('对抗：类型错位（DOM 表 vs 模型段）→ ok:false', () => {
    const wrap = buildWrap([{ kind: 'table' }]);
    expect(mapDocxParas(wrap, [P(0, 'x')]).ok).toBe(false);
    wrap.remove();
  });
});

// ── Task 2: 块抽取 ────────────────────────────────────────────

describe('collectFidelityBlocks', () => {
  it('未改动段落取模型原文（canonical）：diff 零幻影', () => {
    const wrap = buildWrap([{ kind: 'p', text: '第一  段\t文本' }]);
    const model = [P(0, '第一 段 文本')];
    const r = mapDocxParas(wrap, model);
    if (!r.ok) throw new Error('map failed');
    const blocks = collectFidelityBlocks(model, r);
    const ops = diffBlocks(blocks, model);
    expect('error' in ops && ops.error).toBeFalsy();
    if (!('error' in ops)) expect(ops.count).toBe(0);
    wrap.remove();
  });

  it('改动段落产出 edits；diffBlocks 只含 edits（无 deletes/inserts）', () => {
    const wrap = buildWrap([
      { kind: 'p', text: '原文A' },
      { kind: 'p', text: '原文B' },
    ]);
    const model = [P(0, '原文A'), P(1, '原文B')];
    const r = mapDocxParas(wrap, model);
    if (!r.ok) throw new Error('map failed');
    (Array.from(r.pByEl.keys())[1] as HTMLElement).textContent = '改成B';
    const blocks = collectFidelityBlocks(model, r);
    const ops = diffBlocks(blocks, model);
    if ('error' in ops) throw new Error(ops.error);
    expect(ops.edits).toEqual([
      { paraIndex: 1, newText: '改成B', runs: undefined },
    ]);
    expect(ops.deletes).toHaveLength(0);
    expect(ops.inserts).toHaveLength(0);
    wrap.remove();
  });

  it('表格单元格抽取：colspan 累加坐标、多段格 \\n 连接、格改动产 tableEdits', () => {
    const wrap = document.createElement('div');
    wrap.className = 'docx-wrapper';
    const sec = document.createElement('section');
    const table = document.createElement('table');
    table.innerHTML =
      '<tr><td>甲</td><td colspan="2">乙一段<br>乙二段</td><td>丙</td></tr>';
    sec.appendChild(table);
    wrap.appendChild(sec);
    document.body.appendChild(wrap);
    const model = [{ index: 0, text: '<table/>', type: 'table' as const }];
    const r = mapDocxParas(wrap, model);
    if (!r.ok) throw new Error('map failed');
    const baselines = new Map<number, BaselineCell[]>([
      [
        0,
        [
          { row: 0, col: 0, colSpan: 1, header: false, text: '甲' },
          { row: 0, col: 1, colSpan: 2, header: false, text: '乙一段\n乙二段' },
          { row: 0, col: 3, colSpan: 1, header: false, text: '丙' },
        ],
      ],
    ]);
    // 用户改了合并格
    const tds = (Array.from(r.tableByEl.keys())[0] as HTMLTableElement).rows[0]
      .cells;
    tds[1].innerHTML = '乙新一段<br>乙二段';
    const blocks = collectFidelityBlocks(model, r, baselines);
    const ops = diffBlocks(blocks, model, baselines);
    if ('error' in ops) throw new Error(ops.error);
    expect(ops.tableEdits).toEqual([
      {
        paraIndex: 0,
        row: 0,
        col: 1,
        newText: '乙新一段\n乙二段',
        runs: undefined,
      },
    ]);
    expect(ops.count).toBe(1);
    wrap.remove();
  });

  it('对抗：清空整段 → diff 产 deletes，由上层拦截', () => {
    const wrap = buildWrap([{ kind: 'p', text: '原文A' }]);
    const model = [P(0, '原文A')];
    const r = mapDocxParas(wrap, model);
    if (!r.ok) throw new Error('map failed');
    (Array.from(r.pByEl.keys())[0] as HTMLElement).textContent = '';
    const ops = diffBlocks(collectFidelityBlocks(model, r), model);
    if ('error' in ops) throw new Error(ops.error);
    expect(ops.deletes).toEqual([0]);
    wrap.remove();
  });
});
