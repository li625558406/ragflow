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
    """把该 task 滞留在 reviewing/fixing 的轮次 CAS 置 failed，保证可重试。

    幂等且范围受限：只命中 status ∈ {reviewing, fixing} 的该 task 行——executor 已把该轮置
    done/failed 时命中 0 行，别的任务的滞留轮次也不受影响。fixing 必须一起收：
    进程在修复中被杀时轮次会停在 fixing，只认 reviewing 会让面板上永远转圈。
    """
    try:
        from api.db.db_models import DB
        from api.db.services.file_review_service import FileReviewRoundService
        with DB.connection_context():
            FileReviewRoundService.model.update(status="failed", error=error).where(
                FileReviewRoundService.model.task_id == task_id,
                FileReviewRoundService.model.status.in_(("reviewing", "fixing")),
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
