/**
 * Lightweight collaborative spreadsheet editor.
 *
 * Pure React + HTML table, no external grid library.
 * Supports: cell editing, keyboard navigation, row/column add/delete, column resize.
 * Syncs via Yjs WebSocket (document-level) through useSpreadsheetCollab hook.
 */
import { Plus, Trash2 } from 'lucide-react';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import EditorHeader from './editor-header';
import useSpreadsheetCollab, {
  type SheetData,
  type SpreadsheetContent,
} from './use-spreadsheet-collab';
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
}

function colLabel(index: number): string {
  let label = '';
  let n = index;
  do {
    label = String.fromCharCode(65 + (n % 26)) + label;
    n = Math.floor(n / 26) - 1;
  } while (n >= 0);
  return label;
}

export default function SpreadsheetEditor({
  document,
  apiFetch,
  onUpdate,
  token,
  onOpenShare,
}: Props) {
  const content = document.content as unknown as SpreadsheetContent;

  const { gridData, setGridData, saveStatus, provider, handleManualSave } =
    useSpreadsheetCollab({
      docId: document.id,
      content,
      ydoc: document.ydoc ?? null,
      token,
      apiFetch,
      onUpdate,
    });

  const [selectedCell, setSelectedCell] = useState<{
    row: number;
    col: number;
  } | null>(null);
  const [editingCell, setEditingCell] = useState<{
    row: number;
    col: number;
  } | null>(null);
  const [editValue, setEditValue] = useState('');
  const [resizingCol, setResizingCol] = useState<number | null>(null);
  const [resizeStart, setResizeStart] = useState(0);
  const [resizeStartWidth, setResizeStartWidth] = useState(0);
  const [downloading, setDownloading] = useState(false);
  const resizeDraftWidth = useRef<number | null>(null);

  const tableRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const editValueRef = useRef(editValue);
  editValueRef.current = editValue;

  const sheet: SheetData = useMemo(
    () =>
      gridData.sheets[gridData.activeSheet] || {
        name: 'Sheet1',
        data: [['']],
        colWidths: [100],
      },
    [gridData],
  );
  const { data, colWidths } = sheet;
  const numRows = data.length;
  const numCols = Math.max(
    data.reduce((max, row) => Math.max(max, row.length), 0),
    1,
  );

  // ── Cell operations ───────────────────────────────────────────────

  const updateCell = useCallback(
    (row: number, col: number, value: string) => {
      const newSheets = gridData.sheets.map((s, i) => {
        if (i !== gridData.activeSheet) return s;
        const newData = s.data.map((r) => [...r]);
        // Ensure row exists
        while (newData.length <= row) newData.push([]);
        // Ensure col exists in row
        while (newData[row].length <= col) newData[row].push('');
        newData[row][col] = value;
        return { ...s, data: newData };
      });
      setGridData({ ...gridData, sheets: newSheets });
    },
    [gridData, setGridData],
  );

  const addRow = useCallback(() => {
    const insertAt = selectedCell ? selectedCell.row + 1 : numRows;
    const newSheets = gridData.sheets.map((s, i) => {
      if (i !== gridData.activeSheet) return s;
      const newRow = new Array(numCols).fill('');
      const newData = [...s.data];
      newData.splice(insertAt, 0, newRow);
      return { ...s, data: newData };
    });
    setGridData({ ...gridData, sheets: newSheets });
  }, [gridData, setGridData, selectedCell, numRows, numCols]);

  const addColumn = useCallback(() => {
    const insertAt = selectedCell ? selectedCell.col + 1 : numCols;
    const newSheets = gridData.sheets.map((s, i) => {
      if (i !== gridData.activeSheet) return s;
      const newData = s.data.map((r) => {
        const nr = [...r];
        nr.splice(insertAt, 0, '');
        return nr;
      });
      const newWidths = [...s.colWidths];
      newWidths.splice(insertAt, 0, 100);
      return { ...s, data: newData, colWidths: newWidths };
    });
    setGridData({ ...gridData, sheets: newSheets });
  }, [gridData, setGridData, selectedCell, numCols]);

  const deleteRow = useCallback(() => {
    if (numRows <= 1) return;
    const deleteAt = selectedCell?.row ?? numRows - 1;
    const newSheets = gridData.sheets.map((s, i) => {
      if (i !== gridData.activeSheet) return s;
      const newData = [...s.data];
      newData.splice(deleteAt, 1);
      return { ...s, data: newData };
    });
    setGridData({ ...gridData, sheets: newSheets });
    setSelectedCell(null);
  }, [gridData, setGridData, selectedCell, numRows]);

  const deleteColumn = useCallback(() => {
    if (numCols <= 1) return;
    const deleteAt = selectedCell?.col ?? numCols - 1;
    const newSheets = gridData.sheets.map((s, i) => {
      if (i !== gridData.activeSheet) return s;
      const newData = s.data.map((r) => {
        const nr = [...r];
        nr.splice(deleteAt, 1);
        return nr;
      });
      const newWidths = [...s.colWidths];
      newWidths.splice(deleteAt, 1);
      return { ...s, data: newData, colWidths: newWidths };
    });
    setGridData({ ...gridData, sheets: newSheets });
    setSelectedCell(null);
  }, [gridData, setGridData, selectedCell, numCols]);

  // ── Keyboard navigation ──────────────────────────────────────────

  const commitEdit = useCallback(() => {
    if (editingCell) {
      updateCell(editingCell.row, editingCell.col, editValueRef.current);
      setEditingCell(null);
    }
  }, [editingCell, updateCell]);

  const handleCellClick = useCallback(
    (row: number, col: number) => {
      if (editingCell) {
        commitEdit();
      }
      setSelectedCell({ row, col });
    },
    [editingCell, commitEdit],
  );

  const handleCellDoubleClick = useCallback(
    (row: number, col: number) => {
      if (editingCell) {
        commitEdit();
      }
      setSelectedCell({ row, col });
      setEditingCell({ row, col });
      setEditValue(data[row]?.[col] ?? '');
    },
    [data, editingCell, commitEdit],
  );

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if (editingCell) {
        if (e.key === 'Enter') {
          e.preventDefault();
          commitEdit();
          // Move down
          const nextRow = Math.min(editingCell.row + 1, numRows - 1);
          setSelectedCell({ row: nextRow, col: editingCell.col });
        } else if (e.key === 'Tab') {
          e.preventDefault();
          commitEdit();
          // Move right (wrap)
          const nextCol = editingCell.col + 1;
          if (nextCol < numCols) {
            setSelectedCell({ row: editingCell.row, col: nextCol });
          } else if (editingCell.row + 1 < numRows) {
            setSelectedCell({ row: editingCell.row + 1, col: 0 });
          }
        } else if (e.key === 'Escape') {
          setEditingCell(null);
        }
        return;
      }

      // Navigation mode (not editing)
      if (!selectedCell) return;

      if (e.key === 'ArrowUp') {
        e.preventDefault();
        setSelectedCell((prev) =>
          prev && prev.row > 0 ? { ...prev, row: prev.row - 1 } : prev,
        );
      } else if (e.key === 'ArrowDown') {
        e.preventDefault();
        setSelectedCell((prev) =>
          prev && prev.row < numRows - 1
            ? { ...prev, row: prev.row + 1 }
            : prev,
        );
      } else if (e.key === 'ArrowLeft') {
        e.preventDefault();
        setSelectedCell((prev) =>
          prev && prev.col > 0 ? { ...prev, col: prev.col - 1 } : prev,
        );
      } else if (e.key === 'ArrowRight') {
        e.preventDefault();
        setSelectedCell((prev) =>
          prev && prev.col < numCols - 1
            ? { ...prev, col: prev.col + 1 }
            : prev,
        );
      } else if (e.key === 'Enter' || e.key === 'F2') {
        e.preventDefault();
        setEditingCell(selectedCell);
        setEditValue(data[selectedCell.row]?.[selectedCell.col] ?? '');
      } else if (e.key === 'Delete' || e.key === 'Backspace') {
        e.preventDefault();
        updateCell(selectedCell.row, selectedCell.col, '');
      } else if (e.key === 'Tab') {
        e.preventDefault();
        const nextCol = selectedCell.col + 1;
        if (nextCol < numCols) {
          setSelectedCell({ ...selectedCell, col: nextCol });
        } else if (selectedCell.row + 1 < numRows) {
          setSelectedCell({ row: selectedCell.row + 1, col: 0 });
        }
      } else if (e.key.length === 1 && !e.ctrlKey && !e.metaKey && !e.altKey) {
        // Start editing with the typed character
        setEditingCell(selectedCell);
        setEditValue(e.key);
      }
    },
    [editingCell, selectedCell, commitEdit, numRows, numCols, data, updateCell],
  );

  // Focus input when editing starts
  useEffect(() => {
    if (editingCell && inputRef.current) {
      inputRef.current.focus();
    }
  }, [editingCell]);

  // ── Column resize ───────────────────────────────────────────────

  const handleResizeStart = useCallback(
    (e: React.MouseEvent, colIndex: number) => {
      e.preventDefault();
      setResizingCol(colIndex);
      setResizeStart(e.clientX);
      setResizeStartWidth(colWidths[colIndex] ?? 100);
    },
    [colWidths],
  );

  useEffect(() => {
    if (resizingCol === null) return;

    const colHeader = tableRef.current?.querySelector(
      `th:nth-child(${resizingCol + 2})`,
    );

    const handleMove = (e: MouseEvent) => {
      const delta = e.clientX - resizeStart;
      const newWidth = Math.max(40, Math.min(500, resizeStartWidth + delta));
      resizeDraftWidth.current = newWidth;
      if (colHeader) {
        (colHeader as HTMLElement).style.width = `${newWidth}px`;
      }
    };

    const handleUp = () => {
      setResizingCol(null);
      // Commit the final width to gridData
      const finalWidth = resizeDraftWidth.current;
      resizeDraftWidth.current = null;
      if (finalWidth != null && finalWidth !== resizeStartWidth) {
        const newSheets = gridData.sheets.map((s, i) => {
          if (i !== gridData.activeSheet) return s;
          const newWidths = [...s.colWidths];
          newWidths[resizingCol] = finalWidth;
          return { ...s, colWidths: newWidths };
        });
        setGridData({ ...gridData, sheets: newSheets });
      }
    };

    window.addEventListener('mousemove', handleMove);
    window.addEventListener('mouseup', handleUp);
    return () => {
      window.removeEventListener('mousemove', handleMove);
      window.removeEventListener('mouseup', handleUp);
    };
  }, [resizingCol, resizeStart, resizeStartWidth, gridData, setGridData]);

  // ── Download handler ─────────────────────────────────────────────

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
        a.download = `${document.name}.xlsx`;
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

  // ── Render ───────────────────────────────────────────────────────

  return (
    <div className="flex-1 flex flex-col min-h-0 bg-white">
      {/* Header */}
      <EditorHeader
        docId={document.id}
        docName={document.name}
        saveStatus={saveStatus}
        version={null}
        provider={provider as CollaborationWebSocketProvider | null}
        showManualSave={!token}
        onManualSave={handleManualSave}
        onDownload={handleDownload}
        downloading={downloading}
        onOpenShare={onOpenShare}
        apiFetch={apiFetch}
        onRenamed={onUpdate}
        fileType="xlsx"
      />

      {/* Toolbar */}
      <div className="flex items-center gap-1 px-3 py-1.5 border-b border-stone-100 text-xs">
        <button
          className="flex items-center gap-1 px-2 py-1 text-stone-600 hover:bg-stone-100 rounded transition-colors"
          onClick={addRow}
        >
          <Plus className="size-3" />行
        </button>
        <button
          className="flex items-center gap-1 px-2 py-1 text-stone-600 hover:bg-stone-100 rounded transition-colors"
          onClick={addColumn}
        >
          <Plus className="size-3" />列
        </button>
        <div className="w-px h-4 bg-stone-200 mx-1" />
        <button
          className="flex items-center gap-1 px-2 py-1 text-stone-600 hover:bg-stone-100 rounded transition-colors disabled:opacity-40"
          onClick={deleteRow}
          disabled={numRows <= 1}
        >
          <Trash2 className="size-3" />行
        </button>
        <button
          className="flex items-center gap-1 px-2 py-1 text-stone-600 hover:bg-stone-100 rounded transition-colors disabled:opacity-40"
          onClick={deleteColumn}
          disabled={numCols <= 1}
        >
          <Trash2 className="size-3" />列
        </button>
      </div>

      {/* Grid */}
      <div
        ref={tableRef}
        className="flex-1 overflow-auto relative outline-none"
        tabIndex={0}
        onKeyDown={handleKeyDown}
      >
        <table className="border-collapse min-w-full">
          <thead className="sticky top-0 z-10">
            <tr>
              <th className="w-10 min-w-10 border-b border-r border-stone-200 bg-stone-50 text-[10px] text-stone-400 font-normal" />
              {Array.from({ length: numCols }, (_, c) => (
                <th
                  key={c}
                  className="border-b border-r border-stone-200 bg-stone-50 text-[10px] text-stone-500 font-medium px-1 text-center relative select-none"
                  style={{ width: colWidths[c] ?? 100, minWidth: 40 }}
                >
                  {colLabel(c)}
                  {/* Resize handle */}
                  <div
                    className="absolute right-0 top-0 bottom-0 w-1 cursor-col-resize hover:bg-blue-400"
                    onMouseDown={(e) => handleResizeStart(e, c)}
                  />
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {Array.from({ length: numRows }, (_, r) => (
              <tr key={r}>
                <td className="border-b border-r border-stone-200 bg-stone-50 text-[10px] text-stone-400 text-center select-none font-normal">
                  {r + 1}
                </td>
                {Array.from({ length: numCols }, (_, c) => {
                  const isSelected =
                    selectedCell?.row === r && selectedCell?.col === c;
                  const isEditing =
                    editingCell?.row === r && editingCell?.col === c;
                  const cellValue = data[r]?.[c] ?? '';
                  return (
                    <td
                      key={c}
                      className={`border-b border-r border-stone-200 relative ${
                        isSelected
                          ? 'outline outline-2 outline-blue-500 outline-offset-[-1px] z-10'
                          : ''
                      } ${r === 0 ? 'bg-stone-50/50' : ''}`}
                      style={{ width: colWidths[c] ?? 100, minWidth: 40 }}
                      onClick={() => handleCellClick(r, c)}
                      onDoubleClick={() => handleCellDoubleClick(r, c)}
                    >
                      {isEditing ? (
                        <input
                          ref={inputRef}
                          className="absolute inset-0 w-full h-full px-1 text-xs outline-none bg-white z-20"
                          value={editValue}
                          onChange={(e) => setEditValue(e.target.value)}
                          onBlur={() => commitEdit()}
                        />
                      ) : (
                        <div className="px-1 py-0.5 text-xs truncate min-h-[22px] whitespace-pre">
                          {cellValue}
                        </div>
                      )}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
