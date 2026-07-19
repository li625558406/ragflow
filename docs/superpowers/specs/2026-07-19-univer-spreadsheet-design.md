# Univer Spreadsheet Editor Integration Design

**Date**: 2026-07-19
**Status**: Approved
**Scope**: Replace the minimal HTML table spreadsheet editor with Univer open-source spreadsheet framework, keeping existing Yjs collaboration infrastructure.

## Background

The collaboration module's spreadsheet editor (`spreadsheet-editor.tsx`) is a 500-line pure React + HTML table implementation with basic cell editing, keyboard navigation, and row/column operations. It lacks formulas, formatting, multi-cell selection, frozen panes, sorting/filtering, charts, conditional formatting, undo/redo, and multi-sheet tabs — all features expected of an Excel/Google Sheets-like product.

The Word document editor already has full real-time collaboration via Lexical + Yjs + custom WebSocket provider. The goal is to bring the spreadsheet editor to parity in both features and collaboration.

## Decision: Univer Free Packages + Existing Yjs Collaboration

After evaluating Univer, FortuneSheet, AG Grid, and Handsontable, **Univer** was selected:
- MIT-licensed, React 18 native, actively maintained
- Full spreadsheet framework: 400+ formulas, charts, pivot tables, conditional formatting, frozen panes, sorting/filtering
- `IWorkbookData` JSON snapshot format enables clean serialization to Yjs

**No Univer Server deployment. No Pro packages.** Collaboration uses the existing Yjs + `CollaborationWebSocketProvider` infrastructure — the same as the current spreadsheet and the Word editor.

## Architecture

```
SpreadsheetEditor (React)
  ├── Univer (createUniver + Presets)
  │     Core: cell editing, formulas, styles, freeze, sort/filter, undo/redo, multi-sheet
  │     Advanced: charts, pivot tables, conditional formatting, sparklines (watermarked)
  │
  ├── useSpreadsheetCollab (bridge)
  │     Local edit → Univer command → save() → IWorkbookData JSON
  │       → debounce 300ms → Y.Map.set('data', json)
  │       → WebSocket broadcast
  │     Remote update → Y.Map observer → JSON.parse
  │       → univerAPI.createWorkbook(snapshot) if not editing
  │       → mark pending if editing, merge on idle
  │
  └── CollaborationWebSocketProvider (unchanged)
        Yjs CRDT ↔ WebSocket relay ↔ other clients
```

## npm Dependencies

```
@univerjs/presets                     # createUniver factory
@univerjs/preset-sheets-core          # Core spreadsheet features
@univerjs/preset-sheets-advanced      # Charts, pivot tables, conditional formatting
```

## Data Model

### New: IWorkbookData (Univer native format)

```typescript
interface IWorkbookData {
  id: string;
  name: string;
  appVersion: string;
  locale: string;
  styles: Record<string, unknown>;
  sheetOrder: string[];
  sheets: Record<string, IWorksheetData>;
  resources?: Array<{ name: string; data: string }>;
}

interface IWorksheetData {
  id: string;
  name: string;
  tabColor: string;
  hidden: number;
  rowCount: number;
  columnCount: number;
  zoomRatio: number;
  freeze: { startRow: number; startColumn: number; ySplit: number; xSplit: number };
  scrollTop: number;
  scrollLeft: number;
  defaultColumnWidth: number;
  defaultRowHeight: number;
  mergeData: unknown[];
  cellData: Record<string, ICellData>;   // key format: "{row},{col}"
  rowData: Record<number, IRowData>;
  columnData: Record<number, IColumnData>;  // includes width
  showGridlines: number;
}
```

### Legacy: SpreadsheetContent (existing, for migration)

```typescript
interface SpreadsheetContent {
  sheets: SheetData[];
  activeSheet: number;
}

interface SheetData {
  name: string;
  data: string[][];
  colWidths: number[];
}
```

### Migration

On first open of a document with legacy `SpreadsheetContent` format:
1. Convert `SheetData.data[][]` to `IWorksheetData.cellData` (key format `{row},{col}`)
2. Convert `SheetData.colWidths[]` to `IWorksheetData.columnData`
3. Set `sheetOrder` from `sheets[].name`
4. Auto-save the new `IWorkbookData` format to backend
5. New documents always use `IWorkbookData`

## Collaboration Bridge (Document-Level CRDT)

The collaboration model remains **document-level CRDT** — the entire `IWorkbookData` JSON is stored as a string in `Y.Map<string>` key `'data'`. This is the same pattern as the current spreadsheet, just with richer state.

**Why not character-level CRDT**: Spreadsheet state includes styles, formulas, merges, freeze configurations, etc. Character-level CRDT is meaningful for text (Word/Lexical) but not for structured spreadsheet models.

### Local → Remote flow

1. Univer emits command events on any state change
2. Hook calls `fWorkbook.save()` → `IWorkbookData` JSON
3. Debounce 300ms (batch rapid edits)
4. `Y.Map.set('data', JSON.stringify(snapshot))`
5. Yjs `doc.on('update')` → provider sends to WebSocket
6. Auto-save to server every 30s (same as current)

### Remote → Local flow

1. WebSocket receives update → `Y.applyUpdate()`
2. `Y.Map.observe()` fires
3. Parse JSON to `IWorkbookData`
4. Check if user is actively editing:
   - **Not editing**: `univerAPI.createWorkbook(snapshot)` (replace entire workbook)
   - **Editing**: Mark as pending, merge after user finishes editing

## Files to Modify

| File | Change |
|------|--------|
| `web/src/components/collaboration/spreadsheet-editor.tsx` | **Rewrite**: Replace HTML table with Univer `createUniver()`. Remove hand-rolled cell editing, keyboard nav, column resize. Delegate all editing to Univer. Keep `EditorHeader` integration. |
| `web/src/components/collaboration/use-spreadsheet-collab.ts` | **Rewrite**: Adapt from `SpreadsheetContent` to `IWorkbookData`. Add Univer event listeners for change detection. Keep Yjs bridge pattern (Y.Map + debounce + auto-save). Add legacy data migration. |
| `web/package.json` | Add 3 `@univerjs` dependencies |

## Files NOT Modified

- `yjs-provider.ts` — WebSocket provider unchanged
- `collaboration_ws.py` — Backend WebSocket relay unchanged
- `collaboration_api.py` — Document CRUD unchanged (content is JSON column)
- `editor-header.tsx` — Shared header unchanged
- `comment-panel.tsx`, `version-history-panel.tsx`, `share-dialog.tsx` — Unchanged
- Backend DB models — No schema changes (content is MySQL JSON column)

## Risks and Mitigations

| Risk | Mitigation |
|------|------------|
| Univer bundle size (~1MB gzipped) increases frontend load | Only load spreadsheet-editor component when `file_type === 'xlsx'` (already conditionally rendered). Lazy-load Univer packages. |
| Document-level CRDT may cause data loss with simultaneous edits | Same risk as current implementation. Acceptable for typical usage (few collaborators on same spreadsheet). Can upgrade to Univer Server OT later if needed. |
| Legacy data migration edge cases | Migration function handles missing/null fields gracefully. Tested with existing data shapes. |
| Univer CSS conflicts with existing Tailwind styles | Univer renders inside a scoped container div. CSS isolation verified. |
