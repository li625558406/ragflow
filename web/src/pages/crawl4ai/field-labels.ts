/**
 * 扩展字段英文 key → 中文标签映射（按 category 分组）
 *
 * 用于详情弹窗渲染采集结果的扩展字段表格。
 * 与后端 collection_*_ext 表的字段名一一对应。
 * 空值字段在 UI 中跳过，这里只为可能出现的字段提供中文标签。
 */
export const EXT_FIELD_LABELS: Record<string, Record<string, string>> = {
  objection: {
    record_no: '编号',
    publication_time: '公示时间',
    tender_no: '招标编号',
    owner_unit: '业主单位',
    tender_agency: '招标代理机构',
    related_sections: '相关标段(包)',
    objector_name: '异议人名称',
    objected_party_name: '被异议人名称',
    objection_time: '异议时间',
    objection_type: '异议类型',
    objection_content: '异议内容',
    basis_and_reasons: '依据和理由',
    acceptance_time: '受理时间',
    processing_time: '处理时间',
    handling_opinion: '异议处理意见',
    processing_result: '处理结果',
    processing_basis: '处理依据',
  },
  policy: {
    doc_number: '文号',
    issuing_authority: '发文机关',
    authority_level: '效力级别',
    topic_category: '主题分类',
    effective_date: '生效日期',
    expiry_date: '失效日期',
    status: '状态',
    legal_basis: '法律依据',
  },
  personnel: {
    person_name: '姓名',
    id_card_masked: '身份证号',
    cert_no: '证书号',
    cert_type: '证书类型',
    employer: '聘用单位',
    specialty: '专业',
    position: '职务',
    valid_until: '有效期至',
    status: '状态',
  },
  announcement: {
    purchaser: '采购人',
    agency: '代理机构',
    openTenderCode: '项目编号',
    openTenderTime: '开标时间',
    budget: '预算金额',
    purchaseManner: '采购方式',
    catalogueNameList: '采购品目',
    regionName: '区域',
    noticeTypeName: '公告类型',
    publishTime: '发布时间',
    noticeTime: '公告时间',
    planId: '计划ID',
  },
  tender: {
    project_num: '项目编号',
    province: '省份',
    city: '城市',
    tender_type: '招标类型',
    news_type: '公告类型',
  },
  '国家文物局-政务公开': {
    doc_number: '文号',
    issuing_authority: '发文机关',
    authority_level: '效力级别',
    topic_category: '主题分类',
    effective_date: '生效日期',
    expiry_date: '失效日期',
    status: '状态',
    legal_basis: '法律依据',
  },
  '福建省文物局-政务公开': {
    source: '来源',
    publish_datetime: '发布时间',
    section_name: '栏目',
  },
};

/**
 * category → Badge 染色（Tailwind 类名）
 */
export const CATEGORY_COLORS: Record<string, string> = {
  bid: 'bg-blue-500/15 text-blue-600',
  policy: 'bg-cyan-500/15 text-cyan-600',
  personnel: 'bg-green-500/15 text-green-600',
  news: 'bg-amber-500/15 text-amber-600',
  other: 'bg-gray-500/15 text-gray-600',
  objection: 'bg-purple-500/15 text-purple-600',
  announcement: 'bg-orange-500/15 text-orange-600',
  tender: 'bg-teal-500/15 text-teal-600',
  '国家文物局-政务公开': 'bg-red-500/15 text-red-600',
  '福建省文物局-政务公开': 'bg-rose-500/15 text-rose-600',
};

/**
 * 多栏目站点的 section 染色调色板。
 * 当结果带 section_name（如 "示范文本"/"行业规范"/"培训资料"）时，
 * 按字符串哈希稳定取色，让同站不同栏目视觉上区分；
 * 不需要为每个站点硬编码颜色映射。
 */
const SECTION_COLOR_PALETTE: string[] = [
  'bg-blue-500/15 text-blue-600',
  'bg-emerald-500/15 text-emerald-600',
  'bg-amber-500/15 text-amber-600',
  'bg-purple-500/15 text-purple-600',
  'bg-pink-500/15 text-pink-600',
  'bg-cyan-500/15 text-cyan-600',
  'bg-indigo-500/15 text-indigo-600',
  'bg-rose-500/15 text-rose-600',
  'bg-teal-500/15 text-teal-600',
  'bg-orange-500/15 text-orange-600',
];

function _hashString(s: string): number {
  let h = 0;
  for (let i = 0; i < s.length; i++) {
    h = (h * 31 + s.charCodeAt(i)) | 0;
  }
  return Math.abs(h);
}

/**
 * 给定 category + section_name，返回 Badge 染色。
 * - 有 section_name：按 section_name 哈希到调色板（同站同栏目颜色稳定）
 * - 无 section_name：回退到 CATEGORY_COLORS[category]
 */
export function badgeColorFor(category: string, sectionName?: string): string {
  if (sectionName && sectionName.trim()) {
    return SECTION_COLOR_PALETTE[
      _hashString(sectionName.trim()) % SECTION_COLOR_PALETTE.length
    ];
  }
  return CATEGORY_COLORS[category] ?? '';
}
