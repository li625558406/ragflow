import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { getAuthorization } from '@/utils/authorization-util';
import {
  AlertTriangle,
  Award,
  Building2,
  FileText,
  Globe,
  Loader2,
  Mail,
  Phone,
  Search,
  Shield,
  Users,
  X,
} from 'lucide-react';
import { useState } from 'react';

const INPUT_CLASS =
  'h-9 px-3 text-xs text-[#000000] border border-[#D4D4D4] bg-white hover:border-[#A3A3A3] focus:border-[#000000] focus:ring-2 focus:ring-[#000000]/10 rounded-lg transition-all';

async function fetchJSON(path: string, params: Record<string, any>) {
  const qs =
    '?' +
    Object.entries(params)
      .filter(([, v]) => v !== undefined && v !== '')
      .map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(v)}`)
      .join('&');
  const resp = await fetch(`/api/v1/bid/${path}${qs}`, {
    headers: { Authorization: getAuthorization() },
  });
  if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
  const json = await resp.json();
  if (json.code !== 0) throw new Error(json.message || 'API error');
  return json.data;
}

// ── New API response types ────────────────────────────────────────────────

interface BusinessBase {
  CompanyName?: string;
  CompanyCode?: string;
  CreditNo?: string;
  LegalPerson?: string;
  CompanyStatus?: string;
  CompanyType?: string;
  Capital?: string;
  BusinessScope?: string;
  BusinessDateFrom?: string;
  BusinessDateTo?: string;
  EstablishDate?: string;
  IssueDate?: string;
  Authority?: string;
  Province?: string;
  CompanyAddress?: string;
  IsOnStock?: string;
  StockNumber?: string;
  StockType?: string;
  RevokeDate?: string;
  KeyNo?: string;
  OrgCode?: string;
}

interface IndustryInfo {
  Industry?: string;
  SubIndustry?: string;
}

interface ContactInfo {
  PhoneNumber?: string;
  Email?: string;
  Website?: { Url?: string; Name?: string }[];
}

interface Partner {
  StockName?: string;
  StockType?: string;
  StockPercent?: string;
  StockCapital?: string;
  StockRealCapital?: string;
  InvestType?: string;
  CapiDate?: string;
  InvestName?: string;
}

interface Employee {
  Position?: string;
  EmployeeName?: string;
}

interface ChangeRecord {
  ChangeField?: string;
  ChangeBefore?: string;
  ChangeAfter?: string;
  ChangeDate?: string;
}

interface Branch {
  CompanyCode?: string;
  CompanyName?: string;
  Authority?: string;
  CreditNo?: string;
  LegalPerson?: string;
}

interface PledgeItem {
  // 股权出质 (Pledges)
  RegistNo?: string;
  Pledgor?: string;
  PledgorNo?: string;
  Pledgee?: string;
  PledgeeNo?: string;
  PledgedAmount?: string;
  RegDate?: string;
  // 动产抵押 (MPledges) — 不同字段名
  RegisterNo?: string;
  RegisterDate?: string;
  RegisterOffice?: string;
  DebtSecuredAmount?: string;
  // 共用
  PublicDate?: string;
  Status?: string;
}

interface PenaltyItem {
  DocNo?: string;
  PenaltyType?: string;
  OfficeName?: string;
  Content?: string;
  PenaltyDate?: string;
  PublicDate?: string;
  Remark?: string;
}

interface ExceptionItem {
  AddReason?: string;
  AddDate?: string;
  RemoveReason?: string;
  RemoveDate?: string;
  DecisionOffice?: string;
  CasRemoveDecisionOfficeeCode?: string;
}

interface SpotCheckItem {
  No?: string;
  ExecutiveOrg?: string;
  Type?: string;
  Date?: string;
  Consequence?: string;
  Remark?: string;
}

interface ShiXinItem {
  Iname?: string;
  RegDate?: string;
  CaseCode?: string;
  CardNum?: string;
  GistCid?: string;
  PublishDate?: string;
  Performance?: string;
  DisreputTypeName?: string;
  CourtName?: string;
}

interface ZhiXingItem {
  CaseState?: string;
  PartyCardnum?: string;
  ZxId?: string;
  Pname?: string;
  CaseCreateTime?: string;
  CaseCode?: string;
  ExecCourtName?: string;
  ExecMoney?: string;
}

interface PermissionItem {
  Name?: string;
  Province?: string;
  Liandate?: string;
  CaseNo?: string;
}

interface TaxCreditItem {
  TaxPayerNo?: string;
  TaxPayerName?: string;
  Year?: string;
  Level?: string;
}

interface OriginalNameItem {
  Name?: string;
  ChangeDate?: string;
}

interface EnterpriseBusinessData {
  Base?: BusinessBase;
  Industry?: IndustryInfo;
  ContactInfo?: ContactInfo;
  Partners?: Partner[];
  Employees?: Employee[];
  Changes?: ChangeRecord[];
  Branches?: Branch[];
  Pledges?: PledgeItem[];
  MPledges?: PledgeItem[];
  Penalties?: PenaltyItem[];
  Exceptions?: ExceptionItem[];
  SpotChecks?: SpotCheckItem[];
  ShiXinItems?: ShiXinItem[];
  ZhiXingItems?: ZhiXingItem[];
  Permissions?: PermissionItem[];
  TaxCreditItems?: TaxCreditItem[];
  OriginalName?: OriginalNameItem[];
}

// ── Utility ───────────────────────────────────────────────────────────────

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

function formatDate(d?: string) {
  if (!d) return '-';
  return d.split(' ')[0];
}

function formatAmount(amount?: string) {
  if (!amount) return '-';
  return amount;
}

type TabKey = 'base' | 'stakeholders' | 'changes' | 'risk' | 'qualifications';

interface TabDef {
  key: TabKey;
  label: string;
  icon: React.ReactNode;
  badge?: number;
}

export default function EnterpriseSearch() {
  const [keyword, setKeyword] = useState('');
  const [loading, setLoading] = useState(false);
  const [data, setData] = useState<EnterpriseBusinessData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [hasSearched, setHasSearched] = useState(false);
  const [activeTab, setActiveTab] = useState<TabKey>('base');

  const handleSearch = async () => {
    const trimmed = keyword.trim();
    if (!trimmed) return;
    setLoading(true);
    setError(null);
    setData(null);
    setActiveTab('base');
    try {
      const result = await fetchJSON('enterprises/business', {
        keyword: trimmed,
      });
      setData(result);
      setHasSearched(true);
    } catch (e: any) {
      setError(e.message);
      setHasSearched(true);
    } finally {
      setLoading(false);
    }
  };

  const handleBackToSearch = () => {
    setHasSearched(false);
    setData(null);
    setError(null);
  };

  const base = data?.Base;
  const industry = data?.Industry;
  const contact = data?.ContactInfo;

  const TABS: TabDef[] = [
    {
      key: 'base',
      label: '工商信息',
      icon: <Building2 className="w-3.5 h-3.5" />,
    },
    {
      key: 'stakeholders',
      label: '股东高管',
      icon: <Users className="w-3.5 h-3.5" />,
      badge: (data?.Partners?.length || 0) + (data?.Employees?.length || 0),
    },
    {
      key: 'changes',
      label: '变更记录',
      icon: <FileText className="w-3.5 h-3.5" />,
      badge: (data?.Changes?.length || 0) + (data?.OriginalName?.length || 0),
    },
    {
      key: 'risk',
      label: '经营风险',
      icon: <AlertTriangle className="w-3.5 h-3.5" />,
      badge:
        (data?.Penalties?.length || 0) +
        (data?.Exceptions?.length || 0) +
        (data?.ShiXinItems?.length || 0) +
        (data?.ZhiXingItems?.length || 0),
    },
    {
      key: 'qualifications',
      label: '资质信息',
      icon: <Award className="w-3.5 h-3.5" />,
      badge:
        (data?.Permissions?.length || 0) + (data?.TaxCreditItems?.length || 0),
    },
  ];

  // ── STATE 1: Search Hero ──────────────────────────────────────────────
  if (!hasSearched) {
    return (
      <div className="flex-1 overflow-auto">
        <div className="min-h-full flex items-center justify-center px-6 py-12">
          <div className="w-full max-w-2xl">
            <div className="text-center mb-8">
              <div className="cs-card-enter inline-flex items-center justify-center size-16 rounded-2xl bg-[#F5F5F5] mb-4">
                <Building2 className="size-7 text-[#404040]" />
              </div>
              <h1 className="cs-card-enter cs-card-d1 text-2xl font-bold text-[#000000] tracking-tight">
                企业查询
              </h1>
              <p className="cs-card-enter cs-card-d1 text-sm text-[#A3A3A3] mt-1">
                输入企业名称或统一社会信用代码查询企业全景工商信息
              </p>
            </div>

            <div className="cs-card-enter cs-card-d2 bg-white rounded-xl border border-[#E8E8E8] shadow-[0_4px_24px_rgba(0,0,0,0.04)] p-6">
              <div className="mb-4">
                <label className="block text-sm font-medium text-[#1a1a1a] mb-1.5">
                  企业名称 / 信用代码
                </label>
                <div className="relative">
                  <Input
                    value={keyword}
                    onChange={(e) => setKeyword(e.target.value)}
                    onKeyDown={(e) => e.key === 'Enter' && handleSearch()}
                    placeholder="输入企业名称或统一社会信用代码..."
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

              <Button
                onClick={handleSearch}
                disabled={loading || !keyword.trim()}
                className="w-full h-11 bg-[#000000] hover:bg-[#171717] text-white text-sm font-medium rounded-lg transition-all hover:shadow-[0_4px_12px_rgba(0,0,0,0.15)] mt-2 disabled:opacity-40 disabled:cursor-not-allowed"
              >
                {loading ? (
                  <span className="inline-flex items-center gap-2">
                    <Loader2 className="size-4 animate-spin" strokeWidth={4} />
                    查询中...
                  </span>
                ) : (
                  <>
                    <Search className="size-4 mr-2" />
                    查询企业信息
                  </>
                )}
              </Button>
            </div>
          </div>
        </div>
      </div>
    );
  }

  // ── STATE 2: Results ──────────────────────────────────────────────────

  const renderBaseInfo = () => (
    <div className="space-y-3">
      {/* Basic info card */}
      <div className="rounded-xl border border-[#E8E8E8] bg-[#FAFAFA] p-5">
        <div className="text-sm font-semibold mb-3 flex items-center gap-1.5">
          <Building2 className="w-4 h-4" />
          企业基本信息
        </div>
        <div className="grid grid-cols-2 gap-x-6">
          <StatRow label="企业名称" value={base?.CompanyName} />
          <StatRow label="法定代表人" value={base?.LegalPerson} />
          <StatRow label="经营状态" value={base?.CompanyStatus} />
          <StatRow label="企业类型" value={base?.CompanyType} />
          <StatRow label="成立日期" value={formatDate(base?.EstablishDate)} />
          <StatRow label="发照日期" value={formatDate(base?.IssueDate)} />
          <StatRow label="注册资本" value={formatAmount(base?.Capital)} />
          <StatRow label="注册号" value={base?.CompanyCode} />
          <StatRow label="统一社会信用代码" value={base?.CreditNo} />
          <StatRow label="组织机构代码" value={base?.OrgCode} />
          <StatRow
            label="营业期限"
            value={
              base?.BusinessDateTo
                ? `${formatDate(base.BusinessDateFrom)} 至 ${formatDate(base.BusinessDateTo)}`
                : '-'
            }
          />
          <StatRow label="登记机关" value={base?.Authority} />
          {base?.IsOnStock === '1' && (
            <StatRow
              label="上市信息"
              value={`${base?.StockType || ''} ${base?.StockNumber || ''}`}
            />
          )}
        </div>
        {base?.CompanyAddress && (
          <div className="mt-2 pt-2 border-t border-[#E8E8E8] text-xs text-[#666]">
            <span className="text-[#999]">注册地址：</span>
            {base.CompanyAddress}
          </div>
        )}
        {base?.BusinessScope && (
          <div className="mt-2 pt-2 border-t border-[#E8E8E8] text-xs text-[#666]">
            <span className="text-[#999]">经营范围：</span>
            <p className="mt-1 leading-relaxed">{base.BusinessScope}</p>
          </div>
        )}
      </div>

      {/* Industry + Contact */}
      <div className="grid grid-cols-2 gap-3">
        {/* Industry */}
        {(industry?.Industry || industry?.SubIndustry) && (
          <div className="rounded-xl border border-[#E8E8E8] bg-[#FAFAFA] p-5">
            <div className="text-sm font-semibold mb-3">行业分类</div>
            <StatRow label="大类" value={industry.Industry} />
            <StatRow label="小类" value={industry.SubIndustry} />
          </div>
        )}

        {/* Contact */}
        <div className="rounded-xl border border-[#E8E8E8] bg-[#FAFAFA] p-5">
          <div className="text-sm font-semibold mb-3">联系方式</div>
          {contact?.PhoneNumber && (
            <div className="flex items-center gap-1.5 text-xs text-[#333] py-1">
              <Phone className="w-3 h-3 text-[#999]" />
              {contact.PhoneNumber}
            </div>
          )}
          {contact?.Email && (
            <div className="flex items-center gap-1.5 text-xs text-[#333] py-1">
              <Mail className="w-3 h-3 text-[#999]" />
              {contact.Email}
            </div>
          )}
          {contact?.Website?.map((w, i) => (
            <div
              key={i}
              className="flex items-center gap-1.5 text-xs text-[#2563EB] py-1"
            >
              <Globe className="w-3 h-3" />
              <a href={w.Url} target="_blank" rel="noreferrer">
                {w.Name || w.Url}
              </a>
            </div>
          ))}
          {!contact?.PhoneNumber &&
            !contact?.Email &&
            !contact?.Website?.length && (
              <div className="text-xs text-[#A3A3A3] py-2">暂无联系方式</div>
            )}
        </div>
      </div>

      {/* Original Names */}
      {data?.OriginalName && data.OriginalName.length > 0 && (
        <div className="rounded-xl border border-[#E8E8E8] bg-[#FAFAFA] p-5">
          <div className="text-sm font-semibold mb-3">曾用名</div>
          <div className="space-y-1.5">
            {data.OriginalName.map((n, i) => (
              <div
                key={i}
                className="flex items-center justify-between text-xs"
              >
                <span className="text-[#333] font-medium">{n.Name}</span>
                <span className="text-[#999]">{formatDate(n.ChangeDate)}</span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );

  const renderStakeholders = () => (
    <div className="space-y-3">
      {/* Partners */}
      {data?.Partners && data.Partners.length > 0 && (
        <div className="rounded-xl border border-[#E8E8E8] bg-[#FAFAFA] p-5">
          <div className="text-sm font-semibold mb-3 flex items-center gap-1.5">
            <Users className="w-4 h-4" />
            股东信息 ({data.Partners.length})
          </div>
          <div className="space-y-2">
            {data.Partners.map((p, i) => (
              <div
                key={i}
                className="border border-[#E8E8E8] rounded-lg p-3 bg-white"
              >
                <div className="flex items-center justify-between mb-1">
                  <span className="text-sm font-medium text-[#000]">
                    {p.StockName || '-'}
                  </span>
                  <span className="text-xs text-[#999]">{p.StockType}</span>
                </div>
                <div className="grid grid-cols-3 gap-x-4 gap-y-1 text-xs">
                  <div>
                    <span className="text-[#999]">出资比例：</span>
                    {p.StockPercent ? `${p.StockPercent}%` : '-'}
                  </div>
                  <div>
                    <span className="text-[#999]">认缴出资：</span>
                    {p.StockCapital ? `${p.StockCapital}万` : '-'}
                  </div>
                  <div>
                    <span className="text-[#999]">实缴出资：</span>
                    {p.StockRealCapital ? `${p.StockRealCapital}万` : '-'}
                  </div>
                  <div>
                    <span className="text-[#999]">认缴方式：</span>
                    {p.InvestType || '-'}
                  </div>
                  <div>
                    <span className="text-[#999]">实缴方式：</span>
                    {p.InvestName || '-'}
                  </div>
                  <div>
                    <span className="text-[#999]">实缴时间：</span>
                    {formatDate(p.CapiDate)}
                  </div>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Employees */}
      {data?.Employees && data.Employees.length > 0 && (
        <div className="rounded-xl border border-[#E8E8E8] bg-[#FAFAFA] p-5">
          <div className="text-sm font-semibold mb-3 flex items-center gap-1.5">
            <Shield className="w-4 h-4" />
            企业高管 ({data.Employees.length})
          </div>
          <div className="space-y-1.5">
            {data.Employees.map((e, i) => (
              <div
                key={i}
                className="flex items-center justify-between py-1.5 border-b border-[#F5F5F5] last:border-0 text-xs"
              >
                <span className="font-medium text-[#333]">
                  {e.EmployeeName || '-'}
                </span>
                <span className="text-[#999]">{e.Position || '-'}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Branches */}
      {data?.Branches && data.Branches.length > 0 && (
        <div className="rounded-xl border border-[#E8E8E8] bg-[#FAFAFA] p-5">
          <div className="text-sm font-semibold mb-3">
            分支机构 ({data.Branches.length})
          </div>
          <div className="space-y-2">
            {data.Branches.map((b, i) => (
              <div
                key={i}
                className="border border-[#E8E8E8] rounded-lg p-3 bg-white"
              >
                <div className="font-medium text-sm text-[#000] mb-1">
                  {b.CompanyName || '-'}
                </div>
                <div className="grid grid-cols-2 gap-x-4 gap-y-0.5 text-xs">
                  <div>
                    <span className="text-[#999]">注册号：</span>
                    {b.CompanyCode || '-'}
                  </div>
                  <div>
                    <span className="text-[#999]">法人：</span>
                    {b.LegalPerson || '-'}
                  </div>
                  <div>
                    <span className="text-[#999]">信用代码：</span>
                    {b.CreditNo || '-'}
                  </div>
                  <div>
                    <span className="text-[#999]">登记机关：</span>
                    {b.Authority || '-'}
                  </div>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {!data?.Partners?.length &&
        !data?.Employees?.length &&
        !data?.Branches?.length && (
          <div className="text-center py-16 text-xs text-[#A3A3A3]">
            暂无股东高管数据
          </div>
        )}
    </div>
  );

  const renderChanges = () => (
    <div className="space-y-3">
      {data?.Changes && data.Changes.length > 0 ? (
        <div className="rounded-xl border border-[#E8E8E8] bg-[#FAFAFA] p-5">
          <div className="text-sm font-semibold mb-3 flex items-center gap-1.5">
            <FileText className="w-4 h-4" />
            工商变更记录 ({data.Changes.length})
          </div>
          <div className="space-y-2">
            {data.Changes.map((c, i) => (
              <div
                key={i}
                className="border border-[#E8E8E8] rounded-lg p-3 bg-white"
              >
                <div className="flex items-center justify-between mb-1.5">
                  <span className="text-xs font-medium text-[#000] px-2 py-0.5 bg-[#F0F0F0] rounded">
                    {c.ChangeField || '-'}
                  </span>
                  <span className="text-xs text-[#999]">
                    {formatDate(c.ChangeDate)}
                  </span>
                </div>
                <div className="text-xs space-y-0.5">
                  <div className="text-[#A3A3A3]">
                    变更前：
                    <span className="text-[#666]">{c.ChangeBefore || '-'}</span>
                  </div>
                  <div className="text-[#A3A3A3]">
                    变更后：
                    <span className="text-[#000] font-medium">
                      {c.ChangeAfter || '-'}
                    </span>
                  </div>
                </div>
              </div>
            ))}
          </div>
        </div>
      ) : (
        <div className="text-center py-16 text-xs text-[#A3A3A3]">
          暂无变更记录
        </div>
      )}
    </div>
  );

  const renderRisk = () => {
    const hasAny =
      (data?.Penalties?.length || 0) > 0 ||
      (data?.Exceptions?.length || 0) > 0 ||
      (data?.ShiXinItems?.length || 0) > 0 ||
      (data?.ZhiXingItems?.length || 0) > 0 ||
      (data?.SpotChecks?.length || 0) > 0;

    if (!hasAny)
      return (
        <div className="text-center py-16 text-xs text-[#A3A3A3]">
          暂无经营风险记录
        </div>
      );

    return (
      <div className="space-y-3">
        {/* Penalties */}
        {data?.Penalties && data.Penalties.length > 0 && (
          <div className="rounded-xl border border-[#FFE8E5] bg-[#FFF9F8] p-5">
            <div className="text-sm font-semibold mb-3 flex items-center gap-1.5 text-[#D4380D]">
              <AlertTriangle className="w-4 h-4" />
              行政处罚 ({data.Penalties.length})
            </div>
            <div className="space-y-2">
              {data.Penalties.map((p, i) => (
                <div
                  key={i}
                  className="border border-[#FFE8E5] rounded-lg p-3 bg-white"
                >
                  <div className="flex items-center justify-between mb-1">
                    <span className="text-xs font-medium text-[#333]">
                      {p.PenaltyType || '行政处罚'}
                    </span>
                    <span className="text-xs text-[#999]">
                      {formatDate(p.PenaltyDate)}
                    </span>
                  </div>
                  <div className="text-xs text-[#666] mb-1">
                    <span className="text-[#999]">决定机关：</span>
                    {p.OfficeName || '-'}
                  </div>
                  <div className="text-xs text-[#666] mb-1">
                    <span className="text-[#999]">文号：</span>
                    {p.DocNo || '-'}
                  </div>
                  {p.Content && (
                    <div className="text-xs text-[#525252] mt-1.5 p-2 bg-[#FAFAFA] rounded">
                      {p.Content}
                    </div>
                  )}
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Exceptions */}
        {data?.Exceptions && data.Exceptions.length > 0 && (
          <div className="rounded-xl border border-[#FFF3E0] bg-[#FFFCF8] p-5">
            <div className="text-sm font-semibold mb-3 flex items-center gap-1.5 text-[#D46B08]">
              <AlertTriangle className="w-4 h-4" />
              经营异常 ({data.Exceptions.length})
            </div>
            <div className="space-y-2">
              {data.Exceptions.map((e, i) => (
                <div
                  key={i}
                  className="border border-[#FFF3E0] rounded-lg p-3 bg-white"
                >
                  <div className="text-xs text-[#333] mb-1 font-medium">
                    列入原因：{e.AddReason || '-'}
                  </div>
                  <div className="flex items-center justify-between text-xs text-[#999] mb-1">
                    <span>列入日期：{formatDate(e.AddDate)}</span>
                    {e.RemoveDate && (
                      <span>移出日期：{formatDate(e.RemoveDate)}</span>
                    )}
                  </div>
                  {e.RemoveReason && (
                    <div className="text-xs text-[#16A34A]">
                      移出原因：{e.RemoveReason}
                    </div>
                  )}
                  <div className="text-xs text-[#999]">
                    决定机关：{e.DecisionOffice || '-'}
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* ShiXin (失信) */}
        {data?.ShiXinItems && data.ShiXinItems.length > 0 && (
          <div className="rounded-xl border border-[#FFE5E5] bg-[#FFFBFB] p-5">
            <div className="text-sm font-semibold mb-3 flex items-center gap-1.5 text-[#CF1322]">
              <AlertTriangle className="w-4 h-4" />
              失信被执行人 ({data.ShiXinItems.length})
            </div>
            <div className="space-y-2">
              {data.ShiXinItems.map((s, i) => (
                <div
                  key={i}
                  className="border border-[#FFE5E5] rounded-lg p-3 bg-white"
                >
                  <div className="text-sm font-medium text-[#CF1322] mb-1">
                    {s.Iname || '-'}
                  </div>
                  <div className="grid grid-cols-2 gap-x-4 gap-y-0.5 text-xs">
                    <div>
                      <span className="text-[#999]">案号：</span>
                      {s.CaseCode || '-'}
                    </div>
                    <div>
                      <span className="text-[#999]">法院：</span>
                      {s.CourtName || '-'}
                    </div>
                    <div>
                      <span className="text-[#999]">立案日期：</span>
                      {formatDate(s.RegDate)}
                    </div>
                    <div>
                      <span className="text-[#999]">发布时间：</span>
                      {formatDate(s.PublishDate)}
                    </div>
                  </div>
                  {s.Performance && (
                    <div className="text-xs text-[#666] mt-1">
                      履约情况：{s.Performance}
                    </div>
                  )}
                </div>
              ))}
            </div>
          </div>
        )}

        {/* ZhiXing (被执行) */}
        {data?.ZhiXingItems && data.ZhiXingItems.length > 0 && (
          <div className="rounded-xl border border-[#FFF0E0] bg-[#FFFCF8] p-5">
            <div className="text-sm font-semibold mb-3 flex items-center gap-1.5 text-[#D46B08]">
              <AlertTriangle className="w-4 h-4" />
              被执行人 ({data.ZhiXingItems.length})
            </div>
            <div className="space-y-2">
              {data.ZhiXingItems.map((z, i) => (
                <div
                  key={i}
                  className="border border-[#FFF0E0] rounded-lg p-3 bg-white"
                >
                  <div className="text-sm font-medium text-[#333] mb-1">
                    {z.Pname || '-'}
                  </div>
                  <div className="grid grid-cols-2 gap-x-4 gap-y-0.5 text-xs">
                    <div>
                      <span className="text-[#999]">案号：</span>
                      {z.CaseCode || '-'}
                    </div>
                    <div>
                      <span className="text-[#999]">法院：</span>
                      {z.ExecCourtName || '-'}
                    </div>
                    <div>
                      <span className="text-[#999]">立案时间：</span>
                      {formatDate(z.CaseCreateTime)}
                    </div>
                    <div>
                      <span className="text-[#999]">标的：</span>
                      {z.ExecMoney || '-'}
                    </div>
                    <div>
                      <span className="text-[#999]">状态：</span>
                      {z.CaseState || '-'}
                    </div>
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* SpotChecks */}
        {data?.SpotChecks && data.SpotChecks.length > 0 && (
          <div className="rounded-xl border border-[#E8E8E8] bg-[#FAFAFA] p-5">
            <div className="text-sm font-semibold mb-3">
              抽查检查 ({data.SpotChecks.length})
            </div>
            <div className="space-y-1.5">
              {data.SpotChecks.map((s, i) => (
                <div
                  key={i}
                  className="flex items-center justify-between text-xs py-1 border-b border-[#F5F5F5] last:border-0"
                >
                  <div>
                    <span className="text-[#333]">{s.Type || '抽查'}</span>
                    <span className="text-[#999] ml-2">
                      {s.ExecutiveOrg || ''}
                    </span>
                  </div>
                  <div className="flex items-center gap-3">
                    <span className="text-[#999]">{formatDate(s.Date)}</span>
                    <span
                      className={
                        s.Consequence === '合格'
                          ? 'text-[#16A34A]'
                          : 'text-[#666]'
                      }
                    >
                      {s.Consequence || '-'}
                    </span>
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>
    );
  };

  const renderQualifications = () => {
    const hasAny =
      (data?.Permissions?.length || 0) > 0 ||
      (data?.TaxCreditItems?.length || 0) > 0 ||
      (data?.Pledges?.length || 0) > 0 ||
      (data?.MPledges?.length || 0) > 0;

    if (!hasAny)
      return (
        <div className="text-center py-16 text-xs text-[#A3A3A3]">
          暂无资质信息
        </div>
      );

    return (
      <div className="space-y-3">
        {/* Permissions (行政许可) */}
        {data?.Permissions && data.Permissions.length > 0 && (
          <div className="rounded-xl border border-[#E8E8E8] bg-[#FAFAFA] p-5">
            <div className="text-sm font-semibold mb-3 flex items-center gap-1.5">
              <Award className="w-4 h-4" />
              行政许可 ({data.Permissions.length})
            </div>
            <div className="space-y-1.5">
              {data.Permissions.map((p, i) => (
                <div
                  key={i}
                  className="flex items-center justify-between text-xs py-1.5 border-b border-[#F5F5F5] last:border-0"
                >
                  <div className="flex-1 min-w-0">
                    <span className="text-[#333] font-medium">
                      {p.Name || '-'}
                    </span>
                    {p.Province && (
                      <span className="text-[#999] ml-2">({p.Province})</span>
                    )}
                  </div>
                  <div className="flex items-center gap-3 shrink-0">
                    <span className="text-[#999]">文号：{p.CaseNo || '-'}</span>
                    <span className="text-[#999]">
                      {formatDate(p.Liandate)}
                    </span>
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* TaxCredit */}
        {data?.TaxCreditItems && data.TaxCreditItems.length > 0 && (
          <div className="rounded-xl border border-[#E8E8E8] bg-[#FAFAFA] p-5">
            <div className="text-sm font-semibold mb-3">纳税信用等级</div>
            <div className="space-y-1.5">
              {data.TaxCreditItems.map((t, i) => (
                <div
                  key={i}
                  className="flex items-center justify-between text-xs py-1 border-b border-[#F5F5F5] last:border-0"
                >
                  <span className="text-[#333]">{t.TaxPayerName || '-'}</span>
                  <div className="flex items-center gap-4">
                    <span className="text-[#999]">{t.Year || '-'}年度</span>
                    <span
                      className={`font-medium ${t.Level === 'A' ? 'text-[#16A34A]' : 'text-[#333]'}`}
                    >
                      {t.Level || '-'}级
                    </span>
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Pledges (股权出质) */}
        {data?.Pledges && data.Pledges.length > 0 && (
          <div className="rounded-xl border border-[#E8E8E8] bg-[#FAFAFA] p-5">
            <div className="text-sm font-semibold mb-3">
              股权出质 ({data.Pledges.length})
            </div>
            <div className="space-y-2">
              {data.Pledges.map((p, i) => (
                <div
                  key={i}
                  className="border border-[#E8E8E8] rounded-lg p-3 bg-white"
                >
                  <div className="grid grid-cols-2 gap-x-4 gap-y-0.5 text-xs">
                    <div>
                      <span className="text-[#999]">出质人：</span>
                      {p.Pledgor || '-'}
                    </div>
                    <div>
                      <span className="text-[#999]">质权人：</span>
                      {p.Pledgee || '-'}
                    </div>
                    <div>
                      <span className="text-[#999]">出质金额：</span>
                      {p.PledgedAmount || '-'}
                    </div>
                    <div>
                      <span className="text-[#999]">状态：</span>
                      {p.Status || '-'}
                    </div>
                    <div>
                      <span className="text-[#999]">登记编号：</span>
                      {p.RegistNo || '-'}
                    </div>
                    <div>
                      <span className="text-[#999]">登记日期：</span>
                      {formatDate(p.RegDate)}
                    </div>
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* MPledges (动产抵押) */}
        {data?.MPledges && data.MPledges.length > 0 && (
          <div className="rounded-xl border border-[#E8E8E8] bg-[#FAFAFA] p-5">
            <div className="text-sm font-semibold mb-3">
              动产抵押 ({data.MPledges.length})
            </div>
            <div className="space-y-2">
              {data.MPledges.map((p, i) => (
                <div
                  key={i}
                  className="border border-[#E8E8E8] rounded-lg p-3 bg-white"
                >
                  <div className="grid grid-cols-2 gap-x-4 gap-y-0.5 text-xs">
                    <div>
                      <span className="text-[#999]">登记编号：</span>
                      {p.RegistNo || p.RegisterNo || '-'}
                    </div>
                    <div>
                      <span className="text-[#999]">状态：</span>
                      {p.Status || '-'}
                    </div>
                    <div>
                      <span className="text-[#999]">登记机关：</span>
                      {p.RegisterOffice || '-'}
                    </div>
                    <div>
                      <span className="text-[#999]">登记日期：</span>
                      {formatDate(p.RegDate || p.RegisterDate)}
                    </div>
                    <div className="col-span-2">
                      <span className="text-[#999]">担保债权数额：</span>
                      {p.DebtSecuredAmount || '-'}
                    </div>
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>
    );
  };

  return (
    <div className="flex-1 flex flex-col min-h-0 bg-white overflow-hidden">
      {/* Header bar */}
      <div className="shrink-0 px-6 py-3 border-b border-[#F0F0F0]">
        <div className="flex items-center gap-3">
          <span className="text-sm font-semibold text-[#000000] truncate">
            {base?.CompanyName || keyword}
          </span>
          <button
            onClick={handleBackToSearch}
            className="inline-flex items-center gap-1 h-8 px-4 text-xs font-medium bg-[#000000] hover:bg-[#171717] text-white rounded-lg transition-all shrink-0"
          >
            返回
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

      {/* Tabs */}
      <div className="shrink-0 px-6 border-b border-[#F0F0F0]">
        <div className="flex gap-0 overflow-x-auto">
          {TABS.map((tab) => (
            <button
              key={tab.key}
              onClick={() => setActiveTab(tab.key)}
              className={`flex items-center gap-1.5 px-4 py-2.5 text-xs font-medium transition-colors border-b-2 -mb-[1px] whitespace-nowrap ${
                activeTab === tab.key
                  ? 'text-[#000000] border-[#000000]'
                  : 'text-[#A3A3A3] border-transparent hover:text-[#525252]'
              }`}
            >
              {tab.icon}
              {tab.label}
              {tab.badge !== undefined && tab.badge > 0 && (
                <span
                  className={`ml-0.5 text-[10px] px-1.5 py-0.5 rounded-full ${
                    activeTab === tab.key
                      ? 'bg-[#000000] text-white'
                      : 'bg-[#F0F0F0] text-[#999]'
                  }`}
                >
                  {tab.badge}
                </span>
              )}
            </button>
          ))}
        </div>
      </div>

      {/* Content */}
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
          <>
            {activeTab === 'base' && renderBaseInfo()}
            {activeTab === 'stakeholders' && renderStakeholders()}
            {activeTab === 'changes' && renderChanges()}
            {activeTab === 'risk' && renderRisk()}
            {activeTab === 'qualifications' && renderQualifications()}
          </>
        ) : (
          <div className="text-center py-16 text-xs text-[#A3A3A3]">
            未查询到企业信息
          </div>
        )}
      </div>
    </div>
  );
}
