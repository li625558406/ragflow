'use client';

import { Button } from '@/components/ui/button';
import { Calendar } from '@/components/ui/calendar';
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from '@/components/ui/popover';
import { format } from 'date-fns';
import { zhCN } from 'date-fns/locale/zh-CN';
import { CalendarIcon } from 'lucide-react';
import { PropsRangeRequired } from 'react-day-picker';

export function DatePickerWithRange({
  selected,
  ...props
}: Omit<PropsRangeRequired, 'mode'>) {
  return (
    <Popover>
      <PopoverTrigger asChild>
        <Button
          variant="outline"
          id="date-picker-range"
          className="justify-start px-2.5 font-normal h-9 text-sm border border-[rgba(124,92,252,0.25)] bg-[#f5f3fa] text-[#2d2d4a] hover:bg-[#ede9fe] hover:text-[#2d2d4a]"
        >
          <CalendarIcon className="size-4" />
          {selected?.from ? (
            selected.to ? (
              <>
                {format(selected.from, 'yyyy-MM-dd', { locale: zhCN })} -{' '}
                {format(selected.to, 'yyyy-MM-dd', { locale: zhCN })}
              </>
            ) : (
              format(selected.from, 'yyyy-MM-dd', { locale: zhCN })
            )
          ) : (
            <span>选择日期范围</span>
          )}
        </Button>
      </PopoverTrigger>
      <PopoverContent className="w-auto p-0" align="start">
        <Calendar
          mode="range"
          selected={selected}
          numberOfMonths={2}
          locale={zhCN}
          {...props}
        />
      </PopoverContent>
    </Popover>
  );
}
