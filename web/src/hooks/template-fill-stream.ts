// TemplateFill 范本填写进度：SSE 事件（template_fill_progress）→ 渲染状态的纯归约。
// 类型与归约独立成模块，供 use-send-message.ts（累积）与渲染组件（展示）共用。
export interface ITemplateFillDownload {
  doc_id: string;
  filename: string;
  mime_type: string;
  size?: number;
  url?: string;
  name?: string;
}

export interface ITemplateFillTemplate {
  template_id: string;
  name: string;
  slot_count?: number;
  done?: number;
  total?: number;
  status: 'selected' | 'filling' | 'filled' | 'failed';
  download?: ITemplateFillDownload;
  error?: string;
}

export interface ITemplateFillState {
  templates: ITemplateFillTemplate[];
  finished?: boolean;
}

/** use-send-message 的 streamAccRef 与本模块解耦的最小结构约束 */
export interface IStreamAcc {
  templateFill?: ITemplateFillState;
}

export interface ITemplateFillEvent {
  stage: 'selected' | 'filling' | 'filled' | 'failed' | 'done' | 'cancelled';
  template_id?: string;
  name?: string;
  slot_count?: number;
  done?: number;
  total?: number;
  download?: ITemplateFillDownload;
  error?: string;
  templates?: Array<{ template_id: string; name: string; slot_count?: number }>;
}

/** O(1) 事件归约：就地更新 acc.templateFill（与 hook 的增量累积模式一致） */
export function applyTemplateFillEvent(
  acc: IStreamAcc,
  d: ITemplateFillEvent,
): void {
  if (!acc.templateFill) {
    acc.templateFill = { templates: [] };
  }
  const tf = acc.templateFill;
  if (d.stage === 'selected') {
    tf.templates = (d.templates || []).map((t) => ({
      template_id: t.template_id,
      name: t.name,
      slot_count: t.slot_count,
      status: 'selected' as const,
    }));
    tf.finished = undefined;
    return;
  }
  if (d.stage === 'done' || d.stage === 'cancelled') {
    tf.finished = true;
    return;
  }
  if (!d.template_id) return;
  let t = tf.templates.find((x) => x.template_id === d.template_id);
  if (!t) {
    t = { template_id: d.template_id, name: d.name || '', status: 'filling' };
    tf.templates.push(t);
  }
  if (d.name) t.name = d.name;
  if (d.stage === 'filling') {
    t.status = 'filling';
    t.done = d.done;
    t.total = d.total;
  } else if (d.stage === 'filled') {
    t.status = 'filled';
    t.download = d.download;
  } else if (d.stage === 'failed') {
    t.status = 'failed';
    t.error = d.error;
  }
}
