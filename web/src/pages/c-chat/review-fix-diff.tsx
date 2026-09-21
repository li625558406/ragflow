// 修复对比 + 回退/确认操作：进度卡修复结果区与审核面板 AiCard 共用（防两处漂移）。
// 数据源：annotation.patch = 修复轮实际落地的 {find, replace}——「确实修好了」那一刻
// 由后端 _settle_annotations 同笔落库，是对比与回退的唯一权威记录。
import {
  useRevertAnnotation,
  useUpdateAnnotationStatus,
} from '@/hooks/use-file-review-request';
import { useState } from 'react';

/** 修复前(红)/修复后(绿)对比块。multiline 补丁按行渲染（后端 _apply_multiline_patch
 * 只改变更行，find/replace 行数恒相等，逐行对齐展示即真实差异）。 */
export function FixDiffView({
  patch,
}: {
  patch: { find: string; replace: string };
}) {
  const findLines = (patch.find ?? '').split('\n');
  const replaceLines = (patch.replace ?? '').split('\n');
  return (
    <div className="mt-1 space-y-0.5 rounded border border-[#E5E5E5] bg-white p-1.5">
      <div className="text-[10px] text-[#8C8C8C]">修复前 → 修复后</div>
      <div>
        {findLines.map((l, i) => (
          <div
            key={`f-${i}`}
            className="whitespace-pre-wrap break-all rounded-sm bg-[#FFF1F0] px-1 text-[#820014] line-through"
          >
            {l || ' '}
          </div>
        ))}
      </div>
      <div>
        {replaceLines.map((l, i) => (
          <div
            key={`r-${i}`}
            className="whitespace-pre-wrap break-all rounded-sm bg-[#F6FFED] px-1 text-[#237804]"
          >
            {l || ' '}
          </div>
        ))}
      </div>
    </div>
  );
}

/** 回退 / 确认保留操作行。仅在 status='fixed' 且有 patch 时渲染：
 * 回退成功后批注回 open（无 patch），本组件随之消失；确认保留置 resolved。 */
export function FixActions({
  fileId,
  annotationId,
}: {
  fileId: string;
  annotationId: string;
}) {
  const revertMutation = useRevertAnnotation(fileId);
  const statusMutation = useUpdateAnnotationStatus(fileId);
  const [err, setErr] = useState('');
  if (!annotationId) return null;

  const busy = revertMutation.isPending || statusMutation.isPending;
  const onRevert = () => {
    if (!window.confirm('回退后文档将恢复该处原文（产生新版本），确定回退？'))
      return;
    setErr('');
    revertMutation.mutate(annotationId, {
      onError: (e) => setErr(e instanceof Error ? e.message : '回退失败'),
    });
  };
  const onConfirm = () => {
    setErr('');
    statusMutation.mutate(
      { annotationId, status: 'resolved' },
      { onError: (e) => setErr(e instanceof Error ? e.message : '操作失败') },
    );
  };

  return (
    <div className="mt-1">
      <div className="flex items-center gap-2">
        <button
          type="button"
          disabled={busy}
          className="rounded border border-[#FF4D4F] px-1.5 py-px text-[#FF4D4F] hover:bg-[#FFF1F0] disabled:opacity-50"
          onClick={onRevert}
        >
          回退
        </button>
        <button
          type="button"
          disabled={busy}
          className="rounded border border-[#52C41A] px-1.5 py-px text-[#52C41A] hover:bg-[#F6FFED] disabled:opacity-50"
          onClick={onConfirm}
        >
          确认保留
        </button>
      </div>
      {err && <div className="mt-0.5 text-[#F5222D]">{err}</div>}
    </div>
  );
}
