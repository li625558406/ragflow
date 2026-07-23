/**
 * Read-only Univer Sheets preview for version history snapshots.
 *
 * Loads an IWorkbookData snapshot into a fresh Univer instance without
 * wiring up Yjs, save, or edit listeners. Mirrors the Docs preview
 * (univer-version-preview.tsx) but uses Sheets presets and createWorkbook.
 *
 * Only Core + Drawing presets are registered: filter / conditional
 * formatting / data validation / note / thread comment are editor-time
 * features and not needed to render a read-only historical snapshot.
 *
 * Lifecycle: same as the Docs variant — do NOT call univer.dispose(),
 * let GC reclaim it to avoid racing the React reconciler.
 */
import type { Univer } from '@univerjs/core';
import { UniverSheetsCorePreset } from '@univerjs/preset-sheets-core';
import '@univerjs/preset-sheets-core/lib/index.css';
import UniverPresetSheetsCoreZhCN from '@univerjs/preset-sheets-core/locales/zh-CN';
import { UniverSheetsDrawingPreset } from '@univerjs/preset-sheets-drawing';
import UniverSheetsDrawingZhCN from '@univerjs/preset-sheets-drawing/locales/zh-CN';
import type { FUniver } from '@univerjs/presets';
import { createUniver, LocaleType } from '@univerjs/presets';
import { useEffect, useRef } from 'react';

const SHEETS_LOCALES = {
  ...UniverPresetSheetsCoreZhCN,
  ...UniverSheetsDrawingZhCN,
};

const SHEETS_PRESETS = (container: HTMLElement) => [
  UniverSheetsCorePreset({ container }),
  UniverSheetsDrawingPreset(),
];

interface Props {
  content: Record<string, unknown> | null;
}

export default function UniverSpreadsheetVersionPreview({ content }: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const univerRef = useRef<Univer | null>(null);
  const univerAPIRef = useRef<FUniver | null>(null);

  useEffect(() => {
    if (!containerRef.current || !content) return;
    const { univer, univerAPI } = createUniver({
      locale: LocaleType.ZH_CN,
      locales: { [LocaleType.ZH_CN]: SHEETS_LOCALES },
      presets: SHEETS_PRESETS(containerRef.current),
    });
    univerRef.current = univer;
    univerAPIRef.current = univerAPI;

    // URL image downloader — same reason as spreadsheet-editor.tsx:
    // drawing.source URLs need a registered downloader to render.
    if (typeof univerAPI.registerURLImageDownloader === 'function') {
      try {
        univerAPI.registerURLImageDownloader(
          async (url: string): Promise<string> => {
            const res = await fetch(url, { credentials: 'include' });
            if (!res.ok)
              throw new Error(`Failed to load image (${res.status}): ${url}`);
            const blob = await res.blob();
            return URL.createObjectURL(blob);
          },
        );
      } catch (e) {
        console.error(
          '[SpreadsheetVersionPreview] registerURLImageDownloader failed:',
          e,
        );
      }
    }

    try {
      (
        univerAPI as unknown as { createWorkbook: (d: unknown) => unknown }
      ).createWorkbook(content);
    } catch (e) {
      console.error('[SpreadsheetVersionPreview] createWorkbook failed:', e);
    }
    return () => {
      univerRef.current = null;
      univerAPIRef.current = null;
    };
  }, [content]);

  return (
    <div className="relative flex-1 w-full min-h-0 bg-stone-50 flex flex-col">
      {/* 只读提示：独立 Univer 实例没接 Yjs/save，编辑无法保存。 */}
      <div className="shrink-0 px-3 py-1.5 bg-amber-50 border-b border-amber-200 text-[11px] text-amber-800 flex items-center gap-1.5">
        <span className="size-1.5 rounded-full bg-amber-500" />
        只读预览 — 此处的修改不会保存
      </div>
      <div ref={containerRef} className="flex-1 min-h-0 min-w-0 relative" />
    </div>
  );
}
