// web/src/pages/c-chat/flow/flow-utils.ts
// 流程页签共享的展示逻辑：状态文案/配色、步骤条定义、相对时间。
import type { FlowStatus } from './flow-types';

export const STATUS_LABEL: Record<string, string> = {
  initiator: '发起人处理中',
  leader: '领导审批中',
  handler: '处理人处理中',
  summary: '汇总审核中',
  archived: '已归档',
  cancelled: '已作废',
};

/** 状态 → 徽章配色（C端 #1a66fb 主色系） */
export const STATUS_BADGE: Record<string, string> = {
  initiator: 'bg-[#EFF4FF] text-[#1a66fb]',
  leader: 'bg-[#EFF4FF] text-[#1a66fb]',
  handler: 'bg-[#EFF4FF] text-[#1a66fb]',
  summary: 'bg-[#FFF7E8] text-[#C7810A]',
  archived: 'bg-[#EAF8F0] text-[#188A52]',
  cancelled: 'bg-[#FFF1F0] text-[#E5484D]',
};

/** 状态 → 列表小圆点配色 */
export const STATUS_DOT: Record<string, string> = {
  initiator: 'bg-[#1a66fb]',
  leader: 'bg-[#1a66fb]',
  handler: 'bg-[#1a66fb]',
  summary: 'bg-[#F5A623]',
  archived: 'bg-[#34C77B]',
  cancelled: 'bg-[#E5484D]',
};

/** 状态 → 列表状态文字配色 */
export const STATUS_TEXT_COLOR: Record<string, string> = {
  initiator: 'text-[#1a66fb]',
  leader: 'text-[#1a66fb]',
  handler: 'text-[#1a66fb]',
  summary: 'text-[#C7810A]',
  archived: 'text-[#188A52]',
  cancelled: 'text-[#E5484D]',
};

/** 流程五个正向节点（步骤条顺序） */
export const FLOW_STEPS: { key: FlowStatus; label: string }[] = [
  { key: 'initiator', label: '发起' },
  { key: 'leader', label: '领导审批' },
  { key: 'handler', label: '处理' },
  { key: 'summary', label: '汇总审核' },
  { key: 'archived', label: '归档' },
];

/** 当前状态在步骤条中的下标；已作废返回 -1（全部节点置灰） */
export function statusStepIndex(status: string): number {
  return FLOW_STEPS.findIndex((s) => s.key === status);
}

/** 相对时间：今天 / 昨天 / N天前 / 同年 M月D日 / 跨年 Y/M/D */
export function relTime(ts: number | string): string {
  const d = new Date(ts);
  if (Number.isNaN(d.getTime())) return '';
  const now = new Date();
  const startOfDay = (x: Date) =>
    new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime();
  const days = Math.round((startOfDay(now) - startOfDay(d)) / 86_400_000);
  if (days <= 0) return '今天';
  if (days === 1) return '昨天';
  if (days < 7) return `${days}天前`;
  if (d.getFullYear() === now.getFullYear())
    return `${d.getMonth() + 1}月${d.getDate()}日`;
  return `${d.getFullYear()}/${d.getMonth() + 1}/${d.getDate()}`;
}
