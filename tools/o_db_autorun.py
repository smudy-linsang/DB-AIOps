#!/usr/bin/env python3
"""DB-AIOps adapter; vendored Xinchuang execution engine keeps its original dispatch protocol."""
import argparse
import json
from pathlib import Path
import re
import sys
import tempfile
from datetime import datetime, timedelta, timezone

ENGINE = Path(__file__).resolve().parent / 'o-autorun' / 'engine'
sys.path.insert(0, str(ENGINE))
import o_autorun as auto
import o_github_coordinator as hosted

DB_TASK = r'DB-(?:[0-9]{3,}|TEST-[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*)'
UPSTREAM_TASK = auto.TASK.pattern.removesuffix(r'\Z')


class DBBoard(auto.Board):
    """Convert new, explicitly O-assigned orders into stable dispatch envelopes."""
    def refresh(self):
        super().refresh()
        # Rebuild virtual envelopes after every fetch, including the engine
        # dependency recheck immediately before submission.
        self.manifests()

    def manifests(self):
        self.generated = {}
        manifests = super().manifests()
        explicit = set()
        for path in manifests:
            # Invalid explicit manifests still reach the engine's durable inbox.
            try:
                explicit.add(json.loads(super().read(path)).get('task_id'))
            except (ValueError, TypeError, AttributeError):
                continue
        raw = self.git('ls-tree', '-r', '--name-only', '-z', self.head, '--', 'docs/pm/orders/')
        for path in raw.decode().split('\0'):
            match = re.fullmatch(r'docs/pm/orders/(' + DB_TASK + r')-[^/]+\.md', path)
            if not match:
                continue
            task = match[1]
            # DB-001..003 were issued before this listener and manually received.
            if task in explicit or (task[3:].isdigit() and int(task[3:]) <= 3):
                continue
            text = super().read(path).decode('utf-8')
            lines = re.findall(r'^[ \t]*(?:[-*+][ \t]+)?执行[ \t]*[：:][ \t]*(.*)$', text, re.MULTILINE)
            if len(lines) != 1:
                raise auto.Failure('INVALID_DB_EXECUTOR_LINE', path)
            members = re.split(r'[、,，\s]+', lines[0].strip())
            if 'O' not in members:
                continue
            if self.mode(path) != '100644':
                raise auto.Failure('INVALID_DB_ORDER_MODE', path)
            # Stable across unrelated reports/commits; editing an order creates
            # a new review identity. Explicit manifests retain round control.
            source = self.git('log', '-1', '--format=%H', self.head, '--', path).decode().strip()
            virtual = auto.DISPATCH + task + '-auto-O.json'
            if virtual in manifests or virtual in self.generated:
                raise auto.Failure('DUPLICATE_DB_ORDER', task)
            self.generated[virtual] = auto.encode({
                'schema_version': 1, 'task_id': task, 'member': 'O', 'round': 1,
                'source_sha': source, 'order_path': path, 'requires': []}).encode()
        return manifests + list(self.generated)

    def read(self, path):
        if path in getattr(self, 'generated', {}):
            return self.generated[path]
        return super().read(path)


def acknowledge(cfg, job):
    """Publish a separate ETA before executing the model, preserving old receipts."""
    path = 'docs/pm/reports/' + job['task_id'] + '-R%d-ack-O.md' % job['round']
    for attempt in range(3):
        with tempfile.TemporaryDirectory(prefix='o-db-ack-') as tmp:
            auto.command(['git', 'clone', '--quiet', '--branch', 'main', cfg.repo_url, tmp])
            target = Path(tmp) / path
            if target.is_symlink() or any(p.is_symlink() for p in target.parents):
                raise auto.Failure('INVALID_DB_ACK')
            if target.exists():
                text = target.read_text()
                if job['job_key'] not in text:
                    raise auto.Failure('DB_ACK_IDENTITY_CONFLICT')
                return
            now = datetime.now(timezone(timedelta(hours=8)))
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text('# O 自动接单回执\n\n执行：O\n\n'
                              + 'task_id: ' + job['task_id'] + '\njob_key: ' + job['job_key']
                              + '\n接单时间：' + now.isoformat()
                              + '\n预计交付：' + (now + timedelta(hours=3)).isoformat()
                              + ' 前交付报告或明确阻塞（Asia/Shanghai）。\n'
                              + '自动接单不等于验收通过；结果以本轮报告为准。\n')
            auto.command(['git', 'add', '--', path], cwd=tmp)
            auto.command(['git', '-c', 'user.name=' + cfg.author_name,
                          '-c', 'user.email=' + cfg.author_email,
                          'commit', '--quiet', '-m', 'docs(pm): O automatic receipt ' + job['task_id']], cwd=tmp)
            try:
                auto.command(['git', 'push', '--quiet', 'origin', 'HEAD:refs/heads/main'], cwd=tmp)
                return
            except auto.Failure:
                if attempt == 2:
                    raise


def activate():
    auto.TASK = re.compile('(?:' + UPSTREAM_TASK + '|' + DB_TASK + r')\Z')
    auto.Board = DBBoard
    import o_exec_worker as worker
    for cls in (worker.ExecWorker, auto.Cloud):
        original_submit = cls.submit

        def submit(self, job, _submit=original_submit):
            acknowledge(self.cfg, job)
            return _submit(self, job)

        cls.submit = submit


def main():
    activate()
    if '--scan-only' not in sys.argv:
        return hosted.main()
    parser = argparse.ArgumentParser()
    parser.add_argument('--scan-only', action='store_true')
    parser.add_argument('--repo', required=True)
    parser.add_argument('--directory', required=True)
    args = parser.parse_args()
    remote = auto.command(['git', 'remote', 'get-url', 'origin'], cwd=args.repo).decode().strip()
    dispatcher = auto.Dispatcher(auto.Config(remote, args.directory))
    try:
        dispatcher.tick(scan_only=True)
        rows = list(dispatcher.store.db.execute('SELECT state FROM inbox'))
        if any(r['state'] == 'invalid' for r in rows):
            raise auto.Failure('DISPATCH_INBOX_INVALID')
        print(json.dumps({'inbox_count': len(rows), 'jobs': len(dispatcher.store.rows())}))
    finally:
        dispatcher.store.db.close()


if __name__ == '__main__':
    try:
        main()
    except auto.Failure as exc:
        print('::error::' + exc.code, file=sys.stderr)
        sys.exit(1)
