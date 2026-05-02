import { Button } from '@/components/ui/button';
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/components/ui/card';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import {
  useListAnalysisTemplates,
  type AnalysisTemplate,
} from '@/hooks/use-analysis-template-request';
import { useAnalyzeDocument } from '@/hooks/use-document-analysis-request';
import { useNavigate, useParams } from 'react-router';
import { useTranslation } from 'react-i18next';
import { useState, useEffect } from 'react';
import { Loader2 } from 'lucide-react';

export function AnalysisStarter() {
  const { documentId } = useParams<{ documentId: string }>();
  const { t } = useTranslation('translation', { keyPrefix: 'documentAnalysis' });
  const navigate = useNavigate();
  const [selectedTemplateId, setSelectedTemplateId] = useState<string>('');
  const [error, setError] = useState<string>('');

  const { data: templatesData, isLoading } = useListAnalysisTemplates({});
  const { mutate: analyzeDocument, isPending } = useAnalyzeDocument();

  const templates = templatesData?.data || [];

  useEffect(() => {
    // 默认选择第一个模板
    if (templates.length > 0 && !selectedTemplateId) {
      setSelectedTemplateId(templates[0].id);
    }
  }, [templates, selectedTemplateId]);

  const handleStartAnalysis = () => {
    if (!documentId || !selectedTemplateId) return;
    setError('');

    analyzeDocument(
      {
        documentId,
        params: { template_id: selectedTemplateId },
      },
      {
        onSuccess: (data) => {
          console.log('Analysis started:', data);
          if (data?.task_id) {
            // 跳转到分析结果页面
            navigate(`/document-analysis/${documentId}?task_id=${data.task_id}`);
          } else {
            setError('启动分析失败：未返回任务ID');
          }
        },
        onError: (err: any) => {
          console.error('Analysis error:', err);
          setError(err?.message || '启动分析失败');
        },
      }
    );
  };

  if (isLoading) {
    return (
      <div className="flex items-center justify-center h-[50vh]">
        <Loader2 className="size-8 animate-spin text-primary" />
      </div>
    );
  }

  return (
    <div className="flex items-center justify-center h-[calc(100vh-200px)]">
      <Card className="w-full max-w-md">
        <CardHeader>
          <CardTitle>{t('analyzeDocument')}</CardTitle>
          <CardDescription>{t('selectTemplate')}</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="space-y-2">
            <label className="text-sm font-medium">{t('selectTemplate')}</label>
            <Select value={selectedTemplateId} onValueChange={setSelectedTemplateId}>
              <SelectTrigger>
                <SelectValue placeholder={t('selectTemplate')} />
              </SelectTrigger>
              <SelectContent>
                {templates.map((template) => (
                  <SelectItem key={template.id} value={template.id}>
                    {template.name}
                    {template.is_system ? ` (${t('defaultTemplate')})` : ` (${t('customTemplate')})`}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          {templates.length === 0 && (
            <p className="text-sm text-muted-foreground">
              暂无分析模板，请先在「用户设置 → 分析模板」中创建模板。
            </p>
          )}

          {error && (
            <div className="text-sm text-red-500 bg-red-50 dark:bg-red-950/20 p-2 rounded">
              {error}
            </div>
          )}

          <div className="flex gap-3 justify-end">
            <Button variant="outline" onClick={() => navigate(-1)}>
              取消
            </Button>
            <Button
              onClick={handleStartAnalysis}
              disabled={!selectedTemplateId || isPending || templates.length === 0}
            >
              {isPending ? (
                <>
                  <Loader2 className="size-4 mr-2 animate-spin" />
                  启动中...
                </>
              ) : (
                t('analyzeDocument')
              )}
            </Button>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}

export default AnalysisStarter;
