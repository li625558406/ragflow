# CHANGE.md — 项目迭代记录

## 2026-09-22（四）批注版本维度——查看文件内容抽屉批注按被查看版本过滤 + 版本标识 UI

**主题**：流程页签两个审核入口的批注版本语义区分——「文件审核」按钮的批注针对最新审核文件（flow-ai-panel，零改动）；版本时间线「查看文件内容」的批注改为跟随**被查看版本**。

**根因**：查看抽屉（flow-detail ReviewPanel）的 `comments={commentsOf}` 跟随**时间线选中版本**过滤——点 v3 的「查看文件内容」而时间线停在 v5 时，v5 的批注被锚在 v3 的内容上（错位）。数据基础已有（flow_comment.version_id 列 + FlowCommentItem 透传），纯前端可修。

**改动**（纯前端 2 文件 + 测试）：
1. `flow-detail.tsx`：新增 `viewComments`（按 viewVersionId 过滤：该版本批注 + 流程级空 version_id）/ `viewVersionLabel`（v{n}）/ `versionNoById` 三 memo；查看抽屉 ReviewPanel 改传 `comments={viewComments}` + `versionLabel`；左下批注模块每张卡加归属徽标（版本绑定显「v{n}」、流程级显「流程」）。
2. `review-panel.tsx`：`MarginComment` 加 `version_id?`；新可选 prop `versionLabel`——标题栏显示「版本 v{n}」徽标；CommentCard/批注列表条目对 version_id 绑定项显示 versionLabel 徽标、流程级显「流程」（`versionTag` 字段）。**不传 versionLabel（文件审核入口）一切 UI 零变化**。

**测试**：review-panel-version 套件 +4 用例（流程级徽标/绑定徽标显 label 而非裸 id/不传 versionLabel 时连裸 version_id 也不误显/仅有标题徽标不渲染空壳），全量 vitest 345 passed；tsc 改动文件零错误。

**遗留**：AI 审核批注无版本维度（锚定审核 fileId），未做批注→流程版本映射（需后端落 version_id，本批未含）；抽屉内无版本切换器（切换=关掉从时间线另点版本）。

**部署**：纯前端 build+dist+nginx reload；**已部署 2026-09-22 + push f16a4a79**（首页 200 + chunk md5 本地/宿主机/容器三端一致）。

## 2026-09-22（三）就地修改后进度卡未填充汇总不刷新——终态行低频权威轮询

**主题**：解「说『把招标代理机构改成福建省品辰有限公司』，AI 回执改了 3 个 key、成稿文档内容也确实变了，但进度卡『390 个填写点未填充』里仍有『招标代理机构名称』」。用户观察到的 390 = 填写完成时刻（16:19）的终态事件快照；modify 后权威口径应为 387（差数恰为 modify 的 3 个 key）。

**根因（生产数据坐实，非猜测）**：容器内直调真实 DB——task `5ec20cfcb6…` 的 `values.render` 中 `tender_agency_name/agency_name_contact/agency_name_sign` 三 key 均已是「福建省品辰有限公司」，用真实 placeholders 重放 `derive_unfilled` = 387 项且三 key 全部判已填充——**后端数据与派生逻辑全对**。断层在前端：进度卡的 `unfilled/filled` 清单来自 `template_fill_events` 终态事件快照（`useTemplateFillTaskPoll` 只轮询 `filling` 行，终态即进 `stopped` 集合**永久停轮询**），而对话就地修改（FillTemplate action=modify）只回写 DB 行、不发任何 SSE 事件 → 卡片清单永远停在填写完成时刻。抽屉内预览内容是新的，因为 LivePreview 有自己独立的 3s 权威轮询（2026-09-17 修的「预览打开拉权威 values」正是这个）——与用户「文档内容改了但列表没更新」的观察完全吻合。

**改动（纯前端 2 文件）**：
- `web/src/hooks/use-template-fill-task-poll.ts`：终态 `filled` 行从「永久停轮询」改为 **10s 低频权威刷新**（`TERMINAL_REFRESH_MS`，filling 行保持 2s 不变；挂载立即刷一次）。刷新产物进**独立 `refreshOverrides` 槽**（只含 `unfilled/filled` 两键，缺省不下键不清 SSE 已有清单）——分槽原因：`overrides` 槽的「SSE filled 行丢弃 override」契约防的是轮询旧数据压过新 SSE，必须保留；而 refresh 槽本身就是终态端点权威派生，必须能更新已合成 filled 行的清单（这正是 modify 后刷新的唯一通道）。`status/download/values` 永不经 refresh 槽（成稿行本体不降级）；values 不在此合并——LivePreview 抽屉已有同款 3s 权威轮询做增量重涂，卡片清单只需两键，省全量产值重复传输。响应处理将 refresh 通道判定**前置于** `stopped` 迟到防护（filling 合成过终态的行 id 恒在 stopped 中，refresh 正是为它续上的路径）。非终态响应（异常竞态）忽略，终态行不降级。
- `web/src/hooks/__tests__/use-template-fill-task-poll.test.ts`：16→21 例。新增 5 例对抗：权威 refresh 更新 filled 清单且 status/download 不降级（语义翻转用例——原「SSE filled 行终态 override 一并丢弃」按新契约改写）；10s 节流边界（挂载立即刷、8s 内 4 个周期全节流、累计 10s 放行）；响应缺两键不清已有清单；running 响应竞态不降级；filling 合成终态（stopped）后行转 filled 改走 refresh 通道继续刷新。既有 16 例全保留全绿（「SSE 为准」契约只对 overrides 槽收窄、未破坏）。

**测试**：套件 21 passed；全量 vitest **340 passed**（基线 336 + 净增 4）。端到端数据验证走容器内真实 DB + 真实 placeholders 重放（见根因段），progress 端点 `build_progress_payload` 即同口径纯函数（`resolve_progress_values` 终态 DB 权威 + `derive_unfilled`）。

**部署**：**已部署 2026-09-22 并 push（7b7f3630）**：build（1m09s）+ dist 上传解包（保 inode）+ nginx reload，首页 200、新构建 chunk `index-5qdmr3QH.js` md5 双端一致（`d8cef006b570`）。修复生效后行为：对话里说改字段 → 进度卡未填充汇总 10s 内自动从 390 变 387（无需刷新/重开）。

**追加（同日生产实测返工）**：首轮部署后用户验收「未填充 387 对了」，但**已填充列表里 modify 的填充点条目在、冒号后值为空**（文档中有值）。根因 = 首轮取舍漏项：`derive_filled` 清单按后端设计**只含 `{key,name}` 不含值**，卡片值靠 `buildFilledRows` 从 `t.values` 按 key join（`template-fill-stream.ts:446`）——而 modify 补填的 key 在 SSE 累积的旧 `values` 里不存在，首轮又刻意没把 `values` 纳入 refresh 槽。修法 = refresh 槽扩为 `unfilled/filled/values` 三键（终态响应本来就带 values，零额外请求成本；缺省不下键防御保持）。测试 21→22 例（新增 values 合并用例 + 权威刷新用例补 values 断言），全量 **341 passed**。（**已部署 2026-09-22 并 push 5792a711**：build+dist+nginx reload，chunk `index-1Tbj5QCa.js` md5 双端一致）


**主题**：版本行铅笔编辑从 Lexical 纯文本旧段落视图换成 docx-preview 保真树上的段落级 contentEditable 编辑，格式所见即所得。v1 限制段内文字与表格单元格修改，拦截一切结构性变更（分段/并段/跨段删除/拖放）。实施计划 `docs/superpowers/plans/2026-09-22-flow-fidelity-edit.md`（6 Task 全完成）。

**改动（纯前端 3 文件）**：
- `web/src/pages/c-chat/docx-fidelity-edit.ts`（新增，核心）：严格对齐层 + 块抽取 + 编辑守卫三段。**对齐口径复刻后端 `_build_para_map`**（`rag/app/naive.py` to_paragraphs）：section 直子 article 的 P/TABLE 按文档序收集、空段不占 index、整表占一个 index、`w:sdt`（目录内容控件）后端整块跳过而 docx-preview `parseSdt` 展平无痕迹 → **模型驱动双指针对齐**（模型段按序在 DOM 找同类型+归一化文本相等段；DOM 多余段只允许「目录形态」（`docx_toc{N}` 类或全部文本在内部锚链接里）跳过，其余整体 fail 回退 Lexical——宁可不编辑不能改错段）；目录形态段即便匹配上也强制只读（readOnlyEls，杜绝「改正文写到目录条目」歧义）。canonical 技巧（归一化相等取模型原文）根除 tab/\u00a0/br 渲染差异幻影 diff。**vMerge 幻影抑制**：python-docx `r.cells` 对垂直合并 continue 位置返回 restart 格文本（naive.py 基线 HTML 表头逐行重复），docx-preview 渲染 continue 为空 td → DOM 格空 + 基线同列多行同文本 → 视为未改动（代价：放弃「清空 vMerge 疑似格」，此类格按 row/col 落地本就有歧义）。beforeinput 拦截 insertParagraph/insertLineBreak/跨段删除/段首退格/拖放；粘贴剥换行强制段内。
- `web/src/pages/c-chat/docx-edit-bar.tsx`（新增）：保真编辑工具条（portal 吸顶），改动数/结构提示/错误 + 保存为新版本/放弃。
- `web/src/pages/c-chat/review-panel.tsx`：editify effect 接线——deps `[editingFidelity, docxEpoch, docxReadyTick, fidelityNonce, content]`，`docxRenderDoneRef` 闸保证跑在渲染完成的树上（全量 renderAsync 异步逐页构建中不对齐，撞空容器必误判失败）；对齐失败 `fidelityEditBlocked` 整体转旧 Lexical 编辑视图（blob/nonce/fileId 变化复位）；放弃 = `docxMutatedRef` 置脏禁入渲染缓存 + nonce bump 强制干净重渲。

**E2E 实测（本地 dev 9222 代理生产 API，真实 315KB 投标文件 2299 模型段/36 表全对齐）**：改字 →「改动 1 处」精确；表格格改字 → tableEdits 坐标正确（paraIndex=354 row0 col0）；回车拦截+提示浮现；段首退格拦截；粘贴三行文本剥换行为一行；保存为新版本 → 后端 `document/edit` 200 → 版本时间线热现 v2 → 拉取新版本 content 验证段落与表格改动**全部落地**；放弃 → 文本恢复且编辑态保持。

**测试**：`docx-fidelity-edit.test.ts` 23 例（含 tocLike 标记、sdt 展平跳过、目录条目 readOnlyEls、vMerge 幻影抑制 3 例、结构拦截、粘贴剥换行等对抗用例）；全量 vitest 336 passed + build 通过。**已部署 2026-09-22 并 push（055810df + 4d569935）**：build（1m13s）+ dist 上传解包（保 inode）+ nginx reload，生产冒烟首页 200 + 新 chunk `index-DjzvYUlT.js` 命中「保真编辑」「仅支持段内文字修改」文案。

## 2026-09-22 流程页签智能体解耦 + 审核弹框默认编辑态

**主题**：两项前端功能首次固化入仓（此前仅存工作区，已随 2026-09-21 渲染优化批部署上线）。

**改动（纯前端 5 文件）**：
- **流程页签智能体解耦**（flow-panel.tsx + flow-ai-panel.tsx + flow-detail.tsx + c-chat/index.tsx）：流程页签不再复用 c-chat 的 `ragflow_agent_id`——flow-panel 顶栏「新建流程」按钮旁新增智能体下拉（只列名称带「流程」二字的 agent，加载失败保持「未找到流程智能体」提示态由发送守卫兜底），独立 localStorage 键 `ragflow_flow_agent_id` 持久化（savedId 失效回退首个），全面板统一一个 agent 经 props（flow-panel → FlowDetail → FlowAiPanel）下发，切换由 FlowAiPanel 自行清 sessionIdRef 防会话-agent 绑定错配；c-chat 对话页按同规则**反向过滤**（名称含「流程」的 agent 不在对话页展示）。
- **审核弹框默认编辑态**（review-panel.tsx）：新增 `defaultEditing` prop——从「编辑」入口打开面板直接落编辑视图，免去再点一次「编辑文档」；面板常驻挂载初值只在首挂载生效，故加 open 翻转复位 effect（`if (open) setUserEditing(!!defaultEditing)`）。

**部署**：随 2026-09-21 渲染优化批已上线（build 含工作区在途改动），2026-09-22 commit+push 固化。生产已验证 flow 智能体选择器代码在线上产物（`ragflow_flow_agent_id` 命中 chunk）。

## 2026-09-21（十二）审核/范本预览大文件渲染优化：渲染产物缓存+体量门槛

**主题**：用户反馈「C端流程页面的文件审核和范本填写，文件过大渲染慢」。根因三层：docx-preview `renderAsync` 在主线程同步建整棵 DOM（JSZip 解压+XML 解析+整树构建，大文件打开瞬间卡死，`content-visibility` 只省排版救不了建树）；审核面板（ReviewPanel）没有体量门槛（范本预览有 >2.5MB 文本降级，审核面板漏了）；弹框/抽屉关闭重开同一文件整本重渲染。选定方案 A（审核面板补门槛）+ B（渲染产物缓存）。

**改动（纯前端 5 文件 + 4 新测试套件）**：
- `web/src/pages/c-chat/docx-render-cache.ts`（新增，核心基建）：`stashDocxRender(blob, el)` 把渲染产物子树搬进 WeakMap<Blob, holder> 离屏缓存 + `takeDocxRender(blob, el)` 命中搬回（appendChild 毫秒级回放）+ `BIG_BLOB_BYTES = 2.5MB` 阈值单一来源（范本预览本地常量收敛至此）。设计要点：**以 Blob 对象身份为键**——版本轮换（新 Blob）天然 miss 全量重渲、React Query 逐出后整树可 GC；**树只存在一份**（不在 el 就在 holder，stash 是搬移非复制，内存不翻倍）；**LRU 上限 3**（order 引用序 + 淘汰清 holder，`firstElementChild` 判空等效失效）。
- `web/src/pages/c-chat/docx-highlight.ts`：末尾纯追加 `stripDocxAnnotationMarks`（剥 mark[data-anchor-key] 含 {{key}} 徽标 span，还原可重涂文本——缓存树带上一轮 mark，重放后须剥净重涂）+ `rebuildPlaceholderSpans`（按 [data-ph-key] 重建 span 映射——applyDocxHighlight 已把 {{key}} 原文 replace 掉无法重扫，范本预览回放走重建）。
- `web/src/pages/c-chat/review-panel.tsx`（方案 A+B）：①派生 `docxOversize`（`blob.size > 阈值` 直接算而非 state+effect——blob 到达的同一 commit 守卫即生效，不会先白渲染一遍再翻转）+ `docxForceFidelity` 显式覆盖 state（blob 换对象重置）+「文档较大，已用文本预览保障流畅」横幅与「切换保真渲染」按钮；②渲染 effect 接缓存：重置失败态**必须在 oversize 守卫之前**（否则跨文件切换残留失败横幅）→ take 命中 `stripDocxAnnotationMarks` + 重涂（回放分支同样注册 cleanup stash，否则缓存隔次生效）→ miss 走 renderAsync（`cancelled/settled/failed` 三闭包标志：堵 late resolve 清新文件树、堵在飞/失败树入缓存形成无自愈跨会话错树污染）；③补插 effect 的 fresh 过滤加 **DOM 已插判定**（`querySelector(mark[data-anchor-key])`）——回放使容器在 effect 阶段就有树，同 commit 补插 effect 闭包里 markedKeys 还是旧空 Set 会双插嵌套 mark（ref 闸方案因 epoch 双跑不可行，DOM 判定时序无关天然幂等）。
- `web/src/pages/c-chat/template-fill-live-preview.tsx`（范本实时预览同款）：本地 `BIG_BLOB_BYTES` 删除收敛到共享模块；`blobOversize` 改派生判定；渲染 effect 接缓存（回放 `rebuildPlaceholderSpans` 重建映射 → 既有 `updateDocxHighlight` effect 按当前 values/names 重涂——关闭期间 values 变更重开显示新值，有用例钉住）。
- 新测试：`docx-render-cache.test.ts`（7 例含 2 对抗：淘汰复用不残留、同 Blob 连续 stash 不叠加）、`docx-highlight-strip-rebuild.test.ts`（4 例）、`review-panel-render-perf.test.tsx`（5 例：oversize 不触发 renderAsync/切换保真/缓存回放 renderAsync 只 1 次/mark 不双插 spy 检测/在飞不入缓存）、`template-fill-live-preview-cache.test.tsx`（3 例：关开重放/派生首帧生效/values 变更重涂）。

**审查修复的关键问题**（两轮 subagent spec+质量审查）：①回放路径 mark 双重嵌套插入（缓存命中使容器在 effect 阶段就有树，暴露了补插 effect 的 stale markedKeys 竞态）；②在飞 renderAsync 错树持久化入缓存（`isConnected` 闸只覆盖重挂覆盖不了同 el 换 blob，缓存使污染跨会话持久）；③回放分支漏注册 cleanup 致缓存隔次生效+冷开双跑；④oversize 守卫在重置失败态之前 return 致跨文件切换双横幅。TDD 附带发现：范本预览旧 state 写法中范本切换 effect 的 `setBlobOversize(false)` 与 oversize 赋值 effect 同批执行时后者被覆盖——派生判定根治。

**测试**：全量 vitest 27 文件 312 passed（基线 293 + 新增 19+）；`npm run build` 通过（1m20s）。**未部署**（部署 = build+dist+nginx reload，纯前端）；未 push。LRU 不按体积加权为已知限制（forceFidelity 放行的超大文档入缓存极端下可达数百 MB，后续可按 blob.size 加权）。

## 2026-09-21（十）审核弹框手动编辑复用：对话附件/流程版本就地改文字，产出新文件自动交接 LLM

**主题**：用户提出「附件上传的文件和流程新发起时的初始文件，都能复用文件审核弹框做文本展示 + 手动修改内容，修改后的文件还能让 LLM 继续分析」。选定方案：产出**全新文件对象**（原文件字节不动）+ 编辑产出的新文件**自动进附件队列**（发送后 LLM 可见）。

**改动（后端 2 文件 + 前端 5 文件）**：
- `api/utils/docx_edit.py`（新增，共享编辑内核）：`parse_edit_payload`（edits/deletes/inserts/table_edits 对抗性校验：上限 200 ops、单文本 20000 字、runs 文本一致性、para_index/行号/列号整型、align/heading_level 枚举、控制字符拒收）+ `ensure_docx_blob`（PK 魔数直放 / OLE2 `.doc`→LibreOffice 转换 / 其他类型拒绝）+ `apply_ops_to_doc`（**先全量定位后统一应用**的事务式编辑：任一 para_index 定位失败整体拒绝、原文件不动；段落改写拷贝首 run 样式、表格 cell 改写首段清多余段）+ `edit_docx_blob` → `(new_blob, doc_title, converted)` + `safe_filename`（路径穿越清洗）。
- `api/apps/restful_apis/file_api.py`：新端点 `POST /files/<file_id>/edit`——`{tenant_id}-downloads/{file_id}` 直传通道取 blob → 内核编辑 → 存为**新 uuid 对象**，返回 `{file_id, file_name: "{原名}_编辑.docx"}`；无 DB 行、原文件不变、bucket 即租户边界（owner-gate 天然成立）。
- `web/src/services/flow-service.ts`：抽 `flowDocEditOpsToBody`（camelCase ops → snake_case 请求体，流程版本编辑与附件编辑共用同一契约）+ 新 `editFileDocument(fileId, fileName, ops)`。
- `web/src/pages/c-chat/chat-input-box.tsx`：新 `injectDoc` prop——父层驱动的队列注入（nonce 防重、removeId 剔旧、同 id 不重复追加）。
- `web/src/pages/c-chat/flow/flow-ai-panel.tsx`：`handleEditDocument` 按 `reviewSource` 分派——`version` → `editFlowDocument`（存新版本）+ 热切面板到新版本文件；`upload` → `editFileDocument` + `setQueueInjectDoc` 注入 ChatInputBox 队列（removeId 剔旧文件）+ 面板热切到新文件；`canEdit` 闸：version 需有版本对象、upload 需 .docx 扩展名。
- `web/src/pages/c-chat/index.tsx`：附件审核弹框接 `onEditDocument` → `editFileDocument` → 直接 `setUploadedFiles` 换队（c-chat 输入框队列在 index 层自管，不经 ChatInputBox）。
- `web/src/pages/c-chat/flow/flow-detail.tsx`：版本时间线行新增铅笔编辑按钮（owner + .docx 才渲染）→ 打开 ReviewPanel 弹框 `canEdit` + `onEditDocument={handleEditVersionDocument}`（`editFlowDocument` → invalidate flow-detail → 关弹框重开新版本，绕开同版本 document 复用）。

**测试**：后端 `test/test_file_edit_document.py` 新 20 例（parse 对抗矩阵／定位失败原子性「输入不动」／表格事务性／safe_filename 穿越／端点：非法 id 矩阵 [32 位非 hex、31 位、全横线、uuid+"!"]、对象缺失、空 ops、成功路径新对象可解析且原文节未动、pdf 拒收无副作用）+ 既有 flow 编辑 50 例 = 72 passed；前端 `flow-service-edit-file.test.ts` 4 例（URL/方法/snake_case 映射/code!=0 抛后端 message/file_name 兜底/流程版本回归闸）+ `chat-input-box-inject.test.tsx` 4 例（注入进队/removeId 剔旧/nonce 防重/null 不变）+ 全量 vitest 23 文件 293 passed；tsc 对改动文件零新增错误（index.tsx 1300/1301/3278 三处经 git stash 对照为 HEAD 既存）。测试基建教训：路由文件加载须在 exec_module 前 inject fake `manager`（包 init 的 F821 注入）；FakeStorage 必须**同步**方法（async 会漏进真线程池）；pytest 里 `resp.get_json()` 不可靠改 `json.loads(await resp.get_data())`；注入只进队列不渲染 DOM chip，断言经 `onUploadedFilesChange` 回调。

**部署**：**后端已部署 2026-09-21**（2 文件 SCP+md5 双端一致+restart+容器内 import 冒烟+路由确认 `/files/<file_id>/edit` 已注册+HTTP 无 Authorization 401）；**前端已部署 2026-09-21**（build 1m22s+dist 上传解包+nginx reload，首页 200、chunk md5 双端一致、静态资源 200）。**已 commit+push 95c72c0e**。

## 2026-09-21（九）原文 ⇄ AI 修改自由切换——回退后可一键恢复 AI 修改

**主题**：接（六）（八），用户反馈回退语义太死板：「可以回退，也可以再点保留，又恢复了，也就是我可以要原文内容，也可以要 AI 修改后的内容，可以自由切换」。现状：回退会**清空** patch_json，批注回 open 后再点「确认保留」只是纯状态标记，文档永远停在原文，AI 修改一旦回退就找不回来。

**方案**：patch_json 改为**永久保留**（回退不再清空），新增 `perform_reapply` 正向恢复。状态环：`fixed/resolved+patch --回退--> open+patch（文档=原文）--确认保留--> resolved+patch（文档=AI 修改）--回退--> ...` 无限双向切换，patch 是切换的唯一权威凭据。

**改动（后端 3 文件 + 前端 2 文件）**：
- `rag/svr/file_review/executor.py`：`perform_revert` 尾部不再清 patch_json（注释写明自由切换语义）；回退闸放行 fixed+resolved（open+patch 被闸住防重复回退）；新增 `perform_reapply(annotation_row)`——`_ADMIT_LOCK` 下重取行→patch 校验→取最佳修复轮 blob→**按文档状态分派**：正向 find 命中 → `apply_patches_to_docx` 应用 + 产 `kind='apply'` 轮 + 置 resolved；find 不中但逆探（replace 在文档中）命中 → 幂等（修复已在文档）纯状态翻转不产轮；两边都不在 → 诚实 101「原文已被后续修复改动，无法恢复」（自愈 legacy 脏数据）；find==replace → 零差异纯翻转不产轮。
- `api/db/services/file_review_service.py`：`fix_rounds_left` 对 kind='apply' 同 revert carve-out（恢复不是修复尝试，不烧额度）。
- `api/apps/restful_apis/file_review_api.py`：`update_annotation_status` 路由——status='resolved' 且行是 open 且 patch_json 非空 → `perform_reapply`（`asyncio.to_thread` 包裹，锁+MinIO 不阻塞 quart 事件循环）；其余组合照旧纯状态翻转。
- `web/src/pages/c-chat/review-fix-diff.tsx`：FixActions 按 status 分派——fixed→[回退][确认保留]；resolved→[回退]（确认后仍可回原文）；open+patch→[恢复 AI 修改]（按钮文案）。
- `web/src/pages/c-chat/review-panel.tsx`：AiCard 新增「已回退」灰徽标（open+patch）；FixActions 带 status 透传。

**测试**：executor 新 7 例（happy path 产 apply 轮+额度不烧／fixed→revert→reapply→revert 全环 roundtrip 断言 v3=原文 v4=AI 修改 v5=原文 共 5 轮／幂等逆探不产轮／两边都不在诚实拒绝／零差异补丁不产轮／缺 patch+坏 JSON 拒绝／resolved 也可回退）；api 新 2 例（open+patch 路由进 reapply 不走纯翻转／fixed→resolved 与裸 open 保持纯翻转不进 reapply）；前端 review-panel-version 新 describe 4 例（fixed 双按钮／open+patch 已回退徽标+恢复按钮无确认保留／resolved 仍有回退无恢复／open 无 patch 不渲染任何切换按钮）。后端 4 套件 260 passed + 前端相关 4 套件 59 passed 全绿。前端用例教训：边栏卡默认折叠（（七）追加项）+ 正文不含 matched_text 时批注落入底部兜底区无卡片——用例正文必须含 matched_text 且先点展开箭头。

**部署硬约束**：后端 3 文件（executor.py + file_review_service.py + file_review_api.py）**成套 SCP + restart**；前端 build+dist+nginx reload。反序无碍（前端按钮对旧后端报错不崩），但只部署前端不部署后端则「恢复 AI 修改」点了被 status 端点当纯翻转处理、文档不变。**后端已部署 2026-09-21**（4 文件含 patcher.py 成套 SCP+md5 双端一致+restart+冒烟：imports OK/apply carve-out/api 路由/revert 保留 patch/回退闸放行 resolved/HTTP 401 鉴权，均通过）；**已 commit+push 088f3f07**。前端未部署。

## 2026-09-21（八）回退误报「原文已被后续修复改动」根修——patcher 跨行通道降级单段

**主题**：用户回退 fixed 批注报 101「原文已被后续修复改动，无法自动回退」。生产诊断（最近任务全部 fixed+patch_json 批注，逐条用与 perform_revert 同口径重放逆补丁）坐实：该任务 5 条 fixed 中 4 条本就可回退，唯一失败的是 `ann 212b980c`——其正向 patch 是「单行 find → **含换行 replace**」（`'30分' → '28分\n（扣2分）'` 形态），replace 经 `_apply_single` 连同 `<w:br/>` 写进了**单个段落**；回退时逆补丁 find=replace 含 `\n`，`apply_patches_to_docx` 按「find 含换行 → 跨行段落序列通道」路由，序列通道要求各行分处连续段落 ⇒ 结构性 0 命中 ⇒ 误报「被后续改动」。**不是真被改过，是路由不对称**。

**改动（后端单文件 `rag/svr/file_review/patcher.py`）**：find 含 `\n` 的 patch 先走跨行序列通道（既有成功路径一字不变），失败后**降级单段通道** `_apply_single`（整段摘文物理落在一个段落里的形态：原文档 w:br 换行，或正向含换行 replace 落进单段后回逆补丁再找它）。严格增量——降级只在原先 applied=False 的地方多一次机会；`run.text`/`para.text` 对 `<w:br/>` 与 `\n` 双向一致已在本地 venv 实测（写入转 br、读回转 \n，runs 拼接与段落 text 同含 \n，`_replace_in_paragraph` 单段匹配无障碍）。既有多行用例行为全部不变（纯插入/重复行歧义/空转补丁的 fallback 均仍 0 命中）。

**测试**：patcher 45→49 passed（新 4 例对抗：单段含换行 find 降级命中／正向含换行 replace→逆补丁完整还原的 roundtrip 对称性闸／降级继承唯一性闸（同形两段仍诚实跳过）／行序倒置单段 0 命中不误救）；executor+service+api 关联 202 passed 全绿。

**部署**：后端单文件 `rag/svr/file_review/patcher.py` SCP + restart（**未部署、未 commit**，待用户确认）；生产重放已在容器旧代码上复现 `ann 212b980c` applied=False 与报错吻合，部署后同脚本应转 True。遗留：纯插入/删行型逆补丁（逆 find 含空行）仍结构性不可回退（无锚点，诚实保留手动处理路径）。

## 2026-09-21（七）审核弹框三项根修：确认保留即时联动 + 未定位有序降级 + 弹框展示成稿版本

**主题**：后端（六）部署后用户实测弹框（ReviewPanel）报三问题：①弹框点「确认保留」无任何反馈（进度卡「查看明细」点了立即变，弹框不变）；②「文档里有批注的文案，但显示未定位」；③确认保留后弹框里文档还是原文。生产诊断（最近 8 任务逐条同口径重放）定位：①面板批注是父组件 props（会话内存），mutation 只 invalidate state 查询、无人刷新 props ⇒ 永远旧值；②MISS 分三类——`multi`（LLM 复制行致序列通道多命中→-1）、`dup`/表格（重复段/表格整体一个 HTML 段落致序列通道结构性失配）、`nomatch`（`/；` 归一化后为空，诚实不可定位）；③面板渲染原始上传文件，成稿版本（`frv-{task_id}-{version}`）从不进面板——state `doc.has_result/version` 设计注释本就写着「面板必须展示最后一版」但前端从未消费。

**改动（后端 1 文件 + 前端 5 文件 + 测试 3 文件）**：
- **A 状态联动**：`review-panel.tsx` 订阅 `useFileReviewState(open?fileId:'')`（与进度卡同源同刷新），新增共享纯函数 `mergeAnnotationOverlays`（docx-view-utils.ts）按 annotation id 把 state 最新 `{status,patch}` 覆盖到 props 批注（不改父组件）；**无变化时返回原数组引用**——state 轮询重放相同数据不能触发下游 annotationMap/rail 全量重算。弹框内徽标/FixDiffView/FixActions 确认保留/回退后即时翻转。
- **B 未定位降级**：`matchAnnotation`/`matchMultiline` 从 review-panel.tsx 迁出至 `docx-view-utils.ts` 共享+可单测；matchMultiline 改**有序降级**——①序列唯命中（不变）→ ②首行通道（最长行 norm，文档序第一个命中段；与单行 dup「定位首现」同口径，多行不应反而更差）→ ③诚实未定位。**计划偏差**：原设计的第③「拼接通道」（表格）实现时证明**不可达死代码**——拼接命中蕴含段落 norm 含全部行 ⇒ 必含最长行 ⇒ 首行通道必先命中，故不设（代码留证明注释）。附带修复：annotationMap 同段覆盖 bug（多行批注与单行批注命中同段时后者被丢）改合并——降级通道增多后碰撞概率上升，必须合并。
- **C 成稿版本展示**：后端 `file_review_api.py` 新端点 `GET /file/review/<task_id>/<file_version>/content`（镜像 download 闸链：任务存在→轮次有产物→bucket→对象非空；读路径不入 owner-gate 与 download 同决策；魔数分发 PK 直接解析/OLE2 先 LibreOffice 转换，两头都解不出报错不静默降级纯文本）。前端 `api.ts` + `fileReviewVersionContent`；`use-file-blob.ts` + `useReviewVersionBlob`（staleTime 5min，(task_id,version) 不可变）；面板 content effect 有成稿时改拉版本 content，**粘性降级状态机**（`versionDegraded` 按 (fileId,taskId,version) 复位 + `contentIsVersion` 记录实际来源）防「版本 blob 失败 × 版本 content 成功」乒乓死循环与段落/blob 不同源错位；blob 双 hook 常调+enabled 门控（禁条件 hooks）合并 data/loading/error；`getLocateText(ann)`（fixed/resolved+patch→patch.replace，否则 matched_text）仅用于定位/高亮（`matchTextOf` 随 `contentIsVersion` 切换），卡片展示仍 matched_text；成稿展示时头部加「修复后成稿 {version}」徽标 + **编辑闸**（编辑流按原 fileId 落地会静默丢修复）。

**测试**：后端 test_file_review_api 49 passed（新 5 例：任务不存在不碰存储/无产物轮+空版本号同闸/对象丢失不伪装空段落成功/PK 直解成功路径/OLE2 转换后解析+转换失败不续解）；前端 docx-view-utils 17 + review-panel-version 5（overlay 翻转+空 state 不误翻/成稿优先拉版本 content+来源徽标+不拉原文件/版本 content 失败降级/无成稿行为不变）+ 既有 6 套件共 85 passed；file_review 后端全 5 套件 267 passed；tsc 改动文件零错误。setup.ts 补 ResizeObserver 守卫桩（jsdom 缺口在 harness）。

**后端已部署 2026-09-21（md5 双端一致+docker restart+import 冒烟+新端点/对照端点无 Authorization 双 401）；已 commit（5739af89）。前端未部署**——生产 dist 旧版，弹框联动/未定位降级/成稿版本展示/边栏折叠需 build+dist+nginx reload 后可见。

**同日追加：边栏批注卡默认折叠**——用户要求「文档正文的批注右侧的批注内容正文默认折叠起来，可展开」。AiCard/CommentCard 正文（摘录/问题/建议/修复对比/操作、人工批注内容）改为默认折叠只留头部行：头部尾随 ChevronDown 切换按钮（stopPropagation 防误触发定位跳转），`selected` 时自动展开（列表区跳转/正文 mark 点击后能看到内容）。E2E（dev :9222 demo01）：9 卡默认全折叠、点箭头展开+标题翻转、annotation-select 定位后自动展开+选中环；tsc 零错误+组件测试 5 例回归全绿。

**同日再追加：展开卡被下一张卡遮挡根修**——用户报「展开后，弹框被遮挡住了」。根因：边栏卡是 `absolute` 定位（top 由 measure() 防重叠算出），展开只改卡片自身高度、不改 wrap 高度 → 既有 ResizeObserver 只观察 wrapRef 永不触发 → measure() 不重算 tops → 展开卡与下一张卡重叠，后绘制的下一张盖住展开内容。修法：ResizeObserver 同时逐卡观察 `[data-card-key]` 元素（高度一变即重测；`top` 变化不改尺寸，无回环）。E2E 复验（demo01）：ai-3 展开 h=178 后 ai-4 正确下推零重叠、展开正文完整可见、SVG 引线保持吸附；tsc 零错误 + 22 用例全绿。既知既有行为（非本次引入）：锚点远超视口的卡被钳制到 wrap 底部时可能相互重叠（ai-5/8/9 同 top），与展开遮挡无关。

**遗留**：①`/；` 类纯符号摘录诚实未定位（不可修）；②目录/标题重复段多命中取首现，可能与批注真实锚定段不符（与单行 dup 同口径的既知折衷）；③已打开的弹框不随新成稿版本自动刷新（需关重开）。

## 2026-09-21（六）修复对比 + 修复标记 + 回退/确认保留——「结果可审视 + 可撤销」闭环

**主题**：接（五），用户要求「能看到修复前和修复后的对比；修复后的批注要做标记；修复的批注可以手动点击回退或使用修复后的内容」。现状缺口：落地补丁（find→replace）只写进 docx 字节，批注行没有存档，UI 无从展示「改了什么」；fixed 批注无视觉标记；修错了无法撤销。

**方案数据流**：修复轮落地时把**实际应用的补丁**存进批注行（新列 `patch_json`，在 `_patch_matches_annotation` 闸通过那一刻与 fixed 同笔写入——它是「确实修好了」的唯一权威记录）→ state 端点下发 `patch` → 前端红绿对比；回退 = 逆补丁（find↔replace 互换）应用到**最新落盘版本** → 产新版本（新轮次行 `kind='revert'`，同步终态建行不经 execute_task）→ 批注回 open + patch 清空；确认保留 = 复用既有 status 端点置 `resolved`（白名单已放行，零新端点语义）。

**改动（后端 4 文件 + 前端 6 文件）**：
- `api/db/db_models.py`：FileReviewRound + `kind`（normal/revert，默认 normal）；FileReviewAnnotation + `patch_json`（空=无修复存档）；migrate_db 追加两行幂等 alter。
- `api/db/services/file_review_service.py`：`fix_rounds_left` 对 kind='revert' carve-out（回退不是修复尝试，不烧额度；kind 空/None 存量行按 normal）；标注 `update_status` 加 `**extra`（镜像 RoundService 模式，供 status+patch_json 同笔写）。
- `rag/svr/file_review/executor.py`：`_settle_annotations` 标 fixed 时写 `patch_json`（未落地的补丁不写，open 行永远无补丁）；新增 `perform_revert(annotation_row)`——闸门链（非 fixed 拒 / 空/脏 patch_json 拒「旧版本修复未存档」/ 删除型 replace="" 拒（逆补丁空 find 会被 patcher 恒跳过，与其静默失败不如明说）/ find="" 纯插入拒）→ 进程锁 `_ADMIT_LOCK` 内取「round_no 最大且已落盘」版本为基线（**不能**用 `_latest_version_name`：它解引用 upto.id，回退没有当前轮）→ 逆补丁不命中（原文被后续轮改动）诚实拒绝且批注不动 → 先 Put 后写状态（与修复轮同顺序契约）→ revert 轮 summary=`回退批注修复：<issue截断>` → 批注 open+patch 清空（防二次回退）。
- `api/apps/restful_apis/file_review_api.py`：`_annotation_payload` + `patch`（`_json_dict` 脏值降级）；新端点 `POST /file/review/annotation/<aid>/revert`（照抄 delete 闸链；FileReviewError 业务文案逐字透传——「为什么不能自动回退」是业务结论，吞掉等于让用户重新一个个对）。
- 前端：`api.ts` + `useRevertAnnotation`（成功 invalidate state，批注自动落回未修复组）；`file-review-stream.ts` 类型 + `patch`；新组件 `review-fix-diff.tsx`（**进度卡与面板共用防漂移**：FixDiffView 修复前行红底删除线/修复后行绿底、multiline 按行渲染（后端 _apply_multiline_patch 只改变更行，行数恒相等）；FixActions 回退（window.confirm 后调 revert）+ 确认保留（status→resolved））；`file-review-progress.tsx` 修复结果区每条 fixed 挂对比+操作，resolved+patch 计入已修复并显示「已确认保留」；`review-panel.tsx` AiCard 加「已修复」（绿）/"已确认" 徽标 + 对比 + 操作（stopPropagation 防触发定位跳转），列表条目加修复小徽标（保持紧凑无操作按钮）。

**测试**：后端 3 套件新增 17 例全绿（service：revert carve-out 矩阵 + patch_json 同笔写/清；executor：补丁落库/跳过不落库 + perform_revert 全对抗（非 fixed/空 patch/垃圾 JSON/删除型/纯插入/原文被改拒绝且批注原样/无 blob/成功路径建 revert 轮+原文恢复+额度不烧+二次回退拒+**陈旧入参行并发双回退只成功一次**——锁内重取复核）；api：revert 闸链（缺失/越权/FileReviewError 逐字/内部错误脱敏）+ 路由契约表更新）——合计 311 passed；前端进度卡套件 25 passed（新增 diff 渲染/回退调 API/confirm 取消不调/已确认保留无操作按钮/无 patch 不显示对比）；tsc 零错误。本地测试库已补列（等价 migrate_db 两行 ALTER）。

**收口审查（superpowers:code-reviewer）2 Major + 4 Minor 全处置**：M-1 闸门原在锁外基于入参行判定——并发双回退等锁期间状态已被改掉，陈旧入参会把逆补丁重复应用（修复被静默前放）→ 改为 `_ADMIT_LOCK` 内按 id 重取行 + `_revert_patch_of` 基于重取行全量闸门（附对抗用例）；M-2 REST 端点同步调 perform_revert（锁等待+MinIO 往返）会阻塞 quart 事件循环、拉长锁持有致 admit_fix_round 误报 busy → `await asyncio.to_thread(perform_revert, row)`（R-6 同款结论）；m-1 kind 先建 normal 再补 revert 有窗口期（并发 state 读会把该轮误计额度）→ `create_round` 加 `kind` 参数随建行一次写齐；m-2 建轮 tenant 以**轮次行**为权威（best.tenant_id，标注行 tenant 历史脏空串不作依据）；m-3 错误码口径（ARGUMENT_ERROR vs OPERATING_ERROR）与既有 fix 端点保持一致，不动；m-4 `_round_payload` 不透出 kind（前端无消费方），不动。

**后端已部署 2026-09-21 并已 commit+push（ec072ab3）**：4 文件成套 SCP + md5 双端一致 + docker restart（migrate 自动建列：kind/patch_json 验证在位）+ import 冒烟 + revert 端点无 Authorization 401（注册且鉴权正常）。**前端未部署**——生产 dist 旧版，对比/徽标/回退按钮需 build+dist+nginx reload 后用户才可见（部署硬约束已满足：后端先行）。

**遗留**：①存量修复（本批之前已 fixed 的批注）无 patch_json，不显示对比/回退（徽标也不显示——`patch` 缺失即旧数据，如实呈现）；②删除型修复与纯插入型修复不可自动回退（逆补丁数学上不可行），文案已引导手动处理；③并发两个回退由 `_ADMIT_LOCK` 串行化，同批注第二次会因 patch 清空被拒，跨批注并发安全。

## 2026-09-21（五）修复轮结果反馈——进度卡显示「已修复 N 项 / 未修复 M 项」+ 可展开明细

**主题**：用户触发「选择级别修复」后进度卡毫无反馈，「好像 llm 并没有修复任何批注内容……起码给我一个 ui 的结果，我这啥都不知道，还要一个一个对」。**生产数据诊断（demo02，task 936136a0）证明修复其实生效**：修复轮（v2，user_query=只修严重级别）把 3 条 high 中的 2 条翻为 `fixed`，剩 1 条 high open——「承诺30分钟内响应…的得分」缺分值（需补「得X分」，属**纯插入+需编造具体分值**，按（十二）既知边界诚实跳过）。真正缺口是 UI：fixed/open 数据全在 state 端点 `annotations` 里，卡片却一条不显。

**改动（纯前端 2 文件）**：
- `file-review-progress.tsx`：新增修复结果区——`fixedAnns`（status='fixed'，AI 修复轮专属成果；手动面板走 resolved/wontfix 互不污染）+ `openAnns`（status='open' 且 source='ai'，人工批注不计入）+ `hasFixRounds`（rounds.length>1 才渲染，首轮 annotated 无修复语义）。默认一行「已修复 N 项（绿）/ 未修复 M 项（灰）+ 查看明细」；展开逐条列级别徽标（SeverityTag：严重红/一般琥珀/提示蓝，fixed 项徽标转绿）+ issue，open 项尾注引导「无法自动修改的类型（目录/标题重复、需补分值的纯插入）可打开审核面板手动处理」；fixed=0 且 open=0 显示「没有待修复的问题」。useState 提前到早退分支之前（hooks 顺序契约），data 判空用 `?.` 兜底。
- 测试 `__tests__/file-review-progress.test.tsx` 新增 3 例（demo02 回归：2 fixed/2 open 计数+手动批注排除+默认收起/单轮不渲染结果区/0 落地如实显示「已修复 0 项」），套件 22 passed；tsc 对改动文件零错误。

**未部署、未 commit**（部署 = 前端 build+dist+nginx reload，可与（二）（三）（四）合并批）。

**遗留**：①fixed 是文件级累计口径（多轮修复时显示累计修复数，非「本轮」——修复轮只翻 status 不记轮次归属，per-round 归因需加轮次字段，未做）；②修复轮选择的 levels 未落库，无法按所选级别过滤「未修复」（未选级别保持 open 是合法态，会一并计入未修复数）。

## 2026-09-21（四）人工批注绿色专属色系——与 AI 批注一眼区分

**主题**：用户反馈「人工批注的样式不够醒目，不能很明显地跟 AI 批注区分开」。现状：①正文 mark 人工与 AI 都按**级别**配色（红/琥珀/蓝）——正文里完全无法区分来源（最关键断层）；②边栏人工卡白底灰框偏淡，AI 卡反而是彩色底；③来源 chip 只是浅色小徽标，存在感弱。方案：**绿色 = 人工专属色系**（AI 保持按级别配色），正文一眼「绿=人工 / 彩=AI」，级别（严重/一般/提示）在人工卡上仅保留徽标不占主色。

**改动（纯前端单文件 `review-panel.tsx`）**：新增 `MANUAL_STYLE` 常量（border `#67C23A` / bg `#F6FFED` / text `#388E3C`）三处统一：
- **正文 mark + SVG 引线**：`railByPara` 人工批注 `color` 从级别色改为绿（引线 `stroke={it.color}` 自动继承）——正文 mark 绿=人工、彩=AI。
- **边栏 CommentCard**：白底灰框 → 绿色系卡（`#F6FFED` 底 + `#D9F2DC` 边 + 绿左边框 + 图标/作者名 `#388E3C`），人工 chip 升实底绿白字；级别徽标保留原级别色。
- **列表区条目**：人工条目左边框/图标转绿（AI 条目维持级别色），来源 chip 双双升级实底白字（AI `#1a66fb` / 人工 `#67C23A`）；AI 卡（AiCard）的 AI chip 同步升级实底蓝白字，两边对称。

**同日追加：列表区人工批注删除按钮**——用户指出「人工批注也可以删除的」。列表条目此前只有 AI 有删除（`annotationId` 闸），人工条目无删除入口；且**无锚点人工批注（plainComments）只有列表这一处展示、全界面无任何删除路径**。修法：listEntries 人工条目补 `commentId/commentUserId`，渲染处新增删除按钮（闸门=`onDeleteComment` 已传 + `commentUserId === currentUserId` 仅本人可删，与边栏 CommentCard 同口径），确认弹窗+`handleDeleteComment` 复用既有链路（deleteFlowComment + onSaved refetch）。

**测试**：tsc 对 review-panel 零错误；file-review-progress 19 + docx-highlight-seq 8 用例全绿。**未部署、未 commit**。

**遗留**：①正文高亮底色由 highlightDocxRanges 按传入色渲染，人工绿底与选区蓝色并存时视觉可辨，未做透明度分级；②历史已渲染的旧 mark 颜色在重开面板时按新色重插（无持久化颜色，无存量兼容问题）。

## 2026-09-21（三）无版本流程手动批注被「流程暂无文件版本」拒绝——批注 version_id 放宽为可空

**主题**：用户在**无版本**新流程里发起文件审核（对话上传文件）→ 审核面板看 AI 批注 → 自己选中文本写批注 → 报 `{code:101, message:"流程暂无文件版本，无法批注"}`。根因：flow_comment 设计之初文档只经版本通道进入流程，add_comment 端点强校验 `version_id = body.version_id or current_version_id` 非空；而文件审核目标走上传文档通道，流程没有任何版本 → 恒空 → 101。**第一性原理**：批注意见锚定的是文档内容（anchor_text/anchor_para），版本只是意见产生时文档的引用；文档不经版本通道进入流程不该丢掉批注能力。version_id 语义放宽为「产生意见的版本，可为空 = 流程级意见」。

**改动**：
- 后端 `flow_app.py` add_comment 单处：删 101 拦截，version_id 回退链尾补 `or ""`（空串合法，FlowComment.version_id null=False 存空串无碍）。
- 前端 `flow-detail.tsx` `commentsOf` 单处：原 `!selectedVersion → []` + 严格按 version_id 相等过滤，改为 `!selectedVersion || version_id 匹配 || version_id 为空`——流程级批注在任何选中版本下都可见，无版本流程也可见；该 memo 同时喂 FlowAiPanel（ReviewPanel 批注列表+锚定高亮，审核文件场景 anchor 匹配天然命中）与左下批注模块，一处改两处通。
- flow-ai-panel `handleAddAnchoredComment` 零改动（本就传 `version?.id`，undefined → 回退链 → 空串放行）。

**测试**：`test_flow_comment_severity.py` 新增 `TestCommentVersionless` 4 例（无版本放行存空串 / 显式 version_id 优先 / body 空值回退 current / 无版本+锚点+级别主路径），套件 10 passed；tsc 对 flow-detail 零错误。**后端已部署 2026-09-21**（flow_app.py SCP+md5 一致+restart+import 冒烟+端点 401）；**已 commit（a919c45d）+ push**；**前端未部署**（生产 dist 还是旧版，commentsOf 放行需 build 后生效）。

**遗留**：①版本删除级联「锚定批注一并删」不会碰 version_id 为空的批注（随流程硬删才回收，语义正确）；②version_id 为空的批注在查看其他版本预览时也会出现在列表（锚点匹配不中显示未定位），可接受不按版本隔离。

## 2026-09-21（二）流程对话用户气泡附件 chip：flow_ai_chat 加 files 列 + live/历史气泡展示

**主题**：用户要求「附件上传的文件在对话输出框做一个展示效果，看看范本填写和文件审核用不用也展示」。现状：流程页签用户气泡只有纯文本指令，随消息手动上传的文件发送后即「消失」（只在进度卡/成稿卡里间接可见），发送者与协作者都无法从对话流看出每轮带了什么附件。**范本填写/文件审核不需要单独做**：两者的进度卡已各自展示文件名（成稿对象/审核目标），chip 在**用户消息层**对所有消息类型统一生效，无需按功能适配。只展示**手动上传**（版本自动附带每条消息都带 id 轮换的流程自身文档，展示是噪音，与（十五）守卫同一取舍）。

**改动**：
- 后端 3 文件：`db_models.py` FlowAiChat 加 `files` TextField（默认空串）+ migrate_db `alter_db_add_column`（幂等）；`flow_service.py` `add_record` 加 `files` 参数透传 insert；`flow_app.py` add_ai_record 白名单归一（只取 dict 且有 id 的项、上限 10 个、id 截 64/name 截 255、json.dumps 落库）——**发送时事实**：只随新增（预存占位）落库，record_id 回填路径不传不覆盖。
- 前端 4 文件：`flow-service.ts` saveFlowAiRecord payload 加 `files`；`flow-types.ts` FlowAiChatItem 加 `files?: string`（JSON 串，空串=无）+ FlowLiveChat 加 `files?: {id,name}[]`（发送起点快照）；`flow-ai-panel.tsx` 新增 `liveFilesRef`，handleSend 在 manualDocs 同步快照处一并赋值（ChatInputBox 清队列后 ref 恒空，流式期间再读会丢——（一）竞态同源），预存调用带 `files`，流式/完成两处 FlowLiveChat 构造挂 `files: liveFilesRef.current`（else 分支 recovered/liveFileReview 变体 instruction 为空不挂）；`flow-detail.tsx` 新增 `parseChatFiles`（空串/畸形/空数组静默返空，与 parseFileReview 同口径）+ `ChatFileChips` 组件（浅色主题 chip：FileText 图标 + truncate + title 全名），历史气泡 parse `c.files`、live 气泡用 `live.files`，均嵌在指令气泡内 bg-[#EFF4FF] 同层。

**测试**：tsc 对 4 个前端改动文件零错误；file-review-progress 套件 19 passed；后端 test_flow_ai_record_update.py 17 passed（add_record 签名变更无回归）。**未部署、未 commit/push**。

**遗留**：①chip 纯展示不可点击下载（附件 id 是 documents 表文件，可后续加点击预览/下载）；②旧记录无 files 列值自然空串不渲染，无需回填。

## 2026-09-21（一）新流程手动上传文件审核报「未指定待审核文件」——上传队列清空竞态

**主题**：用户新建流程（无版本）→ 输入框上传文件 → 发送，FileReview 节点报「未指定待审核文件：…由前端以 inputs={'review_file_id': …} 传入」。**根因是竞态而非注入缺失**：flow-ai-panel `handleSend` 起手 `setSending(true)` → ChatInputBox 在 `sendLoading` 上升沿清空文件队列（`chat-input-box.tsx:106`）并回传 `onUploadedFilesChange([])` → 父层 `uploadedDocsRef.current` 被清空；而 handleSend 在**预存占位记录的 `await saveFlowAiRecord` 之后**才读 `uploadedDocsRef.current` 取附件（原 line 811）→ 恒读到空数组 → `firstFileId` 为空 → 不注入 `review_file_id`。**为什么此前 E2E 没抓到**：demo01 有流程版本，`files` 为空时走版本自动附带路径（uploadVersionAsDocument 在 await 后重新上传）兜底成功；「新流程无版本 + 手动上传」无任何兜底路径才暴露。c-chat 无此问题——它用 handler 起手同步展开的 state 快照 `[...uploadedFiles]`（index.tsx:1453）。

**改动（纯前端单文件 `flow-ai-panel.tsx`，2 处）**：handleSend 顶部（守卫处，任何 await 之前）同步快照 `const manualDocs = [...uploadedDocsRef.current]`，守卫与后续取附件（`const docs = manualDocs`）统一用这份快照；注释记录竞态链条。快照取守卫时点而非更早：与 manualUploadId 守卫同一读点，语义一致。

**验证**：tsc 对 flow-ai-panel 零错误；file-review-progress 套件 19 passed 回归。**已部署 2026-09-21**（前端 build 1m25s + dist 上传解包 + nginx reload + 首页 200）。**未 commit/push**。

**遗留**：①待用户人肉复测原路径（新流程→上传→发送→进度卡出现）；②ChatInputBox 清队列时机（sendLoading 上升沿）与父层异步读取的耦合模式仍在，后续其他调用方若在 await 后读 `uploadedDocsRef` 会踩同款坑——已用注释在两处标记。

## 2026-09-20（十八）文件审核成稿「存为流程版本」：修复后新版本进「查看文件内容」

**主题**：用户要求「确认审核修改后，生成新的版本，然后查看文件内容是修改后的文件」——现状是审核成稿只活在进度卡的「下载成稿」里，流程版本时间线 /「查看文件内容」（ReviewPanel 只读视图）看到的仍是最初上传的原件，修复结果对流程其他参与人不可见。经权衡取**手动按钮**（同范本填写成稿「存为流程版本」既有模式）：自动建版会在最多 3 轮修复中连灌 1~3 条版本且第 1 轮无修复时会把未修改原件也灌进去，何时「修够了」只有用户知道。

**改动（纯前端 2 文件）**：
- `web/src/pages/c-chat/file-review-progress.tsx`：新增可选 prop `onSaveAsVersion?: (taskId, fileVersion) => Promise<void>`——有成稿（`doc.has_result && doc.version`）且调用方提供时，成稿行渲染「存为流程版本」按钮；卡片**自管按钮态**（saving/saved/error 按成稿版本记 `Record` 键，新轮 v1→v2 时新版本无键自然回到可点击态），resolve →「已存为流程版本」禁用、reject →「保存失败，重试」可再点。c-chat 不传即不渲染（无流程版本概念），共享组件零影响。
- `web/src/pages/c-chat/flow/flow-detail.tsx`：新增 `saveReviewAsVersion(taskId, fileVersion)`——`downloadFileReviewVersionBlob`（review 专用 download 端点取 Blob，`@login_required` 只认请求头，直链必 401 的既有结论）→ `FormData(file + source='ai_file_review')` → `uploadFlowVersion(flowId, fd)` → invalidate `['flow-detail', flowId]`（版本时间线 /「查看文件内容」随之可见修改后文件）；失败向上抛由卡片翻态（与范本填写 `saveDownloadAsVersion` 同构，但状态归卡片、不落父层 saving 集合）。经 ConversationView 新 prop `onReviewSaveAsVersion` 透传给**已入库记录与 live 两处**进度卡。

**测试**：`file-review-progress.test.tsx` +3（不传 prop 不渲染按钮 / 点击回调收 `(taskId='t1', fileVersion='v2')` 且成功翻「已存为流程版本」+disabled / reject 翻「保存失败，重试」且重试走通翻已存）；套件 19 passed。tsc 对两改动文件零新增错误（唯一命中为（十四）既存遗留 `use-file-review-request.ts:56`）。

**遗留**：①按钮态在卡片实例内，卡片因流程切换卸载重挂后「已存」态丢失（可重复点击再存一条同内容版本——与范本填写同款已知取舍）；②E2E 未跑（需部署后：审核→修复→点「存为流程版本」→版本时间线出现新版本→「查看文件内容」看到修改后文件）。**已部署 2026-09-21（前端+后端合并批）**：后端 2 文件 SCP + md5 双端一致 + restart + import 冒烟（`REVIEW_TASK_ID_INPUT_KEY`/`_norm_extra_query` 在位）；前端 build（1m26s）+ dist 39M 上传解包 + nginx reload；冒烟：首页 200、chunk 命中「存为流程版本」「一个流程只能审核一个文件」文案、fix/download 端点无 Authorization 401。**未 commit/push**。

## 2026-09-20（十七）文件审核对话修复：点名某条 + 补充原因（fix user_query 透传）

**主题**：用户问「说『修复某某条的内容因为什么什么』可以吗」。排查：**现在不行**——①对话工具 `_fix` 刻意传 `user_query_override=""`，「本次补充要求」入口没暴露给对话（REST body["user_query"] 有、卡片 UI 也没发）；②「只修某一条」无通道——修复轮待修清单是该任务全部 pending（截 20 条），级别约束本就是 prompt 软指令（`compose_fix_query` →「本次只修复【X】级别…其余保持原样」），指定单条完全没有锚点。经确认取**方案 A（软约束轻适配）**：先跑起来，实测越界再升级硬约束（Service/executor 白名单）。

**改动（仅 `agent/tools/file_review.py`）**：
- `_fix` 开放 `user_query` → `admit_fix_round(user_query_override=...)`（Service/REST 同通道早已支持，Service `_fix_base_query` 把它拼进本轮基准、与首轮需求/级别指令叠加）；新增 `_norm_extra_query` 归一（非字符串按没提处理同 REST 口径、裁 500 字防挤爆修复 prompt）。
- `_status` 待修条目改编号列表「N. [级别] 问题（原文：…）」——序号与修复 prompt 的 `[idx]` 编号**同序同源**（list_pending_by_task 顺序、1 起），「修复第 N 条」从此有锚点；超 MAX_FIX_ITEMS(20) 的条目修复轮截断不含，序号仅供阅读。
- meta 描述引导 LLM：点名某条时 levels 取该条级别、user_query 写「序号/原文片段 + 要求/原因 + 其余保持原样」；`user_query` 参数描述合并 review/fix 两义（原 key 复用，不新增参数键）。

**测试**：`test_file_review_tool.py` +4（补充要求拼入且首轮基准/级别指令同在、非字符串忽略、超长裁 500、status 序号锚点）；既有 `test_status_truncates_pending_list` 断言随 `- [` → `N. [` 格式升级更新。50 passed；service/api 套件回归 145 passed。

**遗留**：①「只修这一条」是 prompt 软约束，修复 LLM 理论上可能越界改动其他条目（指令已写硬「其余问题保持原样、不要改动」，与级别过滤同机制，（十二）实测遵守良好）；若实测越界 → 升级 Service/executor 标注白名单硬过滤；②前端进度卡级别选择 Popover 无对应入口（对话专属能力，卡片行为不变）。**已部署 2026-09-21**（与（十六）同批：后端 SCP + 重启，见（十八）冒烟记录）。**未 commit/push**。

## 2026-09-20（十六）文件审核对话修复：task_id 经 Begin 注入（流程页签）

**主题**：用户要求适配两个能力——①「选择级别修复」能否**二次修复**；②**对话方式修复**。排查结论：①已支持无需开发（卡片 `canFix = !stale && !running && fix_rounds_left > 0` 镜像服务端闸门，`MAX_FIX_ROUNDS=3` 额度内可反复发起，失败轮也计入；对话工具 fix 同样支持连修）；②存在真缺口——流程页签的审核由 `FileReview` **节点**发起，task_id 只经 SSE 到前端、**从不进 LLM 上下文**，用户在流程对话里说「把严重的问题修一下」时 LLM 无从提供 task_id（c-chat 无此问题：同会话 review 工具返回里带 task_id）。

**改动**：
- `agent/component/file_review.py`：新增常量 `REVIEW_TASK_ID_INPUT_KEY = "review_task_id"`（与 `FILE_ID_INPUT_KEY` 并列，前端经 `canvas.run(inputs=...)` 送入 Begin）。
- `agent/tools/file_review.py`：新增 `_resolve_task_id(kwargs)`——「LLM 显式参数 > Begin 注入」两通道（显式优先：同会话 review/fix 刚返回的 task_id 比历史绑定注入更即时）；`_fix`/`_status` 的 task_id 解析换用它；meta 描述同步（task_id 可不传、fix 额度内可多次发起）。
- `web/src/pages/c-chat/flow/flow-ai-panel.tsx`：绑定源 memo 扩展为 `boundFileReview: {fileId, taskId} | null`（（十五）守卫同源）；发送时把 `boundFileReview.taskId` 经 `inputs.review_task_id` 注入 Begin——**无附件也注入**（纯文本对话修复正是主场景；本轮实时 `fileReviewRef` 已在发送起点被清空，只读落库绑定，保存后 refetch 及时会补）。

**链路闭环**：对话发起修复 → 工具 `admit_fix_round` 建新轮（round_no+1）→ 已有进度卡按 file_id 轮询 state 自动接上新 fixing 轮 → 卡片「选择级别修复」按钮按剩余额度继续可用（即二次修复）。二次修复本身零改动，仅回归确认。

**测试**：`test_file_review_tool.py` +5（Begin 注入发起 fix/status / 显式参数压过注入 / 两通道全空拒绝且不建轮次 / Begin 抛错降级同款拒绝），46 passed；相邻三套件（node/service/api）114 passed 回归无破坏；tsc 对 flow-ai-panel 零新增。

**遗留**：①注入依赖「最近一条携带 file_review 的记录」，记录保存后 ai_chats refetch 完成前的瞬间窗口内发送会拿不到 taskId（工具回退为提示用户提供，不误伤）；②`use-file-review-request.ts:56` 的 tsc 既有类型错误（（十四）遗留，非本次文件）；③未做浏览器 E2E（需运行中画布+真实 LLM），部署后建议人肉验证：流程对话说「修复严重级别的问题」→ 观察修复轮被受理且进度卡转为修复中。**已部署 2026-09-21**（后端 2 文件 SCP + md5 一致 + 重启 + import 冒烟；前端随合并批 build 部署，见（十八）冒烟记录）。**未 commit/push**。

## 2026-09-20（十五）流程「一个流程只能审核一个文件」发送守卫

**主题**：流程 DSL 的 FileReview 审核能力按文件独立成任务链（`agent/component/file_review.py` 每次触发 `get_uuid()` 新 task_id，轮次/成稿对象名都挂 file_id），业务约束是**一个流程只能审核一个文件**——但流程页签此前无任何约束，用户在同一流程里先后上传不同文件会产生第二条互不相干的审核链（进度卡与修复轮互相打架）。经确认：行为取「已有审核则拒绝新文件」（拦截并提示、不创建新审核任务），落地层仅前端流程页签。

**改动（纯前端单文件 `flow-ai-panel.tsx`，3 处）**：
- 新增本地 `parseRecordFileReview`（与 flow-detail.parseFileReview 同构：`{fileId,taskId}` JSON 安全解析）。
- 新增 `boundReviewFileId` useMemo：从 `aiChats` 尾部往前扫最近一条携带 `file_review` 的记录取其 fileId（绑定源）。
- `handleSend` 顶部守卫（在 `fileReviewRef` 清空与预存占位记录之前）：用户**手动上传**的文件 id 与绑定 fileId 不同 → `message.error('一个流程只能审核一个文件：当前流程已发起过文件审核，如需审核其他文件请新建流程')` 并拦截整条发送（输入框文字保留，不预存、不建会话、不上传）。绑定源同时兜底 `fileReviewRef.current`（本轮实时产出、记录未落库窗口）。

**刻意不拦的路径**：版本自动附带（attachFile）路径——`uploadVersionAsDocument` 每次上传产生**新 document id**，且语义上仍是流程自身文档；若一并拦截，流程里已有审核后每轮带附件的普通对话都发不出去。即「同一流程版本文件的重复审核」不被此守卫拦（属重新发起，走同一文件的链路语义）。

**验证**：tsc 全量零新增错误（flow-ai-panel 相关 0 条）；未跑浏览器 E2E（守卫为纯前端前置判断，路径清晰）。

**遗留**：①仅拦手动上传路径，若用户先发版本文件审核、再手动上传同一文件的不同副本（新 id）会被误拦（id 不同）——按「拒绝新文件」语义可接受；②后端 DSL 节点/对话工具入口不设同款闸（用户明确仅前端）。**已部署 2026-09-21**（前端随合并批 build + dist + nginx reload，见（十八）冒烟记录）。**未 commit/push**。

## 2026-09-20（十四）文件审核批注：跨行序列定位 + AI 批注硬删 + AI/人工来源强区分

**主题**：用户三连需求（demo01 流程文件审核面板）：①「为什么有些批注是未定位的」——实测 11/53 未定位，主因 8 条为 LLM 跨行摘录（matched_text 含 `\n`），前端 `matchAnnotation` 单段 `includes` 必然失配，保真视图 `tryResolve` 有同段校验（`p1 !== p2` 跳过）跨行永远产不出 mark → rail 过滤后进未定位（与（十二）修复轮落地率是同一表示断层的前端另一半）；②「批注可以删除」——AI 批注无删除入口（人工批注已有链路），选**硬删**（直接删 DB 行，`prev_annotation_id` 全库只写不读实证无链断裂风险）+ **与状态修改同闸**（`login_required` + `get_owned_task`）；③「明显区分人为批注和ai批注」——rail 卡徽标同为灰色辨识度低、批注列表区完全没有来源标识。

**改动**：

*A. 跨行序列匹配（救回 8 条跨行摘录类未定位）*
- `review-panel.tsx` 新增纯函数 `matchMultiline(paragraphs, matchedText)`：`\n` split + 空行过滤 + 非空行 ≥2 才走通道；连续段落滑窗，第 i 段 norm-contains 第 i 行；**唯命中闸**（命中 ≠1 → -1，宁未定位不错位）；`annotationMap` useMemo 前置通道（多行批注不进逐段 `matchAnnotation`——keyword 策略会把多行关键词误配单段造成错位），命中即 claimed + `map.set(baseIdx, [ann])`。
- `docx-highlight.ts` `DocxHighlightItem` 加 `lines?: string[]`；`highlightDocxRanges` 增序列通道（复用 ParaAgg 聚合结构，排除页眉页脚段落）：唯一连续段落序列命中后在窗口内逐行定位插 mark，**各行 mark 共用同一 `data-anchor-key`**；多命中/跨 HF/空段断裂 → 跳过（`text` 首行仍走常规兜底单段匹配）。
- `toHighlightItems`：含 `\n` 的 matched_text 填 `lines`（首行 trim 作 `text` 兜底）。

*B. 批注删除（硬删）*
- 后端：`file_review_service.py` `delete_annotation(aid)`（物理删除，`@classmethod @DB.connection_context()` 照抄 `update_status` 风格）；`file_review_api.py` 新端点 `POST /file/review/annotation/<annotation_id>/delete`，装饰器链+归属闸（`get_owned_task`）+错误 envelope（「批注不存在或无权访问」，并发删除窗口按不存在回）照抄 `update_annotation_status`。
- 前端：`api.ts` + `useDeleteFileReviewAnnotation(fileId)`（onSuccess invalidate state）；`review-panel.tsx` 新 prop `onDeleteAnnotation` + `removedAnnIds` Set 乐观移除（失败回滚+alert），rail 卡 AiCard 加 Trash2 确认删除按钮（`window.confirm`）、批注列表区 AI 条目同款（stopPropagation）；`flow-ai-panel.tsx` 传 `(id) => delAnnotation.mutateAsync(id).then(() => undefined)`（**必须返回真 Promise**——初版块体不返回 Promise 使 handleDeleteAnnotation 无法感知失败，404 后乐观移除不回滚，E2E 实测发现）。
- 测试：`test_file_review_api.py` +4（删除成功返回 id/不存在拒/跨租户拒/写 0 行映射错误）、`test_file_review_service.py` +3（物理删单行/空参 false/重复删幂等），95 passed。

*C. AI/人工强区分*
- AI = 蓝底蓝字 `bg-[#F0F5FF] text-[#1a66fb]`、人工 = 绿底绿字 `bg-[#F0F9EB] text-[#67C23A]`；rail 卡 AiCard「AI」/CommentCard「人工」徽标同色化，批注列表区每条加来源 chip（级别徽标后）。

**验证**：后端 pytest 95 passed；前端 vitest 19 套件 250 全绿（新增 `docx-highlight-seq.test.ts` 7 用例：唯一命中逐行插 mark/多命中仅首行兜底/空行过滤/空段断裂/单行走兜底/序列不存在/跨 HF 边界）；tsc 零新增；E2E（dev :9222，demo01）——①统计 **53 处标注 50 处已定位（未定位 11→3**，剩余为 content 端点 1897 段 vs 后端 addr map 4505 段解析口径差异类）；②8 个跨行 key 各产 2 个段落 mark（序列通道直接证据）；③列表点已定位条目跳转 + ann-flash 正常；④删除 confirm → 后端未部署 404 → **回滚恢复 53 + alert**（成功路径待部署后验证）；⑤AI 蓝 chip class 实证（demo01 无人工批注实例，绿 chip 为同构静态样式）。

**遗留**：①c-chat/index.tsx 的「查看文件内容」弹框 ReviewPanel 实例未接 onDeleteAnnotation（计划只要求 flow-ai-panel，如需一致后续追加）；②解析口径差异类 3 条未定位不根治。**后端已部署 2026-09-20**（2 文件 SCP + `diff --strip-trailing-cr` 部署前验证纯新增零删改 + md5 双端一致 + restart + import 冒烟（`delete_annotation(aid) -> bool` 签名/端点符号在位）+ 无 Authorization 头 401；**删除成功路径复验通过**：dev 前端新代码 + 远程新后端，删除未定位批注 POST 200 → 统计 53→52/列表同步/无 alert → 刷新后仍 52 条 DB 持久化确认）。**前端已随 2026-09-21 合并批部署**（build + dist + nginx reload，见 2026-09-20（十八）冒烟记录）。**未 commit/push**。

## 2026-09-20（十三）审核面板批注定位三层修复：fit-width 缩放 + 滚动/懒渲染持续重测

**主题**：用户报「批注的定位好像有点乱」。Playwright 几何实测定性出**三层纯视觉问题**（数据层无恙：39 个 mark 文本与批注 matched_text 对应正确）——①**rail 卡漂移**：锚点 Y 只在面板打开时测一次（rAF+120ms 二次校准），测量发生在 content-visibility 懒渲染估算布局下（containIntrinsicSize 794×1123），真实页高与估算差异随页深累积（实测 drift 第 3 页 3px、第 8 页 180~204px）；且 rail 是滚动容器的**兄弟节点**（不随文档滚动），锚点坐标是「mark 视口位置 − wrap 视口位置」，滚动后不重测必然错位。②**横向裁切**：docx-preview 的 A4 页固定 794px 宽、无缩放渲染选项，文档列仅 675px（960 抽屉 − 210 rail − 16 gap）→ `docx-wrapper`（flex 水平居中）对称溢出 + 外层 overflow-x-hidden → 页面两侧被裁（1920 视口实裁 ~60px）。③**巨页**：前附表跨页大表格 docx-preview 无法正确分页，单页真实 13612px（保真局限，只能靠缩放+重测缓解不可根治）。

**改动（纯前端单文件 `review-panel.tsx`，3 处）**：
- 新增 `fitDocxToColumn()`：CSS zoom fit-width——复位后 `width:max-content` 测自然内容宽（**不能用 scrollWidth**：flex 居中对称溢出时只计右侧溢出，实测 854 被低估成 764，缩放后仍裁 26px），`k=列宽/自然宽` 设 zoom，宽度补偿为自然宽、放开 max-w；只在渲染完成与窗口 resize 调用（滚动/重测路径禁入，防反馈循环）。zoom 影响布局无需高度补偿，getBoundingClientRect 天然 zoom-aware ⇒ 边栏测量一致；zoom 继承使 containIntrinsicSize 自动等比。
- measure effect 加双通道持续重测：`document.addEventListener('scroll', …, true)` capture 捕获任意内层滚动容器（规避容器识别启发式失败——内层无 min-h-0 随内容长高、真实滚动在外层）+ `ResizeObserver(wrap)` 感知懒渲染页高从估算变真实；rAF 节流。resize 回调先 fit 再测。
- 渲染 effect：renderAsync 成功后先 `fitDocxToColumn()` 再懒渲染/高亮。

**验证**：tsc 本次文件零新增；vitest 11 文件 141 全绿；Playwright E2E（本地 dev :9222，demo01 第 2 轮 39 处标注）——①1920 视口 zoom=0.790（自然宽 854=794+60 padding）、页面双侧 **0 裁切**；②1400 视口 zoom=0.486 同样 0 裁切；③**历史漂移最严重的 6 个锚点（ai-1/14/6/23 旧 drift 3~204px + 深处 ai-30/47）滚动后 drift 全部 = 0**（判定式 = cardTop − (markTop + markH/2 − 8)，锚点取 mark 中心）；④列表点 `list-ai-14` 跳转：mark 入视口 + ann-flash 闪烁 + 落点 drift 0；⑤截图确认 rail 卡引线精确对齐。

**遗留**：①docx-preview 巨页保真局限（跨页大表格不分页）不可根治，窄抽屉下 fit-width 字号偏小（1400 视口 zoom 0.486）是「完整显示优先」的权衡；②附带发现未修：已作废流程（terminal）不挂 FlowAiPanel → 历史进度卡「打开审核面板」按钮 optional chaining 静默无效（demo05 全作废 hence 面板打不开）。**已部署 2026-09-20**（build 1m39s + dist 上传解包保 inode + nginx reload + 首页 200；部署前 md5 对比 7 个后端文件双端全一致 ⇒ 本次纯前端、无需重启；生产验证：弹框 1280px/zoom 复位 A4 原尺寸/零裁切/39 卡在位/深锚点 ai-14/23/47 drift 全 0）。**未 commit/push**。

**追加（同日）——三处文档弹框加宽（随（十三）同批已部署）**：用户要求「文件审核弹框、查看文件内容按钮弹框、范本完善弹框宽度都加大」。前两者共用同一组件（`ReviewPanel`，版本列表「查看文件内容」= 无批注的纯查看模式），后者 = `TemplateFillLivePreview`（范本填写预览抽屉），两处弹框根容器同为 `fixed right-0 top-0 w-1/2` 半屏——统一 `w-1/2` → `w-2/3`（50% → 67%，1920 视口 960px → 1280px）。附带收益：审核面板文档列宽 995px 超过 A4 自然宽 854px，`fitDocxToColumn` 不再缩放（zoom 复位），**A4 原尺寸 1:1 完整显示**。E2E：1920 视口下审核面板与「查看文件内容」弹框均实测 1280px、零裁切、39 批注卡定位正常；vitest 46 用例通过。范本填写预览同一行类名同款改动（内层 flex-1 自适应受益），demo01 无填写卡未做浏览器实测，部署后可见。

**追加（同日）——弹框打开时左侧内容腾位适配**：用户报「左边的弹框内容没有展示全」。根因：弹框加宽到 `w-2/3`（fixed 盖视口右 2/3）后，外层腾位 padding 还是 `w-1/2` 时代写的 `'50%'`（c-chat/index.tsx 对话视图与流程页签两处，注释仍写「同为半屏宽」）⇒ 右侧 16.7% 宽度内对话内容（进度卡「本轮修复 3 项…」等）仍被面板盖住。修法：①两处 padding `'50%'` → `'66.667%'` + 注释同步；②超管「全部流程」页（flow-manage.tsx）**完全没有腾位**——补齐：新增 `tplPreviewOpen`/`reviewOpen` 两 state，详情容器加同款 `paddingRight: 66.667%` 腾位 + `transition-[padding]` 过渡，并给 FlowDetail 透传 `onTplPreviewOpenChange`/`onReviewOpenChange`（既有可选 props，FlowDetail 内部 viewOpen/aiReviewOpen/tplPreviewOpen 三路已上抛）。腾位链路说明：对话页 chat 视图内联审核面板是 flex 兄弟节点（`inline` 模式推挤布局）天然无遮挡，只有 fixed 弹框路径（reviewSheetOpen = 成稿预览/非 chat 审核视图/flowReviewOpen）需要 padding。E2E（dev :9222，demo01 流程页签）：打开审核面板后面板 left=640（1280px），进度卡 right=547、提交按钮 right=528、选择级别修复按钮 right=395 全部完整可见（零遮挡）；关闭后面板 padding 恢复 0px 布局还原；tsc 零新增、vitest 18 文件 242 全绿。**已随 2026-09-21 合并批部署**（前端 build + dist + nginx reload，见 2026-09-20（十八）冒烟记录）。**未 commit/push**。

## 2026-09-20（十二）文件审核修复轮补丁落地率根修：跨行序列定位 + 双重转义归一 + 提示词契约

**主题**：用户在 demo05 勾「严重」确认修复后「没有修复」。生产数据实锤（任务 `d9373b2c`）：第 2 轮 7 条补丁仅 1 条落地、第 3 轮 6 条全部落地失败，摘要全被计成「原文未能唯一定位或补丁与本条不符」。**根因链**：①`_compose_file_text` 给 LLM 的是按行拼接的多行正文，而 `FIX_SYSTEM` 只要求「find 逐字摘自文档、全文唯一」，没约束单段内 → LLM 为求全文唯一必然摘出**跨段** find（含换行）；②docx 落地闸门是 `find in para.text`（单段内），跨行 find 必然 0 命中；③LLM 还可能双重转义换行（json.loads 后残留字面 `\`+`n`）；④规划层 `_patch_matches_annotation` 用 norm_ws（空白不敏感）校验全部放行，失败只在 docx 层暴露。**首撞即真因：LLM 视图（多行文本）与 docx 落地口径（单段落）之间存在表示断层**。

**改动（后端 2 文件）**：
- `patcher.py` 新增 `_apply_multiline_patch`：find 含换行时走**连续段落序列定位**——find/replace 按行 1:1 对齐（行数不等=纯插入/删行，诚实跳过），用「连续段落逐一包含对应 find 行」唯一定位（**定位必须用完整行序列**：孤立编号段「3.1.1」全文 12 处歧义，加上后继行上下文后序列唯一——剥上下文行会连带剥掉唯一性来源），替换只落 `f_lines[k] != r_lines[k]` 的变更行；序列命中 ≠1、变更行段内出现 >1 次、空 find 行配非空 replace 一律整条跳过。
- `executor.py`：①`_plan_patches` 补 `_unescape_llm_newline`（字面 `\n` → 真实换行，None/非 str 原样透传不破坏 patcher 跳过规则）；②`FIX_SYSTEM` 与 `_build_fix_prompt` 明确「find 必须取自同一段落内的连续文字，不得包含换行；跨行问题只能摘其中一行内足以唯一定位的最小片段」。

**测试**：patcher +6 / executor +4，含 demo05 形态回归闸（单行多处歧义+后继行上下文唯一、序列定位不波及后续重复行、纯插入放弃、重复行删除不猜、空转补丁 False、纯文本降级路径不受影响、双重转义归一、提示词契约）；file_review 全套 **358 passed**。

**生产真实数据重放验证**（容器内只读脚本，新逻辑原样内嵌 + 真实 docx 字节 + 真实 llm_raw）：第 2 轮 1/7 → **3/7**、第 3 轮 0/6 → **2/6**（新增落地均为「孤立编号段+后继行」序列补丁）；剩余未落地 4 条属**本质不可自动改**：3 条目录行/正文标题重复（删哪份有歧义，目录是域生成删错即毁）+ 1 条纯插入行（段落级替换无锚点），诚实跳过是正确行为。

**遗留**：①executor `chosen = pending[:MAX_FIX_ITEMS]` 不按所选级别过滤（levels 只是 user_query 文字指令），预算被未勾选级别稀释——本例 4 条高危都在前 20 未伤到，后续可考虑结构化透传 levels；②「目录/标题重复」类高危问题的自动修复需要域感知的段落删除能力，当前设计下永远走人工。**已部署 2026-09-20**（用户确认后执行：后端 2 文件 SCP，md5 双文件一致 + `docker restart` + 容器内 import 冒烟（`_apply_multiline_patch`/`_apply_single`/`_unescape_llm_newline` 符号在位）+ `_plan_patches` 源码确认调用归一化 + `FIX_SYSTEM` 含禁换行契约；无前端改动）。旧轮次 3 轮额度已耗尽，验证需重新发起审核。**未 commit/push**。

## 2026-09-20（十一）审核面板批注列表：列表区与文档批注同时存在 + 点击跳转文档锚点

**主题**：用户报「我要的是所有批注都要在列表中能看到和点击跳转，我现在只能看到其他批注的列表」。此前批注以 Word 式边栏卡（绝对定位散布文档右侧 + SVG 引线）呈现，用户只在「其他批注」折叠区看到 1 条未定位条目。**初版做成了「列表/文档」双视图切换（列表视图下隐藏边栏卡），用户随即否定**：「你这个列表我要的是文档页面的列表格式样式啊，下面文档的批注也是在的，一个是列表展示，一个是批注展示，**同时存在**，然后列表点击后可以跳转到指定批注的地方」——修正方向为：①列表用流程页签批注模块同款**紧凑列表样式**（不是 AiCard/CommentCard 大卡）；②列表展示与文档边栏批注**共存**（不是二选一）；③列表点击跳文档中对应批注位置。

**最终改动（纯前端单文件 `review-panel.tsx`）**：
- 还原单滚动列布局（初版新增的 `railView` 状态、「列表/文档」切换按钮、右侧 `<aside>` 窗格、`otherOpen` 折叠态全部移除），rail 批注栏（210px）与 SVG 引线恢复常驻——文档批注展示不变。
- **批注列表区置顶**（滚动容器顶部、文档之前的白色圆角卡）：「📋 批注列表（N 条）」+ 提示「点击条目跳转到文档中的批注位置」；条目 = 流程页签批注模块同款紧凑卡（`bg-[#F7F8FA]` + 左侧 3px 级别色边，未定位虚线边 + 黄色「未定位」徽标），编号圆点（AI 接续已定位序号）+ 级别徽标 + 类型 + 作者 + `line-clamp-2` 内容（title 含建议全文）；**全部 25 条集中可见**（**级别高→中→低降序**——稳定排序同级别内保持文档序，已定位整体在未定位之前）。
- `listEntries` memo：从 `activeRailItems`（已定位）+ `unmatched`（未定位）统一产出紧凑条目模型，AI 字段提取（issue/suggestion/severity/type）与编号接续逻辑与 rail 卡同源。
- `handleAnchorClick` 统一跳**文档锚点**：mark `[data-anchor-key]` 优先、无 mark（表格内等）按 `[data-para-index]` 段落兜底，`instantFocusScroll`（强制真实渲染懒加载分页后瞬时居中，规避 smooth 滚动被懒页高度漂移带偏）+ `ann-flash` 闪烁（`void offsetWidth` 重启动画）；`annotation-select` 事件（表格内点击）复用同一回调；未定位条目点击给既有「无法精确定位」黄色提示条。

**验证**：tsc 本次文件零新增；vitest 18 文件 242 全绿；Playwright E2E（本地 dev :9222 代理生产后端，demo05 流程 25 处标注轮次）：①列表区 25 条（含 1 未定位徽标）与边栏 24 卡**同屏共存**；②点击 `list-ai-5` 跳转全链路 `{markFound: true, visible: true, flashed: true, railSelected: true, listSelected: true}`；③未定位条目点击提示条出现。**已部署 2026-09-20**（build 1m14s + dist 上传解包保 inode + nginx reload + 首页 200 + chunk 含新文案「批注列表」），未 commit/push。遗留：c-chat 侧刷新恢复需会话消息持久化；「未定位」徽标两套抽取器口径差异未动。

## 2026-09-20（九）审核面板重开批注恢复 + 存量轮次历史挂卡回填

**主题**：用户报「审核完重开文件审核，之前的批注都不见了，对话区域的控件也没了」。排查定性两缺口：①**批注重开消失＝设计缺口**——面板批注来源（live 透传 / structured output / 消息扫描 / 节点事件）全部依赖本轮会话内存，刷新/重开后全空，而批注真源在 file_review 系统存库却无前端读取通路；②**控件消失＝存量遗留**——用户轮次（15:48）在（七）持久化部署（16:53）之前，记录无 `file_review` 列 → 无卡可恢复。

**改动（纯前端 2 文件 + 一次性数据回填，后端零改动）**：
- `flow-ai-panel.tsx`：`fileReviewAnnotations` 升级 `{fileId, annotations}` 绑定结构（与 c-chat `frPanelAnnotations` 同构，切文件自动失效防串显）；新增 **restore effect**——reviewMode+reviewFileId 在位且 live 透传/structured output 皆空时，按 fileId 拉 `GET /file/review/file/<id>/state`（现成端点，返回跨轮次批注全集）兜底恢复；`openWithFile` 空批注也绑定 fileId（让 restore 识别「需拉取」）。
- `c-chat/index.tsx`：`reviewAnnotations` memo 后加同款 restore effect（四来源全空才拉，不覆盖）。
- **存量回填**（服务器一次性脚本，已清理）：文件上传通道不落 `file` 表、轮次不含 flow_id、minio_path 全 NULL——无任何名称/归属锚点，唯一可靠关联是**时间邻近**（轮次与发送即存记录恒差 4~9.5 秒）。贪心最近邻（Δ≤600s，一条记录只配一轮，`__test_fr_` 测试轮自动跳过）回填 9 条 `flow_ai_chat.file_review = {"fileId":..,"taskId":..}`（camelCase 与 `parseFileReview` 约定一致），幂等（空串守卫）。

**验证**：tsc 本次文件零新增（c-chat/index 3 错 stash 基线确认既存）；vitest 242 全绿；Playwright E2E：demo05 刷新后 4 条历史消息全部恢复挂卡（第 1 轮·已完成/剩余 3 轮/问题统计）→ 点「打开审核面板（原件）」→ 面板显示「共 25 处标注，高 4 / 中 15 / 低 6」与轮次数据一致，批注条目级别徽标+AI 标识+问题+建议完整渲染（「其他批注 1 条」系其余 24 条已定位锚在文档内，属保真预览正常行为）；4 张卡各自拉自己的 state 端点，fileId 绑定防串显生效。（**前端已部署 2026-09-20**：build 1m25s + dist 上传解包 + nginx reload + 首页 200 + chunk 含新逻辑；**未 commit、未 push**。遗留：c-chat 侧刷新恢复仍需会话消息持久化；「未定位」徽标两套抽取器口径差异未动）

**追加（2026-09-20 十）——边栏批注卡重复渲染：一条批注匹配多段落各生成一卡**：用户报「批注统计与批注列表数量不一致（25 vs 列表只有 1）」。Playwright DOM 实测定性：边栏实际渲染 **33 张** `rail-ai-*` 卡 + 其他批注 1 条，统计行「共 25 处标注，**33 处已定位**」——已定位超过总数，实为**同一条批注被重复渲染**：`review-panel.tsx` 的 `annotationMap` 对每个段落放入**所有**匹配该段文本的批注，而 matched_text（如「第二章 投标人须知」）常命中多个段落（章标题/目录/页眉短语），同一条批注便在每个命中段落各生成一张边栏卡，docx 高亮也随之重复标蓝；用户看到的「列表只有 1 条」即其他批注折叠区条目数与顶部统计对不上。修法（单文件 1 处）：`annotationMap` 加 `claimed` Set，每条批注只归属**首个**匹配段落（段内多条批注按 matched_text 长度排序的原逻辑保留）。修后实测：24 张卡 + 其他批注 1 条 + 统计「共 25 处标注，24 处已定位」三者完全一致。vitest 242 全绿、tsc 零新增，**已部署 2026-09-20**（build 1m19s + dist + nginx reload + 首页 200），未 commit/push。

## 2026-09-20 流程页文件审核节点缺 thoughts() 致整轮无回复

**主题**：用户在 C端流程页（demo01）发「审核一下这个文件」，LLM 整轮无回复。服务器日志（10:13:21）定位：`POST /api/v1/agents/chat/completion` SSE 流在 `Canvas BATCH [2:3]: FileReview:BraveLionsScan` 起跑即崩——`canvas.run` 的 node_started 事件对批内每个组件调 `get_component_thoughts` → `ComponentBase.thoughts()`（base.py:584）抛 `NotImplementedError`，异常沿 `async for ans in canvas.run(...)` 逸出杀掉整条流（quart `raise_task_exceptions` 落 ASGI ERROR，HTTP 响应体为空）。FileReview 组件（fire-and-forget 形态）漏覆写 `thoughts()`——TemplateFill/FanOut 均有同款覆写，属节点新增时遗漏；单测全走 `_make()` 桩直调 `_invoke`，不经过 canvas.run 事件路径故未拦住。

**改动**：`agent/component/file_review.py` 补 `thoughts()` 返回「正在发起文件审核...」（5 行）；`test/test_file_review_node.py` 补回归测试 `test_thoughts_returns_str`。19 用例全绿。

**遗留**：①部署（后端单文件 SCP + 重启，未授权未执行）；②审查其余自定义组件是否同样缺 `thoughts()` 覆写（仅 FileReview 一例）；③事故轮的消息未落库（异常先于 append_message），前端该轮显示空白属预期，修复后重发即可。

**追加（2026-09-20 二）——review_file_id 未随请求传入（第二层缺口）**：thoughts 修复部署后用户重发，节点正常起跑但报「未指定待审核文件」。排查确认：FileReview 节点与工具都只认 Begin 输出 `review_file_id`（画布会丢弃 files 里的上传 id，见两处 `_resolve_file_id`），而后端 inputs 透传链路（REST body → completion → canvas.run → Begin._invoke 逐键 set_output）齐全，**但 c-chat 与 flow-ai-panel 两个发送入口的请求体都没带 `inputs`**——正是 09-17 部署时「T17 Step 7 人肉浏览器验收 9 条未执行」会拦住的缺口。修法（纯前端 2 文件）：①两处发送体补 `inputs: { review_file_id: { value: <第一个附件 id>, type: 'line' } }`（无附件不发键）；②flow 面板附带版本文件时 **Word 版本（.doc/.docx）不走轻量 txt 通道**——审核执行器仅支持 .docx（executor.py:375），txt 传了也白传，改走 uploadVersionAsDocument 上传原件（轻量通道仍覆盖非 Word 版本）。tsc 零新增（报错均既存腐坏），file-review 相关 2 套件 18 用例全绿（前端已部署 2026-09-20：build+dist+nginx reload，chunk 命中新代码；无后端改动）。

**追加（2026-09-20 三）——老 .doc 原件就地转 docx（第三层缺口）**：前端部署后用户再试，轮次建起但执行器 failed「暂不支持审核该文件类型」。生产取证：轮次行 file_id 指向的 `-downloads` 对象头 4 字节是 `d0cf11e0`——**用户版本文件是老 .doc（Word 97-2003 OLE2），不是真 .docx（PK zip）**，`_load_docx_items` 的 PK 校验如实拒绝；顺带发现该上传 id 无 file 表行（`FileService.get_by_id` 落空），三级兜底的最后一跳 `-downloads/{file_id}` 救了场。修法（后端单文件 `rag/svr/file_review/executor.py`）：`_load_input_blob` 对原件判 OLE2 魔数 → `_convert_doc_to_docx` 就地转 docx（与范本入口 `template_api._convert_to_docx` 同款 soffice 参数——独立 UserInstallation 防 profile 锁 + 显式 `LD_LIBRARY_PATH=/usr/lib/libreoffice/program` 防 rc=127；**不共用代码是有意的**：REST 层与执行器层级方向不允许反向 import，注释里写明以范本那份为基准同步）；修复版本分支直接返回不重转。范本库「入口格式归一化」同口径：转换一次，后续全链路只认 docx，批注/修复产物天然是 docx 版本链。82 用例全绿（新增 3 例：OLE2 触发转换/PK 直通不转换/版本分支跳过原件路径）（**已部署 2026-09-20**：executor.py SCP + md5 一致 + 容器重启 + import 冒烟）。

**追加（2026-09-20 四）——老 .doc 文件审核/查看保真渲染+标注适配（第四层）**：用户要求文件审核与查看的文档 UI 展示与原件格式一模一样（含文字颜色），并确认标注能否适配。排查结论：ReviewPanel 本就具备 docx 保真渲染（docx-preview `renderAsync` 保字号/加粗/颜色/表格）+ AI 批注/手动批注锚定（`highlightDocxRanges` → `mark[data-anchor-key]` + 批注栏），但**老 .doc 文件两层都被挡在门外**：①`GET /files/{id}/content` 对 OLE2 转换成功后仍返回 `file_type:"doc"`，前端 `docxFidelityCandidate = content?.file_type === 'docx' && !editing` 闸门不放行 → 永远走纯文本降级视图；②`GET /files/{id}` 下载端点原样返回 OLE2 字节 → 即使放行 `renderAsync` 也会解析失败。修法（后端单文件 `api/apps/restful_apis/file_api.py` 2 处，前端零改动）：①content 端点 .doc 转换成功路径返回 `file_type:"docx"`（前端保真闸自动打开）；②download 端点判 OLE2 魔数 → 复用 `api/utils/doc_utils.doc_to_docx_via_libreoffice` 转 docx 后返回，文件名 `.doc`→`.docx` 同步改名。影响面核实：`/files/{id}/content` 与 `useFileBlob` 的唯一前端消费方均为 ReviewPanel（document-viewer 同步受益），无其他调用方受影响。顺带把第三层 executor 里 ~30 行 soffice 重复实现改为复用共享 `doc_utils`（import 方向合规：api/utils 是公共工具层），行为不变。101 用例全绿（executor 82 + node 19；file_api 无既有专属测试文件）。（**已部署 2026-09-20**：2 文件 SCP + md5 双文件一致 + 容器重启 + import 冒烟 OK + files 两端点无鉴权 401 路由在位）**追加（2026-09-20 五）——文件审核进度卡永不出现的三层根因**：用户实测「已开始审核…」回复了但进度面板始终不出现。排查坐实**三层**断点，任一都足以让卡片永不挂载：①前端两处扫描（c-chat T14 Path A + flow T15）精确匹配 `component_name === 'FileReview'`，但后端 `node_finished` 事件的 `component_name` 是 **DSL 节点显示名**（`canvas.py:590` 取 `n["data"]["name"]`，实测 `FileReview:BraveLionsScan`），组件类型在 `component_type` 字段——按显示名匹配永远 continue 掉；②即便匹配上，file_id 也取不到——`get_input_values()` 只返回 `_param.inputs`（FileReviewParam 未定义 → 事件 inputs 是空 `{}`），而节点 outputs 只吐 task_id/round_id/content 没吐 file_id，且 `useFileReviewState` 轮询端点**只认 file_id**；③**最致命**：扫描 effect 门在 `if (!done) return`，而 `use-send-message.ts` 收尾时 `setDone(true)` 与 `resetAnswerList()` **同帧批处理**——effect 触发时 done=true 但 answerList 已被清空，事件永远扫不到（hook 里 structuredOutputRef 注释原话 "survives answerList reset" 早已记录同一陷阱，FileReview 扫描设计时没吸收）。修法：后端 outputs 增补 `file_id`（已部署）；前端 `file-review-progress.tsx` 新增导出纯函数 `extractFileReviewTarget(events)`（component_type 判定 + outputs.file_id 优先 + inputs 两键旧兜底），两个发送路径在 `send()` 返回后从 **`res.events` 全量原始事件**提取（c-chat downloads 回填 / flow finalTplEvents 早已是同款模式，证明 answerList 不可依赖），done 态扫描 effect 保留仅作中止路径兜底（中止不 resetAnswerList）。4+16 用例全绿（新增 4 例对抗：显示名含 FileReview 的 Agent 节点不误匹配/缺 task 或缺 file 返 null/undefined 入参/旧后端 inputs 兜底），tsc 295 基线零新增。（后端**已部署 2026-09-20**：SCP + md5 一致 + 容器重启 + import 冒烟 `file_id in outputs` OK；**前端随本地 dev 生效**，生产 dist 待下次 build 部署；遗留：fileReview 仍不落 flow 记录，刷新后卡片不恢复——T14/T15 原设计缺口，未在本轮处理）。**追加（2026-09-20 六）——第四层根因：auto-save 收尾上报 null 抹掉 live，进度卡「闪现即消失」**：三层修复后 Playwright 实测卡片仍不出现（SSE 抓包证明 node_finished 事件正确、extractFileReviewTarget 能提取到 {fileId,taskId}），定位到 flow-ai-panel 主上报 effect 的 else 分支（无范本快照路径）：流结束后 `setCompleted(null)` 触发重渲染，else 分支无条件 `onLiveChatChange?.(null)` 把 live 整个清空 → flow-detail 挂载点 `{live.fileReview ? <FileReviewProgress/> : null}` 随 auto-save 完成瞬间卸载。修法一行思路：else 分支改为三分支上报——`completed ?? (recoveredTemplateFill ? {...} : fileReview ? {instruction:'', response:'', busy:false, templateFill: templateFillRef.current, fileReview} : null)`，instruction/response 置空防与已入库历史气泡重复（同 recoveredTemplateFill 分支口径）。Playwright 端到端验证通过：demo05 流程发「审核一下这个文件」→ 回复后中部对话区出现进度卡（第 1 轮 · 已完成 / 剩余 3 轮 / 共 30 个问题（高 2 / 中 20 / 低 8）+ 打开审核面板 + 选择级别修复按钮），且 auto-save 完成后 15s 仍在。纯前端 flow-ai-panel.tsx 单文件（**未部署、未 commit**；部署 = build + dist + nginx reload）。**追加（2026-09-20 七）——批注面板空白根修 + 进度卡刷新持久化**：用户实测两个症状：①点「打开审核面板」看不到批注内容；②要求进度卡刷新后仍在。**症状①根因（批注在链路上被丢弃）**：FileReviewProgress.onOpenReview 明明把轮询 state 的批注全集 `(annotations, doc.version)` 传了出来，但两个消费方都**丢弃了它**——flow-detail 只调 `reviewCtl.openWithFile(fileId)`，c-chat 只 `setReviewFileId + setReviewMode(true)`；而 ReviewPanel 的 `annotations` prop 来源是智能体 structured output（flow）/ msg.data.annotations 扫描（c-chat），本轮批注产自 FileReview 节点走 file_review 轮询端点，原三来源里都没有 ⇒ 面板恒空。修法：批注透传——`FlowReviewControl.openWithFile(fileId, fileName, annotations?)` 加第三参，flow-ai-panel 新增 `fileReviewAnnotations` state 优先于 structured output；c-chat 新增 `frPanelAnnotations` state（按 fileId 绑定，打开其他文件自动失效回落）。IFileReviewAnnotation 与 ReviewPanel.Annotation 字段天然兼容（matched_text/severity/issue/suggestion），直接透传；未锚定批注有 rail 边栏兜底（「未定位」徽标）不影响阅读。Playwright 实测：demo05 新轮审核 25 个问题，点「打开审核面板」批注栏全部可见（编号+级别+问题+建议）。**症状②（持久化）**：fileReview 原来只存于 live state，刷新即丢。仿 template_fill_events 同构最小方案：`flow_ai_chat` 加 `file_review` 列（CharField(255)，存 `{file_id,task_id}` JSON，migrate_db 增列），`add_record/update_content/add_ai_record` 三处透传（dict 入参序列化、超长 255 截断、None=不覆盖），前端自动保存随记录落库；**恢复走历史挂卡**——ConversationView `chats.map` 里 parse `record.file_review` 后挂 FileReviewProgress（组件自管轮询），flow-ai-panel 主上报 effect 加**去重让位**：ai_chats 里已有同 taskId 记录时 live 不再上报 fileReview（否则 auto-save→refetch 后同一条卡出现两遍）。测试：后端 34 passed（新增 5 例对抗：file_review None 不覆盖/dict 序列化/超长截断/缺席透传/普通轮不覆盖），前端 11 文件 141 passed，tsc 顺带修正 `saveFlowAiRecord` payload 类型缺陷（instruction 改可选——record_id 回填模式本就不传，382/700 行既有调用全在报错）。**部署约束：前后端必须成套**——前端先上而后端无 file_review 列 → 历史挂卡静默不出现（parse 空），持久化失效但无害；后端先上前端旧版无影响。（**后端 3 文件未部署、前端随本地 dev 生效、全部未 commit**；部署 = 后端 `db_models.py + flow_service.py + flow_app.py` 成套 SCP + 重启（自动建列），前端 build + dist + nginx reload。遗留：①批注锚定率——file_review anchor 区间与 ReviewPanel 段落抽取器不同，部分批注显示「未定位」（内容可读，点击定位待对齐抽取口径）；②c-chat 侧刷新恢复未做（derivedMessages 不持久化，需走会话消息持久化，工程量另计）；③存量记录无 file_review 字段，刷新后旧回复无卡，新轮起才有）**追加（2026-09-20 八）——其他批注置顶折叠区 + 人工批注级别 + flow-panel ref 无限循环崩溃修复**：①「📋 其他批注（N 条）」从未定位兜底区（滚动区底部）移至审核面板滚动容器**首行**，默认折叠可展开；点击未定位条目显示「无法精确定位」黄色提示并高亮该卡片（此前点击会滚动定位失败无反馈）。②人工批注支持级别：后端 `flow_comment` 加 `severity` 列（high/medium/low，存量默认 medium，migrate_db 增列）+ Service/端点双层非法值兜底 medium；前端批注草稿表单加级别选择器（严重/一般/提示），CommentCard 徽标按级别配色 + 「人工」徽标与 AI 卡「AI」徽标区分，rail/文档旁注气泡同配色；flow 页签左下批注模块（flow-detail portal）补级别徽标同口径；`addFlowComment` anchor 参数透传 severity。③**Playwright E2E 中发现既存生产崩溃**：`flow-panel.tsx` slot 挂载点内联箭头 `ref={(el) => setSlot(id, el)}` 每次 render 新建 → React 18 每轮先旧 ref(null) 再新 ref(el) → 每轮 2 次 setState → Maximum update depth exceeded——**任何有批注的流程打开页签必崩**（commentsOpen 有批注自动展开才挂载 slot，无批注流程掩盖了该 bug；stash 基线对比确认与本轮改动无关）。修法：`useMemo` 按 keptIds 稳定生成 ref 表 `slotRefs`。E2E 实测：demo05（含批注）正常打开无崩溃；无锚批注 → 顶部折叠区出现 → 展开 → 点击 → 提示+高亮全链路通过；文档旁注气泡与批注卡「一般」「人工」徽标渲染正确；E2E 测试批注已清理。6 后端 + 242 前端用例全绿，tsc 本次文件零新增（顺带修掉新写的 flow-detail sevStyle 索引 possibly-undefined 2 处）。（**已部署 2026-09-20**：后端 3 文件成套 SCP（md5 三文件一致）+ 容器重启 + import 冒烟（severity 参数+列在位）+ MySQL 建列确认（varchar(16) NOT NULL，3 旧行回填 medium，顺带补列 DEFAULT 'medium' 消除 NOT NULL 无默认隐患）+ severity=high 线上落库闭环验证通过后清理；前端 build 1m32s + dist 上传解包（保 inode）+ nginx reload + 首页 200 + 新 chunk 含「其他批注」文案 + delete 端点无鉴权 401 路由在位；本地 dist.tar.gz 已清理。**未 commit、未 push**。本次部署同时带上此前未部署的 2026-09-20（七）file_review 持久化改动——同在 db_models/flow_service/flow_app 三文件内）

## 2026-09-19（五）C端流程页签空状态滚动条消除

**主题**：用户报「暂无流程 + 发起新流程 空状态出现上下滚动条」。根因（`c-chat/flow/flow-panel.tsx`）：空状态占位是 `h-full`（= 滚动容器 100% 高），但同滚动区内列表包装层 `<div className="space-y-1 p-2">` **无条件渲染**——列表为空时该层仅剩 p-2 padding 也占 16px，总内容高 = 100% + 16px → 必然溢出出滚动条。`flow-manage.tsx` 无此问题（其空态/表格分支互斥且表格有 `list.length > 0` 守卫）。

**改动**：纯前端单文件——列表包装层加 `{list.length > 0 && (...)}` 条件渲染。tsc 零新增（全量 295 错误均既存腐坏），build+dist+nginx 已部署（chunk `index-jUtbJoR3.js` 双侧一致）。

## 2026-09-19（四）B端范本库列表页滚动被裁切修复

**主题**：用户报范本库列表「不能滚动，超出的被遮挡」。根因：B端 `root-layout` 的 `<main>` 是 `size-full overflow-hidden`（`layouts/root-layout.tsx:11`），所有 B端页面必须自带内部滚动容器；范本库列表页裸渲染 `Card` 无滚动层，列表条目变多（电子招标系列 12 条长名模板）后内容超过视口即被裁切。属布局缺口被内容增长暴露，非本轮改动回归（本页本轮零改动）。

**改动**（纯前端单文件 `template-fill/index.tsx`）：`Card` 加 `flex size-full flex-col overflow-hidden`（标题+搜索区固定），`CardContent` 加 `min-h-0 flex-1 overflow-auto`（列表+分页滚动），与 datasets 页 `CardContainer flex-1 overflow-auto` 同款模式。

**验证**：tsc 零新增、19 用例全绿，已部署 + push。

**追加（2026-09-20）——分页固定外移**：分页本就存在（每页 20 条 + 上一页/下一页），但按钮藏在滚动区底部，需滚到底才可见（此前被裁切时完全不可见，用户误以为没有分页）。改法：分页条挪出 `CardContent` 固定在卡片底部（左「共 N 条模板」+ 右翻页按钮），并加 `gotoPage` 翻页后滚动区回顶；搜索/筛选切换仍重置第 1 页。已部署 + push。

**追加（2026-09-20 二）——通用分页升级（复用 RAGFlowPagination）**：范本库列表 + 填写任务两页统一替换手写翻页条为项目现成 `RAGFlowPagination`（总数 + 页码 + 省略号 + 上/下页 + 每页条数选择器 10/20/50/100）；默认每页 **10** 条（前端 state + 后端两处 `get_list_page` API/service 默认值 20→10，钳制上界 100 不变）；切每页条数回第 1 页；填写任务页顺带补齐同款内部滚动容器（此前同有裁切隐患）。173 后端 + 19 前端用例全绿；后端 2 文件 SCP+md5+重启+容器内签名冒烟（`size: int = 10`），前端 build+dist+nginx。已部署 + push。

**主题**：用户要求 B端范本库详情页「编辑配置」模式下点击占位符行也能跳转定位到文档位置，与非编辑（确认视图）模式功能一致。此前 `onLocate` 虽已传入 PlaceholderTable，但 edit 分支的 `TableRow` 完全没绑定点击逻辑。

**首版缺陷与返工**：首版把排除区做得太宽（`closest('input, …')` 全排除）——编辑行「占位符」正是 key 输入框，用户点它必然无反应；且 FidelityPreview `focusDoneRef`「同一目标只定位一次」，在确认视图点过一次后进编辑模式再点同一点位全无反应。返工两处：
- `placeholder-table.tsx`：排除区收窄到仅 `closest('button')`（删除按钮/Radix SelectTrigger/Checkbox 都渲染为 button），点击输入框同样触发定位。
- `fidelity-preview.tsx` + `detail.tsx`：定位请求改为 `{key, seq}`，父组件每次点击 `seq` 递增，FidelityPreview 按 `key+seq` 识别新请求——同一点位重复点击也能重新定位（对 view 模式同样生效，修掉既有「点一次后就哑」缺陷）。

**验证**：vitest 4 套件 76 用例全绿；tsc 对改动文件零新增错误（detail.tsx TS7006 为 HEAD 既有）。

**部署**：已随同批次部署（前端 build + dist 上传 + nginx reload），已 commit + push。

**追加（同日性能返工）——编辑模式点击定位卡顿根修**：用户反馈编辑模式点击很卡。根因不在聚焦逻辑本身（view/edit 同一条 `setFocusReq → FidelityPreview effect` 链路），而在渲染成本：编辑表数百行 × 7 受控 Input，`setFocusReq` 是 detail 页 state，点击一次即全表重渲染 reconcile 上千输入框；确认视图是只读 span，成本可忽略，故「感觉不一致」。修法：`updateRow`/`removeRow`/`handleSaveDefault`/`locateRow` 四回调 `useCallback` 稳定引用 + `PlaceholderTable` 包 `memo`——定位点击只触发 FidelityPreview 效果，编辑表整体跳过重渲染。tsc 零新增、76 用例全绿，已部署 + push。

**追加（同日二次返工）——保存被「格式不正确」拦截根修**：用户删一行配置点「保存配置」报「存在格式不正确的填写点」。服务器实测该范本 1143 行中 5 行锚文本为纯空白（4×`' '`、1×`'\t'`）——09-18 run 层识别「留白位」的合法产物（确认视图显「留白 N 字符」，后端 `validate_placeholders` 对带 addr 行本就放行），但前端 `trimRows` 把 anchor 无脑 `trim()` 成空串 + `collectRowErrors` 判空标红，纯前端校验与识别语义矛盾。修法：`trimRows` anchor 仅在有非空白内容时去首尾（纯空白原样保留）；`collectRowErrors` 改「空锚文本恒标红 + 纯空白须有 addr 才合法」（无 addr 手动行仍必须有可见锚文本，后端要靠它反查定位）；新增 `placeholder-table.test.ts` 19 用例对抗性回归（留白保留/key 边界 64 字符/全角假名/空行三连标等）。**注意**：修复前若对含留白位范本点过保存成功，其留白 anchor 已被历史 trim 毁成空串，需重新 AI 识别。tsc 零新增、95 用例全绿，已部署 + push。

## 2026-09-19（二）B端范本详情「下载原件」401 修复（fetch+Blob 落盘）

**主题**：用户报 B端范本详情点「下载原件」新开页签打开 `/api/v1/template/fill/{id}/file?kind=original` 返回 401。与成稿下载乱码（09-17）同款地雷：`@login_required` 不从 cookie 兜底，`window.open` 直链不带 Authorization 头必 401。

**澄清的语义**：PDF 上传已归一化——上传时源 PDF 转 docx 后入库，**源 PDF 不留存**，`original_file_id` 存的就是转换后 docx。故 `kind=original` 下载到的即「PDF 转 Word」文件，无需第二个下载入口（用户确认按此口径）。

**改动**（纯前端单文件 `web/src/pages/template-fill/detail.tsx`）：`openDownload` 从 `window.open(直链)` 改为 fetch 带 `getAuthorization()` 取 Blob → `downloadFileFromBlob` 落盘（与 `downloadTemplateFillResult` 同构），文件名 `{范本名}.{docx|xlsx}`；「下载原件」「下载工作副本」两按钮共用此链路。同页面族排查：fidelity-preview 已是带 token 的 axios、tasks 走 fetch 模式，无其他直链地雷。

**验证**：`npx vitest run src/pages/template-fill src/hooks` 4 套件 76 用例全绿；tsc 对本文件零新增错误（line 476 TS7006 为 HEAD 既有）。

**部署**：未部署、未 commit、未 push；部署 = 前端 `npm run build` + dist 上传 + nginx reload。

## 2026-09-19 PDF 转 Word 关闭 stream 表格识别（根治正文被伪表格切碎）

**主题**：用户报「电子招标投标示范文本」PDF（237 页，WPS 导出，`.scratch/电子招标投标示范文本.pdf`）上传范本库转 Word 效果不理想。

**排查（第一性复核，含一次自我纠错）**：首轮回填判定函数用了自行复刻的旧逻辑（词底 y 窗口 + 宽/4 估算），得出「留白判定窗口不匹配、超宽 39%」的错误结论；重读真实代码（垂直中点窗口 + `get_text_length` 精确宽度）后推翻。**真实基线**：留白回填 1154 条正常、`_` 存活 63%、内容零丢失——留白链路本来是好的。渲染源页目视对比定位真凶：**pdf2docx 的 stream 表格识别把无框正文误判为表格**——「3.3 每个投标人最多可对___（具体数量）个标段投标」被拆进 4×6 伪表格、语序断裂（325 表格中 164 个单行/单列伪表格），回填的 `_` 随之被吞 37%。注意 `extract_stream_table` 默认已是 False，真正的开关是 `parse_stream_table`。

**改动**（后端单文件 `api/apps/restful_apis/template_api.py` +1 处）：
- `_convert_pdf_to_docx` 的 `cv.convert(out)` 改为 `cv.convert(out, parse_stream_table=False)`——关闭无线框表格识别；有线框真表格走 lattice 识别不受影响（「业绩要求」附表实测保留）
- 测试契约：`test_convert_pdf_to_docx_routing_and_output` stub 签名改 `convert(self, out, **kwargs)`，新增断言 `parse_stream_table is False`（防参数回退）

**验证**：106 测试全绿；真实样张端到端转换：伪表格 325→78、`_` 存活 63%→91%、页探针零缺失、第 8 页正文流与原件逐句一致、`项目报建编号：___` 等留白在正文流可见。ruff 报 7 处均为改动行之外的既有债务，未动。

**遗留**：① 剩余 9% `_` 损耗（lattice 表格区域内的留白被表格结构吞并，可接受）；② pdf2docx 偶发非确定性 page error（第 26 页 `list index out of range` 同一输入两次转换一次出现一次不出现，Python 哈希随机化影响布局分析顺序；第 203 页合并单元格报错但内容未丢）——同因，未处理；③ 页眉页脚（「第X章 XXX+页码」）仍混入正文，对 AI 识别是噪声，未处理。

**部署**：**已部署 2026-09-19**（单文件 `template_api.py` SCP + md5 双侧一致 + 容器重启 + 冒烟：容器内 grep 到 `parse_stream_table=False` 在位、`template_api` 导入正常、服务 HTTP 200）；未 commit、未 push。影响此后所有 PDF 上传转换（存量范本不受影响）。

## 2026-09-18（四）流程页预览抽屉不出现根修（portal 到 body）+ 未填充汇总默认折叠

**主题**：用户报 C端流程页点击「已填充 N 个填写点」里的字段名，右侧预览抽屉不出现（「被内容长度撑回去」）；同轮要求未填充汇总也像已填充清单一样默认折叠。

**根因（Playwright 生产复现实证）**：抽屉其实已挂载，只是被定位到 x≈4922（屏幕外）。两因素叠加：① `c-chat/index.tsx` 页壳 `.cs-page-enter` 动画 `fill-mode: both` 使 `transform: translateY(0)` **永久保留**——identity transform 也是 transform，该元素成为所有 `fixed` 后代的包含块，抽屉相对内容树而非视口定位；② FlowAiPanel 上传区存在超大 min-content 内容且祖先链缺 `min-w-0` 钳制，把包含块横向撑宽到 9844px，抽屉 `w-1/2` + `right-0` 随之落到屏幕外。c-chat 对话页同受①影响（仅因 pane 恰好未撑宽而侥幸正常）。

**改动**（纯前端 3 文件）：
- `template-fill-live-preview.tsx`：抽屉改 `createPortal(…, document.body)`——视口级覆盖层不应依赖「所有祖先恰好不创建包含块」这一脆弱条件，portal 后任何页面的内容树布局怪癖都不再影响其定位
- `template-fill-progress.tsx`：①未填充汇总抽成 `TemplateFillUnfilledList`，与已填充清单同级别同交互（默认收起、展开才挂 DOM、点击字段走 liveTarget+focusKey 定位），开关保留琥珀色 ⚠ 与已填充中性色形成待办/已完成主次对比；②已填充行值 span 补 `w-0`（flex-basis 0）——`min-w-0` 只允许「用的时候」缩、不改变 intrinsic 贡献，`truncate` 的 nowrap 长值仍会把祖先 min-content 撑到近万 px（同轮用户追加报告「关抽屉后对话区乱、右侧无限宽、下载按钮看不见」的根因，Playwright 二分实证：244 个值 span 把 pane 撑到 9968px，加 w-0 后回落 1030）
- `c-chat/index.tsx`：7 处 tab pane 容器补 `min-w-0`——详情列内容 min-content（675px）经 min-w-0 不阻断的 intrinsic 贡献链传到 pane，pane 作为 flex 项 `min-width:auto` 被钉在 1020px；补 min-w-0 后 pane 收缩、内部 overflow-auto/hidden 消化。Playwright 实测：展开双清单 pane 稳定 929px（=视口），下载按钮 x=594 在视口内，「版本记录」侧栏回归可见，开抽屉→关抽屉全程布局不乱

**验证**：本地 dev + Playwright 实测（上）；`vitest` c-chat 5 套件 42 用例全绿；tsc 对改动文件零新增错误（index.tsx 3 个报错经 stash 基线对照确认为既有）。

**遗留**：`.cs-page-enter` 的永久 transform 仍在（portal 后 fixed 抽屉已挂 body，无消费方受损）；该动画是页壳级样式、改动影响面大，本次不动。

**部署**：未部署、未 commit、未 push；部署 = 前端 `npm run build` + dist 上传 + nginx reload。

## 2026-09-18 文件审核加固（R-1 中断轮次自愈 + R-6/R-7/R-8 + 前端迁 Vitest，未部署）

**主题**：收口 2026-09-17 文件审核全链路的遗留清单（R-1/R-6/R-7/R-8）+ 前端测试基建从损坏的 jest（依赖已移除的 umi/test）整体迁 Vitest。设计稿 `docs/superpowers/specs/2026-09-18-file-review-hardening-design.md`，实施计划 `docs/superpowers/plans/2026-09-18-file-review-hardening.md`（subagent-driven 执行，每任务两道审查）。

**四项修复**：
- **R-1 中断轮次惰性自愈**：新增 Service 层 `heal_stale_round(row, now_ms)`（判据复用 `is_stale_running` 三判据；命中即 `update_status(id,'failed',error='服务重启或异常退出，本轮审核已中断')` 落库 + 同步刷新传入行内存；幂等由谓词天然保证）。「读路径触发写」是设计核心而非补偿机制：中断轮唯一消费场景是被读取（state 端点逐轮 heal）与被受理（`admit_fix_round` stale 闸门 heal 后放行），两点接入即覆盖全部出口，不引入 janitor 线程/启动扫描。**语义变化：中断轮自愈后与崩溃 failed 轮同权——有修复余额即可直接发起新修复轮，不再强制「重新发起审核」**；`FixAdmissionDenied` reason 契约 `stale` 移除、`invalid_levels` 加入
- **R-6**：`fix_review` 端点受理改 `await asyncio.to_thread(admit_fix_round, ...)`——最坏 5s 锁等待 + 同步 DB 往返移出事件循环；threading.Lock 跨线程互斥不变（仓库既有模式，llm_app/template_api 同款）
- **R-7**：`admit_fix_round` 入口（取锁/触库之前）`levels` 非 list 一律 `invalid_levels` 拒绝（原 `None` 会 TypeError 冒 500）
- **R-8**：`_doc_payload` 摘除 MinIO 内部对象名，返回 `{has_result, version}`（替代「object != file_id 哨兵」隐式约定）；前端 `file-review-stream.ts` 类型收窄、`file-review-progress.tsx` 下载按钮门控改 `has_result && version`、`onPreviewDoc` 签名收窄为 `(fileVersion)`、两个调用点同步。**注意 `doc.object` 字段已从 API 删除，后端（file_review_service.py + file_review_api.py）与前端必须同批部署**——前端先行会短暂隐藏「下载成稿」按钮（后端上线自愈）

**前端测试基建迁 Vitest**：`vitest.config.ts`（jsdom/globals/别名，include `src/**/*.test.{ts,tsx}` 全量收录——执行期修订：窄 `__tests__` 模式会静默排除目录外 7 个健康套件 96 用例）+ `src/test/setup.ts`（API 桩移植自 .scratch 脚手架）+ devDeps 换血（vitest/jsdom 进，jest 4 包出，连带删 jest.config.ts/jest-setup.ts）+ tsconfig types → `vitest/globals`；10 个测试文件 jest→vi 机械迁移（1 处 `vi.hoisted` 治 TDZ、3 处 requireMock 改直接 import+vi.mocked）；修 2 个既有腐坏套件（chat.test LaTeX 断言对齐现实现——捕获组空格保留；useScrollToBottom mock 补 scrollTo + 断言改锚现契约 `container.scrollTo`）。**实测基线勘误**：设计稿「3 套件 9 用例腐坏」为过期记录，实际 2 套件 4 用例（confirm-card 已被上一批修好）。

**测试**：后端 10 文件 **344 passed**（基线 337 + 净增 7，含 heal 落库/幂等/heal 后放行/heal 后 no_quota 仍拒/invalid_levels 触库前拒/state heal 恰一次/活轮次绝不 heal）+ ruff 0 error；前端 **17 套件 219 用例全绿** + 改动文件 tsc 零新增错误（useScrollToBottom 测试 listeners/initialProps 类型顺手修正）。

**遗留**：①`use-file-review-request.ts:56` 既有 tsc 错误（null vs undefined，非本批引入，一词可修）；②vitest 5 engines 要求 node ≥22.12 与 web/package.json `>=18.20.4` 声明冲突（本机 Node 24 无碍）；③web/CLAUDE.md 测试命令说明已同步改 Vitest。

**部署**（等用户指令）：后端 2 文件成套 SCP（`api/db/services/file_review_service.py` + `api/apps/restful_apis/file_review_api.py`）+ 容器重启；前端 `npm run build` + dist 上传 + nginx reload，**须与后端同批**。冒烟：state 端点响应 doc 无 `object` 键；无 Authorization 头 401。

## 2026-09-18（三）填写点列表按文档序排序（对齐预览从上到下）

**主题**：用户反馈 B端填写点列表顺序与模板预览文件中占位符出现顺序不一致。

**根因**：列表（placeholder-table.tsx）按 `placeholders` 数组序渲染、无排序；落库序是识别管线**拼接序**（手动 {{key}} 直通 → V1 无位行 → V2 有位行 → 漏标兜底），组内再按分块+LLM 输出序——与文档序无关。预览按 docx DOM 天然文档序，故「正文段（V1）在前、签署栏（V2）在后」类范本两序必然错开。功能无影响（预览高亮按锚定位不依赖数组序），纯展示顺序问题。

**改动**：
- `detector.detect_fill_points`：`_verify_slot_occ` 后对 merged 按 `(line, _anchor_pos)` 稳定排序（行号=候选行扁平序号即文档序；段内偏移使乱序输出的同段多位归位）——新识别落库即文档序
- `web/src/pages/template-fill/detail.tsx`：详情加载时按 `(line ?? MAX, occ ?? 0)` 排序展示——**存量旧数据立即受益**；划选手动添加行（无 line）垫底不跳顶；保存随展示序回写
- `use-template-fill-request.ts`：`TplPlaceholder` 补 `line?`/`occ?` 类型

**测试**：TDD 红→绿，新增 `test_detect_fill_points_sorted_document_order`（V1 line=9 在管线序先于 V2 line=2、V2 乱序输出偏移 7/1 → 排序后 ka(1)、kb(7)、v1_field(9)；`_anchor_pos` 不外泄）；557 后端全绿

**部署**：**已部署 2026-09-18**——后端 detector.py 单文件 SCP + 双侧 md5 一致 + 容器重启 + import 冒烟通过；前端 build（1m18s，本机内存不足 OOM 杀过一次、用户清理后重跑成功）+ dist 上传 + nginx reload + 首页 200。**未 commit、未 push**

## 2026-09-18（二）切位上线后两症状根修：簇级分类 + 完整空白出现定位

**主题**：run 层切位部署后用户实测两缺陷——①「＿＿＿（项目名称）＿＿＿ 已由 ＿＿＿（项目审批、核准或备案机关名称）＿＿＿」识别成 3 个占位符（应 2 个）；②B端预览徽标错位：para:76 的 contract_no_94、para:759 的 supervisor 渲染到 para:54/55 区域。

**根因（生产数据实证，非猜测）**：
- ①Word 把提示语拆成多 run（机电监理范本 para85 实测 `(` + `项目名称` + `)   ` 三个 u=True run），blank_slots 逐 run 全匹配全部失败 → 只有空白 run 成位、提示语丢弃 → 12 个空白碎片占位符
- ②anchor 是下划线 run 文本，相邻非下划线空白没拼进去 → anchor 非段内「完整空白 run」（contract_no_94 锚 4 空格 vs 段内 5 空格，严格口径计 0）→ compute_anchor_positions 跳过 pHash → 前端回退全文顺序匹配分到文档第一个同形空白段。tenderer_seal_54 等锚含「（盖单位章）」走 norm 通道正常 →「一半对一半飞」

**核心变更（三层）**：
- `blank_slots.py`：连续下划线 run 成簇 + 簇文本整体分类（全空白→blank；括号组扫描且组外全空白/下划线→hint 位，单组铺满整簇（`＿＿＿（项目名称）＿＿` 1 个 hint 位）、多组分区段铺满；组外有实心文字→逐 run 回退旧行为）+ 纯空白 blank 位扩展为段内最大空白 run（向前/后吸收相邻空白，止于非空白或相邻位边界）+ 触接的同为 hint 位不合并（多括号簇拆出的相邻提示位保持独立）
- `docx_utils.compute_anchor_positions`：纯空白 anchor 严格完整 run 计数为 0 时回退普通非重叠计数（64 截断防病态段），仍写 p_idx/p_hash/a_occ/p_total；**整组统一口径**（同 (addr,anchor) 组严格>0 全用严格序、严格=0 全用宽松序）避免混编号——**存量范本无需重新识别即修复定位**
- `docx-highlight.ts`（C/B 端共用）：新增 findPlainOcc（非重叠 indexOf 步长=len，与后端 _occurrence_intervals 同构）作 resolveInPara 第四回退（raw 严格→canon 严格→raw 宽容→canon 宽容）；**rawResolvable 打分刻意 strict-only**（审查 M-1：宽容回退参与打分会削弱同形克隆段强区分信号）；填值层（addr 定位+宽松 indexOf）不受影响零改动

**测试**：TDD 全程红→绿。新增后端 10 例（多 run 提示语簇合并精确复刻 para85 形态/一簇多括号拆多位/混合形态单 hint/簇分类失败回退/空白扩展 4+1→5/扩展止于非空白与位边界/空白 gap 并入 hint 回归闸/compute 部分空白锚保 pHash/完全不存在仍跳过/宽松序 occ 分配）+ 前端 1 例（8 空格锚 vs 12 空格 run 宽容回退段内定位）；template_fill 全套 556 后端 + 31 前端全绿

**审查**：superpowers:code-reviewer 1 Major（M-1 rawResolvable）+ 4 Minor，M-1/m-1（canon 宽容补齐）/m-2（64 截断）已修复，m-3（plain 回退出现序固有歧义，仅残留于 V1 存量 sub-run 锚，V2 新锚走严格通道不受影响）为已知取舍，m-4（commit 拆分）知悉

**部署**：**已部署 2026-09-18**——后端 2 文件成套 SCP（blank_slots.py / docx_utils.py）+ 双侧 md5 一致 + 容器重启 + import 冒烟通过；前端 npm run build（3m24s）+ dist 上传（rm -rf dist/* 保 inode）+ nginx reload，首页内外网均 200。注意：本次 dist 一并带上了工作区 3 个此前已完工待部署的前端改动（template-fill-live-preview.tsx / template-fill/detail.tsx / fidelity-preview.tsx）。**未 commit、未 push**

## 2026-09-18（一）范本AI识别加固：run层确定性切位 + LLM语义标注

**主题**：修下划线场景识别四症状（拆碎片/漏识别/重复错位/提示语污染）

**核心变更**：
- 新增 `rag/svr/template_fill/blank_slots.py`：run 层切位器——下划线格式（w:u）留白 run + 字符下划线串确定性切出填写位区间（runs 拼接文本坐标系，与替换层一致）；含超链接段落整段回退 V1
- `docx_utils.extract_docx_candidates` 候选行附带 `slots` 键；有位行即使不命中 FILL_HINT_RE 也入候选；纯空白整行下划线段落入候选（真实范本 57 行漏识别根修）
- `detector.py` 分流：有位行走 DETECT_SYSTEM_V2「位编号→语义」契约（LLM 不再自选 anchor，anchor 由切位区间精确切片）；无位行走现有 V1 契约（存量零影响）；LLM 漏标位确定性兜底（hint→括号提示为中文名不低置信，blank→「未命名填写位」低置信，key=blank_N 全局续接）；`_verify_slot_occ` 确定性 occ 校验闸（复刻渲染层非重叠出现+更长锚嵌套剔除口径，治同段非位同形文本/嵌套位的 occ 错位串位）
- V2 解析防御：key 截断感知去重（防 validate 判死）、required 字符串判型、anchor membership 校验、slot 序号非法丢弃；V2 条目显式 `default_value=""` 硬闸 + `derive_default_from_anchor` 混合形态骨架判定（`＿＿＿（项目名称）＿＿` 类「下划线+括号提示」合并位不再把提示语派生成默认值致字段被跳过填写，收口审查 Major-1 根修 fff3a8ec）；下游渲染/前端/validate 零改动

**验证**：真实范本（福建省信息化工程招标示范文本 docx）切位 504 候选行/408 含位行/553 位，用户报告句一次切出 7 位无碎片；539 后端测试全绿；两道审查（spec+quality）×5 任务全过 + 收口审查 READY

**遗留**：underline=None 继承样式不识别（首版）；xlsx 不适用；兜底 key（blank_N）可读性一般依赖 B端人工改名；识别后手动增删占位符不经 occ 校验闸的已知窄缝（见设计稿 §7）

**部署**：未部署。后端 3 文件成套 SCP（blank_slots.py 新增 / docx_utils.py / detector.py）+ 容器重启 + import 冒烟

## 2026-09-17 范本删除级联清理填写任务（解「流程删完了范本删不掉」）

**主题**：用户报「C端流程都删除完了，B端范本还是删除不了」。根因：删除守卫挡在 `tpl_fill_task` 行，而流程删除（`flow_service.delete_flow` 硬删 FlowInstance/AiChat/Comment/Version）**从不回收任务行**，且任务行与流程**无可靠外键**（`flow_instance_id` 2026-09-17 之前全库空串，之后存的也是 canvas session id 而非 flow id）——「流程删掉→任务跟着删」在数据上走不通，范本被死任务行永久锁住。

**修法（级联语义）**：`TplTemplateService.delete_template` 从「有任务拒删」改为**删除范本时级联删除其全部填写任务行 + 成稿对象**（真源 `{template_id}/{result_file_id}` + 派生下载副本 `{tenant_id}-downloads/tplfill-{task_id}`，无成稿的任务不产生对象删除）。自洽性：范本删除后历史成稿本就无从下载（bucket 随范本一起清），任务行与成稿一并删才对。保留「published 须先停用」守卫；清理顺序维持「MinIO rm 在事务外（失败仅告警）→ 事务内锁模板行 → 裸查询依次删任务行/版本行/主表行」（⚠️ 事务内禁用装饰器方法的线上 500 教训 docstring 保留）。`has_tasks` 方法随之删除（唯一调用方即旧守卫）。前端删除确认文案同步：单删补「其历史填写任务与成稿将一并删除」，批删改「仅草稿/已停用的模板会被删除（其填写任务与成稿一并删除），其余自动跳过」。

**测试**：`test_delete_template_refuses_when_tasks_exist` 反转为 `test_delete_template_cascades_fill_tasks`（任务行 DELETE 执行 + 真源/派生副本两对象 rm + 无成稿任务零对象删除）；新增 `_FakeTaskModel` 桩；happy path/rm 失败两用例补空任务桩；批删 fixture 消息换成仍可能出现的「模板不存在」。`test_template_api_routes.py` 106 passed + `test_template_fill_service.py` 11 passed。

**部署清单**：后端单文件 `api/db/services/template_fill_service.py` SCP + 重启；前端 `web/src/pages/template-fill/index.tsx` 需 build。**未部署、未 commit、未 push**。

## 2026-09-17 B端模板预览回显默认值（纯前端，未部署）

**主题**：用户要求「B端的默认值可以回显到模板预览的文件中」——写回范本库/手动编辑的默认值此前只在右侧列表可见，预览文件里仍是留白/锚文本。

**实现**：B端范本详情保真预览（`fidelity-preview.tsx`）在 `highlightDocxRanges` 完成后，对**有默认值且高亮命中**的填写点执行 `applyDefaultValues`——把 `mark[data-anchor-key]` 内的锚文本（含空白留白）就地替换为默认值蓝字（`#1a66fb`，与 C端填入值同款语义），`{{key}}` 徽标保留在值后，悬浮 title 显「默认值：xxx」。DOM 手术要点：mark 内容是 `range.cloneContents()`（docx-preview 的 run span，文本藏在元素里，逐文本节点清理不可靠）+ 徽标是**最后追加**的 span → 整段清空后按 `textContent` 前缀 `{{key}}` 校验把徽标移回，防误留原文残片。未定位/空默认值行静默跳过；`anchorsSig` 加入 defaultValue 使默认值编辑触发重渲染。`detail.tsx` anchors 透传 `default_value`；说明条补「蓝字为已设默认值的回显」。

**验证**：Playwright 实测生产范本详情页——113 个高亮点中 51 个默认值回显（`tenderer_name=武功县住房和城乡建设局`、`approval_authority=李港111` 等），0 个徽标丢失；截图确认值以蓝字嵌在正文原留白处。`tsc` 无新增错误（`detail.tsx` 存量 1 处隐式 any 与本改动无关）。

**部署清单**：纯前端 2 文件（`web/src/pages/template-fill/fidelity-preview.tsx` + `web/src/pages/template-fill/detail.tsx`），需 `npm run build` + dist 上传 + nginx reload。**未部署、未 commit、未 push**。

## 2026-09-17 成稿下载适配：新开页签乱码改 fetch Blob 落盘（纯前端，未部署）

**主题**：用户报「流程对话页面的下载按钮打开新浏览器页签显示乱码」。涉及两处同构写法：共用车 `template-fill-progress.tsx` 成稿行（c-chat 对话页与流程页签共用）+ `c-chat/index.tsx` 的 `msg.downloads` 行（DocumentRewrite 等产物），均为 `<a href={dl.url} target="_blank">` 直链。

**根因**：`dl.url` 指向 `/api/v1/agents/download?id&created_by`（agent_api.py:343）——该端点**无 `@login_required`、无 `Content-Disposition` 头**，直接 `Response(blob)` 返回原始字节。浏览器新页签导航到它时把 docx（zip 二进制）当文本渲染 → 乱码；且即使加头也不会带 Authorization 头，直链方案本就不可靠（同 file-review 下载链路已确认的教训）。

**修法**：`template-fill-progress.tsx` 新增导出 `downloadTemplateFillResult(dl)`——fetch（带 Authorization 头，端点日后加鉴权也不破）→ `downloadFileFromBlob`（复用 utils/file-util 既有函数，a.download 带文件名落盘）；两处 `<a target="_blank">` 均改为 button 调它。url 缺失/非 2xx/异常均 message.error 提示。

**验证**：本地 dev（`:9223` 代理生产后端）+ Playwright 实测流程页签 demo04 成稿卡「下载」→ 触发浏览器下载、文件名正确（`…建设信息化….docx`）、219503 字节、`PK` 魔数 + `Microsoft Word 2007+` 识别有效，无新页签。`tsc` 无新增错误（index.tsx 存量 3 处报错与本改动无关）。

**部署清单**：纯前端 2 文件（`web/src/pages/c-chat/template-fill-progress.tsx` + `web/src/pages/c-chat/index.tsx`），需 `npm run build` + dist 上传 + nginx reload。**未部署、未 commit、未 push**。

## 2026-09-17 范本预览：流程页签发送消息致「查看填写内容」抽屉卸载变白底（纯前端，未部署）

**主题**：用户报「点击查看填写内容弹框后，在输入框发送对话，右边渲染的文件变成白色背景」。Playwright 生产环境复现确认：抽屉**整体卸载**——发送后右侧露出流程详情的白色「版本记录」面板，用户感知即「文件变白底」。

**根因（流程页签特有，c-chat 对话页无此问题）**：进度卡在 `flow-detail.tsx` 只条件挂载于 **live（进行中一轮）**：`live.templateFill?.templates?.length ? <TemplateFillProgress/> : null`；而抽屉（`TemplateFillLivePreview`）连同打开状态 `liveTarget`、整棵 docx-preview DOM 树都挂在**进度卡内部**。`flow-ai-panel` 的对话状态上报 effect 在流式分支上报 `templateFill: streamState.templateFill`——**新轮刚起流式时它必为 undefined** → 判空 → 卡片卸载 → 抽屉连带销毁 → `onLivePreviewOpenChange(false)` 收起右侧腾位布局。轮次结束后卡片虽由 `templateFillRef` 兜底装回，但 `liveTarget` 已随卸载丢失，抽屉不会重开。对照 c-chat：卡片挂在旧消息的 `msg.templateFill` 上，发送新消息不影响旧消息 → 抽屉正常存活。

**修法（一行）**：`flow-ai-panel.tsx` 流式上报分支改为 `templateFill: streamState.templateFill ?? templateFillRef.current`——新轮流式期沿用上一轮终态兜底，与 `handleSend` 里「范本填写快照**不清**」的既定注释、与 completed 分支既有的 `templateFillRef` 兜底完全同构。语义上也与 c-chat 对齐：旧填写卡在新轮对话期间持续可见。若新轮真正开启新填写，第一个 `template_fill_progress` 事件到达后 `live.templateFill` 自然切换到新状态（同 template_id 时抽屉还能继续实时填入）。

**验证**：本地 dev server（`:9223`，代理生产后端）+ Playwright 全程复现——修前生产环境发「你好」抽屉立即消失；修后发「在吗」流式期间与回复完成后抽屉均保持打开、docx 渲染完好。c-chat 5 套件 40 用例全绿；`tsc` 无新增错误（`flow-ai-panel` 存量 3 处 `saveFlowAiRecord` 缺 `instruction` 的类型报错与本改动无关）。

**部署清单**：纯前端单文件 `web/src/pages/c-chat/flow/flow-ai-panel.tsx`，需 `npm run build` + dist 上传 + nginx reload。**未部署、未 commit、未 push**。

## 2026-09-17 范本填写：就地修改产值被 Redis 旧快照遮蔽（修「LLM 光说改好了」，未部署）

**主题**：用户报「**改了个屁**，之前都是好的，现在 llm 光说改好了，**是不是改的文件不是一个？**」。用户假设「改的文件不是正在看的那份」——**经查证该假设不成立**，真因是读取侧口径。

**第一性原理判据（决定「该修哪里」）**：「查看填写内容」预览**不是成稿文件**，是「范本**工作副本**（含 `{{key}}`）+ 前端 `values` 覆盖渲染」，屏上文字由**前端拿到的 values** 决定。所以先分清两条链：**下载**读 MinIO 派生副本、**预览**读 values 接口。用户在预览里看到没变 ⇒ 嫌疑在 values 接口，不在 MinIO。

**逐层取证（生产任务 `a77dc640b29211f1be7cdb1ab9caea8d`，范本 `e1a491e0…`）**：

| 层 | 证据 | 结论 |
|---|---|---|
| DB 行 | `values.render.approval_authority = '李港111'`，status done，update_time 20:42:38 | ✅ 已改对 |
| MinIO 真源 | `{template_id}/v1_result_a77dc640….docx` 219503 B，md5 `c7c1b9c14c49`，含「李港111」 | ✅ 已改对 |
| MinIO 派生副本 | `{tenant_id}-downloads/tplfill-a77dc640…` 219503 B，**md5 与真源完全一致** | ✅ 已改对 → **用户假设被证伪** |
| values 接口 | 返回 `approval_authority = ''` | ❌ **就是这里** |

**根因**：`tpl_fill_progress:a77dc640…` 的 Redis 进度快照停留在**填写完成时刻**（`updated_at` 20:25:55，即 20:42 就地修改**之前**），其中 `approval_authority = ''`，与 DB render 的差异**恰好只有这一个键**（键集合 113 = 113）。而 `api/apps/restful_apis/template_api.py` 的两处读取点（`build_progress_payload`、`build_run_snapshot_payload`）都**无条件快照优先**；`FillTemplate(action=modify)` 又**只回写 MinIO+DB、从不碰 Redis** ⇒ 修改前旧值持续遮蔽新值，**预览打开时拉权威值、刷新恢复重放，两条链路全被遮蔽**——没有任何一条路径能显示新值，故「改了个屁」。这也解释了**为什么上一批「就地修改可见性修复」（`b1867dff`）新增的预览打开拉取没能生效**：那次新增的 fetch 正是打在这个被遮蔽的端点上。

**改动**：新增纯函数 `resolve_progress_values(status, task, snapshot)`，作为**两处读取点共用的唯一权威口径**——判据用**状态口径**（复用既有明文语义 `TERMINAL_TASK_STATUSES`）而非时间戳（时间戳等价性依赖「DB 每次写都刷新 update_time」这一未在模型层强制的约定，判据越少越不易腐坏）：
- **非终态** → 全量产值只在快照里（DB 行要到终态才写 values）⇒ 快照优先（原行为不变）；
- **终态** → DB 行是权威（执行器已写全量、此后只有 `modify` 会改且只写 DB）⇒ **DB 优先，快照只配补缺、不配遮蔽**；DB 无产值（脏行/未回写）才退快照；空 dict 仍走快照分支以保住「全部未填」派生。

**测试**：`test_template_fill_progress_api.py` / `test_template_fill_run_snapshot.py` 新增事故回归闸（终态 DB 压过快照、DB 无值/空 dict 退快照、权威跟随**生效状态**而非 DB 状态、非终态反向保护）；并**反转**既有 `test_snapshot_values_authoritative`——该用例把「终态快照优先」**契约化**了，正是本次事故的成因，改名 `test_terminal_derivations_follow_db_render_not_snapshot` 并写明缘由。11 套件 **666 passed**。

**生产实测（修前基线）**：容器内直调真实 `build_progress_payload` + 真实 DB 行 + 真实 Redis 快照 → `approval_authority = ''`（DB 为 `'李港111'`），断言 `端点仍返回旧值` **失败** ⇒ 与用户现象、与上述根因**完全吻合**。

**遗留**：①已打开的预览不随同屏 modify 刷新（无「新轮」信号，需关重开）——既有已知限制；②运行快照终态 TTL 600s，事件重放兜底在预览打开拉取纠正前可能短暂显示改前值。

**部署清单**：后端单文件 `api/apps/restful_apis/template_api.py` SCP + `docker restart docker-ragflow-cpu-1`。**未部署、未 commit、未 push**。

## 2026-09-17 范本填写：增量基线按工作上下文隔离（修 demo03 跨流程串值，未部署）

**主题**：用户报「我的**新**流程，填写范本，把内容填成我**之前要改的值**了」。查证属实，且与上一个「可见性修复」批次**无关**——是又一条独立的值泄漏通道，同样属于「把上一轮输出自动变成下一轮输入」这一族缺陷。

**证据链（DB）**：三份任务行对比 `_baseline_values` / `_retrieve_skip_keys` / 检索结果：

| task | 流程 | `_baseline_values.tenderer_name` | `_changed_keys` 含 tenderer? | chunks | `_retrieve_skip_keys[0]` |
|---|---|---|---|---|---|
| `70b17a56…` | demo01 | 无 | 是 | 有 | 无（skip_len=0） |
| `9549487c…` | demo02 | A | 否 | **空** | `tenderer_name` |
| `a5b3a53e…` | **demo03（全新流程）** | **B** | 否 | **空** | `tenderer_name` |

demo03 是全新流程，却拿到了 demo02 里手改出的基线值 B。

**根因（一行）**：`agent/component/template_fill.py` 取基线用的是 `TplFillTaskService.latest_done(template_id, tenant_id)`——**只有租户+范本两个维度**；而 `tpl_fill_task.flow_instance_id` 在**三处**建行点全写 `""`（死字段），所以「同一个范本的上一份成稿」在同租户内是**跨流程共享**的。`modify` 恰好把用户手改结果 `patch_values` 写回的就是那一行 `values.render` → 用户在 demo02 的手改动作，静默成了 demo03 的既有结论。

**放大机制（为什么后果是「锁死」而不是「只多填一个字段」）**：基线命中 → `baseline_values` 非空 → 该 key 被移出 `_llm_fill_items` 的候选（LLM 白名单）→ 又被塞进 `_retrieve_skip_keys` → **既不检索也不重填**（demo03 的 `_retrieve_skip_keys` 长 52，demo02 是 44；两次 `chunks=[]`）。于是错值不仅没被纠正，**连纠正的机会都被剥夺**——检索被显式跳过，KB 里的真值永远回不来。

**关键设计判据（决定改法，避免改错）**：`latest_done` 有两个消费方，**语义完全不同**：
- **定位**（工具 `detail`/`modify`：用户说「把 XX 改成 YY」，要找到「最近那份成稿」）——**必须保持宽口径**，跨会话找到用户刚填的稿正是期望行为。收窄它会让「刚在别的会话填过、这里想改」找不到目标（**静默改行为**），故本轮**不动**，只把两处误导性注释改写成「宽是刻意的」。
- **继承**（画布节点取增量基线）——**必须同上下文**，否则即本事故。

结论：**不能共用同一个方法**，拆成两个名字自解释的方法，而不是给 `latest_done` 加一个可选参数（可选参数会让「忘了传」静默退化成跨上下文继承，正是本次事故的形态）。

**核心变更**（后端 4 文件 + 前端 0 文件）：
- **Service 新增 `latest_done_in_context(template_id, tenant_id, context_id)`**（`api/db/services/template_fill_service.py`）：WHERE 增加 `flow_instance_id == context_id` + `status == 'done'`。**`context_id` 为空一律直接返回 `None`（查询前短路）**——安全默认是「走全量重填」，绝不退化成「不限上下文」的宽查；`latest_done` 的 docstring 加反向指引「不要拿它当基线来源」
- **`context_id` 取什么 = 会话 id（`sys.session_id`）**：流程页的「影子会话」（`flow_app.create_flow_chat_session`，每 (流程, 用户) 一条且持久）**就是该流程实例**；对话页的会话即本次会话。选它而非新造字段，是因为「工作上下文」在系统里已有权威载体，新增字段要多一条生命周期要维护且当下无消费方
- **`canvas_service.completion` 写入 `canvas.globals["sys.session_id"] = session_id`**（`api/db/services/canvas_service.py`，+6 行）：组件统一从 `self._canvas.globals` 读 `sys.*`，走 globals 而非新增 run 参数即全员可用。`session_id` 在 `if session_id` 分支是入参、`else` 分支是 `get_uuid()`（已随 `API4ConversationService.save` 落库），两条路径都有值，**服务端单点改动、前端零改动**（与 `flow_version_id` 由前端传的既有先例互补）
- **画布节点取基线改走新方法 + 落对 `flow_instance_id`**（`agent/component/template_fill.py`）：新增 `_work_context_id()`（读 `sys.session_id`，strip，**任何异常/缺失一律空串**）；基线查 `latest_done_in_context(...)`；任务行 `flow_instance_id=ctx_id` 不再写 `""`——**这条是自洽的关键**：新流程第一轮落对了 context，第二轮才能被自己的历史成稿续上，增量填写在同流程内照常工作
- **工具侧只落字段**（`agent/tools/template_fill.py`）：`_fill` 建任务行时 `flow_instance_id=self._work_context_id()`（同会话内后续画布节点方可把它当基线续写）；工具本身**不做增量**。`_modify` 定位仍走宽口径 `latest_done`（见上「定位」判据）

**行为变化（用户可感知，且正是要的）**：**新流程不再继承任何历史成稿 → 首轮走全量**（检索 + LLM 正常跑）；**同一流程内**再填同一范本仍走增量。**代价需知**：`flow_instance_id` 此前全库是空串，本次改动**只对改后新建的任务行生效**，存量行仍为空串——于是「老流程接着填」时 `latest_done_in_context` 查不到自己的历史（`flow_instance_id=''` ≠ 真实会话 id）→ 退化为**全量重填**（安全方向，不会串值，只是少走一次增量）。

**测试**（后端 899 passed / 0 failed，含 10 套范本填写 + file_review + canvas_service 消费方）：
- `test/test_agent_fill_template_component.py` 新增 4 例**对抗性**用例：①**demo03 事故回归门**（历史成稿只存在于 `("t1","other-session")` → 断言**三件事同时成立**：无 `_baseline_values`、该字段回到 `_changed_keys` 白名单、**不在 `_retrieve_skip_keys`**、且任务行落 `flow_instance_id == CTX`）；②**空上下文一律不增量**（`session_id=""` 时即便宽查有稿也不得用，并断言仍以 `("t1","")` 去问服务层）；③上下文 **strip** 后原样下传并落行（`"  sess-9  "` → `sess-9`）；④`_work_context_id()` 单测穷举 `"  sess-2  "`/缺键/`None`/`{}`/无 `globals` 属性/`globals=None`/`int` 类型 —— **一律空串不抛**
- **新增 `test/test_template_fill_service.py`（11 passed）**：桩件测试只能证明「调用方传了 context_id」，**证明不了「服务端真的拿它过滤」**——一个删掉 `flow_instance_id` 条件的改动，桩件测试全绿而生产事故重演。故用 `__func__.__wrapped__` 取回裸函数体 + 假 model 记录列引用，**直接断言 WHERE 里用了哪些列**：收窄口径必须含 `flow_instance_id`、空/falsy 上下文**不得发起查询**、宽口径 `latest_done` **必须不含** `flow_instance_id`（防止后人「顺手」合并两者）。另固化「服务层**不** trim，空格串是 truthy」的分工，避免后人误以为服务层兜了底而删掉节点侧 strip
- **变异验证（证明守卫有牙）**：临时删掉服务端 `flow_instance_id == context_id` 条件 → 该套件如期 `1 failed`（报 `收窄口径丢了 flow_instance_id 过滤 = demo03 事故重演`），已还原并复测 11 passed

**对上一批次的收口**：本条目**解决**了上一批次遗留项②（「`latest_done` 是租户+范本粒度、`flow_instance_id` 全库空串无法收窄」）的**继承侧**——该遗留描述的正是本事故根因，现已按「流程实例隔离」修掉；**定位侧**仍刻意保持宽口径。

**状态**：**已部署 2026-09-17 + 已 commit + 已 push**（HEAD `12228c49`）。部署集：后端 4 文件成套 SCP（`api/db/services/template_fill_service.py`、`api/db/services/canvas_service.py`、`agent/component/template_fill.py`、`agent/tools/template_fill.py`）+ `docker restart docker-ragflow-cpu-1`。

**部署实测（2026-09-17）**：①覆盖前按规程把 4 个文件从服务器拉回 `diff --strip-trailing-cr` 比对，**服务器独有行均为 0**（本地严格超集，可安全覆盖）；②SCP 后 md5 四文件一致；③容器 `import` 冒烟通过，且断言到 `latest_done_in_context` 的 `context_id` **无默认值**、`canvas_service` 源码含 `sys.session_id`、节点 `hasattr(_work_context_id)` 为真且源码不再出现宽口径 `latest_done(`；④**运行时验证（决定性证据）**：容器内直调 `latest_done_in_context('x','y','')` / `(…,None)` / `(…,'no-such-session')` **均返回 None**，证明空上下文短路与 `flow_instance_id` 过滤在真实 DB 上生效；⑤最新 done 行 `a5b3a53e`（demo03 那条）`flow_instance_id` 仍为 `''`，与设计预期一致——存量行不回溯，老流程接着填退化为全量。
**探活教训（易误判，已回写部署参考）**：`/api/v1/template/**` 的 blueprint 把 `@login_required` 挂在 **blueprint 级 `before_request`**，**不存在的路径也回 401** → 「目标 401」**不能**作为「路由已注册」的证据（本次差点据此误报成功）。区分法：打一个**故意写错的同前缀路径**，若同样 401 则 401 无区分力，改用上面的运行时证据。

---

## 2026-09-17 范本填写：就地修改后的可见性修复（卡片消失 + 预览不变，未部署）

**主题**：用户实测报出两个症状——①填写完成后**页面整块空白**，「查看填写内容」按钮消失，刷新重进流程才出现；②LLM 回执说「已全部由 A 改为 B」，但「查看填写内容」的文件预览**文案没变**。设计稿 `docs/superpowers/specs/2026-09-17-template-fill-modify-visibility-design.md`。

**第一性原理（本题的关键判据）**：先确认「用户看到的字从哪来」，再决定改哪里。查证结果是——**「查看填写内容」预览根本不是成稿**，而是「范本**工作副本**（识别阶段 anchor→`{{key}}` 的那份）+ 前端 `tpl.values` 覆盖渲染」。屏幕上的文字完全由前端 state 决定，成稿字节改了它也不会变。**因此「只修后端字节」不可能修好症状②**，这一点决定了修复必须横跨四层。

**两个症状、四个根因（互不相干，任一不修症状都仍成立）**：

- **症状①（卡片消失）— 前端状态层**：`flow-ai-panel.tsx` 的 `handleSend` **无条件**执行 `templateFillRef.current = undefined` / `templateFillEventsRef.current = []` / `setLastTemplateFill(null)`。这在「新一轮填写」语义下对，但 `modify` 轮**一个 `template_fill_progress` 事件都不发**（走工具回执，不走 SSE 进度管道）→ 三处快照被清空且无人回填 → `TemplateFillProgress` 返回 `null` → 成稿卡整块消失。**刷新能恢复**这条线索反向证明了「数据一直在，只是本地状态被清掉」，排除了「后端没下发」的歧义。修复：**发送时不清范本快照**（成稿卡可见性不该由「发了一条消息」决定）；语义不丢——真填写轮由 `streamState.templateFill` 经 `[streamState.templateFill]` effect 在同一 commit 内覆盖（该 effect 声明在报告 effect 之前）。`templateFillEventsRef` 同理保留，本轮无新事件则落库的 `template_fill_events` 沿用上轮快照，「改完刷新」能从**本条**记录重放出卡片。`fileReviewRef.current = null` **不动**（文件审核确为每轮重新发起，语义不同）。**刻意不做**：流式期回落 `?? templateFillRef.current` —— 超出批准范围，且流式期卡片闪一下不是用户报的症状

- **症状②之一（模型改错字段）— 工具语义层**：`detail` 的 keyword 只匹配 中文名/key/锚文本，用户给的是一段**值** → 定位不到 → 挑了个名字最像的 key。实测：该段文字实际是 `project_owner（项目业主）` 的值，模型却把 4 个同名「招标人名称」（`tenderer_name` 及 `_2/_3/_4`）全改了——**LLM 的回执没撒谎**（它确实改了它认为的 4 个字段），用户看到的却是「没改对」。修复：新增**第 4 个匹配通道**——`_current_render()` 取该范本最近一次 done 的 `values.render`，keyword 也在**当前成稿值**中匹配，命中项回显「当前值」让模型自证；`detail`/`modify` 均支持 `task_id` 显式钉住（同范本多份成稿时「最近那份」未必在用户眼前）；工具描述 3 处（`keyword` 参数 / `task_id` 参数 / `使用时机` 段）同步写明「用户给的是**内容而不是字段名**时，先 `detail(keyword=那段内容)` 拿真正持有该值的 key 再 modify，别因中文名听起来像就挑一个」

- **症状②之二（预览不刷新）— 前端渲染层**：新增 `fetchTemplateFillTaskProgress(task_id)` 拉 `templateFillTaskProgress` 端点（其 values 取自 DB render，`modify` 已回写），预览**打开时**拉一次覆盖显示。**仅终态取用**（`TERMINAL_PROGRESS_STATUSES = ['done','partial']`）：流式期 SSE 的 values 更新鲜，不能被压回去；`failed/cancelled` 无稿可取。失败/空值/返回 null → 回落卡片快照，**不清空预览**。`filled`/`unfilled` 按 **null 与否**判定而非 `??`（权威响应里 `null` = 空，不能回落卡片上可能过时的清单，否则已填槽位会悬浮显示上轮中文名）。`values` 包 `useMemo`：两者都空时裸 `|| {}` 每渲染产新对象，会把下游 `updateDocxHighlight` effect 与 `filledCount` 的依赖打成「每渲染必变」

- **症状②之三（下载到的也是旧的）— 存储桥接层**：成稿有**两份**——真源 `{template_id}/v{ver}_result_{task_id}.ext`（`_storage_put` 写）与派生副本 `{tenant_id}-downloads/tplfill-{task_id}`（卡片「查看填写内容」/下载走 `/api/v1/agents/download?id=tplfill-…`）。`modify` 原先只覆盖真源。实测字节：真源 219617 B md5 `7c82405c…`（已含新值）vs 副本 219566 B md5 `0f839570…`（旧值）。**不能靠 REST 层自愈**——`template_api._bridge_download` 的进程内记忆化 `_bridged_tasks` 命中即跳过 get+put，正是它把「对象名确定、内容已过时」放大成静默错误。修复：主成稿落盘后**同名覆盖**派生副本。**不去 invalidate `_bridged_tasks`**——对象名确定性、`_modify` 直接写入即维持了该记忆化的不变式（「已桥过 ⇒ 副本等于真源」），跨层去动 REST 私有集合是分层违规且要在两处维护同一不变式。桥接失败**不当作修改失败**（真源已正确落盘），但回执必带 `bridge_note` 讲明，否则用户会以为模型在编造「已修改」

**测试新发现的既存生产 bug（本轮唯一非由用户症状反推出来的缺陷）**：`_modify` 原 `render = dict(vals.get("render") or {})` / `dict(vals.get("cells") or {})` 在 `values.render`/`cells` 为**字符串或标量**时抛 `ValueError`/`TypeError`，异常冒到 `_invoke` 的顶层 try/except 变成一句用户看不懂的「范本填写执行失败：…」。由 `test_modify_values_non_dict_render_does_not_crash` 击穿 → 加 `isinstance` 判型（与 `_current_render` 同口径），缺失即当空基线（merge 后仍能正确覆盖 patch 字段）。

**测试**：后端 `test_template_fill_tool.py` **67 passed**；范本填写相关 10 套件 **644 passed**；前端 `--testPathPattern="template-fill"` **5 suites / 85 tests**（新增 `template-fill-live-preview.test.tsx` 9 例，走 xlsx 文本分支使渲染确定，可对屏幕文本直接断言）。对抗性覆盖要点：`values` 六种脏形态矩阵不炸；`task_id` 四种非法态（不存在/他人/别范本/非 done）逐一拒绝；桥接失败时主成稿仍在且回执含降级提示（断言 `len(puts)==2` + `puts[1]` 精确指向 `{tenant}-downloads/tplfill-task1`）；keyword 5000 字符时输出被截断（`len(out) < 3000`）；**空值永不命中**（防「空串匹配一切」）；权威 `filled: []` 时不得回落卡片过时中文名；`detail` 无 keyword 时**不查** current（计数器断言，不靠 DB 桩副作用）。**桩内不能抛异常**——`_invoke` 顶层 try/except 会把异常吞成返回串，失败信号一律用计数器（本轮踩过，会假通过）。

**未覆盖项（需人肉验收）**：**修复 3（`flow-ai-panel.tsx` 的 3 行删除）没有单测**——仓库既无渲染 `FlowAiPanel` 的测试也未 mock `use-send-message`，补测需 ~7 个 mock 块并驱动完整 SSE 生命周期，为 3 行删除引入的脚手架成本远高于收益。验收步骤：流程里完成一次填写 → 说「把 XX 改成 YY」→ 观察成稿卡是否**始终在场**。

**遗留（3 项，均已写进代码注释与设计稿 §6）**：①**已打开的预览不随同屏 modify 刷新**（成稿卡状态对象在 modify 轮引用不变，无「本轮是新轮」信号；不加轮询——没信号就轮询等于给每个打开的预览挂永久定时器），需关闭重开；②**`latest_done` 是「租户+范本」粒度而非「本次流程」粒度**——本应由当前流程收窄，但 `flow_instance_id` 全库写空串（死字段）无可用信号；缓解是回执必带 `task_id` + 本地化生成时间让误选**可见**且可由用户带 `task_id` 纠正，根治须先让该字段有值；③修复 3 无自动化测试。

**状态**：**已部署 2026-09-17 + 已 commit + 已 push**（commit `b1867dff`，HEAD 随其后一批为 `12228c49`）。部署集：后端 `agent/tools/template_fill.py` 单文件 SCP + `docker restart`；前端 `template-fill-live-preview.tsx` / `use-template-fill-request.ts` / `flow/flow-ai-panel.tsx`（另含 `__tests__/template-fill-live-preview.test.tsx` 仅入库不参与构建）+ 同批登记 `FillTemplate` 工具的 5 个画布编辑器文件，一并 `npm run build`（1m27s，39M dist）+ dist 上传 + 原地解压 + `nginx -s reload`。
**部署实测**：`curl 127.0.0.1/` → 200；容器内 `curl /chunk/js/locale-zh-BiKDwP1h.js | grep -c 范本填写工具` → **1**（构建产物与服务器所服务 chunk 名一致，确认新包已生效，非旧缓存）。

---

## 2026-09-17 范本填写：写回范本库改按钮触发（停止自动沉淀默认值，已部署 2026-09-17）

**主题**：解决用户痛点「范本只要被 LLM 填写过一次，下一轮新流程再填这个范本，产出的就是上一次的内容——占位符像是被覆盖掉了」。设计定稿 `docs/superpowers/specs/2026-09-17-template-fill-manual-sediment-design.md`。

**第一性原理**：用户看到的「占位符被替换」是误判——成稿按任务独立（`result_file_id = v{ver}_result_{task.id}.{ext}`），范本文件与 `{{key}}` 占位符**从未被改写**。真正被改写的是版本行的 `placeholders[].default_value`：`executor` pipeline 第 ⑦ 步与工具 `modify` **无条件**把每轮全部非空产值沉淀进去（`default_source="auto"`）。下一轮的连锁反应是：244 个字段全带默认值 → `_confirm_changed_fields` 的 `default_map` 非空 → 确认卡出现但 AI 只预判少数几项 → 未勾字段经 `_merge_default_values` 用上轮值回填 → 检索+LLM 几乎不跑 → 「新流程被上一轮内容占满」。机制上的根因是**语义错配**：`default_value` 的正当用途是「同一范本在组织内的稳定取值」（招标人名称、报建编号），而「本轮 LLM 恰好填了什么」只是**这一次的结果**，把后者自动写进前者等于让单次运行静默改写范本基线。

**用户拍板的四条口径**：①按钮语义 = **只沉淀默认值**（写 `placeholders[].default_value`，`source=auto`；范本文件与 `{{key}}` 原样不动）；②位置 = **C端成稿行**（对话页 + 流程页签）；③范围 = **仅本轮确认卡里用户/LLM 显式改动的字段**；④存量数据**本次不动，只停止新的自动沉淀**。

**核心变更**（后端 4 文件 + 前端 3 文件 + 测试 4 套件）：
- **Service `only_keys` 必填硬闸**（`api/db/services/template_fill_service.py`）：`_sediment_into_placeholders(placeholders, values, only_keys, override_keys=None)` 循环内 key 判空后立刻 `if key not in only: continue`。**刻意不给默认值**——`None`（不限制）与 `set()`（一个都不写）语义天差地别，留默认值就等于给「静默全量写」留后门，而本接口的存在意义正是根除该缺陷；去掉自动调用点后生产调用方只剩新端点一处，故能安全收紧为必填。`override_keys` 原语义与 `manual` 保护、`MAX_ANCHOR_LEN` 截断、空值不沉淀等不变量一律不动
- **删除两处自动沉淀**：`executor` pipeline 第 ⑦ 步整段 + 工具 `modify` 内的 sediment try/except（均连同注释）。`_merge_default_values` 仍在、`default_value` 仍是兜底来源、确认卡触发条件不变——只是默认值从此**只由人工按钮产生**
- **新端点 `POST /template/fill/fill-task/<task_id>/sediment`**：owner 校验（照 `get_fill_task_progress` 骨架）→ 状态闸（非 `done`/`partial` 拒绝）→ **保留键闸**（`_changed_keys` 与 `_direct_values` **两者都缺**即拒，故意不退化全量——那等于把老 bug 从后门放回来）→ 白名单空集则 `written=False` 且**不碰 DB** → 版本行（任务钉住的版本优先，为空回落 `latest()`）→ `sediment_defaults` 异常包 `logger.exception` + 「写回失败，请重试」
- **白名单 = 并集**：`only_keys = set(_changed_keys) | set(_direct_values)`。**必须取并集**——`_llm_fill_items` 刻意把直填键从 `_changed_keys` 剔除；漏掉直填键会让「用户在卡里手打的值按了按钮却没写回」。判据用 **key 是否存在**（`"_changed_keys" in params`）而非并集是否非空：noop 轮两者都在但为空集，必须落「写 0 个」而非「无限制全写」。`override_keys` 只传 `_direct_values` 的键（用户手打是显式决策，可覆盖 `manual`；LLM 产的值不行）
- **拒绝复用 `split_canvas_params`**：`_changed_keys` 传字符串时它会把字符串**逐字符**当 key（CHAR 迭代），单字符 key 理论上可命中真实字段 → 端点显式 `isinstance` 判型（非 list/dict 按空处理），宁可「脏数据下落为空集不写」也不给误命中的可能
- **前端按钮为独立子组件** `TemplateFillSedimentButton`（`templates.map` 回调内不能用 `useState`，`TemplateFillFilledList` 已是同款先例），挂在成稿行 `<a>下载</a>`（带 `ml-auto`）**之后**天然靠右，形状即 `[下载] [写回范本库]`；`t.task_id` 存在才渲染（旧消息/脏数据宁可不给入口也不发无效请求）。四态 `idle → loading → done/empty/error`（error 可再点重试，`title` 带服务端文案）；文案取短版「写回范本库」，完整语义「沉淀为默认值，不改变范本文件本身」放 `title`——成稿行是窄列。**不用** `extraAction` 槽（`:241` 只有 flow 传「存为流程版本」，而本按钮对话页也要有）。前端不走 `useMutation`（沉淀结果不在前端读模型里，无缓存需失效），照 `confirmTemplateFill` / `testTemplateFill` 直调 async 函数

**行为变化（需向用户明示）**：自动沉淀停掉后 `default_map` 更常为空 → **确认卡出现的频率下降**（只在范本确实有默认值时才弹），首轮/未点过按钮的范本全量走检索+LLM。这正是用户要的「不点按钮就按当前流程的填写内容展示」。

**测试**：后端 **624 passed**（`utils` 198 / `executor` + `tool` + `agent_fill_template_component` + `api_routes` / 新 `sediment_api` 17 / `progress_api` + `run_snapshot` + `delegate` + `events`）。新增含对抗性覆盖：白名单是**硬闸**（白名单外字段即使产值非空、即使是 `manual` 也一个字节不动，用 `snapshot` 逐项比对）、`only_keys=set()` 时全量产值也不写且占位符逐字节不变、幽灵 key 不报错不新建、`override` 与 `only` 是**与**关系（只在两者都命中时才覆盖 `manual`）、非字符串项不误命中 + 非 hashable 抛 `TypeError`（固化失败模式）、placeholders 混入 `"junk"`/`None`/无 key 项时正常项照写；端点侧：**保留键闸反向门禁**（`params={}` / `{"a":"b"}` / `None` / 字符串 / 列表 / 数字一律拒绝且零写入）、`_changed_keys` 与 `_direct_values` 的**类型脏数据矩阵**（字符串不会被逐字符当 key）、`values` 七种畸形形状不炸、版本回落与彻底缺失、幂等（第二次 `written=False`）、异常出富文案。端点的沉淀语义用**真实** `_sediment_into_placeholders`（纯静态方法，不触库）在内存 placeholders 上跑——只测桩的话「端点算出的 only_keys 是否真的落到字段上」这条链路完全没被覆盖。前端 **76 passed**（`.scratch/jest.local.cjs` 脚手架）；`tsc --noEmit` 对三个改动文件零报错（仓库既存 529 行历史松散类型债与本批次无关）。

**收口审查**（`superpowers:code-reviewer`，1 Major + 5 Minor 全部处置，详见设计稿 §7）：
- **Major 保留键闸可伪造**：写回端点以 `params` 是否带保留键判定「有确认记录」，但 REST 入参同样能带这些键 → 任意 key 可被写进 `default_value`。审查建议的修法（改用 `task.source == "canvas"`）**不成立**——`source` 同样是请求体字段（`template_api.py` 的 `source=(source or "web")[:16]`）。改在契约真正可能被破坏处加固：`create_fill_task` 剥离 `CANVAS_RESERVED_KEYS`，使「带保留键 ⇔ 画布写入」成为不变式。剥离**零副作用**（`validate_placeholders` 要求 key 匹配 `[a-z][a-z0-9_]{0,63}`，`_` 前缀键不可能是合法 param 键），顺带堵上同源的**既有**缺口——executor 的 `is_canvas` 门控此前同样可被 REST 调用方伪造，从而误用 LLM 白名单/直填覆盖/检索收窄。常量随 `_CANVAS_RESERVED_KEYS` → `CANVAS_RESERVED_KEYS` 升为跨模块契约，定义处与两个使用点均写明该等价关系；配 2 个对抗用例（`test_create_fill_task_strips_canvas_reserved_keys` 含「非保留键的下划线键必须保留，只按白名单剥离不通杀前缀」、「只含保留键 → 落空 dict 而非 None」）
- **Minor `find_running` 会复用非画布任务**：后果比审查描述更重——`is_canvas` 变 False 会让画布的勾选/直填决策**整批丢弃**按全量 LLM 重跑，且该行终态后写回按钮**必然报**「缺少确认记录」。限定 `source="canvas"`（唯一生产调用方即画布节点）
- **Minor `empty` 文案只描述三分之一成因**：`written=False` 有三种来源（白名单空 / 全受 `manual` 保护 / 新值等于现值），前端无法区分（设计明示返回值保持 `bool`）→ 放宽文案与 `title` 使三者都成立，不硬猜一种误导用户
- **Minor `_modify` docstring 与行为不符**：删调用后仍暗示会沉淀 → 改正并写明「改动字段不进白名单」
- **Minor 并发点按钮丢更新**：`select → 内存改 → save()` 整列覆盖无 `for_update()` → 判定可接受（既有形态、概率低、再点一次自愈，且设计 §6-4 明令不动该链路），按建议记入 `sediment_defaults` docstring 作为已知失败模式并写明将来修法
- **Minor 前端测试缺失**：原「无测试脚手架故不写」的结论不成立 → 补 `template-fill-sediment-button.test.tsx` 6 例（挂载口径 2 + 四态 4；桩掉 `use-template-fill-request` 与 `template-fill-live-preview`，后者顶层 import docx-preview）
- **附带**：修掉一处**与本批次无关的既存测试腐坏**——`template-fill-confirm-card.test.tsx` 在 `388ce463`「确认卡默认折叠」后未更新，5 例全红（需先点「点击展开调整」），抽 `expandCard()` 修复

**部署实测（2026-09-17 已部署，用户指示）**：
- **部署前先核对服务器版本**（关键一步）：工作区同时叠着上一批「已填字段中文名」的未提交改动（该批 2026-09-17 已部署），故先把 5 个候选文件从服务器拉回 `.scratch/server/` 与本地 `diff --strip-trailing-cr` 比对——**服务器独有行数全为 0**（本地是严格超集，不存在需保留的服务器侧热修），`agent/component/template_fill.py` 差异 **0 行**（确认上批次确已完整落地）。若跳过此步，无法排除「服务器上有本地没有的直改」被覆盖回去。
- 后端 **4 文件成套 SCP**：`api/db/services/template_fill_service.py` / `rag/svr/template_fill/executor.py` / `agent/tools/template_fill.py` / `api/apps/restful_apis/template_api.py` → 回读 **md5 四文件全部一致**（`bc6597b3…` / `48cd9426…` / `eee04b5f…` / `d086a404…`）→ `docker restart docker-ragflow-cpu-1`。
- **import 冒烟**（容器内）：`CANVAS_RESERVED_KEYS` 已加载（5 键元组）、`sediment_defaults` 签名为 `(template_id, version_id, values, only_keys: set, override_keys=None) -> bool`——**`only_keys` 无默认值**，必填闸门确认生效。
- **残留检查**（均应为 0，实测全 0）：容器内 `executor.py` 的 `sediment_defaults` 调用、`agent/tools/template_fill.py` 的 `sediment`；`service.py` 的 `only_keys` 8 处、`find_running` 的 `source == "canvas"` 1 处均在位。
- 前端 `npm run build`（**1m37s**，`tsc` 无新增报错）→ tar **39M** → SCP → `rm -rf dist/* dist/.[!.]* dist/..?*` 就地解包（**未用 `mv`**，保 bind mount inode）→ `nginx -s reload`。
- **HTTP 冒烟**：首页 200（8358 字节，与容器内 `index.html` 一致）；`/chunk/js/index-DmkGVcxP.js` 200 且**命中「写回范本库」「本轮无可写回改动」**（新按钮确已上线）；入口 chunk `/5d41402abc4b2a76b9719d911017c592/entry/js/index-BCo-cRDu.js` 200。
- 端点探活：`POST /api/v1/template/fill/fill-task/<id>/sediment` 无 Authorization 头 → **401**（非 404，证明路由已注册且鉴权在生效），对照已有 `/progress` 同为 401。
- **踩坑（新增）**：API 路径前缀是 **`/api/v1`**（`web/src/utils/api.ts:2` 的 `restAPIv1`），首次探活误用 `/v1/...` 得 404，一度误判为「路由未注册」——**用已有端点做对照探针**（同前缀打 `/progress`）即可立刻区分「前缀错」与「未部署」，是本轮避免误诊的关键动作。另注意 `docker exec ... curl -o /tmp/x` 后接的 `grep` 仍在宿主机跑，管道要整段包进 `sh -c`。

**遗留**：
①**未 commit、未 push**（代码已上线运行）；
②`modify`（对话里说「把 X 改成 Y」）改动的字段**不进白名单**，点写回不沉淀该字段——需要就下一轮再改一次；
③任务钉住旧版本而范本已升版时，写回落在旧版本行（不影响新版，也不报错）；
④「无确认卡 → 白名单 = 全部 llm key」在首轮会一次写入全量字段（按按钮即授权的有意设计）；如认为过宽可后置为「只写有直填值的字段」；
⑤存量 `default_source="auto"` 默认值**不动**（用户明确选择「本次不动」），B端可经 `PUT /template/fill/<id>/defaults {key: ""}` 手动清；
⑥按钮无「已写回」持久化态——刷新后回到可点态，重按幂等无害。

**待人工验收（9 条，均为浏览器侧，未执行）**：①全新范本（无默认值）首轮填写 → 成稿行出现「写回范本库」；**先不点**直接开新一轮 → 应重新走检索+LLM、**不出现上轮内容占满**（本需求核心回归点）；②点按钮 → 「已写回范本库」；B端范本详情确认对应字段 `default_value` 变成本轮值、`default_source=auto`，且**范本文件与 `{{key}}` 未变**；③再点一次幂等；④有默认值的范本开新一轮 → 确认卡出现，只勾 2 项 + 直填 1 项 → 写回后只有这 3 个 key 被写、其余默认值原样保留；⑤noop 轮 → 提示「本轮无可写回改动」，B端零变化；⑥刷新（走运行快照恢复）与断连重连后按钮仍在且可用；⑦failed 任务成稿行无按钮；非画布（B端发起）任务点按钮应回「不支持写回」（前端未按 source 区分，后端已拦）；⑧越权 `curl -i` 打他人 task_id → 「任务不存在」；⑨对抗：`curl -i POST .../sediment` 打一个 `params={}` 的 B端任务 → 必须被拒（不能从后门全量沉淀）。注：⑧⑨ 的端点级门禁已有自动化用例覆盖，浏览器侧验收主要看 ①–⑦。

## 2026-09-17 范本填写：已填字段中文名可见 + 按旧值匹配字段（已部署 2026-09-17）

**主题**：解决用户痛点「第一轮 LLM 把范本填完了，我说『把 XX 改成 YY』，LLM 识别不到」。设计定稿 `docs/superpowers/specs/2026-09-17-template-fill-filled-names-design.md`。

**第一性原理**：用户指代一个字段只有三种可能——**说中文名**、**说英文 key**、**复述已填的旧值**。前两条此前名义上支持，第三条被 prompt 明文禁用；而前两条实际也不可能用，因为**已填字段的中文名在整条链路上没有任何展示位**（`filled` 事件只有 `values`，卡片只列「⚠ N 个未填充」，预览里已填值只有 `title=key`、未填槽位显示英文 key）。可见性是可指代的前提。故本批次两件事必须同时做：把中文名送上屏 + 把「按旧值定位」这条路放行（但加确定性闸门）。

**核心变更**（后端 3 文件 + 前端 5 文件 + 测试 7 套件）：
- **`executor.derive_filled`**（新）：取「有 key 的填写点 − `derive_unfilled` 集合」的**减法构造**，让互补性由构造保证而非靠两处谓词写法一致——两处各写一遍判空，任一漂移都会让 `key→中文名` 映射静默漏项（漏项时 LivePreview 悄悄回落英文 key，用户看不出错但用不了）。**不下发 value**（前端从已下发的 `values` join；`values` 已 30–80KB，`filled` 仅约 13KB）
- **下发协议与 `unfilled` 完全同构**：终态（`done`/`partial`）才下发、空列表归一为缺省不下发、缺省不清旧值、不落 Redis run snapshot（API 现算，避免第二真源）。三条路径全覆盖：画布 SSE `filled` 事件 / `progress` 端点 / `fill-run/<id>/snapshot` 端点
- **组件 `_render_of` 归一化**：`row.values.render` 为非 dict 真值时旧代码抛 `AttributeError`，在观察者循环里会带走整轮多范本填写；改为归一为 `{}`（= 全部未填），行为与旧 `values.get("render") or {}` 逐字节一致，只把崩溃转成降级
- **`PATCH_EXTRACT_SYSTEM` 放宽 + `current_ok` 确定性闸**：新增每项布尔 `current_ok`，**三种成因一律 false**（宁漏不误——漏了还有 name/key 两条路，误了直接改错文档字段）：① `len(c) < MIN_CURRENT_MATCH_LEN(2)`：判据是「原话逐字包含该值」，而 `是/无/男/0` 单字符在中文里几乎必然作为子串出现在任意原话中（「但是」「是否」）→ 必然误命中，**即使清单里唯一也禁用**；② 值歧义：精确重复（`张三` 同时是两个字段的当前值）**或互相包含**（`数量 5 / 金额 50`、`张三 / 张三丰`、`朝阳区 / 朝阳区人民政府`）——用户说「把 50 改成 60」时两个字段都满足「逐字包含」而 prompt ③ 无并列消歧规则 → 双方禁用；③ 截断（`len >= DEFAULT_HINT_MAX=100`，长值尾部不可复述）。歧义计数建在 `_clean_for_prompt` **之后**（故 `张\x00三` 与 `张三` 正确撞值）。**不清空不可用的 current**（会让 LLM 误判「该字段当前为空」，影响 noop/`fill_unfilled` 意图判断），**不调大 `DEFAULT_HINT_MAX`**（它同时是产值 prompt 的 token 预算）。定位优先级写死为 ① name → ② key → ③ 仅前两者都匹配不上且 `current_ok=true` 且原话逐字包含该值时才用 current；prompt 里的三种 false 成因文案与代码实际产生的原因**逐字对齐**（曾有文案称「太短」而代码从不为长度判 false 的错位，测试用 marker 守护）
- **前端 key→中文名映射**：`buildKeyNameMap(tpl)` 由 `filled ∪ unfilled` 合并派生——两者按判空口径穷尽且互斥，并集即全量，**无需新增 `key_names` 字段、不改 `selected` 事件**（否则两个 name 真源各自随版本漂移）；`buildFilledRows(tpl)` 保序 join `values`，缺失/null/非字符串一律归一（不为脏数据崩、也不显示 `undefined`）
- **成稿行「已填充 N 个填写点」折叠清单**：默认收起、**条件挂载**（展开才建 DOM，244 项常挂 DOM 无意义）、中性色，与上方未填充汇总（黄标/红标、默认展开）形成「待办醒目 / 已完成收起」的主次；点击字段名沿用未填充汇总的 `liveTarget + focusKey` 定位链路。折叠态必须是独立子组件（`templates.map` 回调里不能用 `useState`）
- **实时预览中文名穿透**：`stylePlaceholderSpan` 是**唯一**写 `title` 的地方且被 `updateDocxHighlight` 每次 values 变化重跑——「渲染完再遍历 span 补 title」会被覆盖，是假降级。故 `names` 作为**可选参数穿透** `applyDocxHighlight` / `updateDocxHighlight`；已填 `title='中文名（key）'`、未填 `textContent=中文名||key`；`data-ph-key` 恒为 key（定位链路依赖，绝不能动）。四象限逻辑抽成纯函数 `describePlaceholderSpan(value, name, key)`，DOM 副作用留在原处。**未碰 `highlightDocxRanges`**（review-panel / B端保真链路）
- **前端判空口径对齐后端**（`isPlaceholderFilled`）：原先前端用 `v !== undefined && v !== ''` 把**纯空白**算已填，而后端 `derive_unfilled` 用 `not str(v).strip()` 算未填。本需求把 unfilled 清单摆到卡片上后矛盾首次可见：同一字段「卡片列为未填充、预览却是无虚线框的空白槽」，点击定位也失去落点。抽出导出纯函数 `isPlaceholderFilled`（`null`/`undefined`/纯空白 → false，`"0"`/`"false"` → true，与后端一致），保真路径 `stylePlaceholderSpan` 与文本降级路径 `renderText` 同改一处判据

**测试**：后端 **257 passed**（`executor` 116 / `delegate` / `events` / `progress_api` / `run_snapshot` / `agent_fill_template_component`）。新增含对抗性覆盖：`derive_filled` 与 `derive_unfilled` 的**分区穷尽且互斥**（反漂移核心）、`values=["not","a","dict"]` / `values="x"` / placeholder 含 `"junk"`/`None` 的**试图让代码出错**用例（固化现状、防后续静默改变失败模式）、`current_ok` 全成因矩阵（唯一值可用 / 精确重复 / **单字符** / **子串包含双向** / **控制字符剥离后撞值** / 截断边界 99/100/101 / 空值）、prompt marker 守护（三种成因文案缺一即失败）、`render` 为字符串时事件**两者都不带且不抛**、run snapshot 的 selected/filling/failed 行**不带** `filled`。

**前端单测 75 passed**（`.scratch/jest.local.cjs` 本地脚手架，仓库 `web/jest.config.ts` 对 `umi/test` 的依赖仍未修）；新增 `describePlaceholderSpan` 四象限 + 空白串/null 算未填（对齐后端 strip 口径）+ `"0"`/`"false"` 算已填 + name 为空串回落 key；reducer 新增**阶段隔离**（`filling` 事件携带 `filled` 被忽略）、`filled` 有值但无 `values` 不崩、重复 key 原样产出（契约固化）。

**收口审查**：本机 `konus-code-review` 技能已不存在（`~/.claude/skills` 与 plugins 下均无），改用 superpowers `code-reviewer` 代理执行。出 1 Major + 3 Minor + 5 Nit，**Major 与全部 Minor 当批修复**：Major = `current_ok` 只拦精确重复、漏了子串包含歧义（`5`/`50`、`张三`/`张三丰` 在「原话逐字包含」判据下双双命中，prompt ③ 无并列消歧 → 可能改错字段），且 prompt 文案称「太短」而代码从不为长度判 false → 补 `MIN_CURRENT_MATCH_LEN` + 子串包含互查 + 文案对齐；Minor = 前端空白串判空与后端漂移（且新测试注释把后端口径写反）+ prompt 语义描述不符 + `null` 值三处口径不一 → 抽 `isPlaceholderFilled` 统一；Nit 中「`buildStateFromRunSnapshot` 用 `||` 而非 `??`」经核为等价（该函数整体重建状态，无旧值可保）故不改，「事件 64KB 截断威胁 `filled` 长期可恢复性」录入遗留⑥。审查另外明确确认**无问题**：互补性构造上穷尽（不存在 keyed 项被双清单漏掉）、三路下发缺省语义同构、`names` 必须穿透（后补会被覆盖）、current 新路径确实受三层兜底约束、`_unfilled_of` 重写对既有行为逐字节等价、React memo 依赖与条件挂载正确。

**遗留**：
①~~未部署~~（**已部署 2026-09-17**：后端 3 文件成套 SCP + md5 逐一核对 + 容器重启；前端 `npm run build`（1m24s）+ dist 39M 上传 + `rm -rf dist/*` 就地解包（873 文件）+ nginx reload。部署实测见下方；**纯增量字段，前后端可独立部署**：旧前端忽略 `filled`，新前端遇旧后端不下发即回落英文 key）；
②`derive_unfilled` 仍不过滤 `isinstance(it, dict)`、`values` 非 dict 真值时 `(values or {}).get` 会抛——本次只在测试里固化现状，**未修**（真实调用路径已由 `_render_of` 归一挡掉）；
③`DEFAULT_HINT_MAX=100` 截断 `current` ⇒ 长值字段无法按旧值定位（设计上接受）；
④值歧义（精确重复 / 互相包含 / 单字符）时按旧值定位不可用，只能按 name/key——**单字符是保守过杀**（全表只有一个「男」字段时其实可定位），取宁漏不误；
⑤`filling` 阶段 `filled`/`unfilled` 都未到达 ⇒ 预览暂无中文名、回落英文 key（终态后即恢复）；
⑥`filled` 的长期可恢复性依赖 Redis run snapshot，**不能只靠事件回放**：`template_fill_events` 有 64KB 截断挽救（只保头部事件），而终态事件在尾部且体积最大（`values` 30–80KB + `filled` 约 13KB）最易被截；超 600s 终态 snapshot TTL 后回放会同时丢 `unfilled`/`filled`（非本批次引入）；
⑦**未 commit、未 push**。

**部署实测（2026-09-17）**：
- **上传前 diff 校验**：`scp` 取回远端三文件后 `diff --strip-trailing-cr --unified=1`，改动量为 executor `+62/-4`、component `+40/-3`、api `+13/-2`，逐行核对**全部落在本批次意图内、零意外改动**。副产物结论：远端 executor **已含** `PATCH_EXTRACT_SYSTEM`/`_clean_for_prompt` 的增量基线 ⇒ 上一条「范本填写增量模式」实际已随此前批次上线（该条目「未部署」字样已过时）。
- **md5 三文件逐一核对**：上传后远端 `b8da6d68…` / `c44a763b…` / `1d696468…` 与本地一致。
- **容器内 import 冒烟**：`derive_filled` / `derive_unfilled` / `MIN_CURRENT_MATCH_LEN` / `_filled_of` / `_render_of` / `build_progress_payload` / `build_run_snapshot_payload` 全部导入成功，`MIN_CURRENT_MATCH_LEN=2`；实调 `derive_filled([{a,甲},{b,乙}], {a:'x', b:'  '})` → `[{'key':'a','name':'甲'}]`，**纯空白值被正确判为未填**（与 `derive_unfilled` 的 strip 口径互补性在生产代码上得到验证）。
- **生产数据端到端（最有价值的一条）**：容器内以端点同路径（`TplTemplateVersionService.get_by_id_checked` 取 placeholders → `build_progress_payload`）跑**真实 DB 全部 18 条 done/partial 任务**，断言 `filled ∪ unfilled == 有 key 的填写点集合`、交集为空、无越界 key —— **18/18 全部 OK，零 BAD**。典型分布 `filled=107 / unfilled=137 / keyed=244`（244 字段真实范本，正是用户痛点的那个范本）；样本 `[{'key':'report_build_no','name':'报建编号'}, {'key':'tender_no','name':'招标编号'}, {'key':'tender_issuer_name','name':'招标人'}]` 印证中文名已正确随 `filled` 下发。
- **前端产物核对**：远端 `dist/` 873 文件、容器内 bind mount 同步可见；`grep -rl` 命中新增文案 `已填充`(1) / `等待 AI 填入`(2) / `个填写点`(2) 个产物文件。
- **接口存活**：`/api/v1/template/fill/fill-task/<id>/progress` 无 `Authorization` 头返回 401（路由已挂载、鉴权闸生效，非 404）。
- **计划偏差 / 操作失误**：清理 `.scratch/deploy_cmp` 的命令里**误把 `docker restart` 串进了 `&&` 链**，导致容器被多余重启一次；已复查 `status=running` + 重跑 import 冒烟通过，与代码经 bind mount 落盘无关，无实质影响。

**待人工验收（8 条，未执行）**：①真实留空范本三种终态核对卡片「已填充 N 个」计数/中文名/值/点击定位落点；②docx 保真路径已填悬浮 title 含中文名、未填槽位显中文名；③刷新走 run snapshot（600s 内）与断连重连走轮询两端点 `filled` 不丢；④「把『<唯一旧值>』改成 X」→ 确认卡只列该字段且 `direct_value` 预填；⑤**故意用 `5`/`张三` 重试**验证子串包含闸门（应匹配不上、不误改）；⑥新旧前后端兼容矩阵；⑦流程页签复用同卡片；⑧xlsx 路径。

## 2026-09-17 文件审核收口遗留修复批次（R-1 中断轮次前端可识别 + R-2/R-3，已部署 2026-09-17）

**主题**：收口审查遗留 R-1 ~ R-8 的排查结论中，R-1（Major）与 R-2/R-3（Minor）在本批修复；R-1 只做「用户可识别 + 有出口」的一半，**不做**启动期扫描（需改上游核心文件 `api/ragflow_server.py`，未获授权）。

**第一性原理**：R-1 的本质不是「状态没回落」，而是**轮次行自称在跑、却没有任何活体会回来写它**——这是一个**可判定的谓词**，不该靠「进程启动时回头扫库」这种事后补偿去猜。判定它需要三个事实同时成立：①状态在 `RUNNING_ROUND_STATUSES`；②`spawn.is_running(task_id)` 为 False（进程内注册表是权威）；③行龄超过宽限期（避开 `create_round(status="fixing")` 与 `spawn_review_task` 之间那个「行已 running、线程未注册」的微秒窗口）。三者齐备即判 stale，无需任何新持久化、无需任何新表。于是「数据库不回落」这个既成事实不再需要被修复——只需要被**读出来**并如实告诉用户。

**核心变更**（后端 3 文件 + 前端 3 文件 + 测试 4 套件）：
- **Service 层 stale 派生**（`api/db/services/file_review_service.py`）：新增 `STALE_GRACE_SECONDS = 60`、`_now_ms()`、`is_stale_running(row, now_ms=None)`（三判据如上一段；**直接访问 `row.create_time` 不做 getattr 兜底**——缺列应响亮报错而非静默把正常轮次判死）。本模块顶层**不**新增对 `spawn` 的依赖边，import 延迟到函数内（与 `admit_fix_round` 同款取向）
- **`admit_fix_round` 新增 stale 闸门**（`reason="stale"`）：**必须在 running 闸门之前判**——两者 status 同属 `RUNNING_ROUND_STATUSES`，顺序反了会把「永远不会好」错答成「等一会就好」，这是唯一会让用户白等到天荒地老的答复；反向不误伤，判据②已要求 `is_running` 为 False。报文含轮号 + 状态中文 + 「请重新发起审核」
- **`no_pending` 富文案下沉（R-3）**：新增 `SEVERITY_RANK` / `severity_cn()` / `severity_summary()` 于 Service 层单点实现，`agent/tools/file_review.py` 删掉本地 `_SEVERITY_RANK` / `_severity_cn` / `_severity_summary` 改为 import。两入口（REST / 对话工具）现在逐字共用同一份拒绝文案：`没有【严重】级别的待修复问题。待修复问题：共 3 条（严重 2 条、一般 1 条）`——用户/LLM 不必靠猜或反复试就知道该改选哪个级别
- **R-2 docstring 对齐**：把假的「闸门 3 必须在 5 之后」换成诚实取舍说明——`is_running`（查进程内 set，近零成本）排在 `no_pending`（要读全量标注）之前是**取舍而非硬约束**，代价是同时命中时先给「稍等片刻」、用户重试一次即得真因
- **API 层透传 stale**（`api/apps/restful_apis/file_review_api.py`）：`_round_payload` 新增 `"stale": is_stale_running(row)`，本层只透传不自创判据；顺带修正 `review_state` docstring 里 `doc.object` 的残留描述（它有成稿时是 MinIO 对象名，**不是**上传系统的 file_id，不能拿去拼 `/api/v1/files/<id>`）
- **对话工具中断分支**（`agent/tools/file_review.py::_format_status`）：stale 判定**在取中文名之前**算——stale 行 status 是 reviewing/fixing，直接映射会报「审核中 / 修复中」，与「永远不会有结果」正好相反；命中则显示「已中断」+ 重新发起引导
- **前端 stale-aware**（`web/src/hooks/file-review-stream.ts` 加必填 `stale: boolean` + 注释明确「前端不得自己按时间重算」；`use-file-review-request.ts` 的 `refetchInterval` 加 `&& !current.stale`，否则僵尸轮次每 3s 白打接口且永远转圈；`pages/c-chat/file-review-progress.tsx` 抽出 `STATUS_CN` 映射、`isRunning` 排除 stale、`canFix` 排除 stale（服务端 stale 闸门拒绝一切 fix，留按钮＝给用户一个必然失败的入口）、状态 label 转红 + 新增中断提示行 + 新增 `current.error` 失败原因行——此前 failed 轮只显示「失败」两个字，与工具侧口径不一致）

**测试**：`test_file_review_{service,api,tool,node,spawn}` + `_e2e` + `db` + `executor` + `patcher` + `kb_aggregator` 共 **337 passed**。新增/强化 12 个用例，含对抗性覆盖：宽限期 **off-by-one 边界**（恰好等于 60s 不算 stale）、线程存活时**绝不**判 stale、终态行不参与、缺 `create_time` 列**断言 AttributeError**（响亮失败而非静默）、stale 与 running **同时命中时 stale 必须优先**、`no_pending` 报文断言含级别名 + 总数 + 分布、未登记 severity 原样透出。`ruff check` 六改动文件全过；前端 `eslint` 四文件 0 告警、`tsc --noEmit` 对本功能零报错。

**前端单测（本批次真正跑起来了）**：此前「jest 不可运行」的结论**已作废**——仓库 `web/jest.config.ts` 确实依赖已移除的 `umi/test`（配置级损坏），但用 `.scratch/` 下的等价本地配置可以完整跑通（见下「前端测试基建」）。本功能两套件 **16 passed**：`web/src/hooks/__tests__/file-review-poll.test.ts` 新建 6 用例（`shouldPollFileReview` 纯函数：reviewing/fixing 续轮询、**stale=true 即便 status 仍 running 也必须停**、终态停、`current` 为 undefined 停、对抗——空串/未知/null status 一律停（白名单而非黑名单，服务端将来加状态时默认行为必须是「不轮询」）、对抗——`stale` 为 undefined（旧服务端）时仍按 status 轮询（向前兼容））+ `web/src/pages/c-chat/__tests__/file-review-progress.test.tsx` 10 用例（含新增的「fix 被服务端闸门拒绝时 Popover 必须显示服务端文案（不吞错）」）。

**顺带修掉 2 个「从未跑过」的既有断言缺陷**（jest 修通后立刻暴露，属既有文件既有缺陷，与本批次改动无关，一并修）：
- `file-review-progress.test.tsx` 的 fixture `current.summary` 是空串却断言 `/high:1 medium:0 low:0/`——组件渲染的是 `current.summary`。服务端 `current = _round_payload(rounds[-1])` 是同一份 payload 的投影，fixture 两者不一致会造出线上不可能出现的态，已把 fixture 补齐并加注释说明该一致性契约；
- 同文件 `getByText(/正在审核|审核中/)` 同时命中 spinner（「正在审核…」）与状态标签（「第 1 轮 · 审核中」）而抛多元素异常，收紧为 `/正在审核/`——与本次审查回合抓到的 M1 是同一类写法，说明该写法在本仓库是重复踩的坑。

**遗留**：
①~~未部署~~（**已部署 2026-09-17**，与下一条「全链路」同批执行——服务器此前对 file_review **零部署**，故按 T17 清单成套 SCP **10 文件**而非本批次改动的 3 个；实测记录见下方「部署实测」）；
②**R-1 只做了一半**：不做启动期扫描 ⇒ 已产生的中断轮次**不会自愈**，用户仍需「重新发起审核」才能继续（此刻该 task 的 fix 永久被拒）；补全需改 `api/ragflow_server.py`（上游核心文件），未获授权故未碰；
③**R-6/R-7/R-8 仍只记录未改**（REST `_ADMIT_LOCK` 同步阻塞最长 5s / `levels=None` 会 TypeError（不可达）/ `doc.object` 下发 MinIO 对象名）；
④**进度卡吞掉 fix 拒绝文案——已修**：`file-review-progress.tsx` 的 `fixMutation` 此前只用了 `isPending`、全组件无 mutation 错误渲染（`web/src/app.tsx` 的 `QueryClient` 也未配全局 `onError`），用户点「确认修复」被拒时界面毫无反馈（Popover 停留、无提示），R-3 的富文案在卡片这条路径上等于白下沉。现新增 `fixError = fixMutation.error?.message` 派生 + `FixLevelPopover` 的 `error` prop（渲染在按钮行之上，Popover 不自动关闭），并补拒绝文案用例；
⑤**「停止轮询」这半边——已补覆盖**：`refetchInterval` 里的 `!current.stale` 是最关键也最难人工验证的一半（其余三半——不转圈/隐藏入口/显示文案——都有人眼可验的表象），回归即恢复「僵尸轮次每 3s 空转 + 永远转圈」。已把判定抽成导出的纯函数 `shouldPollFileReview(current)`（`FILE_REVIEW_POLL_MS` 间隔常量同时收口到一处），配 6 个用例；
⑥**本地 jest 工具链不入库**：`.scratch/jest*.cjs|ts` 与 transformer 是本地验证脚手架（`.scratch/` 已 gitignore），**未**纳入仓库；仓库 `web/jest.config.ts` 对 `umi/test` 的依赖仍未修（属独立议题：需决定是修复配置还是迁移到 Vitest）。他人 checkout 后仍跑不了 `npm run test`；
⑦全量前端套件仍有 **3 个套件失败（9 用例）**，经逐个核对**均与本功能无关、且都是「测试没跟上实现」的既有腐坏**，本批次不越界修改：`src/utils/__tests__/chat.test.ts`（首提交写入，断言 `$$x + y$$`，而 `preprocessLaTeX` 用 `$$${equation}$$` 保留捕获组内的空格，实现于 `000665ea` 后已改）、`src/pages/c-chat/__tests__/template-fill-confirm-card.test.tsx`（不先点开折叠头就找字段按钮，而确认卡自 2026-09-15 起默认折叠）、`src/hooks/__tests__/logic-hooks.useScrollToBottom.test.tsx`（mock container 是纯对象，缺 hook 已开始调用的 `scrollTo`）；
⑧~~未 commit、未 push~~（**已 commit `d59a5af1` 并 push 2026-09-17**——随本批次之后的部署指令一并推送，另补提交 `e84fd4de`（上一批漏 `git add` 的 `use-template-fill-run-recovery.ts`，HEAD 已 import 却从未入库，clone 后 build 必失败））。

**前端测试基建（本次打通的本地脚手架，不入库）**：仓库 `web/jest.config.ts` import 了已从 `package.json` 移除的 `umi/test`、`web/jest-setup.ts` import 了 `umi/test-setup`，属配置级损坏，jest 一行都跑不起来。本次在 `.scratch/` 下建等价配置绕开，要点如下（供后续复用）：
- `jest.local.cjs`：`rootDir` 指 web、`testEnvironment: jsdom`、`@/` → `<rootDir>/src/`、`testMatch` 同原配置；
- `jest-esbuild-transformer.cjs`：**四段流水线**。仓库没装 `@babel/preset-react`，JSX 只能交给 esbuild；但 esbuild（任何 format）与 babel 的 `@babel/plugin-transform-modules-commonjs` **都会把 import 的 require 提到文件最前**，把 `jest.mock(...)` 压到其下 ⇒ 被测模块先被真实 require 进缓存、**mock 全部失效**（表现为 9/9 用例报 `mockReturnValue is not a function`）。故必须**另起一趟只含自定义 hoist 插件的 babel pass**（同趟加会被 commonjs 的 import 提升覆盖）。顺序：esbuild(tsx→esm) → babel-jest-hoist（包 `_getJestObj()`）→ commonjs（+ `import.meta.env` 预替换）→ 自定义 hoist；
- `jest-setup.local.ts`：补 jsdom 缺失的浏览器 API——`TextEncoder/TextDecoder`（react-router 的 development 构建在模块顶层就 `new TextEncoder()`）、`Request/Response/Headers`（`route-hook.ts` 顶层 import `routes.tsx`，`routes.tsx` 又在模块作用域调 `createBrowserRouter()` → `createClientNavigation` → `new Request(...)`，即**只要 import 到 logic-hooks，整棵路由树就在求值期被建一遍**）、`Element.prototype.scrollTo/scrollIntoView`；RTL 的自动 cleanup 在该 transformer 链下不生效（上例 DOM 残留到下一例），显式 `afterEach(cleanup)`；
- 运行：`cd web && ./node_modules/.bin/jest --config ../.scratch/jest.local.cjs [路径]`；
- **注意**：上述 `Request/Response/Headers` 是**最小桩实现**（只保证构造不抛错），不是真的 fetch polyfill；将来若有本地用例真发请求，必须换真实现，否则会得到「静默假成功」。

**代码审查回合**：批改后走收口审查（含审查者自建 4 个对抗性探针实跑），修正 6 处：
①**必修**——新增的进度卡 stale 用例断言 `getByText(/已中断/)` 会同时命中状态标签「第 1 轮 · 已中断」与引导行，testing-library 抛多元素异常（审查者用 jsdom 实测复现），收紧为 `/本轮已中断/`；
②③ 两处注释（`file-review-stream.ts` / `file_review_api.py`）写「放行修复入口」，与实现恰好相反（是**隐藏**入口 + 服务端拒绝一切 fix），已改；
④ `is_stale_running` docstring 补两条被审查指出「未来会咬人」的契约：**部署契约**（本判定要求 HTTP 服务与 review 线程同进程，改多 worker 前必须先改本判定，否则所有活轮次 60s 后误判中断）+ 爆炸半径（一行脏 `create_time` 会让整张卡退化成「加载失败」而非该轮显红，为有意接受）；
⑤ 前端 `STATUS_CN` 注释夸大「与服务端 ROUND_STATUS_CN 对齐」，实为**语义对齐、措辞刻意不同**（服务端「审核完成/已收口」面向 LLM，前端「已完成/已结束」面向用户），已改；
⑥ 过时/歧义注释：工具侧「两个 `_format_status` 渲染点」→ 一个；`severity_summary` 补「多个未登记级别之间无稳定序」半句。

**效果**：服务重启/崩溃后卡在 `reviewing|fixing` 的轮次不再无限转圈、不再每 3s 空转轮询——进度卡显示红色「已中断」并引导重新发起审核，修复入口不再出现（避免必然失败的按钮）；修复被拒时给出可操作原因（级别中文名 + 待修复问题全貌），失败轮显示 `error` 原文。以上文案在对话工具文本与进度卡（C 端对话、流程 AI 面板共用同一组件）两处看到的是同一份（画布节点不渲染轮次状态，不在此列）。

## 2026-09-17 文件审核（File Review）全链路（已部署 2026-09-17）

**主题**：C 端对话工具 / 流程页 / 画布节点三入口共用的「投标文件格式审核」能力——上传成稿 → LLM 按模板逐条比对出问题清单 → 用户在审核面板逐条处置 → 可发起最多 3 轮「按级别修复」→ 修复成稿可下载。设计定稿 `docs/superpowers/specs/2026-09-16-file-review-node-design.md`，实施计划 `docs/superpowers/plans/2026-09-16-file-review-node.md`（T1–T18 全部完成）。

**第一性原理**：投标文件成稿的格式合规是**逐条可枚举的检查项**（模板 → 规则清单 → 逐条判定），不是「让 LLM 自由点评」。故数据模型以「模板 → 轮次 → 标注」三层落库，标注是唯一事实，进度是派生量——这让「人工闭环单条标注」「只修某些级别」成为自然操作，而不需要额外的任务表或状态机引擎。

**核心变更**（后端 5 文件 + 前端 8 文件 + 测试 5 套件）：
- **DB 3 表**：`file_review_template` / `file_review_round` / `file_review_annotation`（`api/db/db_models.py` 末尾追加 + `migrate_db`），5 套预置审核模板 seed。**无 task 表**——`task_id` 是轮次行上的外键，一把审核会话 = 同一个 `task_id` 下的多轮
- **Service 层**（`api/db/services/file_review_service.py`）：CRUD + 多轮状态机（`reviewing → annotated → fixing → done` / 异常 `failed`）+ `MAX_FIX_ROUNDS = 3` + `fix_rounds_left(rounds) = MAX - count(round_no > 1)`（**按轮次行数派生，不落库**，避免计数列与真实轮次漂移）+ `RUNNING_ROUND_STATUSES` / `ROUND_STATUS_CN` 单点定义
- **执行链**（`rag/svr/file_review/`）：`kb_aggregator`（全 KB 并发 retrieve + token 预算截断）→ `executor`（审查轮 / 修复轮两条 LLM 路径，`execute_task(task_id)` 同步推进）→ `patcher`（docx 格式保真区间替换，唯一匹配才改）→ 成稿落 `{tenant_id}-downloads` 桶，对象名 `frv-{task_id}-{file_version}`；`spawn` 负责 daemon 线程 + 防重入
- **5 个 REST 端点**（`api/apps/restful_apis/file_review_api.py`，blueprint 由 `api/apps/__init__.py` 自动 glob 发现，无需注册）：`GET /file/review/templates`、`GET /file/review/file/<file_id>/state`、`POST /file/review/<task_id>/fix`、`POST /file/review/annotation/<aid>/status`、`GET /file/review/<task_id>/<file_version>/download`。**轮询读模型，不做进度 SSE**——executor 每轮只写一行终态、没有步进粒度；读不限、写严格（state 不按 tenant 过滤对齐「文件所有人可见」，fix / annotation-status 必须 `get_owned_task` 严格校验，因为下游全链路只按 task_id 圈定、不含 tenant 谓词）
- **画布节点 + 对话工具**：`agent/component/file_review.py`（FileReview 节点）+ `agent/tools/file_review.py`（FileReviewTool，对话内自动发现，**不加 DSL 意图分析**）
- **前端**：`hooks/file-review-stream.ts`（流类型 + 归约）、`hooks/use-file-review-request.ts`（4 个 hook，轮询开关由 hook 内部控）、进度卡 `pages/c-chat/file-review-progress.tsx`（c-chat 与 flow AI 面板共用）、`services/file-review-service.ts`（成稿下载）

**收口审查修复（4 个 Major，均已闭环）**：
- **M1 下载成稿必然 401**：`download` 端点带 `@login_required`，而 `_load_user` **只从 `request.headers["Authorization"]` 取用户、不从 cookie 兜底**；两处触发点用 `window.open` 直链（浏览器导航请求不带自定义头）⇒ 必 401。改为 `services/file-review-service.ts` 的 `downloadFileReviewVersion`（fetch 手挂 `getAuthorization()` 取 Blob → createObjectURL 触发下载，与既有 `flow-service.downloadVersionBlob` 同款）。顺带把 Content-Disposition 里的内部 file_id 摘掉，改为 `文件审核_{version}.docx`
- **M2 前端 canFix 闸门与服务端不一致**：服务端放行条件只有三条（`rounds[-1].status ∉ (reviewing, fixing)`、spawn 未在跑、`fix_rounds_left > 0`）；前端曾写成 `status === 'annotated'`，但**修复轮的终态是 `done`** ⇒ 第 2 轮起按钮永久消失，「最多 3 轮修复」在 UI 只能触发 1 轮（只有对话工具能继续）。改为 `!isRunning && fix_rounds_left > 0` 镜像服务端闸门 + 补回归用例
- **M3 三入口 fix 受理无并发保护**：画布线程与 quart 事件循环是两个线程，「校验 — 定轮号 — 建轮次 — 起线程」整段可被并发穿插出僵尸 `fixing` 轮（此后该 task 永久被闸门挡死、只能手工改库）。下沉 `admit_fix_round`（`_ADMIT_LOCK` 进程级锁 + 覆盖整段 + `finally` 释放 + `acquire(timeout=5s)` 超时返回可重试拒绝而非静默卡死），REST 与对话工具两个调用方共用；`threading.Lock` 在 `WS=1` 单进程部署下即全局锁
- **M4 画布 FileReview 节点缺前端注册**：补 `Operator.FileReview` 枚举 + `RestrictedUpstreamMap` / `NodeMap` / `initialFormValuesMap` / `form-config-map` / 图标 / 工具面板项 + 新增 `pages/agent/form/file-review-form/`（5 字段与后端 `FileReviewParam` 逐字一致）

**测试**：`test/test_file_review_service.py` + `_api.py` + `_tool.py` + `_e2e.py` 共 **114 passed**（e2e 5 用例覆盖 5 端点全链路 + mock LLM + 修复轮终态 done + `fix_rounds_left` 归位）；并发串行化用例用 `threading.Barrier(2)` 断言并发两次 fix 只建 1 轮、败者 reason=`running`，锁超时用例断言 `_ADMIT_LOCK.locked()` 仍为 True（未误放）

**遗留**：
①~~未部署~~（**已部署 2026-09-17**：后端按 T17 清单成套 SCP **10 文件**（含 `api/db/db_models.py` 三表 + seed，且 `rag/svr/file_review/` 目录此前在服务器上不存在）+ 容器重启 → 前端 `npm run build`（1m50s）+ dist 上传 + nginx reload；部署实测与两处计划偏差见下方「部署实测」）；
②**R-1（Major，既有缺陷）**：进程重启时若某轮停在 `reviewing|fixing`，`spawn._running_tasks` 随进程清零但**轮次行状态不回落**，此后该 task 的所有 fix 被闸门 2 永久拒绝（`_force_fail_round` 只覆盖崩溃/调度失败分支，无兜底扫描）→ 需手工改库。M3 未使其恶化，但把「不可自愈」从注释提升为显式契约，建议后续加启动期 stale 轮次扫描；
③**R-2（Minor）**：`admit_fix_round` docstring 声明「闸门 3 必须在 5 之后」，而实现是先判 `is_running` 再判 `no_pending`（抽取时调换，旧 T8 实现是反的）——两条同时成立时用户会先拿到「上一轮正在收尾」文案、重试一次才拿到真正原因「没有待修复问题」。两者都在 `create_round` 之前，不产生僵尸轮次，属文案时机差，需二选一对齐；
④**R-3（Minor）**：对话工具侧 `_fix` 被闸门拒绝时丢了旧实现的富文案（待修复问题全量分布 + 建议 `action=status`），只剩一句通用拒绝。功能等价但 LLM 上下文变少，恢复需让 `admit_fix_round` 一并回传 pending 分布；
⑤**R-6（Minor）**：REST 路径的 `_ADMIT_LOCK.acquire(timeout=5s)` 是**同步阻塞**，单事件循环下单次请求最多阻塞全服 5s（正常路径毫秒级、需异常持锁者才放大），后续可评估改 `asyncio.to_thread` 包装；
⑥**R-7（Minor）**：`admit_fix_round` 对 `levels=None` 会 `TypeError`（REST 与工具两个调用方均已在入口校验，不可达），属防御性缺口；
⑦**R-8（Minor）**：state 端点的 `doc.object` 把 MinIO 对象名下发给前端（低敏，仅用于判断「是否显示下载按钮」）；
⑧前端 jest 无法执行是**仓库既存问题**（`web/jest.config.ts` 依赖已从 `package.json` 移除的 `umi/test`，项目已迁 Vite），本次新增的进度卡回归用例只经代码审查核对断言自洽、未实际跑过——修 jest 配置属独立议题。**（后续更新：该结论已部分作废，见上方 2026-09-17「前端测试基建」——用 `.scratch/` 本地脚手架可跑通，两套件 16 用例实跑通过；仓库 `web/jest.config.ts` 仍未修。）**

**效果**：投标文件成稿可一键发起格式审核（按模板出问题清单）→ 面板内逐条处置或按级别发起修复（最多 3 轮，每轮产新成稿版本）→ 成稿带鉴权下载；审核结果在 C 端对话、流程页、画布节点三处看到的是同一份数据与同一个交互。

### 部署实测（2026-09-17）

后端 10 文件成套 SCP → 容器重启 → 前端 `npm run build` + dist 上传 + nginx reload。实测记录与两处**计划偏差**：

- **偏差一：成套范围是 10 文件而非本批次改动的 3 个**。服务器此前对 file_review **零部署**（`rag/svr/file_review/` 目录在服务器上根本不存在），故按 T17 清单成套 SCP，而非只传收口批次的 `file_review_service.py` + `file_review_api.py` + `file_review.py`。
- **偏差二：`api/db/db_models.py` 在清单内，动了上游核心文件**。项目约束 #2 禁止改上游核心文件，但 T17 清单本就包含它（3 张新表 + seed 必须经 `migrate_db` 建表）。覆盖**前**先 `diff --strip-trailing-cr` 对比服务器与本地：差异为**纯新增**（`@@ -2854,8 +2854,28 @@` 与 `@@ -3156,3 +3176,235 @@`，**+253 / -0**，无任何既有行被删改），据此判定覆盖不破坏服务器上其他功能的模型定义。
- **md5 校验**：10 个文件逐个 `md5sum` 与本地一致后才重启。
- **冒烟（T17 Step 2 脚本需修正）**：计划里写的 `aggregate_chunks` **在实现中不存在**，实际符号是 `aggregate_references(*, kb_chunks, budget)`（keyword-only）；同批导出还有 `apply_patches` / `apply_patches_to_docx` / `spawn_review_task` / `is_running` / `execute_task`。修正后冒烟通过：`imports OK` + `preset templates: 5` + `routes OK`（5 条 file_review 路由全部注册）。
- **5 端点鉴权冒烟全部 401**：`GET /file/review/templates`、三个 POST、`GET …/download` 在无 `Authorization` 头时全部返回 `HTTP/1.1 401` + `{"code":401,…}`，鉴权正常、无安全缺口。
- **踩坑一（会误判成漏洞）**：用 `curl -s -o /dev/null -w "%{http_code}"` 打 POST 端点会得到**假 200**，必须用 `curl -i` 看真实状态行才可信——`-w` 的写法在部分分支下把连接层结果当成了 HTTP 码。
- **踩坑二（会误判成服务没起）**：`docker restart` 后约 **40s** 内 5 个端点全返回连接失败（`000`），此时 `curl /v1/system/version` 返回 404 说明 HTTP 已通、只是应用未 ready；日志出现 `Running on http://0.0.0.0:9380` 后再打即得真实状态码。冒烟需等待/重试，不能一次失败就下结论。
- **前端**：`npm run build` 用时 **1m50s**，`dist.tar.gz` 约 **39M**；上传后按 Inode 陷阱要求 `rm -rf dist/*`（删内容保留 inode）再解包，`nginx -s reload` 成功，`dist/index.html` mtime 为 Sep 17 13:56，容器内 `curl localhost:80/` 与 `/index.html` 均 **200**。
- **遗留：T17 Step 7（人肉浏览器验收 9 条）未执行**，留待用户——C 端对话发起审核 → 面板处置 → 按级别修复 → 下载成稿；流程页 FileReview 节点；刷新恢复；与 TemplateFill 并行独立性。计划里带 ⛔ 块的 checkbox 同理未勾。

## 2026-09-16 范本填写增量模式（同范本有 done → 走增量而非从头填充，未部署）

**主题**：用户反馈点击「确认并继续填写」后又「从头开始了」——画布 TemplateFill 节点每次触发都是无状态重启，从空基线重跑全流程；同范本已有 done 成稿时，应当走「增量修改」而非「全部重填」。设计定稿 `docs/superpowers/specs/2026-09-16-template-fill-incremental-design.md`。

**第一性原理**：范本填写完成后产出成稿。后续操作（如「批文名称及编号 填写成 李港」）的第一性意图是「修改这一处」而不是「全部重填」。

**核心变更**（2 后端文件 + 2 测试文件）：
- 后端基线检测：`agent/component/template_fill.py::_invoke_async` 新增 baseline 检测循环——按范本调 `TplFillTaskService.latest_done`（service 已存在，**此前仅被对话内 modify 工具调用**，画布节点从未用过），校验版本一致后取 `task.values["render"]` 作基线
- 后端增量意图识别：新增 `executor.extract_patch_values` 函数（1 次 LLM 调用）——从用户 query + 上次填写值抽 `{intent, direct, changed}`，intent ∈ `{patch, refill, fill_unfilled, noop}`；中文名/key 必须能在占位符清单里匹配上，否则忽略（防编造 key）
- 后端增量分支：节点按 intent 路由——`patch/fill_unfilled` 走 incremental_overrides（确认卡只列本次要改的项，不是全量 244 项）；`refill` 弹 baseline 走全量（与首次相同）；`noop` 保留 baseline + 走轻量复用（task 仍 spawn 但 LLM 槽空，baseline 兜回所有 missing，渲染产物与上次同值）
- 后端合并优先级：`executor.split_canvas_params` 新增 `_baseline_values` 解析 → `_run_task` 在 `_merge_default_values` 之前按 missing 兜回 baseline → 用户在 baseline 上填的、本轮没被 LLM/直填覆盖的字段必须原样保留，不能回退到 default_value 提示（空串视为无值不覆盖）
- 后端确认卡增量契约：`_confirm_changed_fields` 新增 `incremental_overrides` kwarg → overrides 范本跳过 default_map 构造（候选只列 patch items，predicted 全勾选，兜底用 LLM 抽出的 fallback）；valid 集合用 overrides candidates key，前端只能勾选 patch items
- 后端 summary 文案：增量 patch 走「本次增量更新 N 个字段（其余沿用上次填写值，共 M 个填写点）」；noop 走「本次未涉及，沿用上次填写值（共 M 个填写点）」；全量文案不变
- 防御加固：refill 模式防御性清空 direct/changed（防 LLM 误输出被误用）；baseline 与当前版本不一致（范本升级）→ 跳过 baseline 走全量（不能跨版本混用）

**测试**（8 套件 466 passed 全绿）：
- `test/test_template_fill_executor.py` +16（split_canvas_params 4：normal/None-drop/missing-bad-type/key-coerce + extract_patch_values 8：patch/noop/refill/无效intent/unknown-key/exception/空placeholders/兜底 + baseline 合并 4：missing-merge/direct-priority/空串跳过/无交集）
- `test/test_agent_fill_template_component.py` +6（baseline 检测/version mismatch/noop/refill/no baseline/summary 分支）+ `_Row` 扩展 `values`+`template_version_id`、`FakeTaskService` 加 `latest_done`（路由 `_active_instance` 桩）
- `test/test_template_fill_events.py` `_TaskServiceStub` 加 `latest_done → None`（兼容旧测试期望无 baseline）
- `test_split_hostile_payloads` 修：opts 期望加 `baseline_values: {}`
- `test_decision_conditional_execution_skips_unchanged_defaults` + `test_decision_empty_direct_value_renders_blank` 修：fake_confirm 加 `*, incremental_overrides=None` kwarg 兼容新签名

**遗留**：
①后端 **未部署**——`rag/svr/template_fill/executor.py` + `agent/component/template_fill.py` 成套 SCP + 容器重启；部署前请逐项验证：对话内 modify 工具（已用 `latest_done`）与画布节点（新加）共享同一 service 方法、画布节点下游 1 次 LLM 增量抽取；
②前端确认卡 UI 未适配增量视觉（`incremental=True` 已下发但前端未读字段区分「全量卡 vs 增量卡」）——当前前端按全量卡渲染对增量场景可工作（只列 N 项）但缺 header 区分 + 默认折叠，待后续；
③noop 仍 spawn task（轻量复用路径 task 仍 spawn + 渲染副本），可优化为「直接复用 baseline 的 render 桶文件」省一次任务行，但需重构 `_bridge_download` 接受 baseline row；后续；
④跨版本 baseline 完全丢弃（不走「老字段保留 + 新字段走全量」混合策略，结构变化语义就变）；后续。

**效果**：同范本已有 done 成稿时，对话「把某字段改成 X」→ 确认卡只列该项，task 只检索该项，其余沿用上次；不再出现「又从头开始」的体验。

## 2026-09-16 范本填写快照化恢复 + 预览内存治理（加固增强，后端已部署 / 前端待部署）

**主题**：用户反馈流程页范本填写两大不稳定症状——①打开实时预览/查看成稿时浏览器标签页崩溃（OOM）；②填写完成后刷新页面，字段确认卡（confirm_pending）重现且可交互，600s nonce 窗口内再提交会触发重复填写。设计定稿 `docs/superpowers/specs/2026-09-16-template-fill-snapshot-hardening-design.md`。

**根因**：①docx-preview 把整本文档一次性建全量 DOM（200+ 页），流程页 keptIds 常驻挂载让隐藏的 FlowDetail 各自驻留预览 DOM 树，切换多轮叠加 → OOM；②挂起确认/选择等待态只活在画布节点内存，前端刷新恢复靠「事件序列最后一条是否 confirm_pending」猜测尾巴——提交后 2s 防抖未落库即刷新、SSE 断连后轮询事件不落库，两条路径都会重放出可交互僵尸卡。

**核心变更**：
- 后端运行快照键：`agent/component/template_fill.py` 新增 `_write_run_snapshot`（键 `tpl_fill:run:{canvas_task_id}`，TTL 过程 7200s/终态 600s，Redis 故障只告警），在 selected/select_pending/confirm_pending/filling/done 五个阶段切换点写入权威状态（stage+templates+tasks 两 id 空间映射+pending 挂起载荷）；多范本选择被消费（用户提交或超时按 AI 初选）后**立即重写为 selected 态并清 pending**（`selected_templates` 同步换为确认后子集）——否则紧随其后的 LLM 预判/确认等待期刷新会读到残留 `select_pending`，重放一张已失效的选择卡（僵尸卡）
- 后端恢复端点：`api/apps/restful_apis/template_api.py` 新增 `GET /template/fill/fill-run/<canvas_task_id>/snapshot`（login_required）——`build_run_snapshot_payload` 纯函数组装：任一 fill task 越权整体按不存在（防探测）、逐模板 DB 行权威+Redis 进度快照补实时数字、终态 done 行复用 `_bridge_download` 桥接+按版本 placeholders 派生 unfilled（与 progress 端点同口径）
- 前端恢复链路：`template-fill-stream.ts` 新增 `findCanvasRunId`（从挂起/心跳事件提取 canvas run id）+ `buildStateFromRunSnapshot`（快照→ITemplateFillState 整体映射）；新 hook `use-template-fill-run-recovery.ts`（拉快照整体覆盖重放态，未完结 3s 轮询至终态，键过期把挂起卡标 expired 不再显示僵尸卡）；`flow-ai-panel.tsx` 接线（parseAndReplay 保留作旧数据兜底与 id 发现，快照可用则权威覆盖）；`c-chat index.tsx` 对最新一条带 templateFill 的历史消息跑同一恢复并回写
- 预览内存治理：`template-fill-progress.tsx` 新增 `forceClosedLivePreview`（true 时清 liveTarget）；`flow-panel.tsx`→`flow-detail.tsx`→`ConversationView` 透传 `visible={id===activeId}`，隐藏详情强制收预览（任意时刻至多一棵大文档 DOM 树）；`template-fill-live-preview.tsx` 超大文档防线（blob>2.5MB 默认纯文本渲染+提示，可显式「切换保真渲染」，渲染失败降级链路不变）

**测试**：新增 `test/test_template_fill_run_snapshot.py` 23 用例（写入侧 4：无 task_id 跳过/键载荷契约/done 短 TTL/Redis 故障吞掉；payload 对抗 15：越权整体隐藏/脏 task 值免查行/脏模板行/selected 挂起期/桥接失败/placeholder 脏数据/空 unfilled 归一/快照值权威/DB 兜底/行值非 dict/failed 取错兜底/partial 与未知态映射/无模板与缺 stage；读侧 4：键缺失/bytes JSON/坏 JSON/非 dict JSON）；模板填写 8 套件 428 passed；前端 tsc 改动文件零新增错误 + 生产构建通过

**遗留**：①后端 **已部署 2026-09-16**（`agent/component/template_fill.py` + `api/apps/restful_apis/template_api.py` 成套 SCP + 容器重启；冒烟验证：容器内 `_write_run_snapshot`/`build_run_snapshot_payload`/`_read_run_snapshot` 全在位、两侧快照键常量一致，宿主机 `GET /api/v1/template/fill/fill-run/<id>/snapshot` 返回 401 证路由已注册且鉴权生效）；②**前端未部署**——build 产物上传 + nginx reload；③前后端可独立部署（旧前端+新后端=快照端点无人调用无影响，新前端+旧后端=快照 404 走 catch 退化重放兜底）；④旧的「事件序列尾巴猜测」重放启发式保留作无 run id 记录兜底，待历史数据自然过期后可评估移除

**效果**：填写中/挂起等待/已完成后刷新页面均从运行快照权威恢复——确认卡不再复活、进度续播、成稿卡完整；大文档预览默认文本兜底，多流程切换不再叠加 DOM 致标签页崩溃。

## 2026-09-16 FillTemplate 新增 modify action：已完成成稿就地改字段（后端 2 文件，已部署 2026-09-16）

**主题**：用户在 C 端流程页填写完成后说「approval_authority 这个填写成 李港」，系统却发起全新填写流程（又出 244 项字段确认卡），观感是之前的填写全部丢失。用户明确要的是**就地修改**：在原已填好的成稿上改指定字段，不重跑、不出确认卡，点开原文件即可核对二次修改处。

**核心变更**（`agent/tools/template_fill.py` + `api/db/services/template_fill_service.py` + 测试）：
- FillTemplate 新增 action=`modify`：定位该范本最近一次 **done** 任务 → 校验 patch key 合法性（不在填写点中直接拒绝并提示，不编造）→ patch 合并进 `task.values`（render 存值 / cells 状态：非空→filled、空→not_found）→ 读模板工作副本 `renderer.render` 全量重渲染 → **覆盖写原成稿对象名**（`v{ver}_result_{task.id}.{ext}` 同名，前端卡片下载/预览链接自动指向新文件）→ `patch_values` 回写 values → patch 值 `sediment_defaults`（override_keys=patch，用户显式决策可覆盖 manual 默认）
- 渲染异常时原成稿不动、错误透传；storage put 成功但 DB 回写失败时提示用户用 status 核对；sediment 失败仅告警不影响交付
- 按名称解析 `_resolve_modify_template_id`：精确同名优先、全状态可改（改历史成稿与当前发布态无关）、多命中列候选
- 工具描述加使用时机：「用户在填写完成后要求修改/补充/更正成稿里的某几个字段时用 modify，绝不要重新 fill 或重新出确认卡」
- service 层新增 `TplFillTaskService.latest_done`（template+tenant 最新 done 行）与 `patch_values`（终态行 values 就地更新）
- 测试：新增 9 用例（缺参/无 done 任务/未知 key 不触文档/成功合并+覆盖写+沉淀/空值标 not_found/渲染失败原稿不动/按名称解析/多命中不触文档）；tool 套件 47 passed，模板填写相关 5 套件 369 passed

**遗留**：①~~未部署~~（**已部署 2026-09-16**：2 文件成套 SCP + 容器重启，容器内冒烟 enum/_modify/_resolve_modify_template_id/latest_done/patch_values 全部在位）；②画布意图路由：Categorize 需把「修改成稿某字段」类意图路由到带 FillTemplate 工具的 Agent 节点（范本查询咨询），提示词文案随画布配置一并给用户手工贴入；③modify 渲染用合并后全量 values，若原成稿由旧版有 bug 链路生成（如超链接腐坏），就地重渲染可顺带修复同款问题

**效果**：填写完成后说「xx 字段改成 yy」即就地改到原成稿，确认卡不再重出，历史填写全部保留。

## 2026-09-16 跳转定位漂移根修：懒渲染估算高度 + scrollIntoView 平滑滚动（纯前端 3 文件，已部署 2026-09-16）

**主题**：用户实测两端跳转定位不对——C 端未填充汇总点击（162 个填写点场景）跳到错误位置；B 端范本库填写点列表点击「前面几个没问题，越往后越偏」。共同根因：`applyDocxPageLazy` 给每个分页 section 设 `content-visibility: auto`（屏外页按估算高度 794×1123 占位），大文档真实页高与估算差异大；定位用 `scrollIntoView({behavior:'smooth'})`，平滑滚动过程中上方懒渲染页逐个真实落地、总高度持续漂移 → 停在错误位置（文档越深、上方未渲染页越多，偏差越大，「越往后越偏」即此症状）。

**核心变更**（`web/src/pages/c-chat/docx-highlight.ts` + C 端 `template-fill-live-preview.tsx` + B 端 `fidelity-preview.tsx`）：
- docx-highlight.ts 新增共享 `instantFocusScroll(el)`：①目标所在分页若为懒渲染先强制 `content-visibility: visible`（该页顶部位置不受自身高度影响，随后测位准确）；②向上遍历所有可滚动祖先，按 getBoundingClientRect 瞬时设 scrollTop 居中——布局已稳定，位置精确
- B 端 `focusAnchor`（mark[data-anchor-key]）与 C 端 `focusPlaceholder`（data-ph-key）都改走共享函数；C 端删本地重复实现；两端各自脉冲闪烁保留（B 端红色脉冲 class / C 端琥珀 WAAPI）
- 顺带修掉 C 端上一版遗留的「2s 闪烁被平滑滚动吃掉」问题（瞬时定位无滚动耗时）

**遗留**：①C 端机电施工范本 3 个 no-op 占位符（tender_agent_1137/1226/1325）文档中无对应 token，点击无反应属已知边界；②同 key 多处出现只定位第一处；③定位后该页保持真实渲染（不再懒渲染），单页开销可忽略

**效果**：两端点击跳转在任意文档深度都精确落在目标段落并脉冲闪烁，不再随文档深度累积偏移。

## 2026-09-16 FillTemplate fill action 支持按名称模糊解析（后端 1 文件，已部署 2026-09-16）

**主题**：C 端流程对话实测「非要用户给全量范本名称才发起填写」——半截名称「福建省高速公路工程电子招标示范文本」时 Agent 查到 2 个候选（1 发布 1 停用）反问 id；说「完善第一个」指代失效又反问；直到贴全量名称才走通。排查确认名称模糊匹配本身没问题（detail/list 均支持），卡点是 `fill` action **强制要求 template_id**（工具描述写死），逼 Agent 必须先拿精确 id。

**核心变更**（`agent/tools/template_fill.py` + 测试）：
- `_fill` 缺 template_id 时接受 `template_name`：新增 `_resolve_fill_template_id`——精确同名优先 → 过滤已发布 → **唯一命中直接发起**（不反问）；多命中列候选让用户挑；零命中/命中均未发布分别说明原因。事故同构场景（部分名称命中 1 发布 + 1 停用）自动选发布者，正是用户卡住的场景
- 工具描述同步更新：fill 支持 template_name、使用时机加「用户只说部分名称或『第一个/上面那个』指代时直接解析调用 fill，不要反问要完整名称或 id」
- 测试：新增 6 用例（事故同构唯一发布命中直接发起/零命中/全部未发布拒绝/多发布列候选不发起/精确同名优先/缺省提示二者之一）；38 passed + 组件套件 41 passed，ruff 通过

**遗留**：① ~~未部署~~（**已部署 2026-09-16**：`agent/tools/template_fill.py` SCP + 容器重启，容器内冒烟 `_resolve_fill_template_id` 在位 + 新描述生效）；② 画布 Agent 节点 sys_prompt 未引导「完善」→ 填写流程与「第一个」指代解析，属 B 端画布 UI 配置，已给用户提示词文案待手工贴入；③ 文件经 ruff format 全量重排（line-length=200），diff 较大但语义仅上述两处

**效果**：用户说半截范本名称即可直接发起填写（唯一已发布命中自动选中）；配合画布提示词修改后「完善第一个」类指代也能走通。

## 2026-09-16 docx 替换链路根修：超链接段落统一区间重写 + occ 嵌套剔除（后端 1 文件，已部署 2026-09-16）

**主题**：C 端流程页《福建省高速公路工程电子招标示范文本（2022版）-机电施工招标范本（2021.12修订）》填写失败「渲染落稿失败: expected token 'end of print statement', got ':'」，其余范本正常。系统化调试（10 个只读诊断脚本）实锤双重根因，均在 `rag/svr/template_fill/docx_utils.py` 替换链路。

**根因**：
- **A（渲染失败根因）**：旧 `_replace_via_run_concat` 对含超链接/域段落把 runs 拼接替换后**按原 run 长度静态切片回写**——替换值长于锚时 run 文本整体后移，而 w:hyperlink 元素钉在 XML 固定位置，超链接 URL 文本被插进先前写入的 `{{key}}` 切片缝隙中，产出 `{{bhttp://fjggzyjy.cn/...}}` 脏 token，docxtpl Jinja 解析报错。
- **B（occ 错位，同链条）**：识别端按独立留白位给同段同形锚编号 occ=1..n，渲染端枚举会把嵌套在更长锚（7 空格年月空位内的 3 空格）内部的短锚出现也计入序号 → 时/分/秒落进年/月空位。

**核心变更**（仅 `docx_utils.py` + 测试）：
- 删除 `_nth_index`/`_has_link_or_field`/`_replace_via_run_concat`，新增 `_occurrence_intervals`/`_rewrite_run_span`；含超链接（w:hyperlink）/域（w:fldSimple）段落统一走 `_replace_cross_run_in_place` 区间重写——超链接/域内 run 不在 p.runs 中天然不被触碰，token 原子落在单 run 永不切碎
- `_replace_cross_run_in_place` occ 路径改非重叠枚举 + 剔除嵌套在 `longer_anchors`（同段更长已注册锚）出现区间内的出现；`apply_docx_placeholders` 按同 addr 收集更长锚集合下传；occ 超出幸存数返回 False no-op
- **存量红线保留**：occ=None 单 run 快路径与 replace-all 语义逐字节不变；无超链接普通段落路径不变
- 测试：新增 5 个对抗性用例（事故结构全等断言+token 纯净正则 / occ 嵌套剔除 / 超幸存数 no-op / fldSimple 域完整性 / fldSimple occ 事故同构——document 序文本流断言，旧实现产出 `{{page9s}}` 腐坏形态被精确击穿）；192+266=458 passed；端到端 docxtpl 真渲染事故结构成功且 URL 完好；code-review 审查通过（2 条建议级设计边界备忘：longer_anchors 依赖「更长锚均被注册」约定、检测/渲染文本坐标系不对称）

**遗留**：① ~~未部署~~（**已部署 2026-09-16**：`docx_utils.py` SCP + `docker restart docker-ragflow-cpu-1`，容器内 import 冒烟新函数在位通过）；② ~~旧 v1 工作副本带脏 token~~（**已原地重建 2026-09-16**：容器内从 original+现有 244 占位符用修复链路重生成 `v1_render.docx` 并覆写 MinIO，验证 255 token 0 脏 + docxtpl 真渲染通过，旧副本备份为 `v1_render.docx.corrupt.bak`；复现实锤存量副本恰含 1 个脏 token `{{bhttp://fjggzyjy.cn/id_deadline_minute}}` 与报错吻合；无需 B 端重新 AI 识别）；③ 服务器 `/home/bid-agent-konus/ragflow2/rag/svr/` 残留 10 个 `_diag_tpl_*.py` + `_diag_repro_tpl_render.py` + `_fix_rebuild_v1_render.py` 诊断/修复脚本待清理；④ 重建时 3 个占位符 replace no-op（tender_agent_1137/1226/1325，纯空白 anchor 与原文不匹配）——该 3 处留白将保持未替换，属识别端锚文本与原文漂移，待观察

**效果**：超链接/域段落替换不再腐坏 URL 与占位符 token；同段同形留白（含嵌套）occ 落位与识别端编号对齐；该范本重新识别后可正常填写。

## 2026-09-16 B端范本画布查询能力：FillTemplate 新增 detail action（后端 1 文件已部署 2026-09-16，画布配置待 UI 操作）

**主题**：用户指出 B 端画布「测试范本」（id=4f9b758cab5b…）Categorize 只有「范本书写/范本文件修改」两类太绝对——用户问「有哪些范本」「某范本有没有这条内容」会被迫落入「范本文件修改」（Categorize 未命中时兜底落**列表最后一类**，categorize.py:151-155），而该分支 Agent 是裸 LLM（tools=[] 无 dataset）只能瞎答。方案 A：扩展工具查询能力 + 用户在画布 UI 加「范本查询咨询」类（放列表最后，模糊问题兜底落到它）。

**核心变更**（`agent/tools/template_fill.py` + `test/test_template_fill_tool.py`）：
- FillTemplate 工具新增 `action=detail`：按 `template_id` 精确查（get_owned 租户隔离）或 `template_name` 模糊查（get_list_page，名称精确相等优先，多命中列候选追问）；可选 `keyword` 在填写点（中文名/key/锚文本，大小写不敏感子串）匹配，直接回答「某范本有没有某条内容」
- detail 覆盖 draft/published/disabled 全状态并中文标注（草稿/已发布/已停用）；填写点截断 30 条/锚文本 60 字符防大范本刷爆上下文；无 keyword 时引导二次查询
- 容错加固（code-review 建议）：placeholders 为合法 JSON 标量/object 等脏数据统一降级空清单；template_name/keyword 回显截断 100 字符防超长刷屏
- 测试：新增 16 个对抗性用例（空参/无租户/越权/零命中/多命中/精确优先/keyword 命中未命中/大小写/标量与 object 脏数据/超长 anchor 截断/非 dict 行），32 passed + 组件套件 41 passed，ruff 通过

**遗留**：① 画布配置（Categorize 加「范本查询咨询」类 + Agent 节点挂 FillTemplate 工具）需在 B 端画布 UI 手工操作；② 既有「范本文件修改」Agent 仍是裸 LLM（tools 空、sys_prompt 一句话），本次未动

**部署验证（2026-09-16）**：SCP `agent/tools/template_fill.py` + 重启容器；import 冒烟 enum 含 detail；功能冒烟按 detail 同链路查真实 DB——5 个范本，填写点 244/290/1095 条读取正常（1095 条大范本验证了 30 条截断上限的必要性）。

**效果**：画布挂上查询类+FillTemplate 工具后，「有哪些范本」「XX 范本有没有 X 填写项」等查询可由工具真实回答，不再落入无工具分支瞎编。

## 2026-09-15 流程切换保持进行中对话状态（已部署 2026-09-15，纯前端 2 文件，commit 0702d7b5）

**主题**：用户需求「流程 1 对话 LLM 执行等待中，切到流程 2 看其他内容，切回流程 1 状态不能丢」。原实现 `flow-panel.tsx` 按 `activeId` 条件渲染单个 `FlowDetail`——切换流程即卸载，SSE 流中断、挂起确认卡/填写进度/流式回复全部丢失。

**核心变更**（`flow-panel.tsx` + `flow-detail.tsx`，常驻挂载隐藏显示模式）：
- `flow-detail.tsx`：新增 `onBusyChange(busy)` 上报（`liveChat.busy` 派生，effect 防抖无谓触发）
- `flow-panel.tsx`：`keptIds`（常驻挂载集 = 当前选中 ∪ 对话进行中）+ `busyIds` 驱动——右栏 map 渲染全部常驻 `FlowDetail`，非选中的包 `hidden` wrapper（display:none 子树不渲染，fixed 抽屉也不会串显）；对话结束且不在选中视图后自动摘除（终态已落库，重开由回放恢复）
- 多实例共享状态隔离：批注 portal 挂载点按流程 id 各一个 slot（非选中隐藏）；批注数/范本预览腾位/审核腾位上报经 `activeIdRef` 门禁——仅激活实例上报，隐藏实例不串改共享镜像；切回时回调身份变化触发实例 effect 重报自身状态，共享镜像自然纠正
- 列表点击/新建流程改走 `handleSelect`（进 keptIds）；`onDeleted` 移出 kept/busy 集并清选中态

**效果**：流程 1 对话进行中切到流程 2 → SSE/确认卡/进度在后台持续，切回现场完整恢复（含流式内容增长）；多个流程可同时进行对话互不干扰。

**遗留**：① 超管「全部流程」视图（flow-manage.tsx）仍按条件渲染单实例，切换同样中断（管理视图低频，未纳入）；② 流程页签切到超管 admin scope 时整个非管理树卸载，常驻集随之中断（既有边界）；③ 隐藏实例的查询/轮询保持活跃（数据量可控，未做暂停优化）

## 2026-09-15 多范本选择卡不可见根修：等待期 SSE 心跳+回放条件保留挂起卡（已部署 2026-09-15，后端 1 文件 + 前端 4 文件，commit 3eb20639）

**主题**：用户实测「多范本选择时不知道在哪选择，干等很久后系统自己执行了字段确认」。排查 17:25 轮证据链：DB 记录事件停在 `selected`+`select_pending`（后端推送正确、增量同步也工作了），但用户界面只看到两条范本行没有选择卡——根因是**刷新后回放无条件剥离 `pendingSelect`**（上一条目 d2aad2ed 的防御），选择卡在回放视图中永不出现；而服务端画布在等待期继续存活，600s 超时后按 AI 初选继续 → 字段确认「自己出现」。另发现结构性风险：挂起等待最长 600s 全程无 SSE 数据流，Nginx 空闲超时（默认 60s）会掐断连接，事件流冻结、增量落库停摆。

**核心变更**（后端 1 + 前端 4）：
- `agent/component/template_fill.py`：`_confirm_template_selection` 与 `_confirm_changed_fields` 等待循环每 30s 推 `{"stage":"heartbeat","task_id"}` 保活 SSE 并维持前端增量落库（新增 `_CONFIRM_HEARTBEAT_INTERVAL=30`；前端 reducer 对无 template_id 事件天然忽略）
- `web/src/pages/c-chat/flow/flow-ai-panel.tsx`：`parseAndReplay` 改为**条件保留**挂起卡——仅当 `select_pending`/`confirm_pending` 是最后一条事件时保留交互态（服务端等待循环不随 SSE 断连停止，刷新后仍可在超时前提交，Redis 键被正常消费）；其后有任何后续事件说明挂起已被消费，剥离防僵尸卡
- `web/src/pages/c-chat/flow/flow-detail.tsx`：ConversationView 自动滚动扩展——`pendingSelect`/`pendingConfirm` 出现时也 scrollIntoView（卡在范本行上方，不滚动可能完全出视口）
- `web/src/pages/c-chat/template-fill-confirm-card.tsx`：选择卡改蓝色强调样式（`#B3CCFF` 边框 + `#F5F8FF` 底），区别于灰色信息行，传达「需要操作」
- `web/src/hooks/template-fill-stream.ts`：stage 联合类型补 `heartbeat`（文档化）
- 测试：`test_agent_fill_template_component.py` 新增 2 用例（选择/字段确认长等待各推 3 次心跳、事件带 task_id 无 template_id、pending/timeout 两端事件不受影响）；41 passed 全绿，eslint 通过

**验证**：`uv run pytest test/test_agent_fill_template_component.py -q` 41 passed

**遗留**：① ~~未部署~~（**已部署 2026-09-15**：`template_fill.py` SCP + 容器重启 + 前端 build，容器内 import 冒烟 HB=30.0 通过）；② 刷新后提交选择能被服务端消费，但因 SSE 已断且回放模板无 task_id，行卡片不会实时更新到最终选择集（彻底解决须画布运行整体后台化，同前遗留）；③ `flow-ai-panel.tsx` 存量 3 处 tsc 宽松类型错误（d2aad2ed 引入，vite build 不受影响，顺手修另列）

## 2026-09-15 填写字段确认卡默认折叠（已部署 2026-09-15，纯前端 1 文件）

**主题**：用户需要确认卡折叠态——大范本确认卡（数百填写点行）全量铺开占据对话区。改为**默认收起**：常显摘要头（展开/收起箭头 +「填写字段确认」+「N 个范本 · M 个填写点，点击展开调整」），收起态保留「确认并继续填写」一键提交（按初始勾选集直接确认，无需展开）；展开后为原完整字段列表+底部提交；提交失败自动展开并显错误（收起态错误也单显一行）。与流式事件增量同步同批待部署（同为纯前端 build）。

## 2026-09-15 流式事件增量同步+重放挂起态剥离（已部署 2026-09-15，纯前端 1 文件）

**主题**：发送即存上线后实测：发送预存成功（DB 有占位记录），但**中途刷新仍丢范本进度**——根因是 `template_fill_events` 只在完成回填时才落库，刷新时记录里 events 为空，历史重放（parseAndReplay→范本行→轮询重连）无数据可用。另排查确认：SSE 断开会取消 canvas 编排（tpl_fill_task 无记录=填写从未委派），回填由前端 done 驱动、刷新后永不到达。

**核心变更**（`web/src/pages/c-chat/flow/flow-ai-panel.tsx`）：
- 流式期间把 template_fill_progress 事件**防抖 2s 增量写入预存记录**（复用 record_id+save_as_version=false 回填更新路径，response 保持「（生成中…）」占位）；守卫 `pendingRecordIdRef.current === rid` 确保回填/失败标记消费后不再覆盖最终内容；新一轮发送时清定时器
- 重放时剥掉 `pendingConfirm`/`pendingSelect` 挂起态——原 canvas 编排已随断连取消，重放出可点击的确认卡会误导（确认写入 Redis 无人消费）；保留范本行/进度/成稿卡，填写阶段行带 task_id 由轮询自动重连真实进度

**效果边界**：① 填写阶段（已委派 tpl_fill_task）刷新 → 重放恢复范本行 → 轮询重连 → 进度/成稿卡完整回归，✅ 完整修复；② 选择/字段确认阶段刷新 → 原编排已死（服务端 canvas 随 SSE 取消，填写未开始），只能恢复展示（范本行+占位记录），无法续跑——彻底修复须把整个 canvas 运行后台化（agent_api completion 重构，遗留）

**验证**：`npm run build` 通过（1m27s）；测试基建无新用例（防抖同步为副作用逻辑，既有 reducer/轮询测试不受影响）

**遗留**：~~未部署~~（**已部署 2026-09-15**，与同日选择卡根修/流程切换常驻挂载同批 build 上线）；canvas 运行整体后台化未做；预存与回填之间容器重启留占位记录同前

## 2026-09-15 流程对话发送即存+完成回填（已部署 2026-09-15）

**主题**：用户反馈「流程里发送消息后立即刷新页面，本轮内容全部丢失」——原逻辑 AI 记录只在流式结束（done && !sending）时才插入 flow_ai_chat，中途刷新即丢。改为**发送即存**：发出消息瞬间先预落一条「（生成中…）」占位记录（拿到 record_id），流式结束后按 record_id 回填最终回复/事件序列，不再二次插记录；发送链路失败则把占位记录标记为「（本轮未完成，无回复内容）」，不留脏占位。

**核心变更**（后端 2 文件 + 前端 1 文件）：
- `api/db/services/flow_service.py`：`FlowAiChatService.update_content(record_id, response, session_id?, template_fill_events?)`——None 表示不修改（防误清空已有事件），空串表示显式覆盖
- `api/apps/restful_apis/flow_app.py`：`add_ai_record` 支持 `record_id + save_as_version=false` 回填更新路径——仅记录本人可回填（user_id 校验 403）、record 不存在/跨流程 404、response 空白 101；instruction/version 以发送时为准不覆盖；不建版本不碰存储
- `web/src/pages/c-chat/flow/flow-ai-panel.tsx`：handleSend 在 ensureSession 后预存占位记录（失败降级为不预存，走原逻辑）；新增 pendingRecordIdRef + markPendingFailed（两个失败分支调用）；自动保存 effect 双路径——有预存 id 走回填更新（带 template_fill_events/session_id），无则维持原插记录逻辑
- 测试：新增 `test/test_flow_ai_record_update.py` 12 用例（update_content None/空串语义、happy path 不插记录不建版本、非本人 403、不存在/跨流程 404、空白 response 101、空白 record_id 404、坏 body 业务码不 500、缺失 events 不覆盖）；47 passed（含 flow 既有 3 套件回归），ruff 违规数与 HEAD 一致（flow_service 12=12、flow_app 59=59 零新增），`npm run build` 通过

**遗留**：无（已部署 2026-09-15：后端 2 文件成套 SCP + 容器重启 + 前端 build 上传 + nginx reload；部署后冒烟通过——容器 import OK、登录态实测 ai-record 路由通/鉴权通/业务 404 正常）；预存与回填之间容器重启会留下「（生成中…）」占位记录（下次进入可人工辨识，属可接受极端情况）。

## 2026-09-15 C端多范本命中暂停询问用户选择（智能折中）（已部署 2026-09-15）

**主题**：C端流程/对话中用户内容经 `_select_templates` LLM 选型后直接全部填充；需求是「查出多个范本时询问用户要哪个，而不是默默全填」。经确认采用智能折中：LLM 只选 1 个 → 维持现状不打断；选多个 → 先推 `selected`（flow 面板 templates.length>0 守卫要求范本行先可见）再推 `select_pending` 确认卡，用户勾选（≥1）提交后按最终子集继续（二次 `selected` 整体替换行卡片）；超时 600s → `select_timeout` 按 AI 初选继续。复用已上线 confirm_pending SSE + Redis 轮询 + POST 回传机制同构骨架。

**核心变更**（后端 2 文件 + 前端 7 文件）：
- `agent/component/template_fill.py`：新增 `_confirm_template_selection(task_id, chosen)`——推 select_pending（select_nonce/select_candidates/ai_selected）→ 轮询 `tpl_fill:select:{task_id}:{nonce}`（1.5s×600s，与字段确认同款运行级 nonce 防孤儿键）；合法提交过滤未知 id+去重保序返回子集，空/全非法/坏 JSON/Redis 故障兜底走 AI 选择，超时推 select_timeout；等待中取消 → _FillCancelled；`_invoke_async` 多选且有 task_id 时接入，勾选集变化再推一次 selected
- `api/apps/restful_apis/template_api.py`：新增 POST `/template/fill/select-confirm` 端点——payload {task_id, nonce, template_ids}，nonce 卫生校验与 confirm 端点同款（限长 64+白名单），ids 清洗去重保序，TTL 700（> 节点 600s 超时），Redis set 返回值必查
- `web/src/hooks/template-fill-stream.ts`：新增 `ITemplateFillSelectPending/ITemplateFillSelectCandidate` 类型与 tf.pendingSelect 态；reducer 新增 select_pending/select_timeout 分支（语义同 confirm）；二次 selected 清挂起态
- `web/src/pages/c-chat/template-fill-confirm-card.tsx`：新增 `TemplateSelectConfirmCard`——候选 checkbox（初始勾选=AI 初选全集，name+填写点数+描述），提交校验 ≥1，submitted/expired 灰字态与字段确认卡一致
- `web/src/pages/c-chat/template-fill-progress.tsx`：挂载选择卡（selectCard 先于 confirmCard）；新增 onSelectSubmitted prop
- `web/src/hooks/use-send-message.ts` / `web/src/hooks/use-template-fill-request.ts` / `web/src/utils/api.ts`：`markSelectSubmitted` 流式态回写（防迟到 select_timeout 置灰）+ `confirmTemplateFillSelect` mutation + select-confirm 路由常量
- `web/src/pages/c-chat/index.tsx` / `web/src/pages/c-chat/flow/flow-ai-panel.tsx` / `web/src/pages/c-chat/flow/flow-detail.tsx`：onSelectSubmitted/onSelectSubmittedReady 透传接线（镜像 confirm 同款模式）
- 测试：component 新增 8 用例（超时兜底/ghost 过滤去重保序/5 类脏载荷兜底/取消中断/端到端 selected→select_pending→selected(子集)/无 task_id 与单选不询问）+ routes 新增 4 用例（空 ids 拒绝/nonce 卫生/清洗写键 TTL/Redis 故障友好报错）；39+104 全绿，ruff 违规数与 HEAD 一致（零新增）

**验证**：`uv run pytest` 两套件全绿；`npm run build` 通过。

**遗留**：无（已部署 2026-09-15：后端 2 文件成套 SCP + 容器重启 + 前端 build 上传；部署后冒烟通过——容器 import OK、登录态实测 select-confirm 非法 nonce 拒绝/合法 payload 200、Redis 键 `{"template_ids": ["t2", "t1"]}` 去重保序 + TTL 633 在 (600,700] 区间）；历史回放对未知 stage 走 reducer default 忽略，旧消息不受影响；选择确认与字段确认串行（先选范本再确认字段）。

## 2026-09-15 B端确认视图行补中文名+key 显示 / 点击定位红色脉冲闪烁

**主题**：① 用户反馈「预览里的 {{extra_long_bridge_count_2325}} 徽标在填写点列表中找不到」——排查确认该行**在列表里**（290 项含 特大桥座数/para:2303），根因是确认视图占位符列只渲染锚文本，同形留白范本几百行全是「（留白 N 字符）」，无法与预览 {{key}} 徽标对号；② 用户反馈点击行定位的琥珀色闪烁与常显高亮同色不醒目。

**核心变更**（纯前端 2 文件）：
- `web/src/pages/template-fill/placeholder-table.tsx`：确认视图占位符列改两行结构——主行中文名（无名称回退 key），副行 `key · 锚文本形态`（留白 N 字符）；低置信/未定位徽标保留
- `web/src/pages/template-fill/fidelity-preview.tsx`：定位闪烁从静态 2px outline 改为红色（#ef4444）脉冲动画（3px 描边+box-shadow 光晕扩散+底色呼吸，0.5s×4 次 2s，keyframes 层叠高于 mark 行内底色、结束自动还原），与琥珀常显高亮形成对比

**验证**：eslint/prettier 通过；线上数据核查 290 项无重复 key、目标行存在。

**遗留**：~~未部署~~（**已部署 2026-09-15**，与段落哈希直定位前端部分、红色脉冲闪烁同批 build+SCP+nginx reload）。

## 2026-09-15 B端点击定位错位根修：段落哈希直定位通道（同形留白 122→275 精确命中）

**主题**：B端范本详情（机电监理 9eaac738，290 填写点）用户反馈「点击占位符行跳转位置不对，且列表不是按文档顺序排」。排查结论：**列表顺序本来就是文档序**（DB para 索引单调递增、cell 交错正确），错位感知来自跳转错误——根因是同形留白（大量 `＿＿＿`/纯空格串）靠**全文顺序分配**：文档靠前的未注册留白把出现序号吃掉，addr 靠后的注册项 mark 落到错误段落（legacy 管线位置校验仅 122/290 正确）。新增**段落哈希直定位通道**：后端按 addr 精确解析锚点所在段落，附加 4 个定位元数据字段，前端按段落指纹精确到段、段内序号精确到第几个留白。

**核心变更**（后端 2 文件 + 前端 5 文件）：
- `rag/svr/template_fill/docx_utils.py`：新增 `NORM_WS_RE` 显式空白类（JS/Python \s 口径统一）、`norm_ws`/`para_hash32x2`（双通道 FNV-1a 32bit，16 hex）、`_count_raw_occurrences`（完整 run 校验）；`compute_anchor_positions(file_bytes, placeholders)`——`_build_addr_map` 编址后对每个有 addr 的占位符计算 `p_idx`（段落扁平序号）/`p_hash`（段落归一化指纹）/`a_occ`（段内第几次出现）/`p_total`（总段数），occ 超界不附加（回退 legacy）
- `api/apps/restful_apis/template_api.py`：`get_template` 详情端点对 docx 范本拉原件 blob 调 `compute_anchor_positions`，异常仅记日志不阻塞详情
- `web/src/pages/c-chat/docx-highlight.ts`：`DocxHighlightItem` 扩展 `pIdx/pHash/aOcc/pTotal`；`highlightDocxRanges` 新增直定位通道——段落级聚合（norm/raw/canon/指纹/normToRaw/docFrac，页眉页脚 `<header>/<footer>` 克隆降权）；按指纹分组→组内按 pIdx 聚段落组→候选对齐（计数相等 1:1 按序；不等则贪心打分：① raw/canon 实际可解析强信号——区分同指纹不同空白长度的克隆段，② docFrac 与 p_idx 期望距离）；段内解析 norm 通道（aOcc 第 N 次出现+尾部空白收缩）与 raw/canon 通道（完整 run 校验）；**canon 等长空白规范化回退**——docx-preview 把 w:tab 渲染为 \u00a0（后端原文 \t），raw 精确失配时等长替换二次匹配、偏移直接复用；直定位失败回退 legacy 管线（行为不变）；新增导出 `fnvHash32x2`
- `web/src/hooks/use-template-fill-request.ts`：`TplPlaceholder` 增加可选 `p_idx/p_hash/a_occ/p_total`
- `web/src/pages/template-fill/fidelity-preview.tsx`：anchors 类型扩展 + `anchorsSig` 纳入新字段（变化触发重渲染高亮）
- `web/src/pages/template-fill/detail.tsx`：anchors 映射透传 4 字段
- `web/src/pages/template-fill/placeholder-table.tsx`：view 模式锚文本列纯留白显示「（留白 N 字符）」替代空白
- 测试：`docx-highlight-direct.test.ts` 新增 8 用例（唯一指纹段非首处/同文段 1:1+段内 aOcc/未注册同文段比例就近/纯空白 raw/【tab→nbsp canon 回退】/指纹未命中回退/无元数据不进通道/aOcc 超界回退），30/30 通过

**验证**：jsdom+docx-preview 真实范本 290 锚全量复现——直定位 marked 281/287、位置校验 **OK 275 / WRONG 0**（legacy 位置正确仅 122），未定位从 17 降到 6（剩余 6 为后端 occ 不足未附元数据项，回退也失败，显示「未定位」徽标待重新识别）；SPOT 抽查 project_report_no/name_1/name_2/planned_start_year 全部落在正确段落；`npm run build` 通过。

**遗留**：后端 2 文件已 SCP+容器重启、**线上接口实测 276/290 附带元数据**（2026-09-15）；前端已随同日条目部署（build+SCP+nginx reload）。地址等同文克隆段靠 raw 可解析信号区分，若克隆段留白完全同形仍有理论错位可能（当前范本 0 例）；后端 `_count_raw_occurrences` 未做 tab↔nbsp 规范化（后端原文自洽，不影响）；剩余 14 项历史坏锚需重新 AI 识别。

## 2026-09-15 docx-highlight 匹配管线三类丢失修复（真实范本 290 锚实测 237→270 命中）

**主题**：B端确认视图仍大面积「未定位」。用 jsdom+docx-preview 真实渲染机电监理范本原件 + 290 个 anchor 全量复现 + 插桩定位，找到 `highlightDocxRanges` 匹配管线三类叠加 bug：① **norm 通道用原文（含内部空白）做 indexOf**——「( 批文名称及编号)」括号后有空格的锚对去空白全文永远失配，整组覆没；② **norm 终点吞尾部空白**——preferEnd 解析到节点原始末尾把紧随空白 run 吞进区间，与 raw 空白组占用区间物理重叠，先插入方 deleteContents 截断共享 textNode，另一方偏移越界（Range "Offset out of bound"）被 try/catch 静默丢弃——「(项目名称)」等锚匹配成功却在插入阶段丢失的根因；③ **同段失败的候选先占位再丢弃**——白占出现位置且该 key 直接丢失不再重试。此 bug 同样影响 C端审核预览（共用该函数）。

**核心变更**（纯前端 1 文件 `web/src/pages/c-chat/docx-highlight.ts` 匹配管线重构）：
- norm 通道搜索改用归一化文本（`searchText`），raw 通道仍用原文
- 候选有效性校验（同段、边界可解析）前移到选择阶段（`tryResolve`）：无效候选跳过并继续向后找下一个出现，不再白占位置；`valid`+`resolved` 两阶段合并为一阶段
- norm 终点做**尾部空白收缩**：解析后向前回退全部尾随空白字符（限同段），norm/raw 区间不再物理重叠，从根上消除共享 textNode 截断导致的越界
- 占位日志插桩（CLAIM/EXHAUSTED/LOCATE-FAIL/CROSS-P/RANGE-ERR）仅用于诊断，未进仓库
- 测试：`docx-highlight.test.ts` 新增 3 用例（内部空白 norm 锚/尾部空白收缩+相邻双命中/跨段首现跳过继续找同段出现）；scratch jsdom 验证 39 断言全过；真实范本 290 锚全量复现命中 237→270（剩余 20 为单空格<2 按设计跳过、含 tab 留白渲染差异、留白长度与文档不符/重复项等真实语义问题，需重新识别）

**验证与部署**：`npm run build` 通过；**已部署**（前端 build+SCP+nginx reload，后端无改动）。

**遗留**：raw 通道对含 tab 的留白（docx-preview tab 渲染为特殊元素）仍不命中，可后续做 tab↔空白弹性归一；剩余 20 类真实未命中需 B端重新识别。

## 2026-09-15 B端确认视图「全部未定位」竞态修复（anchors 晚到重渲染）

**主题**：部署后用户反馈确认视图**所有行**都显示「未定位」徽标。根因是首挂载竞态：FidelityPreview 渲染 effect 依赖 `[templateId]` 在子组件先跑，而父组件 `placeholders`（anchors 来源）要等详情接口回来才在父 effect 里初始化——首次高亮跑在**空 anchors** 上，`onMarked` 回传空集合，且 anchors 到齐后无任何机制重跑 → `markedKeys` 永远为空 → 有 addr 的行全部「未定位」。

**核心变更**（纯前端 1 文件 `web/src/pages/template-fill/fidelity-preview.tsx` 重写）：
- **blob 缓存**（`blobRef`）：原件只拉一次，anchors 变化时用同一 blob 重渲染+重跑高亮
- **anchors 稳定签名**（`anchorsSig`：key\0anchor\0addr join）：父组件每次 render 重建数组不能做 effect 依赖，签名 effect `[anchorsSig]` 在 anchors 首帧到齐/识别/保存后触发 `renderDoc()` 重跑高亮
- 渲染函数收敛为 `renderDoc()`：`renderSeqRef` 序号守卫（拉取渲染与 anchors 触发的重渲染竞争只保留最后一次）+ `setRenderedOk` 门控 + `onMarked` 回传
- 点击定位逻辑（focusAnchor/renderedOk 门控/focusDoneRef 一次性防重）不变

**验证**：`npm run build` 通过；**已部署**（前端 build+SCP+nginx reload，后端无改动）。上一条目的「编辑保存后 anchors 变化不重跑高亮」遗留随本修复一并解决。

**追加（同日二连）**：部署后用户仍见整批「未定位」——第二道过滤：fidelity-preview 传参前 `.filter((a) => a.anchor.trim().length >= 2)` 把**纯空格留白 anchor**（trim 后为空串）整项剔除，根本没进 highlightDocxRanges（raw 通道能力在 docx-highlight 内部，前端预过滤不知道）。过滤改为 `a.anchor.length >= 2`（原文长度），通道判定交还给内部。已重新构建部署。

## 2026-09-15 B端填写点列表双模式（确认/编辑）+ 点击行定位文档

**主题**：填写点配置列表暴露 key/中文名/检索词/必填/默认值等 LLM 配置列，但用户查看列表的目的只是「确认识别结果、知道填写点在文档哪里」；且 key 不是文档字面文本无法搜索定位。列表改双模式：默认**确认视图**（#/占位符锚文本/位置，整行可点击），点击行左侧保真预览平滑滚动到对应填写点并琥珀色闪烁 2s；配置列收纳进**编辑模式**（「编辑配置」按钮切换，全列与保存/AI 识别/添加行为不变）。高亮未命中的行显示「未定位」徽标，直接暴露识别问题。

**核心变更**（纯前端 3 文件）：
- `web/src/pages/template-fill/fidelity-preview.tsx`：新增 `focusKey`/`onMarked` props——`focusAnchor`（`mark[data-anchor-key]` scrollIntoView 居中 + outline 琥珀闪烁 2s，复用 C端 live-preview 模式）+ `renderedOk` 渲染完成门控 + `focusDoneRef` 一次性防重；回传 `highlightDocxRanges` 返回值（成功标记 key 集合）
- `web/src/pages/template-fill/placeholder-table.tsx`：新增 `mode`（默认 'edit' 向后兼容）/`onLocate`/`markedKeys` props；view 模式渲染只读三列表格（低置信徽标保留）；新增 `addrLabel`（para:N→第N+1段、cell:N→表格N+1、hdr:/ftr:/tx:/sdt: 前缀映射）
- `web/src/pages/template-fill/detail.tsx`：`configEditing`/`focusKey`/`markedKeys` state + 卡头「编辑配置⇄完成编辑」toggle；AI 识别/添加/保存按钮仅编辑模式显示；文本模式/渲染失败不传 markedKeys（防误导性「未定位」）
- 测试/验证：`npm run build` 通过（lintstaged prettier+eslint 过）

**遗留**：~~未部署~~（已随同日「全部未定位」竞态修复条目部署）；编辑保存后 anchors 变化不重跑高亮——已随竞态修复（blob 缓存+签名重渲染）一并解决；新增未填 key 的行点击定位静默无效果。

## 2026-09-15 B端保真预览纯空白 anchor 徽标缺失修复（raw 原文匹配通道）

**主题**：通用本保函段「查验保函网址：（必填）/ 开 立 人：（公章）/ 地址/电话」等大量填写点的留白是**纯空格串**（非下划线字符），归一化去空白后为空串，被 `text.length < 2` 拦截，同日的徽标修复对它们完全无效（专用本 623 个填写点中 556 个属此类）。`highlightDocxRanges` 新增 **raw 原文匹配通道**：纯空白 anchor 走 `fullRaw.indexOf` 精确匹配（docx-preview textNode 保留原始空格），实体字符 anchor 仍走归一化通道，两通道互不干扰。

**核心变更**（纯前端 1 文件 `web/src/pages/c-chat/docx-highlight.ts`）：
- 节点偏移快照增加 `rawStart/rawEnd` + `fullRaw` 原文拼接；分组按 `text.length===0 && rawText.length>=2` 判定 raw 通道（组键 `R:`/`N:` 前缀隔离）
- 新增 `locateRaw`（原始偏移算术映射，preferEnd 语义与 locate 一致）；插入改为「先全部解析为节点位置 → 按 raw 空间 sortKey 文档序从后往前」（norm/raw 两个偏移空间不可直接排序）
- 修复过程中发现并解决两个匹配陷阱：① **跨组占位撞死**——10 空格串的 8 空格子窗口先被 raw 组占用后，8 空格组 `continue` 不推进 `searchFrom` 导致整组全灭 → `findFree` 循环跳过被占位置；② **子窗口误匹配**——8 空格 anchor 会命中 10 空格串内部 → raw 通道要求「完整空白 run」（候选窗口前后必须是非空白字符或文档边界，留白段由可见文本分隔的语义）
- 测试：`docx-highlight.test.ts` 新增 raw 通道 7 用例（纯空格命中+原始空白保留/同形多项 addr 分配+不同长度互不误匹配/完整 run 校验/raw+norm 混合/段尾 preferEnd/单空格跳过/出现次数超额），jsdom+esbuild 直跑 32 断言全过；**已部署**（前端 build+SCP+nginx reload，后端无改动）

**遗留**：同前条——页眉/页脚未渲染区无徽标；中间未注册同形留白会错位（addr→DOM 精确定位待做）；anchor 与检测时留白长度不一致则 raw 通道不命中（需重新识别）。

## 2026-09-15 B端范本保真预览显示填写点占位符徽标 + 显示不全修复

**主题**：保真模式预览只看到琥珀色高亮，不知道该填写点对应哪个占位符——`highlightDocxRanges` 新增可选 `opts.showKeyBadge`，在 mark 高亮末尾追加 `{{key}}` 内联徽标（小字号白字琥珀底 chip）。实现中发现并修复「显示不全」的两个根因：① **同形 anchor 只标首处**——范本大量填写点共用同串留白（如都是 `＿＿＿`），位置去重后只显示第一项；② **段落末尾 anchor 全部丢失**——`locate()` 把区间终点解析到下一段开头，同段校验误判跨段整项跳过（此 bug 同时影响 C端审核预览的段尾锚点）。

**核心变更**（纯前端 3 文件）：
- `web/src/pages/c-chat/docx-highlight.ts`：① `highlightDocxRanges` 加可选 `opts.showKeyBadge` 追加 {{key}} 徽标；② 同形 anchor 多项按 addr 数值感知排序（`para:10` 不再排在 `para:9` 前）依次分配文档第 1/2/…次出现（occ 语义），单项组保持首处+start 提示；③ `locate()` 加 `preferEnd`——区间终点恰为节点归一化终点时优先解析为该节点末尾（Range 语义等价但 parentElement 同段校验不再误判）；④ `DocxHighlightItem` 加可选 `addr`
- `web/src/pages/template-fill/fidelity-preview.tsx`：传 `showKeyBadge` + addr，说明条补徽标说明
- `web/src/pages/template-fill/detail.tsx`：anchors 透传 addr
- 测试：新增 `web/src/pages/c-chat/docx-highlight.test.ts`（11 用例：同形分配/addr 数值序/段尾边界/跨段拦截/同 key 去重/徽标/空安全/混合组回归；jest 基建因遗留 umi/test 配置不可用，等价用例经 jsdom+esbuild 直跑 18 断言全过）

**遗留**：未部署——纯前端，`npm run build` + tar + dist SCP + nginx reload；页眉/页脚未渲染区域仍无徽标（与高亮一致）；同形顺序分配在「中间有未注册同形留白」时会错位（后续可做 addr→DOM 精确定位）；web jest 配置遗留 `umi/test` 依赖缺失，测试基建待修。

## 2026-09-14 范本填写确认卡填写项点击定位跳转

## 2026-09-14 范本填写确认卡填写项点击定位跳转

**主题**：confirm_pending 确认卡里用户看得到字段名却定位不到成稿中的填写点——把每个填写项的字段名做成可点击（hover 虚线下划线，勾选/划线均可点），点击经父组件 `onLocate` 回调写入 `liveTarget({template_id, focusKey})`，复用未填充汇总同款 LivePreview 定位链路（data-ph-key → scrollIntoView 居中 + outline 闪烁 2s）直达成稿对应位置。

**核心变更**（纯前端 2 文件，后端零改动）：
- 确认卡组件：填写项字段名渲染为可点击元素（hover 虚线下划线提示可点），新增可选 `onLocate(template_id, focusKey)` prop；未传时回退纯文本渲染，向后兼容存量调用方
- 父组件：接线 `onLocate` → `setLiveTarget({template_id, focusKey})`，完全复用既有 LivePreview focusKey 定位链路（滚动居中 + outline 闪烁 2s、渲染完成门控 + 一次性防重），无新状态机

**测试**：确认卡 jest 2 用例（点击触发 onLocate 回调携带 template_id+focusKey / 未传 onLocate 保持纯文本不抛错）+ 相关套件回归 38 passed；`npm run build` 构建通过。

**遗留**：未部署——纯前端改动，`npm run build` + tar + nginx reload 即可，无需重启后端。

## 2026-09-14 范本填写未填充汇总与成稿内定位跳转

**主题**：范本填写完成后用户不知道成稿里哪些填写点留了空、还要逐页翻找——终态从 values 派生 `unfilled`（留空填写点清单），经 filled 事件/progress 端点透传到前端，成稿行内联汇总展示，点击定位跳转到成稿预览中的对应位置并闪烁。

**核心变更**：
- `rag/svr/template_fill/executor.py`：新增 `derive_unfilled` 纯函数——终态 values 派生成稿留空填写点（key 缺失/None/空串/纯空白 = 未填充，与 docxtpl 空值渲染留白口径一致）；显式 None 判断防数值 0/0.0/False 与字符串 "0"/"false" 被误杀（number 字段合法产值）；required 缺失兜底 True（detector 默认）
- `api/apps/restful_apis/template_api.py`：progress 端点 `build_progress_payload` 终态（done/partial）透传 `unfilled`（placeholders 由端点层查范本版本传入，快照缺失回退 DB render；派生为空不下发空数组）
- `agent/component/template_fill.py`：画布 TemplateFill 节点 done 分支 filled 事件带 `unfilled`（`_unfilled_of` 包装，值源 DB 行 values.render，不依赖 Redis 快照存活）
- 前端：SSE 归约器/轮询 hook 合并 `unfilled`（缺省不下键不清 SSE 数据）；成稿行内联汇总（必填红标「必填」徽标/选填灰/点击带 focusKey）；LivePreview focusKey 定位（docx span 与文本 span 写入 data-ph-key，滚动居中+outline 闪烁 2s，渲染完成门控+一次性防重+降级路径 textContainerRef 兜底）

**测试**：derive_unfilled 对抗用例（0/false 不误杀/纯空白/缺 key/required 兜底）、progress 端点（含 partial 终态派生/非终态不下发/快照值权威）、`_unfilled_of` 边界（行缺 values 属性返回 None 不抛错/render=None 按全空派生全部占位符）、前端归约/轮询 jest 用例；后端 5 套件 154 passed；`npm run build` 构建通过（1m29s）。另修 `test_find_running_rejects_stale_running_rows` 时间容差单侧 flaky（上界补 +5000ms 时钟流逝容差）。

**遗留**：未部署——后端 3 文件成套 SCP（`rag/svr/template_fill/executor.py`、`api/apps/restful_apis/template_api.py`、`agent/component/template_fill.py`）+ docker restart；前端 `npm run build` + tar + nginx reload。前后端可独立部署（增量字段无互锁）。

## 2026-09-14 AI 识别 occ 超界判死整次识别（溢出封顶修复）

**主题**：「电子招标投标示范文本」（1351 候选大范本）AI 识别失败，detect_error=`识别结果校验未通过：station_range：锚文本出现次数不足（需第 2 处，实际仅 1 处）`。根因：LLM 对同段同形留白（`＿＿`）输出 2 个不同字段的建议落在同一 (addr, anchor) 组，`_merge_detection::_preassign_occ` 按组大小机械分配 occ=1..n 不核对 anchor 实际出现次数，occ=2 超界 → `validate_placeholders` 保守拒绝（判死整次识别，全部建议丢弃）——旧注释明示「接受此权衡」，但代价是 LLM 输出随机性可让一个坏组毁掉整次识别且重跑可能复现。

**核心变更**（`rag/svr/template_fill/detector.py`）：
- `_preassign_occ` 增加 cap 封顶：`_merge_detection` 新增可选 `candidates` 参数构建 addr→text 映射，组内序位 n 超过 `text.count(anchor)`（非重叠语义，与 validate 终审同口径）的溢出项**直接丢弃**（LLM 重复建议/幻觉），不再分配超界 occ；显式直通组与 LLM 组同样封顶；幻觉 addr（cap=0）整组保守丢弃
- `detect_fill_points` 透传 `candidates`；`validate_placeholders` 严格终审不变（继续保护人工确认保存路径）；不传 candidates 的直调行为同旧（兼容存量单测）

**测试**：`test_template_fill_utils.py` +9 对抗用例——真实事故复刻（组 3 项实际 1 处 → 保 1 丢 2 过终审）/ 非重叠计数口径对齐 / 组大小==实际次数不误丢（回归）/ 按 (addr, anchor) 组隔离 / 幻觉 addr 全组丢弃 / 不传 candidates 旧语义回归 / explicit 组封顶 / 溢出封顶优先于收缩回退 / 跨源回显路径不受封顶影响；286 passed（全模板填写套件）；代码审查无 block。

**部署**：**已部署 2026-09-14**（`detector.py` SCP + 容器重启，容器内冒烟验证溢出封顶生效；目标模板「电子招标投标示范文本」重新识别成功：detect_status=done，识别 1111 个填写点，临时重跑脚本已清理）。

## 2026-09-14 PDF 矢量留白横线回填（识别不到横线修复）

**主题**：范本 PDF 上传后「福建省___市（区）___」「招标编号：____」等填写留白横线识别不到——根因是源 PDF 里这些横线是**矢量绘制线条而非文字**，pdf2docx 只转文字/表格、矢量线直接丢弃，留白在转换件中消失。修复：转换前用 PyMuPDF 把留白横线回填为 `_` 文字再交给 pdf2docx。

**实测依据**（237 页样张）：横线画在文字基线上（线 y 与同行词中点 y 差恒定 ~6.5-7pt）；全文档 225 页含横线；增强后封面碎片行（pdf2docx 重建为表格 cell）出现真文字 `__________`/`市（区）______`/`（招标编号：____________`，候选提取 **565 → 1351**（生产口径含网格防护；无防护原型为 1886，多出的 535 全是表格边框被误填下划线的垃圾候选——网格防护实测剔除 195 条真表格边框线，页 15 纵线 x=282 横贯 y=167..631 即典型）。

**核心变更**（`template_api.py`）：
- `_blank_line_targets(drawings, words)` 纯几何判定：水平线段/扁矩形（长≥15pt 厚<3pt）中，排除①表格网格边框（端点接纵线 ±2pt）②装饰线（同行无文字）；「同行」= 基线 y 差 ∈ (2,10)pt，「相邻」= 词与线区间水平距离 <120pt（容 0.1pt 级重叠——实测封面横线起点比词尾早 0.1pt，无容差会漏掉「项目报建编号：____」）
- `_augment_pdf_blank_lines(src)`：回填 `_`×N（N 按线宽/字宽，最少 3 个，基线上方 1.5pt）另存 input_aug.pdf；**尽力而为**——fitz 异常（畸形/加密 PDF）告警回退原 PDF 不阻断转换；以 stream 方式打开不持源文件句柄（Windows 下失败构造的遗留句柄会锁临时目录致清理报 WinError 32）
- `_convert_pdf_to_docx` 转换前调用增强
- 测试 +6：几何判定对抗用例（留白命中/装饰线跳过/表格网格跳过/远距文字跳过/扁矩形+词重叠）+ 真 fitz 集成（畸形 PDF 回退/真 PDF 产出副本含下划线/无横线零改动）；99 passed

**边界**：扫描件无矢量线不受影响；表格网格防护已在本样张页 15 真 ruled 表上验证有效；横线本身无同行文字的独立留白行不回填（避免装饰线误污染，真实范本罕见此形态）。

**部署**：**已部署 2026-09-14**（`template_api.py` SCP + 容器重启，线上实测转换/回填/候选提取全链路通过）。

## 2026-09-14 PDF→Word 转换引擎替换为 pdf2docx

**主题**：范本库 PDF 上传的转 docx 引擎从 LibreOffice `writer_pdf_import` 替换为 pdf2docx（真实样张 A/B 实测后用户裁定采纳）。

**实测依据**（237 页《电子招标投标示范文本.pdf》容器内 A/B）：LibreOffice 产出 34,050 个定位文本框 / 0 个原生表格，python-docx 按段落读取 0 字符（下游识别/渲染链路不可见，是转换件格式差的根因）；pdf2docx 产出 0 文本框 / 633 个原生表格（989 处合并单元格）/ 6,119 真段落，耗时 26.5s。

**核心变更**：
- `template_api.py`：新增 `_convert_pdf_to_docx`（pdf2docx Converter 惰性导入 + 临时目录 + close 兜底且 close 异常不顶替真凶 + 空/缺产物 RuntimeError）；upload PDF 分支改调它（专用 2 线程池 `_PDF_CONVERT_POOL` + `asyncio.wait_for` 300s 超时——病态 PDF 自旋只占专用池，不拖垮全局 to_thread 池，超时返回「PDF 转换超时」）；`_convert_to_docx` 收窄为仅 .doc 走 LibreOffice（移除 infilter 分支）
- `pyproject.toml`：主依赖加 `pdf2docx==0.5.13`（连带 PyMuPDF——AGPL，服务端内部使用无碍；pdf2docx 本体 MIT）
- 测试：`test_template_api_routes.py` 同步——passthrough 测试收窄为 .doc-only，新增 `_convert_pdf_to_docx` 用例（stub 模块注入 sys.modules，不依赖本地装包；覆盖 close 必调/产物缺失/0 字节产物 RuntimeError）；3 个 PDF 上传用例改 patch `_convert_pdf_to_docx`；93 passed；审查（Code Reviewer agent）结论无 block，major（超时保护）与 minor（0 字节用例/close 掩蔽）均已修复

**遗留**：uv.lock 未同步——全量 `uv lock` 被存量 mistralai==0.4.2 × scrapling[fetchers] 冲突卡死（scrapling 自 6 月加入 pyproject 起从未进 lock，存量问题），重建镜像前须先解该冲突；运行容器内已临时 pip 安装 pdf2docx 0.5.13（docker restart 保留、recreate 丢失）。LibreOffice 产物文本框全靠 `:tx<k>:` 编址覆盖的旧链路仍是 2026-09-13 加固版的兜底能力，不受影响。

**部署**：待部署——`template_api.py` SCP 热更新即生效（容器已有 pdf2docx）；镜像重建需先修复 uv.lock 并确认 pdf2docx 进镜像。

## 2026-09-14 B端范本预览保真渲染（双模式）

**主题**：B端范本详情「模板预览」从纯文本段落列表升级为双模式——默认保真视图（docx-preview 渲染 original 原件，字号/加粗/颜色/表格/排版与源文档一致），「文本模式」切换保留原有划选标记填写点功能。设计文档 `docs/superpowers/specs/2026-09-14-bend-template-fidelity-preview-design.md`。

**核心变更**：
- 新增 `web/src/pages/template-fill/fidelity-preview.tsx`：拉 kind=original blob（JSON 错误体解析）→ renderAsync 保真渲染 → 屏外页懒渲染（applyDocxPageLazy）→ 已注册填写点按锚文本琥珀色高亮（复用 c-chat/docx-highlight 的 highlightDocxRanges，归一化匹配、同 key 只标首处；已知边界：同形文本取首处，不按 addr 精确定位）
- `detail.tsx`：previewMode state（默认 fidelity，切模板重置）+ 卡头「文本模式/保真模式」切换按钮（仅 docx）+ 渲染失败自动降级文本模式（一次性 warning + console.warn 排查日志）；text 分支与划选标记零改动；xlsx 分支不受影响

**边界**：保真视图内不可划选（页眉/页脚/文本框 docx-preview 渲染不完整无法映射 addr，双模式是用户裁定方案）；preview 接口 0 段时仍显示空态（先于 fidelity 分支）。

**部署**：纯前端（detail.tsx + fidelity-preview.tsx，build + dist SCP）。

## 2026-09-14 范本库支持上传 PDF（转 docx 后 AI 识别）

**主题**：范本上传入口格式归一化——.pdf 上传即用 LibreOffice 转 docx，之后全链路（候选提取/AI 识别/预览/替换/标蓝/下载）按 docx 处理，与旧版 .doc 策略同构。设计文档 `docs/superpowers/specs/2026-09-14-template-pdf-upload-design.md`。

**核心变更**：
- `template_api.py`：新增 `_is_pdf`；`_convert_doc_to_docx` 泛化为 `_convert_to_docx(blob, src_ext=".doc")`（src 临时文件按后缀选 soffice import filter，输出恒 docx，.doc 行为不变）；upload_template 新增独立 PDF 转换块（asyncio.to_thread + 转换失败文案 + 转换后体积复查）；提示文案三处含 .pdf；`_ZERO_CANDIDATES_MSG` 追加 PDF/扫描件兜底子句
- 前端 `upload-wizard.tsx`：文件正则/accept/label/错误文案/拖拽提示 5 处加 .pdf
- 测试：`_is_pdf` 边界、src_ext 传参、PDF 上传 happy/失败路径、对抗用例（伪 .pdf 内容、.pdfx 伪扩展名、转换产物膨胀超 20MB）

**已知边界**：soffice 的 PDF 导入是 Draw 系导入，转出 docx 文字多在文本框内——识别/替换/标蓝已覆盖文本框（依赖 2026-09-13 识别加固的 `:tx<k>:` 编址），但版式保真度待真实样张验证；图片型扫描 PDF 转 0 候选走兜底文案。

**遗留**：timeout 断言在测试中暂不锁数值（待工作区他人 timeout hunk 定向后恢复锁定）；设计文档 §2.1 已注明 .doc 分支 to_thread 包装属他人未提交 hunk。

**部署**：**已部署 2026-09-14**（后端 4 文件 SCP + 容器重启 + 前端 build/dist SCP + nginx reload）。注意：template_api.py 部署的是工作区版本，包含当时未提交的 4 个 hunk（timeout 60→180、.doc 转换 asyncio.to_thread 包装、detect 两处零候选守卫）——零候选守卫依赖的 `_ZERO_CANDIDATES_MSG` 已随识别加固入库，运行时已验证。**上线即修**：首版 PDF 转换报「source file could not be loaded」——soffice 默认按 Draw 打开 PDF 无法导出 docx，已补 `--infilter=writer_pdf_import`（commit 86249f25，容器内真实 PDF 转换验证通过）。

## 2026-09-13 范本 AI 识别加固

**主题**：识别覆盖面扩展（页眉/页脚/文本框/内容控件）+ 同形留白多点 occ 语义 + LLM 解析与警示加固。设计文档 `docs/superpowers/specs/2026-09-13-template-detect-hardening-design.md`。

**核心变更**：
- `_build_addr_map` 单点编址扩展（`rag/svr/template_fill/docx_utils.py`）：页眉/页脚（`hdr:/ftr:` 前缀，linked 跳过 + partname 全文档去重，first/even 显式 unlinked 追加后缀段防撞号）、文本框（`:tx<k>:` 后缀，mc:AlternateContent 只取 Choice，深度限 8）、body 直系 w:sdt 递归展开（独立 `sdt:` 前缀 + `:sdt<k>:` 嵌套）——候选提取/B端预览/替换/标蓝四链路自动受益，docxtpl 原生渲染新区域 {{key}}。**存量 addr 兼容红线**：`para_seq` 消耗者仍只有 body 直系段落 + 顶层表格 cell 段落（legacy 标志隔离），嵌套/新区域零消耗，升级前文档存量 para:/cell: 序列逐字节不变（6 场景与基线对比验证）
- occ 语义：同段同形留白不再静默丢弃。同源同 (addr, anchor) 组预分配 occ（第 N 次非重叠出现定位，`_nth_index` 按 len(sub) 步长）；替换三路径（单 run/跨 run/拼接）支持 occ；缺省 occ=None 保持存量 replace-all 逐字节不变；occ 越界 no-op + warning（对齐设计 §8 可观测性）；xlsx 禁用 occ 预分配（apply 层不识别，防串值）
- occ 识别链（`detector.py`）：同源组预分配（LLM 组 occ≥2 打 low_confidence）+ 跨源手动优先 + parse 阶段记 anchor 偏移防 LLM 乱序输出串位 + validate 出现次数/序号校验（手动行仅 occ=1）；文案去术语
- LLM 输出解析三级容错（`_extract_json_array`）：裸数组直解 → 剥 ``` 围栏 → 贪婪正则回退，全败返回 []
- 前端：填写点表格锚文本列「低置信」琥珀徽标（title 说明收缩修正/同形留白）+ 0 候选文案标注覆盖面（正文/表格/页眉/页脚/文本框）

**验证**：8 套件 415 passed（唯一失败 test_convert_doc_to_docx_success 为他人未提交 timeout 改动所致，与本迭代无关）；`test_template_fill_utils.py` 178 passed 含 16 个 occ + 10 个解析容错 + 存量逐字节兼容钉子；前端 npm run build 通过。

**遗留**：已填现值型（段落无留白特征不进候选）仍不识别（设计文档已知边界）；LLM 同段幻觉重复 anchor 且实际仅 1 次出现时 occ 校验拒绝整批（保守失败）；C 端 docx-preview 保真预览对新区域的展示未专项适配。

**部署**：**已部署 2026-09-14**（后端成套 SCP `docx_utils.py` / `detector.py` / `renderer.py` / `template_api.py` + 容器重启；前端 `placeholder-table.tsx` build + dist SCP；与同日 PDF 上传适配一并上线）。无数据库变更。

## 2026-09-13 加强：流程「版本记录」默认勾选聚焦最新一条

**主题**：版本记录栏默认选中逻辑跟随 `current_version_id`——回退后 current 指向旧版本，打开流程时勾选落在旧版本上，且其批注/详情视图也跟随旧版本，不符合「默认看最新」预期。

**修复**（`web/src/pages/c-chat/flow/flow-detail.tsx`，纯前端）：
- `selectedVersion` memo 默认值由 `current_version_id` 改为 version_no 最大的最新版本（reduce 取最大，与倒序列表首项一致）；用户手动点击后以用户选择为准
- 补 `selectedVersionId` 随 flowId 切换重置（与 visibleCount 重置一致）：切走再切回不带入上一轮手动选择，回到默认最新

**验证**：Playwright 三场景（demo05 打开=v2 最新高亮 → 点 v1=用户选择生效 → 切 demo06 再切回=重置回 v2）全过；tsc 本文件零错误。

**遗留**：无。**未部署**（与同日白屏修复、保真预览切换一并，纯前端 build+SCP+nginx reload）。

## 2026-09-13 加强：文件审核默认保真预览显示原色 + 显式编辑切换

**主题**：文件审核（flow-ai-panel 路径）打开成稿看不到原文颜色（范本填写标蓝的 LLM 填充内容显示为纯黑）。根因：`canEdit=true` 时 ReviewPanel 一律进 Lexical 旧段落编辑视图（纯文本模型，颜色/格式必然丢失），docx 保真视图只有只读路径（眼睛查看）才走。

**修复**（`web/src/pages/c-chat/review-panel.tsx` + `docx-toolbar.tsx`）：
- 可编辑文件默认也进 docx-preview 保真预览（原色/字号/表格保真 + AI 标注/批注锚定照常），头部新增「编辑文档」按钮，显式点击才切 Lexical 编辑视图
- 修一个被掩盖的 React 协调 bug：editing/fidelity/loading/fallback 四个分支根节点同为 div，React 就地复用 DOM 不卸载——残留的 docx-wrapper 渲染产物漏进编辑视图、ref callback 不触发。四个分支根节点加 key 强制重挂
- 「放弃修改」按钮 dirty=0 时原本禁用，现兼任「退出编辑」（有改动时文案仍为放弃修改），保存/放弃/切换文件后均回到保真预览

**验证**：Playwright 全链路（打开=保真 56 页 490 蓝字 span → 编辑=纯 Lexical 无残留 → 退出编辑=保真恢复）通过；tsc 本文件零错误。

**遗留**：编辑视图本身仍是纯文本模型（Lexical 不支持字色），这是编辑态格式简化的既有设计；FlowPanel button 嵌套 button 的 validateDOMNesting 告警为既有问题。**未部署**（与同日白屏修复一并，纯前端 build+SCP+nginx reload）。

## 2026-09-13 调研结论：doc→docx 转换格式问题取证（封面居中/目录）

**取证**：用户反馈文件审核里范本「文字居中了、目录不对、很多格式不对」。取线上 v2 版本文件 + 用户本地原始 .doc（机电监理招标范本 2021.12修订）传服务器容器用同版 LibreOffice 24.2 转换对比 XML：
- 封面居中（含「福建省公路水运工程…」字样）在**原始 .doc 里就是 jc=center**——是范本编制方套用水运范本模板的痕迹，不是转换弄乱；两次转换 XML 一致
- 目录域完好（TOC \o "1-3" 域 + 222 个域指令 + 全部章节行）——目录「不对」是 **docx-preview 对 Word 域渲染支持弱**，真实 Word 打开正常
- 结论：LibreOffice 转换忠实；格式观感问题主要来自 docx-preview 渲染局限，不是转换丢格式

**B端范本库上传时转换 + 全链路 docx 已是现状**（template_api.py：.doc 上传即转 docx，原始 .doc 不留存，识别/填写/预览/下载全按 docx）。

**开源替代结论**：LibreOffice headless 已是开源界 .doc→docx 保真度最高的事实标准；OnlyOffice x2t（AGPL）是唯一可对比备选但部署重、质量互有胜负；pandoc 不支持 .doc 二进制；antiword/wvWare 只提文本；Gotenberg 是 LO 封装。不建议引入新工具。

**可落地改进**：容器补装中文商用字体（宋体/仿宋/楷体/黑体——当前仅 Noto CJK 39 字体，字体替换影响字宽度量→换行位置漂移）；格式争议时用真实 Word 打开转换后 docx 作基准对比。

## 2026-09-13 修复：流程版本历史二次查看 docx 白屏（ReviewPanel 容器重挂不重渲染）

**主题**：demo05 流程版本历史点眼睛查看 docx，第二次打开同一版本内容区空白（首次打开/切换另一版本/切回均正常，唯独「关闭后重开同一版本」白屏）。

**根因**（纯前端，后端链路全程 200 正常）：ReviewPanel 关闭时 `if (!open) return null` 卸载全部 DOM，而 docx-preview 的 renderAsync 渲染产物只存在 DOM 不在 React state。重开同一 fileId 时 blob 命中 TanStack Query 缓存（引用不变）、content 仍在 state → renderAsync effect deps 全不变 → 不重跑渲染 → 容器空白。更深一层竞争：内容请求 effect 的 `setLoading(true)` 会卸载 fidelity 容器，renderAsync 写进 detached DOM，容器重挂后同样因 deps 不变不再渲染。

**修复**（`web/src/pages/c-chat/review-panel.tsx`，epoch 桥接）：容器 ref 改为 ref callback（`docxWrapRefCb`），每次容器真实挂载递增 `docxEpoch` state；renderAsync effect deps 由 `[open, docxBlob, docxFidelityCandidate]` 改为 `[docxBlob, docxFidelityCandidate, docxEpoch]`——任何原因的容器重挂（open 切换 return null / loading 闪断 / 文件切换 / editing↔只读切换）都强制重跑渲染。另采纳审查建议两道防线：`.then` 首行 `if (!el.isConnected) return` 丢弃 stale 渲染产物（防 detached DOM 操作 + stale setMarkedKeys 覆盖）；渲染 effect 起始 `setMarkedKeys(new Set())` 随容器清空重置锚定记录。

**验证**：本地 Playwright 4 场景（首开 55 sections / 重开同版本 55（修复前 0）/ 切另一版本 56 / 切回 55）全过；M1/M2 加入后复验首开+重开仍 55；`tsc --noEmit` 本文件零错误。代码审查（superpowers:code-reviewer）无 Critical/Major。

**遗留**：渲染失败的文件重开不重试渲染（停留在降级段落视图，既有行为非回归）；StrictMode 下 effect 双跑可产生嵌套重复 mark 的外观瑕疵（既有，isConnected 守卫已收敛大半）。**未部署**——纯前端改动，部署须 `npm run build` + dist.tar.gz SCP + nginx reload。

## 2026-09-13 修复：对话按节重写丢章——「第X章」文本章标题兜底切节

**主题**：C端对话 DocumentRewrite「重写第一章」报文档里没有该章。根因（服务器实测证实）：政府范本排版不统一，部分章标题（如机电/高速公路范本第一、二章）在 .doc→docx 转换后是普通文本段落（style=Normal、无大纲级别），`split_sections` 只认 Heading 1 导致这些章从大纲消失（用户大纲只剩第三章起）。

**修复**（`rag/svr/document_rewrite/sections.py`）：新增「第X章」文本特征兜底判定 `_is_chapter_title`——文本以章号开头（汉字/阿拉伯/全角/零〇数字）、≤50 字，排除三类误报：TOC 目录样式段、目录行（字面点线 `......` 及 Word 自动目录的 tab+页码结尾——真实 Word 目录点线是制表符前导符样式而非文本字符）、正文引用句（句读结尾）。标题收集条件改为 `_is_heading1(p) or _is_chapter_title(p)`，混排时按文档顺序统一切节。Heading 2 样式的「第X章」段落也被提升为顶层节（章是重写单元，与 heading 级别无关，有意行为）。

**测试**：`test/test_doc_rewrite_sections.py` 新增 10 个对抗用例（纯文本章识别、Heading1+文本章混排顺序、字面点线/Word tab 目录排除、TOC 样式排除、句读/超长排除、「第一节」不误判、数字变体含第一百零一章、Heading 2 章提升钉住、邻接文本章 para_end==para_start），14→18 用例全过；document_rewrite 全套件 73 passed；ruff 零新增。

**遗留**：另一叠加因素属使用层面——多范本会话产生多张成稿卡，rewrite 绑定 `sys.recent_downloads[0]`（最近一张，机电监理），用户想改的是施工监理成稿；LLM 已正确提示用户先切换/生成目标成稿，暂不改代码。正文引用句（如「第一章总则所述内容适用本章」，≤50 字无句读结尾）理论上仍可能误判，为已知限制。

## 2026-09-13 加强：范本 AI 识别对标准招标范本文件的适配

**主题**：B端范本库 AI 识别填写点针对真实标准范本（`F:\投标项目\投标资料\最新招标文件标准范本`，35 个文件中 15 个 .doc / 12 个 .pdf / 1 个 .docx）的三项加强；PDF 暂不支持（用户决策）。

**核心变更**：
- `rag/svr/template_fill/docx_utils.py`：FILL_HINT_RE 补全角下划线 `＿{2,}` 与勾选框 `□` 特征（此前全角下划线行根本进不了候选集）；`_build_addr_map` 重写——嵌套表格递归编址（父单元格 addr 追加 `:t<j>` 段，此前嵌套表内填写点整体丢失）+ 合并单元格 gridSpan 按 tc 去重（此前同一文本重复进候选致 anchor 反查「匹配到多处」）；apply 时 addr 悬空补 logger.warning（存量模板编址演进定位痕迹）；模块 docstring 同步 addr 约定
- `api/apps/restful_apis/template_api.py`：.doc→docx 转换 timeout 60→180（几百页大部头范本）+ `asyncio.to_thread` 丢线程池（不再阻塞 Quart 事件循环 180s）；同步 detect 端点与后台识别线程对「候选为空」返回专门文案 `_ZERO_CANDIDATES_MSG`（提示 .doc 转换可能丢格式/建议另存 docx/手写占位符），后台线程仍置 failed 可重试
- `rag/svr/template_fill/renderer.py`：仅 docstring 更新（_colorize_placeholder_runs 复用 _build_addr_map 自动获得嵌套覆盖）
- 测试：新增 5 用例（全角下划线/勾选框特征、嵌套表编址+候选+替换、合并单元格去重、无嵌套普通文档编址回归守护），116 passed；ruff 零新增

**兼容性**：顶层表格 tbl_no 编号规则不变，无合并单元格的存量模板 addr 完全一致；含横向合并单元格的存量模板若曾按非首现坐标落库，重渲染时该填写点会 skip 并打 warning 日志——建议此类模板重新识别一次。

**遗留**：PDF 范本（水利工程 2022、普通公路 2024 等 12 个）不支持上传，待后续评估 pdf2docx 方案；前端对 detect 零候选 error toast 的展示需部署后验证。

## 2026-09-13 修复：无版本流程无法记录 AI 处理

**主题**：创建流程时未带初始文件 → 范本填写成稿后自动保存记录报「流程暂无文件版本，无法记录 AI 处理」，`template_fill_events` 不落库、刷新后成稿卡回放丢失。

**修复**（`api/apps/restful_apis/flow_app.py`）：ai-record 端点放开 version_id 空值——无版本流程照存记录（version_id="" 不锚定版本）；填写执行/成稿卡/「存为流程版本」本就不受影响。前端无改动。

## 2026-09-13 修复：存为流程版本后文件审核打开旧文件

**主题**：范本填写成稿「存为流程版本」后，AI 面板「文件审核」仍打开旧版本的文件。

**根因**：`FlowAiPanel.toggleReview` 在 `reviewFileId` 已有值时直接复用旧 document（旧版本转换产物），版本切换后该状态不失效。后端无问题（upload 端点 `add_version` 默认 `switch_current=True`，`current_version_id` 已切新版本）。

**修复**（`web/src/pages/c-chat/flow/flow-ai-panel.tsx`）：新增 `reviewFromVersionId` 记录审阅 document 的来源版本 id；`toggleReview` 进入时若来源为版本且 id ≠ 当前版本 → 作废旧 document 并重传当前版本。手动上传目标（source='upload'）不受版本切换影响；编辑保存/回退切换版本同样触发失效。

**遗留**：审阅抽屉打开状态下版本切换不自动刷新（避免打断用户标注），关闭再开即取新版本。

## 2026-09-13 范本确认卡全量展示字段

**主题**：C端对话/流程的「确认并继续填写」弹框从「仅有默认值字段」扩为**全部 LLM 填写点**，用户自行决定哪些字段交给 AI 填写。

**核心变更**：
- 语义改为白名单：勾选 = 交给检索+LLM；不勾 = 有默认值直用默认值、无默认值留空交人工。初始勾选 = AI 预判变化字段 ∪ 无默认值字段（维持现状全填）
- `agent/component/template_fill.py`：`_confirm_changed_fields` 候选扩为全部 llm 填写点（触发条件不变：有默认值字段才弹框；AI 预判只跑默认值子集）；超时/异常兜底 changed = 预判 ∪ 无默认值字段；`_llm_fill_items` 改白名单语义（decision 缺失的范本全量照旧）；`_canvas_task_params` 的 `_changed_keys` 改写 llm_item_keys
- `rag/svr/template_fill/executor.py`：llm_placeholders 收窄与 missing 纳入去掉 default_value 前提，统一 `key in changed_keys` 白名单判断；B端任务 changed_keys=全部 key，行为零变化
- `web/src/pages/c-chat/template-fill-confirm-card.tsx`：初始勾选 = predicted ∪ 无默认值字段（对话页+流程 AI 面板共用，一处生效）
- 测试：3 套件 107 passed（节点/委托参数/executor 断言同步白名单语义）

**遗留**：
- 未部署（须 `agent/component/template_fill.py` + `rag/svr/template_fill/executor.py` 成套 SCP + 前端 build，后端先于前端）
- 弹框字段多时列表较长，未做分组/折叠（后续可按需加）

## 2026-09-12 C端对话文档按节局部重写（DocumentRewrite）

**主题**：对话里说「把第3节重写，补充XX」→ LLM 按节重写成稿 docx → 新版本成稿卡，可回退。

**核心变更**：
- 新增 Agent 工具 `DocumentRewrite`（agent/tools/document_rewrite.py，outline/rewrite/versions/rollback 四 action，DSL 零改动，自动发现注册）
- 新增执行层 `rag/svr/document_rewrite/`：sections.py（heading1/标题1/大纲级别0切节）、docx_edit.py（段落区间替换+pPr/rPr样式拷贝+表格保留）、rewriter.py（LLM JSON 段落契约+校验重试）、versions.py（doc_rewrite_version 版本链，(root_id,version_no) 唯一索引+并发取号重试，回退=复制式 append-only）
- 新表 `doc_rewrite_version`（init_database_tables + migrate_db 幂等迁移，部署时自动建表）
- flow 场景复用 flow_version 表（`FlowVersionService.add_version` 新增 `switch_current` 参数，ai_rewrite 不切 current_version_id）
- 产物链路：工具写 canvas 全局 sys.pending_downloads → Message 组件合并输出 download 契约 → 成稿卡
- 上下文链路：前端 payload 附 recent_downloads（最近2张成稿卡）/ flow_version_id → canvas.run 白名单 → sys 变量 → 工具读取
- 存量缺口修复：canvas workflow_finished 的 downloads 持久化进 message data + 前端历史消息恢复 downloads 渲染成稿卡（否则刷新后无法发起重写）；canvas.run 开局清零 sys.pending_downloads 防跨轮幽灵成稿卡；sendMessage 从 res.events 回填 downloads（在线流被 done 批处理吞掉的修复）
- 测试：6 个新测试套件 61 用例（切节/替换/版本/重写/工具/管道，对抗用例覆盖无heading、邻接标题、表格保留、并发撞号、越权、非法JSON、链感知连续重写等），13 套件合跑 394 passed 零回退

**遗留**：
- flow 场景 rollback 走流程页签人工操作（对话内提示引导），对话内 flow 回退未做
- heading 识别覆盖度依赖样式名枚举（Heading 1/标题 1/大纲级别0），奇形模板切节失败明确报错优于错切（设计已接受）
- 多 Message 终端节点画布的 pending_downloads 合并约束（仅单 Message 画布成立，已在 Message docstring 钉死）
- E2E 联调（填写→重写→回退→下载还原）待部署后验证
- /api/v1/agents/download 端点无鉴权（既有风险 M8，非本功能引入）

**部署**：后端 7 文件成套 SCP（agent/tools/document_rewrite.py、rag/svr/document_rewrite/ 4文件、agent/canvas.py、agent/component/message.py、api/db/services/canvas_service.py、api/db/services/flow_service.py、api/db/db_models.py）+ docker restart；前端 npm run build + dist 部署。新表由 migrate_db 自动创建。

## 2026-09-12 范本填写后台化与断连重连（方案A）

**主题**：填写执行与 SSE 连接解耦——断连/刷新后任务在服务器跑完，成稿落库可取。

**核心变更**：
- 画布 TemplateFill 节点确认后委托 tpl_fill_task 后台线程（source='canvas'，spawn 与 B端共用），节点降级为观察者轮询（1.5s，总 deadline 1h），事件新增可选 task_id
- executor 对齐节点能力：params 保留键拆分（直填/D−C 变化键/检索跳过/用户文件证据，is_canvas 门控 B端零变化）、Redis 进度快照（tpl_fill_progress:{id} TTL 24h，0.5s 节流+毫秒量纲）、取消键契约（tpl_fill:cancel:{id}）、直填沉淀 override_keys 覆盖 manual 默认
- spawn 抽取 rag/svr/template_fill/spawn.py（daemon 线程+防重入，B端/画布共用）
- 新端点 GET /template/fill/fill-task/{id}/progress（owner 校验+stalled 双信号判定+downloads bucket 桥接 tplfill-{task_id}，记忆化）
- 状态机扩展 cancelled 态；TplFillTaskService 新增 cancel_running/find_running（2h 复用窗）/TERMINAL_TASK_STATUSES/sanitize_filename 公开别名
- 前端：归约记 task_id + useTemplateFillTaskPoll 轮询 hook（仅历史恢复态启用、终态本地合成成稿卡、stalled 提示刷新重试、迟到响应竞态封死）

**遗留**：未部署；flow_instance_id 暂留空；多范本共享检索去重随委托化不再适用（B端行为）；汇总文案不再含 filled 计数（节点不再持有逐槽状态）；stalled 行不可 retry（retry 白名单仅 failed/partial，放宽需带超龄 CAS 条件，下轮）；executor 检索期心跳缺失（>10min 纯检索长任务有 stalled 误判窗口，R1）；retry 成功不覆盖旧 progress 快照（当前仅 B端用 retry，无轮询冲突，R2）；B端 create_fill_task 入参未剥 `_` 前缀保留键（自伤范围，建议硬化，R3）；find_running 复用会静默丢弃新一轮确认的直填值（设计 §6 已接受的取舍，R5）。终审全量：后端 8 套件 346+ 用例、前端 jest 32 用例全绿；部署须后端 5 文件成套 SCP（executor/spawn/template_api/template_fill_service/template_fill.py）+ 前端 build。

## 2026-09-12 执行失败对话 UI 提醒（toast 弹窗 + 气泡红底红字）（已部署 2026-09-12）

**主题**：在 canvas 错误兜底消息基础上加显式 UI 提醒，用户不再只看到一段普通文本。

**核心变更**：
- 后端 `canvas_service.py`：兜底 message 事件 data 加 `error: true` 标记
- 前端 `flow-ai-panel.tsx`：发送完成后扫描 SSE 事件，命中 `message && data.error` 时弹 `message.error` toast（内容即错误文本）
- 前端 `flow-detail.tsx`：`isErrorResponse`（「执行失败：」前缀识别）——历史记录气泡与进行中实时气泡红底红字（`border-red-300 bg-red-50 text-red-700`），刷新回看同样醒目

**部署**：2026-09-12 后端 SCP + 重启 + 前端 build 上传 + nginx reload；git 已推送（385fd4e2）。

## 2026-09-12 修复流程对话运行期报错后 UI 整轮空白（canvas 错误兜底为 assistant 消息）（已部署 2026-09-12）

**主题**：流程 demo03 触发对话后界面被置空。根因：范本填写节点运行时报「暂无可用的已发布范本」（范本识别完成但仍为草稿未发布），canvas 只置 `canvas.error` 后静默结束——无 message 事件、无异常，`completion` 以 0 字符文本走 final 落库，SSE HTTP 200 正常收流，前端无错误分支可走 → 整轮空白、flow_ai_chat 不落记录（错误只存在 API4Conversation.errors 无人展示）。

**核心变更**（`api/db/services/canvas_service.py` completion）：canvas 正常收尾后若 `canvas.error` 非空且无任何回复文本，把错误兜底成一条 assistant message 事件（`执行失败：{error}`）下发，并随 final 落库——前端气泡可见、刷新可回看、flow_ai_chat 自动保存能拿到文本。事件结构与 canvas 原生 message 事件对齐（`data.content`）。

**遗留**：仅修 session 补全路径（流程 AI 面板/对话页共用）；agent_api 无 session 直跑路径（B端调试运行）如遇同类错误仍无提示，量小暂不动。

**部署**：2026-09-12 canvas_service.py SCP + docker restart，容器内确认新代码已加载；git 已推送（509146f6）。

## 2026-09-12 修复范本 AI 识别被单颗超长 key 判死（parse 阶段截断兜底）（已部署 2026-09-12）

**主题**：《福建省房屋建筑和市政基础设施工程标准施工招标文件专用本》上传后识别失败，detect_error=`非法 key: 'liability_for_refusing_to_replace_key_construction_management_personnel'（71 字符 > 64 上限）`——LLM 照中文长字段名直译出合法 snake_case 但超长的 key，`validate_placeholders` 终审判死**整次识别**，623 项正确建议全部丢弃。

**核心变更**（`rag/svr/template_fill/detector.py`）：
- `parse_detection_response` 内 key 截断兜底：超 64 字符截断到 `KEY_MAX_LEN`（新常量，与 validate 的 `[a-z][a-z0-9_]{0,63}` 对齐）；撞车去重后缀拼接时同步收缩基串保证含 `_2`/`_10` 总长仍 ≤64（旧逻辑 `key+"_2"` 对 64 字符 key 会溢出到 66，一并修复）
- DETECT_SYSTEM 规则 2 加约束：key 不超过 32 字符（降低截断概率）
- `validate_placeholders` 终审保持严格不变（手动提交路径仍拦非法输入）

**测试**：+3 对抗用例（生产同款 71 字符 key 截断且过终审 / 截断撞车去重不超限 / 恰好 64 字符 key 去重不溢出），模板三套件 254 全绿。

**遗留**：无。部署记录（2026-09-12）：detector.py SCP + docker restart，容器内冒烟（71→64 截断实测通过）；容器内重触发该范本识别 → done，623 个填写点全部合法（0 超长 0 低置信）；git 已推送（0a4e5999）。

## 2026-09-12 范本AI识别准确性与格式保真改造（已部署 2026-09-12）

**主题**：治理范本 AI 识别三类问题（① 标签被选为 anchor 变蓝 ② 跨 run 替换格式错乱 ③ 正文原文被选为 anchor 被覆盖）+ B端预览划选手动标记兜底。

**核心变更**：
- `rag/svr/template_fill/detector.py`：识别后处理三层防御——混合 anchor（"编号：＿＿＿"）收缩为纯留白后缀；标签型 anchor（冒号结尾）收缩到标签后留白、失败丢弃；实心 anchor 同行有留白则收缩（收缩成功也打低置信）、失败保留打 `low_confidence`；`_merge_detection` 撞车时收缩项回退原 anchor 保留（双标签同行不丢字段，`_orig_anchor` 内部字段不落库）；DETECT_SYSTEM prompt 同步强化
- `rag/svr/template_fill/docx_utils.py`：跨 run 替换从整段重写改为区间重写（`_replace_cross_run_in_place` + `_locate_run_span`）——首 run 保前缀接 `{{key}}`、尾 run 保后缀、中段清空、**区间外 run 格式完整保留**；`scan_from` 偏移扫描防 anchor⊂repl（如 name→{{name}}）重扫腐坏；中段空文本 run 跳过置空保住 w:fldChar/w:drawing 结构
- 前端：`TplPlaceholder.low_confidence?` 字段 + 填写点表格行琥珀警示底色；`detail.tsx` docx 预览划选文字 → 段落旁「标记为填写点」按钮一键加行（key 自动生成、readonly 屏蔽），预览接口本就透传 addr 无后端改动
- 测试 +16 个对抗用例（标签收缩/撞车回退/混合 anchor/跨 run 格式保留/anchor 子串/结构 run 保留等），`test_template_fill_utils.py` 108 用例 + API 路由 194 全绿

**遗留**：已知既有局限（审查备案）：单 run 快路径只替首个含 anchor 的 run；同行重复标签收缩取首个出现位置。部署记录（2026-09-12）：detector.py + docx_utils.py 成套 SCP + docker restart，容器内 import 冒烟通过；前端 dist 构建上传 + nginx reload 完成；git 已推送（3dac0d8b..0c7a1635，10 commits）。

**设计**：docs/superpowers/specs/2026-09-12-template-detect-accuracy-design.md | **计划**：docs/superpowers/plans/2026-09-12-template-detect-accuracy.md

## 2026-09-12 范本填写三连修：成稿标蓝 + 预览高亮失效 + 标签型默认值污染清理（已编码未部署）

**主题**：C端流程 demo01 实测三问题：① 实时预览无蓝色高亮占位符 ② 成稿 Word 填入值无蓝色标记 ③ 478 处占位只产 204 值且大量位置「看起来没填」。

**根因**（服务器 DB + 成稿 docx XML 实证）：
- ① 09-11 docx-preview 保真改造把预览文件从 render 工作副本改拉 original 原件——detector 生成的 `{{key}}` 只写入 render 副本，original 无占位符，applyDocxHighlight 永远扫不到高亮目标。
- ② renderer 从未实现标色（设计缺口非回归）。
- ③ detector LLM 识别把「编号：」「申请人：」「年 月 日」「（投标人名称）」等模板提示文字当 anchor，derive_default_from_anchor 派生成默认值 → D−C 条件执行原样回写成稿（视觉=没填）→ sediment 固化污染基线（523 槽实测 208 个污染默认值）。

**核心变更**：
- `web/src/hooks/use-template-fill-request.ts`：`useTemplateFillFile` 改拉 `kind=render`（唯一调用方 template-fill-live-preview.tsx，B端下载不受影响）
- `rag/svr/template_fill/renderer.py`：新增 `_colorize_placeholder_runs`——渲染前把 {{key}} 隔离成独立 run 并标蓝 0000FF（docxtpl 值继承占位 run rPr 成稿即蓝）；混合 run 深拷贝拆分只染占位段；含 w:br/w:drawing 的 run 整体跳过降级；`_set_run_color` 用 `CT_RPr.get_or_add_color` 按 OOXML schema sequence 插入（裸 append 乱序 XML 严格校验器会丢色）
- `rag/svr/template_fill/detector.py`：新增 `_is_template_skeleton` 三规则防御（冒号结尾标签/日期骨架无数字含全角下划线/括号提示含半角与「万元」后缀），derive_default 拒绝派生——代码层根治污染源头
- 服务器数据清理：`tpl_template_version` placeholders 清空 208 个垃圾默认值（477→269 有值，备份 /tmp/tpl_placeholders_backup_20260912_103619.json）
- 测试：`test_template_fill_utils.py` 新增 11 个对抗用例（三类拒绝+不误杀「2026年9月28日」「（含）税金额100万元」「____2026」；标蓝整run/混合run只染值/免序列化字节相等/w:br 降级/rPr 顺序+既有 sz 保留），项目 .venv 89 passed

**遗留**：① anchor 吃掉标签文字的存量问题：LLM 后续填真实值时会落在原标签位置（「编号：」消失），需另起识别质量整改；② `kind=render` 后端缺 fallback original（template_api.py:511，刚上传 draft 无 render 副本时返回文件不存在，实际风险低）；③ 半角括号提示拦截可能保守误杀「(…)」结尾纯括号现值（可接受）。

## 2026-09-12 修复流程 AI 面板刷新后无成稿卡：template_fill_events 超 TEXT 64KB 被截断致 JSON 损坏（已编码未部署）

**主题**：范本填写（523 填写点大范本）完成后流程 AI 面板只有文字总结，成稿卡（预览/下载/存为流程版本入口）整个消失。

**根因**（服务器 DB 实证，记录 7507b528）：`flow_ai_chat.template_fill_events` 为 MySQL `TEXT`（65535 字节上限），大范本事件序列（含大量中文填入值）实测存储 65533 字节被静默截断，JSON 损坏（Unterminated string @ char 50912）；刷新回放 `parseAndReplay` 解析失败返回 undefined → 成稿卡消失（`response` 文本独立存储不受影响）。

**核心变更**：
- `api/db/db_models.py`：新增 `MediumTextField`（field_type='MEDIUMTEXT'，16MB），`FlowAiChat.template_fill_events` 改用；`migrate_db` 加 `alter_db_column_type` 幂等迁移（存量 TEXT → MEDIUMTEXT）
- `web/src/hooks/template-fill-stream.ts`：新增 `parseTemplateFillEvents(raw)`——JSON.parse 失败时按引号/转义/括号深度状态扫描，截到最后一个完整闭合的事件元素补 `]` 挽救重试（只丢尾部残缺事件）
- `flow-ai-panel.tsx`：`parseAndReplay` 改用挽救解析
- 测试：`template-fill-stream.test.ts` 新增 6 个挽救用例（合法/畸形/字符串中段截断/嵌套对象截断/值含 `}` 引号状态/无完整元素），18 全绿；并用服务器真实截断记录实测：挽救出 21 个完整事件（selected/confirm/filling 全保住）

**遗留**：① 已截断的存量记录（7507b528）尾部事件（收尾产值批/filled/done）物理丢失不可恢复，回放停在中途；新记录在 MEDIUMTEXT 下完整。② 部署需成套：`db_models.py`（重启容器触发 migrate_db）+ 前端 build，一起上线；部署后确认迁移生效：
```sql
SELECT COLUMN_TYPE FROM information_schema.columns
WHERE table_name='flow_ai_chat' AND column_name='template_fill_events';
-- 预期 mediumtext（ALTER 失败仅打日志不阻断启动，需人工确认）
```

## 2026-09-11 修复范本「实时预览」看不到 AI 填入内容（默认值/param 产值不随事件下发）（未部署）

**主题**：用户点「实时预览」后正文里几乎看不到蓝色填入内容（523 槽范本实测仅 11 个值随事件到达）。

**根因**（生产 DB 事件序列 + 容器内 LLM 复现实证）：LLM 批次只覆盖 117 个 llm 槽，且无检索证据时按设计返回 null（仅 11 槽产出）；其余 ~476 槽由 param 直取/默认值兜底（D−C、missing→default）填充——这条路径**不经过 generate_values 批次回调，产值从不随 filling 事件下发**，预览里这些槽位永远停在虚线状态。成稿本身不受影响（渲染用完整 values）。

**核心变更**：
- `agent/component/template_fill.py`：`_fill_one` 在 `build_values` 之后、渲染之前把非空产值按 `_VALUES_PUSH_CHUNK=40` 槽/事件分批补推（filling 事件只带 `values`，不带 done/total，进度口径仍以 LLM 批次为准；前端合并幂等，与批次事件重叠无副作用）
- `web/src/hooks/template-fill-stream.ts`：reducer 对 filling 事件的 `done`/`total` 改为存在才覆盖，兼容无进度字段的补推事件
- 测试：后端 `test_template_fill_events.py` 新增 `test_values_backfill_pushed_before_render`（真实 _fill_one + 桩渲染/存储/沉淀，断言补推覆盖默认值兜底字段、空串不推、无 done/total），4 套件 164 单测全绿；前端新增 reducer 补推事件用例

**遗留**：LLM 无证据槽位（本例 106/117）依赖 KB 检索质量，检索不到就靠默认值兜底——实时预览的「逐批填入」节奏因此主要发生在收尾补推阶段（默认值集中到达），属设计取舍非缺陷。

## 2026-09-11 修复流程对话回放缺成稿卡（filled/done 范本事件未落库）（已部署，纯前端）

**主题**：接上条自动保存修复——记录虽已保存，但刷新回放永远停在「填写中 x/y」，看不到「可在上方预览或下载成稿」对应的成稿卡（下载/存为流程版本入口）。

**根因**：下载入口来自 `filled` 事件（`download` 字段），完成态来自 `done` 事件；两者与 `[DONE]` 在同一网络分帧到达（服务端实测间隔仅 200ms），RAF 未执行，`resetAnswerList` 批处理清空前 `answerList`/`eventBuffer` 对消费方不可见——落库的 `template_fill_events` 止于 `filling`（生产 DB 实证：8 事件无 filled/done）。

**核心变更**：
- `use-send-message.ts`：`send()` 用局部闭包数组收集本轮全量原始 SSE 事件，随返回值新增 `events?: any[]`
- `flow-ai-panel.tsx`：`await send()` 后按 `event === 'template_fill_progress'` 过滤重建 `templateFillEventsRef`，`filled.download` 与 `done` 一并落库，回放 reducer（template-fill-stream.ts）按既有逻辑还原成稿卡

**遗留**：该修复前保存的记录（如 22:36 那条测试记录）事件序列已缺失，回放仍无成稿卡；成稿文件本身在 `{tenant_id}-downloads` 桶，可从版本记录/AI 范本填写条目下载。

## 2026-09-11 修复流程 AI 对话刷新后记录丢失（自动保存静默失效）（已部署+验证，纯前端）

**主题**：流程 AI 面板一轮对话正常完成后 `POST /flow/<id>/ai-record` 从未发出（服务器 24h 日志零请求），刷新页面记录全部丢失。Playwright 生产复现 + SSE 抓包定位根因后修复。

**根因**：范本填写轮 agent 只产出**一条** `message` 事件（如"等待超时，已按 AI 预判字段继续填写。"84 字符），且与 `message_end`/`workflow_finished`/`[DONE]` 在同一网络分帧内到达；reader 循环在 microtask 中连续处理完毕，RAF 节流的 streamState flush **一次都没执行**；hook 收尾同步执行 `flushStreamState + setDone(true) + resetAnswerList()`，React 18 批处理下最终 `streamState.content=''` 落地——面板 `contentRef` 全程未填充，自动保存在 `if (!text) return` 处静默退出。

**核心变更**：
- `use-send-message.ts`：`send()` 在 `resetAnswerList()` 清空累积器**之前**捕获 `streamAccRef.current.content`，随返回值新增 `content?: string` 交付调用方（增量字段，其余 7 个消费方不受影响）
- `flow-ai-panel.tsx` `handleSend`：`await send()` 后用 `res.content` 兜底补写 `contentRef`（仅当其为空时），`setSending(false)` 触发的最终渲染使自动保存 effect 拿到非空文本正常 POST

**遗留**：用户在流式中途刷新/关闭页面仍会丢记录（未做中途快照持久化，当前修复覆盖"完整等到流结束"主场景）；本地 jest 配置依赖未安装的 `umi/test` 无法本机跑前端单测（环境既有问题）。

## 2026-09-11 文件审核 docx 保真渲染（批注功能全保留）+ 范本预览大文档性能优化（已部署，纯前端）

**主题**：① 文件审核（review-panel）只读路径 docx 改 docx-preview 渲染原始文件，Word 字号/表格/排版保真，AI 标注+手动批注锚定/批注栏/引线/兜底全部保留；② 范本实时预览大文档卡顿治理：占位符高亮由 innerHTML 快照重放改 span 映射增量更新 + 屏外页懒渲染。设计文档 `docs/superpowers/specs/2026-09-11-review-panel-docx-fidelity-design.md`。

**核心变更**：
- `docx-highlight.ts` 新增文本标注能力 `highlightDocxRanges`：去空白归一化全文匹配（与 findTextEndRect 同口径），anchor_start 偏移消歧重复文本，同 `<p>` 校验防跨块误删，从后往前插 `mark[data-anchor-key]`（bg color+22、下边框 2px color、cloneContents 保原空白）
- 性能：`applyDocxHighlight` 返回 key→span[] 映射，`updateDocxHighlight` 增量更新（values 变化零 DOM 重建，StrictMode 下 updater 双调用不再重复改 DOM）；`applyDocxPageLazy` 给分页 section 设 `content-visibility: auto` + `contain-intrinsic-size: 794px 1123px`，屏外页跳过布局绘制
- `review-panel.tsx` 只读三路分支：编辑态保持旧纸张视图（Lexical 模型不兼容）→ 保真（useFileBlob 拉 `GET /files/<id>` blob + renderAsync + mark 锚定，点击 mark 跳批注栏）→ 降级（渲染失败黄条提示回旧段落视图）；`activeRailItems` 过滤未锚定项落入既有兜底列表；批注栏/引线/统计/编号全部改用 activeRailItems
- 新 hook `use-file-blob.ts`（queryKey `['fileBlob', fileId]`，JSON 错误体检测）；`api.ts` 加 `getFileBlob` entry；后端零改动

**遗留**：保真模式下手动批注无段落索引（docx DOM 无 data-para-index），anchor_para 为空靠 anchor_text 文本匹配回锚（正常可命中，重复文本取首处）；文本标注起止跨 `<p>` 时跳过锚定落兜底列表（防块级结构破坏，设计约束）；B端范本库详情页仍未升级保真。

## 2026-09-11 范本预览 Word 格式保真渲染（docx-preview + 填入高亮保留）（未部署，纯前端）

**主题**：C端「查看范本 / 实时预览 / 查看填写内容」抽屉的 docx 分支由纯文本段落渲染改为 docx-preview 渲染原始 docx，字号/加粗/颜色/表格/页面排版保真还原；AI 填入实时高亮（蓝色值 + 虚线槽位）保留。设计文档 `docs/superpowers/specs/2026-09-11-template-preview-docx-fidelity-design.md`，实施计划 `docs/superpowers/plans/2026-09-11-template-preview-docx-fidelity.md`。

**核心变更**：
- 新依赖 `docx-preview@0.4.0`；新 hook `useTemplateFillFile`（`/template/fill/<id>/file?kind=original` 拉 blob，JSON 错误体检测照 downloadTemplateFillResult 口径）
- `template-fill-live-preview.tsx` docx 分支重写：`renderAsync` 渲染 → pristine innerHTML 快照；SSE filling values 变化时快照重放 + 高亮重涂（渲染失败/接口报错降级回原纯文本渲染 + 顶部黄色轻提示）
- 新模块 `docx-highlight.ts`：`applyDocxHighlight` 处理 Word 占位符跨 run 拆分——TreeWalker 收集文本节点拼接全文定位 `{{key}}`，Range 跨节点 deleteContents + insertNode 替换，从后往前处理保证偏移不失效；高亮 span 继承 Word 上下文字号（不再硬编码 text-xs）
- 部署：纯前端 build + SCP（后端零改动）；npm install 需联网

**遗留**：xlsx 分支维持旧渲染（docx-preview 不支持 xlsx）；B端范本库详情页（template-fill/detail.tsx）纯文本预览本次未升级（如需同款保真另行安排）；docx-preview 为 A4 固定页宽，半屏抽屉下横向滚动看全（未做缩放）。

## 2026-09-11 流程对话保存自治存储（流程/对话页解耦）（未部署，前后端须一起上线）

**主题**：流程 AI 对话与 c-chat 对话页彻底解耦，权威存储归流程。设计文档 `docs/superpowers/specs/2026-09-11-flow-chat-save-design.md`，实施计划 `docs/superpowers/plans/2026-09-11-flow-chat-save.md`。

**核心变更**：
- `flow_ai_chat` 加 `user_id` / `template_fill_events` 列，成为权威对话存储（migrate_db 幂等迁移 + 存量空 user_id 回填流程发起人）
- 新端点 `POST /flow/<id>/chat/session`：创建 `source='flow'` 影子会话（画布 DSL 运行时缓存，多轮续聊靠它，可随时清理不丢权威数据）
- c-chat 会话列表（`API4ConversationService.get_list/get_names`，`COALESCE(source,'') != 'flow'` 防 NULL 误滤）过滤影子会话；DELETE 会话接口对 `source='flow'` 返回 OPERATING_ERROR 拒删
- 前端 ensureSession 改走 flow 端点；sessionIdRef 只恢复本人（user_id 匹配）记录且适配 aiChats 异步到达；刷新回放范本进度改从 `flow_ai_chat.template_fill_events` 重放（JSON 字符串 parse 兜底，删除 agent 会话 fetch）；自动/手动保存附带原始事件序列
- 影子会话失效自愈：发送命中 "Session not found" / "does not belong"（路由层 HTTP 200 code≠0 信封）时清空 sessionIdRef，下次发送自动重建会话（设计 §8 承诺）
- 对话气泡 meta 行展示操作人昵称 chip（复用 nicknameMap）；存量「流程：xxx」会话由迁移打标 source='flow' 后从对话页签消失

**遗留**：agent_id 仍读 localStorage（未列入本次痛点）；`template_fill_events` 为 TEXT 列（64KB 上限，超长事件序列会被 MySQL 截断/报错，后续可评估 MEDIUMTEXT）；`sdk/session.py` SDK 会话列表与 `stats()` 统计未过滤 source='flow'（本次范围外，影子会话会轻微放大 PV/统计数字）；「路由校验通过后、completion 执行前会话被删」的窄竞态表现为 SSE 中断无结构化报错，前端不覆盖（窗口极窄，下一轮发送自愈）；部署须成套 SCP 并执行存量打标迁移（见设计文档 §7/§10），**顺序：先后端 5 文件 + 重启（迁移顺带完成），再上前端 build，切勿前端先行**。

**冒烟补充**（在计划 Task 9 六步之外）：手删一条 flow 影子会话行 → 追问报一次 "Session not found!" → 再发送自动重建恢复正常；大范本长填写值跑一轮自动保存观察 TEXT 64KB 是否触发；纯文本轮后刷新 → 进度回放到更早记录的范本进度。

## 2026-09-11 文件审核弹窗改为常驻右抽屉（与范本实时预览同款）（未部署）

**主题**：文件审核/成稿预览/流程版本审核的 Sheet 弹窗（带遮罩、挡对话）改造为与「查看范本」一致的右侧常驻抽屉。

**核心变更**：
- `review-panel.tsx`：Sheet → fixed 右抽屉（无遮罩、z-40、`w-[min(56rem,85vw)]`、animate-in 滑入、Esc 关闭、右上角关闭按钮两种模式均渲染、**open 门控**：非 inline 且 open=false 时 return null——使用点均常挂载+open 属性切换，缺门控会常显）；内容区高度统一 flex-1，删除废弃的 reviewDrawerIn/Out keyframes
- 腾位联动三层打通：c-chat 对话区 paddingRight（范本预览/审核类抽屉统一 56rem，带过渡）+ 侧栏自动收起恢复；flow 左流程列表收起；flow 右版本记录栏收起
- 接线：flow-detail `onReviewOpenChange`（viewOpen 上报+卸载兜底）→ flow-panel → index `flowReviewOpen`，与 `previewDoc`、非 chat 视图 reviewMode 合流为 `reviewSheetOpen`
- chat 视图的 inline 审阅列（55vw）保持不变
- 修正记录：首版漏 open 门控（抽屉常显）+ 宽度 75rem/z-50 与范本预览不一致，已对齐为与范本实时预览完全同款
- 控件细节对齐范本预览（第二轮）：头部 px-4/边框 #E5E5E5、关闭按钮同款类名（hover 变黑字）、正文区 `min-h-0 flex-1 overflow-x-hidden px-6`、抽屉容器去掉多余的 overflow-hidden
- 平分布局（第三轮）：抽屉宽度与主区腾位从固定 `min(56rem,85vw)` 改为各占 50%——抽屉 `w-1/2`、主区 `paddingRight: 50%`（侧栏联动收起为 w-0 后主区即全视口宽，正好平分）；范本预览与审核类抽屉同步改
- flow AI 面板「文件审核」抽屉接入腾位联动：flow-ai-panel 新增 `onReviewOpenChange` 上报 reviewMode → flow-detail 合并 viewOpen/aiReviewOpen 上报并收起版本时间线 → flow-panel 左列表收起 → index 主区 paddingRight + 侧栏联动（此前该抽屉打开时直接盖住时间线/列表，不腾位）

## 2026-09-11 持久化「未生效」根因修复：断连/取消整轮丢失 → partial 落盘（未部署）

**主题**：用户反馈范本填写进度持久化没生效。排查结论：收集/落库代码链路正确且已部署，但持久化只在流自然走完后执行——用户测试时刷新页面，SSE 断连触发 GeneratorExit 杀掉整个 completion 生成器，落库逻辑被整体跳过，整轮（含用户消息）全部丢失（服务器日志三次 `Canvas batch cancelled due to client disconnect`，DB 中对应会话 round=0 仅剩欢迎语）。

**核心变更**（仅 `canvas_service.py`）：落库逻辑提取为 `_persist_messages(tag)`，三条路径统一收口：`final`（自然走完，原行为）、`partial-disconnect`（GeneratorExit 后落盘部分文本+已捕获进度事件再 re-raise）、`partial-canceled`（显式取消也落盘）。前端无需改动——replay 部分事件即还原部分进度卡片。

**遗留**：断连后画布仍会取消运行（canvas.run 原行为），刷新回看的是「已产生的部分进度」，任务不会后台续跑；若要断点续跑需另立后台执行架构。

## 2026-09-11 范本填写进度持久化 + 预览入口全程可见（未部署）

**主题**：范本填写进度卡片/填入值刷新即丢（仅流式内存态）+ 预览入口只在 filling 态显示——刷新后无法回看填了什么，filled 后也找不到入口。

**核心变更**：
- 后端 `canvas_service.py`：`template_fill_progress` 事件随流收集，结束后以原始事件序列挂到 assistant 消息 `data.templateFillEvents`（上限 200 条防刷屏）；归约逻辑唯一收敛在前端，后端零重复
- 前端 `template-fill-stream.ts`：新增 `replayTemplateFillEvents` 纯函数（重放历史事件还原 ITemplateFillState）
- c-chat `index.tsx`：loadSessionMessages 重放恢复 `msg.templateFill`；渲染条件 `streaming || msg.templateFill`（流式用实时态、历史用恢复态）；done 时把最终快照回填到最后一条 assistant 消息（镜像 structuredOutputRef 模式，修「流结束瞬间卡片消失」）
- `template-fill-progress.tsx`：selected 行加「查看范本」、filled 行加「查看填写内容」（复用实时预览抽屉回看蓝色填入值）
- flow `flow-ai-panel.tsx`：挂载时从 agent 会话最后一条 assistant 消息重放恢复 lastTemplateFill，成稿条与「存为流程版本」跨刷新保留

**遗留**：历史事件重放后 pendingConfirm 确认卡片也会恢复（nonce 已持久化，提交仍有效——属预期行为）；Jest 配置在本机无法解析 umi/test（预存环境问题），前端归约逻辑未跑单测，靠 tsc 比对（改动零新增错误）。

## 2026-09-11 B端范本 AI 识别精度三连修：LLM 分块识别 + 正则补漏报 + 手动占位符直通（未部署）

**主题**：标准施工招标范本（福建省 2022 版通用本/专用本实测）识别不精准的根因修复。

**根因（容器内真实文件实测）**：
- 通用本 274 候选中 ~95% 是「下列情形之一：」类 prose 误报，真填写点被淹没
- detect_fill_points 全部候选单次 LLM 调用无分块 → 大文档输出截断 + 注意力稀释（主因）
- FILL_HINT_RE 漏报三类真实填写点：冒号+留白+后续文字（盖章行/日期行）、「　年　月　日」空白日期、行内长空白占位

**核心变更**：
- `detector._detect_chunked`：候选按 60 行/块、并发 3 分块调用 LLM 再合并；单块失败返回部分结果（全部失败才抛错）
- `detector.extract_explicit_placeholders` + `_merge_detection`：模板正文手写 `{{snake_key}}` 直通识别（不经 LLM、确定性），(addr,anchor) 去重 + 跨源 key 加后缀；渲染链路 anchor→{{key}} 替换幂等，docxtpl 直接渲染；docx/xlsx 候选提取无条件纳入含占位符段落
- `FILL_HINT_RE` 增补三模式：`[:：][空白]{3,}\S`、`[空白]{2,}年[空白]*月[空白]*日`（真实日期不误报）、`\S[空白]{6,}\S`
- DETECT_SYSTEM 增规则 5：prose 引导冒号句（"包括以下内容："）不是填写点

**效果（专用本实测）**：候选 237→665，漏报的盖章/日期/行内占位全部纳入且样本全为真填写点；通用本 274→564。测试 195 passed。

**遗留**：候选总量上升（LLM 调用次数 10 块/大范本），识别耗时相应增加；prose 冒号噪声仍靠 LLM 规则 5 排除。

## 2026-09-11 范本填写「实时影子预览」：打开正文全览，LLM 产值逐槽实时填入高亮（已部署）

**主题**：填写运行期间用户可点「实时预览」打开模板正文抽屉，LLM 每产出一批字段值即填入对应占位符槽位并蓝色高亮——纯前端展示层合成（模板正文段落 + 已产值 values），交付管线不动，最终成稿仍以后端 docxtpl/openpyxl 渲染为准。

**核心变更**：
- 后端：`executor.generate_values` 的 on_progress 回调扩展为 `(done, total, new_values)`，new_values 为该批已过 `_apply_constraints` 约束闸的产出值（与最终返回同口径，回调异常仅 try/except 吞掉）；画布 `TemplateFill._on_gen_progress` 把 values 随 `filling` SSE 事件下发
- 前端：`template-fill-stream.ts` 归约器 filling 分支逐批合并 `values`（换引用保 memo 感知）；新建 `template-fill-live-preview.tsx` 抽屉组件（useTemplateFillPreview 拉正文，`{{lower_snake_key}}` 拆槽，已填值蓝字 #1a66fb + 浅蓝底高亮，未填虚线槽位；docx 段落流 / xlsx 按 sheet 分组）；`template-fill-progress.tsx` filling 行加「实时预览」按钮（存 template_id 而非对象快照，values 更新实时刷新）
- 事件量评估：产值批次每批 ~10 字段，SSE 压力可忽略

**遗留**：xlsx 实时预览从简（coord+text 平铺）；实时视图与最终渲染文件可能存在极小延迟差（事件管道 vs 渲染完成）。

## 2026-09-11 SSE 过滤器吞掉心跳/范本进度事件 → 前端「正在思考」永久卡死（已部署）

**主题**：范本书写长运行（~9 分钟）期间会话 SSE 流零字节，被 NAT/代理静默掐断，浏览器 reader 永远等不到关闭。

**根因**：`agent_api.py _iter_session_completion_events` 白名单（message/message_end/workflow_*/node_*）把 `heartbeat` 和 `template_fill_progress` 全部过滤（日志实锤 filtered=41 = 23 进度 + 18 心跳）。范本填写运行期间 SSE 连接除首尾 node 事件外全程无字节 → 中间层按空闲连接掐断 → 后端关闭信号到不了浏览器 → `useSendMessageBySSE` 的 `done` 永不置 true → flow-detail「正在思考…▌」卡死。

**核心变更**：
- 白名单加入 `heartbeat`（15s keepalive，根治空闲掐断）+ `template_fill_progress`（范本进度卡片 + confirm_pending 确认卡片依赖同一事件名，此前在该路径从未到达前端——P2 确认功能实际不可用，本次一并修复）
- 同日顺带：`docker/entrypoint.sh` 加 docxtpl 自愈守卫（容器 recreate 丢失容器内手工安装的 docxtpl，范本渲染报 No module named 'docxtpl'）

**遗留**：DeepSeek API 产值延迟（~5.5 分钟）为外部瓶颈；前端画布运行失败时 content length=0 无错误提示的 UX 缺陷仍未修。

## 2026-09-10 范本默认值基线 P2：变化字段确认填写（后端+前端，未部署）

**主题**：重填场景 LLM 负担从几百字段降到 ~100 变化字段。

**核心变更**：
- `executor.predict_changed_fields`：分块（200/块）LLM 预判疑似变化字段，失败/非法输出回退空集，GenerateCancelled 穿透
- 画布 TemplateFill 暂停确认：`_confirm_changed_fields` 推 `confirm_pending` SSE 事件（含 confirm_nonce）→ 轮询 Redis `tpl_fill:confirm:{task_id}:{nonce}`（600s 超时按预判继续）→ `POST /template/fill/confirm` 端点写键唤醒（nonce 运行级隔离防跨运行误读，Redis 写失败检测）
- 条件执行：D−C 字段免检索免 LLM 直用默认值；用户直填值最高优先（空串=清空）+ 沉淀 override；检索/产值/进度三处同步收窄
- 前端确认卡片（C端对话 + flow AI 面板共用）：勾选变化字段 + 直填值，SSE confirm_pending 归约、轮次 key 隔离、提交状态回写流式态、超时/提交竞态降级文案

**最终整体审查修复**（268→270 passed）：
- ★ 修复条件执行致命缺陷：D−C 字段被排除在 `llm_placeholders` 后不进 `generate_values`，也就不进返回的 `missing` 集，`_merge_default_values` 只填 missing → 未变化字段渲染为空白而非默认值，免检索免 LLM 收益实际失效；修复为把 D−C 键显式纳入 missing（`agent/component/template_fill.py`），并补 2 个组件级 decision 消费侧测试（检索收窄口径/直填优先/空串清空/沉淀 override_keys）
- `predict_changed_fields` 脏 item 防御补 isinstance（非 dict 项不再 AttributeError）
- 确认卡片提交禁用条件补 `!pending.task_id`（nonce/task_id 任一缺失即确认通道未就绪）

**遗留**：B端异步填写任务（无人在场）不接入暂停确认，保持 P1 fallback 行为；未部署（后端 5 文件成套 SCP + 前端 build，前后端需一起上线——confirm 端点要求 nonce，旧前端调用会被拒）。审查已确认可接受的已知限制：①用户直填空串（清空）当轮生效，但沉淀层空值不写回，下一轮该字段仍按旧默认值填回（跨轮清空需在 B端默认值列手动清）；②confirm 端点无归属校验（屏障为 32-hex nonce 保密 + 登录态）；③确认等待期刷新页面 = 整个运行作废（与既有进度卡同为内存态，但暂停窗口最长 10 分钟放大了误刷新代价）；④Redis 宕机期间确认轮询空转至 600s 超时兜底（降级延迟最长 10 分钟，功能最终正确）。

## 2026-09-10 范本默认值基线 P1（后端+前端，未部署）

**主题**：模板填写大范本（几百填写点）漏填根治与基线沉淀。

**核心变更**：
- anchor 派生默认值（`detector.derive_default_from_anchor`，识别时零 LLM 成本提取已填现值，留空标记/控制字符过滤）
- `save_placeholders` 默认值合并（`_merge_defaults`：显式携带 > 按 key 继承 > anchor 派生；显式清空态跨保存持久）
- LLM 提取不到时 fallback 默认值（`executor._merge_default_values`，过 `_apply_constraints` 约束闸）+ 产值 prompt 注入默认值参考（`_default_hint`）
- 成稿后产值自动沉淀为默认值（`sediment_defaults`，manual 保护、空值不抹历史、失败仅告警不影响交付）
- B端默认值编辑（`PUT /template/fill/<id>/defaults` + 范本详情默认值列行内编辑/来源徽标/单 key 即时保存，含 IME 合成态保护）

**遗留**：P2（变化字段预判 + 对话中暂停确认 + 条件执行）另行实施；未部署。

## 2026-09-10 流程页签：文件审核入口上移到顶部按钮行（前端，未部署）

**主题**：C端流程详情页「文件审核」按钮从 AI 面板输入框标题行（ChatInputBox leftSlot）上移到顶部状态条按钮行，与「作废 / 退回上一节点 / 提交下一节点 / 上传修改版」同排，样式统一用 Button size="sm"。

**核心变更**：
- `flow-ai-panel.tsx`：标题行删除文件审核按钮；新增导出 `FlowReviewControl` 类型与 `onReviewControlChange` prop，面板内部 effect 将 `{ visible, active, toggle }` 实时上报父级（卸载时上报 null 清空）；顺带清理不再使用的 FileText import
- `flow-detail.tsx`：新增 `reviewCtl` state 接收上报，顶部按钮行首位渲染文件审核按钮（醒目样式：主蓝填充+投影+半粗字重；审核中变 outline「关闭审核」）；FlowAiPanel 传入 `onReviewControlChange={setReviewCtl}`
- 作废按钮从详情页顶栏移到左侧流程列表卡片（flow-panel.tsx）：发起人 + 非终态（未归档/未作废）时在卡片状态行显示红色「作废」文字按钮，stopPropagation 不触发卡片选中，确认弹窗与原详情页一致；成功后刷新 flow-list / todo 角标 / flow-detail；`flow-detail.tsx` 顶栏删除作废按钮与 handleCancel（cancelFlow import 一并清理）

**遗留**：未部署（前端改动，随下次 build 一起发）；可见性逻辑不变（有版本文件或手动上传文件时显示）。

## 2026-09-09 流程页签：全部流程管理页超管专属 + 节点状态独立配色（后端+前端，未部署）

**主题**：①原「已结束流程」管理视图改为仅超级管理员可见的「全部流程」——可看系统内所有用户的全部流程；②流程 6 状态节点独立配色（含文字颜色），覆盖详情页时间线步骤条与左侧列表状态标签。

**核心变更**：
- 后端：`FlowInstanceService.list_all(status)`（不做参与人过滤，deleted/running/archived/cancelled 四分支）；`flow_app.py` list_flows 新增 `scope=admin`（非超管 403，`_ADMIN_STATUS` 校验，其他 scope 行为零变化）；新增 `_require_viewer`（参与人或超管）仅用于 3 个 GET 读端点（详情/版本下载/版本内容）——超管可查看任意未参与流程，写端点仍走 participant/owner 不放大写权限
- 前端：`flow-manage.tsx` 重写为「全部流程」视图（删除三视角分段改状态筛选 全部/进行中/已归档/已作废/回收站，表格加发起人列，操作按钮改 `initiator_id === meId` 门槛，isError 透传后端 message）；`flow-panel.tsx` Archive 入口包 `usePermission().isSuperuser`；`FlowFinishedFilter` 删 'finished' 改 running/archived/cancelled/deleted，`FlowScope` 加 'admin'
- 配色：`flow-utils.ts` 四个映射（BADGE/DOT/TEXT_COLOR/STATUS_COLOR）每状态独立色系（发起蓝 #1a66fb / 领导紫 #7C3AED / 处理青 #0E9488 / 汇总橙 #C7810A / 归档绿 #188A52 / 作废红 #E5484D）；`flow-detail.tsx` FlowStepper 重写——节点圈/边框/连线/呼吸光圈/文字全用各节点主色内联 style（Tailwind JIT 不支持运行时拼类名），cancelled 全灰

**审查**：代码审查子代理发现并修复 Critical——超管查看未参与流程详情 403（读端点换 `_require_viewer`，写端点逐个确认未放宽）；另修管理页错误提示吞后端 message。复审结论 READY。

**遗留**：未部署（与「移除团队权限隔离」15 后端文件合并成套：追加 flow_service.py + flow_app.py，前端须 build）；列表无分页（全量返回，存量量小接受）；非参与人超管在详情页写操作显示「无权访问该流程」为预期。

## 2026-09-09 移除团队权限隔离：读全局放开 + 写 owner-only（后端，未部署）

**主题**：团队（user_tenant）机制未被用作权限模型反而造成资源互相不可见。去掉团队隔离——任何账号的知识库/智能体/对话助手/搜索应用/文件全部互相可见（含 permission='me' 的 KB），写操作（编辑/删除/移动/上传）仅限资源所有者；后续写权限管控统一由 /permission 页面 RBAC 承接。设计 `docs/superpowers/specs/2026-09-09-remove-team-permission-design.md`（方案A 显式全局化），实施计划 `docs/superpowers/plans/2026-09-09-remove-team-permission.md`，5 任务子代理驱动执行（每任务 spec 合规审查+质量审查双循环）。

**核心变更**（commits `c0af2d85`→`672281d6`，15 个后端 py + 1 个测试）：
- Service 层：`get_by_tenant_ids`/`get_list` 系列签名保留，租户参数传 `None`=全局（跳过租户与 permission='team' 过滤；`is not None` 判断避开 peewee `in_([])` 恒假）；`KnowledgebaseService`/`DocumentService`/`UserCanvasService` 的 `accessible` 降为存在性检查（status=VALID 即通过），新增对称的 `owned()`（tenant_id==user_id + VALID）作写门禁
- API 层：KB/智能体/对话助手/搜索应用列表全局可见；18 处写端点（chunk 增删改 4、文档删除/解析/元数据 5、知识图谱/索引/标签/嵌入 6、摄取门禁 1、SDK 解析触发/停止 2）accessible→owned；`check_kb_team_permission`/`check_file_team_permission` 函数体改纯 owner 判断（上传/文件删除/移动仍 owner-only）；文件下载 `get_file_content` 移除团队校验（读全局）；补 spec 3.2 漏盘点——agent reset 加 owner 硬校验（防覆写他人 DSL）、agent 会话删除校验「会话创建者或 agent 所有者」+ conv.dialog_id 归属绑定（封跨 agent 越权删除）、rerun_agent 补 DocumentService.owned（存量洞）、批量删对话 chat_id 兼容分支补 _ensure_owned_chat（存量洞）；清除 7 处读端点残留 joined-tenants 拦截（dataset search、chat/search 详情、SDK retrieval/searchbot、list_tags、list_datasets 按 id/name 查询）
- 有意不动：user_tenant 表与团队邀请/B端团队成员页（死路径）、前端全部页面、协作功能、统计/系统 token/鉴权路径、「我的智能体」计数、accessible4deletion

**测试**：新增 `test/test_check_team_permission.py` 15 用例（owner-only 双分支/空值/model+dict）全过；全量 pytest 342 passed（2 failed 为 8 月 RBAC 既有破损 permission_utils，与本改造无关）；14 文件 py_compile 零错误；ruff 与基线净零。对抗性 grep 审计：`get_joined_tenants_by_user_id` 零调用方、`UserTenantService` 残留全部在白名单（统计/token/协作/团队管理）、写端点 owned 全在场、无前端/db_models 越界改动。审查修复记录：model 分支桩类型分裂、search_all tenant_ids NameError（计划遗漏）、DocumentService.owned 补 KB VALID 对称、get_chat 软删回归。

**遗留**：未部署——**必须成套 SCP 全部 15 个后端文件后重启**（中间态：Task2/3 合并后写端点门禁曾短暂为「存在即可写」，列表传 [] 返回空，单独部署部分文件会出现行为不一致）；部署后需两账号交叉验证（B 可见/可检索/可下载 A 的资源，B 改/删/传 A 的资源被拒）；chunk 读端点（chunk_api.py list）索引名仍用请求者租户构造，非 owner 检索他人 KB chunk 视图为空（存量问题非回归，主检索链路 search/search_all 已按 KB owner 修复，后续迭代统一）；SDK session `_retrieval` 逐 KB 查询为 N+1（量级小未优化）；`get_all_kb_by_tenant_ids` 现零调用方（死代码保留）。

## 2026-09-09 流程页签：已结束流程维护页（后端+前端，未部署）

**主题**：C端流程页签新增「已结束流程」管理视图，发起人可统一维护走完的流程（查看/软删除/再次发起/重新激活），常规列表只留进行中。

**核心变更**：
- 数据模型：`flow_instance` 新增 `deleted`（默认0，索引）/`deleted_time` 软删字段（db_models.py + migrate_db 幂等迁移，存量行默认值安全）
- 列表查询：`FlowInstanceService.list_for_user` 加 `status` 参数（finished/archived/cancelled/deleted 四值，deleted 只看本人软删）；常规 scope（todo/initiated/joined/all）剔除终态与软删行，todo 角标轮询走同一查询自动干净
- 新端点（flow_app.py，均 @login_required 仅发起人）：`POST /flow/<id>/soft-delete`（仅终态；乐观锁含终态条件，防并发 reactivate 后误删进行中流程）、`/restore`（幂等）、`/reactivate`（校验领导/处理人账号存活，状态回 initiator，current_version_id 不变，notify 通知 leader+handler）；全部乐观锁前置状态条件更新，冲突报「流程状态已变化」
- 前端：新建 `flow-manage.tsx` 管理视图（视角切换/四态筛选/表格操作/详情整区切换复用 FlowDetail/再次发起预填含 doc-docx 版本自动下载转 File）；flow-panel 双视图接入（顶栏 Archive 入口）；`CreateFlowDialog` 抽独立组件 `create-flow-dialog.tsx` 支持 `initial` 预填（含已选文件展示条与重选同文件修复）；flow-service 补 status 参数与 3 个动作 API
- 设计文档 `docs/superpowers/specs/2026-09-09-flow-finished-manage-design.md`；实施计划 `docs/superpowers/plans/2026-09-09-flow-finished-manage.md`

**测试**：后端纯逻辑单测 35 passed（含 check_terminal_action 权限/终态/软删态/未知 action 7 项）；ruff 新增错误均为文件既有模式延续；前端 tsc flow 文件零错误。子代理两阶段审查 5 个批次：批次3 修复 soft_delete 乐观锁缺终态条件竞态（commit `2f4a113d`）、批次4 补文件展示条（`7dc9f979`）+ 移除后重选同文件丢失（`c44efcee`）、批次5 修再次发起 busy 锁乱序竞态 + 详情态错误条遮蔽（`c66586d1`）。

**遗留**：未部署（成套 SCP：db_models.py + flow_service.py + flow_app.py + 前端 build；后端三文件须整套部署否则常规列表过滤与端点不一致）；彻底删除入口维持 FlowDetail 内原硬删除（仅已作废）不变；DB 写入路径（软删/恢复/重新激活乐观锁）待手动冒烟。

## 2026-09-09 范本填写取消链路补全 + 检索并发限流（后端+前端，未部署）

**主题**：2026-09-09 流程页签实测暴露三个缺口收口——百级填写点范本填写时检索并发 6 路 × 大 KNN 打满磁盘（load 10.86、iowait 7-8%、单查询 22s→244s），用户点「停止」只能 abort 本地 SSE 无法取消服务端，最终靠重启容器收场。

**核心变更**：
- executor 层（commit `af6866f9`+`16822d16`）：新增 `GenerateCancelled` 异常；`generate_values`/`retrieve_all_shared`/`_retrieve_all` 均加可选 `should_cancel` 回调（检查点：`_one` 获信号量后 + as_completed 收集循环每轮）与 `sem` 信号量注入；探针异常防御式处理（视为未取消，避免 `_retrieve_all` 在 `return_exceptions=True` 下静默吞取消）；`should_cancel=None` 时 B 端路径零变化
- 组件接线（commit `7bccb152`+`5276570b`）：`_invoke_async` 传取消探针（`check_if_canceled`）+ 共享检索信号量；检索/产值阶段取消统一转推 `cancelled` 事件（检索阶段就地消化而非转抛——`_FillCancelled` 无参 `str()==""` 会被 base.py 吞成空 `_ERROR` 静默成功，与原 bug 同形）；`TEMPLATE_FILL_RETRIEVAL_CONCURRENCY` env 可调默认 2（坏值防御：非数字降级、0/负数钳 1 防 Semaphore(0) 挂死）；B 端检索保持默认 6 不变
- 取消端点（commit `f3d45b82`+`2b7817da`）：`task_service.cancel_task()`（写 Redis `{task_id}-cancel`，与 `has_canceled` 同键）+ `POST /api/v1/agents/tasks/<task_id>/cancel`（@login_required，幂等；Redis 写失败返回错误）
- 前端（commit `cb2db289`+`4770f584`）：`use-send-message.ts` SSE envelope 捕获 `task_id` 存 `taskIdRef`（每次 send 重置、流正常结束清空防幽灵请求）；`stopOutputMessage` fire-and-forget POST cancel（getAuthorization 头，失败不阻断本地停止）后照旧 abort；c-chat 与 flow-ai-panel 共用即同时生效

**测试**：后端 166 passed（executor 47/events 9/component 23/canvas drain 3/flow version source 6/tool/utils/cancel 端点 5 等）；前端 jest 7 passed（template-fill-stream 归约）+ tsc/eslint 改动文件零新增。审查修复循环：Task 1 探针防御、Task 2 检索阶段取消逸出（C1）+ env 坏值（I1）、Task 3 Redis 写失败伪幂等、Task 4 幽灵 cancel 请求。

**遗留**：待部署（4 后端文件 SCP + 重启 + 前端 build 部署）；端到端验证点停止→服务器日志 "has been canceled" 且检索停止；取消键无 TTL（与既有 cancel_all_task_of 一致，canvas.run 启动时 clear 闭环）；取消在途 LLM 调用需等当前批次完成（asyncio 语义，设计非目标）。

## 2026-09-08 模板填写进度流式 + 流程页签适配（后端+前端，已部署 2026-09-09 并通过端到端冒烟：content 端点真实 JWT 返回非空正文）

**主题**：TemplateFill 画布节点全程零反馈 → 5 类进度事件实时渲染（C端对话 + 流程 AI 面板共用）；流程场景版本文本轻量注入 + 成稿一键落流程版本时间线。

**核心变更**：
- 后端：`generate_values` 批次进度回调 `on_progress(done,total)`（done/total 为 LLM 产值槽位，param 直取槽不计数）；TemplateFill 组件 `_event_queue` 推 selected/filling/filled/failed/done|cancelled（filled 即时下载条含 url/name，单范本失败不中断；取消为检查点式，计划批准取舍）；canvas drain 泛化（任意带 `_event_queue` 的组件，FanOut 行为不变）；flow 新端点 `GET /flow/<id>/version/<vid>/content`（三角色可读、20000 字截断、解析失败回空串）+ `POST /flow/<id>/version` 支持 `source=ai_template_fill`；`FileService.parse` 支持 tenant_id 显式传入短路（Quart executor 线程无请求上下文，current_user LocalProxy 陷阱）
- 前端：`template-fill-stream.ts` 事件归约纯函数（每事件浅拷贝换引用 + finished 终态防御）+ `use-send-message.ts` SSE 分支；`template-fill-progress.tsx` 共用进度卡片（c-chat 流式气泡 / flow 对话区）；FlowAiPanel 附带版本改走轻量文本通道（**审阅模式排除**，仍走整份 docx 上传保证 ReviewPanel 段落锚点一致；轻通道失败回退整份上传）；flow 成稿条「存为流程版本」按钮（blob→uploadFlowVersion→时间线刷新，自动保存成功后成稿条仍保留可操作）；版本时间线来源三分支（人工上传/AI 产出/AI 范本填写）
- 设计文档：docs/superpowers/specs/2026-09-08-template-fill-flow-design.md；实施计划：docs/superpowers/plans/2026-09-08-template-fill-flow.md

**测试**：后端 124 passed（executor 21/events 5/canvas drain 3/flow version source 6/tool/utils）；前端 jest 7 passed（归约模块，用 .scratch/jest.template-fill.config.js 临时配置，存量 jest.config.ts 损坏引用未安装的 umi/test）；tsc/eslint 改动文件零新增。口径说明：设计 §5 的「存版本失败前端 toast」实现为按钮内联错误态（失败重试）+ console.warn，功能等价且带重试入口。

**遗留**：容器内端到端冒烟（SSE 事件到达 + content 端点真实 JWT 实测非空 + 成稿落版本）待部署后验证

## 2026-09-08 ES 检索重试冲突根治第二例（track_total_hits）+ 索引名去重消 KNN 4 倍放大 + KB 3b4f619c 索引瘦身（保留近1个月）（后端，已部署）

**主题**：用户「再看看」复查服务器——load 12.55、iowait 88~93%、磁盘读 ~180MB/s。ES hot threads 实证 6+ 个 search 线程 100% 阻塞在 HNSW 向量检索 off-heap 读盘（`OffHeapFloatVectorValues`）：17.9GB 索引（78 万 chunk）+ ES 堆仅 2GB，向量无法常驻页缓存，每次 KNN 现读盘。两个叠加的放大因素：① **track_total_hits 重试冲突**（与上文 timeout 同族 bug）——用户画布范本填写检索（`canvas:TemplateFill`，k=1024/num_candidates=2048 大查询）超时重试时 ES 9.x 客户端 kwarg 原地合并进 body 导致 `Received multiple values for 'track_total_hits'`，重试必然失败；② **索引名 4 倍重复**——同租户 4 个 KB 走 `index_name(tenant_id)` 返回同一索引，`index_names` 未去重，ES 对同一索引建 4 个搜索上下文，KNN 开销 4 倍放大。修复（commit `ce55fef1`）：`_es_search_once` 把 `track_total_hits` 写进 body 不再走 kwarg；`_source=True` kwarg 一并移除（ES 默认值，与 body 内字段列表潜在冲突）；入口 `dict.fromkeys` 去重保序 index_names。

**索引瘦身（用户指示 KB 3b4f619c 保留近1个月）**：先摸底后删——KB「其他」34878 文档/52.9 万 chunk（token 1.39 亿），30 天前 16606 文档/~21.1 万 chunk 待删。执行脚本走官方 `DocumentService.remove_document` 全路径（DB 行+KB 计数回写+取消任务+删任务行+chunk 图+缩略图+ES chunks+元数据+图谱清理），补 MinIO 原文件 + File2Document/File 孤儿行清理（禁裸 SQL 防孤儿）。**16606 文档删除完成、0 错误、92 分钟**（前段 15 文档/s，后段大文档 delete_by_query 变重降至 ~1.3/s），KB 剩 18273 文档/31.8 万 chunk。删除后 ES 磁盘虚高（17.9→33.8GB，tombstone 未合并），随即 `_forcemerge?max_num_segments=1` 后台合并回收。

**测试**：`test/test_es_conn_search_once.py` 扩到 6 用例（新增 track_total_hits 进 body 无 kwarg、无 _source kwarg、index_names 去重保序；重试幂等断言扩到全部参数），11/11 全绿（含 kb_uploader 5 例）；ruff 与基线一致（39=39）。

**部署**：es_conn.py SCP + 容器重启，md5 一致、healthz 200；服务器脚本已清理。

**遗留**：平台级容量瓶颈仍在——17.9GB 索引 vs ES 堆 2GB/机器 4核15GB，向量检索本质是磁盘速度；已向用户给出升级建议（8核32GB 起步/16核64GB 一步到位 + 按时间清理过期 KB chunk 控制索引体积 + 必要时 ES 独立部署）；forcemerge 完成后需复核最终 store.size；KB 按月清理目前为一次性脚本（`.scratch/_kb_slim_delete.py`），如需常态化可做成定时任务。

## 2026-09-08 KBUploader PDF 页数解析上限（>50页只上传不排队）+ 僵尸解析任务清理（后端，已部署）

**主题**：服务器负载排查（load 峰值 7.46/4核）定位为采集批量入库触发 115 篇文档解析、task 队列积压 110 自愈后，用户发现既有 5MiB 体积上限（`kb_uploader.py PARSE_SIZE_LIMIT`）挡不住解析成本——文件大小与解析成本不相关：1.3MB 高压缩文本型招标 PDF 可达 ~96 页，按 12 页/分片跑 DeepDOC 布局识别（CPU 密集），多个此类文件即占满共享 task 队列。新增 `PARSE_PAGE_LIMIT=50`：`_upload_blob` 对 .pdf 用 `PdfParser.total_page_number`（pdfplumber）数页数，超限照常上传、不排队解析（用户可在 KB UI 手动触发）；页数统计失败 fail-open（None 照常排队，与 `queue_tasks` 的 None→0 语义一致）；非 PDF 文件不检查；5MiB 体积上限优先（先判体积，未超体积才判页数）。commit `c351201f`。

**服务器清理**：观察确认队列正常消费非卡死（done 7→133、lag 110→0，约 20 分钟自愈，load 回落 1.40）；task 表 202 行 progress<1 僵尸行（所属文档 run=4 FAIL 终态、178 行为 8 月遗留，永不被消费只污染统计）按官方 stop_parsing 语义清理——删 200 行任务行 + 对应文档残留 ES chunk 按文档逐个 delete_by_query（实测 chunk 删除 0 条，即无残留），2 个文档跳过（非终态/已有完成任务，防误删有效 chunk）。

**测试**：新增 `test/test_kb_uploader_page_limit.py` 5 用例（60页跳过 / 边界50页解析 / 统计失败 fail-open / 非 PDF 不检查 / 体积上限优先且短路页数检查），全绿；ruff 与文件基线一致（仅新增 1 条 LOG015 与既有同款日志风格一致）。已 SCP `rag/svr/crawler_engine/kb_uploader.py`（爬虫子进程每次新起 Python 导入，无需重启容器），容器内冒烟 `PARSE_PAGE_LIMIT: 50` 生效。

**遗留**：`PARSE_PAGE_LIMIT=50` 为代码常量，如需调阈值改代码（阈值依据：本次肇事 PDF 84~96 页，100 挡不住故取 50）；detector 定时探测与解析共用单实例 task_executor 的挤兑问题仍未做隔离。

## 2026-09-08 修复 ES 高负载下检索重试必然失败：timeout kwarg 与 body 合并冲突（后端，已部署）

**主题**：用户画布触发范本填写（196 填写点）长时间无结果——日志显示 `retrieve_slot failed ... Received multiple values for 'timeout', specify parameters using either body or parameters, not both`，失败槽每个耗时 ~600s 且节奏与 ES 客户端超时（`Elasticsearch(..., timeout=600)`）吻合。根因链：① `es_conn.py _es_search_once` 把 `timeout="600s"` 作为 **kwarg** 传给 ES 客户端；② elasticsearch-py 9.x 的 `@_rewrite_parameters` wrapper 会把 kwarg **原地合并进 body dict**（`body[key]=kwargs.pop(key)`）；③ `es_conn.search()` 重试循环（`ATTEMPT_TIME=2`）在 ConnectionTimeout 后**复用同一 query dict** 重试；④ 第二次调用时 body 已含 timeout、kwarg 再传一次 → ValueError → 该槽降级空证据。即：**ES 高负载下凡触发超时重试的检索必然失败**，平时低负载不触发重试所以长期未暴露（对话检索、B端填写任务同受此债）。修复（commit `fd8f97be`）：`_es_search_once` 把 `timeout` 直接写进 body、不再走 kwarg 合并路径，重试恢复可用。诊断过程中容器内实证：单查/12 路并发均复现不了（当时 ES 负载低不超时），最终靠「失败节奏=600s+kwarg 合并语义」锁定。

**测试**：新增 `test/test_es_conn_search_once.py` 3 用例（timeout 进 body 不走 kwarg、重试复用同一 dict 不抛冲突、caller 预带 timeout 幂等）；模板填写 5 套件 214 单测全绿；ruff 无新增违规（es_conn.py 存量 40 条风格问题为基线，不做全文件重排）。已 SCP `rag/utils/es_conn.py` + 容器重启 + 容器内冒烟确认修复生效。

**遗留**：ES 单查询 100s+ 的平台级负载根因（OCR 解析压载）不变；本次画布实测运行因该 bug 已产出污染结果，需重新触发验证。

## 2026-09-08 范本填写 LLM 压力优化：产值批次并发 + 多范本并行 + 跨范本检索去重（后端，已部署）

**主题**：用户提出「一个范本过长（专用本 196 填写点）、多个范本处理时对 LLM 压力很大」——量化根因是调用链三层全串行：196 槽 ÷ BATCH_SIZE=10 → 20 次串行 LLM 批次调用（10 分钟级）× `_invoke_async` 里多范本 for 循环串行。三层解串（commit `4be0c134`）：① **产值批次并发**（executor.py `generate_values`）：分批后 `asyncio.gather` 并发（`GENERATE_CONCURRENCY=3`，批次间字段独立无依赖），支持外部传入共享 `sem`；② **多范本并行**（agent/component/template_fill.py）：`_fill_one` 并行 gather，全局 LLM 并发总闸 `_FILL_CONCURRENCY=4`（画布与 executor 共用同一信号量，多范本×批次并发不相乘打爆 provider）；**单范本失败不再拖死节点**——降级为「《xx》：填写失败（原因）」汇总行，其余范本照常产出，全失败才报节点错误；③ **跨范本检索去重**（executor.py 新增 `retrieve_all_shared`）：各范本填写点按 `(top_k, query)` 去重，同一检索词只查一次 ES、结果分发回各范本（同域政务范本检索词高度重合），槽位归集/降级语义与 `_retrieve_all` 一致（ctx 失败全槽空、单槽失败降级、param/无 key 槽不进检索）。效果：单范本 10min → ~3.5min；3 范本 30min → ~8min；任务 pipeline（execute_task/dry_run）路径行为不变（仅产值批次内部从串行变 3 路并发）。

**测试**：executor 新增 5 用例（共享检索去重/top_k 入键/ctx 失败全降级/空 kb_ids 不加载 ctx、批次并发峰值≥2+共享 sem 钉 1、空占位符不建模型）；画布组件新增 3 用例（单范本失败降级汇总行、全失败报错、共享检索只调一次）；修正批次顺序敏感断言（并发后完成顺序不定）；模板填写 5 套件 214 单测全绿，ruff 0 新增违规（存量 3 项为基线）。

**遗留**：已部署（SCP `rag/svr/template_fill/executor.py` + `agent/component/template_fill.py` + 容器重启，冒烟 `GENERATE_CONCURRENCY: 3` 生效）；LLM provider 侧压力峰值从 1 路变 4 路（DeepSeek/Qwen 速率上限内）；ES 单查 100s 的平台级负载根因不变（见上条）。

## 2026-09-08 范本填写检索性能优化：并发检索 + 上下文复用 + KB 去重（后端，已部署）

**主题**：用户实测「专用本（196 填写点）+ 画布范本填写」疑似卡死——排查为非卡死而是极慢：逐槽串行检索 × 单次 ES 查询 70~125s（ES 同时被 OCR 解析任务压载）× 每槽重复加载 KB/embedding 模型，预计 5 小时+。优化 executor.py `_retrieve_all`：① llm 槽并发检索（`RETRIEVAL_CONCURRENCY=6` 信号量），百级填写点从小时级压到分钟级；② 新增 `load_retrieval_ctx` 整批只加载一次知识库校验+embedding 模型（`retrieve_slot` 加可选 ctx 参数，向后兼容）；③ 上下文加载失败等价全槽降级空证据（不中断）；④ 画布节点 kb_ids 去重（实测配置同一 KB 重复 4 次，ES 索引列表翻倍）。单槽失败仍降级空证据，语义不变。

**测试**：新增并发回归测试（ctx 只加载一次、param/无 key 槽不进检索、单槽异常降级）；修复 gather 结果解包 bug（测试先行抓到）；模板填写套件 206 单测全绿，ruff check 0 违规（ruff format 存量文件非 format 风格，不做全文件重排）。已 SCP executor.py + template_fill.py（组件）+ 容器重启。

**遗留**：ES 单查询 100s+ 的根因是 OCR/DeepDOC 解析任务并发压载+KB 体量大，属平台级负载问题，本次只做并发缓解；产值 LLM 批次（196 槽 / 10 ≈ 20 次串行调用）仍需 10 分钟级，如仍慢可再并发化。

## 2026-09-08 范本填写节点升级：多范本各产一份成稿 + 三路数据注入 + C端成稿在线预览（前后端，已部署）

**主题**：按用户需求「自己捞取适配的单个或多个范本，LLM 根据范本做 KB 检索 + 上传文件 + 用户输入内容做范本数据注入，最终输出到前端 UI 渲染写好的内容」（渲染复用 C 端流程页签现成 Word 渲染设施）。① **TemplateFill 节点多范本**（agent/component/template_fill.py）：选型 prompt 改为选出一个或多个适配范本（`{"template_ids": [...]}`，兼容旧单选契约；非法 JSON/编造 id/空列表仍必报错），每个选中范本独立走「检索→产值→渲染」各产一份成稿，`download` 输出改为**列表 JSON**（Message._extract_downloads 原生支持 list 契约），content 汇总逐份列出填充情况。② **三路数据注入**：KB 检索（原有）+ **用户上传文件**（canvas 已解析的 `sys.file_content`，截 2000 字预置片段插到每个填写点证据首位，优先于 KB 片段）+ **用户输入**（需求描述进产值 LLM 背景信息；**Begin 表单字段**标量输出自动收集——与 param 模式填写点 key 同名即不经 LLM 直取，其余作背景信息）。③ **产物落桶修正**：产物改存 `{tenant_id}-downloads` bucket——`/agents/download`（FileService.get_blob）与 `/files/{id}/content` 两个端点的既有读取契约都是这个 bucket。④ **下载契约修复（存量 bug）**：message.py `_extract_downloads` 给每条下载信息注入 `url`（`/api/v1/agents/download?id=&created_by=`）与 `name`（此前 dl.url/dl.name 全链路无人赋值，c-chat 下载按钮 href undefined 是坏的）；docs_generator.py 产物同步改存 `-downloads` bucket（原存裸租户 bucket，下载端点读不到必 404）。⑤ **前端成稿预览**（web/src/pages/c-chat/index.tsx）：下载条目重构为「文件名（点击预览）+ 下载」双操作——预览打开现成 ReviewPanel（`GET /files/{id}/content` 段落 JSON 只读渲染，与流程页签同款），下载走修复后的 url；use-send-message.ts downloads 类型补 url/name。

**测试**：test_agent_fill_template_component.py 扩到 23 单测全绿（新增：多 template_ids 两份成稿、parse_selection 多选去重保序/空列表/脏类型、Begin 字段 param 直取、上传文件证据注入首位+需求描述进背景、无上传文件不注入、产物落 `-downloads` bucket 断言）；模板填写相关 5 套件 205 单测全绿无回归；前端改动文件 tsc 0 新增错误（3 个 c-chat 存量报错与本次无关，已对照基线确认）。

**遗留**：已部署（2026-09-08 全量部署：SCP template_fill/message/docs_generator + 前端 build + 容器重启冒烟通过）。待画布实测「范本填写 → Message」多范本链路；xlsx 产物在线预览暂走下载（file content 端点仅解析 docx/doc，xlsx 预览为原生降级）。



**主题**：按用户需求「配置一个节点专门写范本：LLM 自行判断用哪个范本，按占位符 KB 检索填写，输出内容」（经确认选独立节点形态，配置时只选知识库不选范本）。① 后端新增 `agent/component/template_fill.py`（`TemplateFillParam` + `TemplateFill` 组件，命名约定自动注册零登记）：运行时拉本租户已发布范本 → LLM 选最合适的一个（唯一候选跳过选型省一次 LLM）→ 复用 executor 的 `_retrieve_all`/`generate_values`/`build_values` 确定性 pipeline（缺值留空待人工二次加工，与 2026-09-08 统一 AI 填写语义一致）→ docxtpl/openpyxl 渲染 → 产物入 STORAGE_IMPL 并输出 `download` JSON（契约同 DocGenerator，下游 Message 节点渲染下载按钮）+ `content` 汇总文本。**关键命名约束**：组件类刻意取 `TemplateFill` 而非 `FillTemplate`——Agent 节点的工具同样经 `component_class` 解析且 agent.component 优先于 agent.tools，C 端对话 FillTemplate 工具类（agent/tools/template_fill.py）会被同名组件遮蔽、破坏存量画布（已加防遮蔽断言验证）。② 前端 9 处登记：Operator 枚举 `TemplateFill`、initial values（query 默认 `{sys.query}` + dataset_ids）、NodeMap/RestrictedUpstreamMap、use-add-node、form-config-map、新建 template-fill-form（需求描述 PromptEditor + KB 多选复用 KnowledgeBaseFormField + 输出列表）、工具面板分组、图标 FilePen、zh.ts（flow.templateFill/templateFillDescription/templateFillQuery）；`use-get-begin-query` 的 download 输出引用过滤同步覆盖 TemplateFill（同 DocGenerator 契约）。

**测试**：新增 `test/test_agent_fill_template_component.py` 16 单测全绿（对抗性覆盖：选型 LLM 非法 JSON/编造范本 id 必报错不静默换第一个、无已发布范本、未选知识库、工作副本缺失、xlsx addr 透传、download 输出契约、唯一候选不调选型 LLM）；`component_class('FillTemplate')` 仍解析到 C 端工具类（防遮蔽断言）；改动前端文件 tsc 0 error（存量 data-source 报错与本次无关）。已部署：前端 build + SCP + nginx reload，后端 SCP `agent/component/template_fill.py` + 容器重启，容器内冒烟通过（TemplateFill 注册成功、FillTemplate 工具未被遮蔽）。

**遗留**：建议在画布实测「范本填写 → Message」链路的下载按钮。query 需求描述为空时自动回退 `{sys.query}`；暂无任务参数（param 模式填写点会留空）。

## 2026-09-08 填写点统一 AI 填写，缺值留空交人工二次加工（已部署）

**主题**：按用户要求「识别后填写方式都让 AI 填，AI 自行判断，没有的空着占位符，最终输出文档人工审核二次加工」：① 识别层（detector.py）——prompt 不再引导 LLM 产 manual，parse_detection_response 代码层强制 fill_mode="llm"（LLM 不听话也拦得住，识别产物不再有「人工」）；② 执行层（executor.py）——_norm_fill_mode 把 manual 视同 llm（存量已保存的 manual 填写点也改走检索+LLM 生成；param 直取任务参数保留），build_values 删【待人工】标记合成，缺失一律落空串，is_partial 语义移除，任务渲染成功即 done；③ 渲染层（renderer.py）——manual_mark/【待人工：xxx】标记机制删除。partial 终态为兼容历史任务保留（重试/下载仍放行），新生成的任务不会再出现。前端零改动（新增填写点默认已是 llm，CELL_STATUS 的 not_found 徽标即「留空待加工」提示）。

**测试**：4 套件 182 单测全绿（parse 强制 llm 断言、build_values manual→空串、dry_run manual 参与生成/partial 恒 False、缺值渲染空串等用例同步改写），ruff 0 违规。已 SCP detector/executor/renderer + 重启，容器冒烟通过。commit `a6cfc21a`。

**遗留**：存量模板（专用本/通用本）已保存的填写点中如有个别 fill_mode=manual 的行，执行时自动视同 llm 处理，无需重新识别；详情页仍可手动把某个填写点改为 manual/param（改为 manual 时执行也按 AI 填写处理，详情页下拉选项后续可按需精简）。

## 2026-09-08 修复 docx 表格 addr 缺表序号导致多表格模板识别校验失败（已部署）

**主题**：专用本模板后台识别报「anchor 不在 cell:0:0:5 文本中」——根因是 docx 表格 addr 格式为 `cell:<row>:<col>:<para_idx>`，**没有表序号**，多表格文档两个表同 (row,col,para) 的段落撞号：parse 按 index 定位到表 A 段落（校验通过），validate/渲染按 addr 查到表 B 段落（anchor 错位）。不只校验失败——渲染替换也会落错表（锚文本恰好存在时静默替换错段落）。修复：addr 改为 `cell:<tbl_no>:<row>:<col>:<para_idx>`（docx_utils.py `_build_addr_map`）；xlsx addr 含 sheet 名天然唯一不受影响。存量兼容：修复前唯一已保存模板（通用本）99 个占位符全是 `para:` 格式，无需迁移；修复后容器内直跑识别线程体重跑专用本成功（196 个填写点，cell addr 均为新格式）。commit `dfb225cd`。

**测试**：新增多表格回归测试（同位置段落 addr 唯一 + 替换落到正确表）；4 套件 182 单测全绿，ruff 0 违规。服务器已 SCP docx_utils.py + 重启，专用本/通用本 detect 均为 done。

**遗留**：旧格式 addr 的存量占位符无自动迁移（当前数据无此类 addr，若未来发现旧导出数据需人工重识别）。

## 2026-09-08 上传模板改为「上传即走」后台 AI 识别 + 列表识别状态（前后端，已部署）

**主题**：按用户需求「上传执行到下一步时，可在列表看到 AI 识别的进度或状态」（经确认选择后台识别方案）：① 后端 `tpl_template` 加 `detect_status`（none|running|done|failed）+ `detect_error` 字段（db_models.py 含 migrate_db 迁移）；`TplTemplateService.set_detect_status` 状态流转方法；新增 `POST /template/fill/detect-async` 端点（template_api.py）——daemon 线程跑 LLM 识别，成功自动 `save_placeholders` 落库，所有失败路径（含 0 条识别结果）必置 failed 防卡 running；防重入双保险（进程内 `_detecting` set 为准 + DB 状态展示，进程重启自愈）；仅对「无已保存填写点」模板开放，防覆盖人工配置。② 前端：上传向导重构为单面板（upload-wizard.tsx 整体重写）——文件队列串行「上传 → 触发后台识别」后自动关闭弹框，不再等 LLM；列表页状态列叠加识别徽标（AI 识别中/已识别/AI 识别失败，失败悬浮显原因），存在识别中行时 3s 函数式轮询自动停止（use-template-fill-request.ts）；移除批量上传按钮与弹框（batch-upload-dialog.tsx 已删）。后端 commit `90d1f057`（含 12 个新单测，4 套件全绿）。

**测试**：后端 pytest 4 套件全过（含线程启动失败自愈、幂等防重、任意失败路径 `_detecting` 必回收等对抗用例）；前端改动文件 tsc/ESLint 0 error；Playwright 实测本地上传链路（上传成功、detect-async 因后端未部署返回 405 → 向导按设计标记失败并关闭，预期行为），测试模板已删除清理。

**遗留**：已部署（2026-09-08 全量部署确认：服务器文件 md5 一致、tpl_template 迁移列 `detect_status`/`detect_error` 已生成、容器重启）。详情页同步 detect 接口保留（已有填写点模板用手动识别）。

## 2026-09-08 上传向导多文件逐个走完整识别流程（纯前端，已 build 部署）

**主题**：按用户要求「单个/多个文件上传流程必须一致，下一步都是 AI 识别」——上传向导多选文件不再转交批量上传弹框，改为向导内维护文件队列：每个文件依次走「上传 → AI 识别 → 确认保存」完整三步，保存一个自动进入下一个（重置模板名/填写点/识别状态并直接开始上传+识别），步骤条显示「第 x/N 个文件」，Step1 显示文件列表（可单个移除），多文件时模板名只对第一个生效、其余自动取文件名，Step3 按钮改「保存并继续」。单个文件流程保持不变；「批量上传」弹框保留为独立入口（仅建草稿），移除 initialFiles 转交死代码。commit `450636fc`。

**测试**：tsc/ESLint 0 error；Playwright 浏览器端到端实测 2 文件全流程通过（Step1 文件列表 → 第 1/2 个识别保存 → 自动进第 2/2 个并完成识别 → 保存后关闭并跳详情），测试模板已批量删除清理。**遗留**：已随 2026-09-08 前端 build 部署。

## 2026-09-08 范本库详情页补 AI 识别按钮（纯前端，已 build 部署）

**主题**：批量上传只建草稿，原设计要求到详情页完成「AI 识别→确认保存→发布」，但详情页漏了识别入口（识别只在上传向导里有），批量上传的草稿只能手动加填写点。补齐：详情页「填写点配置」卡片加「AI 识别」按钮（template-fill/detail.tsx），复用既有 detect 接口（后端零改动）+ 上传向导同款合并规则——建议行在前、手动行（无 addr）在后、key 重复的手动行丢弃；识别中按钮禁用显「识别中…」；0 条识别结果 warning、失败 error 兜底。已提交 commit `ce2173e8`。

**测试**：tsc/ESLint 0 error。**遗留**：未浏览器实测识别链路；已随 2026-09-08 前端 build 部署。

## 2026-09-08 范本库批量上传模板（纯前端，已 build 部署；后续迭代中弹框被移除重构）

**主题**：范本库列表页新增「批量上传」入口（batch-upload-dialog.tsx）：多选 .docx/.doc/.xlsx 文件（单次上限 20 个、单个 ≤20MB），逐个展示文件名/大小，顺序复用既有 `POST /template/fill/upload` 端点逐个创建草稿模板（名称默认取文件名去扩展），逐条展示上传中/成功/失败状态与失败原因，单个失败不中断后续。AI 识别填写点为逐模板 LLM+人工确认环节，刻意不批量；上传完成后需逐个进详情走「AI 识别→确认保存→发布」。后端零改动。

**测试**：tsc/ESLint 0 error。**遗留**：已随 2026-09-08 前端 build 部署。

## 2026-09-08 范本库列表批量删除 + 单个删除（前后端，已部署）

**主题**：范本库列表页新增删除能力。① 后端新增 `POST /api/v1/template/fill/batch-delete`（template_api.py）：ids 非空字符串数组校验 + 单次上限 50，逐个复用 `TplTemplateService.delete_template`（守卫/事务/TOCTOU 行锁全复用单删），部分失败不影响其余，返回 `{deleted: [...], failed: [{id, message}]}`；单个删除复用既有 `DELETE /template/fill/<id>` 端点。② 前端：api.ts 补 `deleteTemplateFill`/`batchDeleteTemplateFill` URL；hooks 新增 `useDeleteTemplateFill`/`useBatchDeleteTemplateFill`（成功后失效列表缓存）；列表页加全选/行复选框列、行内「删除」按钮（仅 draft/disabled 可删，与后端守卫一致）、选中后顶部浮现「批量删除(N)」按钮，均走 `ConfirmDeleteDialog` 二次确认；搜索/筛选/翻页时清空选中；批量删除部分失败时 toast 汇总展示首条失败原因。

**线上 500 修复（部署首删即现）**：`delete_template` 在 `DB.atomic()` 事务块内调用带 `@DB.connection_context` 装饰器的 `has_tasks`/`get_owned`——该装饰器退出时无条件 `db.close()`，与开启的事务冲突，真实 MySQL 必现 `OperationalError('Attempting to close database while transaction is open.')`（单测桩/SQLite 不触发，属 review+153 单测都漏掉的盲区）。修复：事务内复查改裸查询（复用事务连接），并加源码断言回归测试（事务块内禁止调用装饰器方法）。已 SCP + 重启部署，commit `ffa23504`。

**测试**：后端 test_template_api_routes.py 新增 10 用例（路由注册、非法 ids 8 组参数化、超 50 上限、部分失败、全成功、事务裸查询源码断言），4 套件 165 单测全绿；共享桩 `_FakeJsonRequest.get_json` 兼容 `silent=True` 签名。前端改动文件 tsc/ESLint 0 error。

**遗留**：前端已随 2026-09-08 build 部署。已发布/有填写任务记录的模板按设计不可删（守卫在 service 层）。

## 2026-09-07 模板填写 P2+P3 实施完成（执行引擎 + 任务闭环 + C端对话工具，已部署）

**主题**：模板填写系统 P2+P3 全量落地（feat/unified-crawler-framework 分支，23 commits）：① 依赖修正（docxtpl 主依赖 + openpyxl 升主依赖）；② P1 遗留债 4 项全部消化（published 保存填写点自动升 v2、模板状态机白名单、prompt 注入面清洗、模板删除端点含事务+行锁防 TOCTOU）；③ 执行引擎 `rag/svr/template_fill/executor.py`——检索层（逐槽 KB 检索 + 租户/Embedding 一致性校验 + 部分命中拒用）、生成层（LLM 批量产 JSON ≤10 字段/批、prompt 全注入面清洗截断、_apply_constraints 兜底、JSON 解析两级 fallback）、编排层（`build_values` 待人工合成 + `execute_task` 六步 CAS 状态机 pipeline：pending→retrieving→generating→rendering→done/partial/failed，按 task.template_version_id 钉住版本）；④ 渲染层 renderer.py（docxtpl Word 模板渲染 + openpyxl Excel 坐标直写，manual/not_found 落【待人工】标记）；⑤ 填写任务 REST 6 端点（发起[后台 daemon 线程+防重入+spawn 自愈]/列表/详情/重试/下载/测试填写 test-fill 试跑直返不落任务）；⑥ B端前端：任务列表页（状态筛选+3s 函数式轮询+下载/重试）、任务详情抽屉（逐格值+单元格状态+检索证据溯源）、发起填写对话框（KB 多选+params 键值对）、测试填写对话框；⑦ C端 agent 画布 FillTemplate 工具（list_templates/fill/status 三 action，fill 同步轮询 50s，自动发现注册验证通过）。

**测试**：后端 4 套件 152 单测全过（含对抗用例：CAS 竞态/越权 KB/脏 placeholder/batch_size=0/LLM 坏输出/线程启动失败自愈），ruff 0 违规；前端 tsc 本功能 0 error。全流程子代理驱动开发：每任务实现→规格审查→质量审查→修复循环，关键修复含：版本钉住防升版静默换版、兜底 update 泄漏连接池、分批 step/slice 不一致、_extract_json 静默吞批、total_datasets 契约对齐。

**遗留（登记为后续任务）**：① 每租户运行中任务数无上限（可刷 create 耗 LLM 额度）；② 进程崩溃后中间态任务无 sweeper 回收（永久卡 running）；③ create-task-dialog 与 test-fill-dialog KB 选择约 150 行重复；④ 试跑同步等待大模板可能超 nginx proxy_read_timeout 60s；⑤ B端 agent 编辑器组件面板未登记 FillTemplate 节点（需前端 Operator enum + form-config 登记，或通过导入 DSL JSON 挂载）；⑥ constraints 约束字段只有消费端（_apply_constraints）没有生产端（detector 不产出、前端表单无输入），约束兜底形同虚设；⑦ 任务详情抽屉用范本 latest 版本 placeholders 做映射而任务钉 template_version_id，升版后历史任务行集合可能错位（仅显示层）；⑧ top_k 默认值三处不一致（前端新行 5 / detector 6 / executor 6），无功能影响。

**待办（部署时）**：已完成（2026-09-08 核验：容器 docxtpl/openpyxl 已装，template_fill_service/template_api/executor/renderer/agent tools 与服务器 md5 一致，容器重启冒烟通过）。P4 flow 模板填写节点未做。

## 2026-09-07 范本库改名 + 旧版 .doc 支持 + 上传控件样式优化（已部署）

**主题**：模板填写 P1 三项增量：① 「模板库」UI 文案统一改为「范本库」（navbar `zh.ts:templateFill` key + 列表页标题，不碰 en.ts）；② 上传支持旧版 .doc——后端 LibreOffice（`soffice --headless`，独立 UserInstallation profile 防并发锁，timeout 60s）转成 .docx 后以 docx 形态进入全链路（candidates/替换/预览/下载），转换失败中文兜底提示，转换产物复查 20MB 上限；③ 上传向导 Step1 文件选择控件重构——隐藏 input + 虚线拖拽风格选择区（图标+主辅文案），已选态展示文件名/大小/重新选择/清除按钮。

**核心变更**：`web/src/locales/zh.ts`（templateFill='范本库'）、`web/src/pages/template-fill/index.tsx`（CardTitle）、`web/src/pages/template-fill/upload-wizard.tsx`（控件重构 + TEMPLATE_FILE_RE 放行 .doc + 外层 onKeyDown 加 `e.target !== e.currentTarget` 卫语句修复 X 清除按钮被键盘劫持）、`api/apps/restful_apis/template_api.py`（_is_legacy_doc/_convert_doc_to_docx + upload 端点接入转换分支 + 转换后 20MB 复查）。部署联调修复：容器 soffice 包装脚本不自设库路径致 rc=127（libreglo.so 加载失败），subprocess 显式注入 `LD_LIBRARY_PATH=/usr/lib/libreoffice/program` 后端到端转换通过。

**测试**：后端 65 单测全过（新增 .doc 后缀判定/转换失败/超限用例，容器无 soffice 场景 monkeypatch subprocess）、ruff 0 违规；tsc 本功能文件 0 error。经合并审查（needs-fixes → 修复 → 复核 APPROVED）。

**遗留**：已部署（2026-09-08 核验：template_api.py 与服务器 md5 一致，前端已随最新 build 上线）；上传 .doc 依赖容器内 LibreOffice（soffice 已确认存在，LD_LIBRARY_PATH 修复已在服务器生效）。

## 2026-09-07 模板填写系统 P1 实施完成（表 + API + B端模板库页面）

> **2026-09-07 已部署至服务器**（前后端成套 SCP + 前端 build + 容器重启，三张 tpl_ 表已建，`/api/v1/template/fill/list` 401 鉴权正常、页面 200）。待浏览器端功能联调。

**主题**：P1 全量落地（feat/unified-crawler-framework 分支，待部署）：① tpl_template / tpl_template_version / tpl_fill_task 三表进 db_models + migrate_db 兜底；② Service 层（template_fill_service.py，租户隔离/分页钳制/MinIO 存储）；③ docx/xlsx 占位符工具（iter/extract/apply，超链接段落 run 拼接替换、公式格排除、合并单元格安全）；④ detector（LLM 识别填写点 prompt/解析/校验，anchor≤500、空 addr 按 anchor 反查唯一推导）；⑤ REST API 9 端点（/api/v1/template/fill/*，损坏文件兜底、20MB 前置校验、zip 校验）；⑥ B端前端：列表页 + 三步上传向导（上传→AI识别→确认保存）+ 详情页（占位符编辑+`{{key}}`高亮预览+下载），路由/顶部菜单「模板库」/中文文案收口。

**测试**：后端 58 单测全过（含边界/对抗用例），ruff 0 违规；前端 tsc 本功能 0 error。经逐任务双阶段审查（规格+质量），关键修复含：伪 zip 500 兜底、await 同步函数运行时 bug、手动添加行 addr 契约断裂（anchor 反查回填）、向导重复上传、trim 校验值与提交值不一致。

**遗留**：P2 执行引擎（检索→LLM→校验→渲染，需容器装 docxtpl）未开始；C端入口 P3/P4 未开始；**待部署联调**（部署冒烟清单见 docs/superpowers/plans/2026-09-07-template-fill-p1.md Task 12）。

## 2026-09-07 模板填写系统设计（固定 Word/Excel 模板 + KB 自动填写）

**主题**：新功能「模板填写」完成方案设计（未编码）。固定模板（Word/Excel）由 LLM 根据知识库内容自动填写：模板独立为「模板库」资产（不放知识库），占位符注册表驱动，LLM 只产字段值 JSON，docxtpl/openpyxl 程序渲染回填，格式样式 100% 保留。

**核心设计**：tpl_template / tpl_template_version / tpl_fill_task 3 张表（占位符清单随版本原子演进，含检索意图/约束/必填元数据）；执行引擎为确定性 pipeline（逐槽位检索 → 一次 LLM 批量产 JSON → 校验兜底 → 渲染）；未命中 required 槽位标「待人工」禁编造，evidence 留存可溯源。入口：B 端独立「模板库」页签（上传向导/LLM 识别填写点/人工确认/版本/预览/测试填写/任务列表）+ C 端对话 FillTemplate 工具（P3）+ flow 模板填写节点（P4）。分 P1-P4 四期实施。

**设计文档**：`docs/superpowers/specs/2026-09-07-template-fill-design.md`（含表结构、API 清单、部署清单、风险对策）。

**遗留**：待实施 P1（表 + 模板管理 API + B 端页面）；新依赖 docxtpl 需进容器镜像。

## 2026-09-01 文件审核表格可编辑（run 级格式 + 批注共存）

**主题**：C端流程「文件审核」弹框正文编辑已支持 Word 式 run 级格式，但表格内容为只读原子块（有意取舍）。本次将表格升级为 `@lexical/table` 节点体系（方案A），实现单元格内文字可改 + 加粗/斜体/下划线/删除线/上下标/颜色/底色/字体/字号等 run 级格式，工具栏在格内同样生效；不做表格结构编辑（不增删行列/表格）；格内批注高亮与编辑共存；图片维持只读。

**核心变更**：
- 前端 `docx-paragraph-editor.tsx`：新增 `DocxTableNode extends TableNode`（携带 paraIndex，import/export 均序列化）；`buildDocxTable` 解析后端表格 HTML → TableRowNode/TableCellNode（colspan/rowspan、th→表头、格内按行分段、空格建空段）；畸形 HTML 降级只读 AtomicBlockNode；`readEditorBlocks` 表格分支逐格产出 `{paraIndex, kind:'table', cell:{row,col}, text, runs, fmtSig}`（格内文本按单 `\n` 连接、runs 含 `\n` 分隔片保证 `''.join(runs.text)===text`）；HighlightPlugin 增加表格分支逐格重建批注高亮
- 前端 diff 契约 `docx-diff.ts` + `flow-service.ts`：新增 `table_edits: [{para_index, row, col, new_text, runs?}]`（与 edits/deletes/inserts 并列，计入 200 处上限）；`new_text` 允许空串（清空格合法）；基线与灌入同源（parseTableCells）
- 后端 `flow_app.py::edit_document`：`_parse_table_edits` 校验（row/col 非负整数、runs 一致性、new_text≤20000、空+runs 拒绝，错误信息带格位）；`_apply_cell_text` 用 `table.cell(r,c)` 定位 `_Cell` 复用 `_apply_runs`/`_replace_para_text` 写首段、清空 cell 内其余段；事务语义不变（先全定位再统一应用，存新版本）
- 降级保护：表格 HTML 解析失败回退只读展示，不阻塞文档打开

**验证**：前端 59/59 jest（docx-diff/docx-table-utils/docx-format-utils 三套件）、后端 22/22 pytest（test_flow_doc_table_edit.py 12 项 + 回归）、tsc 涉及文件全干净；编辑态表格样式已收敛到编辑器分支（不泄漏只读静态渲染）。

**遗留**：合并单元格 false-positive colspan 场景下只写首格（格位被覆盖时后端 `cell(r,c)` 返回同一 `_Cell`，多格改动同写一格）；嵌套表格不支持（灌入时按纯文本处理）；待随前端 build + 后端 flow_app.py SCP 部署后生效。

## 2026-09-01 流程页签：非负责人版本只读查看 + 流程删除（仅已作废）

**主题**：两个功能补充——①流程参与人即使不在当前节点，也能查看版本记录的文件内容（此前只有当前节点负责人能进「文件审核」）；②已作废的流程可由发起人彻底删除（此前作废流程只能永久留在列表）。

**核心变更**：
- 后端 `flow_service.py`：`FlowActionService.delete_flow(flow, user_id)`——仅发起人、仅 cancelled 状态；事务内级联删 FlowAiChat/FlowComment/FlowVersion/FlowInstance，返回版本存储路径列表
- 后端 `flow_app.py`：新增 `POST /flow/<flow_id>/delete`（需参与人登录）——调 service 后对存储对象 best-effort 逐个 rm（失败仅 warning，不影响删除结果）
- 前端 `flow-service.ts`：新增 `deleteFlow(flowId)`
- 前端 `flow-detail.tsx`：
  - 版本时间线每项新增「查看」眼睛按钮（所有参与人可用，含已归档/已作废）：下载 blob → `POST /documents/upload` 转 document → 复用 `ReviewPanel` 只读模式（不传 onAddComment/onDeleteComment/canEdit，批注仅边栏展示）；同一版本二次点击直接复用已上传 document
  - 详情头部：`isInitiator && status==='cancelled'` 时显示「删除流程」按钮（confirm 后调接口，成功回调 onDeleted）
  - 新增 prop `onDeleted`
- 前端 `flow-panel.tsx`：onDeleted → 清空选中态 + 刷新流程列表/todo 角标

**验证**：tsc/eslint 通过（无 flow 相关新增问题）；Playwright 实测 dev（9222 → 生产后端）：测试6（领导审批中，自己非当前节点）点 v1 .doc 版本「查看」正常打开内容渲染；已作废流程「测试1」正确显示「删除流程」按钮，非作废流程不显示。

**遗留**：删除接口为后端新端点，生产未部署前前端点击删除会报错；随下次后端 SCP（flow_app.py + flow_service.py）+ docker restart 后生效。前端需 build 部署 dist。

## 2026-09-01 流程页签 UI 重设计（布局 + 交互 + 动画）

**主题**：C端流程页签视觉与交互重设计——新增流程步骤条进度可视化、列表卡片信息分层、版本时间线化、全页适度动效，不改任何业务逻辑与 API。

**核心变更**：
- 新增 `web/src/pages/c-chat/flow/flow-utils.ts`：状态文案/徽章配色/圆点配色、五节点步骤定义（发起→领导审批→处理→汇总审核→归档）、`statusStepIndex`、`relTime` 相对时间（今天/昨天/N天前/M月D日）
- flow-panel.tsx：
  - scope 切换改分段控件（白色指示块滑动过渡 200ms）
  - 列表卡片化：状态彩色圆点+状态色文字+相对时间；选中卡片左侧蓝色指示条+浅蓝底+描边；入场 stagger 滑入（40ms 间隔，封顶 240ms）
  - 加载改骨架屏；空状态图标化 +「发起新流程」快捷入口
  - 批注区改可折叠：底栏开关+计数角标（由 FlowDetail 新 prop `onCommentsCount` 上报），有批注自动展开、手动切换后不干预；收起时列表占满整栏
- flow-detail.tsx：
  - 顶部新增 FlowStepper 步骤条：完成节点实心蓝+对勾（zoom 弹入）、当前节点描边+ping 呼吸光圈、连线随进度填充 500ms；已作废全灰+红色标记
  - 状态徽章按状态配色（进行中蓝/汇总黄/归档绿/作废红）；负责人改胶囊 chip（窄屏隐藏）
  - 版本列表改时间线样式：左侧竖线+节点圆点（选中实心蓝光圈/AI 产出蓝描边/人工上传灰描边）、来源标签、相对时间；下载/删除改图标 ghost 按钮悬停显隐（focus-within 兜底键盘）
  - 详情切换 fade+slide 200ms 过渡（key=flowId）；加载改 DetailSkeleton 骨架屏；对话区空状态图标化
  - 动画统一尊重 `motion-reduce:animate-none`

**验证**：tsc 无 flow 相关错误、eslint 0 error；Playwright 实测 dev（9222）：列表/分段控件/步骤条/时间线/批注折叠展开均正常渲染。

**遗留**：无业务逻辑改动；待随前端 build 部署生产。

## 2026-09-01 流程页签：AI 对话自动保存 + 会话续接 + 存版本不重复（方案C）

**主题**：流程对话持久化改造——每轮对话自动写入 flow_ai_chat（告别手动「仅存记录」）、跨次进入续接同一 agent 会话（多轮上下文不丢）、会话命名「流程：标题」在对话页签可识别、「存为新版本」基于已存记录补建版本不重复插记录。

**核心变更**：
- 前端 flow-ai-panel.tsx：
  - 自动保存 effect：流式结束（done && contentRef 有内容）自动调 saveFlowAiRecord(save_as_version=false)，成功后清空兜底态刷新记录；失败保留 contentRef，手动「仅存记录」按钮兜底显示
  - 会话续接：sessionIdRef 初始化自 aiChats 最后一条的 session_id，重进流程不再新建碎片会话（后端校验仅 agent 归属+canvas 可访问，跨参与人续接共享会话成立）
  - ensureSession 会话命名 `流程：{flowTitle}`（新建 flowTitle prop）；对话页签历史列表天然可见（同 API 同 user）
  - lastRecord state 记录自动/手动保存返回的 record_id；「存为新版本」传 record_id 走补建分支；按钮显示条件改 `hasContent || lastRecord`（hasContent 时说明自动保存失败，补显「仅存记录」）
- 后端 flow_service.py：FlowAiChatService 新增 get_record / set_output_version
- 后端 flow_app.py add_ai_record：支持 record_id 分支——校验记录归属后仅建版本并回写 output_version_id，不重复插记录；record_id 与 save_as_version=false 组合拒绝
- flow-service.ts payload 类型补 record_id

**验证**（Playwright 实测，dev 前端 → 生产后端）：
- 自动保存：发送后记录自动入列表，无「仅存记录」按钮
- 会话续接：第二轮 completion 请求 session_id 与上轮一致（无新建），AI 准确复述上一轮指令
- 命名：测试3 新建会话名为「流程：测试3」
- 存为新版本：record_id 已正确发送；因生产后端未部署新代码暂走旧逻辑重复插记录（测试数据 2 条），部署后即走补建分支

**偏差说明**：验证时生产后端尚为旧代码，record_id 分支走了旧逻辑产生 2 条测试重复记录（测试2 流程，未清理）。

**部署验证**（2026-09-01 16:05 后端 SCP + docker restart 后实测）：测试3 流程点「存为新版本」→ 同一条记录原地回写「已存为新版本」（无重复插入），v3 AI 产出 .md 版本生成，上下文切至 v3。record_id 补建分支生产验证通过。

**遗留**：前端待 build 部署生产 dist（当前 dev 已验证）。

## 2026-09-01 流程页签：AI 回复渲染格式适配（Markdown + 思考过程折叠）

**主题**：流程中部对话区 AI 回复从纯文本（whitespace-pre-wrap）升级为 Markdown 渲染，与对话页签同款体验。

**核心变更**：
- flow-detail.tsx ConversationView 回复气泡（正式记录 + 进行中流式）改用 `ChapteredMarkdown`（c-chat 同源组件：流式走 StreamMdContent 增量解析、完成后走 MarkdownContent，内置 `<think>` 折叠块/加粗/列表/表格渲染）；进行中传 `loading={live.busy}`
- 新增 `normalizeLlmMarkdown`：think 标签前后补空行——`<think>` 被转成 `<section>` HTML 块后，CommonMark 的 HTML 块持续到空行结束，`</think># 标题` 无空行时标题被吞成字面文本

**验证**：Playwright 实测：`##` 标题/加粗/两行列表正确渲染、思考过程折叠为按钮、流式期间无格式错乱；tsc/eslint 零新增报错。

## 2026-09-01 流程页签：附带版本文件发送挂死修复（mime_type 缺失）

**主题**：流程「AI 处理」开启「附带版本文件」发送后永远卡「正在思考…」且输入框锁死的根因修复。

**根因**：flow-ai-panel 的 `uploadVersionAsDocument` 只返回 `{id, name}`，后端 `canvas.py get_files_async`（line 1015）直接取 `file["mime_type"]` → KeyError，SSE 生成器在输出任何字节前崩溃 → 请求永不结束，`busy` 锁死后续发送。c-chat 正常是因为它传完整上传响应对象（含 mime_type）。

**核心变更**：
- flow-ai-panel.tsx `uploadVersionAsDocument` 改为返回完整上传响应对象 `d`（含 mime_type/extension 等），与 c-chat 传参格式对齐；类型注解保持 `{id, name}` 兼容既有调用点（toggleReview/handleEditDocument 只用 id/name）

**验证**：Playwright 实测流程「测试2」附带版本文件发送 → 流式回复正常完成、保存按钮出现、AI 明确识别到附带文件内容。

## 2026-09-01 流程页签：AI 对话实时上屏 + 输入框加高

**主题**：C端流程详情页「AI 处理」发送后，指令与流式回复实时显示在中部对话区（此前只显示在输入框上方小 pre 块，中部一直停留在「暂无对话记录」）；输入框最小高度 40px → 120px。

**核心变更**：
- flow-types.ts 新增 `FlowLiveChat`（instruction/response/busy）
- flow-ai-panel.tsx：新增 `onLiveChatChange` 上报对话状态；修复「一直正在思考」——hook 的 send() 结束时 resetAnswerList 会清空 streamState，面板内用 contentRef 流式累积 + completed 状态兜住完整回复（保存按钮 hasContent 同步兜底）；移除原流式 pre 块；输入框包裹层 `[&_textarea]:min-h-[68px]`（约三行，仅 flow 场景生效）
- flow-detail.tsx：`liveChat` state 传入 ConversationView，未保存的进行中对话以同款气泡追加在正式记录之后（busy 无内容显示「正在思考…」+光标，结束无内容显示「（无回复内容）」），流式增长自动滚底

**遗留**：无（纯前端，未部署）。

## 2026-09-01 人事模块 P4：财务凭证报表 + 考勤机API预留（功能点15-20）

**主题**：人事页签新增「报表」子页签（仅 hr_manage 可见），交付工资手工调整（留痕+stale 提示）、财务凭证生成（计提/发放，借贷平衡断言）、3 种 Excel 报表导出、历史归档检索、考勤机批量同步预留端点。

**核心变更**：
- 新增3张表：hr_payslip_adjust（调整日志 old/new 全留痕）/ hr_voucher（月度凭证，(month, voucher_type) 唯一，重生成覆盖，status normal|stale）/ hr_attendance_import（同步批次留痕，失败明细前50条）
- 凭证纯函数 build_voucher_entries：accrue=借「管理费用—工资」/贷「应付职工薪酬」；pay=借「应付职工薪酬」/贷个税+社保+公积金+银行存款，逐行校验恒等式 `net = gross − att − social − fund − tax`（脏数据行抛 ValueError 带 employee_id）；仅从 published 工资单汇总
- 工资手工调整闭环：仅 published 可调，field 白名单（考勤扣款/社保/公积金/个税），强制 reason，条件更新防竞态，net 重算落盘 + 调整日志；该月 pay 凭证已生成时标记 stale 提示重生成；前端 salary-view 工资单行内「调整」表单（e65b9e3 补齐操作入口）
- 3 种报表导出（openpyxl 内存构建流式下载）：考勤月汇总 / 工资发放明细 / 社保公积金个税汇总；文件名 UTF-8 filename* + ASCII fallback；单元格字符串首字符 `= + - @` 前缀单引号防 Excel 公式注入
- 历史归档检索：month/department/keyword 三条件可选（至少一个），keyword 工号/昵称 OR 匹配，附当月 payslip 状态与实发
- 考勤机预留：POST /hr/attendance/sync-api（api_sync）与 /hr/attendance/import（manual_excel，本期接受 JSON records）共用 batch_punch——逐条同分钟去重、失败收集不中断（非 dict 记录 safe 兜底）、批次留痕；2000 条批量卸载 thread_pool_exec 不阻塞事件循环
- 前端 report-view.tsx：报表导出卡/凭证卡（计提蓝发放绿徽章+entries 借贷表格+stale 标记）/调整记录卡/归档检索卡/考勤机导入卡；exportReport 独立 fetch+blob+Content-Disposition 文件名解析

**偏差说明**：
- Excel 文件解析预留后续迭代（本期 import 接受 JSON records 数组）
- hr_finance 权限未启用，P4 端点暂挂 hr_manage（与 P3 口径一致）
- 凭证全月汇总一张、不分部门（简化口径）
- 连续二次 adjust 时 voucher_stale 布尔可能失真（MySQL changed-rows 语义，同值更新 rowcount=0，信息级不修）

**测试**：test/hr/ 73 passed；ruff 基线不增长（hr_service 8 / hr_app 7 / 新文件 0）；tsc 零 hr 报错。

**遗留**：待部署联调（后端成套 SCP：db_models/hr_calculator/hr_service/hr_payroll/hr_app + 前端 build）。

## 2026-09-01 人事模块 P4 质量审查修复（后端 10 项）

**主题**：P4 财务凭证/报表/考勤机导入端点的后端质量审查（Critical 1 + Major 4 + 建议 3）逐项修复。

**核心变更**：
- batch_punch 失败收集路径崩溃修复（Critical）：rec 非 dict 时 except 内 `rec.get` 抛 AttributeError 整批 500 且不留痕 → `safe = rec if isinstance(rec, dict) else {}`
- adjust 上限校验（回退 P3 M4）：hr_app 端点复用 `_valid_amount`（bool/负数/NaN/Inf/>99999999 拒绝）；`apply_adjustment` 服务层兜底补同上限，防 1e12 撞 DecimalField(10,2) 落库 DataError
- adjust 原子性：条件更新/调整日志/pay 凭证 stale 标记包进 `DB.atomic()`；连带修复调整日志 insert 漏调 `.execute()` 从未落库的隐患
- 凭证恒等式真校验：`build_voucher_entries` pay 分支恒真 assert 改为逐行校验 `net = gross − att − social − fund − tax`（round(2) 后比较），脏数据行抛 ValueError 并带 employee_id/行号
- batch_punch 卸载线程池：sync-api/import 两个 async handler 改 `await thread_pool_exec(...)`，2000 条逐条 DB 查询不再阻塞事件循环
- Excel 公式注入防御：`_build_xlsx` 字符串首字符 `= + - @` 前缀单引号
- 凭证 stale 状态标记：adjust 时条件更新已存在 pay 凭证 `status="stale"`，generate 重生成恢复 `normal`；批次留痕 insert 包 try/except（`logger.exception` 不吞成功结果）；Content-Disposition 补 ASCII fallback `filename="report.xlsx"`

**测试**：test/hr/ 73 passed（70 存量 + 新增 3 行用例：bool/1e12 拒绝、上限边界、脏 net 行拒绝含 employee_id 报文）；ruff hr_service 8 / hr_app 7 / hr_payroll 0 / test 0 不增长。

**遗留**：Excel 注入防御为 hr_app 内部函数，纯函数级单测不可行，依赖代码审查验证；#10（量级小）暂不处理。

## 2026-09-01 人事模块 P3：薪资核算引擎（功能点10-14 + 加班小时数）

**主题**：人事页签新增「薪资」子页签，交付薪资档案管理、加班时长推导（补齐 P1 月汇总缺口）、考勤扣款/加班费/社保公积金/个税累计预扣核算引擎、试算→核算入库→发布工资条闭环。

**核心变更**：
- 新增2张表：hr_salary_profile（员工薪资档案，employee_id 唯一）/ hr_payslip（月薪资单快照，(employee_id, month) 唯一，status draft|published）
- 加班时长推导：derive_day_status 输出 overtime_hours——工作日=last_out 超 work_end 时长；休息日/法定节假日（rule.holidays 逗号分隔 YYYY-MM-DD）=首末打卡跨度；leave/abnormal/missing 恒 0；upsert_day/close_month 全量接入
- 法定节假日免打卡：derive_day_status 将节假日按休息日口径处理（status=rest，不判 missing→absent、不判迟到）——修复「节假日不打卡被扣 3 倍日薪」资金受损路径
- 核算引擎 hr_payroll.py 纯函数（零 DB 依赖，18 个对抗性单测）：考勤扣款（21.75 日薪制）、加班费（weekday/weekend 单价 + holiday 倍数×日薪）、个税累计预扣 7 级超额累进（tax_snapshot 记 cum_gross/cum_social/cum_fund/cum_special 跨月续算）、应发实发全公式、金额一律 round(2)
- 核算流程：前置=该月考勤月汇总已 confirmed（否则拒绝）；加班按日归类（holidays→holiday 费率、rest/周末→weekend、其余→weekday）；手工覆盖 manual_overrides（social/fund/tax 数值）命中走覆盖值且 snapshot 留计算原值；draft 幂等重跑（published 条件更新拒绝覆盖）
- 后端 7 端点：GET/PUT /hr/salary-profile（费率显式 null=清除回退全局）、POST /hr/salary/trial（只读试算）/calc（全员核算入库，单人失败不中断）/publish（draft→published），GET /hr/salary/payslips、GET /hr/payslip/my（员工仅见 published）；HR 端点挂 hr_manage
- 前端：salary-view.tsx——员工工资条卡片（未发布提示「该月工资条尚未发布」）+ HR 档案管理（keyword 搜索/行内编辑/费率可留空走全局）、试算（失败员工红字显示原因）、核算入库/发布（confirm 强提示）、工资单列表（draft/published 徽章）
- 规则键：DEFAULT_RULE 新增 social_rate(0.105)/fund_rate(0.12)/holidays("")，holidays 经 normalize_holidays 正则+真日期双重校验入库
- 质量审查修复：节假日免打卡（Critical）、save_draft 发布竞态改条件更新+rowcount、唯一约束并发 IntegrityError 回查重试、calc 消除重复查询+月快照单条倒序查询替代逐月回退、金额上限 9999 万防 DecimalField 落库 DataError、试算响应白名单防内部字段泄漏、前端试算按 ok 分流渲染

**偏差说明**：
- 个税跨月续算：上月无快照（含 overridden 断链）时按当月首月起算——设计原文「从1月按当前累计补算」需全量历史收入数据，系统无外部收入源不可得
- hr_finance 权限未启用：薪资端点暂挂 hr_manage，hr_finance 预留 P4 财务凭证
- 计划文字「8 端点」实为 7 条路由，按列表实现
- manual_overrides 暂无前端 UI（仅 API 可设），后续按需补
- publish 用 datetime.now()（published_at 为 DateTimeField；current_timestamp() 返回毫秒 int 不兼容）

**测试**：test/hr/ 61 passed（26 存量 + 加班推导 7 + 核算引擎 18 + 节假日/normalize_holidays 10）；ruff 新文件 0 告警；tsc 零 hr 报错。

**遗留**：待部署联调（后端 5 文件成套 SCP：db_models/hr_calculator/hr_service/hr_app/hr_payroll + 前端 build）；报表/凭证/导出见 P4。

## 2026-09-01 人事模块 P2：请假与出差审批联动（功能点6-9 + 员工补卡申请）

**主题**：人事页签新增「请假」子页签，交付假单申请→多级审批→考勤自动修正→假期余额冻结扣减全闭环。

**核心变更**：
- 新增3张表：hr_leave_request / hr_leave_step / hr_leave_balance（启动自动建表）
- 审批链：rule_config 新键 approval_chain / approval_chain_long（≥3天，逗号分隔 user_id，空回退超管列表），提交时实例化 hr_leave_step 逐步推进
- 余额两段式：frozen（审批中冻结）→ used（终审转已用），驳回/撤销释放；有额度假型 annual(5)/sick(15)/marriage(3)/maternity(98) 天（rule_config 可调），personal/business_trip/other 不占额度；submit 全程 DB.atomic 防孤儿单
- 并发防重：act() 全部条件更新+rowcount，审批双击只成功一次，防双扣额度
- 考勤修正：假单类型语义修正——business_trip 判「出差」，其余类型（事假/病假/年假/婚假/产假/other）统一判「请假」；终审回写区间内未锁定日 hr_attendance_day.status+leave_id，驳回/撤销重新推导恢复；close_month/日历/今日全部接假单推导
- 员工自助补卡申请：leave_type=repair 复用审批链，通过后按 work_start 自动补 source='repair' 打卡（终审前预检同分钟撞卡）；考勤视图仅当日无签到时显示入口
- 后端 8 端点：POST /hr/leave、GET my（含 steps 审批进度）/pending/\<id\>、POST \<id\>/approve、POST \<id\>/cancel、GET/PUT /hr/leave/balance（HR 调年度额度）
- 前端：leave-view.tsx（余额卡片/新建假单/审批进度条/待我审批/撤销）、人事页签待审批红点角标（60s 轮询）、假单 7 个 API
- 触达方案偏差：设计原定复用采集通知系统，因其表结构绑 crawler_result 语义不符，改为「待我审批」tab + 页签角标轮询
- 审查修复：并发双扣（条件更新）、孤儿假单（原子事务）、假单排序确定性、详情权限口径（复用 permission_allowed）、/hr/leave/my 补 steps

**遗留**：act() 未包整体事务（各步条件更新独立提交，已无双扣风险）；leave-type=leave 历史语义已在推导层兼容；跨年假单整段记入开始年度额度（口径一致，代码有注释）；ruff DTZ/C408 存量告警未清；待部署联调。

**测试**：test/hr/ 26 passed（leave_status_for_date 对抗性单测：边界日期/pending 不计/repair 跳过/类型映射/空输入/列表序优先）。

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
