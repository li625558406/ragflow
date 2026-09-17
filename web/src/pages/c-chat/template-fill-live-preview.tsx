// 范本填写「实时预览」：docx 分支拉原始文件经 docx-preview 保真渲染（字号/加粗/
// 颜色/表格/排版不丢），占位符经 DOM 后处理渲染为高亮槽位；LLM 每产出一批字段值
// （filling 事件 values），pristine 快照重放 + 高亮重涂实现实时填入。渲染失败降级
// 回纯文本段落渲染。xlsx 分支维持旧链路。最终成稿仍以后端 docxtpl/openpyxl 为准。
// c-chat 对话与 flow AI 面板共用（经 template-fill-progress 接入）。文案全中文。
import {
  buildKeyNameMap,
  type ITemplateFillTemplate,
} from '@/hooks/template-fill-stream';
import {
  useTemplateFillFile,
  useTemplateFillPreview,
} from '@/hooks/use-template-fill-request';
import {
  applyDocxHighlight,
  applyDocxPageLazy,
  instantFocusScroll,
  isPlaceholderFilled,
  updateDocxHighlight,
  type DocxPlaceholderSpans,
} from '@/pages/c-chat/docx-highlight';
import { renderAsync } from 'docx-preview';
import { Loader2, X } from 'lucide-react';
import { useEffect, useMemo, useRef, useState } from 'react';

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

// 点击未填充汇总字段后的定位脉冲：500ms×6 次 = 3s
const PULSE_MS = 500;
const PULSE_TIMES = 6;

// 超大文档防线阈值：>2.5MB 默认文本预览（设计 2026-09-16 预览内存治理）
const BIG_BLOB_BYTES = 2.5 * 1024 * 1024;

/** 定位到容器内 data-ph-key 匹配的占位符：强制渲染所在分页 + 瞬时居中
 * （共享 instantFocusScroll，治懒渲染漂移跳错位）+ 琥珀色脉冲闪烁
 * （WAAPI 自清理、可重复触发）。找不到返回 false（调用方据此不标记
 * 已定位，留待渲染完成后重试）。 */
function focusPlaceholder(container: HTMLElement, key: string): boolean {
  const el = container.querySelector<HTMLElement>(
    `[data-ph-key="${CSS.escape(key)}"]`,
  );
  if (!el) return false;
  instantFocusScroll(el);
  el.animate?.(
    [
      { backgroundColor: '#FFE58F', boxShadow: '0 0 0 3px #FA8C16' },
      { backgroundColor: '#EFF4FF', boxShadow: '0 0 0 1px #FA8C16' },
    ],
    { duration: PULSE_MS, iterations: PULSE_TIMES, easing: 'ease-in-out' },
  );
  return true;
}

export default function TemplateFillLivePreview({
  tpl,
  focusKey,
  onClose,
}: {
  tpl: ITemplateFillTemplate;
  /** 点击未填充汇总字段带来的定位目标（抽屉打开后定位一次） */
  focusKey?: string;
  onClose: () => void;
}) {
  const enabled = Boolean(tpl.template_id);
  const { data, isLoading } = useTemplateFillPreview(
    enabled ? tpl.template_id : '',
  );
  const items = data?.data?.items ?? [];
  const fileType = data?.data?.file_type || 'docx';
  const values = tpl.values || {};

  // key→中文名（filled ∪ unfilled 合并派生；两者按判空口径穷尽且互斥 → 并集即全量）。
  // filling 阶段两者都未到达 → 预览暂无中文名、回落英文 key（对终态后「把 XX 改成
  // YY」的真实用途无影响）。deps 只列两个清单引用：tpl 在流式期间每次归约都换引用，
  // 挂 [tpl] 会让下游 updateDocxHighlight 被事件频率放大。
  const filledList = tpl.filled;
  const unfilledList = tpl.unfilled;
  const names = useMemo(
    () => buildKeyNameMap({ filled: filledList, unfilled: unfilledList }),
    [filledList, unfilledList],
  );
  const namesRef = useRef(names);
  namesRef.current = names;

  // 常驻抽屉：Esc 快捷关闭
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  // ── docx 保真渲染（docx-preview）：原始 blob → renderAsync → 占位符高亮。
  // values 变化时经 spans 映射增量更新（零 DOM 重建），大文档不卡顿。
  // 渲染失败降级回纯文本段落渲染。
  const [renderFailed, setRenderFailed] = useState(false);
  // docx 保真渲染完成标记：定位 effect 依赖它区分「渲染未完不能定位」与「文档无该 key 静默放弃」
  const [renderedOk, setRenderedOk] = useState(false);
  // 超大文档防线（预览内存治理，设计 2026-09-16）：>2.5MB 的 docx 默认走纯文本渲染
  // （docx-preview 整本文档一次性建 DOM 树，200+ 页曾致标签页 OOM），顶部提示 +
  // 「切换保真渲染」显式覆盖（本地 state 不落库）。渲染失败降级链路不变。
  const [blobOversize, setBlobOversize] = useState(false);
  const [forceFidelity, setForceFidelity] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);
  // 文本降级/xlsx 路径的滚动容器（与 docx 容器互斥挂载，二者必有其一）
  const textContainerRef = useRef<HTMLDivElement>(null);
  const placeholderSpansRef = useRef<DocxPlaceholderSpans>(new Map());
  const valuesRef = useRef(values);
  valuesRef.current = values;

  // file_type 为 docx 才拉原始文件（xlsx 走旧链路）；preview 接口未返回前默认 docx，
  // 文件与 preview 并行拉取提速
  const docxEnabled = fileType === 'docx';
  const {
    data: fileBlob,
    isLoading: fileLoading,
    error: fileError,
  } = useTemplateFillFile(docxEnabled ? tpl.template_id : '');

  // blob 到达判定体量：超阈值先翻转渲染分支（声明在渲染 effect 之前，同批提交内先生效）
  useEffect(() => {
    setBlobOversize(Boolean(fileBlob && fileBlob.size > BIG_BLOB_BYTES));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [fileBlob]);

  // blob 到达：清容器 → renderAsync → 屏外页懒渲染 → 建占位符 span 映射。
  // 超大文档且未显式切换保真 → 跳过整本 DOM 建树（文本分支接管）
  useEffect(() => {
    if (!docxEnabled || !fileBlob || !containerRef.current) return;
    if (blobOversize && !forceFidelity) return;
    const el = containerRef.current;
    setRenderFailed(false);
    setRenderedOk(false);
    placeholderSpansRef.current = new Map();
    el.innerHTML = '';
    renderAsync(fileBlob, el, undefined, { inWrapper: true, breakPages: true })
      .then(() => {
        applyDocxPageLazy(el);
        placeholderSpansRef.current = applyDocxHighlight(
          el,
          valuesRef.current,
          namesRef.current,
        );
        setRenderedOk(true);
      })
      .catch(() => setRenderFailed(true));
  }, [fileBlob, docxEnabled, blobOversize, forceFidelity]);

  // values 变化：按 span 映射增量更新（已填⇄未填双向切换），不重建 DOM。
  // names 一同入 deps：终态 filled/unfilled 到达后补涂已填值的 title（中文名）
  useEffect(() => {
    if (!docxEnabled) return;
    updateDocxHighlight(placeholderSpansRef.current, values, names);
  }, [values, names, docxEnabled]);

  // 范本切换时清占位符映射与体量防线状态（防止上一范本的 span 基线/覆盖选择串台）
  useEffect(() => {
    placeholderSpansRef.current = new Map();
    setRenderFailed(false);
    setRenderedOk(false);
    setBlobOversize(false);
    setForceFidelity(false);
  }, [tpl.template_id]);

  const docxFidelity =
    docxEnabled &&
    !renderFailed &&
    !fileError &&
    (!blobOversize || forceFidelity);
  const docxLoading =
    fileLoading || (docxEnabled && !fileBlob && !fileError && isLoading);

  // 点击汇总字段后的定位：渲染完成后滚动到该占位符并闪烁；只执行一次
  //（focusDoneRef 记录已定位 key）。docx 保真渲染未完成（renderedOk=false 且
  // 未降级）时容器还没有占位符 span，不标记已定位，等依赖翻转后重试；
  // 文本降级/xlsx 路径随 items 到达触发。文档中无该 key → 静默跳过。
  const focusDoneRef = useRef<string | null>(null);
  useEffect(() => {
    // docx 保真容器与文本降级容器互斥挂载，取当前实际挂载的那个
    const root = containerRef.current ?? textContainerRef.current;
    // 已定位标记按 范本:字段 维度记录，避免多范本切换后同 key 字段无法重新定位
    if (!tpl.template_id || !focusKey || docxLoading || !root) return;
    const focusTarget = `${tpl.template_id}:${focusKey}`;
    if (focusDoneRef.current === focusTarget) return;
    if (docxEnabled && !renderedOk && !renderFailed) return;
    if (focusPlaceholder(root, focusKey)) {
      focusDoneRef.current = focusTarget;
    }
  }, [focusKey, docxLoading, renderedOk, items, docxEnabled, renderFailed]);

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
      const name = names.get(seg.key);
      // 与 docx 保真路径同口径：已填值 title 带中文名；未填槽位正文显示中文名
      // （无 name 回落 key）。data-ph-key 恒为 key（定位链路依赖）。
      // 判空走 isPlaceholderFilled（纯空白算未填，与后端 derive_unfilled 一致）
      if (isPlaceholderFilled(v)) {
        return (
          <span
            key={i}
            title={name ? `${name}（${seg.key}）` : seg.key}
            data-ph-key={seg.key}
            className="mx-0.5 rounded bg-[#EFF4FF] px-1 py-px font-mono text-xs font-medium text-[#1a66fb]"
          >
            {v}
          </span>
        );
      }
      const label = name || seg.key;
      return (
        <span
          key={i}
          title={`${label}（等待 AI 填入）`}
          data-ph-key={seg.key}
          className="mx-0.5 rounded border border-dashed border-[#1a66fb]/60 bg-[#EFF4FF] px-1 py-px font-mono text-[10px] text-[#1a66fb]"
        >
          {label}
        </span>
      );
    });

  return (
    // 右侧常驻抽屉：无遮罩不挡对话（可边跟 LLM 对话边实时看填入）；由使用方收缩主区腾位
    <div className="fixed right-0 top-0 z-40 flex h-full w-1/2 flex-col border-l border-[#E5E5E5] bg-white shadow-[-8px_0_24px_rgba(0,0,0,0.08)] animate-in fade-in slide-in-from-right-4 duration-300">
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
      {/* 正文：docx 走 docx-preview 保真渲染；失败/降级回纯文本段落 */}
      {docxLoading ? (
        <div className="flex min-h-0 flex-1 items-center justify-center gap-2 py-16 text-xs text-[#8C8C8C]">
          <Loader2 className="h-4 w-4 animate-spin" />
          正在加载模板正文…
        </div>
      ) : docxFidelity ? (
        // 保真渲染容器：docx-preview 页面宽度固定（A4），窄抽屉下横向滚动看全
        <div className="min-h-0 flex-1 overflow-auto px-6 py-4">
          <div ref={containerRef} />
        </div>
      ) : (
        <div
          ref={textContainerRef}
          className="min-h-0 flex-1 overflow-y-auto overflow-x-hidden px-6 py-4"
        >
          {docxEnabled && (renderFailed || fileError) && (
            <div className="mb-2 rounded bg-[#FFF7E8] px-3 py-2 text-xs text-[#FAAD14]">
              格式渲染失败，已降级为纯文本预览
            </div>
          )}
          {docxEnabled && blobOversize && !forceFidelity && (
            <div className="mb-2 flex items-center gap-2 rounded bg-[#FFF7E8] px-3 py-2 text-xs text-[#FAAD14]">
              <span>文档较大，已用文本预览保障流畅</span>
              <button
                className="ml-auto shrink-0 text-[#1a66fb] transition-colors hover:text-[#1557d6]"
                onClick={() => setForceFidelity(true)}
              >
                切换保真渲染
              </button>
            </div>
          )}
          {fileType === 'xlsx' ? (
            <div className="mx-auto w-full max-w-3xl space-y-4">
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
                        <span className="min-w-0 flex-1 break-words">
                          {renderText(it.text)}
                        </span>
                      </div>
                    ))}
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <div className="mx-auto w-full max-w-3xl space-y-1.5 text-sm leading-7 text-[#000000]">
              {items
                .filter((it) => it.text.trim())
                .map((it) => (
                  <p key={it.index} className="break-words">
                    {renderText(it.text)}
                  </p>
                ))}
            </div>
          )}
        </div>
      )}
      {/* 底部说明 */}
      <div className="border-t border-[#E5E5E5] px-4 py-2 text-[10px] text-[#8C8C8C]">
        按 Word 原始格式渲染；蓝色为 AI 已填入内容，虚线槽位等待 AI
        填入。成稿以最终渲染文件为准。
      </div>
    </div>
  );
}
