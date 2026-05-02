import api from '@/utils/api';
import React, { useCallback, useEffect, useState } from 'react';
import { useParams, useSearchParams } from 'react-router-dom';
import { AnalysisContent } from './components/analysis-content';
import { SectionNav } from './components/section-nav';

interface Section {
  section_id: string;
  section_name: string;
  page_range: number[];
  analysis: Record<string, { label: string; content: string }>;
}

export const DocumentAnalysisPage: React.FC = () => {
  const { documentId } = useParams<{ documentId: string }>();
  const [searchParams] = useSearchParams();
  const taskId = searchParams.get('task_id');

  const [status, setStatus] = useState<string>('pending');
  const [progress, setProgress] = useState<number>(0);
  const [sections, setSections] = useState<Section[]>([]);
  const [activeSectionId, setActiveSectionId] = useState<string>('');
  const [errorMessage, setErrorMessage] = useState<string>('');

  const fetchResult = useCallback(async () => {
    if (!documentId) return;
    try {
      const params = taskId ? `?task_id=${taskId}` : '';
      const response = await api.get(
        `/documents/${documentId}/analysis${params}`,
      );
      const data = response.data.data;

      setStatus(data.status);
      setProgress(data.progress);
      setSections(data.sections || []);
      setErrorMessage(data.error_message || '');

      if (data.sections?.length) {
        setActiveSectionId((prev) => prev || data.sections[0].section_id);
      }
    } catch (error) {
      console.error('Failed to fetch analysis result:', error);
    }
  }, [documentId, taskId]);

  useEffect(() => {
    fetchResult();

    // 如果正在分析，轮询更新
    if (status === 'pending' || status === 'running') {
      const interval = setInterval(fetchResult, 2000);
      return () => clearInterval(interval);
    }
  }, [fetchResult, status]);

  const triggerAnalysis = async () => {
    if (!documentId) return;
    try {
      await api.post(`/documents/${documentId}/analyze`, {});
      setStatus('pending');
      setProgress(0);
    } catch (error) {
      console.error('Failed to trigger analysis:', error);
    }
  };

  const activeSection = sections.find((s) => s.section_id === activeSectionId);

  if (status === 'failed') {
    return (
      <div className="flex flex-col items-center justify-center h-full">
        <p className="text-red-500 mb-4">分析失败: {errorMessage}</p>
        <button
          className="px-4 py-2 bg-blue-500 text-white rounded hover:bg-blue-600"
          onClick={triggerAnalysis}
        >
          重试
        </button>
      </div>
    );
  }

  if (status === 'pending' || status === 'running') {
    return (
      <div className="flex flex-col items-center justify-center h-full">
        <div className="w-64 mb-4 bg-gray-200 rounded-full h-2">
          <div
            className="bg-blue-500 h-2 rounded-full"
            style={{ width: `${progress}%` }}
          />
        </div>
        <p className="text-gray-500">正在分析文档... {progress}%</p>
      </div>
    );
  }

  return (
    <div className="flex h-[calc(100vh-64px)]">
      <SectionNav
        sections={sections}
        activeId={activeSectionId}
        onSelect={setActiveSectionId}
      />
      <AnalysisContent section={activeSection || null} />
    </div>
  );
};

export default DocumentAnalysisPage;
