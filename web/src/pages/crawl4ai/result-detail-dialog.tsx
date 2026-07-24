import { Badge } from '@/components/ui/badge';
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import {
  CollectionResult,
  getCollectionResult,
} from '@/services/collection-service';
import { useQuery } from '@tanstack/react-query';
import { ExternalLink, FileText } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { EXT_FIELD_LABELS, badgeColorFor } from './field-labels';

interface ResultDetailDialogProps {
  resultId: string;
  hideModal: () => void;
}

export function ResultDetailDialog({
  resultId,
  hideModal,
}: ResultDetailDialogProps) {
  const { t } = useTranslation();

  const { data: result, isFetching } = useQuery<CollectionResult | null>({
    queryKey: ['collectionResult', resultId],
    queryFn: async () => {
      const { data: res } = await getCollectionResult(resultId);
      return res?.code === 0 ? res.data : null;
    },
  });

  // 按 category 取扩展字段标签映射；遍历 ext 渲染键值对表格
  const category = result?.category ?? '';
  const extLabels = EXT_FIELD_LABELS[category] ?? {};
  const extEntries = Object.entries(result?.ext ?? {})
    .filter(([k, v]) => {
      // 跳过空值 + 跳过内部字段
      if (
        [
          'result_id',
          'create_time',
          'create_date',
          'update_time',
          'update_date',
          'id',
        ].includes(k)
      ) {
        return false;
      }
      return v !== null && v !== '' && v !== undefined;
    })
    .map(([k, v]) => [extLabels[k] ?? k, v as string]);

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
              {(result.section_name || result.category_label) && (
                <Badge
                  variant="secondary"
                  className={badgeColorFor(
                    result.category,
                    result.section_name,
                  )}
                >
                  {result.section_name || result.category_label}
                </Badge>
              )}
              <span>{result.site_display || result.site_id}</span>
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
                  {extEntries.length > 0 && (
                    <Badge
                      variant="secondary"
                      className="ml-1.5 bg-purple-500/15 text-purple-600"
                    >
                      {extEntries.length}
                    </Badge>
                  )}
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
                {/* 已识别的结构化扩展字段（带中文标签） */}
                {extEntries.length > 0 && (
                  <section className="mb-4">
                    <h3 className="text-sm font-medium mb-2 text-muted-foreground">
                      {t('crawl4ai.structuredData')}
                    </h3>
                    <Table>
                      <TableHeader>
                        <TableRow>
                          <TableHead className="w-40">
                            {t('crawl4ai.fieldName')}
                          </TableHead>
                          <TableHead>{t('crawl4ai.fieldValue')}</TableHead>
                        </TableRow>
                      </TableHeader>
                      <TableBody>
                        {extEntries.map(([label, value]) => (
                          <TableRow key={label}>
                            <TableCell className="font-medium bg-muted/40 align-top">
                              {label}
                            </TableCell>
                            <TableCell className="whitespace-pre-wrap break-words">
                              {String(value)}
                            </TableCell>
                          </TableRow>
                        ))}
                      </TableBody>
                    </Table>
                  </section>
                )}

                {/* 原始 extracted_json dump（兜底，供调试） */}
                <section>
                  <h3 className="text-sm font-medium mb-2 text-muted-foreground">
                    {t('crawl4ai.rawExtractedJson')}
                  </h3>
                  <pre className="text-xs p-4 rounded-lg border bg-card overflow-auto">
                    {JSON.stringify(result.extracted_json ?? {}, null, 2)}
                  </pre>
                </section>
              </TabsContent>
            </Tabs>
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}
