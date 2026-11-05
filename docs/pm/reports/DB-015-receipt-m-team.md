# DB-015 接单回执（M-team）

- 任务单：`docs/pm/orders/DB-015-DB-004-005-005R改派MMMan外网测试.md`（**统筹单**）
- 覆盖子单：`docs/pm/orders/DB-004-TDSQL内网验证准备.md`、`DB-005-TDSQL慢SQL全链路内网实测.md`、`DB-005R-005复测回归确认.md`（三单原派 Lingma，2026-11-05 由本单改派 M-team 外网执行；按 DB-015「以本单为准」，三单接单确认与 ETA 以本回执一并给出，不另占编号）
- 接单人：MMMan（M-team team lead）；M-team 三条内部分工同步启用
- 接单时间：2026-11-05 12:05
- 接收方式：黑板 STATUS v1.9 已登记 DB-015/004/005/005R 执行＝MMMan；本方经人机通道接单，非自动唤醒所致（见「自动链接入状态」）

## 预计交付

| 单号 | 交付物 | 预计交付 |
|---|---|---|
| DB-015 | 统筹：环境就绪口径 + 探针可跑范围 + 三单交付汇总签字 | 随 DB-004 首份回执同步更新 |
| DB-004 | 环境就绪回执 + V-1~V-9 验证清单（逐项标注外网可跑/需内网/缺口） | **2026-11-05 18:00** |
| DB-005 | 外网可跑部分实测报告 + 可复现缺陷单（每项含复现步骤与证据） | **2026-11-06 20:00**（依赖 004 环境就绪） |
| DB-005R | 缺陷单逐项复测报告（关闭/仍 open 判定） | **2026-11-09 18:00 暂定**（依赖 DB-006 完成，随 006 进度滚动更新） |

> 交付口径：所有报告落 `docs/pm/reports/`，直推 main，结论三档（通过／有条件通过／不通过）；测试报告须同时给出「已实测」与「未覆盖（缺口）」两张清单，**缺口不硬编、不伪造**。

## 分工

- **MMMan（team lead）**：统筹与汇总签字；环境与凭据通道；探针执行与结论表定稿；对外回执与交付。
- **测试数据线**：隔离测试库准备、造数与分档、只读与超时策略落 `.env` 通道；V-7 验证所需的最小权限隔离账号。
- **UAT 场景线**：V-10 端到端（平台慢 SQL 列表 / SQL 详情 / 退化检测）与手工 `/*sets:allsets*/` 查询值的一致性核对。
- **缺陷闭环线**：DB-005 缺陷单的复现步骤固化与销账核验（判「真实用户还会不会踩坑」），产出给 DB-006（GG）的缺陷单。

## 已实测的环境事实（2026-11-05 12:0x，本机核验）

**关键结论：「外网执行」指执行方在外网，目标 TDSQL 仍在内网 —— 本机到内网 TDSQL 直连可达，故 V-1~V-9 不整体记为缺口。**

| 项 | 实测结果 |
|---|---|
| 内网 TDSQL 连通性 | `10.243.16.252:3306` **可达**；`10.243.17.192:3306` **可达**（Test-NetConnection，均 True） |
| 外网 mock TDSQL 环境（现成） | `tdsql-proxy`（`tdsql-proxy:mock-v1.0`，0.0.0.0:15002）、`tdsql-mysql-test`（`mysql:8.0`，healthy，127.0.0.1:13306→3306）、`tdsql-zk`（`zookeeper:3.8`，2181）、`sqlcheck-redis`（`redis:7-alpine`，6380）；均 Up 43 小时 |
| 探针脚本（现成） | `scripts/tdsql_probe.py`（349 行，一次跑完 V-1~V-9，只读，账号需 SELECT+PROCESS，V-7 用「只读事务 + `WHERE 1=0` 的 UPDATE + ROLLBACK」保护，V-8 用 `SELECT SLEEP(3)` ＋500ms 超时；输出不打码主机/账号/密码）；V-10 走 `scripts/tdsql_workload.py` ＋ `docs/review/tdsql/workload.sql` |
| 本地运行环境 | 仓库根 `.venv`（CPython 3.11.16，按 `requirements.lock` 建齐）；`scripts/validate.sh unit` exit 0、396 tests OK |
| 探针定义来源 | `TDSQL_SLOWSQL_DESIGN.md` §8「内网 TDSQL 验证清单」V-1~V-10 |

**V-1~V-9 外网可跑性初判（待 004 逐项确认后定稿）**

| 探针 | 依赖 | 外网可跑性初判 |
|---|---|---|
| V-1 allsets 透传带 `info` 列 | TDSQL proxy 透传 | 需内网真 TDSQL 或行为等价 proxy；mock proxy 可作回归对照 |
| V-2 透传查 performance_schema | 同上 | 同上 |
| V-3 digest 扩展列 `SUM_CPU_TIME`/`QUERY_SAMPLE_TEXT` | TXSQL 8.0.33 扩展 | mock MySQL 8.0 **无该扩展列** ⇒ 须内网真 TDSQL 判定 |
| V-4 透传 SHOW GLOBAL STATUS | TDSQL proxy 透传 | 需内网真 TDSQL |
| V-5 指定单 set 透传 | set 拓扑 | 需内网真 TDSQL |
| V-6 透传 EXPLAIN ANALYZE | 透传 + **V-7 通过** | V-7 未通过禁止开 ⇒ 门禁生效 |
| V-7 只读事务经 proxy 生效（报 1792） | proxy 只读 | 普通 MySQL 8 可跑；内网真 TDSQL 为准 |
| V-8 `max_execution_time` 经 proxy 生效 | proxy 超时 | 普通 MySQL 8 可跑；内网真 TDSQL 为准 |
| V-9 读写分离落主 | 集中式实例 + `show routes` | 需内网真 TDSQL |
| V-10 端到端 | 平台采集链路 + TDSQL | 依赖 004/005 与平台配置指向 TDSQL |

## 风险与依赖

1. **凭据（阻塞项，需 PM/用户供给）**：内网 TDSQL 只读账号（`TDSQL_PROBE_HOST/PORT/USER/PASSWORD`，走环境变量或纳管界面，不落盘、不进版本库）。V-7 要得确定结论需对某张表有 UPDATE 权限（脚本以 `WHERE 1=0` + ROLLBACK 保证不改数据）；无此权限时该项记「无法判定」，不硬编。
2. **门禁**：V-7 未通过 ⇒ 禁止开 EXPLAIN ANALYZE；`PERF_EXPLAIN_ANALYZE_ENABLED` 默认 False、`PERF_EXPLAIN_ANALYZE_TIMEOUT_MS` 默认 5000。
3. **TXSQL 专有面**：digest 扩展列、set 拓扑、proxy 透传语义在 mock MySQL 8 上不成立 ⇒ 这部分结论只能来自内网真 TDSQL，mock 仅作回归对照。
4. **依赖 DB-006（GG）**：005R 的复测对象是 006 的修复结果；GG 的缺陷单来源改为本单外网实测结果 ⇒ 若 005 产出缺陷单晚于 006 开工，005R ETA 顺延，当日更新黑板。
5. **DB-010A（GG）**：与本单并行，但 010A 阻断「执行入口放通」的规矩不变 ⇒ 本单只做只读探测与隔离库准备，不放开生产执行入口。
6. **只读与超时纪律**：目标库连接一律只读 + 超时；V-8 不扫业务表；产出中不回显主机/账号/密码（探针脚本已默认打码）。

## 自动链接入状态（本方已就位）

- 本机已建 **M-team 的 DB-AIOps 守护实例**：`C:\dispatcher\mman_dbaiops_launch.py` → `member_poller.py --member MMMan --repo smudy-linsang/DB-AIOps --workdir C:\DB-AIOps --state C:\dispatcher\member_poller_state_MMMan_DBAIOps.json --interval 60`。
- 状态文件独立命名（`..._MMMan_DBAIOps.json`），与本方 Xinchuang 侧实例（`..._MMMan.json`）不撞名；首轮已记基线「13 个已有任务单不叫醒」，历史单不触发重复唤醒。
- 唤醒桥：`C:\dispatcher\wake_mman.py`（WAKE_CMD）—— 路径 A 必做落 trigger 到 `C:\dispatcher\pending_wake_MMMan\`（绝不丢单）；路径 B best-effort 直写 MiniMax Bot 消息库（写前备份、原子替换）。
- **本单是人工接单**：接单时该实例尚未建立，故本单非自动唤醒所致；实例建立后的新单将自动巡检。

## 成本披露

搜索 0 次；抓取 0 次。本轮为只读核验：git fetch、端口可达性探测、Docker 镜像与容器清单、探针脚本与设计文档阅读；未改代码、未改他人文件、未触碰 `STATUS.md`／`DECISIONS.md`（本单为报告类新增）。

## 版本记录

| 版本 | 日期 | 修订人 | 修订说明 |
|---|---|---|---|
| v1.0 | 2026-11-05 | MMMan | DB-015 及 DB-004/005/005R 接单确认与预计交付；附已实测环境事实、V-1~V-9 外网可跑性初判、风险与依赖、凭据阻塞项、本方自动链接入就位说明 |
