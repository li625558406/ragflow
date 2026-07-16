import { useEffect, useState } from 'react';
import type { CollaborationWebSocketProvider } from './yjs-provider';

interface Member {
  clientID: number;
  name: string;
  color: string;
}

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
  const [members, setMembers] = useState<Member[]>([]);

  useEffect(() => {
    if (!provider) {
      setMembers([]);
      return;
    }

    const update = () => {
      const states = provider.awareness.getStates();
      const list: Member[] = [];
      states.forEach((state, clientID) => {
        if (state.name) {
          list.push({
            clientID,
            name: state.name,
            color: state.color || '#958DF1',
          });
        }
      });
      setMembers(list);
    };

    update();
    provider.awareness.on('update', update);
    return () => {
      provider.awareness.off('update', update);
    };
  }, [provider]);

  if (members.length === 0) return null;

  return (
    <div className="flex items-center gap-1">
      {members.map((m) => (
        <div
          key={m.clientID}
          className="size-7 rounded-full flex items-center justify-center text-[10px] font-semibold text-white ring-2 ring-white -ml-1 first:ml-0"
          style={{ backgroundColor: m.color }}
          title={m.name}
        >
          {getInitials(m.name)}
        </div>
      ))}
      <span className="text-xs text-stone-400 ml-1">
        {members.length} 人在线
      </span>
    </div>
  );
}
