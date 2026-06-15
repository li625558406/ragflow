import DynamicIcon from '@/components/dynamic-icon';
import { BidList } from '@/pages/home/bid-list';
import { ChevronLeft, ChevronRight } from 'lucide-react';
import { useState } from 'react';
import BidHome from './bid-home';
import CcgpSearch from './ccgp-search';
import ConstructionList from './construction-list';
import ContractList from './contract-list';
import CrawlerMonitor from './crawler-monitor';
import CreditChinaSearch from './credit-china-search';
import EnterpriseSearch from './enterprise-search';
import KbSearch from './kb-search';
import ShixinSearch from './shixin-search';

interface ModuleItem {
  id: string;
  name: string;
  description: string;
  icon: string;
}

const modules: ModuleItem[] = [
  {
    id: 'bid-home',
    name: '首页',
    description: '快速导航常用招投标网站',
    icon: 'layout-dashboard',
  },
  {
    id: 'bid-search',
    name: '标讯搜索',
    description: '搜索、浏览招投标标讯信息',
    icon: 'search',
  },
  {
    id: 'kb-search',
    name: '知识库搜索',
    description: '搜索所有知识库中的文档内容',
    icon: 'book-open',
  },
  {
    id: 'contracts',
    name: '中标/合同',
    description: '搜索中标结果和合同公告',
    icon: 'file-check-2',
  },
  {
    id: 'enterprises',
    name: '企业查询',
    description: '查询企业画像与招投标关系',
    icon: 'building-2',
  },
  {
    id: 'construction',
    name: '拟在建项目',
    description: '搜索规划审批中的建设项目',
    icon: 'hammer',
  },
  {
    id: 'shixin-search',
    name: '中国执行信息公开网',
    description: '查询法院失信被执行人名单',
    icon: 'scale',
  },
  {
    id: 'credit-china-search',
    name: '信用中国',
    description: '查询严重失信主体名单',
    icon: 'badge-check',
  },
  {
    id: 'ccgp-search',
    name: '政府采购违法失信',
    description: '查询政府采购严重违法失信记录',
    icon: 'alert-triangle',
  },
  {
    id: 'crawler-monitor',
    name: '爬虫监控',
    description: '实时监控爬虫运行状态与数据采集',
    icon: 'activity',
  },
];

interface Props {
  apiFetch?: (url: string, options?: RequestInit) => Promise<Response>;
}

export default function BidPanel({ apiFetch }: Props) {
  const [selectedId, setSelectedId] = useState<string>('bid-home');
  const [collapsed, setCollapsed] = useState(false);

  const selectedModule = modules.find((m) => m.id === selectedId);

  return (
    <div className="flex-1 flex min-h-0 bg-[#FFFFFF]">
      {/* Left: Module list */}
      <div
        className={`shrink-0 border-r border-[#D4D4D4] bg-white flex flex-col transition-[width] duration-300 ease-in-out overflow-hidden ${
          collapsed ? 'w-0 border-r-0' : 'w-56'
        }`}
      >
        <div className="px-4 pt-4 pb-2 whitespace-nowrap">
          <span className="text-[#333333] text-[15px] font-semibold tracking-widest uppercase">
            模块列表
          </span>
        </div>
        <div className="flex-1 overflow-y-auto px-2 space-y-0.5 pb-4">
          {modules.map((mod, idx) => (
            <button
              key={mod.id}
              onClick={() => setSelectedId(mod.id)}
              className={`cs-list-enter cs-list-d${Math.min(idx, 7)} w-full flex items-center gap-2.5 px-3 py-2.5 rounded-xl cursor-pointer transition text-left group whitespace-nowrap ${
                mod.id === selectedId
                  ? 'bg-[#EAEAEA] text-[#000000]'
                  : 'text-[#333333] hover:bg-[#EAEAEA] hover:text-[#000000]'
              }`}
            >
              <div
                className={`w-8 h-8 rounded-xl flex items-center justify-center shrink-0 ${
                  mod.id === selectedId ? 'bg-white' : 'bg-[#EAEAEA]'
                }`}
              >
                <DynamicIcon
                  name={mod.icon}
                  className={`w-4 h-4 ${
                    mod.id === selectedId ? 'text-[#000000]' : 'text-[#333333]'
                  }`}
                  strokeWidth={1.5}
                />
              </div>
              <div className="min-w-0">
                <div className="text-[15px] font-medium truncate">
                  {mod.name}
                </div>
                <div className="text-[11px] text-[#A3A3A3] truncate">
                  {mod.description}
                </div>
              </div>
            </button>
          ))}
        </div>
      </div>

      {/* Toggle button — floats on sidebar edge */}
      <button
        onClick={() => setCollapsed((c) => !c)}
        className="shrink-0 self-start mt-6 -ml-3.5 z-10 size-7 flex items-center justify-center rounded-full border-2 border-[#D4D4D4] bg-white text-[#525252] hover:text-[#000000] hover:border-[#A3A3A3] hover:shadow-[0_2px_8px_rgba(0,0,0,0.12)] transition-all cursor-pointer"
        title={collapsed ? '展开侧边栏' : '收起侧边栏'}
      >
        {collapsed ? (
          <ChevronRight className="size-3.5" />
        ) : (
          <ChevronLeft className="size-3.5" />
        )}
      </button>

      {/* Right: Module content */}
      <div className="flex-1 flex flex-col min-h-0 overflow-hidden bg-[#FFFFFF] min-w-0 relative">
        {selectedModule?.id === 'bid-home' && <BidHome />}
        {selectedModule?.id === 'bid-search' && (
          <BidList setListLength={() => {}} />
        )}
        {selectedModule?.id === 'kb-search' && apiFetch && (
          <KbSearch apiFetch={apiFetch} />
        )}
        {selectedModule?.id === 'contracts' && <ContractList />}
        {selectedModule?.id === 'enterprises' && <EnterpriseSearch />}
        {selectedModule?.id === 'construction' && <ConstructionList />}
        {selectedModule?.id === 'shixin-search' && <ShixinSearch />}
        {selectedModule?.id === 'credit-china-search' && <CreditChinaSearch />}
        {selectedModule?.id === 'ccgp-search' && <CcgpSearch />}
        {selectedModule?.id === 'crawler-monitor' && <CrawlerMonitor />}
      </div>
    </div>
  );
}
