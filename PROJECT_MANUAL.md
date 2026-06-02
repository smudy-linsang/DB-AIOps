# DB-AIOps 智能运维平台 — 项目说明书

> **版本**: v3.0 Phase 2 智能增强版  
> **代码仓库**: https://github.com/smudy-linsang/DB-AIOps  
> **最后更新**: 2026年5月

---

## 一、项目概述

### 1.1 项目定位

DB-AIOps 是面向**金融行业异构数据库**的自动化智能运维平台，实现对 Oracle、MySQL、PostgreSQL、达梦 DM8、GBase 8A、TDSQL 六种数据库的**统一监控、智能告警、根因分析、容量预测和健康评分**。

### 1.2 解决的核心问题

| 痛点 | 现状 | 本平台解决方案 |
|------|------|---------------|
| 异构数据库各自为政 | 每种数据库用不同工具监控，运维人员需切换多套系统 | 一套平台统一纳管6种数据库 |
| 静态阈值告警泛滥 | 固定阈值导致误报多、漏报多 | 168时间槽动态基线 + 三重条件判定 |
| 故障定位靠经验 | 出了问题靠DBA人肉排查，耗时长 | 自动RCA根因分析 + 修复建议 |
| 容量规划凭感觉 | 不知道什么时候会满，扩容被动 | 多模型趋势预测 + 自动生成扩容工单 |
| 健康状况无量化 | 不知道哪个库"最不健康"，无法排优先级 | 五维健康评分 + A-F评级 |

### 1.3 功能全景图

```
DB-AIOps 智能运维平台
├── 智能监控
│   ├── 多源指标采集（6种数据库×50+指标/库）
│   ├── 高频巡检调度（默认60秒/轮，20线程并发）
│   └── 前端实时仪表盘 + 指标下钻
├── 动态基线告警
│   ├── 168时间槽基线建模（7天×24小时）
│   ├── 三重异常判定（量级+方向+持续性）
│   ├── 多渠道通知（邮件/钉钉/企业微信）
│   └── 告警模板配置 + 静默窗口
├── 根因分析 (RCA)
│   ├── 内置6条诊断规则（连接泄漏/慢查询锁等待等）
│   ├── 自动诊断分析 + 影响范围评估
│   └── 修复建议生成 + 自动修复工单
├── 容量预测
│   ├── 多模型趋势预测（线性/ARIMA/Holt-Winters）
│   ├── 容量瓶颈分析
│   └── 扩容工单生成 + 预计触达日期
└── 健康评分
    ├── 五维打分（可用性/容量/性能/配置/运维）
    └── A-F评级 + 评分报告
```

---

## 二、技术架构

### 2.1 技术栈

| 层级 | 技术选型 | 说明 |
|------|---------|------|
| **后端框架** | Django 5.2+ | Python Web 框架，REST API |
| **前端框架** | React 18 + Vite 5 + Ant Design 5 | SPA 单页应用，ECharts 图表 |
| **业务主库** | PostgreSQL 16 | 存储核心业务实体（配置/日志/告警/基线等） |
| **时序数据** | TimescaleDB (PG扩展) | 指标时序点 + 采集快照，支持自动压缩 |
| **缓存/消息** | Redis 7 | Token缓存 + SSE 事件发布订阅 |
| **指标检索** | Elasticsearch 8.12 | 按月分片的指标/告警文档，全文检索 |
| **任务调度** | APScheduler | 定时巡检采集 |
| **密码加密** | AES-256-GCM | 数据库密码加密存储 |
| **容器化** | Docker Compose | 一键启动全栈环境 |

### 2.2 四库协同架构

```
┌──────────────────────────────────────────────────────┐
│                    Django REST API                     │
│              (api_views.py / 3281行)                   │
├──────────┬───────────┬──────────────┬────────────────┤
│PostgreSQL │ TimescaleDB│    Redis     │ Elasticsearch │
│  业务主库  │  时序数据湖 │ 缓存+消息中心 │  全文搜索索引  │
│          │            │              │               │
│·配置表    │·metric_point│·Token缓存   │·db_metrics_*  │
│·监控日志  │·collection │·健康评分缓存 │·db_alerts_*   │
│·告警记录  │  _snapshot │·SSE事件PubSub│·按月自动分片   │
│·基线模型  │·自动压缩    │              │·复杂聚合查询   │
│·健康评分  │            │              │               │
│·审计日志  │            │              │               │
└──────────┴───────────┴──────────────┴────────────────┘
```

**数据流向**: 采集器 → 写入PostgreSQL(业务数据) + TimescaleDB(时序快照) + Elasticsearch(指标文档) → Redis缓存热数据 → 前端API读取

### 2.3 项目目录结构

```
DB_Monitor/
├── dbmonitor/                  # Django 项目配置
│   ├── settings.py             # 核心配置（数据库/缓存/ES/安全/调度参数）
│   ├── urls.py                 # URL 路由
│   └── wsgi.py
├── monitor/                    # 核心业务模块（★重点）
│   ├── models.py               # 数据模型（826行，14+模型）
│   ├── api_views.py            # REST API 视图（3281行）
│   ├── views.py                # 传统视图（兼容）
│   ├── auth.py                 # 认证与RBAC权限
│   ├── checkers/               # 数据库采集器（模块化）
│   │   ├── base.py             # 采集器基类
│   │   ├── oracle.py           # Oracle 采集器
│   │   ├── mysql.py            # MySQL 采集器
│   │   ├── pgsql.py            # PostgreSQL 采集器
│   │   ├── dm.py               # 达梦 DM8 采集器
│   │   ├── gbase.py            # GBase 8A 采集器
│   │   └── tdsql.py            # TDSQL 采集器
│   ├── alert_engine.py         # 告警引擎
│   ├── baseline_engine.py      # 动态基线引擎
│   ├── rca_engine.py           # 根因分析引擎
│   ├── capacity_engine.py      # 容量预测引擎
│   ├── health_engine.py        # 健康评分引擎
│   ├── slow_query_engine.py    # 慢查询分析引擎
│   ├── config_advisor.py       # 配置建议引擎
│   ├── notifications.py        # 通知发送（邮件/钉钉/企微）
│   ├── timeseries.py           # TimescaleDB 交互层
│   ├── elasticsearch_engine.py # Elasticsearch 交互层
│   ├── crypto.py               # AES-256-GCM 加解密
│   ├── cache.py                # Redis 缓存封装
│   ├── tasks.py                # Celery 异步任务
│   └── management/commands/
│       └── start_monitor.py    # 采集守护进程入口
├── frontend/                   # React 前端
│   ├── src/
│   │   ├── pages/              # 页面组件（10个）
│   │   │   ├── Dashboard.jsx       # 仪表盘
│   │   │   ├── DatabaseList.jsx    # 数据库列表
│   │   │   ├── DatabaseDetail.jsx  # 数据库详情（指标卡片+图表+下钻）
│   │   │   ├── AlertList.jsx       # 告警列表
│   │   │   ├── AlertConfig.jsx     # 告警配置
│   │   │   ├── SQLMonitoring.jsx   # SQL监控
│   │   │   ├── CapacityPlanning.jsx # 容量规划
│   │   │   ├── TicketManagement.jsx # 工单管理
│   │   │   ├── NotificationSettings.jsx # 通知设置
│   │   │   └── BusinessSystems.jsx  # 业务系统
│   │   ├── components/         # 通用组件（6个）
│   │   ├── config/
│   │   │   └── dbMetricsConfig.js   # ★六库指标配置（核心，1238行）
│   │   ├── services/
│   │   │   └── api.js          # API 封装
│   │   └── stores/
│   │       └── useAppStore.js  # 全局状态管理(Zustand)
│   └── vite.config.js
├── docker-compose.yml          # 全栈容器编排
├── Dockerfile                  # Django 应用镜像
├── requirements.txt            # Python 依赖
└── manage.py                   # Django 管理入口
```

---

## 三、核心模块详解

### 3.1 数据库采集器 (monitor/checkers/)

**设计模式**: 模板方法模式，所有采集器继承 `BaseChecker`，实现 `check()` 方法返回指标字典。

| 采集器 | 连接方式 | 核心指标 |
|--------|---------|---------|
| **oracle.py** | oracledb | 实例信息、SGA/PGA、RAC/DG、表空间、等待事件、Top SQL、锁等待 |
| **mysql.py** | pymysql | InnoDB缓冲池/IO/行操作、复制状态、SSL、Top SQL、未使用索引、冗余索引 |
| **pgsql.py** | psycopg2 | 缓存命中率、后台写入、流复制、AutoVacuum、锁等待、序列使用率 |
| **dm.py** | pyodbc | 缓冲池、DW主备/DSC集群、表空间、数据文件、慢查询、配置参数 |
| **gbase.py** | pymysql | 集群管理/数据节点、副本状态、会话列表 |
| **tdsql.py** | pymysql | InnoDB详情、Proxy连接检测、SSL状态、SQL摘要、未使用索引、集群节点 |

**每个采集器的返回值**是一个扁平字典，包含：
- 数值型指标：`qps`, `tps`, `buffer_hit_ratio`, `innodb_deadlocks` 等
- 表格型指标：`session_list`, `top_sql_by_latency`, `unused_indexes` 等（列表值）
- 兼容字段：`innodb_buffer_pool_hit_ratio` = `buffer_hit_ratio`（确保前端key能命中）

### 3.2 动态基线引擎 (baseline_engine.py)

**核心算法**: 168时间槽（7天×24小时）滑动窗口

```
时间槽编号 = 星期几(0-6) × 24 + 小时(0-23)
例: 周三下午3点 = 2 × 24 + 15 = 63号槽
```

**每个槽维护的统计量**: 均值(mean)、标准差(std)、P90/P95/P99、正常范围上下限

**三重异常判定**:
1. **量级条件**: 当前值 > mean + k×std (默认k=2)
2. **方向条件**: 值偏离方向与告警方向一致
3. **持续性条件**: 连续N次采集均为异常（避免瞬时抖动误报）

### 3.3 告警引擎 (alert_engine.py)

**告警类型**:

| 类型 | 说明 | 检测方式 |
|------|------|---------|
| `down` | 实例DOWN/UP | 采集连接失败 |
| `tablespace` | 表空间容量 | 固定阈值(85%/95%) |
| `connection` | 连接数使用率 | 固定阈值(80%/90%) |
| `lock` | 锁等待 | 锁等待记录检测 |
| `baseline` | 基线偏离 | 动态基线三重判定 |

**去重机制**: 同一数据库 + 告警类型 + metric_key 同时只保留一条 active 记录。

**通知渠道**: 邮件(SMTP)、钉钉机器人(Webhook)、企业微信机器人(Webhook)

### 3.4 根因分析引擎 (rca_engine.py)

内置6条诊断规则：

| 规则ID | 名称 | 触发条件 | 诊断结论 |
|--------|------|---------|---------|
| R001 | 连接泄漏 | 连接使用率>90% + 活跃连接<30% | 应用未释放连接 |
| R002 | 慢查询导致锁等待 | 慢查询数>0 + 锁等待>0 | 慢查询阻塞 |
| R003 | 表空间即将耗尽 | 使用率>85% | 需扩容 |
| R004 | 缓冲池命中率低 | 命中率<95% | 需增大缓冲池 |
| R005 | 主备延迟 | 延迟>30秒 | 复制瓶颈 |
| R006 | 磁盘IO瓶颈 | IO等待时间占比高 | 磁盘性能不足 |

### 3.5 容量预测引擎 (capacity_engine.py)

**三种预测模型**:
- **线性回归**: 适用于线性增长指标（如表空间使用率）
- **ARIMA**: 适用于有季节性波动的指标
- **Holt-Winters**: 适用于有趋势+季节性的指标

**输出**: 月增长率、预计触达告警线日期、预计触达危险线日期、扩容建议

### 3.6 健康评分引擎 (health_engine.py)

**五维评分模型**（总分100）:

| 维度 | 权重 | 评估内容 |
|------|------|---------|
| 可用性 | 30分 | 实例在线状态、主备延迟 |
| 容量 | 25分 | 表空间/连接使用率 |
| 性能 | 20分 | 缓冲命中率、慢查询、死锁 |
| 配置 | 15分 | 归档模式、GTID、SSL |
| 运维 | 10分 | 密码轮换、告警响应 |

**评级**: A(≥90) / B(≥80) / C(≥70) / D(≥60) / F(<60)

---

## 四、前端架构

### 4.1 技术选型

- **框架**: React 18 + Vite 5
- **UI库**: Ant Design 5
- **图表**: ECharts (按需引入)
- **状态管理**: Zustand
- **路由**: React Router v6
- **HTTP**: Axios

### 4.2 核心配置文件: dbMetricsConfig.js

这是前端最重要的配置文件（1238行），**定义了每种数据库在详情页展示哪些指标、如何展示**。

```javascript
// 配置结构示例
export const DB_METRIC_CATEGORIES = {
  tdsql: [
    {
      key: 'innodb_buffer',           // 分类唯一标识
      title: 'TDSQL InnoDB 缓冲池',    // 卡片标题
      type: 'cards',                   // 展示类型: cards(指标卡片) | table(表格)
      metrics: [
        { key: 'innodb_buffer_pool_hit_ratio', label: '命中率', format: 'percent', highlight: true },
        { key: 'innodb_buffer_pool_pages_dirty', label: '脏页数', format: 'number' },
      ]
    },
    {
      key: 'top_sql',
      title: 'TDSQL Top SQL',
      type: 'table',
      showWhen: (data) => data.top_sql_by_latency && data.top_sql_by_latency.length > 0,
      columns: [
        { key: 'sql_text', title: 'SQL文本' },
        { key: 'exec_count', title: '执行次数', format: 'number' },
      ]
    }
  ]
}
```

**新增数据库指标的标准流程**:
1. 在 `checkers/xxx.py` 中新增SQL采集逻辑（try-except兜底）
2. 在返回字典中添加字段
3. 在 `dbMetricsConfig.js` 对应数据库的分类中添加配置项
4. 前端会自动根据 `getMetricCategories(dbType)` 渲染

### 4.3 页面与API对应关系

| 前端页面 | API 端点 | 说明 |
|---------|---------|------|
| 仪表盘 | `/api/v1/dashboard/charts/` | 概览统计 |
| 数据库列表 | `/api/v1/databases/` | CRUD + 状态查询 |
| 数据库详情 | `/api/v1/databases/{id}/status/` | 实时指标 + 时序图表 |
| 告警列表 | `/api/v1/alerts/` | 告警查询 + 确认/恢复 |
| 告警配置 | `/api/v1/alert-templates/` | 阈值模板管理 |
| SQL监控 | `/api/v1/databases/{id}/status/` | Top SQL/慢查询 |
| 容量规划 | `/api/v1/capacity/predictions/` | 预测结果 |
| 工单管理 | `/api/v1/tickets/` | 审批流程 |
| 通知设置 | `/api/v1/notifications/` | 渠道配置 |
| 业务系统 | `/api/v1/business-systems/` | 业务关联 |

---

## 五、数据模型

### 5.1 核心模型关系

```
DatabaseConfig (数据库配置)
    ├── 1:N → MonitorLog (监控日志)
    ├── 1:N → AlertLog (告警记录)
    ├── 1:N → BaselineModel (基线模型)
    ├── 1:N → HealthScore (健康评分)
    ├── 1:N → PredictionResult (容量预测)
    ├── 1:N → AuditLog (运维审计)
    ├── 1:N → AlertSilenceWindow (告警静默)
    └── M:N → BusinessSystem (业务系统)

AlertLog (告警记录)
    ├── 1:N → AlertNotificationLog (通知日志)
    └── N:1 → AuditLog (关联工单)

UserProfile (用户配置)
    └── 1:1 → auth.User (Django用户)
```

### 5.2 关键模型字段速查

| 模型 | 核心字段 | 说明 |
|------|---------|------|
| **DatabaseConfig** | name, db_type, host, port, username, password(加密), service_name, is_active | 被监控数据库的连接信息 |
| **MonitorLog** | config(FK), status(UP/DOWN), message(JSON), create_time | 每轮采集的原始数据 |
| **AlertLog** | config(FK), alert_type, metric_key, severity, title, status(active/resolved) | 告警生命周期管理 |
| **BaselineModel** | config(FK), metric_key, time_slot(0-167), mean, std, p95, normal_min/max | 168时间槽统计量 |
| **HealthScore** | config(FK), score_date, total_score, availability/capacity/performance/config/ops_score | 五维评分 |
| **AuditLog** | config(FK), action_type, risk_level, status(审批流程), execution_evidence | 运维操作审计 |

---

## 六、安全设计

| 安全项 | 实现方式 |
|--------|---------|
| 密码存储 | AES-256-GCM 加密，以 `enc:` 前缀标识密文 |
| API认证 | Token-Based Auth，登录签发，24小时过期 |
| RBAC权限 | 四级角色：admin / supervisor / user / readonly |
| 数据隔离 | `allowed_databases` 字段控制用户可见数据库范围 |
| CSRF防护 | Django 中间件 + 信任来源配置 |
| 生产安全 | HTTPS强制跳转、Secure Cookie、HSTS、密钥必须环境变量 |

---

## 七、本地开发环境搭建

### 7.1 前置依赖

- Python 3.11+
- Node.js 18+
- Docker Desktop（运行数据库容器）
- Git

### 7.2 一键启动

```bash
# 1. 克隆代码
git clone https://github.com/smudy-linsang/DB-AIOps.git
cd DB-AIOps

# 2. 创建Python虚拟环境
python -m venv .venv
# Windows:
.\.venv\Scripts\Activate.ps1
# Linux/Mac:
source .venv/bin/activate

# 3. 安装后端依赖
pip install -r requirements.txt

# 4. 启动数据库容器（PostgreSQL + Redis + ES + 测试库）
docker compose up -d timescaledb redis elasticsearch
docker start dbmonitor-mysql dbmonitor-oracle gbase8a

# 5. 数据库迁移
python manage.py migrate

# 6. 初始化告警模板
python manage.py init_alert_templates

# 7. 初始化TimescaleDB
python manage.py init_timeseries

# 8. 创建管理员
python manage.py createsuperuser

# 9. 启动后端
python manage.py runserver 0.0.0.0:8000

# 10. 启动监控采集（另一个终端）
python manage.py start_monitor

# 11. 启动前端（另一个终端）
cd frontend
npm install
npm run dev
```

### 7.3 访问地址

| 服务 | 地址 |
|------|------|
| 前端 | http://localhost:3000 |
| 后端API | http://localhost:8000/api/v1/ |
| Django Admin | http://localhost:8000/admin/ |
| Elasticsearch | http://localhost:9200 |

### 7.4 默认账号

| 用途 | 用户名 | 密码 |
|------|--------|------|
| 系统登录 | admin | admin123 |
| PostgreSQL | postgres | postgres123 |
| MySQL测试库 | root | root123 |
| Redis | 无密码 | - |

---

## 八、关键开发规范

### 8.1 新增采集指标

1. **后端**: 在 `checkers/xxx.py` 的 `check()` 方法中新增SQL查询，**必须**用 `try-except` 包裹
2. **返回值**: 在 return 字典末尾添加新字段，优先复用已有变量
3. **前端**: 在 `dbMetricsConfig.js` 对应数据库的分类中添加 metric 配置
4. **格式**: 使用 `format` 字段指定展示格式（number/percent/size_mb/bytes/duration/text/boolean/status）

### 8.2 前端变量定义

- 组件内局部状态使用 `*Local` 后缀（如 `loadingLocal`），避免与全局状态冲突
- 所有 hooks 必须在条件返回之前调用（React Hooks 规则）

### 8.3 异常处理

- 禁止使用裸 `except`，必须使用 `except Exception`
- 单个指标采集失败不得影响整体采集流程

### 8.4 环境变量

所有敏感配置必须通过环境变量传入，生产环境不提供不安全的默认值：

```
DJANGO_SECRET_KEY=xxx          # 生产必须设置
DB_MONITOR_SECRET_KEY=xxx      # 密码加密密钥，生产必须设置
POSTGRES_HOST=xxx              # 数据库地址
POSTGRES_PASSWORD=xxx          # 数据库密码
```

---

## 九、常用运维命令

```bash
# 加密已有明文密码
python manage.py encrypt_db_passwords

# 查看系统健康状态
curl http://localhost:8000/api/v1/health/

# 手动触发一轮采集
python manage.py start_monitor --once

# 查看采集进程日志
tail -f logs/db_monitor.log

# 前端构建
cd frontend && npm run build

# Docker 全栈重启
docker compose down && docker compose up -d
```

---

## 十、项目里程碑

| 阶段 | 内容 | 状态 |
|------|------|------|
| Phase 1 | 基础监控 + 告警 + 前端 | ✅ 已完成 |
| Phase 2 | 动态基线 + RCA + 容量预测 + 健康评分 | ✅ 已完成 |
| Phase 3 | 自动修复 + 审批流程 + RBAC + 审计 | ✅ 已完成 |
| Phase 4 | 密码轮换 + 报表 + 业务拓扑 + SSE推送 | ✅ 已完成 |
| 持续迭代 | TDSQL Toolkit集成、指标体系增强、前端UX优化 | 🔄 进行中 |
