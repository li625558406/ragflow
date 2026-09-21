// 文件审核进度卡：c-chat 对话与 flow AI 对话区共用
// （T11 交接契约：必须复用 useFileReviewState 拿数据，不自写轮询）。
import {
  useFileReviewState,
  useFixFileReview,
  useRevertAnnotation,
  useUpdateAnnotationStatus,
} from '@/hooks/use-file-review-request';
import FileReviewProgress, {
  extractFileReviewTarget,
} from '@/pages/c-chat/file-review-progress';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('@/hooks/use-file-review-request', () => ({
  useFileReviewState: vi.fn(),
  useFixFileReview: vi.fn(),
  useUpdateAnnotationStatus: vi.fn(),
  useRevertAnnotation: vi.fn(),
}));

const mockUseFileReviewState = vi.mocked(useFileReviewState);
const mockUseFixFileReview = vi.mocked(useFixFileReview);
const mockUseUpdateAnnotationStatus = vi.mocked(useUpdateAnnotationStatus);
const mockUseRevertAnnotation = vi.mocked(useRevertAnnotation);

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
    mockUseRevertAnnotation.mockReturnValue({
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

  it('不传 onSaveAsVersion 不渲染「存为流程版本」（c-chat 无流程版本概念）', () => {
    mockUseFileReviewState.mockReturnValue(baseState() as any);
    render(<FileReviewProgress fileId="f1" />);
    expect(screen.queryByRole('button', { name: /存为流程版本/ })).toBeNull();
  });

  it('点击「存为流程版本」回调 (taskId, fileVersion)，成功翻转「已存为流程版本」且禁用', async () => {
    mockUseFileReviewState.mockReturnValue(baseState() as any);
    const onSaveAsVersion = vi.fn().mockResolvedValue(undefined);
    render(
      <FileReviewProgress fileId="f1" onSaveAsVersion={onSaveAsVersion} />,
    );
    fireEvent.click(screen.getByRole('button', { name: '存为流程版本' }));
    expect(onSaveAsVersion).toHaveBeenCalledWith('t1', 'v2');
    await waitFor(() =>
      expect(
        screen.getByRole('button', { name: '已存为流程版本' }),
      ).toBeDisabled(),
    );
  });

  it('保存失败 reject 翻转「保存失败，重试」且可再次点击', async () => {
    mockUseFileReviewState.mockReturnValue(baseState() as any);
    const onSaveAsVersion = vi
      .fn()
      .mockRejectedValueOnce(new Error('boom'))
      .mockResolvedValueOnce(undefined);
    render(
      <FileReviewProgress fileId="f1" onSaveAsVersion={onSaveAsVersion} />,
    );
    fireEvent.click(screen.getByRole('button', { name: '存为流程版本' }));
    await waitFor(() =>
      expect(
        screen.getByRole('button', { name: '保存失败，重试' }),
      ).toBeEnabled(),
    );
    // 失败重试走通后翻转为已存
    fireEvent.click(screen.getByRole('button', { name: '保存失败，重试' }));
    await waitFor(() =>
      expect(
        screen.getByRole('button', { name: '已存为流程版本' }),
      ).toBeDisabled(),
    );
    expect(onSaveAsVersion).toHaveBeenCalledTimes(2);
  });

  it('修复轮结果：统计已修复/未修复（fixed=AI修复轮成果，手动批注不计入未修复），默认收起明细', () => {
    // demo02 回归场景：修复轮实际修了 2/3 条严重项，但卡片毫无反馈，用户以为一条没修。
    // 手动批注（source='human'）走 resolved/wontfix，不参与修复轮统计。
    const ann = (over: any) => ({
      id: 'a',
      round_id: 'r1',
      task_id: 't1',
      file_id: 'f1',
      file_version: 'v1',
      type: 'format',
      severity: 'high',
      issue: '',
      suggestion: '',
      matched_text: '',
      source: 'ai',
      status: 'open',
      prev_annotation_id: '',
      anchor: {},
      ...over,
    });
    mockUseFileReviewState.mockReturnValue(
      baseState({
        data: {
          ...baseState().data,
          data: {
            ...baseState().data.data,
            fix_rounds_left: 2,
            current: {
              ...baseState().data.data.current,
              round_no: 2,
              status: 'done',
            },
            rounds: [
              { ...baseState().data.data.current },
              {
                ...baseState().data.data.current,
                round_no: 2,
                status: 'done',
              },
            ],
            annotations: [
              ann({
                id: 'fx1',
                status: 'fixed',
                severity: 'high',
                issue: '签章页缺少公章',
              }),
              ann({
                id: 'fx2',
                status: 'fixed',
                severity: 'high',
                issue: '投标函日期缺失',
              }),
              ann({
                id: 'op1',
                status: 'open',
                severity: 'high',
                issue: '评分项未写明分值',
              }),
              ann({
                id: 'op2',
                status: 'open',
                severity: 'medium',
                issue: '格式不统一',
              }),
              ann({
                id: 'man1',
                status: 'open',
                severity: 'low',
                source: 'human',
                issue: '我的人工批注',
              }),
            ],
          },
        },
      }) as any,
    );
    render(<FileReviewProgress fileId="f1" />);
    expect(screen.getByText(/已修复 2 项/)).toBeInTheDocument();
    expect(screen.getByText(/未修复 2 项/)).toBeInTheDocument();
    // 手动批注不得计入未修复
    expect(screen.queryByText(/人工批注/)).toBeNull();
    // 默认收起：明细里的 issue 文案不可见
    expect(screen.queryByText(/签章页缺少公章/)).toBeNull();
    // 展开后可见 fixed 与 open 明细
    fireEvent.click(screen.getByRole('button', { name: /查看明细/ }));
    expect(screen.getByText(/签章页缺少公章/)).toBeInTheDocument();
    expect(screen.getByText(/投标函日期缺失/)).toBeInTheDocument();
    expect(screen.getByText(/评分项未写明分值/)).toBeInTheDocument();
    expect(screen.getByText(/格式不统一/)).toBeInTheDocument();
  });

  it('只有一轮（首轮 annotated）不渲染修复结果区', () => {
    mockUseFileReviewState.mockReturnValue(baseState() as any);
    render(<FileReviewProgress fileId="f1" />);
    expect(screen.queryByText(/已修复/)).toBeNull();
    expect(screen.queryByText(/未修复/)).toBeNull();
  });

  it('修复轮 0 落地（fixed=0）：如实显示「已修复 0 项」而非谎报成功', () => {
    mockUseFileReviewState.mockReturnValue(
      baseState({
        data: {
          ...baseState().data,
          data: {
            ...baseState().data.data,
            current: {
              ...baseState().data.data.current,
              round_no: 2,
              status: 'done',
            },
            rounds: [
              { ...baseState().data.data.current },
              {
                ...baseState().data.data.current,
                round_no: 2,
                status: 'done',
              },
            ],
          },
        },
      }) as any,
    );
    render(<FileReviewProgress fileId="f1" />);
    expect(screen.getByText(/已修复 0 项/)).toBeInTheDocument();
    expect(screen.getByText(/未修复 1 项/)).toBeInTheDocument();
  });

  it('fixed+patch 展开显示修复前/后对比与回退/确认操作；回退经 confirm 调 useRevertAnnotation', async () => {
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true);
    const revertMutate = vi.fn();
    mockUseRevertAnnotation.mockReturnValue({
      mutate: revertMutate,
      isPending: false,
      isLoading: false,
    } as any);
    mockUseFileReviewState.mockReturnValue(
      baseState({
        data: {
          ...baseState().data,
          data: {
            ...baseState().data.data,
            current: {
              ...baseState().data.data.current,
              round_no: 2,
              status: 'done',
            },
            rounds: [
              { ...baseState().data.data.current },
              {
                ...baseState().data.data.current,
                round_no: 2,
                status: 'done',
              },
            ],
            annotations: [
              {
                id: 'a1',
                round_id: 'r1',
                task_id: 't1',
                file_id: 'f1',
                file_version: 'v1',
                type: 'format',
                severity: 'high',
                issue: '日期格式错误',
                suggestion: '',
                matched_text: '',
                source: 'ai',
                status: 'fixed',
                prev_annotation_id: '',
                anchor: {},
                patch: { find: '2025年1月', replace: '2026年9月' },
              },
            ],
          },
        },
      }) as any,
    );
    render(<FileReviewProgress fileId="f1" />);
    fireEvent.click(screen.getByRole('button', { name: /查看明细/ }));
    // 红绿对比块 + 前后文本
    expect(screen.getByText('修复前 → 修复后')).toBeInTheDocument();
    expect(screen.getByText('2025年1月')).toBeInTheDocument();
    expect(screen.getByText('2026年9月')).toBeInTheDocument();
    // 回退：confirm 拦截通过后调 revert mutation
    fireEvent.click(screen.getByRole('button', { name: '回退' }));
    expect(confirmSpy).toHaveBeenCalled();
    expect(revertMutate).toHaveBeenCalledWith(
      'a1',
      expect.objectContaining({ onError: expect.any(Function) }),
    );
    confirmSpy.mockRestore();
  });

  it('回退 confirm 取消时不调 revert', () => {
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(false);
    const revertMutate = vi.fn();
    mockUseRevertAnnotation.mockReturnValue({
      mutate: revertMutate,
      isPending: false,
      isLoading: false,
    } as any);
    mockUseFileReviewState.mockReturnValue(
      baseState({
        data: {
          ...baseState().data,
          data: {
            ...baseState().data.data,
            current: {
              ...baseState().data.data.current,
              round_no: 2,
              status: 'done',
            },
            rounds: [
              { ...baseState().data.data.current },
              {
                ...baseState().data.data.current,
                round_no: 2,
                status: 'done',
              },
            ],
            annotations: [
              {
                id: 'a1',
                round_id: 'r1',
                task_id: 't1',
                file_id: 'f1',
                file_version: 'v1',
                type: 'format',
                severity: 'high',
                issue: '日期格式错误',
                suggestion: '',
                matched_text: '',
                source: 'ai',
                status: 'fixed',
                prev_annotation_id: '',
                anchor: {},
                patch: { find: 'A', replace: 'B' },
              },
            ],
          },
        },
      }) as any,
    );
    render(<FileReviewProgress fileId="f1" />);
    fireEvent.click(screen.getByRole('button', { name: /查看明细/ }));
    fireEvent.click(screen.getByRole('button', { name: '回退' }));
    expect(revertMutate).not.toHaveBeenCalled();
    confirmSpy.mockRestore();
  });

  it('resolved+patch（确认保留）计入已修复、显示已确认保留且无操作按钮；无 patch 的 fixed 不显示对比', () => {
    const statusMutate = vi.fn();
    mockUseUpdateAnnotationStatus.mockReturnValue({
      mutate: statusMutate,
      isPending: false,
      isLoading: false,
    } as any);
    mockUseFileReviewState.mockReturnValue(
      baseState({
        data: {
          ...baseState().data,
          data: {
            ...baseState().data.data,
            current: {
              ...baseState().data.data.current,
              round_no: 2,
              status: 'done',
            },
            rounds: [
              { ...baseState().data.data.current },
              {
                ...baseState().data.data.current,
                round_no: 2,
                status: 'done',
              },
            ],
            annotations: [
              {
                id: 'a1',
                round_id: 'r1',
                task_id: 't1',
                file_id: 'f1',
                file_version: 'v1',
                type: 'format',
                severity: 'high',
                issue: '已确认那条',
                suggestion: '',
                matched_text: '',
                source: 'ai',
                status: 'resolved',
                prev_annotation_id: '',
                anchor: {},
                patch: { find: '旧文案', replace: '新文案' },
              },
              {
                id: 'a2',
                round_id: 'r1',
                task_id: 't1',
                file_id: 'f1',
                file_version: 'v1',
                type: 'format',
                severity: 'medium',
                issue: '无补丁那条',
                suggestion: '',
                matched_text: '',
                source: 'ai',
                status: 'fixed',
                prev_annotation_id: '',
                anchor: {},
              },
            ],
          },
        },
      }) as any,
    );
    render(<FileReviewProgress fileId="f1" />);
    expect(screen.getByText(/已修复 2 项/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /查看明细/ }));
    // resolved+patch：已确认保留，无回退/确认按钮，不显示对比
    expect(screen.getByText('已确认保留')).toBeInTheDocument();
    const confirmedRow = screen.getByText('已确认那条').closest('div')!;
    expect(confirmedRow.textContent).not.toContain('修复前');
    // 无 patch 的 fixed：不显示对比与操作（旧版本修复数据无补丁）
    const plainRow = screen.getByText('无补丁那条').closest('div')!;
    expect(plainRow.textContent).not.toContain('修复前');
    expect(plainRow.textContent).not.toContain('回退');
  });
});

describe('extractFileReviewTarget', () => {
  // 生产实测事件形态：component_name 是 DSL 节点显示名，组件类型在 component_type；
  // file_id 唯一来源是节点 outputs（inputs 是空 dict）
  const frEvent = {
    event: 'node_finished',
    data: {
      component_type: 'FileReview',
      component_name: 'FileReview:BraveLionsScan',
      inputs: {},
      outputs: {
        task_id: 't-1',
        round_id: 'r-1',
        file_id: 'f-9',
        content: 'ok',
      },
    },
  };

  it('extracts from component_type match + outputs.file_id', () => {
    expect(extractFileReviewTarget([frEvent])).toEqual({
      fileId: 'f-9',
      taskId: 't-1',
    });
  });

  it('falls back to inputs.review_file_id when outputs lacks file_id（旧后端兜底）', () => {
    const ev = {
      event: 'node_finished',
      data: {
        component_name: 'FileReview',
        inputs: { review_file_id: 'f-legacy' },
        outputs: { task_id: 't-2' },
      },
    };
    expect(extractFileReviewTarget([ev])).toEqual({
      fileId: 'f-legacy',
      taskId: 't-2',
    });
  });

  it('ignores other components and non-node_finished events', () => {
    const others = [
      { event: 'message', data: { content: 'hi' } },
      {
        event: 'node_finished',
        data: {
          component_type: 'Agent',
          component_name: 'FileReview:模仿者', // 显示名含 FileReview 也不得误匹配
          outputs: { task_id: 'bad', file_id: 'bad' },
        },
      },
      ...Array.from({ length: 50 }, (_, i) => ({
        event: 'message',
        data: { content: `pad-${i}` },
      })),
    ];
    expect(extractFileReviewTarget([...others, frEvent])).toEqual({
      fileId: 'f-9',
      taskId: 't-1',
    });
  });

  it('returns null on missing fileId/taskId/undefined events', () => {
    const noTask = {
      event: 'node_finished',
      data: { component_type: 'FileReview', outputs: { file_id: 'f-9' } },
    };
    const noFile = {
      event: 'node_finished',
      data: { component_type: 'FileReview', outputs: { task_id: 't-1' } },
    };
    expect(extractFileReviewTarget([noTask])).toBeNull();
    expect(extractFileReviewTarget([noFile])).toBeNull();
    expect(extractFileReviewTarget(undefined)).toBeNull();
    expect(extractFileReviewTarget([])).toBeNull();
  });
});
