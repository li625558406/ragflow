import {
  applyTemplateFillEvent,
  buildFilledRows,
  buildKeyNameMap,
  buildStateFromRunSnapshot,
  IStreamAcc,
  parseTemplateFillEvents,
  replayTemplateFillEvents,
} from '../template-fill-stream';

describe('parseTemplateFillEvents（截断 JSON 挽救）', () => {
  const ev = (stage: string, extra: Record<string, unknown> = {}) =>
    JSON.stringify({ stage, ...extra });

  it('合法 JSON 数组直接解析', () => {
    const raw = `[${ev('selected')},${ev('done')}]`;
    const events = parseTemplateFillEvents(raw) as any[];
    expect(events).toHaveLength(2);
    expect(events[0].stage).toBe('selected');
    expect(events[1].stage).toBe('done');
  });

  it('非字符串/空串/非数组/空数组返回 undefined', () => {
    expect(parseTemplateFillEvents(undefined)).toBeUndefined();
    expect(parseTemplateFillEvents('')).toBeUndefined();
    expect(parseTemplateFillEvents('{"a":1}')).toBeUndefined();
    expect(parseTemplateFillEvents('[]')).toBeUndefined();
  });

  it('已解析的事件数组直通（序列化层提前解析的输入）', () => {
    const arr = [{ stage: 'done' }];
    expect(parseTemplateFillEvents(arr)).toBe(arr);
    expect(parseTemplateFillEvents([])).toBeUndefined();
    expect(parseTemplateFillEvents(42)).toBeUndefined();
  });

  it('截断在字符串中间（值含 } 和中文）挽救出前面的完整事件', () => {
    // 模拟 MySQL TEXT 64KB 截断：最后一个事件的 values 字符串被拦腰截断，
    // 且已输出的值文本里含 } 字符（不能被误认成元素边界）
    const raw =
      `[${ev('selected', { templates: [{ template_id: 't1' }] })},` +
      ev('filling', { values: { a: '中文值{带花括号}' } }) +
      ',' +
      ev('filled', { download: { filename: '成稿.docx', size: 12 } }).slice(
        0,
        -30,
      ); // 截断在 filled 事件中途
    const events = parseTemplateFillEvents(raw) as any[];
    expect(events).toHaveLength(2);
    expect(events[0].stage).toBe('selected');
    expect(events[1].stage).toBe('filling');
    expect(events[1].values.a).toBe('中文值{带花括号}');
  });

  it('截断在嵌套对象中间时回退到上一个完整元素边界', () => {
    // download 是嵌套对象：截断点位于内层 } 之后、外层 } 之前
    const inner = `{"stage":"filled","download":{"doc_id":"d1"`;
    const raw = `[${ev('selected')},${inner}`;
    const events = parseTemplateFillEvents(raw) as any[];
    expect(events).toHaveLength(1);
    expect(events[0].stage).toBe('selected');
  });

  it('字符串值内的 } 不被当作元素边界（引号状态跟踪）', () => {
    const raw = `[${ev('filling', { values: { k: 'a}b]c"d}e' } })}`;
    const events = parseTemplateFillEvents(raw) as any[];
    expect(events).toHaveLength(1);
    expect(events[0].values.k).toBe('a}b]c"d}e');
  });

  it('无任何完整元素时返回 undefined', () => {
    expect(parseTemplateFillEvents('[{"stage":"fil')).toBeUndefined();
  });
});

describe('applyTemplateFillEvent', () => {
  it('selected 重置范本卡片列表', () => {
    const acc: IStreamAcc = {};
    applyTemplateFillEvent(acc, {
      stage: 'selected',
      templates: [
        { template_id: 't1', name: '范本A', slot_count: 3 },
        { template_id: 't2', name: '范本B', slot_count: 5 },
      ],
    });
    expect(acc.templateFill?.templates).toEqual([
      { template_id: 't1', name: '范本A', slot_count: 3, status: 'selected' },
      { template_id: 't2', name: '范本B', slot_count: 5, status: 'selected' },
    ]);
    expect(acc.templateFill?.finished).toBeUndefined();
  });

  it('filling 更新进度 done/total', () => {
    const acc: IStreamAcc = {};
    applyTemplateFillEvent(acc, {
      stage: 'selected',
      templates: [{ template_id: 't1', name: '范本A', slot_count: 4 }],
    });
    applyTemplateFillEvent(acc, {
      stage: 'filling',
      template_id: 't1',
      name: '范本A',
      done: 2,
      total: 4,
    });
    expect(acc.templateFill?.templates[0]).toMatchObject({
      status: 'filling',
      done: 2,
      total: 4,
    });
  });

  it('未知 template_id 自动补建卡片（容错）', () => {
    const acc: IStreamAcc = {};
    applyTemplateFillEvent(acc, {
      stage: 'filling',
      template_id: 'tx',
      name: '范本X',
      done: 1,
      total: 2,
    });
    expect(acc.templateFill?.templates).toHaveLength(1);
    expect(acc.templateFill?.templates[0].name).toBe('范本X');
  });

  it('渲染前产值补推事件（无 done/total）保留既有进度、只合并 values', () => {
    const acc: IStreamAcc = {};
    applyTemplateFillEvent(acc, {
      stage: 'selected',
      templates: [{ template_id: 't1', name: '范本A', slot_count: 4 }],
    });
    applyTemplateFillEvent(acc, {
      stage: 'filling',
      template_id: 't1',
      name: '范本A',
      done: 2,
      total: 4,
      values: { k1: 'v1' },
    });
    // 后端渲染前的产值补推：默认值/param 直取产值不经过 LLM 批次回调，
    // 事件只带 values 不带 done/total——进度显示不得被破坏
    applyTemplateFillEvent(acc, {
      stage: 'filling',
      template_id: 't1',
      name: '范本A',
      values: { k2: 'v2' },
    } as any);
    expect(acc.templateFill?.templates[0]).toMatchObject({
      status: 'filling',
      done: 2,
      total: 4,
    });
    expect(acc.templateFill?.templates[0].values).toEqual({
      k1: 'v1',
      k2: 'v2',
    });
  });

  it('filled 落 download、failed 落 error、done/cancelled 置 finished', () => {
    const acc: IStreamAcc = {};
    applyTemplateFillEvent(acc, {
      stage: 'selected',
      templates: [{ template_id: 't1', name: 'A', slot_count: 1 }],
    });
    applyTemplateFillEvent(acc, {
      stage: 'filled',
      template_id: 't1',
      download: {
        doc_id: 'd1',
        filename: 'a.docx',
        mime_type: 'x',
        url: '/u',
        name: 'a.docx',
      },
    });
    expect(acc.templateFill?.templates[0].status).toBe('filled');
    expect(acc.templateFill?.templates[0].download?.doc_id).toBe('d1');
    applyTemplateFillEvent(acc, { stage: 'done' });
    expect(acc.templateFill?.finished).toBe(true);
  });

  it('selected 在中途重复到达时重置（新一轮运行容错）', () => {
    const acc: IStreamAcc = {};
    applyTemplateFillEvent(acc, {
      stage: 'selected',
      templates: [{ template_id: 't1', name: 'A' }],
    });
    applyTemplateFillEvent(acc, { stage: 'done' });
    applyTemplateFillEvent(acc, {
      stage: 'selected',
      templates: [{ template_id: 't9', name: 'B' }],
    });
    expect(acc.templateFill?.templates).toHaveLength(1);
    expect(acc.templateFill?.templates[0].template_id).toBe('t9');
  });

  it('cancelled 置 finished 终态', () => {
    const acc: IStreamAcc = {};
    applyTemplateFillEvent(acc, {
      stage: 'selected',
      templates: [{ template_id: 't1', name: 'A' }],
    });
    applyTemplateFillEvent(acc, { stage: 'cancelled' });
    expect(acc.templateFill?.finished).toBe(true);
  });

  it('finished 后迟到 filling/filled 被忽略，selected 重开新一轮生效', () => {
    const acc: IStreamAcc = {};
    applyTemplateFillEvent(acc, {
      stage: 'selected',
      templates: [{ template_id: 't1', name: 'A', slot_count: 2 }],
    });
    applyTemplateFillEvent(acc, { stage: 'done' });
    expect(acc.templateFill?.finished).toBe(true);

    // 迟到的进度事件：卡片状态与 finished 均不变
    const before = acc.templateFill;
    applyTemplateFillEvent(acc, {
      stage: 'filling',
      template_id: 't1',
      done: 1,
      total: 2,
    });
    applyTemplateFillEvent(acc, {
      stage: 'filled',
      template_id: 't1',
      download: { doc_id: 'd1', filename: 'a.docx', mime_type: 'x' },
    });
    expect(acc.templateFill).toBe(before);
    expect(acc.templateFill?.finished).toBe(true);
    expect(acc.templateFill?.templates[0]).toMatchObject({
      status: 'selected',
      name: 'A',
      slot_count: 2,
    });

    // selected 重开新一轮：清除 finished，新列表生效
    applyTemplateFillEvent(acc, {
      stage: 'selected',
      templates: [{ template_id: 't2', name: 'B', slot_count: 1 }],
    });
    expect(acc.templateFill?.finished).toBeUndefined();
    expect(acc.templateFill?.templates).toHaveLength(1);
    expect(acc.templateFill?.templates[0].template_id).toBe('t2');
  });

  it('filled 携带 unfilled 写入模板行；缺省不清旧值', () => {
    const acc: IStreamAcc = {};
    applyTemplateFillEvent(acc, {
      stage: 'selected',
      templates: [{ template_id: 't1', name: 'A', slot_count: 2 }],
    });
    applyTemplateFillEvent(acc, {
      stage: 'filled',
      template_id: 't1',
      download: { doc_id: 'd1', filename: 'a.docx', mime_type: 'x' },
      unfilled: [{ key: 'k1', name: '字段一', required: true }],
    });
    expect(acc.templateFill?.templates[0].unfilled).toEqual([
      { key: 'k1', name: '字段一', required: true },
    ]);

    // 事件缺省 unfilled（如旧后端/全部填满）→ 不清已有值
    applyTemplateFillEvent(acc, {
      stage: 'filled',
      template_id: 't1',
      download: { doc_id: 'd2', filename: 'b.docx', mime_type: 'x' },
    });
    expect(acc.templateFill?.templates[0].unfilled).toEqual([
      { key: 'k1', name: '字段一', required: true },
    ]);
  });

  it('filled 携带 values 整体替换（增量/noop 场景 baseline+default 全量覆盖）', () => {
    // 增量模式：filling 事件只带 LLM 实时产值（patch 项或 0 项），baseline+default
    // 兜回的字段不在 filling 事件里。filled.values 才是 row.values.render 的全量
    // ——必须整体覆盖，否则 t.values 只剩 patch 项，预览大面积虚线槽位（用户视觉
    // 等同「没填」）。
    const acc: IStreamAcc = {};
    applyTemplateFillEvent(acc, {
      stage: 'selected',
      templates: [{ template_id: 't1', name: 'A', slot_count: 4 }],
    });
    applyTemplateFillEvent(acc, {
      stage: 'filling',
      template_id: 't1',
      name: 'A',
      done: 1,
      total: 4,
      values: { k1: 'patch_value' },
    });
    expect(acc.templateFill?.templates[0].values).toEqual({
      k1: 'patch_value',
    });
    applyTemplateFillEvent(acc, {
      stage: 'filled',
      template_id: 't1',
      download: { doc_id: 'd1', filename: 'a.docx', mime_type: 'x' },
      values: {
        k1: 'patch_value',
        k2: 'baseline2',
        k3: 'baseline3',
        k4: 'default4',
      },
    });
    expect(acc.templateFill?.templates[0].values).toEqual({
      k1: 'patch_value',
      k2: 'baseline2',
      k3: 'baseline3',
      k4: 'default4',
    });
    expect(acc.templateFill?.templates[0].status).toBe('filled');
  });

  it('filled 缺省 values 时不清旧值（兼容旧后端 / 全量场景填充已被 filling 累积）', () => {
    // 全量场景：filling 事件已累积全量 values；filled 不带 values（缺省或旧后端）
    // → 保留 filling 累积结果，行为兼容。
    const acc: IStreamAcc = {};
    applyTemplateFillEvent(acc, {
      stage: 'selected',
      templates: [{ template_id: 't1', name: 'A', slot_count: 2 }],
    });
    applyTemplateFillEvent(acc, {
      stage: 'filling',
      template_id: 't1',
      name: 'A',
      done: 2,
      total: 2,
      values: { k1: 'v1', k2: 'v2' },
    });
    applyTemplateFillEvent(acc, {
      stage: 'filled',
      template_id: 't1',
      download: { doc_id: 'd1', filename: 'a.docx', mime_type: 'x' },
    });
    expect(acc.templateFill?.templates[0].values).toEqual({
      k1: 'v1',
      k2: 'v2',
    });
  });

  it('filled 携带空 values 时不清旧值（防御空覆盖）', () => {
    // 极端边界：后端发了空 dict → 不覆盖已有的 filling 累积值，避免丢内容。
    const acc: IStreamAcc = {};
    applyTemplateFillEvent(acc, {
      stage: 'selected',
      templates: [{ template_id: 't1', name: 'A', slot_count: 2 }],
    });
    applyTemplateFillEvent(acc, {
      stage: 'filling',
      template_id: 't1',
      name: 'A',
      done: 1,
      total: 2,
      values: { k1: 'v1' },
    });
    applyTemplateFillEvent(acc, {
      stage: 'filled',
      template_id: 't1',
      download: { doc_id: 'd1', filename: 'a.docx', mime_type: 'x' },
      values: {},
    });
    expect(acc.templateFill?.templates[0].values).toEqual({ k1: 'v1' });
  });

  it('unfilled 后到者胜（终态一次性数据整体替换，重放幂等）', () => {
    const acc: IStreamAcc = {};
    applyTemplateFillEvent(acc, {
      stage: 'selected',
      templates: [{ template_id: 't1', name: 'A' }],
    });
    const ev = {
      stage: 'filled',
      template_id: 't1',
      download: { doc_id: 'd1', filename: 'a.docx', mime_type: 'x' },
      unfilled: [{ key: 'k1', name: '字段一', required: true }],
    };
    applyTemplateFillEvent(acc, ev as any);
    applyTemplateFillEvent(acc, JSON.parse(JSON.stringify(ev)) as any);
    expect(acc.templateFill?.templates[0].unfilled).toEqual([
      { key: 'k1', name: '字段一', required: true },
    ]);
  });
});

describe('confirm_pending / confirm_timeout', () => {
  it('confirm_pending 写入 pendingConfirm 状态（含 nonce）', () => {
    const acc: IStreamAcc = {};
    applyTemplateFillEvent(acc, {
      stage: 'confirm_pending',
      task_id: 't1',
      confirm_nonce: 'n1',
      confirm_templates: [
        {
          template_id: 'tp1',
          name: '范本',
          candidates: [{ key: 'a', name: '甲', default_value: 'v' }],
          predicted: ['a'],
        },
      ],
    } as any);
    expect(acc.templateFill?.pendingConfirm?.task_id).toBe('t1');
    expect(acc.templateFill?.pendingConfirm?.nonce).toBe('n1');
    expect(
      acc.templateFill?.pendingConfirm?.templates[0].candidates[0].key,
    ).toBe('a');
  });

  it('confirm_pending 缺字段容错（空 task_id/nonce/templates）', () => {
    const acc: IStreamAcc = {};
    applyTemplateFillEvent(acc, { stage: 'confirm_pending' } as any);
    expect(acc.templateFill?.pendingConfirm?.task_id).toBe('');
    expect(acc.templateFill?.pendingConfirm?.nonce).toBe('');
    expect(acc.templateFill?.pendingConfirm?.templates).toEqual([]);
  });

  it('confirm_timeout 置 expired；submitted 后不覆盖', () => {
    const acc: IStreamAcc = {};
    applyTemplateFillEvent(acc, {
      stage: 'confirm_pending',
      task_id: 't1',
      confirm_nonce: 'n1',
      confirm_templates: [],
    } as any);
    applyTemplateFillEvent(acc, { stage: 'confirm_timeout' } as any);
    expect(acc.templateFill?.pendingConfirm?.expired).toBe(true);
    // nonce 等已有字段保留
    expect(acc.templateFill?.pendingConfirm?.nonce).toBe('n1');

    // 已提交场景：timeout 不覆盖 expired
    const acc2: IStreamAcc = {};
    applyTemplateFillEvent(acc2, {
      stage: 'confirm_pending',
      task_id: 't2',
      confirm_templates: [],
    } as any);
    acc2.templateFill!.pendingConfirm!.submitted = true;
    applyTemplateFillEvent(acc2, { stage: 'confirm_timeout' } as any);
    expect(acc2.templateFill!.pendingConfirm!.expired).toBeUndefined();
  });

  it('filling/filled/failed 事件携带 task_id 时记录到模板行（断连重连锚点）', () => {
    const acc: IStreamAcc = {};
    applyTemplateFillEvent(acc, {
      stage: 'filling',
      template_id: 't1',
      name: 'A',
      done: 1,
      total: 5,
      task_id: 'task-1',
    });
    expect(acc.templateFill?.templates[0].task_id).toBe('task-1');
    applyTemplateFillEvent(acc, {
      stage: 'filled',
      template_id: 't1',
      name: 'A',
      download: { doc_id: 'd', filename: 'a.docx', mime_type: 'x' },
      task_id: 'task-1',
    });
    expect(acc.templateFill?.templates[0].task_id).toBe('task-1');
    const acc2: IStreamAcc = {};
    applyTemplateFillEvent(acc2, {
      stage: 'failed',
      template_id: 't2',
      name: 'B',
      error: 'x',
      task_id: 'task-2',
    });
    expect(acc2.templateFill?.templates[0].task_id).toBe('task-2');
  });

  it('无 task_id 的旧消息归约不变（兼容）', () => {
    const acc: IStreamAcc = {};
    applyTemplateFillEvent(acc, {
      stage: 'filling',
      template_id: 't1',
      name: 'A',
      done: 0,
      total: 3,
    });
    expect(acc.templateFill?.templates[0].task_id).toBeUndefined();
  });

  it('同名无 task_id 的事件不抹掉已记录的 task_id', () => {
    const acc: IStreamAcc = {};
    applyTemplateFillEvent(acc, {
      stage: 'filling',
      template_id: 't1',
      name: 'A',
      done: 0,
      total: 3,
      task_id: 'task-1',
    });
    applyTemplateFillEvent(acc, {
      stage: 'filling',
      template_id: 't1',
      name: 'A',
      done: 1,
      total: 3,
    });
    expect(acc.templateFill?.templates[0].task_id).toBe('task-1');
  });

  it('finished 终态后迟到的 confirm_pending/confirm_timeout 被忽略', () => {
    const acc: IStreamAcc = {};
    applyTemplateFillEvent(acc, {
      stage: 'selected',
      templates: [{ template_id: 't1', name: 'A' }],
    });
    applyTemplateFillEvent(acc, { stage: 'done' });
    applyTemplateFillEvent(acc, {
      stage: 'confirm_pending',
      task_id: 'tx',
      confirm_templates: [],
    } as any);
    applyTemplateFillEvent(acc, { stage: 'confirm_timeout' } as any);
    expect(acc.templateFill?.pendingConfirm).toBeUndefined();
    expect(acc.templateFill?.finished).toBe(true);
  });
});

// ── filled（已填字段清单）：与 unfilled 完全同构的终态一次性数据 ──────────────

describe('filled 事件写入模板行', () => {
  const selected = (acc: IStreamAcc) =>
    applyTemplateFillEvent(acc, {
      stage: 'selected',
      templates: [{ template_id: 't1', name: 'A', slot_count: 3 }],
    });
  const filledEv = (extra: Record<string, unknown> = {}) => ({
    stage: 'filled',
    template_id: 't1',
    download: { doc_id: 'd1', filename: 'a.docx', mime_type: 'x' },
    ...extra,
  });

  it('filled 携带 filled 清单写入模板行（保序原样）', () => {
    const acc: IStreamAcc = {};
    selected(acc);
    applyTemplateFillEvent(
      acc,
      filledEv({
        filled: [
          { key: 'k1', name: '字段一' },
          { key: 'k2', name: '字段二' },
        ],
      }) as any,
    );
    expect(acc.templateFill?.templates[0].filled).toEqual([
      { key: 'k1', name: '字段一' },
      { key: 'k2', name: '字段二' },
    ]);
  });

  it('缺省 filled（旧后端/全空）→ 不下键，不清已有清单', () => {
    const acc: IStreamAcc = {};
    selected(acc);
    applyTemplateFillEvent(
      acc,
      filledEv({ filled: [{ key: 'k1', name: '字段一' }] }) as any,
    );
    applyTemplateFillEvent(
      acc,
      filledEv({ download: { doc_id: 'd2' } }) as any,
    );
    expect(acc.templateFill?.templates[0].filled).toEqual([
      { key: 'k1', name: '字段一' },
    ]);
  });

  it('边界：filled: [] 为真值 → 清空（与 unfilled 同语义，后端已归一为缺省不下发）', () => {
    const acc: IStreamAcc = {};
    selected(acc);
    applyTemplateFillEvent(
      acc,
      filledEv({ filled: [{ key: 'k1', name: '字段一' }] }) as any,
    );
    applyTemplateFillEvent(acc, filledEv({ filled: [] }) as any);
    expect(acc.templateFill?.templates[0].filled).toEqual([]);
  });

  it('filled 与 unfilled 并存互不干扰（并集即全量 key→中文名）', () => {
    const acc: IStreamAcc = {};
    selected(acc);
    applyTemplateFillEvent(
      acc,
      filledEv({
        filled: [{ key: 'k1', name: '字段一' }],
        unfilled: [{ key: 'k2', name: '字段二', required: true }],
      }) as any,
    );
    const row = acc.templateFill!.templates[0];
    expect(row.filled).toEqual([{ key: 'k1', name: '字段一' }]);
    expect(row.unfilled).toEqual([
      { key: 'k2', name: '字段二', required: true },
    ]);
  });

  it('failed 事件不携带 filled（未终态无产值）', () => {
    const acc: IStreamAcc = {};
    selected(acc);
    applyTemplateFillEvent(acc, {
      stage: 'failed',
      template_id: 't1',
      error: 'boom',
    });
    expect(acc.templateFill?.templates[0].filled).toBeUndefined();
    expect(acc.templateFill?.templates[0].status).toBe('failed');
  });

  it('finished 终态后迟到的 filled 被忽略', () => {
    const acc: IStreamAcc = {};
    selected(acc);
    applyTemplateFillEvent(acc, { stage: 'done' });
    const before = acc.templateFill;
    applyTemplateFillEvent(
      acc,
      filledEv({ filled: [{ key: 'k1', name: '字段一' }] }) as any,
    );
    expect(acc.templateFill).toBe(before);
    expect(acc.templateFill?.templates[0].filled).toBeUndefined();
  });

  it('filling 阶段携带 filled 字段被忽略（只在 filled 阶段写入）', () => {
    // 阶段隔离：filled 是终态清单（整体替换语义），若 filling 也写会在流式中
    // 出现「先有全量清单、后被终态覆盖」的中间态，且补推事件可能带脏数据
    const acc: IStreamAcc = {};
    selected(acc);
    applyTemplateFillEvent(acc, {
      stage: 'filling',
      template_id: 't1',
      name: 'A',
      done: 1,
      total: 2,
      values: { k1: 'v1' },
      filled: [{ key: 'k1', name: '字段一' }],
    } as any);
    expect(acc.templateFill?.templates[0].filled).toBeUndefined();
    expect(acc.templateFill?.templates[0].unfilled).toBeUndefined();
  });

  it('filled 有值但同一响应无 values → buildFilledRows 渲染空串不崩', () => {
    const acc: IStreamAcc = {};
    selected(acc);
    applyTemplateFillEvent(
      acc,
      filledEv({
        values: undefined,
        filled: [{ key: 'k1', name: '字段一' }],
      }) as any,
    );
    const t = acc.templateFill?.templates[0];
    expect(t?.filled).toEqual([{ key: 'k1', name: '字段一' }]);
    expect(buildFilledRows(t!)).toEqual([
      { key: 'k1', name: '字段一', value: '' },
    ]);
  });

  it('filled 有重复 key → buildFilledRows 原样产出（唯一性由后端保证）', () => {
    // 契约固化：key 唯一性由 validate_placeholders / _merge_detection 保证，
    // 前端不额外去重（去重会静默掩盖后端 bug）；React 重复 key 只是 warning
    const rows = buildFilledRows({
      filled: [
        { key: 'k1', name: '字段一' },
        { key: 'k1', name: '字段一' },
      ],
      values: { k1: 'v1' },
    });
    expect(rows).toHaveLength(2);
    expect(rows.map((r) => r.key)).toEqual(['k1', 'k1']);
  });

  it('重放往返：replay 后 filled 与原始事件序列一致（后到者胜幂等）', () => {
    const events = [
      { stage: 'selected', templates: [{ template_id: 't1', name: 'A' }] },
      {
        stage: 'filling',
        template_id: 't1',
        name: 'A',
        done: 1,
        total: 2,
        values: { k1: 'v1' },
      },
      {
        stage: 'filled',
        template_id: 't1',
        name: 'A',
        download: { doc_id: 'd1', filename: 'a.docx', mime_type: 'x' },
        values: { k1: 'v1', k2: '' },
        filled: [{ key: 'k1', name: '字段一' }],
        unfilled: [{ key: 'k2', name: '字段二', required: false }],
      },
    ];
    const state = replayTemplateFillEvents(events);
    expect(state?.templates[0].filled).toEqual([{ key: 'k1', name: '字段一' }]);
    expect(state?.templates[0].unfilled).toEqual([
      { key: 'k2', name: '字段二', required: false },
    ]);
    // 二次重放（模拟重复恢复）不叠加、形状不变
    const again = replayTemplateFillEvents(
      JSON.parse(JSON.stringify(events)) as unknown[],
    );
    expect(again?.templates[0].filled).toEqual([{ key: 'k1', name: '字段一' }]);
  });

  it('buildStateFromRunSnapshot：filled/unfilled 映射（null 归一 undefined）', () => {
    const state = buildStateFromRunSnapshot(
      {
        exists: true,
        stage: 'done',
        finished: true,
        templates: [
          {
            template_id: 't1',
            name: 'A',
            status: 'filled',
            filled: [{ key: 'k1', name: '字段一' }],
            unfilled: null,
            values: { k1: 'v1' },
          },
          {
            template_id: 't2',
            name: 'B',
            status: 'filling',
            filled: null,
            unfilled: null,
          },
        ],
      },
      'run-1',
    );
    expect(state?.templates[0].filled).toEqual([{ key: 'k1', name: '字段一' }]);
    expect(state?.templates[0].unfilled).toBeUndefined();
    expect(state?.templates[1].filled).toBeUndefined();
  });

  it('buildStateFromRunSnapshot：exists=false 返回 undefined', () => {
    expect(
      buildStateFromRunSnapshot({ exists: false }, 'run-1'),
    ).toBeUndefined();
  });
});

describe('buildKeyNameMap（filled ∪ unfilled → 全量 key→中文名）', () => {
  it('合并两清单（并集即全量，顺序确定）', () => {
    const map = buildKeyNameMap({
      filled: [{ key: 'k1', name: '字段一' }],
      unfilled: [{ key: 'k2', name: '字段二', required: false }],
    });
    expect(map.get('k1')).toBe('字段一');
    expect(map.get('k2')).toBe('字段二');
    expect(map.size).toBe(2);
  });

  it('name 缺失回落 key（映射不漏项）', () => {
    const map = buildKeyNameMap({
      filled: [{ key: 'k1', name: '' } as any],
      unfilled: [{ key: 'k2' } as any],
    });
    expect(map.get('k1')).toBe('k1');
    expect(map.get('k2')).toBe('k2');
  });

  it('重名 key：后写者胜（unfilled 覆盖 filled）', () => {
    // 理论上不可能重叠（后端同一真源互斥），固化为确定性行为防漂移
    const map = buildKeyNameMap({
      filled: [{ key: 'k1', name: 'FILLED名' }],
      unfilled: [{ key: 'k1', name: 'UNFILLED名', required: true }],
    });
    expect(map.get('k1')).toBe('UNFILLED名');
  });

  it('空输入 / undefined 键 / 脏项不抛', () => {
    expect(buildKeyNameMap({}).size).toBe(0);
    expect(buildKeyNameMap({ filled: [], unfilled: [] }).size).toBe(0);
    expect(
      buildKeyNameMap({
        filled: [null as any, {} as any, { key: '' } as any],
        unfilled: [undefined as any],
      }).size,
    ).toBe(0);
  });
});

describe('buildFilledRows（已填清单 join values → 可渲染行）', () => {
  it('按 filled 顺序 join values', () => {
    const rows = buildFilledRows({
      filled: [
        { key: 'k2', name: '字段二' },
        { key: 'k1', name: '字段一' },
      ],
      values: { k1: 'v1', k2: 'v2' },
    });
    expect(rows).toEqual([
      { key: 'k2', name: '字段二', value: 'v2' },
      { key: 'k1', name: '字段一', value: 'v1' },
    ]);
  });

  it('值缺失 / null / undefined 一律归一为空串（不显示 undefined）', () => {
    const rows = buildFilledRows({
      filled: [
        { key: 'k1', name: '字段一' },
        { key: 'k2', name: '字段二' },
        { key: 'k3', name: '字段三' },
      ],
      values: { k1: null as any, k2: undefined as any },
    });
    expect(rows.map((r) => r.value)).toEqual(['', '', '']);
  });

  it('非字符串值（number/bool 脏数据）String 归一', () => {
    const rows = buildFilledRows({
      filled: [
        { key: 'k1', name: '甲' },
        { key: 'k2', name: '乙' },
        { key: 'k3', name: '丙' },
      ],
      values: { k1: 0 as any, k2: false as any, k3: 12.5 as any },
    });
    expect(rows.map((r) => r.value)).toEqual(['0', 'false', '12.5']);
  });

  it('name 缺失回落 key；无 key 的脏项被过滤', () => {
    const rows = buildFilledRows({
      filled: [{ key: 'k1', name: '' } as any, {} as any, null as any],
      values: { k1: 'v1' },
    });
    expect(rows).toEqual([{ key: 'k1', name: 'k1', value: 'v1' }]);
  });

  it('空输入 / values 缺失不抛', () => {
    expect(buildFilledRows({})).toEqual([]);
    expect(buildFilledRows({ filled: [] })).toEqual([]);
    expect(
      buildFilledRows({ filled: [{ key: 'k1', name: '甲' }] } as any),
    ).toEqual([{ key: 'k1', name: '甲', value: '' }]);
  });
});
