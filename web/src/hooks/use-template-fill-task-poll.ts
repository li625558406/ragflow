// 范本填写断连重连轮询（方案A）：历史恢复态（SSE 已停）下，对带 task_id 且仍在
// filling 的模板行轮询 progress 端点；running → 本地 override 更新进度+实时预览值；
// 终态 → 合成 filled 成稿卡（下载/预览走桥接后的 /agents/download 契约）。
// 实时流式期间事件也带 task_id，但 SSE 为准、override 与 SSE 幂等合并（后到者胜），
// 无状态冲突（设计 §5）。
// 终态后仍需低频权威刷新（2026-09-22）：对话就地修改（FillTemplate action=modify）
// 只回写 DB 行、不发任何 SSE 事件 → 已合成成稿卡上的 unfilled/filled 清单会永久
// 停在填写完成时刻（例：modify 补填「招标代理机构名称」后卡片仍显示在未填充列表）。
// 故 filled 行以 10s 低频轮询权威派生清单（unfilled/filled 两键），status/download
// 永不被轮询降级；values 不在此合并——实时预览抽屉已有同款 3s 权威轮询（3s 粒度
// 增量重涂），卡片清单只需 unfilled/filled，省全量产值重复传输。
import type { ITemplateFillTemplate } from '@/hooks/template-fill-stream';
import api from '@/utils/api';
import request from '@/utils/request';
import { useEffect, useRef, useState } from 'react';

const POLL_INTERVAL_MS = 2000;
// 终态行权威刷新间隔：modify 是低频人工操作，10s 延迟可接受；
// filling 行保持 2s（进度实时性优先）
const TERMINAL_REFRESH_MS = 10000;

// 终态集合，与后端 TERMINAL_TASK_STATUSES 保持一致（done/partial/failed/cancelled）
const TERMINAL_TASK_STATUSES = ['done', 'partial', 'failed', 'cancelled'];

export function useTemplateFillTaskPoll(
  templates: ITemplateFillTemplate[] | undefined,
  enabled: boolean,
) {
  const [overrides, setOverrides] = useState<
    Record<string, Partial<ITemplateFillTemplate>>
  >({});
  // 终态权威刷新产物（与 overrides 分槽）：unfilled/filled/values 三键，专供已
  // 合成 filled 行合并。分槽原因：overrides 槽的「SSE filled 行丢弃 override」契约
  // 防的是轮询旧数据压过新 SSE；而 refresh 槽本身就是终态端点权威派生，必须
  // 能更新 SSE 合成行的清单（这正是 modify 后刷新的唯一通道）。
  // values 必须一并合并：derive_filled 清单只含 {key,name} 不含值，卡片「已填充
  // 列表」的值靠从 t.values 按 key join——modify 补填的 key 在 SSE 累积的旧
  // values 里不存在 → 条目在、冒号后空白（2026-09-22 生产实测）。
  const [refreshOverrides, setRefreshOverrides] = useState<
    Record<
      string,
      Pick<ITemplateFillTemplate, 'unfilled' | 'filled' | 'values'>
    >
  >({});
  // 已到终态的 task_id，停 filling 轮询（终态本地合成后不再按 2s 请求；
  // 行转 filled 后改走 10s 低频 refresh，见 targets 筛选）。
  // 刻意不清理：retry 场景走新一轮消息/新卡片（新 task_id），同 task_id 复活不存在，
  // 清理反而可能让已合成终态的行被再次轮询覆盖。
  const stopped = useRef<Set<string>>(new Set());
  // 终态行上次 refresh 时刻（节流）：挂载立即刷一次，此后每 TERMINAL_REFRESH_MS 一次
  const lastRefreshAt = useRef<Record<string, number>>({});
  // 最新 templates 存 ref：effect deps 只留 [enabled]，interval 不随 SSE 事件
  // （流式期间每次归约都换 templates 引用）拆建重置，避免请求被事件频率放大。
  const templatesRef = useRef(templates);
  templatesRef.current = templates;

  useEffect(() => {
    if (!enabled) return;
    let cancelled = false;

    const tick = async () => {
      const now = Date.now();
      const targets = (templatesRef.current || []).filter((t) => {
        if (!t.task_id) return false;
        // filled 行走 refresh 通道（stopped 只封 filling 通道，见响应处理）
        if (t.status === 'filled') return true;
        return t.status === 'filling' && !stopped.current.has(t.task_id);
      });
      for (const t of targets) {
        const taskId = t.task_id!;
        // 终态行：10s 节流的权威清单刷新；filling 行：每 tick（2s）轮询进度
        if (t.status === 'filled') {
          if (now - (lastRefreshAt.current[taskId] || 0) < TERMINAL_REFRESH_MS)
            continue;
          lastRefreshAt.current[taskId] = now;
        } else if (t.status !== 'filling') {
          continue;
        }
        try {
          // request 为 umi-request extend 实例，data 为 {code, data, message} 信封
          const { data } = await request.get(
            api.templateFillTaskProgress(taskId),
          );
          if (cancelled) return;
          const d = data?.data || data;
          // 响应到达时行的最新状态（await 期间 SSE 可能已把行转 filled）
          const cur = (templatesRef.current || []).find(
            (x) => x.task_id === taskId,
          );
          if (!d?.status) continue;
          // 终态行 refresh 通道：只写清单两键+values（modify 后 DB 权威派生）；
          // 非终态响应忽略（终态行不降级），failed/cancelled 行已被 targets 排除。
          // 必须先于 stopped 防护判定——filling 合成过终态的行 id 恒在 stopped 中，
          // 而本通道正是为它续上的刷新路径，不受该集合约束
          if (cur?.status === 'filled') {
            if (!TERMINAL_TASK_STATUSES.includes(d.status)) continue;
            const patch: Pick<
              ITemplateFillTemplate,
              'unfilled' | 'filled' | 'values'
            > = {};
            // 缺省（旧后端/全填满的 null）不下键：不清 SSE 已有清单
            if (d.unfilled) patch.unfilled = d.unfilled;
            if (d.filled) patch.filled = d.filled;
            if (d.values && Object.keys(d.values).length)
              patch.values = d.values;
            if (!Object.keys(patch).length) continue;
            setRefreshOverrides((prev) =>
              prev[taskId] === patch ? prev : { ...prev, [taskId]: patch },
            );
            continue;
          }
          // 迟到响应防护：unmount 或同任务更快的 tick 已处理终态（stopped）时，
          // 直接丢弃本响应——否则 running 快照会整键覆盖终态 override（丢 status:
          // 'filled'），而该 id 在 filling 通道永不再轮询 → 卡片永久卡死
          if (cancelled || stopped.current.has(taskId)) continue;
          // stalled 中断：后端探活判定任务已死但 status 仍是生成中——直接判失败停轮询，
          // 否则非终态分支只更新进度、永远轮询，中断提示不可达
          if (d.stalled) {
            stopped.current.add(taskId);
            setOverrides((prev) => ({
              ...prev,
              [taskId]: {
                status: 'failed' as const,
                error: '任务中断，请稍后刷新重试',
              },
            }));
            continue;
          }
          if (TERMINAL_TASK_STATUSES.includes(d.status)) {
            stopped.current.add(taskId);
            setOverrides((prev) => ({
              ...prev,
              [taskId]:
                d.status === 'done' && d.download
                  ? {
                      status: 'filled' as const,
                      download: d.download,
                      // values 缺省时不下该键：避免清掉 SSE/回放已累积的 t.values
                      //（「查看填写内容」入口依赖它）
                      ...(d.values && Object.keys(d.values).length
                        ? { values: d.values }
                        : {}),
                      // unfilled 同防御：缺省（旧后端/全填满）不清 SSE 已有汇总
                      ...(d.unfilled ? { unfilled: d.unfilled } : {}),
                      // filled 同防御（与 unfilled 镜像）
                      ...(d.filled ? { filled: d.filled } : {}),
                    }
                  : {
                      status: 'failed' as const,
                      error: d.error || '填写失败',
                    },
            }));
          } else {
            setOverrides((prev) => ({
              ...prev,
              [taskId]: {
                done: d.done ?? 0,
                total: d.total ?? 0,
                ...(d.values && Object.keys(d.values).length
                  ? { values: d.values }
                  : {}),
              },
            }));
          }
        } catch {
          // 网络抖动/端点异常：下一轮继续，不中断其他任务
        }
      }
    };

    // 仅依赖 enabled（历史恢复态挂载即启停）：interval 稳定，不随流式事件重建；
    // 无轮询目标时 tick 空转直接返回，开销可忽略
    const timer = setInterval(tick, POLL_INTERVAL_MS);
    tick();
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [enabled]);

  // 合并 override：SSE 已到 filled 的行对 overrides 槽以 SSE 为准（丢弃）；
  // refreshOverrides 槽例外——它是终态端点权威派生，必须能更新已合成 filled 行的
  // unfilled/filled（modify 后卡片清单刷新的唯一通道），status/download 永不经过它。
  // 无 override 时直接透传原引用，避免每次渲染都 map 出新数组引发下游重渲染
  if (!Object.keys(overrides).length && !Object.keys(refreshOverrides).length)
    return templates;
  const merged = templates?.map((t) => {
    let row = t;
    const rid = t.task_id ? refreshOverrides[t.task_id] : undefined;
    if (t.status === 'filled' && rid) row = { ...t, ...rid };
    const ov = t.task_id ? overrides[t.task_id] : undefined;
    if (!ov) return row;
    if (row.status === 'filled') return row;
    return { ...row, ...ov } as ITemplateFillTemplate;
  });
  return merged;
}
