"""填写任务执行线程 spawn（B端 fill-task API 与画布节点共用）。

从 template_api._spawn_fill_task 抽取：防重入集合 + daemon 线程 +
线程启动失败兜底（集合 discard + 任务行 CAS 置 failed 供重试）。
executor 延迟 import——spawn 模块自身不拉起任何重依赖。
"""
import logging
import threading

logger = logging.getLogger(__name__)

__all__ = ["is_running", "spawn_fill_task"]

# 填写任务线程防重入：同一任务同时最多一个执行线程（spawn 时 add、线程 finally discard）
_running_lock = threading.Lock()
_running_tasks: set = set()

# 生产恒为 None；仅作测试注入点（monkeypatch spawn 模块属性可命中 _run 的名字解析），
# None 时 _run 内延迟 import 真正的 executor.execute_task
execute_task = None


def is_running(task_id: str) -> bool:
    with _running_lock:
        return task_id in _running_tasks


def spawn_fill_task(task_id: str) -> None:
    """起 daemon 线程跑填写 pipeline。线程内异常自行兜底，
    executor.execute_task 内部已把崩溃任务置 failed。"""
    with _running_lock:
        if task_id in _running_tasks:
            return
        _running_tasks.add(task_id)

    def _run():
        try:
            fn = execute_task  # 读模块全局（调用时解析，测试注入可见）
            if fn is None:
                from rag.svr.template_fill.executor import execute_task as fn
            fn(task_id)
        except Exception:
            logger.exception("fill task thread crashed, task_id=%s", task_id)
        finally:
            with _running_lock:
                _running_tasks.discard(task_id)

    try:
        threading.Thread(target=_run, daemon=True, name=f"tpl-fill-{task_id[:8]}").start()
    except Exception:
        # 线程启动失败：add 已执行而 finally 永不会跑，task_id 会永久滞留集合
        # 导致 retry 恒报「任务正在执行中」。锁内 discard + 任务行 CAS 置 failed 供重试。
        with _running_lock:
            _running_tasks.discard(task_id)
        logger.exception("fill task thread start failed, task_id=%s", task_id)
        try:
            from api.db.db_models import DB
            from api.db.services.template_fill_service import TplFillTaskService
            with DB.connection_context():
                TplFillTaskService.model.update(
                    status="failed", error="任务调度失败：后台线程启动异常，请重试").where(
                    TplFillTaskService.model.id == task_id,
                    TplFillTaskService.model.status == "pending").execute()
        except Exception:
            logger.exception("fill task spawn force-fail failed, task_id=%s", task_id)
