import { Button } from '@/components/ui/button';
import { getAuthorization } from '@/utils/authorization-util';
import { Search, X } from 'lucide-react';
import { useCallback, useRef, useState } from 'react';

interface ConstructionItem {
  id: number;
  title: string;
  summary: string;
  publishTime: string;
  proviceCode: string;
  cityCode: string;
  countyCode: string;
  hasFile: number;
}

const INPUT_CLASS =
  'h-9 px-3 text-xs text-[#000000] border border-[#D4D4D4] bg-white hover:border-[#A3A3A3] focus:border-[#000000] focus:ring-2 focus:ring-[#000000]/10 rounded-lg transition-all';

async function constructionFetch(url: string, params?: Record<string, any>) {
  const qs = params
    ? '?' +
      Object.entries(params)
        .filter(([, v]) => v !== undefined && v !== '')
        .map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(v)}`)
        .join('&')
    : '';
  const resp = await fetch(`/api/v1/bid/${url}${qs}`, {
    headers: { Authorization: getAuthorization() },
  });
  if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
  const json = await resp.json();
  if (json.code !== 0)
    throw new Error(json.message || `API error code=${json.code}`);
  return json;
}

const PAGE_SIZE = 20;

export default function ConstructionList() {
  const [items, setItems] = useState<ConstructionItem[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [hasSearched, setHasSearched] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const pageRef = useRef(1);
  const [keyword, setKeyword] = useState('');
  const [startDate, setStartDate] = useState('');
  const [endDate, setEndDate] = useState('');

  const doFetch = useCallback(
    async (page: number) => {
      setLoading(true);
      setError(null);
      try {
        const json = await constructionFetch('construction/projects', {
          page: page,
          items_per_page: PAGE_SIZE,
          keyword: keyword || undefined,
          start_date: startDate ? `${startDate} 00:00:00` : undefined,
          end_date: endDate ? `${endDate} 23:59:59` : undefined,
        });
        const data = json.data;
        if (page === 1) {
          setItems(data.projects || []);
        } else {
          setItems((prev) => [...prev, ...(data.projects || [])]);
        }
        setTotal(data.total || 0);
        setHasSearched(true);
      } catch (e: any) {
        setError(e.message);
      } finally {
        setLoading(false);
      }
    },
    [keyword, startDate, endDate],
  );

  const handleSearch = () => {
    pageRef.current = 1;
    setItems([]);
    doFetch(1);
  };

  const loadMore = () => {
    pageRef.current += 1;
    doFetch(pageRef.current);
  };

  return (
    <div className="flex flex-col h-full">
      <div className="px-4 pt-4 pb-2 flex flex-wrap gap-2 items-end">
        <input
          className={`${INPUT_CLASS} w-48`}
          placeholder="关键词（如：医院、公路）"
          value={keyword}
          onChange={(e) => setKeyword(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && handleSearch()}
        />
        <input
          className={`${INPUT_CLASS} w-36`}
          type="date"
          value={startDate}
          onChange={(e) => setStartDate(e.target.value)}
        />
        <span className="text-xs text-[#999]">~</span>
        <input
          className={`${INPUT_CLASS} w-36`}
          type="date"
          value={endDate}
          onChange={(e) => setEndDate(e.target.value)}
        />
        <Button
          size="sm"
          onClick={handleSearch}
          disabled={loading}
          className="bg-[#000000] hover:bg-[#171717] text-white h-9 text-xs"
        >
          <Search className="w-3.5 h-3.5 mr-1" />
          {loading ? '搜索中...' : '搜索'}
        </Button>
      </div>

      <div className="flex-1 overflow-y-auto px-4 pb-4">
        {error && (
          <div className="mt-2 p-3 bg-red-50 text-red-600 text-xs rounded-lg flex items-center gap-2">
            <X className="w-3.5 h-3.5" />
            {error}
          </div>
        )}

        {!hasSearched && !error && (
          <div className="flex items-center justify-center h-40 text-xs text-[#999]">
            输入条件搜索拟在建项目
          </div>
        )}

        {hasSearched && items.length === 0 && !loading && (
          <div className="flex items-center justify-center h-40 text-xs text-[#999]">
            未找到匹配结果
          </div>
        )}

        {items.map((item, idx) => (
          <div
            key={`${item.id}-${idx}`}
            className="p-3 mb-2 border border-[#E5E5E5] rounded-lg hover:border-[#A3A3A3] transition cursor-pointer group"
          >
            <div
              className="text-[13px] font-medium text-[#000] leading-snug line-clamp-2 mb-1.5"
              dangerouslySetInnerHTML={{ __html: item.title }}
            />
            {item.summary && (
              <div
                className="text-xs text-[#666] leading-relaxed line-clamp-2 mb-1.5"
                dangerouslySetInnerHTML={{ __html: item.summary }}
              />
            )}
            <div className="flex gap-x-3 text-xs text-[#999]">
              {item.publishTime && <span>{item.publishTime}</span>}
              {item.hasFile === 1 && <span>有附件</span>}
            </div>
          </div>
        ))}

        {hasSearched && items.length < total && (
          <div className="flex justify-center mt-2">
            <Button
              size="sm"
              variant="outline"
              onClick={loadMore}
              disabled={loading}
              className="h-8 text-xs"
            >
              {loading
                ? '加载中...'
                : `加载更多 (已显示 ${items.length}/${total})`}
            </Button>
          </div>
        )}
      </div>
    </div>
  );
}
