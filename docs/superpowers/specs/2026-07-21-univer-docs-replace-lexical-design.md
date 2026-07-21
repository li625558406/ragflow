# Univer Docs 替换 Lexical 协作文档 — 设计文档

- **日期**: 2026-07-21
- **范围**: C 端协作页签的"文档"（Word 风格富文本）编辑器全量替换
- **目标**: 用 Univer Docs 官方 JS SDK 替换 Lexical，保留并增强多账号实时协同、在线人数显示、内容实时同步；所有 UI 走 Univer 原生；docx 导入导出走官方 JS SDK；复用现有 Yjs + WebSocket + presence 基础设施

---

## 1. 背景与决策

### 1.1 现状

- 前端 `web/src/components/collaboration/` 共 21 文件 ~9000 行
- **文档编辑器**：`document-editor.tsx` (1009L) + `toolbar-plugin.tsx` (1202L) + `nodes/` 基于 Lexical
- **表格编辑器**：`spreadsheet-editor.tsx` (505L) 基于 Univer Sheets，已用 `use-spreadsheet-collab.ts` (785L) 桥接 Yjs + WS
- **WS / Yjs 基础设施**：`yjs-provider.ts` (689L) 自写 Provider，`collaboration_ws.py` (350L) Quart WS 盲中继，已有 presence 广播、30s 自动保存、StrictMode safe、visibilitychange flush
- 后端 `collaboration_api_service.py` (2694L) 含 Lexical↔markdown 转换、python-docx 生成、format rule 业务

### 1.2 关键决策（用户已确认）

| 主题 | 决策 |
|---|---|
| 历史 Lexical 文档数据 | 直接弃用（开发环境数据），无需迁移 |
| docx 导入导出 | 纯 Univer 官方 JS SDK（浏览器端跑），后端只存文件 |
| 格式规则系统 (FormatRule) | 全部删除（前后端、DB 表暂保留不迁移） |
| 侧边面板（评论/版本/附件/审计/分享） | 全部换 Univer 原生 UI，现有前端面板删除，后端 REST 端点保留给移动端/API |
| 协同架构 | 方案 A 桥接模式：照搬表格 `use-spreadsheet-collab.ts` 范式，复用现有 Yjs Provider、WS 盲中继、presence |
| 后续阶段 | 在线人数、presence、保存状态等通用件也适配到表格 |

---

## 2. 架构总览

```
┌─ 前端 web/src/components/collaboration/ ─────────────────────┐
│                                                              │
│  DocumentEditor (重写, Univer Docs preset)                   │
│      │                                                       │
│      ▼                                                       │
│  useDocumentCollab (新增) ── 照搬 useSpreadsheetCollab 范式  │
│      │                                                       │
│      ▼                                                       │
│  CollaborationWebSocketProvider (现有, 不改)                 │
│      │ Y.Doc (state + awareness)                             │
└──────┼───────────────────────────────────────────────────────┘
       │  WebSocket (blind relay JSON messages)
       ▼
┌─ 后端 api/apps/ ─────────────────────────────────────────────┐
│  collaboration_ws.py (现有 350L, 不改)                        │
│    - room {doc_id: clients{}} 维护在线列表                    │
│    - presence 广播（在线账号 id/name/avatar）                 │
│    - update / save / awareness 消息盲中继                     │
│                                                              │
│  collaboration_api.py (裁剪: 删 apply-rule + 5 个 format-rules)│
│  collaboration_api_service.py (2694L → 预估 ~900L)             │
│    - 删除 Lexical↔markdown、python-docx 生成、format rule     │
│    - 新增 exported-file 上传/下载端点                         │
└──────────────────────────────────────────────────────────────┘
```

### 2.1 数据存储

- `collaboration_document.content`：存 Univer Docs 原生 JSON（含 `{document: true, ...}`）
- `collaboration_document.markdown_content`：保留字段但标 deprecated，仅导入时存原始文本留档，不参与渲染
- `collaboration_document.ydoc`：保留，存 Yjs 二进制快照（30s 自动保存不变）
- `collaboration_document.file_path`：保留，存最近一次导出的 docx/pdf 的 storage key
- 不新增表，不做数据迁移（开发数据可弃）

### 2.2 新增依赖

- 前端 `web/package.json`：
  - `@univerjs/preset-docs-core` + `zh-CN` locale
  - `@univerjs/preset-docs-drawing` + `zh-CN` locale
  - `@univerjs/preset-docs-thread-comment` + `zh-CN` locale
- 前端删除：`lexical`、`@lexical/react`、`@lexical/yjs`、`@lexical/rich-text`、`@lexical/link`、`@lexical/list`、`@lexical/markdown` 等所有 `@lexical/*`

---

## 3. 前端组件清单

### 3.1 新增

| 文件 | 行数预估 | 职责 |
|---|---|---|
| `document-editor.tsx` (重写) | ~350 | 挂载 Univer Docs（createUniver + Docs presets），接管文档生命周期、command 监听、卸载释放 |
| `use-document-collab.ts` | ~600 | Univer Docs JSON ↔ Yjs 快照同步、debounce + epoch gate、30s 自动保存、visibilitychange/pagehide flush、StrictMode safe、presence.setLocalState |
| `univer-docs-presets.ts` | ~60 | 集中导出 Docs presets + zh-CN locale |
| `use-online-users.ts` | ~80 | 订阅 `provider.awareness`，返回 `onlineUsers[]` + `onlineCount`，**engine 无关，Docs/Sheets 共用** |
| `editor-header.tsx` (重写) | ~180 | 在线人数头像组、保存状态、分享按钮、导出/导入按钮；接 `useOnlineUsers` |
| `use-univer-export.ts` | ~150 | `exportDocx()`、`exportPdf()`、`importDocx(file)`：调 `FUniver` API + 上传/下载端点 |

### 3.2 删除（Lexical 相关全量）

- `toolbar-plugin.tsx` (1202L)
- `mention-plugin.tsx` (285L)
- `emoji-picker.tsx` (266L)
- `format-rule-panel.tsx` (768L)
- `docx-import-dialog.tsx` (135L)
- `comment-panel.tsx` (410L)
- `version-history-panel.tsx` (150L)
- `attachment-panel.tsx` (202L)
- `audit-log-panel.tsx` (165L)
- `nodes/` 目录

### 3.3 保留不动

- `document-list.tsx` (466L)、`folder-tree.tsx` (463L)、`create-document-dialog.tsx` (157L)
- `share-dialog.tsx` (520L)、`side-panel-bar.tsx` (139L)、`member-avatars.tsx` (75L)
- `index.tsx` (333L) — CollaborationPanel 入口，按 `content.document === true` 分发到 Docs，否则 Sheets
- `spreadsheet-editor.tsx`、`use-spreadsheet-collab.ts`、`yjs-provider.ts` 全保留

---

## 4. 后端裁剪与新增

### 4.1 `collaboration_api.py` 端点变化

| 端点 | 动作 |
|---|---|
| `/documents` POST/GET、`/documents/<id>` GET/PUT/DELETE | 保留，content 字段语义改为 Univer Docs JSON |
| `/documents/spreadsheet` POST | 保留 |
| `/documents/<id>/ydoc` PUT | **保留不改**，blind 存 BlobField |
| `/documents/<id>/download` GET | 保留，简化为返回最近一次导出文件，无则 404 |
| `/documents/<id>/assets/<asset_id>` GET | 保留（Univer Drawing 图片资源） |
| `/documents/import` POST | 保留，前端转完 Univer JSON 再 PUT |
| `/documents/<id>/exported-file` POST | **新增**：前端导出后上传 Blob，后端存 STORAGE_IMPL |
| `/documents/<id>/exported-file` GET | **新增**：下载已存的导出文件 |
| `/documents/<id>/apply-rule` POST | **删除** |
| `/format-rules` POST/GET | **删除** |
| `/format-rules/<id>` PUT/DELETE | **删除** |
| `/comments`、`/versions`、`/share`、`/attachments`、`/audit-logs`、`/collaborators`、`/folders` | **全保留**（移动端/API 复用） |

### 4.2 `collaboration_api_service.py` 瘦身（2694L → 预估 ~900L）

删除：
- `_generate_docx()`、`_generate_pdf()` 全部
- `apply_format_rule()` 及 FormatRule CRUD 业务
- `_lexical_to_markdown()`、`_markdown_to_lexical()` 及所有 markdown 中转
- 所有 `_bid_format_*` 字号/字体/颜色辅助函数

新增：
- `save_exported_file(doc_id, blob, fmt)` — 存 STORAGE_IMPL，更新 `file_path`
- `get_exported_file(doc_id)` — 读 STORAGE_IMPL 返回 Blob

后端不再做任何 docx/pdf 生成，Univer 只在浏览器跑。

### 4.3 数据库

- `collaboration_format_rule` 表本次不删（避免迁移风险），标注"后续清理"
- `markdown_content` 字段保留但标 deprecated

---

## 5. 数据流与并发安全

### 5.1 实时编辑数据流（照搬表格已验证模式）

```
本地按键 → Univer Docs command 执行
         → onCommandExecuted 监听 (origin !== 'ws-remote')
         → debounce 300ms
         → FUniver.saveDocument() 取 JSON 快照
         → epoch++ (丢弃过期回调)
         → Y.Doc.transact(() => yMap.set('content', json))
         → provider 触发 WS 广播 update bytes

服务端 collaboration_ws.py 盲中继 → 其他客户端
         → CollaborationWebSocketProvider 收到 update
         → Y.applyUpdate(doc, bytes, 'ws-remote')
         → yMap.observe 触发
         → 比较 epoch，落后则丢弃
         → FUniver.applyDocumentData(json) (origin='ws-remote')
         → Univer 重渲染
```

### 5.2 Presence / 在线人数

```
连接建立 → awareness.setLocalState({user_id, name, avatar, cursor})
         → WS 广播 awareness 消息

服务端 _build_presence() 返回 room 内所有 clients
       → _broadcast_presence() 推给所有客户端

前端 useOnlineUsers(provider) 订阅 awareness 'change'
       → 返回 onlineUsers[] + onlineCount
       → editor-header.tsx 头像组渲染
```

### 5.3 保存策略（完全继承表格）

- 防抖：本地编辑后 30s 自动 `PUT /ydoc` 存二进制快照
- visibilitychange=hidden、pagehide、组件 unmount 强制 flush
- yDoc 初始挂载：从 `/ydoc` 拉二进制 → `Y.applyUpdate(yDoc, bytes, 'ws-init')`
- StrictMode safe：用 `ws_uid = uuid()` 而非 `id(websocket)`

### 5.4 冲突处理

- Yjs CRDT 本身无冲突合并
- debounce + epoch gate 保证只应用最新远端快照，过期回调丢弃
- 子节点 `_collabNode` 残留：复用 `yjs-provider.ts:218-229` 已有清理逻辑

---

## 6. 实施顺序

每阶段独立验证，单独提交 commit 便于回滚。

1. **依赖安装** — 加 Docs presets 包，跑 `console.log(FUniver)` 验证导出 API 存在
2. **document-editor.tsx 重写** — 仅本地渲染，能加载/保存 Univer Docs JSON 到 `content` 字段
3. **use-document-collab.ts** — 接 Yjs + WS，单人多端打开验证实时同步
4. **use-online-users.ts + editor-header.tsx** — 在线人数、光标、保存状态 UI
5. **use-univer-export.ts** — docx 导入导出按钮、后端新端点 `exported-file`
6. **大扫除** — 删除清单全量执行，跑完整回归

---

## 7. 风险与开放问题

### 7.1 已识别风险

| # | 风险 | 影响 | 缓解 |
|---|---|---|---|
| 1 | Univer Docs preset 在桥接模式下多人光标/选择不如官方 collab 顺滑 | 体验 | POC 阶段先验证 2 人同时编辑段落；不行再评估切官方 collab plugin |
| 2 | `FUniver.exportDocument({format:'docx'})` 是否存在于社区版 SDK | docx 导出可能要 pro 授权 | 阶段 1 依赖安装时跑 `console.log(FUniver)` 确认；若无，退化到浏览器 html→docx |
| 3 | Docs/Sheets JSON 结构不同，index.tsx 分发要正确 | 文档打不开 | 靠 `content.document === true` 判断 Docs；fallback Sheets |
| 4 | `use-spreadsheet-collab.ts` 里部分逻辑是 Sheets 专属 | 抽公用件时漏边界 | 先复制一份改 Docs，跑通后再回头抽公用 hook（YAGNI） |
| 5 | StrictMode 双挂载导致 provider 重复连接 | 在线人数翻倍 | 现有 `yjs-provider.ts` 已用 `ws_uid` 解决，直接继承 |
| 6 | 删除 `@lexical/*` 后残留 import 导致编译失败 | 构建红 | 删除阶段 grep 全扫 `from '@lexical` 和 `from 'lexical'` |
| 7 | `collaboration_format_rule` 表残留 | 历史包袱 | 本次不删表，文档标注"后续清理" |
| 8 | Univer Docs 的斜杠命令、@提及、emoji 是否原生支持 | UI 体验 | 阶段 1 挂上 preset 后逐个验证；缺失的接受暂时不要 |

### 7.2 开放问题（POC 阶段解决，不阻塞设计）

1. Univer Docs 是否支持从 docx 直接导入？（影响 `/import` 实现）
2. PDF 导出官方 SDK 是否直接支持？（若不支持走 docx→libreoffice 退化）
3. Univer Docs JSON 的体积上限？（长文档可能 Yjs 同步慢）

---

## 8. 后续阶段（本次不做，文档记录）

- 在线人数、光标 presence、保存状态等通用 hook 适配到表格 `spreadsheet-editor.tsx`
- 抽公用 `use-collab-common.ts`，Docs/Sheets 共享 epoch gate、debounce、auto-save、visibilitychange flush
- 删除 `collaboration_format_rule` 表的 migration
- 评估是否升级到 Univer 官方 collab plugin（替换自写 Yjs Provider）
