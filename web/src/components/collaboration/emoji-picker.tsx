import { useEffect, useRef, useState } from 'react';

const EMOJI_DATA: { category: string; emojis: string[] }[] = [
  {
    category: '表情',
    emojis: [
      '😀',
      '😃',
      '😄',
      '😁',
      '😅',
      '😂',
      '🤣',
      '😊',
      '😇',
      '🙂',
      '😉',
      '😌',
      '😍',
      '🥰',
      '😘',
      '😗',
      '😋',
      '😛',
      '😜',
      '🤪',
      '😎',
      '🤩',
      '🥳',
      '😏',
      '😒',
      '😞',
      '😔',
      '😟',
      '😕',
      '🙁',
      '😢',
      '😭',
      '😤',
      '😡',
      '🤬',
      '😱',
      '😨',
      '😰',
      '😥',
      '😓',
      '🤔',
      '🤗',
      '🤫',
      '🤭',
      '😶',
      '😐',
      '😑',
      '😬',
      '😴',
      '🤒',
    ],
  },
  {
    category: '手势',
    emojis: [
      '👍',
      '👎',
      '👌',
      '✌️',
      '🤞',
      '🤟',
      '🤘',
      '🤙',
      '👊',
      '✊',
      '👏',
      '🙌',
      '🤝',
      '🙏',
      '💪',
      '🦾',
      '✍️',
      '🤳',
      '👈',
      '👉',
      '☝️',
      '👆',
      '👇',
      '🖐️',
      '✋',
      '🤚',
      '🖖',
      '🤏',
    ],
  },
  {
    category: '符号',
    emojis: [
      '❤️',
      '🧡',
      '💛',
      '💚',
      '💙',
      '💜',
      '🖤',
      '🤍',
      '🤎',
      '💔',
      '💯',
      '🔥',
      '⭐',
      '🌟',
      '✨',
      '💡',
      '💎',
      '🎉',
      '🎊',
      '🎈',
      '✅',
      '❌',
      '⚠️',
      '🚫',
      'ℹ️',
      '❓',
      '❗',
      '💬',
      '🗨️',
      '📢',
      '🔔',
      '🔕',
      '💤',
      '🏁',
      '🚩',
      '🎯',
      '📌',
      '📍',
      '🔖',
      '📎',
    ],
  },
  {
    category: '办公',
    emojis: [
      '📝',
      '📄',
      '📋',
      '📊',
      '📈',
      '📉',
      '📅',
      '📆',
      '🗂️',
      '📁',
      '📂',
      '🗃️',
      '📇',
      '📑',
      '🔍',
      '🔎',
      '📧',
      '📨',
      '📩',
      '📲',
      '💻',
      '🖥️',
      '⌨️',
      '🖱️',
      '🖨️',
      '📱',
      '☎️',
      '🔗',
      '📐',
      '📏',
      '🗓️',
      '📌',
      '✂️',
      '🖊️',
      '🖋️',
      '✒️',
      '📎',
      '🔒',
      '🔓',
      '🔐',
    ],
  },
  {
    category: '箭头',
    emojis: [
      '➡️',
      '⬅️',
      '⬆️',
      '⬇️',
      '↗️',
      '↘️',
      '↙️',
      '↖️',
      '🔄',
      '🔁',
      '🔃',
      '⤴️',
      '⤵️',
      '↪️',
      '↩️',
      '⏩',
      '⏪',
      '⏫',
      '⏬',
      '▶️',
      '◀️',
      '🔽',
      '🔼',
    ],
  },
];

interface Props {
  onSelect: (emoji: string) => void;
  onClose: () => void;
}

export default function EmojiPicker({ onSelect, onClose }: Props) {
  const [activeCategory, setActiveCategory] = useState(0);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const handleClickOutside = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) {
        onClose();
      }
    };
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, [onClose]);

  return (
    <div
      ref={ref}
      className="absolute bottom-full left-0 mb-1 bg-white border border-stone-200 rounded-lg shadow-lg z-50 p-2 w-80"
    >
      {/* Category tabs */}
      <div className="flex gap-0.5 mb-2 border-b border-stone-100 pb-1.5">
        {EMOJI_DATA.map((cat, i) => (
          <button
            key={cat.category}
            className={`px-2 py-0.5 text-[10px] rounded transition-colors ${
              i === activeCategory
                ? 'bg-indigo-50 text-indigo-600'
                : 'text-stone-400 hover:text-stone-600'
            }`}
            onClick={() => setActiveCategory(i)}
          >
            {cat.category}
          </button>
        ))}
      </div>
      {/* Emoji grid */}
      <div className="grid grid-cols-10 gap-0.5 max-h-44 overflow-y-auto">
        {EMOJI_DATA[activeCategory].emojis.map((emoji) => (
          <button
            key={emoji}
            className="w-7 h-7 flex items-center justify-center text-base rounded hover:bg-stone-100 transition-colors cursor-pointer"
            onClick={() => onSelect(emoji)}
          >
            {emoji}
          </button>
        ))}
      </div>
    </div>
  );
}
