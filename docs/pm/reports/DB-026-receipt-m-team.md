# DB-026 接单回执（M-team / MMMan）

- 单号：DB-026（连接池治本——独立实例或调高 max_connections）
- 执行：MMMan
- 接单时间：2026-11-13 11:54（回执写入 2026-11-13 12:0x）
- 仓库：smudy-linsang/DB-AIOps，分支 main，接单时 HEAD = 6eec91f（已 git pull --ff-only 同步）
- 交付物：
  1. 本单评估与实施报告 docs/pm/reports/DB-026-report-m-team.md（v1.0 评估版，第一行结论「有条件通过」）
  2. 只读连接池巡检脚本 scripts/pg_pool_audit.py + 首日读数 docs/pm/reports/evidence/DB-026-pool-audit-20261113.json
- 预计交付时间：
  - 评估部分（D2 前）：2026-11-13 当日交付 —— 已具备交付条件（v1.0 已交，v1.1 订正版同日交付）
  - 实施部分：D2 复判（11-15 09:50）之后首个可动窗口内执行，实施后补终版报告（v2.0）

## 接单口径确认

1. D2（11-15 09:50）之前只做评估与准备（写配置、备脚本、备回退），不动库、不重启时序库容器、采集器、Web —— 本单遵守。
2. D2 之前连接池若再次占满，只报黑板，不擅自重启任何进程，等用户处置。
3. 推荐路径若为独立实例（涉数据迁移），实施前先报黑板请 PM 确认 —— 本单据此把「独立实例」相关实施动作全部推迟到 PM 确认之后。
4. 不改 docs/pm/STATUS.md 与 docs/pm/DECISIONS.md。
5. 凭据不落明文：巡检脚本只走容器内 psql 本地 socket，不读 .env、不打印口令。

## 评估阶段已取得的关键实测事实（详见报告）

- 时序库容器 db-aiops-timescaledb 实测 max_connections=100、superuser_reserved_connections=3。
- 库清单与体量（12:3x 复测）：timeseriesdb 1210 MB、db_monitor 561 MB（public 对象 105）、postgres 9351 kB、template0/template1；test_db_monitor 已删除（v1.0 回执误列，见报告 §〇 D-4）。
- 行数：metric_point 2,171,816；sql_stat 269,622；collection_snapshot 136,348；session_sample 0。
- 占用方定位（订正）：DB_Monitor 开发副本与本仓共用同一容器与同一组库；宿主侧 netstat **分侧**实测：客户端侧（远端＝5432，真正占用方）＝采集器链 43592×7、平台 Web 链 47216×2、DB_Monitor 开发副本 39072×1；服务端侧 40492＝wslrelay.exe×10 与监听属主 36736＝com.docker.backend.exe 皆非占用方（v1.0 回执此处表述不据实，见报告 §〇 D-1）。
- 巡检读数（订正口径）：v1 首轮 11:58:08 的 17/100＝17.0% 系裸 count(*) 含后台进程所致，真实 client 口径为 8.26%；v2 脚本 12:4x 读数＝client backends 11／可用槽 97＝11.34%，未达 80% 告警阈值。证据已订正并保留 v1 原值供追溯。

## b1 实施回执（2026-11-18 20:2x–20:4x CST）

### 一、授权与范围

- 授权来源：Mr.Linsang 11-18 20:2x 人工转达授权实施 DB-026 b1；依据＝D2 已出结果（YES）、G-5 获准（仅限 b1 的 S3）、平台侧按采纳 S-2 与 b1 同批。
- 本次实施内容：建非 superuser 角色 dbmonitor_app（CONNECTION LIMIT 30）与 dbaiops_app（CONNECTION LIMIT 60）＋ 两库授权（S-3 清单、覆盖 dev 副本 migrate 能力）＋ 两侧同批切角色 ＋ 重启 Web 与采集器使其生效。
- 明确不做：b2（max_connections 调至 200）、compose 改动、不改 settings.py 默认值、不执行 migrate。

### 二、实施前只读基线（scripts/db026_b1.ps1 -Mode Verify，exit 0）

| 项 | 读数 |
|---|---|
| dbmonitor_app / dbaiops_app | 0 行（两角色均不存在 ⇒ 至今未动库） |
| max_connections / superuser_reserved / client_usable_slots | 100 / 3 / 97 |
| client backends | 16，**全部为 postgres**（⇒ 两侧客户端身份均仍为 superuser） |
| metric_point | 3,817,374 行，max(time)=2026-11-18 12:31:50Z，lag 3.2s（采集在线） |
| 表属主 | db_monitor.public 102 张、timeseriesdb.public 4 张，均为 postgres |

### 三、★实施物料两处缺陷（首次真实执行才暴露）

- 缺陷一（阻断级）：scripts/db026_b1_apply.sql 原 L72／L87 的 `GRANT ALL PRIVILEGES ON ALL VIEWS IN SCHEMA public TO ...` —— PostgreSQL 无该语法（PG16 报 syntax error at or near "VIEWS"）⇒ 首次 Apply 在 [3/4] 的 db_monitor 段中断：角色已建、db_monitor 的 schema/表/序列授权已下，而 **timeseriesdb 段未执行**。处置：两处改为注释（视图与物化视图本属 relation，已含于上一行 ALL TABLES 之内）。
- 缺陷二（使脚本 exit≠0）：末尾 G-3 残余项诊断查询用 `pg_tables.table_schema`（PG 实为 `schemaname`）⇒ 第二次 Apply 在 [4/4] 诊断查询报错（此前的建角色与全部授权均已执行完毕）。处置：改为 `schemaname AS table_schema`。
- 两次修改后均按 AGENTS.md 立即执行 `scripts/validate.sh unit --json` ⇒ exit 0（红线扫描 pass、unit 测试 pass），再重跑 Apply。
- 据实登记：两处缺陷属「D2 之前只做评估与准备（备脚本、备回退）」阶段从未被真实执行过的物料缺陷，本次首次真实执行方暴露；已随本回执修复入库。

### 四、Apply 终态（-Mode Apply -Confirm，exit 0）

- dbmonitor_app：NOSUPERUSER／LOGIN／NOCREATEDB／NOCREATEROLE／INHERIT／CONNECTION LIMIT 30；dbaiops_app：同上，CONNECTION LIMIT 60。
- 库级 CONNECT：db_monitor、timeseriesdb 两库对两个角色均已授。
- schema 级：两库 public 的 USAGE+CREATE、表/序列/函数全量授权；timeseriesdb 的 _timescaledb_internal USAGE（条件块已执行）。
- 实施后校验查询与 G-3 残余项查询均正常返回。

### 五、身份切换落点（据实说明）

- 两侧落点＝**各自仓根 dotenv**：平台 C:\DB-AIOps\.env、副本 C:\DB_Monitor\.env。依据＝dbmonitor/settings.py 的 `_load_dotenv()`（读 BASE_DIR/.env，以 os.environ.setdefault 注入、不覆盖真实环境变量）与 L237／L265（默认 postgres，本次未改默认值）。
- Mr.Linsang 口令中「C:\DB-AIOps.env」「C:\DB_Monitor.env」按**各仓仓根 .env** 理解并实施；若按 C:\ 根目录字面文件理解，则 settings.py 不会读取该文件、身份不会切换（据实并列说明，未代裁）。
- 两个 .env 均按 UTF-8 无 BOM 写入（若带 BOM，首行键名会带 BOM 前缀而失效）。
- 口令只写入该两个文件、不入仓：两侧 .gitignore L35–L37 覆盖 .env，`git check-ignore -v .env` 两侧均命中；全流程未回显任何口令明文。

### 六、同批切与重启

- 20:36:11 一批内停两侧进程：平台 Web 47216/45704、平台采集器 43592/48396、副本 Web 39072/47884；停后端口 8000／8009 均确认释放。
- 同批拉起：平台 Web（.venv pythonw，cwd=C:\DB-AIOps，日志沿用 web_resident_20261111.log/.err）；副本 Web（C:\DB_Monitor\.venv python.exe -u，cwd=C:\DB_Monitor，日志新建 dbmonitor_web_resident_20261118.log/.err）。
- ★采集器：我方同批拉起的实例在 20:36:2x 被拒（LeaseUnavailable：collector/global 已被其他实例持有）—— 进程租约（process_lease 表，TTL 30s／续租 10s）仍被刚被杀实例持有（其最后心跳 20:36:10、到期 20:36:40）⇒ **20:37:10 由既有看护任务 MMMan-DBAIOps-CollectorWatchdog 拉起**（pid 55624/57324，看护日志「collector absent -> launched pid=55624」→「collector up after launch pids=[55624, 57324]」）⇒ 20:37:21 取得租约（fencing_token 8）。本轮未改动任何看护任务与排程。
- ★副本 Web：原进程父进程在停机前已消失、无自动拉起者（据他方既有记载，其由 Antigravity IDE language server 经 WSL 启动）⇒ 本会话同批手工拉起（命令与工作目录同款）；副作用＝该副本 Web 的生命周期不再由 IDE language server 托管。

### 七、验证读数（读时点 2026-11-18 20:35–20:44 CST）

1. **角色登录实测**（TCP ＋ 口令认证，4 组全 OK；脚本 C:\dispatcher\_db026_b1_verify_20261118.py，口令只从 .env 读、不打印）：平台 dbaiops_app → db_monitor／timeseriesdb（current_user＝session_user＝dbaiops_app，rolsuper＝false，rolconnlimit＝60）；副本 dbmonitor_app → db_monitor／timeseriesdb（rolsuper＝false，rolconnlimit＝30）。
2. **授权实测**：metric_point 与 django_migrations 的 SELECT/INSERT/UPDATE/DELETE 权限位全为 true；django_migrations_id_seq 的 USAGE 为 true；★**S-3 migrate 能力实测＝两库 public schema 建表 OK**（DDL 在事务内执行并回滚，无残留对象）。
3. **服务端身份实测**（pg_stat_activity）：观测到 dbaiops_app（平台采集器与 Web）与 dbmonitor_app（副本 Web）连接；★**应用侧已无 superuser 连接**（postgres 仅剩本次测量用 psql 自身 1 条）。副本侧 ORM 直读＝COPY_EFFECTIVE_DB_USER=dbmonitor_app，平台侧＝PLATFORM_EFFECTIVE_DB_USER=dbaiops_app。
4. **★限额强制生效实测**：并发占位至第 30 个会话时返回 `FATAL: too many connections for role "dbmonitor_app"`，且 global_max_connections_hit＝false ⇒ 触发的是**角色限额（30）**而非全局上限（100）；测毕全部关闭，槽位已归还。
5. **健康检查**：平台 8009 /api/health/ ＝ HTTP 200；副本 8000 /api/health/ ＝ HTTP 200。
6. **采集连续性（断档基线）**：重启前最后写入 2026-11-18 12:36:05.56Z（20:36:05），重启后首写 12:37:39.98Z ⇒ ★**数据断档 1 分 34.4 秒**；12:39–12:40 两分钟无写入（采集器重启后首轮全量采集未完成），12:41 起恢复（51 条/分）、12:42 为 164 条/分，20:43 复核 lag ＝ 19 秒 ⇒ 连续性已恢复。
7. **采集器日志**：20:37 之后无任何权限类错误（permission denied／insufficient privilege 均 0 处），基线与容量引擎正常落库（monitor_baselinemodel n_tup_upd 146 万余次、process_lease 续租 46160 次 ⇒ 非 superuser 角色可正常 UPDATE 租约与业务表）。

### 八、残余项（据实登记，不掩盖）

- ★**G-3 属主残余**：timeseriesdb.public 的 4 张表（collection_snapshot、metric_point、session_sample、sql_stat）与 db_monitor.public 的 102 张表仍由 postgres 拥有 ⇒ 非 superuser 角色对既有表不能 ALTER、不能改属主；TimescaleDB 的 create_hypertable／add_retention_policy／add_compression_policy 需对象属主权限 ⇒ dev 副本首次建超表仍须由 postgres 执行一次，或按需把目标对象属主转给该角色。
- ★**平台侧切非 superuser 后，后续 Django migrate 若需 ALTER 既有表（postgres 拥有）将报权限不足**；本次未执行任何 migrate（不在本次范围）。
- ★副本 Web 生命周期改由本会话手工拉起（见第六节），后续重启须手工或另设看护。
- 容器内临时副本 /tmp/db026_b1_apply.sql 残留一份（**不含任何口令**），执行脚本自带的 finally 清理未生效，待清理。
- **compose 未动**（属 b2 范围）：两份 compose 的 timescaledb 服务定义逐字相同（container_name db-aiops-timescaledb、5432:5432 同端口）⇒ 本次全程未在本仓目录执行任何 compose 命令；本次容器动作只有 docker exec／docker cp（不触发 compose 项目解析）。

### 九、未做事项（边界声明）

b2（max_connections 调至 200）未做；compose 改动未做；未改 settings.py 默认值；未执行 migrate；未改任何他人提交与文件（工作区他方未跟踪证据文件未触碰）；未新建、未修改、未删除任何排程或看护任务（本轮仅被动使用既有看护任务）。

### 十、回退路径

- 轻量回退：撤两侧 .env 的两行 → 重启 Web 与采集器 ⇒ 回落 postgres。
- 完整回退：`scripts/db026_b1.ps1 -Mode Rollback -Confirm`（REASSIGN／DROP OWNED ＋ DROP ROLE，不动既有表与数据）⇒ 回退后同样须切回 postgres 并重启。

### 十一、落板与转呈

- 本回执（v1.4）与实施物料缺陷修复已入库；报告 DB-026-report-m-team.md 首行档位维持「通过」（据 STATUS v1.50 的 PM 裁决，本次未改档位）。
- 群内已回执（转呈件：DB-026 b1 实施完成通报）。

## 版本记录

| 版本 | 日期 | 修订人 | 修订说明 |
|---|---|---|---|
| v1.0 | 2026-11-13 | MMMan | DB-026 接单回执与 ETA 登记 |
| v1.1 | 2026-11-13 | MMMan | 据 DB-026 独立复核（commit 2d6d6c0）订正本回执中的占用方归因、库清单与巡检读数口径三处表述（详见报告 §〇 D-1/D-3/D-4）；结论与 ETA 不变 |
| v1.2 | 2026-11-13 | MMMan | 据复核方 13:1x 终态回签（评估面闭环、条件已满足）作两项据实并列订正并同步报告升 v1.2（§〇 D-3 裸口径按读时点拆开、v1 首轮 client 口径 8.25%、§一 补用户决策两粒度并列口径）；结论档位与 ETA 均不变 |
| v1.3 | 2026-11-13 | MMMan | 据 STATUS v1.47（commit 53a2900）同步报告基线更新（G-5 获准限 b1 的 S3、S-2 平台侧同批、b2 暂缓、用户 12:31 确认治本照做 D2 后实施），并补平台侧生效前提；实施仍排在 11-15 09:50 D2 之后，今日不动库；结论档位与 ETA 均不变 |
| v1.4 | 2026-11-18 | MMMan | b1 治本实施完成（Mr.Linsang 11-18 20:2x 人工转达授权）：建非 superuser 角色 dbmonitor_app(LIMIT 30) 与 dbaiops_app(LIMIT 60) ＋ 两库授权（S-3 清单、覆盖 dev 副本 migrate，实测建表 OK）＋ 两侧同批切角色 ＋ 重启 Web 与采集器使其生效；实施中发现并修复实施物料两处 PG16 语法缺陷（ALL VIEWS 阻断、pg_tables 列名），两次修改后 validate.sh unit 均 exit 0；含完整验证读数（身份普查：应用侧零 superuser 连接；限额强制生效实测：FATAL too many connections for role dbmonitor_app）、断档基线（数据断档 1 分 34.4 秒，12:41 起恢复）与残余项登记（G-3 属主、平台侧后续 migrate ALTER 限制、副本 Web 生命周期、容器临时副本残留）；b2 与 compose 改动仍未实施 |
| v1.5 | 2026-11-18 | MMMan | b1 实施回执的两处据实订正与收尾（★订正一：容器内临时副本 /tmp/db026_b1_apply.sql 已于 20:47 清理完毕，v1.4 第八项所记「待清理」就此闭合，原行保留不改；★订正二：v1.4 第七节第 6 项中「12:39–12:40 两分钟无写入（采集器重启后首轮全量采集未完成）」的括号归因属**未逐项确证**，据实改为「与采集周期节奏一致（monitor_job 周期 > 60 秒，采集器日志可见 APScheduler「maximum number of running instances reached (1)」的跳过记录），具体成因未逐项确证」）；另登记群内转呈件 seq 94（id msg_task_fa2ef25fa832）与终态读数（读时点 20:47：身份 dbaiops_app 11／dbmonitor_app 1／postgres 1（测量用 psql 自身）、两侧 /api/health/ 均 200、metric_point lag 9 秒）；★判项、结论与档位不变 |
