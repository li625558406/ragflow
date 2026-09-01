import type {
  ArchiveRow,
  BatchImportResult,
  CalendarDay,
  HrEmployeeProfile,
  HrRuleConfig,
  LeaveBalance,
  LeaveRequest,
  Payslip,
  PayslipAdjust,
  SalaryFailedItem,
  SalaryProfile,
  SalaryTrialItem,
  TodayPunch,
  Voucher,
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

// ── P3: 薪资 ──

export async function listSalaryProfiles(keyword?: string) {
  const q = keyword ? `?keyword=${encodeURIComponent(keyword)}` : '';
  return apiFetch<{ list: SalaryProfile[]; total: number }>(
    `/hr/salary-profile${q}`,
  );
}

export async function upsertSalaryProfile(data: {
  employee_id: string;
  base_salary: number;
  post_allowance: number;
  meal_allowance: number;
  transport_allowance: number;
  social_base: number;
  fund_base: number;
  special_deduction: number;
  social_rate: number | null;
  fund_rate: number | null;
  manual_overrides?: Record<string, number>;
}): Promise<SalaryProfile> {
  return apiFetch('/hr/salary-profile', {
    method: 'PUT',
    body: JSON.stringify(data),
  });
}

export async function trialSalary(month: string, employeeId?: string) {
  // 后端契约：失败员工内嵌在 list（ok=false + reason），无独立 failed 数组
  return apiFetch<{
    month: string;
    list: SalaryTrialItem[];
    total: number;
  }>('/hr/salary/trial', {
    method: 'POST',
    body: JSON.stringify({
      month,
      ...(employeeId ? { employee_id: employeeId } : {}),
    }),
  });
}

export async function calcSalary(month: string) {
  return apiFetch<{ ok: number; failed: SalaryFailedItem[] }>(
    '/hr/salary/calc',
    { method: 'POST', body: JSON.stringify({ month }) },
  );
}

export async function publishSalary(month: string) {
  return apiFetch<{ published: number }>('/hr/salary/publish', {
    method: 'POST',
    body: JSON.stringify({ month }),
  });
}

export async function fetchPayslips(month: string) {
  return apiFetch<{ list: Payslip[]; total: number }>(
    `/hr/salary/payslips?month=${encodeURIComponent(month)}`,
  );
}

export async function fetchMyPayslip(month?: string) {
  const q = month ? `?month=${encodeURIComponent(month)}` : '';
  return apiFetch<{ payslip: Payslip | null }>(`/hr/payslip/my${q}`);
}

// ── P4: 调整 / 凭证 / 报表 / 归档 / 考勤机导入 ──

// 工资手工调整（仅 published）：后端重算 net_pay 落盘并写日志；
// voucher_stale=true 表示该月 pay 凭证已生成但数据已过期，需重生成
export async function adjustPayslip(
  payslipId: string,
  field: string,
  newValue: number,
  reason: string,
) {
  return apiFetch<{ payslip: Payslip; voucher_stale: boolean }>(
    `/hr/salary/payslip/${encodeURIComponent(payslipId)}/adjust`,
    {
      method: 'POST',
      body: JSON.stringify({ field, new_value: newValue, reason }),
    },
  );
}

export async function fetchAdjustments(
  params: { payslip_id?: string; month?: string; employee_id?: string } = {},
) {
  const qs = new URLSearchParams(
    Object.entries(params).filter(([, v]) => v) as [string, string][],
  ).toString();
  return apiFetch<{ list: PayslipAdjust[]; total: number }>(
    `/hr/salary/adjustments${qs ? `?${qs}` : ''}`,
  );
}

// 生成（或重生成，幂等覆盖）月度财务凭证：voucher_type ∈ accrue(计提) | pay(发放)
export async function generateVoucher(month: string, voucherType: string) {
  return apiFetch<Voucher>('/hr/voucher/generate', {
    method: 'POST',
    body: JSON.stringify({ month, voucher_type: voucherType }),
  });
}

export async function fetchVouchers(month: string) {
  return apiFetch<{ list: Voucher[]; total: number }>(
    `/hr/voucher/list?month=${encodeURIComponent(month)}`,
  );
}

// 从 Content-Disposition 解析文件名：filename*=UTF-8''<RFC5987> 优先，
// 回退 filename="..."，再回退调用方提供的默认名
function parseFilenameFromDisposition(
  disposition: string,
  fallback: string,
): string {
  try {
    const star = disposition.match(/filename\*=(?:UTF-8|utf-8)''([^;]+)/i);
    if (star?.[1]) {
      const decoded = decodeURIComponent(star[1].trim().replace(/^"|"$/g, ''));
      if (decoded) return decoded;
    }
    const plain = disposition.match(/filename="?([^";]+)"?/i);
    if (plain?.[1]) {
      const decoded = decodeURIComponent(plain[1].trim());
      if (decoded && decoded !== 'report.xlsx') return decoded;
    }
  } catch {
    // 解析失败静默走 fallback
  }
  return fallback;
}

// 报表导出（xlsx 二进制流）：apiFetch 走 resp.json() 不支持 blob，
// 故单独用 fetch 复刻相同鉴权逻辑（Authorization 头 + user_id 参数 + 401 处理）
export async function exportReport(
  type: 'attendance' | 'payroll' | 'insurance',
  month: string,
): Promise<void> {
  const uid = getUserInfo().id || '';
  const url = `${BASE}/hr/report/export?type=${type}&month=${encodeURIComponent(month)}&user_id=${encodeURIComponent(uid)}`;
  const resp = await fetch(url, { headers: authHeaders() });
  if (resp.status === 401) {
    localStorage.removeItem('Authorization');
    localStorage.removeItem('userInfo');
    window.location.href = '/login';
    throw new Error('unauthorized');
  }
  if (!resp.ok) throw new Error(`报表下载失败（HTTP ${resp.status}）`);
  // 后端错误时仍可能返回 JSON（get_data_error_result），靠 Content-Type 甄别
  const contentType = resp.headers.get('Content-Type') || '';
  if (contentType.includes('application/json')) {
    const body = (await resp.json()) as { message?: string };
    throw new Error(body.message || '报表导出失败');
  }
  const blob = await resp.blob();
  const fallbackName = `report-${type}-${month}.xlsx`;
  const fileName = parseFilenameFromDisposition(
    resp.headers.get('Content-Disposition') || '',
    fallbackName,
  );
  const objUrl = URL.createObjectURL(blob);
  try {
    const a = document.createElement('a');
    a.href = objUrl;
    a.download = fileName;
    document.body.appendChild(a);
    a.click();
    a.remove();
  } finally {
    URL.revokeObjectURL(objUrl);
  }
}

export async function searchArchive(
  params: { month?: string; department?: string; keyword?: string } = {},
) {
  const qs = new URLSearchParams(
    Object.entries(params).filter(([, v]) => v) as [string, string][],
  ).toString();
  return apiFetch<{ list: ArchiveRow[]; total: number }>(
    `/hr/archive/search${qs ? `?${qs}` : ''}`,
  );
}

export async function syncAttendanceApi(records: unknown[]) {
  return apiFetch<BatchImportResult>('/hr/attendance/sync-api', {
    method: 'POST',
    body: JSON.stringify({ records }),
  });
}

export async function importAttendance(records: unknown[], fileName?: string) {
  return apiFetch<BatchImportResult>('/hr/attendance/import', {
    method: 'POST',
    body: JSON.stringify({
      records,
      ...(fileName ? { file_name: fileName } : {}),
    }),
  });
}
