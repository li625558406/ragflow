/**
 * Hook: bridge Univer Docs state ↔ Yjs CRDT ↔ WebSocket.
 *
 * Two modes:
 *   - Collab mode (token): Yjs CRDT sync via WebSocket, 30s auto-save to /ydoc endpoint
 *   - Non-collab mode (no token): 2s debounce auto-save on edit to plain /documents endpoint
 *
 * Uses an epoch counter to prevent dead loops (same pattern as use-spreadsheet-collab):
 *   - Local edit → Univer command → save() → epoch captured → debounce → Y.Map.set()
 *   - Remote edit → Y.Map.observe() → epoch incremented → setDocsData
 *   - pushSnapshot checks epoch: if changed, the trigger was remote, skip
 *
 * Adapted from use-spreadsheet-collab.ts. Removed Sheets-specific:
 * asset URL token injection, legacy content migration, UI state stripping.
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import * as Y from 'yjs';
import {
  base64ToUint8Array,
  CollaborationWebSocketProvider,
  uint8ArrayToBase64,
} from './yjs-provider';

/**
 * Transaction origin tag for local Y.Map.set calls pushed from pushSnapshot /
 * saveToServer / auto-save. The yMap observer checks this origin to distinguish
 * genuine remote updates (origin 'ws-remote', set by the WS provider when it
 * applies incoming update bytes) from local echoes.
 *
 * Without this tag, every local `yMap.set('data', ...)` triggers the observer
 * on the SAME client, which bumps remoteEpoch and routes the change through the
 * useEffect apply path. That apply path then calls `fDoc.save()` and diffs it
 * against our own just-pushed snapshot — racing against Univer's in-flight
 * layout recomputation and reverting legitimate state.
 */
const LOCAL_PUSH_ORIGIN = 'local-push';

/* ── Helpers ── */

function createBlankDocsContent(): Record<string, unknown> {
  // Univer Docs 最小空白文档结构 — createDocument 会补全其他字段
  return {
    document: true,
    body: { blockType: 'paragraph', children: [] },
  };
}

/* ── Hook interface ── */

interface Options {
  docId: string;
  content: Record<string, unknown>;
  ydoc: string | null;
  token?: string;
  userName?: string;
  apiFetch: (url: string, options?: RequestInit) => Promise<Response>;
  onUpdate: () => void;
  /** Commit any in-progress edit and return a fresh document snapshot.
   *  Implementation in editor: `await fDoc.endEditingAsync?.(); return fDoc.save();` */
  getLatestSnapshot?: () => Promise<Record<string, unknown> | null>;
}

interface Return {
  /** Current document data snapshot */
  docsData: Record<string, unknown>;
  /** Current epoch — incremented each time remote data arrives */
  remoteEpoch: React.MutableRefObject<number>;
  /** Push a new document snapshot into Yjs CRDT */
  pushSnapshot: (data: Record<string, unknown>) => void;
  saveStatus: 'idle' | 'saving' | 'saved' | 'error';
  provider: CollaborationWebSocketProvider | null;
  /** Save current document directly to server */
  saveToServer: () => Promise<void>;
}

/* ── Hook ── */

export default function useDocumentCollab({
  docId,
  content,
  ydoc,
  token,
  userName,
  apiFetch,
  onUpdate,
  getLatestSnapshot,
}: Options): Return {
  // Resolve initial data: null → blank. Docs has no legacy format to migrate.
  const [docsData, setDocsData] = useState<Record<string, unknown>>(() => {
    if (!content) return createBlankDocsContent();
    // 判断是否是合法的 Docs content
    if (
      typeof content === 'object' &&
      (content as { document?: boolean }).document
    ) {
      return content;
    }
    return createBlankDocsContent();
  });

  const [saveStatus, setSaveStatus] = useState<Return['saveStatus']>('idle');
  const [provider, setProvider] =
    useState<CollaborationWebSocketProvider | null>(null);

  const yDocRef = useRef<Y.Doc | null>(null);
  const yMapRef = useRef<Y.Map<string> | null>(null);
  const providerRef = useRef<CollaborationWebSocketProvider | null>(null);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const saveTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  // Debounced server save after local edits — ensures edits are persisted
  // within a few seconds instead of waiting for the 30s auto-save interval.
  // Without this, clearing the browser cache could lose up to 30s of edits.
  const saveDebounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const statusResetRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const cancelledRef = useRef(false);
  const docsDataRef = useRef(docsData);
  docsDataRef.current = docsData;
  const apiFetchRef = useRef(apiFetch);
  apiFetchRef.current = apiFetch;
  const getLatestSnapshotRef = useRef(getLatestSnapshot);
  getLatestSnapshotRef.current = getLatestSnapshot;

  // Epoch counter: incremented each time remote data arrives.
  // pushSnapshot captures the epoch; if it changed when debounce fires, skip.
  const remoteEpochRef = useRef(0);

  // Push snapshot to Yjs (debounced, epoch-gated)
  const pushSnapshot = useCallback(
    (data: Record<string, unknown>) => {
      setDocsData(data);
      docsDataRef.current = data;
      const map = yMapRef.current;
      if (!map) return;
      // Collab mode: sync to Yjs with debounce
      const epochAtPush = remoteEpochRef.current;
      if (debounceRef.current) clearTimeout(debounceRef.current);
      debounceRef.current = setTimeout(() => {
        if (remoteEpochRef.current !== epochAtPush) return;
        const m = yMapRef.current;
        const doc = yDocRef.current;
        if (m && doc) {
          // Tag the transaction with LOCAL_PUSH_ORIGIN so the yMap observer
          // (registered below) treats this as a local echo, not a remote
          // update, and does NOT re-apply it through the useEffect apply path.
          doc.transact(() => {
            m.set('data', JSON.stringify(data));
          }, LOCAL_PUSH_ORIGIN);
          // Schedule a debounced server save so edits persist within ~5s
          // instead of waiting for the 30s interval. Skipped if no token
          // (non-collab mode uses saveToServer directly).
          if (yDocRef.current && providerRef.current?.doc) {
            if (saveDebounceRef.current) clearTimeout(saveDebounceRef.current);
            saveDebounceRef.current = setTimeout(() => {
              saveDebounceRef.current = null;
              if (cancelledRef.current || !yDocRef.current) return;
              (async () => {
                try {
                  // Re-fetch snapshot in case the user has started editing
                  // another region since the last pushSnapshot fired (those
                  // edits aren't committed yet, so they're missing from `data`).
                  let dataToSave: Record<string, unknown> = data;
                  const fresh = await getLatestSnapshotRef.current?.();
                  if (fresh) {
                    dataToSave = fresh;
                    // Sync yMap so ydoc_state includes the just-committed edit
                    const mm = yMapRef.current;
                    const dd = yDocRef.current;
                    if (mm && dd) {
                      dd.transact(() => {
                        mm.set('data', JSON.stringify(fresh));
                      }, LOCAL_PUSH_ORIGIN);
                    }
                  }
                  const ydocState = Y.encodeStateAsUpdate(yDocRef.current);
                  const b64 = uint8ArrayToBase64(ydocState);
                  setSaveStatus('saving');
                  apiFetchRef
                    .current(`/api/v1/collaboration/documents/${docId}/ydoc`, {
                      method: 'PUT',
                      headers: { 'Content-Type': 'application/json' },
                      body: JSON.stringify({
                        ydoc_state: b64,
                        content: dataToSave,
                        markdown_content: '',
                      }),
                    })
                    .then((r) => r.json())
                    .then((result) => {
                      if (cancelledRef.current) return;
                      if (result.code === 0) {
                        providerRef.current?.sendFullState();
                        setSaveStatus('saved');
                        onUpdate();
                        if (statusResetRef.current)
                          clearTimeout(statusResetRef.current);
                        statusResetRef.current = setTimeout(() => {
                          if (!cancelledRef.current) setSaveStatus('idle');
                        }, 1500);
                      } else {
                        setSaveStatus('error');
                      }
                    })
                    .catch(() => {
                      if (!cancelledRef.current) setSaveStatus('error');
                    });
                } catch {
                  // ignore encode errors
                }
              })();
            }, 5000);
          }
        }
        debounceRef.current = null;
      }, 300);
    },
    [docId, onUpdate],
  );

  // Save current document to server. Uses getLatestSnapshot to grab the
  // freshest state (commits any in-progress edit via endEditingAsync).
  const saveToServer = useCallback(async () => {
    if (cancelledRef.current) return;
    setSaveStatus('saving');
    try {
      // Grab the latest snapshot via the editor-provided callback.
      // For Docs, the caller is responsible for committing pending edits
      // (endEditingAsync) inside getLatestSnapshot.
      let data: Record<string, unknown> | null = null;
      if (getLatestSnapshotRef.current) {
        data = await getLatestSnapshotRef.current();
      }
      if (!data) {
        data = docsDataRef.current;
      }

      // Collab mode: use /ydoc endpoint with ydoc_state
      // Non-collab mode: use plain /documents endpoint without ydoc_state
      const isCollab = !!yDocRef.current;
      let ydocB64: string | undefined;

      if (isCollab) {
        // Sync yMap with the data BEFORE encoding ydoc_state.
        // Why: when saveToServer is called right after endEditingAsync (manual
        // save with a focused region), pushSnapshot's yMap.set is still in its
        // 300ms debounce — yDoc doesn't yet contain the committed edit, so
        // encoding ydoc_state now would produce stale bytes and lose the edit
        // on next page load.
        const m = yMapRef.current;
        const doc = yDocRef.current;
        if (m && doc) {
          doc.transact(() => {
            m.set('data', JSON.stringify(data));
          }, LOCAL_PUSH_ORIGIN);
        }
        const ydocState = Y.encodeStateAsUpdate(yDocRef.current!);
        ydocB64 = uint8ArrayToBase64(ydocState);
      }

      const endpoint = isCollab
        ? `/api/v1/collaboration/documents/${docId}/ydoc`
        : `/api/v1/collaboration/documents/${docId}`;

      const resp = await apiFetchRef.current(endpoint, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          ...(ydocB64 ? { ydoc_state: ydocB64 } : {}),
          content: data,
          markdown_content: '',
        }),
      });
      const result = await resp.json();
      if (result.code === 0) {
        setSaveStatus('saved');
        providerRef.current?.sendFullState();
        onUpdate();
      } else {
        setSaveStatus('error');
      }
    } catch {
      setSaveStatus('error');
    }
    if (statusResetRef.current) clearTimeout(statusResetRef.current);
    statusResetRef.current = setTimeout(() => {
      if (!cancelledRef.current) setSaveStatus('idle');
    }, 2000);
  }, [docId, onUpdate]);

  // Initialize Yjs + Provider
  useEffect(() => {
    if (!token) return;
    cancelledRef.current = false;

    const yDoc = new Y.Doc();
    const yMap = yDoc.getMap<string>('grid');
    yDocRef.current = yDoc;
    yMapRef.current = yMap;

    // Apply server ydoc state if available (reusing shared helper)
    if (ydoc) {
      try {
        const bytes = base64ToUint8Array(ydoc);
        Y.applyUpdate(yDoc, bytes, 'ws-init');
      } catch (e) {
        console.error('[DocumentCollab] Failed to apply server ydoc:', e);
      }
    }

    // Check if Y.Map already has data from server ydoc
    const existingData = yMap.get('data');
    if (existingData) {
      try {
        const parsed = JSON.parse(existingData) as Record<string, unknown>;
        if (parsed && typeof parsed === 'object') {
          remoteEpochRef.current++;
          setDocsData(parsed);
        }
      } catch {
        // ignore
      }
    } else {
      // Initialize Y.Map with current data.
      yMap.set('data', JSON.stringify(docsDataRef.current));
    }

    // Observe remote changes. Skip echoes from our own LOCAL_PUSH_ORIGIN
    // transactions — those changes are already in Univer (we just pushed them
    // from pushSnapshot) and routing them through the apply path would fight
    // Univer's in-flight layout recomputation.
    const observer = (event: Y.YMapEvent<string>) => {
      if (event.transaction.origin === LOCAL_PUSH_ORIGIN) return;
      const data = yMap.get('data');
      if (data) {
        try {
          const parsed = JSON.parse(data) as Record<string, unknown>;
          if (!parsed || typeof parsed !== 'object') return; // skip malformed remote snapshots
          remoteEpochRef.current++;
          setDocsData(parsed);
        } catch {
          // ignore
        }
      }
    };
    yMap.observe(observer);

    // Create provider and connect
    const wsProvider = new CollaborationWebSocketProvider(yDoc, docId, token);
    providerRef.current = wsProvider;
    setProvider(wsProvider); // trigger re-render so EditorHeader sees provider

    wsProvider.on('sync', (...args: unknown[]) => {
      const isSynced = args[0] as boolean;
      if (isSynced) {
        console.log('[DocumentCollab] synced');
      }
    });

    wsProvider.connect();

    // Set awareness (user name + color) so other collaborators see this user.
    // Always set even if userName is empty — otherwise onopen heartbeat sends nothing
    // and other clients won't see this user online.
    wsProvider.awareness.setLocalState({
      anchorPos: null,
      color: '#958DF1',
      focusPos: null,
      focusing: false,
      name: userName || '匿名用户',
      awarenessData: {},
    });

    // Auto-save every 30s
    saveTimerRef.current = setInterval(() => {
      if (cancelledRef.current) return;
      if (yDocRef.current && providerRef.current?.doc) {
        const currentMap = yMapRef.current;
        if (currentMap) {
          const currentData = currentMap.get('data');
          if (currentData) {
            (async () => {
              if (cancelledRef.current) return;
              setSaveStatus('saving');
              try {
                // Commit any pending edit before saving — otherwise the edit
                // lives only in Univer's editor overlay and the persisted
                // snapshot is missing it.
                let parsed: unknown;
                const fresh = await getLatestSnapshotRef.current?.();
                if (fresh) {
                  parsed = fresh;
                  // Sync yMap so ydoc_state encoding includes the committed edit
                  yDocRef.current!.transact(() => {
                    currentMap.set('data', JSON.stringify(fresh));
                  }, LOCAL_PUSH_ORIGIN);
                } else {
                  parsed = JSON.parse(currentData);
                }
                const ydocState = Y.encodeStateAsUpdate(yDocRef.current!);
                const b64 = uint8ArrayToBase64(ydocState);
                const resp = await apiFetchRef.current(
                  `/api/v1/collaboration/documents/${docId}/ydoc`,
                  {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                      ydoc_state: b64,
                      content: parsed as Record<string, unknown>,
                      markdown_content: '',
                    }),
                  },
                );
                const result = await resp.json();
                if (cancelledRef.current) return;
                if (result.code === 0) {
                  providerRef.current?.sendFullState();
                  setSaveStatus('saved');
                  if (statusResetRef.current)
                    clearTimeout(statusResetRef.current);
                  statusResetRef.current = setTimeout(() => {
                    if (!cancelledRef.current) setSaveStatus('idle');
                  }, 1500);
                }
              } catch {
                if (cancelledRef.current) return;
                setSaveStatus('error');
                if (statusResetRef.current)
                  clearTimeout(statusResetRef.current);
                statusResetRef.current = setTimeout(() => {
                  if (!cancelledRef.current) setSaveStatus('idle');
                }, 2000);
              }
            })();
          }
        }
      }
    }, 30000);

    // Flush pending edits to server when tab is hidden or page is being closed.
    // visibilitychange fires when user switches tab / minimizes browser.
    // pagehide fires on actual page close (more reliable than beforeunload
    // for async fetches on mobile Safari).
    const flushSave = async () => {
      if (cancelledRef.current) return;
      if (!yDocRef.current || !providerRef.current?.doc) return;
      const currentMap = yMapRef.current;
      if (!currentMap) return;
      try {
        // Commit pending edit first so it's included in the snapshot
        let parsed: unknown = null;
        const fresh = await getLatestSnapshotRef.current?.();
        if (fresh) {
          parsed = fresh;
          yDocRef.current!.transact(() => {
            currentMap.set('data', JSON.stringify(fresh));
          }, LOCAL_PUSH_ORIGIN);
        } else {
          const currentData = currentMap.get('data');
          if (!currentData) return;
          parsed = JSON.parse(currentData);
        }
        const ydocState = Y.encodeStateAsUpdate(yDocRef.current);
        const b64 = uint8ArrayToBase64(ydocState);
        // Fire-and-forget; browser may not await full completion on pagehide
        // but the request typically completes for visibilitychange.
        apiFetchRef
          .current(`/api/v1/collaboration/documents/${docId}/ydoc`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              ydoc_state: b64,
              content: parsed as Record<string, unknown>,
              markdown_content: '',
            }),
          })
          .catch(() => {});
        // Also broadcast via WS 'save' — server updates room.full_state and
        // persists on room close, providing a second safety net.
        providerRef.current?.sendFullState();
      } catch {
        // ignore
      }
    };
    const onVisibilityChange = () => {
      if (document.visibilityState === 'hidden') flushSave();
    };
    const onPageHide = () => flushSave();
    document.addEventListener('visibilitychange', onVisibilityChange);
    window.addEventListener('pagehide', onPageHide);

    return () => {
      cancelledRef.current = true;
      // Best-effort flush on unmount too
      flushSave();
      document.removeEventListener('visibilitychange', onVisibilityChange);
      window.removeEventListener('pagehide', onPageHide);
      if (saveTimerRef.current) clearInterval(saveTimerRef.current);
      if (debounceRef.current) clearTimeout(debounceRef.current);
      if (saveDebounceRef.current) clearTimeout(saveDebounceRef.current);
      if (statusResetRef.current) clearTimeout(statusResetRef.current);
      yMap.unobserve(observer);
      wsProvider.disconnect();
      yDoc.destroy();
      yDocRef.current = null;
      yMapRef.current = null;
      providerRef.current = null;
      setProvider(null);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token, docId]);

  return {
    docsData,
    remoteEpoch: remoteEpochRef,
    pushSnapshot,
    saveStatus,
    provider,
    saveToServer,
  };
}
