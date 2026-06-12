import {
  AlertTriangle,
  ArrowLeftRight,
  BadgeCheck,
  BarChart3,
  Blocks,
  Bookmark,
  BookmarkCheck,
  Building2,
  Calculator,
  ClipboardCheck,
  Compass,
  FileCheck2,
  FileSearch2,
  Gavel,
  GraduationCap,
  Hammer,
  HardHat,
  HeartPulse,
  Landmark,
  LayoutDashboard,
  Lock,
  MessageCircle,
  MessagesSquare,
  Monitor,
  Receipt,
  Scale,
  ScrollText,
  Search,
  ShieldCheck,
  Sparkles,
  Users,
  Wrench,
  type LucideIcon,
} from 'lucide-react';

const iconMap: Record<string, LucideIcon> = {
  'alert-triangle': AlertTriangle,
  'arrow-left-right': ArrowLeftRight,
  'badge-check': BadgeCheck,
  'bar-chart-3': BarChart3,
  blocks: Blocks,
  bookmark: Bookmark,
  'bookmark-check': BookmarkCheck,
  'building-2': Building2,
  calculator: Calculator,
  'clipboard-check': ClipboardCheck,
  compass: Compass,
  'file-check-2': FileCheck2,
  'file-search-2': FileSearch2,
  gavel: Gavel,
  'graduation-cap': GraduationCap,
  hammer: Hammer,
  'hard-hat': HardHat,
  'heart-pulse': HeartPulse,
  landmark: Landmark,
  'layout-dashboard': LayoutDashboard,
  lock: Lock,
  'message-circle': MessageCircle,
  'messages-square': MessagesSquare,
  monitor: Monitor,
  receipt: Receipt,
  scale: Scale,
  'scroll-text': ScrollText,
  search: Search,
  'shield-check': ShieldCheck,
  sparkles: Sparkles,
  users: Users,
  wrench: Wrench,
};

interface DynamicIconProps {
  name: string;
  className?: string;
  strokeWidth?: number;
  color?: string;
}

export default function DynamicIcon({
  name,
  className,
  strokeWidth = 1.5,
  color,
}: DynamicIconProps) {
  const Icon = iconMap[name];
  if (!Icon) return null;
  return (
    <Icon
      className={className}
      strokeWidth={strokeWidth}
      style={color ? { color } : undefined}
    />
  );
}
