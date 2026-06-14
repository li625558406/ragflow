import { Button } from '@/components/ui/button';
import { BidCard } from '@/pages/home/bid-card';
import { BidConfigDialog } from '@/pages/home/bid-config-dialog';
import { BidDetailView } from '@/pages/home/bid-detail-view';
import { SearchHero } from '@/pages/home/search-hero';
import { getAuthorization } from '@/utils/authorization-util';
import { format } from 'date-fns';
import { X } from 'lucide-react';
import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from 'react';
import { DateRange } from 'react-day-picker';

export type BidProject = {
  id: number;
  title: string;
  content: string;
  project_money: string;
  publish_time: string;
  part_a_names: string | null;
  part_b_names: string | null;
  provice_code: string;
  city_code: string;
  county_code: string;
  project_class_id: string | null;
  purchase_type_id: string | null;
  news_type_id: number;
  has_file: number;
  contract_end_date: string | null;
  se_keywords: string | null;
  industry_codes: string | null;
  source_type: string | null;
};

export class BidRateLimitError extends Error {
  retryAfter: number;
  constructor(msg: string, retryAfter: number) {
    super(msg);
    this.retryAfter = retryAfter;
  }
}

async function bidFetch(url: string, params?: Record<string, any>) {
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
  if (resp.status === 429) {
    const reset = resp.headers.get('X-RateLimit-Reset');
    const retryAfter = reset ? parseInt(reset, 10) : 10;
    throw new BidRateLimitError('请求过于频繁，请稍后重试', retryAfter);
  }
  if (!resp.ok) {
    const errText = await resp.text().catch(() => '');
    throw new Error(`HTTP ${resp.status}: ${errText || resp.statusText}`);
  }
  const json = await resp.json();
  if (json.code !== 0) {
    throw new Error(json.message || `API error code=${json.code}`);
  }
  return json;
}

const PAGE_SIZE = 20;

export function BidList({
  setListLength,
  setLoading,
}: {
  setListLength: (length: number) => void;
  setLoading?: (loading: boolean) => void;
}) {
  // --- Data ---
  const [projects, setProjects] = useState<BidProject[]>([]);
  const [total, setTotal] = useState(0);
  const [localLoading, setLocalLoading] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [hasSearched, setHasSearched] = useState(false);
  const [searchError, setSearchError] = useState<string | null>(null);
  const [rateLimitCooldown, setRateLimitCooldown] = useState(0);
  const cooldownTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const pageRef = useRef(1);

  // --- Detail / Config sub-views ---
  const [selectedProject, setSelectedProject] = useState<{
    id: number;
    publish_time: string;
    title: string;
  } | null>(null);
  const [configProject, setConfigProject] = useState<{
    id: number;
    title: string;
  } | null>(null);

  // --- Form state ---
  const [keyword, setKeyword] = useState('');
  const [dateRange, setDateRange] = useState<DateRange | undefined>(() => {
    const today = new Date();
    return { from: today, to: today };
  });
  const [selectedProvince, setSelectedProvince] = useState<string>('');
  const [selectedCity, setSelectedCity] = useState<string>('');
  const [selectedIndustryCategory, setSelectedIndustryCategory] =
    useState<string>('');
  const [selectedIndustry, setSelectedIndustry] = useState<string>('');
  const [selectedNewsType, setSelectedNewsType] = useState<string>('');
  const [hasFile, setHasFile] = useState<string>('');
  const [projectMoneyMin, setProjectMoneyMin] = useState('');
  const [projectMoneyMax, setProjectMoneyMax] = useState('');
  const [partAName, setPartAName] = useState('');
  const [partBName, setPartBName] = useState('');
  const [agentName, setAgentName] = useState('');
  const [contractEndDateRange, setContractEndDateRange] = useState<
    DateRange | undefined
  >();

  // --- Applied state (actual search params) ---
  const [appliedKeyword, setAppliedKeyword] = useState('');
  const [appliedDateRange, setAppliedDateRange] = useState<
    DateRange | undefined
  >();
  const [appliedProvince, setAppliedProvince] = useState<string>('');
  const [appliedCity, setAppliedCity] = useState<string>('');
  const [appliedIndustryCategory, setAppliedIndustryCategory] =
    useState<string>('');
  const [appliedIndustry, setAppliedIndustry] = useState<string>('');
  const [appliedFilters, setAppliedFilters] = useState<Record<string, any>>({});

  // --- Options ---
  const [provinceOptions, setProvinceOptions] = useState<
    { label: string; value: string }[]
  >([]);
  const [cityOptions, setCityOptions] = useState<
    { label: string; value: string }[]
  >([]);
  const [areaNameMap, setAreaNameMap] = useState<Record<string, string>>({});
  const [industryTree, setIndustryTree] = useState<any[]>([]);
  const [subIndustryOptions, setSubIndustryOptions] = useState<
    { label: string; value: string }[]
  >([]);

  const industryNameMap = useMemo(() => {
    const map: Record<string, string> = {};
    for (const cat of industryTree) {
      for (const child of cat.children ?? []) {
        map[child.code] = child.name;
      }
    }
    return map;
  }, [industryTree]);

  const industryCategoryOptions = useMemo(
    () =>
      industryTree.map((cat: any) => ({
        label: `${cat.code} - ${cat.name}`,
        value: cat.code,
      })),
    [industryTree],
  );

  // --- Refs for external callbacks ---
  const setListLengthRef = useRef(setListLength);
  setListLengthRef.current = setListLength;
  const setLoadingRef = useRef(setLoading);
  setLoadingRef.current = setLoading;

  // --- Data fetching ---
  const buildParams = useCallback(
    (p: number) => {
      const params: Record<string, any> = {
        page: p,
        items_per_page: PAGE_SIZE,
        data_source: 'api',
      };
      if (appliedKeyword) params.keyword = appliedKeyword;
      if (appliedDateRange?.from)
        params.start_date = format(appliedDateRange.from, 'yyyy-MM-dd');
      if (appliedDateRange?.to)
        params.end_date = format(appliedDateRange.to, 'yyyy-MM-dd');
      if (appliedProvince) params.provice_code = appliedProvince;
      if (appliedCity) params.city_code = appliedCity;
      if (appliedIndustry) params.industry_code = appliedIndustry;
      else if (appliedIndustryCategory)
        params.industry_code = appliedIndustryCategory;
      if (appliedFilters.agent_name)
        params.agent_name = appliedFilters.agent_name;
      if (appliedFilters.part_a_name)
        params.part_a_name = appliedFilters.part_a_name;
      if (appliedFilters.part_b_name)
        params.part_b_name = appliedFilters.part_b_name;
      if (appliedFilters.project_money_min)
        params.project_money_min = appliedFilters.project_money_min;
      if (appliedFilters.project_money_max)
        params.project_money_max = appliedFilters.project_money_max;
      if (
        appliedFilters.file_flag !== undefined &&
        appliedFilters.file_flag !== ''
      )
        params.file_flag = appliedFilters.file_flag;
      if (appliedFilters.news_type_id)
        params.news_type_id = appliedFilters.news_type_id;
      if (appliedFilters.contract_end_min)
        params.contract_end_min = appliedFilters.contract_end_min;
      if (appliedFilters.contract_end_max)
        params.contract_end_max = appliedFilters.contract_end_max;
      return params;
    },
    [
      appliedKeyword,
      appliedDateRange,
      appliedProvince,
      appliedCity,
      appliedIndustry,
      appliedIndustryCategory,
      appliedFilters,
    ],
  );

  const doFetch = useCallback(
    (params: Record<string, any>, append: boolean) => {
      if (append) {
        setLoadingMore(true);
      } else {
        setLocalLoading(true);
        setSearchError(null);
      }
      setLoadingRef.current?.(true);
      bidFetch('projects', params)
        .then((res: any) => {
          const data = res?.data;
          const list: BidProject[] = data?.projects ?? [];
          const newTotal: number = data?.total ?? 0;
          if (append) {
            setProjects((prev) => {
              const combined = [...prev, ...list];
              setListLengthRef.current(combined.length);
              return combined;
            });
          } else {
            setProjects(list);
            setListLengthRef.current(list.length);
          }
          setTotal(newTotal);
        })
        .catch((err: Error) => {
          console.error('Bid search failed:', err.message);
          if (err instanceof BidRateLimitError) {
            if (!append) setSearchError(null);
            const cooldown = err.retryAfter;
            setRateLimitCooldown(cooldown);
            if (cooldownTimerRef.current)
              clearInterval(cooldownTimerRef.current);
            cooldownTimerRef.current = setInterval(() => {
              setRateLimitCooldown((prev) => {
                if (prev <= 1) {
                  if (cooldownTimerRef.current)
                    clearInterval(cooldownTimerRef.current);
                  return 0;
                }
                return prev - 1;
              });
            }, 1000);
          } else if (!append) {
            setProjects([]);
            setTotal(0);
            setListLengthRef.current(0);
            setSearchError(err.message);
          }
        })
        .finally(() => {
          setLocalLoading(false);
          setLoadingMore(false);
          setLoadingRef.current?.(false);
        });
    },
    [],
  );

  // --- Search ---
  const searchTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const handleSearch = useCallback(() => {
    if (!dateRange?.from) return;
    if (searchTimer.current) return;
    searchTimer.current = setTimeout(() => {
      searchTimer.current = null;
    }, 500);

    const industryCode = selectedIndustry || selectedIndustryCategory;

    const advFilters: Record<string, any> = {};
    if (agentName) advFilters.agent_name = agentName;
    if (partAName) advFilters.part_a_name = partAName;
    if (partBName) advFilters.part_b_name = partBName;
    if (projectMoneyMin) advFilters.project_money_min = Number(projectMoneyMin);
    if (projectMoneyMax) advFilters.project_money_max = Number(projectMoneyMax);
    if (hasFile !== '') advFilters.file_flag = Number(hasFile);
    if (selectedNewsType) advFilters.news_type_id = Number(selectedNewsType);
    if (contractEndDateRange?.from)
      advFilters.contract_end_min = format(
        contractEndDateRange.from,
        'yyyy-MM-dd',
      );
    if (contractEndDateRange?.to)
      advFilters.contract_end_max = format(
        contractEndDateRange.to,
        'yyyy-MM-dd',
      );

    setAppliedKeyword(keyword);
    setAppliedDateRange(dateRange);
    setAppliedProvince(selectedProvince);
    setAppliedCity(selectedCity);
    setAppliedIndustryCategory(selectedIndustryCategory);
    setAppliedIndustry(selectedIndustry);
    setAppliedFilters(advFilters);

    pageRef.current = 1;
    scrollPositionRef.current = 0; // reset scroll position on new search

    const params: Record<string, any> = { page: 1, items_per_page: PAGE_SIZE };
    if (keyword) params.keyword = keyword;
    if (dateRange?.from)
      params.start_date = format(dateRange.from, 'yyyy-MM-dd');
    if (dateRange?.to) params.end_date = format(dateRange.to, 'yyyy-MM-dd');
    if (selectedProvince) params.provice_code = selectedProvince;
    if (selectedCity) params.city_code = selectedCity;
    if (industryCode) params.industry_code = industryCode;
    Object.assign(params, advFilters);

    doFetch(params, false);
    setHasSearched(true);
  }, [
    keyword,
    dateRange,
    selectedProvince,
    selectedCity,
    selectedIndustryCategory,
    selectedIndustry,
    agentName,
    partAName,
    partBName,
    projectMoneyMin,
    projectMoneyMax,
    hasFile,
    selectedNewsType,
    contractEndDateRange,
    doFetch,
  ]);

  // --- Infinite scroll ---
  const sentinelRef = useRef<HTMLDivElement>(null);
  const loadingMoreRef = useRef(false);

  // --- Scroll position preservation ---
  const listContainerRef = useRef<HTMLDivElement>(null);
  const scrollPositionRef = useRef(0);

  // Restore scroll position when returning from detail view (fires before paint)
  useLayoutEffect(() => {
    if (selectedProject !== null) return; // still in detail view
    if (!hasSearched) return; // search hero, no list to restore
    const saved = scrollPositionRef.current;
    if (!saved) return; // nothing to restore
    if (listContainerRef.current) {
      listContainerRef.current.scrollTop = saved;
    }
  }, [selectedProject, hasSearched]);

  useEffect(() => {
    loadingMoreRef.current = loadingMore;
  }, [loadingMore]);

  const loadMore = useCallback(() => {
    if (loadingMoreRef.current) return;
    if (projects.length >= total) return;
    pageRef.current += 1;
    const params = buildParams(pageRef.current);
    doFetch(params, true);
  }, [projects.length, total, buildParams, doFetch]);

  useEffect(() => {
    if (!hasSearched) return;
    const sentinel = sentinelRef.current;
    if (!sentinel) return;

    const observer = new IntersectionObserver(
      (entries) => {
        if (entries[0].isIntersecting) {
          loadMore();
        }
      },
      { threshold: 0.1 },
    );

    observer.observe(sentinel);
    return () => observer.disconnect();
  }, [hasSearched, loadMore]);

  // --- Load options on mount ---
  useEffect(() => {
    bidFetch('industries')
      .then((res: any) => setIndustryTree(res?.data ?? []))
      .catch(() => {});
  }, []);

  useEffect(() => {
    if (!selectedIndustryCategory) {
      setSubIndustryOptions([]);
      setSelectedIndustry('');
      return;
    }
    const cat = industryTree.find(
      (c: any) => c.code === selectedIndustryCategory,
    );
    if (cat?.children) {
      setSubIndustryOptions(
        cat.children.map((child: any) => ({
          label: `${child.code} ${child.name}`,
          value: child.code,
        })),
      );
    } else {
      setSubIndustryOptions([]);
    }
    setSelectedIndustry('');
  }, [selectedIndustryCategory, industryTree]);

  useEffect(() => {
    bidFetch('areas', { parent_code: '0', level: 1 })
      .then((res: any) => {
        const list = res?.data ?? [];
        setProvinceOptions(
          list.map((a: any) => ({ label: a.name, value: a.code })),
        );
        const map: Record<string, string> = {};
        list.forEach((a: any) => {
          map[a.code] = a.name;
        });
        Promise.all(
          list.map((p: any) =>
            bidFetch('areas', { parent_code: p.code })
              .then((r: any) => {
                (r?.data ?? []).forEach((c: any) => {
                  map[c.code] = c.name;
                });
              })
              .catch(() => {}),
          ),
        ).then(() => setAreaNameMap(map));
      })
      .catch(() => {});
  }, []);

  useEffect(() => {
    if (!selectedProvince) {
      setCityOptions([]);
      setSelectedCity('');
      return;
    }
    bidFetch('areas', { parent_code: selectedProvince })
      .then((res: any) => {
        setCityOptions(
          (res?.data ?? []).map((a: any) => ({ label: a.name, value: a.code })),
        );
      })
      .catch((err) => {
        console.error(
          'Failed to load cities for province',
          selectedProvince,
          err,
        );
        setCityOptions([]);
      });
    setSelectedCity('');
  }, [selectedProvince]);

  // --- Actions ---
  const handleView = (project: BidProject) => {
    // Save scroll position before switching to detail
    if (listContainerRef.current) {
      scrollPositionRef.current = listContainerRef.current.scrollTop;
    }
    setSelectedProject({
      id: project.id,
      publish_time: project.publish_time || '',
      title: project.title,
    });
  };

  const handleConfig = (project: BidProject) => {
    setConfigProject({ id: project.id, title: project.title });
  };

  const handleBackToSearch = () => {
    setHasSearched(false);
  };

  // --- Active filter count for compact bar ---
  const activeFilterCount = useMemo(() => {
    let count = 0;
    if (appliedKeyword) count++;
    if (appliedDateRange?.from) count++;
    if (appliedProvince) count++;
    if (appliedCity) count++;
    if (appliedIndustry || appliedIndustryCategory) count++;
    if (appliedFilters.news_type_id) count++;
    if (
      appliedFilters.file_flag !== undefined &&
      appliedFilters.file_flag !== ''
    )
      count++;
    if (appliedFilters.project_money_min) count++;
    if (appliedFilters.part_a_name) count++;
    if (appliedFilters.part_b_name) count++;
    if (appliedFilters.agent_name) count++;
    if (appliedFilters.contract_end_min) count++;
    return count;
  }, [
    appliedKeyword,
    appliedDateRange,
    appliedProvince,
    appliedCity,
    appliedIndustry,
    appliedIndustryCategory,
    appliedFilters,
  ]);

  // --- Detail sub-page ---
  if (selectedProject) {
    return (
      <BidDetailView
        projectId={selectedProject.id}
        publishTime={selectedProject.publish_time}
        projectTitle={selectedProject.title}
        onBack={() => setSelectedProject(null)}
      />
    );
  }

  // ================================================================
  // STATE 1: Search Hero (no search performed yet)
  // ================================================================
  if (!hasSearched) {
    return (
      <>
        <SearchHero
          keyword={keyword}
          setKeyword={setKeyword}
          dateRange={dateRange}
          setDateRange={setDateRange}
          selectedProvince={selectedProvince}
          setSelectedProvince={setSelectedProvince}
          selectedCity={selectedCity}
          setSelectedCity={setSelectedCity}
          selectedIndustryCategory={selectedIndustryCategory}
          setSelectedIndustryCategory={setSelectedIndustryCategory}
          selectedIndustry={selectedIndustry}
          setSelectedIndustry={setSelectedIndustry}
          selectedNewsType={selectedNewsType}
          setSelectedNewsType={setSelectedNewsType}
          hasFile={hasFile}
          setHasFile={setHasFile}
          projectMoneyMin={projectMoneyMin}
          setProjectMoneyMin={setProjectMoneyMin}
          projectMoneyMax={projectMoneyMax}
          setProjectMoneyMax={setProjectMoneyMax}
          partAName={partAName}
          setPartAName={setPartAName}
          partBName={partBName}
          setPartBName={setPartBName}
          agentName={agentName}
          setAgentName={setAgentName}
          contractEndDateRange={contractEndDateRange}
          setContractEndDateRange={setContractEndDateRange}
          provinceOptions={provinceOptions}
          cityOptions={cityOptions}
          industryCategoryOptions={industryCategoryOptions}
          subIndustryOptions={subIndustryOptions}
          onSearch={handleSearch}
        />
        <BidConfigDialog
          visible={configProject !== null}
          projectId={configProject?.id ?? 0}
          projectTitle={configProject?.title}
          onClose={() => setConfigProject(null)}
        />
      </>
    );
  }

  // ================================================================
  // STATE 2: Search Results (card list + infinite scroll)
  // ================================================================
  return (
    <>
      <div className="flex-1 flex flex-col min-h-0 bg-[#F8F9FB] overflow-hidden">
        {/* Compact search bar */}
        <div className="shrink-0 px-6 py-3">
          <div className="flex items-center gap-3">
            {/* Keyword summary + return button */}
            <div className="flex items-center gap-2 flex-1 min-w-0">
              <span className="text-sm font-semibold text-[#000000] truncate">
                {appliedKeyword || '全部标讯'}
              </span>
              {activeFilterCount > 0 && (
                <span className="inline-flex items-center px-1.5 py-0.5 rounded-md bg-[#F5F5F5] text-xs font-medium text-[#525252] shrink-0">
                  {activeFilterCount}个筛选
                </span>
              )}
              <Button
                onClick={handleBackToSearch}
                className="h-8 px-3 bg-[#000000] hover:bg-[#171717] text-white text-xs font-medium rounded-lg transition-colors shrink-0"
              >
                返回
              </Button>
            </div>

            <span className="text-xs text-[#A3A3A3] shrink-0">
              共 <b className="text-[#000000]">{total}</b> 条结果
            </span>
          </div>
        </div>

        {/* Error banner */}
        {searchError && (
          <div className="shrink-0 px-6 pb-2">
            <div className="bg-[#FFF2F0] border border-[#FFCCC7] rounded-lg px-4 py-3 flex items-start gap-3">
              <span className="text-sm text-[#FF4D4F] shrink-0 mt-0.5">!</span>
              <div className="flex-1 min-w-0">
                <p className="text-sm font-medium text-[#FF4D4F]">搜索失败</p>
                <p className="text-xs text-[#8C8C8C] mt-0.5 break-all">
                  {searchError}
                </p>
              </div>
              <button
                onClick={() => setSearchError(null)}
                className="shrink-0 text-[#A3A3A3] hover:text-[#000000] transition-colors"
              >
                <X className="size-3.5" />
              </button>
            </div>
          </div>
        )}

        {/* Rate limit cooldown */}
        {rateLimitCooldown > 0 && (
          <div className="shrink-0 px-6 pb-2">
            <div className="bg-[#FFFBE6] border border-[#FFE58F] rounded-lg px-4 py-3 flex items-center gap-3">
              <span className="text-sm text-[#AD6800] shrink-0">
                请求过于频繁，请 {rateLimitCooldown}s 后重试
              </span>
            </div>
          </div>
        )}

        {/* Card list */}
        <div className="flex-1 min-h-0 px-6 pb-4 overflow-auto">
          {localLoading ? (
            <div className="space-y-3">
              {Array.from({ length: 5 }).map((_, i) => (
                <div
                  key={i}
                  className="bg-white rounded-xl border border-[#E8E8E8] p-5 animate-pulse"
                >
                  <div className="flex items-center justify-between mb-3">
                    <div className="flex items-center gap-2">
                      <div className="h-5 w-12 bg-[#F0F0F0] rounded-md" />
                      <div className="h-5 w-16 bg-[#F0F0F0] rounded-md" />
                    </div>
                    <div className="h-4 w-20 bg-[#F0F0F0] rounded" />
                  </div>
                  <div className="h-5 w-3/4 bg-[#F0F0F0] rounded mb-3" />
                  <div className="h-4 w-1/2 bg-[#F0F0F0] rounded mb-3" />
                  <div className="h-4 w-1/3 bg-[#F0F0F0] rounded mb-4" />
                  <div className="flex justify-end gap-2">
                    <div className="h-8 w-20 bg-[#F0F0F0] rounded-lg" />
                    <div className="h-8 w-14 bg-[#F0F0F0] rounded-lg" />
                  </div>
                </div>
              ))}
            </div>
          ) : projects.length > 0 ? (
            <>
              <div className="space-y-3">
                {projects.map((project) => (
                  <BidCard
                    key={project.id}
                    project={project}
                    areaNameMap={areaNameMap}
                    industryNameMap={industryNameMap}
                    onView={handleView}
                    onConfig={handleConfig}
                  />
                ))}
              </div>

              {/* Infinite scroll sentinel */}
              <div
                ref={sentinelRef}
                className="py-6 flex items-center justify-center"
              >
                {loadingMore ? (
                  <span className="text-xs text-[#A3A3A3]">加载中...</span>
                ) : projects.length >= total ? (
                  <span className="text-xs text-[#A3A3A3]">已显示全部结果</span>
                ) : (
                  <span className="text-xs text-[#A3A3A3]">
                    已加载 {projects.length}/{total}
                  </span>
                )}
              </div>
            </>
          ) : (
            <div className="flex flex-col items-center justify-center py-20">
              <div className="size-16 rounded-2xl bg-[#F5F5F5] flex items-center justify-center mb-4">
                <X className="size-7 text-[#A3A3A3]" />
              </div>
              <p className="text-sm font-medium text-[#525252] mb-1">
                未找到相关标讯
              </p>
              <p className="text-xs text-[#A3A3A3] mb-4">
                请调整筛选条件后重新搜索
              </p>
              <Button
                onClick={handleBackToSearch}
                className="h-9 px-4 bg-[#000000] hover:bg-[#171717] text-white text-sm rounded-lg"
              >
                返回
              </Button>
            </div>
          )}
        </div>
      </div>

      <BidConfigDialog
        visible={configProject !== null}
        projectId={configProject?.id ?? 0}
        projectTitle={configProject?.title}
        onClose={() => setConfigProject(null)}
      />
    </>
  );
}
