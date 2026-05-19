import { useCallback, useEffect, useState } from 'react';
import DocumentEditor from './document-editor';
import DocumentList from './document-list';

interface DocumentItem {
  id: string;
  name: string;
  file_type: string;
  agent_id: string;
  create_time: string;
  update_time: string;
}

interface DocumentData {
  id: string;
  name: string;
  file_type: string;
  file_path: string;
  content: Record<string, unknown>;
  markdown_content: string;
  agent_id: string;
  create_time: string;
  update_time: string;
}

interface Props {
  apiFetch: (url: string, options?: RequestInit) => Promise<Response>;
}

export default function CollaborationPanel({ apiFetch }: Props) {
  const [documents, setDocuments] = useState<DocumentItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [selectedDoc, setSelectedDoc] = useState<DocumentData | null>(null);
  const [docLoading, setDocLoading] = useState(false);

  const loadDocuments = useCallback(async () => {
    setLoading(true);
    try {
      const resp = await apiFetch('/api/v1/collaboration/documents');
      const result = await resp.json();
      if (result.code === 0) {
        setDocuments(result.data || []);
      }
    } catch (e) {
      console.error('加载文档列表失败:', e);
    } finally {
      setLoading(false);
    }
  }, [apiFetch]);

  useEffect(() => {
    loadDocuments();
  }, [loadDocuments]);

  const handleSelect = useCallback(
    async (doc: DocumentItem | null) => {
      if (!doc) {
        setSelectedId(null);
        setSelectedDoc(null);
        return;
      }
      setSelectedId(doc.id);
      setDocLoading(true);
      try {
        const resp = await apiFetch(
          `/api/v1/collaboration/documents/${doc.id}`,
        );
        const result = await resp.json();
        if (result.code === 0) {
          setSelectedDoc(result.data);
        }
      } catch (e) {
        console.error('加载文档详情失败:', e);
      } finally {
        setDocLoading(false);
      }
    },
    [apiFetch],
  );

  const handleDocUpdate = useCallback(() => {
    loadDocuments();
  }, [loadDocuments]);

  return (
    <div className="flex-1 flex min-h-0">
      <DocumentList
        selectedId={selectedId}
        onSelect={handleSelect}
        documents={documents}
        loading={loading}
        apiFetch={apiFetch}
        onRefresh={loadDocuments}
      />
      <div className="flex-1 flex min-w-0">
        {docLoading ? (
          <div className="flex-1 flex items-center justify-center">
            <div className="w-6 h-6 border-2 border-indigo-300 border-t-indigo-500 rounded-full animate-spin" />
          </div>
        ) : selectedDoc ? (
          <DocumentEditor
            document={selectedDoc}
            apiFetch={apiFetch}
            onUpdate={handleDocUpdate}
          />
        ) : (
          <div className="flex-1 flex items-center justify-center bg-stone-50">
            <div className="text-center text-stone-400">
              <svg
                className="w-12 h-12 mx-auto mb-3 text-stone-300"
                fill="none"
                stroke="currentColor"
                viewBox="0 0 24 24"
              >
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  strokeWidth={1.5}
                  d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z"
                />
              </svg>
              <p className="text-sm">请从左侧选择一个文档</p>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
