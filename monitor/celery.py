"""
Celery 应用配置

使用方式：
    1. 设置环境变量 USE_CELERY=True
    2. 启动 Redis
    3. 启动 Worker: celery -A monitor worker -l info
    4. 启动 Beat: celery -A monitor beat -l info
"""

import os
from celery import Celery
from celery.schedules import crontab

# 设置 Django settings module
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'dbmonitor.settings')

app = Celery('dbmonitor')

# 从 Django settings 读取 Celery 配置
app.config_from_object('django.conf:settings', namespace='CELERY')

# 自动发现所有 app 的 tasks.py
app.autodiscover_tasks()


# ==========================================
# 定时任务调度配置
# ==========================================

app.conf.beat_schedule = {
    # 每5分钟执行一次全量采集
    'collect-all-databases': {
        'task': 'monitor.tasks.collect_all_databases',
        'schedule': 300.0,  # 5分钟
    },

    # 每日凌晨2:00重算基线
    'recalculate-baselines': {
        'task': 'monitor.tasks.recalculate_baselines',
        'schedule': crontab(hour=2, minute=0),
    },

    # 每日凌晨3:00运行容量预测
    'run-capacity-predictions': {
        'task': 'monitor.tasks.run_capacity_predictions',
        'schedule': crontab(hour=3, minute=0),
    },

    # 每小时运行健康评分
    'run-health-scoring': {
        'task': 'monitor.tasks.run_health_scoring',
        'schedule': crontab(minute=0),  # 每小时整点
    },

    # 每日8:00生成日报
    'generate-daily-report': {
        'task': 'monitor.tasks.generate_daily_report',
        'schedule': crontab(hour=8, minute=0),
    },

    # 每分钟更新平台指标
    'update-platform-metrics': {
        'task': 'monitor.tasks.update_platform_metrics',
        'schedule': 60.0,  # 1分钟
    },
}

app.conf.timezone = 'Asia/Shanghai'


@app.task(bind=True)
def debug_task(self):
    """调试任务"""
    print(f'Request: {self.request!r}')
