// 审核面板大文件渲染优化（2026-09-21）组件级回归：
// A) >2.5MB docx 默认文本降级（不触发 renderAsync）+「切换保真渲染」显式覆盖；
// B) 渲染产物按 blob 缓存：卸载重开同一文件 appendChild 回放，renderAsync 只跑一次。
// mock 模式对齐 review-panel-version.test.tsx；renderAsync mock 产出可断言的最小
// docx 结构（含一个 section 供 applyDocxPageLazy 处理）。
import { useFileBlob } from '@/hooks/use-file-blob';
import {
  useFileReviewState,
  useRevertAnnotation,
  useUpdateAnnotationStatus,
} from '@/hooks/use-file-review-request';
import ReviewPanel from '@/pages/c-chat/review-panel';
import request from '@/utils/next-request';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { renderAsync } from 'docx-preview';
import { beforeEach, describe, expect, it, vi } from 'vitest';

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

const mockUseFileReviewState = vi.mocked(useFileReviewState);
const mockUseFileBlob = vi.mocked(useFileBlob);
const mockGet = vi.mocked(request.get);
const mockRenderAsync = vi.mocked(renderAsync);

beforeEach(() => {
  vi.clearAllMocks();
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
});
