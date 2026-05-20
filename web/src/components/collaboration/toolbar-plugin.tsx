import {
  INSERT_ORDERED_LIST_COMMAND,
  INSERT_UNORDERED_LIST_COMMAND,
} from '@lexical/list';
import { useLexicalComposerContext } from '@lexical/react/LexicalComposerContext';
import { $createHeadingNode, $isHeadingNode } from '@lexical/rich-text';
import { $setBlocksType } from '@lexical/selection';
import {
  $createParagraphNode,
  $getSelection,
  $isRangeSelection,
  $isTextNode,
  FORMAT_ELEMENT_COMMAND,
  FORMAT_TEXT_COMMAND,
} from 'lexical';
import { useCallback, useEffect, useState } from 'react';

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
  const [fontFamily, setFontFamily] = useState('SimSun');
  const [fontSize, setFontSize] = useState('12pt');
  const [alignment, setAlignment] = useState('left');
  const [lineSpacing, setLineSpacing] = useState('1.5');
  const [heading, setHeading] = useState('paragraph');

  const updateFormatState = useCallback(() => {
    editor.getEditorState().read(() => {
      const selection = $getSelection();
      if (!$isRangeSelection(selection)) return;

      // Font family
      const ff = getStyleValueFromSelection(selection, 'font-family');
      if (ff) setFontFamily(ff);

      // Font size
      const fs = getStyleValueFromSelection(selection, 'font-size');
      if (fs) setFontSize(fs);

      // Line spacing
      const ls = getStyleValueFromSelection(selection, 'line-height');
      if (ls) setLineSpacing(ls);

      // Bold / Italic / Underline — check first text node
      const anchorNode = selection.anchor.getNode();
      if ($isTextNode(anchorNode)) {
        setIsBold(anchorNode.hasFormat('bold'));
        setIsItalic(anchorNode.hasFormat('italic'));
        setIsUnderline(anchorNode.hasFormat('underline'));
      }

      // Alignment — check parent block
      const anchorBlock = selection.anchor
        .getNode()
        .getTopLevelElementOrThrow();
      const align = anchorBlock.getFormat();
      if (align) setAlignment(align);

      // Heading detection
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
        selection.formatText('font-family', value);
      }
    });
    setFontFamily(value);
  };

  const applyFontSize = (value: string) => {
    editor.update(() => {
      const selection = $getSelection();
      if ($isRangeSelection(selection)) {
        selection.formatText('font-size', value);
      }
    });
    setFontSize(value);
  };

  const applyLineSpacing = (value: string) => {
    editor.update(() => {
      const selection = $getSelection();
      if ($isRangeSelection(selection)) {
        selection.formatText('line-height', value);
      }
    });
    setLineSpacing(value);
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

  return (
    <div className="flex items-center gap-1 px-3 py-2 border-b border-stone-100 bg-stone-50/80 overflow-x-auto flex-wrap">
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
        className={`h-7 w-7 flex items-center justify-center rounded text-xs font-bold transition-colors ${
          isBold
            ? 'bg-indigo-100 text-indigo-700'
            : 'text-stone-500 hover:bg-stone-100'
        }`}
        title="加粗"
        onMouseDown={(e) => {
          e.preventDefault();
          editor.dispatchCommand(FORMAT_TEXT_COMMAND, 'bold');
        }}
      >
        B
      </button>

      {/* Italic */}
      <button
        className={`h-7 w-7 flex items-center justify-center rounded text-xs italic transition-colors ${
          isItalic
            ? 'bg-indigo-100 text-indigo-700'
            : 'text-stone-500 hover:bg-stone-100'
        }`}
        title="斜体"
        onMouseDown={(e) => {
          e.preventDefault();
          editor.dispatchCommand(FORMAT_TEXT_COMMAND, 'italic');
        }}
      >
        I
      </button>

      {/* Underline */}
      <button
        className={`h-7 w-7 flex items-center justify-center rounded text-xs underline transition-colors ${
          isUnderline
            ? 'bg-indigo-100 text-indigo-700'
            : 'text-stone-500 hover:bg-stone-100'
        }`}
        title="下划线"
        onMouseDown={(e) => {
          e.preventDefault();
          editor.dispatchCommand(FORMAT_TEXT_COMMAND, 'underline');
        }}
      >
        U
      </button>

      <div className="w-px h-5 bg-stone-200 mx-0.5" />

      {/* Align Left */}
      <button
        className={`h-7 w-7 flex items-center justify-center rounded transition-colors ${
          alignment === 'left'
            ? 'bg-indigo-100 text-indigo-700'
            : 'text-stone-500 hover:bg-stone-100'
        }`}
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
        className={`h-7 w-7 flex items-center justify-center rounded transition-colors ${
          alignment === 'center'
            ? 'bg-indigo-100 text-indigo-700'
            : 'text-stone-500 hover:bg-stone-100'
        }`}
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
        className={`h-7 w-7 flex items-center justify-center rounded transition-colors ${
          alignment === 'right'
            ? 'bg-indigo-100 text-indigo-700'
            : 'text-stone-500 hover:bg-stone-100'
        }`}
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
        className={`h-7 w-7 flex items-center justify-center rounded transition-colors ${
          alignment === 'justify'
            ? 'bg-indigo-100 text-indigo-700'
            : 'text-stone-500 hover:bg-stone-100'
        }`}
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
    </div>
  );
}
