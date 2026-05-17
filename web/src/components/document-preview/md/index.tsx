import { Authorization } from '@/constants/authorization';
import { cn } from '@/lib/utils';
import FileError from '@/pages/document-viewer/file-error';
import { getAuthorization } from '@/utils/authorization-util';
import React, { useEffect, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';

interface MdProps {
  className?: string;
  url: string;
}

const MAX_PREVIEW_SIZE = 1 * 1024 * 1024; // 1MB — warn threshold
const PREVIEW_TRUNCATE_SIZE = 500 * 1024; // 500KB — render this much, then truncate

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export const Md: React.FC<MdProps> = ({ url, className }) => {
  const [content, setContent] = useState<string>('');
  const [error, setError] = useState<string | null>(null);
  const [fileSize, setFileSize] = useState<number | null>(null);
  const [truncated, setTruncated] = useState(false);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError(null);
    setContent('');
    setFileSize(null);
    setTruncated(false);

    const doFetch = async () => {
      // 1. HEAD request to check size
      let totalSize: number | null = null;
      try {
        const headRes = await fetch(url, {
          method: 'HEAD',
          headers: { [Authorization]: getAuthorization() },
          signal: controller.signal,
        });
        const cl = headRes.headers.get('Content-Length');
        if (cl) totalSize = parseInt(cl, 10);
        setFileSize(totalSize);
      } catch {
        // HEAD may fail on some backends — proceed to GET
      }

      if (controller.signal.aborted) return;

      // 2. HEAD request to establish full size, then fetch truncated preview
      let isTruncated = false;

      const previewUrl =
        totalSize !== null && totalSize > MAX_PREVIEW_SIZE
          ? url +
            (url.includes('?') ? '&' : '?') +
            `max_bytes=${PREVIEW_TRUNCATE_SIZE}`
          : url;

      if (previewUrl !== url) isTruncated = true;

      const res = await fetch(previewUrl, {
        headers: { [Authorization]: getAuthorization() },
        signal: controller.signal,
      });
      if (!res.ok) throw new Error('Failed to fetch markdown file');

      // Read full size from response header if available
      const fullContentLength = res.headers.get('X-Full-Content-Length');
      if (fullContentLength && !totalSize) {
        totalSize = parseInt(fullContentLength, 10);
        setFileSize(totalSize);
      }

      let text = await res.text();

      // Fallback truncation if no Content-Length / max_bytes was available
      if (!isTruncated && text.length > PREVIEW_TRUNCATE_SIZE) {
        text = text.slice(0, PREVIEW_TRUNCATE_SIZE);
        isTruncated = true;
        if (!totalSize) setFileSize(text.length);
      }

      // Truncate at last newline to avoid broken markdown
      if (isTruncated) {
        const lastNewline = text.lastIndexOf('\n', PREVIEW_TRUNCATE_SIZE);
        if (lastNewline > 0) text = text.slice(0, lastNewline);
      }

      setTruncated(isTruncated);
      setContent(text);
      setLoading(false);
    };

    doFetch().catch((err) => {
      if (err.name !== 'AbortError') {
        setError(err.message);
        setLoading(false);
      }
    });

    return () => {
      controller.abort();
    };
  }, [url]);

  if (error) return <FileError>{error}</FileError>;

  return (
    <div
      style={{ padding: 4, overflow: 'scroll' }}
      className={cn(className, 'markdown-body h-[calc(100vh - 200px)]')}
    >
      {loading && (
        <div className="flex items-center justify-center py-8 text-sm text-gray-400">
          Loading...
        </div>
      )}
      {truncated && !loading && (
        <div className="mb-4 rounded border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800">
          The file is {fileSize ? formatSize(fileSize) : 'large'} (
          {formatSize(PREVIEW_TRUNCATE_SIZE)} previewed).
          <a
            href={url}
            download
            className="ml-2 font-medium text-blue-600 underline hover:text-blue-800"
            onClick={(e) => {
              // Use fetch + blob to include auth header
              e.preventDefault();
              fetch(url, {
                headers: { [Authorization]: getAuthorization() },
              })
                .then((r) => r.blob())
                .then((blob) => {
                  const a = document.createElement('a');
                  a.href = URL.createObjectURL(blob);
                  a.download = '';
                  a.click();
                  URL.revokeObjectURL(a.href);
                })
                .catch(() => {
                  // Fallback: direct navigation (may fail for auth-protected files)
                  window.open(url, '_blank');
                });
            }}
          >
            Download full file
          </a>
        </div>
      )}
      <ReactMarkdown remarkPlugins={[remarkGfm]}>{content}</ReactMarkdown>
    </div>
  );
};

export default Md;
