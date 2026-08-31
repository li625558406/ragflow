import { ListItemNode, ListNode } from '@lexical/list';
import {
  InitialConfigType,
  LexicalComposer,
} from '@lexical/react/LexicalComposer';
import { useLexicalComposerContext } from '@lexical/react/LexicalComposerContext';
import { ContentEditable } from '@lexical/react/LexicalContentEditable';
import { LexicalErrorBoundary } from '@lexical/react/LexicalErrorBoundary';
import { HistoryPlugin } from '@lexical/react/LexicalHistoryPlugin';
import { ListPlugin } from '@lexical/react/LexicalListPlugin';
import { RichTextPlugin } from '@lexical/react/LexicalRichTextPlugin';
import {
  HeadingNode,
  HeadingTagType,
  SerializedHeadingNode,
} from '@lexical/rich-text';
import { mergeRegister } from '@lexical/utils';
import {
  $applyNodeReplacement,
  $createTextNode,
  $getRoot,
  $getSelection,
  $isElementNode,
  $isRangeSelection,
  $isRootNode,
  $isTextNode,
  CLICK_COMMAND,
  COMMAND_PRIORITY_EDITOR,
  COMMAND_PRIORITY_HIGH,
  DecoratorNode,
  EditorConfig,
  ElementNode,
  INDENT_CONTENT_COMMAND,
  LexicalEditor,
  LexicalNode,
  LexicalUpdateJSON,
  NodeKey,
  OUTDENT_CONTENT_COMMAND,
  ParagraphNode,
  PASTE_COMMAND,
  RangeSelection,
  SerializedLexicalNode,
  SerializedParagraphNode,
  SerializedTextNode,
  TextNode,
} from 'lexical';
import React, { useEffect, useRef } from 'react';
import {
  diffBlocks,
  DocxDiffOps,
  DocxSourceParagraph,
  EditorBlock,
  splitIntoSegments,
} from './docx-diff';
import type { DocxRun } from './docx-format-utils';
import { parseStyle, runsFmtSig, stripBgFromStyle } from './docx-format-utils';
import DocxToolbar from './docx-toolbar';

// ── 自定义节点 ──────────────────────────────────────────────
// paraIndex 仅在初始内容灌入（buildInitialContent）时赋值；Lexical 内部
// 克隆（回车分段等）走 insertNewAfter/无参构造 → 新实例 paraIndex 为
// undefined，天然区隔「原文段落」与「新增段落」，diff 无需解析 DOM。

/** 文档正文段（对应 source paragraph type=paragraph） */
export class DocxParagraphNode extends ParagraphNode {
  __paraIndex: number | undefined;

  constructor(paraIndex?: number, key?: NodeKey) {
    super(key);
    this.__paraIndex = paraIndex;
  }

  static getType(): string {
    return 'docx-paragraph';
  }

  static clone(node: DocxParagraphNode): DocxParagraphNode {
    return new DocxParagraphNode(node.__paraIndex, node.__key);
  }

  static importJSON(json: Record<string, unknown>): DocxParagraphNode {
    const node = new DocxParagraphNode(json.paraIndex as number | undefined);
    node.updateFromJSON(json as LexicalUpdateJSON<SerializedParagraphNode>);
    return node;
  }

  exportJSON(): SerializedParagraphNode & { paraIndex: number | undefined } {
    return {
      ...super.exportJSON(),
      type: 'docx-paragraph',
      paraIndex: this.__paraIndex,
    };
  }

  // 回车分段：新段为本类（无 paraIndex → diff 记 insert）
  insertNewAfter(
    _selection: RangeSelection,
    restoreSelection = true,
  ): DocxParagraphNode {
    const node = $applyNodeReplacement(new DocxParagraphNode(undefined));
    node.__indent = this.__indent;
    node.__dir = this.__dir;
    node.__format = this.__format;
    node.__style = this.__style;
    this.insertAfter(node, restoreSelection);
    return node;
  }

  createDOM(config: EditorConfig): HTMLElement {
    const dom = super.createDOM(config);
    if (this.__paraIndex != null) {
      dom.setAttribute('data-para-index', String(this.__paraIndex));
    }
    dom.className += ' text-[14px] leading-[2] text-justify text-[#333333]';
    dom.style.textIndent = '2em';
    return dom;
  }
}

/** 文档标题段（对应 source paragraph type=heading，tag 由 heading_level 派生） */
export class DocxHeadingNode extends HeadingNode {
  __paraIndex: number | undefined;

  constructor(tag: HeadingTagType, paraIndex?: number, key?: NodeKey) {
    super(tag, key);
    this.__paraIndex = paraIndex;
  }

  static getType(): string {
    return 'docx-heading';
  }

  static clone(node: DocxHeadingNode): DocxHeadingNode {
    return new DocxHeadingNode(node.__tag, node.__paraIndex, node.__key);
  }

  static importJSON(json: Record<string, unknown>): DocxHeadingNode {
    const node = new DocxHeadingNode(
      (json.tag as HeadingTagType) || 'h3',
      json.paraIndex as number | undefined,
    );
    node.updateFromJSON(json as LexicalUpdateJSON<SerializedHeadingNode>);
    return node;
  }

  exportJSON(): SerializedHeadingNode & { paraIndex: number | undefined } {
    return {
      ...super.exportJSON(),
      type: 'docx-heading',
      paraIndex: this.__paraIndex,
    };
  }

  insertNewAfter(
    _selection: RangeSelection,
    restoreSelection = true,
  ): DocxHeadingNode {
    const node = $applyNodeReplacement(
      new DocxHeadingNode(this.__tag, undefined),
    );
    node.__indent = this.__indent;
    node.__dir = this.__dir;
    node.__format = this.__format;
    this.insertAfter(node, restoreSelection);
    return node;
  }

  createDOM(config: EditorConfig): HTMLElement {
    const dom = super.createDOM(config);
    if (this.__paraIndex != null) {
      dom.setAttribute('data-para-index', String(this.__paraIndex));
    }
    dom.className += ' text-[15px] font-bold text-[#1A1A1A] mt-5 mb-2';
    return dom;
  }
}

/** 批注/标注高亮文本片段：带 data-anchor-key 供引线锚定与点击联动 */
export class HighlightTextNode extends TextNode {
  __anchorKey: string;

  constructor(text: string, anchorKey: string, key?: NodeKey) {
    super(text, key);
    this.__anchorKey = anchorKey;
  }

  static getType(): string {
    return 'highlight-text';
  }

  static clone(node: HighlightTextNode): HighlightTextNode {
    return new HighlightTextNode(node.__text, node.__anchorKey, node.__key);
  }

  static importJSON(json: Record<string, unknown>): HighlightTextNode {
    const node = new HighlightTextNode(
      (json.text as string) || '',
      (json.anchorKey as string) || '',
    );
    node.updateFromJSON(json as LexicalUpdateJSON<SerializedTextNode>);
    return node;
  }

  exportJSON(): SerializedTextNode & { anchorKey: string } {
    return {
      ...super.exportJSON(),
      type: 'highlight-text',
      anchorKey: this.__anchorKey,
    };
  }

  createDOM(config: EditorConfig): HTMLElement {
    const dom = super.createDOM(config);
    dom.setAttribute('data-anchor-key', this.__anchorKey);
    return dom;
  }
}

// 表格/图片原子块的 React 渲染函数由父级经 Context 注入（闭包持有
// review-panel 的高亮/批注状态），decorate 时从 Context 取
const AtomicRenderContext = React.createContext<
  (p: {
    paraIndex: number;
    kind: 'table' | 'image';
    html: string;
  }) => React.ReactNode
>(() => null);

/** 表格/图片只读原子块：contenteditable=false，不可编辑、不可改写 */
export class AtomicBlockNode extends DecoratorNode<React.ReactNode> {
  __paraIndex: number;
  __kind: 'table' | 'image';
  __html: string;

  constructor(
    paraIndex: number,
    kind: 'table' | 'image',
    html: string,
    key?: NodeKey,
  ) {
    super(key);
    this.__paraIndex = paraIndex;
    this.__kind = kind;
    this.__html = html;
  }

  static getType(): string {
    return 'atomic-block';
  }

  static clone(node: AtomicBlockNode): AtomicBlockNode {
    return new AtomicBlockNode(
      node.__paraIndex,
      node.__kind,
      node.__html,
      node.__key,
    );
  }

  static importJSON(json: Record<string, unknown>): AtomicBlockNode {
    return new AtomicBlockNode(
      (json.paraIndex as number) ?? -1,
      (json.kind as 'table' | 'image') || 'table',
      (json.html as string) || '',
    );
  }

  exportJSON(): SerializedLexicalNode & {
    paraIndex: number;
    kind: 'table' | 'image';
    html: string;
  } {
    return {
      type: 'atomic-block',
      version: 1,
      paraIndex: this.__paraIndex,
      kind: this.__kind,
      html: this.__html,
    };
  }

  createDOM(): HTMLElement {
    const div = document.createElement('div');
    div.setAttribute('data-para-index', String(this.__paraIndex));
    div.contentEditable = 'false';
    div.className = 'relative py-0.5';
    return div;
  }

  updateDOM(): false {
    return false;
  }

  decorate(): React.ReactNode {
    return (
      <AtomicRenderContext.Consumer>
        {(render) =>
          render({
            paraIndex: this.__paraIndex,
            kind: this.__kind,
            html: this.__html,
          })
        }
      </AtomicRenderContext.Consumer>
    );
  }
}

// ── 模型抽取与 diff ─────────────────────────────────────────

/** 抽取一个文本块的 run 序列：format 位 + style 串 → DocxRun；
 * HighlightTextNode 的 background 是批注 UI 色不落盘（其余 format 正常）；
 * 相邻同样式 run 合并；全默认格式返回 undefined（保持旧整段替换行为）。
 * 不变量：各 run.text 拼接等于块 text（供后端 runs 与 new_text 一致性校验） */
function $extractRuns(block: ElementNode): DocxRun[] | undefined {
  const runs: DocxRun[] = [];
  const visit = (n: LexicalNode) => {
    if ($isElementNode(n)) {
      for (const c of n.getChildren()) visit(c);
      return;
    }
    if (!$isTextNode(n)) return;
    const isHl = n instanceof HighlightTextNode;
    const styleMap = parseStyle(
      isHl ? stripBgFromStyle(n.getStyle()) : n.getStyle(),
    );
    const run: DocxRun = { text: n.getTextContent() };
    if (n.hasFormat('bold')) run.bold = true;
    if (n.hasFormat('italic')) run.italic = true;
    if (n.hasFormat('underline')) run.underline = true;
    if (n.hasFormat('strikethrough')) run.strike = true;
    if (n.hasFormat('superscript')) run.superscript = true;
    if (n.hasFormat('subscript')) run.subscript = true;
    if (styleMap['color']) run.color = styleMap['color'];
    if (styleMap['background-color'])
      run.bg_color = styleMap['background-color'];
    if (styleMap['font-family']) run.font = styleMap['font-family'];
    if (styleMap['font-size']) {
      const pt = parseFloat(styleMap['font-size']);
      if (Number.isFinite(pt)) run.size = pt;
    }
    runs.push(run);
  };
  for (const c of block.getChildren()) visit(c);

  const merged: DocxRun[] = [];
  for (const r of runs) {
    if (!r.text) continue;
    const prev = merged[merged.length - 1];
    const styleOf = (x: DocxRun) => JSON.stringify({ ...x, text: undefined });
    if (prev && styleOf(prev) === styleOf(r)) prev.text += r.text;
    else merged.push({ ...r });
  }
  const hasFmt = merged.some((r) => Object.keys(r).some((k) => k !== 'text'));
  return hasFmt ? merged : undefined;
}

export function readEditorBlocks(editor: LexicalEditor): EditorBlock[] {
  return editor.read(() => {
    const out: EditorBlock[] = [];
    for (const child of $getRoot().getChildren()) {
      if (child instanceof AtomicBlockNode) {
        out.push({
          paraIndex: child.__paraIndex,
          kind: child.__kind,
          text: '',
        });
        continue;
      }
      const paraIndex =
        child instanceof DocxParagraphNode || child instanceof DocxHeadingNode
          ? child.__paraIndex
          : undefined;
      const runs = $isElementNode(child) ? $extractRuns(child) : undefined;
      out.push({
        paraIndex,
        kind: 'text',
        text: child.getTextContent(),
        runs,
        fmtSig: runsFmtSig(runs),
      });
    }
    return out;
  });
}

/** 把选区涉及的顶级块替换为目标类型（正文↔标题），保留原 paraIndex 语义：
 * 改原文段标题层级仍 edit 原段；新增段保持无 index 记 insert。
 * 列表块（ListNode>ListItemNode）替换为正文/标题时解包 ListItem，
 * 避免 ListItemNode 直接挂到 Paragraph/Heading 下的非法模型；
 * 有意不保留原块的段落级属性（缩进/对齐/style）——切换块类型即重置 */
export function $applyDocxBlockType(
  make: (paraIndex: number | undefined) => ElementNode,
): void {
  const selection = $getSelection();
  if (!$isRangeSelection(selection)) return;
  const blocks = new Set<ElementNode>();
  for (const node of selection.getNodes()) {
    let cur: LexicalNode | null = node;
    while (cur && !$isRootNode(cur.getParent())) cur = cur.getParent();
    if (cur && $isElementNode(cur) && !$isRootNode(cur)) blocks.add(cur);
  }
  for (const b of blocks) {
    const paraIndex =
      b instanceof DocxParagraphNode || b instanceof DocxHeadingNode
        ? b.__paraIndex
        : undefined;
    const nb = make(paraIndex);
    for (const c of b.getChildren()) {
      if (c instanceof ListItemNode) {
        for (const cc of c.getChildren()) nb.append(cc);
      } else {
        nb.append(c);
      }
    }
    b.replace(nb);
  }
}

export function collectEditorOps(
  editor: LexicalEditor,
  paragraphs: DocxSourceParagraph[],
): DocxDiffOps {
  return diffBlocks(readEditorBlocks(editor), paragraphs);
}

// ── 插件 ────────────────────────────────────────────────────

/** 初始内容灌入：挂载后一次性构建文档段落（绕开 initialEditorState，
 * 该回调在本项目环境下不触发）；须排在 HighlightPlugin 之前，
 * 保证先有正文再拆高亮 */
function InitialContentPlugin({
  paragraphs,
}: {
  paragraphs: DocxSourceParagraph[];
}) {
  const [editor] = useLexicalComposerContext();
  const doneRef = useRef(false);
  useEffect(() => {
    if (doneRef.current) return;
    doneRef.current = true;
    buildInitialContent(editor, paragraphs);
  }, [editor, paragraphs]);
  return null;
}

/** 批注高亮：targetsByPara 身份变化时重建高亮片段（含批注删除后清除残留高亮）；
 * 打字过程不重拆（父级须用 useMemo 稳定 targetsByPara），避免光标跳动 */
function HighlightPlugin({
  targetsByPara,
}: {
  targetsByPara: Map<
    number,
    Array<{ text: string; color: string; key: string }>
  >;
}) {
  const [editor] = useLexicalComposerContext();
  useEffect(() => {
    editor.update(
      () => {
        const root = $getRoot();
        for (const child of root.getChildren()) {
          if (
            !(child instanceof DocxParagraphNode) &&
            !(child instanceof DocxHeadingNode)
          )
            continue;
          const paraIndex = child.__paraIndex;
          if (paraIndex == null) continue;
          const targets = targetsByPara.get(paraIndex) || [];
          const hasStale = child
            .getChildren()
            .some((n) => n instanceof HighlightTextNode);
          if (!targets.length && !hasStale) continue;
          const segments = splitIntoSegments(child.getTextContent(), targets);
          child.clear();
          for (const seg of segments) {
            if (seg.key) {
              const node = new HighlightTextNode(seg.text, seg.key);
              node.setStyle(`background-color:${seg.color}22;`);
              child.append(node);
            } else {
              child.append($createTextNode(seg.text));
            }
          }
        }
      },
      { tag: 'history-merge' },
    );
  }, [editor, targetsByPara]);
  return null;
}

/** 点击高亮 → 联动右侧卡片（复用 review-panel 的 handleAnchorClick） */
function ClickPlugin({
  onAnchorClick,
}: {
  onAnchorClick: (key: string) => void;
}) {
  const [editor] = useLexicalComposerContext();
  useEffect(() => {
    return editor.registerCommand(
      CLICK_COMMAND,
      (event: MouseEvent) => {
        const target = event.target as HTMLElement | null;
        const el = target?.closest('[data-anchor-key]');
        if (el) {
          event.preventDefault();
          onAnchorClick(el.getAttribute('data-anchor-key') || '');
          return true;
        }
        return false;
      },
      COMMAND_PRIORITY_HIGH,
    );
  }, [editor, onAnchorClick]);
  return null;
}

/** 粘贴降级纯文本：换行替换为空格（单段语义，防外部富文本破坏结构） */
function PastePlugin() {
  const [editor] = useLexicalComposerContext();
  useEffect(() => {
    return editor.registerCommand(
      PASTE_COMMAND,
      (event: ClipboardEvent) => {
        const text = event.clipboardData?.getData('text/plain');
        if (text == null) return false;
        event.preventDefault();
        editor.update(() => {
          const selection = $getSelection();
          if (selection) selection.insertText(text.replace(/\r?\n/g, ' '));
        });
        return true;
      },
      COMMAND_PRIORITY_HIGH,
    );
  }, [editor]);
  return null;
}

/** 缩进：core 只定义 INDENT/OUTDENT 命令不注册处理器，这里对选区顶级块
 * setIndent（0-8 封顶/封底）；缩进不进 diff 落盘契约，仅编辑器视觉 */
function IndentPlugin() {
  const [editor] = useLexicalComposerContext();
  useEffect(() => {
    const apply = (delta: number) => () => {
      const selection = $getSelection();
      if (!$isRangeSelection(selection)) return false;
      const blocks = new Set<ElementNode>();
      for (const node of selection.getNodes()) {
        let cur: LexicalNode | null = node;
        while (cur && !$isRootNode(cur.getParent())) cur = cur.getParent();
        if (cur && $isElementNode(cur) && !$isRootNode(cur)) blocks.add(cur);
      }
      blocks.forEach((b) =>
        b.setIndent(Math.max(0, Math.min(8, b.getIndent() + delta))),
      );
      return true;
    };
    return mergeRegister(
      editor.registerCommand(
        INDENT_CONTENT_COMMAND,
        apply(1),
        COMMAND_PRIORITY_EDITOR,
      ),
      editor.registerCommand(
        OUTDENT_CONTENT_COMMAND,
        apply(-1),
        COMMAND_PRIORITY_EDITOR,
      ),
    );
  }, [editor]);
  return null;
}

/** 脏检查：任何编辑器更新后抽块描述抛给父级（父级防抖后 diff 计数） */
function DirtyPlugin({
  onBlocksChange,
}: {
  onBlocksChange: (blocks: EditorBlock[]) => void;
}) {
  const [editor] = useLexicalComposerContext();
  useEffect(() => {
    return editor.registerUpdateListener(() => {
      onBlocksChange(readEditorBlocks(editor));
    });
  }, [editor, onBlocksChange]);
  return null;
}

/** 把 LexicalEditor 实例抛给父级（保存时调 collectEditorOps 用） */
function EditorRefPlugin({
  editorRef,
}: {
  editorRef: { current: LexicalEditor | null };
}) {
  const [editor] = useLexicalComposerContext();
  useEffect(() => {
    editorRef.current = editor;
    return () => {
      editorRef.current = null;
    };
  }, [editor, editorRef]);
  return null;
}

// ── 组件 ────────────────────────────────────────────────────

function buildInitialContent(
  editor: LexicalEditor,
  paragraphs: DocxSourceParagraph[],
) {
  // history-merge：灌入不进撤销栈 —— 否则「LexicalComposer 默认空段 + 15 段」
  // 这个快照会成为 undo 终点，Ctrl+Z 到底后残留 1 个头部空段。
  editor.update(
    () => {
      const root = $getRoot();
      // LexicalComposer 在 initialEditorState 为 undefined 时会先灌一个默认空
      // ParagraphNode，若不清理会以无 data-para-index 空段形式混进块列表。
      root.clear();
      for (const para of paragraphs) {
        if (para.type === 'table' || para.type === 'image') {
          root.append(new AtomicBlockNode(para.index, para.type, para.text));
        } else if (para.type === 'heading') {
          const tag = (
            para.heading_level && para.heading_level <= 3
              ? `h${para.heading_level + 1}`
              : 'h3'
          ) as HeadingTagType;
          const h = new DocxHeadingNode(tag, para.index);
          h.append($createTextNode(para.text));
          root.append(h);
        } else {
          const p = new DocxParagraphNode(para.index);
          p.append($createTextNode(para.text));
          root.append(p);
        }
      }
    },
    { tag: 'history-merge' },
  );
}

export default function DocxParagraphEditor({
  paragraphs,
  targetsByPara,
  onAnchorClick,
  renderAtomic,
  editorRef,
  onBlocksChange,
  toolbarPortal,
  dirty,
  saving,
  onSave,
  onDiscard,
}: {
  paragraphs: DocxSourceParagraph[];
  /** 高亮目标集：身份变化会触发高亮重建（历史合并标签，不入撤销栈），
   * 必须用 useMemo 稳定该 Map，否则打字期间会重拆高亮导致光标跳动 */
  targetsByPara: Map<
    number,
    Array<{ text: string; color: string; key: string }>
  >;
  onAnchorClick: (key: string) => void;
  renderAtomic: (p: {
    paraIndex: number;
    kind: 'table' | 'image';
    html: string;
  }) => React.ReactNode;
  editorRef: { current: LexicalEditor | null };
  onBlocksChange: (blocks: EditorBlock[]) => void;
  /** 工具栏 portal 宿主（父级吸顶容器），null 时工具栏不渲染 */
  toolbarPortal?: HTMLElement | null;
  dirty?: number;
  saving?: boolean;
  onSave?: () => void;
  onDiscard?: () => void;
}) {
  // paragraphs 固定于挂载时刻（初始内容由 InitialContentPlugin 在挂载后灌入）；
  // 文档刷新/放弃修改由父级换 key 重挂载。
  // targetsByPara 约定：其身份变化会触发 HighlightPlugin 高亮重建（history-merge
  // 标签，不入撤销栈），父级必须用 useMemo 稳定该 Map，否则打字期间会重拆高亮导致光标跳动
  return (
    <LexicalComposer
      initialConfig={
        {
          namespace: 'docx-review-editor',
          nodes: [
            DocxParagraphNode,
            DocxHeadingNode,
            AtomicBlockNode,
            HighlightTextNode,
            HeadingNode,
            ParagraphNode,
            ListNode,
            ListItemNode,
          ],
          theme: {},
          onError: (error: Error) => console.error('[docx-editor]', error),
        } as InitialConfigType
      }
    >
      <AtomicRenderContext.Provider value={renderAtomic}>
        <DocxToolbar
          portal={toolbarPortal ?? null}
          dirty={dirty ?? 0}
          saving={saving ?? false}
          onSave={onSave ?? (() => {})}
          onDiscard={onDiscard ?? (() => {})}
        />
        <RichTextPlugin
          contentEditable={
            <ContentEditable className="space-y-2 outline-none focus:outline-none" />
          }
          placeholder={null}
          ErrorBoundary={LexicalErrorBoundary}
        />
        <HistoryPlugin />
        <ListPlugin />
        <IndentPlugin />
        <InitialContentPlugin paragraphs={paragraphs} />
        <HighlightPlugin targetsByPara={targetsByPara} />
        <ClickPlugin onAnchorClick={onAnchorClick} />
        <PastePlugin />
        <DirtyPlugin onBlocksChange={onBlocksChange} />
        <EditorRefPlugin editorRef={editorRef} />
      </AtomicRenderContext.Provider>
    </LexicalComposer>
  );
}
