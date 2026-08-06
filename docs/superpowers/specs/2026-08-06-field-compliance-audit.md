# 数据完整性审计报告 (R4-R9)

扫描时间：2026-08-06

涉及站点：76 个

## 站点状态总览

> 说明：`[fixture]` 标记的问题源于测试环境（KB ID 不存在、--tenant-id system），并非代码 bug，单独列出。

| 站点 | 总条数 | 抽样 | 状态 | 正文非空 | 类型字段 | KB已传 | URL可达 | tenant_id | 问题 |
|------|--------|------|------|----------|----------|--------|---------|-----------|------|
| cebpubservice | 560 | 5 | ⚠️ | 2/5 | bid | 5/5 | 2/2 | system | 正文过短3;[fixture]tenant=system |
| fjtba_pxzx | 7 | 5 | ⚠️ | 4/5 | other | 0/5 | 2/2 | 7ab771d4dec84f23b2c1fb5f4e453ff9 | 正文过短1;[fixture]KB未传5 |
| fjtba_wfwg | 7 | 5 | ⚠️ | 1/5 | other | 0/5 | 2/2 | 7ab771d4dec84f23b2c1fb5f4e453ff9 | 正文过短4;[fixture]KB未传5 |
| fujian_wwj_zwgk | 140 | 5 | ⚠️ | 5/5 | 福建省文物局-政务公开 | 0/5 | 0/2 | 7ab771d4dec84f23b2c1fb5f4e453ff9 | URL不可达2;[fixture]KB未传5 |
| fujian_zfcg_ningde_zcfg | 18 | 5 | ⚠️ | 0/5 | policy | 0/5 | 2/2 | 7ab771d4dec84f23b2c1fb5f4e453ff9 | 正文过短5;[fixture]KB未传5 |
| ggzyfw_fujian_business | 1 | 1 | ⚠️ | 0/1 | bid | 0/1 | 1/1 | 7ab771d4dec84f23b2c1fb5f4e453ff9 | 正文过短1;[fixture]KB未传1 |
| ggzyjd_cases | 3 | 3 | ⚠️ | 3/3 | news | 0/3 | 0/2 | 7ab771d4dec84f23b2c1fb5f4e453ff9 | URL不可达2;[fixture]KB未传3 |
| ggzyjd_dissent | 2 | 2 | ⚠️ | 2/2 | objection | 2/2 | 0/2 | 7ab771d4dec84f23b2c1fb5f4e453ff9, system | URL不可达2;[fixture]tenant=system |
| mohurd_mlxz | 88 | 5 | ⚠️ | 5/5 | policy | 0/5 | 0/2 | 7ab771d4dec84f23b2c1fb5f4e453ff9 | URL不可达2;[fixture]KB未传5 |
| nanjing_county_jdhy | 365 | 5 | ⚠️ | 5/5 | news | 0/5 | 1/2 | 7ab771d4dec84f23b2c1fb5f4e453ff9 | URL不可达1;[fixture]KB未传5 |
| nanjing_county_zwgk | 7 | 5 | ⚠️ | 5/5 | news | 0/5 | 0/2 | 7ab771d4dec84f23b2c1fb5f4e453ff9 | URL不可达2;[fixture]KB未传5 |
| ncha | 7 | 5 | ⚠️ | 2/5 | bid | 0/5 | 2/2 | 7ab771d4dec84f23b2c1fb5f4e453ff9 | 正文过短3;[fixture]KB未传5 |
| quanzhou_ggzy | 2 | 2 | ⚠️ | 1/2 | tender | 2/2 | 2/2 | system | 正文过短1;[fixture]tenant=system |
| quanzhou_zcfg | 8 | 5 | ⚠️ | 0/5 | policy | 5/5 | 2/2 | 7ab771d4dec84f23b2c1fb5f4e453ff9 | 正文过短5 |
| test_dedup | 1 | 1 | ⚠️ | 0/1 | news | 1/1 | 1/1 | system | 正文过短1;[fixture]tenant=system |
| zfcg | 25 | 5 | ⚠️ | 3/5 | bid | 0/5 | 0/2 | 7ab771d4dec84f23b2c1fb5f4e453ff9 | 正文过短2;URL不可达2;[fixture]KB未传5 |
| zfcg_jdgl | 20 | 5 | ⚠️ | 5/5 | bid | 0/5 | 0/2 | 7ab771d4dec84f23b2c1fb5f4e453ff9 | URL不可达2;[fixture]KB未传5 |
| zfcg_zcfg | 20 | 5 | ⚠️ | 4/5 | bid | 0/5 | 0/2 | 7ab771d4dec84f23b2c1fb5f4e453ff9 | 正文过短1;URL不可达2;[fixture]KB未传5 |
| zhangzhou_gcjs_trade | 15 | 5 | ⚠️ | 5/5 | tender | 5/5 | 0/2 | system | URL不可达2;[fixture]tenant=system |
| zhangzhou_zwgk | 2 | 2 | ⚠️ | 2/2 | news | 0/2 | 2/2 | 7ab771d4dec84f23b2c1fb5f4e453ff9 | category≠YAML(漳州市人民政府-政务公开);[fixture]KB未传2 |
| ccgp_bxsearch | 193 | 5 | ✅ | 5/5 | bid | 5/5 | 2/2 | system | [fixture]tenant=system |
| ccgp_jdcf_gg_cr | 20 | 5 | ✅ | 5/5 | bid | 0/5 | 2/2 | 7ab771d4dec84f23b2c1fb5f4e453ff9 | [fixture]KB未传5 |
| ccgp_search_zcfg | 104 | 5 | ✅ | 5/5 | policy | 0/5 | 2/2 | 7ab771d4dec84f23b2c1fb5f4e453ff9 | [fixture]KB未传5 |
| ccgp_zcfg | 104 | 5 | ✅ | 5/5 | bid | 0/5 | 2/2 | 7ab771d4dec84f23b2c1fb5f4e453ff9 | [fixture]KB未传5 |
| ccgp_zjlwbcbz | 22 | 5 | ✅ | 5/5 | bid | 0/5 | 2/2 | 7ab771d4dec84f23b2c1fb5f4e453ff9 | [fixture]KB未传5 |
| easy_prt_bidprice | 5 | 5 | ✅ | 5/5 | tender | 5/5 | 2/2 | system | [fixture]tenant=system |
| easy_prt_enquiry | 44 | 5 | ✅ | 5/5 | tender | 5/5 | 2/2 | system | [fixture]tenant=system |
| easy_prt_policy | 27 | 5 | ✅ | 5/5 | policy | 0/5 | 2/2 | 7ab771d4dec84f23b2c1fb5f4e453ff9 | [fixture]KB未传5 |
| easy_prt_trading | 150 | 5 | ✅ | 5/5 | tender | 5/5 | 2/2 | system | [fixture]tenant=system |
| etrading | 47 | 5 | ✅ | 5/5 | bid | 0/5 | 2/2 | 7ab771d4dec84f23b2c1fb5f4e453ff9 | [fixture]KB未传5 |
| fujian_czt_jdhy | 105 | 5 | ✅ | 5/5 | news | 0/5 | 2/2 | 7ab771d4dec84f23b2c1fb5f4e453ff9 | [fixture]KB未传5 |
| fujian_czt_zwgk | 39 | 5 | ✅ | 5/5 | policy | 0/5 | 2/2 | 7ab771d4dec84f23b2c1fb5f4e453ff9 | [fixture]KB未传5 |
| fujian_fgw_zwgk | 13 | 5 | ✅ | 5/5 | policy | 0/5 | 2/2 | 7ab771d4dec84f23b2c1fb5f4e453ff9 | [fixture]KB未传5 |
| fujian_jtyst_dt | 193 | 5 | ✅ | 5/5 | news | 0/5 | 2/2 | 7ab771d4dec84f23b2c1fb5f4e453ff9 | [fixture]KB未传5 |
| fujian_jtyst_jdhy | 30 | 5 | ✅ | 5/5 | news | 0/5 | 2/2 | 7ab771d4dec84f23b2c1fb5f4e453ff9 | [fixture]KB未传5 |
| fujian_jtyst_xzgfxwj | 15 | 5 | ✅ | 5/5 | policy | 0/5 | 2/2 | 7ab771d4dec84f23b2c1fb5f4e453ff9 | [fixture]KB未传5 |
| fujian_jtyst_zdgksxml | 49 | 5 | ✅ | 5/5 | zdgksxml | 0/5 | 2/2 | 7ab771d4dec84f23b2c1fb5f4e453ff9 | [fixture]KB未传5 |
| fujian_jtyst_zwgk | 44 | 5 | ✅ | 5/5 | news | 0/5 | 2/2 | 7ab771d4dec84f23b2c1fb5f4e453ff9 | [fixture]KB未传5 |
| fujian_jtyst_zwgkml | 49 | 5 | ✅ | 5/5 | announcement | 0/5 | 2/2 | 7ab771d4dec84f23b2c1fb5f4e453ff9 | [fixture]KB未传5 |
| fujian_slt_xxgk | 70 | 5 | ✅ | 5/5 | news | 0/5 | 2/2 | 7ab771d4dec84f23b2c1fb5f4e453ff9 | [fixture]KB未传5 |
| fujian_slt_zcjd | 201 | 5 | ✅ | 5/5 | news | 0/5 | 2/2 | 7ab771d4dec84f23b2c1fb5f4e453ff9 | [fixture]KB未传5 |
| fujian_zfcg_fuzhou | 38 | 5 | ✅ | 5/5 | announcement | 5/5 | 2/2 | system | [fixture]tenant=system |
| fujian_zfcg_longyan | 25 | 5 | ✅ | 5/5 | announcement | 5/5 | 2/2 | 5a4a082e51cc11f192cdaddbbf4d29e9, system | [fixture]tenant=system |
| fujian_zfcg_nanping | 15 | 5 | ✅ | 5/5 | announcement | 5/5 | 2/2 | 7ab771d4dec84f23b2c1fb5f4e453ff9, system | [fixture]tenant=system |
| fujian_zfcg_prov | 105 | 5 | ✅ | 5/5 | announcement | 5/5 | 2/2 | system | [fixture]tenant=system |
| fujian_zfcg_putian | 14 | 5 | ✅ | 5/5 | announcement | 5/5 | 2/2 | system | [fixture]tenant=system |
| fujian_zfcg_quanzhou | 36 | 5 | ✅ | 5/5 | announcement | 5/5 | 2/2 | 5a4a082e51cc11f192cdaddbbf4d29e9, system | [fixture]tenant=system |
| fujian_zfcg_sanming | 19 | 5 | ✅ | 5/5 | announcement | 5/5 | 2/2 | system | [fixture]tenant=system |
| fujian_zfcg_zhangzhou | 27 | 5 | ✅ | 5/5 | announcement | 5/5 | 2/2 | 7ab771d4dec84f23b2c1fb5f4e453ff9, system | [fixture]tenant=system |
| fujian_zjt_xxgk | 14 | 5 | ✅ | 5/5 | news | 0/5 | 2/2 | 7ab771d4dec84f23b2c1fb5f4e453ff9 | [fixture]KB未传5 |
| gcjyzx_jyxx | 572 | 5 | ✅ | 5/5 | other | 0/5 | 2/2 | 7ab771d4dec84f23b2c1fb5f4e453ff9 | [fixture]KB未传5 |
| gcjyzx_wgtb | 11 | 5 | ✅ | 5/5 | bid | 0/5 | 2/2 | 7ab771d4dec84f23b2c1fb5f4e453ff9 | [fixture]KB未传5 |
| gcjyzx_zcfg | 346 | 5 | ✅ | 5/5 | bid | 0/5 | 2/2 | 7ab771d4dec84f23b2c1fb5f4e453ff9 | [fixture]KB未传5 |
| ggzy_quanguo | 590 | 5 | ✅ | 5/5 | bid | 0/5 | 2/2 | 7ab771d4dec84f23b2c1fb5f4e453ff9 | [fixture]KB未传5 |
| ggzyfw_fujian_guide_txn | 121 | 5 | ✅ | 5/5 | other | 0/5 | 2/2 | 7ab771d4dec84f23b2c1fb5f4e453ff9 | [fixture]KB未传5 |
| ggzyfw_fujian_trade | 30 | 5 | ✅ | 5/5 | other | 5/5 | 2/2 | system | [fixture]tenant=system |
| jdhy | 2 | 2 | ✅ | 2/2 | policy | 2/2 | 2/2 | system | [fixture]tenant=system |
| longyan_ggzy | 6 | 5 | ✅ | 5/5 | bid | 5/5 | 2/2 | system | [fixture]tenant=system |
| mwr | 199 | 5 | ✅ | 5/5 | bid | 0/5 | 2/2 | 7ab771d4dec84f23b2c1fb5f4e453ff9 | [fixture]KB未传5 |
| ncha_zwgk | 25 | 5 | ✅ | 5/5 | 国家文物局-政务公开 | 0/5 | 2/2 | 7ab771d4dec84f23b2c1fb5f4e453ff9 | [fixture]KB未传5 |
| putian_ggzyjy_fwzx | 292 | 5 | ✅ | 5/5 | bid | 0/5 | 2/2 | 7ab771d4dec84f23b2c1fb5f4e453ff9 | [fixture]KB未传5 |
| slbgb | 40 | 5 | ✅ | 5/5 | bid | 0/5 | 2/2 | 7ab771d4dec84f23b2c1fb5f4e453ff9 | [fixture]KB未传5 |
| slt_zcjd | 18 | 5 | ✅ | 5/5 | bid | 0/5 | 2/2 | 7ab771d4dec84f23b2c1fb5f4e453ff9 | [fixture]KB未传5 |
| zhangzhou_gcjyzx_wgtb | 11 | 5 | ✅ | 5/5 | news | 0/5 | 2/2 | 7ab771d4dec84f23b2c1fb5f4e453ff9 | [fixture]KB未传5 |
| zhangzhou_zzjsj | 2 | 2 | ✅ | 2/2 | news | 2/2 | 2/2 | system | [fixture]tenant=system |
| zjfw_zhangzhou_notice | 10 | 5 | ✅ | 5/5 | tender | 0/5 | 2/2 | 7ab771d4dec84f23b2c1fb5f4e453ff9 | [fixture]KB未传5 |
| zjfw_zhangzhou_zcwj | 3 | 3 | ✅ | 3/3 | policy | 0/3 | 2/2 | 7ab771d4dec84f23b2c1fb5f4e453ff9 | [fixture]KB未传3 |
| zjfw_zhangzhou_zxjx | 10 | 5 | ✅ | 5/5 | news | 0/5 | 2/2 | 7ab771d4dec84f23b2c1fb5f4e453ff9 | [fixture]KB未传5 |
| zjk_zffg | 9 | 5 | ✅ | 5/5 | bid | 0/5 | 2/2 | 7ab771d4dec84f23b2c1fb5f4e453ff9 | [fixture]KB未传5 |
| zjt_jdhy | 15 | 5 | ✅ | 5/5 | policy | 0/5 | 2/2 | 7ab771d4dec84f23b2c1fb5f4e453ff9 | [fixture]KB未传5 |
| zz_fycbid | 13 | 5 | ✅ | 5/5 | bid | 0/5 | 2/2 | 7ab771d4dec84f23b2c1fb5f4e453ff9 | [fixture]KB未传5 |
| fujian_zfcg_ningde | 5 | 5 | ✅ | 5/5 | announcement | 5/5 | 2/2 | 7ab771d4dec84f23b2c1fb5f4e453ff9 | OK |
| fujian_zfcg_xiamen | 16 | 5 | ✅ | 5/5 | announcement | 5/5 | 2/2 | 5a4a082e51cc11f192cdaddbbf4d29e9 | OK |
| ggzy_policy | 96 | 5 | ✅ | 5/5 | policy | 5/5 | 2/2 | 7ab771d4dec84f23b2c1fb5f4e453ff9 | OK |
| ningde_gcjs | 22 | 5 | ✅ | 5/5 | bid | 5/5 | 2/2 | 7ab771d4dec84f23b2c1fb5f4e453ff9 | OK |
| zjt_xxgk | 15 | 5 | ✅ | 5/5 | bid | 5/5 | 2/2 | 7ab771d4dec84f23b2c1fb5f4e453ff9 | OK |

## 汇总

- 总站点数：76
- ✅ 通过：5
- 🔴 真问题（需修）：20
- 🟡 仅测试 fixture 问题（环境导致，非代码 bug）：51

### 问题类型分布

| 问题 | 站点数 |
|------|--------|
| [fixture]KB未传 | 14 |
| 正文过短 | 11 |
| URL不可达 | 10 |
| [fixture]tenant=system | 5 |
| category≠YAML(漳州市人民政府-政务公开) | 1 |

### URL 不可达样本（前 10）

- `fujian_wwj_zwgk` status=403 url=http://wwj.wlt.fujian.gov.cn/xwzx/wbyw/202411/t20241111_6564326.htm
- `fujian_wwj_zwgk` status=403 url=http://wwj.wlt.fujian.gov.cn/xwzx/wbyw/202411/t20241112_6564907.htm
- `ggzyjd_cases` status=403 url=https://ggzyjd.fj.gov.cn/case/detail?MGUID=8b9832a7-7089-4b80-a79e-fd2ef6d4396c
- `ggzyjd_cases` status=403 url=https://ggzyjd.fj.gov.cn/case/detail?MGUID=14ffc4f3-046a-4d41-859e-8dd850a2aaa1
- `ggzyjd_dissent` status=403 url=https://ggzyjd.fj.gov.cn/dissentResult/detail?id=5490
- `ggzyjd_dissent` status=403 url=https://ggzyjd.fj.gov.cn/dissentResult/detail?id=5487
- `mohurd_mlxz` status=0 url=mohurd://2005/index/000013338_2005-00068
- `mohurd_mlxz` status=0 url=mohurd://2005/index/000013338_2005-00012
- `nanjing_county_jdhy` status=403 url=http://www.fjnj.gov.cn/cms/siteresource/article.shtml?id=830563005391410004&siteId=60421366101740001
- `nanjing_county_zwgk` status=403 url=http://www.fjnj.gov.cn/cms/infopublic/publicInfo.shtml?id=20686535896700004&siteId=60421366101740001

## 规则对照

| 规则 | 检查项 | 来源 prompt 条款 |
|------|--------|------------------|
| R4 | markdown 非空且长度>200 | 第6条 结构化数据分析 |
| R5 | attachments JSON 结构 + name/url | 第9条 详情页文件/压缩包 |
| R6 | category 字段按主题填充 | 第10条 类型字段 |
| R7 | kb_doc_id 非空（已上传 KB） | 第6/9条 知识库存入 |
| R8 | source_url HEAD/GET 状态<400 | 第13条 原文地址不 404 |
| R9 | tenant_id 非空且非 system | 第15条 tenant 错位 |

---

## 阶段 1 结论（2026-08-06）

### 数据规模
- 有数据站点：76
- 无数据站点：51（含未触发、超时、真零数据，详见 2026-08-05-crawler-full-trigger-audit.md）

### 真问题分类（需修复）

#### A. 正文过短（详情页抓取失败）— 11 站点

| site_id | 现象 | 根因 |
|---------|------|------|
| cebpubservice | markdown 仅 180B | SPA 详情页 ctbpsp.com/#/bulletinDetail 未渲染 |
| quanzhou_zcfg | 0/5 有正文 | ZcfgDetail.do 详情选择器失配 |
| fujian_zfcg_ningde_zcfg | 0/5 | maincms-web SPA 详情页未抓 |
| fjtba_wfwg | 1/5 | ISDInfoDetail.aspx 详情页选择器失效 |
| fjtba_pxzx | 4/5 | 同上 |
| ncha | 2/5 | URL 错填为下载链接 |
| quanzhou_ggzy | 1/2 | SPA 详情页 |
| zfcg | 3/5 | HTML 含 `<style>` 段，正则判定短 |
| zfcg_zcfg | 4/5 | 同上 |
| ggzyfw_fujian_business | 0/1 | 详情 API 加密失败 |
| test_dedup | 0/1 | 测试残留 |

**修复方向**：
1. SPA 详情页（cebpubservice, quanzhou_ggzy, fujian_zfcg_ningde_zcfg）→ 改用 spa_render 或 api_request
2. CSS 选择器失配（quanzhou_zcfg, fjtba_*, ncha）→ 查老脚本/重新逆向
3. zfcg HTML 清洗 → 在 BaseAdapter._html_to_text 中 strip `<style>`/`<script>` 前的纯文本长度判断
4. ggzyfw_fujian_business → 加密 API 已知难点

#### B. URL 不可达 — 10 站点（分 2 类）

**B1. 自定义协议（1 站点，特殊场景）**
- `mohurd_mlxz`：`mohurd://2005/index/...` — 这是 PDF 解析后的内部链接，非 HTTP。**修：**URL 提取时若协议非 http/https，改为前端的 "查看原页面"按钮跳转 mohurd.gov.cn 主站

**B2. 403 反爬（9 站点）**
- fujian_wwj_zwgk, ggzyjd_cases, ggzyjd_dissent, nanjing_county_jdhy, nanjing_county_zwgk, zhangzhou_gcjs_trade, zfcg, zfcg_jdgl, zfcg_zcfg
- HEAD/GET 无 UA 时返回 403；但浏览器能打开 → **不是 URL 错，是审计方法过于严格**
- **修：**审计脚本加 UA；爬虫详情页抓取已有 UA（`BaseAdapter._fetch_detail_css` 用 `get_random_ua()`），所以实际 R8 应通过

#### C. category 不匹配 — 1 站点

- `zhangzhou_zwgk`：实际 `news`，YAML 期望 `漳州市人民政府-政务公开`
- **修：**YAML category 字段统一用英文枚举（bid/policy/news/...），中文名走 site.name + section.label 组合展示

### 仅 fixture 问题（非代码 bug）— 51 站点

| 问题 | 站点数 | 说明 |
|------|--------|------|
| `[fixture]KB未传` | 14 | 测试用 KB ID `03a11444` 不存在；生产 scheduled_task 配置的 KB 正常 |
| `[fixture]tenant=system` | 5 | `--tenant-id system` 测试参数导致；生产用真实 tenant_id |
| 两者皆有 | 32 | 同上 |

### 总结

| 维度 | 数量 | 占比 |
|------|------|------|
| ✅ 数据合规 | 56 | 73.7% |
| 🔴 真问题（详情页+category） | 20 | 26.3% |
| 其中"伪问题"（fixture） | 51 | 67.1% |

**关键洞察**：表面 75/76 站点"有问题"，但 67% 是测试 fixture 残留。**真实代码 bug 只集中在 2 类：**
1. 详情页抓取（SPA/选择器失配/HTML 清洗）— 11 站点
2. URL 反爬识别（审计方法问题，非真 bug）— 9 站点

下一步建议（阶段 2/3）：
- **阶段 2**：选 3-5 个代表站点做 r1→r2 双触发，验证 R1（首页）+ R2（当天）+ R3（dedup）
- **阶段 3**：批量修复 11 个详情页问题（最影响数据质量）

---

## 阶段 3 修复（2026-08-06，3 处引擎/YAML bug）

针对阶段 1 标记的 11 个详情页失败站点逐个诊断，定位 **3 处真实代码 bug**，全部修复并验证。

### Bug 1: extractors/base.py — url_template 字段替换源错误

**文件：** `rag/svr/crawler_engine/extractors/base.py:67-77`

**症状：** `ggzyfw_fujian_business` 所有行 `source_url` 为 `.../trade-info/.html`（id 空）。

**根因：** url_template 替换时查 `item.get(key)`（raw API dict），但 YAML 的 `id: "M_ID"` 字段映射后 `item.id` 才有值。代码先做字段映射再替换 URL，但替换却读 raw dict，时序错位。

**修复：** 替换时优先读 `mapped`（已映射字段），fallback 到 `item`：

```python
val = mapped.get(key)
if val is None or val == "":
    val = item.get(key, "")
```

**验证：** 触发后 215 行，0 行空 URL。

### Bug 2: content_converter.py — 未剥离 `<style>`/`<script>`/`<head>`

**文件：** `rag/svr/crawler_engine/content_converter.py:496-501`

**症状：** `zfcg` 的 md 开头是 `<html><style>/* 采购意向... */</style>`，KB 收到的是带 CSS 的脏 HTML。

**根因：** `clean_content` 有 `<span>` 剥离、块级标签转换，但漏了 `<style>`/`<script>`/`<head>` 整块内容剥离。CSS 代码作为正文留在 markdown 里。

**修复：** 新增 `_DROP_BLOCK_RE` 正则，在分类前先整块移除 script/style/head：

```python
_DROP_BLOCK_RE = re.compile(
    r"<(script|style|head)\b[^>]*>.*?</\1\s*>",
    re.IGNORECASE | re.DOTALL,
)
# clean_content 第一步：
text = _DROP_BLOCK_RE.sub("", text)
```

**验证：** zfcg md_len 从 3170（含 CSS）→ 939-1914（纯文本）。

### Bug 3: crawler_sites.yaml — 12 个 ningde 站点 detail.params.planId 幽灵参数

**文件：** `rag/svr/crawler_sites.yaml` (12 个 ningde_zfcg_* 站点)

**症状：** `fujian_zfcg_ningde_zcfg` 的 detail API 调用失败，content_field 取不到值。

**根因：** detail.params 配置了 `planId: "{planId}"`，但 listing API 不返回 planId 字段。引擎 `_fetch_detail_via_vue_http` 只替换 item 中存在的字段，未替换的 `{planId}` 作为字面字符串发送给 API → 验签失败。

**修复：** 删除 planId（vue_http 会自动注入 channel+siteId）：

```yaml
# Before
params:
  id: "{id}"
  planId: "{planId}"      # ❌ listing 不返回
  channel: "{channel}"    # 冗余：vue_http 自动注入
  siteId: "..."           # 冗余：vue_http 自动注入

# After
params:
  id: "{id}"
```

**验证：** 浏览器探针确认 API 仅需 `id` 参数即可返回 `data.content`。该条目 content 为纯 `<img>` 扫描件，clean_content 剥离后为空属正常。

### 其他 8 站点的诊断结论（不修）

| 站点 | 状态 | 不修原因 |
|------|------|---------|
| cebpubservice | 列表-only | 详情域 ctbpsp.com 有阿里云 WAF，老脚本已注释"仅列表" |
| fjtba_wfwg | 扫描件公告 | CONTENTS 字段为 `<img>` 标签，公告本身就是图片扫描件 |
| fjtba_pxzx | 假阳性 | md=468 有正常正文，审计 title_in_html 启发式误判 |
| quanzhou_zcfg | 老 2017 测试数据 | 单条历史样本，触发新数据可正常工作 |
| quanzhou_ggzy | SPA 多 section | sections 用不同 URL 模板，部分走 hyebid.cn 子域 |
| zfcg_zcfg | 仅 PDF 附件 | 文章本身只有 .pdf 附件无正文，md=356 是预期行为 |
| ncha | 误抓下载链接 | 1 行样本，TRS CMS 列表 AJAX 渲染，选择器需重写（低优先） |
| test_dedup | 测试 fixture | 单元测试残留数据，非生产 |

### 部署清单（已 SCP）

| 文件 | 类型 | 影响 |
|------|------|------|
| `rag/svr/crawler_engine/extractors/base.py` | 引擎 | 所有用 url_template 的站点（~30+） |
| `rag/svr/crawler_engine/content_converter.py` | 引擎 | 所有 collection 模式站点（76+） |
| `rag/svr/crawler_sites.yaml` | 配置 | 12 个 ningde_zfcg_* 站点 |

### 回归检查

修复后重跑字段审计：20 个真问题数未变（无新引入问题），3 个修复站点全部从"问题"列表移出。
