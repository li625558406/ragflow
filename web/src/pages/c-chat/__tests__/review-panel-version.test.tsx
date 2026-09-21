// 审核弹框三项根修的组件级回归：
// ① 确认保留后 props 旧状态 + state 新状态 → 弹框内徽标即时翻转（overlay 合并）；
// ② state.doc.has_result 时正文改拉版本 content（成稿本就含修复）并显示来源徽标；
// ③ 版本 content 失败 → 降级回原文件 content，来源徽标不出现（contentIsVersion 复位）。
import {
  useFileReviewState,
  useRevertAnnotation,
  useUpdateAnnotationStatus,
} from '@/hooks/use-file-review-request';
import ReviewPanel, { type Annotation } from '@/pages/c-chat/review-panel';
import request from '@/utils/next-request';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('@/hooks/use-file-review-request', () => ({
  useFileReviewState: vi.fn(),
  useUpdateAnnotationStatus: vi.fn(),
  useFixFileReview: vi.fn(),
  useRevertAnnotation: vi.fn(),
}));
vi.mock('@/hooks/use-file-blob', () => ({
  useFileBlob: vi.fn(() => ({
    data: undefined,
    isLoading: false,
    error: null,
  })),
  useReviewVersionBlob: vi.fn(() => ({
    data: undefined,
    isLoading: false,
    error: null,
  })),
}));
vi.mock('@/utils/next-request', () => ({ default: { get: vi.fn() } }));

const mockUseFileReviewState = vi.mocked(useFileReviewState);
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
const mockGet = vi.mocked(request.get);

beforeEach(() => {
  vi.clearAllMocks();
  mockUseFileReviewState.mockReturnValue({ data: undefined } as any);
});

const stateResp = (inner: Record<string, unknown>) => ({
  data: {
    code: 0,
    data: {
      file_id: 'f1',
      task_id: 't1',
      doc: { has_result: false, version: '' },
      annotations: [],
      rounds: [],
      current: null,
      ...inner,
    },
  },
});

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

const renderPanel = (annotations: Annotation[]) =>
  render(
    <ReviewPanel
      open
      onClose={vi.fn()}
      fileId="f1"
      fileName="投标文件.docx"
      annotations={annotations}
    />,
  );

describe('ReviewPanel 状态联动（问题①）', () => {
  it('props 旧 open + state 新 resolved+patch → 弹框内出现「已确认」徽标', async () => {
    mockUseFileReviewState.mockReturnValue(
      stateResp({
        annotations: [
          {
            id: 'a1',
            status: 'resolved',
            patch: { find: '旧文案', replace: '新文案' },
          },
        ],
      }) as any,
    );
    mockGet.mockResolvedValue(ok(paras(['正文含旧文案一段'])));
    renderPanel([ann()]);
    const badges = await screen.findAllByText('已确认');
    expect(badges.length).toBeGreaterThanOrEqual(1);
  });

  it('state 无该 id / 空列表 → 不误翻徽标（保持 open 无已确认）', async () => {
    mockUseFileReviewState.mockReturnValue(
      stateResp({ annotations: [] }) as any,
    );
    mockGet.mockResolvedValue(ok(paras(['这是原始正文段落'])));
    renderPanel([ann()]);
    await waitFor(() =>
      expect(screen.getByText(/这是原始正文段落/)).toBeInTheDocument(),
    );
    expect(screen.queryByText('已确认')).toBeNull();
  });
});

describe('ReviewPanel 原文 ⇄ AI 修改自由切换（（八））', () => {
  const patch = { find: '旧文案', replace: '新文案' };

  it('fixed → 边栏卡显示 回退+确认保留 双按钮', async () => {
    mockUseFileReviewState.mockReturnValue(
      stateResp({ annotations: [{ id: 'a1', status: 'fixed', patch }] }) as any,
    );
    // 正文需含 matched_text 才能进右侧边栏卡（否则落入底部兜底区，无展开箭头）
    mockGet.mockResolvedValue(ok(paras(['正文含旧文案一段'])));
    renderPanel([ann({ status: 'fixed', patch })]);
    // 边栏批注卡默认折叠，FixActions 在展开区内——先点箭头展开
    fireEvent.click(await screen.findByTitle('展开'));
    expect((await screen.findAllByText('回退')).length).toBeGreaterThanOrEqual(
      1,
    );
    expect(
      (await screen.findAllByText('确认保留')).length,
    ).toBeGreaterThanOrEqual(1);
  });

  it('open+patch（已回退）→ 「已回退」徽标 + 「恢复 AI 修改」按钮，无回退/确认保留', async () => {
    mockUseFileReviewState.mockReturnValue(
      stateResp({ annotations: [{ id: 'a1', status: 'open', patch }] }) as any,
    );
    mockGet.mockResolvedValue(ok(paras(['正文含旧文案一段'])));
    renderPanel([ann({ status: 'open', patch })]);
    fireEvent.click(await screen.findByTitle('展开'));
    expect(
      (await screen.findAllByText('已回退')).length,
    ).toBeGreaterThanOrEqual(1);
    expect(
      (await screen.findAllByText('恢复 AI 修改')).length,
    ).toBeGreaterThanOrEqual(1);
    expect(screen.queryByText('确认保留')).toBeNull();
  });

  it('resolved → 回退按钮仍在（确认保留后仍可回到原文），无恢复按钮', async () => {
    mockUseFileReviewState.mockReturnValue(
      stateResp({
        annotations: [{ id: 'a1', status: 'resolved', patch }],
      }) as any,
    );
    mockGet.mockResolvedValue(ok(paras(['正文含旧文案一段'])));
    renderPanel([ann({ status: 'resolved', patch })]);
    fireEvent.click(await screen.findByTitle('展开'));
    expect((await screen.findAllByText('回退')).length).toBeGreaterThanOrEqual(
      1,
    );
    expect(screen.queryByText('恢复 AI 修改')).toBeNull();
  });

  it('open 无 patch（从未修过）→ 不渲染任何切换按钮', async () => {
    mockUseFileReviewState.mockReturnValue(
      stateResp({ annotations: [] }) as any,
    );
    mockGet.mockResolvedValue(ok(paras(['这是原始正文段落'])));
    renderPanel([ann()]);
    await waitFor(() =>
      expect(screen.getByText(/这是原始正文段落/)).toBeInTheDocument(),
    );
    expect(screen.queryByText('回退')).toBeNull();
    expect(screen.queryByText('恢复 AI 修改')).toBeNull();
    expect(screen.queryByText('确认保留')).toBeNull();
  });
});

describe('ReviewPanel 成稿版本展示（问题③）', () => {
  it('doc.has_result → 优先拉版本 content，渲染修复后文案+来源徽标，不拉原文件', async () => {
    mockUseFileReviewState.mockReturnValue(
      stateResp({ doc: { has_result: true, version: 'v2' } }) as any,
    );
    mockGet.mockImplementation((url: string) => {
      if (url.includes('/file/review/') && url.endsWith('/content')) {
        return ok({
          file_type: 'docx',
          file_version: 'v2',
          paragraphs: [
            { index: 0, text: '修复后的正文段落', type: 'paragraph', page: 1 },
          ],
        });
      }
      return ok(paras(['这是原始正文段落']));
    });
    renderPanel([ann()]);
    expect(await screen.findByText('修复后的正文段落')).toBeInTheDocument();
    expect(screen.queryByText('这是原始正文段落')).toBeNull();
    expect(screen.getByText('修复后成稿 v2')).toBeInTheDocument();
    const versionCalls = mockGet.mock.calls.filter(
      ([u]) =>
        String(u).includes('/file/review/') && String(u).endsWith('/content'),
    );
    expect(versionCalls).toHaveLength(1);
    expect(
      mockGet.mock.calls.some(([u]) => String(u).includes('/files/f1/content')),
    ).toBe(false);
  });

  it('版本 content 失败 → 降级回原文件，来源徽标不出现', async () => {
    mockUseFileReviewState.mockReturnValue(
      stateResp({ doc: { has_result: true, version: 'v2' } }) as any,
    );
    mockGet.mockImplementation((url: string) => {
      if (url.includes('/file/review/') && url.endsWith('/content')) {
        return Promise.reject(new Error('object lost'));
      }
      return ok(paras(['这是原始正文段落']));
    });
    renderPanel([ann()]);
    expect(await screen.findByText(/这是原始正文段落/)).toBeInTheDocument();
    expect(screen.queryByText(/修复后成稿/)).toBeNull();
  });

  it('无成稿（doc.has_result=false）→ 行为不变，只拉原文件', async () => {
    mockGet.mockResolvedValue(ok(paras(['这是原始正文段落'])));
    renderPanel([ann()]);
    expect(await screen.findByText(/这是原始正文段落/)).toBeInTheDocument();
    expect(
      mockGet.mock.calls.some(
        ([u]) =>
          String(u).includes('/file/review/') && String(u).endsWith('/content'),
      ),
    ).toBe(false);
  });
});
