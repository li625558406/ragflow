# 确认卡填写项点击定位跳转 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 确认卡（confirm_pending）中每个填写项的字段名可点击，打开「查看填写内容」预览抽屉并滚动定位到该占位符。

**Architecture:** 纯前端接线复用：父组件 `TemplateFillProgress` 已有 `liveTarget`/`TemplateFillLivePreview` focusKey 定位链路，给确认卡传 `onLocate(templateId, key)` 回调（内部就是 `setLiveTarget`）；卡片字段名 `<span>` 改为条件渲染的 `<button type="button">`。后端零改动。

**Tech Stack:** React 18 + TypeScript + Tailwind；测试 jest + @testing-library/react（jsdom）。

**环境注意（执行前必读）：**
- 项目原生 `jest.config.ts` 依赖未安装的 umi/test 不可用，**所有 jest 命令必须带临时配置**：
  `cd web && npx jest --config ../.scratch/jest.web.config.js <path>`
- Windows + git-bash；修改文件前必须先 Read。
- 禁止部署（不 SCP / 不重启 Docker / 不 push）；`npm run build` 仅 Task 3 本地验证。

---

### Task 1: 确认卡字段名可点击（TDD）

**Files:**
- Create: `web/src/pages/c-chat/__tests__/template-fill-confirm-card.test.tsx`
- Modify: `web/src/pages/c-chat/template-fill-confirm-card.tsx:115-149`（候选行字段名渲染）

- [ ] **Step 1: 写失败测试**

新建 `web/src/pages/c-chat/__tests__/template-fill-confirm-card.test.tsx`：

```tsx
// 确认卡填写项点击定位：字段名在 onLocate 传入时渲染为可点 button 并回传
// (template_id, key)；未传时保持纯文本（向后兼容）。定位链路本身（LivePreview
// focusKey → focusPlaceholder）由未填充汇总功能覆盖，此处只测卡片接线。
import { fireEvent, render, screen } from '@testing-library/react';
import TemplateFillConfirmCard from '@/pages/c-chat/template-fill-confirm-card';
import type { ITemplateFillConfirmPending } from '@/hooks/template-fill-stream';

jest.mock('@/hooks/use-template-fill-request', () => ({
  confirmTemplateFill: jest.fn().mockResolvedValue(undefined),
}));

const pending: ITemplateFillConfirmPending = {
  task_id: 't1',
  nonce: 'n1',
  templates: [
    {
      template_id: 'tpl1',
      name: '范本一',
      candidates: [
        { key: 'project_name', name: '项目名称', default_value: 'XX项目' },
        { key: 'phone', name: '联系电话', default_value: '' },
      ],
      predicted: ['project_name'],
    },
  ],
};

describe('TemplateFillConfirmCard 字段名点击定位', () => {
  it('onLocate 传入时字段名渲染为 button，点击回传 (template_id, key)', () => {
    const onLocate = jest.fn();
    render(
      <TemplateFillConfirmCard pending={pending} onLocate={onLocate} />,
    );
    const btn = screen.getByRole('button', { name: /项目名称/ });
    fireEvent.click(btn);
    expect(onLocate).toHaveBeenCalledTimes(1);
    expect(onLocate).toHaveBeenCalledWith('tpl1', 'project_name');
    // 另一个字段同样可点，参数正确
    fireEvent.click(screen.getByRole('button', { name: /联系电话/ }));
    expect(onLocate).toHaveBeenCalledWith('tpl1', 'phone');
  });

  it('onLocate 未传时字段名保持纯文本（不渲染 button）', () => {
    render(<TemplateFillConfirmCard pending={pending} />);
    expect(screen.queryByRole('button', { name: /项目名称/ })).toBeNull();
    expect(screen.getByText(/项目名称/)).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd web && npx jest --config ../.scratch/jest.web.config.js src/pages/c-chat/__tests__/template-fill-confirm-card.test.tsx`
Expected: FAIL —— TypeScript 层面 `onLocate` prop 不存在（babel 转译不查类型，实际运行断言：第一条 `getByRole` 找到的是「确认并继续填写」以外的按钮失败——`onLocate` 未消费时字段名是 span，`getByRole('button', { name: /项目名称/ })` 抛「找不到元素」）。若两条意外通过，说明改错了对象，停下检查。

- [ ] **Step 3: 实现确认卡改动**

Read `web/src/pages/c-chat/template-fill-confirm-card.tsx`，做两处修改：

①组件 props 增加 `onLocate`（签名与注释如下）：

```tsx
export default function TemplateFillConfirmCard({
  pending,
  onSubmitted,
  onLocate,
}: {
  pending: ITemplateFillConfirmPending;
  /** 提交成功后回调（可选）：使用方把 submitted 回写进流式状态，
   *  使归约器的 confirm_timeout 守卫（!submitted）真正生效 */
  onSubmitted?: () => void;
  /** 点击字段名定位到预览文档位置（可选）：父组件接 setLiveTarget({template_id, focusKey})；
   *  未传时字段名保持纯文本（向后兼容） */
  onLocate?: (templateId: string, key: string) => void;
}) {
```

②候选行字段名渲染（现 126-135 行的 `<span>`）改为条件渲染，文字色逻辑原样保留（勾选/直填黑、未勾选灰+划线；划线语义「不交给 AI」与定位无关）：

```tsx
                {onLocate ? (
                  <button
                    type="button"
                    title={`定位到文档中的「${c.name || c.key}」`}
                    className={`text-left underline-offset-2 decoration-dotted transition-colors hover:underline ${
                      isChecked || hasInput
                        ? 'text-[#000000]'
                        : 'text-[#8C8C8C] line-through'
                    }`}
                    onClick={(e) => {
                      e.stopPropagation();
                      onLocate(t.template_id, c.key);
                    }}
                  >
                    {c.name || c.key}
                    {c.default_value ? `（默认：${c.default_value}）` : ''}
                  </button>
                ) : (
                  <span
                    className={
                      isChecked || hasInput
                        ? 'text-[#000000]'
                        : 'text-[#8C8C8C] line-through'
                    }
                  >
                    {c.name || c.key}
                    {c.default_value ? `（默认：${c.default_value}）` : ''}
                  </span>
                )}
```

行内其余元素（Checkbox、Input、`hasInput` 计算）一律不动。

- [ ] **Step 4: 跑测试确认通过**

Run: `cd web && npx jest --config ../.scratch/jest.web.config.js src/pages/c-chat/__tests__/template-fill-confirm-card.test.tsx`
Expected: 2 passed

- [ ] **Step 5: 提交**

```bash
git add web/src/pages/c-chat/template-fill-confirm-card.tsx web/src/pages/c-chat/__tests__/template-fill-confirm-card.test.tsx
git commit -m "feat(template-fill-web): 确认卡字段名可点击触发 onLocate 定位回调"
```

---

### Task 2: 父组件接线 onLocate → liveTarget

**Files:**
- Modify: `web/src/pages/c-chat/template-fill-progress.tsx:58-67`（confirmCard 的 TemplateFillConfirmCard 调用）

- [ ] **Step 1: 传回调**

Read `web/src/pages/c-chat/template-fill-progress.tsx`，把 confirmCard 块（`{mergedState?.pendingConfirm && (...)}` 内）的卡片调用改为：

```tsx
      <TemplateFillConfirmCard
        key={`${mergedState.pendingConfirm.task_id}:${mergedState.pendingConfirm.nonce || ''}`}
        pending={mergedState.pendingConfirm}
        onSubmitted={onConfirmSubmitted}
        onLocate={(templateId, key) =>
          setLiveTarget({ template_id: templateId, focusKey: key })
        }
      />
```

（`key` prop 与其余 props 原样保留；`liveTarget`/`liveTpl`/`TemplateFillLivePreview` 零改动。）

- [ ] **Step 2: 类型检查**

Run: `cd web && npx tsc --noEmit -p tsconfig.json 2>&1 | grep -E "template-fill-(progress|confirm-card)"`
Expected: 无输出（这两个文件 0 报错；其他文件的存量报错不计）。

- [ ] **Step 3: 回归相关 jest 套件**

Run: `cd web && npx jest --config ../.scratch/jest.web.config.js src/pages/c-chat/__tests__/template-fill-confirm-card.test.tsx src/hooks/__tests__/template-fill-stream.test.ts src/hooks/__tests__/use-template-fill-task-poll.test.ts`
Expected: 全部 passed（2 + 24 + 12 = 38）。

- [ ] **Step 4: 提交**

```bash
git add web/src/pages/c-chat/template-fill-progress.tsx
git commit -m "feat(template-fill-web): 确认卡定位回调接入 liveTarget focusKey"
```

---

### Task 3: 构建验证与文档收尾

**Files:**
- Modify: `CHANGE.md`（顶部增量条目）
- Modify: `CLAUDE.md:66`（确认卡条目尾注「设计完成，未实施」→「已完成编码+测试，未部署」）

- [ ] **Step 1: 前端生产构建验证**

Run: `cd web && npm run build`
Expected: 构建成功（chunk 体积警告为存量，不计）；产物留在本地，不部署。

- [ ] **Step 2: CHANGE.md 增量条目**

Read `CHANGE.md`，顶部追加（风格对齐既有条目）：

- 日期：2026-09-14
- 主题：范本填写确认卡填写项点击定位跳转
- 核心变更点：confirm_pending 确认卡每个填写项字段名可点击（hover 虚线下划线，划线/未划线均可点），经父组件 `onLocate` 回调写入 `liveTarget({template_id, focusKey})`，复用未填充汇总同款 LivePreview 定位链路（data-ph-key → scrollIntoView 居中 + outline 闪烁 2s）；`onLocate` 未传时保持纯文本纯文本向后兼容；纯前端 2 文件，后端零改动。
- 测试：确认卡 jest 2 用例（onLocate 回传参数/未传不渲染 button）+ 相关套件回归 38 passed；`npm run build` 通过
- 遗留：未部署（纯前端 `npm run build` + tar + nginx reload 即可，无需重启后端）

- [ ] **Step 3: CLAUDE.md 尾注更新**

Read 项目 `CLAUDE.md` 参考表「确认卡填写项点击定位跳转」行，尾注「（设计完成，未实施）」改为「（已完成编码+测试，未部署：纯前端 build）」。

- [ ] **Step 4: 提交**

```bash
git add CHANGE.md CLAUDE.md
git commit -m "docs: 确认卡填写项点击定位跳转实施完成记录"
```

- [ ] **Step 5: 部署提醒（不执行）**

部署需用户明确指示：纯前端 `npm run build` + tar + SCP + nginx reload；后端无需重启。

---

## Self-Review 记录

- **Spec 覆盖**：§4.1 父组件传回调→Task 2；§4.2 确认卡 button 化+props→Task 1；§5 边界（划线可点/hover 下划线/未传纯文本）→Task 1 实现；§6 测试→Task 1 Step 1 + Task 2 Step 3；§7 部署→Task 3。无缺口。
- **占位符扫描**：所有代码步骤均含完整代码；无 TBD/TODO。
- **类型一致性**：`onLocate: (templateId: string, key: string) => void`（Task 1 定义 = Task 2 使用）；`liveTarget`/`setLiveTarget`/`focusKey` 均为 template-fill-progress.tsx 既有命名；测试 fixture 的 `ITemplateFillConfirmPending` 结构与 template-fill-stream.ts 实际类型一致（task_id/nonce/templates[].{template_id,name,candidates,predicted}）。
