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

# 终态判定统一导入 service 层 TERMINAL_TASK_STATUSES（本模块 tool 注册期不可顶层
# 触发 DB 依赖，沿用方法内延迟 import 惯例，在使用点取用）
_RUNNING_STATUSES = ("pending", "retrieving", "generating", "rendering")

# status 摘要裁剪阈值
_MAX_VALUE_KEYS = 40
_MAX_VALUE_CHARS = 80

# detail 摘要裁剪阈值：填写点条数上限（防大范本刷爆上下文）、锚文本截断长度
_MAX_DETAIL_POINTS = 30
_MAX_ANCHOR_CHARS = 60


class FillTemplateParam(ToolParamBase):
    """FillTemplate 工具参数声明。"""

    def __init__(self):
        self.meta: ToolMeta = {
            "name": "FillTemplate",
            "description": """模板（范本）工具。五个 action：

1. list_templates：列出当前已发布的范本（name/file_type/id），用户没指定范本时先调用它给用户挑选。
2. detail：查询范本详情。可按 template_id 精确查，或按 template_name 名称模糊查；可选 keyword 在范本填写点（中文名称/英文 key/锚文本）中匹配，用于回答"这个范本里有没有某某内容/这一条"。查询范围含草稿和已停用范本（结果会标注状态）。
3. fill：发起一次填写任务。需要 kb_ids（用于检索取数的知识库 ID 列表，JSON 数组字符串，如 '["kb1","kb2"]'）、可选 params（补充参数对象，可传 JSON 字符串）。范本可用 template_id 指定，也可只给 template_name（支持部分名称模糊匹配：唯一已发布命中会直接发起；多个命中会列出候选让用户挑）。任务提交后本工具会同步等待最多约 1 分钟，完成后直接返回字段值摘要；超时未完成会返回 task_id，让用户稍后用 action=status 查询。
4. status：按 task_id 查询填写任务进度/结果，含字段值摘要、待人工补充字段列表和成稿下载指引。
5. modify：就地修改已完成填写的成稿。params 传 {字段key: 新值} 对象，直接改到该范本最近一次已完成填写任务的成稿上（原下载/预览不变，不重新发起填写、不出确认卡）。可按 template_id 或 template_name 定位。

使用时机：用户询问某个范本的详情、包含哪些填写点、有没有某条内容时用 detail；用户想浏览全部范本时用 list_templates；用户想基于知识库内容按固定模板生成/完善文档时用 fill。用户只提到范本名称的一部分、或用「第一个/上面那个」指代上文列过的范本时，直接解析后调用 fill（传 template_name 或从上文工具结果取 template_id），不要反问用户要完整名称或 id。kb_ids 必须由用户提供（可结合知识库列表工具），不要编造。用户在填写完成后要求修改/补充/更正成稿里的某几个字段（如「把 approval_authority 改成 李港」「xx 那项填成 yy」）时，用 action=modify（params 传字段 key 和新值），绝不要重新 fill 或重新出确认卡——那会让用户感觉之前的填写全部丢失。""",
            "parameters": {
                "action": {
                    "type": "string",
                    "description": "操作类型：list_templates（列范本）/ detail（查范本详情与填写点）/ fill（发起填写）/ status（查进度）/ modify（就地修改已完成成稿的指定字段）。",
                    "enum": ["list_templates", "detail", "fill", "status", "modify"],
                    "required": True,
                },
                "template_id": {
                    "type": "string",
                    "description": "范本 id（list_templates/detail 返回的 id）。action=fill 时与 template_name 至少提供一个；action=detail 时与 template_name 至少提供一个。",
                    "default": "",
                    "required": False,
                },
                "template_name": {
                    "type": "string",
                    "description": "范本名称（支持部分名称模糊匹配）。action=detail/fill 未提供 template_id 时用它查找；fill 时唯一已发布命中会直接发起，多个命中会返回候选列表。",
                    "default": "",
                    "required": False,
                },
                "keyword": {
                    "type": "string",
                    "description": '关键词，在范本填写点的中文名称/英文 key/锚文本中匹配。action=detail 时可选，用于回答"某范本有没有某条内容"。',
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
                    "description": 'JSON 对象字符串。action=fill 时为可选补充参数；action=modify 时必填，为要修改的字段值对象（如 \'{"approval_authority": "李港"}\'，key 用填写点英文 key）。',
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
            if action == "detail":
                return self._template_detail(kwargs)
            if action == "fill":
                return self._fill(kwargs)
            if action == "status":
                return self._status(kwargs)
            if action == "modify":
                return self._modify(kwargs)
            return f"不支持的 action：{action or '(空)'}。可用 action：list_templates（列范本）/ detail（查范本详情与填写点）/ fill（发起填写）/ status（查进度）/ modify（就地修改成稿字段）。"
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
        rows, _total = TplTemplateService.get_list_page(tenant_id, status="published", page=1, size=20)
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

    # ---------- action: detail ----------

    def _template_detail(self, kwargs):
        from api.db.services.template_fill_service import (
            TplTemplateService,
            TplTemplateVersionService,
        )

        tenant_id = self._get_tenant_id()
        if not tenant_id:
            return "无法确定当前用户身份，请稍后重试。"

        template_id = str(kwargs.get("template_id") or "").strip()
        template_name = str(kwargs.get("template_name") or "").strip()
        keyword = str(kwargs.get("keyword") or "").strip()

        if template_id:
            found = TplTemplateService.get_owned(template_id, tenant_id)
            if found is None:
                return "范本不存在或无权访问，请用 action=list_templates 确认。"
            tpl = found.to_dict()
        elif template_name:
            rows, _total = TplTemplateService.get_list_page(tenant_id, keyword=template_name, page=1, size=50)
            # 名称精确相等的优先视为唯一命中，避免同名模糊项干扰
            # （仅在第一页 50 条内找精确命中；单租户范本量小，足够）
            candidates = [r for r in rows if str(r.get("name", "") or "").strip() == template_name] or rows
            if not candidates:
                return f"没有找到名称包含「{template_name[:100]}」的范本。可用 action=list_templates 查看已发布范本。"
            if len(candidates) > 1:
                lines = [f"名称包含「{template_name}」的范本有 {len(candidates)} 个，请指定 id："]
                for r in candidates:
                    lines.append(f"- {r.get('name', '')}（{self._status_cn(str(r.get('status', '') or ''))}，{r.get('file_type', '')}） id={r.get('id', '')}")
                return "\n".join(lines)
            tpl = candidates[0]
        else:
            return "请提供 template_id 或 template_name 之一（按名称查找支持模糊匹配）。"

        ver = TplTemplateVersionService.latest(str(tpl.get("id", "") or ""))
        placeholders = getattr(ver, "placeholders", None) if ver is not None else None
        if isinstance(placeholders, str):
            try:
                placeholders = json.loads(placeholders)
            except Exception:  # noqa: BLE001 — 历史脏数据兜底为空清单，不让 detail 整体失败
                placeholders = []
        if not isinstance(placeholders, list):
            # 合法 JSON 标量/对象等脏数据同样降级为空清单，不进迭代抛 TypeError
            placeholders = []
        placeholders = [p for p in placeholders if isinstance(p, dict)]
        return self._format_template_detail(tpl, placeholders, keyword)

    def _format_template_detail(self, tpl: dict, placeholders: list, keyword: str) -> str:
        header = f"范本：{tpl.get('name', '')}（{tpl.get('file_type', '')}，{self._status_cn(str(tpl.get('status', '') or ''))}） id={tpl.get('id', '')}"
        lines = [header]
        desc = str(tpl.get("description", "") or "").strip()
        if desc:
            lines.append(f"说明：{desc[:200]}")

        if not placeholders:
            lines.append("该范本尚未配置填写点（未完成 AI 识别或人工标记）。")
            return "\n".join(lines)

        lines.append(f"共 {len(placeholders)} 个填写点。")
        show = placeholders
        if keyword:
            kw = keyword.lower()
            show = [p for p in placeholders if kw in str(p.get("name", "") or "").lower() or kw in str(p.get("key", "") or "").lower() or kw in str(p.get("anchor", "") or "").lower()]
            if not show:
                lines.append(f"没有与「{keyword[:100]}」匹配的填写点。")
            else:
                lines.append(f"与「{keyword}」匹配的填写点有 {len(show)} 个：")

        for p in show[:_MAX_DETAIL_POINTS]:
            seg = f"- {p.get('name', '')}（{p.get('key', '')}）锚点「{str(p.get('anchor', '') or '')[:_MAX_ANCHOR_CHARS]}」"
            default = str(p.get("default_value", "") or "").strip()
            if default:
                seg += f" 默认值：{default[:_MAX_VALUE_CHARS]}"
            lines.append(seg)
        if len(show) > _MAX_DETAIL_POINTS:
            lines.append(f"…（其余 {len(show) - _MAX_DETAIL_POINTS} 个填写点略）")
        if not keyword:
            lines.append("如需确认本范本是否包含某条内容，请带上 keyword 再次查询。")
        return "\n".join(lines)

    @staticmethod
    def _status_cn(status: str) -> str:
        return {"draft": "草稿", "published": "已发布", "disabled": "已停用"}.get(status, status or "未知")

    # ---------- action: fill ----------

    def _fill(self, kwargs):
        from api.db.services.template_fill_service import (
            TERMINAL_TASK_STATUSES,
            TplFillTaskService,
            TplTemplateService,
            TplTemplateVersionService,
        )

        tenant_id = self._get_tenant_id()
        if not tenant_id:
            return "无法确定当前用户身份，请稍后重试。"

        template_id = str(kwargs.get("template_id") or "").strip()
        if not template_id:
            template_name = str(kwargs.get("template_name") or "").strip()
            if not template_name:
                return "请提供 template_id 或 template_name 之一（可先用 action=list_templates 查看已发布范本；按名称支持模糊匹配）。"
            template_id, err = self._resolve_fill_template_id(tenant_id, template_name)
            if err:
                return err

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
            return 'params 不是合法的 JSON 对象，请检查后重试（示例：\'{"project": "xx项目"}\'）。'

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

        # 延迟 import：直连 spawn 模块，复用 B 端同一条 daemon 线程执行链路（含防重入）
        from rag.svr.template_fill.spawn import spawn_fill_task

        spawn_fill_task(task_id)

        # 同步轮询至终态或超时（sleep 在测试中可被替换）
        waited = 0.0
        while waited < _MAX_WAIT_SECONDS:
            time.sleep(_POLL_INTERVAL)
            waited += _POLL_INTERVAL
            task = TplFillTaskService.get_owned(task_id, tenant_id)
            if task is not None and getattr(task, "status", "") in TERMINAL_TASK_STATUSES:
                return self._format_status(task)
        return f"填写任务已提交（task_id={task_id}），目前仍在进行中。请稍后用 action=status 携带该 task_id 查询进度与结果。"

    @staticmethod
    def _resolve_fill_template_id(tenant_id: str, template_name: str):
        """fill 按名称解析范本 id：精确同名优先，再取已发布；唯一命中返回 id，
        多命中/零命中/命中均未发布返回候选或原因（填表让 Agent/用户二选一）。"""
        from api.db.services.template_fill_service import TplTemplateService

        rows, _total = TplTemplateService.get_list_page(tenant_id, keyword=template_name, page=1, size=50)
        if not rows:
            return None, (f"没有找到名称包含「{template_name[:100]}」的范本。可用 action=list_templates 查看已发布范本。")
        candidates = [r for r in rows if str(r.get("name", "") or "").strip() == template_name] or rows
        published = [r for r in candidates if str(r.get("status", "") or "") == "published"]
        if not published:
            return None, ("命中的范本均未发布，暂不能发起填写。请先在 B 端「范本库」发布后再试。")
        if len(published) > 1:
            lines = [f"名称包含「{template_name[:100]}」的已发布范本有 {len(published)} 个，请指定 id："]
            for r in published:
                lines.append(f"- {r.get('name', '')}（{r.get('file_type', '')}） id={r.get('id', '')}")
            return None, "\n".join(lines)
        return str(published[0].get("id", "") or ""), None

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

    # ---------- action: modify ----------

    def _modify(self, kwargs):
        """就地修改：定位该范本最近一次 done 任务 → 合并 patch 值进 values →
        重渲染 → 覆盖写原成稿对象（同名对象，卡片下载/预览链接自动更新）→
        回写 values → 沉淀 patch 为默认值。不新建任务、不出确认卡。"""
        from api.db.services.template_fill_service import (
            TplFillTaskService,
            TplTemplateService,
            TplTemplateVersionService,
            _storage_get,
            _storage_put,
        )

        tenant_id = self._get_tenant_id()
        if not tenant_id:
            return "无法确定当前用户身份，请稍后重试。"

        template_id = str(kwargs.get("template_id") or "").strip()
        if not template_id:
            template_name = str(kwargs.get("template_name") or "").strip()
            if not template_name:
                return "请提供 template_id 或 template_name 之一（可先用 action=list_templates 查看范本）。"
            template_id, err = self._resolve_modify_template_id(tenant_id, template_name)
            if err:
                return err

        patch = self._parse_params(kwargs.get("params"))
        if not patch:
            return '请通过 params 提供要修改的字段值（JSON 对象字符串，如 \'{"approval_authority": "李港"}\'，key 用填写点英文 key）。'

        task = TplFillTaskService.latest_done(template_id, tenant_id)
        if task is None:
            return "该范本还没有已完成的填写任务，无法就地修改。请先用 action=fill 完成一次填写。"

        ver = TplTemplateVersionService.latest(template_id)
        placeholders = self._ver_placeholders(ver)
        if placeholders is None or not getattr(ver, "render_file_id", ""):
            return "该范本填写点配置缺失（可能已重新上传未识别），无法就地修改。请重新完成一次填写。"
        valid_keys = {str(p.get("key") or "") for p in placeholders} - {""}
        unknown = [k for k in patch if k not in valid_keys]
        if unknown:
            return (
                f"以下字段不在该范本填写点中：{'、'.join(unknown[:10])}。"
                "请用 action=detail（带 keyword）核对字段 key 后重试，不要编造 key。"
            )

        # 合并上次产值：render 存值、cells 存状态（非空→filled，空→not_found）
        vals = getattr(task, "values", None) or {}
        if isinstance(vals, str):
            try:
                vals = json.loads(vals)
            except Exception:  # noqa: BLE001 — 历史脏数据兜底为空基线
                vals = {}
        render = dict(vals.get("render") or {})
        cells = dict(vals.get("cells") or {})
        for k, v in patch.items():
            render[k] = v
            cells[k] = "filled" if str(v or "").strip() else "not_found"

        # 重渲染：读模板工作副本 → renderer 回填 → 覆盖写原成稿对象名
        from rag.svr.template_fill import renderer

        tpl = TplTemplateService.get_or_none(id=template_id)
        tpl_file_type = tpl.file_type if tpl and tpl.file_type else "docx"
        blob = _storage_get(template_id, ver.render_file_id)
        if not blob:
            return "模板工作副本缺失，无法就地修改。请重新完成一次填写。"
        addr_by_key = None
        if tpl_file_type == "xlsx":
            addr_by_key = {it["key"]: it.get("addr") for it in placeholders if it.get("key")}
        try:
            out = renderer.render(tpl_file_type, blob, render, addr_by_key)
        except Exception as e:  # noqa: BLE001 — 渲染失败原成稿不动，错误透传给用户
            return f"就地修改失败（渲染异常，原成稿未改动）：{e}"
        result_obj = str(getattr(task, "result_file_id", "") or "") or f"v{ver.version}_result_{task.id}.{tpl_file_type}"
        _storage_put(template_id, result_obj, out)

        if not TplFillTaskService.patch_values(task.id, {"cells": cells, "render": render}):
            return "成稿已更新，但填写记录回写失败，请稍后用 action=status 核对该任务字段值。"

        try:
            TplTemplateVersionService.sediment_defaults(
                template_id, ver.id, patch, override_keys=set(patch.keys()))
        except Exception:  # noqa: BLE001 — 沉淀失败不影响本次修改交付
            logger.warning("modify sediment_defaults failed, task=%s", task.id, exc_info=True)

        filled_total = sum(1 for v in render.values() if str(v or "").strip())
        lines = [f"已在原成稿上就地修改 {len(patch)} 个字段（task_id={task.id}，下载/预览链接不变）："]
        for k, v in patch.items():
            s = str(v)
            lines.append(f"- {k}: {s[:_MAX_VALUE_CHARS]}{'…' if len(s) > _MAX_VALUE_CHARS else ''}")
        lines.append(f"当前共填入 {filled_total}/{len(render)} 个字段，可点开原成稿核对修改处。")
        return "\n".join(lines)

    @staticmethod
    def _resolve_modify_template_id(tenant_id: str, template_name: str):
        """modify 按名称解析范本 id：精确同名优先，全状态（含草稿/停用）皆可
        （改的是历史成稿，与范本当前发布态无关）；唯一命中返回 id，多命中让用户挑。"""
        from api.db.services.template_fill_service import TplTemplateService

        rows, _total = TplTemplateService.get_list_page(tenant_id, keyword=template_name, page=1, size=50)
        if not rows:
            return None, (f"没有找到名称包含「{template_name[:100]}」的范本。可用 action=list_templates 查看范本。")
        candidates = [r for r in rows if str(r.get("name", "") or "").strip() == template_name] or rows
        if len(candidates) > 1:
            lines = [f"名称包含「{template_name[:100]}」的范本有 {len(candidates)} 个，请指定 id："]
            for r in candidates:
                lines.append(f"- {r.get('name', '')}（{r.get('file_type', '')}） id={r.get('id', '')}")
            return None, "\n".join(lines)
        return str(candidates[0].get("id", "") or ""), None

    @staticmethod
    def _ver_placeholders(ver) -> list | None:
        """版本 placeholders 容错解析（JSON 字符串/脏数据兜底），无版本返回 None。"""
        if ver is None:
            return None
        placeholders = getattr(ver, "placeholders", None)
        if isinstance(placeholders, str):
            try:
                placeholders = json.loads(placeholders)
            except Exception:  # noqa: BLE001 — 历史脏数据兜底为空清单
                placeholders = []
        if not isinstance(placeholders, list):
            placeholders = []
        return [p for p in placeholders if isinstance(p, dict)]

    # ---------- 状态摘要 ----------

    def _format_status(self, task) -> str:
        status = getattr(task, "status", "")
        if status in _RUNNING_STATUSES:
            return f"填写进行中（status={status}），task_id={getattr(task, 'id', '')}。请稍后再用 action=status 查询。"
        if status == "failed":
            err = getattr(task, "error", "") or "未知原因"
            return f"填写失败：{err}。可检查知识库内容后用相同参数重新发起（action=fill，task_id={getattr(task, 'id', '')}）。"

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
