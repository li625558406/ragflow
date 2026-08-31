// web/src/pages/c-chat/docx-toolbar.tsx
// Word ribbon 简化风工具栏：在 LexicalComposer 内渲染，portal 到父级吸顶容器。
// 激活态订阅 selection 变化同步读；全部格式按钮 onMouseDown preventDefault 防丢选区。
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from '@/components/ui/popover';
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from '@/components/ui/tooltip';
import {
  INSERT_ORDERED_LIST_COMMAND,
  INSERT_UNORDERED_LIST_COMMAND,
} from '@lexical/list';
import { useLexicalComposerContext } from '@lexical/react/LexicalComposerContext';
import {
  $getSelection,
  $isElementNode,
  $isRangeSelection,
  $isRootNode,
  $isTextNode,
  $setSelection,
  FORMAT_ELEMENT_COMMAND,
  FORMAT_TEXT_COMMAND,
  INDENT_CONTENT_COMMAND,
  OUTDENT_CONTENT_COMMAND,
  REDO_COMMAND,
  UNDO_COMMAND,
  type ElementNode,
  type LexicalNode,
  type RangeSelection,
} from 'lexical';
import {
  AlignCenter,
  AlignJustify,
  AlignLeft,
  AlignRight,
  ChevronDown,
  Highlighter,
  IndentDecrease,
  IndentIncrease,
  List,
  ListOrdered,
  Redo2,
  RemoveFormatting,
  Undo2,
} from 'lucide-react';
import { useEffect, useRef, useState, type ReactNode } from 'react';
import { createPortal } from 'react-dom';
import { mergeStyle, parseStyle } from './docx-format-utils';
import {
  $applyDocxBlockType,
  DocxHeadingNode,
  DocxParagraphNode,
} from './docx-paragraph-editor';

// ── 常量 ────────────────────────────────────────────────────

const FONT_FAMILIES = [
  '宋体',
  '黑体',
  '楷体',
  '仿宋',
  '微软雅黑',
  'Times New Roman',
  'Arial',
];
const FONT_SIZES: Array<{ label: string; pt: number }> = [
  { label: '初号 42', pt: 42 },
  { label: '小初 36', pt: 36 },
  { label: '一号 26', pt: 26 },
  { label: '二号 22', pt: 22 },
  { label: '小二 18', pt: 18 },
  { label: '三号 16', pt: 16 },
  { label: '四号 14', pt: 14 },
  { label: '小四 12', pt: 12 },
  { label: '五号 10.5', pt: 10.5 },
  { label: '小五 9', pt: 9 },
];
const TEXT_COLORS = [
  '#000000',
  '#595959',
  '#8C8C8C',
  '#FF4D4F',
  '#FA8C16',
  '#FADB14',
  '#52C41A',
  '#13C2C2',
  '#1890FF',
  '#722ED1',
];
const BG_COLORS = ['#FFF1B8', '#FFA39E', '#B7EB8F', '#91D5FF', '#D3ADF7'];

// TextNode format 位（TEXT_TYPE_TO_FORMAT）
const FMT_BOLD = 1;
const FMT_ITALIC = 2;
const FMT_STRIKE = 4;
const FMT_UNDERLINE = 8;
const FMT_SUB = 32;
const FMT_SUP = 64;

const BTN_BASE =
  'inline-flex h-7 min-w-7 items-center justify-center gap-0.5 rounded px-1 text-[13px] leading-none transition-colors disabled:cursor-not-allowed disabled:opacity-40';

// ── 基础控件 ────────────────────────────────────────────────

/** 小工具按钮：Tooltip bottom + 激活态高亮；onMouseDown preventDefault 保选区 */
function TBtn({
  title,
  active = false,
  disabled = false,
  onClick,
  children,
}: {
  title: string;
  active?: boolean;
  disabled?: boolean;
  onClick: () => void;
  children: ReactNode;
}) {
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <button
          type="button"
          title={title}
          disabled={disabled}
          onMouseDown={(e) => e.preventDefault()}
          onClick={onClick}
          className={`${BTN_BASE} ${
            active
              ? 'bg-[#E8F0FF] text-[#1a66fb]'
              : 'text-[#333333] hover:bg-[#F0F2F5]'
          }`}
        >
          {children}
        </button>
      </TooltipTrigger>
      <TooltipContent side="bottom" className="text-xs">
        {title}
      </TooltipContent>
    </Tooltip>
  );
}

function GroupDivider() {
  return <span className="mx-1 h-5 w-px shrink-0 bg-[#E5E5E5]" />;
}

/** 下拉触发按钮（块类型/字体/字号共用）。
 * onMouseDown preventDefault 防止点击瞬间丢选区；Radix 菜单抢焦点导致
 * 的选区塌陷由主组件的 lastSelRef 回挂兜底（$ensureSelection） */
function SelectTrigger({ label, width }: { label: string; width: string }) {
  return (
    <DropdownMenuTrigger asChild>
      <button
        type="button"
        onMouseDown={(e) => e.preventDefault()}
        className={`inline-flex h-7 items-center justify-between gap-0.5 rounded px-1.5 text-[12px] text-[#333333] transition-colors hover:bg-[#F0F2F5] ${width}`}
      >
        <span className="truncate">{label}</span>
        <ChevronDown className="h-3 w-3 shrink-0 text-[#999999]" />
      </button>
    </DropdownMenuTrigger>
  );
}

function MenuItem({
  active = false,
  onClick,
  children,
}: {
  active?: boolean;
  onClick: () => void;
  children: ReactNode;
}) {
  return (
    <DropdownMenuItem
      onMouseDown={(e) => e.preventDefault()}
      onClick={onClick}
      className={`cursor-pointer text-[13px] ${active ? 'bg-[#E8F0FF] text-[#1a66fb]' : ''}`}
    >
      {children}
    </DropdownMenuItem>
  );
}

/** 浮层（色板 Popover）打开时不抢焦点（保 Lexical 选区） */
const keepFocus = (e: Event) => e.preventDefault();

// ── 编辑器内操作（须在 editor.update / editorState.read 内） ──

/** 给选区 extract 出的节点设置/剔除 inline style（setStyle 存在才调，
 * 兼容 ElementNode 等无 style 的节点）；sel 由调用方 $ensureSelection 提供 */
function $applySelectionStyle(
  sel: RangeSelection,
  set: Record<string, string>,
  removeKeys: string[] = [],
): void {
  for (const n of sel.extract()) {
    const anyN = n as {
      setStyle?: (s: string) => void;
      getStyle?: () => string;
    };
    if (anyN.setStyle && anyN.getStyle) {
      anyN.setStyle(mergeStyle(anyN.getStyle(), set, removeKeys));
    }
  }
}

/** 清除选区格式：style 清空 + 文本 format 位清零 */
function $clearSelectionFormat(sel: RangeSelection): void {
  for (const n of sel.extract()) {
    const anyN = n as {
      setStyle?: (s: string) => void;
      setFormat?: (f: number) => void;
    };
    if (anyN.setStyle) anyN.setStyle('');
    if (anyN.setFormat) anyN.setFormat(0);
  }
}

// ── 主组件 ──────────────────────────────────────────────────

export default function DocxToolbar({
  portal,
  dirty,
  saving,
  onSave,
  onDiscard,
}: {
  /** 父级提供的吸顶容器 DOM；null 时不渲染 */
  portal: HTMLElement | null;
  /** 待保存变更块数（>0 显示胶囊、启用保存区） */
  dirty: number;
  saving: boolean;
  onSave: () => void;
  onDiscard: () => void;
}) {
  const [editor] = useLexicalComposerContext();
  // 最近一次有效选区缓存：Radix DropdownMenu 为 modal 且公开类型不含
  // onOpenAutoFocus，打开菜单会抢焦点丢 DOM selection；apply 前用缓存回挂
  const lastSelRef = useRef<RangeSelection | null>(null);
  const [hasSel, setHasSel] = useState(false);
  const [fmt, setFmt] = useState(0);
  const [blockKind, setBlockKind] = useState<'p' | 'h'>('p');
  const [align, setAlign] = useState('');
  const [fontFamily, setFontFamily] = useState('');
  const [fontSize, setFontSize] = useState('');

  // 激活态：selection 变化时同步读（anchor 上溯顶级块判断块类型/对齐，
  // 首个 TextNode 的 style 回显字体字号）
  useEffect(() => {
    return editor.registerUpdateListener(({ editorState }) => {
      editorState.read(() => {
        const sel = $getSelection();
        if (!$isRangeSelection(sel)) {
          setHasSel(false);
          return;
        }
        setHasSel(true);
        lastSelRef.current = sel;
        setFmt(sel.format);

        // 锚点上溯到根下第一层顶级块
        let block: LexicalNode | null = sel.anchor.getNode();
        let parent = block.getParent();
        while (parent && !$isRootNode(parent)) {
          block = parent;
          parent = parent.getParent();
        }
        if (block && $isElementNode(block)) {
          setBlockKind(block instanceof DocxHeadingNode ? 'h' : 'p');
          // 对齐存在 element.__format（不在 sel.format 里）；取不到则不亮对齐钮
          const el = block as ElementNode;
          const ft =
            typeof el.getFormatType === 'function'
              ? (el.getFormatType() as string)
              : '';
          setAlign(ft || '');
        } else {
          setBlockKind('p');
          setAlign('');
        }

        // 字体字号回显：选区内首个 TextNode 的 style
        let font = '';
        let size = '';
        for (const n of sel.getNodes()) {
          if (!$isTextNode(n)) continue;
          const st = parseStyle(n.getStyle());
          font = st['font-family'] || '';
          size = st['font-size'] || '';
          break;
        }
        setFontFamily(font);
        setFontSize(size);
      });
    });
  }, [editor]);

  if (!portal) return null;

  /** apply 前取可用选区：当前无效则回挂最近缓存（editor.update 内调用） */
  const $ensureSelection = (): RangeSelection | null => {
    const sel = $getSelection();
    if ($isRangeSelection(sel)) return sel;
    const cached = lastSelRef.current;
    if (cached && $isRangeSelection(cached)) {
      $setSelection(cached.clone());
      return cached;
    }
    return null;
  };

  const sizeLabel =
    FONT_SIZES.find(
      (s) => String(s.pt) === parseFloat(fontSize || '').toString(),
    )?.label ||
    fontSize ||
    '字号';

  const applyFont = (name: string) => {
    editor.update(() => {
      const sel = $ensureSelection();
      if (!sel) return;
      $applySelectionStyle(sel, { 'font-family': name });
    });
  };
  const applySize = (pt: number) => {
    editor.update(() => {
      const sel = $ensureSelection();
      if (!sel) return;
      $applySelectionStyle(sel, { 'font-size': `${pt}pt` });
    });
  };
  const applyColor = (hex: string | null) => {
    editor.update(() => {
      const sel = $ensureSelection();
      if (!sel) return;
      if (hex) $applySelectionStyle(sel, { color: hex });
      else $applySelectionStyle(sel, {}, ['color']);
    });
  };
  const applyBg = (hex: string | null) => {
    editor.update(() => {
      const sel = $ensureSelection();
      if (!sel) return;
      if (hex) $applySelectionStyle(sel, { 'background-color': hex });
      else $applySelectionStyle(sel, {}, ['background-color']);
    });
  };
  const toggleText = (
    t:
      | 'bold'
      | 'italic'
      | 'underline'
      | 'strikethrough'
      | 'superscript'
      | 'subscript',
  ) => {
    editor.dispatchCommand(FORMAT_TEXT_COMMAND, t);
  };
  const alignTo = (t: 'left' | 'center' | 'right' | 'justify') => {
    editor.dispatchCommand(FORMAT_ELEMENT_COMMAND, t);
  };
  const applyBlock = (make: (i: number | undefined) => ElementNode) => {
    editor.update(() => {
      if (!$ensureSelection()) return;
      $applyDocxBlockType(make);
    });
  };

  return createPortal(
    <TooltipProvider delayDuration={300}>
      <div className="flex flex-wrap items-center rounded-lg border border-[#E5E5E5] bg-white px-2 py-1 shadow-sm">
        {/* 历史 */}
        <TBtn
          title="撤销"
          onClick={() => editor.dispatchCommand(UNDO_COMMAND, undefined)}
        >
          <Undo2 className="h-4 w-4" />
        </TBtn>
        <TBtn
          title="重做"
          onClick={() => editor.dispatchCommand(REDO_COMMAND, undefined)}
        >
          <Redo2 className="h-4 w-4" />
        </TBtn>
        <GroupDivider />

        {/* 块类型 */}
        <DropdownMenu>
          <SelectTrigger
            label={blockKind === 'h' ? '标题' : '正文'}
            width="w-16"
          />
          <DropdownMenuContent align="start" className="w-32">
            <MenuItem
              active={blockKind === 'p'}
              onClick={() => applyBlock((i) => new DocxParagraphNode(i))}
            >
              正文
            </MenuItem>
            <MenuItem
              active={blockKind === 'h'}
              onClick={() => applyBlock((i) => new DocxHeadingNode('h2', i))}
            >
              标题 2
            </MenuItem>
            <MenuItem
              onClick={() => applyBlock((i) => new DocxHeadingNode('h3', i))}
            >
              标题 3
            </MenuItem>
            <MenuItem
              onClick={() => applyBlock((i) => new DocxHeadingNode('h4', i))}
            >
              标题 4
            </MenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
        <GroupDivider />

        {/* 字体 */}
        <DropdownMenu>
          <SelectTrigger label={fontFamily || '字体'} width="w-24" />
          <DropdownMenuContent align="start" className="w-40">
            {FONT_FAMILIES.map((f) => (
              <MenuItem
                key={f}
                active={fontFamily === f}
                onClick={() => applyFont(f)}
              >
                <span style={{ fontFamily: f }}>{f}</span>
              </MenuItem>
            ))}
          </DropdownMenuContent>
        </DropdownMenu>

        {/* 字号 */}
        <DropdownMenu>
          <SelectTrigger label={sizeLabel} width="w-20" />
          <DropdownMenuContent align="start" className="w-28">
            {FONT_SIZES.map((s) => (
              <MenuItem
                key={s.label}
                active={sizeLabel === s.label}
                onClick={() => applySize(s.pt)}
              >
                {s.label}
              </MenuItem>
            ))}
          </DropdownMenuContent>
        </DropdownMenu>
        <GroupDivider />

        {/* B/I/U/S/上标/下标 */}
        <TBtn
          title="加粗"
          active={!!(fmt & FMT_BOLD)}
          disabled={!hasSel}
          onClick={() => toggleText('bold')}
        >
          <span className="font-bold">B</span>
        </TBtn>
        <TBtn
          title="斜体"
          active={!!(fmt & FMT_ITALIC)}
          disabled={!hasSel}
          onClick={() => toggleText('italic')}
        >
          <span className="italic">I</span>
        </TBtn>
        <TBtn
          title="下划线"
          active={!!(fmt & FMT_UNDERLINE)}
          disabled={!hasSel}
          onClick={() => toggleText('underline')}
        >
          <span className="underline">U</span>
        </TBtn>
        <TBtn
          title="删除线"
          active={!!(fmt & FMT_STRIKE)}
          disabled={!hasSel}
          onClick={() => toggleText('strikethrough')}
        >
          <span className="line-through">S</span>
        </TBtn>
        <TBtn
          title="上标"
          active={!!(fmt & FMT_SUP)}
          disabled={!hasSel}
          onClick={() => toggleText('superscript')}
        >
          x<sup>2</sup>
        </TBtn>
        <TBtn
          title="下标"
          active={!!(fmt & FMT_SUB)}
          disabled={!hasSel}
          onClick={() => toggleText('subscript')}
        >
          x<sub>2</sub>
        </TBtn>
        <GroupDivider />

        {/* 字体颜色 / 背景高亮 */}
        <Popover>
          <Tooltip>
            <TooltipTrigger asChild>
              <PopoverTrigger asChild>
                <button
                  type="button"
                  title="字体颜色"
                  onMouseDown={(e) => e.preventDefault()}
                  className={`${BTN_BASE} flex-col text-[#333333] hover:bg-[#F0F2F5]`}
                >
                  <span className="text-[12px] font-semibold leading-[10px]">
                    A
                  </span>
                  <span className="mt-[1px] h-[3px] w-3.5 bg-[#FF4D4F]" />
                </button>
              </PopoverTrigger>
            </TooltipTrigger>
            <TooltipContent side="bottom" className="text-xs">
              字体颜色
            </TooltipContent>
          </Tooltip>
          <PopoverContent
            align="start"
            className="w-auto p-2"
            onOpenAutoFocus={keepFocus}
          >
            <div className="grid grid-cols-5 gap-1.5">
              {TEXT_COLORS.map((c) => (
                <button
                  key={c}
                  type="button"
                  onMouseDown={(e) => e.preventDefault()}
                  onClick={() => applyColor(c)}
                  className="h-5 w-5 rounded border border-black/10 transition-transform hover:scale-110"
                  style={{ backgroundColor: c }}
                />
              ))}
            </div>
            <button
              type="button"
              onMouseDown={(e) => e.preventDefault()}
              onClick={() => applyColor(null)}
              className="mt-2 w-full rounded px-1 py-0.5 text-center text-[12px] text-[#666666] hover:bg-[#F0F2F5]"
            >
              清除颜色
            </button>
          </PopoverContent>
        </Popover>
        <Popover>
          <Tooltip>
            <TooltipTrigger asChild>
              <PopoverTrigger asChild>
                <button
                  type="button"
                  title="背景高亮"
                  onMouseDown={(e) => e.preventDefault()}
                  className={`${BTN_BASE} text-[#333333] hover:bg-[#F0F2F5]`}
                >
                  <Highlighter className="h-4 w-4" />
                </button>
              </PopoverTrigger>
            </TooltipTrigger>
            <TooltipContent side="bottom" className="text-xs">
              背景高亮
            </TooltipContent>
          </Tooltip>
          <PopoverContent
            align="start"
            className="w-auto p-2"
            onOpenAutoFocus={keepFocus}
          >
            <div className="grid grid-cols-5 gap-1.5">
              {BG_COLORS.map((c) => (
                <button
                  key={c}
                  type="button"
                  onMouseDown={(e) => e.preventDefault()}
                  onClick={() => applyBg(c)}
                  className="h-5 w-5 rounded border border-black/10 transition-transform hover:scale-110"
                  style={{ backgroundColor: c }}
                />
              ))}
            </div>
            <button
              type="button"
              onMouseDown={(e) => e.preventDefault()}
              onClick={() => applyBg(null)}
              className="mt-2 w-full rounded px-1 py-0.5 text-center text-[12px] text-[#666666] hover:bg-[#F0F2F5]"
            >
              清除高亮
            </button>
          </PopoverContent>
        </Popover>
        <GroupDivider />

        {/* 对齐 */}
        <TBtn
          title="左对齐"
          active={align === 'left'}
          onClick={() => alignTo('left')}
        >
          <AlignLeft className="h-4 w-4" />
        </TBtn>
        <TBtn
          title="居中"
          active={align === 'center'}
          onClick={() => alignTo('center')}
        >
          <AlignCenter className="h-4 w-4" />
        </TBtn>
        <TBtn
          title="右对齐"
          active={align === 'right'}
          onClick={() => alignTo('right')}
        >
          <AlignRight className="h-4 w-4" />
        </TBtn>
        <TBtn
          title="两端对齐"
          active={align === 'justify'}
          onClick={() => alignTo('justify')}
        >
          <AlignJustify className="h-4 w-4" />
        </TBtn>
        <GroupDivider />

        {/* 列表 / 缩进 / 清除格式 */}
        <TBtn
          title="无序列表"
          onClick={() =>
            editor.dispatchCommand(INSERT_UNORDERED_LIST_COMMAND, undefined)
          }
        >
          <List className="h-4 w-4" />
        </TBtn>
        <TBtn
          title="有序列表"
          onClick={() =>
            editor.dispatchCommand(INSERT_ORDERED_LIST_COMMAND, undefined)
          }
        >
          <ListOrdered className="h-4 w-4" />
        </TBtn>
        <TBtn
          title="增加缩进"
          onClick={() =>
            editor.dispatchCommand(INDENT_CONTENT_COMMAND, undefined)
          }
        >
          <IndentIncrease className="h-4 w-4" />
        </TBtn>
        <TBtn
          title="减少缩进"
          onClick={() =>
            editor.dispatchCommand(OUTDENT_CONTENT_COMMAND, undefined)
          }
        >
          <IndentDecrease className="h-4 w-4" />
        </TBtn>
        <TBtn
          title="清除格式"
          disabled={!hasSel}
          onClick={() => {
            editor.update(() => {
              const sel = $ensureSelection();
              if (sel) $clearSelectionFormat(sel);
            });
            editor.dispatchCommand(FORMAT_ELEMENT_COMMAND, 'left');
          }}
        >
          <RemoveFormatting className="h-4 w-4" />
        </TBtn>

        {/* 右侧保存区 */}
        <div className="ml-auto flex items-center gap-1.5 pl-2">
          {dirty > 0 && (
            <span className="rounded-full bg-[#F0F5FF] px-2 py-0.5 text-[12px] text-[#1a66fb]">
              已修改 {dirty} 处
            </span>
          )}
          <button
            type="button"
            disabled={saving || dirty === 0}
            onClick={onDiscard}
            className="h-7 rounded-md border border-[#D9D9D9] px-2.5 text-[12px] text-[#333333] transition-colors hover:bg-[#F0F2F5] disabled:cursor-not-allowed disabled:opacity-40"
          >
            放弃修改
          </button>
          <button
            type="button"
            disabled={saving || dirty === 0}
            onClick={onSave}
            className="h-7 rounded-md bg-[#1a66fb] px-2.5 text-[12px] text-white transition-colors hover:bg-[#1554d6] disabled:cursor-not-allowed disabled:opacity-40"
          >
            {saving ? '保存中…' : '保存'}
          </button>
        </div>
      </div>
    </TooltipProvider>,
    portal,
  );
}
