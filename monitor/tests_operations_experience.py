"""Queue contracts and incident handoff behavior through Django's HTTP layer."""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from unittest.mock import patch

from django.db import connections
from django.test import Client, TestCase, TransactionTestCase, tag
from django.utils import timezone

from monitor.auth import Perm
from monitor.models import AlertLog, AuditLog, Incident
from monitor.tests_bugfix import login, make_db, make_user

QUEUE = '/api/v1/work-queue/'


def make_incident(db, code, priority='P2', status='open', **extra):
    return Incident.objects.create(
        config=db, incident_id=code, db_type=db.db_type, category='availability',
        title=f'Triage {code}', priority=priority, status=status, dedup_key=code,
        occurred_at=timezone.now(), **extra,
    )


@tag('unit')
class OperationsExperienceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.mine = make_db('On-call north')
        cls.hidden = make_db('On-call south')
        cls.operator = make_user('queue-operator', 'queue_operator',
                                 [Perm.ALERTS_VIEW, Perm.ALERTS_ACKNOWLEDGE, Perm.DASHBOARD_VIEW], [cls.mine.pk])
        cls.reader = make_user('queue-reader', 'queue_reader', [Perm.ALERTS_VIEW], [cls.mine.pk])
        cls.no_permission = make_user('queue-zero', 'queue_no_permission', [], [cls.mine.pk])

    def setUp(self):
        login(self.client, self.operator)

    def test_scope_stage_deterministic_pagination_and_search(self):
        oldest = make_incident(self.mine, 'OLD-P1', 'P1')
        Incident.objects.filter(pk=oldest.pk).update(occurred_at=timezone.now() - timedelta(days=2))
        for i in range(22):
            make_incident(self.mine, f'NORTH-{i:02}')
        make_incident(self.mine, 'LEGACY', acked_by='previous-operator')
        make_incident(self.mine, 'CLOSED', status='closed')
        make_incident(self.hidden, 'SOUTH-P1', 'P1')
        page1 = self.client.get(QUEUE).json()
        self.assertEqual(page1['counts'], {'pending': 23, 'acknowledged': 1, 'finished': 1, 'all': 25})
        self.assertEqual(page1['items'][0]['id'], 'OLD-P1')
        page2 = self.client.get(QUEUE, {'page': 500}).json()
        self.assertEqual((page2['page'], len(page2['items'])), (2, 3))
        self.assertTrue(set(row['id'] for row in page1['items']).isdisjoint(row['id'] for row in page2['items']))
        self.assertEqual(self.client.get(QUEUE, {'q': 'south'}).json()['total'], 0)
        self.assertEqual(self.client.get(QUEUE, {'q': 'north-01'}).json()['items'][0]['id'], 'NORTH-01')
        self.assertEqual(self.client.get(QUEUE, {'stage': 'acknowledged'}).json()['items'][0]['id'], 'LEGACY')

    def test_alert_details_and_scope(self):
        for db, status in [(self.mine, 'acknowledged'), (self.hidden, 'active')]:
            AlertLog.objects.create(config=db, title='Capacity', status=status,
                                    description='Check evidence', severity='critical')
        payload = self.client.get(QUEUE, {'kind': 'alerts', 'stage': 'all'}).json()
        self.assertEqual(payload['counts'], {'pending': 0, 'acknowledged': 1, 'finished': 0, 'all': 1})
        self.assertEqual(payload['items'][0]['description'], 'Check evidence')
        self.assertIsNone(payload['items'][0]['resolved_at'])
        self.assertEqual(payload['source'], 'business_database')

    def test_permissions_validation_and_missing_incident(self):
        login(self.client, self.no_permission)
        self.assertEqual(self.client.get(QUEUE).status_code, 403)
        login(self.client, self.reader)
        self.assertEqual(self.client.get(QUEUE).status_code, 200)
        self.assertEqual(self.client.post('/api/v1/incidents/nothing/ack/').status_code, 403)
        for query in ({'kind': 'bad'}, {'stage': 'bad'}, {'page': '0'}, {'page': '²'}, {'q': 'a' * 101}):
            with self.subTest(query=query):
                self.assertEqual(self.client.get(QUEUE, query).status_code, 400)
        login(self.client, self.operator)
        make_incident(self.hidden, 'PRIVATE')
        self.assertEqual(self.client.post('/api/v1/incidents/PRIVATE/ack/').status_code, 404)

    def test_ack_receipt_and_close_reason(self):
        incident = make_incident(self.mine, 'RECEIPT')
        first = self.client.post('/api/v1/incidents/RECEIPT/ack/').json()
        another = make_user('queue-other', 'queue_operator', allowed_dbs=[self.mine.pk])
        login(self.client, another)
        self.assertEqual(self.client.post('/api/v1/incidents/RECEIPT/ack/').json(), first)
        self.assertEqual(first['acked_by'], self.operator.username)
        close_path = '/api/v1/incidents/RECEIPT/close/'
        for reason in ('  ', None, 'x' * 301):
            self.assertEqual(self.client.post(close_path, {'reason': reason}, content_type='application/json').status_code, 400)
        with patch('monitor.tasks_phase8.dispatch_distill_incident'):
            self.assertEqual(self.client.post(close_path, {'reason': '已验证恢复'}, content_type='application/json').status_code, 200)
        incident.refresh_from_db()
        self.assertEqual(incident.status, 'closed')
        self.assertTrue(AuditLog.objects.filter(action_type='incident_transition', description__contains='已验证恢复').exists())
        self.assertEqual(self.client.post('/api/v1/incidents/RECEIPT/ack/').status_code, 409)

    def test_timeline_failure_is_explicit(self):
        make_incident(self.mine, 'TIMELINE')
        with patch('monitor.models.AuditLog.objects.filter', side_effect=RuntimeError('audit unavailable')):
            result = self.client.get('/api/v1/incidents/TIMELINE/timeline/')
        self.assertEqual(result.status_code, 503)
        self.assertEqual(result.json()['code'], 'TIMELINE_UNAVAILABLE')

    def test_health_summary_explains_missing_workers_and_dependency_failure(self):
        with patch('monitor.healthcheck.PlatformHealthCheckView._check_redis', return_value={'status': 'error'}):
            response = self.client.get('/api/v1/system/health')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['status'], 'degraded')
        self.assertIn('dependency', response.json()['reasons'])
        self.assertIn('unreported_component', response.json()['reasons'])


@tag('integration')
class ConcurrentIncidentReceiptTests(TransactionTestCase):
    def test_two_operators_receive_the_same_first_receipt(self):
        db = make_db('receipt-db')
        make_incident(db, 'CONCURRENT')
        users = [
            make_user(f'receipt-{i}', 'receipt_role', [Perm.ALERTS_ACKNOWLEDGE], [db.pk])
            for i in range(2)
        ]
        clients = [Client(), Client()]
        for client, user in zip(clients, users):
            login(client, user)
        gate = Barrier(2, timeout=10)

        def acknowledge(client):
            try:
                gate.wait()
                result = client.post('/api/v1/incidents/CONCURRENT/ack/')
                return result.status_code, result.json()
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as executor:
            result = list(executor.map(acknowledge, clients))
        self.assertEqual([code for code, _ in result], [200, 200])
        self.assertEqual(result[0][1], result[1][1])
        self.assertIn(result[0][1]['acked_by'], [u.username for u in users])
