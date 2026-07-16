# 协作页面布局重设计 — 设计文档

> 日期：2026-07-16
> 范围：C端协作页签（`web/src/components/collaboration/`）整体三栏布局重构
> 前置：Phase 1-4 协作功能已全部集成（文件夹/ACL/评论/@提及/Yjs协同/附件/审计/分享链接/Word导入）

## 背景与问题

现有布局是功能逐步堆叠的结果，存在以下问题：

1. 编辑器头部 9+ 个文字按钮平铺（保存/版本/恢复/评论/附件/审计日志/分享/头像/.docx/.pdf），无图标无分组，窄窗溢出
2. 评论/附件/审计三个右侧面板可同时打开，互相挤压编辑区
3. 版本历史只有头部 `v3 恢复` 小字按钮，无版本列表面板
4. 分享入口分裂：列表项"邀请协作者"(ACL) 与编辑器头部"分享链接"是两个弹窗
5. 左侧列表混合了文件夹树 + 格式规则面板（761行）
6. `document-editor.tsx` 已 890 行，头部/面板/编辑器逻辑耦合
7. 工具栏 ~30 个按钮平铺（`toolbar-plugin.tsx` 1236行）

## 目标布局

```
┌────────────────────────────────────────────────────────────────────┐
│ 📄 文档名          已保存 · v3    👤👤👤  [分享]  [⋯]                │ ← editor-header (极简)
├────────────────────────────────────────────────────────────────────┤
│ 段落▾ │ 字体▾ 字号▾ │ B I U S x² x₂ │ 🎨 │ ≡▾ 行距▾ │ ➕插入▾ │ 🧹  │ ← 分组工具栏
├─────────┬──────────────────────────────────────┬──────────┬───┤
│ 🔍 搜索  │                                      │          │ 💬 │
│ 📁 树    │         编辑区 (白纸卡片)              │  活动面板 │ 📎 │
│ 📄 文档  │                                      │  (互斥)   │ 🕐 │
│ [+新建▾] │                                      │          │ ✨ │
│         │                                      │          │ 📋 │
└─────────┴──────────────────────────────────────┴──────────┴───┘
  左栏(固定)         编辑区(flex-1)               面板(288px) 图标栏(40px)
```

### 四区域职责

| 区域 | 内容 |
|---|---|
| 左栏 | 搜索框（前端过滤文档名）+ 文件夹树/文档列表 + 底部"+ 新建"下拉（新建文档/新建文件夹/导入 Word）。格式规则移出 |
| 头部 | 文档名（点击重命名）、保存状态+版本号小字、在线成员头像、主按钮"分享"、"⋯"菜单（导出 docx/pdf、手动保存） |
| 工具栏 | 6 组：段落样式下拉 \| 字体字号 \| 行内样式 \| 颜色 \| 对齐行距 \| 插入下拉（表格/图片/链接/代码块/公式/Callout/Emoji/引用/待办）+ 清除格式 |
| 右侧图标栏 | 5 个图标：评论/附件/版本历史/格式规则/审计日志（仅 owner），互斥展开单面板，宽 288px，图标栏常驻 40px |

### 分享合并

单入口"分享"按钮 → 双 Tab 弹窗：**协作者**（ACL 角色管理，原 share-dialog）+ **公开链接**（原 share-link-dialog，含密码/过期）。

## 组件结构（方案 B：抽壳重构）

| 文件 | 操作 | 职责 |
|---|---|---|
| `index.tsx` | 改 | 三栏容器；持有 `activePanel` 状态；格式规则应用链路保留 |
| `document-list.tsx` | 改 | 移除 FormatRulePanel；加搜索框；底部"+ 新建"下拉 |
| `document-editor.tsx` | 瘦身 | 只保留 LexicalComposer + 插件 + 编辑区（~500行） |
| `editor-header.tsx` | 新建 | 极简头部；接收 saveStatus/version/provider props |
| `side-panel-bar.tsx` | 新建 | 图标栏 + 面板容器；面板注册表 `{key, icon, title, visible?(role), render(props)}` |
| `version-history-panel.tsx` | 新建 | `GET /versions` 列表 + 恢复（二次确认）→ `POST /versions/<v>/restore` |
| `share-dialog.tsx` | 改造 | 双 Tab；吸收 share-link-dialog 后删除该文件 |
| `toolbar-plugin.tsx` | 改 | 6 组重排 + 插入下拉（仅移动按钮位置，不改行为） |
| `comment/attachment/audit/format-rule-panel` | 微调 | 去掉各自定位外壳，改纯内容组件，壳由 SidePanelBar 统一提供 |

## 状态设计

```
index.tsx
├── documents / folders / selectedDoc      (现有)
├── activePanel: 'comments'|'attachments'|'versions'|'formatRules'|'audit'|null  (新)
├── shareTarget                             (现有，弹窗改双Tab)
└── applyingRule / appliedRuleConfig        (现有)

document-editor.tsx
├── saveStatus / version → props 上报 editor-header
└── collabProvider → header 的 MemberAvatars
```

- `activePanel` 放 `index.tsx`：格式规则面板需调用 index 层的 `handleApplyFormatRule`；切换文档时面板保持打开
- 审计面板 `visible: (role) => role === 'owner'`，角色来自现有 ACL 接口
- viewer 只读：工具栏隐藏（现状逻辑），图标栏保留评论/附件/版本只读展示

## 交互细节

| 场景 | 行为 |
|---|---|
| 未选中文档 | 图标栏隐藏，显示空状态 |
| 切换文档 | activePanel 保持，面板内容随 docId 重载 |
| 窄窗 (<1280px) | 面板压缩编辑区可接受；工具栏溢出组收进"⋯"溢出菜单 |
| 版本恢复 | 二次确认 → 编辑器重挂载（key 变化）+ toast；Yjs 在线时广播新状态 |
| 导入 Word | 左栏"+ 新建"下拉进入，成功后刷新并自动选中 |
| 保存状态 | 四态小字：编辑中(灰)/保存中(琥珀)/已保存(绿)/失败(红，点击重试) |

## 不做（YAGNI）

- 面板/左栏宽度拖拽、面板浮动模式
- 后端 API 改动、Yjs 协议与 yjs-provider.ts 改动
- `src/components/ui/` 共享库改动（新壳组件全部在 collaboration 目录内）
- 移动端响应式

## 回归验证

1. Yjs 协同：双浏览器编辑/光标/头像不回归（YjsPlugin 挂载位置不变）
2. 格式规则：面板移位后 `handleApplyFormatRule → FormatApplyPlugin` 链路正常
3. 分享：ACL 增删改 + 链接创建/密码/删除在双 Tab 内可用
4. 导出：⋯菜单 docx/pdf 下载正常
5. `npm run build` 通过
6. 仅本地开发验证，不部署服务器
