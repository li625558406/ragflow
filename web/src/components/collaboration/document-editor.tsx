import { ListItemNode, ListNode } from '@lexical/list';
import { LexicalComposer } from '@lexical/react/LexicalComposer';
import { useLexicalComposerContext } from '@lexical/react/LexicalComposerContext';
import { ContentEditable } from '@lexical/react/LexicalContentEditable';
import LexicalErrorBoundary from '@lexical/react/LexicalErrorBoundary';
import { HistoryPlugin } from '@lexical/react/LexicalHistoryPlugin';
import { ListPlugin } from '@lexical/react/LexicalListPlugin';
import { RichTextPlugin } from '@lexical/react/LexicalRichTextPlugin';
import { HeadingNode } from '@lexical/rich-text';
import type { EditorState } from 'lexical';
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
}

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
  },
};

function onError(error: Error) {
  console.error('Lexical error:', error);
}

function AutoSavePlugin({
  docId,
  apiFetch,
  onUpdate,
}: {
  docId: string;
  apiFetch: (url: string, options?: RequestInit) => Promise<Response>;
  onUpdate: () => void;
}) {
  const [editor] = useLexicalComposerContext();
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const savingRef = useRef(false);

  useEffect(() => {
    if (!docId) return;

    const unregister = editor.registerUpdateListener(
      ({ editorState }: { editorState: EditorState }) => {
        if (timerRef.current) {
          clearTimeout(timerRef.current);
        }
        timerRef.current = setTimeout(async () => {
          if (savingRef.current) return;
          savingRef.current = true;
          try {
            const json = editorState.toJSON();
            await apiFetch(`/api/v1/collaboration/documents/${docId}`, {
              method: 'PUT',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ content: json }),
            });
            onUpdate();
          } catch (e) {
            console.error('Auto-save failed:', e);
          } finally {
            savingRef.current = false;
          }
        }, 2000);
      },
    );

    return () => {
      unregister();
      if (timerRef.current) clearTimeout(timerRef.current);
    };
  }, [docId, editor, apiFetch, onUpdate]);

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

export default function DocumentEditor({
  document,
  apiFetch,
  onUpdate,
}: Props) {
  const [downloading, setDownloading] = useState(false);

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
    nodes: [HeadingNode, ListNode, ListItemNode],
  };

  return (
    <div className="flex-1 flex flex-col min-h-0 bg-white">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-stone-100">
        <h2 className="text-sm font-semibold text-stone-900 truncate">
          {document.name}
        </h2>
        <div className="flex items-center gap-1.5">
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
      <div className="flex-1 overflow-y-auto">
        <div className="max-w-3xl mx-auto px-6 py-5">
          <LexicalComposer initialConfig={initialConfig}>
            <ToolbarPlugin />
            <div className="relative">
              <RichTextPlugin
                contentEditable={
                  <ContentEditable className="min-h-[400px] outline-none" />
                }
                placeholder={
                  <div className="absolute top-0 left-0 text-stone-400 text-sm pointer-events-none">
                    开始编辑文档内容...
                  </div>
                }
                ErrorBoundary={LexicalErrorBoundary}
              />
              <HistoryPlugin />
              <ListPlugin />
              <AutoSavePlugin
                docId={document.id}
                apiFetch={apiFetch}
                onUpdate={onUpdate}
              />
              <SetInitialStatePlugin content={document.content} />
            </div>
          </LexicalComposer>
        </div>
      </div>
    </div>
  );
}
