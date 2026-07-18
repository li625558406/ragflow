import storage from '@/utils/authorization-util';
import notification from '@/utils/notification';
import { CodeHighlightNode, CodeNode } from '@lexical/code';
import { AutoLinkNode, LinkNode } from '@lexical/link';
import { $isListNode, ListItemNode, ListNode } from '@lexical/list';
import { CheckListPlugin } from '@lexical/react/LexicalCheckListPlugin';
import { CollaborationPlugin } from '@lexical/react/LexicalCollaborationPlugin';
import { LexicalComposer } from '@lexical/react/LexicalComposer';
import { useLexicalComposerContext } from '@lexical/react/LexicalComposerContext';
import { ContentEditable } from '@lexical/react/LexicalContentEditable';
import LexicalErrorBoundary from '@lexical/react/LexicalErrorBoundary';
import { HistoryPlugin } from '@lexical/react/LexicalHistoryPlugin';
import { LinkPlugin } from '@lexical/react/LexicalLinkPlugin';
import { ListPlugin } from '@lexical/react/LexicalListPlugin';
import { RichTextPlugin } from '@lexical/react/LexicalRichTextPlugin';
import { TablePlugin } from '@lexical/react/LexicalTablePlugin';
import {
  $createHeadingNode,
  $isHeadingNode,
  HeadingNode,
  QuoteNode,
} from '@lexical/rich-text';
import {
  $isTableCellNode,
  $isTableNode,
  $isTableRowNode,
  TableCellNode,
  TableNode,
  TableRowNode,
} from '@lexical/table';
import { $getRoot, $isTextNode, ElementNode } from 'lexical';
import { useCallback, useEffect, useRef, useState } from 'react';
import * as Y from 'yjs';
import EditorHeader from './editor-header';
import MentionPlugin from './mention-plugin';
import { $isCalloutNode, CalloutNode } from './nodes/callout-node';
import { $isImageNode, ImageNode } from './nodes/image-node';
import { MathNode } from './nodes/math-node';
import { MentionNode } from './nodes/mention-node';
import ToolbarPlugin from './toolbar-plugin';
import {
  CollaborationWebSocketProvider,
  uint8ArrayToBase64,
} from './yjs-provider';

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
  /** Raw JWT token for WebSocket auth (enables real-time collab) */
  token?: string;
  onOpenShare: () => void;
  /** Called with the provider instance for external awareness access */
  onProviderReady?: (provider: CollaborationWebSocketProvider | null) => void;
}

type SaveStatus = 'idle' | 'saving' | 'saved' | 'error';

const theme = {
  paragraph: 'mb-2 text-stone-900 text-sm leading-relaxed',
  quote: 'border-l-2 border-stone-300 pl-4 italic text-stone-600 my-2 text-sm',
  heading: {
    h1: 'text-xl font-bold text-stone-900 mb-3 mt-4',
    h2: 'text-lg font-semibold text-stone-900 mb-2 mt-3',
    h3: 'text-base font-medium text-stone-900 mb-2 mt-3',
  },
  list: {
    ul: 'list-disc ml-4 mb-2 text-sm text-stone-900',
    ol: 'list-decimal ml-4 mb-2 text-sm text-stone-900',
    listitem: 'mb-1',
    checklist: 'list-none ml-4 mb-2 text-sm text-stone-900',
    listitemChecked: 'line-through text-stone-400',
    listitemUnchecked: '',
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
  code: 'bg-stone-900 text-green-300 px-3 py-2 rounded-lg my-2 text-sm font-mono block overflow-x-auto whitespace-pre-wrap',
  link: 'text-blue-600 underline cursor-pointer hover:text-blue-800',
  image: 'max-w-full h-auto rounded-lg my-2',
};

function onError(error: Error) {
  console.error('Lexical error:', error);
}

/** Convert Lexical editor state to markdown text. */
// eslint-disable-next-line @typescript-eslint/no-explicit-any
function lexicalStateToMarkdown(editorState: any): string {
  const lines: string[] = [];
  editorState.read(() => {
    const root = $getRoot();
    for (const child of root.getChildren()) {
      const text = child.getTextContent();
      if ($isHeadingNode(child)) {
        const tag = child.getTag();
        const prefix =
          ({ h1: '# ', h2: '## ', h3: '### ' } as Record<string, string>)[
            tag
          ] || '';
        lines.push(prefix + text);
      } else if ($isListNode(child)) {
        const listType = child.getListType();
        const listItems = child.getChildren();
        for (const item of listItems) {
          if (listType === 'check') {
            const checked = (item as ListItemNode).getChecked?.() ?? false;
            lines.push((checked ? '- [x] ' : '- [ ] ') + item.getTextContent());
          } else {
            lines.push(
              (listType === 'bullet' ? '- ' : '1. ') + item.getTextContent(),
            );
          }
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
          lines.push(mdRows[0]);
          const colCount = (mdRows[0].match(/\|/g) || []).length - 1;
          if (colCount > 0) {
            lines.push('|' + ' --- |'.repeat(colCount));
          }
          for (let i = 1; i < mdRows.length; i++) {
            lines.push(mdRows[i]);
          }
        }
      } else if ($isCalloutNode(child)) {
        const calloutType = child.__calloutType;
        const emoji =
          { info: '💡', warning: '⚠️', tip: '✅', danger: '🚫' }[calloutType] ||
          '';
        lines.push(`:::${calloutType} ${emoji}`);
        lines.push(text);
        lines.push(':::');
      } else if ($isImageNode(child)) {
        lines.push(`![${child.__altText || ''}](${child.__src})`);
      } else if (child.getType() === 'code') {
        const codeChildren = (child as ElementNode).getChildren();
        const codeLines: string[] = [];
        for (const codeChild of codeChildren) {
          codeLines.push(codeChild.getTextContent());
        }
        const language =
          ((child as unknown as Record<string, unknown>)
            .__language as string) || '';
        lines.push('```' + language);
        lines.push(codeLines.join('\n'));
        lines.push('```');
      } else {
        lines.push(text);
      }
    }
  });
  return lines.join('\n');
}

/**
 * CollabSavePlugin — handles periodic HTTP persistence + sendFullState for collab mode.
 * The actual real-time sync + cursors are handled by CollaborationPlugin (from @lexical/react).
 * This plugin only manages saving to the server.
 */
interface CollabSavePluginProps {
  providerRef: React.MutableRefObject<CollaborationWebSocketProvider | null>;
  docId: string;
  apiFetch: (url: string, options?: RequestInit) => Promise<Response>;
  onUpdate: () => void;
  onSaveStatus: (status: SaveStatus) => void;
  onProviderReady?: (provider: CollaborationWebSocketProvider | null) => void;
  triggerSaveRef: React.MutableRefObject<(() => void) | null>;
}

function CollabSavePlugin({
  providerRef,
  docId,
  apiFetch,
  onUpdate,
  onSaveStatus,
  onProviderReady,
  triggerSaveRef,
}: CollabSavePluginProps) {
  const [editor] = useLexicalComposerContext();
  const saveTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const statusTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const savingRef = useRef(false);
  const apiFetchRef = useRef(apiFetch);
  apiFetchRef.current = apiFetch;
  const onUpdateRef = useRef(onUpdate);
  onUpdateRef.current = onUpdate;

  const doSave = useCallback(() => {
    const provider = providerRef.current;
    if (!provider || savingRef.current) {
      console.log(
        '[CollabSave] skip save: provider=',
        !!provider,
        'saving=',
        savingRef.current,
      );
      return;
    }
    savingRef.current = true;
    onSaveStatus('saving');

    // Access the Y.Doc from the provider
    const doc = provider.doc;
    const ydocState = Y.encodeStateAsUpdate(doc);
    const ydocB64 = uint8ArrayToBase64(ydocState);

    const editorState = editor.getEditorState();
    const json = editorState.toJSON();
    const markdownContent = lexicalStateToMarkdown(editorState);
    console.log(
      '[CollabSave] content keys:',
      Object.keys(json),
      'root children:',
      json.root?.children?.length,
      'first child type:',
      json.root?.children?.[0]?.type,
    );

    apiFetchRef
      .current(`/api/v1/collaboration/documents/${docId}/ydoc`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          ydoc_state: ydocB64,
          content: json,
          markdown_content: markdownContent,
        }),
      })
      .then(() => {
        console.log(
          '[CollabSave] save success, docId=',
          docId,
          'ydocB64 len=',
          ydocB64.length,
        );
        onSaveStatus('saved');
        onUpdateRef.current();
        provider.sendFullState();
        if (statusTimerRef.current) clearTimeout(statusTimerRef.current);
        statusTimerRef.current = setTimeout(() => onSaveStatus('idle'), 2000);
      })
      .catch((e) => {
        console.error('[CollabSave] save FAILED:', e);
        onSaveStatus('error');
        if (statusTimerRef.current) clearTimeout(statusTimerRef.current);
        statusTimerRef.current = setTimeout(() => onSaveStatus('idle'), 3000);
      })
      .finally(() => {
        savingRef.current = false;
      });
  }, [docId, editor, onSaveStatus, providerRef]);

  // Expose doSave to parent via ref
  useEffect(() => {
    triggerSaveRef.current = doSave;
  }, [doSave, triggerSaveRef]);

  useEffect(() => {
    // Notify parent when provider is ready
    const checkInterval = setInterval(() => {
      if (providerRef.current) {
        onProviderReady?.(providerRef.current);
        clearInterval(checkInterval);
      }
    }, 100);
    return () => clearInterval(checkInterval);
  }, [onProviderReady, providerRef]);

  useEffect(() => {
    // Start periodic save
    saveTimerRef.current = setInterval(() => {
      doSave();
    }, 30000);

    return () => {
      if (saveTimerRef.current) clearInterval(saveTimerRef.current);
      if (statusTimerRef.current) clearTimeout(statusTimerRef.current);
      // Flush before unmount
      const provider = providerRef.current;
      if (provider) {
        provider.sendFullState();
      }
      // Don't null providerRef or call onProviderReady(null) here —
      // the provider lifecycle is owned by CollaborationPlugin.
      // Nulling the ref breaks React StrictMode double-invoke (mount→cleanup→remount)
      // because CollaborationPlugin's isProviderInitialized guard skips the factory on remount.
    };
  }, [doSave, providerRef, onProviderReady]);

  return null;
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
      const markdownContent = lexicalStateToMarkdown(editorState);

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
        const textNodes = [...(paragraph as ElementNode).getChildren()];
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
            if (typeof bold === 'boolean' && bold) {
              node.setFormat(node.getFormat() | 1); // IS_BOLD bitmask
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
            const childrenToMove = [
              ...(paragraph as ElementNode).getChildren(),
            ];
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
  token,
  onOpenShare,
  onProviderReady,
}: Props) {
  const [downloading, setDownloading] = useState(false);
  const [saveStatus, setSaveStatus] = useState<SaveStatus>('idle');
  const [collabProvider, setCollabProvider] =
    useState<CollaborationWebSocketProvider | null>(null);
  const triggerSaveRef = useRef<(() => void) | null>(null);
  const [version, setVersion] = useState<number | null>(null);
  const apiFetchRef = useRef(apiFetch);
  apiFetchRef.current = apiFetch;

  // Ref to hold provider instance created by providerFactory (shared with CollabSavePlugin)
  const collabProviderRef = useRef<CollaborationWebSocketProvider | null>(null);

  // Fetch version info
  useEffect(() => {
    if (!document) return;
    apiFetchRef
      .current(`/api/v1/collaboration/documents/${document.id}/versions`)
      .then((r) => r.json())
      .then((result) => {
        if (result.code === 0 && result.data) {
          setVersion(result.data.current_version);
        }
      })
      .catch(() => {});
  }, [document]);

  // Refresh version after save
  useEffect(() => {
    if (!document || saveStatus !== 'saved') return;
    apiFetchRef
      .current(`/api/v1/collaboration/documents/${document.id}/versions`)
      .then((r) => r.json())
      .then((result) => {
        if (result.code === 0 && result.data) {
          setVersion(result.data.current_version);
        }
      })
      .catch(() => {});
  }, [saveStatus]);

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
    if (!collabProviderRef.current) {
      notification.warning({
        message: '协同连接未就绪，无法保存',
        duration: 3,
      });
      return;
    }
    triggerSaveRef.current?.();
  }, []);

  const handleProviderReady = useCallback(
    (p: CollaborationWebSocketProvider | null) => {
      setCollabProvider(p);
      onProviderReady?.(p);
    },
    [onProviderReady],
  );

  // Listen for remote save notifications from other collaborators
  // Poll for provider readiness (same pattern as CollabSavePlugin's onProviderReady)
  const savedListenerRef = useRef<((...args: unknown[]) => void) | null>(null);
  useEffect(() => {
    const checkInterval = setInterval(() => {
      const provider = collabProviderRef.current;
      if (!provider) return;
      // Already registered
      if (savedListenerRef.current) return;
      clearInterval(checkInterval);
      const onRemoteSaved = (({ userName }: { userName: string }) => {
        notification.info({
          message: `${userName} 已保存文档`,
          duration: 3,
        });
      }) as (...args: unknown[]) => void;
      savedListenerRef.current = onRemoteSaved;
      provider.on('saved', onRemoteSaved);
    }, 200);
    return () => {
      clearInterval(checkInterval);
      if (savedListenerRef.current && collabProviderRef.current) {
        collabProviderRef.current.off('saved', savedListenerRef.current);
        savedListenerRef.current = null;
      }
    };
  }, []);

  // Factory for CollaborationPlugin: creates Y.Doc + WebSocketProvider
  const providerFactory = useCallback(
    (id: string, yjsDocMap: Map<string, Y.Doc>) => {
      console.log(
        '[DocEditor] providerFactory called, id=',
        id,
        'token=',
        token ? '***' : 'undefined',
      );
      const doc = new Y.Doc();
      yjsDocMap.set(id, doc);
      const provider = new CollaborationWebSocketProvider(doc, id, token!);
      collabProviderRef.current = provider;
      console.log(
        '[DocEditor] providerFactory done, collabProviderRef.current set =',
        !!collabProviderRef.current,
      );
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      return provider as any;
    },
    [token],
  );

  // User info for cursor display
  const userInfo = storage.getUserInfoObject();
  const userName = userInfo?.nickname || userInfo?.email || '';

  // Debug: log document content on mount
  useEffect(() => {
    if (document && token) {
      console.log(
        '[DocEditor] mount with token, doc.id=',
        document.id,
        'content keys:',
        Object.keys(document.content || {}),
        'root children:',
        document.content?.root?.children?.length,
        'first child type:',
        document.content?.root?.children?.[0]?.type,
      );
    }
  }, [document?.id, token]);

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
      QuoteNode,
      ListNode,
      ListItemNode,
      TableNode,
      TableCellNode,
      TableRowNode,
      CalloutNode,
      CodeNode,
      CodeHighlightNode,
      LinkNode,
      AutoLinkNode,
      ImageNode,
      MathNode,
      MentionNode,
    ],
  };

  return (
    <div className="flex-1 flex flex-col min-h-0 bg-white">
      <EditorHeader
        docId={document.id}
        docName={document.name}
        saveStatus={saveStatus}
        version={version}
        provider={collabProvider}
        showManualSave={true}
        onManualSave={handleSave}
        onDownload={handleDownload}
        downloading={downloading}
        onOpenShare={onOpenShare}
        apiFetch={apiFetch}
        onRenamed={onUpdate}
      />

      {/* Editor + Comment Panel side by side */}
      <div className="flex-1 flex min-h-0">
        <div className="flex-1 overflow-y-auto bg-stone-100/60 min-w-0">
          <LexicalComposer initialConfig={initialConfig}>
            {/* Sticky toolbar — full width, outside max-w constraint */}
            <div className="sticky top-0 z-10 bg-white border-b border-stone-200">
              <div className="max-w-5xl mx-auto px-6">
                <ToolbarPlugin />
              </div>
            </div>
            {/* Content — white paper card on gray background */}
            <div className="max-w-5xl mx-auto px-6 py-5">
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
                <CheckListPlugin />
                <TablePlugin
                  hasCellMerge={false}
                  hasCellBackgroundColor={false}
                  hasTabHandler
                  hasHorizontalScroll
                />
                <LinkPlugin />
                <MentionPlugin apiFetch={apiFetch} />
                {token ? (
                  <>
                    <CollaborationPlugin
                      id={document.id}
                      providerFactory={providerFactory}
                      shouldBootstrap={true}
                      username={userName}
                      cursorColor="#958DF1"
                      initialEditorState={
                        document.content
                          ? JSON.stringify(document.content)
                          : undefined
                      }
                    />
                    <CollabSavePlugin
                      providerRef={collabProviderRef}
                      docId={document.id}
                      apiFetch={apiFetch}
                      onUpdate={onUpdate}
                      onSaveStatus={setSaveStatus}
                      onProviderReady={handleProviderReady}
                      triggerSaveRef={triggerSaveRef}
                    />
                  </>
                ) : (
                  <>
                    <AutoSavePlugin
                      docId={document.id}
                      apiFetch={apiFetch}
                      onUpdate={onUpdate}
                      onSaveStatus={setSaveStatus}
                      triggerSaveRef={triggerSaveRef}
                    />
                    <SetInitialStatePlugin content={document.content} />
                  </>
                )}
                <FormatApplyPlugin
                  config={appliedRuleConfig}
                  onApplied={onRuleApplied}
                />
              </div>
            </div>
          </LexicalComposer>
        </div>
      </div>
    </div>
  );
}
