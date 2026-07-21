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

---

## 9. 需求功能点总结（实施完成清单）

本次需求围绕 **C 端协作页签 Word 风格文档编辑器** 从 Lexical 全量替换为 Univer Docs 官方 JS SDK，并保证功能不丢失。按"功能点 → 当前状态 → 验证位置"组织，便于回归对照。

### 9.1 核心编辑能力

| # | 功能点 | 实现状态 | 实现位置 |
|---|---|---|---|
| F1 | Univer Docs 富文本编辑（段落/标题/列表/加粗斜体等） | ✅ 已完成 | `document-editor.tsx` + `univer-docs-presets.ts`（DocsCorePreset） |
| F2 | Drawing 能力（插入图片/浮动元素） | ✅ 已完成 | `univer-docs-presets.ts`（DocsDrawingPreset） |
| F3 | Thread Comment 现代批注 | ✅ 已完成 | `univer-docs-presets.ts`（DocsThreadCommentPreset） |
| F4 | zh-CN 本地化 | ✅ 已完成 | `DOCS_LOCALES` 合集导出 |
| F5 | Univer Docs JSON 结构持久化到 `collaboration_document.content` | ✅ 已完成 | `create_document` 服务端默认结构 + hook 初始化校验 `content.document === true` |

### 9.2 多账号实时协同

| # | 功能点 | 实现状态 | 实现位置 |
|---|---|---|---|
| F6 | 多账号 CRDT 实时同步（无冲突合并） | ✅ 已完成 | `use-document-collab.ts` 照搬表格范式，Yjs + WebSocket 盲中继 |
| F7 | 本地编辑 → debounce 300ms → Yjs → WS 广播 → 远端 apply | ✅ 已完成 | `pushSnapshot` + `yMap.observe` + `useEffect` apply 路径 |
| F8 | epoch gate 防回环（本地/远端编辑区分） | ✅ 已完成 | `LOCAL_PUSH_ORIGIN` + `remoteEpochRef` + `lastSeenEpochRef` + `applyEpochRef` |
| F9 | StrictMode safe（WS uid 用 UUID） | ✅ 已继承 | `yjs-provider.ts` 未改动 |
| F10 | 协同模式下 30s 自动保存 `PUT /ydoc` | ✅ 已完成 | `saveTimerRef` setInterval |
| F11 | 5s debounce 落库（避免丢 30s 编辑） | ✅ 已完成 | `saveDebounceRef` |
| F12 | visibilitychange=hidden / pagehide / unmount 强制 flush | ✅ 已完成 | `flushSave` + 事件监听 |
| F13 | 首次连接从 server ydoc 恢复状态 | ✅ 已完成 | `base64ToUint8Array` + `Y.applyUpdate(yDoc, bytes, 'ws-init')` |

### 9.3 在线人数 / Presence

| # | 功能点 | 实现状态 | 实现位置 |
|---|---|---|---|
| F14 | 实时在线用户列表（engine-agnostic，Docs/Sheets 共用） | ✅ 已完成 | `use-online-users.ts` 订阅 `awareness.on('update')` |
| F15 | 头像组 UI 渲染在线人数 | ✅ 已完成 | `member-avatars.tsx` 改用 `useOnlineUsers` hook |
| F16 | awareness.setLocalState（name/color/anchorPos） | ✅ 已完成 | hook 内固定 color `#958DF1`，name 从 `storage.getUserInfoObject()` |

### 9.4 导入导出

| # | 功能点 | 实现状态 | 实现位置 |
|---|---|---|---|
| F17 | 导出 docx：浏览器跑 `FUniver.exportDocument` → POST blob 到后端 → 浏览器下载 | ✅ 已完成 | `use-univer-export.ts:exportDocx` + `POST /exported-file?format=docx` |
| F18 | 导出 docx 退化：SDK 不支持时上传 JSON 快照 | ✅ 已完成 | `use-univer-export.ts:40-50` |
| F19 | 导出 PDF：SDK 直出失败时弹窗提示用 Word/LibreOffice 转换 | ✅ 已完成 | `use-univer-export.ts:exportPdf` |
| F20 | 后端 `save_exported_file` / `get_exported_file` | ✅ 已完成 | `collaboration_api_service.py:1136-1175` + REST 端点 |
| F21 | 导入 Word（从 DocumentList "导入 Word" 按钮） | ❌ **已移除** | 按用户决策："Drop historical data + 全量替换" |
| F22 | 导入 Excel（从 DocumentList "导入 Excel" 按钮） | ❌ **已移除** | 保留后端 `/documents/import` 端点，前端按钮移除 |

### 9.5 删除/裁剪项

| # | 功能点 | 实施状态 | 说明 |
|---|---|---|---|
| F23 | 删除 Lexical 前端代码（toolbar/nodes/mention/emoji/comment/version/attachment/audit/format-rule-panel/docx-import-dialog） | ✅ 已完成 | 9 个文件全量删除 |
| F24 | 删除 FormatRule 系统（前后端 + REST 端点 apply-rule、format-rules CRUD） | ✅ 已完成 | 后端 5 个端点删除，服务函数删除 |
| F25 | 删除 Lexical↔markdown 转换、python-docx 生成、format rule 业务 | ✅ 已完成 | `collaboration_api_service.py` 瘦身（2694L → ~1175L） |
| F26 | 删除 SidePanelBar 侧边面板挂载（评论/版本/附件/审计） | ✅ 已完成 | `index.tsx` 清理 + `collabProvider` 状态移除 |
| F27 | 卸载 `@lexical/*` 依赖 | ⚠️ **未卸载** | `agent/form/components/prompt-editor/` 仍在用 Lexical（Agent 画布的提示词编辑器），无法卸载 |
| F28 | 删除 `collaboration_format_rule` DB 表 | ❌ **本次不做** | 避免迁移风险，标注"后续清理" |

### 9.6 保留项（不动）

| # | 功能点 | 说明 |
|---|---|---|
| F29 | `collaboration_ws.py`（WS 盲中继 350L） | 完全不改，Docs/Sheets 共用 |
| F30 | `yjs-provider.ts`（自写 Provider 689L） | 完全不改 |
| F31 | `spreadsheet-editor.tsx` + `use-spreadsheet-collab.ts` | 表格侧不动，后续阶段再适配通用 hook |
| F32 | 后端 ACL / Collaborator / Share / Folder / Audit Log 端点 | 全保留（移动端/API 复用） |
| F33 | `collaboration_document` 表结构 | 不新增字段，`markdown_content` 标 deprecated |

### 9.7 已知缺陷与待修复（来自对抗式代码审查 2026-07-21）

> 详见 `docs/superpowers/plans/2026-07-21-univer-docs-replace-lexical.md` 审查报告小节。摘要：

| # | 缺陷 | 严重度 | 修复优先级 |
|---|---|---|---|
| B1 | `download_document` 不再生成 xlsx → spreadsheet 下载链路整体失效 | CRITICAL | P0 |
| B2 | `save_exported_file` 越权覆写 `file_type`（xlsx → docx） | CRITICAL | P0 |
| B3 | exported-file 上传无大小/类型校验 → 内存 OOM 风险 | CRITICAL | P0 |
| B4 | Docs `getLatestSnapshot` 未调 `endEditingAsync` → 边打字边保存丢字 | HIGH | P1 |
| B5 | applyEpoch 用 `queueMicrotask` 重置过早 → 远端同步可能 echo | HIGH | P1 |
| B6 | `replaceDocument` API 存在性未验证 → 退化路径 dispose 坑 | HIGH | P1 |
| B7 | 旧 Lexical 文档静默丢内容（检测到非 Docs 结构直接空白） | HIGH | P1 |

---

## 10. 验收用例（最小测试单元）

每个功能点至少一条手工验证步骤，用于回归。

| 用例 | 步骤 | 预期 | 关联功能点 |
|---|---|---|---|
| TC-01 | 打开任一 Docs 文档 → 输入文字、加粗、改段落样式 | 实时生效，工具栏可用 | F1-F4 |
| TC-02 | 两账号同时打开同一 Docs → A 输入文字 | B 端 1s 内看到，A 端不抖动 | F6-F8 |
| TC-03 | 修改后等 5s / 30s → 刷新页面 → 内容仍在 | 内容不丢 | F10-F13 |
| TC-04 | 切换浏览器 Tab 再回来 → 检查 DB ydoc 字段 | 切走时已 flush | F12 |
| TC-05 | 两账号同时打开 → 看右上角头像组 | 显示双方头像 + 在线人数一致 | F14-F16 |
| TC-06 | 点击导出 Word → 浏览器下载 .docx | 文件能在 Word 打开 | F17 |
| TC-07 | 点击导出 PDF → 若 SDK 不支持应弹提示 | 不报错卡死 | F19 |
| TC-08 | 打开 spreadsheet 文档 → 点下载 xlsx | **当前会失败（B1）** | 回归用 |
| TC-09 | 边打字边点手动保存 → 刷新 | **当前会丢最后一段（B4）** | 回归用 |
| TC-10 | 打开 v0.25.1 之前创建的 Lexical 文档 | **当前静默空白（B7）** | 回归用 |
