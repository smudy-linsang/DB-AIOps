# DB-AIOps Phase 7 详细设计说明书（施工总纲）——性能中心（对标 EMCC 性能大模块）

> **性质**: 详细设计说明书（Detailed Design Spec），目标是让实现者**照图施工**，
> 不依赖二次脑补。编写规范沿用 `../phase6/README.md` 与 `../phase6/00_conventions.md`。
> **本期目标**: 性能模块达到 Oracle EMCC（EM13c）性能大模块的形态与能力——
> **神形俱备**（性能主页 / 顶级活动 / ASH 分析 / SQL 监控 / 阻塞会话 五大页面）,
> 并**真正赋能 DBA 排障**（以 6 个典型排障场景的端到端走查作为终验标准, 见 40 册）。

---

## 0. 文档地图

| 文档 | 层 | 交付目标 | 施工工单 |
|------|----|---------|---------|
| `README.md`（本册） | 全局 | 对标矩阵、统一等待类契约、AAS 口径、性能预算、安全铁律、工单总表 | — |
| `10_ash_engine.md` | 数据/采样 | session_sample v2（wait_class/sql_id/锁明细）、逐库采样 SQL v2、SQL 指纹、sql_stat 快照、sql_plan 计划库、连续聚合与保留 | 7A-01 ~ 7A-08 |
| `20_perf_api.md` | API | 9 个性能端点契约（AAS/顶级活动/ASH 分面/实时会话/阻塞树/运行中SQL/SQL详情/计划采集/期间对比）+ kill 会话审批链 | 7B-01 ~ 7B-07 |
| `30_perf_frontend.md` | 前端 | 性能中心五 Tab 页照图施工（布局图/组件树/ECharts 配置/交互契约） | 7C-01 ~ 7C-07 |
| `40_dba_scenarios.md` | 检测/终验 | plan_change・长事务检测器 + 6 个 DBA 排障场景终验（含注入脚本） | 7D-01 ~ 7D-05 |

**阅读顺序**: 本册 → 10 → 20 → 30 → 40。7A 是地基；7B 依赖 7A 的表结构；
7C 依赖 7B 的 API 契约；7D 依赖全部。

**契约源唯一性原则**（沿用 phase6 铁律）: 字段表/API JSON 样例/映射字典以本说明书为准，
改名先改说明书。每工单验收含四方契约比对（字段表 vs models/DDL vs API 样例 vs 前端读取）。

---

## 1. EMCC 对标矩阵（本期范围）

| EMCC 性能模块功能 | 本期交付物 | 数据源（已实测可用） | 差距声明 |
|---|---|---|---|
| Performance Home（AAS 按等待类堆叠 + Max CPU 线 + Top 卡片） | 性能中心 Tab1「性能主页」 | session_sample v2 聚合 + cpu_cores 黄金量 | 采样粒度 5s（EMCC 内核级 1s） |
| Top Activity（时间刷选 + Top SQL/Top 会话联动） | Tab2「顶级活动」 | session_ash_1m + session_sample | 等价 |
| ASH Analytics（多维切片/负载图/过滤器叠加） | Tab3「ASH 分析」 | session_sample v2（9 维度列） | 无"计划操作行"维度（内核数据） |
| Real-Time SQL Monitoring（执行中 SQL/进度/计划行统计） | Tab4「SQL 监控」运行中视图 + SQL 详情页 | Oracle v$session_longops・v$sql_plan；PG pg_stat_progress_*；MySQL processlist+stage | **计划行级实时统计不做**（v$sql_monitor 为 Oracle 调优包内核特性，外部采样原理上无法等价；以"运行中语句+进度(可得时)+计划快照+digest 趋势"逼近） |
| Blocking Sessions（阻塞树/锁明细/终止会话） | Tab5「阻塞分析」 | session_sample 阻塞边 + 各库锁明细视图 | 超出 EMCC：接 Playbook 自动处置闭环 |
| AWR 期间对比 | 期间对比 API + 前端对比抽屉 | session_ash_1m / sql_stat | 简化为 AAS/TopSQL/等待类三视图对比 |
| ADDM | （已有）diagnosis_pipeline，本期打通页面证据 → 事故跳转 | — | — |
| SQL Tuning Advisor | （已有）index_advisor 建议挂到 SQL 详情页 | — | 建议为规则式，不做试跑 |

**能力边界诚实声明**（写进验收）: ① 采样型 ASH 的粒度下限 5s；② v$sql_monitor 级
计划行实时统计不承诺；③ Oracle Idle 等待不采集（与 EMCC 同口径）。

---

## 2. 全局架构增量（无新增常驻进程）

```
P1 sentinel_daemon  [升级]  ASH v2: 采样列扩展(wait_class/sql_id/program/锁明细),
                            间隔 15s→5s(可调), 背压自动降频
P2 collector        [升级]  T2(60s) 新增: sql_stat 快照增量(逐库 digest 聚合),
                            cpu_cores 黄金量, plan_change 检测挂钩
P3 pipeline_worker  [复用]  新信号 plan_change / long_transaction 走既有事件→事故链
P4 django           [升级]  新增 monitor/api_views_perf.py, 9 个只读端点 +
                            kill 会话复用 AuditLog 审批执行链
前端                 [升级]  /databases/:id/performance 重构为五 Tab 性能中心
```

数据流增量:
```
目标库 ──(哨兵5s ASH v2)──▶ session_sample(超表, raw 7d)
                              └─连续聚合─▶ session_ash_1m(90d)
目标库 ──(采集60s)──▶ sql_stat(超表, digest增量, 90d) ─▶ plan_change 检测─▶ events
目标库 ──(按需/小时)─▶ sql_plan(计划库, 普通表)
前端 ──REST──▶ api_views_perf ──┬─ 历史: TimescaleDB 聚合
                                └─ 实时: DbConnector 只读直连(3s 超时, 失败降级)
```

---

## 3. 统一等待类模型（全局契约, 全册引用）

九类枚举（存库小写下划线; 展示名与颜色为前端契约, 禁止自配色）:

| wait_class | 展示名 | 颜色(hex) | 语义 |
|---|---|---|---|
| `on_cpu` | CPU | `#04B04B` | 活跃且未在等待 |
| `user_io` | 用户 I/O | `#1868C8` | 数据读写等待 |
| `system_io` | 系统 I/O | `#6FB7F5` | 日志/控制文件等后台 I/O |
| `concurrency` | 并发 | `#8B1A1A` | 锁/闩/缓冲争用 |
| `application` | 应用 | `#D9312B` | 行锁枚举/应用设计类等待、长事务空闲 |
| `commit` | 提交 | `#E8842A` | 日志同步/组提交 |
| `network` | 网络 | `#8C9BAB` | 网络收发 |
| `configuration` | 配置 | `#9A6B2F` | 资源不足类（日志切换/缓冲不足） |
| `other` | 其他 | `#D96BA8` | 未归类 |

逐库映射字典是**代码级契约**，全文见 `10_ash_engine.md §3`（Oracle 直取 `v$session.wait_class`；
PG 按 `wait_event_type`；MySQL 按 state 关键词优先级规则）。

---

## 4. AAS 度量口径（全局契约）

- 每条 ASH 样本行代表 `sample_gap_sec` 秒的会话活动（新增列, 写入时=当时采样间隔）。
- **AAS(bucket, 维度) = SUM(sample_gap_sec) / bucket_seconds**（时间加权, 间隔变化仍正确）。
- DB Time(窗口) = Σ AAS × bucket_seconds；主页卡片用。
- Max CPU 参考线 = 该实例 `cpu_cores`（黄金量采集, 缺失时不画线并显示"未知"）。

---

## 5. 性能预算与安全铁律

| 项 | 预算/铁律 |
|---|---|
| 哨兵单次 ASH v2 采样 | 目标库侧 ≤80ms; 连续 2 次 >800ms → 间隔×2 背压(上限 30s), 恢复后逐步回落 |
| 单次采样行数上限 | 500 行（超出截断并计数告警） |
| 实时直连端点 | 只读 SQL、单语句 3s 超时、每请求 1 连接用完即关; 失败返回 `degraded:true` + 历史兜底 |
| API P95 | 历史聚合类 ≤500ms（走连续聚合）; 实时类 ≤3.5s |
| 写操作 | 性能中心内**唯一写操作**是 kill 会话, 必须走 AuditLog 审批执行链(复用现有), 禁止直连执行 |
| EXPLAIN 采集 | 仅 EXPLAIN（禁 ANALYZE）; Oracle 只读 v$sql_plan; 采集失败静默降级 |
| 存储 | session_sample raw 7d(已有) + session_ash_1m 90d + sql_stat 90d, 预算见 10 册 §8 |

---

## 6. 工单总表

| 工单 | 内容 | 依赖 | 册 |
|---|---|---|---|
| 7A-01 | session_sample v2 迁移(8 新列)+索引 | — | 10 |
| 7A-02 | 统一等待类映射模块 detectors/wait_class.py | 7A-01 | 10 |
| 7A-03 | 逐库 ASH 采样 SQL v2(oracle/mysql/pg 含锁明细) | 7A-02 | 10 |
| 7A-04 | SQL 归一化与指纹 sqlfingerprint.py | — | 10 |
| 7A-05 | 采样频率 5s + 背压降频 | 7A-03 | 10 |
| 7A-06 | session_ash_1m 连续聚合 + 保留策略 | 7A-01 | 10 |
| 7A-07 | sql_stat 快照增量采集(三库) | 7A-04 | 10 |
| 7A-08 | sql_plan 计划库 + 采集器(三库) + cpu_cores 黄金量 | 7A-07 | 10 |
| 7B-01 | perf API 骨架 + 通用参数/降级约定 | 7A-06 | 20 |
| 7B-02 | AAS + 顶级活动 + ASH 分面 三端点 | 7B-01 | 20 |
| 7B-03 | 实时会话 + 阻塞树 两端点 | 7B-01 | 20 |
| 7B-04 | 运行中 SQL(进度) 端点 | 7B-01 | 20 |
| 7B-05 | SQL 详情 + 计划采集 端点 | 7A-08 | 20 |
| 7B-06 | 期间对比端点 | 7B-02 | 20 |
| 7B-07 | kill 会话审批链端点 | 7B-03 | 20 |
| 7C-01 | 性能中心壳: 五 Tab 路由/菜单/权限/公共组件 | 7B-01 | 30 |
| 7C-02 | Tab1 性能主页(AAS 堆叠+卡片+联动) | 7B-02 | 30 |
| 7C-03 | Tab2 顶级活动(刷选+双 Top 联动) | 7B-02 | 30 |
| 7C-04 | Tab3 ASH 分析(维度切片+过滤器叠加) | 7B-02 | 30 |
| 7C-05 | Tab4 SQL 监控(运行中+SQL 详情+计划) | 7B-04/05 | 30 |
| 7C-06 | Tab5 阻塞分析(树+锁明细+kill+回放) | 7B-03/07 | 30 |
| 7C-07 | 期间对比抽屉 + 事故联动跳转 | 7B-06 | 30 |
| 7D-01 | plan_change 检测器 + 事故链路 | 7A-08 | 40 |
| 7D-02 | long_transaction 检测器 | 7A-03 | 40 |
| 7D-03 | 场景演练脚本 e2e_perf_*(6 个) | 7C 全 | 40 |
| 7D-04 | pg_stat_statements 环境准备(容器参数) | — | 40 |
| 7D-05 | 六场景终验走查 + 验收报告 | 全部 | 40 |

---

## 7. 验收口径

1. **功能验收**: 每工单验收标准（各册内）全过 + `verify_phase7.py`（工单 7D-05 附带）。
2. **形似验收**: 五 Tab 与 EMCC 对应页面逐要素比对清单（30 册 §8）。
3. **神似（赋能）终验**: 40 册 6 个 DBA 排障场景, 每个场景从"接到告警"到"定位根因证据
   并处置"全程只用本系统页面完成, 走查路径与预期证据逐条打勾。任一场景需要 DBA 登录
   数据库手工查系统视图才能定位 → 该场景 FAIL。
