import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { ExternalLink, Pencil, Plus, Trash2 } from 'lucide-react';
import { useCallback, useState } from 'react';

interface LinkItem {
  name: string;
  url: string;
}

interface LinkGroup {
  title: string;
  items: LinkItem[];
}

interface CustomLink {
  name: string;
  url: string;
  group: string;
}

const STORAGE_KEY = 'bid_custom_sites';
const HIDDEN_KEY = 'bid_hidden_builtins';

function loadCustomSites(): CustomLink[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    return raw ? JSON.parse(raw) : [];
  } catch {
    return [];
  }
}

function saveCustomSites(links: CustomLink[]) {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(links));
}

function loadHiddenBuiltins(): string[] {
  try {
    const raw = localStorage.getItem(HIDDEN_KEY);
    return raw ? JSON.parse(raw) : [];
  } catch {
    return [];
  }
}

function saveHiddenBuiltins(keys: string[]) {
  localStorage.setItem(HIDDEN_KEY, JSON.stringify(keys));
}

/** Stable key for a link (name+url) to identify built-in items. */
function linkKey(name: string, url: string) {
  return `${name}||${url}`;
}

const GROUPS: LinkGroup[] = [
  {
    title: '招投标交易平台',
    items: [
      {
        name: '福建省公共资源交易电子行政监督平台',
        url: 'https://ggzyjd.fj.gov.cn/index/new',
      },
      {
        name: '福建省公共资源交易电子公共服务平台',
        url: 'https://ggzyfw.fujian.gov.cn/web/index.html#/index/new',
      },
      { name: '福建省政府采购网', url: 'http://zfcg.czt.fujian.gov.cn/' },
      { name: '中国政府采购网', url: 'http://www.ccgp.gov.cn/' },
      {
        name: '随行易交易电子招标投标交易平台',
        url: 'https://www.enjoy5191.com/',
      },
      { name: '新点电子交易平台福建专区', url: 'https://fujian.etrading.cn/' },
      { name: '中国采购与招标网', url: 'http://www.chinabidding.com.cn/' },
      {
        name: '中国招标投标公共服务平台',
        url: 'https://bulletin.cebpubservice.com/',
      },
      { name: '福建省国资采购平台', url: 'https://ygcg.fjcqjy.com/' },
      { name: '福易采漳州分中心', url: 'http://zz.fycbid.cn/' },
      { name: '工采通电子招投标交易平台', url: 'https://easy-prt.com/policy' },
      {
        name: '中央政府采购网',
        url: 'https://www.zycg.gov.cn/freecms/site/zygjjgzfcgzx/index.html',
      },
      { name: '全国公共资源交易平台', url: 'https://www.ggzy.gov.cn/' },
      {
        name: '福建省综合评标专家库',
        url: 'https://zjk.ggzyfw.fujian.gov.cn/',
      },
    ],
  },
  {
    title: '福建省各地市公共资源交易中心',
    items: [
      { name: '福州公共资源交易网', url: 'https://fzsggzyjyfwzx.cn/' },
      {
        name: '泉州市公共资源交易信息网',
        url: 'http://ggzyjy.quanzhou.gov.cn/default/homePage.do',
      },
      {
        name: '莆田市公共资源交易中心',
        url: 'http://ggzyjy.xzfwzx.putian.gov.cn/fwzx/',
      },
      {
        name: '宁德市公共资源交易中心',
        url: 'https://ggzyjy.xgw.ningde.gov.cn/',
      },
      { name: '三明市公共资源交易网', url: 'https://smggzy.sm.gov.cn/smwz/' },
      {
        name: '龙岩公共资源交易中心',
        url: 'https://ggzy.longyan.gov.cn/lyztb/',
      },
      { name: '厦门市公共资源交易网', url: 'https://zyjy.as.xm.gov.cn/' },
      {
        name: '平潭综合实验区公共资源统一平台',
        url: 'https://xzfwzx.pingtan.gov.cn:9999/#',
      },
      {
        name: '漳州公共资源交易中心',
        url: 'http://ggzyjy.xzfwzx.zhangzhou.gov.cn/',
      },
    ],
  },
  {
    title: '漳州本地平台',
    items: [
      {
        name: '漳州市工程项目网上中介服务平台',
        url: 'http://zjfw.zhangzhou.gov.cn/imng/zjfw',
      },
      {
        name: '漳州市工程项目交易中心',
        url: 'https://gcjyzx.zhangzhou.gov.cn/',
      },
      {
        name: '漳州市人民政府',
        url: 'https://www.zhangzhou.gov.cn/cms/html/zzsrmzf/index.html',
      },
      {
        name: '漳州市发展和改革委员会',
        url: 'http://fgw.zhangzhou.gov.cn/cms/html/zzsfzhggwyh/index.html',
      },
      {
        name: '漳州市住房和城乡建设局',
        url: 'http://jsj.zhangzhou.gov.cn/cms/html/zzszfhcxjsj/index.html',
      },
      {
        name: '南靖县人民政府',
        url: 'http://www.fjnj.gov.cn/cms/html/njxrmzf/index.html',
      },
    ],
  },
  {
    title: '福建省政府部门',
    items: [
      { name: '福建省住房和城乡建设厅', url: 'https://zjt.fujian.gov.cn/' },
      { name: '福建省发展和改革委员会', url: 'http://fgw.fujian.gov.cn/' },
      { name: '福建省交通运输厅', url: 'https://jtyst.fujian.gov.cn/' },
      { name: '福建省水利厅', url: 'https://slt.fujian.gov.cn/' },
      { name: '福建省财政厅', url: 'https://czt.fujian.gov.cn/' },
      { name: '福建省文物局', url: 'http://wwj.wlt.fujian.gov.cn/' },
      { name: '福建省招标投标协会', url: 'http://www.fjtba.com/' },
      { name: '福建省建筑业协会', url: 'http://www.fjcia.org/' },
      {
        name: '福建省水利建设市场信用管理平台',
        url: 'http://27.156.118.74:18001/#/statisticsData',
      },
    ],
  },
  {
    title: '国家部委',
    items: [
      {
        name: '中华人民共和国住房和城乡建设部',
        url: 'https://www.mohurd.gov.cn/',
      },
      {
        name: '中华人民共和国国家发展和改革委员会',
        url: 'https://www.ndrc.gov.cn/',
      },
      { name: '中华人民共和国水利部', url: 'http://www.mwr.gov.cn/' },
      { name: '国家文物局', url: 'http://www.ncha.gov.cn/' },
    ],
  },
  {
    title: '信用信息查询',
    items: [
      { name: '信用中国', url: 'https://www.creditchina.gov.cn/' },
      {
        name: '全国法院失信被执行人名单信息公布与查询',
        url: 'https://zxgk.court.gov.cn/shixin/',
      },
      {
        name: '严重失信主体名单查询',
        url: 'https://www.creditchina.gov.cn/xinxigongshi/shixinheimingdan/',
      },
      {
        name: '重大税收违法失信主体信息公示',
        url: 'https://www.creditchina.gov.cn/zhuanxiangchaxun/zhongdashuishouweifaanjian/',
      },
      {
        name: '政府采购严重违法失信行为记录名单',
        url: 'http://www.ccgp.gov.cn/search/cr/',
      },
    ],
  },
  {
    title: '资质/业绩查询',
    items: [
      {
        name: '全国建筑市场监管公共服务平台（四库一平台）',
        url: 'https://jzsc.mohurd.gov.cn/home',
      },
      {
        name: '全国公路建设市场监督管理系统',
        url: 'https://hwdms.mot.gov.cn/BMWebSite/',
      },
      {
        name: '资质/人员/业绩查询-鲁班乐标',
        url: 'https://jy.lubanlebiao.com/query',
      },
      { name: '建设通-建筑业大数据服务平台', url: 'https://jst.cbi360.net/' },
    ],
  },
];

export default function BidHome() {
  const [customSites, setCustomSites] = useState<CustomLink[]>(loadCustomSites);
  const [hiddenBuiltins, setHiddenBuiltins] =
    useState<string[]>(loadHiddenBuiltins);
  const hiddenSet = new Set(hiddenBuiltins);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [editId, setEditId] = useState<string | null>(null);
  const [editingBuiltinKey, setEditingBuiltinKey] = useState<string | null>(
    null,
  );
  const [newName, setNewName] = useState('');
  const [newUrl, setNewUrl] = useState('');
  const [newGroup, setNewGroup] = useState(GROUPS[0].title);
  const [customGroupName, setCustomGroupName] = useState('');
  const [isNewGroup, setIsNewGroup] = useState(false);
  const [formError, setFormError] = useState('');
  const [deleteTarget, setDeleteTarget] = useState<{
    name: string;
    url: string;
    groupTitle: string;
  } | null>(null);

  const isEditing = editId !== null || editingBuiltinKey !== null;

  const mergedGroups = (() => {
    const groupMap = new Map<string, LinkItem[]>();
    for (const g of GROUPS) {
      const visible = g.items.filter(
        (it) => !hiddenSet.has(linkKey(it.name, it.url)),
      );
      if (visible.length) {
        groupMap.set(g.title, visible);
      }
    }
    for (const site of customSites) {
      const items = groupMap.get(site.group) || [];
      items.push({ name: site.name, url: site.url });
      if (!groupMap.has(site.group)) {
        groupMap.set(site.group, items);
      }
    }
    return Array.from(groupMap, ([title, items]) => ({ title, items }));
  })();

  const groupNames = GROUPS.map((g) => g.title);

  const resetForm = useCallback(() => {
    setNewName('');
    setNewUrl('');
    setNewGroup(GROUPS[0].title);
    setCustomGroupName('');
    setIsNewGroup(false);
    setFormError('');
    setEditId(null);
    setEditingBuiltinKey(null);
  }, []);

  const handleSave = () => {
    const name = newName.trim();
    const url = newUrl.trim();
    if (!name || !url) {
      setFormError('网站名称和网址不能为空');
      return;
    }
    if (!/^https?:\/\//i.test(url)) {
      setFormError('网址需以 http:// 或 https:// 开头');
      return;
    }
    const group = isNewGroup ? customGroupName.trim() : newGroup;
    if (!group) {
      setFormError('请选择或输入所属分组');
      return;
    }

    if (isEditing) {
      if (editingBuiltinKey) {
        // Editing a built-in item: hide original + save as custom
        const updatedHidden = [...hiddenBuiltins, editingBuiltinKey];
        saveHiddenBuiltins(updatedHidden);
        setHiddenBuiltins(updatedHidden);
        const updated = [...customSites, { name, url, group }];
        saveCustomSites(updated);
        setCustomSites(updated);
      } else {
        // Update existing custom site
        const updated = customSites.map((s, i) =>
          i.toString() === editId ? { name, url, group } : s,
        );
        saveCustomSites(updated);
        setCustomSites(updated);
      }
    } else {
      // Add new
      const updated = [...customSites, { name, url, group }];
      saveCustomSites(updated);
      setCustomSites(updated);
    }
    setDialogOpen(false);
    resetForm();
  };

  const openEdit = (
    name: string,
    url: string,
    groupTitle: string,
    isBuiltin: boolean,
    customIndex?: number,
  ) => {
    if (isBuiltin) {
      setEditId(null);
      setEditingBuiltinKey(linkKey(name, url));
      setNewGroup(groupTitle);
      setIsNewGroup(false);
    } else {
      setEditId(customIndex!.toString());
      setEditingBuiltinKey(null);
      const builtinGroup = GROUPS.some((g) => g.title === groupTitle);
      if (builtinGroup) {
        setNewGroup(groupTitle);
        setIsNewGroup(false);
      } else {
        setCustomGroupName(groupTitle);
        setIsNewGroup(true);
      }
    }
    setNewName(name);
    setNewUrl(url);
    setFormError('');
    setDialogOpen(true);
  };

  const confirmDelete = useCallback(() => {
    if (!deleteTarget) return;
    const { name, url, groupTitle } = deleteTarget;
    const isBuiltin = GROUPS.some(
      (g) =>
        g.title === groupTitle &&
        g.items.some((it) => it.name === name && it.url === url),
    );
    if (isBuiltin) {
      const key = linkKey(name, url);
      const updated = [...hiddenBuiltins, key];
      saveHiddenBuiltins(updated);
      setHiddenBuiltins(updated);
    } else {
      const updated = customSites.filter(
        (s) => !(s.name === name && s.url === url && s.group === groupTitle),
      );
      saveCustomSites(updated);
      setCustomSites(updated);
    }
    setDeleteTarget(null);
  }, [deleteTarget, customSites, hiddenBuiltins]);

  return (
    <>
      <div className="flex-1 overflow-auto">
        <div className="px-6 py-6 space-y-8 max-w-[1400px] mx-auto">
          {/* Header */}
          <div className="flex items-center justify-between">
            <h1 className="text-base font-bold text-[#000000]">常用网站导航</h1>
            <Button
              size="default"
              onClick={() => {
                resetForm();
                setDialogOpen(true);
              }}
              className="gap-1.5 bg-[#000000] hover:bg-[#171717] text-white text-sm font-medium rounded-lg"
            >
              <Plus className="size-4" />
              新增网站
            </Button>
          </div>

          {mergedGroups.map((group) => (
            <section key={group.title}>
              <h2 className="text-sm font-semibold text-[#333333] mb-3 pb-2 border-b border-[#F0F0F0]">
                {group.title}
                <span className="ml-2 text-xs font-normal text-[#A3A3A3]">
                  {group.items.length}
                </span>
              </h2>
              <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-3">
                {group.items.map((item) => (
                  <div
                    key={item.url}
                    className="group/card relative flex items-start gap-2 p-3 rounded-xl border border-[#E8E8E8] bg-white hover:shadow-[0_2px_12px_rgba(0,0,0,0.06)] hover:border-[#D4D4D4] transition-all"
                  >
                    <a
                      href={item.url}
                      target="_blank"
                      rel="noreferrer"
                      className="flex items-start gap-2 flex-1 min-w-0"
                    >
                      <ExternalLink
                        className="size-3.5 shrink-0 mt-0.5 text-[#A3A3A3] group-hover/card:text-[#2563EB] transition-colors"
                        style={{ color: undefined }}
                      />
                      <span className="text-xs text-[#333333] leading-relaxed line-clamp-2 group-hover/card:text-[#000000] transition-colors">
                        {item.name}
                      </span>
                    </a>
                    <div className="flex items-center gap-0.5">
                      <button
                        onClick={() => {
                          const isBuiltin = GROUPS.some(
                            (g) =>
                              g.title === group.title &&
                              g.items.some(
                                (it) =>
                                  it.name === item.name && it.url === item.url,
                              ),
                          );
                          if (isBuiltin) {
                            openEdit(item.name, item.url, group.title, true);
                          } else {
                            const idx = customSites.findIndex(
                              (s) =>
                                s.name === item.name &&
                                s.url === item.url &&
                                s.group === group.title,
                            );
                            openEdit(
                              item.name,
                              item.url,
                              group.title,
                              false,
                              idx,
                            );
                          }
                        }}
                        className="shrink-0 size-5 flex items-center justify-center rounded text-[#A3A3A3] hover:text-[#2563EB] hover:bg-blue-50 transition-colors"
                        title="编辑"
                      >
                        <Pencil className="size-3" />
                      </button>
                      <button
                        onClick={() =>
                          setDeleteTarget({
                            name: item.name,
                            url: item.url,
                            groupTitle: group.title,
                          })
                        }
                        className="shrink-0 size-5 flex items-center justify-center rounded text-[#A3A3A3] hover:text-red-500 hover:bg-red-50 transition-colors"
                        title="删除"
                      >
                        <Trash2 className="size-3" />
                      </button>
                    </div>
                  </div>
                ))}
              </div>
            </section>
          ))}
        </div>
      </div>

      {/* Delete confirm dialog */}
      <Dialog
        open={deleteTarget !== null}
        onOpenChange={(open) => {
          if (!open) setDeleteTarget(null);
        }}
      >
        <DialogContent className="sm:max-w-[360px]">
          <DialogHeader>
            <DialogTitle>确认删除</DialogTitle>
          </DialogHeader>
          <p className="text-sm text-[#525252]">
            确定要删除「{deleteTarget?.name}」吗？此操作不可撤销。
          </p>
          <DialogFooter>
            <Button variant="outline" onClick={() => setDeleteTarget(null)}>
              取消
            </Button>
            <Button
              onClick={confirmDelete}
              className="bg-red-500 hover:bg-red-600 text-white"
            >
              确认删除
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Add / Edit dialog */}
      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent className="sm:max-w-[420px]">
          <DialogHeader>
            <DialogTitle>{isEditing ? '修改网站' : '新增网站'}</DialogTitle>
          </DialogHeader>
          <div className="space-y-4 py-2">
            <div className="space-y-1.5">
              <Label htmlFor="site-name">网站名称</Label>
              <Input
                id="site-name"
                value={newName}
                onChange={(e) => {
                  setNewName(e.target.value);
                  setFormError('');
                }}
                placeholder="例如：住房和城乡建设部"
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="site-url">网址</Label>
              <Input
                id="site-url"
                value={newUrl}
                onChange={(e) => {
                  setNewUrl(e.target.value);
                  setFormError('');
                }}
                placeholder="https://www.example.com"
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="site-group">所属分组</Label>
              {!isNewGroup ? (
                <div className="flex gap-2">
                  <select
                    id="site-group"
                    value={newGroup}
                    onChange={(e) => {
                      const val = e.target.value;
                      if (val === '__new__') {
                        setIsNewGroup(true);
                        setCustomGroupName('');
                      } else {
                        setNewGroup(val);
                      }
                      setFormError('');
                    }}
                    className="flex-1 h-9 rounded-md border border-input bg-background px-3 py-1 text-sm shadow-sm transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
                  >
                    {groupNames.map((g) => (
                      <option key={g} value={g}>
                        {g}
                      </option>
                    ))}
                    <option value="__new__">+ 新建分组...</option>
                  </select>
                </div>
              ) : (
                <div className="flex gap-2">
                  <Input
                    value={customGroupName}
                    onChange={(e) => {
                      setCustomGroupName(e.target.value);
                      setFormError('');
                    }}
                    placeholder="输入新分组名称"
                    className="flex-1"
                  />
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => {
                      setIsNewGroup(false);
                      setNewGroup(GROUPS[0].title);
                    }}
                  >
                    取消
                  </Button>
                </div>
              )}
            </div>
            {formError && <p className="text-sm text-red-500">{formError}</p>}
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setDialogOpen(false)}>
              取消
            </Button>
            <Button onClick={handleSave}>
              {isEditing ? '保存修改' : '确定添加'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
