'use client';

import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from '@/components/ui/popover';
import { ChevronLeftIcon, ChevronRightIcon, X } from 'lucide-react';
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

interface DatePickerWithRangeProps {
  selected: DateRange | undefined;
  onSelect: (range: DateRange | undefined) => void;
  className?: string;
  label?: string;
}

// ============================================================================
// Single month grid
// ============================================================================

function MonthGrid({
  year,
  month,
  range,
  hoverDate,
  onDayClick,
  onDayEnter,
  onDayLeave,
}: {
  year: number;
  month: number;
  range: DateRange | undefined;
  hoverDate: Date | null;
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

    // Determine selection state
    const from = range?.from;
    const to = range?.to;
    let isSelected = false;
    let isRangeStart = false;
    let isRangeEnd = false;
    let isRangeMiddle = false;

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
      // Selecting: show preview range
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

    let bg = '';
    if (isRangeStart) {
      bg = 'bg-[#000000] text-white rounded-l-lg';
    } else if (isRangeEnd) {
      bg = 'bg-[#000000] text-white rounded-r-lg';
    } else if (isRangeMiddle) {
      bg = 'bg-[#F5F5F5] rounded-none text-[#1a1a1a]';
    } else {
      bg = 'text-[#1a1a1a] hover:bg-[#EAEAEA] rounded-lg';
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
        onClick={() => onDayClick(date)}
        onMouseEnter={() => onDayEnter(date)}
        onMouseLeave={onDayLeave}
      >
        {d}
      </button>,
    );
  }

  // Fill remaining cells to make 6 rows
  const totalCells = firstDay + daysInMonth;
  const remaining = totalCells % 7 === 0 ? 0 : 7 - (totalCells % 7);
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
// Main component
// ============================================================================

export function DatePickerWithRange({
  selected,
  onSelect,
  className = '',
  label,
}: DatePickerWithRangeProps) {
  const [open, setOpen] = useState(false);
  const [baseMonth, setBaseMonth] = useState(() => startOfMonth(new Date()));
  const [hoverDate, setHoverDate] = useState<Date | null>(null);

  const leftMonth = baseMonth;
  const rightMonth = addMonths(baseMonth, 1);

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
      }
    },
    [selected, onSelect],
  );

  const handleClear = (e: React.MouseEvent) => {
    e.stopPropagation();
    onSelect(undefined);
  };

  const canGoPrev = useMemo(() => {
    const now = new Date();
    const limit = new Date(now.getFullYear(), now.getMonth() - 12, 1);
    return baseMonth > limit;
  }, [baseMonth]);

  const canGoNext = useMemo(() => {
    const now = new Date();
    const limit = new Date(now.getFullYear(), now.getMonth() + 12, 1);
    return rightMonth < limit;
  }, [rightMonth]);

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <button
          type="button"
          className={`inline-flex items-center gap-1.5 h-9 px-3 text-sm rounded-lg border-0 bg-[#F5F5F5] text-[#000000] hover:bg-[#EAEAEA] focus:bg-white transition-all ${className}`}
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
      <PopoverContent className="w-auto p-4 bg-white" align="start">
        <div className="flex gap-6">
          {/* Left month */}
          <div className="flex flex-col">
            <div className="flex items-center justify-between mb-3">
              {canGoPrev && (
                <button
                  type="button"
                  className="size-7 flex items-center justify-center rounded-md text-[#525252] hover:bg-[#F5F5F5] hover:text-[#000000] transition-colors"
                  onClick={() => setBaseMonth((m) => addMonths(m, -1))}
                >
                  <ChevronLeftIcon className="size-4" />
                </button>
              )}
              <div className="flex-1 text-center">
                <span className="text-sm font-semibold text-[#1a1a1a]">
                  {leftMonth.getFullYear()}年{leftMonth.getMonth() + 1}月
                </span>
              </div>
              {/* Spacer for alignment when no prev button */}
              {!canGoPrev && <div className="size-7" />}
            </div>
            <MonthGrid
              year={leftMonth.getFullYear()}
              month={leftMonth.getMonth()}
              range={selected}
              hoverDate={hoverDate}
              onDayClick={handleDayClick}
              onDayEnter={setHoverDate}
              onDayLeave={() => setHoverDate(null)}
            />
          </div>

          {/* Divider */}
          <div className="w-px bg-[#E8E8E8]" />

          {/* Right month */}
          <div className="flex flex-col">
            <div className="flex items-center justify-between mb-3">
              {/* Spacer for alignment */}
              <div className="size-7" />
              <div className="flex-1 text-center">
                <span className="text-sm font-semibold text-[#1a1a1a]">
                  {rightMonth.getFullYear()}年{rightMonth.getMonth() + 1}月
                </span>
              </div>
              {canGoNext && (
                <button
                  type="button"
                  className="size-7 flex items-center justify-center rounded-md text-[#525252] hover:bg-[#F5F5F5] hover:text-[#000000] transition-colors"
                  onClick={() => setBaseMonth((m) => addMonths(m, 1))}
                >
                  <ChevronRightIcon className="size-4" />
                </button>
              )}
              {!canGoNext && <div className="size-7" />}
            </div>
            <MonthGrid
              year={rightMonth.getFullYear()}
              month={rightMonth.getMonth()}
              range={selected}
              hoverDate={hoverDate}
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
