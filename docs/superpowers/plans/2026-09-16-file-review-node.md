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
> - **xlsx / pdf 修复路径不在本任务范围，且 v1 明确不做**：设计里 xlsx 锚点
>   `{sheet, cell}` 仍是占位形态，逐单元格定位未设计。T6 的修复轮对非 docx 文件
>   **只出标注、不自动改**：收口 `done` 并提示「该文件类型不支持自动修复（v1 仅支持
>   Word .docx），请按批注手动修改」。原稿写的「修复走纯文本降级」是错的——把解码文本
>   当新版本存回去会毁掉原件，还会成为下一轮审核的输入。
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
- Create: `test/test_file_review_executor.py`
- Modify: `api/db/services/file_review_service.py`（`create_round` 加 `kb_ids`、`list_pending_by_task`、`delete_by_round`）
- Modify: `test/test_file_review_service.py`（上述三个新方法的用例）
- Modify: `rag/svr/file_review/spawn.py`（`_force_fail_round` 的 CAS 覆盖 `fixing`）
- Modify: `test/test_file_review_spawn.py`（`test_force_fail_marks_fixing_round_too`）
- Modify: `api/db/db_models.py`（`FileReviewRound.kb_ids` + 迁移；预置模板文案修订 + seed 改 insert-or-update；`anchor` 列注释纠正）
- Modify: `test/test_file_review_db.py`（对齐新文案与 seed 语义）
- Modify: `docs/superpowers/plans/2026-09-16-file-review-node.md`（T4 说明句：修复范围收敛为 docx-only）

---

### 背景：为什么这一步不是「照着原稿写 executor」

原 T6 草稿与本计划前五个任务已冻结的契约**相互矛盾**，直接实现会立刻炸。逐条列出（实现者必须理解这些约束，不能只看代码片段照抄）：

1. **spawn 的执行单元是 `task_id`，不是 `round_id`。** T5 的 `_force_fail_round(task_id, ...)` 按 `FileReviewRound.task_id` 做 CAS；T7/T8/T9 也都按 task 组织。故 `execute_task(task_id)` 的入参是 task，轮次由它自己 `get_by_task(task_id)[-1]` 取。
2. **`from common.llm_util import llm_complete` 这个模块不存在。** 正确写法是 `LLMBundle(tenant_id, get_tenant_default_model_by_type(tenant_id, LLMType.CHAT))` + `asyncio.run(mdl.async_chat(system, [{"role": "user", "content": prompt}]))`（`async_chat` 返回 `str`，见 `api/db/services/llm_service.py:385`）。导入路径已在 `rag/svr/template_fill/executor.py:29` 验证过：`from api.db.services.llm_service import LLMBundle`。
3. **检索不能自己拼 `settings.retriever.retrieval(...)`。** 复用 `rag.svr.template_fill.executor.retrieve_slot(tenant_id, kb_ids, query, top_k=...)`——它已带 KB 校验与结果裁剪，`asyncio.run` 包一层即可在同步线程内用。
4. **不能写 `anchor=json.dumps({"p_hash": 0})` 这种假锚点。** 服务端按 `matched_text` 反查权威锚点 `{p_idx, p_hash, a_occ, p_total}`（复用 `iter_docx_paragraphs` / `norm_ws` / `para_hash32x2`，与 B 端填写点直定位同一套口径），查不到就写 `{}`——**宁可不给跳转链接，也不能给错链接**。
5. **原稿从不调用 `apply_patches`**，也即修复轮什么都改不了。本任务必须真正落盘：`patcher.apply_patches_to_docx` → 存新版本 blob → 标 `fixed`。
6. **纯文本降级修复非 docx 会损坏文件。** 把解码文本当新版本存回去，既毁掉原件，又成为下一轮审核的输入。**v1 自动修复只支持 docx**；非 docx 的修复轮诚实收口 `done` 并给出「请按批注手动修改」的说明，标注一律不动。
7. **`reviewing` 以外还有 `fixing` 会长时间在途。** T5 的 `_force_fail_round` 只 CAS `status == "reviewing"`，进程在修复中被杀就会留下一行永远转圈的 `fixing`。本任务把它扩到 `("reviewing", "fixing")`。
8. **`kb_ids` 无处可存。** T7 节点接收的 `kb_ids` 要跨轮幸存（重试/续轮都得用同一批 KB），但 `FileReviewRound` 没有该列。本任务补列 + `alter_db_add_column` 迁移 + `create_round(kb_ids=...)`。
9. **`list_open_or_new_for_next_round(task_id, round_no-1)` 在修复轮取不到东西。** 修复轮本身不产标注，按上一轮 round_no 圈定会在第 3 轮拿到空集从而静默不修。新增 task 级 `list_pending_by_task(task_id)`。
10. **同一轮重跑会产生两套标注。** 进程被杀后轮次滞留 `reviewing`，重试再跑一遍就会把每条标注写第二次。写标注前先 `delete_by_round(round_id)`。
11. **预置模板文案有硬伤，且改了也进不了已初始化的库。** ① `{file_excerpt}` 只喂首 500 字却让 LLM「审视全文」；② 输出示例里的 `"anchor": {{...}}` 会诱导 LLM 编造锚点，而锚点本该由服务端反查；③ `_seed_file_review_templates` 对已存在的行 `continue`，`_ensure_file_review_templates` 又按 ID 数量短路——**常量文案修订永远到不了已建库**。故：文案改名 `{file_text}` 并去掉示例里的 `anchor`，seed 改 insert-or-update（只同步提示词内容列，**绝不动 `enabled`**——管理员显式停用是用户态，启动同步不许把它重新打开），`_ensure` 每次都跑一遍。已核实不存在模板编辑端点（只有 `GET /file/review/template/list`），故无条件同步不会覆盖用户改动。
12. **空正文会诱发幻觉标注。** 扫描件/图片 docx 提不出文字，还让 LLM「审视全文」等于请它编。正文归一化后不足 `MIN_FILE_TEXT_CHARS` 直接抛 `FileReviewError` 让该轮 `failed`。
13. **静默截断不诚实。** 长文档截到 4 万字符而提示词说「全文」，模型就会断言「全文未提及某条款」。截断必须在正文里显式写明。
14. **失败收口自己失败会把轮次永久卡死。** 若连 `update_status(..., "failed")` 都写不进去（DB 抖），`execute_task` 正常返回，spawn 的崩溃兜底就永远不会触发。故收口失败要 `raise`，把球踢回 spawn 的独立 CAS。
15. **JSON 解析顺序错 = 假「审核通过」。** 贪心 `{...}` 排在 `[...]` 之前，会在裸数组 `[{...},{...}]` 里先吃掉第一个内层对象，得到 1 条而不是 N 条，或者直接解析失败；再叠加「把 `[无]` 这类散文字符串数组当成空数组」，就会让一次**根本没审成的**轮次显示为「未发现问题」。解析器必须：先整体 parse → 贪心数组 → 懒数组 → 贪心对象，且候选数组的元素**必须全是 dict**，否则视为解析失败（`None`），与「LLM 明确回了空数组 `[]`」严格区分。

**格式保真提醒（实现者易错点）**：`apply_patches_to_docx` 内部复用 `_replace_cross_run_in_place`，替换只重写覆盖到的 run 区间并保留区间外 `rPr`。`python-docx` 设置 `Run.text` 不会增删 run，所以 **`len(runs)` 不能作为格式保真的证据**——测试必须逐 run 断言 `bold` / `font.size`。

---

- [ ] **Step 1: 写失败测试（四个测试文件）**

先写测试。四个文件各自的完整内容/增量见下。

**1a. 新建 `test/test_file_review_executor.py`**

```python
# test/test_file_review_executor.py
"""executor 对抗测试：多轮状态机 / LLM 输出解析容错 / 锚点唯一性 / 修复落盘 / 幂等与失败收口。

真实 MySQL（同 test_file_review_service.py 的取舍）+ 真实 docx 字节（同 test_file_review_patcher.py）：
解析与锚定复用的是私有符号，只有真造真读才能证明耦合点还在；打桩掉 DB 后只剩
「函数被调用过」这种空断言，测不出 where 条件是否真的命中目标行。
所有测试行带 PFX 前缀，清理只按该前缀删，绝不误伤线上数据。禁止调用 migrate_db()。
"""
import io
import json

import pytest
from docx import Document
from docx.shared import Pt

from api.db.db_models import DB, FileReviewAnnotation, FileReviewRound, FileReviewTemplate
from api.db.services.file_review_service import (
    FileReviewAnnotationService,
    FileReviewRoundService,
)
from rag.svr.file_review import executor

PFX = "__test_fr_exec__"


def _cleanup():
    FileReviewAnnotation.delete().where(FileReviewAnnotation.task_id.startswith(PFX)).execute()
    FileReviewRound.delete().where(FileReviewRound.task_id.startswith(PFX)).execute()
    FileReviewTemplate.delete().where(FileReviewTemplate.id.startswith(PFX)).execute()


@pytest.fixture(scope="module", autouse=True)
def _tables_and_cleanup():
    from api.db import db_models as dbm
    DB.connect(reuse_if_open=True)
    try:
        for m in (FileReviewTemplate, FileReviewRound, FileReviewAnnotation):
            if not m.table_exists():
                m.create_table(safe=True)
        # 预置模板必须齐：有「template_id 非法 → 回退 DEFAULT_TEMPLATE_ID」的用例要解析到它
        dbm._seed_file_review_templates()
        _cleanup()
        yield
    finally:
        try:
            _cleanup()
        finally:
            DB.close()


class _FakeStorage:
    """内存对象存储替身：只实现 executor 用到的 get/put 两个方法。"""

    def __init__(self):
        self.blobs = {}

    def get(self, bucket, name):
        return self.blobs.get((bucket, name))

    def put(self, bucket, name, blob):
        self.blobs[(bucket, name)] = blob
        return True


@pytest.fixture
def fstore(monkeypatch):
    from types import SimpleNamespace
    st = _FakeStorage()
    monkeypatch.setattr(executor, "settings", SimpleNamespace(STORAGE_IMPL=st))
    return st


def _docx(paragraphs):
    d = Document()
    for t in paragraphs:
        d.add_paragraph(t)
    b = io.BytesIO()
    d.save(b)
    return b.getvalue()


def _mk_template(*, types=("format",), user_tpl=None):
    tid = PFX + "-tpl"
    FileReviewTemplate.delete().where(FileReviewTemplate.id == tid).execute()
    FileReviewTemplate.create(
        id=tid, name="测试模板", description="d", system_prompt="系统提示",
        user_prompt_template=user_tpl or "需求：{user_query}\n正文：\n{file_text}\n参考：\n{references}",
        annotation_types=json.dumps(list(types)), enabled=1, tenant_id="", created_by=PFX,
    )
    return tid


def _mk_round(tid, *, status="reviewing", file_version="v1", template_id=None,
              round_no=1, tenant_id=PFX, user_query="看看格式", **kw):
    return FileReviewRoundService.create_round(
        task_id=tid, file_id=PFX + "-file", round_no=round_no,
        template_id=PFX + "-tpl" if template_id is None else template_id,
        user_query=user_query, file_version=file_version, status=status,
        tenant_id=tenant_id, created_by=PFX, **kw)


def _round(tid):
    return FileReviewRoundService.get_by_id(tid)


def _anns(tid):
    return list(FileReviewAnnotation.select().where(
        FileReviewAnnotation.task_id == tid).order_by(FileReviewAnnotation.create_time.asc()))


def _wire(monkeypatch, *, blob, raw, chunks=None, storage=None):
    """把 executor 的四条外部缝全部接上：文件、检索、LLM、对象存储。"""
    monkeypatch.setattr(executor, "_load_original_blob", lambda tenant_id, file_id: blob)
    monkeypatch.setattr(executor, "_retrieve_chunks",
                        lambda tenant_id, kb_ids, query: list(chunks or []))
    monkeypatch.setattr(executor, "_call_llm", lambda tenant_id, system, user: raw)


# ── 纯函数：JSON 解析 ────────────────────────────────────────────
def test_parse_annotation_items_bare_array():
    raw = '[{"matched_text": "甲", "type": "format", "severity": "high", "issue": "i"}]'
    out = executor._parse_annotation_items(raw)
    assert len(out) == 1 and out[0]["matched_text"] == "甲"


def test_parse_annotation_items_greedy_array_before_object():
    """贪心 object 排在数组前会只吃第一个内层对象 —— 2 条变 1 条。"""
    raw = '[{"matched_text": "甲"}, {"matched_text": "乙"}]'
    assert len(executor._parse_annotation_items(raw)) == 2


def test_parse_annotation_items_recovers_from_fenced_prose():
    raw = '好的，结果如下：\n```json\n[{"matched_text": "甲", "issue": "i"}]\n```\n以上。'
    out = executor._parse_annotation_items(raw)
    assert len(out) == 1 and out[0]["issue"] == "i"


def test_parse_annotation_items_accepts_wrapped_object():
    raw = '{"annotations": [{"matched_text": "甲"}, {"matched_text": "乙"}], "note": "x"}'
    assert len(executor._parse_annotation_items(raw)) == 2


def test_parse_annotation_items_empty_array_is_empty_not_none():
    """`[]` = LLM 明确说「没问题」，与「解析不出来」是相反语义。"""
    assert executor._parse_annotation_items("[]") == []


def test_parse_annotation_items_rejects_non_dict_array_elements():
    """`["无问题"]` / `[1,2]` 是散文噪声，不是「零标注」——折成 [] 会伪造「审核通过」。"""
    assert executor._parse_annotation_items('["无问题"]') is None
    assert executor._parse_annotation_items("[1, 2]") is None


def test_parse_annotation_items_garbage_is_none():
    assert executor._parse_annotation_items("对不起，我无法完成该任务") is None
    assert executor._parse_annotation_items("") is None
    assert executor._parse_annotation_items(None) is None


def test_parse_patch_items_variants():
    assert executor._parse_patch_items('{"patches": [{"idx": 1}]}') == [{"idx": 1}]
    assert executor._parse_patch_items('[{"idx": 1}]') == [{"idx": 1}]
    assert executor._parse_patch_items("[]") == []
    assert executor._parse_patch_items("嗯") is None


# ── 纯函数：归一化 ───────────────────────────────────────────────
@pytest.mark.parametrize("raw,expect", [
    ("high", "high"), ("HIGH", "high"), ("严重", "high"), ("Critical", "high"),
    ("medium", "medium"), ("中", "medium"), ("moderate", "medium"),
    ("low", "low"), ("低", "low"), ("minor", "low"),
])
def test_norm_severity_matrix(raw, expect):
    assert executor._norm_severity(raw) == expect


def test_norm_severity_unknown_falls_back_to_medium():
    assert executor._norm_severity("天知道") == "medium"
    assert executor._norm_severity(None) == "medium"
    assert executor._norm_severity(3) == "medium"


def test_clean_str_rejects_non_str_and_strips_control():
    assert executor._clean_str(None) == ""
    assert executor._clean_str(123) == ""
    assert executor._clean_str("  a\x00b\x1fc  ") == "abc"
    assert executor._clean_str("abcdef", 3) == "abc"


def test_norm_token_fallback_on_empty():
    assert executor._norm_token("  FORMAT ") == "format"
    assert executor._norm_token("") == "other"
    assert executor._norm_token(None, "x") == "x"


def test_summary_from_stats():
    assert executor._summary_from_stats({"total": 0, "high": 0, "medium": 0, "low": 0}) == "未发现问题"
    s = executor._summary_from_stats({"total": 3, "high": 1, "medium": 1, "low": 1})
    assert "3" in s and "高 1" in s and "中 1" in s and "低 1" in s


# ── 纯函数：锚点 ─────────────────────────────────────────────────
def _items(*texts):
    return [{"index": i, "text": t, "addr": f"a{i}"} for i, t in enumerate(texts)]


def test_compute_anchor_unique_hit():
    items = _items("封面", "投标文件缺少封面，请补充")
    a = executor._compute_anchor(items, "投标文件缺少封面")
    assert a["p_idx"] == 1 and a["a_occ"] == 1 and a["p_total"] == 2
    assert len(a["p_hash"]) == 16


def test_compute_anchor_missing_returns_empty():
    assert executor._compute_anchor(_items("甲"), "乙") == {}


def test_compute_anchor_two_paragraphs_returns_empty():
    assert executor._compute_anchor(_items("缺封面", "也缺封面"), "缺封面") == {}


def test_compute_anchor_twice_in_one_paragraph_returns_empty():
    assert executor._compute_anchor(_items("缺封面，真的缺封面"), "缺封面") == {}


def test_compute_anchor_too_short_returns_empty():
    assert executor._compute_anchor(_items("甲"), "甲") == {}


def test_compute_anchor_normalizes_whitespace_before_matching():
    """Word 把同一句拆进多段/多空白时，归一化后仍应命中（与前端同口径）。"""
    a = executor._compute_anchor(_items("投标 文件 缺少 封面"), "投标文件缺少封面")
    assert a["p_idx"] == 0


# ── 纯函数：版本基线 ─────────────────────────────────────────────
def test_latest_version_name_picks_latest_non_null_upto_round():
    from types import SimpleNamespace as NS
    rounds = [NS(id="r1", round_no=1, minio_path=None),
              NS(id="r2", round_no=2, minio_path="frv-t-v2"),
              NS(id="r3", round_no=3, minio_path="frv-t-v3")]
    assert executor._latest_version_name(rounds, rounds[2]) == "frv-t-v2"
    assert executor._latest_version_name(rounds, rounds[0]) is None


# ── 纯函数：正文装配 ─────────────────────────────────────────────
def test_compose_file_text_joins_non_empty_and_errors_when_blank():
    long_enough = "这是一段足够长的正文内容，用于通过最小长度校验。" * 3
    out = executor._compose_file_text(_items("", long_enough, "  "))
    assert out.startswith("这是一段")
    with pytest.raises(executor.FileReviewError):
        executor._compose_file_text(_items("", "  "))


def test_compose_file_text_marks_truncation(monkeypatch):
    monkeypatch.setattr(executor, "FILE_TEXT_MAX_CHARS", 20)
    out = executor._compose_file_text(_items("啊" * 100))
    assert out.startswith("啊" * 20) and executor.TRUNCATED_NOTE.strip() in out


# ── 纯函数：docx 载入 ────────────────────────────────────────────
def test_load_docx_items_rejects_non_zip():
    with pytest.raises(executor.FileReviewError):
        executor._load_docx_items(b"%PDF-1.4 not a zip")


def test_load_docx_items_rejects_zip_without_docx_parts():
    """PK 开头但不是 docx：python-docx 抛 PackageNotFoundError，必须转成可读文案。"""
    with pytest.raises(executor.FileReviewError):
        executor._load_docx_items(b"PK\x03\x04garbage")


# ── 纯函数：补丁规划 ─────────────────────────────────────────────
class _A:
    def __init__(self, i):
        self.id = f"a{i}"


def test_plan_patches_maps_idx_and_skips_bad():
    chosen = [_A(1), _A(2), _A(3)]
    parsed = [{"idx": 1, "find": "x", "replace": "y"},
              {"idx": 99, "find": "z", "replace": "w"},
              {"idx": "abc", "find": "q", "replace": "r"},
              {"idx": 2, "find": "x", "replace": "y"}]
    patches, by_pos = executor._plan_patches(parsed, chosen)
    assert [p["find"] for p in patches] == ["x", "x"]
    assert by_pos == {0: 1, 1: 2}


def test_plan_patches_keeps_none_find_verbatim():
    """find=None 不能被折成 ""：patcher 对非 str find 的跳过规则必须原样生效。"""
    patches, _ = executor._plan_patches([{"idx": 1, "find": None, "replace": "y"}], [_A(1)])
    assert patches == [{"find": None, "replace": "y"}]


def test_build_fix_prompt_numbers_items_and_states_uniqueness_rule():
    chosen = [_A(1)]
    chosen[0].type, chosen[0].severity = "format", "high"
    chosen[0].matched_text, chosen[0].issue, chosen[0].suggestion = "缺封面", "没封面", "补封面"
    from types import SimpleNamespace as NS
    p = executor._build_fix_prompt(NS(user_query="看看"), "文档正文", chosen)
    assert "[1]" in p and "缺封面" in p and "文档正文" in p and "唯一" in p


def test_retrieval_query_uses_template_name_and_user_query():
    from types import SimpleNamespace as NS
    q = executor._retrieval_query(NS(user_query="看看格式"), NS(name="投标文件格式规范"))
    assert "格式规范" in q and "看看格式" in q
    assert executor._retrieval_query(NS(user_query=""), NS(name="")) == "招标文件要求"


# ── 集成：审查轮 ─────────────────────────────────────────────────
def test_execute_task_review_happy_path(monkeypatch, fstore):
    tid, blob = PFX + "-r1", _docx(["投标文件缺少封面", "其余内容正常"])
    _mk_template()
    rid = _mk_round(tid)
    raw = json.dumps([{"matched_text": "投标文件缺少封面", "type": "format",
                       "severity": "严重", "issue": "缺封面", "suggestion": "补上"}])
    _wire(monkeypatch, blob=blob, raw=raw)

    executor.execute_task(tid)

    row = _round(rid)
    assert row.status == "annotated"
    assert "1" in row.summary and "高 1" in row.summary
    anns = _anns(tid)
    assert len(anns) == 1
    assert anns[0].severity == "high" and anns[0].source == "ai" and anns[0].status == "open"
    assert anns[0].file_version == "v1" and anns[0].tenant_id == PFX
    anchor = json.loads(anns[0].anchor)
    assert anchor["p_idx"] == 0 and anchor["a_occ"] == 1 and anchor["p_total"] == 2


def test_execute_task_unknown_task_is_noop(monkeypatch, fstore):
    called = []
    monkeypatch.setattr(executor, "_call_llm", lambda *a: called.append(1) or "[]")
    executor.execute_task(PFX + "-nope")
    assert called == []


def test_execute_task_finished_round_is_noop(monkeypatch, fstore):
    tid = PFX + "-done"
    _mk_template()
    rid = _mk_round(tid, status="done")
    called = []
    monkeypatch.setattr(executor, "_call_llm", lambda *a: called.append(1) or "[]")
    executor.execute_task(tid)
    assert called == [] and _round(rid).status == "done"


def test_execute_task_unparseable_llm_marks_failed_without_annotations(monkeypatch, fstore):
    tid = PFX + "-bad"
    _mk_template()
    rid = _mk_round(tid)
    _wire(monkeypatch, blob=_docx(["一段足够长的正文用于通过最小长度校验，重复重复重复重复。"]),
          raw="我不知道该怎么回答")
    executor.execute_task(tid)
    row = _round(rid)
    assert row.status == "failed" and "无法解析" in row.error
    assert _anns(tid) == []
    assert "我不知道" in row.llm_raw          # 原始响应留档，便于排查


def test_execute_task_empty_annotation_array_marks_annotated(monkeypatch, fstore):
    """LLM 明确回空数组 = 「未发现问题」，不能判 failed（否则用户被迫无效重试）。"""
    tid = PFX + "-empty"
    _mk_template()
    rid = _mk_round(tid)
    _wire(monkeypatch, blob=_docx(["一段足够长的正文用于通过最小长度校验，重复重复重复重复。"]), raw="[]")
    executor.execute_task(tid)
    row = _round(rid)
    assert row.status == "annotated" and row.summary == "未发现问题" and _anns(tid) == []


def test_execute_task_retrieval_failure_marks_failed(monkeypatch, fstore):
    """检索失败不得静默按「零参考」继续——那会让用户以为「标准就是这些」。"""
    tid = PFX + "-retrfail"
    _mk_template()
    rid = _mk_round(tid, kb_ids=["kb-1"])

    def _boom(*a, **k):
        raise RuntimeError("retriever down")

    monkeypatch.setattr(executor, "_load_original_blob",
                        lambda t, f: _docx(["一段足够长的正文用于通过最小长度校验，重复重复重复重复。"]))
    monkeypatch.setattr(executor, "_retrieve_chunks", _boom)
    monkeypatch.setattr(executor, "_call_llm", lambda *a: "[]")
    executor.execute_task(tid)
    row = _round(rid)
    assert row.status == "failed" and "retriever down" in row.error


def test_execute_task_unsupported_file_type_marks_failed(monkeypatch, fstore):
    tid = PFX + "-pdf"
    _mk_template()
    rid = _mk_round(tid)
    _wire(monkeypatch, blob=b"%PDF-1.4 xx", raw="[]")
    executor.execute_task(tid)
    row = _round(rid)
    assert row.status == "failed" and "暂不支持审核该文件类型" in row.error


def test_execute_task_blank_document_marks_failed(monkeypatch, fstore):
    """扫描件提不出文字：不能把空正文喂给 LLM 让它「审视全文」（等于请它编）。"""
    tid = PFX + "-blank"
    _mk_template()
    rid = _mk_round(tid)
    _wire(monkeypatch, blob=_docx(["", "  "]), raw="[]")
    executor.execute_task(tid)
    assert _round(rid).status == "failed"
    assert "扫描件" in _round(rid).error


def test_execute_task_invalid_template_id_falls_back_to_default(monkeypatch, fstore):
    tid = PFX + "-tplfb"
    _mk_template()
    rid = _mk_round(tid, template_id="not-exist-tpl")
    _wire(monkeypatch, blob=_docx(["一段足够长的正文用于通过最小长度校验，重复重复重复重复。"]), raw="[]")
    executor.execute_task(tid)
    assert _round(rid).status == "annotated"


def test_execute_task_bad_placeholder_in_template_marks_failed(monkeypatch, fstore):
    tid = PFX + "-tplbad"
    _mk_template(user_tpl="需求：{user_query} 未知：{nope}")
    rid = _mk_round(tid)
    _wire(monkeypatch, blob=_docx(["一段足够长的正文用于通过最小长度校验，重复重复重复重复。"]), raw="[]")
    executor.execute_task(tid)
    assert _round(rid).status == "failed"


def test_execute_task_persisted_kb_ids_are_used_for_retrieval(monkeypatch, fstore):
    tid = PFX + "-kbs"
    _mk_template()
    rid = _mk_round(tid, kb_ids=["kb-a", "kb-b"])
    seen = []

    def _spy(tenant_id, kb_ids, query):
        seen.append(list(kb_ids))
        return []

    monkeypatch.setattr(executor, "_load_original_blob",
                        lambda t, f: _docx(["一段足够长的正文用于通过最小长度校验，重复重复重复重复。"]))
    monkeypatch.setattr(executor, "_retrieve_chunks", _spy)
    monkeypatch.setattr(executor, "_call_llm", lambda *a: "[]")
    executor.execute_task(tid)
    assert seen == [["kb-a", "kb-b"]]
    assert _round(rid).status == "annotated"


def test_execute_task_without_kb_ids_skips_retrieval(monkeypatch, fstore):
    tid = PFX + "-nokb"
    _mk_template()
    rid = _mk_round(tid)
    seen = []
    monkeypatch.setattr(executor, "_load_original_blob",
                        lambda t, f: _docx(["一段足够长的正文用于通过最小长度校验，重复重复重复重复。"]))
    monkeypatch.setattr(executor, "_retrieve_chunks",
                        lambda *a, **k: seen.append(1) or [])
    monkeypatch.setattr(executor, "_call_llm", lambda *a: "[]")
    executor.execute_task(tid)
    assert seen == [] and _round(rid).status == "annotated"


def test_execute_task_rerun_does_not_duplicate_annotations(monkeypatch, fstore):
    """进程被杀后重试同一轮不得留下两套标注。"""
    tid, blob = PFX + "-rerun", _docx(["投标文件缺少封面"])
    _mk_template()
    rid = _mk_round(tid)
    raw = json.dumps([{"matched_text": "投标文件缺少封面", "type": "format",
                       "severity": "high", "issue": "缺封面"}])
    _wire(monkeypatch, blob=blob, raw=raw)
    executor.execute_task(tid)
    assert len(_anns(tid)) == 1
    FileReviewRoundService.update_status(rid, "reviewing")     # 模拟重试
    executor.execute_task(tid)
    assert len(_anns(tid)) == 1
    assert _round(rid).status == "annotated"


def test_execute_task_collects_references_from_kb_chunks(monkeypatch, fstore):
    """检索结果必须真的进 prompt（否则「LLM+KB 出标准」这条需求是空话）。"""
    tid = PFX + "-ref"
    _mk_template()
    _mk_round(tid, kb_ids=["kb-a"])
    prompts = []

    def _llm(tenant_id, system, user):
        prompts.append(user)
        return "[]"

    monkeypatch.setattr(executor, "_load_original_blob",
                        lambda t, f: _docx(["一段足够长的正文用于通过最小长度校验，重复重复重复重复。"]))
    monkeypatch.setattr(executor, "_retrieve_chunks", lambda *a: [
        {"content": "投标文件必须包含封面", "doc_id": "d1", "doc_name": "招标文件", "similarity": 0.9}])
    monkeypatch.setattr(executor, "_call_llm", _llm)
    executor.execute_task(tid)
    assert "投标文件必须包含封面" in prompts[0]


# ── 集成：修复轮 ─────────────────────────────────────────────────
def _fix_setup(monkeypatch, fstore, *, tag, blob, raw, sev="high"):
    """造一个「上一轮 review 已产标注、当前轮 fixing」的局面。"""
    tid = PFX + "-" + tag
    _mk_template()
    r1 = _mk_round(tid, status="annotated", file_version="v1", round_no=1)
    FileReviewAnnotationService.create(
        round_id=r1, task_id=tid, file_id=PFX + "-file", file_version="v1",
        anchor="{}", matched_text="投标文件缺少封面", type="format", severity=sev,
        issue="缺封面", suggestion="补上", source="ai", status="open",
        tenant_id=PFX, created_by=PFX)
    r2 = _mk_round(tid, status="fixing", file_version="v2", round_no=2)
    _wire(monkeypatch, blob=blob, raw=raw)
    return tid, r1, r2, store_key(tid, "v2")


def store_key(tid, ver):
    return f"frv-{tid}-{ver}"


def test_execute_task_fix_happy_path_stores_version_and_marks_fixed(monkeypatch, fstore):
    tid, r1, r2, key = _fix_setup(
        monkeypatch, fstore, tag="fix",
        blob=_docx(["投标文件缺少封面"]),
        raw=json.dumps({"patches": [{"idx": 1, "find": "投标文件缺少封面", "replace": "投标文件包含封面"}]}))
    executor.execute_task(tid)
    row = _round(r2)
    assert row.status == "done" and "本轮修复 1 项" in row.summary
    assert row.minio_path == key and key in [k[1] for k in fstore.blobs]
    texts = [p.text for p in Document(io.BytesIO(fstore.blobs[(f"{PFX}-downloads", key)])).paragraphs]
    assert texts == ["投标文件包含封面"]
    assert _anns(tid)[0].status == "fixed"


def test_execute_task_fix_preserves_run_formatting(monkeypatch, fstore):
    """格式保真必须逐 run 断言：run 个数在错误实现下同样不变。"""
    d = Document()
    p = d.add_paragraph()
    r0 = p.add_run("前缀：")
    r0.bold = True
    r0.font.size = Pt(10)
    r1 = p.add_run("投标文件缺少封面")
    r1.font.size = Pt(16)
    b = io.BytesIO()
    d.save(b)
    tid, _r1, r2, key = _fix_setup(
        monkeypatch, fstore, tag="fixfmt", blob=b.getvalue(),
        raw=json.dumps({"patches": [{"idx": 1, "find": "投标文件缺少封面",
                                     "replace": "投标文件包含封面"}]}))
    executor.execute_task(tid)
    runs = Document(io.BytesIO(fstore.blobs[(f"{PFX}-downloads", key)])).paragraphs[0].runs
    assert "".join(r.text for r in runs) == "前缀：投标文件包含封面"
    assert runs[0].bold is True and runs[0].font.size == Pt(10)
    assert runs[1].font.size == Pt(16)


def test_execute_task_fix_unlocatable_patch_keeps_annotation_open(monkeypatch, fstore):
    """find 在文中不唯一 → patcher 跳过 → 标注保持 open、**不产新版本**（保持原样）。"""
    tid, _r1, r2, key = _fix_setup(
        monkeypatch, fstore, tag="fixskip",
        blob=_docx(["投标文件缺少封面", "投标文件缺少封面"]),
        raw=json.dumps({"patches": [{"idx": 1, "find": "投标文件缺少封面", "replace": "X"}]}))
    executor.execute_task(tid)
    row = _round(r2)
    assert row.status == "done" and "未能唯一定位" in row.summary
    assert row.minio_path is None and fstore.blobs == {}
    assert _anns(tid)[0].status == "open"


def test_execute_task_fix_without_pending_annotations_finishes_done(monkeypatch, fstore):
    tid = PFX + "-fixnone"
    _mk_template()
    r2 = _mk_round(tid, status="fixing", file_version="v2", round_no=2)
    called = []
    monkeypatch.setattr(executor, "_call_llm", lambda *a: called.append(1) or "[]")
    monkeypatch.setattr(executor, "_load_original_blob", lambda t, f: b"")
    executor.execute_task(tid)
    assert _round(r2).status == "done" and called == []


def test_execute_task_fix_non_docx_finishes_done_with_manual_hint(monkeypatch, fstore):
    """非 docx 不得走「纯文本降级」把原件覆盖成文本。"""
    tid, _r1, r2, _key = _fix_setup(
        monkeypatch, fstore, tag="fixpdf", blob=b"%PDF-1.4 xx",
        raw=json.dumps({"patches": [{"idx": 1, "find": "a", "replace": "b"}]}))
    executor.execute_task(tid)
    row = _round(r2)
    assert row.status == "done" and "手动修改" in row.summary
    assert row.minio_path is None and fstore.blobs == {}
    assert _anns(tid)[0].status == "open"


def test_execute_task_fix_unparseable_marks_failed(monkeypatch, fstore):
    tid, _r1, r2, _key = _fix_setup(
        monkeypatch, fstore, tag="fixbad", blob=_docx(["投标文件缺少封面"]), raw="嗯……")
    executor.execute_task(tid)
    assert _round(r2).status == "failed"


def test_execute_task_fix_all_idx_out_of_range_marks_failed(monkeypatch, fstore):
    """LLM 回了条目但 idx 全对不上 → 畸形响应，不能伪装成「无需改动」的 done。"""
    tid, _r1, r2, _key = _fix_setup(
        monkeypatch, fstore, tag="fixoob", blob=_docx(["投标文件缺少封面"]),
        raw=json.dumps({"patches": [{"idx": 42, "find": "投标文件缺少封面", "replace": "X"}]}))
    executor.execute_task(tid)
    row = _round(r2)
    assert row.status == "failed" and "idx" in row.error


def test_execute_task_fix_empty_patches_finishes_done(monkeypatch, fstore):
    """LLM 合法地回空 patches = 无需改动：判 failed 会诱发无效重试。"""
    tid, _r1, r2, _key = _fix_setup(
        monkeypatch, fstore, tag="fixempty", blob=_docx(["投标文件缺少封面"]), raw="[]")
    executor.execute_task(tid)
    row = _round(r2)
    assert row.status == "done" and "保持原样" in row.summary


def test_execute_task_fix_caps_items_at_max_fix_items(monkeypatch, fstore):
    tid = PFX + "-fixcap"
    _mk_template()
    r1 = _mk_round(tid, status="annotated", file_version="v1", round_no=1)
    for i in range(5):
        FileReviewAnnotationService.create(
            round_id=r1, task_id=tid, file_id=PFX + "-file", file_version="v1",
            anchor="{}", matched_text=f"缺项{i}", type="format", severity="low",
            issue="i", suggestion="", source="ai", status="open",
            tenant_id=PFX, created_by=PFX)
    r2 = _mk_round(tid, status="fixing", file_version="v2", round_no=2)
    monkeypatch.setattr(executor, "MAX_FIX_ITEMS", 2)
    prompts = []
    monkeypatch.setattr(executor, "_load_original_blob", lambda t, f: _docx(["待修复文本"]))
    monkeypatch.setattr(executor, "_call_llm",
                        lambda t, s, u: prompts.append(u) or "[]")
    executor.execute_task(tid)
    assert "[3]" not in prompts[0] and "[2]" in prompts[0]
    assert _round(r2).status == "done"


def test_execute_task_fix_reads_previous_fixed_version_as_input(monkeypatch, fstore):
    """多轮叠加：第 3 轮的输入必须是第 2 轮修复后的版本，不是原件。"""
    tid = PFX + "-fixchain"
    _mk_template()
    v2_key = store_key(tid, "v2")
    fstore.blobs[(f"{PFX}-downloads", v2_key)] = _docx(["已修过一次的正文"])
    r1 = _mk_round(tid, status="annotated", file_version="v1", round_no=1)
    FileReviewAnnotationService.create(
        round_id=r1, task_id=tid, file_id=PFX + "-file", file_version="v1",
        anchor="{}", matched_text="缺封面", type="format", severity="high",
        issue="i", suggestion="", source="ai", status="open", tenant_id=PFX, created_by=PFX)
    FileReviewRoundService.update_status(
        _mk_round(tid, status="done", file_version="v2", round_no=2), "done", minio_path=v2_key)
    r3 = _mk_round(tid, status="fixing", file_version="v3", round_no=3)
    prompts = []
    monkeypatch.setattr(executor, "_load_original_blob",
                        lambda t, f: _docx(["原件正文，不该被读到"]))
    monkeypatch.setattr(executor, "_call_llm", lambda t, s, u: prompts.append(u) or "[]")
    executor.execute_task(tid)
    assert "已修过一次的正文" in prompts[0] and "原件正文" not in prompts[0]
    assert _round(r3).status == "done"


def test_execute_task_reraises_when_failure_status_cannot_be_written(monkeypatch, fstore):
    """收口写库都失败时必须 raise，把球踢回 spawn 的独立 CAS——否则轮次永久卡死。"""
    tid = PFX + "-wede"
    _mk_template()
    _mk_round(tid)
    _wire(monkeypatch, blob=_docx(["一段足够长的正文用于通过最小长度校验，重复重复重复重复。"]),
          raw="完全无法解析")

    def _boom_update(*a, **k):
        raise RuntimeError("mysql down")

    # 直接替换 Model.update（类属性覆盖，经 FileReviewRoundService.update_status 的
    # `cls.model.update(...)` 可见），让「收口 failed」这一步也失败。
    monkeypatch.setattr(FileReviewRound, "update", _boom_update)
    with pytest.raises(RuntimeError):
        executor.execute_task(tid)


def test_execute_task_skips_annotations_outside_declared_type_scope(monkeypatch, fstore):
    """模板声明 scope=format，LLM 回了个越界 type：记录（不静默丢问题）但打 warning。"""
    tid = PFX + "-scope"
    _mk_template(types=("format",))
    rid = _mk_round(tid)
    raw = json.dumps([{"matched_text": "甲甲甲", "type": "clause", "severity": "low", "issue": "i"}])
    _wire(monkeypatch, blob=_docx(["甲甲甲是一段足够长的正文内容，重复重复重复重复。"]), raw=raw)
    executor.execute_task(tid)
    assert _round(rid).status == "annotated"
    assert len(_anns(tid)) == 1 and _anns(tid)[0].type == "clause"


def test_execute_task_drops_empty_shell_annotations(monkeypatch, fstore):
    tid = PFX + "-shell"
    _mk_template()
    rid = _mk_round(tid)
    raw = json.dumps([{"matched_text": "", "issue": "", "severity": "low"},
                      {"matched_text": "甲甲甲", "issue": "真的问题", "severity": "low"}])
    _wire(monkeypatch, blob=_docx(["甲甲甲是一段足够长的正文内容，重复重复重复重复。"]), raw=raw)
    executor.execute_task(tid)
    assert len(_anns(tid)) == 1 and _round(rid).status == "annotated"
```

**1b. `test/test_file_review_service.py` 追加三个用例**

```python
def test_create_round_persists_normalized_kb_ids():
    tid = PFX + 'kbids'
    rid = FileReviewRoundService.create_round(
        task_id=tid, file_id=PFX + '-file', round_no=1, template_id='',
        user_query='q', file_version='v1', status='reviewing',
        tenant_id=PFX, kb_ids=['kb-1', 'kb-2'],
    )
    assert json.loads(_round_row(rid).kb_ids) == ['kb-1', 'kb-2']
    rid2 = FileReviewRoundService.create_round(
        task_id=tid, file_id=PFX + '-file', round_no=2, template_id='',
        user_query='q', file_version='v1', status='reviewing', tenant_id=PFX,
    )
    assert _round_row(rid2).kb_ids is None
    rid3 = FileReviewRoundService.create_round(
        task_id=tid, file_id=PFX + '-file', round_no=3, template_id='',
        user_query='q', file_version='v1', status='reviewing',
        tenant_id=PFX, kb_ids=['kb-9'],
    )
    assert _round_row(rid3).kb_ids == '["kb-9"]'


def test_list_pending_by_task_is_task_wide_not_round_scoped():
    """修复轮不产标注；按 round_no 圈定会让第 3 轮取到空集而静默不修。"""
    tid = PFX + 'pending'
    r_old = _mk_round(tid, 1, 'annotated')
    r_cur = _mk_round(tid, 2, 'fixing')
    for r, st in ((r_old, 'open'), (r_old, 'fixed'), (r_cur, 'new'), (r_cur, 'wontfix')):
        FileReviewAnnotationService.create(
            round_id=r, task_id=tid, file_id=PFX + '-file', file_version='v1',
            anchor='{}', matched_text='t', type='format', severity='low',
            issue='i', suggestion='', source='ai', status=st, tenant_id=PFX)
    got = [a.status for a in FileReviewAnnotationService.list_pending_by_task(tid)]
    assert sorted(got) == ['new', 'open']
    assert FileReviewAnnotationService.list_pending_by_task('') == []


def test_delete_by_round_only_removes_that_round():
    tid = PFX + 'delround'
    r1, r2 = _mk_round(tid, 1, 'reviewing'), _mk_round(tid, 2, 'reviewing')
    for r in (r1, r2):
        FileReviewAnnotationService.create(
            round_id=r, task_id=tid, file_id=PFX + '-file', file_version='v1',
            anchor='{}', matched_text='t', type='format', severity='low',
            issue='i', suggestion='', source='ai', tenant_id=PFX)
    assert FileReviewAnnotationService.delete_by_round(r1) == 1
    rest = FileReviewAnnotation.select().where(FileReviewAnnotation.task_id == tid)
    assert [a.round_id for a in rest] == [r2]
```

`_round_row(rid)` 需要新增到该文件的辅助区：

```python
def _round_row(rid):
    return FileReviewRoundService.get_by_id(rid)
```

**1c. `test/test_file_review_spawn.py` 追加一个用例**

```python
def test_force_fail_marks_fixing_round_too(monkeypatch):
    """进程在**修复中**被杀时轮次会滞留在 fixing：CAS 只认 reviewing 会让它永远转圈。"""
    tid = PFX + 'fixing'
    _mk_round(tid, status='fixing')

    def boom(task_id):
        raise RuntimeError("boom")

    monkeypatch.setattr(spawn, 'execute_task', boom)
    spawn.spawn_review_task(tid)
    assert _wait_until(lambda: [r.status for r in _rounds(tid)] == ['failed'])
```

**1d. `test/test_file_review_db.py` 改三处 + 换一个用例**

- `test_presets_prompt_template_format_safe`：

```python
def test_presets_prompt_template_format_safe():
    allowed = {"user_query", "file_text", "references"}
    for t in _PRESET_REVIEW_TEMPLATES:
        tpl = t["user_prompt_template"]
        fields = {n for _, n, _, _ in string.Formatter().parse(tpl) if n}
        assert fields == allowed, f"{t['id']} 占位符={fields}"
        stripped = tpl.replace("{{", "").replace("}}", "")
        assert stripped.count("{") == stripped.count("}") == len(allowed)
        out = tpl.format(user_query="Q", file_text="E", references="R")
        assert "{{" not in out and "}}" not in out
        assert out.count("{") == out.count("}") and out.count("[") == out.count("]")
        assert out.rstrip().endswith("]") and '"matched_text"' in out
        assert '"anchor"' not in out          # 锚点由服务端反查，不得诱导 LLM 编造
```

- `test_presets_prompt_template_requires_all_fields`：把 `format(user_query="Q", file_excerpt="E")` 换成 `format(user_query="Q", file_text="E")`。
- `test_ensure_guard_not_fooled_by_decoy_row` → 语义已变（守卫不再按 ID 计数短路），换成：

```python
def test_seed_leaves_foreign_rows_untouched():
    """drift sync 只认预置 ID：别的租户/用户的模板行不得被写入或改写。"""
    decoy = "decoy_tpl_" + PFX
    FileReviewTemplate.delete().where(FileReviewTemplate.id == decoy).execute()
    FileReviewTemplate.create(
        id=decoy, name="别人的模板", description="d", system_prompt="s",
        user_prompt_template="{user_query}", annotation_types="[]",
        enabled=0, tenant_id="other-tenant", created_by="someone",
    )
    try:
        _seed_file_review_templates()
        row = FileReviewTemplate.get(FileReviewTemplate.id == decoy)
        assert row.enabled == 0 and row.tenant_id == "other-tenant" and row.name == "别人的模板"
    finally:
        FileReviewTemplate.delete().where(FileReviewTemplate.id == decoy).execute()
```

- 追加：

```python
def test_seed_drift_syncs_prompt_text_but_never_re_enables():
    """常量文案修订必须能到已初始化的库（否则改了也白改）；但管理员显式停用的
    enabled=0 是用户态，启动同步不得把它重新打开。"""
    tpl_id = _PRESET_REVIEW_TEMPLATES[0]["id"]
    FileReviewTemplate.update(
        system_prompt="被改坏的旧值", enabled=0,
    ).where(FileReviewTemplate.id == tpl_id).execute()
    try:
        assert _seed_file_review_templates() == ""
        row = FileReviewTemplate.get(FileReviewTemplate.id == tpl_id)
        assert row.system_prompt == _PRESET_REVIEW_TEMPLATES[0]["system_prompt"]
        assert row.user_prompt_template == _PRESET_REVIEW_TEMPLATES[0]["user_prompt_template"]
        assert row.enabled == 0
    finally:
        _seed_file_review_templates()
        FileReviewTemplate.update(enabled=1).where(FileReviewTemplate.id == tpl_id).execute()
```

---

- [ ] **Step 2: 跑测试确认失败**

```bash
uv run --no-sync pytest test/test_file_review_executor.py test/test_file_review_service.py \
  test/test_file_review_spawn.py test/test_file_review_db.py -x -q
```

Expected（实现前，必须真的看到这些失败）：
- `ModuleNotFoundError: No module named 'rag.svr.file_review.executor'`
- 去掉 executor 导入后：`TypeError: create_round() got an unexpected keyword argument 'kb_ids'`
- `AttributeError: type object 'FileReviewAnnotationService' has no attribute 'list_pending_by_task'` / `delete_by_round`
- `test_force_fail_marks_fixing_round_too` → 轮次停在 `fixing`
- db 测试 `assert fields == {'user_query','file_excerpt','references'}` 失败

若某个失败与预期**不符**，先停下来查清原因，不要继续往下写实现。

---

- [ ] **Step 3: 基础设施改造（executor 之外的四份改动）**

**3a. `api/db/services/file_review_service.py`**

文件头 import 追加：

```python
import json
import logging

from common.utils import get_uuid            # 已存在
from api.db.db_models import json_dumps      # 若顶层未导入，按本文件既有 import 风格补齐
```

（以文件现有 import 块为准，缺失才加；`logger = logging.getLogger(__name__)` 若已有则不重复。）

新增模块级辅助函数（放在 `_clamp_str` 下方）：

```python
def _normalize_kb_ids(kb_ids) -> str | None:
    """把 kb_ids 统一成 JSON 文本落库（None / 空 → None）。

    接受三种入参并归一，是因为三个调用方（T7 节点 / T8 工具 / T9 API）拿到的形态不同：
    节点来自画布 DSL（list）、工具来自 LLM 参数解析（可能是 JSON 文本）、API 来自
    request body。归一放在 Service 层，避免每个调用方各写一遍、口径漂移。
    """
    if kb_ids is None:
        return None
    if isinstance(kb_ids, str):
        raw = kb_ids.strip()
        if not raw:
            return None
        try:
            val = json.loads(raw)
        except Exception:
            return json_dumps([raw])          # 裸的单个 id 文本
        return _normalize_kb_ids(val)
    if isinstance(kb_ids, (list, tuple, set)):
        ids = [str(x) for x in kb_ids if x]
        return json_dumps(ids) if ids else None
    return None
```

`FileReviewRoundService.create_round` 签名与写入改为：

```python
    @classmethod
    @DB.connection_context()
    def create_round(cls, *, task_id: str, file_id: str, round_no: int,
                     template_id: str, user_query: str, file_version: str,
                     status: str, tenant_id: str = "", created_by: str = "",
                     kb_ids=None) -> str:
        """新建一轮审核，返回轮次 id。

        tenant_id / created_by / kb_ids 由调用方（T7 节点、T8 工具、T9 API）从会话
        上下文透传，本层不猜。kb_ids 归一为 JSON 文本（见 _normalize_kb_ids）：修复轮
        与重试都要用同一批知识库，故必须随轮次持久化，不能只活在当次请求里。
        其余契约不变（必填列为 None 时不吞异常，时间字段全部由框架写）。
        """
        rid = get_uuid()
        cls.model.create(
            id=rid, task_id=task_id, file_id=file_id, round_no=round_no,
            template_id=template_id, user_query=user_query,
            file_version=_clamp_str(cls.model, "file_version", file_version),
            status=_clamp_str(cls.model, "status", status),
            tenant_id=tenant_id, created_by=created_by,
            kb_ids=_normalize_kb_ids(kb_ids),
        )
        return rid
```

`FileReviewAnnotationService` 末尾追加两个类方法：

```python
    @classmethod
    @DB.connection_context()
    def list_pending_by_task(cls, task_id: str) -> list:
        """该 task **全部轮次**中 status ∈ {open, new} 的标注，按创建时间升序。

        与 list_open_or_new_for_next_round 的区别是**不按轮次圈定**，因为修复轮本身
        不产标注：第 3 轮修复要处理的是第 1 轮 review 留下的 open 项，按 round_no
        圈定会取到空集，结果是一轮「什么都不修」的静默空转。

        task_id 为空串/None 时短路返回 []（同 max_completed_round_no）：空串行可落库，
        不短路会把这类脏行当成某个任务的待修复项。
        """
        if not task_id:
            return []
        return list(cls.model.select().where(
            (cls.model.task_id == task_id)
            & cls.model.status.in_(PENDING_ANNOTATION_STATUSES)
        ).order_by(cls.model.create_time.asc()))

    @classmethod
    @DB.connection_context()
    def delete_by_round(cls, round_id: str) -> int:
        """删除该轮次的全部标注，返回删除行数。

        用途是**保重试幂等**：进程被杀后轮次会滞留在 reviewing，重试会重跑整轮，
        若不清旧标注就会把每条问题再写一遍，面板上出现成对重复。范围严格限定在
        round_id（不按 task_id），避免把历史轮次的人工批注一起抹掉。
        """
        if not round_id:
            return 0
        return cls.model.delete().where(cls.model.round_id == round_id).execute()
```

**3b. `rag/svr/file_review/spawn.py`**

`_force_fail_round` 的 CAS 条件改为覆盖修复中：

```python
        with DB.connection_context():
            FileReviewRoundService.model.update(status="failed", error=error).where(
                FileReviewRoundService.model.task_id == task_id,
                FileReviewRoundService.model.status.in_(("reviewing", "fixing")),
            ).execute()
```

docstring 同步补一句：

```
    幂等且范围受限：只命中 status ∈ {reviewing, fixing} 的该 task 行——executor 已把该轮置
    done/failed 时命中 0 行，别的任务的滞留轮次也不受影响。fixing 必须一起收：
    进程在修复中被杀时轮次会停在 fixing，只认 reviewing 会让面板上永远转圈。
```

**3c. `api/db/db_models.py`**

（i）`FileReviewRound` 加列（放在 `file_version` 之后）：

```python
    kb_ids = TextField(null=True)  # JSON 数组文本：本轮用的知识库 id（修复轮与重试复用）
```

（ii）`migrate_db` 里补迁移（参照同文件 `flow_comment` 的写法，**必须放在建表之后**）：

```python
    alter_db_add_column(migrator, "file_review_round", "kb_ids", TextField(null=True))
```

（iii）`FileReviewAnnotation.anchor` 的注释纠正（原注释写的是过时且错误的三元组）：

```python
    anchor = TextField(null=False)  # JSON: docx {p_idx,p_hash,a_occ,p_total}；定位失败为 {}
```

（iv）五个预置模板的 `user_prompt_template` 全部改成下面这个形态（把 `{file_excerpt}` 换成 `{file_text}`、删掉示例里的 `"anchor": {{...}}`、补「必须逐字摘录」的约束；`type` 值按各自模板的 `annotation_types` 填）：

```python
        "user_prompt_template": (
            "用户需求：{user_query}\n\n"
            "文件正文：\n{file_text}\n\n"
            "参考资料：\n{references}\n\n"
            "请审视全文，输出 JSON 标注列表（type='format'）：\n"
            "matched_text 必须逐字摘自文档原文（用于定位与修复），不要改写或概括。\n"
            '[{{"matched_text": "...", "type": "format", '
            '"severity": "high|medium|low", "issue": "...", "suggestion": "..."}}]'
        ),
```

五个模板的 `type` 取值依次为：`bid_doc_format` → `format`，`bid_response_complete` → `completeness`，`bid_substantive_clause` → `clause`，`bid_qualification` → `qualification`，`bid_price_review` → `price`。

（v）`_seed_file_review_templates` / `_ensure_file_review_templates` 重写：

```python
def _seed_file_review_templates():
    """幂等补齐并同步预置审核模板（tenant_id='' 即系统预置）。返回失败摘要（空串 = 全部成功）。

    逐行 insert-or-update，而非「已存在即 continue」：
    旧实现 + `_ensure_file_review_templates` 的 ID 计数短路会让**常量文案的修订永远到不了
    已初始化的库**——改 prompt 等于白改，线上跑的还是第一次建库时的文本。
    已存在的行只 UPDATE 提示词内容列，**不动 enabled**：管理员显式停用（enabled=0）是用户态，
    启动时的 drift sync 无权把它重新打开；tenant_id / created_by 同样不碰（不属于内容）。
    下游没有模板编辑端点（只有 list），故无条件同步不会覆盖任何用户改动。
    """
    errs = []
    for tpl in _PRESET_REVIEW_TEMPLATES:
        try:
            fields = {
                "name": tpl["name"], "description": tpl["description"],
                "system_prompt": tpl["system_prompt"],
                "user_prompt_template": tpl["user_prompt_template"],
                "annotation_types": json_dumps(tpl["annotation_types"]),
            }
            if FileReviewTemplate.get_or_none(FileReviewTemplate.id == tpl["id"]):
                FileReviewTemplate.update(**fields).where(
                    FileReviewTemplate.id == tpl["id"]).execute()
            else:
                FileReviewTemplate.create(
                    id=tpl["id"], enabled=1, tenant_id="", created_by="system", **fields)
        except Exception as e:
            errs.append(f"{tpl.get('id')}: {e.__class__.__name__}: {e}")
    return "; ".join(errs)


def _ensure_file_review_templates():
    """确保预置模板齐全且与常量同步（幂等自愈）。返回错误文本，正常返回 None。

    不再先按 ID 集合计数再决定要不要播：计数守卫一旦通过就永远跳过 seed，模板文案
    的修订便再也进不去。改为每个启动无条件跑一遍 drift sync（5 行 UPDATE 的开销可忽略）。
    migrate_db 运行在 logging.disable(ERROR) 窗口内，故本函数不抛异常、也不直接 log ERROR，
    而是把失败摘成文本返回给调用方。
    """
    try:
        return _seed_file_review_templates() or None
    except Exception as e:
        return f"{e.__class__.__name__}: {e}"
```

**3d. `docs/superpowers/plans/2026-09-16-file-review-node.md` 的 T4 说明句**

把 T4 里「v1 对非 docx 文件只做审查（出标注），修复走纯文本降级（见 T6）」这句改成：

```
**xlsx / pdf 修复路径不在本任务范围，且 v1 明确不做**：设计里 xlsx 锚点 `{sheet, cell}` 仍是
占位形态，逐单元格定位未设计。T6 的修复轮对非 docx 文件**只出标注不自动改**——收口 `done`
并提示「该文件类型不支持自动修复（v1 仅支持 Word .docx），请按批注手动修改」。原稿写的
「修复走纯文本降级」是错的：把解码文本当新版本存回去会毁掉原件，还会成为下一轮审核的输入。
```

---

- [ ] **Step 4: 跑基础设施测试**

```bash
uv run --no-sync pytest test/test_file_review_service.py test/test_file_review_spawn.py \
  test/test_file_review_db.py -q
```

Expected: 全绿（executor 测试此刻仍因模块不存在而 collect error，属预期）。

---

- [ ] **Step 5: 创建 `rag/svr/file_review/executor.py`**

```python
"""文件审核执行器：多轮状态机（审查轮 reviewing → annotated；修复轮 fixing → done）。

设计边界（为什么这样切）：
- 本模块**不认识**「此刻该审查还是该修复」：那是 T7 画布节点 / T8 对话工具 / T9 REST 的
  职责——它们先写好轮次行（`status='reviewing'` 或 `'fixing'`）再交给 spawn 拉起本模块。
  本模块只按轮次行推进状态机，故三个入口共用同一套逻辑，各自不必复制。
- 纯变换一律委托已测过的兄弟模块，不在这里重复实现：检索拼装 → `kb_aggregator`；
  锚点语义 → `template_fill.docx_utils`（与 B 端填写点直定位、前端 fnvHash32x2 同口径）；
  格式保真替换 → `file_review.patcher`。
- 不 import Quart / SSE：进度由 T9 的 progress 端点按轮次行反查（三入口共用同一份进度真相），
  在此推流会把「执行」与「某个具体连接」绑死。
- T5 契约：`execute_task` 必须保证返回（防重入集合没有超时/看门狗，永久阻塞 = 该 task 被
  永远判为「已在执行中」，只能重启进程恢复）。故每条外部调用都有明确的失败出口。
- 幂等：状态非 reviewing/fixing 的轮次（已完结 / 未知）安静返回，重复 spawn 无副作用；
  同一轮重跑会先删该轮旧标注（否则进程被杀后重试会留下两套标注）。
"""
import asyncio
import json
import logging
import re

from api.db.services.file_review_service import (
    FileReviewAnnotationService,
    FileReviewRoundService,
    FileReviewTemplateService,
)
from common import settings
from rag.svr.file_review import kb_aggregator
from rag.svr.file_review.patcher import apply_patches_to_docx
from rag.svr.template_fill.docx_utils import iter_docx_paragraphs, norm_ws, para_hash32x2

logger = logging.getLogger(__name__)

__all__ = ["FileReviewError", "execute_task"]

DEFAULT_TEMPLATE_ID = "bid_doc_format"
KB_TOP_K = 6
KB_BUDGET_TOKENS = 3000
FILE_TEXT_MAX_CHARS = 40000
TRUNCATED_NOTE = "\n（注：文档较长，以上为可纳入本次审核的正文，其余部分未纳入）"
MIN_FILE_TEXT_CHARS = 50
QUERY_MAX = 300
LLM_RAW_MAX = 200_000
MAX_FIX_ITEMS = 20
MATCHED_TEXT_MAX = 500
ISSUE_MAX = 1000
SUGGESTION_MAX = 1000

_CTRL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

# 各级别的同义写法归一：UI 的三色方案与用户的 levels 过滤都按 high/medium/low 匹配，
# 放任 LLM 自由发挥（"严重"/"Critical"/"warning"）会让这两处同时失配。
_SEVERITY_ALIASES = {
    "high": "high", "严重": "high", "高": "high", "critical": "high", "blocker": "high",
    "medium": "medium", "中": "medium", "中等": "medium", "moderate": "medium",
    "warning": "medium",
    "low": "low", "低": "low", "轻微": "low", "minor": "low", "info": "low",
}

_ARRAY_GREEDY = re.compile(r"\[[\s\S]*\]")
_ARRAY_LAZY = re.compile(r"\[[\s\S]*?\]")
_OBJECT_GREEDY = re.compile(r"\{[\s\S]*\}")

FIX_SYSTEM = (
    "你是文档修复助手。用户给你一份文档正文和一批待修复的问题标注，"
    "你要为每条标注给出**最小改动**的替换方案：find 必须逐字摘自文档、且全文只出现一次；"
    "replace 是替换后的文本。无法唯一定位或无需改动的条目直接省略，不要编造原文。"
    '只输出 JSON：{"patches": [{"idx": 1, "find": "原文片段", "replace": "新文本"}]}'
)


class FileReviewError(Exception):
    """已知的、可直接展示给用户的中断原因（文案即面向用户的说明）。"""


# ── 入口 ─────────────────────────────────────────────────────────
def execute_task(task_id: str) -> None:
    """按该 task 最后一个轮次的状态推进状态机；无可推进项则安静返回。"""
    try:
        rounds = FileReviewRoundService.get_by_task(task_id)
    except Exception:
        # 连轮次都读不出来（DB 不可用）→ 交给 spawn 的兜底 CAS 再试一次
        logger.exception("file review: load rounds failed, task_id=%s", task_id)
        raise
    cur = rounds[-1] if rounds else None
    if cur is None:
        logger.warning("file review: no round row for task_id=%s", task_id)
        return
    handler = {"reviewing": _run_review_round, "fixing": _run_fix_round}.get(cur.status)
    if handler is None:
        # 已完结（annotated/done/failed）或未知状态：重复 spawn 的幂等出口
        logger.info("file review: round %s status=%s, nothing to do", cur.id, cur.status)
        return
    try:
        handler(cur, _latest_version_name(rounds, cur))
    except Exception as e:
        err = str(e) if isinstance(e, FileReviewError) else f"{e.__class__.__name__}: {e}"
        logger.exception("file review round failed, round_id=%s", cur.id)
        try:
            FileReviewRoundService.update_status(cur.id, "failed", error=err[:2000])
        except Exception:
            # 收口都写不进去：绝不能让异常在这里被吞掉——那样 execute_task 正常返回，
            # spawn 的崩溃兜底 CAS 永不触发，该轮次就永久卡在中间态。重新抛出。
            logger.exception("file review: cannot record failure, round_id=%s", cur.id)
            raise


# ── 审查轮 ───────────────────────────────────────────────────────
def _run_review_round(round_row, version_name) -> None:
    tpl = _resolve_template(round_row.template_id)
    blob = _load_input_blob(round_row, version_name)
    items = _load_docx_items(blob)
    file_text = _compose_file_text(items)
    chunks = _retrieve_chunks(round_row.tenant_id, _parse_kb_ids(round_row.kb_ids),
                              _retrieval_query(round_row, tpl))
    references = kb_aggregator.aggregate_references(kb_chunks=chunks, budget=KB_BUDGET_TOKENS)
    try:
        user_prompt = tpl.user_prompt_template.format(
            user_query=round_row.user_query or "", file_text=file_text, references=references)
    except Exception as e:
        raise FileReviewError(f"审核模板 {tpl.id} 的提示词占位符不合法：{e}") from e
    raw = _call_llm(round_row.tenant_id, tpl.system_prompt, user_prompt)
    parsed = _parse_annotation_items(raw)
    if parsed is None:
        # None ≠ []：解析失败不能伪装成「审核通过」，否则用户看到的是一个虚假的干净结果
        FileReviewRoundService.update_status(
            round_row.id, "failed", llm_raw=_clip_raw(raw),
            error="LLM 输出无法解析为标注列表（原始响应见 llm_raw），请重试")
        return
    FileReviewAnnotationService.delete_by_round(round_row.id)   # 重跑先清旧标注，保幂等
    stats = _persist_annotations(round_row, _collect_annotations(parsed, tpl, items))
    FileReviewRoundService.update_status(
        round_row.id, "annotated", llm_raw=_clip_raw(raw), summary=_summary_from_stats(stats))


# ── 修复轮 ───────────────────────────────────────────────────────
def _run_fix_round(round_row, version_name) -> None:
    blob = _load_input_blob(round_row, version_name)
    pending = FileReviewAnnotationService.list_pending_by_task(round_row.task_id)
    if not pending:
        FileReviewRoundService.update_status(round_row.id, "done", summary="没有待修复的问题")
        return
    if blob[:2] != b"PK":
        # 非 docx 一律不自动改：把解码文本当新版本存回去会毁掉原件，还会成为下一轮
        # 审核的输入。诚实收口 done（不是 failed——没有可重试的东西），标注保持原样。
        FileReviewRoundService.update_status(
            round_row.id, "done",
            summary=(f"共 {len(pending)} 项待修复；该文件类型不支持自动修复"
                     "（v1 仅支持 Word .docx），请按批注手动修改"))
        return
    chosen = pending[:MAX_FIX_ITEMS]
    items = _load_docx_items(blob)
    file_text = _compose_file_text(items)
    raw = _call_llm(round_row.tenant_id, FIX_SYSTEM,
                    _build_fix_prompt(round_row, file_text, chosen))
    parsed = _parse_patch_items(raw)
    if parsed is None:
        FileReviewRoundService.update_status(
            round_row.id, "failed", llm_raw=_clip_raw(raw),
            error="LLM 输出无法解析为修复补丁列表（原始响应见 llm_raw），请重试")
        return
    patches, by_pos = _plan_patches(parsed, chosen)
    if parsed and not patches:
        # 有条目但 idx 全对不上：畸形响应，不能伪装成「无需改动」
        FileReviewRoundService.update_status(
            round_row.id, "failed", llm_raw=_clip_raw(raw),
            error="修复补丁的 idx 均无法与本轮标注对应，请重试")
        return
    if not patches:
        # 合法但空：LLM 判断无需改动。判 failed 会诱发无效重试
        FileReviewRoundService.update_status(
            round_row.id, "done", llm_raw=_clip_raw(raw),
            summary=f"共 {len(chosen)} 项待修复；本轮未产出可落地的修复补丁（保持原样）")
        return
    new_blob, applied = apply_patches_to_docx(blob, patches)
    fixed_n = _settle_annotations(chosen, by_pos, applied)
    extra = {"minio_path": _store_version_blob(round_row, new_blob)} if any(applied) else {}
    open_n = len(chosen) - fixed_n
    summary = f"本轮修复 {fixed_n} 项" + (
        f"；{open_n} 项未能唯一定位原文（保持原样）" if open_n else "")
    FileReviewRoundService.update_status(round_row.id, "done", llm_raw=_clip_raw(raw),
                                        summary=summary, **extra)


# ── 外部依赖（模块级 seam，便于测试注入）───────────────────────────
def _load_input_blob(round_row, version_name) -> bytes:
    """修复轮的输入是**上一轮修复后的版本**（多轮叠加），无版本则回原件。"""
    if version_name:
        blob = settings.STORAGE_IMPL.get(f"{round_row.tenant_id}-downloads", version_name)
        if blob:
            return blob
        logger.warning("file review: version blob missing, round_id=%s name=%s",
                       round_row.id, version_name)
    return _load_original_blob(round_row.tenant_id, round_row.file_id)


def _load_original_blob(tenant_id: str, file_id: str) -> bytes:
    """取文件原件字节；三级兜底与 file_api 的下载链路同口径，全部落空才报错。"""
    from api.db.services.file2document_service import File2DocumentService
    from api.db.services.file_service import FileService

    cands = []
    ok, file = FileService.get_by_id(file_id)
    if ok and file is not None:
        cands.append((file.parent_id, file.location))
    try:
        cands.append(File2DocumentService.get_storage_address(file_id=file_id))
    except Exception:
        logger.exception("file review: resolve storage address failed, file_id=%s", file_id)
    for bucket, name in cands:
        if not bucket or not name:
            continue
        blob = settings.STORAGE_IMPL.get(bucket, name)
        if blob:
            return blob
    blob = settings.STORAGE_IMPL.get(f"{tenant_id}-downloads", file_id)
    if blob:
        return blob
    raise FileReviewError(f"文件不存在或已删除（file_id={file_id}），无法审核")


def _store_version_blob(round_row, blob: bytes) -> str:
    """把修复后的文档存为版本对象，返回对象名。

    对象名只用 task_id + file_version（不含轮次号）是刻意的：同一版本号重复跑会**覆盖**，
    而不是不断堆孤儿对象；重试也天然幂等。
    """
    name = f"frv-{round_row.task_id}-{round_row.file_version}"
    settings.STORAGE_IMPL.put(f"{round_row.tenant_id}-downloads", name, blob)
    return name


def _retrieve_chunks(tenant_id: str, kb_ids: list, query: str) -> list:
    """检索参考资料。失败**不吞**：静默按「零参考」继续，等于告诉用户「标准就这些」。"""
    if not kb_ids:
        return []
    from rag.svr.template_fill.executor import retrieve_slot

    return asyncio.run(retrieve_slot(tenant_id, kb_ids, query, top_k=KB_TOP_K))


def _call_llm(tenant_id: str, system_prompt: str, user_prompt: str) -> str:
    """同步线程内跑一次 chat 补全（线程无事件循环，必须 asyncio.run）。"""
    from api.db.joint_services.tenant_model_service import get_tenant_default_model_by_type
    from api.db.services.llm_service import LLMBundle
    from common.constants import LLMType

    mdl = LLMBundle(tenant_id, get_tenant_default_model_by_type(tenant_id, LLMType.CHAT))
    return asyncio.run(mdl.async_chat(system_prompt, [{"role": "user", "content": user_prompt}]))


# ── 轮次元数据 ───────────────────────────────────────────────────
def _latest_version_name(rounds: list, upto) -> str | None:
    """取 round_no <= upto 的最近一个已落盘版本对象名（多轮叠加的输入基线）。

    rounds 已按 round_no 升序，故最后一个命中即最近；排除当前轮自身，避免把
    「本轮的产物」当成「本轮的输入」。
    """
    name = None
    for r in rounds:
        if r.id != upto.id and r.round_no <= upto.round_no and r.minio_path:
            name = r.minio_path
    return name


def _parse_kb_ids(raw) -> list:
    """把轮次行里的 kb_ids（JSON 文本）还原成 id 列表；脏值降级为空列表并告警。"""
    if not raw:
        return []
    if isinstance(raw, (list, tuple, set)):
        return [str(x) for x in raw if x]
    try:
        val = json.loads(raw)
    except Exception:
        logger.warning("file review: kb_ids is not valid JSON, ignored: %r", raw)
        return []
    if isinstance(val, list):
        return [str(x) for x in val if x]
    logger.warning("file review: kb_ids JSON is not a list, ignored: %r", raw)
    return []


def _resolve_template(template_id):
    tpl = FileReviewTemplateService.get_by_id(template_id) if template_id else None
    if tpl is None:
        tpl = FileReviewTemplateService.get_by_id(DEFAULT_TEMPLATE_ID)
    if tpl is None:
        raise FileReviewError(
            f"审核模板不存在（template_id={template_id or DEFAULT_TEMPLATE_ID}），"
            "请检查预置模板是否已初始化")
    return tpl


def _template_types(tpl) -> list:
    raw = getattr(tpl, "annotation_types", None)
    if not raw:
        return []
    try:
        val = json.loads(raw) if isinstance(raw, str) else raw
    except Exception:
        logger.warning("file review: template %s annotation_types unparsable: %r", tpl.id, raw)
        return []
    return [str(x) for x in val] if isinstance(val, list) else []


def _retrieval_query(round_row, tpl) -> str:
    parts = [getattr(tpl, "name", "") or "", round_row.user_query or ""]
    q = " ".join(p.strip() for p in parts if p and p.strip())
    return q[:QUERY_MAX] or "招标文件要求"


# ── 字符串归一 ───────────────────────────────────────────────────
def _clean_str(value, max_len: int = 0) -> str:
    """只接受 str（其余一律当没填）；剥控制字符（防注入进 LLM 提示或前端渲染）。"""
    if not isinstance(value, str):
        return ""
    out = _CTRL_RE.sub("", value).strip()
    return out[:max_len] if max_len > 0 else out


def _norm_token(value, fallback: str = "other") -> str:
    """类型/来源类短标记归一：小写、去控制符、限长。

    刻意**不做白名单改写**——模板自带 annotation_types 声明了 scope，越界与否只用于
    告警（见 _collect_annotations），不在这里篡改 LLM 的判断。
    """
    return _clean_str(value, 32).lower() or fallback


def _norm_severity(value) -> str:
    token = _clean_str(value, 32).lower()
    sev = _SEVERITY_ALIASES.get(token)
    if sev is None:
        logger.warning("file review: unknown severity %r, fallback to medium", value)
        return "medium"
    return sev


def _clip_raw(raw) -> str:
    return (raw or "")[:LLM_RAW_MAX]


# ── 文档装配与锚点 ───────────────────────────────────────────────
def _load_docx_items(blob: bytes) -> list:
    if blob[:2] != b"PK":
        raise FileReviewError("暂不支持审核该文件类型（当前仅支持 Word .docx），请转换后重试")
    try:
        return iter_docx_paragraphs(blob)
    except Exception as e:
        raise FileReviewError(f"无法解析文档（可能已损坏或非 .docx 格式）：{e}") from e


def _compose_file_text(items: list) -> str:
    text = "\n".join(it["text"].strip() for it in items if (it.get("text") or "").strip())
    if len(norm_ws(text)) < MIN_FILE_TEXT_CHARS:
        # 扫描件/图片文档提不出文字：把空正文喂给「请审视全文」的提示词等于请模型编
        raise FileReviewError("文档正文为空或无法提取文字（可能是扫描件/图片文档），无法审核")
    if len(text) > FILE_TEXT_MAX_CHARS:
        # 静默截断会让模型断言「全文未提及某条款」——截断必须在正文里说明
        return text[:FILE_TEXT_MAX_CHARS] + TRUNCATED_NOTE
    return text


def _count_occurrences(norm: str, needle: str) -> int:
    """norm 通道的 indexOf step+1 计数（允许重叠），与 docx_utils 生成 a_occ 时同口径：
    下划线串「＿＿＿＿」含「＿＿＿」在 step+1 下算 2 次，str.count 非重叠只算 1 次。"""
    count, start = 0, 0
    while True:
        idx = norm.find(needle, start)
        if idx < 0:
            return count
        count += 1
        start = idx + 1


def _compute_anchor(addr_items: list, matched_text: str) -> dict:
    """按 matched_text 反查权威锚点 {p_idx,p_hash,a_occ,p_total}；不唯一/定位不到 → {}。

    要求**全文唯一命中**（唯一段落 + 段内唯一位置），与 patcher 修复侧的唯一定位口径
    一致。宁可没有跳转链接，也不能给出指向错误位置的链接。
    """
    needle = norm_ws(matched_text)
    if len(needle) < 2:
        return {}          # 单字匹配无法稳定定位
    hits = []
    for it in addr_items:
        norm = norm_ws(it.get("text") or "")
        if not norm or needle not in norm:
            continue
        occ = _count_occurrences(norm, needle)
        if occ:
            hits.append((it, occ))
    if len(hits) != 1:
        return {}
    it, occ = hits[0]
    if occ != 1:
        return {}
    return {
        "p_idx": it["index"],
        "p_hash": para_hash32x2(norm_ws(it["text"])),
        "a_occ": occ,
        "p_total": len(addr_items),
    }


# ── LLM 输出解析 ─────────────────────────────────────────────────
def _json_candidates(raw: str) -> list:
    """可能的 JSON 片段，按「越可能整体成立」的顺序排列。

    顺序是硬要求：贪心 `{...}` 排在数组之前，会在裸数组 `[{...},{...}]` 里先吃掉第一个
    内层对象，得到 1 条而不是 N 条（或直接解析失败 → 0 条 → 伪造「审核通过」）。
    """
    cands = [raw]
    for rx in (_ARRAY_GREEDY, _ARRAY_LAZY, _OBJECT_GREEDY):
        m = rx.search(raw)
        if m:
            cands.append(m.group(0))
    return cands


def _items_from(raw, key: str):
    """从候选片段里取出目标数组；取不到返回 None（**不返回 []**）。"""
    if not isinstance(raw, str) or not raw.strip():
        return None
    for cand in _json_candidates(raw):
        try:
            val = json.loads(cand)
        except Exception:
            continue
        if isinstance(val, dict):
            val = val.get(key)
        if not isinstance(val, list):
            continue
        if not all(isinstance(x, dict) for x in val):
            # ["无问题"] / [1,2] 是散文噪声，不是「零标注」——折成 [] 会伪造「审核通过」
            continue
        return val
    return None


def _parse_annotation_items(raw):
    """审查轮输出 → 标注 dict 列表；无法解析返回 None（与「空数组」严格区分）。"""
    return _items_from(raw, "annotations")


def _parse_patch_items(raw):
    """修复轮输出 → 补丁 dict 列表；无法解析返回 None。"""
    return _items_from(raw, "patches")


# ── 标注落库 ─────────────────────────────────────────────────────
def _collect_annotations(parsed: list, tpl, addr_items: list) -> list:
    """把 LLM 的原始条目整理成可落库的形态。

    type 越出模板声明的 annotation_types 时**记录但不丢弃**：静默丢问题比多一个标签
    更糟；越界本身是「提示词需要修」的信号，故打 warning 供排查。
    """
    allowed = _template_types(tpl)
    out = []
    for item in parsed:
        matched = _clean_str(item.get("matched_text"), MATCHED_TEXT_MAX)
        issue = _clean_str(item.get("issue"), ISSUE_MAX)
        if not matched and not issue:
            continue          # 空壳：既无原文也无可读问题，落库只是噪声
        ann_type = _norm_token(item.get("type"), "other")
        if allowed and ann_type not in allowed:
            logger.warning("file review: annotation type %r outside template scope %r",
                           ann_type, allowed)
        out.append({
            "matched_text": matched,
            "type": ann_type,
            "severity": _norm_severity(item.get("severity")),
            "issue": issue,
            "suggestion": _clean_str(item.get("suggestion"), SUGGESTION_MAX),
            "anchor": json.dumps(_compute_anchor(addr_items, matched), ensure_ascii=False),
        })
    return out


def _persist_annotations(round_row, collected: list) -> dict:
    stats = {"total": 0, "high": 0, "medium": 0, "low": 0}
    for ann in collected:
        FileReviewAnnotationService.create(
            round_id=round_row.id, task_id=round_row.task_id, file_id=round_row.file_id,
            file_version=round_row.file_version, anchor=ann["anchor"],
            matched_text=ann["matched_text"], type=ann["type"], severity=ann["severity"],
            issue=ann["issue"], suggestion=ann["suggestion"], source="ai", status="open",
            tenant_id=round_row.tenant_id, created_by=round_row.created_by or "")
        stats["total"] += 1
        stats[ann["severity"]] += 1
    return stats


def _summary_from_stats(stats: dict) -> str:
    """摘要由**实际落库**的标注派生，不用 LLM 自述的 summary——预置模板输出的是裸数组，
    没有 summary 槽位；自述一旦与实际条数不符，面板就会自相矛盾。"""
    if not stats.get("total"):
        return "未发现问题"
    return f"共 {stats['total']} 个问题（高 {stats['high']} / 中 {stats['medium']} / 低 {stats['low']}）"


# ── 修复轮辅助 ───────────────────────────────────────────────────
def _build_fix_prompt(round_row, file_text: str, chosen: list) -> str:
    lines = []
    for i, a in enumerate(chosen, 1):
        lines.append(f"[{i}] 类型={a.type} 级别={a.severity} 原文：{a.matched_text or '（未给出原文）'}")
        lines.append(f"    问题：{a.issue}")
        if a.suggestion:
            lines.append(f"    建议：{a.suggestion}")
    return (
        "用户需求：" + (round_row.user_query or "（无）") + "\n\n"
        "待修复问题：\n" + "\n".join(lines) + "\n\n"
        "文档正文：\n" + file_text + "\n\n"
        "请为每条问题给出最小改动的替换方案，输出 JSON：\n"
        '{"patches": [{"idx": 1, "find": "原文片段", "replace": "新文本"}]}\n'
        "find 必须是文档中逐字出现且全文唯一的片段，否则该条会被跳过。"
    )


def _plan_patches(parsed: list, chosen: list) -> tuple:
    """补丁数组 + {补丁下标: 标注序号(1-based)} 映射。

    find/replace **原样透传**（哪怕是 None）：patcher 对 None / 非 str 的跳过规则已被
    对抗测试锁死，在这里预处理会把那些保护绕过去。
    """
    patches, by_pos, seen = [], {}, set()
    for item in parsed:
        try:
            idx = int(item.get("idx"))
        except (TypeError, ValueError):
            continue
        if idx in seen or not 1 <= idx <= len(chosen):
            continue
        seen.add(idx)
        patches.append({"find": item.get("find"), "replace": item.get("replace")})
        by_pos[len(patches) - 1] = idx
    return patches, by_pos


def _settle_annotations(chosen: list, by_pos: dict, applied: list) -> int:
    """把真正落地的补丁对应标注标 fixed，返回条数；未生效的保持 open（未修复好保持原样）。"""
    fixed = 0
    for pos, ok in enumerate(applied):
        if not ok:
            continue
        idx = by_pos.get(pos)
        if idx is None:
            continue
        if FileReviewAnnotationService.update_status(chosen[idx - 1].id, "fixed"):
            fixed += 1
    return fixed
```

---

- [ ] **Step 6: 跑 executor 测试**

```bash
uv run --no-sync pytest test/test_file_review_executor.py -v
```

Expected: 全绿。若出现 `OperationalError: Unknown column 'kb_ids'`，说明 `migrate_db()` 里的加列没跑到。手工补列**必须走生产同款 helper**：

```bash
uv run --no-sync python - <<'PY'
from peewee import TextField
from playhouse.migrate import MySQLMigrator
from api.db.db_models import DB, alter_db_add_column
DB.connect(reuse_if_open=True)
alter_db_add_column(MySQLMigrator(DB), "file_review_round", "kb_ids", TextField(null=True))
print("kb_ids" in DB.get_columns("file_review_round"))
PY
```

> 不要写 `MySQLMigrator(DB).add_column(...)`：那只是**构造**一个迁移操作，不 `migrate(...)` 就没有任何效果（却会静默打印成功）；而且 `add_column` 的签名是 `(table, column_name, field)`，漏掉 `column_name` 会直接 TypeError。上面这个 helper 内部包了 `migrate()` 并容忍 1060（列已存在）错误码。

---

- [ ] **Step 7: ruff + 全量回归**

```bash
uv run --no-sync ruff check rag/svr/file_review api/db/services/file_review_service.py api/db/db_models.py
uv run --no-sync ruff format rag/svr/file_review/executor.py test/test_file_review_executor.py
uv run --no-sync pytest test/test_file_review_executor.py test/test_file_review_service.py \
  test/test_file_review_spawn.py test/test_file_review_db.py test/test_file_review_patcher.py \
  test/test_file_review_kb_aggregator.py -q
```

Expected: ruff 无告警；测试全绿。

---

- [ ] **Step 8: 提交**

```bash
git add rag/svr/file_review/executor.py test/test_file_review_executor.py \
        api/db/services/file_review_service.py test/test_file_review_service.py \
        rag/svr/file_review/spawn.py test/test_file_review_spawn.py \
        api/db/db_models.py test/test_file_review_db.py \
        docs/superpowers/plans/2026-09-16-file-review-node.md
git commit -m "feat(file-review): executor 多轮状态机 + 标注锚点/修复落盘/幂等收口"
```

---

### 本任务的缺陷日志（原稿 → 修正，实现者须理解）

| # | 原稿问题 | 修正 |
|---|---|---|
| 1 | `execute_task(round_id)`，与 T5 的 task 级 CAS、T7/T8/T9 的调用口径不一致 | 入参改 `task_id`，内部取 `get_by_task()[-1]`；同步修正 T7/T8/T9 的调用 |
| 2 | `from common.llm_util import llm_complete`（模块不存在） | `LLMBundle` + `asyncio.run(mdl.async_chat(...))` |
| 3 | 自拼 `settings.retriever.retrieval(...)` 参数错位 | 复用 `template_fill.executor.retrieve_slot` |
| 4 | `anchor=json.dumps({"p_hash": 0})` 假锚点 | 服务端按 matched_text 反查 `{p_idx,p_hash,a_occ,p_total}`，不唯一则 `{}` |
| 5 | 从不调用 `apply_patches` —— 修复轮永远改不动文件 | `apply_patches_to_docx` → 存版本 blob → 标 `fixed` |
| 6 | 非 docx 走「纯文本降级」修复 | v1 只自动修 docx；其余收口 `done` + 手动修改提示（原件与标注都不动） |
| 7 | `_force_fail_round` 只 CAS `reviewing` | 扩到 `("reviewing","fixing")` |
| 8 | `kb_ids` 无处持久化（无列） | `kb_ids` 列 + `alter_db_add_column` + `create_round(kb_ids=...)` 归一 |
| 9 | 修复轮按上一轮 round_no 取 open 标注 → 第 3 轮空集空转 | 新增 task 级 `list_pending_by_task` |
| 10 | 同轮重跑会写两套标注 | 写前 `delete_by_round(round_id)` |
| 11 | 预置模板只喂首 500 字却要求「审视全文」；示例诱导 LLM 编 anchor；seed 短路使文案修订永远进不了库 | 改名 `{file_text}`、删示例 `anchor`、seed 改 insert-or-update（不动 `enabled`）、`_ensure` 每次同步 |
| 12 | 空正文喂 LLM = 请它编 | 正文不足 `MIN_FILE_TEXT_CHARS` 抛 `FileReviewError` |
| 13 | 静默截断而提示词说「全文」 | 截断时在正文里追加 `TRUNCATED_NOTE` |
| 14 | 收口写库失败被吞 → 轮次永久卡死 | 收口失败重新抛，交回 spawn 的独立 CAS |
| 15 | 贪心 object 排在数组前 → 条数失真 / 解析失败被当成「零问题」 | 候选顺序：整体 → 贪心数组 → 懒数组 → 贪心对象；数组元素必须全为 dict |

### 留给后续任务的口径（T7/T8/T9 必须照此调用）

- `spawn_review_task(task_id)`：入参是 **task_id**，不是 round_id。
- 三个入口建轮次时必须传 `kb_ids=`（节点来自 DSL，工具来自 LLM 参数，API 来自请求体），否则修复轮与重试检索不到同一批 KB。
- `fixing` 轮次由入口写好后 spawn；`execute_task` 只认轮次行的现状，不接收「这次是审还是修」的参数。
- 修复轮只处理 `status ∈ {open, new}` 的标注；要让某项不修，入口必须显式把它的 status 写成 `wontfix`——`levels` 过滤若只塞进 `user_query` 文本里，executor 是不会去解析的。
---

## Task 7: 画布节点 FileReview

**Files:**
- Create: `agent/component/file_review.py`
- Test: `test/test_file_review_node.py`

**形态：fire-and-forget——只建轮次行 + spawn，随即返回；不观察、不推 SSE。**
与 TemplateFill 的差异是有意的：执行的唯一真相是轮次行（`file_review_round`），
进度由 T9 的 REST 端点按 `file_id` / `task_id` 反查，三个入口（本节点 / T8 工具 /
T9 API）共用同一份真相。在节点内推流会把「执行」绑死在某个具体连接上：刷新即丢，
且第三个入口无连接可推（T6 的模块边界同款理由）。

**两条已实测的硬约束（写错不会报错，只会静默审错文件 / 互相覆盖）：**

1. **`file_id` = `/documents/upload` 返回的 `id`**（MinIO 对象名，桶
   `{tenant_id}-downloads`；executor 的 `_load_original_blob` 第 4 级兜底正好查这个桶，
   无需为取字节改任何后端代码）。但**画布会丢弃上传对象的 id**：
   `canvas.run(files=...)` 只把解析文本放进 `sys.files` / `sys.file_content`
   （`agent/canvas.py:419-433` 显式丢掉 id），节点拿不到。传 id 的唯一通道是
   `canvas.run(inputs={"review_file_id": {"value": <upload uuid>}})` →
   `Begin._invoke` 把**非 file 类型**的 input 值直接 `set_output(k, v)`
   （`agent/component/begin.py`，file 类型走 `FileService.get_files` 换成文本）。
   Begin 组件的 **id 恒为字面量 `"begin"`**（`agent/canvas.py:49/75/967` 硬编码，
   前端 `BeginId = 'begin'` 见 `web/src/constants/agent.tsx:255`），
   故 DSL 引用写作 **`{begin@review_file_id}`**，不是 `begin_0`。
   本节点不假设该引用一定写对：参数解析落空时回退「按 component_name 扫 Begin 输出」。
2. **`task_id` 必须每次调用新生成**。不得复用 `self._canvas.task_id`——它即 agent_id、
   跨运行不变（TemplateFill 同注释），复用它会让不同文件的审核串成一条任务链：
   第 2 个文件的第 1 轮按 `max_completed_round_no` 继承旧文件的轮次号，且
   `_store_version_blob` 的对象名 `frv-{task_id}-{file_version}` 会互相覆盖。

**默认模板要落具体 id，不能落空串**：轮次行是「这一轮用了哪套模板」的审计记录，
写空串等于对「用的是默认模板」这件事撒谎；T9 的修复轮又按 `cur.template_id` 继承，
空串会一路传下去。默认值的唯一真相是 `executor.DEFAULT_TEMPLATE_ID`，本节点在
`_invoke` 内**延迟 import** 取用（顶层 import 会把 `python-docx` 等重依赖带进
画布启动路径——`agent.component` 包扫描时会 import 本模块；`spawn.py` 同款取舍）。
T9 建轮次时应照此办理，不要再复制 `'bid_doc_format'` 字面量。

**参数名取 `dataset_ids`（覆盖设计稿 §2.1 写的 `kb_ids`）**：画布知识库选择器的表单
字段名恒为 `dataset_ids`（`web/src/components/knowledge-base-item.tsx:72` 默认 name；
`web/src/pages/agent/form/template-fill-form/index.tsx:18` 同）。DB 列与 Service/executor
仍是 `kb_ids`，只有**节点参数**跟画布约定，否则 T15 的节点配置表单挂不上 KB 选择器。

**类名 `FileReview` ≠ 工具类名 `FileReviewTool`**：`component_class` 按类名解析且
`agent.component` 优先于 `agent.tools`（`agent/component/__init__.py:51-58`），
两包同名类会互相遮蔽。TemplateFill 的 docstring 记录了同一条教训，T8 必须配合。

- [ ] **Step 1: 写测试（先红）**

创建 `test/test_file_review_node.py`：

```python
"""「文件审核」画布节点单测。对抗性覆盖：
- file_id 三条解析路径：参数写死 uuid / 参数写引用 {begin@review_file_id} / 参数留空扫 Begin 输出
- 解析落空 → 抛错（不许静默建一条 file_id 为空的轮次行）；非引用垃圾串**原样透传**
  （不吞不改——错误由 executor 的「文件不存在」响亮暴露，节点不做创造性猜测）
- 租户缺失 → 抛错（不许建 tenant_id 为空的轮次行）
- 建轮次契约：round_no=1 / status=reviewing / file_version=v1 / created_by=tenant_id /
  kb_ids 原样透传（形态归一是 Service 层 _normalize_kb_ids 的职责，节点不二次解析）/
  user_query 来自 custom_prompt
- spawn 收到的是 **task_id** 而不是 round id（T5 契约）
- 两次调用产出两个不同 task_id，且都不等于画布 task_id（复用会让不同文件互相覆盖）
所有外部依赖（Service / spawn / Begin 组件）经替身注入，不触真实 DB / 线程 / LLM。
（共 18 例：上列之外另有 4 例由审查补强——下划线 id 原样透传 / create_round 抛错不 spawn /
Begin 读输出抛错降级 / Begin 输出非 str 视为取不到。）
"""
import pytest

from agent.component import file_review as fr
from agent.component.file_review import FileReviewParam

# ---------- 桩件 ----------


class _Begin:
    component_name = "Begin"

    def __init__(self, outs):
        self._outs = outs

    def output(self):
        return dict(self._outs)


class FakeCanvas:
    def __init__(self, tenant="t1", begin_outs=None, refs=None):
        self._tenant = tenant
        self._refs = refs or {}
        self.task_id = "canvas-task-id"     # 画布运行 id：跨运行不变，节点不得复用
        self.components = {"begin": {"obj": _Begin(begin_outs or {})}}

    def get_tenant_id(self):
        return self._tenant

    def get_variable_value(self, exp):
        return self._refs.get(exp)

    def get_component_name(self, cpn_id):
        return "begin"


def _make(param=None, canvas=None):
    """绕过 __init__ 直接装桩（与本仓 test_agent_fill_template_component.py 同款）。"""
    cpn = fr.FileReview.__new__(fr.FileReview)
    cpn._id = "review_0"
    cpn._param = param or FileReviewParam()
    cpn._param.check()
    cpn._canvas = canvas or FakeCanvas()
    return cpn


@pytest.fixture
def rec(monkeypatch):
    """记录 create_round / spawn_review_task 的调用，并返回可变 round id。"""
    calls = {"rounds": [], "spawned": [], "round_id": "round-1"}

    def _create_round(**kwargs):
        calls["rounds"].append(kwargs)
        return calls["round_id"]

    monkeypatch.setattr(fr.FileReviewRoundService, "create_round", _create_round)
    monkeypatch.setattr(fr.spawn_mod, "spawn_review_task",
                        lambda tid: calls["spawned"].append(tid))
    return calls


# ---------- file_id 解析 ----------

def test_file_id_literal_param(rec):
    p = FileReviewParam()
    p.file_id = "upload-uuid-1"
    cpn = _make(p)
    cpn._invoke()
    assert rec["rounds"][0]["file_id"] == "upload-uuid-1"


def test_file_id_from_reference(rec):
    p = FileReviewParam()
    p.file_id = "{begin@review_file_id}"
    cpn = _make(p, FakeCanvas(begin_outs={"review_file_id": "ignored"},
                              refs={"begin@review_file_id": "upload-uuid-2"}))
    cpn._invoke()
    assert rec["rounds"][0]["file_id"] == "upload-uuid-2"


def test_file_id_falls_back_to_begin_output(rec):
    """参数留空（B端用户不填）时，仍应取到前端送入 Begin 的上传 id。"""
    cpn = _make(canvas=FakeCanvas(begin_outs={"review_file_id": "upload-uuid-3"}))
    cpn._invoke()
    assert rec["rounds"][0]["file_id"] == "upload-uuid-3"


def test_file_id_ref_unresolved_falls_back(rec):
    """上游没推（组件在、但该输出为空）：展开为空 → 回退 Begin 输出，
    不得把 '{begin@...}' 当 id。"""
    # 注意本用例只覆盖「上游没推」这一种；「引用写错」是另外的结局（拼错的 id 含下划线时
    # 不匹配引用正则 → 字面串原样透传、不触发回退），见 test_file_id_underscore_ref_stays_literal。
    p = FileReviewParam()
    p.file_id = "{begin@review_file_id}"
    cpn = _make(p, FakeCanvas(begin_outs={"review_file_id": "upload-uuid-4"}, refs={}))
    cpn._invoke()
    assert rec["rounds"][0]["file_id"] == "upload-uuid-4"


def test_file_id_underscore_ref_stays_literal(rec):
    """不匹配引用正则的 {cpn_x@var} 形态不进展开，原样落库——
    错误由 executor 的「文件不存在」暴露，节点不做创造性猜测。"""
    p = FileReviewParam()
    p.file_id = "{begin_x@review_file_id}"
    _make(p)._invoke()
    assert rec["rounds"][0]["file_id"] == "{begin_x@review_file_id}"


def test_begin_output_raising_falls_back_to_error(rec):
    """Begin 读输出出问题不应逸出异常，应降级成同一条用户可读报错。"""
    class _BadBegin:
        component_name = "Begin"

        def output(self):
            raise RuntimeError("boom")

    cpn = _make(canvas=FakeCanvas(begin_outs={}))
    cpn._canvas.components = {"begin": {"obj": _BadBegin()}}
    with pytest.raises(ValueError, match="未指定待审核文件"):
        cpn._invoke()
    assert rec["rounds"] == []


def test_begin_output_non_str_ignored(rec):
    """Begin 输出不是 str（此处为 int）时按取不到处理，不得把 123 当 file_id。"""
    cpn = _make(canvas=FakeCanvas(begin_outs={"review_file_id": 123}))
    with pytest.raises(ValueError, match="未指定待审核文件"):
        cpn._invoke()
    assert rec["rounds"] == []


def test_file_id_missing_raises(rec):
    with pytest.raises(ValueError, match="未指定待审核文件"):
        _make()._invoke()
    assert rec["rounds"] == []
    assert rec["spawned"] == []


def test_tenant_missing_raises(rec):
    cpn = _make(canvas=FakeCanvas(tenant="", begin_outs={"review_file_id": "u"}))
    with pytest.raises(ValueError, match="租户"):
        cpn._invoke()
    assert rec["rounds"] == []


def test_non_ref_garbage_passed_through(rec):
    """非引用形态的串不做任何猜测，原样落库——由 executor 报「文件不存在」。"""
    p = FileReviewParam()
    p.file_id = "not-a-uuid"
    _make(p)._invoke()
    assert rec["rounds"][0]["file_id"] == "not-a-uuid"


# ---------- 建轮次契约 ----------

def test_create_round_contract(rec):
    p = FileReviewParam()
    p.file_id = "u1"
    p.template_id = ""
    p.custom_prompt = "重点看资质"
    p.dataset_ids = ["kb1", "kb2"]
    cpn = _make(p)
    cpn._invoke()
    kw = rec["rounds"][0]
    assert kw["round_no"] == 1
    assert kw["status"] == "reviewing"
    assert kw["file_version"] == "v1"
    # 留空 → 落**具体**默认模板 id，而不是空串：轮次行是「用了哪套模板」的审计记录，
    # 写空等于对「默认」这件事撒谎；且 T9 的修复轮按 cur.template_id 继承。
    assert kw["template_id"] == "bid_doc_format"
    assert kw["user_query"] == "重点看资质"
    assert kw["tenant_id"] == "t1"
    assert kw["created_by"] == "t1"
    assert kw["kb_ids"] == ["kb1", "kb2"]


def test_template_id_override(rec):
    p = FileReviewParam()
    p.file_id = "u1"
    p.template_id = "bid_qualification"
    _make(p)._invoke()
    assert rec["rounds"][0]["template_id"] == "bid_qualification"


# ---------- spawn 契约 ----------

def test_spawn_receives_task_id_not_round_id(rec):
    rec["round_id"] = "round-xyz"
    p = FileReviewParam()
    p.file_id = "u1"
    _make(p)._invoke()
    assert rec["spawned"] == [rec["rounds"][0]["task_id"]]
    assert "round-xyz" not in rec["spawned"]


def test_no_spawn_when_create_round_fails(rec, monkeypatch):
    """建轮次失败必须不 spawn——否则起一个永远读不到轮次行的线程。"""
    def _boom(**kwargs):
        raise RuntimeError("db down")

    monkeypatch.setattr(fr.FileReviewRoundService, "create_round", _boom)
    p = FileReviewParam()
    p.file_id = "u1"
    with pytest.raises(RuntimeError, match="db down"):
        _make(p)._invoke()
    assert rec["spawned"] == []


def test_task_id_fresh_per_invoke(rec):
    """两次运行必须是两个 task：复用画布 task_id 会让不同文件的多轮审核串链。"""
    cpn = _make(canvas=FakeCanvas(begin_outs={"review_file_id": "u1"}))
    cpn._invoke()
    cpn._invoke()
    t1, t2 = (r["task_id"] for r in rec["rounds"])
    assert t1 != t2
    assert "canvas-task-id" not in (t1, t2)


# ---------- 输出与元数据 ----------

def test_outputs_after_invoke(rec):
    rec["round_id"] = "round-9"
    p = FileReviewParam()
    p.file_id = "u1"
    cpn = _make(p)
    cpn._invoke()
    assert cpn.output("task_id") == rec["rounds"][0]["task_id"]
    assert cpn.output("round_id") == "round-9"
    assert cpn.output("content")


def test_param_outputs_declared():
    """输出的键必须在 param 里声明，否则画布序列化 DSL 时下游引用不到。"""
    assert set(FileReviewParam().outputs) == {"task_id", "round_id", "content"}


def test_check_always_true():
    """canvas.load() 会调 param.check() 并把异常包装成节点级报错——
    本节点是运行期解析 file_id，配置期不该拦人。"""
    assert FileReviewParam().check() is True
```

- [ ] **Step 2: 跑测试确认失败**

```bash
uv run --no-sync pytest test/test_file_review_node.py -v
```
Expected: FAIL（`ModuleNotFoundError: No module named 'agent.component.file_review'`）

- [ ] **Step 3: 创建 `agent/component/file_review.py`**

```python
# agent/component/file_review.py
"""「文件审核」画布节点：fire-and-forget（只建轮次行 + 起后台线程，随即返回）。

为什么与 TemplateFill 形态不同（不观察、不推 SSE）：执行的唯一真相是轮次行
（file_review_round），进度由 T9 的 REST 端点按 file_id / task_id 反查——三个入口
（本节点 / T8 对话工具 / T9 API）共用同一份真相。在节点内推流会把「执行」绑死在
某个具体连接上（刷新即丢），且第三个入口无连接可推。

file_id 是 /documents/upload 返回的 id（MinIO 对象名，桶 {tenant_id}-downloads）。
画布会丢弃上传对象的 id（canvas.run 只把解析文本放进 sys.files / sys.file_content），
故由前端以 canvas.run(inputs={"review_file_id": {"value": <uuid>}}) 送入 Begin，
节点按 {begin@review_file_id} 引用展开或回退扫 Begin 输出（详见 _resolve_file_id）。

类名刻意取 FileReview（工具侧是 FileReviewTool）：component_class 按类名解析且
agent.component 优先于 agent.tools，两包同名类会互相遮蔽——TemplateFill 的 docstring
记录了同一条教训，两包不得重名。
"""
import json
import logging
import re
from functools import partial

from agent.component.base import ComponentBase, ComponentParamBase
from api.db.services.file_review_service import FileReviewRoundService
from common.misc_utils import get_uuid
from rag.svr.file_review import spawn as spawn_mod

logger = logging.getLogger(__name__)

# Begin 承载「本次审核哪个上传文件」的输出键名。前端在 canvas.run(inputs=...) 里用这个
# 键名送入上传 id，三处（对话页 / 流程页 / 本节点）必须一致。
FILE_ID_INPUT_KEY = "review_file_id"

# 第 1 轮的文件版本号。后续修复轮的 v2/v3 由 T9 的 fix 端点写入——executor 只按
# 「同 task 已完成轮次」推输入版本，不认识版本号语义。
_FIRST_VERSION = "v1"


class FileReviewParam(ComponentParamBase):
    """file_id 有三种给法：参数写死 uuid / 参数写变量引用 / 留空读 Begin 输出。"""

    def __init__(self):
        super().__init__()
        self.file_id = ""         # upload uuid，或 {begin@review_file_id} 形态的引用
        self.template_id = ""     # 留空 → 建轮次时落 executor.DEFAULT_TEMPLATE_ID
        self.custom_prompt = ""   # 用户自定义审核要求，落进轮次行 user_query
        self.dataset_ids = []     # 检索知识库（画布 KB 表单字段名，同 TemplateFill）
        self.max_rounds = 3       # 前端节点面板用；后端不消费——轮次上限由 T9 的 fix
                                  # 端点按 max_completed_round_no 判定，无对应 DB 列
        self.outputs = {
            "task_id": {"value": "", "type": "string"},
            "round_id": {"value": "", "type": "string"},
            "content": {"value": "", "type": "string"},
        }

    def check(self) -> bool:
        # canvas.load() 会调 check() 并把异常包装成节点级报错。file_id 是**运行期**才
        # 能解析出来的（引用要等 Begin 输出），配置期一律放行，让 _invoke 报具体原因。
        return True


class FileReview(ComponentBase):
    component_name = "FileReview"

    def _expand_refs(self, text: str) -> str:
        """变量引用展开：TemplateFill._resolve_query 同款四态（partial/list/str/JSON）。

        复用同一套写法而不是另写解析：引用值的形态由上游组件决定（流式输出是 partial、
        检索结果是 list），任一处漏判都会让引用原样留在文本里，变成一个看似合法的假
        file_id，最后变成一句「文件不存在」的费解报错。
        """
        for k, v in self.get_input_elements_from_text(text).items():
            val = v.get("value")
            if isinstance(val, partial):
                ans = "".join(str(chunk) for chunk in val())
            elif isinstance(val, list):
                ans = ",".join(str(item) for item in val)
            elif val is None or isinstance(val, str):
                ans = val or ""
            else:
                try:
                    ans = json.dumps(val, ensure_ascii=False)
                except Exception:  # noqa: BLE001 — 不可序列化值降级 str
                    ans = str(val)
            # 必须用 callable 作替换值，不能把 ans 直接当替换串：replacement template
            # 会把 ans 里的 \ 和 \g<n> 解释成转义 / 反向引用（"C:\Users\x.docx" 抛
            # bad escape \U 且异常逸出连轮次行都建不起来，"a\nb" 被静默改写成真换行）。
            # _ans=ans 把本轮值绑成默认参数（过 ruff B023「循环变量未绑定」）。
            # ⚠ 照抄本段时保留 _ans=ans 写法——TemplateFill._resolve_query 的裸 ans 版本
            # 有同款缺陷，别抄回去。
            text = re.sub(r"\{" + re.escape(k) + r"\}", lambda _m, _ans=ans: _ans, text)
        return text.strip()

    def _begin_output(self, key: str) -> str:
        """按 component_name 扫 Begin 输出取键值（TemplateFill._begin_fields 同款）。

        不按 id 取：Begin 的 id 虽是硬编码的 "begin"，但节点参数可能被写成别的组件的
        引用、或干脆留空，而「本次传入的上传文件」只可能来自 Begin 输出——扫一遍比
        断言 id 更耐用。取不到（含异常）都返回空串，由调用方给出用户可读的报错。
        """
        try:
            for cpn in (self._canvas.components or {}).values():
                obj = cpn.get("obj") if isinstance(cpn, dict) else None
                if obj is not None and getattr(obj, "component_name", "").lower() == "begin":
                    val = (obj.output() or {}).get(key)
                    return val if isinstance(val, str) else ""
        except Exception:  # noqa: BLE001 — 取 Begin 输出失败不该拖垮整轮审核
            logger.warning("FileReview._begin_output failed", exc_info=True)
        return ""

    def _resolve_file_id(self) -> str:
        """待审核文件 id（= /documents/upload 返回的 uuid）。"""
        text = self._expand_refs(self._param.file_id or "")
        return text or self._begin_output(FILE_ID_INPUT_KEY)

    def _invoke(self, **kwargs):
        tenant_id = self._canvas.get_tenant_id() if self._canvas else ""
        if not tenant_id:
            raise ValueError("无法确定画布租户")
        file_id = self._resolve_file_id()
        if not file_id:
            raise ValueError(
                "未指定待审核文件：请在节点配置里填写 file_id，"
                "或由前端以 inputs={'review_file_id': {'value': <上传文件 id>}} 传入")
        # 每次调用新生成 task_id：画布 task_id 跨运行不变（即 agent_id），复用它会让不同
        # 文件的审核进同一条任务链（轮次号继承 + frv-{task_id}-{file_version} 对象名互覆盖）。
        task_id = get_uuid()
        # 默认模板 id 的唯一真相在 executor，此处延迟 import 取用（不在本模块复制字面量）；
        # 延迟而非顶层 import，是为了不让画布启动路径（agent.component 包扫描会 import
        # 本模块）连带拉起 python-docx / settings 等重依赖——spawn.py 同款取舍。
        from rag.svr.file_review.executor import DEFAULT_TEMPLATE_ID
        round_id = FileReviewRoundService.create_round(
            task_id=task_id, file_id=file_id, round_no=1,
            template_id=self._param.template_id or DEFAULT_TEMPLATE_ID,
            user_query=self._param.custom_prompt or "",
            file_version=_FIRST_VERSION, status="reviewing",
            tenant_id=tenant_id, created_by=tenant_id,
            # 形态（list / JSON 文本 / 裸 id）归一是 Service 层 _normalize_kb_ids 的职责，
            # 节点原样透传，不在两处各写一遍口径。
            kb_ids=self._param.dataset_ids,
        )
        spawn_mod.spawn_review_task(task_id)
        self.set_output("task_id", task_id)
        self.set_output("round_id", round_id)
        self.set_output("content", "已开始审核，批注结果将显示在「文件审核」面板中。")
```

- [ ] **Step 4: 跑测试确认通过**

```bash
uv run --no-sync pytest test/test_file_review_node.py -v
```
Expected: PASS（18 例全绿）

- [ ] **Step 5: 冒烟——画布包整体 import（本模块在 `agent.component` 包扫描时被加载）**

```bash
uv run --no-sync python -c "
from agent.component import component_class
from agent.component.file_review import FileReviewParam
print(component_class('FileReview').component_name, sorted(FileReviewParam().outputs))
"
```
Expected: `FileReview ['content', 'round_id', 'task_id']`
（若报 `ImportError`/`AssertionError`，是本模块顶层 import 引入了画布启动路径上的
重依赖或循环——顶层只允许 import `spawn` 与 Service，**不得**顶层 import
`rag.svr.file_review.executor`（含 `python-docx`），那是 `_invoke` 内按需拉起的。）

- [ ] **Step 6: 提交**

```bash
git add agent/component/file_review.py test/test_file_review_node.py
git commit -m "feat(file-review): canvas node FileReview + 单测"
```

**实测落地**：`a9eb846d`（实现，14 例）→ 审查后 `0dff040f`（修复：`_expand_refs` 的 `re.sub`
替换值由裸 `ans` 改 callable，杜绝 `\` / `\g<n>` 被当转义或反向引用解释而抛 `bad escape \U`
或静默改写 file_id；测试补到 18 例）。两道审查均已通过。
**同批发现的既有问题（本任务未修，越界）**：`agent/component/template_fill.py:242` 的
`_resolve_query` 有同款 replacement-template 漏洞。

---

## Task 8: 对话工具 FileReviewTool

**Files:**
- Create: `agent/tools/file_review.py`
- Create: `test/test_file_review_tool.py`
- Modify: `api/db/services/file_review_service.py`（**追加**三个助手，不改既有方法）
- Modify: `test/test_file_review_service.py`（**追加**助手用例）

> **本段是 T7 交付后按真实 API 重写的一版。** 前版有 5 处硬伤（下），照抄会直接坏：
> ① 用了 `name=` / `description=` / `_run()` —— 那不是 ToolBase 的表面。真实表面是
> `component_name` + `FileReviewToolParam.meta` + `_invoke(**kwargs)`，照
> `agent/tools/template_fill.py` 抄（那边已有两道审查通过的同款结构）。
> ② `spawn_review_task(rid)` 传了**轮次 id** —— T5 契约是 **task_id**，传轮次 id 会让
> 执行器读不到轮次行（`get_by_task(round_id)` 返回空）而静默不动。
> ③ `tenant_id=''` —— 轮次行会永久丢失归属，而 T9/T8 的越权闸门（见 Step 1 的
> `get_owned_task`）正是按轮次 tenant 判归属：写空串 = 工具把自己人挡在门外。
> ④ 缺 `kb_ids` —— 轮次行是「本轮用了哪些知识库」的审计记录，修复轮要继承它。
> ⑤ 硬编码 `'bid_doc_format'` —— 默认模板 id 的唯一真相是
> `executor.DEFAULT_TEMPLATE_ID`（T7 节点已按此办理）。
>
> **边界（严格执行）：**只允许新建 `agent/tools/file_review.py` 与
> `test/test_file_review_tool.py`，以及**追加**到上述两个既有文件。**禁止**修改
> `agent/component/file_review.py`（T7）、`rag/svr/file_review/*`（T5/T6）、任何
> `template_fill*` 文件。

**为什么 `file_id` 不做成 LLM 参数：**待审核文件是「本次运行环境注入的」，不是用户说出来的。
把它暴露成参数，LLM 就会在缺上下文时**编造一个 uuid**，而编造值会一路落进轮次行与 MinIO
对象名，最终以一句「文件不存在」暴露。故本工具与 T7 节点同款：扫 Begin 输出取
`FILE_ID_INPUT_KEY`（键名从 T7 模块 import，单一真相）。

- [ ] **Step 1: 追加 Service 层三个助手（先写失败测试）**

在 `test/test_file_review_service.py` 顶部的 import 区加一行：

```python
from types import SimpleNamespace
```

在该文件**末尾**追加：

```python
# ── T8 助手：越权闸门 / 轮次编号 / 修复轮余额 ─────────────────────────


def _r(no):
    """只带 round_no 的轮次替身：fix_rounds_left 是纯函数，不需要真轮次行。"""
    return SimpleNamespace(round_no=no)


def test_get_owned_task_short_circuits_on_empty_args():
    assert FileReviewRoundService.get_owned_task("", "t1") == []
    assert FileReviewRoundService.get_owned_task(None, "t1") == []
    assert FileReviewRoundService.get_owned_task(PFX + "own-x", "") == []


def test_get_owned_task_denies_unknown_task():
    assert FileReviewRoundService.get_owned_task(PFX + "no-such-task", "t1") == []


def test_get_owned_task_passes_only_when_all_rounds_owned():
    tid = PFX + "own-a"
    _mk_round(tid, 1, "annotated", tenant_id="t1")
    assert len(FileReviewRoundService.get_owned_task(tid, "t1")) == 1
    # 他人租户：拒绝
    assert FileReviewRoundService.get_owned_task(tid, "t2") == []


def test_get_owned_task_denies_when_any_round_has_empty_tenant():
    """同一 task 只要**有一条**轮次 tenant 为空/他人，整体拒绝（保守口径）：
    宁可让脏数据的人自己重新发起，也不能把「是不是他的」判成「大概是」。"""
    tid = PFX + "own-b"
    _mk_round(tid, 1, "annotated", tenant_id="t1")
    _mk_round(tid, 2, "done", tenant_id="")
    assert FileReviewRoundService.get_owned_task(tid, "t1") == []


def test_next_round_counts_all_rounds_including_failed():
    tid = PFX + "next-a"
    assert FileReviewRoundService.next_round(tid) == (1, "v1")
    _mk_round(tid, 1, "annotated")
    assert FileReviewRoundService.next_round(tid) == (2, "v2")
    # failed 轮也顶号：只看 done/annotated 会让 v2 这个名字被复用，
    # 覆盖失败轮已落盘的成稿（T6 交接契约第 2 条），并把同号失败兄弟选成输入基线。
    _mk_round(tid, 2, "failed")
    assert FileReviewRoundService.next_round(tid) == (3, "v3")


def test_fix_rounds_left_counts_failed_and_clamps():
    from api.db.services.file_review_service import MAX_FIX_ROUNDS, fix_rounds_left

    assert MAX_FIX_ROUNDS == 3
    assert fix_rounds_left([]) == MAX_FIX_ROUNDS
    assert fix_rounds_left([_r(1)]) == MAX_FIX_ROUNDS          # 首轮审核不算修复轮
    assert fix_rounds_left([_r(1), _r(2)]) == MAX_FIX_ROUNDS - 1
    assert fix_rounds_left([_r(1), _r(2), _r(3)]) == MAX_FIX_ROUNDS - 2
    assert fix_rounds_left([_r(1), _r(2), _r(3), _r(4)]) == 0  # 负数钳到 0
    assert fix_rounds_left([_r(0), _r(1)]) == MAX_FIX_ROUNDS    # round_no=0 的脏行不算修复轮
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run --no-sync pytest test/test_file_review_service.py -k "owned_task or next_round or fix_rounds_left" -v`
Expected: FAIL —— `AttributeError: type object 'FileReviewRoundService' has no attribute 'get_owned_task'`

- [ ] **Step 3: 在 `api/db/services/file_review_service.py` 追加实现**

在 `PENDING_ANNOTATION_STATUSES` 定义之后追加常量：

```python
# 「最多三轮修复」的权威口径。DB 无该列，由本层判定。T7 节点参数面板里的 max_rounds
# 只是给用户看的 UI 字段（见 agent/component/file_review.py 的注释「后端不消费」），
# 后端唯一的轮数上限在这里。
MAX_FIX_ROUNDS = 3


def fix_rounds_left(rounds: list) -> int:
    """还剩几轮修复机会 = MAX_FIX_ROUNDS - 已发生的修复轮数（下限 0）。

    修复轮 = 该 task 中 round_no > 1 的轮次（第 1 轮按契约恒为 review）。
    **失败轮也计入**：用户烧掉的是一次尝试机会，不是「什么都没发生」；不计入会让失败的
    retry 次数无上限，与「最大三轮重试」的口径相悖。
    round_no 为 0/None 的脏行不算修复轮（不短路会让一条脏行白吃一次机会）。
    """
    used = sum(1 for r in rounds if (r.round_no or 0) > 1)
    return max(0, MAX_FIX_ROUNDS - used)
```

在 `FileReviewRoundService` 内、`max_completed_round_no` 之前插入两个方法：

```python
    @classmethod
    @DB.connection_context()
    def get_owned_task(cls, task_id: str, tenant_id: str) -> list:
        """越权闸门：返回该 task 的轮次列表，**仅当轮次全部归属 tenant_id**；否则 []。

        [] 同时覆盖四种「不许继续」的情形，调用方一律按「空 = 拒绝」处理、**不区分**：
        task_id 为空 / 无轮次行 / 轮次归他人 / 轮次 tenant 为空（历史脏行）。区分会把
        「他人的 task 是否存在」这一信息泄露给攻击者。

        为什么必须有这一层：`execute_task(task_id)` / `spawn_review_task(task_id)` /
        `_force_fail_round(task_id)` 全链路只按 task_id 圈定、**不含任何 tenant 谓词**
        （round_row.tenant_id 仅用于选 bucket）。入口不做归属校验 = 任何人拿到 task_id
        就能触发、读取、收口他人的审核（T6 → T9 交接契约第 5 条，T8/T9 共用本闸门）。

        刻意**不做** task_id 的格式白名单（如「必须 32 位十六进制」）：格式不匹配时本
        就查不到行、结果同为 []，白名单不增加任何安全边界；反过来，一旦 id 生成方式
        （现在是 uuid1().hex）变动而白名单没同步，闸门会静默拒绝**所有**合法请求 ——
        用一个更隐蔽的故障换一个不存在的收益。
        """
        if not task_id or not tenant_id:
            return []
        rounds = cls.get_by_task(task_id)
        if not rounds:
            return []
        if any((r.tenant_id or "") != tenant_id for r in rounds):
            return []
        return rounds

    @classmethod
    @DB.connection_context()
    def next_round(cls, task_id: str) -> tuple[int, str]:
        """下一轮的 (round_no, file_version)：max(**全部**轮次 round_no) + 1。

        刻意**不**复用 max_completed_round_no（只数 done/annotated）—— 那个口径会让
        **失败轮的编号被复用**，后果有三（T6 审查已实测确认）：
          ① 产物对象名是 frv-{task_id}-{file_version}，同号即同名：新轮会覆盖失败轮
             **已经落盘**的成稿（交接契约第 2 条：failed 轮次可能带 minio_path）；
          ② _latest_version_name 按 round_no 取基线、同号后者胜，新轮会把同号的失败
             兄弟（或它自己）选成输入基线 —— 自引用，补丁 find 全数落空；
          ③ 面板上两条同号轮次，用户分不清哪条是哪次。
        rounds 为空时返回 (1, "v1")，与 T7 节点首轮口径一致（首轮即原件）。
        """
        rounds = cls.get_by_task(task_id)
        no = max((r.round_no or 0) for r in rounds) + 1 if rounds else 1
        return no, "v%d" % no
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run --no-sync pytest test/test_file_review_service.py -v`
Expected: PASS（既有用例 + 新增 6 例全绿；本文件连真实 MySQL，需本机 DB 起着）

- [ ] **Step 5: 写工具失败测试**

创建 `test/test_file_review_tool.py`：

```python
"""FileReviewTool（agent/tools/file_review.py）单测。

Service / spawn / time.sleep 全部 monkeypatch，不触 DB/LLM/MinIO；工具实例用
object.__new__ 绕过 ToolBase.__init__（其要求真实 Canvas 实例），_param 用
SimpleNamespace 打桩（同 test_template_fill_tool.py）。

对抗性覆盖：
- file_id 三条路：Begin 输出拿到 / 拿不到 → 明确拒绝（不建 file_id 为空的轮次行）/
  Begin 组件缺失或 output() 抛错或返回非 str → 降级成同一条用户可读拒绝；
- 租户缺失 → 一律拒绝（不建 tenant 为空的轮次行，否则越权闸门先把自己挡住）；
- 模板 id：编造的 id 必须被拒（不静默回落默认值 —— 否则审计列里留下假模板记录）；
- spawn 收到的是 **task_id** 而非 round_id（T5 契约）；
- fix 的级别过滤：**不得**改写任何标注的状态（未选中级别不许被置 wontfix）；
- 级别为空 / 未知词 / 该级别无待修项 → 拒绝新建轮次；
- 轮次余额用尽 / 上一轮仍在跑 → 拒绝；
- 越权：get_owned_task 返回 [] 时 status/fix 必须拒绝；
- 轮询超时 → 返回 task_id 并引导 action=status。
"""
from types import SimpleNamespace

PFX = "__test_fr_tool__"


def _svc():
    from api.db.services import file_review_service as svc

    return svc


def _tpl(tid="bid_doc_format", name="招标文件格式审核", desc="看格式与要件"):
    return SimpleNamespace(id=tid, name=name, description=desc)


def _round(no=1, status="annotated", **kw):
    base = dict(task_id=PFX + "task", file_id=PFX + "file", round_no=no,
                status=status, template_id="bid_doc_format", user_query="",
                file_version="v%d" % no, kb_ids=None, minio_path=None,
                summary="", error=None)
    base.update(kw)
    return SimpleNamespace(**base)


def _ann(severity="high", status="open", issue="资质缺失", matched_text="甲公司"):
    return SimpleNamespace(id="ann-1", severity=severity, status=status,
                           issue=issue, matched_text=matched_text)


class _Begin:
    component_name = "Begin"

    def __init__(self, outs):
        self._outs = outs

    def output(self):
        return dict(self._outs)


class _BadBegin:
    component_name = "Begin"

    def output(self):
        raise RuntimeError("boom")


def _begin(outs):
    return {"begin": {"obj": _Begin(outs)}}


def _patch(monkeypatch, *, templates=None, rounds=None, pending=None,
           next_round=(1, "v1"), left=3):
    """统一打桩：Service 查询 + spawn + sleep。返回记录写操作的 calls。"""
    svc = _svc()
    calls = {"rounds": [], "spawned": [], "ann_updates": []}

    monkeypatch.setattr(svc.FileReviewRoundService, "create_round",
                        staticmethod(lambda **kw: calls["rounds"].append(kw) or "round-1"))
    monkeypatch.setattr(svc.FileReviewRoundService, "get_owned_task",
                        staticmethod(lambda task_id, tid: list(rounds or [])))
    monkeypatch.setattr(svc.FileReviewRoundService, "next_round",
                        staticmethod(lambda task_id: next_round))
    monkeypatch.setattr(svc.FileReviewTemplateService, "list_enabled",
                        staticmethod(lambda tid: list(templates or [])))
    monkeypatch.setattr(svc.FileReviewAnnotationService, "list_pending_by_task",
                        staticmethod(lambda task_id: list(pending or [])))
    # 任何对标注状态的改写都是本任务明确禁止的（未选中级别不得被置 wontfix），
    # 记下来供 test_fix_never_mutates_annotation_status 断言。
    monkeypatch.setattr(svc.FileReviewAnnotationService, "update_status",
                        staticmethod(
                            lambda aid, status: calls["ann_updates"].append((aid, status))))
    monkeypatch.setattr(svc, "fix_rounds_left", lambda rows: left)
    monkeypatch.setattr("agent.tools.file_review.time.sleep", lambda s: None)

    from rag.svr.file_review import spawn as spawn_mod
    monkeypatch.setattr(spawn_mod, "spawn_review_task",
                        lambda task_id: calls["spawned"].append(task_id))
    return calls


def _make_tool(tenant="tenant_x", components=None):
    from agent.tools.file_review import FileReviewTool

    tool = object.__new__(FileReviewTool)
    tool._param = SimpleNamespace(outputs={}, inputs={}, debug_inputs={})
    tool.set_output = lambda key, value=None: tool._param.outputs.update({key: {"value": value}})
    tool.check_if_canceled = lambda msg="": False
    tool._canvas = SimpleNamespace(get_tenant_id=lambda: tenant,
                                   components=components or {})
    return tool


# ---------- meta 声明 ----------

def test_meta_declaration():
    from agent.tools.file_review import FileReviewTool, FileReviewToolParam

    param = FileReviewToolParam()
    assert param.meta["name"] == "FileReviewTool"
    assert param.meta["parameters"]["action"]["enum"] == [
        "list_templates", "review", "status", "fix"]
    # 类名必须与 T7 节点 FileReview 不同：component_class 按类名解析且 agent.component
    # 优先于 agent.tools，同名会互相遮蔽
    assert FileReviewTool.component_name == "FileReviewTool"


def test_unknown_action_lists_options():
    out = _make_tool()._invoke(action="nope")
    assert "不支持的 action" in out and "list_templates" in out


# ---------- list_templates ----------

def test_list_templates_renders_rows(monkeypatch):
    _patch(monkeypatch, templates=[_tpl("t1", "投标文件审核"), _tpl("t2", "合同审核")])
    out = _make_tool()._invoke(action="list_templates")
    assert "投标文件审核" in out and "t1" in out and "合同审核" in out


def test_list_templates_empty(monkeypatch):
    _patch(monkeypatch, templates=[])
    out = _make_tool()._invoke(action="list_templates")
    assert "没有可用的审核模板" in out


# ---------- review：file_id 解析 ----------

def test_review_uses_file_id_from_begin_output(monkeypatch):
    calls = _patch(monkeypatch, templates=[_tpl()])
    tool = _make_tool(components=_begin({"review_file_id": PFX + "upload-1"}))
    tool._invoke(action="review", user_query="重点看资质", kb_ids='["kb1", "kb2"]')
    kw = calls["rounds"][0]
    assert kw["file_id"] == PFX + "upload-1"
    assert kw["round_no"] == 1 and kw["file_version"] == "v1"
    assert kw["status"] == "reviewing"
    assert kw["tenant_id"] == "tenant_x" and kw["created_by"] == "tenant_x"
    assert kw["user_query"] == "重点看资质"
    assert kw["kb_ids"] == ["kb1", "kb2"]


def test_review_spawns_task_id_not_round_id(monkeypatch):
    calls = _patch(monkeypatch, templates=[_tpl()])
    _make_tool(components=_begin({"review_file_id": "u1"}))._invoke(action="review")
    assert calls["spawned"] == [calls["rounds"][0]["task_id"]]
    assert "round-1" not in calls["spawned"]


def test_review_refuses_without_file_id(monkeypatch):
    calls = _patch(monkeypatch, templates=[_tpl()])
    out = _make_tool(components=_begin({}))._invoke(action="review")
    assert "上传" in out
    assert calls["rounds"] == [] and calls["spawned"] == []


def test_review_refuses_when_begin_component_missing(monkeypatch):
    calls = _patch(monkeypatch, templates=[_tpl()])
    out = _make_tool(components={})._invoke(action="review")
    assert "上传" in out and calls["rounds"] == []


def test_review_refuses_when_begin_output_raises(monkeypatch):
    calls = _patch(monkeypatch, templates=[_tpl()])
    tool = _make_tool(components={"begin": {"obj": _BadBegin()}})
    out = tool._invoke(action="review")
    assert "上传" in out and calls["rounds"] == []


def test_review_refuses_when_begin_output_non_str(monkeypatch):
    calls = _patch(monkeypatch, templates=[_tpl()])
    tool = _make_tool(components=_begin({"review_file_id": 123}))
    out = tool._invoke(action="review")
    assert "上传" in out and calls["rounds"] == []


def test_review_refuses_without_tenant(monkeypatch):
    calls = _patch(monkeypatch, templates=[_tpl()])
    tool = _make_tool(tenant="", components=_begin({"review_file_id": "u1"}))
    out = tool._invoke(action="review")
    assert "身份" in out and calls["rounds"] == []


# ---------- review：模板与 kb_ids ----------

def test_review_rejects_unknown_template(monkeypatch):
    """LLM 编造的模板 id 不许静默回落默认值：否则轮次行会留下一条假模板审计记录。"""
    calls = _patch(monkeypatch, templates=[_tpl("t1", "投标文件审核")])
    tool = _make_tool(components=_begin({"review_file_id": "u1"}))
    out = tool._invoke(action="review", template_id="made-up-id")
    assert "不可用" in out and "投标文件审核" in out
    assert calls["rounds"] == []


def test_review_default_template_comes_from_executor_constant(monkeypatch):
    from rag.svr.file_review.executor import DEFAULT_TEMPLATE_ID

    calls = _patch(monkeypatch, templates=[_tpl(DEFAULT_TEMPLATE_ID)])
    _make_tool(components=_begin({"review_file_id": "u1"}))._invoke(action="review")
    assert calls["rounds"][0]["template_id"] == DEFAULT_TEMPLATE_ID


def test_review_kb_ids_tolerant_parsing(monkeypatch):
    calls = _patch(monkeypatch, templates=[_tpl()])
    tool = _make_tool(components=_begin({"review_file_id": "u1"}))
    tool._invoke(action="review", kb_ids="kb1, kb2")
    assert calls["rounds"][0]["kb_ids"] == ["kb1", "kb2"]
    calls["rounds"].clear()
    tool._invoke(action="review", kb_ids="")
    assert calls["rounds"][0]["kb_ids"] is None


# ---------- review：轮询 ----------

def test_review_poll_timeout_returns_task_id(monkeypatch):
    running = [_round(1, "reviewing")]
    calls = _patch(monkeypatch, templates=[_tpl()], rounds=running)
    out = _make_tool(components=_begin({"review_file_id": "u1"}))._invoke(action="review")
    assert "仍在进行中" in out and "task_id=" in out and "status" in out
    assert calls["spawned"]


def test_review_poll_terminal_returns_summary(monkeypatch):
    done = [_round(1, "annotated", summary="共发现 3 处问题")]
    _patch(monkeypatch, templates=[_tpl()], rounds=done, pending=[_ann()])
    out = _make_tool(components=_begin({"review_file_id": "u1"}))._invoke(action="review")
    assert "审核完成" in out and "共发现 3 处问题" in out
    assert "资质缺失" in out and "严重" in out


# ---------- status ----------

def test_status_requires_task_id():
    out = _make_tool()._invoke(action="status")
    assert "task_id" in out


def test_status_not_owned_refused(monkeypatch):
    _patch(monkeypatch, rounds=[])
    out = _make_tool()._invoke(action="status", task_id=PFX + "other-task")
    assert "没有找到" in out and "无权访问" in out


def test_status_lists_pending_grouped_by_severity(monkeypatch):
    rows = [_round(1, "annotated"), _round(2, "done", minio_path="frv-x-v2")]
    _patch(monkeypatch, rounds=rows,
           pending=[_ann("high"), _ann("medium"), _ann("low")])
    out = _make_tool()._invoke(action="status", task_id=PFX + "task")
    assert "严重" in out and "一般" in out and "提示" in out
    assert "成稿" in out and "余额" in out


def test_status_reports_failed_round_error(monkeypatch):
    rows = [_round(1, "failed", error="LLM 输出无法解析为修复补丁列表")]
    _patch(monkeypatch, rounds=rows)
    out = _make_tool()._invoke(action="status", task_id=PFX + "task")
    assert "本轮失败" in out and "无法解析" in out


# ---------- fix ----------

def test_fix_creates_new_round_and_encodes_levels(monkeypatch):
    cur = _round(1, "annotated", user_query="重点看资质", kb_ids='["kb1"]')
    calls = _patch(monkeypatch, rounds=[cur], next_round=(2, "v2"), left=3,
                   pending=[_ann("high")])
    _make_tool()._invoke(action="fix", task_id=PFX + "task", levels="high")
    kw = calls["rounds"][0]
    assert kw["round_no"] == 2 and kw["file_version"] == "v2"
    assert kw["status"] == "fixing"
    assert kw["file_id"] == cur.file_id          # 沿用该 task 的文件，不重新解析 Begin
    assert kw["template_id"] == cur.template_id
    assert kw["kb_ids"] == cur.kb_ids            # 修复轮继承知识库
    assert kw["tenant_id"] == "tenant_x" and kw["created_by"] == "tenant_x"
    assert "严重" in kw["user_query"] and "重点看资质" in kw["user_query"]
    assert calls["spawned"] == [kw["task_id"]]


def test_fix_never_mutates_annotation_status(monkeypatch):
    """级别过滤**不得**靠把未选中级别置 wontfix 实现。

    wontfix 的语义是「用户决定永不修复此条」；自动置位后用户改口「把中等的也修了」
    会静默失效（list_pending_by_task 不再看到它），且用户无法从对话里察觉。
    过滤改为写进本轮 user_query（executor 的修复 prompt 会带上它）。
    """
    cur = _round(1, "annotated")
    calls = _patch(monkeypatch, rounds=[cur], next_round=(2, "v2"),
                   pending=[_ann("high"), _ann("medium")])
    _make_tool()._invoke(action="fix", task_id=PFX + "task", levels="high")
    assert calls["ann_updates"] == []
    assert calls["rounds"]


def test_fix_rejects_while_previous_round_running(monkeypatch):
    calls = _patch(monkeypatch, rounds=[_round(1, "reviewing")])
    out = _make_tool()._invoke(action="fix", task_id=PFX + "task", levels="high")
    assert "仍在进行中" in out and calls["rounds"] == []


def test_fix_rejects_when_rounds_exhausted(monkeypatch):
    calls = _patch(monkeypatch, rounds=[_round(3, "done")], left=0)
    out = _make_tool()._invoke(action="fix", task_id=PFX + "task", levels="high")
    assert "最大修复轮数" in out and "保持原样" in out and calls["rounds"] == []


def test_fix_rejects_unknown_levels(monkeypatch):
    """未知级别必须拒绝而不是兜底成 medium：用户只想要 low 却被升格成 medium
    会让「只修低级别」变成「修中等级别」，与用户明确的指令相反。"""
    calls = _patch(monkeypatch, rounds=[_round(1, "annotated")], pending=[_ann("low")])
    out = _make_tool()._invoke(action="fix", task_id=PFX + "task", levels="urgent")
    assert "levels" in out and calls["rounds"] == []


def test_fix_rejects_when_no_pending_at_that_level(monkeypatch):
    calls = _patch(monkeypatch, rounds=[_round(1, "annotated")], pending=[_ann("high")])
    out = _make_tool()._invoke(action="fix", task_id=PFX + "task", levels="low")
    assert "没有" in out and calls["rounds"] == []


def test_fix_not_owned_refused(monkeypatch):
    calls = _patch(monkeypatch, rounds=[])
    out = _make_tool()._invoke(action="fix", task_id=PFX + "other", levels="high")
    assert "无权访问" in out and calls["rounds"] == []


def test_parse_levels_tolerant_and_ordered():
    from agent.tools.file_review import FileReviewTool

    assert FileReviewTool._parse_levels("低,high,LOW,严重") == ["high", "low"]
    assert FileReviewTool._parse_levels('["medium"]') == ["medium"]
    assert FileReviewTool._parse_levels(["medium", "high"]) == ["high", "medium"]
    assert FileReviewTool._parse_levels("") == []
    assert FileReviewTool._parse_levels(None) == []
```

- [ ] **Step 6: 跑测试确认失败**

Run: `uv run --no-sync pytest test/test_file_review_tool.py -v`
Expected: FAIL —— `ModuleNotFoundError: No module named 'agent.tools.file_review'`

- [ ] **Step 7: 创建 `agent/tools/file_review.py`**

```python
# agent/tools/file_review.py
"""C端对话 FileReviewTool：列审核模板 / 发起审核 / 查进度 / 按级别发起修复。

照 agent/tools/template_fill.py 的插件结构：FileReviewToolParam(ToolParamBase) 声明
meta，FileReviewTool(ToolBase) 提供 _invoke，被 component_class 按类名自动发现。
Service 层与 executor 全部**延迟 import**：前者避免工具注册期拉起 DB/Quart，后者
（含 python-docx）只在真正跑起来时才需要。

与 T7 画布节点 agent/component/file_review.py 是同一份执行真相的**两个入口**：两者都
只做「建轮次行 + spawn 后台线程」，随即返回；进度与结果一律按轮次行反查（本工具的
action=status 与 T9 的 REST 端点读同一批行）。故本模块不推 SSE、不观察线程 —— 把执行
绑死在某个具体连接上，刷新即丢，且第三个入口无连接可推。

类名必须与节点的 FileReview 不同：component_class 按类名解析且 agent.component 优先于
agent.tools，同名会互相遮蔽（T7 docstring 记录的同一条教训）。
"""

import json
import logging
import os
import time
from abc import ABC

from agent.component.file_review import FILE_ID_INPUT_KEY
from agent.tools.base import ToolBase, ToolMeta, ToolParamBase
from common.connection_utils import timeout
from common.misc_utils import get_uuid
from rag.svr.file_review import spawn as spawn_mod

logger = logging.getLogger(__name__)

# 执行超时与同步轮询上限：与 FillTemplate 同款（env 驱动；等待取 min(90, timeout-10)，
# 保证轮询不会吃满装饰器配额，超时后引导用户用 action=status 异步查询）。
_EXEC_TIMEOUT = int(os.environ.get("COMPONENT_EXEC_TIMEOUT", "60"))
_MAX_WAIT_SECONDS = min(90, max(_EXEC_TIMEOUT - 10, 10))
_POLL_INTERVAL = 3

# 轮次终态：annotated（首轮审核收口）/ done（修复轮收口）/ failed。
# 前两者 = Service 层 COMPLETED_ROUND_STATUSES，这里并上 failed —— 工具要能对用户
# 如实说「这轮失败了」，不能像 Service 那样把 failed 当作「没发生」。
_TERMINAL_ROUND_STATUSES = ("annotated", "done", "failed")
_RUNNING_ROUND_STATUSES = ("reviewing", "fixing")

_ROUND_STATUS_CN = {
    "reviewing": "审核中", "annotated": "审核完成", "fixing": "修复中",
    "done": "修复完成", "failed": "失败",
}
_SEVERITY_CN = {"high": "严重", "medium": "一般", "low": "提示"}
_SEVERITY_RANK = {"high": 0, "medium": 1, "low": 2}

# 返回值会进 LLM 上下文：逐条 issue 全文（上限 1000 字）会刷爆 token，逐项裁剪。
_MAX_LIST_ITEMS = 30
_MAX_ISSUE_CHARS = 120
_MAX_MATCHED_CHARS = 60
_MAX_SUMMARY_CHARS = 400


def _clip(text, limit: int) -> str:
    s = (text or "").strip()
    return s if len(s) <= limit else s[:limit] + "…"


def _compose_fix_query(prev: str, levels: list) -> str:
    """把级别选择编进本轮 user_query。

    executor 的修复 prompt 会把轮次行的 user_query 原样作为「用户需求：」喂给 LLM
    （_build_fix_prompt 实测确认），而待修复清单里每条都带「级别=」，级别过滤借此生效。

    刻意**不**把未选中级别的标注置 wontfix 来硬过滤：wontfix 的语义是「用户决定永不
    修复此条」，自动置位后用户改口「把中等的也修了」会静默失效（list_pending_by_task
    不再看到它），而用户没有任何办法从对话里发现这件事。保留原需求原文是因为 executor
    的修复 prompt 只认这一个字段，丢掉它等于丢掉用户的审核意图。
    """
    chosen = "、".join(_SEVERITY_CN.get(s, s) for s in levels)
    head = f"本次只修复【{chosen}】级别的问题，其余级别的问题请保持原样、不要改动。"
    base = (prev or "").strip()
    return f"{base}\n{head}" if base else head


class FileReviewToolParam(ToolParamBase):
    """FileReviewTool 参数声明。"""

    def __init__(self):
        self.meta: ToolMeta = {
            "name": "FileReviewTool",
            "description": """文件审核工具。四个 action：

1. list_templates：列出可用的审核模板（名称/用途/id）。用户没指定模板时先调用它。
2. review：对**用户刚上传的文件**发起一轮审核。本 action **不需要 file_id 参数** —— 待审核的文件由运行环境注入，工具自己取。可选 template_id（不传则用招标文件格式审核模板）、kb_ids（检索审核依据的知识库 ID 列表，JSON 数组字符串，如 '["kb1"]'，可选）、user_query（用户的审核要求原话，如「重点看资质和工期」）。提交后同步等待最多约 1 分钟，完成即返回批注摘要；超时返回 task_id，引导用户稍后用 action=status 查。
3. status：按 task_id 查审核进度与批注清单（按级别分组、列出未修复项），以及成稿预览/下载指引。
4. fix：按**用户选定的问题级别**发起一轮修复。task_id 与 levels 均必填（levels 取值 high/medium/low，逗号分隔）。用户说「把严重的问题改掉」「只修中等的」时用它。最多 3 轮；用尽后明确告知「未修复的保持原样」。

使用时机：用户上传文件并表达审核/检查/把关/看看有没有问题的意图时用 review；用户明确表示要针对某个级别的问题动手修改时用 fix。**先展示后修复**：review 之后要把批注读给用户听、由用户决定修哪个级别，不要自动接着调 fix。levels 只能取用户明确说出的级别，不得替用户扩大范围。kb_ids 必须由用户提供（可结合知识库列表工具），不要编造。""",
            "parameters": {
                "action": {
                    "type": "string",
                    "description": "操作类型：list_templates（列审核模板）/ review（发起审核）/ status（查进度与批注）/ fix（按级别发起修复）。",
                    "enum": ["list_templates", "review", "status", "fix"],
                    "required": True,
                },
                "task_id": {
                    "type": "string",
                    "description": "审核任务 id。action=status / fix 时必填（review 返回的 task_id）。",
                    "default": "",
                    "required": False,
                },
                "template_id": {
                    "type": "string",
                    "description": "审核模板 id（list_templates 返回的 id）。action=review 时可选，不传则用默认的招标文件格式审核模板。",
                    "default": "",
                    "required": False,
                },
                "user_query": {
                    "type": "string",
                    "description": "用户的审核要求原话（如「重点看资质和工期是否满足」）。action=review 时可选。",
                    "default": "",
                    "required": False,
                },
                "kb_ids": {
                    "type": "string",
                    "description": "知识库 ID 列表，JSON 数组字符串，如 '[\"kb123\"]'。action=review 时可选，用于检索审核依据。",
                    "default": "",
                    "required": False,
                },
                "levels": {
                    "type": "string",
                    "description": "要修复的问题级别，逗号分隔，取值 high/medium/low。action=fix 时必填。",
                    "default": "",
                    "required": False,
                },
            },
        }
        super().__init__()


class FileReviewTool(ToolBase, ABC):
    """C端对话文件审核工具。"""

    component_name = "FileReviewTool"

    @timeout(_EXEC_TIMEOUT)
    def _invoke(self, **kwargs):
        if self.check_if_canceled("FileReviewTool"):
            return
        try:
            action = str(kwargs.get("action") or "").strip()
            if action == "list_templates":
                return self._list_templates()
            if action == "review":
                return self._review(kwargs)
            if action == "status":
                return self._status(kwargs)
            if action == "fix":
                return self._fix(kwargs)
            return (f"不支持的 action：{action or '(空)'}。可用 action："
                    "list_templates（列审核模板）/ review（发起审核）/ "
                    "status（查进度与批注）/ fix（按级别发起修复）。")
        except Exception as e:
            logger.exception("FileReviewTool invoke failed")
            self.set_output("_ERROR", str(e))
            return f"文件审核执行失败：{e}"

    # ---------- action: list_templates ----------

    def _list_templates(self):
        from api.db.services.file_review_service import FileReviewTemplateService

        tenant_id = self._get_tenant_id()
        if not tenant_id:
            return "无法确定当前用户身份，请稍后重试。"
        rows = FileReviewTemplateService.list_enabled(tenant_id)
        if not rows:
            return "当前没有可用的审核模板。"
        lines = [f"共 {len(rows)} 套可用审核模板："]
        for r in rows:
            desc = (r.description or "").strip()
            lines.append(f"- {r.name}（id={r.id}）" + (f"：{desc}" if desc else ""))
        lines.append("请告知用哪套模板、以及本次审核要重点看什么，即可发起审核。")
        return "\n".join(lines)

    # ---------- action: review ----------

    def _review(self, kwargs):
        from api.db.services.file_review_service import (
            FileReviewRoundService,
            FileReviewTemplateService,
        )
        from rag.svr.file_review.executor import DEFAULT_TEMPLATE_ID

        tenant_id = self._get_tenant_id()
        if not tenant_id:
            return "无法确定当前用户身份，请稍后重试。"

        file_id = self._resolve_file_id()
        if not file_id:
            return ("没有拿到待审核的文件。请先上传要审核的文件，再说出审核要求"
                    "（例如「帮我看看这份标书有没有问题」）。")

        enabled = FileReviewTemplateService.list_enabled(tenant_id)
        # 默认模板取自 executor 常量，不在此复刻字面量：默认值改了这里必须跟着变，
        # 复制一份就是等着漂移（T7 节点同款处理）。
        template_id = str(kwargs.get("template_id") or "").strip() or DEFAULT_TEMPLATE_ID
        if template_id not in [t.id for t in enabled]:
            # 不静默回落到默认值：LLM 编造的模板 id 若被吞掉，轮次行里就会留下一条
            # 「用户用了 X 模板」的假审计记录。把可用清单回给 LLM 让它自查重试。
            lines = [f"审核模板不可用：{template_id}。可用模板："]
            for t in enabled:
                lines.append(f"- {t.name}（id={t.id}）")
            return "\n".join(lines)

        task_id = get_uuid()
        round_no, file_version = FileReviewRoundService.next_round(task_id)
        FileReviewRoundService.create_round(
            task_id=task_id, file_id=file_id, round_no=round_no,
            template_id=template_id,
            user_query=str(kwargs.get("user_query") or "").strip(),
            file_version=file_version, status="reviewing",
            tenant_id=tenant_id, created_by=tenant_id,
            # 形态（list / JSON 文本 / 裸 id）归一是 Service 层 _normalize_kb_ids 的职责，
            # 工具只把解析出的空值折成 None（「这次不用参考资料」的显式表达）。
            kb_ids=self._parse_kb_ids(kwargs.get("kb_ids")) or None,
        )
        spawn_mod.spawn_review_task(task_id)
        return self._poll(task_id, tenant_id)

    # ---------- action: status ----------

    def _status(self, kwargs):
        from api.db.services.file_review_service import FileReviewRoundService

        tenant_id = self._get_tenant_id()
        if not tenant_id:
            return "无法确定当前用户身份，请稍后重试。"
        task_id = str(kwargs.get("task_id") or "").strip()
        if not task_id:
            return "请提供 task_id（发起审核时返回的任务 id）。"
        rounds = FileReviewRoundService.get_owned_task(task_id, tenant_id)
        if not rounds:
            return "没有找到该审核任务，或无权访问（请确认 task_id 是否正确）。"
        return self._format_status(rounds)

    # ---------- action: fix ----------

    def _fix(self, kwargs):
        from api.db.services.file_review_service import (
            MAX_FIX_ROUNDS,
            FileReviewAnnotationService,
            FileReviewRoundService,
            fix_rounds_left,
        )

        tenant_id = self._get_tenant_id()
        if not tenant_id:
            return "无法确定当前用户身份，请稍后重试。"
        task_id = str(kwargs.get("task_id") or "").strip()
        if not task_id:
            return "请提供 task_id（发起审核时返回的任务 id）。"
        rounds = FileReviewRoundService.get_owned_task(task_id, tenant_id)
        if not rounds:
            return "没有找到该审核任务，或无权访问（请确认 task_id 是否正确）。"

        cur = rounds[-1]
        if cur.status in _RUNNING_ROUND_STATUSES:
            return (f"第 {cur.round_no} 轮（{_ROUND_STATUS_CN.get(cur.status, cur.status)}）"
                    "仍在进行中，请稍后用 action=status 查询进度后再发起修复。")
        if fix_rounds_left(rounds) <= 0:
            return f"已达到最大修复轮数（{MAX_FIX_ROUNDS} 轮），未修复的问题保持原样。"

        levels = self._parse_levels(kwargs.get("levels"))
        if not levels:
            return "请提供要修复的问题级别 levels（high / medium / low 中的一到多个，逗号分隔）。"

        pending = FileReviewAnnotationService.list_pending_by_task(task_id)
        if not [a for a in pending if a.severity in levels]:
            # 该级别没东西可修时**不建空转轮次**：executor 会立刻以「没有待修复的问题」
            # 收口，白烧一次轮次余额，面板上还多一条无意义的行。
            return ("没有【" + "、".join(_SEVERITY_CN.get(s, s) for s in levels)
                    + "】级别的待修复问题。待修复问题：" + self._severity_summary(pending))

        round_no, file_version = FileReviewRoundService.next_round(task_id)
        FileReviewRoundService.create_round(
            task_id=task_id, file_id=cur.file_id, round_no=round_no,
            template_id=cur.template_id,
            user_query=_compose_fix_query(cur.user_query, levels),
            file_version=file_version, status="fixing",
            tenant_id=tenant_id, created_by=tenant_id,
            # 修复轮沿用本轮的 kb_ids（已是 JSON 文本，_normalize_kb_ids 幂等）：
            # 轮次行是「这轮用了哪些知识库」的审计记录，不能因为本轮不检索就写成空。
            kb_ids=cur.kb_ids,
        )
        spawn_mod.spawn_review_task(task_id)
        return self._poll(task_id, tenant_id)

    # ---------- 轮询与摘要 ----------

    def _poll(self, task_id: str, tenant_id: str) -> str:
        """同步轮询至终态或超时（sleep 在测试中被替换）。

        每一跳都带 tenant 走 get_owned_task：轮询期间租户上下文不变，但重查时仍走
        同一条闸门，避免「入口校验了、产出路径没校验」的半吊子防线。
        """
        from api.db.services.file_review_service import FileReviewRoundService

        waited = 0.0
        while waited < _MAX_WAIT_SECONDS:
            time.sleep(_POLL_INTERVAL)
            waited += _POLL_INTERVAL
            rounds = FileReviewRoundService.get_owned_task(task_id, tenant_id)
            if rounds and rounds[-1].status in _TERMINAL_ROUND_STATUSES:
                return self._format_status(rounds)
        return (f"审核任务已提交（task_id={task_id}），目前仍在进行中。"
                "请稍后用 action=status 携带该 task_id 查询进度与批注。")

    def _format_status(self, rounds) -> str:
        """轮次 + 批注统计 + 待修复清单 + 成稿指引。

        action=status 与 _poll 收尾**共用**本方法：两条路径（同步等到 / 稍后查）给出的
        信息必须一致，否则用户前后两次看到的对不上，会以为结果变了。
        """
        from api.db.services.file_review_service import (
            MAX_FIX_ROUNDS,
            FileReviewAnnotationService,
            fix_rounds_left,
        )

        task_id = rounds[0].task_id
        cur = rounds[-1]
        lines = [f"审核任务 {task_id}：共 {len(rounds)} 轮，当前第 {cur.round_no} 轮"
                 f"（{_ROUND_STATUS_CN.get(cur.status, cur.status)}）。"]
        if cur.status == "failed":
            lines.append("本轮失败：" + (cur.error or "（无详细信息，详见服务端日志）"))
        if cur.summary:
            lines.append("本轮结果：" + _clip(cur.summary, _MAX_SUMMARY_CHARS))

        pending = FileReviewAnnotationService.list_pending_by_task(task_id)
        lines.append("待修复问题：" + self._severity_summary(pending))
        for a in pending[:_MAX_LIST_ITEMS]:
            line = (f"- [{_SEVERITY_CN.get(a.severity, a.severity)}] "
                    f"{_clip(a.issue, _MAX_ISSUE_CHARS)}")
            if a.matched_text:
                line += f"（原文：{_clip(a.matched_text, _MAX_MATCHED_CHARS)}）"
            lines.append(line)
        if len(pending) > _MAX_LIST_ITEMS:
            lines.append(f"…（其余 {len(pending) - _MAX_LIST_ITEMS} 条略）")

        # 判据是「有 minio_path 就有可下载成稿」，与 status 无关：T6 交接契约第 2 条
        # 实测 failed 轮次也可能已经落盘（收口那步抛错前成稿已写进 MinIO）。
        produced = [r for r in rounds if r.minio_path]
        if produced:
            latest = produced[-1]
            lines.append(f"已产出第 {latest.round_no} 轮成稿，可在「文件审核」面板中预览/下载。")
        lines.append(f"修复轮次余额：还可发起 {fix_rounds_left(rounds)} 轮"
                     f"（上限 {MAX_FIX_ROUNDS} 轮）。")
        if pending:
            lines.append("如需修复，请告知要修复哪个级别（严重/一般/提示）；"
                         "不需要修复的可以先放着，未修复的问题会保持原样。")
        return "\n".join(lines)

    @staticmethod
    def _severity_summary(items) -> str:
        """按级别统计待修复条数（无则明说「无」，不留空让 LLM 猜）。"""
        if not items:
            return "无"
        counts = {}
        for a in items:
            counts[a.severity] = counts.get(a.severity, 0) + 1
        parts = [f"{_SEVERITY_CN.get(s, s)} {counts[s]} 条"
                 for s in sorted(counts, key=lambda x: _SEVERITY_RANK.get(x, 99))]
        return f"共 {len(items)} 条（{'、'.join(parts)}）"

    # ---------- 辅助 ----------

    def _get_tenant_id(self) -> str:
        canvas = getattr(self, "_canvas", None)
        if not canvas:
            return ""
        try:
            return canvas.get_tenant_id() or ""
        except Exception:  # noqa: BLE001 — canvas 实现异常类型不一，兜底空串
            return ""

    def _begin_output(self, key: str) -> str:
        """扫 Begin 输出取键值（与 T7 节点 FileReview._begin_output 同款）。

        不按 id 取：Begin 的 id 虽是硬编码的 "begin"，但节点参数可能被写成别的组件的
        引用、或干脆留空，而「本次传入的上传文件」只可能来自 Begin 输出 —— 扫一遍比
        断言 id 更耐用。取不到（含异常）都返回空串，由调用方给出用户可读的拒绝。
        """
        canvas = getattr(self, "_canvas", None)
        if not canvas:
            return ""
        try:
            for cpn in (canvas.components or {}).values():
                obj = cpn.get("obj") if isinstance(cpn, dict) else None
                if obj is not None and getattr(obj, "component_name", "").lower() == "begin":
                    val = (obj.output() or {}).get(key)
                    return val if isinstance(val, str) else ""
        except Exception:
            logger.warning("FileReviewTool._begin_output failed", exc_info=True)
        return ""

    def _resolve_file_id(self) -> str:
        """待审核文件 id（= /documents/upload 返回的 uuid）= 前端送入 Begin 的值。

        刻意不接受 LLM 传参：见模块 docstring —— 编造 uuid 会一路落进轮次行与 MinIO
        对象名，最后以一句「文件不存在」暴露，用户完全无从修正。
        """
        return self._begin_output(FILE_ID_INPUT_KEY)

    @staticmethod
    def _parse_kb_ids(raw) -> list:
        """kb_ids 容错解析：JSON 数组字符串 / 逗号分隔字符串 / list 均可。"""
        if raw is None:
            return []
        if isinstance(raw, (list, tuple)):
            items = list(raw)
        else:
            s = str(raw).strip()
            if not s:
                return []
            try:
                parsed = json.loads(s)
                items = parsed if isinstance(parsed, (list, tuple)) else []
            except Exception:  # noqa: BLE001 — LLM 输出容错：非 JSON 降级为逗号分隔
                items = [x for x in s.split(",") if x.strip()]
        return [str(k).strip() for k in items if str(k).strip()]

    @staticmethod
    def _parse_levels(raw) -> list:
        """levels → high/medium/low 去重且按严重度排序的列表。

        别名表复用 executor._SEVERITY_ALIASES（「级别语义」的唯一真相；不复刻一份，
        否则「警告」在一处算 medium、在另一处被拒）。
        与 executor._norm_severity 的区别是未知词**丢弃而非兜底成 medium**：用户可能
        只想要 low，静默升格会让「只修低级别」变成「修中等级别」，与用户明说的指令相反。
        """
        if raw is None:
            return []
        if isinstance(raw, (list, tuple)):
            items = [str(x) for x in raw]
        else:
            s = str(raw).strip()
            if not s:
                return []
            try:
                parsed = json.loads(s)
            except Exception:  # noqa: BLE001 — LLM 输出容错：非 JSON 降级为逗号分隔
                parsed = None
            items = [str(x) for x in parsed] if isinstance(parsed, list) else s.split(",")

        from rag.svr.file_review.executor import _SEVERITY_ALIASES

        out = []
        for it in items:
            sev = _SEVERITY_ALIASES.get(it.strip().lower())
            if sev and sev not in out:
                out.append(sev)
        return sorted(out, key=lambda s: _SEVERITY_RANK[s])
```

- [ ] **Step 8: 跑测试确认通过**

Run: `uv run --no-sync pytest test/test_file_review_tool.py -v`
Expected: PASS（28 例；代码质量整改轮追加 9 例后为 37 例，见本节文末「实测落地」）

- [ ] **Step 9: 冒烟 + 回归**

```bash
uv run --no-sync pytest test/test_file_review_tool.py test/test_file_review_service.py test/test_file_review_node.py -q
uv run --no-sync pytest test/test_template_fill_tool.py -q
uv run --no-sync ruff check agent/tools/file_review.py api/db/services/file_review_service.py test/test_file_review_tool.py
python -c "from agent.tools.file_review import FileReviewTool; print('tool OK', FileReviewTool.component_name)"
```

Expected: 全绿 + ruff 无输出 + `tool OK FileReviewTool`。
（最后一条必须在**项目根**跑：它同时证明工具模块顶层 import 不会拉起 docx/Quart 等重依赖。）

- [ ] **Step 10: 提交**

```bash
git add agent/tools/file_review.py test/test_file_review_tool.py api/db/services/file_review_service.py test/test_file_review_service.py
git commit -m "feat(file-review): 对话工具 FileReviewTool + 越权闸门/轮次编号助手"
```

**实测落地**：`4eb682db`（实现：`agent/tools/file_review.py` 458 行 + `test/test_file_review_tool.py` 348 行／28 例 + Service 层纯追加 63 行／service 测 6 例）→ 代码质量审查判 `CHANGES REQUESTED` → 整改 `e7ea3c7d`（+223/-16，3 文件）→ 复审 `APPROVED` → `3ec33847`（清两条 Minor）。

两轮审查抓到的**真缺陷**（都在实现里，且都由新增用例锁死，控制者已实测「回退实现即红」）：
1. **连轮修复向 LLM 投喂互斥指令**：原 `_compose_fix_query(cur.user_query, levels)` 的 `cur` 是 `rounds[-1]`，第 3 轮会把「本次只修复【严重】…」与「本次只修复【一般】…」叠在一起（实测拼出原文两条并存），而 `executor._build_fix_prompt` 把 `user_query` **原样**塞进「用户需求：」——LLM 收到自相矛盾的要求。修法：基准改 `rounds[0].user_query`（首轮恒为该 task 的原始审核轮，因 `_review` 每次用新 `get_uuid()` 开新 task）。
2. **spawn 静默 no-op 造孤儿 `fixing` 轮次、永久卡死该 task**：`spawn_review_task` 命中 `_running_tasks` 即静默 return；`executor._run_fix_round` **先**置轮次终态 `done`、**再** `_settle_annotations` 写最多 20 条标注（窗口宽达 20 次 DB 往返）；`execute_task` 每次只处理 `rounds[-1]` 一轮且不循环；无看门狗。四者叠加 ⇒ 新轮无人消费、恒停 `fixing`、之后所有 fix/review 被前置闸门挡死且**不可自愈**。修法：`_fix` 在 `create_round` 之前加 `if spawn_mod.is_running(task_id): return "…稍后再试…"`，把不可逆卡死换成可重试；位置必须在四道语义闸门**之后**，否则真因是「没有可修项」时会被误答成「稍后再试」。
另修正 3 处**注释与实际行为不符**（M4 模块 docstring 的「延迟 import」论断、I3 `agent/component/file_review.py:49` 已作废的 `max_completed_round_no` 指向、M7 `_severity_cn` 口径）——本项目已两次栽在「注释说得比代码好听」上，故一律改注释就实，不改注释就改代码。
**越界未修（已挂下游）**：`_get_tenant_id`/`_parse_kb_ids`/`_begin_output` 与 `template_fill.py`/T7 节点约 40 行重复——抽 mixin 要动 `agent/tools/base.py` 与既有 `template_fill.py`，违反「不影响现有功能点」硬约束，留作独立后续任务。`executor.py` 的 `chosen = pending[:MAX_FIX_ITEMS]` 无 severity 硬过滤是 T6 既有取舍（级别过滤只能经 `user_query` 软表达），不改上游。

**交棒 T9 的硬约束**（T9 规格另在 Task 9 段就地标注，此处汇总）：
- 轮号一律用 `FileReviewRoundService.next_round()`，**不得**用 `max_completed_round_no`（后者只数 `done`/`annotated`，会出现同号覆盖失败轮产物 / 新轮基线自引用 / 面板同号）。
- 修复余额一律用 `fix_rounds_left()`（失败轮计入），不得自算。
- **并发闸门必须自己在 REST 层补**：I2 的 `is_running` 检查只覆盖单写入者前提（对话内工具调用串行）；T9 的 HTTP 重试/双击会让两个请求同时过闸、各建一条同号轮次、后到者 spawn 静默 no-op。第一性上更干净的修法是让 `spawn_review_task` 返回 `bool`（注册失败由调用方决定是否回滚轮次，消除 TOCTOU），但那要改 `spawn.py`（T5 已审契约），故 T9 需显式决策。


## Task 9: REST API 端点（4 个）+ Service 层收口

**Files:**
- Create: `api/apps/restful_apis/file_review_api.py`
- Create: `test/test_file_review_api.py`
- Modify: `api/db/services/file_review_service.py`
- Modify: `test/test_file_review_service.py`
- Modify: `agent/tools/file_review.py`

> **T6 → T9 交接契约（强制执行，来自 T6 executor 的两道审查实测结论）**
>
> 以下六条是 T6 实现完成后由质量复审**实测**得出的硬约束。不遵守会直接损坏数据或越权，不是风格建议。
>
> 1. **重试必须新建轮次，禁止把 `failed` 轮次原地重置为 `fixing` 重跑。** `_latest_version_name` 刻意排除当前轮自身，所以原地重跑会退回**原件**基线，只补剩余 pending 的补丁，再以同名对象 `frv-{task_id}-{file_version}` **覆盖**上一轮的成稿——上一批已置 `fixed` 的改动会从成稿里消失，而标注仍显示 `fixed`。实测复现：两轮改动 a/b，中途失败后原地重跑，成稿只剩 b 的修复。**必须走 `round_no` / `file_version` 递增的新轮次**，此时基线是已落盘版本，不丢。
> 2. **`status='failed'` 的轮次现在可能带 `minio_path`。** T6 修复轮顺序是「落盘 → 轮次收口 `done` → 置标注 `fixed`」，因此收口那一步抛错时轮次被兜底写成 `failed`，但成稿**已经落盘**（实测：`round=failed && minio_path=frv-…-v2 && summary=本轮修复 1 项`）。progress 端点与 T12 进度卡**不得**按「failed ⇒ 无成稿」渲染，否则会把已修好的成稿藏起来。判据是「有 `minio_path` 就有可下载成稿」，与 `status` 无关。
> 3. **轮号与版本号一律取 `FileReviewRoundService.next_round(task_id)`（T8 新增），禁止用 `max_completed_round_no`。** ~~本条原为「二选一待定」，已由 T8 实测结论取代。~~ 后者只数 `done`/`annotated`，而 `failed` 轮次也可能已经落盘（见第 2 条），复用它会出现三种实测后果：① 与失败轮同号 ⇒ 同名对象 `frv-{task_id}-{file_version}` **覆盖**上一轮已落盘的成稿；② `_latest_version_name` 会把同号的失败兄弟当成新轮自己的基线（自引用）⇒ 所有补丁 `find` 全失败；③ 面板出现两条同号轮次。`next_round` 数**全部** `round_no`（含 failed），已由 `test_next_round_counts_all_rounds_including_failed` 锁死。
> 4. **必须提供「open 但已成稿」的人工兜底出口。** 除上条窗口外还有一种残留：落盘成功、轮次收口成功，但逐条置 `fixed` 时该标注写入失败 → 标注永远停在 `open`，而文档已修好、`find` 已被替换，重跑必然报「0 项未能自动修复」，**不会自愈**。annotations 端点必须提供把标注人工置 `wontfix` / `resolved` 的能力，否则面板上永远挂着一个假未闭环项。
> 5. **`task_id` 归属校验（越权闸门）——一律用 `FileReviewRoundService.get_owned_task(task_id, tenant_id)`（T8 新增）。** `execute_task(task_id)` / `spawn_review_task(task_id)` / `_force_fail_round(task_id)` 全链路**只按 task_id 圈定，不含任何 tenant 谓词**（`round_row.tenant_id` 仅用于选 bucket，不参与鉴权），所以入口这一层是唯一防线。`get_owned_task` 取该 task 全部轮次，任一轮 tenant 不符、或一行都没有，都整体返回 `[]` ⇒ 拒绝，调用方无从区分「不存在」与「不是你的」。
> ~~原「必须在入口把 `task_id` 归一为 UUID」已作废~~：T8 论证并采纳了更简的口径——该白名单不增加任何安全（格式不符的 id 同样查不到行 ⇒ `[]` ⇒ 拒绝），却会在 id 生成方式变更时**静默拒绝全部合法请求**，拿一个更坏的失败模式换零收益。
> 6. **`error` 列的固定文案闸门只在 executor 兜底层。** 非 `FileReviewError` 的异常原文一律被替换成「服务端内部错误，请稍后重试（详见服务端日志）」（原文只进服务端日志），以避免 MySQL host:port / MinIO endpoint / 内网路径经 `error` 列透给前端。**T9 自行拼错误文案时同样不得把异常原文写进 `error` 列。**
> 7. **同一 task 的并发请求必须在 T9 补闸门（T8 显式遗留）。** T8 在 `_fix` 里加了 `spawn_mod.is_running(task_id)` 检查，防「spawn 静默 no-op → 孤儿 `fixing` 轮次永久卡死」（机理：`spawn_review_task` 命中 `_running_tasks` 即静默 return；`executor._run_fix_round` 先置轮次终态、再写最多 20 条标注，窗口宽达 20 次 DB 往返；`execute_task` 每次只处理 `rounds[-1]` 一轮且不循环；无看门狗）。但该检查的保证以**单写入者**为前提（对话内工具调用串行）。REST 端点一旦暴露给 HTTP 重试/双击，两个请求会同时观测 `is_running=False`、各自 `next_round()` 取到**同一轮号**（`(task_id, round_no)` 无唯一约束）、后到者的 spawn 静默 no-op ⇒ 又回到永久卡死。T9 必须显式决策并写进代码注释：**(a)** REST 层加同款 `is_running` 闸门 + 拒绝并发请求（简单，仍留 TOCTOU 残余）；或 **(b)** 改 `spawn_review_task` 返回 `bool`、注册失败时调用方回滚轮次（第一性更干净，消除 TOCTOU，但要改 `spawn.py`——属 T5 已审契约，**须先向用户确认**）。
> 8. **「按级别修复」只能经 `user_query` 软表达，且绝不自动置 `wontfix`。** executor 的 `chosen = pending[:MAX_FIX_ITEMS]` 没有 severity 过滤（T6 取舍），级别过滤靠 `round_row.user_query` 被 `_build_fix_prompt` 原样写进「用户需求：」生效。故 fix 端点必须：① 拼装基准用**首轮** `rounds[0].user_query`，不是上一轮——上一轮若是修复轮，其 `user_query` 里带着已作废的级别指令，叠加后 LLM 收到互斥要求（**T8 实测踩过此坑**）；② 拼装函数**提到 Service 层做唯一实现**（T8 现在 `agent/tools/file_review.py` 里的 `_compose_fix_query`；第二个调用方出现时才抽，符合 YAGNI——但抽的时候是**移动**，不是在 API 里复制一份，否则「只修X级」这句话会存在两份口径）；③ **绝不**把未选中级别的标注自动置 `wontfix`/`resolved`——`wontfix` 的语义是「用户决定永不修」，自动置位后用户改口「把中等的也修了」会静默失效，而用户没有任何办法从对话或面板上发现这件事（T8 的 `test_fix_never_mutates_annotation_status` 锁的就是这条）。
> 9. **默认模板 id 取 `rag.svr.file_review.executor.DEFAULT_TEMPLATE_ID`，不得在 API 里复刻 `'bid_doc_format'` 字面量。** 默认值改了 API 必须跟着变，复制一份就是等着漂移（T7 节点、T8 工具都按此处理）。
> 
> **第 7 条已决策：(a)** —— REST 层加 `spawn_mod.is_running(task_id)` 闸门，且 fix 端点内做**无 `await` 临界区**（见 Step 5）。`WS=1`（`docker/launch_backend_service.sh:36-38`）⇒ 单 webserver 进程，临界区内无挂起点 ⇒ 「检查 → next_round → create_round → spawn」之间不存在可交错点，TOCTOU 被消除，不需要改 `spawn.py`（T5 已审契约，不动）。**该性质由 `test_fix_critical_section_has_no_await` 的 AST 结构断言锁死**：日后谁在临界区里加一个 `await`，测试就红。


> ### T9 规格重写说明（前置侦察实测结论，替换原「7 端点 + SSE」版）
>
> 原规格是在没有核对真实 API 面之前写的，实测有 8 处硬伤。**执行前先读完本节**，它同时解释了「为什么端点从 7 个收缩到 4 个」。
>
> **原规格的硬伤（逐条已核实）**
>
> 1. `spawn_mod.spawn_review_task(rid)` —— 实参是 `round_id`。真实签名只吃 `task_id`（T7 节点、T8 工具都是 `spawn_review_task(task_id)`，`is_running(task_id)` 同理）。传 rid 会让 `_running_tasks` 记下一串没人再用的 round_id，防重入闸门**永久失效**。
> 2. `tenant_id='', created_by=''` —— 建轮次时把 tenant 写死为空串。后果有两层：① `get_owned_task(task_id, tenant)` 要求「任一轮 tenant 不符即拒绝」，自己写空串的轮次连自己都过不了闸门；② executor `_store_version_blob` 用 `f"{round_row.tenant_id}-downloads"` 选桶，空串会往 `-downloads` 桶里写，成稿**在任何用户那里都取不到**。
> 3. `if not FileReviewTemplateService.get_by_id(template_id): template_id = 'bid_doc_format'` —— 复刻了模板 id 字面量。默认值的唯一真相是 `rag.svr.file_review.executor.DEFAULT_TEMPLATE_ID`（T7/T8 都按此处理），复制一份就是等着漂移。
> 4. `max_completed_round_no(task_id)` 算轮号 —— 该口径已被 T6/T8 实测证伪（只数 done/annotated，而 failed 轮也可能已落盘 → 同号即同名对象覆盖 + 自引用基线 + 面板两条同号轮）。权威口径是 `next_round(task_id)`（数全部轮次，含 failed）。**本任务顺手删掉这个死方法**（见 Step 1）。
> 5. 无归属校验 —— `execute_task` / `spawn_review_task` / `_force_fail_round` 全链路只按 `task_id` 圈定、无 tenant 谓词，入口是唯一防线。原规格一个校验都没有。
> 6. SSE 端点永不终止：`if status in ('done','failed'): break`，而**主happy path 的终态是 `annotated`**（首轮审核完成），于是流永远不 break、每 2s 空转一次直到浏览器断开。另外 SSE 里混用了 `flask.Response` / `flask.request` / `stream_with_context`，而蓝图是 `quart.Blueprint`。
> 7. `blueprint = Blueprint(...)` —— 变量名必须是 `manager`（`api/apps/__init__.py:315` 的 `page.manager = Blueprint(...)`，自动注册靠的就是这个名字）。
> 8. 凭空发明的两个端点：`POST /annotation`（手动加批注 —— 手动批注在本项目已有既有归属：流程页存 `flow_comment`，对话页存在消息本地态。再造一张表/一条链路 = 与现有功能重复）与 `POST /finish`（把最后一轮直接写 `done` —— 会**覆盖**正在跑的 `reviewing`/`fixing` 轮次，线程回头还会写它自己的终态，一轮两个终态；而且「结束审核」这个动作本身就是多余的，见下）。

> **为什么砍掉 SSE（第一性论证）**
>
> executor 的粒度是「一轮一行状态」：`execute_task` 每次只处理 `rounds[-1]` 一轮，中途没有任何可上报的步进。也就是说一条 feature 级 SSE 每隔 2s 推的还是同一个状态 —— 比轮询更差的复杂度（多了长连接、心跳、断线重连、nginx 空闲超时四件事）而零信息增益。
>
> 更关键的是**本仓库没有「功能级 SSE」先例**：现有 SSE 全部挂在画布/agent 会话上（`agent_api._build_sse_response` + `canvas.py` 的 15s 心跳 + `GeneratorExit` 取消），进度类事件只是**搭车**在画布流里推。唯一的同族功能「范本填写」正是踩过「执行与连接耦合」的坑之后，才改成「后台线程 + 轮询 + 快照」的（见 `2026-09-12-template-fill-detached-task-design.md`）。T9 直接照最终形态做，不重走一遍那条路。
>
> 结论：**进度真相是 `file_review_round` 表，前端轮询**。这一条同时简化 T10/T11/T12（见本节末尾「对下游任务的影响」）。

> **为什么没有 start / finish 端点**
>
> - **start**：发起入口在 T7 画布节点与 T8 对话工具 —— 二者都已在各自运行时里拿着 `tenant_id` + 上传文件 id，且各自的调用者（画布 / LLM）本来就在那条路径上。再开一个 HTTP 入口，只是多一条**可以绕过它们、也绕过它们归属校验**的越权面。
> - **finish**：轮次上限已由 `fix_rounds_left` 兜住，不存在「必须显式收口」的状态；而写 `done` 的代价是可能覆盖运行中的轮次（见硬伤 8）。用户「不再修了」的表达方式就是不再点修复。

- [ ] **Step 1: Service 层收口**（三个改动：迁移唯一实现、加两个按文件查询、删死方法）

**1a. 把「只修 X 级」指令的唯一实现从工具层迁到 Service 层**（交接契约第 8 条）

在 `api/db/services/file_review_service.py` 的 `MAX_FIX_ROUNDS` / `fix_rounds_left` 之后、`_clamp_str` 之前插入：

```python
# ── 修复轮「只修 X 级」指令的唯一实现 ────────────────────────────────
# 级别过滤在 executor 里**只能软表达**：_run_fix_round 的 chosen = pending[:MAX_FIX_ITEMS]
# 没有 severity 谓词，LLM 收到的级别约束全部来自 round_row.user_query 被 _build_fix_prompt
# 原样写进「用户需求：」。所以这句中文措辞就是过滤机制本身，两个发起方（T8 对话工具 /
# T9 REST 端点）必须是同一份实现 —— 各写一份就是等着两处措辞漂移、行为分叉。
# 放在本层（而非某一层调用方）是因为两处都要 import 本模块，不新增任何依赖边。
SEVERITY_CN = {"high": "严重", "medium": "一般", "low": "提示"}


def compose_fix_query(base_query: str, levels: list) -> str:
    """把级别选择编进本轮 user_query。

    `base_query` 必须是**首轮原始需求**，不是上一轮：连轮修复时上一轮本身就是修复轮，
    其 user_query 里带着**上一轮**已作废的级别指令，拿它当基准会拼出「只修【严重】…」
    +「只修【一般】…」两条互相排斥的指令，LLM 同时收到后可能该修的不修、或越界改了
    用户本次没选中的级别——与本函数「按用户本次选定级别修复」的目标正好相反。
    （T8 实测踩过：test_fix_second_round_does_not_stack_level_directives 锁定该口径。）

    级别中英双写（如「严重/high」）：待修清单里每条写的是英文 `级别=high`（severity 已
    被 _norm_severity 归一成英文），只给中文会多出一层「严重 ⇔ high」的映射不确定性。

    刻意**不**把未选中级别的标注置 wontfix 来硬过滤：wontfix 的语义是「用户决定永不
    修复此条」，自动置位后用户改口「把中等的也修了」会静默失效（list_pending_by_task
    不再看到它），而用户没有任何办法从对话里发现这件事。

    levels 为空时只返回 base_query、不加任何指令：「修全部级别」这种假指令会让
    executor 修掉用户本次没要的范围。调用方应先用自己的白名单把空集挡在外面。
    """
    base = (base_query or "").strip()
    if not levels:
        return base
    chosen = "、".join(f"{SEVERITY_CN.get(s, s)}/{s}" for s in levels)
    head = f"本次只修复【{chosen}】级别的问题，其余级别的问题请保持原样、不要改动。"
    return f"{base}\n{head}" if base else head
```

同时修正本模块顶部 docstring 里「本层只做单条 INSERT/UPDATE/SELECT」的自我描述，补一句：`compose_fix_query` / `SEVERITY_CN` 是纯文本助手，与 `fix_rounds_left` 同属「不触库的判定逻辑」，落在本层是因为两个发起方都已 import 本模块。

**1b. 删掉被证伪的死方法**（硬伤 4）

在 `FileReviewRoundService` 中删除整个 `max_completed_round_no` 方法，以及模块顶部的 `COMPLETED_ROUND_STATUSES` 常量与它上面那段注释。删除后的既有调用点只有测试（见 1d）与文档字符串，一处不漏。

删完顺带修掉两处引用（**必须改**，否则它们会继续向读代码的人推荐这个被证伪的口径）：
- `next_round` docstring 里「刻意**不**复用 max_completed_round_no（只数 done/annotated）」→ 改为「刻意**不**用「只数 done/annotated」的口径」；
- `list_by_file_version` / `list_open_or_new_for_next_round` / `list_pending_by_task` docstring 里的「（同 max_completed_round_no）」→ 改为「（同本模块其余聚合方法的短路口径）」。

**1c. 加两个按文件查询**（面板只拿得到 `file_id`，见下）

在 `FileReviewRoundService.get_by_task` **之后**追加：

```python
    @classmethod
    @DB.connection_context()
    def get_by_file(cls, file_id: str) -> list:
        """该文件**最近一次审核任务**的全部轮次，按轮次号升序；从未审核返回 []。

        面板/进度卡只拿得到 file_id（对话与流程都以「上传的文件」为中心），task_id 是
        审核过程内部的编号 —— 让前端去「发现」它就得再开一条链路（节点输出 / 工具返回
        文本都不可靠：用户刷新一次就没了）。故按 file_id 反查 task_id：取该文件**最新
        的一行**轮次（跨任务按 create_time 取新），再用它的 task_id 取全轮次。

        「最新一行」的 tie-break 是 create_time（13 位毫秒）。同毫秒内插入的两行无法
        区分先后，调用方不应依赖绝对稳定的结果 —— 与 get_by_task 的次级排序同款取舍。

        刻意**不按 tenant_id 过滤**：读路径遵循本项目「文件所有人可见」的口径
        （docs/superpowers/specs/2026-09-09-remove-team-permission-design.md），且流程场景
        下轮次行的 tenant_id 是发起人/画布所有者的，按调用者过滤会让协作者看不到审核结果。
        写路径（T9 fix 端点）仍必须走 get_owned_task 严格校验（交接契约第 5 条）——
        读不限、写严格，是有意的不对称。

        脏数据兜底：最新一行没有 task_id（不该发生，列 NOT NULL）时返回 []，而不是拿
        空 task_id 去 get_by_task（那会把 task_id="" 的脏行当成「一个任务的全部轮次」）。
        """
        if not file_id:
            return []
        last = cls.model.select().where(
            cls.model.file_id == file_id
        ).order_by(cls.model.create_time.desc()).first()
        if last is None or not last.task_id:
            return []
        return cls.get_by_task(last.task_id)
```

在 `FileReviewAnnotationService.list_pending_by_task` **之后**追加：

```python
    @classmethod
    @DB.connection_context()
    def list_by_file(cls, file_id: str) -> list:
        """该文件的**全部**标注（跨轮次、跨版本、跨任务），按创建时间升序。

        面板必须看得到全部，**不能**按 file_version 过滤：只有审查轮产标注，而审查轮的
        file_version 恒为首轮版本（v1），修复轮只改文档、不产新标注，成稿是 v2/v3/v4 ——
        按「成稿版本」过滤会一条都查不到（T6 executor 实测：_persist_annotations 只在
        _run_review_round 里被调用，用的就是 `round_row.file_version`）。
        同一文件被重复审核（新 task）时也必须合并展示，否则用户看不到上一轮的批注。
        """
        if not file_id:
            return []
        return list(cls.model.select().where(
            cls.model.file_id == file_id
        ).order_by(cls.model.create_time.asc()))
```

**1d. 同步删掉死方法的测试**（`test/test_file_review_service.py`）

- 删整个 `test_max_completed_rounds_excludes_failed`；
- 删「2. max_completed_round_no 的对抗」整节（含 `test_max_completed_round_no_ignores_reviewing_fixing_and_failed` 与它上面的分节注释）；
- `test_lookup_misses_are_none_zero_and_empty_list` 里删掉 `max_completed_round_no(...) == 0` 那一行断言；
- `test_aggregates_short_circuit_on_empty_task_id` 里删掉两行 `max_completed_round_no` 断言（保留 `list_open_or_new_for_next_round` 的两行 —— 那两行仍在测活方法）；
- `test_round_queries_are_isolated_between_tasks`：删掉两行 `max_completed_round_no` 断言，**改成覆盖新方法 `get_by_file`**（该测试的主题正是「多 task 不串号」，`get_by_file` 是这条契约上最需要被锁的新成员）：

```python
def test_round_queries_are_isolated_between_tasks():
    ta, tb = f"{PFX}t_iso_a", f"{PFX}t_iso_b"
    with DB.connection_context():
        _mk_round(ta, 1, "done", file_id=f"{PFX}f_iso_a")
        _mk_round(ta, 2, "annotated", file_id=f"{PFX}f_iso_a", file_version="v2")
        _mk_round(tb, 4, "done", file_id=f"{PFX}f_iso_b", file_version="v4")
        assert [r.round_no for r in FileReviewRoundService.get_by_task(ta)] == [1, 2]
        assert [r.round_no for r in FileReviewRoundService.get_by_task(tb)] == [4]
        # get_by_file 只认自己文件的轮次，不会把另一个 task 的轮次并进来
        assert [r.round_no for r in FileReviewRoundService.get_by_file(f"{PFX}f_iso_a")] == [1, 2]
        assert [r.round_no for r in FileReviewRoundService.get_by_file(f"{PFX}f_iso_b")] == [4]
        assert FileReviewRoundService.get_by_file(f"{PFX}f_iso_ghost") == []
```

**1e. 补两个新方法的真实 DB 测试**（追加到 `test_file_review_service.py` 末尾）

```python
# ── 11. 按文件查询（T9 面板读模型的两个数据源） ────────────────────────

def test_get_by_file_returns_latest_task_rounds():
    """「最新一行」= create_time 最大者 → 用它的 task_id 取全轮次。

    create_time 是毫秒，两个 task 必须在**不同毫秒**建行，否则 tie-break 未定义；
    故这里显式 sleep 让两次写入落在不同毫秒（10ms ≫ 1ms 分辨率）。
    """
    import time
    fid = f"{PFX}f_byfile"
    with DB.connection_context():
        _mk_round(f"{PFX}t_byfile_old", 1, "done", file_id=fid)
        time.sleep(0.01)
        _mk_round(f"{PFX}t_byfile_new", 1, "annotated", file_id=fid)
        _mk_round(f"{PFX}t_byfile_new", 2, "done", file_id=fid, file_version="v2")
        rows = FileReviewRoundService.get_by_file(fid)
    assert [r.round_no for r in rows] == [1, 2], "必须是新 task 的两轮，不是旧 task 的一轮"
    assert rows[0].task_id == f"{PFX}t_byfile_new"


def test_get_by_file_empty_and_blank_are_empty_list():
    with DB.connection_context():
        assert FileReviewRoundService.get_by_file(f"{PFX}f_byfile_ghost") == []
        assert FileReviewRoundService.get_by_file("") == []


def test_get_by_file_is_not_tenant_scoped_locks_read_policy():
    """锁住「读不限、写严格」的不对称：读路径不按 tenant 过滤（文件所有人可见）。

    若有人给 get_by_file 加上 tenant 谓词，流程场景下协作者会看不到审核结果
    （轮次行的 tenant 是发起人/画布所有者的），这条用例会立刻红。
    """
    fid = f"{PFX}f_byfile_tenant"
    with DB.connection_context():
        _mk_round(f"{PFX}t_byfile_tenant", 1, "annotated", file_id=fid,
                  tenant_id="someone_else")
        rows = FileReviewRoundService.get_by_file(fid)
    assert [r.round_no for r in rows] == [1]


def test_list_by_file_spans_versions_and_tasks():
    """面板要看到文件的**全部**标注：跨版本、跨 task。"""
    fid = f"{PFX}f_annot_file"
    with DB.connection_context():
        r1 = _mk_round(f"{PFX}t_annot_a", 1, "annotated", file_id=fid)
        r2 = _mk_round(f"{PFX}t_annot_a", 2, "done", file_id=fid, file_version="v2")
        r3 = _mk_round(f"{PFX}t_annot_b", 1, "annotated", file_id=fid)
        _mk_ann(round_id=r1, task_id=f"{PFX}t_annot_a", file_id=fid,
                file_version="v1", status="fixed")
        _mk_ann(round_id=r2, task_id=f"{PFX}t_annot_a", file_id=fid,
                file_version="v2", status="open")
        _mk_ann(round_id=r3, task_id=f"{PFX}t_annot_b", file_id=fid,
                file_version="v1", status="open")
        # 另一个文件的标注不得混入
        r_other = _mk_round(f"{PFX}t_annot_other", 1, "annotated", file_id=f"{PFX}f_other")
        _mk_ann(round_id=r_other, task_id=f"{PFX}t_annot_other", file_id=f"{PFX}f_other")
        rows = FileReviewAnnotationService.list_by_file(fid)
    assert len(rows) == 3, f"三个版本三条都要返回，实际 {len(rows)}"
    assert {r.file_version for r in rows} == {"v1", "v2"}
    assert {r.task_id for r in rows} == {f"{PFX}t_annot_a", f"{PFX}t_annot_b"}


def test_list_by_file_empty_and_blank_are_empty_list():
    with DB.connection_context():
        assert FileReviewAnnotationService.list_by_file(f"{PFX}f_annot_ghost") == []
        assert FileReviewAnnotationService.list_by_file("") == []
```

> `_mk_round` / `_mk_ann` 是本文件既有的 helper；若它们的签名不含 `tenant_id`，在 `_mk_round` 上加一个 `tenant_id=""` 关键字参数并透传给 `create_round`（不要改其它参数与默认值）。

**1f. 跑测试 + 提交**

```bash
uv run --no-sync pytest test/test_file_review_service.py -v
```

Expected: PASS（原 30+ 例去掉 3 例死方法用例、新增 5 例 → 全绿）

```bash
git add api/db/services/file_review_service.py test/test_file_review_service.py
git commit -m "refactor(file-review): compose_fix_query 迁至 Service 层 + get_by_file/list_by_file + 删被证伪的 max_completed_round_no"
```

- [ ] **Step 2: 工具侧改为复用唯一实现**

在 `agent/tools/file_review.py`：

1. 删除 `_compose_fix_query` 整个函数（含 docstring）。
2. 把 `_SEVERITY_CN = {...}` 换成从 Service 层导入（共 4 处使用点不变，见下）：
   ```python
   from api.db.services.file_review_service import SEVERITY_CN as _SEVERITY_CN
   ```
   （放在该文件既有的 `api.db.services.file_review_service` import 块里，**不要**新增 import 语句块。）
3. 调用点 `user_query=_compose_fix_query(rounds[0].user_query, levels)` → 改为 `compose_fix_query(...)`，并把该 import 一并加进同一个块。
4. 修正第 41-43 行那段注释：它写着「前两者 = Service 层 COMPLETED_ROUND_STATUSES」，而该常量已删。改为：

```python
# 轮次终态：annotated（首轮审核收口）/ done（修复轮收口）/ failed。
# 工具要能对用户如实说「这轮失败了」，故不能像 Service 的完成口径那样把 failed 当作
# 「没发生」—— 但**不许**回头去引 Service 的完成口径常量（它已随 max_completed_round_no
# 一并删除，见 T9），这里就是权威定义。
```

**注意**：`_severity_cn()`（None/空串 → 「未知」、未知串原样透出）**留在本模块**，不要一起迁走 —— 它的职责是「渲染给用户/LLM 的文案不许出现字面 None」，与 `compose_fix_query` 的「编指令」不是一回事，API 侧不需要它。

```bash
uv run --no-sync pytest test/test_file_review_tool.py -v
```

Expected: PASS（37 例，与改动前一致 —— 本步只换实现位置、不改行为）

```bash
git add agent/tools/file_review.py
git commit -m "refactor(file-review): 工具复用 Service 层 compose_fix_query，去掉重复实现"
```

- [ ] **Step 3: 写失败测试**

```python
# test/test_file_review_api.py
"""文件审核 REST API 测试（T9）。

不依赖 Redis/ES/DB：照 test_template_api_routes.py / test_flow_ai_record_update.py 的模式，
从源文件加载 file_review_api.py 并注入 `api.apps` 最小桩（login_required 透传 +
current_user），端点体内的 Service 调用一律 monkeypatch。

端点走**真实 Quart 请求上下文**（app.test_request_context），因此 body 解析、装饰器注入
tenant_id、路由分发都是真的；只有触库/起线程那两层被替换掉。
"""
import ast
import asyncio
import os
import sys
import types
from importlib.util import module_from_spec, spec_from_file_location
from types import SimpleNamespace

import pytest
from quart import Quart

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

_API_PATH = os.path.abspath(os.path.join(
    os.path.dirname(__file__), "..", "api", "apps", "restful_apis", "file_review_api.py"))

TENANT = "u1"


def _noop_decorator(f=None, *a, **kw):
    """login_required 透传桩：必须原样返回被装饰函数（返回内部 deco 会丢函数）。"""
    return f


def _load_api():
    stub = types.ModuleType("api.apps")
    stub.current_user = SimpleNamespace(id=TENANT)
    stub.login_required = _noop_decorator
    sys.modules["api.apps"] = stub
    spec = spec_from_file_location("file_review_api_under_test", _API_PATH)
    mod = module_from_spec(spec)
    sys.modules["file_review_api_under_test"] = mod
    spec.loader.exec_module(mod)
    return mod


_api = _load_api()

APP = Quart(__name__)
APP.register_blueprint(_api.manager)


def _call(endpoint, *, method="POST", path="/", body=None, **kwargs):
    """在真实请求上下文里调端点，返回响应的业务体（dict）。

    注意 endpoint 被真实的 add_tenant_id_to_kwargs 包着，其 wrapper 签名是
    `(**kwargs)` —— 调用时**必须**全部用关键字传参（传位置参数会被丢进 `*args`、
    进而漏掉 task_id 等路径参数）。
    Quart 的 Response.get_json() 是协程，故整体包在 asyncio.run 里。
    """
    async def _inner():
        async with APP.test_request_context(path, method=method, json=body):
            resp = await endpoint(**kwargs)
        return await resp.get_json()
    return asyncio.run(_inner())


def _round(round_no, status, **over):
    row = SimpleNamespace(
        id=f"r{round_no}", task_id="t1", file_id="f1", round_no=round_no,
        template_id="bid_doc_format", user_query="审核这份招标文件",
        status=status, file_version=f"v{round_no}", kb_ids=None,
        minio_path=None, summary="", llm_raw=None, error="",
        tenant_id=TENANT, created_by=TENANT)
    for k, v in over.items():
        setattr(row, k, v)
    return row


def _ann(aid, **over):
    row = SimpleNamespace(
        id=aid, round_id="r1", task_id="t1", file_id="f1", file_version="v1",
        anchor='{"p_idx": 3}', matched_text="投标人须", type="clause",
        severity="high", issue="缺少投标保证金条款", suggestion="补一条",
        source="ai", status="open", prev_annotation_id=None, tenant_id=TENANT)
    for k, v in over.items():
        setattr(row, k, v)
    return row


def _patch_services(monkeypatch, *, rounds=(), annotations=(), pending=(),
                    owned=None, running=False, next_no=None):
    """替换端点用到的全部 Service / spawn 入口，返回被捕获的调用记录。"""
    calls = {}
    owned_rounds = list(rounds) if owned is None else list(owned)

    monkeypatch.setattr(_api.FileReviewRoundService, "get_by_file",
                        classmethod(lambda cls, fid: list(rounds)))
    monkeypatch.setattr(_api.FileReviewAnnotationService, "list_by_file",
                        classmethod(lambda cls, fid: list(annotations)))
    monkeypatch.setattr(_api.FileReviewRoundService, "get_owned_task",
                        classmethod(lambda cls, tid, tenant: owned_rounds))
    monkeypatch.setattr(_api.FileReviewAnnotationService, "list_pending_by_task",
                        classmethod(lambda cls, tid: list(pending)))
    monkeypatch.setattr(_api.FileReviewRoundService, "next_round",
                        classmethod(lambda cls, tid: next_no))
    monkeypatch.setattr(_api.FileReviewAnnotationService, "get_by_id",
                        classmethod(lambda cls, aid: calls.get("_ann_row")))
    monkeypatch.setattr(_api.FileReviewAnnotationService, "update_status",
                        classmethod(lambda cls, aid, status:
                                    calls.setdefault("updated", []).append((aid, status)) or True))
    monkeypatch.setattr(
        _api.FileReviewRoundService, "create_round",
        classmethod(lambda cls, **kw: calls.update({"created": kw}) or "rid-new"))
    monkeypatch.setattr(_api.spawn_mod, "spawn_review_task",
                        lambda tid: calls.setdefault("spawned", []).append(tid))
    monkeypatch.setattr(_api.spawn_mod, "is_running", lambda tid: running)
    return calls
```

- [ ] **Step 4: 跑测试确认失败** — `uv run --no-sync pytest test/test_file_review_api.py -v`
      期望：`FileNotFoundError`（`file_review_api.py` 还不存在）

- [ ] **Step 5: 实现 `api/apps/restful_apis/file_review_api.py`**

```python
#
#  Copyright 2026 The InfiniFlow Authors. All Rights Reserved.
#
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.
#
"""文件审核 REST API（4 端点）。

  GET  /file/review/templates                      可用审核模板（系统预置 + 本租户）
  GET  /file/review/file/<file_id>/state            以文件为中心的权威读模型
  POST /file/review/<task_id>/fix                   发起一轮修复
  POST /file/review/annotation/<aid>/status          人工闭环单条标注（兜底出口）

── 设计取舍（T9 前置侦察实测结论，改前请先读 Task 9 的「规格重写说明」）──────

* **不做进度 SSE。** executor 每轮只写一行最终状态、没有步进粒度，SSE 每 2s 推的还是
  同一个状态；本仓库现有 SSE 全部挂在画布/agent 会话上，没有「功能级 SSE」先例。最接近
  的同族功能（范本填写）也是踩过「执行与连接耦合」的坑之后才改成「后台线程 + 轮询 + 快照」。
  进度真相是 file_review_round 表，前端轮询。
* **没有 start / finish 端点。** 发起入口在 T7 画布节点与 T8 对话工具（都已在运行时里
  拿着 tenant_id + 文件 id），再开 HTTP 入口只是多一条能绕过它们的越权面。finish 更危险：
  把最后一轮写 done 会覆盖正在跑的 reviewing/fixing 轮次，线程回头还会写自己的终态。
* **读不限、写严格**（有意的不对称）：state 不按 tenant 过滤（对齐「文件所有人可见」口径，
  也让流程协作者看得到审核结果）；fix / annotation-status 必须 get_owned_task 严格校验 ——
  因为 execute_task / spawn_review_task / _force_fail_round 全链路只按 task_id 圈定、
  **不含任何 tenant 谓词**，本层是唯一防线（T6 → T9 交接契约第 5 条）。
* 本模块**不返回**成稿的下载 URL：doc.object 是对象名（或原件 file_id），前端沿用既有
  GET /api/v1/files/<id> 取 blob —— 该路由对 `{tenant}-downloads` 桶有兜底、并按 zip 魔数
  识别 docx，审核面板的保真渲染本来就依赖它。
"""
import json
import logging

from quart import Blueprint, request

from api.apps import login_required
from api.db.services.file_review_service import (
    MAX_FIX_ROUNDS,
    FileReviewAnnotationService,
    FileReviewRoundService,
    FileReviewTemplateService,
    compose_fix_query,
    fix_rounds_left,
)
from api.utils.api_utils import (
    add_tenant_id_to_kwargs,
    get_error_argument_result,
    get_error_data_result,
    get_json_result,
)
from common.constants import RetCode
from rag.svr.file_review import spawn as spawn_mod

logger = logging.getLogger(__name__)

manager = Blueprint("file_review_api", __name__)

# 轮次状态口径与 T6 executor 一致（取值见 db_models.FileReviewRound.status 注释）。
# 只列「线程还会回来写这一行」的两态：它们与 spawn 的 _running_tasks 是同一件事的两个视角，
# 是 fix 端点必须拒的两个前置条件。
_RUNNING_ROUND_STATUSES = ("reviewing", "fixing")

# 级别白名单：与 T1 预置模板 / T6 _norm_severity 的输出一致。
_SEVERITY_WHITELIST = ("high", "medium", "low")

# 人工可置的标注状态。**排除 fixed**：那是 executor 在补丁真正落地后写的派生结论，
# 手置会让「文档改了没改」与面板状态失去对应关系。排除 new（下一轮的中间态）。
_MANUAL_ANNOTATION_STATUSES = ("open", "resolved", "wontfix")


def _json_list(raw) -> list:
    """把 TEXT 列里的 JSON 数组还原成 list；非 list / 坏 JSON / 空一律降级 []。

    脏值只影响前端下拉候选项，不值得为它让整条端点 500。
    """
    if not raw:
        return []
    try:
        val = json.loads(raw)
    except (TypeError, ValueError):
        return []
    return val if isinstance(val, list) else []


def _json_dict(raw) -> dict:
    """把 anchor 这类 JSON 对象列还原成 dict；脏值降级 {}（=「无定位信息」）。

    前端对 anchor == {} 有既有降级路径（退到 matched_text 全文匹配），故这里降级是安全的；
    整条端点因为一行脏 anchor 挂掉则不是。
    """
    if not raw:
        return {}
    try:
        val = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return val if isinstance(val, dict) else {}


def _template_payload(row) -> dict:
    return {
        "id": row.id,
        "name": row.name,
        "description": row.description or "",
        "annotation_types": _json_list(row.annotation_types),
    }


def _round_payload(row) -> dict:
    return {
        "id": row.id,
        "round_no": row.round_no,
        "status": row.status,
        "file_version": row.file_version,
        "template_id": row.template_id or "",
        "user_query": row.user_query or "",
        "summary": row.summary or "",
        "error": row.error or "",
        "minio_path": row.minio_path or "",
        # 判据是「有对象名」而不是「状态是 done」：T6 的修复轮先落盘、后收口，收口那一步
        # 抛错时轮次被兜底写成 failed，而成稿**已经落盘**（交接契约第 2 条）。按状态判会把
        # 已修好的成稿藏起来。
        "produced": bool(row.minio_path),
    }


def _doc_payload(rounds: list, file_id: str) -> dict:
    """该展示的文档：最近一次落盘的成稿；从未落盘时退回原件。

    显式选 round_no 最大且带 minio_path 的轮次（不依赖入参顺序），与 T6
    _latest_version_name 的基线口径一致 —— 面板必须展示**最后一版**，否则用户看的是中间稿、
    批注却来自最终轮。
    """
    best = None
    for r in rounds:
        if not r.minio_path:
            continue
        if best is None or (r.round_no or 0) >= (best.round_no or 0):
            best = r
    if best is None:
        # 还没有成稿：用户看的就是原件。version 留空串而不是 "v1" ——
        # 「首轮版本号 = v1」是 T7/T8 的命名习惯，不是本层的契约，不该由这里替前端断言。
        return {"object": file_id, "version": ""}
    return {"object": best.minio_path, "version": best.file_version}


def _annotation_payload(row) -> dict:
    return {
        "id": row.id,
        "round_id": row.round_id,
        "task_id": row.task_id,
        "file_id": row.file_id,
        "file_version": row.file_version,
        "type": row.type,
        "severity": row.severity,
        "issue": row.issue,
        "suggestion": row.suggestion or "",
        "matched_text": row.matched_text or "",
        "source": row.source,
        "status": row.status,
        "prev_annotation_id": row.prev_annotation_id or "",
        "anchor": _json_dict(row.anchor),
    }


def _count_annotations(rows) -> dict:
    """面板头部计数。pending 与 executor 的取数口径一致（open/new），fixed 单列。

    这里只做展示、不参与任何判定，故不 import Service 的 PENDING_ANNOTATION_STATUSES ——
    展示口径与「下一轮要修什么」的取数口径是两件事，绑在一起会让其中一方的调整被迫同步。
    """
    counts = {"total": len(rows), "high": 0, "medium": 0, "low": 0,
              "pending": 0, "fixed": 0}
    for a in rows:
        if a.severity in _SEVERITY_WHITELIST:
            counts[a.severity] += 1
        if a.status in ("open", "new"):
            counts["pending"] += 1
        elif a.status == "fixed":
            counts["fixed"] += 1
    return counts


def _parse_levels(raw) -> list | None:
    """归一 levels 入参：小写化、去重、保序。**任一项非法即整批拒绝**（返回 None）。

    不静默丢弃非法项：level 会被 compose_fix_query 拼成给 LLM 的中文指令，悄悄吞掉一个
    "critical" 会让用户以为「严重级别已纳入本次修复」，而实际指令里根本没有它 ——
    这种失败没有任何回执。
    空输入同样返回 None（而非 []）：调用方拿 None 与 [] 走同一条「参数错误」分支，
    避免出现「修了 0 个级别但轮次照样建」的空转。
    """
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, (list, tuple)) or not raw:
        return None
    out = []
    for item in raw:
        val = str(item or "").strip().lower()
        if val not in _SEVERITY_WHITELIST:
            return None
        if val not in out:
            out.append(val)
    return out or None


def _fix_base_query(rounds: list, extra: str = "") -> str:
    """修复轮的 user_query 基准 = **首轮**原始需求（可选追加本次补充说明）。

    基准只能取首轮：修复轮的 user_query 里已经带着上一轮写进去的级别指令，拿它当基准会把
    历轮指令叠起来（见 compose_fix_query 的 docstring，T8 实测踩过）。
    """
    base = (rounds[0].user_query or "").strip() if rounds else ""
    extra = (extra or "").strip()
    if not extra:
        return base
    return f"{base}\n本次补充要求：{extra}" if base else f"本次补充要求：{extra}"


@manager.route("/file/review/templates", methods=["GET"])  # noqa: F821
@login_required
@add_tenant_id_to_kwargs
async def list_review_templates(tenant_id: str = None):
    """可用审核模板：系统预置（tenant_id == ""）+ 本租户自有，且 enabled == 1。"""
    try:
        rows = FileReviewTemplateService.list_enabled(tenant_id or "")
        return get_json_result(data={"templates": [_template_payload(r) for r in rows]})
    except Exception:
        logger.exception("file review: list templates failed")
        return get_error_data_result(message="Internal server error")


@manager.route("/file/review/file/<file_id>/state", methods=["GET"])  # noqa: F821
@login_required
async def review_state(file_id: str):
    """以文件为中心的权威读模型：前端只凭 file_id 就能渲染进度与批注。

    doc.object = 该展示的文档对象名（最近一次落盘的成稿；无成稿时为原件 file_id），
    前端沿用既有 GET /api/v1/files/<id>。
    annotations = 该文件**全部**标注（跨轮次/版本/任务）—— 不能按成稿版本过滤：只有审查轮
    产标注且其 file_version 恒为首轮版本，成稿是 v2/v3/v4，按成稿版本过滤会一条都查不到。

    不按 tenant 过滤（读路径，见模块 docstring 的「读不限、写严格」）。
    """
    try:
        rounds = FileReviewRoundService.get_by_file(file_id)
        anns = FileReviewAnnotationService.list_by_file(file_id)
        return get_json_result(data={
            "file_id": file_id,
            "task_id": rounds[-1].task_id if rounds else None,
            "rounds": [_round_payload(r) for r in rounds],
            "current": _round_payload(rounds[-1]) if rounds else None,
            "doc": _doc_payload(rounds, file_id),
            "annotations": [_annotation_payload(a) for a in anns],
            "annotation_counts": _count_annotations(anns),
            "fix_rounds_left": fix_rounds_left(rounds),
            "max_fix_rounds": MAX_FIX_ROUNDS,
        })
    except Exception:
        logger.exception("file review: load state failed, file_id=%s", file_id)
        return get_error_data_result(message="Internal server error")


@manager.route("/file/review/<task_id>/fix", methods=["POST"])  # noqa: F821
@login_required
@add_tenant_id_to_kwargs
async def fix_review(task_id: str, tenant_id: str = None):
    """发起一轮修复。body: {"levels": ["high","medium"], "user_query": 可选补充说明}"""
    try:
        body = await request.get_json(silent=True) or {}
        levels = _parse_levels(body.get("levels"))
        if not levels:
            return get_error_argument_result(
                "levels 必须是非空数组，取值只能是 high / medium / low")

        # ══ 临界区开始：到 spawn_review_task 之前**不许出现任何 await** ══════════
        # 这段是「校验 — 定轮号 — 建轮次 — 起线程」的互斥边界。quart 是单进程单事件循环
        # （WS 默认 1，见 docker/launch_backend_service.sh），只要区内没有 await，另一个请求
        # 就插不进来。没有这道边界会怎样：两个并发 fix 各自观测到 is_running=False、
        # next_round() 取到**同一轮号**（(task_id, round_no) 无唯一约束），后到者的
        # spawn_review_task 命中 _running_tasks 后**静默 return**，于是留下一条永远没人消费的
        # fixing 轮次 —— 该 task 之后所有 fix/review 会被下面这些闸门永久挡死
        # （T6/T8 实测的必死路径）。区内要加 await，请先把 await 挪到临界区之外。
        rounds = FileReviewRoundService.get_owned_task(task_id, tenant_id or "")
        if not rounds:
            # 「不存在」与「不是你的」共用同一句文案：区分会把「他人 task 是否存在」
            # 这一信息透给攻击者（get_owned_task 的 docstring 同款口径）。
            return get_error_data_result("文件审核任务不存在或无权访问")
        cur = rounds[-1]
        if cur.status in _RUNNING_ROUND_STATUSES:
            return get_json_result(code=RetCode.OPERATING_ERROR,
                                   message="当前轮次仍在进行中，请等它结束后再发起修复")
        if spawn_mod.is_running(task_id):
            # 轮次行可能**已经**是终态而线程还在收尾：executor._run_fix_round 先置轮次终态、
            # 再逐条写最多 MAX_FIX_ITEMS(20) 条标注，中间隔着 20 次 DB 往返。此刻
            # spawn_review_task 会静默 no-op，新轮次永远等不到线程去消费它（T8 实测）。
            return get_json_result(code=RetCode.OPERATING_ERROR,
                                   message="上一轮审核正在收尾，请稍等片刻后重试")
        left = fix_rounds_left(rounds)
        if left <= 0:
            return get_json_result(
                code=RetCode.OPERATING_ERROR,
                message=f"已达到最大修复轮次（{MAX_FIX_ROUNDS} 轮），未修复的问题请按批注手动处理")

        pending = FileReviewAnnotationService.list_pending_by_task(task_id)
        if not [a for a in pending if a.severity in levels]:
            # 所选级别没有待修项时必须拦下：executor 的 chosen 不带 severity 谓词，
            # 建出来的轮次会去修**别的**级别（把用户没选中的问题改掉），或者空转一轮
            # 白白烧掉一次重试机会。
            return get_json_result(code=RetCode.OPERATING_ERROR,
                                   message="所选级别没有待修复的问题，无需发起修复")

        no, version = FileReviewRoundService.next_round(task_id)
        rid = FileReviewRoundService.create_round(
            task_id=task_id,
            file_id=cur.file_id,
            round_no=no,
            template_id=cur.template_id,
            # 基准取首轮原始需求，不用上一轮（见 _fix_base_query 的说明）。
            user_query=compose_fix_query(
                _fix_base_query(rounds, body.get("user_query")), levels),
            file_version=version,
            status="fixing",
            # tenant 必须来自当前用户，不能留空：get_owned_task 要求「任一轮 tenant 不符
            # 即拒绝」，写空串的轮次连自己都过不了闸门；且 executor 用 `{tenant}-downloads`
            # 选桶，空串会让成稿落进 `-downloads` 桶、谁都取不到。
            tenant_id=tenant_id or "",
            created_by=tenant_id or "",
            # 继承本轮 KB 配置：修复轮不做检索，但 kb_ids 是「这一轮用了哪些知识库」的
            # 可追溯配置，留空会让它无从知晓。
            kb_ids=cur.kb_ids,
        )
        spawn_mod.spawn_review_task(task_id)
        # ══ 临界区结束 ══════════════════════════════════════════════════════
        return get_json_result(data={"task_id": task_id, "round_id": rid, "round_no": no,
                                     "status": "fixing", "fix_rounds_left": left - 1})
    except Exception:
        logger.exception("file review: start fix round failed, task_id=%s", task_id)
        return get_error_data_result(message="Internal server error")


@manager.route("/file/review/annotation/<annotation_id>/status", methods=["POST"])  # noqa: F821
@login_required
@add_tenant_id_to_kwargs
async def update_annotation_status(annotation_id: str, tenant_id: str = None):
    """人工把单条标注置为 open / resolved / wontfix（交接契约第 4 条的人工兜底出口）。

    为什么必须有：修复轮可能「成稿落盘成功、轮次收口成功，但逐条置 fixed 时某条标注写入
    失败」—— 这条标注就永远停在 open，而文档已经改好、find 已被替换，再跑修复只会报
    「0 项未能自动修复」，**没有任何自动路径能自愈**。没有这个出口，面板上会永远挂着一个
    假未闭环项。
    """
    try:
        body = await request.get_json(silent=True) or {}
        status = (body.get("status") or "").strip().lower()
        if status not in _MANUAL_ANNOTATION_STATUSES:
            return get_error_argument_result(
                "status 只能是 open / resolved / wontfix（fixed 由服务端在修复落地后写入，不接受人工设置）")
        row = FileReviewAnnotationService.get_by_id(annotation_id)
        if not row:
            return get_error_data_result("批注不存在或无权访问")
        # 写路径严格校验：标注 → 所属 task → 轮次归属。用 get_owned_task 而不是直接比
        # row.tenant_id，是因为后者的可见范围与轮次不完全一致（标注行的 tenant 为空而轮次行
        # 有 tenant 的历史脏数据），而权限必须按「这条标注所属的审核任务」判。
        if not FileReviewRoundService.get_owned_task(row.task_id, tenant_id or ""):
            return get_error_data_result("批注不存在或无权访问")
        if not FileReviewAnnotationService.update_status(annotation_id, status):
            # 上面刚查到行，这里再失败只可能是并发删除：同样按「不存在」回，不泄露时序差异。
            return get_error_data_result("批注不存在或无权访问")
        return get_json_result(data={"annotation_id": annotation_id, "status": status})
    except Exception:
        logger.exception("file review: update annotation status failed, aid=%s", annotation_id)
        return get_error_data_result(message="Internal server error")
```

- [ ] **Step 6: 补端点行为测试**（追加到 `test/test_file_review_api.py`）

```python
# ── 端点接线（不依赖 DB，靠真实 Quart 路由表） ────────────────────────

def test_module_exposes_manager_blueprint_and_async_endpoints():
    """`manager` 变量名是自动注册的唯一契约（api/apps/__init__.py:315）。"""
    import inspect
    assert _api.manager.name == "file_review_api"
    for name in ("list_review_templates", "review_state", "fix_review",
                 "update_annotation_status"):
        fn = getattr(_api, name, None)
        assert fn is not None, f"缺少端点函数 {name}"
        assert inspect.iscoroutinefunction(getattr(fn, "__wrapped__", fn)) or callable(fn)


def test_all_routes_registered_on_blueprint():
    rules = {}
    for r in APP.url_map.iter_rules():
        if r.rule == "/static/<path:filename>":
            continue
        rules.setdefault(r.rule, set()).update(r.methods)
    assert rules == {
        "/file/review/templates": {"GET", "HEAD", "OPTIONS"},
        "/file/review/file/<file_id>/state": {"GET", "HEAD", "OPTIONS"},
        "/file/review/<task_id>/fix": {"POST", "OPTIONS"},
        "/file/review/annotation/<annotation_id>/status": {"POST", "OPTIONS"},
    }, f"路由集合不符：{rules}"


def test_fix_critical_section_has_no_await():
    """结构性锁死临界区：get_owned_task → spawn 之间不许有 await。

    quart 单进程单事件循环下，「区内无 await」就是「检查—定轮号—建轮次—起线程」的互斥
    边界；一旦有人塞进 await，并发 fix 会各自建出同号轮次、留下永远没人消费的 fixing 轮次，
    该 task 之后所有 fix/review 被闸门永久挡死。普通用例测不到这个（要构造真并发），
    故用 AST 在代码层断言。
    """
    tree = ast.parse(open(_API_PATH, encoding="utf-8").read())
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.AsyncFunctionDef) and n.name == "fix_review")
    body = next(s for s in fn.body if isinstance(s, ast.Try)).body
    start = next(i for i, s in enumerate(body)
                 if "get_owned_task" in ast.dump(s))
    end = next(i for i, s in enumerate(body)
               if "spawn_review_task" in ast.dump(s))
    assert start < end, "临界区起止顺序异常（get_owned_task 应在 spawn 之前）"
    for stmt in body[start + 1:end]:
        assert not any(isinstance(n, ast.Await) for n in ast.walk(stmt)), (
            f"临界区内出现 await，破坏 fix 的并发互斥：{ast.dump(stmt)[:200]}")
```

```python
# ── 纯函数：脏值与边界 ───────────────────────────────────────────────

def test_json_list_and_dict_degrade_on_dirty_input():
    assert _api._json_list(None) == []
    assert _api._json_list("") == []
    assert _api._json_list("not-json") == []
    assert _api._json_list('{"a": 1}') == []          # 是 JSON 但不是数组
    assert _api._json_list('["format"]') == ["format"]
    assert _api._json_dict(None) == {}
    assert _api._json_dict("[]") == {}                 # 是 JSON 但不是对象
    assert _api._json_dict("{bad") == {}
    assert _api._json_dict('{"p_idx": 3}') == {"p_idx": 3}


def test_parse_levels_rejects_whole_batch_on_any_invalid_item():
    f = _api._parse_levels
    assert f(["high", "medium"]) == ["high", "medium"]
    assert f("HIGH") == ["high"]                       # 标量字符串也接
    assert f(["high", "high", "low"]) == ["high", "low"]   # 去重保序
    assert f(["high", "critical"]) is None             # 整批拒绝，不静默丢弃
    assert f(["CRITICAL"]) is None
    assert f([]) is None
    assert f(None) is None
    assert f({"high": 1}) is None
    assert f([None]) is None
    assert f([" high "]) == ["high"]                   # 前后空白归一


def test_fix_base_query_uses_first_round_and_appends_extra():
    rounds = [_round(1, "annotated", user_query="按招标文件要求审核"),
              _round(2, "done", user_query="本次只修复【严重/high】级别的问题")]
    assert _api._fix_base_query(rounds) == "按招标文件要求审核"
    assert _api._fix_base_query(rounds, "重点看保证金") == \
        "按招标文件要求审核\n本次补充要求：重点看保证金"
    assert _api._fix_base_query([], "只这一句") == "本次补充要求：只这一句"
    assert _api._fix_base_query([]) == ""


def test_doc_payload_picks_latest_produced_round():
    rounds = [_round(1, "annotated"),
              _round(2, "done", minio_path="frv-t1-v2"),
              _round(3, "failed", minio_path="frv-t1-v3")]
    assert _api._doc_payload(rounds, "f1") == {"object": "frv-t1-v3", "version": "v3"}
    # 乱序入参也要选 round_no 最大的那一版（不依赖调用方排序）
    assert _api._doc_payload(list(reversed(rounds)), "f1") == \
        {"object": "frv-t1-v3", "version": "v3"}
    # 无产物 → 原件，且 version 留空（不替前端断言「原件就是 v1」）
    assert _api._doc_payload([_round(1, "reviewing")], "f1") == {"object": "f1", "version": ""}
    assert _api._doc_payload([], "f1") == {"object": "f1", "version": ""}


def test_round_payload_produced_follows_minio_path_not_status():
    """failed 轮也可能已落盘（T6 交接契约第 2 条）：按状态判会把成稿藏起来。"""
    assert _api._round_payload(_round(2, "failed", minio_path="frv-t1-v2"))["produced"] is True
    assert _api._round_payload(_round(2, "done"))["produced"] is False


def test_count_annotations_buckets():
    rows = [_ann("a1", severity="high", status="open"),
            _ann("a2", severity="high", status="fixed"),
            _ann("a3", severity="medium", status="new"),
            _ann("a4", severity="low", status="wontfix"),
            _ann("a5", severity="weird", status="resolved")]
    assert _api._count_annotations(rows) == {
        "total": 5, "high": 2, "medium": 1, "low": 1, "pending": 2, "fixed": 1}
    assert _api._count_annotations([]) == {
        "total": 0, "high": 0, "medium": 0, "low": 0, "pending": 0, "fixed": 0}
```

```python
# ── GET /file/review/templates ──────────────────────────────────────

def test_templates_endpoint_decodes_annotation_types(monkeypatch):
    rows = [SimpleNamespace(id="bid_doc_format", name="招标文件格式审核",
                            description=None, annotation_types='["format","clause"]'),
            SimpleNamespace(id="bad", name="脏数据", description="x",
                            annotation_types="{not json")]
    monkeypatch.setattr(_api.FileReviewTemplateService, "list_enabled",
                        classmethod(lambda cls, tenant: rows))
    body = _call(_api.list_review_templates, method="GET")
    assert body["code"] == 0
    assert body["data"]["templates"][0] == {
        "id": "bid_doc_format", "name": "招标文件格式审核",
        "description": "", "annotation_types": ["format", "clause"]}
    assert body["data"]["templates"][1]["annotation_types"] == []
```

```python
# ── GET /file/review/file/<file_id>/state ───────────────────────────

def test_state_endpoint_shape_when_never_reviewed(monkeypatch):
    _patch_services(monkeypatch)
    body = _call(_api.review_state, method="GET", file_id="f1")
    assert body["code"] == 0
    d = body["data"]
    assert d["task_id"] is None
    assert d["current"] is None
    assert d["rounds"] == []
    assert d["doc"] == {"object": "f1", "version": ""}
    assert d["annotations"] == []
    assert d["fix_rounds_left"] == 3 and d["max_fix_rounds"] == 3
    assert d["annotation_counts"]["total"] == 0


def test_state_endpoint_assembles_rounds_annotations_and_doc(monkeypatch):
    rounds = [_round(1, "annotated", summary="high:1"),
              _round(2, "done", summary="本轮修复 1 项", minio_path="frv-t1-v2")]
    _patch_services(monkeypatch, rounds=rounds,
                    annotations=[_ann("a1"), _ann("a2", status="fixed")])
    body = _call(_api.review_state, method="GET", file_id="f1")
    d = body["data"]
    assert d["task_id"] == "t1"
    assert [r["round_no"] for r in d["rounds"]] == [1, 2]
    assert d["current"]["round_no"] == 2 and d["current"]["produced"] is True
    assert d["doc"] == {"object": "frv-t1-v2", "version": "v2"}
    assert d["fix_rounds_left"] == 2          # 已发生 1 个修复轮
    assert len(d["annotations"]) == 2
    assert d["annotation_counts"]["pending"] == 1


def test_state_endpoint_hides_internal_error_text(monkeypatch):
    monkeypatch.setattr(_api.FileReviewRoundService, "get_by_file",
                        classmethod(lambda cls, fid: (_ for _ in ()).throw(
                            RuntimeError("MySQL 10.0.0.5:3306 refused"))))
    body = _call(_api.review_state, method="GET", file_id="f1")
    assert body["code"] != 0
    assert "3306" not in body["message"], "异常原文不得透给前端（交接契约第 6 条）"
```

```python
# ── POST /file/review/<task_id>/fix ─────────────────────────────────

def _fix_setup(monkeypatch, **over):
    """一轮已完成的审核 + 一条 high 待修项 + next_round 桩，返回调用记录。"""
    rounds = [_round(1, "annotated", kb_ids='["kb1"]')]
    kw = dict(rounds=rounds, owned=rounds, pending=[_ann("a1", severity="high")],
              next_no=(2, "v2"))
    kw.update(over)
    return _patch_services(monkeypatch, **kw)


def test_fix_rejects_foreign_or_missing_task(monkeypatch):
    calls = _fix_setup(monkeypatch, owned=[])
    body = _call(_api.fix_review, task_id="t1", body={"levels": ["high"]})
    assert body["code"] != 0
    assert "create_round" not in calls and "spawned" not in calls


def test_fix_rejects_while_round_still_running(monkeypatch):
    rounds = [_round(1, "reviewing")]
    calls = _fix_setup(monkeypatch, rounds=rounds, owned=rounds)
    body = _call(_api.fix_review, task_id="t1", body={"levels": ["high"]})
    assert body["code"] == RetCode.OPERATING_ERROR
    assert "create_round" not in calls


def test_fix_rejects_while_spawn_still_running(monkeypatch):
    """轮次行已是终态、线程还在收尾：必须拒，否则新轮次永远等不到线程。"""
    calls = _fix_setup(monkeypatch, running=True)
    body = _call(_api.fix_review, task_id="t1", body={"levels": ["high"]})
    assert body["code"] == RetCode.OPERATING_ERROR
    assert "create_round" not in calls and "spawned" not in calls


def test_fix_rejects_when_rounds_exhausted(monkeypatch):
    rounds = [_round(1, "annotated"), _round(2, "done"), _round(3, "done"), _round(4, "done")]
    calls = _fix_setup(monkeypatch, rounds=rounds, owned=rounds)
    body = _call(_api.fix_review, task_id="t1", body={"levels": ["high"]})
    assert body["code"] == RetCode.OPERATING_ERROR
    assert "create_round" not in calls


def test_fix_rejects_when_selected_levels_have_no_pending(monkeypatch):
    calls = _fix_setup(monkeypatch, pending=[_ann("a1", severity="low")])
    body = _call(_api.fix_review, task_id="t1", body={"levels": ["high"]})
    assert body["code"] == RetCode.OPERATING_ERROR
    assert "create_round" not in calls, "选中级别无待修项时不许建轮次（会白烧一次重试机会）"


def test_fix_rejects_invalid_levels(monkeypatch):
    calls = _fix_setup(monkeypatch)
    assert _call(_api.fix_review, task_id="t1",
                 body={"levels": ["high", "critical"]})["code"] == RetCode.ARGUMENT_ERROR
    assert _call(_api.fix_review, task_id="t1", body={})["code"] == RetCode.ARGUMENT_ERROR
    assert _call(_api.fix_review, task_id="t1",
                 body={"levels": []})["code"] == RetCode.ARGUMENT_ERROR
    assert "create_round" not in calls


def test_fix_creates_gated_round_and_spawns(monkeypatch):
    calls = _fix_setup(monkeypatch)
    body = _call(_api.fix_review, task_id="t1", body={"levels": ["high"]})
    assert body["code"] == 0
    assert body["data"] == {"task_id": "t1", "round_id": "rid-new",
                            "round_no": 2, "status": "fixing", "fix_rounds_left": 2}
    kw = calls["created"]
    assert kw["task_id"] == "t1"
    assert kw["file_id"] == "f1"                     # 继承当前轮的 file_id
    assert (kw["round_no"], kw["file_version"]) == (2, "v2")   # 只用 next_round 的口径
    assert kw["status"] == "fixing"
    assert kw["tenant_id"] == TENANT and kw["created_by"] == TENANT   # 不许写空串
    assert kw["kb_ids"] == '["kb1"]'                 # 继承本轮 KB 配置
    assert kw["user_query"] == ("审核这份招标文件\n"
                                "本次只修复【严重/high】级别的问题，其余级别的问题请保持原样、不要改动。")
    assert calls["spawned"] == ["t1"], "spawn 的实参必须是 task_id（不是 round_id）"


def test_fix_second_round_uses_first_round_query_baseline(monkeypatch):
    """连轮修复：基准恒为首轮原文，不把上一轮的级别指令叠进来。"""
    rounds = [_round(1, "annotated", user_query="审核这份招标文件"),
              _round(2, "done", user_query="审核这份招标文件\n本次只修复【严重/high】级别的问题，其余级别的问题请保持原样、不要改动。")]
    calls = _fix_setup(monkeypatch, rounds=rounds, owned=rounds, next_no=(3, "v3"))
    _call(_api.fix_review, task_id="t1", body={"levels": ["medium"]})
    q = calls["created"]["user_query"]
    assert q.count("本次只修复") == 1, f"级别指令被叠加了：{q}"
    assert "严重/high" not in q, "上一轮已作废的级别指令不得出现在本轮"
    assert "一般/medium" in q


def test_fix_appends_caller_extra_query(monkeypatch):
    calls = _fix_setup(monkeypatch)
    _call(_api.fix_review, task_id="t1",
          body={"levels": ["high"], "user_query": "重点看保证金"})
    q = calls["created"]["user_query"]
    assert "--" not in q and "重点看保证金" in q
    assert "本次补充要求：重点看保证金" in q
```

```python
# ── POST /file/review/annotation/<aid>/status ───────────────────────

def _status_setup(monkeypatch, **over):
    calls = _patch_services(monkeypatch, owned=[_round(1, "annotated")])
    calls["_ann_row"] = _ann("a1")
    return calls


def test_annotation_status_rejects_fixed_and_unknown(monkeypatch):
    calls = _status_setup(monkeypatch)
    for bad in ("fixed", "new", "", "OPEN "[:0] + "resolved-x", None):
        body = _call(_api.update_annotation_status, annotation_id="a1",
                     body={"status": bad})
        assert body["code"] == RetCode.ARGUMENT_ERROR, f"{bad!r} 应被拒"
    assert "updated" not in calls, "参数非法时不许写库"


def test_annotation_status_rejects_missing_annotation(monkeypatch):
    calls = _status_setup(monkeypatch)
    calls["_ann_row"] = None
    body = _call(_api.update_annotation_status, annotation_id="a1",
                 body={"status": "wontfix"})
    assert body["code"] != 0
    assert "updated" not in calls


def test_annotation_status_rejects_foreign_task(monkeypatch):
    """标注所属 task 不归当前用户 → 拒绝且不写库（写路径严格校验）。"""
    calls = _status_setup(monkeypatch, owned=[])
    body = _call(_api.update_annotation_status, annotation_id="a1",
                 body={"status": "resolved"})
    assert body["code"] != 0
    assert "updated" not in calls


def test_annotation_status_writes_and_returns(monkeypatch):
    calls = _status_setup(monkeypatch)
    body = _call(_api.update_annotation_status, annotation_id="a1",
                 body={"status": "WontFix"})
    assert body["code"] == 0
    assert body["data"] == {"annotation_id": "a1", "status": "wontfix"}
    assert calls["updated"] == [("a1", "wontfix")]


def test_annotation_status_maps_write_miss_to_error(monkeypatch):
    """并发删除：查到了行但写 0 行 → 仍按「不存在」回，不泄露时序差异。"""
    calls = _status_setup(monkeypatch)
    monkeypatch.setattr(_api.FileReviewAnnotationService, "update_status",
                        classmethod(lambda cls, aid, status: False))
    body = _call(_api.update_annotation_status, annotation_id="a1",
                 body={"status": "resolved"})
    assert body["code"] != 0


def test_annotation_status_hides_internal_error_text(monkeypatch):
    _status_setup(monkeypatch)
    monkeypatch.setattr(_api.FileReviewAnnotationService, "get_by_id",
                        classmethod(lambda cls, aid: (_ for _ in ()).throw(
                            RuntimeError("MinIO endpoint=http://10.0.0.9:9000"))))
    body = _call(_api.update_annotation_status, annotation_id="a1",
                 body={"status": "resolved"})
    assert body["code"] != 0
    assert "9000" not in body["message"]
```

> 测试文件顶部需要 `from common.constants import RetCode`（断言业务码用）。`_fix_setup` 里
> `next_no` 是 stub 返回值，`next_round` 的真实口径已由 `test/test_file_review_service.py`
> 覆盖，这里只验证端点**用**它而不是自己算轮号。

- [ ] **Step 7: 蓝图自动注册确认**

`api/apps/__init__.py` 的 `search_pages_path` 会 glob `api/apps/restful_apis/*.py` 全自动注册
（`page.manager = Blueprint(page_name, module_name)`，`url_prefix = f"/api/{API_VERSION}"`），
**无需手动改 `__init__.py`**。确认 `file_review_api.py` 落在该目录、且变量名是 `manager` 即可。

- [ ] **Step 8: 跑测试确认通过**

```bash
uv run --no-sync pytest test/test_file_review_api.py test/test_file_review_tool.py test/test_file_review_service.py -v
uv run --no-sync ruff check api/apps/restful_apis/file_review_api.py api/db/services/file_review_service.py agent/tools/file_review.py test/test_file_review_api.py
```

Expected: 全部 PASS；ruff 无输出

- [ ] **Step 9: 提交**

```bash
git add api/apps/restful_apis/file_review_api.py test/test_file_review_api.py
git commit -m "feat(file-review): REST API 4 endpoints (轮询读模型 + fix + 标注人工闭环)"
```

- [ ] **Step 10: 回填「实测落地」**

实现完成后，把实际提交 SHA、跑测结果（用例数）、实现过程中发现的规格偏差（若有）、
以及「交给 T10 的硬约束」写进本节末尾的「实测落地」块（格式照 T7 / T8 两节）。

---

> ### T9 对下游任务的影响（T10 / T11 / T12 / T14 / T15 必须按此执行）
>
> 上游砍掉 SSE 后，原任务描述里与 SSE 相关的部分全部作废：
>
> | 任务 | 原描述 | 改为 |
> |---|---|---|
> | T10 | 前端流类型 + **归约函数** | 只保留**类型定义**（`IFileReviewAnnotation` / `IFileReviewRound` / `IFileReviewState`）；删掉 `IFileReviewEvent` 与 `applyFileReviewEvent`（没有事件流可归约）。`filterAnnotationsByVersion` 保留但**面板不得使用**：标注的 file_version 恒为首轮版本而成稿是 v2/v3/v4，按成稿版本过滤会一条都查不到 |
> | T11 | API Hook（含 start/finish/annotation） | 按 T9 实际端点重写：`listFileReviewTemplates` / `getFileReviewState(file_id)` / `fixFileReview(task_id, levels, user_query?)` / `updateAnnotationStatus(aid, status)`；**删掉** `startFileReview` / `finishReview` / `addAnnotation` |
> | T12 | 进度卡（EventSource 订阅） | 改为**轮询** `getFileReviewState(file_id)`（未终态时 3s 一次，终态停），参照 `use-template-fill-run-recovery.ts` 的写法；`canFix` 判据改用返回的 `fix_rounds_left > 0 && current.status === 'annotated'`，**不要**用 `rounds.length < 3`（failed 轮同样占名额），也**不要**自己拼 user_query（级别指令由服务端 `compose_fix_query` 生成） |
> | T14 / T15 | 订阅 `file_review_progress` 流式事件 | 没有该事件。改为「节点/工具产出 task_id → 用 file_id 轮询 state」，即 T12 的进度卡自取数据，不需要对话侧喂事件 |

---

## Task 10: 前端类型 + 小工具（无 SSE 归约）

**Files:**
- Create: `web/src/hooks/file-review-stream.ts`

> ### T10 规格重写说明（替换原「SSE 事件 + 归约」版）
>
> 原规格假设 T9 提供 SSE 事件流；T9 已砍 SSE（见 T9 规格重写说明），改为「以文件为中心的轮询读模型」+ `GET /file/review/file/<file_id>/state` 一次性给齐。本任务随之简化：
>
> - **删** `IFileReviewEvent` 与 `applyFileReviewEvent` —— 没有事件流可归约。状态权威源是 state 端点返回的完整对象（`rounds[]` + `current` + `annotations[]` + `fix_rounds_left`）。
> - **删** `filterAnnotationsByVersion` —— 该函数按 `file_version` 过滤标注，但 T6/T9 实测：**所有标注的 `file_version` 恒为首轮版本 v1**（只有 review 轮产标注，fix 轮不产标注）；而成稿是 v2/v3/v4。按成稿版本过滤 = 一条都查不到 = 纯死代码。面板要展示标注就是 state 端点给的 `annotations[]` 全集，不需要过滤。
> - **不加 SSE 重连/状态合并等新增能力** —— 父项目 CLAUDE.md 明确「不为想象中的未来需求过度设计」。轮询逻辑由 T12 在 hook 层负责（参照既有 `use-template-fill-run-recovery.ts` 的快照 + 轮询模式）。
> - **保留** 类型定义 —— 因为响应体形状横跨 hook / 进度卡 / 面板三处共享，不抽出来三处各写一份会漂移。
> - **加一个 `isRoundRunning(status)` 谓词** —— T12 的轮询要按它判断「继续轮询 / 停止」，集中在一个文件里供 hook 与进度卡共用（避免三处各写 `status === 'reviewing' || status === 'fixing'` 漂移）。
>
> T11/T12 必须按本文件的类型契约对齐 API hook 与进度卡。

- [ ] **Step 1: 创建文件**

**1a. 文件头注释（说明数据来源与设计取舍）**

文件顶端写明：
- 模块名：`web/src/hooks/file-review-stream.ts`（与 `template-fill-stream.ts` 并列，纯类型与小工具模块，无 React 依赖）。
- 数据来源：`GET /api/v1/file/review/file/<file_id>/state` 端点（`api/apps/restful_apis/file_review_api.py` 的 `review_state` 处理器）。
- 与 `template-fill-stream.ts` 的关键差异：本模块**没有** `IFileReviewEvent` / 归约器，因为没有 SSE；状态权威源是完整对象（轮询一次性给齐），不是事件序列。
- 标注版本语义：**所有标注的 `file_version` 恒为首轮 v1**（T9 交接契约第 2 条的延伸：只有 review 轮产标注）。面板禁止按 `file_version` 过滤 —— 见 `IFileReviewAnnotation.file_version` 字段的 JSDoc 警告。

**1b. 四个类型 + 一个谓词**（完整代码如下，逐行覆盖文件全部内容）

```typescript
// 文件审核状态类型与小工具（与 template-fill-stream.ts 并列，纯类型模块）
// 数据来源：GET /api/v1/file/review/file/<file_id>/state（T9 review_state 处理器）
// 没有 SSE、没有归约器：进度真相是 file_review_round 表，前端轮询拿完整对象。
// 注意标注版本语义：所有标注的 file_version 恒为首轮 v1（只有 review 轮产标注）；
// 面板展示标注用 state.annotations 全集，禁止按 file_version 过滤。

/** 单条标注 */
export interface IFileReviewAnnotation {
  id: string;
  round_id: string;
  task_id: string;
  file_id: string;
  /** 恒为首轮 v1；不要按此字段过滤面板标注列表。 */
  file_version: string;
  /** 自由字符串（format / completeness / clause / qualification / price / other …），
   *  T6 不做白名单校验，executor 兜底归一为 'other'。 */
  type: string;
  severity: 'high' | 'medium' | 'low';
  issue: string;
  suggestion: string;
  matched_text: string;
  source: 'ai' | 'manual';
  status: 'open' | 'new' | 'fixed' | 'resolved' | 'wontfix';
  prev_annotation_id: string;
  /** 已序列化的 JSON 字符串（docx: p_hash/offset/run_index；xlsx: sheet/cell），
   * 解析由调用方按类型处理。 */
  anchor: string | Record<string, unknown>;
}

/** 单轮次（一次 review 或一次 fix 的产物） */
export interface IFileReviewRound {
  id: string;
  round_no: number;
  /** reviewing/annotated/fixing/done/failed —— 见 isRoundRunning 谓词的注释 */
  status: string;
  /** 成稿对象名（v2/v3/v4 …）；无成稿时为 '' */
  file_version: string;
  template_id: string;
  user_query: string;
  summary: string;
  /** 已被 T9 _ERROR_CLIP=200 裁剪后的错误文案（>200 加 … 后缀） */
  error: string;
  /** 该轮成稿的 MinIO 对象名（无成稿时为 ''） */
  minio_path: string;
  /** 是否有可下载成稿（判据是 minio_path 非空，与 status 无关——见 T9 交接契约第 2 条） */
  produced: boolean;
}

/** state 端点的完整响应体（轮询结果） */
export interface IFileReviewState {
  file_id: string;
  task_id: string | null;
  rounds: IFileReviewRound[];
  /** 最近一轮；用户主动选 fix 的入口会读它的 task_id/round_no */
  current: IFileReviewRound | null;
  /** 该展示的文档：最近一次落盘的成稿；从未落盘时回退到原件（object=file_id, version=''） */
  doc: { object: string; version: string };
  /** 全部标注（跨轮次/版本/任务）；file_version 恒为 v1 */
  annotations: IFileReviewAnnotation[];
  /** 面板头部计数（仅展示，不参与任何判定——见 _count_annotations 注释） */
  annotation_counts: {
    total: number;
    high: number;
    medium: number;
    low: number;
    pending: number;
    fixed: number;
  };
  /** 剩余可发起 fix 的轮次数（Service.fix_rounds_left，0 表示封顶） */
  fix_rounds_left: number;
  max_fix_rounds: number;
}

/** fix 端点的响应（POST /file/review/<task_id>/fix） */
export interface IFileReviewFixResponse {
  task_id: string;
  round_id: string;
  round_no: number;
  status: 'fixing';
  fix_rounds_left: number;
}

/** 标注状态修改端点的响应 */
export interface IFileReviewAnnotationUpdateResponse {
  annotation_id: string;
  status: IFileReviewAnnotation['status'];
}

/** 范本列表端点的响应 */
export interface IFileReviewTemplatesResponse {
  templates: Array<{
    id: string;
    name: string;
    description: string;
    annotation_types: string[];
  }>;
}

/** 轮次是否还在执行（决定轮询是否继续）。
 * 包含 reviewing 与 fixing —— 两种状态都意味着「后台线程还在写这轮」。
 * 注意：fixing 轮次的「终态前窗口」可能长达 20 次 DB 往返（T9 交接契约第 7 条），
 * 所以即使 status='fixing' 也必须继续轮询，不能停下。 */
export function isRoundRunning(status: string): boolean {
  return status === 'reviewing' || status === 'fixing';
}
```

**1c. 自检（不允许的导出）**

文件**不得**导出以下名字（已删 / 重新设计）：
- ~~`IFileReviewEvent`~~
- ~~`applyFileReviewEvent`~~
- ~~`filterAnnotationsByVersion`~~

理由：见本节开头的「T10 规格重写说明」。

- [ ] **Step 2: 自检 + 类型对齐**

读 `api/apps/restful_apis/file_review_api.py` 的四个 payload 构造器（`_template_payload` / `_round_payload` / `_doc_payload` / `_annotation_payload`）与 state 端点 body，逐字段核对本文件类型定义；任何不对齐的地方按服务端为准改类型。

**特别核对**：
- `_round_payload` 返回的 11 个字段全在 `IFileReviewRound` 里（id/round_no/status/file_version/template_id/user_query/summary/error/minio_path/produced + status）。
- `_annotation_payload` 返回的 13 个字段全在 `IFileReviewAnnotation` 里。
- state 端点的 data 字段（`file_id`/`task_id`/`rounds`/`current`/`doc`/`annotations`/`annotation_counts`/`fix_rounds_left`/`max_fix_rounds`）全在 `IFileReviewState` 里。
- `annotation_counts` 字段在 `api/apps/restful_apis/file_review_api.py:_count_annotations` 实测返回什么字段就写什么字段（不要按规约猜 —— 本节明确「按服务端为准」）。

- [ ] **Step 3: 跑类型检查 + 构建**

```bash
cd web && npx tsc --noEmit -p tsconfig.json 2>&1 | grep -E "file-review-stream\.ts|error TS" | head -20
```

预期：本文件 0 错误（其他文件已有错误不在本任务范围）。

若前端类型检查通过率低（很多历史错误），退化为：
```bash
cd web && npx tsc --noEmit -p tsconfig.json 2>&1 | grep -c "file-review-stream"
```
预期：**0**（本文件零类型错误）。

- [ ] **Step 4: 提交**

```bash
git add web/src/hooks/file-review-stream.ts
git commit -m "feat(file-review): frontend types + isRoundRunning (no SSE reducer)"
```

---

## T10 → T11/T12/T14/T15 交接契约（强制执行）

| 任务 | 约束 |
|---|---|
| T11 API Hook | **必须**用本文件导出的 5 个类型（`IFileReviewState` / `IFileReviewRound` / `IFileReviewAnnotation` / `IFileReviewFixResponse` / `IFileReviewTemplatesResponse`）声明响应类型；不得再自造一份局部 interface。 |
| T12 进度卡 | **必须**用 `isRoundRunning` 决定「继续轮询 / 停止」（不要在 hook / 组件里自写 `status === 'reviewing' \|\| status === 'fixing'`）；轮询间隔默认 3s，可被 hook 接受 `intervalMs` 入参覆盖；终态（`status === 'annotated'` 且 `fix_rounds_left === 0`，或 `status === 'done'` 或 `status === 'failed'`）停轮询。 |
| T12 进度卡（canFix 判据） | `canFix = fix_rounds_left > 0 && current?.status === 'annotated'`；**禁止**用 `rounds.length < 3`（failed 轮同样占名额）；**禁止**自拼 user_query（级别指令由服务端 `compose_fix_query` 生成）。 |
| T12 面板展示 | 展示标注用 `state.annotations` 全集，**禁止**按 `file_version` 过滤（见 `IFileReviewAnnotation.file_version` JSDoc）。 |
| T14 / T15 集成 | 流式事件已砍，对话侧 / 流程侧都改为「节点 / 工具产出 task_id → 用 file_id 轮询 state」；不需要对话侧再喂进度事件给本模块。 |

---

## Task 11: API Hook 封装

**Files:**
- Modify: `web/src/utils/api.ts`（追加 4 个 endpoint 路径常量）
- Create: `web/src/hooks/use-file-review-request.ts`

> ### T11 规格重写说明（替换原「start / finish / annotation」6 函数版）
>
> 原 T11 假设 T9 提供 `start` / `finish` / `annotation` / `template/list` / `annotations` 共 6 个端点。T9 砍 SSE 与 `start`/`finish`/`annotation`/`annotations` 后只剩 4 端点，T11 随之收缩为 **4 函数 + 1 失效器**。所有返回类型必须用 T10 导出的接口，不允许再写局部 interface。

- [ ] **Step 1: 追加 endpoint 路径到 `web/src/utils/api.ts`**

在「模板填写」相关路径附近追加（参照既有 `confirmTemplateFill` 的格式，紧贴在一个分组内）。注意 `restAPIv1 = '/api/v1'`（已在 L2 定义），所有路径前缀用它；服务端 `api/apps/restful_apis/file_review_api.py` 的 `@manager.route` 也确认是 `/file/review/...`，最终路径前缀 `/api/v1` × 后端 `/file/review/...` = `/api/v1/file/review/...`。

```typescript
// 文件审核（T9 REST API：4 端点轮询读模型 + fix + 标注人工闭环）
fileReviewTemplates: `${restAPIv1}/file/review/templates`,
fileReviewState: (fileId: string) =>
  `${restAPIv1}/file/review/file/${fileId}/state`,
fileReviewFix: (taskId: string) => `${restAPIv1}/file/review/${taskId}/fix`,
fileReviewAnnotationStatus: (annotationId: string) =>
  `${restAPIv1}/file/review/annotation/${annotationId}/status`,
```

- [ ] **Step 2: 创建 `web/src/hooks/use-file-review-request.ts`**

**2a. 头部 import**（参照 `use-template-fill-request.ts` 的 sibling 风格）

```typescript
import api from '@/utils/api';
import request from '@/utils/request';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { isRoundRunning } from './file-review-stream';
import type {
  IFileReviewState,
  IFileReviewFixResponse,
  IFileReviewAnnotationUpdateResponse,
  IFileReviewTemplatesResponse,
} from './file-review-stream';
```

**2b. queryKey 约定**

统一前缀 `['fileReview', ...]`，便于失效器批量处理（与 `['templateFill', ...]` 同形态）。

**2c. 4 个 hook + 1 个失效器**

```typescript
/** 范本列表：启用即拉一次，无轮询 */
export function useFileReviewTemplates(opts?: { enabled?: boolean }) {
  return useQuery({
    queryKey: ['fileReview', 'templates'] as const,
    queryFn: async () => {
      const { data } = await request.get(api.fileReviewTemplates);
      return data as { code: number; data: IFileReviewTemplatesResponse };
    },
    enabled: opts?.enabled ?? true,
  });
}

/** 文件状态：reviewing/fixing 时 3s 函数式轮询，否则停（终态判定走 isRoundRunning） */
export function useFileReviewState(fileId: string) {
  return useQuery({
    queryKey: ['fileReview', 'state', fileId] as const,
    queryFn: async () => {
      const { data } = await request.get(api.fileReviewState(fileId));
      return data as { code: number; data: IFileReviewState };
    },
    enabled: !!fileId,
    refetchInterval: (query) => {
      const status = query.state.data?.data?.current?.status;
      return status && isRoundRunning(status) ? 3000 : false;
    },
  });
}

/** 发起一轮修复：成功时同步失效对应 file_id 的 state 缓存（fix 端点按 task_id 圈定，
 * 但返回值不含 file_id，故调用方必须透传 file_id 才能失效）。 */
export function useFixFileReview(fileId: string) {
  const invalidate = useInvalidateFileReview();
  return useMutation({
    mutationFn: async (params: {
      taskId: string;
      levels: string[];
      userQuery?: string;
    }) => {
      const { data } = await request.post(api.fileReviewFix(params.taskId), {
        data: { levels: params.levels, user_query: params.userQuery },
      });
      if (data.code !== 0) {
        throw new Error(data.message || '修复发起失败');
      }
      return data.data as IFileReviewFixResponse;
    },
    onSuccess: () => invalidate(fileId),
  });
}

/** 人工置标注状态：成功时失效对应 file_id 的 state 缓存 */
export function useUpdateAnnotationStatus(fileId: string) {
  const invalidate = useInvalidateFileReview();
  return useMutation({
    mutationFn: async (params: {
      annotationId: string;
      status: 'open' | 'resolved' | 'wontfix';
    }) => {
      const { data } = await request.post(
        api.fileReviewAnnotationStatus(params.annotationId),
        { data: { status: params.status } },
      });
      if (data.code !== 0) {
        throw new Error(data.message || '标注状态更新失败');
      }
      return data.data as IFileReviewAnnotationUpdateResponse;
    },
    onSuccess: () => invalidate(fileId),
  });
}

/** 失效器：fix / annotation status 变更后必须调一次，让面板与进度卡重拉 state。
 * fix 端点的 response 不含 file_id（只有 task_id），故调用方必须把 file_id 传进来。
 * 仅失效 ['fileReview', 'state', fileId] 这一条，避免误冲掉其它文件的轮询。 */
function useInvalidateFileReview() {
  const queryClient = useQueryClient();
  return (fileId: string) => {
    queryClient.invalidateQueries({
      queryKey: ['fileReview', 'state', fileId],
    });
  };
}
```

**2d. 不允许的导出**（与 T10 一样禁导出已删能力）

文件不得导出以下名字（spec 没写、若非必要不加）：
- ~~`startFileReview`~~ —— T9 没这个端点，审核入口在 T7 画布节点 / T8 对话工具，它们各自走各自的路径
- ~~`finishReview`~~ —— 同上
- ~~`addAnnotation`~~ —— 同上
- ~~`listAnnotations`~~ —— 标注已包含在 state 端点 response 里（`state.annotations`），无需单列

- [ ] **Step 3: 跑类型检查**

```bash
cd web && npx tsc --noEmit -p tsconfig.json 2>&1 | grep -E "use-file-review-request\.ts|file-review-stream\.ts|api\.ts" | head
```

预期：0 命中（本任务触及的 3 文件零类型错误；其他文件历史错误不在范围）。

- [ ] **Step 4: 提交**

```bash
git add web/src/utils/api.ts web/src/hooks/use-file-review-request.ts
git commit -m "feat(file-review): API hook wrapper (4 endpoints + invalidate)"
```

---

## T11 → T12/T14/T15 交接契约（强制执行）

| 任务 | 约束 |
|---|---|
| T12 进度卡 | **必须**复用 `useFileReviewState(fileId)` 作为轮询数据源，不要自写 `setInterval` + `fetch`（破坏 React Query 缓存层会让刷新恢复失效）。`canFix` 判据从 `data.data.current?.status === 'annotated' && data.data.fix_rounds_left > 0` 派生。轮询开关判断**不要**自己拼 `status === 'reviewing' \|\| status === 'fixing'` —— 已由 `isRoundRunning` 集中放在 T10 里。 |
| T14 对话集成 | 画布节点/对话工具产出 task_id 时调用方必须把 file_id 一并存进 React state；调用 `useFixFileReview(fileId).mutate({taskId, levels, userQuery})`。 |
| T15 流程页集成 | 同 T14，流程节点挂载进度卡时用 file_id 拉 state。 |
| 失效器 | fix / annotation status 变更后**必须**失效 `['fileReview', 'state', fileId]`；漏调一次会让用户看到陈旧状态（修复已开始但面板还停在 `annotated`）。已由 hook 内置 `onSuccess` 自动调用，调用方不要再手动失效（重复失效只是浪费，不会出错）。 |

---

## Task 12: 进度卡组件 file-review-progress

**Files:**
- Create: `web/src/pages/c-chat/file-review-progress.tsx`
- Create: `web/src/pages/c-chat/__tests__/file-review-progress.test.tsx`

> ### T12 规格重写说明（替换原「EventSource 订阅 + SSE 归约」版）
>
> T9 砍 SSE 后原 T12 整套基于 `new EventSource(...)` + `applyFileReviewEvent` 的实现失效。本任务 reshape 为「**轮询 + hook 数据驱动**」：
>
> - **删** SSE EventSource 订阅（用 `useFileReviewState(fileId)` 内置的 refetchInterval 替代）
> - **删** `state?: IFileReviewState` 初始 prop —— 改为 `fileId: string` 必填 prop（hook 拿数据）
> - **删** `listAnnotations` 独立调用 —— 标注已在 `state.data.annotations` 全集中
> - **删** 「再修一轮」「结束审核」按钮 —— T9 砍了 `finish` 端点；修复即新一轮（`next_round` 自动 +1）
> - **改** 修复级别按钮：原规格硬编码 `handleFix(['high', 'medium'])` / `handleFix(['high'])` 写在 JSX 内，**改为调起 Popover 让用户多选级别**（用户语义是「我想修严重和一般」，不是「点击预设按钮」）
> - **改** canFix 判据：用 `state.data.current?.status === 'annotated' && state.data.fix_rounds_left > 0`（与 T11 交接契约一致），**禁止**用 `rounds.length < 3`
> - **删** `streaming?: boolean` prop —— 轮询开关由 hook 内部控制（`isRoundRunning`），调用方不需要懂
>
> 文件位置保持 `pages/c-chat/`（与 `template-fill-progress.tsx` 同目录；c-chat 与 flow 双方都从此处 import，路径不影响复用）

- [ ] **Step 1: 写失败测试**

`web/src/pages/c-chat/__tests__/file-review-progress.test.tsx`，参照 `__tests__/template-fill-confirm-card.test.tsx` 的 mock 风格：

```tsx
// 文件审核进度卡：c-chat 对话与 flow AI 对话区共用
// （T11 交接契约：必须复用 useFileReviewState 拿数据，不自写轮询）。
import {
  useFileReviewState,
  useFixFileReview,
  useUpdateAnnotationStatus,
} from '@/hooks/use-file-review-request';
import FileReviewProgress from '@/pages/c-chat/file-review-progress';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

jest.mock('@/hooks/use-file-review-request', () => ({
  useFileReviewState: jest.fn(),
  useFixFileReview: jest.fn(),
  useUpdateAnnotationStatus: jest.fn(),
}));

const mockUseFileReviewState = useFileReviewState as jest.MockedFunction<typeof useFileReviewState>;
const mockUseFixFileReview = useFixFileReview as jest.MockedFunction<typeof useFixFileReview>;
const mockUseUpdateAnnotationStatus = useUpdateAnnotationStatus as jest.MockedFunction<
  typeof useUpdateAnnotationStatus
>;

const baseState = (over: any = {}) => ({
  data: {
    code: 0,
    data: {
      file_id: 'f1', task_id: 't1',
      rounds: [{
        id: 'r1', round_no: 1, status: 'annotated', file_version: 'v1',
        template_id: 'bid_doc_format', user_query: '', summary: 'high:1 medium:0 low:0',
        error: '', minio_path: 'frv-t1-v2.docx', produced: true,
      }],
      current: { id: 'r1', round_no: 1, status: 'annotated', file_version: 'v1',
                 template_id: 'bid_doc_format', user_query: '', summary: '', error: '',
                 minio_path: 'frv-t1-v2.docx', produced: true },
      doc: { object: 'frv-t1-v2.docx', version: 'v2' },
      annotations: [{ id: 'a1', round_id: 'r1', task_id: 't1', file_id: 'f1',
                      file_version: 'v1', type: 'format', severity: 'high',
                      issue: '正文未签字', suggestion: '', matched_text: '',
                      source: 'ai', status: 'open', prev_annotation_id: '', anchor: {} }],
      annotation_counts: { total: 1, high: 1, medium: 0, low: 0, pending: 1, fixed: 0 },
      fix_rounds_left: 3, max_fix_rounds: 3,
    },
  },
  isLoading: false, isError: false,
  refetch: jest.fn(), ...over,
});

describe('FileReviewProgress', () => {
  beforeEach(() => {
    mockUseFixFileReview.mockReturnValue({ mutate: jest.fn(), isLoading: false } as any);
    mockUseUpdateAnnotationStatus.mockReturnValue({ mutate: jest.fn(), isLoading: false } as any);
  });

  it('renders round summary + 「打开审核面板」callback with annotations + doc.version', async () => {
    mockUseFileReviewState.mockReturnValue(baseState() as any);
    const onOpenReview = jest.fn();
    render(<FileReviewProgress fileId="f1" onOpenReview={onOpenReview} />);
    expect(screen.getByText(/第 1 轮/)).toBeInTheDocument();
    expect(screen.getByText(/high:1 medium:0 low:0/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /打开审核面板/ }));
    await waitFor(() => expect(onOpenReview).toHaveBeenCalledTimes(1));
    const [annotations, version] = onOpenReview.mock.calls[0];
    expect(version).toBe('v2');
    expect(annotations[0].id).toBe('a1');
  });

  it('canFix=true 时显示「选择级别修复」入口，fix_rounds_left=0 时隐藏', () => {
    mockUseFileReviewState.mockReturnValue(baseState() as any);
    const { rerender } = render(<FileReviewProgress fileId="f1" />);
    expect(screen.getByRole('button', { name: /修复|级别/ })).toBeInTheDocument();
    mockUseFileReviewState.mockReturnValue(baseState({
      data: { ...baseState().data, data: { ...baseState().data.data, fix_rounds_left: 0 } },
    }) as any);
    rerender(<FileReviewProgress fileId="f1" />);
    expect(screen.queryByRole('button', { name: /修复|级别/ })).toBeNull();
  });

  it('round.status=reviewing 时不显示「修复」入口，显示 spinner', () => {
    mockUseFileReviewState.mockReturnValue(baseState({
      data: { ...baseState().data, data: {
        ...baseState().data.data,
        current: { ...baseState().data.data.current, status: 'reviewing' },
      }},
    }) as any);
    render(<FileReviewProgress fileId="f1" />);
    expect(screen.queryByRole('button', { name: /修复|级别/ })).toBeNull();
    expect(screen.getByText(/正在审核|审核中/)).toBeInTheDocument();
  });

  it('点击「选择级别修复」调起 Popover，勾选 high + medium 后提交触发 useFixFileReview.mutate', async () => {
    const mutate = jest.fn();
    mockUseFixFileReview.mockReturnValue({ mutate, isLoading: false } as any);
    mockUseFileReviewState.mockReturnValue(baseState() as any);
    render(<FileReviewProgress fileId="f1" />);
    fireEvent.click(screen.getByRole('button', { name: /修复|级别/ }));
    fireEvent.click(screen.getByLabelText(/严重/));
    fireEvent.click(screen.getByLabelText(/一般/));
    fireEvent.click(screen.getByRole('button', { name: /确认|提交/ }));
    await waitFor(() => expect(mutate).toHaveBeenCalledTimes(1));
    expect(mutate.mock.calls[0][0]).toMatchObject({ levels: ['high', 'medium'] });
  });

  it('hook isError 时显示错误降级文案（不暴露服务端文案）', () => {
    mockUseFileReviewState.mockReturnValue({ ...baseState(), isError: true, error: new Error('MySQL 10.0.0.5:3306') } as any);
    render(<FileReviewProgress fileId="f1" />);
    expect(screen.getByText(/加载失败|稍后重试/)).toBeInTheDocument();
    expect(screen.queryByText(/3306/)).toBeNull();
  });

  it('fix_rounds_left 用 max_fix_rounds - 已发起轮次数派生；禁止用 rounds.length < 3', () => {
    // 2 轮 round 1=annotated, round 2=failed → fix_rounds_left 来自服务端（不应按 rounds.length 派生）
    const state2 = baseState({
      data: { ...baseState().data, data: {
        ...baseState().data.data, fix_rounds_left: 1,
        rounds: [
          { ...baseState().data.data.current, status: 'annotated' },
          { ...baseState().data.data.current, round_no: 2, status: 'failed' },
        ],
      }},
    });
    mockUseFileReviewState.mockReturnValue(state2 as any);
    render(<FileReviewProgress fileId="f1" />);
    expect(screen.getByText(/剩余.*1.*轮/)).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: 创建组件**

文件 `web/src/pages/c-chat/file-review-progress.tsx`，与 `template-fill-progress.tsx` 同形态（无 i18n、纯中文文案、按设计 3.2「逻辑同构」落地）。

**2a. import 约定**

```tsx
import {
  useFileReviewState,
  useFixFileReview,
  useUpdateAnnotationStatus,
} from '@/hooks/use-file-review-request';
import type {
  IFileReviewAnnotation,
  IFileReviewRound,
  IFileReviewState,
} from '@/hooks/file-review-stream';
import { ChevronDown, Download, Eye, Loader2 } from 'lucide-react';
import { useState } from 'react';
```

**2b. props 契约**

```tsx
export default function FileReviewProgress({
  fileId,
  onOpenReview,
  onPreviewDoc,
}: {
  /** 必填。轮询 / 失效 / fix 入参都从这里派生。 */
  fileId: string;
  /** 点击「打开审核面板」时回调（参数：标注全集 + 当前成稿版本） */
  onOpenReview?: (annotations: IFileReviewAnnotation[], fileVersion: string) => void;
  /** 点击「下载成稿」时回调（参数：成稿 MinIO 对象名） */
  onPreviewDoc?: (minioPath: string) => void;
}) {
  // ── 数据 ─────────────────────────────────────────
  const state = useFileReviewState(fileId);
  const fixMutation = useFixFileReview(fileId);
  const _updateAnnotation = useUpdateAnnotationStatus(fileId); // 标注状态由面板触发，组件只透传

  const data = state.data?.code === 0 ? state.data.data : null;
  const error = state.error;

  // ── 派生（按 T11 交接契约：canFix 不自拼，用 fix_rounds_left） ──
  const current = data?.current ?? null;
  const status = current?.status ?? '';
  const isRunning = status === 'reviewing' || status === 'fixing';
  const canFix = status === 'annotated' && (data?.fix_rounds_left ?? 0) > 0;

  // ── 修复级别选择 Popover ─────────────────────────
  const [picking, setPicking] = useState(false);
  const [picked, setPicked] = useState<string[]>([]);
  const submitFix = (extraUserQuery?: string) => {
    fixMutation.mutate(
      {
        taskId: current!.task_id || data!.task_id || '',
        levels: picked,
        userQuery: extraUserQuery,
      },
      { onSuccess: () => { setPicking(false); setPicked([]); } },
    );
  };
  // 注：上式中 taskId 取值优先用 state 端点的 task_id（T9 砍了 start，端点由 T7/T8 触发；
  //  本组件是进度展示，task_id 必须由调用方保证存在 —— 此处兜底空串会在 useFixFileReview
  //  抛出，但 useFixFileReview 的 taskId 实际由父组件传入更安全。
  //  设计上：调用方在 fileId 已知时已有 task_id（来自工具/节点产出），建议通过 props 显式传。
}
```

> ⚠️ **设计缺陷补注**：上面 `taskId` 取值是兜底逻辑。更清晰的契约是父组件传 `taskId` prop，因为：
> - 调用方（对话/流程）在挂载进度卡时一定知道 task_id（来自工具/节点的产出）。
> - state 端点返回的 `task_id` 是**最近一轮**的 task_id，不是用户想要的「发起 fix 用的 task_id」（虽然通常一致，但耦合就是 bug 温床）。
>
> **修正方案**：在 props 中加 `taskId?: string` 可选 prop，**显式优先**，缺省才回退到 state.data.task_id。这样调用方可以零改动（继续传 fileId 即可，走兜底分支），但需要传 taskId 时有路径。
>
> 实现代码采用下方的最终版本（**已含 taskId prop**）。

**2c. 最终 props 与渲染**

```tsx
export default function FileReviewProgress({
  fileId,
  taskId: taskIdProp,
  onOpenReview,
  onPreviewDoc,
}: {
  fileId: string;
  /** 调用方已知 task_id 时显式传（来自 T7 节点 / T8 工具产出），
   *  修复端点需要的 task_id 优先取此；缺省回退到 state.data.task_id。
   *  推荐调用方总是传，避免与 state 端点的「最近一轮 task_id」语义耦合。 */
  taskId?: string;
  onOpenReview?: (annotations: IFileReviewAnnotation[], fileVersion: string) => void;
  onPreviewDoc?: (minioPath: string) => void;
}) {
  const state = useFileReviewState(fileId);
  const fixMutation = useFixFileReview(fileId);
  // （useUpdateAnnotationStatus 在面板调用，组件不在此处挂载）
  void useUpdateAnnotationStatus;  // 占位 import（spec 要求 import 必须使用，否则 lint 报错）

  const data = state.data?.code === 0 ? state.data.data : null;
  const error = state.error;

  const current = data?.current ?? null;
  const status = current?.status ?? '';
  const isRunning = status === 'reviewing' || status === 'fixing';
  const canFix = status === 'annotated' && (data?.fix_rounds_left ?? 0) > 0;
  const taskId = taskIdProp || data?.task_id || '';

  // 修复级别选择
  const [picking, setPicking] = useState(false);
  const [picked, setPicked] = useState<string[]>([]);
  const submitFix = () => {
    if (!taskId) return; // 防御：极端情况下 state 尚未拉到
    fixMutation.mutate(
      { taskId, levels: picked, userQuery: undefined },
      { onSuccess: () => { setPicking(false); setPicked([]); } },
    );
  };

  // ── 渲染 ─────────────────────────────────────────
  if (state.isError) {
    return (
      <div className="rounded-lg border border-[#E5E5E5] bg-[#F5F5F5] px-3 py-2.5 text-xs text-[#8C8C8C]">
        加载失败，请稍后重试
      </div>
    );
  }
  if (state.isLoading && !data) {
    return (
      <div className="flex items-center gap-1 rounded-lg border border-[#E5E5E5] bg-[#F5F5F5] px-3 py-2.5 text-xs text-[#8C8C8C]">
        <Loader2 className="h-3 w-3 animate-spin" /> 加载中…
      </div>
    );
  }
  if (!data || data.rounds.length === 0) {
    return null; // 还没产出轮次（不应该出现，组件挂在审核流程之后）
  }

  return (
    <div className="space-y-2 rounded-lg border border-[#E5E5E5] bg-[#F5F5F5] px-3 py-2.5 text-xs">
      <div className="font-medium text-[#000000]">
        文件审核{' '}
        <span className="text-[#8C8C8C]">
          第 {current!.round_no} 轮 ·{' '}
          {status === 'reviewing' ? '审核中'
            : status === 'fixing' ? '修复中'
            : status === 'annotated' ? '已完成'
            : status === 'done' ? '已结束'
            : status === 'failed' ? '失败'
            : status}
        </span>
        {canFix && (
          <span className="ml-2 text-[#8C8C8C]">
            剩余 {data.fix_rounds_left} 轮
          </span>
        )}
      </div>
      {current!.summary && (
        <div className="text-[#8C8C8C]">{current!.summary}</div>
      )}
      <div className="flex flex-wrap items-center gap-2 pt-0.5">
        <button
          type="button"
          className="flex items-center gap-1 rounded border border-[#1a66fb] px-2 py-0.5 text-[#1a66fb] hover:bg-[#F5F8FF]"
          onClick={() => onOpenReview?.(data.annotations, data.doc.version)}
        >
          <Eye className="h-3 w-3" /> 打开审核面板（{data.doc.version || '原件'}）
        </button>
        {data.doc.object && data.doc.object !== fileId && (
          <button
            type="button"
            className="flex items-center gap-1 rounded border border-[#1a66fb] px-2 py-0.5 text-[#1a66fb] hover:bg-[#F5F8FF]"
            onClick={() => onPreviewDoc?.(data.doc.object)}
          >
            <Download className="h-3 w-3" /> 下载成稿
          </button>
        )}
        {canFix && (
          <button
            type="button"
            className="flex items-center gap-1 rounded bg-[#1a66fb] px-2 py-0.5 text-white hover:bg-[#1557d6]"
            onClick={() => setPicking(true)}
          >
            选择级别修复 <ChevronDown className="h-3 w-3" />
          </button>
        )}
      </div>
      {isRunning && (
        <div className="flex items-center text-[#8C8C8C]">
          <Loader2 className="mr-1 h-3 w-3 animate-spin" />
          {status === 'reviewing' ? '正在审核…' : '正在修复…'}
        </div>
      )}
      {picking && (
        <FixLevelPopover
          picked={picked}
          onToggle={(l) => setPicked((p) =>
            p.includes(l) ? p.filter((x) => x !== l) : [...p, l],
          )}
          onCancel={() => { setPicking(false); setPicked([]); }}
          onConfirm={submitFix}
          busy={fixMutation.isLoading}
        />
      )}
    </div>
  );
}

function FixLevelPopover({
  picked, onToggle, onCancel, onConfirm, busy,
}: {
  picked: string[];
  onToggle: (l: string) => void;
  onCancel: () => void;
  onConfirm: () => void;
  busy: boolean;
}) {
  // 三选多；中文 label 与 T10 SEVERITY_CN 对齐（high→严重 / medium→一般 / low→提示）
  return (
    <div className="rounded border border-[#E5E5E5] bg-white p-2">
      <div className="mb-1 text-[#8C8C8C]">勾选要修复的级别：</div>
      <div className="flex flex-wrap gap-2">
        {[
          { v: 'high', l: '严重' },
          { v: 'medium', l: '一般' },
          { v: 'low', l: '提示' },
        ].map((opt) => (
          <label key={opt.v} className="flex items-center gap-1">
            <input
              type="checkbox"
              checked={picked.includes(opt.v)}
              onChange={() => onToggle(opt.v)}
              disabled={busy}
            />
            {opt.l}
          </label>
        ))}
      </div>
      <div className="mt-2 flex justify-end gap-2">
        <button type="button" className="text-[#8C8C8C]" onClick={onCancel} disabled={busy}>
          取消
        </button>
        <button
          type="button"
          className="rounded bg-[#1a66fb] px-2 py-0.5 text-white disabled:opacity-50"
          onClick={onConfirm}
          disabled={busy || picked.length === 0}
        >
          {busy ? '提交中…' : '确认修复'}
        </button>
      </div>
    </div>
  );
}
```

**2d. 不允许的导出**

文件不得导出以下名字（已删 / 不该在此层出现）：
- ~~`streaming?: boolean`~~ —— 轮询开关在 hook 内部
- ~~`state?: IFileReviewState` 初始 prop~~ —— 数据由 hook 提供
- ~~`finishReview` 调用~~ —— T9 砍了
- ~~硬编码 `handleFix(['high','medium'])`/`['high']`~~ —— 改为 Popover 多选
- ~~`rounds.length < 3` 判据~~ —— 用 `fix_rounds_left`

- [ ] **Step 3: 跑测试确认通过**

```bash
cd web && npx jest web/src/pages/c-chat/__tests__/file-review-progress.test.tsx --no-coverage 2>&1 | tail -20
```

预期：6 个用例全绿。jest 跑不通时退化为 build 验证：`cd web && npm run build`（可能慢，~30s）。

- [ ] **Step 4: 提交**

```bash
git add web/src/pages/c-chat/file-review-progress.tsx web/src/pages/c-chat/__tests__/file-review-progress.test.tsx
git commit -m "feat(file-review): progress card (poll + canFix from fix_rounds_left)"
```

---

## T12 → T13/T14/T15 交接契约（强制执行）

| 任务 | 约束 |
|---|---|
| T13 i18n | **不适用** —— 本组件按设计 3.2「全部文案中文，不走 i18n」（与 `template-fill-progress.tsx` 同款），T13 仅补 dialog / modal / button 通用 key。 |
| T14 对话侧集成 | 在 c-chat 消息流渲染处挂载 `<FileReviewProgress fileId={...} taskId={...} onOpenReview={...} onPreviewDoc={...} />`。**必须**把 fileId + taskId 一并存进 React state（来自 T8 工具产出）。**禁止**自写 EventSource。 |
| T15 流程页集成 | 同 T14，从 flow AI 对话区 import 本组件挂载。 |

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