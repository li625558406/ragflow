SET NAMES utf8mb4;
INSERT INTO crawler_task (
  id, tenant_id, name, site_id, target_url,
  start_page, max_pages, extraction_schema, detail_config, headers,
  output_targets, kb_id, parser_id, enabled,
  create_time, create_date, last_run_summary
) VALUES (
  REPLACE(UUID(), '-', ''),
  '7ab771d4dec84f23b2c1fb5f4e453ff9',
  '漳州市工程项目交易中心-工程信息',
  'gcjyzx_jyxx',
  'https://gcjyzx.zhangzhou.gov.cn/gcxx/jyxx.html',
  0, 30,
  '{"baseSelector":"#infoContentM","fields":[]}',
  '{}',
  '{}',
  '["db","kb"]',
  '3b4f619c85c211f198269135a1db216c',
  'naive',
  1,
  UNIX_TIMESTAMP()*1000, NOW(), '{}'
);
SELECT id, name, site_id, enabled FROM crawler_task WHERE site_id='gcjyzx_jyxx';
