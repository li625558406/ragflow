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
};
