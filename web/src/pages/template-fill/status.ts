// 填写任务状态公共常量：tasks.tsx 列表与 task-detail-drawer.tsx 抽屉共用
// 状态 → 中文标签
export const TASK_STATUS_LABEL: Record<string, string> = {
  pending: '排队中',
  retrieving: '检索中',
  generating: '生成中',
  rendering: '渲染中',
  done: '已完成',
  partial: '部分完成',
  failed: '失败',
};

// 状态 → Badge 配色（照 ui/badge 既有变体；done/partial/failed 着色，进行中中性）
export const TASK_STATUS_VARIANT: Record<
  string,
  'default' | 'secondary' | 'success' | 'destructive' | 'outline'
> = {
  pending: 'secondary',
  retrieving: 'secondary',
  generating: 'default',
  rendering: 'default',
  done: 'success',
  partial: 'outline',
  failed: 'destructive',
};

// 单元格填写状态 → 中文标签 + 底色（逐格表用）
export const CELL_STATUS: Record<string, { label: string; cls: string }> = {
  filled: { label: '已填', cls: 'bg-green-100 text-green-700' },
  not_found: { label: '待人工', cls: 'bg-amber-100 text-amber-700' },
  manual: { label: '人工格', cls: 'bg-gray-200 text-gray-700' },
};
