import {
  getSubscription,
  putSubscription,
  type Subscription,
} from '@/services/c-notification-service';
import { useEffect, useState } from 'react';

interface Props {
  onClose: () => void;
}

const ALL_CATEGORIES = ['bid', 'policy', 'news', 'personnel', 'other'];

export function NotificationSettingsDialog({ onClose }: Props) {
  const [sub, setSub] = useState<Subscription>({
    site_ids: [],
    categories: [],
    browser_push: true,
    force_modal: true,
  });
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    getSubscription().then(setSub);
  }, []);

  const toggleCategory = (c: string) => {
    setSub((s) => {
      const has = s.categories.includes(c);
      return {
        ...s,
        categories: has
          ? s.categories.filter((x) => x !== c)
          : [...s.categories, c],
      };
    });
  };

  const handleSave = async () => {
    setSaving(true);
    try {
      await putSubscription(sub);
      onClose();
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40">
      <div className="w-[460px] bg-white rounded-xl shadow-2xl">
        <div className="flex items-center justify-between px-5 py-3 border-b">
          <span className="font-semibold">通知订阅设置</span>
          <button
            onClick={onClose}
            className="text-gray-400 hover:text-gray-700"
          >
            ×
          </button>
        </div>
        <div className="px-5 py-4 space-y-4">
          <div className="space-y-1">
            <div className="text-sm font-medium">订阅分类（空 = 全订阅）</div>
            <div className="flex flex-wrap gap-2">
              {ALL_CATEGORIES.map((c) => (
                <label key={c} className="flex items-center gap-1 text-sm">
                  <input
                    type="checkbox"
                    checked={sub.categories.includes(c)}
                    onChange={() => toggleCategory(c)}
                  />
                  {c}
                </label>
              ))}
            </div>
          </div>
          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={sub.browser_push}
              onChange={(e) =>
                setSub((s) => ({ ...s, browser_push: e.target.checked }))
              }
            />
            启用浏览器原生推送
          </label>
          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={sub.force_modal}
              onChange={(e) =>
                setSub((s) => ({ ...s, force_modal: e.target.checked }))
              }
            />
            启用强制弹框
          </label>
        </div>
        <div className="flex justify-end gap-2 px-5 py-3 border-t">
          <button
            onClick={onClose}
            className="px-4 py-1.5 text-sm border rounded hover:bg-gray-50"
          >
            取消
          </button>
          <button
            onClick={handleSave}
            disabled={saving}
            className="px-4 py-1.5 text-sm bg-blue-600 text-white rounded hover:bg-blue-700 disabled:opacity-50"
          >
            {saving ? '保存中…' : '保存'}
          </button>
        </div>
      </div>
    </div>
  );
}
