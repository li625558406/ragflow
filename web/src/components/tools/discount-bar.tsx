import { useState } from 'react';

/* ═══════════════════════════════════════════════════════════
   折扣选择器（受控组件，外部管理 rate 状态）
   ═══════════════════════════════════════════════════════════ */

export const PRESETS = [
  { label: '不打折', rate: 1.0 },
  { label: '9折', rate: 0.9 },
  { label: '8折', rate: 0.8 },
  { label: '7折', rate: 0.7 },
  { label: '5折', rate: 0.5 },
];

interface DiscountSelectorProps {
  /** 当前折扣率（1.0 = 不打折） */
  rate: number;
  /** 折扣率变化回调 */
  onRateChange: (rate: number) => void;
  /** 标签文字 */
  label?: string;
}

export function DiscountSelector({
  rate,
  onRateChange,
  label,
}: DiscountSelectorProps) {
  const [customInput, setCustomInput] = useState('');
  const [activeMode, setActiveMode] = useState<'preset' | 'custom'>('preset');

  // 判断哪个预设按钮高亮（仅 preset 模式下匹配）
  const isPresetActive = (presetRate: number) => {
    if (activeMode !== 'preset') return false;
    if (presetRate === 1.0 && rate >= 0.99) return true;
    return Math.abs(rate - presetRate) < 0.001;
  };

  const isCustomActive = activeMode === 'custom';

  const handlePresetClick = (presetLabel: string, presetRate: number) => {
    setActiveMode('preset');
    setCustomInput('');
    onRateChange(presetRate);
  };

  const handleCustomChange = (val: string) => {
    setActiveMode('custom');
    setCustomInput(val);
    const parsed = parseFloat(val);
    // 只有输入了合法数值才回调更新，非法值不改变外部 rate
    if (!isNaN(parsed) && parsed >= 0.1 && parsed <= 1.0) {
      onRateChange(parsed);
    }
  };

  const handleCustomFocus = () => {
    setActiveMode('custom');
  };

  return (
    <div className="flex items-center gap-2 flex-wrap">
      {label && (
        <span className="text-xs text-[#525252] shrink-0">{label}</span>
      )}
      {PRESETS.map((p) => (
        <button
          key={p.label}
          type="button"
          onClick={() => handlePresetClick(p.label, p.rate)}
          className={`px-3 py-1.5 text-xs rounded-lg transition-colors cursor-pointer ${
            isPresetActive(p.rate)
              ? 'bg-[#000000] text-white'
              : 'bg-white border border-[#D4D4D4] text-[#333333] hover:border-[#000000] hover:text-[#000000]'
          }`}
        >
          {p.label}
        </button>
      ))}
      <div className="flex items-center gap-1.5">
        <span className="text-xs text-[#525252]">自定义:</span>
        <input
          type="text"
          value={customInput}
          onChange={(e) => handleCustomChange(e.target.value)}
          onFocus={handleCustomFocus}
          placeholder="如 0.85"
          className={`w-16 h-7 px-2 text-xs text-[#000000] border rounded-lg focus:outline-none transition-colors ${
            isCustomActive
              ? 'border-[#000000]'
              : 'border-[#D4D4D4] focus:border-[#000000]'
          }`}
        />
      </div>
    </div>
  );
}
