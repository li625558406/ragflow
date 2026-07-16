import { Badge } from '@/components/ui/badge';
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Crawl4aiResult, getCrawl4aiResult } from '@/services/crawl4ai-service';
import { useQuery } from '@tanstack/react-query';
import { ExternalLink, FileText } from 'lucide-react';
import { useTranslation } from 'react-i18next';

interface ResultDetailDialogProps {
  resultId: string;
  hideModal: () => void;
}

export function ResultDetailDialog({
  resultId,
  hideModal,
}: ResultDetailDialogProps) {
  const { t } = useTranslation();

  const { data: result, isFetching } = useQuery<Crawl4aiResult | null>({
    queryKey: ['crawl4aiResult', resultId],
    queryFn: async () => {
      const { data: res } = await getCrawl4aiResult(resultId);
      return res?.code === 0 ? res.data : null;
    },
  });

  return (
    <Dialog open onOpenChange={(open) => !open && hideModal()}>
      <DialogContent className="max-w-4xl max-h-[85vh] flex flex-col">
        <DialogHeader>
          <DialogTitle className="pr-8 leading-snug">
            {result?.title || t('crawl4ai.resultDetail')}
          </DialogTitle>
        </DialogHeader>

        {isFetching && (
          <div className="py-10 text-center text-muted-foreground">
            {t('crawl4ai.loading')}
          </div>
        )}

        {result && (
          <div className="flex-1 min-h-0 flex flex-col gap-3">
            <div className="flex flex-wrap items-center gap-2 text-sm text-muted-foreground">
              <span>{result.site_id}</span>
              <span>·</span>
              <span>{result.publish_date || '-'}</span>
              <span>·</span>
              <a
                href={result.source_url}
                target="_blank"
                rel="noreferrer"
                className="inline-flex items-center gap-1 text-primary hover:underline"
              >
                {t('crawl4ai.sourceLink')}
                <ExternalLink className="size-3" />
              </a>
              {result.kb_doc_id && (
                <Badge
                  variant="secondary"
                  className="bg-green-500/15 text-green-600"
                >
                  {t('crawl4ai.statusKbUploaded')}
                </Badge>
              )}
            </div>

            <Tabs
              defaultValue="content"
              className="flex-1 min-h-0 flex flex-col"
            >
              <TabsList className="w-fit">
                <TabsTrigger value="content">
                  {t('crawl4ai.content')}
                </TabsTrigger>
                <TabsTrigger value="attachments">
                  {t('crawl4ai.attachments')} ({result.attachments?.length ?? 0}
                  )
                </TabsTrigger>
                <TabsTrigger value="structured">
                  {t('crawl4ai.structuredData')}
                </TabsTrigger>
              </TabsList>

              <TabsContent
                value="content"
                className="flex-1 min-h-0 overflow-auto mt-3"
              >
                {result.markdown ? (
                  <pre className="whitespace-pre-wrap break-words text-sm p-4 rounded-lg border bg-card font-sans">
                    {result.markdown}
                  </pre>
                ) : (
                  <div className="py-10 text-center text-muted-foreground">
                    {t('crawl4ai.noContent')}
                  </div>
                )}
                {result.error_msg && (
                  <p className="mt-2 text-sm text-red-500">
                    {result.error_msg}
                  </p>
                )}
              </TabsContent>

              <TabsContent
                value="attachments"
                className="flex-1 min-h-0 overflow-auto mt-3"
              >
                {result.attachments?.length ? (
                  <ul className="space-y-2">
                    {result.attachments.map((att, i) => (
                      <li
                        key={`${att.file_url}-${i}`}
                        className="flex items-center gap-2 p-3 rounded-lg border bg-card"
                      >
                        <FileText className="size-4 text-muted-foreground shrink-0" />
                        <a
                          href={att.file_url}
                          target="_blank"
                          rel="noreferrer"
                          className="flex-1 truncate text-sm hover:underline"
                        >
                          {att.file_name}
                        </a>
                        <Badge variant="secondary">
                          {t(
                            `crawl4ai.attStatus_${att.status ?? 'pending'}`,
                            att.status ?? 'pending',
                          )}
                        </Badge>
                      </li>
                    ))}
                  </ul>
                ) : (
                  <div className="py-10 text-center text-muted-foreground">
                    {t('crawl4ai.noAttachments')}
                  </div>
                )}
              </TabsContent>

              <TabsContent
                value="structured"
                className="flex-1 min-h-0 overflow-auto mt-3"
              >
                <pre className="text-xs p-4 rounded-lg border bg-card overflow-auto">
                  {JSON.stringify(result.extracted_json ?? {}, null, 2)}
                </pre>
              </TabsContent>
            </Tabs>
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}
