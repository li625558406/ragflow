// 文件审核状态类型与小工具（与 template-fill-stream.ts 并列，纯类型模块）
// 数据来源：GET /api/v1/file/review/file/<file_id>/state（T9 review_state 处理器）
// 没有 SSE、没有归约器：进度真相是 file_review_round 表，前端轮询拿完整对象。
// 注意标注版本语义：所有标注的 file_version 恒为首轮 v1（只有 review 轮产标注）；
// 面板展示标注用 state.annotations 全集，禁止按 file_version 过滤。

/** 单条标注 */
export interface IFileReviewAnnotation {
  id: string;
  round_id: string;
  task_id: string;
  file_id: string;
  /** 恒为首轮 v1；不要按此字段过滤面板标注列表。 */
  file_version: string;
  /** 自由字符串（format / completeness / clause / qualification / price / other …），
   *  T6 不做白名单校验，executor 兜底归一为 'other'。 */
  type: string;
  severity: 'high' | 'medium' | 'low';
  issue: string;
  suggestion: string;
  matched_text: string;
  source: 'ai' | 'manual';
  status: 'open' | 'new' | 'fixed' | 'resolved' | 'wontfix';
  prev_annotation_id: string;
  /** 已解析的定位对象（docx: p_hash/offset/run_index；xlsx: sheet/cell）。
   *  服务端 _json_dict 永远返回 dict（脏值降级 {}，永远不会是 string / null）。 */
  anchor: Record<string, unknown>;
}

/** 单轮次（一次 review 或一次 fix 的产物） */
export interface IFileReviewRound {
  id: string;
  round_no: number;
  /** reviewing/annotated/fixing/done/failed —— 见 isRoundRunning 谓词的注释 */
  status: string;
  /** 成稿对象名（v2/v3/v4 …）；无成稿时为 '' */
  file_version: string;
  template_id: string;
  user_query: string;
  summary: string;
  /** 已被 T9 _ERROR_CLIP=200 裁剪后的错误文案（>200 加 … 后缀） */
  error: string;
  /** 该轮成稿的 MinIO 对象名（无成稿时为 ''） */
  minio_path: string;
  /** 是否有可下载成稿（判据是 minio_path 非空，与 status 无关——见 T9 交接契约第 2 条） */
  produced: boolean;
  /** 该轮自称在跑（status 是 reviewing/fixing），但后台线程已不复存在 —— 服务重启 /
   *  崩溃后轮次状态永远不会回落（只有线程内部抛错才会被置 failed）。此时：
   *  status 仍是 reviewing/fixing 但**必须**停止轮询、停止转圈、**隐藏** fix 入口
   *  （服务端 stale 闸门拒绝一切 fix，唯一出路是重新发起审核 —— 留着按钮等于给用户
   *  一个必然失败的入口）。
   *  服务端判定（Service.is_stale_running，含 60s 宽限避开建轮→起线程窗口）；
   *  前端**不得**自己按时间重算。 */
  stale: boolean;
}

/** state 端点的完整响应体（轮询结果） */
export interface IFileReviewState {
  file_id: string;
  task_id: string | null;
  rounds: IFileReviewRound[];
  /** 最近一轮；用户主动选 fix 的入口会读它的 task_id/round_no */
  current: IFileReviewRound | null;
  /** 该展示的文档：最近一次落盘的成稿；从未落盘时回退到原件（object=file_id, version=''） */
  doc: { object: string; version: string };
  /** 全部标注（跨轮次/版本/任务）；file_version 恒为 v1 */
  annotations: IFileReviewAnnotation[];
  /** 面板头部计数（仅展示，不参与任何判定——见 _count_annotations 注释） */
  annotation_counts: {
    total: number;
    high: number;
    medium: number;
    low: number;
    pending: number;
    fixed: number;
  };
  /** 剩余可发起 fix 的轮次数（Service.fix_rounds_left，0 表示封顶） */
  fix_rounds_left: number;
  max_fix_rounds: number;
}

/** fix 端点的响应（POST /file/review/<task_id>/fix） */
export interface IFileReviewFixResponse {
  task_id: string;
  round_id: string;
  round_no: number;
  status: 'fixing';
  fix_rounds_left: number;
}

/** 标注状态修改端点的响应 */
export interface IFileReviewAnnotationUpdateResponse {
  annotation_id: string;
  status: IFileReviewAnnotation['status'];
}

/** 范本列表端点的响应 */
export interface IFileReviewTemplatesResponse {
  templates: Array<{
    id: string;
    name: string;
    description: string;
    annotation_types: string[];
  }>;
}

/** 轮次是否还在执行（决定轮询是否继续）。
 * 包含 reviewing 与 fixing —— 两种状态都意味着「后台线程还在写这轮」。
 * 注意：fixing 轮次的「终态前窗口」可能长达 20 次 DB 往返（T9 交接契约第 7 条），
 * 所以即使 status='fixing' 也必须继续轮询，不能停下。 */
export function isRoundRunning(status: string): boolean {
  return status === 'reviewing' || status === 'fixing';
}
