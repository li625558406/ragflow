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
  '福建省财政厅-解读回应（回应关切 + 政策文件解读/图片解读/视频解读）',
  '[rest_api] fujian_czt_jdhy: 4 个栏目 (hygq/bmzcwjjd/tpjd/spjd)，YAML 配置驱动，每日 date_filter=today 增量；首次回溯全量',
  'fujian_czt_jdhy',
  'https://czt.fujian.gov.cn/jdhy/',
  '', 1, 1,
  '{"baseSelector":".list_base_date_01 li","fields":[]}',
  '{"content_field":".article_content"}',
  '{}',
  '["db","kb"]',
  '3b4f619c85c211f198269135a1db216c',
  'naive',
  1,
  UNIX_TIMESTAMP()*1000,
  '',
  '{}'
);

SELECT id, name, site_id, enabled FROM crawler_task WHERE site_id='fujian_czt_jdhy';
