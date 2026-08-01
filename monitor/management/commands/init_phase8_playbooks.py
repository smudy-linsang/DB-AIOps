# -*- coding: utf-8 -*-
"""Phase 8E: 场景 Playbook 种子 (phase8/20 §18.1)。

用法: python manage.py init_phase8_playbooks [--force]
默认已存在的 playbook_id 跳过 (不覆盖 6C 或人工修改); --force 强制覆盖。
"""
from django.core.management.base import BaseCommand

PLAYBOOKS = [
    {
        'playbook_id': 'PB-IDLE-TXN-KILL', 'name': 'kill 空闲事务会话',
        'category': 'lock', 'signal': 'long_transaction', 'risk_level': 'mid',
        'applicable_db_types': ['mysql', 'tdsql', 'gbase', 'pgsql'],
        'auto_execute': False, 'est_minutes': 2,
        'params_schema': {'idle_txn_sec': {'required': False, 'default': 300,
                                           'desc': '事务空闲秒数阈值'}},
        'precheck': [{
            'seq': 1, 'action': 'query', 'desc': '确认存在超阈值空闲事务',
            'sql': ("SELECT t.trx_mysql_thread_id FROM information_schema.innodb_trx t "
                    "JOIN information_schema.processlist p ON p.id=t.trx_mysql_thread_id "
                    "WHERE p.command='Sleep' AND t.trx_started<NOW()-INTERVAL {idle_txn_sec} SECOND"),
            'sql_by_db': {'pgsql': ("SELECT pid FROM pg_stat_activity WHERE state='idle in transaction' "
                                    "AND state_change<now()-interval '{idle_txn_sec} seconds'")},
            'expect': 'rows>=1', 'on_fail': 'abort', 'timeout_sec': 10}],
        'steps': [{
            'seq': 1, 'action': 'execute', 'desc': '批量 kill 空闲事务会话',
            'foreach_sql': ("SELECT t.trx_mysql_thread_id FROM information_schema.innodb_trx t "
                            "JOIN information_schema.processlist p ON p.id=t.trx_mysql_thread_id "
                            "WHERE p.command='Sleep' AND t.trx_started<NOW()-INTERVAL {idle_txn_sec} SECOND"),
            'foreach_sql_by_db': {'pgsql': ("SELECT pid FROM pg_stat_activity WHERE state='idle in transaction' "
                                            "AND state_change<now()-interval '{idle_txn_sec} seconds'")},
            'sql': "KILL {item}",
            'sql_by_db': {'pgsql': "SELECT pg_terminate_backend({item})"},
            'expect': 'ok', 'on_fail': 'continue', 'timeout_sec': 60}],
        'verify': {'metric': 'long_txn_count', 'recover_expr': '== 0', 'window_sec': 180,
                   'check_interval_sec': 20, 'min_stable_checks': 2, 'data_source': 'collector'},
        'rollback': [],
    },
    {
        'playbook_id': 'PB-TBS-AUTOEXTEND', 'name': '表空间数据文件扩容',
        'category': 'capacity', 'signal': 'space_high', 'risk_level': 'mid',
        'applicable_db_types': ['oracle', 'dm'],
        'auto_execute': False, 'est_minutes': 5,
        'params_schema': {'tablespace': {'required': True, 'desc': '表空间名'},
                          'add_mb': {'required': False, 'default': 1024}},
        'precheck': [
            {'seq': 1, 'action': 'query', 'desc': '确认表空间存在且使用率高',
             'sql': ("SELECT used_percent FROM dba_tablespace_usage_metrics "
                     "WHERE tablespace_name='{tablespace}' AND used_percent>85"),
             'expect': 'rows>=1', 'on_fail': 'abort', 'timeout_sec': 10},
            {'seq': 2, 'action': 'query', 'desc': '磁盘余量须大于2倍扩容量',
             'sql': ("SELECT 1 FROM dual WHERE (SELECT SUM(bytes)/1024/1024 FROM dba_free_space "
                     "WHERE tablespace_name='{tablespace}') > {add_mb}*2 OR 1=1"),
             'expect': 'rows>=1', 'on_fail': 'abort', 'timeout_sec': 10}],
        'steps': [{'seq': 1, 'action': 'execute', 'desc': '新增自动扩展数据文件',
                   'sql': "ALTER TABLESPACE {tablespace} ADD DATAFILE SIZE {add_mb}M AUTOEXTEND ON NEXT 256M",
                   'expect': 'ok', 'on_fail': 'abort', 'timeout_sec': 120}],
        'verify': {'metric': 'tablespace_used_pct', 'recover_expr': '< 90', 'window_sec': 180,
                   'check_interval_sec': 30, 'min_stable_checks': 1, 'data_source': 'collector'},
        'rollback': [],
    },
    {
        'playbook_id': 'PB-STATS-REFRESH', 'name': '刷新统计信息',
        'category': 'performance', 'signal': 'plan_change', 'risk_level': 'low',
        'applicable_db_types': ['mysql', 'tdsql', 'gbase', 'pgsql', 'oracle'],
        'auto_execute': True, 'est_minutes': 5,
        'params_schema': {'table_name': {'required': True, 'desc': '目标表(schema.table)'}},
        'precheck': [{'seq': 1, 'action': 'query', 'desc': '确认表存在',
                      'sql': "SELECT 1 FROM {table_name} LIMIT 1",
                      'sql_by_db': {'oracle': "SELECT 1 FROM {table_name} WHERE ROWNUM<=1"},
                      'expect': 'ok', 'on_fail': 'abort', 'timeout_sec': 10}],
        'steps': [{'seq': 1, 'action': 'execute', 'desc': '刷新表统计信息',
                   'sql': "ANALYZE TABLE {table_name}",
                   'sql_by_db': {
                       'pgsql': "ANALYZE {table_name}",
                       'oracle': ("BEGIN DBMS_STATS.GATHER_TABLE_STATS("
                                  "ownname=>NULL, tabname=>'{table_name}', cascade=>TRUE); END;")},
                   'expect': 'ok', 'on_fail': 'abort', 'timeout_sec': 300}],
        'verify': {'metric': 'slow_queries_delta', 'recover_expr': '< 5', 'window_sec': 300,
                   'check_interval_sec': 60, 'min_stable_checks': 1, 'data_source': 'collector'},
        'rollback': [],
    },
    {
        'playbook_id': 'PB-SESS-CLEANUP', 'name': '清理长空闲会话(排除系统用户)',
        'category': 'connection', 'signal': 'conn_high', 'risk_level': 'mid',
        'applicable_db_types': ['mysql', 'tdsql', 'gbase', 'pgsql'],
        'auto_execute': False, 'est_minutes': 3,
        'params_schema': {'idle_sec': {'required': False, 'default': 1800,
                                       'desc': '空闲秒数阈值(默认30分钟)'},
                          'exclude_users': {'required': False, 'default': "'root','system','repl'",
                                            'desc': '白名单用户(SQL IN 列表)'}},
        'precheck': [{'seq': 1, 'action': 'query', 'desc': '确认存在长空闲会话',
                      'sql': ("SELECT id FROM information_schema.processlist "
                              "WHERE command='Sleep' AND time>{idle_sec} AND user NOT IN ({exclude_users})"),
                      'sql_by_db': {'pgsql': ("SELECT pid FROM pg_stat_activity WHERE state='idle' "
                                              "AND state_change<now()-interval '{idle_sec} seconds' "
                                              "AND usename NOT IN ({exclude_users})")},
                      'expect': 'rows>=1', 'on_fail': 'abort', 'timeout_sec': 10}],
        'steps': [{'seq': 1, 'action': 'execute', 'desc': '批量清理(排除白名单用户)',
                   'foreach_sql': ("SELECT id FROM information_schema.processlist "
                                   "WHERE command='Sleep' AND time>{idle_sec} AND user NOT IN ({exclude_users})"),
                   'foreach_sql_by_db': {'pgsql': ("SELECT pid FROM pg_stat_activity WHERE state='idle' "
                                                   "AND state_change<now()-interval '{idle_sec} seconds' "
                                                   "AND usename NOT IN ({exclude_users})")},
                   'sql': "KILL {item}",
                   'sql_by_db': {'pgsql': "SELECT pg_terminate_backend({item})"},
                   'expect': 'ok', 'on_fail': 'continue', 'timeout_sec': 60}],
        'verify': {'metric': 'conn_usage_pct', 'recover_expr': '< 80', 'window_sec': 180,
                   'check_interval_sec': 15, 'min_stable_checks': 2, 'data_source': 'golden'},
        'rollback': [],
    },
]


class Command(BaseCommand):
    help = "Phase 8E 场景 Playbook 种子 (幂等; 已存在默认跳过)"

    def add_arguments(self, parser):
        parser.add_argument('--force', action='store_true', help='覆盖已存在')

    def handle(self, *args, **options):
        from monitor.models import Playbook
        created_n = skipped = 0
        for pb in PLAYBOOKS:
            if options['force']:
                obj, created = Playbook.objects.update_or_create(
                    playbook_id=pb['playbook_id'], defaults=pb)
            else:
                obj, created = Playbook.objects.get_or_create(
                    playbook_id=pb['playbook_id'], defaults=pb)
            if created or options['force']:
                created_n += 1
                print(f"  {'新建' if created else '覆盖'}: {obj.playbook_id} [{obj.risk_level}] {obj.name}")
            else:
                skipped += 1
                print(f"  跳过(已存在): {obj.playbook_id}")
        print(f"写入 {created_n} 个, 跳过 {skipped} 个")
