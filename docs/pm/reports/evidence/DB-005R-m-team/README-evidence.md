# DB-005R 证据清单（S-1：真实 MySQL 8.0 链路级回归 + G-13 三方互证 + v1.1 G-11 回填）

入库副本已按 DB-005 同一口径脱敏：本地地址、端口、账号、实例名统一掩码为 `<local-host>` / `<platform-port>` / `<mysql-port>` / `<proxy-port>` / `<account>` / `<instance-proxy>` / `<instance-mysql>`；口令与令牌一律不出现在证据中（口令只经环境变量 `MTEAM_PW` 传入进程，随机值不落盘；令牌只经 `Authorization: Bearer` 头在进程内使用）。

**共 19 份**（含本清单与 `manifest.json`）：10 份实测产物 + 8 份可复现脚本 + 本清单 + manifest。**实测产物（10 份数据类文件）敏感扫描命中 0**（沿用 v1.0 口径，清单见 `manifest.json` 的 `scan_list` / `scan_scope`）。脚本与本清单内出现的 `127.0.0.1` / `8009` / `MTEAM_PW=<…>` / `'Bearer ' + token` 等为**占位默认值与代码片段**，不含任何真实凭据值。

| 文件 | 说明 | 字节 | 已脱敏 |
|---|---|---|---|
| `db005r_s1_log.json` | S-1 阶段日志（建实例/场景A/删索引/场景B/计划采集/API 验收/A-2/G-13） | 2086 | 否 |
| `db005r_s1_api.json` | S-1 API 链路级回归验收结果（慢列表/详情/建议/耗时构成/环比/告警，22 项） | 30010 | 是 |
| `db005r_s1_a2.json` | A-2 专项：索引存在时不重复建议同一索引 | 4983 | 否 |
| `db005r_g13.json` | G-13 探针：平台 EXPLAIN digest 在直连实例与 mock proxy 上的失败形态 + 目标库直证 | 7304 | 是 |
| `db005r_g11_day_baseline.json` | **G-11 回填（v1.1）**：cid=18/19 采集连续性（只读 `sql_stat` 覆盖与间隙）+ `baseline=day` / `baseline=week` / `sql-periodic` 三次同一接口调用结果 | 14965 | 是 |
| `s1_run_A_client_summary.txt` | 场景A（索引在位）客户端侧 workload 汇总 | 1562 | 否 |
| `s1_run_B_client_summary.txt` | 场景B（索引已删）客户端侧 workload 汇总 | 1575 | 否 |
| `s1_lockwait_injection.txt` | 锁等待注入记录（A 持锁 20s / B 等锁） | 95 | 否 |
| `s1_plan_before.json` | 计划采集 before（索引在位） | 2253 | 是 |
| `s1_plan_after.json` | 计划采集 after（索引已删） | 3652 | 是 |
| `reproduce_s1_run.py` | S-1 全流程脚本（建实例→场景A→删索引→场景B→计划采集→API 验收→A-2→G-13） | 9632 | 是 |
| `reproduce_g13_probe.py` | G-13 探针脚本 | 7498 | 是 |
| `reproduce_api_check.py` | API 链路级验收脚本（22 项检查，DB-005 同一脚本） | 30836 | 是 |
| `reproduce_a2_check.py` | A-2 专项脚本（索引存在时不重复建议） | 5209 | 是 |
| `reproduce_g11_day_baseline.py` | **G-11 回填脚本（v1.1）**：同一接口重跑日环比/上周同窗/日周期 SQL，并只读核算采集连续性 | 15854 | 是 |
| `reproduce_reset_pw.py` | 本地开发账号口令轮换脚本（口令只经环境变量传入） | 676 | 否 |
| `reproduce_plan_capture.py` | 计划采集脚本（before/after 计划哈希） | 4540 | 是 |

## 复现命令

```powershell
# S-1 全流程（v1.0）
$env:MTEAM_PW = '<本地开发账号口令>'
$env:BASE = 'http://127.0.0.1:8009'
$env:TDSQL_PROBE_PASSWORD = '<目标库口令>'
python reproduce_s1_run.py --out 'C:\dispatcher\probe_out\db005r_s1_api.json'

# G-11 回填（v1.1）：同一接口、同一命令重跑
$env:BASE = 'http://127.0.0.1:8009'
python reproduce_g11_day_baseline.py --cid 18 --cid2 19 --window 24h --periodic-days 7 `
    --out 'C:\dispatcher\probe_out\db005r_g11.json' `
    --compact-out 'C:\dispatcher\probe_out\db005r_g11_evidence.json'
```

> `reproduce_g11_day_baseline.py` 的认证优先用平台自身 `TokenManager` 签发令牌（不动口令）；本地开发态 token 存 Django LocMemCache、跨进程不可见时，脚本按预设回退路径重置本地开发账号口令并登录，该副作用写入证据 `side_effects` 字段（**口令值不入证据**）。脚本全程只读 `sql_stat`、不改业务数据、不重启任何平台进程。

原始运行副本保留在本机 `C:\dispatcher\probe_out\`，**不入库**；仓库 `.gitignore` 含 `*.log`，故日志类证据入库时统一改名为 `.txt`（沿用 DB-005 先例）。
