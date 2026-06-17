import { ChevronDown, ChevronRight } from 'lucide-react';
import { useCallback, useEffect, useState } from 'react';

interface ThinkBlockProps {
  children: React.ReactNode;
  loading?: boolean;
}

const ThinkIcon = () => (
  <svg
    width="14"
    height="14"
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth="2"
    strokeLinecap="round"
    strokeLinejoin="round"
    className="text-[#6B597F] shrink-0"
  >
    <path d="M12 2a10 10 0 1 0 10 10A10 10 0 0 0 12 2Z" />
    <path d="M9.09 9a3 3 0 0 1 5.83 1c0 2-3 3-3 3" />
    <path d="M12 17h.01" />
  </svg>
);

export default function ThinkBlock({ children, loading }: ThinkBlockProps) {
  const [expanded, setExpanded] = useState(true);

  useEffect(() => {
    if (!loading) {
      setExpanded(false);
    }
  }, [loading]);

  const toggle = useCallback(() => {
    setExpanded((prev) => !prev);
  }, []);

  return (
    <div className="think-block border border-[#E5E5E5] rounded-lg mb-3 overflow-hidden">
      <button
        type="button"
        onClick={toggle}
        className="think-block__header flex items-center gap-1.5 w-full px-3 py-1.5 text-xs text-[#8B8B8B] hover:bg-[#FAFAFA] transition-colors cursor-pointer select-none"
      >
        <ThinkIcon />
        <span className="font-medium">思考过程</span>
        {expanded ? (
          <ChevronDown className="w-3 h-3" />
        ) : (
          <ChevronRight className="w-3 h-3" />
        )}
      </button>
      {expanded && (
        <div className="think-block__content px-3 pb-2 text-xs text-[#8B8B8B] border-t border-[#F0F0F0] pt-2">
          {children}
        </div>
      )}
    </div>
  );
}
