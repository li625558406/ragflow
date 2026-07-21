import { useOnlineUsers } from './use-online-users';
import type { CollaborationWebSocketProvider } from './yjs-provider';

function getInitials(name: string): string {
  if (!name) return '?';
  // Take first char of each CJK character or first 2 ASCII chars
  const trimmed = name.trim();
  if (/[\u4e00-\u9fff]/.test(trimmed)) {
    return trimmed.slice(0, 2);
  }
  return trimmed.slice(0, 2).toUpperCase() || trimmed[0]?.toUpperCase() || '?';
}

interface Props {
  provider: CollaborationWebSocketProvider | null;
}

export default function MemberAvatars({ provider }: Props) {
  const { users, count } = useOnlineUsers(provider);

  // 过滤掉没有 name 的状态（行为对齐原实现）
  const members = users.filter((u) => u.name);

  if (members.length === 0) return null;

  return (
    <div className="flex items-center gap-1">
      {members.map((m) => (
        <div
          key={m.clientId}
          className="size-7 rounded-full flex items-center justify-center text-[10px] font-semibold text-white ring-2 ring-white -ml-1 first:ml-0"
          style={{ backgroundColor: m.color || '#958DF1' }}
          title={m.name}
        >
          {getInitials(m.name!)}
        </div>
      ))}
      <span className="text-xs text-stone-400 ml-1">{count} 人在线</span>
    </div>
  );
}
