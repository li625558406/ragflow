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
