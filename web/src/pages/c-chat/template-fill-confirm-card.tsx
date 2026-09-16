// 范本填写「填写字段确认」卡片：画布 TemplateFill 推 SSE confirm_pending 事件后挂起，
// 此处渲染全部 LLM 填写点候选 checkbox（有默认值字段初始勾选 = AI 预判变化；
// 无默认值字段默认勾选 = 维持交给 AI 填）供用户调整并提交确认
// （POST /template/fill/confirm 写 Redis 唤醒画布继续；不勾 = 有默认值用默认、无默认值留空）。
// 样式对齐 template-fill-progress.tsx 既有卡片；文案全中文，不走 i18n。
import { Button } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/checkbox';
import { Input } from '@/components/ui/input';
import type {
  ITemplateFillConfirmPending,
  ITemplateFillSelectPending,
} from '@/hooks/template-fill-stream';
import {
  confirmTemplateFill,
  confirmTemplateFillSelect,
} from '@/hooks/use-template-fill-request';
import { ChevronDown, Loader2 } from 'lucide-react';
import { useState } from 'react';

export default function TemplateFillConfirmCard({
  pending,
  onSubmitted,
  onLocate,
}: {
  pending: ITemplateFillConfirmPending;
  /** 提交成功后回调（可选）：使用方把 submitted 回写进流式状态，
   *  使归约器的 confirm_timeout 守卫（!submitted）真正生效 */
  onSubmitted?: () => void;
  /** 点击字段名定位到预览文档位置（可选）：父组件接 setLiveTarget({template_id, focusKey})；
   *  未传时字段名保持纯文本（向后兼容） */
  onLocate?: (templateId: string, key: string) => void;
}) {
  // 各范本勾选的候选字段 key（初始 = AI 预判 ∪ 无默认值字段——后者维持交给 AI），
  // Set 不可变更新保证 memo 感知
  const [checked, setChecked] = useState<Record<string, Set<string>>>(() =>
    Object.fromEntries(
      pending.templates.map((t) => [
        t.template_id,
        new Set([
          ...t.predicted,
          ...t.candidates.filter((c) => !c.default_value).map((c) => c.key),
        ]),
      ]),
    ),
  );
  // 直填值（key 为 `${template_id}:${candidate.key}`，留空 = 让 AI 重填）。
  // 增量模式下后端 cands 携带 direct_value（LLM 从用户原话抽出的 direct），
  // 这里预填到输入框：用户提交时若没改，values_raw 等于 direct_value，
  // 后端 ov 分支走「非空 user input 覆盖 fallback」路径——值与 fallback 一致，
  // 渲染照常；同时让用户在卡里能看到/编辑 AI 抽取的结果再确认。
  const [inputs, setInputs] = useState<Record<string, string>>(() => {
    const init: Record<string, string> = {};
    for (const t of pending.templates) {
      for (const c of t.candidates) {
        if (c.direct_value) {
          init[`${t.template_id}:${c.key}`] = c.direct_value;
        }
      }
    }
    return init;
  });
  const [submitting, setSubmitting] = useState(false);
  const [submitted, setSubmitted] = useState(false);
  const [error, setError] = useState('');
  // 折叠态：大范本（数百填写点）默认收起为一行摘要，点开调整；收起时保留一键确认
  const [expanded, setExpanded] = useState(false);

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
      setExpanded(true); // 出错自动展开，让错误与可调整字段可见
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

  const totalCandidates = pending.templates.reduce(
    (n, t) => n + t.candidates.length,
    0,
  );
  const channelNotReady = !pending.nonce || !pending.task_id;
  const submitButton = (
    <Button
      size="sm"
      className="h-7 bg-[#1a66fb] px-3 text-xs text-white hover:bg-[#1557d6]"
      disabled={submitting || channelNotReady}
      onClick={submit}
    >
      {submitting && <Loader2 className="mr-1 h-3 w-3 animate-spin" />}
      确认并继续填写
    </Button>
  );

  return (
    <div className="space-y-2 rounded-lg border border-[#E5E5E5] bg-[#F5F5F5] px-3 py-2.5 text-xs">
      {/* 摘要头（常显）：点击展开/收起完整字段列表；收起时保留一键确认 */}
      <div className="flex items-center gap-2">
        <button
          type="button"
          className="flex flex-1 items-center gap-1.5 text-left"
          onClick={() => setExpanded((v) => !v)}
        >
          <ChevronDown
            className={`h-3.5 w-3.5 shrink-0 text-[#8C8C8C] transition-transform ${
              expanded ? '' : '-rotate-90'
            }`}
          />
          <span className="font-medium text-[#000000]">填写字段确认</span>
          <span className="text-[#8C8C8C]">
            {pending.templates.length} 个范本 · {totalCandidates} 个填写点
            {!expanded && '，点击展开调整'}
          </span>
        </button>
        {!expanded && submitButton}
      </div>
      {expanded && (
        <>
          {pending.templates.map((t) => (
            <div key={t.template_id} className="space-y-1.5">
              <div className="font-medium text-[#000000]">《{t.name}》</div>
              {t.candidates.map((c) => {
                const inputKey = `${t.template_id}:${c.key}`;
                const isChecked = checked[t.template_id]?.has(c.key) ?? false;
                // 直填了值的字段后端无论是否勾选都直取生效，因此不划线（划线暗示"不变化"，与生效矛盾）
                const hasInput = (inputs[inputKey] || '').trim() !== '';
                // 增量模式 + 后端带了 direct_value（LLM 从原话抽出）→ 预填到输入框
                // 视作「AI 建议值」，placeholder 提示用户可改可清；区分全量模式
                // 「留空则 AI 重填」，避免误导。
                const isPreFilled =
                  !!c.direct_value &&
                  hasInput &&
                  inputs[inputKey] === c.direct_value;
                return (
                  <div key={c.key} className="flex items-center gap-2">
                    <Checkbox
                      checked={isChecked}
                      onCheckedChange={() => toggle(t.template_id, c.key)}
                    />
                    {onLocate ? (
                      <button
                        type="button"
                        title={`定位到文档中的「${c.name || c.key}」`}
                        className={`text-left underline-offset-2 decoration-dotted transition-colors hover:underline ${
                          isChecked || hasInput
                            ? 'text-[#000000]'
                            : 'text-[#8C8C8C] line-through'
                        }`}
                        onClick={(e) => {
                          e.stopPropagation();
                          onLocate(t.template_id, c.key);
                        }}
                      >
                        {c.name || c.key}
                        {c.default_value ? `（默认：${c.default_value}）` : ''}
                      </button>
                    ) : (
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
                    )}
                    <Input
                      className="ml-auto h-6 w-[140px] border-[#E5E5E5] bg-white text-xs"
                      placeholder={
                        isPreFilled ? 'AI 建议值，可改' : '留空则 AI 重填'
                      }
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
            {/* nonce/task_id 缺失 = 确认通道未就绪（Redis 唤醒令牌缺失），提交必然无效，前置禁用 */}
            {channelNotReady && (
              <span className="mr-auto self-center text-[#8C8C8C]">
                确认通道未就绪，暂时无法提交
              </span>
            )}
            {submitButton}
          </div>
        </>
      )}
      {/* 收起态下的提交错误也要可见：摘要头下方一行红字 */}
      {!expanded && error && <div className="text-[#E5484D]">{error}</div>}
    </div>
  );
}

// 「多范本选择确认」卡片：AI 初选了多个范本时挂起询问（select_pending 事件），
// 用户勾选 ≥1 个提交（POST /template/fill/select-confirm 写 Redis 唤醒继续）；
// 超时（600s）未提交 → 画布按 AI 选择继续，卡片转灰字只读。
export function TemplateSelectConfirmCard({
  pending,
  onSubmitted,
}: {
  pending: ITemplateFillSelectPending;
  /** 提交成功后回调（可选）：使用方把 submitted 回写进流式状态，
   *  使归约器的 select_timeout 守卫（!submitted）真正生效 */
  onSubmitted?: () => void;
}) {
  // 勾选集合（初始 = AI 初选全集），Set 不可变更新保证 memo 感知
  const [checked, setChecked] = useState<Set<string>>(
    () => new Set(pending.ai_selected),
  );
  const [submitting, setSubmitting] = useState(false);
  const [submitted, setSubmitted] = useState(false);
  const [error, setError] = useState('');

  const toggle = (templateId: string) => {
    setChecked((prev) => {
      const cur = new Set(prev);
      if (cur.has(templateId)) {
        cur.delete(templateId);
      } else {
        cur.add(templateId);
      }
      return cur;
    });
  };

  const submit = async () => {
    if (submitting || checked.size === 0) return;
    setSubmitting(true);
    setError('');
    try {
      await confirmTemplateFillSelect(
        pending.task_id,
        pending.select_nonce || '',
        Array.from(checked),
      );
      setSubmitted(true);
      // 回写流式状态：迟到/并发的 select_timeout 不再把本卡片置 expired
      onSubmitted?.();
    } catch (e) {
      setError(e instanceof Error ? e.message : '提交失败');
    } finally {
      setSubmitting(false);
    }
  };

  if (submitted || pending.submitted) {
    return (
      <div className="rounded-lg border border-[#E5E5E5] bg-[#F5F5F5] px-3 py-2 text-xs text-[#1a66fb]">
        {pending.expired
          ? '已提交选择，但填写可能已按 AI 初选继续'
          : '已确认范本，正在继续填写…'}
      </div>
    );
  }

  if (pending.expired) {
    return (
      <div className="rounded-lg border border-[#E5E5E5] bg-[#F5F5F5] px-3 py-2 text-xs text-[#8C8C8C]">
        等待超时，已按 AI 初选的 {pending.ai_selected.length} 个范本继续填写。
      </div>
    );
  }

  return (
    // 蓝色强调样式（区别于灰色信息行）：这是需要用户主动操作的挂起卡，
    // 灰卡易被当成纯展示忽略，导致干等超时后误以为系统「自己执行」了
    <div className="space-y-2 rounded-lg border border-[#B3CCFF] bg-[#F5F8FF] px-3 py-2.5 text-xs">
      <div className="font-medium text-[#000000]">
        检测到 {pending.select_candidates.length} 个适用范本，请选择需要填充的
        （至少 1 个）
      </div>
      <div className="text-[#8C8C8C]">不操作将在 10 分钟后按 AI 初选继续。</div>
      {pending.select_candidates.map((c) => (
        <label
          key={c.template_id}
          className="flex cursor-pointer items-start gap-2"
        >
          <Checkbox
            className="mt-0.5"
            checked={checked.has(c.template_id)}
            onCheckedChange={() => toggle(c.template_id)}
          />
          <span className="leading-5">
            <span className="text-[#000000]">《{c.name}》</span>
            {c.slot_count != null && (
              <span className="text-[#8C8C8C]">
                （{c.slot_count} 个填写点）
              </span>
            )}
            {c.description && (
              <span className="block text-[#8C8C8C]">{c.description}</span>
            )}
          </span>
        </label>
      ))}
      {error && <div className="text-[#E5484D]">{error}</div>}
      <div className="flex justify-end pt-0.5">
        {checked.size === 0 && (
          <span className="mr-auto self-center text-[#8C8C8C]">
            请至少勾选 1 个范本
          </span>
        )}
        {/* nonce/task_id 缺失 = 唤醒通道未就绪，提交必然无效，前置禁用 */}
        {(!pending.select_nonce || !pending.task_id) && (
          <span className="mr-auto self-center text-[#8C8C8C]">
            确认通道未就绪，暂时无法提交
          </span>
        )}
        <Button
          size="sm"
          className="h-7 bg-[#1a66fb] px-3 text-xs text-white hover:bg-[#1557d6]"
          disabled={
            submitting ||
            checked.size === 0 ||
            !pending.select_nonce ||
            !pending.task_id
          }
          onClick={submit}
        >
          {submitting && <Loader2 className="mr-1 h-3 w-3 animate-spin" />}
          确认并继续填写
        </Button>
      </div>
    </div>
  );
}
