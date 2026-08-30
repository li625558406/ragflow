// web/src/pages/c-chat/flow/flow-panel.tsx
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import {
  createFlow,
  listCandidates,
  listFlows,
  type FlowCandidate,
} from '@/services/flow-service';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Plus } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import FlowDetail from './flow-detail';
import type { FlowInstanceItem, FlowScope } from './flow-types';

const SCOPES: { key: FlowScope; label: string }[] = [
  { key: 'todo', label: '待我处理' },
  { key: 'initiated', label: '我发起的' },
  { key: 'joined', label: '我参与的' },
];

const STATUS_LABEL: Record<string, string> = {
  initiator: '发起人处理中',
  leader: '领导审批中',
  handler: '处理人处理中',
  summary: '汇总审核中',
  archived: '已归档',
  cancelled: '已作废',
};

export default function FlowPanel() {
  const [scope, setScope] = useState<FlowScope>('todo');
  const [activeId, setActiveId] = useState<string | null>(null);
  const [createOpen, setCreateOpen] = useState(false);
  // 页签采用常驻 hidden-div 模式：不可见时暂停 todo 角标轮询
  const rootRef = useRef<HTMLDivElement>(null);
  const [panelVisible, setPanelVisible] = useState(true);
  const qc = useQueryClient();

  useEffect(() => {
    const el = rootRef.current;
    if (!el || typeof IntersectionObserver === 'undefined') return;
    const ob = new IntersectionObserver(
      ([entry]) => setPanelVisible(entry.isIntersecting),
      {
        threshold: 0,
      },
    );
    ob.observe(el);
    return () => ob.disconnect();
  }, []);

  const { data, isLoading, isError } = useQuery({
    queryKey: ['flow-list', scope],
    queryFn: () => listFlows(scope),
  });

  const todo = useQuery({
    queryKey: ['flow-list-todo-badge'],
    queryFn: () => listFlows('todo'),
    refetchInterval: panelVisible ? 30_000 : false,
  });

  return (
    <div ref={rootRef} className="flex h-full w-full gap-3">
      {/* 左：流程列表 */}
      <div className="flex w-80 shrink-0 flex-col rounded-xl border border-[#E5E5E5] bg-white">
        <div className="flex items-center justify-between border-b border-[#F0F0F0] px-3 py-2.5">
          <div className="flex gap-1">
            {SCOPES.map((s) => (
              <button
                key={s.key}
                onClick={() => setScope(s.key)}
                className={`rounded-md px-2.5 py-1 text-xs font-medium ${
                  scope === s.key
                    ? 'bg-[#1a66fb] text-white'
                    : 'text-[#666] hover:bg-[#F5F5F5]'
                }`}
              >
                {s.label}
                {s.key === 'todo' && (todo.data?.total ?? 0) > 0 && (
                  <span className="ml-1 inline-flex h-4 min-w-4 items-center justify-center rounded-full bg-red-500 px-1 text-[10px] text-white">
                    {todo.data!.total}
                  </span>
                )}
              </button>
            ))}
          </div>
          <Button
            size="sm"
            className="h-7 px-2"
            onClick={() => setCreateOpen(true)}
          >
            <Plus className="h-3.5 w-3.5" />
          </Button>
        </div>
        <div className="flex-1 overflow-y-auto">
          {isLoading && <div className="p-4 text-sm text-[#999]">加载中…</div>}
          {isError && !isLoading && (
            <div className="p-4 text-sm text-red-500">加载失败，请稍后重试</div>
          )}
          {!isLoading && !isError && data?.list?.length === 0 && (
            <div className="p-4 text-sm text-[#999]">暂无流程</div>
          )}
          {data?.list?.map((f: FlowInstanceItem) => (
            <button
              key={f.id}
              onClick={() => setActiveId(f.id)}
              className={`block w-full border-b border-[#F7F7F7] px-3 py-2.5 text-left hover:bg-[#F7FAFF] ${
                activeId === f.id ? 'bg-[#EFF4FF]' : ''
              }`}
            >
              <div className="truncate text-sm font-medium text-[#222]">
                {f.title}
              </div>
              <div className="mt-1 flex items-center justify-between">
                <span className="text-xs text-[#888]">
                  {STATUS_LABEL[f.status] ?? f.status}
                </span>
                <span className="text-xs text-[#aaa]">
                  {new Date(f.update_time).toLocaleDateString()}
                </span>
              </div>
            </button>
          ))}
        </div>
      </div>

      {/* 右：详情 */}
      <div className="min-w-0 flex-1 rounded-xl border border-[#E5E5E5] bg-white">
        {activeId ? (
          <FlowDetail
            flowId={activeId}
            onChanged={() => {
              qc.invalidateQueries({ queryKey: ['flow-list'] });
              qc.invalidateQueries({ queryKey: ['flow-list-todo-badge'] });
            }}
          />
        ) : (
          <div className="flex h-full items-center justify-center text-sm text-[#999]">
            从左侧选择一个流程，或点击 + 新建
          </div>
        )}
      </div>

      <CreateFlowDialog
        open={createOpen}
        onClose={() => setCreateOpen(false)}
        onCreated={(id) => {
          setCreateOpen(false);
          setActiveId(id);
          qc.invalidateQueries({ queryKey: ['flow-list'] });
        }}
      />
    </div>
  );
}

function CreateFlowDialog({
  open,
  onClose,
  onCreated,
}: {
  open: boolean;
  onClose: () => void;
  onCreated: (id: string) => void;
}) {
  const [title, setTitle] = useState('');
  const [leaderId, setLeaderId] = useState('');
  const [handlerId, setHandlerId] = useState('');
  const [file, setFile] = useState<File | null>(null);
  const [users, setUsers] = useState<FlowCandidate[]>([]);
  const [error, setError] = useState('');
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    if (!open) return;
    // 每次打开重置上次残留的表单状态
    setTitle('');
    setLeaderId('');
    setHandlerId('');
    setFile(null);
    setError('');
    listCandidates()
      .then((res) => setUsers(res.list ?? []))
      .catch(() => setUsers([]));
  }, [open]);

  const submit = async () => {
    setError('');
    if (!title.trim() || !leaderId || !handlerId) {
      setError('请填写完整：标题、领导、处理人');
      return;
    }
    if (leaderId === handlerId) {
      setError('领导和处理人不能是同一人');
      return;
    }
    const fd = new FormData();
    fd.append('title', title.trim());
    fd.append('leader_id', leaderId);
    fd.append('handler_id', handlerId);
    if (file) {
      fd.append('file', file);
    }
    setSubmitting(true);
    try {
      const res = await createFlow(fd);
      setTitle('');
      setLeaderId('');
      setHandlerId('');
      setFile(null);
      onCreated(res.id);
    } catch (e: any) {
      setError(e.message || '创建失败');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle>发起流程</DialogTitle>
        </DialogHeader>
        <div className="space-y-3">
          <div>
            <label className="text-sm text-[#555]">流程标题</label>
            <Input
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              placeholder="例如：XX 项目投标文件完善"
            />
          </div>
          <div>
            <label className="text-sm text-[#555]">领导（审批人）</label>
            <select
              className="mt-1 h-9 w-full rounded-md border border-[#DDD] px-2 text-sm"
              value={leaderId}
              onChange={(e) => setLeaderId(e.target.value)}
            >
              <option value="">请选择</option>
              {users.map((u) => (
                <option key={u.id} value={u.id}>
                  {u.nickname}
                </option>
              ))}
            </select>
          </div>
          <div>
            <label className="text-sm text-[#555]">处理人（角色2）</label>
            <select
              className="mt-1 h-9 w-full rounded-md border border-[#DDD] px-2 text-sm"
              value={handlerId}
              onChange={(e) => setHandlerId(e.target.value)}
            >
              <option value="">请选择</option>
              {users.map((u) => (
                <option key={u.id} value={u.id}>
                  {u.nickname}
                </option>
              ))}
            </select>
          </div>
          <div>
            <label className="text-sm text-[#555]">
              初始文件
              <span className="ml-1 text-xs text-[#999]">
                （可选，创建后可在详情页上传）
              </span>
            </label>
            <input
              type="file"
              className="mt-1 text-sm"
              onChange={(e) => setFile(e.target.files?.[0] ?? null)}
            />
          </div>
          {error && <div className="text-sm text-red-500">{error}</div>}
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>
            取消
          </Button>
          <Button onClick={submit} disabled={submitting}>
            {submitting ? '创建中…' : '创建'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
