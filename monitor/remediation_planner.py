"""
方案生成器 v1.0 (Phase 5 - 多方案生成 + 风险评估)

功能:
- 根据 RCA 诊断结果生成多套方案(保守/标准/激进)
- 每套方案含执行步骤、风险评级、回滚方案
- 集成审批工作流
- 与现有 auto_remediation_engine.py 互补

设计文档参考: PHASE5_DEVELOPMENT_DESIGN.md 第二部分 P0-5
"""
import json
import logging
import uuid
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional, Any
from datetime import datetime

logger = logging.getLogger(__name__)


@dataclass
class PlanStep:
    """方案执行步骤"""
    order: int
    action: str
    description: str
    sql: str = ''
    command: str = ''
    expected_outcome: str = ''
    risk: str = 'low'  # low/medium/high
    rollback: str = ''
    est_time_sec: int = 60


@dataclass
class PlanScenario:
    """方案场景"""
    name: str  # conservative / standard / aggressive
    risk_level: str  # low/medium/high/critical
    estimated_time: str  # '5min'
    auto_executable: bool
    requires_approval: bool
    steps: List[PlanStep] = field(default_factory=list)
    description: str = ''


@dataclass
class RemediationPlanV2:
    """方案 V2"""
    plan_id: str
    title: str
    rule_id: str
    db_type: str
    db_name: str
    created_at: str
    scenarios: List[PlanScenario] = field(default_factory=list)
    recommended: str = 'conservative'
    business_impact: Dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        result = asdict(self)
        return result


# ==========================================
# 方案模板库 - 按 RCA 规则 ID 组织
# ==========================================
PLAN_TEMPLATES = {
    'R001': {  # 连接数泄漏
        'title': '解决数据库连接数泄漏',
        'scenarios': {
            'conservative': {
                'risk_level': 'low',
                'auto_executable': True,
                'requires_approval': False,
                'estimated_time': '2min',
                'description': '清理长时间空闲会话,收集信息',
                'steps': [
                    {
                        'order': 1,
                        'action': 'analyze_sessions',
                        'description': '分析当前会话状态分布',
                        'sql': 'SELECT state, count(*) FROM pg_stat_activity GROUP BY state;',
                        'expected_outcome': '了解 idle/active 分布',
                        'risk': 'low',
                        'est_time_sec': 5,
                    },
                    {
                        'order': 2,
                        'action': 'kill_idle_long',
                        'description': '清理空闲超过 30 分钟的会话',
                        'sql_pg': "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE state='idle' AND state_change < now() - interval '30 min' AND pid != pg_backend_pid();",
                        'sql_mysql': "SELECT concat('KILL ', id, ';') FROM information_schema.processlist WHERE command='Sleep' AND time > 1800;",
                        'sql_oracle': "SELECT 'ALTER SYSTEM KILL SESSION ''' || sid || ',' || serial# || ''';' FROM v$session WHERE status='INACTIVE' AND last_call_et > 1800;",
                        'expected_outcome': '释放空闲连接',
                        'risk': 'low',
                        'rollback': '会话被 kill 后应用会自动重连',
                        'est_time_sec': 30,
                    },
                ],
            },
            'standard': {
                'risk_level': 'medium',
                'auto_executable': False,
                'requires_approval': True,
                'estimated_time': '10min',
                'description': '调整连接池配置 + 清理',
                'steps': [
                    {
                        'order': 1,
                        'action': 'kill_idle_long',
                        'description': '清理空闲超过 30 分钟的会话',
                        'sql_pg': "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE state='idle' AND state_change < now() - interval '30 min';",
                        'risk': 'low',
                        'est_time_sec': 30,
                    },
                    {
                        'order': 2,
                        'action': 'adjust_pool',
                        'description': '通知应用方调整连接池配置',
                        'command': '通知应用 owner 调小 maxPoolSize 至 50%',
                        'risk': 'medium',
                        'rollback': '人工通知回滚',
                        'est_time_sec': 600,
                    },
                ],
            },
        },
    },

    'R031': {  # 表空间满
        'title': '解决表空间不足',
        'scenarios': {
            'conservative': {
                'risk_level': 'low',
                'auto_executable': True,
                'requires_approval': False,
                'estimated_time': '5min',
                'description': '扩容数据文件',
                'steps': [
                    {
                        'order': 1,
                        'action': 'identify_full_ts',
                        'description': '定位使用率超 90% 的表空间',
                        'sql_oracle': "SELECT tablespace_name, used_pct FROM dba_tablespace_usage_metrics WHERE used_pct > 90;",
                        'sql_pg': "SELECT spcname, pg_size_pretty(pg_database_size(oid)) FROM pg_tablespace;",
                        'sql_mysql': "SELECT table_schema, sum(data_length+index_length) FROM information_schema.tables GROUP BY table_schema;",
                        'risk': 'low',
                        'est_time_sec': 5,
                    },
                    {
                        'order': 2,
                        'action': 'resize_datafile',
                        'description': '扩容数据文件(增加 20%)',
                        'sql_oracle': "ALTER DATABASE DATAFILE '/path/to/file.dbf' RESIZE 12288M;",
                        'risk': 'low',
                        'rollback': '无法回滚,扩容是单向操作',
                        'est_time_sec': 60,
                    },
                ],
            },
            'aggressive': {
                'risk_level': 'high',
                'auto_executable': False,
                'requires_approval': True,
                'estimated_time': '30min',
                'description': '归档历史数据 + 重建',
                'steps': [
                    {
                        'order': 1,
                        'action': 'archive_old_data',
                        'description': '归档超过 1 年的历史数据',
                        'sql': 'INSERT INTO orders_archive SELECT * FROM orders WHERE create_time < now() - interval \'1 year\';',
                        'risk': 'high',
                        'rollback': '需要从归档表恢复',
                        'est_time_sec': 1800,
                    },
                    {
                        'order': 2,
                        'action': 'delete_archived',
                        'description': '删除已归档数据',
                        'sql': 'DELETE FROM orders WHERE create_time < now() - interval \'1 year\';',
                        'risk': 'critical',
                        'rollback': '从归档表回插',
                        'est_time_sec': 600,
                    },
                ],
            },
        },
    },

    'R011': {  # 慢查询
        'title': '缓解慢查询',
        'scenarios': {
            'conservative': {
                'risk_level': 'low',
                'auto_executable': True,
                'requires_approval': False,
                'estimated_time': '3min',
                'description': '收集统计信息 + 列出 Top SQL',
                'steps': [
                    {
                        'order': 1,
                        'action': 'collect_stats',
                        'description': '更新统计信息',
                        'sql_pg': 'ANALYZE;',
                        'sql_mysql': 'ANALYZE TABLE orders, users;',
                        'sql_oracle': 'BEGIN DBMS_STATS.GATHER_SCHEMA_STATS(ownname=>USER); END;',
                        'risk': 'low',
                        'est_time_sec': 120,
                    },
                ],
            },
            'standard': {
                'risk_level': 'medium',
                'auto_executable': False,
                'requires_approval': True,
                'estimated_time': '15min',
                'description': 'kill 长查询 + 建索引',
                'steps': [
                    {
                        'order': 1,
                        'action': 'kill_long_query',
                        'description': '终止运行超 30min 的查询',
                        'sql_pg': "SELECT pg_cancel_backend(pid) FROM pg_stat_activity WHERE state='active' AND now() - query_start > interval '30 min';",
                        'risk': 'medium',
                        'est_time_sec': 60,
                    },
                    {
                        'order': 2,
                        'action': 'create_index',
                        'description': '创建推荐索引',
                        'sql': 'CREATE INDEX CONCURRENTLY idx_orders_userid ON orders(user_id);',
                        'risk': 'medium',
                        'rollback': 'DROP INDEX CONCURRENTLY idx_orders_userid;',
                        'est_time_sec': 600,
                    },
                ],
            },
        },
    },

    'R023': {  # 死锁
        'title': '处理死锁',
        'scenarios': {
            'conservative': {
                'risk_level': 'low',
                'auto_executable': True,
                'requires_approval': False,
                'estimated_time': '2min',
                'description': '收集死锁信息 + 监控',
                'steps': [
                    {
                        'order': 1,
                        'action': 'collect_deadlock_info',
                        'description': '收集最近死锁信息',
                        'sql_pg': 'SELECT * FROM pg_stat_database WHERE deadlocks > 0;',
                        'sql_oracle': 'SELECT * FROM v$lock WHERE type = \'TM\';',
                        'risk': 'low',
                        'est_time_sec': 10,
                    },
                ],
            },
            'standard': {
                'risk_level': 'medium',
                'auto_executable': False,
                'requires_approval': True,
                'estimated_time': '10min',
                'description': 'kill 死锁相关会话',
                'steps': [
                    {
                        'order': 1,
                        'action': 'identify_deadlock_sessions',
                        'description': '识别参与死锁的会话',
                        'sql_pg': "SELECT pid, usename, query FROM pg_stat_activity WHERE state='active' AND pid IN (SELECT pid FROM pg_locks WHERE locktype='tuple');",
                        'risk': 'low',
                        'est_time_sec': 10,
                    },
                    {
                        'order': 2,
                        'action': 'kill_deadlock_sessions',
                        'description': '终止死锁会话',
                        'sql_pg': 'SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE pid IN (...);',
                        'risk': 'medium',
                        'rollback': '会话终止后应用自动重连',
                        'est_time_sec': 30,
                    },
                ],
            },
        },
    },

    'R021': {  # 锁等待
        'title': '处理锁等待',
        'scenarios': {
            'conservative': {
                'risk_level': 'low',
                'auto_executable': True,
                'requires_approval': False,
                'estimated_time': '2min',
                'description': '分析锁等待',
                'steps': [
                    {
                        'order': 1,
                        'action': 'show_locks',
                        'description': '查看锁等待详情',
                        'sql_pg': "SELECT blocked_locks.pid AS blocked_pid, blocking_locks.pid AS blocking_pid FROM pg_locks blocked_locks JOIN pg_locks blocking_locks ON ...;",
                        'sql_oracle': 'SELECT * FROM v$lock WHERE block > 0;',
                        'risk': 'low',
                        'est_time_sec': 10,
                    },
                ],
            },
            'standard': {
                'risk_level': 'medium',
                'auto_executable': False,
                'requires_approval': True,
                'estimated_time': '5min',
                'description': 'kill 阻塞会话',
                'steps': [
                    {
                        'order': 1,
                        'action': 'identify_blocker',
                        'description': '识别阻塞源',
                        'sql': '...',
                        'risk': 'low',
                        'est_time_sec': 10,
                    },
                    {
                        'order': 2,
                        'action': 'kill_blocker',
                        'description': '终止阻塞会话',
                        'sql': '...',
                        'risk': 'medium',
                        'rollback': '会话自动重连',
                        'est_time_sec': 30,
                    },
                ],
            },
        },
    },

    # 通用默认方案
    '_default': {
        'title': '通用处置方案',
        'scenarios': {
            'conservative': {
                'risk_level': 'low',
                'auto_executable': True,
                'requires_approval': False,
                'estimated_time': '5min',
                'description': '收集诊断信息 + 监控',
                'steps': [
                    {
                        'order': 1,
                        'action': 'collect_info',
                        'description': '收集当前状态信息',
                        'sql': '-- 查看相关指标快照',
                        'risk': 'low',
                        'est_time_sec': 30,
                    },
                ],
            },
            'standard': {
                'risk_level': 'medium',
                'auto_executable': False,
                'requires_approval': True,
                'estimated_time': '15min',
                'description': '常规处置',
                'steps': [
                    {
                        'order': 1,
                        'action': 'standard_remediation',
                        'description': '请参考相关运维文档',
                        'risk': 'medium',
                        'est_time_sec': 600,
                    },
                ],
            },
        },
    },
}


class RemediationPlanner:
    """方案生成器"""

    def __init__(self, db_config, rca_diagnosis: Dict[str, Any]):
        self.db_config = db_config
        self.db_type = db_config.db_type
        self.diagnosis = rca_diagnosis
        self.rule_id = rca_diagnosis.get('rule_id', '_default')

    def generate(self) -> RemediationPlanV2:
        """
        生成多套方案
        """
        template = PLAN_TEMPLATES.get(self.rule_id, PLAN_TEMPLATES['_default'])
        plan_id = f"P-{datetime.now().strftime('%Y%m%d')}-{uuid.uuid4().hex[:6].upper()}"

        scenarios = []
        for sc_name, sc_data in template.get('scenarios', {}).items():
            scenario = PlanScenario(
                name=sc_name,
                risk_level=sc_data.get('risk_level', 'medium'),
                estimated_time=sc_data.get('estimated_time', '?'),
                auto_executable=sc_data.get('auto_executable', False),
                requires_approval=sc_data.get('requires_approval', True),
                description=sc_data.get('description', ''),
                steps=[
                    PlanStep(
                        order=step.get('order', idx + 1),
                        action=step.get('action', ''),
                        description=step.get('description', ''),
                        sql=self._resolve_db_specific_sql(step, self.db_type),
                        command=step.get('command', ''),
                        expected_outcome=step.get('expected_outcome', ''),
                        risk=step.get('risk', 'low'),
                        rollback=step.get('rollback', ''),
                        est_time_sec=step.get('est_time_sec', 60),
                    )
                    for idx, step in enumerate(sc_data.get('steps', []))
                ],
            )
            scenarios.append(scenario)

        business_impact = self._build_business_impact(scenarios)
        return RemediationPlanV2(
            plan_id=plan_id,
            title=template.get('title', '通用方案'),
            rule_id=self.rule_id,
            db_type=self.db_type,
            db_name=self.db_config.name,
            created_at=datetime.now().isoformat(),
            scenarios=scenarios,
            recommended='conservative',
            business_impact=business_impact,
        )

    def _resolve_db_specific_sql(self, step: Dict, db_type: str) -> str:
        """解析 DB 特定的 SQL"""
        # 优先匹配 db 特定的 sql
        for key in [f'sql_{db_type}', 'sql']:
            sql = step.get(key, '')
            if sql and sql != '-- 查看相关指标快照':
                return sql
        return step.get('sql', '')

    def _build_business_impact(self, scenarios: List[PlanScenario]) -> Dict[str, str]:
        """构建各方案的业务影响说明"""
        result = {}
        for sc in scenarios:
            if sc.risk_level == 'low':
                result[sc.name] = '业务影响极小,建议立即执行'
            elif sc.risk_level == 'medium':
                result[sc.name] = '业务影响可控,建议业务低峰期执行'
            elif sc.risk_level == 'high':
                result[sc.name] = '业务影响较大,需提前公告'
            else:
                result[sc.name] = '业务影响极大,需审批+人工值守'
        return result


# ==========================================
# 便捷入口
# ==========================================
def generate_plan(db_config, rca_diagnosis: Dict[str, Any]) -> Dict[str, Any]:
    """
    便捷函数: 根据 RCA 诊断生成多套方案
    """
    planner = RemediationPlanner(db_config, rca_diagnosis)
    plan = planner.generate()
    return plan.to_dict()
