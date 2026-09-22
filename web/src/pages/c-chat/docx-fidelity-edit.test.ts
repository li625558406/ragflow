// web/src/pages/c-chat/docx-fidelity-edit.test.ts
import { describe, expect, it } from 'vitest';
import {
  collectDocxFlowEls,
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
