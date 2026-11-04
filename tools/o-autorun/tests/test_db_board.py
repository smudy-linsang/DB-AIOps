import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('db_adapter', Path(__file__).resolve().parents[2] / 'o_db_autorun.py')
db = importlib.util.module_from_spec(spec)
spec.loader.exec_module(db)
db.activate()
auto = db.auto


class DBBoardTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.remote = self.root / 'origin.git'
        self.seed = self.root / 'seed'
        self.git('init', '--bare', '--quiet', str(self.remote))
        self.git('init', '-b', 'main', '--quiet', str(self.seed))
        self.git('config', 'user.name', 'Synthetic O', cwd=self.seed)
        self.git('config', 'user.email', 'o@example.invalid', cwd=self.seed)
        self.git('remote', 'add', 'origin', str(self.remote), cwd=self.seed)
        self.write('README.md', 'synthetic board\n')
        self.commit()
        self.cfg = auto.Config(str(self.remote), str(self.root / 'state'), environment_id='synthetic-runner')
        self.board = db.DBBoard(self.cfg)

    def git(self, *args, **kw):
        return auto.command(['git', *args], **kw)

    def write(self, name, text):
        p = self.seed / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)

    def commit(self):
        self.git('add', '.', cwd=self.seed)
        self.git('commit', '--quiet', '-m', 'synthetic change', cwd=self.seed)
        self.git('push', '--quiet', 'origin', 'main', cwd=self.seed)

    def jobs(self):
        self.board.refresh()
        return [auto.resolve(self.board, p) for p in self.board.manifests()]

    def order(self, task='DB-004', assignee='O'):
        self.write('docs/pm/orders/' + task + '-test.md', '执行：' + assignee + '\nSynthetic read/report.\n')
        self.commit()

    def test_exact_member_and_history_filters(self):
        for task, member in [('DB-001', 'O'), ('DB-002', 'O'), ('DB-003', 'O'),
                             ('DB-004', 'GG、O'), ('DB-005', 'OO'), ('DB-006', 'A')]:
            self.order(task, member)
        self.assertEqual([j['task_id'] for j in self.jobs()], ['DB-004'])

    def test_reports_do_not_resubmit_and_receipt_precedes_worker(self):
        self.order()
        first = self.jobs()[0]
        db.acknowledge(self.cfg, first)
        ack = self.git('--git-dir', str(self.remote), 'show', 'main:docs/pm/reports/DB-004-R1-ack-O.md')
        self.assertIn(first['job_key'].encode(), ack)
        self.assertIn('预计交付'.encode(), ack)
        second = self.jobs()[0]
        self.assertEqual(first['job_key'], second['job_key'])
        head = self.board.head
        db.acknowledge(self.cfg, second)
        self.board.refresh()
        self.assertEqual(head, self.board.head)

    def test_test_namespace_and_duplicate_rejection(self):
        self.order('DB-TEST-001')
        self.assertEqual(self.jobs()[0]['task_id'], 'DB-TEST-001')
        self.write('docs/pm/orders/DB-TEST-001-duplicate.md', '执行：O\n')
        self.commit()
        with self.assertRaisesRegex(auto.Failure, 'DUPLICATE_DB_ORDER'):
            self.jobs()

    def test_missing_executor_is_failure(self):
        self.write('docs/pm/orders/DB-004-test.md', 'No executor line\n')
        self.commit()
        with self.assertRaisesRegex(auto.Failure, 'INVALID_DB_EXECUTOR_LINE'):
            self.jobs()

    def test_refresh_rechecks_changed_order_before_submission(self):
        self.order()
        first = self.jobs()[0]
        self.write('docs/pm/orders/DB-004-test.md', '执行：A\nReassigned before submission.\n')
        self.commit()
        self.board.refresh()
        with self.assertRaises(auto.Waiting):
            auto.resolve(self.board, first['manifest_path'])

    def test_read_failure_is_not_empty(self):
        self.order()
        self.board.refresh()
        self.board.git('remote', 'set-url', 'origin', str(self.root / 'missing.git'))
        with self.assertRaises(auto.Failure):
            self.board.refresh()

    def test_explicit_manifest_keeps_prerequisites_and_round(self):
        self.order()
        source = self.jobs()[0]['source_sha']
        self.write('docs/pm/dispatch/DB-004.json', json.dumps({
            'schema_version': 1, 'task_id': 'DB-004', 'round': 2, 'member': 'O',
            'order_path': 'docs/pm/orders/DB-004-test.md', 'source_sha': source,
            'requires': [{'role': 'A', 'receipt': 'docs/pm/reports/DB-004-R2-A.receipt.json'}]}))
        self.commit()
        with self.assertRaises(auto.Waiting):
            self.jobs()

    def test_local_execution_and_validated_report_publication(self):
        self.order('DB-TEST-E2E')
        job = self.jobs()[0]
        db.acknowledge(self.cfg, job)
        report = '通过\n\nsource_sha: ' + job['source_sha'] + '\nSynthetic executor only.\n'
        meta = {'schema_version': 1, 'role': 'O', 'task_id': job['task_id'],
                'round': 1, 'source_sha': job['source_sha'], 'job_key': job['job_key'],
                'conclusion': '通过', 'report_path': job['report_path'],
                'report_sha256': auto.digest(report.encode()),
                'checks': [{'name': 'synthetic read', 'result': 'PASS'}], 'evidence': []}
        patch = []
        for name, content in [(job['report_path'], report), (job['receipt_path'], json.dumps(meta) + '\n')]:
            lines = content.splitlines(keepends=True)
            patch.append('diff --git a/{0} b/{0}\nnew file mode 100644\n--- /dev/null\n+++ b/{0}\n@@ -0,0 +1,{1} @@\n'.format(name, len(lines)))
            patch.extend('+' + line for line in lines)
        payload = ''.join(patch).encode()

        class SyntheticExecutor:
            submitted = 0
            def preflight(self):
                return None
            def submit(self, actual):
                self.submitted += 1
                return 'task_synthetic'
            def status(self, task_id):
                return 'completed'
            def diff(self, task_id):
                return payload

        cloud = SyntheticExecutor()
        dispatcher = auto.Dispatcher(self.cfg, cloud=cloud)
        self.addCleanup(dispatcher.store.db.close)
        dispatcher.tick()
        dispatcher.tick()
        self.assertEqual(dispatcher.store.rows()[0]['state'], 'done')
        self.assertEqual(cloud.submitted, 1)
        dispatcher.tick()
        self.assertEqual(cloud.submitted, 1)
        result = self.git('--git-dir', str(self.remote), 'show', 'main:' + job['report_path'])
        self.assertEqual(result, report.encode())

    def test_legacy_engine_board_unchanged(self):
        self.order()
        legacy = db.DBBoard.__bases__[0](self.cfg)
        legacy.refresh()
        self.assertEqual(legacy.manifests(), [])


if __name__ == '__main__':
    unittest.main()
