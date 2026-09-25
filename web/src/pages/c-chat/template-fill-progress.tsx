// 范本填写进度卡片：c-chat 对话与 flow AI 对话区共用（设计 3.2「逻辑同构」的落地）。
// selected → 范本卡片行（填写点徽标）；filling → 进度行；filled → 即时下载条（不等其他范本）；
// failed → 降级文案行。全部文案中文，不走 i18n。
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from '@/components/ui/alert-dialog';
import message from '@/components/ui/message';
import {
  buildFilledRows,
  type ITemplateFillDownload,
  type ITemplateFillFilledRow,
  type ITemplateFillState,
  type ITemplateFillUnfilled,
} from '@/hooks/template-fill-stream';
import { sedimentTemplateFillDefaults } from '@/hooks/use-template-fill-request';
import { useTemplateFillTaskPoll } from '@/hooks/use-template-fill-task-poll';
import TemplateFillConfirmCard, {
  TemplateSelectConfirmCard,
} from '@/pages/c-chat/template-fill-confirm-card';
import TemplateFillLivePreview from '@/pages/c-chat/template-fill-live-preview';
import { getAuthorization } from '@/utils/authorization-util';
import { downloadFileFromBlob } from '@/utils/file-util';
import {
  Check,
  ChevronDown,
  Download,
  Eye,
  FileText,
  Loader2,
  RefreshCw,
  Save,
} from 'lucide-react';
import { useEffect, useState, type ReactNode } from 'react';

/** 成稿下载：fetch 取 Blob 后走 a.download 落盘（带文件名）。
 *  **禁止**改成 window.open / a[href] 直链：/agents/download 端点无
 *  Content-Disposition 头，新开页签会把 docx（zip 二进制）当文本渲染成乱码
 *  （用户实测：流程页签点下载 → 新页签全是乱码）。供 c-chat 对话页
 *  msg.downloads 行与本组件成稿行共用。 */
export async function downloadTemplateFillResult(
  dl: Pick<ITemplateFillDownload, 'url' | 'filename' | 'name'>,
): Promise<void> {
  if (!dl.url) {
    message.error('下载链接缺失，无法下载');
    return;
  }
  try {
    const resp = await fetch(dl.url, {
      headers: { Authorization: getAuthorization() },
    });
    if (!resp.ok) throw new Error(`下载失败 ${resp.status}`);
    downloadFileFromBlob(await resp.blob(), dl.filename || dl.name || '成稿');
  } catch (e) {
    message.error(e instanceof Error ? e.message : '下载失败，请稍后重试');
  }
}

/** 已填充填写点折叠清单（终态成稿行内）：默认收起、**展开才挂载** DOM
 *  ——244 项的常挂 DOM 无意义（不是 hidden / max-h-0）。中性色，与上方
 *  未填充汇总（琥珀开关/红必填标、同样默认收起）形成主次对比：待办醒目、已完成中性。
 *  点击字段名沿用未填充汇总的 liveTarget+focusKey 定位链路。
 *  必须是独立子组件：模板行在 map 回调里渲染，回调内不能用 useState。 */
function TemplateFillFilledList({
  rows,
  onLocate,
}: {
  rows: ITemplateFillFilledRow[];
  onLocate: (key: string) => void;
}) {
  const [open, setOpen] = useState(false);
  // 全部填满时后端不下发 filled（空列表归一为缺省）→ 这里天然不渲染；
  // 双保险防空数组（旧后端/脏数据）渲染出「已填充 0 个」
  if (!rows.length) return null;
  return (
    <div className="px-3 py-1 text-xs">
      <button
        className="flex items-center gap-1 text-[#8C8C8C] transition-colors hover:text-[#525252]"
        onClick={() => setOpen((v) => !v)}
      >
        <ChevronDown
          className={`h-3 w-3 shrink-0 transition-transform ${open ? '' : '-rotate-90'}`}
        />
        已填充 {rows.length} 个填写点
      </button>
      {open && (
        <div className="mt-0.5 space-y-0.5 border-l border-[#E5E5E5] pl-3">
          {rows.map((r) => (
            // key 唯一性由 validate_placeholders / _merge_detection 保证
            <div key={r.key} className="flex items-center gap-1.5">
              <span className="shrink-0 text-[#8C8C8C]">·</span>
              <button
                className="shrink-0 text-[#525252] underline decoration-dotted underline-offset-2 transition-colors hover:text-[#1a66fb]"
                title={`定位到文档中的「${r.name}」（${r.key}）`}
                onClick={() => onLocate(r.key)}
              >
                {r.name}
              </button>
              <span className="shrink-0 text-[#8C8C8C]">：</span>
              {/* 值用 CSS truncate 截断展示，不做 JS 切片——保留完整值供复制/悬浮查看。
                  w-0 必须有：min-w-0 只允许「用的时候」缩，不改 intrinsic 贡献——
                  truncate 的 nowrap 长值仍会把祖先 min-content 撑到近万 px（流程页
                  对话区被撑宽、下载按钮被推出屏幕事故根因）；w-0 让 flex-basis=0
                  才真正切断传播 */}
              <span
                className="w-0 min-w-0 flex-1 truncate text-[#000000]"
                title={`${r.name}（${r.key}）：${r.value}`}
              >
                {r.value}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

/** 未填充填写点折叠清单（终态成稿行内）：与已填充清单同级别同交互——默认收起、
 *  展开才挂 DOM，点击字段名走 liveTarget+focusKey 定位链路。开关文字保留琥珀色
 *  ⚠ 与已填充的中性色形成主次对比：待办醒目、已完成收起。 */
function TemplateFillUnfilledList({
  rows,
  onLocate,
}: {
  rows: ITemplateFillUnfilled[];
  onLocate: (key: string) => void;
}) {
  const [open, setOpen] = useState(false);
  if (!rows.length) return null;
  return (
    <div className="px-3 py-1 text-xs">
      <button
        className="flex items-center gap-1 text-[#FAAD14] transition-colors hover:text-[#D48806]"
        onClick={() => setOpen((v) => !v)}
      >
        <ChevronDown
          className={`h-3 w-3 shrink-0 transition-transform ${open ? '' : '-rotate-90'}`}
        />
        ⚠ {rows.length} 个填写点未填充
      </button>
      {open && (
        <div className="mt-0.5 space-y-0.5 border-l border-[#E5E5E5] pl-3">
          {rows.map((f) => (
            <div
              key={`${f.key}-${f.name}`}
              className="flex items-center gap-1.5"
            >
              <span className="shrink-0 text-[#8C8C8C]">·</span>
              <button
                className={
                  f.required
                    ? 'shrink-0 text-[#F5222D] underline decoration-dotted underline-offset-2 transition-colors hover:text-[#CF1322]'
                    : 'shrink-0 text-[#8C8C8C] underline decoration-dotted underline-offset-2 transition-colors hover:text-[#525252]'
                }
                title={`定位到文档中的「${f.name}」`}
                onClick={() => onLocate(f.key)}
              >
                {f.name}
              </button>
              {f.required && (
                <span className="shrink-0 rounded bg-[#FFF1F0] px-1 text-[10px] text-[#F5222D]">
                  必填
                </span>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

/** 写回范本库按钮（终态成稿行内）：把本轮**用户显式确认/直填**的字段值沉淀为该
 *  范本版本的默认值（placeholders[].default_value，source=auto）。范本文件与
 *  {{key}} 占位符原样不动——下一轮检索+LLM 仍是权威，默认值只在字段缺值时兜底。
 *
 *  填写 pipeline 不再自动沉淀：不点这个按钮，下一轮就按当前流程重新检索+LLM 填，
 *  不会出现「新流程被上一轮内容占满」。幂等——重复点第二次回落「本轮无可写回改动」。
 *
 *  必须是独立子组件：模板行在 map 回调里渲染，回调内不能用 useState。 */
function TemplateFillSedimentButton({ taskId }: { taskId: string }) {
  const [state, setState] = useState<
    'idle' | 'loading' | 'done' | 'empty' | 'error'
  >('idle');
  const [errMsg, setErrMsg] = useState('');

  const onWriteBack = async () => {
    if (state === 'loading') return;
    setState('loading');
    try {
      const { written } = await sedimentTemplateFillDefaults(taskId);
      setState(written ? 'done' : 'empty');
    } catch (e) {
      setErrMsg(e instanceof Error ? e.message : '写回失败');
      setState('error');
    }
  };

  if (state === 'loading') {
    return (
      <span className="flex shrink-0 items-center gap-1 text-[#8C8C8C]">
        <Loader2 className="h-3.5 w-3.5 animate-spin" />
        写回中…
      </span>
    );
  }
  if (state === 'done') {
    return (
      <span
        className="flex shrink-0 items-center gap-1 text-[#52C41A]"
        title="已沉淀为本范本默认值，范本文件与 {{占位符}} 未改动"
      >
        <Check className="h-3.5 w-3.5" />
        已写回范本库
      </span>
    );
  }
  if (state === 'empty') {
    return (
      <span
        className="flex shrink-0 items-center gap-1 text-[#8C8C8C]"
        title="本轮没有可沉淀的改动：确认卡未勾选/直填任何字段，或这些字段已有范本库里人工维护的默认值（不会被自动覆盖）"
      >
        <Check className="h-3.5 w-3.5" />
        本轮无可写回改动
      </span>
    );
  }
  return (
    // 二次确认：写回会把本轮字段值沉淀为范本默认值（影响后续所有轮次的兜底值），
    // 误触代价高，先弹框确认再执行
    <AlertDialog>
      <AlertDialogTrigger asChild>
        <button
          className={`flex shrink-0 items-center gap-1 transition-colors ${
            state === 'error'
              ? 'text-[#F5222D] hover:text-[#CF1322]'
              : 'text-[#1a66fb] hover:text-[#1557d6]'
          }`}
          title={
            state === 'error'
              ? `写回失败：${errMsg}（可再点重试）`
              : '写回范本库：把本轮确认/直填的字段沉淀为范本默认值，不改变范本文件本身'
          }
        >
          {state === 'error' ? (
            <RefreshCw className="h-3.5 w-3.5" />
          ) : (
            <Save className="h-3.5 w-3.5" />
          )}
          {state === 'error' ? '写回失败' : '写回范本库'}
        </button>
      </AlertDialogTrigger>
      <AlertDialogContent className="bg-bg-base">
        <AlertDialogHeader>
          <AlertDialogTitle>写回范本库</AlertDialogTitle>
          <AlertDialogDescription>
            确定把本轮确认/直填的字段值沉淀为该范本的默认值？范本文件与占位符本身不会被改动；写回后可随时在范本库详情中人工修改。
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel>取消</AlertDialogCancel>
          <AlertDialogAction
            className="bg-[#1a66fb] text-white hover:bg-[#1557d6]"
            onClick={onWriteBack}
          >
            确认写回
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}

export default function TemplateFillProgress({
  state,
  streaming = true,
  onPreview,
  extraAction,
  onConfirmSubmitted,
  onSelectSubmitted,
  onLivePreviewOpenChange,
  forceClosedLivePreview,
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
  /** 多范本选择卡片提交成功后回调（透传给卡片 onSubmitted）：使用方回写流式状态 */
  onSelectSubmitted?: () => void;
  /** 实时预览抽屉开/关上报：使用方收缩左右布局为抽屉腾位（c-chat 用；flow 不传则无腾位） */
  onLivePreviewOpenChange?: (open: boolean) => void;
  /** 强制收起实时预览（预览内存治理，设计 2026-09-16）：流程页签常驻挂载的隐藏
   *  详情（visible=false）透传 true——docx-preview 整本文档 DOM 树随隐藏实例驻留，
   *  多流程切换叠加会 OOM；保证任意时刻至多一棵大文档 DOM 树 */
  forceClosedLivePreview?: boolean;
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
  // 抽屉开/关上报（布局腾位联动）；liveTarget 存在但范本行已被新一轮清空时视为关闭
  useEffect(() => {
    onLivePreviewOpenChange?.(Boolean(liveTpl));
  }, [liveTpl, onLivePreviewOpenChange]);
  // 隐藏详情强制收预览：liveTarget 清空后 liveTpl 派生为 undefined，预览组件卸载，
  // 整本 docx DOM 树随之释放
  useEffect(() => {
    if (forceClosedLivePreview) setLiveTarget(null);
  }, [forceClosedLivePreview]);
  // 画布挂起确认卡片（confirm_pending / select_pending）：附加块，置于范本行列表之上
  const confirmCard = mergedState?.pendingConfirm && (
    <div className="mt-2">
      {/* task_id+nonce 唯一标识一轮确认：新一轮覆盖时 remount，重置卡片全部本地状态，避免多轮确认 stale */}
      <TemplateFillConfirmCard
        key={`${mergedState.pendingConfirm.task_id}:${mergedState.pendingConfirm.nonce || ''}`}
        pending={mergedState.pendingConfirm}
        onSubmitted={onConfirmSubmitted}
        onLocate={(templateId, key) =>
          setLiveTarget({ template_id: templateId, focusKey: key })
        }
      />
    </div>
  );
  // 多范本选择确认卡片（select_pending）：先于字段确认出现（后端先选范本再确认字段）
  const selectCard = mergedState?.pendingSelect && (
    <div className="mt-2">
      <TemplateSelectConfirmCard
        key={`${mergedState.pendingSelect.task_id}:${mergedState.pendingSelect.select_nonce || ''}`}
        pending={mergedState.pendingSelect}
        onSubmitted={onSelectSubmitted}
      />
    </div>
  );
  if (!mergedState?.templates?.length) return confirmCard || selectCard || null;
  return (
    <>
      {selectCard}
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
                <button
                  className="ml-auto flex shrink-0 items-center gap-1 text-[#525252] transition-colors hover:text-[#000000]"
                  onClick={() => void downloadTemplateFillResult(dl)}
                >
                  <Download className="h-3.5 w-3.5" strokeWidth={2} />
                  下载
                </button>
                {/* 写回范本库：只有点这里才把本轮成果沉淀为范本默认值。
                    task_id 缺失（旧消息/脏数据）则不渲染，宁可不给入口也不发无效请求 */}
                {t.task_id && <TemplateFillSedimentButton taskId={t.task_id} />}
              </div>
              {/* 未填充汇总：默认折叠（与已填充清单同级别），必填红/选填灰，点击定位 */}
              {t.unfilled && t.unfilled.length > 0 && (
                <TemplateFillUnfilledList
                  rows={t.unfilled}
                  onLocate={(key) =>
                    setLiveTarget({ template_id: t.template_id, focusKey: key })
                  }
                />
              )}
              {/* 已填充汇总：默认折叠（展开才建 DOM），点击字段名走同一套定位链路。
                  放在未填充汇总之后：待办在上、已完成在下 */}
              <TemplateFillFilledList
                rows={buildFilledRows(t)}
                onLocate={(key) =>
                  setLiveTarget({ template_id: t.template_id, focusKey: key })
                }
              />
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
