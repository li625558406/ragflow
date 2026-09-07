# 模板填写系统设计（Template Fill）

> 日期：2026-09-07
> 状态：设计完成，待实施
> 需求背景：用户有大量固定模版的 Word/Excel，希望 LLM 根据知识库内容自动填写模版中的待填项

## 1. 核心原则（第一性原理）

- **LLM 只产出"字段值 JSON"，不直接"写文档"**。回填由程序（docxtpl / openpyxl）完成 → 格式/样式/模板结构 100% 保留，出错可定位、可重试。
- **模板是「渲染资产」，不是「检索语料」** → 独立模板库，不放知识库（避免模板被切块索引污染检索，且 KB 文档模型无处挂占位符元数据）。
- **执行引擎只有一个**，对话分支、flow 节点、B 端页面都只是入口 → 质量/日志/重试/审核口径统一。

## 2. 总体架构（三层）

```
入口层
├── B端「模板库」页签（独立页面，功能完善）：模板治理 + 手动发起填写
├── C端对话：Agent FillTemplate 工具（意图分支路由，反问补参数）
└── C端流程：flow「模板填写」节点（表单化参数，P4）

执行层（唯一）
    模板填写服务（确定性 pipeline）
    占位符注册表 → 逐槽位 KB 检索 → LLM 产 values JSON（一次批量调用）
    → 校验/兜底 → docxtpl(openpyxl) 渲染 → 生成稿落 MinIO

审核层（复用已有）
    生成稿进 flow 文件审核工作流（P4）；此前先支持 B 端任务列表下载审阅
```

## 3. 数据库表设计（3 张，前缀 `tpl_`，需进 db_models.py + migrate_db）

### 3.1 `tpl_template` 模板主表

| 字段 | 类型 | 说明 |
|---|---|---|
| id | varchar(32) PK | UUID |
| name | varchar(256) | 模板名（如"投标文件-技术标"） |
| description | text | 用途说明 |
| file_type | varchar(16) | `docx` / `xlsx` |
| status | varchar(16) | `draft` / `published` / `disabled` |
| latest_version | int | 当前版本号 |
| tenant_id | varchar(32) | 租户 |
| created_by / create_time / update_time | — | 常规 |

### 3.2 `tpl_template_version` 模板版本表（占位符清单随版本原子演进）

| 字段 | 类型 | 说明 |
|---|---|---|
| id | varchar(32) PK | UUID |
| template_id | varchar(32) idx | 归属模板 |
| version | int | 版本号，+1 递增 |
| original_file_id | varchar(64) | 用户上传的原件（MinIO） |
| render_file_id | varchar(64) | 预处理后带 `{{占位符}}` 的工作副本（MinIO，渲染引擎实际用这份） |
| placeholders | JSON | 占位符注册表，结构见下 |
| created_by / create_time | — | — |

`placeholders` JSON 元素结构：

```json
{
  "key": "project_name",          // 占位符名，模板中写作 {{project_name}}
  "name": "项目名称",              // 中文显示名
  "description": "填投标项目全称",  // 给 LLM 的填写说明
  "retrieval_query": "{project_name} 项目概况 建设内容",  // 检索意图，支持 {params.xxx} 引用任务参数
  "kb_ids": ["..."],               // 可选，默认用任务级 KB
  "fill_mode": "llm",             // llm=检索+生成 / param=任务参数直填 / manual=留空标待人工
  "constraints": {"type": "string", "max_length": 200, "enum": null, "unit": null},
  "required": true,                // 检索未命中且 required → 标「待人工」而非编造
  "top_k": 6                       // 该槽位检索条数
}
```

### 3.3 `tpl_fill_task` 填写任务表

| 字段 | 类型 | 说明 |
|---|---|---|
| id | varchar(32) PK | UUID |
| template_id / template_version_id | varchar(32) idx | 用的哪个版本 |
| kb_ids | JSON | 内容来源知识库（可多个） |
| params | JSON | 任务参数（项目名、日期等，供 retrieval_query 引用） |
| status | varchar(24) | `pending` / `retrieving` / `generating` / `rendering` / `done` / `partial` / `failed` |
| values | JSON | 每个占位符的最终值 + 状态（`filled` / `not_found` / `manual`） |
| evidence | JSON | 每个槽位引用的检索片段（doc_id + 片段文本，供审阅溯源） |
| result_file_id | varchar(64) | 生成稿（MinIO） |
| error | text | 失败原因 |
| source | varchar(16) | `web` / `chat` / `flow` |
| flow_instance_id | varchar(32) null | 若由流程发起，关联 flow 实例（P4） |
| tenant_id / created_by / create_time / update_time | — | 常规 |

## 4. 执行引擎（确定性 pipeline）

```
api/db/services/template_fill_service.py      # CRUD + 任务编排
rag/svr/template_fill/executor.py             # 执行 pipeline（无 Quart 依赖，可独立测）
rag/svr/template_fill/renderer.py             # docxtpl / openpyxl 渲染
```

Pipeline 步骤：

1. **检索**：逐槽位按 `retrieval_query` 调 Retrieval（复用 KB 检索），收集 top_k 片段。
2. **生成**：一次 LLM 调用批量产出 `{"key": "value", ...}` + 每格依据。System prompt 约束：只准依据证据作答、未找到返回 `null`、遵守 constraints。
3. **校验兜底**（代码端，LLM 安全网）：类型/字数/枚举校验；漏字段对缺失槽位单独重试一次；`required && null` → 状态 `partial`，该格标「待人工」。
4. **渲染**：
   - Word：`docxtpl`（Jinja2）跑 render_file → 生成稿。新依赖 `docxtpl`（需进 Docker 镜像依赖）。
   - Excel：`openpyxl`（已有依赖）按占位符坐标替换。
   - `manual` 格渲染为高亮占位文本 `【待人工：xxx】`，便于审核时一眼识别。
5. **落库**：生成稿传 MinIO，task 置 `done/partial`，evidence 存档。

### 模板预处理（B 端向导中一次性完成）

上传原件 → 后端抽取全文文本 → LLM 识别填写点（输出：锚文本、建议 key/名称/检索词/约束）→ B 端 UI 人工确认/增删改 → 保存时用 python-docx/openpyxl 把锚文本替换为 `{{key}}` 生成 render_file（run 级替换，复用 flow `_build_para_map` 经验）→ 从此永久复用。**原模板不允许改的场景不在本期范围**（那属于锚点定位方案 B）。

## 5. REST API（`api/apps/restful_apis/template_app.py`，复用 RBAC 鉴权）

```
# 模板管理（B端）
GET    /template/list                     # 列表（搜索/状态/分页）
POST   /template/upload                   # 上传原件 → 建 draft 模板+v1
POST   /template/detect-placeholders      # LLM 识别填写点（返回建议清单，不落库）
POST   /template/{id}/save-placeholders   # 人工确认后保存 → 生成 render_file → 可发布
POST   /template/{id}/publish             # draft → published
POST   /template/{id}/disable
GET    /template/{id}/versions            # 版本历史
GET    /template/{id}/preview             # 在线预览（高亮占位符）
GET    /template/{id}/file                # 下载原件/工作副本

# 填写任务
POST   /template/fill-task                # 发起（template_id+version, kb_ids, params, source）
GET    /template/fill-task/list           # 任务列表（状态筛选）
GET    /template/fill-task/{id}           # 详情（values + evidence）
POST   /template/fill-task/{id}/retry     # 重试（可只重跑失败槽位）
GET    /template/fill-task/{id}/download  # 下载生成稿
```

任务执行走后台异步（复用现有任务执行模式），接口立即返回 task_id，前端轮询/SSE 进度。

## 6. B端页面（`web/src/pages/template-fill/`，路由注册进 `web/src/routes.tsx`，RBAC 权限点控制）

| 页面 | 功能 |
|---|---|
| 模板列表 | 搜索/状态筛选/版本数/操作（填写、编辑、发布、禁用） |
| 上传向导（3 步） | ① 上传 docx/xlsx ② LLM 识别填写点 → 表格化人工确认（key/中文名/检索词/约束/必填，行内编辑）③ 预览高亮 → 保存发布 |
| 模板详情 | 版本历史切换；占位符配置编辑；在线预览（docx 高亮 `{{}}`）；**测试填写**（选 KB 试跑，逐格看值+证据，不产生正式任务） |
| 任务列表 | 状态/进度条；详情抽屉（values 逐格 + evidence 溯源）；下载生成稿；失败重试 |

前端文案只用中文（不加 en.ts），遵循现有 B 端组件体系。

## 7. C端接入点

| 入口 | 方式 | 阶段 |
|---|---|---|
| 对话 | `agent/tools/template_fill.py` 新增 `FillTemplate` 工具（列模板/发起填写/查进度/取文件链接），对话 Agent 挂载；缺参数时 Agent 反问补齐 | P3 |
| 流程 | flow 工作流新增「模板填写」节点类型，发起人表单选模板+KB；生成稿直接落该流程的文件审核环节，LLM 填写格与 `【待人工】` 格高亮 | P4 |

## 8. 实施阶段

| 阶段 | 内容 | 交付物 |
|---|---|---|
| P1 | 3 张表（进 migrate_db）+ 模板管理 API + B 端模板库页面（上传/识别/确认/发布/预览） | 模板可治理 |
| P2 | 执行引擎（检索→LLM→校验→渲染）+ 任务列表 + 测试填写 | B 端可出稿 |
| P3 | C 端对话 FillTemplate 工具 | 对话可出稿 |
| P4 | flow 模板填写节点 + 审核高亮闭环 | 流程可出稿 |

## 9. 部署清单（成套 SCP）

| 类型 | 路径 |
|---|---|
| ORM | `api/db/db_models.py` |
| Service | `api/db/services/template_fill_service.py` |
| REST API | `api/apps/restful_apis/template_app.py` |
| 执行引擎 | `rag/svr/template_fill/executor.py`、`renderer.py`、`__init__.py` |
| 前端 | `web/dist/`（build 后 tar） |
| 依赖 | 容器内 `pip install docxtpl`（并固化进 Dockerfile/依赖清单） |

部署后冒烟：`python -c "from api.db.services.template_fill_service import TemplateFillService; from rag.svr.template_fill.executor import TemplateFillExecutor; print('ok')"`，再跑一次 docx + xlsx 各一份的测试填写。

## 10. 风险与对策

| 风险 | 对策 |
|---|---|
| LLM 编造字段值 | 只准依据检索证据；`required` 未命中标待人工；evidence 强制留存 |
| Word 占位符被自动更正破坏 | 工作副本由程序生成（不经人手在 Word 里敲 `{{}}`） |
| Excel 合并单元格 | openpyxl 写锚点单元格（合并区左上角）；预处理时校验占位符不落在合并区非锚点位 |
| 大模板多占位符超上下文 | 检索片段按槽位裁剪 top_k；生成调用按占位符数量分批（每批 ≤30 格） |
| 模板更新后占位符失效 | 版本化 + 任务绑定版本；render_file 与 placeholders 原子保存 |
