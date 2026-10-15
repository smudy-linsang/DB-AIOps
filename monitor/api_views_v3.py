# -*- coding: utf-8 -*-
"""DB-AIOps V3.0 OAM REST 接口（设计 §6）。

响应约定沿用 v2 的 `{code, message, timestamp, data}`（`api_views_v2._BaseV2View`）。
**为什么不复用 `_BaseV2View`**：本仓库既有风格是"每个版本文件自带一套原语"
（`api_views_phase8.py` 就自己复制了一份 `_BaseV2View` 形状不同的 `_BaseView`），
跨版本 import 会把两套响应约定搅在一起。

## 两条独立的闸（本项目红线，已复发五次）

- `require_permission` 管"能不能调这个接口"；
- `visible_to` 管"能看到哪些实例"。

**缺一不可，且测试也要分开验**：用零权限账号测范围会被 403 短路，等于没测到范围。
所以 `tests_v3_oam.py` 里 `Scope*` 与 `PermissionGate*` 两组用例是分开的。

文件名必须叫 `api_views_v3.py`：`scripts/lint_redlines.py` 的 `SCOPED_QUERY_FILES`
按**文件名词元组**匹配（`'api_views'`、`'views_enhanced'`、`'copilot'`、`'sse_views'`），
名字不含 `api_views` 就会绕过 `[未套数据范围]` 规则。

## 诊断接口的两条硬约束（设计 §6）

1. `POST /diagnose/` **只产出报告，不执行任何变更**。响应里的推荐修复须附
   `risk_level` 与"需人工审批"标记；真要执行走既有的
   `/api/v2/playbooks/execute-safely/`（已接 `decide_trigger` 审批链）。
2. 报告未通过证据审计时 `report.status='evidence_insufficient'` 且**不得给出根因**，
   同时 `escalated=true`。**这是合法终止，不是错误** ——
   前端与评测都按"不计成功、也不计幻觉"处理。
"""
import json
import logging

from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt

from monitor.auth import Perm, require_auth, require_permission
from monitor.models import DatabaseConfig, OamBenchRun, OamCase

logger = logging.getLogger("monitor.api_v3")


def _json_default(obj):
    if hasattr(obj, 'isoformat'):
        return obj.isoformat()
    return str(obj)


class _BaseV3View(View):
    """与 `api_views_v2._BaseV2View` 同形：`{code, message, timestamp, data}`。"""

    def json_response(self, data, status=200):
        return json_response(data, status=status)

    def ok(self, data=None, **kwargs):
        res = {'code': 'OK', 'message': '操作成功', 'timestamp': timezone.now().isoformat()}
        res['data'] = data if data is not None else kwargs
        return self.json_response(res)

    def err(self, code, message, status=400):
        return self.json_response({'code': code, 'message': message,
                                   'timestamp': timezone.now().isoformat()}, status=status)

    def body(self, request) -> dict:
        try:
            return json.loads(request.body or b'{}')
        except (ValueError, TypeError):
            return {}


def json_response(data, status=200):
    from django.http import JsonResponse
    return JsonResponse(data, status=status, json_dumps_params={'default': _json_default})


def _username(request) -> str:
    return getattr(request.user, 'username', '') or str(request.user)


def _scoped_config(request, config_id):
    """按当前用户的数据范围解析实例；越权与不存在统一为 None。"""
    try:
        config_id = int(config_id)
    except (TypeError, ValueError):
        return None
    cfg = DatabaseConfig.objects.visible_to(request.user).filter(id=config_id).first()
    if cfg is None:
        logger.warning("[api-v3] 实例访问被拒 config_id=%s user=%s exists=%s",
                       config_id, _username(request),
                       DatabaseConfig.objects.filter(id=config_id).exists())  # scope-check: allow 审计存在性判定(故意全局)
    return cfg


def _scoped_trajectory(request, traj_id):
    """按数据范围解析轨迹。

    范围判定取"轨迹自身绑定的实例"或"其用例的来源实例"，两者都为空时
    视为纯离线评测记录（不含任何实例数据）予以放行 —— 但必须在日志里留痕，
    否则"忘了绑实例"会和"在范围内"长得一模一样。
    """
    from monitor.models import OamTrajectory
    traj = (OamTrajectory.objects.select_related('case', 'config')
            .filter(traj_id=traj_id).first())
    if traj is None:
        logger.warning("[api-v3] 轨迹访问被拒 traj_id=%s user=%s", traj_id, _username(request))
        return None

    anchor_id = traj.config_id or getattr(traj.case, 'source_config_id', None)
    if anchor_id is None:
        logger.info("[api-v3] 轨迹 %s 未绑定任何实例（离线评测记录），用户=%s",
                    traj_id, _username(request))
        return traj
    allowed = DatabaseConfig.objects.visible_to(request.user).filter(id=anchor_id).exists()
    if not allowed:
        logger.warning("[api-v3] 轨迹越权 traj_id=%s anchor_config=%s user=%s exists=%s",
                       traj_id, anchor_id, _username(request),
                       DatabaseConfig.objects.filter(id=anchor_id).exists())  # scope-check: allow 审计存在性判定(故意全局)
        return None
    return traj


def _scoped_case(request, case_id):
    from monitor.models import OamCase as _OamCase
    case = _OamCase.objects.select_related('source_config').filter(case_id=case_id).first()
    if case is None:
        return None
    if case.source_config_id is None:
        return case
    if not DatabaseConfig.objects.visible_to(request.user).filter(
            id=case.source_config_id).exists():
        logger.warning("[api-v3] 用例越权 case_id=%s source_config=%s user=%s",
                       case_id, case.source_config_id, _username(request))
        return None
    return case


# =============================================================================
# 1. 诊断
# =============================================================================
class OamDiagnoseView(_BaseV3View):
    """POST /api/v3/oam/diagnose/ (发起一次诊断，只产出报告不执行变更)"""

    @method_decorator(csrf_exempt)
    @method_decorator(require_auth)
    @method_decorator(require_permission(Perm.TICKETS_EXECUTE))
    def dispatch(self, *a, **k):
        return super().dispatch(*a, **k)

    def post(self, request):
        data = self.body(request)
        config_id = data.get('config_id')
        symptom = str(data.get('symptom') or '').strip()
        if not config_id or not symptom:
            return self.err('BAD_REQUEST', '缺少 config_id 或 symptom', 400)
        cfg = _scoped_config(request, config_id)
        if not cfg:
            return self.err('NOT_FOUND', f'数据库 {config_id} 不存在', 404)

        from monitor.oam import oam_enabled
        if not oam_enabled():
            return self.err('DISABLED', 'OAM 未启用（配置项 OAM_ENABLED）', 409)

        from monitor.oam.bench.runner import run_case
        # 入参先过枚举闸：category/difficulty 有 CheckConstraint 兜底，
        # 但让它撞 DB 约束会变成未捕获的 IntegrityError（HTTP 500），
        # 调用方看到的是"服务器炸了"而不是"你的 category 写错了"。
        valid_categories = {c for c, _ in OamCase.CATEGORY}
        valid_difficulties = {d for d, _ in OamCase.DIFFICULTY}
        category = data.get('category') or 'inspection'
        difficulty = data.get('difficulty') or 'easy'
        if category not in valid_categories:
            return self.err('BAD_REQUEST',
                            f'category 非法，合法取值={sorted(valid_categories)}', 400)
        if difficulty not in valid_difficulties:
            return self.err('BAD_REQUEST',
                            f'difficulty 非法，合法取值={sorted(valid_difficulties)}', 400)

        # 线上诊断没有专家标注真值：`gt_root_cause` 只是标签空间的占位，
        # 不参与验证器判定（设计 §9.1 —— 封闭标签空间只用于离线评测）。
        case = OamCase(case_id=f'LIVE-{cfg.id}-{timezone.now().strftime("%Y%m%d%H%M%S")}',
                       category=category, difficulty=difficulty,
                       db_type=cfg.db_type, symptom=symptom,
                       gt_root_cause='CONFIG_DRIFT',
                       source_config=cfg, split='train')
        # **必须先落库**：`OamTrajectory.case` 是指向 `OamCase` 的外键，
        # 用例没保存时轨迹插入会违反外键约束 —— 生产环境立即抛错，
        # 被 `_persist` 的兜底 except 吞成 degrade 留痕，
        # 接口照样 200，但"发起诊断 → 查看轨迹 → 查看审计"整条链路是断的。
        case.save()
        try:
            traj, verdict, audit_res, _env = run_case(case, persist=True, score=False)
        except Exception as e:
            logger.exception('[api-v3] 诊断执行失败 config_id=%s: %s', cfg.id, e)
            return self.err('DIAGNOSE_FAILED', f'诊断执行失败：{type(e).__name__}', 500)

        report = _build_report(traj, verdict, audit_res)
        # 设计 §6：接口必须回传 `traj_id`，否则调用方拿不到回查轨迹/审计的句柄。
        traj_id = _traj_id_of(traj, case)
        report['traj_id'] = traj_id
        report['trajectory_url'] = f'/api/v3/oam/trajectories/{traj_id}/'
        report['audit_url'] = f'/api/v3/oam/trajectories/{traj_id}/audit/'
        return self.ok(report=report, traj_id=traj_id)


def _traj_id_of(traj, case) -> str:
    from monitor.oam.bench.runner import traj_traj_id
    return traj_traj_id(traj, case)


def _build_report(traj, verdict, audit_res) -> dict:
    """组装对外报告。

    两条硬约束在这里落地：
      · 未通过证据审计 ⇒ `status='evidence_insufficient'` 且**不给根因**；
      · 推荐修复必须附 `risk_level` 与"需人工审批"标记（只诊断不执行）。
    """
    report = dict(traj.report or {})
    if not audit_res.passed:
        report['status'] = 'evidence_insufficient'
        report['root_cause'] = ''
        report['evidence_insufficient_reason'] = audit_res.reason
        report['unsupported_claims'] = [
            {'kind': c.kind, 'text': c.text, 'evidence_refs': c.evidence_refs}
            for c in audit_res.unsupported]
    report['escalated'] = bool(traj.escalated or audit_res.route_to_human)
    # 线上诊断不评分：`verdict=None` 时如实标注"未判定"，
    # 不拿一个捏造的真值去判分（那会稳定回传"根因不匹配"，
    # 把无意义的信息当成验收结论 —— 设计 §9.1）。
    if verdict is None:
        report['verdict'] = {
            'passed': None, 'scored': False,
            'reason': '线上诊断无专家标注真值，不参与验证器判定（仅供人工研判）'}
    else:
        report['verdict'] = {'passed': bool(verdict.passed),
                             'scored': True, 'reason': verdict.reason}
    report['audit'] = audit_res.to_dict()
    report['recommended_fix'] = {
        'spec': report.get('fix', ''),
        'risk_level': 'unknown',       # 线上诊断不判风险等级，执行前必须人工评估
        'requires_approval': True,
        'execute_via': '/api/v2/playbooks/execute-safely/',
        'note': '本接口不执行任何变更；变更须走既有审批链',
    }
    return report


class OamTrajectoryView(_BaseV3View):
    """GET /api/v3/oam/trajectories/<traj_id>/ (完整思考-行动-观察轨迹)"""

    @method_decorator(csrf_exempt)
    @method_decorator(require_auth)
    @method_decorator(require_permission(Perm.ALERTS_VIEW))
    def dispatch(self, *a, **k):
        return super().dispatch(*a, **k)

    def get(self, request, traj_id):
        traj = _scoped_trajectory(request, traj_id)
        if not traj:
            return self.err('NOT_FOUND', f'轨迹 {traj_id} 不存在', 404)
        return self.ok({
            'traj_id': traj.traj_id,
            'case_id': traj.case_id,
            'policy_backend': traj.policy_backend,
            'policy_version': traj.policy_version,
            'passed': traj.passed,
            'escalated': traj.escalated,
            'hallucinated': traj.hallucinated,
            'verdict_reason': traj.verdict_reason,
            'report': traj.report,
            'turns': [{
                'turn': s.turn,
                'thought': s.thought,
                'tool_name': s.tool_name,
                'tool_params': s.tool_params,
                'tool_error_code': s.tool_error_code,
                'observation': s.observation,
                'observation_evidence': s.observation_evidence,
            } for s in traj.steps.all()],
            'started_at': traj.started_at,
            'finished_at': traj.finished_at,
        })


class OamTrajectoryAuditView(_BaseV3View):
    """GET /api/v3/oam/trajectories/<traj_id>/audit/ (证据审计结果)"""

    @method_decorator(csrf_exempt)
    @method_decorator(require_auth)
    @method_decorator(require_permission(Perm.ALERTS_VIEW))
    def dispatch(self, *a, **k):
        return super().dispatch(*a, **k)

    def get(self, request, traj_id):
        traj = _scoped_trajectory(request, traj_id)
        if not traj:
            return self.err('NOT_FOUND', f'轨迹 {traj_id} 不存在', 404)
        from monitor.oam.audit import audit
        result = audit(traj, traj.report)
        return self.ok({
            'traj_id': traj.traj_id,
            'audit': result.to_dict(),
            'hallucinated': traj.hallucinated,
            'escalated': traj.escalated,
        })


# =============================================================================
# 2. 基准用例与快照
# =============================================================================
class OamCaseListView(_BaseV3View):
    """POST /api/v3/oam/cases/ (从工单登记基准用例)"""

    @method_decorator(csrf_exempt)
    @method_decorator(require_auth)
    @method_decorator(require_permission(Perm.ALERT_CONFIG_MANAGE))
    def dispatch(self, *a, **k):
        return super().dispatch(*a, **k)

    def post(self, request):
        data = self.body(request)
        case_id = str(data.get('case_id') or '').strip()
        db_type = str(data.get('db_type') or '').strip()
        gt = str(data.get('gt_root_cause') or '').strip()
        if not case_id or not db_type or not gt:
            return self.err('BAD_REQUEST', '缺少 case_id / db_type / gt_root_cause', 400)

        # 根因必须在该方言的封闭标签空间内 —— 标签空间定不下来，
        # 验证器就无从判定（设计 §9.1）。非法标签不入库。
        from monitor.oam.labels import assert_label
        try:
            gt = assert_label(gt, db_type)
        except ValueError as e:
            return self.err('INVALID_LABEL', str(e), 400)

        if data.get('source_config_id'):
            cfg = _scoped_config(request, data['source_config_id'])
            if not cfg:
                return self.err('NOT_FOUND', '来源实例不存在', 404)
        case = OamCase(
            case_id=case_id,
            category=data.get('category') or 'inspection',
            difficulty=data.get('difficulty') or 'easy',
            db_type=db_type, symptom=str(data.get('symptom') or ''),
            gt_root_cause=gt,
            fix_spec=data.get('fix_spec') or {},
            fix_executable=bool(data.get('fix_executable')),
            required_evidence=data.get('required_evidence') or [],
            source_ticket=str(data.get('source_ticket') or ''),
            source_config_id=data.get('source_config_id') or None,
            split=data.get('split') or 'train',
            is_deidentified=bool(data.get('is_deidentified')))
        try:
            case.full_clean(exclude=['case_id'])
        except Exception as e:
            return self.err('VALIDATION_FAILED', str(e), 400)
        case.save()
        return self.ok(case_id=case.case_id, db_type=case.db_type,
                       gt_root_cause=case.gt_root_cause, split=case.split)


class OamCaseSnapshotView(_BaseV3View):
    """POST /api/v3/oam/cases/<case_id>/snapshot/ (上传冻结快照)"""

    @method_decorator(csrf_exempt)
    @method_decorator(require_auth)
    @method_decorator(require_permission(Perm.ALERT_CONFIG_MANAGE))
    def dispatch(self, *a, **k):
        return super().dispatch(*a, **k)

    def post(self, request, case_id):
        case = _scoped_case(request, case_id)
        if not case:
            return self.err('NOT_FOUND', f'用例 {case_id} 不存在', 404)
        data = self.body(request)
        window = data.get('window') or []
        if len(window) != 2:
            return self.err('BAD_REQUEST', 'window 必须为 [since, until]', 400)

        from monitor.oam.env.snapshot import OamSnapshot, SnapshotRangeError
        from monitor.models import OamSnapshotBlob
        snap = OamSnapshot.from_dict({
            'case_id': case_id, 'db_type': case.db_type,
            'kpi_series': data.get('kpi_series') or {},
            'window': window,
            'session_samples': data.get('session_samples') or [],
            'plans': data.get('plans') or [],
            'logs': data.get('logs') or [],
            'config_meta': data.get('config_meta') or {},
            'schema_meta': data.get('schema_meta') or {},
        })
        try:
            snap.validate(strict=True)
        except SnapshotRangeError as e:
            # 结构性越界一律明确拒绝，不静默截断（设计 §4.1）
            return self.err('SNAPSHOT_RANGE', str(e), 400)

        blob, _created = OamSnapshotBlob.objects.update_or_create(
            case=case, defaults={
                'window_start': snap.window[0], 'window_end': snap.window[1],
                # 用 `to_model_dict()` 而不是手写字典：它会显式把 datetime
                # 转成 ISO 字符串（JSONField 存不下 datetime）。手写
                # `[list(p) for p in v]` 会把 datetime 直接塞进 JSONField，
                # 成功路径必然抛 TypeError → 接口 500。
                **snap.to_model_dict()})
        return self.ok(case_id=case_id, window_hours=blob.window_hours,
                       series_lengths=snap.series_lengths())


# =============================================================================
# 3. 基准评测
# =============================================================================
class OamBenchRunView(_BaseV3View):
    """POST /api/v3/oam/bench/run/ (跑基准)"""

    @method_decorator(csrf_exempt)
    @method_decorator(require_auth)
    @method_decorator(require_permission(Perm.ALERT_CONFIG_MANAGE))
    def dispatch(self, *a, **k):
        return super().dispatch(*a, **k)

    def post(self, request):
        data = self.body(request)
        split = data.get('split') or 'test'
        backend = data.get('policy_backend') or 'prompted'
        qs = OamCase.objects.filter(split=split)
        if data.get('category'):
            qs = qs.filter(category=data['category'])
        if data.get('difficulty'):
            qs = qs.filter(difficulty=data['difficulty'])

        # 数据范围：用例若绑定了来源实例，则只跑范围内的用例。
        visible_ids = [c.case_id for c in
                       DatabaseConfig.objects.visible_to(request.user)
                       .values_list('id', flat=True)]
        allowed = [c.case_id for c in qs if (c.source_config_id is None
                                            or c.source_config_id in visible_ids)]

        from monitor.oam.bench.runner import run_bench
        report = run_bench([OamCase.objects.get(pk=cid) for cid in allowed],
                           policy_backend=backend, split=split,
                           category=data.get('category') or '',
                           difficulty=data.get('difficulty') or '')
        return self.ok(**report)


class OamBenchReportView(_BaseV3View):
    """GET /api/v3/oam/bench/report/<run_id>/ (成绩 + 覆盖度同屏)"""

    @method_decorator(csrf_exempt)
    @method_decorator(require_auth)
    @method_decorator(require_permission(Perm.METRICS_VIEW))
    def dispatch(self, *a, **k):
        return super().dispatch(*a, **k)

    def get(self, request, run_id):
        run = OamBenchRun.objects.filter(run_id=run_id).first()
        if not run:
            return self.err('NOT_FOUND', f'评测批次 {run_id} 不存在', 404)
        payload = run.to_dict_report()
        # 缺 coverage 视为接口未实现 —— 这里兜一层，宁可报错也不给"看起来完整"的响应
        if 'coverage' not in payload or not payload['coverage']:
            return self.err('COVERAGE_MISSING',
                            '该评测记录缺少覆盖度数据，按设计视为未完成评测', 500)
        if payload['coverage'].get('root_causes_missing'):
            payload['coverage']['warning'] = (
                '该成绩不覆盖以下根因：'
                + '、'.join(payload['coverage']['root_causes_missing']))
        return self.ok(**payload)


# =============================================================================
# 4. 知识库
# =============================================================================
class OamKbStalenessView(_BaseV3View):
    """GET /api/v3/oam/kb/staleness/ (KB 新鲜度看板)"""

    @method_decorator(csrf_exempt)
    @method_decorator(require_auth)
    @method_decorator(require_permission(Perm.ALERT_CONFIG_VIEW))
    def dispatch(self, *a, **k):
        return super().dispatch(*a, **k)

    def get(self, request):
        from monitor import appconf
        from monitor.models import KbDocument, OamRetrievalMiss
        warn_days = appconf.get('OAM_KB_STALENESS_WARN_DAYS')
        docs = list(KbDocument.objects.filter(is_active=True))
        by_category, stale = {}, []
        for d in docs:
            by_category.setdefault(d.category, []).append(d.staleness_days)
            if d.staleness_days > warn_days:
                stale.append({'doc_id': d.doc_id, 'title': d.title,
                              'staleness_days': d.staleness_days,
                              'approved_by': d.approved_by})
        dist = {c: {'count': len(v), 'max_days': max(v) if v else 0,
                    'over_threshold': sum(1 for x in v if x > warn_days)}
                for c, v in sorted(by_category.items())}
        misses = (OamRetrievalMiss.objects
                  .values('kb_category', 'db_type')
                  .order_by('kb_category').count())
        return self.ok(threshold_days=warn_days, by_category=dist,
                       stale_items=sorted(stale, key=lambda x: -x['staleness_days']),
                       retrieval_miss_total=misses,
                       total_docs=len(docs))


class OamKbDocumentsView(_BaseV3View):
    """GET/POST /api/v3/oam/kb/documents/ (KB 文档管理)"""

    @method_decorator(csrf_exempt)
    @method_decorator(require_auth)
    @method_decorator(require_permission(Perm.ALERT_CONFIG_VIEW))
    def dispatch(self, *a, **k):
        return super().dispatch(*a, **k)

    def get(self, request):
        from monitor.models import KbDocument
        docs = KbDocument.objects.filter(is_active=True).order_by('category', 'doc_id')
        return self.ok(items=[{'doc_id': d.doc_id, 'category': d.category,
                               'db_types': d.db_types, 'title': d.title,
                               'version': d.version, 'approved_by': d.approved_by,
                               'staleness_days': d.staleness_days}
                              for d in docs[:200]], total=docs.count())

    def post(self, request):
        # 写操作需要 manage 权限，而 dispatch 上挂的是 view —— 这里二次校验，
        # 保证"读"与"写"两道闸真的独立（V2.6 W1 曾一次补出 12 个只有 require_auth 的视图）
        from monitor.auth import has_permission
        if not has_permission(request.user, Perm.ALERT_CONFIG_MANAGE):
            return self.err('PERMISSION_DENIED',
                            '写操作需要 alert_config.manage', 403)
        return self.err('NOT_IMPLEMENTED',
                        'KB 文档写入须经"人工复核 + 去标识 + 带版本审批"闸门，'
                        'P0 未开放直接写入；请走 oam_kb_import 管理命令', 501)


class OamKbRebuildView(_BaseV3View):
    """POST /api/v3/oam/kb/rebuild/ (触发三步构建管线)"""

    @method_decorator(csrf_exempt)
    @method_decorator(require_auth)
    @method_decorator(require_permission(Perm.ALERT_CONFIG_MANAGE))
    def dispatch(self, *a, **k):
        return super().dispatch(*a, **k)

    def post(self, request):
        return self.err('NOT_IMPLEMENTED',
                        'KB 重建须经管理命令 oam_kb_rebuild 执行（需版本化产物与审批链），'
                        'P0 未从 HTTP 暴露', 501)


class OamToolsView(_BaseV3View):
    """GET /api/v3/oam/tools/ (工具目录与语义增强描述)"""

    @method_decorator(csrf_exempt)
    @method_decorator(require_auth)
    @method_decorator(require_permission(Perm.METRICS_VIEW))
    def dispatch(self, *a, **k):
        return super().dispatch(*a, **k)

    def get(self, request):
        from monitor.oam.tool_catalog import mutating_tools, tools_for
        db_type = request.GET.get('db_type') or ''
        if not db_type:
            from monitor.oam.labels import ALL_DIALECTS
            return self.ok(db_types=list(ALL_DIALECTS))
        items = [{'name': s.name, 'kind': s.kind, 'db_type': s.db_type,
                  'description': s.enriched_description,
                  'params': s.params_schema.get('properties', {}),
                  'replayable': s.replayable}
                 for s in tools_for(db_type)]
        return self.ok(db_type=db_type, tools=items,
                       mutating=[s.name for s in mutating_tools(db_type)],
                       note='变更类工具不在策略动作空间内，一律返回 MUTATION_FORBIDDEN')
