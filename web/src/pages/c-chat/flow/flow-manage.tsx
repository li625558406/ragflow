// web/src/pages/c-chat/flow/flow-manage.tsx
// 已结束流程管理视图：查看 / 再次发起 / 重新激活 / 软删除（回收站可恢复）。
import { Button } from '@/components/ui/button';
import {
  downloadVersionBlob,
  getFlowDetail,
  listCandidates,
  listFlows,
  reactivateFlow,
  restoreFlow,
  softDeleteFlow,
  type FlowCandidate,
} from '@/services/flow-service';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import {
  Archive,
  ArchiveRestore,
  ArrowLeft,
  Copy,
  Eye,
  RotateCcw,
  Trash2,
} from 'lucide-react';
import { useMemo, useState, type ReactNode } from 'react';
import CreateFlowDialog, { type CreateFlowInitial } from './create-flow-dialog';
import FlowDetail from './flow-detail';
import type {
  FlowFinishedFilter,
  FlowInstanceItem,
  FlowManageScope,
} from './flow-types';
import { relTime, STATUS_BADGE, STATUS_LABEL } from './flow-utils';

const FILTERS: { key: FlowFinishedFilter; label: string }[] = [
  { key: 'finished', label: '全部' },
  { key: 'archived', label: '已归档' },
  { key: 'cancelled', label: '已作废' },
  { key: 'deleted', label: '回收站' },
];

export default function FlowManage({ onBack }: { onBack: () => void }) {
  const [scope, setScope] = useState<FlowManageScope>('initiated');
  const [filter, setFilter] = useState<FlowFinishedFilter>('finished');
  const [viewFlowId, setViewFlowId] = useState<string | null>(null);
  const [reinitiate, setReinitiate] = useState<{
    initial: CreateFlowInitial;
  } | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [actionError, setActionError] = useState('');
  const qc = useQueryClient();

  const { data, isLoading, isError } = useQuery({
    queryKey: ['flow-manage', scope, filter],
    queryFn: () => listFlows(scope, filter),
  });

  // id → 昵称映射（表格展示领导/处理人名字）
  const { data: usersData } = useQuery({
    queryKey: ['flow-candidates'],
    queryFn: listCandidates,
    staleTime: 5 * 60_000,
  });
  const userMap = useMemo(() => {
    const m = new Map<string, string>();
    (usersData?.list ?? []).forEach((u: FlowCandidate) =>
      m.set(u.id, u.nickname),
    );
    return m;
  }, [usersData]);

  const refresh = () => {
    qc.invalidateQueries({ queryKey: ['flow-manage'] });
    qc.invalidateQueries({ queryKey: ['flow-list'] });
    qc.invalidateQueries({ queryKey: ['flow-list-todo-badge'] });
  };

  const runAction = async (
    f: FlowInstanceItem,
    act: 'delete' | 'restore' | 'reactivate',
  ) => {
    setActionError('');
    setBusyId(f.id);
    try {
      if (act === 'delete') await softDeleteFlow(f.id);
      if (act === 'restore') await restoreFlow(f.id);
      if (act === 'reactivate') await reactivateFlow(f.id);
      refresh();
    } catch (e: any) {
      setActionError(e.message || '操作失败');
    } finally {
      setBusyId(null);
    }
  };

  /** 再次发起：预填标题/参与人；最终版本为 doc/docx 时预填为初始文件 */
  const startReinitiate = async (f: FlowInstanceItem) => {
    setActionError('');
    setBusyId(f.id);
    try {
      let file: File | null = null;
      try {
        if (f.current_version_id) {
          const detail = await getFlowDetail(f.id);
          const last = detail.versions[detail.versions.length - 1];
          if (last && /\.(doc|docx)$/i.test(last.file_name)) {
            const blob = await downloadVersionBlob(f.id, last.id);
            file = new File([blob], last.file_name, {
              type: last.file_type || undefined,
            });
          }
        }
      } catch {
        // 预填文件失败不阻断，走手动上传
      }
      setReinitiate({
        initial: {
          title: f.title,
          leaderId: f.leader_id,
          handlerId: f.handler_id,
          file,
        },
      });
    } finally {
      setBusyId(null);
    }
  };

  // 详情查看态：整区切换到 FlowDetail
  if (viewFlowId) {
    return (
      <div className="flex h-full w-full flex-col gap-2">
        <div className="flex shrink-0 items-center gap-2">
          <Button
            size="sm"
            variant="outline"
            className="h-7 gap-1 rounded-full"
            onClick={() => {
              setActionError('');
              setViewFlowId(null);
              refresh();
            }}
          >
            <ArrowLeft className="h-3.5 w-3.5" />
            返回列表
          </Button>
        </div>
        <div className="min-h-0 flex-1 overflow-hidden rounded-xl border border-[#E5E5E5] bg-white">
          <FlowDetail
            flowId={viewFlowId}
            onChanged={refresh}
            onDeleted={() => {
              setViewFlowId(null);
              refresh();
            }}
          />
        </div>
      </div>
    );
  }

  const list = data?.list ?? [];
  const inTrash = filter === 'deleted';

  return (
    <div className="flex h-full w-full flex-col overflow-hidden rounded-xl border border-[#E5E5E5] bg-white">
      {/* 顶栏：返回 + 标题 + 视角分段 + 状态筛选 */}
      <div className="flex shrink-0 items-center gap-3 border-b border-[#F0F0F0] px-4 py-2.5">
        <button
          type="button"
          onClick={onBack}
          className="flex cursor-pointer items-center gap-1 text-sm text-[#666] transition-colors hover:text-[#1a66fb]"
        >
          <ArrowLeft className="h-4 w-4" />
          返回
        </button>
        <span className="text-sm font-semibold text-[#222]">已结束流程</span>

        <div className="ml-4 flex overflow-hidden rounded-lg bg-[#F2F3F5] p-0.5">
          {(
            [
              { key: 'initiated', label: '我发起的' },
              { key: 'joined', label: '我参与的' },
            ] as { key: FlowManageScope; label: string }[]
          ).map((s) => (
            <button
              key={s.key}
              type="button"
              onClick={() => setScope(s.key)}
              className={`cursor-pointer rounded-md px-3 py-1 text-xs transition-colors ${
                scope === s.key
                  ? 'bg-[#1a66fb] text-white'
                  : 'bg-white text-[#666] hover:bg-[#F7F8FA]'
              }`}
            >
              {s.label}
            </button>
          ))}
        </div>

        <div className="ml-auto flex items-center gap-1.5">
          <span className="text-xs text-[#999]">筛选</span>
          {FILTERS.map((ft) => (
            <button
              key={ft.key}
              type="button"
              onClick={() => setFilter(ft.key)}
              className={`cursor-pointer rounded-full px-2.5 py-1 text-xs transition-colors ${
                filter === ft.key
                  ? 'bg-[#EFF4FF] font-medium text-[#1a66fb]'
                  : 'text-[#666] hover:bg-[#F7F8FA]'
              }`}
            >
              {ft.label}
            </button>
          ))}
        </div>
      </div>

      {/* 表格滚动区 */}
      <div className="min-h-0 flex-1 overflow-y-auto scrollbar-thin">
        {isLoading && (
          <div className="flex h-full items-center justify-center text-sm text-[#999]">
            加载中…
          </div>
        )}

        {isError && !isLoading && (
          <div className="flex h-full items-center justify-center text-sm text-red-500">
            加载失败，请稍后重试
          </div>
        )}

        {!isLoading && !isError && list.length === 0 && (
          <div className="flex h-full flex-col items-center justify-center gap-2">
            <div className="flex h-11 w-11 items-center justify-center rounded-full bg-[#F2F6FF]">
              <Archive className="h-5 w-5 text-[#1a66fb]" />
            </div>
            <div className="text-sm text-[#666]">
              {inTrash ? '回收站为空' : '暂无已结束的流程'}
            </div>
          </div>
        )}

        {!isLoading && !isError && list.length > 0 && (
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-[#F0F0F0] text-xs text-[#999]">
                <th className="px-4 py-2 text-left font-normal">标题</th>
                <th className="px-3 py-2 text-left font-normal">状态</th>
                <th className="px-3 py-2 text-left font-normal">领导</th>
                <th className="px-3 py-2 text-left font-normal">处理人</th>
                <th className="px-3 py-2 text-left font-normal">最后更新</th>
                <th className="px-4 py-2 text-right font-normal">操作</th>
              </tr>
            </thead>
            <tbody>
              {list.map((f: FlowInstanceItem) => (
                <tr
                  key={f.id}
                  className="border-b border-[#F7F7F7] transition-colors hover:bg-[#FAFBFC]"
                >
                  <td className="max-w-[280px] px-4 py-2.5">
                    <span
                      className="block truncate font-medium text-[#222]"
                      title={f.title}
                    >
                      {f.title}
                    </span>
                  </td>
                  <td className="whitespace-nowrap px-3 py-2.5">
                    <span
                      className={`rounded-md px-2 py-0.5 text-xs font-medium ${
                        STATUS_BADGE[f.status] ?? 'bg-[#F2F2F2] text-[#666]'
                      }`}
                    >
                      {STATUS_LABEL[f.status] ?? f.status}
                    </span>
                  </td>
                  <td className="whitespace-nowrap px-3 py-2.5 text-[#444]">
                    {userMap.get(f.leader_id) || f.leader_id}
                  </td>
                  <td className="whitespace-nowrap px-3 py-2.5 text-[#444]">
                    {userMap.get(f.handler_id) || f.handler_id}
                  </td>
                  <td className="whitespace-nowrap px-3 py-2.5 text-[#999]">
                    {relTime(f.update_time)}
                  </td>
                  <td className="whitespace-nowrap px-4 py-2.5 text-right">
                    <div className="inline-flex items-center gap-0.5">
                      <ActionBtn
                        title="查看"
                        disabled={busyId === f.id}
                        onClick={() => {
                          setActionError('');
                          setViewFlowId(f.id);
                        }}
                      >
                        <Eye className="h-3.5 w-3.5" />
                      </ActionBtn>

                      {scope === 'initiated' && !inTrash && (
                        <>
                          <ActionBtn
                            title="再次发起（预填标题与参与人）"
                            disabled={busyId === f.id}
                            onClick={() => startReinitiate(f)}
                          >
                            <Copy className="h-3.5 w-3.5" />
                          </ActionBtn>
                          <ActionBtn
                            title="重新激活"
                            disabled={busyId === f.id}
                            onClick={() => {
                              if (
                                !window.confirm(
                                  `确定重新激活流程「${f.title}」？激活后回到发起人节点继续流转。`,
                                )
                              )
                                return;
                              runAction(f, 'reactivate');
                            }}
                          >
                            <RotateCcw className="h-3.5 w-3.5" />
                          </ActionBtn>
                          <ActionBtn
                            title="删除（移入回收站）"
                            danger
                            disabled={busyId === f.id}
                            onClick={() => {
                              if (
                                !window.confirm(
                                  `确定删除流程「${f.title}」？删除后移入回收站，可随时恢复。`,
                                )
                              )
                                return;
                              runAction(f, 'delete');
                            }}
                          >
                            <Trash2 className="h-3.5 w-3.5" />
                          </ActionBtn>
                        </>
                      )}

                      {scope === 'initiated' && inTrash && (
                        <ActionBtn
                          title="恢复"
                          disabled={busyId === f.id}
                          onClick={() => runAction(f, 'restore')}
                        >
                          <ArchiveRestore className="h-3.5 w-3.5" />
                        </ActionBtn>
                      )}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {actionError && (
        <div className="shrink-0 bg-red-50 px-4 py-1.5 text-xs text-red-500">
          {actionError}
        </div>
      )}

      <CreateFlowDialog
        open={!!reinitiate}
        initial={reinitiate?.initial}
        onClose={() => setReinitiate(null)}
        onCreated={(id) => {
          setReinitiate(null);
          refresh();
          setViewFlowId(id);
        }}
      />
    </div>
  );
}

/** 表格操作列图标按钮 */
function ActionBtn({
  title,
  onClick,
  disabled,
  danger,
  children,
}: {
  title: string;
  onClick: () => void;
  disabled?: boolean;
  danger?: boolean;
  children: ReactNode;
}) {
  return (
    <button
      type="button"
      title={title}
      disabled={disabled}
      onClick={onClick}
      className={`cursor-pointer rounded-md p-1.5 transition-colors disabled:cursor-not-allowed disabled:opacity-40 ${
        danger
          ? 'text-[#999] hover:bg-[#FFF1F0] hover:text-[#E5484D]'
          : 'text-[#666] hover:bg-[#EFF4FF] hover:text-[#1a66fb]'
      }`}
    >
      {children}
    </button>
  );
}
