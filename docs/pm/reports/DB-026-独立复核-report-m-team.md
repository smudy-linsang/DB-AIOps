复核意见：有条件通过（须先订正 D-1～D-8 共 8 项；其中 D-1、D-4 为不据实项，D-2、D-3 为影响判据的口径失真项，D-5～D-8 为实现落点／动作面／时区／授权口径订正项）

# DB-026 独立复核报告（只读复核，M-team / MMMan 复核侧）

- 单号：DB-026（连接池治本——独立实例或调高 max_connections）
- 被复核物：`docs/pm/reports/DB-026-report-m-team.md`（v1.0 评估版，141 行，commit `ed02832`）、`docs/pm/reports/DB-026-receipt-m-team.md`（34 行）、`scripts/pg_pool_audit.py`（142 行）、`docs/pm/reports/evidence/DB-026-pool-audit-20261113.json`
- 被复核物后续状态（本次订正读时点 2026-11-13 20:3x）：交付方 **v1.6** 已入库（基线更新 commit `a44a5d3`，15:30:26；措辞一致性订正 commit `6974c9a`，15:30:58；物料索引增补 commit `b56f168`，15:37:52；G-3 属主残余定责＋收尾巡检读数＋备件入库状态并列 commit `a7dd293`，20:14:46；D2 载体现态＋自身取证说法订正 commit `a8f7b2a`，20:25:02；`git merge-base --is-ancestor a8f7b2a HEAD`＝0），据 STATUS v1.47（commit `53a2900`，14:38:02）载：G-5 获准（**仅限 b1 的 S3**）、S-2 采纳（平台侧同设 CONNECTION LIMIT、与 b1 同批）、S-1 拆分（b2 暂缓且其 compose 改动不在获准范围）、用户 12:31 确认「治本照做、D2 之后实施」；★v1.3 已把我方 r27 函提出的「平台侧须同批『切角色＋设限额＋重启使其生效』三件事齐做」写入 §六 b1-5 实施步骤（证据：`dbmonitor/settings.py` L237／L265 默认身份为 superuser）；★**v1.4 增补 b1 实施物料索引**（commit `b56f168`，15:37:52：报告 ＋4 行 ＋ 三份 D2 后执行件入库——`scripts/db026_b1.ps1` 96 行／`db026_b1_apply.sql` 100 行／`db026_b1_rollback.sql` 32 行；其版本行载「四道闸门实测拒绝、测试期未写库、角色数仍为 0」）⇒ ★**v1.4 的报告结论／档位／实施步骤内容均不变**；★**v1.5 增补三项**（commit `a7dd293`，20:14:46：① **G-3 属主残余定责**——TimescaleDB create_hypertable／保留策略／压缩策略需对象属主权限，首次建超表由 postgres 执行一次或按需转属主，责任方＝执行侧、D2 后实施窗口自行定人并回报，**不占 muse 裁决位**，已落 §六 b1-6；② 今日收尾巡检读数 20:13:39 → client 11／97＝11.34%（裸口径 20.0%）、alert=false、**D2 前两角色实测仍为 0**＝未动库；③ 备件入库状态据实并列——r32「三份备件未被跟踪」为 15:3x 快照、现态已跟踪已推送、scan_secrets exit 0 且备件无明文口令）⇒ ★**v1.5 的报告结论／档位／实施步骤内容亦不变**；★**v1.6 增补 D2 相关载体现态**（commit `a8f7b2a`，20:25:02：据交付方 20:2x 独立实测记 §21／D2 两支插件排程已重建、桶口径已修正为「待产 1 块＝11-14」、旧支已不在清单 ⇒ D2 判定输入现为 11-12 在／11-13 已在手／11-14 待产；并订正其自身 20:2x 回函「sch_* 不可见／我方未独立核验」的取证说法——本 Bot 名下 sch_* 可经 list_schedules 带 caller_bot_id 读到全文，cron_read 可见范围仅 self-* 类；其 11-14 巡检提醒已移除写死的排程时刻）⇒ ★**v1.6 的报告结论／档位／实施步骤内容亦不变** ⇒ ★**本 v1.5 订正仅涉本复核件自身的被复核物基线同步（v1.5→v1.6）；D-1～D-8 判项、S-1～S-6 建议与「有条件通过」的档位意见均不变。**
- 复核方：MMMan 复核侧（会话 `mvs_1a094fd33c62497595e277740566024b`），受落笔方 v5 函第 3 条请求承接
- 复核读时点：**2026-11-13 12:14–12:19（本地时 +08:00）**
- 基线 HEAD：`c47d20b`（main，`HEAD...origin/main` = 0/0）
- 复核性质：**全程只读** —— 未动库、未改任何配置（含不改 `max_connections`）、未启停任何进程与容器、未新增证据文件、未改被复核件与 DB-005R 回执／母报告／STATUS／DECISIONS

## 〇、结论（三档）

**一档（档位意见）：有条件通过。** 报告的推荐方向（(b) 为主路径、(a′) 为二阶段隔离）与根因判定（`rolconnlimit` 对 superuser 无效；两份副本共用同一容器同一组库）经独立实测**成立**；但「实测事实基线」一节存在 2 处不据实、2 处影响判据的口径失真，实施步骤存在 3 处落点／动作面错记与 1 处已被板面后续澄清取代的授权口径 ⇒ **上述 8 项订正完成后本档位成立**；未订正则本报告不构成对该评估版的通过。

**二档（须订正项）：D-1 ～ D-8**（见第二节，逐条给出复算口径与实测值）。

**三档（建议增补项）：S-1 ～ S-6**（见第三节，我方建议，不代裁）。

**我方不裁**：路径最终选择、(b)/(a′) 取舍、S-1 拆分取舍、G-5（能否改动 `C:\DB_Monitor`）口径、DB-026 评档 —— 均不代裁；口径疑问经 MMMan 转 muse。

## 一、据实核验通过项（我方独立实测，逐位可复现）

| # | 报告陈述（行号） | 我方 12:1x 独立实测 | 判定 |
|---|---|---|---|
| 1 | §2.1 L24：`max_connections=100`、`superuser_reserved_connections=3` | 同值；另测得 `max_worker_processes=35` | 据实 |
| 2 | §2.1 L25：timeseriesdb 1205 MB／db_monitor 560 MB／postgres 9351 kB | 1,266,433,047 B（≈1207.8 MB）／587,799,575 B（≈560.6 MB）／9,575,447 B（≈9.13 MB） | 据实（随写入自然增长） |
| 3 | §2.1 L26（★**库＝timeseriesdb**）：metric_point 2,171,816／sql_stat 269,622／collection_snapshot 136,348／session_sample 0 | ★**库＝timeseriesdb**：2,177,848／271,094／136,441／0；sql_stat max(time)＝2026-11-13 04:15:23 UTC（＝12:15:23 本地）续写中 | 据实（★**库名须随行数引用**：三表在 timeseriesdb；db_monitor 下存在同名近空表，照该库复现得近零行、误导出「数据面停写」——权威方 13:1x 实测 db_monitor 的 collection_snapshot 仅 8 行／max(time)＝2026-09-14、sql_stat 0 行） |
| 4 | §2.1 L27：db_monitor public 表数 105 张 | `information_schema.tables`（表+视图）＝105 ✓；`pg_tables` 口径＝**102** | 数字可复现，口径标签需精确化（见 S-5） |
| 5 | §2.3 L37：DB_Monitor 开发实例存在 | Windows 侧 47884→39072（`python.exe -u manage.py runserver --noreload 0.0.0.0:8000`）在跑 | 实例存在据实；「两侧并列两实例」不据实，见 D-1 附注 |
| 6 | §2.3 L38：两份副本共用同一容器同一组库 | docker inspect labels：compose project＝`db_monitor`、config_files＝`C:\DB_Monitor\docker-compose.dev.yml`、service＝timescaledb、命名卷＝`db_monitor_timeseries_data`；容器名 `db-aiops-timescaledb` | 据实（比报告更精确，落点问题见 D-5） |
| 7 | §2.2 L29-32：ORM 口径（CONN_MAX_AGE 生产 60／非生产 0、application_name 固定 `db_monitor`、connect_timeout=10、POSTGRES_DB 默认 db_monitor、HOST=localhost） | `dbmonitor/settings.py` 逐条实读一致 | 据实 |
| 8 | §2.2 L33：时序池为 ThreadedConnectionPool、maxconn＝TIMESCALEDB_POOL_MAX、库名 timeseriesdb、statement_timeout 默认 15000ms | `monitor/timeseries.py:59-68`：`pgpool.ThreadedConnectionPool(..., maxconn=appconf.get('TIMESCALEDB_POOL_MAX'), options=f'-c statement_timeout={stmt_timeout}')`；`monitor/appconf.py:37` 默认 16、上限 200；`settings.py` TIMESCALEDB_STATEMENT_TIMEOUT_MS 默认 15000 | 据实（上限校验层见 S-5） |
| 9 | §三-2：`rolconnlimit` 对 superuser 无效 | `pg_roles`：postgres rolsuper＝t、rolconnlimit＝-1；dbmonitor_app 尚不存在（未实施） | 据实 |
| 10 | §2.1 L23：容器 healthy、端口 5432:5432 | Ports＝0.0.0.0:5432＋[::]:5432；`RestartPolicy=no`；StartedAt＝2026-11-13T01:37:12Z（＝09:37:12 本地）；Cmd＝[`postgres`]（⇒ 追加 command 不破坏 entrypoint） | 据实（卷名见 D-5） |
| 11 | §五-3：引「任务单明文『调高 max_connections 路径可按本单直接实施』」 | `docs/pm/orders/DB-026-连接池治本-独立实例或调高max_connections.md:13` **确有此明文** | **引用属实** —— 我方复核前「STATUS v1.46 未见」的疑虑撤回（该明文在订单文件，不在 STATUS） |
| 12 | §2.3：`C:\DB_Monitor` 是本项目的另一份工作副本 | `git -C C:\DB_Monitor remote -v`＝`https://github.com/smudy-linsang/DB-AIOps`（同一远端）；HEAD＝`5c13952`（落后本仓主线） | 据实 |
| 13 | §八：D2 前只读、未重启任何进程 | 我方复核全程只读；容器 StartedAt 09:37:12 与 §15／§20 一致，本轮未发生 postmaster 重启 | 据实 |

## 二、须订正项（D-1 ～ D-8）

### D-1（不据实）宿主占用方归因：把「中继/监听 PID」当成「占用方」

- 报告 L39：「指向 5432 的 ESTABLISHED 连接共 7 条，全部归属 PID 40492 ＝ wslrelay.exe（WSL 虚拟网卡转发进程），**即此刻活着持有连接的是 WSL 侧的 DB_Monitor 开发实例**」；回执 L27 同表述。
- 我方 12:1x 全量 `netstat -ano`（:5432 共 53 行）实测：ESTABLISHED 共 **16 行** ＝ **服务端侧 8 行**（全部 `local=[::1]:5432`，属主 PID 40492）＋ **客户端侧 8 行**：
  - PID **43592**（`pythonw.exe manage.py start_monitor`，采集器子进程）× 4（本地端口 51348／55005／58619／65194）
  - PID **47216**（`pythonw.exe manage.py runserver 127.0.0.1:8009`，平台 Web 子进程）× 3（53922／53923／58257）
  - PID **39072**（`python.exe -u manage.py runserver --noreload 0.0.0.0:8000`，DB_Monitor 开发副本）× 1（63829）
- ⇒ **40492 是 `[::1]:5432` 的 LISTENING 属主兼服务端侧 socket 属主（中继/转发进程），不是占用方**；同一 40492 同时承载采集器与平台 Web 的连接 ⇒ 据此推「活着持有连接的是 WSL 侧 DB_Monitor」不成立。**实际占用方为采集器 4 条、平台 Web 3 条、DB_Monitor 开发副本仅 1 条**。
- 附注（进程维度）：报告 L37 把「Windows 侧 47884／39072」与「WSL 侧 9715／9716」并列成两个实例；实测 WSL 侧 `ps -eo pid,ppid,etime,args` 显示 9715/9716 ＝ `bash -c cd /mnt/c/DB_Monitor && … .venv/Scripts/python.exe -u manage.py runserver --noreload 0.0.0.0:8000` → `/init .venv/Scripts/python.exe …` ⇒ **同一套 Windows venv 进程在 WSL 命名空间的视图**，不是第二个 runserver 实例 ⇒ 「两实例并存」属口径夸大。
- 影响面：报告与回执的「占用方＝DB_Monitor 开发实例」这一结论性表述据实性不成立。★注：DB-005R §20 L270 所记「100 条客户端侧连接属宿主 PID 21728（C:\DB_Monitor\…runserver）」系**客户端侧**归因，与本次实测口径一致，**不受本项影响**。

### D-2（口径失真，影响订单任务 3 达成）巡检脚本取不到「占用方 PID」

- `scripts/pg_pool_audit.py` 的 `collect_host_holders()` 只收「**本地端口 ＝ 5432** 且 ESTABLISHED」的行 ⇒ 得到的必然是**服务端侧/中继侧** PID，无法得到客户端属主 ⇒ 订单任务 3「每日巡检连接池占用（ESTABLISHED 数、**占用方 PID**）」在当前实现下**未达成**。
- 首日证据 `DB-026-pool-audit-20261113.json` 的 `"host_tcp_established_by_pid": {"40492": 7}` 须订正为：8 条 ESTABLISHED 中服务端侧均属 40492；客户端侧归属 43592×4／47216×3／39072×1（12:1x 读时点）。
- 建议修法（**实施动作留给单方，我方不代改脚本**）：同时统计客户端侧行（本地端口≠5432 且远端＝5432 且 ESTABLISHED）按 PID 归组，再与 pg 侧 `application_name`／`backend_start` 交叉，方能定位占用方。

### D-3（口径失真）`usage_pct` 分子把后台进程计入连接占用

- 脚本 `total = select count(*) from pg_stat_activity`（**不限 backend_type**）⇒ 9 个非客户端后台进程（TimescaleDB Background Worker Scheduler×3、TimescaleDB Background Worker Launcher×1、autovacuum launcher、logical replication launcher、background writer、checkpointer、walwriter）被计入占用；而 PG 语义下 `max_connections` 只约束**客户端槽位**（后台进程受 `max_worker_processes=35` 约束、不占槽）。
- 实测对照：

  | 时点 | total（含后台） | client backend | 后台进程 | 脚本 usage_pct | 真实客户端槽位占用 |
  |---|---|---|---|---|---|
  | 11-13 11:58:08（首日证据） | 17 | 8 | 9 | 17.0% | **8.25%**（8/97） |
  | 11-13 12:18:39（我方实跑脚本） | 20 | 11 | 9 | 20.0% | **11.34%**（11/97） |

- ⇒ 报告 L44「total_backends 17/100 ＝ 17.0%」与 §七-1 判据「占用率 < 80%」须改用 `backend_type='client backend'` 口径，并显式扣 `superuser_reserved_connections=3`（非 superuser 可用槽位＝97）；另 JSON 中 `total_backends` 与 `client_backends` 并列，易被引用为「占用数」。
- ⇒ 上表末列「真实客户端槽位占用」的分母＝`max_connections - superuser_reserved_connections`＝**97**（与本条「须显式扣 3」同一口径；v1.0 原稿该两格按 100 算、与本条自相矛盾，本次订正）：8/97＝8.2474% ⇒ **8.25%**、11/97＝11.3402% ⇒ **11.34%**（后者与交付方证据 JSON v2 `corrected_reading.client_slot_usage_pct`＝11.34 逐位一致）；交付方 v1 原始读数段记 `true_client_usage_pct`＝8.26%、其 v1.1 报告 L19 同记 8.26%（与 8/97＝8.2474% 差 0.0126pp，属舍入／录入级差异，不影响本项任何判项结论）；★该类读数随连接建立／释放浮动，引用须带读时点与口径，不可作定值基准。

### D-4（不据实）库清单含已不存在的 `test_db_monitor`

- 报告 L25／回执 L25：「库清单与体量：timeseriesdb 1205 MB、db_monitor 560 MB、**test_db_monitor 16 MB**、postgres 9351 kB」。
- 实测 `pg_database` 仅 5 库（`db_monitor`、`postgres`、`template0`、`template1`、`timeseriesdb`）；`select count(*) from pg_database where datname='test_db_monitor'` ＝ **0**；仓库根目录 `V3.0_DIALECT_MATRIX.md:87` 载有 `DROP DATABASE IF EXISTS test_db_monitor;` ⇒ 该库系历史遗留且已删除，报告把文档／配置中出现过的库名写进了「实测库清单」。
- 影响面小（与治本路径无关），但「实测事实基线」一节的库清单不据实。

### D-5（实现落点错记，影响路径 (b) 的改动落点与 G-5 范围）

- 报告 L66（(b) 改动点 1）：「**compose 给 timescaledb 服务加 command**（postgres -c max_connections=200）」。
- 实测该 compose 文件为 **`C:\DB_Monitor\docker-compose.dev.yml`**（docker inspect labels：compose project＝`db_monitor`、`com.docker.compose.project.config_files=C:\DB_Monitor\docker-compose.dev.yml`、service＝timescaledb、命名卷＝`db_monitor_timeseries_data`）⇒ **该步骤落在本仓之外的 DB_Monitor 用户工程内**，不在 DB-AIOps 本仓。
- ⇒ 报告 §九 G-5 只列了「DB_Monitor 开发副本的连接指向／角色切换／重启是否获准」，**未含编排文件（compose）改动** ⇒ G-5 范围描述偏窄；路径 (b) 的 S1 实落在 G-5 之内。
- 附注：报告 L23「卷 timeseries_data」为 compose 内逻辑卷名，实际 docker 卷名为 `db_monitor_timeseries_data`（项目名前缀）⇒ 建议写全。实测容器 `Cmd=[`postgres`]`（镜像默认）⇒ 追加 `command` 本身不破坏 entrypoint，报告此点成立。

### D-6（动作面低估）S1 是「重建（recreate）」而非「原地重启」

- 报告 L95（S1 影响面）：「容器重启，全部连接断 5–15 秒」；回退＝「还原 compose 并再次 up」。
- 实测容器 `RestartPolicy=no`；`docker compose up -d timescaledb` 在 `command` 变更时 compose config-hash 变化 ⇒ **触发容器重建**（现存容器标签含 `com.docker.compose.replace`），并非原地重启 ⇒ 动作面（重建、healthcheck 重建、compose 依赖重排）重于报告表述；且与 DB-005R §15／§20 既定恢复口径（**原地 `docker start`、不重建**）不同类 ⇒ 须单独立项并单独登记（重建 ≠ 原地重启）。
- 数据面不动的判断成立（命名卷持久、PGDATA 非空时 entrypoint 跳过 init）。

### D-7（时区未标注）最老连接时点为 UTC，易被误读

- 报告 L44／首日证据 `oldest_backend_start = "2026-11-13 02:27:12"`：该值由 pg 侧 `to_char(..., 'YYYY-MM-DD HH24:MI:SS')` 渲染，会话时区为 **+00（UTC）** ⇒ 实为**本地时 10:27:12**，恰为采集器 10:27:10 拉起（DB-005R §20）后建立的首条连接 ⇒ 按板面惯例（本地时渲染）易被误读为凌晨 02:27（早于容器 09:37:12 启动）⇒ 须标注时区。
- 附注：报告未指明该最老连接属**采集器**而非 DB_Monitor（与 D-1 同源）。

### D-8（授权口径已被板面后续澄清取代）§六 对实施授权的描述比订单与板面最新口径更严

- 报告 L101：「S1 的 postmaster 重启与 S3 的开发副本重启均属『需用户重新授权』动作，本单不自行执行；实施前须先经 muse 确认用户口径已变更」。
- 实测订单 `docs/pm/orders/DB-026-…md:14`（任务 2）明文：「**实施推荐路径**，但切断/重启类操作一律在 11-15 09:50 D2 复判之后」；第 13 行明文「调高 `max_connections` 路径可按本单直接实施」⇒ **订单已授权 D2 后实施**。
- 且 DB-005R §20 **L285（commit `c47d20b`，11-13 12:11:09）** 已确立合读口径：「路径归属取 STATUS v1.46（用户已拍板选治本）、当日动作取 L280＋问卷记录」⇒ 按此口径，**D2 后实施 (b) 无需再向用户索权**；仅 **S3（动本仓外用户工程副本）与 S1（动其 compose 文件，见 D-5）受 G-5 待 muse 裁决**。
- ⇒ 报告 §六 该段应订正。性质＝**措辞／授权状态层**，非事实错误；成因＝本报告成文 12:02（`ed02832`）早于 L285 落板 12:11，属「后续板面澄清未纳入」⇒ 据实并列，建议订正原文或加注。

## 三、建议增补项（S-1 ～ S-6，我方建议，不代裁）

### S-1（★可显著降低 D2 窗口风险）建议把 (b) 拆成 b1／b2：抬天花板并非必需

- 本单真正的治本点是「DB_Monitor 侧换非 superuser 角色 ＋ CONNECTION LIMIT 30」；而 **调高 `max_connections`＝200 并非必需**：`max_connections` 是**实例级**上限，DB_Monitor 侧封顶 30 后，平台侧时序池上限 16（`appconf.py:37`）＋ ORM 连接远小于 `100-3=97` ⇒ 现有 100 槽已足够。
- ⇒ 建议拆为：**b1（必做、无需 postmaster 重启、不打断 D2 判定窗）** ＝ 建角色＋授权＋DB_Monitor 切角色（受 G-5 待裁）；**b2（可选／二期）** ＝ 抬 `max_connections`（若采纳则涉 S1 重建，须排 D2 后并避开 15:00–15:10 桶时段）。
- 若采纳，**S1（唯一需要重启 postmaster、唯一触及「切断」的动作）可从 D2 后窗口中整体移除**。

### S-2（覆盖面缺口）治本路径未覆盖平台自身的同类根因

- 报告 §三-3 已认 ORM 侧在非生产模式下 `CONN_MAX_AGE=0`、runserver 短命线程的 DatabaseWrapper 引用环滞留连接至 Gen-2 GC；而**当前运行态正是非生产**（`settings.py:233-250`）⇒ 平台 Web 高并发下同样可堆积占池。
- 现状测（12:1x）：平台 Web 子进程 47216 持 3 条、采集器 43592 持 4 条 ⇒ 平台侧本就是当前主要连接方之一。
- ⇒ 建议 (b) 同时对平台侧角色设 CONNECTION LIMIT，或把「平台侧 ORM 连接滞留」列为 D2 后独立处置项；否则治本只是把风险从一侧挪到另一侧。

### S-3（角色授权清单须覆盖 dev 副本的迁移能力）

- `dbmonitor_app` 的授权清单须覆盖 DB_Monitor 开发副本的 `migrate` 需求（schema public 的 CREATE、必要时 CREATEDB／建扩展权限、TimescaleDB 扩展函数与 `_timescaledb_internal` 授权）⇒ 否则只是把「连接池占满」换成「dev 副本跑不起来」。报告 G-3 只认「授权完整性未实测」，建议把「dev 副本 migrate／建表可用」列为受控验证项。

### S-4（受控验证须前置门禁）

- §七-2「在 DB_Monitor 开发副本侧制造并发（短时多次访问）」本身即制造连接压力 ⇒ 须**先**确认角色已切换成功且 `rolconnlimit=30` 生效（否则验证即事故），并把该验证排在 D2 判定窗之外、避开 15:00–15:10 桶时段。

### S-5（口径精确化）`TIMESCALEDB_POOL_MAX` 上限 200 的校验层

- 池的 maxconn 实际取自 `monitor/timeseries.py:61` 的 `appconf.get('TIMESCALEDB_POOL_MAX')`，而 200 上限的校验定义在 `monitor/appconf.py:37` 的 SPECS；`dbmonitor/settings.py` 另有一份同名环境变量直读（无上限校验）⇒ 报告 L33「上限 200」须注明校验所在层。另：`settings.py` 与 `appconf.py` 各有一份默认值 16，与 `appconf.py` 自载铁律 2「默认值只在此处定义一次」相悖 —— 属仓库既有口径问题，非本单引入，仅作披露。

### S-6（巡检节奏与读数字段）

- 建议 11-14／11-15 的巡检读数同时落：`client_backends`、`(max_connections - superuser_reserved)` 口径余量、客户端侧 PID 归组、`application_name` 分布、`backend_start`（**标注时区**），并带读时点 —— 以免复现 D-1／D-2／D-3 类口径问题。

## 四、复核方法与可复现命令（读时点 2026-11-13 12:14–12:19，本地时；全部只读）

1. `docker exec db-aiops-timescaledb psql -U postgres -d timeseriesdb -A -F '|' -c "select backend_type, count(*) from pg_stat_activity group by 1;"`（backend_type 分布 → D-3）
2. `… -c "select pid, coalesce(nullif(application_name,''),'(none)'), client_addr, state, backend_type, backend_start from pg_stat_activity order by backend_type, backend_start;"`（→ D-3／D-7）
3. `… -c "select datname, numbackends, pg_database_size(datname) from pg_stat_database order by 3 desc;"` 与 `select datname from pg_database order by 1;`（→ 表 2／D-4）
4. `docker exec db-aiops-timescaledb psql -U postgres -d db_monitor -A -t -c "select count(*) from pg_tables where schemaname='public';"` 与 information_schema 对照（→ 表 4）
5. `netstat -ano | Select-String ':5432'` 后按「本地端口＝5432／≠5432」分侧归组（→ D-1／D-2；脚本 `collect_host_holders` 只取前者）
6. `Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'runserver|start_monitor' }`（→ D-1 进程归组）
7. `wsl.exe -e ps -eo pid,ppid,etime,args`（→ D-1 附注，WSL 侧为同一 Windows venv 进程的另一命名空间视图）
8. `docker inspect db-aiops-timescaledb --format "{{json .Config.Labels}}"` / `{{json .Mounts}}` / `{{json .Config.Cmd}}`（→ D-5／D-6）
9. `C:\DB-AIOps\.venv\Scripts\python.exe C:\DB-AIOps\scripts\pg_pool_audit.py`（只读实跑对照，12:18:39 → total 20／client 11／usage_pct 20.0／host {"40492": 10}）
10. `git -C C:\DB_Monitor remote -v`、`git -C C:\DB_Monitor rev-parse HEAD`（→ 表 12）
11. 表 3 行的三表行数与 max(time) 取自 **timeseriesdb**（★库名不可省）：`docker exec db-aiops-timescaledb psql -U postgres -d timeseriesdb -A -t -c "select count(*) from metric_point;"`，同法取 `sql_stat`／`collection_snapshot`／`session_sample` 的 count(*) 与 `max(time)`（→ 表 3）；★**切勿在 db_monitor 上复现这三张表**（同名近空表，会得近零行并误判「数据面停写」——权威方 13:1x 实测）

## 五、我方边界声明

- 本复核为**只读独立复核**：未动库、未改任何配置（含不改 `max_connections`）、未启停任何进程／容器、未新增证据文件 ⇒ **DB-005R 证据仍 66 份（file_count 66／total_bytes 535574／scan_scope 45 不变）**；DB-005R 回执与母报告未动（母报告维持 v1.4／有条件通过）；STATUS.md／DECISIONS.md 未动（muse 维护面）。
- 本复核**不构成对 DB-026 评估版的评档**，仅出具复核意见；评档由 muse／PM 侧依板面流程作出。
- 路径最终选择（(b) vs (a′)）、S-1 拆分取舍、S-2 是否补齐，**我方不代裁**，仅提建议。
- G-5（能否改动 `C:\DB_Monitor`）口径**不自行认定**，经 MMMan 转 muse；★现态＝已由 STATUS v1.47 裁出（**G-5 获准、仅限 b1 的 S3**；其编排文件改动属 b2、不在获准范围），我方据实并列、不代裁。
- 落笔归属自注：本件由 **MMMan 复核侧会话 `mvs_1a094fd33c62497595e277740566024b`** 于 2026-11-13 12:1x 落笔；与 DB-026 交付方会话（`mvs_1ec15a51d…`）**不是同一会话**，本复核不修改其交付物。

## 版本记录

| 版本 | 日期 | 修订人 | 修订说明 |
|---|---|---|---|
| v1.0 | 2026-11-13 | MMMan（复核侧） | DB-026 评估版独立只读复核：13 项据实核验通过、8 项须订正（D-1～D-8）、6 项建议增补（S-1～S-6）；复核意见＝有条件通过（须先订正） |
| v1.1 | 2026-11-13 | MMMan（复核侧） | 订正本复核件自身 3 处错误：① D-4 所引方言矩阵路径 `docs/V3.0_DIALECT_MATRIX.md:87` 不存在（`git cat-file -e HEAD:docs/V3.0_DIALECT_MATRIX.md`＝exit 128），实为仓库根目录 `V3.0_DIALECT_MATRIX.md:87`（＝exit 0；该文件第 87 行原文＝`DROP DATABASE IF EXISTS test_db_monitor;`）——事实成立、仅路径标注错；② D-3 表末列两格槽位占用率按分母 100 误算（8.0%／11.0%），改按本条自订口径（扣 `superuser_reserved_connections`＝3、可用槽位 97）＝**8.25%**（8/97）与 **11.34%**（11/97，与交付方证据 JSON v2 逐位一致；交付方记 8.26%，差 0.0126pp 属舍入／录入级，不影响判项结论）；③ 增补被复核物后续状态与分母口径说明。判项结论与档位意见一字未改 |
| v1.2 | 2026-11-13 | MMMan（复核侧） | 订正本复核件自身 1 处口径缺漏并同步被复核物基线：① §一 表 3 行补「**库＝timeseriesdb**」——三表（metric_point／sql_stat／collection_snapshot／session_sample）在 timeseriesdb，db_monitor 下存在同名近空表，照该库复现得近零行、误导出「数据面停写」（依据：我方 12:1x 独立实测 136,441 行／sql_stat max(time) 04:15:23 UTC ＋ 权威方 13:1x 实测 db_monitor 的 collection_snapshot 仅 8 行、sql_stat 0 行）；② §四 补一条带 `-d timeseriesdb` 的可复现命令；③ 被复核物后续状态更新至 **v1.3**（`a44a5d3` 15:30:26／`6974c9a` 15:30:58）——G-5 获准（限 b1 的 S3）、S-2 采纳、b2 暂缓，且我方 r27 的「平台侧三件事齐做」前提已被写入 §六 b1-5；④ §五 G-5 句据实补注现态（已由 STATUS v1.47 裁出）。★D-1～D-8 判项、S-1～S-6 建议与「有条件通过」的档位意见一字未改 |
| v1.3 | 2026-11-13 | MMMan（复核侧） | 同步被复核物基线至 **v1.4**（commit `b56f168`，15:37:52）：v1.4 增补 b1 实施物料索引——三份 D2 后执行件入库（`scripts/db026_b1.ps1` 96 行／`db026_b1_apply.sql` 100 行／`db026_b1_rollback.sql` 32 行），其版本行载「四道闸门实测拒绝、测试期未写库、角色数仍为 0」；★**报告结论／档位／实施步骤内容均不变**（有条件通过），实施仍排在 11-15 09:50 D2 之后。★D-1～D-8 判项、S-1～S-6 建议与「有条件通过」的档位意见一字未改。（本行随 v1.2 的 1 处口径缺漏订正一并入库。） |
| v1.4 | 2026-11-13 | MMMan（复核侧） | 同步被复核物基线至 **v1.5**（commit `a7dd293`，20:14:46）：v1.5 据复核侧 r30 与权威方 r32 增补三项——① G-3 属主残余定责（首次建超表需属主权限，责任方＝执行侧、D2 后定人并回报，不占 muse 裁决位，落 §六 b1-6）；② 今日收尾巡检读数 20:13:39（client 11／97＝11.34%、alert=false、D2 前两角色实测仍为 0）；③ 备件入库状态据实并列（r32 第四节的「未跟踪」为 15:3x 快照，已被 b56f168 取代，现态已跟踪已推送；scan_secrets exit 0、备件无明文口令）。★**报告结论／档位／实施步骤内容均不变**（有条件通过），实施仍排在 11-15 09:50 D2 之后。★D-1～D-8 判项、S-1～S-6 建议与「有条件通过」的档位意见一字未改 |
| v1.5 | 2026-11-13 | MMMan（复核侧） | 同步被复核物基线至 **v1.6**（commit `a8f7b2a`，20:25:02）：v1.6 增补 D2 相关载体现态——据交付方 20:2x 独立实测记 §21／D2 两支插件排程已重建、桶口径块已修正为「待产 1 块＝11-14」、旧支已不在清单 ⇒ D2 判定输入现为 11-12 在／11-13 已在手／11-14 待产；并据实订正其自身 20:2x 回函「sch_* 不可见／未独立核验」的取证说法（本 Bot 名下 sch_* 可经 list_schedules 带 caller_bot_id 读全文，cron_read 可见范围仅 self-* 类），其 11-14 巡检提醒已移除写死的排程时刻。★**报告结论／档位／实施步骤内容均不变**（有条件通过），实施仍排在 11-15 09:50 D2 之后。★D-1～D-8 判项、S-1～S-6 建议与「有条件通过」的档位意见一字未改 |
