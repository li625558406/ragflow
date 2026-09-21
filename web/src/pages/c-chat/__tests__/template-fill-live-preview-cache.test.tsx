// 「查看填写内容」实时预览渲染缓存（2026-09-21）：
// ①docx 渲染产物按 blob 缓存，抽屉关开 appendChild 重放，renderAsync 只跑一次，
//   占位符 span 经 rebuildPlaceholderSpans 重建 + updateDocxHighlight 重涂；
// ②>2.5MB 改派生判定：超大门槛首帧即生效，不再「先白渲染一遍再翻转文本分支」。
import type { ITemplateFillTemplate } from '@/hooks/template-fill-stream';
import { useTemplateFillFile } from '@/hooks/use-template-fill-request';
import TemplateFillLivePreview from '@/pages/c-chat/template-fill-live-preview';
import { render, screen, waitFor } from '@testing-library/react';
import { renderAsync } from 'docx-preview';
import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('docx-preview', () => ({ renderAsync: vi.fn() }));

const previewData = vi.hoisted(() => ({
  code: 0,
  data: {
    file_type: 'docx',
    items: [
      {
        index: 0,
        text: '招标人：{{tenderer_name}}',
        addr: 'A1',
        placeholder_key: 'tenderer_name',
      },
    ],
  },
}));

vi.mock('@/hooks/use-template-fill-request', () => ({
  useTemplateFillPreview: vi.fn(() => ({
    data: previewData,
    isLoading: false,
  })),
  useTemplateFillFile: vi.fn(() => ({
    data: undefined,
    isLoading: false,
    error: undefined,
  })),
  fetchTemplateFillTaskProgress: vi.fn(() => Promise.resolve(null)),
}));

const mockRenderAsync = vi.mocked(renderAsync);
const mockUseTemplateFillFile = vi.mocked(useTemplateFillFile);

beforeEach(() => {
  vi.clearAllMocks();
  mockRenderAsync.mockImplementation(async (_b: Blob, el: HTMLElement) => {
    el.innerHTML = '<section><p>招标人：{{tenderer_name}}</p></section>';
  });
  mockUseTemplateFillFile.mockReturnValue({
    data: new Blob(['docx-bytes-1']),
    isLoading: false,
    error: undefined,
  } as any);
});

// values 可传参覆盖：progress mock 恒返回 null → authoritative 恒空 →
// values 完全来自 tpl.values（组件 154-157 行口径），改 tpl 即可驱动重涂。
const tpl = (values?: Record<string, string>) =>
  ({
    template_id: 'tpl1',
    name: '范本',
    status: 'filled',
    task_id: 'task1',
    values: values ?? { tenderer_name: '石狮市交通建设公司' },
  }) as ITemplateFillTemplate;

const renderPreview = (values?: Record<string, string>) =>
  render(<TemplateFillLivePreview tpl={tpl(values)} onClose={() => {}} />);

describe('TemplateFillLivePreview 渲染缓存', () => {
  it('关开重放：renderAsync 只调一次，占位符 span 重建后 values 照常显示', async () => {
    const first = renderPreview();
    expect(await screen.findByText('石狮市交通建设公司')).toBeTruthy();
    expect(mockRenderAsync).toHaveBeenCalledTimes(1);
    first.unmount();
    renderPreview();
    await waitFor(() =>
      expect(screen.getByText('石狮市交通建设公司')).toBeTruthy(),
    );
    expect(mockRenderAsync).toHaveBeenCalledTimes(1);
  });

  it('关闭期间 values 变更：重开回放按新 values 重涂', async () => {
    // 用例 1 两次渲染 values 相同，「重开后文本可见」可能是缓存树旧值恰好正确；
    // 此处钉住重涂链路：回放命中缓存（renderAsync 不再跑）后 updateDocxHighlight
    // 必须按当前 values 覆盖缓存树里的旧值文本。
    const first = renderPreview();
    expect(await screen.findByText('石狮市交通建设公司')).toBeTruthy();
    expect(mockRenderAsync).toHaveBeenCalledTimes(1);
    first.unmount();
    renderPreview({ tenderer_name: '龙岩市城建投资集团' });
    expect(await screen.findByText('龙岩市城建投资集团')).toBeTruthy();
    expect(screen.queryByText('石狮市交通建设公司')).toBeNull();
    expect(mockRenderAsync).toHaveBeenCalledTimes(1);
  });

  it('>2.5MB 派生判定首帧生效：不触发 renderAsync 直接文本降级', async () => {
    mockUseTemplateFillFile.mockReturnValue({
      data: new Blob(['x'.repeat(3 * 1024 * 1024)]),
      isLoading: false,
      error: undefined,
    } as any);
    renderPreview();
    expect(
      await screen.findByText('文档较大，已用文本预览保障流畅'),
    ).toBeTruthy();
    expect(screen.getByText(/招标人：/)).toBeTruthy();
    expect(mockRenderAsync).not.toHaveBeenCalled();
  });
});
