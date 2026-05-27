import { BidSelect } from '@/components/bid-select';
import { TableEmpty, TableSkeleton } from '@/components/table-skeleton';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { DatePickerWithRange } from '@/components/ui/range-picker';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from '@/components/ui/tooltip';
import { BidConfigDialog } from '@/pages/home/bid-config-dialog';
import { BidDetailView } from '@/pages/home/bid-detail-view';
import { getAuthorization } from '@/utils/authorization-util';
import { format } from 'date-fns';
import {
  ChevronLeft,
  ChevronRight,
  Eye,
  Search,
  Settings,
  X,
} from 'lucide-react';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { DateRange } from 'react-day-picker';

type BidProject = {
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
};

function parseJsonArray(val: string | null): string {
  if (!val || val === '[]') return '';
  try {
    return JSON.parse(val).join(', ');
  } catch {
    return val;
  }
}

const NEWS_TYPE_MAP: Record<number, string> = {
  1: '招标',
  2: '中标',
  3: '合同',
};

function getNewsTypeName(id: number | null): string {
  if (!id) return '-';
  return NEWS_TYPE_MAP[id] || '-';
}

const CELL_BORDER = 'border-r border-[#E0E0E0]';
const HEAD_CLASS =
  'text-[#404040] text-[13px] font-bold bg-[#E5E5E5] h-10 whitespace-nowrap ' +
  CELL_BORDER;

const INPUT_CLASS =
  'h-9 pl-9 pr-3 text-sm text-[#000000] border-0 bg-[#F5F5F5] hover:bg-[#EAEAEA] focus:bg-white focus:ring-2 focus:ring-[#000000]/10 rounded-lg transition-all';
const SELECT_CLASS =
  'h-9 text-sm text-[#000000] bg-[#F5F5F5] border-0 hover:bg-[#EAEAEA] focus:bg-white rounded-lg transition-all';

// 用 fetch 绕过 axios 拦截器，避免后端 raw 错误直接弹 notification
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
  return resp.json();
}

export function BidList({
  setListLength,
  setLoading,
}: {
  setListLength: (length: number) => void;
  setLoading?: (loading: boolean) => void;
}) {
  const [projects, setProjects] = useState<BidProject[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  const [localLoading, setLocalLoading] = useState(true);

  // 详情子页面状态
  const [selectedProject, setSelectedProject] = useState<{
    id: number;
    publish_time: string;
    title: string;
  } | null>(null);

  const [configProject, setConfigProject] = useState<{
    id: number;
    title: string;
  } | null>(null);

  // 搜索条件（表单状态）
  const [keyword, setKeyword] = useState('');
  const [dateRange, setDateRange] = useState<DateRange | undefined>();
  const [selectedProvince, setSelectedProvince] = useState<string>('');
  const [selectedCity, setSelectedCity] = useState<string>('');
  const [selectedIndustry, setSelectedIndustry] = useState<string>('');

  // 实际提交的搜索条件
  const [appliedKeyword, setAppliedKeyword] = useState('');
  const [appliedDateRange, setAppliedDateRange] = useState<
    DateRange | undefined
  >();
  const [appliedProvince, setAppliedProvince] = useState<string>('');
  const [appliedCity, setAppliedCity] = useState<string>('');
  const [appliedIndustry, setAppliedIndustry] = useState<string>('');
  const [appliedIndustryCategory, setAppliedIndustryCategory] =
    useState<string>('');

  const [provinceOptions, setProvinceOptions] = useState<
    { label: string; value: string }[]
  >([]);
  const [cityOptions, setCityOptions] = useState<
    { label: string; value: string }[]
  >([]);
  const [areaNameMap, setAreaNameMap] = useState<Record<string, string>>({});

  // 行业分类数据
  const [industryTree, setIndustryTree] = useState<any[]>([]);
  const [selectedIndustryCategory, setSelectedIndustryCategory] =
    useState<string>('');
  const [subIndustryOptions, setSubIndustryOptions] = useState<
    { label: string; value: string }[]
  >([]);

  // 行业名称映射（code -> 中文名）
  const industryNameMap = useMemo(() => {
    const map: Record<string, string> = {};
    for (const cat of industryTree) {
      for (const child of cat.children ?? []) {
        map[child.code] = child.name;
      }
    }
    return map;
  }, [industryTree]);

  // Build industry display name from codes
  const buildIndustryName = useCallback(
    (project: BidProject) => {
      if (!project.industry_codes) return '-';
      try {
        const codes: string[] = JSON.parse(project.industry_codes);
        if (!codes || codes.length === 0) return '-';
        const names = codes.map((c) => industryNameMap[c] || c).filter(Boolean);
        return names.length > 0 ? names.join(', ') : '-';
      } catch {
        return '-';
      }
    },
    [industryNameMap],
  );

  // Force table header divider line (CSS variable override)
  useEffect(() => {
    const id = 'bid-table-header-divider';
    if (document.getElementById(id)) return;
    const s = document.createElement('style');
    s.id = id;
    s.textContent = `
      .bid-table thead tr {
        border-bottom: 2px solid #D4D4D4 !important;
      }
    `;
    document.head.appendChild(s);
    return () => document.getElementById(id)?.remove();
  }, []);

  // Load industries on mount
  useEffect(() => {
    bidFetch('industries')
      .then((res: any) => {
        const data = res?.data ?? [];
        setIndustryTree(data);
      })
      .catch(() => {});
  }, []);

  // Build category options for the first dropdown
  const industryCategoryOptions = useMemo(
    () =>
      industryTree.map((cat: any) => ({
        label: `${cat.code} - ${cat.name}`,
        value: cat.code,
      })),
    [industryTree],
  );

  // When category changes, update sub-industry options
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

  // Load provinces on mount + build area name map
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
                const cities = r?.data ?? [];
                cities.forEach((c: any) => {
                  map[c.code] = c.name;
                });
              })
              .catch(() => {}),
          ),
        ).then(() => {
          setAreaNameMap(map);
        });
      })
      .catch(() => {});
  }, []);

  // Load cities when province changes
  useEffect(() => {
    if (!selectedProvince) {
      setCityOptions([]);
      setSelectedCity('');
      return;
    }
    bidFetch('areas', { parent_code: selectedProvince })
      .then((res: any) => {
        const list = res?.data ?? [];
        setCityOptions(
          list.map((a: any) => ({ label: a.name, value: a.code })),
        );
      })
      .catch(() => {
        setCityOptions([]);
      });
    setSelectedCity('');
  }, [selectedProvince]);

  // 用 ref 保存外部回调，避免每次渲染获取新引用导致无限请求
  const setListLengthRef = useRef(setListLength);
  setListLengthRef.current = setListLength;
  const setLoadingRef = useRef(setLoading);
  setLoadingRef.current = setLoading;

  const doFetch = useCallback((params: Record<string, any>) => {
    setLocalLoading(true);
    setLoadingRef.current?.(true);
    bidFetch('projects', params)
      .then((res: any) => {
        const data = res?.data;
        const list = data?.projects ?? [];
        setProjects(list);
        setTotal(data?.total ?? 0);
        setListLengthRef.current(list.length);
      })
      .catch(() => {
        setProjects([]);
        setTotal(0);
        setListLengthRef.current(0);
      })
      .finally(() => {
        setLocalLoading(false);
        setLoadingRef.current?.(false);
      });
  }, []);

  const buildParams = useCallback(
    (p: number, ps: number) => {
      const params: Record<string, any> = { page: p, items_per_page: ps };
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
      return params;
    },
    [
      appliedKeyword,
      appliedDateRange,
      appliedProvince,
      appliedCity,
      appliedIndustry,
      appliedIndustryCategory,
    ],
  );

  // 分页或筛选条件变化时自动请求
  const skipEffectRef = useRef(false);

  useEffect(() => {
    if (skipEffectRef.current) {
      skipEffectRef.current = false;
      return;
    }
    doFetch(buildParams(page, pageSize));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [
    page,
    pageSize,
    appliedKeyword,
    appliedDateRange,
    appliedProvince,
    appliedCity,
    appliedIndustry,
    appliedIndustryCategory,
  ]);

  const searchTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const handleSearch = () => {
    if (searchTimer.current) return;

    const industryCode = selectedIndustry || selectedIndustryCategory;

    // 更新 applied 状态（由 useEffect 自动触发请求）
    setAppliedKeyword(keyword);
    setAppliedDateRange(dateRange);
    setAppliedProvince(selectedProvince);
    setAppliedCity(selectedCity);
    setAppliedIndustry(selectedIndustry);
    setAppliedIndustryCategory(selectedIndustryCategory);

    // 重置页码（若当前不是第1页，翻页会触发请求；若已是第1页则手动触发）
    if (page !== 1) {
      setPage(1);
    } else {
      // 直接用表单值构建参数（此时 applied* 尚未更新）
      skipEffectRef.current = true;
      const params: Record<string, any> = { page: 1, items_per_page: pageSize };
      if (keyword) params.keyword = keyword;
      if (dateRange?.from)
        params.start_date = format(dateRange.from, 'yyyy-MM-dd');
      if (dateRange?.to) params.end_date = format(dateRange.to, 'yyyy-MM-dd');
      if (selectedProvince) params.provice_code = selectedProvince;
      if (selectedCity) params.city_code = selectedCity;
      if (industryCode) params.industry_code = industryCode;
      doFetch(params);
    }

    searchTimer.current = setTimeout(() => {
      searchTimer.current = null;
    }, 500);
  };

  const handleView = (project: BidProject) => {
    setSelectedProject({
      id: project.id,
      publish_time: project.publish_time || '',
      title: project.title,
    });
  };

  const handleConfig = (project: BidProject) => {
    setConfigProject({ id: project.id, title: project.title });
  };

  const buildAreaName = (project: BidProject) => {
    const parts: string[] = [];
    if (project.provice_code && areaNameMap[project.provice_code]) {
      parts.push(areaNameMap[project.provice_code]);
    }
    if (project.city_code && areaNameMap[project.city_code]) {
      parts.push(areaNameMap[project.city_code]);
    }
    if (project.county_code && areaNameMap[project.county_code]) {
      parts.push(areaNameMap[project.county_code]);
    }
    return parts.length > 0 ? parts.join('/') : '-';
  };

  const columnsLength = 9;

  const totalPages = Math.max(1, Math.ceil(total / pageSize));

  const buildPageNumbers = () => {
    const maxDisplay = 5;
    if (totalPages <= maxDisplay) {
      return Array.from({ length: totalPages }, (_, i) => i + 1);
    }
    const pages: number[] = [1];
    const left = Math.max(2, page - 2);
    const right = Math.min(totalPages - 1, page + 2);
    if (left > 2) pages.push(-1);
    for (let i = left; i <= right; i++) pages.push(i);
    if (right < totalPages - 1) pages.push(-1);
    if (totalPages > 1) pages.push(totalPages);
    return pages;
  };

  // 详情子页面
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

  return (
    <>
      <div className="flex-1 flex flex-col min-h-0 bg-[#F8F9FB]">
        {/* Search bar — card style */}
        <div className="cs-page-enter shrink-0 px-6 py-4">
          <div className="bg-white rounded-xl border border-[#E8E8E8] shadow-[0_1px_3px_rgba(0,0,0,0.03)] p-4">
            <div className="flex items-center gap-3 flex-wrap">
              {/* Keyword */}
              <div className="relative flex-1 min-w-[200px] max-w-[320px]">
                <Search className="absolute left-3 top-1/2 -translate-y-1/2 size-4 text-[#A3A3A3]" />
                <Input
                  value={keyword}
                  onChange={(e) => setKeyword(e.target.value)}
                  onKeyDown={(e) => e.key === 'Enter' && handleSearch()}
                  placeholder="搜索标题、甲方、乙方、内容..."
                  className={INPUT_CLASS}
                />
                {keyword && (
                  <button
                    onClick={() => setKeyword('')}
                    className="absolute right-2 top-1/2 -translate-y-1/2 text-[#A3A3A3] hover:text-[#000000] transition-colors"
                  >
                    <X className="size-3.5" />
                  </button>
                )}
              </div>

              {/* Separator */}
              <div className="w-px h-6 bg-[#E8E8E8] hidden md:block" />

              {/* Date */}
              <DatePickerWithRange
                selected={dateRange}
                onSelect={(range: any) => setDateRange(range)}
              />

              {/* Separator */}
              <div className="w-px h-6 bg-[#E8E8E8] hidden md:block" />

              {/* Province */}
              <BidSelect
                value={selectedProvince}
                onChange={(val) => {
                  setSelectedProvince(val);
                  setSelectedCity('');
                }}
                options={provinceOptions}
                placeholder="全部省份"
                allowClear
                className={`${SELECT_CLASS} w-32`}
              />

              {/* City */}
              <BidSelect
                value={selectedCity}
                onChange={(val) => setSelectedCity(val)}
                options={cityOptions}
                placeholder="全部城市"
                allowClear
                disabled={!selectedProvince}
                className={`${SELECT_CLASS} w-32`}
              />

              {/* Industry category */}
              <BidSelect
                value={selectedIndustryCategory}
                onChange={(val) => {
                  setSelectedIndustryCategory(val);
                  setSelectedIndustry('');
                }}
                options={industryCategoryOptions}
                placeholder="全部门类"
                allowClear
                className={`${SELECT_CLASS} w-36`}
              />

              {selectedIndustryCategory && (
                <BidSelect
                  value={selectedIndustry}
                  onChange={(val) => setSelectedIndustry(val)}
                  options={subIndustryOptions}
                  placeholder="全部中类"
                  allowClear
                  className={`${SELECT_CLASS} w-36`}
                />
              )}

              {/* Search button */}
              <Button
                onClick={handleSearch}
                className="h-9 px-5 bg-[#000000] hover:bg-[#171717] text-white text-sm font-medium rounded-lg transition-all hover:shadow-[0_4px_12px_rgba(0,0,0,0.15)]"
              >
                <Search className="size-3.5 mr-1.5" />
                搜索
              </Button>
            </div>
          </div>
        </div>

        {/* Table */}
        <div className="flex-1 min-h-0 px-6 pb-4 overflow-auto">
          <div className="bg-white rounded-xl border border-[#E8E8E8] shadow-[0_1px_3px_rgba(0,0,0,0.03)]">
            <Table
              rootClassName="overflow-visible !rounded-none"
              className="bid-table [border-collapse:separate] [border-spacing:0]"
            >
              <TableHeader className="[&_tr]:!border-b-2 [&_tr]:!border-[#D4D4D4]">
                <TableRow className="hover:bg-transparent sticky top-0 z-10">
                  <TableHead
                    className={`min-w-[200px] rounded-tl-xl ${HEAD_CLASS}`}
                  >
                    项目名称
                  </TableHead>
                  <TableHead className={`min-w-[56px] ${HEAD_CLASS}`}>
                    类别
                  </TableHead>
                  <TableHead className={`min-w-[80px] ${HEAD_CLASS}`}>
                    金额
                  </TableHead>
                  <TableHead className={`min-w-[80px] ${HEAD_CLASS}`}>
                    地区
                  </TableHead>
                  <TableHead className={`min-w-[110px] ${HEAD_CLASS}`}>
                    行业
                  </TableHead>
                  <TableHead className={`min-w-[110px] ${HEAD_CLASS}`}>
                    甲方
                  </TableHead>
                  <TableHead className={`min-w-[110px] ${HEAD_CLASS}`}>
                    乙方
                  </TableHead>
                  <TableHead className={`min-w-[90px] ${HEAD_CLASS}`}>
                    合同到期
                  </TableHead>
                  <TableHead
                    className={`min-w-[100px] text-center ${HEAD_CLASS} rounded-tr-xl`}
                  >
                    操作
                  </TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {localLoading ? (
                  <TableSkeleton columnsLength={columnsLength} />
                ) : projects.length > 0 ? (
                  projects.map((project, idx) => (
                    <TableRow
                      key={project.id}
                      className={`cs-row-enter cs-row-d${Math.min(idx, 9)} group/row transition-all border-b border-[#F0F0F0] hover:bg-[#FAFAFA] hover:shadow-[0_2px_8px_rgba(0,0,0,0.04)] relative`}
                    >
                      <TableCell className={CELL_BORDER}>
                        <Tooltip>
                          <TooltipTrigger asChild>
                            <div className="font-medium text-xs text-[#000000] leading-snug truncate max-w-[320px]">
                              {project.title}
                            </div>
                          </TooltipTrigger>
                          <TooltipContent side="bottom" className="max-w-md">
                            {project.title}
                          </TooltipContent>
                        </Tooltip>
                      </TableCell>
                      <TableCell className={CELL_BORDER}>
                        <span
                          className={`inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium ${
                            project.news_type_id === 1
                              ? 'bg-[#EFF6FF] text-[#2563EB]'
                              : project.news_type_id === 2
                                ? 'bg-[#F0FDF4] text-[#16A34A]'
                                : 'bg-[#F5F5F5] text-[#525252]'
                          }`}
                        >
                          {getNewsTypeName(project.news_type_id)}
                        </span>
                      </TableCell>
                      <TableCell className={CELL_BORDER}>
                        {project.project_money ? (
                          <span className="text-[#16A34A] font-medium text-xs whitespace-nowrap">
                            {project.project_money}
                          </span>
                        ) : (
                          <span className="text-[#D4D4D4]">-</span>
                        )}
                      </TableCell>
                      <TableCell className={CELL_BORDER}>
                        <Tooltip>
                          <TooltipTrigger asChild>
                            <div className="text-[#404040] text-xs truncate max-w-[120px]">
                              {buildAreaName(project)}
                            </div>
                          </TooltipTrigger>
                          <TooltipContent side="bottom">
                            {buildAreaName(project)}
                          </TooltipContent>
                        </Tooltip>
                      </TableCell>
                      <TableCell className={CELL_BORDER}>
                        <Tooltip>
                          <TooltipTrigger asChild>
                            <div className="text-[#404040] text-xs truncate max-w-[180px]">
                              {buildIndustryName(project)}
                            </div>
                          </TooltipTrigger>
                          <TooltipContent side="bottom" className="max-w-sm">
                            {buildIndustryName(project)}
                          </TooltipContent>
                        </Tooltip>
                      </TableCell>
                      <TableCell className={CELL_BORDER}>
                        <Tooltip>
                          <TooltipTrigger asChild>
                            <div className="text-[#404040] text-xs truncate max-w-[180px]">
                              {parseJsonArray(project.part_a_names) || '-'}
                            </div>
                          </TooltipTrigger>
                          <TooltipContent side="bottom">
                            {parseJsonArray(project.part_a_names) || '-'}
                          </TooltipContent>
                        </Tooltip>
                      </TableCell>
                      <TableCell className={CELL_BORDER}>
                        <Tooltip>
                          <TooltipTrigger asChild>
                            <div className="text-[#404040] text-xs truncate max-w-[180px]">
                              {parseJsonArray(project.part_b_names) || '-'}
                            </div>
                          </TooltipTrigger>
                          <TooltipContent side="bottom">
                            {parseJsonArray(project.part_b_names) || '-'}
                          </TooltipContent>
                        </Tooltip>
                      </TableCell>
                      <TableCell className={CELL_BORDER}>
                        <span className="text-[#404040] text-xs whitespace-nowrap">
                          {project.contract_end_date
                            ? project.contract_end_date.substring(0, 10)
                            : '-'}
                        </span>
                      </TableCell>
                      <TableCell>
                        <div className="flex items-center justify-center gap-1">
                          <button
                            className="h-7 px-2.5 text-xs font-medium text-[#000000] hover:bg-[#EAEAEA] rounded-md transition-colors inline-flex items-center gap-1"
                            onClick={(e: React.MouseEvent) => {
                              e.stopPropagation();
                              handleView(project);
                            }}
                          >
                            <Eye className="size-3" />
                            查看
                          </button>
                          <button
                            className="h-7 px-2.5 text-xs font-medium text-[#525252] hover:text-[#000000] hover:bg-[#EAEAEA] rounded-md transition-colors inline-flex items-center gap-1"
                            onClick={(e: React.MouseEvent) => {
                              e.stopPropagation();
                              handleConfig(project);
                            }}
                          >
                            <Settings className="size-3" />
                            配置
                          </button>
                        </div>
                      </TableCell>
                    </TableRow>
                  ))
                ) : (
                  <TableEmpty columnsLength={columnsLength} />
                )}
              </TableBody>
            </Table>
          </div>
        </div>

        {/* Pagination footer */}
        <div className="shrink-0 px-6 py-3 flex items-center justify-between text-sm text-[#525252]">
          <span className="text-xs">
            共 <b className="text-[#000000]">{total}</b> 条记录
          </span>

          <div className="flex items-center gap-3">
            {/* Page size selector */}
            <BidSelect
              value={pageSize.toString()}
              onChange={(val) => {
                if (val) {
                  setPageSize(Number(val));
                  setPage(1);
                }
              }}
              options={[
                { label: '10条/页', value: '10' },
                { label: '20条/页', value: '20' },
                { label: '50条/页', value: '50' },
                { label: '100条/页', value: '100' },
              ]}
              className="h-8 text-xs text-[#525252] bg-[#F5F5F5] border-0 rounded-lg"
            />

            {/* Page navigation */}
            <div className="flex items-center gap-0.5">
              <button
                onClick={() => page > 1 && setPage(page - 1)}
                disabled={page <= 1}
                className="size-7 inline-flex items-center justify-center rounded-md text-[#A3A3A3] hover:bg-[#F5F5F5] hover:text-[#000000] disabled:opacity-30 disabled:cursor-not-allowed transition-all"
              >
                <ChevronLeft className="size-3.5" />
              </button>
              {buildPageNumbers().map((p, idx) =>
                p === -1 ? (
                  <span key={`e${idx}`} className="px-1 text-xs text-[#A3A3A3]">
                    ...
                  </span>
                ) : (
                  <button
                    key={p}
                    onClick={() => setPage(p)}
                    className={`size-7 rounded-md text-xs font-medium transition-all ${
                      page === p
                        ? 'bg-[#000000] text-white shadow-[0_2px_6px_rgba(0,0,0,0.15)]'
                        : 'text-[#525252] hover:bg-[#F5F5F5] hover:text-[#000000]'
                    }`}
                  >
                    {p}
                  </button>
                ),
              )}
              <button
                onClick={() => page < totalPages && setPage(page + 1)}
                disabled={page >= totalPages}
                className="size-7 inline-flex items-center justify-center rounded-md text-[#A3A3A3] hover:bg-[#F5F5F5] hover:text-[#000000] disabled:opacity-30 disabled:cursor-not-allowed transition-all"
              >
                <ChevronRight className="size-3.5" />
              </button>
            </div>
          </div>
        </div>
      </div>

      {/* 配置弹框 */}
      <BidConfigDialog
        visible={configProject !== null}
        projectId={configProject?.id ?? 0}
        projectTitle={configProject?.title}
        onClose={() => setConfigProject(null)}
      />
    </>
  );
}
