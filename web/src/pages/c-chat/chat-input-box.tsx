// web/src/pages/c-chat/chat-input-box.tsx
// C端对话输入框组件：从 c-chat/index.tsx 原样抽取（非空态输入框 + 打字机占位），
// c-chat 页与「流程 → AI 处理」面板共用，保证交互完全一致。
// 组件内部持有：FileUpload 文件队列（上传/拖拽/粘贴）、IME 组合态、语音转写、文件审核按钮。
import {
  FileUpload,
  FileUploadDropzone,
  FileUploadItem,
  FileUploadItemDelete,
  FileUploadItemMetadata,
  FileUploadItemPreview,
  FileUploadItemProgress,
  FileUploadList,
  FileUploadTrigger,
  type FileUploadProps,
} from '@/components/file-upload';
import { RealtimeAudioButton } from '@/components/realtime-audio-button';
import { Textarea } from '@/components/ui/textarea';
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from '@/components/ui/tooltip';
import { FileText, Paperclip, Send, Square, Upload, X } from 'lucide-react';
import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type ChangeEventHandler,
  type RefObject,
} from 'react';

export function showToast(message: string) {
  const toast = document.createElement('div');
  toast.className =
    'fixed top-6 left-1/2 -translate-x-1/2 bg-[#F0FDF4] text-[#16A34A] px-5 py-3 rounded-xl text-sm z-[9999] transition-all font-medium border border-[#BBF7D0] shadow-[0_4px_24px_rgba(22,163,74,0.1)]';
  toast.textContent = message;
  document.body.appendChild(toast);
  setTimeout(() => {
    toast.style.opacity = '0';
    setTimeout(() => toast.remove(), 300);
  }, 2500);
}

export interface UploadedDoc {
  id: string;
  name?: string;
  [key: string]: unknown;
}

interface ChatInputBoxProps {
  value: string;
  setValue: (v: string) => void;
  handleInputChange: ChangeEventHandler<HTMLTextAreaElement>;
  textareaRef: RefObject<HTMLTextAreaElement>;
  /** IME 组合态 ref：父级发送逻辑用它避免选词回车误发送 */
  composingRef: { current: boolean };
  sendLoading: boolean;
  onSend: () => void;
  onStop: () => void;
  /** false 时用 typewriterText 打字机占位，true 时显示「继续输入您的问题...」 */
  hasMessages?: boolean;
  typewriterText?: string;
  reviewMode: boolean;
  onToggleReview: () => void;
  /** 审阅按钮显示条件；不传时默认「已上传文件 > 0」 */
  reviewAvailable?: boolean;
  /** 上传完成的文档对象数组变化回调（父级发送时附带） */
  onUploadedFilesChange?: (files: UploadedDoc[]) => void;
  /** 限制可上传的文件类型（如 '.doc,.docx'），文件选择/拖拽/粘贴均生效 */
  accept?: string;
  autoFocus?: boolean;
}

export default function ChatInputBox({
  value,
  setValue,
  handleInputChange,
  textareaRef,
  composingRef,
  sendLoading,
  onSend,
  onStop,
  hasMessages = false,
  typewriterText = '',
  reviewMode,
  onToggleReview,
  reviewAvailable,
  onUploadedFilesChange,
  accept,
  autoFocus,
}: ChatInputBoxProps) {
  const [composing, setComposing] = useState(false);
  // FileUpload 受控队列：raw 文件 + 上传结果（document 对象）
  const [files, setFiles] = useState<File[]>([]);
  const [uploadedFiles, setUploadedFiles] = useState<UploadedDoc[]>([]);
  const [audioInputValue, setAudioInputValue] = useState<string | null>(null);

  // 发送开始时清空文件队列（与 c-chat handlePressEnter 行为一致）
  const prevLoadingRef = useRef(sendLoading);
  useEffect(() => {
    if (sendLoading && !prevLoadingRef.current) {
      setFiles([]);
      setUploadedFiles([]);
    }
    prevLoadingRef.current = sendLoading;
  }, [sendLoading]);

  useEffect(() => {
    onUploadedFilesChange?.(uploadedFiles);
  }, [uploadedFiles, onUploadedFilesChange]);

  // 语音转写结果回填输入框（与 c-chat 一致）
  useEffect(() => {
    if (audioInputValue !== null) {
      setValue(audioInputValue);
    }
  }, [audioInputValue]); // eslint-disable-line react-hooks/exhaustive-deps

  // ── File upload（与 c-chat 完全同款：POST /documents/upload）──
  const handleFileUpload: NonNullable<FileUploadProps['onUpload']> =
    useCallback(async (uploadFiles, { onProgress, onSuccess, onError }) => {
      for (const file of uploadFiles) {
        try {
          onProgress(file, 0);
          const formData = new FormData();
          formData.append('file', file);

          const resp = await fetch('/api/v1/documents/upload', {
            method: 'POST',
            headers: {
              Authorization: localStorage.getItem('Authorization') || '',
            },
            body: formData,
          });

          if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
          const result = await resp.json();
          if (result.code === 0 && result.data) {
            const fileData = Array.isArray(result.data)
              ? result.data
              : [result.data];
            setUploadedFiles((prev) => [...prev, ...fileData]);
            onProgress(file, 100);
            onSuccess(file);
          } else {
            throw new Error(result.message || 'Unknown error');
          }
        } catch (e: any) {
          onError(file, e);
          showToast('文件上传失败: ' + e.message);
        }
      }
    }, []);

  const removeUploadedFile = useCallback((file: File) => {
    setUploadedFiles((prev) => prev.filter((f) => f.name !== file.name));
  }, []);

  const handleFileReject = useCallback(
    (_file: File, message: string) => {
      showToast(
        message === 'File too large'
          ? '文件超过50MB限制'
          : message === 'Maximum 10 files allowed'
            ? '最多上传10个文件'
            : message === 'File type not accepted'
              ? `仅支持 ${accept} 格式的文档`
              : message,
      );
    },
    [accept],
  );

  const showReviewButton = reviewAvailable ?? uploadedFiles.length > 0;

  return (
    <div className="max-w-3xl mx-auto">
      <FileUpload
        value={files}
        onValueChange={setFiles}
        onUpload={handleFileUpload}
        className="w-full"
        disabled={sendLoading}
        multiple
        maxFiles={10}
        maxSize={50 * 1024 * 1024}
        onFileReject={handleFileReject}
        accept={accept}
      >
        <FileUploadDropzone
          tabIndex={-1}
          onClick={(event) => event.preventDefault()}
          className="absolute top-0 left-0 z-0 flex size-full items-center justify-center rounded-none border-none bg-background/50 p-0 opacity-0 pointer-events-none backdrop-blur transition-opacity duration-200 ease-out data-[dragging]:z-10 data-[dragging]:opacity-100 data-[dragging]:pointer-events-auto"
        >
          <div className="flex flex-col items-center gap-1 text-center">
            <div className="flex items-center justify-center rounded-full border p-2.5">
              <Upload className="size-6 text-muted-foreground" />
            </div>
            <p className="font-medium text-sm text-foreground">
              拖拽文件到此处上传
            </p>
            <p className="text-muted-foreground text-xs">
              最多上传10个文件，每个不超过50MB
            </p>
          </div>
        </FileUploadDropzone>

        <div
          className="cs-input-ring relative flex flex-col gap-2 bg-white border border-[#E8E8E8] rounded-2xl px-3 py-2 shadow-[0_1px_6px_rgba(63,91,141,0.04)] transition-all duration-200 hover:border-[#D0D0D0] hover:shadow-[0_2px_12px_rgba(63,91,141,0.06)]"
          onDragOver={(e) => {
            e.preventDefault();
            e.stopPropagation();
          }}
          onDrop={(e) => {
            e.preventDefault();
            e.stopPropagation();
            const fileInput = e.currentTarget
              .closest('[data-slot="file-upload"]')
              ?.querySelector('input[type="file"]') as HTMLInputElement;
            if (fileInput && e.dataTransfer.files.length > 0) {
              const dt = new DataTransfer();
              for (const f of e.dataTransfer.files) dt.items.add(f);
              fileInput.files = dt.files;
              fileInput.dispatchEvent(new Event('change', { bubbles: true }));
            }
          }}
          onPaste={(e) => {
            const items = e.clipboardData?.items;
            if (!items) return;
            const fileItems: File[] = [];
            for (let i = 0; i < items.length; i++) {
              const file = items[i].getAsFile?.();
              if (file) fileItems.push(file);
            }
            if (!fileItems.length) return;
            e.preventDefault();
            e.stopPropagation();
            const fileInput = e.currentTarget
              .closest('[data-slot="file-upload"]')
              ?.querySelector('input[type="file"]') as HTMLInputElement;
            if (fileInput) {
              const dt = new DataTransfer();
              for (const f of fileItems) dt.items.add(f);
              fileInput.files = dt.files;
              fileInput.dispatchEvent(new Event('change', { bubbles: true }));
            }
          }}
        >
          {files.length > 0 && (
            <FileUploadList
              orientation="horizontal"
              className="overflow-x-auto px-0 py-1"
            >
              {files.map((file, index) => (
                <FileUploadItem
                  key={index}
                  value={file}
                  className="max-w-none w-fit p-1 pr-4 gap-1.5 rounded-lg border border-[#E8E8E8]"
                >
                  <FileUploadItemPreview className="size-6 [&>svg]:size-3.5 [&>svg]:text-[#525252]">
                    <FileUploadItemProgress variant="fill" />
                  </FileUploadItemPreview>
                  <FileUploadItemMetadata
                    size="sm"
                    className="[&_span:first-child]:text-[#000000]"
                  />
                  <FileUploadItemDelete asChild>
                    <button
                      className="-top-1 -right-1 absolute size-4 shrink-0 cursor-pointer rounded-full bg-gray-200 hover:bg-gray-300 flex items-center justify-center"
                      onClick={(e) => {
                        e.stopPropagation();
                        removeUploadedFile(file);
                      }}
                    >
                      <X className="size-2.5" />
                    </button>
                  </FileUploadItemDelete>
                </FileUploadItem>
              ))}
            </FileUploadList>
          )}
          <Textarea
            ref={textareaRef}
            value={value}
            onChange={handleInputChange}
            onCompositionStart={() => {
              composingRef.current = true;
              setComposing(true);
            }}
            onCompositionEnd={(
              e: React.CompositionEvent<HTMLTextAreaElement>,
            ) => {
              composingRef.current = false;
              setComposing(false);
              const finalValue = (e.target as HTMLTextAreaElement).value;
              setValue(finalValue);
            }}
            onKeyDown={(e: React.KeyboardEvent<HTMLTextAreaElement>) => {
              if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                onSend();
              }
            }}
            placeholder={hasMessages ? '继续输入您的问题...' : typewriterText}
            className={`min-h-[40px] w-full p-0 overflow-auto !outline-none !border-transparent !bg-transparent !shadow-none !ring-transparent !ring-offset-transparent !text-[#000000]${hasMessages ? '' : ' cs-typewriter-cursor'}`}
            style={{ color: '#000000' }}
            autoSize={{ minRows: 1, maxRows: 10 }}
            autoFocus={autoFocus}
          />
          <div className="flex items-center justify-end gap-2">
            {!sendLoading ? (
              <>
                <div className="shrink-0 w-9 h-9 flex items-center justify-center">
                  <RealtimeAudioButton
                    onTranscript={(val) => setAudioInputValue(val)}
                    testId="c-chat-audio-toggle"
                  />
                </div>
                <Tooltip>
                  <TooltipTrigger asChild>
                    <FileUploadTrigger asChild>
                      <button
                        disabled={sendLoading}
                        className="shrink-0 w-9 h-9 flex items-center justify-center text-[#525252] hover:text-[#000000] hover:bg-[#F5F5F5] rounded-lg transition-all disabled:opacity-30 disabled:cursor-not-allowed"
                      >
                        <Paperclip className="w-4 h-4" strokeWidth={2} />
                      </button>
                    </FileUploadTrigger>
                  </TooltipTrigger>
                  <TooltipContent
                    side="top"
                    className="bg-[#1A1A1A] text-[#F5F5F4] border-[#333333] text-xs px-3 py-1.5 rounded-lg shadow-lg"
                  >
                    <p>上传文件（最多10个，每个不超过50MB）</p>
                  </TooltipContent>
                </Tooltip>
                {showReviewButton && (
                  <button
                    onClick={onToggleReview}
                    className={`shrink-0 flex items-center gap-1.5 text-xs font-medium px-2.5 py-1.5 rounded-lg border transition-all cursor-pointer ${
                      reviewMode
                        ? 'bg-[#F0F3FA] border-[#3F5B8D] text-[#3F5B8D]'
                        : 'bg-white border-[#E8E8E8] text-[#8A8A8A] hover:border-[#D4D4D4] hover:text-[#525252]'
                    }`}
                  >
                    <FileText className="w-3.5 h-3.5" strokeWidth={2} />
                    文件审核
                  </button>
                )}
                <button
                  onMouseDown={(e) => {
                    e.preventDefault();
                    onSend();
                  }}
                  disabled={!value.trim() && !composing}
                  className="shrink-0 size-9 flex items-center justify-center bg-[#1A1A1A] hover:bg-[#333333] text-white rounded-full transition-colors disabled:opacity-30 disabled:cursor-not-allowed relative z-10"
                >
                  <Send className="w-4 h-4" strokeWidth={2} />
                </button>
              </>
            ) : (
              <button
                onMouseDown={(e) => {
                  e.preventDefault();
                  onStop();
                }}
                className="shrink-0 size-9 flex items-center justify-center bg-white border-2 border-[#1A1A1A] text-[#1A1A1A] hover:bg-[#F5F5F4] rounded-full transition-colors relative z-10"
              >
                <Square className="w-3 h-3" fill="currentColor" stroke="none" />
              </button>
            )}
          </div>
        </div>
      </FileUpload>
    </div>
  );
}
