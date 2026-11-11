# -*- coding: utf-8 -*-
"""DB-023：下半 34 项检测器的命中、未命中、方言 skip、查询异常。

脚本游标会真正执行检测函数。状态不对、SQL 对象不对，用例失败。
"""
import json
from types import SimpleNamespace
from unittest.mock import patch

from django.test import Client, TestCase, tag

from monitor.auth import Perm, RoleCode
from monitor.inspection_detectors_010b import (
    DB023_DETECTORS,
    SQL_BLOAT,
    SQL_DM_ARCH_STATUS,
    SQL_FREEZE,
    gtid_missing_count,
    parse_oracle_interval,
)
from monitor.inspection_executor import (
    DETECTOR_REGISTRY,
    DetectionContext,
    InspectionExecutor,
)
from monitor.inspection_registry import ALL_ITEMS
from monitor.models import InspectionFinding, InspectionRun
from monitor.tests_bugfix import login, make_db, make_user

ITEMS = {item['item_id']: item for item in ALL_ITEMS}
GTID = '11111111-1111-1111-1111-111111111111'
MB = 1024 * 1024
ORIGINAL_DIRECT = [
    'INS-COM-INVALID-OBJ', 'INS-COM-RECYCLEBIN', 'INS-COM-LONG-TXN',
    'INS-COM-FK-NO-INDEX', 'INS-COM-STAT-STALE', 'INS-COM-BACKUP',
    'INS-COM-FAILED-LOGIN', 'INS-ORA-SCN-HEADROOM',
    'INS-TDSQL-SHARD', 'INS-TDSQL-PROXY', 'INS-TDSQL-SHARDKEY',
]
ORIGINAL_METHODS = {
    'I001': 'detect_monitoring_coverage',
    'I007': 'detect_backup_status',
    'I008': 'detect_replication_lag',
    'I010': 'detect_table_fragmentation',
    'I105': 'detect_log_switch_freq',
    'I108': 'detect_fk_no_index',
    'INS-COM-BACKUP': 'detect_backup_status',
    'INS-ORA-SCN-HEADROOM': 'detect_scn_headroom',
    'INS-TDSQL-SHARD': 'detect_tdsql_set_health',
}
EXCLUDED = ('INS-ORA-OPT-STATS-GATHER', 'INS-MYSQL-BINLOG-DELAY')
DB022_IDS = [
    'INS-COM-USER-LOCKED', 'INS-COM-PWD-EXPIRE', 'INS-COM-CAP-7D', 'INS-COM-CAP-30D',
    'INS-COM-DEADLOCK-24H', 'INS-COM-MISSING-INDEX', 'INS-COM-PART-NEEDED',
    'INS-COM-SPARSE-INDEX', 'INS-COM-PARAM-DEV', 'INS-COM-ARCH-GAP',
    'INS-COM-LOCK-WAIT', 'INS-COM-BLOCK-SESSION', 'INS-COM-OPEN-CURSOR',
    'INS-COM-TEMP-USAGE', 'INS-COM-UNDO-USAGE', 'INS-ORA-AWR-TOP-EVT',
    'INS-ORA-AWR-TOP-SQL', 'INS-ORA-STA-OUTPUT', 'INS-ORA-SEG-ADV', 'INS-ORA-FRA',
    'INS-ORA-CTRL-BAK', 'INS-ORA-ARCH-DELETE', 'INS-ORA-DBA-USERS',
]
MUST = {
    'INS-ORA-LOB-PCT': (['dba_lobs', 'pctversion'], []),
    'INS-ORA-DBLINK': (['dba_db_links', 'v$sql'], []),
    'INS-ORA-DIR-OBJ': (['dba_directories'], []),
    'INS-ORA-RMAN-AGE': (['v$rman_backup_job_details', 'DB FULL'], []),
    'INS-ORA-DG-LAG': (['v$dataguard_stats', 'apply lag'], ['V$STREAMS_APPLIED_SCN']),
    'INS-ORA-DG-STATUS': (['v$archive_dest_status', 'v$managed_standby'], []),
    'INS-ORA-RAC-VIP': (['gv$instance', 'local_listener'], []),
    'INS-ORA-REDOLOG-SIZE': (['v$log', 'bytes'], ['v$log_history']),
    'INS-ORA-OPEN-FILE': (['v$datafile', 'v$tempfile'], []),
    'INS-ORA-LOB-CACHE': (['lob reads', 'physical reads direct (lob)'], []),
    'INS-ORA-ASH-USAGE': (['ASH buffers', '_ash_size'], []),
    'INS-ORA-RMAN-CONF': (['v$rman_configuration'], []),
    'INS-ORA-AUDIT-TRAIL': (['audit_trail', 'Unified Auditing'], []),
    'INS-ORA-PGA-LEAK': (['total PGA allocated', 'aggregate PGA target parameter'], []),
    'INS-ORA-IO-STAT': (['v$filestat', 'v$datafile'], ['v$system_event']),
    'INS-ORA-EXADATA': (['v$cell', 'cell_offload_processing'], []),
    'INS-MYSQL-GTID': (["gtid%", 'SHOW SLAVE STATUS'], []),
    'INS-MYSQL-SEMI-SYNC': (["rpl_semi_sync%", "Rpl_semi_sync%"], []),
    'INS-MYSQL-EVENT': (['event_scheduler', 'information_schema.events'], []),
    'INS-MYSQL-FK-CASCADE': (['referential_constraints', 'delete_rule'], ['all_cons_columns']),
    'INS-MYSQL-TMP-DISK': (['Created_tmp%'], []),
    'INS-PG-WAL-RECV': (['pg_is_in_recovery', 'pg_stat_wal_receiver'], ['pg_stat_replication']),
    'INS-PG-INACTIVE-SLOT': (['pg_replication_slots', 'active'], []),
    'INS-PG-VACUUM-FREEZE': (['relfrozenxid', 'age('], ['n_dead_tup']),
    'INS-PG-BLOAT': (['relpages', 'bloat_pct'], ['n_dead_tup']),
    'INS-PG-EXT-OUTDATED': (['pg_extension', 'pg_available_extensions'], []),
    'INS-PG-STMT-TIMEOUT': (['pg_settings', 'statement_timeout'], []),
    'INS-DM-DSC': (['DSC_NODES', 'V$DSC_EP_INFO'], []),
    'INS-DM-DW-STANDBY': (['ROLE$', 'V$ARCH_STATUS'], []),
    'INS-DM-ARCH': (['ARCH_MODE', 'V$ARCH_STATUS', 'V$ARCH_FILE'], []),
    'INS-DM-RLOG': (['V$ARCH_STATUS'], ['V$RLOG']),
    'INS-GBASE-BALANCE': (['gcluster_v$node_info', 'CPU_USED'], []),
    'INS-GBASE-DIST': (['gcluster_v$table_distribution', 'DATA_SIZE'], []),
    'INS-GBASE-NODE-DOWN': (['gcluster_v$node_info', 'STATUS'], []),
}


def D(match, rows):
    return {'match': match, 'dicts': rows}


def E(match):
    return {'match': match, 'error': RuntimeError('boom')}


class ScriptedCursor:
    def __init__(self, steps):
        self.steps = list(steps)
        self.calls = []
        self._rows = []
        self.description = []

    def execute(self, sql, params=None):
        self.calls.append(sql)
        if not self.steps:
            raise AssertionError('多余 SQL: ' + ' '.join(sql.split()))
        step = self.steps.pop(0)
        if step.get('match') and step['match'] not in sql:
            raise AssertionError(f"期望 SQL 含 {step['match']!r}，实际 {sql}")
        if step.get('error'):
            raise step['error']
        if 'dicts' in step:
            self._rows = step['dicts']
            self.description = []
        else:
            self._rows = step.get('tuples') or []
            self.description = [(col,) for col in step.get('cols') or []]

    def fetchall(self):
        return list(self._rows)

    def close(self):
        return None


class FakeConnector:
    def __init__(self, cursor):
        self.cursor_obj = cursor
        self.closed = False

    def connect(self):
        return self

    def cursor(self):
        return self.cursor_obj

    def close(self):
        self.closed = True


def run_case(item_id, db_type, steps):
    cursor = ScriptedCursor(steps)
    connector = FakeConnector(cursor)
    ctx = DetectionContext(
        db_config=SimpleNamespace(id=1, db_type=db_type),
        db_connector=connector,
        db_type=db_type,
        item=ITEMS[item_id],
    )
    result = DB023_DETECTORS[item_id](ctx)
    return result, cursor, connector


def _nodes(pairs):
    return [
        {
            'nodeid': str(idx), 'nodename': f'n{idx}', 'nodetype': 'DN',
            'status': 'ONLINE', 'cpu_used': cpu, 'mem_used': mem,
        }
        for idx, (cpu, mem) in enumerate(pairs, start=1)
    ]


def _files(spec):
    rows = []
    for count, reads in spec:
        for idx in range(count):
            rows.append({'file_name': f'/data/f{len(rows)}.dbf', 'phyrds': reads, 'phywrts': 0})
    return rows


CASES = {
    'INS-ORA-LOB-PCT': {
        'db': 'oracle',
        'miss': [D('dba_lobs', [])],
        'hit': [D('dba_lobs', [
            {'owner': 'APP', 'table_name': f'T{i}', 'column_name': 'C', 'pctversion': 40}
            for i in range(20)
        ])],
        'hit_status': 'critical',
        'error': [E('dba_lobs')],
    },
    'INS-ORA-DBLINK': {
        'db': 'oracle',
        'miss': [D('dba_db_links', []), D('v$sql', [])],
        'hit': [
            D('dba_db_links', [{'owner': 'APP', 'db_link': f'L{i}.WORLD', 'username': 'U', 'host': 'h'} for i in range(100)]),
            D('v$sql', []),
        ],
        'hit_status': 'critical',
        'error': [E('dba_db_links')],
    },
    'INS-ORA-DIR-OBJ': {
        'db': 'oracle',
        'miss': [D('dba_directories', [{'owner': 'SYS', 'directory_name': 'DATA_PUMP_DIR', 'directory_path': '/u01/dp'}])],
        'hit': [D('dba_directories', [
            {'owner': 'APP', 'directory_name': f'DIR{i}', 'directory_path': f'/u01/app/{i}'}
            for i in range(50)
        ])],
        'hit_status': 'critical',
        'error': [E('dba_directories')],
    },
    'INS-ORA-RMAN-AGE': {
        'db': 'oracle',
        'miss': [D('v$rman_backup_job_details', [{'age_hours': 1}])],
        'hit': [D('v$rman_backup_job_details', [{'age_hours': 80}])],
        'hit_status': 'critical',
        'error': [E('v$rman_backup_job_details')],
        'extra': [{
            'name': 'no-backup',
            'steps': [D('v$rman_backup_job_details', [{'age_hours': None}])],
            'status': 'critical',
            'summary': '无已完成的 RMAN 全库备份',
        }],
    },
    'INS-ORA-DG-LAG': {
        'db': 'oracle',
        'miss': [
            D('database_role', [{'database_role': 'PHYSICAL STANDBY'}]),
            D('v$archive_dest_status', [{'dest_id': 2, 'target': 'STANDBY', 'status': 'VALID'}]),
            D('v$dataguard_stats', [{'name': 'apply lag', 'value': '+00 00:00:05', 'unit': 'day'}]),
        ],
        'hit': [
            D('database_role', [{'database_role': 'PHYSICAL STANDBY'}]),
            D('v$archive_dest_status', [{'dest_id': 2, 'target': 'STANDBY', 'status': 'VALID'}]),
            D('v$dataguard_stats', [{'name': 'apply lag', 'value': '+00 00:40:00', 'unit': 'day'}]),
        ],
        'hit_status': 'critical',
        'hit_metrics': {'lag_sec': 2400},
        'error': [E('database_role')],
        'extra': [{
            'name': 'not-dg',
            'steps': [
                D('database_role', [{'database_role': 'PRIMARY'}]),
                D('v$archive_dest_status', []),
            ],
            'status': 'skip',
            'summary': '未配置 Data Guard',
        }],
    },
    'INS-ORA-DG-STATUS': {
        'db': 'oracle',
        'miss': [
            D('database_role', [{'database_role': 'PRIMARY'}]),
            D('v$archive_dest_status', [{'dest_id': 2, 'status': 'VALID', 'dest_error': None, 'target': 'STANDBY'}]),
            D('v$managed_standby', []),
        ],
        'hit': [
            D('database_role', [{'database_role': 'PRIMARY'}]),
            D('v$archive_dest_status', [{'dest_id': 2, 'status': 'ERROR', 'dest_error': 'ORA-16191', 'target': 'STANDBY'}]),
            D('v$managed_standby', []),
        ],
        'hit_status': 'critical',
        'error': [E('database_role')],
        'extra': [{
            'name': 'not-dg',
            'steps': [
                D('database_role', [{'database_role': 'PRIMARY'}]),
                D('v$archive_dest_status', []),
            ],
            'status': 'skip',
            'summary': '未配置 Data Guard',
        }],
    },
    'INS-ORA-RAC-VIP': {
        'db': 'oracle',
        'miss': [
            D('COUNT(*)', [{'cnt': 2}]),
            D('local_listener', [
                {'inst_id': 1, 'instance_name': 'A', 'host_name': 'h1', 'status': 'OPEN',
                 'local_listener': '(ADDRESS=(PROTOCOL=TCP)(HOST=vip1)(PORT=1521))'},
                {'inst_id': 2, 'instance_name': 'B', 'host_name': 'h2', 'status': 'OPEN',
                 'local_listener': '(ADDRESS=(PROTOCOL=TCP)(HOST=vip2)(PORT=1521))'},
            ]),
        ],
        'hit': [
            D('COUNT(*)', [{'cnt': 2}]),
            D('local_listener', [
                {'inst_id': 1, 'instance_name': 'A', 'host_name': 'h1', 'status': 'OPEN',
                 'local_listener': '(ADDRESS=(PROTOCOL=TCP)(HOST=vip1)(PORT=1521))'},
                {'inst_id': 2, 'instance_name': 'B', 'host_name': 'h2', 'status': 'OPEN',
                 'local_listener': '(ADDRESS=(PROTOCOL=TCP)(HOST=vip1)(PORT=1521))'},
            ]),
        ],
        'hit_status': 'critical',
        'error': [E('COUNT(*)')],
        'extra': [{
            'name': 'not-rac',
            'steps': [D('COUNT(*)', [{'cnt': 1}])],
            'status': 'skip',
            'summary': '非 RAC',
        }],
    },
    'INS-ORA-REDOLOG-SIZE': {
        'db': 'oracle',
        'miss': [D('v$log', [{'log_group': 1, 'bytes': 2 * 1024 * MB}])],
        'hit': [D('v$log', [{'log_group': 1, 'bytes': 100 * MB}])],
        'hit_status': 'critical',
        'hit_metrics': {'min_mb': 100},
        'error': [E('v$log')],
    },
    'INS-ORA-OPEN-FILE': {
        'db': 'oracle',
        'miss': [D('v$resource_limit', []), D('v$datafile', [{'open_files': 3}])],
        'hit': [D('v$resource_limit', []), D('v$datafile', [{'open_files': 60000}])],
        'hit_status': 'critical',
        'error': [E('v$resource_limit')],
    },
    'INS-ORA-LOB-CACHE': {
        'db': 'oracle',
        'miss': [D('v$sysstat', [
            {'name': 'lob reads', 'value': 100},
            {'name': 'physical reads direct (lob)', 'value': 10},
        ])],
        'hit': [D('v$sysstat', [
            {'name': 'lob reads', 'value': 100},
            {'name': 'physical reads direct (lob)', 'value': 95},
        ])],
        'hit_status': 'critical',
        'error': [E('v$sysstat')],
    },
    'INS-ORA-ASH-USAGE': {
        'db': 'oracle',
        'miss': [D('v$sgastat', [{'bytes': 100}]), D('_ash_size', [{'value': 1000}])],
        'hit': [D('v$sgastat', [{'bytes': 960}]), D('_ash_size', [{'value': 1000}])],
        'hit_status': 'critical',
        'error': [E('v$sgastat')],
    },
    'INS-ORA-RMAN-CONF': {
        'db': 'oracle',
        'miss': [D('v$rman_configuration', [
            {'name': 'RETENTION POLICY', 'value': 'TO REDUNDANCY 2'},
            {'name': 'BACKUP OPTIMIZATION', 'value': 'ON'},
            {'name': 'COMPRESSION ALGORITHM', 'value': 'BASIC'},
        ])],
        'hit': [D('v$rman_configuration', [
            {'name': 'RETENTION POLICY', 'value': 'TO NONE'},
            {'name': 'BACKUP OPTIMIZATION', 'value': 'OFF'},
            {'name': 'COMPRESSION ALGORITHM', 'value': 'NONE'},
            {'name': 'DEFAULT DEVICE TYPE', 'value': 'UNKNOWN'},
            {'name': 'EXCLUDE', 'value': 'SYSTEM'},
        ])],
        'hit_status': 'critical',
        'error': [E('v$rman_configuration')],
    },
    'INS-ORA-AUDIT-TRAIL': {
        'db': 'oracle',
        'miss': [
            D('audit_trail', [{'value': 'DB'}]),
            D('Unified Auditing', [{'value': 'FALSE'}]),
        ],
        'hit': [
            D('audit_trail', [{'value': 'NONE'}]),
            D('Unified Auditing', [{'value': 'FALSE'}]),
        ],
        'hit_status': 'critical',
        'error': [E('audit_trail')],
    },
    'INS-ORA-PGA-LEAK': {
        'db': 'oracle',
        'miss': [D('v$pgastat', [
            {'name': 'total PGA allocated', 'value': 100},
            {'name': 'aggregate PGA target parameter', 'value': 1000},
        ])],
        'hit': [D('v$pgastat', [
            {'name': 'total PGA allocated', 'value': 1200},
            {'name': 'aggregate PGA target parameter', 'value': 1000},
        ])],
        'hit_status': 'critical',
        'error': [E('v$pgastat')],
        'extra': [{
            'name': 'memory-target',
            'steps': [
                D('v$pgastat', [
                    {'name': 'total PGA allocated', 'value': 100},
                    {'name': 'aggregate PGA target parameter', 'value': 0},
                ]),
                D('memory_target', [{'value': 5000}]),
            ],
            'status': 'skip',
            'summary': 'memory_target 托管',
        }],
    },
    'INS-ORA-IO-STAT': {
        'db': 'oracle',
        'miss': [D('v$filestat', _files([(3, 10)]))],
        'hit': [D('v$filestat', _files([(10, 100), (90, 1)]))],
        'hit_status': 'critical',
        'hit_metrics': {'hot_files': 10},
        'error': [E('v$filestat')],
    },
    'INS-ORA-EXADATA': {
        'db': 'oracle',
        'miss': [
            D('v$cell', [{'cell_name': 'cell1', 'status': 'ONLINE'}]),
            D('cell_offload_processing', [{'name': 'cell_offload_processing', 'value': 'TRUE'}]),
        ],
        'hit': [
            D('v$cell', [{'cell_name': f'c{i}', 'status': 'OFFLINE'} for i in range(5)]),
            D('cell_offload_processing', [{'name': 'cell_offload_processing', 'value': 'FALSE'}]),
        ],
        'hit_status': 'critical',
        'error': [E('v$cell')],
        'extra': [
            {
                'name': 'not-exadata',
                'steps': [{'match': 'v$cell', 'error': RuntimeError('ORA-00942: table or view does not exist')}],
                'status': 'skip',
                'summary': '不是特性关闭',
            },
            {
                'name': 'empty-cell',
                'steps': [D('v$cell', [])],
                'status': 'skip',
                'summary': '无存储单元',
            },
        ],
    },
    'INS-MYSQL-GTID': {
        'db': 'mysql', 'also': 'tdsql',
        'miss': [
            D("gtid%", [{'variable_name': 'gtid_mode', 'value': 'ON'}]),
            D('SHOW SLAVE STATUS', [{
                'retrieved_gtid_set': f'{GTID}:1-10',
                'executed_gtid_set': f'{GTID}:1-10',
                'auto_position': 1,
            }]),
        ],
        'hit': [
            D("gtid%", [{'variable_name': 'gtid_mode', 'value': 'ON'}]),
            D('SHOW SLAVE STATUS', [{
                'retrieved_gtid_set': f'{GTID}:1-10',
                'executed_gtid_set': f'{GTID}:1-4',
                'auto_position': 1,
            }]),
        ],
        'hit_status': 'critical',
        'hit_metrics': {'missing_gtids': 6},
        'error': [E("gtid%")],
        'extra': [{
            'name': 'gtid-off',
            'steps': [D("gtid%", [{'variable_name': 'gtid_mode', 'value': 'OFF'}])],
            'status': 'skip',
            'summary': 'GTID 未开启',
        }],
    },
    'INS-MYSQL-SEMI-SYNC': {
        'db': 'mysql', 'also': 'tdsql',
        'miss': [
            D("rpl_semi_sync%", [{'variable_name': 'rpl_semi_sync_master_enabled', 'value': 'ON'}]),
            D("Rpl_semi_sync%", [{'variable_name': 'Rpl_semi_sync_master_status', 'value': 'ON'}]),
        ],
        'hit': [
            D("rpl_semi_sync%", [{'variable_name': 'rpl_semi_sync_master_enabled', 'value': 'ON'}]),
            D("Rpl_semi_sync%", [{'variable_name': 'Rpl_semi_sync_master_status', 'value': 'OFF'}]),
        ],
        'hit_status': 'critical',
        'error': [E("rpl_semi_sync%")],
        'extra': [{
            'name': 'plugin-absent',
            'steps': [D("rpl_semi_sync%", [])],
            'status': 'skip',
            'summary': '未安装半同步插件',
        }],
    },
    'INS-MYSQL-EVENT': {
        'db': 'mysql', 'also': 'tdsql',
        'miss': [
            D('event_scheduler', [{'variable_name': 'event_scheduler', 'value': 'ON'}]),
            D('information_schema.events', [{'cnt': 0}]),
        ],
        'hit': [
            D('event_scheduler', [{'variable_name': 'event_scheduler', 'value': 'OFF'}]),
            D('information_schema.events', [{'cnt': 2}]),
        ],
        'hit_status': 'critical',
        'error': [E('event_scheduler')],
    },
    'INS-MYSQL-FK-CASCADE': {
        'db': 'mysql', 'also': 'tdsql',
        'miss': [D('referential_constraints', [])],
        'hit': [D('referential_constraints', [
            {'constraint_schema': 'app', 'table_name': f't{i}', 'constraint_name': f'fk{i}',
             'delete_rule': 'CASCADE', 'update_rule': 'RESTRICT'}
            for i in range(50)
        ])],
        'hit_status': 'critical',
        'error': [E('referential_constraints')],
    },
    'INS-MYSQL-TMP-DISK': {
        'db': 'mysql', 'also': 'tdsql',
        'miss': [D('Created_tmp%', [
            {'variable_name': 'Created_tmp_tables', 'value': '100'},
            {'variable_name': 'Created_tmp_disk_tables', 'value': '1'},
        ])],
        'hit': [D('Created_tmp%', [
            {'variable_name': 'Created_tmp_tables', 'value': '100'},
            {'variable_name': 'Created_tmp_disk_tables', 'value': '80'},
        ])],
        'hit_status': 'critical',
        'hit_metrics': {'tmp_disk_ratio': 80},
        'error': [E('Created_tmp%')],
    },
    'INS-PG-WAL-RECV': {
        'db': 'pgsql',
        'miss': [
            D('pg_is_in_recovery', [{'in_recovery': True}]),
            D('pg_stat_wal_receiver', [{'status': 'streaming', 'pid': 9, 'sender_host': '10.0.0.2'}]),
        ],
        'hit': [
            D('pg_is_in_recovery', [{'in_recovery': True}]),
            D('pg_stat_wal_receiver', []),
        ],
        'hit_status': 'critical',
        'error': [E('pg_is_in_recovery')],
        'extra': [{
            'name': 'primary',
            'steps': [D('pg_is_in_recovery', [{'in_recovery': False}])],
            'status': 'skip',
            'summary': '当前不是备库',
        }],
    },
    'INS-PG-INACTIVE-SLOT': {
        'db': 'pgsql',
        'miss': [D('pg_replication_slots', [{'slot_name': 's1', 'slot_type': 'physical', 'active': True}])],
        'hit': [D('pg_replication_slots', [
            {'slot_name': f's{i}', 'slot_type': 'physical', 'active': False} for i in range(5)
        ])],
        'hit_status': 'critical',
        'error': [E('pg_replication_slots')],
    },
    'INS-PG-VACUUM-FREEZE': {
        'db': 'pgsql',
        'miss': [D('relfrozenxid', [{'schema': 'app', 'name': 't', 'freeze_age': 1000}])],
        'hit': [D('relfrozenxid', [{'schema': 'app', 'name': 't', 'freeze_age': 200000000}])],
        'hit_status': 'critical',
        'error': [E('relfrozenxid')],
    },
    'INS-PG-BLOAT': {
        'db': 'pgsql',
        'miss': [D('bloat_pct', [])],
        'hit': [D('bloat_pct', [
            {'schema': 'app', 'name': f't{i}', 'kind': 'table', 'bloat_pct': 80} for i in range(50)
        ])],
        'hit_status': 'critical',
        'error': [E('bloat_pct')],
    },
    'INS-PG-EXT-OUTDATED': {
        'db': 'pgsql',
        'miss': [D('pg_extension', [{'extname': 'pg_trgm', 'extversion': '1.6', 'default_version': '1.6'}])],
        'hit': [D('pg_extension', [
            {'extname': f'ext{i}', 'extversion': '1.0', 'default_version': '1.2'} for i in range(5)
        ])],
        'hit_status': 'critical',
        'error': [E('pg_extension')],
        'extra': [{
            'name': 'no-default',
            'steps': [D('pg_extension', [{'extname': 'pg_trgm', 'extversion': '1.6', 'default_version': None}])],
            'status': 'ok',
            'summary': '不能证明过期',
        }],
    },
    'INS-PG-STMT-TIMEOUT': {
        'db': 'pgsql',
        'miss': [D('pg_settings', [{
            'name': 'statement_timeout', 'setting': '30000', 'source': 'configuration file',
            'boot_val': '0', 'reset_val': '30000',
        }])],
        'hit': [D('pg_settings', [{
            'name': 'statement_timeout', 'setting': '0', 'source': 'configuration file',
            'boot_val': '0', 'reset_val': '0',
        }])],
        'hit_status': 'critical',
        'error': [E('pg_settings')],
        'extra': [{
            'name': 'ignore-session',
            'steps': [
                D('pg_settings', [{
                    'name': 'statement_timeout', 'setting': '30000', 'source': 'session',
                    'boot_val': '0', 'reset_val': '0',
                }]),
                D('pg_file_settings', []),
                D('pg_db_role_setting', []),
            ],
            'status': 'critical',
            'summary': '均未设置 statement_timeout',
            'metrics': {'ignored_session_setting': 30000},
        }],
    },
    'INS-DM-DSC': {
        'db': 'dm',
        'miss': [
            D('DSC_NODES', [{'dsc_nodes': 2}]),
            D('V$DSC_EP_INFO', [
                {'ep_seqno': 1, 'ep_name': 'EP0', 'ep_status': 'OPEN'},
                {'ep_seqno': 2, 'ep_name': 'EP1', 'ep_status': 'OPEN'},
            ]),
        ],
        'hit': [
            D('DSC_NODES', [{'dsc_nodes': 3}]),
            D('V$DSC_EP_INFO', [
                {'ep_seqno': i, 'ep_name': f'EP{i}', 'ep_status': 'DOWN'} for i in range(3)
            ]),
        ],
        'hit_status': 'critical',
        'error': [E('DSC_NODES')],
        'extra': [{
            'name': 'single',
            'steps': [D('DSC_NODES', [{'dsc_nodes': 1}])],
            'status': 'skip',
            'summary': '非多节点 DSC',
        }],
    },
    'INS-DM-DW-STANDBY': {
        'db': 'dm',
        'miss': [
            D('ROLE$', [{'role$': 0}]),
            D('MODE$', [{'mode$': 'NORMAL'}]),
            D('V$ARCH_STATUS', [{
                'arch_type': 'REALTIME', 'arch_dest': '/arch', 'arch_status': 'VALID', 'arch_src': 'EP0',
            }]),
        ],
        'hit': [
            D('ROLE$', [{'role$': 0}]),
            D('MODE$', [{'mode$': 'PRIMARY'}]),
            D('V$ARCH_STATUS', [{
                'arch_type': 'REALTIME', 'arch_dest': '/arch', 'arch_status': 'ERROR', 'arch_src': 'EP0',
            }]),
        ],
        'hit_status': 'critical',
        'error': [E('ROLE$')],
        'extra': [{
            'name': 'no-standby',
            'steps': [
                D('ROLE$', [{'role$': 0}]),
                D('MODE$', [{'mode$': 'NORMAL'}]),
                D('V$ARCH_STATUS', []),
            ],
            'status': 'skip',
            'summary': '未配置 DataWatch',
        }],
    },
    'INS-DM-ARCH': {
        'db': 'dm',
        'miss': [
            D('ARCH_MODE', [{'arch_mode': 'Y'}]),
            D('V$ARCH_STATUS', [{
                'arch_type': 'LOCAL', 'arch_dest': '/arch', 'arch_status': 'VALID', 'arch_src': 'LOCAL',
            }]),
            D('V$ARCH_FILE', [{'cnt': 3}]),
        ],
        'hit': [
            D('ARCH_MODE', [{'arch_mode': 'N'}]),
            D('V$ARCH_STATUS', []),
            D('V$ARCH_FILE', [{'cnt': 0}]),
        ],
        'hit_status': 'critical',
        'error': [E('ARCH_MODE')],
    },
    'INS-DM-RLOG': {
        'db': 'dm',
        'miss': [D('V$ARCH_STATUS', [{
            'arch_type': 'REALTIME', 'arch_dest': '/arch', 'arch_status': 'VALID', 'arch_src': 'EP0',
        }])],
        'hit': [D('V$ARCH_STATUS', [{
            'arch_type': 'REALTIME', 'arch_dest': '/arch', 'arch_status': 'ERROR', 'arch_src': 'EP0',
        }])],
        'hit_status': 'critical',
        'error': [E('V$ARCH_STATUS')],
        'extra': [{
            'name': 'local-only',
            'steps': [D('V$ARCH_STATUS', [{
                'arch_type': 'LOCAL', 'arch_dest': '/arch', 'arch_status': 'VALID', 'arch_src': 'LOCAL',
            }])],
            'status': 'skip',
            'summary': '无实时 RLOG',
        }],
    },
    'INS-GBASE-BALANCE': {
        'db': 'gbase',
        'miss': [D('gcluster_v$node_info', _nodes([(40, 40), (50, 50)]))],
        'hit': [D('gcluster_v$node_info', _nodes([(10, 10), (90, 90)]))],
        'hit_status': 'critical',
        'error': [E('gcluster_v$node_info')],
        'extra': [
            {
                'name': 'one-node',
                'steps': [D('gcluster_v$node_info', _nodes([(10, 10)]))],
                'status': 'skip',
                'summary': '节点数不足 2',
            },
            {
                'name': 'empty',
                'steps': [D('gcluster_v$node_info', [])],
                'status': 'error',
                'summary': '不能把空结果',
            },
        ],
    },
    'INS-GBASE-DIST': {
        'db': 'gbase',
        'miss': [D('gcluster_v$table_distribution', [
            {'nodeid': '1', 'bytes': 40}, {'nodeid': '2', 'bytes': 50},
        ])],
        'hit': [D('gcluster_v$table_distribution', [
            {'nodeid': '1', 'bytes': 10}, {'nodeid': '2', 'bytes': 90},
        ])],
        'hit_status': 'critical',
        'error': [E('gcluster_v$table_distribution')],
        'extra': [{
            'name': 'empty',
            'steps': [D('gcluster_v$table_distribution', [])],
            'status': 'error',
            'summary': '不能把空结果',
        }],
    },
    'INS-GBASE-NODE-DOWN': {
        'db': 'gbase',
        'miss': [D('gcluster_v$node_info', _nodes([(1, 1), (1, 1)]))],
        'hit': [D('gcluster_v$node_info', [
            {'nodeid': str(i), 'nodename': f'n{i}', 'nodetype': 'DN',
             'status': 'OFFLINE', 'cpu_used': 0, 'mem_used': 0}
            for i in range(3)
        ])],
        'hit_status': 'critical',
        'error': [E('gcluster_v$node_info')],
        'extra': [{
            'name': 'empty',
            'steps': [D('gcluster_v$node_info', [])],
            'status': 'error',
            'summary': '不能把空结果',
        }],
    },
}


@tag('unit')
class Db023DetectorTests(TestCase):
    def test_registry_counts_and_does_not_touch_existing_keys(self):
        self.assertEqual(len(DB023_DETECTORS), 34)
        self.assertEqual(set(CASES), set(DB023_DETECTORS))
        for item_id, fn in DB023_DETECTORS.items():
            self.assertIn(item_id, DETECTOR_REGISTRY)
            self.assertIs(DETECTOR_REGISTRY[item_id], fn)
            self.assertEqual(fn.__name__, ITEMS[item_id]['detect_method'])
        for item_id in EXCLUDED:
            self.assertNotIn(item_id, DETECTOR_REGISTRY)
            self.assertNotIn(item_id, DB023_DETECTORS)
        for item_id in DB022_IDS:
            self.assertIn(item_id, DETECTOR_REGISTRY)
            self.assertNotIn(item_id, DB023_DETECTORS)
        for key, name in ORIGINAL_METHODS.items():
            self.assertEqual(DETECTOR_REGISTRY[key].__name__, name)
        direct = [item['item_id'] for item in ALL_ITEMS if item['item_id'] in DETECTOR_REGISTRY]
        self.assertTrue(set(ORIGINAL_DIRECT).issubset(direct))
        self.assertTrue(set(DB022_IDS).issubset(direct))
        self.assertEqual(len(set(DB023_DETECTORS).intersection(direct)), 34)
        base_keys = len(DETECTOR_REGISTRY) - len(DB023_DETECTORS)
        self.assertEqual(base_keys, 57)
        self.assertEqual(len(DETECTOR_REGISTRY), 91)
        self.assertEqual(len(direct), 68)

    def test_sql_constants_do_not_reuse_rejected_signals(self):
        self.assertIn('age(c.relfrozenxid)', SQL_FREEZE)
        self.assertNotIn('n_dead_tup', SQL_FREEZE)
        self.assertIn('relpages', SQL_BLOAT)
        self.assertIn('bloat_pct', SQL_BLOAT)
        self.assertNotIn('n_dead_tup', SQL_BLOAT)
        self.assertIn('V$ARCH_STATUS', SQL_DM_ARCH_STATUS)
        self.assertNotIn('V$RLOG', SQL_DM_ARCH_STATUS)
        self.assertEqual(parse_oracle_interval('+00 00:40:00'), 2400)
        self.assertEqual(gtid_missing_count(f'{GTID}:1-10', f'{GTID}:1-4'), 6)
        with self.assertRaises(ValueError):
            parse_oracle_interval('not-a-lag')

    def test_dialect_skip_does_not_connect(self):
        for item_id, fn in DB023_DETECTORS.items():
            ctx = DetectionContext(db_type='sqlite', item=ITEMS[item_id])
            with patch('monitor.db_connector.get_connector', side_effect=AssertionError('不应连接')):
                result = fn(ctx)
            self.assertEqual(result.status, 'skip', item_id)
            self.assertIn('不适用当前方言', result.summary)

    def test_each_detector_hit_miss_error_and_morphology(self):
        for item_id, spec in CASES.items():
            with self.subTest(item=item_id, case='miss'):
                result, cursor, connector = run_case(item_id, spec['db'], spec['miss'])
                self.assertEqual(result.status, 'ok', result.summary)
                self.assertEqual(result.metrics.get('threshold_band'), 'ok', result.summary)
                self.assertFalse(cursor.steps)
                self.assertTrue(connector.closed)
            with self.subTest(item=item_id, case='hit'):
                result, cursor, connector = run_case(item_id, spec['db'], spec['hit'])
                self.assertEqual(result.status, spec['hit_status'], result.summary)
                self.assertNotEqual(result.metrics.get('threshold_band'), 'ok')
                self.assertTrue(result.findings, result.summary)
                self.assertFalse(cursor.steps)
                self.assertTrue(connector.closed)
                required, banned = MUST[item_id]
                sql = '\n'.join(cursor.calls)
                for token in required:
                    self.assertIn(token, sql, item_id)
                for token in banned:
                    self.assertNotIn(token, sql, item_id)
                for key, expected in spec.get('hit_metrics', {}).items():
                    self.assertEqual(result.metrics.get(key), expected, result.summary)
            with self.subTest(item=item_id, case='error'):
                result, _cursor, connector = run_case(item_id, spec['db'], spec['error'])
                self.assertEqual(result.status, 'error', result.summary)
                self.assertIn('boom', result.error)
                self.assertIn('检测失败', result.summary)
                self.assertTrue(connector.closed)
            for extra in spec.get('extra', []):
                with self.subTest(item=item_id, case=extra['name']):
                    result, cursor, connector = run_case(item_id, spec['db'], extra['steps'])
                    self.assertEqual(result.status, extra['status'], result.summary)
                    self.assertIn(extra['summary'], result.summary)
                    self.assertFalse(cursor.steps, cursor.calls)
                    self.assertTrue(connector.closed)
                    for key, expected in extra.get('metrics', {}).items():
                        self.assertEqual(result.metrics.get(key), expected)
            if spec.get('also'):
                with self.subTest(item=item_id, case='also-dialect'):
                    result, _cursor, _connector = run_case(item_id, spec['also'], spec['miss'])
                    self.assertEqual(result.status, 'ok', result.summary)

    def test_run_for_db_skip_does_not_write_finding(self):
        db = make_db('db023-skip', db_type='mysql', port=3306)
        run_id = InspectionExecutor().run_for_db(
            db, level='monthly', item_ids=['INS-ORA-LOB-PCT'], triggered_by='unit',
        )
        run = InspectionRun.objects.get(run_id=run_id)
        self.assertEqual(run.status, 'completed')
        self.assertEqual(run.total_items, 1)
        self.assertEqual(run.executed_items, 0)
        self.assertEqual(run.info_count, 1)
        self.assertEqual(InspectionFinding.objects.filter(run=run).count(), 0)

    def test_execution_entry_still_has_both_gates(self):
        mine = make_db('db023-mine', db_type='oracle', port=1521)
        other = make_db('db023-other', db_type='oracle', port=1522)
        allowed = make_user('db023_dba', RoleCode.DBA, [Perm.DATABASES_VIEW], allowed_dbs=[mine.id])
        zero = make_user('db023_zero', RoleCode.READONLY, [], allowed_dbs=[mine.id])
        client = Client()
        login(client, zero)
        before = InspectionRun.objects.count()
        denied = client.post(
            '/api/v1/inspection/runs/trigger/',
            data=json.dumps({'config_id': mine.id, 'level': 'daily'}),
            content_type='application/json',
        )
        self.assertEqual(denied.status_code, 403)
        self.assertEqual(InspectionRun.objects.count(), before)
        login(client, allowed)
        hidden = client.post(
            '/api/v1/inspection/runs/trigger/',
            data=json.dumps({'config_id': other.id, 'level': 'daily'}),
            content_type='application/json',
        )
        self.assertEqual(hidden.status_code, 404)
        self.assertEqual(InspectionRun.objects.count(), before)
