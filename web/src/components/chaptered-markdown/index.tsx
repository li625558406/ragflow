import MarkdownContent from '@/components/next-markdown-content';
import StreamMdContent from '@/components/stream-md-content';
import { IReferenceChunk, IReferenceObject } from '@/interfaces/database/chat';
import { memo, useMemo } from 'react';

/** Props matching MarkdownContent's actual type signature. */
type MarkdownContentProps = {
  content: string;
  loading: boolean;
  reference?: IReferenceObject;
  clickDocumentButton?: (documentId: string, chunk: IReferenceChunk) => void;
};

interface ChapterSegment {
  key: string;
  content: string;
}

/**
 * Memo'd segment renderer.
 *
 * During streaming (`loading === true`) the component delegates to
 * StreamMdContent — an incremental block parser that only re-renders
 * the currently-streaming block per token (≈300x fewer chars parsed).
 *
 * When the stream completes it falls back to MarkdownContent
 * (react-markdown) for the full reference-citation / think-block UX.
 */
const MemoMarkdownSegment = memo(function MemoMarkdownSegment(
  props: MarkdownContentProps,
) {
  if (props.loading) {
    return <StreamMdContent {...props} />;
  }
  return <MarkdownContent {...props} />;
});

/**
 * Splits markdown content at horizontal-rule boundaries (`---` or `***` or `___`)
 * and renders each segment as an independently memo'd MarkdownContent.
 *
 * This is critical for FanOut parallel output: when N chapters stream in,
 * each completed chapter is a separate segment.  Adding chapter N+1 only
 * triggers a re-render of that single segment — chapters 0..N are skipped
 * by React.memo because their `content` prop hasn't changed.
 *
 * For short / non-chaptered content (no `---` separator), the component
 * renders a single MarkdownContent — zero overhead vs. using MarkdownContent
 * directly.
 */
function ChapteredMarkdown(props: MarkdownContentProps) {
  const { content } = props;

  const segments = useMemo<ChapterSegment[]>(() => {
    if (!content) return [];
    const parts = content.split(/\n(?:---|\*\*\*|___)\n/);
    if (parts.length <= 1) {
      return [{ key: '0', content }];
    }
    return parts
      .map((part, i) => ({
        key: String(i),
        content: part.trim(),
      }))
      .filter((s) => s.content.length > 0);
  }, [content]);

  if (segments.length === 0) {
    return null;
  }

  // Single segment — no wrapper overhead
  if (segments.length === 1) {
    return <MemoMarkdownSegment {...props} content={segments[0].content} />;
  }

  // Multiple segments — render independently with content-visibility
  return (
    <div className="chaptered-markdown">
      {segments.map((seg, idx) => (
        <div
          key={seg.key}
          className="chapter-segment"
          style={{
            contentVisibility: 'auto',
            containIntrinsicSize: 'auto 200px',
          }}
        >
          <MemoMarkdownSegment
            {...props}
            content={seg.content}
            loading={props.loading && idx === segments.length - 1}
          />
        </div>
      ))}
    </div>
  );
}

export default memo(ChapteredMarkdown);
