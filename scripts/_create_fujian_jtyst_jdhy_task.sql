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
  '福建省交通运输-互动栏目（解读回应 + 回应关切）',
  '[one-shot] fujian_jtyst_jdhy: 解读回应/回应关切 2 个栏目，YAML 配置驱动，每日 date_filter=today 增量；首次回溯全量',
  'fujian_jtyst_jdhy',
  'https://jtyst.fujian.gov.cn/jdhy/zcjd/',
  '', 1, 1,
  '{"baseSelector":".jtt-gl_list li","fields":[]}',
  '{"content_field":".TRS_Editor"}',
  '{}',
  '["db","kb"]',
  '3b4f619c85c211f198269135a1db216c',
  'naive',
  1,
  UNIX_TIMESTAMP()*1000,
  '',
  '{}'
);

SELECT id, name, site_id, enabled FROM crawler_task WHERE site_id='fujian_jtyst_jdhy';
