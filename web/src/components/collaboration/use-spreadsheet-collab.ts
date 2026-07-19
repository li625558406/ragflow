/**
 * Hook: bridge Univer spreadsheet state ↔ Yjs CRDT ↔ WebSocket.
 *
 * Two modes:
 *   - Collab mode (token): Yjs CRDT sync via WebSocket, 30s auto-save to /ydoc endpoint
 *   - Non-collab mode (no token): 2s debounce auto-save on edit to plain /documents endpoint
 *
 * Uses an epoch counter to prevent dead loops:
 *   - Local edit → Univer command → save() → epoch captured → debounce → Y.Map.set()
 *   - Remote edit → Y.Map.observe() → epoch incremented → setWorkbookData
 *   - pushSnapshot checks epoch: if changed, the trigger was remote, skip
 *
 * UI state (scrollTop, scrollLeft, zoomRatio) is stripped before syncing
 * to avoid broadcasting each user's viewport to all collaborators.
 */
import type { IWorkbookData } from '@univerjs/core';
import { LocaleType } from '@univerjs/core';
import { useCallback, useEffect, useRef, useState } from 'react';
import * as Y from 'yjs';
import {
  base64ToUint8Array,
  CollaborationWebSocketProvider,
  uint8ArrayToBase64,
} from './yjs-provider';

/* ── Legacy data types (for migration from old HTML table format) ── */

interface LegacySheetData {
  name: string;
  data: string[][];
  colWidths: number[];
}

interface LegacySpreadsheetContent {
  sheets: LegacySheetData[];
  activeSheet: number;
}

/* ── Helpers ── */

function isLegacyContent(content: Record<string, unknown>): boolean {
  return Array.isArray((content as Record<string, unknown>).sheets);
}

const APP_VERSION = '0.10.2';

/** Convert old SpreadsheetContent format to IWorkbookData */
function convertLegacyToWorkbookData(
  content: LegacySpreadsheetContent,
): IWorkbookData {
  const sheets: IWorkbookData['sheets'] = {};
  const sheetOrder: string[] = [];

  (content.sheets ?? []).forEach((sheet, idx) => {
    const sheetId = `sheet_${idx}`;
    sheetOrder.push(sheetId);

    // Build cellData: Univer expects nested format { [row]: { [col]: ICellData } }
    const cellData: Record<string, Record<string, { v: string; t: 1 }>> = {};
    (sheet.data ?? []).forEach((row, r) => {
      row.forEach((val, c) => {
        if (val !== '' && val != null) {
          const rowKey = String(r);
          if (!cellData[rowKey]) cellData[rowKey] = {};
          cellData[rowKey][String(c)] = { v: val, t: 1 };
        }
      });
    });

    // Build columnData from colWidths
    const columnData: Record<number, { w: number }> = {};
    (sheet.colWidths ?? []).forEach((w, cIdx) => {
      columnData[cIdx] = { w };
    });

    sheets[sheetId] = {
      id: sheetId,
      name: sheet.name || `Sheet${idx + 1}`,
      tabColor: '',
      hidden: 0,
      rowCount: Math.max((sheet.data ?? []).length, 50),
      columnCount: Math.max(
        (sheet.data ?? []).reduce((max, row) => Math.max(max, row.length), 0),
        26,
      ),
      zoomRatio: 1,
      scrollTop: 0,
      scrollLeft: 0,
      defaultColumnWidth: 100,
      defaultRowHeight: 23,
      freeze: { startRow: -1, startColumn: -1, ySplit: 0, xSplit: 0 },
      mergeData: [],
      cellData,
      rowData: {},
      columnData,
      showGridlines: 1,
      rowHeader: { width: 46 },
      columnHeader: { height: 20 },
      rightToLeft: 0,
    };
  });

  return {
    id: 'workbook',
    name: 'Workbook',
    appVersion: APP_VERSION,
    locale: LocaleType.ZH_CN,
    styles: {},
    sheetOrder,
    sheets,
    resources: [],
  };
}

/** Generate a blank IWorkbookData */
function createBlankWorkbookData(): IWorkbookData {
  const sheetId = 'sheet_0';
  return {
    id: 'workbook',
    name: 'Workbook',
    appVersion: APP_VERSION,
    locale: LocaleType.ZH_CN,
    styles: {},
    sheetOrder: [sheetId],
    sheets: {
      [sheetId]: {
        id: sheetId,
        name: 'Sheet1',
        tabColor: '',
        hidden: 0,
        rowCount: 50,
        columnCount: 26,
        zoomRatio: 1,
        scrollTop: 0,
        scrollLeft: 0,
        defaultColumnWidth: 100,
        defaultRowHeight: 23,
        freeze: { startRow: -1, startColumn: -1, ySplit: 0, xSplit: 0 },
        mergeData: [],
        cellData: {},
        rowData: {},
        columnData: {},
        showGridlines: 1,
        rowHeader: { width: 46 },
        columnHeader: { height: 20 },
        rightToLeft: 0,
      },
    },
    resources: [],
  };
}

/** Strip UI-only state before syncing to Yjs (avoids broadcasting scroll/zoom) */
function stripUIState(snapshot: IWorkbookData): IWorkbookData {
  const cleaned = {
    ...snapshot,
    sheets: {} as IWorkbookData['sheets'],
  };
  for (const [id, sheet] of Object.entries(snapshot.sheets)) {
    cleaned.sheets[id] = {
      ...sheet,
      // These fields are per-user viewport state, not shared data
      scrollTop: 0,
      scrollLeft: 0,
    };
  }
  return cleaned;
}

/* ── Hook interface ── */

interface Options {
  docId: string;
  content: Record<string, unknown> | null;
  ydoc: string | null;
  token: string | undefined;
  userName?: string;
  apiFetch: (url: string, options?: RequestInit) => Promise<Response>;
  onUpdate: () => void;
}

interface Return {
  /** Current workbook data snapshot (IWorkbookData) */
  workbookData: IWorkbookData;
  /** Current epoch — incremented each time remote data arrives. Used by
   *  the editor to detect whether a CommandExecuted is from a local or remote edit. */
  remoteEpoch: React.MutableRefObject<number>;
  /** Push a new workbook data snapshot into the Yjs CRDT layer */
  pushSnapshot: (data: IWorkbookData) => void;
  saveStatus: 'idle' | 'saving' | 'saved' | 'error';
  provider: CollaborationWebSocketProvider | null;
  /** Save arbitrary workbook data directly to server (bypasses workbookDataRef) */
  saveToServer: (data: IWorkbookData) => void;
}

/* ── Hook ── */

export default function useSpreadsheetCollab({
  docId,
  content,
  ydoc,
  token,
  userName,
  apiFetch,
  onUpdate,
}: Options): Return {
  // Resolve initial data: legacy format → migrate, null → blank
  const [workbookData, setWorkbookData] = useState<IWorkbookData>(() => {
    if (!content) return createBlankWorkbookData();
    if (isLegacyContent(content))
      return convertLegacyToWorkbookData(
        content as unknown as LegacySpreadsheetContent,
      );
    return content as unknown as IWorkbookData;
  });

  const [saveStatus, setSaveStatus] = useState<Return['saveStatus']>('idle');
  const [provider, setProvider] =
    useState<CollaborationWebSocketProvider | null>(null);

  const yDocRef = useRef<Y.Doc | null>(null);
  const yMapRef = useRef<Y.Map<string> | null>(null);
  const providerRef = useRef<CollaborationWebSocketProvider | null>(null);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const saveTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const statusResetRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const cancelledRef = useRef(false);
  const workbookDataRef = useRef(workbookData);
  workbookDataRef.current = workbookData;
  const apiFetchRef = useRef(apiFetch);
  apiFetchRef.current = apiFetch;

  // Epoch counter: incremented each time remote data arrives.
  // pushSnapshot captures the epoch; if it changed when debounce fires, skip.
  const remoteEpochRef = useRef(0);

  // Push snapshot to Yjs (debounced, epoch-gated)
  const pushSnapshot = useCallback((data: IWorkbookData) => {
    setWorkbookData(data);
    workbookDataRef.current = data;
    const map = yMapRef.current;
    if (!map) return;
    // Collab mode: sync to Yjs with debounce
    const epochAtPush = remoteEpochRef.current;
    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => {
      if (remoteEpochRef.current !== epochAtPush) return;
      const m = yMapRef.current;
      if (m) {
        m.set('data', JSON.stringify(stripUIState(data)));
      }
      debounceRef.current = null;
    }, 300);
  }, []);

  // Save workbook data to server (accepts fresh data from caller)
  const saveToServer = useCallback(
    async (data: IWorkbookData) => {
      if (cancelledRef.current) return;
      setSaveStatus('saving');
      try {
        // Collab mode: use /ydoc endpoint with ydoc_state
        // Non-collab mode: use plain /documents endpoint without ydoc_state
        const isCollab = !!yDocRef.current;
        let ydocB64: string | undefined;

        if (isCollab) {
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
    },
    [docId, onUpdate],
  );

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
        console.error('[SpreadsheetCollab] Failed to apply server ydoc:', e);
      }
    }

    // Check if Y.Map already has data from server ydoc
    const existingData = yMap.get('data');
    if (existingData) {
      try {
        const parsed = JSON.parse(existingData) as IWorkbookData;
        remoteEpochRef.current++;
        setWorkbookData(parsed);
      } catch {
        // ignore
      }
    } else {
      // Initialize Y.Map with current data
      yMap.set('data', JSON.stringify(workbookDataRef.current));
    }

    // Observe remote changes
    const observer = () => {
      const data = yMap.get('data');
      if (data) {
        try {
          remoteEpochRef.current++;
          setWorkbookData(JSON.parse(data) as IWorkbookData);
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
        console.log('[SpreadsheetCollab] synced');
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
                const ydocState = Y.encodeStateAsUpdate(yDocRef.current!);
                const b64 = uint8ArrayToBase64(ydocState);
                const parsed = JSON.parse(currentData);
                const resp = await apiFetchRef.current(
                  `/api/v1/collaboration/documents/${docId}/ydoc`,
                  {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                      ydoc_state: b64,
                      content: parsed,
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

    return () => {
      cancelledRef.current = true;
      if (saveTimerRef.current) clearInterval(saveTimerRef.current);
      if (debounceRef.current) clearTimeout(debounceRef.current);
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
    workbookData,
    remoteEpoch: remoteEpochRef,
    pushSnapshot,
    saveStatus,
    provider,
    saveToServer,
  };
}
