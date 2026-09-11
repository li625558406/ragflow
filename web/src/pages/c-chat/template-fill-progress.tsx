// 范本填写进度卡片：c-chat 对话与 flow AI 对话区共用（设计 3.2「逻辑同构」的落地）。
// selected → 范本卡片行（填写点徽标）；filling → 进度行；filled → 即时下载条（不等其他范本）；
// failed → 降级文案行。全部文案中文，不走 i18n。
import type {
  ITemplateFillDownload,
  ITemplateFillState,
} from '@/hooks/template-fill-stream';
import TemplateFillConfirmCard from '@/pages/c-chat/template-fill-confirm-card';
import TemplateFillLivePreview from '@/pages/c-chat/template-fill-live-preview';
import { Download, Eye, FileText, Loader2 } from 'lucide-react';
import { useState, type ReactNode } from 'react';

export default function TemplateFillProgress({
  state,
  onPreview,
  extraAction,
  onConfirmSubmitted,
}: {
  state?: ITemplateFillState;
  /** 成稿点击预览（c-chat 传 setPreviewDoc；不传则文件名为纯文本） */
  onPreview?: (dl: ITemplateFillDownload) => void;
  /** 条目右侧附加动作（flow 传「存为流程版本」按钮） */
  extraAction?: (dl: ITemplateFillDownload) => ReactNode;
  /** 确认卡片提交成功后回调（透传给卡片 onSubmitted）：使用方回写流式状态 */
  onConfirmSubmitted?: () => void;
}) {
  // 实时预览：当前打开正文预览的范本 id（存 id 而非对象快照，values 更新时
  // 从 state.templates 派生最新引用，预览槽位才能随 filling 事件实时填入）
  const [liveTplId, setLiveTplId] = useState<string>('');
  const liveTpl = state?.templates.find((t) => t.template_id === liveTplId);
  // 画布挂起确认卡片（confirm_pending）：附加块，置于范本行列表之上
  const confirmCard = state?.pendingConfirm && (
    <div className="mt-2">
      {/* task_id+nonce 唯一标识一轮确认：新一轮覆盖时 remount，重置卡片全部本地状态，避免多轮确认 stale */}
      <TemplateFillConfirmCard
        key={`${state.pendingConfirm.task_id}:${state.pendingConfirm.nonce || ''}`}
        pending={state.pendingConfirm}
        onSubmitted={onConfirmSubmitted}
      />
    </div>
  );
  if (!state?.templates?.length) return confirmCard || null;
  return (
    <>
      {confirmCard}
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
                <button
                  className="ml-auto flex shrink-0 items-center gap-1 text-[#1a66fb] transition-colors hover:text-[#1557d6]"
                  onClick={() => setLiveTplId(t.template_id)}
                >
                  <Eye className="h-3.5 w-3.5" />
                  实时预览
                </button>
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
      {liveTpl && (
        <TemplateFillLivePreview
          tpl={liveTpl}
          onClose={() => setLiveTplId('')}
        />
      )}
    </>
  );
}
