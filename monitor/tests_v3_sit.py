# -*- coding: utf-8 -*-
"""V3.0 OAM 系统集成测试（SIT, System Integration Test）。

## 与 `tests_v3_oam.py` 的分工

| 文件 | 层次 | 标签 | 回答的问题 |
|------|------|------|-----------|
| `tests_v3_oam.py` | 单元 / 组件 | `unit` | 每个模块**自己**的契约成立吗（设计 §8 的 14 条验收项） |
| 本文件 | **系统集成** | `integration` | 模块**串起来**还成立吗：HTTP 路由 → 视图 → 权限/范围 → 服务层 → ORM → 策略循环 → 工具代理 → 感知层 |

单元层全绿而集成层断裂，是这个项目历史上最典型的失效形态
（"有页面无数据"、REV-02 谎报执行成功、R25-02 编造证据都是这一类）。
所以本文件的每条用例都刻意**跨过至少两个边界**，并且断言设计的
**对外契约**（`V3.0_DBAGENT_REDESIGN.md` §6/§7/§8），而不是实现细节。

## 用例编号

`SIT-V3-xx`，与报告 `V3.0_SIT_UAT_TEST_REPORT.md` 一一对应。
"""
import json
from datetime import timedelta

from django.test import Client, TestCase, TransactionTestCase, override_settings, tag
from django.utils import timezone

from monitor.auth import Perm, RoleCode
from monitor.models import (
    AuditLog, KbDocument, KbRecord, OamCase, OamSnapshotBlob, OamTrajectory,
    PlaybookRun,
)
from monitor.oam.verifier import STATUS_EVIDENCE_INSUFFICIENT
from monitor.tests_bugfix import login, make_db, make_user


# =============================================================================
# 本地夹具：脚本化策略（确定性，不依赖 LLM 与网络）
# =============================================================================
class ScriptedPolicy:
    """按预设脚本逐轮吐输出。

    集成用例必须**确定性**：真实 LLM 的温度与可用性不该影响一条
    "接口契约是否成立"的断言（设计 §8 第一条验收项：可复现）。
    """

    backend = 'prompted'
    version = 'sit-v1'

    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.calls = 0

    def step(self, state, turn=1):
        idx = min(self.calls, len(self.outputs) - 1)
        self.calls += 1
        return self.outputs[idx]


def _patch_policy(outputs):
    """把 run_case 里的策略后端替换成脚本策略（确定性）。"""
    from unittest import mock
    return mock.patch('monitor.oam.bench.runner.build_policy',
                      return_value=ScriptedPolicy(outputs))


def _tool(tool_name, params=None):
    """构造一轮**符合思考-行动-观察协议**的原始输出。

    必须带 `<think>` / `<tool_call>` 标签：`parse_action` 只认标签内的内容，
    裸 JSON 会被判 `BAD_PROTOCOL`，用例就会"跑通但没跑到"（假绿）。
    """
    payload = json.dumps({'tool': tool_name, 'params': params or {}},
                         ensure_ascii=False)
    return f'<think>验证 {tool_name}</think><tool_call>{payload}</tool_call>'


def _answer(root_cause='', fix='', refs=None, status='', **extra):
    payload = {'root_cause': root_cause, 'evidence_refs': refs or []}
    if fix:
        payload['fix'] = fix
    if status:
        payload['status'] = status
    payload.update(extra)
    return f'<think>汇总结论</think><answer>{json.dumps(payload, ensure_ascii=False)}</answer>'


def _window(hours=2):
    end = timezone.now()
    return end - timedelta(hours=hours), end


def _mkcase(case_id, db_type='mysql', gt='LOCK_WAIT', **kw):
    kw.setdefault('category', 'tuning')
    kw.setdefault('difficulty', 'easy')
    kw.setdefault('symptom', '连接数突增且部分会话阻塞')
    kw.setdefault('split', 'test')
    return OamCase.objects.create(case_id=case_id, db_type=db_type,
                                  gt_root_cause=gt, **kw)


def _mksnapshot(case, points=60, hours=2):
    start, end = _window(hours)
    return OamSnapshotBlob.objects.create(
        case=case, window_start=start, window_end=end,
        kpi_series={'conn_usage_pct': [
            [(start + timedelta(seconds=60 * i)).isoformat(), 20 + i % 5]
            for i in range(points)]},
        session_samples=[{'sid': 1, 'blocking': 0, 'sql_id': 'abc'}],
        plans=[], logs=[],
        config_meta={'parameters': {'max_connections': '150'}},
        schema_meta={'tables': {'orders': {'rows': 12000000}}})


# =============================================================================
# SIT-V3-01~05 诊断接口：设计的对外契约（§6）
# =============================================================================
@tag('integration')
class DiagnoseApiSIT(TransactionTestCase):
    """`POST /api/v3/oam/diagnose/` 的端到端链路。

    用 `TransactionTestCase` 而不是 `TestCase`：后者会把外键约束改成
    `DEFERRABLE INITIALLY DEFERRED`，悬空外键要等到用例拆卸才报错，
    掩盖了生产环境（约束即时生效）的真实行为。

    设计 §6 契约三条：
      1. 入参 `{config_id, symptom, category?}`；**异步返回 `traj_id`**；
      2. **只产出报告，不执行任何变更**；推荐修复须附 `risk_level` 与"需人工审批"；
      3. 报告未过证据审计 ⇒ `status='evidence_insufficient'` 且不得给根因。
    """

    def setUp(self):
        self.db = make_db('sit-diag-db', port=3511)
        self.user = make_user(
            'sit-diag-user', RoleCode.DBA,
            [Perm.TICKETS_EXECUTE, Perm.ALERTS_VIEW, Perm.METRICS_VIEW],
            allowed_dbs=[self.db.id])
        self.client = Client()
        login(self.client, self.user)

    def _diagnose(self, outputs, **body):
        payload = {'config_id': self.db.id, 'symptom': '连接数突增且部分会话阻塞'}
        payload.update(body)
        with override_settings(OAM_ENABLED=True), _patch_policy(outputs):
            return self.client.post('/api/v3/oam/diagnose/',
                                    data=json.dumps(payload),
                                    content_type='application/json')

    def test_SIT_V3_01_diagnose_returns_traj_id_and_trajectory_is_readable(self):
        """设计 §6：接口返回 `traj_id`，且按该 id 能读到完整轨迹。

        不能只断言 200 —— 返回体里没有 `traj_id` 时，调用方拿不到任何句柄，
        「发起诊断 → 查看轨迹 → 查看审计」这条业务链路直接断掉。
        """
        r = self._diagnose([_answer(status=STATUS_EVIDENCE_INSUFFICIENT)])
        self.assertEqual(r.status_code, 200, r.content[:400])
        data = r.json()['data']
        self.assertIn('traj_id', data,
                      '设计 §6 明写"异步返回 traj_id"；缺了它调用方无法回查轨迹')
        traj_id = data['traj_id']
        self.assertTrue(traj_id)

        r2 = self.client.get(f'/api/v3/oam/trajectories/{traj_id}/')
        self.assertEqual(r2.status_code, 200,
                         f'按接口返回的 traj_id={traj_id} 必须能读到轨迹')
        body = r2.json()['data']
        self.assertEqual(body['traj_id'], traj_id)
        self.assertIn('turns', body)

    def test_SIT_V3_02_diagnose_persists_a_traceable_trajectory(self):
        """轨迹必须落库，且其外键必须指向真实存在的用例。

        只断言"多了一行"不够：用例没落库时，轨迹行仍可能插进去而外键悬空
        （测试库的外键被改成 DEFERRABLE），生产库则会立刻违反约束、被
        `_persist` 的 `except` 吞成 degrade 留痕 —— 两种情况下轨迹都不可追溯。
        """
        r = self._diagnose([_answer(status=STATUS_EVIDENCE_INSUFFICIENT)])
        self.assertEqual(r.status_code, 200, r.content[:400])
        traj = OamTrajectory.objects.order_by('-started_at').first()
        self.assertIsNotNone(traj, '一次成功的诊断必须留下一条可审计的轨迹')
        self.assertTrue(
            OamCase.objects.filter(pk=traj.case_id).exists(),
            f'轨迹 {traj.traj_id} 指向的用例 {traj.case_id!r} 不存在（外键悬空）：'
            f'线上诊断没有把用例落库，轨迹因此不可追溯、数据范围闸也无从判定')

    def test_SIT_V3_03_report_never_authorises_direct_change(self):
        """设计 §6 硬约束 1：只诊断不执行；推荐修复必须带审批标记。

        注意区分两类 `AuditLog`：`AuditApiMiddleware` 会给每个 POST 写一条
        `API_CREATE`（那是"有人调过接口"的流水），本用例关心的是
        **执行类**审计（KILL_SESSION / EXECUTE_SQL / incident_transition …）——
        诊断接口不该产生任何一条。
        """
        api_types = ('API_CREATE', 'API_UPDATE', 'API_DELETE', 'API_UNKNOWN')
        a_before = AuditLog.objects.exclude(action_type__in=api_types).count()
        p_before = PlaybookRun.objects.count()
        r = self._diagnose([_tool('fix.kill_session', {'sid': 1}),
                            _answer(status=STATUS_EVIDENCE_INSUFFICIENT)])
        self.assertEqual(r.status_code, 200, r.content[:400])
        # 响应体形状：{code, message, timestamp, data:{report:{...}}}
        report = r.json()['data'].get('report') or {}
        fix = report.get('recommended_fix') or {}
        self.assertTrue(fix.get('requires_approval'), '推荐修复必须标"需人工审批"')
        self.assertIn('risk_level', fix)
        self.assertIn('execute_via', fix)
        executed = list(AuditLog.objects.exclude(action_type__in=api_types)
                        .values_list('action_type', flat=True))
        self.assertEqual(len(executed), a_before,
                         f'诊断接口不得产生执行类工单，实际新增 {executed}')
        self.assertEqual(PlaybookRun.objects.count(), p_before,
                         '诊断接口不得执行 playbook')

    def test_SIT_V3_04_empty_snapshot_escalates_and_gives_no_root_cause(self):
        """设计 §8：零数据实例必须返回 `evidence_insufficient`，不得编造根因。

        防 R25-02（编造 ASH 会话/表空间）与 REV-10（无条件追加结论）。
        """
        r = self._diagnose([_tool('db.session_profile', {}),
                            _answer(root_cause='LOCK_WAIT',
                                    refs=['tool:db.session_profile'])])
        self.assertEqual(r.status_code, 200, r.content[:400])
        report = r.json()['data'].get('report') or {}
        self.assertEqual(report.get('status'), STATUS_EVIDENCE_INSUFFICIENT,
                         '空快照下不得给出可判定的根因')
        self.assertFalse(report.get('root_cause'), '证据不足时 root_cause 必须为空')

    def test_SIT_V3_05_online_verdict_is_not_scored_against_fabricated_truth(self):
        """线上诊断没有专家标注真值，`verdict` 不得按捏造真值判分。

        设计 §9.1：封闭标签空间**只用于自动验证与离线评测**。
        线上诊断若仍拿一个硬编码的 `gt_root_cause` 去 `verify()`，
        接口会稳定回传"根因不匹配"，把无意义的信息当成验收结论。
        """
        r = self._diagnose([_tool('db.session_profile', {}),
                            _answer(root_cause='LOCK_WAIT',
                                    refs=['tool:db.session_profile'])])
        self.assertEqual(r.status_code, 200, r.content[:400])
        report = r.json()['data'].get('report') or {}
        verdict = report.get('verdict') or {}
        self.assertNotIn('根因不匹配', verdict.get('reason', ''),
                         '线上诊断按捏造的真值判分：%r。'
                         '设计 §9.1 明确封闭标签空间只用于离线评测'
                         % verdict.get('reason'))


# =============================================================================
# SIT-V3-06~09 L3 知识库接线：设计 §3.2 / §7 P0 / §1.6⑤
# =============================================================================
@tag('integration')
class KnowledgeBaseWiringSIT(TestCase):
    """L3 是本设计相对论文的主要收益来源（消融：去掉它 84.5% → 29.2%）。

    所以"知识库写好了"与"知识库接上了"是两件事，必须分开验。
    """

    def setUp(self):
        # 检索层（KbIndex）吃的是 extract.KbRecord **dataclass**，
        # 落库层（oam_kb_migrate_rules / KbDocument）用的是 ORM 模型。
        # 两者同名不同形，本文件刻意把"内存态链路"与"落库态链路"分开验。
        from monitor.oam.kb import KbRecord as KbRecordDT
        self.rec = KbRecordDT(
            record_id='R-SIT-1', symptom='锁等待升高、事务阻塞',
            db_types=['mysql'], candidate_causes=['LOCK_WAIT'],
            probe_actions=['db.lock_chain'], fix_actions=['终止长事务'],
            category='fault_solution', text='锁等待升高 事务阻塞 LOCK_WAIT',
            version='v1')
        KbDocument.objects.create(
            doc_id='D-SIT-1', category='fault_solution', db_types=['mysql'],
            title='锁等待处理方案', body='先看阻塞链', version='v1',
            approved_by='sit')

    @staticmethod
    def _kb_refs_of(traj):
        refs = []
        for step in traj.steps:
            if step.observation:
                refs.extend(step.observation.evidence_refs)
        return refs

    @staticmethod
    def _idx(rec):
        """建索引。注意 `KbIndex.add()` 返回 None（只有 `build()` 返回 self），
        所以 **不能**链式写成 `KbIndex().add(r).build()` —— 模块 docstring 的
        示例正是这么写的，照抄会踩 AttributeError。"""
        from monitor.oam.kb import KbIndex
        idx = KbIndex()
        idx.add(rec)
        return idx.build()

    def test_SIT_V3_06_kb_search_observation_carries_kb_refs(self):
        """`kb.search` 的观测必须带 `kb:<id>` 证据引用。

        `monitor/oam/perception/kb_digest.py` 的契约写明 refs 统一带 `kb:` 前缀，
        `types.Trajectory.kb_refs()` 也正是按此前缀挑知识库引用。
        引用出不来 ⇒ 证据审计认不出知识库支撑 ⇒ 咨询类用例永远判失败。
        """
        from monitor.oam.env import OamSnapshot, ReplayEnv
        from monitor.oam.perception import summarize_tool_result

        idx = self._idx(self.rec)
        start, end = _window()
        env = ReplayEnv(OamSnapshot(case_id='KB1', db_type='mysql',
                                    window=(start, end)), kb_index=idx)
        res = env.call('kb.search', {'query': '锁等待升高'})
        self.assertTrue(res.ok, f'kb.search 应能取到结果，实得 {res.error_code}')

        try:
            obs = summarize_tool_result('kb.search', res.data, state=None)
        except TypeError as e:
            self.fail('kb.search 的检索结果无法被感知层凝练成观测：'
                      f'{type(e).__name__}: {e}（L3 → L2 桥接断裂）')
        kb_refs = [r for r in obs.evidence_refs if r.startswith('kb:')]
        self.assertTrue(kb_refs,
                        'kb.search 的观测没有产生任何 kb: 引用（refs=%r）；'
                        '整条 L3→L5 证据链因此断裂' % obs.evidence_refs)

    def test_SIT_V3_07_consultation_case_can_pass_end_to_end(self):
        """咨询类用例：引用集合与 `required_evidence` 完全相等即判通过。

        设计 §1.6⑤ 是**端到端**判据（策略检索 → 引用 → 验证器判定），
        任何一环断了，这一整类用例的成功率都恒为 0 —— 基准数字就失去意义。
        """
        from monitor.oam.bench.runner import run_case

        idx = self._idx(self.rec)
        case = _mkcase('C-SIT-CONS', category='consultation', gt='LOCK_WAIT',
                       required_evidence=['kb:R-SIT-1'])
        outputs = [_tool('kb.search', {'query': '锁等待升高'}),
                   _answer(root_cause='LOCK_WAIT', refs=['kb:R-SIT-1'])]
        with _patch_policy(outputs):
            traj, verdict, _audit, _env = run_case(case, kb_index=idx, persist=False)
        self.assertIn('kb:R-SIT-1', self._kb_refs_of(traj),
                      '策略检索到的知识没有以 kb: 引用进入观测证据')
        self.assertTrue(verdict.passed,
                        '咨询类用例在 KB 正常接线时应能判通过；实际理由=%r'
                        % verdict.reason)

    def test_SIT_V3_08_bench_wires_the_knowledge_base(self):
        """基准评测必须把已落库的 KB 记录接进环境。

        若 `kb_index` 在生产路径上永远为 None，`kb.search` 恒返回 NO_DATA，
        跑出来的成功率是**没有 L3 的系统**的成绩，却被当成 V3.0 的成绩汇报 ——
        这正是设计 §8 要防的"指标看着好、实际不成立"。
        """
        from monitor.oam.bench.runner import run_case
        case = _mkcase('C-SIT-KBWIRE')
        with _patch_policy([_tool('kb.search', {'query': '锁等待升高'}),
                            _answer(status=STATUS_EVIDENCE_INSUFFICIENT)]):
            traj, _v, _a, _env = run_case(case, persist=False)
        codes = [s.tool_error_code for s in traj.steps if s.tool_error_code]
        self.assertNotIn('NO_DATA', codes,
                         '未注入 KB 索引时 kb.search 恒为 NO_DATA（全部错误码=%r）——'
                         'L3 在评测路径上缺席' % codes)

    def test_SIT_V3_09_kb_migration_command_feeds_retrieval(self):
        """管理命令落库的规则必须能进入检索索引。

        迁移不是终点，"可检索"才是（设计 §7 P0：L3 KB 三步构建 +
        RULES_V2 迁移为 KbRecord）。落库用的是 ORM 模型，
        `KbIndex.add()` 吃的是 `extract.KbRecord` dataclass ——
        两者之间**必须有适配**，否则迁移出的规则永远进不了检索上下文，
        L3 就只是一堆躺在表里的行。
        """
        from io import StringIO

        from django.core.management import call_command

        from monitor.oam.bench.runner import default_kb_index
        from monitor.oam.kb import records_from_orm

        call_command('oam_kb_migrate_rules', stdout=StringIO())
        rows = list(KbRecord.objects.filter(is_active=True))
        self.assertGreater(len(rows), 0, '迁移必须产出 KbRecord 行')
        try:
            adapted = records_from_orm(rows)
        except AttributeError as e:
            self.fail(f'ORM KbRecord 无法适配成检索层的记录：{e}')
        self.assertEqual(len(adapted), len(rows))
        self.assertTrue(all(r.text for r in adapted),
                        '适配后的检索文本不得为空，否则索引里查不到任何东西')
        # 生产默认路径：调用方不注入索引时必须自动接上已落库的 KB
        idx = default_kb_index()
        self.assertGreater(len(idx), 0,
                           '默认 KB 索引为空 —— L3 在评测/诊断路径上仍然缺席')


# =============================================================================
# SIT-V3-10~12 快照接口：输入校验的边界（§4.1）
# =============================================================================
@tag('integration')
class SnapshotApiSIT(TestCase):
    """设计 §4.1：结构性越界一律**明确拒绝**（400），不静默截断、不 500。"""

    def setUp(self):
        self.case = _mkcase('C-SIT-SNAP')
        self.user = make_user('sit-snap-user', RoleCode.DBA,
                              [Perm.ALERT_CONFIG_MANAGE])
        self.client = Client()
        login(self.client, self.user)

    def _post(self, payload):
        return self.client.post(f'/api/v3/oam/cases/{self.case.case_id}/snapshot/',
                                data=json.dumps(payload),
                                content_type='application/json')

    def test_SIT_V3_10_unparseable_window_is_a_4xx_not_500(self):
        """窗口不可解析时必须返回确定性 4xx，而不是未捕获异常导致的 500。"""
        r = self._post({'window': ['not-a-date', 'also-bad'], 'kpi_series': {}})
        self.assertLess(r.status_code, 500,
                        f'非法窗口返回 {r.status_code}，说明校验异常未收敛成'
                        f'确定性错误码：{r.content[:300]}')
        self.assertGreaterEqual(r.status_code, 400)

    def test_SIT_V3_11_naive_aware_mixed_window_is_a_4xx_not_500(self):
        """带时区与不带时区的时间戳混用也不能炸成 500。"""
        now = timezone.now()
        r = self._post({'window': [now.replace(tzinfo=None).isoformat(),
                                   now.isoformat()], 'kpi_series': {}})
        self.assertLess(r.status_code, 500,
                        f'aware/naive 混用返回 {r.status_code}：{r.content[:300]}')

    def test_SIT_V3_12_empty_window_is_rejected(self):
        r = self._post({'window': [], 'kpi_series': {}})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json()['code'], 'BAD_REQUEST')


# =============================================================================
# SIT-V3-13~15 基准评测：成绩与覆盖度必须同屏且自洽（§8）
# =============================================================================
@tag('integration')
class BenchReportSIT(TestCase):
    """设计 §8：「评测与覆盖度同屏」「成功率按类别宏平均」。"""

    def setUp(self):
        self.user = make_user('sit-bench-user', RoleCode.DBA,
                              [Perm.ALERT_CONFIG_MANAGE, Perm.METRICS_VIEW])
        self.client = Client()
        login(self.client, self.user)
        for i, (cause, cat) in enumerate([('LOCK_WAIT', 'tuning'),
                                          ('MISSING_INDEX', 'tuning'),
                                          ('CONN_EXHAUSTED', 'overload')], start=1):
            c = _mkcase(f'C-SIT-BENCH-{i}', gt=cause, category=cat)
            _mksnapshot(c, points=30)

    def _run_bench(self):
        with override_settings(OAM_ENABLED=True), \
                _patch_policy([_answer(status=STATUS_EVIDENCE_INSUFFICIENT)]):
            return self.client.post('/api/v3/oam/bench/run/',
                                    data=json.dumps({'split': 'test',
                                                     'policy_backend': 'prompted'}),
                                    content_type='application/json')

    def test_SIT_V3_13_bench_run_returns_coverage_with_the_score(self):
        r = self._run_bench()
        self.assertEqual(r.status_code, 200, r.content[:400])
        data = r.json()['data']
        self.assertIn('coverage', data, '设计 §8：缺 coverage 就不算完成评测')
        self.assertTrue(data['coverage'])
        for key in ('db_types', 'root_causes_covered', 'root_causes_missing',
                    'plan_operator_buckets', 'noise_injection'):
            self.assertIn(key, data['coverage'], f'覆盖度缺字段 {key}')

    def test_SIT_V3_14_coverage_covered_and_missing_are_consistent(self):
        """覆盖度必须自洽：已覆盖 ∩ 未覆盖 = ∅。

        逐用例上报时若用了**累积**的已覆盖集合，第 1 条用例的"缺失根因"
        会包含后面用例已经覆盖的根因；合并后 `root_causes_covered` 与
        `root_causes_missing` 互相重叠 —— 报告自相矛盾，且无法按用例下钻。
        """
        r = self._run_bench()
        cov = r.json()['data']['coverage']
        covered = set(cov['root_causes_covered'])
        missing = set(cov['root_causes_missing'])
        self.assertFalse(covered & missing,
                         '同一份覆盖度里同时把 %r 记为"已覆盖"和"未覆盖"'
                         % sorted(covered & missing))

    def test_SIT_V3_14b_multi_pass_coverage_is_not_cumulative(self):
        """多条用例**都判通过**时，覆盖度仍不许自相矛盾。

        `SIT-V3-14` 里三条用例全部升级（都不通过），累积与非累积算出的
        `covered` 恰好相同 —— 覆盖不到这个缺陷。这里让策略对**每个用例**
        给出它自己的真值与修复规格，制造"多条不同根因同时被覆盖"的真实场景：
        累积写法会把第 2、3 条用例已覆盖的根因列进 `missing`。
        """
        from unittest import mock

        from monitor.oam.bench.runner import run_bench

        fix = {'sort_buffer_size': '4MB'}
        fix_spec = {'params': dict(fix)}

        class _GtPolicy:
            backend = 'prompted'
            version = 'sit-v1'

            def __init__(self, gt):
                self.gt, self.calls = gt, 0

            def step(self, state, turn=1):
                self.calls += 1
                if self.calls == 1:
                    return _tool('db.lock_chain', {})
                return _answer(root_cause=self.gt, fix=fix,
                               refs=['tool:db.lock_chain'])

        def _build(backend, db_type='', incident_id=''):
            return _GtPolicy(OamCase.objects.get(pk=incident_id).gt_root_cause)

        cases = []
        for i, (cause, cat) in enumerate([('LOCK_WAIT', 'tuning'),
                                          ('MISSING_INDEX', 'tuning'),
                                          ('SLOW_QUERY_SURGE', 'tuning')], start=1):
            c = _mkcase(f'C-SIT-COV-{i}', gt=cause, category=cat, fix_spec=fix_spec)
            _mksnapshot(c, points=30)
            cases.append(c)

        with mock.patch('monitor.oam.bench.runner.build_policy',
                        side_effect=_build):
            rep = run_bench(cases, policy_backend='prompted', split='test')
        cov = rep['coverage']
        covered = set(cov['root_causes_covered'])
        missing = set(cov['root_causes_missing'])
        self.assertGreaterEqual(len(covered), 2,
                                f'本用例需要至少两条不同根因被判通过，实际 covered={covered}')
        self.assertFalse(covered & missing,
                         f'{sorted(covered & missing)} 同时被记为已覆盖与未覆盖 —— '
                         f'覆盖度是按用例累积上报的')
        self.assertTrue({c.gt_root_cause for c in cases} <= covered,
                        '每条通过的用例的根因都必须出现在 root_causes_covered 里')

    def test_SIT_V3_15_bench_report_endpoint_refuses_missing_coverage(self):
        """设计 §6 硬约束 3：缺 coverage 视为接口未实现。"""
        from monitor.models import OamBenchRun
        run = OamBenchRun.objects.create(
            run_id='BR-SIT-NOCOV', split='test', policy_backend='prompted',
            policy_version='v1', status='done', total=0, coverage={})
        r = self.client.get(f'/api/v3/oam/bench/report/{run.run_id}/')
        self.assertEqual(r.status_code, 500)
        self.assertEqual(r.json()['code'], 'COVERAGE_MISSING')


# =============================================================================
# SIT-V3-16~19 两道独立的闸：权限 与 数据范围（AGENTS.md 红线）
# =============================================================================
@tag('integration')
class OamGateSIT(TestCase):
    """权限闸与数据范围闸**分开验** —— 合在一起会被 403 短路，等于没测到范围。"""

    def setUp(self):
        self.mine = make_db('sit-gate-mine', port=3521)
        self.others = make_db('sit-gate-others', port=3522)
        self.foreign_case = _mkcase('C-SIT-FOREIGN', source_config=self.others)

    @staticmethod
    def _user(name, perms, allowed):
        return make_user(name, RoleCode.DBA, perms, allowed_dbs=allowed)

    def test_SIT_V3_16_scope_gate_blocks_out_of_scope_instance(self):
        """有权限、范围受限 ⇒ 404（不是 403，且与"不存在"同码）。"""
        c = Client()
        login(c, self._user('sit-scope',
                            [Perm.TICKETS_EXECUTE, Perm.ALERTS_VIEW], [self.mine.id]))
        with override_settings(OAM_ENABLED=True):
            r = c.post('/api/v3/oam/diagnose/',
                       data=json.dumps({'config_id': self.others.id, 'symptom': '慢'}),
                       content_type='application/json')
        self.assertEqual(r.status_code, 404)
        self.assertNotIn('sit-gate-others', r.content.decode('utf-8'))

    def test_SIT_V3_17_permission_gate_blocks_without_permission(self):
        """范围包含目标、但没有权限 ⇒ 403。"""
        c = Client()
        login(c, self._user('sit-perm', [Perm.ALERTS_VIEW], [self.mine.id]))
        with override_settings(OAM_ENABLED=True):
            r = c.post('/api/v3/oam/diagnose/',
                       data=json.dumps({'config_id': self.mine.id, 'symptom': '慢'}),
                       content_type='application/json')
        self.assertEqual(r.status_code, 403)

    def test_SIT_V3_18_anonymous_is_rejected(self):
        r = Client().get('/api/v3/oam/tools/?db_type=mysql')
        self.assertEqual(r.status_code, 401)

    def test_SIT_V3_19_trajectory_of_out_of_scope_case_is_404(self):
        traj = OamTrajectory.objects.create(
            traj_id='TRAJ-SIT-FOREIGN', case=self.foreign_case, config=self.others,
            policy_backend='prompted', policy_version='v1',
            report={'root_cause': 'LOCK_WAIT'}, turns=0)
        c = Client()
        login(c, self._user('sit-scope2', [Perm.ALERTS_VIEW], [self.mine.id]))
        for url in (f'/api/v3/oam/trajectories/{traj.traj_id}/',
                    f'/api/v3/oam/trajectories/{traj.traj_id}/audit/'):
            r = c.get(url)
            self.assertEqual(r.status_code, 404, f'{url} 未套数据范围')


# =============================================================================
# SIT-V3-20~22 安全边界：变更类工具与方言隔离（§8）
# =============================================================================
@tag('integration')
class SafetyBoundarySIT(TestCase):
    """设计 §8：变更类工具一律不可由策略直接调用；方言隔离。"""

    def setUp(self):
        self.db = make_db('sit-safe-db', db_type='mysql', port=3531)

    def test_SIT_V3_20_mutating_tool_is_forbidden_and_trace_is_persisted(self):
        """策略点变更类工具 ⇒ 轨迹里必须留下 MUTATION_FORBIDDEN 且轨迹落库。"""
        from monitor.oam.bench.runner import run_case
        case = _mkcase('C-SIT-MUT', source_config=self.db)
        outputs = [_tool('fix.kill_session', {'sid': 1}),
                   _answer(status=STATUS_EVIDENCE_INSUFFICIENT)]
        with _patch_policy(outputs):
            traj, _v, _a, _env = run_case(case, persist=True)
        codes = [s.tool_error_code for s in traj.steps]
        self.assertIn('MUTATION_FORBIDDEN', codes,
                      '变更类工具的拒绝信号必须进轨迹，供审计核对')
        self.assertTrue(OamTrajectory.objects.filter(case=case).exists(),
                        '轨迹必须落库，否则审计与追溯都无从谈起')

    def test_SIT_V3_21_tool_catalog_is_dialect_specific(self):
        """同一逻辑工具的描述必须按方言分化（防 REV-10 复发）。"""
        u = make_user('sit-tools', RoleCode.DBA, [Perm.METRICS_VIEW],
                      allowed_dbs=[self.db.id])
        c = Client()
        login(c, u)
        mysql = c.get('/api/v3/oam/tools/?db_type=mysql').json()['data']
        oracle = c.get('/api/v3/oam/tools/?db_type=oracle').json()['data']
        m = {t['name']: t['description'] for t in mysql['tools']}
        o = {t['name']: t['description'] for t in oracle['tools']}
        shared = set(m) & set(o)
        self.assertTrue(shared, '两个方言应有同名工具，否则本用例失去意义')
        self.assertTrue(any(m[k] != o[k] for k in shared),
                        '同名工具的描述在所有方言下完全相同 = 语义增强没做')

    def test_SIT_V3_22_mutating_tools_are_declared_not_executable(self):
        """变更类工具必须显式暴露为"不可调用"，而不是隐藏。"""
        u = make_user('sit-tools2', RoleCode.DBA, [Perm.METRICS_VIEW],
                      allowed_dbs=[self.db.id])
        c = Client()
        login(c, u)
        data = c.get('/api/v3/oam/tools/?db_type=mysql').json()['data']
        self.assertTrue(data['mutating'], '变更类工具清单不得为空')
        for t in data['tools']:
            self.assertFalse(t['name'].startswith('fix.'),
                             '变更类工具不得进策略可调用的工具列表')

    def test_SIT_V3_23_every_readonly_tool_says_no_data_on_empty_snapshot(self):
        """空快照下，每个只读工具都必须返回 `NO_DATA`，不得返回"看起来成功"的空结果。

        设计 §4.1：*"要么返回格式良好的输出，要么返回确定性错误信号 ——
        绝不出现静默失败或看起来成功"*。一个恒真的空字典 `{'chains': [], 'total': 0}`
        属于"看起来成功"：上层会把它当成"查过了，确实没有"，审计也会据此
        认定主张有工具观测支撑 —— R25-02 的编造正是从这种缝里长出来的。
        """
        from monitor.oam.env import OamSnapshot, ReplayEnv

        start, end = _window()
        env = ReplayEnv(OamSnapshot(case_id='SIT-EMPTY', db_type='mysql',
                                    window=(start, end)))
        offenders = []
        for spec in env.catalog():
            name = spec['name']
            if name == 'kb.search':
                continue          # 知识库未接入时单独测（见 SIT-V3-08）
            res = env.call(name, {})
            if res.ok:
                offenders.append((name, res.data))
        self.assertFalse(offenders,
                         '以下工具在空快照下返回了成功而不是 NO_DATA：'
                         f'{[n for n, _ in offenders]}')
