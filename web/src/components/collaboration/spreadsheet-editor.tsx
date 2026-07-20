/**
 * Spreadsheet editor powered by Univer.
 *
 * Replaces the old HTML table with a full-featured Excel-like spreadsheet:
 * 400+ formulas, formatting, freeze panes, sort/filter, undo/redo, multi-sheet.
 * Collaboration via Yjs CRDT (document-level) through useSpreadsheetCollab hook.
 */
import storage from '@/utils/authorization-util';
import type { ICellData, IWorkbookData, Univer } from '@univerjs/core';
import { UniverSheetsConditionalFormattingPreset } from '@univerjs/preset-sheets-conditional-formatting';
import UniverSheetsConditionalFormattingZhCN from '@univerjs/preset-sheets-conditional-formatting/locales/zh-CN';
import { UniverSheetsCorePreset } from '@univerjs/preset-sheets-core';
import '@univerjs/preset-sheets-core/lib/index.css';
import UniverPresetSheetsCoreZhCN from '@univerjs/preset-sheets-core/locales/zh-CN';
import { UniverSheetsDataValidationPreset } from '@univerjs/preset-sheets-data-validation';
import UniverSheetsDataValidationZhCN from '@univerjs/preset-sheets-data-validation/locales/zh-CN';
import { UniverSheetsDrawingPreset } from '@univerjs/preset-sheets-drawing';
import UniverSheetsDrawingZhCN from '@univerjs/preset-sheets-drawing/locales/zh-CN';
import { UniverSheetsFilterPreset } from '@univerjs/preset-sheets-filter';
import UniverSheetsFilterZhCN from '@univerjs/preset-sheets-filter/locales/zh-CN';
import { UniverSheetsNotePreset } from '@univerjs/preset-sheets-note';
import UniverSheetsNoteZhCN from '@univerjs/preset-sheets-note/locales/zh-CN';
import { UniverSheetsThreadCommentPreset } from '@univerjs/preset-sheets-thread-comment';
import UniverSheetsThreadCommentZhCN from '@univerjs/preset-sheets-thread-comment/locales/zh-CN';
import type { FUniver } from '@univerjs/presets';
import { createUniver, LocaleType } from '@univerjs/presets';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import EditorHeader from './editor-header';
import useSpreadsheetCollab from './use-spreadsheet-collab';
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
  onProviderReady,
}: Props) {
  const content = document.content;

  // Commit any in-progress cell edit and return a fresh workbook snapshot.
  // Univer keeps in-progress edits in an editor overlay (NOT in cellData)
  // until the cell loses focus or Enter is pressed. Without this, saving
  // while a cell is focused would persist a snapshot missing that edit.
  const getLatestSnapshot = useCallback(async () => {
    const api = univerAPIRef.current;
    if (!api) return null;
    const fWorkbook = api.getActiveWorkbook();
    if (!fWorkbook) return null;
    try {
      // Pass true to commit (vs false to cancel). Awaits Univer's commit pipeline.
      await fWorkbook.endEditingAsync(true);
    } catch {
      // ignore — snapshot below is still the best we have
    }
    try {
      return fWorkbook.save() as IWorkbookData;
    } catch {
      return null;
    }
  }, []);

  const {
    workbookData,
    remoteEpoch,
    pushSnapshot,
    saveStatus,
    provider,
    saveToServer,
  } = useSpreadsheetCollab({
    docId: document.id,
    content,
    ydoc: document.ydoc ?? null,
    token,
    apiFetch,
    onUpdate,
    getLatestSnapshot,
    userName: useMemo(() => {
      const userInfo = storage.getUserInfoObject();
      return userInfo?.nickname || userInfo?.email || '';
    }, []),
  });

  const [downloading, setDownloading] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);
  const univerRef = useRef<Univer | null>(null);
  const univerAPIRef = useRef<FUniver | null>(null);

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
  // remote-driven workbookData changes (observer bumped epoch) from local
  // pushSnapshot changes (epoch unchanged). Without this, every useEffect
  // run would set applyEpochRef and silently drop all subsequent local edits.
  const lastSeenEpochRef = useRef<number>(0);

  // Initialize Univer instance (runs once on mount)
  useEffect(() => {
    if (!containerRef.current) return;

    const { univer, univerAPI } = createUniver({
      locale: LocaleType.ZH_CN,
      locales: {
        [LocaleType.ZH_CN]: {
          ...UniverPresetSheetsCoreZhCN,
          ...UniverSheetsDrawingZhCN,
          ...UniverSheetsConditionalFormattingZhCN,
          ...UniverSheetsDataValidationZhCN,
          ...UniverSheetsFilterZhCN,
          ...UniverSheetsNoteZhCN,
          ...UniverSheetsThreadCommentZhCN,
        },
      },
      presets: [
        UniverSheetsCorePreset({
          container: containerRef.current,
        }),
        // ── Free preset suite (Apache-2.0) ───────────────────────────
        // Registered together so newly-imported xlsx features (images,
        // formulas, data validation, comments) have their UI available.
        // Chart is NOT here — Univer's chart plugin is Pro-only.
        // Drawing covers: floating images, image insertion UI.
        UniverSheetsDrawingPreset(),
        // Filter/sort: auto-filter dropdown on header cells.
        UniverSheetsFilterPreset(),
        // Conditional formatting: color scales / data bars / rule-based.
        UniverSheetsConditionalFormattingPreset(),
        // Data validation: dropdowns, number/date/list constraints.
        UniverSheetsDataValidationPreset(),
        // Note: cell hover notes (legacy Excel comment style).
        UniverSheetsNotePreset(),
        // Thread comment: modern threaded comments panel.
        UniverSheetsThreadCommentPreset(),
      ],
    });

    univerRef.current = univer;
    univerAPIRef.current = univerAPI;

    // Create initial workbook with data
    univerAPI.createWorkbook(workbookData);

    // Listen for command events to detect local edits → push to Yjs.
    // applyEpoch guard: only skip events from a remote-update-triggered createWorkbook.
    // No initEpoch check needed — the listener is registered AFTER createWorkbook,
    // so initial workbook creation won't trigger it.
    const disposable = univerAPI.addEvent(
      univerAPI.Event.CommandExecuted,
      () => {
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
      // Do NOT call univer.dispose() here.
      // Univer's internal React root shares the same React reconciler as our
      // app. Calling univer.dispose() triggers root.unmount(), which removes
      // DOM nodes that React's own deletion walk (commitDeletionEffectsOnFiber)
      // will later try to remove — causing a NotFoundError race condition.
      // The old instance is GC'd when refs are nulled; each createUniver()
      // creates an independent UniverInstanceService, so no global state leaks.
      univerRef.current = null;
      univerAPIRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Apply remote workbook data changes to Univer incrementally.
  // Only applies when workbookData changes from a REMOTE Yjs update —
  // local pushSnapshot changes are skipped because Univer is already
  // the source of truth for those.
  useEffect(() => {
    const api = univerAPIRef.current;
    if (!api) return;

    const currentEpoch = remoteEpoch.current;
    const isRemoteUpdate = currentEpoch !== lastSeenEpochRef.current;
    lastSeenEpochRef.current = currentEpoch;

    if (!isRemoteUpdate) {
      // Local pushSnapshot triggered this re-render — Univer is already
      // up to date. Skip apply AND skip setting applyEpochRef (otherwise
      // every subsequent local CommandExecuted would be dropped).
      return;
    }

    // We're applying remote data. Set applyEpoch so synchronous
    // CommandExecuted events from setValue/setRange get skipped.
    applyEpochRef.current = currentEpoch;

    try {
      const fWorkbook = api.getActiveWorkbook();
      if (!fWorkbook) return;

      const currentSnapshot = fWorkbook.save();
      const remoteSnapshot = workbookData;

      // Check for structural changes (sheet add/remove/reorder) — fall back to full recreate
      const currentSheets = Object.keys(currentSnapshot.sheets)
        .sort()
        .join(',');
      const remoteSheets = Object.keys(remoteSnapshot.sheets).sort().join(',');
      if (currentSheets !== remoteSheets) {
        fWorkbook.dispose();
        api.createWorkbook(remoteSnapshot);
        return;
      }

      // Incremental cell-level update per sheet
      for (const sheetId of remoteSnapshot.sheetOrder) {
        const remoteSheet = remoteSnapshot.sheets[sheetId];
        if (!remoteSheet) continue;

        const currentSheet = currentSnapshot.sheets[sheetId];
        const fWorksheet = fWorkbook.getSheetBySheetId(sheetId);
        if (!fWorksheet || !currentSheet) continue;

        // Handle column width changes
        const remoteColData = remoteSheet.columnData;
        const currentColData = currentSheet.columnData;
        if (remoteColData) {
          for (const colStr of Object.keys(remoteColData)) {
            const col = Number(colStr);
            const remoteW = (remoteColData as Record<string, { w?: number }>)[
              colStr
            ]?.w;
            const currentW = (currentColData as Record<string, { w?: number }>)[
              colStr
            ]?.w;
            if (remoteW !== undefined && remoteW !== currentW) {
              try {
                fWorksheet.setColumnWidth(col, remoteW);
              } catch {
                // ignore
              }
            }
          }
        }

        // Handle row height changes
        const remoteRowData = remoteSheet.rowData;
        const currentRowData = currentSheet.rowData;
        if (remoteRowData) {
          for (const rowStr of Object.keys(remoteRowData)) {
            const row = Number(rowStr);
            const remoteH = (remoteRowData as Record<string, { h?: number }>)[
              rowStr
            ]?.h;
            const currentH = (currentRowData as Record<string, { h?: number }>)[
              rowStr
            ]?.h;
            if (remoteH !== undefined && remoteH !== currentH) {
              try {
                fWorksheet.setRowHeight(row, remoteH);
              } catch {
                // ignore
              }
            }
          }
        }

        // Handle sheet name changes
        if (
          remoteSheet.name &&
          currentSheet.name &&
          remoteSheet.name !== currentSheet.name
        ) {
          try {
            fWorksheet.setName(remoteSheet.name);
          } catch {
            // ignore
          }
        }

        // Diff cellData: compare nested { [row]: { [col]: ICellData } }
        const remoteCells = remoteSheet.cellData as
          | Record<string, Record<string, ICellData>>
          | undefined;
        const currentCells = currentSheet.cellData as
          | Record<string, Record<string, ICellData>>
          | undefined;

        if (remoteCells) {
          // Collect all row keys from both remote and current
          const allRows = new Set([
            ...Object.keys(remoteCells),
            ...(currentCells ? Object.keys(currentCells) : []),
          ]);

          for (const rowStr of allRows) {
            const row = Number(rowStr);
            const remoteRow = remoteCells[rowStr];
            const currentRow = currentCells?.[rowStr];

            // If remote has a row that current doesn't, all cells are new
            // If current has a row that remote doesn't, cells were deleted
            const allCols = new Set([
              ...(remoteRow ? Object.keys(remoteRow) : []),
              ...(currentRow ? Object.keys(currentRow) : []),
            ]);

            for (const colStr of allCols) {
              const col = Number(colStr);
              const remoteCell = remoteRow?.[colStr];
              const currentCell = currentRow?.[colStr];

              // Check if cell changed or was added/removed
              const remoteJson = remoteCell ? JSON.stringify(remoteCell) : '';
              const currentJson = currentCell
                ? JSON.stringify(currentCell)
                : '';

              if (remoteJson !== currentJson) {
                try {
                  if (remoteJson === '') {
                    // Cell was deleted — clear it
                    api.syncExecuteCommand(
                      'sheet.command.set-range-values',
                      {
                        range: {
                          startRow: row,
                          startColumn: col,
                          endRow: row + 1,
                          endColumn: col + 1,
                        },
                        value: null,
                      },
                      { onlyLocal: true } as never,
                    );
                  } else {
                    // Cell changed or added — set value
                    const range = fWorksheet.getRange(row, col, 1, 1);
                    range.setValue(remoteCell as ICellData);
                  }
                } catch (e) {
                  console.error(
                    `[SpreadsheetEditor] Failed to apply cell [${row},${col}]:`,
                    e,
                  );
                }
              }
            }
          }
        }
      }
    } catch (e) {
      const msg = (e as Error).message ?? '';
      if (msg.includes('same unit id')) return;
      console.error('[SpreadsheetEditor] Failed to apply remote data:', e);
    }
    // Reset applyEpoch after Univer's command queue drains. Synchronous
    // CommandExecuted events have already fired and been skipped via the
    // epoch check; the small delay also catches any queued microtasks.
    // This is CRITICAL — without the reset, applyEpoch would stay set and
    // block ALL subsequent local edits from being pushed to collaborators.
    setTimeout(() => {
      applyEpochRef.current = -1;
    }, 50);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [workbookData]);

  // Manual save — commit any focused cell first, then grab a fresh snapshot.
  // MUST be async because endEditingAsync awaits Univer's commit pipeline.
  const handleManualSave = useCallback(async () => {
    const fresh = await getLatestSnapshot();
    if (fresh) {
      saveToServer(fresh);
      return;
    }
    // Fallback: use hook's workbookData (may be stale)
    saveToServer(workbookData);
  }, [getLatestSnapshot, saveToServer, workbookData]);

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
        showManualSave={true}
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
