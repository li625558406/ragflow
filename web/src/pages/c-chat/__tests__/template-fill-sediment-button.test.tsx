// 写回范本库按钮（成稿行内）：只测接线与四态——按钮是否挂载、点击后的
// loading/done/empty/error 分支、错误态可重试、task_id 缺失时不渲染入口。
// 真实沉淀语义（白名单并集 / manual 保护 / 截断 / 幂等）由后端
// test/test_template_fill_sediment_api.py 覆盖，前端不重复造桩。
import type { ITemplateFillState } from '@/hooks/template-fill-stream';
import TemplateFillProgress from '@/pages/c-chat/template-fill-progress';
import { fireEvent, render, screen } from '@testing-library/react';

jest.mock('@/hooks/use-template-fill-request', () => ({
  sedimentTemplateFillDefaults: jest.fn(),
  confirmTemplateFill: jest.fn().mockResolvedValue(undefined),
  confirmTemplateFillSelect: jest.fn().mockResolvedValue(undefined),
}));

// live-preview 顶层 import docx-preview（重且与本用例无关），整体桩掉
jest.mock('@/pages/c-chat/template-fill-live-preview', () => ({
  __esModule: true,
  default: () => null,
}));

const { sedimentTemplateFillDefaults } = jest.requireMock(
  '@/hooks/use-template-fill-request',
) as { sedimentTemplateFillDefaults: jest.Mock };

/** 终态成稿行（走到 filled 分支即渲染下载条与写回按钮） */
function finishedRow(taskId?: string): ITemplateFillState {
  return {
    templates: [
      {
        template_id: 'tpl1',
        name: '范本一',
        status: 'filled',
        task_id: taskId,
        download: {
          doc_id: 'd1',
          filename: '成稿.docx',
          mime_type:
            'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
          url: 'http://example.invalid/d1',
        },
      },
    ],
  };
}

const writeBackBtn = () => screen.queryByRole('button', { name: /写回范本库/ });

beforeEach(() => {
  sedimentTemplateFillDefaults.mockReset();
});

describe('TemplateFillSedimentButton 挂载口径', () => {
  it('task_id 存在 → 终态成稿行渲染「写回范本库」', () => {
    render(<TemplateFillProgress state={finishedRow('task1')} streaming />);
    expect(writeBackBtn()).toBeInTheDocument();
  });

  it('task_id 缺失（旧消息/脏数据）→ 不渲染入口，宁可不给也不发无效请求', () => {
    render(<TemplateFillProgress state={finishedRow(undefined)} streaming />);
    expect(writeBackBtn()).toBeNull();
    expect(sedimentTemplateFillDefaults).not.toHaveBeenCalled();
  });
});

describe('TemplateFillSedimentButton 四态', () => {
  it('点击 → 按钮被「写回中…」替换（loading 期不可再点）→ 成功落「已写回范本库」', async () => {
    let resolve!: (v: { written: boolean }) => void;
    sedimentTemplateFillDefaults.mockReturnValue(
      new Promise<{ written: boolean }>((r) => {
        resolve = r;
      }),
    );
    render(<TemplateFillProgress state={finishedRow('task1')} streaming />);

    fireEvent.click(writeBackBtn()!);
    expect(sedimentTemplateFillDefaults).toHaveBeenCalledWith('task1');
    // loading 期按钮整体卸载 → 第二次物理点击无从发生（非靠 state 守卫兜底）
    expect(writeBackBtn()).toBeNull();
    expect(screen.getByText('写回中…')).toBeInTheDocument();

    resolve({ written: true });
    expect(await screen.findByText('已写回范本库')).toBeInTheDocument();
    expect(writeBackBtn()).toBeNull();
  });

  it('written=false（无白名单 / 受 manual 保护）→ 落「本轮无可写回改动」', async () => {
    sedimentTemplateFillDefaults.mockResolvedValue({ written: false });
    render(<TemplateFillProgress state={finishedRow('task1')} streaming />);

    fireEvent.click(writeBackBtn()!);
    expect(await screen.findByText('本轮无可写回改动')).toBeInTheDocument();
  });

  it('抛错 → 红字「写回失败」+ title 带原因，再点可重试并成功', async () => {
    sedimentTemplateFillDefaults
      .mockRejectedValueOnce(
        new Error('该任务不支持写回范本库（缺少确认记录）'),
      )
      .mockResolvedValueOnce({ written: true });
    render(<TemplateFillProgress state={finishedRow('task1')} streaming />);

    fireEvent.click(writeBackBtn()!);
    const failed = await screen.findByRole('button', { name: /写回失败/ });
    expect(failed).toHaveAttribute(
      'title',
      '写回失败：该任务不支持写回范本库（缺少确认记录）（可再点重试）',
    );

    // 错误态保留按钮 → 重试走第二次调用
    fireEvent.click(failed);
    expect(await screen.findByText('已写回范本库')).toBeInTheDocument();
    expect(sedimentTemplateFillDefaults).toHaveBeenCalledTimes(2);
  });

  it('非 Error 抛出（字符串）→ 回落默认文案，不炸组件', async () => {
    sedimentTemplateFillDefaults.mockRejectedValue('boom');
    render(<TemplateFillProgress state={finishedRow('task1')} streaming />);

    fireEvent.click(writeBackBtn()!);
    const failed = await screen.findByRole('button', { name: /写回失败/ });
    expect(failed).toHaveAttribute('title', '写回失败：写回失败（可再点重试）');
  });
});
