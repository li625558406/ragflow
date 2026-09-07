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
"""C端对话 FillTemplate 工具：列范本 / 发起填写 / 查进度（模板填写 P3）。

照 agent/tools/email.py、crawl_fetch.py 的插件结构：FillTemplateParam(ToolParamBase)
声明 meta，FillTemplate(ToolBase) 提供 _invoke，被 component_class 按类名自动发现。
service 层全部延迟 import，避免工具注册期触发 DB/Quart 依赖。
"""
import json
import logging
import os
import time
from abc import ABC

from agent.tools.base import ToolBase, ToolMeta, ToolParamBase
from common.connection_utils import timeout

logger = logging.getLogger(__name__)

# 执行超时与同步轮询上限：与 email.py 同款 env 驱动；等待取 min(90, timeout-10)，
# 保证轮询不会吃满装饰器配额（超时后引导用户用 action=status 异步查询）。
_EXEC_TIMEOUT = int(os.environ.get("COMPONENT_EXEC_TIMEOUT", "60"))
_MAX_WAIT_SECONDS = min(90, max(_EXEC_TIMEOUT - 10, 10))
_POLL_INTERVAL = 3

_TERMINAL_STATUSES = ("done", "partial", "failed")
_RUNNING_STATUSES = ("pending", "retrieving", "generating", "rendering")

# status 摘要裁剪阈值
_MAX_VALUE_KEYS = 40
_MAX_VALUE_CHARS = 80


class FillTemplateParam(ToolParamBase):
    """FillTemplate 工具参数声明。"""

    def __init__(self):
        self.meta: ToolMeta = {
            "name": "FillTemplate",
            "description": """模板（范本）填写工具。用固定 Word/Excel 范本 + 用户知识库内容自动生成公文/报告成稿。三个 action：

1. list_templates：列出当前已发布的范本（name/file_type/id），用户没指定范本时先调用它给用户挑选。
2. fill：发起一次填写任务。需要 template_id（范本 id）、kb_ids（用于检索取数的知识库 ID 列表，JSON 数组字符串，如 '["kb1","kb2"]'）、可选 params（补充参数对象，可传 JSON 字符串）。任务提交后本工具会同步等待最多约 1 分钟，完成后直接返回字段值摘要；超时未完成会返回 task_id，让用户稍后用 action=status 查询。
3. status：按 task_id 查询填写任务进度/结果，含字段值摘要、待人工补充字段列表和成稿下载指引。

使用时机：用户想基于知识库内容按固定模板生成文档时使用。kb_ids 必须由用户提供（可结合知识库列表工具），不要编造。""",
            "parameters": {
                "action": {
                    "type": "string",
                    "description": "操作类型：list_templates（列范本）/ fill（发起填写）/ status（查进度）。",
                    "enum": ["list_templates", "fill", "status"],
                    "required": True,
                },
                "template_id": {
                    "type": "string",
                    "description": "范本 id（list_templates 返回的 id）。action=fill 时必填。",
                    "default": "",
                    "required": False,
                },
                "kb_ids": {
                    "type": "string",
                    "description": "知识库 ID 列表，JSON 数组字符串，如 '[\"kb123\"]'。action=fill 时必填。",
                    "default": "",
                    "required": False,
                },
                "params": {
                    "type": "string",
                    "description": "可选补充参数，JSON 对象字符串（param 模式字段直取此处值）。",
                    "default": "",
                    "required": False,
                },
                "task_id": {
                    "type": "string",
                    "description": "填写任务 id。action=status 时必填。",
                    "default": "",
                    "required": False,
                },
            },
        }
        super().__init__()


class FillTemplate(ToolBase, ABC):
    """C端对话范本填写工具。"""

    component_name = "FillTemplate"

    @timeout(_EXEC_TIMEOUT)
    def _invoke(self, **kwargs):
        if self.check_if_canceled("FillTemplate"):
            return
        try:
            action = str(kwargs.get("action") or "").strip()
            if action == "list_templates":
                return self._list_templates()
            if action == "fill":
                return self._fill(kwargs)
            if action == "status":
                return self._status(kwargs)
            return (
                f"不支持的 action：{action or '(空)'}。"
                "可用 action：list_templates（列范本）/ fill（发起填写）/ status（查进度）。"
            )
        except Exception as e:
            logger.exception("FillTemplate invoke failed")
            self.set_output("_ERROR", str(e))
            return f"范本填写执行失败：{e}"

    # ---------- action: list_templates ----------

    def _list_templates(self):
        from api.db.services.template_fill_service import TplTemplateService

        tenant_id = self._get_tenant_id()
        if not tenant_id:
            return "无法确定当前用户身份，请稍后重试。"
        rows, _total = TplTemplateService.get_list_page(
            tenant_id, status="published", page=1, size=20)
        if not rows:
            return "当前没有已发布的范本。请先在 B 端「范本库」上传并发布范本后再试。"
        lines = [f"共 {len(rows)} 个已发布范本："]
        for r in rows:
            name = r.get("name", "") if isinstance(r, dict) else getattr(r, "name", "")
            file_type = r.get("file_type", "") if isinstance(r, dict) else getattr(r, "file_type", "")
            tid = r.get("id", "") if isinstance(r, dict) else getattr(r, "id", "")
            lines.append(f"- {name}（{file_type}） id={tid}")
        lines.append("请告知要用哪个范本（提供 id）以及用于取数的知识库，即可发起填写。")
        return "\n".join(lines)

    # ---------- action: fill ----------

    def _fill(self, kwargs):
        from api.db.services.template_fill_service import (
            TplFillTaskService,
            TplTemplateService,
            TplTemplateVersionService,
        )

        tenant_id = self._get_tenant_id()
        if not tenant_id:
            return "无法确定当前用户身份，请稍后重试。"

        template_id = str(kwargs.get("template_id") or "").strip()
        if not template_id:
            return "请提供 template_id（可先用 action=list_templates 查看已发布范本）。"

        tpl = TplTemplateService.get_owned(template_id, tenant_id)
        if tpl is None:
            return "范本不存在或无权访问，请用 action=list_templates 确认。"
        if tpl.status != "published":
            return "该范本尚未发布，暂不能发起填写。请先在 B 端「范本库」发布后再试。"

        ver = TplTemplateVersionService.latest(template_id)
        if ver is None or not getattr(ver, "render_file_id", ""):
            return "该范本未配置填写点，请先在 B 端完成填写点配置后再发起填写。"

        kb_ids = self._parse_kb_ids(kwargs.get("kb_ids"))
        if not kb_ids:
            return "请提供 kb_ids（用于检索取数的知识库 ID 列表，JSON 数组字符串，如 '[\"kb123\"]'）。"

        params = self._parse_params(kwargs.get("params"))
        if params is None:
            return "params 不是合法的 JSON 对象，请检查后重试（示例：'{\"project\": \"xx项目\"}'）。"

        from common.misc_utils import get_uuid
        task_id = get_uuid()
        TplFillTaskService.insert(
            id=task_id,
            template_id=template_id,
            template_version_id=ver.id,
            kb_ids=kb_ids,
            params=params,
            status="pending",
            source="chat",
            flow_instance_id="",
            tenant_id=tenant_id,
            created_by=tenant_id,
        )

        # 延迟 import：复用 B 端同一条 daemon 线程执行链路（含防重入）
        from api.apps.restful_apis import template_api
        template_api._spawn_fill_task(task_id)

        # 同步轮询至终态或超时（sleep 在测试中可被替换）
        waited = 0.0
        while waited < _MAX_WAIT_SECONDS:
            time.sleep(_POLL_INTERVAL)
            waited += _POLL_INTERVAL
            task = TplFillTaskService.get_owned(task_id, tenant_id)
            if task is not None and getattr(task, "status", "") in _TERMINAL_STATUSES:
                return self._format_status(task)
        return (
            f"填写任务已提交（task_id={task_id}），目前仍在进行中。"
            "请稍后用 action=status 携带该 task_id 查询进度与结果。"
        )

    # ---------- action: status ----------

    def _status(self, kwargs):
        from api.db.services.template_fill_service import TplFillTaskService

        task_id = str(kwargs.get("task_id") or "").strip()
        if not task_id:
            return "请提供 task_id（发起填写时返回的任务 id）。"
        tenant_id = self._get_tenant_id()
        if not tenant_id:
            return "无法确定当前用户身份，请稍后重试。"
        task = TplFillTaskService.get_owned(task_id, tenant_id)
        if task is None:
            return "任务不存在或无权访问，请确认 task_id 是否正确。"
        return self._format_status(task)

    # ---------- 状态摘要 ----------

    def _format_status(self, task) -> str:
        status = getattr(task, "status", "")
        if status in _RUNNING_STATUSES:
            return (
                f"填写进行中（status={status}），task_id={getattr(task, 'id', '')}。"
                "请稍后再用 action=status 查询。"
            )
        if status == "failed":
            err = getattr(task, "error", "") or "未知原因"
            return (
                f"填写失败：{err}。"
                f"可检查知识库内容后用相同参数重新发起（action=fill，task_id={getattr(task, 'id', '')}）。"
            )

        # done / partial：字段值摘要 + 待人工字段 + 下载指引
        tid = getattr(task, "id", "")
        vals = getattr(task, "values", None) or {}
        if isinstance(vals, str):
            try:
                vals = json.loads(vals)
            except Exception:  # noqa: BLE001 — 历史脏数据兜底为空摘要，不让 status 整体失败
                vals = {}
        render = vals.get("render") or {}
        cells = vals.get("cells") or {}

        if status == "partial":
            lines = [f"填写部分完成（存在待人工字段），task_id={tid}，已生成字段值如下："]
        else:
            lines = [f"填写完成，task_id={tid}，字段值如下："]
        for i, (k, v) in enumerate(render.items()):
            if i >= _MAX_VALUE_KEYS:
                lines.append(f"…（其余 {len(render) - _MAX_VALUE_KEYS} 个字段略）")
                break
            s = str(v)
            lines.append(f"- {k}: {s[:_MAX_VALUE_CHARS]}{'…' if len(s) > _MAX_VALUE_CHARS else ''}")

        manual_keys = [k for k, st in cells.items() if st in ("manual", "not_found")]
        if manual_keys:
            lines.append("待人工补充字段：" + "、".join(manual_keys))
        lines.append(f"下载生成稿：B 端「范本库」→ 填写任务（task_id={tid}）。")
        return "\n".join(lines)

    # ---------- 辅助 ----------

    def _get_tenant_id(self) -> str:
        if hasattr(self, "_canvas") and self._canvas:
            try:
                return self._canvas.get_tenant_id() or ""
            except Exception:  # noqa: BLE001 — canvas 实现异常类型不一，兜底空串
                return ""
        return ""

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
    def _parse_params(raw):
        """params 容错解析：dict 直接用；字符串须为 JSON 对象；其余/失败返回 None。"""
        if raw is None or raw == "":
            return {}
        if isinstance(raw, dict):
            return raw
        try:
            parsed = json.loads(str(raw))
        except Exception:  # noqa: BLE001 — 用户/LLM 输入不可信，解析失败统一提示
            return None
        return parsed if isinstance(parsed, dict) else None
