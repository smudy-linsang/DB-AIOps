# -*- coding: utf-8 -*-
"""统一等待类映射 (phase7/10 §3, 代码级契约)。

九类枚举: on_cpu / user_io / system_io / concurrency / application /
commit / network / configuration / other。
classify() 只会返回九类之一; is_blocked 行强制 concurrency (阻塞语义优先)。
"""

WAIT_CLASSES = ('on_cpu', 'user_io', 'system_io', 'concurrency', 'application',
                'commit', 'network', 'configuration', 'other')

_ORACLE_MAP = {
    'User I/O': 'user_io', 'System I/O': 'system_io', 'Concurrency': 'concurrency',
    'Application': 'application', 'Commit': 'commit', 'Network': 'network',
    'Configuration': 'configuration', 'Scheduler': 'configuration',
    'Queueing': 'other', 'Other': 'other', 'Administrative': 'other',
}

_PG_MAP = {
    'Lock': 'concurrency', 'LWLock': 'concurrency', 'BufferPin': 'concurrency',
    'IO': 'user_io', 'Client': 'network', 'IPC': 'other', 'Extension': 'other',
    'Timeout': 'other', 'Activity': 'other',
}

_MYSQL_CPU_STATES = ('executing', 'statistics', 'preparing', 'optimizing',
                     'sorting result', 'creating sort index', 'updating',
                     'update', 'init', 'checking permissions')


def _classify_mysql(row) -> str:
    s = (row.get('state') or '').lower()
    if 'lock' in s:
        return 'concurrency'
    if 'commit' in s or 'binlog' in s or 'flush' in s:
        return 'commit'
    if 'net' in s or 'sending to client' in s:
        return 'network'
    if 'tmp table' in s or 'file' in s or 'disk' in s or s == 'sending data':
        return 'user_io'
    if 'repl' in s or 'relay' in s or s == 'idle_in_trx':
        return 'application'
    if s in _MYSQL_CPU_STATES:
        return 'on_cpu'
    if not s and (row.get('command') or '') == 'Query':
        return 'on_cpu'
    return 'other'


def _classify_pg(row) -> str:
    state = (row.get('state') or row.get('command') or '').lower()
    if state == 'idle in transaction':
        return 'application'
    wet = row.get('wait_event_type')
    if not wet:
        return 'on_cpu' if state == 'active' else 'other'
    return _PG_MAP.get(wet, 'other')


def _classify_oracle(row) -> str:
    # v$session.state != 'WAITING' 即在 CPU 上 (WAITED% 家族)
    state = (row.get('state') or '').upper()
    if state and state != 'WAITING':
        return 'on_cpu'
    return _ORACLE_MAP.get(row.get('raw_wait_class') or '', 'other')


def classify(db_type: str, row: dict) -> str:
    """row: 采样原始行 dict (含 command/state/wait_event/raw_wait_class/
    wait_event_type/is_blocked)。返回统一九类之一。"""
    try:
        if row.get('is_blocked'):
            return 'concurrency'
        if db_type in ('mysql', 'tdsql', 'gbase'):
            cls = _classify_mysql(row)
        elif db_type in ('pgsql', 'postgresql'):
            cls = _classify_pg(row)
        elif db_type in ('oracle', 'dm'):
            cls = _classify_oracle(row)
        else:
            cls = 'other'
        return cls if cls in WAIT_CLASSES else 'other'
    except Exception:
        return 'other'
