# C端「人事」页签设计 — 打卡考勤 / 请假审批 / 薪资核算 / 财务报表

> 日期：2026-08-31
> 状态：设计已获用户确认，待分阶段实施
> 需求来源：用户提供的 4 模块 20 功能点清单

## 0. 需求基线（已确认）

| 决策点 | 结论 |
|---|---|
| 使用主体 | 贵司内部员工，单组织，无租户隔离 |
| 考勤机对接 | 本期只做预留接口（HTTP sync endpoint + 导入批次表），不绑品牌 |
| 财务凭证 | 系统内生成 + Excel 导出，不对接用友/金蝶 |
| 角色权限 | 复用现有 RBAC，新增 `hr:manage`、`hr:finance` 权限点 |
| 工资条 | 员工可见自己的（发布后），仅本人 |
| 加班费 | 固定单价：工作日 15 元/h、周末 20 元/h、法定节假日 3 倍日薪，单价可配置 |
| 社保/个税 | 自动计算为默认（内置累计预扣税率表），HR 可手工覆盖，双值留痕 |

## 1. 架构方案

**方案A：独立人事模块**（已选定，否决了复用 flow_* 文件工作流的方案B）。

- 新建 `hr_*` 表 + `api/apps/restful_apis/hr_app.py` Blueprint（挂 `/hr` 前缀）
- C端 `mainView` 加 `'hr'` 页签（`web/src/pages/c-chat/index.tsx`），内部 4 个子页签
- 请假审批自建轻量多级审批链（表单为中心），复用现有通知系统（铃铛+Modal）触达审批人
- 理由：flow_* 核心语义是"文件+版本+串行批注"，请假单是"表单+状态机+余额扣减"，硬套污染两边模型

## 2. 数据库表（13张，含规则配置）

### 员工与档案

```
hr_employee            员工档案：id, user_id(unique), emp_no工号, department, position,
                         entry_date, status(active/resigned)
hr_salary_profile      薪资档案：employee_id, base_salary, post_allowance, meal_allowance,
                         transport_allowance, social_base, fund_base, special_deduction,
                         social_rate/fund_rate(留空走全局配置),
                         manual_overrides(JSON: 社保/公积金/个税手填覆盖值)
hr_rule_config         规则配置(单行JSON)：上下班时间、迟到阈值、迟到扣20/次、旷工扣3倍日薪、
                         加班单价15/20元时、法定3倍日薪、计薪天数21.75、审批链模板
```

### 考勤

```
hr_attendance_record   打卡流水(原始)：employee_id, punch_time, source(web/api_sync/import),
                         ip_address, device_raw(JSON预留考勤机字段)
hr_attendance_day      日汇总(清洗后)：employee_id, work_date, status(normal/late/absent/
                         leave/business_trip/rest/missing/abnormal), first_in, last_out,
                         late_minutes, overtime_hours, leave_id, locked
hr_attendance_month    月汇总：employee_id, month, attend_days, late_count, late_minutes,
                         absent_days, overtime_hours(weekday/weekend/holiday分列), leave_days,
                         status(draft/confirmed), confirmed_by
hr_attendance_import   同步批次(预留)：source, import_type(api/manual_excel), file,
                         record_count, success_count, status, error_msg
```

### 请假

```
hr_leave_request       假单：employee_id, leave_type(personal/sick/annual/marriage/
                         maternity/business_trip/other), start_time, end_time,
                         duration_days, reason, attachment_id,
                         status(pending/approved/rejected/cancelled/finished)
hr_leave_step          审批步骤：request_id, step_no, approver_id, status(pending/
                         approved/rejected), comment, action_time
hr_leave_balance       假期余额：employee_id, year, leave_type, total_days, used_days,
                         frozen_days(审批中冻结)
```

### 财务

```
hr_payslip             月薪资单：employee_id, month, 考勤快照(出勤/迟到/旷工/加班时长),
                         应发明细(基本+津贴+补贴+加班费), 扣款明细(考勤扣款+社保+公积金+个税),
                         gross_pay应发, net_pay实发, tax_snapshot(JSON累计预扣计算过程),
                         status(draft/trial/published), published_at
hr_payslip_adjust      调整日志：payslip_id, field, old_value, new_value, reason, operator_id
hr_voucher             财务凭证：month, voucher_type(accrue计提/pay发放), entries(JSON:
                         [摘要,科目,借,贷]), total_amount, status, created_by
```

### 关键设计决策

1. **打卡流水与日汇总分离**：清洗校验（去重/异常识别）发生在"流水→日汇总"推导时；补卡直接修正 day 表并留痕
2. **假单冻结机制**：`frozen_days` 审批中冻结余额，防并行假单超扣；通过转 `used_days`，驳回释放
3. **tax_snapshot 落盘**：个税累计预扣中间量落盘，财务可追溯，下月续算读上月快照
4. **手工覆盖留痕**：`manual_overrides` 手填值与自动计算值并列保存，试算对比展示
5. **迁移部署**：全部新表写入项目初始化脚本（遵循 CLAUDE.md 约束）；离职状态保留历史不物理删除

## 3. 后端 API（30个端点）与规则引擎

文件：`api/apps/restful_apis/hr_app.py`、`api/db/services/hr_service.py`、`api/db/services/hr_calculator.py`（纯函数、可独立单测）。

### 端点清单

```
考勤（员工）                          考勤（HR）
POST /hr/attendance/punch            签到/签退（记录IP）        GET  /hr/attendance/day-list       按月查全员日汇总
GET  /hr/attendance/today            今日打卡状态              POST /hr/attendance/repair        人工补卡审批
GET  /hr/attendance/calendar         个人考勤日历(月)          POST /hr/attendance/month-close   月度一键汇总
POST /hr/attendance/sync-api         预留：考勤机API同步        GET  /hr/attendance/month/:m      月汇总表
POST /hr/attendance/import           预留：Excel导入批次

请假                                  薪资（HR/财务）
POST /hr/leave                       提交假单                  GET/PUT /hr/salary-profile        薪资档案CRUD
GET  /hr/leave/my                    我的假单                   POST /hr/salary/calc              月度核算(批量生成draft)
GET  /hr/leave/pending               待我审批                   POST /hr/salary/trial             试算校验(不入库)
POST /hr/leave/:id/approve           审批通过/驳回              POST /hr/salary/publish           发布工资条
POST /hr/leave/:id/cancel            撤销                      POST /hr/salary/:id/adjust        手工调整(写日志)
GET  /hr/leave/balance               假期余额                   GET  /hr/salary/payslips          薪资明细表
                                                               POST /hr/voucher/generate         生成凭证(计提/发放)
员工自助                              GET  /hr/voucher/list                凭证列表
GET  /hr/payslip/my                  我的工资条                  GET  /hr/report/export            报表导出Excel(3种)
GET  /hr/employee/me                 我的档案                    GET  /hr/archive/search           历史归档检索(月/部门/姓名)
HR: GET/POST /hr/employee            员工建档/列表
GET  /hr/rule-config / PUT同          规则配置读写
```

### 规则引擎（hr_calculator.py）

```
1. derive_day_status(records, rule, leave)     考勤状态推导：无打卡→missing；首打>上班+阈值→late；
                                               半夜打卡(22:00-05:00)→abnormal待人工确认；
                                               同分钟重复去重；有效假单→leave/business_trip优先
2. calc_attendance_deduction(...)              迟到20元/次(可配阶梯)、旷工1天扣3倍日薪、日薪=base/21.75
3. calc_overtime(...)                          工作日15元/h + 周末20元/h + 法定3倍日薪，单价走rule_config
4. calc_tax(month, cumulative_income, ...)     个税累计预扣：起征点5000/月、7级超额累进、读上月快照续算
5. calc_payslip(profile, month_stats, ...)     应发=基本+岗位+餐补+交补+加班费；
                                               实发=应发-(考勤扣款+社保+公积金+个税)；overrides覆盖
```

### 关键流程

- **月度汇总**：month-close 遍历全员逐日推导 → draft → HR确认锁定；`missing` 日确认时按旷工或提醒人工处理
- **考勤自动修正**：假单终审通过 → 回写覆盖区间 day.status + 抵扣 attend_days；驳回/撤销回滚
- **审批链**：提交时按 `rule_config.approval_chain`（默认部门主管→HR管理员→3天以上追加总经理）实例化 hr_leave_step；逐步推进；每步通知审批人
- **发布工资条**：publish 后员工才可见；draft/trial 员工不可见

## 4. 前端结构

```
web/src/pages/c-chat/hr/
├── index.tsx                主页：子页签（考勤|请假|薪资|报表），按角色过滤可见性
├── attendance-view.tsx      员工：打卡卡片+IP+个人考勤日历(正常绿/迟到黄/旷工红/请假蓝/出差紫/缺卡灰)
│                            HR追加：全员月汇总表+补卡审批+一键月度汇总
├── leave-view.tsx           假单列表+审批进度条+新建弹窗+待我审批+假期余额卡片
├── salary-view.tsx          员工：我的工资条(发布后可见)；HR：档案管理(含手填覆盖开关)；
│                            财务：批量核算→试算→校验→发布+手工调整(强制填原因)
├── report-view.tsx          HR/财务：三种报表导出+凭证生成(计提/发放)+凭证列表+历史检索
└── types.ts
```

- `web/src/services/hr-service.ts` — 30 端点调用层，snake_case
- 未建档用户隐藏「人事」页签或显示"未开通"
- 权限点 `hr:manage`/`hr:finance` 注册进 `permission.ts`；前端按钮隐藏 + 后端 `@permission_required` 双重校验
- 文案全中文硬编码（不做 i18n 多语言同步）；C端配色 `#1a66fb` 系，显式写文字/背景色
- 不修改 `src/components/ui/` 内任何文件

## 5. 边界处理与安全

| 场景 | 处理 |
|---|---|
| 重复打卡 | 同分钟多条只取首(签到)/末(签退)，流水保留推导去重 |
| 半夜打卡 | 22:00–05:00 标 abnormal，不计有效状态，待HR确认 |
| 并行假单 | 提交即冻结余额，不足拒绝提交；审批中失败自动驳回 |
| 补卡 | 员工对 missing 日申请 → HR审批 → 回写留痕 |
| 月度锁定后变动 | confirmed 后拒绝回写，提示下月调整；day.locked 兜底 |
| 跨月假单 | 按自然日拆分归属各月独立修正 |
| 个税跨月依赖 | 上月无快照 → 从1月按当前累计补算，不静默清零 |
| 并发打卡 | (employee_id, 分钟, source) 防重 + DB 唯一约束 |
| 员工离职 | resigned 禁止打卡/请假，历史保留可筛 |
| 手工调整 | 仅 published 可调、强制原因、写日志、凭证提示重生成 |

安全：全接口登录校验；薪资/考勤严格限本人；`@permission_required` 控 HR/财务端点；报表导出校角色；Peewee 参数化防注入。

## 6. 测试策略（对抗性优先）

`hr_calculator.py` 纯函数单测重点：个税7级临界点±0.01元、跨月累计续算、空打卡/全缺卡、21.75日薪除法、2月/31号边界、假单覆盖全月、手填覆盖冲突。

API 层：越权访问他人工资条必须403、重复提交假单幂等、审批并发只成功一次、余额恰好用完边界。

## 7. 阶段拆分（每阶段独立交付上线）

| 阶段 | 内容 | 对应模块 |
|---|---|---|
| P1 | 员工档案 + 打卡考勤（表+建档+打卡+日历+清洗+月汇总） | 模块一（功能点1-5） |
| P2 | 请假审批联动（假单+审批链+考勤修正+余额扣减） | 模块二（功能点6-9） |
| P3 | 薪资核算（薪资档案+规则配置+核算引擎+试算+发布工资条） | 模块三（功能点10-14） |
| P4 | 财务凭证报表（凭证+调整+导出+归档）+ 考勤机API预留收尾 | 模块四（功能点15-20） |
