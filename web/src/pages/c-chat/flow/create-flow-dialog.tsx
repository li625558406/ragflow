// web/src/pages/c-chat/flow/create-flow-dialog.tsx
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
  type FlowCandidate,
} from '@/services/flow-service';
import { useEffect, useState } from 'react';

/** 「再次发起」预填：标题/参与人 + 可选初始文件（仅 doc/docx 版本可预填） */
export interface CreateFlowInitial {
  title?: string;
  leaderId?: string;
  handlerId?: string;
  file?: File | null;
}

export default function CreateFlowDialog({
  open,
  onClose,
  onCreated,
  initial,
}: {
  open: boolean;
  onClose: () => void;
  onCreated: (id: string) => void;
  initial?: CreateFlowInitial | null;
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
    // 每次打开重置上次残留的表单状态，并应用「再次发起」预填
    setTitle(initial?.title || '');
    setLeaderId(initial?.leaderId || '');
    setHandlerId(initial?.handlerId || '');
    setFile(initial?.file || null);
    setError('');
    listCandidates()
      .then((res) => setUsers(res.list ?? []))
      .catch(() => setUsers([]));
  }, [open, initial]);

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
                （可选，仅支持 doc/docx，创建后可在详情页上传）
              </span>
            </label>
            {file && (
              <div className="mt-1 flex items-center gap-2 rounded-md bg-[#F0F5FF] px-2 py-1 text-xs text-[#1a66fb]">
                <span className="truncate">{file.name}</span>
                <button
                  type="button"
                  className="ml-auto shrink-0 cursor-pointer text-[#999] hover:text-[#E5484D]"
                  onClick={() => setFile(null)}
                >
                  移除
                </button>
              </div>
            )}
            <input
              type="file"
              accept=".doc,.docx"
              className="mt-1 text-sm"
              onChange={(e) => {
                const f = e.target.files?.[0] ?? null;
                if (f && !/\.(doc|docx)$/i.test(f.name)) {
                  setError('初始文件仅支持 doc/docx 格式');
                  e.target.value = '';
                  return;
                }
                setError('');
                setFile(f);
              }}
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
