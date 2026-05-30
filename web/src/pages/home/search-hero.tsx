import { BidSelect } from '@/components/bid-select';
import { SelectWithSearch } from '@/components/originui/select-with-search';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { DatePickerWithRange } from '@/components/ui/range-picker';
import { ChevronDown, ChevronUp, Search, X } from 'lucide-react';
import { useState } from 'react';
import { DateRange } from 'react-day-picker';

const INPUT_CLASS =
  'h-9 px-3 text-xs text-[#000000] border border-[#D4D4D4] bg-white hover:border-[#A3A3A3] focus:border-[#000000] focus:ring-2 focus:ring-[#000000]/10 rounded-lg transition-all';
const SELECT_CLASS =
  'h-9 text-xs text-[#000000] bg-white border border-[#D4D4D4] hover:border-[#A3A3A3] focus:border-[#000000] rounded-lg transition-all';

interface SearchHeroProps {
  // Core states
  keyword: string;
  setKeyword: (v: string) => void;
  dateRange: DateRange | undefined;
  setDateRange: (r: DateRange | undefined) => void;
  selectedProvince: string;
  setSelectedProvince: (v: string) => void;
  selectedCity: string;
  setSelectedCity: (v: string) => void;
  selectedIndustryCategory: string;
  setSelectedIndustryCategory: (v: string) => void;
  selectedIndustry: string;
  setSelectedIndustry: (v: string) => void;

  // Advanced states
  selectedNewsType: string;
  setSelectedNewsType: (v: string) => void;
  hasFile: string;
  setHasFile: (v: string) => void;
  projectMoneyMin: string;
  setProjectMoneyMin: (v: string) => void;
  projectMoneyMax: string;
  setProjectMoneyMax: (v: string) => void;
  partAName: string;
  setPartAName: (v: string) => void;
  partBName: string;
  setPartBName: (v: string) => void;
  agentName: string;
  setAgentName: (v: string) => void;
  contractEndDateRange: DateRange | undefined;
  setContractEndDateRange: (r: DateRange | undefined) => void;

  // Options
  provinceOptions: { label: string; value: string }[];
  cityOptions: { label: string; value: string }[];
  industryCategoryOptions: { label: string; value: string }[];
  subIndustryOptions: { label: string; value: string }[];

  onSearch: () => void;
}

export function SearchHero({
  keyword,
  setKeyword,
  dateRange,
  setDateRange,
  selectedProvince,
  setSelectedProvince,
  selectedCity,
  setSelectedCity,
  selectedIndustryCategory,
  setSelectedIndustryCategory,
  selectedIndustry,
  setSelectedIndustry,
  selectedNewsType,
  setSelectedNewsType,
  hasFile,
  setHasFile,
  projectMoneyMin,
  setProjectMoneyMin,
  projectMoneyMax,
  setProjectMoneyMax,
  partAName,
  setPartAName,
  partBName,
  setPartBName,
  agentName,
  setAgentName,
  contractEndDateRange,
  setContractEndDateRange,
  provinceOptions,
  cityOptions,
  industryCategoryOptions,
  subIndustryOptions,
  onSearch,
}: SearchHeroProps) {
  const [showAdvanced, setShowAdvanced] = useState(false);

  return (
    <div className="flex-1 overflow-auto">
      <div className="min-h-full flex items-center justify-center px-6 py-12">
        <div className="w-full max-w-2xl">
          {/* Header */}
          <div className="text-center mb-8">
            <div className="cs-card-enter inline-flex items-center justify-center size-16 rounded-2xl bg-[#F5F5F5] mb-4">
              <Search className="size-7 text-[#404040]" />
            </div>
            <h1 className="cs-card-enter cs-card-d1 text-2xl font-bold text-[#000000] tracking-tight">
              标讯搜索
            </h1>
            <p className="cs-card-enter cs-card-d1 text-sm text-[#A3A3A3] mt-1">
              搜索全国招标、中标、合同信息
            </p>
          </div>

          {/* Search card */}
          <div className="cs-card-enter cs-card-d2 bg-white rounded-xl border border-[#E8E8E8] shadow-[0_4px_24px_rgba(0,0,0,0.04)] p-6">
            {/* 项目名称 */}
            <div className="mb-4">
              <label className="block text-sm font-medium text-[#1a1a1a] mb-1.5">
                项目名称
              </label>
              <div className="relative">
                <Input
                  value={keyword}
                  onChange={(e) => setKeyword(e.target.value)}
                  onKeyDown={(e) => e.key === 'Enter' && onSearch()}
                  placeholder="输入项目名称关键词..."
                  className={`${INPUT_CLASS} w-full`}
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
            </div>

            {/* Row: 省份 + 城市 */}
            <div className="grid grid-cols-2 gap-3 mb-4">
              <div>
                <label className="block text-sm font-medium text-[#1a1a1a] mb-1.5">
                  省份
                </label>
                <BidSelect
                  value={selectedProvince}
                  onChange={(val) => {
                    setSelectedProvince(val);
                    setSelectedCity('');
                  }}
                  options={provinceOptions}
                  placeholder="全部省份"
                  allowClear
                  className={`${SELECT_CLASS} w-full`}
                />
              </div>
              <div>
                <label className="block text-sm font-medium text-[#1a1a1a] mb-1.5">
                  城市
                </label>
                <BidSelect
                  value={selectedCity}
                  onChange={(val) => setSelectedCity(val)}
                  options={cityOptions}
                  placeholder="全部城市"
                  allowClear
                  disabled={!selectedProvince}
                  className={`${SELECT_CLASS} w-full`}
                />
              </div>
            </div>

            {/* Row: 行业门类 + 行业中类 */}
            <div className="grid grid-cols-2 gap-3 mb-4">
              <div>
                <label className="block text-sm font-medium text-[#1a1a1a] mb-1.5">
                  行业门类
                </label>
                <BidSelect
                  value={selectedIndustryCategory}
                  onChange={(val) => {
                    setSelectedIndustryCategory(val);
                    setSelectedIndustry('');
                  }}
                  options={industryCategoryOptions}
                  placeholder="全部门类"
                  allowClear
                  className={`${SELECT_CLASS} w-full`}
                />
              </div>
              <div>
                <label className="block text-sm font-medium text-[#1a1a1a] mb-1.5">
                  行业中类
                </label>
                {selectedIndustryCategory ? (
                  <SelectWithSearch
                    value={selectedIndustry}
                    onChange={(val) => setSelectedIndustry(val)}
                    options={subIndustryOptions}
                    placeholder="选择中类"
                    allowClear
                    triggerClassName={`${SELECT_CLASS} w-full`}
                  />
                ) : (
                  <div
                    className={`${SELECT_CLASS} w-full flex items-center px-3 text-[#A3A3A3] text-xs cursor-not-allowed`}
                  >
                    请先选门类
                  </div>
                )}
              </div>
            </div>

            {/* 发布时间（必填） */}
            <div className="mb-4">
              <label className="block text-sm font-medium text-[#1a1a1a] mb-1.5">
                发布时间 <span className="text-red-500">*</span>
              </label>
              <DatePickerWithRange
                selected={dateRange}
                onSelect={(range: any) => setDateRange(range)}
                className="w-full"
              />
              {!dateRange?.from && (
                <p className="text-xs text-red-500 mt-1">请选择发布时间范围</p>
              )}
            </div>

            {/* 更多条件 toggle */}
            <button
              type="button"
              onClick={() => setShowAdvanced(!showAdvanced)}
              className="flex items-center gap-1 text-sm font-medium text-[#1a1a1a] hover:text-[#000000] transition-colors mb-4"
            >
              {showAdvanced ? (
                <ChevronUp className="size-3.5" />
              ) : (
                <ChevronDown className="size-3.5" />
              )}
              更多条件
            </button>

            {/* Advanced filters */}
            {showAdvanced && (
              <div className="space-y-4 pt-4 border-t border-[#F0F0F0]">
                {/* 类别 + 附件 */}
                <div className="grid grid-cols-2 gap-3">
                  <div>
                    <label className="block text-sm font-medium text-[#1a1a1a] mb-1.5">
                      类别
                    </label>
                    <BidSelect
                      value={selectedNewsType}
                      onChange={(val) => setSelectedNewsType(val)}
                      options={[
                        { label: '全部', value: '' },
                        { label: '招标', value: '1' },
                        { label: '中标', value: '2' },
                        { label: '合同', value: '3' },
                      ]}
                      placeholder="全部"
                      className={`${SELECT_CLASS} w-full`}
                    />
                  </div>
                  <div>
                    <label className="block text-sm font-medium text-[#1a1a1a] mb-1.5">
                      附件
                    </label>
                    <BidSelect
                      value={hasFile}
                      onChange={(val) => setHasFile(val)}
                      options={[
                        { label: '不限', value: '' },
                        { label: '有', value: '1' },
                        { label: '无', value: '0' },
                      ]}
                      placeholder="不限"
                      className={`${SELECT_CLASS} w-full`}
                    />
                  </div>
                </div>

                {/* 金额 */}
                <div>
                  <label className="block text-sm font-medium text-[#1a1a1a] mb-1.5">
                    金额（元）
                  </label>
                  <div className="flex items-center gap-2">
                    <Input
                      value={projectMoneyMin}
                      onChange={(e) => setProjectMoneyMin(e.target.value)}
                      placeholder="下限"
                      className={`${INPUT_CLASS} flex-1`}
                    />
                    <span className="text-xs text-[#A3A3A3]">—</span>
                    <Input
                      value={projectMoneyMax}
                      onChange={(e) => setProjectMoneyMax(e.target.value)}
                      placeholder="上限"
                      className={`${INPUT_CLASS} flex-1`}
                    />
                  </div>
                </div>

                {/* 甲方 + 乙方 */}
                <div className="grid grid-cols-2 gap-3">
                  <div>
                    <label className="block text-sm font-medium text-[#1a1a1a] mb-1.5">
                      甲方
                    </label>
                    <Input
                      value={partAName}
                      onChange={(e) => setPartAName(e.target.value)}
                      placeholder="甲方名称"
                      className={`${INPUT_CLASS} w-full`}
                    />
                  </div>
                  <div>
                    <label className="block text-sm font-medium text-[#1a1a1a] mb-1.5">
                      乙方
                    </label>
                    <Input
                      value={partBName}
                      onChange={(e) => setPartBName(e.target.value)}
                      placeholder="乙方名称"
                      className={`${INPUT_CLASS} w-full`}
                    />
                  </div>
                </div>

                {/* 代理机构 */}
                <div>
                  <label className="block text-sm font-medium text-[#1a1a1a] mb-1.5">
                    代理机构
                  </label>
                  <Input
                    value={agentName}
                    onChange={(e) => setAgentName(e.target.value)}
                    placeholder="代理机构名称"
                    className={`${INPUT_CLASS} w-full`}
                  />
                </div>

                {/* 合同到期 */}
                <div>
                  <label className="block text-sm font-medium text-[#1a1a1a] mb-1.5">
                    合同到期
                  </label>
                  <DatePickerWithRange
                    selected={contractEndDateRange}
                    onSelect={(range: any) => setContractEndDateRange(range)}
                    className="w-full"
                  />
                </div>
              </div>
            )}

            {/* Search button */}
            <Button
              onClick={onSearch}
              disabled={!dateRange?.from}
              className="w-full h-11 bg-[#000000] hover:bg-[#171717] text-white text-sm font-medium rounded-lg transition-all hover:shadow-[0_4px_12px_rgba(0,0,0,0.15)] mt-2 disabled:opacity-40 disabled:cursor-not-allowed"
            >
              <Search className="size-4 mr-2" />
              搜索标讯
            </Button>
          </div>
        </div>
      </div>
    </div>
  );
}
