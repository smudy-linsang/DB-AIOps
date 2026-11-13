# DB-005R 证据清单（S-1：真实 MySQL 8.0 链路级回归 + G-13 三方互证 + v1.1/v1.2 G-11 回填 + 人工连驱 11-09 / 11-10 + 11-11 落桶核验与中断取证 + 11-12 D1 日周期复判 + 11-12 新窗口首日连驱与落桶核验 + 11-12 W18 轮失败取证 + 11-13 上午根因实测）

入库副本已按 DB-005 同一口径脱敏：本地地址、端口、账号、实例名统一掩码为 `<local-host>` / `<platform-port>` / `<mysql-port>` / `<proxy-port>` / `<account>` / `<instance-proxy>` / `<instance-mysql>`；口令与令牌一律不出现在证据中（口令只经环境变量 `MTEAM_PW` 传入进程，随机值不落盘；令牌只经 `Authorization: Bearer` 头在进程内使用）。

**共 65 份**（含本清单与 `manifest.json`）：44 份实测产物 + 19 份可复现脚本 + 本清单 + manifest。**实测产物（44 份数据类文件）敏感扫描命中 0**

| 文件 | 说明 | 字节 | 已脱敏 |
|---|---|---|---|
| `db005r_s1_log.json` | S-1 阶段日志（建实例/场景A/删索引/场景B/计划采集/API 验收/A-2/G-13） | 2086 | 否 |
| `db005r_s1_api.json` | S-1 API 链路级回归验收结果（慢列表/详情/建议/耗时构成/环比/告警，22 项） | 30010 | 是 |
| `db005r_s1_a2.json` | A-2 专项：索引存在时不重复建议同一索引 | 4983 | 否 |
| `db005r_g13.json` | G-13 探针：平台 EXPLAIN digest 在直连实例与 mock proxy 上的失败形态 + 目标库直证 | 7304 | 是 |
| `db005r_g11_day_baseline.json` | **G-11 回填（v1.1）**：cid=18/19 采集连续性（只读 `sql_stat` 覆盖与间隙）+ `baseline=day` / `baseline=week` / `sql-periodic` 三次同一接口调用结果 | 14965 | 是 |
| `db005r_g11_day_baseline_20261108.json` | **G-11 二轮回填（v1.2，2026-11-08 15:27）**：同一接口同一命令重跑 `baseline=day` / `baseline=week` / `sql-periodic`（cid=19 主、cid=18 次）的精简结果 + 采集连续性只读核算 | 34068 | 是 |
| `db005r_g11_day_baseline_20261108_full.json` | G-11 二轮回填的**完整版**输出（含全部 items 明细，供逐条核对） | 46247 | 是 |
| `g11_periodic_gate.json` | **日周期判定门槛机理诊断**：`sql-periodic` 在 days=3/4/5/6/7 的扫描结果 + 按平台同规则复刻（去掉 day_share 门槛）统计每个 digest 的**实际运行日数** + 全留存覆盖与逐本地日期行数 | 24244 | 是 |
| `g11_workload_hourly_buckets.txt` | 决定性事实：workload digest `1581b156…` 在 cid=18/19 的**小时执行桶**直查（证明压测负载在 11-07 15:27 之后已停止） | 239 | 否 |
| `g11_topology_probe.json` | **G-11 拓扑与采集管道只读核验**：mock proxy 后端指向（与 cid=19 同一后端）+ 近 4h 两路 digest 集合重合度（25/25 重合、独有 0/0）+ 两路采样推进情况 | 2808 | 是 |
| `g11_gate_math.json` | **G-11 判定窗边界复刻**：按 `monitor/sql_compare.py::detect_daily` 真实实现复刻，枚举 5 个判定时刻 × days=3/4/5/7 × cid=18/19 的判定结果（证明 days=3 复判必须落在 11-12 15:00 前） | 19970 | 是 |
| `g11_gate_math_magnitudes.json` | **G-11 判定窗边界复刻（执行量级敏感性）**：单路连驱 vs 两路并行（同后端双跑）两种日执行量下的判定对照 | 12320 | 是 |
| `g11_drive_20261109.json` | **首日人工连驱执行证据**（2026-11-09 15:05:01 起、45 分钟、计划任务 `MMMan-DBAIOps-G11DailyDrive`）：两路 workload `run` 模式（纯只读 SELECT）均 rc=0 + 客户端侧各查询次数与平均耗时 | 3015 | 是 |
| `g11_bucket_verify_20261109.json` | **首日连驱落库只读核验**（2026-11-09 15:51）：workload digest `1581b156…` 在 cid=18/19 的 **11-09 15:00 本地小时桶**执行量（各 21587）+ 采集间隙（>180s = 0）+ 累计 15 点运行日 | 1398 | 是 |
| `g11_drive_20261110.json` | **次日人工连驱执行证据**（2026-11-10 15:05:03 起、45 分钟、计划任务 `MMMan-DBAIOps-G11DailyDrive` 自动触发、Last Result 0）：两路 workload `run` 模式（纯只读 SELECT）均 rc=0 + 客户端侧各查询次数与平均耗时 | 2733 | 是 |
| `g11_bucket_verify_20261110.json` | **次日连驱落库只读核验**（2026-11-10 16:05，任务 `MMMan-DBAIOps-G11BucketVerify`）：workload digest `1581b156…` 在 cid=18/19 的 **11-10 15:00 本地小时桶**执行量（各 21617）+ 采集间隙（>180s = 0）+ 累计 15 点运行日 | 1703 | 是 |
| `g11_drive_20261111.json` | **第三轮人工连驱未执行取证**（2026-11-11 15:05:02 计划任务触发、Last Result=1，驱动日志无当日任何条目，两路 workload run 均未启动） | 1147 | 是 |
| `g11_bucket_verify_20261111.json` | **第三轮落桶只读核验**（2026-11-11 16:06，TSDB 恢复后）：workload digest `1581b156…` 在 cid=18/19 的 **11-11 15:00 本地小时桶 exec 均为 0**（桶不存在）+ 采集间隙指标口径说明 | 2031 | 是 |
| `g11_drive_20261112.json` | **新窗口首日人工连驱执行证据**（2026-11-12 15:05:02 起、45 分钟、计划任务 `MMMan-DBAIOps-G11DailyDrive` 自动触发、Last Result 0）：两路 workload `run` 模式（纯只读 SELECT）均 rc=0 + 客户端侧各查询次数与平均耗时 | 2670 | 是 |
| `g11_bucket_verify_20261112.json` | **新窗口首日连驱落库只读核验**（2026-11-12 16:05，任务 `MMMan-DBAIOps-G11BucketVerify`）：workload digest `1581b156…` 在 cid=18/19 的 **11-12 15:00 本地小时桶执行量**（各 21520）+ 采集间隙（>180s = 0；该指标只覆盖最近 30 分钟，非整窗断档数） | 1580 | 是 |
| `g11_collection_gap_20261111.json` | **采集中断量化**：两路最后采样 08:53:09/08:53:07，至 16:07 中断 434 分钟，11-11 09:00 起无任何 sql_stat 采样行、15:00 桶整桶无数据 | 1705 | 是 |
| `g11_rootcause_20261111.json` | **根因审计**：宿主主机 09:10:45 重启 → 时序库容器 09:09:16 停止（主库与时序库同端口）→ 采集器/Web 进程消失 → 11-11 连驱与落桶双缺失；含 TSDB 恢复动作与影响面 | 2356 | 是 |
| `reproduce_g11_verify_20261111.py` | 11-11 落桶只读核验脚本（TSDB 恢复后手动复核用） | 2587 | 是 |
| `reproduce_g11_gap_20261111.py` | 采集中断量化脚本（逐小时 rows 分布 + 最后采样时刻 + 11-11 全天样本统计） | 2382 | 是 |
| `g11_recheck_20261111.json` | **11-11 独立复核轮复验记录**（16:2x 第二会话）：重跑落桶只读核验、重跑 manifest 自洽脚本、重跑 reconcile（no drift）、宿主主机重启与容器/PID 存活性复核、计划任务 Last Result 复核 | 5435 | 是 |
| `g11_manifest_selfcheck_20261111.py` | manifest 自洽性独立复核脚本（只读）：目录实有文件数 vs file_count、逐文件字节 vs total_bytes、scan_scope 份数 vs 数据类实数、新增证据敏感串扫描 | 3427 | 是 |
| `g11_recovery_20261111.json` | **恢复取证（2026-11-11 恢复轮）**：采集器/Web 新 PID 与启动时刻、逐小时写入分布（证明无业务负载也持续写基线行 ⇒ 08:53 后无行＝采集器进程已死）、中断→恢复缺口量化（489.2 分钟）、新窗口装置与相关计划任务清单 | 27105 | 是 |
| `reproduce_g11_recovery_20261111.py` | 恢复取证可复现脚本（只读：进程/计划任务/DRIVE_DATES/sql_stat 连续性与逐小时写入分布） | 8242 | 是 |
| `g11_recovery_probe_20261111.json` | 恢复后双读采样前推核验（间隔 65s：sql_stat / metric_point / collection_snapshot 前后对照） | 3478 | 是 |
| `g11_recovery_probe_20261111.py` | 恢复后双读采样前推核验脚本（只读） | 3353 | 是 |
| `g11_recovery_digest_probe_20261111.json` | **G-11 目标实例采集通路端到端证明**：对两路探针各执行一条只读 SELECT 1 后采集是否落出新行（verdict=PASS；cid=19 +21 行、cid=18 +25 行 ⇒ 采集通路端到端可用） | 1409 | 是 |
| `g11_recovery_digest_probe_20261111.py` | G-11 目标实例采集通路端到端证明脚本（只读，不跑 workload） | 4566 | 是 |
| `g11_recovery_cadence_20261111.json` | 采样节奏核验：cid=19 130s 双读（+20 行、最大间隔 124.4s；配置 collect_interval_sec=60s）与 10 个纳管实例 collect_interval_sec 清单 | 2027 | 是 |
| `g11_recovery_cadence_20261111.py` | 采样节奏核验脚本（只读） | 3135 | 是 |
| `g11_evidence_mask_20261111.py` | 入库证据脱敏器（本地地址/端口/实例名按 cid 掩码；映射不嵌入真实实例名与账号） | 3384 | 是 |
| `g11_per_instance_coverage_20261111.json` | **全纳管实例采集覆盖实测**（近 30 分钟）：sql_stat 有行 5/10（MySQL 家族 digest 表口径）、metric_point 有行 8/10、collection_snapshot 有行 **10/10** ⇒ 10 个实例全部在采集 | 3364 | 是 |
| `g11_per_instance_coverage_20261111.py` | 全纳管实例采集覆盖实测脚本（只读，三张表口径对照） | 2916 | 是 |
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
| `reproduce_g11_periodic_gate.py` | **G-11 二轮诊断脚本（v1.2）**：扫描 `sql-periodic` 的 days 门槛表现 + 只读核算每 digest 实际运行日数（判定「采集时长不足」还是「负载已停」） | 8390 | 是 |
| `reproduce_reset_pw.py` | 本地开发账号口令轮换脚本（口令只经环境变量传入） | 676 | 否 |
| `reproduce_plan_capture.py` | 计划采集脚本（before/after 计划哈希） | 4540 | 是 |

| `g11_instance_coverage_20261111.json` | 纳管实例采集覆盖复算：逐实例 sql_stat / metric_point 覆盖与「无行」归因（PG 缺 pg_stat_statements、Oracle common 用户口令过期 ORA-65162、DM 缺 dmPython、GBase 无 performance_schema digest 口径）＋ gbase healthcheck 配置假阴性三步定性 | 4804 | 是 |
| `reproduce_g11_instance_coverage_20261111.py` | 纳管实例采集覆盖复算脚本（只读；实例名按 cid 掩码，不落端口/口令/地址） | 4746 | 是 |
| `db005r_g11_day_baseline_20261112.json` | **G-11 D1 复判（v1.4 口径订正；权威轮 2026-11-12 09:58:42，--periodic-days 3）**：cid=18/19 采集连续性（48h 窗、间隙 >180s 处数与样本）+ `baseline=day` / `baseline=week` / `sql-periodic` 同一接口同一 slot 口径重跑的完整输出（09:50 那轮因 argparse 缺省 7 静默覆盖 slot 口径已作废，未入库） | 33620 | 是 |
| `db005r_g11_day_baseline_20261112_compact.json` | G-11 D1 复判的**精简版**输出（入库证据口径；权威轮 09:58:42，--periodic-days 3） | 14294 | 是 |
| `g11_periodic_gate_20261112.json` | **G-11 日周期门槛机理诊断（D1 权威轮 09:58:42）**：`sql-periodic` days=3/4/5/6/7 端点扫描（全部 `items_returned=0`）+ 按平台同规则复刻核算每 digest **实际运行日数** + 全留存覆盖与逐本地日期行数 | 27424 | 是 |
| `g11_d1_summary_20261112.json` | D1 轮次 summary（权威轮 09:58:42，`periodic_days`＝3；内容取自已脱敏子对象）：`slot` / `rcs`（两脚本均 0）/ 两路接口判定摘要 + `side_effects` 口令轮换记录 | 11891 | 是 |
| `g11_d1_cmd0_20261112.txt` | D1 轮次 `day_baseline` 脚本 stdout（权威轮 09:58:42，--periodic-days 3；原 `.log` 按仓库 `*.gitignore` 约定改名为 `.txt`）：两路 continuity 摘要与三次接口调用回显 | 1214 | 否 |
| `g11_d1_cmd1_20261112.txt` | D1 轮次 `periodic_gate` 脚本 stdout（权威轮 09:58:42；原 `.log` 按仓库 `*.gitignore` 约定改名为 `.txt`）：两路 `retention` / `max_run_days` 与 days=3..7 端点扫描回显 | 733 | 否 |
| `db005r_g11_rerun.py` | **G-11 复判轮次驱动脚本**（宿主机侧，2026-11-12 10:4x 补入 slot→口径映射表 `SLOT_PERIODIC_DAYS`：d1_d3／d2_d3＝3、week_*＝7）：按 `--slot` 依次执行 `day_baseline` 与 `periodic_gate` 两个只读脚本；口令随机值只经环境变量传入、不落盘。含本地地址/端口占位默认值、无任何真实凭据（脚本类按本清单口径豁免敏感扫描） | 7607 | 否 |
| `db005r_g11_week_c18_cmd0_20261112.txt` | **W18 轮（11-12 20:35）cmd0 失败日志（入库掩码副本）**：该路驱动 rc=1 的完整输出，根因为 psycopg2 `OperationalError`「Connection refused (0x0000274D/10061)」（11-12 19:22 起时序库不可连）；原 `.log` 按仓库 `*.gitignore` 约定改名为 `.txt` 入库（沿用 D1 轮先例），原始未掩码副本留档 `probe_out` 不入库 | 5486 | 是 |
| `db005r_g11_week_c18_cmd1_20261112.txt` | **W18 轮（11-12 20:35）cmd1 失败日志（入库掩码副本）**：同一根因（时序库连接被拒），该路 rc=1；原 `.log` 改名为 `.txt` 入库 | 5496 | 是 |
| `db005r_g11_week_c18_summary_20261112.json` | **W18 轮（11-12 20:35）驱动摘要**：`rcs=[1,1]`、`errors` 两条 `FileNotFoundError`（上游产物未生成的**级联**）、`command_line` 含 `--periodic-days 7`（slot→口径映射正确 ⇒ **非口径问题**） | 868 | 是 |
| `g11_rootcause_20261113.json` | **11-13 上午只读实测根因汇总**：W18 轮失败事实、`sql_stat` 五路中断（max 11-12 19:23:17）、Postgres 连接池占满与占用方定位、采集守护两处缺陷与修正、今日三轮前提与待授权处置三选一 | 4743 | 是 |

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

# G-11 二轮回填（v1.2，2026-11-08 15:27）：与 v1.1 同一脚本、同一命令、同一接口
$env:BASE = 'http://127.0.0.1:8009'
python reproduce_g11_day_baseline.py --cid 19 --cid2 18 --window 24h --periodic-days 7 `
    --out 'C:\dispatcher\probe_out\db005r_g11_20261108.json' `
    --compact-out 'C:\dispatcher\probe_out\db005r_g11_20261108_evidence.json'

# G-11 二轮门槛机理诊断（v1.2 新增脚本）：只读 sql_stat + GET 平台接口
python reproduce_g11_periodic_gate.py --cid 18 --cid2 19 `
    --out 'C:\dispatcher\probe_out\g11_periodic_gate.json'


# G-11 首日人工连驱（2026-11-09 15:05–15:50）：计划任务自动触发 + 只读落库核验
python C:\dispatcher\g11_daily_drive.py            # 驱动（只跑 workload run 模式）
python C:\dispatcher\g11_verify_buckets.py           # 只读核验 15 点桶落库与采集间隙

# G-11 第三轮核验（2026-11-11 16:05–16:07）：连驱未执行 + 采集中断取证（全程只读，不驱动负载）
# 前置：时序库容器需可用（11-11 因宿主主机重启停止，已恢复后复核）
python reproduce_g11_verify_20261111.py             # 只读核验 11-11 15 点桶落库与采集间隙
python reproduce_g11_gap_20261111.py                 # 量化采集中断（逐小时 rows + 最后采样时刻）

# G-11 11-11 独立复核轮（16:2x 第二会话）：复跑上述只读核验 + manifest 自洽复核
python reproduce_g11_verify_20261111.py             # 复跑落桶核验（exec=0、桶不存在）
python g11_manifest_selfcheck_20261111.py          # manifest 四项口径自洽复核（逐文件差异 0）
python C:\dispatcher\g11_reconcile_and_commit.py   # manifest 订正（无漂移则不提交）

# G-11 恢复轮（2026-11-11 16:59–17:1x）：恢复采集器与 Web + 放开新窗口闸门（全程只读，不驱动负载）
python reproduce_g11_recovery_20261111.py     # 恢复取证：新 PID / 逐小时写入分布 / 中断缺口量化 / 装置与计划任务
python g11_recovery_probe_20261111.py        # 恢复后双读（65s）采样前推核验
python g11_recovery_digest_probe_20261111.py # G-11 目标实例采集通路端到端证明（只读 SELECT 1）
python g11_recovery_cadence_20261111.py     # 采样节奏核验（130s 双读 + 各实例采集周期）
python g11_evidence_mask_20261111.py        # 入库副本脱敏（本地地址/端口/实例名按 cid 掩码）

# G-11 D1 日周期复判（权威轮 2026-11-12 09:58:42；slot 口径表 d1_d3＝3；09:50 那轮口径作废）：同一脚本、同一接口，--periodic-days 按 slot 表取 3
python db005r_g11_rerun.py --slot d1_d3

# 等价直调（同一脚本、同一参数：--periodic-days 3，与 slot 口径表一致）
$env:BASE = 'http://127.0.0.1:8009'
python reproduce_g11_day_baseline.py --cid 18 --cid2 19 --window 24h --periodic-days 3 --out 'C:/dispatcher/probe_out/db005r_g11_d1_d3.json' --compact-out 'C:/dispatcher/probe_out/db005r_g11_d1_d3_compact.json'
python reproduce_g11_periodic_gate.py --cid 18 --cid2 19 --out 'C:/dispatcher/probe_out/g11_periodic_gate_d1_d3.json'

> ★**口径订正（2026-11-12 10:4x）**：驱动脚本 `db005r_g11_rerun.py` 已补入 **slot→口径映射表 `SLOT_PERIODIC_DAYS`**（`d1_d3`／`d2_d3`＝**3**、`week_c18`／`week_c19`／`week_mature`＝7）—— `--periodic-days` **不再是「缺省 7、只影响描述字段」**：它直接决定 `day_baseline` 内 `sql-periodic` 端点的**判定窗长与门槛 `ceil(days×0.7)`**，故 D1／D2 必须取 **3**。原 09:50 轮因 argparse 缺省 7 静默覆盖 slot 口径而**作废**（6 份产物以 `*.days7-superseded-20261112-0950.*` 留痕于本机 `C:\dispatcher\probe_out\`，未入库）；入库件为 **09:58:42 重跑（`--periodic-days 3`）**的权威轮。D1 的 days=3 判定结论由 `reproduce_g11_periodic_gate.py` 内部 sweep days=3..7 产出，两轮结论一致（NO），见 `g11_periodic_gate_20261112.json` 的 `endpoint_sweep`。

原始运行副本保留在本机 `C:\dispatcher\probe_out\`，**不入库**；仓库 `.gitignore` 含 `*.log`，故日志类证据入库时统一改名为 `.txt`（沿用 DB-005 先例）。

> ★**W18 轮（11-12 20:35）入库口径（2026-11-13 补记）**：该轮**已触发但无判定产物** —— 两路驱动 cmd0 rc=1／cmd1 rc=1，根因＝时序库连接被拒（`OperationalError`「Connection refused (0x0000274D/10061)」，11-12 19:22 起时序库不可连），summary `errors` 两条 `FileNotFoundError` 属**级联**；驱动已按 `SLOT_PERIODIC_DAYS` 传 `--periodic-days 7`（slot 口径正确 ⇒ **非口径问题**）。入库三份 W18 产物中，**两份 cmd 日志的掩码副本按上方 `.log`→`.txt` 约定改名入库**（`db005r_g11_week_c18_cmd0_20261112.txt` 5,486 B／`db005r_g11_week_c18_cmd1_20261112.txt` 5,496 B），**原始未掩码副本**（5,406 B／5,416 B）仍留档 `probe_out` 不入库；另入库 `g11_rootcause_20261113.json`（4,743 B，11-13 上午实测事实）。
