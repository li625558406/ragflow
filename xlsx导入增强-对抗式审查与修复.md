# XLSX 导入增强 — 对抗式代码审查与修复报告

> 日期：2026-07-20
> 范围：表格导入非单元格值适配（P1–P5）新增/修改代码
> 方法：逐文件代码审阅 + 最小测试单元验证

---

## 一、审查范围

| 模块 | 文件 | 行数 |
|------|------|------|
| 后端适配器（新增） | `api/apps/services/spreadsheet_xlsx_adapter.py` | ~1500 |
| 协作 API 服务 | `api/apps/services/collaboration_api_service.py` | `import_xlsx` / `_generate_xlsx` / `get_doc_asset` |
| 协作 REST 路由（新增端点） | `api/apps/restful_apis/collaboration_api.py` | `GET /collaboration/documents/<doc_id>/assets/<asset_id>` |
| 前端表格编辑器 | `web/src/components/collaboration/spreadsheet-editor.tsx` | Univer 6 个免费 Preset 注册 |
| 前端 Yjs 协作 Hook | `web/src/components/collaboration/use-spreadsheet-collab.ts` | `rewriteAssetUrls` / `injectAssetTokens` / `stripAssetTokens` |

---

## 二、发现的 Bug 与修复

### Bug #1 — 前端 URL strip 留下孤立的 `&`【已修复】

**位置**：`web/src/components/collaboration/use-spreadsheet-collab.ts:64-78`

**问题**：
原实现
```ts
newUrl = url.replace(/[?&]token=[^&]*/g, '');
if (newUrl.endsWith('?')) newUrl = newUrl.slice(0, -1);
if (newUrl.endsWith('&')) newUrl = newUrl.slice(0, -1);
```
对 URL `/x?token=abc&foo=bar`，正则把 `?token=abc` 切掉，得到 `/x&foo=bar` —— 没有起始 `?`，是非法 URL。

**修复**：
```ts
const hasQ = url.includes('?');
newUrl = url.replace(/[?&]token=[^&]*/g, '');
// token 是第一个参数时把剩余首个 & 恢复为 ?
if (hasQ && !newUrl.includes('?') && newUrl.includes('&')) {
  newUrl = newUrl.replace('&', '?');
}
newUrl = newUrl.replace('?&', '?').replace('&&', '&');
if (newUrl.endsWith('?') || newUrl.endsWith('&')) {
  newUrl = newUrl.slice(0, -1);
}
```

**测试用例**（4 个）：
- `?token=abc&foo=bar` → `/x?foo=bar`
- `?token=abc` → `/x`
- `?foo=bar&token=abc` → `/x?foo=bar`
- 无 query → 保持原样

---

### Bug #2 — 导出时空样式单元格被跳过【已修复】

**位置**：`api/apps/services/spreadsheet_xlsx_adapter.py:_write_univer_sheets`

**问题**：
```python
value = _univer_cell_to_excel(cell_record)
if value is None:
    continue  # ← 空但有样式的单元格（背景/边框）会丢失
```

**修复**：仅在「无值」**且**「无样式」时跳过：
```python
value = _univer_cell_to_excel(cell_record)
style_id = cell_record.get("s") if isinstance(cell_record, dict) else None
if value is None and style_id is None:
    continue
cell = ws.cell(row=row_idx + 1, column=col_idx + 1, value=value)
if style_id is not None:
    style = styles.get(str(style_id))
    if style:
        _apply_style_to_cell(cell, style)
```

**测试用例**：手工构造 `cellData[1][1] = {"s": "0"}`，`styles[0] = {bg:{rgb:"FF0000"}}`，导出后用 openpyxl 读回，断言 `fill.fill_type == "solid"`。✅ 通过。

---

### Bug #3 — CF cellIs 规则 value 类型错误【已修复】

**位置**：`api/apps/services/spreadsheet_xlsx_adapter.py:_convert_cf_rule`

**问题**：
```python
"value": formula[0] if formula else 0
```
- `formula[0]` 是字符串（来自 openpyxl），Univer `INumberHighlightCell.value` 要求是 `number`。
- `between` / `notBetween` 需要数组 `[num1, num2]`，原实现只给单值。

**修复**：
```python
nums: list[float] = []
for raw in formula:
    try:
        nums.append(float(raw))
    except (TypeError, ValueError):
        continue  # 单元格引用等非数字 → 跳过

if operator in ("between", "notBetween"):
    value = [nums[0], nums[1]] if len(nums) >= 2 else (
        [nums[0], nums[0]] if nums else [0, 0]
    )
else:
    value = nums[0] if nums else 0
```

**测试用例**（3 个）：
- `greaterThan "5"` → `value == 5`（数字类型）
- `between ["1","10"]` → `value == [1.0, 10.0]`
- `equal ["$A$1"]` → 非数字被跳过，`value == 0` 不崩溃

---

## 三、其他观察（非阻塞，记录待观察）

| 项 | 说明 | 建议 |
|----|------|------|
| 重复导入产生孤立图片 | MinIO 中 `docs/{doc_id}/images/{uuid}` 不会被回收 | 后续：导入前清空该前缀，或加 GC 任务 |
| Token 刷新后旧 URL 失效 | 注入的 JWT 过期后，已打开的表格内图片可能 401 | 前端 401 时主动重注入；或 asset 端点放宽 TTL |
| 部分样式覆盖 | `_apply_style_to_cell` 按字段单独写，Univer 单元格的某些样式继承规则可能不完全等价 | 仅在用户反馈样式问题时处理 |
| 主题色（theme color）解析 | `_argb_to_rgb` 对 theme 返回 None，CF / 单元格色丢失 | 后续：解析 workbook theme XML + tint |

---

## 四、验证方式

测试脚本：`rag/tmp_test_xlsx_fixes.py`

运行：
```bash
cd D:/AI/ragflow2
python rag/tmp_test_xlsx_fixes.py
```

结果：
```
PASS  test_strip_token_first_param
PASS  test_strip_token_only_param
PASS  test_strip_token_subsequent
PASS  test_strip_token_no_query
PASS  test_empty_styled_cell_survives_round_trip
PASS  test_cf_cellis_numeric_value
PASS  test_cf_cellis_between_returns_array
PASS  test_cf_cellis_non_numeric_formula_skipped

8 passed, 0 failed
```

> 测试通过 stub `common.misc_utils` / `common.settings` 绕过 `api.apps.__init__`（依赖 quart），仅加载 `spreadsheet_xlsx_adapter` 单模块；URL strip 用例以 Python 复刻 TS 正则逻辑做等价验证。

---

## 五、回归影响评估

| 影响面 | 评估 |
|--------|------|
| 已存在表格（无图片/CF/样式）| ✅ 零影响 — 改动仅在新增分支内生效 |
| ydoc 兼容性 | ✅ 无需清理 — 之前 ydoc 已是 IWorkbookData 结构 |
| 缓存 | ✅ 无影响 — DB 字段不变 |
| 部署 | ⚠️ 需用户确认后手动部署：①前端 `npm run build` + scp + nginx reload；②后端 scp `spreadsheet_xlsx_adapter.py` + 重启容器 |

---

## 六、变更文件清单

| 文件 | 类型 |
|------|------|
| `web/src/components/collaboration/use-spreadsheet-collab.ts` | 修改（URL strip 修复） |
| `api/apps/services/spreadsheet_xlsx_adapter.py` | 修改（空样式单元格 + CF cellIs） |
| `rag/tmp_test_xlsx_fixes.py` | 新增（最小测试单元） |
