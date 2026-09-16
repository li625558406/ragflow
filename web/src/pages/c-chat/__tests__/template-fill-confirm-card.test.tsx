// 确认卡填写项点击定位：字段名在 onLocate 传入时渲染为可点 button 并回传
// (template_id, key)；未传时保持纯文本（向后兼容）。定位链路本身（LivePreview
// focusKey → focusPlaceholder）由未填充汇总功能覆盖，此处只测卡片接线。
// 增量模式 direct_value 预填：LLM 从用户原话抽出的 direct 值预填到输入框，
// placeholder 切换为「AI 建议值，可改」；提交时未改 → 走 ov 分支非空 user 覆盖
// fallback，值与 fallback 一致——后端保留 fallback 兜底，前端让用户看见/编辑。
import type { ITemplateFillConfirmPending } from '@/hooks/template-fill-stream';
import TemplateFillConfirmCard from '@/pages/c-chat/template-fill-confirm-card';
import { fireEvent, render, screen } from '@testing-library/react';

jest.mock('@/hooks/use-template-fill-request', () => ({
  confirmTemplateFill: jest.fn().mockResolvedValue(undefined),
}));

const pending: ITemplateFillConfirmPending = {
  task_id: 't1',
  nonce: 'n1',
  templates: [
    {
      template_id: 'tpl1',
      name: '范本一',
      candidates: [
        { key: 'project_name', name: '项目名称', default_value: 'XX项目' },
        { key: 'phone', name: '联系电话', default_value: '' },
      ],
      predicted: ['project_name'],
    },
  ],
};

const pendingWithDirect: ITemplateFillConfirmPending = {
  task_id: 't2',
  nonce: 'n2',
  templates: [
    {
      template_id: 'tpl2',
      name: '范本二',
      candidates: [
        {
          key: 'approval_doc',
          name: '批文名称',
          default_value: '原批文',
          direct_value: '港里',
        },
        { key: 'body', name: '正文', default_value: '原正文' }, // 无 direct
      ],
      predicted: ['approval_doc', 'body'],
    },
  ],
};

describe('TemplateFillConfirmCard 字段名点击定位', () => {
  it('onLocate 传入时字段名渲染为 button，点击回传 (template_id, key)', () => {
    const onLocate = jest.fn();
    render(<TemplateFillConfirmCard pending={pending} onLocate={onLocate} />);
    const btn = screen.getByRole('button', { name: /项目名称/ });
    fireEvent.click(btn);
    expect(onLocate).toHaveBeenCalledTimes(1);
    expect(onLocate).toHaveBeenCalledWith('tpl1', 'project_name');
    // 另一个字段同样可点，参数正确
    fireEvent.click(screen.getByRole('button', { name: /联系电话/ }));
    expect(onLocate).toHaveBeenCalledWith('tpl1', 'phone');
  });

  it('onLocate 未传时字段名保持纯文本（不渲染 button）', () => {
    render(<TemplateFillConfirmCard pending={pending} />);
    expect(screen.queryByRole('button', { name: /项目名称/ })).toBeNull();
    expect(screen.getByText(/项目名称/)).toBeInTheDocument();
  });
});

describe('TemplateFillConfirmCard 增量 direct_value 预填', () => {
  it('candidates 带 direct_value → 输入框预填 + placeholder 切换为「AI 建议值，可改」', () => {
    render(<TemplateFillConfirmCard pending={pendingWithDirect} />);
    // 找到所有 input
    const inputs =
      screen.getAllByPlaceholderText(/AI 建议值，可改|留空则 AI 重填/);
    // approval_doc：direct_value=港里 → 预填，placeholder=「AI 建议值，可改」
    const approvalInput = inputs.find(
      (el) => (el as HTMLInputElement).value === '港里',
    );
    expect(approvalInput).toBeTruthy();
    expect(approvalInput).toHaveAttribute('placeholder', 'AI 建议值，可改');
    // body：无 direct_value → 预填空，placeholder=「留空则 AI 重填」
    const bodyInput = inputs.find(
      (el) => (el as HTMLInputElement).placeholder === '留空则 AI 重填',
    );
    expect(bodyInput).toBeTruthy();
    expect(bodyInput).toHaveValue('');
  });

  it('无 direct_value 的旧契约 → 输入框不预填 + placeholder 保持「留空则 AI 重填」', () => {
    // 兼容全量模式 / 旧后端不传 direct_value
    render(<TemplateFillConfirmCard pending={pending} />);
    const inputs = screen.getAllByPlaceholderText('留空则 AI 重填');
    expect(inputs.length).toBeGreaterThan(0);
    for (const el of inputs) {
      expect(el).toHaveValue('');
    }
  });

  it('用户清空预填值（清空「港里」回空）→ 提交时 values_raw={} → 后端走 fallback 兜底', async () => {
    const { confirmTemplateFill } = jest.requireMock(
      '@/hooks/use-template-fill-request',
    );
    (confirmTemplateFill as jest.Mock).mockClear();
    render(<TemplateFillConfirmCard pending={pendingWithDirect} />);
    // 拿到预填「港里」的 input
    const approvalInput = screen.getAllByDisplayValue(
      '港里',
    )[0] as HTMLInputElement;
    // 用户清空
    fireEvent.change(approvalInput, { target: { value: '' } });
    expect(approvalInput).toHaveValue('');
    // 提交
    fireEvent.click(screen.getByRole('button', { name: /确认并继续填写/ }));
    // 等异步提交完成（mockResolvedValue）
    await new Promise((r) => setTimeout(r, 0));
    expect(confirmTemplateFill).toHaveBeenCalledTimes(1);
    const [, , decisions] = (confirmTemplateFill as jest.Mock).mock.calls[0];
    // 用户空值 → 不入 values（前端提交时不发空键），后端 ov 分支保留 fallback
    expect(decisions.tpl2.values).toEqual({});
  });
});
