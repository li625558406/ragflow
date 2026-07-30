SET NAMES utf8mb4;

-- 删除已存在的同 id 任务（幂等）
DELETE FROM crawler_task WHERE id='a1f1b2c3d4e5f60708090a0b0c0d0e01';

INSERT INTO crawler_task (
  id, create_time, create_date, update_time, update_date,
  tenant_id, name, description, site_id, target_url,
  start_page, max_pages, extraction_schema, detail_config, headers,
  output_targets, kb_id, parser_id, enabled,
  last_run_time, last_run_status, last_run_summary
) VALUES (
  'a1f1b2c3d4e5f60708090a0b0c0d0e01',
  UNIX_TIMESTAMP()*1000, NOW(), UNIX_TIMESTAMP()*1000, NOW(),
  '7ab771d4dec84f23b2c1fb5f4e453ff9',
  '福建省交通运输厅-交通动态',
  '福建省交通运输厅交通动态栏目（chnlid=8700,8701,8702,8703 合并：交通要闻/工作动态/政策文件/媒体视点）。列表 GET /was5/web/search 按 pubtime 取当天数据；详情页 .TRS_Editor 静态 HTML；附件由 engine._extract_files_from_item 自动扫描。YAML 驱动，detector 自动增量。',
  'fujian_jtyst_dt',
  'https://jtyst.fujian.gov.cn/zwgk/ztyw/',
  1, 3,
  '{"baseSelector":".list-item","fields":[]}',
  '{}',
  '{"User-Agent":"Mozilla/5.0 Chrome/125","Referer":"https://jtyst.fujian.gov.cn/zwgk/ztyw/"}',
  '["db","kb"]',
  '3b4f619c85c211f198269135a1db216c',
  'naive',
  1,
  NULL, NULL, ''
);

SELECT id, name, site_id, tenant_id, kb_id, enabled FROM crawler_task WHERE id='a1f1b2c3d4e5f60708090a0b0c0d0e01';
