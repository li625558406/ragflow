import storage from '@/utils/authorization-util';
import { useLexicalComposerContext } from '@lexical/react/LexicalComposerContext';
import {
  $getSelection,
  $isRangeSelection,
  $isTextNode,
  COMMAND_PRIORITY_LOW,
  KEY_DOWN_COMMAND,
  TextNode,
} from 'lexical';
import { useCallback, useEffect, useRef, useState } from 'react';
import { $createMentionNode } from './nodes/mention-node';

interface UserItem {
  id: string;
  nickname: string;
  email: string;
}

interface Props {
  apiFetch: (url: string, options?: RequestInit) => Promise<Response>;
}

export default function MentionPlugin({ apiFetch }: Props) {
  const [editor] = useLexicalComposerContext();
  const [users, setUsers] = useState<UserItem[]>([]);
  const [showMenu, setShowMenu] = useState(false);
  const [query, setQuery] = useState('');
  const [selectedIndex, setSelectedIndex] = useState(0);
  const [position, setPosition] = useState({ x: 0, y: 0 });
  const menuRef = useRef<HTMLDivElement>(null);
  const loadedRef = useRef(false);

  const filteredUsers = users
    .filter((u) => {
      const name = u.nickname || u.email || '';
      return name.toLowerCase().includes(query.toLowerCase());
    })
    .slice(0, 8);

  // Refs to avoid re-registering KEY_DOWN_COMMAND on every keystroke
  const filteredUsersRef = useRef(filteredUsers);
  filteredUsersRef.current = filteredUsers;
  const selectedIndexRef = useRef(selectedIndex);
  selectedIndexRef.current = selectedIndex;

  const loadUsers = useCallback(async () => {
    if (loadedRef.current) return;
    loadedRef.current = true;
    try {
      const userInfo = storage.getUserInfoObject();
      const tenantId = userInfo?.id;
      if (!tenantId) return;
      const resp = await apiFetch(`/api/v1/tenants/${tenantId}/users`);
      const result = await resp.json();
      if (result.code === 0 && Array.isArray(result.data)) {
        setUsers(result.data);
      }
    } catch (e) {
      console.error('Failed to load users:', e);
    }
  }, [apiFetch]);

  // Monitor selection changes to detect @ trigger
  useEffect(() => {
    return editor.registerUpdateListener(() => {
      editor.getEditorState().read(() => {
        const selection = $getSelection();
        if (!$isRangeSelection(selection) || !selection.isCollapsed()) {
          setShowMenu(false);
          return;
        }

        const anchorNode = selection.anchor.getNode();
        const anchorOffset = selection.anchor.offset;

        if (!$isTextNode(anchorNode)) {
          setShowMenu(false);
          return;
        }

        const textContent = anchorNode.getTextContent();
        // Find '@' before cursor
        const textBeforeCursor = textContent.slice(0, anchorOffset);
        const atIndex = textBeforeCursor.lastIndexOf('@');

        if (atIndex === -1) {
          setShowMenu(false);
          return;
        }

        // Check that @ is at word boundary (preceded by space or start of text)
        if (atIndex > 0 && textBeforeCursor[atIndex - 1] !== ' ') {
          setShowMenu(false);
          return;
        }

        // Get query text after @
        const queryText = textBeforeCursor.slice(atIndex + 1);
        // Don't show menu if query contains spaces
        if (queryText.includes(' ')) {
          setShowMenu(false);
          return;
        }

        setQuery(queryText);

        // Load users on first trigger
        loadUsers();

        // Calculate popover position from cursor
        try {
          const domSelection = window.getSelection();
          if (domSelection && domSelection.rangeCount > 0) {
            const range = domSelection.getRangeAt(0);
            const rect = range.getBoundingClientRect();
            setPosition({
              x: rect.left,
              y: rect.bottom + 4,
            });
          }
        } catch {
          // ignore position errors
        }

        setShowMenu(true);
        setSelectedIndex(0);
      });
    });
  }, [editor, loadUsers]);

  const insertMention = useCallback(
    (user: UserItem) => {
      editor.update(() => {
        const selection = $getSelection();
        if (!$isRangeSelection(selection)) return;

        const anchorNode = selection.anchor.getNode();
        const anchorOffset = selection.anchor.offset;

        if (!$isTextNode(anchorNode)) return;

        const textContent = anchorNode.getTextContent();
        const atIndex = textContent.slice(0, anchorOffset).lastIndexOf('@');
        if (atIndex === -1) return;

        // Remove @query text and insert MentionNode
        const beforeAt = textContent.slice(0, atIndex);
        const afterCursor = textContent.slice(anchorOffset);

        if (atIndex === 0) {
          // @ was at the start - split into mention + rest
          const restNode = anchorNode.splitText(anchorOffset);
          // anchorNode now contains the @query text, remove it
          anchorNode.remove();
          // Insert mention
          const mentionNode = $createMentionNode(
            user.id,
            user.nickname || user.email || user.id,
          );
          if (restNode) {
            restNode.getParent()?.insertBefore(mentionNode, restNode);
          } else {
            if (beforeAt || afterCursor) {
              const newText = new TextNode(afterCursor);
              const parent = selection.anchor.getNode().getParent();
              parent?.insertAfter(newText);
            }
          }
        } else {
          // @ is in the middle - split into before, @query, after
          const atNode = anchorNode.splitText(atIndex);
          const afterNode = atNode.splitText(anchorOffset - atIndex);
          // Remove the @query node
          atNode.remove();
          // Insert mention node
          const mentionNode = $createMentionNode(
            user.id,
            user.nickname || user.email || user.id,
          );
          // Get parent of afterNode
          const parent = afterNode.getParent();
          if (parent) {
            parent.insertBefore(mentionNode, afterNode);
          }
        }

        // Add a space after the mention
        const sel = $getSelection();
        if ($isRangeSelection(sel)) {
          sel.insertText(' ');
        }
      });

      setShowMenu(false);
    },
    [editor],
  );

  // Keyboard navigation — uses refs to avoid re-registering on every keystroke
  useEffect(() => {
    return editor.registerCommand(
      KEY_DOWN_COMMAND,
      (event: KeyboardEvent) => {
        if (!showMenu) return false;

        const users = filteredUsersRef.current;
        const idx = selectedIndexRef.current;

        if (event.key === 'ArrowDown') {
          event.preventDefault();
          setSelectedIndex((prev) => (prev >= users.length - 1 ? 0 : prev + 1));
          return true;
        }
        if (event.key === 'ArrowUp') {
          event.preventDefault();
          setSelectedIndex((prev) => (prev <= 0 ? users.length - 1 : prev - 1));
          return true;
        }
        if (event.key === 'Enter') {
          event.preventDefault();
          if (users[idx]) {
            insertMention(users[idx]);
          }
          return true;
        }
        if (event.key === 'Escape') {
          setShowMenu(false);
          return true;
        }
        return false;
      },
      COMMAND_PRIORITY_LOW,
    );
  }, [editor, showMenu, insertMention]);

  const handleUserClick = useCallback(
    (user: UserItem) => {
      insertMention(user);
    },
    [insertMention],
  );

  if (!showMenu) return null;

  return (
    <div
      ref={menuRef}
      className="fixed z-50 bg-white border border-stone-200 rounded-lg shadow-lg overflow-hidden"
      style={{ left: position.x, top: position.y, minWidth: 200 }}
    >
      {filteredUsers.length === 0 ? (
        <div className="px-3 py-2 text-xs text-stone-400">无匹配用户</div>
      ) : (
        filteredUsers.map((user, idx) => (
          <button
            key={user.id}
            className={`w-full text-left px-3 py-2 text-xs flex items-center gap-2 transition-colors ${
              idx === selectedIndex ? 'bg-stone-100' : 'hover:bg-stone-50'
            }`}
            onMouseDown={(e) => {
              e.preventDefault();
              handleUserClick(user);
            }}
            onMouseEnter={() => setSelectedIndex(idx)}
          >
            <span className="size-5 rounded-full bg-stone-200 flex items-center justify-center text-[9px] font-semibold text-stone-600 shrink-0">
              {(user.nickname || user.email || '?').slice(0, 1).toUpperCase()}
            </span>
            <div className="flex flex-col min-w-0">
              <span className="text-stone-800 truncate">
                {user.nickname || user.email}
              </span>
              {user.nickname && user.email && (
                <span className="text-[10px] text-stone-400 truncate">
                  {user.email}
                </span>
              )}
            </div>
          </button>
        ))
      )}
    </div>
  );
}
