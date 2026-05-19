import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { useCallback, useEffect, useState } from 'react';

interface FormatRule {
  id: string;
  name: string;
  description: string;
  config: Record<string, unknown>;
}

interface Props {
  apiFetch: (url: string, options?: RequestInit) => Promise<Response>;
}

export default function FormatRulePanel({ apiFetch }: Props) {
  const [rules, setRules] = useState<FormatRule[]>([]);
  const [loading, setLoading] = useState(false);
  const [editingRule, setEditingRule] = useState<FormatRule | null>(null);
  const [showDialog, setShowDialog] = useState(false);

  // Form state
  const [formName, setFormName] = useState('');
  const [formDesc, setFormDesc] = useState('');
  const [formFontName, setFormFontName] = useState('SimSun');
  const [formFontSize, setFormFontSize] = useState(12);
  const [formLineSpacing, setFormLineSpacing] = useState(1.5);

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
    setFormName('');
    setFormDesc('');
    setFormFontName('SimSun');
    setFormFontSize(12);
    setFormLineSpacing(1.5);
    setShowDialog(true);
  };

  const openEdit = (rule: FormatRule) => {
    setEditingRule(rule);
    setFormName(rule.name);
    setFormDesc(rule.description || '');
    const config = rule.config || {};
    setFormFontName((config.font_name as string) || 'SimSun');
    setFormFontSize((config.font_size as number) || 12);
    setFormLineSpacing((config.line_spacing as number) || 1.5);
    setShowDialog(true);
  };

  const handleSave = async () => {
    if (!formName.trim()) return;
    const config = {
      font_name: formFontName,
      font_size: formFontSize,
      line_spacing: formLineSpacing,
    };
    try {
      if (editingRule) {
        await apiFetch(`/api/v1/collaboration/format-rules/${editingRule.id}`, {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            name: formName,
            description: formDesc,
            config,
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
          }),
        });
      }
      setShowDialog(false);
      loadRules();
    } catch (e) {
      console.error('保存格式规则失败:', e);
    }
  };

  const handleDelete = async (ruleId: string) => {
    if (!window.confirm('确定删除此格式规则？')) return;
    try {
      await apiFetch(`/api/v1/collaboration/format-rules/${ruleId}`, {
        method: 'DELETE',
      });
      loadRules();
    } catch (e) {
      console.error('删除格式规则失败:', e);
    }
  };

  return (
    <>
      {/* Inline Rules List */}
      <div className="border-t border-stone-100">
        <div className="flex items-center justify-between px-3 py-2">
          <span className="text-xs font-medium text-stone-600">格式规则</span>
          <button
            className="text-[10px] text-indigo-500 hover:text-indigo-700 font-medium"
            onClick={openCreate}
          >
            + 新建
          </button>
        </div>
        {loading ? (
          <div className="px-3 pb-2 text-[10px] text-stone-400">加载中...</div>
        ) : rules.length === 0 ? (
          <div className="px-3 pb-2 text-[10px] text-stone-400">暂无规则</div>
        ) : (
          <div className="px-2 pb-2 space-y-0.5">
            {rules.map((rule) => (
              <div
                key={rule.id}
                className="flex items-center justify-between px-1 py-1 rounded hover:bg-stone-50 group"
              >
                <span className="text-xs text-stone-700 truncate flex-1">
                  {rule.name}
                </span>
                <div className="hidden group-hover:flex items-center gap-0.5">
                  <button
                    className="text-[10px] text-stone-400 hover:text-stone-600 px-1"
                    onClick={() => openEdit(rule)}
                  >
                    编辑
                  </button>
                  <button
                    className="text-[10px] text-stone-400 hover:text-red-500 px-1"
                    onClick={() => handleDelete(rule.id)}
                  >
                    删除
                  </button>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Create/Edit Dialog */}
      <Dialog open={showDialog} onOpenChange={setShowDialog}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>
              {editingRule ? '编辑格式规则' : '新建格式规则'}
            </DialogTitle>
          </DialogHeader>
          <div className="space-y-3 py-4">
            <div>
              <label className="block text-xs font-medium text-stone-700 mb-1">
                规则名称
              </label>
              <input
                type="text"
                className="w-full px-3 py-1.5 border border-stone-200 rounded text-sm text-stone-900 focus:outline-none focus:ring-2 focus:ring-indigo-500"
                value={formName}
                onChange={(e) => setFormName(e.target.value)}
              />
            </div>
            <div>
              <label className="block text-xs font-medium text-stone-700 mb-1">
                描述
              </label>
              <input
                type="text"
                className="w-full px-3 py-1.5 border border-stone-200 rounded text-sm text-stone-900 focus:outline-none focus:ring-2 focus:ring-indigo-500"
                value={formDesc}
                onChange={(e) => setFormDesc(e.target.value)}
              />
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="block text-xs font-medium text-stone-700 mb-1">
                  字体
                </label>
                <select
                  className="w-full px-2 py-1.5 border border-stone-200 rounded text-sm text-stone-900 focus:outline-none"
                  value={formFontName}
                  onChange={(e) => setFormFontName(e.target.value)}
                >
                  <option value="SimSun">宋体</option>
                  <option value="SimHei">黑体</option>
                  <option value="KaiTi">楷体</option>
                  <option value="FangSong">仿宋</option>
                  <option value="Arial">Arial</option>
                  <option value="Times New Roman">Times New Roman</option>
                </select>
              </div>
              <div>
                <label className="block text-xs font-medium text-stone-700 mb-1">
                  字号
                </label>
                <select
                  className="w-full px-2 py-1.5 border border-stone-200 rounded text-sm text-stone-900 focus:outline-none"
                  value={formFontSize}
                  onChange={(e) => setFormFontSize(Number(e.target.value))}
                >
                  <option value="10">五号 (10pt)</option>
                  <option value="12">小四 (12pt)</option>
                  <option value="14">四号 (14pt)</option>
                  <option value="16">小三 (16pt)</option>
                </select>
              </div>
            </div>
            <div>
              <label className="block text-xs font-medium text-stone-700 mb-1">
                行距: {formLineSpacing}
              </label>
              <input
                type="range"
                min="1"
                max="3"
                step="0.25"
                className="w-full"
                value={formLineSpacing}
                onChange={(e) => setFormLineSpacing(Number(e.target.value))}
              />
              <div className="flex justify-between text-[10px] text-stone-400">
                <span>1.0</span>
                <span>3.0</span>
              </div>
            </div>
          </div>
          <DialogFooter>
            <button
              className="px-4 py-2 text-sm text-stone-500 hover:text-stone-700"
              onClick={() => setShowDialog(false)}
            >
              取消
            </button>
            <button
              className="px-4 py-2 text-sm font-medium bg-indigo-500 text-white rounded-lg hover:bg-indigo-600 disabled:opacity-50"
              onClick={handleSave}
              disabled={!formName.trim()}
            >
              保存
            </button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
