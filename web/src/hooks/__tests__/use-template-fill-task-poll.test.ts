// 范本填写断连重连轮询 hook 单测：enabled 门控 / stalled 中断可达 /
// 终态 values 缺省不清空 / interval 不随 templates 引用变化重建（请求放大防御）。
// request/api 均为 mock，不触网。
import request from '@/utils/request';
import { act, renderHook } from '@testing-library/react';
import {
  afterEach,
  beforeEach,
  describe,
  expect,
  it,
  vi,
  type Mock,
} from 'vitest';
import type { ITemplateFillTemplate } from '../template-fill-stream';
import { useTemplateFillTaskPoll } from '../use-template-fill-task-poll';

vi.mock('@/utils/request', () => ({
  __esModule: true,
  default: { get: vi.fn() },
}));
vi.mock('@/utils/api', () => ({
  __esModule: true,
  default: {
    templateFillTaskProgress: (id: string) =>
      `/mock/api/v1/template_fill/task/${id}/progress`,
  },
}));

const mockedGet = request.get as unknown as Mock;

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
    vi.useRealTimers();
  });

  it('enabled=false（流式期间）不发起任何请求', async () => {
    vi.useFakeTimers();
    const { result } = renderHook(() =>
      useTemplateFillTaskPoll([fillingTpl()], false),
    );
    vi.advanceTimersByTime(6000);
    await flush();
    expect(mockedGet).not.toHaveBeenCalled();
    expect(result.current?.[0].status).toBe('filling');
  });

  it('enabled=true 立即 tick：running 响应合并进度 override', async () => {
    vi.useFakeTimers();
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
    vi.useFakeTimers();
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
    vi.advanceTimersByTime(4000);
    await flush();
    // stopped 集合生效：后续 tick 不再请求
    expect(mockedGet).toHaveBeenCalledTimes(1);
  });

  it('终态 done 缺 values → 不下 values 键，保留 SSE 已累积的值', async () => {
    vi.useFakeTimers();
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
    vi.useFakeTimers();
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
    vi.useFakeTimers();
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
    vi.useFakeTimers();
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
    vi.advanceTimersByTime(2000);
    await flush();
    expect(mockedGet).toHaveBeenCalledTimes(2);
  });

  it('轮询响应异常（reject）不中断：下一周期继续轮询', async () => {
    vi.useFakeTimers();
    mockedGet.mockRejectedValueOnce(new Error('network'));
    const { result } = renderHook(() =>
      useTemplateFillTaskPoll([fillingTpl()], true),
    );
    await flush();
    expect(result.current?.[0].status).toBe('filling'); // 无 override
    vi.advanceTimersByTime(2000);
    await flush();
    expect(mockedGet).toHaveBeenCalledTimes(2);
  });

  it('重叠 tick 竞态：慢请求迟到的 running 响应不覆盖终态 override', async () => {
    vi.useFakeTimers();
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
    vi.advanceTimersByTime(2000);
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

  it('终态 done 带 unfilled → 合并 override（历史恢复汇总不丢）', async () => {
    vi.useFakeTimers();
    mockedGet.mockReturnValue(
      envelope({
        status: 'done',
        download: { doc_id: 'd1', filename: '成稿.docx' },
        values: { a: 'x' },
        unfilled: [{ key: 'b', name: '字段乙', required: false }],
      }),
    );
    const { result } = renderHook(() =>
      useTemplateFillTaskPoll([fillingTpl()], true),
    );
    await flush();
    expect(result.current?.[0]).toMatchObject({
      status: 'filled',
      unfilled: [{ key: 'b', name: '字段乙', required: false }],
    });
  });

  it('终态 done 缺 unfilled → 不下键，保留 SSE 已有汇总', async () => {
    vi.useFakeTimers();
    mockedGet.mockReturnValue(
      envelope({
        status: 'done',
        download: { doc_id: 'd1', filename: '成稿.docx' },
      }),
    );
    const { result } = renderHook(() =>
      useTemplateFillTaskPoll(
        [
          fillingTpl({
            status: 'filling',
            unfilled: [{ key: 'k1', name: '字段一', required: true }],
          }),
        ],
        true,
      ),
    );
    await flush();
    expect(result.current?.[0]).toMatchObject({
      status: 'filled',
      unfilled: [{ key: 'k1', name: '字段一', required: true }],
    });
  });

  it('终态 done 带 filled → 合并 override（历史恢复已填清单不丢）', async () => {
    vi.useFakeTimers();
    mockedGet.mockReturnValue(
      envelope({
        status: 'done',
        download: { doc_id: 'd1', filename: '成稿.docx' },
        values: { a: 'x' },
        filled: [{ key: 'a', name: '字段甲' }],
        unfilled: [{ key: 'b', name: '字段乙', required: false }],
      }),
    );
    const { result } = renderHook(() =>
      useTemplateFillTaskPoll([fillingTpl()], true),
    );
    await flush();
    expect(result.current?.[0]).toMatchObject({
      status: 'filled',
      filled: [{ key: 'a', name: '字段甲' }],
      unfilled: [{ key: 'b', name: '字段乙', required: false }],
    });
  });

  it('终态 done 缺 filled → 不下键，保留 SSE 已有清单', async () => {
    vi.useFakeTimers();
    mockedGet.mockReturnValue(
      envelope({
        status: 'done',
        download: { doc_id: 'd1', filename: '成稿.docx' },
      }),
    );
    const { result } = renderHook(() =>
      useTemplateFillTaskPoll(
        [
          fillingTpl({
            status: 'filling',
            filled: [{ key: 'k1', name: '字段一' }],
          }),
        ],
        true,
      ),
    );
    await flush();
    expect(result.current?.[0]).toMatchObject({
      status: 'filled',
      filled: [{ key: 'k1', name: '字段一' }],
    });
  });

  it('终态 done 带空 filled 数组 → 下发（数组为真值，与 unfilled 同语义）', async () => {
    vi.useFakeTimers();
    mockedGet.mockReturnValue(
      envelope({
        status: 'done',
        download: { doc_id: 'd1' },
        filled: [],
      }),
    );
    const { result } = renderHook(() =>
      useTemplateFillTaskPoll(
        [
          fillingTpl({
            status: 'filling',
            filled: [{ key: 'k1', name: '字段一' }],
          }),
        ],
        true,
      ),
    );
    await flush();
    expect(result.current?.[0].filled).toEqual([]);
  });

  it('竞态：终态后迟到的 running 响应不覆盖已落地的 filled', async () => {
    vi.useFakeTimers();
    let resolveSlow: (v: {
      data: { code: number; data: Record<string, unknown> };
    }) => void = () => {};
    const slowPromise = new Promise<{
      data: { code: number; data: Record<string, unknown> };
    }>((resolve) => {
      resolveSlow = resolve;
    });
    mockedGet.mockImplementationOnce(() => slowPromise);
    mockedGet.mockImplementationOnce(() =>
      envelope({
        status: 'done',
        download: { doc_id: 'd1' },
        filled: [{ key: 'k1', name: '字段一' }],
      }),
    );
    const { result } = renderHook(() =>
      useTemplateFillTaskPoll([fillingTpl()], true),
    );
    await flush();
    vi.advanceTimersByTime(2000);
    await flush();
    expect(result.current?.[0]).toMatchObject({
      status: 'filled',
      filled: [{ key: 'k1', name: '字段一' }],
    });
    // 慢 tick 的 running 快照迟到：整键丢弃，filled 不被 undefined 抹掉
    resolveSlow({
      data: { code: 0, data: { status: 'running', done: 5, total: 10 } },
    });
    await flush();
    expect(result.current?.[0]).toMatchObject({
      status: 'filled',
      filled: [{ key: 'k1', name: '字段一' }],
    });
  });

  it('SSE 已 filled 的行：权威 refresh 更新 filled 清单（modify 后刷新），status/download 不降级', async () => {
    vi.useFakeTimers();
    // 2026-09-22 语义：终态行不再永久停轮询，10s 低频权威刷新 unfilled/filled
    // （对话就地 modify 只回写 DB 不发事件——refresh 槽是卡片清单刷新的唯一通道）；
    // status/download 永不经 refresh 槽，SSE 合成的成稿行不降级
    mockedGet.mockReturnValue(
      envelope({
        status: 'done',
        download: { doc_id: 'd2' },
        filled: [{ key: 'zzz', name: '权威名' }],
        unfilled: [{ key: 'empty1', name: '补填前留空', required: true }],
      }),
    );
    const { result } = renderHook(() =>
      useTemplateFillTaskPoll(
        [
          fillingTpl({
            status: 'filled',
            filled: [{ key: 'k1', name: 'SSE名' }],
            download: { doc_id: 'd1', filename: '成稿.docx' },
          }),
        ],
        true,
      ),
    );
    await flush();
    const merged = result.current?.[0];
    // 清单被权威响应更新（modify 生效可见）
    expect(merged?.filled).toEqual([{ key: 'zzz', name: '权威名' }]);
    expect(merged?.unfilled).toEqual([
      { key: 'empty1', name: '补填前留空', required: true },
    ]);
    // 成稿行本体不被轮询降级：status 保持 filled、download 保留 SSE 合成的那份
    expect(merged?.status).toBe('filled');
    expect(merged?.download).toMatchObject({ doc_id: 'd1' });
  });

  it('refresh 带 values → filled 行 values 更新（已填充列表 join 出值，modify 补填可见）', async () => {
    vi.useFakeTimers();
    // derive_filled 清单只含 {key,name} 不含值，卡片值靠 t.values 按 key join；
    // modify 补填的 key 在 SSE 累积的旧 values 里不存在 → 条目在、值空白，
    // 故 refresh 槽必须一并合并 values（2026-09-22 生产实测补的键）
    mockedGet.mockReturnValue(
      envelope({
        status: 'done',
        filled: [
          { key: 'tender_agency_name', name: '招标代理机构名称' },
          { key: 'project_name', name: '项目名称' },
        ],
        values: {
          tender_agency_name: '福建省品辰有限公司',
          project_name: '渔港工程',
        },
      }),
    );
    const { result } = renderHook(() =>
      useTemplateFillTaskPoll(
        [
          fillingTpl({
            status: 'filled',
            // SSE 累积的旧 values：不含 modify 补填的 key
            values: { project_name: '渔港工程' },
          }),
        ],
        true,
      ),
    );
    await flush();
    const merged = result.current?.[0];
    expect(merged?.values).toEqual({
      tender_agency_name: '福建省品辰有限公司',
      project_name: '渔港工程',
    });
    expect(merged?.status).toBe('filled');
  });

  it('终态行 10s 节流：挂载立即刷一次，2s 周期内不重复请求，10s 后再刷', async () => {
    vi.useFakeTimers();
    mockedGet.mockReturnValue(
      envelope({
        status: 'done',
        unfilled: [{ key: 'b', name: '字段乙', required: true }],
      }),
    );
    const { result } = renderHook(() =>
      useTemplateFillTaskPoll(
        [
          fillingTpl({
            status: 'filled',
            unfilled: [{ key: 'old', name: '旧字段', required: true }],
          }),
        ],
        true,
      ),
    );
    await flush();
    expect(mockedGet).toHaveBeenCalledTimes(1);
    expect(result.current?.[0].unfilled).toEqual([
      { key: 'b', name: '字段乙', required: true },
    ]);
    // 8s 内经过 4 个 2s 周期：全部被节流跳过
    vi.advanceTimersByTime(8000);
    await flush();
    expect(mockedGet).toHaveBeenCalledTimes(1);
    // 累计 10s：refresh 放行
    vi.advanceTimersByTime(2000);
    await flush();
    expect(mockedGet).toHaveBeenCalledTimes(2);
  });

  it('终态行 refresh：响应缺 unfilled/filled → 不写键，保留 SSE 已有清单', async () => {
    vi.useFakeTimers();
    mockedGet.mockReturnValue(
      envelope({ status: 'done', download: { doc_id: 'd1' } }),
    );
    const existing = [{ key: 'k1', name: '字段一', required: true }];
    const { result } = renderHook(() =>
      useTemplateFillTaskPoll(
        [fillingTpl({ status: 'filled', unfilled: existing })],
        true,
      ),
    );
    await flush();
    expect(result.current?.[0].unfilled).toBe(existing);
  });

  it('终态行 refresh：响应 running（异常竞态）→ 忽略，行不降级', async () => {
    vi.useFakeTimers();
    mockedGet.mockReturnValue(
      envelope({ status: 'running', done: 5, total: 10 }),
    );
    const { result } = renderHook(() =>
      useTemplateFillTaskPoll(
        [fillingTpl({ status: 'filled', download: { doc_id: 'd1' } })],
        true,
      ),
    );
    await flush();
    const merged = result.current?.[0];
    expect(merged?.status).toBe('filled');
    expect(merged?.done).toBeUndefined();
  });

  it('filling 行合成终态（stopped）后行转 filled，改走 refresh 通道继续低频刷新', async () => {
    vi.useFakeTimers();
    mockedGet.mockImplementation(() =>
      Promise.resolve({
        data: {
          code: 0,
          data: {
            status: 'done',
            download: { doc_id: 'd1' },
            unfilled: [{ key: 'a', name: '首轮未填', required: true }],
          },
        },
      }),
    );
    const { result, rerender } = renderHook(
      ({ templates }: { templates: ITemplateFillTemplate[] }) =>
        useTemplateFillTaskPoll(templates, true),
      { initialProps: { templates: [fillingTpl()] } },
    );
    await flush();
    // filling 合成终态：stopped.add，2s 通道停
    expect(result.current?.[0]).toMatchObject({
      status: 'filled',
      unfilled: [{ key: 'a', name: '首轮未填', required: true }],
    });
    // 行转 filled 后 refresh 通道立即可用（挂载后首个 tick 已把 filled 行纳入 targets，
    // 但节流以首轮 filling 请求时刻为基线 → 推进 10s 放行）
    rerender({
      templates: [
        fillingTpl({
          status: 'filled',
          download: { doc_id: 'd1' },
          unfilled: [{ key: 'a', name: '首轮未填', required: true }],
        }),
      ],
    });
    mockedGet.mockImplementation(() =>
      Promise.resolve({
        data: {
          code: 0,
          data: {
            status: 'done',
            unfilled: [{ key: 'b', name: 'modify 后新清单', required: true }],
          },
        },
      }),
    );
    vi.advanceTimersByTime(10000);
    await flush();
    expect(result.current?.[0].unfilled).toEqual([
      { key: 'b', name: 'modify 后新清单', required: true },
    ]);
  });

  it('unmount 后不再发起请求', async () => {
    vi.useFakeTimers();
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
    vi.advanceTimersByTime(6000);
    await flush();
    expect(mockedGet).toHaveBeenCalledTimes(1);
  });
});
