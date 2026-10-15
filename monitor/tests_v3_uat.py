# -*- coding: utf-8 -*-
"""V3.0 OAM 用户验收测试（UAT, User Acceptance Test）。

## 与 SIT 的分工

| 层次 | 文件 | 谁在问 | 问什么 |
|------|------|--------|--------|
| 单元 | `tests_v3_oam.py` | 开发 | 模块自己的契约成立吗 |
| SIT | `tests_v3_sit.py` | 测试 | 模块串起来、按接口契约还成立吗 |
| **UAT** | 本文件 | **DBA / 质量负责人** | **按业务旅程走一遍，结果能不能签字** |

所以本文件的用例一律写成**业务旅程**（谁、要做什么、看到什么才算通过），
每条给出明确的验收判据，并在报告 `V3.0_SIT_UAT_TEST_REPORT.md` 里
逐条给出"通过 / 不通过 + 理由"。

## 角色

- `dba_a` / `dba_b`：两个 DBA，数据范围互不相交（验范围隔离用）；
- `quality`：质量负责人，有基准与知识库的查看/登记权限。

## 用例编号

`UAT-V3-xx`，与报告一一对应。
"""
import json
from datetime import timedelta

from django.test import Client, TestCase, TransactionTestCase, override_settings, tag
from django.utils import timezone

from monitor.auth import Perm, RoleCode
from monitor.models import (
    AuditLog, KbDocument, OamCase, OamRetrievalMiss, OamTrajectory,
)
from monitor.oam.verifier import STATUS_EVIDENCE_INSUFFICIENT
from monitor.tests_bugfix import login, make_db, make_user
from monitor.tests_v3_sit import _answer, _mkcase, _mksnapshot, _patch_policy, _tool


def _w(hours=2):
    end = timezone.now()
    return end - timedelta(hours=hours), end


# =============================================================================
# UAT-V3-01 知识入库 → 可检索（DBA 维护知识库）
# =============================================================================
@tag('integration')
class UatKbIngestion(TestCase):
    """旅程：DBA 把一份处理方案交给平台 → 平台分类、切分、抽取出
    "症状-成因-动作"记录 → 事后能被检索到，且不会串方言。"""

    # 平台推荐形态：症状/成因/动作已结构化的来源（RULES_V2 迁移即此形态）
    STRUCTURED = {
        'doc_id': 'D-MYSQL-SORT', 'title': 'MySQL 排序落盘处理方案',
        'version': 'v1', 'db_types': ['mysql'], 'category': 'fault_solution',
        'body': '',
        'records': [{
            'symptom': '查询耗时升高、临时表落盘',
            'causes': ['MISSING_INDEX'],
            'db_types': ['mysql'],
            'probe_actions': ['db.top_sql'],
            'fix_actions': ['fix.create_index'],
        }],
    }

    # 真实形态：DBA 手里现成的自然语言运维手册
    RUNBOOK = {
        'doc_id': 'D-MYSQL-RUNBOOK', 'title': 'MySQL 查询变慢处理手册',
        'version': 'v1', 'db_types': ['mysql'],
        'body': (
            '## 症状\n查询耗时升高，Sort_merge_passes 增长，临时表落盘。\n\n'
            '## 处理步骤\n1. 用 SHOW GLOBAL STATUS 查看 Sort_merge_passes。\n'
            '2. 调整 sort_buffer_size 到 4M。\n'
        ),
    }

    @staticmethod
    def _index(rec):
        from monitor.oam.kb import KbIndex
        idx = KbIndex()
        idx.add(rec)
        return idx.build()

    def test_UAT_V3_01_structured_doc_is_ingested_and_retrievable(self):
        from monitor.oam.kb import chunk, classify, extract

        doc = dict(self.STRUCTURED)
        doc['category'] = classify(doc)
        self.assertIn(doc['category'], ('config_manual', 'fault_solution',
                                        'best_practice', 'basic_fact'),
                      '分类必须落在设计的四类里')
        chunks = chunk(doc)
        self.assertTrue(chunks, '切分必须产出片段')
        for c in chunks:
            self.assertLessEqual(c.token_count, 1000, '每片段须 <1000 token')
        records = extract(doc, chunks)
        self.assertTrue(records, '抽取必须产出症状-成因-动作记录')
        rec = records[0]
        from monitor.oam.labels import is_valid_label
        for cause in rec.candidate_causes:
            self.assertTrue(is_valid_label(cause, 'mysql'),
                            f'成因 {cause} 必须落在该方言的封闭标签空间内')
        idx = self._index(rec)
        res = idx.search('查询耗时升高 临时表落盘', db_type='mysql', top_k=5)
        self.assertIn(rec.record_id_or_key, [h.record_id for h in res.hits],
                      '刚入库的知识应当能被同义查询检索到')

    def test_UAT_V3_01b_free_text_runbook_is_ingested_with_its_remediation_kept(self):
        """自然语言运维手册（处理步骤没写成工具名）必须能入库，且修法不能丢。

        这条用例原先断言的是"必须抛 `ValueError`"（当时记为遗留项 R-1：
        硬报错拦下的是**整份手册**，DBA 手里的真实文档一份都进不了库，
        而论文的 KB 本来就来自真实文档）。

        现在的判据比"报错可执行"更强，要求同时成立四件事：
          1. 不报错，抽出记录；
          2. 文档写的修法**原样保留**，没有被静默丢弃；
          3. 明确标成 `advisory`（可检索但待人工复核），且**不是**
             `needs_review` —— 后者会让记录在检索层被排除，等于假修复；
          4. 该记录对自己的方言可检索，对别的方言仍然不可见。
        """
        from monitor.oam.kb import chunk, classify, extract

        doc = dict(self.RUNBOOK)
        doc['category'] = classify(doc)
        records = extract(doc, chunk(doc))
        self.assertEqual(len(records), 1, '真实手册应当能抽出记录，而不是整份被拒')

        rec = records[0]
        self.assertFalse(rec.needs_review,
                         '方言已由 db_types 明确给出，不该按 needs_review 处置')
        self.assertTrue(rec.advisory, '绑不上工具的修法必须留痕待复核')
        self.assertTrue(rec.advisory_reason)
        self.assertTrue(rec.unbound_fix_actions,
                        '绑不上工具的修复动作必须被记录下来，不能消失')
        joined = ' '.join(rec.fix_actions)
        self.assertIn('sort_buffer_size', joined,
                      '手册里的真实修法必须原样保留，否则等于把知识扔了')
        self.assertIn('Sort_merge_passes', joined)

        # 可检索：advisory 记录仍要进检索上下文
        idx = self._index(rec)
        res = idx.search('查询变慢 sort_buffer_size', db_type='mysql', top_k=5)
        self.assertIn(rec.record_id_or_key, [h.record_id for h in res.hits],
                      'advisory 记录应可检索，否则又变成"入库成功但查不到"')
        # 方言隔离不受影响
        res_o = idx.search('查询变慢 sort_buffer_size', db_type='oracle', top_k=5)
        self.assertNotIn(rec.record_id_or_key, [h.record_id for h in res_o.hits],
                         'MySQL 的手册不得进入 Oracle 的上下文（REV-10）')

    def test_UAT_V3_01c_other_dialect_never_sees_it(self):
        """方言隔离：Oracle 事故的检索上下文里不得出现 MySQL 的方案。"""
        from monitor.oam.kb import chunk, classify, extract

        doc = dict(self.STRUCTURED)
        doc['category'] = classify(doc)
        rec = extract(doc, chunk(doc))[0]
        idx = self._index(rec)
        res = idx.search('查询耗时升高 临时表落盘', db_type='oracle', top_k=5)
        self.assertNotIn(rec.record_id_or_key, [h.record_id for h in res.hits],
                         'MySQL 的知识不得进入 Oracle 的诊断上下文（REV-10）')


# =============================================================================
# UAT-V3-02 基准用例登记（质量负责人）
# =============================================================================
@tag('integration')
class UatCaseRegistration(TestCase):
    """旅程：质量负责人从真实工单登记一条基准用例，填专家标注根因与修复规格。"""

    def setUp(self):
        self.db = make_db('uat-case-db', port=3611)
        self.quality = make_user('uat-quality', RoleCode.DBA,
                                 [Perm.ALERT_CONFIG_MANAGE, Perm.ALERT_CONFIG_VIEW,
                                  Perm.METRICS_VIEW],
                                 allowed_dbs=[self.db.id])
        self.client = Client()
        login(self.client, self.quality)

    def _post(self, payload):
        return self.client.post('/api/v3/oam/cases/', data=json.dumps(payload),
                                content_type='application/json')

    def test_UAT_V3_02_valid_case_is_registered(self):
        r = self._post({'case_id': 'UAT-C-1', 'db_type': 'mysql',
                        'gt_root_cause': 'MISSING_INDEX',
                        'category': 'tuning', 'difficulty': 'hard',
                        'symptom': '大表查询走全表扫描',
                        'fix_spec': {'params': {'index': 'idx_orders_cust'}},
                        'source_ticket': 'INC-2026-0001',
                        'is_deidentified': True,
                        'source_config_id': self.db.id, 'split': 'test'})
        self.assertEqual(r.status_code, 200, r.content[:300])
        case = OamCase.objects.get(case_id='UAT-C-1')
        self.assertTrue(case.source_ticket, '基准用例必须可溯源到工单')
        self.assertTrue(case.is_deidentified, '入库前必须去标识')

    def test_UAT_V3_02b_cross_dialect_label_is_refused(self):
        """Oracle 专有根因不得登记到 MySQL 用例上（标签空间按方言分表）。"""
        from monitor.oam.labels import oracle_only_labels
        oracle_label = sorted(oracle_only_labels())[0]
        r = self._post({'case_id': 'UAT-C-BAD', 'db_type': 'mysql',
                        'gt_root_cause': oracle_label})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json()['code'], 'INVALID_LABEL')
        self.assertFalse(OamCase.objects.filter(case_id='UAT-C-BAD').exists())

    def test_UAT_V3_02c_all_json_fields_filled_can_be_registered(self):
        """把 JSON 字段都填上时应当能登记 —— 用来隔离"默认值登记不了"的根因。

        若本用例通过而 `UAT-V3-02` 失败，说明问题不在权限/校验逻辑，
        而在 `OamCase` 的 JSONField 默认空值过不了 `full_clean`。
        """
        r = self._post({'case_id': 'UAT-C-2', 'db_type': 'mysql',
                        'gt_root_cause': 'MISSING_INDEX',
                        'symptom': '大表查询走全表扫描',
                        'fix_spec': {'params': {'index': 'idx_a'}},
                        'required_evidence': ['kb:1'],
                        'source_ticket': 'INC-2', 'is_deidentified': True})
        self.assertEqual(r.status_code, 200, r.content[:300])


# =============================================================================
# UAT-V3-03 冻结快照上传（窗口与点数闸门）
# =============================================================================
@tag('integration')
class UatSnapshotUpload(TestCase):
    """旅程：质量负责人把事发时段的遥测打包成冻结快照上传，供离线回放。"""

    def setUp(self):
        self.case = _mkcase('UAT-SNAP-1')
        self.quality = make_user('uat-snap', RoleCode.DBA,
                                 [Perm.ALERT_CONFIG_MANAGE])
        self.client = Client()
        login(self.client, self.quality)

    def test_UAT_V3_03_valid_snapshot_accepted_and_reported(self):
        start, end = _w(2)
        payload = {'window': [start.isoformat(), end.isoformat()],
                   'kpi_series': {'conn_usage_pct': [
                       [(start + timedelta(seconds=60 * i)).isoformat(), 20 + i % 5]
                       for i in range(60)]},
                   'session_samples': [{'sid': 1, 'blocking': 0}],
                   'config_meta': {'parameters': {'max_connections': '150'}}}
        r = self.client.post(f'/api/v3/oam/cases/{self.case.case_id}/snapshot/',
                             data=json.dumps(payload),
                             content_type='application/json')
        self.assertEqual(r.status_code, 200, r.content[:300])
        data = r.json()['data']
        self.assertIn('window_hours', data)
        self.assertEqual(data['series_lengths'].get('conn_usage_pct'), 60)

    def test_UAT_V3_03b_oversize_series_is_refused_not_truncated(self):
        """>500 点（论文：序列一长 KPI 检测 F1 就崩）必须明确拒绝。"""
        start, end = _w(2)
        payload = {'window': [start.isoformat(), end.isoformat()],
                   'kpi_series': {'conn': [
                       [(start + timedelta(seconds=i)).isoformat(), i % 50]
                       for i in range(600)]}}
        r = self.client.post(f'/api/v3/oam/cases/{self.case.case_id}/snapshot/',
                             data=json.dumps(payload),
                             content_type='application/json')
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json()['code'], 'SNAPSHOT_RANGE')


# =============================================================================
# UAT-V3-04 零遥测实例：如实说"不知道"，不编造
# =============================================================================
@tag('integration')
class UatEvidenceInsufficient(TransactionTestCase):
    """旅程：DBA 对一个刚纳管、还没有任何遥测的实例发起诊断。
    期望：平台明确告知"证据不足、已转人工"，**而不是**给一个听起来合理的根因。"""

    def setUp(self):
        self.db = make_db('uat-zero-db', port=3621)
        self.dba = make_user('uat-zero-dba', RoleCode.DBA,
                             [Perm.TICKETS_EXECUTE, Perm.ALERTS_VIEW],
                             allowed_dbs=[self.db.id])
        self.client = Client()
        login(self.client, self.dba)

    def test_UAT_V3_04_no_telemetry_means_no_fabricated_root_cause(self):
        outputs = [_tool('db.session_profile', {}),
                   _tool('db.lock_chain', {}),
                   _answer(root_cause='LOCK_WAIT', refs=['tool:db.lock_chain'])]
        with override_settings(OAM_ENABLED=True), _patch_policy(outputs):
            r = self.client.post('/api/v3/oam/diagnose/',
                                 data=json.dumps({'config_id': self.db.id,
                                                  'symptom': '业务反馈卡顿'}),
                                 content_type='application/json')
        self.assertEqual(r.status_code, 200, r.content[:300])
        report = r.json()['data'].get('report') or {}
        self.assertEqual(report.get('status'), STATUS_EVIDENCE_INSUFFICIENT,
                         '零遥测时不得给出可判定的根因')
        self.assertFalse(report.get('root_cause'))
        self.assertTrue(report.get('escalated'), '必须转人工')


# =============================================================================
# UAT-V3-05 无支撑结论被证据审计拦下
# =============================================================================
@tag('integration')
class UatEvidenceAudit(TestCase):
    """旅程：DBA 收到一份"根因是 XX"的报告，想知道这个结论有没有证据。
    期望：没有工具观测/KB 支撑的主张被明确标记并转人工。"""

    def test_UAT_V3_05_unsupported_claim_is_flagged(self):
        from monitor.oam.audit import audit
        from monitor.oam.types import Action, Observation, Step, Trajectory

        traj = Trajectory(case_id='UAT-AUDIT', db_type='mysql')
        traj.steps.append(Step(
            turn=1, thought='看看锁', action=Action(tool='db.lock_chain', params={}),
            observation=Observation(text='无阻塞链',
                                    evidence={'refs': ['tool:db.lock_chain']})))
        report = {'root_cause': 'MISSING_INDEX', 'fix': 'CREATE INDEX i ON t(c)',
                  'evidence_refs': ['tool:db.top_sql']}     # 引了没跑过的工具
        res = audit(traj, report)
        self.assertFalse(res.passed, '引用不存在的观测必须判不通过')
        self.assertTrue(res.route_to_human)
        self.assertEqual(res.unsupported[0].kind, 'root_cause')

    def test_UAT_V3_05b_supported_claim_passes(self):
        from monitor.oam.audit import audit
        from monitor.oam.types import Action, Observation, Step, Trajectory

        traj = Trajectory(case_id='UAT-AUDIT2', db_type='mysql')
        traj.steps.append(Step(
            turn=1, thought='看看锁', action=Action(tool='db.lock_chain', params={}),
            observation=Observation(text='阻塞链 3 层',
                                    evidence={'refs': ['tool:db.lock_chain']})))
        report = {'root_cause': 'LOCK_WAIT', 'fix': 'kill 长事务',
                  'evidence_refs': ['tool:db.lock_chain']}
        res = audit(traj, report)
        self.assertTrue(res.passed)
        self.assertFalse(res.route_to_human)

    def test_UAT_V3_05c_declared_insufficient_but_still_gives_cause_is_fraud(self):
        """"声明证据不足"却顺手塞了根因 = 编造，审计必须拦。"""
        from monitor.oam.audit import audit
        from monitor.oam.types import Trajectory

        traj = Trajectory(case_id='UAT-AUDIT3', db_type='mysql')
        report = {'status': STATUS_EVIDENCE_INSUFFICIENT, 'root_cause': 'LOCK_WAIT'}
        res = audit(traj, report)
        self.assertFalse(res.passed)
        self.assertTrue(res.route_to_human)


# =============================================================================
# UAT-V3-06 诊断只给建议，变更必须走审批链
# =============================================================================
@tag('integration')
class UatNoAutonomousChange(TransactionTestCase):
    """旅程：DBA 让平台诊断一个锁等待事故。期望：平台给出建议与风险标记，
    **不**擅自在生产库上 KILL 会话；真要执行必须走既有审批链。"""

    def setUp(self):
        self.db = make_db('uat-nofix-db', port=3631)
        self.dba = make_user('uat-nofix-dba', RoleCode.DBA,
                             [Perm.TICKETS_EXECUTE, Perm.ALERTS_VIEW],
                             allowed_dbs=[self.db.id])
        self.client = Client()
        login(self.client, self.dba)

    def test_UAT_V3_06_agent_cannot_execute_and_must_route_to_approval(self):
        api_types = ('API_CREATE', 'API_UPDATE', 'API_DELETE', 'API_UNKNOWN')
        before = AuditLog.objects.exclude(action_type__in=api_types).count()
        outputs = [_tool('fix.kill_session', {'sid': 42}),
                   _answer(status=STATUS_EVIDENCE_INSUFFICIENT)]
        with override_settings(OAM_ENABLED=True), _patch_policy(outputs):
            r = self.client.post('/api/v3/oam/diagnose/',
                                 data=json.dumps({'config_id': self.db.id,
                                                  'symptom': '锁等待'}),
                                 content_type='application/json')
        self.assertEqual(r.status_code, 200, r.content[:300])
        report = r.json()['data'].get('report') or {}
        fix = report.get('recommended_fix') or {}
        self.assertTrue(fix.get('requires_approval'),
                        '修复建议必须显式标注"需人工审批"')
        self.assertEqual(fix.get('execute_via'),
                         '/api/v2/playbooks/execute-safely/',
                         '执行入口必须指向既有审批链')
        after = AuditLog.objects.exclude(action_type__in=api_types).count()
        self.assertEqual(after, before, '诊断过程不得产生任何执行工单')

    def test_UAT_V3_06b_mutating_tool_is_refused_in_the_trace(self):
        from monitor.oam.bench.runner import run_case
        case = _mkcase('UAT-MUT-1', source_config=self.db)
        outputs = [_tool('fix.kill_session', {'sid': 42}),
                   _answer(status=STATUS_EVIDENCE_INSUFFICIENT)]
        with _patch_policy(outputs):
            traj, _v, _a, _env = run_case(case, persist=False)
        codes = [s.tool_error_code for s in traj.steps]
        self.assertIn('MUTATION_FORBIDDEN', codes,
                      '变更类工具必须返回 MUTATION_FORBIDDEN 并留痕')


# =============================================================================
# UAT-V3-07 基准成绩单（质量负责人签字依据）
# =============================================================================
@tag('integration')
class UatBenchScorecard(TestCase):
    """旅程：质量负责人跑一轮基准，需要一份能签字的成绩单：
    按类别宏平均的成功率 + 幻觉率 + 覆盖度，且三者必须自洽。"""

    def setUp(self):
        self.quality = make_user('uat-bench', RoleCode.DBA,
                                 [Perm.ALERT_CONFIG_MANAGE, Perm.METRICS_VIEW])
        self.client = Client()
        login(self.client, self.quality)
        for i, (cause, cat) in enumerate([('LOCK_WAIT', 'tuning'),
                                          ('MISSING_INDEX', 'tuning'),
                                          ('CONN_EXHAUSTED', 'overload')], start=1):
            c = _mkcase(f'UAT-BENCH-{i}', gt=cause, category=cat)
            _mksnapshot(c, points=30)

    def _run(self):
        with override_settings(OAM_ENABLED=True), \
                _patch_policy([_answer(status=STATUS_EVIDENCE_INSUFFICIENT)]):
            return self.client.post('/api/v3/oam/bench/run/',
                                    data=json.dumps({'split': 'test',
                                                     'policy_backend': 'prompted'}),
                                    content_type='application/json')

    def test_UAT_V3_07_scorecard_has_score_hallucination_and_coverage(self):
        r = self._run()
        self.assertEqual(r.status_code, 200, r.content[:300])
        d = r.json()['data']
        for key in ('success_rate', 'success_rate_weighted', 'hallucination_rate',
                    'per_category', 'coverage', 'avg_turns'):
            self.assertIn(key, d, f'成绩单缺字段 {key}')
        self.assertEqual(set(d['per_category']), {'tuning', 'overload'},
                         'per_category 必须按类别给出')

    def test_UAT_V3_07b_coverage_must_not_contradict_itself(self):
        """签字的成绩单不能自相矛盾：同一条根因不能既"已覆盖"又"未覆盖"。"""
        r = self._run()
        cov = r.json()['data']['coverage']
        overlap = set(cov['root_causes_covered']) & set(cov['root_causes_missing'])
        self.assertFalse(overlap,
                         f'覆盖度自相矛盾：{sorted(overlap)} 同时出现在'
                         f' covered 与 missing 里')

    def test_UAT_V3_07c_missing_coverage_is_explicitly_warned(self):
        """覆盖度有缺口时，报告必须显式写明"该成绩不覆盖以下根因"。"""
        r = self._run()
        run_id = None
        from monitor.models import OamBenchRun
        latest = OamBenchRun.objects.order_by('-finished_at').first()
        self.assertIsNotNone(latest)
        run_id = latest.run_id
        r2 = self.client.get(f'/api/v3/oam/bench/report/{run_id}/')
        self.assertEqual(r2.status_code, 200, r2.content[:300])
        cov = r2.json()['data']['coverage']
        if cov.get('root_causes_missing'):
            self.assertIn('warning', cov,
                          'root_causes_missing 非空时必须显式标注不覆盖哪些根因')
            self.assertIn('不覆盖', cov['warning'])
        else:
            self.assertTrue(cov.get('root_causes_covered'))

    def test_UAT_V3_07d_success_rate_is_macro_averaged(self):
        """成功率必须是"按类别宏平均后再汇总"，不是按用例数加权。"""
        from monitor.models import OamBenchRun, OamTrajectory
        r = self._run()
        d = r.json()['data']
        per_cat = d['per_category']
        macro = round(sum(per_cat.values()) / len(per_cat), 4)
        self.assertAlmostEqual(d['success_rate'], macro, places=4,
                               msg='success_rate 不是 per_category 的宏平均')


# =============================================================================
# UAT-V3-08 方言隔离（MySQL 事故不出现 Oracle 专有内容）
# =============================================================================
@tag('integration')
class UatDialectIsolation(TestCase):
    """旅程：DBA 排查 MySQL 事故时，工具目录里不能出现 Oracle 专有视图/等待事件。"""

    def setUp(self):
        self.db = make_db('uat-dialect-db', port=3641)
        self.dba = make_user('uat-dialect', RoleCode.DBA, [Perm.METRICS_VIEW],
                             allowed_dbs=[self.db.id])
        self.client = Client()
        login(self.client, self.dba)

    def test_UAT_V3_08_mysql_catalog_has_no_oracle_terms(self):
        data = self.client.get('/api/v3/oam/tools/?db_type=mysql').json()['data']
        blob = json.dumps(data, ensure_ascii=False)
        for term in ('V$SESSION', 'V$SESSIONS_ACTIVITY', 'enq: TX',
                     'DBMS_XPLAN', 'PGA_AGGREGATE_TARGET'):
            self.assertNotIn(term, blob,
                             f'MySQL 的工具目录里不得出现 Oracle 专有词 {term}')

    def test_UAT_V3_08b_oracle_catalog_does_have_them(self):
        data = self.client.get('/api/v3/oam/tools/?db_type=oracle').json()['data']
        blob = json.dumps(data, ensure_ascii=False)
        self.assertTrue(any(t in blob for t in ('V$SESSION', 'V$SESSIONS_ACTIVITY')),
                        'Oracle 目录必须给出 Oracle 的取值来源，否则语义增强没做')


# =============================================================================
# UAT-V3-09 数据范围隔离（两个 DBA 互不可见）
# =============================================================================
@tag('integration')
class UatScopeIsolation(TestCase):
    """旅程：A 组 DBA 登录后，看不到 B 组实例的任何诊断轨迹。"""

    def setUp(self):
        self.a_db = make_db('uat-a-db', port=3651)
        self.b_db = make_db('uat-b-db', port=3652)
        self.dba_a = make_user('uat-dba-a', RoleCode.DBA,
                               [Perm.TICKETS_EXECUTE, Perm.ALERTS_VIEW],
                               allowed_dbs=[self.a_db.id])
        self.dba_b = make_user('uat-dba-b', RoleCode.DBA,
                               [Perm.TICKETS_EXECUTE, Perm.ALERTS_VIEW],
                               allowed_dbs=[self.b_db.id])
        case_b = _mkcase('UAT-B-CASE', source_config=self.b_db)
        self.traj_b = OamTrajectory.objects.create(
            traj_id='UAT-B-TRAJ', case=case_b, config=self.b_db,
            policy_backend='prompted', policy_version='v1',
            report={'root_cause': 'LOCK_WAIT'}, turns=1)

    def test_UAT_V3_09_b_trajectory_is_invisible_to_a(self):
        c = Client()
        login(c, self.dba_a)
        for url in (f'/api/v3/oam/trajectories/{self.traj_b.traj_id}/',
                    f'/api/v3/oam/trajectories/{self.traj_b.traj_id}/audit/'):
            r = c.get(url)
            self.assertEqual(r.status_code, 404, f'{url} 泄露了范围外实例的数据')
            self.assertNotIn('UAT-B-CASE', r.content.decode('utf-8'))

    def test_UAT_V3_09b_owner_can_see_own_trajectory(self):
        c = Client()
        login(c, self.dba_b)
        r = c.get(f'/api/v3/oam/trajectories/{self.traj_b.traj_id}/')
        self.assertEqual(r.status_code, 200, '范围内的实例必须可读')


# =============================================================================
# UAT-V3-10 知识库新鲜度与检索未命中可观测
# =============================================================================
@tag('integration')
class UatKbFreshness(TestCase):
    """旅程：知识管理员每月看一眼 KB 新鲜度看板，
    知道哪些文档过期、哪些类目检索老是不命中。"""

    def setUp(self):
        self.admin = make_user('uat-kb-admin', RoleCode.DBA,
                               [Perm.ALERT_CONFIG_VIEW])
        self.client = Client()
        login(self.client, self.admin)

    def test_UAT_V3_10_stale_documents_are_listed(self):
        from django.utils import timezone as tz
        doc = KbDocument.objects.create(
            doc_id='UAT-KB-OLD', category='config_manual', db_types=['mysql'],
            title='过期的参数手册', body='x', version='v1', approved_by='old',
            reviewed_at=tz.now() - timedelta(days=200))
        r = self.client.get('/api/v3/oam/kb/staleness/')
        self.assertEqual(r.status_code, 200, r.content[:300])
        data = r.json()['data']
        self.assertGreaterEqual(data['threshold_days'], 1)
        ids = [s['doc_id'] for s in data['stale_items']]
        self.assertIn(doc.doc_id, ids, '超过阈值未复核的文档必须出现在过期清单里')

    def test_UAT_V3_10b_retrieval_misses_are_observable(self):
        OamRetrievalMiss.objects.create(
            trajectory_id='UAT-TRJ', kb_category='fault_solution',
            query_text='网络抖动导致的连接重置', db_type='mysql',
            top_similarity=0.01)
        r = self.client.get('/api/v3/oam/kb/staleness/')
        data = r.json()['data']
        self.assertGreaterEqual(data['retrieval_miss_total'], 1,
                                '检索未命中必须可观测（论文：占总失败 12%）')

    def test_UAT_V3_10c_kb_write_is_gated_by_review(self):
        """知识写入必须经"人工复核 + 去标识 + 版本审批"闸门，不能从 HTTP 直接写。"""
        c = Client()
        login(c, make_user('uat-kb-w', RoleCode.DBA,
                           [Perm.ALERT_CONFIG_VIEW, Perm.ALERT_CONFIG_MANAGE]))
        r = c.post('/api/v3/oam/kb/documents/',
                   data=json.dumps({'doc_id': 'X', 'title': 'x', 'body': 'x'}),
                   content_type='application/json')
        self.assertIn(r.status_code, (501, 403),
                      'KB 写入必须被审批闸门挡住（P0 未开放直接写入）')
        self.assertFalse(KbDocument.objects.filter(doc_id='X').exists())


# =============================================================================
# UAT-V3-11 可复现：同用例同策略重跑结果一致
# =============================================================================
@tag('integration')
class UatReproducibility(TestCase):
    """旅程：质量负责人复核某条战绩时重跑同一用例，结论必须一致。"""

    def test_UAT_V3_11_same_case_same_policy_same_verdict(self):
        from monitor.oam.bench.runner import run_case

        case = _mkcase('UAT-REPRO-1', fix_spec={'params': {'lock_timeout': '50'}})
        _mksnapshot(case, points=30)
        outputs = [_tool('db.lock_chain', {}),
                   _answer(root_cause='LOCK_WAIT', fix={'lock_timeout': '50'},
                           refs=['tool:db.lock_chain'])]
        results = []
        for _ in range(2):
            with _patch_policy(outputs):
                traj, verdict, audit_res, _env = run_case(case, persist=False)
            results.append((verdict.passed, verdict.reason,
                            traj.report.get('root_cause'), audit_res.passed))
        self.assertEqual(results[0], results[1],
                         f'同用例同策略重跑结论不一致：{results}')
        self.assertTrue(results[0][0], '根因与证据都对的用例应当判通过')
