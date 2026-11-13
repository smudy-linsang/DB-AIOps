有条件通过

# DB-026 交付报告：连接池治本 —— 独立实例或调高 max_connections

- 版本：v1.0（评估版；D2 复判前落评估部分，实施后补 v2.0 终版）
- 执行：MMMan｜单号：DB-026｜下达：muse（用户 2026-11-13 拍板三选一选治本）
- 成文：2026-11-13｜基线 HEAD：接单时 6eec91f（main，已 pull --ff-only 同步）；提交时 main 已由他方并发前推，本单只提交自身 4 个文件，未改动他人内容
- 结论档位口径：本版结论「有条件通过」= 评估与准备已完成且推荐路径明确，但推荐路径的实施按任务单要求排在 11-15 09:50 D2 复判之后，尚未实施，故不得记「通过」。

## 一、结论摘要

1. 推荐路径：以「(b) 调高 max_connections + 数据库侧对 DB_Monitor 侧强制连接上限」为主路径；以「(a′) 给 DB_Monitor 开发实例单独实例、DB-AIOps 原地不动」为二阶段彻底隔离方案。
2. 不推荐按任务单原文的「(a) 为 DB-AIOps 分配独立 Postgres 实例」作为首选项：它要迁移的 1.77 GB 数据恰好包含 D2 正在判定的 sql_stat 15:00 桶，为一致性还需停采集，风险与收益倒挂。
3. D2 之前本轮只做了评估、只读巡检脚本与备份/回退物料准备，未动库、未重启时序库容器、采集器、Web（见第八节逐项自证）。
4. 遗留缺口 5 条（G-1..G-5），其中 G-5「能否改动本仓之外的 DB_Monitor 开发副本」需 muse 裁决。

## 二、实测事实基线（时点 2026-11-13 11:54–12:05）

全部为宿主本机实测；命令与取值如下，未使用任何历史推断值。

### 2.1 时序库容器与库体量

- 容器 db-aiops-timescaledb（compose 服务 timescaledb，image timescale/timescaledb:latest-pg16，端口 5432:5432，卷 timeseries_data）运行中，healthy。
- 实测 show max_connections = 100；show superuser_reserved_connections = 3。
- 库清单与体量（pg_database_size）：timeseriesdb 1205 MB、db_monitor 560 MB、test_db_monitor 16 MB、postgres 9351 kB。
- timeseriesdb 行数实测：metric_point 2,171,816；sql_stat 269,622；collection_snapshot 136,348；session_sample 0。
- db_monitor 的 public schema 表数实测 105 张（Django ORM 面：纳管实例、告警、用户权限等）。

### 2.2 平台侧两条连接面

- Django ORM（dbmonitor/settings.py:233-250）：库名 POSTGRES_DB 默认 db_monitor；CONN_MAX_AGE 取 DB_CONN_MAX_AGE（生产默认 60、非生产默认 0）；application_name 固定写死为 db_monitor；connect_timeout=10。
- 时序连接池（monitor/timeseries.py 的 TimeseriesStorage._get_pool）：ThreadedConnectionPool，maxconn = TIMESCALEDB_POOL_MAX（monitor/appconf.py:37，默认 16，上限 200），库名 TIMESCALEDB_NAME 默认 timeseriesdb，statement_timeout 默认 15000ms。该池是借还式，天然有上限 16。

### 2.3 占用方进程盘点

- 本仓平台进程：采集器 PID 48396（C:\DB-AIOps\.venv\Scripts\pythonw.exe manage.py start_monitor）及其子进程 43592；Web PID 45704（manage.py runserver 127.0.0.1:8009 --noreload）及其子进程 47216。
- DB_Monitor 开发实例：Windows 侧 PID 47884（C:\DB_Monitor\.venv\Scripts\python.exe manage.py runserver --noreload 0.0.0.0:8000）及其子进程 39072；WSL 侧 PID 9715/9716（cd /mnt/c/DB_Monitor 后同一 runserver 命令，已运行 5139 秒）。
- 关键发现：C:\DB_Monitor 经核查是本项目的另一份工作副本，不是无关工程 —— 其 dbmonitor/settings.py 与本仓同构（同样的 DATABASES/CONN_MAX_AGE/application_name/TIMESCALEDB_POOL_MAX 段），并含 AGENTS.md、phase6/phase7/phase8、frontend、docs 等同构目录。两份副本因此共用同一容器、同一组库（db_monitor + timeseriesdb）、同一批表。
- 宿主侧 netstat 实测（11-13 12:0x）：指向 5432 的 ESTABLISHED 连接共 7 条，全部归属 PID 40492 = wslrelay.exe（WSL 虚拟网卡转发进程），即此刻活着持有连接的是 WSL 侧的 DB_Monitor 开发实例。

### 2.4 连接池占用读数（两次只读实测）

- 11-13 11:1x：total_backends 24，client backends 按状态 idle 16 / active 1 / idle in transaction 1；按 application_name：db_monitor 9、无名 5+4、TimescaleDB 后台 4+1、psql 1。
- 11-13 11:58:08（scripts/pg_pool_audit.py 首轮巡检）：total_backends 17/100 = 17.0%；client backends 8（idle 7、active 1）；application_name：db_monitor 4、(none) 3、psql 1；最老 client backend 的 backend_start 为 2026-11-13 02:27:12。
- 结论：当前连接池未饱和，本轮无占满事件可处置；§20 记录的「100/100 占满」是 11-13 08:35 宿主重启后由 DB_Monitor 开发实例造成（DB-005R §20 与 STATUS v1.45 已记录事实），本轮未复现、未重启任何进程。

## 三、根因判定

1. 共享面过大：两份同仓工作副本共用同一时序库容器与同一组库，连接与表都是共享的，任一侧的连接风暴都会打到对方的头上。
2. 限流机制在当前账号下无效：两份副本都用 superuser postgres 登录。PostgreSQL 的 rolconnlimit 对 superuser 不生效，因此「在数据库侧给 DB_Monitor 侧设连接池上限」若不先把该侧换成非 superuser 角色，就是空话——这是本次评估中最关键的一条技术前提。
3. 平台自身也有可堆积面：ORM 侧在非生产模式下 CONN_MAX_AGE=0，runserver 每请求新建短命线程且 close_request 不调用 connections.close_all()，DatabaseWrapper 引用环滞留 PostgreSQL 连接直至 Gen-2 GC 才释放（该机制由本仓 settings.py:241-244 自带注释记载），高并发下 ORM 连接可堆积；时序侧已被 TIMESCALEDB_POOL_MAX=16 限制住。
4. 组合结果：DB_Monitor 开发实例可在短时间内吃满 100/100，采集器、平台 Web、psql 一并被拒；平台自身 Web 高并发时同样可能成为占用方。

## 四、两条治本路径评估

### 4.1 路径 (a)：为 DB-AIOps 分配独立 Postgres 实例

- 改动点：compose 增 services.timescaledb-aiops（新容器 + 新命名卷 + 独立端口 + 专用非 superuser 角色 + 调高 max_connections）；pg_dump/pg_restore 迁移 db_monitor（560 MB / 105 表）与 timeseriesdb（1205 MB / 4 张超表与连续聚合）；平台连接指向切换（POSTGRES_HOST/PORT/NAME/USER/PASSWORD 与 TIMESCALEDB_HOST/PORT/NAME/USER/PASSWORD）；重建连续聚合、保留策略、压缩策略；重启采集器与 Web。
- 风险：迁移量 1.77 GB；为一致性须停采集，直接打断 D2 判定窗（11-12/13/14 三日 15:00 桶）；TimescaleDB 对连续聚合与策略的 pg_dump/restore 保真度本轮未实测（缺口 G-1）；切面大，回退需处理切换窗口内的数据分叉。
- 回退：连接指向切回旧容器（旧数据仍在原卷），新容器保留供事后比对；但切换窗口内写入新实例的数据需人工合并。
- 按任务单要求：本路径实施前须先报黑板请 PM 确认。

### 4.2 路径 (b)：调高 max_connections + 两侧连接池上限

- 改动点：
  1) compose 给 timescaledb 服务加 command（postgres -c max_connections=200）→ 需重启 postmaster 生效；命名卷 timeseries_data 持久，数据面不动。
  2) 建非 superuser 角色 dbmonitor_app（LOGIN、CONNECTION LIMIT 30），授权 db_monitor 与 timeseriesdb 所需对象（105 张表 + 超表 + 序列 + 扩展函数）。
  3) DB_Monitor 开发副本改用该角色（宿主侧环境变量切换，不在本仓、不入本仓提交）。
  4) 平台侧 application_name 改为可配置（新增 DB_APPLICATION_NAME，默认仍为 db_monitor），便于日后逐副本归属连接。
- 风险：postmaster 重启会断掉全部连接，窗口约 5–15 秒（须避开 15:00 桶时段）；非 superuser 角色的授权若不全会直接报权限错；第 3 步需重启 DB_Monitor 开发副本（该副本在本仓之外，属用户工程）。
- 回退：compose 还原并重启容器、角色切回 postgres、application_name 改回；全程无数据面动作，回退无数据损失。

### 4.3 补充路径 (a′)：给 DB_Monitor 开发实例单独实例、DB-AIOps 原地不动

- 改动点：为 DB_Monitor 开发副本起一个独立 Postgres 实例并改其指向；DB-AIOps 的容器、库、数据、连接指向全部不动。
- 隔离效果与 (a) 等价（两个工程彻底分开），但迁移量为零 —— 因为 DB_Monitor 是开发副本、其库内数据可弃；而 DB-AIOps 侧的 1.77 GB（D2 判定所依赖的数据）原地不动。
- 代价：动的是用户自建工程（C:\DB_Monitor）而非本仓，需用户知会；DB_Monitor 侧历史开发数据需重新初始化（migrate + 种子）。

## 五、推荐路径与理由

**首推 (b)，二阶段做 (a′)。**

理由：

1. 不动数据即可治本。(b) 的限流点放在数据库侧、由 PostgreSQL 强制：DB_Monitor 侧换成非 superuser 角色后，其连接数被 CONNECTION LIMIT 硬性封顶，代码再怎么泄漏也占不满池；平台侧因此恒有可用连接余量。这修的是「为什么会占满」，而不是把天花板抬高了事。
2. D2 判定窗零风险。(b) 不迁移任何数据、不需要停采集，唯一的动作是一次 postmaster 重启（5–15 秒），安排在非 15:00 桶时段即可；而 (a) 要搬走的正是 D2 正在判定的 sql_stat 15:00 桶，为一致性还得停采集。
3. 实施权责清晰。任务单明文「调高 max_connections 路径可按本单直接实施」，(b) 无需先行报 PM 确认；涉及本仓之外的 DB_Monitor 副本改动，列入 G-5 待 muse 裁决。
4. (a) 的成本与风险最高、收益与 (a′) 等价：同为彻底隔离，(a) 要迁移 1.77 GB 并打断判定窗，(a′) 迁移量为零。因此二阶段隔离选 (a′) 更划算；若用户坚持「必须是 DB-AIOps 独占实例」的口径，则 (a) 作为终局方案保留，但应排在 D2 与 (b) 之后，并单独请 PM 确认。

## 六、实施步骤（D2 复判 11-15 09:50 之后执行，本轮未执行）

| 步 | 动作 | 影响面 | 回退 |
|---|---|---|---|
| S0 | 备份：docker inspect 与 compose 文件留档；ALTER SYSTEM RESET ALL；pg_dumpall --globals-only 导出角色 | 无 | 直接丢弃备份 |
| S1 | compose 给 timescaledb 加 max_connections=200；docker compose up -d timescaledb；等 healthcheck | 容器重启，全部连接断 5–15 秒 | 还原 compose 并再次 up |
| S2 | 建角色 dbmonitor_app（NOSUPERUSER、CONNECTION LIMIT 30）+ 授权 db_monitor/timeseriesdb | 仅新对象 | DROP ROLE |
| S3 | DB_Monitor 开发副本改用该角色并重启该副本 | 用户工程，其 dev 连接断开重连 | 环境变量切回 postgres 并重启 |
| S4 | 平台侧 application_name 可配置化补丁 + 重启 Web 与采集器 | 采集写入短暂中断 | 改回并重启 |
| S5 | 按第七节方法验证 | — | — |

实施窗口建议：11-15 09:50 D2 出结果之后、且避开 15:00–15:10 桶写入时段。另需注意：2026-11-13 11:54 用户对「DB_Monitor 开发实例压满连接池」曾选定「④暂不处置（只观察、不关开发实例、不重启 Postgres 容器、不改 max_connections）」（见提交 6b4d8ec 记录的 DB-005R §20 续记）。因此 S1 的 postmaster 重启与 S3 的开发副本重启均属「需用户重新授权」动作，本单不自行执行；实施前须先经 muse 确认用户口径已变更（治本路径的授权已由 DB-026 下达给出，但重启类动作仍按单约束排在 D2 之后）。

## 七、实施后验证方法（已备好，D2 后按此逐项执行）

1. 连接池：scripts/pg_pool_audit.py 连续 3 轮读数，占用率 < 80%，alert=false。
2. 占用方：受控验证 —— 在 DB_Monitor 开发副本侧制造并发（短时多次访问），观察其在库连接数不超过 CONNECTION LIMIT 30，而平台侧连接数不受影响。
3. 采集器与 Web 在跑：采集器 PID 与 Web PID 存在、/api/health/ 返回 200；采集器与 Web 在实施前后 PID 变化须在报告中逐条登记。
4. sql_stat 持续续写：查 sql_stat 的 max(time) 较实施时点前推，且 11-15 之后各 15:00 桶按驱动脚本落桶。
5. 采集连续性：采样间隙 > 180 秒的计数为 0（沿用 G-11 既有口径）。
6. 配置复核：show max_connections = 200；select rolname, rolsuper, rolconnlimit from pg_roles where rolname='dbmonitor_app'。

## 八、D2 之前「只监不擅动」的落实

- 已新增只读巡检脚本 scripts/pg_pool_audit.py：全部查询为 SELECT/SHOW，连接一律走 docker exec 容器内 psql 本地 socket，不读 .env、不落任何口令；宿主侧用 netstat 统计 ESTABLISHED 归属 PID。阈值占用率 80%，超阈值退出码 3，仅告警、不做任何处置。
- 首日读数已落库：docs/pm/reports/evidence/DB-026-pool-audit-20261113.json（11-13 11:58:08，17/100 = 17.0%，未达阈值）。
- 巡检节奏：11-14、11-15 各至少 1 次读数并落 evidence，D2 复判前每轮在黑板留痕。
- 若期间连接池再次占满：只报黑板（任务单跟进 + docs/pm/reports/DB-026-*.md），不重启时序库容器、采集器、Web，也不擅自终止任何进程，等用户处置。
- 本轮未重启任何进程，时序库容器启动时间与 11-13 宿主重启后一致，未发生 postmaster 重启。

## 九、缺口与待确认项

- G-1：TimescaleDB 连续聚合（metric_hourly / metric_daily / session_ash_1m）与保留/压缩策略在 pg_dump/restore 下的保真度本轮未实测 —— 影响路径 (a) 的成本与风险估算，不影响首推 (b)。
- G-2：两份副本的 application_name 同为 db_monitor，现有 pg_stat_activity 无法逐副本归属连接；已备「application_name 可配置化」补丁（第 S4 步），需重启 Web 与采集器才生效，本轮未实施（避免在 D2 窗口内动进程）。
- G-3：非 superuser 角色 dbmonitor_app 的授权完整性未实测（105 张表 + 超表 + 序列 + 扩展函数），待 S2 之后受控验证。
- G-4：scripts/validate.sh unit 在本机无法整体跑通，两处均为环境口径、非仓库缺陷：①工作区 CRLF 行尾使 bash 解析报 set: pipefail invalid option name（与 SYNC14 记录的 G-7 同源）；②本机 bash 走 WSL，validate.sh 会激活 Windows 版 .venv/Scripts/activate，导致 python 解析为 Windows python.exe、无法解析 /mnt/c/... 路径，红线扫描阶段假失败。本轮改以 Windows 解释器直跑 validate.sh unit 的等价两个阶段，结果见下节。
- G-5（待 muse 裁决）：DB_Monitor 开发副本位于 C:\DB_Monitor，不在本仓，其连接指向/角色切换/重启是否获准 —— 倾向 D2 之后实施并由用户知会；口径不自行认定，经 MMMan 转 muse。

## 十、本轮改动与验证记录

- 本轮仅两处改动，均不涉及业务逻辑与运行期配置：
  1. scripts/pg_pool_audit.py（新增，只读巡检脚本）；
  2. docs/pm/reports/DB-026-receipt-m-team.md、docs/pm/reports/DB-026-report-m-team.md、docs/pm/reports/evidence/DB-026-pool-audit-20261113.json（本报告、回执与证据）。
- 未改动 docs/pm/STATUS.md、docs/pm/DECISIONS.md；未改动任何业务代码；未新增配置项（故不涉及 monitor/appconf.py SPECS 登记）。
- validate.sh unit 等价两阶段（因 G-4 所述环境口径无法整体调用脚本，改用 Windows 解释器直跑同两阶段，语义等价）：红线扫描 scripts/lint_redlines.py exit 0（输出「红线约束扫描通过」）；unit 测试 DJANGO_SETTINGS_MODULE=dbmonitor.settings_test_unit python manage.py test monitor --tag unit exit 0，Ran 434 tests。该结果对应提交时点 main 的 HEAD，非引用历史结论。
- 证据份数：1 份（DB-026-pool-audit-20261113.json）。

## 版本记录

| 版本 | 日期 | 修订人 | 修订说明 |
|---|---|---|---|
| v1.0 | 2026-11-13 | MMMan | DB-026 评估版：两条路径评估、推荐 (b) 主路径 + (a′) 二阶段、实施与回退步骤、D2 前只读巡检与首日读数；结论有条件通过 |
