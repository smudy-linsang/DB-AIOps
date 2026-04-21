#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
数据库监控指标采集测试用例

测试覆盖：Oracle, MySQL, PostgreSQL, Dameng 四种数据库
覆盖所有采集指标类别

使用方法：
    python test_all_db_metrics.py --db oracle --host 192.168.1.100 --port 1521 --service ORCL --user system --password xxx
    python test_all_db_metrics.py --db mysql --host 192.168.1.101 --port 3306 --user root --password xxx
    python test_all_db_metrics.py --db pgsql --host 192.168.1.102 --port 5432 --user postgres --password xxx
    python test_all_db_metrics.py --db dm --host 192.168.1.103 --port 5236 --user SYSDBA --password xxx
"""

import argparse
import json
import sys
import os
from datetime import datetime

# 添加项目路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'dbmonitor.settings')

import django
django.setup()

from monitor.management.commands.start_monitor import (
    OracleChecker, MySQLChecker, PostgreSQLChecker, DamengChecker
)
from monitor.models import DatabaseConfig


class DatabaseMetricsTester:
    """数据库监控指标采集测试器"""
    
    def __init__(self, db_type, host, port, username, password, service_name=None):
        self.db_type = db_type.lower()
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.service_name = service_name
        
        # 创建模拟的 DatabaseConfig
        self.config = self._create_mock_config()
        
        # 获取对应的 Checker
        self.checker = self._get_checker()
        
        # 测试结果
        self.test_results = {
            'db_type': self.db_type,
            'test_time': datetime.now().isoformat(),
            'total_tests': 0,
            'passed': 0,
            'failed': 0,
            'errors': [],
            'warnings': [],
            'categories': {}
        }
    
    def _create_mock_config(self):
        """创建模拟的数据库配置"""
        config = type('Config', (), {})()
        config.id = 999
        config.name = f'{self.db_type}_test'
        config.db_type = self.db_type
        config.host = self.host
        config.port = self.port
        config.username = self.username
        config.service_name = self.service_name or ''
        
        def get_password():
            return self.password
        
        config.get_password = get_password
        return config
    
    def _get_checker(self):
        """获取对应的 Checker"""
        checkers = {
            'oracle': OracleChecker,
            'mysql': MySQLChecker,
            'pgsql': PostgreSQLChecker,
            'dm': DamengChecker,
        }
        checker_class = checkers.get(self.db_type)
        if not checker_class:
            raise ValueError(f"不支持的数据库类型: {self.db_type}")
        return checker_class(self)
    
    def _assert_metric(self, metric_name, value, expected_type=None, required=True, category="general"):
        """断言指标是否符合预期"""
        self.test_results['total_tests'] += 1
        
        if category not in self.test_results['categories']:
            self.test_results['categories'][category] = {'tests': 0, 'passed': 0, 'failed': 0, 'metrics': []}
        
        self.test_results['categories'][category]['tests'] += 1
        self.test_results['categories'][category]['metrics'].append(metric_name)
        
        # 检查必填指标
        if required and value is None:
            self.test_results['failed'] += 1
            self.test_results['categories'][category]['failed'] += 1
            self.test_results['errors'].append(f"[{category}] {metric_name}: 值为 None (必填)")
            return False
        
        # 检查类型
        if expected_type and value is not None:
            if not isinstance(value, expected_type):
                # 允许 int/float 互相转换
                if expected_type in (int, float) and isinstance(value, (int, float)):
                    pass
                else:
                    self.test_results['failed'] += 1
                    self.test_results['categories'][category]['failed'] += 1
                    self.test_results['errors'].append(
                        f"[{category}] {metric_name}: 类型错误, 期望 {expected_type.__name__}, 实际 {type(value).__name__}"
                    )
                    return False
        
        self.test_results['passed'] += 1
        self.test_results['categories'][category]['passed'] += 1
        return True
    
    def _warn_metric(self, metric_name, message, category="general"):
        """警告指标异常"""
        if category not in self.test_results['categories']:
            self.test_results['categories'][category] = {'tests': 0, 'passed': 0, 'failed': 0, 'metrics': []}
        self.test_results['warnings'].append(f"[{category}] {metric_name}: {message}")
    
    def test_connection(self):
        """测试数据库连接"""
        print("\n" + "="*60)
        print(f"测试 {self.db_type.upper()} 数据库连接...")
        print("="*60)
        
        try:
            conn = self.checker.get_connection(self.config)
            self._assert_metric('connection', True, bool, True, 'connection')
            print(f"[OK] 连接成功: {self.host}:{self.port}")
            conn.close()
            return True
        except Exception as e:
            self._assert_metric('connection', False, bool, True, 'connection')
            self.test_results['errors'].append(f"[connection] 连接失败: {str(e)}")
            print(f"[FAIL] 连接失败: {str(e)}")
            return False
    
    def test_basic_metrics(self, data):
        """测试基础信息指标"""
        print("\n--- 1. 基础信息 (basic) ---")
        
        # 通用指标
        self._assert_metric('version', data.get('version'), str, True, 'basic')
        self._assert_metric('uptime_seconds', data.get('uptime_seconds'), (int, float), True, 'basic')
        
        if self.db_type == 'oracle':
            self._assert_metric('instance_name', data.get('instance_name'), str, True, 'basic')
            self._assert_metric('host_name', data.get('host_name'), str, True, 'basic')
            self._assert_metric('db_name', data.get('db_name'), str, True, 'basic')
            self._assert_metric('db_unique_name', data.get('db_unique_name'), str, True, 'basic')
            self._assert_metric('open_mode', data.get('open_mode'), str, True, 'basic')
            self._assert_metric('database_role', data.get('database_role'), str, True, 'basic')
            self._assert_metric('log_mode', data.get('log_mode'), str, True, 'basic')
            print(f"  instance_name: {data.get('instance_name')}")
            print(f"  db_name: {data.get('db_name')}")
            print(f"  open_mode: {data.get('open_mode')}")
            print(f"  database_role: {data.get('database_role')}")
            
        elif self.db_type == 'mysql':
            self._assert_metric('server_id', data.get('server_id'), int, True, 'basic')
            self._assert_metric('port', data.get('port'), int, True, 'basic')
            print(f"  server_id: {data.get('server_id')}")
            print(f"  version: {data.get('version')}")
            
        elif self.db_type == 'pgsql':
            self._assert_metric('current_database', data.get('current_database'), str, True, 'basic')
            print(f"  current_database: {data.get('current_database')}")
            print(f"  version: {data.get('version')}")
            
        elif self.db_type == 'dm':
            self._assert_metric('instance_name', data.get('instance_name'), str, True, 'basic')
            self._assert_metric('db_mode', data.get('db_mode'), str, True, 'basic')
            print(f"  instance_name: {data.get('instance_name')}")
            print(f"  db_mode: {data.get('db_mode')}")
    
    def test_session_metrics(self, data):
        """测试连接会话指标"""
        print("\n--- 2. 连接与会话 (session) ---")
        
        self._assert_metric('active_connections', data.get('active_connections'), (int,), True, 'session')
        self._assert_metric('total_connections', data.get('total_connections'), (int,), True, 'session')
        self._assert_metric('max_connections', data.get('max_connections'), (int,), True, 'session')
        self._assert_metric('conn_usage_pct', data.get('conn_usage_pct'), (int, float), True, 'session')
        
        print(f"  active_connections: {data.get('active_connections')}")
        print(f"  total_connections: {data.get('total_connections')}")
        print(f"  conn_usage_pct: {data.get('conn_usage_pct')}%")
    
    def test_space_metrics(self, data):
        """测试空间使用指标"""
        print("\n--- 3. 空间使用 (space) ---")
        
        tablespaces = data.get('tablespaces', [])
        self._assert_metric('tablespaces', tablespaces, list, True, 'space')
        print(f"  tablespaces count: {len(tablespaces)}")
        
        if tablespaces:
            for ts in tablespaces[:3]:  # 只打印前3个
                print(f"    - {ts.get('name')}: {ts.get('used_pct')}% used")
        
        self._assert_metric('datafile_count', data.get('datafile_count'), (int,), False, 'space')
        print(f"  datafile_count: {data.get('datafile_count')}")
    
    def test_performance_metrics(self, data):
        """测试性能指标"""
        print("\n--- 4. 性能指标 (performance) ---")
        
        self._assert_metric('qps', data.get('qps'), (int, float), True, 'performance')
        self._assert_metric('tps', data.get('tps'), (int, float), True, 'performance')
        
        print(f"  qps: {data.get('qps')}")
        print(f"  tps: {data.get('tps')}")
        
        # Oracle 特有
        if self.db_type == 'oracle':
            self._assert_metric('logical_reads', data.get('logical_reads'), (int,), False, 'performance')
            self._assert_metric('physical_reads', data.get('physical_reads'), (int,), False, 'performance')
            self._assert_metric('physical_writes', data.get('physical_writes'), (int,), False, 'performance')
        
        # MySQL 特有
        elif self.db_type == 'mysql':
            self._assert_metric('buffer_hit_ratio', data.get('buffer_hit_ratio'), (int, float), False, 'performance')
        
        # PostgreSQL 特有
        elif self.db_type == 'postgresql':
            self._assert_metric('cache_hit_ratio', data.get('cache_hit_ratio'), (int, float), False, 'performance')
    
    def test_lock_metrics(self, data):
        """测试锁等待指标"""
        print("\n--- 5. 锁等待 (lock) ---")
        
        locks = data.get('locks', [])
        self._assert_metric('locks', locks, list, True, 'lock')
        self._assert_metric('lock_wait_count', data.get('lock_wait_count'), (int,), True, 'lock')
        
        print(f"  lock_wait_count: {data.get('lock_wait_count')}")
        print(f"  locks detail count: {len(locks)}")
    
    def test_buffer_metrics(self, data):
        """测试缓冲池指标"""
        print("\n--- 6. 缓冲池 (buffer) ---")
        
        if self.db_type == 'oracle':
            buffer_pools = data.get('buffer_pools', [])
            self._assert_metric('buffer_pools', buffer_pools, list, False, 'buffer')
            self._assert_metric('buffer_cache_mb', data.get('buffer_cache_mb'), (int, float), False, 'buffer')
            self._assert_metric('shared_pool_mb', data.get('shared_pool_mb'), (int, float), False, 'buffer')
            print(f"  buffer_cache_mb: {data.get('buffer_cache_mb')}")
            print(f"  shared_pool_mb: {data.get('shared_pool_mb')}")
            
        elif self.db_type == 'mysql':
            self._assert_metric('innodb_buffer_pool_size_mb', data.get('innodb_buffer_pool_size_mb'), (int, float), False, 'buffer')
            self._assert_metric('buffer_hit_ratio', data.get('buffer_hit_ratio'), (int, float), False, 'buffer')
            print(f"  innodb_buffer_pool_size_mb: {data.get('innodb_buffer_pool_size_mb')}")
    
    def test_transaction_metrics(self, data):
        """测试事务统计"""
        print("\n--- 7. 事务统计 (transaction) ---")
        
        self._assert_metric('active_transactions', data.get('active_transactions'), (int,), True, 'transaction')
        print(f"  active_transactions: {data.get('active_transactions')}")
    
    def test_sql_metrics(self, data):
        """测试 SQL 统计"""
        print("\n--- 8. SQL统计 (sql) ---")
        
        self._assert_metric('slow_queries_total', data.get('slow_queries_total'), (int,), False, 'sql')
        print(f"  slow_queries_total: {data.get('slow_queries_total')}")
    
    def test_replication_metrics(self, data):
        """测试复制集群指标"""
        print("\n--- 9. 复制与集群 (replication) ---")
        
        if self.db_type == 'oracle':
            # RAC 指标
            self._assert_metric('rac_instance_count', data.get('rac_instance_count'), (int,), True, 'replication')
            rac_instances = data.get('rac_instances', [])
            self._assert_metric('rac_instances', rac_instances, list, True, 'replication')
            print(f"  rac_instance_count: {data.get('rac_instance_count')}")
            print(f"  rac_instances: {len(rac_instances)} nodes")
            
            # ADG 指标
            self._assert_metric('dg_database_role', data.get('dg_database_role'), str, True, 'replication')
            self._assert_metric('dg_protection_mode', data.get('dg_protection_mode'), str, True, 'replication')
            print(f"  dg_database_role: {data.get('dg_database_role')}")
            print(f"  dg_protection_mode: {data.get('dg_protection_mode')}")
            
        elif self.db_type == 'mysql':
            self._assert_metric('slave_io_running', data.get('slave_io_running'), str, False, 'replication')
            self._assert_metric('slave_sql_running', data.get('slave_sql_running'), str, False, 'replication')
            self._assert_metric('seconds_behind_master', data.get('seconds_behind_master'), (int,), False, 'replication')
            print(f"  slave_io_running: {data.get('slave_io_running')}")
            print(f"  slave_sql_running: {data.get('slave_sql_running')}")
            print(f"  seconds_behind_master: {data.get('seconds_behind_master')}")
            
        elif self.db_type == 'pgsql':
            self._assert_metric('is_in_recovery', data.get('is_in_recovery'), bool, False, 'replication')
            replication_slots = data.get('replication_slots', [])
            self._assert_metric('replication_slots', replication_slots, list, False, 'replication')
            print(f"  is_in_recovery: {data.get('is_in_recovery')}")
            print(f"  replication_slots: {len(replication_slots)}")
            
        elif self.db_type == 'dm':
            self._assert_metric('dm_instance_mode', data.get('dm_instance_mode'), str, False, 'replication')
            print(f"  dm_instance_mode: {data.get('dm_instance_mode')}")
    
    def test_oracle_rac_metrics(self, data):
        """测试 Oracle RAC 特有指标"""
        if self.db_type != 'oracle':
            return
            
        print("\n--- 10. Oracle RAC 集群 (新增) ---")
        
        # 互联网络
        rac_interconnects = data.get('rac_interconnects', [])
        self._assert_metric('rac_interconnects', rac_interconnects, list, False, 'rac')
        self._assert_metric('ic_bytes_sent_total', data.get('ic_bytes_sent_total'), (int,), False, 'rac')
        self._assert_metric('ic_bytes_received_total', data.get('ic_bytes_received_total'), (int,), False, 'rac')
        print(f"  rac_interconnects: {len(rac_interconnects)}")
        print(f"  ic_bytes_sent_total: {data.get('ic_bytes_sent_total')}")
        
        # 缓存融合
        cache_fusion_stats = data.get('cache_fusion_stats', [])
        gc_wait_events = data.get('gc_wait_events', [])
        self._assert_metric('cache_fusion_stats', cache_fusion_stats, list, False, 'rac')
        self._assert_metric('gc_wait_events', gc_wait_events, list, False, 'rac')
        print(f"  cache_fusion_stats: {len(cache_fusion_stats)}")
        print(f"  gc_wait_events: {len(gc_wait_events)}")
    
    def test_oracle_adg_metrics(self, data):
        """测试 Oracle ADG 特有指标"""
        if self.db_type != 'oracle':
            return
            
        print("\n--- 11. Oracle ADG 监控 (新增) ---")
        
        # 延迟指标
        self._assert_metric('apply_lag', data.get('apply_lag'), str, False, 'adg')
        self._assert_metric('transport_lag', data.get('transport_lag'), str, False, 'adg')
        print(f"  apply_lag: {data.get('apply_lag')}")
        print(f"  transport_lag: {data.get('transport_lag')}")
        
        # Gap 检测
        self._assert_metric('archive_gap_count', data.get('archive_gap_count'), (int,), False, 'adg')
        archive_gap_list = data.get('archive_gap_list', [])
        self._assert_metric('archive_gap_list', archive_gap_list, list, False, 'adg')
        print(f"  archive_gap_count: {data.get('archive_gap_count')}")
        
        # 备库进程
        adg_processes = data.get('adg_processes', [])
        self._assert_metric('adg_processes', adg_processes, list, False, 'adg')
        self._assert_metric('mrp_status', data.get('mrp_status'), str, False, 'adg')
        self._assert_metric('rfs_status', data.get('rfs_status'), str, False, 'adg')
        print(f"  adg_processes: {len(adg_processes)}")
        print(f"  mrp_status: {data.get('mrp_status')}")
        print(f"  rfs_status: {data.get('rfs_status')}")
        
        # Switchover 状态
        self._assert_metric('dg_switchover_status', data.get('dg_switchover_status'), str, False, 'adg')
        print(f"  dg_switchover_status: {data.get('dg_switchover_status')}")
    
    def test_mysql_replication_metrics(self, data):
        """测试 MySQL 主从复制增强指标"""
        if self.db_type != 'mysql':
            return
            
        print("\n--- 10. MySQL 主从复制增强 (新增) ---")
        
        # GTID
        self._assert_metric('gtid_mode', data.get('gtid_mode'), str, False, 'mysql_replication')
        self._assert_metric('gtid_purged', data.get('gtid_purged'), str, False, 'mysql_replication')
        self._assert_metric('gtid_executed', data.get('gtid_executed'), str, False, 'mysql_replication')
        print(f"  gtid_mode: {data.get('gtid_mode')}")
        
        # 并行复制
        self._assert_metric('slave_parallel_workers', data.get('slave_parallel_workers'), (int,), False, 'mysql_replication')
        self._assert_metric('slave_parallel_type', data.get('slave_parallel_type'), str, False, 'mysql_replication')
        print(f"  slave_parallel_workers: {data.get('slave_parallel_workers')}")
        
        # 复制通道
        replication_channels = data.get('replication_channels', [])
        self._assert_metric('replication_channels', replication_channels, list, False, 'mysql_replication')
        print(f"  replication_channels: {len(replication_channels)}")
        
        # 中继日志
        self._assert_metric('relay_log_name', data.get('relay_log_name'), str, False, 'mysql_replication')
        self._assert_metric('exec_master_log_pos', data.get('exec_master_log_pos'), (int,), False, 'mysql_replication')
        
        # 错误信息
        self._assert_metric('last_sql_errno', data.get('last_sql_errno'), (int,), False, 'mysql_replication')
        self._assert_metric('last_io_errno', data.get('last_io_errno'), (int,), False, 'mysql_replication')
        
        # 主库信息
        self._assert_metric('master_host', data.get('master_host'), str, False, 'mysql_replication')
        self._assert_metric('master_uuid', data.get('master_uuid'), str, False, 'mysql_replication')
        print(f"  master_host: {data.get('master_host')}")
        
        # 复制健康状态
        self._assert_metric('replication_health', data.get('replication_health'), str, False, 'mysql_replication')
        print(f"  replication_health: {data.get('replication_health')}")
        
        # 过滤规则
        self._assert_metric('replicate_do_db', data.get('replicate_do_db'), str, False, 'mysql_replication')
        self._assert_metric('replicate_ignore_db', data.get('replicate_ignore_db'), str, False, 'mysql_replication')
    
    def test_dameng_dw_metrics(self, data):
        """测试 Dameng DW 集群指标"""
        if self.db_type != 'dm':
            return
            
        print("\n--- 10. Dameng DW 集群 (新增) ---")
        
        self._assert_metric('dm_instance_mode', data.get('dm_instance_mode'), str, False, 'dameng_dw')
        self._assert_metric('dm_database_mode', data.get('dm_database_mode'), str, False, 'dameng_dw')
        print(f"  dm_instance_mode: {data.get('dm_instance_mode')}")
        print(f"  dm_database_mode: {data.get('dm_database_mode')}")
        
        # 实时归档
        realtime_archive_dest = data.get('realtime_archive_dest', [])
        self._assert_metric('realtime_archive_dest', realtime_archive_dest, list, False, 'dameng_dw')
        print(f"  realtime_archive_dest: {len(realtime_archive_dest)}")
        
        # 日志同步
        rlog_sync_status = data.get('rlog_sync_status', [])
        self._assert_metric('rlog_sync_status', rlog_sync_status, list, False, 'dameng_dw')
        print(f"  rlog_sync_status: {len(rlog_sync_status)}")
        
        # 应用延迟
        self._assert_metric('apply_delay_total', data.get('apply_delay_total'), (int,), False, 'dameng_dw')
        print(f"  apply_delay_total: {data.get('apply_delay_total')}")
        
        # 目标待应用
        dest_pending = data.get('dest_pending', [])
        self._assert_metric('dest_pending', dest_pending, list, False, 'dameng_dw')
        print(f"  dest_pending: {len(dest_pending)}")
        
        # 健康状态
        self._assert_metric('dw_replication_health', data.get('dw_replication_health'), str, False, 'dameng_dw')
        print(f"  dw_replication_health: {data.get('dw_replication_health')}")
    
    def test_dameng_dsc_metrics(self, data):
        """测试 Dameng DSC 集群指标"""
        if self.db_type != 'dm':
            return
            
        print("\n--- 11. Dameng DSC 集群 (新增) ---")
        
        # 集群信息
        dsc_cluster_info = data.get('dsc_cluster_info', [])
        self._assert_metric('dsc_cluster_info', dsc_cluster_info, list, False, 'dameng_dsc')
        self._assert_metric('dsc_node_count', data.get('dsc_node_count'), (int,), False, 'dameng_dsc')
        self._assert_metric('dsc_primary_node', data.get('dsc_primary_node'), str, False, 'dameng_dsc')
        print(f"  dsc_node_count: {data.get('dsc_node_count')}")
        print(f"  dsc_primary_node: {data.get('dsc_primary_node')}")
        
        # 实例详情
        dsc_instances = data.get('dsc_instances', [])
        self._assert_metric('dsc_instances', dsc_instances, list, False, 'dameng_dsc')
        print(f"  dsc_instances: {len(dsc_instances)}")
        
        # 全局锁
        dsc_global_latches = data.get('dsc_global_latches', [])
        self._assert_metric('dsc_global_latches', dsc_global_latches, list, False, 'dameng_dsc')
        self._assert_metric('dsc_lock_contention_count', data.get('dsc_lock_contention_count'), (int,), False, 'dameng_dsc')
        print(f"  dsc_lock_contention_count: {data.get('dsc_lock_contention_count')}")
        
        # 健康状态
        self._assert_metric('dsc_cluster_health', data.get('dsc_cluster_health'), str, False, 'dameng_dsc')
        print(f"  dsc_cluster_health: {data.get('dsc_cluster_health')}")
    
    def test_config_metrics(self, data):
        """测试配置参数"""
        print("\n--- 12. 配置参数 (config) ---")
        
        config_params = data.get('config_params', {})
        self._assert_metric('config_params', config_params, dict, True, 'config')
        print(f"  config_params count: {len(config_params)}")
        if config_params:
            for k, v in list(config_params.items())[:5]:
                print(f"    - {k}: {v}")
    
    def test_log_metrics(self, data):
        """测试日志统计"""
        print("\n--- 13. 日志统计 (log) ---")
        
        if self.db_type == 'oracle':
            self._assert_metric('log_current', data.get('log_current'), (int,), False, 'log')
            self._assert_metric('log_active', data.get('log_active'), (int,), False, 'log')
            self._assert_metric('archive_logs_1day', data.get('archive_logs_1day'), (int,), False, 'log')
            print(f"  archive_logs_1day: {data.get('archive_logs_1day')}")
            
        elif self.db_type == 'mysql':
            self._assert_metric('binlog_file', data.get('binlog_file'), str, False, 'log')
            self._assert_metric('binlog_position', data.get('binlog_position'), (int,), False, 'log')
            print(f"  binlog_file: {data.get('binlog_file')}")
    
    def run_all_tests(self):
        """运行所有测试"""
        print("\n" + "#"*60)
        print(f"# 数据库监控指标采集测试 - {self.db_type.upper()}")
        print("#"*60)
        
        # 测试连接
        if not self.test_connection():
            print("\n[X] 连接失败，无法继续测试")
            return self.test_results
        
        # 采集指标
        print("\n开始采集指标...")
        try:
            conn = self.checker.get_connection(self.config)
            data = self.checker.collect_metrics(self.config, conn)
            conn.close()
        except Exception as e:
            self.test_results['errors'].append(f"指标采集失败: {str(e)}")
            print(f"\n[X] 指标采集失败: {str(e)}")
            return self.test_results
        
        print(f"[OK] 指标采集成功，共 {len(data)} 个顶级指标\n")
        
        # 打印所有顶级指标
        print("所有采集的指标:")
        for key in sorted(data.keys()):
            value_type = type(data[key]).__name__
            if isinstance(data[key], (list, dict)):
                value_info = f"{value_type} ({len(data[key])} items)"
            else:
                value_info = str(data[key])[:50]
            print(f"  - {key}: {value_info}")
        
        # 执行各类测试
        self.test_basic_metrics(data)
        self.test_session_metrics(data)
        self.test_space_metrics(data)
        self.test_performance_metrics(data)
        self.test_lock_metrics(data)
        self.test_buffer_metrics(data)
        self.test_transaction_metrics(data)
        self.test_sql_metrics(data)
        self.test_replication_metrics(data)
        self.test_config_metrics(data)
        self.test_log_metrics(data)
        
        # 特定数据库测试
        if self.db_type == 'oracle':
            self.test_oracle_rac_metrics(data)
            self.test_oracle_adg_metrics(data)
        elif self.db_type == 'mysql':
            self.test_mysql_replication_metrics(data)
        elif self.db_type == 'dm':
            self.test_dameng_dw_metrics(data)
            self.test_dameng_dsc_metrics(data)
        
        return self.test_results
    
    def print_summary(self):
        """打印测试摘要"""
        print("\n" + "="*60)
        print("测试结果摘要")
        print("="*60)
        
        print(f"\n总计: {self.test_results['total_tests']} 项测试")
        print(f"  [PASS] 通过: {self.test_results['passed']}")
        print(f"  [FAIL] 失败: {self.test_results['failed']}")
        
        print("\n各类别测试结果:")
        for category, stats in self.test_results['categories'].items():
            print(f"\n  [{category}]")
            print(f"    测试: {stats['tests']}, 通过: {stats['passed']}, 失败: {stats['failed']}")
            print(f"    指标: {', '.join(stats['metrics'][:10])}")
            if len(stats['metrics']) > 10:
                print(f"           ... 还有 {len(stats['metrics']) - 10} 项")
        
        if self.test_results['errors']:
            print("\n错误详情:")
            for error in self.test_results['errors'][:20]:
                print(f"  [X] {error}")
            if len(self.test_results['errors']) > 20:
                print(f"  ... 还有 {len(self.test_results['errors']) - 20} 项错误")
        
        if self.test_results['warnings']:
            print("\n警告详情:")
            for warning in self.test_results['warnings'][:10]:
                print(f"  ! {warning}")
            if len(self.test_results['warnings']) > 10:
                print(f"  ... 还有 {len(self.test_results['warnings']) - 10} 项警告")
        
        return self.test_results['failed'] == 0


def main():
    parser = argparse.ArgumentParser(description='数据库监控指标采集测试')
    parser.add_argument('--db', required=True, choices=['oracle', 'mysql', 'pgsql', 'dm', 'gbase', 'tdsql'],
                        help='数据库类型 (pgsql=PostgreSQL, dm=达梦)')
    parser.add_argument('--host', required=True, help='数据库主机')
    parser.add_argument('--port', required=True, type=int, help='数据库端口')
    parser.add_argument('--user', required=True, help='用户名')
    parser.add_argument('--password', required=True, help='密码')
    parser.add_argument('--service', help='服务名/数据库名 (Oracle: service_name, MySQL/PG: database)')
    
    args = parser.parse_args()
    
    tester = DatabaseMetricsTester(
        db_type=args.db,
        host=args.host,
        port=args.port,
        username=args.user,
        password=args.password,
        service_name=args.service
    )
    
    tester.run_all_tests()
    success = tester.print_summary()
    
    # 输出 JSON 结果
    output_file = f"test_results_{args.db}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(tester.test_results, f, ensure_ascii=False, indent=2)
    print(f"\n详细结果已保存到: {output_file}")
    
    sys.exit(0 if success else 1)


if __name__ == '__main__':
    main()
