import api from '@/utils/api';
import { getAuthorization } from '@/utils/authorization-util';
import { ArrowLeft, BookOpen, ExternalLink, Search, X } from 'lucide-react';
import { useCallback, useEffect, useRef, useState } from 'react';

const INPUT_CLASS =
  'h-10 px-3 text-sm text-[#000000] border border-[#D4D4D4] bg-white hover:border-[#A3A3A3] focus:border-[#000000] focus-visible:ring-0 rounded-lg transition-all';

interface ChunkItem {
  chunk_id: string;
  content_with_weight: string;
  similarity: number;
  vector_similarity: number;
  term_similarity: number;
  kb_id: string;
  kb_name: string;
  doc_id: string;
  docnm_kwd: string;
  tag_kwd: string[];
  important_kwd: string[];
  positions: number[][];
  image_id: string;
}

interface Props {
  apiFetch: (url: string, options?: RequestInit) => Promise<Response>;
}

// ── Content cleaning ─────────────────────────

function cleanContent(raw: string): string {
  if (!raw) return '';
  let s = raw;

  // Strip HTML tags
  s = s.replace(/<br\s*\/?>/gi, '\n');
  s = s.replace(
    /<\/?(p|div|span|h[1-6]|li|ul|ol|tr|td|th|table|blockquote|section|article|header|footer|nav|main|figure|figcaption|details|summary)\b[^>]*>/gi,
    '\n',
  );
  s = s.replace(/<hr\s*\/?>/gi, '\n────────\n');
  s = s.replace(/<(strong|b|em|i|u|mark|small|sub|sup)\b[^>]*>/gi, '');
  s = s.replace(/<\/(strong|b|em|i|u|mark|small|sub|sup)>/gi, '');
  s = s.replace(/<img\b[^>]*alt="([^"]*)"[^>]*>/gi, '$1');
  s = s.replace(/<img\b[^>]*>/gi, '[图片]');
  s = s.replace(/<a\b[^>]*href="([^"]*)"[^>]*>(.*?)<\/a>/gi, '$2($1)');
  s = s.replace(/<[^>]+>/g, '');
  // Decode common HTML entities
  s = s.replace(/&nbsp;/g, ' ');
  s = s.replace(
    /&(lt|gt|amp|quot|apos|#39);/g,
    (_, entity) =>
      ({
        lt: '<',
        gt: '>',
        amp: '&',
        quot: '"',
        apos: "'",
        '#39': "'",
      })[entity] || entity,
  );

  // Strip Markdown heading hashes
  s = s.replace(/^#{1,6}\s+/gm, '');
  // Strip Markdown bold/italic/strikethrough markers
  s = s.replace(/\*\*\*(.+?)\*\*\*/g, '$1');
  s = s.replace(/\*\*(.+?)\*\*/g, '$1');
  s = s.replace(/\*(.+?)\*/g, '$1');
  s = s.replace(/~~(.+?)~~/g, '$1');
  // Strip Markdown links (leave text)
  s = s.replace(/\[([^\]]+)\]\([^)]+\)/g, '$1');
  // Strip Markdown image syntax (leave alt)
  s = s.replace(/!\[([^\]]*)\]\([^)]+\)/g, '$1');
  // Strip Markdown blockquotes
  s = s.replace(/^>\s?/gm, '');
  // Strip Markdown code fences and inline code
  s = s.replace(/```[\s\S]*?```/g, '');
  s = s.replace(/`([^`]+)`/g, '$1');
  // Strip Markdown horizontal rules
  s = s.replace(/^[-*_]{3,}\s*$/gm, '');
  // Strip Markdown unordered list markers
  s = s.replace(/^\s*[-*+]\s+/gm, '');
  // Strip Markdown ordered list markers
  s = s.replace(/^\s*\d+\.\s+/gm, '');
  // Strip Markdown table pipes
  s = s.replace(/^\|?\s*[-:]+[-|:\s]+\|?\s*$/gm, '');
  s = s.replace(/^\|/gm, '');
  s = s.replace(/\|$/gm, '');

  // Clean up whitespace
  s = s.replace(/\t/g, '    ');
  s = s.replace(/  +/g, ' ');
  s = s.replace(/\n{3,}/g, '\n\n');

  return s.trim();
}

// ── Similarity helpers ────────────────────────

function similarityColor(sim: number): string {
  if (sim >= 0.8) return 'text-[#16A34A] bg-[#F0FDF4]';
  if (sim >= 0.6) return 'text-[#2563EB] bg-[#EFF6FF]';
  if (sim >= 0.4) return 'text-[#D97706] bg-[#FFFBEB]';
  return 'text-[#A3A3A3] bg-[#F5F5F5]';
}

function similarityLabel(sim: number): string {
  if (sim >= 0.8) return '高';
  if (sim >= 0.6) return '中';
  if (sim >= 0.4) return '低';
  return '弱';
}

// ── Highlight + auto-link ───────────────────

// eslint-disable-next-line no-useless-escape
const URL_RE = /(https?:\/\/[^\s<>\[\]{}|"'`\u3000-\u303F\uFF00-\uFFFF]+)/g;

function highlightSegment(text: string, query: string) {
  if (!query.trim()) return text;
  // eslint-disable-next-line no-useless-escape
  const escaped = query.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const parts = text.split(new RegExp(`(${escaped})`, 'gi'));
  return parts.map((part, i) =>
    i % 2 === 1 ? (
      <mark
        key={i}
        className="bg-[#FEF08A]/60 text-[#000000] rounded-sm px-0.5"
      >
        {part}
      </mark>
    ) : (
      <span key={i}>{part}</span>
    ),
  );
}

function renderContent(content: string, query: string) {
  const cleaned = cleanContent(content);
  if (!cleaned) return null;

  const segments: { text: string; isUrl: boolean }[] = [];
  let last = 0;
  URL_RE.lastIndex = 0;
  let m;
  while ((m = URL_RE.exec(cleaned)) !== null) {
    if (m.index > last)
      segments.push({ text: cleaned.slice(last, m.index), isUrl: false });
    segments.push({ text: m[0], isUrl: true });
    last = m.index + m[0].length;
  }
  if (last < cleaned.length)
    segments.push({ text: cleaned.slice(last), isUrl: false });

  return segments.map((seg, i) =>
    seg.isUrl ? (
      <a
        key={i}
        href={seg.text}
        target="_blank"
        rel="noopener noreferrer"
        className="text-[#2563EB] underline decoration-1 underline-offset-2 hover:text-[#1D4ED8] transition-colors"
        onClick={(e) => e.stopPropagation()}
      >
        {seg.text}
      </a>
    ) : (
      <span key={i}>{highlightSegment(seg.text, query)}</span>
    ),
  );
}

// ── Component ─────────────────────────────────

export default function KbSearch({ apiFetch }: Props) {
  const [query, setQuery] = useState('');
  const [loading, setLoading] = useState(false);
  const [chunks, setChunks] = useState<ChunkItem[]>([]);
  const [total, setTotal] = useState(0);
  const [kbNames, setKbNames] = useState<Record<string, string>>({});
  const [, setPage] = useState(1);
  const [hasSearched, setHasSearched] = useState(false);
  const [hasMore, setHasMore] = useState(true);
  const [selectedChunk, setSelectedChunk] = useState<ChunkItem | null>(null);
  const pageSize = 10;

  const apiFetchRef = useRef(apiFetch);
  apiFetchRef.current = apiFetch;
  const loadingRef = useRef(false);
  const pageRef = useRef(1);
  const scrollPosRef = useRef(0);

  // Core fetch — always call via apiFetchRef, results via setters
  const doFetch = useCallback(async (q: string, p: number, append: boolean) => {
    const trimmed = q.trim();
    if (!trimmed) return;
    loadingRef.current = true;
    setLoading(true);
    try {
      const resp = await apiFetchRef.current(api.searchAllDatasets, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          question: trimmed,
          page: p,
          size: pageSize,
        }),
      });
      const res = await resp.json();
      if (res.code === 0 && res.data) {
        const newChunks = res.data.chunks || [];
        setChunks((prev) => (append ? [...prev, ...newChunks] : newChunks));
        setTotal(res.data.total || 0);
        setKbNames((prev) => ({ ...prev, ...res.data.kb_names }));
        setHasMore(newChunks.length >= pageSize);
      }
    } catch (e) {
      console.error('知识库搜索失败:', e);
    } finally {
      loadingRef.current = false;
      setLoading(false);
    }
  }, []);

  // New search (button click)
  const handleSearch = useCallback(() => {
    setChunks([]);
    setTotal(0);
    setKbNames({});
    setPage(1);
    pageRef.current = 1;
    setHasMore(true);
    setSelectedChunk(null);
    setHasSearched(true);
    doFetch(query, 1, false);
  }, [query, doFetch]);

  // Load next page (infinite scroll)
  const loadNextPage = useCallback(() => {
    if (loadingRef.current) return;
    const next = pageRef.current + 1;
    pageRef.current = next;
    setPage(next);
    doFetch(query, next, true);
  }, [query, doFetch]);

  const handleBack = useCallback(() => {
    setChunks([]);
    setTotal(0);
    setKbNames({});
    setPage(1);
    pageRef.current = 1;
    setHasMore(true);
    setHasSearched(false);
    setSelectedChunk(null);
  }, []);

  // Scroll event for infinite scroll (more reliable than IntersectionObserver across hidden/display toggles)
  const scrollContainerRef = useRef<HTMLDivElement>(null);
  const loadNextPageRef = useRef(loadNextPage);
  loadNextPageRef.current = loadNextPage;
  useEffect(() => {
    const el = scrollContainerRef.current;
    if (!el) return;
    const onScroll = () => {
      if (loadingRef.current || !hasMore) return;
      const { scrollTop, scrollHeight, clientHeight } = el;
      if (scrollTop + clientHeight >= scrollHeight - 200) {
        loadNextPageRef.current();
      }
    };
    el.addEventListener('scroll', onScroll, { passive: true });
    return () => el.removeEventListener('scroll', onScroll);
  });

  const handleViewImage = useCallback((chunk: ChunkItem) => {
    const token = getAuthorization();
    if (!token || !chunk.image_id) return;
    const imgId =
      chunk.image_id.includes('-') && chunk.image_id.length > 40
        ? chunk.image_id
        : `${chunk.kb_id}-${chunk.chunk_id}`;
    const url = `/api/v1/documents/images/${imgId}?Authorization=${encodeURIComponent(token)}`;
    window.open(url, '_blank', 'noopener,noreferrer');
  }, []);

  // ─── Detail view ───────────────────────────
  if (selectedChunk) {
    const handleBackToList = () => {
      setSelectedChunk(null);
      requestAnimationFrame(() => {
        if (scrollContainerRef.current) {
          scrollContainerRef.current.scrollTop = scrollPosRef.current;
        }
      });
    };
    return (
      <div className="flex-1 flex flex-col min-h-0 overflow-hidden">
        <div className="shrink-0 px-6 py-3 border-b border-[#F0F0F0]">
          <button
            onClick={handleBackToList}
            className="flex items-center gap-1 text-sm text-[#525252] hover:text-[#000000] transition cursor-pointer"
          >
            <ArrowLeft className="size-4" />
            返回列表
          </button>
        </div>

        <div className="flex-1 overflow-y-auto px-6 py-5">
          {/* Meta bar */}
          <div className="flex items-center gap-2 mb-4 flex-wrap">
            <span className="inline-flex items-center gap-1 px-2.5 py-1 rounded-lg bg-[#F0F0F0] text-xs font-medium text-[#525252]">
              <BookOpen className="size-3.5" />
              {selectedChunk.kb_name || '未知知识库'}
            </span>
            <span className="inline-flex items-center gap-1 px-2.5 py-1 rounded-lg bg-[#F5F5F5] text-xs text-[#333333]">
              {selectedChunk.docnm_kwd || '未知文档'}
            </span>
            <span
              className={`inline-flex items-center px-2.5 py-1 rounded-lg text-xs font-medium ${similarityColor(selectedChunk.similarity)}`}
            >
              匹配度 {(selectedChunk.similarity * 100).toFixed(1)}% (
              {similarityLabel(selectedChunk.similarity)})
            </span>
          </div>

          {/* Scores */}
          <div className="grid grid-cols-3 gap-3 mb-5">
            {[
              { label: '综合相似度', value: selectedChunk.similarity },
              { label: '向量相似度', value: selectedChunk.vector_similarity },
              { label: '文本相似度', value: selectedChunk.term_similarity },
            ].map((item) => (
              <div
                key={item.label}
                className="rounded-lg border border-[#F0F0F0] p-3 text-center"
              >
                <div className="text-lg font-semibold text-[#000000]">
                  {(item.value * 100).toFixed(1)}%
                </div>
                <div className="text-[11px] text-[#A3A3A3]">{item.label}</div>
              </div>
            ))}
          </div>

          {/* Cleaned content */}
          <div className="mb-5">
            <h3 className="text-sm font-semibold text-[#1a1a1a] mb-2">
              文段内容
            </h3>
            <div className="rounded-xl border border-[#F0F0F0] bg-[#FAFAFA] p-4">
              <p className="text-sm text-[#333333] leading-relaxed whitespace-pre-wrap">
                {renderContent(selectedChunk.content_with_weight, query)}
              </p>
            </div>
          </div>

          {/* Actions */}
          {selectedChunk.image_id && (
            <button
              onClick={() => handleViewImage(selectedChunk)}
              className="inline-flex items-center gap-1.5 px-4 py-2 rounded-lg border border-[#D4D4D4] text-sm text-[#333333] hover:bg-[#F5F5F5] transition cursor-pointer"
            >
              <ExternalLink className="size-3.5" />
              查看原文图片
            </button>
          )}
        </div>
      </div>
    );
  }

  // ─── Results view ──────────────────────────
  if (hasSearched) {
    return (
      <div className="flex-1 flex flex-col min-h-0 overflow-hidden">
        {/* Compact top bar */}
        <div className="shrink-0 px-6 py-3 border-b border-[#F0F0F0]">
          <div className="flex items-center gap-3">
            <button
              onClick={handleBack}
              className="px-3 h-8 rounded-lg bg-[#000000] text-white text-xs font-medium hover:bg-[#171717] transition cursor-pointer"
            >
              返回
            </button>
            <span className="text-sm font-semibold text-[#000000] truncate">
              &ldquo;{query}&rdquo;
            </span>
            {Object.keys(kbNames).length > 1 && (
              <div className="flex gap-1 ml-1">
                {Object.entries(kbNames).map(([id, name]) => (
                  <span
                    key={id}
                    className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full bg-[#EAEAEA] text-[10px] text-[#525252]"
                  >
                    <BookOpen className="size-2.5" />
                    {name}
                  </span>
                ))}
              </div>
            )}
            <span className="ml-auto text-xs text-[#A3A3A3] shrink-0">
              共 <b className="text-[#000000]">{total}</b> 条结果
            </span>
          </div>
        </div>

        {/* Scrollable list */}
        <div
          ref={scrollContainerRef}
          className="flex-1 overflow-y-auto px-6 py-4"
        >
          {chunks.length === 0 && !loading && (
            <div className="flex flex-col items-center justify-center h-48 text-[#A3A3A3]">
              <Search className="size-10 mb-3 text-[#D4D4D4]" />
              <p className="text-sm">未找到匹配结果，请换个关键词试试</p>
            </div>
          )}

          {chunks.length > 0 && (
            <div className="space-y-3">
              {chunks.map((chunk) => (
                <div
                  key={chunk.chunk_id}
                  onClick={() => {
                    if (scrollContainerRef.current) {
                      scrollPosRef.current =
                        scrollContainerRef.current.scrollTop;
                    }
                    setSelectedChunk(chunk);
                  }}
                  className={`group bg-white rounded-xl border border-[#E8E8E8] p-5 transition-all duration-200 hover:shadow-[0_4px_20px_rgba(0,0,0,0.06)] hover:-translate-y-0.5 cursor-pointer`}
                >
                  {/* Top row */}
                  <div className="flex items-center justify-between mb-2.5">
                    <div className="flex items-center gap-1.5 min-w-0">
                      <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-md text-[11px] font-medium shrink-0 bg-gradient-to-r from-[#EFF6FF] to-[#F0F0FF] text-[#2563EB]">
                        <BookOpen className="size-3" />
                        {chunk.kb_name || '未知知识库'}
                      </span>
                      <span className="text-[11px] text-[#A3A3A3] truncate">
                        {chunk.docnm_kwd || '未知文档'}
                      </span>
                    </div>
                    <span
                      className={`inline-flex items-center px-2 py-0.5 rounded-md text-[11px] font-bold shrink-0 ${similarityColor(chunk.similarity)}`}
                    >
                      {(chunk.similarity * 100).toFixed(1)}%
                    </span>
                  </div>

                  {/* Content */}
                  <div className="mb-3">
                    <p className="text-sm text-[#333333] leading-relaxed line-clamp-3">
                      {renderContent(chunk.content_with_weight, query)}
                    </p>
                  </div>

                  {/* Bottom row */}
                  <div className="flex items-center gap-1.5 flex-wrap">
                    {chunk.important_kwd?.length > 0 && (
                      <div className="flex flex-wrap gap-1">
                        {chunk.important_kwd.slice(0, 4).map((kw) => (
                          <span
                            key={kw}
                            className="px-1.5 py-0.5 rounded bg-[#EFF6FF] text-[10px] text-[#2563EB] font-medium"
                          >
                            {kw}
                          </span>
                        ))}
                      </div>
                    )}
                    {chunk.tag_kwd?.length > 0 && (
                      <div className="flex flex-wrap gap-1">
                        {chunk.tag_kwd.slice(0, 3).map((tag) => (
                          <span
                            key={tag}
                            className="px-1.5 py-0.5 rounded bg-[#F5F5F5] text-[10px] text-[#A3A3A3]"
                          >
                            {tag}
                          </span>
                        ))}
                      </div>
                    )}
                    <div className="ml-auto flex items-center gap-1.5 shrink-0">
                      <span className="inline-flex items-center gap-0.5 px-1.5 py-0.5 rounded-md bg-[#F0F0F7] text-[10px] text-[#7C3AED] font-medium">
                        向量 {(chunk.vector_similarity * 100).toFixed(1)}%
                      </span>
                      <span className="inline-flex items-center gap-0.5 px-1.5 py-0.5 rounded-md bg-[#F0FDF4] text-[10px] text-[#16A34A] font-medium">
                        文本 {(chunk.term_similarity * 100).toFixed(1)}%
                      </span>
                    </div>
                  </div>
                </div>
              ))}

              {/* Loading / no more indicator */}
              {loading && (
                <div className="flex items-center justify-center py-6">
                  <div className="w-5 h-5 border-2 border-[#D4D4D4] border-t-[#000000] rounded-full animate-spin" />
                </div>
              )}
              {!loading && !hasMore && chunks.length > 0 && (
                <p className="text-center text-xs text-[#D4D4D4] py-4">
                  已加载全部 {chunks.length} 条结果
                </p>
              )}
            </div>
          )}

          {loading && chunks.length === 0 && (
            <div className="flex items-center justify-center h-48">
              <div className="flex flex-col items-center gap-3">
                <div className="w-6 h-6 border-2 border-[#D4D4D4] border-t-[#000000] rounded-full animate-spin" />
                <span className="text-xs text-[#A3A3A3]">
                  正在搜索知识库...
                </span>
              </div>
            </div>
          )}
        </div>
      </div>
    );
  }

  // ─── Search hero (centered, before search) ──
  return (
    <div className="flex-1 overflow-auto">
      <div className="min-h-full flex items-center justify-center px-6 py-12">
        <div className="w-full max-w-2xl">
          <div className="text-center mb-8">
            <div className="cs-card-enter inline-flex items-center justify-center size-16 rounded-2xl bg-[#F5F5F5] mb-4">
              <BookOpen className="size-7 text-[#404040]" />
            </div>
            <h1 className="cs-card-enter cs-card-d1 text-2xl font-bold text-[#000000] tracking-tight">
              知识库搜索
            </h1>
            <p className="cs-card-enter cs-card-d1 text-sm text-[#A3A3A3] mt-1">
              跨所有知识库检索文档内容
            </p>
          </div>

          <div className="cs-card-enter cs-card-d2 bg-white rounded-xl border border-[#E8E8E8] shadow-[0_4px_24px_rgba(0,0,0,0.04)] p-6">
            <div className="mb-5">
              <label className="block text-sm font-medium text-[#1a1a1a] mb-1.5">
                搜索关键词
              </label>
              <div className="relative">
                <input
                  type="text"
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter') handleSearch();
                  }}
                  placeholder="输入关键词搜索知识库内容..."
                  className={`${INPUT_CLASS} w-full pr-9`}
                />
                {query && (
                  <button
                    onClick={() => setQuery('')}
                    className="absolute right-2 top-1/2 -translate-y-1/2 text-[#A3A3A3] hover:text-[#000000] transition-colors cursor-pointer"
                  >
                    <X className="size-3.5" />
                  </button>
                )}
              </div>
            </div>

            <button
              onClick={handleSearch}
              disabled={loading || !query.trim()}
              className="w-full h-11 bg-[#000000] hover:bg-[#171717] text-white text-sm font-medium rounded-lg transition-all hover:shadow-[0_4px_12px_rgba(0,0,0,0.15)] disabled:opacity-40 disabled:cursor-not-allowed cursor-pointer"
            >
              <Search className="size-4 mr-2 inline-block" />
              搜索知识库
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
