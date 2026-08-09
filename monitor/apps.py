from django.apps import AppConfig


class MonitorConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'monitor'

    def ready(self):
        # 注册 appconf 启动校验（monitor.E001），使 manage.py check 覆盖配置合法性
        from monitor import checks  # noqa: F401
