import { CendTooltip } from '@/components/ui/tooltip';
import { History, MessageSquare, Paperclip, ScrollText } from 'lucide-react';
import type { ComponentType } from 'react';
import AttachmentPanel from './attachment-panel';
import AuditLogPanel from './audit-log-panel';
import CommentPanel from './comment-panel';
import VersionHistoryPanel from './version-history-panel';
import type { CollaborationWebSocketProvider } from './yjs-provider';

// 注：原 Lexical 时代的「格式规则」面板 (Sparkles) 已移除 —— Univer Docs 自带
// 原生格式工具栏,不再需要自定义格式规则。其余四个面板 (评论/附件/版本/审计)
// 都是纯后端 API 耦合,与编辑器内核无关,继续保留。
export type PanelKey = 'comments' | 'attachments' | 'versions' | 'audit';

interface Props {
  docId: string;
  apiFetch: (url: string, options?: RequestInit) => Promise<Response>;
  activePanel: PanelKey | null;
  onChange: (key: PanelKey | null) => void;
  isOwner: boolean;
  provider?: CollaborationWebSocketProvider | null;
}

const PANEL_ICONS: {
  key: PanelKey;
  title: string;
  icon: ComponentType<{ className?: string }>;
  ownerOnly?: boolean;
}[] = [
  { key: 'comments', title: '评论', icon: MessageSquare },
  { key: 'attachments', title: '附件', icon: Paperclip },
  { key: 'versions', title: '版本历史', icon: History },
  { key: 'audit', title: '审计日志', icon: ScrollText, ownerOnly: true },
];

export default function SidePanelBar({
  docId,
  apiFetch,
  activePanel,
  onChange,
  isOwner,
  provider,
}: Props) {
  const close = () => onChange(null);

  return (
    <div className="flex shrink-0 min-h-0 h-full">
      {/* 互斥面板容器 */}
      {activePanel && (
        <div className="w-72 shrink-0 border-l border-stone-200 bg-white h-full min-h-0 flex flex-col overflow-hidden">
          {activePanel === 'comments' && (
            <CommentPanel
              docId={docId}
              apiFetch={apiFetch}
              open
              onToggle={close}
              provider={provider}
            />
          )}
          {activePanel === 'attachments' && (
            <AttachmentPanel
              docId={docId}
              apiFetch={apiFetch}
              open
              onToggle={close}
            />
          )}
          {activePanel === 'versions' && (
            <VersionHistoryPanel
              docId={docId}
              apiFetch={apiFetch}
              open
              onToggle={close}
            />
          )}
          {activePanel === 'audit' && isOwner && (
            <AuditLogPanel
              docId={docId}
              apiFetch={apiFetch}
              open
              onToggle={close}
            />
          )}
        </div>
      )}
      {/* 常驻图标栏 */}
      <div className="w-10 shrink-0 border-l border-stone-200 bg-white flex flex-col items-center gap-1 pt-3">
        {PANEL_ICONS.filter((p) => !p.ownerOnly || isOwner).map((p) => {
          const Icon = p.icon;
          const active = activePanel === p.key;
          return (
            <CendTooltip key={p.key} title={p.title}>
              <button
                className={`size-7 flex items-center justify-center rounded-lg transition-colors ${
                  active
                    ? 'bg-stone-900 text-white'
                    : 'text-stone-400 hover:text-stone-700 hover:bg-stone-100'
                }`}
                onClick={() => onChange(active ? null : p.key)}
              >
                <Icon className="size-4" />
              </button>
            </CendTooltip>
          );
        })}
      </div>
    </div>
  );
}
