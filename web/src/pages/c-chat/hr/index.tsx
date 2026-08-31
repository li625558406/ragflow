import { useState } from 'react';
import AttendanceView from './attendance-view';

// P2 追加「请假」、P3 追加「薪资」、P4 追加「报表」子页签（见设计文档 §7 阶段拆分）
const SUB_TABS = [{ key: 'attendance', label: '考勤' }] as const;

type SubTabKey = (typeof SUB_TABS)[number]['key'];

export default function HrView() {
  const [sub, setSub] = useState<SubTabKey>('attendance');
  return (
    <div className="flex h-full flex-col">
      <div className="flex justify-center gap-1 border-b border-[#E2E8F0] bg-white px-4 py-2">
        {SUB_TABS.map((t) => (
          <button
            key={t.key}
            onClick={() => setSub(t.key)}
            className={`rounded-lg px-4 py-1.5 text-sm font-medium transition-colors ${
              sub === t.key
                ? 'bg-[#1a66fb] text-white'
                : 'text-[#475569] hover:bg-[#F1F5F9]'
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>
      <div className="min-h-0 flex-1 overflow-auto">
        {sub === 'attendance' && <AttendanceView />}
      </div>
    </div>
  );
}
