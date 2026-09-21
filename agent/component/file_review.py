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

# Begin 承载「当前已绑定审核任务」的输出键名。FileReview 节点产出的 task_id 只经 SSE
# 到前端、从不进 LLM 上下文——流程页签里用户说「修复严重问题」时 LLM 无从提供 task_id。
# 前端把已知 task_id（进度卡/历史记录派生）经 canvas.run(inputs=...) 用这个键名送入
# Begin，工具 _fix/_status 按「LLM 显式参数 > 本键注入」解析。
REVIEW_TASK_ID_INPUT_KEY = "review_task_id"

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
        self.max_rounds = 3       # 前端节点面板用；后端不消费——轮次上限的唯一权威是
                                  # FileReviewRoundService.MAX_FIX_ROUNDS / fix_rounds_left()，
                                  # 轮号一律由 next_round()（数全部轮次、含 failed）分配。
                                  # 刻意不用 max_completed_round_no：它只数 done/annotated，
                                  # 拿它取轮号会撞号覆盖失败轮产物，且新轮基线自引用。
        self.outputs = {
            "task_id": {"value": "", "type": "string"},
            "round_id": {"value": "", "type": "string"},
            # file_id 必须进 outputs：前端进度卡轮询端点只认 file_id（useFileReviewState
            # 按 file_id 轮询），而节点 inputs 是空 dict（_param.inputs 未定义），事件里
            # 无处可取——outputs 是 SSE node_finished 事件里唯一能带出它的通道。
            "file_id": {"value": "", "type": "string"},
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
            # 会把 ans 里的 \ 和 \g<n> 解释成转义 / 反向引用（如 "C:\Users\x.docx" 直接
            # 抛 bad escape \U，异常逸出连轮次行都建不起来，"a\nb" 还会被静默改写成真换行）。
            # lambda 返回的字面串不经模板解析，对不含反斜杠的输入行为完全等价。
            # _ans=ans 把本轮值绑成默认参数（lambda 立即被 re.sub 调用，语义与闭包捕获等价，
            # 但显式绑定可过 ruff B023「循环变量未绑定」）。
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
        except Exception:
            logger.warning("FileReview._begin_output failed", exc_info=True)
        return ""

    def _resolve_file_id(self) -> str:
        """待审核文件 id（= /documents/upload 返回的 uuid）。"""
        text = self._expand_refs(self._param.file_id or "")
        return text or self._begin_output(FILE_ID_INPUT_KEY)

    def thoughts(self) -> str:
        # canvas.run 的 node_started 事件对批内每个组件调 thoughts()，基类抛
        # NotImplementedError 会杀掉整条 SSE 流（TemplateFill/FanOut 均有同款覆写）。
        return "正在发起文件审核..."

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
        self.set_output("file_id", file_id)
        self.set_output("content", "已开始审核，批注结果将显示在「文件审核」面板中。")
