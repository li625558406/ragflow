import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import {
  exportReport,
  fetchAdjustments,
  fetchVouchers,
  generateVoucher,
  importAttendance,
  searchArchive,
} from '@/services/hr-service';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import {
  Archive,
  BookText,
  ChevronDown,
  ChevronRight,
  FileDown,
  Fingerprint,
  History,
} from 'lucide-react';
import { Fragment, useState } from 'react';
import type {
  ArchiveRow,
  BatchImportResult,
  PayslipAdjust,
  Voucher,
} from './hr-types';

function fmt(n: number | null | undefined) {
  return Number(n ?? 0).toFixed(2);
}

function todayMonth() {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`;
}

// ── 中文映射（全模块统一唯一来源）──

const REPORT_TYPES: {
  key: 'attendance' | 'payroll' | 'insurance';
  label: string;
}[] = [
  { key: 'attendance', label: '考勤月汇总' },
  { key: 'payroll', label: '工资发放明细' },
  { key: 'insurance', label: '社保公积金个税汇总' },
];

const VOUCHER_TYPE_LABEL: Record<Voucher['voucher_type'], string> = {
  accrue: '计提',
  pay: '发放',
};

// 类型徽章：计提蓝 / 发放绿
const VOUCHER_TYPE_BADGE: Record<Voucher['voucher_type'], string> = {
  accrue: 'bg-blue-50 text-blue-600 border-blue-200',
  pay: 'bg-emerald-50 text-emerald-600 border-emerald-200',
};

// status: normal=正常 | stale=工资调整后未重生成
const VOUCHER_STATUS_BADGE: Record<string, string> = {
  normal: 'bg-gray-100 text-gray-500 border-gray-200',
  stale: 'bg-amber-50 text-amber-600 border-amber-200',
};

const ADJUST_FIELD_LABEL: Record<string, string> = {
  attendance_deduction: '考勤扣款',
  social_insurance: '社保',
  housing_fund: '公积金',
  income_tax: '个税',
};

const PAYSLIP_STATUS_BADGE: Record<string, string> = {
  draft: 'bg-gray-100 text-gray-500 border-gray-200',
  published: 'bg-emerald-50 text-emerald-600 border-emerald-200',
};

const PAYSLIP_STATUS_LABEL: Record<string, string> = {
  draft: '草稿',
  published: '已发布',
};

// ── 报表导出卡 ──

function ReportExportCard() {
  const [month, setMonth] = useState(todayMonth());
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  // 月份输入可能为空或非法（YYYY-MM 之外），回退到当月，避免用脏值发请求
  const safeMonth = /^\d{4}-\d{2}$/.test(month) ? month : '';

  const doExport = async (
    type: 'attendance' | 'payroll' | 'insurance',
    label: string,
  ) => {
    if (busy) return;
    setBusy(true);
    setMsg(null);
    try {
      await exportReport(type, safeMonth);
      setMsg({ ok: true, text: `${label}已开始下载` });
    } catch (e) {
      setMsg({
        ok: false,
        text: e instanceof Error ? e.message : '报表下载失败',
      });
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="rounded-xl border border-[#E2E8F0] bg-white p-5 shadow-sm">
      <div className="mb-3 flex items-center gap-2 text-sm font-medium text-[#0F172A]">
        <FileDown className="size-4" /> 报表导出
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <div className="flex items-center gap-2 text-xs text-[#94A3B8]">
          <span>月份</span>
          <Input
            type="month"
            value={month}
            onChange={(e) => setMonth(e.target.value)}
            className="w-40"
          />
        </div>
        {REPORT_TYPES.map((t) => (
          <Button
            key={t.key}
            size="sm"
            variant={t.key === 'attendance' ? 'default' : 'outline'}
            disabled={busy || !safeMonth}
            onClick={() => doExport(t.key, t.label)}
            className={
              t.key === 'attendance'
                ? 'bg-[#1a66fb] text-white hover:bg-[#1554d6]'
                : ''
            }
          >
            {t.label}
          </Button>
        ))}
      </div>
      {!safeMonth && (
        <div className="mt-1 text-xs text-red-500">请选择月份（YYYY-MM）</div>
      )}
      {msg && (
        <div
          className={`mt-2 text-sm ${msg.ok ? 'text-[#1a66fb]' : 'text-red-500'}`}
        >
          {msg.text}
        </div>
      )}
    </div>
  );
}

// ── 凭证展开明细表（借贷）──

function VoucherEntries({ entries }: { entries: Voucher['entries'] }) {
  if (!entries.length) {
    return <div className="px-3 py-2 text-xs text-[#94A3B8]">无分录</div>;
  }
  const debitTotal = entries.reduce((s, e) => s + Number(e[2] ?? 0), 0);
  const creditTotal = entries.reduce((s, e) => s + Number(e[3] ?? 0), 0);
  return (
    <div className="overflow-auto rounded-lg border border-[#F1F5F9]">
      <table className="w-full text-xs">
        <thead className="bg-[#F8FAFC] text-[#64748B]">
          <tr>
            <th className="px-2 py-1.5 text-left">摘要</th>
            <th className="px-2 py-1.5 text-left">科目</th>
            <th className="px-2 py-1.5 text-right">借方</th>
            <th className="px-2 py-1.5 text-right">贷方</th>
          </tr>
        </thead>
        <tbody>
          {entries.map((e, i) => (
            <tr key={i} className="border-t border-[#F8FAFC]">
              <td className="px-2 py-1.5 text-left text-[#0F172A]">{e[0]}</td>
              <td className="px-2 py-1.5 text-left text-[#0F172A]">{e[1]}</td>
              <td className="px-2 py-1.5 text-right text-[#0F172A]">
                {Number(e[2] ?? 0) ? fmt(e[2]) : ''}
              </td>
              <td className="px-2 py-1.5 text-right text-[#0F172A]">
                {Number(e[3] ?? 0) ? fmt(e[3]) : ''}
              </td>
            </tr>
          ))}
          <tr className="border-t border-[#F1F5F9] bg-[#F8FAFC]/60">
            <td className="px-2 py-1.5 font-medium text-[#475569]" colSpan={2}>
              合计
            </td>
            <td className="px-2 py-1.5 text-right font-medium text-[#0F172A]">
              ¥{fmt(debitTotal)}
            </td>
            <td className="px-2 py-1.5 text-right font-medium text-[#0F172A]">
              ¥{fmt(creditTotal)}
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  );
}

// ── 凭证卡（生成 + 列表）──

function VoucherCard() {
  const qc = useQueryClient();
  const [month, setMonth] = useState(todayMonth());
  const [voucherType, setVoucherType] = useState<'accrue' | 'pay'>('accrue');
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [expandedId, setExpandedId] = useState('');
  const safeMonth = /^\d{4}-\d{2}$/.test(month) ? month : '';

  const vouchers = useQuery({
    queryKey: ['hr-vouchers', safeMonth],
    queryFn: () => fetchVouchers(safeMonth),
    enabled: !!safeMonth,
  });
  const list = vouchers.data?.list ?? [];

  const doGenerate = async () => {
    if (busy || !safeMonth) return;
    setBusy(true);
    setMsg(null);
    try {
      const v = await generateVoucher(safeMonth, voucherType);
      setMsg({
        ok: true,
        text: `${safeMonth} ${VOUCHER_TYPE_LABEL[v.voucher_type] ?? v.voucher_type} 凭证已生成（合计 ¥${fmt(v.total_amount)}）`,
      });
      qc.invalidateQueries({ queryKey: ['hr-vouchers'] });
    } catch (e) {
      setMsg({
        ok: false,
        text: e instanceof Error ? e.message : '凭证生成失败',
      });
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="rounded-xl border border-[#E2E8F0] bg-white p-5 shadow-sm">
      <div className="mb-3 flex items-center gap-2 text-sm font-medium text-[#0F172A]">
        <BookText className="size-4" /> 财务凭证
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <div className="flex items-center gap-2 text-xs text-[#94A3B8]">
          <span>月份</span>
          <Input
            type="month"
            value={month}
            onChange={(e) => setMonth(e.target.value)}
            className="w-40"
          />
        </div>
        <div className="flex items-center gap-1 text-xs text-[#94A3B8]">
          <span>类型</span>
          {(['accrue', 'pay'] as const).map((t) => (
            <button
              key={t}
              onClick={() => setVoucherType(t)}
              className={`rounded-lg px-3 py-1.5 text-xs font-medium transition-colors ${
                voucherType === t
                  ? 'bg-[#1a66fb] text-white'
                  : 'text-[#475569] hover:bg-[#F1F5F9]'
              }`}
            >
              {VOUCHER_TYPE_LABEL[t]}
            </button>
          ))}
        </div>
        <Button
          size="sm"
          onClick={doGenerate}
          disabled={busy || !safeMonth}
          className="bg-[#1a66fb] text-white hover:bg-[#1554d6]"
        >
          生成/重生成凭证
        </Button>
      </div>
      {!safeMonth && (
        <div className="mt-1 text-xs text-red-500">请选择月份（YYYY-MM）</div>
      )}
      {msg && (
        <div
          className={`mt-2 text-sm ${msg.ok ? 'text-[#1a66fb]' : 'text-red-500'}`}
        >
          {msg.text}
        </div>
      )}
      <div className="mt-3">
        {vouchers.isLoading ? (
          <div className="text-sm text-[#94A3B8]">加载中…</div>
        ) : list.length === 0 ? (
          <div className="text-sm text-[#94A3B8]">
            {safeMonth
              ? '该月暂无凭证，请先在薪资页发布工资条后生成'
              : '请选择月份'}
          </div>
        ) : (
          <div className="space-y-2">
            {list.map((v) => (
              <Fragment key={v.id}>
                <div className="flex flex-wrap items-center gap-2 rounded-lg border border-[#F1F5F9] px-3 py-2">
                  <button
                    onClick={() =>
                      setExpandedId(expandedId === v.id ? '' : v.id)
                    }
                    className="flex items-center gap-1 text-xs text-[#1a66fb] hover:underline"
                  >
                    {expandedId === v.id ? (
                      <ChevronDown className="size-3.5" />
                    ) : (
                      <ChevronRight className="size-3.5" />
                    )}
                    分录
                  </button>
                  <span className="text-xs font-medium text-[#0F172A]">
                    {v.month}
                  </span>
                  <span
                    className={`inline-block rounded-full border px-2 py-0.5 text-[10px] ${VOUCHER_TYPE_BADGE[v.voucher_type] ?? 'bg-gray-100 text-gray-500 border-gray-200'}`}
                  >
                    {VOUCHER_TYPE_LABEL[v.voucher_type] ?? v.voucher_type}
                  </span>
                  {v.status === 'stale' && (
                    <span
                      className={`inline-block rounded-full border px-2 py-0.5 text-[10px] ${VOUCHER_STATUS_BADGE.stale}`}
                      title="工资单生成凭证后有手工调整，数据已过期"
                    >
                      已过期，建议重生成
                    </span>
                  )}
                  <span className="text-xs text-[#64748B]">
                    借贷合计 ¥{fmt(v.total_amount)}
                  </span>
                  <span className="ml-auto text-[10px] text-[#94A3B8]">
                    {v.create_time}
                  </span>
                </div>
                {expandedId === v.id && (
                  <div className="pl-4">
                    <VoucherEntries entries={v.entries} />
                  </div>
                )}
              </Fragment>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

// ── 调整日志卡（本期仅查询展示；调整入口在薪资页工资单列表，后续迭代）──

function AdjustLogCard() {
  const [month, setMonth] = useState(todayMonth());
  const [queryMonth, setQueryMonth] = useState(todayMonth());
  const safeMonth = /^\d{4}-\d{2}$/.test(queryMonth) ? queryMonth : '';

  const adjusts = useQuery({
    queryKey: ['hr-adjustments', safeMonth],
    queryFn: () => fetchAdjustments({ month: safeMonth }),
    enabled: !!safeMonth,
  });
  const list: PayslipAdjust[] = adjusts.data?.list ?? [];

  return (
    <div className="rounded-xl border border-[#E2E8F0] bg-white p-5 shadow-sm">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2 text-sm font-medium text-[#0F172A]">
          <History className="size-4" /> 工资调整记录（
          {adjusts.data?.total ?? 0}）
        </div>
        <div className="flex items-center gap-2">
          <Input
            type="month"
            value={month}
            onChange={(e) => setMonth(e.target.value)}
            className="w-40"
          />
          <Button
            size="sm"
            variant="outline"
            disabled={!/^\d{4}-\d{2}$/.test(month)}
            onClick={() => setQueryMonth(month)}
          >
            查询
          </Button>
        </div>
      </div>
      {list.length === 0 ? (
        <div className="text-sm text-[#94A3B8]">该月暂无工资调整记录</div>
      ) : (
        <div className="max-h-64 overflow-auto rounded-lg border border-[#F1F5F9]">
          <table className="w-full text-xs">
            <thead className="bg-[#F8FAFC] text-[#64748B]">
              <tr>
                <th className="px-2 py-1.5 text-left">字段</th>
                <th className="px-2 py-1.5 text-right">旧值</th>
                <th className="px-2 py-1.5 text-right">新值</th>
                <th className="px-2 py-1.5 text-left">原因</th>
                <th className="px-2 py-1.5 text-left">操作人</th>
                <th className="px-2 py-1.5 text-left">时间</th>
              </tr>
            </thead>
            <tbody>
              {list.map((r) => (
                <tr key={r.id} className="border-t border-[#F8FAFC]">
                  <td className="px-2 py-1.5 text-left text-[#0F172A]">
                    {ADJUST_FIELD_LABEL[r.field] ?? r.field}
                  </td>
                  <td className="px-2 py-1.5 text-right text-[#64748B]">
                    ¥{fmt(r.old_value)}
                  </td>
                  <td className="px-2 py-1.5 text-right font-medium text-[#1a66fb]">
                    ¥{fmt(r.new_value)}
                  </td>
                  <td
                    className="max-w-40 truncate px-2 py-1.5 text-left text-[#0F172A]"
                    title={r.reason}
                  >
                    {r.reason || '—'}
                  </td>
                  <td className="px-2 py-1.5 text-left text-[#0F172A]">
                    {r.operator_name || r.operator_id || '—'}
                  </td>
                  <td className="px-2 py-1.5 text-left text-[#94A3B8]">
                    {r.create_time}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

// ── 归档检索卡 ──

function ArchiveSearchCard() {
  const [month, setMonth] = useState('');
  const [department, setDepartment] = useState('');
  const [keyword, setKeyword] = useState('');
  const [error, setError] = useState('');
  const [rows, setRows] = useState<ArchiveRow[] | null>(null);
  const [busy, setBusy] = useState(false);

  const doSearch = async () => {
    if (busy) return;
    const m = month.trim();
    const d = department.trim();
    const k = keyword.trim();
    if (!m && !d && !k) {
      setError('月份 / 部门 / 关键词至少填写一项');
      return;
    }
    if (m && !/^\d{4}-\d{2}$/.test(m)) {
      setError('月份格式应为 YYYY-MM');
      return;
    }
    setError('');
    setBusy(true);
    try {
      const r = await searchArchive({
        ...(m ? { month: m } : {}),
        ...(d ? { department: d } : {}),
        ...(k ? { keyword: k } : {}),
      });
      setRows(r.list ?? []);
    } catch (e) {
      setError(e instanceof Error ? e.message : '查询失败');
      setRows(null);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="rounded-xl border border-[#E2E8F0] bg-white p-5 shadow-sm">
      <div className="mb-3 flex items-center gap-2 text-sm font-medium text-[#0F172A]">
        <Archive className="size-4" /> 历史归档检索
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <Input
          type="month"
          value={month}
          onChange={(e) => setMonth(e.target.value)}
          placeholder="月份"
          className="w-36 text-xs"
        />
        <Input
          value={department}
          onChange={(e) => setDepartment(e.target.value)}
          placeholder="部门（模糊）"
          className="h-8 w-36 text-xs"
        />
        <Input
          value={keyword}
          onChange={(e) => setKeyword(e.target.value)}
          placeholder="姓名/工号"
          className="h-8 w-36 text-xs"
        />
        <Button
          size="sm"
          onClick={doSearch}
          disabled={busy}
          className="bg-[#1a66fb] text-white hover:bg-[#1554d6]"
        >
          查询
        </Button>
      </div>
      {error && <div className="mt-1 text-xs text-red-500">{error}</div>}
      {rows !== null &&
        (rows.length === 0 ? (
          <div className="mt-3 text-sm text-[#94A3B8]">无匹配的归档记录</div>
        ) : (
          <div className="mt-3 max-h-72 overflow-auto rounded-lg border border-[#F1F5F9]">
            <table className="w-full text-xs">
              <thead className="bg-[#F8FAFC] text-[#64748B]">
                <tr>
                  <th className="px-2 py-1.5 text-left">工号</th>
                  <th className="px-2 py-1.5 text-left">姓名</th>
                  <th className="px-2 py-1.5 text-left">部门</th>
                  <th className="px-2 py-1.5 text-center">月份</th>
                  <th className="px-2 py-1.5 text-right">出勤</th>
                  <th className="px-2 py-1.5 text-right">迟到</th>
                  <th className="px-2 py-1.5 text-right">旷工</th>
                  <th className="px-2 py-1.5 text-right">加班(h)</th>
                  <th className="px-2 py-1.5 text-center">工资单</th>
                  <th className="px-2 py-1.5 text-right">实发</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => (
                  <tr
                    key={`${r.employee_id}-${r.month}`}
                    className="border-t border-[#F8FAFC]"
                  >
                    <td className="px-2 py-1.5 text-left text-[#0F172A]">
                      {r.emp_no || '—'}
                    </td>
                    <td className="px-2 py-1.5 text-left text-[#0F172A]">
                      {r.nickname || '—'}
                    </td>
                    <td className="px-2 py-1.5 text-left text-[#0F172A]">
                      {r.department || '—'}
                    </td>
                    <td className="px-2 py-1.5 text-center text-[#0F172A]">
                      {r.month}
                    </td>
                    <td className="px-2 py-1.5 text-right text-[#0F172A]">
                      {fmt(r.attend_days)}
                    </td>
                    <td className="px-2 py-1.5 text-right text-[#0F172A]">
                      {r.late_count}
                    </td>
                    <td className="px-2 py-1.5 text-right text-[#0F172A]">
                      {r.absent_days}
                    </td>
                    <td className="px-2 py-1.5 text-right text-[#0F172A]">
                      {fmt(r.overtime_hours)}
                    </td>
                    <td className="px-2 py-1.5 text-center">
                      {r.payslip_status ? (
                        <span
                          className={`inline-block rounded-full border px-2 py-0.5 ${PAYSLIP_STATUS_BADGE[r.payslip_status]}`}
                        >
                          {PAYSLIP_STATUS_LABEL[r.payslip_status] ??
                            r.payslip_status}
                        </span>
                      ) : (
                        <span className="text-[#94A3B8]">无</span>
                      )}
                    </td>
                    <td className="px-2 py-1.5 text-right font-medium text-[#1a66fb]">
                      {r.net_pay === null ? '—' : `¥${fmt(r.net_pay)}`}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ))}
    </div>
  );
}

// ── 考勤机导入卡（预留：JSON 数组粘贴导入）──

function AttendanceImportCard() {
  const [open, setOpen] = useState(false);
  const [text, setText] = useState('');
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [result, setResult] = useState<BatchImportResult | null>(null);

  const doImport = async () => {
    if (busy) return;
    setMsg(null);
    let records: unknown;
    try {
      records = JSON.parse(text);
    } catch {
      setMsg({ ok: false, text: 'JSON 解析失败，请检查格式' });
      return;
    }
    if (!Array.isArray(records) || records.length === 0) {
      setMsg({ ok: false, text: '请粘贴非空的 JSON 数组' });
      return;
    }
    setBusy(true);
    try {
      const r = await importAttendance(records);
      const failTotal = r.fail_total ?? r.failed.length;
      setResult(r);
      setMsg({
        ok: failTotal === 0,
        text: `导入完成：成功 ${r.success} / 失败 ${failTotal}（共 ${r.total} 条）`,
      });
    } catch (e) {
      setMsg({
        ok: false,
        text: e instanceof Error ? e.message : '导入失败',
      });
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="rounded-xl border border-[#E2E8F0] bg-white p-5 shadow-sm">
      <button
        onClick={() => setOpen(!open)}
        className="flex w-full items-center gap-2 text-left text-sm font-medium text-[#0F172A]"
      >
        {open ? (
          <ChevronDown className="size-4" />
        ) : (
          <ChevronRight className="size-4" />
        )}
        <Fingerprint className="size-4" /> 考勤机批量导入
        <span className="ml-1 rounded-full border border-gray-200 bg-gray-100 px-2 py-0.5 text-[10px] font-normal text-gray-500">
          预留
        </span>
      </button>
      {open && (
        <div className="mt-3">
          <div className="mb-2 text-xs text-[#64748B]">
            预留功能：支持考勤机 API 同步与 Excel 批量导入（当前版本请粘贴 JSON
            数组）。格式：[
            {'{'}&quot;emp_no&quot;: &quot;工号或 employee_id&quot;,
            &quot;punch_time&quot;: &quot;2026-09-01 08:55:00&quot;{'}'},
            ...]，单批最多 2000 条。
          </div>
          <textarea
            value={text}
            onChange={(e) => setText(e.target.value)}
            placeholder='[{"emp_no":"EMP001","punch_time":"2026-09-01 08:55:00"}]'
            rows={6}
            className="w-full rounded-lg border border-[#E2E8F0] bg-white p-3 font-mono text-xs text-[#0F172A] outline-none focus:border-[#1a66fb]"
          />
          <div className="mt-2 flex items-center gap-2">
            <Button
              size="sm"
              onClick={doImport}
              disabled={busy}
              className="bg-[#1a66fb] text-white hover:bg-[#1554d6]"
            >
              导入打卡记录
            </Button>
          </div>
          {msg && (
            <div
              className={`mt-2 text-sm ${msg.ok ? 'text-[#1a66fb]' : 'text-red-500'}`}
            >
              {msg.text}
            </div>
          )}
          {result && result.failed.length > 0 && (
            <div className="mt-2 max-h-48 overflow-auto rounded-lg border border-red-100">
              <table className="w-full text-xs">
                <thead className="bg-red-50 text-red-500">
                  <tr>
                    <th className="px-2 py-1.5 text-left">行号</th>
                    <th className="px-2 py-1.5 text-left">员工</th>
                    <th className="px-2 py-1.5 text-left">打卡时间</th>
                    <th className="px-2 py-1.5 text-left">失败原因</th>
                  </tr>
                </thead>
                <tbody>
                  {result.failed.map((f) => (
                    <tr key={f.row} className="border-t border-red-50">
                      <td className="px-2 py-1.5 text-left text-[#64748B]">
                        {f.row}
                      </td>
                      <td className="px-2 py-1.5 text-left text-[#0F172A]">
                        {f.emp || '—'}
                      </td>
                      <td className="px-2 py-1.5 text-left text-[#0F172A]">
                        {f.punch_time || '—'}
                      </td>
                      <td className="px-2 py-1.5 text-left text-red-500">
                        {f.error}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {(result.fail_total ?? result.failed.length) >
                result.failed.length && (
                <div className="px-2 py-1.5 text-[10px] text-[#94A3B8]">
                  仅展示前 {result.failed.length} 条失败明细
                </div>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ── 主视图（全 HR 功能，无员工自助区）──

export default function ReportView() {
  return (
    <div className="mx-auto w-full max-w-3xl space-y-4 p-4">
      <ReportExportCard />
      <VoucherCard />
      <AdjustLogCard />
      <ArchiveSearchCard />
      <AttendanceImportCard />
    </div>
  );
}
