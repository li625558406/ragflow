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

/** 未填充字段（成稿留空的填写点，任务终态一次性下发） */
export interface ITemplateFillUnfilled {
  key: string;
  name: string;
  required: boolean;
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
  /** 后台任务锚点（断连重连轮询用）；旧消息无此字段 → 轮询不触发 */
  task_id?: string;
  /** 成稿留空的填写点（filled 事件/progress 终态派生；历史恢复经轮询端点合并） */
  unfilled?: ITemplateFillUnfilled[];
}

export interface ITemplateFillState {
  templates: ITemplateFillTemplate[];
  finished?: boolean;
  /** 画布挂起等待人工确认（confirm_pending 事件产出的确认卡片状态） */
  pendingConfirm?: ITemplateFillConfirmPending;
  /** 多范本命中：挂起等待用户勾选范本（select_pending 事件产出） */
  pendingSelect?: ITemplateFillSelectPending;
}

// ── 变化字段确认（P2：confirm_pending / confirm_timeout）─────────────────

/** 候选变化字段（一条填写点） */
export interface ITemplateFillCandidate {
  key: string;
  name: string;
  default_value: string;
  /** 增量模式专用：LLM 从用户原话抽出的直填值（如「approval_doc 填写成 港里」
   *  → {approval_doc: 港里}），前端确认卡输入框用它预填；全量/无此字段
   *  时为 undefined（不预填，行为同旧契约）。提交后端 confirm 端点时
   *  values_raw 用户改了走用户的、没改等于空 → 后端 ov 分支保留 fallback_values。 */
  direct_value?: string;
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

// ── 多范本选择确认（智能折中：select_pending / select_timeout）─────────────

/** select_pending 候选范本（AI 初选集合，用户至少勾选 1 个） */
export interface ITemplateFillSelectCandidate {
  template_id: string;
  name: string;
  slot_count?: number;
  description?: string;
}

/** select_pending 事件的归约结果（expired/submitted 由前端标记，语义同 ConfirmPending） */
export interface ITemplateFillSelectPending {
  task_id: string;
  /** Redis 唤醒 nonce，提交选择时必传 */
  select_nonce?: string;
  select_candidates: ITemplateFillSelectCandidate[];
  /** AI 初选的 template_id 列表（确认卡初始勾选） */
  ai_selected: string[];
  /** select_timeout 且用户未提交 → 置 true，卡片转灰字只读 */
  expired?: boolean;
  /** 用户已提交选择 → 置 true */
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
    | 'confirm_timeout'
    | 'select_pending'
    | 'select_timeout'
    /** 等待期 SSE 保活心跳（选择/字段确认挂起期每 30s 一条，无 template_id，
     *  reducer 按「无 template_id 直接忽略」语义丢弃；仅用于保持连接与增量落库） */
    | 'heartbeat';
  template_id?: string;
  name?: string;
  slot_count?: number;
  done?: number;
  total?: number;
  /** filling：该批产出的字段值（实时预览逐槽填入）。
   *  filled：终态全量产值（覆盖 filling 的部分值，对齐 row.values.render，
   *  供增量/noop 场景预览 baseline+default 兜回的全部字段）。 */
  values?: Record<string, string>;
  /** filled：成稿留空的填写点汇总（终态一次性整体替换） */
  unfilled?: ITemplateFillUnfilled[];
  download?: ITemplateFillDownload;
  error?: string;
  templates?: Array<{ template_id: string; name: string; slot_count?: number }>;
  /** confirm_pending：挂起任务 ID；filling/filled/failed：后台任务锚点（断连重连轮询用） */
  task_id?: string;
  /** confirm_pending：Redis 唤醒 nonce */
  confirm_nonce?: string;
  // confirm_pending：各范本候选变化字段（命名避开了 selected 事件的 templates）
  confirm_templates?: ITemplateFillConfirmTemplate[];
  /** select_pending：挂起任务 ID 同 task_id；Redis 唤醒 nonce；候选与 AI 初选 */
  select_nonce?: string;
  select_candidates?: ITemplateFillSelectCandidate[];
  ai_selected?: string[];
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
    // 勾选后的二次 selected（整体替换）：选择卡完成使命，清除挂起态
    if (tf.pendingSelect && !tf.pendingSelect.submitted) {
      tf.pendingSelect = undefined;
    }
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
  // 多范本选择确认：语义与字段确认对齐（先于 filling，迟到事件不穿透 finished）
  if (d.stage === 'select_pending') {
    tf.pendingSelect = {
      task_id: d.task_id || '',
      select_nonce: d.select_nonce || '',
      select_candidates: d.select_candidates || [],
      ai_selected: d.ai_selected || [],
    };
    return;
  }
  if (d.stage === 'select_timeout') {
    // 用户已提交选择则不覆盖；否则标记过期（卡片转灰字只读）
    if (tf.pendingSelect && !tf.pendingSelect.submitted) {
      tf.pendingSelect = { ...tf.pendingSelect, expired: true };
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
  if (d.task_id) t.task_id = d.task_id;
  if (d.stage === 'filling') {
    t.status = 'filling';
    // done/total 缺省（渲染前产值补推事件）时保留既有进度，进度口径仍以 LLM 批次为准
    if (d.done != null) t.done = d.done;
    if (d.total != null) t.total = d.total;
    // 实时预览：合并该批产出值（换引用保证 memo 感知）
    if (d.values && Object.keys(d.values).length > 0) {
      t.values = { ...t.values, ...d.values };
    }
  } else if (d.stage === 'filled') {
    t.status = 'filled';
    t.download = d.download;
    // 终态一次性数据整体替换（后到者胜幂等）；缺省不清旧值。
    // 增量/noop 场景下 filling 事件只带 LLM 实时产值（patch 项或 0 项），
    // baseline+default 兜回的字段不在 filling 事件里，但都在 row.values.render
    // ——这里必须用 filled.values 整体覆盖，否则预览只能看到 patch 项的填入，
    // 其余字段被还原为虚线槽位，用户视觉等同「没填」。
    if (d.values && Object.keys(d.values).length > 0) {
      t.values = d.values;
    }
    if (d.unfilled) t.unfilled = d.unfilled;
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

// ── 快照化恢复（设计 2026-09-16）：事件序列降级为运行 id 发现 + 旧数据兜底，
// 刷新恢复的权威态来自后端运行快照端点（/template/fill/fill-run/<id>/snapshot）。

/** 运行快照响应（后端 build_run_snapshot_payload 组装，exists=false 表示键过期） */
export interface ITemplateFillRunSnapshot {
  exists: boolean;
  stage?: string;
  finished?: boolean;
  templates?: Array<{
    template_id: string;
    name: string;
    slot_count?: number;
    task_id?: string;
    status: 'selected' | 'filling' | 'filled' | 'failed';
    done?: number | null;
    total?: number | null;
    values?: Record<string, string> | null;
    unfilled?: ITemplateFillUnfilled[] | null;
    download?: ITemplateFillDownload | null;
    error?: string;
  }>;
  pending?: {
    type: 'select' | 'confirm';
    nonce: string;
    select_candidates?: ITemplateFillSelectCandidate[];
    ai_selected?: string[];
    confirm_templates?: ITemplateFillConfirmTemplate[];
  } | null;
}

/** 从原始事件序列提取画布运行 id（canvas task_id）：只出现在 confirm_pending /
 *  select_pending / heartbeat 事件的 task_id 字段（filling 等进度事件的 task_id
 *  是 fill task id，另一个 id 空间，不可混用）。截断挽救只保头部，而这些事件
 *  必在序列头部，提取健壮。无确认/选择轮次无此 id → 返回空串（退化为旧兜底）。 */
export function findCanvasRunId(events: unknown): string {
  if (!Array.isArray(events)) return '';
  for (const ev of events) {
    if (!ev || typeof ev !== 'object') continue;
    const e = ev as { stage?: string; task_id?: string };
    if (
      (e.stage === 'confirm_pending' ||
        e.stage === 'select_pending' ||
        e.stage === 'heartbeat') &&
      typeof e.task_id === 'string' &&
      e.task_id
    ) {
      return e.task_id;
    }
  }
  return '';
}

/** 运行快照 → ITemplateFillState（权威整体替换重放态）。挂起卡带 nonce 原样
 *  映射（是否可交互由快照存在性本身保证——节点收口即删/终态短 TTL）。 */
export function buildStateFromRunSnapshot(
  snap: ITemplateFillRunSnapshot,
  canvasRunId: string,
): ITemplateFillState | undefined {
  if (!snap?.exists) return undefined;
  const state: ITemplateFillState = {
    templates: (snap.templates || []).map((t) => ({
      template_id: t.template_id,
      name: t.name || '',
      slot_count: t.slot_count,
      task_id: t.task_id || undefined,
      status: t.status,
      done: t.done ?? undefined,
      total: t.total ?? undefined,
      values: t.values || undefined,
      unfilled: t.unfilled || undefined,
      download: t.download || undefined,
      error: t.error || undefined,
    })),
    finished: snap.finished ? true : undefined,
  };
  if (snap.pending?.type === 'select') {
    state.pendingSelect = {
      task_id: canvasRunId,
      select_nonce: snap.pending.nonce,
      select_candidates: snap.pending.select_candidates || [],
      ai_selected: snap.pending.ai_selected || [],
    };
  } else if (snap.pending?.type === 'confirm') {
    state.pendingConfirm = {
      task_id: canvasRunId,
      nonce: snap.pending.nonce,
      templates: snap.pending.confirm_templates || [],
    };
  }
  return state;
}

/** 持久化事件序列解析：合法 JSON 数组直接返回；截断损坏（旧 TEXT 64KB 落库上限）
 *  时按括号深度扫描挽救——截到最后一个「完整闭合的事件对象」补 ] 重试，只丢
 *  尾部残缺事件，前面的进度/产值/成稿事件全部保住。无法挽救返回 undefined。 */
export function parseTemplateFillEvents(raw: unknown): unknown[] | undefined {
  // 序列化层可能已把该列解析成数组直传（旧 parseAndReplay 契约），直通
  if (Array.isArray(raw)) return raw.length > 0 ? raw : undefined;
  if (typeof raw !== 'string' || !raw) return undefined;
  try {
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) && parsed.length > 0 ? parsed : undefined;
  } catch {
    // fall through to salvage
  }
  // 引号/转义状态跟踪 + 括号深度计数：记录最后一个 depth 由 2→1 的 '}' 位置
  // （即一个事件元素刚闭合处）。字符串值内部的 } [ { 均不计。
  let inStr = false;
  let esc = false;
  let depth = 0;
  let lastElementEnd = -1;
  for (let i = 0; i < raw.length; i++) {
    const ch = raw[i];
    if (inStr) {
      if (esc) esc = false;
      else if (ch === '\\') esc = true;
      else if (ch === '"') inStr = false;
    } else if (ch === '"') {
      inStr = true;
    } else if (ch === '{' || ch === '[') {
      depth++;
    } else if (ch === '}' || ch === ']') {
      depth--;
      if (depth === 1 && ch === '}') lastElementEnd = i;
    }
  }
  if (lastElementEnd < 0) return undefined;
  try {
    const parsed = JSON.parse(raw.slice(0, lastElementEnd + 1) + ']');
    return Array.isArray(parsed) && parsed.length > 0 ? parsed : undefined;
  } catch {
    return undefined;
  }
}
