// 范本填写进度卡片：c-chat 对话与 flow AI 对话区共用（设计 3.2「逻辑同构」的落地）。
// selected → 范本卡片行（填写点徽标）；filling → 进度行；filled → 即时下载条（不等其他范本）；
// failed → 降级文案行。全部文案中文，不走 i18n。
import type {
  ITemplateFillDownload,
  ITemplateFillState,
} from '@/hooks/template-fill-stream';
import { useTemplateFillTaskPoll } from '@/hooks/use-template-fill-task-poll';
import TemplateFillConfirmCard from '@/pages/c-chat/template-fill-confirm-card';
import TemplateFillLivePreview from '@/pages/c-chat/template-fill-live-preview';
import { Download, Eye, FileText, Loader2 } from 'lucide-react';
import { useEffect, useState, type ReactNode } from 'react';

export default function TemplateFillProgress({
  state,
  streaming = true,
  onPreview,
  extraAction,
  onConfirmSubmitted,
  onLivePreviewOpenChange,
}: {
  state?: ITemplateFillState;
  /** 实时流式进行中（c-chat 传 sendLoading 派生标志；flow 传 live.busy）：
   *  流式期间 SSE 为准不轮询；false（历史恢复态）才启用断连重连轮询（设计 §5）。
   *  默认 true（fail-safe）：新调用点漏传 → 不轮询，而非静默开启请求放大 */
  streaming?: boolean;
  /** 成稿点击预览（c-chat 传 setPreviewDoc；不传则文件名为纯文本） */
  onPreview?: (dl: ITemplateFillDownload) => void;
  /** 条目右侧附加动作（flow 传「存为流程版本」按钮） */
  extraAction?: (dl: ITemplateFillDownload) => ReactNode;
  /** 确认卡片提交成功后回调（透传给卡片 onSubmitted）：使用方回写流式状态 */
  onConfirmSubmitted?: () => void;
  /** 实时预览抽屉开/关上报：使用方收缩左右布局为抽屉腾位（c-chat 用；flow 不传则无腾位） */
  onLivePreviewOpenChange?: (open: boolean) => void;
}) {
  // 实时预览：当前打开正文预览的范本 id（存 id 而非对象快照，values 更新时
  // 从 state.templates 派生最新引用，预览槽位才能随 filling 事件实时填入）
  // 打开预览的目标：范本 id + 可选定位字段 key（点击未填充汇总字段时携带）。
  // 存 id 不存对象快照的既有惯例不变（values 更新时从 templates 派生最新引用）
  const [liveTarget, setLiveTarget] = useState<{
    template_id: string;
    focusKey?: string;
  } | null>(null);
  // 断连重连：带 task_id 且 SSE 已停（历史恢复态，!streaming）的行走轮询 override；
  // 实时流式期间（streaming=true）SSE 为准不轮询，避免请求被 SSE 事件放大
  const mergedTemplates = useTemplateFillTaskPoll(state?.templates, !streaming);
  const mergedState = mergedTemplates
    ? ({ ...(state || {}), templates: mergedTemplates } as ITemplateFillState)
    : state;
  const liveTpl = mergedState?.templates.find(
    (t) => t.template_id === liveTarget?.template_id,
  );
  // 抽屉开/关上报（布局腾位联动）；liveTplId 存在但范本行已被新一轮清空时视为关闭
  useEffect(() => {
    onLivePreviewOpenChange?.(Boolean(liveTpl));
  }, [liveTpl, onLivePreviewOpenChange]);
  // 画布挂起确认卡片（confirm_pending）：附加块，置于范本行列表之上
  const confirmCard = mergedState?.pendingConfirm && (
    <div className="mt-2">
      {/* task_id+nonce 唯一标识一轮确认：新一轮覆盖时 remount，重置卡片全部本地状态，避免多轮确认 stale */}
      <TemplateFillConfirmCard
        key={`${mergedState.pendingConfirm.task_id}:${mergedState.pendingConfirm.nonce || ''}`}
        pending={mergedState.pendingConfirm}
        onSubmitted={onConfirmSubmitted}
      />
    </div>
  );
  if (!mergedState?.templates?.length) return confirmCard || null;
  return (
    <>
      {confirmCard}
      <div className="mt-2 space-y-1.5">
        {mergedState.templates.map((t) => {
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
                {/* 范本正文预览：未开填时也可查看范本内容（占位符显示为虚线待填槽位） */}
                <button
                  className="flex shrink-0 items-center gap-1 text-[#1a66fb] transition-colors hover:text-[#1557d6]"
                  onClick={() => setLiveTarget({ template_id: t.template_id })}
                >
                  <Eye className="h-3.5 w-3.5" />
                  查看范本
                </button>
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
                  onClick={() => setLiveTarget({ template_id: t.template_id })}
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
            <div key={t.template_id} className="space-y-1">
              <div className="flex items-center gap-2 rounded-lg border border-[#E5E5E5] bg-[#F5F5F5] px-3 py-2 text-xs text-[#000000]">
                {onPreview ? (
                  <button
                    className="flex min-w-0 items-center gap-2 text-left transition-colors hover:text-[#1a66fb]"
                    onClick={() => onPreview(dl)}
                  >
                    <FileText
                      className="h-3.5 w-3.5 shrink-0"
                      strokeWidth={2}
                    />
                    <span className="max-w-[280px] truncate">
                      {dl.filename || dl.name || '成稿'}
                    </span>
                  </button>
                ) : (
                  <span className="flex min-w-0 items-center gap-2">
                    <FileText
                      className="h-3.5 w-3.5 shrink-0"
                      strokeWidth={2}
                    />
                    <span className="max-w-[280px] truncate">
                      {dl.filename || dl.name || '成稿'}
                    </span>
                  </span>
                )}
                {extraAction?.(dl)}
                {/* 填写内容回看入口：filled 后 values 仍在（filling 事件累积），
                    打开正文预览可回看蓝色填入值 */}
                {t.values && Object.keys(t.values).length > 0 && (
                  <button
                    className="flex shrink-0 items-center gap-1 text-[#1a66fb] transition-colors hover:text-[#1557d6]"
                    onClick={() =>
                      setLiveTarget({ template_id: t.template_id })
                    }
                  >
                    <Eye className="h-3.5 w-3.5" />
                    查看填写内容
                  </button>
                )}
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
              {/* 未填充汇总行：必填红/选填灰，点击带 focusKey 打开预览定位 */}
              {t.unfilled && t.unfilled.length > 0 && (
                <div className="flex flex-wrap items-center gap-x-1 gap-y-0.5 px-3 py-1 text-xs">
                  <span className="text-[#FAAD14]">
                    ⚠ {t.unfilled.length} 个填写点未填充：
                  </span>
                  {t.unfilled.map((f, i) => (
                    <span
                      key={`${f.key}-${i}`}
                      className="flex items-center gap-1"
                    >
                      <button
                        className={
                          f.required
                            ? 'text-[#F5222D] underline decoration-dotted underline-offset-2 transition-colors hover:text-[#CF1322]'
                            : 'text-[#8C8C8C] underline decoration-dotted underline-offset-2 transition-colors hover:text-[#525252]'
                        }
                        title={`定位到文档中的「${f.name}」`}
                        onClick={() =>
                          setLiveTarget({
                            template_id: t.template_id,
                            focusKey: f.key,
                          })
                        }
                      >
                        {f.name}
                      </button>
                      {f.required && (
                        <span className="rounded bg-[#FFF1F0] px-1 text-[10px] text-[#F5222D]">
                          必填
                        </span>
                      )}
                      {i < (t.unfilled?.length ?? 0) - 1 && (
                        <span className="text-[#8C8C8C]">·</span>
                      )}
                    </span>
                  ))}
                </div>
              )}
            </div>
          );
        })}
      </div>
      {liveTpl && (
        <TemplateFillLivePreview
          tpl={liveTpl}
          focusKey={liveTarget?.focusKey}
          onClose={() => setLiveTarget(null)}
        />
      )}
    </>
  );
}
