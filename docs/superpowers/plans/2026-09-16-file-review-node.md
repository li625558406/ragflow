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

## Task 4: Patcher（find-match + patch 应用 + annotation 状态机）

**Files:**
- Create: `rag/svr/file_review/patcher.py`
- Test: `test/test_file_review_patcher.py`

- [ ] **Step 1: 写失败测试**

```python
# test/test_file_review_patcher.py
from rag.svr.file_review.patcher import apply_patches, find_unique


def test_find_unique_returns_pos_when_single_match():
    text = '投标人应满足以下要求：abc 资质等级'
    pos = find_unique(text, 'abc 资质等级')
    assert pos > 0


def test_find_unique_returns_neg1_when_zero_match():
    assert find_unique('hello world', 'xyz') == -1


def test_find_unique_returns_neg2_when_multi_match():
    assert find_unique('foo bar foo', 'foo') == -2  # 歧义


def test_apply_patches_single_replace():
    text = '报价：1000元'
    out, applied = apply_patches(text, [{'find': '1000元', 'replace': '1500元'}])
    assert out == '报价：1500元'
    assert applied == [True]


def test_apply_patches_skips_ambiguous():
    text = 'foo bar foo baz'
    out, applied = apply_patches(text, [{'find': 'foo', 'replace': 'QUX'}])
    # 歧义 find → 跳过（不改）
    assert out == text
    assert applied == [False]
```

- [ ] **Step 2: 跑测试确认失败** — `uv run --no-sync pytest test/test_file_review_patcher.py -v` 期望：ImportError

- [ ] **Step 3: 创建 `rag/svr/file_review/patcher.py`**

```python
"""文件 patcher：find 唯一匹配 + replace 替换；歧义/缺失则跳过保安全。
被 executor 用于多轮修复；不改 docx 格式（仅段落 run 字符串替换）。
apply_to_docx / apply_to_xlsx 在 docx/xlsx 文件层应用（后续 task 接入）。"""
from typing import List, Tuple


def find_unique(text: str, find_str: str) -> int:
    """在 text 中找 find_str：唯一出现返回正位置；0 次返回 -1；>1 次返回 -2（歧义）"""
    if not find_str:
        return -1
    count = text.count(find_str)
    if count == 0:
        return -1
    if count > 1:
        return -2
    return text.find(find_str)


def apply_patches(text: str, patches: List[dict]) -> Tuple[str, List[bool]]:
    """逐个 patch 应用：find 唯一才替换，否则跳过并记 applied=False。
    返回 (新文本, applied 列表)；patches 元素含 find/replace。"""
    out = text
    applied = []
    for p in patches:
        find_str = p.get('find', '')
        replace_str = p.get('replace', '')
        if find_unique(out, find_str) >= 0:
            out = out.replace(find_str, replace_str, 1)
            applied.append(True)
        else:
            applied.append(False)
    return out, applied
```

- [ ] **Step 4: 跑测试确认通过**

```bash
uv run --no-sync pytest test/test_file_review_patcher.py -v
```
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add rag/svr/file_review/patcher.py test/test_file_review_patcher.py
git commit -m "feat(file-review): patcher with unique-match safety"
```

---

## Task 5: Spawn（daemon 线程 + 防重入）

**Files:**
- Create: `rag/svr/file_review/spawn.py`
- Test: `test/test_file_review_spawn.py`

- [ ] **Step 1: 写失败测试**

```python
# test/test_file_review_spawn.py
from rag.svr.file_review import spawn


def test_is_running_false_initially():
    assert spawn.is_running('nonexistent-task-id') is False


def test_spawn_runs_executor(monkeypatch):
    seen = []

    def fake_executor(task_id):
        seen.append(task_id)

    monkeypatch.setattr(spawn, 'execute_task', fake_executor)
    spawn.spawn_review_task('t1')
    import time
    time.sleep(0.5)
    assert seen == ['t1']
    assert not spawn.is_running('t1')  # finally 清理
```

- [ ] **Step 2: 跑测试确认失败** — `uv run --no-sync pytest test/test_file_review_spawn.py -v` 期望：ImportError

- [ ] **Step 3: 创建 `rag/svr/file_review/spawn.py`**

```python
"""审核任务执行线程 spawn（与 template_fill/spawn.py 同形态）。
防重入集合 + daemon 线程 + 线程启动失败兜底。"""
import logging
import threading

logger = logging.getLogger(__name__)

__all__ = ["is_running", "spawn_review_task"]

_running_lock = threading.Lock()
_running_tasks: set = set()

execute_task = None


def is_running(task_id: str) -> bool:
    with _running_lock:
        return task_id in _running_tasks


def spawn_review_task(task_id: str) -> None:
    with _running_lock:
        if task_id in _running_tasks:
            return
        _running_tasks.add(task_id)

    def _run():
        try:
            fn = execute_task
            if fn is None:
                from rag.svr.file_review.executor import execute_task as fn
            fn(task_id)
        except Exception:
            logger.exception("review task thread crashed, task_id=%s", task_id)
        finally:
            with _running_lock:
                _running_tasks.discard(task_id)

    try:
        threading.Thread(target=_run, daemon=True, name=f"file-review-{task_id[:8]}").start()
    except Exception:
        with _running_lock:
            _running_tasks.discard(task_id)
        logger.exception("review task spawn failed, task_id=%s", task_id)
        # 任务行 CAS 置 failed 供重试
        try:
            from api.db.db_models import DB
            from api.db.services.file_review_service import FileReviewRoundService
            with DB.connection_context():
                FileReviewRoundService.model.update(
                    status="failed", error="任务调度失败：后台线程启动异常，请重试"
                ).where(
                    FileReviewRoundService.model.task_id == task_id,
                    FileReviewRoundService.model.status == "reviewing",
                ).execute()
        except Exception:
            logger.exception("review task force-fail failed, task_id=%s", task_id)
```

- [ ] **Step 4: 跑测试确认通过**

```bash
uv run --no-sync pytest test/test_file_review_spawn.py -v
```
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add rag/svr/file_review/spawn.py test/test_file_review_spawn.py
git commit -m "feat(file-review): spawn with thread safety"
```

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