// 范本填写「变化字段确认」卡片：画布 TemplateFill 预判出变化字段后挂起，
// 后端推 SSE confirm_pending 事件，此处渲染候选 checkbox（初始勾选 = AI 预判）
// 供用户调整并提交确认（POST /template/fill/confirm 写 Redis 唤醒画布继续）。
// 样式对齐 template-fill-progress.tsx 既有卡片；文案全中文，不走 i18n。
import { Button } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/checkbox';
import { Input } from '@/components/ui/input';
import type { ITemplateFillConfirmPending } from '@/hooks/template-fill-stream';
import { confirmTemplateFill } from '@/hooks/use-template-fill-request';
import { Loader2 } from 'lucide-react';
import { useState } from 'react';

export default function TemplateFillConfirmCard({
  pending,
  onSubmitted,
}: {
  pending: ITemplateFillConfirmPending;
  /** 提交成功后回调（可选）：使用方把 submitted 回写进流式状态，
   *  使归约器的 confirm_timeout 守卫（!submitted）真正生效 */
  onSubmitted?: () => void;
}) {
  // 各范本勾选的候选字段 key（初始 = AI 预判），Set 不可变更新保证 memo 感知
  const [checked, setChecked] = useState<Record<string, Set<string>>>(() =>
    Object.fromEntries(
      pending.templates.map((t) => [t.template_id, new Set(t.predicted)]),
    ),
  );
  // 直填值（key 为 `${template_id}:${candidate.key}`，留空 = 让 AI 重填）
  const [inputs, setInputs] = useState<Record<string, string>>({});
  const [submitting, setSubmitting] = useState(false);
  const [submitted, setSubmitted] = useState(false);
  const [error, setError] = useState('');

  const toggle = (templateId: string, key: string) => {
    setChecked((prev) => {
      const cur = new Set(prev[templateId] || []);
      if (cur.has(key)) {
        cur.delete(key);
      } else {
        cur.add(key);
      }
      return { ...prev, [templateId]: cur };
    });
  };

  const submit = async () => {
    if (submitting) return;
    setSubmitting(true);
    setError('');
    try {
      // 组装 decisions：changed=勾选 key 数组；values=直填非空项
      const decisions: Record<
        string,
        { changed: string[]; values: Record<string, string> }
      > = {};
      for (const t of pending.templates) {
        const changed = Array.from(checked[t.template_id] || []);
        const values: Record<string, string> = {};
        for (const c of t.candidates) {
          const v = inputs[`${t.template_id}:${c.key}`];
          if (v && v.trim() !== '') values[c.key] = v.trim();
        }
        decisions[t.template_id] = { changed, values };
      }
      await confirmTemplateFill(
        pending.task_id,
        pending.nonce || '',
        decisions,
      );
      setSubmitted(true);
      // 回写流式状态：迟到/并发的 confirm_timeout 不再把本卡片置 expired
      onSubmitted?.();
    } catch (e) {
      setError(e instanceof Error ? e.message : '确认提交失败');
    } finally {
      setSubmitting(false);
    }
  };

  // 已提交（本地成功或流式态回写）优先于超时判断：显示蓝字；
  // 若超时与提交同真，说明提交在途时后端已按超时继续 —— 降级提示，
  // 不再宣称「正在继续填写」（直填值可能已被后端按 AI 预判丢弃）
  if (submitted || pending.submitted) {
    return (
      <div className="rounded-lg border border-[#E5E5E5] bg-[#F5F5F5] px-3 py-2 text-xs text-[#1a66fb]">
        {pending.expired
          ? '已提交确认，但填写可能已按 AI 预判继续'
          : '已确认，正在继续填写…'}
      </div>
    );
  }

  // 超时且未提交：灰字只读，按钮隐藏
  if (pending.expired) {
    return (
      <div className="rounded-lg border border-[#E5E5E5] bg-[#F5F5F5] px-3 py-2 text-xs text-[#8C8C8C]">
        等待超时，已按 AI 预判字段继续填写。
      </div>
    );
  }

  return (
    <div className="space-y-2 rounded-lg border border-[#E5E5E5] bg-[#F5F5F5] px-3 py-2.5 text-xs">
      {pending.templates.map((t) => (
        <div key={t.template_id} className="space-y-1.5">
          <div className="font-medium text-[#000000]">《{t.name}》</div>
          {t.candidates.map((c) => {
            const inputKey = `${t.template_id}:${c.key}`;
            const isChecked = checked[t.template_id]?.has(c.key) ?? false;
            // 直填了值的字段后端无论是否勾选都直取生效，因此不划线（划线暗示"不变化"，与生效矛盾）
            const hasInput = (inputs[inputKey] || '').trim() !== '';
            return (
              <div key={c.key} className="flex items-center gap-2">
                <Checkbox
                  checked={isChecked}
                  onCheckedChange={() => toggle(t.template_id, c.key)}
                />
                <span
                  className={
                    isChecked || hasInput
                      ? 'text-[#000000]'
                      : 'text-[#8C8C8C] line-through'
                  }
                >
                  {c.name || c.key}
                  {c.default_value ? `（默认：${c.default_value}）` : ''}
                </span>
                <Input
                  className="ml-auto h-6 w-[140px] border-[#E5E5E5] bg-white text-xs"
                  placeholder="留空则 AI 重填"
                  value={inputs[inputKey] ?? ''}
                  onChange={(e) =>
                    setInputs((prev) => ({
                      ...prev,
                      [inputKey]: e.target.value,
                    }))
                  }
                />
              </div>
            );
          })}
        </div>
      ))}
      {error && <div className="text-[#E5484D]">{error}</div>}
      <div className="flex justify-end pt-0.5">
        {/* nonce 缺失 = 确认通道未就绪（Redis 唤醒令牌缺失），提交必然无效，前置禁用 */}
        {!pending.nonce && (
          <span className="mr-auto self-center text-[#8C8C8C]">
            确认通道未就绪，暂时无法提交
          </span>
        )}
        <Button
          size="sm"
          className="h-7 bg-[#1a66fb] px-3 text-xs text-white hover:bg-[#1557d6]"
          disabled={submitting || !pending.nonce}
          onClick={submit}
        >
          {submitting && <Loader2 className="mr-1 h-3 w-3 animate-spin" />}
          确认并继续填写
        </Button>
      </div>
    </div>
  );
}
