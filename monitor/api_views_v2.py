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
from monitor.models import Incident, DatabaseConfig, IncidentCauseChain
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


ACTIVE_INCIDENT_STATUSES = ('open', 'diagnosing', 'plan_ready', 'executing', 'verifying')


def _scoped_database(request, config_id):
    """按当前用户的数据范围解析实例；越权与不存在统一为 None。"""
    qs = DatabaseConfig.objects.filter(id=config_id)
    allowed = get_user_database_ids(request.user)
    if allowed is not None:
        qs = qs.filter(id__in=allowed)
    cfg = qs.first()
    if cfg is None:
        logger.warning(
            "[api-v2] 实例访问被拒 config_id=%s user=%s exists=%s",
            config_id, _username(request),
            DatabaseConfig.objects.filter(id=config_id).exists())
    return cfg


def _scoped_incident(request, incident_id):
    """按当前用户的数据范围解析事故；防止通过事故 ID 绕过实例授权。"""
    qs = Incident.objects.select_related('config').filter(incident_id=incident_id)
    allowed = get_user_database_ids(request.user)
    if allowed is not None:
        qs = qs.filter(config_id__in=allowed)
    inc = qs.first()
    if inc is None:
        logger.warning(
            "[api-v2] 事故访问被拒 incident_id=%s user=%s exists=%s",
            incident_id, _username(request),
            Incident.objects.filter(incident_id=incident_id).exists())
    return inc


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
        qs = Incident.objects.select_related('config').filter(
            status__in=ACTIVE_INCIDENT_STATUSES)
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
        inc = _scoped_incident(request, incident_id)
        if not inc:
            return self.err('NOT_FOUND', f'事故 {incident_id} 不存在', 404)

        # GET 必须无副作用：未生成 RCA 时返回空证据链，由显式重诊断命令生成。
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
        cfg = _scoped_database(request, config_id)
        if not cfg:
            return self.err('NOT_FOUND', f'数据库 {config_id} 不存在', 404)

        conn = None
        try:
            from monitor.db_connector import DbConnector
            from monitor.sentinel import sample_sessions
            from monitor.api_views_perf import _build_blocking_tree

            conn = DbConnector.get_connection(
                cfg, statement_timeout_ms=3000, readonly=True)
            cur = conn.cursor()
            try:
                rows = sample_sessions(cur, cfg.db_type, config_id=cfg.id)
            finally:
                cur.close()
            return self.ok(
                degraded=False,
                observed_at=timezone.now().isoformat(),
                root_blockers=_build_blocking_tree(rows),
            )
        except Exception as e:
            logger.warning("[api-v2] 实时阻塞图采集失败 config_id=%s: %s", cfg.id, e)
            return self.ok(
                degraded=True,
                observed_at=None,
                root_blockers=[],
                reason='目标库不可达或缺少会话/锁视图权限',
            )
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    logger.debug("[api-v2] 关闭阻塞图目标连接失败", exc_info=True)


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
        cfg = _scoped_database(request, config_id)
        if not cfg:
            return self.err('NOT_FOUND', '目标实例不存在', 404)
        if not code:
            return self.err('BAD_REQUEST', '缺少 config_id 或 playbook_code 参数', 400)

        incident = None
        if data.get('incident_id'):
            incident = _scoped_incident(request, data['incident_id'])
            if not incident or incident.config_id != cfg.id:
                return self.err('NOT_FOUND', '事故不存在或不属于目标实例', 404)
        res = PlaybookExecutor.evaluate_dryrun(
            code, cfg, data.get('params') or {}, incident=incident)
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
        cfg = _scoped_database(request, config_id)
        if not cfg:
            return self.err('NOT_FOUND', '目标实例不存在', 404)
        if not code:
            return self.err('BAD_REQUEST', '缺少 config_id 或 playbook_code 参数', 400)

        incident_id = data.get('incident_id')
        incident = _scoped_incident(request, incident_id) if incident_id else None
        if not incident or incident.config_id != cfg.id:
            return self.err('NOT_FOUND', '执行必须关联当前目标实例内的有效事故', 404)

        res = PlaybookExecutor.execute_playbook(
            code, cfg, _username(request), data.get('params') or {}, incident=incident
        )
        return self.ok(**res)
