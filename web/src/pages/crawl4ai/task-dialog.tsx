import { Button } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/checkbox';
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import message from '@/components/ui/message';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { Textarea } from '@/components/ui/textarea';
import {
  Crawl4aiTask,
  createCrawl4aiTask,
  fetchKnowledgeBases,
  updateCrawl4aiTask,
} from '@/services/crawl4ai-service';
import { useQuery } from '@tanstack/react-query';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';

interface TaskDialogProps {
  editingTask: Crawl4aiTask | null;
  hideModal: () => void;
  onSaved: () => void;
}

const DEFAULT_SCHEMA = JSON.stringify(
  {
    name: 'Items',
    baseSelector: 'ul.list li',
    fields: [
      { name: 'title', selector: 'a', type: 'text' },
      { name: 'url', selector: 'a', type: 'attribute', attribute: 'href' },
      { name: 'date', selector: 'span.date', type: 'text' },
    ],
  },
  null,
  2,
);

const DEFAULT_DETAIL_CONFIG = JSON.stringify(
  {
    enabled: true,
    url_field: 'url',
    content_selector: '',
    attachment_extensions: ['.pdf', '.doc', '.docx', '.xls', '.xlsx', '.zip'],
  },
  null,
  2,
);

export function TaskDialog({
  editingTask,
  hideModal,
  onSaved,
}: TaskDialogProps) {
  const { t } = useTranslation();

  const [name, setName] = useState(editingTask?.name ?? '');
  const [siteId, setSiteId] = useState(editingTask?.site_id ?? '');
  const [targetUrl, setTargetUrl] = useState(editingTask?.target_url ?? '');
  const [pageUrlTemplate, setPageUrlTemplate] = useState(
    editingTask?.page_url_template ?? '',
  );
  const [maxPages, setMaxPages] = useState(editingTask?.max_pages ?? 1);
  const [schemaText, setSchemaText] = useState(
    editingTask?.extraction_schema &&
      Object.keys(editingTask.extraction_schema).length
      ? JSON.stringify(editingTask.extraction_schema, null, 2)
      : DEFAULT_SCHEMA,
  );
  const [detailText, setDetailText] = useState(
    editingTask?.detail_config && Object.keys(editingTask.detail_config).length
      ? JSON.stringify(editingTask.detail_config, null, 2)
      : DEFAULT_DETAIL_CONFIG,
  );
  const [uploadKb, setUploadKb] = useState(
    (editingTask?.output_targets ?? []).includes('kb'),
  );
  const [kbId, setKbId] = useState(editingTask?.kb_id ?? '');
  const [saving, setSaving] = useState(false);

  const { data: kbs = [] } = useQuery({
    queryKey: ['crawl4aiKbList'],
    queryFn: fetchKnowledgeBases,
  });

  const handleSave = async () => {
    if (!name.trim() || !siteId.trim() || !targetUrl.trim()) {
      message.error(t('crawl4ai.requiredFieldsMissing'));
      return;
    }
    let schema: Record<string, any>;
    let detailConfig: Record<string, any>;
    try {
      schema = JSON.parse(schemaText);
      detailConfig = JSON.parse(detailText);
    } catch {
      message.error(t('crawl4ai.invalidJson'));
      return;
    }
    if (uploadKb && !kbId) {
      message.error(t('crawl4ai.kbRequired'));
      return;
    }

    const payload = {
      name: name.trim(),
      site_id: siteId.trim(),
      target_url: targetUrl.trim(),
      page_url_template: pageUrlTemplate.trim(),
      max_pages: Number(maxPages) || 1,
      extraction_schema: schema,
      detail_config: detailConfig,
      output_targets: uploadKb ? ['db', 'kb'] : ['db'],
      kb_id: uploadKb ? kbId : '',
    };

    setSaving(true);
    try {
      const { data: res } = editingTask
        ? await updateCrawl4aiTask(editingTask.id, payload)
        : await createCrawl4aiTask(payload);
      if (res?.code === 0) {
        message.success(t('crawl4ai.saveSuccess'));
        onSaved();
        hideModal();
      } else {
        message.error(res?.message ?? t('crawl4ai.saveFailed'));
      }
    } finally {
      setSaving(false);
    }
  };

  return (
    <Dialog open onOpenChange={(open) => !open && hideModal()}>
      <DialogContent className="max-w-2xl max-h-[85vh] overflow-auto">
        <DialogHeader>
          <DialogTitle>
            {editingTask ? t('crawl4ai.editTask') : t('crawl4ai.createTask')}
          </DialogTitle>
        </DialogHeader>

        <div className="space-y-4">
          <div className="grid grid-cols-2 gap-4">
            <div className="space-y-1.5">
              <Label>{t('crawl4ai.taskName')} *</Label>
              <Input value={name} onChange={(e) => setName(e.target.value)} />
            </div>
            <div className="space-y-1.5">
              <Label>{t('crawl4ai.siteId')} *</Label>
              <Input
                value={siteId}
                onChange={(e) => setSiteId(e.target.value)}
                placeholder="ccgp_zygg"
              />
            </div>
          </div>

          <div className="space-y-1.5">
            <Label>{t('crawl4ai.targetUrl')} *</Label>
            <Input
              value={targetUrl}
              onChange={(e) => setTargetUrl(e.target.value)}
              placeholder="https://example.com/list/index.html"
            />
          </div>

          <div className="grid grid-cols-2 gap-4">
            <div className="space-y-1.5">
              <Label>{t('crawl4ai.pageUrlTemplate')}</Label>
              <Input
                value={pageUrlTemplate}
                onChange={(e) => setPageUrlTemplate(e.target.value)}
                placeholder="https://example.com/list/index_{page}.html"
              />
            </div>
            <div className="space-y-1.5">
              <Label>{t('crawl4ai.maxPages')}</Label>
              <Input
                type="number"
                min={1}
                max={50}
                value={maxPages}
                onChange={(e) => setMaxPages(Number(e.target.value))}
              />
            </div>
          </div>

          <div className="space-y-1.5">
            <Label>{t('crawl4ai.extractionSchema')} *</Label>
            <Textarea
              className="font-mono text-xs min-h-[160px]"
              value={schemaText}
              onChange={(e) => setSchemaText(e.target.value)}
            />
            <p className="text-xs text-muted-foreground">
              {t('crawl4ai.extractionSchemaHint')}
            </p>
          </div>

          <div className="space-y-1.5">
            <Label>{t('crawl4ai.detailConfig')}</Label>
            <Textarea
              className="font-mono text-xs min-h-[120px]"
              value={detailText}
              onChange={(e) => setDetailText(e.target.value)}
            />
            <p className="text-xs text-muted-foreground">
              {t('crawl4ai.detailConfigHint')}
            </p>
          </div>

          <div className="flex items-center gap-3">
            <div className="flex items-center gap-2">
              <Checkbox
                id="crawl4ai-upload-kb"
                checked={uploadKb}
                onCheckedChange={(v) => setUploadKb(v === true)}
              />
              <Label htmlFor="crawl4ai-upload-kb">
                {t('crawl4ai.uploadToKb')}
              </Label>
            </div>
            {uploadKb && (
              <Select value={kbId} onValueChange={setKbId}>
                <SelectTrigger className="w-64">
                  <SelectValue placeholder={t('crawl4ai.selectKb')} />
                </SelectTrigger>
                <SelectContent>
                  {kbs.map((kb: any) => (
                    <SelectItem key={kb.id} value={kb.id}>
                      {kb.name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            )}
          </div>
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={hideModal}>
            {t('common.cancel')}
          </Button>
          <Button onClick={handleSave} disabled={saving}>
            {t('common.save')}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
