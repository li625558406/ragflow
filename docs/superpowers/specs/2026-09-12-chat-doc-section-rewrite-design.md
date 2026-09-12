# C端对话文档局部重写（按节 LLM 重写）设计

日期：2026-09-12 ｜ 状态：已评审 ｜ 关联：2026-09-07-template-fill-design.md（成稿链路）、2026-09-08-template-fill-flow-design.md（flow 版本注入）、2026-09-09-flow-finished-manage（FlowVersion 体系）

## 1. 背景与问题

范本填写成稿（docx）产出后，用户常需要「把某一节重写」——LLM 一次生成的整稿总有局部不满意。现有能力缺口：

1. **执行层缺失**：`docx_utils` 有跨 run 保格式替换（占位符粒度），但没有「段落区间替换 + 多段插入 + 样式拷贝」能力；executor 是模板填写语义，无「输入 docx + 节区间 + 指令 → 改写后 docx」任务类型。
2. **定位层缺失**：`docx_utils` addr 协议只到段落/单元格，无 heading 分节；「第 N 节」无法解析。
3. **上下文断裂（最大缺口）**：对话画布拿不到上一轮成稿——completions payload 无字段、message JSON 不存 downloads（`message.py:115-119` 被抽空）、前端刷新后成稿卡消失（`index.tsx:1048-1059` 不恢复 downloads）。

## 2. 目标 / 非目标

**目标**
1. C端对话说「把第 3 节重写，重点补充 XX」→ LLM 重写该节 → 生成新版本 → 对话出成稿卡，格式保真。
2. 操作对象：①对话范本填写成稿卡；②流程页签文档版本（flow AI 面板同链路）。
3. 版本化：每次重写落新版本，可对话回退（复制式 append-only）。
4. 修复存量缺口：downloads 持久化进 message data + 前端历史恢复，否则重写无据可依。

**非目标**
- xlsx（「节」概念不成立，区域改写交互另立项）；
- 表格内容重写（节内表格原样保留）；
- UI 版本列表页（版本操作走对话）；
- 流式进度 / 后台任务 / 断连重连（单节重写秒级同步完成，断了无副作用重发即可）；
- 多文档批量重写。

## 3. 架构与数据流

```
用户：「把第3节重写，补充进度安排」
  └─ C端对话画布 Agent 节点（现有，DSL 零改动）→ function calling 调 DocumentRewrite 工具
       ├─ action=outline：取 blob → heading 切节 → 返回带编号目录（LLM 解析「第3节」；歧义反问）
       ├─ action=rewrite(section_no, instruction)：
       │    该节全文 + 全文目录 + 前后节标题 → LLM 重写（JSON 段落数组）
       │    → python-docx 段落区间替换（顶层标题不动、仅正文、样式拷贝、表格保留）
       │    → 落新版本 → 返回说明文本 + download 契约对象
       ├─ action=versions：列版本链
       └─ action=rollback(version_no)：历史版复制为新 version_no
  └─ Message 节点识别 download 对象 → 前端成稿卡（预览/下载走新版本 doc_id）
```

**上下文传递**：
- 前端 completions payload 新增 `recent_downloads: [{doc_id, filename}]`（当前会话最近 2 张成稿卡）；
- flow AI 面板请求附带 `flow_version_id`（当前版本文档即重写对象）；
- canvas 侧两者注入 sys 变量，工具从 sys 读取——LLM 无需记忆 doc_id。

**组件边界**：
| 组件 | 职责 |
|---|---|
| `agent/tools/document_rewrite.py` | 工具壳：action 分发、参数校验、download 契约组装、owner 校验入口 |
| `rag/svr/document_rewrite/sections.py` | heading 切节、目录提取（纯函数，python-docx） |
| `rag/svr/document_rewrite/rewriter.py` | LLM 重写（LLMBundle 聊天模型，与范本产值同源）、JSON 校验兜底 |
| `rag/svr/document_rewrite/docx_edit.py` | 段落区间替换、多段插入+样式拷贝、表格跳过 |
| `rag/svr/document_rewrite/versions.py` | `doc_rewrite_version` 表读写、并发取号、MinIO 落盘 |
| canvas_service / message 落库 | downloads 存入 message data（存量缺口修复） |
| 前端 use-send-message / index.tsx | payload 附 recent_downloads；历史消息恢复 downloads 渲染卡片 |

执行层（rag/svr/document_rewrite/）不依赖 agent 运行时，可独立测试；flow 场景复用同一执行层，仅版本落库分支不同。

## 4. 分节定位协议（sections.py）

- python-docx 遍历 body，按 **heading 1**（样式名 `Heading 1`/`标题 1` 及大纲级别 0）切顶层节；heading 2/3 跟随所属顶层节。
- 每节结构：`{section_no, title, para_start, para_end, preview(首行40字), word_count}`——编号即文档顺序，与 Word 自动编号无关。
- **无任何 heading 的文档**：明确报错，LLM 引导「该文档无章节结构，建议全文重新生成」。
- 「把实施方案那节重写」这类标题名指令：Agent LLM 对照 outline 自行解析；模糊时反问用户，工具不瞎猜。

## 5. LLM 重写（rewriter.py）

- **输入**：用户指令 + 目标节全文 + 全文目录（每节标题）+ 前后各节标题。不投喂全文正文（token 压力与跑题风险）。
- **输出约束**：JSON `{"paragraphs": ["新段落1", ...]}`——纯段落文本，LLM 不产格式标记。
- **代码端校验**：非法 JSON/空段落重试 1 次 → 仍失败报「重写失败，请重试」，不产生版本；段数 1-50、单段 ≤2000 字（超出截断告警）；投喂正文 >3 万字报「该节过长，建议拆分」。
- 模型：走既有 LLMBundle 聊天模型配置。

## 6. docx 回写与格式保真（docx_edit.py）

- 定位 `para_start..para_end`（**顶层节标题段落跳过，永不改写**）。
- 删除区间旧段落 → 逐条 `insert_paragraph_before` 插入新段落：
  - `pPr` 拷贝自首个被删段落（样式/缩进/行距/对齐）；
  - run `rPr` 拷贝自被删段落「正文特征 run」（跳过加粗标题 run）。
- 支持 1 段→N 段、N 段→1 段。
- **表格策略**：区间内 `w:tbl` 原样保留不删；表格前后正文段落照常替换。
- 每次重写基于**当前版 blob** 重新解析切节，不跨版本缓存段落索引（防漂移）。
- 原对象永不变：新 blob 写 MinIO → 新版本行。

## 7. 版本管理（versions.py + 新表 doc_rewrite_version）

| 字段 | 说明 |
|---|---|
| id / tenant_id / create_time | 常规 |
| root_id | 版本链锚：chat=原成稿 task_id；flow=flow_id |
| version_no | 链内递增（1=原始成稿登记；2+=每次重写/回退） |
| source_type | `chat_fill` / `flow_version` / `rewrite` / `rollback` |
| bucket / obj / file_name / file_type | MinIO 定位 + 展示名（带 v{n}） |
| instruction / section_title | 触发指令 + 重写节标题（审计与续改上下文） |
| created_by | owner 校验用 |

- **`(root_id, version_no)` 唯一索引 + 事务取号**，并发重写撞线者重试。
- **回退 = 复制式**：历史版内容登记为新 version_no；「当前版」恒为 max(version_no)，无指针字段。
- chat 场景：对象写 `{tenant_id}-downloads` 桶（`FileService.put_blob`），download 卡直接可用。
- flow 场景：`FlowVersionService.add_version`（新 source 枚举 `ai_rewrite`），**不自动切 `current_version_id`**（流程状态机由流程操作控制）；对话内以新版卡片为准，流程页签可查。
- 首次对某成稿重写时登记 version_no=1（原始成稿），保证链完整可回退。

## 8. 前端交互

- 重写完成 = LLM 文字说明（改了什么节、要点）+ 新版本成稿卡；预览/下载走既有 ReviewPanel 链路（`/files/{id}/content`）。
- **版本操作走对话**：「查看版本」→ versions；「回退到上一版/第 2 版」→ rollback。
- **存量缺口修复（本次必做）**：
  - canvas_service 落库时把 `outputs.downloads` 存进 message data；
  - 前端历史消息映射恢复 downloads 渲染成稿卡（否则刷新后无法发起重写）。
- `use-send-message` payload 附 `recent_downloads`（会话内最近 2 张卡，含历史恢复的）；flow 面板附 `flow_version_id`。

## 9. 边界与对抗场景

| 场景 | 行为 |
|---|---|
| 文档无 heading | 明确报错 → LLM 引导全文重新生成 |
| 「第3节」超界 / 指令模糊 | outline 返回 → Agent LLM 反问，不瞎猜 |
| LLM 输出非法 JSON / 空段落 | 校验 + 重试 1 次 → 报「重写失败，请重试」，不产生版本 |
| 目标节超长（>3 万字投喂） | 明确报错「建议拆分」，不静默截断 |
| 同文档并发重写 | 唯一索引 + 事务取号，撞线重试 |
| recent_downloads 越权 / 对象已删 | owner 校验（created_by 比对）+ blob 缺失明确报错 |
| flow 版本已被删 | 报错引导，不产生孤儿版本 |
| 段落索引漂移 | 每次基于当前版 blob 重新解析 |
| 执行中断连 | 无副作用，重发即可（不引入后台机制） |

## 10. 测试要求

- `sections.py`：无 heading / 多级 heading / 中文样式名「标题 1」/ 节含表格 / 空文档。
- `docx_edit.py`：pPr/rPr 拷贝保真逐项断言、1→N 与 N→1、表格保留、首节/末节边界。
- `versions.py`：并发取号、回退复制、owner 越权 403、flow 分支不切 current_version_id。
- 工具层：outline/rewrite/versions/rollback 契约、download 对象字段、sys 变量缺失报错。
- 前端：downloads 持久化恢复、recent_downloads 附带逻辑。
- E2E：填写 → 「把第2节重写，重点补充XX」→ 预览新卡 → 「回退到上一版」→ 下载验证内容还原。

## 11. 工作量与风险

- 后端：工具 + 执行层 4 模块 + 版本表 + canvas 落库改造，约 6 个文件；前端：payload/download 恢复，约 3 个文件。
- 主要风险：①heading 识别对奇形怪状模板的覆盖率（样式名变体、大纲级别缺失）——切节失败明确报错优于错切；②LLM 重写质量依赖投喂上下文取舍（目录+节全文+相邻标题），上线后按反馈调 prompt；③downloads 持久化改造触及 canvas 落库路径，需回归验证既有消息回放不受影响。
