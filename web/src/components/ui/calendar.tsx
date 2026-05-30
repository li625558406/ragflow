'use client';

import * as React from 'react';
import { DayPicker, getDefaultClassNames } from 'react-day-picker';

import { cn } from '@/lib/utils';
import { ChevronLeftIcon, ChevronRightIcon } from 'lucide-react';

function Calendar({
  className,
  classNames,
  showOutsideDays = true,
  captionLayout = 'label',
  locale,
  formatters,
  components,
  ...props
}: React.ComponentProps<typeof DayPicker>) {
  const defaultClassNames = getDefaultClassNames();

  return (
    <DayPicker
      showOutsideDays={showOutsideDays}
      className={cn('p-4 bg-white', className)}
      captionLayout={captionLayout}
      locale={locale}
      formatters={formatters}
      classNames={{
        root: cn('w-fit', defaultClassNames.root),
        months: cn(
          'relative flex flex-col gap-6 md:flex-row',
          defaultClassNames.months,
        ),
        month: cn('flex w-full flex-col gap-4', defaultClassNames.month),
        nav: cn(
          'absolute inset-x-0 top-0 flex w-full items-center justify-between gap-2 z-10',
          defaultClassNames.nav,
        ),
        button_previous: cn(
          'inline-flex items-center justify-center size-9 rounded-lg border-0 bg-transparent text-[#525252] hover:bg-[#F5F5F5] hover:text-[#000000] transition-colors cursor-pointer',
          defaultClassNames.button_previous,
        ),
        button_next: cn(
          'inline-flex items-center justify-center size-9 rounded-lg border-0 bg-transparent text-[#525252] hover:bg-[#F5F5F5] hover:text-[#000000] transition-colors cursor-pointer',
          defaultClassNames.button_next,
        ),
        month_caption: cn(
          'flex h-9 w-full items-center justify-center px-9',
          defaultClassNames.month_caption,
        ),
        dropdowns: cn(
          'flex h-9 w-full items-center justify-center gap-1.5 text-sm font-medium',
          defaultClassNames.dropdowns,
        ),
        dropdown_root: cn(
          'relative rounded-lg',
          defaultClassNames.dropdown_root,
        ),
        dropdown: cn('absolute inset-0 opacity-0', defaultClassNames.dropdown),
        caption_label: cn(
          'font-semibold text-sm text-[#1a1a1a] select-none',
          defaultClassNames.caption_label,
        ),
        table: 'w-full border-collapse',
        weekdays: cn('flex', defaultClassNames.weekdays),
        weekday: cn(
          'flex-1 rounded-lg h-9 text-xs font-medium text-[#A3A3A3] select-none flex items-center justify-center',
          defaultClassNames.weekday,
        ),
        week: cn('mt-2 flex w-full', defaultClassNames.week),
        week_number_header: cn(
          'w-9 select-none',
          defaultClassNames.week_number_header,
        ),
        week_number: cn(
          'text-xs text-[#A3A3A3] select-none',
          defaultClassNames.week_number,
        ),
        day: cn(
          'group/day relative aspect-square h-full w-full p-0 text-center select-none',
          defaultClassNames.day,
        ),
        range_start: cn(
          'relative isolate z-0 rounded-l-lg bg-[#F5F5F5] after:absolute after:inset-y-0 after:right-0 after:w-4 after:bg-[#F5F5F5]',
          defaultClassNames.range_start,
        ),
        range_middle: cn(
          'rounded-none bg-[#F5F5F5]',
          defaultClassNames.range_middle,
        ),
        range_end: cn(
          'relative isolate z-0 rounded-r-lg bg-[#F5F5F5] after:absolute after:inset-y-0 after:left-0 after:w-4 after:bg-[#F5F5F5]',
          defaultClassNames.range_end,
        ),
        today: cn(
          'rounded-lg font-bold text-[#000000]',
          defaultClassNames.today,
        ),
        outside: cn('text-[#D4D4D4]', defaultClassNames.outside),
        disabled: cn('text-[#D4D4D4] opacity-50', defaultClassNames.disabled),
        hidden: cn('invisible', defaultClassNames.hidden),
        ...classNames,
      }}
      components={{
        Chevron: ({ orientation, ...chevronProps }) => {
          if (orientation === 'left') {
            return <ChevronLeftIcon className="size-4" {...chevronProps} />;
          }
          return <ChevronRightIcon className="size-4" {...chevronProps} />;
        },
        DayButton: ({ day, modifiers, className, ...dayProps }) => (
          <button
            data-day={day.date.toLocaleDateString(locale?.code)}
            data-selected-single={
              modifiers.selected &&
              !modifiers.range_start &&
              !modifiers.range_end &&
              !modifiers.range_middle
            }
            data-range-start={modifiers.range_start}
            data-range-end={modifiers.range_end}
            data-range-middle={modifiers.range_middle}
            className={cn(
              'relative isolate z-10 flex aspect-square w-full items-center justify-center rounded-lg border-0 text-sm font-normal transition-colors',
              // Range middle
              modifiers.range_middle &&
                'rounded-none bg-[#F5F5F5] text-[#1a1a1a]',
              // Range start
              modifiers.range_start && 'bg-[#000000] text-white rounded-l-lg',
              // Range end
              modifiers.range_end && 'bg-[#000000] text-white rounded-r-lg',
              // Selected single
              modifiers.selected &&
                !modifiers.range_start &&
                !modifiers.range_end &&
                !modifiers.range_middle &&
                'bg-[#000000] text-white',
              // Today
              modifiers.today &&
                !modifiers.selected &&
                'font-bold underline underline-offset-4',
              // Outside
              day.outside && 'text-[#D4D4D4]',
              // Disabled
              modifiers.disabled && 'opacity-30 cursor-not-allowed',
              // Hover (not selected, not disabled)
              !modifiers.selected &&
                !modifiers.disabled &&
                'hover:bg-[#EAEAEA] text-[#1a1a1a]',
              className,
            )}
            {...dayProps}
          />
        ),
        ...components,
      }}
      {...props}
    />
  );
}

export { Calendar };
