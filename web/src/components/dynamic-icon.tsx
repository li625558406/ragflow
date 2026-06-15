import {
  Activity,
  AlertCircle,
  AlertTriangle,
  ArrowLeftRight,
  BadgeCheck,
  BarChart3,
  Blocks,
  BookOpen,
  Bookmark,
  BookmarkCheck,
  Building2,
  Calculator,
  ClipboardCheck,
  Compass,
  Database,
  FileCheck2,
  FileSearch2,
  Gavel,
  GraduationCap,
  Hammer,
  HardHat,
  HeartPulse,
  History,
  Landmark,
  LayoutDashboard,
  Loader,
  Lock,
  MessageCircle,
  MessagesSquare,
  Monitor,
  Radio,
  Receipt,
  RefreshCw,
  Scale,
  ScrollText,
  Search,
  ShieldCheck,
  Sparkles,
  TrendingUp,
  Users,
  Wrench,
  type LucideIcon,
} from 'lucide-react';

const iconMap: Record<string, LucideIcon> = {
  activity: Activity,
  'alert-circle': AlertCircle,
  'alert-triangle': AlertTriangle,
  'arrow-left-right': ArrowLeftRight,
  'badge-check': BadgeCheck,
  'bar-chart-3': BarChart3,
  blocks: Blocks,
  'book-open': BookOpen,
  bookmark: Bookmark,
  'bookmark-check': BookmarkCheck,
  'building-2': Building2,
  calculator: Calculator,
  'clipboard-check': ClipboardCheck,
  compass: Compass,
  database: Database,
  'file-check-2': FileCheck2,
  'file-search-2': FileSearch2,
  gavel: Gavel,
  'graduation-cap': GraduationCap,
  hammer: Hammer,
  history: History,
  'hard-hat': HardHat,
  'heart-pulse': HeartPulse,
  landmark: Landmark,
  'layout-dashboard': LayoutDashboard,
  loader: Loader,
  lock: Lock,
  'message-circle': MessageCircle,
  'messages-square': MessagesSquare,
  monitor: Monitor,
  radio: Radio,
  receipt: Receipt,
  'refresh-cw': RefreshCw,
  scale: Scale,
  'scroll-text': ScrollText,
  search: Search,
  'shield-check': ShieldCheck,
  sparkles: Sparkles,
  'trending-up': TrendingUp,
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
