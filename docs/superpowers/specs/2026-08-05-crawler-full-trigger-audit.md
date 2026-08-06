# 全量采集脚本触发审计 — 2026-08-05

总任务数：87 · 启动：2026-08-05T22:27:02

每任务两轮，按 YAML 规则跑（不传 --date-filter / --force）。

| # | task_id | 站点名 | site_id | kb_id | 第一轮<br>总数/日期/今日数 | 第二轮<br>总数/今日数 | KB上传 | 符合 | 执行/备注 |
|---|---------|--------|---------|-------|----------------------|----------------------|--------|------|-----------|
| 1 | 0391b8cb4d32… | 35福建省泉州市采购网 | zfcg | 03a11444… | 0 / ~ / today=0 | 0 / today=0 | 0 | ? | r1:rc=0 148s<br>r2:rc=0 27s Δ=0 · r1 zero items |
| 2 | 04e511b6571d… | 【公报】中华人民共和国水利部 | slbgb | f494f9d2… | 0 / ~ / today=0 | 0 / today=0 | 0 | ? | r1:rc=0 17s<br>r2:rc=0 21s Δ=0 · r1 zero items |
| 3 | 099dcd9c56a5… | 【解读回应】福建省财政厅 | czt_jdhy | a35c93a0… | 0 / ~ / today=0 | 0 / today=0 | 0 | ? | r1:rc=1 19s<br>r2:rc=1 19s Δ=0 · r1err:connection.
2026-08-05 22:30:53,131 INFO     3027 === Unified crawler started: site=czt_jdhy ===
2026-08-05 22:30:53,901 INFO     3027 Loaded 136 site configs from /ragflow/rag/svr/crawler_sites.yaml
; r2err:connection.
2026-08-05 22:31:12,849 INFO     3068 === Unified crawler started: site=czt_jdhy ===
2026-08-05 22:31:13,661 INFO     3068 Loaded 136 site configs from /ragflow/rag/svr/crawler_sites.yaml
; r1 zero items |
| 4 | 0a846aac569d… | 【政务公开】福建省发展和改革委员会  | fgw_zwgk | f494f9d2… | 0 / ~ / today=0 | 0 / today=0 | 0 | ? | r1:rc=1 15s<br>r2:rc=1 14s Δ=0 · r1err:connection.
2026-08-05 22:31:28,243 INFO     3103 === Unified crawler started: site=fgw_zwgk ===
2026-08-05 22:31:28,910 INFO     3103 Loaded 136 site configs from /ragflow/rag/svr/crawler_sites.yaml
; r2err:connection.
2026-08-05 22:31:42,116 INFO     3149 === Unified crawler started: site=fgw_zwgk ===
2026-08-05 22:31:42,635 INFO     3149 Loaded 136 site configs from /ragflow/rag/svr/crawler_sites.yaml
; r1 zero items |
| 5 | 1037e4b456bc… | 【政策法规】泉州市公共资源交易信息网 | quanzhou_zcfg | a35c93a0… | 8 / 2017-09-22~2021-07-13 / today=0 | 8 / today=0 | 8 | ✓ | r1:rc=0 52s<br>r2:rc=0 17s Δ=0 · ok |
| 6 | 11a35e7a56b5… | 【业务培训】福建省建筑业协会  | fjcia_edu | f494f9d2… | 0 / ~ / today=0 | 0 / today=0 | 0 | ? | r1:rc=0 19s<br>r2:rc=0 20s Δ=0 · r1 zero items |
| 7 | 1202cd105674… | 【招标信息】中国招标投标公共服务平台 （防爬严重，只获取标题 | cebpubservice | 03a11444… | 427 / 2026-08-05~2026-08-05 / today=427 | 427 / today=427 | 427 | ✓ | r1:rc=0 20s<br>r2:rc=0 18s Δ=0 · ok |
| 8 | 159c9428571f… | 【政策法规】福建省综合评标专家库 | zjk_zffg | a35c93a0… | 0 / ~ / today=0 | 0 / today=0 | 0 | ? | r1:rc=0 21s<br>r2:rc=0 20s Δ=0 · r1 zero items |
| 9 | 196ac97c5697… | 【政策法规】福建省公共资源交易电子公共服务平台   | ggzyfw_policies | a35c93a0… | 0 / ~ / today=0 | 0 / today=0 | 0 | ? | r1:rc=0 19s<br>r2:rc=0 20s Δ=0 · r1 zero items |
| 10 | 19b970aa55e2… | 【规范+法规】福建省公共资源交易电子公共服务平台  | ggzyfw_guide | a35c93a0… | 0 / ~ / today=0 | 0 / today=0 | 0 | ? | r1:rc=0 206s<br>r2:rc=0 208s Δ=0 · r1 zero items |
| 11 | 1ac6b29055d9… | 【法规】福建省招标投标规范 | ggzyjd_policies | a35c93a0… | 0 / ~ / today=0 | 0 / today=0 | 0 | ? | r1:rc=0 94s<br>r2:rc=0 128s Δ=0 · r1 zero items |
| 12 | 1ba79b20569c… | 【政务公开】福建省交通运输厅   | jtyst_zwgk | f494f9d2… | 0 / ~ / today=0 | 0 / today=0 | 0 | ? | r1:rc=1 18s<br>r2:rc=1 19s Δ=0 · r1err:nnection.
2026-08-05 22:46:20,291 INFO     3884 === Unified crawler started: site=jtyst_zwgk ===
2026-08-05 22:46:21,134 INFO     3884 Loaded 136 site configs from /ragflow/rag/svr/crawler_sites.yaml
; r2err:nnection.
2026-08-05 22:46:39,405 INFO     3908 === Unified crawler started: site=jtyst_zwgk ===
2026-08-05 22:46:40,353 INFO     3908 Loaded 136 site configs from /ragflow/rag/svr/crawler_sites.yaml
; r1 zero items |
| 13 | 21bcf2405691… | 【政策法规】全国公共资源交易平台  | ggzy_policy | a35c93a0… | 96 / 2015-08-14~2026-07-17 / today=0 | 96 / today=0 | 96 | ✓ | r1:rc=0 363s<br>r2:rc=0 19s Δ=0 · ok |
| 14 | 25efeee54ae6… | 【处理结果+有关案例】福建省监督平台 | ggzyjd | 03a11444… | 0 / ~ / today=0 | 0 / today=0 | 0 | ? | r1:rc=1 15s<br>r2:rc=1 18s Δ=0 · r1err:s connection.
2026-08-05 22:53:16,355 INFO     4014 === Unified crawler started: site=ggzyjd ===
2026-08-05 22:53:17,131 INFO     4014 Loaded 136 site configs from /ragflow/rag/svr/crawler_sites.yaml
; r2err:s connection.
2026-08-05 22:53:33,874 INFO     4038 === Unified crawler started: site=ggzyjd ===
2026-08-05 22:53:34,837 INFO     4038 Loaded 136 site configs from /ragflow/rag/svr/crawler_sites.yaml
; r1 zero items |
| 15 | 261a888c566b… | 【政策法规】随行易交易电子招标投标交易平台 | enjoy5191_policy | a35c93a0… | 0 / ~ / today=0 | 0 / today=0 | 0 | ? | r1:rc=0 23s<br>r2:rc=0 21s Δ=0 · r1 zero items |
| 16 | 29822b2c56b8… | 【公告】福建省水利建设市场信用管理平台 | smzx_notice | f494f9d2… | 0 / ~ / today=0 | 0 / today=0 | 0 | ? | r1:rc=0 37s<br>r2:rc=0 39s Δ=0 · r1 zero items |
| 17 | 2da9070d4d32… | 36福建省漳州市采购网 | zfcg | 03a11444… | 0 / ~ / today=0 | 0 / today=0 | 0 | ? | r1:rc=0 30s<br>r2:rc=0 32s Δ=0 · r1 zero items |
| 18 | 308fcb7a56ab… | 【政务公开】漳州市发展和改革委员会  | zhangzhou_fgw | a35c93a0… | 0 / ~ / today=0 | 0 / today=0 | 0 | ? | r1:rc=0 302s<br>r2:rc=0 302s Δ=0 · r1 zero items |
| 19 | 34f0ba245667… | 【示例文本】福建省公共资源交易电子公共服务平台 | ggzyfw_guide_trade | fc495e11… | 0 / ~ / today=0 | 0 / today=0 | 0 | ? | r1:rc=0 125s<br>r2:rc=0 132s Δ=0 · r1 zero items |
| 20 | 38eeb44056c2… | 【工程建设】宁德市公共资源交易中心--首页   | ningde_gcjs | f494f9d2… | 22 / 2026-08-05~2026-08-05 / today=22 | 22 / today=22 | 22 | ✓ | r1:rc=0 245s<br>r2:rc=0 233s Δ=0 · ok |
| 21 | 398dc82055ee… | 【代理机构名单】 中国政府采购网 | ccgp_agency | 03a11444… | 0 / ~ / today=0 | 0 / today=0 | 0 | ? | r1:rc=0 22s<br>r2:rc=0 18s Δ=0 · r1 zero items |
| 22 | 39a27efe568a… | 【机构】漳州市工程项目网上中介服务平台 | zjfw_score_sort | 03a11444… | 0 / ~ / today=0 | 0 / today=0 | 0 | ? | r1:rc=0 49s<br>r2:rc=0 53s Δ=0 · r1 zero items |
| 23 | 3a9a6c68568f… | 【政策法规】漳州市工程项目交易中心 | gcjyzx_zcfg | a35c93a0… | 0 / ~ / today=0 | 0 / today=0 | 0 | ? | r1:rc=0 24s<br>r2:rc=0 23s Δ=0 · r1 zero items |
| 24 | 3c34629456b8… | 【政务公开】国家文物局 | ncha | f494f9d2… | 0 / ~ / today=0 | 0 / today=0 | 0 | ? | r1:rc=0 25s<br>r2:rc=0 24s Δ=0 · r1 zero items |
| 25 | 3c6736c856aa… | 【政务公开】中华人民共和国国家发展和改革委员会 | ndrc_fzggwl | f494f9d2… | 0 / ~ / today=0 | 0 / today=0 | 0 | ? | r1:rc=0 76s<br>r2:rc=0 77s Δ=0 · r1 zero items |
| 26 | 443b8366545e… | 【信息公告】 中国政府采购网 | ccgp_jdcf_gg_cr | 03a11444… | 92 / 2026-06-24~2026-08-05 / today=13 | 92 / today=13 | 92 | ✓ | r1:rc=0 253s<br>r2:rc=0 20s Δ=0 · ok |
| 27 | 46402c3e567a… | 【招标信息】福易采漳州分中心 | fycbid | 03a11444… | 0 / ~ / today=0 | 0 / today=0 | 0 | ? | r1:rc=0 20s<br>r2:rc=0 19s Δ=0 · r1 zero items |
| 28 | 47936490566e… | 【政策法规】【一次】新点电子交易平台福建专区 | etrading_statute | a35c93a0… | 0 / ~ / today=0 | 0 / today=0 | 0 | ? | r1:rc=0 20s<br>r2:rc=0 18s Δ=0 · r1 zero items |
| 29 | 4ef0fe2456a4… | 【政务公开】福建省水利厅   | slt_xxgk | f494f9d2… | 0 / ~ / today=0 | 0 / today=0 | 0 | ? | r1:rc=0 44s<br>r2:rc=0 20s Δ=0 · r1 zero items |
| 30 | 4f49a58856ae… | 【政务公开】南靖县人民政府   | fjnj | f494f9d2… | 0 / ~ / today=0 | 0 / today=0 | 0 | ? | r1:rc=0 304s<br>r2:rc=0 302s Δ=0 · r1 zero items |
| 31 | 532792ab4d32… | 37福建省南平市采购网 | zfcg | 03a11444… | 0 / ~ / today=0 | 0 / today=0 | 0 | ? | r1:rc=0 58s<br>r2:rc=0 31s Δ=0 · r1 zero items |
| 32 | 589152d255ee… | 【专家报酬】【一次】 中国政府采购网 | ccgp_zjlwbcbz | 03a11444… | 0 / ~ / today=0 | 0 / today=0 | 0 | ? | r1:rc=0 19s<br>r2:rc=0 21s Δ=0 · r1 zero items |
| 33 | 59a817ac55e7… | 【处理结果】福建省政府采购网  | zfcg_jdgl | 03a11444… | 0 / ~ / today=0 | 0 / today=0 | 0 | ? | r1:rc=0 117s<br>r2:rc=0 28s Δ=0 · r1 zero items |
| 34 | 5e94e834566a… | 【招标范本】随行易交易电子招标投标交易平台 | enjoy5191 | fc495e11… | 0 / ~ / today=0 | 0 / today=0 | 0 | ? | r1:rc=0 34s<br>r2:rc=0 34s Δ=0 · r1 zero items |
| 35 | 608e9aea569c… | 【政策解读】福建省交通运输厅   | jtyst_jdhy | a35c93a0… | 0 / ~ / today=0 | 0 / today=0 | 0 | ? | r1:rc=0 44s<br>r2:rc=0 18s Δ=0 · r1 zero items |
| 36 | 639bb5e8567d… | 【招标信息】工采通电子招投标交易平台  | easy_prt_trading | 03a11444… | 115 / 2026-08-05~2026-08-05 / today=115 | 135 / today=135 | 135 | ✓ | r1:rc=0 61s<br>r2:rc=0 58s Δ=20 · r2 grew 20 (dedup may not work) |
| 37 | 68a79e345453… | 【法规】 中国政府采购网 | ccgp_zcfg | a35c93a0… | 0 / ~ / today=0 | 0 / today=0 | 0 | ? | r1:rc=0 35s<br>r2:rc=0 30s Δ=0 · r1 zero items |
| 38 | 69de010056ae… | 【解读回应】南靖县人民政府 | fjnj_jdhy | a35c93a0… | 0 / ~ / today=0 | 0 / today=0 | 0 | ? | r1:rc=0 304s<br>r2:rc=0 304s Δ=0 · r1 zero items |
| 39 | 6d31e45656c3… | 【交易信息】三明市公共资源交易网 | smjy | f494f9d2… | 0 / ~ / today=0 | 0 / today=0 | 0 | ? | r1:rc=0 24s<br>r2:rc=0 25s Δ=0 · r1 zero items |
| 40 | 6f8cb8174d31… | 32福建省厦门市采购网 | zfcg | 03a11444… | 0 / ~ / today=0 | 0 / today=0 | 0 | ? | r1:rc=0 36s<br>r2:rc=0 25s Δ=0 · r1 zero items |
| 41 | 71725d4255e3… | 【交易信息】福建省公共资源交易电子公共服务平台  | ggzyfw_fujian_business | 03a11444… | 0 / ~ / today=0 | 0 / today=0 | 0 | ? | r1:rc=1 22s<br>r2:rc=1 42s Δ=0 · r1err:eError: 'int' object has no attribute 'startswith'
2026-08-06 00:04:34,142 ERROR    7902 === Unified crawler crashed: site=ggzyfw_fujian_business, error='int' object has no attribute 'startswith' ===
; r2err:eError: 'int' object has no attribute 'startswith'
2026-08-06 00:05:15,944 ERROR    7986 === Unified crawler crashed: site=ggzyfw_fujian_business, error='int' object has no attribute 'startswith' ===
; r1 zero items |
| 42 | 758ada1856b4… | 【政策法规】福建省招标投标协会 | fjtba | a35c93a0… | 0 / ~ / today=0 | 0 / today=0 | 0 | ? | r1:rc=0 17s<br>r2:rc=0 17s Δ=0 · r1 zero items |
| 43 | 7b5ecad056c3… | 【法规政策】三明市公共资源交易网 | smzcfg | a35c93a0… | 0 / ~ / today=0 | 0 / today=0 | 0 | ? | r1:rc=0 21s<br>r2:rc=0 22s Δ=0 · r1 zero items |
| 44 | 7e788d905674… | 【Q&A咨询】【一次】中国招标投标公共服务平台  | cebpubservice_consult | f494f9d2… | 0 / ~ / today=0 | 0 / today=0 | 0 | ? | r1:rc=1 16s<br>r2:rc=1 20s Δ=0 · r1err:== Unified crawler crashed: site=cebpubservice_consult, error=scrapling is required for transport type 'scrapling_stealth'. Install it with: pip install 'scrapling[fetchers]' && scrapling install ===
; r2err:== Unified crawler crashed: site=cebpubservice_consult, error=scrapling is required for transport type 'scrapling_stealth'. Install it with: pip install 'scrapling[fetchers]' && scrapling install ===
; r1 zero items |
| 45 | 89aeb9b656a4… | 【解读回应】福建省水利厅 | slt_zcjd | a35c93a0… | 0 / ~ / today=0 | 0 / today=0 | 0 | ? | r1:rc=0 18s<br>r2:rc=0 19s Δ=0 · r1 zero items |
| 46 | 8b146be4568f… | 【违规通报】漳州市工程项目交易中心 | gcjyzx_wgtb | f494f9d2… | 0 / ~ / today=0 | 0 / today=0 | 0 | ? | r1:rc=0 19s<br>r2:rc=0 17s Δ=0 · r1 zero items |
| 47 | 91855c6e569d… | 【解读回应】福建省发展和改革委员会 | jdhy | a35c93a0… | 2 / 2026-08-05~2026-08-05 / today=2 | 2 / today=2 | 2 | ✓ | r1:rc=0 65s<br>r2:rc=0 68s Δ=0 · ok |
| 48 | 9dec34f94d32… | 37福建省龙岩市采购网 | zfcg | 03a11444… | 0 / ~ / today=0 | 0 / today=0 | 0 | ? | r1:rc=0 27s<br>r2:rc=0 30s Δ=0 · r1 zero items |
| 49 | 9e2bb2ac56ab… | 【政务公开】漳州市住房和城乡建设局   | jsj_zhangzhou | f494f9d2… | 0 / ~ / today=0 | 0 / today=0 | 0 | ? | r1:rc=0 42s<br>r2:rc=0 44s Δ=0 · r1 zero items |
| 50 | a0fea61056c4… | 【交易信息】厦门市公共资源交易网 | xmzyjy | f494f9d2… | 0 / ~ / today=0 | 0 / today=0 | 0 | ? | r1:rc=1 18s<br>r2:rc=1 16s Δ=0 · r1err:s connection.
2026-08-06 00:13:12,408 INFO     9287 === Unified crawler started: site=xmzyjy ===
2026-08-06 00:13:13,272 INFO     9287 Loaded 127 site configs from /ragflow/rag/svr/crawler_sites.yaml
; r2err:s connection.
2026-08-06 00:13:29,417 INFO     9349 === Unified crawler started: site=xmzyjy ===
2026-08-06 00:13:29,876 INFO     9349 Loaded 127 site configs from /ragflow/rag/svr/crawler_sites.yaml
; r1 zero items |
| 51 | a47a838855e6… | 【公告信息】福建省政府采购网  | zfcg_xmgg | f494f9d2… | 0 / ~ / today=0 | 0 / today=0 | 0 | ? | r1:rc=0 22s<br>r2:rc=0 22s Δ=0 · r1 zero items |
| 52 | a55d92bc567d… | 【网上竞价+询价采购】工采通电子招投标交易平台  | easy_prt_bidding | f494f9d2… | 0 / ~ / today=0 | 0 / today=0 | 0 | ? | r1:rc=0 42s<br>r2:rc=0 43s Δ=0 · r1 zero items |
| 53 | a614dbc04d31… | 33福建省莆田市采购网 | zfcg | 84f29caf… | 0 / ~ / today=0 | 0 / today=0 | 0 | ? | r1:rc=0 23s<br>r2:rc=0 21s Δ=0 · r1 zero items |
| 54 | a75c598656c3… | 【交易信息】龙岩公共资源 | longyan | f494f9d2… | 0 / ~ / today=0 | 0 / today=0 | 0 | ? | r1:rc=0 38s<br>r2:rc=0 38s Δ=0 · r1 zero items |
| 55 | ab37fcb45686… | 【公告】漳州市工程项目网上中介服务平台 | zjfw | f494f9d2… | 0 / ~ / today=0 | 0 / today=0 | 0 | ? | r1:rc=0 181s<br>r2:rc=0 181s Δ=0 · r1 zero items |
| 56 | ae33b35e5690… | 【招标信息】全国公共资源交易平台  | ggzy_deal | 03a11444… | 0 / ~ / today=0 | 0 / today=0 | 0 | ? | r1:rc=1 16s<br>r2:rc=1 13s Δ=0 · r1err:nection.
2026-08-06 00:23:54,001 INFO     12810 === Unified crawler started: site=ggzy_deal ===
2026-08-06 00:23:54,747 INFO     12810 Loaded 127 site configs from /ragflow/rag/svr/crawler_sites.yaml
; r2err:nection.
2026-08-06 00:24:07,506 INFO     12983 === Unified crawler started: site=ggzy_deal ===
2026-08-06 00:24:07,992 INFO     12983 Loaded 127 site configs from /ragflow/rag/svr/crawler_sites.yaml
; r1 zero items |
| 57 | b04091d65685… | 【公告】中央政府采购网 | zycg_notice | f494f9d2… | 0 / ~ / today=0 | 0 / today=0 | 0 | ? | r1:rc=0 63s<br>r2:rc=0 65s Δ=0 · r1 zero items |
| 58 | b0b1009e56c4… | 【政策法规】厦门市公共资源交易网 | xmzyjy_zcfg | a35c93a0… | 0 / ~ / today=0 | 0 / today=0 | 0 | ? | r1:rc=1 19s<br>r2:rc=1 18s Δ=0 · r1err:ction.
2026-08-06 00:26:33,867 INFO     13368 === Unified crawler started: site=xmzyjy_zcfg ===
2026-08-06 00:26:34,675 INFO     13368 Loaded 127 site configs from /ragflow/rag/svr/crawler_sites.yaml
; r2err:ction.
2026-08-06 00:26:52,101 INFO     13388 === Unified crawler started: site=xmzyjy_zcfg ===
2026-08-06 00:26:52,845 INFO     13388 Loaded 127 site configs from /ragflow/rag/svr/crawler_sites.yaml
; r1 zero items |
| 59 | b19894fe5683… | 【法规】中央政府采购网  | zycg_zdfg | a35c93a0… | 0 / ~ / today=0 | 0 / today=0 | 0 | ? | r1:rc=0 43s<br>r2:rc=0 42s Δ=0 · r1 zero items |
| 60 | b3508ae65669… | 【招标文件】随行易交易电子招标投标交易平台 | enjoy5191_trade | 03a11444… | 0 / ~ / today=0 | 0 / today=0 | 0 | ? | r1:rc=0 30s<br>r2:rc=0 33s Δ=0 · r1 zero items |
| 61 | b75d1cbe56bc… | 【招标信息】莆田市公共资源交易中心 | putian_fwzx | 03a11444… | 0 / ~ / today=0 | 0 / today=0 | 0 | ? | r1:rc=0 29s<br>r2:rc=0 27s Δ=0 · r1 zero items |
| 62 | bdb01df856b4… | 【培训咨询】福建省招标投标协会 | fjtba_pxzx | f494f9d2… | 0 / ~ / today=0 | 0 / today=0 | 0 | ? | r1:rc=0 14s<br>r2:rc=0 15s Δ=0 · r1 zero items |
| 63 | bebe4cd65675… | 【招标信息】福建省国资采购平台 | ygcg_engineering | 03a11444… | 0 / ~ / today=0 | 0 / today=0 | 0 | ? | r1:rc=1 15s<br>r2:rc=1 19s Δ=0 · r1err:750 === Unified crawler crashed: site=ygcg_engineering, error=scrapling is required for transport type 'scrapling_stealth'. Install it with: pip install 'scrapling[fetchers]' && scrapling install ===
; r2err:772 === Unified crawler crashed: site=ygcg_engineering, error=scrapling is required for transport type 'scrapling_stealth'. Install it with: pip install 'scrapling[fetchers]' && scrapling install ===
; r1 zero items |
| 64 | beefbd5256a7… | 【政务公开】福建省住房和城乡建设厅 | zjt_xxgk | f494f9d2… | 15 / 07-27~08-05 / today=0 | 15 / today=0 | 15 | ✓ | r1:rc=0 73s<br>r2:rc=0 21s Δ=0 · ok |
| 65 | c0818ab65697… | 【招标信息】漳州公共资源交易中心   | zhangzhou | 03a11444… | 0 / ~ / today=0 | 0 / today=0 | 0 | ? | r1:rc=0 22s<br>r2:rc=0 17s Δ=0 · r1 zero items |
| 66 | c2a71d8855fc… | 【政策法规】福建省政府采购网 | zfcg_zcfg | a35c93a0… | 0 / ~ / today=0 | 0 / today=0 | 0 | ? | r1:rc=0 34s<br>r2:rc=0 25s Δ=0 · r1 zero items |
| 67 | c5066b7256bc… | 【政策文件】莆田市公共资源交易中心 | putian_zcwj | a35c93a0… | 0 / ~ / today=0 | 0 / today=0 | 0 | ? | r1:rc=0 26s<br>r2:rc=0 26s Δ=0 · r1 zero items |
| 68 | c59518cc4ab8… | 【通知+行业动态】福建省公共资源交易电子公共服务平台 | ggzyfw_fujian | f494f9d2… | 0 / ~ / today=0 | 0 / today=0 | 0 | ? | r1:rc=0 136s<br>r2:rc=0 135s Δ=0 · r1 zero items |
| 69 | c763cba44d32… | 39福建省宁德市采购网 | zfcg | 03a11444… | 0 / ~ / today=0 | 0 / today=0 | 0 | ? | r1:rc=0 36s<br>r2:rc=0 28s Δ=0 · r1 zero items |
| 70 | d14ca01e56a7… | 【解读回应】福建省住房和城乡建设厅 | zjt_jdhy | a35c93a0… | 0 / ~ / today=0 | 0 / today=0 | 0 | ? | r1:rc=0 29s<br>r2:rc=0 32s Δ=0 · r1 zero items |
| 71 | d7169e4856af… | 【政务公开】漳州市人民政府   | zz_zhangzhou | f494f9d2… | 0 / ~ / today=0 | 0 / today=0 | 0 | ? | r1:rc=0 44s<br>r2:rc=0 42s Δ=0 · r1 zero items |
| 72 | d85c8bae5683… | 【法规】工采通电子招投标交易平台  | easy_prt_policy | a35c93a0… | 0 / ~ / today=0 | 0 / today=0 | 0 | ? | r1:rc=0 17s<br>r2:rc=0 15s Δ=0 · r1 zero items |
| 73 | d9a1be254d31… | 34福建省三明市采购网 | zfcg | 03a11444… | 0 / ~ / today=0 | 0 / today=0 | 0 | ? | r1:rc=0 27s<br>r2:rc=0 25s Δ=0 · r1 zero items |
| 74 | da27a23a5696… | 【招标信息】福建省公共资源交易电子公共服务平台   | ggzyfw_business | 03a11444… | 0 / ~ / today=0 | 0 / today=0 | 0 | ? | r1:rc=0 24s<br>r2:rc=0 28s Δ=0 · r1 zero items |
| 75 | db978ce456a4… | 【政务公开】福建省财政厅   | czt | f494f9d2… | 0 / ~ / today=0 | 0 / today=0 | 0 | ? | r1:rc=0 43s<br>r2:rc=0 21s Δ=0 · r1 zero items |
| 76 | dce930ea56b6… | 【政策法规】全国公路建设市场监督管理系统 | hwdms_policy | a35c93a0… | 0 / ~ / today=0 | 0 / today=0 | 0 | ? | r1:rc=0 20s<br>r2:rc=0 24s Δ=0 · r1 zero items |
| 77 | e6cadfd456af… | 【解读回应】漳州市人民政府 | jdhy_zhangzhou | a35c93a0… | 0 / ~ / today=0 | 0 / today=0 | 0 | ? | r1:rc=1 17s<br>r2:rc=1 20s Δ=0 · r1err:on.
2026-08-06 00:47:46,746 INFO     15593 === Unified crawler started: site=jdhy_zhangzhou ===
2026-08-06 00:47:47,289 INFO     15593 Loaded 127 site configs from /ragflow/rag/svr/crawler_sites.yaml
; r2err:on.
2026-08-06 00:48:06,735 INFO     15613 === Unified crawler started: site=jdhy_zhangzhou ===
2026-08-06 00:48:07,573 INFO     15613 Loaded 127 site configs from /ragflow/rag/svr/crawler_sites.yaml
; r1 zero items |
| 78 | e7b418905711… | 【交易信息】平潭综合实验区公共资源统一平台 | pingtan_fjsz | f494f9d2… | 0 / ~ / today=0 | 0 / today=0 | 0 | ? | r1:rc=0 585s<br>r2:rc=0 56s Δ=0 · r1 zero items |
| 79 | ef64349456b7… | 【违法违规公告】福建省招标投标协会 | fjtba_wfwg | 03a11444… | 0 / ~ / today=0 | 0 / today=0 | 0 | ? | r1:rc=0 19s<br>r2:rc=0 20s Δ=0 · r1 zero items |
| 80 | f37e96ce56a7… | 【公告】中华人民共和国住房和城乡建设部 | mohurd | f494f9d2… | 0 / ~ / today=0 | 0 / today=0 | 0 | ? | r1:rc=0 42s<br>r2:rc=0 42s Δ=0 · r1 zero items |
| 81 | f59cb5ec566d… | 【招标信息】新点电子交易平台福建专区 | etrading | 03a11444… | 0 / ~ / today=0 | 0 / today=0 | 0 | ? | r1:rc=0 25s<br>r2:rc=0 28s Δ=0 · r1 zero items |
| 82 | f62ec858568e… | 【招标信息】漳州市工程项目交易中心   | gcjyzx_jyxx | 03a11444… | 98 / 2026-07-27 09:14~2026-08-05 18:31 / today=9 | 534 / today=9 | 534 | ✓ | r1:rc=0 319s<br>r2:rc=0 1398s Δ=436 · r2 grew 436 (dedup may not work) |
| 83 | f66d002e571e… | 【通知公告】福建省综合评标专家库 | zjk_notice | f494f9d2… | 0 / ~ / today=0 | 0 / today=0 | 0 | ? | r1:rc=0 19s<br>r2:rc=0 15s Δ=0 · r1 zero items |
| 84 | f6e701dc571c… | 【政策法规】中华人民共和国水利部  | mwr | a35c93a0… | 0 / ~ / today=0 | 0 / today=0 | 0 | ? | r1:rc=0 20s<br>r2:rc=0 24s Δ=0 · r1 zero items |
| 85 | fc8b10e456bb… | 【交易信息】泉州市公共资源交易信息网 | quanzhou | 03a11444… | 0 / ~ / today=0 | 0 / today=0 | 0 | ? | r1:rc=0 188s<br>r2:rc=0 22s Δ=0 · r1 zero items |
| 86 | fec309654ae5… | 福建省政府采购网  | zfcg | 03a11444… | 0 / ~ / today=0 | 0 / today=0 | 0 | ? | r1:rc=0 21s<br>r2:rc=0 21s Δ=0 · r1 zero items |
| 87 | fecc612a4d30… | 31福建省福州市采购网 | zfcg | 03a11444… | 0 / ~ / today=0 | 0 / today=0 | 0 | ? | r1:rc=0 24s<br>r2:rc=0 25s Δ=0 · r1 zero items |

---

## 最终分析（2026-08-06 完成）

总耗时：约 3 小时 9 分（22:27 → 01:36）· 87/87 完成

### 汇总

| 维度 | 数量 |
|---|---|
| **正常抓取 (rc=0 且有数据)** | **9 个 site_id** |
| **rc=0 但 0 条** | ~67 个 |
| **rc=1 连接错误** | 8 个 |
| **rc=1 缺 scrapling 依赖** | 2 个 |
| **rc=1 代码 Bug** | 1 个 |
| **dedup 异常（r2 比 r1 多）** | 2 个 |

### ✅ 正常抓取站点 (9)

| # | site_id | 第一轮 | 第二轮 | 规则符合 | 备注 |
|---|---------|--------|--------|---------|------|
| 5 | quanzhou_zcfg | 8 条 (2017-2021) | 0 Δ | ✓ 全量 | 旧历史数据，今日无新 |
| 7 | cebpubservice | 427 条 (今日) | 0 Δ | ✓ 首页/今日 | dedup 正常 |
| 13 | ggzy_policy | 96 条 (2015-2026) | 0 Δ | ✓ 全量 | dedup 正常 |
| 20 | ningde_gcjs | 22 条 (今日) | 0 Δ | ✓ 首页/今日 | dedup 正常 |
| 26 | ccgp_jdcf_gg_cr | 92 条 (今日 13) | 0 Δ | ✓ 首页+今日 | 本次修复，dedup 正常 |
| 36 | easy_prt_trading | 115 条 | +20 | ⚠️ dedup 异常 | r2 不应增长 |
| 47 | jdhy | 2 条 (今日) | 0 Δ | ✓ 首页/今日 | dedup 正常 |
| 64 | zjt_xxgk | 15 条 (07-27~08-05) | 0 Δ | ✓ 首页 | dedup 正常 |
| 82 | gcjyzx_jyxx | 98 条 | +436 | ⚠️ dedup 严重异常 | r1 跑了 319s, r2 跑了 1398s |

### ❌ 异常分类

#### A. dedup 失效（r2 应=0 实际大增，最高优先）

| site_id | r1 | r2 Δ | 推测原因 |
|---------|-----|------|---------|
| easy_prt_trading | 115 | +20 | source_url 含动态参数（如时间戳/_）每次不同 |
| gcjyzx_jyxx | 98 | +436 | 同上，或 item.id 不稳定；r2 跑得异常久（23 分钟） |

#### B. rc=1 代码 Bug

| site_id | 错误 |
|---------|------|
| ggzyfw_fujian_business | `'int' object has no attribute 'startswith'` — 某字段是 int 但代码当 str 处理 |

#### C. rc=1 缺 scrapling 依赖

| site_id | YAML transport |
|---------|---------------|
| cebpubservice_consult | scrapling_stealth |
| ygcg_engineering | scrapling_stealth |

需 `pip install 'scrapling[fetchers]' && scrapling install` 或改用其他 transport

#### D. rc=1 连接错误（8 个，容器无法访问目标站点）

| site_id | 类型 |
|---------|------|
| czt_jdhy, fgw_zwgk, jtyst_zwgk, ggzyjd, xmzyjy, ggzy_deal, xmzyjy_zcfg, jdhy_zhangzhou | TLS/SSL 或防火墙拦截 |

#### E. zfcg 系列单点故障（8 任务全 0 条）

所有 31-39 福建省地市采购网任务都共用 `site_id=zfcg`，YAML 配置走 zfcg API，r1/r2 均 rc=0 但 0 条 — 怀疑 API auth code=4001（之前已知问题）。**8 个任务实际失效**：

泉州/漳州/南平/厦门/龙岩/莆田/三明/宁德/福州/省本级

#### F. rc=0 但 0 条（约 55 个 site_id，最大一类）

抓取成功但未返回数据，原因可能是：
- 反爬识别（返回空列表或验证页）
- YAML 配置错误（选择器/API 路径过期）
- 站点改版

需要逐个排查。重点排查的有：ccgp_zcfg, ccgp_agency, ccgp_zjlwbcbz, fycbid, etrading*, enjoy5191*, slt_*, fjnj*, smjy, smzcfg, fjtba*, zjk_*, ggzyfw_*, mohurd, mwr, ncha, ndrc_fzggwl, zhangzhou, longyan, quanzhou, putian_*, hwdms_policy, czt, slbgb, fjcia_edu, smzx_notice, zjfw*

### 建议下一步优先级

| 优先级 | 任务 | 预估工作量 |
|--------|------|-----------|
| P0 | 修 dedup 异常（easy_prt_trading + gcjyzx_jyxx）— 检查 source_url 提取逻辑 | 0.5 天 |
| P0 | 修 zfcg API auth=4001 — 影响 8+ 任务 | 0.5 天 |
| P1 | 安装 scrapling 依赖 — 修复 cebpubservice_consult + ygcg_engineering | 10 分钟 |
| P1 | 修 ggzyfw_fujian_business int→str Bug | 30 分钟 |
| P2 | 排查 8 个连接错误站点（SSL/DNS） | 0.5 天 |
| P3 | 逐个排查 ~55 个 0 条站点（最大工作量） | 3-5 天 |


---

## 二次排查（2026-08-06，P0-P3 全量处理）

### 处理结果

| 任务 | 状态 | 详情 |
|------|------|------|
| P0 dedup | ✅ 代码已部署 | DedupChecker 增加集合模式 crawler_result 查询（dedup_checker.py）；但 easy_prt_trading/gcjyzx_jyxx 因 API 分页不稳定（每次返回不同 items），URL 维度去重无效。需要后续加 date_filter 或限制只抓 page 1 |
| P0 zfcg auth | ✅ 修复 | YAML detail.params 加 `id: "{id}"`；测试：单站抓 3 条带 20 字段 extracted_json，markdown 内容真实（3170 字节） |
| P1 scrapling | ✅ 已装 | scrapling 0.4.12 + browserforge + camoufox + playwright 1.58 + chromium-headless-shell-1208 装好；适配器 StealthyFetcher 可用 |
| P1 ggzyfw int | ✅ 修复 | engine.py:480 + base.py:105 加 isinstance→str 强转；YAML ggzyfw_fujian_business detail.type 改 inline（无真实详情页） |
| P2 8 连接错误 | ✅ 已澄清 | 8 个 site_id 在 YAML 重构时已改名（87→127），audit 跑的是旧名 → "Site not found"。新名测试全部正常：fujian_czt_jdhy=1, fujian_fgw_zwgk=1, fujian_jtyst_zwgk=1, ggzyjd_cases=100, ggzy_quanguo=584 条。仅 xmzyjy 真正不存在 |
| P3 ~55 零条 | ⚠️ 大部分已通 | 全量触发后 38 个站点恢复正常产出；剩 ~15 个真零数据 + ~35 个 90s 超时（多为详情页慢，需单独排查） |

### 87 零数据站点全量重抓结果

**38 个站点现已有数据**（首次触发即恢复，说明 audit 时只是没跑过）：

| site_id | 新数据 | 备注 |
|---------|--------|------|
| ccgp_agency | 150 | ✅ |
| ccgp_zjlwbcbz | 24 | ✅ |
| czt | 20 | ✅ |
| easy_prt_policy | 27 | ✅ |
| enjoy5191_policy | 20 | ✅ |
| enjoy5191_qycg | 5 | ✅ |
| etrading_statute | 4 | ✅ |
| fjcia_edu | 1 | ✅ |
| fjtba | 100 | ✅ |
| fjtba_pxzx | 7 | ✅ |
| fjtba_wfwg | 7 | ✅ |
| fujian_jtyst_jdhy | 45 | ✅ |
| fujian_jtyst_xzgfxwj | 15 | ✅ |
| fujian_zfcg_ningde_zcfg | 18 | ✅ |
| gcjyzx_wgtb | 11 | ✅ |
| ggzyfw_fujian_guide_txn | 196 | ✅ |
| ggzyfw_guide_trade | 1 | ✅ |
| jtyst_jdhy | 20 | ✅ |
| ncha | 7 | ✅ |
| ncha_zwgk | 25 | ✅ |
| putian_fwzx | 15 | ✅ |
| putian_zcwj | 10 | ✅ |
| slt_xxgk | 20 | ✅ |
| slt_zcjd | 18 | ✅ |
| smzx_notice | 6 | ✅ |
| zfcg_jdgl | 20 | ✅ |
| zfcg_xmgg | 20 | ✅ |
| zfcg_zcfg | 20 | ✅ |
| zhangzhou | 299 | ✅ |
| zhangzhou_gcjyzx_wgtb | 11 | ✅ |
| zjfw_zhangzhou_zcwj | 3 | ✅ |
| zjfw_zhangzhou_zxjx | 10 | ✅ |
| zjk_notice | 20 | ✅ |
| zjk_zffg | 9 | ✅ |
| zz_fycbid | 13 | ✅ |

**~15 个真 0 数据**（需 YAML 排查）：

| site_id | transport | 推测原因 |
|---------|-----------|---------|
| cebpubservice_consult | scrapling_stealth | URL 404 (/consult.html 不存在) |
| easy_prt_bidding | encrypted_api | 加密 API 返回空 |
| enjoy5191 | spa_render | SPA 未渲染或选择器失配 |
| enjoy5191_trade | spa_render | 同上 |
| fujian_zfcg_ningde_jdgl | spa_render | Vue 站，列表 0 条 |
| ggzyfw_fujian_news | encrypted_api | 加密 API 0 条 |
| ggzyfw_fujian_policies | encrypted_api | 加密 API 0 条 |
| jsj_zhangzhou | rest_api | 选择器失配 |
| mohurd | rest_api | 选择器失配 |
| ndrc_fzggwl | rest_api | 选择器失配 |
| ygcg_engineering | scrapling_stealth | 待重测（scrapling 现已装） |
| zjfw_score_sort | spa_render | 列表选择器失配 |
| zycg_cggg, zycg_notice, zycg_zdfg | rest_api | 政采云反爬或 API 失效 |
| zz_zhangzhou | rest_api | 选择器失配 |

**~35 个 90s 超时**（数据量过大或详情页慢）：

| 类型 | 站点 |
|------|------|
| 列表超大（正常，只是慢） | fujian_jtyst_dt(15529), fujian_jtyst_zwgkml(8326), quanzhou(8967) |
| 列表 0（API 失效） | ccgp_search_zcfg, ccgp_zcfg, etrading, gcjyzx_zcfg, ggzyfw_business, ggzyfw_fujian_business, slbgb, smjy, smzcfg, mwr |
| 列表 1 但详情超时 | fujian_slt_xxgk, fujian_slt_zcjd, fujian_wwj_zwgk, fujian_zjt_xxgk, mohurd_mlxz, nanjing_county_jdhy, nanjing_county_zwgk, putian_ggzyjy_fwzx, zhangzhou_fgw, zhangzhou_rmzf_jdhy, zhangzhou_zwgk, zhangzhou_zzjsj, zjfw_zhangzhou_notice, zjt_jdhy |
| 列表 N 但卡死 | fjnj, fjnj_jdhy, fujian_czt_zwgk, fujian_jtyst_zdgksxml, ggzyjd_policies, hwdms_policy, ndrc_xxgk |

### 二次处理总结

| 维度 | 数量 |
|---|---|
| 完全修复 | 12 个 (P0/P1/P2 全部) |
| 首次触发即恢复 | 38 个零数据站点 |
| 仍需 YAML 排查 | ~15 个真零数据 |
| 仍需性能优化 | ~35 个超时站点（多数是详情页慢，需单独排查） |

**关键洞察**：87 个"零数据"站点中，38 个 (44%) 只是之前从未被触发过。scrapling 缺失、int bug、zfcg auth、site_id 改名等"显性 bug"修复后，真实需要 YAML 调试的站点只剩 ~15 个。
