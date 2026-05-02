# 文档分析逻辑优化设计

## 背景

当前文档分析流程：
1. 从 ES 获取文档 chunks
2. `ChunkMerger` 将 chunks 合并为章节（基于正则匹配章节标题）
3. `SectionAnalyzer` 逐章节调用 LLM 分析

**问题：**
- 章节识别依赖正则，可能不准确
- 每个章节单独分析，缺乏整体上下文
- 结果分散，无法形成完整的文档摘要

## 目标

优化为整体文档分析：
- 合并所有 chunks 为完整文本
- 超过上下文长度时，按段落分批分析
- 最终合并所有批次结果，生成一份完整报告

## 设计方案

### 核心流程

```
┌─────────────────────────────────────────────────────────────┐
│                    文档分析流程                              │
├─────────────────────────────────────────────────────────────┤
│  1. 获取文档 chunks                                          │
│  2. 合并所有 chunks 为完整文本                                │
│  3. 计算 tokens 数量                                         │
│  4. 判断是否需要分批：                                        │
│     - tokens <= MAX_CONTENT_TOKENS：直接分析                 │
│     - tokens > MAX_CONTENT_TOKENS：分批分析 → 最终合并       │
│  5. 返回分析结果                                              │
└─────────────────────────────────────────────────────────────┘
```

### 关键参数

| 参数 | 值 | 说明 |
|------|-----|------|
| `MAX_CONTEXT_TOKENS` | 8000 | 模型上下文限制（固定值） |
| `RESERVED_TOKENS` | 2000 | 预留给系统提示和输出 |
| `MAX_CONTENT_TOKENS` | 6000 | 实际可用于文档内容的 tokens |

### 新增组件

#### 1. DocumentAnalyzer

整体文档分析器，替代原有的 SectionAnalyzer。

**职责：**
- 合并 chunks 为完整文本
- 判断是否需要分批
- 执行单批次或多批次分析
- 调用 ResultMerger 合并多批次结果

**核心方法：**
```python
class DocumentAnalyzer:
    def analyze_document(
        self,
        chunks: list[dict],
        analysis_types: list[str] = None,
        progress_callback: Callable = None
    ) -> DocumentAnalysisResult:
        """分析整个文档"""
        # 1. 合并 chunks
        full_text = self._merge_chunks(chunks)

        # 2. 计算 tokens
        tokens = self._count_tokens(full_text)

        # 3. 判断分批
        if tokens <= MAX_CONTENT_TOKENS:
            return self._analyze_single(full_text, analysis_types)
        else:
            return self._analyze_batched(full_text, analysis_types, progress_callback)
```

#### 2. TextSplitter

按段落切割文本。

**职责：**
- 按段落边界切割文本
- 确保每批不超过 MAX_CONTENT_TOKENS
- 保留段落完整性

**切割规则：**
- 优先在 `\n\n`（双换行）处切割
- 若单个段落超长，再按 `\n`（单换行）切割
- 记录每批的页码范围（用于进度显示）

**核心方法：**
```python
class TextSplitter:
    def split_by_paragraphs(
        self,
        text: str,
        max_tokens: int = MAX_CONTENT_TOKENS
    ) -> list[TextBatch]:
        """按段落切割文本"""
        paragraphs = text.split("\n\n")
        batches = []
        current_batch = []
        current_tokens = 0

        for para in paragraphs:
            para_tokens = self._count_tokens(para)
            if current_tokens + para_tokens > max_tokens and current_batch:
                # 保存当前批次
                batches.append(TextBatch(
                    content="\n\n".join(current_batch),
                    tokens=current_tokens
                ))
                current_batch = [para]
                current_tokens = para_tokens
            else:
                current_batch.append(para)
                current_tokens += para_tokens

        # 保存最后一批
        if current_batch:
            batches.append(TextBatch(
                content="\n\n".join(current_batch),
                tokens=current_tokens
            ))

        return batches
```

#### 3. ResultMerger

合并多批次分析结果。

**职责：**
- 收集所有批次的分析结果
- 调用 LLM 将多份结果合并为一份完整报告

**合并策略：**
- 所有批次完成后，一次性调用 LLM 合并
- 合并 prompt 指导 AI 整合重复内容、补充遗漏内容

**核心方法：**
```python
class ResultMerger:
    def merge_results(
        self,
        batch_results: list[dict],
        analysis_type: str
    ) -> str:
        """合并多批次结果"""
        # 构建合并 prompt
        merge_prompt = self._build_merge_prompt(batch_results, analysis_type)

        # 调用 LLM 合并
        merged_result = self._call_llm(merge_prompt)

        return merged_result
```

### 数据结构

```python
@dataclass
class TextBatch:
    """文本批次"""
    content: str              # 批次内容
    tokens: int               # tokens 数量
    start_page: int = None    # 起始页码
    end_page: int = None      # 结束页码

@dataclass
class DocumentAnalysisResult:
    """文档分析结果"""
    doc_name: str             # 文档名称
    analyses: list            # 分析结果列表
    total_batches: int = 1    # 总批次数
    success: bool = True
    error_message: str = ""
```

### Prompt 设计

**分析 Prompt（单批次）：**
```
请对以下招标文件内容进行分析，提取{analysis_type}：

文档内容：
{content}

请按以下格式输出：
1. [要点1]
2. [要点2]
...
```

**合并 Prompt（多批次）：**
```
以下是同一份招标文件分批次分析得到的{analysis_type}结果。
请将这些结果合并为一份完整、连贯的分析报告。
注意：去除重复内容，补充遗漏内容，保持逻辑连贯。

批次1结果：
{result_1}

批次2结果：
{result_2}

...

请输出合并后的完整分析报告：
```

### 进度回调

```python
def progress_callback(current_batch: int, total_batches: int, stage: str):
    """
    进度回调函数

    Args:
        current_batch: 当前批次（1 到 total_batches）
        total_batches: 总批次数
        stage: 当前阶段 - 'analyzing' 或 'merging'
    """
    if stage == 'analyzing':
        progress = int((current_batch / total_batches) * 80)
    elif stage == 'merging':
        progress = 80 + int((current_batch / total_batches) * 20)
```

### API 变化

**响应结构：**
```json
{
    "status": "completed",
    "progress": 100,
    "sections": [
        {
            "section_title": "整体分析",
            "analyses": [
                {
                    "analysis_type": "key_points",
                    "result": "合并后的完整分析报告...",
                    "success": true
                }
            ]
        }
    ]
}
```

### 文件结构

```
api/lib/analysis/
├── document_analyzer.py    # 新增：整体文档分析器
├── text_splitter.py        # 新增：文本切割器
├── result_merger.py        # 新增：结果合并器
├── chunk_merger.py         # 保留：用于其他场景
├── section_analyzer.py     # 保留：用于分章节分析场景
└── prompts/
    ├── bid_analysis.yaml   # 分析 prompt
    └── merge_analysis.yaml # 新增：合并 prompt
```

### 兼容性

- 保留原有 `ChunkMerger` 和 `SectionAnalyzer`，供其他场景使用
- 新增 `use_section_analysis` 参数，允许用户选择分析模式：
  - `false`（默认）：整体文档分析
  - `true`：分章节分析（原有逻辑）

## 实现要点

1. **tokens 计算**：使用 `tiktoken` 或简化估算（中文约 1.5 字符/token）
2. **段落切割**：优先双换行，确保语义完整性
3. **合并质量**：合并 prompt 强调去重、补充、连贯
4. **进度显示**：分批分析时实时更新进度
5. **错误处理**：单批次失败不影响其他批次，记录错误信息

## 测试要点

1. 小文档（< 6000 tokens）：单批次分析
2. 大文档（> 6000 tokens）：多批次分析 + 合并
3. 超大文档（> 30000 tokens）：验证切割和合并质量
4. 不同文档类型：招标文件、合同、通用文档
5. 边界情况：空文档、单段落超长文档