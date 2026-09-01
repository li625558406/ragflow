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
