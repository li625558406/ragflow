-- 福建省财政厅-政务公开 采集任务（智能采集 / policy）
-- site_id: fujian_czt_zwgk (YAML 配置已部署 + custom_runner 脚本)
-- kb_id:   3b4f619c85c211f198269135a1db216c
-- tenant:  7ab771d4dec84f23b2c1fb5f4e453ff9 (lg18629285296@163.com)
-- 入口:    https://czt.fujian.gov.cn/zwgk/
-- 6 栏目:  zcfg / tzgg / tjsj / czzj / ghjh / srdzxjyhtabl_60587
-- 抓取由 rag/svr/fujian_czt_zwgk_crawler.py 完成，YAML 仅做站点登记
SET NAMES utf8mb4;

-- 幂等：同 site_id 旧任务先删
DELETE FROM crawler_task WHERE site_id='fujian_czt_zwgk';

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
  '福建省财政厅-政务公开',                          -- name
  '福建省财政厅 政务公开首页（czt.fujian.gov.cn/zwgk/）6 栏目聚合采集任务。栏目: 政策文件(zcfg) / 通知公告(tzgg) / 统计数据(tjsj) / 财政资金(czzj) / 规划计划(ghjh) / 代表委员之声(srdzxjyhtabl_60587)。TRS CMS 静态 HTML，列表条目混合 HTML 详情页 (t*.htm) 与直链文件 (P0*.pdf/docx/zip)。custom_runner rag.svr.fujian_czt_zwgk_crawler 逐条分类处理。category=policy，首次回溯全量；后续 detector 触发带 date_filter=today 增量。',
  'fujian_czt_zwgk',                              -- site_id (匹配 YAML)
  'https://czt.fujian.gov.cn/zwgk/',              -- target_url
  '', 1, 1,                                       -- page_url_template / start_page / max_pages
  '{"baseSelector":"li","fields":[]}',            -- extraction_schema (YAML 占位)
  '{}',                                           -- detail_config
  '{}',                                           -- headers
  '["db","kb"]',                                  -- output_targets
  '3b4f619c85c211f198269135a1db216c',             -- kb_id
  'naive',                                        -- parser_id
  1,                                              -- enabled
  '',                                             -- last_run_status (NULL → 首次回溯全量)
  '{}'                                            -- last_run_summary
);

SELECT id, name, site_id, kb_id, enabled FROM crawler_task WHERE site_id='fujian_czt_zwgk';
