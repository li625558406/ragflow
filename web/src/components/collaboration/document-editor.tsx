/**
 * Document editor powered by Univer Docs.
 * Replaces the old Lexical implementation.
 * Phase 3: Yjs + WebSocket collab via useDocumentCollab.
 */
import storage from '@/utils/authorization-util';
import type { Univer } from '@univerjs/core';
import type { FUniver } from '@univerjs/presets';
import { createUniver, LocaleType } from '@univerjs/presets';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import EditorHeader from './editor-header';
import { DOCS_LOCALES, DOCS_PRESETS } from './univer-docs-presets';
import useDocumentCollab from './use-document-collab';
import type { CollaborationWebSocketProvider } from './yjs-provider';

interface DocumentData {
  id: string;
  name: string;
  file_type: string;
  file_path?: string;
  content: Record<string, unknown>;
  markdown_content?: string;
  agent_id?: string;
  create_time?: string;
  update_time?: string;
  ydoc?: string | null;
}

interface Props {
  document: DocumentData;
  apiFetch: (url: string, options?: RequestInit) => Promise<Response>;
  onUpdate: () => void;
  token?: string;
  onOpenShare: () => void;
  onProviderReady?: (provider: CollaborationWebSocketProvider | null) => void;
}

export default function DocumentEditor({
  document: doc,
  apiFetch,
  onUpdate,
  token,
  onOpenShare,
  onProviderReady,
}: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const univerRef = useRef<Univer | null>(null);
  const univerAPIRef = useRef<FUniver | null>(null);
  const [downloading, setDownloading] = useState(false);

  // Commit any in-progress edit and return a fresh document snapshot.
  // Univer Docs may keep pending edits in an overlay until the region loses
  // focus; without this, saving while editing would persist a stale snapshot.
  const getLatestSnapshot = useCallback(async () => {
    const api = univerAPIRef.current as any;
    if (!api) return null;
    const fDoc = api.getActiveDocument?.();
    if (!fDoc) return null;
    try {
      return fDoc.save?.() ?? fDoc.getSnapshot?.() ?? null;
    } catch {
      return null;
    }
  }, []);

  const userName = useMemo(() => {
    const userInfo = storage.getUserInfoObject();
    return userInfo?.nickname || userInfo?.email || '';
  }, []);

  const {
    docsData,
    remoteEpoch,
    pushSnapshot,
    saveStatus: collabSaveStatus,
    provider,
    saveToServer,
  } = useDocumentCollab({
    docId: doc.id,
    content: doc.content,
    ydoc: doc.ydoc ?? null,
    token,
    userName,
    apiFetch,
    onUpdate,
    getLatestSnapshot,
  });

  // Report provider readiness to parent so side panels can subscribe to events.
  const onProviderReadyRef = useRef(onProviderReady);
  onProviderReadyRef.current = onProviderReady;
  useEffect(() => {
    onProviderReadyRef.current?.(provider ?? null);
  }, [provider]);

  // Track whether we're currently applying a remote update to Univer.
  // Set to the remote epoch during apply; reset to -1 after apply completes.
  // CommandExecuted events that fire during apply are skipped to avoid echo.
  const applyEpochRef = useRef<number>(-1);
  // Last remote epoch we processed in useEffect — used to distinguish
  // remote-driven docsData changes (observer bumped epoch) from local
  // pushSnapshot changes (epoch unchanged). Without this, every useEffect
  // run would set applyEpochRef and silently drop all subsequent local edits.
  const lastSeenEpochRef = useRef<number>(0);

  // Initialize Univer Docs instance (runs once on mount)
  useEffect(() => {
    if (!containerRef.current) return;
    const { univer, univerAPI } = createUniver({
      locale: LocaleType.ZH_CN,
      locales: { [LocaleType.ZH_CN]: DOCS_LOCALES },
      presets: DOCS_PRESETS(containerRef.current),
    });
    univerRef.current = univer;
    univerAPIRef.current = univerAPI;

    // any cast: Univer Docs createDocument expects IDocumentData; docsData is
    // resolved by the hook (valid content or blank docs).
    (univerAPI as any).createDocument(docsData as any);

    // Listen for command events to detect local edits → push to Yjs.
    // applyEpoch guard: skip events from a remote-update-triggered
    // createDocument so we don't echo remote updates back into the CRDT.
    const disposable = univerAPI.addEvent(
      univerAPI.Event.CommandExecuted,
      () => {
        // Skip events from a remote-update-triggered createDocument
        if (remoteEpoch.current === applyEpochRef.current) return;
        const fDoc = (univerAPI as any).getActiveDocument?.();
        if (fDoc) {
          try {
            const snapshot = fDoc.save?.() ?? fDoc.getSnapshot?.();
            if (snapshot) pushSnapshot(snapshot);
          } catch (e) {
            console.error('[DocumentEditor] save snapshot failed:', e);
          }
        }
      },
    );

    return () => {
      disposable.dispose();
      // 注意：不要调用 univer.dispose() —— 会触发与 React reconciler 的竞争
      // （见同目录 spreadsheet-editor.tsx 的踩坑说明）。靠 GC 回收即可。
      univerRef.current = null;
      univerAPIRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Apply remote docsData changes to Univer Docs.
  // Only applies when docsData changes from a REMOTE Yjs update — local
  // pushSnapshot changes are skipped because Univer is already the source
  // of truth for those.
  useEffect(() => {
    const api = univerAPIRef.current as any;
    if (!api) return;

    const currentEpoch = remoteEpoch.current;
    const isRemoteUpdate = currentEpoch !== lastSeenEpochRef.current;
    lastSeenEpochRef.current = currentEpoch;

    if (!isRemoteUpdate) return;

    applyEpochRef.current = currentEpoch;
    try {
      const fDoc = api.getActiveDocument?.();
      if (!fDoc) {
        api.createDocument(docsData);
        return;
      }
      // Docs 版本：直接替换文档内容。Univer Docs 的 replaceDocument API
      // 若存在则用，否则退化到销毁重建（dispose + createDocument）。
      if (typeof fDoc.replaceDocument === 'function') {
        fDoc.replaceDocument(docsData);
      } else {
        try {
          fDoc.dispose?.();
        } catch {
          /* ignore */
        }
        api.createDocument(docsData);
      }
    } catch (e) {
      console.error('[DocumentEditor] apply remote update failed:', e);
    } finally {
      // Reset applyEpoch after a microtask so synchronous CommandExecuted
      // events from the replacement are skipped, but subsequent user edits
      // flow through.
      queueMicrotask(() => {
        applyEpochRef.current = -1;
      });
    }
  }, [docsData, remoteEpoch, pushSnapshot]);

  // 下载占位（Phase 5 实现）
  const handleDownload = useCallback(async (type: 'docx' | 'pdf') => {
    setDownloading(true);
    try {
      console.log('[DocumentEditor] download placeholder', type);
      window.alert('导出功能将在 Phase 5 实现');
    } finally {
      setDownloading(false);
    }
  }, []);

  return (
    <div className="flex-1 flex flex-col min-w-0 h-full">
      <EditorHeader
        docId={doc.id}
        docName={doc.name}
        saveStatus={collabSaveStatus}
        version={null}
        provider={provider}
        showManualSave={!token}
        onManualSave={saveToServer}
        onDownload={handleDownload}
        downloading={downloading}
        onOpenShare={onOpenShare}
        apiFetch={apiFetch}
        onRenamed={onUpdate}
        fileType="docx"
      />
      <div ref={containerRef} className="flex-1 min-h-0" />
    </div>
  );
}
