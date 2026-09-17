// 刷新恢复权威化（设计 2026-09-16）：重放态只负责「运行 id 发现 + 旧数据兜底」，
// 权威恢复来自运行快照端点（/template/fill/fill-run/<id>/snapshot）。
// 发现 canvas run id → 拉快照；exists && !finished 每 3s 轮询直至终态/键过期；
// 快照可用则整体替换重放态；键过期且重放态有未提交挂起卡 → 把挂起卡标记
// expired（不再显示可交互僵尸卡）。旧数据无 run id → 返回 undefined，行为退化为现状。
import {
  buildStateFromRunSnapshot,
  findCanvasRunId,
  type ITemplateFillRunSnapshot,
  type ITemplateFillState,
} from '@/hooks/template-fill-stream';
import api from '@/utils/api';
import request from '@/utils/request';
import { useEffect, useMemo, useRef, useState } from 'react';

const POLL_INTERVAL_MS = 3000;

export function useTemplateFillRunRecovery(
  /** 原始事件序列（flow_ai_chat.template_fill_events 或消息重放产物），用于发现 run id */
  events: unknown,
  /** 重放得到的兜底态；无 run id 时原样透出 */
  replayed: ITemplateFillState | undefined,
): ITemplateFillState | undefined {
  const [snapshotState, setSnapshotState] = useState<
    ITemplateFillState | undefined
  >(undefined);
  // 快照键不存在/已过期（exists=false）：停轮询，重放态挂起卡降级为 expired
  const [snapshotMissing, setSnapshotMissing] = useState(false);

  // events 随历史记录到位/切换（c-chat 切会话会换新序列）：按引用变更重新发现 run id，
  // 同一序列不重复扫。用 state 存 run id：发现后触发轮询 effect
  const [runId, setRunId] = useState('');
  const lastEventsRef = useRef<unknown>(undefined);
  useEffect(() => {
    if (events === lastEventsRef.current) return;
    lastEventsRef.current = events;
    setRunId(events ? findCanvasRunId(events) : '');
  }, [events]);

  useEffect(() => {
    // 换运行（切会话/换消息/退化为无 run id）：先清上一运行的快照与过期标记，
    // 避免旧权威态串到新会话的消息上（快照态是整体替换语义，串台即错卡片）
    setSnapshotState(undefined);
    setSnapshotMissing(false);
    if (!runId) return;
    const canvasRunId = runId;
    let cancelled = false;
    let stopped = false;

    const tick = async () => {
      try {
        const { data } = await request.get(
          api.templateFillRunSnapshot(canvasRunId),
        );
        if (cancelled) return;
        const snap = (data?.data || data) as ITemplateFillRunSnapshot;
        if (!snap?.exists) {
          setSnapshotMissing(true);
          return false;
        }
        setSnapshotState(buildStateFromRunSnapshot(snap, canvasRunId));
        // finished：终态快照已整体替换，无需继续轮询；过程态继续 3s 轮询直至终态
        return !snap.finished;
      } catch {
        // 网络抖动：下一轮继续，不中断轮询
        return true;
      }
    };

    (async () => {
      const cont = await tick();
      if (!cont) stopped = true;
    })();
    const timer = setInterval(async () => {
      if (stopped) return;
      const cont = await tick();
      if (!cont) {
        stopped = true;
        clearInterval(timer);
      }
    }, POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [runId]);

  // 快照存在 → 权威整体替换；快照缺失且重放态有未提交挂起卡 → 挂起卡标灰。
  // 纯派生（useMemo），不在渲染期 setState
  return useMemo(() => {
    if (snapshotState) return snapshotState;
    if (snapshotMissing && replayed) {
      const expired = { expired: true } as const;
      return {
        ...replayed,
        pendingConfirm: replayed.pendingConfirm?.submitted
          ? replayed.pendingConfirm
          : replayed.pendingConfirm
            ? { ...replayed.pendingConfirm, ...expired }
            : undefined,
        pendingSelect: replayed.pendingSelect?.submitted
          ? replayed.pendingSelect
          : replayed.pendingSelect
            ? { ...replayed.pendingSelect, ...expired }
            : undefined,
      };
    }
    return replayed;
  }, [snapshotState, snapshotMissing, replayed]);
}
