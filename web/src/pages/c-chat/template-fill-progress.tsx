// 范本填写进度卡片：c-chat 对话与 flow AI 对话区共用（设计 3.2「逻辑同构」的落地）。
// selected → 范本卡片行（填写点徽标）；filling → 进度行；filled → 即时下载条（不等其他范本）；
// failed → 降级文案行。全部文案中文，不走 i18n。
import type {
  ITemplateFillDownload,
  ITemplateFillState,
} from '@/hooks/template-fill-stream';
import { Download, FileText, Loader2 } from 'lucide-react';
import type { ReactNode } from 'react';

export default function TemplateFillProgress({
  state,
  onPreview,
  extraAction,
}: {
  state?: ITemplateFillState;
  /** 成稿点击预览（c-chat 传 setPreviewDoc；不传则文件名为纯文本） */
  onPreview?: (dl: ITemplateFillDownload) => void;
  /** 条目右侧附加动作（flow 传「存为流程版本」按钮） */
  extraAction?: (dl: ITemplateFillDownload) => ReactNode;
}) {
  if (!state?.templates?.length) return null;
  return (
    <div className="mt-2 space-y-1.5">
      {state.templates.map((t) => {
        if (t.status === 'selected') {
          return (
            <div
              key={t.template_id}
              className="flex items-center gap-2 rounded-lg border border-[#E5E5E5] bg-[#F5F5F5] px-3 py-2 text-xs text-[#000000]"
            >
              <FileText className="h-3.5 w-3.5 shrink-0" strokeWidth={2} />
              <span className="truncate">《{t.name}》</span>
              <span className="ml-auto shrink-0 rounded bg-[#EFF4FF] px-1.5 text-[#1a66fb]">
                {t.slot_count ?? 0} 个填写点
              </span>
            </div>
          );
        }
        if (t.status === 'filling') {
          return (
            <div
              key={t.template_id}
              className="flex items-center gap-2 px-3 py-2 text-xs text-[#525252]"
            >
              <Loader2 className="h-3.5 w-3.5 shrink-0 animate-spin text-[#1a66fb]" />
              《{t.name}》填写中 {t.done ?? 0}/{t.total ?? 0}
            </div>
          );
        }
        if (t.status === 'failed') {
          return (
            <div
              key={t.template_id}
              className="px-3 py-2 text-xs text-[#FAAD14]"
            >
              《{t.name}》填写失败（{t.error || '未知原因'}
              ），其余范本不受影响。
            </div>
          );
        }
        const dl = t.download;
        if (!dl) return null;
        return (
          <div
            key={t.template_id}
            className="flex items-center gap-2 rounded-lg border border-[#E5E5E5] bg-[#F5F5F5] px-3 py-2 text-xs text-[#000000]"
          >
            {onPreview ? (
              <button
                className="flex min-w-0 items-center gap-2 text-left transition-colors hover:text-[#1a66fb]"
                onClick={() => onPreview(dl)}
              >
                <FileText className="h-3.5 w-3.5 shrink-0" strokeWidth={2} />
                <span className="max-w-[280px] truncate">
                  {dl.filename || dl.name || '成稿'}
                </span>
              </button>
            ) : (
              <span className="flex min-w-0 items-center gap-2">
                <FileText className="h-3.5 w-3.5 shrink-0" strokeWidth={2} />
                <span className="max-w-[280px] truncate">
                  {dl.filename || dl.name || '成稿'}
                </span>
              </span>
            )}
            {extraAction?.(dl)}
            <a
              href={dl.url}
              target="_blank"
              rel="noopener noreferrer"
              className="ml-auto flex shrink-0 items-center gap-1 text-[#525252] transition-colors hover:text-[#000000]"
            >
              <Download className="h-3.5 w-3.5" strokeWidth={2} />
              下载
            </a>
          </div>
        );
      })}
    </div>
  );
}
