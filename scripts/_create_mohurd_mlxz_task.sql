-- 国家住建部-公开 采集任务（智能采集 / policy）
-- site_id: mohurd_mlxz (YAML 配置 + custom_runner 脚本)
-- kb_id:   3b4f619c85c211f198269135a1db216c
-- tenant:  7ab771d4dec84f23b2c1fb5f4e453ff9 (lg18629285296@163.com)
-- 入口:    https://www.mohurd.gov.cn/gongkai/mlxz/index.html
-- 内容:    2004-2023 年 20 个 PDF 信息公开目录，内含 ~12,000 条政策法规条目
-- 抓取:    rag/svr/mohurd_mlxz_crawler.py (custom_runner)
-- 数据流:  下载 PDF → PyMuPDF 提取超链接 → 逐条抓取详情页 → crawler_result + policy_ext + KB
SET NAMES utf8mb4;

-- 幂等：同 site_id 旧任务先删
DELETE FROM crawler_task WHERE site_id='mohurd_mlxz';

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
  '国家住建部-公开',                               -- name
  '住建部官网信息公开目录下载（www.mohurd.gov.cn/gongkai/mlxz/）。2004-2023 年共 20 个 PDF 目录文件，每个 PDF 内含 ~600 条政策公开条目（索引号/发文单位/文号/公文名称等元数据 + 详情页超链接）。custom_runner rag.svr.mohurd_mlxz_crawler 下载 PDF → PyMuPDF 提取超链接 → 逐条抓取详情页正文/附件。category=policy，写 collection_policy_ext。首次回溯全量；后续 detector 触发仅下载最新年份 PDF + date_filter=today 增量。',
  'mohurd_mlxz',                                  -- site_id (匹配 YAML)
  'https://www.mohurd.gov.cn/gongkai/mlxz/index.html',  -- target_url
  '', 1, 1,                                       -- page_url_template / start_page / max_pages
  '{"baseSelector":"a[href$=.pdf]","fields":[]}', -- extraction_schema (YAML 占位)
  '{}',                                           -- detail_config
  '{}',                                           -- headers
  '["db","kb"]',                                  -- output_targets
  '3b4f619c85c211f198269135a1db216c',             -- kb_id
  'naive',                                        -- parser_id
  1,                                              -- enabled
  '',                                             -- last_run_status (NULL → 首次回溯全量)
  '{}'                                            -- last_run_summary
);

SELECT id, name, site_id, kb_id, enabled FROM crawler_task WHERE site_id='mohurd_mlxz';
