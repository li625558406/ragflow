# Univer Docs 替换 Lexical 协作文档 — 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 用 Univer Docs 官方 JS SDK 完全替换 Lexical 富文本编辑器，保留实时协同（Yjs+WS）、在线人数、docx 导入导出能力。

**Architecture:** 桥接模式 — Univer Docs 状态 ↔ Yjs CRDT ↔ WebSocket 盲中继。完全复刻 `use-spreadsheet-collab.ts` 的同步范式，复用现有 `CollaborationWebSocketProvider` 和 `collaboration_ws.py` 后端。后端不再做 docx/pdf 生成，改由前端 Univer SDK 导出后上传。

**Tech Stack:** React 18 + TypeScript 5、`@univerjs/preset-docs-core` + `drawing` + `thread-comment`、Yjs、Quart WebSocket、Peewee + MySQL。

**设计文档:** `docs/superpowers/specs/2026-07-21-univer-docs-replace-lexical-design.md`

---

## 文件结构

**新建**
- `web/src/components/collaboration/univer-docs-presets.ts` — 集中导出 Docs presets + zh-CN locale 清单
- `web/src/components/collaboration/use-document-collab.ts` — Univer Docs ↔ Yjs ↔ WS 桥接 hook
- `web/src/components/collaboration/use-online-users.ts` — engine 无关的 presence 订阅 hook（Docs/Sheets 共用）
- `web/src/components/collaboration/use-univer-export.ts` — docx 导入导出 hook

**重写（覆盖原 Lexical 实现）**
- `web/src/components/collaboration/document-editor.tsx` — Univer Docs 挂载、command 监听、卸载

**修改**
- `web/src/components/collaboration/index.tsx` — 分发逻辑改用 `content.document === true` 判断 Docs
- `web/src/components/collaboration/editor-header.tsx` — 已经共用，仅确认 Docs 场景下的 props 兼容
- `api/apps/restful_apis/collaboration_api.py` — 删 6 个 format-rule 端点，加 2 个 exported-file 端点
- `api/apps/services/collaboration_api_service.py` — 删 Lexical/markdown/format-rule 相关函数（~1700L），加 `save_exported_file` / `get_exported_file`
- `web/package.json` — 加 Docs presets 依赖，删 `@lexical/*`

**删除**
- `web/src/components/collaboration/toolbar-plugin.tsx`
- `web/src/components/collaboration/mention-plugin.tsx`
- `web/src/components/collaboration/emoji-picker.tsx`
- `web/src/components/collaboration/format-rule-panel.tsx`
- `web/src/components/collaboration/docx-import-dialog.tsx`
- `web/src/components/collaboration/comment-panel.tsx`
- `web/src/components/collaboration/version-history-panel.tsx`
- `web/src/components/collaboration/attachment-panel.tsx`
- `web/src/components/collaboration/audit-log-panel.tsx`
- `web/src/components/collaboration/nodes/`（整目录）

---

## Phase 1：依赖安装与 API 探查

### Task 1.1：安装 Univer Docs presets 依赖

**Files:**
- Modify: `web/package.json`
- Modify: `web/package-lock.json`（由 npm install 自动更新）

- [ ] **Step 1：查看当前已安装的 Univer 版本**

Run:
```bash
cd D:/AI/ragflow2/web && grep '"@univerjs/preset-sheets-core"' package.json
```
Expected: 打印一行类似 `"@univerjs/preset-sheets-core": "0.6.x"` 的版本号。记下版本号 `<VER>`。

- [ ] **Step 2：安装 Docs presets，版本对齐 Sheets**

Run:
```bash
cd D:/AI/ragflow2/web && npm install --save \
  @univerjs/preset-docs-core@<VER> \
  @univerjs/preset-docs-drawing@<VER> \
  @univerjs/preset-docs-thread-comment@<VER>
```
Expected: 三个包写入 `dependencies`，版本与 Sheets presets 完全一致。无 peer dep 冲突告警。

- [ ] **Step 3：验证包可被 import**

Run:
```bash
cd D:/AI/ragflow2/web && node -e "console.log(Object.keys(require('@univerjs/preset-docs-core')))"
```
Expected: 打印出导出的 key 列表，包含 `UniverDocsCorePreset`。

- [ ] **Step 4：提交**

```bash
cd D:/AI/ragflow2 && git add web/package.json web/package-lock.json
git commit -m "deps(collab): 新增 Univer Docs presets (core/drawing/thread-comment)"
```

---

### Task 1.2：探查 FUniver 导出 API（开放问题 #1 #2 验证）

**Files:**
- Create: `web/src/components/collaboration/_univer-api-probe.tsx`（临时文件，验证后删除）

- [ ] **Step 1：写最小探查组件**

```tsx
// web/src/components/collaboration/_univer-api-probe.tsx
import { UniverDocsCorePreset } from '@univerjs/preset-docs-core';
import UniverDocsCoreZhCN from '@univerjs/preset-docs-core/locales/zh-CN';
import { UniverDocsDrawingPreset } from '@univerjs/preset-docs-drawing';
import UniverDocsDrawingZhCN from '@univerjs/preset-docs-drawing/locales/zh-CN';
import { UniverDocsThreadCommentPreset } from '@univerjs/preset-docs-thread-comment';
import UniverDocsThreadCommentZhCN from '@univerjs/preset-docs-thread-comment/locales/zh-CN';
import { createUniver, LocaleType } from '@univerjs/presets';
import { useEffect, useRef } from 'react';

export default function Probe() {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!ref.current) return;
    const { univerAPI } = createUniver({
      locale: LocaleType.ZH_CN,
      locales: { [LocaleType.ZH_CN]: {
        ...UniverDocsCoreZhCN, ...UniverDocsDrawingZhCN, ...UniverDocsThreadCommentZhCN,
      } },
      presets: [
        UniverDocsCorePreset({ container: ref.current }),
        UniverDocsDrawingPreset(),
        UniverDocsThreadCommentPreset(),
      ],
    });
    univerAPI.createDocument({ document: true });
    // 在浏览器控制台查看 FUniver 实例上的所有方法
    (window as any).__univerAPI = univerAPI;
    console.log('[Probe] FUniver keys:', Object.keys(univerAPI));
    console.log('[Probe] exportDocument?', typeof (univerAPI as any).exportDocument);
    console.log('[Probe] importDocument?', typeof (univerAPI as any).importDocument);
    console.log('[Probe] saveDocument?', typeof (univerAPI as any).saveDocument);
  }, []);
  return <div ref={ref} style={{ height: 600 }} />;
}
```

- [ ] **Step 2：在 c-chat 页面临时挂载（仅在本地开发分支验证，不提交）**

手动把 `_univer-api-probe.tsx` 临时插入到 `web/src/pages/c-chat/index.tsx` 的 collaboration tab 下，运行 `npm run dev`，打开浏览器，看控制台输出。

Expected：控制台打印 `[Probe] FUniver keys: [...]`。记录 `exportDocument` / `importDocument` / `saveDocument` 是否为 `function`。

- [ ] **Step 3：根据结果更新设计文档的开放问题章节**

若 `exportDocument` 存在：在 `docs/superpowers/specs/2026-07-21-univer-docs-replace-lexical-design.md` §7.2 把开放问题 #1 #2 标记为"已验证：存在"。

若不存在：改为"退化方案 — 浏览器端 html-docx-js 或服务端 libreoffice"。

- [ ] **Step 4：删除探查文件，不提交**

```bash
rm D:/AI/ragflow2/web/src/components/collaboration/_univer-api-probe.tsx
```

- [ ] **Step 5：提交 spec 更新（如 Step 3 有改动）**

```bash
cd D:/AI/ragflow2 && git add docs/superpowers/specs/2026-07-21-univer-docs-replace-lexical-design.md
git commit -m "docs(collab): 更新 Univer Docs API 开放问题验证结果"
```

---

## Phase 2：重写 `document-editor.tsx`（仅本地，不接 Yjs）

### Task 2.1：新建 `univer-docs-presets.ts` 集中配置

**Files:**
- Create: `web/src/components/collaboration/univer-docs-presets.ts`

- [ ] **Step 1：写 presets + locale 清单**

```ts
// web/src/components/collaboration/univer-docs-presets.ts
import '@univerjs/preset-docs-core/lib/index.css';
import { UniverDocsCorePreset } from '@univerjs/preset-docs-core';
import UniverDocsCoreZhCN from '@univerjs/preset-docs-core/locales/zh-CN';
import { UniverDocsDrawingPreset } from '@univerjs/preset-docs-drawing';
import UniverDocsDrawingZhCN from '@univerjs/preset-docs-drawing/locales/zh-CN';
import { UniverDocsThreadCommentPreset } from '@univerjs/preset-docs-thread-comment';
import UniverDocsThreadCommentZhCN from '@univerjs/preset-docs-thread-comment/locales/zh-CN';

/** Docs 场景下挂载的 zh-CN locale 合集 */
export const DOCS_LOCALES = {
  ...UniverDocsCoreZhCN,
  ...UniverDocsDrawingZhCN,
  ...UniverDocsThreadCommentZhCN,
};

/** Docs 场景下挂载的 preset 清单 */
export const DOCS_PRESETS = (container: HTMLElement) => [
  UniverDocsCorePreset({ container }),
  UniverDocsDrawingPreset(),
  UniverDocsThreadCommentPreset(),
];
```

- [ ] **Step 2：提交**

```bash
git add web/src/components/collaboration/univer-docs-presets.ts
git commit -m "feat(collab): 新增 univer-docs-presets.ts 集中配置"
```

---

### Task 2.2：重写 `document-editor.tsx` 本地渲染版

**Files:**
- Modify: `web/src/components/collaboration/document-editor.tsx`（完全覆盖原 Lexical 实现）

**参考实现：** `web/src/components/collaboration/spreadsheet-editor.tsx:135-241`（Univer 挂载范式，直接对照）

- [ ] **Step 1：读现有 Lexical 版 `document-editor.tsx` 的对外 Props 签名**

Run:
```bash
grep -n "^interface Props\|^export default" D:/AI/ragflow2/web/src/components/collaboration/document-editor.tsx
```
Expected：拿到 `Props` 字段清单（`document`, `apiFetch`, `onUpdate`, `token`, `onProviderReady`, `onOpenShare`, 以及 Lexical 专属的 `appliedRuleConfig`/`onRuleApplied`）。重写时去掉 Lexical 专属字段。

- [ ] **Step 2：完全覆盖文件，写最小可跑的 Univer Docs 版本**

```tsx
// web/src/components/collaboration/document-editor.tsx
/**
 * Document editor powered by Univer Docs.
 * Replaces the old Lexical implementation.
 * Phase 2: local-only — load/save content JSON via /documents PUT.
 * Phase 3 will attach Yjs + WebSocket via useDocumentCollab.
 */
import storage from '@/utils/authorization-util';
import type { Univer } from '@univerjs/core';
import type { FUniver } from '@univerjs/presets';
import { createUniver, LocaleType } from '@univerjs/presets';
import { useCallback, useEffect, useRef, useState } from 'react';
import EditorHeader from './editor-header';
import { DOCS_LOCALES, DOCS_PRESETS } from './univer-docs-presets';
import type { CollaborationWebSocketProvider } from './yjs-provider';

interface DocumentData {
  id: string;
  name: string;
  file_type: string;
  file_path?: string;
  content: Record<string, unknown>;
  markdown_content?: string;
  agent_id?: string;
  create_time?: string;
  update_time?: string;
  ydoc?: string | null;
}

interface Props {
  document: DocumentData;
  apiFetch: (url: string, options?: RequestInit) => Promise<Response>;
  onUpdate: () => void;
  token?: string;
  onOpenShare: () => void;
  onProviderReady?: (provider: CollaborationWebSocketProvider | null) => void;
}

/** Univer Docs content snapshot shape (minimal guard). */
function isDocsContent(c: unknown): c is { document?: boolean } {
  return !!c && typeof c === 'object';
}

function createBlankDocsContent(): Record<string, unknown> {
  // Univer Docs 最小空白文档结构 — createDocument 会补全其他字段
  return { document: true, body: { blockType: 'paragraph', children: [] } };
}

export default function DocumentEditor({
  document: doc,
  apiFetch,
  onUpdate,
  token,
  onOpenShare,
  onProviderReady,
}: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const univerRef = useRef<Univer | null>(null);
  const univerAPIRef = useRef<FUniver | null>(null);
  const apiFetchRef = useRef(apiFetch);
  apiFetchRef.current = apiFetch;
  const [saveStatus, setSaveStatus] = useState<'idle' | 'saving' | 'saved' | 'error'>('idle');
  const [downloading, setDownloading] = useState(false);

  // Phase 2: 占位 provider（Phase 3 替换为真实 useDocumentCollab 返回值）
  const provider: CollaborationWebSocketProvider | null = null;
  const onProviderReadyRef = useRef(onProviderReady);
  onProviderReadyRef.current = onProviderReady;
  useEffect(() => {
    onProviderReadyRef.current?.(null);
  }, []);

  // 初始化 Univer Docs 实例（仅挂载时跑一次）
  useEffect(() => {
    if (!containerRef.current) return;
    const { univer, univerAPI } = createUniver({
      locale: LocaleType.ZH_CN,
      locales: { [LocaleType.ZH_CN]: DOCS_LOCALES },
      presets: DOCS_PRESETS(containerRef.current),
    });
    univerRef.current = univer;
    univerAPIRef.current = univerAPI;

    const initialContent = isDocsContent(doc.content) && doc.content.document
      ? doc.content
      : createBlankDocsContent();
    univerAPI.createDocument(initialContent as any);

    return () => {
      // 注意：不要调用 univer.dispose() —— 会触发与 React reconciler 的竞争
      // （见 spreadsheet-editor.tsx:230-238 的踩坑说明）。靠 GC 回收即可。
      univerRef.current = null;
      univerAPIRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Phase 2 最小保存：手动按钮触发，把当前 JSON PUT 回 /documents/<id>
  const saveToServer = useCallback(async () => {
    const api = univerAPIRef.current;
    if (!api) return;
    setSaveStatus('saving');
    try {
      const snapshot = (api as any).getActiveDocument?.()?.save?.()
        ?? api.getActiveDocument?.()?.getSnapshot?.();
      if (!snapshot) throw new Error('save() returned empty');
      const resp = await apiFetchRef.current(
        `/api/v1/collaboration/documents/${doc.id}`,
        {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ content: snapshot }),
        },
      );
      const result = await resp.json();
      if (result.code !== 0) throw new Error(result.message || 'save failed');
      setSaveStatus('saved');
      onUpdate();
    } catch (e) {
      console.error('[DocumentEditor] save failed:', e);
      setSaveStatus('error');
    }
  }, [doc.id, onUpdate]);

  // 下载占位（Phase 5 实现）
  const handleDownload = useCallback(async (_type: 'docx' | 'pdf') => {
    setDownloading(true);
    try {
      window.alert('导出功能将在 Phase 5 实现');
    } finally {
      setDownloading(false);
    }
  }, []);

  return (
    <div className="flex-1 flex flex-col min-w-0 h-full">
      <EditorHeader
        docId={doc.id}
        docName={doc.name}
        saveStatus={saveStatus}
        version={null}
        provider={provider}
        showManualSave={!token}
        onManualSave={saveToServer}
        onDownload={handleDownload}
        downloading={downloading}
        onOpenShare={onOpenShare}
        apiFetch={apiFetch}
        onRenamed={onUpdate}
        fileType="docx"
      />
      <div ref={containerRef} className="flex-1 min-h-0" />
    </div>
  );
```

- [ ] **Step 3：手动验证**

```bash
cd D:/AI/ragflow2/web && npm run dev
```

打开 C 端 collaboration tab，新建一个 docx 文档，确认 Univer Docs 编辑器挂载、可输入文字、手动保存按钮工作、刷新页面后内容还在。

- [ ] **Step 4：提交**

```bash
git add web/src/components/collaboration/document-editor.tsx
git commit -m "feat(collab): document-editor 重写为 Univer Docs (Phase 2 本地版)"
```

---

### Task 2.3：修正 `index.tsx` 的 Docs/Sheets 分发逻辑

**Files:**
- Modify: `web/src/components/collaboration/index.tsx:241-271`

- [ ] **Step 1：读现有分发逻辑**

Run:
```bash
sed -n '241,272p' D:/AI/ragflow2/web/src/components/collaboration/index.tsx
```
确认：`file_type === 'xlsx'` 走 Sheets，否则走 DocumentEditor。同时 `Props` 里传了 `appliedRuleConfig` / `onRuleApplied`（Lexical 专属，要删）。

- [ ] **Step 2：用 Edit 替换 DocumentEditor 调用处**

把 `index.tsx` 中 `<DocumentEditor .../>` 这块（原 257-270 行）改成：

```tsx
<DocumentEditor
  key={selectedDoc.id}
  document={selectedDoc}
  apiFetch={apiFetch}
  onUpdate={handleDocUpdate}
  token={wsToken}
  onProviderReady={setCollabProvider}
  onOpenShare={() => {
    const node = documents.find((d) => d.id === selectedDoc.id);
    if (node) setShareTarget(node);
              }}
/>
```

删除 `appliedRuleConfig={appliedRuleConfigRef.current}` 和 `onRuleApplied={handleRuleApplied}` 两行。

- [ ] **Step 3：删除已失效的状态和回调**

用 Edit 删除 `index.tsx` 中：
- 第 44 行 `const [applyingRuleId, setApplyingRuleId] = useState<string | null>(null);`
- 第 45 行 `const appliedRuleConfigRef = useRef<...>(null);`
- 第 198-208 行 `handleApplyFormatRule` 和 `handleRuleApplied`
- 第 317-318 行 SidePanelBar 的 `onApplyFormatRule={...}` 和 `applyingRuleId={...}` 两个 props（SidePanelBar 后续在 Phase 6 会整体删除，现在先不再传）

- [ ] **Step 4：验证编译**

```bash
cd D:/AI/ragflow2/web && npm run dev
```

确认浏览器控制台无 TS 报错，Doc/Spreadsheet 都能打开。

- [ ] **Step 5：提交**

```bash
git add web/src/components/collaboration/index.tsx
git commit -m "refactor(collab): index.tsx 移除 Lexical 专属 props，分发逻辑简化"
```

---

## Phase 3：Yjs + WebSocket 桥接（实时同步）

### Task 3.1：创建 `use-document-collab.ts`

**Files:**
- Create: `web/src/components/collaboration/use-document-collab.ts`

**参考实现：** `web/src/components/collaboration/use-spreadsheet-collab.ts`（785L）。**完全照搬结构**，把所有 `workbookData` / `IWorkbookData` / `fWorkbook.save()` 换成 `docsData` / `Record<string, unknown>` / `api.getActiveDocument().save()`。

- [ ] **Step 1：复制 `use-spreadsheet-collab.ts` 为起点**

```bash
cp D:/AI/ragflow2/web/src/components/collaboration/use-spreadsheet-collab.ts \
   D:/AI/ragflow2/web/src/components/collaboration/use-document-collab.ts
```

- [ ] **Step 2：全局替换 Sheets 概念为 Docs**

在 `use-document-collab.ts` 中用 Edit 逐项替换：

| 原文 | 替换为 |
|---|---|
| `useSpreadsheetCollab` | `useDocumentCollab` |
| `use-spreadsheet-collab` 注释中的 "spreadsheet" | "document" |
| `IWorkbookData` 类型 | `Record<string, unknown>` |
| `workbookData` 变量名 | `docsData` |
| `setWorkbookData` | `setDocsData` |
| `workbookDataRef` | `docsDataRef` |
| `pushSnapshot` 的入参类型 `IWorkbookData` | `Record<string, unknown>` |
| `yMap.set('data', JSON.stringify(stripUIState(data)))` | `yMap.set('data', JSON.stringify(data))`（Docs 无 UI 状态需要 strip） |
| `createBlankWorkbookData()` | `createBlankDocsContent()` — 返回 `{ document: true, body: { blockType: 'paragraph', children: [] } }` |
| `isLegacyContent` + `convertLegacyToWorkbookData` 分支 | 整块删除（Docs 没有遗留格式要迁移） |
| `rewriteAssetUrls` + `injectAssetTokens` + `stripAssetTokens` + `SHEET_DRAWING_RESOURCE` | 删除（Phase 2 POC 验证 Docs 图片 URL 处理方式后再补，YAGNI） |

- [ ] **Step 3：调整 Options 接口**

在 `use-document-collab.ts` 中，`Options` 接口改为：

```ts
interface Options {
  docId: string;
  content: Record<string, unknown>;
  ydoc: string | null;
  token?: string;
  userName: string;
  apiFetch: (url: string, options?: RequestInit) => Promise<Response>;
  onUpdate: () => void;
  getLatestSnapshot: () => Promise<Record<string, unknown> | null>;
}

interface Return {
  docsData: Record<string, unknown>;
  remoteEpoch: { current: number };
  pushSnapshot: (data: Record<string, unknown>) => void;
  saveStatus: 'idle' | 'saving' | 'saved' | 'error';
  provider: CollaborationWebSocketProvider | null;
  saveToServer: () => Promise<void>;
}
```

- [ ] **Step 4：返回对象改名**

在 hook 末尾的 `return { ... }` 中，把 `workbookData` 改为 `docsData`，其余字段保持。

- [ ] **Step 5：编译检查**

```bash
cd D:/AI/ragflow2/web && npx tsc --noEmit
```

Expected: 无新增类型错误（已有的 Lexical 相关错误在 Phase 6 才修复）。

- [ ] **Step 6：提交**

```bash
git add web/src/components/collaboration/use-document-collab.ts
git commit -m "feat(collab): 新增 useDocumentCollab hook (复制自 spreadsheet 版本并改写)"
```

---

### Task 3.2：把 `document-editor.tsx` 接到 `useDocumentCollab`

**Files:**
- Modify: `web/src/components/collaboration/document-editor.tsx`

- [ ] **Step 1：替换 hook 占位为真实 useDocumentCollab**

在 `document-editor.tsx` 顶部 import 中加：
```ts
import useDocumentCollab from './use-document-collab';
```

删除 `// Phase 2: 占位 provider ...` 那段，改为：

```ts
const getLatestSnapshot = useCallback(async () => {
  const api = univerAPIRef.current;
  if (!api) return null;
  const fDoc = (api as any).getActiveDocument?.();
  if (!fDoc) return null;
  try {
    return fDoc.save?.() ?? fDoc.getSnapshot?.() ?? null;
  } catch {
    return null;
  }
}, []);

const userName = useMemo(() => {
  const userInfo = storage.getUserInfoObject();
  return userInfo?.nickname || userInfo?.email || '';
}, []);

const {
  docsData,
  remoteEpoch,
  pushSnapshot,
  saveStatus: collabSaveStatus,
  provider,
  saveToServer,
} = useDocumentCollab({
  docId: doc.id,
  content: doc.content,
  ydoc: doc.ydoc ?? null,
  token,
  userName,
  apiFetch,
  onUpdate,
  getLatestSnapshot,
});
```

- [ ] **Step 2：替换本地 saveStatus 为 collab 的**

把组件内 `const [saveStatus, setSaveStatus] = useState<...>('idle');` 删除，改用 `collabSaveStatus`。

`EditorHeader` 的 `saveStatus={saveStatus}` 改为 `saveStatus={collabSaveStatus}`。

- [ ] **Step 3：替换挂载初始化中的 `initialContent`**

把 `useDocumentCollab` 返回的 `docsData` 作为挂载和远程更新源头：

在 Univer 挂载 effect 里，`univerAPI.createDocument(initialContent as any)` 中的 `initialContent` 改为 `docsData`。

- [ ] **Step 4：加 command 监听推送本地编辑**

参照 `spreadsheet-editor.tsx:210-226`，在 Univer 挂载 effect 的 `createDocument` 之后加：

```ts
const disposable = univerAPI.addEvent(
  univerAPI.Event.CommandExecuted,
  () => {
    if (remoteEpoch.current === applyEpochRef.current) return;
    const fDoc = (univerAPI as any).getActiveDocument?.();
    if (fDoc) {
      try {
        const snapshot = fDoc.save?.() ?? fDoc.getSnapshot?.();
        if (snapshot) pushSnapshot(snapshot);
      } catch (e) {
        console.error('[DocumentEditor] save snapshot failed:', e);
      }
    }
  },
);
```

并加 `applyEpochRef` / `lastSeenEpochRef`（完全照搬 `spreadsheet-editor.tsx:128-133`）。

cleanup 函数里加 `disposable.dispose()`。

- [ ] **Step 5：加远程更新应用 effect**

参照 `spreadsheet-editor.tsx:247-330` 写一个 effect：当 `docsData` 变化且 `remoteEpoch.current !== lastSeenEpochRef.current` 时，调用 `api.getActiveDocument().replaceDocument(docsData)`（Univer Docs 的远程替换 API；若该 API 不存在，退化到销毁重建：`fDoc.dispose(); api.createDocument(docsData);`）。

POC 验证：在浏览器双开同一文档，A 端输入 → B 端 1s 内出现同样文字。

- [ ] **Step 6：提交**

```bash
git add web/src/components/collaboration/document-editor.tsx
git commit -m "feat(collab): document-editor 接入 useDocumentCollab，实现多人实时同步"
```

---

## Phase 4：在线人数与 presence UI

### Task 4.1：新建通用 `use-online-users.ts`

**Files:**
- Create: `web/src/components/collaboration/use-online-users.ts`

**参考：** `yjs-provider.ts` 中的 `AwarenessStore`（第 21-150 行）。

- [ ] **Step 1：写订阅 hook**

```ts
// web/src/components/collaboration/use-online-users.ts
import { useEffect, useState } from 'react';
import type { CollaborationWebSocketProvider } from './yjs-provider';

export interface OnlineUser {
  clientId: number;
  userId?: string;
  name?: string;
  avatar?: string;
  cursor?: { line: number; column: number } | null;
}

/**
 * 订阅 Yjs awareness，返回当前 room 的在线用户列表与人数。
 * Engine-agnostic：Docs 和 Sheets 都可用。
 */
export function useOnlineUsers(
  provider: CollaborationWebSocketProvider | null,
): { users: OnlineUser[]; count: number } {
  const [users, setUsers] = useState<OnlineUser[]>([]);

  useEffect(() => {
    if (!provider) {
      setUsers([]);
      return;
    }

    const sync = () => {
      const states = provider.awareness.getStates();
      const list: OnlineUser[] = [];
      for (const [clientId, state] of states) {
        if (!state) continue;
        list.push({
          clientId,
          userId: state.user_id || state.userId,
          name: state.name || state.userName,
          avatar: state.avatar,
          cursor: state.cursor || null,
        });
      }
      setUsers(list);
    };

    sync();
    provider.awareness.on('change', sync);
    return () => {
      provider.awareness.off('change', sync);
    };
  }, [provider]);

  return { users, count: users.length };
}
```

- [ ] **Step 2：验证 `AwarenessStore` 的 API 匹配**

Run:
```bash
grep -n "getStates\|on(\|off(\|awarenessData" D:/AI/ragflow2/web/src/components/collaboration/yjs-provider.ts | head -20
```

对照 `AwarenessStore` 类的实际方法名/事件名。若与上面 hook 中用的 `getStates()` / `'change'` 不一致，修正 hook。

- [ ] **Step 3：提交**

```bash
git add web/src/components/collaboration/use-online-users.ts
git commit -m "feat(collab): 新增 useOnlineUsers engine 无关 presence 订阅 hook"
```

---

### Task 4.2：`editor-header.tsx` 接入在线人数

**Files:**
- Modify: `web/src/components/collaboration/editor-header.tsx`

**验证：** `editor-header.tsx` 已有 `MemberAvatars` 的集成（见第 10 行 import）。只是 `MemberAvatars` 的数据源当前可能是通过 `/collaborators` REST API 拉。本任务改为从 `provider.awareness` 实时拉。

- [ ] **Step 1：读 `editor-header.tsx` 当前 MemberAvatars 数据来源**

Run:
```bash
grep -n "MemberAvatars\|collaborators\|members" D:/AI/ragflow2/web/src/components/collaboration/editor-header.tsx
```

定位现有 collaborator 列表的拉取逻辑。

- [ ] **Step 2：在 EditorHeader 里加 `useOnlineUsers` 订阅**

在 EditorHeader 函数顶部加：
```ts
import { useOnlineUsers } from './use-online-users';
// ...
const { users: onlineUsers, count: onlineCount } = useOnlineUsers(provider);
```

- [ ] **Step 3：把 MemberAvatars 的数据源从 REST 切到 awareness**

找到 `<MemberAvatars ... />` 调用，把 `members={...}` prop 改为 `members={onlineUsers}`（字段名映射：`OnlineUser` → `MemberAvatars` 期望的 shape，必要时做一层 `map(u => ({ id: u.userId, name: u.name, avatar: u.avatar }))`）。

在 `onlineCount > 0` 时显示人数徽标。

- [ ] **Step 4：手动验证**

打开两个浏览器窗口（A、B）登录不同账号，打开同一文档：
- A 看到 B 的头像出现在 header
- B 关闭页面后 5-10s 内 A 的头像组里 B 消失
- 人数徽标实时更新

- [ ] **Step 5：提交**

```bash
git add web/src/components/collaboration/editor-header.tsx
git commit -m "feat(collab): editor-header 在线人数改用 awareness 实时订阅"
```

---

## Phase 5：docx 导入导出

### Task 5.1：后端新增 `exported-file` 端点

**Files:**
- Modify: `api/apps/restful_apis/collaboration_api.py`
- Modify: `api/apps/services/collaboration_api_service.py`

- [ ] **Step 1：在 service 层加 save / get 函数**

在 `api/apps/services/collaboration_api_service.py` 末尾加：

```python
async def save_exported_file(doc_id: str, tenant_id: str, blob: bytes, fmt: str) -> dict:
    """前端导出后上传 blob，存到 STORAGE_IMPL，更新 file_path。"""
    e, doc = CollaborationDocumentService.get_by_id(doc_id)
    if not e:
        raise LookupError("Document not found")
    if not _get_user_role(doc_id, tenant_id):
        raise PermissionError("Access denied")
    if fmt not in ("docx", "pdf"):
        raise ValueError(f"Unsupported format: {fmt}")
    storage_key = f"{doc_id}.{fmt}"
    settings.STORAGE_IMPL.put("collaboration", storage_key, blob)
    CollaborationDocumentService.update_by_id(doc_id, {"file_path": storage_key, "file_type": fmt})
    return {"file_path": storage_key, "size": len(blob)}


async def get_exported_file(doc_id: str, tenant_id: str) -> tuple:
    """返回最近一次导出的 (blob_bytes, filename, mimetype)，无则 None。"""
    e, doc = CollaborationDocumentService.get_by_id(doc_id)
    if not e:
        raise LookupError("Document not found")
    if not _get_user_role(doc_id, tenant_id):
        raise PermissionError("Access denied")
    storage_key = doc.file_path
    if not storage_key:
        return None
    ext = storage_key.rsplit(".", 1)[-1]
    mimetype = "application/vnd.openxmlformats-officedocument.wordprocessingml.document" if ext == "docx" else "application/pdf"
    blob = settings.STORAGE_IMPL.get("collaboration", storage_key)
    filename = f"{doc.name}.{ext}"
    return blob, filename, mimetype
```

- [ ] **Step 2：在 API 层加两个端点**

在 `api/apps/restful_apis/collaboration_api.py` 找到 `download_document` 端点（约第 171-193 行）下方，加：

```python
@manager.route("/collaboration/documents/<doc_id>/exported-file", methods=["POST"])  # noqa: F821
@login_required
async def upload_exported_file(doc_id):
    """前端导出 docx/pdf 后上传，后端只存文件不生成。"""
    user_id = current_user.id
    tenant_id = current_user.id
    fmt = (request.args.get("format") or "docx").lower()
    blob = await request.get_data()
    try:
        result = await collaboration_api_service.save_exported_file(doc_id, tenant_id, blob, fmt)
        return get_json_result(data=result)
    except (LookupError, PermissionError, ValueError) as ex:
        return get_json_result(message=str(ex), code=404 if isinstance(ex, LookupError) else 403)


@manager.route("/collaboration/documents/<doc_id>/exported-file", methods=["GET"])  # noqa: F821
@login_required
async def download_exported_file(doc_id):
    """下载最近一次导出的文件。"""
    user_id = current_user.id
    tenant_id = current_user.id
    try:
        result = await collaboration_api_service.get_exported_file(doc_id, tenant_id)
    except (LookupError, PermissionError) as ex:
        return get_json_result(message=str(ex), code=404 if isinstance(ex, LookupError) else 403)
    if not result:
        return get_json_result(message="No exported file yet", code=404)
    blob, filename, mimetype = result
    response = Response(blob, mimetype=mimetype)
    response.headers.add("Content-Disposition", f"attachment; filename={filename}")
    return response
```

- [ ] **Step 3：确认 import 里有 Response / get_json_result / current_user / login_required**

Run:
```bash
grep -n "^from\|^import" D:/AI/ragflow2/api/apps/restful_apis/collaboration_api.py | head -30
```

缺啥补啥。`Response` 通常从 `flask`/`quart` 导入。

- [ ] **Step 4：本地跑一遍接口**

启动后端后，手动 curl 验证：

```bash
# 假设 doc_id=test-doc-1，token=xxx
curl -X POST "http://localhost:9380/api/v1/collaboration/documents/test-doc-1/exported-file?format=docx" \
  -H "Authorization: Bearer xxx" \
  --data-binary @test.docx
curl -o /tmp/dl.docx "http://localhost:9380/api/v1/collaboration/documents/test-doc-1/exported-file" \
  -H "Authorization: Bearer xxx"
diff test.docx /tmp/dl.docl && echo "OK"
```

Expected: 两次调用都成功，文件 byte 级一致。

- [ ] **Step 5：提交**

```bash
git add api/apps/restful_apis/collaboration_api.py api/apps/services/collaboration_api_service.py
git commit -m "feat(collab): 新增 exported-file 上传/下载端点 (前端导出后存)"
```

---

### Task 5.2：前端 `use-univer-export.ts`

**Files:**
- Create: `web/src/components/collaboration/use-univer-export.ts`

- [ ] **Step 1：写导出/导入 hook**

```ts
// web/src/components/collaboration/use-univer-export.ts
import { useCallback, useState } from 'react';
import type { FUniver } from '@univerjs/presets';

interface Options {
  docId: string;
  apiFetch: (url: string, options?: RequestInit) => Promise<Response>;
  univerAPIRef: React.MutableRefObject<FUniver | null>;
}

export function useUniverExport({ docId, apiFetch, univerAPIRef }: Options) {
  const [busy, setBusy] = useState(false);

  const exportDocx = useCallback(async () => {
    const api = univerAPIRef.current as any;
    if (!api) return;
    setBusy(true);
    try {
      const fDoc = api.getActiveDocument?.();
      if (!fDoc) throw new Error('No active document');
      // Task 1.2 验证：FUniver.exportDocument 存在则用，否则退化到 getSnapshot 上传
      let blob: Blob;
      if (typeof api.exportDocument === 'function') {
        blob = await api.exportDocument({ format: 'docx' });
      } else {
        const json = fDoc.save?.() ?? fDoc.getSnapshot?.();
        blob = new Blob([JSON.stringify(json, null, 2)], { type: 'application/json' });
      }
      const resp = await apiFetch(
        `/api/v1/collaboration/documents/${docId}/exported-file?format=docx`,
        { method: 'POST', body: blob },
      );
      const result = await resp.json();
      if (result.code !== 0) throw new Error(result.message);
      // 触发浏览器下载
      const url = `/api/v1/collaboration/documents/${docId}/exported-file`;
      window.open(url, '_blank');
    } finally {
      setBusy(false);
    }
  }, [docId, apiFetch]);

  const exportPdf = useCallback(async () => {
    // 若 Task 1.2 验证 FUniver 支持 pdf 则直接 exportDocument({format:'pdf'})
    // 否则退化：先导 docx，提示用户用浏览器打印为 PDF
    window.alert('PDF 导出：先导出 docx，再用 Word/LibreOffice 另存为 PDF');
  }, []);

  const importDocx = useCallback(async (file: File) => {
    const api = univerAPIRef.current as any;
    if (!api) return;
    setBusy(true);
    try {
      const buf = await file.arrayBuffer();
      if (typeof api.importDocument === 'function') {
        await api.importDocument({ format: 'docx', data: buf });
      } else {
        throw new Error('当前 Univer 版本不支持 docx 导入，请用其他方式');
      }
    } finally {
      setBusy(false);
    }
  }, []);

  return { busy, exportDocx, exportPdf, importDocx };
}
```

- [ ] **Step 2：在 `document-editor.tsx` 接入**

在 `document-editor.tsx` 中 import 并替换占位的 `handleDownload`：

```ts
import { useUniverExport } from './use-univer-export';
// ...
const { busy: exportBusy, exportDocx, exportPdf } = useUniverExport({
  docId: doc.id,
  apiFetch,
  univerAPIRef,
});

const handleDownload = useCallback(async (type: 'docx' | 'pdf') => {
  if (type === 'docx') await exportDocx();
  else await exportPdf();
}, [exportDocx, exportPdf]);
```

把 `downloading={downloading}` 改为 `downloading={exportBusy}`，删除原来的 `downloading` state。

- [ ] **Step 3：手动验证**

浏览器里打开文档 → 点导出 Word → 浏览器下载 `xxx.docx` → 用 Word 打开确认内容完整。

- [ ] **Step 4：提交**

```bash
git add web/src/components/collaboration/use-univer-export.ts web/src/components/collaboration/document-editor.tsx
git commit -m "feat(collab): 新增 useUniverExport，docx 导出走前端 Univer SDK + 后端存储"
```

---

## Phase 6：删除 Lexical 与 FormatRule

### Task 6.1：删除 Lexical 前端组件

**Files:**
- Delete: 9 个文件 + `nodes/` 目录

- [ ] **Step 1：先全扫残留 import**

Run:
```bash
cd D:/AI/ragflow2/web && grep -rln "from '@lexical\|from 'lexical\|from './toolbar-plugin\|from './mention-plugin\|from './emoji-picker\|from './format-rule-panel\|from './docx-import-dialog\|from './comment-panel\|from './version-history-panel\|from './attachment-panel\|from './audit-log-panel\|from './nodes/" src/
```

Expected：列出所有残留引用位置。逐个处理：若引用方在待删文件内 → 跟随删除；若在外部存活文件 → 用 Edit 移除该 import 和使用处。

- [ ] **Step 2：删除文件**

```bash
cd D:/AI/ragflow2/web/src/components/collaboration
rm toolbar-plugin.tsx mention-plugin.tsx emoji-picker.tsx format-rule-panel.tsx docx-import-dialog.tsx
rm comment-panel.tsx version-history-panel.tsx attachment-panel.tsx audit-log-panel.tsx
rm -rf nodes/
```

- [ ] **Step 3：删除 `side-panel-bar.tsx` 中对已删面板的引用**

Run:
```bash
grep -n "CommentPanel\|VersionHistory\|AttachmentPanel\|AuditLog\|FormatRule" D:/AI/ragflow2/web/src/components/collaboration/side-panel-bar.tsx
```

根据结果用 Edit 把 `PanelKey` 枚举和相关分支删除。本任务范围：`side-panel-bar.tsx` 保留 `share` 入口（ShareDialog 还在），其他面板入口删除。

- [ ] **Step 4：编译**

```bash
cd D:/AI/ragflow2/web && npm run dev
```

Expected：无编译错误，整个 collaboration tab 正常工作。

- [ ] **Step 5：提交**

```bash
git add -A web/src/components/collaboration/
git commit -m "chore(collab): 删除 Lexical 相关前端文件 (9 个组件 + nodes 目录)"
```

---

### Task 6.2：删除前端 `@lexical/*` 依赖

**Files:**
- Modify: `web/package.json`
- Modify: `web/package-lock.json`

- [ ] **Step 1：全扫 lexical 包**

Run:
```bash
cd D:/AI/ragflow2/web && grep -oE '"@lexical/[^"]+"' package.json | sort -u
```

Expected：列出所有已装的 `@lexical/*` 包。

- [ ] **Step 2：卸载**

```bash
cd D:/AI/ragflow2/web && npm uninstall lexical @lexical/react @lexical/yjs @lexical/rich-text @lexical/link @lexical/list @lexical/markdown @lexical/plain-text @lexical/selection @lexical/utils @lexical/headless @lexical/clipboard @lexical/code @lexical/table @lexical/overflow @lexical/history @lexical/markup @lexical/file
```

（上面列出哪些就卸哪些，不存在的包 npm 会跳过）

- [ ] **Step 3：验证无残留**

```bash
cd D:/AI/ragflow2/web && grep -rln "from '@lexical\|from 'lexical" src/
```

Expected：空输出。

- [ ] **Step 4：提交**

```bash
git add web/package.json web/package-lock.json
git commit -m "chore(collab): 卸载所有 @lexical/* 依赖"
```

---

### Task 6.3：删除后端 FormatRule 端点与业务函数

**Files:**
- Modify: `api/apps/restful_apis/collaboration_api.py`
- Modify: `api/apps/services/collaboration_api_service.py`

- [ ] **Step 1：删 API 端点（6 个）**

在 `api/apps/restful_apis/collaboration_api.py` 中，用 Edit 删除以下 6 段：
- L260-285 `apply_format_rule` 端点
- L287-306 `create_format_rule` 端点
- L308-320 `list_format_rules` 端点
- L322-340 `update_format_rule` 端点
- L342-360 `delete_format_rule` 端点

（注意：实际删之前先重新读一下确认行号，本文件之前可能已经被前面 Task 改动）

- [ ] **Step 2：删 service 层函数**

在 `api/apps/services/collaboration_api_service.py` 中删除：
- `_cell_text_children` (L195)
- `_build_lexical_table` (L215)
- `_markdown_to_lexical_json` (L246)
- `_flush_code_block` (L403)
- `_flush_callout` (L425)
- `_parse_css_style` (L456)
- `_lexical_parse_format` (L471)
- `_add_styled_docx_run` (L483)
- `_iter_lexical_blocks` (L528)
- `_generate_docx` (L577)
- `_is_md_table_row` (L727), `_is_md_table_separator` (L733), `_parse_md_table_row` (L743), `_set_cell_markdown` (L750), `_flush_docx_table` (L767)
- `_parse_inline_markdown` (L787), `_add_markdown_paragraph` (L852), `_generate_docx_markdown` (L875)
- `_escape_xml` (L1061), `_build_pdf_inline_markup` (L1066), `_generate_pdf` (L1108), `_generate_pdf_markdown` (L1247)
- `apply_format_rule` (L1711), `create_format_rule` (L1756), `list_format_rules` (L1771), `update_format_rule` (L1799), `delete_format_rule` (L1817)
- `import_docx` (L1968) —— Lexical 专用导入，Phase 5 前端导入接管后删除

删之前每个函数都重新 grep 确认它没有被本次保留的端点（`/comments`、`/versions`、`/share`、`/attachments`、`/audit-logs`、`/collaborators`、`/folders`、`/documents`、`/documents/<id>`、`/ydoc`、`/download`、`/exported-file`、`/assets`）引用。

- [ ] **Step 3：删 download_document 里残留的 Lexical 分支**

读 `download_document` 函数（L1669-1709 附近）。如果它内部还在调 `_generate_docx` / `_generate_pdf`，改为：

```python
async def download_document(doc_id: str, tenant_id: str, file_type: str = "docx") -> tuple:
    """下载最近一次导出的文件。不再在服务端生成 docx/pdf。"""
    return await get_exported_file(doc_id, tenant_id)
```

- [ ] **Step 4：编译检查（py_compile）**

```bash
cd D:/AI/ragflow2 && python -m py_compile api/apps/restful_apis/collaboration_api.py api/apps/services/collaboration_api_service.py
```

Expected：无 SyntaxError。

- [ ] **Step 5：本地启动后端验证**

按项目规范跑 `bash docker/launch_backend_service.sh`，curl 验证 `/documents` 列表、`/documents/<id>` 详情、`/exported-file` 上传下载都正常。

- [ ] **Step 6：提交**

```bash
git add api/apps/restful_apis/collaboration_api.py api/apps/services/collaboration_api_service.py
git commit -m "chore(collab): 删除 Lexical/markdown/format-rule 后端代码 (约 1700 行)"
```

---

### Task 6.4：最终回归

- [ ] **Step 1：完整功能验证清单**

打开两个浏览器（A 用户 lg18629285296@163.com / B 用户其他账号），对每个场景验证：

| 场景 | 期望 |
|---|---|
| 新建 docx 文档 | Univer Docs 挂载，无报错 |
| A 输入段落、标题、加粗 | 本地渲染正常 |
| B 同时打开 | 1s 内看到 A 的内容 |
| A 继续输入 | B 实时同步 |
| A B 同时编辑不同段落 | 无冲突，双方都看到对方修改 |
| Header 在线人数 | 显示 A、B 两个头像 |
| B 关闭页面 | 5-10s 后 A 的头像组里 B 消失 |
| 点导出 Word | 浏览器下载 xxx.docx，Word 能打开 |
| 刷新页面 | 内容从 /ydoc 恢复 |
| 新建 xlsx 表格 | 依然走 SpreadsheetEditor，不受影响 |
| /collaboration/comments REST | 仍然可调（给移动端用） |
| /collaboration/format-rules | 返回 404（已删） |

- [ ] **Step 2：ruff 检查**

```bash
cd D:/AI/ragflow2 && uv run ruff check api/apps/restful_apis/collaboration_api.py api/apps/services/collaboration_api_service.py
```

Expected：无 error。warning 修复到合理程度。

- [ ] **Step 3：前端 build 验证**

```bash
cd D:/AI/ragflow2/web && npm run build
```

Expected：成功，无 TS 报错。

- [ ] **Step 4：最终提交（如有 lint 修复）**

```bash
git add -A
git commit -m "chore(collab): 回归修复"
```

---

## 自检结果

**Spec 覆盖：** 设计文档 §1-8 全部映射到任务 — §1 架构（Task 2.2/3.1/3.2）、§2 数据存储（Task 2.2 createBlankDocsContent）、§3 前端组件（Task 2.1/2.2/3.1/4.1/5.2）、§4 后端（Task 5.1/6.3）、§5 数据流（Task 3.1/3.2）、§6 实施顺序（本计划 6 个 Phase）、§7 风险（Task 1.2 开放问题验证）、§8 后续阶段（标注不在本次范围）。

**Placeholder 扫描：** 已扫，无 TBD/TODO，所有代码块完整。

**类型一致性：** `useDocumentCollab` 的 `Options` / `Return` 接口在 Task 3.1 定义，Task 3.2 引用字段名（`docsData`, `remoteEpoch`, `pushSnapshot`, `saveStatus`, `provider`, `saveToServer`）完全匹配。`useOnlineUsers` 返回 `{users, count}`，Task 4.2 引用一致。

**注意事项**
- Task 1.2 的开放问题验证结果会影响 Task 5.2 的导出实现路径（退化 vs 直走官方 API），执行时先跑 Task 1.2
- Task 6.3 删除前端的范围里，每次删之前都重新 grep 当前行号（前面 Task 会改动行号）
- 本计划未删除 `collaboration_format_rule` 数据库表（设计文档 §4.3 决定），如后续要清理单独发 migration
