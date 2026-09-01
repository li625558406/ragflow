import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { usePermission } from '@/hooks/use-permission';
import {
  adjustPayslip,
  calcSalary,
  fetchMyPayslip,
  fetchPayslips,
  listSalaryProfiles,
  publishSalary,
  trialSalary,
  upsertSalaryProfile,
} from '@/services/hr-service';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Banknote, Calculator, IdCard, ScrollText, Wrench } from 'lucide-react';
import { Fragment, useState } from 'react';
import type {
  Payslip,
  SalaryFailedItem,
  SalaryProfile,
  SalaryTrialItem,
} from './hr-types';

function fmt(n: number | null | undefined) {
  return Number(n ?? 0).toFixed(2);
}

function todayMonth() {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`;
}

const PAYSLIP_BADGE: Record<Payslip['status'], string> = {
  draft: 'bg-gray-100 text-gray-500 border-gray-200',
  published: 'bg-emerald-50 text-emerald-600 border-emerald-200',
};

const PAYSLIP_STATUS_LABEL: Record<Payslip['status'], string> = {
  draft: '草稿',
  published: '已发布',
};

// ── 金额明细行 ──

function MoneyRow({ label, value }: { label: string; value: number }) {
  return (
    <div className="flex justify-between text-sm text-[#64748B]">
      <span>{label}</span>
      <span>¥{fmt(value)}</span>
    </div>
  );
}

// ── 员工区：我的工资条 ──

function MyPayslipCard({ month }: { month: string }) {
  const { data, isLoading } = useQuery({
    queryKey: ['hr-salary-my', month],
    queryFn: () => fetchMyPayslip(month),
  });
  const p = data?.payslip ?? null;
  const deductionTotal = p
    ? p.attendance_deduction +
      p.social_insurance +
      p.housing_fund +
      p.income_tax
    : 0;
  return (
    <div className="rounded-xl border border-[#E2E8F0] bg-white p-5 shadow-sm">
      <div className="mb-3 flex items-center gap-2 text-sm font-medium text-[#0F172A]">
        <Banknote className="size-4" /> 我的工资条（{month}）
      </div>
      {isLoading ? (
        <div className="text-sm text-[#94A3B8]">加载中…</div>
      ) : !p ? (
        <div className="text-sm text-[#94A3B8]">该月工资条尚未发布</div>
      ) : (
        <>
          <div className="grid gap-3 sm:grid-cols-2">
            <div className="rounded-lg border border-[#F1F5F9] p-3">
              <div className="mb-1 text-xs text-[#94A3B8]">应发</div>
              <MoneyRow label="基本工资" value={p.base_salary} />
              <MoneyRow label="津贴" value={p.allowances} />
              <MoneyRow label="加班费" value={p.overtime_pay} />
              <div className="mt-1 flex justify-between border-t border-[#F1F5F9] pt-1 text-sm text-[#0F172A]">
                <span>应发合计</span>
                <span className="font-medium">¥{fmt(p.gross_pay)}</span>
              </div>
            </div>
            <div className="rounded-lg border border-[#F1F5F9] p-3">
              <div className="mb-1 text-xs text-[#94A3B8]">扣款</div>
              <MoneyRow label="考勤扣款" value={p.attendance_deduction} />
              <MoneyRow label="社保" value={p.social_insurance} />
              <MoneyRow label="公积金" value={p.housing_fund} />
              <MoneyRow label="个税" value={p.income_tax} />
              <div className="mt-1 flex justify-between border-t border-[#F1F5F9] pt-1 text-sm text-[#0F172A]">
                <span>扣款合计</span>
                <span className="font-medium">¥{fmt(deductionTotal)}</span>
              </div>
            </div>
          </div>
          <div className="mt-3 flex items-end justify-between border-t border-[#F1F5F9] pt-3">
            <div className="text-xs text-[#94A3B8]">
              出勤 {p.attend_days} 天 · 迟到 {p.late_count} 次（
              {p.late_minutes} 分）· 旷工 {p.absent_days} 天 · 加班{' '}
              {p.overtime_hours} 小时
            </div>
            <div className="text-right">
              <div className="text-xs text-[#94A3B8]">实发</div>
              <div className="text-2xl font-semibold text-[#1a66fb]">
                ¥{fmt(p.net_pay)}
              </div>
            </div>
          </div>
        </>
      )}
    </div>
  );
}

// ── HR：薪资档案编辑表单 ──

const NUM_FIELDS: {
  key:
    | 'base_salary'
    | 'post_allowance'
    | 'meal_allowance'
    | 'transport_allowance'
    | 'social_base'
    | 'fund_base'
    | 'special_deduction';
  label: string;
}[] = [
  { key: 'base_salary', label: '基本工资' },
  { key: 'post_allowance', label: '岗位津贴' },
  { key: 'meal_allowance', label: '餐补' },
  { key: 'transport_allowance', label: '交通补贴' },
  { key: 'social_base', label: '社保基数' },
  { key: 'fund_base', label: '公积金基数' },
  { key: 'special_deduction', label: '专项扣除' },
];

const RATE_FIELDS: {
  key: 'social_rate' | 'fund_rate';
  label: string;
}[] = [
  { key: 'social_rate', label: '社保费率' },
  { key: 'fund_rate', label: '公积金费率' },
];

function ProfileEditForm({
  profile,
  onDone,
}: {
  profile: SalaryProfile;
  onDone: () => void;
}) {
  const [values, setValues] = useState<Record<string, string>>(() => {
    const init: Record<string, string> = {};
    for (const f of NUM_FIELDS) init[f.key] = String(profile[f.key] ?? 0);
    for (const f of RATE_FIELDS)
      init[f.key] =
        profile[f.key] === null || profile[f.key] === undefined
          ? ''
          : String(profile[f.key]);
    return init;
  });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  const save = async () => {
    if (busy) return;
    const payload: Parameters<typeof upsertSalaryProfile>[0] = {
      employee_id: profile.employee_id,
      base_salary: 0,
      post_allowance: 0,
      meal_allowance: 0,
      transport_allowance: 0,
      social_base: 0,
      fund_base: 0,
      special_deduction: 0,
      social_rate: null,
      fund_rate: null,
    };
    // 数值字段：非负校验；空串且原值非 0 时拦截，避免误清金额静默存 0
    for (const f of NUM_FIELDS) {
      const raw = (values[f.key] ?? '').trim();
      if (raw === '') {
        if (Number(profile[f.key] ?? 0) !== 0) {
          setError(`${f.label}请输入金额（0 表示无）`);
          return;
        }
        payload[f.key] = 0;
        continue;
      }
      const v = Number(raw);
      if (!Number.isFinite(v) || v < 0) {
        setError(`${f.label}必须是不小于 0 的数字`);
        return;
      }
      payload[f.key] = v;
    }
    // 费率：可留空（null = 走全局），填了必须在 0 ~ 0.3
    for (const f of RATE_FIELDS) {
      const raw = (values[f.key] ?? '').trim();
      if (raw === '') {
        payload[f.key] = null;
        continue;
      }
      const v = Number(raw);
      if (!Number.isFinite(v) || v < 0 || v > 0.3) {
        setError(`${f.label}需在 0 ~ 0.3 之间（留空表示走全局费率）`);
        return;
      }
      payload[f.key] = v;
    }
    setBusy(true);
    setError('');
    try {
      await upsertSalaryProfile(payload);
      onDone();
    } catch (e) {
      setError(e instanceof Error ? e.message : '保存失败');
    } finally {
      setBusy(false);
    }
  };

  return (
    <div>
      <div className="mb-2 text-xs font-medium text-[#0F172A]">
        编辑档案：{profile.nickname || profile.employee_id}（
        {profile.emp_no || '无工号'}）
      </div>
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
        {[...NUM_FIELDS, ...RATE_FIELDS].map((f) => (
          <div key={f.key}>
            <div className="mb-1 text-[10px] text-[#94A3B8]">{f.label}</div>
            <Input
              type="number"
              min={0}
              step="any"
              value={values[f.key]}
              onChange={(e) =>
                setValues((prev) => ({ ...prev, [f.key]: e.target.value }))
              }
              className="h-8 text-xs"
            />
          </div>
        ))}
      </div>
      <div className="mt-2 flex flex-wrap items-center gap-2">
        <Button
          size="sm"
          onClick={save}
          disabled={busy}
          className="bg-[#1a66fb] text-white hover:bg-[#1554d6]"
        >
          保存
        </Button>
        <Button size="sm" variant="outline" onClick={onDone} disabled={busy}>
          取消
        </Button>
        <span className="text-[10px] text-[#94A3B8]">
          费率留空表示使用全局费率（范围 0 ~ 0.3）
        </span>
      </div>
      {error && <div className="mt-1 text-xs text-red-500">{error}</div>}
    </div>
  );
}

// ── HR：薪资档案列表 ──

function SalaryProfileList() {
  const qc = useQueryClient();
  const [keyword, setKeyword] = useState('');
  const [editingId, setEditingId] = useState('');
  const profiles = useQuery({
    queryKey: ['hr-salary-profiles', keyword],
    queryFn: () => listSalaryProfiles(keyword.trim() || undefined),
  });
  const list = profiles.data?.list ?? [];
  return (
    <div className="rounded-xl border border-[#E2E8F0] bg-white p-5 shadow-sm">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2 text-sm font-medium text-[#0F172A]">
          <IdCard className="size-4" /> 薪资档案（{profiles.data?.total ?? 0}）
        </div>
        <Input
          value={keyword}
          onChange={(e) => setKeyword(e.target.value)}
          placeholder="搜索昵称/工号"
          className="h-8 w-48 text-xs"
        />
      </div>
      {list.length === 0 ? (
        <div className="text-sm text-[#94A3B8]">暂无薪资档案</div>
      ) : (
        <div className="max-h-80 overflow-auto rounded-lg border border-[#F1F5F9]">
          <table className="w-full text-xs">
            <thead className="bg-[#F8FAFC] text-[#64748B]">
              <tr>
                <th className="px-2 py-1.5 text-left">昵称</th>
                <th className="px-2 py-1.5 text-left">工号</th>
                <th className="px-2 py-1.5 text-right">基本工资</th>
                <th className="px-2 py-1.5 text-right">社保基数</th>
                <th className="px-2 py-1.5 text-right">公积金基数</th>
                <th className="px-2 py-1.5 text-center">操作</th>
              </tr>
            </thead>
            <tbody>
              {list.map((p) => (
                <Fragment key={p.employee_id}>
                  <tr className="border-t border-[#F8FAFC]">
                    <td className="px-2 py-1.5 text-left text-[#0F172A]">
                      {p.nickname || '—'}
                    </td>
                    <td className="px-2 py-1.5 text-left text-[#0F172A]">
                      {p.emp_no || '—'}
                    </td>
                    <td className="px-2 py-1.5 text-right text-[#0F172A]">
                      ¥{fmt(p.base_salary)}
                    </td>
                    <td className="px-2 py-1.5 text-right text-[#0F172A]">
                      ¥{fmt(p.social_base)}
                    </td>
                    <td className="px-2 py-1.5 text-right text-[#0F172A]">
                      ¥{fmt(p.fund_base)}
                    </td>
                    <td className="px-2 py-1.5 text-center">
                      <button
                        onClick={() =>
                          setEditingId(
                            editingId === p.employee_id ? '' : p.employee_id,
                          )
                        }
                        className="text-[#1a66fb] hover:underline"
                      >
                        {editingId === p.employee_id ? '收起' : '编辑'}
                      </button>
                    </td>
                  </tr>
                  {editingId === p.employee_id && (
                    <tr className="border-t border-[#F8FAFC]">
                      <td colSpan={6} className="bg-[#F8FAFC]/60 px-3 py-3">
                        <ProfileEditForm
                          profile={p}
                          onDone={() => {
                            setEditingId('');
                            qc.invalidateQueries({
                              queryKey: ['hr-salary-profiles'],
                            });
                          }}
                        />
                      </td>
                    </tr>
                  )}
                </Fragment>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

// ── HR：月度操作（试算 / 核算入库 / 发布）──

function MonthlyOps({ month }: { month: string }) {
  const qc = useQueryClient();
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [calcFailed, setCalcFailed] = useState<SalaryFailedItem[]>([]);
  const [trial, setTrial] = useState<SalaryTrialItem[] | null>(null);

  // 成功后统一失效：工资单列表 + 员工我的工资条
  const invalidatePayslips = () => {
    qc.invalidateQueries({ queryKey: ['hr-payslips'] });
    qc.invalidateQueries({ queryKey: ['hr-salary-my'] });
  };

  const doTrial = async () => {
    if (busy) return;
    setBusy(true);
    setMsg(null);
    setCalcFailed([]);
    try {
      const r = await trialSalary(month);
      // 后端契约：失败员工内嵌在 list 中（ok=false + reason），无独立 failed 数组
      const list = r.list ?? [];
      const failedCount = list.filter((t) => !t.ok).length;
      setTrial(list);
      setMsg({
        ok: failedCount === 0,
        text: failedCount
          ? `试算完成：成功 ${list.length - failedCount} 人 / 失败 ${failedCount} 人（见红字）`
          : `试算完成：成功 ${list.length} 人`,
      });
    } catch (e) {
      setMsg({ ok: false, text: e instanceof Error ? e.message : '试算失败' });
    } finally {
      setBusy(false);
    }
  };

  const doCalc = async () => {
    if (busy) return;
    if (
      !window.confirm(
        `确认核算 ${month} 全员工资并入库？将覆盖该月未发布的草稿。`,
      )
    ) {
      return;
    }
    setBusy(true);
    setMsg(null);
    setCalcFailed([]);
    try {
      const r = await calcSalary(month);
      const failed = r.failed ?? [];
      setCalcFailed(failed);
      setMsg({
        ok: !failed.length,
        text: failed.length
          ? `核算完成：成功 ${r.ok ?? 0} 人，失败 ${failed.length} 人（见红字）`
          : `核算完成：成功 ${r.ok ?? 0} 人，已生成草稿`,
      });
      invalidatePayslips();
    } catch (e) {
      setMsg({ ok: false, text: e instanceof Error ? e.message : '核算失败' });
    } finally {
      setBusy(false);
    }
  };

  const doPublish = async () => {
    if (busy) return;
    if (
      !window.confirm(
        `确认发布 ${month} 工资条？发布后员工立即可见，已发布的工资单不可覆盖。`,
      )
    ) {
      return;
    }
    setBusy(true);
    setMsg(null);
    setCalcFailed([]);
    try {
      const r = await publishSalary(month);
      if ((r.published ?? 0) === 0) {
        setMsg({ ok: false, text: '该月没有待发布的工资条' });
      } else {
        setMsg({ ok: true, text: `已发布 ${r.published} 条工资条` });
        invalidatePayslips();
      }
    } catch (e) {
      setMsg({ ok: false, text: e instanceof Error ? e.message : '发布失败' });
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="rounded-xl border border-[#E2E8F0] bg-white p-5 shadow-sm">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2 text-sm font-medium text-[#0F172A]">
          <Calculator className="size-4" /> 月度核算（{month}）
        </div>
        <div className="flex gap-2">
          <Button
            size="sm"
            onClick={doTrial}
            disabled={busy}
            className="bg-[#1a66fb] text-white hover:bg-[#1554d6]"
          >
            试算
          </Button>
          <Button size="sm" variant="outline" onClick={doCalc} disabled={busy}>
            核算入库
          </Button>
          <Button
            size="sm"
            variant="outline"
            onClick={doPublish}
            disabled={busy}
            className="border-emerald-200 text-emerald-600 hover:bg-emerald-50"
          >
            发布工资条
          </Button>
        </div>
      </div>
      {msg && (
        <div
          className={`mb-2 text-sm ${msg.ok ? 'text-[#1a66fb]' : 'text-red-500'}`}
        >
          {msg.text}
        </div>
      )}
      {calcFailed.map((f) => (
        <div key={f.employee_id} className="text-xs text-red-500">
          {f.employee_id}：{f.reason}
        </div>
      ))}
      {trial && (
        <div className="overflow-auto rounded-lg border border-[#F1F5F9]">
          <table className="w-full text-xs">
            <thead className="bg-[#F8FAFC] text-[#64748B]">
              <tr>
                <th className="px-2 py-1.5 text-left">员工</th>
                <th className="px-2 py-1.5 text-right">应发</th>
                <th className="px-2 py-1.5 text-right">扣款合计</th>
                <th className="px-2 py-1.5 text-right">实发</th>
              </tr>
            </thead>
            <tbody>
              {trial.map((t) =>
                t.ok ? (
                  <tr key={t.employee_id} className="border-t border-[#F8FAFC]">
                    <td className="px-2 py-1.5 text-left text-[#0F172A]">
                      {t.nickname || t.employee_id}
                    </td>
                    <td className="px-2 py-1.5 text-right text-[#0F172A]">
                      ¥{fmt(t.gross_pay)}
                    </td>
                    <td className="px-2 py-1.5 text-right text-[#0F172A]">
                      ¥
                      {fmt(
                        t.attendance_deduction +
                          t.social_insurance +
                          t.housing_fund +
                          t.income_tax,
                      )}
                    </td>
                    <td className="px-2 py-1.5 text-right font-medium text-[#1a66fb]">
                      ¥{fmt(t.net_pay)}
                    </td>
                  </tr>
                ) : (
                  <tr key={t.employee_id} className="border-t border-[#F8FAFC]">
                    <td
                      colSpan={4}
                      className="px-2 py-1.5 text-left text-red-500"
                    >
                      {t.nickname || t.employee_id}（{t.emp_no || '无工号'}）：
                      {t.reason || '核算失败'}
                    </td>
                  </tr>
                ),
              )}
              {trial.length === 0 && (
                <tr>
                  <td
                    colSpan={4}
                    className="px-2 py-2 text-center text-[#94A3B8]"
                  >
                    该月无可试算的员工
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

// ── HR：工资单手工调整 ──

// 可调整的扣款项（与后端 /hr/salary/payslip/<pid>/adjust 允许字段对齐）
const ADJUST_FIELDS: {
  key:
    | 'attendance_deduction'
    | 'social_insurance'
    | 'housing_fund'
    | 'income_tax';
  label: string;
}[] = [
  { key: 'attendance_deduction', label: '考勤扣款' },
  { key: 'social_insurance', label: '社保' },
  { key: 'housing_fund', label: '公积金' },
  { key: 'income_tax', label: '个税' },
];

function PayslipAdjustForm({
  payslip,
  onDone,
}: {
  payslip: Payslip;
  onDone: () => void;
}) {
  const [field, setField] = useState<string>(ADJUST_FIELDS[0].key);
  const [newValue, setNewValue] = useState('');
  const [reason, setReason] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [okMsg, setOkMsg] = useState('');
  const qc = useQueryClient();

  const submit = async () => {
    if (busy) return;
    const raw = (newValue ?? '').trim();
    const v = Number(raw);
    if (raw === '' || !Number.isFinite(v) || v < 0) {
      setError('新金额必须是不小于 0 的数字');
      return;
    }
    if (!reason.trim()) {
      setError('请填写调整原因');
      return;
    }
    setBusy(true);
    setError('');
    setOkMsg('');
    try {
      const r = await adjustPayslip(payslip.id, field, v, reason.trim());
      qc.invalidateQueries({ queryKey: ['hr-payslips'] });
      setOkMsg(
        r?.voucher_stale
          ? '已调整，实发已重算；该月发放凭证已标记过期，请到报表页重新生成'
          : '已调整，实发已重算',
      );
      setNewValue('');
      setReason('');
    } catch (e) {
      setError(e instanceof Error ? e.message : '调整失败');
    } finally {
      setBusy(false);
    }
  };

  return (
    <div>
      <div className="mb-2 text-xs font-medium text-[#0F172A]">
        手工调整：{payslip.nickname || payslip.employee_id}
        {payslip.emp_no ? `（${payslip.emp_no}）` : ''} · {payslip.month}
      </div>
      <div className="grid gap-2 sm:grid-cols-3">
        <div>
          <div className="mb-1 text-[10px] text-[#94A3B8]">调整字段</div>
          <select
            value={field}
            onChange={(e) => setField(e.target.value)}
            className="h-8 w-full rounded-md border border-[#E2E8F0] bg-white px-2 text-xs text-[#0F172A]"
          >
            {ADJUST_FIELDS.map((f) => (
              <option key={f.key} value={f.key}>
                {f.label}
              </option>
            ))}
          </select>
        </div>
        <div>
          <div className="mb-1 text-[10px] text-[#94A3B8]">新金额</div>
          <Input
            type="number"
            min={0}
            step="any"
            value={newValue}
            onChange={(e) => setNewValue(e.target.value)}
            className="h-8 text-xs"
          />
        </div>
        <div>
          <div className="mb-1 text-[10px] text-[#94A3B8]">
            调整原因（必填）
          </div>
          <Input
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            placeholder="如：迟到补扣 / 社保基数修正"
            className="h-8 text-xs"
          />
        </div>
      </div>
      <div className="mt-2 flex flex-wrap items-center gap-2">
        <Button
          size="sm"
          onClick={submit}
          disabled={busy}
          className="bg-[#1a66fb] text-white hover:bg-[#1554d6]"
        >
          提交调整
        </Button>
        <Button size="sm" variant="outline" onClick={onDone} disabled={busy}>
          收起
        </Button>
        <span className="text-[10px] text-[#94A3B8]">
          仅已发布工资单可调整，调整将重算实发并记录日志
        </span>
      </div>
      {error && <div className="mt-1 text-xs text-red-500">{error}</div>}
      {okMsg && <div className="mt-1 text-xs text-[#1a66fb]">{okMsg}</div>}
    </div>
  );
}

// ── HR：工资单列表 ──

function PayslipsList({ month }: { month: string }) {
  const qc = useQueryClient();
  const [adjustingId, setAdjustingId] = useState('');
  const { data } = useQuery({
    queryKey: ['hr-payslips', month],
    queryFn: () => fetchPayslips(month),
  });
  const list = data?.list ?? [];
  return (
    <div className="rounded-xl border border-[#E2E8F0] bg-white p-5 shadow-sm">
      <div className="mb-3 flex items-center gap-2 text-sm font-medium text-[#0F172A]">
        <ScrollText className="size-4" /> 工资单列表（{month}，共{' '}
        {data?.total ?? 0} 条）
      </div>
      {list.length === 0 ? (
        <div className="text-sm text-[#94A3B8]">
          该月暂无工资单，请先执行「核算入库」生成草稿
        </div>
      ) : (
        <div className="max-h-72 overflow-auto rounded-lg border border-[#F1F5F9]">
          <table className="w-full text-xs">
            <thead className="bg-[#F8FAFC] text-[#64748B]">
              <tr>
                <th className="px-2 py-1.5 text-left">昵称</th>
                <th className="px-2 py-1.5 text-left">工号</th>
                <th className="px-2 py-1.5 text-right">应发</th>
                <th className="px-2 py-1.5 text-right">实发</th>
                <th className="px-2 py-1.5 text-center">状态</th>
                <th className="px-2 py-1.5 text-center">操作</th>
              </tr>
            </thead>
            <tbody>
              {list.map((p) => (
                <Fragment key={p.id}>
                  <tr className="border-t border-[#F8FAFC]">
                    <td className="px-2 py-1.5 text-left text-[#0F172A]">
                      {p.nickname || '—'}
                    </td>
                    <td className="px-2 py-1.5 text-left text-[#0F172A]">
                      {p.emp_no || '—'}
                    </td>
                    <td className="px-2 py-1.5 text-right text-[#0F172A]">
                      ¥{fmt(p.gross_pay)}
                    </td>
                    <td className="px-2 py-1.5 text-right text-[#0F172A]">
                      ¥{fmt(p.net_pay)}
                    </td>
                    <td className="px-2 py-1.5 text-center">
                      <span
                        className={`inline-block rounded-full border px-2 py-0.5 ${PAYSLIP_BADGE[p.status]}`}
                      >
                        {PAYSLIP_STATUS_LABEL[p.status]}
                      </span>
                    </td>
                    <td className="px-2 py-1.5 text-center">
                      {p.status === 'published' ? (
                        <button
                          onClick={() =>
                            setAdjustingId(adjustingId === p.id ? '' : p.id)
                          }
                          className="text-[#1a66fb] hover:underline"
                        >
                          {adjustingId === p.id ? '收起' : '调整'}
                        </button>
                      ) : (
                        <span className="text-[#CBD5E1]">—</span>
                      )}
                    </td>
                  </tr>
                  {adjustingId === p.id && (
                    <tr className="border-t border-[#F8FAFC]">
                      <td colSpan={6} className="bg-[#F8FAFC]/60 px-3 py-3">
                        <PayslipAdjustForm
                          payslip={p}
                          onDone={() => {
                            setAdjustingId('');
                            qc.invalidateQueries({
                              queryKey: ['hr-payslips'],
                            });
                          }}
                        />
                      </td>
                    </tr>
                  )}
                </Fragment>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

// ── HR 区 ──

function HrSection() {
  const [month, setMonth] = useState(todayMonth());
  // 月份输入可能为空或非法（YYYY-MM 之外），回退到当月，避免用脏值发请求
  const safeMonth = /^\d{4}-\d{2}$/.test(month) ? month : todayMonth();
  return (
    <div className="space-y-4">
      <div className="flex items-center gap-2 text-sm font-medium text-[#0F172A]">
        <Wrench className="size-4" /> HR 薪资管理
      </div>
      <div className="flex items-center gap-2 text-xs text-[#94A3B8]">
        <span>操作月份</span>
        <Input
          type="month"
          value={month}
          onChange={(e) => setMonth(e.target.value)}
          className="w-40"
        />
      </div>
      <MonthlyOps month={safeMonth} />
      <SalaryProfileList />
      <PayslipsList month={safeMonth} />
    </div>
  );
}

// ── 主视图 ──

export default function SalaryView() {
  const { hasPermission } = usePermission();
  const isHr = hasPermission('hr_manage');
  const [month, setMonth] = useState(todayMonth());
  const safeMonth = /^\d{4}-\d{2}$/.test(month) ? month : todayMonth();
  return (
    <div className="mx-auto w-full max-w-3xl space-y-4 p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2 text-sm text-[#475569]">
          <Banknote className="size-4" /> 薪资查询
        </div>
        <div className="flex items-center gap-2 text-xs text-[#94A3B8]">
          <span>月份</span>
          <Input
            type="month"
            value={month}
            onChange={(e) => setMonth(e.target.value)}
            className="w-40"
          />
        </div>
      </div>
      <MyPayslipCard month={safeMonth} />
      {isHr && <HrSection />}
    </div>
  );
}
