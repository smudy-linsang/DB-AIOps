# -*- coding: utf-8 -*-
"""
Phase 6A: 事故/事件 API (phase6/10 §7)。
契约见 phase6/10 §7；响应字段名为前后端唯一契约源。
"""
import json
import logging

from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt

from monitor.auth import require_auth, get_user_database_ids
from monitor.models import Incident, Event, IncidentStateError

logger = logging.getLogger(__name__)


def _json_default(obj):
    if hasattr(obj, 'isoformat'):
        return obj.isoformat()
    return str(obj)


class _BaseView(View):
    def json_response(self, data, status=200):
        from django.http import JsonResponse
        return JsonResponse(data, status=status, json_dumps_params={'default': _json_default})

    def ok(self, **kw):
        return self.json_response(dict(code='OK', **kw))

    def err(self, code, message, status=400):
        return self.json_response({'code': code, 'message': message}, status=status)


def _incident_brief(inc: Incident) -> dict:
    return {
        'incident_id': inc.incident_id,
        'config_id': inc.config_id,
        'db_name': inc.config.name if inc.config_id else '',
        'db_type': inc.db_type,
        'category': inc.category,
        'title': inc.title,
        'priority': inc.priority,
        'status': inc.status,
        'event_count': inc.event_count,
        'is_storm': inc.is_storm,
        'is_flapping': inc.is_flapping,
        'occurred_at': inc.occurred_at,
        'detected_at': inc.detected_at,
        't_detect_sec': inc.t_detect_sec,
        'sla_detect_ok': inc.sla_detect_ok,
        'acked_by': inc.acked_by,
        'created_at': inc.created_at,
    }


def _incident_full(inc: Incident) -> dict:
    d = _incident_brief(inc)
    d.update({
        'plan_ready_at': inc.plan_ready_at,
        'resolved_at': inc.resolved_at,
        'acked_at': inc.acked_at,
        't_plan_sec': inc.t_plan_sec,
        'sla_plan_ok': inc.sla_plan_ok,
        't_resolve_sec': inc.t_resolve_sec,
        'sla_resolve_ok': inc.sla_resolve_ok,
        'health_snapshot': inc.health_snapshot,
        'rca_result': inc.rca_result or {},
        'impact': inc.impact or {},
        'plans': inc.plans or [],
    })
    return d


def _rbac_qs(qs, request):
    allowed = get_user_database_ids(request.user)
    if allowed is not None:
        qs = qs.filter(config_id__in=allowed)
    return qs


class IncidentListView(_BaseView):
    @method_decorator(csrf_exempt)
    @method_decorator(require_auth)
    def dispatch(self, *a, **k):
        return super().dispatch(*a, **k)

    def get(self, request):
        qs = Incident.objects.select_related('config').all()
        for f in ('status', 'priority', 'category'):
            v = request.GET.get(f)
            if v:
                qs = qs.filter(**{f: v})
        cid = request.GET.get('config_id')
        if cid:
            qs = qs.filter(config_id=cid)
        qs = _rbac_qs(qs, request)
        limit = int(request.GET.get('limit', 50))
        rows = [_incident_brief(i) for i in qs[:limit]]
        return self.ok(total=len(rows), incidents=rows)


class IncidentDetailView(_BaseView):
    @method_decorator(csrf_exempt)
    @method_decorator(require_auth)
    def dispatch(self, *a, **k):
        return super().dispatch(*a, **k)

    def get(self, request, incident_id):
        inc = Incident.objects.select_related('config').filter(incident_id=incident_id).first()
        if not inc:
            return self.err('NOT_FOUND', f'事故 {incident_id} 不存在', 404)
        return self.ok(incident=_incident_full(inc))


class IncidentTimelineView(_BaseView):
    @method_decorator(csrf_exempt)
    @method_decorator(require_auth)
    def dispatch(self, *a, **k):
        return super().dispatch(*a, **k)

    def get(self, request, incident_id):
        inc = Incident.objects.filter(incident_id=incident_id).first()
        if not inc:
            return self.err('NOT_FOUND', f'事故 {incident_id} 不存在', 404)
        items = []
        for e in inc.events.all().order_by('occurred_at'):
            items.append({'kind': 'event', 'at': e.occurred_at, 'signal': e.signal,
                          'severity': e.severity, 'detail': e.detail})
        # 状态转移(来自 AuditLog, action_type=incident_transition, 描述含 incident_id)
        try:
            from monitor.models import AuditLog
            for a in AuditLog.objects.filter(
                    config=inc.config, action_type='incident_transition',
                    description__contains=inc.incident_id).order_by('create_time'):
                items.append({'kind': 'status', 'at': a.create_time, 'text': a.description})
        except Exception:
            pass
        items.sort(key=lambda x: (x['at'] or timezone.now()))
        return self.ok(items=items)


class IncidentAckView(_BaseView):
    @method_decorator(csrf_exempt)
    @method_decorator(require_auth)
    def dispatch(self, *a, **k):
        return super().dispatch(*a, **k)

    def post(self, request, incident_id):
        inc = Incident.objects.filter(incident_id=incident_id).first()
        if not inc:
            return self.err('NOT_FOUND', f'事故 {incident_id} 不存在', 404)
        if inc.status in ('closed',):
            return self.err('CONFLICT', '事故已关闭, 无法确认', 409)
        actor = getattr(request.user, 'username', 'unknown')
        inc.acked_at = timezone.now()
        inc.acked_by = actor
        inc.save(update_fields=['acked_at', 'acked_by', 'updated_at'])
        return self.ok(status=inc.status, acked_at=inc.acked_at, acked_by=inc.acked_by)


class IncidentCloseView(_BaseView):
    @method_decorator(csrf_exempt)
    @method_decorator(require_auth)
    def dispatch(self, *a, **k):
        return super().dispatch(*a, **k)

    def post(self, request, incident_id):
        inc = Incident.objects.filter(incident_id=incident_id).first()
        if not inc:
            return self.err('NOT_FOUND', f'事故 {incident_id} 不存在', 404)
        try:
            body = json.loads(request.body or '{}')
        except Exception:
            body = {}
        reason = body.get('reason', '手动关闭')
        actor = getattr(request.user, 'username', 'unknown')
        try:
            inc.transition('closed', actor, reason)
        except IncidentStateError as e:
            return self.err('CONFLICT', str(e), 409)
        return self.ok(status=inc.status, closed_at=inc.closed_at)


class IncidentRediagnoseView(_BaseView):
    """Phase 6B: 手动重新诊断 (phase6/20 §8.2)。"""
    @method_decorator(csrf_exempt)
    @method_decorator(require_auth)
    def dispatch(self, *a, **k):
        return super().dispatch(*a, **k)

    def post(self, request, incident_id):
        inc = Incident.objects.filter(incident_id=incident_id).first()
        if not inc:
            return self.err('NOT_FOUND', f'事故 {incident_id} 不存在', 404)
        if inc.status in ('resolved', 'closed'):
            return self.err('CONFLICT', '事故已结束, 无法重诊断', 409)
        try:
            from monitor.redis_bus import emit_diagnosis
            emit_diagnosis(inc.incident_id, inc.config_id, 'replan')
        except Exception as e:
            return self.err('INTERNAL', f'诊断入队失败: {e}', 500)
        return self.ok(status=inc.status)


class EventListView(_BaseView):
    @method_decorator(csrf_exempt)
    @method_decorator(require_auth)
    def dispatch(self, *a, **k):
        return super().dispatch(*a, **k)

    def get(self, request):
        qs = Event.objects.select_related('config').all()
        for f in ('signal',):
            v = request.GET.get(f)
            if v:
                qs = qs.filter(**{f: v})
        cid = request.GET.get('config_id')
        if cid:
            qs = qs.filter(config_id=cid)
        iid = request.GET.get('incident_id')
        if iid:
            qs = qs.filter(incident__incident_id=iid)
        qs = _rbac_qs(qs, request)
        limit = int(request.GET.get('limit', 50))
        rows = [{
            'event_uid': e.event_uid, 'config_id': e.config_id, 'db_type': e.db_type,
            'source': e.source, 'signal': e.signal, 'metric_key': e.metric_key,
            'value': e.value, 'severity': e.severity, 'occurred_at': e.occurred_at,
            'incident_id': e.incident.incident_id if e.incident_id else None,
            'detail': e.detail,
        } for e in qs[:limit]]
        return self.ok(total=len(rows), events=rows)
