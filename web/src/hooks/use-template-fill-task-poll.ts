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
  // 已到终态的 task_id，停轮询（终态本地合成后不再请求）
  const stopped = useRef<Set<string>>(new Set());

  useEffect(() => {
    if (!enabled || !templates?.length) return;
    const targets = templates.filter(
      (t) =>
        t.task_id && t.status === 'filling' && !stopped.current.has(t.task_id),
    );
    if (!targets.length) return;
    let cancelled = false;

    const tick = async () => {
      for (const t of targets) {
        const taskId = t.task_id!;
        try {
          // request 为 axios 实例，data 为 {code, data, message} 信封
          const { data } = await request.get(
            api.templateFillTaskProgress(taskId),
          );
          if (cancelled) return;
          const d = data?.data || data;
          if (!d?.status) continue;
          if (TERMINAL_TASK_STATUSES.includes(d.status)) {
            stopped.current.add(taskId);
            setOverrides((prev) => ({
              ...prev,
              [taskId]:
                d.status === 'done' && d.download
                  ? {
                      status: 'filled' as const,
                      download: d.download,
                      values: d.values || undefined,
                    }
                  : {
                      status: 'failed' as const,
                      error: d.stalled
                        ? '任务中断，可重试'
                        : d.error || '填写失败',
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

    const timer = setInterval(tick, POLL_INTERVAL_MS);
    tick();
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [enabled, templates]);

  // 合并 override：SSE 已到 filled 的行以 SSE 为准（丢弃 override）；
  // filling 行叠加轮询产物（done/total/values），终态 override 换 status。
  const merged = templates?.map((t) => {
    const ov = t.task_id ? overrides[t.task_id] : undefined;
    if (!ov) return t;
    if (t.status === 'filled') return t;
    return { ...t, ...ov } as ITemplateFillTemplate;
  });
  return merged;
}
