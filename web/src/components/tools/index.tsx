import { useState } from 'react';
import AgencyFeeCalculator from './agency-fee-calculator';
import CostConsultingCalculator from './cost-consulting-calculator';
import EngineeringSurveyFeeCalculator from './engineering-survey-fee-calculator';
import SupervisionFeeCalculator from './supervision-fee-calculator';

interface ToolItem {
  id: string;
  name: string;
  description: string;
  icon: string;
}

const tools: ToolItem[] = [
  {
    id: 'agency-fee',
    name: '代理费用计算',
    description: '按差额定率分档累进法计算招标代理服务费',
    icon: 'M15.75 15.75V18m-7.5-6.75h.008v.008H8.25v-.008zm0 2.25h.008v.008H8.25V16.5zm0 2.25h.008v.008H8.25v-.008zm0 2.25h.008v.008H8.25v-.008zm2.25-4.5h.008v.008H10.5v-.008zm0 2.25h.008v.008H10.5V16.5zm0 2.25h.008v.008H10.5v-.008zm2.25-4.5h.008v.008H12.75v-.008zm0 2.25h.008v.008H12.75V16.5zm2.25-4.5h.008v.008H15v-.008zm0 2.25h.008v.008H15V16.5z',
  },
  {
    id: 'cost-consulting',
    name: '造价咨询服务费',
    description: '建设工程造价咨询服务费（14类项目）',
    icon: 'M2.25 18.75a60.07 60.07 0 0115.797 2.101c.727.198 1.453-.342 1.453-1.096V18.75M3.75 4.5v.75A.75.75 0 013 6h-.75m0 0v-.375c0-.621.504-1.125 1.125-1.125H20.25M2.25 6v9m18-10.5v.75c0 .414.336.75.75.75h.75m-1.5-1.5h.375c.621 0 1.125.504 1.125 1.125v9.75c0 .621-.504 1.125-1.125 1.125h-.375m1.5-1.5H21a.75.75 0 00-.75.75v.75m0 0H3.75m0 0h-.375a1.125 1.125 0 01-1.125-1.125V15m1.5 1.5v-.75A.75.75 0 003 15h-.75M15 10.5a3 3 0 11-6 0 3 3 0 016 0zm3 0h.008v.008H18V10.5zm-12 0h.008v.008H6V10.5z',
  },
  {
    id: 'engineering-survey',
    name: '工程勘察设计费',
    description: '工程勘察设计收费标准（2002）10号',
    icon: 'M3.75 21h16.5M4.5 3h15M5.25 3v18m13.5-18v18M9 6.75h1.5m-1.5 3h1.5m-1.5 3h1.5m3-6H15m-1.5 3H15m-1.5 3H15M9 21v-3.375c0-.621.504-1.125 1.125-1.125h3.75c.621 0 1.125.504 1.125 1.125V21',
  },
  {
    id: 'supervision-fee',
    name: '建设工程监理费',
    description: '建设工程监理收费 发改价格[2007]670号',
    icon: 'M9 12.75L11.25 15 15 9.75m-3-7.036A11.959 11.959 0 013.598 6 11.99 11.99 0 003 9.749c0 5.592 3.824 10.29 9 11.623 5.176-1.332 9-6.03 9-11.622 0-1.31-.21-2.571-.598-3.751h-.152c-3.196 0-6.1-1.248-8.25-3.285z',
  },
];

export default function ToolsPanel() {
  const [selectedId, setSelectedId] = useState<string>(tools[0].id);

  const selectedTool = tools.find((t) => t.id === selectedId);

  return (
    <div className="flex-1 flex min-h-0 bg-[#f8f6f3]">
      {/* Left: Tool list */}
      <div className="w-56 shrink-0 border-r border-[rgba(124,92,252,0.06)] bg-white flex flex-col">
        <div className="px-4 pt-4 pb-2">
          <span className="text-[#9494b5] text-[10px] font-semibold tracking-widest uppercase">
            工具列表
          </span>
        </div>
        <div className="flex-1 overflow-y-auto px-2 space-y-0.5 pb-4">
          {tools.map((tool) => (
            <button
              key={tool.id}
              onClick={() => setSelectedId(tool.id)}
              className={`w-full flex items-center gap-2.5 px-3 py-2.5 rounded-xl cursor-pointer transition text-left group ${
                tool.id === selectedId
                  ? 'bg-[#ede9fe] text-[#7c5cfc]'
                  : 'text-[#5a5a7a] hover:bg-[#f4f1fb] hover:text-[#1c1c2e]'
              }`}
            >
              <div
                className={`w-8 h-8 rounded-xl flex items-center justify-center shrink-0 ${
                  tool.id === selectedId ? 'bg-white' : 'bg-[#f4f1fb]'
                }`}
              >
                <svg
                  className={`w-4 h-4 ${
                    tool.id === selectedId ? 'text-[#7c5cfc]' : 'text-[#9494b5]'
                  }`}
                  fill="none"
                  stroke="currentColor"
                  strokeWidth={1.5}
                  viewBox="0 0 24 24"
                >
                  <path
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    d={tool.icon}
                  />
                </svg>
              </div>
              <div className="min-w-0">
                <div className="text-sm font-medium truncate">{tool.name}</div>
                <div className="text-[11px] text-[#9494b5] truncate">
                  {tool.description}
                </div>
              </div>
            </button>
          ))}
        </div>
      </div>

      {/* Right: Tool content */}
      <div className="flex-1 overflow-y-auto bg-[#f8f6f3]">
        {selectedTool?.id === 'agency-fee' && <AgencyFeeCalculator />}
        {selectedTool?.id === 'cost-consulting' && <CostConsultingCalculator />}
        {selectedTool?.id === 'engineering-survey' && (
          <EngineeringSurveyFeeCalculator />
        )}
        {selectedTool?.id === 'supervision-fee' && <SupervisionFeeCalculator />}
      </div>
    </div>
  );
}
