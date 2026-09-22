// web/src/pages/c-chat/docx-fidelity-edit.test.ts
import { describe, expect, it } from 'vitest';
import { diffBlocks, type BaselineCell } from './docx-diff';
import {
  collectDocxFlowEls,
  collectFidelityBlocks,
  editifyDocx,
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

  it('article 结构：正文容器取 section 直子 article，页眉 p 不占 index', () => {
    // 真实 docx-preview（breakPages）产物：section 直子级是 header/article/footer
    const wrap = document.createElement('div');
    wrap.className = 'docx-wrapper';
    const sec = document.createElement('section');
    const hdr = document.createElement('header');
    hdr.innerHTML = '<p>页眉文本</p>';
    const art = document.createElement('article');
    art.innerHTML =
      '<p>正文一</p><p></p><table><tr><td>格</td></tr></table><p>正文二</p>';
    const ftr = document.createElement('footer');
    ftr.innerHTML = '<p>第 1 页</p>';
    sec.append(hdr, art, ftr);
    wrap.appendChild(sec);
    document.body.appendChild(wrap);
    const els = collectDocxFlowEls(wrap);
    expect(els).toHaveLength(3); // 页眉/页脚 p 与空段均不占 index
    expect(els[0].kind).toBe('text');
    expect(els[0].normText).toBe('正文一');
    expect(els[1].kind).toBe('table');
    expect(els[2].normText).toBe('正文二');
    wrap.remove();
  });

  it('目录形态段标记 tocLike：TOC 样式类与内部锚链接全包两种形态', () => {
    const wrap = document.createElement('div');
    wrap.className = 'docx-wrapper';
    const sec = document.createElement('section');
    const art = document.createElement('article');
    art.innerHTML = [
      '<p class="docx_toc1"><a href="#_Toc1"><span>第1章 招标公告</span><span>6</span></a></p>',
      '<p><a href="#_Ref1"><span>交叉引用</span></a></p>', // 全部文本在内部锚链接里
      '<p>正文一</p>',
      '<p><a href="https://example.com"><span>外链段</span></a>尾注</p>', // 外链+段外文本，非目录形态
    ].join('');
    sec.appendChild(art);
    wrap.appendChild(sec);
    document.body.appendChild(wrap);
    const els = collectDocxFlowEls(wrap);
    expect(els).toHaveLength(4);
    expect(els[0].tocLike).toBe(true);
    expect(els[1].tocLike).toBe(true);
    expect(els[2].tocLike).toBe(false);
    expect(els[2].normText).toBe('正文一');
    expect(els[3].tocLike).toBe(false);
    expect(els[3].normText).toBe('外链段尾注');
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

  it('DOM 多余目录段（sdt 展平）被跳过：模型驱动对齐 ok', () => {
    // 真实场景：w:sdt（目录）被 docx-preview 展平成 53 个普通段，后端模型没有
    const wrap = document.createElement('div');
    wrap.className = 'docx-wrapper';
    const sec = document.createElement('section');
    const art = document.createElement('article');
    art.innerHTML =
      '<p class="docx_toc1"><a href="#_Toc1"><span>第1章 目录条目</span><span>6</span></a></p>' +
      '<p class="docx_toc2"><a href="#_Toc2"><span>第2章 目录条目二</span><span>9</span></a></p>' +
      '<p>正文一</p>';
    sec.appendChild(art);
    wrap.appendChild(sec);
    document.body.appendChild(wrap);
    const r = mapDocxParas(wrap, [P(0, '正文一')]);
    expect(r.ok).toBe(true);
    if (r.ok) {
      expect(r.pByEl.size).toBe(1);
      expect(r.readOnlyEls.size).toBe(0); // 多余目录段被跳过，不进映射
    }
    wrap.remove();
  });

  it('sdt 外目录条目与模型匹配 → 进 readOnlyEls（映射有效但禁编辑）', () => {
    // 真实场景：目录条目在 sdt 外（后端占 index），与正文段文本可能相同
    const wrap = document.createElement('div');
    wrap.className = 'docx-wrapper';
    const sec = document.createElement('section');
    const art = document.createElement('article');
    art.innerHTML =
      '<p class="docx_toc1"><a href="#_Toc1"><span>十三、汇总表</span></a></p>' +
      '<p>十三、汇总表</p>';
    sec.appendChild(art);
    wrap.appendChild(sec);
    document.body.appendChild(wrap);
    const r = mapDocxParas(wrap, [P(0, '十三、汇总表'), P(1, '十三、汇总表')]);
    expect(r.ok).toBe(true);
    if (r.ok) {
      expect(r.pByEl.size).toBe(2);
      expect(r.readOnlyEls.size).toBe(1); // 只有目录形态段被禁编辑
      const [first] = Array.from(r.pByEl.keys());
      expect(r.readOnlyEls.has(first)).toBe(true);
    }
    wrap.remove();
  });

  it('对抗：DOM 多余段非目录形态 → ok:false（绝不带病映射）', () => {
    const wrap = buildWrap([
      { kind: 'p', text: '多余正文段' },
      { kind: 'p', text: '正文一' },
    ]);
    expect(mapDocxParas(wrap, [P(0, '正文一')]).ok).toBe(false);
    wrap.remove();
  });

  it('对抗：模型段无对应 DOM（DOM 先耗尽）→ ok:false', () => {
    const wrap = buildWrap([{ kind: 'p', text: '唯一段' }]);
    expect(mapDocxParas(wrap, [P(0, '唯一段'), P(1, '缺失段')]).ok).toBe(false);
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

  it('对抗：vMerge 幻影抑制——基线同列多行同文本且 DOM 格空 → 视为未改动', () => {
    // 真实场景：python-docx r.cells 对垂直合并 continue 位置返回 restart 格文本，
    // naive.py 基线 HTML 表头逐行重复；docx-preview 把 continue 渲染为空 td。
    const wrap = document.createElement('div');
    wrap.className = 'docx-wrapper';
    const sec = document.createElement('section');
    const table = document.createElement('table');
    table.innerHTML =
      '<tr><td>列名甲</td><td>列名乙</td></tr>' + '<tr><td></td><td></td></tr>'; // docx-preview：vMerge continue 渲染空格
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
          { row: 0, col: 0, colSpan: 1, header: false, text: '列名甲' },
          { row: 0, col: 1, colSpan: 1, header: false, text: '列名乙' },
          // 基线侧：continue 位置重复了 restart 文本（python-docx 语义）
          { row: 1, col: 0, colSpan: 1, header: false, text: '列名甲' },
          { row: 1, col: 1, colSpan: 1, header: false, text: '列名乙' },
        ],
      ],
    ]);
    const blocks = collectFidelityBlocks(model, r, baselines);
    const ops = diffBlocks(blocks, model, baselines);
    if ('error' in ops) throw new Error(ops.error);
    expect(ops.tableEdits).toEqual([]);
    expect(ops.count).toBe(0);
    wrap.remove();
  });

  it('对抗：vMerge 特征不吞真实改动——同列文本唯一时清空仍产 tableEdit', () => {
    const wrap = document.createElement('div');
    wrap.className = 'docx-wrapper';
    const sec = document.createElement('section');
    const table = document.createElement('table');
    table.innerHTML = '<tr><td>列名甲</td></tr><tr><td></td></tr>';
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
          { row: 0, col: 0, colSpan: 1, header: false, text: '列名甲' },
          { row: 1, col: 0, colSpan: 1, header: false, text: '独值' },
        ],
      ],
    ]);
    const blocks = collectFidelityBlocks(model, r, baselines);
    const ops = diffBlocks(blocks, model, baselines);
    if ('error' in ops) throw new Error(ops.error);
    expect(ops.tableEdits).toEqual([
      { paraIndex: 0, row: 1, col: 0, newText: '', runs: undefined },
    ]);
    wrap.remove();
  });

  it('对抗：vMerge 抑制不吞用户改字——DOM 格非空走正常 diff', () => {
    const wrap = document.createElement('div');
    wrap.className = 'docx-wrapper';
    const sec = document.createElement('section');
    const table = document.createElement('table');
    table.innerHTML = '<tr><td>列名甲</td></tr><tr><td>改成新值</td></tr>';
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
          { row: 0, col: 0, colSpan: 1, header: false, text: '列名甲' },
          { row: 1, col: 0, colSpan: 1, header: false, text: '列名甲' },
        ],
      ],
    ]);
    const blocks = collectFidelityBlocks(model, r, baselines);
    const ops = diffBlocks(blocks, model, baselines);
    if ('error' in ops) throw new Error(ops.error);
    expect(ops.tableEdits).toEqual([
      { paraIndex: 0, row: 1, col: 0, newText: '改成新值', runs: undefined },
    ]);
    wrap.remove();
  });
});

// ── Task 3: 编辑守卫 ──────────────────────────────────────────

function fireBeforeInput(el: Element, inputType: string): boolean {
  const ev = new Event('beforeinput', {
    bubbles: true,
    cancelable: true,
  }) as InputEvent;
  Object.defineProperty(ev, 'inputType', { value: inputType });
  el.dispatchEvent(ev);
  return ev.defaultPrevented;
}

describe('editifyDocx', () => {
  it('对映射段落与表格 td 开 contentEditable；dispose 还原', () => {
    const wrap = buildWrap([{ kind: 'p', text: '甲' }, { kind: 'table' }]);
    const r = mapDocxParas(wrap, [
      P(0, '甲'),
      { index: 1, text: '<table/>', type: 'table' },
    ]);
    if (!r.ok) throw new Error('map failed');
    // buildWrap 的 table 没有行，手动补一行供 td 断言
    const table = Array.from(r.tableByEl.keys())[0] as HTMLTableElement;
    table.innerHTML = '<tr><td>格</td></tr>';
    const blocked: string[] = [];
    const dispose = editifyDocx(wrap, {
      pEls: r.pByEl,
      tableByEl: r.tableByEl,
      onInput: () => {},
      onStructBlocked: (m) => blocked.push(m),
    });
    const p = Array.from(r.pByEl.keys())[0];
    expect(p.getAttribute('contenteditable')).toBe('true');
    expect(table.rows[0].cells[0].getAttribute('contenteditable')).toBe('true');
    dispose();
    expect(p.getAttribute('contenteditable')).toBeNull();
    expect(table.rows[0].cells[0].getAttribute('contenteditable')).toBeNull();
    wrap.remove();
  });

  it('回车（insertParagraph）被拦截并提示', () => {
    const wrap = buildWrap([{ kind: 'p', text: '甲' }]);
    const r = mapDocxParas(wrap, [P(0, '甲')]);
    if (!r.ok) throw new Error('map failed');
    const blocked: string[] = [];
    const dispose = editifyDocx(wrap, {
      pEls: r.pByEl,
      tableByEl: r.tableByEl,
      onInput: () => {},
      onStructBlocked: (m) => blocked.push(m),
    });
    const p = Array.from(r.pByEl.keys())[0];
    expect(fireBeforeInput(p, 'insertParagraph')).toBe(true);
    expect(blocked).toHaveLength(1);
    dispose();
    wrap.remove();
  });

  it('段中间退格放行；段首退格（会并段）拦截', () => {
    const wrap = buildWrap([{ kind: 'p', text: '甲乙' }]);
    const r = mapDocxParas(wrap, [P(0, '甲乙')]);
    if (!r.ok) throw new Error('map failed');
    const blocked: string[] = [];
    const dispose = editifyDocx(wrap, {
      pEls: r.pByEl,
      tableByEl: r.tableByEl,
      onInput: () => {},
      onStructBlocked: (m) => blocked.push(m),
    });
    const p = Array.from(r.pByEl.keys())[0] as HTMLElement;
    p.innerHTML = '<span>甲</span><span>乙</span>';
    const sel = window.getSelection()!;

    // 段首（"甲"前无字符）→ 拦截
    const first = p.childNodes[0].firstChild!;
    const r3 = document.createRange();
    r3.setStart(first, 0);
    r3.collapse(true);
    sel.removeAllRanges();
    sel.addRange(r3);
    expect(fireBeforeInput(p, 'deleteContentBackward')).toBe(true);
    expect(blocked).toHaveLength(1);

    // 段中间（"乙"开头，其前有"甲"可删）→ 放行
    const second = p.childNodes[1].firstChild!;
    const r2 = document.createRange();
    r2.setStart(second, 0);
    r2.collapse(true);
    sel.removeAllRanges();
    sel.addRange(r2);
    expect(fireBeforeInput(p, 'deleteContentBackward')).toBe(false);
    expect(blocked).toHaveLength(1);
    dispose();
    wrap.remove();
  });

  it('拖放被拦截', () => {
    const wrap = buildWrap([{ kind: 'p', text: '甲' }]);
    const r = mapDocxParas(wrap, [P(0, '甲')]);
    if (!r.ok) throw new Error('map failed');
    const dispose = editifyDocx(wrap, {
      pEls: r.pByEl,
      tableByEl: r.tableByEl,
      onInput: () => {},
      onStructBlocked: () => {},
    });
    const p = Array.from(r.pByEl.keys())[0];
    const ev = new Event('drop', { bubbles: true, cancelable: true });
    p.dispatchEvent(ev);
    expect(ev.defaultPrevented).toBe(true);
    dispose();
    wrap.remove();
  });
});
