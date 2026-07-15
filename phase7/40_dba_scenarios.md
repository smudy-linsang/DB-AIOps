# Phase 7 第四册：新检测器 + DBA 排障场景终验（"神似"的验收标准）

> 工单 7D-01 ~ 7D-05。本册回答"真正赋能 DBA"怎么验收：6 个典型排障场景,
> 每个都必须**只用本系统页面**完成"发现→定位→证据→处置"全程。
> 任一步需要 DBA 手工登库查系统视图 → 该场景 FAIL。

---

## 1. 7D-01 plan_change 检测器（执行计划突变 → 事故）

- 触发点: `plan_capture` 发现某 digest 新 plan_hash ≠ is_current 的 hash（10 册 §9）。
- 事件契约（复用 L1 `_evt` 结构）:
```python
signal='plan_change', metric_key='plan_hash', severity='warning',
dedup_key=f'{config_id}:plan_change:{digest[:16]}', category='performance',
detail={'sql_digest':..., 'old_plan_hash':..., 'new_plan_hash':...,
        'old_cost':..., 'new_cost':..., 'latency_before_ms':..., 'latency_after_ms':...}
# latency_before/after: sql_stat 中该 digest 变更前后各 30min 的 avg(elapsed_ms_delta/exec_delta)
```
- **降噪铁律**: 仅当 ①该 digest 位于该实例近 1h elapsed Top20 且 ②latency_after ≥
  2× latency_before（或 before 无数据但 after > 500ms）才发事件; 纯计划变化不扰民。
- 诊断映射: `_SIGNAL_ROOT_CAUSE['plan_change']` = R012 计划劣化,
  suggestions=['对比新旧执行计划差异行','检查统计信息是否过期','评估绑定/固化旧计划',
  '检查索引是否失效或新建']; `SIGNAL_PLAYBOOK_REF` 不挂自动 Playbook（计划干预
  各库手段差异大, 本期只出建议）。
- 作战室证据: rca evidence 附两 plan_hash + 深链 SQL 详情"计划对比"Tab。

**验收 7D-01**: 场景 3 演练通过（见 §3）。

## 2. 7D-02 long_transaction 检测器（长事务/空闲中事务）

- 数据源: ASH v2 已采 `state='idle_in_trx'`（MySQL）/ `idle in transaction`（PG）/
  Oracle `v$transaction` 起始时间（哨兵黄金量补一条
  `SELECT MIN(start_date) FROM v$transaction`）。
- 规则（哨兵 ash_sample 尾部, 与 blocked 检测同位置）: 同一 session 的
  idle-in-trx / 事务持续 ≥ `LONG_TRX_WARN_SEC`(默认 300) → 事件
  `signal='long_transaction', category='application'→归 performance,
  detail={'session_id','user_name','duration_sec','sql_text'}`,
  dedup 按 session 分键; ≥1800s 升 critical。
- 诊断建议: ['确认应用是否泄漏事务/忘 commit','评估 kill 会话','检查 undo/回滚段
  压力']; Playbook 挂 PB-LOCK-KILL-BLOCKER 同款 kill（params session_id）——
  复用已有剧本, 仅在 SIGNAL_PLAYBOOK_REF 增映射。

**验收 7D-02**: 场景 6 演练通过。

## 3. 7D-03/7D-05 六场景终验（照此走查, 逐格打勾）

统一注入前提: 五 Tab 已上线, 守护(P1/P2/P3)在跑。注入脚本为
`phase6/drills/e2e_perf_scenarios.py <场景号1-6>`（单文件多场景, 复用
`_drill_common`, 只注入不断言, 断言由走查表完成; 7D-03 交付脚本,
7D-05 交付走查报告 `phase7/ACCEPTANCE.md`）。

### 场景 1: CPU 突高（业务风暴）
| 步 | 动作/页面 | 预期证据 |
|---|---|---|
| 注入 | mysql: 8 并发线程各跑 `SELECT BENCHMARK(2e8, md5('x'))` 类计算 SQL 3min | — |
| 1 | 性能主页 | AAS 绿色(on_cpu)带隆起, 总 AAS ≈ 8 |
| 2 | 缩放到隆起窗口 | Top SQL 首行= benchmark digest, 占比>60%, 色段几乎全绿 |
| 3 | 点击进 SQL 详情 | 趋势: exec/耗时同步上翘; ASH 占比环 on_cpu 主导 |
| 4 | 处置 | 运行中语句列表可见 8 条 → 申请终止(审批链)其中 1 条成功 |

### 场景 2: 锁等待风暴
| 注入 | e2e_lock 手法但持锁 3min, 5 个 waiter |
| 1 | 性能主页: concurrency 暗红带隆起; 顶部横幅提示已有 lock 事故(6A 自动) |
| 2 | 阻塞分析 Tab: 树根源=holder, 影响面=5, 锁明细 `RECORD/X` + `testdb.e2e_lock` |
| 3 | ASH 分析: 过滤 wait_class=concurrency → 面板 lock_object Top1=e2e_lock |
| 4 | 处置: 树上申请终止根源 → 审批执行 → 树清空; 或跳作战室走 Playbook |

### 场景 3: 执行计划劣化
| 注入 | mysql: 带索引查询固定跑量(建基线) → `ALTER TABLE ... DROP INDEX` → 继续跑量 |
| 1 | 事故中心出现 plan_change 事故(7D-01), 证据含新旧 plan_hash 与耗时倍率 |
| 2 | 深链进 SQL 详情"计划对比": 旧=ref 索引扫描, 新=ALL 全表, diff 高亮 |
| 3 | 优化建议 Tab: index_advisor 给出重建该索引建议 |
| 4 | 处置(人工按建议建索引) → 详情页平均耗时趋势回落 |

### 场景 4: 连接风暴溯源
| 注入 | e2e_conn 手法(140 空闲连接) |
| 1 | 性能主页卡片/6A 事故横幅; ASH 分析按 user_name 分面 → 注入账号占比第一 |
| 2 | 按 client_host 分面 → 定位风暴来源主机（EMCC 做不到这一步的一键分面） |
| 3 | 处置走事故 Playbook(已有) 或会话网格批量申请终止 |

### 场景 5: I/O 等待突高
| 注入 | mysql: 对大表(预置 500 万行)冷启动全表聚合 ×4 并发, 或 pg 大表 seqscan |
| 1 | 性能主页 user_io 蓝带隆起 |
| 2 | 顶级活动 brush 该窗口, dim=等待事件 → Top1='Sending data'/'DataFileRead' |
| 3 | dim=SQL → 定位全表扫 SQL → 详情页 reads_delta 趋势陡增, 建议出索引/改写 |

### 场景 6: 长事务拖库
| 注入 | mysql: BEGIN; UPDATE 一行; 不提交挂 10min |
| 1 | 事故中心 long_transaction 事件→事故(7D-02), 含 session/时长/最后 SQL |
| 2 | ASH 分析过滤 wait_class=application → 该会话样本连续可见 |
| 3 | 处置: 事故方案含 kill(复用剧本) → 一键执行 → 验证回路 resolved |

**每场景判定**: 全部"预期证据"格打勾且未登库手查 = PASS。6/6 = 本期"神似"终验通过。

## 4. 7D-04 pg_stat_statements 环境准备

```bash
docker exec db-aiops-postgres psql -U postgres -c \
  "ALTER SYSTEM SET shared_preload_libraries='pg_stat_statements'"
docker restart db-aiops-postgres   # 唯一一次重启, 提前通知
docker exec db-aiops-postgres psql -U postgres -d db_monitor -c \
  "CREATE EXTENSION IF NOT EXISTS pg_stat_statements"
# 验收: SELECT count(*) FROM pg_stat_statements 可查; pg_stat_activity.query_id 非空
```
（compute_query_id=auto 在加载扩展后自动生效, 已实测确认 auto。）

## 5. verify_phase7.py 骨架（随 7D-05 交付）

检查项: ① session_sample 8 新列存在且近 5min 有非空 wait_class 行;
② session_ash_1m/sql_stat 表有数据; ③ 9 条 perf 路由 200; ④ AAS 与 raw 聚合
误差<1%; ⑤ classify() 30 样例单测; ⑥ plan_capture 三库各成功 1 条;
⑦ plan_change/long_transaction 检测器单测; ⑧ 前端 build 通过 + 五 Tab 冒烟。
