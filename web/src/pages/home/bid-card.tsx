import { getAuthorization } from '@/utils/authorization-util';
import { ExternalLink, Eye, Paperclip, Settings } from 'lucide-react';
import { useState } from 'react';
import type { BidProject } from './bid-list';

const NEWS_TYPE_MAP: Record<number, { label: string; color: string }> = {
  1: { label: '招标', color: 'text-[#2563EB] bg-[#EFF6FF]' },
  2: { label: '中标', color: 'text-[#16A34A] bg-[#F0FDF4]' },
  3: { label: '合同', color: 'text-[#A3A3A3] bg-[#F5F5F5]' },
};

function getNewsTypeBadge(id: number | null) {
  if (!id || !NEWS_TYPE_MAP[id]) return null;
  const { label, color } = NEWS_TYPE_MAP[id];
  return (
    <span
      className={`inline-flex items-center px-2 py-0.5 rounded-md text-xs font-medium ${color}`}
    >
      {label}
    </span>
  );
}

function fmtMoney(val: string | null | undefined): string {
  if (!val) return '';
  const n = Number(val);
  if (isNaN(n) || n <= 0) return '';
  if (n >= 10000) {
    return `${(n / 10000).toFixed(1)}万元`;
  }
  return `${n}元`;
}

function parseJsonArray(val: string | null): string[] {
  if (!val || val === '[]') return [];
  try {
    return JSON.parse(val);
  } catch {
    return [];
  }
}

function buildAreaName(
  provice_code: string,
  city_code: string,
  county_code: string,
  areaNameMap: Record<string, string>,
): string {
  const parts: string[] = [];
  if (provice_code && areaNameMap[provice_code])
    parts.push(areaNameMap[provice_code]);
  if (city_code && areaNameMap[city_code]) parts.push(areaNameMap[city_code]);
  if (county_code && areaNameMap[county_code])
    parts.push(areaNameMap[county_code]);
  return parts.join('/');
}

function buildIndustryName(
  industry_codes: string | null,
  industryNameMap: Record<string, string>,
): string {
  const codes = parseJsonArray(industry_codes);
  if (codes.length === 0) return '';
  return codes
    .map((c) => industryNameMap[c] || c)
    .filter(Boolean)
    .join(' / ');
}

interface BidCardProps {
  project: BidProject;
  areaNameMap: Record<string, string>;
  industryNameMap: Record<string, string>;
  onView: (project: BidProject) => void;
  onConfig: (project: BidProject) => void;
}

export function BidCard({
  project,
  areaNameMap,
  industryNameMap,
  onView,
  onConfig,
}: BidCardProps) {
  const [collectUrl, setCollectUrl] = useState<string | null>(null);
  const [urlLoading, setUrlLoading] = useState(false);

  const badge = getNewsTypeBadge(project.news_type_id);
  const money = fmtMoney(project.project_money);
  const area = buildAreaName(
    project.provice_code,
    project.city_code,
    project.county_code,
    areaNameMap,
  );
  const industry = buildIndustryName(project.industry_codes, industryNameMap);
  const pubDate = project.publish_time
    ? project.publish_time.substring(0, 10)
    : '';
  const partyA = parseJsonArray(project.part_a_names).join('、');
  const partyB = parseJsonArray(project.part_b_names).join('、');

  const handleCollectUrl = async (e: React.MouseEvent) => {
    e.stopPropagation();
    if (collectUrl) {
      window.open(collectUrl, '_blank', 'noopener,noreferrer');
      return;
    }
    setUrlLoading(true);
    try {
      const resp = await fetch(
        `/api/v1/bid/projects/${project.id}/collect-url?publish_time=${encodeURIComponent(project.publish_time || '')}`,
        { headers: { Authorization: getAuthorization() } },
      );
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const json = await resp.json();
      const url = json?.data?.url || '';
      if (url) {
        setCollectUrl(url);
        window.open(url, '_blank', 'noopener,noreferrer');
      }
    } catch {
      // silently ignore
    } finally {
      setUrlLoading(false);
    }
  };

  return (
    <div
      className="group bg-white rounded-xl border border-[#E8E8E8] p-5 transition-all duration-200 hover:shadow-[0_4px_20px_rgba(0,0,0,0.06)] hover:-translate-y-0.5 cursor-pointer"
      onClick={() => onView(project)}
    >
      {/* Top row: badges + date */}
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2">
          {badge}
          {project.has_file === 1 && (
            <span className="inline-flex items-center gap-1 text-xs text-[#525252]">
              <Paperclip className="size-3" />
              有附件
            </span>
          )}
        </div>
        <span className="text-xs text-[#A3A3A3]">{pubDate}</span>
      </div>

      {/* Title */}
      <h3 className="text-[15px] font-semibold text-[#000000] leading-snug mb-3 group-hover:text-[#2563EB] transition-colors line-clamp-2">
        {project.title}
      </h3>

      {/* Info row */}
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-[#525252] mb-4">
        {area && (
          <span className="inline-flex items-center gap-1">
            <span className="text-[#A3A3A3]">📍</span>
            {area}
          </span>
        )}
        {money && (
          <span className="inline-flex items-center gap-1 font-medium text-[#000000]">
            <span className="text-[#A3A3A3]">💰</span>
            预算 {money}
          </span>
        )}
        {industry && (
          <span className="inline-flex items-center gap-1">
            <span className="text-[#A3A3A3]">🏷️</span>
            {industry}
          </span>
        )}
      </div>

      {/* Party info */}
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-[#525252] mb-4">
        {partyA && (
          <span className="inline-flex items-center gap-1">
            <span className="text-[#A3A3A3]">🏢</span>
            甲方: {partyA}
          </span>
        )}
        {partyB && (
          <span className="inline-flex items-center gap-1">
            <span className="text-[#A3A3A3]">🤝</span>
            乙方: {partyB}
          </span>
        )}
      </div>

      {/* Actions */}
      <div className="flex items-center justify-between pt-3 border-t border-[#F0F0F0]">
        <button
          onClick={handleCollectUrl}
          disabled={urlLoading}
          className="inline-flex items-center gap-1 h-8 px-3 text-xs font-medium text-[#2563EB] hover:bg-[#EFF6FF] rounded-lg transition-colors disabled:opacity-50"
        >
          <ExternalLink className="size-3" />
          {urlLoading ? '获取中...' : '原文跳转'}
        </button>
        <div className="flex items-center gap-2">
          <button
            onClick={(e) => {
              e.stopPropagation();
              onView(project);
            }}
            className="inline-flex items-center gap-1 h-8 px-3 text-xs font-medium text-[#000000] hover:bg-[#F5F5F5] rounded-lg transition-colors"
          >
            <Eye className="size-3" />
            查看详情
          </button>
          <button
            onClick={(e) => {
              e.stopPropagation();
              onConfig(project);
            }}
            className="inline-flex items-center gap-1 h-8 px-3 text-xs font-medium text-[#525252] hover:text-[#000000] hover:bg-[#F5F5F5] rounded-lg transition-colors"
          >
            <Settings className="size-3" />
            配置
          </button>
        </div>
      </div>
    </div>
  );
}
