// 「查看填写内容」实时预览：打开时按 task_id 拉权威产值覆盖卡片快照。
//
// 背景（2026-09-17 实测「模型说改了、预览还是旧文案」）：对话里的
// FillTemplate(action=modify) 不发任何 template_fill_progress 事件，卡片上的
// values 会一直停在改前；而预览里的文字完全由 values 决定（docx 分支是用
// values 覆盖模板工作副本渲染），所以必须打开时按 task_id 拉一次 progress
// （该端点 values 取自 DB render，modify 已回写）。
//
// 本套件走 xlsx 文本分支（不走 docx-preview，渲染确定）：file_type='xlsx'
// 时组件用 renderText 把 {{key}} 槽位替换成 values[key]，可直接断言屏幕文本。
import type { ITemplateFillTemplate } from '@/hooks/template-fill-stream';
import TemplateFillLivePreview from '@/pages/c-chat/template-fill-live-preview';
import { render, screen, waitFor } from '@testing-library/react';

jest.mock('docx-preview', () => ({ renderAsync: jest.fn() }));

const previewData = {
  code: 0,
  data: {
    file_type: 'xlsx',
    items: [
      {
        index: 0,
        text: '招标人：{{tenderer_name}}',
        addr: 'A1',
        placeholder_key: 'tenderer_name',
      },
    ],
  },
};

jest.mock('@/hooks/use-template-fill-request', () => ({
  useTemplateFillPreview: jest.fn(() => ({
    data: previewData,
    isLoading: false,
  })),
  useTemplateFillFile: jest.fn(() => ({
    data: undefined,
    isLoading: false,
    error: undefined,
  })),
  fetchTemplateFillTaskProgress: jest.fn(),
}));

// eslint-disable-next-line @typescript-eslint/no-var-requires
const hooks = jest.requireMock('@/hooks/use-template-fill-request') as {
  fetchTemplateFillTaskProgress: jest.Mock;
};

const OLD = '石狮市交通建设投资有限责任公司';
const NEW = '石狮市李港aaa交通建设投资有限责任公司';

function cardTpl(
  over: Partial<ITemplateFillTemplate> = {},
): ITemplateFillTemplate {
  return {
    template_id: 'tpl1',
    name: '福建高速范本',
    status: 'filled',
    task_id: 'task1',
    values: { tenderer_name: OLD },
    ...over,
  };
}

function renderPreview(tpl: ITemplateFillTemplate) {
  return render(<TemplateFillLivePreview tpl={tpl} onClose={() => {}} />);
}

beforeEach(() => {
  jest.clearAllMocks();
});

describe('TemplateFillLivePreview 权威产值覆盖', () => {
  it('终态（done）拉到的 values 覆盖卡片上的旧快照', async () => {
    hooks.fetchTemplateFillTaskProgress.mockResolvedValue({
      status: 'done',
      values: { tenderer_name: NEW },
    });
    renderPreview(cardTpl());
    // 首帧仍是卡片快照（拉取未回），随后被权威值替换
    await waitFor(() => expect(screen.getByText(NEW)).toBeTruthy());
    expect(screen.queryByText(OLD)).toBeNull();
  });

  it('未终态（filling）不覆盖：流式期间 SSE 的 values 更新鲜', async () => {
    hooks.fetchTemplateFillTaskProgress.mockResolvedValue({
      status: 'filling',
      values: { tenderer_name: NEW },
    });
    renderPreview(cardTpl({ status: 'filling' }));
    expect(screen.getByText(OLD)).toBeTruthy();
    expect(screen.queryByText(NEW)).toBeNull();
  });

  it('失败态（failed/cancelled）不覆盖——没有成稿可取', async () => {
    hooks.fetchTemplateFillTaskProgress.mockResolvedValue({
      status: 'failed',
      values: { tenderer_name: NEW },
    });
    renderPreview(cardTpl());
    expect(screen.getByText(OLD)).toBeTruthy();
  });

  it('没有 task_id 时不发请求（旧消息无锚点）', async () => {
    renderPreview(cardTpl({ task_id: undefined }));
    expect(hooks.fetchTemplateFillTaskProgress).not.toHaveBeenCalled();
    expect(screen.getByText(OLD)).toBeTruthy();
  });

  it('状态非 filled（如还在 filling 卡片态）不请求', async () => {
    renderPreview(cardTpl({ status: 'selected' }));
    expect(hooks.fetchTemplateFillTaskProgress).not.toHaveBeenCalled();
  });

  it('拉取失败回落卡片快照：预览仍可用，只是可能显示改前内容', async () => {
    hooks.fetchTemplateFillTaskProgress.mockRejectedValue(new Error('500'));
    renderPreview(cardTpl());
    await waitFor(() =>
      expect(hooks.fetchTemplateFillTaskProgress).toHaveBeenCalledWith('task1'),
    );
    expect(screen.getByText(OLD)).toBeTruthy();
  });

  it('返回 null / 空 values 时回落卡片快照，不把预览清空', async () => {
    hooks.fetchTemplateFillTaskProgress.mockResolvedValue(null);
    const { unmount } = renderPreview(cardTpl());
    await waitFor(() =>
      expect(hooks.fetchTemplateFillTaskProgress).toHaveBeenCalled(),
    );
    expect(screen.getByText(OLD)).toBeTruthy();
    unmount();

    hooks.fetchTemplateFillTaskProgress.mockResolvedValue({
      status: 'done',
      values: null,
    });
    renderPreview(cardTpl());
    await waitFor(() => expect(screen.getByText(OLD)).toBeTruthy());
  });

  it('权威响应里的 filled 清单生效：已填槽位悬浮显中文名', async () => {
    hooks.fetchTemplateFillTaskProgress.mockResolvedValue({
      status: 'done',
      values: { tenderer_name: NEW },
      filled: [{ key: 'tenderer_name', name: '招标人名称' }],
      unfilled: null,
    });
    renderPreview(cardTpl());
    const span = await waitFor(() => screen.getByText(NEW));
    // title 用「中文名（key）」；names 来自权威 filled 清单而非卡片快照
    expect(span.getAttribute('title')).toBe('招标人名称（tenderer_name）');
  });

  it('权威响应 filled 为空（null/[]）表示「无留空/全填满」：不回落卡片上可能过时的清单', async () => {
    hooks.fetchTemplateFillTaskProgress.mockResolvedValue({
      status: 'done',
      values: { tenderer_name: NEW },
      filled: [],
      unfilled: null,
    });
    // 卡片快照里塞一份中文名：权威响应已到达，且其 unfilled 为 null（空）——
    // 此时名称只能来自权威，不得回落到卡片那份（按 null 与否判定，而非 ??）
    renderPreview(
      cardTpl({
        unfilled: [
          { key: 'tenderer_name', name: '不该出现的名字', required: true },
        ],
      }),
    );
    const span = await waitFor(() => screen.getByText(NEW));
    expect(span.getAttribute('title')).toBe('tenderer_name');
  });
});
