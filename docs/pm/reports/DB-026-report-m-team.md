有条件通过

# DB-026 交付报告：连接池治本 —— 独立实例或调高 max_connections（v1.1 订正版）

- 版本：**v1.3**（v1.1 据复核意见订正 D-1～D-8；v1.2 两项据实并列；**v1.3 基线更新：据 STATUS v1.47（commit 53a2900，14:38:02）—— G-5 获准、S-2 采纳同批实施、b2 暂缓，并补齐平台侧生效前提**）
- 单号：DB-026｜执行：MMMan（落笔会话 mvs_1ec15a51d…）｜复核方：MMMan 复核侧会话 mvs_1a094fd33…（独立复核件 commit 2d6d6c0，157 行）
- 成文：v1.0 于 2026-11-13 12:02（commit ed02832）；v1.1 于 2026-11-13 12:4x（本次订正）
- 基线 HEAD：接单时 6eec91f；v1.0 提交时 ed02832；本版提交时 main 已由他方并发前推（本单只提交自身文件，未动他人内容）
- 结论档位口径：本版结论「有条件通过」＝评估与准备完成、推荐路径明确且已按复核意见收敛；推荐路径的实施按订单排在 11-15 09:50 D2 复判之后，尚未实施，故不得记「通过」。★v1.3 补：STATUS v1.47 载「评估版须按 D-1～D-8 订正后方可转通过」—— 该条件我方 v1.2 已完成，并经复核侧 13:2x 终态回签逐位复算成立；**最终评档由 muse 依 STATUS 流程落定，我方不自评转通过**；本报告整体仍含尚未实施的实施面，故自评档位维持「有条件通过」。

## ★v1.3 基线更新（据 STATUS v1.47 / commit 53a2900，14:38:02，读时点 15:3x）

1) **PM 裁决已到**：G-5 **获准**（仅限 b1 的 S3＝DB_Monitor 开发副本的连接指向/角色切换/重启；**compose 文件改动属 b2、不在本次获准范围**）；采纳 S-1（(b) 拆 b1/b2：b1＝建角色＋授权＋切角色、无需 postmaster 重启、D2 后实施；**b2＝调高 max_connections 至 200、暂缓**）、S-2（**平台侧同样设 CONNECTION LIMIT，与 b1 同批**；ORM 引用环滞留列为已知问题）、S-3～S-6。
2) **用户口径**：用户 12:31 确认「治本照做，D2 之后实施」；§20 的「④暂不处置」与治本**并存不互斥**（④管 D2 之前只观察、治本管 D2 之后实施）。
3) ★**我方 13:2x 函中「G-5／G-6 与最终评档均待 muse／PM 裁决」一句已过期**（成文于 13:2x、当时属实）⇒ 据实订正、不回改原函；实施面「不启动、不代裁」的处置在今日与 D2 前仍然成立（今日不动库、不实施）。
4) ★**实施面一处生效前提（PM 裁决的执行前置条件显式化，非异议）**：「平台侧同样设 CONNECTION LIMIT、与 b1 同批」的正确执行面含义是**同批内「切角色」＋「设限额」＋「重启使其生效」三件事齐做** —— 因为**平台侧两条连接面默认身份均为 superuser**：dbmonitor/settings.py L237（'USER': os.environ.get('POSTGRES_USER','postgres')）与 L265（TIMESCALEDB_USER 同默认值）（我方 15:3x 只读核本仓配置，未连任何实例、未启停任何进程）⇒ **平台侧只设限额而不切身份，该限额对平台自身即空话**（与 b1 侧 b1-3 必须先切 DB_Monitor 副本角色同型前提）；影响边界＝仅 11-15 09:50 D2 之后的实施窗口，今日无影响。

## 〇、v1.1／v1.2 订正摘要（历史留痕）

本版对 v1.0 的全部更正，均由我方**独立复算**（不照抄复核结论）：复算命令与读时点见各行「依据」列。结论：复核方 8 项指控**逐项成立**，我方原表述确有 2 处不据实、2 处口径失真、4 处落点/动作面/时区/授权口径错记，现全部订正。

| 编号 | v1.0 表述 | v1.1 订正后 | 我方独立复算依据（读时点 2026-11-13 12:3x–12:4x） |
|---|---|---|---|
| D-1 不据实 | 「指向 5432 的 ESTABLISHED 共 7 条全部归属 PID 40492＝wslrelay.exe，即此刻活着持有连接的是 WSL 侧 DB_Monitor 开发实例」 | **40492 是服务端侧 socket 属主兼 [::1]:5432 的 LISTENING 属主，不是占用方**。客户端侧占用方实测＝采集器链 43592（start_monitor）×7、平台 Web 链 47216（runserver 127.0.0.1:8009）×2、DB_Monitor 开发副本 39072（runserver 0.0.0.0:8000）×1 | netstat -ano 按「远端＝5432＝客户端侧／本地＝5432＝服务端侧」分侧归组；LISTENING 侧：0.0.0.0:5432 与 [::]:5432 属主 36736＝com.docker.backend.exe，[::1]:5432 属主 40492＝wslrelay.exe ⇒ 40492 属中继侧。结论：**本单当前的主要连接持有方是平台自己（采集器+Web 共 9 条），DB_Monitor 侧仅 1 条** |
| D-2 口径失真 | 巡检脚本 collect_host_holders() 只筛「本地端口＝5432」的行 | 脚本改为 v2：**分侧归组**（client_side／server_side／listening_owners）＋客户端侧 PID 映射进程名与脱敏命令行 ⇒ 订单任务 3「巡检 ESTABLISHED 数、占用方 PID」**已达成** | scripts/pg_pool_audit.py v2 实跑输出：client_side_by_pid {43592:7, 47216:2, 39072:1}、server_side_by_pid {40492:10}、listening_owners_by_pid {36736:2, 40492:1}，并附三个进程的命令行 |
| D-3 口径失真 | 「total_backends 17/100 ＝ 17.0%」，判据用「占用率 < 80%」 | 占用率改 **client 口径**：分子＝backend_type='client backend'，分母＝max_connections − superuser_reserved_connections＝**97**；含后台进程的裸计数单列（后台进程不占槽，受 max_worker_processes 约束） | v2 实跑（读时点 12:38:20）：client_backends 11／可用槽 97＝**11.34%**；同一次读数的含后台裸口径为 **20.0%**（client 11 ＋ 后台 9 ＝ 20，裸计数单列、不作占用数）；后台进程实测 9 个。v1 首轮（11:58:08）裸口径 17.0% 对应真实 client 口径 8/97＝8.2474%⇒**8.25%**（证据文件 v1_raw_readings 已原样保留并标注失真）。★**v1.2 订正**：本行原将「裸口径 18.0%」与 11/97 同句并列，18.0% 实为 12:37:35 读数（client 9 ＋ 后台 9 ＝ 18）之值，属沿用未标注 ⇒ 现按读时点分开；另 v1.1 曾记 8.26%，与复核方 commit 6464830 所记 8.25% 属舍入/录入级差异（8/97＝8.2474%），据实并列、以 8.25% 为准 |
| D-4 不据实 | 库清单含 test_db_monitor 16 MB | **test_db_monitor 已不存在**：实测 pg_database 仅 5 库（db_monitor 561 MB、timeseriesdb 1210 MB、postgres 9351 kB、template0 7361 kB、template1 8953 kB）；该库系历史遗留，仓库根目录 V3.0_DIALECT_MATRIX.md:87 载有 DROP DATABASE IF EXISTS test_db_monitor | 12:3x 实测 pg_database 列表与体量；复核方所引路径 docs/V3.0_DIALECT_MATRIX.md 实为**仓库根目录** V3.0_DIALECT_MATRIX.md（复核件该处路径标注有误，事实成立） |
| D-5 落点错记 | 「compose 给 timescaledb 服务加 command」落点在本仓 | **编排落点在本仓之外**：该容器由 **C:\DB_Monitor\docker-compose.dev.yml** 管理（compose project＝db_monitor、working_dir＝C:\DB_Monitor、service＝timescaledb），命名卷实名为 db_monitor_timeseries_data ⇒ 该步骤属 G-5（待 muse 裁决），G-5 范围据此扩至「编排文件改动」 | docker inspect db-aiops-timescaledb 的 labels 与 Mounts 实测；Cmd=["postgres"] ⇒ 追加 command 不破坏 entrypoint（此点 v1.0 表述成立） |
| D-6 动作面低估 | S1＝「容器重启，全部连接断 5–15 秒」 | S1 实际是**容器重建（recreate）而非原地重启**：command 变更致 config-hash 变化触发重建（现存容器标签含 com.docker.compose.replace），RestartPolicy=no；与 DB-005R §15／§20 既定「原地 docker start、不重建」口径**不同类**，须单独立项并单独登记 | docker inspect labels（config-hash 4b7e9863…、com.docker.compose.replace=db-aiops-timescaledb）、RestartPolicy=no、StartedAt 2026-11-13T01:37:12Z＝本地 09:37:12、RestartCount=0 |
| D-7 时区未标注 | oldest_backend_start＝2026-11-13 02:27:12 | 该值为 **UTC**，本地时 **10:27:12**，恰为采集器 10:27:10 拉起后的首条连接；v2 脚本同时输出 utc 与 local_cst 并标注时区 | v2 实跑 oldest_client_backend={utc: 2026-11-13 02:27:12, local_cst: 2026-11-13 10:27:12}；容器 09:37:12 本地时启动 |
| D-8 授权口径 | 「S1／S3 均属需用户重新授权」 | **订正**：订单任务 2 已明文授权「D2 之后实施推荐路径」，故 D2 后实施 (b) **无需再向用户索权**；仅 **S1（动他方 compose，见 D-5）与 S3（动他方工程副本）受 G-5 待 muse 裁决**。当日动作层仍受用户 11:53:30「暂不处置、只观察、再次打满即回函」约束（D2 之前不动库） | 订单文件 docs/pm/orders/DB-026-…md 第 13、14 行原文；DB-005R 回执 docs/pm/reports/DB-005R-receipt-m-team.md:285「★据实并列：同一用户拍板在 L280 与 STATUS v1.46 两处的标签差」确立合读口径（路径归属取 STATUS v1.46、当日动作取 L280 与问卷作答记录） |

采纳复核方建议（S 项，我方不代裁其取舍，仅登记采纳与否）：

| 编号 | 建议 | 我方处置 |
|---|---|---|
| S-1 | 把 (b) 拆 b1（建非 superuser 角色＋授权＋DB_Monitor 切角色，**无需 postmaster 重启**）／b2（抬 max_connections=200，可选二期），抬天花板非必需 | **采纳**：主路径改为 b1；b2 降为可选二期（落点在他方 compose，属 G-5）。我方补充前提：b1 的限流只有在 DB_Monitor 侧真正切到该角色后才对它生效（rolconnlimit 对 superuser 无效），故 b1 的 DB 侧部分我方可执行、DB_Monitor 侧切换仍属 G-5 |
| S-2 | 治本未覆盖平台侧同类根因（ORM 在非生产 CONN_MAX_AGE=0 下的连接滞留；平台自身即当前主要连接方） | **采纳**：列为 G-6，D2 后独立处置（平台侧角色限额 或 ORM 连接滞留治理），不得因本次治本而把风险从一侧挪到另一侧 |
| S-3 | 角色授权清单须覆盖 dev 副本的 migrate 需求（public schema CREATE、CREATEDB/建扩展、TimescaleDB 扩展函数授权） | **采纳**：写入 b1 授权清单与第七节受控验证项 |
| S-4 | 受控验证须前置门禁（先确认角色已切换且 rolconnlimit 生效，再制造并发），并排在 D2 判定窗外、避开 15:00–15:10 | **采纳**：见第七节验证顺序 |
| S-5 | TIMESCALEDB_POOL_MAX 的 200 上限校验在 monitor/appconf.py 的 SPECS 层；settings.py 另有一份无上限校验的同名环境变量直读，且两处各有默认值 16 与 appconf 自载「默认值只定义一次」相悖 | **采纳为口径标注**：属仓库既有口径问题、非本单引入，本单只披露不改动 |
| S-6 | 巡检读数字段标准化（client_backends／可用槽余量／客户端侧 PID 归组／application_name 分布／backend_start 标注时区／读时点） | **采纳**：v2 脚本字段已按此标准化 |

## 一、结论摘要（v1.1 更新）

1. **主路径改为 b1**：建非 superuser 角色 dbmonitor_app（CONNECTION LIMIT 30）＋授权 db_monitor/timeseriesdb，让 DB_Monitor 开发副本切到该角色 —— 不需要 postmaster 重启、不打断 D2 判定窗、不迁任何数据；限流由数据库强制，代码再漏也占不满池。
2. **b2 降为可选二期**：抬 max_connections 至 200。b1 生效后（DB_Monitor 侧封顶 30、平台侧时序池上限 16 ＋ ORM 若干），现有 100 槽已够用 ⇒ 抬天花板非必需；且其落点在他方 compose 且触发容器重建（D-5/D-6），须单独立项。★**据 STATUS v1.47：b2 暂缓，且其 compose 文件改动不在本次获准范围** ⇒ 本单不实施 b2。
3. **二阶段仍为 (a′)**：给 DB_Monitor 开发副本单独实例、DB-AIOps 原地不动 —— 隔离效果与 (a) 等价而迁移量为零。★据 STATUS v1.47：(a′) 为二阶段方案，实施前须另行确认。
4. **(a) 仍不首选**：为 DB-AIOps 迁独立实例要搬 1.77 GB，恰好包含 D2 正在判定的 sql_stat 15:00 桶，还需停采集保一致，风险收益倒挂。
5. **D2 之前仍只评估与准备**：本轮（含本版订正）未动库、未重启时序库容器/采集器/Web，未改 max_connections。
6. 缺口更新（v1.3）：**G-5 已获准**（STATUS v1.47，限 b1 的 S3：DB_Monitor 开发副本的连接指向/角色切换/重启；compose 改动不在范围）；**S-2 已采纳（平台侧同设限流，与 b1 同批）**，我方原建议「列 D2 后独立处置」与 PM 方向不同，据实并列、不代裁；剩余待办＝G-1（TimescaleDB dump/restore 保真度，只影响 (a) 成本）、G-2（application_name 不可区分，需重启才生效）、G-3（角色授权完整性待受控验证）。
7. **平台侧生效前提（PM 裁决 S-2 的执行前置）**：平台侧须与 b1 同批「切角色＋设限额＋重启使其生效」三件事齐做，否则其限额对自身即空话（详见 §v1.3 基线更新第 4 条）。

**关于用户决策的表述口径（两粒度并列，不得单引其一）**：路径归属层（STATUS v1.46／v1.47，裁决面）＝用户已拍板选治本，并于 12:31 确认「治本照做，D2 之后实施」；当日动作层（DB-005R 回执 L280＋问卷作答记录）＝④暂不处置、只观察、再次占满即回函重拉。★二者并存不互斥，我方不代裁；操作约束取交集执行 —— D2 之前不动库、不改 max_connections、不重启时序库容器/采集器/Web、不擅杀在跑的进程；D2 之后按本单实施授权推进（b1）—— 据 STATUS v1.47，G-5 已获准（限 b1 的 S3），b2 暂缓且不在获准范围；平台侧同批（切角色＋设限额＋重启使其生效）。

## 二、实测事实基线（v1.1 订正后）

### 2.1 时序库容器、库与体量

- 容器 db-aiops-timescaledb（compose project＝**db_monitor**，service＝timescaledb，**编排文件＝C:\DB_Monitor\docker-compose.dev.yml**，working_dir＝C:\DB_Monitor，命名卷＝**db_monitor_timeseries_data**），Ports 0.0.0.0:5432 与 [::]:5432，Cmd=["postgres"]，RestartPolicy=no，StartedAt 2026-11-13T01:37:12Z（＝本地 09:37:12），RestartCount=0。
- 实测 show：max_connections＝**100**；superuser_reserved_connections＝**3**（⇒ 非 superuser 可用槽位 **97**）。
- 实测库清单（pg_database，12:3x）：**db_monitor 561 MB、timeseriesdb 1210 MB、postgres 9351 kB、template0 7361 kB、template1 8953 kB**；**test_db_monitor 已不存在**（v1.0 误列，见 §〇 D-4）。
- timeseriesdb 行数实测（12:1x 复核方口径，我方于 12:3x 同口径复现为增长中）：metric_point 约 2,177,848、sql_stat 约 271,094、collection_snapshot 约 136,441、session_sample 0；sql_stat max(time) 续写推进中。
- db_monitor 的 public schema 对象数实测 105（information_schema 口径＝表＋视图；pg_tables 口径＝102，差异为视图，复核方 §一-4 提示口径标签须精确化）。

### 2.2 平台侧两条连接面

- Django ORM（dbmonitor/settings.py:233-250）：库名 POSTGRES_DB 默认 db_monitor；HOST 默认 localhost；PORT 默认 5432；**CONN_MAX_AGE 取 DB_CONN_MAX_AGE（生产默认 60、非生产默认 0）**；application_name 固定 db_monitor；connect_timeout=10。
- 时序连接池（monitor/timeseries.py:59-68）：ThreadedConnectionPool，**maxconn 取 appconf.get('TIMESCALEDB_POOL_MAX')**（monitor/appconf.py:37，默认 16，**上限 200 的校验在该 SPECS 层**；settings.py:272 另有一份同名环境变量直读且无上限校验，且两处各有默认值 16，与 appconf 自载「默认值只在此处定义一次」相悖 —— 属仓库既有口径问题，本单只披露，见 S-5）；库名 timeseriesdb；statement_timeout 默认 15000ms。

### 2.3 占用方进程与连接归组（订正 D-1，分侧口径）

- 本仓平台进程：采集器 PID 48396（C:\DB-AIOps\.venv\Scripts\pythonw.exe manage.py start_monitor）及其子进程 43592；Web PID 45704（manage.py runserver 127.0.0.1:8009 --noreload）及其子进程 47216。
- DB_Monitor 开发副本进程：Windows 侧 47884（C:\DB_Monitor\.venv\Scripts\python.exe manage.py runserver --noreload 0.0.0.0:8000）→ 子进程 39072（实际解释器，LISTENING 8000）；WSL 侧 9715/9716 可见同源 /init 包装（bash -c cd /mnt/c/DB_Monitor … runserver）⇒ v1.0「两侧并列两实例」属口径夸大，本版订正为**同一套开发副本进程的两种命名空间视图**（复核方 D-1 附注口径，我方采信并按此表述）。
- **客户端侧（远端＝5432，真正连着库的一方）实测**：43592（采集器链）×7、47216（平台 Web 链）×2、39072（DB_Monitor 开发副本）×1。
- **服务端侧（本地＝5432）**：40492＝wslrelay.exe ×10（WSL 虚拟网卡转发/中继进程，**不是占用方**）。
- **监听属主**：0.0.0.0:5432 与 [::]:5432 → 36736＝com.docker.backend.exe；[::1]:5432 → 40492＝wslrelay.exe。
- ⇒ **结论订正**：此刻占用连接池的主要是我方平台自身（采集器＋Web 合计 9 条），DB_Monitor 开发副本仅 1 条；DB-005R §20 L270 所记「100 条客户端侧连接属宿主 PID 21728（C:\DB_Monitor\…runserver）」系客户端侧归因，与本节口径一致、不受影响。

### 2.4 连接池占用读数（订正 D-3，client 口径）

- 12:37:35（v2 脚本首次实跑）：client_backends **9**／可用槽 97 ＝ **9.28%**；含后台进程的裸口径 18.0%（后台进程 9 个）；client_side 43592×5、47216×2、39072×1。
- 12:38:20（已落证据文件 corrected_reading）：client_backends **11**／可用槽 97 ＝ **11.34%**；同次读数含后台裸口径 **20.0%**（11 ＋ 9 ＝ 20）；client_side 43592×7、47216×2、39072×1；server_side 40492×10；最老客户端连接 UTC 02:27:12 ＝ 本地 10:27:12（采集器拉起后首条）。
- 结论：连接池**未饱和**，本轮无占满事件可处置。§20 记录的「100/100 占满」为 11-13 08:35 宿主重启后由 DB_Monitor 开发实例造成（DB-005R §20 与 STATUS v1.45 已记录事实），本轮未复现、未重启任何进程。

## 三、根因判定（v1.1 增补 S-2）

1. 共享面过大：两份同仓工作副本共用同一时序库容器与同一组库（db_monitor + timeseriesdb）、同一批表 ⇒ 任一侧的连接风暴都会打到对方头上。
2. 限流机制在当前账号下无效：两份副本都以 superuser postgres 登录，PostgreSQL 的 rolconnlimit 对 superuser 不生效（实测 pg_roles：postgres rolsuper＝t、rolconnlimit＝-1）⇒ 「在数据库侧给 DB_Monitor 侧设连接池上限」若不先把该侧换成非 superuser 角色即为空话。这是本次评估最关键的技术前提。
3. 平台自身也有可堆积面（S-2 补齐）：ORM 侧在非生产模式下 CONN_MAX_AGE=0，runserver 每请求新建短命线程且 close_request 不调用 connections.close_all()，DatabaseWrapper 引用环滞留 PostgreSQL 连接直至 Gen-2 GC 才释放（该机制由本仓 settings.py:241-244 自带注释记载）⇒ 高并发下 ORM 连接可堆积。**当前运行态下平台侧本就是主要连接持有方（9/10 条客户端连接）**，故治本不能只治一侧。
4. 组合结果：DB_Monitor 开发实例曾在短时间内吃满 100/100，采集器、平台 Web、psql 一并被拒；平台自身 Web 高并发时同样可能成为占用方。

## 四、两条治本路径评估（v1.1 订正落点与动作面）

### 4.1 路径 (a)：为 DB-AIOps 分配独立 Postgres 实例（落点与风险不变，结论仍不首选）

- 改动点：新增 timescaledb-aiops 服务（新容器 + 新命名卷 + 独立端口 + 专用非 superuser 角色 + 调高 max_connections）；pg_dump/pg_restore 迁移 db_monitor（561 MB / 105 个 public 对象）与 timeseriesdb（1210 MB / 4 张超表与连续聚合）；平台连接指向切换（POSTGRES_* 与 TIMESCALEDB_*）；重建连续聚合、保留策略、压缩策略；重启采集器与 Web。
- 风险：迁移量约 1.77 GB；为一致性须停采集，直接打断 D2 判定窗（11-12/13/14 三日 15:00 桶）；TimescaleDB 对连续聚合与策略的 pg_dump/restore 保真度未实测（G-1）；切面大，回退需处理切换窗口内的数据分叉。
- 回退：连接指向切回旧容器（旧数据仍在原卷），新容器保留供事后比对；切换窗口内写入新实例的数据需人工合并。
- 按订单要求：本路径实施前须先报黑板请 PM 确认。

### 4.2 路径 (b)：数据库侧限额（b1）＋ 抬 max_connections（b2）—— 拆分（采纳 S-1）

**b1（主路径，必做；无需 postmaster 重启、不打断 D2 判定窗、不迁数据）**

1. 建角色：CREATE ROLE dbmonitor_app LOGIN NOSUPERUSER CONNECTION LIMIT 30 PASSWORD '…'（凭据只落 .env/纳管配置，不进仓库、不进本报告）。
2. 授权（须覆盖 dev 副本的迁移能力，见 S-3）：db_monitor 与 timeseriesdb 两库的 public schema CREATE；现有全部表/视图/序列授权；TimescaleDB 扩展函数与 _timescaledb_internal 相关权限；必要时 CREATEDB。
3. DB_Monitor 开发副本切到该角色（宿主侧环境变量切换，不在本仓、不入本仓提交）⇒ 其连接被数据库强制封顶 30。
4. **平台侧同批（S-2）**：平台侧 ORM 连接面（库 db_monitor）与时序连接面（库 timeseriesdb）当前默认身份均为 superuser（settings.py L237／L265）⇒ 须同样「切角色 ＋ 设限额 ＋ 重启使其生效」三件事齐做；重启 Web 与采集器 ⇒ 排在 D2 判定窗之后、且与 15:00–15:10 采集驱动窗错开。
- 影响面：仅新增角色与授权（DB 目录写入），不重启任何容器/进程，采集与写入不中断；执行时点仍按订单排在 D2 之后（「D2 之前不动库」）。
- 风险：授权清单不全会报权限错（S-3 已补清单与验证项）；若 DB_Monitor 侧未真正切换，限流对它不生效（前置条件，不可省）。
- 回退：DROP ROLE dbmonitor_app（连带撤销其授权）＋ DB_Monitor 侧环境变量切回 postgres ＋ 重启该开发副本。

**b2（可选二期；涉及容器重建，落点在他方工程）**

1. 抬 max_connections 至 200：改 **C:\DB_Monitor\docker-compose.dev.yml** 的 timescaledb 服务加 command（postgres -c max_connections=200）⇒ 落点在本仓之外（属 b2，本次不在获准范围、暂缓）。
2. 执行：docker compose up -d timescaledb ⇒ command 变更致 config-hash 变化，触发**容器重建（recreate）**而非原地重启；容器 RestartPolicy=no、数据在命名卷内持久。
3. 影响面：重建动作面重于「重启」（healthcheck 重建、compose 依赖重排）；与 DB-005R §15／§20 既定「原地 docker start、不重建」口径**不同类**，须单独立项并单独登记。
- **状态（STATUS v1.47）：b2 暂缓，且 compose 文件改动不在本次获准范围** ⇒ 本单不实施 b2。
- 必要性：b1 生效后（DB_Monitor 侧封顶 30、平台侧时序池上限 16 ＋ ORM 若干）现有 100 槽足够 ⇒ **b2 非必需**；若用户坚持抬高天花板，按 D2 后、避开 15:00–15:10 桶时段执行。
- 回退：还原 compose 并再次 up（重建回旧 command）。

### 4.3 补充路径 (a′)：给 DB_Monitor 开发实例单独实例、DB-AIOps 原地不动

- 改动点：为 DB_Monitor 开发副本起一个独立 Postgres 实例并改其指向；DB-AIOps 的容器、库、数据、连接指向全部不动。
- 隔离效果与 (a) 等价，迁移量为零（DB_Monitor 是开发副本、数据可弃）；DB-AIOps 侧 1.77 GB（D2 判定所依赖）原地不动。
- 代价：动的是用户自建工程（C:\DB_Monitor），需用户知会（G-5）；其历史开发数据需重新初始化（migrate ＋ 种子）。

## 五、推荐路径与理由（v1.1 更新）

**主路径 b1；b2 与 (a′) 列为后续可选；不首选 (a)。**

1. b1 不动数据即可治本：限流点放在数据库侧、由 PostgreSQL 强制。DB_Monitor 侧换成非 superuser 角色后其连接数被 CONNECTION LIMIT 硬性封顶，代码再怎么泄漏也占不满池，平台侧恒有余量 —— 修的是「为什么会占满」。
2. b1 对 D2 判定窗零风险：无重启、无采集中断、无数据迁移；而 (a) 要搬走的正是 D2 正在判定的 sql_stat 15:00 桶，还需停采集。
3. b1 无需再索权：订单任务 2 已授权 D2 后实施（订正 D-8）；涉及本仓之外的落点受 G-5 范围限定 —— **据 STATUS v1.47：b1 的 S3 已获准，b2 暂缓且其 compose 改动不在获准范围**。
4. (a) 成本最高、收益与 (a′) 等价：(a) 要迁移 1.77 GB 并打断判定窗，(a′) 迁移量为零 ⇒ 二阶段隔离选 (a′)。若用户坚持「必须是 DB-AIOps 独占实例」口径，则 (a) 作终局方案保留，单独请 PM 确认并排在 D2 与 b1 之后。

## 六、实施步骤（D2 复判 11-15 09:50 之后执行；本轮未执行）

| 步 | 动作 | 影响面 | 回退 |
|---|---|---|---|
| b1-0 | 备份：docker inspect 与 compose 文件留档；pg_dumpall --globals-only 导出角色 | 无 | 丢弃备份 |
| b1-1 | 建角色 dbmonitor_app（NOSUPERUSER、CONNECTION LIMIT 30） | DB 目录写入，不重启 | DROP ROLE |
| b1-2 | 授权（两库 public schema、全部表/视图/序列、TimescaleDB 扩展与内部对象、必要时 CREATEDB —— 覆盖 dev 副本 migrate 能力，见 S-3） | 同上 | DROP ROLE 连带撤销 |
| b1-6 | **首次建超表的属主责任划分（G-3 残余）**：TimescaleDB 的 create_hypertable／add_retention_policy／add_compression_policy 需**对象属主**权限 ⇒ dev 副本首次建超表由 **postgres 执行一次**（或按需把目标对象属主转给 dbmonitor_app）。★**责任方＝我方执行侧（b1 实施人），在 D2 后实施窗口内自行定人并回报**（依复核侧 r30 第一节：该项待 D2 后定人、复核侧不代裁）⇒ **不占 muse 裁决位** | 一次性初始化动作，不打断采集；属 b1 之后的独立动作 | 若走属主转移：ALTER TABLE … OWNER TO dbmonitor_app（回退再转回 postgres） |
| b1-3 | DB_Monitor 开发副本切到该角色并重启该副本（宿主侧环境变量） | 用户工程，其 dev 连接断开重连；**G-5 已获准**（STATUS v1.47，限 b1 的 S3） | 环境变量切回 postgres 并重启 |
| b1-4 | 受控验证（先验证 rolconnlimit 生效，再制造并发，见 S-4 与第七节） | 制造短暂连接压力 | 无需回退 |
| b2（可选） | 改 C:\DB_Monitor\docker-compose.dev.yml 加 max_connections=200 → docker compose up -d timescaledb（**容器重建**） | 全部连接断 5–15 秒；重建 ≠ 原地重启；**暂缓、不在本次获准范围**（compose 改动属 b2） | 还原 compose 并再次 up |
| b1-5 | 平台侧同批（S-2）：建平台侧非 superuser 角色＋设限额＋切 POSTGRES_USER／TIMESCALEDB_USER＋重启 Web 与采集器使其生效 | 采集写入短时中断；**须排 D2 判定窗之后并避开 15:00–15:10** | 切回 postgres 并重启 |
| G-6（**PM 裁与 b1 同批**） | 平台侧限额（见 b1-5）；ORM 引用环滞留按 PM 裁决列为已知问题 | 随 b1-5 实施 | 见 b1-5 |

实施窗口建议：11-15 09:50 D2 出结果之后，且避开 15:00–15:10 桶写入时段。b1 四步不含重启，可与 b2 解耦排期。

★**实施物料已备（D2 前只备不执行）**：scripts/db026_b1.ps1（驱动，默认 -Mode Verify 只读；Apply/Rollback 须显式 -Confirm ＋ 环境变量 DB026_ROLE_PASSWORD／DB026_APP_ROLE_PASSWORD 注入口令，缺一即拒绝）、scripts/db026_b1_apply.sql（建 dbmonitor_app CONNECTION LIMIT 30 与 dbaiops_app CONNECTION LIMIT 60 ＋ 两库 public schema 授权，幂等；口令经 psql 变量注入、不落文件不进仓库）、scripts/db026_b1_rollback.sql（REASSIGN/DROP OWNED ＋ DROP ROLE 回退，不动既有表与数据）。★**已实测四道闸门**：Verify 模式 exit 0（纯只读，输出 max_connections 100／reserved 3／可用槽 97）；Apply 缺 -Confirm → 拒绝；Apply -Confirm 但缺口令环境变量 → 拒绝；apply.sql 无 pw 变量 → psql RAISE EXCEPTION、exit 3；**测试全程角色数实测仍为 0（未写库）**。

## 七、实施后验证方法（v1.1 更新，client 口径 + 前置门禁）

0. **前置门禁（S-4）**：先确认 b1-3 已生效（pg_roles.rolconnlimit＝30、DB_Monitor 副本进程命令行/连接串已切角色），确认前不得制造任何并发；且本项排在 D2 判定窗之外、避开 15:00–15:10。
1. 连接池：scripts/pg_pool_audit.py（v2）连续 3 轮读数，**client_slot_usage_pct < 80%**（分子＝client backend、分母＝97），alert=false。
2. 占用方归组：客户端侧 PID 归组显示 DB_Monitor 开发副本侧连接数 **≤ 30**，且平台侧连接不受连带影响；server_side/listening_owners 与占用方分开记录。
3. 限流有效性受控验证：DB_Monitor 开发副本侧短时多次访问，pg_stat_activity 中其 client backend 数不超过 30（超限者报 too many connections）⇒ 证明库侧封顶生效而非靠运气。
4. 采集器与 Web 在跑：采集器 PID 与 Web PID 存在、/api/health/ 返回 200；实施前后 PID 变化逐条登记（本单实施时必须记录）。
5. sql_stat 持续续写：max(time) 较实施时点前推，11-15 之后各 15:00 桶按驱动脚本落桶；采集连续性：采样间隙 > 180 秒的计数为 0（沿用 G-11 口径）。
6. 配置复核：show max_connections（未做 b2 则仍为 100）、superuser_reserved_connections、select rolname, rolsuper, rolconnlimit from pg_roles where rolname='dbmonitor_app'。

## 八、D2 之前「只监不擅动」的落实（v1.1 更新）

- 巡检脚本升级为 v2（scripts/pg_pool_audit.py）：只读、全 SELECT/SHOW、连接走容器内 psql 本地 socket（不读 .env、不落口令）、命令行输出经脱敏；新增分侧归组（客户端侧/服务端侧/监听属主）＋客户端侧 PID→进程映射＋client 口径占用率＋UTC/本地双时区（D-1/D-2/D-3/D-7/S-6 已闭合）。
- 读数落库：docs/pm/reports/evidence/DB-026-pool-audit-20261113.json（结构：corrected_reading ＋ v1_raw_readings 原值保留并标注失真 ＋ corrections_applied ＋ supersedes_note）。
- 今日巡检读数（v2 脚本、只读、client 口径）：12:38:20 → client 9／97＝9.28%；13:04:52 → client 9／97＝9.28%（含后台裸口径 18.0%）；**15:28:23 → client 13／97＝13.40%**（含后台裸口径 22.0%；client_side 43592×8／47216×2／39072×1，采集器链因 15:05 采集驱动上行；server_side 40492×12），三次均 alert=false；容器 StartedAt 仍 2026-11-13T01:37:12Z、RestartCount=0，采集器 48396／43592 与 Web 45704／47216 四进程在位。★**今日收尾读数 20:13:39 → client 11／97＝11.34%**（含后台裸口径 20.0%；client_side 43592×5／47216×2／39072×1，server_side 40492×10），alert=false；★**D2 前全程未动库：dbmonitor_app／dbaiops_app 两角色实测仍为 0**（权威方 15:5x 独立查库同结论）。
- 巡检节奏：11-14、11-15 各至少 1 次读数并落 evidence（新增文件，不覆盖既有证据），D2 复判前每轮在黑板留痕。
- 若期间连接池再次占满：只报黑板（任务单跟进 + 本单报告留痕），不重启时序库容器、采集器、Web，也不擅自终止任何进程，等用户处置。
- 本轮未重启任何进程，时序库容器 StartedAt 仍为 2026-11-13T01:37:12Z（本地 09:37:12）、RestartCount=0，未发生 postmaster 重启。

## 九、缺口与待确认项（v1.1 更新）

- G-1：TimescaleDB 连续聚合与保留/压缩策略在 pg_dump/restore 下的保真度未实测 —— 只影响路径 (a) 的成本与风险估算，不影响首推 b1。
- G-2：两份副本的 application_name 同为 db_monitor，现有 pg_stat_activity 无法逐副本归属连接；已备「application_name 可配置化」补丁（需重启 Web 与采集器才生效），未在 D2 窗口内动进程。
- G-3：非 superuser 角色 dbmonitor_app 的授权完整性未实测 —— 已按 S-3 补齐清单（含 dev 副本 migrate 能力），待 b1-2 之后受控验证。★**属主权限残余（首次建超表）已定责**：由我方执行侧在 D2 后实施窗口自行定人并回报（不占 muse 裁决位，依复核侧 r30 第一节）；板面侧已由落笔方订正廿三（commit f7e47c4）把「平台侧须同批切角色＋设限额＋重启生效」三件事前提落板。
- G-4：scripts/validate.sh unit 在本机无法整体跑通，两处均为环境口径、非仓库缺陷：①工作区 CRLF 行尾使 bash 解析报 set: pipefail invalid option name（与 SYNC14 记录的 G-7 同源）；②本机 bash 走 WSL，validate.sh 会激活 Windows 版 .venv/Scripts/activate，导致 python 解析为 Windows python.exe、无法解析 /mnt/c/... 路径，红线扫描阶段假失败。本轮以 Windows 解释器直跑 unit 的等价两阶段，结果见第十节。
- G-5（**已获准、范围限定**）：STATUS v1.47 裁决 G-5 获准，**仅限 b1 的 S3**（DB_Monitor 开发副本 **C:\DB_Monitor** 的连接指向/角色切换/重启）；**其编排文件 C:\DB_Monitor\docker-compose.dev.yml 的 command 改动属 b2，不在本次获准范围**（b2 暂缓）。裁决前我方未动该工程任何文件。
- G-6（**PM 裁与 b1 同批，方向差异据实并列**）：我方原建议「平台侧限额不与 b1 同期做、列 D2 后独立处置」，PM 裁「与 b1 同批」⇒ 落笔方已按 PM 方向取「同批」并记「治本不再只治一侧」，我方接受该处置、不代裁。★**执行前提（我方补齐）**：平台侧同批须「切角色＋设限额＋重启使其生效」三件事齐做（settings.py L237／L265 默认 superuser ⇒ 只设限额即空话）；ORM 在非生产 CONN_MAX_AGE=0 下的引用环滞留**按 PM 裁决列为已知问题**，不并入 b1。

## 十、本轮改动与验证记录（v1.1）

- 本轮改动（均为本单自身文件）：
  1. scripts/pg_pool_audit.py（v1 → v2：分侧归组、client 口径、双时区、进程映射、字段标准化）；
  2. docs/pm/reports/DB-026-report-m-team.md（v1.0 → v1.1：逐项订正 D-1～D-8、采纳 S-1～S-6、b1/b2 拆分、G-6 新增）；
  3. docs/pm/reports/DB-026-receipt-m-team.md（订正占用方归因与库清单表述）；
  4. docs/pm/reports/evidence/DB-026-pool-audit-20261113.json（订正读数口径并保留 v1 原值供追溯）。
  5. v1.2 追加订正（据复核方 13:1x 终态回签第三节「据实并列」）：§〇 D-3 行的裸口径按读时点拆开（18.0%→ 20.0%）、v1 首轮 client 口径 8.26%→8.25%（8/97＝8.2474%）；§一 补「用户决策两粒度并列」口径；证据文件同步订正 true_client_usage_pct 8.26→8.25 并加注口径说明。**结论档位不变（有条件通过），无待办遗留。**
  6. v1.3 基线更新（据 STATUS v1.47 / commit 53a2900）：G-5 获准（限 b1 的 S3）、S-2 采纳平台侧同批实施、b2 暂缓且不在获准范围、用户 12:31 确认「治本照做、D2 之后实施」；据此订正我方 13:2x 函中已过期的「待裁决」状态描述，并补齐平台侧生效前提（切角色＋设限额＋重启生效三件事齐做，证据 settings.py L237／L265）。**结论档位不变（有条件通过）：评估面条件已满足，实施面待 11-15 09:50 D2 之后启动。**
  7. v1.4 增补实施物料索引（订单任务 2 的「D2 之前…备脚本、备回退」）：新增 scripts/db026_b1.ps1／db026_b1_apply.sql／db026_b1_rollback.sql 三个 D2 后执行件（默认只读校验；Apply/Rollback 四道闸门实测拒绝；测试期未写库、角色数仍为 0）。**报告结论、实施步骤内容与档位均不变**，仅增补物料索引与实测记录。
  8. v1.5 增补三项（据复核侧 r30 与权威方 r32 的复核结论）：①**G-3 属主残余定责** —— 首次建超表（create_hypertable／保留策略／压缩策略 需属主权限）由我方执行侧在 D2 后实施窗口自行定人并回报，**不占 muse 裁决位**（复核侧 r30：不代裁、待 D2 后定人），并已落 §六 b1-6；②今日收尾巡检读数 20:13:39（client 11／97＝11.34%，alert=false，D2 前角色数实测仍为 0＝未动库）；③**备件入库状态的据实并列** —— 权威方 r32 第四节「三份 b1 物料未被 git 跟踪」为 15:3x 读时点快照，已被 b56f168（15:37:52）取代，现态为**已跟踪并已推送**（复核侧 r30 同判为代次差不判错）；我方 scripts/scan_secrets.py 实测 exit 0「密钥扫描通过」，三份备件**无任何明文口令赋值**，「不进仓库」一句本意指**凭据**不进仓库、备件本身入库且经扫描合格。**结论档位、实施步骤内容与档位均不变（有条件通过）**。
- 未改动：docs/pm/STATUS.md、docs/pm/DECISIONS.md（muse 维护面）；未改动任何业务代码；未新增配置项（故不涉及 monitor/appconf.py SPECS 登记）；未改动复核方文件与 DB-005R 回执/母报告。
- validate（因 G-4 环境口径，以 Windows 解释器直跑 unit 的等价两阶段，结果对应本版提交时点 HEAD）：
  - 红线扫描 .venv\Scripts\python.exe scripts/lint_redlines.py → exit 0（输出「红线约束扫描通过」）；
  - unit 测试 DJANGO_SETTINGS_MODULE=dbmonitor.settings_test_unit python manage.py test monitor --tag unit → exit 0，Ran 434 tests。
- 证据份数：1 份（DB-026-pool-audit-20261113.json，含 corrected_reading 与 v1_raw_readings 两组读数）。

## 版本记录

| 版本 | 日期 | 修订人 | 修订说明 |
|---|---|---|---|
| v1.0 | 2026-11-13 | MMMan | DB-026 评估版：两条路径评估、推荐 (b) 主路径 + (a′) 二阶段、实施与回退步骤、D2 前只读巡检与首日读数；结论有条件通过（commit ed02832） |
| v1.1 | 2026-11-13 | MMMan | 据 DB-026 独立复核（commit 2d6d6c0）逐项订正：D-1 占用方归因（分侧口径，40492 非占用方）、D-2 巡检脚本分侧归组＋进程映射、D-3 占用率改 client 口径（分母扣 3 保留槽）、D-4 库清单剔除已删的 test_db_monitor、D-5 编排落点在本仓之外（他方 compose）、D-6 S1 为容器重建而非原地重启、D-7 时间标注 UTC/本地、D-8 授权口径订正（D2 后实施无需再索权）；采纳 S-1（拆 b1/b2，主路径改 b1）、S-2（新增 G-6）、S-3～S-6；巡检脚本升级 v2 并订正证据口径；结论档位不变（有条件通过） |
| v1.2 | 2026-11-13 | MMMan | 据复核方 13:1x 终态回签（评估面闭环、条件已满足；复核件自身 3 处错误已由其 commit 6464830 订正）作两项据实并列订正：①§〇 D-3 行「含后台裸口径 18.0%」与 11/97 同句并列属沿用未标注（18.0% 实为 12:37:35 读数）→ 按读时点拆开为 12:38:20 读数的 20.0%；v1 首轮 client 口径 8.26%→8.25%（8/97＝8.2474%）；②§一 补「用户决策两粒度并列」口径（路径归属取 STATUS v1.46、当日动作取 L280＋问卷记录，操作约束取交集）；证据文件同步订正。**评估面档位不变（有条件通过），实施面不启动、G-5/G-6/评档待 muse/PM** |
| v1.3 | 2026-11-13 | MMMan | 据 STATUS v1.47（commit 53a2900，14:38:02）作基线更新：G-5 获准（仅限 b1 的 S3；compose 改动属 b2、不在获准范围）、S-2 采纳（平台侧同设 CONNECTION LIMIT，与 b1 同批；ORM 滞留列为已知问题）、S-1 b1/b2 拆分（b2 暂缓）、用户 12:31 确认「治本照做、D2 之后实施」；订正我方 13:2x 函中已过期的「待裁决」状态描述，并补齐平台侧生效前提（同批须切角色＋设限额＋重启生效三件事齐做；settings.py L237／L265 默认 superuser）；今日巡检读数 12:38:20／13:04:52／15:28:23 三次（client 9／9／13，末次 13.40%，alert=false，容器与四进程未变）。**结论档位不变（有条件通过）：评估面条件已满足，实施面待 11-15 09:50 D2 之后启动** |
| v1.4 | 2026-11-13 | MMMan | 增补实施物料索引（订单任务 2「D2 之前…备脚本、备回退」）：新增 scripts/db026_b1.ps1（驱动，默认只读 Verify；Apply/Rollback 须 -Confirm ＋ 环境变量注入口令）、db026_b1_apply.sql（建 dbmonitor_app LIMIT 30 与 dbaiops_app LIMIT 60 ＋ 两库授权，幂等，口令经 psql 变量注入）、db026_b1_rollback.sql（REASSIGN/DROP OWNED ＋ DROP ROLE 回退）；已实测四道闸门并确认测试期未写库（角色数 0）。**结论档位、实施步骤内容均不变（有条件通过），实施仍排在 11-15 09:50 D2 之后** |
| v1.5 | 2026-11-13 | MMMan | 据复核侧 r30 与权威方 r32 增补三项：①G-3 属主残余定责（首次建超表由我方执行侧在 D2 后定人并回报，不占 muse 裁决位，落 §六 b1-6）；②今日收尾巡检读数 20:13:39（client 11／97＝11.34%，alert=false，D2 前角色数实测仍为 0）；③备件入库状态据实并列（r32 §4 为 15:3x 快照、已被 b56f168 取代，现态已跟踪已推送；scan_secrets exit 0 密钥扫描通过，备件无明文口令，「不进仓库」指凭据不入库）。**结论档位与实施步骤内容均不变（有条件通过），实施仍排在 11-15 09:50 D2 之后** |
