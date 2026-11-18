"""体验真实性：范围隔离、今日评分、趋势窗口与降级证据。"""
import json
from datetime import timedelta
from unittest.mock import patch
from django.test import TestCase, tag
from django.utils import timezone
from monitor.auth import Perm, RoleCode
from monitor.models import MonitorLog, HealthScore, AlertLog
from monitor.tests_bugfix import make_db, make_user, login


@tag('unit')
class ExperienceContractTests(TestCase):
    def setUp(self):
        self.mine = make_db('ux-mine')
        self.hidden = make_db('ux-hidden')
        self.user = make_user('ux-scoped', RoleCode.DBA, [Perm.METRICS_VIEW], [self.mine.id])
        login(self.client, self.user)
        for db, qps in [(self.mine, 12), (self.hidden, 9000)]:
            MonitorLog.objects.create(config=db, status='UP', message=json.dumps({'qps': qps}))
            HealthScore.objects.create(config=db, score_date=timezone.localdate(), total_score=qps if qps == 12 else 100)
            AlertLog.objects.create(config=db, title='test', status='active', severity='warning')

    def test_stats_scope_and_today(self):
        HealthScore.objects.create(config=self.mine, score_date=timezone.localdate()-timedelta(days=1), total_score=100)
        data = self.client.get('/api/v1/dashboard/stats/').json()
        self.assertEqual((data['total'], data['avg_health'], data['active_alerts'], data['scored_count']), (1, 12, 1, 1))
        self.assertEqual(sum(data[k] for k in ['online', 'offline', 'degraded', 'unknown']), data['total'])

    def test_unknown_and_degraded_are_not_offline(self):
        MonitorLog.objects.create(config=self.mine, status='DEGRADED', message='{}')
        data = self.client.get('/api/v1/dashboard/stats/').json()
        self.assertEqual((data['degraded'], data['offline']), (1, 0))

    def test_orm_trend_scope_and_metadata(self):
        response = self.client.get('/api/v1/dashboard/charts/?hours=1')
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data['trend'][0]['qps'], 12)
        self.assertEqual((data['hours'], data['source']), (1, 'orm'))

    def test_window_filters_old_samples(self):
        log = MonitorLog.objects.create(config=self.mine, status='UP', message='{"qps":100}')
        MonitorLog.objects.filter(pk=log.pk).update(create_time=timezone.now()-timedelta(hours=3))
        short = self.client.get('/api/v1/dashboard/charts/?hours=1').json()
        long = self.client.get('/api/v1/dashboard/charts/?hours=6').json()
        self.assertEqual(len(short['trend']), 1)
        self.assertEqual(len(long['trend']), 2)

    def test_bad_window_is_400(self):
        for hours in ['oops', '0', '169', '-1', '1.5']:
            self.assertEqual(self.client.get('/api/v1/dashboard/charts/', {'hours': hours}).status_code, 400)

    def test_empty_scope_cannot_see_samples(self):
        with patch('monitor.auth.get_user_database_ids', return_value=[]):
            self.assertEqual(self.client.get('/api/v1/dashboard/charts/').json()['trend'], [])

    def test_orm_failure_is_not_empty_success(self):
        with patch('monitor.api_views.DashboardChartsView._get_trend_from_orm', side_effect=RuntimeError('unavailable')):
            self.assertEqual(self.client.get('/api/v1/dashboard/charts/').status_code, 503)

    def test_create_preserves_user_collection_choices(self):
        from monitor.models import DatabaseConfig
        user = make_user('ux-creator', RoleCode.DBA, [Perm.DATABASES_CREATE])
        login(self.client, user)
        payload = {'name': 'ux-new', 'db_type': 'pgsql', 'host': 'localhost', 'port': 5432,
                   'username': 'fixture', 'password': 'fixture', 'collect_interval_sec': 120,
                   'template_name': 'pgsql_standard'}
        response = self.client.post('/api/v1/databases/', payload, content_type='application/json')
        self.assertEqual(response.status_code, 201)
        db = DatabaseConfig.objects.get(pk=response.json()['id'])
        self.assertEqual((db.collect_interval_sec, db.template_name), (120, 'pgsql_standard'))
        self.assertNotEqual(db.password, payload['password'])
        for interval in ['bad', -1, 0, 10.5, True, 3601]:
            payload['collect_interval_sec'] = interval
            self.assertEqual(self.client.post('/api/v1/databases/', payload, content_type='application/json').status_code, 400)
