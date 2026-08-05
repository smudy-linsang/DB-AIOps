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
        d = _incident_full(inc)
        d['my_feedback'] = _my_feedback(inc, request)
        return self.ok(incident=d)


def _my_feedback(inc, request) -> dict:
    """Phase 8B: 当前用户已提交的反馈 (phase8/30 §3.3)。"""
    try:
        from monitor.models import PlanFeedback, RcaFeedback
        user = getattr(request.user, 'username', '') or str(request.user)
        return {
            'rca': {f.rule_id: f.verdict
                    for f in RcaFeedback.objects.filter(incident=inc, user=user)},
            'plan': {f.scenario: f.verdict
                     for f in PlanFeedback.objects.filter(incident=inc, user=user)},
        }
    except Exception:
        return {'rca': {}, 'plan': {}}


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


# ==========================================================================
# Phase 6C: Playbook 执行 / 审批 / 回滚 / 列表 (phase6/30 §9)
# ==========================================================================
import threading as _threading
from monitor.models import Playbook, PlaybookRun


def _run_async(run_id):
    def _job():
        from django.db import close_old_connections, connection as dj_conn
        close_old_connections()
        try:
            from monitor.playbook_engine import execute_run
            execute_run(run_id)
        finally:
            try:
                dj_conn.close()
            except Exception:
                pass
    _threading.Thread(target=_job, daemon=True, name=f"exec-{run_id}").start()


class IncidentExecuteView(_BaseView):
    """执行某方案: 建 PlaybookRun + 按授权策略执行 (phase6/30 §9)。"""
    @method_decorator(csrf_exempt)
    @method_decorator(require_auth)
    def dispatch(self, *a, **k):
        return super().dispatch(*a, **k)

    def post(self, request, incident_id):
        import json as _json
        import time as _time
        inc = Incident.objects.select_related('config').filter(incident_id=incident_id).first()
        if not inc:
            return self.err('NOT_FOUND', f'事故 {incident_id} 不存在', 404)
        try:
            body = _json.loads(request.body or '{}')
        except Exception:
            body = {}

        # 解析 playbook: 显式 playbook_id 或从 scenario 对应方案取 playbook_ref
        playbook_id = body.get('playbook_id')
        params = body.get('params') or {}
        if not playbook_id:
            scen = body.get('scenario', 'standard')
            plan = next((p for p in (inc.plans or []) if p.get('scenario') == scen), None)
            if not plan:
                plan = (inc.plans or [None])[0]
            if plan:
                playbook_id = plan.get('playbook_ref')
                # 从事故证据链补参数(blocker_id)
                if inc.events.exists():
                    chains = (inc.events.first().detail or {}).get('chains', [])
                    if chains and 'blocker_id' not in params:
                        params['blocker_id'] = chains[0].get('blocker')
        if not playbook_id:
            return self.err('VALIDATION', '未指定 playbook_id 且方案无 playbook_ref', 400)

        pb = Playbook.objects.filter(playbook_id=playbook_id, enabled=True).first()
        if not pb:
            return self.err('NOT_FOUND', f'Playbook {playbook_id} 不存在', 404)

        from monitor.playbook_authz import decide_trigger, record_auto_action
        decision = decide_trigger(inc, pb)

        run = PlaybookRun.objects.create(
            run_id=f"PBR-{_time.strftime('%Y%m%d%H%M%S')}-{inc.id}",
            playbook=pb, incident=inc, params=params,
            trigger_mode=decision['mode'],
            status='pending_approval' if not decision['execute_now'] else 'prechecking')

        if decision['execute_now']:
            record_auto_action(inc.config_id)
            _run_async(run.run_id)
            return self.ok(run_id=run.run_id, mode=decision['mode'],
                           executing=True, reason=decision['reason'])
        return self.ok(run_id=run.run_id, mode=decision['mode'],
                       executing=False, need_approval=True, reason=decision['reason'])


class PlaybookRunDetailView(_BaseView):
    @method_decorator(csrf_exempt)
    @method_decorator(require_auth)
    def dispatch(self, *a, **k):
        return super().dispatch(*a, **k)

    def get(self, request, run_id):
        run = PlaybookRun.objects.select_related('playbook', 'incident').filter(run_id=run_id).first()
        if not run:
            return self.err('NOT_FOUND', f'执行 {run_id} 不存在', 404)
        return self.ok(run={
            'run_id': run.run_id, 'playbook_id': run.playbook.playbook_id,
            'incident_id': run.incident.incident_id, 'status': run.status,
            'trigger_mode': run.trigger_mode, 'approved_by': run.approved_by,
            'params': run.params, 'step_results': run.step_results,
            'verify_result': run.verify_result, 'error_message': run.error_message,
            'started_at': run.started_at, 'finished_at': run.finished_at,
        })


class PlaybookRunApproveView(_BaseView):
    @method_decorator(csrf_exempt)
    @method_decorator(require_auth)
    def dispatch(self, *a, **k):
        return super().dispatch(*a, **k)

    def post(self, request, run_id):
        run = PlaybookRun.objects.filter(run_id=run_id).first()
        if not run:
            return self.err('NOT_FOUND', f'执行 {run_id} 不存在', 404)
        if run.status != 'pending_approval':
            return self.err('CONFLICT', f'当前状态 {run.status} 不可审批', 409)
        run.approved_by = getattr(request.user, 'username', 'unknown')
        run.trigger_mode = 'approved'
        run.status = 'prechecking'
        run.save(update_fields=['approved_by', 'trigger_mode', 'status'])
        _run_async(run.run_id)
        return self.ok(run_id=run.run_id, status='prechecking')


class PlaybookRunRollbackView(_BaseView):
    @method_decorator(csrf_exempt)
    @method_decorator(require_auth)
    def dispatch(self, *a, **k):
        return super().dispatch(*a, **k)

    def post(self, request, run_id):
        run = PlaybookRun.objects.select_related('playbook', 'incident', 'incident__config').filter(run_id=run_id).first()
        if not run:
            return self.err('NOT_FOUND', f'执行 {run_id} 不存在', 404)
        from monitor.db_connector import DbConnector
        from monitor.playbook_engine import _do_rollback
        conn = None
        try:
            conn = DbConnector.get_connection(run.incident.config)
            sr = list(run.step_results or [])
            _do_rollback(conn, run.incident.db_type, run.playbook, run.params or {}, sr)
            run.step_results = sr  # 4NF: setter 已写入 PlaybookRunStepResult 子表
            run.status = 'rolled_back'
            run.save(update_fields=['status'])
            return self.ok(run_id=run.run_id, status='rolled_back')
        except Exception as e:
            logger.exception("[incident] 回滚失败 run_id=%s", run_id)
            return self.err('INTERNAL', f'回滚失败: {e}', 500)
        finally:
            if conn:
                try:
                    conn.close()
                except Exception:
                    pass


class PlaybookListView(_BaseView):
    @method_decorator(csrf_exempt)
    @method_decorator(require_auth)
    def dispatch(self, *a, **k):
        return super().dispatch(*a, **k)

    def get(self, request):
        qs = Playbook.objects.filter(enabled=True)
        rows = [{
            'playbook_id': p.playbook_id, 'name': p.name, 'category': p.category,
            'signal': p.signal, 'risk_level': p.risk_level, 'auto_execute': p.auto_execute,
            'applicable_db_types': p.applicable_db_types, 'est_minutes': p.est_minutes,
        } for p in qs]
        return self.ok(total=len(rows), playbooks=rows)


# ==========================================================================
# Phase 6C: SLA 报表 / 值班表 (phase6/30 §8/§9)
# ==========================================================================
class SlaReportView(_BaseView):
    @method_decorator(csrf_exempt)
    @method_decorator(require_auth)
    def dispatch(self, *a, **k):
        return super().dispatch(*a, **k)

    def get(self, request):
        from monitor.sla_report import build_sla_report
        from django.utils.dateparse import parse_datetime
        frm = parse_datetime(request.GET.get('from', '')) if request.GET.get('from') else None
        to = parse_datetime(request.GET.get('to', '')) if request.GET.get('to') else None
        rep = build_sla_report(frm, to, request.GET.get('category'))
        return self.ok(**rep)


class OnCallView(_BaseView):
    @method_decorator(csrf_exempt)
    @method_decorator(require_auth)
    def dispatch(self, *a, **k):
        return super().dispatch(*a, **k)

    def get(self, request):
        from monitor.models import OnCallSchedule
        from monitor.oncall import get_current_oncall
        rows = [{
            'id': s.id, 'name': s.name, 'user': s.user,
            'contact_dingtalk': s.contact_dingtalk, 'contact_wecom': s.contact_wecom,
            'contact_phone': s.contact_phone, 'weekday_mask': s.weekday_mask,
            'start_hour': s.start_hour, 'end_hour': s.end_hour,
            'escalate_to': s.escalate_to, 'enabled': s.enabled,
        } for s in OnCallSchedule.objects.all()]
        cur = get_current_oncall()
        return self.ok(total=len(rows), schedules=rows,
                       current_oncall=cur.user if cur else None)

    def post(self, request):
        from monitor.models import OnCallSchedule
        try:
            body = json.loads(request.body or '{}')
        except Exception:
            body = {}
        if not body.get('name') or not body.get('user'):
            return self.err('VALIDATION', 'name 和 user 必填', 400)
        sc = OnCallSchedule.objects.create(
            name=body['name'], user=body['user'],
            contact_dingtalk=body.get('contact_dingtalk', ''),
            contact_wecom=body.get('contact_wecom', ''),
            contact_phone=body.get('contact_phone', ''),
            weekday_mask=int(body.get('weekday_mask', 127)),
            start_hour=int(body.get('start_hour', 0)),
            end_hour=int(body.get('end_hour', 24)),
            escalate_to=body.get('escalate_to', ''))
        return self.ok(id=sc.id)
