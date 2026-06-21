import { IReferenceChunk, IReferenceObject } from '@/interfaces/database/chat';
import {
  preprocessLaTeX,
  replaceBareIdToCitation,
  replaceTextByOldReg,
} from '@/utils/chat';
import DOMPurify from 'dompurify';
import { memo, useMemo } from 'react';
import { StreamMD } from 'stream-md';
import 'stream-md/styles.css';
import MarkdownContent from '../next-markdown-content';
import './styles.css';

interface StreamMdContentProps {
  content: string;
  loading: boolean;
  reference?: IReferenceObject;
  clickDocumentButton?: (documentId: string, chunk: IReferenceChunk) => void;
}

/**
 * Preprocesses markdown content for StreamMD rendering during streaming.
 *
 * StreamMD handles standard markdown natively (headings, lists, code blocks,
 * tables, blockquotes, bold/italic/links/images).  The preprocessing pipeline:
 *
 *   1. Sanitize (DOMPurify)
 *   2. Normalise citation IDs  (bare ID:NNN → [ID:NNN], old ##NNN$$ → [ID:NNN])
 *   3. <think> → blockquote  (StreamMD parses blockquotes natively, so the
 *      "thinking" block appears styled during streaming rather than as raw HTML)
 *   4. LaTeX delimiters  (\[…\] → $$…$$, \(…\) → $…$)
 *
 * Reference citations ([ID:NNN]) will appear as plain text during streaming.
 * When the stream completes the component switches to MarkdownContent which
 * handles the full citation hover-card UX.
 */
function preprocessForStreamMd(content: string): string {
  let text = DOMPurify.sanitize(content, {
    ADD_TAGS: ['think'],
  });

  text = replaceBareIdToCitation(replaceTextByOldReg(text));

  // Convert <think>...</think> to markdown blockquote so StreamMD
  // renders it natively (styled, collapsible via CSS)
  text = text.replace(
    /<think>([\s\S]*?)<\/think>/g,
    (_: string, inner: string) =>
      '\n> **思考中…**\n' +
      inner
        .split('\n')
        .map((l: string) => (l ? '> ' + l : '>'))
        .join('\n') +
      '\n',
  );

  // Clean up unclosed / orphaned tags during streaming
  text = text.replace(/<think>/g, '').replace(/<\/think>/g, '');

  text = preprocessLaTeX(text);
  return text;
}

/**
 * Streaming-optimised markdown renderer.
 *
 * During active streaming (`loading === true`) the component renders with
 * StreamMD which does **incremental block parsing** — only the currently-
 * streaming block re-renders per token, while completed blocks are frozen
 * via React.memo.  This eliminates the O(n²) re-parse penalty of a full
 * react-markdown pass on every SSE chunk.
 *
 * When the stream completes, the component switches to MarkdownContent
 * (react-markdown) which provides the full UX: reference citation hover-
 * cards, collapsible `<think>` blocks, artifact link handling, etc.
 * This one-time transition at stream-end is cheap.
 */
function StreamMdContent(props: StreamMdContentProps) {
  const { content, loading, reference, clickDocumentButton } = props;

  const processed = useMemo(() => {
    if (!content) return '';
    return preprocessForStreamMd(content);
  }, [content]);

  if (!processed) return null;

  // Stream complete → full-featured react-markdown render
  if (!loading) {
    return (
      <MarkdownContent
        content={content}
        loading={false}
        reference={reference}
        clickDocumentButton={clickDocumentButton}
      />
    );
  }

  // Streaming → incremental StreamMD render (no flicker, ~300x fewer chars parsed)
  return <StreamMD text={processed} theme="light" />;
}

export default memo(StreamMdContent);
