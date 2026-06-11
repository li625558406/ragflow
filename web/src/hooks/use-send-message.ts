import message from '@/components/ui/message';
import { Authorization } from '@/constants/authorization';
import { IReferenceObject } from '@/interfaces/database/chat';
import { BeginQuery } from '@/pages/agent/interface';
import { getAuthorization } from '@/utils/authorization-util';
import { EventSourceParserStream } from 'eventsource-parser/stream';
import { useCallback, useEffect, useRef, useState } from 'react';

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
  }>;
  /** FanOut multi-lane streaming slots (pre-allocated on fanout_meta). */
  fanOutLanes?: {
    total: number;
    labels: string[];
    contents: string[];
    finished: boolean[];
    errored: boolean[];
  };
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
  const workflowFinishedRef = useRef(false);

  // ── Incremental stream accumulator (ref → O(1) per event) ──
  const streamAccRef = useRef<IStreamState & { fanOutDirty?: boolean }>({
    content: '',
    id: '',
    audioBinary: undefined,
    attachment: undefined,
    downloads: [],
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
      return;
    }

    if (rafRef.current !== null) return;

    // Adaptive debounce based on TOTAL content size, not delta.
    // When content is still light (< 30KB), render at ~16fps (60ms).
    // As it grows, back off to ~10fps (100ms) to keep react-markdown
    // parsing + React reconciliation from blocking the main thread.
    const contentLen = streamAccRef.current.content?.length || 0;
    const interval = contentLen > 30000 ? 100 : 60;

    rafRef.current = window.setTimeout(() => {
      rafRef.current = null;
      flushNextFanOutLane();
      flushEventBuffer();
      setStreamState({ ...streamAccRef.current });
    }, interval);
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
    };
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
    ): Promise<{ response: Response; data: ResponseType } | undefined> => {
      initializeSseRef();
      try {
        setDone(false);
        setWasAborted(false);
        workflowFinishedRef.current = false;
        eventBufferRef.current = [];

        streamAccRef.current = {
          content: '',
          id: '',
          audioBinary: undefined,
          attachment: undefined,
          downloads: [],
          fanOutLanes: undefined,
          fanOutDirty: false,
        };

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
          });

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
                  break;
                }

                const val = JSON.parse(value?.data || '');

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

                const isFinished = val?.event === 'workflow_finished';
                if (isFinished) {
                  workflowFinishedRef.current = true;
                }

                eventBufferRef.current.push(val);
                scheduleStreamFlush();

                if (isFinished) {
                  setDone(true);
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
              break;
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
        setDone(true);
        resetAnswerList();
        return { data: await res, response };
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
    sseRef.current?.abort();
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
  };
};
