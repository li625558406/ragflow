// 文件审核进度卡：c-chat 对话与 flow AI 对话区共用
// （T11 交接契约：必须复用 useFileReviewState 拿数据，不自写轮询）。
import {
  useFileReviewState,
  useFixFileReview,
  useUpdateAnnotationStatus,
} from '@/hooks/use-file-review-request';
import FileReviewProgress from '@/pages/c-chat/file-review-progress';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('@/hooks/use-file-review-request', () => ({
  useFileReviewState: vi.fn(),
  useFixFileReview: vi.fn(),
  useUpdateAnnotationStatus: vi.fn(),
}));

const mockUseFileReviewState = vi.mocked(useFileReviewState);
const mockUseFixFileReview = vi.mocked(useFixFileReview);
const mockUseUpdateAnnotationStatus = vi.mocked(useUpdateAnnotationStatus);

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
          stale: false,
        },
      ],
      // current 是服务端对 rounds[-1] 的同一份 payload 投影（review_state 里
      // `current = _round_payload(rounds[-1])`），故 summary 必须与 rounds[0] 一致 ——
      // 组件渲染的是 current.summary，两者不一致的 fixture 会造出「线上不可能出现」的态。
      current: {
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
        stale: false,
      },
      doc: { has_result: true, version: 'v2' },
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
  refetch: vi.fn(),
  ...over,
});

describe('FileReviewProgress', () => {
  beforeEach(() => {
    mockUseFixFileReview.mockReturnValue({
      mutate: vi.fn(),
      // TanStack Query v5 mutation: busy 状态用 isPending（v4 的 isLoading 已重命名）。
      // 同时保留 isLoading:false 兜底未升级 mock 的类型。
      isPending: false,
      isLoading: false,
    } as any);
    mockUseUpdateAnnotationStatus.mockReturnValue({
      mutate: vi.fn(),
      isPending: false,
      isLoading: false,
    } as any);
  });

  it('renders round summary + 「打开审核面板」callback with annotations + doc.version', async () => {
    mockUseFileReviewState.mockReturnValue(baseState() as any);
    const onOpenReview = vi.fn();
    render(<FileReviewProgress fileId="f1" onOpenReview={onOpenReview} />);
    expect(screen.getByText(/第 1 轮/)).toBeInTheDocument();
    expect(screen.getByText(/high:1 medium:0 low:0/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /打开审核面板/ }));
    await waitFor(() => expect(onOpenReview).toHaveBeenCalledTimes(1));
    const [annotations, version] = onOpenReview.mock.calls[0];
    expect(version).toBe('v2');
    expect(annotations[0].id).toBe('a1');
  });

  it('has_result 时显示「下载成稿」，onPreviewDoc 只收 fileVersion（R-8 不再透传对象名）', () => {
    const onPreviewDoc = vi.fn();
    mockUseFileReviewState.mockReturnValue(baseState() as any);
    render(<FileReviewProgress fileId="f1" onPreviewDoc={onPreviewDoc} />);
    fireEvent.click(screen.getByRole('button', { name: /下载成稿/ }));
    expect(onPreviewDoc).toHaveBeenCalledTimes(1);
    expect(onPreviewDoc).toHaveBeenCalledWith('v2');
  });

  it('has_result=false（从未落盘）不显示「下载成稿」', () => {
    mockUseFileReviewState.mockReturnValue(
      baseState({
        data: {
          ...baseState().data,
          data: {
            ...baseState().data.data,
            doc: { has_result: false, version: '' },
          },
        },
      }) as any,
    );
    render(<FileReviewProgress fileId="f1" />);
    expect(screen.queryByRole('button', { name: /下载成稿/ })).toBeNull();
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
    // 必须只匹配 spinner 文案：状态标签是「第 1 轮 · 审核中」，写成
    // /正在审核|审核中/ 会同时命中两处元素而抛多元素异常。
    expect(screen.getByText(/正在审核/)).toBeInTheDocument();
  });

  it('点击「选择级别修复」调起 Popover，勾选 high + medium 后提交触发 useFixFileReview.mutate', async () => {
    const mutate = vi.fn();
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

  it('fix 被服务端闸门拒绝时，Popover 必须显示服务端文案（不吞错）', () => {
    // 服务端把拒绝原因（含级别中文名 + 待修复问题全貌）放在 mutation 的 error 里；
    // 组件若只消费 isPending，用户点「确认修复」被拒后界面毫无反馈 —— 富文案等于白下沉。
    const state = baseState();
    mockUseFileReviewState.mockReturnValue(state as any);
    const okFix = {
      mutate: vi.fn(),
      isPending: false,
      isLoading: false,
      error: null,
    };
    mockUseFixFileReview.mockReturnValue(okFix as any);
    const { rerender } = render(<FileReviewProgress fileId="f1" />);
    fireEvent.click(screen.getByRole('button', { name: '选择级别修复' }));
    expect(screen.queryByText(/待修复问题/)).toBeNull();

    // 提交被拒：Popover 不关闭，就地显示原因
    mockUseFixFileReview.mockReturnValue({
      ...okFix,
      error: new Error(
        '没有【提示】级别的待修复问题。待修复问题：共 1 条（严重 1 条）',
      ),
    } as any);
    rerender(<FileReviewProgress fileId="f1" />);
    expect(screen.getByText(/待修复问题/)).toBeInTheDocument();
    expect(screen.getByText(/共 1 条（严重 1 条）/)).toBeInTheDocument();
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

  it('round.stale=true（进程重启后卡住的轮次）：显示已中断、不转圈、不给修复入口', () => {
    // 服务端 stale 判据成立 ⇒ 后台线程已不存在，那一轮永远不会出结果。
    // 三件事必须同时变：状态文案（不是「审核中」）、spinner（不是「正在审核」）、
    // 修复入口（服务端 stale 闸门会拒绝一切 fix，留着按钮＝给用户一个必然失败的入口）。
    mockUseFileReviewState.mockReturnValue(
      baseState({
        data: {
          ...baseState().data,
          data: {
            ...baseState().data.data,
            fix_rounds_left: 3,
            current: {
              ...baseState().data.data.current,
              status: 'reviewing',
              stale: true,
            },
          },
        },
      }) as any,
    );
    render(<FileReviewProgress fileId="f1" />);
    // 必须匹配引导行的完整措辞而非 /已中断/ —— 状态标签「第 1 轮 · 已中断」也含这三个字，
    // 用宽正则 getByText 会匹配到两个元素并抛异常。
    expect(screen.getByText(/本轮已中断/)).toBeInTheDocument();
    expect(screen.getByText(/重新发起审核/)).toBeInTheDocument();
    expect(screen.queryByText(/正在审核/)).toBeNull();
    expect(screen.queryByRole('button', { name: '选择级别修复' })).toBeNull();
  });

  it('failed 轮显示失败原因（error），不只显示「失败」两个字', () => {
    mockUseFileReviewState.mockReturnValue(
      baseState({
        data: {
          ...baseState().data,
          data: {
            ...baseState().data.data,
            current: {
              ...baseState().data.data.current,
              status: 'failed',
              error: 'LLM 输出无法解析为修复补丁列表',
            },
          },
        },
      }) as any,
    );
    render(<FileReviewProgress fileId="f1" />);
    expect(screen.getByText(/失败原因/)).toBeInTheDocument();
    expect(screen.getByText(/无法解析/)).toBeInTheDocument();
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
