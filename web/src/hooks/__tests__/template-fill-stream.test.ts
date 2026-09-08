import { applyTemplateFillEvent, IStreamAcc } from '../template-fill-stream';

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
