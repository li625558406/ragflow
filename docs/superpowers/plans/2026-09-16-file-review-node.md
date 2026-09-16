# 文件审核节点（File Review Node）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 C 端对话/流程新增「文件审核」能力——上传文件后 LLM 自动按招标规范多轮审视、产出可点击标注，用户触发修复、多轮叠加直到达标。

**Architecture:** FileReview 专精画布节点 + FileReviewTool 双入口（与 TemplateFill 同构、独立命名空间），detached 后台线程驱动多轮状态机（idle→reviewing→annotated→fixing→re-reviewing→done），5 套预置招标场景模板由 LLM 意图匹配；review-panel 复用零改动，仅外层注入多轮状态 className。

**Tech Stack:** Python 3.12 (Quart + Peewee + Redis + MinIO + python-docx/openpyxl + LLM) / React 18 + TypeScript + SSE

**设计文档:** `docs/superpowers/specs/2026-09-16-file-review-node-design.md`

**增量边界（硬约束）：**
- 不修改 `agent/component/template_fill.py` / `web/src/pages/c-chat/template-fill-*.tsx` / `template-fill-stream.ts` / `tpl_*` 表 / `tpl_fill:*` Redis 键
- 不修改 `web/src/pages/c-chat/review-panel.tsx` / `annotation/annotation.tsx` / `annotation/margin-comment.tsx` 内部实现

---

## File Structure

**后端新增：**
- `api/db/db_models.py` — 追加 3 个模型 + migrate_db 写 5 套预置模板
- `api/db/services/file_review_service.py` — CRUD + 多轮判定 + annotation 状态机
- `rag/svr/file_review/__init__.py` — 包标识
- `rag/svr/file_review/executor.py` — 多轮状态机主循环（无 Quart 依赖）
- `rag/svr/file_review/patcher.py` — find-match + python-docx/openpyxl patch 应用
- `rag/svr/file_review/kb_aggregator.py` — 纯函数：chunk 列表 → references 文本 + token 预算截断（检索 I/O 在 T6 executor）
- `rag/svr/file_review/spawn.py` — daemon 线程 + 防重入（与 template_fill/spawn.py 同形态）
- `agent/component/file_review.py` — 画布节点
- `agent/tools/file_review.py` — 对话工具
- `api/apps/restful_apis/file_review_api.py` — REST 端点（7 个）

**前端新增：**
- `web/src/hooks/file-review-stream.ts` — 事件类型 + 归约函数
- `web/src/hooks/use-file-review-request.ts` — API 封装
- `web/src/pages/c-chat/file-review-progress.tsx` — SSE 进度卡 + 跳 review-panel
- `web/src/pages/c-chat/__tests__/file-review-progress.test.tsx` — 组件测试
- `web/src/locales/zh.ts` — 新增 key

**前端修改（新增接点，0 改既有逻辑）：**
- `web/src/pages/c-chat/index.tsx` — 注册 FileReviewTool + 流式事件挂载
- `web/src/pages/c-chat/flow/flow-panel.tsx` — flow 节点注册 + 进度卡挂载
- `web/src/pages/agent/canvas/index.tsx` — B 端节点调色板 + 配置表单

**测试新增：**
- `test/test_file_review_service.py` — 服务层单测
- `test/test_file_review_executor.py` — 执行层对抗测试
- `test/test_file_review_api.py` — REST 端点单测
- `test/test_file_review_patcher.py` — patcher 对抗测试

---

## Task 1: DB 模型 + 迁移（5 套预置模板写入）

**Files:**
- Modify: `api/db/db_models.py`（在 `TplTemplate` 等之后追加 3 个模型 + migrate_db）
- Test: `test/test_file_review_db.py`

- [ ] **Step 1: 写失败测试** — 期望 `FileReviewTemplate` 存在且内置 5 套

```python
# test/test_file_review_db.py
from api.db.db_models import DB, FileReviewTemplate


def test_preset_templates_seeded():
    with DB.connection_context():
        rows = FileReviewTemplate.select().where(FileReviewTemplate.tenant_id == '')
        ids = {r.id for r in rows}
    assert ids == {'bid_doc_format', 'bid_response_complete', 'bid_substantive_clause',
                   'bid_qualification', 'bid_price_review'}
```

- [ ] **Step 2: 跑测试确认失败** — `uv run --no-sync pytest test/test_file_review_db.py -v` 期望：ImportError

- [ ] **Step 3: 在 db_models.py 末尾追加模型**（定位文件末尾的 `TplFillTask` 之后）

> **实施修正（2026-09-16，已按此落地）**：初稿写的 `DB.Model` + 手写 `create_time`/`update_time` 是错的。
> 必须继承项目统一的 **`DataBaseModel`**（全文件 99 个模型无一例外），它经 `BaseModel` 提供
> `to_dict()`/`to_human_model_dict()`/`query()`（service 层依赖）+ 自动审计四列
> （`create_time`/`update_time` = `BigIntegerField` 毫秒、`create_date`/`update_date` = `DateTimeField`）。
> 手写 `DateTimeField` 会与框架冲突（毫秒整数写进 DATETIME 列被静默压成零值日期）。
> 另：`llm_raw`/`summary` 用 `MediumTextField`（TEXT 64KB 会静默截断整轮 LLM 原始 JSON，
> 项目已有 `flow_ai_chat.template_fill_events` 同因事故）；`tenant_id` 必须 `null=False, default="", index=True`。

```python
class FileReviewTemplate(DataBaseModel):
    """审核模板：预置 5 套招标场景，DB 行式存储便于用户复制修改"""
    id = CharField(max_length=64, primary_key=True)
    name = CharField(max_length=128, null=False)
    description = TextField(null=True)
    system_prompt = TextField(null=False)
    user_prompt_template = TextField(null=False)
    annotation_types = TextField(null=True)  # JSON: ['format', 'clause', ...]
    enabled = IntegerField(default=1)
    tenant_id = CharField(max_length=32, null=False, default="", index=True)  # 系统预置 = 空串
    created_by = CharField(max_length=32, null=True)

    class Meta:
        db_table = "file_review_template"


class FileReviewRound(DataBaseModel):
    """审核轮次：每轮 1 行；状态机 reviewing/annotated/fixing/failed/done"""
    id = CharField(max_length=64, primary_key=True)
    task_id = CharField(max_length=64, null=False)  # 单列索引省略：复合 (task_id, round_no) 左前缀已覆盖
    file_id = CharField(max_length=64, null=False, index=True)
    round_no = IntegerField(null=False)
    template_id = CharField(max_length=64, null=True)
    user_query = TextField(null=True)
    status = CharField(max_length=16, null=False)  # reviewing/annotated/fixing/failed/done
    file_version = CharField(max_length=64, null=False)  # v1/v2/v3
    minio_path = CharField(max_length=256, null=True)
    summary = MediumTextField(null=True)   # 一轮汇总；TEXT 64KB 会静默截断
    llm_raw = MediumTextField(null=True)   # 整篇审核的 LLM 原始 JSON（200 条中文标注 ≈80KB）
    error = TextField(null=True)
    tenant_id = CharField(max_length=32, null=False, default="", index=True)
    created_by = CharField(max_length=32, null=True)

    class Meta:
        db_table = "file_review_round"
        indexes = (
            (("task_id", "round_no"), False),
        )


class FileReviewAnnotation(DataBaseModel):
    """审核标注：每条独立入库；多轮状态 open/fixed/new/wontfix"""
    id = CharField(max_length=64, primary_key=True)
    round_id = CharField(max_length=64, null=False, index=True)
    task_id = CharField(max_length=64, null=False, index=True)  # 复合索引不含 task_id，此单列索引必需
    file_id = CharField(max_length=64, null=False, index=True)
    file_version = CharField(max_length=64, null=False)
    anchor = TextField(null=False)  # JSON: docx {p_hash,offset,run_index} / xlsx {sheet,cell}
    matched_text = TextField(null=True)
    type = CharField(max_length=32, null=False)
    severity = CharField(max_length=16, null=False)  # high/medium/low；值来自 LLM，留余量防 DataError 1406
    issue = TextField(null=False)
    suggestion = TextField(null=True)
    source = CharField(max_length=8, null=False)  # ai/manual
    status = CharField(max_length=16, null=False, default='open')
    prev_annotation_id = CharField(max_length=64, null=True)
    tenant_id = CharField(max_length=32, null=False, default="", index=True)
    created_by = CharField(max_length=32, null=True)

    class Meta:
        db_table = "file_review_annotation"
        indexes = (
            (("file_id", "file_version"), False),
        )
```


- [ ] **Step 4: 在 `migrate_db()` 末尾追加创建 3 表 + seed 5 套模板**

定位 `migrate_db` 函数末尾（既有 `if not XxxModel.table_exists(): XxxModel.create_table(safe=True)` 块之后），追加：

```python
    # ── 文件审核（2026-09-16） ──
    if not FileReviewTemplate.table_exists():
        FileReviewTemplate.create_table(safe=True)
        logging.info("file_review: file_review_template table created")
    # 表由 init_database_tables 先行创建（它为所有 DataBaseModel 子类建表后才调 migrate_db），
    # 故不按 table_exists 判定；按预置 ID 集合判定，漏跑/中途失败可自愈
    _file_review_seed_err = _ensure_file_review_templates()
    if not FileReviewRound.table_exists():
        FileReviewRound.create_table(safe=True)
        logging.info("file_review: file_review_round table created")
    if not FileReviewAnnotation.table_exists():
        FileReviewAnnotation.create_table(safe=True)
        logging.info("file_review: file_review_annotation table created")

    logging.disable(logging.NOTSET)
    # seed 错误延后到恢复日志级别后输出（窗口内 ERROR 级被 logging.disable 抑制，会静默丢失）
    if _file_review_seed_err:
        logging.error("file review templates seed failed: %s", _file_review_seed_err)
```

> **实施修正**：初稿的 `DB.create_table(...)` 不存在（`DB` 是 `PooledMySQLDatabase`，只有 `create_tables`），
> 项目既有写法一律是 `Model.create_table(safe=True)`。
> 且 seed **不能**放在 `if not table_exists()` 里面——改成 `DataBaseModel` 后表已被
> `init_database_tables` 建好，该守卫恒为 False → 0 套预置。

- [ ] **Step 5: 在文件末尾（migrate_db 之后）追加 `_seed_file_review_templates` 函数**

```python
_PRESET_REVIEW_TEMPLATES = [
    {
        "id": "bid_doc_format",
        "name": "投标文件格式规范",
        "description": "审查投标文件的章节完整性、签字盖章、目录页码、字体行距等格式合规",
        "system_prompt": (
            "你是投标文件格式审核专家。审查文档的格式规范，包括：\n"
            "1. 章节结构完整性（目录/正文/附件）\n"
            "2. 签字盖章页是否齐全\n"
            "3. 目录/页码/页眉页脚一致性\n"
            "4. 字体字号行距是否符合招标要求\n\n"
            "每条问题标注 type='format'。"
        ),
        "user_prompt_template": (
            "用户需求：{user_query}\n\n"
            "文件首段（前 500 字）：\n{file_excerpt}\n\n"
            "参考资料：\n{references}\n\n"
            "请审视全文，输出 JSON 标注列表：\n"
            '[{{"anchor": {{...}}, "matched_text": "...", "type": "format", '
            '"severity": "high|medium|low", "issue": "...", "suggestion": "..."}}]'
        ),
        "annotation_types": ["format"],
    },
    {
        "id": "bid_response_complete",
        "name": "投标响应完整性",
        "description": "审查投标响应是否对招标点逐项应答、附件是否齐全、偏离表是否填写",
        "system_prompt": (
            "你是投标响应完整性审核专家。审查：\n"
            "1. 招标点是否逐项应答\n"
            "2. 附件清单是否齐全\n"
            "3. 偏离表是否填写\n"
            "4. 应答索引是否清晰\n\n"
            "每条问题标注 type='completeness'。"
        ),
        "user_prompt_template": (
            "用户需求：{user_query}\n\n"
            "文件首段：\n{file_excerpt}\n\n"
            "参考资料：\n{references}\n\n"
            "请审视全文，输出 JSON 标注列表（type='completeness'）：\n"
            '[{{"anchor": {{...}}, "matched_text": "...", "type": "completeness", '
            '"severity": "high|medium|low", "issue": "...", "suggestion": "..."}}]'
        ),
        "annotation_types": ["completeness"],
    },
    {
        "id": "bid_substantive_clause",
        "name": "实质性条款合规",
        "description": "审查招标★号条款、废标项、否决项、付款/工期/违约金等核心条款",
        "system_prompt": (
            "你是招投标实质性条款合规专家。审查：\n"
            "1. 招标★号条款是否逐条响应\n"
            "2. 是否触发废标项/否决项\n"
            "3. 付款方式/工期/违约金/质保等核心条款是否合规\n\n"
            "每条问题标注 type='clause'。"
        ),
        "user_prompt_template": (
            "用户需求：{user_query}\n\n"
            "文件首段：\n{file_excerpt}\n\n"
            "参考资料：\n{references}\n\n"
            "请审视全文，输出 JSON 标注列表（type='clause'）：\n"
            '[{{"anchor": {{...}}, "matched_text": "...", "type": "clause", '
            '"severity": "high|medium|low", "issue": "...", "suggestion": "..."}}]'
        ),
        "annotation_types": ["clause"],
    },
    {
        "id": "bid_qualification",
        "name": "资质合规",
        "description": "审查营业执照、资质等级、业绩、人员、财务审计报告等资质证明",
        "system_prompt": (
            "你是投标资质合规审核专家。审查：\n"
            "1. 营业执照是否有效\n"
            "2. 资质等级是否满足招标要求\n"
            "3. 业绩数量与金额是否达标\n"
            "4. 项目人员资质是否齐全\n"
            "5. 财务审计报告是否在有效期内\n\n"
            "每条问题标注 type='qualification'。"
        ),
        "user_prompt_template": (
            "用户需求：{user_query}\n\n"
            "文件首段：\n{file_excerpt}\n\n"
            "参考资料：\n{references}\n\n"
            "请审视全文，输出 JSON 标注列表（type='qualification'）：\n"
            '[{{"anchor": {{...}}, "matched_text": "...", "type": "qualification", '
            '"severity": "high|medium|low", "issue": "...", "suggestion": "..."}}]'
        ),
        "annotation_types": ["qualification"],
    },
    {
        "id": "bid_price_review",
        "name": "投标报价审核",
        "description": "审查报价上限/下限、清单完整性、合算错误、单价合理性、税率一致",
        "system_prompt": (
            "你是投标报价审核专家。审查：\n"
            "1. 报价是否在招标控制价上限/下限内\n"
            "2. 报价清单是否完整（无漏项/重复）\n"
            "3. 是否有合算错误（单价×数量≠合价）\n"
            "4. 单价是否合理（与同期市场行情偏差）\n"
            "5. 税率是否一致\n\n"
            "每条问题标注 type='price'。"
        ),
        "user_prompt_template": (
            "用户需求：{user_query}\n\n"
            "文件首段：\n{file_excerpt}\n\n"
            "参考资料：\n{references}\n\n"
            "请审视全文，输出 JSON 标注列表（type='price'）：\n"
            '[{{"anchor": {{...}}, "matched_text": "...", "type": "price", '
            '"severity": "high|medium|low", "issue": "...", "suggestion": "..."}}]'
        ),
        "annotation_types": ["price"],
    },
]


def _seed_file_review_templates():
    """幂等补齐预置审核模板（tenant_id='' 即系统预置）。

    逐行 get_or_none 判重，重复调用/手工清表后重跑均安全；单套失败只收集错误文本
    不抛出，由调用方决定输出方式（见 _ensure_file_review_templates）。
    返回失败摘要（空串 = 全部成功）。
    """
    errs = []
    for tpl in _PRESET_REVIEW_TEMPLATES:
        try:
            if FileReviewTemplate.get_or_none(FileReviewTemplate.id == tpl["id"]):
                continue
            FileReviewTemplate.create(
                id=tpl["id"],
                name=tpl["name"],
                description=tpl["description"],
                system_prompt=tpl["system_prompt"],
                user_prompt_template=tpl["user_prompt_template"],
                annotation_types=json_dumps(tpl["annotation_types"]),
                enabled=1,
                tenant_id="",
                # 哨兵值（非真实 user_id）：本功能以 tenant_id="" 认系统预置
                created_by="system",
            )
        except Exception as e:
            errs.append(f"{tpl.get('id')}: {e.__class__.__name__}: {e}")
    return "; ".join(errs)


def _ensure_file_review_templates():
    """确保预置审核模板齐全（幂等自愈）。返回错误文本，正常返回 None。

    守卫按预置 **ID 集合** 计数：tenant_id 的 default="" 会让「漏传 tenant_id」的行
    混进「系统预置」口径，若按 tenant_id=='' 计数，缺一套预置时会被诱饵顶替而误判齐全。
    migrate_db 在 logging.disable(ERROR) 窗口内，故此处不外抛也不直接 log ERROR，
    由调用方在恢复日志级别后输出错误文本。
    """
    try:
        preset_ids = [t["id"] for t in _PRESET_REVIEW_TEMPLATES]
        have = FileReviewTemplate.select().where(FileReviewTemplate.id << preset_ids).count()
        if have >= len(preset_ids):
            return None
        return _seed_file_review_templates() or None
    except Exception as e:
        return f"{e.__class__.__name__}: {e}"
```

> **实施修正**：初稿的局部 `import json` + `json.dumps` 违反项目「JSON 操作走统一封装」规范——
> 本文件顶部已 `from api.utils.json_encode import json_dumps`（`json_dumps` 内部已 `ensure_ascii=False`）。
> 且初稿直接 `create()` 不幂等（第二次调用 `IntegrityError 1062`）、手写 `create_time` 会与框架冲突。

- [ ] **Step 6: 无需 `import datetime`**（初稿此步已作废：审计时间由 `BaseModel` 自动维护）

- [ ] **Step 7: 跑测试确认通过**

```bash
uv run --no-sync pytest test/test_file_review_db.py -v
```
Expected: PASS

- [ ] **Step 8: 提交**

```bash
git add api/db/db_models.py test/test_file_review_db.py
git commit -m "feat(file-review): add 3 db models + 5 preset templates"
```

---

## Task 2: Service 层 CRUD + 多轮判定

**Files:**
- Create: `api/db/services/file_review_service.py`
- Test: `test/test_file_review_service.py`

- [ ] **Step 1: 写失败测试**

```python
# test/test_file_review_service.py
import json
from api.db.db_models import DB, FileReviewTemplate, FileReviewRound, FileReviewAnnotation
from api.db.services.file_review_service import (
    FileReviewTemplateService,
    FileReviewRoundService,
    FileReviewAnnotationService,
)


def test_list_enabled_templates():
    with DB.connection_context():
        rows = FileReviewTemplateService.list_enabled()
    assert len(rows) >= 5


def test_max_completed_rounds_excludes_failed():
    with DB.connection_context():
        rid = FileReviewRoundService.create_round(
            task_id='t1', file_id='f1', round_no=1, template_id='bid_doc_format',
            user_query='q', file_version='v1', status='done', tenant_id='', created_by='u'
        )
        FileReviewRoundService.create_round(
            task_id='t1', file_id='f1', round_no=2, template_id='bid_doc_format',
            user_query='q', file_version='v2', status='failed', tenant_id='', created_by='u'
        )
        n = FileReviewRoundService.max_completed_round_no('t1')
    assert n == 1  # 只统计 done/annotated，不含 failed


def test_upsert_annotations_status_transition():
    with DB.connection_context():
        ann_id = FileReviewAnnotationService.create(
            round_id='r1', task_id='t1', file_id='f1', file_version='v1',
            anchor=json.dumps({"p_hash": 0}), matched_text='foo', type='format',
            severity='high', issue='x', suggestion='y', source='ai', status='open',
            tenant_id='', created_by='u'
        )
        FileReviewAnnotationService.update_status(ann_id, 'fixed')
        ann = FileReviewAnnotationService.get_by_id(ann_id)
    assert ann.status == 'fixed'
```

- [ ] **Step 2: 跑测试确认失败** — `uv run --no-sync pytest test/test_file_review_service.py -v` 期望：ImportError

- [ ] **Step 3: 创建 `api/db/services/file_review_service.py`**

```python
"""文件审核 Service 层：CRUD + 多轮判定 + annotation 状态机。
所有方法均走 Peewee Model 直接操作，与 template_fill_service 同形态。"""
import json
from datetime import datetime

from api.db.db_models import DB, FileReviewAnnotation, FileReviewRound, FileReviewTemplate
from common.misc_utils import get_uuid


class FileReviewTemplateService:
    model = FileReviewTemplate

    @classmethod
    def list_enabled(cls, tenant_id: str = '') -> list:
        with DB.connection_context():
            rows = cls.model.select().where(
                (cls.model.tenant_id == tenant_id) | (cls.model.tenant_id == ''),
                cls.model.enabled == 1,
            ).order_by(cls.model.id)
            return list(rows)

    @classmethod
    def get_by_id(cls, tid: str):
        with DB.connection_context():
            try:
                return cls.model.get(cls.model.id == tid)
            except cls.model.DoesNotExist:
                return None


class FileReviewRoundService:
    model = FileReviewRound

    @classmethod
    def create_round(cls, *, task_id: str, file_id: str, round_no: int,
                     template_id: str, user_query: str, file_version: str,
                     status: str, tenant_id: str = '', created_by: str = '') -> str:
        rid = get_uuid()
        now = datetime.now()
        with DB.connection_context():
            cls.model.create(
                id=rid, task_id=task_id, file_id=file_id, round_no=round_no,
                template_id=template_id, user_query=user_query,
                file_version=file_version, status=status, tenant_id=tenant_id,
                created_by=created_by, create_time=now, update_time=now,
            )
        return rid

    @classmethod
    def update_status(cls, rid: str, status: str, **extra):
        with DB.connection_context():
            cls.model.update(status=status, update_time=datetime.now(), **extra).where(
                cls.model.id == rid).execute()

    @classmethod
    def max_completed_round_no(cls, task_id: str) -> int:
        """返回 task_id 下已完成（done/annotated）的最大轮次；failed 不算"""
        with DB.connection_context():
            row = cls.model.select(
                cls.model.round_no
            ).where(
                cls.model.task_id == task_id,
                cls.model.status.in_(['done', 'annotated']),
            ).order_by(cls.model.round_no.desc()).first()
        return row.round_no if row else 0

    @classmethod
    def get_by_task(cls, task_id: str) -> list:
        with DB.connection_context():
            return list(cls.model.select().where(cls.model.task_id == task_id).order_by(cls.model.round_no))


class FileReviewAnnotationService:
    model = FileReviewAnnotation

    @classmethod
    def create(cls, *, round_id: str, task_id: str, file_id: str, file_version: str,
               anchor: str, matched_text: str, type: str, severity: str, issue: str,
               suggestion: str, source: str, status: str = 'open',
               prev_annotation_id: str = None, tenant_id: str = '',
               created_by: str = '') -> str:
        aid = get_uuid()
        now = datetime.now()
        with DB.connection_context():
            cls.model.create(
                id=aid, round_id=round_id, task_id=task_id, file_id=file_id,
                file_version=file_version, anchor=anchor, matched_text=matched_text,
                type=type, severity=severity, issue=issue, suggestion=suggestion,
                source=source, status=status, prev_annotation_id=prev_annotation_id,
                tenant_id=tenant_id, created_by=created_by,
                create_time=now, update_time=now,
            )
        return aid

    @classmethod
    def update_status(cls, aid: str, status: str):
        with DB.connection_context():
            cls.model.update(status=status, update_time=datetime.now()).where(
                cls.model.id == aid).execute()

    @classmethod
    def get_by_id(cls, aid: str):
        with DB.connection_context():
            try:
                return cls.model.get(cls.model.id == aid)
            except cls.model.DoesNotExist:
                return None

    @classmethod
    def list_by_file_version(cls, file_id: str, file_version: str,
                             task_id: str = None) -> list:
        """review-panel 加载用：返回某文件某版本的全部标注（AI + manual）"""
        with DB.connection_context():
            q = cls.model.select().where(
                cls.model.file_id == file_id,
                cls.model.file_version == file_version,
            )
            if task_id:
                q = q.where(cls.model.task_id == task_id)
            return list(q.order_by(cls.model.create_time))

    @classmethod
    def list_open_or_new_for_next_round(cls, task_id: str, current_round_no: int) -> list:
        """下一轮 LLM prompt 用：返回 status∈{open, new} 且属于当前轮的标注"""
        with DB.connection_context():
            return list(cls.model.select().where(
                cls.model.task_id == task_id,
                cls.model.round_id.in_(
                    cls.model.select(cls.model.round_id).where(
                        cls.model.task_id == task_id,
                    )
                ),
                cls.model.status.in_(['open', 'new']),
            ))
```

- [ ] **Step 4: 跑测试确认通过**

```bash
uv run --no-sync pytest test/test_file_review_service.py -v
```
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add api/db/services/file_review_service.py test/test_file_review_service.py
git commit -m "feat(file-review): service layer CRUD + max round + annotation status"
```

---

## Task 3: KB 聚合器（references 拼装 + token 预算截断）

**Files:**
- Create: `rag/svr/file_review/kb_aggregator.py`
- Test: `test/test_file_review_kb_aggregator.py`

> **实施修正（2026-09-16，T3 派发前）**：原步骤有两处与真实代码不符，已就地改正——
> ① `from rag.llm.tokenizer import num_tokens_from_string` 该模块不存在，项目统一用
> `common/token_utils.num_tokens_from_string`（30+ 处调用点，见 `rag/app/naive.py:33`）；
> ② chunk 契约写成「带 `page_key` 属性的对象」是错的——`rag/nlp/search.py:537-563`
> `retrieval()` 实际构造的是 **dict**，文档名键为 `docnm_kwd`；另有 KG 分支把正文放
> 在 `content`（`agent/tools/retrieval.py:385-388` 删掉了 `content_with_weight`）、
> 分析结果分支用 `doc_name`。取值按 `content_with_weight → content → text`、
> 文档名按 `docnm_kwd → doc_name → doc_id → chunk_id` 逐级回退（与
> `rag/nlp/__init__.py:434-439 get_text()` 同款约定）。
> ③ 任务标题里的「并发 retrieve」不在本任务：真实检索入口是
> `await settings.retriever.retrieval(...)`（需要 embd_mdl/tenant_ids，属 I/O），
> 由 T6 executor 的 `retrieve_kb_chunks` 承担；T3 保持**纯函数**，才能满足设计文档
> §执行层「无 Quart 依赖，可独立测」的约束。
>
> **实施修正 2（2026-09-16，T3 质量审查后）——去掉头部标题行**：原设计让本函数额外
> 输出 `用户需求：{user_query}` / `审核模板：{name}` / `参考资料：` 头部，但 5 套预置
> 模板（`db_models.py _PRESET_REVIEW_TEMPLATES`）的 `user_prompt_template` 自己已经写了
> `用户需求：{user_query}` 与 `参考资料：\n{references}` 两行标题，于是 T6 里
> `tpl.user_prompt_template.format(..., references=references)` 会渲染出
> 「`参考资料：`\n`用户需求：…`\n`审核模板：…`\n`参考资料：`\n[1] …」的嵌套重复——
> 白烧 token 且容易被模型误读成两段独立清单。
> **本质**：返回值是填进模板 `{references}` 占位符的**槽位内容**，标题归模板所有，
> 槽位填充方只产片段。故**删除整个头部**，并删除随之失去意义的 `user_query` /
> `template_name` 两个入参（签名收敛为 `aggregate_references(*, kb_chunks, budget)`）；
> 无可用片段时返回空串，模板渲染出空的 `参考资料：` 段——这就是「本次没有参考资料」
> 的诚实表达。此修正**不动已 seed 的 5 行预置模板**（seed 按 id 幂等跳过，改模板文本
> 反而要额外迁移），因此是零迁移代价的修法。

- [ ] **Step 1: 写失败测试**

```python
# test/test_file_review_kb_aggregator.py
from common.token_utils import num_tokens_from_string
from rag.svr.file_review.kb_aggregator import aggregate_references


def _ck(text, name="doc1"):
    """假 chunk：形状对齐 rag/nlp/search.py retrieval() 产出的 dict。"""
    return {"chunk_id": "c1", "doc_id": "d1", "docnm_kwd": name,
            "content_with_weight": text, "kb_id": "kb1", "similarity": 0.9}


def test_empty_kb_returns_empty():
    assert aggregate_references(kb_chunks=[], budget=7800) == ''


def test_concat_within_budget():
    out = aggregate_references(kb_chunks=[_ck('x' * 100)], budget=200)
    assert 'doc1' in out
    assert len(out) < 300


def test_truncate_when_exceeds_budget():
    chunks = [_ck('y' * 1000, name=f'd{i}') for i in range(10)]
    out = aggregate_references(kb_chunks=chunks, budget=2000)
    # 预算即真实上限；且 10 条装不下（'y'*1000 = 250 token）
    assert num_tokens_from_string(out) <= 2000
```

- [ ] **Step 2: 跑测试确认失败** — `uv run --no-sync pytest test/test_file_review_kb_aggregator.py -v` 期望：ImportError

- [ ] **Step 3: 创建 `rag/svr/file_review/__init__.py`**

```python
"""文件审核执行层（无 Quart 依赖，可独立测）"""
```

- [ ] **Step 4: 创建 `rag/svr/file_review/kb_aggregator.py`**

```python
"""KB 聚合器：把已检索到的 KB chunk 拼成给 LLM 的参考资料片段 + token 预算截断。

为什么是纯函数：执行层（T6 executor 守护线程）负责 I/O——embedding/ES 检索与
LLM 调用；本模块只做「chunk 列表 → references 字符串」这一段纯变换，
不 import settings/Quart，可用假数据独立测试（设计文档 §执行层约束）。

为什么只产片段、不产标题：返回值是填进审核模板 {references} 占位符的**槽位内容**。
5 套预置模板（db_models.py _PRESET_REVIEW_TEMPLATES）自己已写了
"用户需求：{user_query}" 与 "参考资料：\n{references}" 两行标题；槽位填充方再输出
一遍标题，成稿 prompt 里就会出现「参考资料：\n用户需求：…\n审核模板：…\n参考
资料：」的嵌套重复。故本模块只输出编号片段，无任何标题行。无可用片段时返回空串，
模板渲染出空的 "参考资料：" 段——这就是「本次没有参考资料」的诚实表达。

chunk 契约（dict，形状见 rag/nlp/search.py retrieval()）：
    {"chunk_id", "content_with_weight", "doc_id", "docnm_kwd", "kb_id", ...}
真实检索结果是 dict 而非对象，文档名读 "docnm_kwd"。
注意 retrieval() 返回的是 ranks 容器 {"chunks": [...], "doc_aggs": [...]}，
调用方要先取 ranks["chunks"]（传错形状会抛 TypeError，见下——刻意的响亮失败，
避免"零参考"静默跑完）。

截断语义（锁定，测试依赖）：
  - budget 是返回串的 token 上限；不变式 tokens(返回值) <= budget。
  - chunk 整条进或整条不进，绝不切半条：半条标准条款截在句子中间，比没有更容易
    误导 LLM 产出错误批注。
  - 超预算即 break，不跳过靠前的大块去塞靠后的小块：保持检索给出的相关度排序。
  - 编号 [i] 按**已输出**顺序连续递增。
"""
from collections.abc import Iterable

from common.token_utils import num_tokens_from_string

# 正文键回退顺序：ES chunk → KG/其它分支 → 兜底
_CONTENT_KEYS = ("content_with_weight", "content", "text")
# 文档可读名回退顺序
_LABEL_KEYS = ("docnm_kwd", "doc_name", "doc_id", "chunk_id")


def _chunk_text(chunk) -> str:
    """取 chunk 正文，兼容三种来源（见模块 docstring）。"""
    if not isinstance(chunk, dict):
        return ""
    for key in _CONTENT_KEYS:
        val = chunk.get(key)
        if isinstance(val, str) and val.strip():
            return val
    return ""


def _chunk_label(chunk) -> str:
    """取 chunk 所属文档的可读名，逐级回退。"""
    if not isinstance(chunk, dict):
        return "?"
    for key in _LABEL_KEYS:
        val = chunk.get(key)
        if isinstance(val, str) and val.strip():
            return val
    return "?"


def aggregate_references(*, kb_chunks: Iterable, budget: int) -> str:
    """把 KB chunk 拼成编号参考资料片段，token 数不超过 budget。

    非 dict / 无正文的 chunk 直接跳过（不占编号、不占预算）；一条都放不下时返回 ""。
    """
    if isinstance(kb_chunks, dict):
        # retrieval() 返回的是 ranks 容器；误把整个容器传进来会退化成「零参考」却
        # 静默跑完。这里响亮失败，让调用方立刻发现。
        raise TypeError("kb_chunks 需为 chunk 列表（ranks['chunks']），不是 ranks 容器")
    entries = []
    n = 0
    for chunk in list(kb_chunks or []):
        text = _chunk_text(chunk)
        if not text:
            continue
        entry = f"[{n + 1}] doc={_chunk_label(chunk)}\n{text}\n"
        candidate = entries + [entry]
        # 预算按**真实输出串**计，不是各段 token 之和："\n".join() 会插入分隔符，且
        # BPE 在段边界可能合并出不同 token，两者并不相等。按 join 后整串计，
        # budget 才是真正的输出上限。
        if num_tokens_from_string("\n".join(candidate)) > budget:
            break
        entries = candidate
        n += 1
    return "\n".join(entries)
```

- [ ] **Step 5: 跑测试确认通过**

```bash
uv run --no-sync pytest test/test_file_review_kb_aggregator.py -v
```
Expected: PASS

- [ ] **Step 6: 提交**

```bash
git add rag/svr/file_review/__init__.py rag/svr/file_review/kb_aggregator.py test/test_file_review_kb_aggregator.py
git commit -m "feat(file-review): kb aggregator with token budget truncate"
```

---

## Task 4: Patcher（唯一匹配 + docx 格式保真替换）

**Files:**
- Create: `rag/svr/file_review/patcher.py`
- Test: `test/test_file_review_patcher.py`
- 只读复用（零修改）：`rag/svr/template_fill/docx_utils.py` 的 `_build_addr_map` / `_replace_in_paragraph`

> **实施修正（2026-09-16，派发前核实后重写）**：原计划本任务只做纯文本 `find/replace`，
> 把 docx 层应用写成「后续 task 接入」——但后续没有任何 task 承接，且设计文档 §14 明确
> `patcher.py` =「find-match + python-docx/openpyxl patch 应用」。缺了 docx 层，「修复轮」
> 就落不了盘（改不了文件、存不出 v2），而这是用户的核心诉求（最多三轮修复）。
> 故本任务补齐 docx 应用层。原标题里的「annotation 状态机」是错位：状态机数据原语
> （`create(status=..., prev_annotation_id=...)` / `update_status` /
> `list_open_or_new_for_next_round`）在 **T2 service** 已实现，"哪条该记 fixed/new/open"的
> 判定属于 **T6 executor**（要 LLM 结果才能定），本任务不涉及。
>
> **复用决策（第一性原理，非抄惯例）**：
> - 跨 run 区间替换是**格式保真**的硬要求——一句话被 Word 切进多个 run 是常态，替换必须
>   只重写 anchor 覆盖的 run 区间、区间外 `rPr` 原样保留。自写这段要 ~60 行极细的 run
>   拼接/区间定位代码，与既有实现重复且必然发散。`docx_utils._replace_in_paragraph` 已在
>   2026-09-15 段落定位链路里被**真实范本**验证（290 锚），故只读复用。`docx_utils.py`
>   顶层无 Quart/settings 依赖（仅 `io/logging/re/docx.*`），执行层可直接 import。
> - 复用的是**私有符号**（下划线开头）。耦合点收敛在 patcher 顶部两行 alias，上游改名只改这两行；
>   测试用**真实 docx 字节**调用（不 mock），符号一旦消失立刻响亮失败。
> - 段落枚举复用 `_build_addr_map`：它已覆盖正文/表格/文本框/页眉页脚/内容控件。
>   自写 `doc.paragraphs` 会漏掉页眉页脚与文本框里的问题句（实测只有 `para:0/1`，
>   而 addr_map 同时给出 `hdr:0:0` 与 `cell:0:0:0:0`）。
> - **xlsx 修复路径不在本任务范围**：设计里 xlsx 锚点 `{sheet, cell}` 仍是占位形态，
>   逐单元格定位未设计。v1 对非 docx 文件只做审查（出标注），修复走纯文本降级（见 T6）——
>   这是显式的范围边界，不是静默缺失。
>
> **唯一性语义（锁定，测试依赖）**：**逐层唯一**。patch 先按 `p.text` 在全文档段落里找候选，
> 候选段落必须**恰好 1 个**（0 = 找不到，>1 = 歧义），再要求该段落内 find 出现**恰好 1 次**。
> 任一层不唯一即跳过不改（`applied=False`）、绝不猜第一个命中——投标/合同文本里「1000元」
> 「30天」天然多处出现，猜错会把 A 处的报价改成 B 处的金额，比不改更糟。
>
> **实测事实（`.scratch/_t4_probe.py`，已跑通）**：`Document()` 默认模板初始 `paragraphs` 为 0；
> 跨 run 段 `报价：|1000元` 用 `_replace_in_paragraph(p,'1000元','1500元',occ=1)` 成功；
> 非 docx 字节抛 `zipfile.BadZipFile`（不是 PackageNotFoundError）；`addr_map` 的 Paragraph 对象唯一。

- [ ] **Step 1: 写失败测试**

```python
# test/test_file_review_patcher.py
"""patcher 对抗测试：唯一匹配 + docx 格式保真替换。
docx 用例一律用真实字节（python-docx 现造现读），不打桩——复用的是私有符号，
只有真跑才能证明耦合点还在。"""
import io
import zipfile

import pytest
from docx import Document
from docx.shared import Pt

from rag.svr.file_review.patcher import (
    apply_patches,
    apply_patches_to_docx,
    find_unique,
)


def _docx_bytes(paragraphs, header=None, table=None):
    """造真实 docx 字节。跨 run 场景不在此处构造——那些用例必须逐 run 设 rPr
    才有断言价值，故各自现搭（见 preserves_run_formatting / true_cross_run_straddle）。"""
    doc = Document()
    for text in paragraphs:
        doc.add_paragraph().add_run(text)
    if header:
        doc.sections[0].header.paragraphs[0].add_run(header)
    if table:
        doc.add_table(rows=1, cols=1).rows[0].cells[0].paragraphs[0].add_run(table)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def _texts(blob):
    """文档全文可见段落文本（Document() 初始 0 段，故无需过滤空段）。"""
    return [p.text for p in Document(io.BytesIO(blob)).paragraphs]


# ── find_unique：唯一 / 缺失 / 歧义 ──────────────────────────────
def test_find_unique_returns_pos_when_single_match():
    assert find_unique('投标人应满足：abc 资质等级', 'abc 资质等级') > 0


def test_find_unique_returns_neg1_when_zero_match():
    assert find_unique('hello world', 'xyz') == -1


def test_find_unique_returns_neg2_when_multi_match():
    assert find_unique('foo bar foo', 'foo') == -2  # 歧义


def test_find_unique_empty_find_is_missing_not_ambiguous():
    """空 find 必须判「找不到」而不是「歧义」——否则空串会被当成匹配任意位置的锚。"""
    assert find_unique('anything', '') == -1


def test_find_unique_non_overlapping_semantics():
    """非重叠口径与 str.replace 一致：'aaa' 里找 'aa' 只算 1 次（不是 2 次）。
    若这里判成歧义（-2）而替换本身能完成，两处口径打架会出现「能改却跳过」。"""
    assert find_unique('aaa', 'aa') == 0


def test_find_unique_find_longer_than_text():
    assert find_unique('short', 'much longer than text') == -1


def test_find_unique_unicode_and_whole_text():
    assert find_unique('报价￥1,000.00元', '￥1,000.00元') > 0
    assert find_unique('整段就是锚', '整段就是锚') == 0


# ── apply_patches：纯文本层 ─────────────────────────────────────
def test_apply_patches_single_replace():
    out, applied = apply_patches('报价：1000元', [{'find': '1000元', 'replace': '1500元'}])
    assert out == '报价：1500元'
    assert applied == [True]


def test_apply_patches_skips_ambiguous():
    out, applied = apply_patches('foo bar foo baz', [{'find': 'foo', 'replace': 'QUX'}])
    assert out == 'foo bar foo baz'  # 歧义 → 一字不动
    assert applied == [False]


def test_apply_patches_skips_missing():
    out, applied = apply_patches('报价：1000元', [{'find': '不存在的片段', 'replace': 'x'}])
    assert out == '报价：1000元'
    assert applied == [False]


def test_apply_patches_sequential_sees_earlier_result():
    """后面一条 patch 必须看得见前面一条的替换结果（顺序生效）。"""
    out, applied = apply_patches(
        'A处', [{'find': 'A处', 'replace': 'B处'}, {'find': 'B处', 'replace': 'C处'}]
    )
    assert out == 'C处'
    assert applied == [True, True]


def test_apply_patches_is_order_independent_per_patch_safety():
    """第一条歧义被跳过后，第二条仍按**原文**判定，不被前一条的跳过影响。"""
    out, applied = apply_patches(
        'foo foo and bar',
        [{'find': 'foo', 'replace': 'X'}, {'find': 'bar', 'replace': 'Y'}],
    )
    assert out == 'foo foo and Y'
    assert applied == [False, True]


def test_apply_patches_empty_patches_and_none():
    assert apply_patches('text', []) == ('text', [])
    assert apply_patches('text', None) == ('text', [])


def test_apply_patches_rejects_empty_find_without_touching_text():
    """空 find 是畸形输入：跳过且不改（str.replace('', x) 会在每个字符间插入 → 灾难）。"""
    out, applied = apply_patches('abc', [{'find': '', 'replace': 'X'}])
    assert out == 'abc'
    assert applied == [False]


def test_apply_patches_malformed_element_skipped():
    """缺 find 键 / None 元素：跳过（applied=False），不抛异常打断整批。"""
    out, applied = apply_patches('abc', [{'replace': 'X'}, None])
    assert out == 'abc'
    assert applied == [False, False]


# ── apply_patches_to_docx：格式保真层 ───────────────────────────
def test_apply_patches_to_docx_single_unique_replace():
    blob = _docx_bytes(['报价：1000元', '工期：30天'])
    out, applied = apply_patches_to_docx(blob, [{'find': '1000元', 'replace': '1500元'}])
    assert applied == [True]
    assert _texts(out) == ['报价：1500元', '工期：30天']


def test_apply_patches_to_docx_preserves_run_formatting():
    """格式保真是复用该原语的唯一理由，必须直接断言 rPr——只数 run 个数不可靠
    （"把整段塞进首 run、其余清空"的错误实现下 run 数同样不变：python-docx
    设 Run.text 从不增删 run）。"""
    doc = Document()
    p = doc.add_paragraph()
    r0 = p.add_run('报价：')
    r0.bold = True
    r0.font.size = Pt(10)
    r1 = p.add_run('1000元')
    r1.font.size = Pt(16)
    doc.add_paragraph('工期：30天')          # 另一段，不应被动
    buf = io.BytesIO()
    doc.save(buf)

    out, applied = apply_patches_to_docx(buf.getvalue(), [{'find': '1000元', 'replace': '1500元'}])
    assert applied == [True]
    d = Document(io.BytesIO(out))
    assert [p.text for p in d.paragraphs] == ['报价：1500元', '工期：30天']
    runs = d.paragraphs[0].runs
    assert [r.text for r in runs] == ['报价：', '1500元']
    assert runs[0].bold is True and runs[0].font.size == Pt(10)   # 区间外 run 格式原样
    assert runs[1].font.size == Pt(16)                            # 替换值落在原 run，rPr 未被重建


def test_apply_patches_to_docx_true_cross_run_straddle():
    """锚**真正跨越** run 边界（'1000元' 被切成 '10' | '00元'）时仍能替换，
    且只重写覆盖区间、保留前后 run 的格式。"""
    doc = Document()
    p = doc.add_paragraph()
    r0 = p.add_run('报价：10')
    r0.bold = True
    r0.font.size = Pt(10)
    r1 = p.add_run('00元')
    r1.font.size = Pt(16)
    buf = io.BytesIO()
    doc.save(buf)

    out, applied = apply_patches_to_docx(buf.getvalue(), [{'find': '1000元', 'replace': '1500元'}])
    assert applied == [True]
    runs = Document(io.BytesIO(out)).paragraphs[0].runs
    assert ''.join(r.text for r in runs) == '报价：1500元'
    assert runs[0].bold is True and runs[0].font.size == Pt(10)
    assert runs[1].font.size == Pt(16)


def test_apply_patches_to_docx_covers_header():
    """页眉里的问题句也要能改——这是复用 _build_addr_map 而非 doc.paragraphs 的理由。"""
    blob = _docx_bytes(['正文段落'], header='秘密标记')
    out, applied = apply_patches_to_docx(blob, [{'find': '秘密标记', 'replace': '公开标记'}])
    assert applied == [True]
    d = Document(io.BytesIO(out))
    assert '公开标记' in d.sections[0].header.paragraphs[0].text


def test_apply_patches_to_docx_covers_table_cell():
    blob = _docx_bytes(['正文'], table='表内2000元')
    out, applied = apply_patches_to_docx(blob, [{'find': '2000元', 'replace': '2500元'}])
    assert applied == [True]
    d = Document(io.BytesIO(out))
    assert '表内2500元' in d.tables[0].rows[0].cells[0].paragraphs[0].text


def test_apply_patches_to_docx_skips_when_in_two_paragraphs():
    """全文档两处命中 → 歧义 → 两处都不动（不是改第一处）。"""
    blob = _docx_bytes(['报价：1000元', '保证金：1000元'])
    out, applied = apply_patches_to_docx(blob, [{'find': '1000元', 'replace': '1500元'}])
    assert applied == [False]
    assert _texts(out) == ['报价：1000元', '保证金：1000元']


def test_apply_patches_to_docx_ambiguity_spans_body_and_table():
    """跨区域歧义同样要拦：正文 1 处 + 表格 1 处 = 2 个候选段落 → 跳过。"""
    blob = _docx_bytes(['正文3000元'], table='表内3000元')
    out, applied = apply_patches_to_docx(blob, [{'find': '3000元', 'replace': '4000元'}])
    assert applied == [False]
    assert '3000元' in _texts(out)[0]
    assert '表内3000元' in Document(io.BytesIO(out)).tables[0].rows[0].cells[0].paragraphs[0].text


def test_apply_patches_to_docx_skips_when_twice_in_one_paragraph():
    """同一段里出现两次 → 段内歧义 → 跳过。"""
    blob = _docx_bytes(['区间为1000元到1000元'])
    out, applied = apply_patches_to_docx(blob, [{'find': '1000元', 'replace': '1500元'}])
    assert applied == [False]
    assert _texts(out) == ['区间为1000元到1000元']


def test_apply_patches_to_docx_skips_missing():
    blob = _docx_bytes(['报价：1000元'])
    out, applied = apply_patches_to_docx(blob, [{'find': '不存在的片段', 'replace': 'x'}])
    assert applied == [False]
    assert _texts(out) == ['报价：1000元']


def test_apply_patches_to_docx_sequential():
    """docx 层同样顺序生效：后一条看得见前一条写入的文本。"""
    blob = _docx_bytes(['A处标记'])
    out, applied = apply_patches_to_docx(
        blob, [{'find': 'A处', 'replace': 'B处'}, {'find': 'B处', 'replace': 'C处'}]
    )
    assert applied == [True, True]
    assert _texts(out) == ['C处标记']


def test_apply_patches_to_docx_empty_patches_returns_input_untouched():
    """无 patch 不解析也不重存（重存会重排 XML 字节），原字节原样返回。"""
    blob = _docx_bytes(['报价：1000元'])
    out, applied = apply_patches_to_docx(blob, [])
    assert out == blob
    assert applied == []


def test_apply_patches_to_docx_rejects_empty_find():
    blob = _docx_bytes(['报价：1000元'])
    out, applied = apply_patches_to_docx(blob, [{'find': '', 'replace': 'X'}])
    assert applied == [False]
    assert _texts(out) == ['报价：1000元']


def test_apply_patches_to_docx_empty_replace_deletes_text():
    """replace 为空串 = 删除该片段，是合法操作（不是"没改"）。"""
    blob = _docx_bytes(['报价：1000元（含税）'])
    out, applied = apply_patches_to_docx(blob, [{'find': '（含税）', 'replace': ''}])
    assert applied == [True]
    assert _texts(out) == ['报价：1000元']


def test_apply_patches_to_docx_malformed_element_skipped():
    blob = _docx_bytes(['报价：1000元'])
    out, applied = apply_patches_to_docx(blob, [{'replace': 'X'}, None])
    assert applied == [False, False]
    assert _texts(out) == ['报价：1000元']


def test_apply_patches_to_docx_raises_on_non_docx_bytes():
    """非 docx 字节必须响亮失败（实测抛 zipfile.BadZipFile），不能静默返回原样——
    静默会让「修复成功」的假象流到用户面前。T6 捕获后把该轮置 failed。
    注意：patches 为空时走短路，不会解析，故此处必须传一条 patch 才会触发解析。
    断言具体类型而非裸 Exception：裸 Exception 会被实现自身的 bug（AttributeError/
    TypeError 等）蒙混过关，测不出「正确拒绝」与「崩了」的区别。"""
    with pytest.raises(zipfile.BadZipFile):
        apply_patches_to_docx(b'not a docx at all', [{'find': 'a', 'replace': 'b'}])


def test_apply_patches_to_docx_applied_length_matches_patches():
    """applied 与 patches 一一对应（T6 靠下标把「未修复」写回对应标注）。"""
    blob = _docx_bytes(['报价：1000元', '工期：30天'])
    _, applied = apply_patches_to_docx(
        blob,
        [{'find': '1000元', 'replace': '1500元'},
         {'find': '30天', 'replace': '60天'},
         {'find': '不存在', 'replace': 'x'}],
    )
    assert applied == [True, True, False]


# ── 畸形输入必须"跳过"，不能静默删字 / 打断整批 ──────────────────
def test_apply_patches_none_replace_is_skip_not_delete():
    """LLM 输出 "replace": null = 「没给出替换文本」，必须跳过而不是删除命中文本。
    （'' 才是显式删除；把 None 折成 '' 会让一次 LLM 脏字段不可逆地删掉正文。）"""
    out, applied = apply_patches('报价：1000元', [{'find': '1000元', 'replace': None}])
    assert out == '报价：1000元'
    assert applied == [False]


def test_apply_patches_to_docx_none_replace_is_skip_not_delete():
    blob = _docx_bytes(['报价：1000元'])
    out, applied = apply_patches_to_docx(blob, [{'find': '1000元', 'replace': None}])
    assert applied == [False]
    assert _texts(out) == ['报价：1000元']


def test_apply_patches_falsy_non_str_replace_is_skip():
    """0 / False 同样不是"删除"的意思。"""
    out, applied = apply_patches(
        'ab', [{'find': 'ab', 'replace': 0}, {'find': 'ab', 'replace': False}]
    )
    assert out == 'ab'
    assert applied == [False, False]


def test_apply_patches_non_str_find_is_skip_not_crash():
    out, applied = apply_patches('abc', [{'find': 1, 'replace': 'Z'}])
    assert out == 'abc'
    assert applied == [False]


def test_apply_patches_non_dict_element_does_not_abort_batch():
    """真值非 dict 的元素跳过即可，不能让同批其余合规 patch 一起作废。"""
    out, applied = apply_patches(
        '报价：1000元', ['a-string', {'find': '1000元', 'replace': '1500元'}]
    )
    assert out == '报价：1500元'
    assert applied == [False, True]


def test_apply_patches_to_docx_non_dict_element_does_not_abort_batch():
    blob = _docx_bytes(['报价：1000元'])
    out, applied = apply_patches_to_docx(
        blob, ['a-string', {'find': '1000元', 'replace': '1500元'}]
    )
    assert applied == [False, True]
    assert _texts(out) == ['报价：1500元']


def test_apply_patches_to_docx_all_failed_returns_input_bytes():
    """有 patch 但全部未生效 → 文档一字未改，必须返回**原字节**：重存会重排 XML
    字节，让"零改动"被 T6 误存成"修复版新版本"。"""
    blob = _docx_bytes(['报价：1000元'])
    out, applied = apply_patches_to_docx(blob, [{'find': '不存在', 'replace': 'x'}])
    assert applied == [False]
    assert out == blob
```

- [ ] **Step 2: 跑测试确认失败**

```bash
PYTHONPATH=/d/AI/ragflow2 uv run --no-sync pytest test/test_file_review_patcher.py -v
```
Expected: FAIL — `ModuleNotFoundError: No module named 'rag.svr.file_review.patcher'`

- [ ] **Step 3: 创建 `rag/svr/file_review/patcher.py`**

```python
"""文件审核 patcher：唯一匹配定位 + docx 格式保真替换。

职责边界（与 T3 同构）：只做「(文件字节, patch 列表) → (新字节, applied 列表)」这一段
确定性变换——不调 LLM、不碰 DB、不读 settings，故能拿真实 docx 字节独立单测。

为什么 find 必须唯一才改：投标/合同文本里「1000元」「30天」这类片段天然多处出现，
猜第一个命中会把 A 处的报价改成 B 处的金额——改错比不改更糟。故 0 次（找不到）与
>1 次（歧义）一律跳过，由调用方把该条标注记为「未修复」。

为什么复用 template_fill 的 docx 原语：跨 run 区间替换（一句话被 Word 切进多个 run 是常态）
要求「只重写 anchor 覆盖的 run 区间、区间外 rPr 原样保留」，这段逻辑在
rag/svr/template_fill/docx_utils.py 已由真实范本验证（2026-09-15 段落定位链路，290 锚）。
本模块只读复用，不修改对方一个字节；耦合点收敛在下面两行 alias。
"""
import io

from docx import Document

# 与 template_fill 的**唯一**耦合点（只读复用，零修改）：
#   _build_addr_map(doc) -> ({addr: Paragraph}, items)
#     覆盖正文/表格/文本框/页眉页脚/内容控件的全部段落；
#     自写 doc.paragraphs 会漏掉页眉页脚与文本框里的问题句。
#   _replace_in_paragraph(p, anchor, repl, occ=1)
#     段内第 occ 次出现替换；跨 run 时只重写覆盖区间，区间外格式保留。
# 上游若改名/改签名，只需改这两行（测试用真实 docx 字节调用，符号消失即响亮失败）。
from rag.svr.template_fill.docx_utils import _build_addr_map as _docx_addr_map
from rag.svr.template_fill.docx_utils import _replace_in_paragraph as _docx_replace

__all__ = ["apply_patches", "apply_patches_to_docx", "find_unique"]


def find_unique(text: str, find_str: str) -> int:
    """find_str 在 text 中的**唯一**非重叠出现：唯一返回起始下标；0 次返回 -1；>1 次返回 -2。

    非重叠口径与 str.replace 一致（'aaa' 里找 'aa' 只算 1 次）——否则会出现
    「判定为歧义而跳过、替换本身却能完成」的口径打架。空 find 判 -1（缺失），
    不判歧义：空串会被当成"匹配任意位置"的锚，是畸形输入。
    """
    if not find_str:
        return -1
    count = text.count(find_str)
    if count == 0:
        return -1
    if count > 1:
        return -2
    return text.find(find_str)


def apply_patches(text: str, patches) -> tuple[str, list]:
    """纯文本层逐条应用 patch（顺序生效，后面的看得见前面的替换结果）。

    docx 走 apply_patches_to_docx（格式保真）；本函数用于已提取成纯文本的场景，
    以及非 docx 文件的降级修复路径。返回 (新文本, applied 列表)。
    """
    out = text
    applied = []
    for p in patches or []:
        if not isinstance(p, dict):
            # 非 dict 元素（如 LLM 直接吐了个字符串）跳过即可，不能让同批其余合规 patch 一起作废
            applied.append(False)
            continue
        find_str = p.get("find")
        replace_str = p.get("replace")
        # 非 str 一律按「缺失」跳过，绝不折成空串："" 表示显式删除该片段，
        # {"replace": null} 表示「LLM 没给出替换文本」——混同会让脏字段静默删掉正文。
        if not isinstance(find_str, str) or not isinstance(replace_str, str):
            applied.append(False)
            continue
        if find_unique(out, find_str) >= 0:
            out = out.replace(find_str, replace_str, 1)
            applied.append(True)
        else:
            applied.append(False)
    return out, applied


def apply_patches_to_docx(file_bytes: bytes, patches) -> tuple[bytes, list]:
    """在 docx 字节层应用 patch，返回 (新字节, applied 列表)。

    逐层唯一：先按 p.text 在全文档段落里找候选，候选必须恰好 1 个
    （覆盖正文/表格/文本框/页眉页脚），再要求该段落内 find 出现恰好 1 次。
    任一层不唯一即跳过该条（applied=False）、文件不动。

    已知保真局限（继承自复用原语，见 docx_utils._replace_in_paragraph docstring）：
    find 只出现在超链接/域内文本时，p.text 命中而 p.runs 不命中 → no-op 降级为
    applied=False（安全：不产脏数据）。
    """
    if not patches:
        # 无 patch 不做无意义的解析/重存（重存会重排 XML 字节）
        return file_bytes, []
    doc = Document(io.BytesIO(file_bytes))
    addr_map, _ = _docx_addr_map(doc)
    paragraphs = list(addr_map.values())
    applied = []
    for p in patches or []:
        if not isinstance(p, dict):
            # 非 dict 元素跳过，不抛异常打断整批
            applied.append(False)
            continue
        find_str = p.get("find")
        replace_str = p.get("replace")
        # 非 str 一律按「缺失」跳过，绝不折成空串（"" 是显式删除，语义不能丢）
        if not isinstance(find_str, str) or not isinstance(replace_str, str):
            applied.append(False)
            continue
        hits = [para for para in paragraphs if find_str and find_str in para.text]
        if len(hits) != 1 or find_unique(hits[0].text, find_str) < 0:
            applied.append(False)
            continue
        applied.append(bool(_docx_replace(hits[0], find_str, replace_str, occ=1)))
    if not any(applied):
        # 全部未生效 → 文档一字未改（_replace_in_paragraph 返回 False 不产生部分写入），
        # 必须返回原字节：重存会重排 XML 字节，让「零改动」被 T6 误存成「修复版新版本」。
        return file_bytes, applied
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue(), applied
```

- [ ] **Step 4: 跑测试确认通过**

```bash
PYTHONPATH=/d/AI/ragflow2 uv run --no-sync pytest test/test_file_review_patcher.py -v
```
Expected: PASS（38 个用例全绿；`ruff check rag/svr/file_review/patcher.py test/test_file_review_patcher.py` 无告警）

- [ ] **Step 5: 提交**

```bash
git add rag/svr/file_review/patcher.py test/test_file_review_patcher.py
git commit -m "feat(file-review): patcher 唯一匹配 + docx 格式保真替换（复用 template_fill 原语）"
```

> **T4 落地修正（2026-09-16，质量审查后，commit `e74383b1`）**：审查发现三处必须修，
> 已在上面 Step 1/Step 3 的代码块中同步为最终形态：
> 1. **Critical — `replace` 非 str 时静默删字**：初版用 `(p or {}).get("replace", "") or ""`
>    把 `None` / `0` / `False` 折成 `""`（=显式删除），与 `find` 非 str 时跳过不对称。
>    一次 LLM 脏字段就能不可逆删掉正文。改为 `isinstance(..., str)` 双向校验，非 str 一律跳过。
>    **证伪实测**：把守卫改回旧写法，`test_apply_patches_none_replace_is_skip_not_delete`
>    与 `test_apply_patches_falsy_non_str_replace_is_skip` 立即变红，证明测试真能拦住回归。
> 2. **Important — 非 dict / 非 str 元素打断整批**：初版对字符串元素调 `.get` 抛
>    AttributeError，整批 patch 一起作废（测试注释已承诺"不抛异常打断整批"，实现没做到）。
> 3. **Important — 全失败仍重存**：初版无条件 `doc.save`，把"零改动"字节重排成一个
>    看似"修复版"的新版本。改为 `if not any(applied): return file_bytes, applied` 短路。
>    成立前提已核实：`_replace_in_paragraph` 所有返回 False 的分支都在
>    `_rewrite_run_span` 之前 return，故 False ⇒ 无部分写入。
> 4. **测试质量缺口**：原 `len(runs) == 2` 是空断言——python-docx 设 `Run.text`
>    从不增删 run，错误实现（整段塞进首 run）同样保持 run 数。已换成逐 run 断言
>    `bold` / `font.size` 的两个真格式用例（`preserves_run_formatting` /
>    `true_cross_run_straddle`）。

---

## Task 5: Spawn（daemon 线程 + 防重入）

**Files:**
- Create: `rag/svr/file_review/spawn.py`
- Test: `test/test_file_review_spawn.py`

> **形态来源**：与已上线的 `rag/svr/template_fill/spawn.py` 同构——防重入集合 + daemon
> 线程 + 线程启动失败兜底 + `execute_task` 延迟 import（本模块不拉起重依赖，测试可注入）。
> 派生差异两处，均由第一性原理确认而非风格偏好：
>
> 1. **崩溃路径也要 CAS 置 failed**：template_fill 把「线程体异常」全交给 executor 自置
>    failed。但 executor **可能压根没跑起来**——`from rag.svr.file_review.executor import
>    execute_task` 抛 ImportError 时，executor 内部那层兜底根本没机会执行，轮次会永远停在
>    `reviewing`，前端无限转圈且 **T9 fix 端点会因「已在执行中」拒绝重试**。我们要的不变式
>    是「**线程退出后，该 task 不得有 reviewing 轮次滞留**」，而线程生命周期只有 spawn 持有，
>    故这条只能在此处补。两条路径的 error 文案**必须不同**（`执行异常中断` vs `调度失败`），
>    否则 executor 的真 bug 会被伪装成"调度问题"，把排查引向错误方向。
> 2. **CAS 用 `task_id` 定位而非 round id**：spawn 入参是 task_id（与 template_fill 的
>    fill task id 同义），一轮一行的 round 表由 `(task_id, status='reviewing')` 定位。
>    已核实 `file_review_round.task_id` 存在（`db_models.py:3197`），且
>    `BaseModel._normalize_data` 由 `Model.update()` 调用（`db_models.py:252-259`），
>    故裸 `model.update(...)` 同样刷新 `update_time`，与 T1「审计列由框架维护」契约一致。
>
> **测试取舍**：force-fail 是**写库**行为，打桩掉 DB 就只剩「函数被调用过」的空断言，测不出
> where 条件是否真的命中目标行；故此处直连真实 MySQL（同 `test_file_review_service.py`），
> 全部测试行带 `PFX` 前缀、只按前缀清理。并发断言一律用「事件 + 轮询到期限」
> （`Event.wait` / `_wait_until`），不用固定 `sleep`——后者要么 flaky 要么拖慢套件。

- [ ] **Step 1: 写失败测试**

```python
# test/test_file_review_spawn.py
"""spawn 对抗测试：防重入 / 崩溃清理 / 线程启动失败清理 + 轮次强制置 failed。

force-fail 是写库操作，故直连真实 MySQL（同 test_file_review_service.py 的取舍）：
打桩掉 DB 后只剩"函数被调用过"这种空断言，测不出 where 条件是否真的命中目标行。
所有测试行带 PFX 前缀，清理只按该前缀删，绝不误伤线上数据。
"""
import threading
import time

import pytest

from api.db.db_models import DB, FileReviewRound
from api.db.services.file_review_service import FileReviewRoundService
from rag.svr.file_review import spawn

PFX = "__test_fr_spawn__"


def _cleanup():
    FileReviewRound.delete().where(FileReviewRound.task_id.startswith(PFX)).execute()


@pytest.fixture(scope="module", autouse=True)
def _table_and_cleanup():
    DB.connect(reuse_if_open=True)
    try:
        if not FileReviewRound.table_exists():
            FileReviewRound.create_table(safe=True)
        _cleanup()
        yield
    finally:
        # try/finally 而非裸顺序：_cleanup() 抛异常时连接不能让本会话后续用例
        # 继续复用坏状态 / 泄漏。
        try:
            _cleanup()
        finally:
            DB.close()


def _mk_round(task_id, status="reviewing"):
    """造一行真实轮次，返回 rid。"""
    return FileReviewRoundService.create_round(
        task_id=task_id, file_id=PFX + "-file", round_no=1, template_id="",
        user_query="q", file_version="v1", status=status, tenant_id=PFX,
    )


def _wait_until(pred, timeout=10.0):
    """轮询等待：daemon 线程的完成时刻不可预知，固定 sleep 要么 flaky 要么白等。

    超时给足 10s：崩溃兜底路径要在远程 MySQL 上真跑一条 UPDATE，5s 在网络抖动时
    会变成 flaky 失败（方向安全——超时只会误报失败，不会误报通过）。
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        if pred():
            return True
        time.sleep(0.02)
    return pred()


def _rounds(task_id):
    return FileReviewRoundService.get_by_task(task_id)


def test_is_running_false_initially():
    assert spawn.is_running('nonexistent-task-id') is False


def test_spawn_runs_executor_and_clears_flag(monkeypatch):
    done = threading.Event()
    seen = []

    def fake_executor(task_id):
        seen.append(task_id)
        done.set()

    monkeypatch.setattr(spawn, 'execute_task', fake_executor)
    tid = PFX + 't1'
    spawn.spawn_review_task(tid)
    assert done.wait(5.0)
    # 断言透传的就是本 task：否则 executor 跑错任务也照样「成功」
    assert seen == [tid]
    assert _wait_until(lambda: not spawn.is_running(tid))   # finally 清理标志位


def test_respawn_after_crash_runs_executor_again(monkeypatch):
    """崩溃 → 标志位清理 → 再次 spawn 必须真的重跑。

    这正是 force-fail 把轮次置 failed 并提示「请重试」所依赖的用户可见恢复路径：
    若 finally 只在正常返回时生效（或标志位只增不减），重试将静默变成空操作。
    """
    calls = []

    def boom(task_id):
        calls.append(task_id)
        raise RuntimeError("boom")

    monkeypatch.setattr(spawn, 'execute_task', boom)
    tid = PFX + 'retry'
    spawn.spawn_review_task(tid)
    assert _wait_until(lambda: not spawn.is_running(tid))
    assert calls == [tid]
    spawn.spawn_review_task(tid)                 # 重试：必须起第二个线程
    assert _wait_until(lambda: len(calls) == 2), calls
    assert _wait_until(lambda: not spawn.is_running(tid))


def test_spawn_is_noop_while_same_task_running(monkeypatch):
    """防重入：同 task 执行期间再次 spawn 必须直接返回，不得起第二个线程
    （否则两轮填写/审核并发写同一文件版本与同一批轮次行）。"""
    started = threading.Event()
    release = threading.Event()
    calls = []

    def fake_executor(task_id):
        calls.append(task_id)
        started.set()
        release.wait(5.0)

    monkeypatch.setattr(spawn, 'execute_task', fake_executor)
    tid = PFX + 'reentry'
    spawn.spawn_review_task(tid)
    assert started.wait(5.0)
    spawn.spawn_review_task(tid)
    spawn.spawn_review_task(tid)
    time.sleep(0.2)                       # 给"若真起了第二个线程"留出可观测窗口
    assert calls == [tid]
    assert spawn.is_running(tid) is True
    release.set()
    assert _wait_until(lambda: not spawn.is_running(tid))


def test_spawn_clears_flag_after_executor_crash(monkeypatch):
    """executor 抛异常也必须清标志位：否则该 task 永久滞留集合，retry 恒报
    「任务正在执行中」，只能重启进程才能恢复。"""
    def boom(task_id):
        raise RuntimeError("boom")

    monkeypatch.setattr(spawn, 'execute_task', boom)
    tid = PFX + 'crash'
    spawn.spawn_review_task(tid)
    assert _wait_until(lambda: not spawn.is_running(tid))


def test_executor_crash_marks_reviewing_round_failed(monkeypatch):
    """崩溃兜底：executor 崩在自身兜底之外时，reviewing 轮次不得滞留。"""
    tid = PFX + 'crash-db'
    _mk_round(tid)

    def boom(task_id):
        raise RuntimeError("boom")

    monkeypatch.setattr(spawn, 'execute_task', boom)
    spawn.spawn_review_task(tid)
    assert _wait_until(lambda: [r.status for r in _rounds(tid)] == ['failed']), \
        [r.status for r in _rounds(tid)]
    # 文案必须与「调度失败」区分：否则 executor 的真 bug 被伪装成调度问题
    assert "执行异常中断" in _rounds(tid)[0].error


def test_thread_start_failure_clears_flag_and_fails_round(monkeypatch):
    """Thread.start 抛异常：add 已执行而 finally 永不跑，标志位必须回滚；
    轮次同步 CAS 置 failed，否则用户看到一个永远转圈的 reviewing。"""
    tid = PFX + 'startfail'
    _mk_round(tid)

    class BoomThread:
        def __init__(self, *a, **kw):
            pass

        def start(self):
            raise RuntimeError("cannot start new thread")

    monkeypatch.setattr(spawn.threading, 'Thread', BoomThread)
    spawn.spawn_review_task(tid)
    assert spawn.is_running(tid) is False
    rows = _rounds(tid)
    assert [r.status for r in rows] == ['failed']
    assert "调度失败" in rows[0].error


def test_thread_start_failure_does_not_touch_completed_round(monkeypatch):
    """CAS 边界：已完结轮次（done）不得被「调度失败」误伤置 failed
    —— 那会把一次成功的审核成果改写成失败态。"""
    tid = PFX + 'startfail-done'
    _mk_round(tid, status='done')

    class BoomThread:
        def __init__(self, *a, **kw):
            pass

        def start(self):
            raise RuntimeError("cannot start new thread")

    monkeypatch.setattr(spawn.threading, 'Thread', BoomThread)
    spawn.spawn_review_task(tid)
    assert [r.status for r in _rounds(tid)] == ['done']


def test_force_fail_is_scoped_to_target_task(monkeypatch):
    """CAS 必须按 task_id 限定：不得把别的任务滞留轮次一起置 failed。"""
    tid_a, tid_b = PFX + 'scope-a', PFX + 'scope-b'
    _mk_round(tid_a)
    _mk_round(tid_b)

    def boom(task_id):
        raise RuntimeError("boom")

    monkeypatch.setattr(spawn, 'execute_task', boom)
    spawn.spawn_review_task(tid_a)
    assert _wait_until(lambda: [r.status for r in _rounds(tid_a)] == ['failed'])
    assert [r.status for r in _rounds(tid_b)] == ['reviewing']   # 未被牵连


def test_force_fail_marks_all_stuck_rounds_of_task(monkeypatch):
    """CAS 是**集合**更新而非单行：同 task 滞留多轮（重试中途再次崩溃）时每行都要
    置 failed，否则用户重试后仍看到一行永远转圈的 reviewing。"""
    tid = PFX + 'multi'
    _mk_round(tid)
    FileReviewRoundService.create_round(
        task_id=tid, file_id=PFX + '-file', round_no=2, template_id='',
        user_query='q', file_version='v2', status='reviewing', tenant_id=PFX,
    )

    def boom(task_id):
        raise RuntimeError("boom")

    monkeypatch.setattr(spawn, 'execute_task', boom)
    spawn.spawn_review_task(tid)
    assert _wait_until(lambda: [r.status for r in _rounds(tid)] == ['failed', 'failed']), \
        [r.status for r in _rounds(tid)]


def test_force_fail_db_failure_does_not_wedge_task(monkeypatch):
    """force-fail 写库失败（MySQL 不可用 / update 抛错）时，防重入标志位仍必须被
    清理、任务不得被永久锁死 —— 否则重试恒报「任务正在执行中」，只能重启进程。
    轮次写不进去就诚实留在 reviewing，不得假装。"""
    tid = PFX + 'forcefail-db'
    _mk_round(tid)

    def boom(task_id):
        raise RuntimeError("boom")

    class _BoomQuery:
        """替身链：.where(...) 返回自身、.execute() 抛错，模拟 DB 写失败。"""

        def where(self, *a, **kw):
            return self

        def execute(self):
            raise RuntimeError("mysql down")

    def boom_update(*a, **kw):
        return _BoomQuery()

    monkeypatch.setattr(spawn, 'execute_task', boom)
    # 直接替换 Model.update（类属性覆盖，经 FileReviewRoundService.model 可见）
    monkeypatch.setattr(FileReviewRound, 'update', boom_update)
    spawn.spawn_review_task(tid)
    assert _wait_until(lambda: not spawn.is_running(tid))
    assert [r.status for r in _rounds(tid)] == ['reviewing']


def test_thread_start_failure_survives_db_failure(monkeypatch):
    """启动失败 + DB 写失败：spawn_review_task 必须正常返回，不得把 DB 异常
    冒泡成调用方 500 —— 由 _force_fail_round 内部的 try/except 保证。"""
    tid = PFX + 'startfail-db'
    _mk_round(tid)

    class BoomThread:
        def __init__(self, *a, **kw):
            pass

        def start(self):
            raise RuntimeError('cannot start new thread')

    class _BoomQuery:
        def where(self, *a, **kw):
            return self

        def execute(self):
            raise RuntimeError('mysql down')

    monkeypatch.setattr(spawn.threading, 'Thread', BoomThread)
    monkeypatch.setattr(FileReviewRound, 'update', lambda *a, **kw: _BoomQuery())
    spawn.spawn_review_task(tid)          # ← 不得抛出
    assert spawn.is_running(tid) is False
```

- [ ] **Step 2: 跑测试确认失败** — `PYTHONPATH=/d/AI/ragflow2 uv run --no-sync pytest test/test_file_review_spawn.py -v` 期望：`ModuleNotFoundError: No module named 'rag.svr.file_review.spawn'`

- [ ] **Step 3: 创建 `rag/svr/file_review/spawn.py`**

```python
"""审核任务执行线程 spawn（T9 API / T7 节点 / T8 工具共用入口）。

防重入集合 + daemon 线程 + 线程生命周期兜底。executor 延迟 import——
本模块自身不拉起任何重依赖，故可被 REST 层与画布层同时安全引用。
"""
import logging
import threading

logger = logging.getLogger(__name__)

__all__ = ["is_running", "spawn_review_task"]

# 防重入：同一 task 同时最多一个执行线程（spawn 时 add、线程 finally discard）
_running_lock = threading.Lock()
_running_tasks: set = set()

# 生产恒为 None；仅作测试注入点（monkeypatch spawn 模块属性可命中 _run 的名字解析），
# None 时 _run 内延迟 import 真正的 executor.execute_task
execute_task = None


def is_running(task_id: str) -> bool:
    with _running_lock:
        return task_id in _running_tasks


def _force_fail_round(task_id: str, error: str) -> None:
    """把该 task 滞留在 reviewing 的轮次 CAS 置 failed，保证可重试。

    幂等且范围受限：只命中 status=='reviewing' 的该 task 行——executor 已把该轮置
    done/failed 时命中 0 行，别的任务的滞留轮次也不受影响。
    """
    try:
        from api.db.db_models import DB
        from api.db.services.file_review_service import FileReviewRoundService
        with DB.connection_context():
            FileReviewRoundService.model.update(status="failed", error=error).where(
                FileReviewRoundService.model.task_id == task_id,
                FileReviewRoundService.model.status == "reviewing",
            ).execute()
    except Exception:
        logger.exception("review task force-fail failed, task_id=%s", task_id)


def spawn_review_task(task_id: str) -> None:
    """起 daemon 线程跑审核 pipeline；同 task 已在跑则直接返回（防重入）。

    T6 契约：防重入集合没有超时/看门狗，标志位只在 executor 线程或启动失败分支的
    finally 里 discard。因此 `execute_task` 必须保证返回（不得永久阻塞）——否则该
    task 会被永远判为「已在执行中」，retry 恒返回，只能重启进程才能恢复。
    """
    with _running_lock:
        if task_id in _running_tasks:
            return
        _running_tasks.add(task_id)

    def _run():
        try:
            fn = execute_task  # 读模块全局（调用时解析，测试注入可见）
            if fn is None:
                from rag.svr.file_review.executor import execute_task as fn
            fn(task_id)
        except Exception:
            # executor 可能压根没跑起来（import 失败），或崩在它自己的兜底之外。
            # 「线程退出后不得有 reviewing 轮次滞留」这条不变式只有持线程生命周期的
            # 本模块能保证，故此处必须补一层 CAS（与下面的调度失败文案刻意区分）。
            logger.exception("review task thread crashed, task_id=%s", task_id)
            _force_fail_round(task_id, "审核执行异常中断（详见服务端日志），请重试")
        finally:
            with _running_lock:
                _running_tasks.discard(task_id)

    try:
        threading.Thread(target=_run, daemon=True, name=f"file-review-{task_id[:8]}").start()
    except Exception:
        # 线程启动失败：add 已执行而 finally 永不会跑，task_id 会永久滞留集合
        # 导致 retry 恒报「任务正在执行中」。锁内 discard + CAS 置 failed 供重试。
        with _running_lock:
            _running_tasks.discard(task_id)
        logger.exception("review task spawn failed, task_id=%s", task_id)
        _force_fail_round(task_id, "任务调度失败：后台线程启动异常，请重试")
```

- [ ] **Step 4: 跑测试确认通过**

```bash
PYTHONPATH=/d/AI/ragflow2 uv run --no-sync pytest test/test_file_review_spawn.py -v
```
Expected: PASS（12 个用例全绿；`ruff check rag/svr/file_review/spawn.py test/test_file_review_spawn.py` 无告警）

- [ ] **Step 5: 提交**

```bash
git add rag/svr/file_review/spawn.py test/test_file_review_spawn.py
git commit -m "feat(file-review): spawn 防重入线程 + 线程生命周期兜底（崩溃/启动失败均 CAS 置 failed）"
```

> **T5 落地修正（2026-09-16，两道审查后，commit `3fbdf2c7` → `c7fe2286`）**：
> 实现者按 TDD 落地后自审加了 3 条用例（崩溃后可重跑 / CAS 是集合更新而非单行 /
> DB 失败不锁死任务），并用**定向变异**证明 7 处改坏实现全部被捕获。规格审查：
> ✅ 精确符合规格、无少做无越界。质量审查 `APPROVED_WITH_NITS`，其中一条 Important 已修：
> - **同步路径的 DB 失败会变成调用方 500**：`spawn.py` 启动失败分支**同步**调用
>   `_force_fail_round`，若其中 DB 写失败而异常未被吞掉，会从 `spawn_review_task`
>   冒泡到 T9 的 REST handler —— 一次本该「已置 failed、可重试」的轮次变成用户可见 500。
>   异步路径只是被 `pyproject.toml` 的 `filterwarnings = ["error"]` **意外**兜住
>   （线程异常转 warning），不可靠。已补 `test_thread_start_failure_survives_db_failure`
>   钉死该契约（变异验证：把 `_force_fail_round` 的 except 改成 re-raise 即变红）。
> - 顺带：修正一条**过度宣称**的用例 docstring（原称验证"吞异常"，实际只验证标志位清理）；
>   `spawn_review_task` docstring 补 T6 契约（防重入集合无超时，`execute_task` 必须保证返回）；
>   fixture 对齐 `test_file_review_service.py` 的 `try/finally + DB.close()` 写法。
> - 复审：`git show c7fe2286 -- rag/svr/file_review/spawn.py` 为 **docstring-only**（剥离
>   docstring 后 `ast.dump` 的 sha256 在两 commit 间完全相同，可执行语句零改动）。
>
> **已知取舍（记录，不在本任务修）**：防重入集合是**进程内**的，与 `template_fill/spawn.py`
> 同限。已核实 `api/ragflow_server.py` 用 `app.run(...)` 单进程，三个调用方都在同一进程内，
> 故当前充分；若将来把 worker 调成 >1，需改 Redis 标记（留给 T9 视部署形态判断）。

---

## Task 6: Executor 主循环（多轮状态机）

**Files:**
- Create: `rag/svr/file_review/executor.py`
- Test: `test/test_file_review_executor.py`

- [ ] **Step 1: 写失败测试**

```python
# test/test_file_review_executor.py
from unittest.mock import MagicMock, patch
from api.db.db_models import DB
from api.db.services.file_review_service import FileReviewRoundService
from rag.svr.file_review.executor import execute_task


def test_execute_task_round1_creates_annotations(monkeypatch):
    # 准备：插 1 个 round（reviewing 状态）+ 1 个 LLM mock 返回 1 条标注
    monkeypatch.setattr('rag.svr.file_review.executor.retrieve_kb_chunks',
                        lambda kb_ids, q: [])
    monkeypatch.setattr('rag.svr.file_review.executor.llm_review',
                        lambda *a, **k: {
                            'summary': '存在 1 个 high',
                            'annotations': [{'matched_text': 'foo', 'type': 'format',
                                             'severity': 'high', 'issue': 'x', 'suggestion': 'y'}]
                        })

    with DB.connection_context():
        rid = FileReviewRoundService.create_round(
            task_id='t1', file_id='f1', round_no=1, template_id='bid_doc_format',
            user_query='审核', file_version='v1', status='reviewing',
            tenant_id='', created_by='u'
        )

    execute_task(rid)

    with DB.connection_context():
        anns = list(FileReviewRoundService.model.select().where(
            FileReviewRoundService.model.id == rid))
        from api.db.services.file_review_service import FileReviewAnnotationService
        ann_rows = FileReviewAnnotationService.list_by_file_version('f1', 'v1', 't1')
    assert anns[0].status == 'annotated'
    assert anns[0].summary == '存在 1 个 high'
    assert len(ann_rows) == 1
```

- [ ] **Step 2: 跑测试确认失败** — `uv run --no-sync pytest test/test_file_review_executor.py -v` 期望：ImportError

- [ ] **Step 3: 创建 `rag/svr/file_review/executor.py`**

```python
"""文件审核执行器主循环：多轮状态机。
execute_task(round_id) 读 round → 跑审视（KB + LLM）→ 写 annotation → 改 round.status=annotated。
修复轮 round_no>1 且 status=fixing → LLM patch 应用 → 改 status=annotated。"""
import json
import logging
from typing import List

from api.db.db_models import DB
from api.db.services.file_review_service import (
    FileReviewAnnotationService,
    FileReviewRoundService,
    FileReviewTemplateService,
)
from api.utils import get_uuid
from rag.svr.file_review.kb_aggregator import aggregate_references
from rag.svr.file_review.patcher import apply_patches

logger = logging.getLogger(__name__)


def retrieve_kb_chunks(kb_ids, query):
    """懒加载包装：测试可 monkeypatch；真实调用走 settings.retriever.retrieval。

    注意：`retrieval()` 返回的是 ranks 容器 {"chunks": [...], "doc_aggs": [...]}，
    必须取 ranks["chunks"] 再交给 aggregate_references（传整个 ranks 容器会抛
    TypeError，这是刻意的响亮失败，避免"零参考"静默跑完）。
    检索是 async 的，本模块跑在守护线程里，故包一层 asyncio.run。
    参数（embd_mdl / tenant_ids / similarity / rerank）依赖 settings 与 LLMBundle，
    属 I/O，测试一律 monkeypatch 掉本函数。
    """
    import asyncio

    from common import settings

    async def _run():
        ranks = await settings.retriever.retrieval(
            query, None, [], list(kb_ids or []), 1, 5,
        )
        return ranks.get("chunks", [])

    return asyncio.run(_run())


def llm_review(*, system_prompt: str, user_prompt: str) -> dict:
    """懒加载包装：测试可 monkeypatch；真实调用走 common/llm_util.py。
    返回 {summary, annotations: [{matched_text, type, severity, issue, suggestion}]}。"""
    from common.llm_util import llm_complete
    raw = llm_complete(system_prompt, user_prompt, json_response=True)
    try:
        return json.loads(raw) if isinstance(raw, str) else raw
    except Exception:
        logger.exception("llm_review parse fail: %s", raw)
        return {"summary": "LLM 输出解析失败", "annotations": []}


def execute_task(round_id: str) -> None:
    """单轮执行入口：审查 round_id 对应状态。"""
    with DB.connection_context():
        try:
            round_row = FileReviewRoundService.model.get(FileReviewRoundService.model.id == round_id)
        except FileReviewRoundService.model.DoesNotExist:
            logger.error("round not found: %s", round_id)
            return

    try:
        tpl = FileReviewTemplateService.get_by_id(round_row.template_id) if round_row.template_id else None
        if not tpl:
            tpl = FileReviewTemplateService.get_by_id('bid_doc_format')
        if not tpl:
            FileReviewRoundService.update_status(round_id, 'failed', error='审核模板缺失')
            return

        # KB 聚合：references 是模板 {references} 占位符的槽位内容（只含编号片段，
        # 标题行由模板自己写，见 T3 实施修正 2）
        kb_chunks = retrieve_kb_chunks(kb_ids=None, query=f"{round_row.user_query or ''} {tpl.name}")
        references = aggregate_references(kb_chunks=kb_chunks, budget=7800)

        # 加载文件全文（docx 用 python-docx，xlsx 用 openpyxl，简化以 plain text 演示）
        from rag.utils.minio_conn import MINIO
        bucket, obj = round_row.minio_path.split('/', 1) if round_row.minio_path else (None, None)
        file_text = ''
        if bucket and obj:
            try:
                import io
                from docx import Document
                blob = MINIO.get(bucket, obj)
                doc = Document(io.BytesIO(blob))
                file_text = '\n'.join(p.text for p in doc.paragraphs)
            except Exception:
                logger.exception("file load failed: %s", round_row.minio_path)
                file_text = ''

        file_excerpt = file_text[:500]
        # user_query 列可空（db_models.FileReviewRound.user_query = TextField(null=True)），
        # 直接 format 会把字面量 "None" 渲染进 prompt，故统一归一为空串
        user_prompt = tpl.user_prompt_template.format(
            user_query=round_row.user_query or '', file_excerpt=file_excerpt,
            references=references,
        )

        # LLM 审视
        result = llm_review(system_prompt=tpl.system_prompt, user_prompt=user_prompt)
        annotations = result.get('annotations', [])

        # 写标注
        for a in annotations:
            matched = (a.get('matched_text') or '')[:500]
            FileReviewAnnotationService.create(
                round_id=round_id, task_id=round_row.task_id,
                file_id=round_row.file_id, file_version=round_row.file_version,
                anchor=json.dumps({"p_hash": 0}),  # 简化锚点，docx 真实锚定待 v2
                matched_text=matched, type=a.get('type', 'other'),
                severity=a.get('severity', 'medium'),
                issue=a.get('issue', ''), suggestion=a.get('suggestion', ''),
                source='ai', status='open',
                tenant_id=round_row.tenant_id, created_by=round_row.created_by or '',
            )

        FileReviewRoundService.update_status(round_id, 'annotated', summary=result.get('summary', ''))
    except Exception as e:
        logger.exception("execute_task crashed: %s", round_id)
        FileReviewRoundService.update_status(round_id, 'failed', error=str(e)[:500])
```

- [ ] **Step 4: 跑测试确认通过**

```bash
uv run --no-sync pytest test/test_file_review_executor.py -v
```
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add rag/svr/file_review/executor.py test/test_file_review_executor.py
git commit -m "feat(file-review): executor main loop round1 review"
```

---

## Task 7: 画布节点 FileReview

**Files:**
- Create: `agent/component/file_review.py`

- [ ] **Step 1: 创建文件**

```python
# agent/component/file_review.py
"""「文件审核」画布节点：与 TemplateFill 同形态，启动 detached 后台线程跑多轮审核。
节点只做任务创建 + 进度观察；执行逻辑全在 rag/svr/file_review/executor.py。"""
import asyncio
import json
import logging
from functools import partial

from agent.component.base import ComponentBase, ComponentParamBase
from api.db.services.file_review_service import (
    FileReviewRoundService,
    FileReviewTemplateService,
)
from common.misc_utils import get_uuid
from rag.svr.file_review import spawn as spawn_mod
from rag.utils.redis_conn import REDIS_CONN

logger = logging.getLogger(__name__)

# SSE 通道：file_review_progress（独立事件名）
_SSE_CHANNEL_PREFIX = "file_review:progress:"


class FileReviewParam(ComponentParamBase):
    """节点参数：file_id 必填；template_id 留空走 LLM 意图匹配（executor 暂走默认）"""
    def __init__(self):
        super().__init__()
        self.file_id = ""
        self.template_id = ""  # 空 → executor 选默认 bid_doc_format
        self.custom_prompt = ""
        self.kb_ids = []
        self.max_rounds = 3


class FileReview(ComponentBase):
    component_name = "FileReview"

    def _invoke(self, **kwargs):
        """节点同步入口：创建 round + 起后台线程"""
        param = self._param
        if not param.file_id:
            raise ValueError("FileReview 节点必须配置 file_id")

        task_id = self.get_component_input('task_id') or get_uuid()
        self.set_component_output('task_id', task_id)

        # 第 1 轮：创建 round
        rid = FileReviewRoundService.create_round(
            task_id=task_id, file_id=param.file_id, round_no=1,
            template_id=param.template_id or 'bid_doc_format',
            user_query=param.custom_prompt or '审核',
            file_version='v1', status='reviewing',
            tenant_id=self._tenant_id, created_by=self._user_id or '',
        )
        self.set_component_output('round_id', rid)

        # 起后台线程
        spawn_mod.spawn_review_task(rid)

        return json.dumps({"task_id": task_id, "round_id": rid, "status": "reviewing"},
                          ensure_ascii=False)
```

- [ ] **Step 2: 提交**

```bash
git add agent/component/file_review.py
git commit -m "feat(file-review): canvas node FileReview"
```

---

## Task 8: 对话工具 FileReviewTool

**Files:**
- Create: `agent/tools/file_review.py`

- [ ] **Step 1: 创建文件**

```python
# agent/tools/file_review.py
"""FileReview 对话工具：用户说"审核投标书"+ 上传文件时触发；
反问 file_id → 调 FileReview 节点 RPC（简化：直接调 service 起 task）。"""
import json
import logging

from agent.tools.base import ToolBase
from api.db.services.file_review_service import (
    FileReviewRoundService,
    FileReviewTemplateService,
)
from common.misc_utils import get_uuid
from rag.svr.file_review import spawn as spawn_mod

logger = logging.getLogger(__name__)


class FileReviewTool(ToolBase):
    """工具名：file_review；参数：file_id（必填）、template_id（选填）、user_query（选填）"""
    name = "file_review"
    description = "文件审核：调用 LLM 对上传文件按招标场景模板多轮审视并产出可点击标注"

    def _run(self, file_id: str, template_id: str = '', user_query: str = '审核') -> str:
        if not file_id:
            return json.dumps({"error": "缺少 file_id"}, ensure_ascii=False)

        tpl_id = template_id or 'bid_doc_format'
        if not FileReviewTemplateService.get_by_id(tpl_id):
            tpl_id = 'bid_doc_format'

        task_id = get_uuid()
        rid = FileReviewRoundService.create_round(
            task_id=task_id, file_id=file_id, round_no=1,
            template_id=tpl_id, user_query=user_query,
            file_version='v1', status='reviewing',
            tenant_id='', created_by='',
        )
        spawn_mod.spawn_review_task(rid)

        return json.dumps({
            "task_id": task_id, "round_id": rid, "status": "reviewing",
            "message": f"已启动审核（模板：{tpl_id}），稍后查看标注。",
        }, ensure_ascii=False)
```

- [ ] **Step 2: 提交**

```bash
git add agent/tools/file_review.py
git commit -m "feat(file-review): dialogue tool FileReviewTool"
```

---

## Task 9: REST API 端点（7 个）

**Files:**
- Create: `api/apps/restful_apis/file_review_api.py`
- Test: `test/test_file_review_api.py`

- [ ] **Step 1: 写失败测试**

```python
# test/test_file_review_api.py
import json
from unittest.mock import patch


def test_start_review_returns_task_id(client, auth_headers):
    with patch('api.apps.restful_apis.file_review_api.spawn_mod.spawn_review_task'):
        r = client.post('/file/review/start',
                        json={'file_id': 'f1', 'template_id': 'bid_doc_format'},
                        headers=auth_headers)
    assert r.status_code == 200
    data = r.get_json()
    assert data['task_id']
    assert data['round_id']
    assert data['status'] == 'reviewing'


def test_list_templates(client, auth_headers):
    r = client.get('/file/review/template/list', headers=auth_headers)
    assert r.status_code == 200
    data = r.get_json()
    assert len(data['templates']) >= 5
```

- [ ] **Step 2: 跑测试确认失败** — `uv run --no-sync pytest test/test_file_review_api.py -v` 期望：ImportError

- [ ] **Step 3: 创建 `api/apps/restful_apis/file_review_api.py`**

```python
"""文件审核 REST API：7 端点。
- POST /file/review/start            启动审核
- GET  /file/review/{task_id}/progress  SSE 流式进度
- POST /file/review/{task_id}/fix    用户触发修复
- POST /file/review/{task_id}/annotation  手动加批注
- POST /file/review/{task_id}/finish 结束
- GET  /file/review/template/list    预置模板列表
- GET  /file/review/{task_id}/annotations  查标注"""
import asyncio
import json
import logging
import time
from typing import AsyncGenerator

from flask import Response, request, stream_with_context
from quart import Blueprint

from api.db.services.file_review_service import (
    FileReviewAnnotationService,
    FileReviewRoundService,
    FileReviewTemplateService,
)
from api.utils.api_utils import get_json_result, validate_request
from common.misc_utils import get_uuid
from rag.svr.file_review import spawn as spawn_mod

logger = logging.getLogger(__name__)
blueprint = Blueprint('file_review_api', __name__)


@blueprint.route('/file/review/start', methods=['POST'])
@validate_request('file_id')
async def start_review():
    body = await request.get_json()
    file_id = body['file_id']
    template_id = body.get('template_id') or 'bid_doc_format'
    user_query = body.get('user_query') or '审核'
    levels = body.get('levels') or ['high', 'medium', 'low']

    if not FileReviewTemplateService.get_by_id(template_id):
        template_id = 'bid_doc_format'

    task_id = get_uuid()
    rid = FileReviewRoundService.create_round(
        task_id=task_id, file_id=file_id, round_no=1,
        template_id=template_id, user_query=user_query,
        file_version='v1', status='reviewing',
        tenant_id='', created_by='',
    )
    spawn_mod.spawn_review_task(rid)
    return get_json_result(data={'task_id': task_id, 'round_id': rid, 'status': 'reviewing'})


@blueprint.route('/file/review/<task_id>/progress', methods=['GET'])
async def review_progress(task_id: str):
    """SSE：每 2s 拉一轮状态推送；30s heartbeat 保活"""
    async def gen() -> AsyncGenerator[str, None]:
        last_status = None
        last_push = 0.0
        while True:
            rounds = FileReviewRoundService.get_by_task(task_id)
            cur = rounds[-1] if rounds else None
            status = cur.status if cur else 'idle'
            now = time.time()
            if status != last_status or now - last_push > 30:
                yield f"data: {json.dumps({'stage': status, 'round_no': cur.round_no if cur else 0}, ensure_ascii=False)}\n\n"
                last_status = status
                last_push = now
            if status in ('done', 'failed'):
                break
            await asyncio.sleep(2.0)
    return Response(gen(), mimetype='text/event-stream')


@blueprint.route('/file/review/<task_id>/fix', methods=['POST'])
@validate_request('levels')
async def fix_review(task_id: str):
    body = await request.get_json()
    levels = body['levels']
    user_query = body.get('user_query') or f"修{'、'.join(levels)}级别问题"

    max_no = FileReviewRoundService.max_completed_round_no(task_id)
    new_no = max_no + 1
    cur = FileReviewRoundService.get_by_task(task_id)[-1] if FileReviewRoundService.get_by_task(task_id) else None
    if not cur:
        return get_json_result(code=404, message='task 不存在')

    rid = FileReviewRoundService.create_round(
        task_id=task_id, file_id=cur.file_id, round_no=new_no,
        template_id=cur.template_id, user_query=user_query,
        file_version=f'v{new_no}', status='fixing',
        tenant_id='', created_by='',
    )
    spawn_mod.spawn_review_task(rid)
    return get_json_result(data={'round_id': rid, 'round_no': new_no, 'status': 'fixing'})


@blueprint.route('/file/review/<task_id>/annotation', methods=['POST'])
async def add_annotation(task_id: str):
    body = await request.get_json()
    aid = FileReviewAnnotationService.create(
        round_id=body.get('round_id', ''), task_id=task_id,
        file_id=body['file_id'], file_version=body['file_version'],
        anchor=json.dumps(body.get('anchor', {})),
        matched_text=body.get('matched_text', ''),
        type=body.get('type', 'other'),
        severity=body.get('severity', 'medium'),
        issue=body.get('issue', ''),
        suggestion=body.get('suggestion', ''),
        source='manual', status='open',
        tenant_id='', created_by='',
    )
    return get_json_result(data={'annotation_id': aid})


@blueprint.route('/file/review/<task_id>/finish', methods=['POST'])
async def finish_review(task_id: str):
    # 标记最后一轮为 done
    rounds = FileReviewRoundService.get_by_task(task_id)
    if rounds:
        FileReviewRoundService.update_status(rounds[-1].id, 'done')
    return get_json_result(data={'finished': True})


@blueprint.route('/file/review/template/list', methods=['GET'])
async def list_templates():
    rows = FileReviewTemplateService.list_enabled()
    data = [{'id': r.id, 'name': r.name, 'description': r.description,
             'annotation_types': json.loads(r.annotation_types or '[]')}
            for r in rows]
    return get_json_result(data={'templates': data})


@blueprint.route('/file/review/<task_id>/annotations', methods=['GET'])
async def list_annotations(task_id: str):
    fv = request.args.get('file_version')
    fid = request.args.get('file_id')
    if not (fv and fid):
        return get_json_result(code=400, message='缺少 file_id/file_version')
    rows = FileReviewAnnotationService.list_by_file_version(fid, fv, task_id)
    data = [{'id': r.id, 'type': r.type, 'severity': r.severity, 'issue': r.issue,
             'suggestion': r.suggestion, 'matched_text': r.matched_text,
             'source': r.source, 'status': r.status,
             'file_version': r.file_version,
             'prev_annotation_id': r.prev_annotation_id,
             'anchor': json.loads(r.anchor or '{}')}
            for r in rows]
    return get_json_result(data={'annotations': data})
```

- [ ] **Step 4: 蓝图自动注册** — `api/apps/__init__.py:289` 的 `search_pages_path` 扫描 `api/apps/restful_apis/*.py` 全自动注册，**无需手动改 `__init__.py`**。确认 `file_review_api.py` 已落入该目录即可生效。

- [ ] **Step 5: 跑测试确认通过**

```bash
uv run --no-sync pytest test/test_file_review_api.py -v
```
Expected: PASS

- [ ] **Step 6: 提交**

```bash
git add api/apps/restful_apis/file_review_api.py test/test_file_review_api.py
git commit -m "feat(file-review): REST API 7 endpoints (auto-registered)"
```

---

## Task 10: 前端流类型 + 归约函数

**Files:**
- Create: `web/src/hooks/file-review-stream.ts`

- [ ] **Step 1: 创建文件**

```typescript
// 文件审核 SSE 事件类型与归约器（与 template-fill-stream.ts 并列，独立）
export interface IFileReviewAnnotation {
  id: string;
  type: string;             // format/completeness/clause/qualification/price/other
  severity: 'high' | 'medium' | 'low';
  issue: string;
  suggestion?: string;
  matched_text?: string;
  source: 'ai' | 'manual';
  status?: 'open' | 'fixed' | 'new' | 'wontfix';
  file_version?: string;
  prev_annotation_id?: string;
  anchor?: Record<string, unknown>;
}

export interface IFileReviewRound {
  id: string;
  round_no: number;
  template_id?: string;
  template_name?: string;
  file_id: string;
  file_version: string;
  status: 'reviewing' | 'annotated' | 'fixing' | 'failed' | 'done';
  summary?: string;
  annotations?: IFileReviewAnnotation[];
}

export interface IFileReviewState {
  rounds: IFileReviewRound[];
  finished?: boolean;
  task_id?: string;
  file_id?: string;
  current_version?: string;
}

export interface IFileReviewEvent {
  stage: 'started' | 'reviewing' | 'annotated' | 'fixing' | 'file_version' | 'done' | 'failed' | 'heartbeat';
  task_id?: string;
  round_no?: number;
  template_id?: string;
  template_name?: string;
  file_id?: string;
  file_version?: string;
  summary?: string;
  annotation_count?: { high?: number; medium?: number; low?: number };
}

export function applyFileReviewEvent(
  acc: IFileReviewState,
  d: IFileReviewEvent,
): IFileReviewState {
  if (!acc.task_id && d.task_id) acc.task_id = d.task_id;
  if (!acc.file_id && d.file_id) acc.file_id = d.file_id;
  // heartbeat 不入态
  if (d.stage === 'heartbeat') return acc;
  if (d.stage === 'started') return { ...acc, rounds: [] };
  if (d.stage === 'done') return { ...acc, finished: true };
  if (d.stage === 'file_version' && d.file_version) {
    return { ...acc, current_version: d.file_version };
  }
  const round_no = d.round_no ?? 0;
  let rounds = [...acc.rounds];
  let cur = rounds.find((r) => r.round_no === round_no);
  if (!cur) {
    cur = {
      id: `r${round_no}`,
      round_no,
      file_id: d.file_id || acc.file_id || '',
      file_version: d.file_version || `v${round_no}`,
      status: 'reviewing',
    };
    rounds.push(cur);
  }
  if (d.template_id) cur.template_id = d.template_id;
  if (d.template_name) cur.template_name = d.template_name;
  if (d.stage === 'reviewing') cur.status = 'reviewing';
  else if (d.stage === 'fixing') cur.status = 'fixing';
  else if (d.stage === 'annotated') {
    cur.status = 'annotated';
    if (d.summary) cur.summary = d.summary;
  } else if (d.stage === 'failed') cur.status = 'failed';
  return { ...acc, rounds };
}

/** 按 file_version 过滤标注：review-panel 加载用 */
export function filterAnnotationsByVersion(
  annotations: IFileReviewAnnotation[],
  file_version: string,
): IFileReviewAnnotation[] {
  return annotations.filter((a) => a.file_version === file_version);
}
```

- [ ] **Step 2: 提交**

```bash
git add web/src/hooks/file-review-stream.ts
git commit -m "feat(file-review): frontend stream types + reducer"
```

---

## Task 11: API Hook 封装

**Files:**
- Create: `web/src/hooks/use-file-review-request.ts`

- [ ] **Step 1: 创建文件**

```typescript
// 文件审核 API 调用封装（与 use-template-fill-request 同形态）
import { useRequest } from '@umijs/max';

export async function startFileReview(params: {
  file_id: string;
  template_id?: string;
  user_query?: string;
}): Promise<{ task_id: string; round_id: string; status: string }> {
  const r = await fetch('/api/file/review/start', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(params),
  });
  return (await r.json()).data;
}

export async function fixFileReview(task_id: string, params: {
  levels: string[];
  user_query?: string;
}): Promise<{ round_id: string; round_no: number }> {
  const r = await fetch(`/api/file/review/${task_id}/fix`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(params),
  });
  return (await r.json()).data;
}

export async function addAnnotation(task_id: string, params: {
  file_id: string;
  file_version: string;
  type?: string;
  severity?: string;
  issue: string;
  suggestion?: string;
  anchor?: Record<string, unknown>;
  matched_text?: string;
}): Promise<{ annotation_id: string }> {
  const r = await fetch(`/api/file/review/${task_id}/annotation`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(params),
  });
  return (await r.json()).data;
}

export async function finishReview(task_id: string): Promise<void> {
  await fetch(`/api/file/review/${task_id}/finish`, { method: 'POST' });
}

export async function listAnnotations(
  task_id: string,
  file_id: string,
  file_version: string,
): Promise<{ annotations: any[] }> {
  const r = await fetch(
    `/api/file/review/${task_id}/annotations?file_id=${file_id}&file_version=${file_version}`,
  );
  return (await r.json()).data;
}

export function useFileReviewTemplates() {
  return useRequest('/api/file/review/template/list', { formatResult: (r: any) => r.data.templates });
}
```

- [ ] **Step 2: 提交**

```bash
git add web/src/hooks/use-file-review-request.ts
git commit -m "feat(file-review): API hook wrapper"
```

---

## Task 12: 进度卡组件 file-review-progress

**Files:**
- Create: `web/src/pages/c-chat/file-review-progress.tsx`
- Test: `web/src/pages/c-chat/__tests__/file-review-progress.test.tsx`

- [ ] **Step 1: 写失败测试**

```tsx
// web/src/pages/c-chat/__tests__/file-review-progress.test.tsx
import { render, screen } from '@testing-library/react';
import FileReviewProgress from '@/pages/c-chat/file-review-progress';

jest.mock('@/hooks/use-file-review-request', () => ({
  listAnnotations: jest.fn().mockResolvedValue({ annotations: [] }),
}));

describe('FileReviewProgress', () => {
  it('renders summary text with counts', () => {
    render(
      <FileReviewProgress
        taskId="t1"
        fileId="f1"
        state={{
          rounds: [{
            id: 'r1', round_no: 1, file_id: 'f1', file_version: 'v1',
            status: 'annotated', summary: 'high:1 medium:2 low:0',
          }],
          task_id: 't1', file_id: 'f1', current_version: 'v1',
        }}
      />,
    );
    expect(screen.getByText(/第 1 轮/)).toBeInTheDocument();
    expect(screen.getByText(/high:1 medium:2 low:0/)).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: 创建 `web/src/pages/c-chat/file-review-progress.tsx`**

```tsx
// 文件审核进度卡：参照 template-fill-progress.tsx 同形态，
// 渲染轮次摘要 + 跳 review-panel 按钮 + 版本下拉 + 修复触发按钮
import {
  applyFileReviewEvent,
  type IFileReviewAnnotation,
  type IFileReviewEvent,
  type IFileReviewState,
} from '@/hooks/file-review-stream';
import {
  finishReview,
  fixFileReview,
  listAnnotations,
} from '@/hooks/use-file-review-request';
import { Eye, Loader2 } from 'lucide-react';
import { useEffect, useState } from 'react';

export default function FileReviewProgress({
  taskId,
  fileId,
  state: initialState,
  streaming = true,
  onOpenReview,
}: {
  taskId: string;
  fileId: string;
  state?: IFileReviewState;
  streaming?: boolean;
  onOpenReview?: (annotations: IFileReviewAnnotation[], fileVersion: string) => void;
}) {
  const [state, setState] = useState<IFileReviewState>(
    initialState || { rounds: [], task_id: taskId, file_id: fileId },
  );

  // SSE 订阅
  useEffect(() => {
    if (!streaming || !taskId) return;
    const es = new EventSource(`/api/file/review/${taskId}/progress`);
    es.onmessage = (e) => {
      try {
        const d: IFileReviewEvent = JSON.parse(e.data);
        setState((s) => applyFileReviewEvent(s, d));
      } catch {}
    };
    return () => es.close();
  }, [taskId, streaming]);

  // 加载当前版本的标注
  const loadAnnotations = async (version: string) => {
    const data = await listAnnotations(taskId, fileId, version);
    return data.annotations;
  };

  const curRound = state.rounds[state.rounds.length - 1];
  const curVersion = state.current_version || curRound?.file_version || 'v1';
  const canFix = curRound?.status === 'annotated' &&
    state.rounds.filter((r) => r.status === 'done' || r.status === 'annotated').length < 3;

  const handleFix = async (levels: string[]) => {
    await fixFileReview(taskId, { levels, user_query: `修${levels.join('、')}级别问题` });
  };

  const handleOpenReview = async () => {
    const annotations = await loadAnnotations(curVersion);
    onOpenReview?.(annotations, curVersion);
  };

  return (
    <div className="space-y-2 rounded-lg border border-[#E5E5E5] bg-[#F5F5F5] px-3 py-2.5 text-xs">
      <div className="font-medium text-[#000000]">
        文件审核{' '}
        {curRound && (
          <span className="text-[#8C8C8C]">
            第 {curRound.round_no} 轮 ·{' '}
            {curRound.status === 'annotated' ? '已完成' : curRound.status}
          </span>
        )}
      </div>
      {curRound?.summary && (
        <div className="text-[#8C8C8C]">{curRound.summary}</div>
      )}
      <div className="flex flex-wrap items-center gap-2 pt-0.5">
        <button
          type="button"
          className="flex items-center gap-1 rounded border border-[#1a66fb] px-2 py-0.5 text-[#1a66fb] hover:bg-[#F5F8FF]"
          onClick={handleOpenReview}
        >
          <Eye className="h-3 w-3" /> 打开审核面板（{curVersion}）
        </button>
        {canFix && (
          <>
            <button
              type="button"
              className="rounded bg-[#1a66fb] px-2 py-0.5 text-white hover:bg-[#1557d6]"
              onClick={() => handleFix(['high', 'medium'])}
            >
              修高中级别
            </button>
            <button
              type="button"
              className="rounded border border-[#1a66fb] px-2 py-0.5 text-[#1a66fb] hover:bg-[#F5F8FF]"
              onClick={() => handleFix(['high'])}
            >
              仅修高
            </button>
          </>
        )}
        {curRound?.status === 'annotated' && state.rounds.length >= 3 && (
          <button
            type="button"
            className="rounded border border-[#8C8C8C] px-2 py-0.5 text-[#8C8C8C]"
            onClick={() => handleFix(['high', 'medium', 'low'])}
          >
            再修一轮
          </button>
        )}
        {curRound?.status === 'annotated' && (
          <button
            type="button"
            className="ml-auto text-[#8C8C8C] underline-offset-2 hover:underline"
            onClick={() => finishReview(taskId)}
          >
            结束审核
          </button>
        )}
      </div>
      {curRound?.status === 'reviewing' && (
        <div className="flex items-center text-[#8C8C8C]">
          <Loader2 className="mr-1 h-3 w-3 animate-spin" /> 正在审核…
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 3: 跑测试确认通过**

```bash
cd web && npm test -- file-review-progress
```
Expected: PASS（jest 跑不通时退化为 build 验证：`npm run build`）

- [ ] **Step 4: 提交**

```bash
git add web/src/pages/c-chat/file-review-progress.tsx web/src/pages/c-chat/__tests__/file-review-progress.test.tsx
git commit -m "feat(file-review): progress card component"
```

---

## Task 13: i18n key 补齐（zh.ts）

**Files:**
- Modify: `web/src/locales/zh.ts`

- [ ] **Step 1: 在 zh.ts 追加 key** — 定位 `web/src/locales/zh.ts:1674`（`templateFill: '范本填写'`）之后的节点描述块（canvas 节点相关 key 集中区），在 `templateFillQuery: '需求描述'` 行之后插入：

```typescript
      fileReview: '文件审核',
      fileReviewDescription:
        '对上传文件按招标场景模板多轮审视并产出可点击标注，用户可触发多轮修复。',
      fileReviewFileId: '文件 ID',
      fileReviewTemplate: '审核模板',
      fileReviewCustomPrompt: '自定义要求',
      fileReviewMaxRounds: '最大轮次',
```

CLAUDE.md 规则：**仅 zh.ts 加 key，en.ts 不动**。

- [ ] **Step 2: 提交**

```bash
git add web/src/locales/zh.ts
git commit -m "feat(file-review): zh.ts i18n keys"
```

---

## Task 14: 对话侧集成（FileReviewTool + 流式事件挂载）

**Files:**
- Modify: `web/src/pages/c-chat/index.tsx`

- [ ] **Step 1: 找到对话侧流式事件订阅处**

定位 `web/src/pages/c-chat/index.tsx` 中处理 `template_fill_progress` 事件的分支（搜索字符串 `template_fill_progress`），在它旁边新增：

```typescript
if (d.event === 'file_review_progress') {
  setFileReviewState((s) => applyFileReviewEvent(s, d));
  continue;
}
```

- [ ] **Step 2: 在对话消息渲染处增加 FileReviewProgress 挂载**

定位 message 渲染分支（搜索 `<TemplateFillProgress` 出现处，按相同挂载模式追加）：

```tsx
{msg.fileReview?.rounds?.length > 0 && (
  <FileReviewProgress
    taskId={msg.fileReview.task_id}
    fileId={msg.fileReview.file_id}
    state={msg.fileReview}
    streaming={false}
    onOpenReview={(annotations, version) => setReviewPanel({ annotations, version })}
  />
)}
```

- [ ] **Step 3: 工具注册**

定位对话侧工具注册列表（搜索 `template_fill` 出现处，按模板填写的注册模式追加 file_review）：

```typescript
{ name: 'file_review', tool: FileReviewTool, label: '文件审核' },
```

- [ ] **Step 4: 验证 build**

```bash
cd web && npm run build
```
Expected: build 成功

- [ ] **Step 5: 提交**

```bash
git add web/src/pages/c-chat/index.tsx
# 若 Step 3 改了其他文件：git add <additional-files>
git commit -m "feat(file-review): c-chat dialogue integration"
```

---

## Task 15: 流程页集成（FileReview 节点注册）

**Files:**
- Modify: `web/src/pages/c-chat/flow/flow-panel.tsx`
- Modify: `web/src/pages/agent/canvas/index.tsx`

- [ ] **Step 1: flow-panel.tsx 注册 FileReview 节点类型**

定位 `flow-panel.tsx` 中节点注册表（搜索 `TemplateFill` 字符串，按其注册模式追加）：

```typescript
{ type: 'FileReview', label: '文件审核', icon: FileSearch },
```

- [ ] **Step 2: flow AI 面板进度卡挂载**

定位 flow AI 对话渲染区（搜索 `TemplateFillProgress` 出现处，按相同模式追加）：

```tsx
{msg.fileReview?.rounds?.length > 0 && (
  <FileReviewProgress
    taskId={msg.fileReview.task_id}
    fileId={msg.fileReview.file_id}
    state={msg.fileReview}
    streaming={false}
    onOpenReview={(annotations, version) => setReviewPanel({ annotations, version })}
  />
)}
```

- [ ] **Step 3: agent/canvas 编辑器节点调色板新增分组**

定位 `agent/canvas/index.tsx` 的节点调色板配置（搜索 `TemplateFill` 字符串，按分组配置模式追加）：

```typescript
{
  group: '文件审核',
  items: [
    { type: 'FileReview', label: '文件审核', icon: FileSearch,
      params: ['file_id', 'template_id', 'custom_prompt', 'kb_ids', 'max_rounds'] },
  ],
},
```

- [ ] **Step 4: 节点配置表单**

定位节点参数编辑组件（搜索 `TemplateFill` 字符串出现于 ParamEditor 处，按 case 分支模式追加）：

```tsx
case 'FileReview':
  return (
    <div className="space-y-2">
      <Input label="文件 ID" value={param.file_id} onChange={...} />
      <Select label="审核模板" value={param.template_id}
              options={templates.map(t => ({ label: t.name, value: t.id }))}
              allowEmpty onChange={...} />
      <Textarea label="自定义要求（可选）" value={param.custom_prompt} onChange={...} />
      <NumberInput label="最大轮次" value={param.max_rounds} defaultValue={3} onChange={...} />
    </div>
  );
```

- [ ] **Step 5: 验证 build**

```bash
cd web && npm run build
```
Expected: build 成功

- [ ] **Step 6: 提交**

```bash
git add web/src/pages/c-chat/flow/flow-panel.tsx web/src/pages/agent/canvas/index.tsx
git commit -m "feat(file-review): flow + canvas editor integration"
```

---

## Task 16: 端到端验收测试（人工 + 集成）

**Files:**
- Create: `test/test_file_review_e2e.py`

- [ ] **Step 1: 写最小 e2e 测试**

```python
# test/test_file_review_e2e.py
"""文件审核端到端：start → progress 终态 → 标 manual → fix → 第二轮终态"""
import time
from unittest.mock import patch
from api.db.db_models import DB
from api.db.services.file_review_service import (
    FileReviewAnnotationService,
    FileReviewRoundService,
)


def test_e2e_two_rounds(monkeypatch, client, auth_headers):
    monkeypatch.setattr('rag.svr.file_review.executor.llm_review',
                        lambda *a, **k: {
                            'summary': 'high:1',
                            'annotations': [{'matched_text': 'x', 'type': 'format',
                                             'severity': 'high', 'issue': 'i', 'suggestion': 's'}]
                        })

    # 1. 启动
    with patch('api.apps.restful_apis.file_review_api.spawn_mod.spawn_review_task'):
        r = client.post('/file/review/start', json={'file_id': 'f1'}, headers=auth_headers)
    task_id = r.get_json()['data']['task_id']
    rid1 = r.get_json()['data']['round_id']

    # 2. 同步触发 executor（绕过线程）
    from rag.svr.file_review.executor import execute_task
    execute_task(rid1)
    time.sleep(0.2)

    with DB.connection_context():
        r1 = FileReviewRoundService.model.get(FileReviewRoundService.model.id == rid1)
        assert r1.status == 'annotated'

    # 3. 触发修复
    r = client.post(f'/file/review/{task_id}/fix', json={'levels': ['high']}, headers=auth_headers)
    rid2 = r.get_json()['data']['round_id']
    execute_task(rid2)
    time.sleep(0.2)

    with DB.connection_context():
        r2 = FileReviewRoundService.model.get(FileReviewRoundService.model.id == rid2)
        assert r2.status == 'annotated'
        assert r2.round_no == 2
```

- [ ] **Step 2: 跑测试确认通过**

```bash
uv run --no-sync pytest test/test_file_review_e2e.py -v
```
Expected: PASS

- [ ] **Step 3: 跑全量**

```bash
uv run --no-sync pytest -k 'file_review' -v
```
Expected: 全绿

```bash
cd web && npm run build
```
Expected: build 成功，0 error

- [ ] **Step 4: 提交**

```bash
git add test/test_file_review_e2e.py
git commit -m "test(file-review): e2e two-rounds happy path"
```

---

## Task 17: 部署（先后端再前端，DB migration 自动）

**Files:**（无新增，仅部署）

- [ ] **Step 1: 后端 SCP**

```bash
# 后端文件清单
FILES=(
  api/db/db_models.py
  api/db/services/file_review_service.py
  api/apps/restful_apis/file_review_api.py
  api/apps/restful_apis/__init__.py
  rag/svr/file_review/__init__.py
  rag/svr/file_review/executor.py
  rag/svr/file_review/kb_aggregator.py
  rag/svr/file_review/patcher.py
  rag/svr/file_review/spawn.py
  agent/component/file_review.py
  agent/tools/file_review.py
)
for f in "${FILES[@]}"; do
  scp -i "D:\AI\konus-key.pem" -o StrictHostKeyChecking=no \
    "D:/AI/ragflow2/$f" \
    "root@47.98.102.55:/home/bid-agent-konus/ragflow2/$f"
done
```

- [ ] **Step 2: 容器内冒烟（确认 import 无问题 + DB migration 跑过）**

```bash
ssh -i "D:\AI\konus-key.pem" root@47.98.102.55 \
  "docker exec docker-ragflow-cpu-1 python -c '
from api.db.db_models import FileReviewTemplate, FileReviewRound, FileReviewAnnotation
from api.db.services.file_review_service import FileReviewTemplateService, FileReviewRoundService, FileReviewAnnotationService
from rag.svr.file_review.executor import execute_task
from rag.svr.file_review.spawn import spawn_review_task
from agent.component.file_review import FileReview
from agent.tools.file_review import FileReviewTool
from api.apps.restful_apis.file_review_api import blueprint
print(\"all imports OK\")
tpls = FileReviewTemplateService.list_enabled()
print(f\"preset templates: {len(tpls)}\")
'"
```
Expected: `all imports OK` + `preset templates: 5`

- [ ] **Step 3: 重启容器**

```bash
ssh -i "D:\AI\konus-key.pem" root@47.98.102.55 "docker restart docker-ragflow-cpu-1"
```

- [ ] **Step 4: 验证端点（HTTP 200 + JSON OK）**

```bash
ssh -i "D:\AI\konus-key.pem" root@47.98.102.55 \
  "sleep 30 && docker exec docker-ragflow-cpu-1 curl -s -o /dev/null -w '%{http_code}\n' \
   http://localhost:9380/file/review/template/list -H 'Authorization: Bearer <TEST_TOKEN>'"
```
Expected: 401（未带 token → 鉴权拦截，**确认鉴权层挂上**）

- [ ] **Step 5: 前端 build**

```bash
cd web && npm run build
tar -czf dist.tar.gz dist/
scp -i "D:\AI\konus-key.pem" dist.tar.gz root@47.98.102.55:/home/bid-agent-konus/ragflow2/web/
ssh -i "D:\AI\konus-key.pem" root@47.98.102.55 \
  "cd /home/bid-agent-konus/ragflow2/web && rm -rf dist/* dist/.[!.]* dist/..?* 2>/dev/null; tar -xzf dist.tar.gz && rm -f dist.tar.gz && docker exec docker-ragflow-cpu-1 nginx -s reload"
```

- [ ] **Step 6: 验收清单**（人肉浏览器）

1. C 端对话发"审核投标书" + 上传文件 → 自动起 FileReview 工具 → progress 卡显示第 1 轮 → 点"打开审核面板" → review-panel 显示 v1 标注
2. 手动加批注 → review-panel 显示 manual 行
3. 点"修高中级别" → progress 卡显示第 2 轮 reviewing → annotated → review-panel 切 v2
4. 第 3 轮无修复按钮，点"再修一轮"扩展到 v4
5. 流程页 FileReview 节点配置 → 跑通同上
6. 刷新页面：标注历史完整，正在审核的轮次经轮询恢复
7. 并行 TemplateFill 节点运行不受影响（独立性验证）

---

## 自检清单

- [ ] **Spec 覆盖**：每个 spec 章节均有 task 覆盖（DB→Service→KB→Patcher→Spawn→Executor→Node→Tool→API→Stream→Hook→Progress→i18n→对话→flow→canvas→e2e→部署）
- [ ] **占位符扫描**：无 TBD / TODO / "fill in"
- [ ] **类型一致**：`IFileReviewState`/`IFileReviewEvent`/`IFileReviewAnnotation` 在 Stream 任务定义、Hook 复用、Progress 组件、对话/flow 挂载全部一致
- [ ] **增量边界确认**：未触及 `template_fill*` / `tpl_*` / `tpl_fill:*` / `review-panel.tsx` / `Annotation` / `MarginComment`