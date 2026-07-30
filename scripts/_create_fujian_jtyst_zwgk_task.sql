SET NAMES utf8mb4;

INSERT INTO crawler_task (
  id, create_time, create_date, update_time, update_date,
  tenant_id, name, description, site_id, target_url,
  page_url_template, start_page, max_pages,
  extraction_schema, detail_config, headers,
  output_targets, kb_id, parser_id, enabled,
  last_run_time, last_run_status, last_run_summary
) VALUES (
  REPLACE(UUID(),'-',''),
  UNIX_TIMESTAMP()*1000, NOW(), UNIX_TIMESTAMP()*1000, NOW(),
  '7ab771d4dec84f23b2c1fb5f4e453ff9',
  '福建省交通运输厅-政务公开（6栏目）',
  '[one-shot] fujian_jtyst_zwgk: 交通要闻/公告通告/政策法规/财政资金/发展规划/信用资质 6 个栏目，YAML 配置驱动，每日 date_filter=today 增量',
  'fujian_jtyst_zwgk',
  'https://jtyst.fujian.gov.cn/zwgk/jtyw/',
  '', 1, 3,
  '{"baseSelector":".jtt-gl_list li","fields":[]}',
  '{"content_field":".TRS_Editor"}',
  '{}',
  '["db","kb"]',
  '3b4f619c85c211f198269135a1db216c',
  'naive',
  1,
  UNIX_TIMESTAMP()*1000,
  'success',
  '{"status":"success","pages":6,"items_found":149,"items_new":149,"kb_uploaded":149,"attachments_uploaded":94,"errors":[]}'
);

SELECT id, name, site_id, enabled FROM crawler_task WHERE site_id='fujian_jtyst_zwgk';
