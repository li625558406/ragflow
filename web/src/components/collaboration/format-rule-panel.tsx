import {
  CDialog,
  CDialogContent,
  CDialogFooter,
  CDialogHeader,
  CDialogTitle,
} from '@/components/c-dialog';
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog';
import { CendTooltip } from '@/components/ui/tooltip';
import { useCallback, useEffect, useRef, useState } from 'react';

interface FormatRule {
  id: string;
  name: string;
  description: string;
  config: Record<string, unknown>;
  created_by?: string;
  permission?: string;
}

function getCurrentUserId(): string | null {
  try {
    const userInfo = JSON.parse(localStorage.getItem('userInfo') || 'null');
    return userInfo?.id || userInfo?.user_id || null;
  } catch {
    return null;
  }
}

interface StyleRule {
  name: string;
  pattern: string;
  fontFamily: string;
  fontSize: number;
  fontColor: string;
  alignment: 'left' | 'center' | 'right' | 'justify';
  bold: boolean;
  heading: '' | 'h1' | 'h2' | 'h3';
}

interface Props {
  apiFetch: (url: string, options?: RequestInit) => Promise<Response>;
  selectedDocId?: string | null;
  onApplyRule?: (rule: FormatRule) => void;
  applyingRuleId?: string | null;
}

const DEFAULT_STYLE_RULES: StyleRule[] = [
  {
    name: '一级标题',
    pattern:
      '^[一二三四五六七八九十]+[、．.）)]|^（\\d+）|^\\d+[、．]|^\\d+\\.(?!\\d)|^第[一二三四五六七八九十\\d]+章',
    fontFamily: 'SimHei',
    fontSize: 16,
    fontColor: '#1C1917',
    alignment: 'center',
    bold: true,
    heading: 'h1',
  },
  {
    name: '二级标题',
    pattern: '^\\d+\\.\\d+',
    fontFamily: 'SimHei',
    fontSize: 14,
    fontColor: '#1C1917',
    alignment: 'left',
    bold: true,
    heading: 'h2',
  },
  {
    name: '正文',
    pattern: '.*',
    fontFamily: 'SimSun',
    fontSize: 12,
    fontColor: '#1C1917',
    alignment: 'left',
    bold: false,
    heading: '',
  },
];

const FONT_FAMILIES = [
  'SimSun',
  'SimHei',
  'KaiTi',
  'FangSong',
  'Microsoft YaHei',
  'Arial',
  'Times New Roman',
];
const FONT_SIZE_OPTIONS = [10, 12, 14, 15, 16, 18, 22];
const ALIGNMENT_OPTIONS: { value: StyleRule['alignment']; label: string }[] = [
  { value: 'left', label: '左' },
  { value: 'center', label: '中' },
  { value: 'right', label: '右' },
  { value: 'justify', label: '两端' },
];

function SelectArrow() {
  return (
    <svg
      className="pointer-events-none absolute right-1.5 top-1/2 -translate-y-1/2 w-3 h-3 text-[#1a1a1a]"
      fill="none"
      stroke="currentColor"
      viewBox="0 0 24 24"
    >
      <path
        strokeLinecap="round"
        strokeLinejoin="round"
        strokeWidth={2}
        d="M19 9l-7 7-7-7"
      />
    </svg>
  );
}

function StyleRuleEditor({
  rule,
  isLast,
  onChange,
  onRemove,
  disabled,
}: {
  rule: StyleRule;
  index: number;
  isLast: boolean;
  onChange: (field: keyof StyleRule, value: unknown) => void;
  onRemove: () => void;
  disabled?: boolean;
}) {
  const colorInputRef = useRef<HTMLInputElement>(null);
  const canRemove = !isLast; // Last rule is always the fallback "正文"

  return (
    <div className="border border-[#E8E8E6] rounded-xl p-3 bg-white space-y-2">
      {/* Row 1: Name + Pattern + Remove */}
      <div className="flex items-center gap-2">
        <input
          type="text"
          className="flex-1 px-2.5 py-1.5 bg-[#F5F5F4] border border-[#E8E8E6] rounded-lg text-xs text-[#000000] placeholder:text-[#A3A3A3] focus:outline-none focus:border-[#000000] focus:bg-white transition disabled:opacity-60"
          value={rule.name}
          onChange={(e) => onChange('name', e.target.value)}
          placeholder="样式名称"
          disabled={disabled}
        />
        <input
          type="text"
          className="flex-[2] px-2.5 py-1.5 bg-[#F5F5F4] border border-[#E8E8E6] rounded-lg text-xs text-[#000000] placeholder:text-[#A3A3A3] font-mono focus:outline-none focus:border-[#000000] focus:bg-white transition disabled:opacity-60"
          value={rule.pattern}
          onChange={(e) => onChange('pattern', e.target.value)}
          placeholder={isLast ? '.* (匹配所有)' : '正则匹配，如 ^[一二三]+[、]'}
          disabled={disabled}
        />
        {canRemove && !disabled && (
          <CendTooltip title="删除此样式">
            <button
              className="shrink-0 w-6 h-6 flex items-center justify-center rounded text-[#A3A3A3] hover:text-red-500 hover:bg-red-50 transition-colors"
              onClick={onRemove}
            >
              <svg
                className="w-3.5 h-3.5"
                fill="none"
                stroke="currentColor"
                viewBox="0 0 24 24"
              >
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  strokeWidth={2}
                  d="M6 18L18 6M6 6l12 12"
                />
              </svg>
            </button>
          </CendTooltip>
        )}
      </div>

      {/* Row 2: Formatting controls */}
      <div className="flex items-center gap-1.5 flex-wrap">
        {/* Font */}
        <div className="relative">
          <select
            className="h-7 px-1.5 pr-6 text-[11px] border border-[#E8E8E6] rounded-lg bg-[#F5F5F4] text-[#000000] focus:outline-none focus:border-[#000000] disabled:opacity-60 appearance-none cursor-pointer"
            value={rule.fontFamily}
            onChange={(e) => onChange('fontFamily', e.target.value)}
            disabled={disabled}
          >
            {FONT_FAMILIES.map((f) => (
              <option key={f} value={f}>
                {f === 'SimSun'
                  ? '宋体'
                  : f === 'SimHei'
                    ? '黑体'
                    : f === 'KaiTi'
                      ? '楷体'
                      : f === 'FangSong'
                        ? '仿宋'
                        : f === 'Microsoft YaHei'
                          ? '微软雅黑'
                          : f}
              </option>
            ))}
          </select>
          <SelectArrow />
        </div>

        {/* Size */}
        <div className="relative">
          <select
            className="h-7 px-1 pr-6 text-[11px] border border-[#E8E8E6] rounded-lg bg-[#F5F5F4] text-[#000000] focus:outline-none focus:border-[#000000] disabled:opacity-60 appearance-none cursor-pointer"
            value={rule.fontSize}
            onChange={(e) => onChange('fontSize', Number(e.target.value))}
            disabled={disabled}
          >
            {FONT_SIZE_OPTIONS.map((s) => (
              <option key={s} value={s}>
                {s}pt
              </option>
            ))}
          </select>
          <SelectArrow />
        </div>

        <div className="w-px h-5 bg-[#D4D4D4]" />

        {/* Bold */}
        <CendTooltip title="加粗">
          <button
            className={`h-7 w-7 flex items-center justify-center rounded-lg text-[11px] font-bold transition-colors ${
              rule.bold
                ? 'bg-black/[0.04] text-[#000000]'
                : 'bg-[#F5F5F4] text-[#525252] hover:bg-[#F5F5F4]'
            } disabled:opacity-50`}
            onClick={() => !disabled && onChange('bold', !rule.bold)}
            disabled={disabled}
          >
            B
          </button>
        </CendTooltip>

        {/* Color */}
        <CendTooltip title="字体颜色">
          <button
            className="h-7 w-7 flex items-center justify-center rounded-lg bg-[#F5F5F4] border border-[#E8E8E6] hover:border-[#000000] transition-colors disabled:opacity-50"
            onClick={() => !disabled && colorInputRef.current?.click()}
            disabled={disabled}
          >
            <span
              className="w-3.5 h-3.5 rounded-full border border-[#E8E8E6]"
              style={{ backgroundColor: rule.fontColor }}
            />
            <input
              ref={colorInputRef}
              type="color"
              className="absolute opacity-0 w-0 h-0"
              value={rule.fontColor}
              onChange={(e) => onChange('fontColor', e.target.value)}
              disabled={disabled}
            />
          </button>
        </CendTooltip>

        <div className="w-px h-5 bg-[#D4D4D4]" />

        {/* Alignment */}
        {ALIGNMENT_OPTIONS.map((a) => (
          <CendTooltip key={a.value} title={a.label + '对齐'}>
            <button
              className={`h-7 px-1.5 text-[10px] rounded-lg transition-colors ${
                rule.alignment === a.value
                  ? 'bg-black/[0.04] text-[#000000]'
                  : 'bg-[#F5F5F4] text-[#525252] hover:bg-[#F5F5F4]'
              } disabled:opacity-50`}
              onClick={() => !disabled && onChange('alignment', a.value)}
              disabled={disabled}
            >
              {a.label}
            </button>
          </CendTooltip>
        ))}

        <div className="w-px h-5 bg-[#D4D4D4]" />

        {/* Heading level */}
        <div className="relative">
          <select
            className="h-7 px-1.5 pr-6 text-[10px] border border-[#E8E8E6] rounded-lg bg-[#F5F5F4] text-[#000000] focus:outline-none focus:border-[#000000] disabled:opacity-60 appearance-none cursor-pointer"
            value={rule.heading}
            onChange={(e) => onChange('heading', e.target.value)}
            disabled={disabled}
          >
            <option value="">正文</option>
            <option value="h1">标题1</option>
            <option value="h2">标题2</option>
            <option value="h3">标题3</option>
          </select>
          <SelectArrow />
        </div>
      </div>
    </div>
  );
}

export default function FormatRulePanel({
  apiFetch,
  selectedDocId,
  onApplyRule,
  applyingRuleId,
}: Props) {
  const [rules, setRules] = useState<FormatRule[]>([]);
  const [loading, setLoading] = useState(false);
  const [editingRule, setEditingRule] = useState<FormatRule | null>(null);
  const [showDialog, setShowDialog] = useState(false);
  const [collapsed, setCollapsed] = useState(true);

  // Form state
  const [formName, setFormName] = useState('');
  const [formDesc, setFormDesc] = useState('');
  const [formStyleRules, setFormStyleRules] = useState<StyleRule[]>([]);
  const [formPermission, setFormPermission] = useState<'me' | 'team'>('me');
  const [readonly, setReadonly] = useState(false);
  const [deleteRuleTarget, setDeleteRuleTarget] = useState<FormatRule | null>(
    null,
  );
  const currentUserId = getCurrentUserId();

  const loadRules = useCallback(async () => {
    setLoading(true);
    try {
      const resp = await apiFetch('/api/v1/collaboration/format-rules');
      const result = await resp.json();
      if (result.code === 0) {
        setRules(result.data || []);
      }
    } catch (e) {
      console.error('加载格式规则失败:', e);
    } finally {
      setLoading(false);
    }
  }, [apiFetch]);

  useEffect(() => {
    loadRules();
  }, [loadRules]);

  const openCreate = () => {
    setEditingRule(null);
    setReadonly(false);
    setFormName('');
    setFormDesc('');
    setFormStyleRules(DEFAULT_STYLE_RULES.map((r) => ({ ...r })));
    setFormPermission('me');
    setShowDialog(true);
  };

  const openEdit = (rule: FormatRule, viewOnly = false) => {
    setEditingRule(rule);
    setReadonly(viewOnly);
    setFormName(rule.name);
    setFormDesc(rule.description || '');
    setFormPermission((rule.permission as 'me' | 'team') || 'me');
    const config = rule.config || {};
    if (Array.isArray(config.rules)) {
      setFormStyleRules(config.rules as StyleRule[]);
    } else {
      setFormStyleRules([
        {
          name: '正文',
          pattern: '.*',
          fontFamily: (config.font_name as string) || 'SimSun',
          fontSize: (config.font_size as number) || 12,
          fontColor: '#1C1917',
          alignment: 'left',
          bold: false,
          heading: '',
        },
      ]);
    }
    setShowDialog(true);
  };

  const handleSave = async () => {
    if (!formName.trim() || formStyleRules.length === 0) return;
    const config = { rules: formStyleRules };
    try {
      if (editingRule) {
        await apiFetch(`/api/v1/collaboration/format-rules/${editingRule.id}`, {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            name: formName,
            description: formDesc,
            config,
            permission: formPermission,
          }),
        });
      } else {
        await apiFetch('/api/v1/collaboration/format-rules', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            name: formName,
            description: formDesc,
            config,
            permission: formPermission,
          }),
        });
      }
      setShowDialog(false);
      loadRules();
    } catch (e) {
      console.error('保存格式规则失败:', e);
    }
  };

  const updateStyleRule = (
    index: number,
    field: keyof StyleRule,
    value: unknown,
  ) => {
    setFormStyleRules((prev) =>
      prev.map((r, i) => (i === index ? { ...r, [field]: value } : r)),
    );
  };

  const addStyleRule = () => {
    setFormStyleRules((prev) => [
      ...prev,
      {
        name: '新样式',
        pattern: '',
        fontFamily: 'SimSun',
        fontSize: 12,
        fontColor: '#1C1917',
        alignment: 'left' as const,
        bold: false,
        heading: '' as const,
      },
    ]);
  };

  const removeStyleRule = (index: number) => {
    setFormStyleRules((prev) => prev.filter((_, i) => i !== index));
  };

  const handleDelete = async () => {
    if (!deleteRuleTarget) return;
    try {
      await apiFetch(
        `/api/v1/collaboration/format-rules/${deleteRuleTarget.id}`,
        {
          method: 'DELETE',
        },
      );
      setDeleteRuleTarget(null);
      loadRules();
    } catch (e) {
      console.error('删除格式规则失败:', e);
    }
  };

  return (
    <>
      {/* Collapsible Rules Panel */}
      <div className="border-t border-[#E8E8E6]">
        <button
          className="w-full px-3 py-2 text-xs text-[#333333] hover:text-[#000000] hover:bg-[#F5F5F4] flex items-center gap-1.5 transition-colors"
          onClick={() => setCollapsed(!collapsed)}
        >
          <svg
            className="w-3.5 h-3.5"
            fill="none"
            stroke="currentColor"
            viewBox="0 0 24 24"
          >
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              strokeWidth={2}
              d="M4 6h16M4 12h16M4 18h7"
            />
          </svg>
          格式规则
          <svg
            className={`w-3 h-3 ml-auto transition-transform ${collapsed ? '' : 'rotate-180'}`}
            fill="none"
            stroke="currentColor"
            viewBox="0 0 24 24"
          >
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              strokeWidth={2}
              d="M19 9l-7 7-7-7"
            />
          </svg>
        </button>
        {!collapsed && (
          <div className="px-2 pb-2">
            <div className="flex items-center justify-between px-1 mb-1">
              <span className="text-[10px] text-[#333333]">
                {rules.length === 0 ? '暂无规则' : `${rules.length} 条规则`}
              </span>
              <button
                className="text-[10px] text-[#000000] hover:text-[#000000] font-medium"
                onClick={openCreate}
              >
                + 新建
              </button>
            </div>
            {loading ? (
              <div className="px-1 text-[10px] text-[#525252]">加载中...</div>
            ) : (
              <div className="space-y-0.5">
                {rules.map((rule) => (
                  <div
                    key={rule.id}
                    className="flex items-center justify-between px-1 py-1 rounded hover:bg-[#F5F5F4] group"
                  >
                    <CendTooltip
                      title={`${rule.name}${rule.description ? ` — ${rule.description}` : ''}`}
                    >
                      <span className="text-xs text-[#1a1a1a] truncate flex-1">
                        {rule.name}
                        {rule.permission === 'team' && (
                          <span className="ml-1 text-[9px] px-1 py-px rounded bg-[#fef3c7] text-[#d97706] border border-[#fde68a]">
                            团队
                          </span>
                        )}
                        {currentUserId &&
                          rule.created_by &&
                          rule.created_by !== currentUserId && (
                            <span className="ml-1 text-[9px] px-1 py-px rounded bg-[#F5F5F4] text-[#000000] border border-[#E8E8E6]">
                              共享
                            </span>
                          )}
                      </span>
                    </CendTooltip>
                    <div className="hidden group-hover:flex items-center gap-0.5">
                      {selectedDocId && onApplyRule && (
                        <button
                          className="text-[10px] text-[#000000] hover:text-[#000000] disabled:opacity-30 px-1"
                          disabled={applyingRuleId === rule.id}
                          onClick={() => onApplyRule(rule)}
                        >
                          {applyingRuleId === rule.id ? '...' : '应用'}
                        </button>
                      )}
                      {rule.created_by === currentUserId ? (
                        <>
                          <button
                            className="text-[10px] text-[#333333] hover:text-[#333333] px-1"
                            onClick={() => openEdit(rule)}
                          >
                            编辑
                          </button>
                          <button
                            className="text-[10px] text-[#333333] hover:text-[#ef4444] px-1"
                            onClick={() => setDeleteRuleTarget(rule)}
                          >
                            删除
                          </button>
                        </>
                      ) : (
                        <button
                          className="text-[10px] text-[#333333] hover:text-[#333333] px-1"
                          onClick={() => openEdit(rule, true)}
                        >
                          查看
                        </button>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}
      </div>

      {/* Create/Edit Dialog */}
      <CDialog open={showDialog} onOpenChange={setShowDialog}>
        <CDialogContent className="sm:max-w-md">
          <CDialogHeader>
            <CDialogTitle>
              {readonly
                ? '查看格式规则'
                : editingRule
                  ? '编辑格式规则'
                  : '新建格式规则'}
            </CDialogTitle>
          </CDialogHeader>
          <div className="space-y-4 py-4 max-h-[60vh] overflow-y-auto">
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="block text-sm font-medium text-[#333333] mb-1.5">
                  规则名称
                </label>
                <input
                  type="text"
                  className="w-full px-3 py-2.5 bg-[#FFFFFF] border border-[#E8E8E6] rounded-xl text-sm text-[#000000] placeholder:text-[#A3A3A3] focus:outline-none focus:border-[#000000] focus:bg-white focus:ring-2 focus:ring-[#D4D4D4] transition disabled:opacity-60"
                  value={formName}
                  onChange={(e) => setFormName(e.target.value)}
                  placeholder="例如：招标文件标准格式"
                  disabled={readonly}
                />
              </div>
              <div>
                <label className="block text-sm font-medium text-[#333333] mb-1.5">
                  描述
                </label>
                <input
                  type="text"
                  className="w-full px-3 py-2.5 bg-[#FFFFFF] border border-[#E8E8E6] rounded-xl text-sm text-[#000000] placeholder:text-[#A3A3A3] focus:outline-none focus:border-[#000000] focus:bg-white focus:ring-2 focus:ring-[#D4D4D4] transition disabled:opacity-60"
                  value={formDesc}
                  onChange={(e) => setFormDesc(e.target.value)}
                  placeholder="描述此规则的用途"
                  disabled={readonly}
                />
              </div>
            </div>

            {/* Permission */}
            {!readonly && (
              <div>
                <label className="block text-sm font-medium text-[#333333] mb-1.5">
                  可见范围
                </label>
                <div className="flex gap-2">
                  <button
                    className={`flex-1 px-3 py-2 rounded-xl text-sm font-medium border transition-colors ${
                      formPermission === 'me'
                        ? 'bg-[#F5F5F4] border-[#A3A3A3] text-[#000000]'
                        : 'border-[#E8E8E6] text-[#333333] hover:bg-[#F5F5F4]'
                    }`}
                    onClick={() => setFormPermission('me')}
                  >
                    仅自己
                  </button>
                  <button
                    className={`flex-1 px-3 py-2 rounded-xl text-sm font-medium border transition-colors ${
                      formPermission === 'team'
                        ? 'bg-[#F5F5F4] border-[#A3A3A3] text-[#000000]'
                        : 'border-[#E8E8E6] text-[#333333] hover:bg-[#F5F5F4]'
                    }`}
                    onClick={() => setFormPermission('team')}
                  >
                    团队共享
                  </button>
                </div>
              </div>
            )}

            {/* Style Rules */}
            <div>
              <div className="flex items-center justify-between mb-2">
                <span className="text-sm font-medium text-[#000000]">
                  样式列表
                </span>
                <span className="text-[10px] text-[#333333]">
                  从上到下优先匹配，第一个命中生效
                </span>
              </div>
              <div className="space-y-2">
                {formStyleRules.map((sr, i) => (
                  <StyleRuleEditor
                    key={i}
                    rule={sr}
                    index={i}
                    isLast={i === formStyleRules.length - 1}
                    onChange={(field, value) =>
                      updateStyleRule(i, field, value)
                    }
                    onRemove={() => removeStyleRule(i)}
                    disabled={readonly}
                  />
                ))}
              </div>
              {!readonly && (
                <button
                  className="mt-2 w-full py-2 border border-dashed border-[#A3A3A3] rounded-xl text-xs text-[#333333] hover:text-[#000000] hover:border-[#000000] transition-colors"
                  onClick={addStyleRule}
                >
                  + 添加样式
                </button>
              )}
            </div>

            {/* Preview hint */}
            <div className="bg-[#F5F5F4] rounded-xl p-3 text-xs text-[#333333]">
              <span className="font-medium">匹配示例：</span>
              {formStyleRules.map((sr, i) => (
                <span key={i} className="ml-2">
                  {i > 0 && ' → '}
                  <span className="text-[#000000]">{sr.name}</span>
                  <span className="text-[#333333]">
                    ({sr.pattern || '未设置'})
                  </span>
                </span>
              ))}
            </div>
          </div>
          <CDialogFooter>
            <button
              className="px-4 py-2.5 text-sm text-[#555555] hover:text-[#1A1A1A] hover:bg-[#F5F5F4] rounded-lg transition-colors"
              onClick={() => setShowDialog(false)}
            >
              {readonly ? '关闭' : '取消'}
            </button>
            {!readonly && (
              <button
                className="px-5 py-2.5 text-sm font-medium bg-[#1A1A1A] text-white rounded-lg hover:bg-[#333333] disabled:opacity-50 transition-colors"
                onClick={handleSave}
                disabled={!formName.trim()}
              >
                保存
              </button>
            )}
          </CDialogFooter>
        </CDialogContent>
      </CDialog>

      {/* Delete confirmation */}
      <AlertDialog
        open={!!deleteRuleTarget}
        onOpenChange={() => setDeleteRuleTarget(null)}
      >
        <AlertDialogContent className="!bg-white !border-[#E8E8E6] !shadow-[0_20px_60px_-12px_rgba(0,0,0,0.08)] !rounded-2xl">
          <AlertDialogHeader>
            <AlertDialogTitle className="!text-[#1A1A1A]">
              确认删除
            </AlertDialogTitle>
            <AlertDialogDescription className="!text-[#8A8A8A]">
              确定要删除格式规则「{deleteRuleTarget?.name}」吗？此操作不可撤销。
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel className="!border-[#E8E8E6] !text-[#555555] hover:!bg-[#F5F5F4] hover:!text-[#1A1A1A] !rounded-lg">
              取消
            </AlertDialogCancel>
            <AlertDialogAction
              onClick={handleDelete}
              className="!bg-red-500 hover:!bg-red-600 !text-white !rounded-lg"
            >
              确认删除
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  );
}
