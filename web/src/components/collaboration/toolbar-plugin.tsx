import { CendTooltip } from '@/components/ui/tooltip';
import { $createCodeNode } from '@lexical/code';
import { TOGGLE_LINK_COMMAND } from '@lexical/link';
import {
  INSERT_CHECK_LIST_COMMAND,
  INSERT_ORDERED_LIST_COMMAND,
  INSERT_UNORDERED_LIST_COMMAND,
} from '@lexical/list';
import { useLexicalComposerContext } from '@lexical/react/LexicalComposerContext';
import {
  $createHeadingNode,
  $createQuoteNode,
  $isHeadingNode,
  $isQuoteNode,
} from '@lexical/rich-text';
import { $patchStyleText, $setBlocksType } from '@lexical/selection';
import {
  $createParagraphNode,
  $getRoot,
  $getSelection,
  $isRangeSelection,
  $isTextNode,
  ElementFormatType,
  FORMAT_ELEMENT_COMMAND,
  FORMAT_TEXT_COMMAND,
  REDO_COMMAND,
  UNDO_COMMAND,
} from 'lexical';
import { useCallback, useEffect, useRef, useState } from 'react';
import EmojiPicker from './emoji-picker';
import {
  $createCalloutNode,
  $isCalloutNode,
  CalloutType,
} from './nodes/callout-node';
import { $createImageNode } from './nodes/image-node';
import { $createMathNode } from './nodes/math-node';

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

function SelectArrow() {
  return (
    <svg
      className="pointer-events-none absolute right-1 top-1/2 -translate-y-1/2 w-3 h-3 text-[#1a1a1a]"
      fill="none"
      stroke="currentColor"
      viewBox="0 0 24 24"
    >
      <path
        strokeLinecap="round"
        strokeLinejoin="round"
        strokeWidth={2}
        d="M19 9l-7 7-7-7"
      />
    </svg>
  );
}

export default function ToolbarPlugin() {
  const [editor] = useLexicalComposerContext();
  const [isBold, setIsBold] = useState(false);
  const [isItalic, setIsItalic] = useState(false);
  const [isUnderline, setIsUnderline] = useState(false);
  const [isStrikethrough, setIsStrikethrough] = useState(false);
  const [isSubscript, setIsSubscript] = useState(false);
  const [isSuperscript, setIsSuperscript] = useState(false);
  const [isCode, setIsCode] = useState(false);
  const [isQuote, setIsQuote] = useState(false);
  const [isCallout, setIsCallout] = useState(false);
  const [linkUrl, setLinkUrl] = useState('');
  const [showLinkInput, setShowLinkInput] = useState(false);
  const [showMathInput, setShowMathInput] = useState(false);
  const [mathFormula, setMathFormula] = useState('');
  const [showImageInput, setShowImageInput] = useState(false);
  const [imageUrl, setImageUrl] = useState('');
  const [showEmoji, setShowEmoji] = useState(false);
  const linkInputRef = useRef<HTMLInputElement>(null);
  const mathInputRef = useRef<HTMLInputElement>(null);
  const imageInputRef = useRef<HTMLInputElement>(null);
  const [fontFamily, setFontFamily] = useState('SimSun');
  const [fontSize, setFontSize] = useState('12pt');
  const [alignment, setAlignment] = useState('left');
  const [lineSpacing, setLineSpacing] = useState('1.5');
  const [heading, setHeading] = useState('paragraph');
  const [textColor, setTextColor] = useState('#1C1917');
  const [bgColor, setBgColor] = useState('transparent');
  const [wordCount, setWordCount] = useState(0);
  const colorInputRef = useRef<HTMLInputElement>(null);
  const bgColorInputRef = useRef<HTMLInputElement>(null);

  const updateFormatState = useCallback(() => {
    editor.getEditorState().read(() => {
      // Word count
      const text = $getRoot().getTextContent();
      const chars = text.replace(/\s/g, '').length;
      setWordCount(chars);

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
        setIsSubscript(anchorNode.hasFormat('subscript'));
        setIsSuperscript(anchorNode.hasFormat('superscript'));
        setIsCode(anchorNode.hasFormat('code'));
      }

      const anchorBlock = selection.anchor
        .getNode()
        .getTopLevelElementOrThrow();
      const alignFmt = anchorBlock.getFormat();
      const alignMap: Record<number, string> = {
        0: 'left',
        1: 'center',
        2: 'right',
        3: 'justify',
      };
      if (alignFmt !== undefined) setAlignment(alignMap[alignFmt] || 'left');

      if ($isHeadingNode(anchorBlock)) {
        setHeading(anchorBlock.getTag());
      } else {
        setHeading('paragraph');
      }
      setIsQuote($isQuoteNode(anchorBlock));
      setIsCallout($isCalloutNode(anchorBlock));
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

  const toggleQuote = () => {
    editor.update(() => {
      const selection = $getSelection();
      if (!$isRangeSelection(selection)) return;
      const anchorBlock = selection.anchor
        .getNode()
        .getTopLevelElementOrThrow();
      if ($isQuoteNode(anchorBlock)) {
        $setBlocksType(selection, () => $createParagraphNode());
      } else {
        $setBlocksType(selection, () => $createQuoteNode());
      }
    });
  };

  const insertCallout = (type: CalloutType = 'info') => {
    editor.update(() => {
      const selection = $getSelection();
      if ($isRangeSelection(selection)) {
        const callout = $createCalloutNode(type);
        const anchorBlock = selection.anchor
          .getNode()
          .getTopLevelElementOrThrow();
        if ($isCalloutNode(anchorBlock)) {
          // If already in a callout, convert back to paragraph
          $setBlocksType(selection, () => $createParagraphNode());
          return;
        }
        // Wrap current block in callout
        anchorBlock.insertBefore(callout);
        callout.append(anchorBlock);
        // Insert an empty paragraph after callout for continuation
        const after = $createParagraphNode();
        callout.insertAfter(after);
      }
    });
  };

  const insertLink = (url: string) => {
    if (url) {
      editor.dispatchCommand(TOGGLE_LINK_COMMAND, url);
    } else {
      editor.dispatchCommand(TOGGLE_LINK_COMMAND, null);
    }
    setShowLinkInput(false);
    setLinkUrl('');
  };

  const insertCodeBlock = () => {
    editor.update(() => {
      const selection = $getSelection();
      if ($isRangeSelection(selection)) {
        $setBlocksType(selection, () => $createCodeNode());
      }
    });
  };

  const insertImage = (url: string) => {
    const trimmed = url.trim();
    if (!trimmed) return;
    editor.update(() => {
      const selection = $getSelection();
      if ($isRangeSelection(selection)) {
        const imageNode = $createImageNode(trimmed);
        selection.insertNodes([imageNode]);
      }
    });
    setShowImageInput(false);
    setImageUrl('');
  };

  const insertMath = (formula: string) => {
    const trimmed = formula.trim();
    if (!trimmed) return;
    editor.update(() => {
      const selection = $getSelection();
      if ($isRangeSelection(selection)) {
        const mathNode = $createMathNode(trimmed);
        selection.insertNodes([mathNode]);
      }
    });
    setShowMathInput(false);
    setMathFormula('');
  };

  const insertEmoji = (emoji: string) => {
    editor.update(() => {
      const selection = $getSelection();
      if ($isRangeSelection(selection)) {
        selection.insertRawText(emoji);
      }
    });
    setShowEmoji(false);
  };

  const applyBodyFormat = () => {
    editor.update(() => {
      const selection = $getSelection();
      if (!$isRangeSelection(selection)) return;

      // Convert selected blocks to paragraph (remove heading)
      $setBlocksType(selection, () => $createParagraphNode());

      // Apply body format styles
      $patchStyleText(selection, {
        'font-family': 'SimSun',
        'font-size': '12pt',
        'line-height': '1.5',
        color: '#1C1917',
        'background-color': 'transparent',
      });

      // Remove bold/italic/underline/strikethrough/code
      const nodes = selection.getNodes();
      for (const node of nodes) {
        if ($isTextNode(node)) {
          node.setFormat(0);
        }
      }
    });
    setHeading('paragraph');
    setFontFamily('SimSun');
    setFontSize('12pt');
    setLineSpacing('1.5');
    setTextColor('#1C1917');
    setBgColor('transparent');
    setAlignment('left');
    setIsBold(false);
    setIsItalic(false);
    setIsUnderline(false);
    setIsStrikethrough(false);
    setIsCode(false);
  };

  const btn = (active: boolean) =>
    `h-7 w-7 flex items-center justify-center rounded text-xs transition-colors ${
      active
        ? 'bg-black/[0.04] text-indigo-700'
        : 'text-black/50 hover:bg-black/[0.04]'
    }`;

  return (
    <div className="flex items-center gap-1 px-3 py-2 border-b border-black/[0.06] bg-black/[0.02]/80 overflow-x-auto flex-wrap select-none">
      {/* Undo / Redo */}
      <CendTooltip title="撤销 (Ctrl+Z)">
        <button
          className="h-7 w-7 flex items-center justify-center rounded text-black/50 hover:bg-black/[0.04] transition-colors"
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
      </CendTooltip>
      <CendTooltip title="重做 (Ctrl+Y)">
        <button
          className="h-7 w-7 flex items-center justify-center rounded text-black/50 hover:bg-black/[0.04] transition-colors"
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
      </CendTooltip>

      <div className="w-px h-5 bg-black/[0.06] mx-0.5" />

      {/* Heading */}
      <div className="relative">
        <select
          className="h-7 px-1.5 text-xs border border-[#D4D4D4] rounded bg-[#EAEAEA] text-[#000000] focus:outline-none focus:border-[#000000] appearance-none cursor-pointer"
          value={heading}
          onChange={(e) => applyHeading(e.target.value)}
        >
          {HEADINGS.map((h) => (
            <option key={h.value} value={h.value}>
              {h.label}
            </option>
          ))}
        </select>
        <SelectArrow />
      </div>

      <div className="w-px h-5 bg-black/[0.06] mx-0.5" />

      {/* Font Family */}
      <div className="relative">
        <select
          className="h-7 px-1.5 text-xs border border-[#D4D4D4] rounded bg-[#EAEAEA] text-[#000000] w-[100px] focus:outline-none focus:border-[#000000] appearance-none cursor-pointer"
          value={fontFamily}
          onChange={(e) => applyFontFamily(e.target.value)}
        >
          {FONT_FAMILIES.map((f) => (
            <option key={f.value} value={f.value}>
              {f.label}
            </option>
          ))}
        </select>
        <SelectArrow />
      </div>

      {/* Font Size */}
      <div className="relative">
        <select
          className="h-7 px-1.5 text-xs border border-[#D4D4D4] rounded bg-[#EAEAEA] text-[#000000] w-[90px] focus:outline-none focus:border-[#000000] appearance-none cursor-pointer"
          value={fontSize}
          onChange={(e) => applyFontSize(e.target.value)}
        >
          {FONT_SIZES.map((s) => (
            <option key={s.value} value={s.value}>
              {s.label}
            </option>
          ))}
        </select>
        <SelectArrow />
      </div>

      <div className="w-px h-5 bg-black/[0.06] mx-0.5" />

      {/* Bold */}
      <CendTooltip title="加粗 (Ctrl+B)">
        <button
          className={btn(isBold, '加粗 (Ctrl+B)')}
          onMouseDown={(e) => {
            e.preventDefault();
            editor.dispatchCommand(FORMAT_TEXT_COMMAND, 'bold');
          }}
        >
          <span className="font-normal">B</span>
        </button>
      </CendTooltip>

      {/* Italic */}
      <CendTooltip title="斜体 (Ctrl+I)">
        <button
          className={btn(isItalic, '斜体 (Ctrl+I)')}
          onMouseDown={(e) => {
            e.preventDefault();
            editor.dispatchCommand(FORMAT_TEXT_COMMAND, 'italic');
          }}
        >
          <span className="italic">I</span>
        </button>
      </CendTooltip>

      {/* Underline */}
      <CendTooltip title="下划线 (Ctrl+U)">
        <button
          className={btn(isUnderline, '下划线 (Ctrl+U)')}
          onMouseDown={(e) => {
            e.preventDefault();
            editor.dispatchCommand(FORMAT_TEXT_COMMAND, 'underline');
          }}
        >
          <span className="underline">U</span>
        </button>
      </CendTooltip>

      {/* Strikethrough */}
      <CendTooltip title="删除线">
        <button
          className={btn(isStrikethrough, '删除线')}
          onMouseDown={(e) => {
            e.preventDefault();
            editor.dispatchCommand(FORMAT_TEXT_COMMAND, 'strikethrough');
          }}
        >
          <span className="line-through">S</span>
        </button>
      </CendTooltip>

      {/* Subscript */}
      <CendTooltip title="下标">
        <button
          className={btn(isSubscript, '下标')}
          onMouseDown={(e) => {
            e.preventDefault();
            editor.dispatchCommand(FORMAT_TEXT_COMMAND, 'subscript');
          }}
        >
          X<span className="text-[8px]">2</span>
        </button>
      </CendTooltip>

      {/* Superscript */}
      <CendTooltip title="上标">
        <button
          className={btn(isSuperscript, '上标')}
          onMouseDown={(e) => {
            e.preventDefault();
            editor.dispatchCommand(FORMAT_TEXT_COMMAND, 'superscript');
          }}
        >
          X<span className="text-[8px]">2</span>
        </button>
      </CendTooltip>

      <div className="w-px h-5 bg-black/[0.06] mx-0.5" />

      {/* Text Color */}
      <CendTooltip title="字体颜色">
        <div className="relative flex items-center">
          <button
            className="h-7 w-7 flex items-center justify-center rounded text-black/50 hover:bg-black/[0.04] transition-colors"
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
      </CendTooltip>

      {/* Highlight Color */}
      <CendTooltip title="高亮底色">
        <div className="relative flex items-center">
          <button
            className="h-7 w-7 flex items-center justify-center rounded text-black/50 hover:bg-black/[0.04] transition-colors"
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
      </CendTooltip>

      <div className="w-px h-5 bg-black/[0.06] mx-0.5" />

      {/* Inline Code */}
      <CendTooltip title="行内代码">
        <button
          className={btn(isCode, '行内代码')}
          onMouseDown={(e) => {
            e.preventDefault();
            editor.dispatchCommand(FORMAT_TEXT_COMMAND, 'code');
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
              d="M10 20l4-16m4 4l4 4-4 4M6 16l-4-4 4-4"
            />
          </svg>
        </button>
      </CendTooltip>

      <div className="w-px h-5 bg-black/[0.06] mx-0.5" />

      {/* Align Left */}
      <CendTooltip title="左对齐">
        <button
          className={btn(alignment === 'left', '左对齐')}
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
      </CendTooltip>

      {/* Align Center */}
      <CendTooltip title="居中">
        <button
          className={btn(alignment === 'center', '居中')}
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
      </CendTooltip>

      {/* Align Right */}
      <CendTooltip title="右对齐">
        <button
          className={btn(alignment === 'right', '右对齐')}
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
      </CendTooltip>

      {/* Align Justify */}
      <CendTooltip title="两端对齐">
        <button
          className={btn(alignment === 'justify', '两端对齐')}
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
      </CendTooltip>

      <div className="w-px h-5 bg-black/[0.06] mx-0.5" />

      {/* Indent / Outdent */}
      <CendTooltip title="减少缩进">
        <button
          className="h-7 w-7 flex items-center justify-center rounded text-black/50 hover:bg-black/[0.04] transition-colors"
          onMouseDown={(e) => {
            e.preventDefault();
            editor.dispatchCommand(
              FORMAT_ELEMENT_COMMAND,
              'outdent' as ElementFormatType,
            );
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
      </CendTooltip>
      <CendTooltip title="增加缩进">
        <button
          className="h-7 w-7 flex items-center justify-center rounded text-black/50 hover:bg-black/[0.04] transition-colors"
          onMouseDown={(e) => {
            e.preventDefault();
            editor.dispatchCommand(
              FORMAT_ELEMENT_COMMAND,
              'indent' as ElementFormatType,
            );
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
      </CendTooltip>

      <div className="w-px h-5 bg-black/[0.06] mx-0.5" />

      {/* Line Spacing */}
      <CendTooltip title="行距">
        <div className="relative">
          <select
            className="h-7 px-1 text-xs border border-[#D4D4D4] rounded bg-[#EAEAEA] text-[#000000] w-[70px] focus:outline-none focus:border-[#000000] appearance-none cursor-pointer"
            value={lineSpacing}
            onChange={(e) => applyLineSpacing(e.target.value)}
          >
            {LINE_SPACINGS.map((l) => (
              <option key={l.value} value={l.value}>
                {l.label}
              </option>
            ))}
          </select>
          <SelectArrow />
        </div>
      </CendTooltip>

      <div className="w-px h-5 bg-black/[0.06] mx-0.5" />

      {/* Bullet List */}
      <CendTooltip title="无序列表">
        <button
          className="h-7 w-7 flex items-center justify-center rounded text-black/50 hover:bg-black/[0.04] transition-colors"
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
      </CendTooltip>

      {/* Numbered List */}
      <CendTooltip title="有序列表">
        <button
          className="h-7 w-7 flex items-center justify-center rounded text-black/50 hover:bg-black/[0.04] transition-colors"
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
      </CendTooltip>

      <div className="w-px h-5 bg-black/[0.06] mx-0.5" />

      {/* Quote */}
      <CendTooltip title="引用">
        <button
          className={btn(isQuote)}
          onMouseDown={(e) => {
            e.preventDefault();
            toggleQuote();
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
              d="M10 11V8.99c0-.88.59-1.64 1.44-1.86h.05A1.99 1.99 0 0114 9.05V12m-4 4v-3m-4 4v-3m-4 4v-3m16-4v3m-4-4v3"
            />
          </svg>
        </button>
      </CendTooltip>

      {/* Todo List */}
      <CendTooltip title="待办列表">
        <button
          className="h-7 w-7 flex items-center justify-center rounded text-black/50 hover:bg-black/[0.04] transition-colors"
          onMouseDown={(e) => {
            e.preventDefault();
            editor.dispatchCommand(INSERT_CHECK_LIST_COMMAND, undefined);
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
              d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2m-6 9l2 2 4-4"
            />
          </svg>
        </button>
      </CendTooltip>

      {/* Callout Dropdown */}
      <div className="relative">
        <select
          className="h-7 px-1 text-xs border border-[#D4D4D4] rounded bg-[#EAEAEA] text-[#000000] w-[72px] focus:outline-none focus:border-[#000000] appearance-none cursor-pointer"
          value={isCallout ? 'in' : ''}
          onChange={(e) => {
            if (e.target.value) insertCallout(e.target.value as CalloutType);
          }}
        >
          <option value="">标注 ▾</option>
          <option value="info">💡 信息</option>
          <option value="warning">⚠️ 警告</option>
          <option value="tip">✅ 提示</option>
          <option value="danger">🚫 重要</option>
        </select>
        <SelectArrow />
      </div>

      <div className="w-px h-5 bg-black/[0.06] mx-0.5" />

      {/* Link */}
      <div className="relative">
        <CendTooltip title="超链接">
          <button
            className="h-7 w-7 flex items-center justify-center rounded text-black/50 hover:bg-black/[0.04] transition-colors"
            onMouseDown={(e) => {
              e.preventDefault();
              setShowLinkInput((prev) => !prev);
              setTimeout(() => linkInputRef.current?.focus(), 50);
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
                d="M13.828 10.172a4 4 0 00-5.656 0l-4 4a4 4 0 105.656 5.656l1.102-1.101m-.758-4.899a4 4 0 005.656 0l4-4a4 4 0 00-5.656-5.656l-1.1 1.1"
              />
            </svg>
          </button>
        </CendTooltip>
        {showLinkInput && (
          <div className="absolute top-full left-0 mt-1 flex items-center gap-1 bg-white border border-stone-200 rounded-lg shadow-lg p-1.5 z-50">
            <input
              ref={linkInputRef}
              className="h-7 px-2 text-xs border border-stone-200 rounded outline-none focus:border-indigo-400 w-44"
              placeholder="输入链接URL..."
              value={linkUrl}
              onChange={(e) => setLinkUrl(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') insertLink(linkUrl);
                if (e.key === 'Escape') setShowLinkInput(false);
              }}
            />
            <button
              className="h-7 px-2 text-xs bg-indigo-500 text-white rounded hover:bg-indigo-600"
              onClick={() => insertLink(linkUrl)}
            >
              确定
            </button>
          </div>
        )}
      </div>

      {/* Code Block */}
      <CendTooltip title="代码块">
        <button
          className="h-7 w-7 flex items-center justify-center rounded text-black/50 hover:bg-black/[0.04] transition-colors"
          onMouseDown={(e) => {
            e.preventDefault();
            insertCodeBlock();
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
              d="M8 9l3 3-3 3m5 0h3M5 20h14a2 2 0 002-2V6a2 2 0 00-2-2H5a2 2 0 00-2 2v12a2 2 0 002 2z"
            />
          </svg>
        </button>
      </CendTooltip>

      {/* Image */}
      <div className="relative">
        <CendTooltip title="图片">
          <button
            className="h-7 w-7 flex items-center justify-center rounded text-black/50 hover:bg-black/[0.04] transition-colors"
            onMouseDown={(e) => {
              e.preventDefault();
              setShowImageInput((prev) => !prev);
              setTimeout(() => imageInputRef.current?.focus(), 50);
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
                d="M4 16l4.586-4.586a2 2 0 012.828 0L16 16m-2-2l1.586-1.586a2 2 0 012.828 0L20 14m-6-6h.01M6 20h12a2 2 0 002-2V6a2 2 0 00-2-2H6a2 2 0 00-2 2v12a2 2 0 002 2z"
              />
            </svg>
          </button>
        </CendTooltip>
        {showImageInput && (
          <div className="absolute top-full left-0 mt-1 flex items-center gap-1 bg-white border border-stone-200 rounded-lg shadow-lg p-1.5 z-50">
            <input
              ref={imageInputRef}
              className="h-7 px-2 text-xs border border-stone-200 rounded outline-none focus:border-indigo-400 w-52"
              placeholder="输入图片URL..."
              value={imageUrl}
              onChange={(e) => setImageUrl(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') insertImage(imageUrl);
                if (e.key === 'Escape') setShowImageInput(false);
              }}
            />
            <button
              className="h-7 px-2 text-xs bg-indigo-500 text-white rounded hover:bg-indigo-600"
              onClick={() => insertImage(imageUrl)}
            >
              确定
            </button>
          </div>
        )}
      </div>

      {/* Math Formula */}
      <div className="relative">
        <CendTooltip title="数学公式 (LaTeX)">
          <button
            className="h-7 w-7 flex items-center justify-center rounded text-black/50 hover:bg-black/[0.04] transition-colors"
            onMouseDown={(e) => {
              e.preventDefault();
              setShowMathInput((prev) => !prev);
              setTimeout(() => mathInputRef.current?.focus(), 50);
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
                d="M7 4h10M7 8h10M7 12h10M7 16h10M7 20h10"
              />
              <text
                x="12"
                y="16"
                textAnchor="middle"
                className="text-[6px] fill-current stroke-none"
                fontStyle="italic"
              >
                fx
              </text>
            </svg>
          </button>
        </CendTooltip>
        {showMathInput && (
          <div className="absolute top-full left-0 mt-1 flex items-center gap-1 bg-white border border-stone-200 rounded-lg shadow-lg p-1.5 z-50">
            <input
              ref={mathInputRef}
              className="h-7 px-2 text-xs border border-stone-200 rounded outline-none focus:border-indigo-400 w-44 font-mono"
              placeholder="x^2 + y^2 = 1"
              value={mathFormula}
              onChange={(e) => setMathFormula(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') insertMath(mathFormula);
                if (e.key === 'Escape') setShowMathInput(false);
              }}
            />
            <button
              className="h-7 px-2 text-xs bg-indigo-500 text-white rounded hover:bg-indigo-600"
              onClick={() => insertMath(mathFormula)}
            >
              确定
            </button>
          </div>
        )}
      </div>

      {/* Emoji */}
      <div className="relative">
        <CendTooltip title="表情符号">
          <button
            className="h-7 w-7 flex items-center justify-center rounded text-black/50 hover:bg-black/[0.04] transition-colors text-sm"
            onMouseDown={(e) => {
              e.preventDefault();
              setShowEmoji((prev) => !prev);
            }}
          >
            😊
          </button>
        </CendTooltip>
        {showEmoji && (
          <EmojiPicker
            onSelect={insertEmoji}
            onClose={() => setShowEmoji(false)}
          />
        )}
      </div>

      <div className="w-px h-5 bg-black/[0.06] mx-0.5" />

      {/* Apply Body Format */}
      <CendTooltip title="正文格式">
        <button
          className="h-7 w-7 flex items-center justify-center rounded text-black/30 hover:text-black/60 hover:bg-black/[0.04] transition-colors"
          onClick={applyBodyFormat}
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
      </CendTooltip>

      {/* Spacer */}
      <div className="flex-1" />

      {/* Word Count */}
      <span className="text-[10px] text-black/30 tabular-nums whitespace-nowrap">
        {wordCount} 字
      </span>
    </div>
  );
}
