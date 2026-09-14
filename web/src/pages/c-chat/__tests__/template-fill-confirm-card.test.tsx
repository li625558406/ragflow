// 确认卡填写项点击定位：字段名在 onLocate 传入时渲染为可点 button 并回传
// (template_id, key)；未传时保持纯文本（向后兼容）。定位链路本身（LivePreview
// focusKey → focusPlaceholder）由未填充汇总功能覆盖，此处只测卡片接线。
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
