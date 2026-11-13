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

## 版本记录

| 版本 | 日期 | 修订人 | 修订说明 |
|---|---|---|---|
| v1.0 | 2026-11-13 | MMMan | DB-026 接单回执与 ETA 登记 |
| v1.1 | 2026-11-13 | MMMan | 据 DB-026 独立复核（commit 2d6d6c0）订正本回执中的占用方归因、库清单与巡检读数口径三处表述（详见报告 §〇 D-1/D-3/D-4）；结论与 ETA 不变 |
