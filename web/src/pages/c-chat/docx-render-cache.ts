// docx 渲染产物缓存（2026-09-21 大文件渲染优化）：
// docx-preview 的 renderAsync 是同步主线程全量建 DOM（JSZip 解压 + XML 解析 +
// 整树构建），大文件打开瞬间卡死，content-visibility 懒渲染只省排版/绘制救不了
// 这一步；而关闭弹框/抽屉组件卸载整棵 DOM 销毁，重开同一文件要整本重渲染。
// 本模块把渲染产物按 Blob 对象键（WeakMap——Blob 被 React Query 逐出后整树随之
// GC）摘进离屏容器保留，重开时 appendChild 回放（毫秒级）。
// 内存治理：树在世界里只存在一份（不在 el 就在 holder），缓存不使内存翻倍；
// LRU 上限 3 棵，防多文件轮流打开时离屏树无限囤积（forceClosedLivePreview 只
// 限「可见」树，管不到离屏缓存）。高亮产物不缓存语义：缓存树里可能带上一轮
// mark/产值 span，回放后由调用方 strip/rebuild + 按当前数据重涂（见
// docx-highlight 的 stripDocxAnnotationMarks / rebuildPlaceholderSpans）。

const MAX_ENTRIES = 3;
const stash = new WeakMap<Blob, HTMLDivElement>();
const order: Blob[] = []; // 新 → 旧；WeakMap 不可枚举，配额淘汰靠这条引用序

function ensureHolder(blob: Blob): HTMLDivElement {
  let holder = stash.get(blob);
  if (!holder) {
    holder = document.createElement('div');
    stash.set(blob, holder);
  }
  const i = order.indexOf(blob);
  if (i > 0) {
    order.splice(i, 1);
    order.unshift(blob);
  } else if (i < 0) {
    order.unshift(blob);
    // 配额淘汰：清空最旧 holder 的内容（WeakMap 条目保留，空 holder 等效失效）
    while (order.length > MAX_ENTRIES) {
      // while 条件已保证 order 非空，pop 必返回 Blob，non-null 断言安全
      const h = stash.get(order.pop()!);
      if (h) h.innerHTML = '';
    }
  }
  return holder;
}

/** 渲染 effect 卸载/依赖变更前调用：把 el 的渲染产物子树整体摘进离屏缓存。
 *  append 前先清空 holder——防御同一 Blob 被两个不同 el 先后 stash 时两棵树
 *  混在 holder（共享基建设防，计划调用流下不可达：stash 前 el 必已被清空）。 */
export function stashDocxRender(blob: Blob, el: HTMLElement): void {
  if (!el.firstElementChild) return;
  const holder = ensureHolder(blob);
  holder.innerHTML = '';
  while (el.firstChild) holder.appendChild(el.firstChild);
}

/** 重开回放：把缓存子树搬回 el。命中返回 true（调用方随后 strip/重涂）。
 *  契约：调用方须传空容器（重开挂载的干净 el）；非空容器内容会与缓存子树混排。 */
export function takeDocxRender(blob: Blob, el: HTMLElement): boolean {
  const holder = stash.get(blob);
  if (!holder || !holder.firstElementChild) return false;
  while (holder.firstChild) el.appendChild(holder.firstChild);
  return true;
}

/** 超大文档防线阈值：>2.5MB 的 docx 默认文本预览（设计 2026-09-16 预览内存
 *  治理；2026-09-21 起审核面板同样适用，常量收敛到本模块单一来源）。 */
export const BIG_BLOB_BYTES = 2.5 * 1024 * 1024;
