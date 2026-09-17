// 范本填写断连重连轮询（方案A）：历史恢复态（SSE 已停）下，对带 task_id 且仍在
// filling 的模板行轮询 progress 端点；running → 本地 override 更新进度+实时预览值；
// 终态 → 停轮询，filled 本地合成成稿卡（下载/预览走桥接后的 /agents/download 契约）。
// 实时流式期间事件也带 task_id，但 SSE 为准、override 与 SSE 幂等合并（后到者胜），
// 无状态冲突（设计 §5）。
import type { ITemplateFillTemplate } from '@/hooks/template-fill-stream';
import api from '@/utils/api';
import request from '@/utils/request';
import { useEffect, useRef, useState } from 'react';

const POLL_INTERVAL_MS = 2000;

// 终态集合，与后端 TERMINAL_TASK_STATUSES 保持一致（done/partial/failed/cancelled）
const TERMINAL_TASK_STATUSES = ['done', 'partial', 'failed', 'cancelled'];

export function useTemplateFillTaskPoll(
  templates: ITemplateFillTemplate[] | undefined,
  enabled: boolean,
) {
  const [overrides, setOverrides] = useState<
    Record<string, Partial<ITemplateFillTemplate>>
  >({});
  // 已到终态的 task_id，停轮询（终态本地合成后不再请求）。
  // 刻意不清理：retry 场景走新一轮消息/新卡片（新 task_id），同 task_id 复活不存在，
  // 清理反而可能让已合成终态的行被再次轮询覆盖。
  const stopped = useRef<Set<string>>(new Set());
  // 最新 templates 存 ref：effect deps 只留 [enabled]，interval 不随 SSE 事件
  // （流式期间每次归约都换 templates 引用）拆建重置，避免请求被事件频率放大。
  const templatesRef = useRef(templates);
  templatesRef.current = templates;

  useEffect(() => {
    if (!enabled) return;
    let cancelled = false;

    const tick = async () => {
      const targets = (templatesRef.current || []).filter(
        (t) =>
          t.task_id &&
          t.status === 'filling' &&
          !stopped.current.has(t.task_id),
      );
      for (const t of targets) {
        const taskId = t.task_id!;
        try {
          // request 为 umi-request extend 实例，data 为 {code, data, message} 信封
          const { data } = await request.get(
            api.templateFillTaskProgress(taskId),
          );
          if (cancelled) return;
          const d = data?.data || data;
          // 迟到响应防护：unmount 或同任务更快的 tick 已处理终态（stopped）时，
          // 直接丢弃本响应——否则 running 快照会整键覆盖终态 override（丢 status:
          // 'filled'），而该 id 已在 stopped 中永不再轮询 → 卡片永久卡死
          if (cancelled || stopped.current.has(taskId)) continue;
          if (!d?.status) continue;
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

  // 合并 override：SSE 已到 filled 的行以 SSE 为准（丢弃 override）；
  // filling 行叠加轮询产物（done/total/values），终态 override 换 status。
  // 无 override 时直接透传原引用，避免每次渲染都 map 出新数组引发下游重渲染
  if (!Object.keys(overrides).length) return templates;
  const merged = templates?.map((t) => {
    const ov = t.task_id ? overrides[t.task_id] : undefined;
    if (!ov) return t;
    if (t.status === 'filled') return t;
    return { ...t, ...ov } as ITemplateFillTemplate;
  });
  return merged;
}
