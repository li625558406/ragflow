import api from '@/utils/api';
import { BookOpen, FileText, Search, X } from 'lucide-react';
import { useCallback, useRef, useState } from 'react';

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
  positions: number[][];
}

interface SearchResponse {
  chunks: ChunkItem[];
  total: number;
  doc_aggs: { doc_name: string; doc_id: string; count: number }[];
  kb_names: Record<string, string>;
}

interface Props {
  apiFetch: (url: string, options?: RequestInit) => Promise<Response>;
}

export default function KbSearch({ apiFetch }: Props) {
  const [query, setQuery] = useState('');
  const [loading, setLoading] = useState(false);
  const [results, setResults] = useState<SearchResponse | null>(null);
  const [page, setPage] = useState(1);
  const [total, setTotal] = useState(0);
  const pageSize = 10;

  const apiFetchRef = useRef(apiFetch);
  apiFetchRef.current = apiFetch;

  const doSearch = useCallback(async (q: string, p: number) => {
    const trimmed = q.trim();
    if (!trimmed) return;
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
        setResults(res.data);
        setTotal(res.data.total || 0);
      }
    } catch (e) {
      console.error('知识库搜索失败:', e);
    } finally {
      setLoading(false);
    }
  }, []);

  const handleSearch = useCallback(() => {
    setPage(1);
    setResults(null);
    doSearch(query, 1);
  }, [query, doSearch]);

  const handlePageChange = useCallback(
    (p: number) => {
      setPage(p);
      doSearch(query, p);
    },
    [query, doSearch],
  );

  const handleClear = useCallback(() => {
    setQuery('');
    setResults(null);
    setTotal(0);
    setPage(1);
  }, []);

  const totalPages = Math.ceil(total / pageSize);

  return (
    <div className="flex-1 flex flex-col min-h-0 overflow-hidden">
      {/* Search bar */}
      <div className="shrink-0 px-6 pt-5 pb-4">
        <div className="flex items-center gap-3">
          <div className="flex-1 relative">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 size-4 text-[#A3A3A3]" />
            <input
              type="text"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') handleSearch();
              }}
              placeholder="输入关键词搜索知识库内容..."
              className="w-full pl-9 pr-9 py-2.5 rounded-xl border border-[#D4D4D4] bg-white text-sm text-[#333333] placeholder:text-[#A3A3A3] focus:outline-none focus:border-[#A3A3A3] focus:ring-1 focus:ring-[#A3A3A3] transition"
            />
            {query && (
              <button
                onClick={handleClear}
                className="absolute right-3 top-1/2 -translate-y-1/2 text-[#A3A3A3] hover:text-[#525252] cursor-pointer"
              >
                <X className="size-3.5" />
              </button>
            )}
          </div>
          <button
            onClick={handleSearch}
            disabled={loading || !query.trim()}
            className="shrink-0 px-5 py-2.5 rounded-xl bg-[#000000] text-white text-sm font-medium hover:bg-[#333333] disabled:opacity-40 disabled:cursor-not-allowed transition cursor-pointer"
          >
            {loading ? '搜索中...' : '搜索'}
          </button>
        </div>
      </div>

      {/* Results */}
      <div className="flex-1 overflow-y-auto px-6 pb-6">
        {loading && !results && (
          <div className="flex items-center justify-center h-48">
            <div className="flex flex-col items-center gap-3">
              <div className="w-6 h-6 border-2 border-[#D4D4D4] border-t-[#000000] rounded-full animate-spin" />
              <span className="text-xs text-[#A3A3A3]">正在搜索知识库...</span>
            </div>
          </div>
        )}

        {!loading && !results && (
          <div className="flex flex-col items-center justify-center h-48 text-[#A3A3A3]">
            <BookOpen className="size-10 mb-3 text-[#D4D4D4]" />
            <p className="text-sm">输入关键词搜索所有知识库内容</p>
          </div>
        )}

        {results && results.chunks.length === 0 && !loading && (
          <div className="flex flex-col items-center justify-center h-48 text-[#A3A3A3]">
            <Search className="size-10 mb-3 text-[#D4D4D4]" />
            <p className="text-sm">未找到匹配结果</p>
          </div>
        )}

        {results && results.chunks.length > 0 && (
          <div className="space-y-4">
            {/* Result summary */}
            <div className="flex items-center justify-between">
              <span className="text-xs text-[#A3A3A3]">
                共找到 {total} 条结果
                {Object.keys(results.kb_names).length > 0 &&
                  `，来自 ${Object.keys(results.kb_names).length} 个知识库`}
              </span>
              {Object.keys(results.kb_names).length > 1 && (
                <div className="flex flex-wrap gap-1.5">
                  {Object.entries(results.kb_names).map(([id, name]) => (
                    <span
                      key={id}
                      className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full bg-[#EAEAEA] text-[11px] text-[#525252]"
                    >
                      <BookOpen className="size-3" />
                      {name}
                    </span>
                  ))}
                </div>
              )}
            </div>

            {/* Chunk cards */}
            {results.chunks.map((chunk, idx) => (
              <div
                key={chunk.chunk_id}
                className={`cs-list-enter cs-list-d${Math.min(idx, 7)} rounded-xl border border-[#EAEAEA] p-4 hover:border-[#D4D4D4] hover:shadow-[0_2px_8px_rgba(0,0,0,0.06)] transition`}
              >
                {/* Header: kb + doc */}
                <div className="flex items-center gap-2 mb-2">
                  <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full bg-[#F0F0F0] text-[11px] text-[#525252] shrink-0">
                    <BookOpen className="size-3" />
                    {chunk.kb_name || '未知知识库'}
                  </span>
                  <span className="inline-flex items-center gap-1 text-[11px] text-[#A3A3A3] truncate">
                    <FileText className="size-3 shrink-0" />
                    {chunk.docnm_kwd || '未知文档'}
                  </span>
                  <span className="ml-auto text-[11px] text-[#A3A3A3] shrink-0">
                    相似度 {(chunk.similarity * 100).toFixed(1)}%
                  </span>
                </div>

                {/* Content */}
                <p className="text-sm text-[#333333] leading-relaxed line-clamp-4 whitespace-pre-wrap">
                  {chunk.content_with_weight}
                </p>

                {/* Tags */}
                {chunk.tag_kwd && chunk.tag_kwd.length > 0 && (
                  <div className="flex flex-wrap gap-1 mt-2">
                    {chunk.tag_kwd.slice(0, 5).map((tag) => (
                      <span
                        key={tag}
                        className="px-1.5 py-0.5 rounded bg-[#F5F5F5] text-[10px] text-[#A3A3A3]"
                      >
                        {tag}
                      </span>
                    ))}
                    {chunk.tag_kwd.length > 5 && (
                      <span className="px-1.5 py-0.5 rounded bg-[#F5F5F5] text-[10px] text-[#A3A3A3]">
                        +{chunk.tag_kwd.length - 5}
                      </span>
                    )}
                  </div>
                )}

                {/* Score details */}
                <div className="flex gap-4 mt-2 text-[10px] text-[#A3A3A3]">
                  <span>
                    向量 {(chunk.vector_similarity * 100).toFixed(1)}%
                  </span>
                  <span>文本 {(chunk.term_similarity * 100).toFixed(1)}%</span>
                </div>
              </div>
            ))}

            {/* Pagination */}
            {totalPages > 1 && (
              <div className="flex items-center justify-center gap-2 pt-4">
                <button
                  onClick={() => handlePageChange(page - 1)}
                  disabled={page <= 1 || loading}
                  className="px-3 py-1.5 rounded-lg text-sm text-[#525252] hover:bg-[#EAEAEA] disabled:opacity-30 disabled:cursor-not-allowed transition cursor-pointer"
                >
                  上一页
                </button>
                <span className="text-sm text-[#A3A3A3]">
                  {page} / {totalPages}
                </span>
                <button
                  onClick={() => handlePageChange(page + 1)}
                  disabled={page >= totalPages || loading}
                  className="px-3 py-1.5 rounded-lg text-sm text-[#525252] hover:bg-[#EAEAEA] disabled:opacity-30 disabled:cursor-not-allowed transition cursor-pointer"
                >
                  下一页
                </button>
              </div>
            )}
          </div>
        )}

        {/* Loading more */}
        {loading && results && (
          <div className="flex items-center justify-center py-8">
            <div className="w-5 h-5 border-2 border-[#D4D4D4] border-t-[#000000] rounded-full animate-spin" />
          </div>
        )}
      </div>
    </div>
  );
}
