import { useMemo, useState } from 'react';

interface DiscountBarProps {
  /** 基准价（元），为 0 时不渲染 */
  baseFee: number;
  /** 可选标题 */
  label?: string;
}

const PRESETS = [
  { label: '不打折', rate: 1.0 },
  { label: '9折', rate: 0.9 },
  { label: '8折', rate: 0.8 },
  { label: '7折', rate: 0.7 },
  { label: '5折', rate: 0.5 },
];

function fmtMoney(n: number): string {
  if (n >= 10000) {
    const w = n / 10000;
    return w % 1 === 0 ? `¥${w.toLocaleString()}万` : `¥${w.toFixed(2)}万`;
  }
  return `¥${Math.round(n).toLocaleString()}`;
}

export function DiscountBar({ baseFee, label }: DiscountBarProps) {
  const [activeKey, setActiveKey] = useState('none');
  const [customVal, setCustomVal] = useState('');

  const selectedRate = useMemo(() => {
    if (activeKey === 'custom' && customVal) {
      const v = parseFloat(customVal);
      if (!isNaN(v) && v >= 0.1 && v <= 1.0) return v;
    }
    if (activeKey !== 'none' && activeKey !== 'custom') {
      return PRESETS.find((p) => p.label === activeKey)?.rate ?? 1.0;
    }
    return 1.0;
  }, [activeKey, customVal]);

  const discountedFee = useMemo(
    () => baseFee * selectedRate,
    [baseFee, selectedRate],
  );

  if (baseFee <= 0) return null;

  const isDiscounted = selectedRate < 1.0;

  return (
    <div className="rounded-xl border border-[#E8E8E8] bg-[#F8F9FB] p-4 space-y-3">
      {/* Header */}
      <div className="flex items-center justify-between">
        <span className="text-sm font-medium text-[#333333]">
          {label || '费用折扣'}
        </span>
        {isDiscounted && (
          <span className="text-xl font-bold text-[#000000]">
            {fmtMoney(discountedFee)}
          </span>
        )}
      </div>

      {/* Preset buttons + custom */}
      <div className="flex items-center gap-2 flex-wrap">
        {PRESETS.map((p) => (
          <button
            key={p.label}
            type="button"
            onClick={() => {
              setActiveKey(p.label);
              if (p.rate === 1.0) setCustomVal('');
            }}
            className={`px-3 py-1.5 text-xs rounded-lg transition-colors cursor-pointer ${
              activeKey === p.label ||
              (!isDiscounted && p.rate === 1.0 && activeKey === 'none')
                ? 'bg-[#000000] text-white'
                : 'bg-white border border-[#D4D4D4] text-[#333333] hover:border-[#000000] hover:text-[#000000]'
            }`}
          >
            {p.label}
          </button>
        ))}

        {/* Custom input */}
        <div className="flex items-center gap-1.5">
          <span className="text-xs text-[#525252]">自定义:</span>
          <input
            type="text"
            value={customVal}
            onChange={(e) => {
              setCustomVal(e.target.value);
              if (e.target.value) setActiveKey('custom');
            }}
            onFocus={() => setActiveKey('custom')}
            placeholder="如 0.85"
            className="w-16 h-7 px-2 text-xs text-[#000000] border border-[#D4D4D4] bg-white rounded-lg focus:border-[#000000] focus:outline-none transition-colors"
          />
        </div>
      </div>

      {/* Floating range based on discounted price */}
      <div className="text-xs text-[#525252]">
        浮动范围: {fmtMoney(discountedFee * 0.8)} ~{' '}
        {fmtMoney(discountedFee * 1.2)}
      </div>
    </div>
  );
}
