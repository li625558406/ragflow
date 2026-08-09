import {
  getNotifSites,
  getSubscription,
  putSubscription,
  type SiteInfo,
  type Subscription,
} from '@/services/c-notification-service';
import { useEffect, useMemo, useState } from 'react';

interface Props {
  onClose: () => void;
}

const ALL_CATEGORIES = ['bid', 'policy', 'news', 'personnel', 'other'];

const CATEGORY_LABEL: Record<string, string> = {
  bid: '招标采购',
  policy: '政策法规',
  news: '新闻动态',
  personnel: '人事信息',
  other: '其他',
};

export function NotificationSettingsDialog({ onClose }: Props) {
  const [sub, setSub] = useState<Subscription>({
    site_ids: [],
    categories: [],
    browser_push: true,
    force_modal: true,
  });
  const [sites, setSites] = useState<SiteInfo[]>([]);
  const [sitesLoading, setSitesLoading] = useState(true);
  // 选中站点集合。语义：size === sites.length 表示"全选"，保存时转成 [] (后端"全订阅")。
  // 这样能正确区分"全选"和"恰好一个个选完"两种 UI 状态。
  const [checkedSites, setCheckedSites] = useState<Set<string>>(new Set());
  const [search, setSearch] = useState('');
  const [saving, setSaving] = useState(false);

  // 拉订阅 + 站点列表。订阅 site_ids 为空时 UI 显示全选。
  useEffect(() => {
    let cancelled = false;
    Promise.all([getSubscription(), getNotifSites()])
      .then(([s, { list }]) => {
        if (cancelled) return;
        setSub(s);
        setSites(list);
        // 后端 [] = 全订阅；非空 = 仅这些站点。
        const allIds = list.map((x) => x.site_id);
        setCheckedSites(new Set(s.site_ids.length === 0 ? allIds : s.site_ids));
      })
      .finally(() => !cancelled && setSitesLoading(false));
    return () => {
      cancelled = true;
    };
  }, []);

  const filteredSites = useMemo(() => {
    const kw = search.trim().toLowerCase();
    if (!kw) return sites;
    return sites.filter(
      (s) =>
        s.site_display.toLowerCase().includes(kw) ||
        s.site_id.toLowerCase().includes(kw),
    );
  }, [sites, search]);

  const allChecked = sites.length > 0 && checkedSites.size === sites.length;
  const noneChecked = checkedSites.size === 0;

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

  const toggleSite = (id: string) => {
    setCheckedSites((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const selectAll = () => setCheckedSites(new Set(sites.map((s) => s.site_id)));
  const selectNone = () => setCheckedSites(new Set());

  const handleSave = async () => {
    setSaving(true);
    try {
      // 语义转换：全选 → [] (后端"全订阅"语义更稳，避免站点列表后续扩充导致老用户漏收)
      const siteIdsToSave =
        checkedSites.size === sites.length ? [] : [...checkedSites];
      await putSubscription({ ...sub, site_ids: siteIdsToSave });
      onClose();
    } finally {
      setSaving(false);
    }
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40"
      onClick={onClose}
    >
      <div
        className="w-[640px] bg-white rounded-xl shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
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
                  {CATEGORY_LABEL[c] || c}
                </label>
              ))}
            </div>
          </div>

          <div className="space-y-1.5">
            <div className="flex items-center justify-between">
              <div className="text-sm font-medium">
                订阅站点（默认全选，共 {sites.length} 个）
              </div>
              <div className="flex items-center gap-3 text-xs">
                <button
                  type="button"
                  onClick={selectAll}
                  disabled={allChecked || sitesLoading}
                  className="text-blue-600 hover:text-blue-800 disabled:text-gray-300"
                >
                  全选
                </button>
                <button
                  type="button"
                  onClick={selectNone}
                  disabled={noneChecked || sitesLoading}
                  className="text-gray-500 hover:text-gray-800 disabled:text-gray-300"
                >
                  全不选
                </button>
              </div>
            </div>
            <input
              type="text"
              placeholder="🔍 搜索站点名称或 ID..."
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              className="w-full px-3 py-1.5 text-sm border rounded focus:outline-none focus:border-blue-400"
            />
            <div className="border rounded max-h-[40vh] overflow-y-auto">
              {sitesLoading && (
                <div className="px-3 py-4 text-center text-xs text-gray-400">
                  加载中…
                </div>
              )}
              {!sitesLoading && filteredSites.length === 0 && (
                <div className="px-3 py-4 text-center text-xs text-gray-400">
                  {search ? '无匹配站点' : '暂无采集站点'}
                </div>
              )}
              {!sitesLoading &&
                filteredSites.map((s) => {
                  const checked = checkedSites.has(s.site_id);
                  return (
                    <label
                      key={s.site_id}
                      className="flex items-center gap-2 px-3 py-1.5 text-sm hover:bg-gray-50 cursor-pointer border-b last:border-b-0"
                    >
                      <input
                        type="checkbox"
                        checked={checked}
                        onChange={() => toggleSite(s.site_id)}
                      />
                      <span className="flex-1 truncate">{s.site_display}</span>
                      <span className="text-xs text-gray-400 shrink-0">
                        {s.site_id}
                      </span>
                    </label>
                  );
                })}
            </div>
            <div className="text-xs text-gray-400">
              已选 {checkedSites.size} / {sites.length}
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
