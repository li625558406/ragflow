export interface HrEmployeeProfile {
  id: string;
  user_id: string;
  nickname?: string;
  emp_no: string;
  department: string;
  position: string;
  entry_date: string;
  status: 'active' | 'resigned';
}

export type DayStatus =
  | 'normal'
  | 'late'
  | 'absent'
  | 'leave'
  | 'business_trip'
  | 'rest'
  | 'missing'
  | 'abnormal'
  | 'future';

export interface AttendanceRecordItem {
  punch_time: string;
  source: string;
  ip_address: string;
}

export interface TodayPunch {
  work_date: string;
  status: DayStatus;
  first_in: string;
  last_out: string;
  late_minutes: number;
  records: AttendanceRecordItem[];
}

export interface CalendarDay {
  work_date: string;
  status: DayStatus;
  first_in: string;
  last_out: string;
  late_minutes: number;
  locked: boolean;
}

export interface HrRuleConfig {
  work_start: string;
  work_end: string;
  late_threshold_minutes: number;
  [key: string]: string | number;
}

// ── P2: 假单 ──

export type LeaveType =
  | 'personal'
  | 'sick'
  | 'annual'
  | 'marriage'
  | 'maternity'
  | 'business_trip'
  | 'other'
  | 'repair';

export type LeaveStatus = 'pending' | 'approved' | 'rejected' | 'cancelled';

export interface LeaveStep {
  step_no: number;
  approver_id: string;
  approver_name: string;
  status: 'waiting' | 'pending' | 'approved' | 'rejected';
  comment: string;
  action_time: string;
}

export interface LeaveRequest {
  id: string;
  employee_id: string;
  nickname?: string;
  leave_type: LeaveType;
  start_date: string;
  end_date: string;
  duration_days: number;
  reason: string;
  status: LeaveStatus;
  current_step: number;
  steps?: LeaveStep[];
}

export interface LeaveBalance {
  leave_type: string;
  total_days: number;
  used_days: number;
  frozen_days: number;
}

// ── P3: 薪资 ──

export interface SalaryProfile {
  id: string;
  employee_id: string;
  nickname?: string;
  emp_no?: string;
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
}

export interface Payslip {
  id: string;
  employee_id: string;
  nickname?: string;
  emp_no?: string;
  month: string;
  attend_days: number;
  late_count: number;
  late_minutes: number;
  absent_days: number;
  overtime_hours: number;
  leave_days: number;
  base_salary: number;
  allowances: number;
  overtime_pay: number;
  gross_pay: number;
  attendance_deduction: number;
  social_insurance: number;
  housing_fund: number;
  income_tax: number;
  net_pay: number;
  status: 'draft' | 'published';
  published_at: string | null;
}

// 试算明细：后端将成功/失败员工统一内嵌在 list 中返回（无独立 failed 数组），
// 失败行 ok=false 且仅带 reason，无金额字段；成功行 ok=true 带金额明细
export interface SalaryTrialItem {
  employee_id: string;
  nickname?: string;
  emp_no?: string;
  ok: boolean;
  reason?: string;
  base_salary: number;
  allowances: number;
  overtime_pay: number;
  gross_pay: number;
  attendance_deduction: number;
  social_insurance: number;
  housing_fund: number;
  income_tax: number;
  net_pay: number;
  tax_snapshot?: Record<string, unknown>;
}

export interface SalaryFailedItem {
  employee_id: string;
  reason: string;
}

// ── P4: 凭证 / 调整日志 / 归档 / 考勤机导入 ──

// 财务凭证：entries 为 [摘要, 科目, 借方金额, 贷方金额] 数组；
// status: normal=正常 | stale=工资调整后未重生成（UI 需徽章提示）
export interface Voucher {
  id: string;
  month: string;
  voucher_type: 'accrue' | 'pay';
  entries: [string, string, number, number][];
  total_amount: number;
  status: 'normal' | 'stale' | string;
  create_time: string;
}

// 工资手工调整日志（本期仅查询展示；调整入口在工资单列表，后续迭代）
export interface PayslipAdjust {
  id: string;
  payslip_id: string;
  employee_id: string;
  month: string;
  field: string;
  old_value: number;
  new_value: number;
  reason: string;
  operator_id?: string;
  operator_name?: string;
  create_time: string;
}

// 历史归档检索行：以 hr_attendance_month 为主表，附当月工资单状态/实发（无则为 null）
export interface ArchiveRow {
  employee_id: string;
  emp_no: string;
  nickname: string;
  department: string;
  month: string;
  attend_days: number;
  late_count: number;
  absent_days: number;
  overtime_hours: number;
  payslip_status: 'draft' | 'published' | null;
  net_pay: number | null;
}

// 考勤机批量导入结果：failed 仅收录前 50 条明细，fail_total 为全部失败行数
export interface BatchImportResult {
  total: number;
  success: number;
  failed: {
    row: number;
    emp: string;
    punch_time: string;
    error: string;
  }[];
  fail_total?: number;
}
