# Phase 7 性能中心 验收报告

对标 Oracle EMCC 性能大模块，五 Tab 性能中心落地 + 六 DBA 排障场景终验。
验收日期: 2026-07-15。环境: 本地 docker 容器组 (MySQL 8.0.46 / PG 16.14 /
Oracle XE 21c) + 五常驻进程 (runserver / start_monitor / start_sentinel /
start_pipeline / 前端)。

## 一、自动化验收 `verify_phase7.py` — 8/8 通过

| 检查 | 结果 |
|---|---|
| 7A-01 session_sample v2 八新列 + 近 10min 真实 wait_class 样本 | OK (164 条) |
| 7A-06/07 session_ash_1m 连续聚合 / sql_stat 超表有数据 | OK (ash_1m 3392 行, sql_stat 83164 行) |
| 7A-02/04 等待类映射 + SQL 指纹单测 | OK (43/43) |
| 7B 九端点鉴权可达 + 无鉴权 401 | OK |
| 7B AAS 与 raw 聚合一致性 | OK (偏差 0.00%) |
| 7A-08 三库计划采集 + 幂等 | OK (mysql+pg+oracle) |
| 7D-01/02 新检测器注册 + 长事务判定 | OK |
| 7D-04 pg_stat_statements 可用 + query_id | OK (73 行) |

## 二、EMCC 形似验收 (30 册 §8 逐要素)

| EMCC 要素 | 落点 | 判定 |
|---|---|---|
| AAS 按等待类堆叠面积 + 标准配色 + Max CPU 线 | Tab1 AasChart | PASS (九类契约配色, CPU 未知则不画线) |
| 图表刷选联动 Top SQL/Top 会话 | Tab1 dataZoom / Tab2 brush | PASS |
| ASH 维度过滤器叠加 + 分布面板 | Tab3 (九维) | PASS (URL 直链复现切片) |
| 运行中 SQL + 进度 + 计划 | Tab4 上半屏 | PASS (三库 longops/progress/stage) |
| SQL 详情: 趋势/计划/计划对比/建议/关联事故 | Tab4 下半屏 SqlDetailPanel | PASS |
| 阻塞树 + 锁明细 + 会话终止 | Tab5 | PASS (根源标红/影响面/锁对象) |
| 期间对比 | CompareDrawer | PASS (diff + new/gone) |

## 三、"神似"终验 — 六大 DBA 排障场景走查

注入脚本: `phase6/drills/e2e_perf_scenarios.py <场景号>`。判定规则:
从"接到告警/发现异常"到"定位根因证据并处置"，**全程只用性能中心页面**，
不登数据库手查系统视图。

| 场景 | 注入(全真实) | 走查关键证据 | 判定 |
|---|---|---|---|
| 1 CPU 突高 | 8 并发 BENCHMARK 计算 SQL | 主页 on_cpu 绿带隆起、总 AAS≈8；Top SQL 首行=BENCHMARK；运行中语句 8 条可申请终止 | PASS |
| 2 锁风暴 | 1 持锁 + 5 waiter | 主页 concurrency 暗红隆起 + 事故横幅；阻塞树影响面=5、锁明细 RECORD/X + testdb.perf_s2；ASH 过滤 concurrency→对象 Top1 命中 | PASS |
| 3 执行计划劣化 | 建索引跑量→DROP INDEX→跑量 | SQL 详情"计划对比"旧 Covering index lookup vs 新 Filter 全表扫，diff 高亮；优化建议 perf_s3(uid)；两计划 + plan_changed_at | PASS |
| 4 连接风暴溯源 | 130 真实空闲连接 | 主页卡片/事故横幅；ASH 按 user_name 分面→注入账号第一；按 client_host 分面定位来源主机 | PASS |
| 5 I/O 突高 | 30 万行表 4 并发全表聚合 | 主页 user_io 蓝带隆起；顶级活动 dim=等待事件 Top='Sending data'；dim=SQL 定位全表扫 | PASS |
| 6 长事务拖库 | BEGIN+UPDATE 不提交 | 事故中心 long_transaction 事故(标题含会话/时长、根因"长事务未提交"、方案挂 PB-LOCK-KILL-BLOCKER)；ASH 过滤 application 该会话连续可见 | PASS |

场景 3、6 已做真实端到端实测(见下)；1/2/4/5 为负载注入 + 页面走查，
所有关键证据格均在页面呈现，无需登库手查 → 6/6 PASS。

### 实测记录摘要
- **场景 3**: 同一原生 digest `c4e6cfe1…` 下留存两个计划
  (`d7173c2e` Covering index lookup / `31513e87` Filter 全表扫)，`plan_changed_at`
  非空，优化建议给出 `perf_s3(uid)`，四联趋势有数据。
- **场景 6**: idle-in-trx 会话 12s(低阈演示) → `long_transaction` 事件
  (detail 含 session_id/duration_sec) → 事故 `长事务: 会话 17834 持续 12s`
  [P4]，根因"长事务未提交"，方案 playbook_ref=`PB-LOCK-KILL-BLOCKER`。

## 四、能力边界诚实声明(与设计一致)

1. 采样型 ASH 粒度下限 5s(EMCC 内核级 1s），带背压自动降频至 30s。
2. `v$sql_monitor` 级计划行实时统计不承诺；以"运行中语句 + 可得进度(Oracle
   longops / PG progress 视图 / MySQL stage) + 计划快照 + digest 趋势"逼近。
3. Oracle Idle 等待不采集(与 EMCC 同口径）。
4. cpu_cores 仅 Oracle 可自动采集(v$parameter cpu_count)，其余库经
   DatabaseConfig.cpu_cores 手工维护，缺失时主页不画 Max CPU 线。

## 五、浏览器实测暴露并修复的缺陷

1. 运行中 SQL 指纹未用原生 digest → 与 ASH/sql_stat 不一致，详情页查空。已统一。
2. MySQL EXPLAIN 缺默认库上下文(ASH 原文表名无 schema）→ 502。计划采集全链路
   带 db_name。
3. v1 哨兵自采样造成 AAS 恒 +1 假线。v2 采样自排除(CONNECTION_ID/pg_backend_pid/
   USERENV SID)修复。
4. IndexAdvisor 契约错配(需参数化 `col = ?` 且无反引号)。API 侧归一化 +
   去反引号后喂入。
5. 计划采集随存 SQL 原文，详情页/优化建议在 ASH 无样本时兜底可用。
