import { CendTooltip } from '@/components/ui/tooltip';
import { getAuthorization } from '@/utils/authorization-util';
import { ChevronLeft, ChevronRight } from 'lucide-react';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import DocumentEditor from './document-editor';
import DocumentList from './document-list';
import type { DocumentNode, FolderNode } from './folder-tree';
import ShareDialog from './share-dialog';
import SidePanelBar, { PanelKey } from './side-panel-bar';
import SpreadsheetEditor from './spreadsheet-editor';

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

interface FormatRule {
  id: string;
  name: string;
  description: string;
  config: Record<string, unknown>;
}

interface Props {
  apiFetch: (url: string, options?: RequestInit) => Promise<Response>;
  refreshToken?: number;
}

export default function CollaborationPanel({ apiFetch, refreshToken }: Props) {
  const [documents, setDocuments] = useState<DocumentNode[]>([]);
  const [folders, setFolders] = useState<FolderNode[]>([]);
  const [loading, setLoading] = useState(false);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [selectedDoc, setSelectedDoc] = useState<DocumentData | null>(null);
  const [docLoading, setDocLoading] = useState(false);
  const [applyingRuleId, setApplyingRuleId] = useState<string | null>(null);
  const appliedRuleConfigRef = useRef<Record<string, unknown> | null>(null);
  const [collapsed, setCollapsed] = useState(false);
  const [shareTarget, setShareTarget] = useState<DocumentNode | null>(null);
  const [activePanel, setActivePanel] = useState<PanelKey | null>('comments');

  const currentUserId = useMemo(() => {
    try {
      const userInfo = JSON.parse(localStorage.getItem('userInfo') || 'null');
      return userInfo?.id || userInfo?.user_id || null;
    } catch {
      return null;
    }
  }, []);

  const isOwner = useMemo(() => {
    if (!selectedId) return false;
    const doc = documents.find((d) => d.id === selectedId);
    return !!doc?.created_by && doc.created_by === currentUserId;
  }, [documents, selectedId, currentUserId]);

  // Extract raw JWT token (strip "Bearer " prefix) for WebSocket auth
  const wsToken = useMemo(() => {
    const auth = getAuthorization();
    if (!auth) return undefined;
    return auth.startsWith('Bearer ') ? auth.slice(7) : auth;
  }, []);

  const apiFetchRef = useRef(apiFetch);
  apiFetchRef.current = apiFetch;

  const loadDocuments = useCallback(async () => {
    setLoading(true);
    try {
      const resp = await apiFetchRef.current('/api/v1/collaboration/documents');
      const result = await resp.json();
      if (result.code === 0) {
        setDocuments(result.data || []);
      }
    } catch (e) {
      console.error('加载文档列表失败:', e);
    } finally {
      setLoading(false);
    }
  }, []);

  const loadFolders = useCallback(async () => {
    try {
      const resp = await apiFetchRef.current('/api/v1/collaboration/folders');
      const result = await resp.json();
      if (result.code === 0) {
        setFolders(result.data || []);
      }
    } catch (e) {
      console.error('加载文件夹列表失败:', e);
    }
  }, []);

  useEffect(() => {
    loadDocuments();
    loadFolders();
  }, [loadDocuments, loadFolders]);

  // Refresh when parent signals (e.g. after creating a doc from chat)
  useEffect(() => {
    if (refreshToken !== undefined && refreshToken > 0) {
      loadDocuments();
      loadFolders();
    }
  }, [refreshToken]);

  const handleCreateFolder = useCallback(
    async (name: string, parentId: string | null) => {
      try {
        const resp = await apiFetchRef.current(
          '/api/v1/collaboration/folders',
          {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name, parent_id: parentId }),
          },
        );
        const result = await resp.json();
        if (result.code === 0) {
          loadFolders();
        }
      } catch (e) {
        console.error('创建文件夹失败:', e);
      }
    },
    [loadFolders],
  );

  const handleDeleteFolder = useCallback(
    async (folderId: string) => {
      try {
        const resp = await apiFetchRef.current(
          `/api/v1/collaboration/folders/${folderId}`,
          { method: 'DELETE' },
        );
        const result = await resp.json();
        if (result.code === 0) {
          loadFolders();
          loadDocuments();
        }
      } catch (e) {
        console.error('删除文件夹失败:', e);
      }
    },
    [loadFolders, loadDocuments],
  );

  const handleSelect = useCallback(async (doc: DocumentNode | null) => {
    if (!doc) {
      setSelectedId(null);
      setSelectedDoc(null);
      return;
    }
    setSelectedId(doc.id);
    setDocLoading(true);
    try {
      const resp = await apiFetchRef.current(
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
  }, []);

  const handleDocUpdate = useCallback(() => {
    loadDocuments();
  }, [loadDocuments]);

  const handleApplyFormatRule = useCallback((rule: FormatRule) => {
    setApplyingRuleId(rule.id);
    appliedRuleConfigRef.current = rule.config;
    // Force re-render of DocumentEditor with a new key to trigger the plugin
    setSelectedDoc((prev) => (prev ? { ...prev } : prev));
  }, []);

  const handleRuleApplied = useCallback(() => {
    setApplyingRuleId(null);
    appliedRuleConfigRef.current = null;
  }, []);

  return (
    <div className="flex-1 flex min-h-0 bg-white">
      <DocumentList
        selectedId={selectedId}
        onSelect={handleSelect}
        documents={documents}
        folders={folders}
        loading={loading}
        apiFetch={apiFetch}
        onRefresh={() => {
          loadDocuments();
          loadFolders();
        }}
        onCreateFolder={handleCreateFolder}
        onDeleteFolder={handleDeleteFolder}
        onShare={setShareTarget}
        collapsed={collapsed}
      />
      <CendTooltip title={collapsed ? '展开侧边栏' : '收起侧边栏'}>
        <button
          onClick={() => setCollapsed((c) => !c)}
          className="shrink-0 self-start mt-6 -ml-3.5 z-10 size-7 flex items-center justify-center rounded-full border-2 border-[#D4D4D4] bg-white text-[#525252] hover:text-[#000000] hover:border-[#A3A3A3] hover:shadow-[0_2px_8px_rgba(0,0,0,0.12)] transition-all cursor-pointer"
        >
          {collapsed ? (
            <ChevronRight className="size-3.5" />
          ) : (
            <ChevronLeft className="size-3.5" />
          )}
        </button>
      </CendTooltip>
      <div className="flex-1 flex min-w-0">
        {docLoading ? (
          <div className="flex-1 flex items-center justify-center">
            <div className="flex flex-col items-center gap-3">
              <div className="w-6 h-6 border-2 border-[#D4D4D4] border-t-[#000000] rounded-full animate-spin" />
              <span className="text-xs text-[#A3A3A3]">
                文档内容过大，耐心等待加载
              </span>
            </div>
          </div>
        ) : selectedDoc ? (
          selectedDoc.file_type === 'xlsx' ? (
            <SpreadsheetEditor
              key={selectedDoc.id}
              document={selectedDoc}
              apiFetch={apiFetch}
              onUpdate={handleDocUpdate}
              token={wsToken}
              onOpenShare={() => {
                const node = documents.find((d) => d.id === selectedDoc.id);
                if (node) setShareTarget(node);
              }}
            />
          ) : (
            <DocumentEditor
              key={selectedDoc.id}
              document={selectedDoc}
              apiFetch={apiFetch}
              onUpdate={handleDocUpdate}
              appliedRuleConfig={appliedRuleConfigRef.current}
              onRuleApplied={handleRuleApplied}
              token={wsToken}
              onOpenShare={() => {
                const node = documents.find((d) => d.id === selectedDoc.id);
                if (node) setShareTarget(node);
              }}
            />
          )
        ) : (
          <div className="flex-1 flex items-center justify-center">
            <div className="text-center text-black/30">
              <svg
                className="w-12 h-12 mx-auto mb-3 text-black/20"
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
      {selectedDoc && (
        <SidePanelBar
          docId={selectedDoc.id}
          apiFetch={apiFetch}
          activePanel={activePanel}
          onChange={setActivePanel}
          isOwner={isOwner}
          onApplyFormatRule={handleApplyFormatRule}
          applyingRuleId={applyingRuleId}
        />
      )}
      {shareTarget && (
        <ShareDialog
          open={!!shareTarget}
          docId={shareTarget.id}
          docName={shareTarget.name}
          apiFetch={apiFetch}
          onClose={() => setShareTarget(null)}
        />
      )}
    </div>
  );
}
