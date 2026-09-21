import type { ITemplateFillState } from '@/hooks/template-fill-stream';

export type FlowStatus =
  | 'initiator'
  | 'leader'
  | 'handler'
  | 'summary'
  | 'archived'
  | 'cancelled';

export interface FlowInstanceItem {
  id: string;
  title: string;
  initiator_id: string;
  leader_id: string;
  handler_id: string;
  status: FlowStatus;
  current_version_id: string;
  create_time: number;
  update_time: number;
  /** 软删标记：0 正常 / 1 回收站（常规列表不会出现 1） */
  deleted?: number;
  deleted_time?: number | null;
}

export interface FlowVersionItem {
  id: string;
  flow_id: string;
  version_no: number;
  file_name: string;
  file_path: string;
  file_type: string;
  file_size: number;
  source: 'manual_upload' | 'ai_output' | 'ai_template_fill';
  created_by: string;
  node_status: FlowStatus;
  create_time: number;
}

export interface FlowCommentItem {
  id: string;
  flow_id: string;
  version_id: string;
  user_id: string;
  content: string;
  anchor_text?: string;
  anchor_para?: number | null;
  /** 批注级别 high/medium/low（存量无值视为 medium=一般） */
  severity?: string;
  create_time: number;
}

export interface FlowAiChatItem {
  id: string;
  flow_id: string;
  version_id: string;
  output_version_id: string;
  instruction: string;
  response: string;
  session_id: string;
  /** 操作人 user_id（存量迁移后非空，归属展示用） */
  user_id: string;
  /** 范本填写原始事件序列：落库/读取均为 JSON 字符串（兼容历史数组类型），回放前 parse */
  template_fill_events?: string | unknown[];
  /** 文件审核进度卡 {file_id,task_id} JSON（刷新后历史气泡按它挂进度卡），空串=无 */
  file_review?: string;
  /** 随消息上传的附件 [{id,name}] JSON 字符串（用户气泡附件 chip 展示），空串=无 */
  files?: string;
  create_time: number;
}

export interface FlowDetail {
  flow: FlowInstanceItem;
  versions: FlowVersionItem[];
  comments: FlowCommentItem[];
  ai_chats: FlowAiChatItem[];
  viewer: { is_owner: boolean; is_initiator: boolean; is_leader: boolean };
}

export type FlowScope = 'todo' | 'initiated' | 'joined' | 'all' | 'admin';

/** 正在进行中的一轮对话（发送后未保存前的流式状态），null 表示无进行中对话 */
export interface FlowLiveChat {
  instruction: string;
  response: string;
  busy: boolean;
  /** 本轮手动上传的附件（发送起点快照；历史记录保存前 live 气泡 chip 用） */
  files?: { id: string; name: string }[];
  /** 范本填写进度（template_fill_progress 事件累积，随流式上报） */
  templateFill?: ITemplateFillState;
  /** 文件审核进度（FileReview 节点产出 task_id 后落到此处供中部对话区挂载
   *  <FileReviewProgress>，与 c-chat 同款 msg.fileReview 字段语义） */
  fileReview?: {
    fileId: string;
    taskId: string;
  };
}

/** 全部流程管理页（超管）：状态筛选，''（全部）由调用方转换为不传 status */
export type FlowFinishedFilter =
  | 'running'
  | 'archived'
  | 'cancelled'
  | 'deleted';
