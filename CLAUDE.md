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
| 范本库 PDF 上传适配 | `D:\AI\ragflow2\docs\superpowers\specs\2026-09-14-template-pdf-upload-design.md` | ★ 范本上传支持 .pdf：入口格式归一化（转 docx 后全链路按 docx，与 .doc 同构）+ 前端 accept/文案 + 扫描件 0 候选兜底文案；转换引擎 2026-09-14 实测后由 LibreOffice 替换为 pdf2docx（见 CHANGE.md 当日「PDF→Word 转换引擎替换为 pdf2docx」条目，代码待部署） |
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
| 成稿下载乱码修复 | `D:\AI\ragflow2\CHANGE.md`（2026-09-17「成稿下载适配：新开页签乱码改 fetch Blob 落盘」条目） | ★ 流程页签/c-chat 成稿卡「下载」按钮 `<a target="_blank">` 直开 `/api/v1/agents/download`——该端点无鉴权无 `Content-Disposition`，新页签把 docx 二进制当文本渲染成乱码。修法：新增共用 `downloadTemplateFillResult(dl)`（fetch 带 Authorization → `downloadFileFromBlob` 落盘带文件名），进度卡与 `c-chat/index.tsx` msg.downloads 两处同换。Playwright 实测落盘 219503 B 有效 docx；纯前端 2 文件（**未部署、未 commit、未 push**；部署 = build+dist+nginx reload） |
| 就地修改产值口径根修 | `D:\AI\ragflow2\CHANGE.md`（2026-09-17「就地修改产值被 Redis 旧快照遮蔽」条目） | ★ **对上一条「就地修改可见性修复」症状②的最终定案**：用户报「**改了个屁**，之前都是好的，现在 llm 光说改好了，**是不是改的文件不是一个？**」——**用户假设被生产数据证伪**：真源与派生副本两份成稿均 219503 B、**md5 完全一致**（`c7c1b9c14c49`）且都含新值「李港111」，文件层从来没错。**真正根因在读取侧口径**：`tpl_fill_progress:{task_id}` 的 Redis 进度快照停留在**填写完成时刻**（早于就地修改），而 `FillTemplate(action=modify)` **只回写 MinIO+DB、从不碰 Redis** ⇒ 修改前旧值持续遮蔽新值。而 `template_api.py` 的**两处读取点**（`build_progress_payload` / `build_run_snapshot_payload`）都**无条件快照优先** ⇒ 预览打开拉权威值 + 刷新恢复重放**两条链路全被遮蔽，无任何路径能显示新值**；这也**解释了上一批为何没修好**（`b1867dff` 新增的预览打开 fetch 正是打在这个被遮蔽的端点上）。**修法**：新增纯函数 `resolve_progress_values(status, task, snapshot)` 作为两处读取点**共用的唯一权威口径**，判据用**状态口径**（复用既有明文语义 `TERMINAL_TASK_STATUSES`）而非时间戳（时间戳等价性依赖「DB 每次写都刷新 update_time」这一未在模型层强制的约定，判据越少越不易腐坏）：非终态 → 快照优先（DB 行要到终态才写 values，原行为不变）；终态 → **DB 行权威，快照只配补缺不配遮蔽**（DB 无值才退快照；空 dict 仍走快照分支以保住「全部未填」派生）。**测试**：两套件新增事故回归闸（终态 DB 压过快照 / DB 无值·空 dict 退快照 / 权威跟随**生效状态**而非 DB 状态 / 非终态反向保护），并**反转**既有 `test_snapshot_values_authoritative`——该用例把「终态快照优先」**契约化**了、正是事故成因，改名 `test_terminal_derivations_follow_db_render_not_snapshot`；11 套件 **666 passed**。**生产实测（修前基线）**：容器内直调真实端点函数 + 真实 DB 行 + 真实 Redis 快照 → 返回 `''`（DB 为 `'李港111'`），断言失败 ⇒ 与用户现象吻合。**遗留**：①已打开的预览不随同屏 modify 刷新（需关重开）；②运行快照终态 TTL 600s，事件重放兜底在预览打开拉取纠正前可能短暂显示改前值（**未部署、未 commit、未 push**；部署清单：后端单文件 `api/apps/restful_apis/template_api.py` SCP + 重启） |
| 文件审核全链路 | `D:\AI\ragflow2\docs\superpowers\specs\2026-09-16-file-review-node-design.md` | ★ C端对话工具/流程页/画布节点三入口共用的投标文件格式审核：3 表（template/round/annotation，**无 task 表**，task_id 是轮次行外键）+ 5 端点轮询读模型（templates/state/fix/annotation-status/download，**不做进度 SSE**）+ 多轮状态机（reviewing→annotated→fixing→done，`MAX_FIX_ROUNDS=3`，`fix_rounds_left` 按轮次派生不落库）+ docx 保真区间替换 + 进度卡 `file-review-progress`（c-chat 与 flow 共用）；收口审查修复 4 个 Major：成稿下载改 fetch 手挂 Authorization 取 Blob（`@login_required` 不从 cookie 兜底，直链必 401）、前端 canFix 镜像服务端闸门（修复轮终态是 done，旧写法致第 2 轮起按钮消失）、`admit_fix_round` 进程级锁（三入口「校验—定轮号—建轮次—起线程」整段互斥防僵尸 fixing 轮）、画布 `Operator.FileReview` 前端注册补齐；114 后端用例全绿；同日再出收口遗留修复批次（见 CHANGE.md 2026-09-17 第二条）：Service 层 `is_stale_running` 三判据派生「中断轮次」→ `rounds[].stale` 透传 → 前端停轮询/不转圈/显示「已中断」并引导重新发起，`admit_fix_round` 新增 stale 闸门（**顺序必须在 running 之前**），`no_pending` 富文案与 severity 助手下沉 Service 层两入口逐字共用，卡片新增 `current.error` 失败原因；**R-1 只做一半**（不做启动期扫描，已中断轮次不自愈，补全需改上游 `api/ragflow_server.py`，未授权）；R-6/R-7/R-8 仍只记录；卡片 Popover 补显服务端拒绝文案 + 轮询判定抽成 `shouldPollFileReview` 纯函数；337 后端用例全绿 + 前端 2 套件 16 用例（本地 jest 脚手架 `.scratch/jest*.cjs|ts` 已打通，**仓库 `web/jest.config.ts` 对 `umi/test` 的依赖仍未修**，全量另 3 个失败套件均属测试腐坏、与本功能无关）（**已部署 2026-09-17 并 push**：服务器此前对 file_review 零部署，故按 T17 清单成套 SCP **10 文件**（含 `api/db/db_models.py` 三表+seed，覆盖前 `diff --strip-trailing-cr` 验证为纯新增 +253/-0）、md5 十文件一致 + 容器重启 → 前端 `npm run build`（1m50s）+ dist 39M 上传 + nginx reload；5 端点无 Authorization 头全部 401；两处计划偏差（`aggregate_chunks` 实为 `aggregate_references`；`curl -w %{http_code}` 打 POST 会假报 200 须用 `-i`）见 CHANGE.md「部署实测」；**T17 Step 7 人肉浏览器验收 9 条未执行**，留待用户） |



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
