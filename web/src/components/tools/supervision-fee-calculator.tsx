import { useCallback, useMemo, useState } from 'react';

/* ═══════════════════════════════════════════════════════════
   建设工程监理费计算器
   依据：发改价格[2007]670号《建设工程监理与相关服务收费管理规定》
   ═══════════════════════════════════════════════════════════ */

// ── 附表二：施工监理服务收费基价表 ──
// [计费额(万元), 收费基价(万元)]
const SUPERVISION_FEE_TABLE: [number, number][] = [
  [500, 16.5],
  [1000, 30.1],
  [3000, 78.1],
  [5000, 120.8],
  [8000, 181.0],
  [10000, 218.6],
  [20000, 393.4],
  [40000, 708.2],
  [60000, 991.4],
  [80000, 1255.8],
  [100000, 1507.0],
  [200000, 2712.5],
  [400000, 4882.6],
  [600000, 6835.6],
  [800000, 8658.4],
  [1000000, 10390.1],
];
const ABOVE_RATE = 0.01039; // 计费额>1000000万元, 乘以1.039%

// ── 附表三：施工监理服务收费专业调整系数 ──
const PROF_GROUPS: {
  group: string;
  items: { name: string; coef: number }[];
}[] = [
  {
    group: '矿山采选工程',
    items: [
      { name: '黑色、有色、黄金、化学、非金属及其他矿采选', coef: 0.9 },
      { name: '选煤及其他煤炭工程', coef: 1.0 },
      { name: '矿井工程、铀矿采选工程', coef: 1.1 },
    ],
  },
  {
    group: '加工冶炼工程',
    items: [
      { name: '冶炼工程', coef: 0.9 },
      { name: '船舶水工工程', coef: 1.0 },
      { name: '各类加工工程', coef: 1.0 },
      { name: '核加工工程', coef: 1.2 },
    ],
  },
  {
    group: '石油化工工程',
    items: [
      { name: '石油工程', coef: 0.9 },
      { name: '化工、石化、化纤、医药工程', coef: 1.0 },
      { name: '核化工工程', coef: 1.2 },
    ],
  },
  {
    group: '水利电力工程',
    items: [
      { name: '风力发电、其他水利工程', coef: 0.9 },
      { name: '火电工程、送变电工程', coef: 1.0 },
      { name: '核能、水电、水库工程', coef: 1.2 },
    ],
  },
  {
    group: '交通运输工程',
    items: [
      { name: '机场场道、助航灯光工程', coef: 0.9 },
      { name: '铁路、公路、城市道路、轻轨及机场空管工程', coef: 1.0 },
      { name: '水运、地铁、桥梁、隧道、索道工程', coef: 1.1 },
    ],
  },
  {
    group: '建筑市政工程',
    items: [
      { name: '园林绿化工程', coef: 0.8 },
      { name: '建筑、人防、市政公用工程', coef: 1.0 },
      { name: '邮政、电信、广播电视工程', coef: 1.0 },
    ],
  },
  {
    group: '农业林业工程',
    items: [
      { name: '农业工程', coef: 0.9 },
      { name: '林业工程', coef: 0.9 },
    ],
  },
];

// 扁平化
const ALL_PROF: { name: string; coef: number; group: string }[] =
  PROF_GROUPS.flatMap((g) => g.items.map((it) => ({ ...it, group: g.group })));

// ── 工程复杂程度调整系数 ──
const COMPLEXITY = [
  { label: 'I级（一般）', coef: 0.85 },
  { label: 'II级（较复杂）', coef: 1.0 },
  { label: 'III级（复杂）', coef: 1.15 },
];

// ── 高程调整系数 ──
const ALTITUDE = [
  { label: '2000m以下', coef: 1.0 },
  { label: '2001~3000m', coef: 1.1 },
  { label: '3001~3500m', coef: 1.2 },
  { label: '3501~4000m', coef: 1.3 },
];

// ── 附表四：人工日费用标准 ──
const DAILY_RATES = [
  { level: '高级专家', min: 1000, max: 1200 },
  { level: '高级专业技术职称', min: 800, max: 1000 },
  { level: '中级专业技术职称', min: 600, max: 800 },
  { level: '初级及以下专业技术职称', min: 300, max: 600 },
];

// ── 辅助函数 ──
function linearInterp(x: number): number {
  if (x <= SUPERVISION_FEE_TABLE[0][0]) return SUPERVISION_FEE_TABLE[0][1];
  for (let i = 1; i < SUPERVISION_FEE_TABLE.length; i++) {
    if (x <= SUPERVISION_FEE_TABLE[i][0]) {
      const [x0, y0] = SUPERVISION_FEE_TABLE[i - 1];
      const [x1, y1] = SUPERVISION_FEE_TABLE[i];
      return y0 + ((y1 - y0) * (x - x0)) / (x1 - x0);
    }
  }
  const [maxX, maxY] = SUPERVISION_FEE_TABLE[SUPERVISION_FEE_TABLE.length - 1];
  return maxY + (x - maxX) * ABOVE_RATE;
}

function fmt(n: number): string {
  return n.toLocaleString('zh-CN', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
}

// ── Tab类型 ──
type TabId = 'supervision' | 'daily';
const TABS: { id: TabId; label: string }[] = [
  { id: 'supervision', label: '施工监理收费' },
  { id: 'daily', label: '人工日费用' },
];

export default function SupervisionFeeCalculator() {
  const [tab, setTab] = useState<TabId>('supervision');

  return (
    <div className="h-full flex flex-col">
      {/* Title bar */}
      <div className="shrink-0 px-6 pt-5 pb-4 border-b border-[rgba(124,92,252,0.06)] bg-white">
        <div className="flex items-center gap-3">
          <div className="w-9 h-9 bg-[#ede9fe] rounded-xl flex items-center justify-center">
            <svg
              className="w-4.5 h-4.5 text-[#7c5cfc]"
              fill="none"
              stroke="currentColor"
              strokeWidth={1.5}
              viewBox="0 0 24 24"
            >
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                d="M9 12.75L11.25 15 15 9.75m-3-7.036A11.959 11.959 0 013.598 6 11.99 11.99 0 003 9.749c0 5.592 3.824 10.29 9 11.623 5.176-1.332 9-6.03 9-11.622 0-1.31-.21-2.571-.598-3.751h-.152c-3.196 0-6.1-1.248-8.25-3.285z"
              />
            </svg>
          </div>
          <div>
            <h2 className="text-[15px] font-normal text-black">
              建设工程监理费计算器
            </h2>
            <p className="text-[11px] text-black/30">
              依据：发改价格[2007]670号
            </p>
          </div>
        </div>
        {/* Sub tabs */}
        <div className="flex gap-1 mt-3">
          {TABS.map((t) => (
            <button
              key={t.id}
              onClick={() => setTab(t.id)}
              className={`px-3 py-1.5 rounded-lg text-xs font-normal transition ${
                tab === t.id
                  ? 'bg-black text-white'
                  : 'bg-black/[0.04] text-black/40 hover:bg-black/[0.06]'
              }`}
            >
              {t.label}
            </button>
          ))}
        </div>
      </div>

      {/* Content */}
      <div className="flex-1 overflow-y-auto">
        {tab === 'supervision' && <SupervisionTab />}
        {tab === 'daily' && <DailyTab />}
      </div>
    </div>
  );
}

/* ═══════════════════════════════════════════
   施工监理服务收费 Tab
   ═══════════════════════════════════════════ */
function SupervisionTab() {
  const [amount, setAmount] = useState('');
  const [profIdx, setProfIdx] = useState(
    ALL_PROF.findIndex((p) => p.name === '建筑、人防、市政公用工程'),
  );
  const [complexIdx, setComplexIdx] = useState(1); // 默认II级
  const [altIdx, setAltIdx] = useState(0); // 默认2000m以下
  const [result, setResult] = useState<{
    basePrice: number;
    benchmark: number;
  } | null>(null);
  const [error, setError] = useState('');

  const profCoef = ALL_PROF[profIdx]?.coef ?? 1.0;
  const complexCoef = COMPLEXITY[complexIdx].coef;
  const altCoef = ALTITUDE[altIdx].coef;

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
    const basePrice = linearInterp(val);
    const benchmark = basePrice * profCoef * complexCoef * altCoef;
    setResult({ basePrice, benchmark });
  }, [amount, profCoef, complexCoef, altCoef]);

  return (
    <div className="flex min-h-full">
      {/* Left: Form + Results */}
      <div className="flex-1 p-6 space-y-4 min-w-0">
        {/* 计费额 */}
        <div>
          <label className="block text-xs font-normal text-black/40 mb-1.5">
            计费额（工程概算投资额）
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
                className="w-full bg-black/[0.02] border border-black/[0.08] rounded-lg px-4 py-2.5 pr-14 text-sm text-black outline-none focus:border-black/20 focus:ring-2 focus:ring-black/[0.04] transition placeholder:text-black/30"
              />
              <span className="absolute right-4 top-1/2 -translate-y-1/2 text-xs text-black/30">
                万元
              </span>
            </div>
            <button
              onClick={handleCalc}
              className="shrink-0 bg-black hover:bg-black/85 text-white px-6 py-2.5 rounded-[50px] text-sm font-normal transition-colors"
            >
              计算
            </button>
          </div>
          {error && <p className="mt-1.5 text-xs text-red-500">{error}</p>}
        </div>

        {/* 专业调整系数 */}
        <div>
          <label className="block text-xs font-normal text-black/40 mb-1.5">
            专业类型
          </label>
          <select
            value={profIdx}
            onChange={(e) => setProfIdx(Number(e.target.value))}
            className="w-full bg-black/[0.02] border border-black/[0.08] rounded-lg px-4 py-2.5 text-sm text-black outline-none focus:border-black/20 focus:ring-2 focus:ring-black/[0.04] transition"
          >
            {PROF_GROUPS.map((g) => (
              <optgroup key={g.group} label={g.group}>
                {g.items.map((it) => {
                  const gi = ALL_PROF.findIndex(
                    (a) => a.name === it.name && a.group === g.group,
                  );
                  return (
                    <option key={gi} value={gi}>
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
          <label className="block text-xs font-normal text-black/40 mb-1.5">
            工程复杂程度
          </label>
          <div className="flex gap-2">
            {COMPLEXITY.map((c, i) => (
              <button
                key={i}
                onClick={() => setComplexIdx(i)}
                className={`flex-1 px-3 py-2 rounded-lg text-xs font-normal transition ${
                  complexIdx === i
                    ? 'bg-black/[0.04] text-indigo-700 border border-indigo-200'
                    : 'bg-black/[0.02] text-black/40 border border-black/[0.08] hover:bg-black/[0.04]'
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

        {/* 高程调整系数 */}
        <div>
          <label className="block text-xs font-normal text-black/40 mb-1.5">
            高程调整系数
          </label>
          <select
            value={altIdx}
            onChange={(e) => setAltIdx(Number(e.target.value))}
            className="w-full bg-black/[0.02] border border-black/[0.08] rounded-lg px-4 py-2.5 text-sm text-black outline-none focus:border-black/20 focus:ring-2 focus:ring-black/[0.04] transition"
          >
            {ALTITUDE.map((a, i) => (
              <option key={i} value={i}>
                {a.label}（系数 {a.coef}）
              </option>
            ))}
          </select>
        </div>

        {/* Results */}
        {result && (
          <div className="bg-white rounded-2xl border border-[rgba(124,92,252,0.06)] p-5 space-y-3">
            <h4 className="text-sm font-normal text-black/70">计算结果</h4>
            <div className="space-y-2 text-sm">
              <div className="flex justify-between py-1.5 border-b border-stone-50">
                <span className="text-black/40">收费基价（内插法）</span>
                <span className="font-normal text-stone-800">
                  {fmt(result.basePrice)} 万元
                </span>
              </div>
              <div className="flex justify-between py-2">
                <span className="text-black/70 font-normal">
                  施工监理服务收费基准价
                </span>
                <span className="text-lg font-normal text-black/60">
                  {fmt(result.benchmark)} 万元
                </span>
              </div>
              <div className="flex justify-between py-1.5 text-xs text-black/30">
                <span>浮动下限（-20%）</span>
                <span>{fmt(result.benchmark * 0.8)} 万元</span>
              </div>
              <div className="flex justify-between py-1.5 text-xs text-black/30">
                <span>浮动上限（+20%）</span>
                <span>{fmt(result.benchmark * 1.2)} 万元</span>
              </div>
            </div>
            {/* 参数回显 */}
            <div className="flex flex-wrap gap-1.5 mt-2">
              <span className="bg-black/[0.04] text-indigo-700 rounded-lg px-2 py-0.5 text-[10px] font-normal">
                专业系数 {profCoef}
              </span>
              <span className="bg-black/[0.04] text-indigo-700 rounded-lg px-2 py-0.5 text-[10px] font-normal">
                复杂程度 {complexCoef}
              </span>
              {altCoef !== 1 && (
                <span className="bg-amber-100 text-amber-700 rounded-lg px-2 py-0.5 text-[10px] font-normal">
                  高程系数 {altCoef}
                </span>
              )}
            </div>
          </div>
        )}
      </div>

      {/* Right: Reference table */}
      <div className="w-72 shrink-0 border-l border-[rgba(124,92,252,0.06)] bg-white/50 overflow-y-auto p-5">
        <div className="bg-white rounded-2xl border border-[rgba(124,92,252,0.06)] p-4">
          <h4 className="text-sm font-normal text-black/70 mb-3">
            收费基价表（附表二）
          </h4>
          <table className="w-full text-xs table-fixed">
            <thead>
              <tr className="border-b border-[rgba(124,92,252,0.06)]">
                <th className="text-left py-1.5 text-black/30 font-normal">
                  计费额（万元）
                </th>
                <th className="text-right py-1.5 text-black/30 font-normal">
                  基价（万元）
                </th>
              </tr>
            </thead>
            <tbody>
              {SUPERVISION_FEE_TABLE.map(([amt, fee], i) => (
                <tr key={i} className="border-b border-stone-50 last:border-0">
                  <td className="py-1 text-black/50">{amt.toLocaleString()}</td>
                  <td className="text-right py-1 text-black/40">
                    {fee.toLocaleString()}
                  </td>
                </tr>
              ))}
              <tr>
                <td className="py-1 text-black/50">{'>1,000,000'}</td>
                <td className="text-right py-1 text-black/40">{'×1.039%'}</td>
              </tr>
            </tbody>
          </table>
          <p className="text-[11px] text-black/30 mt-3">
            注：计费额处于两个数值区间的，采用直线内插法确定基价
          </p>
        </div>

        <div className="bg-white rounded-2xl border border-[rgba(124,92,252,0.06)] p-4 mt-4">
          <h4 className="text-sm font-normal text-black/70 mb-2">计算公式</h4>
          <div className="text-[11px] text-black/40 space-y-1.5 leading-relaxed">
            <p>施工监理收费 = 基准价 × (1±浮动幅度)</p>
            <p>基准价 = 基价 × 专业调整系数 × 复杂程度系数 × 高程调整系数</p>
            <p>浮动幅度：上下 20%</p>
          </div>
        </div>
      </div>
    </div>
  );
}

/* ═══════════════════════════════════════════
   人工日费用 Tab
   ═══════════════════════════════════════════ */
function DailyTab() {
  const [rows, setRows] = useState<
    { level: string; days: string; rate: string }[]
  >([
    { level: '高级专家', days: '', rate: '1100' },
    { level: '高级专业技术职称', days: '', rate: '900' },
    { level: '中级专业技术职称', days: '', rate: '700' },
    { level: '初级及以下专业技术职称', days: '', rate: '450' },
  ]);

  const updateRow = (idx: number, field: 'days' | 'rate', val: string) => {
    setRows((prev) =>
      prev.map((r, i) => (i === idx ? { ...r, [field]: val } : r)),
    );
  };

  const total = useMemo(() => {
    return rows.reduce((sum, r) => {
      const d = parseFloat(r.days) || 0;
      const rate = parseFloat(r.rate) || 0;
      return sum + d * rate;
    }, 0);
  }, [rows]);

  return (
    <div className="flex min-h-full">
      <div className="flex-1 p-6 space-y-4 min-w-0">
        <div className="bg-amber-50 border border-amber-100 rounded-lg px-4 py-2.5 text-xs text-amber-700">
          适用于勘察、设计、保修等其他阶段的相关服务，以及短期服务的人工费用计算
        </div>

        <div className="bg-white rounded-2xl border border-[rgba(124,92,252,0.06)] p-5">
          <h4 className="text-sm font-normal text-black/70 mb-4">
            人工日费用计算
          </h4>
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-[rgba(124,92,252,0.06)]">
                <th className="text-left py-2 text-xs text-black/30 font-normal">
                  人员级别
                </th>
                <th className="text-center py-2 text-xs text-black/30 font-normal w-24">
                  工日数
                </th>
                <th className="text-center py-2 text-xs text-black/30 font-normal w-28">
                  单价（元/日）
                </th>
                <th className="text-right py-2 text-xs text-black/30 font-normal w-28">
                  小计（元）
                </th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r, i) => {
                const days = parseFloat(r.days) || 0;
                const rate = parseFloat(r.rate) || 0;
                const sub = days * rate;
                const ref = DAILY_RATES.find((d) => d.level === r.level);
                return (
                  <tr
                    key={i}
                    className="border-b border-stone-50 last:border-0"
                  >
                    <td className="py-2">
                      <span className="text-black/70 text-xs">{r.level}</span>
                      {ref && (
                        <span className="block text-[10px] text-black/30">
                          {ref.min}~{ref.max}元/日
                        </span>
                      )}
                    </td>
                    <td className="py-2 px-1">
                      <input
                        type="text"
                        value={r.days}
                        onChange={(e) => updateRow(i, 'days', e.target.value)}
                        placeholder="0"
                        className="w-full bg-black/[0.02] border border-black/[0.08] rounded-lg px-2 py-1.5 text-xs text-center text-black outline-none focus:border-black/20"
                      />
                    </td>
                    <td className="py-2 px-1">
                      <input
                        type="text"
                        value={r.rate}
                        onChange={(e) => updateRow(i, 'rate', e.target.value)}
                        className="w-full bg-black/[0.02] border border-black/[0.08] rounded-lg px-2 py-1.5 text-xs text-center text-black outline-none focus:border-black/20"
                      />
                    </td>
                    <td className="py-2 text-right">
                      <span className="text-xs text-black/50">
                        {sub > 0 ? fmt(sub) : '-'}
                      </span>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>

          <div className="mt-4 pt-3 border-t border-[rgba(124,92,252,0.06)] flex justify-between items-center">
            <span className="text-sm font-normal text-black/70">合计</span>
            <span className="text-lg font-normal text-black/60">
              {total > 0 ? `${fmt(total)} 元` : '-'}
            </span>
          </div>
        </div>
      </div>

      {/* Right: Reference */}
      <div className="w-72 shrink-0 border-l border-[rgba(124,92,252,0.06)] bg-white/50 overflow-y-auto p-5">
        <div className="bg-white rounded-2xl border border-[rgba(124,92,252,0.06)] p-4">
          <h4 className="text-sm font-normal text-black/70 mb-3">
            人工日费用标准（附表四）
          </h4>
          <table className="w-full text-xs">
            <thead>
              <tr className="border-b border-[rgba(124,92,252,0.06)]">
                <th className="text-left py-1.5 text-black/30 font-normal">
                  人员职级
                </th>
                <th className="text-right py-1.5 text-black/30 font-normal">
                  标准（元/日）
                </th>
              </tr>
            </thead>
            <tbody>
              {DAILY_RATES.map((d, i) => (
                <tr key={i} className="border-b border-stone-50 last:border-0">
                  <td className="py-1.5 text-black/50">{d.level}</td>
                  <td className="text-right py-1.5 text-black/40">
                    {d.min}~{d.max}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="text-[11px] text-black/30 mt-3">
            注：本表适用于提供短期服务的人工费用标准
          </p>
        </div>

        <div className="bg-white rounded-2xl border border-[rgba(124,92,252,0.06)] p-4 mt-4">
          <h4 className="text-sm font-normal text-black/70 mb-2">说明</h4>
          <div className="text-[11px] text-black/40 space-y-1.5 leading-relaxed">
            <p>
              其他阶段（勘察、设计、保修等）的相关服务收费，按工作所需工日和附表四标准计算。
            </p>
            <p>
              施工监理服务中的部分工作单独发包的，按工作量比例计算，其中质量控制和安全生产监督管理不宜低于70%。
            </p>
          </div>
        </div>
      </div>
    </div>
  );
}
