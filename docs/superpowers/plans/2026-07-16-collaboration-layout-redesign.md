# 协作页面布局重设计 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 C 端协作页签重构为「左栏列表 + 极简头部编辑器 + 右侧图标栏互斥面板」的现代协作布局。

**Architecture:** 方案 B 抽壳重构 —— 新建 `side-panel-bar.tsx`（图标栏+面板注册表）、`editor-header.tsx`（极简头部）、`version-history-panel.tsx`（版本面板）；`document-editor.tsx` 瘦身为纯编辑器；分享双弹窗合并；工具栏插入类按钮收进"插入"下拉。所有面板内容组件复用，不改后端 API、不动 Yjs 协议。

**Tech Stack:** React 18 + TypeScript + Tailwind CSS + Lexical + lucide-react。所有新组件放 `web/src/components/collaboration/`，禁止修改 `src/components/ui/`。

**设计文档:** `docs/superpowers/specs/2026-07-16-collaboration-layout-redesign-design.md`

**对 spec 的一处修正:** 后端 `GET /collaboration/documents/<id>/versions` 只返回 `{current_version, has_ydoc, update_time}`（无版本列表数组），且本次不改后端 → 版本历史面板展示「当前版本 + 最近保存时间 + 恢复按钮」，不做列表。

**验证约定（每个任务通用）:**
- 类型检查: `cd web && npx tsc --noEmit`（若基线本身有报错，只需确认**无新增**错误）
- 手动验证: 开发服务器固定端口 `http://localhost:9222`，登录账号 `lg18629285296@163.com / 12345678`，进入 C 端 `/home` → 协作页签
- **禁止**执行 `npm run build`（本地开发不构建）、禁止部署服务器、禁止重启 Docker

---

### Task 1: 新建版本历史面板 `version-history-panel.tsx`

**Files:**
- Create: `web/src/components/collaboration/version-history-panel.tsx`

- [ ] **Step 1: 创建文件，写入以下完整内容**

```tsx
import { History, RotateCcw, X } from 'lucide-react';
import { useCallback, useEffect, useState } from 'react';

interface VersionInfo {
  current_version: number;
  has_ydoc: boolean;
  update_time: number | string | null;
}

interface Props {
  docId: string;
  apiFetch: (url: string, options?: RequestInit) => Promise<Response>;
  open: boolean;
  onToggle: () => void;
}

function formatTime(t: number | string | null): string {
  if (!t) return '—';
  let ms = Number(t);
  if (!Number.isFinite(ms)) return '—';
  if (ms < 1e12) ms *= 1000; // 秒级时间戳兼容
  const d = new Date(ms);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(
    d.getDate(),
  ).padStart(2, '0')} ${String(d.getHours()).padStart(2, '0')}:${String(
    d.getMinutes(),
  ).padStart(2, '0')}`;
}

export default function VersionHistoryPanel({
  docId,
  apiFetch,
  open,
  onToggle,
}: Props) {
  const [info, setInfo] = useState<VersionInfo | null>(null);
  const [loading, setLoading] = useState(false);
  const [restoring, setRestoring] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const resp = await apiFetch(
        `/api/v1/collaboration/documents/${docId}/versions`,
      );
      const result = await resp.json();
      if (result.code === 0 && result.data) {
        setInfo(result.data);
      }
    } catch (e) {
      console.error('Failed to load versions:', e);
    } finally {
      setLoading(false);
    }
  }, [apiFetch, docId]);

  useEffect(() => {
    if (open) load();
  }, [docId, open, load]);

  const handleRestore = async () => {
    if (restoring || !info) return;
    if (!window.confirm('确认恢复到最近保存的版本？当前未保存的更改将丢失。'))
      return;
    setRestoring(true);
    try {
      const resp = await apiFetch(
        `/api/v1/collaboration/documents/${docId}/versions/${info.current_version || 0}/restore`,
        { method: 'POST' },
      );
      const result = await resp.json();
      if (result.code === 0) {
        window.location.reload();
      } else {
        alert(result.message || '恢复失败');
      }
    } catch (e) {
      console.error('Restore failed:', e);
    } finally {
      setRestoring(false);
    }
  };

  if (!open) return null;

  return (
    <div className="w-full h-full flex flex-col bg-white">
      {/* Header */}
      <div className="flex items-center justify-between px-3 py-2.5 border-b border-stone-100">
        <div className="flex items-center gap-1.5 text-stone-700">
          <History className="size-3.5" />
          <span className="text-xs font-semibold">版本历史</span>
        </div>
        <button
          className="size-6 flex items-center justify-center rounded text-stone-400 hover:text-stone-700 hover:bg-stone-100"
          onClick={onToggle}
        >
          <X className="size-3.5" />
        </button>
      </div>
      {/* Body */}
      <div className="flex-1 overflow-y-auto p-3">
        {loading ? (
          <div className="text-xs text-stone-400 text-center py-6">
            加载中...
          </div>
        ) : info ? (
          <div className="space-y-3">
            <div className="border border-stone-200 rounded-lg p-3">
              <div className="flex items-center justify-between">
                <span className="text-xs font-medium text-stone-800">
                  当前版本
                </span>
                <span className="text-xs text-stone-500 tabular-nums">
                  v{info.current_version || 0}
                </span>
              </div>
              <div className="text-[10px] text-stone-400 mt-1">
                最近保存: {formatTime(info.update_time)}
              </div>
            </div>
            {info.has_ydoc ? (
              <button
                className="w-full flex items-center justify-center gap-1.5 px-3 py-2 text-xs font-medium text-stone-700 border border-stone-200 rounded-lg hover:bg-stone-50 disabled:opacity-50 transition-colors"
                onClick={handleRestore}
                disabled={restoring}
              >
                <RotateCcw className="size-3.5" />
                {restoring ? '恢复中...' : '恢复到最近保存状态'}
              </button>
            ) : (
              <p className="text-[10px] text-stone-400 text-center">
                暂无可恢复的保存状态
              </p>
            )}
            <p className="text-[10px] text-stone-400 leading-relaxed">
              文档每次保存会递增版本号。恢复操作将丢弃当前未保存的本地更改，并重新加载页面。
            </p>
          </div>
        ) : (
          <div className="text-xs text-stone-400 text-center py-6">
            无版本信息
          </div>
        )}
      </div>
    </div>
  );
}
```

- [ ] **Step 2: 类型检查**

Run: `cd web && npx tsc --noEmit`
Expected: 无新增错误

- [ ] **Step 3: Commit**

```bash
git add web/src/components/collaboration/version-history-panel.tsx
git commit -m "feat(collab): 新增版本历史面板组件"
```

---

### Task 2: 新建右侧图标栏 `side-panel-bar.tsx`

**Files:**
- Create: `web/src/components/collaboration/side-panel-bar.tsx`

依赖 Task 1（引用 VersionHistoryPanel）。此时尚未接线，编译通过即可。

- [ ] **Step 1: 创建文件，写入以下完整内容**

```tsx
import { CendTooltip } from '@/components/ui/tooltip';
import {
  History,
  MessageSquare,
  Paperclip,
  ScrollText,
  Sparkles,
} from 'lucide-react';
import type { ComponentType } from 'react';
import AttachmentPanel from './attachment-panel';
import AuditLogPanel from './audit-log-panel';
import CommentPanel from './comment-panel';
import FormatRulePanel from './format-rule-panel';
import VersionHistoryPanel from './version-history-panel';

export type PanelKey =
  | 'comments'
  | 'attachments'
  | 'versions'
  | 'formatRules'
  | 'audit';

interface FormatRule {
  id: string;
  name: string;
  description: string;
  config: Record<string, unknown>;
}

interface Props {
  docId: string;
  apiFetch: (url: string, options?: RequestInit) => Promise<Response>;
  activePanel: PanelKey | null;
  onChange: (key: PanelKey | null) => void;
  isOwner: boolean;
  onApplyFormatRule?: (rule: FormatRule) => void;
  applyingRuleId?: string | null;
}

const PANEL_ICONS: {
  key: PanelKey;
  title: string;
  icon: ComponentType<{ className?: string }>;
  ownerOnly?: boolean;
}[] = [
  { key: 'comments', title: '评论', icon: MessageSquare },
  { key: 'attachments', title: '附件', icon: Paperclip },
  { key: 'versions', title: '版本历史', icon: History },
  { key: 'formatRules', title: '格式规则', icon: Sparkles },
  { key: 'audit', title: '审计日志', icon: ScrollText, ownerOnly: true },
];

export default function SidePanelBar({
  docId,
  apiFetch,
  activePanel,
  onChange,
  isOwner,
  onApplyFormatRule,
  applyingRuleId,
}: Props) {
  const close = () => onChange(null);

  return (
    <div className="flex shrink-0 min-h-0 h-full">
      {/* 互斥面板容器 */}
      {activePanel && (
        <div className="w-72 shrink-0 border-l border-stone-200 bg-white h-full min-h-0 flex flex-col overflow-hidden">
          {activePanel === 'comments' && (
            <CommentPanel
              docId={docId}
              apiFetch={apiFetch}
              open
              onToggle={close}
            />
          )}
          {activePanel === 'attachments' && (
            <AttachmentPanel
              docId={docId}
              apiFetch={apiFetch}
              open
              onToggle={close}
            />
          )}
          {activePanel === 'versions' && (
            <VersionHistoryPanel
              docId={docId}
              apiFetch={apiFetch}
              open
              onToggle={close}
            />
          )}
          {activePanel === 'formatRules' && (
            <FormatRulePanel
              apiFetch={apiFetch}
              selectedDocId={docId}
              onApplyRule={onApplyFormatRule}
              applyingRuleId={applyingRuleId}
              onClose={close}
            />
          )}
          {activePanel === 'audit' && isOwner && (
            <AuditLogPanel
              docId={docId}
              apiFetch={apiFetch}
              open
              onToggle={close}
            />
          )}
        </div>
      )}
      {/* 常驻图标栏 */}
      <div className="w-10 shrink-0 border-l border-stone-200 bg-white flex flex-col items-center gap-1 pt-3">
        {PANEL_ICONS.filter((p) => !p.ownerOnly || isOwner).map((p) => {
          const Icon = p.icon;
          const active = activePanel === p.key;
          return (
            <CendTooltip key={p.key} title={p.title}>
              <button
                className={`size-7 flex items-center justify-center rounded-lg transition-colors ${
                  active
                    ? 'bg-stone-900 text-white'
                    : 'text-stone-400 hover:text-stone-700 hover:bg-stone-100'
                }`}
                onClick={() => onChange(active ? null : p.key)}
              >
                <Icon className="size-4" />
              </button>
            </CendTooltip>
          );
        })}
      </div>
    </div>
  );
}
```

注意：`FormatRulePanel` 的 `onClose` prop 在 Task 3 中添加，本 Task 结束时 tsc 会报该 prop 不存在 —— **Task 2 与 Task 3 需连续完成后一起验证**（commit 分开，tsc 在 Task 3 末尾执行）。

- [ ] **Step 2: Commit**

```bash
git add web/src/components/collaboration/side-panel-bar.tsx
git commit -m "feat(collab): 新增右侧图标栏与互斥面板容器"
```

---

### Task 3: 统一面板外壳（评论/附件/审计/格式规则）

**Files:**
- Modify: `web/src/components/collaboration/comment-panel.tsx:308`
- Modify: `web/src/components/collaboration/attachment-panel.tsx:122`
- Modify: `web/src/components/collaboration/audit-log-panel.tsx:101`
- Modify: `web/src/components/collaboration/format-rule-panel.tsx:471-508` 附近外壳

- [ ] **Step 1: 三个面板外层宽度类改为自适应**

三个文件中找到完全相同的外层 div（各文件一处）：

```tsx
<div className="w-72 shrink-0 border-l border-stone-200 flex flex-col bg-white h-full">
```

统一替换为（宽度与边框由 SidePanelBar 容器提供）：

```tsx
<div className="w-full flex flex-col bg-white h-full">
```

- [ ] **Step 2: format-rule-panel 增加 `onClose` prop 并替换折叠外壳**

在 `format-rule-panel.tsx` 的 Props interface 中新增：

```tsx
  onClose?: () => void;
```

并在组件函数参数解构中加入 `onClose`。

找到 line 471 起的返回 JSX 外壳（`<> {/* Collapsible Rules Panel */} <div className="border-t border-[#E8E8E6]">` 及其内部的折叠 toggle `<button onClick={() => setCollapsed(!collapsed)}>...格式规则...</button>` 和 `{!collapsed && (` 包裹层），替换为固定展开的面板壳：

```tsx
  return (
    <>
      <div className="w-full h-full flex flex-col bg-white">
        {/* Header */}
        <div className="flex items-center justify-between px-3 py-2.5 border-b border-stone-100">
          <div className="flex items-center gap-1.5 text-stone-700">
            <svg
              className="w-3.5 h-3.5"
              fill="none"
              stroke="currentColor"
              viewBox="0 0 24 24"
            >
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                strokeWidth={2}
                d="M4 6h16M4 12h16M4 18h7"
              />
            </svg>
            <span className="text-xs font-semibold">格式规则</span>
          </div>
          <div className="flex items-center gap-1">
            <button
              className="text-[10px] text-[#000000] hover:text-[#000000] font-medium px-1"
              onClick={openCreate}
            >
              + 新建
            </button>
            {onClose && (
              <button
                className="size-6 flex items-center justify-center rounded text-stone-400 hover:text-stone-700 hover:bg-stone-100"
                onClick={onClose}
              >
                <svg
                  className="w-3.5 h-3.5"
                  fill="none"
                  stroke="currentColor"
                  viewBox="0 0 24 24"
                >
                  <path
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    strokeWidth={2}
                    d="M6 18L18 6M6 6l12 12"
                  />
                </svg>
              </button>
            )}
          </div>
        </div>
        {/* Body */}
        <div className="flex-1 overflow-y-auto px-2 py-2">
          <div className="flex items-center justify-between px-1 mb-1">
            <span className="text-[10px] text-[#333333]">
              {rules.length === 0 ? '暂无规则' : `${rules.length} 条规则`}
            </span>
          </div>
          {/* ↓ 以下保留原有内容：loading 分支 + rules.map 列表，原样不动 ↓ */}
```

处理要点：
1. 原折叠按钮及 `collapsed` state、`setCollapsed` 全部删除（连同 `useState` 声明），`{!collapsed && (...)}` 拆包只留内部内容
2. 原「+ 新建」按钮从内容区移到新 Header（如上），内容区原位置的该按钮删除
3. `loading ? ... : rules.map(...)` 列表 JSX 原样保留在 Body 内
4. 文件尾部原 `</div></>` 闭合按新结构调整；文件里若有规则编辑弹窗（Modal）等兄弟节点保持在 `<>...</>` 内不动

- [ ] **Step 3: 类型检查**

Run: `cd web && npx tsc --noEmit`
Expected: 无新增错误（Task 2 的 onClose 报错此时消除）

- [ ] **Step 4: Commit**

```bash
git add web/src/components/collaboration/comment-panel.tsx web/src/components/collaboration/attachment-panel.tsx web/src/components/collaboration/audit-log-panel.tsx web/src/components/collaboration/format-rule-panel.tsx
git commit -m "refactor(collab): 面板外壳统一为自适应宽度, 格式规则改为面板样式"
```

注意：此 commit 后 document-list 底部的格式规则区渲染为面板样式（临时视觉不协调），Task 4 立即移除，可接受。

---

### Task 4: index.tsx 接入 SidePanelBar + 编辑器/列表移除旧面板

**Files:**
- Modify: `web/src/components/collaboration/index.tsx`
- Modify: `web/src/components/collaboration/document-editor.tsx`
- Modify: `web/src/components/collaboration/document-list.tsx`

- [ ] **Step 1: index.tsx 增加 activePanel 状态与 isOwner 计算**

新增 import：

```tsx
import SidePanelBar, { PanelKey } from './side-panel-bar';
```

在组件内新增（与现有 useState 并列）：

```tsx
  const [activePanel, setActivePanel] = useState<PanelKey | null>(null);

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
```

- [ ] **Step 2: index.tsx 布局挂载 SidePanelBar**

在根容器 `<div className="flex-1 flex min-h-0 bg-white">` 内、`<div className="flex-1 flex min-w-0">...编辑器...</div>` 之后、`{shareTarget && <ShareDialog .../>}` 之前插入：

```tsx
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
```

- [ ] **Step 3: index.tsx 移除 DocumentList 的格式规则 props**

`<DocumentList>` 调用处删除 `onApplyFormatRule={handleApplyFormatRule}` 和 `applyingRuleId={applyingRuleId}` 两行（`handleApplyFormatRule`/`applyingRuleId` 本身保留，已被 SidePanelBar 使用）。

- [ ] **Step 4: document-list.tsx 移除格式规则**

1. 删除 import：`import FormatRulePanel from './format-rule-panel';`
2. 删除 Props 中的 `onApplyFormatRule?: (rule: FormatRule) => void;` 与 `applyingRuleId?: string | null;` 及函数参数解构中的对应两项
3. 删除 `FormatRule` interface（若仅此处使用）与 `handleApplyRule` 回调
4. 删除 JSX 中的：

```tsx
      {/* Format Rules Section */}
      <FormatRulePanel
        apiFetch={apiFetch}
        selectedDocId={selectedId}
        onApplyRule={handleApplyRule}
        applyingRuleId={applyingRuleId}
      />
```

- [ ] **Step 5: document-editor.tsx 移除旧面板挂载与头部面板按钮**

1. 删除 import：`AttachmentPanel`、`AuditLogPanel`、`CommentPanel`（`MemberAvatars`、`ShareLinkDialog` 保留，Task 5/6 处理）
2. 删除 state：`showComments`、`showAttachments`、`showAuditLog` 三个 useState
3. 删除 `handleRestore` 函数与 `restoring` state（恢复已移入版本面板）
4. 头部 JSX（line ~708-798）中删除以下按钮块及其相邻分隔线 `<div className="w-px h-4 bg-[#E8E8E6]" />`：
   - `评论` 按钮块、`附件` 按钮块、`审计日志` 按钮块
   - 版本区的 `恢复` 按钮（`v{version}` 小字保留）
5. 编辑区尾部（line ~863-880）删除：

```tsx
        <CommentPanel ... />
        <AttachmentPanel ... />
        <AuditLogPanel ... />
```

（`<ShareLinkDialog ... />` 本任务保留）

- [ ] **Step 6: 类型检查**

Run: `cd web && npx tsc --noEmit`
Expected: 无新增错误

- [ ] **Step 7: 手动验证（localhost:9222 协作页签）**

- 选中文档后右侧出现 40px 图标栏（评论/附件/版本/格式规则，owner 文档多一个审计图标）
- 点击图标展开对应面板，再点关闭；切换图标时面板互斥
- 格式规则面板中点"应用"，编辑器段落样式生效（FormatApplyPlugin 链路）
- 版本面板显示 v 号与保存时间，恢复按钮弹确认
- 未选中文档时无图标栏
- 左栏底部不再有格式规则区；编辑器头部不再有 评论/附件/审计日志/恢复 按钮

- [ ] **Step 8: Commit**

```bash
git add web/src/components/collaboration/index.tsx web/src/components/collaboration/document-editor.tsx web/src/components/collaboration/document-list.tsx
git commit -m "feat(collab): 接入右侧图标栏互斥面板, 移除编辑器内旧面板挂载"
```

---

### Task 5: 新建极简头部 `editor-header.tsx` 并替换编辑器头部

**Files:**
- Create: `web/src/components/collaboration/editor-header.tsx`
- Modify: `web/src/components/collaboration/document-editor.tsx`
- Modify: `web/src/components/collaboration/index.tsx`

- [ ] **Step 1: 创建 `editor-header.tsx`，写入以下完整内容**

```tsx
import {
  Check,
  Download,
  Loader2,
  MoreHorizontal,
  Save,
  Share2,
} from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import MemberAvatars from './member-avatars';
import type { CollaborationWebSocketProvider } from './yjs-provider';

type SaveStatus = 'idle' | 'saving' | 'saved' | 'error';

interface Props {
  docId: string;
  docName: string;
  saveStatus: SaveStatus;
  version: number | null;
  provider: CollaborationWebSocketProvider | null;
  /** 非协同模式(无 token)时展示手动保存入口 */
  showManualSave: boolean;
  onManualSave: () => void;
  onDownload: (type: 'docx' | 'pdf') => void;
  downloading: boolean;
  onOpenShare: () => void;
  apiFetch: (url: string, options?: RequestInit) => Promise<Response>;
  onRenamed: () => void;
}

const STATUS_META: Record<SaveStatus, { label: string; cls: string }> = {
  idle: { label: '', cls: 'text-stone-400' },
  saving: { label: '保存中…', cls: 'text-amber-600' },
  saved: { label: '已保存', cls: 'text-emerald-600' },
  error: { label: '保存失败', cls: 'text-red-600' },
};

export default function EditorHeader({
  docId,
  docName,
  saveStatus,
  version,
  provider,
  showManualSave,
  onManualSave,
  onDownload,
  downloading,
  onOpenShare,
  apiFetch,
  onRenamed,
}: Props) {
  const [showMore, setShowMore] = useState(false);
  const [renaming, setRenaming] = useState(false);
  const [nameValue, setNameValue] = useState(docName);
  const [displayName, setDisplayName] = useState(docName);
  const moreRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    setDisplayName(docName);
    setNameValue(docName);
  }, [docName]);

  // 点击外部关闭更多菜单
  useEffect(() => {
    if (!showMore) return;
    const handler = (e: MouseEvent) => {
      if (moreRef.current && !moreRef.current.contains(e.target as Node)) {
        setShowMore(false);
      }
    };
    window.document.addEventListener('mousedown', handler);
    return () => window.document.removeEventListener('mousedown', handler);
  }, [showMore]);

  const submitRename = async () => {
    const name = nameValue.trim();
    setRenaming(false);
    if (!name || name === displayName) return;
    try {
      await apiFetch(`/api/v1/collaboration/documents/${docId}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name }),
      });
      setDisplayName(name);
      onRenamed();
    } catch (e) {
      console.error('重命名失败:', e);
    }
  };

  const status = STATUS_META[saveStatus];

  return (
    <div className="flex items-center justify-between px-4 py-2.5 border-b border-stone-100 gap-3">
      {/* 左侧：文档名 + 保存状态 */}
      <div className="flex items-center gap-2 min-w-0 flex-1">
        {renaming ? (
          <input
            className="text-sm font-semibold text-stone-900 border border-stone-300 rounded px-1.5 py-0.5 outline-none focus:border-stone-500 max-w-xs"
            value={nameValue}
            autoFocus
            onChange={(e) => setNameValue(e.target.value)}
            onBlur={submitRename}
            onKeyDown={(e) => {
              if (e.key === 'Enter') submitRename();
              if (e.key === 'Escape') {
                setNameValue(displayName);
                setRenaming(false);
              }
            }}
          />
        ) : (
          <h2
            className="text-sm font-semibold text-stone-900 truncate cursor-text hover:bg-stone-50 rounded px-1 -mx-1"
            title="点击重命名"
            onClick={() => setRenaming(true)}
          >
            {displayName}
          </h2>
        )}
        <span
          className={`text-[10px] whitespace-nowrap flex items-center gap-0.5 ${status.cls}`}
        >
          {saveStatus === 'saving' && (
            <Loader2 className="size-2.5 animate-spin" />
          )}
          {saveStatus === 'saved' && <Check className="size-2.5" />}
          {status.label}
          {version !== null && (
            <span className="text-stone-300 ml-1">· v{version}</span>
          )}
        </span>
      </div>

      {/* 右侧：成员头像 + 分享 + 更多 */}
      <div className="flex items-center gap-2 shrink-0">
        {provider && <MemberAvatars provider={provider} />}
        <button
          className="flex items-center gap-1 px-3 py-1.5 text-xs font-medium text-white bg-stone-900 hover:bg-stone-700 rounded-lg transition-colors"
          onClick={onOpenShare}
        >
          <Share2 className="size-3.5" />
          分享
        </button>
        <div className="relative" ref={moreRef}>
          <button
            className="size-7 flex items-center justify-center rounded-lg text-stone-500 hover:text-stone-900 hover:bg-stone-100 transition-colors"
            onClick={() => setShowMore((v) => !v)}
          >
            <MoreHorizontal className="size-4" />
          </button>
          {showMore && (
            <div className="absolute top-full right-0 mt-1 w-40 bg-white border border-stone-200 rounded-lg shadow-lg py-1 z-50">
              {showManualSave && (
                <button
                  className="w-full px-3 py-1.5 text-left text-xs text-stone-700 hover:bg-stone-50 flex items-center gap-2"
                  onClick={() => {
                    setShowMore(false);
                    onManualSave();
                  }}
                >
                  <Save className="size-3.5" />
                  手动保存
                </button>
              )}
              <button
                className="w-full px-3 py-1.5 text-left text-xs text-stone-700 hover:bg-stone-50 flex items-center gap-2 disabled:opacity-50"
                disabled={downloading}
                onClick={() => {
                  setShowMore(false);
                  onDownload('docx');
                }}
              >
                <Download className="size-3.5" />
                导出 Word (.docx)
              </button>
              <button
                className="w-full px-3 py-1.5 text-left text-xs text-stone-700 hover:bg-stone-50 flex items-center gap-2 disabled:opacity-50"
                disabled={downloading}
                onClick={() => {
                  setShowMore(false);
                  onDownload('pdf');
                }}
              >
                <Download className="size-3.5" />
                导出 PDF (.pdf)
              </button>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
```

- [ ] **Step 2: document-editor.tsx 替换头部**

1. Props 增加 `onOpenShare: () => void;`（函数解构同步加）
2. import `EditorHeader from './editor-header';`，删除 `MemberAvatars` import（已由 header 内部使用）
3. 删除整个旧头部 JSX（`{/* Header */}` 到其闭合 `</div>`，含 保存按钮/saveLabel/v版本/分享/头像/.docx/.pdf 全部），替换为：

```tsx
      <EditorHeader
        docId={document.id}
        docName={document.name}
        saveStatus={saveStatus}
        version={version}
        provider={collabProvider}
        showManualSave={!token}
        onManualSave={handleSave}
        onDownload={handleDownload}
        downloading={downloading}
        onOpenShare={onOpenShare}
        apiFetch={apiFetch}
        onRenamed={onUpdate}
      />
```

4. 删除不再使用的 `saveLabel` 常量；`handleSave`、`handleDownload`、`version`、`downloading`、`collabProvider` 保留

- [ ] **Step 3: index.tsx 传入 onOpenShare**

`<DocumentEditor>` 调用处新增 prop：

```tsx
            onOpenShare={() => {
              const node = documents.find((d) => d.id === selectedDoc.id);
              if (node) setShareTarget(node);
            }}
```

- [ ] **Step 4: 类型检查**

Run: `cd web && npx tsc --noEmit`
Expected: 无新增错误

- [ ] **Step 5: 手动验证（localhost:9222）**

- 头部只剩：文档名 / 保存状态小字+v号 / 成员头像 / 分享 / ⋯
- 点文档名 → inline 重命名 → 回车生效，列表同步刷新
- ⋯ 菜单：导出 docx、导出 pdf 下载成功；非协同模式（无 token 时）显示手动保存
- 编辑内容触发保存状态小字变化（保存中→已保存）
- "分享"按钮打开协作者弹窗（下一任务合并链接 Tab）

- [ ] **Step 6: Commit**

```bash
git add web/src/components/collaboration/editor-header.tsx web/src/components/collaboration/document-editor.tsx web/src/components/collaboration/index.tsx
git commit -m "feat(collab): 极简编辑器头部, 导出/手动保存收进更多菜单, 支持点击重命名"
```

---

### Task 6: 分享弹窗合并（协作者 + 公开链接 双 Tab）

**Files:**
- Modify: `web/src/components/collaboration/share-dialog.tsx`
- Delete: `web/src/components/collaboration/share-link-dialog.tsx`
- Modify: `web/src/components/collaboration/document-editor.tsx`

- [ ] **Step 1: share-link-dialog 内容迁移为 share-dialog 内部 Tab 组件**

在 `share-dialog.tsx` 中：

1. 将 `share-link-dialog.tsx` 的 `ShareData` interface、全部 state（share/loading/permission/password/expiresDays/copied/copiedTimerRef）、全部 handler（loadShare/handleCreateOrUpdate/handleDelete/handleCopy 等）与内容区 JSX **原样迁移**为同文件内的子组件：

```tsx
function ShareLinkTab({
  docId,
  apiFetch,
}: {
  docId: string;
  apiFetch: (url: string, options?: RequestInit) => Promise<Response>;
}) {
  // ← share-link-dialog.tsx 中 ShareLinkDialog 函数体原样迁入，
  //    删除其外层 fixed 遮罩/弹窗壳 JSX 与 open/onClose 相关逻辑，
  //    useEffect(() => { if (open) loadShare(); ... }) 改为
  //    useEffect(() => { loadShare(); }, [loadShare]);
  //    return 只保留原弹窗的内容区（权限选择/密码/有效期/链接展示/复制/删除按钮）
}
```

2. 迁移完成后需要的 lucide 图标（`Copy`, `Link`, `Lock` 等）合并进 share-dialog 的 import。

- [ ] **Step 2: ShareDialog 增加 Tab 切换**

在 `ShareDialog` 组件内新增：

```tsx
  const [tab, setTab] = useState<'collaborators' | 'link'>('collaborators');
```

弹窗标题栏下方插入 Tab 栏：

```tsx
        <div className="flex items-center gap-1 border-b border-stone-100 mb-3">
          {(
            [
              { key: 'collaborators', label: '协作者' },
              { key: 'link', label: '公开链接' },
            ] as const
          ).map((t) => (
            <button
              key={t.key}
              className={`px-3 py-2 text-xs font-medium border-b-2 -mb-px transition-colors ${
                tab === t.key
                  ? 'border-stone-900 text-stone-900'
                  : 'border-transparent text-stone-400 hover:text-stone-700'
              }`}
              onClick={() => setTab(t.key)}
            >
              {t.label}
            </button>
          ))}
        </div>
```

原协作者列表内容包进 `{tab === 'collaborators' && (...)}`，并追加：

```tsx
        {tab === 'link' && <ShareLinkTab docId={docId} apiFetch={apiFetch} />}
```

- [ ] **Step 3: 删除 share-link-dialog.tsx 与编辑器中的引用**

1. 删除文件 `web/src/components/collaboration/share-link-dialog.tsx`
2. `document-editor.tsx` 中删除 `import ShareLinkDialog from './share-link-dialog';`、`showShareLink` state、`<ShareLinkDialog ... />` JSX（头部分享按钮已在 Task 5 改为 onOpenShare，无残留入口）

- [ ] **Step 4: 类型检查**

Run: `cd web && npx tsc --noEmit`
Expected: 无新增错误

- [ ] **Step 5: 手动验证（localhost:9222）**

- 头部"分享" → 弹窗默认"协作者"Tab：添加/改角色/移除协作者正常
- 切"公开链接"Tab：创建链接（view/edit、密码、有效期）、复制、删除正常
- 左栏列表项的分享入口打开同一弹窗
- 无痕窗口访问 `/share/doc/<token>` 免登录页正常

- [ ] **Step 6: Commit**

```bash
git add -A web/src/components/collaboration/share-dialog.tsx web/src/components/collaboration/share-link-dialog.tsx web/src/components/collaboration/document-editor.tsx
git commit -m "refactor(collab): 分享弹窗合并为协作者+公开链接双Tab, 移除独立链接弹窗"
```

---

### Task 7: 左栏改造（搜索框 + 底部"+ 新建"下拉）

**Files:**
- Modify: `web/src/components/collaboration/document-list.tsx`

- [ ] **Step 1: 头部图标按钮移除 + 搜索框**

1. 删除头部 `导入 Word`（FileUp）与 `新建文件夹`（FolderPlus）两个 CendTooltip 按钮块（import 中 `FileUp`/`FolderPlus` 若仍被下拉使用则保留）
2. 新增 state：`const [query, setQuery] = useState('');`
3. 头部标题行之下插入搜索框：

```tsx
      <div className="px-3 pb-2">
        <div className="relative">
          <Search className="absolute left-2 top-1/2 -translate-y-1/2 size-3.5 text-stone-300 pointer-events-none" />
          <input
            className="w-full pl-7 pr-2 py-1.5 text-xs bg-stone-50 border border-stone-200 rounded-lg outline-none focus:border-stone-400 placeholder:text-stone-300"
            placeholder="搜索文档..."
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
        </div>
      </div>
```

（import 增加 `Search`）

4. 过滤逻辑（组件内、return 前）：

```tsx
  const q = query.trim().toLowerCase();
  const filteredDocuments = q
    ? documents.filter((d) => d.name.toLowerCase().includes(q))
    : documents;
```

`<FolderTree documents={documents}>` 改为 `documents={filteredDocuments}`；空状态判断同步用 `filteredDocuments`（搜索无结果时显示"暂无匹配文档"）。

- [ ] **Step 2: 底部"+ 新建"下拉**

1. 新增 state：

```tsx
  const [showCreateMenu, setShowCreateMenu] = useState(false);
  const [showNewDoc, setShowNewDoc] = useState(false);
  const [newDocName, setNewDocName] = useState('');
```

2. 新建文档 handler：

```tsx
  const handleCreateDoc = async () => {
    const name = newDocName.trim();
    if (!name) {
      setShowNewDoc(false);
      return;
    }
    try {
      const resp = await apiFetch('/api/v1/collaboration/documents', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name, markdown_content: '' }),
      });
      const result = await resp.json();
      setNewDocName('');
      setShowNewDoc(false);
      onRefresh();
      if (result.code === 0 && result.data?.id) {
        onSelect(result.data as DocumentNode);
      }
    } catch (e) {
      console.error('创建文档失败:', e);
    }
  };
```

3. 组件 JSX 末尾（删除确认弹窗之前）、树列表之后插入底部栏：

```tsx
      {/* 底部新建入口 */}
      <div className="relative border-t border-stone-100 p-2 mt-auto">
        {showNewDoc && (
          <input
            type="text"
            className="w-full mb-1.5 px-2 py-1 text-xs border border-stone-300 rounded focus:outline-none focus:border-stone-500"
            placeholder="文档名称..."
            value={newDocName}
            autoFocus
            onChange={(e) => setNewDocName(e.target.value)}
            onBlur={handleCreateDoc}
            onKeyDown={(e) => {
              if (e.key === 'Enter') handleCreateDoc();
              if (e.key === 'Escape') setShowNewDoc(false);
            }}
          />
        )}
        {showCreateMenu && (
          <div className="absolute bottom-full left-2 right-2 mb-1 bg-white border border-stone-200 rounded-lg shadow-lg py-1 z-50">
            <button
              className="w-full px-3 py-1.5 text-left text-xs text-stone-700 hover:bg-stone-50 flex items-center gap-2"
              onClick={() => {
                setShowCreateMenu(false);
                setShowNewDoc(true);
              }}
            >
              <FileText className="size-3.5" />
              新建文档
            </button>
            <button
              className="w-full px-3 py-1.5 text-left text-xs text-stone-700 hover:bg-stone-50 flex items-center gap-2"
              onClick={() => {
                setShowCreateMenu(false);
                setShowNewFolder(true);
              }}
            >
              <FolderPlus className="size-3.5" />
              新建文件夹
            </button>
            <button
              className="w-full px-3 py-1.5 text-left text-xs text-stone-700 hover:bg-stone-50 flex items-center gap-2"
              onClick={() => {
                setShowCreateMenu(false);
                setShowImport(true);
              }}
            >
              <FileUp className="size-3.5" />
              导入 Word
            </button>
          </div>
        )}
        <button
          className="w-full flex items-center justify-center gap-1 px-3 py-1.5 text-xs font-medium text-stone-600 border border-dashed border-stone-300 rounded-lg hover:border-stone-400 hover:text-stone-900 transition-colors"
          onClick={() => setShowCreateMenu((v) => !v)}
        >
          <Plus className="size-3.5" />
          新建
        </button>
      </div>
```

（import 增加 `FileText`, `Plus`；树列表容器需 `flex-1 overflow-y-auto` 确保底部栏贴底 —— 若现有树容器无此类，则给包裹 FolderTree 的分支外套 `<div className="flex-1 overflow-y-auto min-h-0">`）

- [ ] **Step 3: 类型检查**

Run: `cd web && npx tsc --noEmit`
Expected: 无新增错误

- [ ] **Step 4: 手动验证（localhost:9222）**

- 搜索框输入关键字实时过滤文档（文件夹结构仍正常显示）
- "+ 新建" → 三项菜单；新建文档回车后创建并自动选中；新建文件夹、导入 Word 与原功能一致
- 头部不再有两个小图标按钮

- [ ] **Step 5: Commit**

```bash
git add web/src/components/collaboration/document-list.tsx
git commit -m "feat(collab): 左栏增加文档搜索, 新建/导入合并为底部下拉入口"
```

---

### Task 8: 工具栏重组（插入类按钮合并为"插入"下拉）

**Files:**
- Modify: `web/src/components/collaboration/toolbar-plugin.tsx`

现状：单行 flex 按钮 + 分隔线；标注 select(~981-997)、链接(~1001-1048)、代码块(~1050-1073)、图片(~1075-1121)、公式(~1124-1180)、Emoji(~1182-1201) 为独立按钮，各带 anchored popover。

- [ ] **Step 1: 新增插入菜单 state 与菜单项子组件**

组件内新增 state：

```tsx
  const [showInsertMenu, setShowInsertMenu] = useState(false);
```

文件底部（export default 之外）新增：

```tsx
function InsertMenuItem({
  label,
  onClick,
}: {
  label: string;
  onClick: () => void;
}) {
  return (
    <button
      className="w-full px-3 py-1.5 text-left text-xs text-stone-700 hover:bg-stone-50"
      onMouseDown={(e) => {
        e.preventDefault();
        onClick();
      }}
    >
      {label}
    </button>
  );
}
```

- [ ] **Step 2: 用"插入"下拉替换 6 个独立按钮块**

删除以下 JSX 块（保留其 handler 函数与 state 不动）：
- 标注 select 块（`{/* Callout Dropdown */}` 整个 relative div）
- 链接按钮块（`{/* Link */}` relative div，**其中 `{showLinkInput && (...)}` popover JSX 剪切备用**）
- 代码块按钮（`{/* Code Block */}` CendTooltip 块）
- 图片按钮块（`{/* Image */}` relative div，`{showImageInput && (...)}` popover 剪切备用）
- 公式按钮块（`{/* Math Formula */}` relative div，`{showMathInput && (...)}` popover 剪切备用）
- Emoji 按钮块（`{/* Emoji */}` relative div，`{showEmoji && <EmojiPicker .../>}` 剪切备用）
- 相应多余的分隔线 `<div className="w-px h-5 bg-black/[0.06] mx-0.5" />`（保留组间必要的一条）

在原标注 select 位置插入统一下拉（原 4 个 popover 原样放回此容器内，定位基准不变）：

```tsx
      {/* 插入 下拉 */}
      <div className="relative">
        <CendTooltip title="插入">
          <button
            className="h-7 px-2 flex items-center gap-1 rounded text-xs text-black/60 hover:bg-black/[0.04] transition-colors whitespace-nowrap"
            onMouseDown={(e) => {
              e.preventDefault();
              setShowInsertMenu((v) => !v);
            }}
          >
            ＋ 插入
            <svg
              className="w-2.5 h-2.5"
              fill="none"
              stroke="currentColor"
              viewBox="0 0 24 24"
            >
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                strokeWidth={2}
                d="M19 9l-7 7-7-7"
              />
            </svg>
          </button>
        </CendTooltip>
        {showInsertMenu && (
          <div className="absolute top-full left-0 mt-1 w-40 bg-white border border-stone-200 rounded-lg shadow-lg py-1 z-50">
            <InsertMenuItem
              label="🔗 链接"
              onClick={() => {
                setShowInsertMenu(false);
                setShowLinkInput(true);
                setTimeout(() => linkInputRef.current?.focus(), 50);
              }}
            />
            <InsertMenuItem
              label="🖼️ 图片"
              onClick={() => {
                setShowInsertMenu(false);
                setShowImageInput(true);
                setTimeout(() => imageInputRef.current?.focus(), 50);
              }}
            />
            <InsertMenuItem
              label="⌨️ 代码块"
              onClick={() => {
                setShowInsertMenu(false);
                insertCodeBlock();
              }}
            />
            <InsertMenuItem
              label="ƒx 数学公式"
              onClick={() => {
                setShowInsertMenu(false);
                setShowMathInput(true);
                setTimeout(() => mathInputRef.current?.focus(), 50);
              }}
            />
            <InsertMenuItem
              label="😊 表情符号"
              onClick={() => {
                setShowInsertMenu(false);
                setShowEmoji(true);
              }}
            />
            <div className="my-1 border-t border-stone-100" />
            <InsertMenuItem
              label="💡 信息标注"
              onClick={() => {
                setShowInsertMenu(false);
                insertCallout('info');
              }}
            />
            <InsertMenuItem
              label="⚠️ 警告标注"
              onClick={() => {
                setShowInsertMenu(false);
                insertCallout('warning');
              }}
            />
            <InsertMenuItem
              label="✅ 提示标注"
              onClick={() => {
                setShowInsertMenu(false);
                insertCallout('tip');
              }}
            />
            <InsertMenuItem
              label="🚫 重要标注"
              onClick={() => {
                setShowInsertMenu(false);
                insertCallout('danger');
              }}
            />
          </div>
        )}
        {/* ↓ 原 4 个输入 popover 原样迁入本容器 ↓ */}
        {showLinkInput && (
          /* 原 Link popover JSX 原样粘贴 */
        )}
        {showImageInput && (
          /* 原 Image popover JSX 原样粘贴 */
        )}
        {showMathInput && (
          /* 原 Math popover JSX 原样粘贴 */
        )}
        {showEmoji && (
          <EmojiPicker onSelect={insertEmoji} onClose={() => setShowEmoji(false)} />
        )}
      </div>
```

注：上面三处 `/* 原 ... popover JSX 原样粘贴 */` 指从被删除按钮块中剪切的、以 `{showLinkInput && (` 等开头的完整 popover div（内容零修改）。

- [ ] **Step 3: 窄屏兜底**

检查工具栏最外层容器 div：若无 `flex-wrap`，为其 className 增加 `flex-wrap`（插入合并后按钮大幅减少，允许换行即可满足窄屏场景，不做溢出检测菜单）。

- [ ] **Step 4: 类型检查**

Run: `cd web && npx tsc --noEmit`
Expected: 无新增错误

- [ ] **Step 5: 手动验证（localhost:9222）**

- 工具栏出现"＋ 插入"下拉，原 6 类独立按钮消失，工具栏明显变短
- 窗口缩窄至 ~1100px，工具栏换行不溢出裁切
- 逐项验证：链接（弹输入框→插入生效）、图片（URL 插入）、代码块、公式（LaTeX 渲染）、Emoji、4 种标注
- 引用/待办/列表/对齐/颜色/字体字号等保留按钮功能不变
- 字数统计仍在最右侧

- [ ] **Step 6: Commit**

```bash
git add web/src/components/collaboration/toolbar-plugin.tsx
git commit -m "refactor(collab): 工具栏插入类按钮合并为插入下拉菜单"
```

---

### Task 9: 全量回归验证

**Files:** 无新改动（发现问题则修复后补 commit）

- [ ] **Step 1: 静态检查**

```bash
cd web && npx tsc --noEmit && npm run lint -- --quiet 2>/dev/null || npx eslint src/components/collaboration --quiet
```

Expected: collaboration 目录无新增错误

- [ ] **Step 2: 手动回归清单（localhost:9222，对照《协作功能测试清单.md》相关项）**

核心链路：
- [ ] 文档 CRUD：新建（底部入口）/ 重命名（头部点名 + 列表右键）/ 删除
- [ ] Yjs 协同：两个浏览器同时编辑，内容同步、光标同步、头部成员头像显示
- [ ] 保存：编辑后状态小字 保存中→已保存，v 号递增
- [ ] 5 个面板逐一开关互斥：评论增删/resolve、附件上传下载、版本恢复（确认弹窗+reload）、格式规则应用、审计日志（仅 owner 可见图标）
- [ ] 分享双 Tab：协作者增删改 + 公开链接创建/复制/删除，免登录页可访问
- [ ] 导出：⋯菜单 docx/pdf 下载打开正常
- [ ] 导入 Word：底部入口上传 → 列表刷新
- [ ] 搜索框过滤正常
- [ ] 左栏折叠按钮（ChevronLeft）仍正常
- [ ] 非 owner 文档（用第二账号）：审计图标隐藏、viewer 只读表现不回归

- [ ] **Step 3: 发现问题逐个修复并单独 commit（fix(collab): ...）**

- [ ] **Step 4: 征询用户是否执行 `npm run build` 做生产构建验证（默认不构建，需用户确认）**

---

## 任务依赖

```
Task 1 → Task 2 → Task 3 → Task 4 → Task 5 → Task 6
                                  ↘ Task 7（仅依赖 Task 4）
Task 8 独立（可在 Task 4 后任意时点）
Task 9 最后
```
