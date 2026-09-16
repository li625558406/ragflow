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
    """引用写错/上游没推：展开为空 → 回退 Begin 输出，不得把 '{begin@...}' 当 id。"""
    p = FileReviewParam()
    p.file_id = "{begin@review_file_id}"
    cpn = _make(p, FakeCanvas(begin_outs={"review_file_id": "upload-uuid-4"}, refs={}))
    cpn._invoke()
    assert rec["rounds"][0]["file_id"] == "upload-uuid-4"


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
            text = re.sub(r"\{" + re.escape(k) + r"\}", ans, text)
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
Expected: PASS

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

> **T6 → T9 交接契约（强制执行，来自 T6 executor 的两道审查实测结论）**
>
> 以下六条是 T6 实现完成后由质量复审**实测**得出的硬约束。不遵守会直接损坏数据或越权，不是风格建议。
>
> 1. **重试必须新建轮次，禁止把 `failed` 轮次原地重置为 `fixing` 重跑。** `_latest_version_name` 刻意排除当前轮自身，所以原地重跑会退回**原件**基线，只补剩余 pending 的补丁，再以同名对象 `frv-{task_id}-{file_version}` **覆盖**上一轮的成稿——上一批已置 `fixed` 的改动会从成稿里消失，而标注仍显示 `fixed`。实测复现：两轮改动 a/b，中途失败后原地重跑，成稿只剩 b 的修复。**必须走 `round_no` / `file_version` 递增的新轮次**，此时基线是已落盘版本，不丢。
> 2. **`status='failed'` 的轮次现在可能带 `minio_path`。** T6 修复轮顺序是「落盘 → 轮次收口 `done` → 置标注 `fixed`」，因此收口那一步抛错时轮次被兜底写成 `failed`，但成稿**已经落盘**（实测：`round=failed && minio_path=frv-…-v2 && summary=本轮修复 1 项`）。progress 端点与 T12 进度卡**不得**按「failed ⇒ 无成稿」渲染，否则会把已修好的成稿藏起来。判据是「有 `minio_path` 就有可下载成稿」，与 `status` 无关。
> 3. **`failed` 轮次不计入 `max_completed_round_no`（T2 既有口径），续轮会复用同一 `round_no` 与 `file_version`，即同一对象名。** 必须在 T9 明确取哪种口径：接受同名覆盖（简单，与「重试幂等」的设计一致），还是显式 `+1` 错开。**二选一写进代码注释**，不要留下未定义行为。
> 4. **必须提供「open 但已成稿」的人工兜底出口。** 除上条窗口外还有一种残留：落盘成功、轮次收口成功，但逐条置 `fixed` 时该标注写入失败 → 标注永远停在 `open`，而文档已修好、`find` 已被替换，重跑必然报「0 项未能自动修复」，**不会自愈**。annotations 端点必须提供把标注人工置 `wontfix` / `resolved` 的能力，否则面板上永远挂着一个假未闭环项。
> 5. **`task_id` 归属校验（越权闸门）。** `execute_task(task_id)` / `spawn_review_task(task_id)` / `_force_fail_round(task_id)` 全链路**只按 task_id 圈定，不含任何 tenant 谓词**（`round_row.tenant_id` 仅用于选 bucket，不参与鉴权）。T9 必须在入口把 `task_id` 归一为 UUID 并校验「该 task 的轮次归属当前登录租户」才允许 spawn / 读取 / 删除，否则可以越权触发、读取、收口他人审核。
> 6. **`error` 列的固定文案闸门只在 executor 兜底层。** 非 `FileReviewError` 的异常原文一律被替换成「服务端内部错误，请稍后重试（详见服务端日志）」（原文只进服务端日志），以避免 MySQL host:port / MinIO endpoint / 内网路径经 `error` 列透给前端。**T9 自行拼错误文案时同样不得把异常原文写进 `error` 列。**

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