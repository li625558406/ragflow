// web/src/pages/c-chat/flow/flow-panel.tsx
import { Button } from '@/components/ui/button';
import { listFlows } from '@/services/flow-service';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import {
  Archive,
  ChevronDown,
  Inbox,
  MessageSquare,
  Plus,
  Waypoints,
} from 'lucide-react';
import { useCallback, useEffect, useRef, useState } from 'react';
import CreateFlowDialog from './create-flow-dialog';
import FlowDetail from './flow-detail';
import FlowManage from './flow-manage';
import type { FlowInstanceItem, FlowScope } from './flow-types';
import {
  relTime,
  STATUS_DOT,
  STATUS_LABEL,
  STATUS_TEXT_COLOR,
} from './flow-utils';

const SCOPES: { key: FlowScope; label: string }[] = [
  { key: 'todo', label: '待我处理' },
  { key: 'initiated', label: '我发起的' },
  { key: 'joined', label: '我参与的' },
];

export default function FlowPanel() {
  const [scope, setScope] = useState<FlowScope>('todo');
  const [activeId, setActiveId] = useState<string | null>(null);
  const [createOpen, setCreateOpen] = useState(false);
  // 工作台 / 已结束流程管理 双视图
  const [view, setView] = useState<'workbench' | 'manage'>('workbench');
  // 页签采用常驻 hidden-div 模式：不可见时暂停 todo 角标轮询
  const rootRef = useRef<HTMLDivElement>(null);
  const [panelVisible, setPanelVisible] = useState(true);
  // 批注模块挂载点：位于左侧流程列表下方，由 FlowDetail portal 渲染
  const [commentSlot, setCommentSlot] = useState<HTMLDivElement | null>(null);
  // 批注区折叠：默认关闭，有批注时自动展开；用户手动切换后不再自动干预
  const [commentsOpen, setCommentsOpen] = useState(false);
  const [commentCount, setCommentCount] = useState(0);
  const commentManualRef = useRef(false);
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

  // 切换流程时重置批注计数（新详情会重新上报）
  useEffect(() => {
    setCommentCount(0);
  }, [activeId]);

  const handleCommentCount = useCallback((n: number) => {
    setCommentCount(n);
    if (!commentManualRef.current) setCommentsOpen(n > 0);
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

  const scopeIdx = SCOPES.findIndex((s) => s.key === scope);
  const list = data?.list ?? [];

  return (
    <div ref={rootRef} className="flex h-full w-full gap-3">
      {view === 'workbench' && (
        <>
          {/* 左：流程列表（上）+ 批注模块（下，可折叠），高度平分 */}
          <div className="flex w-80 shrink-0 flex-col overflow-hidden rounded-xl border border-[#E5E5E5] bg-white">
            <div className="flex min-h-0 flex-1 flex-col">
              {/* 顶栏：分段控件 + 新建 */}
              <div className="flex shrink-0 items-center gap-2 border-b border-[#F0F0F0] px-3 py-2.5">
                <div className="relative flex flex-1 rounded-lg bg-[#F2F3F5] p-0.5">
                  {/* 滑动指示块 */}
                  <span
                    aria-hidden
                    className="absolute inset-y-0.5 left-0.5 rounded-md bg-white shadow-[0_1px_3px_rgba(0,0,0,0.10)] transition-transform duration-200 ease-out"
                    style={{
                      width: 'calc((100% - 4px) / 3)',
                      transform: `translateX(${scopeIdx * 100}%)`,
                    }}
                  />
                  {SCOPES.map((s) => (
                    <button
                      key={s.key}
                      onClick={() => setScope(s.key)}
                      className={`relative z-10 flex-1 cursor-pointer rounded-md px-1 py-1 text-xs font-medium transition-colors duration-150 ${
                        scope === s.key
                          ? 'text-[#1a66fb]'
                          : 'text-[#666] hover:text-[#333]'
                      }`}
                    >
                      {s.label}
                      {s.key === 'todo' && (todo.data?.total ?? 0) > 0 && (
                        <span className="ml-1 inline-flex h-4 min-w-4 items-center justify-center rounded-full bg-red-500 px-1 align-top text-[10px] leading-4 text-white">
                          {todo.data!.total}
                        </span>
                      )}
                    </button>
                  ))}
                </div>
                <Button
                  size="icon"
                  variant="outline"
                  title="已结束流程"
                  className="h-7 w-7 shrink-0 rounded-full border-[#E5E5E5] text-[#666] transition-transform hover:text-[#1a66fb] active:scale-90"
                  onClick={() => setView('manage')}
                >
                  <Archive className="h-4 w-4" />
                </Button>
                <Button
                  size="icon"
                  className="h-7 w-7 shrink-0 rounded-full transition-transform active:scale-90"
                  onClick={() => setCreateOpen(true)}
                >
                  <Plus className="h-4 w-4" />
                </Button>
              </div>

              {/* 列表 */}
              <div className="min-h-0 flex-1 overflow-y-auto scrollbar-thin">
                {isLoading && <ListSkeleton />}

                {isError && !isLoading && (
                  <div className="p-4 text-sm text-red-500">
                    加载失败，请稍后重试
                  </div>
                )}

                {!isLoading && !isError && list.length === 0 && (
                  <div className="flex h-full flex-col items-center justify-center gap-2 px-6 text-center">
                    <div className="flex h-11 w-11 items-center justify-center rounded-full bg-[#F2F6FF]">
                      <Inbox className="h-5 w-5 text-[#1a66fb]" />
                    </div>
                    <div className="text-sm text-[#666]">
                      {scope === 'todo' ? '暂无待处理的流程' : '暂无流程'}
                    </div>
                    <button
                      type="button"
                      onClick={() => setCreateOpen(true)}
                      className="cursor-pointer text-xs font-medium text-[#1a66fb] transition-opacity hover:opacity-80"
                    >
                      + 发起新流程
                    </button>
                  </div>
                )}

                <div className="space-y-1 p-2">
                  {list.map((f: FlowInstanceItem, i) => {
                    const active = activeId === f.id;
                    return (
                      <button
                        key={f.id}
                        onClick={() => setActiveId(f.id)}
                        className={`group relative block w-full cursor-pointer overflow-hidden rounded-lg border px-3 py-2.5 text-left transition-all duration-150 motion-reduce:animate-none animate-in fade-in slide-in-from-left-2 fill-mode-both active:scale-[0.99] ${
                          active
                            ? 'border-[#BFD3F5] bg-[#F0F5FF]'
                            : 'border-transparent hover:bg-[#F7F8FA]'
                        }`}
                        style={{
                          animationDelay: `${Math.min(i * 40, 240)}ms`,
                        }}
                      >
                        {/* 选中态左侧指示条 */}
                        {active && (
                          <span
                            aria-hidden
                            className="absolute inset-y-1.5 left-0 w-[3px] rounded-r-full bg-[#1a66fb] motion-reduce:animate-none animate-in fade-in slide-in-from-left-1 duration-200"
                          />
                        )}
                        <div className="truncate text-sm font-medium text-[#222]">
                          {f.title}
                        </div>
                        <div className="mt-1.5 flex items-center gap-1.5 text-xs">
                          <span
                            className={`h-1.5 w-1.5 shrink-0 rounded-full ${
                              STATUS_DOT[f.status] ?? 'bg-[#bbb]'
                            }`}
                          />
                          <span
                            className={`font-medium ${
                              STATUS_TEXT_COLOR[f.status] ?? 'text-[#888]'
                            }`}
                          >
                            {STATUS_LABEL[f.status] ?? f.status}
                          </span>
                          <span className="ml-auto shrink-0 text-[#aaa]">
                            {relTime(f.update_time)}
                          </span>
                        </div>
                      </button>
                    );
                  })}
                </div>
              </div>
            </div>

            {/* 批注区开关：计数由 FlowDetail 上报 */}
            <div className="shrink-0 border-t border-[#F0F0F0]">
              <button
                type="button"
                onClick={() => {
                  commentManualRef.current = true;
                  setCommentsOpen((o) => !o);
                }}
                className="flex w-full cursor-pointer items-center gap-1.5 px-3 py-2 text-sm font-medium text-[#444] transition-colors hover:bg-[#F7F8FA]"
              >
                <MessageSquare className="h-4 w-4 text-[#1a66fb]" />
                批注
                {commentCount > 0 && (
                  <span className="inline-flex h-4 min-w-4 items-center justify-center rounded-full bg-[#EFF4FF] px-1 text-[10px] font-semibold text-[#1a66fb]">
                    {commentCount}
                  </span>
                )}
                <ChevronDown
                  className={`ml-auto h-3.5 w-3.5 text-[#999] transition-transform duration-200 ${
                    commentsOpen ? '' : '-rotate-90'
                  }`}
                />
              </button>
            </div>
            {commentsOpen && (
              /* 批注模块挂载点：由 FlowDetail portal 渲染，与列表平分高度 */
              <div ref={setCommentSlot} className="h-1/2 min-h-0" />
            )}
          </div>

          {/* 右：详情 */}
          <div className="min-w-0 flex-1 overflow-hidden rounded-xl border border-[#E5E5E5] bg-white">
            {activeId ? (
              <FlowDetail
                flowId={activeId}
                commentPortal={commentSlot}
                onCommentsCount={handleCommentCount}
                onChanged={() => {
                  qc.invalidateQueries({ queryKey: ['flow-list'] });
                  qc.invalidateQueries({ queryKey: ['flow-list-todo-badge'] });
                }}
                onDeleted={() => {
                  setActiveId(null);
                  qc.invalidateQueries({ queryKey: ['flow-list'] });
                  qc.invalidateQueries({ queryKey: ['flow-list-todo-badge'] });
                }}
              />
            ) : (
              <div className="flex h-full flex-col items-center justify-center gap-3">
                <div className="flex h-14 w-14 items-center justify-center rounded-2xl bg-[#F2F6FF]">
                  <Waypoints className="h-6 w-6 text-[#1a66fb]" />
                </div>
                <div className="text-sm text-[#999]">
                  从左侧选择一个流程，或点击 + 新建
                </div>
              </div>
            )}
          </div>
        </>
      )}

      {view === 'manage' && (
        <div className="min-w-0 flex-1">
          <FlowManage onBack={() => setView('workbench')} />
        </div>
      )}

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

/** 列表加载骨架屏 */
function ListSkeleton() {
  return (
    <div className="space-y-2 p-2">
      {[0, 1, 2, 3].map((i) => (
        <div
          key={i}
          className="rounded-lg border border-[#F2F2F2] px-3 py-2.5"
          style={{ opacity: 1 - i * 0.18 }}
        >
          <div className="h-3.5 w-3/4 animate-pulse rounded bg-[#F0F1F3]" />
          <div className="mt-2 h-2.5 w-1/3 animate-pulse rounded bg-[#F4F5F7]" />
        </div>
      ))}
    </div>
  );
}
