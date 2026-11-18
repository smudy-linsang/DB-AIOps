import json
from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase, tag
from django.utils import timezone

from monitor.alert_manager import AlertManager
from monitor.auth import Perm
from monitor.llm.brain import merge_llm_with_rules
from monitor.llm.evidence import build_evidence_pack
from monitor.models import AlertLog, AuditLog, ChangeEvent, Event, MonitorLog, Playbook, PlaybookRun
from monitor.tests_bugfix import login, make_db, make_user
from monitor.tests_operations_experience import make_incident
from monitor.verify_loop import VerificationEvidence, read_observation


@tag('unit')
class OperationsReliabilityTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.db = make_db('ops-reliability', db_type='pgsql')
        cls.other = make_db('ops-other')
        cls.operator = make_user(
            'ops-operator', 'ops_operator', [
                Perm.ALERTS_VIEW, Perm.ALERTS_ACKNOWLEDGE, Perm.DATABASE_DETAIL_VIEW,
                Perm.TICKETS_VIEW, Perm.METRICS_VIEW,
            ], [cls.db.pk],
        )
        cls.reader = make_user('ops-reader', 'ops_reader', [Perm.ALERTS_VIEW], [cls.db.pk])
        cls.incident = make_incident(cls.db, 'OPS-RELIABILITY')
        cls.url = '/api/v1/incidents/OPS-RELIABILITY/'

    def setUp(self):
        login(self.client, self.operator)

    def test_acknowledgement_survives_restoration_and_deduplication(self):
        manager = AlertManager(self.db)
        with patch.object(manager, '_send_notification'), patch.object(manager, '_send_to_channels'):
            alert = manager.fire('connection', 'conn_usage_pct', 'Connections high', 'Over threshold')
            url = f'/api/v1/alerts/{alert.id}/acknowledge/'
            receipt = self.client.post(url).json()
            self.assertEqual(receipt['monitoring_state'], 'ongoing')
            self.assertEqual(receipt['acknowledged_by'], self.operator.username)
            self.assertEqual(self.client.post(url).json(), receipt)
            self.assertEqual(manager.fire('connection', 'conn_usage_pct', 'Still high', 'Over threshold').pk, alert.pk)
            self.assertEqual(self.client.get('/api/v1/alerts/', {'status': 'ongoing'}).json()['alerts'][0]['id'], alert.pk)
            manager.resolve('connection', 'conn_usage_pct', evidence={'value': 0, 'threshold': 80})
            alert.refresh_from_db()
            self.assertEqual((alert.status, alert.acknowledged_by, alert.recovery_evidence['value']),
                             ('resolved', self.operator.username, 0))
            self.assertIsNotNone(alert.acknowledged_at)
            self.assertIsNotNone(alert.resolved_at)
            new = manager.fire('connection', 'conn_usage_pct', 'Again', 'Over threshold')
            self.assertNotEqual(new.pk, alert.pk)

    def test_missing_metric_does_not_resolve_acknowledged_signal(self):
        from monitor.management.commands.start_monitor import Command
        runner = Command()
        with patch.object(AlertManager, '_send_notification'), patch.object(AlertManager, '_send_to_channels'), \
             patch.object(runner, '_collect_perf_extras'), patch.object(runner, '_emit_phase6_events'), \
             patch('monitor.management.commands.start_monitor.ENABLE_PHASE2_ENGINES', False):
            runner.process_result(self.db, 'UP', {'conn_usage_pct': 100})
            alert = AlertLog.objects.get(config=self.db, alert_type='connection')
            self.client.post(f'/api/v1/alerts/{alert.id}/acknowledge/')
            runner.process_result(self.db, 'UP', {})
            alert.refresh_from_db()
            self.assertEqual(alert.status, 'acknowledged')
            runner.process_result(self.db, 'UP', {'conn_usage_pct': 0})
        alert.refresh_from_db()
        self.assertEqual(alert.status, 'resolved')
        self.assertEqual(alert.recovery_evidence['value'], 0)

    def test_collection_health_uses_last_success_and_explicit_missing_fields(self):
        good = MonitorLog.objects.create(config=self.db, status='UP', message='{"active_connections":0}')
        MonitorLog.objects.filter(pk=good.pk).update(create_time=timezone.now() - timedelta(hours=1))
        url = f'/api/v1/databases/{self.db.pk}/collection-health/'
        stale = self.client.get(url).json()
        self.assertEqual(stale['state'], 'stale')
        self.assertEqual(stale['missing_metrics'], ['conn_usage_pct'])
        MonitorLog.objects.create(config=self.db, status='DOWN', message='{"status":"missing_driver"}')
        failed = self.client.get(url).json()
        self.assertEqual((failed['state'], failed['failure_reason']), ('failed', 'missing_driver'))
        self.assertEqual(failed['last_success_at'], stale['last_success_at'])
        self.assertEqual(self.client.get(f'/api/v1/databases/{self.other.pk}/collection-health/').status_code, 404)

    def test_context_and_ticket_linking_respect_incident_scope_and_permissions(self):
        for db, label in ((self.db, 'visible'), (self.other, 'hidden')):
            ChangeEvent.objects.create(config=db, source='manual', title=label, dedup_key=label,
                                       occurred_at=timezone.now())
        ticket = AuditLog.objects.create(config=self.db, action_type='manual_sql',
                                         description='follow-up', sql_command='', status='pending')
        other = AuditLog.objects.create(config=self.other, action_type='manual_sql',
                                        description='outside', sql_command='', status='pending')
        self.assertEqual(self.client.post(self.url + 'tickets/', {'ticket_id': other.pk},
                                          content_type='application/json').status_code, 404)
        self.assertEqual(self.client.post(self.url + 'tickets/', {'ticket_id': ticket.pk},
                                          content_type='application/json').status_code, 200)
        context = self.client.get(self.url + 'context/').json()
        self.assertEqual([c['title'] for c in context['changes']], ['visible'])
        self.assertEqual([t['id'] for t in context['tickets']], [ticket.pk])
        login(self.client, self.reader)
        scoped = self.client.get(self.url + 'context/').json()
        self.assertIsNone(scoped['tickets'])
        self.assertIsNone(scoped['metrics'])
        self.assertEqual(self.client.post(self.url + 'tickets/', {'ticket_id': ticket.pk},
                                          content_type='application/json').status_code, 403)

    def test_handoff_is_persistent_and_stale_writes_are_rejected(self):
        body = {'owner': self.reader.username, 'next_step': 'Review evidence', 'version': None}
        response = self.client.post(self.url + 'handoff/', body, content_type='application/json')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.get(self.url + 'context/').json()['handoff']['owner'], self.reader.username)
        self.assertEqual(self.client.post(self.url + 'handoff/', body,
                                          content_type='application/json').status_code, 409)
        body.update(owner='missing-operator', version=response.json()['version'])
        self.assertEqual(self.client.post(self.url + 'handoff/', body,
                                          content_type='application/json').status_code, 400)

    def test_latest_post_recovery_sample_can_show_recurrence(self):
        now = timezone.now()
        pb = Playbook.objects.create(playbook_id='PB-UX', name='Validate metric', category='availability', signal='instance_down')
        run = PlaybookRun.objects.create(
            incident=self.incident, playbook=pb, status='succeeded', run_id='RUN-UX',
            started_at=now-timedelta(minutes=5), finished_at=now-timedelta(minutes=1),
            verify_result={'recovered': True, 'metric': 'conn_usage_pct', 'predicate': '<= 60',
                           'source': 'collector', 'state': 'verified'},
        )
        MonitorLog.objects.create(config=self.db, status='UP', message='{"conn_usage_pct": 90}')
        state = self.client.get(self.url + 'context/').json()['runs'][0]
        self.assertEqual(state['id'], run.run_id)
        self.assertEqual(state['followup']['value'], 90)
        self.assertFalse(state['followup']['passes'])

    def test_evidence_pack_has_real_source_time_and_keeps_rule_references(self):
        event = Event.objects.create(config=self.db, incident=self.incident, event_uid='ops-evidence',
                                     db_type=self.db.db_type, signal='connection', metric_key='conn_usage_pct',
                                     value=0, threshold=80, source='collector', occurred_at=timezone.now())
        pack = build_evidence_pack(self.incident, {}, {'conn_usage_pct': 0}, [], [])
        item = next(r for r in pack if r['type'] == 'event')
        self.assertEqual(item['observed_at'], event.occurred_at.isoformat())
        self.assertEqual(item['focus'], 'event-ops-evidence')
        self.assertEqual(item['preview']['value'], 0)
        merged = merge_llm_with_rules(
            [{'rule_id': 'R01', 'confidence': .7}],
            {'hypotheses': [{'rule_id': 'R01', 'verdict_on_rule': 'agree', 'evidence_refs': [item['id']]}]},
        )
        self.assertEqual(merged[0]['evidence_refs'], [item['id']])

    def test_failed_snapshot_is_not_a_zero_metric(self):
        log = MonitorLog.objects.create(config=self.db, status='DOWN', message='{"conn_usage_pct":0}')
        value, at = read_observation(self.incident, 'conn_usage_pct', 'collector', None,
                                     timezone.now() - timedelta(minutes=1))
        self.assertIsNone(value)
        self.assertEqual(at, log.create_time)

    def test_oam_persisted_observation_is_auditable(self):
        from monitor.models import OamCase, OamStep, OamTrajectory
        case = OamCase.objects.create(case_id='ops-oam', db_type='pgsql', source_config=self.db, category='inspection', difficulty='easy')
        trajectory = OamTrajectory.objects.create(
            traj_id='ops-trace', case=case, config=self.db, policy_backend='prompted', policy_version='test',
            report={'root_cause': 'connection', 'evidence_refs': ['metric:conn']},
        )
        OamStep.objects.create(
            trajectory=trajectory, turn=11, tool_name='db.metrics', observation='connection measurement',
            observation_evidence={'refs': ['metric:conn']},
        )
        response = self.client.get('/api/v3/oam/trajectories/ops-trace/')
        result = response.json()['data']['report']['evidence_index']
        self.assertEqual(result[0]['turn'], 11)
        audited = self.client.get('/api/v3/oam/trajectories/ops-trace/audit/')
        self.assertEqual(audited.status_code, 200)
        self.assertTrue(audited.json()['data']['audit']['passed'])


@tag('unit')
class VerificationEvidenceTests(TestCase):
    def test_duplicate_missing_and_recurrence_are_distinct(self):
        start = timezone.now()
        proof = VerificationEvidence('connections', '<= 2', 'collector', start, 60, 2, 30)
        self.assertFalse(proof.observe(0, start, start))
        self.assertFalse(proof.observe(0, start, start + timedelta(seconds=1)))
        self.assertEqual(proof.report['sample_count'], 1)
        at = start + timedelta(seconds=2)
        self.assertFalse(proof.observe(3, at, at))
        self.assertEqual(proof.report['recurrences'], 1)
        self.assertFalse(proof.observe(None, None, at))
        self.assertEqual(proof.result(at, False)['state'], 'unstable')
        at += timedelta(seconds=1)
        self.assertFalse(proof.observe(2, at, at))
        at += timedelta(seconds=1)
        self.assertTrue(proof.observe(2, at, at))
        self.assertEqual(proof.result(at, True)['state'], 'verified')

    def test_timeout_with_no_evidence_does_not_report_non_recovery(self):
        now = timezone.now()
        proof = VerificationEvidence('blocked_sessions', '== 0', 'ash', now, 20, 2, 10)
        proof.observe(None, None, now)
        result = proof.result(now, False)
        self.assertEqual(result['state'], 'insufficient_evidence')
        self.assertIsNone(result['recovered'])
