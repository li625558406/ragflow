import { X } from 'lucide-react';
import { useCallback, useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';

export interface BidSelectOption {
  label: string;
  value: string;
  disabled?: boolean;
}

interface BidSelectProps {
  value: string;
  onChange: (value: string) => void;
  options: BidSelectOption[];
  placeholder?: string;
  allowClear?: boolean;
  disabled?: boolean;
  className?: string;
}

export function BidSelect({
  value,
  onChange,
  options,
  placeholder = '全部',
  allowClear,
  disabled,
  className = '',
}: BidSelectProps) {
  const [open, setOpen] = useState(false);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const contentRef = useRef<HTMLDivElement>(null);

  const selected = options.find((o) => o.value === value);

  // Close on click outside
  useEffect(() => {
    if (!open) return;
    const handler = (e: MouseEvent) => {
      if (
        triggerRef.current?.contains(e.target as Node) ||
        contentRef.current?.contains(e.target as Node)
      ) {
        return;
      }
      setOpen(false);
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, [open]);

  // Close on Escape
  useEffect(() => {
    if (!open) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setOpen(false);
    };
    document.addEventListener('keydown', handler);
    return () => document.removeEventListener('keydown', handler);
  }, [open]);

  const handleSelect = useCallback(
    (opt: BidSelectOption) => {
      if (opt.disabled) return;
      onChange(opt.value);
      setOpen(false);
    },
    [onChange],
  );

  const handleClear = useCallback(
    (e: React.MouseEvent) => {
      e.stopPropagation();
      onChange('');
    },
    [onChange],
  );

  const base =
    'inline-flex items-center justify-between rounded-md text-sm outline-none h-9 px-3';
  const colorCls = disabled
    ? 'bg-[#F5F5F5] text-[#A3A3A3] border border-[#E5E5E5] cursor-not-allowed'
    : open
      ? 'bg-white text-[#000000] border border-[#000000] cursor-pointer'
      : 'bg-white text-[#000000] border border-[#A3A3A3] hover:bg-[#EAEAEA] hover:border-[#000000] cursor-pointer';

  const triggerCls = `${base} ${colorCls} ${className}`;

  return (
    <>
      <button
        ref={triggerRef}
        type="button"
        disabled={disabled}
        className={triggerCls}
        onClick={() => {
          if (!disabled) setOpen((v) => !v);
        }}
      >
        <span className="truncate">
          {selected ? selected.label : placeholder}
        </span>
        {value && allowClear ? (
          <X
            className="ml-1.5 h-3.5 w-3.5 shrink-0 opacity-50"
            onClick={handleClear}
          />
        ) : (
          <svg
            className={`ml-1.5 h-4 w-4 shrink-0 opacity-50 transition-transform ${open ? 'rotate-180' : ''}`}
            fill="none"
            stroke="currentColor"
            strokeWidth={2}
            viewBox="0 0 24 24"
          >
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              d="M6 9l6 6 6-6"
            />
          </svg>
        )}
      </button>

      {open &&
        createPortal(
          <div
            ref={contentRef}
            className="absolute z-[9999] mt-1 max-h-60 min-w-[8rem] overflow-y-auto rounded-md border border-[#D4D4D4] bg-white shadow-[0_4px_16px_rgba(0,0,0,0.08)] py-1"
            style={{
              top: triggerRef.current
                ? triggerRef.current.getBoundingClientRect().bottom +
                  window.scrollY
                : 0,
              left: triggerRef.current
                ? triggerRef.current.getBoundingClientRect().left +
                  window.scrollX
                : 0,
              width: triggerRef.current
                ? triggerRef.current.offsetWidth
                : 'auto',
            }}
          >
            {options.length === 0 ? (
              <div className="px-3 py-2 text-sm text-[#A3A3A3]">暂无选项</div>
            ) : (
              options.map((opt) => (
                <div
                  key={opt.value}
                  onClick={() => handleSelect(opt)}
                  className={`px-3 py-2 text-sm cursor-pointer transition-colors ${
                    opt.disabled
                      ? 'text-[#A3A3A3] cursor-not-allowed'
                      : opt.value === value
                        ? 'bg-[#EAEAEA] text-[#000000]'
                        : 'text-[#1a1a1a] hover:bg-[#F5F5F5]'
                  }`}
                >
                  {opt.label}
                </div>
              ))
            )}
          </div>,
          document.body,
        )}
    </>
  );
}
