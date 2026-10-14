# -*- coding: utf-8 -*-
"""V3.0 OAM 验收测试（依据 `V3.0_DBAGENT_REDESIGN.md` §8 的 14 条验收项）。

本项目的规矩：新增缺陷不得以 `expectedFailure` 换绿灯；本文件**全部**打
`@tag('unit')`，进 `scripts/validate.sh unit` 与 CI。

## 两条铁律（AGENTS.md）

**权限与数据范围是两道独立的闸。** 本文件刻意把两类用例分开：

- `Scope*`：给足权限、限定数据范围 → 验证"范围闸"；
- `PermissionGate*`：零权限、数据范围包含目标 → 验证"权限闸"。

合在一起测会被 403 短路，等于没测到范围。这条已复发五次
（BUG-103 → REV-01 → R25-01 → RT-01 → V2.6 W1 `3b2884c`）。

**零数据实例必须返回 `evidence_insufficient`。** 这条专门防 R25-02
（编造 ASH 会话、编造表空间 90.3%）与 REV-10（无条件追加 0.88 置信度结论）。
"""
import json
from datetime import timedelta

from django.test import Client, TestCase, tag
from django.utils import timezone

from monitor.auth import Perm, RoleCode
from monitor.models import (
    DatabaseConfig, KbDocument, OamCase, OamSnapshotBlob, OamTrajectory,
)
from monitor.oam import labels as oam_labels
from monitor.oam.env import OamSnapshot, ReplayEnv, SnapshotRangeError, ToolProxy
from monitor.oam.perception import (
    PlanParseError, analyze, extract, is_supported,
)
from monitor.oam.perception.kpi_stl_gesd import gesd_critical_value_analytic
from monitor.oam.reward import WEIGHTS, compute, r_format, r_tool
from monitor.oam.types import Action, Observation, Step, Trajectory
from monitor.oam.verifier import (
    STATUS_EVIDENCE_INSUFFICIENT, hallucination_rate, macro_success_rate, verify,
)
from monitor.tests_bugfix import login, make_db, make_user


# =============================================================================
# 测试替身：脚本化策略（确定性，不依赖 LLM，也不依赖外部服务）
# =============================================================================
class ScriptedPolicy:
    """按预设脚本逐轮吐输出的策略。

    基准的可复现性验收项（§8 第一条）要求"同一 case_id + policy_version
    重跑结果一致"，所以测试里**不用真实 LLM** —— 模型温度、后端可用性
    都不该影响一个可复现性断言。
    """

    backend = 'prompted'
    version = 'test-v1'

    def __init__(self, outputs, db_type='mysql'):
        self.outputs = list(outputs)
        self.db_type = db_type
        self.calls = 0

    def step(self, state, turn=1):
        idx = min(self.calls, len(self.outputs) - 1)
        self.calls += 1
        return self.outputs[idx]


def _script(tool_name, params=None):
    return json.dumps({'tool': tool_name, 'params': params or {}},
                      ensure_ascii=False)


def _answer(root_cause, fix='', refs=None, status=''):
    payload = {'root_cause': root_cause, 'evidence_refs': refs or []}
    if fix:
        payload['fix'] = fix
    if status:
        payload['status'] = status
    return json.dumps(payload, ensure_ascii=False)


ANSWER_INSUFFICIENT = json.dumps(
    {'status': STATUS_EVIDENCE_INSUFFICIENT, 'reason': '快照没有可支撑判断的信号'},
    ensure_ascii=False)


def _make_case(db_type='mysql', gt='MISSING_INDEX', split='test', **kw):
    kw.setdefault('symptom', '某表查询耗时突增')
    kw.setdefault('category', 'tuning')
    kw.setdefault('difficulty', 'easy')
    return OamCase(case_id=kw.pop('case_id', 'C1'), db_type=db_type,
                   gt_root_cause=gt, split=split, **kw)


def _window():
    end = timezone.now()
    return end - timedelta(hours=2), end


def _attach_snapshot(case, series=None, plans=None, session=None):
    start, end = _window()
    series = series if series is not None else {
        'conn_usage_pct': [[(start + timedelta(seconds=60 * i)).isoformat(),
                            20 + i % 5] for i in range(60)]}
    blob = OamSnapshotBlob.objects.create(
        case=case, window_start=start, window_end=end,
        kpi_series=series,          # 时间戳已是 ISO 字符串（JSONField 存不下 datetime）
        session_samples=session or [], plans=plans or [],
        config_meta={'parameters': {'max_connections': '150'},
                     'os_metrics': {'cpu': [{'usage': 30}]}},
        schema_meta={'tables': {'orders': {'rows': 12000000}},
                     'statistics': {'orders': {'last_analyzed': '2024-01-01'}}})
    return blob


# =============================================================================
# 标签空间：§9.1 的前置条件，也是 §8 多条验收项的地基
# =============================================================================
@tag('unit')
class RootCauseLabelSpaceTests(TestCase):
    """§9.1：标签空间定不下来，验证器就无从判定，整条链都是空转。"""

    def test_label_is_enum_not_free_text(self):
        self.assertFalse(oam_labels.is_valid_label('负载有点高', 'mysql'),
                         '自由文本不得被当作合法根因（根因匹配是精确相等，不是语义相似）')

    def test_oracle_only_label_rejected_on_mysql(self):
        """REV-10 的结构性防线：Oracle 专有标签不得用于 MySQL 事故。"""
        self.assertGreater(len(oam_labels.oracle_only_labels()), 0,
                           'Oracle 专有标签集合不应为空，否则本用例失去意义')
        for label in oam_labels.oracle_only_labels():
            self.assertFalse(oam_labels.is_valid_label(label, 'mysql'),
                             f'{label} 是 Oracle 专有，不得对 MySQL 有效')

    def test_idle_in_txn_not_valid_on_oracle(self):
        """反向：PG/MySQL 系的 idle in transaction 在 Oracle 不成立。"""
        self.assertTrue(oam_labels.is_valid_label('IDLE_IN_TXN', 'pgsql'))
        self.assertFalse(oam_labels.is_valid_label('IDLE_IN_TXN', 'oracle'))

    def test_assert_label_raises_for_cross_dialect(self):
        from django.core.exceptions import ValidationError
        case = _make_case(db_type='mysql', gt='PGA_EXHAUSTED')
        with self.assertRaises(ValidationError):
            case.full_clean(exclude=['case_id'])

    def test_every_migrated_rule_has_a_label(self):
        """RULES_V2 的 39 条规则迁移成标签时不能丢条。"""
        from monitor.rca_engine_v2 import RULES_V2
        migrated = set(oam_labels.migrated_rule_ids())
        missing = [r['id'] for r in RULES_V2 if r['id'] not in migrated]
        self.assertEqual(missing, [],
                         f'这些专家规则迁移时没有对应标签，会在 KB 迁移中丢知识: {missing}')


# =============================================================================
# 验证器：§8 的第 13 条与第 14 条
# =============================================================================
@tag('unit')
class VerifierCriterionTests(TestCase):
    """§8：可执行修复的判据不许自行加严；证据不足是合法终止。"""

    def test_latency_equal_to_baseline_passes(self):
        """§8 明列：修复后时延与基线持平且结果一致 ⇒ 判通过。

        论文判据是 `Lat(m̂;Σ) ≤ t_ref` 且 `Out(m̂) = Out(ref)`，
        **不是**"快多少"。这里若写成 `<` 或加了提升幅度门槛就会红。
        """
        case = _make_case(fix_executable=True,
                          fix_spec={'params': 'CREATE INDEX idx ON t(c)',
                                    't_ref': 100, 'out_ref': 'R1',
                                    'measured_latency': 100, 'measured_output': 'R1'})
        v = verify(case, {'root_cause': 'MISSING_INDEX',
                          'fix': 'CREATE INDEX idx ON t(c)'})
        self.assertTrue(v.passed, f'时延持平且结果一致应当判通过，实际: {v.reason}')

    def test_slower_or_different_result_fails(self):
        base = {'params': 'CREATE INDEX idx ON t(c)', 't_ref': 100, 'out_ref': 'R1'}
        slower = _make_case(fix_executable=True, fix_spec=dict(
            base, measured_latency=101, measured_output='R1'))
        self.assertFalse(verify(slower, {'root_cause': 'MISSING_INDEX',
                                         'fix': 'CREATE INDEX idx ON t(c)'}).passed)
        changed = _make_case(fix_executable=True, fix_spec=dict(
            base, measured_latency=10, measured_output='R9'))
        self.assertFalse(verify(changed, {'root_cause': 'MISSING_INDEX',
                                          'fix': 'CREATE INDEX idx ON t(c)'}).passed)

    def test_evidence_insufficient_is_legal_stop(self):
        """§8：证据与验证器检查冲突 ⇒ 输出 evidence_insufficient 且不计入幻觉。"""
        case = _make_case()
        v = verify(case, {'status': STATUS_EVIDENCE_INSUFFICIENT, 'reason': '缺信号'})

        class T:
            escalated, hallucinated = True, None
        self.assertFalse(v.passed, '合法终止不计成功')
        self.assertTrue(v.escalated)
        self.assertEqual(hallucination_rate([T()]), 0.0,
                         '合法终止不得计入幻觉率，否则会逼策略硬编根因')

    def test_unparseable_report_scores_zero(self):
        """论文：确定性抽取器解析失败判 0。"""
        case = _make_case()
        for bad in ('not json', {'fix': 'x'}, None, 123):
            self.assertEqual(verify(case, bad).score, 0.0)

    def test_root_cause_match_is_exact_not_semantic(self):
        """§0.1 三：根因匹配是轻度归一化后的精确字符串相等，不是语义相似度。"""
        case = _make_case(gt='MISSING_INDEX')
        self.assertFalse(verify(case, {'root_cause': 'SLOW_QUERY_SURGE',
                                       'fix': 'x'}).passed)

    def test_consultation_requires_exact_evidence_set(self):
        """§1.6⑤：咨询类引用的文档集合与规格完全相等（不是包含、不是相似）。"""
        case = _make_case(category='consultation', gt='CONFIG_DRIFT',
                          required_evidence=['kb:1', 'kb:2'])
        self.assertTrue(verify(case, {'root_cause': 'CONFIG_DRIFT',
                                      'evidence_refs': ['kb:2', 'kb:1']}).passed)
        self.assertFalse(verify(case, {'root_cause': 'CONFIG_DRIFT',
                                       'evidence_refs': ['kb:1']}).passed,
                         '缺引用应判失败')
        self.assertFalse(verify(case, {'root_cause': 'CONFIG_DRIFT',
                                       'evidence_refs': ['kb:1', 'kb:2', 'kb:3']}).passed,
                         '多引用也应判失败')

    def test_macro_average_is_not_case_weighted(self):
        """§8：断言汇总口径是宏平均，不是用例数加权。"""
        class V:
            def __init__(self, ok):
                self.passed = ok
        big = [V(True)] * 99 + [V(False)]
        small = [V(False)]
        m = macro_success_rate({'tuning': big, 'overload': small})
        self.assertAlmostEqual(m['overall'], 0.495, places=6,
                               msg='宏平均应为 (0.99+0)/2；加权会得到 ~0.98')


# =============================================================================
# 快照窗口约束：§8 第 9 条
# =============================================================================
@tag('unit')
class SnapshotWindowTests(TestCase):
    """§8：>500 点序列入库被拒。防直接继承论文的长序列 F1 崩塌。"""

    def test_series_over_limit_is_rejected(self):
        start, end = _window()
        long_series = [[start + timedelta(seconds=i), i % 50] for i in range(600)]
        snap = OamSnapshot(case_id='X', db_type='mysql',
                           kpi_series={'conn': long_series}, window=(start, end))
        with self.assertRaises(SnapshotRangeError) as ctx:
            snap.validate(strict=True)
        self.assertIn('500', str(ctx.exception))

    def test_window_required_and_ordered(self):
        snap = OamSnapshot(case_id='X', db_type='mysql')
        problems = snap.validate(strict=False)
        self.assertTrue(any('window 必填' in p for p in problems),
                        '窗口必须显式给出，否则长序列会原样继承 F1 崩塌')
        snap2 = OamSnapshot(case_id='X', db_type='mysql',
                            window=(_window()[1], _window()[0]))
        self.assertTrue(any('必须晚于起点' in p for p in snap2.validate(strict=False)))

    def test_snapshot_endpoint_rejects_oversize_series(self):
        case = _make_case(case_id='C-SNAP-1')
        case.save()
        start, end = _window()
        payload = {'window': [start.isoformat(), end.isoformat()],
                   'kpi_series': {'conn': [[(start + timedelta(seconds=i)).isoformat(),
                                            i % 50] for i in range(600)]}}
        from monitor.tests_bugfix import make_role
        from monitor.models import Role, RolePermission, UserProfile
        user = make_user('oam-snap', RoleCode.DBA, [Perm.ALERT_CONFIG_MANAGE])
        c = Client()
        login(c, user)
        r = c.post(f'/api/v3/oam/cases/{case.case_id}/snapshot/',
                   data=json.dumps(payload), content_type='application/json')
        self.assertEqual(r.status_code, 400, '超限快照必须明确拒绝，不能静默截断')
        self.assertEqual(r.json()['code'], 'SNAPSHOT_RANGE')


# =============================================================================
# 工具代理：§8 第 5 条 + 方言隔离
# =============================================================================
@tag('unit')
class ToolProxyTests(TestCase):
    """§8：变更类工具一律不可由策略直接调用（防 REV-02 谎报执行成功）。"""

    def setUp(self):
        start, end = _window()
        self.snap = OamSnapshot(case_id='C1', db_type='mysql', window=(start, end))
        self.env = ReplayEnv(self.snap)

    def test_mutating_tool_forbidden(self):
        """§8：策略请求 mutating 工具 ⇒ MUTATION_FORBIDDEN，且与参数是否合法无关。"""
        cases = [('fix.kill_session', {'sid': 1}),
                 ('fix.alter_parameter', {'name': 'work_mem', 'value': '64MB'}),
                 ('fix.create_index', {'table': 'orders', 'columns': 'c'}),
                 ('fix.extend_space', {'tablespace': 'ts', 'size': '1G'})]
        for tool, params in cases:
            r = self.env.call(tool, params)
            self.assertFalse(r.ok, f'{tool} 是变更类，策略不得直接调用')
            self.assertEqual(r.error_code, 'MUTATION_FORBIDDEN')
        # 参数非法时也必须仍报 MUTATION_FORBIDDEN：最强的信号不能被稀释成 BAD_PARAMS
        r_bad = self.env.call('fix.kill_session', {'evil': 1})
        self.assertEqual(r_bad.error_code, 'MUTATION_FORBIDDEN',
                         '变更拦截应先于参数校验，保证该验收项无条件成立')

    def test_mutating_tools_absent_from_catalog(self):
        """变更类工具不应出现在给策略看的目录里。"""
        names = [t['name'] for t in self.env.catalog()]
        self.assertFalse([n for n in names if n.startswith('fix.')],
                         '变更类工具不得进策略的动作空间')

    def test_unknown_tool_is_deterministic_error(self):
        r = self.env.call('nope.nope', {})
        self.assertEqual(r.error_code, 'UNKNOWN_TOOL')

    def test_bad_params_is_rejected(self):
        r = self.env.call('db.top_sql', {'limit': 9999})
        self.assertEqual(r.error_code, 'BAD_PARAMS')
        r2 = self.env.call('db.top_sql', {'sql': 'DROP TABLE x'})
        self.assertEqual(r2.error_code, 'BAD_PARAMS',
                         '契约外参数必须拒绝（禁止自由 SQL）')

    def test_missing_signal_is_no_data_not_failure(self):
        """快照里没有的信号属"证据不足"，要能区分于"查不了"。"""
        r = self.env.call('db.slow_query', {})
        self.assertFalse(r.ok)
        self.assertEqual(r.error_code, 'NO_DATA')

    def test_dialect_mismatch_rejected(self):
        """方言隔离闸。

        当前工具目录对 6 个方言**全量生成**规格，所以这条分支在真实目录里
        走不到；用 mock 构造"该工具只存在于别的方言"的场景来验证闸门本身
        有效。一旦将来目录里出现方言专属工具，这条防线就是 REV-10 的兜底。
        """
        from unittest import mock
        from monitor.oam.tool_catalog import ToolSpec
        oracle_only = ToolSpec(name='db.session_profile', kind='db',
                               db_type='oracle', params_schema={},
                               enriched_description='oracle 专属')
        with mock.patch('monitor.oam.env.tool_proxy.get_tool',
                        return_value=oracle_only):
            r = self.env.call('db.session_profile', {})
        self.assertEqual(r.error_code, 'DIALECT_MISMATCH')
        self.assertEqual(r.error_params['tool_db_type'], 'oracle')

    def test_tool_description_is_dialect_specific(self):
        """§9.2 / REV-10：同一条工具的描述必须按方言不同。

        这是"把 Oracle 的东西讲给 MySQL 用"的第一道静态防线。
        """
        from monitor.oam.tool_catalog import get_tool
        mysql = get_tool('db.session_profile', 'mysql').enriched_description
        oracle = get_tool('db.session_profile', 'oracle').enriched_description
        self.assertNotEqual(mysql, oracle, '描述未按方言分化，等于没做语义增强')
        self.assertIn('performance_schema', mysql)
        self.assertIn('V$SESSION', oracle)
        for oracle_only_term in ('enq: TX', 'V$SESSION', 'V$SESSIONS_ACTIVITY'):
            self.assertNotIn(oracle_only_term, mysql,
                             f'MySQL 的描述里绝不能出现 Oracle 专有词 {oracle_only_term}，'
                             f'哪怕是在否定句里 —— 词进了上下文就有被复述进诊断报告的风险')

    def test_replayable_false_is_rejected_in_replay(self):
        proxy = ToolProxy(ctx=self.env, mode='replay', handlers={})
        r = proxy.call('db.top_sql', {})
        self.assertEqual(r.error_code, 'TOOL_FAILED',
                         '未接入实现要给出确定性错误，不静默')


# =============================================================================
# 零数据实例：§8 第 4 条（防 R25-02 / REV-10）
# =============================================================================
@tag('unit')
class ZeroDataInstanceTests(TestCase):
    """§8：空快照输入 ⇒ 断言不出根因。"""

    def test_empty_snapshot_gives_no_data_not_zero(self):
        case = _make_case(case_id='C-ZERO')
        case.save()
        snap = OamSnapshot.from_case(case)
        self.assertEqual(snap.kpi_series, {})
        self.assertIsNone(snap.metric_value('conn_usage_pct'),
                          '缺失指标必须返回 None，不能编造 0')

    def test_policy_that_finds_nothing_must_escalate(self):
        """策略给不出根因时，正确行为是升级，不是硬凑一个。"""
        from monitor.oam.policy import diagnose
        case = _make_case(case_id='C-ZERO2')
        case.save()
        env = ReplayEnv(OamSnapshot.from_case(case))
        policy = ScriptedPolicy([_script('db.top_sql', {'limit': 5}),
                                 ANSWER_INSUFFICIENT], db_type='mysql')
        traj = diagnose(case, policy, env, max_turns=3)
        self.assertTrue(traj.escalated, '应当转人工')
        self.assertEqual(traj.report['status'], STATUS_EVIDENCE_INSUFFICIENT)
        self.assertFalse(traj.report.get('root_cause'),
                         '证据不足时不得给出根因')

    def test_forced_root_cause_without_evidence_fails_audit(self):
        """硬凑根因且无任何证据引用 ⇒ 审计必须标记。"""
        from monitor.oam.audit import audit
        traj = Trajectory(case_id='C', db_type='mysql', steps=[])
        traj.report = {'root_cause': 'MISSING_INDEX', 'evidence_refs': [],
                       'fix': 'CREATE INDEX i ON t(c)'}
        result = audit(traj, traj.report)
        self.assertFalse(result.passed)
        self.assertTrue(result.route_to_human)


# =============================================================================
# 覆盖度：§8 第 10 条
# =============================================================================
@tag('unit')
class CoverageReportTests(TestCase):
    """§8：评测输出必须含 coverage_report；缺口不许留空。"""

    def test_coverage_report_has_missing_root_causes(self):
        case = _make_case(case_id='C-COV', gt='MISSING_INDEX')
        case.save()
        _attach_snapshot(case)
        env = ReplayEnv(OamSnapshot.from_case(case))
        report = env.coverage_report(covered_root_causes={'MISSING_INDEX'})
        self.assertIn('root_causes_missing', report)
        self.assertNotIn('MISSING_INDEX', report['root_causes_missing'])
        self.assertIn('LOCK_WAIT', report['root_causes_missing'],
                      '未覆盖的根因必须显式列出，否则会被当成泛化能力')
        self.assertIn('capability_boundary', report)

    def test_bench_report_always_carries_coverage(self):
        from monitor.oam.bench.runner import run_bench
        from monitor.models import OamBenchRun
        rep = run_bench([], split='test')
        self.assertIn('coverage', rep, '缺 coverage 视为接口未实现')
        stored = OamBenchRun.objects.get(run_id=rep['run_id'])
        self.assertIn('coverage', stored.to_dict_report())

    def test_report_endpoint_refuses_missing_coverage(self):
        from monitor.models import OamBenchRun
        run = OamBenchRun.objects.create(run_id='BR-NOCOV', split='test',
                                         coverage={}, status='done')
        user = make_user('oam-cov', RoleCode.DBA, [Perm.METRICS_VIEW])
        c = Client()
        login(c, user)
        r = c.get('/api/v3/oam/bench/report/BR-NOCOV/')
        self.assertEqual(r.status_code, 500)
        self.assertEqual(r.json()['code'], 'COVERAGE_MISSING',
                         '缺覆盖度时宁可报错，也不给"看起来完整"的响应')
        self.assertEqual(run.run_id, 'BR-NOCOV')


# =============================================================================
# 评测/训练实例隔离：§8 第 12 条
# =============================================================================
@tag('unit')
class SplitInstanceDisjointTests(TestCase):
    """§8：测试集 db_config_id 与训练集不相交（防训练评测同源的假绿）。"""

    def test_overlapping_instance_is_flagged(self):
        from monitor.oam.bench.runner import assert_split_instance_disjoint
        cfg = make_db('oam-shared', port=3401)
        a = OamCase.objects.create(case_id='TR1', category='tuning', difficulty='easy',
                                   db_type='mysql', symptom='s', gt_root_cause='LOCK_WAIT',
                                   source_config=cfg, split='train')
        b = OamCase.objects.create(case_id='TE1', category='tuning', difficulty='easy',
                                   db_type='mysql', symptom='s', gt_root_cause='LOCK_WAIT',
                                   source_config=cfg, split='test')
        ok, detail = assert_split_instance_disjoint([a], [b])
        self.assertFalse(ok, '同一实例同时进训练集与测试集必须被判失败')
        self.assertEqual(detail['overlap_config_ids'], [cfg.id])

    def test_disjoint_instances_pass(self):
        from monitor.oam.bench.runner import assert_split_instance_disjoint
        c1, c2 = make_db('oam-tr', port=3402), make_db('oam-te', port=3403)
        a = OamCase.objects.create(case_id='TR2', category='tuning', difficulty='easy',
                                   db_type='mysql', symptom='s', gt_root_cause='LOCK_WAIT',
                                   source_config=c1, split='train')
        b = OamCase.objects.create(case_id='TE2', category='tuning', difficulty='easy',
                                   db_type='mysql', symptom='s', gt_root_cause='LOCK_WAIT',
                                   source_config=c2, split='test')
        ok, _ = assert_split_instance_disjoint([a], [b])
        self.assertTrue(ok)


# =============================================================================
# 标注质量：§8 第 2 条
# =============================================================================
@tag('unit')
class BenchmarkCaseProvenanceTests(TestCase):
    """§8：基准用例来自真实工单且有专家标注，防用造出来的用例自证。"""

    def test_invalid_label_rejected_by_api(self):
        user = make_user('oam-prov', RoleCode.DBA, [Perm.ALERT_CONFIG_MANAGE])
        c = Client()
        login(c, user)
        payload = {'case_id': 'C-PROV-1', 'db_type': 'mysql',
                   'gt_root_cause': '我也不知道是啥', 'symptom': '慢'}
        r = c.post('/api/v3/oam/cases/', data=json.dumps(payload),
                   content_type='application/json')
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json()['code'], 'INVALID_LABEL')

    def test_label_space_asserted_on_model(self):
        from django.core.exceptions import ValidationError
        case = _make_case(case_id='C-PROV-2', db_type='oracle',
                          gt='IDLE_IN_TXN')      # PG 系概念，用在 Oracle 上
        with self.assertRaises(ValidationError):
            case.full_clean(exclude=['case_id'])


# =============================================================================
# 奖励四分量
# =============================================================================
@tag('unit')
class RewardTests(TestCase):
    """§4.6：权重照论文 (0.2, 0.1, 0.5, 0.2)，Σ=1；可执行判据不得加严。"""

    def test_weights_sum_to_one(self):
        self.assertAlmostEqual(sum(WEIGHTS.values()), 1.0, places=9)
        self.assertEqual(WEIGHTS, {'format': 0.2, 'tool': 0.1,
                                   'answer': 0.5, 'stepwise': 0.2})

    def test_r_format_is_k_over_t(self):
        traj = Trajectory(case_id='C', db_type='mysql')
        traj.steps = [Step(turn=1, format_ok=True,
                           action=Action(tool='db.top_sql')),
                      Step(turn=2, format_ok=False,
                           action=Action(tool='db.top_sql'))]
        self.assertAlmostEqual(r_format(traj), 0.5)

    def test_r_tool_punishes_unplanned_execution(self):
        """r_tool 同时惩罚空想式调用与未经规划的执行。"""
        traj = Trajectory(case_id='C', db_type='mysql')
        traj.steps = [Step(turn=1, plan_tool='', action=Action(tool='db.top_sql'))]
        self.assertLess(r_tool(traj), 1.0)

    def test_r_tool_rewards_plan_execute_alignment(self):
        traj = Trajectory(case_id='C', db_type='mysql')
        traj.steps = [Step(turn=1, plan_tool='db.top_sql',
                           plan_params={'limit': 5},
                           action=Action(tool='db.top_sql', params={'limit': 5}))]
        self.assertAlmostEqual(r_tool(traj), 1.0)

    def test_step_reward_zero_on_over_probe(self):
        """§5.2：单轮工具调用数超 L_tool ⇒ r_stepwise 置零（防过度探查）。"""
        from monitor.oam.reward.step_bayes import step_reward
        before = {'a': 0.5, 'b': 0.3, 'c': 0.2}
        after = {'a': 0.9, 'b': 0.07, 'c': 0.03}
        self.assertEqual(step_reward(before, after, tool_calls_this_turn=99), 0.0)
        self.assertGreater(step_reward(before, after, tool_calls_this_turn=1), 0.0)

    def test_total_is_bounded(self):
        case = _make_case(case_id='C-RW')
        case.save()
        traj = Trajectory(case_id='C-RW', db_type='mysql')
        traj.report = {'root_cause': 'MISSING_INDEX', 'fix': 'x'}
        traj.steps = [Step(turn=1, format_ok=True,
                           action=Action(tool='db.top_sql'))]
        total, parts = compute(traj, case)
        self.assertGreaterEqual(total, 0.0)
        self.assertLessEqual(total, 1.0)
        self.assertIn('format', parts)


# =============================================================================
# L2 感知层
# =============================================================================
@tag('unit')
class PerceptionTests(TestCase):
    def test_unsupported_dialect_plan_raises_explicitly(self):
        """§9.2：Oracle/达梦/GBase/TDSQL 抛"该方言暂不支持计划分析"，不降级猜测。"""
        for db_type in ('oracle', 'dm', 'gbase', 'tdsql'):
            self.assertFalse(is_supported(db_type))
            with self.assertRaises(PlanParseError) as ctx:
                extract('Seq Scan on t  (cost=0.00..1.00)', db_type)
            self.assertIn('暂不支持计划分析', str(ctx.exception))

    def test_mysql_plan_parsed_and_analyzed(self):
        """大表 + **带谓词** + 低选择率 + 全表扫描 ⇒ 触发 MISSING_INDEX。

        三个条件缺一不可，这是实现的刻意收紧：
        MySQL 的 `filtered` 是"过滤后存活百分比"，低选择率对应很小的值；
        而**没有过滤条件的全表扫描不算缺索引** —— 那种情况本来就得全读，
        把它报成"缺索引"是误报。
        """
        plan = json.dumps({
            'query_block': {'table': {
                'table_name': 'orders', 'access_type': 'ALL',
                'attached_condition': '((status = 3))',
                'rows_examined_per_scan': 12000000,
                'rows_produced_per_join': 1200,
                'filtered': '0.01',
                'cost_info': {'read_cost': '1000.00'}}}})
        root = extract(plan, 'mysql')
        alerts = analyze(root, 'mysql')
        self.assertTrue(alerts, '大表低选择率带谓词全表扫描应触发启发式')
        codes = {a.code for a in alerts}
        self.assertIn('MISSING_INDEX', codes)
        for a in alerts:
            self.assertTrue(a.evidence, 'PlanAlert 必须带支撑证据')

    def test_scan_without_predicate_does_not_fire(self):
        """反向①：无谓词的全表扫描不报"缺索引"（本来就得全读）。"""
        plan = json.dumps({'query_block': {'table': {
            'table_name': 'orders', 'access_type': 'ALL',
            'rows_examined_per_scan': 12000000,
            'rows_produced_per_join': 11900000, 'filtered': '100.00',
            'cost_info': {'read_cost': '1000.00'}}}})
        self.assertNotIn('MISSING_INDEX',
                         {a.code for a in analyze(extract(plan, 'mysql'), 'mysql')})

    def test_high_selectivity_scan_does_not_fire(self):
        """反向②：有谓词但高选择率 ⇒ 也不该报缺索引。防误报。"""
        plan = json.dumps({'query_block': {'table': {
            'table_name': 'orders', 'access_type': 'ALL',
            'attached_condition': '((status = 3))',
            'rows_examined_per_scan': 12000000,
            'rows_produced_per_join': 11900000, 'filtered': '100.00',
            'cost_info': {'read_cost': '1000.00'}}}})
        self.assertNotIn('MISSING_INDEX',
                         {a.code for a in analyze(extract(plan, 'mysql'), 'mysql')},
                         '高选择率扫描不该被报成缺索引')

    def test_kpi_no_anomaly_returns_empty(self):
        """§4.2 硬约束：无异常就返回空列表，不得为了"有话说"降阈值。"""
        from monitor.oam.perception import detect
        start, end = _window()
        flat = [[start + timedelta(seconds=60 * i), 50.0] for i in range(60)]
        self.assertEqual(detect(flat, 'flat_metric'), [])

    def test_kpi_anomaly_carries_evidence(self):
        from monitor.oam.perception import detect
        start, end = _window()
        series = [[start + timedelta(seconds=60 * i), 50.0] for i in range(60)]
        series[30] = [series[30][0], 900.0]
        obs = detect(series, 'cpu')
        self.assertTrue(obs, '明显尖峰应被检出')
        self.assertTrue(obs[0].evidence, '观测必须带证据')
        self.assertIn('refs', obs[0].evidence)

    def test_kpi_false_positive_rate_is_bounded(self):
        """纯噪声序列上的误报必须受控。

        这条是 §1.4「证据链看着满、其实没有信息」的直接防线：MMA 若在无异常的
        窗口上凭空产出带完整 evidence 的观测，L5 审计无从分辨真假。

        历史缺陷（已修）：① MAD 漏乘一致性常数 1.4826，z 被放大 1.485 倍；
        ② 临界值按"真实 σ"标定而非本实现实际用的样本 median/MAD 标准化量；
        ③ 去趋势引入的局部相关性未被计入标定。三者叠加时 n=60 纯噪声的误报率
        约 18/40，修复后应降到个位数。
        """
        import numpy as np
        from monitor.oam.perception import detect
        rng = np.random.default_rng(20240514)
        base, end = _window()
        false_positive_windows = 0
        trials = 12
        for _ in range(trials):
            vals = rng.normal(50, 8, 60)
            series = [[base + timedelta(seconds=60 * i), float(v)]
                      for i, v in enumerate(vals)]
            if detect(series, 'noise'):
                false_positive_windows += 1
        self.assertLessEqual(false_positive_windows, 3,
                             f'纯噪声 12 个窗口中报了 {false_positive_windows} 个异常，'
                             f'远超名义 α=0.05；KPI 检测被过标定或漏乘 MAD 一致性常数')

    def test_kpi_rejects_oversized_series(self):
        """§8：>500 点序列被拒。纵深防御 —— 即使绕过快照层也不静默分析长序列。

        依据：论文 Table 3a 实测 KPI 检测 F1 在 500 点时只剩 3.2%，
        照单全收等于把"分析不出东西"说成"没发现异常"。
        """
        from monitor.oam.perception import detect
        start, end = _window()
        for n in (501, 800):
            vals = [50.0 + (i % 9) for i in range(n)]
            vals[n // 2] = 500.0            # 明显尖峰，拒收时应一条都检不出
            series = [[start + timedelta(seconds=60 * i), v]
                      for i, v in enumerate(vals)]
            self.assertEqual(detect(series, 'long'), [],
                             f'{n} 点超出窗口上限，应当拒收而不是给一个不可信的结论')

    def test_kpi_critical_value_is_deterministic(self):
        """临界值按固定种子标定：同一 (n, α) 必须永远得到同一个值。

        这是 §8「同用例同策略结果一致」的前提 —— 不可复现的阈值会让基准分数
        带着随机噪声波动。
        """
        from monitor.oam.perception.kpi_stl_gesd import gesd_critical_value
        first = gesd_critical_value(120, 1, 0.05)
        self.assertEqual(gesd_critical_value(120, 1, 0.05), first)
        self.assertGreater(first, gesd_critical_value_analytic(120, 1),
                           '流水线标定的临界值应不小于解析解（解析解偏松）')

    def test_norm_ppf_body_accuracy(self):
        from monitor.oam.perception.kpi_stl_gesd import norm_ppf
        self.assertAlmostEqual(norm_ppf(0.975), 1.959963984540054, places=12)
        self.assertAlmostEqual(norm_ppf(0.995), 2.5758293035489004, places=12)


# =============================================================================
# L3 知识库
# =============================================================================
@tag('unit')
class KnowledgeBaseTests(TestCase):
    def test_retrieval_miss_is_recorded(self):
        """§7 P3：检索未命中必须写 `OamRetrievalMiss` 留痕。

        论文部署失败原因里"检索未命中"占 12%，且往往早于"KB 过时"暴露。
        不记就无从改进索引与类目划分 —— 这是 KB 新鲜度机制的早期信号。
        """
        from monitor.models import OamRetrievalMiss
        from monitor.oam.env import OamSnapshot, ReplayEnv
        from monitor.oam.kb import KbIndex, KbRecord

        idx = KbIndex()
        idx.add(KbRecord(record_id='kb:m1', symptom='排序落盘导致查询变慢',
                         db_types=['mysql'], candidate_causes=['MISSING_INDEX']))
        start, end = _window()
        env = ReplayEnv(OamSnapshot(case_id='C-MISS', db_type='mysql',
                                    window=(start, end)),
                        kb_index=idx, trajectory_id='TRAJ-MISS')
        OamRetrievalMiss.objects.all().delete()

        result = env.call('kb.search',
                          {'query': '完全不相干的另一个问题关于网络配置'})
        self.assertTrue(result.ok, '检索本身执行成功，只是没命中')
        payload = getattr(result.data, 'missed', None)
        self.assertTrue(payload, '低相似度应被标记为未命中')

        rows = list(OamRetrievalMiss.objects.all())
        self.assertEqual(len(rows), 1, '未命中必须留痕，否则无法改进索引与类目划分')
        row = rows[0]
        self.assertEqual(row.trajectory_id, 'TRAJ-MISS')
        self.assertEqual(row.db_type, 'mysql')
        self.assertIn('网络配置', row.query_text)

    def test_retrieval_hit_leaves_no_miss_record(self):
        """命中时**不得**写未命中留痕 —— 否则看板会被噪声淹没。"""
        from monitor.models import OamRetrievalMiss
        from monitor.oam.env import OamSnapshot, ReplayEnv
        from monitor.oam.kb import KbIndex, KbRecord

        idx = KbIndex()
        idx.add(KbRecord(record_id='kb:m1', symptom='排序落盘导致查询变慢',
                         db_types=['mysql'], candidate_causes=['MISSING_INDEX']))
        start, end = _window()
        env = ReplayEnv(OamSnapshot(case_id='C-HIT', db_type='mysql',
                                    window=(start, end)), kb_index=idx)
        OamRetrievalMiss.objects.all().delete()
        env.call('kb.search', {'query': '排序落盘导致查询变慢'})
        self.assertEqual(OamRetrievalMiss.objects.count(), 0)

    def test_kb_record_rejects_cross_dialect_cause(self):
        """成因必须落在该方言的标签空间内，否则会把 Oracle 方案推给 MySQL。"""
        from monitor.oam.kb import extract as kb_extract
        doc = {'doc_id': 'D1', 'category': 'fault_solution', 'db_types': ['mysql'],
               'title': 'MySQL 排序落盘', 'body': ''}
        with self.assertRaises(Exception):
            kb_extract.extract(doc, [])

    def test_retrieval_filters_by_dialect(self):
        """§9.2：检索必须按方言过滤，否则会把 Oracle 方案推给 MySQL。"""
        from monitor.oam.kb import KbIndex, KbRecord
        idx = KbIndex()
        idx.add(KbRecord(record_id='kb:o1', symptom='PGA 使用率过高排序落盘',
                         db_types=['oracle'],
                         candidate_causes=['PGA_EXHAUSTED']))
        idx.add(KbRecord(record_id='kb:m1', symptom='排序落盘导致查询变慢',
                         db_types=['mysql'],
                         candidate_causes=['MISSING_INDEX']))
        res = idx.search('排序落盘导致查询变慢', db_type='mysql', top_k=5)
        ids = [h.record_id for h in (res.hits or [])]
        self.assertNotIn('kb:o1', ids, 'Oracle 记录不得出现在 MySQL 的检索结果里')
        self.assertIn('kb:m1', ids)
        # 反向：查 Oracle 时 MySQL 那条也不该出现
        res_o = idx.search('排序落盘导致查询变慢', db_type='oracle', top_k=5)
        self.assertNotIn('kb:m1', [h.record_id for h in (res_o.hits or [])])

    def test_retrieval_reports_miss(self):
        """检索未命中必须可观测：论文里它占部署失败的 12%。"""
        from monitor.oam.kb import KbIndex, KbRecord
        idx = KbIndex()
        idx.add(KbRecord(record_id='kb:m1', symptom='排序落盘导致查询变慢',
                         db_types=['mysql'], candidate_causes=['MISSING_INDEX']))
        res = idx.search('完全不相干的另一个问题关于网络配置', db_type='mysql',
                         top_k=5)
        self.assertTrue(res.missed, '零命中或低于阈值应标记为未命中')
        self.assertIsNotNone(res.miss_payload())
        self.assertEqual(idx.stats()['records'], 1)
        self.assertEqual(idx.stats()['by_dialect']['mysql'], 1)

    def test_retrieval_filters_by_category(self):
        """类目过滤必须**先于**相似度计算，不能只是挤掉 top-k 排序。

        模型点名了 `config_manual` 却拿到 `fault_solution` 的记录时，
        它分辨不出这是检索质量问题还是自己传错了参数 —— 这是 REV-10
        同一形状的错误（错来源的知识被当作对的交付）。
        """
        from monitor.oam.kb import KbIndex, KbRecord
        idx = KbIndex()
        idx.add(KbRecord(record_id='kb:fix', symptom='排序落盘导致查询变慢',
                         db_types=['mysql'], category='fault_solution',
                         candidate_causes=['MISSING_INDEX']))
        idx.add(KbRecord(record_id='kb:man', symptom='排序落盘导致查询变慢',
                         db_types=['mysql'], category='config_manual',
                         candidate_causes=['MISSING_INDEX']))
        # 两条检索文本**完全相同**，不靠相似度区分，只能靠类目过滤
        res = idx.search('排序落盘导致查询变慢', db_type='mysql',
                         category='config_manual', top_k=5)
        ids = [h.record_id for h in (res.hits or [])]
        self.assertEqual(ids, ['kb:man'])
        self.assertEqual(res.category, 'config_manual', '类目须随结果回传以便留痕')
        res2 = idx.search('排序落盘导致查询变慢', db_type='mysql',
                          category='fault_solution', top_k=5)
        self.assertEqual([h.record_id for h in (res2.hits or [])], ['kb:fix'])

    def test_retrieval_category_filter_fails_closed(self):
        """类目过滤失败关闭：没填类目的记录不得冒充该类目。

        放行它 = 把"这条到底属不属于这一类"的判断偷偷转嫁给模型。
        清空候选集反而更好：`no_category_match` 当场指出数据缺类目。
        """
        from monitor.oam.kb import KbIndex, KbRecord
        idx = KbIndex()
        idx.add(KbRecord(record_id='kb:none', symptom='排序落盘导致查询变慢',
                         db_types=['mysql'], category='',
                         candidate_causes=['MISSING_INDEX']))
        res = idx.search('排序落盘导致查询变慢', db_type='mysql',
                         category='config_manual', top_k=5)
        self.assertEqual(res.hits, [])
        self.assertTrue(res.missed)
        self.assertEqual(res.miss_reason, 'no_category_match')
        self.assertEqual(res.filtered['category'], 1)
        # 不指定类目时不受影响
        plain = idx.search('排序落盘导致查询变慢', db_type='mysql', top_k=5)
        self.assertEqual([h.record_id for h in (plain.hits or [])], ['kb:none'])

    def test_kb_document_staleness(self):
        from monitor import appconf
        warn = appconf.get('OAM_KB_STALENESS_WARN_DAYS')
        doc = KbDocument.objects.create(doc_id='KB-STALE', category='basic_fact',
                                        db_types=['mysql'], title='t', body='b',
                                        version='v1')
        self.assertEqual(doc.staleness_days, 0)
        doc.reviewed_at = timezone.now() - timedelta(days=warn + 5)
        doc.save()
        self.assertTrue(doc.is_stale())

    def test_headerless_pipe_table_keeps_first_param(self):
        """无表头分隔行的管道表，第一个参数不能被静默吞掉。

        丢一条 = 入库少一条 + 检索永远查不到 + 无任何痕迹，是最难发现的数据损坏。
        """
        from monitor.oam.kb import chunk
        doc = {'doc_id': 'D', 'category': 'config_manual', 'db_types': ['mysql'],
               'title': '参数手册',
               'body': ('| work_mem | 4MB | 64KB~1GB | 每个排序节点分配 |\n'
                        '| shared_buffers | 128MB | 16MB~64GB | 共享内存 |\n')}
        text = '\n'.join(c.text for c in chunk(doc))
        self.assertIn('work_mem', text, '首个参数被当成了表头并丢弃')
        self.assertIn('shared_buffers', text)

    def test_table_header_row_is_not_a_param(self):
        """有分隔行时，表头本身不应变成一个"参数"。"""
        from monitor.oam.kb import chunk
        doc = {'doc_id': 'D2', 'category': 'config_manual', 'db_types': ['mysql'],
               'title': '参数手册',
               'body': ('| 参数 | 默认值 | 取值范围 | 说明 |\n'
                        '|---|---|---|---|\n'
                        '| work_mem | 4MB | 64KB~1GB | 每个排序节点分配 |\n')}
        names = [c.text.split('\n')[0] for c in chunk(doc)]
        self.assertNotIn('默认值', names, '表头行被当成了参数单元')


# =============================================================================
# 可复现性：§8 第 1 条
# =============================================================================
@tag('unit')
class ReproducibilityTests(TestCase):
    """§8：同一 case_id + policy_version 重跑结果一致。"""

    def test_same_case_same_script_same_verdict(self):
        from monitor.oam.policy import diagnose
        case = _make_case(case_id='C-REPRO')
        case.save()
        _attach_snapshot(case)
        results = []
        for _ in range(2):
            env = ReplayEnv(OamSnapshot.from_case(case))
            policy = ScriptedPolicy(
                [_script('meta.guc_config', {'name': 'max_connections'}),
                 _answer('CONFIG_DRIFT', fix='SET max_connections=200',
                         refs=['tool:meta.guc_config'])], db_type='mysql')
            traj = diagnose(case, policy, env, max_turns=4)
            results.append((traj.passed, traj.report.get('root_cause')))
        self.assertEqual(results[0], results[1], '同用例同策略须完全一致')

    def test_replay_env_noise_is_seeded_per_case(self):
        """噪声按 case_id 派生种子，否则"可复现"验收项无从成立。"""
        start, end = _window()
        series = [[start + timedelta(seconds=60 * i), 50.0 + i % 7] for i in range(60)]
        outs = []
        for _ in range(2):
            env = ReplayEnv(OamSnapshot(case_id='SEED1', db_type='mysql',
                                        kpi_series={'m': series}, window=(start, end)))
            outs.append(env.perturb(series))
        self.assertEqual(len(outs[0]), len(outs[1]))
        self.assertEqual(outs[0], outs[1], '同 case_id 的噪声必须可复现')


# =============================================================================
# 接口：权限闸 与 数据范围闸 分开验
# =============================================================================
@tag('unit')
class OamApiPermissionGateTests(TestCase):
    """权限闸：零权限 + 数据范围含目标 ⇒ 403。"""

    def setUp(self):
        self.db = make_db('oam-perm-db', port=3410)
        self.nobody = make_user('oamnobody', RoleCode.READONLY, [],
                                allowed_dbs=[self.db.id])
        self.client = Client()
        login(self.client, self.nobody)

    def test_diagnose_requires_tickets_execute(self):
        r = self.client.post('/api/v3/oam/diagnose/',
                             data=json.dumps({'config_id': self.db.id, 'symptom': '慢'}),
                             content_type='application/json')
        self.assertEqual(r.status_code, 403, '无 tickets.execute 不应能发起诊断')

    def test_trajectory_view_requires_alerts_view(self):
        r = self.client.get('/api/v3/oam/trajectories/BR-1/')
        self.assertEqual(r.status_code, 403)

    def test_tools_requires_metrics_view(self):
        r = self.client.get('/api/v3/oam/tools/?db_type=mysql')
        self.assertEqual(r.status_code, 403)

    def test_case_create_requires_manage(self):
        r = self.client.post('/api/v3/oam/cases/',
                             data=json.dumps({'case_id': 'X', 'db_type': 'mysql',
                                              'gt_root_cause': 'LOCK_WAIT'}),
                             content_type='application/json')
        self.assertEqual(r.status_code, 403)


@tag('unit')
class OamApiScopeTests(TestCase):
    """数据范围闸：权限齐备但范围受限 ⇒ 404，且与"不存在"响应一致。

    这是第六次越权防护。历史上复发五次（BUG-103 → REV-01 → R25-01 → RT-01 →
    V2.6 W1 `3b2884c`）。注意**不能用零权限账号测**——会被 403 短路，
    等于没测到范围。
    """

    def setUp(self):
        self.mine = make_db('oam-mine', port=3411)
        self.others = make_db('oam-others', port=3412)
        self.user = make_user(
            'oamscoped', RoleCode.DBA,
            [Perm.TICKETS_EXECUTE, Perm.ALERTS_VIEW, Perm.METRICS_VIEW,
             Perm.ALERT_CONFIG_MANAGE, Perm.ALERT_CONFIG_VIEW],
            allowed_dbs=[self.mine.id])
        self.client = Client()
        login(self.client, self.user)

        # 一条绑定到范围外实例的轨迹
        self.foreign_case = OamCase.objects.create(
            case_id='C-FOREIGN', category='tuning', difficulty='easy',
            db_type='mysql', symptom='慢', gt_root_cause='LOCK_WAIT',
            source_config=self.others, split='test')
        self.foreign_traj = OamTrajectory.objects.create(
            traj_id='TRAJ-FOREIGN', case=self.foreign_case,
            policy_backend='prompted', policy_version='v1', report={}, turns=0)

    def test_diagnose_out_of_scope_is_404(self):
        r = self.client.post('/api/v3/oam/diagnose/',
                             data=json.dumps({'config_id': self.others.id,
                                              'symptom': '慢'}),
                             content_type='application/json')
        self.assertEqual(r.status_code, 404, '越权访问范围外实例应 404')
        self.assertNotIn('oam-others', r.content.decode('utf-8'))

    def test_out_of_scope_and_missing_are_same_code(self):
        """越权与不存在必须同响应，否则 403/404 差异会泄露"该 id 存在"。"""
        r_missing = self.client.post('/api/v3/oam/diagnose/',
                                     data=json.dumps({'config_id': 999999,
                                                      'symptom': '慢'}),
                                     content_type='application/json')
        r_denied = self.client.post('/api/v3/oam/diagnose/',
                                    data=json.dumps({'config_id': self.others.id,
                                                     'symptom': '慢'}),
                                    content_type='application/json')
        self.assertEqual(r_missing.status_code, r_denied.status_code)

    def test_trajectory_of_out_of_scope_case_is_404(self):
        r = self.client.get(f'/api/v3/oam/trajectories/{self.foreign_traj.traj_id}/')
        self.assertEqual(r.status_code, 404, '越权读取范围外实例的轨迹')
        self.assertNotIn('C-FOREIGN', r.content.decode('utf-8'))

    def test_trajectory_audit_also_scoped(self):
        r = self.client.get(f'/api/v3/oam/trajectories/{self.foreign_traj.traj_id}/audit/')
        self.assertEqual(r.status_code, 404, '审计接口同样必须套数据范围')

    def test_case_snapshot_out_of_scope_is_404(self):
        r = self.client.post(f'/api/v3/oam/cases/{self.foreign_case.case_id}/snapshot/',
                             data=json.dumps({}), content_type='application/json')
        self.assertEqual(r.status_code, 404)

    def test_in_scope_trajectory_is_visible(self):
        case = OamCase.objects.create(
            case_id='C-MINE', category='tuning', difficulty='easy',
            db_type='mysql', symptom='慢', gt_root_cause='LOCK_WAIT',
            source_config=self.mine, split='test')
        traj = OamTrajectory.objects.create(
            traj_id='TRAJ-MINE', case=case, config=self.mine,
            policy_backend='prompted', policy_version='v1', report={}, turns=0)
        r = self.client.get(f'/api/v3/oam/trajectories/{traj.traj_id}/')
        self.assertEqual(r.status_code, 200, '范围内的实例应当可读')

    def test_tools_endpoint_is_scope_independent(self):
        """工具目录不含任何实例数据，不套实例范围，但权限闸仍要生效。"""
        r = self.client.get('/api/v3/oam/tools/?db_type=mysql')
        self.assertEqual(r.status_code, 200)
        payload = r.json()['data']
        self.assertEqual(payload['db_type'], 'mysql')
        self.assertIn('fix.kill_session', payload['mutating'],
                      '变更类必须显式暴露为"不可调用"而不是隐藏') 
