# DB-026 接单回执（M-team / MMMan）

- 单号：DB-026（连接池治本——独立实例或调高 max_connections）
- 执行：MMMan
- 接单时间：2026-11-13 11:54（回执写入 2026-11-13 12:0x）
- 仓库：smudy-linsang/DB-AIOps，分支 main，接单时 HEAD = 6eec91f（已 git pull --ff-only 同步）
- 交付物：
  1. 本单评估与实施报告 docs/pm/reports/DB-026-report-m-team.md（v1.0 评估版，第一行结论「有条件通过」）
  2. 只读连接池巡检脚本 scripts/pg_pool_audit.py + 首日读数 docs/pm/reports/evidence/DB-026-pool-audit-20261113.json
- 预计交付时间：
  - 评估部分（D2 前）：2026-11-13 当日交付 —— 已具备交付条件
  - 实施部分：D2 复判（11-15 09:50）之后首个可动窗口内执行，实施后补终版报告（v2.0）

## 接单口径确认

1. D2（11-15 09:50）之前只做评估与准备（写配置、备脚本、备回退），不动库、不重启时序库容器、采集器、Web —— 本单遵守。
2. D2 之前连接池若再次占满，只报黑板，不擅自重启任何进程，等用户处置。
3. 推荐路径若为独立实例（涉数据迁移），实施前先报黑板请 PM 确认 —— 本单据此把「独立实例」相关实施动作全部推迟到 PM 确认之后。
4. 不改 docs/pm/STATUS.md 与 docs/pm/DECISIONS.md。
5. 凭据不落明文：巡检脚本只走容器内 psql 本地 socket，不读 .env、不打印口令。

## 评估阶段已取得的关键实测事实（详见报告）

- 时序库容器 db-aiops-timescaledb 实测 max_connections=100、superuser_reserved_connections=3。
- 库清单与体量：timeseriesdb 1205 MB、db_monitor 560 MB（public 表 105 张）、test_db_monitor 16 MB、postgres 9351 kB。
- 行数：metric_point 2,171,816；sql_stat 269,622；collection_snapshot 136,348；session_sample 0。
- 占用方定位：DB_Monitor 开发实例与本仓共用同一容器与同一组库；宿主侧 netstat 实测 PID 40492 = wslrelay.exe 持 7 条到 5432 的 ESTABLISHED，即 WSL 侧实例为活着占用方。
- 11-13 11:58:08 巡检读数：17/100（17.0%），client backends 8（idle 7 / active 1），application_name db_monitor 4 / (none) 3 / psql 1，未达 80% 告警阈值。

## 版本记录

| 版本 | 日期 | 修订人 | 修订说明 |
|---|---|---|---|
| v1.0 | 2026-11-13 | MMMan | DB-026 接单回执与 ETA 登记 |
