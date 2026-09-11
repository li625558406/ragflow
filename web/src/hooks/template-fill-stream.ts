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
  /** 实时预览：filling 事件逐批累积的已产出字段值 {key: value} */
  values?: Record<string, string>;
}

export interface ITemplateFillState {
  templates: ITemplateFillTemplate[];
  finished?: boolean;
  /** 画布挂起等待人工确认（confirm_pending 事件产出的确认卡片状态） */
  pendingConfirm?: ITemplateFillConfirmPending;
}

// ── 变化字段确认（P2：confirm_pending / confirm_timeout）─────────────────

/** 候选变化字段（一条填写点） */
export interface ITemplateFillCandidate {
  key: string;
  name: string;
  default_value: string;
}

/** 单范本的确认信息：candidates 为全部可决策字段，predicted 为 AI 预判有变化的字段 */
export interface ITemplateFillConfirmTemplate {
  template_id: string;
  name: string;
  candidates: ITemplateFillCandidate[];
  predicted: string[];
}

/** confirm_pending 事件的归约结果（confirmed_state：expired/submitted 由前端标记） */
export interface ITemplateFillConfirmPending {
  task_id: string;
  /** Redis 唤醒 nonce，提交确认时必传（T9 契约） */
  nonce?: string;
  templates: ITemplateFillConfirmTemplate[];
  /** confirm_timeout 且用户未提交 → 置 true，卡片转灰字只读 */
  expired?: boolean;
  /** 用户已提交确认 → 置 true */
  submitted?: boolean;
}

/** use-send-message 的 streamAccRef 与本模块解耦的最小结构约束 */
export interface IStreamAcc {
  templateFill?: ITemplateFillState;
}

export interface ITemplateFillEvent {
  stage:
    | 'selected'
    | 'filling'
    | 'filled'
    | 'failed'
    | 'done'
    | 'cancelled'
    | 'confirm_pending'
    | 'confirm_timeout';
  template_id?: string;
  name?: string;
  slot_count?: number;
  done?: number;
  total?: number;
  /** filling：该批产出的字段值（实时预览逐槽填入） */
  values?: Record<string, string>;
  download?: ITemplateFillDownload;
  error?: string;
  templates?: Array<{ template_id: string; name: string; slot_count?: number }>;
  /** confirm_pending：挂起任务 ID */
  task_id?: string;
  /** confirm_pending：Redis 唤醒 nonce */
  confirm_nonce?: string;
  // confirm_pending：各范本候选变化字段（命名避开了 selected 事件的 templates）
  confirm_templates?: ITemplateFillConfirmTemplate[];
}

/** 事件归约：进度分支浅拷贝换引用（防 React.memo/useEffect 依赖引用漏渲染），与 hook 的增量累积模式一致 */
export function applyTemplateFillEvent(
  acc: IStreamAcc,
  d: ITemplateFillEvent,
): void {
  if (!acc.templateFill) {
    acc.templateFill = { templates: [] };
  }
  let tf = acc.templateFill;
  // finished 终态防御：结束后忽略一切迟到事件；仅 selected（重开新一轮）可穿透并清 finished
  if (tf.finished && d.stage !== 'selected') return;
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
  // 画布挂起等待人工确认（确认发生在填写开始前；不穿透 finished 终态防御，迟到事件照常忽略）
  if (d.stage === 'confirm_pending') {
    tf.pendingConfirm = {
      task_id: d.task_id || '',
      nonce: d.confirm_nonce || '',
      templates: d.confirm_templates || [],
    };
    return;
  }
  if (d.stage === 'confirm_timeout') {
    // 用户已提交确认则不覆盖；否则标记过期（卡片转灰字只读）
    if (tf.pendingConfirm && !tf.pendingConfirm.submitted) {
      tf.pendingConfirm = { ...tf.pendingConfirm, expired: true };
    }
    return;
  }
  if (d.stage === 'done' || d.stage === 'cancelled') {
    tf.finished = true;
    return;
  }
  if (!d.template_id) return;
  // 进度类事件（filling/filled/failed）：浅拷贝换引用，保证下游 memo 组件感知更新（模板数个位数，开销可忽略）
  acc.templateFill = { ...tf, templates: [...tf.templates] };
  tf = acc.templateFill;
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
    // 实时预览：合并该批产出值（换引用保证 memo 感知）
    if (d.values && Object.keys(d.values).length > 0) {
      t.values = { ...t.values, ...d.values };
    }
  } else if (d.stage === 'filled') {
    t.status = 'filled';
    t.download = d.download;
  } else if (d.stage === 'failed') {
    t.status = 'failed';
    t.error = d.error;
  }
}

/** 历史消息恢复：重放消息 data.templateFillEvents（后端随消息持久化的原始事件序列）
 *  还原 templateFill 状态（进度行 + 填入值回看）。无有效模板行时返回 undefined。 */
export function replayTemplateFillEvents(
  events: unknown,
): ITemplateFillState | undefined {
  if (!Array.isArray(events) || events.length === 0) return undefined;
  const acc: IStreamAcc = {};
  for (const ev of events) {
    if (ev && typeof ev === 'object') {
      applyTemplateFillEvent(acc, ev as ITemplateFillEvent);
    }
  }
  return acc.templateFill?.templates?.length ? acc.templateFill : undefined;
}
