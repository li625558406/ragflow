SET NAMES utf8mb4;

INSERT INTO crawler_task (
  id, create_time, create_date, update_time, update_date,
  tenant_id, name, description, site_id, target_url,
  start_page, max_pages, extraction_schema, detail_config, headers,
  output_targets, kb_id, parser_id, enabled,
  last_run_time, last_run_status, last_run_summary
) VALUES (
  'a1b2c3d4e5f60708090a0b0c0d0e0ff1',
  UNIX_TIMESTAMP()*1000, NOW(), UNIX_TIMESTAMP()*1000, NOW(),
  '7ab771d4dec84f23b2c1fb5f4e453ff9',
  '漳州市公共资源交易中心-交易信息',
  '漳州公共资源交易中心工程建设交易信息（招标公告/中标候选人公示/合同备案等）。列表 POST /proxy_api/publicResource/front/viewProjects 按当日 startDate/endDate 过滤；详情 GET /proxy_api/publicResource/front/projectDetail/{infoID}；附件在 attachFiles[].attUrl。YAML 驱动，detector 自动增量。',
  'zhangzhou_gcjs_trade',
  'http://ggzyjy.xzfwzx.zhangzhou.gov.cn/cms/sitemanage/index.shtml?siteId=40669965560550000&templateId=10671863355640000&projectType=sg&cateNum=001001001',
  1, 5,
  '{"baseSelector":".resultList-item","fields":[]}',
  '{}',
  '{"Content-Type":"application/json","User-Agent":"Mozilla/5.0"}',
  '["db","kb"]',
  '3b4f619c85c211f198269135a1db216c',
  'naive',
  1,
  NULL, NULL, ''
);

SELECT id, name, site_id, tenant_id, kb_id, enabled FROM crawler_task WHERE id='a1b2c3d4e5f60708090a0b0c0d0e0ff1';
