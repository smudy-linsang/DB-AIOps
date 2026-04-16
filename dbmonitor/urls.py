from django.contrib import admin
from django.urls import path
from monitor.views_enhanced import (
    dashboard,
    detail,
    api_latest_metrics,
    api_baseline,
    api_intelligent_baseline,
    api_anomaly_detection,
    api_baseline_trend,
    api_rca,
    remediation_list,
    approve_operation,
    reject_operation,
    get_audit_detail,
    execute_operation,
    health_check,
    db_list,
    db_create,
    db_edit,
    db_delete,
    db_toggle_active,
)

urlpatterns = [
    path('admin/', admin.site.urls),
    path('', dashboard, name='home'),
    path('dashboard/', dashboard, name='dashboard'),
    path('monitor/<int:config_id>/', detail, name='detail'),

    # API 接口
    path('api/metrics/<int:config_id>/', api_latest_metrics, name='api_metrics'),
    path('api/baseline/<int:config_id>/', api_baseline, name='api_baseline'),
    path('api/intelligent-baseline/<int:config_id>/', api_intelligent_baseline, name='api_intelligent_baseline'),
    path('api/anomaly-detection/<int:config_id>/', api_anomaly_detection, name='api_anomaly_detection'),
    path('api/baseline-trend/<int:config_id>/', api_baseline_trend, name='api_baseline_trend'),
    path('api/rca/<int:config_id>/', api_rca, name='api_rca'),
    path('api/health/', health_check, name='health_check'),

    # 运维操作审批
    path('monitor/remediation/', remediation_list, name='remediation_list'),
    path('monitor/remediation/<int:audit_id>/approve/', approve_operation, name='approve_operation'),
    path('monitor/remediation/<int:audit_id>/reject/', reject_operation, name='reject_operation'),
    path('monitor/remediation/<int:audit_id>/detail/', get_audit_detail, name='get_audit_detail'),
    path('monitor/remediation/<int:audit_id>/execute/', execute_operation, name='execute_operation'),

    # 数据库配置管理
    path('monitor/db/', db_list, name='db_list'),
    path('monitor/db/create/', db_create, name='db_create'),
    path('monitor/db/<int:config_id>/edit/', db_edit, name='db_edit'),
    path('monitor/db/<int:config_id>/delete/', db_delete, name='db_delete'),
    path('monitor/db/<int:config_id>/toggle/', db_toggle_active, name='db_toggle_active'),
]
