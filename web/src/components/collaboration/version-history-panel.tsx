import {
  ChevronDown,
  ChevronRight,
  Eye,
  History,
  RotateCcw,
  X,
} from 'lucide-react';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import UniverSpreadsheetVersionPreview from './univer-spreadsheet-version-preview';
import UniverVersionPreview from './univer-version-preview';

interface VersionEntry {
  id: string;
  version: number;
  created_by: string;
  user_name: string;
  create_time: number;
}

interface VersionInfo {
  current_version: number;
  has_ydoc: boolean;
  update_time: number | string | null;
  versions: VersionEntry[];
}

interface Props {
  docId: string;
  apiFetch: (url: string, options?: RequestInit) => Promise<Response>;
  open: boolean;
  onToggle: () => void;
  /**
   * 文件类型 — 决定 content_snapshot 的解析方式和完整预览渲染器。
   * 默认 'docx' (Univer IDocumentData) 以保持向后兼容。
   * 'xlsx' 走 IWorkbookData 解析路径。
   */
  fileType?: 'docx' | 'xlsx';
}

function formatTime(t: number | string | null): string {
  if (!t) return '—';
  let ms = Number(t);
  if (!Number.isFinite(ms)) return '—';
  if (ms < 1e12) ms *= 1000; // 秒级时间戳兼容
  const d = new Date(ms);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(
    d.getDate(),
  ).padStart(2, '0')} ${String(d.getHours()).padStart(2, '0')}:${String(
    d.getMinutes(),
  ).padStart(2, '0')}`;
}

function relativeTime(t: number): string {
  if (!t) return '';
  let ms = Number(t);
  if (!Number.isFinite(ms)) return '';
  if (ms < 1e12) ms *= 1000;
  const diff = Date.now() - ms;
  if (diff < 60000) return '刚刚';
  if (diff < 3600000) return `${Math.floor(diff / 60000)} 分钟前`;
  if (diff < 86400000) return `${Math.floor(diff / 3600000)} 小时前`;
  if (diff < 7 * 86400000) return `${Math.floor(diff / 86400000)} 天前`;
  return '';
}

/**
 * 从 Univer IWorkbookData 快照中提取纯文本预览。
 *
 * 结构: { sheetOrder: string[], sheets: { [id]: { name, cellData: { [row]: { [col]: { v, t } } } } } }
 * 策略: 按 sheetOrder 依次遍历，每张表用 "【Sheet名】" 打头，行内单元格用 " | " 分隔，
 * 空行保留为空串。最多渲染前 100 行避免超长文本拖垮面板。
 */
function extractSpreadsheetText(workbook: Record<string, unknown>): string {
  const sheetOrder = workbook.sheetOrder as string[] | undefined;
  const sheets = workbook.sheets as
    | Record<
        string,
        {
          name?: string;
          cellData?: Record<string, Record<string, { v?: unknown }>>;
        }
      >
    | undefined;
  if (!sheetOrder || !sheets) return '(无法解析表格内容)';

  const MAX_ROWS_PER_SHEET = 100;
  const sections: string[] = [];

  for (const sheetId of sheetOrder) {
    const sheet = sheets[sheetId];
    if (!sheet) continue;
    const sheetName = sheet.name || sheetId;
    const cellData = sheet.cellData;
    if (!cellData) {
      sections.push(`【${sheetName}】(空表)`);
      continue;
    }
    // 收集所有行号并按数字升序，保证阅读顺序符合直觉。
    const rowKeys = Object.keys(cellData)
      .map((k) => Number(k))
      .filter((n) => Number.isFinite(n))
      .sort((a, b) => a - b);
    if (rowKeys.length === 0) {
      sections.push(`【${sheetName}】(空表)`);
      continue;
    }
    const trimmed = rowKeys.slice(0, MAX_ROWS_PER_SHEET);
    const lines = trimmed.map((rowNum) => {
      const row = cellData[String(rowNum)];
      if (!row) return '';
      const colKeys = Object.keys(row)
        .map((k) => Number(k))
        .filter((n) => Number.isFinite(n))
        .sort((a, b) => a - b);
      return colKeys
        .map((colNum) => formatCellValue(row[String(colNum)]?.v))
        .join(' | ');
    });
    const truncatedNote =
      rowKeys.length > MAX_ROWS_PER_SHEET
        ? `\n…(仅显示前 ${MAX_ROWS_PER_SHEET} 行)`
        : '';
    sections.push(`【${sheetName}】\n${lines.join('\n')}${truncatedNote}`);
  }

  const text = sections.join('\n\n').trim();
  return text || '(空表格)';
}

function formatCellValue(v: unknown): string {
  if (v === null || v === undefined) return '';
  if (typeof v === 'string') return v;
  if (typeof v === 'number' || typeof v === 'boolean') return String(v);
  // 复杂类型 (公式对象、富文本等) 退化成 JSON 以免丢信息。
  try {
    return JSON.stringify(v);
  } catch {
    return '';
  }
}

export default function VersionHistoryPanel({
  docId,
  apiFetch,
  open,
  onToggle,
  fileType = 'docx',
}: Props) {
  const [info, setInfo] = useState<VersionInfo | null>(null);
  const [loading, setLoading] = useState(false);
  const [restoringId, setRestoringId] = useState<string | null>(null);
  const [confirmTarget, setConfirmTarget] = useState<VersionEntry | null>(null);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  // 按需预览：每条版本点击「预览」才拉取 content_snapshot (体积可能很大)。
  // 缓存到 previewCache 避免重复请求；expandedId 控制当前展开的条目。
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [previewLoadingId, setPreviewLoadingId] = useState<string | null>(null);
  const [previewCache, setPreviewCache] = useState<
    Record<string, { text: string; content: Record<string, unknown> | null }>
  >({});
  // 「查看完整」模态：加载 Univer 只读实例渲染完整格式。
  const [fullViewTarget, setFullViewTarget] = useState<{
    version: number;
    content: Record<string, unknown> | null;
  } | null>(null);

  // 单调递增的请求序号，用于消除 togglePreview 的竞态：
  // 用户点开 A → fetch 中 → 点收起 → fetch 完成 —— 如果不检查序号，
  // fetch 完成会再次展开 A，违背用户意图。每次新请求递增，
  // 旧的 in-flight 请求完成时如果发现序号已过期就直接跳过状态写入。
  const reqIdRef = useRef(0);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const resp = await apiFetch(
        `/api/v1/collaboration/documents/${docId}/versions`,
      );
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const result = await resp.json();
      if (result.code === 0 && result.data) {
        setInfo(result.data);
      }
    } catch (e) {
      console.error('Failed to load versions:', e);
    } finally {
      setLoading(false);
    }
  }, [apiFetch, docId]);

  useEffect(() => {
    if (open) load();
  }, [docId, open, load]);

  // 切换文档时重置预览相关 state。entry.id 虽然全局唯一不会错乱，
  // 但保留旧文档的 expandedId/fullViewTarget 会让状态不干净
  // (比如切文档时模态仍开着、展开条目指向不存在的 entry)。
  // 同步递增 reqIdRef 作废所有 in-flight 请求，避免旧文档的 fetch
  // 完成后向新文档的 previewCache 里写入脏数据。
  useEffect(() => {
    reqIdRef.current += 1;
    setExpandedId(null);
    setPreviewCache({});
    setFullViewTarget(null);
    setPreviewLoadingId(null);
  }, [docId]);

  // Extract a plain-text summary from a Univer snapshot.
  // - docx (IDocumentData): body.dataStream 是带 \r\n 的全文文本
  // - xlsx (IWorkbookData): 遍历 sheetOrder → 每张表的 cellData，按行拼接
  //   单元格值。不渲染完整表格，只给"能看到这版本大致写了什么"的概览。
  const extractPlainText = useCallback(
    (content: unknown): string => {
      if (!content || typeof content !== 'object') return '(空文档)';
      if (fileType === 'xlsx') {
        return extractSpreadsheetText(content as Record<string, unknown>);
      }
      const body = (content as { body?: { dataStream?: unknown } }).body;
      const ds = body?.dataStream;
      if (typeof ds !== 'string') return '(无法解析内容)';
      const text = ds
        .replace(/\r/g, '\n')
        .replace(/\n{3,}/g, '\n\n')
        .trim();
      return text || '(空文档)';
    },
    [fileType],
  );

  const togglePreview = useCallback(
    async (entry: VersionEntry) => {
      // 已展开 → 收起。
      if (expandedId === entry.id) {
        setExpandedId(null);
        return;
      }
      // 乐观展开：立即把条目展开，loading 阶段在内容区显示「加载中...」。
      // 避免旧实现「fetch 完成才展开」带来的竞态 —— 用户若在 fetch
      // 中途收起，fetch 完成会再次意外展开。
      setExpandedId(entry.id);
      if (previewCache[entry.id]) {
        return; // 缓存命中，直接展示
      }
      const myReqId = ++reqIdRef.current;
      setPreviewLoadingId(entry.id);
      try {
        const resp = await apiFetch(
          `/api/v1/collaboration/documents/${docId}/versions/${entry.id}`,
        );
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        const result = await resp.json();
        if (result.code !== 0) {
          throw new Error(result.message || '加载失败');
        }
        const content = result?.data?.content ?? null;
        // 请求过期：用户在 fetch 中途进行了新的操作 (点其他条目/收起/切文档)。
        // 只跳过 state 写入，不报错 —— 用户的最新意图由最新请求负责。
        if (myReqId !== reqIdRef.current) return;
        setPreviewCache((prev) => ({
          ...prev,
          [entry.id]: { text: extractPlainText(content), content },
        }));
      } catch (e) {
        if (myReqId !== reqIdRef.current) return;
        console.error('Failed to load version content:', e);
        // 乐观展开已把 expandedId 设为本条目，fetch 失败时必须回滚，
        // 否则展开块会因为 previewCache 空 + loading 完成而永远卡在
        // 「加载中...」。
        setExpandedId((prev) => (prev === entry.id ? null : prev));
        setErrorMsg('加载版本内容失败，请稍后重试');
      } finally {
        if (myReqId === reqIdRef.current) {
          setPreviewLoadingId(null);
        }
      }
    },
    [apiFetch, docId, expandedId, extractPlainText, previewCache],
  );

  const fullViewContent = useMemo(
    () =>
      fullViewTarget?.content
        ? (fullViewTarget.content as Record<string, unknown>)
        : null,
    [fullViewTarget],
  );

  const handleRestore = async (entry: VersionEntry) => {
    if (restoringId) return;
    setRestoringId(entry.id);
    try {
      const resp = await apiFetch(
        `/api/v1/collaboration/documents/${docId}/versions/${entry.id}/restore`,
        { method: 'POST' },
      );
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const result = await resp.json();
      if (result.code === 0) {
        // 恢复后主表内容已被覆盖，reload 是最稳妥的热更新方式。
        window.location.reload();
      } else {
        setErrorMsg(result.message || '恢复失败');
      }
    } catch (e) {
      console.error('Restore failed:', e);
      setErrorMsg('恢复失败，请稍后重试');
    } finally {
      setRestoringId(null);
    }
  };

  if (!open) return null;

  const versions = info?.versions ?? [];
  const currentVersion = info?.current_version ?? 0;

  return (
    <div className="w-full h-full flex flex-col bg-white">
      {/* Header */}
      <div className="flex items-center justify-between px-3 py-2.5 border-b border-stone-100">
        <div className="flex items-center gap-1.5 text-stone-700">
          <History className="size-3.5" />
          <span className="text-xs font-semibold">版本历史</span>
          {versions.length > 0 && (
            <span className="text-[10px] bg-stone-100 text-stone-500 px-1.5 py-0.5 rounded-full">
              {versions.length}
            </span>
          )}
        </div>
        <button
          className="size-6 flex items-center justify-center rounded text-stone-400 hover:text-stone-700 hover:bg-stone-100"
          onClick={onToggle}
        >
          <X className="size-3.5" />
        </button>
      </div>

      {/* Body */}
      <div className="flex-1 overflow-y-auto p-3">
        {loading ? (
          <div className="text-xs text-stone-400 text-center py-6">
            加载中...
          </div>
        ) : versions.length === 0 ? (
          <div className="text-xs text-stone-400 text-center py-6">
            暂无历史版本
          </div>
        ) : (
          <div className="relative">
            {/* Timeline line */}
            <div className="absolute left-1.5 top-2 bottom-2 w-px bg-stone-200" />
            {versions.map((entry, idx) => {
              const isLatest = idx === 0;
              const isCurrent = entry.version === currentVersion;
              const rel = relativeTime(entry.create_time);
              return (
                <div key={entry.id} className="relative pl-5 py-2">
                  <div
                    className={`absolute left-0.5 top-3 size-2 rounded-full ring-1 ring-white ${
                      isCurrent ? 'bg-emerald-500' : 'bg-stone-300'
                    }`}
                  />
                  <div className="flex items-center justify-between gap-2">
                    <div className="flex items-baseline gap-1.5 min-w-0">
                      <span className="text-xs font-medium text-stone-800 shrink-0">
                        v{entry.version}
                      </span>
                      {isCurrent && (
                        <span className="text-[10px] text-emerald-600 bg-emerald-50 px-1.5 py-0.5 rounded-full shrink-0">
                          当前
                        </span>
                      )}
                      {isLatest && !isCurrent && (
                        <span className="text-[10px] text-stone-400 shrink-0">
                          最新保存
                        </span>
                      )}
                    </div>
                    <div className="flex items-center gap-1 shrink-0">
                      <button
                        className="shrink-0 inline-flex items-center gap-1 px-2 py-1 text-[10px] font-medium text-stone-600 border border-stone-200 rounded hover:bg-stone-50 disabled:opacity-50 transition-colors"
                        onClick={() => togglePreview(entry)}
                        disabled={previewLoadingId !== null}
                        title={
                          expandedId === entry.id
                            ? '收起预览'
                            : `预览 v${entry.version}`
                        }
                      >
                        {expandedId === entry.id ? (
                          <ChevronDown className="size-3" />
                        ) : (
                          <ChevronRight className="size-3" />
                        )}
                        <Eye className="size-3" />
                        {previewLoadingId === entry.id
                          ? '加载...'
                          : expandedId === entry.id
                            ? '收起'
                            : '预览'}
                      </button>
                      {!isCurrent && (
                        <button
                          className="shrink-0 inline-flex items-center gap-1 px-2 py-1 text-[10px] font-medium text-stone-600 border border-stone-200 rounded hover:bg-stone-50 disabled:opacity-50 transition-colors"
                          onClick={() => setConfirmTarget(entry)}
                          disabled={restoringId !== null}
                          title={`恢复到 v${entry.version}`}
                        >
                          <RotateCcw className="size-3" />
                          {restoringId === entry.id ? '恢复中...' : '恢复'}
                        </button>
                      )}
                    </div>
                  </div>
                  <div className="text-[10px] text-stone-500 mt-0.5 truncate">
                    {entry.user_name || '未知用户'}
                  </div>
                  <div className="text-[10px] text-stone-400 mt-0.5 flex items-center gap-1.5">
                    <span>{formatTime(entry.create_time)}</span>
                    {rel && <span className="text-stone-300">· {rel}</span>}
                  </div>
                  {/* 展开预览：纯文本摘要 + 「查看完整」入口 */}
                  {expandedId === entry.id && (
                    <div className="mt-2 ml-1 rounded-md bg-stone-50 border border-stone-200 p-2.5">
                      {previewCache[entry.id] ? (
                        <>
                          <pre className="text-[11px] text-stone-700 whitespace-pre-wrap break-words max-h-48 overflow-y-auto font-sans leading-relaxed">
                            {previewCache[entry.id].text}
                          </pre>
                          <div className="flex justify-end mt-2">
                            <button
                              className="inline-flex items-center gap-1 px-2 py-1 text-[10px] font-medium text-stone-700 hover:bg-stone-100 rounded transition-colors"
                              onClick={() =>
                                setFullViewTarget({
                                  version: entry.version,
                                  content: previewCache[entry.id].content,
                                })
                              }
                              disabled={!previewCache[entry.id].content}
                              title="用 Univer 渲染完整格式"
                            >
                              <Eye className="size-3" />
                              查看完整
                            </button>
                          </div>
                        </>
                      ) : (
                        <div className="text-[11px] text-stone-400 text-center py-2">
                          加载中...
                        </div>
                      )}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )}

        {!loading && versions.length > 0 && (
          <p className="text-[10px] text-stone-400 leading-relaxed mt-3 pt-3 border-t border-stone-100">
            每次保存会生成一条快照，系统最多保留最新 20
            条。恢复时当前状态会自动存档，便于反悔。
          </p>
        )}
      </div>

      {/* 恢复确认弹框 — 复用项目现有 modal 样式 (与 document-list 删除确认一致) */}
      {!!confirmTarget && (
        <div className="fixed inset-0 z-50 flex items-center justify-center">
          <div
            className="fixed inset-0 bg-black/40"
            onClick={() => setConfirmTarget(null)}
          />
          <div className="relative z-10 bg-white border border-[#E8E8E6] rounded-2xl shadow-[0_20px_60px_-12px_rgba(0,0,0,0.08)] p-6 max-w-md w-full mx-4">
            <h2 className="text-lg font-semibold text-[#1A1A1A]">
              恢复历史版本
            </h2>
            <p className="text-sm text-[#8A8A8A] mt-2 leading-relaxed">
              确认恢复到版本
              <span className="font-medium text-[#1A1A1A] mx-1">
                v{confirmTarget.version}
              </span>
              （{formatTime(confirmTarget.create_time)}）？
              <br />
              当前未保存的更改将丢失，恢复后页面会自动刷新。
            </p>
            <div className="flex flex-col-reverse sm:flex-row sm:justify-end sm:space-x-2 mt-6">
              <button
                onClick={() => setConfirmTarget(null)}
                disabled={restoringId !== null}
                className="inline-flex items-center justify-center whitespace-nowrap rounded-lg text-sm font-medium transition-colors h-10 px-4 py-2 border border-[#E8E8E6] text-[#555555] hover:bg-[#F5F5F4] hover:text-[#1A1A1A] mt-2 sm:mt-0 disabled:opacity-50"
              >
                取消
              </button>
              <button
                onClick={() => {
                  const target = confirmTarget;
                  setConfirmTarget(null);
                  if (target) handleRestore(target);
                }}
                disabled={restoringId !== null}
                className="inline-flex items-center justify-center gap-1.5 whitespace-nowrap rounded-lg text-sm font-medium transition-colors h-10 px-4 py-2 bg-stone-900 hover:bg-stone-700 text-white disabled:opacity-50"
              >
                {restoringId ? (
                  <>
                    <div className="w-3.5 h-3.5 border-2 border-white/40 border-t-white rounded-full animate-spin" />
                    恢复中...
                  </>
                ) : (
                  <>
                    <RotateCcw className="size-3.5" />
                    确认恢复
                  </>
                )}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* 错误提示弹框 — 同 modal 样式 */}
      {!!errorMsg && (
        <div className="fixed inset-0 z-50 flex items-center justify-center">
          <div
            className="fixed inset-0 bg-black/40"
            onClick={() => setErrorMsg(null)}
          />
          <div className="relative z-10 bg-white border border-[#E8E8E6] rounded-2xl shadow-[0_20px_60px_-12px_rgba(0,0,0,0.08)] p-6 max-w-md w-full mx-4">
            <h2 className="text-lg font-semibold text-[#1A1A1A]">恢复失败</h2>
            <p className="text-sm text-[#8A8A8A] mt-2">{errorMsg}</p>
            <div className="flex flex-col-reverse sm:flex-row sm:justify-end sm:space-x-2 mt-6">
              <button
                onClick={() => setErrorMsg(null)}
                className="inline-flex items-center justify-center whitespace-nowrap rounded-lg text-sm font-medium transition-colors h-10 px-4 py-2 bg-stone-900 hover:bg-stone-700 text-white"
              >
                知道了
              </button>
            </div>
          </div>
        </div>
      )}

      {/* 「查看完整」模态 — Univer 只读渲染历史快照 */}
      {!!fullViewTarget && (
        <div className="fixed inset-0 z-50 flex items-center justify-center">
          <div
            className="fixed inset-0 bg-black/50"
            onClick={() => setFullViewTarget(null)}
          />
          <div className="relative z-10 bg-white rounded-2xl shadow-[0_20px_60px_-12px_rgba(0,0,0,0.20)] w-[90vw] max-w-5xl h-[85vh] flex flex-col overflow-hidden">
            <div className="flex items-center justify-between px-4 py-2.5 border-b border-stone-100">
              <div className="flex items-center gap-1.5 text-stone-700">
                <History className="size-4" />
                <span className="text-sm font-semibold">
                  版本 v{fullViewTarget.version} 预览
                </span>
                <span className="text-[10px] text-stone-400 ml-1">只读</span>
              </div>
              <button
                className="size-7 flex items-center justify-center rounded text-stone-400 hover:text-stone-700 hover:bg-stone-100"
                onClick={() => setFullViewTarget(null)}
              >
                <X className="size-4" />
              </button>
            </div>
            <div className="flex-1 min-h-0">
              {fullViewContent ? (
                fileType === 'xlsx' ? (
                  <UniverSpreadsheetVersionPreview content={fullViewContent} />
                ) : (
                  <UniverVersionPreview content={fullViewContent} />
                )
              ) : (
                <div className="w-full h-full flex items-center justify-center text-sm text-stone-400">
                  该版本无内容快照
                </div>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
