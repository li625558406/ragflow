/**
 * Hook: bridge spreadsheet grid state ↔ Yjs CRDT ↔ WebSocket.
 *
 * Stores the entire grid JSON as a string inside a Y.Map.
 * Local edits → debounce → yMap.set() → doc.on('update') → provider sends.
 * Remote edits → Y.applyUpdate → yMap.observe() → grid updates.
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import * as Y from 'yjs';
import {
  CollaborationWebSocketProvider,
  uint8ArrayToBase64,
} from './yjs-provider';

export interface SheetData {
  name: string;
  data: string[][];
  colWidths: number[];
}

export interface SpreadsheetContent {
  sheets: SheetData[];
  activeSheet: number;
}

interface Options {
  docId: string;
  content: SpreadsheetContent | null;
  ydoc: string | null;
  token: string | undefined;
  apiFetch: (url: string, options?: RequestInit) => Promise<Response>;
  onUpdate: () => void;
}

interface Return {
  gridData: SpreadsheetContent;
  setGridData: (data: SpreadsheetContent) => void;
  saveStatus: 'idle' | 'saving' | 'saved' | 'error';
  provider: CollaborationWebSocketProvider | null;
  handleManualSave: () => void;
}

const DEFAULT_CONTENT: SpreadsheetContent = {
  sheets: [{ name: 'Sheet1', data: [['']], colWidths: [100] }],
  activeSheet: 0,
};

export default function useSpreadsheetCollab({
  docId,
  content,
  ydoc,
  token,
  apiFetch,
  onUpdate,
}: Options): Return {
  const [gridData, setGridDataState] = useState<SpreadsheetContent>(
    content || DEFAULT_CONTENT,
  );
  const [saveStatus, setSaveStatus] = useState<Return['saveStatus']>('idle');

  const yDocRef = useRef<Y.Doc | null>(null);
  const yMapRef = useRef<Y.Map<string> | null>(null);
  const providerRef = useRef<CollaborationWebSocketProvider | null>(null);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const saveTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const isRemoteUpdate = useRef(false);
  const gridDataRef = useRef(gridData);
  gridDataRef.current = gridData;
  const apiFetchRef = useRef(apiFetch);
  apiFetchRef.current = apiFetch;

  // Wrap setGridData to also push to Yjs
  const setGridData = useCallback((data: SpreadsheetContent) => {
    setGridDataState(data);
    if (!isRemoteUpdate.current && yMapRef.current) {
      // Debounce: batch rapid edits into one Yjs update
      if (debounceRef.current) clearTimeout(debounceRef.current);
      debounceRef.current = setTimeout(() => {
        const map = yMapRef.current;
        if (map) {
          map.set('data', JSON.stringify(data));
        }
        debounceRef.current = null;
      }, 300);
    }
  }, []);

  // Manual save
  const handleManualSave = useCallback(async () => {
    if (!token || !yDocRef.current) return;
    setSaveStatus('saving');
    try {
      const ydocState = Y.encodeStateAsUpdate(yDocRef.current);
      const b64 = uint8ArrayToBase64(ydocState);
      const resp = await apiFetchRef.current(
        `/api/v1/collaboration/documents/${docId}/ydoc`,
        {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            ydoc_state: b64,
            content: gridData,
            markdown_content: '',
          }),
        },
      );
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
    setTimeout(() => setSaveStatus('idle'), 2000);
  }, [token, docId, gridData, onUpdate]);

  // Initialize Yjs + Provider
  useEffect(() => {
    if (!token) return;

    const yDoc = new Y.Doc();
    const yMap = yDoc.getMap<string>('grid');
    yDocRef.current = yDoc;
    yMapRef.current = yMap;

    // Apply server ydoc state if available
    if (ydoc) {
      try {
        const binary = atob(ydoc);
        const bytes = new Uint8Array(binary.length);
        for (let i = 0; i < binary.length; i++) {
          bytes[i] = binary.charCodeAt(i);
        }
        Y.applyUpdate(yDoc, bytes, 'ws-init');
      } catch (e) {
        console.error('[SpreadsheetCollab] Failed to apply server ydoc:', e);
      }
    }

    // Check if Y.Map already has data from server ydoc
    const existingData = yMap.get('data');
    if (existingData) {
      try {
        const parsed = JSON.parse(existingData);
        isRemoteUpdate.current = true;
        setGridDataState(parsed);
        isRemoteUpdate.current = false;
      } catch {
        // ignore
      }
    } else {
      // Initialize Y.Map with current grid data (use ref to avoid stale closure)
      yMap.set('data', JSON.stringify(gridDataRef.current));
    }

    // Observe remote changes
    const observer = () => {
      const data = yMap.get('data');
      if (data) {
        try {
          isRemoteUpdate.current = true;
          setGridDataState(JSON.parse(data));
          isRemoteUpdate.current = false;
        } catch {
          // ignore
        }
      }
    };
    yMap.observe(observer);

    // Create provider and connect
    const provider = new CollaborationWebSocketProvider(yDoc, docId, token);
    providerRef.current = provider;

    provider.on('sync', (...args: unknown[]) => {
      const isSynced = args[0] as boolean;
      if (isSynced) {
        console.log('[SpreadsheetCollab] synced');
      }
    });

    provider.connect();

    // Auto-save every 30s
    saveTimerRef.current = setInterval(() => {
      if (yDocRef.current && providerRef.current?.doc) {
        const currentMap = yMapRef.current;
        if (currentMap) {
          const currentData = currentMap.get('data');
          if (currentData) {
            (async () => {
              setSaveStatus('saving');
              try {
                const ydocState = Y.encodeStateAsUpdate(yDocRef.current!);
                const b64 = btoa(
                  String.fromCharCode(...new Uint8Array(ydocState)),
                );
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
                if (result.code === 0) {
                  providerRef.current?.sendFullState();
                  setSaveStatus('saved');
                  setTimeout(() => setSaveStatus('idle'), 1500);
                }
              } catch {
                setSaveStatus('error');
                setTimeout(() => setSaveStatus('idle'), 2000);
              }
            })();
          }
        }
      }
    }, 30000);

    return () => {
      if (saveTimerRef.current) clearInterval(saveTimerRef.current);
      if (debounceRef.current) clearTimeout(debounceRef.current);
      yMap.unobserve(observer);
      provider.disconnect();
      yDoc.destroy();
      yDocRef.current = null;
      yMapRef.current = null;
      providerRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token, docId]);

  return {
    gridData,
    setGridData,
    saveStatus,
    provider: providerRef.current,
    handleManualSave,
  };
}
