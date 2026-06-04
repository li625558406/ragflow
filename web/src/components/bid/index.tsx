import { BidList } from '@/pages/home/bid-list';
import { useState } from 'react';
import CreditChinaSearch from './credit-china-search';
import ShixinSearch from './shixin-search';

interface ModuleItem {
  id: string;
  name: string;
  description: string;
  icon: string;
}

const modules: ModuleItem[] = [
  {
    id: 'bid-search',
    name: '标讯搜索',
    description: '搜索、浏览招投标标讯信息',
    icon: 'M21 21l-5.197-5.197m0 0A7.5 7.5 0 105.196 5.196a7.5 7.5 0 0010.607 10.607z',
  },
  {
    id: 'shixin-search',
    name: '失信查询',
    description: '查询法院失信被执行人名单',
    icon: 'M12 9v3.75m9-.75a9 9 0 11-18 0 9 9 0 0118 0zm-9 3.75h.008v.008H12v-.008zM12 15h.008v.008H12V15zm0 0h.008v.008H12V15z',
  },
  {
    id: 'credit-china-search',
    name: '信用中国',
    description: '查询严重失信主体名单',
    icon: 'M12 3v17.25m0 0c-1.472 0-2.882.265-4.185.75M12 20.25c1.472 0 2.882.265 4.185.75M18.75 4.97A48.416 48.416 0 0012 4.5c-2.291 0-4.545.16-6.75.47m13.5 0c1.01.143 2.01.317 3 .52m-3-.52l2.62 10.726c.122.499-.106 1.028-.589 1.202a5.988 5.988 0 01-2.031.352 5.988 5.988 0 01-2.031-.352c-.483-.174-.711-.703-.59-1.202L18.75 4.971zm-13.5 0l-2.62 10.726c-.122.499.106 1.028.589 1.202a5.989 5.989 0 002.031.352 5.989 5.989 0 002.031-.352c.483-.174.711-.703.59-1.202L5.25 4.971z',
  },
];

export default function BidPanel() {
  const [selectedId, setSelectedId] = useState<string>(modules[0].id);

  const selectedModule = modules.find((m) => m.id === selectedId);

  return (
    <div className="flex-1 flex min-h-0 bg-[#FFFFFF]">
      {/* Left: Module list */}
      <div className="w-56 shrink-0 border-r border-[#D4D4D4] bg-white flex flex-col">
        <div className="px-4 pt-4 pb-2">
          <span className="text-[#333333] text-[15px] font-semibold tracking-widest uppercase">
            模块列表
          </span>
        </div>
        <div className="flex-1 overflow-y-auto px-2 space-y-0.5 pb-4">
          {modules.map((mod, idx) => (
            <button
              key={mod.id}
              onClick={() => setSelectedId(mod.id)}
              className={`cs-list-enter cs-list-d${Math.min(idx, 7)} w-full flex items-center gap-2.5 px-3 py-2.5 rounded-xl cursor-pointer transition text-left group ${
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

      {/* Right: Module content */}
      <div className="flex-1 overflow-y-auto bg-[#FFFFFF]">
        {selectedModule?.id === 'bid-search' && (
          <BidList setListLength={() => {}} />
        )}
        {selectedModule?.id === 'shixin-search' && <ShixinSearch />}
        {selectedModule?.id === 'credit-china-search' && <CreditChinaSearch />}
      </div>
    </div>
  );
}
