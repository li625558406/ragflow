import message from '@/components/ui/message';
import { Authorization } from '@/constants/authorization';
import { IReferenceObject } from '@/interfaces/database/chat';
import { BeginQuery } from '@/pages/agent/interface';
import { getAuthorization } from '@/utils/authorization-util';
import { EventSourceParserStream } from 'eventsource-parser/stream';
import { useCallback, useEffect, useRef, useState } from 'react';
import {
  applyTemplateFillEvent,
  ITemplateFillState,
} from './template-fill-stream';

export enum MessageEventType {
  WorkflowStarted = 'workflow_started',
  NodeStarted = 'node_started',
  NodeFinished = 'node_finished',
  Message = 'message',
  MessageEnd = 'message_end',
  WorkflowFinished = 'workflow_finished',
  UserInputs = 'user_inputs',
  NodeLogs = 'node_logs',
}

export interface IAnswerEvent<T> {
  event: MessageEventType;
  message_id: string;
  session_id: string;
  created_at: number;
  task_id: string;
  data: T;
}

export interface IToolUsage {
  tool_name: string;
  elapsed_time?: number;
  status?: 'running' | 'done';
}

export interface INodeData {
  inputs: Record<string, any>;
  outputs: Record<string, any>;
  component_id: string;
  component_name: string;
  component_type: string;
  error: null | string;
  elapsed_time: number;
  created_at: number;
  thoughts: string;
  tool_usage?: IToolUsage[] | null;
}

export interface IInputData {
  content: string;
  inputs: Record<string, BeginQuery>;
  tips: string;
}
export interface IAttachment {
  doc_id: string;
  format: string;
  file_name: string;
}
export interface IMessageData {
  content: string;
  audio_binary: string;
  outputs: any;
  start_to_think?: boolean;
  end_to_think?: boolean;
  lane_index?: number;
  lane_label?: string;
  lane_total?: number;
  finished?: boolean;
  error?: boolean;
}

export interface IMessageEndData {
  reference: IReferenceObject;
}

export interface ILogData extends INodeData {
  logs: {
    name: string;
    result: string;
    args: {
      query: string;
      topic: string;
    };
  };
}

export type INodeEvent = IAnswerEvent<INodeData>;

export type IMessageEvent = IAnswerEvent<IMessageData>;

export type IMessageEndEvent = IAnswerEvent<IMessageEndData>;

export type IInputEvent = IAnswerEvent<IInputData>;

export type ILogEvent = IAnswerEvent<ILogData>;

export type IChatEvent = INodeEvent | IMessageEvent | IMessageEndEvent;

export type IEventList = Array<IChatEvent>;

/**
 * Incrementally-accumulated streaming state.
 *
 * Unlike {@link answerList} which stores every raw event and requires a
 * full O(n) recomputation on every tick, this object is updated O(1) per
 * SSE event.  A separate requestAnimationFrame throttle copies it into
 * React state at most once per frame, so rendering never outpaces the
 * browser's paint cycle.
 */
export interface IStreamState {
  content: string;
  id: string;
  audioBinary?: string;
  attachment?: IAttachment;
  downloads?: Array<{
    doc_id: string;
    filename: string;
    mime_type: string;
    size?: number;
    /** 后端 Message._extract_downloads 注入：/api/v1/agents/download 下载地址 */
    url?: string;
    /** 展示名兜底（后端从 filename 派生） */
    name?: string;
  }>;
  /** FanOut multi-lane streaming slots (pre-allocated on fanout_meta). */
  fanOutLanes?: {
    total: number;
    labels: string[];
    contents: string[];
    finished: boolean[];
    errored: boolean[];
  };
  /** TemplateFill 范本填写进度（template_fill_progress 事件累积） */
  templateFill?: ITemplateFillState;
}

// ── Debug logging for SSE stream diagnosis ──
// Toggle this to trace event flow from SSE reader → streamState → UI.
// Logs are prefixed with [SSE] for easy filtering in DevTools console.
const DEBUG_SSE = false;

let _debugSeq = 0;
function _debugId() {
  return ++_debugSeq;
}

export const useSendMessageBySSE = (
  url: string,
  opts?: { excludeFanOutFromContent?: boolean },
) => {
  const excludeFanOutFromContent = opts?.excludeFanOutFromContent ?? true;
  const [answerList, setAnswerList] = useState<IEventList>([]);
  const [done, setDone] = useState(true);
  const [wasAborted, setWasAborted] = useState(false);
  const timer = useRef<any>();
  const sseRef = useRef<AbortController>();
  // Persistent ref for structured output (annotations) — survives answerList reset
  const structuredOutputRef = useRef<any>(null);
  const workflowFinishedRef = useRef(false);

  // ── Incremental stream accumulator (ref → O(1) per event) ──
  const streamAccRef = useRef<IStreamState & { fanOutDirty?: boolean }>({
    content: '',
    id: '',
    audioBinary: undefined,
    attachment: undefined,
    downloads: [],
    templateFill: undefined,
  });

  // ── RAF-throttled rendering state (updated at most 60 fps) ──
  const [streamState, setStreamState] = useState<IStreamState>({
    content: '',
    id: '',
  });
  const rafRef = useRef<number | null>(null);

  // Buffer SSE events so answerList is also throttled (not updated per-event).
  // Without this, setAnswerList spread-copying on every event creates O(N²)
  // pressure, and React re-renders keep recomputing useMemo(…, [answerList])
  // even in background tabs — causing a freeze when the user returns.
  const eventBufferRef = useRef<any[]>([]);
  // 服务端取消：SSE envelope 每帧带 task_id（canvas.run decorate），停止时写取消键
  const taskIdRef = useRef<string | null>(null);

  const flushEventBuffer = useCallback(() => {
    const batch = eventBufferRef.current;
    if (batch.length === 0) return;
    eventBufferRef.current = [];
    setAnswerList((list) => {
      const nextList = [...list];
      nextList.push(...batch);
      return nextList;
    });
  }, []);

  // ── Incremental FanOut content flush ──
  // Instead of rebuilding ALL chapters on every flush (O(total_chars)),
  // we append only the current in-order lane's new content (O(new_chars)).
  // Chapters stream into the chat bubble sequentially as they generate.
  const flushNextFanOutLane = useCallback(() => {
    const acc = streamAccRef.current;
    const lanes = acc.fanOutLanes as any;
    if (!lanes || excludeFanOutFromContent) return;

    const total = lanes.total as number;
    if (lanes._nextFlush === undefined) lanes._nextFlush = 0;
    if (!lanes._laneLen) lanes._laneLen = new Array(total).fill(0);

    // Only flush the current in-order lane, preserving chapter sequence.
    const li = lanes._nextFlush;
    if (li >= total) return;

    const full = (lanes.contents[li] as string) || '';
    const prev = (lanes._laneLen[li] as number) || 0;
    const delta = full.slice(prev);

    if (delta) {
      // First content for this lane → add chapter header
      if (prev === 0) {
        const label = lanes.labels[li] || `Chapter ${li + 1}`;
        acc.content += `### 📄 ${label}\n\n`;
      }
      acc.content += delta;
      lanes._laneLen[li] = full.length;
    }

    // Lane finished → advance to next chapter
    if (lanes.finished[li]) {
      acc.content += '\n\n---\n\n';
      lanes._nextFlush = li + 1;
    }
  }, [excludeFanOutFromContent]);

  const flushStreamState = useCallback(() => {
    flushNextFanOutLane();
    flushEventBuffer();
    if (rafRef.current !== null) {
      cancelAnimationFrame(rafRef.current);
      clearTimeout(rafRef.current);
      rafRef.current = null;
    }
    const acc = streamAccRef.current;
    const contentLen = acc.content?.length || 0;
    const laneLens =
      acc.fanOutLanes?.contents?.map((c: string) => c.length) || [];
    if (DEBUG_SSE && contentLen > 0) {
      console.log(
        `%c[SSE] %c▷ FLUSH %ccontent=${contentLen} %clanes=[${laneLens}]`,
        'color:#4fc3f7;font-weight:bold',
        'color:#ce93d8',
        'color:#a5d6a7',
        'color:inherit',
      );
    }
    setStreamState({ ...acc });
  }, [flushEventBuffer, flushNextFanOutLane]);

  const scheduleStreamFlush = useCallback(() => {
    if (document.hidden) {
      // Events accumulate in streamAccRef/eventBufferRef (refs, not state).
      // Skip setState to prevent unbounded React updates in background tabs.
      // The visibilitychange handler flushes everything when tab becomes visible.
      return;
    }

    if (rafRef.current !== null) return;

    // Tiered debounce — keeps streaming smooth for short responses while
    // backing off react-markdown rendering pressure for large documents:
    //   < 10KB  → requestAnimationFrame (~16 fps, smooth incremental feel)
    //   10-30KB → 40 ms (~25 fps)
    //   > 30KB  → 100 ms (~10 fps, keeps UI responsive)
    const contentLen = streamAccRef.current.content?.length || 0;

    if (contentLen <= 10000) {
      rafRef.current = requestAnimationFrame(() => {
        rafRef.current = null;
        flushNextFanOutLane();
        flushEventBuffer();
        setStreamState({ ...streamAccRef.current });
      });
    } else {
      const interval = contentLen > 30000 ? 100 : 40;
      rafRef.current = window.setTimeout(() => {
        rafRef.current = null;
        flushNextFanOutLane();
        flushEventBuffer();
        setStreamState({ ...streamAccRef.current });
      }, interval);
    }
  }, [flushEventBuffer, flushNextFanOutLane]);

  const initializeSseRef = useCallback(() => {
    sseRef.current = new AbortController();
  }, []);

  const resetAnswerList = useCallback(() => {
    if (timer.current) {
      clearTimeout(timer.current);
    }
    setAnswerList([]);
    eventBufferRef.current = [];
    streamAccRef.current = {
      content: '',
      id: '',
      audioBinary: undefined,
      attachment: undefined,
      downloads: [],
      fanOutLanes: undefined,
      fanOutDirty: false,
      templateFill: undefined,
    };
    // Must also clear the React state so that consumers (e.g. c-chat) that
    // read streamState.content directly don't pick up stale text from a
    // previous conversation when `done` toggles from true → false.
    setStreamState({ content: '', id: '' });
    if (rafRef.current !== null) {
      cancelAnimationFrame(rafRef.current);
      clearTimeout(rafRef.current);
      rafRef.current = null;
    }
    timer.current = setTimeout(() => {
      setAnswerList([]);
      clearTimeout(timer.current);
    }, 1000);
  }, []);

  const send = useCallback(
    async (
      body: any,
      controller?: AbortController,
    ): Promise<
      | {
          response: Response;
          data: ResponseType;
          content?: string;
        }
      | undefined
    > => {
      // Clear any pending resetAnswerList timer from a previous abort
      // to prevent it from clearing answerList during the new stream.
      if (timer.current) {
        clearTimeout(timer.current);
        timer.current = null;
      }
      structuredOutputRef.current = null;
      initializeSseRef();
      try {
        setDone(false);
        setWasAborted(false);
        workflowFinishedRef.current = false;
        eventBufferRef.current = [];
        taskIdRef.current = null;

        streamAccRef.current = {
          content: '',
          id: '',
          audioBinary: undefined,
          attachment: undefined,
          downloads: [],
          fanOutLanes: undefined,
          fanOutDirty: false,
          templateFill: undefined,
        };
        // Reset streamState so stale content from a previous abort
        // doesn't leak into the c-chat effect when done toggles to false.
        setStreamState({ content: '', id: '' });

        const response = await fetch(url, {
          method: 'POST',
          headers: {
            [Authorization]: getAuthorization(),
            'Content-Type': 'application/json',
          },
          body: JSON.stringify(body),
          signal: controller?.signal || sseRef.current?.signal,
        });
        const res = response
          .clone()
          .text()
          .then((text) => {
            try {
              return JSON.parse(text);
            } catch {
              return { code: 0 };
            }
          })
          .catch(() => ({ code: 0 })); // AbortError: body stream aborted

        const reader = response?.body
          ?.pipeThrough(new TextDecoderStream())
          ?.pipeThrough(new EventSourceParserStream())
          .getReader();

        // ── Debug: per-stream event counters ──
        const _dbg = DEBUG_SSE
          ? {
              seq: _debugId(),
              count: 0,
              fanoutCount: 0,
              replyCount: 0,
              byType: {} as Record<string, number>,
              firstContentAt: 0,
            }
          : null;
        if (_dbg) {
          console.log(
            `%c[SSE #${_dbg.seq}] %c▷ OPEN %c${url.split('/').reverse()[0]}`,
            'color:#4fc3f7;font-weight:bold',
            'color:#66bb6a',
            'color:inherit',
          );
        }

        // eslint-disable-next-line no-constant-condition
        while (true) {
          try {
            const x = await reader?.read();
            if (x) {
              const { done, value } = x;
              if (done) {
                if (!workflowFinishedRef.current) {
                  setWasAborted(true);
                }
                if (_dbg) {
                  console.warn(
                    `%c[SSE #${_dbg.seq}] %c◁ READER_DONE %cworkflowFinished=${workflowFinishedRef.current} %ctotalEvents=${_dbg.count} %cbyType=%o`,
                    'color:#4fc3f7;font-weight:bold',
                    'color:#ef5350',
                    'color:#ffa726',
                    'color:inherit',
                    'color:inherit',
                    _dbg.byType,
                  );
                }
                break;
              }
              try {
                if (value?.data === '[DONE]') {
                  if (_dbg) {
                    console.log(
                      `%c[SSE #${_dbg.seq}] %c◁ [DONE] %ctotalEvents=${_dbg.count}`,
                      'color:#4fc3f7;font-weight:bold',
                      'color:#66bb6a',
                      'color:inherit',
                    );
                  }
                  setDone(true);
                  // 流正常结束，清空 task_id：卸载/防御性 stop 不再对已结束任务发 cancel
                  taskIdRef.current = null;
                  break;
                }

                const val = JSON.parse(value?.data || '');

                if (typeof val?.task_id === 'string' && val.task_id) {
                  taskIdRef.current = val.task_id;
                }

                if (typeof val?.code === 'number' && val.code !== 0) {
                  if (_dbg) {
                    console.warn(
                      `%c[SSE #${_dbg.seq}] %c◁ ERROR_CODE=${val.code} %c${val.message}`,
                      'color:#4fc3f7;font-weight:bold',
                      'color:#ef5350',
                      'color:inherit',
                    );
                  }
                  message.error(val.message);
                }

                // ── Debug: count and periodic log ──
                if (_dbg) {
                  _dbg.count++;
                  const etype = val?.event || 'unknown';
                  _dbg.byType[etype] = (_dbg.byType[etype] || 0) + 1;
                  if (etype === 'message') {
                    const d = val.data as IMessageData;
                    if (d.lane_index !== undefined) {
                      _dbg.fanoutCount++;
                    } else {
                      _dbg.replyCount++;
                      if (!_dbg.firstContentAt && d.content) {
                        _dbg.firstContentAt = _dbg.count;
                      }
                    }
                  }
                  // Log first 5 events, then every 500
                  if (_dbg.count <= 5 || _dbg.count % 500 === 0) {
                    console.log(
                      `%c[SSE #${_dbg.seq}] %c#${_dbg.count} %c${etype} %ccontent_len=${streamAccRef.current.content.length} %c(fanout=${_dbg.fanoutCount} reply=${_dbg.replyCount})`,
                      'color:#4fc3f7;font-weight:bold',
                      'color:#ffa726',
                      'color:#fff176',
                      'color:#a5d6a7',
                      'color:inherit',
                    );
                  }
                }

                // ── Content accumulation ──
                if (!streamAccRef.current.id && val.message_id) {
                  streamAccRef.current.id = val.message_id;
                }

                if (val?.event === 'fanout_meta') {
                  const d = val.data;
                  streamAccRef.current.fanOutLanes = {
                    total: d.lane_total,
                    labels: d.lanes.map((l: any) => l.label),
                    contents: new Array(d.lane_total).fill(''),
                    finished: new Array(d.lane_total).fill(false),
                    errored: new Array(d.lane_total).fill(false),
                  };
                  if (_dbg) {
                    console.log(
                      `%c[SSE #${_dbg.seq}] %c◁ FANOUT_META %clanes=${d.lane_total} %clabels=%o`,
                      'color:#4fc3f7;font-weight:bold',
                      'color:#ff8a65',
                      'color:#ffa726',
                      'color:inherit',
                      d.lanes.map((l: any) => l.label),
                    );
                  }
                }

                if (val?.event === 'template_fill_progress') {
                  applyTemplateFillEvent(streamAccRef.current, val.data);
                }

                if (val?.event === MessageEventType.Message) {
                  const d = val.data as IMessageData;

                  if (d.audio_binary) {
                    streamAccRef.current.audioBinary = d.audio_binary;
                  }

                  if (d.lane_index !== undefined) {
                    // Auto-create lanes on first FanOut message when the API
                    // doesn't emit a separate fanout_meta event (e.g. C-end chat).
                    let lanes = streamAccRef.current.fanOutLanes;
                    if (!lanes && d.lane_total !== undefined) {
                      lanes = {
                        total: d.lane_total,
                        labels: new Array(d.lane_total).fill(''),
                        contents: new Array(d.lane_total).fill(''),
                        finished: new Array(d.lane_total).fill(false),
                        errored: new Array(d.lane_total).fill(false),
                      };
                      streamAccRef.current.fanOutLanes = lanes;
                    }
                    if (lanes) {
                      const li = d.lane_index;
                      if (d.finished) {
                        lanes.finished[li] = true;
                        if (d.error) {
                          lanes.errored[li] = true;
                          lanes.contents[li] += d.content || '';
                        }
                      } else {
                        lanes.contents[li] += d.content || '';
                      }
                    }
                  } else {
                    // When FanOut lanes are streaming into content (excludeFanOutFromContent=false),
                    // skip non-FanOut messages to prevent Reply from duplicating chapter output.
                    if (
                      !excludeFanOutFromContent &&
                      streamAccRef.current.fanOutLanes
                    ) {
                      // Content is handled by flushNextFanOutLane — skip.
                    } else if (d.start_to_think) {
                      streamAccRef.current.content += '<think>';
                    } else if (d.end_to_think) {
                      streamAccRef.current.content += '</think>';
                    } else {
                      streamAccRef.current.content += d.content || '';
                    }
                  }
                } else if (val?.event === MessageEventType.WorkflowFinished) {
                  const outputs = val.data?.outputs || {};
                  if (outputs.attachment) {
                    streamAccRef.current.attachment = outputs.attachment;
                  }
                  if (outputs.downloads) {
                    streamAccRef.current.downloads = outputs.downloads;
                  }
                }

                // Intercept structured output directly from SSE — survives answerList reset
                if (val?.event === 'node_finished') {
                  const out = val?.data?.outputs || val?.outputs || {};
                  if (out.structured) {
                    structuredOutputRef.current = out.structured;
                  }
                }

                const isFinished = val?.event === 'workflow_finished';
                if (isFinished) {
                  workflowFinishedRef.current = true;
                }

                eventBufferRef.current.push(val);
                scheduleStreamFlush();

                if (isFinished) {
                  setDone(true);
                  // 流正常结束，清空 task_id：卸载/防御性 stop 不再对已结束任务发 cancel
                  taskIdRef.current = null;
                  break;
                }
              } catch (e) {
                if (_dbg) {
                  console.warn(
                    `%c[SSE #${_dbg.seq}] %c◁ PARSE_ERROR %c${e}`,
                    'color:#4fc3f7;font-weight:bold',
                    'color:#ef5350',
                    'color:inherit',
                  );
                }
                console.warn(e);
              }
            }
          } catch (e) {
            if (e instanceof DOMException && e.name === 'AbortError') {
              throw e; // re-throw so outer catch handles it (skips resetAnswerList)
            }
          }
        }

        // ── Debug: final summary ──
        if (_dbg) {
          const lanes = streamAccRef.current.fanOutLanes;
          console.log(
            `%c[SSE #${_dbg.seq}] %c◁ SUMMARY %cevents=%c${_dbg.count} %cfanout=%c${_dbg.fanoutCount} %creply=%c${_dbg.replyCount} %ccontent=${streamAccRef.current.content.length} %clanes=${lanes ? `${lanes.total}ch total=${lanes.contents.reduce((s: number, c: string) => s + c.length, 0)}` : 'none'}`,
            'color:#4fc3f7;font-weight:bold',
            'color:#66bb6a',
            'color:inherit',
            'color:#ffa726',
            'color:inherit',
            'color:#ffa726',
            'color:inherit',
            'color:#ffa726',
            'color:inherit',
            'color:#ffa726',
          );
        }

        flushStreamState();
        // resetAnswerList 会同步清空 streamAccRef/streamState（React 批处理下
        // 消费者看不到中间态）。尾包 message 与 [DONE] 同帧到达时 RAF flush
        // 一次都没跑过，streamState.content 直接以空态落地——必须在 reset 前
        // 捕获完整累积回复并随返回值带给调用方（flow AI 面板自动保存依赖它）。
        const finalContent = streamAccRef.current.content || '';
        setDone(true);
        resetAnswerList();
        return { data: await res, response, content: finalContent };
      } catch (e) {
        // Aborted: flush remaining content but do NOT clear the accumulator.
        // The user should see whatever was rendered before stopping.
        flushStreamState();
        setDone(true);
        console.warn(e);
      }
    },
    [
      initializeSseRef,
      url,
      resetAnswerList,
      scheduleStreamFlush,
      flushStreamState,
    ],
  );

  useEffect(() => {
    const handleVisibilityChange = () => {
      if (document.hidden) return;

      if (rafRef.current !== null) {
        clearTimeout(rafRef.current);
        cancelAnimationFrame(rafRef.current);
        rafRef.current = null;
      }

      flushNextFanOutLane();
      flushEventBuffer();
      setStreamState({ ...streamAccRef.current });
    };
    document.addEventListener('visibilitychange', handleVisibilityChange);
    return () =>
      document.removeEventListener('visibilitychange', handleVisibilityChange);
  }, [flushEventBuffer, flushNextFanOutLane]);

  const stopOutputMessage = useCallback(() => {
    const taskId = taskIdRef.current;
    if (taskId) {
      // fire-and-forget：服务端取消（幂等），失败不阻断本地停止
      fetch(`/api/v1/agents/tasks/${taskId}/cancel`, {
        method: 'POST',
        headers: {
          [Authorization]: getAuthorization(),
        },
      }).catch((e) => console.warn('cancel task failed', e));
    }
    sseRef.current?.abort();
  }, []);

  // 确认卡片提交成功后的流式态回写：把 submitted 标记写入累积器并立即刷新渲染态，
  // 使归约器的 confirm_timeout 守卫（!submitted）真正生效 —— 用户已提交时，
  // 迟到/并发的 confirm_timeout 不再把确认卡片置 expired（消除超时/提交竞态的 UI 假象）
  const markConfirmSubmitted = useCallback(() => {
    const acc = streamAccRef.current;
    const pc = acc.templateFill?.pendingConfirm;
    if (!pc || pc.submitted) return;
    // 换引用更新（与归约器浅拷贝约定一致），让依赖 streamState.templateFill 的下游感知更新
    acc.templateFill = {
      ...acc.templateFill!,
      pendingConfirm: { ...pc, submitted: true },
    };
    setStreamState({ ...acc });
  }, []);

  return {
    send,
    answerList,
    streamState,
    done,
    wasAborted,
    setDone,
    resetAnswerList,
    stopOutputMessage,
    structuredOutputRef,
    markConfirmSubmitted,
  };
};
