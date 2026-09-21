// ChatInputBox injectDoc 注入附件队列：编辑产出的新文件自动进队（旧文件剔除/
// nonce 防重/发送清空后仍可再次注入）。经 onUploadedFilesChange 回调断言队列内容。
import { TooltipProvider } from '@/components/ui/tooltip';
import ChatInputBox from '@/pages/c-chat/chat-input-box';
import { render } from '@testing-library/react';
import { createRef } from 'react';
import { describe, expect, it, vi } from 'vitest';

const baseProps = {
  value: '',
  setValue: vi.fn(),
  handleInputChange: vi.fn(),
  textareaRef: createRef<HTMLTextAreaElement>(),
  composingRef: { current: false },
  sendLoading: false,
  onSend: vi.fn(),
  onStop: vi.fn(),
  reviewMode: false,
  onToggleReview: vi.fn(),
};

describe('ChatInputBox injectDoc 注入附件队列', () => {
  it('注入的文档进入队列并经 onUploadedFilesChange 上报', async () => {
    const onUploadedFilesChange = vi.fn();
    render(
      <TooltipProvider>
        <ChatInputBox
          {...baseProps}
          onUploadedFilesChange={onUploadedFilesChange}
          injectDoc={{
            doc: { id: 'new1', name: '文档_编辑.docx' },
            removeId: 'old1',
            nonce: 1,
          }}
        />
      </TooltipProvider>,
    );
    // 初始空队列上报 + 注入后上报
    const last = onUploadedFilesChange.mock.calls.at(-1)?.[0];
    expect(last).toHaveLength(1);
    expect(last[0]).toMatchObject({ id: 'new1', name: '文档_编辑.docx' });
  });

  it('removeId 剔除旧文档，新文档追加到队尾', () => {
    const onUploadedFilesChange = vi.fn();
    // 用带初值的方式：先渲染注入第一个文件（模拟上传），再注入编辑版替换
    const { rerender } = render(
      <TooltipProvider>
        <ChatInputBox
          {...baseProps}
          onUploadedFilesChange={onUploadedFilesChange}
        />
      </TooltipProvider>,
    );
    rerender(
      <TooltipProvider>
        <ChatInputBox
          {...baseProps}
          onUploadedFilesChange={onUploadedFilesChange}
          injectDoc={{ doc: { id: 'a', name: 'a.docx' }, nonce: 1 }}
        />
      </TooltipProvider>,
    );
    rerender(
      <TooltipProvider>
        <ChatInputBox
          {...baseProps}
          onUploadedFilesChange={onUploadedFilesChange}
          injectDoc={{
            doc: { id: 'b', name: 'b_编辑.docx' },
            removeId: 'a',
            nonce: 2,
          }}
        />
      </TooltipProvider>,
    );
    const last = onUploadedFilesChange.mock.calls.at(-1)?.[0];
    expect(last).toHaveLength(1);
    expect(last[0].id).toBe('b');
  });

  it('同一 nonce 不重复追加', () => {
    const onUploadedFilesChange = vi.fn();
    const inject = { doc: { id: 'x', name: 'x.docx' }, nonce: 7 };
    const { rerender } = render(
      <TooltipProvider>
        <ChatInputBox
          {...baseProps}
          onUploadedFilesChange={onUploadedFilesChange}
          injectDoc={inject}
        />
      </TooltipProvider>,
    );
    rerender(
      <TooltipProvider>
        <ChatInputBox
          {...baseProps}
          onUploadedFilesChange={onUploadedFilesChange}
          injectDoc={inject}
        />
      </TooltipProvider>,
    );
    rerender(
      <TooltipProvider>
        <ChatInputBox
          {...baseProps}
          onUploadedFilesChange={onUploadedFilesChange}
          injectDoc={inject}
        />
      </TooltipProvider>,
    );
    const last = onUploadedFilesChange.mock.calls.at(-1)?.[0];
    expect(last.filter((d: { id: string }) => d.id === 'x')).toHaveLength(1);
  });

  it('injectDoc 为 null 时不改变队列', () => {
    const onUploadedFilesChange = vi.fn();
    render(
      <TooltipProvider>
        <ChatInputBox
          {...baseProps}
          onUploadedFilesChange={onUploadedFilesChange}
          injectDoc={null}
        />
      </TooltipProvider>,
    );
    expect(onUploadedFilesChange).toHaveBeenCalledWith([]);
  });
});
