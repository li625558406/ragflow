import { BidList } from '@/pages/home/bid-list';
import { ChevronLeft, ChevronRight } from 'lucide-react';
import { useState } from 'react';
import BidHome from './bid-home';
import CcgpSearch from './ccgp-search';
import ConstructionList from './construction-list';
import ContractList from './contract-list';
import CreditChinaSearch from './credit-china-search';
import EnterpriseSearch from './enterprise-search';
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
    icon: 'M3.75 6A2.25 2.25 0 016 3.75h2.25A2.25 2.25 0 0110.5 6v2.25a2.25 2.25 0 01-2.25 2.25H6a2.25 2.25 0 01-2.25-2.25V6zM3.75 15.75A2.25 2.25 0 016 13.5h2.25a2.25 2.25 0 012.25 2.25V18a2.25 2.25 0 01-2.25 2.25H6A2.25 2.25 0 013.75 18v-2.25zM13.5 6a2.25 2.25 0 012.25-2.25H18A2.25 2.25 0 0120.25 6v2.25A2.25 2.25 0 0118 10.5h-2.25a2.25 2.25 0 01-2.25-2.25V6zM13.5 15.75a2.25 2.25 0 012.25-2.25H18a2.25 2.25 0 012.25 2.25V18A2.25 2.25 0 0118 20.25h-2.25a2.25 2.25 0 01-2.25-2.25v-2.25z',
  },
  {
    id: 'bid-search',
    name: '标讯搜索',
    description: '搜索、浏览招投标标讯信息',
    icon: 'M21 21l-5.197-5.197m0 0A7.5 7.5 0 105.196 5.196a7.5 7.5 0 0010.607 10.607z',
  },
  {
    id: 'contracts',
    name: '中标/合同',
    description: '搜索中标结果和合同公告',
    icon: 'M9 12h3.75M9 15h3.75M9 18h3.75m3 .75H18a2.25 2.25 0 002.25-2.25V6.108c0-1.135-.845-2.098-1.976-2.192a48.424 48.424 0 00-1.123-.08m-5.801 0c-.065.21-.1.433-.1.664 0 .414.336.75.75.75h4.5a.75.75 0 00.75-.75 2.25 2.25 0 00-.1-.664m-5.8 0A2.251 2.251 0 0113.5 2.25H15a2.25 2.25 0 012.15 1.586m-5.8 0c-.376.023-.75.05-1.124.08C9.095 4.01 8.25 4.973 8.25 6.108V8.25m0 0H4.875c-.621 0-1.125.504-1.125 1.125v11.25c0 .621.504 1.125 1.125 1.125h9.75c.621 0 1.125-.504 1.125-1.125V9.375c0-.621-.504-1.125-1.125-1.125H8.25zM6.75 12h.008v.008H6.75V12zm0 3h.008v.008H6.75V15zm0 3h.008v.008H6.75V18z',
  },
  {
    id: 'enterprises',
    name: '企业查询',
    description: '查询企业画像与招投标关系',
    icon: 'M3.75 21h16.5M4.5 3h15M5.25 3v18m13.5-18v18M9 6.75h1.5m-1.5 3h1.5m-1.5 3h1.5m3-6H15m-1.5 3H15m-1.5 3H15M9 21v-3.375c0-.621.504-1.125 1.125-1.125h3.75c.621 0 1.125.504 1.125 1.125V21',
  },
  {
    id: 'construction',
    name: '拟在建项目',
    description: '搜索规划审批中的建设项目',
    icon: 'M11.42 15.17l-5.384-3.108A2.093 2.093 0 005.27 12H4.5a1.5 1.5 0 01-1.5-1.5V6a1.5 1.5 0 011.5-1.5h3.637a2.093 2.093 0 011.765.94l5.384 3.108M21 12a9 9 0 11-18 0 9 9 0 0118 0z',
  },
  {
    id: 'shixin-search',
    name: '中国执行信息公开网',
    description: '查询法院失信被执行人名单',
    icon: 'M12 9v3.75m9-.75a9 9 0 11-18 0 9 9 0 0018 0zm-9 3.75h.008v.008H12v-.008zM12 15h.008v.008H12V15zm0 0h.008v.008H12V15z',
  },
  {
    id: 'credit-china-search',
    name: '信用中国',
    description: '查询严重失信主体名单',
    icon: 'M12 3v17.25m0 0c-1.472 0-2.882.265-4.185.75M12 20.25c1.472 0 2.882.265 4.185.75M18.75 4.97A48.416 48.416 0 0012 4.5c-2.291 0-4.545.16-6.75.47m13.5 0c1.01.143 2.01.317 3 .52m-3-.52l2.62 10.726c.122.499-.106 1.028-.589 1.202a5.988 5.988 0 01-2.031.352 5.988 5.988 0 01-2.031-.352c-.483-.174-.711-.703-.59-1.202L18.75 4.971zm-13.5 0l-2.62 10.726c-.122.499.106 1.028.589 1.202a5.989 5.989 0 002.031.352 5.989 5.989 0 002.031-.352c.483-.174.711-.703.59-1.202L5.25 4.971z',
  },
  {
    id: 'ccgp-search',
    name: '政府采购违法失信',
    description: '查询政府采购严重违法失信记录',
    icon: 'M9 12.75L11.25 15 15 9.75m-3-7.036A11.959 11.959 0 013.598 6 11.99 11.99 0 003 9.749c0 5.592 3.824 10.29 9 11.623 5.176-1.332 9-6.03 9-11.622 0-1.31-.21-2.571-.598-3.751h-.152c-3.196 0-6.1-1.248-8.25-3.285z',
  },
];

export default function BidPanel() {
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
                <svg
                  className={`w-4 h-4 ${
                    mod.id === selectedId ? 'text-[#000000]' : 'text-[#333333]'
                  }`}
                  fill="none"
                  stroke="currentColor"
                  strokeWidth={1.5}
                  viewBox="0 0 24 24"
                >
                  <path
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    d={mod.icon}
                  />
                </svg>
              </div>
              <div className="min-w-0">
                <div className="text-[15px] font-medium truncate">
                  {mod.name}
                </div>
                <div className="text-[11px] text-[#333333] truncate">
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
      <div className="flex-1 overflow-y-auto bg-[#FFFFFF] min-w-0">
        {selectedModule?.id === 'bid-home' && <BidHome />}
        {selectedModule?.id === 'bid-search' && (
          <BidList setListLength={() => {}} />
        )}
        {selectedModule?.id === 'contracts' && <ContractList />}
        {selectedModule?.id === 'enterprises' && <EnterpriseSearch />}
        {selectedModule?.id === 'construction' && <ConstructionList />}
        {selectedModule?.id === 'shixin-search' && <ShixinSearch />}
        {selectedModule?.id === 'credit-china-search' && <CreditChinaSearch />}
        {selectedModule?.id === 'ccgp-search' && <CcgpSearch />}
      </div>
    </div>
  );
}
