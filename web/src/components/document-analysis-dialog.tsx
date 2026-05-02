import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import {
  useListAnalysisTemplates,
} from '@/hooks/use-analysis-template-request';
import { useAnalyzeDocument, useGetDocumentAnalysis, useCancelDocumentAnalysis, type AnalysisSection } from '@/hooks/use-document-analysis-request';
import { useTranslation } from 'react-i18next';
import { useState, useEffect, useRef } from 'react';
import { Loader2, CheckCircle2, AlertCircle, RefreshCw, ChevronLeft, ChevronRight } from 'lucide-react';
import { Separator } from '@/components/ui/separator';
import { ScrollArea } from '@/components/ui/scroll-area';

interface DocumentAnalysisDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  documentId: string;
  documentName?: string;
}

type AnalysisStatus = 'idle' | 'loading' | 'pending' | 'running' | 'completed' | 'failed';

export function DocumentAnalysisDialog({
  open,
  onOpenChange,
  documentId,
  documentName,
}: DocumentAnalysisDialogProps) {
  const { t } = useTranslation('translation', { keyPrefix: 'documentAnalysis' });

  const [selectedTemplateId, setSelectedTemplateId] = useState<string>('');
  const [error, setError] = useState<string>('');
  const [status, setStatus] = useState<AnalysisStatus>('idle');
  const [progress, setProgress] = useState<number>(0);
  const [sections, setSections] = useState<AnalysisSection[]>([]);
  const [activeSectionIndex, setActiveSectionIndex] = useState<number>(0);
  const [errorMessage, setErrorMessage] = useState<string>('');
  const [templateName, setTemplateName] = useState<string>('');
  const [taskId, setTaskId] = useState<string>('');
  const [hasCheckedExistingTask, setHasCheckedExistingTask] = useState<boolean>(false);

  const { data: templatesData, isLoading: templatesLoading } = useListAnalysisTemplates({}, open);
  const { mutate: analyzeDocument, isPending: isAnalyzing } = useAnalyzeDocument();
  const { mutate: cancelAnalysis, isPending: isCanceling } = useCancelDocumentAnalysis();

  // 获取分析结果 - 弹框打开时启用
  // 注意：这里 enabled 始终为 true（当 open 时），这样轮询才会工作
  const { data: analysisResult, refetch } = useGetDocumentAnalysis(documentId, taskId, true);

  const templates = templatesData?.data || [];

  // 用于跟踪是否启动了新任务
  const justStartedRef = useRef(false);

  // 默认选择第一个模板
  useEffect(() => {
    if (templates.length > 0 && !selectedTemplateId) {
      setSelectedTemplateId(templates[0].id);
    }
  }, [templates, selectedTemplateId]);

  // 监听分析结果变化 - 这是进度更新的关键
  useEffect(() => {
    if (!analysisResult) {
      // 没有分析结果
      if (!justStartedRef.current) {
        // 如果不是刚启动的任务，才重置状态
        setStatus('idle');
        setProgress(0);
        setSections([]);
        setErrorMessage('');
        setTemplateName('');
      }
      return;
    }

    // 更新状态
    const resultStatus = analysisResult.status as AnalysisStatus;

    // 如果刚启动任务，且后端返回 running/pending，清除标记
    if (justStartedRef.current && (resultStatus === 'running' || resultStatus === 'pending')) {
      justStartedRef.current = false;
    }

    // 如果进度为 100 且有 sections，视为完成
    if (analysisResult.progress === 100 && analysisResult.sections?.length > 0) {
      setStatus('completed');
    } else {
      setStatus(resultStatus);
    }

    setProgress(analysisResult.progress || 0);
    setSections(analysisResult.sections || []);
    setErrorMessage(analysisResult.error_message || '');
    setTemplateName(analysisResult.template_name || '');
  }, [analysisResult]);

  // 弹框打开时检查是否有正在进行的任务
  useEffect(() => {
    if (open && documentId) {
      // 重置状态
      setHasCheckedExistingTask(false);
      setActiveSectionIndex(0);
      setError('');
      justStartedRef.current = false;

      // 触发获取数据
      refetch().then((result) => {
        setHasCheckedExistingTask(true);

        if (result.data) {
          const resultStatus = result.data.status as AnalysisStatus;
          // 如果有正在进行的任务，设置状态
          if (resultStatus === 'pending' || resultStatus === 'running') {
            setStatus(resultStatus);
            setProgress(result.data.progress || 0);
            setTemplateName(result.data.template_name || '');
          } else if (resultStatus === 'completed' || resultStatus === 'failed') {
            // 如果已完成或失败，也设置状态
            setStatus(resultStatus);
            setProgress(result.data.progress || 0);
            setTemplateName(result.data.template_name || '');
            if (result.data.sections) {
              setSections(result.data.sections);
            }
            if (result.data.error_message) {
              setErrorMessage(result.data.error_message);
            }
          } else {
            // 否则显示空闲状态
            setStatus('idle');
          }
        } else {
          // 没有分析结果，显示空闲状态
          setStatus('idle');
        }
      });
    }
  }, [open, documentId, refetch]);

  const handleStartAnalysis = () => {
    if (!documentId || !selectedTemplateId) return;
    setError('');
    setTaskId('');
    setStatus('pending');
    setProgress(0);
    setSections([]);
    setErrorMessage('');
    justStartedRef.current = true;

    analyzeDocument(
      {
        documentId,
        params: { template_id: selectedTemplateId },
      },
      {
        onSuccess: (data) => {
          if (data?.task_id) {
            setTaskId(data.task_id);
            // 立即触发一次获取，开始轮询
            setTimeout(() => refetch(), 100);
          } else {
            setError('启动分析失败：未返回任务ID');
            setStatus('idle');
            justStartedRef.current = false;
          }
        },
        onError: (err: any) => {
          console.error('Analysis error:', err);
          setError(err?.message || '启动分析失败');
          setStatus('idle');
          justStartedRef.current = false;
        },
      }
    );
  };

  const handleReAnalyze = () => {
    setStatus('idle');
    setTaskId('');
    setSections([]);
    setProgress(0);
    justStartedRef.current = false;
  };

  const handleCancelAnalysis = () => {
    if (!documentId) return;
    cancelAnalysis(
      { documentId, taskId },
      {
        onSuccess: () => {
          // 取消成功后，重新获取状态
          refetch();
        },
        onError: (err) => {
          console.error('Cancel error:', err);
        },
      }
    );
  };

  // 渲染章节内容
  const renderSectionContent = (section: AnalysisSection | undefined) => {
    if (!section) return null;

    const analyses = section.analyses || [];

    return (
      <div className="space-y-4">
        <div className="text-sm font-medium text-muted-foreground">
          {section.section_title}
        </div>
        <Separator />
        {analyses.map((analysis, idx) => (
          <div key={idx} className="space-y-1">
            <div className="text-sm font-medium text-primary">
              {analysis.analysis_type === 'key_points' ? '关键要点' : analysis.analysis_type}
            </div>
            <div className="text-sm whitespace-pre-wrap">{analysis.result}</div>
          </div>
        ))}
      </div>
    );
  };

  // 加载中（正在检查是否有现有任务）
  if (!hasCheckedExistingTask) {
    return (
      <Dialog open={open} onOpenChange={onOpenChange}>
        <DialogContent className="sm:max-w-[500px]">
          <DialogHeader>
            <DialogTitle>{t('analyzeDocument')}</DialogTitle>
          </DialogHeader>
          <div className="flex items-center justify-center py-8">
            <Loader2 className="size-6 animate-spin text-primary" />
            <span className="ml-2 text-muted-foreground">加载中...</span>
          </div>
        </DialogContent>
      </Dialog>
    );
  }

  // 分析中状态
  if (status === 'pending' || status === 'running') {
    return (
      <Dialog open={open} onOpenChange={onOpenChange}>
        <DialogContent className="sm:max-w-[400px]">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <Loader2 className="size-5 animate-spin text-primary" />
              正在分析文档
            </DialogTitle>
            <DialogDescription>
              {templateName && `使用模板: ${templateName}`}
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-4 py-4">
            <div className="space-y-2">
              <div className="flex justify-between text-sm">
                <span className="text-muted-foreground">分析进度</span>
                <span className="font-medium">{progress}%</span>
              </div>
              <div className="w-full bg-gray-200 dark:bg-gray-800 rounded-full h-2 overflow-hidden">
                <div
                  className="bg-primary h-2 rounded-full transition-all duration-500"
                  style={{ width: `${progress}%` }}
                />
              </div>
            </div>
            <p className="text-sm text-muted-foreground text-center">
              {progress === 0 ? '正在初始化分析任务...' : `已处理约 ${progress}% 的内容`}
            </p>
          </div>
          <div className="flex justify-end gap-2">
            <Button
              variant="outline"
              onClick={handleCancelAnalysis}
              disabled={isCanceling}
            >
              {isCanceling ? (
                <>
                  <Loader2 className="size-4 mr-2 animate-spin" />
                  取消中...
                </>
              ) : (
                '取消分析'
              )}
            </Button>
          </div>
        </DialogContent>
      </Dialog>
    );
  }

  // 失败状态
  if (status === 'failed') {
    return (
      <Dialog open={open} onOpenChange={onOpenChange}>
        <DialogContent className="sm:max-w-[400px]">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2 text-red-500">
              <AlertCircle className="size-5" />
              分析失败
            </DialogTitle>
          </DialogHeader>
          <div className="space-y-4 py-4">
            <div className="bg-red-50 dark:bg-red-950/20 border border-red-200 dark:border-red-900 rounded-lg p-3">
              <p className="text-sm text-red-800 dark:text-red-200">{errorMessage || '未知错误'}</p>
            </div>
            <div className="flex justify-end gap-2">
              <Button variant="outline" onClick={() => onOpenChange(false)}>
                关闭
              </Button>
              <Button onClick={handleReAnalyze}>
                <RefreshCw className="size-4 mr-2" />
                重新分析
              </Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>
    );
  }

  // 完成状态
  if (status === 'completed' && sections.length > 0) {
    const activeSection = sections[activeSectionIndex];

    return (
      <Dialog open={open} onOpenChange={onOpenChange}>
        <DialogContent className="sm:max-w-[700px] max-h-[80vh]">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <CheckCircle2 className="size-5 text-green-500" />
              分析完成
            </DialogTitle>
            <DialogDescription>
              {templateName && `模板: ${templateName} · `}
              共 {sections.length} 个章节
            </DialogDescription>
          </DialogHeader>

          <div className="flex gap-4 py-2">
            {/* 左侧章节列表 */}
            <div className="w-48 flex-shrink-0">
              <ScrollArea className="h-[400px]">
                <div className="space-y-1 pr-2">
                  {sections.map((section, index) => (
                    <button
                      key={index}
                      onClick={() => setActiveSectionIndex(index)}
                      className={`w-full text-left px-3 py-2 text-sm rounded-md transition-colors truncate ${
                        index === activeSectionIndex
                          ? 'bg-primary text-primary-foreground'
                          : 'hover:bg-muted'
                      }`}
                      title={section.section_title}
                    >
                      {section.section_title}
                    </button>
                  ))}
                </div>
              </ScrollArea>
            </div>

            {/* 右侧内容 */}
            <div className="flex-1 min-w-0">
              <ScrollArea className="h-[400px]">
                <div className="pr-2">
                  {renderSectionContent(activeSection)}
                </div>
              </ScrollArea>
            </div>
          </div>

          {/* 底部导航 */}
          <div className="flex items-center justify-between pt-2 border-t">
            <div className="flex items-center gap-2">
              <Button
                variant="outline"
                size="sm"
                disabled={activeSectionIndex === 0}
                onClick={() => setActiveSectionIndex(i => i - 1)}
              >
                <ChevronLeft className="size-4" />
                上一节
              </Button>
              <Button
                variant="outline"
                size="sm"
                disabled={activeSectionIndex === sections.length - 1}
                onClick={() => setActiveSectionIndex(i => i + 1)}
              >
                下一节
                <ChevronRight className="size-4" />
              </Button>
            </div>
            <div className="flex items-center gap-2">
              <Button variant="outline" onClick={() => onOpenChange(false)}>
                关闭
              </Button>
              <Button variant="outline" onClick={handleReAnalyze}>
                <RefreshCw className="size-4 mr-2" />
                重新分析
              </Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>
    );
  }

  // 空闲状态 - 选择模板启动分析
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-[400px]">
        <DialogHeader>
          <DialogTitle>{t('analyzeDocument')}</DialogTitle>
          <DialogDescription>
            {documentName && `"${documentName}"`}
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4 py-4">
          {templatesLoading ? (
            <div className="flex items-center justify-center py-4">
              <Loader2 className="size-5 animate-spin text-primary" />
            </div>
          ) : (
            <>
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
            </>
          )}
        </div>

        <div className="flex justify-end gap-2">
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            取消
          </Button>
          <Button
            onClick={handleStartAnalysis}
            disabled={!selectedTemplateId || isAnalyzing || templates.length === 0}
          >
            {isAnalyzing ? (
              <>
                <Loader2 className="size-4 mr-2 animate-spin" />
                启动中...
              </>
            ) : (
              t('analyzeDocument')
            )}
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}
