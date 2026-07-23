/**
 * React hook for crawler task real-time progress.
 *
 * Connects via WebSocket on mount; disconnects on unmount or taskId change.
 * Buffers last 200 log lines in state. Receives historical backfill on
 * connect so the UI shows full context even if opened mid-run.
 */
import {
  connectTaskProgress,
  type TaskDone,
  type TaskLog,
  type TaskProgress,
} from '@/services/crawler-progress';
import { useEffect, useRef, useState } from 'react';

const MAX_LOGS = 200;

export function useCrawlerProgress(taskId: string | null) {
  const [progress, setProgress] = useState<TaskProgress | null>(null);
  const [logs, setLogs] = useState<TaskLog[]>([]);
  const [done, setDone] = useState<TaskDone | null>(null);
  const [connected, setConnected] = useState(false);
  const doneRef = useRef(false);

  useEffect(() => {
    if (!taskId) return;

    // reset state for new task
    setProgress(null);
    setLogs([]);
    setDone(null);
    doneRef.current = false;

    const disconnect = connectTaskProgress(taskId, {
      onConnectionChange: setConnected,
      onHistory: (messages) => {
        // Backfill: replay prior messages in order
        const replayedLogs: TaskLog[] = [];
        let lastProgress: TaskProgress | null = null;
        let lastDone: TaskDone | null = null;
        for (const m of messages) {
          if (m.type === 'log') replayedLogs.push(m);
          else if (m.type === 'progress') lastProgress = m;
          else if (m.type === 'done') lastDone = m;
        }
        if (replayedLogs.length) {
          setLogs((prev) => [...prev, ...replayedLogs].slice(-MAX_LOGS));
        }
        if (lastProgress) setProgress(lastProgress);
        if (lastDone) {
          setDone(lastDone);
          doneRef.current = true;
        }
      },
      onProgress: (p) => {
        if (!doneRef.current) setProgress(p);
      },
      onLog: (l) => {
        if (!doneRef.current) {
          setLogs((prev) => [...prev, l].slice(-MAX_LOGS));
        }
      },
      onDone: (d) => {
        setDone(d);
        doneRef.current = true;
      },
    });

    return disconnect;
  }, [taskId]);

  return { progress, logs, done, connected };
}
