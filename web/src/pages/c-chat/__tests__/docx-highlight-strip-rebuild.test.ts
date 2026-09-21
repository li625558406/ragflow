// stripDocxAnnotationMarks / rebuildPlaceholderSpans 单测：
// 渲染缓存回放的前置清理——缓存树里带上一轮的 mark / 产值 span，
// 回放后必须先还原成「pristine」再按当前数据重涂。
import {
  highlightDocxRanges,
  rebuildPlaceholderSpans,
  stripDocxAnnotationMarks,
  updateDocxHighlight,
} from '@/pages/c-chat/docx-highlight';
import { describe, expect, it } from 'vitest';

describe('stripDocxAnnotationMarks', () => {
  it('剥掉 mark[data-anchor-key] 保留原文，且可再次高亮（回放语义）', () => {
    const el = document.createElement('div');
    el.innerHTML = '<p><span>前</span><span>文</span>后续</p>';
    const marked = highlightDocxRanges(el, [
      { text: '前文', color: '#1a66fb', key: 'k1' },
    ]);
    expect(marked.has('k1')).toBe(true);
    expect(el.querySelector('mark[data-anchor-key]')).toBeTruthy();
    stripDocxAnnotationMarks(el);
    expect(el.querySelector('mark[data-anchor-key]')).toBeNull();
    expect(el.textContent).toContain('前文');
    const again = highlightDocxRanges(el, [
      { text: '前文', color: '#f5222d', key: 'k1' },
    ]);
    expect(again.has('k1')).toBe(true);
  });

  it('showKeyBadge 徽标一并清除，不残留 {{key}} 文本', () => {
    const el = document.createElement('div');
    el.innerHTML = '<p><span>正文内容</span></p>';
    highlightDocxRanges(
      el,
      [{ text: '正文内容', color: '#1a66fb', key: 'k2' }],
      { showKeyBadge: true },
    );
    stripDocxAnnotationMarks(el);
    expect(el.querySelector('mark')).toBeNull();
    expect(el.textContent).not.toContain('{{k2}}');
    expect(el.textContent).toContain('正文内容');
  });

  it('无 mark 时安全空操作', () => {
    const el = document.createElement('div');
    el.innerHTML = '<p>普通文本</p>';
    expect(() => stripDocxAnnotationMarks(el)).not.toThrow();
    expect(el.textContent).toBe('普通文本');
  });
});

describe('rebuildPlaceholderSpans', () => {
  it('按 data-ph-key 重建映射（含同 key 多处），updateDocxHighlight 可据其重涂', () => {
    const el = document.createElement('div');
    el.innerHTML =
      '<p><span data-ph-key="a">旧值A</span>间隔<span data-ph-key="b">b</span></p>' +
      '<p><span data-ph-key="a">旧值A2</span></p>';
    const groups = rebuildPlaceholderSpans(el);
    expect([...groups.keys()].sort()).toEqual(['a', 'b']);
    expect(groups.get('a')!.length).toBe(2);
    updateDocxHighlight(groups, {}, new Map([['a', '字段甲']]));
    // 未填（values 空）：正文显示中文名（有 name）或回落 key
    expect(groups.get('a')![0].textContent).toBe('字段甲');
    expect(groups.get('b')![0].textContent).toBe('b');
  });
});
