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

/**
 * Build an ordered markdown string from FanOut lane slots.
 * Chapters are rendered in 0..n-1 order regardless of which lane
 * produced its content first.
 */
function buildFanOutContent(
  lanes: NonNullable<IStreamState['fanOutLanes']>,
): string {
  const parts: string[] = [];
  for (let i = 0; i < lanes.total; i++) {
    const content = lanes.contents[i];
    const isFinished = lanes.finished[i];
    if (!content && !isFinished) continue;
    const label = lanes.labels[i] || `Chapter ${i + 1}`;
    const header = `### 📄 ${label}`;
    parts.push(`${header}\n\n${content}`);
  }
  return parts.join('\n\n---\n\n');
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
  const streamAccRef = useRef<IStreamState>({
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

  const flushStreamState = useCallback(() => {
    flushEventBuffer();
    if (rafRef.current !== null) {
      cancelAnimationFrame(rafRef.current);
      clearTimeout(rafRef.current);
      rafRef.current = null;
    }
    const acc = streamAccRef.current;
    setStreamState({ ...acc });
  }, [flushEventBuffer]);

  const scheduleStreamFlush = useCallback(() => {
    if (document.hidden) {
      // rAF is paused in background tabs.  Use a low-frequency timer
      // (every 500 ms) so the accumulator doesn't grow unbounded while
      // still avoiding a render storm when the user returns.
      if (rafRef.current !== null) return;
      rafRef.current = window.setTimeout(() => {
        rafRef.current = null;
        flushEventBuffer();
        setStreamState({ ...streamAccRef.current });
      }, 500);
      return;
    }

    if (rafRef.current !== null) return;
    rafRef.current = requestAnimationFrame(() => {
      rafRef.current = null;
      flushEventBuffer();
      setStreamState({ ...streamAccRef.current });
    });
  }, [flushEventBuffer]);

  const initializeSseRef = useCallback(() => {
    sseRef.current = new AbortController();
  }, []);

  const resetAnswerList = useCallback(() => {
    if (timer.current) {
      clearTimeout(timer.current);
    }
    setAnswerList([]);
    eventBufferRef.current = [];
    // Also reset the incremental accumulator so the next run starts clean.
    streamAccRef.current = {
      content: '',
      id: '',
      audioBinary: undefined,
      attachment: undefined,
      downloads: [],
      fanOutLanes: undefined,
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

        // Reset accumulator for the new stream.
        streamAccRef.current = {
          content: '',
          id: '',
          audioBinary: undefined,
          attachment: undefined,
          downloads: [],
          fanOutLanes: undefined,
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
          .pipeThrough(new EventSourceParserStream())
          .getReader();

        // eslint-disable-next-line no-constant-condition
        while (true) {
          try {
            const x = await reader?.read();
            if (x) {
              const { done, value } = x;
              if (done) {
                // Stream ended without receiving workflow_finished —
                // the SSE connection was dropped (proxy timeout, network
                // issue, etc.).  Keep the partial content and flag the
                // abnormal termination so the UI can warn the user.
                if (!workflowFinishedRef.current) {
                  setWasAborted(true);
                }
                break;
              }
              try {
                // Some server implementations send "data:[DONE]" as
                // end-of-stream marker — treat it as stream completion.
                if (value?.data === '[DONE]') {
                  setDone(true);
                  break;
                }

                const val = JSON.parse(value?.data || '');

                if (typeof val?.code === 'number' && val.code !== 0) {
                  message.error(val.message);
                }

                // ── Incremental content accumulation (O(1) per event) ──
                // Instead of deferring to findMessageFromList which
                // re-scans the entire event list on every tick, we mirror
                // the same logic here so the content string is always
                // up-to-date in streamAccRef without any recomputation.

                // Capture message_id from the first event of any type
                // (NodeStarted often arrives before the first Message),
                // so downstream consumers like AgentStatusChip can match
                // node events to the streaming answer immediately.
                if (!streamAccRef.current.id && val.message_id) {
                  streamAccRef.current.id = val.message_id;
                }

                // FanOut meta: pre-allocate ordered chapter slots so
                // content streams into the correct position regardless
                // of which lane finishes first.
                // FanOut chapters are progress-only — do NOT write to
                // `content` (that field is reserved for the final Reply
                // output shown in the chat bubble).
                if (val?.event === 'fanout_meta') {
                  const d = val.data;
                  streamAccRef.current.fanOutLanes = {
                    total: d.lane_total,
                    labels: d.lanes.map((l: any) => l.label),
                    contents: new Array(d.lane_total).fill(''),
                    finished: new Array(d.lane_total).fill(false),
                    errored: new Array(d.lane_total).fill(false),
                  };
                  if (!excludeFanOutFromContent) {
                    streamAccRef.current.content = buildFanOutContent(
                      streamAccRef.current.fanOutLanes,
                    );
                  }
                }

                if (val?.event === MessageEventType.Message) {
                  const d = val.data as IMessageData;

                  if (d.audio_binary) {
                    streamAccRef.current.audioBinary = d.audio_binary;
                  }

                  // FanOut streaming: write each chunk into the correct
                  // lane slot for task-panel progress display.
                  // Do NOT update `content` — that field is reserved for
                  // the final Reply output rendered in the chat bubble.
                  // lane_index is ONLY set by the FanOut backend (no other
                  // component sets it), so it's a reliable discriminator.
                  if (d.lane_index !== undefined) {
                    const lanes = streamAccRef.current.fanOutLanes;
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
                      if (!excludeFanOutFromContent) {
                        streamAccRef.current.content =
                          buildFanOutContent(lanes);
                      }
                    }
                    // FanOut content goes into fanOutLanes for task-panel
                    // display.  When excludeFanOutFromContent is false (B-end
                    // agent canvas), it is also mirrored into `content` so the
                    // chat bubble shows per-chapter streaming progress.
                  } else {
                    // Non-FanOut: final Reply (or other non-FanOut LLM) output
                    if (d.start_to_think) {
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

                // Capture whether this event marks logical completion BEFORE
                // pushing into the list so we can exit the read loop.
                const isFinished = val?.event === 'workflow_finished';
                if (isFinished) {
                  workflowFinishedRef.current = true;
                }

                // Push event into buffer — answerList is throttled
                // together with streamState via scheduleStreamFlush /
                // flushStreamState so it doesn't cause O(N²) re-renders
                // while the tab is hidden.
                eventBufferRef.current.push(val);

                // Schedule a throttled flush so the UI picks up the
                // latest accumulator content without rendering on
                // every single SSE tick.
                scheduleStreamFlush();

                // When the canvas signals workflow_finished, all content has
                // been delivered.  Stop reading the SSE stream immediately so
                // that a TCP-level disconnect (timeout / proxy / etc.) cannot
                // keep the front-end in a perpetual loading state.
                if (isFinished) {
                  setDone(true);
                  break;
                }
              } catch (e) {
                console.warn(e);
              }
            }
          } catch (e) {
            if (e instanceof DOMException && e.name === 'AbortError') {
              break;
            }
          }
        }

        // Flush the final accumulator content before marking done.
        flushStreamState();
        setDone(true);
        resetAnswerList();
        return { data: await res, response };
      } catch (e) {
        flushStreamState();
        setDone(true);
        resetAnswerList();

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

  // When the user returns to the tab after it was hidden, flush any
  // pending content AND buffered events immediately instead of waiting for
  // the background timer (which could be up to 500 ms away).
  useEffect(() => {
    const handleVisibilityChange = () => {
      if (!document.hidden && rafRef.current !== null) {
        clearTimeout(rafRef.current);
        cancelAnimationFrame(rafRef.current);
        rafRef.current = null;
        flushEventBuffer();
        setStreamState({ ...streamAccRef.current });
      }
    };
    document.addEventListener('visibilitychange', handleVisibilityChange);
    return () =>
      document.removeEventListener('visibilitychange', handleVisibilityChange);
  }, [flushEventBuffer]);

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
