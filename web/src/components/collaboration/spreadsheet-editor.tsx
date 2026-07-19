/**
 * Spreadsheet editor powered by Univer.
 *
 * Replaces the old HTML table with a full-featured Excel-like spreadsheet:
 * 400+ formulas, formatting, freeze panes, sort/filter, undo/redo, multi-sheet.
 * Collaboration via Yjs CRDT (document-level) through useSpreadsheetCollab hook.
 */
import type { Univer } from '@univerjs/core';
import { UniverSheetsAdvancedPreset } from '@univerjs/preset-sheets-advanced';
import '@univerjs/preset-sheets-advanced/lib/index.css';
import UniverPresetSheetsAdvancedZhCN from '@univerjs/preset-sheets-advanced/locales/zh-CN';
import { UniverSheetsCorePreset } from '@univerjs/preset-sheets-core';
import '@univerjs/preset-sheets-core/lib/index.css';
import UniverPresetSheetsCoreZhCN from '@univerjs/preset-sheets-core/locales/zh-CN';
import type { FUniver } from '@univerjs/presets';
import { createUniver, LocaleType, mergeLocales } from '@univerjs/presets';
import { useCallback, useEffect, useRef, useState } from 'react';
import EditorHeader from './editor-header';
import useSpreadsheetCollab from './use-spreadsheet-collab';

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
}

const EXT_MAP: Record<string, string> = {
  docx: '.docx',
  pdf: '.pdf',
  xlsx: '.xlsx',
};

export default function SpreadsheetEditor({
  document,
  apiFetch,
  onUpdate,
  token,
  onOpenShare,
}: Props) {
  const content = document.content;

  const {
    workbookData,
    remoteEpoch,
    pushSnapshot,
    saveStatus,
    provider,
    handleManualSave,
  } = useSpreadsheetCollab({
    docId: document.id,
    content,
    ydoc: document.ydoc ?? null,
    token,
    apiFetch,
    onUpdate,
  });

  const [downloading, setDownloading] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);
  const univerRef = useRef<Univer | null>(null);
  const univerAPIRef = useRef<FUniver | null>(null);

  // Epoch captured at Univer init — CommandExecuted events from the initial
  // createWorkbook() should NOT be pushed to Yjs.
  const initEpochRef = useRef<number>(-1);

  // Track the epoch at the time we start applying a remote update,
  // so CommandExecuted events during createWorkbook() can be gated.
  const applyEpochRef = useRef<number>(-1);

  // Initialize Univer instance (runs once on mount)
  useEffect(() => {
    if (!containerRef.current) return;

    const { univer, univerAPI } = createUniver({
      locale: LocaleType.ZH_CN,
      locales: {
        [LocaleType.ZH_CN]: mergeLocales(
          UniverPresetSheetsCoreZhCN,
          UniverPresetSheetsAdvancedZhCN,
        ),
      },
      presets: [
        UniverSheetsCorePreset({
          container: containerRef.current,
        }),
        UniverSheetsAdvancedPreset(),
      ],
    });

    univerRef.current = univer;
    univerAPIRef.current = univerAPI;

    // Capture the epoch so we can skip CommandExecuted from initial createWorkbook
    initEpochRef.current = remoteEpoch.current;

    // Create initial workbook with data
    univerAPI.createWorkbook(workbookData);

    // Listen for command events to detect local edits → push to Yjs.
    // Epoch-gated: only push if no remote update arrived since the last render.
    const disposable = univerAPI.addEvent(
      univerAPI.Event.CommandExecuted,
      () => {
        // Skip events from initial createWorkbook
        if (remoteEpoch.current === initEpochRef.current) return;
        // Skip events from a remote-update-triggered createWorkbook
        if (remoteEpoch.current === applyEpochRef.current) return;

        const fWorkbook = univerAPI.getActiveWorkbook();
        if (fWorkbook) {
          try {
            const snapshot = fWorkbook.save();
            pushSnapshot(snapshot);
          } catch (e) {
            console.error('[SpreadsheetEditor] Failed to save snapshot:', e);
          }
        }
      },
    );

    return () => {
      disposable.dispose();
      univer.dispose();
      // Clean up DOM in case Univer didn't fully remove its elements
      if (containerRef.current) {
        containerRef.current.innerHTML = '';
      }
      univerRef.current = null;
      univerAPIRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Apply remote workbook data changes to Univer.
  // Only runs when workbookData changes from a remote Yjs update.
  useEffect(() => {
    const api = univerAPIRef.current;
    if (!api) return;

    // Record epoch so CommandExecuted handler knows to skip
    applyEpochRef.current = remoteEpoch.current;

    try {
      // Dispose current workbook and recreate with new data.
      // This loses scroll position / selection / undo history, which is an
      // acceptable trade-off for document-level CRDT sync.
      const activeWorkbook = api.getActiveWorkbook();
      if (activeWorkbook) {
        activeWorkbook.dispose();
      }
      api.createWorkbook(workbookData);
    } catch (e) {
      console.error('[SpreadsheetEditor] Failed to apply remote data:', e);
    }
    // NOTE: We intentionally do NOT reset applyEpochRef here.
    // It stays set until the next local edit changes the epoch naturally.
    // This prevents any delayed CommandExecuted from re-pushing.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [workbookData]);

  // Download handler
  const handleDownload = useCallback(
    async (type: 'docx' | 'pdf' | 'xlsx') => {
      setDownloading(true);
      try {
        const resp = await apiFetch(
          `/api/v1/collaboration/documents/${document.id}/download?type=${type}`,
        );
        if (!resp.ok) throw new Error('Download failed');
        const blob = await resp.blob();
        const url = URL.createObjectURL(blob);
        const a = window.document.createElement('a');
        a.href = url;
        a.download = `${document.name}${EXT_MAP[type] ?? '.xlsx'}`;
        a.click();
        URL.revokeObjectURL(url);
      } catch (e) {
        console.error('Download failed:', e);
      } finally {
        setDownloading(false);
      }
    },
    [apiFetch, document.id, document.name],
  );

  return (
    <div className="flex-1 flex flex-col min-h-0 bg-white">
      {/* Header */}
      <EditorHeader
        docId={document.id}
        docName={document.name}
        saveStatus={saveStatus}
        version={null}
        provider={provider}
        showManualSave={!token}
        onManualSave={handleManualSave}
        onDownload={handleDownload}
        downloading={downloading}
        onOpenShare={onOpenShare}
        apiFetch={apiFetch}
        onRenamed={onUpdate}
        fileType="xlsx"
      />

      {/* Univer container — renders its own toolbar, formula bar, sheet tabs, grid */}
      <div ref={containerRef} className="flex-1 min-h-0" />
    </div>
  );
}
