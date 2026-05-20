import {
  INSERT_ORDERED_LIST_COMMAND,
  INSERT_UNORDERED_LIST_COMMAND,
} from '@lexical/list';
import { useLexicalComposerContext } from '@lexical/react/LexicalComposerContext';
import { $createHeadingNode, $isHeadingNode } from '@lexical/rich-text';
import { $patchStyleText, $setBlocksType } from '@lexical/selection';
import {
  $createParagraphNode,
  $getSelection,
  $isRangeSelection,
  $isTextNode,
  FORMAT_ELEMENT_COMMAND,
  FORMAT_TEXT_COMMAND,
  REDO_COMMAND,
  UNDO_COMMAND,
} from 'lexical';
import { useCallback, useEffect, useRef, useState } from 'react';

const FONT_FAMILIES = [
  { label: '宋体', value: 'SimSun' },
  { label: '黑体', value: 'SimHei' },
  { label: '楷体', value: 'KaiTi' },
  { label: '仿宋', value: 'FangSong' },
  { label: '微软雅黑', value: 'Microsoft YaHei' },
  { label: 'Arial', value: 'Arial' },
  { label: 'Times New Roman', value: 'Times New Roman' },
];

const FONT_SIZES = [
  { label: '五号 10.5', value: '10.5pt' },
  { label: '小四 12', value: '12pt' },
  { label: '四号 14', value: '14pt' },
  { label: '小三 15', value: '15pt' },
  { label: '三号 16', value: '16pt' },
  { label: '小二 18', value: '18pt' },
  { label: '二号 22', value: '22pt' },
];

const LINE_SPACINGS = [
  { label: '1.0', value: '1' },
  { label: '1.15', value: '1.15' },
  { label: '1.5', value: '1.5' },
  { label: '2.0', value: '2' },
  { label: '2.5', value: '2.5' },
  { label: '3.0', value: '3' },
];

const HEADINGS = [
  { label: '正文', value: 'paragraph' },
  { label: '标题 1', value: 'h1' },
  { label: '标题 2', value: 'h2' },
  { label: '标题 3', value: 'h3' },
];

function getStyleValueFromSelection(
  selection: ReturnType<typeof $getSelection>,
  property: string,
): string {
  if (!$isRangeSelection(selection)) return '';
  const nodes = selection.getNodes();
  for (const node of nodes) {
    if ($isTextNode(node)) {
      const style = node.getStyle();
      const regex = new RegExp(`${property}:\\s*([^;]+)`, 'i');
      const match = style.match(regex);
      if (match) return match[1].trim();
    }
  }
  return '';
}

export default function ToolbarPlugin() {
  const [editor] = useLexicalComposerContext();
  const [isBold, setIsBold] = useState(false);
  const [isItalic, setIsItalic] = useState(false);
  const [isUnderline, setIsUnderline] = useState(false);
  const [isStrikethrough, setIsStrikethrough] = useState(false);
  const [fontFamily, setFontFamily] = useState('SimSun');
  const [fontSize, setFontSize] = useState('12pt');
  const [alignment, setAlignment] = useState('left');
  const [lineSpacing, setLineSpacing] = useState('1.5');
  const [heading, setHeading] = useState('paragraph');
  const [textColor, setTextColor] = useState('#1C1917');
  const [bgColor, setBgColor] = useState('transparent');
  const colorInputRef = useRef<HTMLInputElement>(null);
  const bgColorInputRef = useRef<HTMLInputElement>(null);

  const updateFormatState = useCallback(() => {
    editor.getEditorState().read(() => {
      const selection = $getSelection();
      if (!$isRangeSelection(selection)) return;

      const ff = getStyleValueFromSelection(selection, 'font-family');
      if (ff) setFontFamily(ff);

      const fs = getStyleValueFromSelection(selection, 'font-size');
      if (fs) setFontSize(fs);

      const ls = getStyleValueFromSelection(selection, 'line-height');
      if (ls) setLineSpacing(ls);

      const tc = getStyleValueFromSelection(selection, 'color');
      if (tc) setTextColor(tc);

      const bc = getStyleValueFromSelection(selection, 'background-color');
      if (bc) setBgColor(bc);

      const anchorNode = selection.anchor.getNode();
      if ($isTextNode(anchorNode)) {
        setIsBold(anchorNode.hasFormat('bold'));
        setIsItalic(anchorNode.hasFormat('italic'));
        setIsUnderline(anchorNode.hasFormat('underline'));
        setIsStrikethrough(anchorNode.hasFormat('strikethrough'));
      }

      const anchorBlock = selection.anchor
        .getNode()
        .getTopLevelElementOrThrow();
      const align = anchorBlock.getFormat();
      if (align) setAlignment(align);

      if ($isHeadingNode(anchorBlock)) {
        setHeading(anchorBlock.getTag());
      } else {
        setHeading('paragraph');
      }
    });
  }, [editor]);

  useEffect(() => {
    const unregister = editor.registerUpdateListener(() => {
      updateFormatState();
    });
    return unregister;
  }, [editor, updateFormatState]);

  const applyFontFamily = (value: string) => {
    editor.update(() => {
      const selection = $getSelection();
      if ($isRangeSelection(selection)) {
        $patchStyleText(selection, { 'font-family': value });
      }
    });
    setFontFamily(value);
  };

  const applyFontSize = (value: string) => {
    editor.update(() => {
      const selection = $getSelection();
      if ($isRangeSelection(selection)) {
        $patchStyleText(selection, { 'font-size': value });
      }
    });
    setFontSize(value);
  };

  const applyLineSpacing = (value: string) => {
    editor.update(() => {
      const selection = $getSelection();
      if ($isRangeSelection(selection)) {
        $patchStyleText(selection, { 'line-height': value });
      }
    });
    setLineSpacing(value);
  };

  const applyTextColor = (value: string) => {
    editor.update(() => {
      const selection = $getSelection();
      if ($isRangeSelection(selection)) {
        $patchStyleText(selection, { color: value });
      }
    });
    setTextColor(value);
  };

  const applyBgColor = (value: string) => {
    editor.update(() => {
      const selection = $getSelection();
      if ($isRangeSelection(selection)) {
        $patchStyleText(selection, {
          'background-color': value === 'transparent' ? 'transparent' : value,
        });
      }
    });
    setBgColor(value);
  };

  const applyHeading = (value: string) => {
    if (value === 'paragraph') {
      editor.update(() => {
        const selection = $getSelection();
        if ($isRangeSelection(selection)) {
          $setBlocksType(selection, () => $createParagraphNode());
        }
      });
    } else {
      editor.update(() => {
        const selection = $getSelection();
        if ($isRangeSelection(selection)) {
          $setBlocksType(selection, () =>
            $createHeadingNode(value as 'h1' | 'h2' | 'h3'),
          );
        }
      });
    }
    setHeading(value);
  };

  const clearFormatting = () => {
    editor.update(() => {
      const selection = $getSelection();
      if ($isRangeSelection(selection)) {
        $patchStyleText(selection, {
          'font-family': '',
          'font-size': '',
          'line-height': '',
          color: '',
          'background-color': '',
        });
      }
    });
  };

  return (
    <div className="flex items-center gap-1 px-3 py-2 border-b border-stone-100 bg-stone-50/80 overflow-x-auto flex-wrap select-none">
      {/* Undo / Redo */}
      <button
        className="h-7 w-7 flex items-center justify-center rounded text-stone-500 hover:bg-stone-100 transition-colors"
        title="撤销"
        onClick={() => editor.dispatchCommand(UNDO_COMMAND, undefined)}
      >
        <svg
          className="w-3.5 h-3.5"
          fill="none"
          stroke="currentColor"
          viewBox="0 0 24 24"
        >
          <path
            strokeLinecap="round"
            strokeLinejoin="round"
            strokeWidth={2}
            d="M3 10h10a8 8 0 018 8v2M3 10l6 6m-6-6l6-6"
          />
        </svg>
      </button>
      <button
        className="h-7 w-7 flex items-center justify-center rounded text-stone-500 hover:bg-stone-100 transition-colors"
        title="重做"
        onClick={() => editor.dispatchCommand(REDO_COMMAND, undefined)}
      >
        <svg
          className="w-3.5 h-3.5"
          fill="none"
          stroke="currentColor"
          viewBox="0 0 24 24"
        >
          <path
            strokeLinecap="round"
            strokeLinejoin="round"
            strokeWidth={2}
            d="M21 10H11a8 8 0 00-8 8v2m18-10l-6 6m6-6l-6-6"
          />
        </svg>
      </button>

      <div className="w-px h-5 bg-stone-200 mx-0.5" />

      {/* Heading */}
      <select
        className="h-7 px-1.5 text-xs border border-stone-200 rounded bg-white text-stone-700 focus:outline-none focus:border-indigo-300"
        value={heading}
        onChange={(e) => applyHeading(e.target.value)}
      >
        {HEADINGS.map((h) => (
          <option key={h.value} value={h.value}>
            {h.label}
          </option>
        ))}
      </select>

      <div className="w-px h-5 bg-stone-200 mx-0.5" />

      {/* Font Family */}
      <select
        className="h-7 px-1.5 text-xs border border-stone-200 rounded bg-white text-stone-700 w-[100px] focus:outline-none focus:border-indigo-300"
        value={fontFamily}
        onChange={(e) => applyFontFamily(e.target.value)}
      >
        {FONT_FAMILIES.map((f) => (
          <option key={f.value} value={f.value}>
            {f.label}
          </option>
        ))}
      </select>

      {/* Font Size */}
      <select
        className="h-7 px-1.5 text-xs border border-stone-200 rounded bg-white text-stone-700 w-[90px] focus:outline-none focus:border-indigo-300"
        value={fontSize}
        onChange={(e) => applyFontSize(e.target.value)}
      >
        {FONT_SIZES.map((s) => (
          <option key={s.value} value={s.value}>
            {s.label}
          </option>
        ))}
      </select>

      <div className="w-px h-5 bg-stone-200 mx-0.5" />

      {/* Bold */}
      <button
        className={`h-7 w-7 flex items-center justify-center rounded text-xs font-bold transition-colors ${isBold ? 'bg-indigo-100 text-indigo-700' : 'text-stone-500 hover:bg-stone-100'}`}
        title="加粗 (Ctrl+B)"
        onMouseDown={(e) => {
          e.preventDefault();
          editor.dispatchCommand(FORMAT_TEXT_COMMAND, 'bold');
        }}
      >
        B
      </button>

      {/* Italic */}
      <button
        className={`h-7 w-7 flex items-center justify-center rounded text-xs italic transition-colors ${isItalic ? 'bg-indigo-100 text-indigo-700' : 'text-stone-500 hover:bg-stone-100'}`}
        title="斜体 (Ctrl+I)"
        onMouseDown={(e) => {
          e.preventDefault();
          editor.dispatchCommand(FORMAT_TEXT_COMMAND, 'italic');
        }}
      >
        I
      </button>

      {/* Underline */}
      <button
        className={`h-7 w-7 flex items-center justify-center rounded text-xs underline transition-colors ${isUnderline ? 'bg-indigo-100 text-indigo-700' : 'text-stone-500 hover:bg-stone-100'}`}
        title="下划线 (Ctrl+U)"
        onMouseDown={(e) => {
          e.preventDefault();
          editor.dispatchCommand(FORMAT_TEXT_COMMAND, 'underline');
        }}
      >
        U
      </button>

      {/* Strikethrough */}
      <button
        className={`h-7 w-7 flex items-center justify-center rounded text-xs line-through transition-colors ${isStrikethrough ? 'bg-indigo-100 text-indigo-700' : 'text-stone-500 hover:bg-stone-100'}`}
        title="删除线"
        onMouseDown={(e) => {
          e.preventDefault();
          editor.dispatchCommand(FORMAT_TEXT_COMMAND, 'strikethrough');
        }}
      >
        S
      </button>

      <div className="w-px h-5 bg-stone-200 mx-0.5" />

      {/* Text Color */}
      <div className="relative flex items-center" title="字体颜色">
        <button
          className="h-7 w-7 flex items-center justify-center rounded text-stone-500 hover:bg-stone-100 transition-colors"
          onClick={() => colorInputRef.current?.click()}
        >
          <svg
            className="w-3.5 h-3.5"
            fill="none"
            stroke="currentColor"
            viewBox="0 0 24 24"
          >
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              strokeWidth={2}
              d="M15.232 5.232l3.536 3.536m-2.036-5.036a2.5 2.5 0 113.536 3.536L6.5 21.036H3v-3.572L16.732 3.732z"
            />
          </svg>
          <span
            className="absolute bottom-1 left-1/2 -translate-x-1/2 w-2.5 h-0.5 rounded"
            style={{ backgroundColor: textColor }}
          />
        </button>
        <input
          ref={colorInputRef}
          type="color"
          className="absolute opacity-0 w-0 h-0"
          value={textColor}
          onChange={(e) => applyTextColor(e.target.value)}
        />
      </div>

      {/* Highlight Color */}
      <div className="relative flex items-center" title="高亮底色">
        <button
          className="h-7 w-7 flex items-center justify-center rounded text-stone-500 hover:bg-stone-100 transition-colors"
          onClick={() => bgColorInputRef.current?.click()}
        >
          <svg
            className="w-3.5 h-3.5"
            fill="none"
            stroke="currentColor"
            viewBox="0 0 24 24"
          >
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              strokeWidth={2}
              d="M15.172 7l-6.586 6.586a2 2 0 102.828 2.828l6.414-6.586a4 4 0 00-5.656-5.656l-6.415 6.585a6 6 0 108.486 8.486L20.5 13"
            />
          </svg>
          <span
            className="absolute bottom-1 left-1/2 -translate-x-1/2 w-2.5 h-0.5 rounded"
            style={{
              backgroundColor:
                bgColor === 'transparent' ? 'transparent' : bgColor,
            }}
          />
        </button>
        <input
          ref={bgColorInputRef}
          type="color"
          className="absolute opacity-0 w-0 h-0"
          value={bgColor === 'transparent' ? '#FFFFFF' : bgColor}
          onChange={(e) => applyBgColor(e.target.value)}
        />
      </div>

      <div className="w-px h-5 bg-stone-200 mx-0.5" />

      {/* Align Left */}
      <button
        className={`h-7 w-7 flex items-center justify-center rounded transition-colors ${alignment === 'left' ? 'bg-indigo-100 text-indigo-700' : 'text-stone-500 hover:bg-stone-100'}`}
        title="左对齐"
        onMouseDown={(e) => {
          e.preventDefault();
          editor.dispatchCommand(FORMAT_ELEMENT_COMMAND, 'left');
        }}
      >
        <svg
          className="w-3.5 h-3.5"
          fill="none"
          stroke="currentColor"
          viewBox="0 0 24 24"
        >
          <path
            strokeLinecap="round"
            strokeLinejoin="round"
            strokeWidth={2}
            d="M4 6h16M4 10h12M4 14h16M4 18h12"
          />
        </svg>
      </button>

      {/* Align Center */}
      <button
        className={`h-7 w-7 flex items-center justify-center rounded transition-colors ${alignment === 'center' ? 'bg-indigo-100 text-indigo-700' : 'text-stone-500 hover:bg-stone-100'}`}
        title="居中"
        onMouseDown={(e) => {
          e.preventDefault();
          editor.dispatchCommand(FORMAT_ELEMENT_COMMAND, 'center');
        }}
      >
        <svg
          className="w-3.5 h-3.5"
          fill="none"
          stroke="currentColor"
          viewBox="0 0 24 24"
        >
          <path
            strokeLinecap="round"
            strokeLinejoin="round"
            strokeWidth={2}
            d="M4 6h16M8 10h8M4 14h16M8 18h8"
          />
        </svg>
      </button>

      {/* Align Right */}
      <button
        className={`h-7 w-7 flex items-center justify-center rounded transition-colors ${alignment === 'right' ? 'bg-indigo-100 text-indigo-700' : 'text-stone-500 hover:bg-stone-100'}`}
        title="右对齐"
        onMouseDown={(e) => {
          e.preventDefault();
          editor.dispatchCommand(FORMAT_ELEMENT_COMMAND, 'right');
        }}
      >
        <svg
          className="w-3.5 h-3.5"
          fill="none"
          stroke="currentColor"
          viewBox="0 0 24 24"
        >
          <path
            strokeLinecap="round"
            strokeLinejoin="round"
            strokeWidth={2}
            d="M4 6h16M8 10h12M4 14h16M8 18h12"
          />
        </svg>
      </button>

      {/* Align Justify */}
      <button
        className={`h-7 w-7 flex items-center justify-center rounded transition-colors ${alignment === 'justify' ? 'bg-indigo-100 text-indigo-700' : 'text-stone-500 hover:bg-stone-100'}`}
        title="两端对齐"
        onMouseDown={(e) => {
          e.preventDefault();
          editor.dispatchCommand(FORMAT_ELEMENT_COMMAND, 'justify');
        }}
      >
        <svg
          className="w-3.5 h-3.5"
          fill="none"
          stroke="currentColor"
          viewBox="0 0 24 24"
        >
          <path
            strokeLinecap="round"
            strokeLinejoin="round"
            strokeWidth={2}
            d="M4 6h16M4 10h16M4 14h16M4 18h16"
          />
        </svg>
      </button>

      <div className="w-px h-5 bg-stone-200 mx-0.5" />

      {/* Indent / Outdent */}
      <button
        className="h-7 w-7 flex items-center justify-center rounded text-stone-500 hover:bg-stone-100 transition-colors"
        title="减少缩进"
        onMouseDown={(e) => {
          e.preventDefault();
          editor.dispatchCommand(FORMAT_ELEMENT_COMMAND, 'outdent');
        }}
      >
        <svg
          className="w-3.5 h-3.5"
          fill="none"
          stroke="currentColor"
          viewBox="0 0 24 24"
        >
          <path
            strokeLinecap="round"
            strokeLinejoin="round"
            strokeWidth={2}
            d="M7 6H21M7 12H21M3 12L7 9m-4 3l4 3m-4 3h18"
          />
        </svg>
      </button>
      <button
        className="h-7 w-7 flex items-center justify-center rounded text-stone-500 hover:bg-stone-100 transition-colors"
        title="增加缩进"
        onMouseDown={(e) => {
          e.preventDefault();
          editor.dispatchCommand(FORMAT_ELEMENT_COMMAND, 'indent');
        }}
      >
        <svg
          className="w-3.5 h-3.5"
          fill="none"
          stroke="currentColor"
          viewBox="0 0 24 24"
        >
          <path
            strokeLinecap="round"
            strokeLinejoin="round"
            strokeWidth={2}
            d="M17 6h4m-4 6h4m-4 6h4M3 12l4-3m-4 3l4 3m4 3h11"
          />
        </svg>
      </button>

      <div className="w-px h-5 bg-stone-200 mx-0.5" />

      {/* Line Spacing */}
      <select
        className="h-7 px-1 text-xs border border-stone-200 rounded bg-white text-stone-700 w-[70px] focus:outline-none focus:border-indigo-300"
        value={lineSpacing}
        onChange={(e) => applyLineSpacing(e.target.value)}
        title="行距"
      >
        {LINE_SPACINGS.map((l) => (
          <option key={l.value} value={l.value}>
            {l.label}
          </option>
        ))}
      </select>

      <div className="w-px h-5 bg-stone-200 mx-0.5" />

      {/* Bullet List */}
      <button
        className="h-7 w-7 flex items-center justify-center rounded text-stone-500 hover:bg-stone-100 transition-colors"
        title="无序列表"
        onMouseDown={(e) => {
          e.preventDefault();
          editor.dispatchCommand(INSERT_UNORDERED_LIST_COMMAND, undefined);
        }}
      >
        <svg
          className="w-3.5 h-3.5"
          fill="none"
          stroke="currentColor"
          viewBox="0 0 24 24"
        >
          <path
            strokeLinecap="round"
            strokeLinejoin="round"
            strokeWidth={2}
            d="M4 6h16M4 6h.01M4 12h16M4 12h.01M4 18h16M4 18h.01"
          />
        </svg>
      </button>

      {/* Numbered List */}
      <button
        className="h-7 w-7 flex items-center justify-center rounded text-stone-500 hover:bg-stone-100 transition-colors"
        title="有序列表"
        onMouseDown={(e) => {
          e.preventDefault();
          editor.dispatchCommand(INSERT_ORDERED_LIST_COMMAND, undefined);
        }}
      >
        <svg
          className="w-3.5 h-3.5"
          fill="none"
          stroke="currentColor"
          viewBox="0 0 24 24"
        >
          <path
            strokeLinecap="round"
            strokeLinejoin="round"
            strokeWidth={2}
            d="M7 6h14M7 12h14M7 18h14M4 6h.01M4 12h.01M4 18h.01"
          />
        </svg>
      </button>

      <div className="w-px h-5 bg-stone-200 mx-0.5" />

      {/* Clear Formatting */}
      <button
        className="h-7 w-7 flex items-center justify-center rounded text-stone-400 hover:text-red-500 hover:bg-red-50 transition-colors"
        title="清除格式"
        onClick={clearFormatting}
      >
        <svg
          className="w-3.5 h-3.5"
          fill="none"
          stroke="currentColor"
          viewBox="0 0 24 24"
        >
          <path
            strokeLinecap="round"
            strokeLinejoin="round"
            strokeWidth={2}
            d="M6 18L18 6M6 6l12 12"
          />
        </svg>
      </button>
    </div>
  );
}
