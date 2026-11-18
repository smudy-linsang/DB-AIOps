"""Permission-aware handoff, time-window evidence and linked run outcomes."""

import json
from datetime import timedelta
from math import isfinite

from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Q
from django.http import JsonResponse
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_exempt

from monitor.alert_lifecycle import alert_observation
from monitor.auth import Perm, has_permission, require_auth, require_permission, require_permissions
from monitor.collection_health import METRIC_EXPECTATIONS
from monitor.models import AlertLog, AuditLog, ChangeEvent, DatabaseConfig, Incident, MonitorLog
from monitor.verify_loop import _eval_recover, read_observation


def scoped_incidents(request):
    return Incident.objects.filter(config__in=DatabaseConfig.objects.visible_to(request.user))


def error(message, status):
    return JsonResponse({'message': message}, status=status)


def body(request):
    try:
        value = json.loads(request.body)
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def handoff(incident):
    return {
        'owner': incident.owner,
        'next_step': incident.next_step,
        'updated_by': incident.handoff_by,
        'updated_at': incident.handoff_at,
        'version': incident.handoff_at.isoformat() if incident.handoff_at else None,
    }


def evidence_window(incident, now):
    start = min(incident.occurred_at, now) - timedelta(minutes=30)
    end = min(now, (incident.closed_at or incident.resolved_at or now) + timedelta(minutes=30))
    # Keep the original incident in view even for a long-running incident.
    end = min(end, start + timedelta(days=7))
    return start, end


def metric_samples(incident, start, end):
    keys = list(METRIC_EXPECTATIONS.get(incident.db_type, ())) + ['blocked_sessions']
    samples = list(MonitorLog.objects.filter(config_id=incident.config_id, create_time__range=(start, end))
                   .order_by('-create_time', '-pk')[:121])
    rows = []
    for sample in reversed(samples[:120]):
        try:
            data = json.loads(sample.message) if sample.status == 'UP' else {}
        except (TypeError, ValueError):
            data = {}
        data = data if isinstance(data, dict) else {}
        values = {key: data[key] for key in keys
                  if isinstance(data.get(key), (int, float)) and not isinstance(data[key], bool)
                  and isfinite(data[key])}
        rows.append({'id': sample.pk, 'at': sample.create_time, 'status': sample.status, 'values': values})
    return {'items': rows, 'truncated': len(samples) > 120, 'source': 'collector_log'}


def run_summary(run):
    verification = run.verify_result or {}
    followup = None
    if (
        run.finished_at and verification.get('recovered') is True
        and verification.get('source') in ('collector', 'golden')
        and verification.get('metric') and verification.get('predicate')
    ):
        value, at = read_observation(
            run.incident, verification['metric'], verification['source'],
            verification.get('target'), run.finished_at,
        )
        if at and isinstance(value, (int, float)) and isfinite(value):
            followup = {
                'at': at, 'value': value,
                'passes': _eval_recover(verification['predicate'], value),
            }
    errors = [step for step in run.step_results if step.get('status') == 'fail']
    return {
        'id': run.run_id,
        'playbook': run.playbook.name,
        'status': run.status,
        'started_at': run.started_at,
        'finished_at': run.finished_at,
        'failed_steps': len(errors),
        'verification': verification,
        'followup': followup,
    }


@method_decorator(never_cache, name='dispatch')
@method_decorator(require_auth, name='dispatch')
@method_decorator(require_permission(Perm.ALERTS_VIEW), name='dispatch')
class IncidentContextView(View):
    def get(self, request, incident_id):
        incident = scoped_incidents(request).filter(incident_id=incident_id).first()
        if not incident:
            return error('事故不存在或无权访问', 404)
        start, end = evidence_window(incident, timezone.now())
        allowed_metrics = has_permission(request.user, Perm.METRICS_VIEW)
        allowed_tickets = has_permission(request.user, Perm.TICKETS_VIEW)
        events = list(incident.events.filter(config_id=incident.config_id)
                      .order_by('-occurred_at', '-pk').values(
                          'event_uid', 'source', 'signal', 'metric_key', 'value', 'threshold', 'occurred_at',
                      )[:30])
        alerts = (AlertLog.objects.filter(config_id=incident.config_id, create_time__lte=end)
                  .filter(Q(resolved_at__gte=start) | Q(status__in=AlertLog.UNRESOLVED)
                          | Q(create_time__gte=start))
                  .order_by('-create_time', '-pk')[:20])
        changes = list(ChangeEvent.objects.filter(config_id=incident.config_id, occurred_at__range=(start, end))
                       .order_by('-occurred_at', '-pk').values('id', 'source', 'title', 'occurred_at')[:20])
        tickets = None
        if allowed_tickets:
            tickets = list(incident.tickets.filter(config_id=incident.config_id).order_by('-create_time', '-pk')
                           .values('id', 'status', 'description', 'execute_time')[:20])
        runs = [run_summary(run) for run in incident.playbook_runs.select_related('playbook', 'incident__config')[:8]]
        return JsonResponse({
            'config_id': incident.config_id,
            'window': {'from': start, 'to': end},
            'handoff': handoff(incident),
            'metrics': metric_samples(incident, start, end) if allowed_metrics else None,
            'events': events,
            'alerts': [{
                'id': row.pk, 'title': row.title, 'severity': row.severity,
                'occurred_at': row.create_time, 'resolved_at': row.resolved_at,
                **alert_observation(row),
            } for row in alerts],
            'changes': changes,
            'tickets': tickets,
            'runs': runs,
        })


@method_decorator(csrf_exempt, name='dispatch')
@method_decorator(require_auth, name='dispatch')
@method_decorator(require_permissions([Perm.ALERTS_VIEW, Perm.ALERTS_ACKNOWLEDGE]), name='dispatch')
class IncidentHandoffView(View):
    def post(self, request, incident_id):
        data = body(request)
        next_step = data.get('next_step')
        owner = data.get('owner')
        if not isinstance(next_step, str) or len(next_step.strip()) > 500 or not isinstance(owner, str) or len(owner.strip()) > 150:
            return error('请提供有效的负责人和下一步，下一步最多 500 字。', 400)
        owner = owner.strip()
        with transaction.atomic():
            incident = scoped_incidents(request).select_for_update().filter(incident_id=incident_id).first()
            if not incident:
                return error('事故不存在或无权访问', 404)
            if data.get('version') != handoff(incident)['version']:
                return error('交接信息已被更新，请刷新后再提交。', 409)
            if owner:
                candidate = get_user_model().objects.filter(username=owner, is_active=True).first()
                if (not candidate or not has_permission(candidate, Perm.ALERTS_VIEW)
                        or not DatabaseConfig.objects.visible_to(candidate).filter(pk=incident.config_id).exists()):
                    return error('负责人必须是可以查看该实例和事故的有效账号。', 400)
            incident.owner = owner
            incident.next_step = next_step.strip()
            incident.handoff_at = timezone.now()
            incident.handoff_by = request.user.username
            incident.save(update_fields=['owner', 'next_step', 'handoff_at', 'handoff_by', 'updated_at'])
        return JsonResponse(handoff(incident))


@method_decorator(csrf_exempt, name='dispatch')
@method_decorator(require_auth, name='dispatch')
@method_decorator(require_permissions([Perm.ALERTS_VIEW, Perm.ALERTS_ACKNOWLEDGE, Perm.TICKETS_VIEW]), name='dispatch')
class IncidentLinkTicketView(View):
    def post(self, request, incident_id):
        incident = scoped_incidents(request).filter(incident_id=incident_id).first()
        if not incident:
            return error('事故不存在或无权访问', 404)
        ticket_id = body(request).get('ticket_id')
        if type(ticket_id) is not int or not 0 < ticket_id < 2**63:
            return error('工单编号必须为有效正整数。', 400)
        with transaction.atomic():
            ticket = (AuditLog.objects.select_for_update().filter(pk=ticket_id, config_id=incident.config_id)
                      .filter(Q(execution_context__source__in=('manual', 'ai_suggestion', 'alert'))
                              | Q(action_type__in=('manual_sql', 'KILL_SESSION', 'RESIZE_DATAFILE', 'EXECUTE_SQL',
                                                  'DROP_INDEX', 'PURGE_TABLE', 'REBALANCE_SHARD')))
                      .first())
            if not ticket:
                return error('工单不存在或不属于该实例。', 404)
            if ticket.incident_id and ticket.incident_id != incident.pk:
                return error('该工单已关联其他事故。', 409)
            if not ticket.incident_id:
                ticket.incident = incident
                ticket.save(update_fields=['incident', 'update_time'])
        return JsonResponse({'id': ticket_id, 'incident_id': incident_id, 'status': ticket.status})
