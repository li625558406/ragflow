# 流程对话保存自治存储 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 流程 AI 对话与 c-chat 对话页彻底解耦——权威存储落 `flow_ai_chat`，影子会话 `source='flow'` 对话页不可见。

**Architecture:** flow_ai_chat 扩 2 列（user_id / template_fill_events）成为权威对话存储；流程会话在 api_4_conversation 表以 source='flow' 创建（纯运行时 DSL 缓存）；c-chat 会话列表 Service 层过滤；存量数据由 migrate_db 幂等回填。多轮续聊、发送链路、自动保存逻辑不变。

**Tech Stack:** Quart + Peewee (后端) / React + TanStack Query (前端)

**设计文档:** `docs/superpowers/specs/2026-09-11-flow-chat-save-design.md`

**注意:** 本项目后端 flow 模块无 pytest 基建（依赖 DB 上下文），验证采用容器 import 冒烟 + API 手工步骤；本地不跑 `npm run build`（用户规则：部署时才构建），前端验证靠 dev server + TypeScript 检查。

---

### Task 1: DB 模型扩展 + 增量迁移 + 存量回填

**Files:**
- Modify: `api/db/db_models.py`（FlowAiChat 类 :2304-2315；migrate_db 函数末尾）

- [ ] **Step 1: FlowAiChat 加 2 个字段**

`api/db/db_models.py` 的 `class FlowAiChat` 中，`session_id` 字段后追加：

```python
    user_id = CharField(max_length=32, null=False, default="", help_text="操作人 user_id（对话归属展示）")
    template_fill_events = TextField(null=False, default="", help_text="范本填写原始事件序列 JSON（刷新回放用）")
```

- [ ] **Step 2: migrate_db 追加迁移与回填**

`migrate_db()` 函数末尾（现有最后一条 `alter_db_add_column` 之后）追加：

```python
    # 2026-09-11 流程对话自治存储：flow_ai_chat 加操作人归属 + 范本填写事件
    alter_db_add_column(migrator, "flow_ai_chat", "user_id", CharField(max_length=32, null=False, default="", help_text="操作人 user_id（对话归属展示）"))
    alter_db_add_column(migrator, "flow_ai_chat", "template_fill_events", TextField(null=False, default="", help_text="范本填写原始事件序列 JSON（刷新回放用）"))
    try:
        # 存量回填（幂等）：流程影子会话打标 → 对话页签不可见
        DB.execute_sql("UPDATE api_4_conversation SET source = 'flow' WHERE source = 'agent' AND name LIKE '流程：%'")
        # 存量记录归属流程发起人（幂等）
        DB.execute_sql(
            "UPDATE flow_ai_chat c JOIN flow_instance f ON c.flow_id = f.id "
            "SET c.user_id = f.initiator_id WHERE c.user_id = ''"
        )
    except Exception as e:
        logging.exception("flow chat save backfill failed: %s", e)
```

- [ ] **Step 3: 验证语法与迁移可用**

Run: `python -c "import ast; ast.parse(open('api/db/db_models.py', encoding='utf-8').read()); print('syntax OK')"`
Expected: `syntax OK`

- [ ] **Step 4: Commit**

```bash
git add api/db/db_models.py
git commit -m "feat(flow): flow_ai_chat 加 user_id/template_fill_events + 存量影子会话打标迁移"
```

---

### Task 2: FlowAiChatService.add_record 增参

**Files:**
- Modify: `api/db/services/flow_service.py:336-348`（FlowAiChatService.add_record）

- [ ] **Step 1: 扩展 add_record 签名与 insert**

替换 `add_record` 方法为：

```python
    @classmethod
    @DB.connection_context()
    def add_record(cls, flow_id: str, version_id: str, instruction: str,
                   response: str, session_id: str = "", output_version_id: str = "",
                   user_id: str = "", template_fill_events: str = "") -> dict:
        rec = cls.insert(
            flow_id=flow_id,
            version_id=version_id,
            instruction=instruction,
            response=response,
            session_id=session_id,
            output_version_id=output_version_id,
            user_id=user_id,
            template_fill_events=template_fill_events,
        )
        return rec.__data__
```

- [ ] **Step 2: 验证语法**

Run: `python -c "import ast; ast.parse(open('api/db/services/flow_service.py', encoding='utf-8').read()); print('syntax OK')"`
Expected: `syntax OK`

- [ ] **Step 3: Commit**

```bash
git add api/db/services/flow_service.py
git commit -m "feat(flow): add_record 支持 user_id 与 template_fill_events 落库"
```

---

### Task 3: flow_app.py —— ai-record 落归属 + 新建影子会话端点

**Files:**
- Modify: `api/apps/restful_apis/flow_app.py`（imports :37-72；add_ai_record :1052-1112；文件末尾追加新端点）

- [ ] **Step 1: 补 imports**

文件头部 import 区追加（`import json` 放在 `import hashlib` 后的 import 块中按字母序；其余放 `from api.db.services.file_service import FileService` 之后）：

```python
import json
```

```python
from agent.canvas import Canvas
from api.db.services.api_service import API4ConversationService
from api.db.services.user_canvas_version import UserCanvasVersionService
from api.db.services.user_canvas_service import UserCanvasService
from common.misc_utils import get_uuid
```

注意：`common.misc_utils` 已 import 过 `thread_pool_exec`，把 `get_uuid` 合并进该行：
```python
from common.misc_utils import get_uuid, thread_pool_exec
```

若 `UserCanvasService` 的实际模块路径不同（上游在 `api/db/services/user_canvas_service.py`），以 agent_api.py:43 的实际 import 来源为准，保持一致。

- [ ] **Step 2: add_ai_record 落 user_id 与 template_fill_events**

`add_ai_record`（:1052）中，`else` 分支取 body 后追加一行读取；最终插入调用改为：

```python
        else:
            instruction = (body.get("instruction") or "").strip()
            response = (body.get("response") or "").strip()
            if not instruction:
                return _err("指令内容不能为空", 101)
            if not response:
                return _err("AI 回复内容不能为空", 101)
            version_id = body.get("version_id") or flow["current_version_id"]
            session_id = body.get("session_id") or ""
        user_id = current_user.id
        template_fill_events = body.get("template_fill_events") or ""
        if not isinstance(template_fill_events, str):
            template_fill_events = json.dumps(template_fill_events, ensure_ascii=False)
```

并把末尾插入调用改为：

```python
        else:
            record = FlowAiChatService.add_record(
                flow_id, version_id, instruction, response, session_id, output_version_id,
                user_id=user_id, template_fill_events=template_fill_events,
            )
```

说明：`user_id`/`template_fill_events` 取自 `current_user` / body，`record_id` 补建版本分支不需要这两个值（不插新记录）。

- [ ] **Step 3: 文件末尾追加影子会话端点**

```python
# ── 7.1 流程对话影子会话（source='flow'，对话页签不可见） ──────────
@manager.route("/flow/<flow_id>/chat/session", methods=["POST"])  # noqa: F821
@login_required
async def create_flow_chat_session(flow_id: str):
    """为当前用户创建流程专属 agent 会话（conversation.source='flow'）。
    影子会话仅作画布多轮续聊的运行时缓存；权威对话记录在 flow_ai_chat。"""
    try:
        flow = _require_viewer(_flow_dict(flow_id))
        body = await request.get_json(silent=True) or {}
        agent_id = (body.get("agent_id") or "").strip()
        if not agent_id:
            return _err("缺少 agent_id", 101)
        # 与 agent_api.create_agent_session（agent_api.py:240-267）同款初始化，
        # get_agent_dsl_with_release 返回 (cvs, dsl)，内部处理 agent 不存在（LookupError）
        cvs, dsl = UserCanvasService.get_agent_dsl_with_release(
            agent_id, False, current_user.id)
        canvas = Canvas(dsl, cvs.user_id, agent_id, canvas_id=cvs.id)
        canvas.globals["sys.user_id"] = current_user.id
        canvas.reset()
        session_id = get_uuid()
        conv = {
            "id": session_id,
            "name": f"流程：{flow['title']}"[:60],
            "dialog_id": agent_id,
            "user_id": current_user.id,
            "exp_user_id": current_user.id,
            "message": [{"role": "assistant", "content": canvas.get_prologue()}],
            "source": "flow",
            "dsl": json.loads(str(canvas)),
            "reference": [],
            "version_title": UserCanvasVersionService.get_latest_version_title(cvs.id),
        }
        API4ConversationService.save(**conv)
        return get_json_result(data={"session_id": session_id})
    except LookupError as e:
        return _err(str(e), 404)
    except (PermissionError, ValueError, RuntimeError) as e:
        return _action_error(e)
    except Exception as e:
        logger.exception(e)
        return _err(str(e))
```

- [ ] **Step 4: 验证语法**

Run: `python -c "import ast; ast.parse(open('api/apps/restful_apis/flow_app.py', encoding='utf-8').read()); print('syntax OK')"`
Expected: `syntax OK`

- [ ] **Step 5: Commit**

```bash
git add api/apps/restful_apis/flow_app.py
git commit -m "feat(flow): ai-record 落操作人归属 + 新增流程影子会话端点(source=flow)"
```

---

### Task 4: c-chat 会话列表过滤流程影子会话

**Files:**
- Modify: `api/db/services/api_service.py:98-144`（get_list / get_names）

- [ ] **Step 1: get_list 加过滤条件**

`get_list` 中 `if include_dsl:` 选出基础 query 后（两处 select 构造之后、`if id:` 之前）插入：

```python
        # 流程影子会话（source='flow'）仅作流程页运行时缓存，对话页签不展示
        sessions = sessions.where(peewee.fn.COALESCE(cls.model.source, '') != 'flow')
```

- [ ] **Step 2: get_names 加过滤条件**

`get_names` 的 `.where(...)` 内追加同一条件：

```python
    def get_names(cls, dialog_id, exp_user_id):
        fields = [cls.model.id, cls.model.name,]
        sessions = cls.model.select(*fields).where(
            cls.model.dialog_id == dialog_id,
            cls.model.exp_user_id == exp_user_id,
            peewee.fn.COALESCE(cls.model.source, '') != 'flow'
            ).order_by(cls.model.getter_by("create_date").desc())

        return list(sessions.dicts())
```

说明：用 `COALESCE` 防御历史 NULL source 行被误过滤（`source != 'flow'` 对 NULL 求值为 NULL 会把行丢掉）。

- [ ] **Step 3: 排查其它列表消费方**

Run: `grep -rn "API4ConversationService.get_list\|API4ConversationService.get_names" api/`
确认所有调用方都是「对话/会话列表」语义（过滤对它们都正确）；若发现统计类消费方依赖看到 flow 会话，另行评估（预期没有）。

- [ ] **Step 4: 验证语法并提交**

Run: `python -c "import ast; ast.parse(open('api/db/services/api_service.py', encoding='utf-8').read()); print('syntax OK')"`

```bash
git add api/db/services/api_service.py
git commit -m "feat(chat): 会话列表过滤 source=flow 的流程影子会话"
```

---

### Task 5: DELETE 会话接口防御流程影子会话

**Files:**
- Modify: `api/apps/restful_apis/agent_api.py:295-311`（delete_agent_session_item）

- [ ] **Step 1: 权限校验前加 source 拦截**

在 `if not conv or conv.dialog_id != agent_id:` 判断之后插入：

```python
    if conv.source == "flow":
        return get_json_result(
            data=False,
            message="流程会话请在流程页签中管理，不能在此删除。",
            code=RetCode.OPERATING_ERROR,
        )
```

- [ ] **Step 2: 验证语法并提交**

Run: `python -c "import ast; ast.parse(open('api/apps/restful_apis/agent_api.py', encoding='utf-8').read()); print('syntax OK')"`

```bash
git add api/apps/restful_apis/agent_api.py
git commit -m "feat(chat): 禁止经对话页 DELETE 接口删除流程影子会话"
```

---

### Task 6: 前端 service + types

**Files:**
- Modify: `web/src/services/flow-service.ts`（saveFlowAiRecord :250-267；新增 createFlowChatSession）
- Modify: `web/src/pages/c-chat/flow/flow-types.ts`（FlowAiChatItem :51-60）

- [ ] **Step 1: FlowAiChatItem 扩展字段**

```typescript
export interface FlowAiChatItem {
  id: string;
  flow_id: string;
  version_id: string;
  output_version_id: string;
  instruction: string;
  response: string;
  session_id: string;
  /** 操作人 user_id（存量迁移后非空，归属展示用） */
  user_id: string;
  /** 范本填写原始事件序列（刷新回放用） */
  template_fill_events?: unknown[];
  create_time: number;
}
```

- [ ] **Step 2: saveFlowAiRecord 增参 + 新增 createFlowChatSession**

```typescript
export async function saveFlowAiRecord(
  flowId: string,
  payload: {
    instruction: string;
    response: string;
    /** 传入时基于已存记录补建版本（不重复插记录） */
    record_id?: string;
    version_id?: string;
    session_id?: string;
    save_as_version?: boolean;
    /** 范本填写原始事件序列（template_fill_progress 事件的 data 载荷数组） */
    template_fill_events?: unknown[];
  },
): Promise<{ record: unknown; output_version_id: string }> {
  return apiFetch(`/flow/${flowId}/ai-record`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
}

/** 创建流程专属影子会话（conversation.source='flow'，对话页签不可见） */
export async function createFlowChatSession(
  flowId: string,
  agentId: string,
): Promise<{ session_id: string }> {
  return apiFetch(`/flow/${flowId}/chat/session`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ agent_id: agentId }),
  });
}
```

- [ ] **Step 3: Commit**

```bash
git add web/src/services/flow-service.ts web/src/pages/c-chat/flow/flow-types.ts
git commit -m "feat(web): flow-service 增加影子会话创建 + 记录扩展字段"
```

---

### Task 7: flow-ai-panel.tsx —— 会话来源/事件收集/刷新回放全部改走 flow 侧

**Files:**
- Modify: `web/src/pages/c-chat/flow/flow-ai-panel.tsx`

前置说明（给零上下文工程师）：
- 范本填写进度在 SSE 里以 `{event: 'template_fill_progress', data: <载荷>}` 出现，`answerList` 保存全部原始事件；回放函数 `replayTemplateFillEvents` 接收「data 载荷数组」。
- `currentUserId` state（:99-110）已存在，`sessionIdRef` 初值逻辑在 :117-124。

- [ ] **Step 1: imports 调整**

从 `@/services/flow-service` 的 import 列表中加入 `createFlowChatSession`。

- [ ] **Step 2: sessionIdRef 只恢复自己的会话**

替换 :116-124 为：

```typescript
  // 会话续接：只恢复【自己】保存记录里的 session_id（多人操作各自独立续聊）
  const sessionIdRef = useRef(
    (() => {
      for (let i = aiChats.length - 1; i >= 0; i--) {
        if (aiChats[i].session_id && aiChats[i].user_id === currentUserId) {
          return aiChats[i].session_id;
        }
      }
      return '';
    })(),
  );
```

（`currentUserId` 是上方 `useState(() => ...)` 解构出的字符串，此处直接可比。）

- [ ] **Step 3: 刷新回放改走 flow_ai_chat**

**整段删除** :203-241 的「刷新恢复」useEffect（fetch `/api/v1/agents/${agentId}/sessions/...` 那段），替换为：

```typescript
  // 刷新恢复：从本流程已保存记录的 template_fill_events 重放范本填写进度
  // （数据源为 flow 自持存储，不再依赖 agent 会话消息）。仅挂载时恢复一次。
  const replayRestoredRef = useRef(false);
  useEffect(() => {
    if (replayRestoredRef.current || lastTemplateFill || aiChats.length === 0) return;
    replayRestoredRef.current = true;
    for (let i = aiChats.length - 1; i >= 0; i--) {
      const restored = replayTemplateFillEvents(aiChats[i].template_fill_events);
      if (restored) {
        templateFillRef.current = restored;
        setLastTemplateFill(restored);
        break;
      }
    }
    // 仅挂载后 aiChats 首次到位时恢复一次
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [aiChats, lastTemplateFill]);
```

- [ ] **Step 4: 收集范本填写原始事件**

在「从流式事件中提取 session_id」effect（:243-247）附近新增 ref 与 effect：

```typescript
  // 范本填写原始事件序列（template_fill_progress 的 data 载荷）：随自动保存/手动保存
  // 落到 flow_ai_chat.template_fill_events；发送新一轮时清空
  const templateFillEventsRef = useRef<unknown[]>([]);
  useEffect(() => {
    const events = answerList
      .filter((e: any) => e?.event === 'template_fill_progress')
      .map((e: any) => e.data);
    if (events.length > 0) templateFillEventsRef.current = events;
  }, [answerList]);
```

并在 `handleSend` 的「新一轮发送：清空」段（:453-459，`templateFillRef.current = undefined;` 之后）加一行：

```typescript
      templateFillEventsRef.current = [];
```

- [ ] **Step 5: ensureSession 改调 flow 端点**

替换 :355-392 的 `ensureSession` 为：

```typescript
  // 无会话时经 flow 后端建影子会话（source='flow'，对话页签不可见），
  // 否则后端走无状态 fresh run 路径，多轮对话没有上下文延续。
  const ensureSession = useCallback(
    async (_query: string): Promise<boolean> => {
      if (sessionIdRef.current) return true;
      try {
        const result = await createFlowChatSession(flowId, agentId);
        if (result?.session_id) {
          sessionIdRef.current = result.session_id;
          return true;
        }
        setError('创建会话失败');
        return false;
      } catch (e: any) {
        setError(e?.message || '创建会话失败');
        return false;
      }
    },
    [agentId, flowId],
  );
```

（调用处 `const ok = await ensureSession(query);` 不变；`flowTitle` 不再被本组件使用的话，从 props 类型与解构中一并移除，并同步清理 flow-detail.tsx 的传参——先 grep 确认无其它引用再删。）

- [ ] **Step 6: 自动保存/手动保存附带事件**

自动保存 effect（:302-334）与 `handleSave` 的两处 `saveFlowAiRecord` 调用，payload 都加一行：

```typescript
          template_fill_events: templateFillEventsRef.current,
```

注意：`record_id` 补建版本分支（handleSave 开头 `if (asVersion && lastRecord)`）**不加**——该分支不插新记录，后端会忽略。

- [ ] **Step 7: TypeScript / lint 验证**

Run: `cd web && npx tsc --noEmit -p tsconfig.json 2>&1 | head -30`
Expected: 无本次改动文件相关的错误（存量错误忽略，只关注 flow-ai-panel / flow-detail / flow-service / flow-types）。

- [ ] **Step 8: Commit**

```bash
git add web/src/pages/c-chat/flow/flow-ai-panel.tsx
git commit -m "feat(web): 流程 AI 面板会话与范本进度回放全部改走 flow 自持存储"
```

---

### Task 8: flow-detail.tsx —— 对话气泡归属展示

**Files:**
- Modify: `web/src/pages/c-chat/flow/flow-detail.tsx`（ConversationView :858-964；调用处 :490）

- [ ] **Step 1: ConversationView 增加 authorNames prop**

签名与 props 类型追加：

```typescript
function ConversationView({
  chats,
  live,
  authorNames,
  extraAction,
  onConfirmSubmitted,
  onLivePreviewOpenChange,
}: {
  chats: FlowAiChatItem[];
  live: FlowLiveChat | null;
  /** user_id -> 昵称（对话归属展示） */
  authorNames?: Record<string, string>;
  ...
```

- [ ] **Step 2: 每轮元信息行展示操作人**

`chats.map` 内的元信息行（:911-918）改为：

```typescript
          <div className="flex items-center gap-2 px-1 text-[10px] text-[#aaa]">
            {(c.user_id && authorNames?.[c.user_id]) && (
              <span className="rounded bg-[#F5F5F5] px-1 text-[#888]">
                {authorNames[c.user_id]}
              </span>
            )}
            <span>{new Date(c.create_time).toLocaleString()}</span>
            {c.output_version_id && (
              <span className="rounded bg-[#EFF4FF] px-1 text-[#1a66fb]">
                已存为新版本
              </span>
            )}
          </div>
```

- [ ] **Step 3: 调用处传参**

:490 的 `<ConversationView` 增加一行 prop（`nicknameMap` 已在组件内 :183 构造）：

```typescript
              authorNames={Object.fromEntries(nicknameMap)}
```

- [ ] **Step 4: TypeScript 验证 + Commit**

Run: `cd web && npx tsc --noEmit -p tsconfig.json 2>&1 | head -30`

```bash
git add web/src/pages/c-chat/flow/flow-detail.tsx
git commit -m "feat(web): 流程对话气泡展示操作人昵称"
```

---

### Task 9: CHANGE.md 迭代记录 + 收尾

**Files:**
- Modify: `CHANGE.md`（顶部追加条目）

- [ ] **Step 1: 追加 CHANGE.md 条目**

```markdown
## 2026-09-11 流程对话保存自治存储（流程/对话页解耦）

**主题**：流程 AI 对话与 c-chat 对话页彻底解耦，权威存储归流程。

**核心变更**：
- flow_ai_chat 加 user_id / template_fill_events 列，成为权威对话存储（migrate_db 幂等迁移 + 存量回填）
- 新端点 POST /flow/<id>/chat/session：创建 source='flow' 影子会话（画布运行时缓存，对话页不可见）
- c-chat 会话列表（get_list/get_names）过滤 source='flow'；DELETE 会话接口拒绝删流程影子会话
- 前端 ensureSession 改走 flow 端点；刷新回放范本进度改从 flow_ai_chat 重放（删除 agent 会话 fetch）
- 对话气泡展示操作人昵称；存量「流程：xxx」会话自动打标隐藏

**遗留**：agent_id 仍读 localStorage（未列入本次痛点）；部署须成套 SCP（见设计文档 §10）。
```

- [ ] **Step 2: 全链路冒烟清单（交付给用户的验证步骤，写入回复）**

1. 后端 import 冒烟：`docker exec docker-ragflow-cpu-1 python -c "from api.apps.restful_apis.flow_app import manager; from api.db.services.api_service import API4ConversationService; print('OK')"`
2. 用户 A 在流程发两轮对话（含范本填写）→ 对话页签会话列表不出现「流程：xxx」
3. A 刷新页面 → 范本进度条与「查看填写内容」回看正常（来自 flow_ai_chat 回放）
4. 用户 B 打开同流程 → 能看到 A 的对话（带昵称）；B 发消息不继承 A 的画布上下文
5. B 存为新版本 → 版本时间线出现 AI 产出（record_id 补建链路正常）
6. 存量迁移：重启后端后，对话页签原「流程：xxx」会话消失

- [ ] **Step 3: Commit**

```bash
git add CHANGE.md
git commit -m "docs: 流程对话自治存储迭代记录"
```

- [ ] **Step 4: 调用 konus-code-review 审查（项目强制规则）**
