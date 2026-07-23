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
import SidePanelBar, { type PanelKey } from './side-panel-bar';
import { DOCS_LOCALES, DOCS_PRESETS } from './univer-docs-presets';
import useDocumentCollab from './use-document-collab';
import { useUniverExport } from './use-univer-export';
import type { CollaborationWebSocketProvider } from './yjs-provider';

// DocumentFlavor.TRADITIONAL = 1 —— A4 分页 / Word 风格。
// 不设默认会走 MODERN(2) —— 灰色背景、连续无分页。
// 旧文档如果没有这个字段，强制按 Traditional 渲染。
const TRADITIONAL_FLAVOR = 1;
const A4_PAGE_SIZE = { width: 794, height: 1124 }; // PAGE_SIZE.A4 @ 96dpi

function normalizeDocsData(
  data: Record<string, unknown>,
): Record<string, unknown> {
  const style =
    (data.documentStyle as Record<string, unknown> | undefined) ?? {};
  if (style.documentFlavor == null) style.documentFlavor = TRADITIONAL_FLAVOR;
  if (!style.pageSize) style.pageSize = A4_PAGE_SIZE;
  if (style.marginTop == null) style.marginTop = 50;
  if (style.marginBottom == null) style.marginBottom = 50;
  if (style.marginLeft == null) style.marginLeft = 90;
  if (style.marginRight == null) style.marginRight = 90;
  return { ...data, documentStyle: style };
}

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
  /** 恢复完成后回调，从头透传到 VersionHistoryPanel */
  onRestored: () => void;
}

export default function DocumentEditor({
  document: doc,
  apiFetch,
  onUpdate,
  token,
  onOpenShare,
  onProviderReady,
  onRestored,
}: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const univerRef = useRef<Univer | null>(null);
  const univerAPIRef = useRef<FUniver | null>(null);

  // 右侧侧边栏激活面板 (评论/附件/版本/审计) —— 互斥,同一时间只开一个。
  // 默认打开评论面板。
  const [activePanel, setActivePanel] = useState<PanelKey | null>('comments');

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
    createVersion,
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

  const {
    busy: exportBusy,
    exportDocx,
    exportPdf,
  } = useUniverExport({
    docId: doc.id,
    apiFetch,
    univerAPIRef,
  });

  // Report provider readiness to parent so side panels can subscribe to events.
  const onProviderReadyRef = useRef(onProviderReady);
  onProviderReadyRef.current = onProviderReady;
  useEffect(() => {
    onProviderReadyRef.current?.(provider ?? null);
  }, [provider]);

  // When another collaborator restores a version, the server sends force-reload
  // to all room clients. Instead of a full page reload, re-fetch content and
  // remount the editor cleanly via the same onRestored callback used by the
  // restorer's own version-history panel.
  const onRestoredRef = useRef(onRestored);
  onRestoredRef.current = onRestored;
  useEffect(() => {
    if (!provider) return;
    const handler = () => onRestoredRef.current();
    provider.on('force-reload', handler);
    return () => {
      provider.off('force-reload', handler);
    };
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

    // FUniverDocsUIMixin (from @univerjs/docs-ui) registers createUniverDoc on
    // FUniver. docsData is resolved by the hook (valid content or blank docs).
    // 对历史文档做 documentFlavor 兜底（旧数据没有此字段时默认 Traditional A4）。
    (univerAPI as any).createUniverDoc(normalizeDocsData(docsData) as any);

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
        api.createUniverDoc(docsData);
        return;
      }
      // FDocument 没有 replaceDocument/dispose 方法。直接 disposeUnit 会
      // 破坏渲染上下文（this._context.unit 变 null，后续 DocZoomRenderController
      // 等监听器读 unit.zoomRatio 抛 NPE）。
      // 正确做法：调用 doc.command-replace-snapshot 命令，它走 mutation 路径
      // 在原 unit 上替换 body / documentStyle / resources 等。
      const unitId =
        typeof fDoc.getId === 'function'
          ? fDoc.getId()
          : (fDoc as { id?: string }).id;
      try {
        api.executeCommand?.('doc.command-replace-snapshot', {
          unitId,
          snapshot: normalizeDocsData(docsData),
          segmentId: '',
        });
      } catch (e) {
        console.warn(
          '[DocumentEditor] replace-snapshot failed, falling back to recreate:',
          e,
        );
        // 兜底：实在不行再走 disposeUnit + 重建（有 NPE 风险但至少能更新）
        try {
          api.disposeUnit?.(unitId);
        } catch {
          /* ignore */
        }
        api.createUniverDoc(normalizeDocsData(docsData));
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

  const handleDownload = useCallback(
    async (type: 'docx' | 'pdf') => {
      if (type === 'docx') {
        await exportDocx();
      } else {
        await exportPdf();
      }
    },
    [exportDocx, exportPdf],
  );

  // 生成版本按钮：触发 createVersion（唯一写快照的入口）。
  // loading 状态独立于 saveStatus —— createVersion 内部会复用 saveStatus 显示
  // "saving/saved"，但按钮自身的 disabled 需要单独的 flag 防止重复点击。
  const [generatingVersion, setGeneratingVersion] = useState(false);
  const handleGenerateVersion = useCallback(async () => {
    if (generatingVersion) return;
    setGeneratingVersion(true);
    try {
      await createVersion();
    } finally {
      setGeneratingVersion(false);
    }
  }, [generatingVersion, createVersion]);

  return (
    <div className="flex-1 flex flex-col min-w-0 h-full">
      <EditorHeader
        docId={doc.id}
        docName={doc.name}
        saveStatus={collabSaveStatus}
        version={null}
        provider={provider}
        showManualSave
        onManualSave={saveToServer}
        onGenerateVersion={handleGenerateVersion}
        generatingVersion={generatingVersion}
        onDownload={handleDownload}
        downloading={exportBusy}
        onOpenShare={onOpenShare}
        apiFetch={apiFetch}
        onRenamed={onUpdate}
        fileType="docx"
      />
      {/* 主体区:Univer Docs canvas + 右侧侧边栏 (评论/附件/版本/审计) 水平排布 */}
      <div className="flex-1 flex min-h-0">
        <div ref={containerRef} className="flex-1 min-w-0 min-h-0" />
        <SidePanelBar
          docId={doc.id}
          apiFetch={apiFetch}
          activePanel={activePanel}
          onChange={setActivePanel}
          isOwner
          provider={provider ?? null}
          fileType="docx"
          onRestored={onRestored}
        />
      </div>
    </div>
  );
}
