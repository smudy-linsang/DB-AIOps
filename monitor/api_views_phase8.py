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
    from monitor.models import DatabaseConfig
    cfg = DatabaseConfig.objects.filter(id=config_id).first()
    if not cfg:
        return None
    allowed = get_user_database_ids(request.user)
    if allowed is not None and cfg.id not in allowed:
        return None
    return cfg


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
    @method_decorator(require_permission(Perm.ALERT_CONFIG_VIEW))
    def dispatch(self, *a, **k):
        return super().dispatch(*a, **k)

    def get(self, request):
        """获取当前大模型与 API Key 配置状态（脱敏输出）"""
        api_key = getattr(settings, 'LLM_API_KEY', '')
        masked_key = ''
        if api_key:
            masked_key = f"{api_key[:4]}****{api_key[-4:]}" if len(api_key) > 8 else "****"

        return self.ok(
            llm_enabled=bool(getattr(settings, 'LLM_ENABLED', False)),
            llm_provider=getattr(settings, 'LLM_PROVIDER', 'openai_compat'),
            llm_base_url=getattr(settings, 'LLM_BASE_URL', 'http://localhost:11434/v1'),
            llm_model=getattr(settings, 'LLM_MODEL', 'qwen2.5:14b-instruct'),
            llm_api_key_masked=masked_key,
            has_api_key=bool(api_key),
            llm_temperature=float(getattr(settings, 'LLM_TEMPERATURE', 0.1)),
            llm_max_tokens=int(getattr(settings, 'LLM_MAX_TOKENS', 2048)),
            llm_timeout_sec=int(getattr(settings, 'LLM_TIMEOUT_SEC', 25)),
            agent_enabled=bool(getattr(settings, 'AGENT_ENABLED', False)),
            embed_enabled=bool(getattr(settings, 'EMBED_ENABLED', False)),
        )

    def put(self, request):
        """在线更新大模型与 API Key 配置，并持久化到 .env 文件"""
        if not request.user.is_superuser:
            return self.err('FORBIDDEN', '仅超级管理员可修改全局大模型与 API Key 配置', 403)

        data = self.body(request)
        llm_enabled_val = data.get('llm_enabled', True)
        base_url = (data.get('llm_base_url') or '').strip()
        api_key = (data.get('llm_api_key') or '').strip()
        model = (data.get('llm_model') or '').strip()
        temperature = data.get('llm_temperature')
        max_tokens = data.get('llm_max_tokens')
        agent_enabled_val = data.get('agent_enabled', True)

        # 动态更新 settings 运行时内存
        settings.LLM_ENABLED = bool(llm_enabled_val)
        if base_url:
            settings.LLM_BASE_URL = base_url
        if api_key:
            settings.LLM_API_KEY = api_key
        if model:
            settings.LLM_MODEL = model
        if temperature is not None:
            settings.LLM_TEMPERATURE = float(temperature)
        if max_tokens is not None:
            settings.LLM_MAX_TOKENS = int(max_tokens)
        settings.AGENT_ENABLED = bool(agent_enabled_val)

        # 重置全局 Provider 单例缓存
        from monitor.llm import providers
        with providers._chat_lock:
            providers._chat_provider = None

        # 同步写入/更新 .env 文件
        env_path = os.path.join(settings.BASE_DIR, '.env')
        try:
            env_lines = []
            if os.path.exists(env_path):
                with open(env_path, 'r', encoding='utf-8') as f:
                    env_lines = f.readlines()

            updated_keys = {
                'LLM_ENABLED': str(settings.LLM_ENABLED),
                'LLM_BASE_URL': settings.LLM_BASE_URL,
                'LLM_MODEL': settings.LLM_MODEL,
                'AGENT_ENABLED': str(settings.AGENT_ENABLED),
            }
            if api_key:
                updated_keys['LLM_API_KEY'] = api_key

            new_lines = []
            seen_keys = set()
            for line in env_lines:
                k = line.split('=')[0].strip() if '=' in line else ''
                if k in updated_keys:
                    new_lines.append(f"{k}={updated_keys[k]}\n")
                    seen_keys.add(k)
                else:
                    new_lines.append(line)

            # 追加新配置项
            for k, v in updated_keys.items():
                if k not in seen_keys:
                    new_lines.append(f"{k}={v}\n")

            with open(env_path, 'w', encoding='utf-8') as f:
                f.writelines(new_lines)

        except Exception as e:
            logger.warning("写入 .env 失败: %s", e)

        return self.ok(message="大模型与 API Key 配置已成功更新并生效！")


class LlmTestConnectionView(_BaseView):
    """POST /api/v1/llm/test-connection/ (phase8/30 §3.9)。
    直接用当前 settings 打一次 ping, 不受 LLM_ENABLED 开关限制 (供上线前验证)。"""

    @method_decorator(csrf_exempt)
    @method_decorator(require_auth)
    @method_decorator(require_permission(Perm.ALERT_CONFIG_VIEW))
    def dispatch(self, *a, **k):
        return super().dispatch(*a, **k)

    def post(self, request):
        data = self.body(request)
        override_base = (data.get('llm_base_url') or '').strip()
        override_key = (data.get('llm_api_key') or '').strip()
        override_model = (data.get('llm_model') or '').strip()

        from monitor.llm.providers import OpenAICompatProvider
        result = {
            'ok': False,
            'model': override_model or getattr(settings, 'LLM_MODEL', ''),
            'latency_ms': 0,
        }
        try:
            provider = OpenAICompatProvider(
                base_url=override_base or getattr(settings, 'LLM_BASE_URL', ''),
                api_key=override_key or getattr(settings, 'LLM_API_KEY', ''),
                model=override_model or getattr(settings, 'LLM_MODEL', ''),
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
        res = run_copilot_chat(query, config_id=config_id, history=history)
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
