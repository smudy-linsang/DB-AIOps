"""Isolated fixtures for the reliability acceptance journey."""

import json
import os
import secrets
import sys
from datetime import timedelta
from pathlib import Path

import django

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'dbmonitor.settings')
django.setup()

from django.conf import settings
from django.utils import timezone

from monitor.alert_manager import AlertManager
from monitor.models import (AlertLog, AuditLog, ChangeEvent, DatabaseConfig, Event, Incident, MonitorLog,
                            OamCase, OamStep, OamTrajectory, Playbook, PlaybookRun)

fixture_path = Path('/workspace/dbaiops-runtime/reliability-uat.json')
if settings.IS_PRODUCTION or settings.DATABASES['default'].get('HOST') != '127.0.0.1':
    raise RuntimeError('Reliability UAT requires the isolated local development database.')

action = sys.argv[1] if len(sys.argv) == 2 else 'seed'
if action in ('cleanup', 'recover'):
    fixture = json.loads(fixture_path.read_text()) if fixture_path.exists() else {}
    if fixture and action == 'cleanup':
        DatabaseConfig.objects.filter(pk__in=fixture['db_ids'], name__startswith=fixture['prefix']).delete()
        Playbook.objects.filter(playbook_id=fixture['playbook_id']).delete()
        OamCase.objects.filter(case_id=fixture['prefix']).delete()
        fixture_path.unlink()
    if fixture and action == 'recover':
        alert = AlertLog.objects.select_related('config').get(pk=fixture['alert_id'])
        AlertManager(alert.config).resolve(
            alert.alert_type, alert.metric_key,
            evidence={'source': 'collector', 'value': 0, 'threshold': 80},
        )
    sys.exit(0)

if action != 'seed' or fixture_path.exists():
    raise RuntimeError('Clean up only the existing test fixture before starting a new run.')

prefix = 'UAT-RELIABILITY-' + secrets.token_hex(4)
stale = DatabaseConfig.objects.create(name=prefix + ' 旧样本', db_type='pgsql', host='127.0.0.1', port=1,
                                      username='fixture', collect_interval_sec=60)
db = DatabaseConfig.objects.create(name=prefix + ' 交接库', db_type='pgsql', host='127.0.0.1', port=1,
                                   username='fixture', collect_interval_sec=60)
now = timezone.now()
old = MonitorLog.objects.create(config=stale, status='UP', message='{"active_connections":0}')
MonitorLog.objects.filter(pk=old.pk).update(create_time=now - timedelta(hours=1))
MonitorLog.objects.create(config=db, status='UP', message='{"active_connections":0,"conn_usage_pct":90}')
incident = Incident.objects.create(
    config=db, incident_id=prefix + '-P1', db_type='pgsql', title=prefix + ' 连接事故', priority='P1',
    category='connection', dedup_key=prefix, occurred_at=now - timedelta(minutes=5),
    rca_result={
        'root_causes': [
            {'rule_id': 'R1', 'name': '连接池额度不足', 'source': 'llm', 'confidence': .8,
             'evidence_refs': ['E1', 'E404']},
            {'rule_id': 'R2', 'name': '流量短时上升', 'source': 'rule', 'confidence': .5},
        ],
        'llm': {'summary': '需要核查连接与业务流量。', 'need_more_evidence': ['业务入口请求量'],
                'meta': {'evidence_index': [{'id': 'E1', 'type': 'event', 'label': '连接池观测',
                                            'observed_at': now.isoformat(),
                                            'focus': 'event-' + prefix[-8:], 'preview': {'value': 90}}]}},
    },
)
Event.objects.create(config=db, incident=incident, event_uid=prefix[-8:], db_type='pgsql', source='collector',
                     signal='connection', metric_key='conn_usage_pct', value=90, threshold=80, occurred_at=now)
ChangeEvent.objects.create(config=db, source='manual', title='验收变更：调整连接池额度', occurred_at=now, dedup_key=prefix)
alert = AlertLog.objects.create(config=db, title=prefix + ' 连接池告警', alert_type='connection',
                                metric_key='conn_usage_pct', severity='critical', description='验收专用合成告警。')
ticket = AuditLog.objects.create(config=db, action_type='manual_sql', description='验收工单：核查连接池',
                                 sql_command='', status='pending')
playbook = Playbook.objects.create(playbook_id=prefix, name='连接池核查', category='connection', risk_level='low')
for suffix, state in [('insufficient', 'insufficient_evidence'), ('verified', 'verified')]:
    recovered = True if state == 'verified' else None
    PlaybookRun.objects.create(
        run_id=f'{prefix}-{suffix}', incident=incident, playbook=playbook,
        status='succeeded' if recovered else 'timeout', started_at=now - timedelta(minutes=4),
        finished_at=now - timedelta(minutes=1),
        verify_result={'state': state, 'recovered': recovered, 'metric': 'conn_usage_pct',
                       'source': 'collector', 'predicate': '<= 80', 'observed_seconds': 120,
                       'window_seconds': 120, 'stable_samples': 3 if recovered else 0,
                       'required_samples': 3, 'sample_count': 3 if recovered else 0,
                       'missing_checks': 0 if recovered else 4, 'recurrences': 0,
                       'last_sample_at': (now - timedelta(minutes=1)).isoformat() if recovered else None,
                       'value': 50 if recovered else None},
    )

case = OamCase.objects.create(case_id=prefix, db_type='pgsql', category='inspection', difficulty='easy',
                             source_config=db, symptom='连接用量升高', gt_root_cause='connection_limit')
trajectory = OamTrajectory.objects.create(
    traj_id=prefix, case=case, config=db, policy_backend='prompted', policy_version='uat',
    report={'root_cause': '连接额度偏高', 'evidence_refs': ['metric:connection']}, turns=11,
)
for turn in range(1, 12):
    OamStep.objects.create(
        trajectory=trajectory, turn=turn, tool_name='db.metrics',
        observation='第11轮实测连接指标' if turn == 11 else '例行采样',
        observation_evidence={'refs': ['metric:connection'], 'observed_at': now.isoformat()} if turn == 11 else {},
    )

with os.fdopen(os.open(fixture_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'w') as output:
    json.dump({'prefix': prefix, 'incident_id': incident.incident_id, 'alert_id': alert.pk,
               'db_ids': [db.pk, stale.pk], 'ticket_id': ticket.pk, 'playbook_id': playbook.playbook_id}, output)
print('Reliability UAT fixtures created.')
