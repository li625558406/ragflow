import { fetchPendingLeaves } from '@/services/hr-service';
import { useQuery } from '@tanstack/react-query';
import { useState } from 'react';
import AttendanceView from './attendance-view';
import LeaveView from './leave-view';
import SalaryView from './salary-view';

// P2 追加「请假」、P3 追加「薪资」、P4 追加「报表」子页签（见设计文档 §7 阶段拆分）
const SUB_TABS = [
  { key: 'attendance', label: '考勤' },
  { key: 'leave', label: '请假' },
  { key: 'salary', label: '薪资' },
] as const;

type SubTabKey = (typeof SUB_TABS)[number]['key'];

export default function HrView() {
  const [sub, setSub] = useState<SubTabKey>('attendance');
  // 待我审批数轮询：与 leave-view 共用同一 queryKey，审批操作后自动同步
  const pending = useQuery({
    queryKey: ['hr-leaves-pending'],
    queryFn: fetchPendingLeaves,
    refetchInterval: 60_000,
  });
  const pendingTotal = pending.data?.total ?? 0;
  return (
    <div className="flex h-full flex-col">
      <div className="flex justify-center gap-1 border-b border-[#E2E8F0] bg-white px-4 py-2">
        {SUB_TABS.map((t) => (
          <button
            key={t.key}
            onClick={() => setSub(t.key)}
            className={`relative rounded-lg px-4 py-1.5 text-sm font-medium transition-colors ${
              sub === t.key
                ? 'bg-[#1a66fb] text-white'
                : 'text-[#475569] hover:bg-[#F1F5F9]'
            }`}
          >
            {t.label}
            {t.key === 'attendance' && pendingTotal > 0 && (
              <span className="absolute -right-1 -top-1 flex h-4 min-w-4 items-center justify-center rounded-full bg-red-500 px-1 text-[10px] font-semibold leading-none text-white">
                {pendingTotal > 99 ? '99+' : pendingTotal}
              </span>
            )}
          </button>
        ))}
      </div>
      <div className="min-h-0 flex-1 overflow-auto">
        {sub === 'attendance' && <AttendanceView />}
        {sub === 'leave' && <LeaveView />}
        {sub === 'salary' && <SalaryView />}
      </div>
    </div>
  );
}
