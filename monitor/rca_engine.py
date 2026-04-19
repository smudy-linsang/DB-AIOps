"""
故障根因分析引擎 v2.0 (Phase 2 增强版 - RCA - Root Cause Analysis)

功能:
- 基于规则的故障诊断 (10+ 条规则)
- 关联多个指标定位问题源头
- 提供处理建议
- 支持复合因果链推导

设计文档参考: DB_AIOps_DESIGN.md 3.6 节
"""

import json
from datetime import datetime, timedelta
from typing import Dict, List, Tuple, Optional, Any
from monitor.models import MonitorLog, DatabaseConfig


# ==========================================
# 辅助函数
# ==========================================

def _check_shard_imbalance(shards: List[Dict]) -> bool:
    """检查分片数据是否不均衡"""
    if len(shards) < 2:
        return False
    
    sizes = [s.get('data_size_mb', 0) for s in shards]
    if not sizes or max(sizes) == 0:
        return False
    
    avg_size = sum(sizes) / len(sizes)
    if avg_size == 0:
        return False
    
    max_deviation = max(abs(s - avg_size) / avg_size for s in sizes)
    
    return max_deviation > 0.5  # 偏差超过 50% 认为不均衡


# ==========================================
# RCA 规则库
# ==========================================

RULES = [
    # --- 原有 6 条规则 ---
    {
        'id': 'R001',
        'name': '连接数泄漏',
        'condition': lambda d: d.get('conn_usage_pct', 0) > 80 and d.get('qps', 0) < 10,
        'description': '连接数使用率高但 QPS 很低，可能存在连接泄漏',
        'suggestions': [
            '检查应用程序是否有未关闭的数据库连接',
            '查看连接池配置是否合理',
            '使用 "SHOW PROCESSLIST" (MySQL) 或 "SELECT * FROM v$session" (Oracle) 查看活跃会话'
        ],
        'severity_default': 'warning',
    },
    {
        'id': 'R002',
        'name': '慢查询导致锁等待',
        'condition': lambda d: len(d.get('locks', [])) > 0 and d.get('slow_queries_active', 0) > 0,
        'description': '检测到锁等待同时存在活跃慢查询，慢查询可能是阻塞源头',
        'suggestions': [
            '立即查看并终止阻塞会话',
            '分析慢查询 SQL 并进行优化',
            '考虑添加索引或优化表结构'
        ],
        'severity_default': 'warning',
    },
    {
        'id': 'R003',
        'name': '表空间容量不足',
        'condition': lambda d: any((t.get('used_pct') or 0) > 90 for t in d.get('tablespaces', [])),
        'description': '表空间使用率超过 90%，存在写满风险',
        'suggestions': [
            '立即扩容数据文件',
            '清理过期数据或归档历史数据',
            '删除不必要的索引或临时表'
        ],
        'severity_default': 'warning',
    },
    {
        'id': 'R004',
        'name': 'QPS 突降',
        'condition': lambda d: d.get('qps', 0) < 5 and d.get('active_connections', 0) > 50,
        'description': '连接数正常但 QPS 极低，可能应用层出现问题',
        'suggestions': [
            '检查应用程序是否正常运行',
            '查看网络连接是否有问题',
            '确认是否有大规模事务未提交'
        ],
        'severity_default': 'warning',
    },
    {
        'id': 'R005',
        'name': '集群节点异常',
        'condition': lambda d: any(n.get('status') != 'ONLINE' for n in d.get('cluster_nodes', [])),
        'description': 'Gbase/TDSQL 集群中有节点状态异常',
        'suggestions': [
            '立即检查异常节点的日志',
            '确认节点是否宕机或网络隔离',
            '准备启动备用节点或进行故障转移'
        ],
        'severity_default': 'critical',
    },
    {
        'id': 'R006',
        'name': '分片数据不均衡',
        'condition': lambda d: len(d.get('shards', [])) > 1 and _check_shard_imbalance(d.get('shards', [])),
        'description': 'TDSQL 分片间数据量差异过大，可能影响性能',
        'suggestions': [
            '检查分片键选择是否合理',
            '考虑重新平衡分片数据',
            '评估是否需要增加分片数量'
        ],
        'severity_default': 'warning',
    },
    
    # --- Phase 2 新增 4 条规则 (R007-R010) ---
    {
        'id': 'R007',
        'name': '日志生成速率突增',
        'condition': lambda d: _check_log_burst(d),
        'description': 'Redo/Binlog 生成速率异常突增，可能存在计划外大批量 DML 操作',
        'suggestions': [
            '确认是否有计划内批量作业在执行',
            '若非计划内操作，立即定位来源会话',
            '检查是否有异常的数据写入行为'
        ],
        'severity_default': 'warning',
    },
    {
        'id': 'R008',
        'name': '实例状态异常',
        'condition': lambda d: d.get('current_status') == 'DOWN',
        'description': '数据库实例当前处于 DOWN 状态',
        'suggestions': [
            '立即检查数据库服务状态',
            '查看数据库 Alert Log / 错误日志',
            '确认是否为计划内停机或意外故障'
        ],
        'severity_default': 'critical',
    },
    {
        'id': 'R009',
        'name': '连接来源集中度过高',
        'condition': lambda d: d.get('conn_usage_pct', 0) > 80 and _check_conn_source_concentration(d),
        'description': '连接数使用率高且连接来自少数几个来源 IP，可能是特定应用连接池配置异常',
        'suggestions': [
            '按来源 IP 分析连接分布',
            '检查问题 IP 对应的应用程序连接池配置',
            '考虑限制特定 IP 的最大连接数'
        ],
        'severity_default': 'warning',
    },
    {
        'id': 'R010',
        'name': '日志写入瓶颈',
        'condition': lambda d: _check_log_sync_wait(d),
        'description': '等待事件显示 log file sync 成为瓶颈，Redo Log 写入可能存在问题',
        'suggestions': [
            '检查 Redo Log 所在磁盘的 I/O 延迟',
            '评估是否需要将 Redo Log 迁移到更快的存储',
            '检查是否存在大批量提交操作'
        ],
        'severity_default': 'warning',
    },
]


def _check_log_burst(data: Dict) -> bool:
    """检测日志生成速率是否突增 (简化版，需结合历史基线)"""
    # 简化实现：binlog_size_delta 或 redo_rate 字段超过阈值
    # 实际应使用基线对比
    if 'binlog_size_delta' in data and data['binlog_size_delta'] > 1000:  # MB/hour
        return True
    if 'redo_rate_mb_per_hour' in data and data['redo_rate_mb_per_hour'] > 500:
        return True
    return False


def _check_conn_source_concentration(data: Dict) -> bool:
    """检测连接来源是否集中于少数 IP"""
    # 需要连接来源信息，简化判断
    if 'top_conn_sources' in data:
        sources = data.get('top_conn_sources', [])
        if len(sources) <= 3 and sources:
            return True
    return False


def _check_log_sync_wait(data: Dict) -> bool:
    """检测是否处于 log file sync 等待"""
    # 简化实现：基于 top_wait_event 字段
    if 'top_wait_event' in data and 'log file sync' in str(data.get('top_wait_event', '')):
        return True
    return False


# ==========================================
# 复合因果链定义
# ==========================================

COMPOUND_RULES = [
    {
        'id': 'CR001',
        'name': '连接堆积引发锁竞争',
        'requires': ['R001', 'R002'],  # 同时触发 R001 和 R002
        'description': '连接数泄漏同时伴随锁等待，可能形成复合故障',
        'priority_boost': 'P1',  # 优先级提升
        'suggestions': [
            '这是复合故障场景，应优先处理',
            '先解决连接泄漏问题 (R001)',
            '同时清理锁等待会话 (R002)',
            '检查应用层连接管理逻辑'
        ]
    },
]


class RCAEngine:
    """
    根因分析引擎 v2.0
    
    支持:
    - 10+ 条诊断规则
    - 复合因果链推导
    - 严重程度动态计算
    - 修复命令生成
    """
    
    def __init__(self, config: DatabaseConfig):
        self.config = config
        self.rules = RULES
        self.compound_rules = COMPOUND_RULES
    
    def get_latest_data(self) -> Optional[Dict]:
        """获取最新一次监控数据"""
        latest_log = MonitorLog.objects.filter(
            config=self.config,
            status='UP'
        ).order_by('-create_time').first()
        
        if not latest_log:
            return None
        
        try:
            data = json.loads(latest_log.message)
            data['current_status'] = latest_log.status
            return data
        except:
            return None
    
    def get_recent_logs(self, limit: int = 10) -> List[Dict]:
        """获取最近 N 条监控日志"""
        logs = MonitorLog.objects.filter(
            config=self.config
        ).order_by('-create_time')[:limit]
        
        result = []
        for log in logs:
            try:
                data = json.loads(log.message)
                data['current_status'] = log.status
                result.append({
                    'time': log.create_time,
                    'status': log.status,
                    'data': data
                })
            except:
                pass
        
        return result
    
    def analyze(self, current_data: Dict = None) -> Dict[str, Any]:
        """
        执行根因分析
        
        参数:
            current_data: 当前监控数据 (如果不传则自动获取最新数据)
        
        返回:
            {
                'timestamp': 分析时间,
                'config': 数据库配置,
                'diagnoses': [诊断结果列表],
                'compound_diagnoses': [复合因果诊断],
                'summary': 总结文本
            }
        """
        if current_data is None:
            current_data = self.get_latest_data()
        
        if not current_data:
            return {'error': '无可用数据', 'config_name': self.config.name}
        
        diagnoses = []
        triggered_rule_ids = set()
        
        # 遍历所有规则
        for rule in self.rules:
            try:
                if rule['condition'](current_data):
                    severity = self._calculate_severity(rule, current_data)
                    diagnoses.append({
                        'rule_id': rule['id'],
                        'name': rule['name'],
                        'description': rule['description'],
                        'severity': severity,
                        'suggestions': rule['suggestions']
                    })
                    triggered_rule_ids.add(rule['id'])
            except Exception as e:
                # 规则执行失败，跳过
                pass
        
        # 检查复合因果链
        compound_diagnoses = []
        for compound in self.compound_rules:
            if all(req in triggered_rule_ids for req in compound['requires']):
                compound_diagnoses.append({
                    'rule_id': compound['id'],
                    'name': compound['name'],
                    'description': compound['description'],
                    'requires': compound['requires'],
                    'priority_boost': compound['priority_boost'],
                    'suggestions': compound['suggestions']
                })
        
        # 生成总结
        summary = self._generate_summary(diagnoses, compound_diagnoses, current_data)
        
        return {
            'timestamp': datetime.now().isoformat(),
            'config_name': self.config.name,
            'db_type': self.config.db_type,
            'diagnoses': diagnoses,
            'compound_diagnoses': compound_diagnoses,
            'summary': summary,
            'current_metrics': self._extract_current_metrics(current_data),
            'rules_total': len(self.rules),
            'rules_triggered': len(diagnoses),
        }
    
    def _calculate_severity(self, rule: Dict, data: Dict) -> str:
        """计算问题严重程度"""
        rule_id = rule['id']
        
        # R002: 锁等待
        if rule_id == 'R002':
            lock_count = len(data.get('locks', []))
            if lock_count > 5:
                return 'critical'
            elif lock_count > 0:
                return 'warning'
        
        # R003: 表空间
        if rule_id == 'R003':
            max_tbs = max([t.get('used_pct', 0) for t in data.get('tablespaces', [])], default=0)
            if max_tbs > 95:
                return 'critical'
            elif max_tbs > 90:
                return 'warning'
        
        # R005: 集群节点
        if rule_id == 'R005':
            offline_count = sum(1 for n in data.get('cluster_nodes', []) if n.get('status') != 'ONLINE')
            if offline_count > 0:
                return 'critical'
        
        # R008: 实例 DOWN
        if rule_id == 'R008':
            return 'critical'
        
        # 默认
        return rule.get('severity_default', 'warning')
    
    def _generate_summary(self, diagnoses: List[Dict], compound_diagnoses: List[Dict], data: Dict) -> str:
        """生成问题总结"""
        parts = []
        
        # 复合故障优先
        if compound_diagnoses:
            for cd in compound_diagnoses:
                parts.append(f"🔴 [复合故障] {cd['name']} (优先级提升至 {cd['priority_boost']})")
        
        # 按严重程度分类
        critical = [d for d in diagnoses if d['severity'] == 'critical']
        warning = [d for d in diagnoses if d['severity'] == 'warning']
        
        if critical:
            parts.append(f"🔴 发现 {len(critical)} 个严重问题")
            for d in critical[:3]:
                parts.append(f"   - {d['name']}")
        
        if warning:
            parts.append(f"🟠 发现 {len(warning)} 个警告")
            for d in warning[:3]:
                parts.append(f"   - {d['name']}")
        
        if not parts:
            return "✅ 未检测到明显问题，数据库运行正常。"
        
        return " | ".join(parts) if len(parts) <= 2 else "\n".join(parts)
    
    def _extract_current_metrics(self, data: Dict) -> Dict:
        """提取当前指标摘要"""
        return {
            'status': data.get('current_status', 'UNKNOWN'),
            'connections': data.get('active_connections', 0),
            'conn_usage_pct': data.get('conn_usage_pct', 0),
            'qps': data.get('qps', 0),
            'locks_count': len(data.get('locks', [])),
            'slow_queries': data.get('slow_queries_active', 0),
            'tbs_high_count': len([t for t in data.get('tablespaces', []) if t.get('used_pct', 0) > 90]),
        }
    
    def generate_fix_commands(self, diagnosis: Dict) -> List[Dict]:
        """
        根据诊断结果生成修复命令
        
        参数:
            diagnosis: 单条诊断结果
        
        返回:
            commands: [{database_type, command, description, risk_level}]
        """
        commands = []
        db_type = self.config.db_type
        
        if diagnosis['rule_id'] == 'R002':  # 锁等待
            if db_type == 'oracle':
                commands.append({
                    'db_type': 'oracle',
                    'command': "SELECT 'ALTER SYSTEM KILL SESSION ''' || sid || ',' || serial# || '''' IMMEDIATE;' FROM v$session WHERE blocking_session IS NOT NULL;",
                    'description': 'Kill 阻塞会话 (Oracle)',
                    'risk_level': 'high'
                })
            elif db_type in ('mysql', 'tdsql', 'gbase'):
                commands.append({
                    'db_type': db_type,
                    'command': "SELECT CONCAT('KILL ', id, ';') FROM information_schema.processlist WHERE command != 'Sleep' AND time > 60;",
                    'description': 'Kill 阻塞线程 (MySQL/TDSQL)',
                    'risk_level': 'high'
                })
            elif db_type == 'pgsql':
                commands.append({
                    'db_type': 'postgresql',
                    'command': "SELECT 'SELECT pg_terminate_backend(' || pid || ');' FROM pg_stat_activity WHERE state != 'idle' AND query_start < NOW() - INTERVAL '5 minutes';",
                    'description': 'Terminate 阻塞进程 (PostgreSQL)',
                    'risk_level': 'high'
                })
            elif db_type == 'dm':
                commands.append({
                    'db_type': 'dameng',
                    'command': "SELECT 'KILL SESSION ' || SESS_ID FROM V$SESSIONS WHERE TRX_ID IN (SELECT TRX_ID FROM V$LOCK WHERE BLOCKED = 1);",
                    'description': 'Kill 阻塞会话 (达梦)',
                    'risk_level': 'high'
                })
        
        elif diagnosis['rule_id'] == 'R003':  # 表空间不足
            if db_type == 'oracle':
                commands.append({
                    'db_type': 'oracle',
                    'command': "ALTER DATABASE DATAFILE '<datafile_path>' RESIZE <new_size>M;",
                    'description': '扩容数据文件 (Oracle)',
                    'risk_level': 'medium'
                })
            elif db_type in ('mysql', 'tdsql'):
                commands.append({
                    'db_type': db_type,
                    'command': "ALTER DATABASE <db_name> ADD DATAFILE '<path>' SIZE 10G;",
                    'description': '添加数据文件 (MySQL/TDSQL)',
                    'risk_level': 'medium'
                })
            elif db_type == 'pgsql':
                commands.append({
                    'db_type': 'postgresql',
                    'command': "ALTER TABLESPACE <tablespace_name> ADD (filename '<new_file>', size 10GB);",
                    'description': '扩容表空间 (PostgreSQL)',
                    'risk_level': 'medium'
                })
            elif db_type == 'dm':
                commands.append({
                    'db_type': 'dameng',
                    'command': "ALTER TABLESPACE <tablespace_name> RESIZE DATAFILE '<path>' SIZE 10G;",
                    'description': '扩容表空间 (达梦)',
                    'risk_level': 'medium'
                })
        
        elif diagnosis['rule_id'] == 'R001':  # 连接数泄漏
            if db_type == 'oracle':
                commands.append({
                    'db_type': 'oracle',
                    'command': "SELECT sid, serial#, username, program, status FROM v$session WHERE status = 'INACTIVE' AND last_call_et > 1800;",
                    'description': '查看长时间空闲的会话 (Oracle)',
                    'risk_level': 'low'
                })
            elif db_type in ('mysql', 'tdsql'):
                commands.append({
                    'db_type': db_type,
                    'command': "SELECT id, user, host, command, time, state FROM information_schema.processlist WHERE command != 'Sleep' AND time > 300;",
                    'description': '查看长时间运行的连接 (MySQL/TDSQL)',
                    'risk_level': 'low'
                })
        
        return commands
    
    def get_rule_count(self) -> int:
        """获取规则总数"""
        return len(self.rules)


# ==========================================
# 使用示例
# ==========================================
"""
# 在 start_monitor.py 中集成:

from monitor.rca_engine import RCAEngine

# 在 process_result 方法中添加 RCA 分析:
if current_status == 'UP':
    rca = RCAEngine(config)
    report = rca.analyze(data)
    
    if report['diagnoses']:
        subject = f"🔍 根因分析报告：{config.name}"
        body = report['summary']
        
        # 发送告警
        for diag in report['diagnoses']:
            if diag['severity'] == 'critical':
                am.fire(alert_type='rca', metric_key=diag['rule_id'],
                        title=f"🔴 {diag['name']}", 
                        description=diag['description'] + "\\n" + "\\n".join(diag['suggestions']),
                        severity='critical')
"""
