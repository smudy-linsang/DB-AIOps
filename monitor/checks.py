from django.core.checks import Error, register


@register()
def check_appconf(app_configs, **kwargs):
    """把配置校验挂到 manage.py check —— CI 与本地验证都会跑到。"""
    from monitor.appconf import validate_all
    return [Error(msg, id='monitor.E001') for msg in validate_all()]
