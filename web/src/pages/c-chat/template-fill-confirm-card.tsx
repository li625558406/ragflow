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
}: {
  pending: ITemplateFillConfirmPending;
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
    } catch (e) {
      setError(e instanceof Error ? e.message : '确认提交失败');
    } finally {
      setSubmitting(false);
    }
  };

  // 超时且未提交：灰字只读，按钮隐藏
  if (pending.expired && !submitted) {
    return (
      <div className="rounded-lg border border-[#E5E5E5] bg-[#F5F5F5] px-3 py-2 text-xs text-[#8C8C8C]">
        等待超时，已按 AI 预判字段继续填写。
      </div>
    );
  }

  // 已提交：蓝字进行中提示
  if (submitted) {
    return (
      <div className="rounded-lg border border-[#E5E5E5] bg-[#F5F5F5] px-3 py-2 text-xs text-[#1a66fb]">
        已确认，正在继续填写…
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
            return (
              <div key={c.key} className="flex items-center gap-2">
                <Checkbox
                  checked={isChecked}
                  onCheckedChange={() => toggle(t.template_id, c.key)}
                />
                <span
                  className={
                    isChecked ? 'text-[#000000]' : 'text-[#8C8C8C] line-through'
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
        <Button
          size="sm"
          className="h-7 bg-[#1a66fb] px-3 text-xs text-white hover:bg-[#1557d6]"
          disabled={submitting}
          onClick={submit}
        >
          {submitting && <Loader2 className="mr-1 h-3 w-3 animate-spin" />}
          确认并继续填写
        </Button>
      </div>
    </div>
  );
}
