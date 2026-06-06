'use client';

import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from '@/components/ui/popover';
import {
  ChevronLeftIcon,
  ChevronRightIcon,
  ChevronsLeftIcon,
  ChevronsRightIcon,
  X,
} from 'lucide-react';
import { useCallback, useMemo, useState } from 'react';

// ============================================================================
// Helpers
// ============================================================================

const WEEKDAYS = ['一', '二', '三', '四', '五', '六', '日'];

function fmt(date: Date): string {
  const y = date.getFullYear();
  const m = String(date.getMonth() + 1).padStart(2, '0');
  const d = String(date.getDate()).padStart(2, '0');
  return `${y}-${m}-${d}`;
}

function sameDay(a: Date, b: Date): boolean {
  return (
    a.getFullYear() === b.getFullYear() &&
    a.getMonth() === b.getMonth() &&
    a.getDate() === b.getDate()
  );
}

function startOfMonth(date: Date): Date {
  return new Date(date.getFullYear(), date.getMonth(), 1);
}

function addMonths(date: Date, n: number): Date {
  const d = new Date(date);
  d.setMonth(d.getMonth() + n);
  return d;
}

function getDaysInMonth(year: number, month: number): number {
  return new Date(year, month + 1, 0).getDate();
}

/** 0=Monday, 6=Sunday */
function dayOfWeek(date: Date): number {
  const d = date.getDay();
  return d === 0 ? 6 : d - 1;
}

// ============================================================================
// Types
// ============================================================================

interface DateRange {
  from?: Date;
  to?: Date;
}

interface QuickOption {
  label: string;
  getRange: () => DateRange;
}

// ============================================================================
// Quick options
// ============================================================================

function quickOptions(minDate: Date): QuickOption[] {
  const now = new Date();
  now.setHours(0, 0, 0, 0);
  return [
    {
      label: '近1个月',
      getRange: () => {
        const from = new Date(now);
        from.setMonth(from.getMonth() - 1);
        return { from, to: now };
      },
    },
    {
      label: '近半年',
      getRange: () => {
        const from = new Date(now);
        from.setMonth(from.getMonth() - 6);
        return { from, to: now };
      },
    },
    {
      label: '近1年',
      getRange: () => {
        const from = new Date(now);
        from.setFullYear(from.getFullYear() - 1);
        return { from, to: now };
      },
    },
    {
      label: '近3年',
      getRange: () => {
        const from = new Date(now);
        from.setFullYear(from.getFullYear() - 3);
        return { from: from < minDate ? new Date(minDate) : from, to: now };
      },
    },
    {
      label: '近5年',
      getRange: () => {
        const from = new Date(now);
        from.setFullYear(from.getFullYear() - 5);
        return { from: from < minDate ? new Date(minDate) : from, to: now };
      },
    },
  ];
}

// ============================================================================
// Props
// ============================================================================

interface DatePickerWithRangeProps {
  selected: DateRange | undefined;
  onSelect: (range: DateRange | undefined) => void;
  className?: string;
  label?: string;
  /** Earliest selectable date (inclusive). Default: 2023-01-01 */
  minYear?: number;
}

// ============================================================================
// Single month grid
// ============================================================================

function MonthGrid({
  year,
  month,
  range,
  hoverDate,
  minDate,
  onDayClick,
  onDayEnter,
  onDayLeave,
}: {
  year: number;
  month: number;
  range: DateRange | undefined;
  hoverDate: Date | null;
  minDate: Date;
  onDayClick: (date: Date) => void;
  onDayEnter: (date: Date) => void;
  onDayLeave: () => void;
}) {
  const daysInMonth = getDaysInMonth(year, month);
  const firstDay = dayOfWeek(new Date(year, month, 1));
  const today = new Date();
  today.setHours(0, 0, 0, 0);

  const cells: React.ReactNode[] = [];

  // Blank cells before first day
  for (let i = 0; i < firstDay; i++) {
    cells.push(<div key={`empty-${i}`} className="size-9" />);
  }

  // Day cells
  for (let d = 1; d <= daysInMonth; d++) {
    const date = new Date(year, month, d);
    const key = fmt(date);
    const isToday = sameDay(date, today);
    const isDisabled = date < minDate;

    // Determine selection state
    const from = range?.from;
    const to = range?.to;
    let isSelected = false;
    let isRangeStart = false;
    let isRangeEnd = false;
    let isRangeMiddle = false;

    if (!isDisabled) {
      if (from && to) {
        if (sameDay(date, from)) {
          isRangeStart = true;
          isSelected = true;
        } else if (sameDay(date, to)) {
          isRangeEnd = true;
          isSelected = true;
        } else if (date > from && date < to) {
          isRangeMiddle = true;
        }
      } else if (from && !to && hoverDate) {
        const lo = from < hoverDate ? from : hoverDate;
        const hi = from < hoverDate ? hoverDate : from;
        if (sameDay(date, from)) {
          isRangeStart = true;
          isSelected = true;
        } else if (date > lo && date < hi) {
          isRangeMiddle = true;
        } else if (sameDay(date, hoverDate)) {
          isRangeEnd = true;
          isSelected = true;
        }
      } else if (from && sameDay(date, from)) {
        isRangeStart = true;
        isSelected = true;
      }
    }

    let bg = '';
    if (isDisabled) {
      bg = 'text-[#D4D4D4] cursor-not-allowed rounded-lg';
    } else if (isRangeStart) {
      bg = 'bg-[#000000] text-white rounded-l-lg';
    } else if (isRangeEnd) {
      bg = 'bg-[#000000] text-white rounded-r-lg';
    } else if (isRangeMiddle) {
      bg = 'bg-[#F5F5F5] rounded-none text-[#1a1a1a]';
    } else {
      bg = 'text-[#1a1a1a] hover:bg-[#EAEAEA] rounded-lg cursor-pointer';
    }

    cells.push(
      <button
        key={key}
        type="button"
        className={`size-9 flex items-center justify-center text-sm transition-colors ${
          isToday && !isSelected && !isRangeMiddle
            ? 'font-bold underline underline-offset-4'
            : ''
        } ${isSelected ? 'font-medium' : 'font-normal'} ${bg}`}
        onClick={() => !isDisabled && onDayClick(date)}
        onMouseEnter={() => !isDisabled && onDayEnter(date)}
        onMouseLeave={onDayLeave}
        disabled={isDisabled}
      >
        {d}
      </button>,
    );
  }

  // Always pad to exactly 6 rows (42 cells) for consistent height
  const totalCells = firstDay + daysInMonth;
  const remaining = Math.max(0, 42 - totalCells);
  for (let i = 0; i < remaining; i++) {
    cells.push(<div key={`pad-${i}`} className="size-9" />);
  }

  return (
    <div className="flex flex-col">
      {/* weekday headers */}
      <div className="grid grid-cols-7 mb-1">
        {WEEKDAYS.map((wd) => (
          <div
            key={wd}
            className="size-9 flex items-center justify-center text-xs font-medium text-[#A3A3A3]"
          >
            {wd}
          </div>
        ))}
      </div>
      {/* day grid */}
      <div className="grid grid-cols-7">{cells}</div>
    </div>
  );
}

// ============================================================================
// Year picker
// ============================================================================

function YearPicker({
  currentYear,
  minYear,
  maxYear,
  onSelect,
}: {
  currentYear: number;
  minYear: number;
  maxYear: number;
  onSelect: (year: number) => void;
}) {
  const years: number[] = [];
  // Show a grid of years centered around currentYear
  const startYear = Math.max(minYear, currentYear - 7);
  const endYear = Math.min(maxYear, currentYear + 8);
  for (let y = startYear; y <= endYear; y++) {
    years.push(y);
  }

  return (
    <div className="grid grid-cols-4 gap-1 py-1">
      {years.map((y) => (
        <button
          key={y}
          type="button"
          className={`size-9 flex items-center justify-center text-sm rounded-lg transition-colors cursor-pointer ${
            y === currentYear
              ? 'bg-[#000000] text-white font-medium'
              : y < minYear || y > maxYear
                ? 'text-[#D4D4D4] cursor-not-allowed'
                : 'text-[#1a1a1a] hover:bg-[#EAEAEA]'
          }`}
          disabled={y < minYear || y > maxYear}
          onClick={() => onSelect(y)}
        >
          {y}
        </button>
      ))}
    </div>
  );
}

// ============================================================================
// Main component
// ============================================================================

export function DatePickerWithRange({
  selected,
  onSelect,
  className = '',
  label,
  minYear = 2020,
}: DatePickerWithRangeProps) {
  const [open, setOpen] = useState(false);
  const [baseMonth, setBaseMonth] = useState(() => startOfMonth(new Date()));
  const [hoverDate, setHoverDate] = useState<Date | null>(null);
  const [showYearPicker, setShowYearPicker] = useState(false);

  const leftMonth = baseMonth;
  const rightMonth = addMonths(baseMonth, 1);

  const minDate = useMemo(() => new Date(minYear, 0, 1), [minYear]);

  const hasSelection = !!selected?.from;

  const handleDayClick = useCallback(
    (date: Date) => {
      const cur = selected;
      if (!cur?.from || (cur.from && cur.to)) {
        // Start new selection
        onSelect({ from: date, to: undefined });
      } else {
        // Complete selection
        if (date < cur.from) {
          onSelect({ from: date, to: cur.from });
        } else {
          onSelect({ from: cur.from, to: date });
        }
        setHoverDate(null);
        setOpen(false);
        setShowYearPicker(false);
      }
    },
    [selected, onSelect],
  );

  const handleQuickSelect = useCallback(
    (option: QuickOption) => {
      const range = option.getRange();
      onSelect(range);
      // Jump calendar to show the selected from-date
      if (range.from) {
        setBaseMonth(startOfMonth(range.from));
      }
      setOpen(false);
    },
    [onSelect],
  );

  const handleClear = (e: React.MouseEvent) => {
    e.stopPropagation();
    onSelect(undefined);
  };

  const handleJumpYear = useCallback(
    (year: number) => {
      setBaseMonth(new Date(year, baseMonth.getMonth(), 1));
      setShowYearPicker(false);
    },
    [baseMonth.getMonth()],
  );

  // Navigation: go back, but not beyond minYear
  const canGoPrev = useMemo(() => {
    const limit = new Date(minYear, 0, 1);
    return leftMonth > limit;
  }, [leftMonth, minYear]);

  return (
    <Popover
      open={open}
      onOpenChange={(v) => {
        setOpen(v);
        if (!v) setShowYearPicker(false);
      }}
    >
      <PopoverTrigger asChild>
        <button
          type="button"
          className={`inline-flex items-center gap-1.5 h-9 px-3 text-sm rounded-lg border border-[#D4D4D4] bg-[#F5F5F5] text-[#000000] hover:bg-[#EAEAEA] hover:border-[#A3A3A3] focus:border-[#000000] focus:bg-white focus:outline-none transition-all ${className}`}
        >
          {label && (
            <span className="text-xs font-medium text-[#525252] shrink-0">
              {label}
            </span>
          )}
          <svg
            className="size-3.5 shrink-0 text-[#A3A3A3]"
            fill="none"
            stroke="currentColor"
            strokeWidth={2}
            viewBox="0 0 24 24"
          >
            <rect x="3" y="4" width="18" height="18" rx="2" />
            <path d="M3 10h18" />
            <path d="M16 2v4M8 2v4" />
          </svg>
          {selected?.from ? (
            selected.to ? (
              <span className="text-[#000000]">
                {fmt(selected.from)} — {fmt(selected.to)}
              </span>
            ) : (
              <span className="text-[#000000]">{fmt(selected.from)}</span>
            )
          ) : (
            <span className="text-[#A3A3A3]">选择日期</span>
          )}
          {hasSelection && (
            <X
              className="size-3.5 shrink-0 text-[#A3A3A3] hover:text-[#000000] transition-colors"
              onClick={handleClear}
            />
          )}
        </button>
      </PopoverTrigger>
      <PopoverContent
        className="w-auto p-0 bg-white border border-[#E8E8E8]"
        align="start"
        sideOffset={4}
      >
        {/* Quick select buttons */}
        <div className="px-4 pt-3 pb-2 border-b border-[#F0F0F0] flex flex-wrap gap-1.5">
          {quickOptions(minDate).map((opt) => (
            <button
              key={opt.label}
              type="button"
              onClick={() => handleQuickSelect(opt)}
              className="px-2.5 py-1 text-xs text-[#333333] bg-[#F5F5F5] hover:bg-[#EAEAEA] hover:text-[#000000] rounded-md transition-colors cursor-pointer"
            >
              {opt.label}
            </button>
          ))}
        </div>

        {/* Year navigation bar */}
        <div className="h-7 px-4 pt-1 flex items-center justify-between">
          <div className="flex items-center gap-1">
            <button
              type="button"
              className="size-7 flex items-center justify-center rounded-md text-[#525252] hover:bg-[#F5F5F5] hover:text-[#000000] transition-colors cursor-pointer"
              title="上一年"
              onClick={() => setBaseMonth((m) => addMonths(m, -12))}
            >
              <ChevronsLeftIcon className="size-4" />
            </button>
            {canGoPrev ? (
              <button
                type="button"
                className="size-7 flex items-center justify-center rounded-md text-[#525252] hover:bg-[#F5F5F5] hover:text-[#000000] transition-colors cursor-pointer"
                onClick={() => setBaseMonth((m) => addMonths(m, -1))}
              >
                <ChevronLeftIcon className="size-4" />
              </button>
            ) : (
              <div className="size-7" />
            )}
            <button
              type="button"
              className="h-7 min-w-[100px] text-sm font-semibold text-[#1a1a1a] hover:text-[#000000] transition-colors cursor-pointer px-1 text-center"
              onClick={() => setShowYearPicker((s) => !s)}
            >
              {leftMonth.getFullYear()}年{leftMonth.getMonth() + 1}月
            </button>
          </div>

          <div className="flex items-center gap-1">
            <button
              type="button"
              className="h-7 min-w-[100px] text-sm font-semibold text-[#1a1a1a] hover:text-[#000000] transition-colors cursor-pointer px-1 text-center"
              onClick={() => setShowYearPicker((s) => !s)}
            >
              {rightMonth.getFullYear()}年{rightMonth.getMonth() + 1}月
            </button>
            <button
              type="button"
              className="size-7 flex items-center justify-center rounded-md text-[#525252] hover:bg-[#F5F5F5] hover:text-[#000000] transition-colors cursor-pointer"
              onClick={() => setBaseMonth((m) => addMonths(m, 1))}
            >
              <ChevronRightIcon className="size-4" />
            </button>
            <button
              type="button"
              className="size-7 flex items-center justify-center rounded-md text-[#525252] hover:bg-[#F5F5F5] hover:text-[#000000] transition-colors cursor-pointer"
              title="下一年"
              onClick={() => setBaseMonth((m) => addMonths(m, 12))}
            >
              <ChevronsRightIcon className="size-4" />
            </button>
          </div>
        </div>

        {/* Year picker overlay */}
        {showYearPicker && (
          <div className="px-4 pb-2 border-b border-[#F0F0F0]">
            <div className="flex items-center justify-between mb-1">
              <button
                type="button"
                className="size-7 flex items-center justify-center rounded-md text-[#525252] hover:bg-[#F5F5F5] transition-colors cursor-pointer"
                onClick={() => {
                  const cur = baseMonth.getFullYear();
                  const target = Math.max(minYear, cur - 12);
                  setBaseMonth(new Date(target, baseMonth.getMonth(), 1));
                }}
              >
                <ChevronLeftIcon className="size-4" />
              </button>
              <span className="text-xs text-[#A3A3A3]">
                {Math.max(minYear, baseMonth.getFullYear() - 7)} —{' '}
                {baseMonth.getFullYear() + 8}
              </span>
              <button
                type="button"
                className="size-7 flex items-center justify-center rounded-md text-[#525252] hover:bg-[#F5F5F5] transition-colors cursor-pointer"
                onClick={() => {
                  const cur = baseMonth.getFullYear();
                  const target = cur + 12;
                  setBaseMonth(new Date(target, baseMonth.getMonth(), 1));
                }}
              >
                <ChevronRightIcon className="size-4" />
              </button>
            </div>
            <YearPicker
              currentYear={baseMonth.getFullYear()}
              minYear={minYear}
              maxYear={baseMonth.getFullYear() + 8}
              onSelect={handleJumpYear}
            />
          </div>
        )}

        {/* Calendar grids */}
        <div className="flex gap-6 px-4 pt-2 pb-4">
          {/* Left month */}
          <div className="flex flex-col">
            <MonthGrid
              year={leftMonth.getFullYear()}
              month={leftMonth.getMonth()}
              range={selected}
              hoverDate={hoverDate}
              minDate={minDate}
              onDayClick={handleDayClick}
              onDayEnter={setHoverDate}
              onDayLeave={() => setHoverDate(null)}
            />
          </div>

          {/* Divider */}
          <div className="w-px bg-[#E8E8E8]" />

          {/* Right month */}
          <div className="flex flex-col">
            <MonthGrid
              year={rightMonth.getFullYear()}
              month={rightMonth.getMonth()}
              range={selected}
              hoverDate={hoverDate}
              minDate={minDate}
              onDayClick={handleDayClick}
              onDayEnter={setHoverDate}
              onDayLeave={() => setHoverDate(null)}
            />
          </div>
        </div>
      </PopoverContent>
    </Popover>
  );
}
