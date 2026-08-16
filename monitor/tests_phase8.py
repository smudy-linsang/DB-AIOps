# -*- coding: utf-8 -*-
"""
Phase 8 单元测试 (phase8/20 设计验收)。

纯函数部分 (SimpleTestCase, 不触库):
  - llm/schemas 三契约校验 + 围栏剥离 + 语义修正
  - causal_miner 皮尔逊/滞后互相关/方差
  - autonomy_policy 分级放权矩阵
  - change_stream dedup_key 小时桶
  - llm 开关默认行为
数据库部分 (TestCase, 需 PostgreSQL):
  - change_stream.record_change 幂等
  - RcaFeedback/PlanFeedback 反馈幂等 (update_or_create)
  - rule_calibrator 校准公式 (partial 0.5 票 / 样本不足维持 0.6)

运行: ./venv/bin/python manage.py test monitor.tests_phase8
"""
from datetime import datetime, timezone as dt_tz
from types import SimpleNamespace

from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone


# ==========================================================================
# 1. llm/schemas 契约校验
# ==========================================================================
class SchemaValidationTests(SimpleTestCase):

    def test_strip_fences_plain_json(self):
        from monitor.llm.schemas import _strip_fences
        self.assertEqual(_strip_fences('{"a": 1}'), '{"a": 1}')

    def test_strip_fences_markdown(self):
        from monitor.llm.schemas import _strip_fences
        text = '```json\n{"a": 1}\n```'
        self.assertEqual(_strip_fences(text), '{"a": 1}')

    def test_strip_fences_with_noise(self):
        from monitor.llm.schemas import _strip_fences
        text = '好的，以下是结果：\n{"a": 1}\n以上。'
        self.assertEqual(_strip_fences(text), '{"a": 1}')

    def test_agent_step_valid(self):
        from monitor.llm.schemas import validate_agent_step
        text = '{"thought": "先看会话", "action": "get_active_sessions", "params": {"limit": 10}}'
        obj = validate_agent_step(text, allowed_actions=['get_active_sessions'])
        self.assertEqual(obj['action'], 'get_active_sessions')
        self.assertEqual(obj['params'], {'limit': 10})

    def test_agent_step_final_requires_final_object(self):
        from monitor.llm.schemas import SchemaValidationError, validate_agent_step
        # action=final 但缺 final 对象 → allOf if-then 拒绝
        with self.assertRaises(SchemaValidationError):
            validate_agent_step('{"thought": "结束", "action": "final"}')

    def test_agent_step_final_ok(self):
        from monitor.llm.schemas import validate_agent_step
        text = ('{"thought": "确认根因", "action": "final", '
                '"final": {"root_cause": "锁等待", "confidence": 0.8, '
                '"evidence_summary": "阻塞链", "suggestions": ["kill 阻塞源"]}}')
        obj = validate_agent_step(text, allowed_actions=[])
        self.assertEqual(obj['final']['root_cause'], '锁等待')

    def test_agent_step_rejects_unknown_action(self):
        from monitor.llm.schemas import SchemaValidationError, validate_agent_step
        with self.assertRaises(SchemaValidationError):
            validate_agent_step('{"thought": "x", "action": "drop_table"}',
                                allowed_actions=['get_active_sessions'])

    def test_agent_step_bad_json(self):
        from monitor.llm.schemas import SchemaValidationError, validate_agent_step
        with self.assertRaises(SchemaValidationError):
            validate_agent_step('这不是 JSON')

    def test_diagnosis_semantic_fix(self):
        from monitor.llm.schemas import validate_diagnosis_output
        text = ('{"summary": "s", "hypotheses": [{"name": "慢SQL", '
                '"confidence": 0.95, "reasoning": "r"}]}')
        obj = validate_diagnosis_output(text)
        h = obj['hypotheses'][0]
        self.assertEqual(h['verdict_on_rule'], 'new')     # 默认值补齐
        self.assertEqual(h['rule_id'], '')
        self.assertEqual(h['suggestions'], [])
        self.assertEqual(obj['need_more_evidence'], [])

    def test_diagnosis_missing_required_raises(self):
        from monitor.llm.schemas import SchemaValidationError, validate_diagnosis_output
        with self.assertRaises(SchemaValidationError):
            validate_diagnosis_output('{"summary": "无假设列表"}')

    def test_distill_defaults(self):
        from monitor.llm.schemas import validate_distill_output
        obj = validate_distill_output(
            '{"title": "t", "root_cause": "rc", "resolution": "fix"}')
        self.assertEqual(obj['tags'], [])
        self.assertEqual(obj['symptom_text'], '')


# ==========================================================================
# 2. causal_miner 数学函数
# ==========================================================================
class CausalMathTests(SimpleTestCase):

    def test_pearson_perfect_positive(self):
        from monitor.causal_miner import _pearson
        xs = [1, 2, 3, 4, 5]
        ys = [2, 4, 6, 8, 10]
        self.assertAlmostEqual(_pearson(xs, ys), 1.0, places=6)

    def test_pearson_perfect_negative(self):
        from monitor.causal_miner import _pearson
        self.assertAlmostEqual(_pearson([1, 2, 3], [3, 2, 1]), -1.0, places=6)

    def test_pearson_constant_series_zero(self):
        from monitor.causal_miner import _pearson
        self.assertEqual(_pearson([5, 5, 5], [1, 2, 3]), 0.0)

    def test_pearson_too_short(self):
        from monitor.causal_miner import _pearson
        self.assertEqual(_pearson([1], [2]), 0.0)

    def test_variance(self):
        from monitor.causal_miner import _variance
        self.assertAlmostEqual(_variance({1: 2.0, 2: 4.0, 3: 6.0}),
                               8.0 / 3, places=6)
        self.assertEqual(_variance({1: 9.9}), 0.0)

    def test_lagged_corr_detects_shift(self):
        from monitor.causal_miner import _lagged_corr
        # cause[t] 与 effect[t+5min] 完全线性 → lag=5 时 r≈1
        step = 5 * 60 * 1000
        cause = {i * step: float(i) for i in range(60)}
        effect = {i * step + step: float(i) * 3 + 1 for i in range(60)}
        r, n = _lagged_corr(cause, effect, lag_min=5)
        self.assertAlmostEqual(r, 1.0, places=6)
        self.assertEqual(n, 60)

    def test_lagged_corr_no_overlap(self):
        from monitor.causal_miner import _lagged_corr
        r, n = _lagged_corr({0: 1.0}, {999999: 2.0}, lag_min=5)
        self.assertEqual((r, n), (0.0, 0))


# ==========================================================================
# 3. autonomy_policy 分级放权矩阵
# ==========================================================================
def _fake_incident(level):
    return SimpleNamespace(config=SimpleNamespace(autonomy_level=level))


def _fake_playbook(risk, rollback=None):
    return SimpleNamespace(risk_level=risk, rollback=rollback or [])


class AutonomyPolicyTests(SimpleTestCase):

    def test_effective_level_instance_override(self):
        from monitor.autonomy_policy import effective_level
        self.assertEqual(effective_level(SimpleNamespace(autonomy_level=2)), 2)

    @override_settings(AUTONOMY_DEFAULT_LEVEL=2)
    def test_effective_level_follows_global_default(self):
        from monitor.autonomy_policy import effective_level
        self.assertEqual(effective_level(SimpleNamespace(autonomy_level=None)), 2)

    def test_effective_level_clamped(self):
        from monitor.autonomy_policy import effective_level
        self.assertEqual(effective_level(SimpleNamespace(autonomy_level=9)), 3)
        self.assertEqual(effective_level(SimpleNamespace(autonomy_level=-1)), 0)

    def test_forbidden_plan_llm_advisory(self):
        from monitor.autonomy_policy import is_forbidden_plan
        self.assertTrue(is_forbidden_plan({'scenario': 'llm_advisory'}))
        self.assertTrue(is_forbidden_plan({'executable': False}))
        self.assertFalse(is_forbidden_plan({'scenario': 'kill_blocking_session'}))
        self.assertFalse(is_forbidden_plan(None))   # 空入参不抛异常

    def test_gate_l0_forces_approval(self):
        from monitor.autonomy_policy import gate
        d = gate(_fake_incident(0), _fake_playbook('low'))
        self.assertEqual(d['mode'], 'approved')
        self.assertFalse(d['execute_now'])
        self.assertTrue(d['need_approval'])

    def test_gate_l1_falls_back(self):
        from monitor.autonomy_policy import gate
        self.assertIsNone(gate(_fake_incident(1), _fake_playbook('low')))
        self.assertIsNone(gate(_fake_incident(1), _fake_playbook('high')))

    def test_gate_l2_low_risk_auto(self):
        from monitor.autonomy_policy import gate
        d = gate(_fake_incident(2), _fake_playbook('low'))
        self.assertEqual(d['mode'], 'auto')
        self.assertTrue(d['execute_now'])
        self.assertFalse(d['need_approval'])

    def test_gate_l2_mid_risk_falls_back(self):
        from monitor.autonomy_policy import gate
        self.assertIsNone(gate(_fake_incident(2), _fake_playbook('mid', ['回滚步骤'])))

    def test_gate_l3_mid_with_rollback_auto_with_audit(self):
        from monitor.autonomy_policy import gate
        d = gate(_fake_incident(3), _fake_playbook('mid', ['rollback sql']))
        self.assertEqual(d['mode'], 'auto')
        self.assertTrue(d['execute_now'])
        self.assertTrue(d['need_approval'])   # 补审留痕

    def test_gate_l3_mid_without_rollback_falls_back(self):
        from monitor.autonomy_policy import gate
        self.assertIsNone(gate(_fake_incident(3), _fake_playbook('mid', [])))

    def test_gate_l3_high_risk_never_auto(self):
        from monitor.autonomy_policy import gate
        self.assertIsNone(gate(_fake_incident(3), _fake_playbook('high', ['rb'])))


# ==========================================================================
# 4. change_stream dedup_key
# ==========================================================================
class DedupKeyTests(SimpleTestCase):

    def test_same_hour_bucket_same_key(self):
        from monitor.change_stream import _make_dedup_key
        t1 = datetime(2026, 7, 27, 10, 5, tzinfo=dt_tz.utc)
        t2 = datetime(2026, 7, 27, 10, 55, tzinfo=dt_tz.utc)
        self.assertEqual(_make_dedup_key(1, 'manual', '重启', t1),
                         _make_dedup_key(1, 'manual', '重启', t2))

    def test_different_hour_or_title_differs(self):
        from monitor.change_stream import _make_dedup_key
        t1 = datetime(2026, 7, 27, 10, 5, tzinfo=dt_tz.utc)
        t2 = datetime(2026, 7, 27, 11, 5, tzinfo=dt_tz.utc)
        self.assertNotEqual(_make_dedup_key(1, 'manual', '重启', t1),
                            _make_dedup_key(1, 'manual', '重启', t2))
        self.assertNotEqual(_make_dedup_key(1, 'manual', '重启', t1),
                            _make_dedup_key(1, 'manual', '扩容', t1))
        self.assertEqual(len(_make_dedup_key(1, 'manual', '重启', t1)), 40)


# ==========================================================================
# 5. 开关默认行为 (红线: 默认全关, 等同 Phase 7)
# ==========================================================================
class FeatureSwitchTests(SimpleTestCase):

    @override_settings(LLM_ENABLED=False, EMBED_ENABLED=False, AGENT_ENABLED=False)
    def test_all_disabled_by_default(self):
        from monitor.llm import agent_enabled, embed_enabled, llm_enabled
        self.assertFalse(llm_enabled())
        self.assertFalse(embed_enabled())
        self.assertFalse(agent_enabled())

    @override_settings(LLM_ENABLED=False, AGENT_ENABLED=True)
    def test_agent_requires_llm(self):
        from monitor.llm import agent_enabled
        self.assertFalse(agent_enabled())   # Agent 依赖 LLM 总开关

    @override_settings(LLM_ENABLED=True, AGENT_ENABLED=True)
    def test_agent_enabled_when_both_on(self):
        from monitor.llm import agent_enabled
        self.assertTrue(agent_enabled())

    @override_settings(RULE_CALIBRATION_ENABLED=False)
    def test_calibration_disabled_returns_default(self):
        from monitor.rule_calibrator import DEFAULT_BASE, get_calibrated_base
        self.assertEqual(get_calibrated_base('R001'), DEFAULT_BASE)


# ==========================================================================
# 6. DB: 变更流幂等 (需 PostgreSQL)
# ==========================================================================
class ChangeStreamDbTests(TestCase):

    @classmethod
    def setUpTestData(cls):
        from monitor.models import DatabaseConfig
        cls.config = DatabaseConfig.objects.create(
            name='ut-mysql', db_type='mysql', host='127.0.0.1',
            port=3306, username='u', password='p')

    def test_record_change_idempotent(self):
        from monitor.change_stream import record_change
        from monitor.models import ChangeEvent
        at = timezone.now()
        obj = record_change(self.config, 'manual', '调整 innodb_buffer_pool_size',
                            change_type='param_change', operator='dba', occurred_at=at)
        self.assertIsNotNone(obj)
        # 同源同标题同小时桶 → 去重, 返回 None 且不新增
        dup = record_change(self.config, 'manual', '调整 innodb_buffer_pool_size',
                            occurred_at=at)
        self.assertIsNone(dup)
        self.assertEqual(ChangeEvent.objects.filter(config=self.config).count(), 1)

    @override_settings(CHANGE_STREAM_ENABLED=False)
    def test_record_change_disabled(self):
        from monitor.change_stream import record_change
        from monitor.models import ChangeEvent
        self.assertIsNone(record_change(self.config, 'manual', '开关关闭时不落库'))
        self.assertEqual(
            ChangeEvent.objects.filter(config=self.config, title='开关关闭时不落库').count(), 0)

    def test_query_changes_timeline(self):
        from monitor.change_stream import query_changes, record_change
        record_change(self.config, 'audit', '重启实例', change_type='deploy')
        rows = query_changes(self.config, hours=1)
        self.assertTrue(any(r['title'] == '重启实例' for r in rows))
        self.assertIn('occurred_at', rows[0])


# ==========================================================================
# 7. DB: 反馈幂等 + 规则校准公式 (需 PostgreSQL)
# ==========================================================================
class FeedbackCalibrationDbTests(TestCase):

    @classmethod
    def setUpTestData(cls):
        from monitor.models import DatabaseConfig, Incident
        cls.config = DatabaseConfig.objects.create(
            name='ut-pg', db_type='postgresql', host='127.0.0.1',
            port=5432, username='u', password='p')
        cls.inc = Incident.objects.create(
            incident_id='INC-UT-P8-001', config=cls.config, db_type='postgresql',
            category='performance', title='单测事故', priority='P2',
            dedup_key='ut-p8', occurred_at=timezone.now())

    def setUp(self):
        from monitor import rule_calibrator
        rule_calibrator._invalidate_cache()   # 进程内缓存跨用例隔离

    def test_rca_feedback_upsert_idempotent(self):
        from monitor.models import RcaFeedback
        for verdict in ('correct', 'wrong'):   # 同人二次反馈覆盖而非新增
            RcaFeedback.objects.update_or_create(
                incident=self.inc, rule_id='R001', user='alice',
                defaults={'verdict': verdict, 'source': 'rule'})
        qs = RcaFeedback.objects.filter(incident=self.inc, rule_id='R001', user='alice')
        self.assertEqual(qs.count(), 1)
        self.assertEqual(qs.first().verdict, 'wrong')

    def test_plan_feedback_upsert_idempotent(self):
        from monitor.models import PlanFeedback
        for verdict in ('adopted', 'adopted_failed'):
            PlanFeedback.objects.update_or_create(
                incident=self.inc, scenario='standard', user='bob',
                defaults={'verdict': verdict})
        qs = PlanFeedback.objects.filter(incident=self.inc, scenario='standard')
        self.assertEqual(qs.count(), 1)
        self.assertEqual(qs.first().verdict, 'adopted_failed')

    @override_settings(RULE_CALIBRATION_MIN_SAMPLES=5)
    def test_recompute_rule_stats_formula(self):
        from monitor.models import RcaFeedback, RuleStat
        from monitor.rule_calibrator import get_calibrated_base, recompute_rule_stats
        # R001: 4 correct + 1 wrong + 1 partial → correct=4.5 wrong=1.5 total=6
        verdicts = ['correct'] * 4 + ['wrong'] + ['partial']
        for i, v in enumerate(verdicts):
            RcaFeedback.objects.create(incident=self.inc, rule_id='R001',
                                       user=f'user{i}', verdict=v, source='rule')
        # R002: 仅 2 票, 样本不足 → 维持默认 0.6
        for i in range(2):
            RcaFeedback.objects.create(incident=self.inc, rule_id='R002',
                                       user=f'user{i}', verdict='correct', source='rule')
        result = recompute_rule_stats()
        self.assertEqual(result['rules_updated'], 2)

        s1 = RuleStat.objects.get(rule_id='R001')
        self.assertEqual(s1.hit_count, 6)
        self.assertAlmostEqual(s1.accuracy, 0.75, places=3)          # 4.5 / 6
        self.assertAlmostEqual(s1.calibrated_base, 0.705, places=3)  # 0.3*0.6 + 0.7*0.75

        s2 = RuleStat.objects.get(rule_id='R002')
        self.assertEqual(s2.calibrated_base, 0.6)                    # 样本不足

        # 接入点读到校准值
        self.assertAlmostEqual(get_calibrated_base('R001'), 0.705, places=3)
        self.assertEqual(get_calibrated_base('R999'), 0.6)           # 未知规则默认

    @override_settings(RULE_CALIBRATION_MIN_SAMPLES=5)
    def test_calibrated_base_clamped(self):
        from monitor.models import RcaFeedback, RuleStat
        from monitor.rule_calibrator import recompute_rule_stats
        # 全错 → 0.3*0.6+0 = 0.18 → 限幅下限 0.2
        for i in range(6):
            RcaFeedback.objects.create(incident=self.inc, rule_id='R010',
                                       user=f'u{i}', verdict='wrong', source='rule')
        recompute_rule_stats()
        self.assertEqual(RuleStat.objects.get(rule_id='R010').calibrated_base, 0.2)


# ==========================================================================
# 7. Copilot & Quick Assessment 单元测试
# ==========================================================================
class CopilotTests(TestCase):

    def setUp(self):
        from monitor.models import DatabaseConfig, MonitorLog
        self.cfg = DatabaseConfig.objects.create(
            name='test-oracle-prod',
            db_type='oracle',
            host='10.0.0.10',
            port=1521,
            username='sys',
            password='enc:test_encrypted_pwd',  # noqa: secret
            is_active=True,
            cpu_cores=16,
        )
        MonitorLog.objects.create(
            config=self.cfg,
            status='UP',
            message='{"cpu_usage": 45, "conn_usage_pct": 30, "disk_usage_pct": 50}',
        )

    def test_copilot_fallback_chat(self):
        from monitor.copilot import run_copilot_chat
        res = run_copilot_chat(query="查看当前数据库的CPU指标与状态", config_id=self.cfg.id)
        self.assertIn('answer', res)
        self.assertEqual(res['source'], 'rule_fallback')
        self.assertTrue(res['context_used'])
        self.assertIn('指标', res['answer'])

    def test_quick_health_assessment(self):
        from monitor.copilot import generate_quick_health_assessment
        assessment = generate_quick_health_assessment(self.cfg.id)
        self.assertIn('overall_score', assessment)
        self.assertIn('grade', assessment)
        self.assertGreaterEqual(assessment['overall_score'], 80)
        self.assertEqual(len(assessment['dimensions']), 5)


# ==========================================================================
# 8. v2.0 RCA 3.0 & Playbook 单元测试
# ==========================================================================
class V2OperationsTests(TestCase):

    def setUp(self):
        from monitor.models import DatabaseConfig, Incident
        self.cfg = DatabaseConfig.objects.create(
            name='test-mysql-trade',
            db_type='mysql',
            host='127.0.0.1',
            port=3306,
            username='trade_user',
            password='enc:test_encrypted_pwd',  # noqa: secret
            is_active=True,
        )
        self.inc = Incident.objects.create(
            incident_id='INC-TEST-001',
            title='MySQL 交易库行锁阻塞与连接激增',
            priority='P1',
            category='performance',
            db_type='mysql',
            config=self.cfg,
            status='open',
            occurred_at=timezone.now(),
            rca_result={'root_causes': [{'domain': 'lock', 'name': '行锁等待', 'confidence': 0.92}]}
        )

    def test_rca3_causal_inference(self):
        from monitor.rca_engine_v3 import CausalInferenceEngine
        from monitor.models import IncidentCauseChain
        chains = CausalInferenceEngine.infer_and_build_cause_chain(self.inc)
        self.assertGreaterEqual(len(chains), 2)
        self.assertTrue(IncidentCauseChain.objects.filter(incident=self.inc).exists())
        self.assertEqual(chains[0]['node_type'], 'LOCK')

    def test_playbook_dryrun_and_execution(self):
        from monitor.playbook_engine_v2 import PlaybookExecutor
        # 1. 预演保护账号 -> 拒绝
        rej = PlaybookExecutor.evaluate_dryrun(
            'KILL_ROOT_BLOCKER', self.cfg, {'username': 'root', 'session_id': '10'}
        )
        self.assertEqual(rej['status'], 'REJECTED')

        # 2. 预演普通账号 -> 通过
        pass_res = PlaybookExecutor.evaluate_dryrun(
            'KILL_ROOT_BLOCKER', self.cfg, {'username': 'trade_user', 'session_id': '1845'}
        )
        self.assertEqual(pass_res['status'], 'PASSED')

        # 3. 正式安全执行
        exec_res = PlaybookExecutor.execute_playbook(
            'KILL_ROOT_BLOCKER', self.cfg, 'admin', {'username': 'trade_user', 'session_id': '1845'}, incident=self.inc
        )
        self.assertEqual(exec_res['status'], 'success')
        self.assertIn('RUN-', exec_res['run_id'])
