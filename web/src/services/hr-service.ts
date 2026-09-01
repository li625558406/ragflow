import type {
  CalendarDay,
  HrEmployeeProfile,
  HrRuleConfig,
  LeaveBalance,
  LeaveRequest,
  TodayPunch,
} from '@/pages/c-chat/hr/hr-types';

const BASE = '/api/v1';

function authHeaders(): HeadersInit {
  const token = localStorage.getItem('Authorization') || '';
  return { Authorization: token };
}

function getUserInfo(): { id?: string } {
  try {
    return JSON.parse(localStorage.getItem('userInfo') || '{}');
  } catch {
    return {};
  }
}

async function apiFetch<T>(path: string, init: RequestInit = {}): Promise<T> {
  const uid = getUserInfo().id || '';
  const url = `${BASE}${path}${path.includes('?') ? '&' : '?'}user_id=${encodeURIComponent(uid)}`;
  const resp = await fetch(url, {
    ...init,
    headers: {
      'Content-Type': 'application/json',
      ...authHeaders(),
      ...(init.headers || {}),
    },
  });
  if (resp.status === 401) {
    localStorage.removeItem('Authorization');
    localStorage.removeItem('userInfo');
    window.location.href = '/login';
    throw new Error('unauthorized');
  }
  if (!resp.ok) throw new Error(`hr api ${resp.status}`);
  const body = await resp.json();
  if (body && typeof body === 'object' && 'code' in body) {
    if (body.code !== 0)
      throw new Error(body.message || `hr api code ${body.code}`);
    return body.data as T;
  }
  return body as T;
}

// ── 员工自助 ──

export async function fetchMyProfile(): Promise<{
  profile: HrEmployeeProfile | null;
}> {
  return apiFetch('/hr/employee/me');
}

export async function punch(
  action: 'in' | 'out',
): Promise<{ punch_time: string; today: TodayPunch }> {
  return apiFetch('/hr/attendance/punch', {
    method: 'POST',
    body: JSON.stringify({ action }),
  });
}

export async function fetchToday(): Promise<TodayPunch> {
  return apiFetch('/hr/attendance/today');
}

export async function fetchCalendar(
  month: string,
): Promise<{ month: string; days: CalendarDay[] }> {
  return apiFetch(`/hr/attendance/calendar?month=${encodeURIComponent(month)}`);
}

// ── HR 管理 ──

export async function listEmployees(
  params: { keyword?: string; department?: string } = {},
) {
  const qs = new URLSearchParams(
    Object.entries(params).filter(([, v]) => v) as [string, string][],
  ).toString();
  return apiFetch<{ list: HrEmployeeProfile[]; total: number }>(
    `/hr/employee${qs ? `?${qs}` : ''}`,
  );
}

export async function createEmployee(data: {
  user_id: string;
  emp_no: string;
  department?: string;
  position?: string;
  entry_date?: string;
}): Promise<HrEmployeeProfile> {
  return apiFetch('/hr/employee', {
    method: 'POST',
    body: JSON.stringify(data),
  });
}

export async function repairPunch(data: {
  employee_id: string;
  punch_time: string;
  reason: string;
}) {
  return apiFetch('/hr/attendance/repair', {
    method: 'POST',
    body: JSON.stringify(data),
  });
}

export async function fetchDayList(month: string, date?: string) {
  const qs = new URLSearchParams({
    month,
    ...(date ? { date } : {}),
  }).toString();
  return apiFetch<{ list: Record<string, unknown>[]; total: number }>(
    `/hr/attendance/day-list?${qs}`,
  );
}

export async function monthClose(
  month: string,
): Promise<{ employees: number; days: number }> {
  return apiFetch('/hr/attendance/month-close', {
    method: 'POST',
    body: JSON.stringify({ month }),
  });
}

export async function fetchMonthSummary(month: string) {
  return apiFetch<{ list: Record<string, unknown>[] }>(
    `/hr/attendance/month/${encodeURIComponent(month)}`,
  );
}

export async function fetchRuleConfig(): Promise<HrRuleConfig> {
  return apiFetch('/hr/rule-config');
}

export async function saveRuleConfig(
  data: Partial<HrRuleConfig>,
): Promise<HrRuleConfig> {
  return apiFetch('/hr/rule-config', {
    method: 'PUT',
    body: JSON.stringify(data),
  });
}

// ── P2: 假单 ──

export async function submitLeave(data: {
  leave_type: string;
  start_date: string;
  end_date: string;
  reason: string;
}) {
  return apiFetch('/hr/leave', { method: 'POST', body: JSON.stringify(data) });
}

export async function fetchMyLeaves() {
  return apiFetch<{ list: LeaveRequest[]; total: number }>('/hr/leave/my');
}

export async function fetchPendingLeaves() {
  return apiFetch<{ list: LeaveRequest[]; total: number }>('/hr/leave/pending');
}

export async function approveLeave(
  id: string,
  action: 'approved' | 'rejected',
  comment = '',
) {
  return apiFetch(`/hr/leave/${id}/approve`, {
    method: 'POST',
    body: JSON.stringify({ action, comment }),
  });
}

export async function cancelLeave(id: string) {
  return apiFetch(`/hr/leave/${id}/cancel`, { method: 'POST' });
}

export async function fetchLeaveBalance(year?: number) {
  const q = year ? `?year=${year}` : '';
  return apiFetch<{ year: number; list: LeaveBalance[] }>(
    `/hr/leave/balance${q}`,
  );
}

export async function updateLeaveBalance(data: {
  employee_id: string;
  leave_type: string;
  year: number;
  total_days: number;
}) {
  return apiFetch('/hr/leave/balance', {
    method: 'PUT',
    body: JSON.stringify(data),
  });
}
