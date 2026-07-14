# -*- coding: utf-8 -*-
"""Phase 7 单元测试: wait_class 映射 30 样例 + sqlfingerprint 12 样例。

运行: venv/bin/python monitor/tests_phase7.py (纯函数, 不依赖 Django)
"""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from monitor.detectors.wait_class import classify  # noqa
from monitor.sqlfingerprint import normalize, unified_digest  # noqa

# (db_type, row, expected) —— 契约样例, 改映射先改 phase7/10 §3 再改这里
WAIT_CLASS_CASES = [
    # ---- MySQL 家族 10 ----
    ('mysql', {'state': 'Waiting for table metadata lock'}, 'concurrency'),
    ('mysql', {'state': 'waiting for handler commit'}, 'commit'),
    ('mysql', {'state': 'Sending to client'}, 'network'),
    ('mysql', {'state': 'Sending data'}, 'user_io'),
    ('mysql', {'state': 'Copying to tmp table on disk'}, 'user_io'),
    ('mysql', {'state': 'idle_in_trx'}, 'application'),
    ('mysql', {'state': 'executing'}, 'on_cpu'),
    ('mysql', {'state': 'Creating sort index'}, 'on_cpu'),
    ('mysql', {'state': '', 'command': 'Query'}, 'on_cpu'),
    ('mysql', {'state': 'something exotic'}, 'other'),
    # ---- PG 10 ----
    ('pgsql', {'state': 'active', 'wait_event_type': None}, 'on_cpu'),
    ('pgsql', {'state': 'active', 'wait_event_type': 'Lock'}, 'concurrency'),
    ('pgsql', {'state': 'active', 'wait_event_type': 'LWLock'}, 'concurrency'),
    ('pgsql', {'state': 'active', 'wait_event_type': 'BufferPin'}, 'concurrency'),
    ('pgsql', {'state': 'active', 'wait_event_type': 'IO'}, 'user_io'),
    ('pgsql', {'state': 'active', 'wait_event_type': 'Client'}, 'network'),
    ('pgsql', {'state': 'idle in transaction'}, 'application'),
    ('pgsql', {'state': 'active', 'wait_event_type': 'IPC'}, 'other'),
    ('pgsql', {'state': 'active', 'wait_event_type': 'Timeout'}, 'other'),
    ('pgsql', {'state': 'active', 'wait_event_type': 'NewType9'}, 'other'),
    # ---- Oracle/DM 10 ----
    ('oracle', {'state': 'WAITED SHORT TIME', 'raw_wait_class': 'User I/O'}, 'on_cpu'),
    ('oracle', {'state': 'WAITING', 'raw_wait_class': 'User I/O'}, 'user_io'),
    ('oracle', {'state': 'WAITING', 'raw_wait_class': 'System I/O'}, 'system_io'),
    ('oracle', {'state': 'WAITING', 'raw_wait_class': 'Concurrency'}, 'concurrency'),
    ('oracle', {'state': 'WAITING', 'raw_wait_class': 'Application'}, 'application'),
    ('oracle', {'state': 'WAITING', 'raw_wait_class': 'Commit'}, 'commit'),
    ('oracle', {'state': 'WAITING', 'raw_wait_class': 'Network'}, 'network'),
    ('oracle', {'state': 'WAITING', 'raw_wait_class': 'Scheduler'}, 'configuration'),
    ('oracle', {'state': 'WAITING', 'raw_wait_class': 'Queueing'}, 'other'),
    ('dm', {'state': 'WAITING', 'raw_wait_class': 'Configuration'}, 'configuration'),
    # ---- 阻塞覆写优先 ----
    ('mysql', {'state': 'executing', 'is_blocked': True}, 'concurrency'),
    ('oracle', {'state': 'WAITED KNOWN TIME', 'is_blocked': True}, 'concurrency'),
]

FINGERPRINT_CASES = [
    # 同 SQL 不同字面量 → 同指纹
    ("SELECT * FROM orders WHERE id = 100",
     "SELECT * FROM orders WHERE id = 999", True),
    ("select name from t where s = 'abc'",
     "SELECT   name FROM t WHERE s = 'xyz'", True),
    ("SELECT a FROM t WHERE x IN (1,2,3)",
     "SELECT a FROM t WHERE x IN (4,5,6,7,8)", True),
    ("SELECT a FROM t /* hint */ WHERE b=1",
     "SELECT a FROM t WHERE b=2 -- tail", True),
    # 不同 SQL → 不同指纹
    ("SELECT a FROM t1", "SELECT a FROM t2", False),
    ("SELECT a FROM t WHERE b=1", "SELECT a,c FROM t WHERE b=1", False),
]


def run():
    fails = []
    for db_type, row, expect in WAIT_CLASS_CASES:
        got = classify(db_type, row)
        if got != expect:
            fails.append(f"classify({db_type},{row}) = {got}, 期望 {expect}")
    for a, b, same in FINGERPRINT_CASES:
        da, db_ = unified_digest('mysql', None, a), unified_digest('mysql', None, b)
        if (da == db_) != same:
            fails.append(f"指纹 {'应同' if same else '应异'}: {a!r} vs {b!r}")
    # 原生优先级
    if unified_digest('oracle', 'sqlid123', 'SELECT 1') != 'sqlid123':
        fails.append("Oracle sql_id 未优先")
    if unified_digest('mysql', 'a' * 64, None) != 'a' * 32:
        fails.append("MySQL digest 未截 32")
    if unified_digest('pgsql', '0', 'SELECT 1') == '0':
        fails.append("PG query_id=0 应回退归一化")
    if unified_digest('mysql', None, None) is not None:
        fails.append("无文本应返回 None")
    if normalize("SELECT  1") != 'select ?':
        fails.append(f"normalize 基础样例: {normalize('SELECT  1')!r}")

    total = len(WAIT_CLASS_CASES) + len(FINGERPRINT_CASES) + 5
    print(f"通过 {total - len(fails)}/{total}")
    for f in fails:
        print("  [FAIL]", f)
    return 1 if fails else 0


if __name__ == '__main__':
    import sys
    sys.exit(run())
