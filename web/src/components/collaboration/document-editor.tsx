import { $isListNode, ListItemNode, ListNode } from '@lexical/list';
import { LexicalComposer } from '@lexical/react/LexicalComposer';
import { useLexicalComposerContext } from '@lexical/react/LexicalComposerContext';
import { ContentEditable } from '@lexical/react/LexicalContentEditable';
import LexicalErrorBoundary from '@lexical/react/LexicalErrorBoundary';
import { HistoryPlugin } from '@lexical/react/LexicalHistoryPlugin';
import { ListPlugin } from '@lexical/react/LexicalListPlugin';
import { RichTextPlugin } from '@lexical/react/LexicalRichTextPlugin';
import { TablePlugin } from '@lexical/react/LexicalTablePlugin';
import {
  $createHeadingNode,
  $isHeadingNode,
  HeadingNode,
} from '@lexical/rich-text';
import {
  $isTableCellNode,
  $isTableNode,
  $isTableRowNode,
  TableCellNode,
  TableNode,
  TableRowNode,
} from '@lexical/table';
import { $getRoot, $isTextNode } from 'lexical';
import { useCallback, useEffect, useRef, useState } from 'react';
import ToolbarPlugin from './toolbar-plugin';

interface DocumentData {
  id: string;
  name: string;
  file_type: string;
  content: Record<string, unknown>;
  markdown_content: string;
}

interface Props {
  document: DocumentData | null;
  apiFetch: (url: string, options?: RequestInit) => Promise<Response>;
  onUpdate: () => void;
  appliedRuleConfig?: Record<string, unknown> | null;
  onRuleApplied?: () => void;
}

type SaveStatus = 'idle' | 'saving' | 'saved' | 'error';

const theme = {
  paragraph: 'mb-2 text-stone-900 text-sm leading-relaxed',
  heading: {
    h1: 'text-xl font-bold text-stone-900 mb-3 mt-4',
    h2: 'text-lg font-semibold text-stone-900 mb-2 mt-3',
    h3: 'text-base font-medium text-stone-900 mb-2 mt-3',
  },
  list: {
    ul: 'list-disc ml-4 mb-2 text-sm text-stone-900',
    ol: 'list-decimal ml-4 mb-2 text-sm text-stone-900',
    listitem: 'mb-1',
  },
  text: {
    bold: 'font-bold',
    italic: 'italic',
    underline: 'underline',
    strikethrough: 'line-through',
    code: 'bg-stone-100 text-amber-700 px-1 py-0.5 rounded text-xs font-mono',
    subscript: 'text-[0.7em] align-sub',
    superscript: 'text-[0.7em] align-super',
  },
  table: 'w-full border-collapse border border-stone-300 my-2 text-sm',
  tableRow: '',
  tableCell: 'border border-stone-300 px-2 py-1 align-top',
  tableCellHeader:
    'border border-stone-300 px-2 py-1 align-top bg-stone-100 font-bold',
};

function onError(error: Error) {
  console.error('Lexical error:', error);
}

interface AutoSavePluginProps {
  docId: string;
  apiFetch: (url: string, options?: RequestInit) => Promise<Response>;
  onUpdate: () => void;
  onSaveStatus: (status: SaveStatus) => void;
  triggerSaveRef: React.MutableRefObject<(() => void) | null>;
}

function AutoSavePlugin({
  docId,
  apiFetch,
  onUpdate,
  onSaveStatus,
  triggerSaveRef,
}: AutoSavePluginProps) {
  const [editor] = useLexicalComposerContext();
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const savingRef = useRef(false);
  const statusTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const doSave = useCallback(async () => {
    if (savingRef.current) return;
    savingRef.current = true;
    onSaveStatus('saving');
    try {
      const editorState = editor.getEditorState();
      const json = editorState.toJSON();

      // Convert Lexical state to markdown text for file generation
      let markdownContent = '';
      editorState.read(() => {
        const root = $getRoot();
        const lines: string[] = [];
        for (const child of root.getChildren()) {
          const text = child.getTextContent();
          if ($isHeadingNode(child)) {
            const tag = child.getTag();
            const prefix = { h1: '# ', h2: '## ', h3: '### ' }[tag] || '';
            lines.push(prefix + text);
          } else if ($isListNode(child)) {
            const isBullet = child.getListType() === 'bullet';
            const listItems = child.getChildren();
            for (const item of listItems) {
              lines.push((isBullet ? '- ' : '1. ') + item.getTextContent());
            }
          } else if ($isTableNode(child)) {
            const rows = child.getChildren();
            const mdRows: string[] = [];
            for (const row of rows) {
              if ($isTableRowNode(row)) {
                const cells = row.getChildren();
                const cellTexts = cells.map((cell) => {
                  if ($isTableCellNode(cell)) {
                    return cell.getTextContent().replace(/\n/g, ' ').trim();
                  }
                  return '';
                });
                mdRows.push('| ' + cellTexts.join(' | ') + ' |');
              }
            }
            if (mdRows.length > 0) {
              // Header row
              lines.push(mdRows[0]);
              // Separator row
              const colCount = (mdRows[0].match(/\|/g) || []).length - 1;
              if (colCount > 0) {
                lines.push('|' + ' --- |'.repeat(colCount));
              }
              // Data rows
              for (let i = 1; i < mdRows.length; i++) {
                lines.push(mdRows[i]);
              }
            }
          } else {
            lines.push(text);
          }
        }
        markdownContent = lines.join('\n');
      });

      await apiFetch(`/api/v1/collaboration/documents/${docId}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          content: json,
          markdown_content: markdownContent,
        }),
      });
      onSaveStatus('saved');
      onUpdate();
      // Reset to idle after 2s
      if (statusTimerRef.current) clearTimeout(statusTimerRef.current);
      statusTimerRef.current = setTimeout(() => onSaveStatus('idle'), 2000);
    } catch (e) {
      console.error('Save failed:', e);
      onSaveStatus('error');
      if (statusTimerRef.current) clearTimeout(statusTimerRef.current);
      statusTimerRef.current = setTimeout(() => onSaveStatus('idle'), 3000);
    } finally {
      savingRef.current = false;
    }
  }, [docId, editor, apiFetch, onUpdate, onSaveStatus]);

  useEffect(() => {
    triggerSaveRef.current = doSave;
  }, [doSave, triggerSaveRef]);

  useEffect(() => {
    if (!docId) return;

    const unregister = editor.registerUpdateListener(() => {
      if (timerRef.current) {
        clearTimeout(timerRef.current);
      }
      timerRef.current = setTimeout(() => {
        doSave();
      }, 2000);
    });

    return () => {
      unregister();
      if (timerRef.current) clearTimeout(timerRef.current);
      if (statusTimerRef.current) clearTimeout(statusTimerRef.current);
    };
  }, [docId, editor, doSave]);

  return null;
}

function SetInitialStatePlugin({
  content,
}: {
  content: Record<string, unknown> | undefined;
}) {
  const [editor] = useLexicalComposerContext();

  useEffect(() => {
    if (content && content.root) {
      try {
        const editorState = editor.parseEditorState(JSON.stringify(content));
        editor.setEditorState(editorState);
      } catch (e) {
        console.error('Failed to set initial editor state:', e);
      }
    }
  }, [editor, content]);

  return null;
}

function setCssProperty(
  style: string,
  property: string,
  value: string,
): string {
  const regex = new RegExp(`${property}:\\s*[^;]+;?`, 'i');
  const newEntry = `${property}: ${value};`;
  if (regex.test(style)) {
    return style.replace(regex, newEntry);
  }
  const sep = style && !style.endsWith(';') ? ';' : '';
  return style + sep + newEntry;
}

interface StyleRuleConfig {
  name: string;
  pattern: string;
  fontFamily: string;
  fontSize: number;
  fontColor: string;
  alignment: string;
  bold: boolean;
  heading: string;
}

function matchParagraphStyle(
  text: string,
  rules: StyleRuleConfig[],
): StyleRuleConfig | null {
  for (const rule of rules) {
    try {
      if (new RegExp(rule.pattern).test(text)) {
        return rule;
      }
    } catch {
      // Invalid regex, skip
    }
  }
  return null;
}

function FormatApplyPlugin({
  config,
  onApplied,
}: {
  config: Record<string, unknown> | undefined | null;
  onApplied?: () => void;
}) {
  const [editor] = useLexicalComposerContext();
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    if (!config) return;

    const styleRules: StyleRuleConfig[] | undefined = Array.isArray(
      config.rules,
    )
      ? (config.rules as StyleRuleConfig[])
      : undefined;

    editor.update(() => {
      const root = $getRoot();
      // Snapshot paragraph list before mutation — replace() modifies the tree
      const paragraphs = [...root.getChildren()];
      for (const paragraph of paragraphs) {
        // Skip table nodes — they have a different internal structure
        if ($isTableNode(paragraph)) {
          continue;
        }
        const text = paragraph.getTextContent();
        const matched = styleRules
          ? matchParagraphStyle(text, styleRules)
          : null;

        const fontFamily = matched?.fontFamily || (config.font_name as string);
        const fontSize = matched?.fontSize || (config.font_size as number);
        const fontColor = matched?.fontColor;
        const bold = matched?.bold;
        const heading = matched?.heading;
        const alignment = matched?.alignment;

        // Snapshot text children — append() moves nodes between parents
        const textNodes = [...paragraph.getChildren()];
        for (const node of textNodes) {
          if ($isTextNode(node)) {
            let style = node.getStyle();
            if (fontFamily) {
              style = setCssProperty(style, 'font-family', String(fontFamily));
            }
            if (fontSize) {
              style = setCssProperty(style, 'font-size', `${fontSize}pt`);
            }
            if (fontColor) {
              style = setCssProperty(style, 'color', String(fontColor));
            }
            if (typeof bold === 'boolean') {
              if (bold) {
                const fmt = node.getFormat();
                node.setFormat(fmt === '' ? 'bold' : `${fmt} bold`);
              }
            }
            node.setStyle(style);
          }
        }

        // Set alignment on paragraph
        if (alignment && 'setFormat' in paragraph) {
          (paragraph as { setFormat: (f: string) => void }).setFormat(
            alignment as string,
          );
        }

        // Convert to heading if needed
        if (
          heading &&
          (heading === 'h1' || heading === 'h2' || heading === 'h3')
        ) {
          const needsConvert =
            !$isHeadingNode(paragraph) || paragraph.getTag() !== heading;
          if (needsConvert) {
            const headingNode = $createHeadingNode(heading);
            // Snapshot children before moving — append() removes from source
            const childrenToMove = [...paragraph.getChildren()];
            for (const child of childrenToMove) {
              headingNode.append(child);
            }
            paragraph.replace(headingNode);
          }
        }
      }
    });

    if (timerRef.current) clearTimeout(timerRef.current);
    timerRef.current = setTimeout(() => onApplied?.(), 300);

    return () => {
      if (timerRef.current) clearTimeout(timerRef.current);
    };
  }, [config, editor, onApplied]);

  return null;
}

export default function DocumentEditor({
  document,
  apiFetch,
  onUpdate,
  appliedRuleConfig,
  onRuleApplied,
}: Props) {
  const [downloading, setDownloading] = useState(false);
  const [saveStatus, setSaveStatus] = useState<SaveStatus>('idle');
  const triggerSaveRef = useRef<(() => void) | null>(null);

  const handleDownload = useCallback(
    async (type: 'docx' | 'pdf') => {
      if (!document || downloading) return;
      setDownloading(true);
      try {
        const resp = await apiFetch(
          `/api/v1/collaboration/documents/${document.id}/download?type=${type}`,
        );
        if (resp.ok) {
          const blob = await resp.blob();
          const url = URL.createObjectURL(blob);
          const a = window.document.createElement('a');
          a.href = url;
          a.download = `${document.name}.${type}`;
          a.click();
          URL.revokeObjectURL(url);
        }
      } catch (e) {
        console.error('下载失败:', e);
      } finally {
        setDownloading(false);
      }
    },
    [document, downloading, apiFetch],
  );

  const handleSave = useCallback(() => {
    triggerSaveRef.current?.();
  }, []);

  if (!document) {
    return (
      <div className="flex-1 flex items-center justify-center bg-stone-50">
        <div className="text-center text-stone-400">
          <svg
            className="w-12 h-12 mx-auto mb-3 text-stone-300"
            fill="none"
            stroke="currentColor"
            viewBox="0 0 24 24"
          >
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              strokeWidth={1.5}
              d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z"
            />
          </svg>
          <p className="text-sm">请从左侧选择一个文档</p>
        </div>
      </div>
    );
  }

  const initialConfig = {
    namespace: `collab-doc-${document.id}`,
    theme,
    onError,
    nodes: [
      HeadingNode,
      ListNode,
      ListItemNode,
      TableNode,
      TableCellNode,
      TableRowNode,
    ],
  };

  const saveLabel = {
    idle: '保存',
    saving: '保存中...',
    saved: '已保存 ✓',
    error: '保存失败',
  }[saveStatus];

  return (
    <div className="flex-1 flex flex-col min-h-0 bg-white">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-stone-100">
        <h2 className="text-sm font-semibold text-stone-900 truncate">
          {document.name}
        </h2>
        <div className="flex items-center gap-1.5">
          <button
            className={`px-3 py-1.5 text-xs font-medium rounded-lg transition-colors disabled:opacity-50 ${
              saveStatus === 'saved'
                ? 'text-emerald-600 bg-emerald-50'
                : saveStatus === 'saving'
                  ? 'text-amber-600 bg-amber-50'
                  : saveStatus === 'error'
                    ? 'text-red-600 bg-red-50'
                    : 'text-indigo-600 hover:bg-indigo-50'
            }`}
            onClick={handleSave}
            disabled={saveStatus === 'saving'}
          >
            {saveLabel}
          </button>
          <div className="w-px h-4 bg-stone-200" />
          <button
            className="px-3 py-1.5 text-xs font-medium text-indigo-600 hover:bg-indigo-50 rounded-lg transition-colors disabled:opacity-50"
            onClick={() => handleDownload('docx')}
            disabled={downloading}
          >
            .docx
          </button>
          <button
            className="px-3 py-1.5 text-xs font-medium text-indigo-600 hover:bg-indigo-50 rounded-lg transition-colors disabled:opacity-50"
            onClick={() => handleDownload('pdf')}
            disabled={downloading}
          >
            .pdf
          </button>
        </div>
      </div>

      {/* Editor */}
      <div className="flex-1 overflow-y-auto bg-stone-100/60">
        <LexicalComposer initialConfig={initialConfig}>
          {/* Sticky toolbar — full width, outside max-w constraint */}
          <div className="sticky top-0 z-10 bg-white border-b border-stone-200">
            <div className="max-w-3xl mx-auto px-6">
              <ToolbarPlugin />
            </div>
          </div>
          {/* Content — white paper card on gray background */}
          <div className="max-w-3xl mx-auto px-6 py-5">
            <div className="bg-white rounded-xl border border-stone-200 shadow-sm p-6 relative">
              <RichTextPlugin
                contentEditable={
                  <ContentEditable className="min-h-[400px] outline-none" />
                }
                placeholder={
                  <div className="absolute top-6 left-6 text-stone-400 text-sm pointer-events-none">
                    开始编辑文档内容...
                  </div>
                }
                ErrorBoundary={LexicalErrorBoundary}
              />
              <HistoryPlugin />
              <ListPlugin />
              <TablePlugin
                hasCellMerge={false}
                hasCellBackgroundColor={false}
                hasTabHandler
                hasHorizontalScroll
              />
              <AutoSavePlugin
                docId={document.id}
                apiFetch={apiFetch}
                onUpdate={onUpdate}
                onSaveStatus={setSaveStatus}
                triggerSaveRef={triggerSaveRef}
              />
              <SetInitialStatePlugin content={document.content} />
              <FormatApplyPlugin
                config={appliedRuleConfig}
                onApplied={onRuleApplied}
              />
            </div>
          </div>
        </LexicalComposer>
      </div>
    </div>
  );
}
