// 文件审核进度卡：c-chat 对话与 flow AI 对话区共用
// （T11 交接契约：必须复用 useFileReviewState 拿数据，不自写轮询）。
import {
  useFileReviewState,
  useFixFileReview,
  useUpdateAnnotationStatus,
} from '@/hooks/use-file-review-request';
import FileReviewProgress from '@/pages/c-chat/file-review-progress';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

jest.mock('@/hooks/use-file-review-request', () => ({
  useFileReviewState: jest.fn(),
  useFixFileReview: jest.fn(),
  useUpdateAnnotationStatus: jest.fn(),
}));

const mockUseFileReviewState = useFileReviewState as jest.MockedFunction<
  typeof useFileReviewState
>;
const mockUseFixFileReview = useFixFileReview as jest.MockedFunction<
  typeof useFixFileReview
>;
const mockUseUpdateAnnotationStatus =
  useUpdateAnnotationStatus as jest.MockedFunction<
    typeof useUpdateAnnotationStatus
  >;

const baseState = (over: any = {}) => ({
  data: {
    code: 0,
    data: {
      file_id: 'f1',
      task_id: 't1',
      rounds: [
        {
          id: 'r1',
          round_no: 1,
          status: 'annotated',
          file_version: 'v1',
          template_id: 'bid_doc_format',
          user_query: '',
          summary: 'high:1 medium:0 low:0',
          error: '',
          minio_path: 'frv-t1-v2.docx',
          produced: true,
        },
      ],
      current: {
        id: 'r1',
        round_no: 1,
        status: 'annotated',
        file_version: 'v1',
        template_id: 'bid_doc_format',
        user_query: '',
        summary: '',
        error: '',
        minio_path: 'frv-t1-v2.docx',
        produced: true,
      },
      doc: { object: 'frv-t1-v2.docx', version: 'v2' },
      annotations: [
        {
          id: 'a1',
          round_id: 'r1',
          task_id: 't1',
          file_id: 'f1',
          file_version: 'v1',
          type: 'format',
          severity: 'high',
          issue: '正文未签字',
          suggestion: '',
          matched_text: '',
          source: 'ai',
          status: 'open',
          prev_annotation_id: '',
          anchor: {},
        },
      ],
      annotation_counts: {
        total: 1,
        high: 1,
        medium: 0,
        low: 0,
        pending: 1,
        fixed: 0,
      },
      fix_rounds_left: 3,
      max_fix_rounds: 3,
    },
  },
  isLoading: false,
  isError: false,
  refetch: jest.fn(),
  ...over,
});

describe('FileReviewProgress', () => {
  beforeEach(() => {
    mockUseFixFileReview.mockReturnValue({
      mutate: jest.fn(),
      // TanStack Query v5 mutation: busy 状态用 isPending（v4 的 isLoading 已重命名）。
      // 同时保留 isLoading:false 兜底未升级 mock 的类型。
      isPending: false,
      isLoading: false,
    } as any);
    mockUseUpdateAnnotationStatus.mockReturnValue({
      mutate: jest.fn(),
      isPending: false,
      isLoading: false,
    } as any);
  });

  it('renders round summary + 「打开审核面板」callback with annotations + doc.version', async () => {
    mockUseFileReviewState.mockReturnValue(baseState() as any);
    const onOpenReview = jest.fn();
    render(<FileReviewProgress fileId="f1" onOpenReview={onOpenReview} />);
    expect(screen.getByText(/第 1 轮/)).toBeInTheDocument();
    expect(screen.getByText(/high:1 medium:0 low:0/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /打开审核面板/ }));
    await waitFor(() => expect(onOpenReview).toHaveBeenCalledTimes(1));
    const [annotations, version] = onOpenReview.mock.calls[0];
    expect(version).toBe('v2');
    expect(annotations[0].id).toBe('a1');
  });

  it('canFix=true 时显示「选择级别修复」入口，fix_rounds_left=0 时隐藏', () => {
    mockUseFileReviewState.mockReturnValue(baseState() as any);
    const { rerender } = render(<FileReviewProgress fileId="f1" />);
    expect(
      screen.getByRole('button', { name: '选择级别修复' }),
    ).toBeInTheDocument();
    mockUseFileReviewState.mockReturnValue(
      baseState({
        data: {
          ...baseState().data,
          data: { ...baseState().data.data, fix_rounds_left: 0 },
        },
      }) as any,
    );
    rerender(<FileReviewProgress fileId="f1" />);
    expect(screen.queryByRole('button', { name: '选择级别修复' })).toBeNull();
  });

  it('回归：修复轮终态 done + fix_rounds_left>0 时入口仍显示（第 2 轮起按钮不得消失）', () => {
    // 曾经 canFix 写成 `status === 'annotated'`，而修复轮的终态是 'done'
    // （executor._run_fix_round 每条收口路径都写 done）→ 第 2 轮起按钮永久消失，
    // 「最多 3 轮修复」在 UI 侧实际只能触发 1 轮。
    mockUseFileReviewState.mockReturnValue(
      baseState({
        data: {
          ...baseState().data,
          data: {
            ...baseState().data.data,
            fix_rounds_left: 2,
            current: { ...baseState().data.data.current, status: 'done' },
            rounds: [
              { ...baseState().data.data.current, round_no: 2, status: 'done' },
            ],
          },
        },
      }) as any,
    );
    render(<FileReviewProgress fileId="f1" />);
    expect(
      screen.getByRole('button', { name: '选择级别修复' }),
    ).toBeInTheDocument();
    expect(screen.getByText(/剩余.*2.*轮/)).toBeInTheDocument();
  });

  it('round.status=reviewing 时不显示「修复」入口，显示 spinner', () => {
    mockUseFileReviewState.mockReturnValue(
      baseState({
        data: {
          ...baseState().data,
          data: {
            ...baseState().data.data,
            current: {
              ...baseState().data.data.current,
              status: 'reviewing',
            },
          },
        },
      }) as any,
    );
    render(<FileReviewProgress fileId="f1" />);
    expect(screen.queryByRole('button', { name: '选择级别修复' })).toBeNull();
    expect(screen.getByText(/正在审核|审核中/)).toBeInTheDocument();
  });

  it('点击「选择级别修复」调起 Popover，勾选 high + medium 后提交触发 useFixFileReview.mutate', async () => {
    const mutate = jest.fn();
    mockUseFixFileReview.mockReturnValue({
      mutate,
      isPending: false,
      isLoading: false,
    } as any);
    mockUseFileReviewState.mockReturnValue(baseState() as any);
    render(<FileReviewProgress fileId="f1" />);
    fireEvent.click(screen.getByRole('button', { name: '选择级别修复' }));
    fireEvent.click(screen.getByLabelText(/严重/));
    fireEvent.click(screen.getByLabelText(/一般/));
    fireEvent.click(screen.getByRole('button', { name: /确认|提交/ }));
    await waitFor(() => expect(mutate).toHaveBeenCalledTimes(1));
    expect(mutate.mock.calls[0][0]).toMatchObject({
      levels: ['high', 'medium'],
    });
  });

  it('hook isError 时显示错误降级文案（不暴露服务端文案）', () => {
    mockUseFileReviewState.mockReturnValue({
      ...baseState(),
      isError: true,
      error: new Error('MySQL 10.0.0.5:3306'),
    } as any);
    render(<FileReviewProgress fileId="f1" />);
    expect(screen.getByText(/加载失败|稍后重试/)).toBeInTheDocument();
    expect(screen.queryByText(/3306/)).toBeNull();
  });

  it('fix_rounds_left 用 max_fix_rounds - 已发起轮次数派生；禁止用 rounds.length < 3', () => {
    // 2 轮 round 1=annotated, round 2=failed → fix_rounds_left 来自服务端（不应按 rounds.length 派生）
    const state2 = baseState({
      data: {
        ...baseState().data,
        data: {
          ...baseState().data.data,
          fix_rounds_left: 1,
          rounds: [
            { ...baseState().data.data.current, status: 'annotated' },
            {
              ...baseState().data.data.current,
              round_no: 2,
              status: 'failed',
            },
          ],
        },
      },
    });
    mockUseFileReviewState.mockReturnValue(state2 as any);
    render(<FileReviewProgress fileId="f1" />);
    expect(screen.getByText(/剩余.*1.*轮/)).toBeInTheDocument();
  });
});
