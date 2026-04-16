from django.contrib import admin
from .models import DatabaseConfig

# 自定义后台显示样式
class DatabaseConfigAdmin(admin.ModelAdmin):
    # 列表页显示的字段
    # 把 service_name 加进去
    list_display = ('name', 'db_type', 'host', 'port', 'service_name', 'is_active')
    # ... 其他不变
    # 允许搜索的字段
    search_fields = ('name', 'host')
    # 允许筛选的字段
    list_filter = ('db_type', 'is_active')

# 注册到后台
admin.site.register(DatabaseConfig, DatabaseConfigAdmin)