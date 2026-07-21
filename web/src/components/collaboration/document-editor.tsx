/**
 * Document editor powered by Univer Docs.
 * Replaces the old Lexical implementation.
 * Phase 2: local-only — load/save content JSON via /documents PUT.
 * Phase 3 will attach Yjs + WebSocket via useDocumentCollab.
 */
import type { Univer } from '@univerjs/core';
import type { FUniver } from '@univerjs/presets';
import { createUniver, LocaleType } from '@univerjs/presets';
import { useCallback, useEffect, useRef, useState } from 'react';
import EditorHeader from './editor-header';
import { DOCS_LOCALES, DOCS_PRESETS } from './univer-docs-presets';
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

/** Univer Docs content snapshot shape (minimal guard). */
function isDocsContent(c: unknown): c is { document?: boolean } {
  return !!c && typeof c === 'object';
}

function createBlankDocsContent(): Record<string, unknown> {
  // Univer Docs 最小空白文档结构 — createDocument 会补全其他字段
  return { document: true, body: { blockType: 'paragraph', children: [] } };
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
  const apiFetchRef = useRef(apiFetch);
  apiFetchRef.current = apiFetch;
  const [saveStatus, setSaveStatus] = useState<
    'idle' | 'saving' | 'saved' | 'error'
  >('idle');
  const [downloading, setDownloading] = useState(false);

  // Phase 2: 占位 provider（Phase 3 替换为真实 useDocumentCollab 返回值）
  const provider: CollaborationWebSocketProvider | null = null;
  const onProviderReadyRef = useRef(onProviderReady);
  onProviderReadyRef.current = onProviderReady;
  useEffect(() => {
    onProviderReadyRef.current?.(null);
  }, []);

  // 初始化 Univer Docs 实例（仅挂载时跑一次）
  useEffect(() => {
    if (!containerRef.current) return;
    const { univer, univerAPI } = createUniver({
      locale: LocaleType.ZH_CN,
      locales: { [LocaleType.ZH_CN]: DOCS_LOCALES },
      presets: DOCS_PRESETS(containerRef.current),
    });
    univerRef.current = univer;
    univerAPIRef.current = univerAPI;

    const initialContent =
      isDocsContent(doc.content) && doc.content.document
        ? doc.content
        : createBlankDocsContent();
    // any cast: Univer Docs 的 createDocument 期望 IDocumentData，这里用宽松类型
    (univerAPI as any).createDocument(initialContent);

    return () => {
      // 注意：不要调用 univer.dispose() —— 会触发与 React reconciler 的竞争
      // （见同目录 spreadsheet-editor.tsx 的踩坑说明）。靠 GC 回收即可。
      univerRef.current = null;
      univerAPIRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Phase 2 最小保存：手动按钮触发，把当前 JSON PUT 回 /documents/<id>
  const saveToServer = useCallback(async () => {
    const api = univerAPIRef.current as any;
    if (!api) return;
    setSaveStatus('saving');
    try {
      const fDoc = api.getActiveDocument?.();
      const snapshot = fDoc?.save?.() ?? fDoc?.getSnapshot?.();
      if (!snapshot) throw new Error('save() returned empty');
      const resp = await apiFetchRef.current(
        `/api/v1/collaboration/documents/${doc.id}`,
        {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ content: snapshot }),
        },
      );
      const result = await resp.json();
      if (result.code !== 0) throw new Error(result.message || 'save failed');
      setSaveStatus('saved');
      onUpdate();
    } catch (e) {
      console.error('[DocumentEditor] save failed:', e);
      setSaveStatus('error');
    }
  }, [doc.id, onUpdate]);

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
        saveStatus={saveStatus}
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
