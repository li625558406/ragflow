# 范本预览 docx-preview 保真渲染 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** C端范本实时预览抽屉改用 docx-preview 渲染原始 docx（字号/加粗/颜色/表格/排版保真），并保留 AI 填入实时高亮（DOM 后处理）。

**Architecture:** 纯前端。`useTemplateFillFile` 拉原始 docx blob → `renderAsync` 渲染进抽屉容器 → pristine innerHTML 快照；SSE values 变化时快照重放 + `applyDocxHighlight`（Range 跨节点替换占位符）。渲染失败降级回现有纯文本段落渲染。xlsx 分支与抽屉外壳零改动。

**Tech Stack:** React 18 + TypeScript、docx-preview（新依赖）、TanStack Query、Tailwind。

**设计文档:** `docs/superpowers/specs/2026-09-11-template-preview-docx-fidelity-design.md`

---

### Task 1: 安装 docx-preview 依赖

**Files:**
- Modify: `web/package.json`（npm 自动改）

- [ ] **Step 1: 安装**

```bash
cd D:/AI/ragflow2/web && npm install docx-preview
```

（如 registry 超时则加 `--registry=https://registry.npmmirror.com`；仍失败按 CLAUDE.md 走 10808 代理）

- [ ] **Step 2: 验证安装**

```bash
cd D:/AI/ragflow2/web && node -e "console.log(require('docx-preview/package.json').version)"
```

Expected: 输出 0.3.x 版本号

- [ ] **Step 3: Commit**

```bash
git add web/package.json web/package-lock.json
git commit -m "chore(web): 新增 docx-preview 依赖（范本预览保真渲染）"
```

---

### Task 2: useTemplateFillFile hook（原始 docx blob 拉取）

**Files:**
- Modify: `web/src/hooks/use-template-fill-request.ts`（文件末尾追加）

- [ ] **Step 1: 追加 hook**

在文件末尾（`confirmTemplateFill` 之后）追加：

```ts
// ── 范本原始文件（docx-preview 保真渲染用）──────────────────────────

// 获取模板原始文件 blob（kind=original）。错误体是 JSON（code != 0）时
// blob 里装的是错误信息而非文件，parse message 抛错（照 downloadTemplateFillResult 口径）。
export function useTemplateFillFile(id: string) {
  return useQuery({
    queryKey: ['templateFillFile', id],
    queryFn: async () => {
      const res = await request.get(api.downloadTemplateFill(id, 'original'), {
        responseType: 'blob',
      });
      const blob = res.data as Blob;
      if (!blob || blob.size === 0) {
        throw new Error('文件为空');
      }
      if (blob.type.includes('application/json')) {
        let message = '文件获取失败';
        try {
          message = JSON.parse(await blob.text()).message || message;
        } catch {
          // 保留默认错误文案
        }
        throw new Error(message);
      }
      return blob;
    },
    enabled: !!id,
  });
}
```

- [ ] **Step 2: 类型检查**

```bash
cd D:/AI/ragflow2/web && npx tsc --noEmit -p tsconfig.json 2>&1 | head -20
```

Expected: 无新增错误（项目已有基线错误可忽略，确认无 use-template-fill-request 相关报错）

- [ ] **Step 3: Commit**

```bash
git add web/src/hooks/use-template-fill-request.ts
git commit -m "feat(web): useTemplateFillFile 拉取范本原始 docx blob"
```

---

### Task 3: applyDocxHighlight 高亮后处理（跨 run 合并）

**Files:**
- Create: `web/src/pages/c-chat/docx-highlight.ts`

- [ ] **Step 1: 创建模块**

```ts
// docx-preview 渲染结果的占位符高亮后处理：在保真 DOM 上把 {{key}} 替换为
// 蓝色填入值 / 虚线待填槽位。Word 会把占位符拆成多段 run（渲染后为多个相邻
// 内联元素），必须跨节点合并匹配 —— 先拼接全文定位匹配区间，再用 Range
// 跨节点 deleteContents + insertNode 替换。从后往前处理保证前置偏移不失效。
// 约束：docxtpl 占位符不跨段落/单元格，只存在同段落内跨 run 的情形。

const PLACEHOLDER_RE = /\{\{([a-z][a-z0-9_]*)\}\}/g;

interface Match {
  start: number;
  end: number;
  key: string;
}

function makeHighlightSpan(values: Record<string, string>, key: string) {
  const span = document.createElement('span');
  const v = values[key];
  // 公共样式：继承 Word 上下文字号（不硬编码字号），保真优先
  span.style.backgroundColor = '#EFF4FF';
  span.style.color = '#1a66fb';
  span.style.borderRadius = '2px';
  span.style.padding = '0 2px';
  if (v !== undefined && v !== '') {
    span.textContent = v;
    span.title = key;
  } else {
    // 未填：虚线槽位显示 key；key 较长，略缩字号避免撑版
    span.textContent = key;
    span.title = `${key}（等待 AI 填入）`;
    span.style.border = '1px dashed rgba(26, 102, 251, 0.6)';
    span.style.fontSize = '0.85em';
  }
  return span;
}

export function applyDocxHighlight(
  container: HTMLElement,
  values: Record<string, string>,
): void {
  // 1. 有序收集全部文本节点
  const walker = document.createTreeWalker(container, NodeFilter.SHOW_TEXT);
  const textNodes: Text[] = [];
  while (walker.nextNode()) textNodes.push(walker.currentNode as Text);
  if (textNodes.length === 0) return;

  // 2. 拼接全文 + 每个节点的偏移区间快照
  const spans: Array<{ node: Text; start: number; end: number }> = [];
  let offset = 0;
  for (const node of textNodes) {
    spans.push({ node, start: offset, end: offset + node.data.length });
    offset += node.data.length;
  }
  const full = textNodes.map((n) => n.data).join('');

  // 3. 全文扫描占位符
  const matches: Match[] = [];
  PLACEHOLDER_RE.lastIndex = 0;
  let m: RegExpExecArray | null;
  while ((m = PLACEHOLDER_RE.exec(full)) !== null) {
    matches.push({ start: m.index, end: m.index + m[0].length, key: m[1] });
  }
  if (matches.length === 0) return;

  // 绝对偏移 → (文本节点, 节点内偏移)；落在边界时归入包含它的节点
  const locate = (pos: number): { node: Text; off: number } | null => {
    for (const s of spans) {
      if (pos >= s.start && pos < s.end) {
        return { node: s.node, off: pos - s.start };
      }
    }
    // pos === 全文末尾（或空节点串联处）：归入最后一个节点末尾
    for (let i = spans.length - 1; i >= 0; i--) {
      if (spans[i].end === pos) {
        return { node: spans[i].node, off: spans[i].node.data.length };
      }
    }
    return null;
  };

  // 4. 从后往前替换：删右侧区间不影响左侧节点的偏移有效性
  for (let i = matches.length - 1; i >= 0; i--) {
    const { start, end, key } = matches[i];
    const from = locate(start);
    const to = locate(end);
    if (!from || !to) continue;
    try {
      const range = document.createRange();
      range.setStart(from.node, from.off);
      range.setEnd(to.node, to.off);
      range.deleteContents();
      range.insertNode(makeHighlightSpan(values, key));
    } catch {
      // 单个匹配替换失败（如节点已被上层修改）不影响其余匹配
    }
  }
}
```

- [ ] **Step 2: 类型检查**

```bash
cd D:/AI/ragflow2/web && npx tsc --noEmit -p tsconfig.json 2>&1 | grep docx-highlight
```

Expected: 无输出（无错误）

- [ ] **Step 3: Commit**

```bash
git add web/src/pages/c-chat/docx-highlight.ts
git commit -m "feat(web): applyDocxHighlight 占位符跨 run 高亮后处理"
```

---

### Task 4: template-fill-live-preview.tsx docx 分支重写

**Files:**
- Modify: `web/src/pages/c-chat/template-fill-live-preview.tsx`

- [ ] **Step 1: 新增 import**

```tsx
import { renderAsync } from 'docx-preview';
import { applyDocxHighlight } from '@/pages/c-chat/docx-highlight';
import { useTemplateFillFile } from '@/hooks/use-template-fill-request';
import { useEffect, useMemo, useRef, useState } from 'react';
```

（`useRef`/`useState` 合并进现有 react import；`useTemplateFillFile` 合并进现有 hook import）

- [ ] **Step 2: 组件内新增 docx 渲染状态与副作用**

在 `const filledCount = ...` 之前插入：

```tsx
// ── docx 保真渲染（docx-preview）：原始 blob → renderAsync → pristine 快照；
// values 变化时快照重放 + 高亮重涂。渲染失败降级回纯文本段落渲染。
const [renderFailed, setRenderFailed] = useState(false);
const containerRef = useRef<HTMLDivElement>(null);
const pristineRef = useRef<string>('');
const valuesRef = useRef(values);
valuesRef.current = values;

// file_type 为 docx 才拉原始文件（xlsx 走旧链路）
const docxEnabled = fileType === 'docx';
const { data: fileBlob, isLoading: fileLoading, error: fileError } =
  useTemplateFillFile(docxEnabled ? tpl.template_id : '');

// blob 到达：清容器 → renderAsync → 存 pristine 快照 → 立即涂一次高亮
useEffect(() => {
  if (!docxEnabled || !fileBlob || !containerRef.current) return;
  const el = containerRef.current;
  setRenderFailed(false);
  el.innerHTML = '';
  renderAsync(fileBlob, el, undefined, { inWrapper: true, breakPages: true })
    .then(() => {
      pristineRef.current = el.innerHTML;
      applyDocxHighlight(el, valuesRef.current);
    })
    .catch(() => setRenderFailed(true));
}, [fileBlob, docxEnabled]);

// values 变化：快照重放 + 重涂（SSE filling 批次频率低，整段替换简单可靠）
useEffect(() => {
  if (!docxEnabled) return;
  const el = containerRef.current;
  if (!el || !pristineRef.current) return;
  el.innerHTML = pristineRef.current;
  applyDocxHighlight(el, values);
}, [values, docxEnabled]);

// 范本切换时清快照（防止上一范本的高亮基线串台）
useEffect(() => {
  pristineRef.current = '';
  setRenderFailed(false);
}, [tpl.template_id]);

const docxFidelity = docxEnabled && !renderFailed && !fileError;
const docxLoading =
  fileLoading || (docxEnabled && !fileBlob && !fileError && isLoading);
```

- [ ] **Step 3: 正文区三分支改造**

替换现有正文区（`{isLoading ? ... : fileType === 'xlsx' ? ... : (...)}`）为：

```tsx
{docxLoading ? (
  <div className="flex items-center justify-center gap-2 py-16 text-xs text-[#8C8C8C]">
    <Loader2 className="h-4 w-4 animate-spin" />
    正在加载模板正文…
  </div>
) : docxFidelity ? (
  // 保真渲染容器：docx-preview 页面宽度固定（A4），窄抽屉下横向滚动看全
  <div className="min-h-0 flex-1 overflow-auto px-6 py-4">
    <div ref={containerRef} />
  </div>
) : (
  <>
    {docxEnabled && (renderFailed || fileError) && (
      <div className="mb-2 rounded bg-[#FFF7E8] px-3 py-2 text-xs text-[#FAAD14]">
        格式渲染失败，已降级为纯文本预览
      </div>
    )}
    {/* —— 以下为原有纯文本渲染（xlsx 分支 + docx 降级共用），原样保留 —— */}
    <div className="min-h-0 flex-1 overflow-y-auto overflow-x-hidden px-6 py-4">
      {fileType === 'xlsx' ? (
        /* 原 xlsx sheetGroups 渲染块，原样搬入 */
      ) : (
        /* 原纯文本段落渲染块，原样搬入 */
      )}
    </div>
  </>
)}
```

注意：原 JSX 中 `isLoading ?` 判定替换为 `docxLoading`；xlsx 分支与纯文本段落块的 JSX **逐字保留**，只挪进降级分支；容器类名 `min-h-0 flex-1 overflow-y-auto overflow-x-hidden px-6 py-4` 保持不变（外层抽屉结构未动）。

- [ ] **Step 4: 底部说明更新**

底部说明行改为（体现保真）：

```tsx
<div className="border-t border-[#E5E5E5] px-4 py-2 text-[10px] text-[#8C8C8C]">
  按 Word 原始格式渲染；蓝色为 AI 已填入内容，虚线槽位等待 AI 填入。成稿以最终渲染文件为准。
</div>
```

- [ ] **Step 5: 类型检查**

```bash
cd D:/AI/ragflow2/web && npx tsc --noEmit -p tsconfig.json 2>&1 | grep template-fill-live-preview
```

Expected: 无输出

- [ ] **Step 6: Commit**

```bash
git add web/src/pages/c-chat/template-fill-live-preview.tsx
git commit -m "feat(web): 范本实时预览 docx 分支改 docx-preview 保真渲染 + 填入高亮保留"
```

---

### Task 5: 构建验证 + 文档 + 收尾

- [ ] **Step 1: 生产构建**

```bash
cd D:/AI/ragflow2/web && npm run build
```

Expected: 构建成功（exit 0）

- [ ] **Step 2: CHANGE.md 增量条目**

在 CHANGE.md 顶部追加 2026-09-11 条目（范本预览 docx-preview 保真渲染），要点：docx 分支 renderAsync 保真 + applyDocxHighlight 跨 run 高亮 + 降级兜底 + xlsx/B端不动；关联设计/计划文档路径。

- [ ] **Step 3: 项目 CLAUDE.md 参考表登记**

`D:\AI\ragflow2\CLAUDE.md` 参考文档表追加一行：范本预览保真渲染设计 `D:\AI\ragflow2\docs\superpowers\specs\2026-09-11-template-preview-docx-fidelity-design.md` + 一句话进度说明。

- [ ] **Step 4: Commit**

```bash
git add CHANGE.md CLAUDE.md
git commit -m "docs: 范本预览 docx 保真渲染迭代记录与参考登记"
```

---

## 验证清单（实施完成后手工冒烟）

1. B端范本库上传带字号/加粗/颜色/表格/页眉的 docx → C端对话触发范本填写 → 「查看范本」与 Word 打开观感一致
2. filling 事件到达 → 槽位变蓝色值（继承上下文字号）；未填槽位虚线显示 key
3. 占位符在 Word 中手工拆 run（部分字符改色）→ 仍整段命中替换
4. 损坏 docx / 接口报错 → 降级纯文本 + 顶部黄色轻提示
5. xlsx 范本预览不回归（旧渲染）
6. filled 后「查看填写内容」回看蓝色值正常（刷新回放 template_fill_events 链路不回归）
7. c-chat 与 flow 两处入口行为一致
