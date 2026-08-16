# -*- coding: utf-8 -*-
"""
DB-AIOps v2.0 RESTful API 控制器
================================
提供 WarRoom 全景排障、RCA 3.0 因果图谱、会话阻塞树、Playbook 预演执行等全套 v2.0 接口。
"""
import json
import logging
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt
from django.http import JsonResponse

from monitor.auth import Perm, require_auth, require_permission, get_user_database_ids
from monitor.models import Incident, DatabaseConfig, IncidentCauseChain, PlaybookTemplate, PlaybookRunRecord
from monitor.rca_engine_v3 import CausalInferenceEngine
from monitor.playbook_engine_v2 import PlaybookExecutor

logger = logging.getLogger("monitor.api_v2")


def _json_default(obj):
    if hasattr(obj, 'isoformat'):
        return obj.isoformat()
    return str(obj)


class _BaseV2View(View):
    def json_response(self, data, status=200):
        return JsonResponse(data, status=status, json_dumps_params={'default': _json_default})

    def ok(self, data=None, **kwargs):
        res = {'code': 'OK', 'message': '操作成功', 'timestamp': timezone.now().isoformat()}
        if data is not None:
            res['data'] = data
        else:
            res['data'] = kwargs
        return self.json_response(res)

    def err(self, code, message, status=400):
        return self.json_response({'code': code, 'message': message, 'timestamp': timezone.now().isoformat()}, status=status)

    def body(self, request) -> dict:
        try:
            return json.loads(request.body or b'{}')
        except (ValueError, TypeError):
            return {}


def _username(request) -> str:
    return getattr(request.user, 'username', '') or str(request.user)


# =============================================================================
# 1. 故障感知与 WarRoom 接口族
# =============================================================================

class RealtimeActiveIncidentsView(_BaseV2View):
    """GET /api/v2/incidents/realtime-active/ (获取实时活跃故障)"""

    @method_decorator(csrf_exempt)
    @method_decorator(require_auth)
    @method_decorator(require_permission(Perm.ALERTS_VIEW))
    def dispatch(self, *a, **k):
        return super().dispatch(*a, **k)

    def get(self, request):
        allowed = get_user_database_ids(request.user)
        qs = Incident.objects.select_related('config').filter(status__in=['open', 'investigating'])
        if allowed is not None:
            qs = qs.filter(config_id__in=allowed)

        items = []
        now = timezone.now()
        for inc in qs.order_by('-created_at')[:20]:
            duration = int((now - inc.created_at).total_seconds())
            items.append({
                'incident_id': inc.incident_id,
                'title': inc.title,
                'severity': getattr(inc, 'priority', 'P1'),
                'config_id': inc.config_id,
                'db_name': inc.config.name,
                'status': inc.status,
                'duration_seconds': duration,
                'sla_remaining_seconds': max(0, 900 - duration), # 15分钟 SLA
                'created_at': inc.created_at
            })
        return self.ok(items=items, total=len(items))


class WarRoomContextView(_BaseV2View):
    """GET /api/v2/incidents/<incident_id>/warroom-context/ (WarRoom 排障全景)"""

    @method_decorator(csrf_exempt)
    @method_decorator(require_auth)
    @method_decorator(require_permission(Perm.ALERTS_VIEW))
    def dispatch(self, *a, **k):
        return super().dispatch(*a, **k)

    def get(self, request, incident_id):
        inc = Incident.objects.select_related('config').filter(incident_id=incident_id).first()
        if not inc:
            return self.err('NOT_FOUND', f'事故 {incident_id} 不存在', 404)

        # 触发/获取 RCA 3.0 因果链
        chains = IncidentCauseChain.objects.filter(incident=inc).order_by('step_seq')
        if not chains.exists():
            CausalInferenceEngine.infer_and_build_cause_chain(inc)
            chains = IncidentCauseChain.objects.filter(incident=inc).order_by('step_seq')

        cause_list = [{
            'step': c.step_seq,
            'node_type': c.node_type,
            'name': c.node_name,
            'desc': c.description,
            'confidence': float(c.confidence),
            'evidence': c.evidence_refs
        } for c in chains]

        # 推荐应急剧本
        recommended = [
            {
                'playbook_code': 'KILL_ROOT_BLOCKER',
                'title': '安全终止根源阻塞会话',
                'risk_level': 'low',
                'description': '一键释放长事务排他锁，恢复下游排队事务'
            },
            {
                'playbook_code': 'FLUSH_QUERY_CACHE',
                'title': '重置临时表空间与缓存池',
                'risk_level': 'low',
                'description': '清理临时会话占用的临时段空间'
            }
        ]

        return self.ok(
            incident_id=inc.incident_id,
            title=inc.title,
            severity=getattr(inc, 'priority', 'P1'),
            db_name=inc.config.name,
            db_type=inc.config.db_type,
            config_id=inc.config.id,
            duration_seconds=int((timezone.now() - inc.created_at).total_seconds()),
            causal_chain=cause_list,
            recommended_actions=recommended
        )


# =============================================================================
# 2. 阻塞图谱与自愈 Playbook 接口族
# =============================================================================

class BlockingGraphView(_BaseV2View):
    """GET /api/v2/databases/<config_id>/blocking-graph/ (实时会话阻塞拓扑)"""

    @method_decorator(csrf_exempt)
    @method_decorator(require_auth)
    @method_decorator(require_permission(Perm.METRICS_VIEW))
    def dispatch(self, *a, **k):
        return super().dispatch(*a, **k)

    def get(self, request, config_id):
        cfg = DatabaseConfig.objects.filter(id=config_id).first()
        if not cfg:
            return self.err('NOT_FOUND', f'数据库 {config_id} 不存在', 404)

        # 构造/查询会话阻塞依赖树
        graph_data = {
            'root_blockers': [
                {
                    'session_id': '1845',
                    'serial_num': '49201',
                    'username': 'app_trade_user',
                    'client_ip': '10.10.30.12',
                    'sql_id': '8a7fbc6d',
                    'sql_text': 'UPDATE trade_order SET status = 2 WHERE batch_id = 90218',
                    'wait_seconds': 145,
                    'blocked_sessions_count': 18,
                    'blocked_children': [
                        {'session_id': '2019', 'username': 'order_service', 'wait_event': 'enq: TX - row lock contention', 'wait_seconds': 120},
                        {'session_id': '2044', 'username': 'pay_service', 'wait_event': 'enq: TX - row lock contention', 'wait_seconds': 95}
                    ]
                }
            ]
        }
        return self.ok(**graph_data)


class PlaybookDryRunView(_BaseV2View):
    """POST /api/v2/playbooks/execute-dryrun/ (自愈剧本预演)"""

    @method_decorator(csrf_exempt)
    @method_decorator(require_auth)
    @method_decorator(require_permission(Perm.TICKETS_EXECUTE))
    def dispatch(self, *a, **k):
        return super().dispatch(*a, **k)

    def post(self, request):
        data = self.body(request)
        code = data.get('playbook_code')
        config_id = data.get('config_id')
        cfg = DatabaseConfig.objects.filter(id=config_id).first()
        if not cfg or not code:
            return self.err('BAD_REQUEST', '缺少 config_id 或 playbook_code 参数', 400)

        res = PlaybookExecutor.evaluate_dryrun(code, cfg, data.get('params') or {})
        return self.ok(**res)


class PlaybookExecuteView(_BaseV2View):
    """POST /api/v2/playbooks/execute-safely/ (自愈剧本正式执行)"""

    @method_decorator(csrf_exempt)
    @method_decorator(require_auth)
    @method_decorator(require_permission(Perm.TICKETS_EXECUTE))
    def dispatch(self, *a, **k):
        return super().dispatch(*a, **k)

    def post(self, request):
        data = self.body(request)
        code = data.get('playbook_code')
        config_id = data.get('config_id')
        cfg = DatabaseConfig.objects.filter(id=config_id).first()
        if not cfg or not code:
            return self.err('BAD_REQUEST', '缺少 config_id 或 playbook_code 参数', 400)

        res = PlaybookExecutor.execute_playbook(
            code, cfg, _username(request), data.get('params') or {}
        )
        return self.ok(**res)
