# CHANGE.md — 项目迭代记录

## 2026-08-31 C端「人事」页签 P1：员工档案 + 打卡考勤

**主题**：C端新增「人事」页签，交付人事模块第一阶段（4模块20功能点中的 P1）。

**核心变更**：
- 设计文档：docs/superpowers/specs/2026-08-31-hr-module-design.md（4模块分4阶段）
- 新增5张表：hr_employee / hr_rule_config / hr_attendance_record / hr_attendance_day / hr_attendance_month（启动自动建表）
- 推导引擎 hr_calculator.py：打卡去重 / 迟到阈值 / 半夜异常窗口 / 假单优先，含对抗性单测
- hr_service.py：员工档案/规则配置/打卡流水/日月汇总 Service 层，month-close 事务化幂等
- hr_app.py：12个端点（打卡/今日/日历/建档/列表/补卡/日明细/月汇总/一键汇总/规则配置），month 正则校验+归一化+数值键类型防御
- RBAC 新增 hr_manage / hr_finance 权限点（前后端常量对齐）
- 前端：c-chat 顶部「人事」页签 + 考勤视图（打卡卡片/考勤日历/HR管理面板），hr-service.ts API 层，中文硬编码
- 范围微调：员工自助补卡申请依赖审批引擎，移至 P2 与假单审批一起交付；P1 先支持 HR 直接补卡

**遗留**：待部署联调；请假/薪资/报表见 P2-P4。

## 2026-08-31 C端：去掉「协作」「收藏」页签入口

**核心变更**
- c-chat 顶部模块页签移除「协作」「收藏」两个入口，保留 对话/工具/标书/流程
- 最小改动：仅删页签按钮项，视图渲染分支与状态保留（不可达死代码，后续确认稳定可清理）

**遗留**
- CollaborationPanel 与收藏相关渲染分支仍在 index.tsx 中（不可达），待确认后可整体清理
- 未构建部署

## 2026-08-31 流程版本记录：倒序 + 分页加载 + 时间醒目

**核心变更**
- 版本时间线倒序排列（最新在前，前端按 version_no desc 排序）
- 分页展示：初始只显示一页（5 条），底部「查看更多（剩余 N 条）」按钮点击再加载一页；切换流程时重置回第一页
- 版本时间显示醒目化：由 10px 浅灰升级为 12px 加粗 #444 + 蓝色时钟图标
- 纯前端改动（flow-detail.tsx），无后端变更

**遗留**
- 未构建部署（生产 dist 仍是旧版）

## 2026-08-31 流程版本记录：版本删除 + 醒目下载/删除按钮

**核心变更**
- 需求：版本时间线每条版本增加醒目的「下载」「删除」按钮；删除规则经用户确认——仅审核领导（leader_id）可删，其余人按钮置灰（title 提示原因）；可删最新版（current 回退剩余最高 version_no，无版本置空）；锚定该版本的批注一并删除
- 后端：`POST /flow/<flow_id>/version/<version_id>/delete`（参与者 + 非 TERMINAL + 仅领导 403 硬校验）；FlowVersionService.delete_version 事务内删版本行 + 锚定批注 + current 回退；存储对象 best-effort `STORAGE_IMPL.rm`；删除后 notify 其他参与人
- 前端：flow-detail 版本项操作区两按钮（下载蓝、删除红，非领导/已结束/忙碌时删除置灰）；删除走 confirm；删除选中版本后选中态回落最新版；flow-service.ts 新增 deleteFlowVersion；viewer 增加 is_leader（get_flow 返回）
- 下载为既有能力（downloadVersionBlob）仅 UI 醒目化

**遗留**
- 未部署（后端 flow_app.py + flow_service.py 需 SCP + docker restart；前端需 build 部署）
- 删除版本不级联清理 flow_ai_chat 中引用该版本的 output_version_id（记录保留，仅展示层「已存为新版本」标记可能悬空）

## 2026-08-31 流程文件审核：支持以任意历史版本为底稿编辑（增量追加版本）

**核心变更**
- 需求：版本记录中选中任意版本 → 文件审核显示该版本内容与批注 → 在该版本上 Word 式修改 → 保存后以该版本为底稿生成新版本**增量追加**到时间线
- 核实结论：前端链路已全部就绪，无需改动——flow-detail 把 `selectedVersion` 传给 FlowAiPanel，文件审核按选中版本下载上传预览；批注列表已按 `version_id === selectedVersion.id` 过滤显示（批注创建时独立落库，不参与文档保存）；`handleEditDocument` 提交的就是选中版本 id
- 唯一改动：flow_app.py `edit_document` 移除「仅允许编辑 current_version_id」限制，允许以任意版本为底稿；结果经 add_version 增量落成最新版（source=manual_edit），不覆盖/回滚已有版本；权限（仅当前节点负责人）与 TERMINAL 状态检查保留
- 保存后行为保持现状：新版本上传刷新预览 + flow-detail 失效查询刷新时间线

**遗留**
- 选中历史版本编辑保存后，左侧版本选中态仍指向旧版本（面板预览已刷到新版本），需手动点选
- 未部署（后端 flow_app.py 需 SCP + docker restart）

## 2026-08-31 文件审核：Word 式工具栏（run 级格式落盘）

**核心变更**
- 文件审核编辑器顶部新增 Word ribbon 简化风工具栏（docx-toolbar.tsx，portal 到父级吸顶容器）：撤销/重做、正文↔标题 2/3/4 块类型、7 种字体 + 10 档中文字号、B/I/U/S/上标/下标、字色/背景高亮色板、四向对齐、有序/无序列表、增减缩进、清除格式、右侧「已修改 N 处」+ 保存/放弃修改
- run 级格式落盘全链路：`$extractRuns` 从编辑器模型抽 run 序列（format 位 + style 串 → DocxRun，相邻同样式合并，高亮 bg 不落盘）→ docx-diff.ts 双比较（文本变 → edit；文本同但 fmtSig 变 → 纯格式 edit，TDD 单测）→ 后端 `_parse_runs` 白名单校验（6 布尔/颜色 ^#hex/字号 1-200pt/字体 50 字/≤500 片段，控制字符清洗）→ `_apply_runs` 用 python-docx 逐 run 重建段落（bg 用 w:shd、字体同时设 ascii+eastAsia），新版本 source=manual_edit
- 后端健壮性加固（质量审查 1 Critical + 5 Important 全修复）：删除+插入组合时插入参照段跳过待删段（防静默丢数据）；重写前移除 w:hyperlink/w:fldSimple（防超链接 run 残留拼接）；runs 与 new_text strip 后一致性校验；deletes 去重校验；ooxml 操作包 thread_pool_exec 防阻塞事件循环；仅允许编辑 current_version_id（防历史版本静默回滚）
- 工具栏交互细节：DropdownMenu（modal 抢焦点）用 lastSelRef 缓存选区 apply 前回挂；Popover 色板 onOpenAutoFocus preventDefault 保选区；激活态订阅 selection 变化同步；全部按钮 onMouseDown preventDefault
- E2E 实测（dev 9222，真实 Playwright 点击）：B/I 应用+激活态+回退、纯格式改动 dirty 识别与保存归零、字号/字体下拉保选区、保存全链路（新版本生成→编辑器重挂载→工具栏保留）、无序列表（ListPlugin）与缩进（自建 IndentPlugin，0-8 封顶，纯视觉不计 dirty）修复后复测通过、向后兼容（旧后端忽略 runs 不报错）
- 块级属性落盘契约补齐（同日追加）：EditorBlock 增加 align/indent/headingLevel，diffBlocks 对比初始基线（对齐''/缩进0/标题按类型派生，>3 级 clamp 2）仅对变化字段产出操作并与文本/run 变化合并；后端 `_parse_block_attrs` 校验 + `_apply_block_attrs` 写 w:jc/w:ind（每级 600 twips）/Heading 2-4 与 Normal 样式（缺样式 best-effort）；新增 10 单测（39/39 过）+ 后端助手对抗性微测 + E2E 纯对齐改动识别与保存
- 已部署服务器：后端 flow_app.py + docker restart（容器内导入与功能微测 ALL PASS）；前端 npm run build + dist SCP + nginx reload；API 健康检查 401（服务正常）

**遗留**
- 列表结构不落盘：无序/有序列表保存后降级为普通段落文本（docx numPr 未纳入契约）
- 新版本文件名 `_edited` 后缀累积（多次编辑成 `xxx_edited_edited.docx`）
- 保存后编辑器重挂载为纯文本灌入，已应用 run 格式不回显（初始灌入设计如此）
- 生产环境真实编辑保存的 run/块级属性落盘效果建议人工抽查一次（服务器端功能微测已过）

## 2026-08-31 流程页签：批注模块去掉直接发表入口

**核心变更**
- 流程详情左侧批注模块（flow-detail.tsx portal）移除底部「填写批注意见」输入框与「发表批注」按钮，批注区仅保留只读列表（连带清理 commentText state、handleAddComment、Textarea/addFlowComment 引用）；文件审核面板内的选字批注入口不受影响

## 2026-08-31 文件审核：Lexical 编辑器替换手写 contentEditable

**核心变更**
- 「文件审核」可编辑正文从手写 contentEditable + DOM diff 升级为 Lexical（0.23.1，项目已有依赖）整篇编辑：4 个自定义节点（DocxParagraphNode / DocxHeadingNode 带 para_index、HighlightTextNode 带批注锚点、AtomicBlockNode 表格/图片只读原子块）+ 插件组（初始灌入、高亮重建、点击联动、粘贴纯文本降级、脏检查）
- 保存 diff 从「DOM 遍历」改为「编辑器模型遍历」：`readEditorBlocks` → `diffBlocks` 纯函数（docx-diff.ts，14 个单测），产出与后端 `/flow/<id>/document/edit` 契约一致的三类操作；para_index 仅初始灌入赋值，回车新段天然无 index → insert，撤销/重做、IME 安全由 Lexical 原生保障
- 撤销栈干净：灌入打 history-merge + root.clear 清掉 LexicalComposer 默认空段（Ctrl+Z 到底无残留空段）；批注高亮仅 targetsByPara 变化时重建（打字不重拆），批注删除后残留高亮清除
- 表格辅助函数（sanitizeTableHtml / highlightInTableHtml / highlightInTableByAnchor / normalizeForMatch）抽到 docx-view-utils.ts，静态渲染与编辑器原子块共用
- 保存时序修复：fileId 变化后用 loadedFileId 门控，等新内容到达才重挂编辑器，避免旧基线冻结导致重复建版本
- E2E 实测（dev 9222，测试2 流程）：15 段挂载、改字/回车加段/并段、撤销重做、保存→v4（manual_edit）→docx 落盘核验、批注创建/删除高亮联动全链路通过

**遗留**
- run 级局部格式（加粗/颜色）仍不保真（沿用段落首 run 样式，设计如此）
- 保存为新版本后，旧版本上的批注高亮不再显示（批注锚定创建时版本，待产品确认是否需跨版本跟随）
- shift+enter 产生字面 \n；sanitizeTableHtml 黑名单清洗存在已知绕过面（未加引号 onerror / javascript: URL，既有问题，建议后续换 DOMPurify allowlist）
- web 全局 jest.config.ts 损坏（umi/test 缺失，既有问题），docx-diff 单测走 .scratch 临时配置
- 生产前端 dist 未部署

## 2026-08-31 文件审核：Word 式整篇自由编辑（增删段）

**核心变更**
- 正文从「逐段编辑」升级为 Word 式整篇自由编辑：纸张整体 contentEditable，点哪改哪、回车新增段落、退格/Delete 并段删段；表格/图片为只读原子块（contentEditable=false）
- 后端 `POST /flow/<flow_id>/document/edit` 从仅 edits 扩展为三类操作：edits 改写 / deletes 删除段落 / inserts 新增段落（after_para_index=-1 表开头）；应用顺序删除→插入→改写（改写持元素引用不受结构变化影响）；插入段复制锚点段样式；全部定位成功才动手；单次 ≤200 处
- 前端 DOM diff（`collectPaperOps`）：保存时遍历纸张 children，wrapper 带 data-para-index，首块文本对比原文→edit、清空/整块消失→delete、回车产生的额外块/游离块→insert（锚定前一个 index）；250ms 防抖统计改动处数，吸顶保存栏显示「已修改 N 处」
- React 兼容：编辑期间不改纸张 vdom（防丢光标），放弃修改用 resetKey 重挂载，保存成功后由新内容重挂载
- 已部署并 E2E 实测：改字+回车加段+并段删段一次保存 → v3（manual_edit）生成 → 下载 v3.docx 用 python-docx 核验三类操作全部正确落盘

**遗留**
- 并段/改写为整段文本替换，段内局部 run 级格式（局部加粗/颜色）会丢失（沿用段落首 run 样式）
- 高亮 `<mark>` 标记（AI 标注/批注锚点）在编辑中可能被浏览器拆散文本节点，diff 按 textContent 取文本不受影响

## 2026-08-31 文件审核：正文默认可编辑 + .doc 编辑支持

**核心变更**
- 后端 `POST /flow/<flow_id>/document/edit` 支持批量 edits（≤200 段/单段 ≤20000 字），先全部定位成功再统一替换；按 `/files/<id>/content` 同源规则（复刻 naive.py `to_paragraphs` 遍历）映射 para_index → docx 段落，python-docx 整段替换文本（保留首 run 格式），存为新版本（source=manual_edit）
- .doc 编辑支持：LibreOffice headless 转 docx 后编辑，新版本统一存为 .docx；转换与 OLE2 识别抽到共享模块 `api/utils/doc_utils.py`（file_api 与 flow_app 共用；restful_apis 蓝图的 manager 由动态加载器注入，蓝图间不能直接 import）
- 前端交互改版：正文段落默认 contentEditable 直接编辑（去掉每段编辑按钮/textarea），回车禁用（单段语义）；输入即记录，改动段淡黄底提示，吸顶保存栏显示「已修改 N 处」+ 保存/放弃修改；保存后自动把新版本重传为 document 刷新预览
- 编辑权限：仅当前节点负责人 + 流程未结束；仅「版本文件来源」（reviewSource==='version' 且 isOwner），手动上传附件只读；表格/图片段落不可编辑
- 部署实测：docx 流程编辑→v2 生成→预览刷新→内容核验通过；.doc 流程（测试2）编辑→LibreOffice 转换→v2 docx（manual_edit）→替换文本核验正确；放弃修改/错误路径正常

**遗留**
- 整段替换会丢失段内局部格式（加粗/颜色等 run 级样式保留段落级首 run 样式）
- 生产前端 dist 未部署

## 2026-08-31 流程批注：表格锚点消歧 + 表格内高亮 + 批注删除

**核心变更**
- 审核弹框正文改 Word 纸张式排版：A4 白纸（max-w 794px）+ 阴影 + 宋体 + 页边距，正文 14px/2 倍行距/首行缩进 2 字符/两端对齐
- 流程文档上传只接受 doc/docx：AI 面板 ChatInputBox（新增 accept 透传，文件选择/拖拽/粘贴均校验）、创建流程初始文件、详情页「上传修改版」三处入口
- 表格内批注引线错乱修复：新增 `anchor_start` 字段（flow_comment 表自动迁移），创建批注时记录选区在段落归一化文本中的起始偏移，定位时按偏移消歧重复文本（`findTextEndRect`），不再错指首次出现行
- 表格内手动批注与正文同款 `<mark>` 高亮：`highlightInTableByAnchor` DOM 级实现（偏移消歧、精确包裹），点击高亮联动右侧卡片
- 批注删除：后端 `POST /flow/<flow_id>/comment/<comment_id>/delete`（仅批注作者本人、流程未结束），前端卡片 hover 显示删除按钮 + confirm 确认
- 已部署服务器并实测：anchor_start 持久化、消歧定位（卡片与选中行像素级对齐）、删除闭环均通过

**遗留**
- 服务器前端（生产 dist）尚未部署本次改动，仅本地开发服务器生效
- 存量旧批注无 anchor_start，仍按首现位置定位
- 表格内每段仍仅首个 AI 标注有高亮（沿用旧逻辑）

## 2026-08-30 C端「流程」页签（多角色文件流转工作流）

**核心变更**
- 新增 4 张表：flow_instance / flow_version / flow_comment / flow_ai_chat（db_models.py，自动建表）
- 新增 flow_service.py（状态机 FlowWorkflow + 服务层 + 通知复用）与 flow_app.py（/api/v1/flow/* 10 个端点）
- 前端 c-chat 新增「流程」页签：列表/创建、文件主视图详情（状态条+版本时间线+预览+批注）、AI 处理面板（复用对话智能体）
- 铃铛通知兼容 category='flow'

**遗留**
- AI 产出仅 Markdown 版本，docx/PDF 格式保真后续迭代
- 多文件流程、可配置模板、在线行内批注为非目标（见设计文档 §8）
- 终审低优先级残留：JSON 文件内容恰形似错误 envelope 时会被误判（概率极低）、add_version 失败回滚可能留 MinIO 孤儿对象、发送失败不回填输入框、REST 层自动化测试缺失

**状态**：代码完成，待部署联调（成套 SCP：db_models.py / flow_service.py / flow_app.py + 前端 build）
