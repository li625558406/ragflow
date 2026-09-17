# 范本 AI 识别加固——run 层确定性切位 + LLM 语义标注

日期：2026-09-18
状态：设计定稿（待实施）
分支基线：feat/unified-crawler-framework

## 1. 问题与根因

### 1.1 用户症状（四项全中）

以福建省各工程招标示范文本（`F:\投标项目\投标资料\最新招标文件标准范本`）为代表的范本中，填写位形态为「下划线 / 下划线+描述」，实测真实形态是 **w:u 下划线格式 run，内容为空格+括号提示**：

```
run[0] underline=None text='本招标项目'
run[1] underline=True text='    （招标项目名称） '        ← 一个填写位 = 一个 run
run[2] underline=None text='已由'
run[3] underline=True text=' （项目审批、核准或备案机关名称）'
run[5] underline=True text=' （批文名称及编号） '
run[7] underline=True text='             '               ← 纯空格填写位
```

一行可含 4-7 个填写位。用户报告的识别症状：

| 症状 | 现有链路根因 |
|---|---|
| 拆成多个碎片（下划线/提示语各成一个填写点） | LLM 在文本层自选 anchor，常只选中 `（提示）` 半截或某段空格；现有收缩逻辑只认单一留白标记 |
| 漏识别 | LLM 没输出该行/该位时无任何兜底；LLM 数错空格 → anchor 非精确子串 → parse 直接丢弃 |
| 重复/错位 | 同行多位让 LLM 一次输出，anchor 相互错位；同形空格 occ 依赖 LLM 输出顺序质量 |
| 提示语污染（`（招标项目名称）`被当现值） | LLM 把 hint 文本选为 anchor，混入现值派生链路 |

### 1.2 第一性原理分析

识别 = ①**「哪里是填写位」**（几何/格式问题，确定性）+ ②**「这个位是什么字段」**（语义问题，LLM 擅长）。

现有链路把两层全部压给 LLM：让 LLM 在纯文本行里「盲猜」anchor 精确子串。而**填写位边界在 run 层是精确已知的**——下划线格式的 run 就是填写位。边界判定不该交给 LLM。

### 1.3 方案选择

- **方案A（采纳）：run 层确定性切位 + LLM 只做语义标注**。根除四症状。
- 方案B（否决）：现有架构打补丁，parse 后 anchor 吞并扩展。改动小但漏识别无解，治标。
- 方案C（否决）：切位 + LLM 仍选 anchor 后吸附对齐。两套坐标系对齐逻辑复杂，边界场景多。

## 2. 总体架构

```
docx blob
  │
  ├─ extract_docx_candidates（现有，产物新增 slots 键）→ [{index, text, addr, slots}]
  │
  ├─ ★ 新增 blank_slots.py：run 层切位器（纯函数）
  │      slots: [{start, end, text, kind, hint}]，runs 拼接文本坐标系
  │
  ├─ detect_fill_points（detector.py，改造）
  │      ├─ 有 slots 的行 → DETECT_SYSTEM_V2：「位编号→语义」契约，LLM 不再选 anchor
  │      ├─ 无 slots 的行 → 现有 V1 契约原样保留（存量范本零影响）
  │      └─ LLM 未覆盖的 slot → 确定性兜底条目（hint 名可信不低置信；纯空白位低置信）
  │
  └─ 下游全部不动：_merge_detection/occ 预分配、validate_placeholders、
       渲染替换（anchor+addr+occ 契约不变）、前端预览高亮、默认值派生、B端确认界面
```

**关键不变式**：识别产物仍为 `{addr, anchor, occ, key, name, ...}`，anchor 是候选文本的精确子串。下游零改动。

**四症状对应机制**：

| 症状 | 根除机制 |
|---|---|
| 拆碎片 | 位区间在 run 层一次切出，`空格+（提示）+空格` 恒为一个位 |
| 漏识别 | 位由代码切出，LLM 没标注也兜底入清单（低置信） |
| 重复/错位 | LLM 只引用位编号，anchor 由代码切片永不失配；同形多 occ 沿用现有预分配 |
| 提示语污染 | hint 在切位阶段标记 `kind=hint`，强制不派生默认值 |

**替换口径**（用户确认）：填写位被填值后**整体替换**——`本招标项目＿＿（招标项目名称）＿已由…` → `本招标项目××大厦已由…`，下划线与括号提示一并消失。

## 3. 切位器 `blank_slots.py`

### 3.1 坐标系（关键约束）

python-docx 1.2.0 的 `Paragraph.text` **含超链接文本**而 `Paragraph.runs` 不含。切位区间必须记录在 **runs 拼接文本坐标系**（与替换层 `_replace_cross_run_in_place` 同坐标系），否则替换错位。含超链接段落的处理见 3.4。

### 3.2 位判定（run 级，两种来源）

1. **格式下划线**：run 满足 `run.underline` 非 None/False 且非显式 `val="none"`，**且**文本匹配留白特征（三选一）：
   - 纯空白/制表符（如 `'             '`）
   - 空白+括号提示+空白（如 `'    （招标项目名称） '`）
   - 下划线字符 `_`/`＿`

   ⚠️ 实心文字加下划线格式（如下划线的标题词）**不算位**——必须满足留白特征，这是防误报的关键闸。
2. **字符下划线**：连续 `_`/`＿` ≥2（不要求格式，兼容手打下划线）。

### 3.3 区间合并与产出

相邻位 run（中间只隔空白文本或直接相接）合并为一个区间。产出：

```python
{"start": 12, "end": 30, "text": "    （招标项目名称） ",
 "kind": "hint" | "blank",   # 含括号提示 → hint；纯空白/下划线 → blank
 "hint": "招标项目名称"}      # kind=hint 时括号内文本原样保留（含冒号说明）
```

接入方式：`extract_docx_candidates` 在遍历段落时对命中行附带 `slots` 键（纯新增，旧消费方无感知）；`template_api.py` 零改动。

### 3.4 边界与对抗性场景（实现时逐条测试）

| 场景 | 处理 |
|---|---|
| 段落含超链接（runs 拼接 ≠ p.text） | 位区间与超链接文本重叠 → 该位放弃，该行回退 V1 路径；不含则正常切 |
| `underline=None`（继承段落/样式） | 不算位（首版只认 run 级显式设置；实测范本均为 run 级） |
| `underline` 显式 `val="none"` | 不算位 |
| 整段就是一个下划线 run（独立填写行） | 正常切，单段单位 |
| 同段多个纯空白位（文本相同） | 正确切多位，occ 由现有预分配按 `_anchor_pos` 排序落位 |
| 提示语超长（>100 字符） | hint/name 截断（沿用现有 100 上限） |
| 表格/文本框/页眉页脚内段落 | 同样切位（复用 `_build_addr_map` 全区域遍历产物） |
| 空 runs / 段落文本空 | 无位，正常跳过 |

**范围**：仅 docx。xlsx 完全不走此模块；.doc/PDF 转换件转换后保留 w:u 则受益，不保留则自动回退 V1，双态都正确。

## 4. detector 识别契约升级

### 4.1 候选行分流

`detect_fill_points` 内：候选行带非空 `slots` → V2 块；否则 → V1 块（现有契约原样保留）。两组各自按 `DETECT_CHUNK_SIZE` 分块并发，结果进同一个 `_merge_detection`（explicit > V2 > V1 的既有优先级语义）。

### 4.2 V2 提示词（新 `DETECT_SYSTEM_V2`）

```
输入：行号\t行文本，行下方列出该行的填写位：位[n]=「位文本」
输出 JSON 数组，每个元素：
{"line": 行号, "slot": 位序号, "key": "snake_case英文标识", "name": "中文字段名",
 "description": "给填写模型的说明", "retrieval_query": "检索词", "required": true/false}
规则：
1. 每个填写位必须标注，一行多位拆成多个元素；括号里的提示语去掉括号就是中文字段名
2. key 全局唯一；同一含义出现多次（如多个「日期」）也应分别标注，由系统自动区分位置
3. 找不到任何填写位输出 []；只输出 JSON 数组
```

### 4.3 V2 解析（新 `parse_slot_response`）

- `(line, slot)` 查位 → `anchor = runs_text[start:end]` 精确切片，**LLM 无权决定 anchor**
- slot 序号非法/超界 → 丢弃该项；同一 slot 被标注多次 → 取第一个
- 产物字段与 V1 完全同构（`addr/anchor/occ/key/name/description/retrieval_query/fill_mode/required/top_k/low_confidence/_anchor_pos`）

### 4.4 兜底条目（LLM 漏标的 slot）

确定性生成，每位恰好一条：

- `kind=hint` → `name = hint 文本去括号`（如「招标项目名称」），`low_confidence=False`（名字高可信，仅 key 机器生成）
- `kind=blank`（纯空白/下划线）→ `name = 未命名填写位`，`low_confidence=True`
- `key = blank_{序号}`（确定性唯一，B端确认时可改）；`required=True` 默认
- 数学性质：兜底条目区间互不重叠、anchor 在 runs_text 中恰好各出现于自身区间 → occ 预分配不溢出、不撞车

### 4.5 默认值派生

V2 条目（含兜底）一律**不派生默认值**——位文本是留白/提示语，不是现值。已填现值识别仍走 V1 的 `derive_default_from_anchor`。`kind=hint` 从源头阻断提示语污染。

### 4.6 前端影响

零改动。兜底条目复用现有 `low_confidence` 琥珀色行样式，B端确认视图/编辑视图照常工作。

## 5. 测试与验收

### 5.1 单测（对抗性）

- `blank_slots`：3.4 表中 8 个场景逐条覆盖 + 从真实范本（信息化工程 docx）提取的 run 序列 fixture
- detector：V2 解析、slot 越界丢弃、同 slot 重复取首、兜底生成、V1/V2 合并不撞车、hint 不派生默认值
- 既有 624 后端用例必须全绿（V1 路径零回归）

### 5.2 真实语料验收

- `F:\投标项目\投标资料\最新招标文件标准范本\福建省信息化工程项目招标文件示范文本（试行）.docx` 直跑全链路（切位→识别→确认清单），人工核对：位数量、位置、中文名质量、无碎片/无重复
- .doc 样本（如高速公路示范文本）经容器 LibreOffice 转换后抽查 1-2 份，验证转换件双态行为

## 6. 部署清单（未授权不部署）

后端 3 文件成套 SCP + 容器重启 + import 冒烟：

| 类型 | 文件 |
|---|---|
| 新增 | `rag/svr/template_fill/blank_slots.py` |
| 修改 | `rag/svr/template_fill/docx_utils.py`（候选行附带 slots） |
| 修改 | `rag/svr/template_fill/detector.py`（V2 契约 + 兜底 + 分流） |

同步更新 `CHANGE.md` 与项目 `CLAUDE.md` 参考表。

## 7. 遗留与不做的事

- **不做**：`underline=None` 继承样式的识别（首版）；xlsx 切位；PDF 原生矢量线切位（已有 `_augment_pdf_blank_lines` 回填 `_` 字符路径覆盖）
- 兜底条目的 key（`blank_N`）可读性一般，依赖 B端确认时人工改名——刻意不引入拼音库，保持零新依赖
- V1 路径的原有收缩修正逻辑原样保留，不在本次重构范围内
