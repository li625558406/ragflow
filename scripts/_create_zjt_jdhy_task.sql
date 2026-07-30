SET NAMES utf8mb4;
INSERT IGNORE INTO crawler_task (id, tenant_id, name, description, site_id, target_url,
  start_page, max_pages, extraction_schema, detail_config, headers,
  output_targets, kb_id, parser_id, enabled, last_run_summary)
VALUES (
  '5a5de279e63041dfb3c4ae7cd5991210',
  '7ab771d4dec84f23b2c1fb5f4e453ff9',
  '福建省住建厅-解读回应',
  '采集 zjt.fujian.gov.cn/jdhy/ 的回应关切和政策解读（含文字/图解/媒体报道/访谈）',
  'zjt_jdhy',
  'https://zjt.fujian.gov.cn/jdhy/',
  1, 1,
  '{"baseSelector": "ul li", "fields": []}',
  '{}',
  '{}',
  '["db","kb"]',
  '3b4f619c85c211f198269135a1db216c',
  'naive',
  1,
  '{}'
);
