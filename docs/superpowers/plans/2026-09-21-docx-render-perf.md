# 审核面板/范本预览大文件渲染优化 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** C端文件审核弹框（ReviewPanel）与范本填写实时预览（TemplateFillLivePreview）打开/重开大 docx 不再卡死：>2.5MB 默认文本降级（审核面板补齐范本预览已有的门槛）+ 渲染产物按 Blob 缓存、重开毫秒级回放。

**Architecture:** docx-preview 的 `renderAsync` 是同步主线程全量建 DOM（JSZip 解压 + XML 解析 + 整树构建），`content-visibility:auto` 只省排版/绘制救不了这一步；组件卸载即销毁整棵 DOM，重开必然整本重渲染。方案：①新增模块 `docx-render-cache.ts`，渲染 effect 卸载时把产物子树摘进按 **Blob 对象键**（WeakMap，Blob 被 React Query 逐出后整树可 GC）的离屏容器，重开 `appendChild` 回放；树只存在一份（不在 el 就在 holder），内存不因缓存翻倍，LRU 上限 3 棵。②回放的树可能带上一轮 mark/产值 span，配套 `stripDocxAnnotationMarks` / `rebuildPlaceholderSpans` 剥离后按当前数据重涂。③审核面板补 `>2.5MB` 文本降级（与范本预览同构），且**超大体量改派生判定**（`blob.size > 阈值` 直接算，不用 state+effect），根除范本预览既有的「首帧先白渲染一次大文件再翻转」浪费。

**Tech Stack:** React 18 + TypeScript + Vitest + @testing-library/react + docx-preview（产物为静态 DOM、无事件绑定，appendChild 回放安全；图片经 blob: URL 在页面存续期内有效，回放不掉图）。

**范围约定：** 纯前端 4 个文件（1 新建 + 3 修改）+ 4 个测试文件。**不部署**（项目规则：部署 = `npm run build` + dist 上传 + nginx reload，须用户明确指示）。在主仓当前分支直接开发 commit（与近期前端迭代一致，不开 worktree）。

---

## File Structure

| 文件 | 动作 | 职责 |
|------|------|------|
| `web/src/pages/c-chat/docx-render-cache.ts` | 新建 | 渲染产物缓存（stash/take + LRU 3）+ `BIG_BLOB_BYTES` 单一来源 |
| `web/src/pages/c-chat/docx-highlight.ts` | 修改 | 新增 `stripDocxAnnotationMarks` / `rebuildPlaceholderSpans`（回放前置清理，纯函数可测） |
| `web/src/pages/c-chat/review-panel.tsx` | 修改 | A：oversize 门槛（派生判定 + 强制保真 + 横幅按钮）；B：渲染 effect 接缓存 |
| `web/src/pages/c-chat/template-fill-live-preview.tsx` | 修改 | B：接缓存 + blobOversize 从 state 改派生 |
| `web/src/pages/c-chat/__tests__/docx-render-cache.test.ts` | 新建 | 缓存模块单测 |
| `web/src/pages/c-chat/__tests__/docx-highlight-strip-rebuild.test.ts` | 新建 | strip/rebuild 单测 |
| `web/src/pages/c-chat/__tests__/review-panel-render-perf.test.tsx` | 新建 | 审核面板门槛+缓存组件级回归 |
| `web/src/pages/c-chat/__tests__/template-fill-live-preview-cache.test.tsx` | 新建 | 实时预览缓存+派生门槛回归 |

---

### Task 1: docx-render-cache.ts 缓存模块

**Files:**
- Create: `web/src/pages/c-chat/docx-render-cache.ts`
- Test: `web/src/pages/c-chat/__tests__/docx-render-cache.test.ts`

- [ ] **Step 1: 写失败测试**

```ts
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
    expect(takeDocxRender(blob('x'), document.createElement('div'))).toBe(false);
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
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd web && npx vitest run src/pages/c-chat/__tests__/docx-render-cache.test.ts`
Expected: FAIL（模块不存在）

- [ ] **Step 3: 实现模块**

```ts
// docx 渲染产物缓存（2026-09-21 大文件渲染优化）：
// docx-preview 的 renderAsync 是同步主线程全量建 DOM（JSZip 解压 + XML 解析 +
// 整树构建），大文件打开瞬间卡死，content-visibility 懒渲染只省排版/绘制救不了
// 这一步；而关闭弹框/抽屉组件卸载整棵 DOM 销毁，重开同一文件要整本重渲染。
// 本模块把渲染产物按 Blob 对象键（WeakMap——Blob 被 React Query 逐出后整树随之
// GC）摘进离屏容器保留，重开时 appendChild 回放（毫秒级）。
// 内存治理：树在世界里只存在一份（不在 el 就在 holder），缓存不使内存翻倍；
// LRU 上限 3 棵，防多文件轮流打开时离屏树无限囤积（forceClosedLivePreview 只
// 限「可见」树，管不到离屏缓存）。高亮产物不缓存语义：缓存树里可能带上一轮
// mark/产值 span，回放后由调用方 strip/rebuild + 按当前数据重涂（见
// docx-highlight 的 stripDocxAnnotationMarks / rebuildPlaceholderSpans）。

const MAX_ENTRIES = 3;
const stash = new WeakMap<Blob, HTMLDivElement>();
const order: Blob[] = []; // 新 → 旧；WeakMap 不可枚举，配额淘汰靠这条引用序

function ensureHolder(blob: Blob): HTMLDivElement {
  let holder = stash.get(blob);
  if (!holder) {
    holder = document.createElement('div');
    stash.set(blob, holder);
  }
  const i = order.indexOf(blob);
  if (i > 0) {
    order.splice(i, 1);
    order.unshift(blob);
  } else if (i < 0) {
    order.unshift(blob);
    // 配额淘汰：清空最旧 holder 的内容（WeakMap 条目保留，空 holder 等效失效）
    while (order.length > MAX_ENTRIES) {
      const oldest = order.pop();
      const h = oldest ? stash.get(oldest) : null;
      if (h) h.innerHTML = '';
    }
  }
  return holder;
}

/** 渲染 effect 卸载/依赖变更前调用：把 el 的渲染产物子树整体摘进离屏缓存。 */
export function stashDocxRender(blob: Blob, el: HTMLElement): void {
  if (!el.firstElementChild) return;
  const holder = ensureHolder(blob);
  while (el.firstChild) holder.appendChild(el.firstChild);
}

/** 重开回放：把缓存子树搬回 el。命中返回 true（调用方随后 strip/重涂）。 */
export function takeDocxRender(blob: Blob, el: HTMLElement): boolean {
  const holder = stash.get(blob);
  if (!holder || !holder.firstElementChild) return false;
  while (holder.firstChild) el.appendChild(holder.firstChild);
  return true;
}

/** 超大文档防线阈值：>2.5MB 的 docx 默认文本预览（设计 2026-09-16 预览内存
 *  治理；2026-09-21 起审核面板同样适用，常量收敛到本模块单一来源）。 */
export const BIG_BLOB_BYTES = 2.5 * 1024 * 1024;
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd web && npx vitest run src/pages/c-chat/__tests__/docx-render-cache.test.ts`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add web/src/pages/c-chat/docx-render-cache.ts web/src/pages/c-chat/__tests__/docx-render-cache.test.ts
git commit -m "feat(review): docx 渲染产物缓存模块（WeakMap+LRU）——大文件重开毫秒级回放基建"
```

---

### Task 2: docx-highlight 新增 strip / rebuild

**Files:**
- Modify: `web/src/pages/c-chat/docx-highlight.ts`（文件末尾追加）
- Test: `web/src/pages/c-chat/__tests__/docx-highlight-strip-rebuild.test.ts`

- [ ] **Step 1: 写失败测试**

```ts
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
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd web && npx vitest run src/pages/c-chat/__tests__/docx-highlight-strip-rebuild.test.ts`
Expected: FAIL（两个函数不存在）

- [ ] **Step 3: 实现（追加到 docx-highlight.ts 末尾）**

```ts
// ── 渲染缓存回放配套（2026-09-21）────────────────────────────────

/** 剥离 highlightDocxRanges 插入的全部 mark[data-anchor-key]，把原文（文本节点）
 *  还原回段落里——渲染缓存回放的前置清理：缓存树里可能带上一轮批注的 mark，
 *  回放后须按当前 railItems 重涂，不能让旧 mark 残留/与新 mark 叠加。
 *  showKeyBadge 产生的 {{key}} 徽标 span 一并删除（仅删 textContent 精确等于
 *  {{本 mark key}} 的直接子 span，不动其他内容）。 */
export function stripDocxAnnotationMarks(container: HTMLElement): void {
  container.querySelectorAll('mark[data-anchor-key]').forEach((m) => {
    const mark = m as HTMLElement;
    const key = mark.dataset.anchorKey || '';
    mark.querySelectorAll(':scope > span').forEach((sp) => {
      if ((sp.textContent || '') === `{{${key}}}`) sp.remove();
    });
    mark.replaceWith(...mark.childNodes);
  });
}

/** 从已应用过 applyDocxHighlight 的渲染树上重建 key → span[] 映射：
 *  缓存回放的树里占位符已是带 data-ph-key 的高亮 span（{{key}} 原文已被
 *  replace 掉，无法再走 applyDocxHighlight 的全文扫描），span 的文本/样式
 *  可能是上一轮 values 的产物，重建映射后交给 updateDocxHighlight 按当前
 *  values/names 重涂即可。 */
export function rebuildPlaceholderSpans(
  container: HTMLElement,
): DocxPlaceholderSpans {
  const groups: DocxPlaceholderSpans = new Map();
  container.querySelectorAll<HTMLElement>('[data-ph-key]').forEach((span) => {
    const key = span.dataset.phKey || '';
    if (!key) return;
    const arr = groups.get(key) || [];
    arr.push(span);
    groups.set(key, arr);
  });
  return groups;
}
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd web && npx vitest run src/pages/c-chat/__tests__/docx-highlight-strip-rebuild.test.ts`
Expected: 4 passed

- [ ] **Step 5: 跑 docx-highlight 既有套件防回归**

Run: `cd web && npx vitest run src/pages/c-chat/docx-highlight.test.ts src/pages/c-chat/__tests__/docx-highlight-describe.test.ts src/pages/c-chat/__tests__/docx-highlight-direct.test.ts src/pages/c-chat/__tests__/docx-highlight-seq.test.ts src/pages/c-chat/docx-view-utils.test.ts`
Expected: 全部 passed（纯追加，不改既有行为）

- [ ] **Step 6: Commit**

```bash
git add web/src/pages/c-chat/docx-highlight.ts web/src/pages/c-chat/__tests__/docx-highlight-strip-rebuild.test.ts
git commit -m "feat(review): docx-highlight 新增 mark 剥离/占位符 span 重建（渲染缓存回放配套）"
```

---

### Task 3: 审核面板 oversize 门槛（方案 A）

**Files:**
- Modify: `web/src/pages/c-chat/review-panel.tsx`
- Test: `web/src/pages/c-chat/__tests__/review-panel-render-perf.test.tsx`

说明：**超大体量用派生判定**（`blob.size > 阈值` 直接算），不用范本预览既有的 state+effect 写法——state 写法在「blob 到达的那个 commit」里渲染 effect 先以 oversize=false 跑一次、把大文件白渲染一遍后才翻转分支，派生判定让守卫首帧即生效。`docxForceFidelity` 是真正的 UI 意图 state，保留。

- [ ] **Step 1: 写失败测试**

```tsx
// 审核面板大文件渲染优化（2026-09-21）组件级回归：
// A) >2.5MB docx 默认文本降级（不触发 renderAsync）+「切换保真渲染」显式覆盖；
// B) 渲染产物按 blob 缓存：卸载重开同一文件 appendChild 回放，renderAsync 只跑一次。
// mock 模式对齐 review-panel-version.test.tsx；renderAsync mock 产出可断言的最小
// docx 结构（含一个 section 供 applyDocxPageLazy 处理）。
import { useFileBlob, useReviewVersionBlob } from '@/hooks/use-file-blob';
import { useFileReviewState } from '@/hooks/use-file-review-request';
import ReviewPanel from '@/pages/c-chat/review-panel';
import request from '@/utils/next-request';
import { renderAsync } from 'docx-preview';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('@/hooks/use-file-review-request', () => ({
  useFileReviewState: vi.fn(),
}));
vi.mock('@/hooks/use-file-blob', () => ({
  useFileBlob: vi.fn(),
  useReviewVersionBlob: vi.fn(() => ({
    data: undefined,
    isLoading: false,
    error: null,
  })),
}));
vi.mock('@/utils/next-request', () => ({ default: { get: vi.fn() } }));
vi.mock('docx-preview', () => ({ renderAsync: vi.fn() }));

const mockUseFileReviewState = vi.mocked(useFileReviewState);
const mockUseFileBlob = vi.mocked(useFileBlob);
const mockGet = vi.mocked(request.get);
const mockRenderAsync = vi.mocked(renderAsync);

beforeEach(() => {
  vi.clearAllMocks();
  mockUseFileReviewState.mockReturnValue({ data: undefined } as any);
  mockUseFileBlob.mockReturnValue({
    data: undefined,
    isLoading: false,
    error: null,
  } as any);
  mockRenderAsync.mockImplementation(async (_b: Blob, el: HTMLElement) => {
    el.innerHTML = '<section><p><span>保真正文段落</span></p></section>';
  });
});

const paras = (texts: string[]) => ({
  filename: '投标文件.docx',
  file_type: 'docx',
  paragraphs: texts.map((text, index) => ({
    index,
    text,
    type: 'paragraph',
    page: 1,
  })),
});
const ok = (data: unknown) => Promise.resolve({ data: { code: 0, data } });

const renderPanel = () =>
  render(
    <ReviewPanel
      open
      onClose={vi.fn()}
      fileId="f1"
      fileName="投标文件.docx"
      annotations={[]}
    />,
  );

describe('ReviewPanel 大文件渲染优化（A：oversize 门槛）', () => {
  it('>2.5MB blob 不触发 renderAsync，文本降级视图 + 提示条', async () => {
    mockUseFileBlob.mockReturnValue({
      data: new Blob(['x'.repeat(3 * 1024 * 1024)]),
      isLoading: false,
      error: null,
    } as any);
    mockGet.mockResolvedValue(ok(paras(['降级正文段落'])));
    renderPanel();
    expect(
      await screen.findByText('文档较大，已用文本预览保障流畅'),
    ).toBeTruthy();
    expect(screen.getByText('降级正文段落')).toBeTruthy();
    expect(mockRenderAsync).not.toHaveBeenCalled();
  });

  it('点击「切换保真渲染」后走 renderAsync 保真视图', async () => {
    mockUseFileBlob.mockReturnValue({
      data: new Blob(['y'.repeat(3 * 1024 * 1024)]),
      isLoading: false,
      error: null,
    } as any);
    mockGet.mockResolvedValue(ok(paras(['降级正文段落'])));
    renderPanel();
    fireEvent.click(await screen.findByText('切换保真渲染'));
    await waitFor(() => expect(mockRenderAsync).toHaveBeenCalled());
    expect(await screen.findByText('保真正文段落')).toBeTruthy();
  });
});
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd web && npx vitest run src/pages/c-chat/__tests__/review-panel-render-perf.test.tsx`
Expected: FAIL（找不到「文档较大，已用文本预览保障流畅」，且 renderAsync 被调用）

- [ ] **Step 3: 实现 review-panel.tsx**

3a. 顶部 import 区追加（docx-highlight 的 import 组里加 `stripDocxAnnotationMarks`，暂未用到可留到 Task 4 一起加；本 Task 先加缓存模块阈值）：

```ts
import { BIG_BLOB_BYTES } from './docx-render-cache';
```

3b. `docxFidelity` 定义处（约 line 752）改为门槛参与判定，并在其后新增派生值与 state：

```ts
  // 超大文档防线（2026-09-21 补齐范本预览同款门槛）：>2.5MB 默认文本降级，
  // 「切换保真渲染」显式覆盖（本地 state 不落库）。派生判定而非 state+effect：
  // blob 到达的同一 commit 内守卫即生效，不会先白渲染一遍再翻转分支。
  const docxOversize = Boolean(docxBlob && docxBlob.size > BIG_BLOB_BYTES);
  const [docxForceFidelity, setDocxForceFidelity] = useState(false);
  // blob 换对象（切文件/版本）后覆盖选择失效，回到默认降级
  useEffect(() => {
    setDocxForceFidelity(false);
  }, [docxBlob]);
  const docxFidelity = Boolean(
    docxFidelityCandidate &&
      docxBlob &&
      !docxBlobError &&
      !docxRenderFailed &&
      (!docxOversize || docxForceFidelity),
  );
```

3c. 降级分支横幅（约 line 2080-2085，原「格式渲染失败」横幅处）追加 oversize 提示条（保留原横幅不动，新增兄弟条件块）：

```tsx
                  {docxFidelityCandidate &&
                    docxOversize &&
                    !docxForceFidelity &&
                    docxBlob &&
                    !docxBlobLoading && (
                      <div className="mx-auto mb-2 flex max-w-[794px] items-center gap-2 rounded bg-[#FFF7E8] px-3 py-2 text-xs text-[#FAAD14]">
                        <span>文档较大，已用文本预览保障流畅</span>
                        <button
                          className="ml-auto shrink-0 text-[#1a66fb] transition-colors hover:text-[#1557d6]"
                          onClick={() => setDocxForceFidelity(true)}
                        >
                          切换保真渲染
                        </button>
                      </div>
                    )}
```

3d. 渲染 effect 顶部加守卫（Task 4 会整体重写该 effect，此处先加一行保证门槛生效）：

```ts
    if (docxOversize && !docxForceFidelity) return;
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd web && npx vitest run src/pages/c-chat/__tests__/review-panel-render-perf.test.tsx`
Expected: 2 passed

- [ ] **Step 5: 跑审核面板既有套件防回归**

Run: `cd web && npx vitest run src/pages/c-chat/__tests__/review-panel-version.test.tsx`
Expected: passed

- [ ] **Step 6: Commit**

```bash
git add web/src/pages/c-chat/review-panel.tsx web/src/pages/c-chat/__tests__/review-panel-render-perf.test.tsx
git commit -m "feat(review): 审核面板 docx >2.5MB 文本降级门槛+显式切换保真（派生判定首帧生效）"
```

---

### Task 4: 审核面板接入渲染产物缓存（方案 B）

**Files:**
- Modify: `web/src/pages/c-chat/review-panel.tsx`
- Test: `web/src/pages/c-chat/__tests__/review-panel-render-perf.test.tsx`（追加）

- [ ] **Step 1: 追加失败测试**

在 `review-panel-render-perf.test.tsx` 追加 describe（文件顶部 import 区补 `stashDocxRender`/`takeDocxRender` 不需要——组件内部用；只追加用例）：

```tsx
describe('ReviewPanel 大文件渲染优化（B：渲染产物缓存）', () => {
  it('卸载重开同一文件走缓存回放，renderAsync 只调用一次', async () => {
    mockUseFileBlob.mockReturnValue({
      data: new Blob(['small-f1']),
      isLoading: false,
      error: null,
    } as any);
    mockGet.mockResolvedValue(ok(paras(['任意'])));
    const first = renderPanel();
    await screen.findByText('保真正文段落');
    expect(mockRenderAsync).toHaveBeenCalledTimes(1);
    first.unmount();
    renderPanel();
    // 缓存命中：appendChild 回放，不再整本重渲染
    await screen.findByText('保真正文段落');
    expect(mockRenderAsync).toHaveBeenCalledTimes(1);
  });
});
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd web && npx vitest run src/pages/c-chat/__tests__/review-panel-render-perf.test.tsx`
Expected: B 用例 FAIL（第二次挂载 renderAsync 被调用第 2 次）

- [ ] **Step 3: 重写渲染 effect（约 line 945-965）**

import 区补齐：

```ts
import {
  BIG_BLOB_BYTES,
  stashDocxRender,
  takeDocxRender,
} from './docx-render-cache';
```

docx-highlight 的 import 组加 `stripDocxAnnotationMarks`。渲染 effect 整体替换为：

```ts
  // blob 到达：清容器 → renderAsync 保真渲染 → 屏外页懒渲染 → 按当前 railItems
  // 插入 mark[data-anchor-key]。渲染失败降级回旧段落视图。
  // deps 必须含 docxEpoch：渲染产物不在 React state 里，而容器可能被三件事重挂——
  // 关闭时 return null（重开时同 fileId blob 命中 query 缓存引用不变、content 仍在
  // state，effect 其他 deps 全不变）、loading 闪断（内容请求 setLoading(true) 卸载
  // body，与渲染竞争）、文件切换。ref 回调把每次容器挂载折算成 epoch 递增，
  // 任何重挂都强制重跑渲染，否则容器空白（版本历史二次查看白屏根因）。
  // 2026-09-21 渲染缓存：effect 卸载/依赖变更时把产物子树摘进按 blob 键的离屏
  // 缓存（stashDocxRender），重开同一文件 takeDocxRender 命中 → appendChild 回放
  // （毫秒级）→ 剥上一轮 mark → 按当前 railItems 重涂，renderAsync 不再重跑。
  useEffect(() => {
    if (!docxFidelityCandidate || !docxBlob || !docxWrapRef.current) return;
    if (docxOversize && !docxForceFidelity) return;
    const el = docxWrapRef.current;
    setDocxRenderFailed(false);
    setMarkedKeys(new Set());
    if (takeDocxRender(docxBlob, el)) {
      stripDocxAnnotationMarks(el);
      fitDocxToColumn();
      applyDocxPageLazy(el);
      setMarkedKeys(
        highlightDocxRanges(el, toHighlightItems(railItemsRef.current)),
      );
      return;
    }
    el.innerHTML = '';
    renderAsync(docxBlob, el, undefined, { inWrapper: true, breakPages: true })
      .then(() => {
        if (!el.isConnected) return; // 容器已被重挂/卸载：丢弃本轮 stale 渲染产物
        fitDocxToColumn();
        applyDocxPageLazy(el);
        setMarkedKeys(
          highlightDocxRanges(el, toHighlightItems(railItemsRef.current)),
        );
      })
      .catch(() => {
        setDocxRenderFailed(true);
        setMarkedKeys(new Set());
      });
    return () => {
      // 卸载/换文件/进编辑视图前：产物子树整体摘进离屏缓存（树只存在一份，
      // 不在 el 就在 holder，内存不翻倍）
      stashDocxRender(docxBlob, el);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [docxBlob, docxFidelityCandidate, docxEpoch, docxOversize, docxForceFidelity]);
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd web && npx vitest run src/pages/c-chat/__tests__/review-panel-render-perf.test.tsx`
Expected: 3 passed

- [ ] **Step 5: 跑审核面板全部相关套件防回归**

Run: `cd web && npx vitest run src/pages/c-chat/__tests__/review-panel-version.test.tsx src/pages/c-chat/__tests__/review-panel-oversize.test.tsx --passWithNoTests 2>/dev/null; cd web && npx vitest run src/pages/c-chat/__tests__/review-panel-version.test.tsx`
Expected: passed（旧套件无回归）

- [ ] **Step 6: Commit**

```bash
git add web/src/pages/c-chat/review-panel.tsx web/src/pages/c-chat/__tests__/review-panel-render-perf.test.tsx
git commit -m "feat(review): 审核面板接入渲染产物缓存——弹框关开重放不再整本重渲染"
```

---

### Task 5: 范本实时预览接缓存 + oversize 改派生判定

**Files:**
- Modify: `web/src/pages/c-chat/template-fill-live-preview.tsx`
- Test: `web/src/pages/c-chat/__tests__/template-fill-live-preview-cache.test.tsx`

- [ ] **Step 1: 写失败测试**

```tsx
// 「查看填写内容」实时预览渲染缓存（2026-09-21）：
// ①docx 渲染产物按 blob 缓存，抽屉关开 appendChild 重放，renderAsync 只跑一次，
//   占位符 span 经 rebuildPlaceholderSpans 重建 + updateDocxHighlight 重涂；
// ②>2.5MB 改派生判定：超大门槛首帧即生效，不再「先白渲染一遍再翻转文本分支」。
import type { ITemplateFillTemplate } from '@/hooks/template-fill-stream';
import { useTemplateFillFile } from '@/hooks/use-template-fill-request';
import { renderAsync } from 'docx-preview';
import TemplateFillLivePreview from '@/pages/c-chat/template-fill-live-preview';
import { render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('docx-preview', () => ({ renderAsync: vi.fn() }));

const previewData = vi.hoisted(() => ({
  code: 0,
  data: {
    file_type: 'docx',
    items: [
      {
        index: 0,
        text: '招标人：{{tenderer_name}}',
        addr: 'A1',
        placeholder_key: 'tenderer_name',
      },
    ],
  },
}));

vi.mock('@/hooks/use-template-fill-request', () => ({
  useTemplateFillPreview: vi.fn(() => ({
    data: previewData,
    isLoading: false,
  })),
  useTemplateFillFile: vi.fn(() => ({
    data: undefined,
    isLoading: false,
    error: undefined,
  })),
  fetchTemplateFillTaskProgress: vi.fn(() => Promise.resolve(null)),
}));

const mockRenderAsync = vi.mocked(renderAsync);
const mockUseTemplateFillFile = vi.mocked(useTemplateFillFile);

beforeEach(() => {
  vi.clearAllMocks();
  mockRenderAsync.mockImplementation(async (_b: Blob, el: HTMLElement) => {
    el.innerHTML = '<section><p>招标人：{{tenderer_name}}</p></section>';
  });
  mockUseTemplateFillFile.mockReturnValue({
    data: new Blob(['docx-bytes-1']),
    isLoading: false,
    error: undefined,
  } as any);
});

const tpl = () =>
  ({
    template_id: 'tpl1',
    name: '范本',
    status: 'filled',
    task_id: 'task1',
    values: { tenderer_name: '石狮市交通建设公司' },
  }) as ITemplateFillTemplate;

const renderPreview = () =>
  render(<TemplateFillLivePreview tpl={tpl()} onClose={() => {}} />);

describe('TemplateFillLivePreview 渲染缓存', () => {
  it('关开重放：renderAsync 只调一次，占位符 span 重建后 values 照常显示', async () => {
    const first = renderPreview();
    expect(await screen.findByText('石狮市交通建设公司')).toBeTruthy();
    expect(mockRenderAsync).toHaveBeenCalledTimes(1);
    first.unmount();
    renderPreview();
    await waitFor(() =>
      expect(screen.getByText('石狮市交通建设公司')).toBeTruthy(),
    );
    expect(mockRenderAsync).toHaveBeenCalledTimes(1);
  });

  it('>2.5MB 派生判定首帧生效：不触发 renderAsync 直接文本降级', async () => {
    mockUseTemplateFillFile.mockReturnValue({
      data: new Blob(['x'.repeat(3 * 1024 * 1024)]),
      isLoading: false,
      error: undefined,
    } as any);
    renderPreview();
    expect(
      await screen.findByText('文档较大，已用文本预览保障流畅'),
    ).toBeTruthy();
    expect(screen.getByText(/招标人：/)).toBeTruthy();
    expect(mockRenderAsync).not.toHaveBeenCalled();
  });
});
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd web && npx vitest run src/pages/c-chat/__tests__/template-fill-live-preview-cache.test.tsx`
Expected: 用例1 FAIL（第二次挂载 renderAsync 第 2 次调用）；用例2 可能 PASS 或 FAIL（取决于旧 state 写法是否先渲染——旧行为存在一次多余 renderAsync，断言 not.toHaveBeenCalled 失败）

- [ ] **Step 3: 实现 template-fill-live-preview.tsx**

3a. import 区：`docx-highlight` 组加 `rebuildPlaceholderSpans`；新增：

```ts
import {
  BIG_BLOB_BYTES,
  stashDocxRender,
  takeDocxRender,
} from '@/pages/c-chat/docx-render-cache';
```

删除本地 `const BIG_BLOB_BYTES = 2.5 * 1024 * 1024;`（约 line 56-57，注释一并上移到共享模块）。

3b. `blobOversize` 从 state 改派生：删除 `const [blobOversize, setBlobOversize] = useState(false);`（约 line 193）与其赋值 effect（约 line 212-215），在 `useTemplateFillFile` 调用（约 line 209）之后改为：

```ts
  // 超大文档防线（派生判定而非 state+effect）：blob 到达的同一 commit 内守卫即
  // 生效——旧写法会在同一 commit 先以 oversize=false 白渲染一遍大文件再翻转分支
  const blobOversize = Boolean(fileBlob && fileBlob.size > BIG_BLOB_BYTES);
```

范本切换 effect（约 line 248-254）里删除 `setBlobOversize(false);`（其余保留）。

3c. 渲染 effect（约 line 219-238）整体替换：

```ts
  // blob 到达：清容器 → renderAsync → 屏外页懒渲染 → 建占位符 span 映射。
  // 超大文档且未显式切换保真 → 跳过整本 DOM 建树（文本分支接管）。
  // 2026-09-21 渲染缓存：卸载/依赖变更时产物子树摘进按 blob 键的离屏缓存，
  // 重开 takeDocxRender 命中 → appendChild 回放（毫秒级）。回放的树里占位符
  // span 已带上一轮 values 文本（{{key}} 原文已被 replace），重建映射后交给
  // 下方 updateDocxHighlight effect 按当前 values/names 重涂。
  useEffect(() => {
    if (!docxEnabled || !fileBlob || !containerRef.current) return;
    if (blobOversize && !forceFidelity) return;
    const el = containerRef.current;
    setRenderFailed(false);
    setRenderedOk(false);
    placeholderSpansRef.current = new Map();
    if (takeDocxRender(fileBlob, el)) {
      applyDocxPageLazy(el);
      placeholderSpansRef.current = rebuildPlaceholderSpans(el);
      setRenderedOk(true);
      return;
    }
    el.innerHTML = '';
    renderAsync(fileBlob, el, undefined, { inWrapper: true, breakPages: true })
      .then(() => {
        applyDocxPageLazy(el);
        placeholderSpansRef.current = applyDocxHighlight(
          el,
          valuesRef.current,
          namesRef.current,
        );
        setRenderedOk(true);
      })
      .catch(() => setRenderFailed(true));
    return () => {
      stashDocxRender(fileBlob, el);
    };
  }, [fileBlob, docxEnabled, blobOversize, forceFidelity]);
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd web && npx vitest run src/pages/c-chat/__tests__/template-fill-live-preview-cache.test.tsx src/pages/c-chat/__tests__/template-fill-live-preview.test.tsx src/pages/c-chat/__tests__/template-fill-confirm-card.test.tsx`
Expected: 全部 passed（含既有 xlsx 分支套件无回归）

- [ ] **Step 5: Commit**

```bash
git add web/src/pages/c-chat/template-fill-live-preview.tsx web/src/pages/c-chat/__tests__/template-fill-live-preview-cache.test.tsx
git commit -m "feat(review): 范本实时预览接入渲染缓存+超大门槛改派生判定（首帧生效）"
```

---

### Task 6: 全量验证 + 文档登记

**Files:**
- Modify: `D:\AI\ragflow2\CHANGE.md`（增量追加条目）
- Modify: `D:\AI\ragflow2\CLAUDE.md`（参考表登记本计划 + CHANGE 索引行）

- [ ] **Step 1: 前端全量 vitest**

Run: `cd web && npx vitest run`
Expected: 本计划 4 个新套件全绿；既有套件无新增失败（仓库存在与本功能无关的既有失败套件——测试腐坏，验收时以 git stash 前后对比为准，只关注新增失败）

- [ ] **Step 2: 生产构建**

Run: `cd web && npm run build`
Expected: 构建成功无 TS 错误

- [ ] **Step 3: CHANGE.md 追加条目**（插在最新条目位置，格式对齐既有条目）

要点：日期 2026-09-21（十二）；主题「审核/范本预览大文件渲染优化——渲染产物缓存+体量门槛」；核心变更（A/B 两方案、派生判定根除首帧白渲染、WeakMap+LRU 内存不翻倍）；**未部署、未 commit 状态在收尾时更新为实际状态**（部署 = build+dist+nginx reload）。

- [ ] **Step 4: CLAUDE.md 参考表登记**

在「### 参考文档」表顶部加一行（绝对路径 + 一句话简介），并把 CHANGE 索引行的最新要点更新：

```markdown
| 大文件渲染优化 | `D:\AI\ragflow2\docs\superpowers\plans\2026-09-21-docx-render-perf.md` | ★ 审核面板/范本预览大 docx 打开卡死治理：>2.5MB 文本降级门槛（审核面板补齐+派生判定首帧生效）+ 渲染产物 WeakMap+LRU 缓存重开毫秒级回放（strip/rebuild 配套重涂） |
```

- [ ] **Step 5: Commit**

```bash
git add CHANGE.md CLAUDE.md docs/superpowers/plans/2026-09-21-docx-render-perf.md
git commit -m "docs: 2026-09-21（十二）审核/范本预览大文件渲染优化——渲染产物缓存+体量门槛"
```

---

## 自查记录（Self-Review）

1. **Spec 覆盖**：A（审核面板门槛）= Task 3；B（渲染缓存）= Task 1/2/4/5；范本预览首帧白渲染浪费 = Task 5 派生判定；文档登记 = Task 6。无缺口。
2. **占位符扫描**：所有代码步骤均含完整代码，无 TBD/「类似 Task N」。
3. **类型/命名一致性**：`stashDocxRender(blob, el)` / `takeDocxRender(blob, el): boolean` / `BIG_BLOB_BYTES` / `stripDocxAnnotationMarks(container)` / `rebuildPlaceholderSpans(container): DocxPlaceholderSpans` 各任务签名一致；`DocxPlaceholderSpans` 沿用 docx-highlight.ts:45 既有导出。
4. **已知风险与对策**：
   - LRU 淘汰只清 holder 内容、WeakMap 条目留存 → `takeDocxRender` 有 `firstElementChild` 判空，等效失效，已测。
   - 缓存树含上一轮 mark/产值 span → strip/rebuild 强制重涂，已测。
   - renderAsync 异步未决时卸载 → cleanup stash 空容器是 no-op（`firstElementChild` 守卫），`.then` 里 `el.isConnected` 检查防 stale 高亮，行为与现状一致。
   - B端范本预览（fidelity-preview）共用 `highlightDocxRanges` 但**不**接缓存——本计划零改动，`stripDocxAnnotationMarks` 是新增导出，对既有调用方无影响。
