SET NAMES utf8mb4;

INSERT INTO crawler_task (
  id, create_time, create_date, update_time, update_date,
  tenant_id, name, description, site_id, target_url,
  start_page, max_pages, extraction_schema, detail_config, headers,
  output_targets, kb_id, parser_id, enabled, last_run_summary
) VALUES (
  '22e9dda8931148baa8e24a3209846adf',
  UNIX_TIMESTAMP()*1000, NOW(),
  UNIX_TIMESTAMP()*1000, NOW(),
  '7ab771d4dec84f23b2c1fb5f4e453ff9',
  '福建省水利局-解读回应',
  '福建省水利厅 政策解读栏目（4 个子栏目：回应关切/部门政策解读/省级政府政策解读/其他政策解读）',
  'fujian_slt_zcjd',
  'https://slt.fujian.gov.cn/xxgk/zcjd/',
  1, 1,
  '{"baseSelector":".xw-list-1 li","fields":[]}',
  '{}',
  '{}',
  '["db","kb"]',
  '3b4f619c85c211f198269135a1db216c',
  'naive',
  1,
  '{}'
);
