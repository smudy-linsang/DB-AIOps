# 数据库监控系统 v2.1

🛡️ **全能型数据库监控解决方案** - 支持 Oracle, MySQL, PostgreSQL, 达梦，Gbase8a, TDSQL

## 📋 功能特性

### ✅ 已实现

#### 1. 全栈数据采集
- **Oracle**: 版本、连接数、表空间、RAC 锁等待、启动时间
- **MySQL**: 版本、连接数、QPS、慢查询、数据库大小、InnoDB 锁
- **PostgreSQL**: 版本、连接数、数据库大小、表空间、锁等待、慢查询
- **达梦 DM8**: 版本、连接数、表空间、锁等待
- **Gbase8a**: 版本、连接数、数据库大小、集群节点状态
- **TDSQL**: 版本、连接数、数据库大小、分片状态

#### 2. 智能告警
- 🔴 **故障告警**: 数据库连接失败立即通知
- 🟢 **恢复通知**: 数据库恢复后自动告知
- 🟠 **容量告警**: 表空间使用率 > 90%
- 🟠 **连接数告警**: 连接数使用率 > 80%
- 🔴 **性能告警**: 锁等待持续检测（直到解除）
- 🟠 **基线异常**: 指标偏离历史基线自动预警

#### 3. 根因分析 (RCA)
内置 6 条诊断规则:
- R001: 连接数泄漏检测
- R002: 慢查询导致锁等待
- R003: 表空间容量不足
- R004: QPS 突降诊断
- R005: 集群节点异常
- R006: 分片数据不均衡

#### 4. Web Dashboard
- 监控大屏：所有数据库状态一目了然
- 详情页：连接数趋势、表空间历史趋势

---

## 🚀 快速开始

### 1. 环境准备

```bash
# 进入项目目录
cd DB_Monitor

# 激活虚拟环境 (Windows)
venv\Scripts\activate

# 激活虚拟环境 (Linux/Mac)
source venv/bin/activate

# 安装依赖 (如果还没装)
pip install -r requirements.txt
```

### 2. 数据库迁移

```bash
python manage.py makemigrations
python manage.py migrate
```

### 3. 创建超级用户 (可选，用于 Django Admin)

```bash
python manage.py createsuperuser
```

### 4. 配置邮件告警

编辑 `dbmonitor/settings.py`:

```python
# 邮件配置
EMAIL_BACKEND = 'django.core.mail.backends.smtp.EmailBackend'
EMAIL_HOST = 'smtp.yourcompany.com'
EMAIL_PORT = 25
EMAIL_HOST_USER = 'dbmonitor@yourcompany.com'
EMAIL_HOST_PASSWORD = 'your_password'
DEFAULT_FROM_EMAIL = 'dbmonitor@yourcompany.com'
ADMIN_EMAILS = ['dba1@yourcompany.com', 'dba2@yourcompany.com']
```

### 5. 添加数据库配置

**方式 A: 通过 Django Admin**
```bash
python manage.py runserver
# 访问 http://127.0.0.1:8000/admin
```

**方式 B: 通过 Shell**
```bash
python manage.py shell
```

```python
from monitor.models import DatabaseConfig

# 添加 Oracle 数据库
DatabaseConfig.objects.create(
    name='核心交易库_主节点',
    db_type='oracle',
    host='192.168.1.100',
    port=1521,
    username='system',
    password='your_password',
    service_name='ORCL',
    is_active=True
)

# 添加 MySQL 数据库
DatabaseConfig.objects.create(
    name='用户库_TDSQL',
    db_type='tdsql',
    host='192.168.1.101',
    port=3306,
    username='root',
    password='your_password',
    is_active=True
)
```

### 6. 启动监控守护进程

```bash
python manage.py start_monitor
```

看到以下输出表示成功:
```
[2026-03-25 10:00:00] 🛡️ 全栈监控守护进程 v2.1 已启动...
>> 支持的数据库：Oracle, MySQL, PostgreSQL, 达梦，Gbase8a, TDSQL
>> 架构模式：插件化检查器
>> 告警策略：锁等待持续轰炸 + 智能恢复通知

[2026-03-25 10:00:01] --- 开始新一轮巡检 ---
  √ Oracle [核心交易库_主节点]: 正常
  √ TDSQL [用户库_TDSQL]: 正常
```

---

## 📊 查看监控数据

### Web 界面
```bash
python manage.py runserver
# 访问 http://127.0.0.1:8000/
```

### 数据库查询
```bash
python manage.py shell
```

```python
from monitor.models import DatabaseConfig, MonitorLog

# 查看所有数据库配置
for db in DatabaseConfig.objects.all():
    print(f"{db.name} ({db.host}) - {db.get_db_type_display()}")

# 查看最新监控日志
latest = MonitorLog.objects.filter(config__name='核心交易库_主节点').order_by('-create_time').first()
print(f"状态：{latest.status}")
print(f"数据：{latest.message}")
```

---

## ⚙️ 配置说明

### 告警阈值调整

编辑 `monitor/management/commands/start_monitor.py`:

```python
# ==========================================
# [配置区] 阈值设置
# ==========================================
TBS_THRESHOLD = 90       # 表空间使用率告警阈值 (%)
LOCK_TIME_THRESHOLD = 10 # 锁等待告警阈值 (秒)
CONN_THRESHOLD_PCT = 80  # 连接数使用率告警阈值 (%)
```

### 巡检频率调整

```python
# 在 handle() 方法中修改
scheduler.add_job(self.monitor_job, 'interval', seconds=60)  # 60 秒 = 1 分钟
```

建议:
- 生产环境：30-60 秒
- 测试环境：5-10 分钟

---

## 🔧 高级功能

### 1. 基线分析

```python
from monitor.baseline_engine import BaselineEngine
from monitor.models import DatabaseConfig

config = DatabaseConfig.objects.get(name='核心交易库_主节点')
engine = BaselineEngine(config)

# 获取连接数基线
baseline = engine.calculate_baseline('active_connections', days=7)
print(f"连接数基线：{baseline}")
# 输出: {'mean': 45.2, 'std': 12.5, 'p95': 68.0, 'normal_range': [20.2, 70.2]}

# 生成完整报告
report = engine.get_full_baseline_report(days=7)
```

### 2. 根因分析

```python
from monitor.rca_engine import RCAEngine

config = DatabaseConfig.objects.get(name='核心交易库_主节点')
engine = RCAEngine(config)

# 执行分析
report = engine.analyze()
print(report['summary'])
# 输出: "🔴 发现 1 个严重问题 | 主要问题：慢查询导致锁等待"

for diagnosis in report['diagnoses']:
    print(f"\n问题：{diagnosis['name']}")
    print(f"描述：{diagnosis['description']}")
    print(f"建议:")
    for suggestion in diagnosis['suggestions']:
        print(f"  - {suggestion}")
```

### 3. 生成修复命令

```python
# 针对锁等待问题生成 Kill 命令
for diagnosis in report['diagnoses']:
    if diagnosis['rule_id'] == 'R002':  # 锁等待
        commands = engine.generate_fix_commands(diagnosis)
        for cmd in commands:
            print(f"[{cmd['risk_level']}] {cmd['description']}")
            print(f"SQL: {cmd['command']}")
```

---

## 📦 项目结构

```
DB_Monitor/
├── dbmonitor/              # Django 项目配置
│   ├── settings.py         # 全局配置 (邮件、数据库等)
│   ├── urls.py             # URL 路由
│   └── wsgi.py
├── monitor/                # 监控应用
│   ├── management/
│   │   └── commands/
│   │       └── start_monitor.py  # 【核心】监控守护进程
│   ├── migrations/         # 数据库迁移
│   ├── templates/
│   │   └── monitor/
│   │       ├── dashboard.html  # 监控大屏
│   │       └── detail.html     # 详情页
│   ├── models.py           # 数据模型
│   ├── views.py            # Web 视图
│   ├── baseline_engine.py  # 【新增】基线分析引擎
│   └── rca_engine.py       # 【新增】根因分析引擎
├── venv/                   # Python 虚拟环境
├── manage.py               # Django 管理命令
└── db.sqlite3              # SQLite 数据库文件
```

---

## 🐛 常见问题

### Q1: Oracle 连接失败 "ORA-12514"
**A:** 检查 `service_name` 是否正确，可以在 `tnsnames.ora` 中查找正确的服务名。

### Q2: 达梦数据库连接失败
**A:** 确保已安装达梦 ODBC 驱动，并在系统中配置好 DSN。

### Q3: 邮件发送失败
**A:** 检查 SMTP 服务器地址、端口、用户名密码是否正确。可以先用 telnet 测试连通性。

### Q4: 锁等待告警太频繁
**A:** 调整 `LOCK_TIME_THRESHOLD` 阈值，比如从 10 秒改为 30 秒。

---

## 📝 更新日志

### v2.1 (2026-03-25)
- ✅ 新增 PostgreSQL 深度监控 (表空间、锁等待、慢查询)
- ✅ 新增达梦数据库深度监控 (表空间、锁等待)
- ✅ 新增 Gbase8a 集群状态监控
- ✅ 新增 TDSQL 支持 (MySQL 兼容版)
- ✅ 新增基线分析引擎 (动态阈值检测)
- ✅ 新增根因分析引擎 (6 条诊断规则)
- ✅ 重构代码为插件化架构，易于扩展
- ✅ 统一所有数据库的指标输出格式

### v1.0 (2025-12-25)
- 初始版本，支持 Oracle/MySQL 基础监控
- 实现锁等待持续告警机制
- Web Dashboard 基础功能

---

## 🤝 贡献指南

欢迎提交 Issue 和 Pull Request!

### 添加新的数据库类型支持

1. 在 `monitor/models.py` 的 `DB_TYPES` 中添加新类型
2. 在 `monitor/management/commands/` 创建新的 Checker 类
3. 继承 `BaseDBChecker` 并实现 `get_connection()` 和 `collect_metrics()`
4. 在 `Command.CHECKER_MAP` 中注册

示例:
```python
class NewDBChecker(BaseDBChecker):
    def get_connection(self, config):
        # 实现连接逻辑
        pass
    
    def collect_metrics(self, config, conn):
        # 实现指标采集
        return {
            'version': '...',
            'active_connections': 0,
            # ...
        }
```

---

## 📄 License

MIT License

---

## 📞 联系方式

如有问题或建议，请联系开发团队。

**Happy Monitoring! 🎉**
