// docx 渲染产物缓存单测：stash/take 往返、未知 blob 失效、LRU 淘汰、阈值常量。
// 模块级 WeakMap 跨用例存在 → 每个用例用全新 Blob 对象保证隔离。
import {
  BIG_BLOB_BYTES,
  stashDocxRender,
  takeDocxRender,
} from '@/pages/c-chat/docx-render-cache';
import { describe, expect, it } from 'vitest';

const blob = (s: string) => new Blob([s]);

function tree(text: string): HTMLElement {
  const el = document.createElement('div');
  const p = document.createElement('p');
  p.textContent = text;
  el.appendChild(p);
  return el;
}

describe('docx-render-cache', () => {
  it('stash 后 el 清空；take 后子树原序搬回；holder 已空再 take 失效', () => {
    const b = blob('a');
    const el = tree('保真正文');
    stashDocxRender(b, el);
    expect(el.firstElementChild).toBeNull();
    expect(takeDocxRender(b, el)).toBe(true);
    expect(el.textContent).toBe('保真正文');
    expect(takeDocxRender(b, el)).toBe(false);
  });

  it('未知 blob take false；空容器 stash 不建条目', () => {
    expect(takeDocxRender(blob('x'), document.createElement('div'))).toBe(
      false,
    );
    const b = blob('empty');
    stashDocxRender(b, document.createElement('div'));
    expect(takeDocxRender(b, tree('t'))).toBe(false);
  });

  it('LRU 上限 3：最早未使用的缓存被淘汰', () => {
    const blobs = ['1', '2', '3', '4'].map(blob);
    blobs.forEach((b, i) => stashDocxRender(b, tree(`p-${i + 1}`)));
    expect(takeDocxRender(blobs[0], document.createElement('div'))).toBe(false);
    expect(takeDocxRender(blobs[1], document.createElement('div'))).toBe(true);
  });

  it('stash 刷新 LRU 位次：刚回放又 stash 的树不会被淘汰', () => {
    const blobs = ['a', 'b', 'c', 'd'].map(blob);
    blobs.slice(0, 3).forEach((b) => stashDocxRender(b, tree('x')));
    const el = document.createElement('div');
    takeDocxRender(blobs[0], el);
    stashDocxRender(blobs[0], el); // 位次刷新到最新
    stashDocxRender(blobs[3], tree('y')); // 淘汰的应是 blobs[1]
    expect(takeDocxRender(blobs[0], document.createElement('div'))).toBe(true);
    expect(takeDocxRender(blobs[1], document.createElement('div'))).toBe(false);
  });

  it('BIG_BLOB_BYTES = 2.5MB', () => {
    expect(BIG_BLOB_BYTES).toBe(2.5 * 1024 * 1024);
  });
});
