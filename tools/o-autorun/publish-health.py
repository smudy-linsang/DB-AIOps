#!/usr/bin/env python3
"""Publish whitelisted Actions health via Git; never publish logs or secrets."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import subprocess

BRANCH = 'o-autorun-health'
STAGES = ('tests', 'scan', 'authorization', 'install', 'sandbox', 'coordinator')
OUTCOMES = {'success', 'failure', 'cancelled', 'skipped', 'pending'}
SECRET_NAMES = ('O_CODEX_AUTH_JSON', 'O_AUTORUN_STATE_KEY')


def snapshot(env):
    stages = {}
    for name in STAGES:
        value = env.get('O_HEALTH_' + name.upper(), '') or 'pending'
        stages[name] = value if value in OUTCOMES else 'unknown'
    phase = env.get('O_HEALTH_PHASE', 'final')
    reason = 'RUNNING'
    for name in STAGES:
        if stages[name] in {'failure', 'cancelled'}:
            reason = name.upper() + '_' + stages[name].upper()
            break
    else:
        if stages['coordinator'] == 'success':
            reason = 'COMPLETED'
        elif phase == 'final':
            reason = 'INCOMPLETE'
    repo, run_id = env.get('GITHUB_REPOSITORY', ''), env.get('GITHUB_RUN_ID', '')
    if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repo) or not run_id.isdigit():
        raise ValueError('INVALID_ACTIONS_IDENTITY')
    missing = [name for name in SECRET_NAMES if not env.get(name)]
    return {'schema_version': 1, 'observed_at': datetime.now(timezone.utc).isoformat(),
            'repository': repo, 'run_id': run_id,
            'run_url': 'https://github.com/' + repo + '/actions/runs/' + run_id,
            'phase': phase, 'reason': reason, 'stages': stages,
            'missing_owner_secret_names': missing}


def git(repo, *args, data=None):
    p = subprocess.run(['git', '-C', str(repo), *args], input=data,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
    if p.returncode:
        raise RuntimeError('HEALTH_GIT_FAILED')
    return p.stdout


def publish(repo, health):
    repo = Path(repo).resolve()
    health['source_sha'] = git(repo, 'rev-parse', 'HEAD').decode().strip()
    content = (json.dumps(health, ensure_ascii=False, indent=2) + '\n').encode()
    blob = git(repo, 'hash-object', '-w', '--stdin', data=content).decode().strip()
    tree = git(repo, 'mktree', data=('100644 blob ' + blob + '\thealth.json\n').encode()).decode().strip()
    for attempt in range(3):
        rows = git(repo, 'ls-remote', '--heads', 'origin', 'refs/heads/' + BRANCH).decode().split()
        parent = rows[0] if rows else None
        if parent:
            if not re.fullmatch('[0-9a-f]{40}', parent):
                raise ValueError('INVALID_HEALTH_PARENT')
            git(repo, 'fetch', '--quiet', 'origin', parent)
        args = ['-c', 'user.name=O automation', '-c', 'user.email=o-automation@users.noreply.github.com',
                'commit-tree', tree]
        if parent:
            args += ['-p', parent]
        commit = git(repo, *args, data=b'O automation safe health\n').decode().strip()
        try:
            git(repo, 'push', '--quiet', 'origin', commit + ':refs/heads/' + BRANCH)
            return commit
        except RuntimeError:
            if attempt == 2:
                raise


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', required=True)
    args = parser.parse_args()
    print('Published safe O health:', publish(args.repo, snapshot(os.environ)))
