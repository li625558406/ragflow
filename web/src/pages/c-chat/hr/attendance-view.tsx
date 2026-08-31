import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { usePermission } from '@/hooks/use-permission';
import {
  createEmployee,
  fetchCalendar,
  fetchDayList,
  fetchMonthSummary,
  fetchMyProfile,
  fetchToday,
  listEmployees,
  monthClose,
  punch,
  repairPunch,
} from '@/services/hr-service';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { CalendarDays, Clock, LogIn, LogOut, Wrench } from 'lucide-react';
import { useMemo, useState } from 'react';
import type { CalendarDay, DayStatus, TodayPunch } from './hr-types';

const STATUS_STYLE: Record<DayStatus, { bg: string; label: string }> = {
  normal: { bg: 'bg-emerald-500/80 text-white', label: '正常' },
  late: { bg: 'bg-amber-500/80 text-white', label: '迟到' },
  absent: { bg: 'bg-red-500/80 text-white', label: '旷工' },
  leave: { bg: 'bg-blue-500/80 text-white', label: '请假' },
  business_trip: { bg: 'bg-purple-500/80 text-white', label: '出差' },
  rest: { bg: 'bg-slate-300/60 text-slate-600', label: '休' },
  missing: { bg: 'bg-gray-400/70 text-white', label: '缺卡' },
  abnormal: { bg: 'bg-orange-600/80 text-white', label: '异常' },
  future: { bg: 'bg-transparent text-[#94A3B8]', label: '' },
};

function fmtTime(iso: string) {
  return iso ? iso.slice(11, 16) : '--:--';
}

function todayMonth() {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`;
}

// ── 员工打卡卡片 ──

function PunchCard({
  today,
  onPunching,
}: {
  today: TodayPunch;
  onPunching: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const doPunch = async (action: 'in' | 'out') => {
    setBusy(true);
    setError('');
    try {
      // 后端按记录顺序自动交替上/下班卡，body 里的 action 仅作语义提示；真正控制是下面的按钮禁用逻辑
      await punch(action);
      onPunching();
    } catch (e) {
      setError(e instanceof Error ? e.message : '打卡失败');
    } finally {
      setBusy(false);
    }
  };
  const st = STATUS_STYLE[today.status] ?? STATUS_STYLE.missing;
  return (
    <div className="rounded-xl border border-[#E2E8F0] bg-white p-5 shadow-sm">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2 text-sm text-[#475569]">
          <Clock className="size-4" />
          今日 · {today.work_date}
        </div>
        <span
          className={`rounded-full px-3 py-0.5 text-xs font-medium ${st.bg}`}
        >
          {st.label || '—'}
        </span>
      </div>
      <div className="mt-4 flex items-center gap-8">
        <div>
          <div className="text-xs text-[#94A3B8]">签到</div>
          <div className="text-2xl font-semibold text-[#0F172A]">
            {fmtTime(today.first_in)}
          </div>
        </div>
        <div>
          <div className="text-xs text-[#94A3B8]">签退</div>
          <div className="text-2xl font-semibold text-[#0F172A]">
            {fmtTime(today.last_out)}
          </div>
        </div>
        {today.late_minutes > 0 && (
          <div className="text-sm text-amber-600">
            迟到 {today.late_minutes} 分钟
          </div>
        )}
      </div>
      <div className="mt-4 flex gap-3">
        <Button
          disabled={busy || !!today.first_in}
          onClick={() => doPunch('in')}
          className="flex-1 gap-2 bg-[#1a66fb] text-white hover:bg-[#1554d6]"
        >
          <LogIn className="size-4" /> 签到
        </Button>
        <Button
          disabled={busy || !today.first_in || !!today.last_out}
          onClick={() => doPunch('out')}
          className="flex-1 gap-2 bg-[#1a66fb] text-white hover:bg-[#1554d6]"
        >
          <LogOut className="size-4" /> 签退
        </Button>
      </div>
      {error && <div className="mt-2 text-sm text-red-500">{error}</div>}
      {today.records.length > 0 && (
        <div className="mt-3 space-y-1 border-t border-[#F1F5F9] pt-3">
          {today.records.map((r, i) => (
            <div
              key={i}
              className="flex justify-between text-xs text-[#94A3B8]"
            >
              <span>
                {r.punch_time.slice(11, 19)} · {r.source}
              </span>
              <span>IP {r.ip_address || '—'}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ── 员工考勤日历 ──

function AttendanceCalendar({ month }: { month: string }) {
  const { data } = useQuery({
    queryKey: ['hr-calendar', month],
    queryFn: () => fetchCalendar(month),
  });
  const byDate = useMemo(() => {
    const m = new Map<string, CalendarDay>();
    (data?.days ?? []).forEach((d) => m.set(d.work_date, d));
    return m;
  }, [data]);
  const [y, mo] = month.split('-').map(Number);
  const firstWeekday = new Date(y, mo - 1, 1).getDay();
  const daysInMonth = new Date(y, mo, 0).getDate();
  const cells: (CalendarDay | null)[] = [
    ...Array.from({ length: (firstWeekday + 6) % 7 }, () => null),
    ...Array.from({ length: daysInMonth }, (_, i) => ({
      work_date: `${month}-${String(i + 1).padStart(2, '0')}`,
      status: 'future' as DayStatus,
      first_in: '',
      last_out: '',
      late_minutes: 0,
      locked: false,
    })),
  ];
  return (
    <div className="rounded-xl border border-[#E2E8F0] bg-white p-5 shadow-sm">
      <div className="mb-3 flex items-center gap-2 text-sm font-medium text-[#0F172A]">
        <CalendarDays className="size-4" /> 考勤日历（{month}）
      </div>
      <div className="grid grid-cols-7 gap-1.5 text-center text-xs text-[#94A3B8]">
        {['一', '二', '三', '四', '五', '六', '日'].map((w) => (
          <div key={w} className="py-1">
            {w}
          </div>
        ))}
        {cells.map((c, i) => {
          if (!c) return <div key={`empty-${i}`} />;
          const day = byDate.get(c.work_date);
          const st =
            STATUS_STYLE[day?.status ?? 'future'] ?? STATUS_STYLE.future;
          return (
            <div
              key={c.work_date}
              title={`${day?.status ?? ''} ${fmtTime(day?.first_in ?? '')}-${fmtTime(day?.last_out ?? '')}`}
              className={`flex h-12 flex-col items-center justify-center rounded-lg text-xs ${st.bg}`}
            >
              <span className="font-medium">
                {Number(c.work_date.slice(8))}
              </span>
              {st.label && (
                <span className="text-[10px] leading-3">{st.label}</span>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

// ── HR 管理面板 ──

function HrAdminPanel({
  month,
  onRefresh,
}: {
  month: string;
  onRefresh: () => void;
}) {
  const qc = useQueryClient();
  const [keyword, setKeyword] = useState('');
  const [newEmp, setNewEmp] = useState({
    user_id: '',
    emp_no: '',
    department: '',
    entry_date: '',
  });
  const [msg, setMsg] = useState('');
  const employees = useQuery({
    queryKey: ['hr-employees', keyword],
    queryFn: () => listEmployees({ keyword }),
  });
  const dayList = useQuery({
    queryKey: ['hr-daylist', month],
    queryFn: () => fetchDayList(month),
  });
  const summary = useQuery({
    queryKey: ['hr-month-summary', month],
    queryFn: () => fetchMonthSummary(month),
  });

  const addEmployee = async () => {
    setMsg('');
    try {
      await createEmployee(newEmp);
      setMsg('建档成功');
      setNewEmp({ user_id: '', emp_no: '', department: '', entry_date: '' });
      qc.invalidateQueries({ queryKey: ['hr-employees'] });
    } catch (e) {
      setMsg(e instanceof Error ? e.message : '建档失败');
    }
  };

  const doMonthClose = async () => {
    setMsg('');
    try {
      const s = await monthClose(month);
      setMsg(`汇总完成：${s.employees} 名员工 / ${s.days} 天`);
      qc.invalidateQueries({ queryKey: ['hr-month-summary', month] });
      qc.invalidateQueries({ queryKey: ['hr-daylist', month] });
      onRefresh();
    } catch (e) {
      setMsg(e instanceof Error ? e.message : '汇总失败');
    }
  };

  const doRepair = async (employeeId: string) => {
    const pt = window.prompt('补卡时间（YYYY-MM-DD HH:MM:SS）：');
    if (!pt) return;
    const reason = window.prompt('补卡原因：') || '';
    try {
      await repairPunch({ employee_id: employeeId, punch_time: pt, reason });
      setMsg('补卡成功');
      qc.invalidateQueries({ queryKey: ['hr-daylist', month] });
    } catch (e) {
      setMsg(e instanceof Error ? e.message : '补卡失败');
    }
  };

  return (
    <div className="rounded-xl border border-[#E2E8F0] bg-white p-5 shadow-sm">
      <div className="mb-3 flex items-center justify-between">
        <div className="flex items-center gap-2 text-sm font-medium text-[#0F172A]">
          <Wrench className="size-4" /> HR 管理（{month}）
        </div>
        <Button
          onClick={doMonthClose}
          className="bg-[#1a66fb] text-white hover:bg-[#1554d6]"
        >
          一键月度汇总
        </Button>
      </div>
      {msg && <div className="mb-2 text-sm text-[#1a66fb]">{msg}</div>}

      <div className="mb-3 flex flex-wrap items-end gap-2">
        <div>
          <div className="text-xs text-[#94A3B8]">搜索（工号/部门/职位）</div>
          <Input
            value={keyword}
            onChange={(e) => setKeyword(e.target.value)}
            className="w-56"
          />
        </div>
        <div className="text-xs text-[#94A3B8]">
          <div>建档：user_id / 工号 / 部门 / 入职日期</div>
          <div className="flex gap-1">
            <Input
              value={newEmp.user_id}
              placeholder="user_id"
              onChange={(e) =>
                setNewEmp({ ...newEmp, user_id: e.target.value })
              }
              className="w-40"
            />
            <Input
              value={newEmp.emp_no}
              placeholder="工号"
              onChange={(e) => setNewEmp({ ...newEmp, emp_no: e.target.value })}
              className="w-24"
            />
            <Input
              value={newEmp.department}
              placeholder="部门"
              onChange={(e) =>
                setNewEmp({ ...newEmp, department: e.target.value })
              }
              className="w-24"
            />
            <Input
              value={newEmp.entry_date}
              placeholder="2026-09-01"
              onChange={(e) =>
                setNewEmp({ ...newEmp, entry_date: e.target.value })
              }
              className="w-32"
            />
            <Button
              onClick={addEmployee}
              className="bg-[#1a66fb] text-white hover:bg-[#1554d6]"
            >
              建档
            </Button>
          </div>
        </div>
      </div>

      <div className="text-xs text-[#94A3B8]">
        员工档案（{employees.data?.total ?? 0}）
      </div>
      <div className="mb-3 max-h-40 overflow-auto rounded-lg border border-[#F1F5F9]">
        {(employees.data?.list ?? []).map((e) => (
          <div
            key={e.id}
            className="flex items-center justify-between border-b border-[#F8FAFC] px-3 py-1.5 text-xs"
          >
            <span>
              {e.emp_no} · {e.department || '—'} · {e.position || '—'} ·{' '}
              {e.status}
            </span>
            <button
              onClick={() => doRepair(e.id)}
              className="text-[#1a66fb] hover:underline"
            >
              补卡
            </button>
          </div>
        ))}
      </div>

      <div className="text-xs text-[#94A3B8]">
        月度汇总（{summary.data?.list.length ?? 0} 人）
      </div>
      <div className="overflow-auto rounded-lg border border-[#F1F5F9]">
        <table className="w-full text-xs">
          <thead className="bg-[#F8FAFC] text-[#64748B]">
            <tr>
              <th className="px-2 py-1.5 text-left">工号</th>
              <th className="px-2 py-1.5">出勤</th>
              <th className="px-2 py-1.5">迟到</th>
              <th className="px-2 py-1.5">旷工</th>
              <th className="px-2 py-1.5">缺卡</th>
              <th className="px-2 py-1.5">状态</th>
            </tr>
          </thead>
          <tbody>
            {(summary.data?.list ?? []).map((r) => (
              <tr
                key={String(r.employee_id)}
                className="border-t border-[#F8FAFC] text-center"
              >
                <td className="px-2 py-1.5 text-left">{String(r.emp_no)}</td>
                <td>{String(r.attend_days)}</td>
                <td>
                  {String(r.late_count)}次/{String(r.late_minutes)}分
                </td>
                <td>{String(r.absent_days)}</td>
                <td>{String(r.missing_days)}</td>
                <td>{r.status === 'confirmed' ? '已归档' : '草稿'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {dayList.data && (
        <div className="mt-1 text-[10px] text-[#CBD5E1]">
          日汇总明细共 {dayList.data.total} 条
        </div>
      )}
    </div>
  );
}

// ── 主视图 ──

export default function AttendanceView() {
  const { hasPermission } = usePermission();
  const isHr = hasPermission('hr_manage');
  const [month, setMonth] = useState(todayMonth());
  const profile = useQuery({
    queryKey: ['hr-my-profile'],
    queryFn: fetchMyProfile,
  });
  const today = useQuery({
    queryKey: ['hr-today'],
    queryFn: fetchToday,
    enabled: !!profile.data?.profile,
  });
  const refreshAll = () => {
    today.refetch();
  };

  if (profile.isLoading) {
    return <div className="p-6 text-sm text-[#94A3B8]">加载中…</div>;
  }
  if (!profile.data?.profile) {
    return (
      <div className="p-6 text-sm text-[#94A3B8]">
        未开通人事功能：请联系 HR 在「人事管理」中为你建档。
      </div>
    );
  }
  if (!today.data) {
    return <div className="p-6 text-sm text-[#94A3B8]">加载考勤数据…</div>;
  }

  return (
    <div className="mx-auto w-full max-w-3xl space-y-4 p-4">
      <PunchCard today={today.data!} onPunching={refreshAll} />
      <AttendanceCalendar month={month} />
      <div className="flex items-center gap-2 text-xs text-[#94A3B8]">
        <span>月份</span>
        <Input
          type="month"
          value={month}
          onChange={(e) => setMonth(e.target.value)}
          className="w-40"
        />
      </div>
      {isHr && <HrAdminPanel month={month} onRefresh={refreshAll} />}
    </div>
  );
}
