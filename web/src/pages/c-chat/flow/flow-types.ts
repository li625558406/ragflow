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
}

export interface FlowVersionItem {
  id: string;
  flow_id: string;
  version_no: number;
  file_name: string;
  file_path: string;
  file_type: string;
  file_size: number;
  source: 'manual_upload' | 'ai_output';
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
  create_time: number;
}

export interface FlowDetail {
  flow: FlowInstanceItem;
  versions: FlowVersionItem[];
  comments: FlowCommentItem[];
  ai_chats: FlowAiChatItem[];
  viewer: { is_owner: boolean; is_initiator: boolean };
}

export type FlowScope = 'todo' | 'initiated' | 'joined' | 'all';
