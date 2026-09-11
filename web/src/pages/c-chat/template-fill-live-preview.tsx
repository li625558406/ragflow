// 范本填写「实时预览」：打开模板正文（/template/fill/<id>/preview 的逐段条目），
// 占位符渲染为高亮槽位；LLM 每产出一批字段值（filling 事件 values），对应槽位
// 实时填入并高亮 —— 纯展示层合成，最终成稿仍以后端 docxtpl/openpyxl 渲染为准。
// c-chat 对话与 flow AI 面板共用（经 template-fill-progress 接入）。文案全中文。
import type { ITemplateFillTemplate } from '@/hooks/template-fill-stream';
import { useTemplateFillPreview } from '@/hooks/use-template-fill-request';
import { Loader2, X } from 'lucide-react';
import { useMemo } from 'react';

// 与后端 PLACEHOLDER_RE 同口径：{{lower_snake_key}}
const PLACEHOLDER_RE = /\{\{([a-z][a-z0-9_]*)\}\}/g;

/** 把一段文本拆成 [纯文本 | {key} 占位符] 序列 */
function splitPlaceholders(
  text: string,
): Array<{ type: 'text'; value: string } | { type: 'ph'; key: string }> {
  const nodes: Array<
    { type: 'text'; value: string } | { type: 'ph'; key: string }
  > = [];
  let last = 0;
  PLACEHOLDER_RE.lastIndex = 0;
  let m: RegExpExecArray | null;
  while ((m = PLACEHOLDER_RE.exec(text)) !== null) {
    if (m.index > last)
      nodes.push({ type: 'text', value: text.slice(last, m.index) });
    nodes.push({ type: 'ph', key: m[1] });
    last = m.index + m[0].length;
  }
  if (last < text.length) nodes.push({ type: 'text', value: text.slice(last) });
  return nodes;
}

export default function TemplateFillLivePreview({
  tpl,
  onClose,
}: {
  tpl: ITemplateFillTemplate;
  onClose: () => void;
}) {
  const enabled = Boolean(tpl.template_id);
  const { data, isLoading } = useTemplateFillPreview(
    enabled ? tpl.template_id : '',
  );
  const items = data?.data?.items ?? [];
  const fileType = data?.data?.file_type || 'docx';
  const values = tpl.values || {};

  const filledCount = useMemo(() => Object.keys(values).length, [values]);

  // docx：段落流；xlsx：按 sheet 分组（与 B端预览同数据结构，展示从简）
  const sheetGroups = useMemo(() => {
    if (fileType !== 'xlsx') return [];
    const bySheet = new Map<string, typeof items>();
    items.forEach((it) => {
      const sheet = it.sheet || 'Sheet1';
      if (!bySheet.has(sheet)) bySheet.set(sheet, []);
      bySheet.get(sheet)!.push(it);
    });
    return [...bySheet.entries()];
  }, [items, fileType]);

  const renderText = (text: string) =>
    splitPlaceholders(text).map((seg, i) => {
      if (seg.type === 'text') return <span key={i}>{seg.value}</span>;
      const v = values[seg.key];
      if (v !== undefined && v !== '') {
        return (
          <span
            key={i}
            title={seg.key}
            className="mx-0.5 rounded bg-[#EFF4FF] px-1 py-px font-mono text-xs font-medium text-[#1a66fb]"
          >
            {v}
          </span>
        );
      }
      return (
        <span
          key={i}
          title={`${seg.key}（等待 AI 填入）`}
          className="mx-0.5 rounded border border-dashed border-[#1a66fb]/60 bg-[#EFF4FF] px-1 py-px font-mono text-[10px] text-[#1a66fb]"
        >
          {seg.key}
        </span>
      );
    });

  return (
    <div className="fixed inset-0 z-50 flex justify-end bg-black/30">
      <div className="flex h-full w-full max-w-2xl flex-col bg-white shadow-xl">
        {/* 头部：模板名 + 实时填充进度 + 关闭 */}
        <div className="flex items-center gap-2 border-b border-[#E5E5E5] px-4 py-3">
          <span className="truncate text-sm font-medium text-[#000000]">
            《{tpl.name || '范本'}》实时预览
          </span>
          {tpl.status === 'filling' && (
            <Loader2 className="h-3.5 w-3.5 shrink-0 animate-spin text-[#1a66fb]" />
          )}
          <span className="shrink-0 rounded bg-[#EFF4FF] px-1.5 text-xs text-[#1a66fb]">
            {tpl.status === 'filling'
              ? `已填入 ${tpl.done ?? filledCount}/${tpl.total ?? tpl.slot_count ?? 0}`
              : tpl.status === 'filled'
                ? `已填入 ${filledCount} 个字段`
                : '等待填写'}
          </span>
          <button
            className="ml-auto rounded p-1 text-[#8C8C8C] transition-colors hover:bg-[#F5F5F5] hover:text-[#000000]"
            onClick={onClose}
          >
            <X className="h-4 w-4" />
          </button>
        </div>
        {/* 正文 */}
        <div className="min-h-0 flex-1 overflow-y-auto px-6 py-4">
          {isLoading ? (
            <div className="flex items-center justify-center gap-2 py-16 text-xs text-[#8C8C8C]">
              <Loader2 className="h-4 w-4 animate-spin" />
              正在加载模板正文…
            </div>
          ) : fileType === 'xlsx' ? (
            <div className="space-y-4">
              {sheetGroups.map(([sheet, rows]) => (
                <div key={sheet}>
                  <div className="mb-1 text-xs font-medium text-[#525252]">
                    {sheet}
                  </div>
                  <div className="space-y-0.5">
                    {rows.map((it) => (
                      <div
                        key={it.index}
                        className="flex gap-2 text-xs leading-6 text-[#000000]"
                      >
                        <span className="w-16 shrink-0 font-mono text-[10px] text-[#8C8C8C]">
                          {it.coord || ''}
                        </span>
                        <span>{renderText(it.text)}</span>
                      </div>
                    ))}
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <div className="space-y-1.5 text-sm leading-7 text-[#000000]">
              {items
                .filter((it) => it.text.trim())
                .map((it) => (
                  <p key={it.index}>{renderText(it.text)}</p>
                ))}
            </div>
          )}
        </div>
        {/* 底部说明 */}
        <div className="border-t border-[#E5E5E5] px-4 py-2 text-[10px] text-[#8C8C8C]">
          蓝色为 AI 已填入内容；虚线槽位等待 AI 填入。成稿以最终渲染文件为准。
        </div>
      </div>
    </div>
  );
}
