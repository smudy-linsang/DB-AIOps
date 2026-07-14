# Phase 6C 破坏性演练手册

8 类 Playbook 全部完成**真实故障注入**的端到端闭环演练 (2026-07-14)。
每个演练验证完整链路: 注入 → 发现(≤60s) → 自动诊断(≤300s) → Playbook 处置 →
验证回路 → 自动 resolved(≤600s), 即 1-5-10 SLA。

## 前置条件

- docker 容器组在跑 (`docker-compose.dev.yml`): mysql/oracle/redis/timescaledb/es
- 常驻守护: `start_pipeline`(消费事件/诊断/验证) + `start_sentinel`(探活/ASH)
  ——启动前 `set -a; source .env; set +a` (否则实例密码解密失败)
- `start_monitor` 采集守护**无需**在跑: 演练进程自驱采集周期 (`_drill_common.collect_once`)
- 演练脚本自动加载 `.env`, 直接 `venv/bin/python phase6/drills/e2e_xxx.py` 即可

## 演练矩阵

| # | 脚本 | 类别/信号 | 注入方式 (全真实) | Playbook | 授权通道 | 结果 |
|---|------|----------|------------------|----------|---------|------|
| 1 | e2e_lock.py | lock/blocked_session | 行锁互斥会话 | PB-LOCK-KILL-BLOCKER | one_click | PASS (MTTR 7.8s) |
| 2 | e2e_conn.py | connection/conn_high | 真开 143 个空闲连接至 96% | PB-CONN-CLEAN-IDLE | one_click | PASS (MTTR 42.3s) |
| 3 | e2e_space.py | capacity/space_high | Oracle 50M 定容表空间灌至 98% | PB-SPACE-EXTEND | one_click | PASS (MTTR 20.8s) |
| 4 | e2e_repl.py | replication/repl_broken | 临时真主从 + STOP REPLICA | PB-REPL-RESTART | one_click | PASS (MTTR 40.1s) |
| 5 | e2e_config.py | config/config_drift | root 私改 sync_binlog | PB-CONFIG-ROLLBACK | one_click | PASS (MTTR 10.1s) |
| 6 | e2e_slow.py | performance/slow_surge | 12 条真实慢查询 + 长查询 | PB-SLOW-KILL-TOPSQL | one_click | PASS (MTTR 30.1s) |
| 7 | e2e_deadlock.py | lock/deadlock_surge | 交叉更新真实死锁 x4 | PB-DEADLOCK-ADVISE | **auto** (低风险) | PASS (建议类, 人确认) |
| 8 | e2e_down.py | availability/instance_down | 一次性实例 docker stop | PB-DOWN-GUIDE | **approved** (高风险) | PASS (MTTD 16.1s, 指引+人工恢复) |

三种授权通道 (auto/one_click/approved) 与熔断 (1h 内自动动作≥3 次转人工) 均被演练覆盖验证。

## 演练暴露并修复的产品缺陷 (7 项)

1. **L3 基线均值恒为空**: `_collect_baseline_means` 调用不存在的
   `calculate_baseline` 方法 → conn_storm/slow_surge 复合检测从未生效。
   已改用 `calculate_baseline_for_metric` 取当前时间槽 (每实例小时级缓存)。
2. **死锁计数恒为 0**: 原生 MySQL 8 无 `Innodb_deadlocks` 状态变量 →
   deadlock_surge 永远检测不到。已回退到 `INNODB_METRICS lock_deadlocks`。
3. **真从库被误判单机**: checker 解析 SHOW SLAVE STATUS 用 `[]` 硬取
   TXSQL 扩展列 (Slave_Parallel_Workers 等) → 原生 MySQL KeyError 整块
   回退 master_host=N/A → repl_broken 永远检测不到。已逐列 `.get()` 容错。
4. **config_drift 无检测器**: 只有映射没有产生方 → 配置类事故不可能出现。
   新增 `detectors/config_drift.py` (config_params 对比 Redis 参数基线快照,
   按参数分键; 预期变更走 `accept_config_snapshot`)。
5. **慢查询验证判据永假**: slow_queries 是累计计数器, `< 5` 永远不满足 →
   处置后永远验证超时。采集侧新增 `slow_queries_delta` 增量 (deadlock 同法),
   L3/验证均改用增量。
6. **Oracle 表空间假高水位**: used_pct 不感知 autoextend → SYSTEM 常年 98%+
   误报容量事故。已按可扩展上限 (maxbytes) 计算。
7. **验证回路取值缺陷**: repl_running/instance_up 用 MonitorLog.status 派生
   (复制断≠实例 DOWN); tablespace_used_pct 无对象级匹配; 无"处置后快照"
   新鲜度约束 (旧数据可误判恢复)。已重写 `_get_metric_value`
   (对象级 + since 时间栅栏 + live_config 实时读参数源)。

顺带的引擎增强: Playbook 步骤支持 `foreach_sql` 批量遍历执行 (批量 kill);
`params_schema` 默认值自动兜底; verify 判据支持 `{param}` 占位符渲染与
对象级验证目标传递。

## 注意事项

- 演练会触发真实告警通知与熔断计数 (`dbaiops:autoact:<config_id>`,
  连续跑多个演练后低风险 auto 会被熔断转人工, 属预期; 复验 6C-05 前
  可 `redis-cli DEL` 该键或等 1h 过期)。
- e2e_repl / e2e_down 自建自清临时容器 (db-aiops-mysql-replica / -down)
  与临时纳管配置, 中断后如有残留请手工 `docker rm -f` 并删配置。
- e2e_space 需要容器内 sysdba 做环境准备 (处置账号授 ALTER TABLESPACE 等,
  PDB 开 db_create_file_dest), 结束自动还原。
- TDSQL 实例 (id=6/7) 为共享测试库, **不做破坏性演练**。
