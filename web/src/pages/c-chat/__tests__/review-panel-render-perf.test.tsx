// 审核面板大文件渲染优化（2026-09-21）组件级回归：
// A) >2.5MB docx 默认文本降级（不触发 renderAsync）+「切换保真渲染」显式覆盖；
// B) 渲染产物按 blob 缓存：卸载重开同一文件 appendChild 回放，renderAsync 只跑一次；
//    缓存回放在 effect 阶段同步插 mark，补插 effect 同一 commit 的 markedKeys 闭包
//    还是旧值，靠 DOM 已插判定防同名 mark 嵌套双层——用 highlightDocxRanges spy 在
//    调用瞬间检测重插（最终 mark count 会被 epoch 双跑的重渲抹平，count 断言无区分力）。
// mock 模式对齐 review-panel-version.test.tsx；renderAsync mock 产出可断言的最小
// docx 结构（含一个 section 供 applyDocxPageLazy 处理）。
import { useFileBlob } from '@/hooks/use-file-blob';
import {
  useFileReviewState,
  useRevertAnnotation,
  useUpdateAnnotationStatus,
} from '@/hooks/use-file-review-request';
import type { DocxHighlightItem } from '@/pages/c-chat/docx-highlight';
import { takeDocxRender } from '@/pages/c-chat/docx-render-cache';
import ReviewPanel, { type Annotation } from '@/pages/c-chat/review-panel';
import request from '@/utils/next-request';
import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react';
import { renderAsync } from 'docx-preview';
import { beforeEach, describe, expect, it, vi } from 'vitest';

// highlightDocxRanges spy 的重插违规记录（vi.mock 工厂先于模块体执行，须 hoisted）
const highlightSpyState = vi.hoisted(() => ({ duplicates: [] as string[] }));

vi.mock('@/hooks/use-file-review-request', () => ({
  useFileReviewState: vi.fn(),
  useUpdateAnnotationStatus: vi.fn(),
  useFixFileReview: vi.fn(),
  useRevertAnnotation: vi.fn(),
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
// partial mock：highlightDocxRanges 包装真实现，调用瞬间检测「容器已有同名
// mark 仍要求插入」的双重插违规
vi.mock('@/pages/c-chat/docx-highlight', async (importOriginal) => {
  const actual =
    await importOriginal<typeof import('@/pages/c-chat/docx-highlight')>();
  const real = actual.highlightDocxRanges;
  return {
    ...actual,
    highlightDocxRanges: vi.fn(
      (
        container: HTMLElement,
        items: DocxHighlightItem[],
        opts?: { showKeyBadge?: boolean },
      ) => {
        const existing = new Set(
          Array.from(container.querySelectorAll('mark[data-anchor-key]'), (m) =>
            m.getAttribute('data-anchor-key'),
          ),
        );
        for (const it of items) {
          if (existing.has(it.key)) highlightSpyState.duplicates.push(it.key);
        }
        return real(container, items, opts);
      },
    ),
  };
});

const mockUseFileReviewState = vi.mocked(useFileReviewState);
const mockUseFileBlob = vi.mocked(useFileBlob);
const mockGet = vi.mocked(request.get);
const mockRenderAsync = vi.mocked(renderAsync);

beforeEach(() => {
  vi.clearAllMocks();
  highlightSpyState.duplicates.length = 0;
  vi.mocked(useUpdateAnnotationStatus).mockReturnValue({
    mutate: vi.fn(),
    mutateAsync: vi.fn(),
    isPending: false,
  } as any);
  vi.mocked(useRevertAnnotation).mockReturnValue({
    mutate: vi.fn(),
    mutateAsync: vi.fn(),
    isPending: false,
  } as any);
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

// 与 review-panel-version.test.tsx 同构：一条能锚定正文的 AI 标注
const ann = (over: Partial<Annotation> = {}): Annotation =>
  ({
    id: 'a1',
    matched_text: '旧文案',
    type: 'format_error',
    severity: 'high',
    issue: '表述问题',
    suggestion: '改为新文案',
    status: 'open',
    ...over,
  }) as Annotation;

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

  it('带 1 条标注重开后 mark 数量不变（回放不与补插 effect 双重插 mark）', async () => {
    // 渲染产物文本须含批注 matched_text，highlightDocxRanges 才能锚定插 mark
    mockRenderAsync.mockImplementation(async (_b: Blob, el: HTMLElement) => {
      el.innerHTML = '<section><p><span>正文含旧文案一段</span></p></section>';
    });
    mockUseFileBlob.mockReturnValue({
      data: new Blob(['small-ann-f1']),
      isLoading: false,
      error: null,
    } as any);
    // 重开后的 content 请求延迟到达：让「content 到达（railItems/docxFidelity
    // 翻转）」与「缓存回放」落在同一 commit——回放分支同步插 mark 时补插
    // effect 闭包 markedKeys 还是旧空集，无 DOM 已插判定会把该 key 再插一层
    let fetchCount = 0;
    mockGet.mockImplementation(async () => {
      fetchCount += 1;
      if (fetchCount > 1) await new Promise((r) => setTimeout(r, 50));
      return ok(paras(['正文含旧文案一段']));
    });
    const ui = (
      <ReviewPanel
        open
        onClose={vi.fn()}
        fileId="f1"
        fileName="投标文件.docx"
        annotations={[ann()]}
      />
    );
    const first = render(ui);
    await waitFor(() =>
      expect(document.querySelectorAll('mark[data-anchor-key]').length).toBe(1),
    );
    first.unmount();
    render(ui);
    await waitFor(() =>
      expect(document.querySelectorAll('mark[data-anchor-key]').length).toBe(1),
    );
    // 等待期结束再核一遍：延迟到达的补插轮不得让 mark 翻倍
    await act(async () => {
      await new Promise((r) => setTimeout(r, 150));
    });
    expect(document.querySelectorAll('mark[data-anchor-key]').length).toBe(1);
    // 关键断言：任何一次 highlightDocxRanges 都不得要求插入容器里已存在的
    // 同名 mark（重插即嵌套双层）。最终 count 会被 epoch 双跑的重渲抹平，
    // 只有调用瞬间的检测能稳定抓住双重插
    expect(highlightSpyState.duplicates).toEqual([]);
  });

  it('渲染在飞时切走：不把在飞容器内容 stash 进缓存（防错树持久化）', async () => {
    const blobA = new Blob(['inflight-a']);
    const blobB = new Blob(['inflight-b']);
    let blob = blobA;
    mockUseFileBlob.mockImplementation(
      () => ({ data: blob, isLoading: false, error: null }) as any,
    );
    mockGet.mockResolvedValue(ok(paras(['任意'])));
    let resolveA!: () => void;
    // blob A 的渲染挂起（在飞）；其余 blob 正常同步完成
    mockRenderAsync.mockImplementation(async (b: Blob, el: HTMLElement) => {
      el.innerHTML = '<section><p><span>保真正文段落</span></p></section>';
      if (b === blobA) await new Promise<void>((r) => (resolveA = r));
    });
    const view = render(
      <ReviewPanel
        open
        onClose={vi.fn()}
        fileId="f1"
        fileName="投标文件.docx"
        annotations={[]}
      />,
    );
    // 等 A 的 renderAsync 进入在飞（cleanup 尚未发生 → settled=false）
    await waitFor(() => expect(resolveA).toBeDefined());
    // A 渲染中切到 B：A 的 cleanup 跑，settled=false → 不得 stash（此时容器
    // 内容马上会被 B 清空重写，stash 进去就是「打开 A 回放出 B/半成品树」的
    // 跨会话错树污染）
    blob = blobB;
    view.rerender(
      <ReviewPanel
        open
        onClose={vi.fn()}
        fileId="f1"
        fileName="投标文件.docx"
        annotations={[]}
      />,
    );
    await screen.findByText('保真正文段落');
    // A 未入缓存
    const probeA = document.createElement('div');
    expect(takeDocxRender(blobA, probeA)).toBe(false);
    // B 正常完成，卸载后入缓存
    view.unmount();
    const probeB = document.createElement('div');
    expect(takeDocxRender(blobB, probeB)).toBe(true);
    // 收尾：放行挂起的 A 渲染，避免悬挂 promise
    resolveA();
    await act(async () => {
      await new Promise((r) => setTimeout(r, 0));
    });
  });
});
