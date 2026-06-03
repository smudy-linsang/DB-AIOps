"""
RCA 2.0 引擎 v2.0 (Phase 5 - 智能根因分析)

功能:
- 在 rca_engine.py 基础上扩展 30+ 条规则 (覆盖 7 大对象域)
- 引入因果图谱(causal graph)实现复合故障推导
- 支持上下文增强诊断
- 输出树形因果链 + 置信度 + 推荐方案

设计文档参考: PHASE5_DEVELOPMENT_DESIGN.md 第二部分 P0-3
"""
import json
import logging
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional, Any, Tuple

logger = logging.getLogger(__name__)


# ==========================================
# 数据类
# ==========================================
@dataclass
class RCADiagnosis:
    """RCA 诊断结果"""
    rule_id: str
    rule_name: str
    confidence: float  # 0.0 - 1.0
    severity: str  # info / warn / error / critical
    description: str
    causal_chain: List[Dict[str, Any]] = field(default_factory=list)  # 因果链
    root_cause: str = ''
    effects: List[str] = field(default_factory=list)  # 次生影响
    suggestions: List[str] = field(default_factory=list)  # 修复建议
    related_metrics: Dict[str, Any] = field(default_factory=dict)
    matched_case_id: Optional[str] = None
    matched_case_similarity: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ==========================================
# 扩展规则库 (R001-R040)
# ==========================================
# 在 rca_engine.py 原有 10 条基础上扩展到 40 条
# 按对象域组织: 连接/SQL/锁/IO/内存/复制/集群/容量
RULES_V2 = [
    # === 连接域 (R001-R008) ===
    {
        'id': 'R001', 'domain': 'connection', 'name': '连接数泄漏',
        'condition': lambda d: d.get('conn_usage_pct', 0) > 80 and d.get('qps', 0) < 10,
        'description': '连接数使用率高但 QPS 很低, 可能存在连接泄漏',
        'typical_chain': '应用连接未关闭 → 连接池持续增长 → DB 连接耗尽',
        'suggestions': [
            '检查应用是否有未关闭的连接(代码 review)',
            '查看连接池配置(maxPoolSize 是否过大)',
            '查看 session_by_state 分布,idle 多说明未复用',
        ],
        'severity_default': 'warning',
    },
    {
        'id': 'R002', 'domain': 'connection', 'name': '连接风暴',
        'condition': lambda d: d.get('aborted_connects', 0) > 100,
        'description': '异常连接数过多,可能存在连接风暴或密码攻击',
        'typical_chain': '应用重启/密码错误/攻击 → 大量失败连接 → max_connect_errors 触发',
        'suggestions': [
            '查看 failed_logins 表定位源 IP',
            '检查是否有应用重启或配置变更',
            '考虑增加 connection pool 预热',
        ],
        'severity_default': 'warning',
    },
    {
        'id': 'R003', 'domain': 'connection', 'name': 'idle in transaction 过多',
        'condition': lambda d: d.get('idle_in_transaction_count', 0) > 10,
        'description': '事务中空闲连接过多,会长时间持有锁',
        'typical_chain': '应用事务未提交/未回滚 → 长事务 → 锁等待',
        'suggestions': [
            '检查应用是否忘记 commit/rollback',
            '设置 idle_in_transaction_session_timeout',
            'kill 超过 30min 的 idle in txn 会话',
        ],
        'severity_default': 'warning',
    },
    {
        'id': 'R004', 'domain': 'connection', 'name': 'max_connections 接近上限',
        'condition': lambda d: d.get('conn_usage_pct', 0) > 90,
        'description': '连接使用率超过 90%,新连接将失败',
        'typical_chain': 'DB 连接数即将耗尽 → 应用报错',
        'suggestions': [
            '提高 max_connections (但需评估内存)',
            '启用连接池 (pgbouncer / proxysql)',
            'kill 空闲超长会话',
        ],
        'severity_default': 'error',
    },

    # === SQL 域 (R011-R018) ===
    {
        'id': 'R011', 'domain': 'sql', 'name': '慢查询激增',
        'condition': lambda d: d.get('slow_queries_active', 0) > 10 or d.get('slow_queries', 0) > 100,
        'description': '检测到慢查询激增',
        'typical_chain': '统计信息过期 / 索引缺失 → 全表扫描 → 慢查询',
        'suggestions': [
            '查看 top_sql_by_latency 列表',
            '分析执行计划,补缺失索引',
            '更新统计信息: ANALYZE TABLE / DBMS_STATS',
        ],
        'severity_default': 'warning',
    },
    {
        'id': 'R012', 'domain': 'sql', 'name': '高 cost 全表扫描',
        'condition': lambda d: any(
            'TABLE ACCESS FULL' in str(sql.get('plan', '')).upper()
            for sql in d.get('top_sql', [])
        ),
        'description': 'Top SQL 中存在全表扫描,可能影响大表性能',
        'typical_chain': 'SQL 写法 / 索引缺失 → 全表扫描 → 大量 IO',
        'suggestions': [
            '检查 WHERE 条件是否能命中索引',
            '考虑建组合索引',
            '对超大表考虑分区',
        ],
        'severity_default': 'warning',
    },
    {
        'id': 'R013', 'domain': 'sql', 'name': '解析过载(高 hard parse)',
        'condition': lambda d: d.get('hard_parses_per_sec', 0) > 100,
        'description': '硬解析过多,Library Cache 压力大',
        'typical_chain': 'SQL 未使用绑定变量 → 大量 hard parse → CPU 飙升',
        'suggestions': [
            '应用层使用 PreparedStatement / 绑定变量',
            '设置 cursor_sharing = FORCE (Oracle)',
        ],
        'severity_default': 'warning',
    },
    {
        'id': 'R014', 'domain': 'sql', 'name': 'Library Cache Hit 突降',
        'condition': lambda d: d.get('library_cache_hit_ratio', 100) < 90,
        'description': 'Library Cache 命中率低,频繁重新加载对象',
        'typical_chain': 'shared_pool 不足 / 频繁 DDL → 重新加载',
        'suggestions': [
            '扩容 shared_pool',
            '减少不必要 DDL',
        ],
        'severity_default': 'warning',
    },

    # === 锁域 (R021-R028) ===
    {
        'id': 'R021', 'domain': 'lock', 'name': '锁等待激增',
        'condition': lambda d: len(d.get('locks', [])) > 10,
        'description': '当前存在大量锁等待',
        'typical_chain': '长事务 / 不当锁粒度 → 阻塞',
        'suggestions': [
            '查看被阻塞会话和阻塞源',
            '分析是否需要调整隔离级别',
            'kill 长时间阻塞会话',
        ],
        'severity_default': 'warning',
    },
    {
        'id': 'R022', 'domain': 'lock', 'name': '行锁升级',
        'condition': lambda d: d.get('row_lock_waits', 0) > 50,
        'description': '行锁等待过多,可能存在热点行',
        'typical_chain': '热点账户/计数器 → 行锁竞争 → TPS 下降',
        'suggestions': [
            '识别热点行(按 rowid 聚簇分析)',
            '考虑用 Sequence + 批量预取',
            '业务上分散热点',
        ],
        'severity_default': 'error',
    },
    {
        'id': 'R023', 'domain': 'lock', 'name': '死锁频发',
        'condition': lambda d: d.get('deadlocks', 0) > 5,
        'description': '24h 内死锁次数过多',
        'typical_chain': '事务顺序不一致 → 死锁循环',
        'suggestions': [
            '收集死锁 trace 文件分析顺序',
            '统一应用层事务访问顺序',
            '减少事务粒度',
        ],
        'severity_default': 'error',
    },
    {
        'id': 'R024', 'domain': 'lock', 'name': '表锁阻塞',
        'condition': lambda d: any(
            l.get('lock_type') in ('TM', 'Table') for l in d.get('locks', [])
        ),
        'description': '检测到表级锁,可能阻塞所有 DML',
        'typical_chain': 'DDL / 未提交事务 → 表锁 → DML 阻塞',
        'suggestions': [
            '查找持锁会话',
            '避免业务高峰期 DDL',
        ],
        'severity_default': 'error',
    },

    # === IO 域 (R031-R038) ===
    {
        'id': 'R031', 'domain': 'io', 'name': '表空间容量不足',
        'condition': lambda d: any(
            (t.get('used_pct') or 0) > 90 for t in d.get('tablespaces', [])
        ),
        'description': '表空间使用率超过 90%,存在写满风险',
        'typical_chain': '数据增长 → 表空间使用率上升 → 写满',
        'suggestions': [
            '扩容数据文件: ALTER DATABASE DATAFILE ... RESIZE',
            '清理过期数据',
            '归档/转移历史数据',
        ],
        'severity_default': 'error',
    },
    {
        'id': 'R032', 'domain': 'io', 'name': '日志切换频率过高',
        'condition': lambda d: d.get('log_switches_per_hour', 0) > 20,
        'description': 'redo log 切换频率过高,影响性能',
        'typical_chain': '大事务 / 小 redo log → 频繁切换 → log file sync 等待',
        'suggestions': [
            '增大 redo log 文件大小',
            '批量写入分批提交',
        ],
        'severity_default': 'warning',
    },
    {
        'id': 'R033', 'domain': 'io', 'name': '归档间隙',
        'condition': lambda d: d.get('arch_gap', 0) > 0,
        'description': '主备归档存在间隙,可能丢失数据',
        'typical_chain': '网络问题 / 归档目录满 → 归档失败',
        'suggestions': [
            '检查归档目录空间',
            '检查网络连通性',
            '手动重新归档丢失部分',
        ],
        'severity_default': 'critical',
    },
    {
        'id': 'R034', 'domain': 'io', 'name': 'Temp 撑爆',
        'condition': lambda d: d.get('temp_usage_pct', 0) > 80,
        'description': '临时表空间使用率过高,大查询可能失败',
        'typical_chain': '大排序 / hash join → temp 撑爆',
        'suggestions': [
            '扩容 temp tablespace',
            '优化大查询,减少排序',
        ],
        'severity_default': 'warning',
    },
    {
        'id': 'R035', 'domain': 'io', 'name': 'Undo 段紧张',
        'condition': lambda d: d.get('undo_usage_pct', 0) > 80,
        'description': 'Undo 表空间使用率过高,长事务可能失败',
        'typical_chain': '长事务 / undo 不足 → ORA-01555',
        'suggestions': [
            '扩容 undo tablespace',
            '检查长事务并 kill',
        ],
        'severity_default': 'warning',
    },

    # === 内存域 (R041-R045) ===
    {
        'id': 'R041', 'domain': 'memory', 'name': 'Buffer Hit 突降',
        'condition': lambda d: d.get('buffer_hit_ratio', 100) < 90,
        'description': 'Buffer Cache 命中率下降,大量物理读',
        'typical_chain': '全表扫描 / buffer_pool 不足 → 物理读飙升',
        'suggestions': [
            '扩容 buffer_cache / buffer_pool',
            '排查是否有大查询在做全表扫描',
        ],
        'severity_default': 'warning',
    },
    {
        'id': 'R042', 'domain': 'memory', 'name': 'PGA 溢出',
        'condition': lambda d: d.get('pga_usage_pct', 0) > 90,
        'description': 'PGA 使用率过高,排序将走磁盘',
        'typical_chain': '大排序 / pga 不足 → 临时表空间撑爆',
        'suggestions': [
            '扩容 pga_aggregate_target',
            '优化 SQL 减少内存排序',
        ],
        'severity_default': 'warning',
    },

    # === 复制域 (R051-R056) ===
    {
        'id': 'R051', 'domain': 'replication', 'name': '主从延迟严重',
        'condition': lambda d: d.get('seconds_behind_master', 0) > 60 or d.get('replication_lag_bytes', 0) > 10485760,
        'description': '主从延迟超过 60s 或 10MB',
        'typical_chain': '主库大事务 / 从库 IO 慢 → 延迟累积',
        'suggestions': [
            '检查主库是否有大事务',
            '检查从库 IO 性能(磁盘/网络)',
            '考虑多线程复制 (MTS)',
        ],
        'severity_default': 'error',
    },
    {
        'id': 'R052', 'domain': 'replication', 'name': '复制中断',
        'condition': lambda d: d.get('slave_io_running') == 'No' or d.get('slave_sql_running') == 'No',
        'description': '复制线程停止',
        'typical_chain': '网络中断 / 数据不一致 / 权限丢失',
        'suggestions': [
            '查看复制错误日志',
            'SHOW SLAVE STATUS\\G 查看具体错误',
            '必要时重建复制',
        ],
        'severity_default': 'critical',
    },
    {
        'id': 'R053', 'domain': 'replication', 'name': 'DataGuard Apply Lag',
        'condition': lambda d: d.get('dg_apply_lag_seconds', 0) > 60,
        'description': 'DataGuard 备库 Apply 延迟',
        'typical_chain': '主库写入压力 / 备库 redo 应用慢',
        'suggestions': [
            '启用异步 Apply',
            '检查备库 IO 性能',
        ],
        'severity_default': 'warning',
    },

    # === 集群域 (R061-R066) ===
    {
        'id': 'R061', 'domain': 'cluster', 'name': 'RAC 节点驱逐',
        'condition': lambda d: d.get('rac_node_eviction', False),
        'description': 'RAC 节点被驱逐',
        'typical_chain': '网络心跳丢失 / 节点无响应',
        'suggestions': [
            '查看集群 alert log',
            '检查私有网络连通性',
            '排查 ssh 等价性问题',
        ],
        'severity_default': 'critical',
    },
    {
        'id': 'R062', 'domain': 'cluster', 'name': '脑裂风险',
        'condition': lambda d: d.get('split_brain_risk', False),
        'description': '检测到脑裂风险',
        'typical_chain': '网络分区 → 多个主节点',
        'suggestions': [
            '立即人工干预',
            '检查 fencing 配置',
        ],
        'severity_default': 'critical',
    },

    # === 容量/对象域 (R071-R075) ===
    {
        'id': 'R071', 'domain': 'capacity', 'name': '7天容量将耗尽',
        'condition': lambda d: d.get('days_to_full', 999) < 7,
        'description': '根据历史增长趋势,容量将在 7 天内耗尽',
        'typical_chain': '数据日增长 > 清理速度',
        'suggestions': [
            '立即扩容',
            '排查异常增长源',
            '归档历史数据',
        ],
        'severity_default': 'error',
    },
    {
        'id': 'R072', 'domain': 'object', 'name': '无效对象',
        'condition': lambda d: d.get('invalid_objects_count', 0) > 0,
        'description': '存在无效对象,可能影响应用调用',
        'typical_chain': '依赖对象变更 → 失效',
        'suggestions': [
            '@@UTLRP.SQL 重新编译',
            '检查无效对象依赖关系',
        ],
        'severity_default': 'warning',
    },
    {
        'id': 'R073', 'domain': 'object', 'name': '回收站对象过多',
        'condition': lambda d: d.get('recyclebin_count', 0) > 100,
        'description': '回收站对象过多,占用空间',
        'suggestions': [
            'PURGE RECYCLEBIN 清理',
        ],
        'severity_default': 'info',
    },
    {
        'id': 'R074', 'domain': 'sequence', 'name': '序列接近 MAX',
        'condition': lambda d: d.get('seq_usage_pct', 0) > 80,
        'description': '序列使用率超过 80%,可能耗尽',
        'suggestions': [
            '扩 MAXVALUE 或重建序列',
        ],
        'severity_default': 'warning',
    },
    {
        'id': 'R075', 'domain': 'statistics', 'name': '统计信息过期',
        'condition': lambda d: d.get('stale_stats_tables', 0) > 10,
        'description': '存在统计信息过期表,可能导致执行计划错误',
        'suggestions': [
            '收集统计信息: DBMS_STATS.GATHER_SCHEMA_STATS',
        ],
        'severity_default': 'warning',
    },
    {
        'id': 'R076', 'domain': 'awr', 'name': 'AWR 快照间隔异常',
        'condition': lambda d: d.get('awr_snap_interval_min', 60) > 120,
        'description': 'AWR 快照间隔过长,无法捕获问题现场',
        'suggestions': [
            '调整为 15-60 分钟',
        ],
        'severity_default': 'info',
    },
    {
        'id': 'R077', 'domain': 'awr', 'name': 'SCN Headroom 不足',
        'condition': lambda d: d.get('scn_headroom_days', 999) < 60,
        'description': 'SCN Headroom 不足,可能触发 ORA-19706',
        'typical_chain': '高并发的 commit → SCN 增长过快',
        'suggestions': [
            '评估是否使用 BigSCN 兼容',
            '降低 commit 频率',
        ],
        'severity_default': 'critical',
    },
    {
        'id': 'R078', 'domain': 'config', 'name': '参数偏离最佳实践',
        'condition': lambda d: d.get('param_deviation_count', 0) > 5,
        'description': '多个关键参数偏离最佳实践',
        'suggestions': [
            '运行 config_advisor 评估',
            '参考 Oracle 最佳实践文档',
        ],
        'severity_default': 'info',
    },
    {
        'id': 'R079', 'domain': 'security', 'name': '失败登录异常',
        'condition': lambda d: d.get('failed_logins', 0) > 50,
        'description': '24h 失败登录次数过多,可能存在攻击',
        'suggestions': [
            '审计日志定位源 IP',
            '考虑启用账号锁定策略',
        ],
        'severity_default': 'warning',
    },
    {
        'id': 'R080', 'domain': 'sql', 'name': '高 cost 扫描',
        'condition': lambda d: d.get('high_cost_scan_count', 0) > 0,
        'description': '检测到高 cost 的全表扫描',
        'suggestions': [
            '运行 index_advisor 推荐索引',
        ],
        'severity_default': 'warning',
    },
]


# ==========================================
# 因果图谱
# ==========================================
CAUSAL_GRAPH = {
    # cause_metric: {description, effects: [{metric, relation, lag_min}]}
    'tablespace_full': {
        'description': '表空间写满',
        'effects': [
            {'metric': 'write_failure', 'relation': 'cause', 'lag_min': 0},
            {'metric': 'qps', 'relation': 'decrease', 'lag_min': 1},
        ],
        'root_cause_candidates': [
            '数据增长未及时清理',
            '归档/备份策略失效',
            '异常批量写入',
        ],
    },
    'conn_usage_pct_high': {
        'description': '连接数耗尽',
        'effects': [
            {'metric': 'qps', 'relation': 'decrease', 'lag_min': 0},
            {'metric': 'application_error', 'relation': 'cause', 'lag_min': 0},
            {'metric': 'lock_waits', 'relation': 'increase', 'lag_min': 5},
        ],
        'root_cause_candidates': [
            '连接泄漏',
            '应用重启风暴',
            'max_pool_size 配置过大',
        ],
    },
    'slow_query_burst': {
        'description': '慢查询激增',
        'effects': [
            {'metric': 'lock_waits', 'relation': 'increase', 'lag_min': 2},
            {'metric': 'tps', 'relation': 'decrease', 'lag_min': 2},
            {'metric': 'cpu_usage', 'relation': 'increase', 'lag_min': 1},
        ],
        'root_cause_candidates': [
            '统计信息过期',
            '索引失效',
            '全表扫描',
        ],
    },
    'replication_lag': {
        'description': '主从延迟',
        'effects': [
            {'metric': 'read_inconsistency', 'relation': 'cause', 'lag_min': 30},
            {'metric': 'failover_risk', 'relation': 'increase', 'lag_min': 60},
        ],
        'root_cause_candidates': [
            '主库大事务',
            '从库 IO 慢',
            '网络抖动',
        ],
    },
}


# ==========================================
# RCA 2.0 主类
# ==========================================
class RCAEngineV2:
    """RCA 2.0 引擎"""

    def __init__(self, db_type: str = 'oracle'):
        self.db_type = db_type
        self.rules = [r for r in RULES_V2]  # 全部规则(可按 db_type 过滤)

    def diagnose(
        self,
        current_data: Dict[str, Any],
        context: Optional[Dict[str, Any]] = None,
    ) -> List[RCADiagnosis]:
        """
        诊断入口

        参数:
            current_data: 当前指标快照
            context: 上下文聚合器返回的上下文(可选)

        返回:
            RCADiagnosis 列表(按严重度+置信度排序)
        """
        context = context or {}
        diagnoses: List[RCADiagnosis] = []

        for rule in self.rules:
            try:
                cond = rule['condition']
                if not cond(current_data):
                    continue
            except Exception as e:
                logger.debug(f"[RCA-V2] 规则 {rule['id']} 条件计算异常: {e}")
                continue

            # 命中规则
            confidence = self._compute_confidence(rule, current_data, context)
            causal_chain = self._build_causal_chain(rule, context)
            related_metrics = self._extract_related_metrics(rule, current_data)

            diag = RCADiagnosis(
                rule_id=rule['id'],
                rule_name=rule['name'],
                confidence=confidence,
                severity=rule['severity_default'],
                description=rule['description'],
                causal_chain=causal_chain,
                root_cause=rule.get('typical_chain', ''),
                effects=[e.get('metric', '') for e in CAUSAL_GRAPH.get(
                    self._rule_to_graph_key(rule), {}).get('effects', []
                )],
                suggestions=rule['suggestions'],
                related_metrics=related_metrics,
            )
            diagnoses.append(diag)

        # 按 (severity 权重, -confidence) 排序
        severity_weight = {'info': 0, 'warning': 1, 'error': 2, 'critical': 3}
        diagnoses.sort(
            key=lambda d: (-severity_weight.get(d.severity, 0), -d.confidence)
        )
        return diagnoses

    def _compute_confidence(
        self,
        rule: Dict,
        data: Dict[str, Any],
        context: Dict[str, Any],
    ) -> float:
        """计算置信度"""
        base = 0.6
        # 上下文相关告警越多,置信度越高
        related_alerts = context.get('related_alerts', [])
        if len(related_alerts) >= 3:
            base += 0.2
        elif len(related_alerts) >= 1:
            base += 0.1
        # 近期有变更,置信度更高(可能是变更引发的)
        recent_changes = context.get('recent_changes', [])
        if recent_changes:
            base += 0.05
        return min(base, 1.0)

    def _build_causal_chain(
        self,
        rule: Dict,
        context: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        """构建因果链(树形结构)"""
        chain = [{
            'level': 0,
            'type': 'rule',
            'rule_id': rule['id'],
            'rule_name': rule['name'],
            'description': rule['description'],
        }]

        # 加入典型链
        if rule.get('typical_chain'):
            chain.append({
                'level': 1,
                'type': 'typical_chain',
                'description': rule['typical_chain'],
            })

        # 加入相关告警
        for alert in context.get('related_alerts', [])[:5]:
            chain.append({
                'level': 2,
                'type': 'correlated_alert',
                'title': alert.get('title'),
                'severity': alert.get('severity'),
            })

        # 加入近期变更
        for change in context.get('recent_changes', [])[:3]:
            chain.append({
                'level': 3,
                'type': 'recent_change',
                'description': change.get('description'),
                'time': change.get('create_time'),
            })

        return chain

    def _extract_related_metrics(
        self,
        rule: Dict,
        data: Dict[str, Any],
    ) -> Dict[str, Any]:
        """提取规则相关的指标"""
        related_keys = []
        if rule['domain'] == 'connection':
            related_keys = ['conn_usage_pct', 'qps', 'aborted_connects']
        elif rule['domain'] == 'sql':
            related_keys = ['slow_queries', 'qps', 'tps']
        elif rule['domain'] == 'lock':
            related_keys = ['locks', 'deadlocks']
        elif rule['domain'] == 'io':
            related_keys = ['tablespaces', 'log_switches_per_hour', 'arch_gap']
        return {k: data.get(k) for k in related_keys if k in data}

    def _rule_to_graph_key(self, rule: Dict) -> str:
        """规则 ID 转图谱 key"""
        return {
            'R031': 'tablespace_full',
            'R004': 'conn_usage_pct_high',
            'R011': 'slow_query_burst',
            'R051': 'replication_lag',
            'R052': 'replication_lag',
        }.get(rule['id'], '')


# ==========================================
# 便捷函数
# ==========================================
def run_rca_v2(
    current_data: Dict[str, Any],
    context: Optional[Dict[str, Any]] = None,
    db_type: str = 'oracle',
) -> List[Dict[str, Any]]:
    """
    便捷入口: 运行 RCA 2.0

    Returns:
        List[dict]: 诊断结果列表(按严重度+置信度排序)
    """
    engine = RCAEngineV2(db_type=db_type)
    diagnoses = engine.diagnose(current_data, context)
    return [d.to_dict() for d in diagnoses]


def get_rule_count() -> int:
    """返回规则总数"""
    return len(RULES_V2)
