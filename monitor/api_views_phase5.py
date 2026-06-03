"""
Phase 5 API 端点 v1.0

包含:
- 告警 RCA 2.0 (根因/上下文/影响/方案)
- 智能巡检 (执行/历史/报告)
- 案例库管理
"""
import json
import logging
import uuid
from datetime import datetime, timedelta

from django.utils import timezone
from django.db import models as db_models
from django.views import View
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt

from monitor.auth import require_auth, get_user_database_ids
from monitor.models import (
    AlertLog, DatabaseConfig, AlertCase, RemediationPlan,
    BusinessImpactAssessment, InspectionItem, InspectionRun,
    InspectionFinding, InspectionIssuePattern,
)
from monitor.context_aggregator import ContextAggregator
from monitor.rca_engine_v2 import RCAEngineV2
from monitor.impact_engine import BusinessImpactAssessor
from monitor.remediation_planner import RemediationPlanner

logger = logging.getLogger(__name__)


def _json_default(obj):
    if isinstance(obj, datetime):
        return obj.isoformat()
    if hasattr(obj, 'isoformat'):
        return obj.isoformat()
    return str(obj)


class _BaseView(View):
    """Phase 5 基础视图"""

    def json_response(self, data, status=200):
        from django.http import JsonResponse
        return JsonResponse(data, status=status, json_dumps_params={'default': _json_default})

    def error_response(self, message, status=400):
        return self.json_response({'error': message}, status=status)


# ==========================================
# 告警 RCA 2.0 接口
# ==========================================
class AlertRCADetailView(_BaseView):
    """告警 RCA 2.0 详情接口 - 一站式返回上下文/根因/影响/方案"""

    @method_decorator(csrf_exempt)
    @method_decorator(require_auth)
    def dispatch(self, *args, **kwargs):
        return super().dispatch(*args, **kwargs)

    def get(self, request, alert_id):
        try:
            alert = AlertLog.objects.select_related('config').get(id=alert_id)
        except AlertLog.DoesNotExist:
            return self.error_response(f'告警 {alert_id} 不存在', 404)

        # RBAC 校验
        allowed = get_user_database_ids(request.user)
        if allowed is not None and alert.config_id not in allowed:
            return self.error_response('Permission denied', 403)

        result = {
            'alert_id': alert.id,
            'alert_title': alert.title,
            'db_name': alert.config.name,
            'db_type': alert.config.db_type,
            'severity': alert.severity,
            'alert_type': alert.alert_type,
            'context': {},
            'rca_diagnoses': [],
            'impact': {},
            'remediation_plans': [],
            'has_data': True,
        }

        try:
            # 1. 上下文聚合
            aggregator = ContextAggregator(alert, time_window_min=30)
            result['context'] = aggregator.aggregate()

            # 2. 提取当前指标快照
            current_data = self._build_current_data(alert, result['context'])
            result['current_snapshot'] = current_data

            # 3. RCA 2.0 诊断
            engine = RCAEngineV2(db_type=alert.config.db_type)
            diagnoses = engine.diagnose(current_data, result['context'])
            result['rca_diagnoses'] = [d.to_dict() for d in diagnoses]

            # 4. 影响评估
            try:
                assessor = BusinessImpactAssessor(alert.config, alert)
                impact = assessor.assess()
                result['impact'] = impact.to_dict()
            except Exception as e:
                logger.warning(f"[RCA API] 影响评估失败: {e}")
                result['impact'] = {'error': str(e)}

            # 5. 方案生成(每条诊断生成方案)
            for diag in result['rca_diagnoses'][:3]:
                try:
                    planner = RemediationPlanner(alert.config, diag)
                    plan = planner.generate()
                    result['remediation_plans'].append(plan.to_dict())
                except Exception as e:
                    logger.warning(f"[RCA API] 方案生成失败: {e}")

        except Exception as e:
            logger.error(f"[RCA API] 处理告警 {alert_id} 失败: {e}")
            result['error'] = str(e)

        return self.json_response(result)

    def _build_current_data(self, alert, context):
        """构建当前指标数据快照"""
        data = {}
        related = context.get('related_metrics', {})
        for k, points in related.items():
            if points:
                data[k] = points[0].get('value', 0)
        # 包含告警本身的指标
        if alert.metric_name:
            data[alert.metric_name] = alert.metric_value
        return data


class AlertRCAQuickView(_BaseView):
    """告警 RCA 快速诊断接口(不查上下文,仅用规则引擎)"""

    @method_decorator(csrf_exempt)
    @method_decorator(require_auth)
    def dispatch(self, *args, **kwargs):
        return super().dispatch(*args, **kwargs)

    def post(self, request):
        try:
            body = json.loads(request.body.decode('utf-8'))
        except Exception:
            return self.error_response('Invalid JSON body')

        data = body.get('data', {})
        db_type = body.get('db_type', 'oracle')
        engine = RCAEngineV2(db_type=db_type)
        diagnoses = engine.diagnose(data, body.get('context'))
        return self.json_response({
            'diagnoses': [d.to_dict() for d in diagnoses],
            'rule_count': len(diagnoses),
        })


# ==========================================
# 智能巡检接口
# ==========================================
class InspectionRunListView(_BaseView):
    """巡检执行列表"""

    @method_decorator(csrf_exempt)
    @method_decorator(require_auth)
    def dispatch(self, *args, **kwargs):
        return super().dispatch(*args, **kwargs)

    def get(self, request):
        config_id = request.GET.get('config_id')
        level = request.GET.get('level')
        status = request.GET.get('status')
        limit = int(request.GET.get('limit', 50))

        qs = InspectionRun.objects.all().order_by('-started_at')
        if config_id:
            qs = qs.filter(config_id=config_id)
        if level:
            qs = qs.filter(level=level)
        if status:
            qs = qs.filter(status=status)

        # RBAC
        allowed = get_user_database_ids(request.user)
        if allowed is not None:
            qs = qs.filter(config_id__in=allowed)

        runs = []
        for r in qs[:limit]:
            runs.append({
                'run_id': r.run_id,
                'db_id': r.config_id,
                'db_name': r.db_config.name if hasattr(r, 'db_config') else '',
                'level': r.level,
                'status': r.status,
                'started_at': r.started_at,
                'finished_at': r.finished_at,
                'duration_sec': r.duration_sec,
                'total_items': r.total_items,
                'executed_items': r.executed_items,
                'passed_items': r.passed_items,
                'failed_items': r.failed_items,
                'critical_count': r.critical_count,
                'error_count': r.error_count,
                'warn_count': r.warn_count,
                'total_risk_score': r.total_risk_score,
            })
        return self.json_response({'total': len(runs), 'runs': runs})


class InspectionRunTriggerView(_BaseView):
    """手动触发巡检"""

    @method_decorator(csrf_exempt)
    @method_decorator(require_auth)
    def dispatch(self, *args, **kwargs):
        return super().dispatch(*args, **kwargs)

    def post(self, request):
        try:
            body = json.loads(request.body.decode('utf-8')) if request.body else {}
        except Exception:
            body = {}
        config_id = body.get('config_id') or request.GET.get('config_id')
        level = body.get('level', 'daily')

        if not config_id:
            return self.error_response('config_id 必填')

        try:
            config = DatabaseConfig.objects.get(id=config_id)
        except DatabaseConfig.DoesNotExist:
            return self.error_response(f'数据库 {config_id} 不存在', 404)

        # 触发巡检(同步执行 - 简化)
        from monitor.inspection_executor import InspectionExecutor
        run_id = f"INSP-{datetime.now().strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:6].upper()}"
        run = InspectionRun.objects.create(
            run_id=run_id,
            db_config=config,
            level=level,
            status='running',
            started_at=timezone.now(),
            triggered_by='api',
        )

        try:
            executor = InspectionExecutor(config, level=level)
            result = executor.run()
            # 写入结果
            run.status = result['status']
            run.finished_at = timezone.now()
            run.duration_sec = (run.finished_at - run.started_at).total_seconds()
            run.total_items = result['total_items']
            run.executed_items = result['executed_items']
            run.passed_items = result['passed_items']
            run.failed_items = result['failed_items']
            run.error_items = result.get('error_items', 0)
            run.critical_count = result['critical_count']
            run.error_count = result['error_count']
            run.warn_count = result['warn_count']
            run.info_count = result.get('info_count', 0)
            run.total_risk_score = result['total_risk_score']
            run.summary = result.get('summary', {})
            run.save()
        except Exception as e:
            logger.error(f"[Inspection API] 巡检执行失败: {e}")
            run.status = 'failed'
            run.error_message = str(e)
            run.finished_at = timezone.now()
            run.save()
            return self.error_response(f'巡检执行失败: {e}')

        return self.json_response({
            'run_id': run.run_id,
            'status': run.status,
            'total_items': run.total_items,
            'failed_items': run.failed_items,
            'critical_count': run.critical_count,
        })


class InspectionRunDetailView(_BaseView):
    """巡检执行详情(含所有 findings)"""

    @method_decorator(csrf_exempt)
    @method_decorator(require_auth)
    def dispatch(self, *args, **kwargs):
        return super().dispatch(*args, **kwargs)

    def get(self, request, run_id):
        try:
            run = InspectionRun.objects.get(run_id=run_id)
        except InspectionRun.DoesNotExist:
            return self.error_response(f'巡检 {run_id} 不存在', 404)

        findings = []
        for f in run.findings.all().order_by('-risk_score'):
            findings.append({
                'id': f.id,
                'item_code': f.item_code,
                'title': f.title,
                'category': f.category,
                'severity': f.severity,
                'risk_score': f.risk_score,
                'raw_data': f.raw_data,
                'recommendation': f.recommendation,
                'auto_fixable': f.auto_fixable,
                'status': f.status,
                'related_object': f.related_object,
            })

        return self.json_response({
            'run': {
                'run_id': run.run_id,
                'db_id': run.config_id,
                'db_name': run.db_config.name,
                'level': run.level,
                'status': run.status,
                'started_at': run.started_at,
                'finished_at': run.finished_at,
                'duration_sec': run.duration_sec,
                'total_items': run.total_items,
                'executed_items': run.executed_items,
                'passed_items': run.passed_items,
                'failed_items': run.failed_items,
                'critical_count': run.critical_count,
                'error_count': run.error_count,
                'warn_count': run.warn_count,
                'total_risk_score': run.total_risk_score,
            },
            'findings': findings,
        })


class InspectionItemListView(_BaseView):
    """巡检项定义列表"""

    @method_decorator(csrf_exempt)
    @method_decorator(require_auth)
    def dispatch(self, *args, **kwargs):
        return super().dispatch(*args, **kwargs)

    def get(self, request):
        level = request.GET.get('level')
        category = request.GET.get('category')
        db_type = request.GET.get('db_type')

        qs = InspectionItem.objects.filter(is_enabled=True)
        if level:
            qs = qs.filter(level=level)
        if category:
            qs = qs.filter(category=category)
        if db_type:
            qs = qs.filter(applicable_db_types__contains=db_type)

        items = []
        for it in qs[:200]:
            items.append({
                'item_code': it.item_id,
                'title': it.title,
                'category': it.category,
                'level': it.level,
                'severity': it.severity,
                'applicable_db_types': it.applicable_db_types,
                'description': it.description,
                'recommendation': it.recommendation,
                'auto_fixable': it.auto_fixable,
                'est_inspect_time_sec': it.est_inspect_time_sec,
            })
        return self.json_response({'total': len(items), 'items': items})


class InspectionIssuePatternListView(_BaseView):
    """巡检问题模式列表"""

    @method_decorator(csrf_exempt)
    @method_decorator(require_auth)
    def dispatch(self, *args, **kwargs):
        return super().dispatch(*args, **kwargs)

    def get(self, request):
        limit = int(request.GET.get('limit', 50))
        patterns = []
        for p in InspectionIssuePattern.objects.all().order_by('-occurrence_count')[:limit]:
            patterns.append({
                'pattern_signature': p.pattern_signature,
                'description': p.description,
                'category': p.category,
                'occurrence_count': p.occurrence_count,
                'first_seen': p.first_seen,
                'last_seen': p.last_seen,
                'severity': p.severity,
                'recommended_action': p.recommended_action,
                'auto_resolve_possible': p.auto_resolve_possible,
            })
        return self.json_response({'total': len(patterns), 'patterns': patterns})


# ==========================================
# 告警案例库接口
# ==========================================
class AlertCaseListView(_BaseView):
    """告警案例库列表"""

    @method_decorator(csrf_exempt)
    @method_decorator(require_auth)
    def dispatch(self, *args, **kwargs):
        return super().dispatch(*args, **kwargs)

    def get(self, request):
        db_type = request.GET.get('db_type')
        severity = request.GET.get('severity')
        keyword = request.GET.get('q')

        qs = AlertCase.objects.all()
        if db_type:
            qs = qs.filter(db_type=db_type)
        if severity:
            qs = qs.filter(severity=severity)
        if keyword:
            qs = qs.filter(title__icontains=keyword)

        cases = []
        for c in qs[:100]:
            cases.append({
                'case_id': c.case_id,
                'title': c.title,
                'db_type': c.db_type,
                'severity': c.severity,
                'root_cause': c.root_cause,
                'success_count': c.success_count,
                'fail_count': c.fail_count,
                'confidence': c.confidence,
                'tags': c.tags,
                'create_time': c.create_time,
                'last_used_at': c.last_used_at,
            })
        return self.json_response({'total': len(cases), 'cases': cases})


class AlertCaseSearchView(_BaseView):
    """案例库相似度检索(RAG)"""

    @method_decorator(csrf_exempt)
    @method_decorator(require_auth)
    def dispatch(self, *args, **kwargs):
        return super().dispatch(*args, **kwargs)

    def post(self, request):
        try:
            body = json.loads(request.body.decode('utf-8'))
        except Exception:
            return self.error_response('Invalid JSON')

        signature = body.get('signature', {})
        top_k = int(body.get('top_k', 5))
        db_type = body.get('db_type')

        # 简化版相似度计算 - 基于标签+数值范围匹配
        qs = AlertCase.objects.all()
        if db_type:
            qs = qs.filter(db_type=db_type)

        scored = []
        for case in qs[:200]:
            score = self._compute_similarity(signature, case.symptom_signature)
            if score > 0.3:
                scored.append((score, case))

        scored.sort(key=lambda x: -x[0])
        results = []
        for score, case in scored[:top_k]:
            results.append({
                'case_id': case.case_id,
                'title': case.title,
                'similarity': round(score, 3),
                'root_cause': case.root_cause,
                'resolution': case.resolution,
                'sql_used': case.sql_used,
                'tags': case.tags,
                'success_count': case.success_count,
            })
        return self.json_response({
            'total': len(results),
            'cases': results,
            'signature_keys': list(signature.keys()),
        })

    def _compute_similarity(self, sig_a, sig_b):
        """计算两个症状签名的相似度(简化版)"""
        if not sig_a or not sig_b:
            return 0.0
        common = set(sig_a.keys()) & set(sig_b.keys())
        if not common:
            return 0.0
        match = 0
        for k in common:
            try:
                va, vb = float(sig_a[k]), float(sig_b[k])
                if abs(va - vb) < max(abs(va), abs(vb), 1) * 0.3 + 0.1:
                    match += 1
            except (TypeError, ValueError):
                if sig_a[k] == sig_b[k]:
                    match += 1
        return match / max(len(sig_a), len(sig_b))


# ==========================================
# Phase 5 统计接口
# ==========================================
class Phase5StatsView(_BaseView):
    """Phase 5 总体统计"""

    @method_decorator(csrf_exempt)
    @method_decorator(require_auth)
    def dispatch(self, *args, **kwargs):
        return super().dispatch(*args, **kwargs)

    def get(self, request):
        return self.json_response({
            'rca': {
                'rule_count': self._get_rule_count(),
                'domains': ['connection', 'sql', 'lock', 'io', 'memory', 'replication', 'cluster', 'capacity', 'object', 'sequence', 'statistics', 'awr', 'config', 'security'],
            },
            'cases': {
                'total': AlertCase.objects.count(),
            },
            'remediation_plans': {
                'total': RemediationPlan.objects.count(),
                'success': RemediationPlan.objects.filter(status='success').count(),
                'pending': RemediationPlan.objects.filter(status='pending').count(),
            },
            'business_impacts': {
                'total': BusinessImpactAssessment.objects.count(),
                'fatal': BusinessImpactAssessment.objects.filter(overall_severity='fatal').count(),
            },
            'inspections': {
                'items_total': InspectionItem.objects.filter(is_enabled=True).count(),
                'runs_total': InspectionRun.objects.count(),
                'findings_open': InspectionFinding.objects.filter(status='open').count(),
                'patterns_known': InspectionIssuePattern.objects.count(),
            },
        })

    def _get_rule_count(self):
        try:
            from monitor.rca_engine_v2 import RULES_V2
            return len(RULES_V2)
        except Exception:
            return 0
