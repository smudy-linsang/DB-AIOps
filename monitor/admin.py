from django.contrib import admin

from monitor.crypto import is_encrypted

from .models import DatabaseConfig


class DatabaseConfigAdmin(admin.ModelAdmin):
    # 列表页显示的字段
    # 把 service_name 加进去
    list_display = ('name', 'db_type', 'host', 'port', 'service_name', 'is_active')
    # ... 其他不变
    # 允许搜索的字段
    search_fields = ('name', 'host')
    # 允许筛选的字段
    list_filter = ('db_type', 'is_active')

    def save_model(self, request, obj, form, change):
        if 'password' in form.changed_data:
            raw = form.cleaned_data.get('password') or ''
            if raw and not is_encrypted(raw):
                obj.set_password(raw)
        super().save_model(request, obj, form, change)


admin.site.register(DatabaseConfig, DatabaseConfigAdmin)