# 范本 AI 识别加固设计（页眉页脚/文本框/内容控件 + 同形留白 + 解析与警示加固）

- 日期：2026-09-13
- 状态：设计完成，待实施
- 关联：`2026-09-12-template-detect-accuracy-design.md`（上一轮识别准确性改造）、`2026-09-07-template-fill-design.md`（模板填写系统总设计）

## 1. 背景与问题

2026-09-13 对 B 端范本库「上传后 AI 识别」链路做可信度/识别度评估，结论：候选已进入识别范围的前提下可信度高（多层代码校验 + low_confidence 警示 + 人工确认闭环），但存在四类结构性缺口：

1. **页眉/页脚/文本框/内容控件盲区**（最大）：`docx_utils._build_addr_map` 只遍历 body 直系 `w:p`/`w:tbl`。公文/投标函类范本常把盖章位、日期、单位名称放在页眉页脚或文本框（`w:txbxContent`）里——既不进候选、也无法渲染替换，且用户无感知。body 直系 `w:sdt`（内容控件）包裹的段落整块跳过。
2. **同段落同形留白静默丢失**：识别 merge 按 `(addr, anchor)` 去重，同段两个相同留白串（如「姓名：＿＿＿ 电话：＿＿＿」）第二项被丢弃。
3. **LLM 输出解析脆弱**：`parse_detection_response` 用贪婪正则 `\[.*\]` 抽取 JSON，LLM 输出带说明文字/代码围栏/多数组时整批解析失败（保守失败为空，损失一轮识别）。
4. **低置信警示不可见**：`low_confidence` 行仅琥珀背景色，无文字说明，用户容易忽略。

## 2. 目标 / 非目标

**目标**
- 页眉/页脚/文本框/内容控件中的段落进入候选提取、识别、预览、替换、标蓝全链路。
- 同段落同形留白支持多个填写点（occ 语义），不再静默丢弃。
- LLM 输出解析加固（围栏/前后缀文字容错）。
- 前端低置信文字警示。
- 存量模板（旧 addr、无 occ）识别/渲染/预览行为完全不变。

**非目标（已知边界，记档不动）**
- 已填现值型填写点（段落无任何留白特征，不进 `FILL_HINT_RE` 候选）——保持现状。
- 跨段落填写位（一个填写位跨两段）——不支持。
- xlsx 侧无页眉页脚问题，本次不动。
- C 端 docx-preview 保真预览（文件审核/范本预览）对页眉页脚/文本框的展示保真度——本次不改 C 端预览。

## 3. 核心方案：`_build_addr_map` 单点编址扩展

### 3.1 依据（第一性原理）

候选提取、B 端预览 items（`/template/fill/<id>/preview`）、渲染替换（`apply_docx_placeholders`）、标蓝（`renderer._colorize_placeholder_runs`）四条链路全部共用 `_build_addr_map`——addr 语义单点收敛是既有架构的核心约束（docx_utils.py 文件头注释：「存量模板 addr 兼容依赖此规则不变」）。在此单点扩展，下游自动受益且不会出现两套遍历逻辑的语义漂移。

渲染链验证结论：docxtpl 的 `DocxTemplate.render` 原生处理 document.xml 之外的 header/footer part，document.xml 内的文本框/内容控件 XML 整体参与 Jinja2 渲染——**渲染引擎无需改动**，瓶颈只在「anchor → {{key}}」替换与标蓝是否触达这些段落，而这正是 `_build_addr_map` 的覆盖面。

### 3.2 扩展编址规则（全部为增量前缀，存量 addr 不变）

| 容器 | addr 格式 | 说明 |
|------|-----------|------|
| 正文段落（现状） | `para:<idx>` | 不变 |
| 正文表格 cell（现状） | `cell:<tbl>:<r>:<c>:<pi>` | 不变；嵌套表 `:t<j>` 递归（不变） |
| 页眉段落 | `hdr:<sec>:<idx>` | `<sec>` 为节序号；`<idx>` 为该 header part 内扁平序号；first/even 类显式 unlinked 时追加后缀段防同节撞号：`hdr:<sec>:first:<idx>` / `hdr:<sec>:even:<idx>`（default 类保持无后缀） |
| 页眉内表格 cell | `hdr:<sec>:cell:<tbl>:<r>:<c>:<pi>` | 复用 cell 语法，挂在 hdr 前缀下（first/even 同理：`hdr:<sec>:first:cell:...`） |
| 页脚段落 / 表格 | `ftr:<sec>:<idx>` / `ftr:<sec>:cell:...` | 同页眉（first/even 后缀规则同上） |
| 文本框段落 | `<父addr>:tx<k>:<pi>` | 与嵌套表格 `:t<j>` 同模式；如 `para:3:tx0:1`、`cell:0:1:2:tx0:0` |
| 文本框内表格 | `<父addr>:tx<k>:cell:...` | 同规则递归 |
| 内容控件（sdt）段落 | `sdt:<k>:<pi>` | `<k>` 为 body 直系 sdt 的文档顺序号；独立前缀，不消耗存量 `para:` 计数器 |
| sdt 内表格 cell | `sdt:<k>:cell:<tbl>:<r>:<c>:<pi>` | 复用 cell 语法，挂在 sdt 前缀下 |
| cell/文本框内 sdt | `<父addr>:sdt<k>:<pi>` | 与 `:t<j>`/`:tx<k>` 同模式追加；内含表格 `:cell:...` 递归 |

`index`（扁平序号）继续全文档单调递增（候选排序用），header/footer/textbox/sdt 段落一并编入，与 addr 一一对应。**存量计数器隔离（红线）**：`para:<idx>`/`cell:<tbl>:...` 的存量编号序列只随存量区域（body 直系 `w:p` 与存量表格 cell 段落）递增；sdt/文本框/页眉页脚段落分配 index 但**不消耗存量计数器**——否则含新元素文档的存量 addr 整体错位，同形 anchor 会静默错填（质量审查 I-1）。

### 3.3 遍历细节与陷阱

1. **linked header/footer 去重**：`section.header.is_linked_to_previous` 为 True 时该节不持有独立 part（与前一节共享），跳过；另按 part 对象（`header.part`）做全文档去重，防 first/even/default 三类引用同一 part 时重复编址。
2. **三类页眉页脚都要编**：`section.header` / `section.first_page_header` / `section.even_page_header`（footer 同理）；`is_linked_to_previous` 对 first/even 同样适用。
3. **mc:AlternateContent 双份文本框**：Word 对浮动文本框常存双份（`mc:Choice` wps + `mc:Fallback` VML）。**只遍历 `mc:Choice`，跳过 `mc:Fallback`**，否则同一段落进候选两次 → anchor 反查「匹配到多处」歧义 + LLM token 浪费。
4. **`w:sdt` 展开**：body 直系 `w:sdt` 递归进入其 `w:sdtContent` 取 `w:p`/`w:tbl`，以**独立 `sdt:<k>:` 前缀**编址（`<k>` 按文档顺序），**不使用 `para:` 前缀、不消耗存量 para: 计数器**（否则含 sdt 的存量范本 para:N 整体错位，同形 anchor 静默错填——违反「存量模板行为完全不变」红线）。cell/文本框内的 sdt 以 `:sdt<k>:` 段追加到父 addr。sdt 内再嵌 sdt 同样递归（`:sdt0:sdt1:<pi>`）；sdt 无 `w:sdtContent` 子节点时 debug 日志跳过。
5. **文本框递归深度防御**：`w:txbxContent` 内可再嵌 drawing/textbox，递归时设深度上限（如 8 层），超限告警跳过，防畸形 XML 爆栈。
6. **Paragraph 包装**：header/footer 段落用 python-docx 自带 API（`header.paragraphs` / `header.tables`）取原生 `Paragraph` 对象；文本框内 `w:p` 用 `Paragraph(el, parent)` 包装（替换只操作 lxml 子树，无 part 依赖，`doc.save` 整体落盘）。
7. `_has_link_or_field`、跨 run 替换、标蓝逻辑对 header/footer/textbox 段落同样适用（均基于 Paragraph.runs / lxml），无需分支。

### 3.4 各链路受益方式（代码改动面）

| 链路 | 文件 | 改动 |
|------|------|------|
| 候选提取 | `docx_utils.extract_docx_candidates` | 零改动（走 `_build_addr_map`） |
| B 端预览 items | `/template/fill/<id>/preview` | 零改动（走 iter_docx_paragraphs）；前端划选 addr 透传自动兼容 |
| 渲染替换 | `docx_utils.apply_docx_placeholders` | 仅 occ 语义改动（见 §4），addr_map 自动覆盖新区域 |
| 标蓝 | `renderer._colorize_placeholder_runs` | 零改动（走 `_build_addr_map`，注释同步更新） |
| 识别 | `template_api._extract_candidates` / `detector.detect_fill_points` | 零改动（候选自动变多） |

## 4. 同段落同形留白：occ 语义

### 4.1 数据模型

填写点新增可选字段 `occ`（int，1-based，表示 anchor 在所属段落文本中的第几次出现）：

- 识别 merge（`_merge_detection`）：`(addr, anchor)` 撞车不再 `continue` 丢弃——第二项起标 `occ = 已占次数 + 1`，保留 `low_confidence = True`（同形留白语义歧义，确认时需人工核对）。
- 存量数据/前端手动行/划选行无 `occ` → 一律按 1 处理，行为与现状完全一致。

### 4.2 替换层（docx_utils）

- `_replace_in_paragraph(p, anchor, repl, occ=1)`：
  - 单 run 路径：`str.replace` 全替换语义改为**只替换第 occ 次出现**（按 `p.text` 中出现次序定位，在 run 文本内做对应偏移替换）。
  - 跨 run 路径（`_replace_cross_run_in_place`）：增加起始偏移参数——先消费掉前 `occ-1` 次出现再进入现有 `scan_from` 扫描替换。
  - run 拼接路径（`_replace_via_run_concat`）：同样支持 occ 定位。
  - anchor 在段落文本中出现次数 `< occ` → 返回 False（no-op + 告警日志，与 addr 悬空同语义）。
- `apply_docx_placeholders`：replacements 条目透传 `rep.get("occ", 1)`。

### 4.3 校验层（detector.validate_placeholders）

- addr 非空分支：校验 `occ`（缺省 1）为正整数且 ≤ anchor 在 `cand["text"]` 中的实际出现次数，超界报错「第N个填写点：锚文本出现次数不足（occ=X，实际Y）」。
- 手动行反查分支（addr 为空）：occ 只能为 1（反查唯一命中语义下无多义），`occ > 1` 报错。

### 4.4 前端

- `placeholder-table.tsx` 不展示、不编辑 occ（内部定位字段）；手动/划选行不带 occ。
- 现有 `persistedKeys`/保存链路对未知字段透传即可（后端 schema 校验兜底）。

## 5. LLM 输出解析加固（detector.parse_detection_response）

抽取顺序改为三级容错：

1. 直接 `json.loads(raw)`（LLM 规整输出裸数组时最快路径）；
2. 剥 ``` 围栏（```json ... ```）后 `json.loads`；
3. 现有贪婪正则 `\[.*\]`（DOTALL）回退。

三级都失败返回 `[]`（现状语义：0 项 → 上层置 failed，不静默）。解析成功后的逐项校验逻辑不变。

## 6. 前端低置信文字警示（placeholder-table.tsx）

- `row.low_confidence` 时除琥珀背景外加「低置信」小徽标（amber 系文字），`title` 提示：「该项由 AI 收缩修正或存在同形留白，请核对锚文本是否落在正确空位」。
- 不改表格结构，只在该行锚文本列或 key 列内追加徽标元素。

## 7. 0 候选错误文案

`_ZERO_CANDIDATES_MSG` 同步更新：提示已覆盖正文/表格/页眉/页脚/文本框（用户不再怀疑盲区），并引导手动添加或使用 `{{key}}` 显式标注。

## 8. 错误处理

| 场景 | 行为 |
|------|------|
| header/footer part XML 损坏（解析抛异常） | 该 part 跳过 + 告警日志，不拖垮整文档编址 |
| 文本框嵌套超深度上限 | 超限子树跳过 + 告警 |
| occ 超界 | 校验层拒绝清单（识别路径置 failed）；替换层 no-op + 告警（旧数据回放） |
| linked header 无独立 part | 跳过（共享 part 已编址） |
| LLM 解析三级全失败 | 返回 [] → 上层 failed（现状） |

## 9. 测试（对抗性用例优先）

**docx_utils（新增/扩展 `test/test_template_fill_utils.py`）**
- 构造含 default/first/even 页眉页脚 + linked 节 + 页眉内表格的 docx：编址正确、linked 去重、扁平序号连续。
- mc:AlternateContent 双份文本框：只编址一次（Choice），Fallback 文本不进候选。
- sdt 包裹段落/表格：正常编址与替换；sdt 嵌 sdt。
- 同段 3 个同形留白：occ=1/2/3 各自替换正确落位；occ=4 越界 no-op + 告警。
- 损坏 header part（手工注入畸形 XML）：跳过不崩。
- 存量回归：无新前缀/无 occ 的旧 addr 全链路行为与现状一致。

**detector（`test/` 现有识别套件扩展）**
- merge：同段同形 anchor 两项 → 第二项 occ=2 且 low_confidence；与显式 `{{key}}` 直通项撞车仍手动优先。
- 解析：裸数组 / 围栏包裹 / 前后带说明文字 / 多数组 / 纯垃圾输出 五类输入。
- validate：occ 缺省=1 兼容旧数据；occ 超界拒绝；手动行 occ>1 拒绝。

**前端**：`npm run build` 通过；低置信徽标在 placeholder-table 正常渲染（手动核对）。

## 10. 部署面与兼容

- 后端成套 SCP：`rag/svr/template_fill/docx_utils.py`、`rag/svr/template_fill/detector.py`、`api/apps/restful_apis/template_api.py`（0 候选文案）、`rag/svr/template_fill/renderer.py`（仅注释，可并入）。
- 前端：`web/src/pages/template-fill/placeholder-table.tsx`（需 `npm run build` + dist SCP）。
- 数据库：无结构变更；`tpl_template_version.placeholders` JSON 向前兼容（occ 可选字段）。
- 存量模板：旧 addr 无新前缀、旧占位符无 occ → 全链路行为不变。
- 遵守项目约束：不自动部署，部署由用户指示。

## 11. 验收标准

1. 上传含页眉盖章位/页脚日期/文本框单位名/内容控件段落的范本，识别结果覆盖这些位置，渲染成稿中对应位置被正确填写且标蓝。
2. 「姓名：＿＿ 电话：＿＿」同段两个同形留白识别为两个填写点，各自替换。
3. LLM 输出带围栏/说明文字时识别不再整批失败。
4. 识别确认表中低置信项有「低置信」文字徽标。
5. 既有全部测试通过 + 新增对抗用例通过；存量模板行为不变。
