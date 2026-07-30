UPDATE crawler_task
SET last_run_time = UNIX_TIMESTAMP()*1000,
    last_run_status = 'success',
    last_run_summary = '{"items_new":0,"items_total":0,"items_attachments":0,"trigger":"cli_equivalent","note":"today filter returned 0 items (no new publications)"}'
WHERE site_id = 'gcjyzx_jyxx';
SELECT id, name, last_run_status, FROM_UNIXTIME(last_run_time/1000) AS last_run FROM crawler_task WHERE site_id='gcjyzx_jyxx';
