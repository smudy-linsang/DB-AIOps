"""Independent DB-002 reproducer: synthetic in-memory data, no target DB access.

Run from repository root:
.venv/bin/python docs/pm/reports/evidence/DB-002-O/reproduce.py --output /tmp/db002-probe.json
"""
import argparse
import collections
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[5]
sys.path.insert(0, str(ROOT))
os.environ['DJANGO_SETTINGS_MODULE'] = 'dbmonitor.settings_test_unit'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    import django
    django.setup()
    from django.test import Client
    from django.test.runner import DiscoverRunner
    from monitor.auth import Perm
    from monitor.inspection_registry import ALL_ITEMS
    import monitor.inspection_executor as engine
    from monitor.models import DatabaseConfig, InspectionFinding, InspectionRun
    from monitor.tests_bugfix import make_db, make_user, login

    methods = {name for cls in (engine.GenericDetector, engine.TDSQLDetector)
               for name, value in vars(cls).items() if callable(value)}
    missing = [{'item_id': it['item_id'], 'detect_method': it['detect_method']}
               for it in ALL_ITEMS if it['detect_method'] not in methods]
    calls = []

    def spy(ctx):
        calls.append(ctx.item['item_id'])
        return engine.DetectionResult(item_id=ctx.item['item_id'], item_title='', status='ok')

    executor = engine.InspectionExecutor()
    with patch.dict(engine.DETECTOR_REGISTRY, {k: spy for k in engine.DETECTOR_REGISTRY}):
        results = [executor._run_single(SimpleNamespace(id=1, db_type='tdsql'), it, 'daily')
                   for it in ALL_ITEMS]
    dispatch = {'total_items': len(ALL_ITEMS), 'detector_keys': len(engine.DETECTOR_REGISTRY),
                'statuses': dict(collections.Counter(r.status for r in results)), 'calls': calls,
                'same_named_methods': len(ALL_ITEMS) - len(missing), 'missing_named_methods': missing,
                'all_skip_score': executor._calc_health_score(0, 0, 0, 0),
                'one_ok_79_skip_score': executor._calc_health_score(1, 0, 0, 0)}
    assert dispatch['statuses'] == {'skip': 79, 'ok': 3}
    assert len(missing) == 71
    assert dispatch['all_skip_score'] == 0 and dispatch['one_ok_79_skip_score'] == 100

    runner = DiscoverRunner(verbosity=0)
    old = runner.setup_databases()
    try:
        own = make_db('DB002-synthetic-owned')
        other = make_db('DB002-synthetic-other')
        perms = [v for k, v in vars(Perm).items() if not k.startswith('_') and isinstance(v, str)]
        scoped = make_user('db002-scoped', 'db002-scoped-role', perms, allowed_dbs=[own.id])
        zero = make_user('db002-zero', 'db002-zero-role', [], allowed_dbs=[own.id])
        assert not DatabaseConfig.objects.visible_to(scoped).filter(pk=other.pk).exists()
        api = []
        for label, user, target in [('own-with-permissions', scoped, own),
                                    ('cross-scope-with-permissions', scoped, other),
                                    ('own-with-zero-permissions', zero, own)]:
            before = InspectionRun.objects.filter(db_config=target).count()
            client = Client(HTTP_HOST='localhost')
            login(client, user)
            response = client.post('/api/v1/inspection/runs/trigger/',
                                   data=json.dumps({'config_id': target.id, 'level': 'daily'}),
                                   content_type='application/json')
            api.append({'case': label, 'status': response.status_code, 'body': response.json(),
                        'new_runs': InspectionRun.objects.filter(db_config=target).count() - before})
        assert all(r['status'] == 400 and r['new_runs'] == 1 and
                   'unexpected keyword argument' in r['body']['error'] for r in api)
        run = InspectionRun.objects.filter(db_config=other).first()
        marker = {'review_marker': 'DB002-synthetic-only'}
        InspectionFinding.objects.create(run=run, item_code='DB002-SYNTHETIC',
                                         title='Synthetic review finding', category='security',
                                         severity='warn', raw_data=marker)
        client = Client(HTTP_HOST='localhost')
        login(client, scoped)
        response = client.get('/api/v1/inspection/runs/' + run.run_id + '/')
        detail = response.json()
        assert response.status_code == 200 and detail['findings'][0]['raw_data'] == marker
        leak = {'status': response.status_code, 'db_name': detail['run']['db_name'],
                'raw_data': detail['findings'][0]['raw_data'], 'scope_control_other_visible': False}
    finally:
        runner.teardown_databases(old)
    output = {'checked_head': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT).decode().strip(),
              'dispatch': dispatch, 'trigger': api, 'cross_scope_detail': leak,
              'limits': 'Original dispatcher/API/auth/ORM; detector bodies are spies; SQLite synthetic fixtures only; no target SQL or production acceptance'}
    args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2) + '\n')
    print('DB-002 probes reproduced; output:', args.output)


if __name__ == '__main__':
    main()
