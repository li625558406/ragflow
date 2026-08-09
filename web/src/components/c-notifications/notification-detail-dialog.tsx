import type { NotificationResult } from '@/services/c-notification-service';
import DOMPurify from 'dompurify';
import { useMemo } from 'react';
import Markdown from 'react-markdown';
import rehypeRaw from 'rehype-raw';
import remarkGfm from 'remark-gfm';

interface Props {
  result: NotificationResult;
  onClose: () => void;
}

// 块级/结构性 HTML 标签正则：命中则启用 rehype-raw 把嵌入 HTML 一起渲染
const HTML_BLOCK_REG =
  /<\/?(p|div|span|table|thead|tbody|tr|td|th|ul|ol|li|h[1-6]|br|img|a|section|article|blockquote|pre|hr|strong|em|font)\b/i;

// 让所有 <a> 在新标签打开 + 加 rel=noreferrer，避免反向 tabnabbing
DOMPurify.addHook('afterSanitizeAttributes', (node) => {
  if (node.tagName === 'A') {
    node.setAttribute('target', '_blank');
    node.setAttribute('rel', 'noreferrer');
  }
});

// 正文排版容器样式：覆盖 react-markdown 渲染出的所有 HTML 元素
const BODY_CLASS_NAME = [
  'text-sm leading-7 text-[#000000]',
  '[&_h1]:text-lg [&_h1]:font-semibold [&_h1]:mt-4 [&_h1]:mb-2',
  '[&_h2]:text-base [&_h2]:font-semibold [&_h2]:mt-3 [&_h2]:mb-2',
  '[&_h3]:text-sm [&_h3]:font-semibold [&_h3]:mt-3 [&_h3]:mb-1.5',
  '[&_p]:my-3 [&_p]:leading-7',
  '[&_ul]:list-disc [&_ul]:pl-5 [&_ul]:my-2',
  '[&_ol]:list-decimal [&_ol]:pl-5 [&_ol]:my-2',
  '[&_li]:my-1 [&_li]:leading-7',
  '[&_table]:w-full [&_table]:text-xs [&_table]:border-collapse [&_table]:my-2',
  '[&_td]:border [&_td]:border-[#D4D4D4] [&_td]:p-1.5 [&_td]:align-top',
  '[&_th]:border [&_th]:border-[#D4D4D4] [&_th]:p-1.5 [&_th]:bg-[#F5F5F5] [&_th]:font-medium',
  '[&_img]:max-w-full [&_img]:h-auto [&_img]:rounded',
  '[&_a]:text-blue-600 [&_a]:underline',
  '[&_blockquote]:border-l-2 [&_blockquote]:border-gray-300 [&_blockquote]:pl-3 [&_blockquote]:text-gray-600 [&_blockquote]:my-2',
  '[&_pre]:bg-gray-50 [&_pre]:p-2 [&_pre]:rounded [&_pre]:overflow-x-auto',
  '[&_code]:font-mono [&_code]:text-xs',
  '[&_hr]:my-3 [&_hr]:border-gray-200',
].join(' ');

export function NotificationDetailDialog({ result, onClose }: Props) {
  const { raw, rehypePlugins } = useMemo(() => {
    const md = result.markdown || '';
    // DOMPurify 默认移除 <script>/<iframe>、on* 事件属性、javascript: 协议等
    const cleaned = DOMPurify.sanitize(md, {
      ADD_ATTR: ['target', 'rel'],
    });
    // 归一化换行符：把 \r\n / \r 统一为 \n
    const normalized = cleaned.replace(/\r\n?/g, '\n');
    const isHtml = HTML_BLOCK_REG.test(md);
    // 非 HTML 内容做"丢失换行恢复"：
    //   1) 连续 2+ 个半角空格 / Tab / 全角空格通常是被压扁的段落边界 → 转成换行
    //   2) 3 个以上换行压缩为段落分隔 \n\n，避免空白过大
    const repaired = isHtml
      ? normalized
      : normalized
          .replace(/( |\t|\u3000){2,}/g, '\n')
          .replace(/\n{3,}/g, '\n\n');
    return { raw: repaired, rehypePlugins: isHtml ? [rehypeRaw] : [] };
  }, [result.markdown]);

  return (
    <div
      className="fixed inset-0 z-[80] flex items-center justify-center bg-black/40"
      onClick={onClose}
    >
      <div
        className="w-[860px] max-h-[80vh] bg-white rounded-xl shadow-2xl flex flex-col"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between px-5 py-3 border-b">
          <span className="font-semibold text-base truncate pr-4">
            {result.title || '(无标题)'}
          </span>
        </div>
        <div className="flex-1 overflow-y-auto px-5 py-4 space-y-3">
          <div className="flex flex-wrap gap-3 text-sm text-gray-600">
            {result.publish_date && (
              <span>发布时间：{result.publish_date}</span>
            )}
            {result.source_url && (
              <a
                href={result.source_url}
                target="_blank"
                rel="noreferrer"
                className="text-blue-600 hover:underline"
              >
                查看原文 ↗
              </a>
            )}
          </div>
          {raw ? (
            <div className={BODY_CLASS_NAME}>
              <Markdown
                // breaks=true：单个换行渲染为 <br>，兼容纯文本正文
                breaks
                rehypePlugins={rehypePlugins}
                remarkPlugins={[remarkGfm]}
              >
                {raw}
              </Markdown>
            </div>
          ) : (
            <div className="p-8 text-center text-sm text-gray-400">
              无正文内容
            </div>
          )}
        </div>
        <div className="flex justify-end px-5 py-3 border-t">
          <button
            onClick={onClose}
            className="px-4 py-1.5 text-sm border rounded hover:bg-gray-50"
          >
            返回
          </button>
        </div>
      </div>
    </div>
  );
}
