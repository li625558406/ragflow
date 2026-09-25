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
  fetchTemplateFillTaskProgress,
  useTemplateFillFile,
  useTemplateFillPreview,
  useTemplateFillResultFile,
  type TemplateFillProgressData,
} from '@/hooks/use-template-fill-request';
import {
  applyDocxHighlight,
  applyDocxPageLazy,
  instantFocusScroll,
  isPlaceholderFilled,
  rebuildPlaceholderSpans,
  updateDocxHighlight,
  type DocxPlaceholderSpans,
} from '@/pages/c-chat/docx-highlight';
import {
  BIG_BLOB_BYTES,
  stashDocxRender,
  takeDocxRender,
} from '@/pages/c-chat/docx-render-cache';
import { renderAsync } from 'docx-preview';
import { Loader2, X } from 'lucide-react';
import { useEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';

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

// 后端 TERMINAL_TASK_STATUSES（done/partial/failed/cancelled）里「有权威产值可取」
// 的那部分：failed/cancelled 没有成稿，拿到也无值可显。
const TERMINAL_PROGRESS_STATUSES = ['done', 'partial'];

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

  // 终态权威产值：预览的文字是「前端 values 覆盖模板工作副本」来的（下方 docx
  // 高亮链路），而对话里的就地修改（FillTemplate action=modify）不发任何
  // template_fill_progress 事件 → 流式/回放快照里的 values 永远停在改前，
  // 用户就会看到「模型说改了、预览还是旧文案」。故按 task_id 拉 progress
  // （终态取值权威：DB render 已被 modify 回写）覆盖显示。
  // 只在终态取用：流式期间 SSE 的 values 比这发请求更新鲜，不能被它压回去。
  // 抽屉打开期间每 3s 轮询：同屏 modify 后无需关重开，值变化才 set（键数+逐键
  // 相等比较，响应引用每次都新不能直接比），下游 updateDocxHighlight 增量重涂
  // 即「静默刷新」——不重建 DOM、不打断滚动/定位。拉取失败静默保留当前显示。
  const [authoritative, setAuthoritative] =
    useState<TemplateFillProgressData | null>(null);
  useEffect(() => {
    const tid = tpl.task_id;
    setAuthoritative(null);
    if (!tid || tpl.status !== 'filled') return;
    let alive = true;
    const sameValues = (
      a?: Record<string, string> | null,
      b?: Record<string, string> | null,
    ) => {
      const ka = Object.keys(a || {});
      const kb = Object.keys(b || {});
      return ka.length === kb.length && ka.every((k) => a![k] === b![k]);
    };
    const tick = () => {
      fetchTemplateFillTaskProgress(tid)
        .then((d) => {
          if (!alive || !d?.status) return;
          if (!TERMINAL_PROGRESS_STATUSES.includes(d.status)) return;
          setAuthoritative((prev) =>
            prev &&
            prev.status === d.status &&
            sameValues(prev.values, d.values)
              ? prev
              : d,
          );
        })
        .catch(() => {
          // 拉取失败回落卡片上的 values：预览仍可用，只是可能显示改前内容
        });
    };
    tick();
    const timer = window.setInterval(tick, 3000);
    return () => {
      alive = false;
      window.clearInterval(timer);
    };
  }, [tpl.task_id, tpl.status]);

  // useMemo 而非裸表达式：两者都空时 `|| {}` 每次渲染都产新对象，会把下游
  // updateDocxHighlight effect / filledCount 的依赖打成「每渲染必变」。
  const values = useMemo(
    () => authoritative?.values || tpl.values || {},
    [authoritative, tpl.values],
  );

  // key→中文名（filled ∪ unfilled 合并派生；两者按判空口径穷尽且互斥 → 并集即全量）。
  // filling 阶段两者都未到达 → 预览暂无中文名、回落英文 key（对终态后「把 XX 改成
  // YY」的真实用途无影响）。deps 只列两个清单引用：tpl 在流式期间每次归约都换引用，
  // 挂 [tpl] 会让下游 updateDocxHighlight 被事件频率放大。
  // 权威响应里两个清单为 null 表示「空」（全填满 / 无留空），此时不能回落到卡片上
  // 那份可能过时的清单，故按 null 与否判定而非 ??
  const filledList = authoritative
    ? (authoritative.filled ?? undefined)
    : tpl.filled;
  const unfilledList = authoritative
    ? (authoritative.unfilled ?? undefined)
    : tpl.unfilled;
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
    data: workBlob,
    isLoading: workLoading,
    error: workError,
  } = useTemplateFillFile(docxEnabled ? tpl.template_id : '');
  // 终态成稿渲染源切换（demo03 事故）：工作副本里的 {{key}} 之外的正文来自
  // 模板原文，replace/rewrite 的修改只存在于成稿——终态且有下载契约时改拉
  // 成稿派生副本（后端桥接保证与版本链最新内容一致），拉取失败回落工作副本。
  // 成稿中占位符已被值替换 → 占位符高亮/定位自然失效（span 映射为空），可接受。
  // 终态渲染源：默认工作副本（占位符高亮/点击定位/蓝字填写值都在这条链路上，
  // 2026-09-25 用户反馈「文档中没有填充点的标识、点击不跳转」后由自动切成稿改回
  // 默认填写视图）；「查看成稿」显式切换后才拉成稿派生副本（replace/rewrite 改的
  // 非填写点正文只存在于成稿），拉取失败回落工作副本。成稿中占位符已被值替换，
  // 高亮/定位在该视图下自然失效。
  const [preferResult, setPreferResult] = useState(false);
  const {
    data: resultBlob,
    isLoading: resultLoading,
    error: resultError,
  } = useTemplateFillResultFile(docxEnabled ? tpl.download : undefined);
  const wantResult =
    docxEnabled && tpl.status === 'filled' && Boolean(tpl.download?.url);
  const showResult = wantResult && preferResult && !resultError;
  const fileBlob = showResult ? resultBlob : workBlob;
  const fileLoading = showResult ? resultLoading : workLoading;
  const fileError = showResult ? undefined : workError;

  // 超大文档防线（派生判定而非 state+effect）：blob 到达的同一 commit 内守卫即
  // 生效——旧写法会在同一 commit 先以 oversize=false 白渲染一遍大文件再翻转分支
  const blobOversize = Boolean(fileBlob && fileBlob.size > BIG_BLOB_BYTES);

  // blob 到达：清容器 → renderAsync → 屏外页懒渲染 → 建占位符 span 映射。
  // 超大文档且未显式切换保真 → 跳过整本 DOM 建树（文本分支接管）。
  // 2026-09-21 渲染缓存：卸载/依赖变更时产物子树摘进按 blob 键的离屏缓存，
  // 重开 takeDocxRender 命中 → appendChild 回放（毫秒级）。回放的树里占位符
  // span 已带上一轮 values 文本（{{key}} 原文已被 replace），重建映射后交给
  // 下方 updateDocxHighlight effect 按当前 values/names 重涂。
  useEffect(() => {
    if (!docxEnabled || !fileBlob || !containerRef.current) return;
    // 注意：此处 oversize 守卫在状态重置之前，与 review-panel（重置在前）刻意
    // 不同——本组件跨范本切换由 template_id 独立 effect 兜底重置，勿「对齐」时
    // 把重置提前而意识不到对另一 effect 的依赖
    if (blobOversize && !forceFidelity) return;
    const el = containerRef.current;
    setRenderFailed(false);
    setRenderedOk(false);
    placeholderSpansRef.current = new Map();
    if (takeDocxRender(fileBlob, el)) {
      applyDocxPageLazy(el);
      placeholderSpansRef.current = rebuildPlaceholderSpans(el);
      setRenderedOk(true);
      // 回放分支同样注册 cleanup：不摘回缓存的话，关闭抽屉后树留在已 detach
      // 的 el 里被 GC，重开必 miss 全量重渲（缓存隔次生效）
      return () => {
        stashDocxRender(fileBlob, el);
      };
    }
    // 在飞渲染防错树：cancelled 堵 late resolve 清掉后续范本已渲染的树；
    // settled/failed 堵在飞或失败残留入缓存（内容不可信，大不了下次重渲）
    let cancelled = false;
    let settled = false;
    let failed = false;
    el.innerHTML = '';
    renderAsync(fileBlob, el, undefined, { inWrapper: true, breakPages: true })
      .then(() => {
        settled = true;
        if (cancelled || !el.isConnected) return;
        applyDocxPageLazy(el);
        placeholderSpansRef.current = applyDocxHighlight(
          el,
          valuesRef.current,
          namesRef.current,
        );
        setRenderedOk(true);
      })
      .catch(() => {
        settled = true;
        failed = true;
        if (cancelled) return;
        setRenderFailed(true);
      });
    return () => {
      cancelled = true;
      if (settled && !failed) stashDocxRender(fileBlob, el);
    };
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
    setForceFidelity(false);
    setPreferResult(false);
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

  return createPortal(
    // 右侧常驻抽屉：无遮罩不挡对话（可边跟 LLM 对话边实时看填入）；由使用方收缩主区腾位。
    // 必须 portal 到 body：本组件挂在对话内容树深处，任一祖先带 transform（如
    // c-chat 页壳 .cs-page-enter 动画 fill-mode:both 永久保留的 identity transform）
    // 都会成为 fixed 的包含块，抽屉就会相对内容树而非视口定位——流程页内容把
    // 包含块撑宽后抽屉被定位到屏幕外（「点击已填充字段预览不出现」事故根因）。
    <div className="fixed right-0 top-0 z-40 flex h-full w-2/3 flex-col border-l border-[#E5E5E5] bg-white shadow-[-8px_0_24px_rgba(0,0,0,0.08)] animate-in fade-in slide-in-from-right-4 duration-300">
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
        {wantResult && (
          <button
            className="ml-1 shrink-0 rounded px-1.5 py-0.5 text-xs text-[#1a66fb] transition-colors hover:bg-[#EFF4FF]"
            onClick={() => setPreferResult((v) => !v)}
          >
            {showResult ? '返回填写视图' : '查看成稿'}
          </button>
        )}
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
    </div>,
    document.body,
  );
}
