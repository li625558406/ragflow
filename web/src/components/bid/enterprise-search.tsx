import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { getAuthorization } from '@/utils/authorization-util';
import { Building2, Globe, Mail, Phone, Search, X } from 'lucide-react';
import { useState } from 'react';

const INPUT_CLASS =
  'h-9 px-3 text-xs text-[#000000] border border-[#D4D4D4] bg-white hover:border-[#A3A3A3] focus:border-[#000000] focus:ring-2 focus:ring-[#000000]/10 rounded-lg transition-all';

async function enterpriseFetch(params: Record<string, any>) {
  const qs =
    '?' +
    Object.entries(params)
      .filter(([, v]) => v !== undefined && v !== '')
      .map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(v)}`)
      .join('&');
  const resp = await fetch(`/api/v1/bid/enterprises/profile${qs}`, {
    headers: { Authorization: getAuthorization() },
  });
  if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
  const json = await resp.json();
  if (json.code !== 0) throw new Error(json.message || 'API error');
  return json.data;
}

interface EnterpriseData {
  companyName: string;
  baseInfo?: {
    enterpriseProfile?: {
      companyTypeName: string;
      industryName: string;
      legalRepresentative: string;
      establishmentDate: string;
      operatingStatus?: { statusName: string };
    };
    registrationInfo?: {
      creditCode: string;
      registeredCapital?: { amount: number; unitName: string };
    };
    operationInfo?: {
      businessScope: string;
    };
    contactInfo?: {
      registeredAddress: string;
      website: string;
      contactPhones: string[];
      contactEmails: string[];
    };
  };
  projectInsights?: {
    bidStatistics?: {
      industryName: string;
      projectCount: number;
      projectShare: string;
      budgetAmountWan: string;
    }[];
    winStatistics?: {
      industryName: string;
      projectCount: number;
      projectShare: string;
      budgetAmountWan: string;
    }[];
  };
  relationshipSummary?: {
    contactPersonCount: string;
    customerProjectCount: string;
    supplierProjectCount: string;
  };
}

function StatRow({
  label,
  value,
}: {
  label: string;
  value: string | number | undefined;
}) {
  return (
    <div className="flex items-center justify-between py-1.5 border-b border-[#F5F5F5] last:border-0">
      <span className="text-xs text-[#999]">{label}</span>
      <span className="text-xs text-[#333] font-medium">{value || '-'}</span>
    </div>
  );
}

export default function EnterpriseSearch() {
  const [companyName, setCompanyName] = useState('');
  const [loading, setLoading] = useState(false);
  const [data, setData] = useState<EnterpriseData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [hasSearched, setHasSearched] = useState(false);

  const handleSearch = async () => {
    if (!companyName.trim()) return;
    setLoading(true);
    setError(null);
    setData(null);
    try {
      const result = await enterpriseFetch({ company_name: companyName });
      setData(result);
      setHasSearched(true);
    } catch (e: any) {
      setError(e.message);
      setHasSearched(true);
    } finally {
      setLoading(false);
    }
  };

  const handleBackToSearch = () => setHasSearched(false);

  const base = data?.baseInfo;
  const profile = base?.enterpriseProfile;
  const reg = base?.registrationInfo;
  const op = base?.operationInfo;
  const contact = base?.contactInfo;
  const insights = data?.projectInsights;
  const rel = data?.relationshipSummary;

  // ================================================================
  // STATE 1: Search Hero (centered card)
  // ================================================================
  if (!hasSearched) {
    return (
      <div className="flex-1 overflow-auto">
        <div className="min-h-full flex items-center justify-center px-6 py-12">
          <div className="w-full max-w-2xl">
            {/* Header */}
            <div className="text-center mb-8">
              <div className="cs-card-enter inline-flex items-center justify-center size-16 rounded-2xl bg-[#F5F5F5] mb-4">
                <Building2 className="size-7 text-[#404040]" />
              </div>
              <h1 className="cs-card-enter cs-card-d1 text-2xl font-bold text-[#000000] tracking-tight">
                企业查询
              </h1>
              <p className="cs-card-enter cs-card-d1 text-sm text-[#A3A3A3] mt-1">
                查询企业画像与招投标关系
              </p>
            </div>

            {/* Search card */}
            <div className="cs-card-enter cs-card-d2 bg-white rounded-xl border border-[#E8E8E8] shadow-[0_4px_24px_rgba(0,0,0,0.04)] p-6">
              <div className="mb-4">
                <label className="block text-sm font-medium text-[#1a1a1a] mb-1.5">
                  企业名称
                </label>
                <div className="relative">
                  <Input
                    value={companyName}
                    onChange={(e) => setCompanyName(e.target.value)}
                    onKeyDown={(e) => e.key === 'Enter' && handleSearch()}
                    placeholder="输入企业名称查询画像..."
                    className={`${INPUT_CLASS} w-full`}
                  />
                  {companyName && (
                    <button
                      onClick={() => setCompanyName('')}
                      className="absolute right-2 top-1/2 -translate-y-1/2 text-[#A3A3A3] hover:text-[#000000] transition-colors"
                    >
                      <X className="size-3.5" />
                    </button>
                  )}
                </div>
              </div>

              <Button
                onClick={handleSearch}
                disabled={loading || !companyName.trim()}
                className="w-full h-11 bg-[#000000] hover:bg-[#171717] text-white text-sm font-medium rounded-lg transition-all hover:shadow-[0_4px_12px_rgba(0,0,0,0.15)] mt-2 disabled:opacity-40 disabled:cursor-not-allowed"
              >
                {loading ? (
                  <span className="inline-flex items-center gap-2">
                    <svg
                      className="size-4 animate-spin"
                      viewBox="0 0 24 24"
                      fill="none"
                    >
                      <circle
                        className="opacity-25"
                        cx="12"
                        cy="12"
                        r="10"
                        stroke="currentColor"
                        strokeWidth="4"
                      />
                      <path
                        className="opacity-75"
                        fill="currentColor"
                        d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"
                      />
                    </svg>
                    查询中...
                  </span>
                ) : (
                  <>
                    <Search className="size-4 mr-2" />
                    查询企业画像
                  </>
                )}
              </Button>
            </div>
          </div>
        </div>
      </div>
    );
  }

  // ================================================================
  // STATE 2: Results (enterprise profile)
  // ================================================================
  return (
    <div className="flex-1 flex flex-col min-h-0 bg-white overflow-hidden">
      {/* Compact bar */}
      <div className="shrink-0 px-6 py-3 border-b border-[#F0F0F0]">
        <div className="flex items-center gap-3">
          <span className="text-sm font-semibold text-[#000000] truncate">
            {companyName}
          </span>
          <button
            onClick={handleBackToSearch}
            className="inline-flex items-center gap-1 h-8 px-3 text-xs font-medium text-[#525252] hover:text-[#000000] hover:bg-[#F5F5F5] rounded-lg transition-colors shrink-0"
          >
            修改条件
          </button>
        </div>
      </div>

      {/* Error banner */}
      {error && (
        <div className="shrink-0 px-6 pt-3">
          <div className="bg-[#FFF2F0] border border-[#FFCCC7] rounded-lg px-4 py-3 flex items-start gap-3">
            <span className="text-sm text-[#FF4D4F] shrink-0 mt-0.5">!</span>
            <div className="flex-1 min-w-0">
              <p className="text-sm font-medium text-[#FF4D4F]">查询失败</p>
              <p className="text-xs text-[#8C8C8C] mt-0.5 break-all">{error}</p>
            </div>
            <button
              onClick={() => setError(null)}
              className="shrink-0 text-[#A3A3A3] hover:text-[#000000] transition-colors"
            >
              <X className="size-3.5" />
            </button>
          </div>
        </div>
      )}

      {/* Profile content */}
      <div className="flex-1 min-h-0 px-6 py-4 overflow-auto">
        {loading ? (
          <div className="space-y-3">
            {Array.from({ length: 3 }).map((_, i) => (
              <div
                key={i}
                className="rounded-xl border border-[#E8E8E8] p-5 animate-pulse"
              >
                <div className="h-4 w-1/3 bg-[#F0F0F0] rounded mb-3" />
                <div className="h-4 w-2/3 bg-[#F0F0F0] rounded mb-2" />
                <div className="h-4 w-1/2 bg-[#F0F0F0] rounded" />
              </div>
            ))}
          </div>
        ) : data ? (
          <div className="space-y-3">
            {/* Basic info card */}
            <div className="rounded-xl border border-[#E8E8E8] bg-[#FAFAFA] p-5">
              <div className="text-sm font-semibold mb-3 flex items-center gap-1.5">
                <Building2 className="w-4 h-4" />
                企业基本信息
              </div>
              <div className="grid grid-cols-2 gap-x-6">
                <StatRow label="企业名称" value={data.companyName} />
                <StatRow label="企业类型" value={profile?.companyTypeName} />
                <StatRow
                  label="法定代表人"
                  value={profile?.legalRepresentative}
                />
                <StatRow
                  label="经营状态"
                  value={profile?.operatingStatus?.statusName}
                />
                <StatRow
                  label="成立日期"
                  value={profile?.establishmentDate?.split(' ')[0]}
                />
                <StatRow label="所属行业" value={profile?.industryName} />
                <StatRow label="信用代码" value={reg?.creditCode} />
                <StatRow
                  label="注册资本"
                  value={
                    reg?.registeredCapital
                      ? `${reg.registeredCapital.amount} ${reg.registeredCapital.unitName || ''}`
                      : ''
                  }
                />
              </div>
              {contact?.registeredAddress && (
                <div className="mt-2 text-xs text-[#666]">
                  <span className="text-[#999]">注册地址：</span>
                  {contact.registeredAddress}
                </div>
              )}
              <div className="mt-1.5 flex flex-wrap gap-3">
                {contact?.website && (
                  <span className="flex items-center gap-1 text-xs text-[#2563EB]">
                    <Globe className="w-3 h-3" />
                    <a href={contact.website} target="_blank" rel="noreferrer">
                      {contact.website}
                    </a>
                  </span>
                )}
                {contact?.contactPhones?.[0] && (
                  <span className="flex items-center gap-1 text-xs text-[#333]">
                    <Phone className="w-3 h-3" />
                    {contact.contactPhones[0]}
                  </span>
                )}
                {contact?.contactEmails?.[0] && (
                  <span className="flex items-center gap-1 text-xs text-[#333]">
                    <Mail className="w-3 h-3" />
                    {contact.contactEmails[0]}
                  </span>
                )}
              </div>
            </div>

            {/* Business scope */}
            {op?.businessScope && (
              <div className="rounded-xl border border-[#E8E8E8] bg-[#FAFAFA] p-5">
                <div className="text-xs font-semibold text-[#333] mb-1">
                  经营范围
                </div>
                <div className="text-xs text-[#666] leading-relaxed">
                  {op.businessScope}
                </div>
              </div>
            )}

            {/* Relationship summary */}
            {rel && (
              <div className="rounded-xl border border-[#E8E8E8] bg-[#FAFAFA] p-5">
                <div className="text-sm font-semibold mb-3">关系网络</div>
                <div className="grid grid-cols-3 gap-4 text-center">
                  <div>
                    <div className="text-lg font-bold text-[#000]">
                      {rel.contactPersonCount || 0}
                    </div>
                    <div className="text-xs text-[#999]">联系人</div>
                  </div>
                  <div>
                    <div className="text-lg font-bold text-[#000]">
                      {rel.customerProjectCount || 0}
                    </div>
                    <div className="text-xs text-[#999]">客户项目</div>
                  </div>
                  <div>
                    <div className="text-lg font-bold text-[#000]">
                      {rel.supplierProjectCount || 0}
                    </div>
                    <div className="text-xs text-[#999]">供应商项目</div>
                  </div>
                </div>
              </div>
            )}

            {/* Bid statistics */}
            {insights?.bidStatistics && insights.bidStatistics.length > 0 && (
              <div className="rounded-xl border border-[#E8E8E8] bg-[#FAFAFA] p-5">
                <div className="text-sm font-semibold mb-3">
                  投标统计（按行业）
                </div>
                <div className="space-y-1">
                  {insights.bidStatistics.map((s, i) => (
                    <div
                      key={i}
                      className="flex items-center justify-between py-1 text-xs"
                    >
                      <span className="text-[#666]">{s.industryName}</span>
                      <div className="flex gap-4">
                        <span className="text-[#333]">
                          {s.projectCount} 个项目
                        </span>
                        <span className="text-[#999]">{s.projectShare}</span>
                        <span className="text-[#999]">{s.budgetAmountWan}</span>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {/* Win statistics */}
            {insights?.winStatistics && insights.winStatistics.length > 0 && (
              <div className="rounded-xl border border-[#E8E8E8] bg-[#FAFAFA] p-5">
                <div className="text-sm font-semibold mb-3">
                  中标统计（按行业）
                </div>
                <div className="space-y-1">
                  {insights.winStatistics.map((s, i) => (
                    <div
                      key={i}
                      className="flex items-center justify-between py-1 text-xs"
                    >
                      <span className="text-[#666]">{s.industryName}</span>
                      <div className="flex gap-4">
                        <span className="text-[#333]">
                          {s.projectCount} 个项目
                        </span>
                        <span className="text-[#999]">{s.projectShare}</span>
                        <span className="text-[#999]">{s.budgetAmountWan}</span>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        ) : null}
      </div>
    </div>
  );
}
