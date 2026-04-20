"""
Celery 应用配置

使用方法：
1. 设置环境变量 USE_CELERY=true 启用 Celery 模式
2. 启动 worker: celery -A monitor.celery worker --loglevel=info
3. 启动 beat: celery -A monitor.celery beat --loglevel=info
"""

import os
from celery import Celery

# 设置 Django settings 模块
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'dbmonitor.settings')

# 创建 Celery 应用
app = Celery('db_monitor')

# 从 Django settings 加载配置
app.config_from_object('django.conf:settings', namespace='CELERY')

# 自动发现 tasks.py 中的任务
app.autodiscover_tasks(['monitor.tasks'])

# Celery Beat 定时任务配置
app.conf.beat_schedule = {
    # 采集任务：每 5 分钟执行一次全量采集
    'collect-all-databases': {
        'task': 'monitor.tasks.collect_all_databases',
        'schedule': 300.0,  # 5 分钟
    },
    # 基线重算：每天凌晨 2 点执行
    'run-baseline-calculation': {
        'task': 'monitor.tasks.run_baseline_calculation',
        'schedule': 86400.0,  # 24 小时
        'options': {
            'run_at': '02:00'  # 定时在凌晨 2 点
        }
    },
    # 容量预测：每天凌晨 3 点执行
    'run-capacity-prediction': {
        'task': 'monitor.tasks.run_capacity_prediction',
        'schedule': 86400.0,
        'options': {
            'run_at': '03:00'
        }
    },
    # 健康评分：每 6 小时执行
    'run-health-check': {
        'task': 'monitor.tasks.run_health_check',
        'schedule': 21600.0,  # 6 小时
    },
    # 告警检查：每分钟执行
    'check-alerts': {
        'task': 'monitor.tasks.check_alerts',
        'schedule': 60.0,  # 1 分钟
    },
    # 日报生成：每天凌晨 4 点执行
    'generate-daily-report': {
        'task': 'monitor.tasks.generate_daily_report',
        'schedule': 86400.0,
        'options': {
            'run_at': '04:00'
        }
    },
    # 月报生成：每月 1 号凌晨 5 点执行
    'generate-monthly-report': {
        'task': 'monitor.tasks.generate_monthly_report',
        'schedule': 86400.0 * 28,  # 约每月执行
    },
    # 日志清理：每天凌晨 6 点执行
    'cleanup-old-logs': {
        'task': 'monitor.tasks.cleanup_old_logs',
        'schedule': 86400.0,
        'options': {
            'run_at': '06:00'
        }
    },
    # 基线同步到TimescaleDB：每小时执行
    'sync-baseline-timeseries': {
        'task': 'monitor.tasks.sync_baseline_to_timeseries',
        'schedule': 3600.0,  # 1 小时
    },
}

# 任务序列化方式
app.conf.task_serializer = 'json'
app.conf.result_serializer = 'json'
app.conf.accept_content = ['json']

# 时区设置
app.conf.timezone = 'Asia/Shanghai'
app.conf.enable_utc = True

# 任务结果过期时间（秒）
app.conf.result_expires = 86400  # 24 小时

# 任务路由配置（可选，用于区分优先级）
app.conf.task_routes = {
    'monitor.tasks.collect_all_databases': {'queue': 'collect'},
    'monitor.tasks.run_baseline_calculation': {'queue': 'analytics'},
    'monitor.tasks.run_capacity_prediction': {'queue': 'analytics'},
    'monitor.tasks.run_health_check': {'queue': 'analytics'},
    'monitor.tasks.check_alerts': {'queue': 'alerts'},
}

# 任务限流配置
app.conf.task_annotations = {
    'monitor.tasks.collect_all_databases': {
        'rate_limit': '1/m',  # 每分钟最多执行一次
    }
}


@app.task(bind=True, ignore_result=True)
def debug_task(self):
    """调试任务"""
    print(f'Request: {self.request!r}')
