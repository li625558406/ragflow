import DynamicIcon from '@/components/dynamic-icon';
import { ChevronLeft, ChevronRight } from 'lucide-react';
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
    icon: 'calculator',
  },
  {
    id: 'cost-consulting',
    name: '造价咨询服务费',
    description: '建设工程造价咨询服务费（14类项目）',
    icon: 'receipt',
  },
  {
    id: 'engineering-survey',
    name: '工程勘察设计费',
    description: '工程勘察设计收费标准（2002）10号',
    icon: 'compass',
  },
  {
    id: 'supervision-fee',
    name: '建设工程监理费',
    description: '建设工程监理收费 发改价格[2007]670号',
    icon: 'clipboard-check',
  },
];

export default function ToolsPanel() {
  const [selectedId, setSelectedId] = useState<string>(tools[0].id);
  const [collapsed, setCollapsed] = useState(false);

  const selectedTool = tools.find((t) => t.id === selectedId);

  return (
    <div className="flex-1 flex min-h-0 bg-[#FFFFFF]">
      {/* Left: Tool list */}
      <div
        className={`shrink-0 border-r border-[#D4D4D4] bg-white flex flex-col transition-[width] duration-300 ease-in-out overflow-hidden ${
          collapsed ? 'w-0 border-r-0' : 'w-56'
        }`}
      >
        <div className="px-4 pt-4 pb-2 whitespace-nowrap">
          <span className="text-[#333333] text-[15px] font-semibold tracking-widest uppercase">
            工具列表
          </span>
        </div>
        <div className="flex-1 overflow-y-auto px-2 space-y-0.5 pb-4">
          {tools.map((tool, idx) => (
            <button
              key={tool.id}
              onClick={() => setSelectedId(tool.id)}
              className={`cs-list-enter cs-list-d${Math.min(idx, 7)} w-full flex items-center gap-2.5 px-3 py-2.5 rounded-xl cursor-pointer transition text-left group whitespace-nowrap ${
                tool.id === selectedId
                  ? 'bg-[#EAEAEA] text-[#000000]'
                  : 'text-[#333333] hover:bg-[#EAEAEA] hover:text-[#000000]'
              }`}
            >
              <div
                className={`w-8 h-8 rounded-xl flex items-center justify-center shrink-0 ${
                  tool.id === selectedId ? 'bg-white' : 'bg-[#EAEAEA]'
                }`}
              >
                <DynamicIcon
                  name={tool.icon}
                  className={`w-4 h-4 ${
                    tool.id === selectedId ? 'text-[#000000]' : 'text-[#333333]'
                  }`}
                  strokeWidth={1.5}
                />
              </div>
              <div className="min-w-0">
                <div className="text-[15px] font-medium truncate">
                  {tool.name}
                </div>
                <div className="text-[11px] text-[#333333] truncate">
                  {tool.description}
                </div>
              </div>
            </button>
          ))}
        </div>
      </div>

      {/* Toggle button */}
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

      {/* Right: Tool content — keep all mounted to preserve input state across tab switches */}
      <div className="flex-1 overflow-y-auto bg-[#FFFFFF] min-w-0">
        <div className={selectedTool?.id === 'agency-fee' ? '' : 'hidden'}>
          <AgencyFeeCalculator />
        </div>
        <div className={selectedTool?.id === 'cost-consulting' ? '' : 'hidden'}>
          <CostConsultingCalculator />
        </div>
        <div
          className={selectedTool?.id === 'engineering-survey' ? '' : 'hidden'}
        >
          <EngineeringSurveyFeeCalculator />
        </div>
        <div className={selectedTool?.id === 'supervision-fee' ? '' : 'hidden'}>
          <SupervisionFeeCalculator />
        </div>
      </div>
    </div>
  );
}
