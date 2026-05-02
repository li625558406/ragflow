import request from '@/utils/request';
import api from '@/utils/api';
import React, { useCallback, useEffect, useState } from 'react';
import { useParams, useSearchParams, useNavigate } from 'react-router';
import { AnalysisContent } from './components/analysis-content';
import { SectionNav } from './components/section-nav';
import { AnalysisStarter } from './analysis-starter';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/card';
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
import { useAnalyzeDocument } from '@/hooks/use-document-analysis-request';
import { useTranslation } from 'react-i18next';
import { AlertCircle, CheckCircle2, Loader2, RefreshCw } from 'lucide-react';

interface Section {
  section_id: string;
  section_name: string;
  page_range: number[];
  analysis: Record<string, { label: string; content: string }>;
}

type AnalysisStatus = 'idle' | 'loading' | 'pending' | 'running' | 'completed' | 'failed';

export const DocumentAnalysisPage: React.FC = () => {
  const { documentId } = useParams<{ documentId: string }>();
  const [searchParams] = useSearchParams();
  const taskId = searchParams.get('task_id');
  const navigate = useNavigate();
  const [status, setStatus] = useState<AnalysisStatus>('idle');
  const [progress, setProgress] = useState<number>(0);
  const [sections, setSections] = useState<Section[]>([]);
  const [activeSectionId, setActiveSectionId] = useState<string>('');
  const [errorMessage, setErrorMessage] = useState<string>('');
  const [templateName, setTemplateName] = useState<string>('');

  const { data: templatesData } = useListAnalysisTemplates({});
  const { mutate: analyzeDocument, isPending: isAnalyzing } = useAnalyzeDocument();

  const templates = templatesData?.data || [];

  const fetchResult = useCallback(async () => {
    if (!documentId) return;

    // 首次加载时设置 loading 状态
    if (status === 'idle') {
      setStatus('loading');
    }

    try {
      const url = taskId
        ? `${api.getDocumentAnalysis(documentId)}?task_id=${taskId}`
        : api.getDocumentAnalysis(documentId);
      const { data } = await request.get(url);
      const result = data.data;

      if (result.status) {
        setStatus(result.status as AnalysisStatus);
      }
      setProgress(result.progress || 0);
      setSections(result.sections || []);
      setErrorMessage(result.error_message || '');
      setTemplateName(result.template_name || '');

      if (result.sections?.length) {
        setActiveSectionId((prev) => prev || result.sections[0].section_id);
      }
    } catch (error) {
      console.error('Failed to fetch analysis result:', error);
      setStatus('idle');
    }
  }, [documentId, taskId, status]);

  useEffect(() => {
    // 如果有 taskId，自动加载结果
    if (taskId && status === 'idle') {
      fetchResult();
    }
  }, [taskId, status, fetchResult]);

  useEffect(() => {
    // 如果正在分析，轮询更新
    if (status === 'pending' || status === 'running') {
      const interval = setInterval(fetchResult, 2000);
      return () => clearInterval(interval);
    }
  }, [fetchResult, status]);

  const handleReAnalyze = (templateId: string) => {
    if (!documentId) return;
    analyzeDocument(
      {
        documentId,
        params: { template_id: templateId },
      },
      {
        onSuccess: (data) => {
          setStatus('pending');
          setProgress(0);
          setErrorMessage('');
          navigate(`/document-analysis/${documentId}?task_id=${data.task_id}`);
        },
      }
    );
  };

  const handleStartNew = () => {
    setStatus('idle');
    navigate(`/document-analysis/${documentId}`);
  };

  const activeSection = sections.find((s) => s.section_id === activeSectionId);

  // 加载中
  if (status === 'loading') {
    return (
      <div className="flex items-center justify-center h-[calc(100vh-64px)]">
        <div className="flex flex-col items-center gap-4">
          <Loader2 className="size-8 animate-spin text-primary" />
          <p className="text-muted-foreground">加载分析结果...</p>
        </div>
      </div>
    );
  }

  // 空闲状态，显示启动页面
  if (status === 'idle') {
    return <AnalysisStarter />;
  }

  // 失败状态
  if (status === 'failed') {
    return (
      <div className="flex items-center justify-center h-[calc(100vh-64px)] p-4">
        <Card className="w-full max-w-md">
          <CardHeader>
            <div className="flex items-center gap-2">
              <AlertCircle className="size-5 text-red-500" />
              <CardTitle>分析失败</CardTitle>
            </div>
            <CardDescription>文档分析过程中遇到错误</CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="bg-red-50 dark:bg-red-950/20 border border-red-200 dark:border-red-900 rounded-lg p-3">
              <p className="text-sm text-red-800 dark:text-red-200">{errorMessage || '未知错误'}</p>
            </div>
            <div className="flex gap-3 justify-end">
              <Button variant="outline" onClick={() => navigate(-1)}>
                返回文档列表
              </Button>
              <Button onClick={handleStartNew}>
                <RefreshCw className="size-4 mr-2" />
                重新分析
              </Button>
            </div>
          </CardContent>
        </Card>
      </div>
    );
  }

  // 分析中状态
  if (status === 'pending' || status === 'running') {
    return (
      <div className="flex items-center justify-center h-[calc(100vh-64px)] p-4">
        <Card className="w-full max-w-md">
          <CardHeader>
            <div className="flex items-center gap-2">
              <Loader2 className="size-5 animate-spin text-primary" />
              <CardTitle>正在分析文档</CardTitle>
            </div>
            <CardDescription>
              {templateName && `使用模板: ${templateName}`}
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="space-y-2">
              <div className="flex justify-between text-sm">
                <span className="text-muted-foreground">分析进度</span>
                <span className="font-medium">{progress}%</span>
              </div>
              <div className="w-full bg-gray-200 dark:bg-gray-800 rounded-full h-3 overflow-hidden">
                <div
                  className="bg-primary h-3 rounded-full transition-all duration-500"
                  style={{ width: `${progress}%` }}
                />
              </div>
            </div>

            {progress === 0 && (
              <p className="text-sm text-muted-foreground text-center">
                正在初始化分析任务，请稍候...
              </p>
            )}

            {progress > 0 && progress < 100 && (
              <p className="text-sm text-muted-foreground text-center">
                正在分析文档章节，已处理约 {progress}% 的内容
              </p>
            )}

            <div className="flex justify-center pt-2">
              <Button variant="outline" size="sm" onClick={() => navigate(-1)}>
                后台运行，返回文档列表
              </Button>
            </div>
          </CardContent>
        </Card>
      </div>
    );
  }

  // 完成但无结果
  if (status === 'completed' && sections.length === 0) {
    return (
      <div className="flex items-center justify-center h-[calc(100vh-64px)] p-4">
        <Card className="w-full max-w-md">
          <CardHeader>
            <div className="flex items-center gap-2">
              <AlertCircle className="size-5 text-yellow-500" />
              <CardTitle>分析完成但无结果</CardTitle>
            </div>
            <CardDescription>文档分析已完成，但未能识别出有效章节</CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <p className="text-sm text-muted-foreground">
              可能的原因：文档内容为空、文档格式不支持、或分析模板不匹配。
            </p>
            <div className="flex gap-3 justify-end">
              <Button variant="outline" onClick={() => navigate(-1)}>
                返回
              </Button>
              <Select onValueChange={handleReAnalyze}>
                <SelectTrigger className="w-[200px]">
                  <SelectValue placeholder="选择模板重新分析" />
                </SelectTrigger>
                <SelectContent>
                  {templates.map((template) => (
                    <SelectItem key={template.id} value={template.id}>
                      {template.name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          </CardContent>
        </Card>
      </div>
    );
  }

  // 成功完成，显示结果
  return (
    <div className="flex h-[calc(100vh-64px)]">
      <SectionNav
        sections={sections}
        activeId={activeSectionId}
        onSelect={setActiveSectionId}
      />
      <div className="flex-1 flex flex-col">
        <div className="p-4 border-b flex justify-between items-center bg-background/95 backdrop-blur supports-[backdrop-filter]:bg-background/60">
          <div>
            <div className="flex items-center gap-2">
              <CheckCircle2 className="size-5 text-green-500" />
              <h1 className="text-lg font-semibold">分析完成</h1>
            </div>
            <p className="text-sm text-muted-foreground">
              模板: {templateName} · 共 {sections.length} 个章节
            </p>
          </div>
          <div className="flex items-center gap-2">
            <Select onValueChange={handleReAnalyze} disabled={isAnalyzing}>
              <SelectTrigger className="w-[180px]">
                {isAnalyzing ? (
                  <span className="flex items-center gap-2">
                    <Loader2 className="size-4 animate-spin" />
                    分析中...
                  </span>
                ) : (
                  <SelectValue placeholder="重新分析" />
                )}
              </SelectTrigger>
              <SelectContent>
                {templates.map((template) => (
                  <SelectItem key={template.id} value={template.id}>
                    {template.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        </div>
        <AnalysisContent section={activeSection || null} />
      </div>
    </div>
  );
};

export default DocumentAnalysisPage;
