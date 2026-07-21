import { useEffect, useState } from 'react';
import type { CollaborationWebSocketProvider } from './yjs-provider';

export interface OnlineUser {
  clientId: number;
  name?: string;
  color?: string;
  focusing?: boolean;
  userId?: string;
  avatar?: string;
}

/**
 * 订阅 Yjs awareness，返回当前 room 的在线用户列表与人数。
 * Engine-agnostic：Univer Docs 和 Sheets 都可用。
 *
 * 数据源：provider.awareness.getStates() 返回 Map<clientId, UserState>，
 * 每次 awareness 'update' 事件触发时重新拉取全量状态。
 */
export function useOnlineUsers(
  provider: CollaborationWebSocketProvider | null,
): { users: OnlineUser[]; count: number } {
  const [users, setUsers] = useState<OnlineUser[]>([]);

  useEffect(() => {
    if (!provider) {
      setUsers([]);
      return;
    }

    const sync = () => {
      const states = provider.awareness.getStates();
      const list: OnlineUser[] = [];
      for (const [clientId, state] of states) {
        if (!state) continue;
        list.push({
          clientId,
          name: typeof state.name === 'string' ? state.name : undefined,
          color: typeof state.color === 'string' ? state.color : undefined,
          focusing: state.focusing,
          userId:
            (state as { user_id?: string }).user_id ||
            (state as { userId?: string }).userId,
          avatar: (state as { avatar?: string }).avatar,
        });
      }
      setUsers(list);
    };

    sync();
    const unsubscribe = provider.awareness.on('update', sync);
    return () => {
      unsubscribe();
    };
  }, [provider]);

  return { users, count: users.length };
}
