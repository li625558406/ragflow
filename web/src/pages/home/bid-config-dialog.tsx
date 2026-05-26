import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Progress } from '@/components/ui/progress';
import { RAGFlowSelect } from '@/components/ui/select';
import { fetchBidParseStatus, triggerBidParse } from '@/services/bid-service';
import request from '@/utils/next-request';
import { CheckCircle2, Clock, Loader2, XCircle } from 'lucide-react';
import { useCallback, useEffect, useRef, useState } from 'react';

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
        return <Loader2 className="size-5 animate-spin text-[#a78bfa]" />;
      default:
        return <Clock className="size-5 text-[#9b9bb5]" />;
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
      <DialogContent className="sm:max-w-[480px] bg-[#fdfcff] border-[#e8e0f0]">
        <DialogHeader>
          <DialogTitle className="text-[#3d3d5c]">
            项目解析配置
            {projectTitle && (
              <span className="text-sm font-normal text-[#9090aa] ml-2">
                — {projectTitle}
              </span>
            )}
          </DialogTitle>
        </DialogHeader>

        <div className="space-y-5 py-4">
          {/* KB Selector */}
          <div className="space-y-2">
            <label className="text-sm font-medium text-[#80809e]">
              选择知识库
            </label>
            <RAGFlowSelect
              value={selectedKb}
              onChange={(val) => setSelectedKb(val || '')}
              options={kbs.map((kb: any) => ({
                label: kb.name,
                value: kb.id,
              }))}
              placeholder={kbsLoading ? '加载中...' : '请选择知识库'}
              allowClear
              disabled={
                parseStatus?.status === 'parsing' ||
                parseStatus?.status === 'pending'
              }
              triggerClassName="bg-white border-[#e0d8ec] text-[#2d2d4a] hover:bg-[#f5f1fa] hover:border-[#c8b8e8] hover:text-[#2d2d4a]"
            />
          </div>

          {/* Progress Display */}
          {showProgress && (
            <div className="rounded-lg border border-[#e8e0f0] bg-[#faf8fd] p-4 space-y-3">
              <div className="flex items-center gap-2">
                {statusIcon(parseStatus.status)}
                <span className="text-sm font-medium text-[#4a4a6a]">
                  {statusLabel(parseStatus.status)}
                </span>
              </div>

              <Progress
                value={Math.round((parseStatus.progress || 0) * 100)}
                className="h-2 [&>div]:bg-[#a78bfa]"
              />

              {parseStatus.progress_msg && (
                <p className="text-xs text-[#9090aa]">
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
            className="border-[#e0d8ec] text-[#80809e] hover:bg-[#f5f1fa] hover:text-[#5a5a7a]"
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
            className="bg-[#9b8aef] hover:bg-[#8b7ae0]"
          >
            开始解析
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
