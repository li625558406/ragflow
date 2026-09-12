import {
  applyTemplateFillEvent,
  IStreamAcc,
  parseTemplateFillEvents,
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
