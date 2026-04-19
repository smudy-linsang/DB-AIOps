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
from monitor.api_views import (
    HealthCheckView,
    DatabaseListView,
    DatabaseStatusView,
    DatabaseMetricsView,
    DatabaseBaselineView,
    DatabasePredictionView,
    DatabaseHealthView,
    AlertListView,
    AlertAcknowledgeView,
    AuditLogListView,
    AuditLogApproveView,
    AuditLogRejectView,
)

urlpatterns = [
    path('admin/', admin.site.urls),
    path('', dashboard, name='home'),
    path('dashboard/', dashboard, name='dashboard'),
    path('monitor/<int:config_id>/', detail, name='detail'),

    # ========== REST API v1 (Phase 3.6) ==========
    # 平台健康检查
    path('api/v1/health/', HealthCheckView.as_view(), name='api_v1_health'),
    
    # 数据库配置
    path('api/v1/databases/', DatabaseListView.as_view(), name='api_v1_database_list'),
    path('api/v1/databases/<int:config_id>/status/', DatabaseStatusView.as_view(), name='api_v1_database_status'),
    path('api/v1/databases/<int:config_id>/metrics/', DatabaseMetricsView.as_view(), name='api_v1_database_metrics'),
    path('api/v1/databases/<int:config_id>/baseline/', DatabaseBaselineView.as_view(), name='api_v1_database_baseline'),
    path('api/v1/databases/<int:config_id>/prediction/', DatabasePredictionView.as_view(), name='api_v1_database_prediction'),
    path('api/v1/databases/<int:config_id>/health/', DatabaseHealthView.as_view(), name='api_v1_database_health'),
    
    # 告警管理
    path('api/v1/alerts/', AlertListView.as_view(), name='api_v1_alert_list'),
    path('api/v1/alerts/<int:alert_id>/acknowledge/', AlertAcknowledgeView.as_view(), name='api_v1_alert_acknowledge'),
    
    # 运维工单
    path('api/v1/auditlogs/', AuditLogListView.as_view(), name='api_v1_auditlog_list'),
    path('api/v1/auditlogs/<int:audit_id>/approve/', AuditLogApproveView.as_view(), name='api_v1_auditlog_approve'),
    path('api/v1/auditlogs/<int:audit_id>/reject/', AuditLogRejectView.as_view(), name='api_v1_auditlog_reject'),
    # ========== END REST API v1 ==========

    # API 接口（旧版，兼容）
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
