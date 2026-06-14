import { useCallback, useState } from 'react';
import { DiscountSelector } from './discount-bar';

// 费率分档表: [上限(万元), 货物招标%, 服务招标%, 工程招标%]
const TIERS: [number, number, number, number][] = [
  [100, 1.5, 1.5, 1.0],
  [500, 1.1, 0.8, 0.7],
  [1000, 0.8, 0.45, 0.55],
  [5000, 0.5, 0.25, 0.35],
  [10000, 0.25, 0.1, 0.2],
  [50000, 0.05, 0.05, 0.05],
  [100000, 0.035, 0.035, 0.035],
  [500000, 0.008, 0.008, 0.008],
  [1000000, 0.006, 0.006, 0.006],
];
const ABOVE_TIER_RATE: [number, number, number] = [0.004, 0.004, 0.004];
const LABELS = ['货物（采购）招标', '服务招标', '工程招标'];

interface FeeResult {
  label: string;
  fee: number;
  lower: number;
  upper: number;
}

interface CalcStep {
  range: string;
  rate: number;
  amountWan: number;
  result: number;
}

interface FeeTrace {
  steps: CalcStep[];
  aboveStep: CalcStep | null;
  total: number;
}

function calcTieredWithTrace(amountWan: number, rateCol: number): FeeTrace {
  const steps: CalcStep[] = [];
  let fee = 0;
  let prevUpper = 0;
  for (const [upper, ...rates] of TIERS) {
    const rate = rates[rateCol] / 100;
    if (amountWan <= prevUpper) break;
    const segmentAmount = Math.min(amountWan, upper) - prevUpper;
    const segmentFee = segmentAmount * 10000 * rate;
    if (segmentAmount > 0) {
      steps.push({
        range: `${prevUpper} - ${upper} 万元`,
        rate,
        amountWan: segmentAmount,
        result: segmentFee,
      });
    }
    fee += segmentFee;
    prevUpper = upper;
  }
  let aboveStep: CalcStep | null = null;
  if (amountWan > TIERS[TIERS.length - 1][0]) {
    const aboveRate = ABOVE_TIER_RATE[rateCol] / 100;
    const aboveAmount = amountWan - TIERS[TIERS.length - 1][0];
    const aboveFee = aboveAmount * 10000 * aboveRate;
    aboveStep = {
      range: `${TIERS[TIERS.length - 1][0]} 万元以上`,
      rate: aboveRate,
      amountWan: aboveAmount,
      result: aboveFee,
    };
    fee += aboveFee;
  }
  return { steps, aboveStep, total: fee };
}

function formatYuan(yuan: number): string {
  return yuan.toLocaleString('zh-CN', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
}

function calcAll(amountWan: number): {
  results: FeeResult[];
  traces: FeeTrace[];
} {
  const traces = LABELS.map((_, i) => calcTieredWithTrace(amountWan, i));
  const results = LABELS.map((label, i) => {
    const fee = traces[i].total;
    return { label, fee, lower: fee * 0.8, upper: fee * 1.2 };
  });
  return { results, traces };
}

export default function AgencyFeeCalculator() {
  const [input, setInput] = useState('');
  const [amount, setAmount] = useState<number | null>(null);
  const [error, setError] = useState('');
  const [expanded, setExpanded] = useState(false);
  const [discountRate, setDiscountRate] = useState(1.0);

  const handleReset = () => {
    setInput('');
    setAmount(null);
    setError('');
    setExpanded(false);
    setDiscountRate(1.0);
  };

  const handleCalc = useCallback(() => {
    const raw = input.replace(/[,，\s]/g, '');
    if (!raw) {
      setError('请输入中标金额');
      setAmount(null);
      return;
    }
    const val = parseFloat(raw);
    if (isNaN(val) || val <= 0) {
      setError('请输入大于0的数字');
      setAmount(null);
      return;
    }
    setError('');
    setExpanded(false);
    setAmount(val);
  }, [input]);

  const data = amount !== null ? calcAll(amount) : null;
  const results = data?.results;
  const traces = data?.traces;

  return (
    <div className="h-full flex flex-col">
      {/* Title bar */}
      <div className="shrink-0 px-6 pt-5 pb-4 border-b border-[#D4D4D4] bg-white">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="w-9 h-9 bg-[#EAEAEA] rounded-xl flex items-center justify-center">
              <svg
                className="w-4.5 h-4.5 text-[#000000]"
                fill="none"
                stroke="currentColor"
                strokeWidth={1.5}
                viewBox="0 0 24 24"
              >
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  d="M5 3h14a2 2 0 012 2v14a2 2 0 01-2 2H5a2 2 0 01-2-2V5a2 2 0 012-2z M9 9h6 M10 13h4v4h-4z"
                />
              </svg>
            </div>
            <div>
              <h2 className="text-[15px] font-bold text-[#000000]">
                招标代理服务费计算器
              </h2>
              <p className="text-[11px] text-[#1a1a1a]">
                依据：闽招协[2021]32号 收费指导价 · 差额定率分档累进法
              </p>
            </div>
          </div>
          <button
            onClick={handleReset}
            className="shrink-0 flex items-center gap-1.5 px-3.5 py-1.5 text-xs font-medium bg-[#000000] text-white hover:bg-[#1a1a1a] border border-[#000000] rounded-lg transition-colors"
          >
            <svg
              className="size-3"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              viewBox="0 0 24 24"
            >
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"
              />
            </svg>
            重置
          </button>
        </div>
      </div>

      {/* Left-Right layout */}
      <div className="flex-1 flex min-h-0 overflow-hidden">
        {/* Left: Form + Results */}
        <div className="flex-1 overflow-y-auto p-6 space-y-4 min-w-0">
          {/* Input */}
          <div>
            <label className="block text-xs font-medium text-[#000000] mb-1.5">
              输入中标金额
            </label>
            <div className="flex gap-3">
              <div className="flex-1 relative">
                <input
                  type="text"
                  value={input}
                  onChange={(e) => setInput(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter') handleCalc();
                  }}
                  placeholder="请输入金额"
                  className="w-full bg-white border border-[#D4D4D4] rounded-xl px-4 py-2.5 pr-14 text-sm text-[#000000] outline-none focus:border-[#A3A3A3] focus:ring-2 focus:ring-[#EAEAEA] transition placeholder:text-[#A3A3A3]"
                />
                <span className="absolute right-4 top-1/2 -translate-y-1/2 text-xs text-[#1a1a1a]">
                  万元
                </span>
              </div>
              <button
                onClick={handleCalc}
                className="shrink-0 bg-[#000000] hover:bg-[#000000] text-white px-6 py-2.5 rounded-xl text-sm font-medium transition-colors"
              >
                计算
              </button>
            </div>
            {error && <p className="mt-1.5 text-xs text-red-500">{error}</p>}
          </div>

          {/* Discount selector */}
          <div className="py-2">
            <DiscountSelector
              rate={discountRate}
              onRateChange={setDiscountRate}
              label="费用折扣："
            />
          </div>

          {/* Results */}
          {results && traces && (
            <div className="bg-white rounded-2xl border border-[#D4D4D4] p-5">
              <div className="mb-4">
                <div className="text-xs text-[#1a1a1a]">中标金额</div>
                <div className="text-xl font-bold text-[#000000] mt-0.5">
                  {amount!.toLocaleString('zh-CN', {
                    minimumFractionDigits: 2,
                  })}{' '}
                  万元
                  <span className="text-xs font-normal text-[#1a1a1a] ml-2">
                    (
                    {(amount! * 10000).toLocaleString('zh-CN', {
                      minimumFractionDigits: 2,
                    })}{' '}
                    元)
                  </span>
                </div>
              </div>
              <div className="space-y-3">
                {results.map((r) => (
                  <div
                    key={r.label}
                    className="flex items-center justify-between py-2 border-b border-[#EAEAEA] last:border-0"
                  >
                    <span className="text-sm text-[#000000]">{r.label}</span>
                    <div className="text-right">
                      <span className="text-lg font-semibold text-[#000000]">
                        {formatYuan(r.fee)}
                      </span>
                      <span className="text-xs text-[#1a1a1a] ml-1">元</span>
                      {discountRate < 1.0 && (
                        <div className="text-sm font-medium text-[#000000] mt-0.5">
                          折扣后：{formatYuan(r.fee * discountRate)} 元
                        </div>
                      )}
                      {discountRate < 1.0 && (
                        <div className="text-xs text-[#1a1a1a] mt-0.5">
                          节省：{formatYuan(r.fee * (1 - discountRate))} 元
                        </div>
                      )}
                      <div
                        className={`text-[11px] text-[#1a1a1a] ${discountRate < 1.0 ? 'text-[#A3A3A3]' : 'mt-0.5'}`}
                      >
                        {discountRate < 1.0
                          ? `${formatYuan(r.fee * discountRate * 0.8)} ~ ${formatYuan(r.fee * discountRate * 1.2)} 元`
                          : `${formatYuan(r.lower)} ~ ${formatYuan(r.upper)} 元`}
                      </div>
                    </div>
                  </div>
                ))}
              </div>
              {results.some((r) => r.fee < 8000) && (
                <div className="mt-3 flex items-start gap-2 bg-[#EAEAEA] border border-[#D4D4D4] rounded-lg px-3 py-2">
                  <svg
                    className="w-3.5 h-3.5 text-[#1a1a1a] shrink-0 mt-0.5"
                    fill="none"
                    stroke="currentColor"
                    viewBox="0 0 24 24"
                  >
                    <path
                      strokeLinecap="round"
                      strokeLinejoin="round"
                      strokeWidth={2}
                      d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-2.5L13.732 4.5c-.77-.833-2.694-.833-3.464 0L3.34 16.5c-.77.833.192 2.5 1.732 2.5z"
                    />
                  </svg>
                  <span className="text-[11px] text-[#000000]">
                    部分结果低于 8,000 元，实际收费请参考指导价浮动范围
                  </span>
                </div>
              )}

              {/* Calculation Process */}
              <div className="mt-4 pt-3 border-t border-[#EAEAEA]">
                <button
                  onClick={() => setExpanded(!expanded)}
                  className="flex items-center gap-1.5 text-xs text-[#1a1a1a] hover:text-[#000000] transition-colors"
                >
                  <svg
                    className={`w-3 h-3 transition-transform ${expanded ? 'rotate-90' : ''}`}
                    fill="none"
                    stroke="currentColor"
                    viewBox="0 0 24 24"
                  >
                    <path
                      strokeLinecap="round"
                      strokeLinejoin="round"
                      strokeWidth={2}
                      d="M9 5l7 7-7 7"
                    />
                  </svg>
                  计算过程（差额定率分档累进法）
                </button>
                {expanded && (
                  <div className="mt-3 space-y-4">
                    {results.map((r, catIdx) => (
                      <div key={r.label}>
                        <div className="text-xs font-medium text-[#000000] mb-2">
                          {r.label}
                        </div>
                        <div className="space-y-1 text-[11px] text-[#1a1a1a]">
                          {traces[catIdx].steps.map((step, i) => (
                            <div key={i} className="flex justify-between">
                              <span>
                                {step.range}: {step.amountWan.toFixed(2)} 万元 ×{' '}
                                {(step.rate * 100).toFixed(2)}%
                              </span>
                              <span className="font-medium text-[#000000]">
                                = {formatYuan(step.result)} 元
                              </span>
                            </div>
                          ))}
                          {traces[catIdx].aboveStep && (
                            <div className="flex justify-between">
                              <span>
                                {traces[catIdx].aboveStep!.range}:{' '}
                                {traces[catIdx].aboveStep!.amountWan.toFixed(2)}{' '}
                                万元 ×{' '}
                                {(traces[catIdx].aboveStep!.rate * 100).toFixed(
                                  2,
                                )}
                                %
                              </span>
                              <span className="font-medium text-[#000000]">
                                = {formatYuan(traces[catIdx].aboveStep!.result)}{' '}
                                元
                              </span>
                            </div>
                          )}
                          <div className="flex justify-between border-t border-[#EAEAEA] pt-1 text-xs font-semibold text-[#000000]">
                            <span>合计</span>
                            <span>= {formatYuan(traces[catIdx].total)} 元</span>
                          </div>
                        </div>
                      </div>
                    ))}
                    <div className="text-[10px] text-[#1a1a1a]">
                      浮动范围 = 计算结果 × (1 ± 20%)
                    </div>
                  </div>
                )}
              </div>
            </div>
          )}
        </div>

        {/* Right: Rate table */}
        <div className="w-72 shrink-0 border-l border-[#D4D4D4] bg-[#FFFFFF]/50 overflow-y-auto p-5">
          <div className="bg-white rounded-2xl border border-[#D4D4D4] p-4">
            <h4 className="text-sm font-semibold text-[#1a1a1a] mb-3">
              费率分档表
            </h4>
            <table className="w-full text-xs table-fixed">
              <thead>
                <tr className="border-b border-[#D4D4D4]">
                  <th className="text-left py-1.5 text-[#1a1a1a] font-medium">
                    分档（万元）
                  </th>
                  <th className="text-right py-1.5 text-[#1a1a1a] font-medium w-11">
                    货物
                  </th>
                  <th className="text-right py-1.5 text-[#1a1a1a] font-medium w-11">
                    服务
                  </th>
                  <th className="text-right py-1.5 text-[#1a1a1a] font-medium w-11">
                    工程
                  </th>
                </tr>
              </thead>
              <tbody>
                {TIERS.map(([upper, r0, r1, r2], i) => {
                  const prev = i === 0 ? 0 : TIERS[i - 1][0];
                  return (
                    <tr
                      key={i}
                      className="border-b border-[#EAEAEA] last:border-0"
                    >
                      <td className="py-1.5 text-[#000000]">
                        {prev} - {upper}
                      </td>
                      <td className="text-right py-1.5 text-[#000000]">
                        {r0}%
                      </td>
                      <td className="text-right py-1.5 text-[#000000]">
                        {r1}%
                      </td>
                      <td className="text-right py-1.5 text-[#000000]">
                        {r2}%
                      </td>
                    </tr>
                  );
                })}
                <tr>
                  <td className="py-1.5 text-[#000000]">
                    {TIERS[TIERS.length - 1][0]} 以上
                  </td>
                  <td className="text-right py-1.5 text-[#000000]">
                    {ABOVE_TIER_RATE[0]}%
                  </td>
                  <td className="text-right py-1.5 text-[#000000]">
                    {ABOVE_TIER_RATE[1]}%
                  </td>
                  <td className="text-right py-1.5 text-[#000000]">
                    {ABOVE_TIER_RATE[2]}%
                  </td>
                </tr>
              </tbody>
            </table>
            <p className="text-[11px] text-[#1a1a1a] mt-3">
              注：上下浮动幅度不超过 20%
            </p>
          </div>
        </div>
      </div>
    </div>
  );
}
