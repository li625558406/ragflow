import { useCallback, useState } from 'react';

const TIERS = [500, 1000, 5000, 10000, 30000, 50000];

const PROJECTS: Record<
  number,
  { name: string; base: string; rates: number[] | null }
> = {
  1: {
    name: '投资估算编制或审核',
    base: '投资总额',
    rates: [0.8, 0.6, 0.4, 0.2, 0.2, 0.2, 0.2],
  },
  2: {
    name: '方案比选',
    base: '各比选方案造价之和',
    rates: [1.5, 1.3, 1.1, 0.9, 0.7, 0.5, 0.5],
  },
  3: {
    name: '概算编制或审核',
    base: '概算金额',
    rates: [1.5, 1.3, 1.1, 0.9, 0.8, 0.7, 0.6],
  },
  4: {
    name: '模拟清单及其最高投标限价编制或审核',
    base: '最高投标限价',
    rates: [4.5, 4.0, 3.5, 3.0, 2.5, 2.3, 2.0],
  },
  5: {
    name: '最高投标限价（招标控制价）编制或审核',
    base: '最高投标限价（招标控制价）',
    rates: [5.3, 4.8, 4.5, 4.3, 4.0, 3.8, 3.5],
  },
  6: {
    name: '施工图预算编制或审核',
    base: '预算价',
    rates: [5.0, 4.6, 4.2, 4.0, 3.8, 3.5, 3.2],
  },
  7: {
    name: '工程结算编制',
    base: '结算价',
    rates: [4.8, 4.3, 3.8, 3.6, 3.2, 2.9, 2.6],
  },
  8: {
    name: '工程结算审核',
    base: '送审造价',
    rates: [4.5, 4.1, 3.6, 3.0, 2.7, 2.3, 2.0],
  },
  9: { name: '全过程造价咨询', base: '结算价', rates: null },
  10: {
    name: '施工进度款编制或审核',
    base: '合同价',
    rates: [1.6, 1.4, 1.2, 1.0, 0.9, 0.8, 0.7],
  },
  11: {
    name: '工程项目决算',
    base: '决算金额',
    rates: [1.2, 1.0, 0.8, 0.8, 0.8, 0.8, 0.8],
  },
  12: {
    name: '工程造价鉴定',
    base: '鉴定标的',
    rates: [12, 10, 8, 6, 5, 4, 4],
  },
  13: { name: '钢筋工程量精细计算或审核', base: '钢筋重量（吨）', rates: null },
  14: { name: '造价师计时咨询', base: '工日', rates: null },
};

const FULLPROCESS_RATES: Record<string, { label: string; rates: number[] }> = {
  a: { label: '立项阶段起至竣工结算', rates: [12, 12, 12, 10, 9, 8.5, 7] },
  b: { label: '实施阶段起至竣工结算', rates: [10, 9, 8, 7.5, 7, 6.5, 6] },
};

const SETTLE_AUDIT_BASIC = [3.0, 2.8, 2.6, 2.4, 2.2, 2.0, 1.8];

const ONSITE_FEES: Record<string, { label: string; fee: number }> = {
  '1': { label: '一级造价师 / 高级工程师', fee: 35000 },
  '2': { label: '二级造价师 / 中级工程师', fee: 25000 },
  '3': { label: '技术人员', fee: 20000 },
};

const COEFFICIENTS: Record<number, { name: string; coef: number }> = {
  1: { name: '房屋建筑、装配式工程', coef: 1.0 },
  2: { name: '单独发包的装饰工程', coef: 1.2 },
  3: { name: '单独发包的安装工程', coef: 2.0 },
  4: { name: '园林绿化、景观工程', coef: 1.2 },
  5: { name: '古建筑保护修复工程', coef: 3.5 },
  6: { name: '仿古建筑、抗震加固工程', coef: 2.0 },
  7: { name: '公路、市政（不含桥梁、隧道）', coef: 0.7 },
  8: { name: '桥梁、隧道、水利、电力', coef: 0.8 },
  9: { name: '给水厂/污水厂/泵站/垃圾厂', coef: 1.2 },
  10: { name: '机场跑道、城市轨道交通', coef: 0.7 },
  11: { name: '港口工程', coef: 0.8 },
  12: { name: '市政维护、爆破工程', coef: 1.2 },
  13: { name: '其他工程', coef: 1.0 },
};

function calcTiered(amountWan: number, ratesPerMil: number[]): number {
  let fee = 0,
    prev = 0;
  for (let i = 0; i < TIERS.length; i++) {
    if (amountWan <= prev) break;
    fee +=
      (Math.min(amountWan, TIERS[i]) - prev) * 10000 * (ratesPerMil[i] / 1000);
    prev = TIERS[i];
  }
  if (amountWan > TIERS[TIERS.length - 1])
    fee +=
      (amountWan - TIERS[TIERS.length - 1]) * 10000 * (ratesPerMil[6] / 1000);
  return fee;
}

function formatYuan(yuan: number): string {
  return yuan.toLocaleString('zh-CN', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
}

function SelectArrow() {
  return (
    <svg
      className="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 w-4 h-4 text-[#1a1a1a]"
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

// ── Shared result card ──
function ResultCard({
  title,
  total,
  detail,
  coef,
}: {
  title: string;
  total: number;
  detail?: { label: string; value: string }[];
  coef?: number;
}) {
  return (
    <div className="bg-white rounded-2xl border border-[#D4D4D4] p-5">
      <div className="text-xs text-[#1a1a1a] mb-2">{title}</div>
      {detail && detail.length > 0 && (
        <div className="space-y-1 mb-3 text-sm">
          {detail.map((d, i) => (
            <div key={i} className="flex justify-between">
              <span className="text-[#000000]">{d.label}</span>
              <span className="font-medium text-[#000000]">{d.value}</span>
            </div>
          ))}
          <div className="border-t border-[#D4D4D4] pt-1.5" />
        </div>
      )}
      <div className="flex items-baseline gap-1">
        <span className="text-2xl font-bold text-[#000000]">
          {formatYuan(total)}
        </span>
        <span className="text-sm text-[#1a1a1a]">元</span>
      </div>
      <div className="flex gap-5 mt-2 text-[11px] text-[#1a1a1a]">
        <span>浮动下限 -20%：{formatYuan(total * 0.8)} 元</span>
        <span>浮动上限 +20%：{formatYuan(total * 1.2)} 元</span>
      </div>
      {coef && coef !== 1.0 && (
        <p className="mt-2 text-[11px] text-[#1a1a1a]">
          已乘专业工程系数 {coef}
        </p>
      )}
    </div>
  );
}

// ── Right panel: rate table ──
function RateTableCard({ title, rates }: { title: string; rates: number[] }) {
  return (
    <div className="bg-white rounded-2xl border border-[#D4D4D4] p-4">
      <h4 className="text-xs font-semibold text-[#000000] mb-2">{title}</h4>
      <table className="w-full text-[11px] table-fixed">
        <thead>
          <tr className="border-b border-[#D4D4D4]">
            <th className="text-left py-1 text-[#1a1a1a] font-medium">
              分档（万元）
            </th>
            <th className="text-right py-1 text-[#1a1a1a] font-medium w-10">
              费率
            </th>
          </tr>
        </thead>
        <tbody>
          {rates.slice(0, 6).map((rate, i) => {
            const prev = i === 0 ? 0 : TIERS[i - 1];
            return (
              <tr key={i} className="border-b border-[#EAEAEA] last:border-0">
                <td className="py-1 text-[#000000]">
                  {prev} - {TIERS[i]}
                </td>
                <td className="text-right py-1 text-[#000000]">{rate}‰</td>
              </tr>
            );
          })}
          <tr>
            <td className="py-1 text-[#000000]">{TIERS[5]} 以上</td>
            <td className="text-right py-1 text-[#000000]">{rates[6]}‰</td>
          </tr>
        </tbody>
      </table>
    </div>
  );
}

function CoefTableCard() {
  return (
    <div className="bg-white rounded-2xl border border-[#D4D4D4] p-4">
      <h4 className="text-xs font-semibold text-[#000000] mb-2">
        专业工程系数表
      </h4>
      <div className="space-y-0.5">
        {Object.entries(COEFFICIENTS).map(([k, v]) => (
          <div key={k} className="flex justify-between text-[11px] py-0.5">
            <span className="text-[#000000] truncate mr-2" title={v.name}>
              {v.name}
            </span>
            <span className="text-[#000000] shrink-0">{v.coef}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

// ── Main component ──
export default function CostConsultingCalculator() {
  const [projectId, setProjectId] = useState(1);
  const [amount, setAmount] = useState('');
  const [coefId, setCoefId] = useState(1);

  const [auditMode, setAuditMode] = useState<'a' | 'b'>('a');
  const [sentCost, setSentCost] = useState('');
  const [deductAmt, setDeductAmt] = useState('');
  const [addAmt, setAddAmt] = useState('');
  const [auditLevel, setAuditLevel] = useState<1 | 2>(1);

  const [phase, setPhase] = useState<'a' | 'b'>('a');
  const [needOnsite, setNeedOnsite] = useState(false);
  const [onsite1, setOnsite1] = useState({ persons: 0, months: 0 });
  const [onsite2, setOnsite2] = useState({ persons: 0, months: 0 });
  const [onsite3, setOnsite3] = useState({ persons: 0, months: 0 });

  const [resultData, setResultData] = useState<{
    title: string;
    total: number;
    detail: { label: string; value: string }[];
    coef?: number;
  } | null>(null);
  const [error, setError] = useState('');

  const parseNum = (v: string) => parseFloat(v.replace(/[,，\s]/g, ''));
  const coef = COEFFICIENTS[coefId].coef;
  const project = PROJECTS[projectId];
  const needsCoef = ![13, 14].includes(projectId);

  const clearResults = () => {
    setResultData(null);
    setError('');
  };

  const handleCalc = useCallback(() => {
    setError('');
    try {
      if (projectId === 13) {
        const qty = parseNum(amount);
        if (!qty || qty <= 0) {
          setError('请输入钢筋重量');
          return;
        }
        const fee = qty * 15;
        setResultData({
          title: project.name,
          total: fee,
          detail: [
            { label: `${qty} 吨 × 15 元/吨`, value: `${formatYuan(fee)} 元` },
          ],
        });
        return;
      }
      if (projectId === 14) {
        const qty = parseNum(amount);
        if (!qty || qty <= 0) {
          setError('请输入工日数');
          return;
        }
        const fee = qty * 2000;
        setResultData({
          title: project.name,
          total: fee,
          detail: [
            {
              label: `${qty} 工日 × 2,000 元/日`,
              value: `${formatYuan(fee)} 元`,
            },
          ],
        });
        return;
      }
      if (projectId === 8) {
        if (auditMode === 'a') {
          const base = parseNum(sentCost);
          if (!base || base <= 0) {
            setError('请输入送审造价');
            return;
          }
          const basic = calcTiered(base, SETTLE_AUDIT_BASIC) * coef;
          const deduct = parseNum(deductAmt) || 0;
          const add = parseNum(addAmt) || 0;
          const benefitRate = auditLevel === 1 ? 0.05 : 0.1;
          const benefitFee =
            (Math.abs(deduct) + Math.abs(add)) * 10000 * benefitRate;
          setResultData({
            title: `工程结算审核（基本费+效益费）`,
            total: basic + benefitFee,
            coef,
            detail: [
              {
                label: `基本费（含系数 ${coef}）`,
                value: `${formatYuan(basic)} 元`,
              },
              {
                label: `效益费（${auditLevel === 1 ? '5' : '10'}%）`,
                value: `${formatYuan(benefitFee)} 元`,
              },
            ],
          });
        } else {
          const base = parseNum(sentCost);
          if (!base || base <= 0) {
            setError('请输入送审造价');
            return;
          }
          const fee = calcTiered(base, PROJECTS[8].rates!) * coef;
          setResultData({
            title: '工程结算审核（按送审造价）',
            total: fee,
            detail: [],
            coef,
          });
        }
        return;
      }
      if (projectId === 9) {
        const base = parseNum(amount);
        if (!base || base <= 0) {
          setError('请输入结算价');
          return;
        }
        const cfg = FULLPROCESS_RATES[phase];
        const basicFee = calcTiered(base, cfg.rates) * coef;
        const detailArr: { label: string; value: string }[] = [
          {
            label: `基本费（含系数 ${coef}）`,
            value: `${formatYuan(basicFee)} 元`,
          },
        ];
        let onsiteTotal = 0;
        if (needOnsite) {
          for (const [key, info] of Object.entries(ONSITE_FEES)) {
            const os = key === '1' ? onsite1 : key === '2' ? onsite2 : onsite3;
            if (os.persons > 0 && os.months > 0) {
              const sub = os.persons * os.months * info.fee;
              onsiteTotal += sub;
              detailArr.push({
                label: `${info.label}（${os.persons}人×${os.months}月）`,
                value: `${formatYuan(sub)} 元`,
              });
            }
          }
        }
        setResultData({
          title: `全过程造价咨询 · ${cfg.label}`,
          total: basicFee + onsiteTotal,
          coef,
          detail: detailArr,
        });
        return;
      }
      if (!project.rates) {
        setError('该项目暂不支持');
        return;
      }
      const base = parseNum(amount);
      if (!base || base <= 0) {
        setError(`请输入${project.base}`);
        return;
      }
      const fee = calcTiered(base, project.rates) * coef;
      setResultData({
        title: project.name,
        total: fee,
        coef: needsCoef ? coef : undefined,
        detail: [],
      });
    } catch {
      setError('输入数据有误，请检查');
    }
  }, [
    projectId,
    amount,
    coefId,
    auditMode,
    sentCost,
    deductAmt,
    addAmt,
    auditLevel,
    phase,
    needOnsite,
    onsite1,
    onsite2,
    onsite3,
    coef,
    project,
    needsCoef,
  ]);

  // Right panel rate data
  const rightRates = (() => {
    if (projectId === 8)
      return auditMode === 'a'
        ? { title: '结算审核 · 基本费率', rates: SETTLE_AUDIT_BASIC }
        : { title: '结算审核 · 按送审造价', rates: PROJECTS[8].rates! };
    if (projectId === 9) {
      const cfg = FULLPROCESS_RATES[phase];
      return { title: `全过程 · ${cfg.label}`, rates: cfg.rates };
    }
    if (project.rates) return { title: project.name, rates: project.rates };
    return null;
  })();

  return (
    <div className="h-full flex flex-col">
      {/* Title bar */}
      <div className="shrink-0 px-6 pt-5 pb-4 border-b border-[#D4D4D4] bg-white">
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
                d="M2.25 18.75a60.07 60.07 0 0115.797 2.101c.727.198 1.453-.342 1.453-1.096V18.75M3.75 4.5v.75A.75.75 0 013 6h-.75m0 0v-.375c0-.621.504-1.125 1.125-1.125H20.25M2.25 6v9m18-10.5v.75c0 .414.336.75.75.75h.75m-1.5-1.5h.375c.621 0 1.125.504 1.125 1.125v9.75c0 .621-.504 1.125-1.125 1.125h-.375m1.5-1.5H21a.75.75 0 00-.75.75v.75m0 0H3.75m0 0h-.375a1.125 1.125 0 01-1.125-1.125V15m1.5 1.5v-.75A.75.75 0 003 15h-.75M15 10.5a3 3 0 11-6 0 3 3 0 016 0zm3 0h.008v.008H18V10.5zm-12 0h.008v.008H6V10.5z"
              />
            </svg>
          </div>
          <div>
            <h2 className="text-[15px] font-bold text-[#000000]">
              造价咨询服务费计算器
            </h2>
            <p className="text-[11px] text-[#1a1a1a]">
              依据：闽招协[2021]32号 附件2 · 差额定率分档累进法 × 专业工程系数
            </p>
          </div>
        </div>
      </div>

      {/* Left-Right layout */}
      <div className="flex-1 flex min-h-0 overflow-hidden">
        {/* Left: Form + Results */}
        <div className="flex-1 overflow-y-auto p-6 space-y-3 min-w-0">
          {/* ① 咨询项目 */}
          <div>
            <label className="block text-xs font-medium text-[#000000] mb-1.5">
              ① 咨询项目
            </label>
            <div className="relative">
              <select
                value={projectId}
                onChange={(e) => {
                  setProjectId(Number(e.target.value));
                  clearResults();
                }}
                className="w-full bg-[#EAEAEA] border border-[#D4D4D4] rounded-xl px-4 py-2.5 pr-9 text-sm text-[#000000] outline-none focus:border-[#000000] focus:ring-2 focus:ring-[#D4D4D4] transition appearance-none cursor-pointer"
              >
                {Object.entries(PROJECTS).map(([k, v]) => (
                  <option key={k} value={k}>
                    {v.name}
                  </option>
                ))}
              </select>
              <SelectArrow />
            </div>
            <p className="mt-1 text-[11px] text-[#1a1a1a]">
              计费基数：{project.base}
            </p>
          </div>

          {/* ② 通用输入 */}
          {![8, 9].includes(projectId) && (
            <div>
              <label className="block text-xs font-medium text-[#000000] mb-1.5">
                ② 输入{project.base}
              </label>
              <div className="relative">
                <input
                  type="text"
                  value={amount}
                  onChange={(e) => {
                    setAmount(e.target.value);
                    clearResults();
                  }}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter') handleCalc();
                  }}
                  placeholder={`请输入${project.base}`}
                  className="w-full bg-[#EAEAEA] border border-[#D4D4D4] rounded-xl px-4 py-2.5 pr-14 text-sm text-[#000000] outline-none focus:border-[#000000] focus:ring-2 focus:ring-[#D4D4D4] transition placeholder:text-[#A3A3A3]"
                />
                <span className="absolute right-4 top-1/2 -translate-y-1/2 text-xs text-[#1a1a1a]">
                  {projectId === 13 ? '吨' : projectId === 14 ? '工日' : '万元'}
                </span>
              </div>
            </div>
          )}

          {/* ② 工程结算审核 */}
          {projectId === 8 && (
            <div className="space-y-3">
              <div>
                <label className="block text-xs font-medium text-[#000000] mb-1.5">
                  ② 计费方式
                </label>
                <div className="flex gap-2">
                  {(
                    [
                      ['a', '基本费 + 效益费'],
                      ['b', '按送审造价'],
                    ] as const
                  ).map(([k, label]) => (
                    <button
                      key={k}
                      onClick={() => {
                        setAuditMode(k);
                        clearResults();
                      }}
                      className={`flex-1 py-2 rounded-xl text-xs font-medium border transition ${auditMode === k ? 'bg-[#EAEAEA] border-[#D4D4D4] text-[#000000]' : 'bg-[#EAEAEA] border-[#D4D4D4] text-[#000000] hover:border-[#A3A3A3]'}`}
                    >
                      {label}
                    </button>
                  ))}
                </div>
              </div>
              <div>
                <label className="block text-xs font-medium text-[#000000] mb-1.5">
                  送审造价
                </label>
                <div className="relative">
                  <input
                    type="text"
                    value={sentCost}
                    onChange={(e) => {
                      setSentCost(e.target.value);
                      clearResults();
                    }}
                    placeholder="请输入送审造价"
                    className="w-full bg-[#EAEAEA] border border-[#D4D4D4] rounded-xl px-4 py-2.5 pr-14 text-sm text-[#000000] outline-none focus:border-[#000000] focus:ring-2 focus:ring-[#D4D4D4] transition placeholder:text-[#A3A3A3]"
                  />
                  <span className="absolute right-4 top-1/2 -translate-y-1/2 text-xs text-[#1a1a1a]">
                    万元
                  </span>
                </div>
              </div>
              {auditMode === 'a' && (
                <>
                  <div className="grid grid-cols-2 gap-3">
                    <div>
                      <label className="block text-xs font-medium text-[#000000] mb-1.5">
                        核减金额
                      </label>
                      <div className="relative">
                        <input
                          type="text"
                          value={deductAmt}
                          onChange={(e) => {
                            setDeductAmt(e.target.value);
                            clearResults();
                          }}
                          placeholder="无则填0"
                          className="w-full bg-[#EAEAEA] border border-[#D4D4D4] rounded-xl px-4 py-2.5 pr-14 text-sm text-[#000000] outline-none focus:border-[#000000] focus:ring-2 focus:ring-[#D4D4D4] transition placeholder:text-[#A3A3A3]"
                        />
                        <span className="absolute right-4 top-1/2 -translate-y-1/2 text-xs text-[#1a1a1a]">
                          万元
                        </span>
                      </div>
                    </div>
                    <div>
                      <label className="block text-xs font-medium text-[#000000] mb-1.5">
                        核增金额
                      </label>
                      <div className="relative">
                        <input
                          type="text"
                          value={addAmt}
                          onChange={(e) => {
                            setAddAmt(e.target.value);
                            clearResults();
                          }}
                          placeholder="无则填0"
                          className="w-full bg-[#EAEAEA] border border-[#D4D4D4] rounded-xl px-4 py-2.5 pr-14 text-sm text-[#000000] outline-none focus:border-[#000000] focus:ring-2 focus:ring-[#D4D4D4] transition placeholder:text-[#A3A3A3]"
                        />
                        <span className="absolute right-4 top-1/2 -translate-y-1/2 text-xs text-[#1a1a1a]">
                          万元
                        </span>
                      </div>
                    </div>
                  </div>
                  <div>
                    <label className="block text-xs font-medium text-[#000000] mb-1.5">
                      审级
                    </label>
                    <div className="flex gap-2">
                      {(
                        [
                          [1, '一审（5%）'],
                          [2, '二审（10%）'],
                        ] as const
                      ).map(([k, label]) => (
                        <button
                          key={k}
                          onClick={() => {
                            setAuditLevel(k as 1 | 2);
                            clearResults();
                          }}
                          className={`flex-1 py-2 rounded-xl text-xs font-medium border transition ${auditLevel === k ? 'bg-[#EAEAEA] border-[#D4D4D4] text-[#000000]' : 'bg-[#EAEAEA] border-[#D4D4D4] text-[#000000] hover:border-[#A3A3A3]'}`}
                        >
                          {label}
                        </button>
                      ))}
                    </div>
                  </div>
                </>
              )}
            </div>
          )}

          {/* ② 全过程造价咨询 */}
          {projectId === 9 && (
            <div className="space-y-3">
              <div>
                <label className="block text-xs font-medium text-[#000000] mb-1.5">
                  ② 阶段选择
                </label>
                <div className="flex gap-2">
                  {Object.entries(FULLPROCESS_RATES).map(([k, v]) => (
                    <button
                      key={k}
                      onClick={() => {
                        setPhase(k as 'a' | 'b');
                        clearResults();
                      }}
                      className={`flex-1 py-2 rounded-xl text-xs font-medium border transition ${phase === k ? 'bg-[#EAEAEA] border-[#D4D4D4] text-[#000000]' : 'bg-[#EAEAEA] border-[#D4D4D4] text-[#000000] hover:border-[#A3A3A3]'}`}
                    >
                      {v.label}
                    </button>
                  ))}
                </div>
              </div>
              <div>
                <label className="block text-xs font-medium text-[#000000] mb-1.5">
                  结算价
                </label>
                <div className="relative">
                  <input
                    type="text"
                    value={amount}
                    onChange={(e) => {
                      setAmount(e.target.value);
                      clearResults();
                    }}
                    placeholder="请输入结算价"
                    className="w-full bg-[#EAEAEA] border border-[#D4D4D4] rounded-xl px-4 py-2.5 pr-14 text-sm text-[#000000] outline-none focus:border-[#000000] focus:ring-2 focus:ring-[#D4D4D4] transition placeholder:text-[#A3A3A3]"
                  />
                  <span className="absolute right-4 top-1/2 -translate-y-1/2 text-xs text-[#1a1a1a]">
                    万元
                  </span>
                </div>
              </div>
              <div>
                <div className="flex items-center gap-2 mb-2">
                  <label className="text-xs font-medium text-[#000000]">
                    驻场人员增加费
                  </label>
                  <button
                    onClick={() => {
                      setNeedOnsite(!needOnsite);
                      clearResults();
                    }}
                    className={`relative w-8 h-4 rounded-full transition-colors ${needOnsite ? 'bg-[#000000]' : 'bg-[#D4D4D4]'}`}
                  >
                    <span
                      className={`absolute top-[1px] left-[1px] w-3 h-3 bg-white rounded-full shadow-sm transition-transform ${needOnsite ? 'translate-x-4' : ''}`}
                    />
                  </button>
                </div>
                {needOnsite && (
                  <div className="space-y-1.5">
                    {[
                      ['1', onsite1, setOnsite1, ONSITE_FEES['1']] as const,
                      ['2', onsite2, setOnsite2, ONSITE_FEES['2']] as const,
                      ['3', onsite3, setOnsite3, ONSITE_FEES['3']] as const,
                    ].map(([key, os, setOs, info]) => (
                      <div
                        key={key}
                        className="flex items-center gap-2 bg-[#EAEAEA] rounded-lg px-3 py-1.5"
                      >
                        <span className="text-[11px] text-[#000000] flex-1 truncate">
                          {info.label}
                        </span>
                        <input
                          type="number"
                          min={0}
                          value={os.persons}
                          onChange={(e) => {
                            setOs({
                              ...os,
                              persons: parseInt(e.target.value) || 0,
                            });
                            clearResults();
                          }}
                          className="w-14 bg-white border border-[#D4D4D4] rounded-lg px-2 py-1 text-[11px] text-center outline-none focus:border-[#A3A3A3]"
                          placeholder="人"
                        />
                        <span className="text-[11px] text-[#1a1a1a]">人</span>
                        <input
                          type="number"
                          min={0}
                          value={os.months}
                          onChange={(e) => {
                            setOs({
                              ...os,
                              months: parseInt(e.target.value) || 0,
                            });
                            clearResults();
                          }}
                          className="w-14 bg-white border border-[#D4D4D4] rounded-lg px-2 py-1 text-[11px] text-center outline-none focus:border-[#A3A3A3]"
                          placeholder="月"
                        />
                        <span className="text-[11px] text-[#1a1a1a]">月</span>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </div>
          )}

          {/* ③ 工程类别 */}
          {needsCoef && (
            <div>
              <label className="block text-xs font-medium text-[#000000] mb-1.5">
                ③ 工程类别
              </label>
              <div className="relative">
                <select
                  value={coefId}
                  onChange={(e) => {
                    setCoefId(Number(e.target.value));
                    clearResults();
                  }}
                  className="w-full bg-[#EAEAEA] border border-[#D4D4D4] rounded-xl px-4 py-2.5 pr-9 text-sm text-[#000000] outline-none focus:border-[#000000] focus:ring-2 focus:ring-[#D4D4D4] transition appearance-none cursor-pointer"
                >
                  {Object.entries(COEFFICIENTS).map(([k, v]) => (
                    <option key={k} value={k}>
                      {v.name}（系数 {v.coef}）
                    </option>
                  ))}
                </select>
                <SelectArrow />
              </div>
            </div>
          )}

          {/* Error + Calculate */}
          {error && <p className="text-xs text-red-500">{error}</p>}
          <button
            onClick={handleCalc}
            className="w-full bg-[#000000] hover:bg-[#000000] text-white py-2.5 rounded-xl text-sm font-medium transition-colors"
          >
            计算
          </button>

          {/* Result */}
          {resultData && (
            <ResultCard
              title={resultData.title}
              total={resultData.total}
              detail={resultData.detail}
              coef={resultData.coef}
            />
          )}
        </div>

        {/* Right: Reference tables */}
        <div className="w-72 shrink-0 border-l border-[#D4D4D4] bg-[#FFFFFF]/50 overflow-y-auto p-4 space-y-3">
          {rightRates && (
            <RateTableCard title={rightRates.title} rates={rightRates.rates} />
          )}
          {projectId === 13 && (
            <div className="bg-white rounded-2xl border border-[#D4D4D4] p-4">
              <h4 className="text-xs font-semibold text-[#000000] mb-1">
                固定单价
              </h4>
              <p className="text-[11px] text-[#000000]">
                钢筋工程量精细计算或审核：15 元/吨
              </p>
            </div>
          )}
          {projectId === 14 && (
            <div className="bg-white rounded-2xl border border-[#D4D4D4] p-4">
              <h4 className="text-xs font-semibold text-[#000000] mb-1">
                固定单价
              </h4>
              <p className="text-[11px] text-[#000000]">
                造价师计时咨询：2,000 元/工日
              </p>
            </div>
          )}
          {needsCoef && <CoefTableCard />}
        </div>
      </div>
    </div>
  );
}
