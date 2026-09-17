# test/test_file_review_e2e.py
"""文件审核端到端：5 端点全链路 + 多轮 happy path + 修复+标注+下载。

参照 test_file_review_api.py 的 _load_api 模式（无需 client fixture），
并 monkeypatch rag.svr.file_review.executor._call_llm 绕开真实 LLM；
后台线程用 execute_task(round_id) 同步执行（绕开 spawn_mod.spawn_review_task）。
"""
import asyncio
import io
import os
import sys
import types
from importlib.util import module_from_spec, spec_from_file_location
from types import SimpleNamespace

import pytest
from docx import Document

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from api.db.db_models import DB, FileReviewAnnotation, FileReviewRound
from api.db.services.file_review_service import (
    MAX_FIX_ROUNDS,
    FileReviewAnnotationService,
    FileReviewRoundService,
)
from common.constants import RetCode
from common.misc_utils import get_uuid

# ── Fixture：LLM 桩 ──────────────────────────────────
PFX = "__test_fr_e2e__"

# 区分 review / fix 的字面量：executor._run_review_round 用模板的 system_prompt
# （如「投标文件格式审核专家」）+ user_prompt；executor._run_fix_round 用 FIX_SYSTEM
# （首句「你是文档修复助手」）+ _build_fix_prompt 含「请为每条问题给出最小改动的
# 替换方案」。模板真实字面量见 db_models._PRESET_REVIEW_TEMPLATES。
_FIX_SYSTEM_MARKER = "你是文档修复助手"        # executor.FIX_SYSTEM 字面量
_FIX_USER_MARKER = "请为每条问题给出最小改动"


@pytest.fixture
def mock_llm(monkeypatch):
    """桩 _call_llm：review 返 1 条 high 标注；fix 返空 patch 列表（不改文档但收口）。"""
    def fake_review_json():
        return (
            '{"summary":"high:1 medium:0 low:0","annotations":[{'
            '"matched_text":"投标人须","type":"format","severity":"high",'
            '"issue":"e2e test issue","suggestion":"e2e test suggestion"}]}'
        )

    def fake_fix_json():
        return '{"patches":[],"summary":"本轮修复 0 项（mock）"}'

    def fake_call_llm(tenant_id, system_prompt, user_prompt):
        # fix 调用：system_prompt == FIX_SYSTEM；review 调用：模板 system_prompt + 用户模板
        if (system_prompt and _FIX_SYSTEM_MARKER in system_prompt) or \
                (user_prompt and _FIX_USER_MARKER in user_prompt):
            return fake_fix_json()
        return fake_review_json()

    monkeypatch.setattr('rag.svr.file_review.executor._call_llm', fake_call_llm)
    # 显式断言「本用例不依赖 KB/ES」：本模块从不传 kb_ids，检索本就走不到；
    # 加这层 patch 是把它变成**被断言的事实**，而非结构上的偶然。
    monkeypatch.setattr('rag.svr.file_review.executor._retrieve_chunks', lambda *a, **k: [])
    return {'call_llm': fake_call_llm}


# ── Api 加载（同 test_file_review_api.py） ─────────────────────
TENANT = "u1"
_API_PATH = os.path.abspath(os.path.join(
    os.path.dirname(__file__), "..", "api", "apps", "restful_apis", "file_review_api.py"))


def _noop_decorator(f=None, *a, **kw):
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


def _call(endpoint, *, method="POST", path="/", body=None, **kwargs):
    """真实 Quart 请求上下文调端点，返回 JSON 业务体。

    注意 endpoint 被真实的 add_tenant_id_to_kwargs 包着，签名是 (**kwargs)：
    调用时**必须**全部用关键字传参，否则路径参数会被丢进 *args、漏到不了 endpoint。
    """
    from quart import Quart
    app = Quart(__name__)
    app.register_blueprint(_api.manager)

    async def _inner():
        async with app.test_request_context(path, method=method, json=body):
            resp = await endpoint(**kwargs)
        return await resp.get_json()
    return asyncio.run(_inner())


# ── Storage 替身 + 真实 docx 构造（同 test_file_review_executor.py） ────
class _FakeStorage:
    """内存对象存储替身：只实现 executor 用到的 get/put 两个方法。"""

    def __init__(self):
        self.blobs = {}

    def get(self, bucket, name):
        return self.blobs.get((bucket, name))

    def put(self, bucket, name, blob):
        self.blobs[(bucket, name)] = blob
        return True


_BODY = ("这是一段用于通过最小正文长度校验的填充文字，用来模拟真实招标文件的正文内容，"
         "确保审核链路的正文装配不会被最小长度闸门提前拦下。")


def _docx(paragraphs):
    d = Document()
    for t in paragraphs:
        d.add_paragraph(t)
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


def _seed_file(file_id: str, blob: bytes, fstore: _FakeStorage, tenant_id: str = TENANT):
    """把 docx 字节塞到 fstore 中：executor._load_original_blob 三级兜底里
    `{tenant}-downloads` 桶的最后一级一定命中，因此只放那里就够。"""
    fstore.blobs[(f"{tenant_id}-downloads", file_id)] = blob


@pytest.fixture
def fstore(monkeypatch):
    """用内存存储替身替换存储后端。

    executor.py:29 是 `from common import settings`，即 executor.settings 与
    file_review_api.settings 是**同一个模块对象**；故只需 patch
    common.settings.STORAGE_IMPL 一处，两个调用方同时命中（monkeypatch 会在
    用例结束自动回滚这个模块属性）。
    """
    from common import settings as common_settings
    st = _FakeStorage()
    monkeypatch.setattr(common_settings, "STORAGE_IMPL", st)
    return st


# ── Helper：建 task + round_1 + 同步跑 ───────────────────
def _make_round_one(file_id: str, *, template_id: str = "bid_doc_format",
                     user_query: str = "审核这份招标文件") -> tuple:
    """直接构造 task + round_1（无 HTTP 启动端点），返回 (task_id, round_id)。

    与 _run_round_sync / 后续断言必须共享同一连接上下文：executor.get_by_task 不
    自带 connection_context，下一调用若开新 context，peewee 连接池切连接时 MySQL
    REPEATABLE READ 看不到刚写的行、状态机停在 reviewing 不前。
    """
    task_id = get_uuid()
    rid = FileReviewRoundService.create_round(
        task_id=task_id, file_id=file_id, round_no=1,
        template_id=template_id, user_query=user_query,
        file_version="v1", status="reviewing",
        tenant_id=TENANT, created_by=TENANT,
    )
    return task_id, rid


def _run_round_sync(task_id: str):
    """同步跑一轮 executor：execute_task 按 task_id 取该 task 最新轮次推进。

    注：实际签名只接 task_id（executor.py:94），不是 round_id。
    execute_task 是同步函数，Service 层写各自在 @DB.connection_context() 内提交，
    无异步落盘竞态，故不需要 sleep。
    """
    from rag.svr.file_review.executor import execute_task
    execute_task(task_id)


def _cleanup():
    """按 file_id 前缀清本模块写的行。

    **不能按 task_id 前缀**：task_id 由 get_uuid() 生成（uuid1().hex），不带 PFX；
    按 task_id.startswith(PFX) 恒删 0 行，会让残留数据在重复运行时把
    「open→resolved」「标注已落库」等断言退化成平凡真。file_id 全部是 PFX + "fN"。
    """
    FileReviewAnnotation.delete().where(FileReviewAnnotation.file_id.startswith(PFX)).execute()
    FileReviewRound.delete().where(FileReviewRound.file_id.startswith(PFX)).execute()


@pytest.fixture(scope="module", autouse=True)
def _tables_and_cleanup():
    """建表 + 预置 5 模板 + 兜底清理（不调 migrate_db，沿用 _seed_file_review_templates 的幂等）。

    模块级保持 DB 连接常开（reuse_if_open=True），让各 test 在同一连接上读写，避开
    peewee + MySQL REPEATABLE READ 的连接切换快照问题。
    """
    from api.db import db_models as dbm
    from api.db.db_models import FileReviewTemplate as _FRT
    DB.connect(reuse_if_open=True)
    try:
        for m in (_FRT, FileReviewRound, FileReviewAnnotation):
            if not m.table_exists():
                m.create_table(safe=True)
        dbm._seed_file_review_templates()
        _cleanup()
        yield
    finally:
        try:
            _cleanup()
        finally:
            DB.close()


# ── E2E 1：两轮 happy path ────────────────────────────────
def test_e2e_two_rounds(monkeypatch, mock_llm, fstore):
    """Round 1 review → annotated → fix → Round 2 annotated。
    验证：round_no 递增、status 流转、annotation 写库。"""
    file_id = PFX + "f1"
    blob = _docx(["投标人须按本招标文件要求编制投标文件" + _BODY, "其余内容" + _BODY])
    _seed_file(file_id, blob, fstore)

    # 屏蔽后台线程派发（fix 端点内部经 Service 层 admit_fix_round 调 spawn_review_task；
    # 它函数内延迟 import 的正是这个模块对象，故按模块打桩即可命中 —— 不再经 _api.spawn_mod）
    from rag.svr.file_review import spawn as spawn_mod

    monkeypatch.setattr(spawn_mod, "spawn_review_task", lambda tid: None)

    with DB.connection_context():
        task_id, rid1 = _make_round_one(file_id)
        _run_round_sync(task_id)

        r1 = FileReviewRoundService.model.get(FileReviewRoundService.model.id == rid1)
        assert r1.status == 'annotated', f"R1 status={r1.status}, expected annotated"
        # list_by_file 是本文件全部标注（跨任务）；file_id 在本用例唯一，故即本轮产物。
        anns = FileReviewAnnotationService.list_by_file(file_id)
        assert len(anns) == 1, f"annotations={len(anns)}"
        assert anns[0].severity == 'high', f"severity={anns[0].severity!r}"
        assert anns[0].status == 'open', f"status={anns[0].status!r}"

    # 触发修复（HTTP）
    fix_endpoint = _api.fix_review
    body = _call(fix_endpoint, task_id=task_id, body={'levels': ['high']})
    assert body['code'] == RetCode.SUCCESS, body
    rid2 = body['data']['round_id']
    assert body['data']['round_no'] == 2
    assert body['data']['status'] == 'fixing'

    with DB.connection_context():
        _run_round_sync(task_id)

        r2 = FileReviewRoundService.model.get(FileReviewRoundService.model.id == rid2)
        # 修复轮 mock LLM 返空 patches → executor 走 _run_fix_round 的「合法但空」分支，
        # 确定性收口 status='done'（executor.py:189-193）。'annotated' 只可能由
        # _run_review_round 写，本路径永远到不了，故不加 OR 放宽。
        assert r2.status == 'done', f"R2 status={r2.status}, expected done"
        assert '保持原样' in (r2.summary or ''), f"R2 summary={r2.summary!r}"
        assert r2.round_no == 2


# ── E2E 2：state 端点读出完整对象 ─────────────────────────
def test_e2e_state_endpoint(monkeypatch, mock_llm, fstore):
    """state 端点应返回 file_id / rounds[] / current / annotations[] / counts。
    验证：response 字段与 IFileReviewState 对齐（前端契约）。"""
    file_id = PFX + "f2"
    blob = _docx(["投标人须按本招标文件要求编制投标文件" + _BODY, "其余内容" + _BODY])
    _seed_file(file_id, blob, fstore)
    with DB.connection_context():
        task_id, rid1 = _make_round_one(file_id)
        _run_round_sync(task_id)

    state_endpoint = _api.review_state  # 无 tenant_id（读路径不限租户）
    body = _call(state_endpoint, method='GET', file_id=file_id)
    assert body['code'] == RetCode.SUCCESS, body
    payload = body['data']

    # T10 类型契约：file_id / task_id / rounds / current / doc / annotations /
    #   annotation_counts / fix_rounds_left / max_fix_rounds
    for field in ['file_id', 'task_id', 'rounds', 'current', 'doc',
                  'annotations', 'annotation_counts', 'fix_rounds_left',
                  'max_fix_rounds']:
        assert field in payload, f"state 端点缺字段 {field}"

    assert payload['file_id'] == file_id, f"file_id={payload['file_id']!r}"
    assert len(payload['rounds']) == 1, f"rounds={len(payload['rounds'])}"
    assert payload['rounds'][0]['status'] == 'annotated', \
        f"round status={payload['rounds'][0]['status']!r}"
    assert payload['annotation_counts']['high'] >= 1, \
        f"counts={payload['annotation_counts']}"
    # 还没发起过修复（只有 round_1 review）→ fix_rounds_left 应等于 max_fix_rounds。
    # 两边同时算错也能通过，故把 max_fix_rounds 钉到字面量（file_review_service.py:64）。
    assert payload['max_fix_rounds'] == MAX_FIX_ROUNDS, \
        f"max_fix_rounds={payload['max_fix_rounds']}"
    assert payload['fix_rounds_left'] == MAX_FIX_ROUNDS, \
        f"fix_rounds_left={payload['fix_rounds_left']}"


# ── E2E 3：annotation 状态修改端点 ─────────────────────────
def test_e2e_annotation_status(monkeypatch, mock_llm, fstore):
    """标注 open → resolved 切换。"""
    file_id = PFX + "f3"
    blob = _docx(["投标人须按本招标文件要求编制投标文件" + _BODY, "其余内容" + _BODY])
    _seed_file(file_id, blob, fstore)
    with DB.connection_context():
        task_id, rid1 = _make_round_one(file_id)
        _run_round_sync(task_id)
        anns = FileReviewAnnotationService.list_by_file(file_id)
        assert len(anns) == 1, f"annotations={len(anns)}"
        ann_id = anns[0].id
        # 前置状态必须是 open，否则「open→resolved」这条转换没被真正验证
        assert anns[0].status == 'open', f"pre status={anns[0].status!r}"

    annotation_endpoint = _api.update_annotation_status  # 入参名 annotation_id
    body = _call(annotation_endpoint, annotation_id=ann_id, body={'status': 'resolved'})
    assert body['code'] == RetCode.SUCCESS, body
    assert body['data']['status'] == 'resolved', f"data={body['data']}"

    with DB.connection_context():
        a = FileReviewAnnotationService.model.get(
            FileReviewAnnotationService.model.id == ann_id)
        assert a.status == 'resolved', f"persisted status={a.status!r}"


# ── E2E 4：templates 端点列出预置 ─────────────────────────
# T1 迁移 seed 的 5 套预置 id（db_models._PRESET_REVIEW_TEMPLATES）。
_PRESET_IDS = {
    "bid_doc_format",
    "bid_response_complete",
    "bid_substantive_clause",
    "bid_qualification",
    "bid_price_review",
}


def test_e2e_templates_list():
    """5 套预置模板（T1 迁移）应全部可列。

    不用 `len(templates) == 5`：list_enabled 返回全库 tenant_id=="" 且 enabled 的模板，
    同库其他套件（如 test_file_review_service.py）会临时造这类行，个数相等是脆断言。
    改为断言「这 5 个 id 都在」，更强且不受他方残留影响。
    """
    templates_endpoint = _api.list_review_templates
    body = _call(templates_endpoint, method='GET')
    assert body['code'] == RetCode.SUCCESS, body
    templates = body['data']['templates']
    ids = {t['id'] for t in templates}
    assert _PRESET_IDS <= ids, f"缺少预置模板：{_PRESET_IDS - ids}"
    for t in templates:
        assert {'id', 'name', 'description', 'annotation_types'} <= set(t.keys()), \
            f"模板字段缺失：{sorted(t.keys())}"


# ── E2E 5：download 端点流式返回字节 ────────────────────────
def test_e2e_download_endpoint(monkeypatch, mock_llm, fstore):
    """fix 端点后产生 minio_path 的 round → download 端点应返回非空字节流。

    这里不跑真 patcher（mock fix LLM 返空 patches，executor 不会落盘），
    直接 mock round 收尾时带 minio_path 的状态，再用 fstore 注入字节。
    """
    file_id = PFX + "f5"
    task_id, rid1 = _make_round_one(file_id)
    # 下载端点只读 round.minio_path 指向的对象，不读原件，故无需 _seed_file。
    bucket = f"{TENANT}-downloads"
    key_name = f"frv-{task_id}-v1"
    fstore.blobs[(bucket, key_name)] = b"fake-docx-bytes"

    with DB.connection_context():
        FileReviewRoundService.update_status(
            rid1, 'annotated',
            minio_path=key_name, file_version='v1',
            summary='mock with minio_path',
        )

    download_endpoint = _api.download_review_version
    # 走真实 Quart HTTP（download 端点返回 Response 对象，不进 _call 抽象）
    from quart import Quart
    app = Quart(__name__)
    app.register_blueprint(_api.manager)

    async def _fetch():
        async with app.test_request_context(
            f"/file/review/{task_id}/v1/download", method='GET',
        ):
            resp = await download_endpoint(task_id=task_id, file_version='v1')
        return resp
    resp = asyncio.run(_fetch())
    assert resp.status_code == 200
    # Quart Response.get_data 是 async；await 取字节
    body_bytes = asyncio.run(resp.get_data())
    assert body_bytes == b"fake-docx-bytes"
