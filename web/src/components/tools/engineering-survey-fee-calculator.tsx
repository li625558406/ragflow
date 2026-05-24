import { useCallback, useMemo, useState } from 'react';

/* ═══════════════════════════════════════════════════════════
   工程勘察设计费计算器
   依据：《工程勘察设计收费标准》(2002)10号
   ═══════════════════════════════════════════════════════════ */

// ── 附表一：工程设计收费基价表 ──
// [计费额(万元), 收费基价(万元)]
const DESIGN_FEE_TABLE: [number, number][] = [
  [200, 9.0],
  [500, 20.9],
  [1000, 38.8],
  [3000, 103.8],
  [5000, 163.9],
  [8000, 249.6],
  [10000, 304.8],
  [20000, 566.8],
  [40000, 1054.0],
  [60000, 1515.2],
  [80000, 1960.1],
  [100000, 2393.4],
  [200000, 4450.8],
  [400000, 8276.7],
  [600000, 11897.5],
  [800000, 15391.4],
  [1000000, 18793.8],
  [2000000, 34948.9],
];
const DESIGN_ABOVE_RATE = 0.016; // 计费额>2000000万元, 乘以1.6%

// ── 附表二：工程设计收费专业调整系数 ──
interface ProfItem {
  name: string;
  coef: number;
}
const PROFESSIONAL_GROUPS: { group: string; items: ProfItem[] }[] = [
  {
    group: '矿山采选工程',
    items: [
      { name: '黑色、黄金、化学、非金属及其他矿采选', coef: 1.1 },
      { name: '采煤工程，有色、铀矿采选', coef: 1.2 },
      { name: '选煤及其他煤炭工程', coef: 1.3 },
    ],
  },
  {
    group: '加工冶炼工程',
    items: [
      { name: '各类冷加工工程', coef: 1.0 },
      { name: '船舶水工工程', coef: 1.1 },
      { name: '各类冶炼、热加工、压力加工工程', coef: 1.2 },
      { name: '核加工工程', coef: 1.3 },
    ],
  },
  {
    group: '石油化工工程',
    items: [
      { name: '石油、化工、石化、化纤、医药工程', coef: 1.2 },
      { name: '核化工工程', coef: 1.6 },
    ],
  },
  {
    group: '水利电力工程',
    items: [
      { name: '风力发电、其他水利工程', coef: 0.8 },
      { name: '火电工程', coef: 1.0 },
      { name: '核电常规岛、水电、水库、送变电工程', coef: 1.2 },
      { name: '核能工程', coef: 1.6 },
    ],
  },
  {
    group: '交通运输工程',
    items: [
      { name: '机场场道工程', coef: 0.8 },
      { name: '公路、城市道路工程', coef: 0.9 },
      { name: '机场空管和助航灯光、轻轨工程', coef: 1.0 },
      { name: '水运、地铁、桥梁、隧道工程', coef: 1.1 },
      { name: '索道工程', coef: 1.3 },
    ],
  },
  {
    group: '建筑市政工程',
    items: [
      { name: '邮政工艺工程', coef: 0.8 },
      { name: '建筑、市政、电信工程', coef: 1.0 },
      { name: '人防、园林绿化、广电工艺工程', coef: 1.1 },
    ],
  },
  {
    group: '农业林业工程',
    items: [
      { name: '农业工程', coef: 0.9 },
      { name: '林业工程', coef: 0.8 },
    ],
  },
];

// 扁平化专业列表
const ALL_PROF_ITEMS: { name: string; coef: number; group: string }[] =
  PROFESSIONAL_GROUPS.flatMap((g) =>
    g.items.map((it) => ({ ...it, group: g.group })),
  );

// ── 工程复杂程度调整系数 ──
const COMPLEXITY_OPTIONS = [
  { label: 'I级（一般）', coef: 0.85 },
  { label: 'II级（较复杂）', coef: 1.0 },
  { label: 'III级（复杂）', coef: 1.15 },
];

// ── 工程勘察：技术工作费收费比例 ──
const SURVEY_TECH_RATIOS = [
  { name: '工程测量', ratio: 0.15 },
  { name: '岩土工程勘察', ratio: 0.2 },
  { name: '水文地质勘察（供水井凿井）- 简单', ratio: 0.15 },
  { name: '水文地质勘察（供水井凿井）- 中等', ratio: 0.18 },
  { name: '水文地质勘察（供水井凿井）- 复杂', ratio: 0.2 },
  { name: '水文地质勘察（其他）- 简单', ratio: 0.27 },
  { name: '水文地质勘察（其他）- 中等', ratio: 0.3 },
  { name: '水文地质勘察（其他）- 复杂', ratio: 0.33 },
  { name: '工程水文气象勘察', ratio: 0.22 },
  { name: '工程物探', ratio: 0.22 },
  { name: '室内试验', ratio: 0.1 },
];

// ── 工程勘察：温度附加调整系数 ──
// ── 工程勘察：海拔附加调整系数 ──
const ALTITUDE_COEFS = [
  { label: '2000m以下', coef: 1.0 },
  { label: '2000~3000m', coef: 1.1 },
  { label: '3001~3500m', coef: 1.2 },
  { label: '3501~4000m', coef: 1.3 },
];

// ── 水利水电工程勘察 ──
// 收费基价表 (同附表一的值)
const WATER_CONSERVANCY_TABLE: [number, number][] = DESIGN_FEE_TABLE; // 同表
const WATER_ABOVE_RATE = 0.017; // >2000000万元乘以1.7%

const WATER_PROF_COEFS = [
  { name: '水电', coef: 1.4 },
  { name: '水库', coef: 1.04 },
  { name: '潮汐发电', coef: 1.7 },
  { name: '水土保持', coef: 0.5 },
  { name: '引调水和河道治理', coef: 0.8 },
  { name: '灌区田间', coef: 0.35 },
  { name: '城市防护、河口整治', coef: 0.88 },
  { name: '围垦', coef: 0.82 },
];

// ── 辅助函数 ──
function linearInterp(
  x: number,
  table: [number, number][],
  aboveRate: number,
): number {
  if (x <= table[0][0]) return table[0][1];
  for (let i = 1; i < table.length; i++) {
    if (x <= table[i][0]) {
      const [x0, y0] = table[i - 1];
      const [x1, y1] = table[i];
      return y0 + ((y1 - y0) * (x - x0)) / (x1 - x0);
    }
  }
  // 超出最大分档
  const [maxX, maxY] = table[table.length - 1];
  return maxY + (x - maxX) * aboveRate;
}

function fmtWan(yuan: number): string {
  return yuan.toLocaleString('zh-CN', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
}

function fmtYuan(yuan: number): string {
  return yuan.toLocaleString('zh-CN', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
}

// ── 计算函数 ──
function calcDesignFee(
  amountWan: number,
  profCoef: number,
  complexityCoef: number,
  extraAdjCoef: number,
  options: {
    overall?: boolean;
    coordination?: boolean;
    budget?: boolean;
    asbuilt?: boolean;
  },
) {
  const basePrice = linearInterp(
    amountWan,
    DESIGN_FEE_TABLE,
    DESIGN_ABOVE_RATE,
  ); // 万元
  const basicFee = basePrice * profCoef * complexityCoef * extraAdjCoef; // 万元
  const otherFee =
    (options.overall ? basicFee * 0.05 : 0) +
    (options.coordination ? basicFee * 0.05 : 0) +
    (options.budget ? basicFee * 0.1 : 0) +
    (options.asbuilt ? basicFee * 0.08 : 0);
  const benchmark = basicFee + otherFee;
  return { basePrice, basicFee, otherFee, benchmark };
}

function calcSurveyFee(
  basePrice: number, // 收费基价（元）
  quantity: number, // 实物工作量
  techRatio: number, // 技术工作费比例
  adjCoefs: number[], // 附加调整系数列表
) {
  // 附加调整系数不能连乘，而是相加减个数加1
  let adjCoef = 1.0;
  if (adjCoefs.length > 0) {
    const sum = adjCoefs.reduce((a, b) => a + b, 0);
    adjCoef = sum - adjCoefs.length + 1;
  }
  const physicalFee = basePrice * quantity * adjCoef; // 实物工作收费
  const techFee = physicalFee * techRatio; // 技术工作收费
  const benchmark = physicalFee + techFee;
  return { physicalFee, techFee, benchmark, adjCoef };
}

function calcWaterConservancyFee(
  amountWan: number,
  profCoef: number,
  complexityCoef: number,
  adjCoef: number,
) {
  const basePrice = linearInterp(
    amountWan,
    WATER_CONSERVANCY_TABLE,
    WATER_ABOVE_RATE,
  );
  const basicFee = basePrice * profCoef * complexityCoef * adjCoef;
  return { basePrice, basicFee };
}

// ── Tab类型 ──
type TabId = 'design' | 'survey' | 'water';
const TABS: { id: TabId; label: string }[] = [
  { id: 'design', label: '工程设计收费' },
  { id: 'survey', label: '通用工程勘察' },
  { id: 'water', label: '水利水电勘察' },
];

export default function EngineeringSurveyFeeCalculator() {
  const [tab, setTab] = useState<TabId>('design');

  return (
    <div className="h-full flex flex-col">
      {/* Title bar */}
      <div className="shrink-0 px-6 pt-5 pb-4 border-b border-stone-100 bg-white">
        <div className="flex items-center gap-3">
          <div className="w-9 h-9 bg-indigo-100 rounded-xl flex items-center justify-center">
            <svg
              className="w-4.5 h-4.5 text-indigo-600"
              fill="none"
              stroke="currentColor"
              strokeWidth={1.5}
              viewBox="0 0 24 24"
            >
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                d="M3.75 21h16.5M4.5 3h15M5.25 3v18m13.5-18v18M9 6.75h1.5m-1.5 3h1.5m-1.5 3h1.5m3-6H15m-1.5 3H15m-1.5 3H15M9 21v-3.375c0-.621.504-1.125 1.125-1.125h3.75c.621 0 1.125.504 1.125 1.125V21"
              />
            </svg>
          </div>
          <div>
            <h2 className="text-[15px] font-bold text-stone-900">
              工程勘察设计费计算器
            </h2>
            <p className="text-[11px] text-stone-400">
              依据：工程勘察设计收费标准（2002）10号
            </p>
          </div>
        </div>
        {/* Sub tabs */}
        <div className="flex gap-1 mt-3">
          {TABS.map((t) => (
            <button
              key={t.id}
              onClick={() => setTab(t.id)}
              className={`px-3 py-1.5 rounded-lg text-xs font-medium transition ${
                tab === t.id
                  ? 'bg-indigo-500 text-white'
                  : 'bg-stone-100 text-stone-500 hover:bg-stone-200'
              }`}
            >
              {t.label}
            </button>
          ))}
        </div>
      </div>

      {/* Content */}
      <div className="flex-1 overflow-y-auto">
        {tab === 'design' && <DesignTab />}
        {tab === 'survey' && <SurveyTab />}
        {tab === 'water' && <WaterTab />}
      </div>
    </div>
  );
}

/* ═══════════════════════════════════════════
   工程设计收费 Tab
   ═══════════════════════════════════════════ */
function DesignTab() {
  const [amount, setAmount] = useState('');
  const [profIdx, setProfIdx] = useState(6 + 1); // 默认"建筑、市政、电信工程"
  const [complexIdx, setComplexIdx] = useState(1); // 默认II级
  const [extraAdj, setExtraAdj] = useState('1.0');
  const [isRenovation, setIsRenovation] = useState(false);
  const [renovationCoef, setRenovationCoef] = useState('1.1');
  const [options, setOptions] = useState({
    overall: false,
    coordination: false,
    budget: false,
    asbuilt: false,
  });
  const [result, setResult] = useState<ReturnType<typeof calcDesignFee> | null>(
    null,
  );
  const [error, setError] = useState('');

  const profCoef = useMemo(
    () => ALL_PROF_ITEMS[profIdx]?.coef ?? 1.0,
    [profIdx],
  );
  const complexCoef = COMPLEXITY_OPTIONS[complexIdx].coef;

  const handleCalc = useCallback(() => {
    const raw = amount.replace(/[,，\s]/g, '');
    if (!raw) {
      setError('请输入计费额');
      setResult(null);
      return;
    }
    const val = parseFloat(raw);
    if (isNaN(val) || val <= 0) {
      setError('请输入大于0的数字');
      setResult(null);
      return;
    }
    setError('');
    let adj = parseFloat(extraAdj) || 1.0;
    if (isRenovation) adj *= parseFloat(renovationCoef) || 1.1;
    setResult(calcDesignFee(val, profCoef, complexCoef, adj, options));
  }, [
    amount,
    profCoef,
    complexCoef,
    extraAdj,
    isRenovation,
    renovationCoef,
    options,
  ]);

  return (
    <div className="flex min-h-full">
      {/* Left: Form + Results */}
      <div className="flex-1 p-6 space-y-4 min-w-0">
        {/* 计费额 */}
        <div>
          <label className="block text-xs font-medium text-stone-500 mb-1.5">
            计费额（概算投资额）
          </label>
          <div className="flex gap-3">
            <div className="flex-1 relative">
              <input
                type="text"
                value={amount}
                onChange={(e) => setAmount(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') handleCalc();
                }}
                placeholder="请输入金额"
                className="w-full bg-stone-50 border border-stone-200 rounded-xl px-4 py-2.5 pr-14 text-sm text-stone-900 outline-none focus:border-indigo-300 focus:ring-2 focus:ring-indigo-50 transition placeholder:text-stone-400"
              />
              <span className="absolute right-4 top-1/2 -translate-y-1/2 text-xs text-stone-400">
                万元
              </span>
            </div>
            <button
              onClick={handleCalc}
              className="shrink-0 bg-indigo-500 hover:bg-indigo-600 text-white px-6 py-2.5 rounded-xl text-sm font-medium transition-colors"
            >
              计算
            </button>
          </div>
          {error && <p className="mt-1.5 text-xs text-red-500">{error}</p>}
        </div>

        {/* 专业调整系数 */}
        <div>
          <label className="block text-xs font-medium text-stone-500 mb-1.5">
            专业类型
          </label>
          <select
            value={profIdx}
            onChange={(e) => setProfIdx(Number(e.target.value))}
            className="w-full bg-stone-50 border border-stone-200 rounded-xl px-4 py-2.5 text-sm text-stone-900 outline-none focus:border-indigo-300 focus:ring-2 focus:ring-indigo-50 transition"
          >
            {PROFESSIONAL_GROUPS.map((g) => (
              <optgroup key={g.group} label={g.group}>
                {g.items.map((it, i) => {
                  const globalIdx = ALL_PROF_ITEMS.findIndex(
                    (a) => a.name === it.name && a.group === g.group,
                  );
                  return (
                    <option key={i} value={globalIdx}>
                      {it.name}（系数 {it.coef}）
                    </option>
                  );
                })}
              </optgroup>
            ))}
          </select>
        </div>

        {/* 工程复杂程度 */}
        <div>
          <label className="block text-xs font-medium text-stone-500 mb-1.5">
            工程复杂程度
          </label>
          <div className="flex gap-2">
            {COMPLEXITY_OPTIONS.map((c, i) => (
              <button
                key={i}
                onClick={() => setComplexIdx(i)}
                className={`flex-1 px-3 py-2 rounded-lg text-xs font-medium transition ${
                  complexIdx === i
                    ? 'bg-indigo-100 text-indigo-700 border border-indigo-200'
                    : 'bg-stone-50 text-stone-500 border border-stone-200 hover:bg-stone-100'
                }`}
              >
                {c.label}
                <span className="block text-[10px] opacity-60">
                  系数 {c.coef}
                </span>
              </button>
            ))}
          </div>
        </div>

        {/* 改扩建 */}
        <div className="flex items-center gap-3">
          <label className="flex items-center gap-2 cursor-pointer">
            <input
              type="checkbox"
              checked={isRenovation}
              onChange={(e) => setIsRenovation(e.target.checked)}
              className="rounded border-stone-300 text-indigo-500 focus:ring-indigo-200"
            />
            <span className="text-xs text-stone-600">改扩建/技术改造项目</span>
          </label>
          {isRenovation && (
            <select
              value={renovationCoef}
              onChange={(e) => setRenovationCoef(e.target.value)}
              className="bg-stone-50 border border-stone-200 rounded-lg px-3 py-1.5 text-xs text-stone-700 outline-none"
            >
              <option value="1.1">1.1（简单）</option>
              <option value="1.2">1.2（一般）</option>
              <option value="1.3">1.3（较复杂）</option>
              <option value="1.4">1.4（复杂）</option>
            </select>
          )}
        </div>

        {/* 自定义附加调整系数 */}
        <div>
          <label className="block text-xs font-medium text-stone-500 mb-1.5">
            附加调整系数（默认1.0）
          </label>
          <input
            type="text"
            value={extraAdj}
            onChange={(e) => setExtraAdj(e.target.value)}
            className="w-full bg-stone-50 border border-stone-200 rounded-xl px-4 py-2.5 text-sm text-stone-900 outline-none focus:border-indigo-300 focus:ring-2 focus:ring-indigo-50 transition placeholder:text-stone-400"
            placeholder="默认1.0，多个系数相加减个数加1"
          />
        </div>

        {/* 其他设计收费 */}
        <div>
          <label className="block text-xs font-medium text-stone-500 mb-1.5">
            其他设计收费（可选）
          </label>
          <div className="flex flex-wrap gap-2">
            {[
              { key: 'overall' as const, label: '总体设计费（+5%）' },
              { key: 'coordination' as const, label: '主体设计协调费（+5%）' },
              { key: 'budget' as const, label: '施工图预算编制费（+10%）' },
              { key: 'asbuilt' as const, label: '竣工图编制费（+8%）' },
            ].map((opt) => (
              <label
                key={opt.key}
                className="flex items-center gap-1.5 cursor-pointer bg-stone-50 border border-stone-200 rounded-lg px-3 py-1.5"
              >
                <input
                  type="checkbox"
                  checked={options[opt.key]}
                  onChange={(e) =>
                    setOptions((o) => ({ ...o, [opt.key]: e.target.checked }))
                  }
                  className="rounded border-stone-300 text-indigo-500 focus:ring-indigo-200"
                />
                <span className="text-xs text-stone-600">{opt.label}</span>
              </label>
            ))}
          </div>
        </div>

        {/* Results */}
        {result && (
          <div className="bg-white rounded-2xl border border-stone-100 p-5 space-y-3">
            <h4 className="text-sm font-semibold text-stone-700">计算结果</h4>
            <div className="space-y-2 text-sm">
              <div className="flex justify-between py-1.5 border-b border-stone-50">
                <span className="text-stone-500">收费基价（内插法）</span>
                <span className="font-medium text-stone-800">
                  {fmtWan(result.basePrice)} 万元
                </span>
              </div>
              <div className="flex justify-between py-1.5 border-b border-stone-50">
                <span className="text-stone-500">基本设计收费</span>
                <span className="font-medium text-stone-800">
                  {fmtWan(result.basicFee)} 万元
                </span>
              </div>
              {result.otherFee > 0 && (
                <div className="flex justify-between py-1.5 border-b border-stone-50">
                  <span className="text-stone-500">其他设计收费</span>
                  <span className="font-medium text-stone-800">
                    {fmtWan(result.otherFee)} 万元
                  </span>
                </div>
              )}
              <div className="flex justify-between py-2">
                <span className="text-stone-700 font-medium">
                  工程设计收费基准价
                </span>
                <span className="text-lg font-bold text-indigo-600">
                  {fmtWan(result.benchmark)} 万元
                </span>
              </div>
              <div className="flex justify-between py-1.5 text-xs text-stone-400">
                <span>浮动下限（-20%）</span>
                <span>{fmtWan(result.benchmark * 0.8)} 万元</span>
              </div>
              <div className="flex justify-between py-1.5 text-xs text-stone-400">
                <span>浮动上限（+20%）</span>
                <span>{fmtWan(result.benchmark * 1.2)} 万元</span>
              </div>
            </div>
            {/* 参数回显 */}
            <div className="flex flex-wrap gap-1.5 mt-2">
              <span className="bg-indigo-100 text-indigo-700 rounded-lg px-2 py-0.5 text-[10px] font-medium">
                专业系数 {profCoef}
              </span>
              <span className="bg-indigo-100 text-indigo-700 rounded-lg px-2 py-0.5 text-[10px] font-medium">
                复杂程度 {complexCoef}
              </span>
              {(parseFloat(extraAdj) !== 1 || isRenovation) && (
                <span className="bg-amber-100 text-amber-700 rounded-lg px-2 py-0.5 text-[10px] font-medium">
                  附加系数{' '}
                  {(parseFloat(extraAdj) || 1) *
                    (isRenovation ? parseFloat(renovationCoef) || 1.1 : 1)}
                </span>
              )}
            </div>
          </div>
        )}
      </div>

      {/* Right: Reference table */}
      <div className="w-72 shrink-0 border-l border-stone-100 bg-white/50 overflow-y-auto p-5">
        <div className="bg-white rounded-2xl border border-stone-100 p-4">
          <h4 className="text-sm font-semibold text-stone-700 mb-3">
            收费基价表
          </h4>
          <table className="w-full text-xs table-fixed">
            <thead>
              <tr className="border-b border-stone-100">
                <th className="text-left py-1.5 text-stone-400 font-medium">
                  计费额（万元）
                </th>
                <th className="text-right py-1.5 text-stone-400 font-medium">
                  基价（万元）
                </th>
              </tr>
            </thead>
            <tbody>
              {DESIGN_FEE_TABLE.map(([amt, fee], i) => (
                <tr key={i} className="border-b border-stone-50 last:border-0">
                  <td className="py-1 text-stone-600">
                    {amt.toLocaleString()}
                  </td>
                  <td className="text-right py-1 text-stone-500">
                    {fee.toLocaleString()}
                  </td>
                </tr>
              ))}
              <tr>
                <td className="py-1 text-stone-600">&gt;2,000,000</td>
                <td className="text-right py-1 text-stone-500">×1.6%</td>
              </tr>
            </tbody>
          </table>
          <p className="text-[11px] text-stone-400 mt-3">
            注：计费额处于两个数值区间的，采用直线内插法确定收费基价
          </p>
        </div>

        <div className="bg-white rounded-2xl border border-stone-100 p-4 mt-4">
          <h4 className="text-sm font-semibold text-stone-700 mb-2">
            计算公式
          </h4>
          <div className="text-[11px] text-stone-500 space-y-1.5 leading-relaxed">
            <p>工程设计收费 = 基准价 × (1±浮动幅度)</p>
            <p>基准价 = 基本设计收费 + 其他设计收费</p>
            <p>
              基本设计收费 = 基价 × 专业调整系数 × 复杂程度系数 × 附加调整系数
            </p>
          </div>
        </div>
      </div>
    </div>
  );
}

/* ═══════════════════════════════════════════
   通用工程勘察收费 Tab
   ═══════════════════════════════════════════ */
function SurveyTab() {
  const [basePrice, setBasePrice] = useState('');
  const [quantity, setQuantity] = useState('1');
  const [techIdx, setTechIdx] = useState(1); // 默认岩土工程勘察
  const [tempCoef, setTempCoef] = useState(false);
  const [altitudeIdx, setAltitudeIdx] = useState(0);
  const [customAdj, setCustomAdj] = useState('');
  const [result, setResult] = useState<ReturnType<typeof calcSurveyFee> | null>(
    null,
  );
  const [error, setError] = useState('');

  const handleCalc = useCallback(() => {
    const bp = parseFloat(basePrice.replace(/[,，\s]/g, ''));
    const qty = parseFloat(quantity.replace(/[,，\s]/g, ''));
    if (isNaN(bp) || bp <= 0) {
      setError('请输入有效的收费基价');
      setResult(null);
      return;
    }
    if (isNaN(qty) || qty <= 0) {
      setError('请输入有效的实物工作量');
      setResult(null);
      return;
    }
    setError('');
    const techRatio = SURVEY_TECH_RATIOS[techIdx].ratio;
    const adjCoefs: number[] = [];
    if (tempCoef) adjCoefs.push(1.2);
    if (altitudeIdx > 0) adjCoefs.push(ALTITUDE_COEFS[altitudeIdx].coef);
    const custom = parseFloat(customAdj);
    if (!isNaN(custom) && custom !== 1.0 && custom > 0) adjCoefs.push(custom);
    setResult(calcSurveyFee(bp, qty, techRatio, adjCoefs));
  }, [basePrice, quantity, techIdx, tempCoef, altitudeIdx, customAdj]);

  return (
    <div className="flex min-h-full">
      {/* Left: Form + Results */}
      <div className="flex-1 p-6 space-y-4 min-w-0">
        <div className="bg-amber-50 border border-amber-100 rounded-xl px-4 py-2.5 text-xs text-amber-700">
          通用工程勘察收费适用于：工程测量、岩土工程勘察、水文地质勘察、工程物探、室内试验等
        </div>

        {/* 收费基价 */}
        <div>
          <label className="block text-xs font-medium text-stone-500 mb-1.5">
            收费基价（元）
          </label>
          <div className="relative">
            <input
              type="text"
              value={basePrice}
              onChange={(e) => setBasePrice(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') handleCalc();
              }}
              placeholder="根据勘察类型查表确定"
              className="w-full bg-stone-50 border border-stone-200 rounded-xl px-4 py-2.5 pr-8 text-sm text-stone-900 outline-none focus:border-indigo-300 focus:ring-2 focus:ring-indigo-50 transition placeholder:text-stone-400"
            />
            <span className="absolute right-4 top-1/2 -translate-y-1/2 text-xs text-stone-400">
              元
            </span>
          </div>
        </div>

        {/* 实物工作量 */}
        <div>
          <label className="block text-xs font-medium text-stone-500 mb-1.5">
            实物工作量
          </label>
          <div className="relative">
            <input
              type="text"
              value={quantity}
              onChange={(e) => setQuantity(e.target.value)}
              placeholder="如：钻孔深度、测点数等"
              className="w-full bg-stone-50 border border-stone-200 rounded-xl px-4 py-2.5 pr-14 text-sm text-stone-900 outline-none focus:border-indigo-300 focus:ring-2 focus:ring-indigo-50 transition placeholder:text-stone-400"
            />
            <span className="absolute right-4 top-1/2 -translate-y-1/2 text-xs text-stone-400">
              数量
            </span>
          </div>
        </div>

        {/* 勘察类型 / 技术工作费比例 */}
        <div>
          <label className="block text-xs font-medium text-stone-500 mb-1.5">
            勘察类型（技术工作费比例）
          </label>
          <select
            value={techIdx}
            onChange={(e) => setTechIdx(Number(e.target.value))}
            className="w-full bg-stone-50 border border-stone-200 rounded-xl px-4 py-2.5 text-sm text-stone-900 outline-none focus:border-indigo-300 focus:ring-2 focus:ring-indigo-50 transition"
          >
            {SURVEY_TECH_RATIOS.map((r, i) => (
              <option key={i} value={i}>
                {r.name}（{(r.ratio * 100).toFixed(0)}%）
              </option>
            ))}
          </select>
        </div>

        {/* 附加调整系数 */}
        <div>
          <label className="block text-xs font-medium text-stone-500 mb-1.5">
            附加调整系数
          </label>
          <div className="space-y-2">
            <label className="flex items-center gap-2 cursor-pointer">
              <input
                type="checkbox"
                checked={tempCoef}
                onChange={(e) => setTempCoef(e.target.checked)}
                className="rounded border-stone-300 text-indigo-500 focus:ring-indigo-200"
              />
              <span className="text-xs text-stone-600">
                高温/低温附加（≥35℃ 或 ≤-10℃），系数 1.2
              </span>
            </label>
            <div className="flex items-center gap-2">
              <span className="text-xs text-stone-600">海拔附加：</span>
              <select
                value={altitudeIdx}
                onChange={(e) => setAltitudeIdx(Number(e.target.value))}
                className="bg-stone-50 border border-stone-200 rounded-lg px-3 py-1.5 text-xs text-stone-700 outline-none"
              >
                {ALTITUDE_COEFS.map((a, i) => (
                  <option key={i} value={i}>
                    {a.label}（系数 {a.coef}）
                  </option>
                ))}
              </select>
            </div>
            <div className="flex items-center gap-2">
              <span className="text-xs text-stone-600">自定义：</span>
              <input
                type="text"
                value={customAdj}
                onChange={(e) => setCustomAdj(e.target.value)}
                placeholder="如1.1，无需填写则留空"
                className="bg-stone-50 border border-stone-200 rounded-lg px-3 py-1.5 text-xs text-stone-700 outline-none w-40"
              />
            </div>
          </div>
        </div>

        {/* 计算按钮 */}
        <button
          onClick={handleCalc}
          className="w-full bg-indigo-500 hover:bg-indigo-600 text-white px-6 py-2.5 rounded-xl text-sm font-medium transition-colors"
        >
          计算
        </button>
        {error && <p className="text-xs text-red-500">{error}</p>}

        {/* Results */}
        {result && (
          <div className="bg-white rounded-2xl border border-stone-100 p-5 space-y-3">
            <h4 className="text-sm font-semibold text-stone-700">计算结果</h4>
            <div className="space-y-2 text-sm">
              <div className="flex justify-between py-1.5 border-b border-stone-50">
                <span className="text-stone-500">附加调整系数（合计）</span>
                <span className="font-medium text-stone-800">
                  {result.adjCoef.toFixed(4)}
                </span>
              </div>
              <div className="flex justify-between py-1.5 border-b border-stone-50">
                <span className="text-stone-500">实物工作收费</span>
                <span className="font-medium text-stone-800">
                  {fmtYuan(result.physicalFee)} 元
                </span>
              </div>
              <div className="flex justify-between py-1.5 border-b border-stone-50">
                <span className="text-stone-500">
                  技术工作收费（
                  {(SURVEY_TECH_RATIOS[techIdx].ratio * 100).toFixed(0)}%）
                </span>
                <span className="font-medium text-stone-800">
                  {fmtYuan(result.techFee)} 元
                </span>
              </div>
              <div className="flex justify-between py-2">
                <span className="text-stone-700 font-medium">
                  勘察收费基准价
                </span>
                <span className="text-lg font-bold text-indigo-600">
                  {fmtYuan(result.benchmark)} 元
                </span>
              </div>
              <div className="flex justify-between py-1.5 text-xs text-stone-400">
                <span>主体勘察协调费（基准价×5%）</span>
                <span>{fmtYuan(result.benchmark * 0.05)} 元</span>
              </div>
              <div className="flex justify-between py-1.5 text-xs text-stone-400">
                <span>浮动下限（-20%）</span>
                <span>{fmtYuan(result.benchmark * 0.8)} 元</span>
              </div>
              <div className="flex justify-between py-1.5 text-xs text-stone-400">
                <span>浮动上限（+20%）</span>
                <span>{fmtYuan(result.benchmark * 1.2)} 元</span>
              </div>
            </div>
          </div>
        )}
      </div>

      {/* Right: Reference */}
      <div className="w-72 shrink-0 border-l border-stone-100 bg-white/50 overflow-y-auto p-5">
        <div className="bg-white rounded-2xl border border-stone-100 p-4">
          <h4 className="text-sm font-semibold text-stone-700 mb-2">
            计算公式
          </h4>
          <div className="text-[11px] text-stone-500 space-y-1.5 leading-relaxed">
            <p>勘察收费 = 基准价 × (1±浮动幅度)</p>
            <p>基准价 = 实物工作收费 + 技术工作收费</p>
            <p>实物工作收费 = 基价 × 工作量 × 附加系数</p>
            <p>技术工作收费 = 实物工作收费 × 技术比例</p>
          </div>
        </div>
        <div className="bg-white rounded-2xl border border-stone-100 p-4 mt-4">
          <h4 className="text-sm font-semibold text-stone-700 mb-2">
            附加调整系数规则
          </h4>
          <div className="text-[11px] text-stone-500 space-y-1.5 leading-relaxed">
            <p>多个附加调整系数不能连乘</p>
            <p>计算方法：各系数相加 － 系数个数 ＋ 1</p>
            <p>例：1.2 + 1.1 → 1.2 + 1.1 - 2 + 1 = 1.3</p>
          </div>
        </div>
        <div className="bg-white rounded-2xl border border-stone-100 p-4 mt-4">
          <h4 className="text-sm font-semibold text-stone-700 mb-2">
            温度附加
          </h4>
          <div className="text-[11px] text-stone-500 leading-relaxed">
            <p>室外气温≥35℃或≤-10℃时，系数 1.2</p>
          </div>
          <h4 className="text-sm font-semibold text-stone-700 mb-2 mt-3">
            海拔附加
          </h4>
          <div className="text-[11px] text-stone-500 space-y-1 leading-relaxed">
            <p>2000~3000m：1.1</p>
            <p>3001~3500m：1.2</p>
            <p>3501~4000m：1.3</p>
          </div>
        </div>
      </div>
    </div>
  );
}

/* ═══════════════════════════════════════════
   水利水电工程勘察收费 Tab
   ═══════════════════════════════════════════ */
function WaterTab() {
  const [amount, setAmount] = useState('');
  const [profIdx, setProfIdx] = useState(0);
  const [complexIdx, setComplexIdx] = useState(1);
  const [adjCoef, setAdjCoef] = useState('1.0');
  const [result, setResult] = useState<ReturnType<
    typeof calcWaterConservancyFee
  > | null>(null);
  const [error, setError] = useState('');

  const profCoef = WATER_PROF_COEFS[profIdx].coef;
  const complexCoef = COMPLEXITY_OPTIONS[complexIdx].coef;

  const handleCalc = useCallback(() => {
    const raw = amount.replace(/[,，\s]/g, '');
    if (!raw) {
      setError('请输入计费额');
      setResult(null);
      return;
    }
    const val = parseFloat(raw);
    if (isNaN(val) || val <= 0) {
      setError('请输入大于0的数字');
      setResult(null);
      return;
    }
    setError('');
    const adj = parseFloat(adjCoef) || 1.0;
    setResult(calcWaterConservancyFee(val, profCoef, complexCoef, adj));
  }, [amount, profCoef, complexCoef, adjCoef]);

  return (
    <div className="flex min-h-full">
      {/* Left: Form + Results */}
      <div className="flex-1 p-6 space-y-4 min-w-0">
        <div className="bg-blue-50 border border-blue-100 rounded-xl px-4 py-2.5 text-xs text-blue-700">
          水利水电工程勘察收费适用于：水库、引调水、河道治理、灌区、水电站、潮汐发电、水土保持等工程
        </div>

        {/* 计费额 */}
        <div>
          <label className="block text-xs font-medium text-stone-500 mb-1.5">
            计费额（概算投资额）
          </label>
          <div className="flex gap-3">
            <div className="flex-1 relative">
              <input
                type="text"
                value={amount}
                onChange={(e) => setAmount(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') handleCalc();
                }}
                placeholder="请输入金额"
                className="w-full bg-stone-50 border border-stone-200 rounded-xl px-4 py-2.5 pr-14 text-sm text-stone-900 outline-none focus:border-indigo-300 focus:ring-2 focus:ring-indigo-50 transition placeholder:text-stone-400"
              />
              <span className="absolute right-4 top-1/2 -translate-y-1/2 text-xs text-stone-400">
                万元
              </span>
            </div>
            <button
              onClick={handleCalc}
              className="shrink-0 bg-indigo-500 hover:bg-indigo-600 text-white px-6 py-2.5 rounded-xl text-sm font-medium transition-colors"
            >
              计算
            </button>
          </div>
          {error && <p className="mt-1.5 text-xs text-red-500">{error}</p>}
        </div>

        {/* 工程类别 */}
        <div>
          <label className="block text-xs font-medium text-stone-500 mb-1.5">
            工程类别（专业调整系数）
          </label>
          <select
            value={profIdx}
            onChange={(e) => setProfIdx(Number(e.target.value))}
            className="w-full bg-stone-50 border border-stone-200 rounded-xl px-4 py-2.5 text-sm text-stone-900 outline-none focus:border-indigo-300 focus:ring-2 focus:ring-indigo-50 transition"
          >
            {WATER_PROF_COEFS.map((c, i) => (
              <option key={i} value={i}>
                {c.name}（系数 {c.coef}）
              </option>
            ))}
          </select>
        </div>

        {/* 复杂程度 */}
        <div>
          <label className="block text-xs font-medium text-stone-500 mb-1.5">
            工程复杂程度
          </label>
          <div className="flex gap-2">
            {COMPLEXITY_OPTIONS.map((c, i) => (
              <button
                key={i}
                onClick={() => setComplexIdx(i)}
                className={`flex-1 px-3 py-2 rounded-lg text-xs font-medium transition ${
                  complexIdx === i
                    ? 'bg-indigo-100 text-indigo-700 border border-indigo-200'
                    : 'bg-stone-50 text-stone-500 border border-stone-200 hover:bg-stone-100'
                }`}
              >
                {c.label}
                <span className="block text-[10px] opacity-60">
                  系数 {c.coef}
                </span>
              </button>
            ))}
          </div>
        </div>

        {/* 附加调整系数 */}
        <div>
          <label className="block text-xs font-medium text-stone-500 mb-1.5">
            附加调整系数（默认1.0）
          </label>
          <input
            type="text"
            value={adjCoef}
            onChange={(e) => setAdjCoef(e.target.value)}
            className="w-full bg-stone-50 border border-stone-200 rounded-xl px-4 py-2.5 text-sm text-stone-900 outline-none focus:border-indigo-300 focus:ring-2 focus:ring-indigo-50 transition placeholder:text-stone-400"
            placeholder="默认1.0，多个系数相加减个数加1"
          />
        </div>

        {/* Results */}
        {result && (
          <div className="bg-white rounded-2xl border border-stone-100 p-5 space-y-3">
            <h4 className="text-sm font-semibold text-stone-700">计算结果</h4>
            <div className="space-y-2 text-sm">
              <div className="flex justify-between py-1.5 border-b border-stone-50">
                <span className="text-stone-500">收费基价（内插法）</span>
                <span className="font-medium text-stone-800">
                  {fmtWan(result.basePrice)} 万元
                </span>
              </div>
              <div className="flex justify-between py-1.5 border-b border-stone-50">
                <span className="text-stone-500">基本勘察收费</span>
                <span className="font-medium text-stone-800">
                  {fmtWan(result.basicFee)} 万元
                </span>
              </div>
              <div className="flex justify-between py-2">
                <span className="text-stone-700 font-medium">
                  勘察收费基准价
                </span>
                <span className="text-lg font-bold text-indigo-600">
                  {fmtWan(result.basicFee)} 万元
                </span>
              </div>
              <div className="flex justify-between py-1.5 text-xs text-stone-400">
                <span>作业准备费（基准价×15%~20%）</span>
                <span>
                  {fmtWan(result.basicFee * 0.15)} ~{' '}
                  {fmtWan(result.basicFee * 0.2)} 万元
                </span>
              </div>
              <div className="flex justify-between py-1.5 text-xs text-stone-400">
                <span>浮动下限（-20%）</span>
                <span>{fmtWan(result.basicFee * 0.8)} 万元</span>
              </div>
              <div className="flex justify-between py-1.5 text-xs text-stone-400">
                <span>浮动上限（+20%）</span>
                <span>{fmtWan(result.basicFee * 1.2)} 万元</span>
              </div>
            </div>
            <div className="flex flex-wrap gap-1.5 mt-2">
              <span className="bg-blue-100 text-blue-700 rounded-lg px-2 py-0.5 text-[10px] font-medium">
                专业系数 {profCoef}
              </span>
              <span className="bg-blue-100 text-blue-700 rounded-lg px-2 py-0.5 text-[10px] font-medium">
                复杂程度 {complexCoef}
              </span>
              {parseFloat(adjCoef) !== 1 && (
                <span className="bg-amber-100 text-amber-700 rounded-lg px-2 py-0.5 text-[10px] font-medium">
                  附加系数 {parseFloat(adjCoef) || 1}
                </span>
              )}
            </div>
          </div>
        )}
      </div>

      {/* Right: Reference */}
      <div className="w-72 shrink-0 border-l border-stone-100 bg-white/50 overflow-y-auto p-5">
        <div className="bg-white rounded-2xl border border-stone-100 p-4">
          <h4 className="text-sm font-semibold text-stone-700 mb-2">
            计算公式
          </h4>
          <div className="text-[11px] text-stone-500 space-y-1.5 leading-relaxed">
            <p>勘察收费 = 基准价 × (1±浮动幅度)</p>
            <p>基准价 = 基本勘察收费 + 其他勘察收费</p>
            <p>
              基本勘察收费 = 基价 × 专业调整系数 × 复杂程度系数 × 附加调整系数
            </p>
          </div>
        </div>
        <div className="bg-white rounded-2xl border border-stone-100 p-4 mt-4">
          <h4 className="text-sm font-semibold text-stone-700 mb-3">
            收费基价表
          </h4>
          <table className="w-full text-xs table-fixed">
            <thead>
              <tr className="border-b border-stone-100">
                <th className="text-left py-1.5 text-stone-400 font-medium">
                  计费额（万元）
                </th>
                <th className="text-right py-1.5 text-stone-400 font-medium">
                  基价（万元）
                </th>
              </tr>
            </thead>
            <tbody>
              {WATER_CONSERVANCY_TABLE.slice(0, 10).map(([amt, fee], i) => (
                <tr key={i} className="border-b border-stone-50 last:border-0">
                  <td className="py-1 text-stone-600">
                    {amt.toLocaleString()}
                  </td>
                  <td className="text-right py-1 text-stone-500">
                    {fee.toLocaleString()}
                  </td>
                </tr>
              ))}
              <tr>
                <td colSpan={2} className="py-1 text-center text-stone-400">
                  {'... 共18档，>2000000万 ×1.7%'}
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
