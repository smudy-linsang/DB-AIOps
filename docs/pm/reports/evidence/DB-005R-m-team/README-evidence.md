# DB-005R 证据清单（S-1：真实 MySQL 8.0 链路级回归 + G-13 三方互证）

入库副本已按 DB-005 同一口径脱敏：本地地址、端口、账号统一掩码为 `<local-host>` / `<platform-port>` / `<target-port>` / `<proxy-port>` / `<account>`；口令一律不出现在证据中（只经环境变量传入进程）。

| 文件 | 说明 | 字节 | 已脱敏 |
|---|---|---|---|
| `db005r_s1_log.json` | S-1 阶段日志（建实例/场景A/删索引/场景B/计划采集/API 验收/A-2/G-13） | 2086 | 否 |
| `db005r_s1_api.json` | S-1 API 链路级回归验收结果（慢列表/详情/建议/耗时构成/环比/告警，22 项） | 30010 | 是 |
| `db005r_s1_a2.json` | A-2 专项：索引存在时不重复建议同一索引 | 4983 | 否 |
| `db005r_g13.json` | G-13 探针：平台 EXPLAIN digest 在直连实例与 mock proxy 上的失败形态 + 目标库直证 | 7304 | 是 |
| `s1_run_A_client_summary.txt` | 场景A（索引在位）客户端侧 workload 汇总 | 1562 | 否 |
| `s1_run_B_client_summary.txt` | 场景B（索引已删）客户端侧 workload 汇总 | 1575 | 否 |
| `s1_lockwait_injection.txt` | 锁等待注入记录（A 持锁 20s / B 等锁） | 95 | 否 |
| `s1_plan_before.json` | 计划采集 before（索引在位） | 2253 | 是 |
| `s1_plan_after.json` | 计划采集 after（索引已删） | 3652 | 是 |
| `reproduce_s1_run.py` | S-1 全流程脚本（建实例→场景A→删索引→场景B→计划采集→API 验收→A-2→G-13） | 9632 | 是 |
| `reproduce_g13_probe.py` | G-13 探针脚本 | 7498 | 是 |
| `reproduce_api_check.py` | API 链路级验收脚本（22 项检查，DB-005 同一脚本） | 30836 | 是 |
| `reproduce_a2_check.py` | A-2 专项脚本（索引存在时不重复建议） | 5209 | 是 |
| `reproduce_reset_pw.py` | 本地开发账号口令轮换脚本（口令只经环境变量传入） | 676 | 否 |
| `reproduce_plan_capture.py` | 计划采集脚本（before/after 计划哈希） | 4540 | 是 |
