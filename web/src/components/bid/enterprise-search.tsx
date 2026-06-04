import { Button } from '@/components/ui/button';
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
  if (json.code !== 0) throw new Error(json.message || `API error`);
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

  const handleSearch = async () => {
    if (!companyName.trim()) return;
    setLoading(true);
    setError(null);
    setData(null);
    try {
      const result = await enterpriseFetch({ company_name: companyName });
      setData(result);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  };

  const base = data?.baseInfo;
  const profile = base?.enterpriseProfile;
  const reg = base?.registrationInfo;
  const op = base?.operationInfo;
  const contact = base?.contactInfo;
  const insights = data?.projectInsights;
  const rel = data?.relationshipSummary;

  return (
    <div className="flex flex-col h-full">
      <div className="px-4 pt-4 pb-2 flex gap-2 items-end">
        <input
          className={`${INPUT_CLASS} flex-1`}
          placeholder="输入企业名称查询画像"
          value={companyName}
          onChange={(e) => setCompanyName(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && handleSearch()}
        />
        <Button
          size="sm"
          onClick={handleSearch}
          disabled={loading}
          className="bg-[#000000] hover:bg-[#171717] text-white h-9 text-xs"
        >
          <Search className="w-3.5 h-3.5 mr-1" />
          {loading ? '查询中...' : '查询'}
        </Button>
      </div>

      <div className="flex-1 overflow-y-auto px-4 pb-4">
        {error && (
          <div className="mt-2 p-3 bg-red-50 text-red-600 text-xs rounded-lg flex items-center gap-2">
            <X className="w-3.5 h-3.5" />
            {error}
          </div>
        )}

        {!data && !error && !loading && (
          <div className="flex items-center justify-center h-40 text-xs text-[#999]">
            输入企业名称查看画像信息
          </div>
        )}

        {data && (
          <div className="mt-2 space-y-3">
            {/* Basic info card */}
            <div className="border border-[#E5E5E5] rounded-lg p-3">
              <div className="text-sm font-semibold mb-2 flex items-center gap-1.5">
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
              <div className="border border-[#E5E5E5] rounded-lg p-3">
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
              <div className="border border-[#E5E5E5] rounded-lg p-3">
                <div className="text-sm font-semibold mb-2">关系网络</div>
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
              <div className="border border-[#E5E5E5] rounded-lg p-3">
                <div className="text-sm font-semibold mb-2">
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
              <div className="border border-[#E5E5E5] rounded-lg p-3">
                <div className="text-sm font-semibold mb-2">
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
        )}
      </div>
    </div>
  );
}
