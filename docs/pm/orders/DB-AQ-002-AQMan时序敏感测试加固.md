# DB-AQ-002：MySQL 方言时序敏感测试加固

- 执行：AQMan
- 下达：2026-11-25（PM 派单）
- ETA：2026-11-25 当天
- 接单：用户在对话里告单号（AQMan 无轮询）；开工前先 pull 最新（本机同步程序）。

## 背景

AQMAN-ENV 报告（`docs/pm/reports/AQMAN-ENV-report-AQMan.md`，结论有条件通过，PM 已验收）条件一：`monitor/tests_dialect.py` 中 `MySQLSlowSqlDialectTests.test_live_fallback_reports_real_seconds_and_sorts_by_total`（L291–308）本机 9 次运行中失败 1 次。失败断言：全表扫描 4 次总耗时应大于主键点查 50 次（失败那次 0.007902s 对 0.008003s）。PM 裁决：属测试对执行时间敏感、低概率抖动，不是产品缺陷；按报告建议拉大工作量差距加固。

## 要求

1. 只改该用例的工作量数字：全表扫描由 4 次改为 20 次（点查保持 50 次）。不得改两处断言（总耗时比较、按总耗时排序），不得改为比较平均单次耗时；不得在 setUp 里清空 performance_schema（已证实无语句混入）。
2. 本机按 AQMAN-ENV 报告 §六方法重跑方言套件（MySQL/PG）至少 3 轮全量，均须通过；并跑一轮后端 unit 确认无回归。
3. 报告写到 `docs/pm/reports/DB-AQ-002-report-AQMan.md`，结论三档之一，附：改动 diff 摘要、3 轮读数（扫描/点查总耗时与比值）、CI 结论。

## 验收

PM 核验代码 diff + 本机读数 + 推送后 CI；通过后条件关闭。
