# CLAUDE.md

可用账号：lg18629285296@163.com
密码：12345678


核心注意点：
1、新增和修改的代码，禁止私自部署服务器
2、前端文案只用中文，不做 i18n 多语言翻译（新增 key 只在 zh.ts 加，不同步 en.ts）
3、名词约定：我说的「用户管理」一律指**顶部导航「智能采集」下一个的「用户管理」** = 路由 `/permission`（Web 权限管理页，`web/src/pages/permission/index.tsx`）。它**不是** user-setting 设置左导航里的旧「用户管理」（`/user-setting/user-management` → 旧 admin/users），也**不是** `/admin/users`。涉及该名词时按此定位，勿删改错。

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

【核心注意：新增的数据库要放到ragflow项目的初始化脚本中，要考虑到迁移部署，不会导致问题】



修改代码和新增代码，需要注意一下规范规则：

1. 原有功能是否被意外移除、更改返回值、修改核心流程。
  2. 新增/修改的代码是否正确实现了目标功能。
  3. 边界条件：空值、零值、极限输入、并发访问、资源耗尽等情况是否安全。
  4. 多场景适配：是否考虑了不同角色、不同配置、不同环境、多语言等场景下的行为。
  5. 异常处理与日志：错误路径是否有兜底，异常是否被正确处理和记录。
  6. 安全性：SQL注入、XSS、权限绕过、敏感信息暴露等。
  7. 性能隐患：循环内 IO、无索引查询、内存泄漏、未释放连接等。
  8. 代码质量：可读性、重复代码、魔法值、命名规范。



### 参考文档（需要时读取原文）

| 文档 | 路径 | 包含内容 |
|------|------|----------|
| 范本填写检索增强（二档全文降级+实体分析） | `D:\AI\ragflow2\docs\superpowers\specs\2026-09-24-template-fill-retrieval-fallback-design.md` | ★ 范本填写检索增强：extract_entities 一次 LLM 抽直填值+实体/语境（高可信闸+空兜底，与预判 gather 并行）；一档空槽二档实体组合词宽检索（0.1 阈值/top_k 翻倍/fulltext 打标+归纳编写 prompt）；`_entities` 画布保留键（is_canvas/REST 剥离自动跟随，B端/REST/dry_run 零变化）；确认卡 direct_value 预填+弹卡条件扩展（前端零改动）；`_confirm_changed_fields` 改元组返回。后端 957+前端 5 全绿（**已部署 2026-09-24 + push 60f969b1**：后端 2 文件成套 SCP+重启+冒烟通过，前端零 build）。实施计划 docs/superpowers/plans/2026-09-24-template-fill-retrieval-fallback.md，见 CHANGE.md 2026-09-24 条目 |
| 流程页 102 任务不存在无限弹错修复 | `D:\AI\ragflow2\CHANGE.md`（2026-09-23（三）条目） | ★ 解「选中流程一直报『提示 : 102 任务不存在』」：范本删除级联清理任务行后，流程历史事件仍引用旧 task_id，filled 行 10s 权威刷新打 progress 恒回 102 → 全局拦截器每 10s 弹一次。修法=`request.ts` 请求级 `skipBusinessError` 选项 + poll hook 识别 102（`missing` 集合停两通道，filling 行降级 failed 提示，filled 行保持现状）+ `fetchTemplateFillTaskProgress` 静默回落 null。纯前端 4 文件，354 passed（**已部署 2026-09-23 + push 4e94ab30**） |
| 范本识别蓝色已填值 | `D:\AI\ragflow2\docs\superpowers\specs\2026-09-23-template-blue-filled-detect-design.md` | ★ docx 蓝色字体已填值识别为填写点+默认值：方案 A 扩展 V2 管线新 slot 类型 kind="blue"——`blank_slots.py` `_is_blue_run`（B-max(R,G)≥40）+ blue 簇切位（优先于下划线）+ merge blue 隔离守卫；`detector.py` blue 位豁免 V2 硬闸（default_value=位文本 + **必须显式 default_source:"detected"**，缺失被 `_merge_defaults` 兜底成 manual 致沉淀保护误伤——三层测试钉死）+ fallback 兜底。下游零改动，存量范本需重新识别。实施计划 docs/superpowers/plans/2026-09-23-template-blue-filled-detect.md，见 CHANGE.md 2026-09-23（二）条目（10 套件 570 passed，**已部署 2026-09-23**：后端 2 文件 SCP+重启；该文件蓝字实测 280 run/切出 134 blue 位） |
| 流程对话 LLM 排版适配 | `D:\AI\ragflow2\CHANGE.md`（2026-09-23（一）条目） | ★ 解「流程页对话 LLM 表格/字体没格式」：生产实证表格其实有解析渲染但零样式（无边框白底气泡里看着像纯文本）。修法=`global.less` 作用域类 `.flow-chat-md`（表格边框+表头灰底+宽表横向滚动、h1~h6 收敛 14~16px、列表/代码/间距统一）挂 flow-detail 两处回复气泡；**不动共享组件**，c-chat 零影响；**存量数据展示时天然生效**。纯前端 2 文件，352 用例全绿（**已部署 2026-09-23 + push**） |
| 流程页签按钮文案两项调整 | `D:\AI\ragflow2\CHANGE.md`（2026-09-22（十）条目） | ★ ①流程页签删除「存为新版本」按钮（lastRecord 只为它存在，渲染条件收窄为 hasContent，自动保存+「仅存记录」兜底不动）；②「提交下一节点/退回上一节点」改为「提交至/退回至 + 目标节点处理人名」（NEXT_HOLDER_FIELD：发起→leader、领导→handler、处理→initiator；PREV：领导→发起、处理→领导；nicknameMap 口径，缺失回退原文案）。纯前端 2 文件，352 用例全绿（**已部署 2026-09-22，随（九）同批 push**） |
| 流程审核携带上一节点成稿 | `D:\AI\ragflow2\CHANGE.md`（2026-09-22（九）条目） | ★ 解「流程下一节点点文件审核报『请先上传流程版本』」：无版本流程的成稿断层——上一操作员的范本填写成稿从未成为流程版本，下一节点无审核入口。修法=toggleReview 守卫加成稿兜底：`uploadTemplateFillResultAsDocument` 从 templateFillRef（template_fill_events 重放）取最新 filled 行 download.url → fetch Blob → 上传 document；优先级 手动上传>流程版本>历史审核绑定>成稿>引导。注意 tpl_fill_task.flow_instance_id 是 canvas session id，成稿与 flow 的关联只能靠前端 events 的 task_id。E2E 实证 leader 打开即审核成稿，352 用例全绿；纯前端单文件 flow-ai-panel.tsx（**已部署+commit+push 2026-09-22，随（十）同批**） |
| 范本预览占位符断行根修 | `D:\AI\ragflow2\CHANGE.md`（2026-09-22（八）条目） | ★ 解「查看填写内容预览里占位符/填写值与标签不在一行，下载文件却正确」：`applyDocxHighlight` 的 `locate(end)` 在占位符为段落末位 run 时把匹配终点解析到**下一段落**的文本节点开头 → Range 跨段 → 高亮 span 被抽到页容器下变块级独占一行。修法=`locate` 加 `preferEnd`（与 highlightDocxRanges 同构），终点优先解析为本节点末尾。真实生产工作副本 + docx-preview 本地复现实证：修复后 415/415 span 回段落内同行；5 套件 53 passed。纯前端单文件 docx-highlight.ts（**已部署 2026-09-22 + push**：873 个 dist 文件双端 md5 全量一致，首页 200） |
| 范本库超管跨租户视野 | `D:\AI\ragflow2\CHANGE.md`（2026-09-22（七）条目） | ★ 解「角色超管进范本库列表空白」（数据全在 lg186 租户，get_list_page/get_owned 按租户隔离）：`get_list_page` 加 `all_tenants`、`get_owned` 加 `allow_global`（service 不自查，调用方须已做超管闸；agent/tools 画布语境零改动）+ template_api 两列表与 `_load_template`/任务 owned 传 `is_superadmin(current_user)`；顺带修（五）遗留的 4 套件测试腐坏（桩用户补 is_superuser=1）。范本 9 套件 259 passed（**已部署 2026-09-22**：2 文件 md5 一致+重启+冒烟，demo01 生产实见 lg186 租户范本） |
| 超管判定=角色+标志 | `D:\AI\ragflow2\CHANGE.md`（2026-09-22（六）条目） | ★ 解「用户管理页指派超管角色没效果」：`is_superadmin()` 唯一口径（is_superuser 账号标志 OR 内置「超级管理员」角色成员）+ `user_has_super_role` + Redis superrole: 缓存 600s（随既有 invalidate_user_permissions 失效，零新增调用点）+ /permission/me、superuser_required、crawl4ai_ws、delete_user 四处同口径 + 内置角色禁改名守卫（角色名是代码判定依据）。事故教训：`REDIS_CONN.set_obj` 内部 json.dumps，存字符串落盘带引号读回恒 False（须存 int 1/0）；redis-cli 清键须 `-n 1`（项目 REDIS 用 db 1，默认 db 0 扫不到）。后端 5 文件，权限 6 套件 46 用例全绿（**已部署 2026-09-22**：5 文件 md5 一致+重启+冒烟，demo01 角色超管四端点 code 0、仅普通用户角色对照 False；前端零改动） |
| B端5模块仅超管 | `D:\AI\ragflow2\CHANGE.md`（2026-09-22（五）条目） | ★ 范本库/智能体/记忆/智能采集/用户管理页面+数据仅超管可见（硬限制，不走角色勾选）：新 `@superuser_required` 装饰器 + 后端 7 文件加闸（agent 15/memory 12/crawl4ai 11+WS close(4003)/template 17/permission 8 端点；C 端填写运行时端点保持仅登录）+ 顺带补 2 个裸奔端点 login 闸（/agents/download、/agents/{id}/upload）+ 移除 GET /agents 的 permission_required("agent")（防 C 端对话下拉坏）+ 前端 3 文件硬限制（isSuperuserOnlyPath 段边界守卫 + RouteGuard 重定向 + navbar superuserOnly 过滤）+ 角色表单移除 4 个失效 key。后端 21+前端 352 用例全绿、build 通过（**已部署 2026-09-22 + push 3c0ec6e3**：7 文件 md5 双端一致+重启+import 冒烟，前端主 chunk md5 一致；生产实测 demo01 被闸端点业务码 403、GET /agents 与 C 端运行时端点正常、超管全放行） |
| 批注版本维度 | `D:\AI\ragflow2\CHANGE.md`（2026-09-22（四）条目） | ★ 流程页签批注加版本功能：「查看文件内容」抽屉批注按**被查看版本**过滤（原跟随时间线选中版本会错位）+ ReviewPanel `versionLabel` prop（标题栏「版本 v{n}」徽标 + 批注卡/列表归属徽标 v{n}/流程）+ 左下批注模块同徽标；文件审核入口（最新审核文件 AI 批注）零改动。纯前端 2 文件，vitest 345 passed（**已部署 2026-09-22 + push f16a4a79**：首页 200 + chunk md5 三端一致） |
| 流程版本保真编辑 | `D:\AI\ragflow2\docs\superpowers\plans\2026-09-22-flow-fidelity-edit.md` | ★ 流程版本铅笔编辑换 docx-preview 保真树上段落级 contentEditable（格式所见即所得，替换 Lexical 旧视图）：模型驱动双指针对齐（复刻后端 sdt/空段/表格遍历口径，DOM 多余段仅目录形态可跳过，否则整体回退 Lexical）+ 目录形态段强制只读 + canonical 消渲染差异幻影 + **vMerge 幻影抑制**（python-docx r.cells 基线表头逐行重复 vs docx-preview continue 空 td）+ beforeinput 拦截分段/并段/跨段删除/拖放。核心 `docx-fidelity-edit.ts` + `docx-edit-bar.tsx`；23 对抗单测，全量 336 passed+E2E 真实文件全链路验证（保存新版本+放弃恢复）。见 CHANGE.md 2026-09-22（二）条目（**已部署 2026-09-22 + push 055810df/4d569935**：build+dist+nginx reload，生产冒烟通过，纯前端 3 文件） |
| 流程智能体解耦+默认编辑态 | `D:\AI\ragflow2\CHANGE.md`（2026-09-22 条目） | ★ ①流程页签智能体与对话页解耦：flow-panel 顶栏下拉只列名称带「流程」的 agent，独立键 `ragflow_flow_agent_id` 持久化，props 下发 FlowAiPanel，切换清会话防绑定错配；c-chat 对话页反向过滤不展示带「流程」agent；②审核弹框 `defaultEditing` prop——从「编辑」入口打开直接落编辑视图（open 翻转复位）。纯前端 5 文件，已随 2026-09-21 渲染优化批上线，2026-09-22 commit+push 固化 |
| 大文件渲染优化 | `D:\AI\ragflow2\docs\superpowers\plans\2026-09-21-docx-render-perf.md` | ★ 审核/范本预览大 docx 打开卡死治理（实施计划，已完成）：>2.5MB 文本降级门槛（审核面板补齐+范本预览改**派生判定**首帧生效，根除「先白渲染一遍再翻转」）+ 渲染产物 WeakMap<Blob>+LRU3 缓存重开毫秒级回放（树只存一份内存不翻倍；strip/rebuild 配套重涂；cancelled/settled/failed 堵在飞错树入缓存）+ CHANGE.md 2026-09-21（十二）条目。纯前端 5 文件+4 新测试套件，全量 vitest 312 passed+build 通过（**已部署+已 push 2026-09-22**） |
| 审核弹框手动编辑复用 | `D:\AI\ragflow2\CHANGE.md`（2026-09-21（十）条目） | ★ 对话附件/流程版本文件复用审核弹框就地改文字，产出**全新文件**（原字节不动）自动交接 LLM：共享内核 `api/utils/docx_edit.py`（parse 对抗校验+.doc 转换+先定位后应用事务式编辑）+ 新端点 `POST /files/<file_id>/edit`（{tenant}-downloads 通道→新 uuid 对象）+ `editFileDocument`/`flowDocEditOpsToBody` 共用 ops 契约 + ChatInputBox `injectDoc` 队列注入（nonce 防重/removeId 剔旧）+ flow-ai-panel 按 reviewSource 分派 + c-chat index 直接换队 + flow-detail 版本时间线铅笔入口（editFlowDocument→新版本热切）。后端 72+前端 11 新用例、全量 vitest 293 passed（**后端+前端均已部署、已 commit+push 95c72c0e 2026-09-21**） |
| 原文⇄AI修改自由切换 | `D:\AI\ragflow2\CHANGE.md`（2026-09-21（九）条目） | ★ 解「回退后 AI 修改找不回来」：patch_json 回退后永久保留（不再清空）+ 新 `perform_reapply` 正向恢复（按文档状态分派：find 命中→应用+产 kind='apply' 轮+置 resolved；逆探 replace 在文档→幂等纯翻转不产轮；两边都不在→诚实 101；零差异不产轮）+ fix_rounds_left 对 apply 轮 carve-out 不烧额度 + status 端点路由（resolved 且行 open 且有 patch→reapply，asyncio.to_thread）。状态环 fixed/resolved+patch⇄open+patch 无限切换。前端 FixActions 按 status 分派（open+patch→「恢复 AI 修改」按钮）+「已回退」灰徽标。后端 3 文件+前端 2 文件；后端 260+前端 59 用例全绿（**后端已部署+已 commit+push 088f3f07 2026-09-21**；前端未部署：build+dist+nginx reload 后「恢复 AI 修改」按钮可见） |
| 弹框三项根修 | `D:\AI\ragflow2\CHANGE.md`（2026-09-21（七）条目） | ★ 解「弹框确认保留无反馈/批注未定位/确认后文档仍原文」三断层：A=面板订阅 useFileReviewState + mergeAnnotationOverlays 按 id 覆盖 {status,patch}（无变化返原引用防轮询重渲染）；B=matchMultiline 迁 docx-view-utils.ts + 有序降级（序列唯命中→首行首现→诚实未定位；拼接通道被证明不可达死代码故不设）；C=新端点 GET /file/review/<task>/<ver>/content + useReviewVersionBlob + 粘性降级状态机（versionDegraded/contentIsVersion 防乒乓）+ getLocateText 成稿定位切 patch.replace + 编辑闸。后端 1 文件+前端 5 文件；后端 316+前端 112 用例全绿（**后端已部署+已 commit 5739af89 2026-09-21；前端未部署**：build+dist+nginx reload 后行）；同批追加边栏批注卡默认折叠（箭头展开/定位选中自动展开） |
| 修复对比+回退/确认 | `D:\AI\ragflow2\CHANGE.md`（2026-09-21（六）条目） | ★ 解「看不到修复前后对比/已修复无标记/修错不能撤」：修复轮落地补丁 {find,replace} 与 fixed 同笔存进批注行（新列 patch_json）→ state 下发 → 前端红绿对比；回退=逆补丁应用最新落盘版本→产 kind='revert' 轮（不烧修复额度）→批注回 open+patch 清空；确认保留=复用 status 端点置 resolved。后端 4 文件（db_models/service/executor/api）+前端 6 文件（共用 review-fix-diff.tsx）；310 后端+25 前端用例全绿（**未部署、未 commit**；部署硬约束：先后端 4 文件 SCP+restart 建列，再前端 build，反序 revert 404） |
| 人工批注绿色色系 | `D:\AI\ragflow2\CHANGE.md`（2026-09-21（四）条目） | ★ 解「人工批注不醒目、与 AI 分不开」：核心断层是正文 mark 双方都按级别配色。方案=**绿=人工专属色系**（MANUAL_STYLE #67C23A/#F6FFED/#388E3C），正文 mark+SVG 引线/边栏 CommentCard/列表条目三处统一转绿，级别只留徽标；来源 chip 升实底白字（AI 蓝人工绿）。同日追加：列表区人工批注删除按钮（仅本人，无锚点批注此前全界面无删除路径）。纯前端单文件 review-panel.tsx（**未部署、未 commit**；部署=build+dist+nginx reload） |
| 无版本流程批注放行 | `D:\AI\ragflow2\CHANGE.md`（2026-09-21（三）条目） | ★ 解「审核面板手动批注报『流程暂无文件版本，无法批注』101」：文件审核目标走上传文档通道不经版本，add_comment 强校验 version_id 非空必拦。修法=批注 version_id 语义放宽为可空（空串=流程级意见，锚定的是 anchor_text 内容而非版本）+ flow-detail `commentsOf` 放行 version_id 为空批注（ReviewPanel+左下批注模块共用一处）。后端 1 文件+前端 1 文件，10 用例全绿（**后端已部署+已 push a919c45d 2026-09-21**；前端未部署） |
| 流程对话附件 chip | `D:\AI\ragflow2\CHANGE.md`（2026-09-21（二）条目） | ★ 流程页签用户气泡展示随消息上传的附件：flow_ai_chat 加 `files` 列（[{id,name}] JSON，发送时事实只随预存新增落库、回填不覆盖，白名单归一上限 10 个）+ flow-ai-panel `liveFilesRef` 发送起点快照（与（一）竞态同源）挂 live.files + flow-detail `parseChatFiles`/`ChatFileChips` 浅色 chip（历史 parse `c.files`、live 用 `live.files`）；范本填写/文件审核无需单独做——进度卡已有各自文件名，chip 在用户消息层统一生效。后端 3 文件+前端 4 文件（**后端已部署+已 push 2383d2cb 2026-09-21**；前端未部署） |
| 上传队列清空竞态修复 | `D:\AI\ragflow2\CHANGE.md`（2026-09-21（一）条目） | ★ 解「新流程手动上传文件审核报『未指定待审核文件』」：handleSend `setSending(true)` 触发 ChatInputBox 在 sendLoading 上升沿清空队列→父层 `uploadedDocsRef` 被清空，而附件读取在预存记录 await 之后→恒空→不注入 review_file_id；demo01 有版本走自动附带兜底掩盖了它。修法=handleSend 顶部同步快照 `manualDocs`，守卫+取附件统一用快照；c-chat 本就安全（state 同步展开）。纯前端单文件，**已部署 2026-09-21**（build+dist+nginx reload；未 commit/push） |
| 文件审核成稿存为流程版本 | `D:\AI\ragflow2\CHANGE.md`（2026-09-20（十八）条目） | ★ 解「确认审核修改后查看文件内容还是原件」：审核进度卡成稿行新增「存为流程版本」按钮（flow 页签专属可选 prop `onSaveAsVersion`，c-chat 不传不渲染），flow-detail `saveReviewAsVersion` 走 review download Blob→`uploadFlowVersion(source='ai_file_review')`→invalidate，版本时间线/「查看文件内容」随之可见修改后文件；卡片自管 saving/saved/error 按钮态；手动按钮而非自动建版（3 轮修复防版本灌水）；纯前端 2 文件，19 前端用例全绿（**已部署 2026-09-21**：后端 2 文件 SCP+md5 一致+restart+import 冒烟，前端 build+dist+nginx reload，冒烟首页 200/chunk 命中新文案/端点 401；未 commit/push） |
| 文件审核点名修复+补充原因 | `D:\AI\ragflow2\CHANGE.md`（2026-09-20（十七）条目） | ★ 对话说「修复某某条因为XX」适配（方案 A 软约束）：FileReviewTool `_fix` 开放 user_query→`admit_fix_round(user_query_override)`（Service/REST 同通道，非字符串忽略+裁500）；`_status` 待修条目编号「N. [级别]」与修复 prompt `[idx]` 同序同源做锚点；meta 引导点名条目时「级别取该条+user_query 写序号/原文+其余保持原样」。越界风险靠 prompt 硬指令，实测越界再升级白名单硬过滤（**已部署 2026-09-21**：与（十六）同批后端 SCP+重启；未 commit/push） |
| 文件审核对话修复 task_id 注入 | `D:\AI\ragflow2\CHANGE.md`（2026-09-20（十六）条目） | ★ 流程页签对话式修复适配：节点发起的审核 task_id 不进 LLM 上下文 → 前端经 `inputs.review_task_id` 注入 Begin + FileReviewTool `_resolve_task_id`（显式参数>注入）双通道解析，用户说「修复严重问题」无需提供 task_id；二次修复确认已支持（canFix 镜像闸门 + MAX_FIX_ROUNDS=3，零改动）。后端 2 文件+前端 1 文件（**已部署 2026-09-21**：后端 SCP+重启、前端随合并批 build；未 commit/push） |
| 流程单文件审核守卫 | `D:\AI\ragflow2\CHANGE.md`（2026-09-20（十五）条目） | ★ 流程页签「一个流程只能审核一个文件」：FileReview 任务链按文件独立（task_id/轮次/成稿对象名挂 file_id），flow-ai-panel 新增发送守卫——流程内已有审核（aiChats 最近 file_review 记录 / 本轮实时 fileReviewRef）时，手动上传不同文件发送被拦截提示；版本自动附带路径刻意不拦（每次上传 id 轮换且属流程自身文档）。纯前端单文件（**已部署 2026-09-21**：随合并批 build+dist+nginx reload；未 commit/push） |
| 迭代记录 | `D:\AI\ragflow2\CHANGE.md` | ★ 全部新增需求/功能整改记录：**增量基线按工作上下文隔离**——修 demo03 全新流程继承了 demo02 手改值（`latest_done` 只有租户+范本两维 + `flow_instance_id` 死字段），拆出 `latest_done_in_context`（空上下文即全量）+ `canvas_service` 写 `sys.session_id`（09-17 已部署+已 push，4 后端文件成套）/范本就地修改可见性修复——卡片消失（前端清快照但 modify 不发 SSE）+ 预览不变（预览是工作副本+前端 values，非成稿）+ `detail` 第四匹配通道按**值**反查 key + 派生副本 `-downloads/tplfill-{task_id}` 覆盖（09-17 已部署+已 push）/FillTemplate 工具在 B 端画布编辑器登记（09-17 已部署）/写回范本库改按钮触发、停止自动沉淀默认值（09-17 已部署）/docx 替换链路根修——超链接段落统一区间重写治 {{token}} 腐坏（docxtpl 报 expected token 'end of print statement'）+ occ 嵌套剔除治同段同形留白错位（09-16 已部署，该范本需重新 AI 识别升 v2）/FillTemplate 新增 detail 查询 action 支撑画布「范本查询」类（09-16 后端已部署，画布配置待 UI 操作）+ fill 支持按名称模糊解析唯一已发布命中直接发起（09-16 已部署）+ 跳转定位漂移根修 instantFocusScroll（09-16 已部署）+ 新增 modify action 已完成成稿就地改字段（09-16 已部署）/B端填写点列表双模式+点击行定位文档（09-15）/纯空白 anchor raw 匹配通道+{{key}} 徽标（09-15）/AI 识别 occ 超界溢出封顶（09-14）/PDF 矢量留白横线回填+pdf2docx 引擎替换（09-14 已部署）/文件审核保真预览显原色+显式编辑、「第X章」文本章标题兜底切节、template_fill_events 64KB 截断修复等，最新迭代在最上方 |
| 部署服务器 | `D:\AI\ragflow2\本地部署服务器.md` | 服务器SSH连接、Docker部署、前端/后端/Flutter热更新、Nginx配置、常见问题排查 |
| 第三方接口 | `D:\AI\ragflow2\接口文档_2026-06-04.md` | 标讯API、企业画像API、合同API等第三方接口的请求/响应字段定义和鉴权方式 |
| 项目架构 | `D:\AI\ragflow2\项目架构.md` | 系统整体架构、模块间调用关系、数据流、技术选型决策背景 |
| 二次开发功能汇总 | `D:\AI\ragflow2\二次开发功能汇总.md` | 全部二次开发功能清单：标讯系统、企业查询、Agent画布、爬虫引擎、MCP Server等 |
| 协作功能二开方案 | `D:\AI\ragflow2\协作功能二开方案.md` | 协作页签25个功能的现状分析、差距评估、适配方案、数据库/前端组件清单、分4个Phase的优先级规划 |
| Crawl4AI 独立爬虫服务方案 | `D:\AI\ragflow2\crawl4ai-service-独立部署方案.md` | ★ 下一代爬虫架构：独立部署 crawl4ai Docker + FastAPI调度服务 + RAGFlow KB/DB适配器，替代现有定时任务爬虫体系 |
| 智能采集系统设计 | `D:\AI\ragflow2\智能采集系统设计.md` | ★ 新智能采集系统：基于 crawler_result + 扩展表的多类别采集（bid/policy/personnel/news/other），与 bid_* 解耦，手动触发+定时调度，YAML category 字段驱动 |
| 权限管控 RBAC | `D:\AI\ragflow2\docs\superpowers\specs\2026-08-25-permission-rbac-design.md` | 角色+权限点+用户角色三表、@permission_required、前端菜单过滤/路由守卫、B端权限管理页、存量用户默认普通用户 |
| 反爬能力等级 | `D:\AI\ragflow2\反爬能力等级.md` | 爬虫三级反爬能力清单：🟢 一级(rest_api基础) / 🟡 二级(加密/浏览器指纹) / 🔴 三级(SPA/Stealth)，含 YAML 配置模板、选型决策树、排查流程 |
| 踩坑问题清单 | `D:\AI\ragflow2\踩坑问题清单.md` | 30 个实战踩坑：Docker/Nginx 部署、SPA 爬虫调试、ORM 迁移、成套 SCP 清单、队列重复任务去重（#30）等 |
| A2 detector 旁路方案 | `D:\AI\ragflow2\A2-detector-inprocess-旁路方案.md` | ★ Detector 改为 scheduled_task_executor 进程内执行，绕过 task_executor 主队列。诊断数据、4 文件改动清单、验证流程、回滚、后续优化方向 |
| crawl-dedup 爬虫排队去重方案 | `D:\AI\ragflow2\crawl-dedup-爬虫排队去重方案.md` | ★ crawl:queued:{site} 标记：入队前 SET NX、task_executor 跑完 DEL，每站最多一条排队/运行中爬虫。含 TTL 自愈被否决的教训、队列清理脚本、验证与回滚 |
| 流程页签设计 | `D:\AI\ragflow2\docs\superpowers\specs\2026-08-30-flow-workflow-design.md` | ★ C端新增「流程」页签：文件为主视图的多角色串行工作流（发起人→领导→处理人→汇总→归档），4张 flow_* 表、文件版本时间线、复用对话智能体；实施计划见 docs/superpowers/plans/2026-08-30-flow-workflow.md（已完成，待部署联调） |
| 模板填写系统设计 | `D:\AI\ragflow2\docs\superpowers\specs\2026-09-07-template-fill-design.md` | ★ 固定模板（Word/Excel）+ KB 自动填写：tpl_ 3表、占位符注册表、LLM产字段值JSON+docxtpl/openpyxl渲染、B端范本库页签 + C端对话工具/flow节点双入口；最新：2026-09-08 画布「范本填写」节点 TemplateFill 升级（多范本各产成稿+三路注入+成稿预览）+ LLM 压力优化（产值批次并发3/多范本并行总闸4/跨范本检索去重，commit 4be0c134，未部署），见 CHANGE.md 当日条目 |
| 模板填写进度流式设计 | `D:\AI\ragflow2\docs\superpowers\specs\2026-09-08-template-fill-flow-design.md` | ★ TemplateFill 进度流式（5 类事件经 FanOut 同款管道，C端对话+流程 AI 面板共用）+ 流程页签适配（版本文本轻量注入 + 成稿存为流程版本）；实施计划 docs/superpowers/plans/2026-09-08-template-fill-flow.md（已完成，已部署 2026-09-09） |
| 范本填写取消链路设计 | `D:\AI\ragflow2\docs\superpowers\specs\2026-09-09-template-fill-cancel-retrieval-design.md` | ★ 前端停止→服务端取消（SSE task_id + POST cancel 端点写 Redis 取消键）+ 取消检查点下沉批次/检索粒度（executor should_cancel）+ 画布检索并发限流（TEMPLATE_FILL_RETRIEVAL_CONCURRENCY 默认2）；实施计划 docs/superpowers/plans/2026-09-09-template-fill-cancel-retrieval.md（已完成，未部署） |
| 范本默认值基线设计 | `D:\AI\ragflow2\docs\superpowers\specs\2026-09-10-template-default-baseline-design.md` | ★ 占位符默认值基线（detected识别提取/auto填写沉淀/manual B端编辑三来源）+ 变化字段 LLM 预判 + 对话中暂停确认（confirm_pending SSE + confirm 端点写 Redis）+ 条件执行；2026-09-13 确认卡升级全量展示（勾选=交给LLM 的白名单语义，见 CHANGE.md 当日条目，未部署），实施计划 docs/superpowers/plans/2026-09-10-template-default-baseline.md |
| 已结束流程维护页 | `D:\AI\ragflow2\docs\superpowers\specs\2026-09-09-flow-finished-manage-design.md` | ★ 流程页签管理视图（2026-09-09 升级为超管专属「全部流程」）：超管可看全库所有用户流程（scope=admin + list_all + _require_viewer 读豁免）+ 状态筛选/发起人列；发起人操作保留（软删/恢复/再次发起/重新激活，乐观锁）；6 状态独立配色（flow-utils STATUS_* + FlowStepper 内联 style）；实施计划 docs/superpowers/plans/2026-09-09-flow-finished-manage.md（已完成编码，未部署，与团队权限隔离改造合并成套 SCP：追加 flow_service.py + flow_app.py + 前端 build） |
| 人事模块设计 | `D:\AI\ragflow2\docs\superpowers\specs\2026-08-31-hr-module-design.md` | ★ C端「人事」页签：打卡考勤/请假审批/薪资核算/财务凭证 4模块20功能点，13张hr_*表+30端点+4阶段实施；P1 已上线，P2/P3/P4（凭证+报表+考勤机导入）已实施待部署（P4 后端质量审查修复见 CHANGE.md 2026-09-01 条目），实施计划 docs/superpowers/plans/2026-09-01-hr-p2-leave.md 与 2026-09-01-hr-p3-salary.md |
| 移除团队权限隔离 | `D:\AI\ragflow2\docs\superpowers\specs\2026-09-09-remove-team-permission-design.md` | ★ 读全局放开（KB/智能体/对话助手/搜索应用/文件所有人可见）+ 写 owner-only；Service 租户参数 None=全局、accessible 存在性检查、新增 owned()；user_tenant 留死路径，后续写权限由 /permission RBAC 承接；实施计划 docs/superpowers/plans/2026-09-09-remove-team-permission.md（已完成编码，未部署，须 15 个后端文件成套 SCP） |
| 流程对话保存自治 | `D:\AI\ragflow2\docs\superpowers\specs\2026-09-11-flow-chat-save-design.md` | ★ 流程 AI 对话与对话页解耦：flow_ai_chat 加 user_id/template_fill_events 成权威存储、影子会话 source='flow' 对话页不可见、存量「流程：xxx」会话打标迁移；多轮续聊仍借 canvas DSL 运行时（设计完成，未实施） |
| 范本预览保真渲染 | `D:\AI\ragflow2\docs\superpowers\specs\2026-09-11-template-preview-docx-fidelity-design.md` | ★ C端范本预览 docx 分支改 docx-preview 渲染原始文件（字号/加粗/颜色/表格保真）+ applyDocxHighlight 跨 run 占位符高亮保留；xlsx/B端不动（已完成编码+构建验证，未部署，纯前端） |
| 文件审核保真渲染 | `D:\AI\ragflow2\docs\superpowers\specs\2026-09-11-review-panel-docx-fidelity-design.md` | ★ 文件审核只读路径 docx-preview 保真（AI 标注/手动批注锚定+批注栏+引线+兜底全保留，highlightDocxRanges 归一化匹配+同 p 校验）+ 范本预览性能优化（span 映射增量高亮 + content-visibility 屏外页懒渲染）；2026-09-13：可编辑文件默认也走保真预览显示原色 +「编辑文档」显式切换（分支根节点加 key 防 React 就地复用泄漏 DOM）+ epoch 桥接修重挂不重渲染（见 CHANGE.md 当日条目，未部署，纯前端） |
| 范本识别准确性与格式保真 | `D:\AI\ragflow2\docs\superpowers\specs\2026-09-12-template-detect-accuracy-design.md` | ★ 范本 AI 识别三层改造：识别后处理（标签型 anchor 收缩修正/丢弃/low_confidence 警示）+ 跨 run 区间替换保格式 + B端预览划选手动标记（预览透传 addr）；已完成编码+审查（+16 对抗测试），已部署 2026-09-12 |
| 范本填写后台化与断连重连 | `D:\AI\ragflow2\docs\superpowers\specs\2026-09-12-template-fill-detached-task-design.md` | ★ 对话/流程填写执行与 SSE 连接解耦（方案A）：节点委托 tpl_fill_task 后台线程+观察者轮询+progress 重连端点+前端轮询 hook；已完成编码+两道审查（未部署，部署须 executor/spawn/template_api/template_fill_service/template_fill.py 成套 SCP + 前端 build） |
| 对话文档按节局部重写 | `D:\AI\ragflow2\docs\superpowers\specs\2026-09-12-chat-doc-section-rewrite-design.md` | ★ C端对话内说「重写第N节」→ DocumentRewrite 工具（outline/rewrite/versions/rollback）对成稿 docx 按节 LLM 重写保格式（heading切节/段落区间替换样式拷贝）+ doc_rewrite_version 版本链可回退 + downloads 持久化/历史恢复/recent_downloads 上下文；已完成编码+两道审查（13 套件 394 passed），实施计划 docs/superpowers/plans/2026-09-12-chat-doc-section-rewrite.md（未部署，后端 7 文件成套 SCP + 前端 build，见 CHANGE.md 当日条目） |
| 范本 AI 识别加固 | `D:\AI\ragflow2\docs\superpowers\specs\2026-09-13-template-detect-hardening-design.md` | ★ `_build_addr_map` 单点编址扩展打通页眉/页脚/文本框/内容控件（识别+预览+替换+标蓝全链路，hdr:/ftr:/tx 前缀向后兼容）+ occ 语义支持同段同形留白多点 + LLM 解析三级容错 + 前端低置信文字徽标；已完成编码+测试，**已部署 2026-09-14**（后端 4 文件成套 SCP + 前端 build，见 CHANGE.md 当日条目） |
| 范本库 PDF 上传适配 | `D:\AI\ragflow2\docs\superpowers\specs\2026-09-14-template-pdf-upload-design.md` | ★ 范本上传支持 .pdf：入口格式归一化（转 docx 后全链路按 docx，与 .doc 同构）+ 前端 accept/文案 + 扫描件 0 候选兜底文案；转换引擎 2026-09-14 实测后由 LibreOffice 替换为 pdf2docx（见 CHANGE.md 当日「PDF→Word 转换引擎替换为 pdf2docx」条目）；2026-09-19 加 `parse_stream_table=False` 根治正文被伪表格切碎（伪表格 325→78、'_' 存活 63%→91%，见 CHANGE.md 当日条目，**已部署 2026-09-19**，未 commit/push） |
| 填写点段落哈希直定位 | `D:\AI\ragflow2\CHANGE.md`（2026-09-15 条目） | ★ B端点击行跳转错位根修：同形留白全文顺序分配改**段落哈希直定位**——后端 `compute_anchor_positions` 附加 p_idx/p_hash/a_occ/p_total 4 元数据（docx_utils.py 双通道 FNV-1a 指纹，与前端 `fnvHash32x2` 常量必须一致），前端按指纹精确到段+段内序号精确到留白+canon 等长空白回退（w:tab→\u00a0）；真实范本 290 锚位置正确 122→275/WRONG 0；同日后续：确认视图行补中文名+key 两行显示（修预览 {{key}} 徽标无法与列表对号）+ 点击定位红色脉冲闪烁；**前后端均已部署 2026-09-15**（剩 14 项历史坏锚需重新 AI 识别） |
| B端范本预览保真渲染 | `D:\AI\ragflow2\docs\superpowers\specs\2026-09-14-bend-template-fidelity-preview-design.md` | ★ B端范本详情预览双模式：默认保真视图（docx-preview 渲染 original 原件 + 填写点锚文本琥珀高亮，复用 c-chat/docx-highlight）+「文本模式」保留划选标记；渲染失败自动降级；xlsx 不动；2026-09-15 后续迭代：填写点列表双模式（确认/编辑）+点击行定位+「未定位」徽标，及「全部未定位」首挂载竞态修复（blob 缓存+anchors 签名重渲染），见 CHANGE.md 当日条目（**已部署 2026-09-15**，纯前端）；2026-09-16 跳转定位漂移根修——懒渲染估算高度+scrollIntoView 平滑滚动致「越往后越偏」，改共享 instantFocusScroll 强制渲染目标页+瞬时居中（C端实时预览 focusPlaceholder 同修，**已部署 2026-09-16**，纯前端 3 文件） |
| 范本填写未填充汇总与定位跳转 | `D:\AI\ragflow2\docs\superpowers\specs\2026-09-14-template-fill-unfilled-summary-design.md` | ★ 范本填写终态派生 unfilled（values 留空判定，无需新持久化）→ filled 事件/progress 端点透传 → 成稿行内联汇总（必填红标/选填灰）→ 点击打开 LivePreview scrollIntoView+闪烁定位（docx span 映射 / data-ph-key 兜底）；前后端可独立部署（已完成编码+测试，**已部署 2026-09-14**：后端 3 文件成套 SCP + 前端 build） |
| 确认卡填写项点击定位跳转 | `D:\AI\ragflow2\docs\superpowers\specs\2026-09-14-confirm-card-locate-design.md` | ★ confirm_pending 确认卡每填写项字段名可点击，复用未填充汇总同款 liveTarget+focusKey 定位链路打开 LivePreview 滚动闪烁；纯前端 2 文件（已完成编码+测试，随 2026-09-15 build 上线） |
| 多范本命中暂停询问选择 | `D:\AI\ragflow2\CHANGE.md`（2026-09-15「C端多范本命中暂停询问用户选择（智能折中）」+「多范本选择卡不可见根修」条目） | ★ LLM 选出多个范本时暂停询问：先推 selected 再推 select_pending 确认卡（复用 confirm_pending SSE+Redis+POST 回传同构骨架，新增 select-confirm 端点+运行级 nonce），勾选≥1 后二次 selected 整体替换、超时 600s 按 AI 初选继续；单选不打断；**已部署 2026-09-15**；同日根修：等待期 30s SSE 心跳保活（防 Nginx 空闲超时掐断）+ 刷新回放条件保留挂起卡（挂起事件为最后一条时恢复可交互）+ 选择卡蓝色强调 + 挂起卡出现时滚动定位（**已部署 2026-09-15**：后端 template_fill.py SCP+重启+前端 build） |
| 流程对话发送即存 | `D:\AI\ragflow2\CHANGE.md`（2026-09-15「流程对话发送即存+完成回填」+「流式事件增量同步」+「流程切换保持进行中对话状态」条目） | ★ 流程 AI 对话发送瞬间预落「（生成中…）」占位记录（修刷新丢本轮），流式结束按 record_id 回填（add_ai_record 新增 record_id+save_as_version=false 更新路径：非本人 403/跨流程 404/空 response 101），失败分支标「本轮未完成」；新增 update_content（None=不修改）+ test_flow_ai_record_update.py 12 用例；**已部署 2026-09-15**；同日追加（**均已部署 2026-09-15**）：流式事件防抖 2s 增量同步预存记录+重放条件剥挂起确认态（修中途刷新丢范本进度）+ 字段确认卡默认折叠；流程切换常驻挂载隐藏显示（keptIds=选中∪对话进行中，切流程不中断 SSE、切回恢复现场）——填写阶段刷新可轮询重连，确认阶段刷新回放条件保留挂起卡可在超时前继续提交 |
| 范本填写快照化恢复加固 | `D:\AI\ragflow2\docs\superpowers\specs\2026-09-16-template-fill-snapshot-hardening-design.md` | ★ 修两大不稳定症状：①刷新后确认卡复活可重复填写——画布节点全程维护 Redis 运行快照键 `tpl_fill:run:{canvas_task_id}`（过程 7200s/终态 600s TTL），新端点 `GET /template/fill/fill-run/<id>/snapshot` 按它组装权威态（越权整体隐藏+终态桥接同 progress 口径），前端刷新恢复改「事件序列发现 run id → 拉快照整体覆盖重放态 → 未完结 3s 轮询」，键过期挂起卡标灰，事件序列降级为旧数据兜底；②预览 OOM 崩溃——隐藏 FlowDetail 强制收预览（visible 透传 forceClosedLivePreview，至多一棵大文档 DOM 树）+ blob>2.5MB 默认文本预览可显式切保真；已完成编码+428 测试全绿+build 通过（**后端已部署 2026-09-16**：2 文件成套 SCP+重启+冒烟通过；**前端未部署**：build 产物待上传） |
| 范本填写增量模式 | `D:\AI\ragflow2\docs\superpowers\specs\2026-09-16-template-fill-incremental-design.md` | ★ 同范本有 done 成稿 → 走增量而非从头填充：节点检测 `TplFillTaskService.latest_done` 取 baseline + 1 次 LLM `extract_patch_values` 抽 `{intent, direct, changed}`（intent ∈ patch/refill/fill_unfilled/noop）→ 增量分支确认卡只列 patch 项、task 只检索该项、其余沿用 baseline；合并优先级 `LLM/检索 → 直填 → baseline → default_value`；跨版本 baseline 丢弃走全量；refill/noop 防御；466 测试全绿（**未部署**：后端 2 文件成套 SCP+重启，前端确认卡 UI 待适配增量视觉） |
| 范本填写已填字段中文名 | `D:\AI\ragflow2\docs\superpowers\specs\2026-09-17-template-fill-filled-names-design.md` | ★ 解「第一轮填完后说『把 XX 改成 YY』LLM 识别不到」：①新增 `derive_filled` 终态下发 `filled`（已填字段 `[{key,name}]`，用「有 key 项 − derive_unfilled」减法构造保证与 `unfilled` 穷尽且互斥，并集即全量 key→中文名，不引入第二真源）；②成稿行新增折叠「已填充 N 个填写点」清单（展开才挂 DOM，点击中文名走既有 liveTarget+focusKey 定位链路）；③docx 保真预览/live-preview 穿透 `names`（已填悬浮 title 显「中文名（key）」、未填槽位正文显中文名而非英文 key，`data-ph-key` 不动），抽纯函数 `describePlaceholderSpan` 四象限可测；④`PATCH_EXTRACT_SYSTEM` 放宽为可按已填旧值 `current` 定位（定位优先级 name → key → current），配确定性闸 `current_ok`（**单字符** `MIN_CURRENT_MATCH_LEN=2` / **精确重复或互相包含** / 长度 ≥ `DEFAULT_HINT_MAX` 一律 false，宁漏不误），三层兜底不变；纯增量字段前后端可独立部署；257 后端 + 75 前端用例全绿，收口审查 1 Major（子串包含歧义）+ 3 Minor 均已修复（**已部署 2026-09-17**：后端 3 文件成套 SCP+重启，前端 build；生产 18 条真实任务验证 `filled∪unfilled` 恰好划分 244 个填写点、零重叠零缺口） |
| 范本写回改按钮触发 | `D:\AI\ragflow2\docs\superpowers\specs\2026-09-17-template-fill-manual-sediment-design.md` | ★ 解「范本被 LLM 填过一次，下轮新流程产出就是上次的内容（像是占位符被覆盖）」。**真实机制非文件覆盖**：成稿按任务独立（`v{ver}_result_{task.id}.ext`），范本文件与 `{{key}}` 从未被改写；真正被写的是版本行 `placeholders[].default_value`——`executor` pipeline 第 ⑦ 步与工具 `modify` **无条件**沉淀每轮全部非空产值（`source=auto`）→ 下轮 `default_map` 非空 → 确认卡只预判少数 → 未勾字段用上轮值回填 → 检索+LLM 几乎不跑。改动：①**删除两处自动沉淀**（默认值从此只由人工按钮产生）；②Service `_sediment_into_placeholders` 加**必填** `only_keys` 硬闸（刻意不给默认值——`None`（不限）与 `set()`（一个都不写）语义天差地别，留默认值就是给「静默全量写」留后门）；③新端点 `POST /template/fill/fill-task/<id>/sediment`（owner+状态+**保留键存在性**三重闸，B端/对话直发任务无确认记录一律拒，故意不退化全量；白名单 = `_changed_keys ∪ _direct_values`（必须取并集，`_llm_fill_items` 把直填键从前者剔除）；noop 轮空集 → `written=false` 不碰 DB；显式 `isinstance` 判型避免 `_changed_keys` 传字符串时被逐字符当 key）；④C端成稿行（对话页+流程页签共用组件）新增独立子组件 `TemplateFillSedimentButton` 四态按钮，`t.task_id` 存在才渲染。**行为变化**：不再自动沉淀 ⇒ `default_map` 更常为空 ⇒ **确认卡出现频率下降**，首轮全量走检索+LLM（正是用户要的「不点按钮就按当前流程内容展示」）。**收口审查** 1 Major + 5 Minor 全处置（详见设计稿 §7）：Major = **保留键闸可伪造**（REST 入参同样能带保留键 → 任意 key 可写进 `default_value`；审查建议的改用 `source=="canvas"` **不成立**——`source` 也是请求体字段）→ 改在 `create_fill_task` **剥离 `CANVAS_RESERVED_KEYS`**，使「带保留键 ⇔ 画布节点写入了确认决策」成为不变式（零副作用：key 校验要求字母开头，`_` 前缀键不可能是合法 param 键），顺带堵上同源的**既有**缺口——executor 的 `is_canvas` 门控此前同样可被 REST 调用方伪造；`find_running` 补限 `source="canvas"`（否则会复用对话/B端中间态行，致画布确认决策整批丢弃 + 写回按钮必然报「缺少确认记录」）；`empty` 文案放宽以覆盖三种成因；`_modify` docstring 改正；并发丢更新记入 `sediment_defaults` docstring 为已知；补前端按钮 6 例 + 顺带修一处既存测试腐坏（确认卡默认折叠后 5 例全红）。624 后端 + 76 前端用例全绿（**已部署 2026-09-17**：后端 4 文件成套 SCP + md5 四文件一致 + 重启 + import 冒烟（`sediment_defaults` 签名确认 `only_keys` 无默认值）+ 残留检查全 0；前端 `npm run build` 1m37s + tar 39M 就地解包 + nginx reload；HTTP 冒烟首页 200、主 chunk 命中新按钮文案、新端点无 Authorization 头回 401。**未 commit、未 push**。部署坑：API 前缀是 `/api/v1`（`api.ts:2`），误用 `/v1` 得 404 会误判成「未注册」——用已有端点同前缀做对照探针即可分辨） |
| 就地修改可见性修复 | `D:\AI\ragflow2\docs\superpowers\specs\2026-09-17-template-fill-modify-visibility-design.md` | ★ 解用户实测两个症状：①填写完成后**页面整块空白**、「查看填写内容」按钮消失（刷新重进才出现）；②LLM 说「已全部改为 B」但预览**文案没变**。**第一性原理判据（决定修复方向）**：「查看填写内容」预览**根本不是成稿**——是「范本**工作副本**（含 `{{key}}`）+ 前端 `tpl.values` 覆盖渲染」，屏幕文字完全由前端 state 决定 ⇒ **只修后端字节不可能修好症状②**。**两症状四根因、横跨四层，任一不修症状仍成立**：**(1) 前端状态层** `handleSend` 无条件清空 `templateFillRef`/`templateFillEventsRef`/`lastTemplateFill`，而 `modify` 轮**不发任何 `template_fill_progress` 事件**（走工具回执不走 SSE 管道）→ 无人回填 → 卡片整块消失；「刷新能恢复」反向证明数据一直在、只是本地状态被清掉 ⇒ 改为**发送时不清范本快照**（真填写轮由 `streamState.templateFill` 在同一 commit 内覆盖；`fileReviewRef=null` 不动，文件审核确为每轮重发）。**(2) 工具语义层** `detail` 的 keyword 只匹配 中文名/key/锚文本，用户给的是**值** → 定位不到 → 挑了名字最像的 key（实测该文字实为 `project_owner（项目业主）` 的值，模型却改了 4 个同名「招标人名称」——**LLM 回执没撒谎，用户看到的是「没改对」**）⇒ 新增**第 4 匹配通道** `_current_render()` 在**当前成稿值**中匹配 + 命中回显「当前值」自证；`detail`/`modify` 支持 `task_id` 显式钉住；工具描述 3 处写明「用户给的是内容而非字段名时先 detail 反查 key」。**(3) 前端渲染层** 新增 `fetchTemplateFillTaskProgress(task_id)`，预览**打开时**拉一次权威 values（**仅终态**，流式期 SSE 更新鲜；失败/null 回落快照不清空）；`filled/unfilled` 按 **null 与否**判定而非 `??`；`values` 包 `useMemo` 防下游 effect 依赖每渲染必变。**(4) 存储桥接层** 成稿有**两份**（真源 `{template_id}/v{ver}_result_{task_id}.ext` vs 派生副本 `{tenant_id}-downloads/tplfill-{task_id}`，卡片下载走后者），`modify` 原先只写真源（实测字节 219617 B md5 `7c82405c…` vs 219566 B md5 `0f839570…`）；且 `template_api._bridge_download` 的进程内记忆化 `_bridged_tasks` 命中即跳过 get+put ⇒ 无法自愈 ⇒ 主成稿落盘后**同名覆盖**派生副本；**不去 invalidate `_bridged_tasks`**（对象名确定，直接写入即维持该记忆化的不变式，跨层动 REST 私有集合是分层违规）；桥接失败**不当作修改失败**但回执带 `bridge_note` 讲明。**测试新发现的既存生产 bug**（本轮唯一非由症状反推）：`_modify` 的 `dict(vals.get("render") or {})` 在 render/cells 为字符串/标量时抛 `ValueError` → 冒到 `_invoke` 变成用户看不懂的「执行失败」→ 加 `isinstance` 判型。`template_fill_tool` **67 passed** / 10 套件 **644 passed** / 前端 **5 suites 85 tests**（新增 live-preview 9 例走 xlsx 文本分支使渲染确定）。**遗留**：①已打开的预览不随同屏 modify 刷新（无「新轮」信号，需关重开）；②`latest_done` 是「租户+范本」粒度，`flow_instance_id` 全库空串（死字段）无法收窄，靠回执带 `task_id` 让误选可见；③**修复 3 无单测，须人肉验收**（完成填写 → 说「把 XX 改成 YY」→ 看成稿卡是否始终在场）（**未部署、未 commit、未 push**；部署清单：后端 `agent/tools/template_fill.py` 单文件 SCP+重启，前端 3 文件 `npm run build`+dist+nginx reload） |
| 范本预览抽屉发送后卸载修复 | `D:\AI\ragflow2\CHANGE.md`（2026-09-17「流程页签发送消息致查看填写内容抽屉卸载变白底」条目） | ★ 流程页签打开「查看填写内容」抽屉后发送任意消息，右侧预览瞬间变白底（抽屉整体卸载，露出白色版本记录面板）。根因：进度卡在 flow-detail 只条件挂载于 live 轮（`live.templateFill?.templates?.length`），而 `flow-ai-panel` 流式上报分支在新轮起手时 `streamState.templateFill` 必为 undefined → 判空卸载 → 卡内的抽屉+liveTarget+整棵 docx DOM 连带销毁，轮末 ref 装回卡片但抽屉不会重开；c-chat 无此问题（卡片挂在旧消息 `msg.templateFill` 上）。修法一行：流式上报分支 `streamState.templateFill ?? templateFillRef.current` 兜底（与 handleSend「范本快照不清」既定语义同构）。Playwright 生产复现+本地 dev 修复后全程验证通过；纯前端单文件 `flow-ai-panel.tsx`（**未部署、未 commit、未 push**；部署 = build+dist+nginx reload） |
| 成稿下载乱码修复 | `D:\AI\ragflow2\CHANGE.md`（2026-09-17「成稿下载适配：新开页签乱码改 fetch Blob 落盘」条目） | ★ 流程页签/c-chat 成稿卡「下载」按钮 `<a target="_blank">` 直开 `/api/v1/agents/download`——该端点无鉴权无 `Content-Disposition`，新页签把 docx 二进制当文本渲染成乱码。修法：新增共用 `downloadTemplateFillResult(dl)`（fetch 带 Authorization → `downloadFileFromBlob` 落盘带文件名），进度卡与 `c-chat/index.tsx` msg.downloads 两处同换。Playwright 实测落盘 219503 B 有效 docx；纯前端 2 文件（**已部署+已 commit+已 push 2026-09-17**） |
| B端模板预览回显默认值 | `D:\AI\ragflow2\CHANGE.md`（2026-09-17「B端模板预览回显默认值」条目） | ★ B端范本详情保真预览对有默认值的填写点，把高亮 mark 内锚文本/留白就地替换为默认值蓝字（{{key}} 徽标保留、悬浮看全值）——写回范本库的效果在预览文件里直接可见。要点：mark 清空后按 `{{key}}` 前缀校验移回徽标（run span 文本藏元素里，逐文本节点清理不可靠）；anchorsSig 加 defaultValue 触发重渲染。实测 113 点回显 51、徽标零丢失；纯前端 2 文件（fidelity-preview + detail，**未部署、未 commit**；部署 = build+dist+nginx reload） |
| 范本删除级联清理填写任务 | `D:\AI\ragflow2\CHANGE.md`（2026-09-17「范本删除级联清理填写任务」条目） | ★ 解「C端流程删完了 B端范本仍删除不了」：流程删除（delete_flow 硬删）从不回收 tpl_fill_task，且任务行与流程无可靠外键（flow_instance_id 历史全空串）→「流程删掉任务跟着删」数据上走不通。改法：delete_template 删除守卫从「有任务拒删」改为**级联删除全部填写任务行 + 成稿对象**（真源 {template_id}/{result_file_id} + 派生副本 {tenant}-downloads/tplfill-{task_id}），published 须先停用守卫保留，has_tasks 方法删除，事务内裸删/行锁/线上500教训全保留；前端删除确认文案同步。106+11 测试全绿（**未部署、未 commit、未 push**；部署 = 后端单文件 template_fill_service.py SCP+重启 + 前端 index.tsx build） |
| 就地修改后未填充汇总不刷新 | `D:\AI\ragflow2\CHANGE.md`（2026-09-22（三）条目） | ★ 解「modify 改了字段、成稿内容变了，但进度卡『390 个未填充』里仍有该字段」：后端 DB 与 derive_unfilled 全对（387），断层在前端——useTemplateFillTaskPoll 终态即永久停轮询，而 modify 只回写 DB 不发 SSE 事件，卡片清单停在填写完成时刻。修法 = 终态 filled 行 10s 低频权威轮询（独立 refreshOverrides 槽只合 unfilled/filled 两键，status/download/values 永不降级；refresh 通道判定前置于 stopped 迟到防护）；纯前端 2 文件 + 5 对抗用例，全量 340 passed（**已部署 2026-09-22 + push 7b7f3630**：build+dist+nginx reload，chunk md5 双端一致） |
| 就地修改产值口径根修 | `D:\AI\ragflow2\CHANGE.md`（2026-09-17「就地修改产值被 Redis 旧快照遮蔽」条目） | ★ **对上一条「就地修改可见性修复」症状②的最终定案**：用户报「**改了个屁**，之前都是好的，现在 llm 光说改好了，**是不是改的文件不是一个？**」——**用户假设被生产数据证伪**：真源与派生副本两份成稿均 219503 B、**md5 完全一致**（`c7c1b9c14c49`）且都含新值「李港111」，文件层从来没错。**真正根因在读取侧口径**：`tpl_fill_progress:{task_id}` 的 Redis 进度快照停留在**填写完成时刻**（早于就地修改），而 `FillTemplate(action=modify)` **只回写 MinIO+DB、从不碰 Redis** ⇒ 修改前旧值持续遮蔽新值。而 `template_api.py` 的**两处读取点**（`build_progress_payload` / `build_run_snapshot_payload`）都**无条件快照优先** ⇒ 预览打开拉权威值 + 刷新恢复重放**两条链路全被遮蔽，无任何路径能显示新值**；这也**解释了上一批为何没修好**（`b1867dff` 新增的预览打开 fetch 正是打在这个被遮蔽的端点上）。**修法**：新增纯函数 `resolve_progress_values(status, task, snapshot)` 作为两处读取点**共用的唯一权威口径**，判据用**状态口径**（复用既有明文语义 `TERMINAL_TASK_STATUSES`）而非时间戳（时间戳等价性依赖「DB 每次写都刷新 update_time」这一未在模型层强制的约定，判据越少越不易腐坏）：非终态 → 快照优先（DB 行要到终态才写 values，原行为不变）；终态 → **DB 行权威，快照只配补缺不配遮蔽**（DB 无值才退快照；空 dict 仍走快照分支以保住「全部未填」派生）。**测试**：两套件新增事故回归闸（终态 DB 压过快照 / DB 无值·空 dict 退快照 / 权威跟随**生效状态**而非 DB 状态 / 非终态反向保护），并**反转**既有 `test_snapshot_values_authoritative`——该用例把「终态快照优先」**契约化**了、正是事故成因，改名 `test_terminal_derivations_follow_db_render_not_snapshot`；11 套件 **666 passed**。**生产实测（修前基线）**：容器内直调真实端点函数 + 真实 DB 行 + 真实 Redis 快照 → 返回 `''`（DB 为 `'李港111'`），断言失败 ⇒ 与用户现象吻合。**遗留**：①已打开的预览不随同屏 modify 刷新（需关重开）；②运行快照终态 TTL 600s，事件重放兜底在预览打开拉取纠正前可能短暂显示改前值（**未部署、未 commit、未 push**；部署清单：后端单文件 `api/apps/restful_apis/template_api.py` SCP + 重启） |
| 文件审核全链路 | `D:\AI\ragflow2\docs\superpowers\specs\2026-09-16-file-review-node-design.md` | ★ C端对话工具/流程页/画布节点三入口共用的投标文件格式审核：3 表（template/round/annotation，**无 task 表**，task_id 是轮次行外键）+ 5 端点轮询读模型（templates/state/fix/annotation-status/download，**不做进度 SSE**）+ 多轮状态机（reviewing→annotated→fixing→done，`MAX_FIX_ROUNDS=3`，`fix_rounds_left` 按轮次派生不落库）+ docx 保真区间替换 + 进度卡 `file-review-progress`（c-chat 与 flow 共用）；收口审查修复 4 个 Major：成稿下载改 fetch 手挂 Authorization 取 Blob（`@login_required` 不从 cookie 兜底，直链必 401）、前端 canFix 镜像服务端闸门（修复轮终态是 done，旧写法致第 2 轮起按钮消失）、`admit_fix_round` 进程级锁（三入口「校验—定轮号—建轮次—起线程」整段互斥防僵尸 fixing 轮）、画布 `Operator.FileReview` 前端注册补齐；114 后端用例全绿；同日再出收口遗留修复批次（见 CHANGE.md 2026-09-17 第二条）：Service 层 `is_stale_running` 三判据派生「中断轮次」→ `rounds[].stale` 透传 → 前端停轮询/不转圈/显示「已中断」并引导重新发起，`admit_fix_round` 新增 stale 闸门（**顺序必须在 running 之前**），`no_pending` 富文案与 severity 助手下沉 Service 层两入口逐字共用，卡片新增 `current.error` 失败原因；**R-1 只做一半**（不做启动期扫描，已中断轮次不自愈，补全需改上游 `api/ragflow_server.py`，未授权）；R-6/R-7/R-8 仍只记录；卡片 Popover 补显服务端拒绝文案 + 轮询判定抽成 `shouldPollFileReview` 纯函数；337 后端用例全绿 + 前端 2 套件 16 用例（本地 jest 脚手架 `.scratch/jest*.cjs|ts` 已打通，**仓库 `web/jest.config.ts` 对 `umi/test` 的依赖仍未修**，全量另 3 个失败套件均属测试腐坏、与本功能无关）（**已部署 2026-09-17 并 push**：服务器此前对 file_review 零部署，故按 T17 清单成套 SCP **10 文件**（含 `api/db/db_models.py` 三表+seed，覆盖前 `diff --strip-trailing-cr` 验证为纯新增 +253/-0）、md5 十文件一致 + 容器重启 → 前端 `npm run build`（1m50s）+ dist 39M 上传 + nginx reload；5 端点无 Authorization 头全部 401；两处计划偏差（`aggregate_chunks` 实为 `aggregate_references`；`curl -w %{http_code}` 打 POST 会假报 200 须用 `-i`）见 CHANGE.md「部署实测」；**T17 Step 7 人肉浏览器验收 9 条未执行**，留待用户）；2026-09-18 加固批次（R-1 中断轮次惰性自愈 + R-6/R-7/R-8 + 前端迁 Vitest）设计 docs/superpowers/specs/2026-09-18-file-review-hardening-design.md，见 CHANGE.md 2026-09-18 加固条目（**未部署、未 push**） |
| 范本识别 run 层切位 | `D:\AI\ragflow2\docs\superpowers\specs\2026-09-18-template-detect-blank-slot-design.md` | ★ 下划线场景识别加固：run 层确定性切位（w:u 留白 run+字符下划线）+ LLM 降级为位编号语义标注 + 漏标位确定性兜底 + `_verify_slot_occ` occ 校验闸，根除拆碎片/漏识别/重复错位/提示语污染；实施计划 docs/superpowers/plans/2026-09-18-template-detect-blank-slot.md；同日（二）上线后两症状根修——下划线 run 簇级整体分类（Word 多 run 拆提示语不再碎片化）+ 纯空白位扩展为段内最大空白 run + compute/前端 findPlainOcc 宽容回退（存量范本**无需重新识别**即修定位错位，rawResolvable 刻意 strict-only），见 CHANGE.md 2026-09-18（二）条目（**两层均已部署 2026-09-18**，未 commit/push） |
| 流程页预览抽屉 portal 根修 | `D:\AI\ragflow2\CHANGE.md`（2026-09-18（四）条目） | ★ 解「流程页点击已填充字段预览抽屉不出现（被内容长度撑走）」：`.cs-page-enter` 动画 fill-mode:both 永久保留 identity transform → 成为 fixed 后代包含块，内容再把包含块撑宽到 9844px → 抽屉被定位到屏幕外；修法 = 抽屉 `createPortal` 到 body（视口级覆盖层不依赖祖先无 transform）+ 未填充汇总改默认折叠与已填充同级别；纯前端 2 文件（**未部署、未 commit**；部署 = build+dist+nginx reload） |
| 文件审核批注面板迭代（2026-09-20 八） | `D:\AI\ragflow2\CHANGE.md`（2026-09-20「追加（2026-09-20 八）」条目） | ★ 三项：①「📋 其他批注」置顶默认折叠+未定位点击兜底提示；②人工批注级别（severity 列+端点/Service 双兜底+级别选择器+严重/一般/提示徽标+人工/AI 徽标区分+flow 左下批注模块同口径）；③**flow-panel slot 内联 ref 无限循环崩溃修复**（任何有批注流程打开页签必崩，既存生产 bug，useMemo 稳定 ref 身份）；E2E 全链路验证通过；**已部署 2026-09-20**（后端 3 文件成套 SCP+重启+severity 建列/回填/落库闭环验证，前端 build+dist+nginx reload；含同三文件的（七）file_review 持久化；未 commit/push） |
| 文件审核重开批注恢复（2026-09-20 九） | `D:\AI\ragflow2\CHANGE.md`（2026-09-20（九）条目） | ★ 解「审核完重开批注不见/进度卡没了」：①面板批注来源全靠会话内存 → flow-ai-panel 与 c-chat 各加 restore effect（来源全空时按 fileId 拉现成 state 端点恢复）+ 批注 state `{fileId,annotations}` 绑定防串显；②存量记录无 file_review 列 → 时间邻近贪心匹配一次性回填 9 条（轮次与记录恒差 4~9.5s 是唯一可靠关联——审核文件不落 file 表/轮次无 flow_id/minio_path 全 NULL）；纯前端 2 文件+数据回填，后端零改动，E2E 验证 4 卡全恢复+面板 25 处标注与轮次一致；**已部署 2026-09-20，未 commit/push** |
| 边栏批注卡重复渲染修复（2026-09-20 十） | `D:\AI\ragflow2\CHANGE.md`（2026-09-20（十）条目） | ★ 解「批注统计 25 与批注列表对不上」：annotationMap 对每段落放入所有匹配批注 → matched_text 命中多段落（章标题/目录短语）时同一条批注重复生成 33 张边栏卡（「33 处已定位」超总数）+ docx 重复标蓝；修法 = claimed Set 每条批注只归属首个匹配段落；修后 24 卡 + 其他批注 1 + 统计 25/24 三者一致；单文件单处，已部署 2026-09-20，未 commit/push |
| 审核面板批注列表共存布局（2026-09-20 十一） | `D:\AI\ragflow2\CHANGE.md`（2026-09-20（十一）条目） | ★ 解「所有批注都要在列表中能看到和点击跳转」：初版做成「列表/文档」双视图切换被用户否定——要求**同时存在**+流程页签批注模块紧凑样式；终版 = 批注列表区置顶（紧凑卡：级别色边+编号圆点+级别徽标+line-clamp-2，25 条全量含未定位）+ 文档边栏批注卡/SVG 引线**常驻共存**；点击列表条目统一跳文档锚点（mark 优先/段落兜底+instantFocusScroll+ann-flash），未定位给提示条；纯前端单文件 review-panel.tsx，E2E 验证列表 25 条+边栏 24 卡同屏+跳转全链路（**已部署 2026-09-20，未 commit/push**） |
| 修复轮补丁落地率根修（2026-09-20 十二） | `D:\AI\ragflow2\CHANGE.md`（2026-09-20（十二）条目） | ★ 解 demo05「选择级别修复没修复」（第 2 轮 1/7、第 3 轮 0/6 落地）：根因 = LLM 视图（按行拼接多行文本）与 docx 落地口径（find 必须单段落内）表示断层，跨行 find 必然 0 命中；修法 = patcher `_apply_multiline_patch` 连续段落序列定位（完整行序列定位+只落变更行，孤立编号段靠后继行消歧）+ executor 双重转义归一 + 提示词明确 find 禁含换行；358 测试全绿+生产真实数据重放 1/7→3/7、0/6→2/6；剩余「目录/标题重复」「纯插入」本质不可自动改属诚实跳过（**已部署 2026-09-20**：后端 2 文件 patcher.py+executor.py 成套 SCP+md5 一致+restart+import 冒烟；未 commit/push；验证需重新发起审核） |
| 审核面板批注定位三层修复（2026-09-20 十三） | `D:\AI\ragflow2\CHANGE.md`（2026-09-20（十三）条目） | ★ 解「批注定位乱」三层纯视觉问题：①rail 卡一次性测量 vs 懒渲染页高漂移+滚动不重测（旧 drift 最高 204px）→ document capture scroll + ResizeObserver 双通道 rAF 持续重测；②A4 794px 页 vs 675px 列无缩放被两侧裁切 → `fitDocxToColumn` CSS zoom fit-width（**必须用 width:max-content 测自然宽，scrollWidth 在 flex 居中对称溢出时系统性低估**）；③前附表跨页巨页 13612px 属 docx-preview 保真局限只能缓解；纯前端单文件 review-panel.tsx，E2E 六锚点 drift 全 0+双视口 0 裁切+列表跳转链路通；**同日追加**：文件审核弹框（=「查看文件内容」共用 ReviewPanel）与范本填写预览抽屉 w-1/2→w-2/3 加宽，1920 下 1280px，A4 原尺寸 1:1 显示（**已部署 2026-09-20**：后端 7 文件 md5 双端一致零差异故纯前端 build+dist+nginx reload，生产验证弹框 1280/零裁切/深锚点 drift 全 0；未 commit/push）；**同日再追加**：弹框打开时左侧内容腾位适配——c-chat/index.tsx 两处外层 padding 50%→66.667%（弹框加宽后未跟改致右侧内容仍被盖）+ 超管全部流程页 flow-manage.tsx 补齐腾位（原完全没有：新增两 state + paddingRight 66.667% + 透传 FlowDetail 既有上报 props），E2E 弹框 left=640 下进度卡/按钮全可见、关闭还原（**已随 2026-09-21 合并批部署**；未 commit/push） |
| 文件审核批注三项迭代（2026-09-20 十四） | `D:\AI\ragflow2\CHANGE.md`（2026-09-20（十四）条目） | ★ ①跨行批注未定位治理（11/53→3）：前端 `matchMultiline` 连续段落滑窗+唯命中闸（与（十二）后端 `_apply_multiline_patch` 同构的表示断层另一半）+ `highlightDocxRanges` 序列通道（`lines` 字段、各行 mark 共用同一 `data-anchor-key`，多命中/跨 HF 跳过仅 text 首行兜底）；②AI 批注硬删：`POST /file/review/annotation/<id>/delete`（与状态修改同闸）+ `useDeleteFileReviewAnnotation` + 乐观移除失败回滚（**flow-ai-panel 必须传真 Promise**，块体不返回 Promise 会使失败无感知不回滚——E2E 实测教训）；③AI 蓝 chip `#F0F5FF/#1a66fb`、人工绿 chip `#F0F9EB/#67C23A`（rail 卡+列表区）；95 后端+250 前端用例全绿，E2E 未定位 50/53、8 跨行 key 各 2 mark、删除 404 回滚验证（**后端已部署 2026-09-20**：2 文件成套 SCP+md5 一致+restart+import 冒烟+401 鉴权，删除成功路径复验通过——POST 200 统计 53→52+刷新持久化；**前端已随 2026-09-21 合并批部署**；未 commit/push） |
| 修复轮结果反馈（2026-09-21 五） | `D:\AI\ragflow2\CHANGE.md`（2026-09-21（五）条目） | ★ 解「触发选择级别修复后毫无反馈，要一个一个对」：生产数据诊断 demo02（task 936136a0）证明修复其实生效（3 条 high 修 2，剩 1 条缺分值纯插入属（十二）既知诚实跳过），真正缺口是 UI。进度卡新增修复结果区：`fixedAnns`（status='fixed'⇔AI 修复轮成果，手动 resolved/wontfix 不污染）+ `openAnns`（open 且 source='ai'）+ `hasFixRounds`（rounds>1 才渲染）——默认「已修复 N 项/未修复 M 项+查看明细」，展开列级别徽标（fixed 徽标转绿）+issue，fixed=0/open=0 有专属文案。纯前端 2 文件，22 用例全绿（**未部署、未 commit**；遗留：fixed 是文件级累计口径、levels 未落库无法按所选级别过滤未修复） |



## Project Overview

基于 [RAGFlow](https://github.com/infiniflow/ragflow) v0.25.1 深度二次开发，聚焦**标讯（招投标）数据采集、存储、检索、知识库解析**和**投标书自动写作**。

- **GitHub**: `li625558406/ragflow` | **上游**: `infiniflow/ragflow` v0.25.1
- **本地路径**: `D:\AI\ragflow2` | **服务器**: `root@47.98.102.55` (SSH密钥: `D:\AI\konus-key.pem`)
- **详细架构**: `F:\投标项目\AI\项目架构.md` | **部署文档**: `F:\投标项目\AI\本地部署服务器.md`
- **MCP/Hermes对接**: `F:\投标项目\AI\对接API-tools.md` | **DSL画布**: `F:\投标项目\AI\分析招标文件内容 (6).json`

### 技术栈

| 层 | 技术 |
|---|---|
| 后端框架 | Python Quart (async Flask-compatible) |
| ORM | Peewee + MySQL 8.0 |
| 搜索 | Elasticsearch 8.11 (向量+全文) | MinIO (文件) | Redis Valkey 8 (缓存/队列) |
| LLM | 自研 LLMBundle 抽象层，支持 30+ 模型 (OpenAI/Qwen/DeepSeek) |
| 前端 | React 18 + Vite 7 + TypeScript 5, Radix UI + Tailwind CSS, Zustand + TanStack Query |
| Agent | JSON DSL 画布 DAG + 动态组件发现 + asyncio 并发 |
| 移动端 | Flutter 3.44 + Dart 3.12 + Riverpod + Dio + go_router |
| 部署 | Docker Compose, Bind Mount 热更新 |

---

## 目录结构与职责

```
ragflow2/
├── api/                    # Python 后端 (Quart)
│   ├── apps/restful_apis/  # 25+ REST API Blueprint (含 ★ bid_app.py)
│   ├── db/
│   │   ├── db_models.py    # Peewee ORM 模型 (~1600行, 含7张标讯表)
│   │   └── services/       # 31个 Service 文件 (含 ★ bid_service.py)
│   └── utils/              # ★ bid_tool_service.py (缓存服务层) + bid_api_client.py
│
├── agent/                  # Agent 画布编排引擎
│   ├── canvas.py           # Canvas 核心 (~800行)
│   ├── component/          # 21个画布节点 (含 ★ FanOut, Loop, Agent, LLM)
│   ├── tools/              # 30个工具插件 (含 ★ bid.py — 12个标讯工具)
│   └── templates/          # 预置 Agent 模板 (JSON DSL)
│
├── rag/                    # RAG 核心引擎
│   ├── llm/                # Chat/Embedding/Rerank/CV/OCR 模型抽象
│   ├── svr/
│   │   ├── mcp_server.py              # ★ MCP JSON-RPC Server (11个Tools)
│   │   ├── unified_crawler.py         # ★ 统一爬虫入口
│   │   ├── crawler_sites.yaml         # ★ 78个站点 YAML 配置
│   │   ├── crawler_engine/            # ★ 爬虫引擎核心包 (v2.1 三层架构)
│   │   ├── wechat_mp_crawler.py       # 微信文章采集
│   │   ├── wechat_mp/                 # 微信公众号采集模块
│   │   └── bid_sync.py                # 标讯定时同步
│   ├── flow/               # 文档处理: Parser → Chunker → Embedding
│   └── graphrag/           # 知识图谱构建与查询
│
├── web/                    # React 前端
│   └── src/
│       ├── pages/
│       │   ├── home/       # ★ B端标讯管理
│       │   ├── c-chat/     # ★ C端对话主页 (投标助手)
│       │   ├── agent/      # B端 Agent 画布编辑器
│       │   └── ...
│       ├── components/bid/ # ★ 标讯组件 (contract-list.tsx等)
│       └── services/       # ★ bid-service.ts (标讯API调用层)
│
├── bidding_app/            # ★ Flutter 移动端 (标书分析助手 App)
└── docker/                 # Docker Compose + Nginx 配置
```

---

## 投标系统 (Bid System) — 核心二次开发

### 数据库表 (7张)

| 表 | 主键 | 用途 | TTL |
|---|---|---|---|
| `bid_project` | `id` (BigInt) | 标讯/中标/合同搜索缓存 | 1h |
| `bid_project_detail` | `id`=project_id | 项目详情 + content_html | 30天 |
| `bid_project_structure` | `id`=project_id | 结构化数据 (JSON) | 30天 |
| `bid_project_file` | auto id | 附件元数据 | 永久(覆盖) |
| `bid_project_parse` | `project_id` | 知识库解析状态 | 永久 |
| `bid_enterprise_cache` | (company_name, cache_type) | 企业画像/联系人/客户/供应商 (旧, Agent工具用) | 7天/3天/1天 |
| `bid_enterprise_business` | `keyword` | ★ 企业工商信息全量缓存 (新接口, 阿里云API市场) | 7天 |
| `bid_tender_search` | `id`=sha256(projectNumber\|title) | ★ 标讯搜索 v4 缓存 (新接口, 10次API额度) | 24h |
| `bid_enterprise_parse` | `company_name` | 企业知识库解析状态 | 永久 |
| `bid_sync_log` | `id` | API同步日志 | 永久 |

### 缓存优先策略

第三方 API 按次收费，所有端点必须走 `DB 缓存 → API 降级`：

```
搜索请求 → DB 查询 (filter_valid_cache 过滤未过期)
  ├── 命中且足够 → 直接返回 (免API费)
  └── 未命中/不足 → 调第三方API
       ├── 成功 → upsert (id为主键) → 设TTL → 返回DB数据
       └── 失败 → stale fallback (返回DB过期数据)
```

**关键规则**：
- 以 `id` 为主键 upsert，API为权威源覆盖DB
- API返回camelCase (`fileUrl`)，DB存储snake_case (`file_url`)
- 返回前端**必须用DB数据**（snake_case字段名），**不能直接返raw API** — 否则前端字段匹配失败

### 20+ REST API 端点 (`bid_app.py`)

```
GET    /bid/projects                          # 列表 (缓存优先)
GET    /bid/projects/{id}/detail              # 详情v1
GET    /bid/projects/{id}/detail-v2           # ★ 详情v2 (合同正文+结构化, 30天缓存)
GET    /bid/projects/{id}/structure           # 结构化数据
GET    /bid/projects/{id}/files               # 附件列表
GET    /bid/projects/{id}/collect-url         # 原始采集源网址
POST   /bid/projects/{id}/parse               # 触发解析→KB
GET    /bid/projects/{id}/parse-status        # 解析进度
GET    /bid/projects/by-number                # 项目编号查询
GET    /bid/stats                             # 统计
GET    /bid/sync-logs                         # 同步日志
POST   /bid/trigger-sync                      # 手动触发同步
GET    /bid/areas                             # 省市联动
GET    /bid/industries                        # 行业分类
GET    /bid/contracts                         # ★ 合同/中标搜索 (1h缓存)
GET    /bid/enterprises/business              # ★ 企业工商信息全量查询 (新, 阿里云API市场, 7天缓存)
POST   /bid/tender-search                     # ★ 标讯搜索 v4 (新, 10次API额度, 24h缓存, 缓存优先)
GET    /bid/construction/projects              # ★ 拟在建项目搜索
GET    /bid/construction/projects/{id}/detail  # ★ 拟在建项目详情
```

### API 字段名约定 (CRITICAL)

```
第三方API (camelCase)  →  DB (snake_case)  →  前端 (两种都兼容)
─────────────────────────────────────────────────────────────
projectFileID          →  project_file_id   →  f.projectFileID ?? f.project_file_id
fileUrl / url          →  file_url          →  f.fileUrl ?? f.file_url ?? f.url
name                   →  file_name         →  f.name ?? f.file_name
partAInfo              →  part_a_names      →  (数组→JSON string)
```

缓存 upsert 时必须做 camelCase→snake_case 映射；返回前端时必须从 DB 查（snake_case），不能直接透传 API 响应。

---

## Agent 画布系统

### 核心组件 (21个)

| 类型 | 组件 | 用途 |
|---|---|---|
| 入口 | `Begin` | 接收用户输入，注入 sys.query |
| 核心 | `Agent` | LLM + Function Calling 工具调用，迭代推理 |
| 核心 | `LLM`/`Generate` | 直接 LLM 调用 |
| 并行 | ★ `FanOut` | asyncio.gather 多lane并发，绕过canvas直接调LLM |
| 串行 | `Iteration` | 通过canvas path循环迭代 |
| 条件 | `Switch`, `Categorize` | 条件分支/LLM分类路由 |
| 循环 | ★ `Loop` | while条件循环，子节点输出透传 (覆盖写) |
| 输出 | `Message` | 最终输出格式化 (Markdown) |
| 数据 | `VariableAssigner`, `VariableAggregator`, `DataOperations` | 变量/数据处理 |

### Agent 工具 (30个，标讯12个)

**标讯工具** (`agent/tools/bid.py`):
`BidLookupCode`, `BidSearch`, `BidSearchAI`, `BidGetDetail`, `BidGetSource`, `BidImportToKb`, `BidCheckImportStatus`, `BidSearchContract`, `BidRewriteQuery`, `BidIndustryTag`, `BidEnterpriseProfile`, `BidConstructionSearch`

**通用工具**: `Retrieval`(KB检索), `CodeExec`, `Tavily`, `DuckDuckGo`, `Wikipedia`, `PubMed`, `GitHub`, `ArXiv`, `Email`, `Crawler`, `ExeSQL`, `AKShare`, `TuShare`, `QWeather`, `Deepl` 等

### MCP Server (11个Tools, JSON-RPC over stdio)

`rag/svr/mcp_server.py` — 零外部依赖，与 Hermes 桌面应用集成。Tools: `ask_agent`, `lookup_bid_code`, `search_bid_projects`, `get_bid_detail`, `import_bid_to_kb`, `check_bid_import_status`, `search_contracts`, `get_bid_detail_v2`, `enterprise_contacts`, `enterprise_customers`, `enterprise_suppliers`

### DSL 画布 (`F:\投标项目\AI\分析招标文件内容 (6).json`)

25个节点的投标分析流水线：6 Agent + 6 Tool + 2 Categorize + 2 CodeExec + 1 FanOut + 1 Loop。使用 DeepSeek V4 Flash 模型，系统提示词定义了12个工具的使用策略和调用规范。

---

## 爬虫引擎 (Crawler v2.1)

三层架构：**Crawl → Dedup → Storage**，YAML 配置驱动，78个站点。

```
Crawl Layer: Adapter → Paginator → Extractor → AntiCrawler → List[Dict]
Dedup Layer: DedupChecker (内存+DB双层去重, O(1))
Storage Layer: bid_writer (标讯入库) + kb_uploader (KB上传) + attachment_handler (附件下载解析)
```

三层通过 `NormalizedItem` 数据结构传递，每层可独立测试。支持4种HTTP适配器 (REST API, SM4/AES加密, SPA渲染, Playwright HTTP) + 6种分页策略 + 3种数据提取器 (JSONPath, CSS, AI)。

### SPA 爬虫开发调试流程（必读）

接入 Vue/React SPA 站点（如政府标讯平台）时，按此顺序排查，避免反复试错。详细踩坑案例见 `D:\AI\ragflow2\踩坑问题清单.md` #23-#28。

**Step 0 — 先看老脚本**：搜索 `rag/svr/*_crawler.py` 同域脚本，复用其选择器/加密/字段映射，不要重新逆向。

**Step 1 — 独立 debug 脚本验证页面是否真的渲染**（在容器内执行）：
```python
# rag/svr/_debug_xxx.py
from playwright.sync_api import sync_playwright
with sync_playwright() as pw:
    browser = pw.chromium.launch(headless=True, executable_path="/opt/chrome/chrome",
                                  args=["--disable-blink-features=AutomationControlled", "--no-sandbox"])
    page = browser.new_context(user_agent="Mozilla/5.0 ... Chrome/125.0.0.0 Safari/537.36",
                                locale="zh-CN").new_page()
    try:
        page.goto(URL, wait_until="load", timeout=30000)
    except Exception as e:
        print(f"goto failed: {e}")  # ← 超时不代表页面没渲染
    page.wait_for_timeout(5000)
    print("HTML len:", len(page.content()))
    for sel in ["a.list-item", ".case-list a", "a[href*='detail']"]:
        print(f"  {sel}: {len(page.query_selector_all(sel))} hits")
```
若 HTML 长度 >30KB 且选择器命中 → DOM 已就绪，问题在 Playwright 等待策略或 extractor，而不是站点本身。

**Step 2 — 三选一排查 Playwright 等待策略**：
- `wait_until="load"` 超时 → 站点有 analytics/长连接（hm.baidu.com）阻止 load 事件
- `wait_until="networkidle"` 超时 → 同上，网络永不空闲
- ✅ 改用 `wait_until="domcontentloaded"` + YAML 配置 `transport.network_idle: false` + spa_render.py goto try/except 容错

**Step 3 — User-Agent 检查**：
- Chromium headless 默认 UA 含 "HeadlessChrome" → 被反爬识别
- `BrowserPool.new_context()` 必须显式设真实 UA（已硬编码）
- YAML `transport.headers.User-Agent` 只作用 HTTP 请求头，**不影响** `navigator.userAgent`

**Step 4 — 数据提取策略选择**：

| 场景 | 用什么 |
|------|--------|
| API 返回 JSON | `extract.type: json_path` + `fields` 映射 |
| 列表项是 `<tr>` / `<li>` 等容器，字段在后代元素 | `extract.type: css_selector` + BS4 语法 (`"a@href"` / `"@data-id"` / `".title-col"`) |
| **列表项就是 `<a>` 本身，需取自身 text + 子元素 date** | ✅ `extract.js_extract` (JS evaluate，本项目专属) |
| 详情正文用 Markdown | `detail.type: css_selector` + `content_field` |

**❌ 不要用** Scrapy 语法 `::text` / `::attr(href)` — 本项目 BS4 extractor 不支持（见踩坑 #25）。

`js_extract` 模板（参考 `rag/svr/ggzyjd_crawler.py::_extract_list_items`）：
```yaml
extract:
  type: css_selector
  items_path: "a.list-item"     # 仅用于 wait_for_selector 提示
  js_extract: |
    () => {
      const results = [];
      for (const a of document.querySelectorAll('a.list-item')) {
        const text = (a.textContent || '').trim();
        const em = a.querySelector('em, .time');
        results.push({
          title: text.replace(/\d{4}[-/]\d{1,2}[-/]\d{1,2}/g, '').trim(),
          url: a.href,
          date: em ? (em.textContent || '').trim() : '',
          id: new URL(a.href).searchParams.get('MGUID') || '',
        });
      }
      return results;
    }
```
spa_render 优先级：API captures → `js_extract` → `_extract_from_dom`。

**Step 5 — 容器内单站测试 + 入库验证**（详见下方"部署流程"）：
```bash
docker exec docker-ragflow-cpu-1 python /ragflow/rag/svr/unified_crawler.py \
  --tenant-id <TID> --kb-id <KID> --task-name test_xxx \
  --writer collection --category news \
  --date-filter 2026-07-16 \
  --script-args '{"site_id":"xxx"}'
```
验证三件事：① `[CRAWLER] Done: N new items` ② `SELECT COUNT(*) FROM crawler_result WHERE site_id='xxx'` 行数对得上 ③ KB 上传数 ≥ DB 写入数 × 80%。

**Step 6 — 开发完成后必须调用 `konus-code-review` 审查。**

### 智能采集系统部署清单（成套 SCP，不能单文件）

智能采集系统横跨 8+ 文件，部署时**必须成套 SCP**，否则会连环报错（见踩坑 #28）。改动任一文件，以下相关文件一起部署：

| 类型 | 路径 |
|------|------|
| ORM 模型 | `api/db/db_models.py`（末尾 CollectionPolicyExt / CollectionPersonnelExt + migrate_db） |
| 主表 Service | `api/db/services/crawler_service.py` |
| 扩展表 Service | `api/db/services/collection_ext_service.py` |
| REST API | `api/apps/restful_apis/collection_app.py` |
| Writer | `rag/svr/crawler_engine/collection_writer.py` |
| Storage 管道 | `rag/svr/crawler_engine/storage_pipeline.py` |
| Engine | `rag/svr/crawler_engine/engine.py` |
| Config | `rag/svr/crawler_engine/config.py` |
| Adapter | `rag/svr/crawler_engine/adapters/spa_render.py` |
| BrowserPool | `rag/svr/crawler_engine/browser_pool.py` |
| CLI 入口 | `rag/svr/unified_crawler.py` |
| YAML 配置 | `rag/svr/crawler_sites.yaml` |

**部署后冒烟测试**（所有改动文件必须能 import）：
```bash
docker exec docker-ragflow-cpu-1 python -c '
from api.db.db_models import CollectionPolicyExt, CollectionPersonnelExt, CrawlerResult
from api.db.services.crawler_service import CrawlerResultService
from api.db.services.collection_ext_service import CollectionPolicyExtService
from rag.svr.crawler_engine.collection_writer import CollectionWriter
print("all imports OK")
'
```
冒烟通过后再跑单站测试。`collection_writer.py` 顶层 try/except import 拖累问题（踩坑 #26）：若见 `CrawlerResultService not available`，单独 import 每个模块定位真正失败点。

### Worktree ↔ 主仓同步流程

默认在 `.worktrees/crawler-dev` 开发并 commit；"同步到主仓"指在主仓 `D:/AI/ragflow2/` 把 worktree 分支 merge 进当前分支。

```bash
# 1. Worktree 内提交
cd D:/AI/ragflow2/.worktrees/crawler-dev
git add <files> && git commit -m "..."

# 2. 主仓 fast-forward merge（worktree 分支 base 与主仓 HEAD 相同时）
cd D:/AI/ragflow2
# 2a. 若主仓 WT 有与即将 merge 的文件冲突的残留改动 (内容一致也算冲突):
git checkout HEAD -- <overlapping files>
# 2b. Fast-forward
git merge --ff-only feat/crawler-dev

# 3. 若有 doc 改动只在主仓 WT (如 D:\AI\ragflow2\踩坑问题清单.md):
cd D:/AI/ragflow2
git add <doc files> && git commit -m "docs: ..."

# 4. 反向同步 worktree 分支 (doc commit 在主仓产生)
cd D:/AI/ragflow2/.worktrees/crawler-dev
git merge --ff-only feat/unified-crawler-framework
```

最终 `feat/unified-crawler-framework` 与 `feat/crawler-dev` 指向同一 HEAD，worktree 与主仓工作树（除未跟踪文件）一致。**禁止自动 push、禁止自动重启 Docker。**

---

## 前端架构

### 路由分割 (CRITICAL)

```
C端 (未匹配 ADMIN_PREFIX /5d41402abc4b2a76b9719d911017c592/)
  / → c-landing (着陆页)
  /login → c-login
  /home → ★ c-chat (投标助手对话页)

B端 (匹配 ADMIN_PREFIX)
  /5d41402abc4b2a76b9719d911017c592/...
  home → ★ 标讯管理 | agent → Agent画布编辑器 | datasets → 知识库
```

C端和B端是完全独立的两套代码，修改前务必确认路由归属。

### 标讯前端关键文件
- `web/src/pages/home/bid-detail-view.tsx` — 详情页 (正文/结构化/附件三页签)
- `web/src/components/bid/contract-list.tsx` — 中标/合同列表 + 详情面板
- `web/src/components/bid/enterprise-search.tsx` — ★ 企业查询 (5 Tab: 工商信息/股东高管/变更记录/经营风险/资质信息)
- `web/src/components/bid/tender-search.tsx` — ★ 标讯搜索 v4 (搜索表单 + 无限滚动结果列表, 详情见 `D:\AI\ragflow2\二次开发功能汇总.md` 第3节)
- `web/src/services/bid-service.ts` — 所有标讯 API 调用

### 前端字段名兼容
```tsx
const fileUrl = f.fileUrl || f.file_url || f.url || '';
const fileName = f.name || f.file_name || '';
```

### 企业查询 (Enterprise Query)
- **新接口**: `GET /bid/enterprises/business?keyword=xxx` — 阿里云API市场全量工商信息
- **缓存**: `bid_enterprise_business` 表, keyword 主键, TTL=7天
- **缓存**: DB优先, TTL=7天
- **前端**: 页面初始化只展示搜索表单, 输入查询条件后触发查询
- **旧端点已移除**: `/bid/enterprises/profile`, `/contacts`, `/customers`, `/suppliers`
- **Agent工具保留**: `agent/tools/bid.py` 中旧企业画像工具继续使用 v2 API (项目关系数据)

---

## 部署

### 服务器信息
- **IP**: `47.98.102.55` | **用户**: `root` | **密钥**: `D:\AI\konus-key.pem`
- **项目路径**: `/home/bid-agent-konus/ragflow2/` | **容器**: `docker-ragflow-cpu-1`
- **SCP**: `scp -i "D:\AI\konus-key.pem" -o StrictHostKeyChecking=no <local> root@47.98.102.55:<remote>`
- **SSH**: `ssh -i "D:\AI\konus-key.pem" -o StrictHostKeyChecking=no root@47.98.102.55 "<cmd>"`

### Bind Mount 映射 (宿主机→容器)
`rag/`, `api/`, `agent/`, `common/`, `deepdoc/`, `web/dist/` — SCP到宿主机即热更新

### ⚠️ Inode 陷阱
`rm -rf dist/*` (删内容，保留inode) ✓ | `mv dist dist.old && tar` (新inode，bind mount断开) ✗

### 前端部署
```bash
cd D:\AI\ragflow2\web && npm run build
tar -czf dist.tar.gz dist/
scp ... dist.tar.gz root@47.98.102.55:/home/bid-agent-konus/ragflow2/web/
ssh ... "cd /home/bid-agent-konus/ragflow2/web && rm -rf dist/* dist/.[!.]* dist/..?* 2>/dev/null; tar -xzf dist.tar.gz && rm -f dist.tar.gz"
ssh ... "docker exec docker-ragflow-cpu-1 nginx -s reload"
```

### 后端部署
SCP 修改的 Python 文件到服务器对应路径 → `docker restart docker-ragflow-cpu-1`

### Flutter 本地开发 (Android 模拟器)

**环境变量** (已写入 `~/.bashrc`，新终端自动生效):

```bash
export ANDROID_HOME="F:/code"
export JAVA_HOME="F:/androidstudio/jbr"      # Android Studio 自带 JDK 21
PATH=$(echo "$PATH" | sed 's#/c/Program Files (x86)/Common Files/Oracle/Java/javapath:##')
export PATH="$JAVA_HOME/bin:$ANDROID_HOME/platform-tools:$ANDROID_HOME/cmdline-tools/latest/bin:/c/Users/lg186/flutter/bin:$PATH"
```

| 组件 | 路径 |
|---|---|
| Flutter 3.44.2 | `C:/Users/lg186/flutter` |
| JDK 21 | `F:/androidstudio/jbr` |
| Android SDK | `F:/code` |
| AVD | `flutter_emulator` (Pixel 6, API 36) |

**快速启动**:
```bash
emulator -avd flutter_emulator -no-snapshot-load &  # 或从 AS AVD Manager 启动
cd D:/AI/ragflow2/bidding_app
flutter run -d emulator-5554
```

**API 地址** (`lib/core/api/api_client.dart`):
- 本机无 Docker: `http://47.98.102.55:9380` (远程服务器)
- 本机有 Docker: `http://10.0.2.2:9380` (模拟器→宿主机)

### Flutter APK 构建
需要中国镜像环境变量 + Java 17 + NDK 28.2 + lucide_icons 兼容性修复。详见 `F:\投标项目\AI\本地部署服务器.md`。

---

## 关键约束

1. **禁止自动部署** — 部署/Docker重启需用户明确指示
2. **禁止修改上游核心文件** — `ragflow_server.py`, `db_models.py`, `pipeline.py`，除非用户确认
3. **禁止重启 Docker** — 用户桌面 Docker 可能未运行
4. **API 按次收费** — 必须走 DB→API→fallback，不能直接调API
5. **前端热部署** — Vite bind mount，TS修改自动生效；仅 `node_modules` 变更或热部署不生效时才 `npm run build`
6. **rm -rf dist/\*** — 绝不能用 `mv dist` 破坏 bind mount
7. **C端≠B端** — 两套独立代码，修改前确认路由
8. **字段名映射** — 缓存返回必须用DB数据(snake_case)，不能透传API raw data(camelCase)

## Konus Skills

| 技能 | 用途 |
|------|------|
| `konus` | 意图路由器 + 项目上下文 |
| `konus-code-review` | ★ 代码审查 (任何开发完成后强制调用) |
| `konus-backend-dev` | 后端 API/ORM/Service 开发 |
| `konus-frontend-dev` | 前端页面/组件开发 |
| `konus-crawler-dev` | 爬虫站点 YAML 配置/修复 |
| `konus-deploy-guide` | 部署规范 |

## 常用命令

### 开发
```bash
# 后端
source .venv/bin/activate && export PYTHONPATH=$(pwd)
bash docker/launch_backend_service.sh
uv run pytest && ruff check && ruff format

# 前端
cd web && npm run dev      # 热部署
cd web && npm run build    # 生产构建

# Docker
docker compose -f docker/docker-compose-base.yml up -d   # 仅基础服务
docker compose -f docker/docker-compose.yml up -d         # 全栈
docker logs -f ragflow-server
```

### 环境要求
Python 3.12–3.14, Node.js >=18.20.4, Docker & Docker Compose, uv, 16GB+ RAM

### 单个测试 & 覆盖率
```bash
uv run pytest test/path/test_file.py::TestClass::test_method -v   # 单个测试
uv run pytest -k "test_name" -v                                    # 按名称匹配
uv run pytest --cov=api/db/services --cov-report=html              # 覆盖率报告
```

### Ruff 配置 (pyproject.toml)
line-length=200, lint: ASYNC/ASYNC1 enabled, E402 ignored
