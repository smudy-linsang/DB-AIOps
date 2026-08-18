# -*- coding: utf-8 -*-
"""
Phase 8: AI 智能化 REST API (phase8/30 §2-3)。

12 个端点: 反馈(8B) / Agent排查(8C) / 变更流(8D) / AI运营统计 /
自治等级(8E) / 因果图(8D) / LLM连通测试(8A)。
错误码沿用 phase8/30 §1 专段: 8001-8006。
"""
import json
import logging
import threading
import time

from django.conf import settings
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt

from monitor.auth import Perm, get_user_database_ids, require_auth, require_permission

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

    def body(self, request) -> dict:
        try:
            return json.loads(request.body or b'{}')
        except (ValueError, TypeError):
            return {}


def _username(request) -> str:
    return getattr(request.user, 'username', '') or str(request.user)


def _get_incident(request, incident_id):
    """按 RBAC 取事故, 无权/不存在返回 None。"""
    from monitor.models import Incident
    inc = Incident.objects.select_related('config').filter(incident_id=incident_id).first()
    if not inc:
        return None
    allowed = get_user_database_ids(request.user)
    if allowed is not None and inc.config_id not in allowed:
        return None
    return inc


def _get_config(request, config_id):
    """按调用者数据范围解析实例；越权与不存在一律返回 None。

    统一走 DatabaseConfig.objects.visible_to()，不再各自拼 allowed 判断 ——
    数据范围只保留一个原语，才不会"换个楼层又忘记过滤"。
    """
    from monitor.models import DatabaseConfig
    return DatabaseConfig.objects.visible_to(request.user).filter(id=config_id).first()


# =============================================================================
# 8B: 反馈接口
# =============================================================================

class RcaFeedbackView(_BaseView):
    """POST /api/v1/incidents/<incident_id>/rca-feedback/ (phase8/30 §3.1)"""

    @method_decorator(csrf_exempt)
    @method_decorator(require_auth)
    @method_decorator(require_permission(Perm.ALERTS_ACKNOWLEDGE))
    def dispatch(self, *a, **k):
        return super().dispatch(*a, **k)

    def post(self, request, incident_id):
        from monitor.models import RcaFeedback
        inc = _get_incident(request, incident_id)
        if not inc:
            return self.err('8003', f'事故 {incident_id} 不存在或无权访问', 404)

        data = self.body(request)
        rule_id = (data.get('rule_id') or '').strip()
        verdict = data.get('verdict')
        if verdict not in ('correct', 'wrong', 'partial'):
            return self.err('8004', 'verdict 须为 correct/wrong/partial', 400)

        roots = (inc.rca_result or {}).get('root_causes') or []
        matched = next((r for r in roots if r.get('rule_id') == rule_id), None)
        if not rule_id or not matched:
            return self.err('8004', f'rule_id {rule_id} 不在该事故的诊断结果中', 400)

        fb, _created = RcaFeedback.objects.update_or_create(
            incident=inc, rule_id=rule_id, user=_username(request),
            defaults={
                'verdict': verdict,
                'source': matched.get('source', 'rules'),
                'actual_cause': (data.get('comment') or data.get('actual_cause') or '')[:500],
            })

        # 确认准确时联动案例库 record_success (提升被引用案例权重)
        bumped = False
        if verdict == 'correct':
            case_id = _referenced_case_id(inc)
            if case_id:
                try:
                    from monitor.case_rag import CaseRag
                    bumped = CaseRag().record_success(case_id) > 0
                except Exception as e:
                    logger.debug("record_success 失败: %s", e)
        return self.ok(feedback_id=fb.id, case_success_bumped=bumped)


def _referenced_case_id(inc) -> str:
    """从 rca_result 中提取被引用的相似案例 case_id (多路径兼容)。"""
    rca = inc.rca_result or {}
    cases = rca.get('similar_cases') or []
    if cases and isinstance(cases, list):
        return (cases[0] or {}).get('case_id', '')
    top = (rca.get('case_match') or {})
    return top.get('case_id', '')


class PlanFeedbackView(_BaseView):
    """POST /api/v1/incidents/<incident_id>/plan-feedback/ (phase8/30 §3.2)"""

    @method_decorator(csrf_exempt)
    @method_decorator(require_auth)
    @method_decorator(require_permission(Perm.ALERTS_ACKNOWLEDGE))
    def dispatch(self, *a, **k):
        return super().dispatch(*a, **k)

    def post(self, request, incident_id):
        from monitor.models import PlanFeedback
        inc = _get_incident(request, incident_id)
        if not inc:
            return self.err('8003', f'事故 {incident_id} 不存在或无权访问', 404)

        data = self.body(request)
        scenario = (data.get('scenario') or data.get('plan_id') or '').strip()
        verdict = data.get('verdict')
        # 接口层 useless 归一到模型枚举 adopted_failed
        if verdict == 'useless':
            verdict = 'adopted_failed'
        if verdict not in ('adopted', 'adopted_failed', 'rejected'):
            return self.err('8004', 'verdict 须为 adopted/adopted_failed/rejected/useless', 400)
        if not scenario:
            return self.err('8004', 'scenario(方案场景) 必填', 400)

        fb, _created = PlanFeedback.objects.update_or_create(
            incident=inc, scenario=scenario[:20], user=_username(request),
            defaults={'verdict': verdict, 'comment': (data.get('comment') or '')[:500]})
        return self.ok(feedback_id=fb.id)


# =============================================================================
# 8C: Agent 深度排查
# =============================================================================

class InvestigateView(_BaseView):
    """POST /api/v1/incidents/<incident_id>/investigate/ (phase8/30 §3.4)"""

    @method_decorator(csrf_exempt)
    @method_decorator(require_auth)
    @method_decorator(require_permission(Perm.TICKETS_EXECUTE))
    def dispatch(self, *a, **k):
        return super().dispatch(*a, **k)

    def post(self, request, incident_id):
        from monitor.llm import agent_enabled
        from monitor.models import AgentTrace
        if not agent_enabled():
            return self.err('8001', 'Agent 排查未启用 (AGENT_ENABLED=False)', 400)
        inc = _get_incident(request, incident_id)
        if not inc:
            return self.err('8003', f'事故 {incident_id} 不存在或无权访问', 404)

        # 并发互斥: 10 分钟内已有 running 轨迹则拒绝
        stale_cutoff = timezone.now() - timezone.timedelta(minutes=10)
        running = AgentTrace.objects.filter(
            incident=inc, status='running', started_at__gte=stale_cutoff).first()
        if running:
            return self.err('8005', f'排查已在进行中 ({running.trace_id})', 409)

        # 原子启动锁（BUG-010）：DB 检查与子线程创建轨迹之间存在竞态窗口，
        # 用缓存 add() 的原子性抢占锁，防止并发请求重复发起排查。
        # 锁覆盖至子线程创建轨迹之后；生产 Redis 缓存可跨 worker 生效。
        from django.core.cache import cache
        lock_key = f"agent_start_lock_{inc.id}"
        if not cache.add(lock_key, 1, 30):
            return self.err('8005', '排查已在进行中', 409)

        user = _username(request)
        t_start = timezone.now()

        def _run():
            from django.db import close_old_connections
            close_old_connections()
            try:
                from monitor.llm.agent import investigate
                investigate(inc, triggered_by=user)
            except Exception:
                logger.exception("agent investigate 后台异常: %s", incident_id)
            finally:
                cache.delete(lock_key)
                close_old_connections()

        threading.Thread(target=_run, daemon=True, name=f"agent-{inc.id}").start()

        # 短轮询拿 trace_id (最多 2s, 拿不到则由前端轮询 agent-trace)
        trace_id = None
        for _ in range(10):
            time.sleep(0.2)
            t = AgentTrace.objects.filter(incident=inc, started_at__gte=t_start)\
                .order_by('-started_at').first()
            if t:
                trace_id = t.trace_id
                break
        return self.ok(trace_id=trace_id, status='running')


class AgentTraceListView(_BaseView):
    """GET /api/v1/incidents/<incident_id>/agent-trace/ (phase8/30 §3.5)"""

    @method_decorator(csrf_exempt)
    @method_decorator(require_auth)
    @method_decorator(require_permission(Perm.ALERTS_VIEW))
    def dispatch(self, *a, **k):
        return super().dispatch(*a, **k)

    def get(self, request, incident_id):
        inc = _get_incident(request, incident_id)
        if not inc:
            return self.err('8003', f'事故 {incident_id} 不存在或无权访问', 404)
        traces = []
        for t in inc.agent_traces.all()[:10]:
            traces.append({
                'trace_id': t.trace_id,
                'trigger': t.triggered_by,
                'status': t.status,
                'started_at': t.started_at,
                'finished_at': t.finished_at,
                'steps': t.steps or [],
                'conclusion': t.conclusion or {},
            })
        return self.ok(traces=traces)


# =============================================================================
# 8D: 变更事件流
# =============================================================================

_CHANGE_TYPES = ('param_change', 'ddl', 'deploy', 'maintenance', 'other')


class ChangeCreateView(_BaseView):
    """POST /api/v1/changes/ 人工登记变更 (phase8/30 §3.6)"""

    @method_decorator(csrf_exempt)
    @method_decorator(require_auth)
    @method_decorator(require_permission(Perm.DATABASES_UPDATE))
    def dispatch(self, *a, **k):
        return super().dispatch(*a, **k)

    def post(self, request):
        from monitor.change_stream import _make_dedup_key, record_change
        from monitor.models import ChangeEvent
        if not getattr(settings, 'CHANGE_STREAM_ENABLED', True):
            return self.err('DISABLED', '变更流未启用 (CHANGE_STREAM_ENABLED=False)', 400)
        data = self.body(request)
        cfg = _get_config(request, data.get('config_id'))
        if not cfg:
            return self.err('NOT_FOUND', f"数据库 {data.get('config_id')} 不存在或无权访问", 404)
        title = (data.get('title') or '').strip()
        change_type = data.get('change_type') or 'other'
        if not title:
            return self.err('BAD_REQUEST', 'title 必填', 400)
        if change_type not in _CHANGE_TYPES:
            return self.err('BAD_REQUEST', f'change_type 须为 {"/".join(_CHANGE_TYPES)}', 400)
        occurred_at = None
        if data.get('occurred_at'):
            occurred_at = parse_datetime(str(data['occurred_at']))
            if not occurred_at:
                return self.err('BAD_REQUEST', 'occurred_at 须为 ISO8601 时间', 400)

        obj = record_change(
            cfg, source='manual', title=title,
            detail=data.get('detail') or {}, change_type=change_type,
            operator=_username(request), occurred_at=occurred_at)
        if obj:
            return self.ok(change_id=obj.id)
        # 幂等: 重复登记时返回已存在记录
        key = _make_dedup_key(cfg.id, 'manual', title, occurred_at or timezone.now())
        exist = ChangeEvent.objects.filter(dedup_key=key).first()
        if exist:
            return self.ok(change_id=exist.id, duplicated=True)
        return self.err('INTERNAL', '变更登记失败', 500)


class ChangeListView(_BaseView):
    """GET /api/v1/databases/<config_id>/changes/?hours=72&types=ddl,param_change"""

    @method_decorator(csrf_exempt)
    @method_decorator(require_auth)
    @method_decorator(require_permission(Perm.DATABASES_VIEW))
    def dispatch(self, *a, **k):
        return super().dispatch(*a, **k)

    def get(self, request, config_id):
        from monitor.models import ChangeEvent
        cfg = _get_config(request, config_id)
        if not cfg:
            return self.err('NOT_FOUND', f'数据库 {config_id} 不存在或无权访问', 404)
        hours = min(int(request.GET.get('hours', 72)), 24 * 30)
        cutoff = timezone.now() - timezone.timedelta(hours=hours)
        qs = ChangeEvent.objects.filter(config=cfg, occurred_at__gte=cutoff)
        types = [t for t in (request.GET.get('types') or '').split(',') if t]
        if types:
            qs = qs.filter(change_type__in=types)
        total = qs.count()
        rows = [{
            'change_id': c.id, 'change_type': c.change_type, 'title': c.title,
            'detail': c.detail, 'source': c.source, 'operator': c.operator,
            'occurred_at': c.occurred_at,
        } for c in qs.order_by('-occurred_at')[:200]]
        return self.ok(changes=rows, total=total)


# =============================================================================
# AI 运营统计 / LLM 调用日志 / 规则准确率
# =============================================================================

class AiOpsStatsView(_BaseView):
    """GET /api/v1/ai-ops/stats/?days=7 (phase8/30 §3.7)"""

    @method_decorator(csrf_exempt)
    @method_decorator(require_auth)
    @method_decorator(require_permission(Perm.METRICS_VIEW))
    def dispatch(self, *a, **k):
        return super().dispatch(*a, **k)

    def get(self, request):
        from django.db.models import Avg, Sum
        from monitor.models import (AgentTrace, AlertCase, LLMCallLog,
                                    PlanFeedback, RcaFeedback)
        days = min(int(request.GET.get('days', 7)), 90)
        cutoff = timezone.now() - timezone.timedelta(days=days)

        llm_qs = LLMCallLog.objects.filter(created_at__gte=cutoff)
        calls = llm_qs.count()
        errors = llm_qs.exclude(status='ok').count()
        agg = llm_qs.aggregate(lat=Avg('latency_ms'), tin=Sum('prompt_tokens'),
                               tout=Sum('completion_tokens'))

        rca_qs = RcaFeedback.objects.filter(created_at__gte=cutoff)
        rca_total = rca_qs.count()
        rca_correct = rca_qs.filter(verdict='correct').count()

        plan_qs = PlanFeedback.objects.filter(created_at__gte=cutoff)
        plan_total = plan_qs.count()
        plan_adopted = plan_qs.filter(verdict='adopted').count()

        agent_qs = AgentTrace.objects.filter(started_at__gte=cutoff)
        agent_runs = agent_qs.count()
        agent_done = agent_qs.filter(status='done').count()
        step_counts = [len(t.steps or []) for t in agent_qs[:200]]

        return self.ok(
            llm={
                'calls': calls,
                'error_rate': round(errors / calls, 4) if calls else 0.0,
                'avg_latency_ms': int(agg['lat'] or 0),
                'token_in_total': int(agg['tin'] or 0),
                'token_out_total': int(agg['tout'] or 0),
            },
            rca={
                'feedback_total': rca_total,
                'accuracy': round(rca_correct / rca_total, 4) if rca_total else 0.0,
            },
            plans={
                'feedback_total': plan_total,
                'adopt_rate': round(plan_adopted / plan_total, 4) if plan_total else 0.0,
            },
            cases={
                'auto_distilled': AlertCase.objects.filter(source='distilled').count(),
                'manual': AlertCase.objects.filter(source='manual').count(),
                'vector_indexed': AlertCase.objects.filter(embedding_indexed=True).count(),
            },
            agent={
                'runs': agent_runs,
                'done': agent_done,
                'avg_steps': round(sum(step_counts) / len(step_counts), 1) if step_counts else 0.0,
            })


class LlmCallListView(_BaseView):
    """GET /api/v1/ai-ops/llm-calls/?scene=&status=&page=1&page_size=20 (phase8/30 §3.8)"""

    @method_decorator(csrf_exempt)
    @method_decorator(require_auth)
    @method_decorator(require_permission(Perm.AUDIT_LOGS_VIEW))
    def dispatch(self, *a, **k):
        return super().dispatch(*a, **k)

    def get(self, request):
        from monitor.models import LLMCallLog
        qs = LLMCallLog.objects.all()
        for f in ('scene', 'status'):
            v = request.GET.get(f)
            if v:
                qs = qs.filter(**{f: v})
        page = max(int(request.GET.get('page', 1)), 1)
        page_size = min(int(request.GET.get('page_size', 20)), 100)
        total = qs.count()
        items = [{
            'id': r.id, 'scene': r.scene, 'incident_id': r.incident_id,
            'provider': r.provider, 'model': r.model, 'status': r.status,
            'prompt_tokens': r.prompt_tokens, 'completion_tokens': r.completion_tokens,
            'latency_ms': r.latency_ms, 'error_message': r.error_message[:200],
            'created_at': r.created_at,
        } for r in qs[(page - 1) * page_size: page * page_size]]
        return self.ok(items=items, total=total, page=page, page_size=page_size)


class RuleStatListView(_BaseView):
    """GET /api/v1/ai-ops/rule-stats/ (phase8/30 §3.8)"""

    @method_decorator(csrf_exempt)
    @method_decorator(require_auth)
    @method_decorator(require_permission(Perm.METRICS_VIEW))
    def dispatch(self, *a, **k):
        return super().dispatch(*a, **k)

    def get(self, request):
        from monitor.models import RuleStat
        names = {}
        try:
            from monitor.rca_engine_v2 import RULES_V2
            names = {r['id']: r.get('name', '') for r in RULES_V2}
        except Exception:
            pass
        items = [{
            'rule_id': s.rule_id,
            'rule_name': names.get(s.rule_id, ''),
            'sample_count': s.correct_count + s.wrong_count,
            'hit_count': s.hit_count,
            'accuracy': round(s.accuracy, 4),
            'calibrated_confidence': round(s.calibrated_base, 4),
            'updated_at': s.updated_at,
        } for s in RuleStat.objects.order_by('-accuracy')[:200]]
        return self.ok(items=items)


# =============================================================================
# 8E: 自治等级 / 8D: 因果图 / 8A: LLM 连通测试
# =============================================================================

class AutonomyView(_BaseView):
    """PUT /api/v1/databases/<config_id>/autonomy/ {"level": "L2"} (phase8/30 §3.9)"""

    @method_decorator(csrf_exempt)
    @method_decorator(require_auth)
    @method_decorator(require_permission(Perm.DATABASES_UPDATE))
    def dispatch(self, *a, **k):
        return super().dispatch(*a, **k)

    def put(self, request, config_id):
        cfg = _get_config(request, config_id)
        if not cfg:
            return self.err('NOT_FOUND', f'数据库 {config_id} 不存在或无权访问', 404)
        raw = self.body(request).get('level')
        if raw is None:
            # null = 还原为跟随全局 AUTONOMY_DEFAULT_LEVEL
            cfg.autonomy_level = None
            cfg.save(update_fields=['autonomy_level'])
            level = None
        else:
            try:
                level = int(str(raw).upper().lstrip('L'))
                if level not in (0, 1, 2, 3):
                    raise ValueError
            except (ValueError, TypeError):
                return self.err('8006', 'level 须为 L0/L1/L2/L3 或 null(跟随全局)', 400)
            cfg.autonomy_level = level
            cfg.save(update_fields=['autonomy_level'])
        circuit_open = False
        try:
            from monitor.playbook_authz import _circuit_broken
            circuit_open = _circuit_broken(cfg.id)
        except Exception:
            pass
        return self.ok(level=(f'L{level}' if level is not None else None),
                       circuit_open=circuit_open)

    def get(self, request, config_id):
        from monitor.autonomy_policy import LEVEL_NAMES, effective_level
        cfg = _get_config(request, config_id)
        if not cfg:
            return self.err('NOT_FOUND', f'数据库 {config_id} 不存在或无权访问', 404)
        lv = effective_level(cfg)
        return self.ok(level=f'L{lv}', level_name=LEVEL_NAMES.get(lv, ''),
                       inherited=cfg.autonomy_level is None)


class CausalGraphView(_BaseView):
    """GET /api/v1/databases/<config_id>/causal-graph/ (phase8/30 §3.9)"""

    @method_decorator(csrf_exempt)
    @method_decorator(require_auth)
    @method_decorator(require_permission(Perm.METRICS_VIEW))
    def dispatch(self, *a, **k):
        return super().dispatch(*a, **k)

    def get(self, request, config_id):
        from monitor.models import CausalEdge
        cfg = _get_config(request, config_id)
        if not cfg:
            return self.err('NOT_FOUND', f'数据库 {config_id} 不存在或无权访问', 404)
        edges = [{
            'cause': e.cause_metric, 'effect': e.effect_metric,
            'lag_min': e.lag_minutes, 'strength': round(e.strength, 4),
            'mined_at': e.updated_at,
        } for e in CausalEdge.objects.filter(config=cfg).order_by('-strength')[:50]]
        return self.ok(edges=edges, fallback_static=not edges)


class LlmConfigView(_BaseView):
    """GET / PUT /api/v1/llm/config/ (查看与动态保存 LLM 大模型及 API Key 配置)"""

    @method_decorator(csrf_exempt)
    @method_decorator(require_auth)
    def dispatch(self, *a, **k):
        return super().dispatch(*a, **k)

    @method_decorator(require_permission(Perm.ALERT_CONFIG_VIEW))
    def get(self, request):
        """读取数据库中的系统默认凭据；部署 settings 只作为只读 fallback。"""
        from monitor.models import LLMProviderCredential, LLMSceneRoutingRule
        credential = LLMProviderCredential.objects.filter(name='系统默认').first()
        route = LLMSceneRoutingRule.objects.filter(scene_code='global_default').first()
        if credential:
            try:
                api_key = credential.get_api_key()
            except ValueError:
                api_key = ''
            provider = credential.provider_type
            base_url = credential.base_url
            model = credential.model_name
            enabled = credential.is_active
        else:
            api_key = getattr(settings, 'LLM_API_KEY', '')
            provider = getattr(settings, 'LLM_PROVIDER', 'openai_compat')
            base_url = getattr(settings, 'LLM_BASE_URL', '')
            model = getattr(settings, 'LLM_MODEL', '')
            enabled = bool(getattr(settings, 'LLM_ENABLED', False))
        masked_key = (f"{api_key[:4]}****{api_key[-4:]}" if len(api_key) > 8
                      else ("****" if api_key else ""))

        return self.ok(
            llm_enabled=enabled,
            llm_provider=provider,
            llm_base_url=base_url,
            llm_model=model,
            llm_api_key_masked=masked_key,
            has_api_key=bool(api_key),
            llm_temperature=(route.temperature if route else
                             float(getattr(settings, 'LLM_TEMPERATURE', 0.1))),
            llm_max_tokens=(route.max_tokens if route else
                            int(getattr(settings, 'LLM_MAX_TOKENS', 2048))),
            llm_timeout_sec=(route.timeout_sec if route else
                             int(getattr(settings, 'LLM_TIMEOUT_SEC', 25))),
            agent_enabled=bool(getattr(settings, 'AGENT_ENABLED', False)),
            embed_enabled=bool(getattr(settings, 'EMBED_ENABLED', False)),
            deployment_managed_fields=['agent_enabled', 'embed_enabled', 'proxy_url'],
        )

    @method_decorator(require_permission(Perm.ALERT_CONFIG_MANAGE))
    def put(self, request):
        """更新数据库系统默认凭据；不修改 worker 内存或部署文件。"""
        from monitor.llm.security import LLMEndpointValidationError, validate_llm_base_url
        from monitor.models import LLMProviderCredential, LLMSceneRoutingRule
        data = self.body(request)
        llm_enabled_val = data.get('llm_enabled', True)
        base_url = (data.get('llm_base_url') or '').strip()
        api_key = (data.get('llm_api_key') or '').strip()
        model = (data.get('llm_model') or '').strip()
        provider_type = (data.get('llm_provider') or 'custom').strip()
        if not base_url or not model:
            return self.err('BAD_REQUEST', 'Base URL 与模型名称为必填项', 400)
        try:
            base_url = validate_llm_base_url(base_url)
        except LLMEndpointValidationError as exc:
            return self.err('BAD_REQUEST', str(exc), 400)
        allowed_types = {value for value, _label in LLMProviderCredential.PROVIDER_CHOICES}
        if provider_type not in allowed_types:
            return self.err('BAD_REQUEST', '不支持的服务商类型', 400)
        credential, _created = LLMProviderCredential.objects.get_or_create(
            name='系统默认',
            defaults={
                'provider_type': provider_type, 'base_url': base_url,
                'model_name': model, 'priority': 1, 'weight': 1,
            },
        )
        credential.provider_type = provider_type
        credential.base_url = base_url
        credential.model_name = model
        credential.is_active = bool(llm_enabled_val)
        credential.proxy_url = ''
        if api_key:
            credential.set_api_key(api_key)
        credential.save()

        route, _created = LLMSceneRoutingRule.objects.get_or_create(
            scene_code='global_default',
            defaults={'scene_name': '全局默认兜底', 'description': '未单独配置场景时使用'},
        )
        route.primary_credential = credential
        if data.get('llm_temperature') is not None:
            route.temperature = float(data['llm_temperature'])
        if data.get('llm_max_tokens') is not None:
            route.max_tokens = int(data['llm_max_tokens'])
        if data.get('llm_timeout_sec') is not None:
            route.timeout_sec = int(data['llm_timeout_sec'])
        route.save()
        return self.ok(message='数据库凭据与路由配置已更新；所有 worker 下次请求统一生效')


class LlmCredentialsView(_BaseView):
    """GET / POST /api/v1/llm/credentials/ (多大模型凭据池管理)"""

    @method_decorator(csrf_exempt)
    @method_decorator(require_auth)
    def dispatch(self, *a, **k):
        return super().dispatch(*a, **k)

    @method_decorator(require_permission(Perm.ALERT_CONFIG_VIEW))
    def get(self, request):
        from monitor.models import LLMProviderCredential
        creds = LLMProviderCredential.objects.all().order_by('priority', '-weight', 'id')
        items = []
        for c in creds:
            try:
                api_key = c.get_api_key()
            except ValueError:
                api_key = ''
            masked = f"{api_key[:4]}****{api_key[-4:]}" if len(api_key) > 8 else ("****" if api_key else "")
            items.append({
                'id': c.id,
                'name': c.name,
                'provider_type': c.provider_type,
                'base_url': c.base_url,
                'model_name': c.model_name,
                'proxy_managed_by_deployment': True,
                'api_key_masked': masked,
                'has_key': bool(api_key),
                'is_active': c.is_active,
                'priority': c.priority,
                'weight': c.weight,
                'is_healthy': c.is_healthy,
                'cooldown_until': c.cooldown_until.isoformat() if c.cooldown_until else None,
                'consecutive_fails': c.consecutive_fails,
                'last_latency_ms': c.last_latency_ms,
                'last_error_message': c.last_error_message,
                'updated_at': c.updated_at.isoformat() if c.updated_at else None,
            })
        return self.ok(credentials=items, total=len(items))

    @method_decorator(require_permission(Perm.ALERT_CONFIG_MANAGE))
    def post(self, request):
        from monitor.models import LLMProviderCredential
        from monitor.llm.security import LLMEndpointValidationError, validate_llm_base_url
        data = self.body(request)
        name = (data.get('name') or '').strip()
        provider_type = data.get('provider_type') or 'custom'
        base_url = (data.get('base_url') or '').strip()
        api_key = (data.get('api_key') or '').strip()
        model_name = (data.get('model_name') or '').strip()
        if (data.get('proxy_url') or '').strip():
            return self.err('BAD_REQUEST', '代理只能通过部署期 LLM_PROXY_URL 配置', 400)

        if not name or not base_url or not model_name:
            return self.err('BAD_REQUEST', '名称、接入端点(Base URL)与模型名称为必填项', 400)
        try:
            base_url = validate_llm_base_url(base_url)
        except LLMEndpointValidationError as exc:
            return self.err('BAD_REQUEST', str(exc), 400)

        cred = LLMProviderCredential.objects.create(
            name=name,
            provider_type=provider_type,
            base_url=base_url,
            api_key=api_key,
            model_name=model_name,
            proxy_url='',
            is_active=bool(data.get('is_active', True)),
            priority=int(data.get('priority', 10)),
            weight=int(data.get('weight', 1)),
        )
        return self.ok(id=cred.id, message="大模型凭据已成功创建并加入连接池！")


class LlmCredentialDetailView(_BaseView):
    """PUT / DELETE /api/v1/llm/credentials/<id>/ (修改与删除指定模型凭据)"""

    @method_decorator(csrf_exempt)
    @method_decorator(require_auth)
    @method_decorator(require_permission(Perm.ALERT_CONFIG_MANAGE))
    def dispatch(self, *a, **k):
        return super().dispatch(*a, **k)

    def put(self, request, cred_id):
        from monitor.models import LLMProviderCredential
        from monitor.llm.security import LLMEndpointValidationError, validate_llm_base_url
        cred = LLMProviderCredential.objects.filter(id=cred_id).first()
        if not cred:
            return self.err('NOT_FOUND', '该凭据不存在', 404)

        data = self.body(request)
        if 'name' in data and data['name'].strip():
            cred.name = data['name'].strip()
        if 'provider_type' in data:
            allowed_types = {value for value, _label in LLMProviderCredential.PROVIDER_CHOICES}
            if data['provider_type'] not in allowed_types:
                return self.err('BAD_REQUEST', '不支持的服务商类型', 400)
            cred.provider_type = data['provider_type']
        if 'base_url' in data and data['base_url'].strip():
            try:
                cred.base_url = validate_llm_base_url(data['base_url'].strip())
            except LLMEndpointValidationError as exc:
                return self.err('BAD_REQUEST', str(exc), 400)
        if 'api_key' in data and data['api_key'].strip():
            cred.api_key = data['api_key'].strip()
        if 'model_name' in data and data['model_name'].strip():
            cred.model_name = data['model_name'].strip()
        if 'proxy_url' in data:
            if (data['proxy_url'] or '').strip():
                return self.err('BAD_REQUEST', '代理只能通过部署期 LLM_PROXY_URL 配置', 400)
            cred.proxy_url = ''
        if 'is_active' in data:
            cred.is_active = bool(data['is_active'])
        if 'priority' in data:
            cred.priority = int(data['priority'])
        if 'weight' in data:
            cred.weight = int(data['weight'])

        cred.save()
        return self.ok(message="凭据配置已成功更新！")

    def delete(self, request, cred_id):
        from monitor.models import LLMProviderCredential
        deleted, _ = LLMProviderCredential.objects.filter(id=cred_id).delete()
        if not deleted:
            return self.err('NOT_FOUND', '凭据不存在或已被删除', 404)
        return self.ok(message="凭据已安全移除。")


class LlmCredentialPingView(_BaseView):
    """POST /api/v1/llm/credentials/<id>/ping/ (单个凭据一键在线探活)"""

    @method_decorator(csrf_exempt)
    @method_decorator(require_auth)
    @method_decorator(require_permission(Perm.ALERT_CONFIG_MANAGE))
    def dispatch(self, *a, **k):
        return super().dispatch(*a, **k)

    def post(self, request, cred_id):
        from monitor.models import LLMProviderCredential
        from monitor.llm.providers import OpenAICompatProvider

        cred = LLMProviderCredential.objects.filter(id=cred_id).first()
        if not cred:
            return self.err('NOT_FOUND', '凭据不存在', 404)

        t0 = time.time()
        result = {'ok': False, 'latency_ms': 0, 'model': cred.model_name}
        try:
            provider = OpenAICompatProvider(
                base_url=cred.base_url,
                api_key=cred.get_api_key(),
                model=cred.model_name,
                timeout=15,
            )
            res = provider.chat(
                [{'role': 'user', 'content': "ping, 请确认连通并回复 pong"}],
                scene='ping', max_tokens=150, json_mode=False
            )
            result['ok'] = bool(res and res.content)
            result['reply'] = res.content if res else ''
            result['latency_ms'] = int((time.time() - t0) * 1000)
            cred.is_healthy = True
            cred.consecutive_fails = 0
            cred.last_latency_ms = result['latency_ms']
            cred.last_error_message = ''
            cred.save(update_fields=['is_healthy', 'consecutive_fails', 'last_latency_ms', 'last_error_message'])
        except Exception as e:
            result['error'] = str(e)[:250]
            cred.consecutive_fails += 1
            cred.last_error_message = str(e)[:250]
            cred.save(update_fields=['consecutive_fails', 'last_error_message'])
            return self.err('BAD_REQUEST', f"连通异常: {result['error']}", 400)

        if not result['ok']:
            return self.err('BAD_REQUEST', f"连通异常: {result.get('error') or '模型未返回有效文本'}", 400)

        return self.ok(**result)


class LlmRoutesView(_BaseView):
    """GET / PUT /api/v1/llm/routes/ (场景智能分流策略管理)"""

    @method_decorator(csrf_exempt)
    @method_decorator(require_auth)
    def dispatch(self, *a, **k):
        return super().dispatch(*a, **k)

    @method_decorator(require_permission(Perm.ALERT_CONFIG_VIEW))
    def get(self, request):
        from monitor.models import LLMSceneRoutingRule, LLMProviderCredential
        rules = LLMSceneRoutingRule.objects.all()
        items = []
        for r in rules:
            items.append({
                'id': r.id,
                'scene_code': r.scene_code,
                'scene_name': r.scene_name,
                'description': r.description,
                'primary_credential_id': r.primary_credential_id,
                'primary_credential_name': r.primary_credential.name if r.primary_credential else '默认继承连接池优先级',
                'fallback_credential_ids': list(r.fallback_credentials.values_list('id', flat=True)),
                'temperature': r.temperature,
                'timeout_sec': r.timeout_sec,
                'max_tokens': r.max_tokens,
            })
        return self.ok(routes=items)

    @method_decorator(require_permission(Perm.ALERT_CONFIG_MANAGE))
    def put(self, request):
        from monitor.models import LLMSceneRoutingRule
        data = self.body(request)
        scene_code = data.get('scene_code')
        rule = LLMSceneRoutingRule.objects.filter(scene_code=scene_code).first()
        if not rule:
            return self.err('NOT_FOUND', f'场景 {scene_code} 不存在', 404)

        if 'primary_credential_id' in data:
            rule.primary_credential_id = data['primary_credential_id']
        if 'temperature' in data:
            rule.temperature = float(data['temperature'])
        if 'timeout_sec' in data:
            rule.timeout_sec = int(data['timeout_sec'])
        if 'max_tokens' in data:
            rule.max_tokens = int(data['max_tokens'])

        rule.save()

        if 'fallback_credential_ids' in data:
            rule.fallback_credentials.set(data['fallback_credential_ids'])

        return self.ok(message="场景路由规则已更新生效！")


class LlmTestConnectionView(_BaseView):
    """POST /api/v1/llm/test-connection/ (大模型全局单点在线连通性测试)"""

    @method_decorator(csrf_exempt)
    @method_decorator(require_auth)
    @method_decorator(require_permission(Perm.ALERT_CONFIG_MANAGE))
    def dispatch(self, *a, **k):
        return super().dispatch(*a, **k)

    def post(self, request):
        from monitor.models import LLMProviderCredential
        data = self.body(request)
        override_base = (data.get('llm_base_url') or '').strip()
        override_key = (data.get('llm_api_key') or '').strip()
        override_model = (data.get('llm_model') or '').strip()

        default_credential = LLMProviderCredential.objects.filter(
            name='系统默认').first() or LLMProviderCredential.objects.filter(
                is_active=True).order_by('priority', '-weight').first()
        from monitor.llm.providers import OpenAICompatProvider
        stored_key = ''
        if default_credential:
            try:
                stored_key = default_credential.get_api_key()
            except ValueError:
                stored_key = ''
        result = {
            'ok': False,
            'model': override_model or (default_credential.model_name if default_credential
                                        else getattr(settings, 'LLM_MODEL', '')),
            'latency_ms': 0,
        }
        try:
            provider = OpenAICompatProvider(
                base_url=override_base or (default_credential.base_url if default_credential
                                           else getattr(settings, 'LLM_BASE_URL', '')),
                api_key=override_key or stored_key or getattr(settings, 'LLM_API_KEY', ''),
                model=override_model or (default_credential.model_name if default_credential
                                         else getattr(settings, 'LLM_MODEL', '')),
                timeout=15
            )
            t0 = time.time()
            res = provider.chat(
                [{'role': 'user', 'content': "你好！请回复一句：DB-AIOps 智能助手连接测试成功！"}],
                scene='test', max_tokens=32, json_mode=False)
            result['ok'] = bool(res and res.content)
            result['reply'] = res.content if res else ''
            result['latency_ms'] = int((time.time() - t0) * 1000)
        except Exception as e:
            result['error'] = str(e)[:300]

        return self.ok(**result)


# =============================================================================
# Copilot 智能问答与一键智能体检 API
# =============================================================================

class CopilotChatView(_BaseView):
    """POST /api/v1/copilot/chat/ (Copilot 智能问答与交互)"""

    @method_decorator(csrf_exempt)
    @method_decorator(require_auth)
    @method_decorator(require_permission(Perm.METRICS_VIEW))
    def dispatch(self, *a, **k):
        return super().dispatch(*a, **k)

    def post(self, request):
        from monitor.copilot import run_copilot_chat
        data = self.body(request)
        query = (data.get('query') or '').strip()
        if not query:
            return self.err('BAD_REQUEST', '提问内容不能为空', 400)

        config_id = data.get('config_id')
        if config_id:
            cfg = _get_config(request, config_id)
            if not cfg:
                return self.err('NOT_FOUND', f'数据库 {config_id} 不存在或无权访问', 404)

        history = data.get('history') or []
        res = run_copilot_chat(
            query, config_id=config_id, history=history, user=request.user)
        return self.ok(**res)


class QuickAssessmentView(_BaseView):
    """GET /api/v1/databases/<config_id>/quick-assessment/ (一键智能体检)"""

    @method_decorator(csrf_exempt)
    @method_decorator(require_auth)
    @method_decorator(require_permission(Perm.DATABASES_VIEW))
    def dispatch(self, *a, **k):
        return super().dispatch(*a, **k)

    def get(self, request, config_id):
        from monitor.copilot import generate_quick_health_assessment
        cfg = _get_config(request, config_id)
        if not cfg:
            return self.err('NOT_FOUND', f'数据库 {config_id} 不存在或无权访问', 404)

        assessment = generate_quick_health_assessment(cfg.id)
        if 'error' in assessment:
            return self.err('INTERNAL', assessment['error'], 500)
        return self.ok(assessment=assessment)
