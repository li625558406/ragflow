import { ExternalLink } from 'lucide-react';

interface LinkItem {
  name: string;
  url: string;
}

interface LinkGroup {
  title: string;
  items: LinkItem[];
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
  return (
    <div className="flex-1 overflow-auto">
      <div className="px-6 py-6 space-y-8 max-w-[1400px] mx-auto">
        {GROUPS.map((group) => (
          <section key={group.title}>
            <h2 className="text-sm font-semibold text-[#333333] mb-3 pb-2 border-b border-[#F0F0F0]">
              {group.title}
              <span className="ml-2 text-xs font-normal text-[#A3A3A3]">
                {group.items.length}
              </span>
            </h2>
            <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-3">
              {group.items.map((item) => (
                <a
                  key={item.url}
                  href={item.url}
                  target="_blank"
                  rel="noreferrer"
                  className="group/card flex items-start gap-2 p-3 rounded-xl border border-[#E8E8E8] bg-white hover:shadow-[0_2px_12px_rgba(0,0,0,0.06)] hover:border-[#D4D4D4] transition-all"
                >
                  <ExternalLink
                    className="size-3.5 shrink-0 mt-0.5 text-[#A3A3A3] group-hover/card:text-[#2563EB] transition-colors"
                    style={{ color: undefined }}
                  />
                  <span className="text-xs text-[#333333] leading-relaxed line-clamp-2 group-hover/card:text-[#000000] transition-colors">
                    {item.name}
                  </span>
                </a>
              ))}
            </div>
          </section>
        ))}
      </div>
    </div>
  );
}
