"""
故障根因分析引擎 v1.0 (RCA - Root Cause Analysis)

功能:
- 基于规则的故障诊断
- 关联多个指标定位问题源头
- 提供处理建议
"""

import json
from datetime import datetime
from monitor.models import MonitorLog, DatabaseConfig


class RCAEngine:
    """根因分析引擎"""
    
    # 规则库
    RULES = [
        {
            'id': 'R001',
            'name': '连接数泄漏',
            'condition': lambda d: d.get('conn_usage_pct', 0) > 80 and d.get('qps', 0) < 10,
            'description': '连接数使用率高但 QPS 很低，可能存在连接泄漏',
            'suggestions': [
                '检查应用程序是否有未关闭的数据库连接',
                '查看连接池配置是否合理',
                '使用 "SHOW PROCESSLIST" (MySQL) 或 "SELECT * FROM v$session" (Oracle) 查看活跃会话'
            ]
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
            ]
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
            ]
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
            ]
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
            ]
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
            ]
        }
    ]
    
    def __init__(self, config: DatabaseConfig):
        self.config = config
    
    def get_latest_data(self):
        """获取最新一次监控数据"""
        latest_log = MonitorLog.objects.filter(
            config=self.config,
            status='UP'
        ).order_by('-create_time').first()
        
        if not latest_log:
            return None
        
        try:
            return json.loads(latest_log.message)
        except:
            return None
    
    def get_recent_logs(self, limit=10):
        """获取最近 N 条监控日志"""
        logs = MonitorLog.objects.filter(
            config=self.config
        ).order_by('-create_time')[:limit]
        
        result = []
        for log in logs:
            try:
                data = json.loads(log.message)
                result.append({
                    'time': log.create_time,
                    'status': log.status,
                    'data': data
                })
            except:
                pass
        
        return result
    
    def analyze(self, current_data=None):
        """
        执行根因分析
        
        参数:
            current_data: 当前监控数据 (如果不传则自动获取最新数据)
        
        返回:
            {
                'timestamp': 分析时间,
                'config': 数据库配置,
                'diagnoses': [
                    {
                        'rule_id': 规则 ID,
                        'name': 问题名称,
                        'description': 问题描述,
                        'severity': 严重程度 (critical/warning/info),
                        'suggestions': [处理建议]
                    }
                ],
                'summary': 总结文本
            }
        """
        if current_data is None:
            current_data = self.get_latest_data()
        
        if not current_data:
            return {'error': '无可用数据'}
        
        diagnoses = []
        
        # 遍历所有规则
        for rule in self.RULES:
            try:
                if rule['condition'](current_data):
                    severity = self._calculate_severity(rule['id'], current_data)
                    diagnoses.append({
                        'rule_id': rule['id'],
                        'name': rule['name'],
                        'description': rule['description'],
                        'severity': severity,
                        'suggestions': rule['suggestions']
                    })
            except Exception as e:
                # 规则执行失败，跳过
                pass
        
        # 生成总结
        summary = self._generate_summary(diagnoses, current_data)
        
        return {
            'timestamp': datetime.now().isoformat(),
            'config_name': self.config.name,
            'db_type': self.config.db_type,
            'diagnoses': diagnoses,
            'summary': summary,
            'current_metrics': {
                'status': 'UP',
                'connections': current_data.get('active_connections', 0),
                'conn_usage_pct': current_data.get('conn_usage_pct', 0),
                'locks_count': len(current_data.get('locks', [])),
                'tbs_high_count': len([t for t in current_data.get('tablespaces', []) if t.get('used_pct', 0) > 90])
            }
        }
    
    def _calculate_severity(self, rule_id, data):
        """计算问题严重程度"""
        # 锁等待 -> critical
        if rule_id == 'R002' and len(data.get('locks', [])) > 5:
            return 'critical'
        
        # 表空间 > 95% -> critical
        if rule_id == 'R003':
            max_tbs = max([t.get('used_pct', 0) for t in data.get('tablespaces', [])], default=0)
            if max_tbs > 95:
                return 'critical'
        
        # 默认 warning
        return 'warning'
    
    def _generate_summary(self, diagnoses, data):
        """生成问题总结"""
        if not diagnoses:
            return "✅ 未检测到明显问题，数据库运行正常。"
        
        critical_count = sum(1 for d in diagnoses if d['severity'] == 'critical')
        warning_count = sum(1 for d in diagnoses if d['severity'] == 'warning')
        
        summary_parts = []
        
        if critical_count > 0:
            summary_parts.append(f"🔴 发现 {critical_count} 个严重问题")
        if warning_count > 0:
            summary_parts.append(f"🟠 发现 {warning_count} 个警告")
        
        # 列出最主要的问题
        main_issues = [d['name'] for d in sorted(diagnoses, key=lambda x: 0 if x['severity'] == 'critical' else 1)[:3]]
        summary_parts.append(f"主要问题：{', '.join(main_issues)}")
        
        return " | ".join(summary_parts)
    
    def generate_fix_commands(self, diagnosis):
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
                    'command': f"SELECT 'ALTER SYSTEM KILL SESSION ''' || sid || ',' || serial# || '''' FROM v$session WHERE sid = <BLOCKER_SID>;",
                    'description': 'Kill 阻塞会话 (Oracle)',
                    'risk_level': 'high'
                })
            elif db_type == 'mysql' or db_type == 'tdsql':
                commands.append({
                    'command': f"KILL <BLOCKER_THREAD_ID>;",
                    'description': 'Kill 阻塞线程 (MySQL/TDSQL)',
                    'risk_level': 'high'
                })
            elif db_type == 'pgsql':
                commands.append({
                    'command': f"SELECT pg_terminate_backend(<BLOCKER_PID>);",
                    'description': 'Terminate 阻塞进程 (PostgreSQL)',
                    'risk_level': 'high'
                })
        
        elif diagnosis['rule_id'] == 'R003':  # 表空间不足
            if db_type == 'oracle':
                commands.append({
                    'command': "ALTER DATABASE DATAFILE '/path/to/datafile.dbf' RESIZE 10G;",
                    'description': '扩容数据文件 (Oracle)',
                    'risk_level': 'medium'
                })
            elif db_type == 'mysql' or db_type == 'tdsql':
                commands.append({
                    'command': "ALTER DATABASE <db_name> ADD DATAFILE '<path>' SIZE 10G;",
                    'description': '添加数据文件 (MySQL/TDSQL)',
                    'risk_level': 'medium'
                })
        
        return commands


def _check_shard_imbalance(shards):
    """检查分片数据是否不均衡"""
    if len(shards) < 2:
        return False
    
    sizes = [s.get('data_size_mb', 0) for s in shards]
    if not sizes or max(sizes) == 0:
        return False
    
    avg_size = sum(sizes) / len(sizes)
    max_deviation = max(abs(s - avg_size) / avg_size for s in sizes)
    
    return max_deviation > 0.5  # 偏差超过 50% 认为不均衡


# ==========================================
# 使用示例
# ==========================================
"""
# 在 start_monitor.py 中集成:

from .rca_engine import RCAEngine

# 在 process_result 方法中添加 RCA 分析:
if current_status == 'UP' and (current_locks or current_tbs_warn):
    rca_engine = RCAEngine(config)
    rca_report = rca_engine.analyze(data)
    
    if rca_report['diagnoses']:
        # 发送 RCA 报告邮件
        subject = f"🔍 根因分析报告：{config.name}"
        message = rca_engine.format_report(rca_report)
        self.send_alert_email(config, subject, message)
"""
