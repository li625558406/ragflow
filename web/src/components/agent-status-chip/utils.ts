import {
  INodeData,
  INodeEvent,
  MessageEventType,
} from '@/hooks/use-send-message';

export interface StepInfo {
  id: string;
  name: string;
  type: string;
  startedAt: number;
  finishedAt?: number;
  error?: string | null;
  elapsedTime?: number;
}

export interface StatusInfo {
  steps: StepInfo[];
  currentStep?: StepInfo;
  completedSteps: StepInfo[];
  totalSteps: number;
  isRunning: boolean;
  isFinished: boolean;
  totalElapsed: number;
}

export function deriveStatus(eventList: INodeEvent[]): StatusInfo {
  const stepMap = new Map<string, StepInfo>();
  let workflowStartedAt = 0;
  let workflowFinishedAt = 0;
  let isRunning = false;

  for (const evt of eventList) {
    const data = evt.data as INodeData;

    if (evt.event === MessageEventType.WorkflowStarted) {
      workflowStartedAt = evt.created_at;
      isRunning = true;
      continue;
    }

    if (evt.event === MessageEventType.WorkflowFinished) {
      workflowFinishedAt = evt.created_at;
      isRunning = false;
      continue;
    }

    if (evt.event === MessageEventType.NodeStarted) {
      stepMap.set(data.component_id, {
        id: data.component_id,
        name: data.component_name,
        type: data.component_type,
        startedAt: evt.created_at,
      });
    }

    if (evt.event === MessageEventType.NodeFinished) {
      const step = stepMap.get(data.component_id);
      if (step) {
        step.finishedAt = evt.created_at;
        step.error = data.error;
        step.elapsedTime = data.elapsed_time;
      } else {
        // Node finished without a corresponding started event (race condition)
        stepMap.set(data.component_id, {
          id: data.component_id,
          name: data.component_name,
          type: data.component_type,
          startedAt: evt.created_at,
          finishedAt: evt.created_at,
          error: data.error,
          elapsedTime: data.elapsed_time,
        });
      }
    }
  }

  const steps = Array.from(stepMap.values());
  const completedSteps = steps.filter((s) => s.finishedAt);
  const currentStep = steps.find((s) => !s.finishedAt);

  const endTime = workflowFinishedAt || Date.now() / 1000;
  const totalElapsed =
    workflowStartedAt > 0 ? Math.max(0, endTime - workflowStartedAt) : 0;

  return {
    steps,
    currentStep,
    completedSteps,
    totalSteps: steps.length,
    isRunning,
    isFinished: !isRunning && steps.length > 0,
    totalElapsed,
  };
}

const NODE_ACTION_MAP: Record<string, { running: string; done: string }> = {
  begin: { running: '正在初始化', done: '初始化完成' },
  retrieval: { running: '正在检索知识库', done: '检索完成' },
  categorize: { running: '正在分析分类', done: '分类完成' },
  switch: { running: '正在判断条件', done: '判断完成' },
  agent: { running: '正在调用 AI', done: 'AI 调用完成' },
  llm: { running: '正在调用 AI', done: 'AI 调用完成' },
  message: { running: '正在生成回答', done: '生成完成' },
  iteration: { running: '正在循环处理', done: '循环完成' },
  loop: { running: '正在循环处理', done: '循环完成' },
  tool: { running: '正在调用工具', done: '工具调用完成' },
  invoke: { running: '正在调用工具', done: '工具调用完成' },
  rewrite: { running: '正在重排序', done: '重排序完成' },
  keyword: { running: '正在提取关键词', done: '提取完成' },
  extractor: { running: '正在提取信息', done: '提取完成' },
  parser: { running: '正在解析文档', done: '解析完成' },
  chunker: { running: '正在分块处理', done: '分块完成' },
  tokenizer: { running: '正在分词处理', done: '分词完成' },
  file: { running: '正在读取文件', done: '文件读取完成' },
  code: { running: '正在执行代码', done: '代码执行完成' },
  textprocessing: { running: '正在处理文本', done: '文本处理完成' },
  titlechunker: { running: '正在标题分块', done: '标题分块完成' },
  tokenchunker: { running: '正在分词分块', done: '分词分块完成' },
  end: { running: '正在收尾', done: '处理完成' },
};

const FALLBACK_ACTION = { running: '正在处理', done: '处理完成' };

export function getNodeAction(nodeType: string): {
  running: string;
  done: string;
} {
  const key = nodeType.toLowerCase().replace(/ /g, '').replace(/_/g, '');
  return NODE_ACTION_MAP[key] || NODE_ACTION_MAP[nodeType] || FALLBACK_ACTION;
}

export function formatDuration(seconds: number): string {
  if (seconds < 0.01) return '';
  if (seconds < 1) return `${(seconds * 1000).toFixed(0)}ms`;
  if (seconds < 60) return `${seconds.toFixed(1)}s`;
  const mins = Math.floor(seconds / 60);
  const secs = Math.floor(seconds % 60);
  return `${mins}m ${secs}s`;
}
