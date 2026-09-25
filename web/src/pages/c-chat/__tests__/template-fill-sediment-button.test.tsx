// 写回范本库按钮（成稿行内）：只测接线与四态——按钮是否挂载、二次确认弹框、
// 确认后的 loading/done/empty/error 分支、错误态可重试、task_id 缺失时不渲染入口。
// 真实沉淀语义（白名单并集 / manual 保护 / 截断 / 幂等）由后端
// test/test_template_fill_sediment_api.py 覆盖，前端不重复造桩。
import type { ITemplateFillState } from '@/hooks/template-fill-stream';
import { sedimentTemplateFillDefaults } from '@/hooks/use-template-fill-request';
import TemplateFillProgress from '@/pages/c-chat/template-fill-progress';
import { fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('@/hooks/use-template-fill-request', () => ({
  sedimentTemplateFillDefaults: vi.fn(),
  confirmTemplateFill: vi.fn().mockResolvedValue(undefined),
  confirmTemplateFillSelect: vi.fn().mockResolvedValue(undefined),
}));

// live-preview 顶层 import docx-preview（重且与本用例无关），整体桩掉
vi.mock('@/pages/c-chat/template-fill-live-preview', () => ({
  __esModule: true,
  default: () => null,
}));

const sedimentMock = vi.mocked(sedimentTemplateFillDefaults);

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

const writeBackBtn = () =>
  screen.queryByRole('button', { name: /写回范本库|写回失败/ });
const confirmBtn = () => screen.getByRole('button', { name: '确认写回' });

/** 点击写回按钮并走到「确认写回」（二次确认弹框是请求的前置闸） */
async function clickThroughConfirm() {
  fireEvent.click(writeBackBtn()!);
  expect(screen.getByRole('alertdialog')).toBeInTheDocument();
  fireEvent.click(confirmBtn());
}

beforeEach(() => {
  sedimentMock.mockReset();
});

describe('TemplateFillSedimentButton 挂载口径', () => {
  it('task_id 存在 → 终态成稿行渲染「写回范本库」', () => {
    render(<TemplateFillProgress state={finishedRow('task1')} streaming />);
    expect(writeBackBtn()).toBeInTheDocument();
  });

  it('task_id 缺失（旧消息/脏数据）→ 不渲染入口，宁可不给也不发无效请求', () => {
    render(<TemplateFillProgress state={finishedRow(undefined)} streaming />);
    expect(writeBackBtn()).toBeNull();
    expect(sedimentMock).not.toHaveBeenCalled();
  });
});

describe('TemplateFillSedimentButton 二次确认', () => {
  it('点击按钮只弹确认框，不发写回请求', () => {
    render(<TemplateFillProgress state={finishedRow('task1')} streaming />);
    fireEvent.click(writeBackBtn()!);
    expect(screen.getByRole('alertdialog')).toBeInTheDocument();
    expect(sedimentMock).not.toHaveBeenCalled();
  });

  it('取消确认框：不发请求，按钮仍在', () => {
    render(<TemplateFillProgress state={finishedRow('task1')} streaming />);
    fireEvent.click(writeBackBtn()!);
    fireEvent.click(screen.getByRole('button', { name: '取消' }));
    expect(sedimentMock).not.toHaveBeenCalled();
    expect(writeBackBtn()).toBeInTheDocument();
  });
});

describe('TemplateFillSedimentButton 四态', () => {
  it('确认 → 按钮被「写回中…」替换（loading 期不可再点）→ 成功落「已写回范本库」', async () => {
    let resolve!: (v: { written: boolean }) => void;
    sedimentMock.mockReturnValue(
      new Promise<{ written: boolean }>((r) => {
        resolve = r;
      }),
    );
    render(<TemplateFillProgress state={finishedRow('task1')} streaming />);

    await clickThroughConfirm();
    expect(sedimentMock).toHaveBeenCalledWith('task1');
    // loading 期按钮整体卸载 → 第二次物理点击无从发生（非靠 state 守卫兜底）
    expect(writeBackBtn()).toBeNull();
    expect(screen.getByText('写回中…')).toBeInTheDocument();

    resolve({ written: true });
    expect(await screen.findByText('已写回范本库')).toBeInTheDocument();
    expect(writeBackBtn()).toBeNull();
  });

  it('written=false（无白名单 / 受 manual 保护）→ 落「本轮无可写回改动」', async () => {
    sedimentMock.mockResolvedValue({ written: false });
    render(<TemplateFillProgress state={finishedRow('task1')} streaming />);

    await clickThroughConfirm();
    expect(await screen.findByText('本轮无可写回改动')).toBeInTheDocument();
  });

  it('抛错 → 红字「写回失败」+ title 带原因，再确认可重试并成功', async () => {
    sedimentMock
      .mockRejectedValueOnce(
        new Error('该任务不支持写回范本库（缺少确认记录）'),
      )
      .mockResolvedValueOnce({ written: true });
    render(<TemplateFillProgress state={finishedRow('task1')} streaming />);

    await clickThroughConfirm();
    const failed = await screen.findByRole('button', { name: /写回失败/ });
    expect(failed).toHaveAttribute(
      'title',
      '写回失败：该任务不支持写回范本库（缺少确认记录）（可再点重试）',
    );

    // 错误态保留按钮 → 再确认走第二次调用
    await clickThroughConfirm();
    expect(await screen.findByText('已写回范本库')).toBeInTheDocument();
    expect(sedimentMock).toHaveBeenCalledTimes(2);
  });

  it('非 Error 抛出（字符串）→ 回落默认文案，不炸组件', async () => {
    sedimentMock.mockRejectedValue('boom');
    render(<TemplateFillProgress state={finishedRow('task1')} streaming />);

    await clickThroughConfirm();
    const failed = await screen.findByRole('button', { name: /写回失败/ });
    expect(failed).toHaveAttribute('title', '写回失败：写回失败（可再点重试）');
  });
});
