import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Progress } from '@/components/ui/progress';
import { fetchBidParseStatus, triggerBidParse } from '@/services/bid-service';
import request from '@/utils/next-request';
import {
  Check,
  CheckCircle2,
  ChevronDown,
  Clock,
  Loader2,
  Search,
  X,
  XCircle,
} from 'lucide-react';
import { useCallback, useEffect, useRef, useState } from 'react';

// ============================================================================
// Simple Custom Select (no Radix dependency)
// ============================================================================

function SimpleSelect({
  value,
  onChange,
  options,
  placeholder,
  disabled,
}: {
  value: string;
  onChange: (val: string) => void;
  options: { label: string; value: string }[];
  placeholder?: string;
  disabled?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [keyword, setKeyword] = useState('');
  const containerRef = useRef<HTMLDivElement>(null);

  const selected = options.find((o) => o.value === value);
  const filtered = keyword
    ? options.filter((o) =>
        o.label.toLowerCase().includes(keyword.toLowerCase()),
      )
    : options;

  // Close on click outside
  useEffect(() => {
    if (!open) return;
    const handler = (e: MouseEvent) => {
      if (
        containerRef.current &&
        !containerRef.current.contains(e.target as Node)
      ) {
        setOpen(false);
        setKeyword('');
      }
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, [open]);

  return (
    <div ref={containerRef} className="relative">
      {/* Trigger */}
      <button
        type="button"
        disabled={disabled}
        onClick={() => {
          if (!disabled) {
            setOpen((v) => !v);
            if (open) setKeyword('');
          }
        }}
        className="w-full h-9 px-3 text-xs text-left border border-[#D4D4D4] bg-white rounded-lg flex items-center justify-between gap-2 hover:border-[#A3A3A3] focus:border-[#000000] focus:outline-none transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
      >
        <span className={selected ? 'text-[#000000]' : 'text-[#A3A3A3]'}>
          {selected ? selected.label : placeholder || '请选择'}
        </span>
        <span className="flex items-center gap-1 shrink-0">
          {value && !disabled && (
            <X
              className="size-3 text-[#A3A3A3] hover:text-[#000000]"
              onClick={(e) => {
                e.stopPropagation();
                onChange('');
              }}
            />
          )}
          <ChevronDown
            className={`size-3.5 text-[#A3A3A3] transition-transform ${open ? 'rotate-180' : ''}`}
          />
        </span>
      </button>

      {/* Dropdown */}
      {open && (
        <div className="absolute z-50 top-full mt-1 left-0 right-0 bg-white border border-[#E8E8E8] rounded-lg shadow-lg overflow-hidden">
          {/* Search */}
          <div className="px-2 py-1.5 border-b border-[#F0F0F0]">
            <div className="flex items-center gap-1.5 px-2 h-7 bg-[#F5F5F5] rounded">
              <Search className="size-3 text-[#A3A3A3] shrink-0" />
              <input
                type="text"
                value={keyword}
                onChange={(e) => setKeyword(e.target.value)}
                placeholder="搜索..."
                autoFocus
                className="flex-1 bg-transparent text-xs text-[#000000] placeholder:text-[#A3A3A3] outline-none"
              />
            </div>
          </div>

          {/* Options */}
          <div className="max-h-48 overflow-y-auto py-1">
            {filtered.length === 0 ? (
              <div className="px-3 py-4 text-center text-xs text-[#A3A3A3]">
                无匹配项
              </div>
            ) : (
              filtered.map((opt) => (
                <button
                  key={opt.value}
                  type="button"
                  onClick={() => {
                    onChange(opt.value);
                    setOpen(false);
                    setKeyword('');
                  }}
                  className="w-full px-3 py-1.5 text-xs text-left flex items-center justify-between gap-2 hover:bg-[#F5F5F5] transition-colors"
                >
                  <span className="truncate text-[#1a1a1a]">{opt.label}</span>
                  {opt.value === value && (
                    <Check className="size-3.5 text-[#000000] shrink-0" />
                  )}
                </button>
              ))
            )}
          </div>
        </div>
      )}
    </div>
  );
}

interface ParseStatus {
  status: string; // none | pending | parsing | done | fail
  progress: number;
  progress_msg: string;
  kb_id: string;
  combined_doc_id: string;
}

interface Props {
  visible: boolean;
  projectId: number;
  projectTitle?: string;
  onClose: () => void;
}

export function BidConfigDialog({
  visible,
  projectId,
  projectTitle,
  onClose,
}: Props) {
  const [kbs, setKbs] = useState<any[]>([]);
  const [kbsLoading, setKbsLoading] = useState(false);
  const [selectedKb, setSelectedKb] = useState<string>('');
  const [parseLoading, setParseLoading] = useState(false);
  const [parseStatus, setParseStatus] = useState<ParseStatus | null>(null);
  const pollingRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // Fetch KB list when dialog opens
  useEffect(() => {
    if (!visible) return;
    setKbsLoading(true);
    request
      .get('/api/v1/datasets', { params: { page_size: 1000 } })
      .then((res: any) => {
        const data = res?.data?.data ?? res?.data ?? [];
        setKbs(Array.isArray(data) ? data : []);
      })
      .catch((e: any) => {
        console.error('加载知识库列表失败:', e);
      })
      .finally(() => setKbsLoading(false));
  }, [visible]);

  // Fetch current parse status when dialog opens
  useEffect(() => {
    if (!visible || !projectId) return;
    fetchBidParseStatus(projectId)
      .then((res: any) => {
        const data = res?.data?.data ?? null;
        if (data) setParseStatus(data);
      })
      .catch(() => {});
  }, [visible, projectId]);

  // Polling when status is parsing
  useEffect(() => {
    if (
      parseStatus?.status === 'parsing' ||
      parseStatus?.status === 'pending'
    ) {
      pollingRef.current = setInterval(() => {
        fetchBidParseStatus(projectId)
          .then((res: any) => {
            const data = res?.data?.data ?? null;
            if (data) {
              setParseStatus(data);
              if (data.status === 'done' || data.status === 'fail') {
                if (pollingRef.current) {
                  clearInterval(pollingRef.current);
                  pollingRef.current = null;
                }
              }
            }
          })
          .catch(() => {});
      }, 3000);
    }

    return () => {
      if (pollingRef.current) {
        clearInterval(pollingRef.current);
        pollingRef.current = null;
      }
    };
  }, [parseStatus?.status, projectId]);

  const handleParse = useCallback(async () => {
    if (!selectedKb) return;
    setParseLoading(true);
    try {
      const res: any = await triggerBidParse(projectId, selectedKb);
      const data = res?.data?.data ?? res?.data ?? null;
      if (data) {
        setParseStatus({ ...data, progress: 0, progress_msg: '任务已提交...' });
      }
    } catch {
      // Error handled by request interceptor
    } finally {
      setParseLoading(false);
    }
  }, [projectId, selectedKb]);

  const handleClose = useCallback(() => {
    // Don't stop polling on close - it will be checked again when reopened
    onClose();
  }, [onClose]);

  const showProgress = parseStatus && parseStatus.status !== 'none';

  const statusIcon = (status: string) => {
    switch (status) {
      case 'done':
        return <CheckCircle2 className="size-5 text-green-500" />;
      case 'fail':
        return <XCircle className="size-5 text-red-500" />;
      case 'parsing':
      case 'pending':
        return <Loader2 className="size-5 animate-spin text-[#000000]" />;
      default:
        return <Clock className="size-5 text-[#525252]" />;
    }
  };

  const statusLabel = (status: string) => {
    switch (status) {
      case 'done':
        return '解析完成';
      case 'fail':
        return '解析失败';
      case 'parsing':
        return '解析中...';
      case 'pending':
        return '已提交';
      default:
        return '未开始';
    }
  };

  return (
    <Dialog
      open={visible}
      onOpenChange={(open) => {
        if (!open) handleClose();
      }}
    >
      <DialogContent className="sm:max-w-[480px] bg-[#FFFFFF] border-[#D4D4D4]">
        <DialogHeader>
          <DialogTitle className="text-[#1a1a1a]">
            项目解析配置
            {projectTitle && (
              <span className="text-sm font-normal text-[#525252] ml-2">
                — {projectTitle}
              </span>
            )}
          </DialogTitle>
        </DialogHeader>

        <div className="space-y-5 py-4">
          {/* KB Selector */}
          <div className="space-y-2">
            <label className="text-sm font-medium text-[#333333]">
              选择知识库
            </label>
            <SimpleSelect
              value={selectedKb}
              onChange={(val) => setSelectedKb(val)}
              options={kbs.map((kb: any) => ({
                label: kb.name,
                value: kb.id,
              }))}
              placeholder={kbsLoading ? '加载中...' : '请选择知识库'}
              disabled={
                parseStatus?.status === 'parsing' ||
                parseStatus?.status === 'pending'
              }
            />
          </div>

          {/* Progress Display */}
          {showProgress && (
            <div className="rounded-lg border border-[#D4D4D4] bg-[#FFFFFF] p-4 space-y-3">
              <div className="flex items-center gap-2">
                {statusIcon(parseStatus.status)}
                <span className="text-sm font-medium text-[#1a1a1a]">
                  {statusLabel(parseStatus.status)}
                </span>
              </div>

              <Progress
                value={Math.round((parseStatus.progress || 0) * 100)}
                className="h-2 [&>div]:bg-[#000000]"
              />

              {parseStatus.progress_msg && (
                <p className="text-xs text-[#525252]">
                  {parseStatus.progress_msg}
                </p>
              )}
            </div>
          )}
        </div>

        <DialogFooter>
          <Button
            variant="outline"
            onClick={handleClose}
            className="border-[#D4D4D4] text-[#333333] hover:bg-[#FFFFFF] hover:text-[#333333]"
          >
            关闭
          </Button>
          <Button
            onClick={handleParse}
            disabled={
              !selectedKb ||
              parseLoading ||
              parseStatus?.status === 'parsing' ||
              parseStatus?.status === 'pending'
            }
            loading={parseLoading}
            className="bg-[#000000] hover:bg-[#171717] text-white"
          >
            开始解析
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
