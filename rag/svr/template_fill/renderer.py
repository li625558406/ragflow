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
"""模板填写：渲染层。LLM 只产值，格式回填全由本层程序完成——
Word 用 docxtpl（Jinja2 语法与 {{key}} 占位符天然兼容，接受 file-like，
无需落盘临时文件），Excel 用 openpyxl 按注册表 addr（"<sheet>!<coord>"，
约定同 xlsx_utils）坐标直写：合并区写左上角天然合法，非左上角/定位失败的
格跳过不中断整批；只改 cell.value，样式随 cell 保留。"""
import io
import logging

logger = logging.getLogger(__name__)

MANUAL_MARK = "【待人工：{name}】"


def manual_mark(name: str) -> str:
    """manual 填写点的产值：渲染时落为可见的人工提示标记（截断超长名防脏输入）。"""
    return MANUAL_MARK.format(name=(name or "")[:50])


def render_docx(blob: bytes, values: dict) -> bytes:
    """用 docxtpl 渲染 Word 模板：values 的 key 对应文档内 {{key}} 占位符，
    未出现的 key 忽略、缺失的占位符保持原样。"""
    from docxtpl import DocxTemplate
    doc = DocxTemplate(io.BytesIO(blob))
    doc.render(values or {})
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def render_xlsx(blob: bytes, values: dict, addr_by_key: dict) -> bytes:
    """按注册表 addr（"<sheet>!<coord>"，按最后一个 "!" 拆分，兼容表名含 "!"）
    直写 Excel 单元格。脏输入健壮性：key 无产值 / addr 缺失或无 "!" → 预检查
    跳过；sheet 不存在（KeyError）/ coord 非法或 range 语法（ValueError/
    AttributeError）→ 跳过该格不中断整批；值为 None 不替换（openpyxl 会把
    None 落成空单元格，且超边界 coord 会被惰性创建，均非预期写入）。"""
    from openpyxl import load_workbook
    wb = load_workbook(io.BytesIO(blob))
    for key, addr in (addr_by_key or {}).items():
        val = (values or {}).get(key)
        if val is None or not addr or "!" not in addr:
            continue
        sheet, _, coord = addr.rpartition("!")
        try:
            wb[sheet][coord] = val
        except (KeyError, ValueError, AttributeError):
            # KeyError: sheet 不存在；ValueError: coord 非法/越界；
            # AttributeError: coord 是 range 语法 → 赋值落在 tuple 上
            logger.warning("xlsx render skip cell key=%s addr=%s", key, addr)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def render(file_type: str, blob: bytes, values: dict, addr_by_key: dict | None = None) -> bytes:
    """按文件类型分发：docx → docxtpl 模板渲染；其余（xlsx）→ 坐标直写。"""
    if file_type == "docx":
        return render_docx(blob, values)
    return render_xlsx(blob, values, addr_by_key or {})
