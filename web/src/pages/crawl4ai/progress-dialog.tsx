import { Badge } from '@/components/ui/badge';
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { useCrawlerProgress } from '@/hooks/use-crawler-progress';
import { useEffect, useRef } from 'react';
import { useTranslation } from 'react-i18next';

interface ProgressDialogProps {
  taskId: string | null;
  taskName?: string;
  hideModal: () => void;
}

const LEVEL_COLORS: Record<string, string> = {
  info: 'text-muted-foreground',
  warning: 'text-yellow-600',
  error: 'text-red-600',
};

export function ProgressDialog({
  taskId,
  taskName,
  hideModal,
}: ProgressDialogProps) {
  const { t } = useTranslation();
  const { progress, logs, done, connected } = useCrawlerProgress(taskId);
  const logEndRef = useRef<HTMLDivElement>(null);

  // Autoscroll to bottom on new logs
  useEffect(() => {
    logEndRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' });
  }, [logs.length]);

  const pagePct =
    progress && progress.total_pages > 0
      ? Math.min(100, Math.round((progress.page / progress.total_pages) * 100))
      : progress && progress.page > 0
        ? Math.min(95, progress.page * 10) // indeterminate-ish
        : 0;

  const isRunning = !!taskId && !done;
  const isDone = !!done;

  return (
    <Dialog open={!!taskId} onOpenChange={(open) => !open && hideModal()}>
      <DialogContent className="max-w-3xl max-h-[85vh] flex flex-col">
        <DialogHeader>
          <DialogTitle className="pr-8 leading-snug flex items-center gap-2">
            <span>{taskName || t('crawl4ai.progressTitle')}</span>
            {connected && isRunning && (
              <Badge
                variant="secondary"
                className="bg-yellow-500/15 text-yellow-600"
              >
                {t('crawl4ai.live')}
              </Badge>
            )}
            {isDone && (
              <Badge
                variant="secondary"
                className={
                  done.status === 'success'
                    ? 'bg-green-500/15 text-green-600'
                    : done.status === 'skipped'
                      ? 'bg-gray-500/15 text-gray-600'
                      : 'bg-red-500/15 text-red-600'
                }
              >
                {t(`crawl4ai.run_${done.status}`, done.status)}
              </Badge>
            )}
          </DialogTitle>
        </DialogHeader>

        {/* Progress section */}
        <section className="space-y-2">
          {progress ? (
            <>
              <div className="flex items-center justify-between text-sm">
                <span className="text-muted-foreground">
                  {progress.total_pages > 0
                    ? t('crawl4ai.pageOf', {
                        current: progress.page,
                        total: progress.total_pages,
                      })
                    : t('crawl4ai.pageUnknown', { current: progress.page })}
                </span>
                <span className="text-muted-foreground">
                  {t('crawl4ai.newCount', { count: progress.new })} ·{' '}
                  {t('crawl4ai.scannedCount', { count: progress.scanned })}
                </span>
              </div>
              <div className="h-2 w-full bg-muted rounded-full overflow-hidden">
                <div
                  className={
                    isDone
                      ? 'h-full bg-green-500 transition-all duration-300'
                      : 'h-full bg-primary transition-all duration-300'
                  }
                  style={{ width: `${pagePct}%` }}
                />
              </div>
            </>
          ) : (
            <div className="text-sm text-muted-foreground py-2">
              {connected
                ? t('crawl4ai.waitingForProgress')
                : t('crawl4ai.connecting')}
            </div>
          )}
        </section>

        {/* Done summary */}
        {done && done.summary && (
          <section className="p-3 rounded-lg border bg-card text-sm">
            <div className="font-medium mb-1">{t('crawl4ai.finalSummary')}</div>
            <pre className="whitespace-pre-wrap break-words text-xs text-muted-foreground max-h-40 overflow-auto">
              {JSON.stringify(done.summary, null, 2)}
            </pre>
          </section>
        )}

        {/* Log panel */}
        <section className="flex-1 min-h-0 flex flex-col">
          <div className="text-xs font-medium text-muted-foreground mb-1">
            {t('crawl4ai.logTitle')}
          </div>
          <div className="flex-1 min-h-[280px] max-h-[400px] overflow-auto rounded-lg border bg-black/90 p-3">
            {logs.length === 0 ? (
              <div className="text-xs text-muted-foreground py-6 text-center">
                {t('crawl4ai.noLogs')}
              </div>
            ) : (
              <div className="space-y-0.5 font-mono text-xs">
                {logs.map((l, i) => (
                  <div
                    key={i}
                    className={`whitespace-pre-wrap break-words ${LEVEL_COLORS[l.level] ?? 'text-gray-300'}`}
                  >
                    <span className="text-gray-500 mr-2">
                      {l.ts ? new Date(l.ts * 1000).toLocaleTimeString() : ''}
                    </span>
                    {l.text}
                  </div>
                ))}
                <div ref={logEndRef} />
              </div>
            )}
          </div>
        </section>
      </DialogContent>
    </Dialog>
  );
}
