有条件通过

# DB-005R 交付报告（当日连驱核验分册）：G-11 人工连驱 2026-11-10

- 任务单：`docs/pm/orders/DB-005R-补充-日周期人工连驱.md`（2026-11-08 muse 裁决 (a) 人工连驱）
- 母单：`docs/pm/orders/DB-005R-005复测回归确认.md`；统筹单：`docs/pm/orders/DB-015-DB-004-005-005R改派MMMan外网测试.md`
- 执行：MMMan（M-team team lead）
- 本件性质：**当日连驱核验与上板分册**（兜底扫描轮「G-11 连驱当日记录缺失」自动叫醒，2026-11-10 16:01）
- 母报告：`docs/pm/reports/DB-005R-report-m-team.md` —— **仍为 v1.2（结论档位不变）**；**v1.3 保留给 11-12 09:50 `G11Rejudge-D1` 的 days=3 实测回填**，本件不顶替、不重写母报告
- 接单回执：`docs/pm/reports/DB-005R-receipt-m-team.md` §12（本次新增章节，编号接续既有最大号 §11，**未重写任何同号章节**）
- 证据目录：`docs/pm/reports/evidence/DB-005R-m-team/`（**31 份** = 21 份实测产物 + 8 份可复现脚本 + `README-evidence.md` + `manifest.json`）

## 结论

**有条件通过** —— 本轮只做**核验与上板**，不重跑负载（任务单明令）。四项核验全部实测通过：① 2026-11-10 有 `drive_start`（15:05:03，minutes=45）与 `drive_done`，两路 `drive_instance_done` **rc=0**；② 候选 digest `1581b156…` **已落进 11-10 15:00 本地小时桶，两路执行量相同（各 21617）**，`gap_count_gt_180s_30min` = **0**；③ 回执 §12 按「不重号」规矩新增（接续 §11）；④ manifest 的 bytes / file_count / total_bytes / scan_scope 四项口径**已按目录实测订正且自洽**（31 份 / 307966 B / 逐文件差异 0）。

**条件**：G-11 **日周期闭合仍未达成** —— 按 §9 订正 1 的判定窗算术，11-12 09:50 的 days=3 判定窗 `[11-09 09:50, 11-12 09:50)` 内目前只有 11-09、11-10 两个 15 点桶，**仍需 11-11 15:05 第三轮落桶**方能在窗内凑满 3 个运行日。故本轮**不提前做 days=3 复判**，母报告结论档位维持 v1.2「有条件通过」，缺陷单仍为空。

---

## 1. 当日驱动执行核验（只读核验，不重跑负载）

| 项 | 实测值 | 依据 |
|---|---|---|
| 触发方式 | 计划任务 `MMMan-DBAIOps-G11DailyDrive` **自动触发**：Last Run **2026-11-10 15:05:01** → Last Result **0** | `Get-ScheduledTaskInfo` |
| `drive_start` | **15:05:03**，`minutes=45`，targets = cid 19（<instance-mysql>）+ cid 18（<instance-proxy>） | `C:\dispatcher\g11_drive_log.jsonl` |
| `drive_instance_done` | cid=19 **rc=0**、cid=18 **rc=0**，均 **15:50:04** | 同上 |
| `drive_done` | 两路 rc 均为 **0**（结果项 rc=0/rc=0） | 同上 |
| 11-10 `skip` 事件 | **无**（未因小时/日期闸门跳过） | 同上 |
| 客户端侧实测 | cid=19：slow_user_sort **1540** 次/20.4ms、agg_no_shardkey 520/39.1ms、range_no_key 516/15.3ms、pk_lookup 10886/1.9ms；cid=18：1601/22.6ms、565/41.8ms、559/17.2ms、10738/4.0ms | `probe_out\g11_drive_20261110_{18,19}.log` |
| 客户端侧对照 | 与 11-09 同量级（11-09：1567/1586、10828/10763）⇒ **无异常劣化** | `g11_drive_20261109.json` |

## 2. 落桶核验（决定性证据）

| 项 | cid=19（直连 MySQL 8.0） | cid=18（mock proxy） |
|---|---|---|
| **11-10 15:00 本地小时桶执行量** | **21617** | **21617** |
| `gap_count_gt_180s_30min`（近 30h 采样间隙 >180s 计数） | **0** | **0** |
| `max_time` | 2026-11-10 08:03:09.084492+00:00 | 2026-11-10 08:05:06.794408+00:00 |
| `rows_total` | 76399 | 84532 |
| 15 点桶历史（含本日） | 11-06: 5722 / 11-09: 21587 / **11-10: 21617** | 11-06: 5798 / 11-09: 21587 / **11-10: 21617**（另有 11-05 20:00 桶 5960） |

- 核验装置：计划任务 `MMMan-DBAIOps-G11BucketVerify`（Last Run **16:05:01** → Last Result **0**），脚本 `C:\dispatcher\g11_verify_buckets.py` —— **只读** `sql_stat` 查询 + 本地时区换算，不驱动负载、不改数据、不重启进程。
- 原始行：`C:\dispatcher\probe_out\g11_bucket_verify.jsonl` 末行（`at=2026-11-10T16:05:02.074486+08:00`）；入库副本 `g11_bucket_verify_20261110.json`（已按 DB-005 同口径脱敏）。
- **两路执行量相同**符合预期：§9 已实测 `docker inspect tdsql-proxy` 证实 proxy 与直连**指向同一后端**，两路并行 = 同后端双跑（证据 `g11_topology_probe.json`，digest 重合 25/0）。
- **21617 与首日 21587 的 +30 差异**属分钟级采样抖动，与「两路并行、同后端、同 workload」的量级假设一致（对照 `g11_gate_math_magnitudes.json`）。

## 3. 15 点运行日累计与 days=3 复判时点

| 口径 | cid=19 | cid=18 |
|---|---|---|
| 全量 15 点运行日 | **3**（11-06、11-09、11-10） | **3**（11-06、11-09、11-10） |
| **11-12 09:50 判定窗 `[11-09 09:50, 11-12 09:50)` 内** | **2**（11-09、11-10；11-06 已滑出窗） | **2**（同上；11-05 20:00 桶按 `hour_share=0.8` 不入候选小时） |
| 结论 | **未达 days=3** —— 仍需 11-11 15:05 第三轮落桶 | 同左 |

> 这正是 §9 订正 1 的算术：`detect_daily` 判定窗为 `[判定时刻 - days, 判定时刻)`，11-06 的桶在 11-12 09:50 已出窗。**11-11 是窗内凑满 3 个运行日的最后一块**，缺它则 days=3 两路皆 NO。本轮**未提前复判**（任务单第 6 条明令）。

## 4. manifest 四项口径订正与自洽核验

按任务单第 4 条执行 `C:\dispatcher\g11_reconcile_and_commit.py`（内含 `g11_manifest_reconcile.py`，**按目录实测**重算，不手填）：

| 口径 | 订正后 | 自洽核验（实测） |
|---|---|---|
| `file_count` | **31** | 目录实有 30 份（不含 manifest 自身）+ 1 = 31 ✅ |
| `files` 列表 | **30 份** | 与目录实有 30 份**逐一对应，无多列、无漏列** ✅ |
| `total_bytes` | **307966** | 30 份逐文件字节合计 = 307966，与 `files` 求和一致 ✅ |
| `scan_scope` | **21 份实测产物**（数据类，含 11-09/11-10 连驱 `g11_drive_*.json` / `g11_bucket_verify_*.json`） | 与 README 正文「31 份 = 21 + 8 + 1 + 1」口径对齐 ✅ |
| 逐文件 bytes | 差异 **0** | 以磁盘实测逐文件比对 `manifest.json`，差异列表为空 ✅ |

- 新增证据 2 份：`g11_drive_20261110.json`、`g11_bucket_verify_20261110.json`；`README-evidence.md` 索引与份数同步（29 → 31 份）。
- 该订正提交由 `g11_reconcile_and_commit.py` 自动完成（**仅提交 manifest.json 一个文件**，未夹带他人文件），提交 `df5fbc1`，已推 origin/main。

## 5. 纪律与副作用（如实披露，未隐瞒）

| 项 | 状态 |
|---|---|
| 采集器 PID 48048 | 起 **2026-11-05 20:31:56**，**未重启**（16:04 实测复核） |
| Web PID 54884 | 起 **2026-11-05 20:29:12**，**未重启**（16:04 实测复核） |
| 业务代码 | **零改动**（`scripts/`、`monitor/` 未动）；`scripts/validate.sh unit` **exit 0**（红线扫描 pass、unit 测试 pass） |
| 他人文件 | 未改；未碰 `STATUS.md` / `DECISIONS.md`（仅 muse 可改） |
| `mteam` 口令 | **本轮未轮换**（连驱不触碰平台认证路径）；累计轮换次数仍为 **五次**（均为此前复跑轮次所致） |
| 凭据 | 只经环境变量传入 workload 子进程，**不落盘 / 不入库 / 不入证据 / 不入日志** |
| 目标库副作用 | 无表结构/数据/索引变更（`idx_user_created` 仍缺失，连驱脚本不碰索引） |
| G-10 开关 | 维持关闭 |
| 正式机 17.192 | 未动 |
| days=3 复判 | **未提前执行**（须待 11-12 09:50 `G11Rejudge-D1`） |

## 6. 缺口与后续（本单未尽事项）

1. **11-11 15:05 第三轮连驱** —— 判定窗内凑满 3 个 15 点运行日的最后一块；装置 `MMMan-DBAIOps-G11DailyDrive`（下次 11-11 15:05:00）与核验 `MMMan-DBAIOps-G11BucketVerify`（16:05）均 **Ready**。
2. **11-12 09:50 `G11Rejudge-D1`** —— days=3 判定（**须在 11-12 15:00 前**），同一脚本同一接口同一命令；结果回填母报告 **v1.3** 并定档。
3. 11-12 20:35 `G11Rejudge-W18`（cid=18 baseline=week）；11-13 15:35 `G11Rejudge-W19`（cid=19 baseline=week）；11-14 16:00 `G11Rejudge-Mature`（周基线成熟轮）—— 均 Ready。
4. 驱动批次（G-D1：装 cp311 `dmPython` + 重启）仍等 PM 于 **11-13 15:27 后**安排（重启会重置采集器 DeltaTracker、打断连续性）。
5. days=7 两路不可达（只作诊断读数），沿用 §9 结论。
6. **缺陷单仍为空** —— 本轮未产生新缺陷。

## 7. 版本记录

| 版本 | 日期 | 修订人 | 修订说明 |
|---|---|---|---|
| v1.0 | 2026-11-10 | MMMan | G-11 人工连驱**当日核验与上板分册**（2026-11-10）：驱动 rc=0 双路、15 点桶各 21617、采集间隙 0、15 点运行日累计 3（判定窗内 2）、manifest 四项口径订正后自洽（31 份 / 307966 B / 差异 0）、回执 §12 新增。**结论档位：有条件通过**（母报告 v1.2 不变，v1.3 留待 11-12 09:50 days=3 实测） |
| v1.1 | 2026-11-10 | MMMan | 补 §8 落库与 CI 核验：manifest 订正提交 `df5fbc1`、交付提交 `5deebe1`、提交纪律复核、本地 unit exit 0、远端 CI run 42781046109 七 job 全绿、并行 README 订正 `51786e9` 已复核自洽。**结论档位不变（有条件通过）** |
## 8. 落库与 CI 核验

| 项 | 结果 |
|---|---|
| manifest 订正提交 | `df5fbc1` —— 由 `g11_reconcile_and_commit.py` 自动生成（**仅含 manifest.json 一个文件**，未夹带他人文件），已推 origin/main |
| 本轮交付提交 | `5deebe1` —— 回执 §12 + 本报告 + 2 份新证据 + README-evidence.md 索引（**5 个文件，266 insertions / 2 deletions**） |
| 提交纪律复核 | `git diff --cached --stat` 提交前逐行核对：暂存区**仅我件 5 个**，未夹带他人文件；未用 `git add .`、未 force；未改 `STATUS.md` / `DECISIONS.md`；中文提交信息写成 UTF-8 文件后用 `git commit --only -F`（未用 `-m`） |
| 本地 / 远端一致 | `local == origin/main == 5deebe1`，ahead/behind = 0/0（另有一条并行 MMMan 进程的 README 口径订正提交 `51786e9` 叠在其上，内容为把 README 首段残留的「19 份数据类文件」改为「21 份」，**字节长度不变**，manifest 无需重算，已复核仍自洽） |
| 本地验证 | `scripts/validate.sh unit` **exit 0**（红线扫描 pass / unit 测试 pass），时点 HEAD = 24c62c2 |
| 远端 CI | 推送后 run **42781046109**（main，push 触发）**7 个 job 全绿**：安全扫描 ✓ / 静态检查 ✓ / 前端构建与测试 ✓ / 单元测试（无外部依赖）✓ / 方言测试（MySQL/PG）✓ / 方言测试（Oracle）✓ / 集成测试（PostgreSQL）✓（日志中的 `X db unreachable` 是容器拉起前的探测噪声，非失败项） |
| 未触碰项 | 未重启采集器 PID 48048 与 Web PID 54884；未轮换 `mteam` 口令；未改业务代码；未执行 days=3 复判；未碰 `STATUS.md` / `DECISIONS.md` |

