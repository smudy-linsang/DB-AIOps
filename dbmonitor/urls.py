from django.contrib import admin
from django.urls import path, re_path
from django.conf import settings
from django.views.static import serve
import os

from monitor.views_enhanced import (
    dashboard, detail, api_latest_metrics, api_baseline,
    api_intelligent_baseline, api_anomaly_detection, api_baseline_trend,
    api_rca, remediation_list, approve_operation, reject_operation,
    get_audit_detail, execute_operation, health_check, db_list, db_create,
    db_edit, db_delete, db_toggle_active,
)
from monitor.api_views import (
    HealthCheckView, LoginView, LogoutView, DatabaseListView,
    DatabaseStatusView, DatabaseMetricsView, DatabaseBaselineView,
    DatabasePredictionView, DatabaseHealthView, DatabaseAlertsView,
    AlertListView, AlertAcknowledgeView, AuditLogListView,
    AuditLogApproveView, AuditLogRejectView, AuditLogExecuteView,
    AuditLogExecuteDryRunView, UserListView, UserDetailView,
    UserPasswordView, CurrentUserView,
)
from monitor.observability import prometheus_metrics_view

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
    
    # Django template pages (not the root)
    path('dashboard/', dashboard, name='dashboard'),
    path('monitor/<int:config_id>/', detail, name='detail'),

    # ========== REST API v1 ==========
    path('api/v1/health/', HealthCheckView.as_view()),
    path('api/v1/auth/login/', LoginView.as_view()),
    path('api/v1/auth/logout/', LogoutView.as_view()),
    path('api/v1/databases/', DatabaseListView.as_view()),
    path('api/v1/databases/<int:config_id>/status/', DatabaseStatusView.as_view()),
    path('api/v1/databases/<int:config_id>/metrics/', DatabaseMetricsView.as_view()),
    path('api/v1/databases/<int:config_id>/baseline/', DatabaseBaselineView.as_view()),
    path('api/v1/databases/<int:config_id>/prediction/', DatabasePredictionView.as_view()),
    path('api/v1/databases/<int:config_id>/health/', DatabaseHealthView.as_view()),
    path('api/v1/databases/<int:config_id>/alerts/', DatabaseAlertsView.as_view()),
    path('api/v1/alerts/', AlertListView.as_view()),
    path('api/v1/alerts/<int:alert_id>/acknowledge/', AlertAcknowledgeView.as_view()),
    path('api/v1/auditlogs/', AuditLogListView.as_view()),
    path('api/v1/auditlogs/<int:audit_id>/approve/', AuditLogApproveView.as_view()),
    path('api/v1/auditlogs/<int:audit_id>/reject/', AuditLogRejectView.as_view()),
    path('api/v1/auditlogs/<int:audit_id>/execute/', AuditLogExecuteView.as_view()),
    path('api/v1/auditlogs/<int:audit_id>/dry-run/', AuditLogExecuteDryRunView.as_view()),
    path('api/v1/users/', UserListView.as_view()),
    path('api/v1/users/me/', CurrentUserView.as_view()),
    path('api/v1/users/<int:user_id>/', UserDetailView.as_view()),
    path('api/v1/users/<int:user_id>/password/', UserPasswordView.as_view()),

    # Legacy API
    path('api/metrics/<int:config_id>/', api_latest_metrics),
    path('api/baseline/<int:config_id>/', api_baseline),
    path('api/intelligent-baseline/<int:config_id>/', api_intelligent_baseline),
    path('api/anomaly-detection/<int:config_id>/', api_anomaly_detection),
    path('api/baseline-trend/<int:config_id>/', api_baseline_trend),
    path('api/rca/<int:config_id>/', api_rca),
    path('api/health/', health_check),

    # Remediation
    path('monitor/remediation/', remediation_list),
    path('monitor/remediation/<int:audit_id>/approve/', approve_operation),
    path('monitor/remediation/<int:audit_id>/reject/', reject_operation),
    path('monitor/remediation/<int:audit_id>/detail/', get_audit_detail),
    path('monitor/remediation/<int:audit_id>/execute/', execute_operation),

    # DB Config
    path('monitor/db/', db_list),
    path('monitor/db/create/', db_create),
    path('monitor/db/<int:config_id>/edit/', db_edit),
    path('monitor/db/<int:config_id>/delete/', db_delete),
    path('monitor/db/<int:config_id>/toggle/', db_toggle_active),

    # ========== Observability ==========
    path('metrics', prometheus_metrics_view, name='prometheus-metrics'),
    path('metrics/', prometheus_metrics_view, name='prometheus-metrics-slash'),
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
