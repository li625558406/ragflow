-- 福建省交通运输-公开事项 采集任务（智能采集 / other）
-- site_id: fujian_jtyst_zdgksxml (YAML 配置已部署，custom_runner 启用)
-- kb_id:   3b4f619c85c211f198269135a1db216c
-- tenant:  7ab771d4dec84f23b2c1fb5f4e453ff9 (lg18629285296@163.com)
-- 列表 API: GET /matterData/4028918175536a3b0175536f92043058.json (嵌套树, ~82 叶子)
SET NAMES utf8mb4;

-- 幂等：同 site_id 旧任务先删
DELETE FROM crawler_task WHERE site_id='fujian_jtyst_zdgksxml';

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
  '福建省交通运输-公开事项',                       -- name
  '福建省交通运输厅 重点公开事项清单（jtyst.fujian.gov.cn/zwgk/zfxxgkzl/zdgksxml/）采集任务。列表 API: GET /matterData/4028918175536a3b0175536f92043058.json 返回嵌套树（matterList → children → children），约 82 个叶子节点。YAML 启用 custom_runner=rag.svr.fujian_jtyst_zdgksxml_crawler，由该模块递归展开 + 抓取详情（.TRS_Editor + .article_attachment）。category=other，每条 news_type=福建省交通运输-公开事项。无日期字段，date_filter 不生效（全量 upsert 去重）。',
  'fujian_jtyst_zdgksxml',                        -- site_id (匹配 YAML)
  'https://jtyst.fujian.gov.cn/zwgk/zfxxgkzl/zdgksxml/',  -- target_url
  '', 1, 1,                                       -- page_url_template / start_page / max_pages
  '{"baseSelector":".matter-list","fields":[]}',  -- extraction_schema (YAML 占位)
  '{}',                                           -- detail_config
  '{}',                                           -- headers
  '["db","kb"]',                                  -- output_targets
  '3b4f619c85c211f198269135a1db216c',             -- kb_id
  'naive',                                        -- parser_id
  1,                                              -- enabled
  '',                                             -- last_run_status (NULL → 首次全量)
  '{}'                                            -- last_run_summary
);

SELECT id, name, site_id, kb_id, enabled FROM crawler_task WHERE site_id='fujian_jtyst_zdgksxml';
