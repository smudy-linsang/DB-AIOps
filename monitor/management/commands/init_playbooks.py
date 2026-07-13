# -*- coding: utf-8 -*-
"""Phase 6C: 首批 Playbook 初始化 (phase6/30 §6)。

用法: python manage.py init_playbooks [--force]
"""
from django.core.management.base import BaseCommand

# 8 类首批 Playbook。SQL 按库用 sql_by_db 覆盖, 占位符由 PlaybookRun.params 渲染。
PLAYBOOKS = [
    {
        'playbook_id': 'PB-LOCK-KILL-BLOCKER', 'name': 'kill 阻塞源会话',
        'category': 'lock', 'signal': 'blocked_session', 'risk_level': 'mid',
        'applicable_db_types': ['mysql', 'tdsql', 'gbase', 'pgsql', 'oracle', 'dm'],
        'auto_execute': False, 'est_minutes': 2,
        'params_schema': {'blocker_id': {'required': True, 'desc': '阻塞源会话ID'}},
        'precheck': [{
            'seq': 1, 'action': 'query', 'desc': '确认 blocker 会话仍存在',
            'sql': "SELECT 1 FROM information_schema.processlist WHERE id={blocker_id}",
            'sql_by_db': {
                'pgsql': "SELECT 1 FROM pg_stat_activity WHERE pid={blocker_id}",
                'oracle': "SELECT 1 FROM v$session WHERE sid={blocker_id}"},
            'expect': 'rows>=1', 'on_fail': 'abort', 'timeout_sec': 10}],
        'steps': [{
            'seq': 1, 'action': 'execute', 'desc': 'kill 阻塞源会话',
            'sql': "KILL {blocker_id}",
            'sql_by_db': {
                'pgsql': "SELECT pg_terminate_backend({blocker_id})",
                'oracle': "ALTER SYSTEM KILL SESSION '{blocker_id}' IMMEDIATE"},
            'expect': 'ok', 'on_fail': 'abort', 'timeout_sec': 15}],
        'verify': {'metric': 'blocked_sessions', 'recover_expr': '== 0',
                   'window_sec': 300, 'check_interval_sec': 15, 'min_stable_checks': 3,
                   'data_source': 'ash'},
        'rollback': [],
    },
    {
        'playbook_id': 'PB-CONN-CLEAN-IDLE', 'name': '清理空闲会话',
        'category': 'connection', 'signal': 'conn_high', 'risk_level': 'mid',
        'applicable_db_types': ['mysql', 'tdsql', 'gbase', 'pgsql', 'oracle'],
        'auto_execute': False, 'est_minutes': 3,
        'precheck': [{'seq': 1, 'action': 'query', 'desc': '确认存在长空闲会话',
                      'sql': "SELECT count(*) c FROM information_schema.processlist WHERE command='Sleep' AND time>600",
                      'sql_by_db': {'pgsql': "SELECT count(*) FROM pg_stat_activity WHERE state='idle' AND state_change<now()-interval '10 min'"},
                      'expect': 'rows>=1', 'on_fail': 'abort', 'timeout_sec': 10}],
        'steps': [{'seq': 1, 'action': 'execute', 'desc': '清理空闲>10min 会话',
                   'sql': "KILL (SELECT id FROM information_schema.processlist WHERE command='Sleep' AND time>600 LIMIT 1)",
                   'sql_by_db': {'pgsql': "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE state='idle' AND state_change<now()-interval '10 min'"},
                   'expect': 'ok', 'on_fail': 'continue', 'timeout_sec': 20}],
        'verify': {'metric': 'conn_usage_pct', 'recover_expr': '< 80', 'window_sec': 180,
                   'check_interval_sec': 15, 'min_stable_checks': 2, 'data_source': 'golden'},
        'rollback': [],
    },
    {
        'playbook_id': 'PB-SPACE-EXTEND', 'name': '扩容表空间',
        'category': 'capacity', 'signal': 'space_high', 'risk_level': 'mid',
        'applicable_db_types': ['oracle', 'dm'], 'auto_execute': False, 'est_minutes': 5,
        'params_schema': {'tablespace': {'required': True}, 'add_mb': {'required': False, 'default': 1024}},
        'precheck': [{'seq': 1, 'action': 'query', 'desc': '确认表空间使用率高',
                      'sql': "SELECT used_percent FROM dba_tablespace_usage_metrics WHERE tablespace_name='{tablespace}'",
                      'expect': 'rows>=1', 'on_fail': 'abort', 'timeout_sec': 10}],
        'steps': [{'seq': 1, 'action': 'execute', 'desc': '增加数据文件',
                   'sql': "ALTER TABLESPACE {tablespace} ADD DATAFILE SIZE {add_mb}M AUTOEXTEND ON",
                   'expect': 'ok', 'on_fail': 'abort', 'timeout_sec': 60}],
        'verify': {'metric': 'tablespace_used_pct', 'recover_expr': '< 90', 'window_sec': 120,
                   'check_interval_sec': 20, 'min_stable_checks': 1, 'data_source': 'collector'},
        'rollback': [],
    },
    {
        'playbook_id': 'PB-REPL-RESTART', 'name': '重启复制线程',
        'category': 'replication', 'signal': 'repl_broken', 'risk_level': 'mid',
        'applicable_db_types': ['mysql', 'tdsql', 'gbase'], 'auto_execute': False, 'est_minutes': 3,
        'precheck': [{'seq': 1, 'action': 'query', 'desc': '确认复制已停止',
                      'sql': "SHOW SLAVE STATUS", 'expect': 'rows>=1', 'on_fail': 'abort', 'timeout_sec': 10}],
        'steps': [
            {'seq': 1, 'action': 'execute', 'desc': '停止复制', 'sql': "STOP SLAVE", 'expect': 'ok', 'on_fail': 'continue', 'timeout_sec': 15},
            {'seq': 2, 'action': 'execute', 'desc': '启动复制', 'sql': "START SLAVE", 'expect': 'ok', 'on_fail': 'abort', 'timeout_sec': 15}],
        'verify': {'metric': 'repl_running', 'recover_expr': '== 1', 'window_sec': 180,
                   'check_interval_sec': 20, 'min_stable_checks': 2, 'data_source': 'collector'},
        'rollback': [],
    },
    {
        'playbook_id': 'PB-CONFIG-ROLLBACK', 'name': '回滚漂移参数',
        'category': 'config', 'signal': 'config_drift', 'risk_level': 'mid',
        'applicable_db_types': ['mysql', 'tdsql', 'gbase', 'pgsql', 'oracle'],
        'auto_execute': False, 'est_minutes': 3,
        'params_schema': {'param': {'required': True}, 'old_value': {'required': True}},
        'precheck': [{'seq': 1, 'action': 'query', 'desc': '读取当前参数值',
                      'sql': "SHOW VARIABLES LIKE '{param}'", 'expect': 'rows>=1', 'on_fail': 'abort', 'timeout_sec': 10}],
        'steps': [{'seq': 1, 'action': 'execute', 'desc': '回滚参数到原值',
                   'sql': "SET GLOBAL {param}={old_value}",
                   'sql_by_db': {'oracle': "ALTER SYSTEM SET {param}={old_value}"},
                   'expect': 'ok', 'on_fail': 'abort', 'timeout_sec': 15}],
        'verify': {'metric': '', 'recover_expr': '', 'window_sec': 300,
                   'check_interval_sec': 30, 'min_stable_checks': 1, 'data_source': 'collector'},
        'rollback': [{'seq': 1, 'action': 'execute', 'desc': '恢复到变更后的值', 'sql': "SET GLOBAL {param}={new_value}"}],
    },
    {
        'playbook_id': 'PB-SLOW-KILL-TOPSQL', 'name': 'kill 慢SQL会话+索引建议',
        'category': 'performance', 'signal': 'slow_surge', 'risk_level': 'mid',
        'applicable_db_types': ['mysql', 'tdsql', 'gbase', 'pgsql'], 'auto_execute': False, 'est_minutes': 3,
        'params_schema': {'session_id': {'required': True}},
        'precheck': [{'seq': 1, 'action': 'query', 'desc': '确认慢会话仍在运行',
                      'sql': "SELECT 1 FROM information_schema.processlist WHERE id={session_id} AND command!='Sleep'",
                      'expect': 'rows>=1', 'on_fail': 'abort', 'timeout_sec': 10}],
        'steps': [{'seq': 1, 'action': 'execute', 'desc': 'kill 慢SQL会话',
                   'sql': "KILL {session_id}",
                   'sql_by_db': {'pgsql': "SELECT pg_terminate_backend({session_id})"},
                   'expect': 'ok', 'on_fail': 'abort', 'timeout_sec': 15}],
        'verify': {'metric': 'slow_queries', 'recover_expr': '< 5', 'window_sec': 180,
                   'check_interval_sec': 30, 'min_stable_checks': 1, 'data_source': 'collector'},
        'rollback': [],
    },
    {
        'playbook_id': 'PB-DEADLOCK-ADVISE', 'name': '死锁分析(仅建议)',
        'category': 'lock', 'signal': 'deadlock_surge', 'risk_level': 'low',
        'applicable_db_types': ['mysql', 'tdsql', 'gbase', 'pgsql', 'oracle'],
        'auto_execute': True, 'est_minutes': 1,
        'precheck': [], 'steps': [{
            'seq': 1, 'action': 'query', 'desc': '采集死锁信息(只读)',
            'sql': "SHOW ENGINE INNODB STATUS", 'expect': 'ok', 'on_fail': 'continue', 'timeout_sec': 10}],
        'verify': {'metric': '', 'recover_expr': '', 'window_sec': 60, 'data_source': 'collector'},
        'rollback': [],
    },
    {
        'playbook_id': 'PB-DOWN-GUIDE', 'name': '宕机处置指引(不自动执行)',
        'category': 'availability', 'signal': 'instance_down', 'risk_level': 'high',
        'applicable_db_types': ['mysql', 'tdsql', 'gbase', 'pgsql', 'oracle', 'dm'],
        'auto_execute': False, 'est_minutes': 10,
        'precheck': [], 'steps': [{
            'seq': 1, 'action': 'query', 'desc': '人工确认实例进程/网络(占位, 需人工)',
            'sql': "SELECT 1", 'expect': 'ok', 'on_fail': 'continue', 'timeout_sec': 5}],
        'verify': {'metric': 'instance_up', 'recover_expr': '== 1', 'window_sec': 300, 'data_source': 'golden'},
        'rollback': [],
    },
]


class Command(BaseCommand):
    help = "初始化首批 Playbook (8 类)"

    def add_arguments(self, parser):
        parser.add_argument('--force', action='store_true', help='覆盖已存在')

    def handle(self, *args, **options):
        from monitor.models import Playbook
        n = 0
        for pb in PLAYBOOKS:
            obj, created = Playbook.objects.update_or_create(
                playbook_id=pb['playbook_id'], defaults=pb)
            n += 1
            print(f"  {'新建' if created else '更新'}: {obj.playbook_id} [{obj.risk_level}] {obj.name}")
        print(f"共 {n} 个 Playbook")
