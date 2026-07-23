/**
 * Read-only Univer Docs preview for version history snapshots.
 *
 * Loads an IDocumentData snapshot into a fresh Univer instance without
 * wiring up Yjs, save, or edit listeners. Edits are technically possible
 * in the canvas but go to a throwaway instance — they cannot be saved and
 * disappear on unmount. A banner makes this explicit so users don't
 * mistake the preview for an editable editor.
 *
 * Why not `pointer-events: none`? Univer's canvas owns its own wheel
 * handler for scrolling/pagination. Blocking pointer events also blocks
 * wheel, so the preview couldn't scroll — see version-history bug report.
 *
 * Lifecycle: a new Univer instance is created on mount and left for GC
 * on unmount. Calling univer.dispose() races with the React reconciler
 * (see document-editor.tsx / spreadsheet-editor.tsx notes in this repo).
 */
import type { Univer } from '@univerjs/core';
import type { FUniver } from '@univerjs/presets';
import { createUniver, LocaleType } from '@univerjs/presets';
import { useEffect, useRef } from 'react';
import { DOCS_LOCALES, DOCS_PRESETS } from './univer-docs-presets';

// DocumentFlavor.TRADITIONAL = 1 — A4 paginated Word-like layout.
// Keep this in sync with document-editor.tsx so historical snapshots
// render with the same page style as the editor.
const TRADITIONAL_FLAVOR = 1;
const A4_PAGE_SIZE = { width: 794, height: 1124 };

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

interface Props {
  content: Record<string, unknown> | null;
}

export default function UniverVersionPreview({ content }: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const univerRef = useRef<Univer | null>(null);
  const univerAPIRef = useRef<FUniver | null>(null);

  useEffect(() => {
    if (!containerRef.current || !content) return;
    const { univer, univerAPI } = createUniver({
      locale: LocaleType.ZH_CN,
      locales: { [LocaleType.ZH_CN]: DOCS_LOCALES },
      presets: DOCS_PRESETS(containerRef.current),
    });
    univerRef.current = univer;
    univerAPIRef.current = univerAPI;
    try {
      (
        univerAPI as unknown as { createUniverDoc: (d: unknown) => unknown }
      ).createUniverDoc(normalizeDocsData(content));
    } catch (e) {
      console.error('[VersionPreview] createUniverDoc failed:', e);
    }
    return () => {
      // Do NOT call univer.dispose() — see file header. Let GC reclaim it.
      univerRef.current = null;
      univerAPIRef.current = null;
    };
  }, [content]);

  return (
    <div className="relative flex-1 w-full min-h-0 bg-stone-50 flex flex-col">
      {/* 只读提示：独立 Univer 实例没有接 Yjs/save，编辑无法保存。
          之前用 pointer-events:none 试图锁只读，但 Univer 自己
          接管 canvas 的 wheel 事件来处理分页滚动，pointer-events:none
          会连滚动一起阻断。改用 banner 显式提示。 */}
      <div className="shrink-0 px-3 py-1.5 bg-amber-50 border-b border-amber-200 text-[11px] text-amber-800 flex items-center gap-1.5">
        <span className="size-1.5 rounded-full bg-amber-500" />
        只读预览 — 此处的修改不会保存
      </div>
      <div ref={containerRef} className="flex-1 min-h-0" />
    </div>
  );
}
