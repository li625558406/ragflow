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

// 试算明细（后端 _TRIAL_KEYS 白名单字段）
export interface SalaryTrialItem {
  employee_id: string;
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
