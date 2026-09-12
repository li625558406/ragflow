// 范本填写断连重连轮询 hook 单测：enabled 门控 / stalled 中断可达 /
// 终态 values 缺省不清空 / interval 不随 templates 引用变化重建（请求放大防御）。
// request/api 均为 mock，不触网。
import request from '@/utils/request';
import { act, renderHook } from '@testing-library/react';
import type { ITemplateFillTemplate } from '../template-fill-stream';
import { useTemplateFillTaskPoll } from '../use-template-fill-task-poll';

jest.mock('@/utils/request', () => ({
  __esModule: true,
  default: { get: jest.fn() },
}));
jest.mock('@/utils/api', () => ({
  __esModule: true,
  default: {
    templateFillTaskProgress: (id: string) =>
      `/mock/api/v1/template_fill/task/${id}/progress`,
  },
}));

const mockedGet = request.get as unknown as jest.Mock;

const fillingTpl = (
  extra: Partial<ITemplateFillTemplate> = {},
): ITemplateFillTemplate => ({
  template_id: 't1',
  name: '范本一',
  status: 'filling',
  task_id: 'task-1',
  ...extra,
});

// 请求信封：hook 里 `const { data } = await request.get(...)` 后 `data?.data || data`
const envelope = (payload: Record<string, unknown>) =>
  Promise.resolve({ data: { code: 0, data: payload } });

const flush = () => act(async () => {});

describe('useTemplateFillTaskPoll', () => {
  beforeEach(() => {
    mockedGet.mockReset();
  });
  afterEach(() => {
    jest.useRealTimers();
  });

  it('enabled=false（流式期间）不发起任何请求', async () => {
    jest.useFakeTimers();
    const { result } = renderHook(() =>
      useTemplateFillTaskPoll([fillingTpl()], false),
    );
    jest.advanceTimersByTime(6000);
    await flush();
    expect(mockedGet).not.toHaveBeenCalled();
    expect(result.current?.[0].status).toBe('filling');
  });

  it('enabled=true 立即 tick：running 响应合并进度 override', async () => {
    jest.useFakeTimers();
    mockedGet.mockReturnValue(
      envelope({ status: 'running', done: 3, total: 10 }),
    );
    const { result } = renderHook(() =>
      useTemplateFillTaskPoll([fillingTpl()], true),
    );
    await flush();
    expect(mockedGet).toHaveBeenCalledTimes(1);
    expect(result.current?.[0]).toMatchObject({
      done: 3,
      total: 10,
      status: 'filling',
    });
  });

  it('stalled（非终态）→ 判 failed「任务中断，请稍后刷新重试」并停止轮询', async () => {
    jest.useFakeTimers();
    mockedGet.mockReturnValue(
      envelope({ status: 'generating', stalled: true, done: 2, total: 10 }),
    );
    const { result } = renderHook(() =>
      useTemplateFillTaskPoll([fillingTpl()], true),
    );
    await flush();
    expect(result.current?.[0]).toMatchObject({
      status: 'failed',
      error: '任务中断，请稍后刷新重试',
    });
    jest.advanceTimersByTime(4000);
    await flush();
    // stopped 集合生效：后续 tick 不再请求
    expect(mockedGet).toHaveBeenCalledTimes(1);
  });

  it('终态 done 缺 values → 不下 values 键，保留 SSE 已累积的值', async () => {
    jest.useFakeTimers();
    mockedGet.mockReturnValue(
      envelope({
        status: 'done',
        download: { doc_id: 'd1', filename: '成稿.docx' },
      }),
    );
    const { result } = renderHook(() =>
      useTemplateFillTaskPoll(
        [fillingTpl({ values: { 项目名称: 'XX项目' } })],
        true,
      ),
    );
    await flush();
    const merged = result.current?.[0];
    expect(merged?.status).toBe('filled');
    expect(merged?.download).toMatchObject({ doc_id: 'd1' });
    expect(merged?.values).toEqual({ 项目名称: 'XX项目' });
  });

  it('终态 done 带 values → 使用轮询返回的 values', async () => {
    jest.useFakeTimers();
    mockedGet.mockReturnValue(
      envelope({
        status: 'done',
        download: { doc_id: 'd1', filename: '成稿.docx' },
        values: { 预算金额: '100万' },
      }),
    );
    const { result } = renderHook(() =>
      useTemplateFillTaskPoll([fillingTpl()], true),
    );
    await flush();
    expect(result.current?.[0]).toMatchObject({
      status: 'filled',
      values: { 预算金额: '100万' },
    });
  });

  it('SSE 已 filled 的行以 SSE 为准，终态 override 被丢弃', async () => {
    jest.useFakeTimers();
    mockedGet.mockReturnValue(
      envelope({ status: 'failed', error: '过期失败' }),
    );
    const { result } = renderHook(() =>
      useTemplateFillTaskPoll([fillingTpl({ status: 'filled' })], true),
    );
    await flush();
    const merged = result.current?.[0];
    expect(merged?.status).toBe('filled');
    expect(merged?.error).toBeUndefined();
  });

  it('interval 稳定：templates 引用连续变化（模拟 SSE 事件）不重建 interval、不触发立即 tick', async () => {
    jest.useFakeTimers();
    mockedGet.mockReturnValue(
      envelope({ status: 'running', done: 1, total: 10 }),
    );
    const initial = [fillingTpl()];
    const { rerender } = renderHook(
      ({ templates }: { templates: ITemplateFillTemplate[] }) =>
        useTemplateFillTaskPoll(templates, true),
      { initialProps: { templates: initial } },
    );
    await flush();
    expect(mockedGet).toHaveBeenCalledTimes(1); // 挂载立即 tick
    // 模拟流式期间每次 SSE 事件归约都换 templates 引用（旧行为：每次 rerender
    // 都拆建 interval 并立即 tick，请求被事件频率放大）
    rerender({ templates: [fillingTpl({ done: 2 })] });
    await flush();
    rerender({ templates: [fillingTpl({ done: 3 })] });
    await flush();
    expect(mockedGet).toHaveBeenCalledTimes(1);
    // 一个完整周期后恰好多一次轮询
    jest.advanceTimersByTime(2000);
    await flush();
    expect(mockedGet).toHaveBeenCalledTimes(2);
  });

  it('轮询响应异常（reject）不中断：下一周期继续轮询', async () => {
    jest.useFakeTimers();
    mockedGet.mockRejectedValueOnce(new Error('network'));
    const { result } = renderHook(() =>
      useTemplateFillTaskPoll([fillingTpl()], true),
    );
    await flush();
    expect(result.current?.[0].status).toBe('filling'); // 无 override
    jest.advanceTimersByTime(2000);
    await flush();
    expect(mockedGet).toHaveBeenCalledTimes(2);
  });

  it('重叠 tick 竞态：慢请求迟到的 running 响应不覆盖终态 override', async () => {
    jest.useFakeTimers();
    let resolveSlow: (v: {
      data: { code: number; data: Record<string, unknown> };
    }) => void = () => {};
    const slowPromise = new Promise<{
      data: { code: number; data: Record<string, unknown> };
    }>((resolve) => {
      resolveSlow = resolve;
    });
    // tick1（挂载立即）：慢请求 pending 不返回
    mockedGet.mockImplementationOnce(() => slowPromise);
    // tick2（下一周期）：快请求先返回终态 done
    mockedGet.mockImplementationOnce(() =>
      envelope({ status: 'done', download: { doc_id: 'd1' } }),
    );
    const { result } = renderHook(() =>
      useTemplateFillTaskPoll([fillingTpl()], true),
    );
    await flush();
    expect(mockedGet).toHaveBeenCalledTimes(1);
    jest.advanceTimersByTime(2000);
    await flush();
    expect(mockedGet).toHaveBeenCalledTimes(2);
    // 快 tick 终态已落地：stopped.add + override {status:'filled'}
    expect(result.current?.[0]).toMatchObject({
      status: 'filled',
      download: { doc_id: 'd1' },
    });
    // 慢 tick 的 running 快照迟到：必须被丢弃，终态不回退
    resolveSlow({
      data: { code: 0, data: { status: 'running', done: 5, total: 10 } },
    });
    await flush();
    expect(result.current?.[0]).toMatchObject({
      status: 'filled',
      download: { doc_id: 'd1' },
    });
  });

  it('unmount 后不再发起请求', async () => {
    jest.useFakeTimers();
    mockedGet.mockReturnValue(
      envelope({ status: 'running', done: 1, total: 2 }),
    );
    const { rerender, unmount } = renderHook(
      ({ templates }: { templates: ITemplateFillTemplate[] }) =>
        useTemplateFillTaskPoll(templates, true),
      { initialProps: { templates: [fillingTpl()] } },
    );
    await flush();
    expect(mockedGet).toHaveBeenCalledTimes(1);
    rerender({ templates: [fillingTpl({ done: 1 })] });
    await flush();
    unmount();
    // 卸载后推进多个周期：effect cleanup 已置 cancelled + 清 interval，不再请求
    jest.advanceTimersByTime(6000);
    await flush();
    expect(mockedGet).toHaveBeenCalledTimes(1);
  });
});
