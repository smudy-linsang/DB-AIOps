from django.contrib import admin
from django.urls import path, re_path
from django.conf import settings
from django.views.static import serve
import os

from monitor.views_enhanced import (
    api_latest_metrics, api_baseline,
    api_intelligent_baseline, api_anomaly_detection, api_baseline_trend,
    api_rca,
    approve_operation, reject_operation,
    get_audit_detail, execute_operation, health_check, db_toggle_active,
)
from monitor.api_views import (
    HealthCheckView, LoginView, LogoutView, DatabaseListView,
    DatabaseTestConnectionView, DatabaseConfigDetailView,
    DatabaseStatusView, DatabaseMetricsView,
    DatabaseBaselineView, DatabasePredictionView, DatabaseHealthView,
    DatabaseAlertsView, AlertListView, AlertAcknowledgeView,
    AuditLogListView, AuditLogApproveView, AuditLogRejectView,
    AuditLogExecuteView, AuditLogExecuteDryRunView, UserListView,
    UserDetailView, UserPasswordView, CurrentUserView,
    AlertAvailableMetricsView, AlertDeleteView,
    AlertTemplateGroupListView, AlertTemplateGroupDetailView,
    AlertTemplateRuleListView, AlertTemplateRuleDetailView,
    AlertTemplateRuleBatchToggleView,
    DatabaseAlertOverrideListView, DatabaseAlertOverrideDetailView,
    DatabaseTemplateAssignmentView,
    DatabaseSlowQueriesView, DatabaseSlowQueryAnalysisView,
    DatabaseSQLTextSearchView,
    # Dashboard & 补充 API
    DashboardStatsView, DashboardChartsView,
    DashboardHealthTrendView, DashboardAlertTrendView,
    AlertStatisticsView, DatabasePerformanceHubView,
    DatabaseMetricsHistoryView,
    # Phase 4 API
    SilenceWindowListView, SilenceWindowDetailView,
    NotificationRuleListView, NotificationRuleDetailView,
    AlertNotificationLogView,
    BusinessSystemListView, BusinessSystemDetailView,
    DatabaseTopologyView, DatabaseImpactView,
    ReportListView, ReportDownloadView,
)
from monitor.sse_views import SSEView
from monitor.observability import prometheus_metrics_view
from monitor.healthcheck import PlatformHealthCheckView

FRONTEND_DIST = os.path.join(settings.BASE_DIR, 'frontend', 'dist')

def frontend_index(request, path=''):
    """Serve React frontend index.html"""
    from django.http import FileResponse
    index_path = os.path.join(FRONTEND_DIST, 'index.html')
    if os.path.exists(index_path):
        return FileResponse(open(index_path, 'rb'), content_type='text/html')
    from django.http import HttpResponse
    return HttpResponse('Frontend not built. Run: cd frontend && npm run build', status=503)

def serve_frontend_assets(request, path):
    """Serve frontend static assets"""
    from django.http import FileResponse, HttpResponseNotFound
    file_path = os.path.join(FRONTEND_DIST, 'assets', path)
    if os.path.exists(file_path):
        return FileResponse(open(file_path, 'rb'))
    return HttpResponseNotFound('Not found')

urlpatterns = [
    # Admin
    path('admin/', admin.site.urls),

    # ========== REST API v1 ==========
    path('api/v1/health/', HealthCheckView.as_view()),
    path('api/v1/auth/login/', LoginView.as_view()),
    path('api/v1/auth/logout/', LogoutView.as_view()),
    path('api/v1/databases/', DatabaseListView.as_view()),
    path('api/v1/databases/test-connection/', DatabaseTestConnectionView.as_view()),
    path('api/v1/databases/<int:config_id>/', DatabaseConfigDetailView.as_view()),
    path('api/v1/databases/<int:config_id>/status/', DatabaseStatusView.as_view()),
    path('api/v1/databases/<int:config_id>/metrics/', DatabaseMetricsView.as_view()),
    path('api/v1/databases/<int:config_id>/baseline/', DatabaseBaselineView.as_view()),
    path('api/v1/databases/<int:config_id>/prediction/', DatabasePredictionView.as_view()),
    path('api/v1/databases/<int:config_id>/health/', DatabaseHealthView.as_view()),
    path('api/v1/databases/<int:config_id>/alerts/', DatabaseAlertsView.as_view()),
    path('api/v1/databases/<int:config_id>/slow-queries/', DatabaseSlowQueriesView.as_view()),
    path('api/v1/databases/<int:config_id>/slow-queries/analysis/', DatabaseSlowQueryAnalysisView.as_view()),
    path('api/v1/databases/<int:config_id>/slow-queries/search/', DatabaseSQLTextSearchView.as_view()),
    path('api/v1/databases/<int:config_id>/performance-hub/', DatabasePerformanceHubView.as_view()),
    path('api/v1/databases/<int:config_id>/metrics/history/', DatabaseMetricsHistoryView.as_view()),
    path('api/v1/alerts/', AlertListView.as_view()),
    path('api/v1/alerts/statistics/', AlertStatisticsView.as_view()),
    path('api/v1/alerts/<int:alert_id>/acknowledge/', AlertAcknowledgeView.as_view()),
    path('api/v1/alerts/<int:alert_id>/', AlertDeleteView.as_view()),
    path('api/v1/auditlogs/', AuditLogListView.as_view()),
    path('api/v1/auditlogs/<int:audit_id>/approve/', AuditLogApproveView.as_view()),
    path('api/v1/auditlogs/<int:audit_id>/reject/', AuditLogRejectView.as_view()),
    path('api/v1/auditlogs/<int:audit_id>/execute/', AuditLogExecuteView.as_view()),
    path('api/v1/auditlogs/<int:audit_id>/dry-run/', AuditLogExecuteDryRunView.as_view()),
    path('api/v1/users/', UserListView.as_view()),
    path('api/v1/users/me/', CurrentUserView.as_view()),
    path('api/v1/users/<int:user_id>/', UserDetailView.as_view()),
    path('api/v1/users/<int:user_id>/password/', UserPasswordView.as_view()),

    # Alert rule templates (multi-template Phase 3)
    path('api/v1/alert-rules/available-metrics/', AlertAvailableMetricsView.as_view()),

    # 告警模板组 CRUD
    path('api/v1/alert-templates/', AlertTemplateGroupListView.as_view()),
    path('api/v1/alert-templates/<int:template_id>/', AlertTemplateGroupDetailView.as_view()),

    # 模板组内规则管理
    path('api/v1/alert-templates/<int:template_id>/rules/', AlertTemplateRuleListView.as_view()),
    path('api/v1/alert-templates/<int:template_id>/rules/<int:rule_id>/', AlertTemplateRuleDetailView.as_view()),
    path('api/v1/alert-templates/<int:template_id>/rules/batch-toggle/', AlertTemplateRuleBatchToggleView.as_view()),

    # 数据库模板分配
    path('api/v1/databases/<int:config_id>/assigned-template/', DatabaseTemplateAssignmentView.as_view()),
    path('api/v1/databases/<int:config_id>/assign-template/', DatabaseTemplateAssignmentView.as_view()),

    # Per-database alert overrides
    path('api/v1/databases/<int:config_id>/alert-overrides/', DatabaseAlertOverrideListView.as_view()),
    path('api/v1/databases/<int:config_id>/alert-overrides/<str:metric_key>/', DatabaseAlertOverrideDetailView.as_view()),

    # Legacy API（向后兼容，新功能请在 api_views.py 中用 CBV 实现）
    path('api/metrics/<int:config_id>/', api_latest_metrics),
    path('api/baseline/<int:config_id>/', api_baseline),
    path('api/intelligent-baseline/<int:config_id>/', api_intelligent_baseline),
    path('api/anomaly-detection/<int:config_id>/', api_anomaly_detection),
    path('api/baseline-trend/<int:config_id>/', api_baseline_trend),
    path('api/rca/<int:config_id>/', api_rca),
    path('api/health/', health_check),

    # 自愈审批 JSON API
    path('api/v1/remediation/<int:audit_id>/approve/', approve_operation),
    path('api/v1/remediation/<int:audit_id>/reject/', reject_operation),
    path('api/v1/remediation/<int:audit_id>/detail/', get_audit_detail),
    path('api/v1/remediation/<int:audit_id>/execute/', execute_operation),

    # 数据库启停切换
    path('api/v1/databases/<int:config_id>/toggle-active/', db_toggle_active),

    # ========== Dashboard API ==========
    path('api/v1/dashboard/stats/', DashboardStatsView.as_view()),
    path('api/v1/dashboard/charts/', DashboardChartsView.as_view()),
    path('api/v1/dashboard/health-trend/', DashboardHealthTrendView.as_view()),
    path('api/v1/dashboard/alert-trend/', DashboardAlertTrendView.as_view()),

    # ========== SSE 实时推送 ==========
    path('api/v1/events/', SSEView.as_view()),

    # ========== Phase 4: 告警通知增强 ==========
    path('api/v1/silence-windows/', SilenceWindowListView.as_view()),
    path('api/v1/silence-windows/<int:pk>/', SilenceWindowDetailView.as_view()),
    path('api/v1/notification-rules/', NotificationRuleListView.as_view()),
    path('api/v1/notification-rules/<int:pk>/', NotificationRuleDetailView.as_view()),
    path('api/v1/alerts/<int:alert_id>/notifications/', AlertNotificationLogView.as_view()),

    # ========== Phase 4: 业务系统 ==========
    path('api/v1/business-systems/', BusinessSystemListView.as_view()),
    path('api/v1/business-systems/<int:pk>/', BusinessSystemDetailView.as_view()),

    # ========== Phase 4: 数据库拓扑与影响分析 ==========
    path('api/v1/databases/<int:config_id>/topology/', DatabaseTopologyView.as_view()),
    path('api/v1/databases/<int:config_id>/impact/', DatabaseImpactView.as_view()),

    # ========== Phase 4: 报表 ==========
    path('api/v1/reports/', ReportListView.as_view()),
    path('api/v1/reports/<int:pk>/download/', ReportDownloadView.as_view()),

    # ========== Observability ==========
    path('metrics', prometheus_metrics_view, name='prometheus-metrics'),
    path('metrics/', prometheus_metrics_view, name='prometheus-metrics-slash'),

    # ========== 自监控健康检查（供 Docker/K8s 探活） ==========
    path('healthcheck/', PlatformHealthCheckView.as_view(), name='platform-healthcheck'),
    path('healthcheck', PlatformHealthCheckView.as_view(), name='platform-healthcheck-no-slash'),
]

# Frontend routes - serve React app
if os.path.exists(FRONTEND_DIST):
    # Assets files
    urlpatterns += [
        re_path(r'^assets/(?P<path>.*)$', serve_frontend_assets),
    ]
    # Frontend routes - serve React app (使用 re_path 匹配所有路径，包括 /databases, /alerts 等)
    urlpatterns += [
        path('login/', frontend_index),
        re_path(r'^(?P<path>.*)$', frontend_index),
    ]
