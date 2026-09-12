# 范本 AI 识别准确性与格式保真改造设计

日期：2026-09-12
状态：设计完成，未实施

## 背景与问题

B端范本库上传模板后 AI 识别填写点存在三类问题（用户实测反馈）：

1. **标签文字变蓝**：LLM 把字段标签本身（如「投标人名称：」）选为 anchor，替换后标签被占位符覆盖，成稿中标签位置变蓝（AI 值颜色）。
2. **格式错乱**：`docx_utils._replace_in_paragraph` 跨 run 时整段重写进首 run、清空其余，段内混合格式（局部加粗/字号）全部丢失。
3. **模板原文被覆盖**：anchor 选到了模板里本该保留的正文原文（如正文中的「工程名称」几个字），填写后被 AI 值覆盖，原文丢失。

手动兜底现状：手动添加填写点需手打 anchor 文本且须与模板原文一字不差，体验差。

## 根因定位

| 症状 | 根因 | 位置 |
|------|------|------|
| ①③ | LLM 选错 anchor 位置（标签/正文原文），识别阶段无拦截；3dac0d8b 只防了默认值派生 | `rag/svr/template_fill/detector.py` |
| ② | 跨 run 替换采用整段重写策略（注释明示「牺牲段内混合格式」） | `rag/svr/template_fill/docx_utils.py:125` |
| 手动兜底 | 预览无划选交互，addr 未透传给前端 | `web/src/pages/template-fill/detail.tsx` + 预览接口 |

## 方案

### 第 1 节：识别准确性后处理（治①③，`detector.py`）

双层防御，代码层不信任 LLM：

1. **prompt 强化**（`DETECT_SYSTEM`）：明确 anchor 只能是留白标记（下划线串/连续空格/括号提示）或已填现值，禁止选字段标签（冒号结尾引导词）与正文叙述文字。
2. **代码层后处理**（`parse_detection_response` 内）：
   - anchor 命中标签型（`_LABEL_ANCHOR_RE`，复用 3dac0d8b 引入的正则）→ **收缩修正**：在同行候选原文中找标签之后的留白串（`_{2,}` / 连续全角空格 / 括号提示），找到则 anchor 改写为该留白串；找不到 → 丢弃该项。
   - anchor 为实心文字（无留白/空白特征）且同行存在留白特征 → 同样收缩到留白；整行无留白特征 → 保留（已填范本的现值是合法 anchor），打 `low_confidence: true` 标记。
   - `low_confidence` 落库透传前端：填写点配置表格该行加警示底色，提醒人工重点检查（可正常使用，不阻断保存）。

### 第 2 节：格式保真替换（治②，`docx_utils.py`）

`_replace_in_paragraph` 跨 run 路径改造：

- 定位 anchor 首次出现覆盖的 **run 区间 [i..j]**：首 run 保留 anchor 前文本 + 接替换值，尾 run 保留 anchor 后文本，中间 run 清空。
- 区间外 run 原样不动 → 段内其他位置格式完整保留；仅 anchor 跨越的 run 退化为首 run 格式（最小不可避代价）。
- 含超链接/域段落仍走 `_replace_via_run_concat` 不变；同段多处出现保持「全部替换」语义（循环处理）。
- 单 run 内快路径不变。

对抗性单测：anchor 等于整段 / 单 run 内 / 跨 2-3 个 run / 段首 / 段尾 / 多次出现 / anchor 不存在 / 与超链接段落混合。

### 第 3 节：B端预览划选手动标记（`detail.tsx` + 预览接口）

- 后端：预览接口 items 增加 `addr` 字段（`docx_utils` 内部已产出，透传即可），前端不猜正文/表格定位。
- 前端：Word 预览区鼠标划选文字 → 旁浮「标记为填写点」按钮 → 右侧表格追加一行（anchor=选中文字、addr=该行 addr、key 自动生成占位如 `field_2`），保存配置后生效；误选可删除。
- xlsx 不做划选（坐标直写无格式问题，继续手动加行 + anchor 反查）。

## 改动清单

| 文件 | 改动 |
|------|------|
| `rag/svr/template_fill/detector.py` | prompt 强化 + parse 后处理（收缩修正/丢弃/low_confidence） |
| `rag/svr/template_fill/docx_utils.py` | `_replace_in_paragraph` 跨 run 区间替换改造 |
| 预览接口（`api/apps/restful_apis/template_api.py:474` preview 端点） | items 透传 addr |
| `web/src/pages/template-fill/detail.tsx` | 划选交互 + low_confidence 行警示底色 |
| `web/src/pages/template-fill/placeholder-table.tsx` | low_confidence 警示样式（如需） |

## 约束

- 不改变手动占位符 `{{key}}` 直通、key 归一化、validate 校验等既有语义。
- 已填范本（detected 默认值派生）行为不受影响：实心 anchor 合法路径保留。
- 手动添加行 anchor 反查逻辑（`validate_placeholders`）不变。
- 前端文案只用中文。

## 验证

- detector 后处理纯函数单测：标签收缩成功/失败丢弃、实心 anchor 低置信、已填现值保留。
- docx 替换单测：见第 2 节对抗性清单。
- 手动链路：真实范本上传 → 划选标记 → 保存 → 测试填写，确认工作副本中 anchor 正确替换为 `{{key}}` 且段内其他格式保留。
