import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import {
  approveLeave,
  cancelLeave,
  fetchLeaveBalance,
  fetchMyLeaves,
  fetchPendingLeaves,
  submitLeave,
} from '@/services/hr-service';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { CalendarPlus, ClipboardCheck, ScrollText } from 'lucide-react';
import { useState } from 'react';
import type {
  LeaveRequest,
  LeaveStatus,
  LeaveStep,
  LeaveType,
} from './hr-types';

// 类型中文映射（全模块统一唯一来源）
const LEAVE_TYPE_LABEL: Record<LeaveType, string> = {
  personal: '事假',
  sick: '病假',
  annual: '年假',
  marriage: '婚假',
  maternity: '产假',
  business_trip: '出差',
  other: '其他',
  repair: '补卡',
};

// 新建表单下拉可选类型（repair 由考勤页补卡入口专用，不在此列）
const FORM_LEAVE_TYPES: LeaveType[] = [
  'personal',
  'sick',
  'annual',
  'marriage',
  'maternity',
  'business_trip',
  'other',
];

const STATUS_BADGE: Record<LeaveStatus, string> = {
  pending: 'bg-blue-50 text-blue-600 border-blue-200',
  approved: 'bg-emerald-50 text-emerald-600 border-emerald-200',
  rejected: 'bg-red-50 text-red-600 border-red-200',
  cancelled: 'bg-gray-100 text-gray-500 border-gray-200',
};

const STATUS_LABEL: Record<LeaveStatus, string> = {
  pending: '审批中',
  approved: '已通过',
  rejected: '已驳回',
  cancelled: '已撤销',
};

const STEP_STATUS_LABEL: Record<LeaveStep['status'], string> = {
  waiting: '等待中',
  pending: '待审批',
  approved: '已通过',
  rejected: '已驳回',
};

const STEP_STATUS_TEXT: Record<LeaveStep['status'], string> = {
  waiting: 'text-[#94A3B8]',
  pending: 'text-blue-600',
  approved: 'text-emerald-600',
  rejected: 'text-red-500',
};

// 有额度假型及其中文标签（余额卡片展示顺序）
const BALANCE_TYPES: { key: string; label: string }[] = [
  { key: 'annual', label: '年假' },
  { key: 'sick', label: '病假' },
  { key: 'marriage', label: '婚假' },
  { key: 'maternity', label: '产假' },
];

// 所有 mutation 成功后的统一缓存失效
function invalidateAll(qc: ReturnType<typeof useQueryClient>) {
  qc.invalidateQueries({ queryKey: ['hr-leaves-my'] });
  qc.invalidateQueries({ queryKey: ['hr-leaves-pending'] });
  qc.invalidateQueries({ queryKey: ['hr-leave-balance'] });
  qc.invalidateQueries({ queryKey: ['hr-calendar'] });
}

function todayStr() {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}

// ── 余额卡片行 ──

function BalanceCards() {
  const { data } = useQuery({
    queryKey: ['hr-leave-balance'],
    queryFn: () => fetchLeaveBalance(),
  });
  const rows = BALANCE_TYPES.map((bt) => ({
    ...bt,
    item: (data?.list ?? []).find((b) => b.leave_type === bt.key),
  })).filter((r) => r.item);
  if (rows.length === 0) return null;
  return (
    <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
      {rows.map(({ key, label, item }) => (
        <div
          key={key}
          className="rounded-xl border border-[#E2E8F0] bg-white p-4 shadow-sm"
        >
          <div className="text-xs text-[#94A3B8]">{label}</div>
          <div className="mt-1 text-sm font-medium text-[#0F172A]">
            总额度 {item?.total_days ?? 0} 天
          </div>
          <div className="mt-0.5 text-xs text-[#64748B]">
            已用 {item?.used_days ?? 0} · 审批中 {item?.frozen_days ?? 0}
          </div>
        </div>
      ))}
    </div>
  );
}

// ── 新建假单表单 ──

function NewLeaveForm({ onDone }: { onDone: () => void }) {
  const qc = useQueryClient();
  const [leaveType, setLeaveType] = useState<LeaveType>('personal');
  const [startDate, setStartDate] = useState(todayStr());
  const [endDate, setEndDate] = useState(todayStr());
  const [reason, setReason] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  const submit = async () => {
    if (busy) return;
    if (!startDate || !endDate) {
      setError('请选择开始与结束日期');
      return;
    }
    if (endDate < startDate) {
      setError('结束日期不能早于开始日期');
      return;
    }
    setBusy(true);
    setError('');
    try {
      await submitLeave({
        leave_type: leaveType,
        start_date: startDate,
        end_date: endDate,
        reason,
      });
      invalidateAll(qc);
      setReason('');
      onDone();
    } catch (e) {
      setError(e instanceof Error ? e.message : '提交失败');
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="rounded-xl border border-[#E2E8F0] bg-white p-5 shadow-sm">
      <div className="mb-3 text-sm font-medium text-[#0F172A]">新建假单</div>
      <div className="flex flex-wrap items-end gap-2">
        <div>
          <div className="mb-1 text-xs text-[#94A3B8]">类型</div>
          <select
            value={leaveType}
            onChange={(e) => setLeaveType(e.target.value as LeaveType)}
            className="h-9 rounded-md border border-[#E2E8F0] bg-white px-2 text-sm text-[#0F172A]"
          >
            {FORM_LEAVE_TYPES.map((t) => (
              <option key={t} value={t}>
                {LEAVE_TYPE_LABEL[t]}
              </option>
            ))}
          </select>
        </div>
        <div>
          <div className="mb-1 text-xs text-[#94A3B8]">开始日期</div>
          <Input
            type="date"
            value={startDate}
            onChange={(e) => setStartDate(e.target.value)}
            className="w-40"
          />
        </div>
        <div>
          <div className="mb-1 text-xs text-[#94A3B8]">结束日期</div>
          <Input
            type="date"
            value={endDate}
            onChange={(e) => setEndDate(e.target.value)}
            className="w-40"
          />
        </div>
        <div className="w-full">
          <div className="mb-1 text-xs text-[#94A3B8]">原因</div>
          <textarea
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            rows={2}
            maxLength={500}
            placeholder="请填写请假原因（选填，最多 500 字）"
            className="w-full rounded-md border border-[#E2E8F0] bg-white p-2 text-sm text-[#0F172A] placeholder:text-[#CBD5E1] focus:outline-none focus:ring-1 focus:ring-[#1a66fb]"
          />
        </div>
        <div className="flex gap-2">
          <Button
            onClick={submit}
            disabled={busy}
            className="bg-[#1a66fb] text-white hover:bg-[#1554d6]"
          >
            提交申请
          </Button>
          <Button variant="outline" onClick={onDone} disabled={busy}>
            取消
          </Button>
        </div>
      </div>
      {error && <div className="mt-2 text-sm text-red-500">{error}</div>}
    </div>
  );
}

// ── 审批进度（steps 内联展示）──

function StepsLine({ steps }: { steps?: LeaveStep[] }) {
  if (!steps || steps.length === 0) return null;
  return (
    <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 border-t border-[#F1F5F9] pt-2 text-xs">
      {steps.map((s) => (
        <span key={s.step_no} className="flex items-center gap-1">
          <span className="text-[#64748B]">
            {s.approver_name || s.approver_id}
          </span>
          <span className={STEP_STATUS_TEXT[s.status]}>
            {STEP_STATUS_LABEL[s.status]}
          </span>
          {s.comment && <span className="text-[#94A3B8]">（{s.comment}）</span>}
        </span>
      ))}
    </div>
  );
}

// ── 我的假单列表 ──

function MyLeaves() {
  const qc = useQueryClient();
  const { data } = useQuery({
    queryKey: ['hr-leaves-my'],
    queryFn: fetchMyLeaves,
  });
  const [busyId, setBusyId] = useState('');
  const [error, setError] = useState('');

  const doCancel = async (id: string) => {
    if (busyId) return;
    if (!window.confirm('确认撤销该假单？')) return;
    setBusyId(id);
    setError('');
    try {
      await cancelLeave(id);
      invalidateAll(qc);
    } catch (e) {
      setError(e instanceof Error ? e.message : '撤销失败');
    } finally {
      setBusyId('');
    }
  };

  const list = data?.list ?? [];
  return (
    <div className="rounded-xl border border-[#E2E8F0] bg-white p-5 shadow-sm">
      <div className="mb-3 flex items-center gap-2 text-sm font-medium text-[#0F172A]">
        <ScrollText className="size-4" /> 我的假单（{list.length}）
      </div>
      {error && <div className="mb-2 text-sm text-red-500">{error}</div>}
      {list.length === 0 ? (
        <div className="text-sm text-[#94A3B8]">暂无假单记录</div>
      ) : (
        <div className="space-y-2">
          {list.map((r) => (
            <div
              key={r.id}
              className="rounded-lg border border-[#F1F5F9] px-3 py-2"
            >
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div className="flex flex-wrap items-center gap-2 text-sm text-[#0F172A]">
                  <span className="font-medium">
                    {LEAVE_TYPE_LABEL[r.leave_type] || r.leave_type}
                  </span>
                  <span className="text-xs text-[#64748B]">
                    {r.start_date} ~ {r.end_date} · {r.duration_days} 天
                  </span>
                  <span
                    className={`rounded-full border px-2 py-0.5 text-xs ${STATUS_BADGE[r.status]}`}
                  >
                    {STATUS_LABEL[r.status]}
                  </span>
                </div>
                {r.status === 'pending' && (
                  <Button
                    variant="outline"
                    size="sm"
                    disabled={!!busyId}
                    onClick={() => doCancel(r.id)}
                    className="text-[#64748B]"
                  >
                    撤销
                  </Button>
                )}
              </div>
              {r.reason && (
                <div className="mt-1 text-xs text-[#94A3B8]">
                  原因：{r.reason}
                </div>
              )}
              {r.status === 'pending' && <StepsLine steps={r.steps} />}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ── 待我审批 ──

function PendingApprovals() {
  const qc = useQueryClient();
  const { data } = useQuery({
    queryKey: ['hr-leaves-pending'],
    queryFn: fetchPendingLeaves,
  });
  const [busyId, setBusyId] = useState('');
  const [error, setError] = useState('');
  const [comments, setComments] = useState<Record<string, string>>({});

  const doAct = async (id: string, action: 'approved' | 'rejected') => {
    if (busyId) return;
    const comment = (comments[id] || '').trim();
    if (action === 'rejected' && !comment) {
      setError('驳回时必须填写审批意见');
      return;
    }
    setBusyId(id);
    setError('');
    try {
      await approveLeave(id, action, comment);
      invalidateAll(qc);
      setComments((prev) => ({ ...prev, [id]: '' }));
    } catch (e) {
      setError(e instanceof Error ? e.message : '审批操作失败');
    } finally {
      setBusyId('');
    }
  };

  const list = data?.list ?? [];
  if (list.length === 0) return null;
  return (
    <div className="rounded-xl border border-[#E2E8F0] bg-white p-5 shadow-sm">
      <div className="mb-3 flex items-center gap-2 text-sm font-medium text-[#0F172A]">
        <ClipboardCheck className="size-4" /> 待我审批（{list.length}）
      </div>
      {error && <div className="mb-2 text-sm text-red-500">{error}</div>}
      <div className="space-y-2">
        {list.map((r: LeaveRequest) => (
          <div
            key={r.id}
            className="rounded-lg border border-[#F1F5F9] px-3 py-2"
          >
            <div className="flex flex-wrap items-center gap-2 text-sm text-[#0F172A]">
              <span className="font-medium">{r.nickname || '未知申请人'}</span>
              <span className="text-xs text-[#64748B]">
                {LEAVE_TYPE_LABEL[r.leave_type] || r.leave_type} ·{' '}
                {r.start_date} ~ {r.end_date} · {r.duration_days} 天
              </span>
            </div>
            {r.reason && (
              <div className="mt-1 text-xs text-[#94A3B8]">
                原因：{r.reason}
              </div>
            )}
            <div className="mt-2 flex flex-wrap items-center gap-2">
              <Input
                value={comments[r.id] || ''}
                onChange={(e) =>
                  setComments((prev) => ({ ...prev, [r.id]: e.target.value }))
                }
                placeholder="审批意见（驳回必填）"
                className="h-8 w-56 text-xs"
              />
              <Button
                size="sm"
                disabled={!!busyId}
                onClick={() => doAct(r.id, 'approved')}
                className="bg-[#1a66fb] text-white hover:bg-[#1554d6]"
              >
                通过
              </Button>
              <Button
                size="sm"
                variant="outline"
                disabled={!!busyId}
                onClick={() => doAct(r.id, 'rejected')}
                className="border-red-200 text-red-500 hover:bg-red-50"
              >
                驳回
              </Button>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

// ── 主视图 ──

export default function LeaveView() {
  const [showForm, setShowForm] = useState(false);
  return (
    <div className="mx-auto w-full max-w-3xl space-y-4 p-4">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2 text-sm text-[#475569]">
          <CalendarPlus className="size-4" /> 请假与出差申请
        </div>
        <Button
          onClick={() => setShowForm((v) => !v)}
          className="bg-[#1a66fb] text-white hover:bg-[#1554d6]"
        >
          {showForm ? '收起表单' : '新建假单'}
        </Button>
      </div>
      {showForm && <NewLeaveForm onDone={() => setShowForm(false)} />}
      <BalanceCards />
      <PendingApprovals />
      <MyLeaves />
    </div>
  );
}
