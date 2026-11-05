import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('health', Path(__file__).resolve().parents[1] / 'publish-health.py')
health = importlib.util.module_from_spec(spec)
spec.loader.exec_module(health)


class HealthTests(unittest.TestCase):
    def env(self):
        return {'GITHUB_REPOSITORY': 'synthetic/board', 'GITHUB_RUN_ID': '123',
                'O_HEALTH_SCAN': 'success', 'O_HEALTH_AUTHORIZATION': 'failure',
                'GITHUB_TOKEN': 'synthetic-token-must-never-be-published'}

    def test_authorization_failure_and_missing_names(self):
        result = health.snapshot(self.env())
        self.assertEqual(result['reason'], 'AUTHORIZATION_FAILURE')
        self.assertEqual(result['missing_owner_secret_names'], list(health.SECRET_NAMES))
        self.assertNotIn('synthetic-token', json.dumps(result))

    def test_present_secrets_never_appear_in_snapshot(self):
        env = self.env()
        env.update({k: 'synthetic-owner-private-value' for k in health.SECRET_NAMES})
        env['O_HEALTH_AUTHORIZATION'] = 'success'
        env['O_HEALTH_COORDINATOR'] = 'success'
        result = health.snapshot(env)
        self.assertEqual(result['reason'], 'COMPLETED')
        self.assertEqual(result['missing_owner_secret_names'], [])
        self.assertNotIn('synthetic-owner-private', json.dumps(result))

    def test_read_failure_remains_visible(self):
        env = self.env();env['O_HEALTH_SCAN'] = 'failure'
        self.assertEqual(health.snapshot(env)['reason'], 'SCAN_FAILURE')

    def test_real_git_publication_is_separate_and_non_force(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); remote = root/'origin.git'; repo = root/'repo'
            import subprocess
            subprocess.run(['git', 'init', '--quiet', '--bare', str(remote)], check=True)
            subprocess.run(['git', 'clone', '--quiet', str(remote), str(repo)], check=True)
            health.git(repo, 'config', 'user.name', 'Synthetic O')
            health.git(repo, 'config', 'user.email', 'o@example.invalid')
            (repo/'README').write_text('synthetic')
            health.git(repo, 'add', 'README')
            health.git(repo, 'commit', '--quiet', '-m', 'fixture')
            head = health.git(repo, 'rev-parse', 'HEAD')
            first = health.publish(repo, health.snapshot(self.env()))
            second = health.publish(repo, health.snapshot(self.env()))
            self.assertEqual(health.git(repo, 'rev-parse', 'HEAD'), head)
            self.assertEqual(health.git(repo, 'rev-parse', second+'^').decode().strip(), first)
            public = health.git(repo, 'show', second+':health.json')
            self.assertNotIn(b'synthetic-token', public)
            self.assertEqual(json.loads(public)['reason'], 'AUTHORIZATION_FAILURE')
