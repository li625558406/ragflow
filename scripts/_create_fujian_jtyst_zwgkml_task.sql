-- 福建省交通运输-公告通知 采集任务（智能采集 / announcement）
-- site_id: fujian_jtyst_zwgkml (YAML 配置已部署)
-- kb_id:   3b4f619c85c211f198269135a1db216c
-- tenant:  7ab771d4dec84f23b2c1fb5f4e453ff9 (lg18629285296@163.com)
-- 列表 API: POST /fjdzapp/search  channelid=229105  classsql=(parentid=8754)  prepage=50
SET NAMES utf8mb4;

-- 幂等：同 site_id 旧任务先删
DELETE FROM crawler_task WHERE site_id='fujian_jtyst_zwgkml';

INSERT INTO crawler_task (
  id, create_time, create_date, update_time, update_date,
  tenant_id, name, description, site_id, target_url,
  page_url_template, start_page, max_pages,
  extraction_schema, detail_config, headers,
  output_targets, kb_id, parser_id, enabled,
  last_run_status, last_run_summary
) VALUES (
  REPLACE(UUID(), '-', ''),                       -- id
  UNIX_TIMESTAMP() * 1000, NOW(),                 -- create_time / create_date
  UNIX_TIMESTAMP() * 1000, NOW(),                 -- update_time / update_date
  '7ab771d4dec84f23b2c1fb5f4e453ff9',             -- tenant_id
  '福建省交通运输-公告通知',                       -- name
  '福建省交通运输厅 政府信息公开目录（jtyst.fujian.gov.cn/zwgk/zfxxgkzl/zfxxgkml/）采集任务。POST /fjdzapp/search channelid=229105，classsql=(parentid=8754) 取首页 50 条；详情页 .TRS_Editor 抽正文，.article_attachment 与列表 files[]._href 互备拾取附件。category=announcement，首次回溯全量；后续 detector 触发带 date_filter=today 增量。',
  'fujian_jtyst_zwgkml',                          -- site_id (匹配 YAML)
  'https://jtyst.fujian.gov.cn/zwgk/zfxxgkzl/zfxxgkml/',  -- target_url
  '', 1, 1,                                       -- page_url_template / start_page / max_pages
  '{"baseSelector":".fjdzapp-list-item","fields":[]}',   -- extraction_schema (YAML 占位)
  '{}',                                           -- detail_config
  '{}',                                           -- headers
  '["db","kb"]',                                  -- output_targets
  '3b4f619c85c211f198269135a1db216c',             -- kb_id
  'naive',                                        -- parser_id
  1,                                              -- enabled
  '',                                             -- last_run_status (NULL → 首次回溯全量)
  '{}'                                            -- last_run_summary
);

SELECT id, name, site_id, kb_id, enabled FROM crawler_task WHERE site_id='fujian_jtyst_zwgkml';
