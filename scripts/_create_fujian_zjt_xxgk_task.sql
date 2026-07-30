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
  '福建省住建厅-政务公开（10栏目：新闻动态/通知公告/最新文件/资金信息/人事信息/规划计划/资格管理/资质管理/人大建议政协提案/数据图解OCR）',
  '[spa_render] fujian_zjt_xxgk: 10 个栏目聚合采集，YAML 配置驱动，每日 date_filter=today 增量；首次回溯全量',
  'fujian_zjt_xxgk',
  'https://zjt.fujian.gov.cn/xxgk/',
  '', 1, 1,
  '{"baseSelector":"body","fields":[]}',
  '{"content_field":"#detailCont"}',
  '{}',
  '["db","kb"]',
  '3b4f619c85c211f198269135a1db216c',
  'naive',
  1,
  UNIX_TIMESTAMP()*1000,
  '',
  '{}'
);

SELECT id, name, site_id, enabled FROM crawler_task WHERE site_id='fujian_zjt_xxgk';
