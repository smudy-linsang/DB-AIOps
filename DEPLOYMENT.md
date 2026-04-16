# 数据库监控系统 v2.1 - 完整交付说明

## 📦 交付内容总览

我已经完成了 **Phase 1-4** 的全部开发工作，交付一个完整可用的数据库监控系统。

### ✅ 已完成功能清单

#### Phase 1: 全栈数据采集
- [x] Oracle 深度监控 (RAC 兼容)
- [x] MySQL 深度监控 (支持 TDSQL MySQL 版)
- [x] PostgreSQL 深度监控 (新增)
- [x] 达梦 DM8 深度监控 (新增)
- [x] Gbase8a 集群监控 (新增)
- [x] TDSQL 分片监控 (新增)
- [x] 统一指标格式
- [x] 插件化架构

#### Phase 2: 智能分析引擎
- [x] 基线分析引擎 (`baseline_engine.py`)
  - 均值、标准差、P95、P99 计算
  - 动态阈值检测
  - 历史趋势分析
- [x] 根因分析引擎 (`rca_engine.py`)
  - 6 条诊断规则
  - 自动问题定位
  - 处理建议生成

#### Phase 3: 自动化处理
- [x] 自动化修复引擎 (`auto_remediation_engine.py`)
  - 根据 RCA 结果生成修复方案
  - Kill Session 命令生成 (所有数据库类型)
  - 表空间扩容命令生成
  - 操作审计日志 (`AuditLog` 模型)
  - 审批流程集成
  - 回滚命令生成

#### Phase 4: Web 界面增强
- [x] 实时监控大屏 (ECharts 图表)
- [x] 60 秒自动刷新
- [x] API 接口 (实时指标/基线/RCA)
- [x] 运维操作审批页面
- [x] 健康检查接口

---

## 📁 项目文件结构

```
DB_Monitor/
├── dbmonitor/                          # Django 项目配置
│   ├── settings.py                     # 邮件、数据库等配置
│   ├── urls.py                         # 【已更新】新增 API 和审批路由
│   ├── wsgi.py
│   └── asgi.py
│
├── monitor/                            # 监控应用核心
│   ├── management/
│   │   └── commands/
│   │       └── start_monitor.py        # 【重构】监控守护进程 (插件化)
│   │
│   ├── migrations/
│   │   ├── 0001_initial.py             # DatabaseConfig 模型
│   │   ├── 0002_monitorlog.py          # MonitorLog 模型
│   │   └── 0003_databaseconfig_service_name.py
│   │
│   ├── templates/monitor/
│   │   ├── dashboard.html              # 原监控大屏
│   │   ├── dashboard_enhanced.html     # 【新增】增强版大屏
│   │   ├── detail.html                 # 详情页
│   │   └── remediation_list.html       # 【新增】审批页面
│   │
│   ├── models.py                       # 【已更新】新增 AuditLog 模型
│   ├── views.py                        # 原视图
│   ├── views_enhanced.py               # 【新增】增强视图 + API
│   │
│   ├── baseline_engine.py              # 【新增】基线分析引擎
│   ├── rca_engine.py                   # 【新增】根因分析引擎
│   └── auto_remediation_engine.py      # 【新增】自动化修复引擎
│
├── venv/                               # Python 虚拟环境
├── manage.py                           # Django 管理命令
├── db.sqlite3                          # SQLite 数据库
├── requirements.txt                    # Python 依赖
└── README.md                           # 使用文档
```

---

## 🚀 快速部署步骤

### 步骤 1: 备份现有项目

```bash
cd /path/to/your/project
mv DB_Monitor DB_Monitor_backup_$(date +%Y%m%d)
```

### 步骤 2: 解压新版本

```bash
unzip DB_Monitor_v2.1.zip
mv db_monitor_project/DB_Monitor .
cd DB_Monitor
```

### 步骤 3: 激活虚拟环境

**Windows:**
```bash
venv\Scripts\activate
```

**Linux/Mac:**
```bash
source venv/bin/activate
```

### 步骤 4: 安装/更新依赖

```bash
pip install -r requirements.txt
```

### 步骤 5: 数据库迁移

```bash
# 创建新的迁移 (因为新增了 AuditLog 模型)
python manage.py makemigrations

# 应用迁移
python manage.py migrate
```

### 步骤 6: 配置邮件告警

编辑 `dbmonitor/settings.py`，在文件末尾添加：

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

### 步骤 7: 启动 Web 服务

```bash
python manage.py runserver 0.0.0.0:8000
```

访问：http://your-server-ip:8000/

### 步骤 8: 启动监控守护进程

**新终端窗口:**

```bash
cd DB_Monitor
venv\Scripts\activate  # 或 source venv/bin/activate

python manage.py start_monitor
```

---

## 🔧 配置数据库连接

### 方式 A: 通过 Django Admin (推荐)

1. 访问：http://127.0.0.1:8000/admin/
2. 登录 (用 superuser 账号)
3. 点击 "数据库配置" → "添加数据库配置"
4. 填写信息并保存

### 方式 B: 通过 Shell

```bash
python manage.py shell
```

```python
from monitor.models import DatabaseConfig

# Oracle 示例
DatabaseConfig.objects.create(
    name='核心交易库_Oracle',
    db_type='oracle',
    host='192.168.1.100',
    port=1521,
    username='system',
    password='your_password',
    service_name='ORCL',
    is_active=True
)

# PostgreSQL 示例
DatabaseConfig.objects.create(
    name='分析库_PG',
    db_type='pgsql',
    host='192.168.1.101',
    port=5432,
    username='postgres',
    password='your_password',
    service_name='analytics',
    is_active=True
)

# TDSQL 示例
DatabaseConfig.objects.create(
    name='交易库_TDSQL',
    db_type='tdsql',
    host='192.168.1.102',
    port=3306,
    username='root',
    password='your_password',
    is_active=True
)

# 达梦示例
DatabaseConfig.objects.create(
    name='档案库_达梦',
    db_type='dm',
    host='192.168.1.103',
    port=5236,
    username='SYSDBA',
    password='your_password',
    is_active=True
)

# Gbase8a 示例
DatabaseConfig.objects.create(
    name='数仓_Gbase8a',
    db_type='gbase',
    host='192.168.1.104',
    port=5258,
    username='root',
    password='your_password',
    is_active=True
)
```

---

## 📊 功能演示

### 1. 查看监控大屏

访问：http://127.0.0.1:8000/

特性:
- 📊 实时统计卡片 (总数/正常/故障/告警)
- 🎨 渐变背景 + 卡片悬浮动画
- ⚡ 60 秒自动刷新
- 🔴 告警提示 (锁等待/表空间)

### 2. 查看数据库详情

访问：http://127.0.0.1:8000/monitor/<数据库 ID>/

显示:
- 📈 连接数趋势图
- 📊 表空间使用率进度条
- 📉 表空间历史趋势
- 🧠 基线分析报告
- 🔍 RCA 诊断结果

### 3. API 接口测试

```bash
# 获取最新指标
curl http://127.0.0.1:8000/api/metrics/1/

# 获取基线报告
curl http://127.0.0.1:8000/api/baseline/1/

# 获取 RCA 报告
curl http://127.0.0.1:8000/api/rca/1/

# 健康检查
curl http://127.0.0.1:8000/api/health/
```

### 4. 运维操作审批

访问：http://127.0.0.1:8000/monitor/remediation/

功能:
- ⏳ 待审批操作列表
- 📜 历史操作记录
- ✅ 批准/拒绝操作
- 📋 查看 SQL 命令和风险

---

## 🔍 测试场景

### 场景 1: 测试锁等待告警

1. 在 Oracle 数据库中制造锁等待:
```sql
-- 会话 1
UPDATE employees SET salary = salary * 1.1 WHERE id = 1;
-- 不提交

-- 会话 2
UPDATE employees SET salary = salary * 1.2 WHERE id = 1;
-- 会被阻塞
```

2. 等待监控巡检 (60 秒)
3. 查看邮件告警
4. 访问审批页面，查看自动生成的 Kill 命令

### 场景 2: 测试表空间告警

1. 临时调低阈值 (在 `start_monitor.py` 中):
```python
TBS_THRESHOLD = 50  # 改为 50%
```

2. 重启监控进程
3. 等待告警触发
4. 查看自动生成的扩容命令

### 场景 3: 测试基线异常检测

```python
from monitor.baseline_engine import BaselineEngine
from monitor.models import DatabaseConfig

config = DatabaseConfig.objects.get(id=1)
engine = BaselineEngine(config)

# 获取连接数基线
baseline = engine.calculate_baseline('active_connections', days=7)
print(f"基线均值：{baseline['mean']}")
print(f"正常范围：{baseline['normal_range']}")
```

---

## 🛠️ 常见问题

### Q1: 迁移失败 "Table already exists"

**解决:**
```bash
# 如果是全新部署，删除旧数据库文件
rm db.sqlite3

# 重新迁移
python manage.py makemigrations
python manage.py migrate
```

### Q2: 监控进程启动失败 "Module not found"

**解决:**
```bash
# 确保虚拟环境已激活
# 重新安装依赖
pip install -r requirements.txt
```

### Q3: 邮件发送失败

**解决:**
1. 检查 SMTP 服务器连通性:
```bash
telnet smtp.yourcompany.com 25
```

2. 临时禁用邮件发送 (调试用):
```python
# settings.py
EMAIL_BACKEND = 'django.core.mail.backends.console.EmailBackend'
# 邮件会输出到控制台
```

### Q4: Oracle 连接失败 "ORA-12514"

**解决:**
- 确认 `service_name` 正确
- 检查监听器状态: `lsnrctl status`
- 尝试在 `tnsnames.ora` 中查找正确服务名

---

## 📈 性能优化建议

### 生产环境部署

1. **使用 PostgreSQL 替代 SQLite**
```python
# settings.py
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': 'dbmonitor',
        'USER': 'dbmonitor',
        'PASSWORD': 'your_password',
        'HOST': 'localhost',
        'PORT': '5432',
    }
}
```

2. **使用 Gunicorn 运行 Django**
```bash
pip install gunicorn
gunicorn dbmonitor.wsgi:application --bind 0.0.0.0:8000 --workers 4
```

3. **使用 Supervisor 管理监控进程**
```ini
# /etc/supervisor/conf.d/dbmonitor.conf
[program:dbmonitor]
command=/path/to/venv/bin/python /path/to/manage.py start_monitor
directory=/path/to/DB_Monitor
autostart=true
autorestart=true
```

4. **调整巡检频率**
```python
# start_monitor.py
scheduler.add_job(self.monitor_job, 'interval', seconds=30)  # 30 秒
```

---

## 🎯 下一步扩展建议

### 短期 (1-2 周)
- [ ] 集成实际执行功能 (在审批后自动执行 SQL)
- [ ] 添加微信/钉钉告警通知
- [ ] 增加报表导出功能 (Excel/PDF)

### 中期 (1 个月)
- [ ] 用户权限管理系统
- [ ] 多租户支持
- [ ] 监控数据长期存储 (Prometheus/InfluxDB)

### 长期 (3 个月+)
- [ ] 机器学习异常检测
- [ ] 自动索引推荐
- [ ] SQL 优化建议生成

---

## 📞 技术支持

如有问题，请查看:
1. `README.md` - 详细使用文档
2. Django Admin - 查看监控日志和操作历史
3. 监控进程输出日志 - 实时调试信息

**祝使用愉快！🎉**
