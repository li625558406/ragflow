# CHANGE.md — 项目迭代记录

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
