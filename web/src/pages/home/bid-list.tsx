import { TableEmpty, TableSkeleton } from '@/components/table-skeleton';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { DatePickerWithRange } from '@/components/ui/range-picker';
import { RAGFlowSelect } from '@/components/ui/select';
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
import request from '@/utils/next-request';
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

const CELL_BORDER = 'border-r border-[rgba(124,92,252,0.08)]';
const HEAD_CLASS =
  'text-[#4a4a6a] text-xs font-semibold tracking-wide bg-[#f0edf8] ' +
  CELL_BORDER;

const LABEL_CLASS = 'text-sm text-[#3d3d5c] font-medium whitespace-nowrap';
const INPUT_CLASS =
  'w-48 h-9 text-sm text-[#2d2d4a] border border-[rgba(124,92,252,0.25)] bg-[#f5f3fa] hover:border-[#7c5cfc] focus:bg-white focus:border-[#7c5cfc]';
const SELECT_CLASS =
  'w-36 h-9 !text-[#2d2d4a] !bg-[#f5f3fa] border border-[rgba(124,92,252,0.25)] hover:!bg-[#ede9fe] hover:!text-[#2d2d4a] focus:!bg-white focus:!border-[#7c5cfc]';

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

  // Load industries on mount
  useEffect(() => {
    request
      .get('/api/v1/bid/industries')
      .then((res: any) => {
        const data = res?.data?.data ?? [];
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
    request
      .get('/api/v1/bid/areas', { params: { parent_code: '0', level: 1 } })
      .then((res: any) => {
        const list = res?.data?.data ?? [];
        setProvinceOptions(
          list.map((a: any) => ({ label: a.name, value: a.code })),
        );
        const map: Record<string, string> = {};
        list.forEach((a: any) => {
          map[a.code] = a.name;
        });
        Promise.all(
          list.map((p: any) =>
            request
              .get('/api/v1/bid/areas', { params: { parent_code: p.code } })
              .then((r: any) => {
                const cities = r?.data?.data ?? [];
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
    request
      .get('/api/v1/bid/areas', { params: { parent_code: selectedProvince } })
      .then((res: any) => {
        const list = res?.data?.data ?? [];
        setCityOptions(
          list.map((a: any) => ({ label: a.name, value: a.code })),
        );
      })
      .catch(() => {
        setCityOptions([]);
      });
    setSelectedCity('');
  }, [selectedProvince]);

  const doFetch = useCallback(
    (params: Record<string, any>) => {
      setLocalLoading(true);
      setLoading?.(true);
      request
        .get('/api/v1/bid/projects', { params })
        .then((res: any) => {
          const data = res?.data?.data;
          const list = data?.projects ?? [];
          setProjects(list);
          setTotal(data?.total ?? 0);
          setListLength(list.length);
        })
        .catch(() => {
          setProjects([]);
          setTotal(0);
          setListLength(0);
        })
        .finally(() => {
          setLocalLoading(false);
          setLoading?.(false);
        });
    },
    [setListLength, setLoading],
  );

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
      return params;
    },
    [
      appliedKeyword,
      appliedDateRange,
      appliedProvince,
      appliedCity,
      appliedIndustry,
    ],
  );

  // 分页变化时自动请求
  useEffect(() => {
    doFetch(buildParams(page, pageSize));
  }, [page, pageSize, doFetch, buildParams]);

  const searchTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const handleSearch = () => {
    if (searchTimer.current) return;

    // 更新 applied 状态
    setPage(1);
    setAppliedKeyword(keyword);
    setAppliedDateRange(dateRange);
    setAppliedProvince(selectedProvince);
    setAppliedCity(selectedCity);
    setAppliedIndustry(selectedIndustry);

    // 用当前条件直接发请求（不依赖 effect）
    const params: Record<string, any> = { page: 1, items_per_page: pageSize };
    if (keyword) params.keyword = keyword;
    if (dateRange?.from)
      params.start_date = format(dateRange.from, 'yyyy-MM-dd');
    if (dateRange?.to) params.end_date = format(dateRange.to, 'yyyy-MM-dd');
    if (selectedProvince) params.provice_code = selectedProvince;
    if (selectedCity) params.city_code = selectedCity;
    if (selectedIndustry) params.industry_code = selectedIndustry;
    doFetch(params);

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

  const columnsLength = 10;

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
      <div className="flex-1 flex flex-col min-h-0 bg-[#f8f6f3]">
        {/* Search bar */}
        <div className="shrink-0 px-6 py-3 bg-white border-b border-[rgba(124,92,252,0.06)] flex items-center gap-6 flex-wrap">
          <div className="flex items-center gap-2">
            <span className={LABEL_CLASS}>关键字</span>
            <div className="relative">
              <Input
                value={keyword}
                onChange={(e) => setKeyword(e.target.value)}
                onKeyDown={(e) => e.key === 'Enter' && handleSearch()}
                placeholder="标题/甲方/乙方/内容"
                className={INPUT_CLASS}
              />
              {keyword && (
                <button
                  onClick={() => setKeyword('')}
                  className="absolute right-2 top-1/2 -translate-y-1/2 text-[#9b9bb5] hover:text-[#7c5cfc] transition-colors"
                >
                  <X className="size-3.5" />
                </button>
              )}
            </div>
          </div>
          <div className="flex items-center gap-2">
            <span className={LABEL_CLASS}>发布时间</span>
            <DatePickerWithRange
              selected={dateRange}
              onSelect={(range: any) => setDateRange(range)}
            />
          </div>
          <div className="flex items-center gap-2">
            <span className={LABEL_CLASS}>省份</span>
            <RAGFlowSelect
              value={selectedProvince}
              onChange={(val) => {
                setSelectedProvince(val ?? '');
                setSelectedCity('');
              }}
              options={provinceOptions}
              placeholder="全部省份"
              allowClear
              triggerClassName={SELECT_CLASS}
            />
          </div>
          <div className="flex items-center gap-2">
            <span className={LABEL_CLASS}>城市</span>
            <RAGFlowSelect
              value={selectedCity}
              onChange={(val) => setSelectedCity(val ?? '')}
              options={cityOptions}
              placeholder="全部城市"
              allowClear
              disabled={!selectedProvince}
              triggerClassName={SELECT_CLASS}
            />
          </div>
          <div className="flex items-center gap-2">
            <span className={LABEL_CLASS}>行业门类</span>
            <RAGFlowSelect
              value={selectedIndustryCategory}
              onChange={(val) => {
                setSelectedIndustryCategory(val ?? '');
                setSelectedIndustry('');
              }}
              options={industryCategoryOptions}
              placeholder="全部门类"
              allowClear
              triggerClassName={SELECT_CLASS}
            />
          </div>
          {selectedIndustryCategory && (
            <div className="flex items-center gap-2">
              <span className={LABEL_CLASS}>行业中类</span>
              <RAGFlowSelect
                value={selectedIndustry}
                onChange={(val) => setSelectedIndustry(val ?? '')}
                options={subIndustryOptions}
                placeholder="全部中类"
                allowClear
                triggerClassName={SELECT_CLASS}
              />
            </div>
          )}
          <Button
            onClick={handleSearch}
            className="h-9 px-5 bg-[#7c5cfc] hover:bg-[#6a4ce0] text-white"
          >
            <Search className="size-4 mr-1" />
            搜索
          </Button>
        </div>

        {/* Table */}
        <div className="flex-1 min-h-0 px-6 overflow-auto">
          <Table
            rootClassName="overflow-visible !rounded-none"
            className="[border-collapse:separate] [border-spacing:0]"
          >
            <TableHeader>
              <TableRow className="hover:bg-transparent sticky top-0 z-10 bg-[#f0edf8]">
                <TableHead className={`min-w-[120px] ${HEAD_CLASS}`}>
                  项目名称
                </TableHead>
                <TableHead className={`min-w-[48px] ${HEAD_CLASS}`}>
                  类别
                </TableHead>
                <TableHead className={`min-w-[70px] ${HEAD_CLASS}`}>
                  金额
                </TableHead>
                <TableHead className={`min-w-[90px] ${HEAD_CLASS}`}>
                  发布时间
                </TableHead>
                <TableHead className={`min-w-[70px] ${HEAD_CLASS}`}>
                  地区
                </TableHead>
                <TableHead className={`min-w-[100px] ${HEAD_CLASS}`}>
                  行业
                </TableHead>
                <TableHead className={`min-w-[100px] ${HEAD_CLASS}`}>
                  甲方
                </TableHead>
                <TableHead className={`min-w-[100px] ${HEAD_CLASS}`}>
                  乙方
                </TableHead>
                <TableHead className={`min-w-[90px] ${HEAD_CLASS}`}>
                  合同到期
                </TableHead>
                <TableHead
                  className={`min-w-[90px] text-[#4a4a6a] text-xs font-semibold tracking-wide text-center bg-[#f0edf8] ${CELL_BORDER}`}
                >
                  操作
                </TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {localLoading ? (
                <TableSkeleton columnsLength={columnsLength} />
              ) : projects.length > 0 ? (
                projects.map((project) => (
                  <TableRow
                    key={project.id}
                    className="group/row bg-white hover:bg-[#ede9fe] transition-colors border-b border-[rgba(124,92,252,0.04)]"
                  >
                    <TableCell className={`${CELL_BORDER}`}>
                      <Tooltip>
                        <TooltipTrigger asChild>
                          <div className="font-medium text-sm text-[#1c1c2e] leading-snug truncate max-w-[280px]">
                            {project.title}
                          </div>
                        </TooltipTrigger>
                        <TooltipContent side="bottom" className="max-w-md">
                          {project.title}
                        </TooltipContent>
                      </Tooltip>
                    </TableCell>
                    <TableCell className={CELL_BORDER}>
                      <span className="text-sm text-[#3d3d5c] whitespace-nowrap">
                        {getNewsTypeName(project.news_type_id)}
                      </span>
                    </TableCell>
                    <TableCell className={CELL_BORDER}>
                      {project.project_money ? (
                        <span className="text-green-600 font-medium text-sm whitespace-nowrap">
                          {project.project_money}
                        </span>
                      ) : (
                        <span className="text-[#c4c4d8]">-</span>
                      )}
                    </TableCell>
                    <TableCell
                      className={`text-[#3d3d5c] text-sm whitespace-nowrap ${CELL_BORDER}`}
                    >
                      {project.publish_time
                        ? project.publish_time.substring(0, 10)
                        : '-'}
                    </TableCell>
                    <TableCell
                      className={`text-[#3d3d5c] text-sm ${CELL_BORDER}`}
                    >
                      <Tooltip>
                        <TooltipTrigger asChild>
                          <div className="truncate max-w-[120px]">
                            {buildAreaName(project)}
                          </div>
                        </TooltipTrigger>
                        <TooltipContent side="bottom">
                          {buildAreaName(project)}
                        </TooltipContent>
                      </Tooltip>
                    </TableCell>
                    <TableCell
                      className={`text-[#3d3d5c] text-sm ${CELL_BORDER}`}
                    >
                      <Tooltip>
                        <TooltipTrigger asChild>
                          <div className="truncate max-w-[180px]">
                            {buildIndustryName(project)}
                          </div>
                        </TooltipTrigger>
                        <TooltipContent side="bottom" className="max-w-sm">
                          {buildIndustryName(project)}
                        </TooltipContent>
                      </Tooltip>
                    </TableCell>
                    <TableCell
                      className={`text-[#3d3d5c] text-sm ${CELL_BORDER}`}
                    >
                      <Tooltip>
                        <TooltipTrigger asChild>
                          <div className="truncate max-w-[180px]">
                            {parseJsonArray(project.part_a_names) || '-'}
                          </div>
                        </TooltipTrigger>
                        <TooltipContent side="bottom">
                          {parseJsonArray(project.part_a_names) || '-'}
                        </TooltipContent>
                      </Tooltip>
                    </TableCell>
                    <TableCell
                      className={`text-[#3d3d5c] text-sm ${CELL_BORDER}`}
                    >
                      <Tooltip>
                        <TooltipTrigger asChild>
                          <div className="truncate max-w-[180px]">
                            {parseJsonArray(project.part_b_names) || '-'}
                          </div>
                        </TooltipTrigger>
                        <TooltipContent side="bottom">
                          {parseJsonArray(project.part_b_names) || '-'}
                        </TooltipContent>
                      </Tooltip>
                    </TableCell>
                    <TableCell
                      className={`text-[#3d3d5c] text-sm whitespace-nowrap ${CELL_BORDER}`}
                    >
                      {project.contract_end_date
                        ? project.contract_end_date.substring(0, 10)
                        : '-'}
                    </TableCell>
                    <TableCell>
                      <div className="flex items-center justify-center gap-1">
                        <Button
                          variant="ghost"
                          size="sm"
                          className="h-7 px-2 text-[#7c5cfc] hover:text-[#7c5cfc] hover:bg-[#f4f1fb]"
                          onClick={(e: React.MouseEvent) => {
                            e.stopPropagation();
                            handleView(project);
                          }}
                        >
                          <Eye className="size-3.5 mr-1" />
                          查看
                        </Button>
                        <Button
                          variant="ghost"
                          size="sm"
                          className="h-7 px-2 text-[#5a5a7a] hover:text-[#7c5cfc] hover:bg-[#f4f1fb]"
                          onClick={(e: React.MouseEvent) => {
                            e.stopPropagation();
                            handleConfig(project);
                          }}
                        >
                          <Settings className="size-3.5 mr-1" />
                          配置
                        </Button>
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

        {/* Pagination footer — 中文 */}
        <div className="shrink-0 px-6 py-3 bg-white border-t border-[rgba(124,92,252,0.06)] flex items-center justify-end gap-4 text-sm text-[#3d3d5c]">
          <span>共 {total} 条</span>

          {/* Page size selector */}
          <RAGFlowSelect
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
            triggerClassName="w-28 h-8 text-sm text-[#3d3d5c] !bg-white border border-[rgba(124,92,252,0.15)]"
          />

          {/* Page navigation */}
          <div className="flex items-center gap-1">
            <button
              onClick={() => page > 1 && setPage(page - 1)}
              disabled={page <= 1}
              className="size-8 inline-flex items-center justify-center rounded text-[#5a5a7a] hover:bg-[#f4f1fb] hover:text-[#7c5cfc] disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
            >
              <ChevronLeft className="size-4" />
            </button>
            {buildPageNumbers().map((p, idx) =>
              p === -1 ? (
                <span key={`e${idx}`} className="px-1 text-[#9b9bb5]">
                  ...
                </span>
              ) : (
                <button
                  key={p}
                  onClick={() => setPage(p)}
                  className={`size-8 rounded text-sm transition-colors ${
                    page === p
                      ? 'bg-[#7c5cfc] text-white font-medium'
                      : 'text-[#5a5a7a] hover:bg-[#f4f1fb] hover:text-[#7c5cfc]'
                  }`}
                >
                  {p}
                </button>
              ),
            )}
            <button
              onClick={() => page < totalPages && setPage(page + 1)}
              disabled={page >= totalPages}
              className="size-8 inline-flex items-center justify-center rounded text-[#5a5a7a] hover:bg-[#f4f1fb] hover:text-[#7c5cfc] disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
            >
              <ChevronRight className="size-4" />
            </button>
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
