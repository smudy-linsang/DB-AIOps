"""
配置参数合理性检查引擎 v1.0 (Phase 3 - 决策辅助)

功能:
- 检查数据库关键参数的合理性
- MySQL/Oracle/PostgreSQL 等主流数据库
- 20+ 条配置检查规则
- 提供优化建议

设计文档参考: DB_AIOps_DESIGN.md 3.7 节 (Phase 3 增强)
"""

import re
from datetime import datetime
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass, field


# ==========================================
# 数据结构
# ==========================================

@dataclass
class ConfigRule:
    """配置检查规则"""
    id: str
    name: str
    db_type: str  # oracle, mysql, pgsql, dm, gbase, tdsql, all
    category: str  # memory, storage, security, performance, backup
    param_name: str  # 参数名（支持正则）
    check_type: str  # range, compare, enumerate, custom
    warning_threshold: Any  # 警告阈值
    critical_threshold: Any  # 严重阈值
    description: str
    suggestion: str
    reason: str  # 为什么这个参数重要


@dataclass
class ConfigCheckResult:
    """配置检查结果"""
    rule_id: str
    rule_name: str
    param_name: str
    current_value: Any
    expected_range: str  # 期望范围描述
    severity: str  # ok, warning, critical
    description: str
    suggestion: str
    reason: str


@dataclass
class ConfigProfile:
    """配置检查报告"""
    db_type: str
    db_name: str
    total_checks: int
    passed: int
    warnings: int
    criticals: int
    score: float  # 0-100
    results: List[ConfigCheckResult] = field(default_factory=list)
    summary: str = ""
    recommendations: List[Dict] = field(default_factory=list)


# ==========================================
# 配置规则库
# ==========================================

# MySQL 配置规则
MYSQL_RULES = [
    # 内存相关
    ConfigRule(
        id='MYSQL-001',
        name='InnoDB Buffer Pool 大小',
        db_type='mysql',
        category='memory',
        param_name=r'^innodb_buffer_pool_size$',
        check_type='range',
        warning_threshold='< 1GB',
        critical_threshold='< 512MB',
        description='InnoDB 缓冲池大小配置',
        suggestion='建议设置为可用内存的 60-80%',
        reason='缓冲池太小会导致频繁磁盘 I/O'
    ),
    ConfigRule(
        id='MYSQL-002',
        name='InnoDB Log File 大小',
        db_type='mysql',
        category='storage',
        param_name=r'^innodb_log_file_size$',
        check_type='range',
        warning_threshold='< 256MB',
        critical_threshold='< 128MB',
        description='Redo 日志文件大小',
        suggestion='建议设置为 256MB-1GB',
        reason='日志文件太小会影响事务性能和崩溃恢复'
    ),
    ConfigRule(
        id='MYSQL-003',
        name='最大连接数',
        db_type='mysql',
        category='performance',
        param_name=r'^max_connections$',
        check_type='range',
        warning_threshold='< 100',
        critical_threshold='< 50',
        description='允许的最大连接数',
        suggestion='根据业务需求设置，建议 200-1000',
        reason='连接数不足会导致应用连接失败'
    ),
    ConfigRule(
        id='MYSQL-004',
        name='查询缓存已启用',
        db_type='mysql',
        category='performance',
        param_name=r'^query_cache_type$',
        check_type='enumerate',
        warning_threshold='ON',
        critical_threshold='ON',  # MySQL 8.0 已移除查询缓存
        description='查询缓存（MySQL 8.0 已移除）',
        suggestion='MySQL 8.0+ 不需要此配置，旧版本建议关闭',
        reason='查询缓存在高并发下反而影响性能'
    ),
    ConfigRule(
        id='MYSQL-005',
        name='慢查询日志',
        db_type='mysql',
        category='performance',
        param_name=r'^slow_query_log$',
        check_type='enumerate',
        warning_threshold='OFF',
        critical_threshold='OFF',
        description='慢查询日志开关',
        suggestion='建议开启，用于分析慢查询',
        reason='慢查询日志是性能优化的基础数据'
    ),
    ConfigRule(
        id='MYSQL-006',
        name='Long Query Time',
        db_type='mysql',
        category='performance',
        param_name=r'^long_query_time$',
        check_type='range',
        warning_threshold='> 10',
        critical_threshold='> 30',
        description='慢查询阈值（秒）',
        suggestion='建议设置为 1-5 秒',
        reason='阈值太高会漏掉需要优化的查询'
    ),
    ConfigRule(
        id='MYSQL-007',
        name='InnoDB Flush Log 策略',
        db_type='mysql',
        category='performance',
        param_name=r'^innodb_flush_log_at_trx_commit$',
        check_type='enumerate',
        warning_threshold='0',
        critical_threshold='0',
        description='事务提交时日志刷新策略',
        suggestion='生产环境建议使用 1（默认），性能要求高可用 2',
        reason='设为 0 或 2 会降低数据安全性'
    ),
    ConfigRule(
        id='MYSQL-008',
        name='Binlog 保留时间',
        db_type='mysql',
        category='backup',
        param_name=r'^expire_logs_days$',
        check_type='range',
        warning_threshold='< 3',
        critical_threshold='< 1',
        description='Binlog 保留天数',
        suggestion='建议设置为 7-30 天',
        reason='保留时间太短会影响数据恢复和复制'
    ),
    ConfigRule(
        id='MYSQL-009',
        name='字符集配置',
        db_type='mysql',
        category='security',
        param_name=r'^character_set_server$',
        check_type='enumerate',
        warning_threshold='utf8mb3',
        critical_threshold='latin1',
        description='服务器字符集',
        suggestion='建议使用 utf8mb4',
        reason='utf8mb3 不支持表情符号，latin1 会导致乱码'
    ),
    ConfigRule(
        id='MYSQL-010',
        name='时区配置',
        db_type='mysql',
        category='security',
        param_name=r'^default_time_zone$',
        check_type='compare',
        warning_threshold='+00:00',
        critical_threshold='+00:00',
        description='默认时区',
        suggestion='应设置为正确的业务时区',
        reason='时区错误会导致时间字段错乱'
    ),
]

# PostgreSQL 配置规则
POSTGRESQL_RULES = [
    ConfigRule(
        id='PGSQL-001',
        name='共享缓冲区大小',
        db_type='pgsql',
        category='memory',
        param_name=r'^shared_buffers$',
        check_type='range',
        warning_threshold='< 1GB',
        critical_threshold='< 256MB',
        description='共享缓冲区大小',
        suggestion='建议设置为系统内存的 25%',
        reason='共享缓冲区太小严重影响查询性能'
    ),
    ConfigRule(
        id='PGSQL-002',
        name='工作内存',
        db_type='pgsql',
        category='memory',
        param_name=r'^work_mem$',
        check_type='range',
        warning_threshold='< 64MB',
        critical_threshold='< 16MB',
        description='每个查询的工作内存',
        suggestion='建议设置为 64MB-256MB',
        reason='工作内存太小会导致排序和哈希操作使用磁盘'
    ),
    ConfigRule(
        id='PGSQL-003',
        name='维护内存',
        db_type='pgsql',
        category='memory',
        param_name=r'^maintenance_work_mem$',
        check_type='range',
        warning_threshold='< 128MB',
        critical_threshold='< 64MB',
        description='维护操作内存',
        suggestion='建议设置为 128MB-1GB',
        reason='创建索引等维护操作需要较大内存'
    ),
    ConfigRule(
        id='PGSQL-004',
        name='最大连接数',
        db_type='pgsql',
        category='performance',
        param_name=r'^max_connections$',
        check_type='range',
        warning_threshold='< 100',
        critical_threshold='< 50',
        description='允许的最大连接数',
        suggestion='建议 100-500，使用连接池更佳',
        reason='连接数不足会导致应用连接失败'
    ),
    ConfigRule(
        id='PGSQL-005',
        name='WAL 归档',
        db_type='pgsql',
        category='backup',
        param_name=r'^wal_level$',
        check_type='enumerate',
        warning_threshold='minimal',
        critical_threshold='minimal',
        description='WAL 级别',
        suggestion='建议使用 replica（流复制）或 logical',
        reason='minimal 级别不支持复制和归档'
    ),
    ConfigRule(
        id='PGSQL-006',
        name='同步提交',
        db_type='pgsql',
        category='performance',
        param_name=r'^synchronous_commit$',
        check_type='enumerate',
        warning_threshold='off',
        critical_threshold='off',
        description='事务同步提交策略',
        suggestion='性能要求高可用 on，安全性要求高用 remote_apply',
        reason='关闭同步提交会降低数据安全性'
    ),
    ConfigRule(
        id='PGSQL-007',
        name='统计信息收集',
        db_type='pgsql',
        category='performance',
        param_name=r'^track_activities$',
        check_type='enumerate',
        warning_threshold='off',
        critical_threshold='off',
        description='是否跟踪查询统计',
        suggestion='建议开启，用于性能分析',
        reason='关闭会影响查询规划和性能诊断'
    ),
    ConfigRule(
        id='PGSQL-008',
        name='随机页面成本',
        db_type='pgsql',
        category='performance',
        param_name=r'^random_page_cost$',
        check_type='range',
        warning_threshold='> 4.0',
        critical_threshold='> 10.0',
        description='随机页面读取成本',
        suggestion='SSD 设置 1.1，机械盘 4.0',
        reason='错误设置会导致错误执行计划'
    ),
    ConfigRule(
        id='PGSQL-009',
        name='有效缓存大小',
        db_type='pgsql',
        category='memory',
        param_name=r'^effective_cache_size$',
        check_type='range',
        warning_threshold='< 2GB',
        critical_threshold='< 1GB',
        description='查询规划器假设的可用缓存',
        suggestion='建议设置为系统内存的 75%',
        reason='影响查询规划器的成本计算'
    ),
    ConfigRule(
        id='PGSQL-010',
        name='连接活跃超时',
        db_type='pgsql',
        category='performance',
        param_name=r'^idle_session_timeout$',
        check_type='range',
        warning_threshold='0',
        critical_threshold='0',
        description='空闲会话超时（毫秒）',
        suggestion='建议设置 1-8 小时',
        reason='防止空闲连接占用资源'
    ),
]

# Oracle 配置规则
ORACLE_RULES = [
    ConfigRule(
        id='ORACLE-001',
        name='SGA Target 大小',
        db_type='oracle',
        category='memory',
        param_name=r'^sga_target$',
        check_type='range',
        warning_threshold='< 2GB',
        critical_threshold='< 1GB',
        description='SGA 目标大小',
        suggestion='建议 2-16GB，根据业务调整',
        reason='SGA 太小会导致频繁磁盘 I/O'
    ),
    ConfigRule(
        id='ORACLE-002',
        name='PGA Aggregate Target',
        db_type='oracle',
        category='memory',
        param_name=r'^pga_aggregate_target$',
        check_type='range',
        warning_threshold='< 1GB',
        critical_threshold='< 512MB',
        description='PGA 聚合目标',
        suggestion='建议 1-8GB',
        reason='PGA 太小影响排序和哈希操作'
    ),
    ConfigRule(
        id='ORACLE-003',
        name='Process 参数',
        db_type='oracle',
        category='performance',
        param_name=r'^processes$',
        check_type='range',
        warning_threshold='< 150',
        critical_threshold='< 100',
        description='最大进程数',
        suggestion='根据并发需求设置，建议 150-1000',
        reason='进程数不足会导致连接失败'
    ),
    ConfigRule(
        id='ORACLE-004',
        name='Undo 表空间',
        db_type='oracle',
        category='storage',
        param_name=r'^undo_tablespace$',
        check_type='custom',
        warning_threshold='N/A',
        critical_threshold='N/A',
        description='Undo 表空间配置',
        suggestion='确保有足够的 Undo 表空间',
        reason='Undo 不足会导致快照过旧错误'
    ),
    ConfigRule(
        id='ORACLE-005',
        name='Redo Log 大小',
        db_type='oracle',
        category='storage',
        param_name=r'^redo_log_size$',
        check_type='range',
        warning_threshold='< 256MB',
        critical_threshold='< 128MB',
        description='Redo 日志文件大小',
        suggestion='建议 256MB-1GB',
        reason='日志太小会导致日志切换频繁'
    ),
    ConfigRule(
        id='ORACLE-006',
        name='Log Archive 模式',
        db_type='oracle',
        category='backup',
        param_name=r'^log_archive$',
        check_type='enumerate',
        warning_threshold='FALSE',
        critical_threshold='FALSE',
        description='归档日志模式',
        suggestion='生产库必须开启归档',
        reason='归档是数据恢复和备份的基础'
    ),
    ConfigRule(
        id='ORACLE-007',
        name='闪回恢复区',
        db_type='oracle',
        category='backup',
        param_name=r'^db_recovery_file_dest_size$',
        check_type='range',
        warning_threshold='< 10GB',
        critical_threshold='< 5GB',
        description='闪回恢复区大小',
        suggestion='建议 10GB 以上',
        reason='空间不足会导致数据库挂起'
    ),
    ConfigRule(
        id='ORACLE-008',
        name='字符集',
        db_type='oracle',
        category='security',
        param_name=r'^nls_characterset$',
        check_type='enumerate',
        warning_threshold='WE8ISO8859P1',
        critical_threshold='US7ASCII',
        description='数据库字符集',
        suggestion='建议 AL32UTF8 或 ZHS16GBK',
        reason='字符集不匹配会导致数据乱码'
    ),
    ConfigRule(
        id='ORACLE-009',
        name='审计跟踪',
        db_type='oracle',
        category='security',
        param_name=r'^audit_trail$',
        check_type='enumerate',
        warning_threshold='NONE',
        critical_threshold='NONE',
        description='审计跟踪',
        suggestion='建议开启 DB 或 OS',
        reason='审计跟踪是安全合规的基础'
    ),
    ConfigRule(
        id='ORACLE-010',
        name='resource_limit',
        db_type='oracle',
        category='security',
        param_name=r'^resource_limit$',
        check_type='enumerate',
        warning_threshold='FALSE',
        critical_threshold='FALSE',
        description='资源限制',
        suggestion='建议 TRUE，防止资源耗尽',
        reason='关闭资源限制可能导致某个会话耗尽资源'
    ),
]

# 通用规则（适用于所有数据库）
COMMON_RULES = [
    ConfigRule(
        id='COMMON-001',
        name='连接超时配置',
        db_type='all',
        category='performance',
        param_name=r'connect.*timeout',
        check_type='range',
        warning_threshold='< 5',
        critical_threshold='< 1',
        description='连接超时时间（秒）',
        suggestion='建议 10-30 秒',
        reason='超时太短容易误判，太长会延长故障时间'
    ),
    ConfigRule(
        id='COMMON-002',
        name='查询超时配置',
        db_type='all',
        category='performance',
        param_name=r'query.*timeout|statement.*timeout',
        check_type='range',
        warning_threshold='< 30',
        critical_threshold='< 10',
        description='查询超时时间（秒）',
        suggestion='建议 60-300 秒',
        reason='超时太短会中断正常查询'
    ),
]

# 达梦数据库规则
DM_RULES = [
    ConfigRule(
        id='DM-001',
        name='内存池大小',
        db_type='dm',
        category='memory',
        param_name=r'^MEMORY_POOL$',
        check_type='range',
        warning_threshold='< 512MB',
        critical_threshold='< 256MB',
        description='共享内存池大小',
        suggestion='建议 1-8GB',
        reason='内存池太小会影响性能'
    ),
    ConfigRule(
        id='DM-002',
        name='最大连接数',
        db_type='dm',
        category='performance',
        param_name=r'^MAX_SESSIONS$',
        check_type='range',
        warning_threshold='< 100',
        critical_threshold='< 50',
        description='最大会话数',
        suggestion='建议 100-1000',
        reason='连接数不足会导致连接失败'
    ),
]

# 合并所有规则
ALL_RULES = (
    MYSQL_RULES + 
    POSTGRESQL_RULES + 
    ORACLE_RULES + 
    COMMON_RULES +
    DM_RULES
)


# ==========================================
# 配置检查引擎
# ==========================================

class ConfigAdvisor:
    """
    配置参数合理性检查引擎
    
    功能:
    - 采集数据库配置参数
    - 按规则检查合理性
    - 生成优化建议
    """
    
    def __init__(self, config):
        self.config = config
        self.db_type = config.db_type
    
    def collect_parameters(self, conn) -> Dict[str, Any]:
        """
        采集数据库配置参数
        
        参数:
            conn: 数据库连接
        
        返回:
            参数字典 {param_name: value}
        """
        db_type = self.db_type
        
        if db_type == 'mysql':
            return self._collect_mysql_params(conn)
        elif db_type == 'pgsql':
            return self._collect_pgsql_params(conn)
        elif db_type == 'oracle':
            return self._collect_oracle_params(conn)
        elif db_type == 'dm':
            return self._collect_dm_params(conn)
        elif db_type in ('tdsql', 'gbase'):
            return self._collect_mysql_params(conn)  # 复用 MySQL 协议
        else:
            return {}
    
    def _collect_mysql_params(self, conn) -> Dict[str, Any]:
        """采集 MySQL 参数"""
        cursor = conn.cursor()
        params = {}
        
        try:
            cursor.execute("SHOW VARIABLES")
            for row in cursor.fetchall():
                params[row[0].lower()] = row[1]
        except Exception as e:
            # 某些变量可能无法访问
            pass
        
        cursor.close()
        return params
    
    def _collect_pgsql_params(self, conn) -> Dict[str, Any]:
        """采集 PostgreSQL 参数"""
        cursor = conn.cursor()
        params = {}
        
        try:
            cursor.execute("SELECT name, setting FROM pg_settings")
            for row in cursor.fetchall():
                params[row[0].lower()] = row[1]
        except Exception as e:
            pass
        
        cursor.close()
        return params
    
    def _collect_oracle_params(self, conn) -> Dict[str, Any]:
        """采集 Oracle 参数"""
        cursor = conn.cursor()
        params = {}
        
        try:
            cursor.execute("""
                SELECT name, value 
                FROM v$parameter 
                WHERE isdeprecated = 'FALSE'
            """)
            for row in cursor.fetchall():
                params[row[0].lower()] = row[1]
        except Exception as e:
            # 可能没有权限访问某些视图
            pass
        
        cursor.close()
        return params
    
    def _collect_dm_params(self, conn) -> Dict[str, Any]:
        """采集达梦参数"""
        cursor = conn.cursor()
        params = {}
        
        try:
            cursor.execute("SELECT name, value FROM V$PARAMETER")
            for row in cursor.fetchall():
                params[row[0].lower()] = row[1]
        except Exception as e:
            pass
        
        cursor.close()
        return params
    
    def check_configuration(self, conn) -> ConfigProfile:
        """
        执行配置检查
        
        参数:
            conn: 数据库连接
        
        返回:
            ConfigProfile 配置检查报告
        """
        # 采集当前配置
        params = self.collect_parameters(conn)
        
        # 获取适用的规则
        applicable_rules = self._get_applicable_rules()
        
        # 执行检查
        results = []
        for rule in applicable_rules:
            result = self._check_rule(rule, params)
            if result:
                results.append(result)
        
        # 汇总结果
        passed = sum(1 for r in results if r.severity == 'ok')
        warnings = sum(1 for r in results if r.severity == 'warning')
        criticals = sum(1 for r in results if r.severity == 'critical')
        
        total = len(results)
        score = (passed / total * 100) if total > 0 else 100
        
        # 生成建议
        recommendations = self._generate_recommendations(results)
        
        # 生成摘要
        summary = self._generate_summary(passed, warnings, criticals, total, score)
        
        return ConfigProfile(
            db_type=self.db_type,
            db_name=self.config.name,
            total_checks=total,
            passed=passed,
            warnings=warnings,
            criticals=criticals,
            score=score,
            results=results,
            summary=summary,
            recommendations=recommendations,
        )
    
    def _get_applicable_rules(self) -> List[ConfigRule]:
        """获取适用的规则"""
        applicable = []
        for rule in ALL_RULES:
            if rule.db_type in (self.db_type, 'all'):
                applicable.append(rule)
        return applicable
    
    def _check_rule(self, rule: ConfigRule, params: Dict[str, Any]) -> Optional[ConfigCheckResult]:
        """检查单条规则"""
        # 查找匹配的参数
        current_value = None
        matched_param = None
        
        for param_name, value in params.items():
            if re.match(rule.param_name, param_name, re.IGNORECASE):
                if current_value is None:
                    current_value = value
                    matched_param = param_name
        
        if matched_param is None:
            # 参数不存在
            return ConfigCheckResult(
                rule_id=rule.id,
                rule_name=rule.name,
                param_name=rule.param_name,
                current_value='N/A',
                expected_range='请确认参数名',
                severity='warning',
                description=f"参数 {rule.param_name} 未找到",
                suggestion='请确认该参数是否适用于此数据库版本',
                reason=rule.reason,
            )
        
        # 根据检查类型进行判断
        severity, expected = self._evaluate_rule(rule, current_value)
        
        return ConfigCheckResult(
            rule_id=rule.id,
            rule_name=rule.name,
            param_name=matched_param,
            current_value=current_value,
            expected_range=expected,
            severity=severity,
            description=rule.description,
            suggestion=rule.suggestion,
            reason=rule.reason,
        )
    
    def _evaluate_rule(self, rule: ConfigRule, current_value: str) -> Tuple[str, str]:
        """评估规则是否满足"""
        check_type = rule.check_type
        
        if check_type == 'enumerate':
            # 枚举检查
            if str(current_value).upper() == str(rule.warning_threshold).upper():
                return 'warning', f"应为 {rule.suggestion.split('，')[0]}"
            return 'ok', rule.suggestion
        
        elif check_type == 'range':
            # 范围检查
            severity, expected = self._check_range(rule, current_value)
            return severity, expected
        
        elif check_type == 'compare':
            # 比较检查
            if str(current_value) == str(rule.warning_threshold):
                return 'warning', rule.suggestion
            return 'ok', rule.suggestion
        
        elif check_type == 'custom':
            # 自定义检查
            return 'ok', rule.suggestion
        
        return 'ok', 'N/A'
    
    def _check_range(self, rule: ConfigRule, current_value: str) -> Tuple[str, str]:
        """检查范围"""
        # 解析阈值
        warning = rule.warning_threshold
        critical = rule.critical_threshold
        
        # 尝试解析数值
        try:
            # 处理带单位的值如 "1GB", "256MB", "10s"
            current_num = self._parse_size_value(current_value)
            warning_num = self._parse_size_value(str(warning))
            critical_num = self._parse_size_value(str(critical))
            
            if warning.startswith('>'):
                # 大于检查
                if current_num <= warning_num:
                    return 'warning', f"应大于 {warning}"
            elif warning.startswith('<'):
                # 小于检查
                if current_num >= critical_num:
                    return 'critical', f"必须小于 {critical}"
                elif current_num >= warning_num:
                    return 'warning', f"应小于 {warning}"
            
            return 'ok', f"{warning} - {critical}"
        
        except:
            # 无法解析，按字符串处理
            if str(current_value) == str(warning):
                return 'warning', rule.suggestion
        
        return 'ok', rule.suggestion
    
    def _parse_size_value(self, value: str) -> float:
        """解析带单位的值（GB, MB, KB, s 等）"""
        value = str(value).strip().upper()
        
        multipliers = {
            'G': 1024 * 1024 * 1024,
            'M': 1024 * 1024,
            'K': 1024,
            'T': 1024 * 1024 * 1024 * 1024,
        }
        
        for unit, mult in multipliers.items():
            if value.endswith(unit):
                num = float(value[:-1])
                return num * mult
        
        # 尝试直接解析为数字
        return float(re.sub(r'[^\d.]', '', value)) if value else 0
    
    def _generate_recommendations(self, results: List[ConfigCheckResult]) -> List[Dict]:
        """生成优化建议"""
        recommendations = []
        
        # 按严重程度排序
        criticals = [r for r in results if r.severity == 'critical']
        warnings = [r for r in results if r.severity == 'warning']
        
        for r in criticals:
            recommendations.append({
                'priority': 'critical',
                'category': 'configuration',
                'rule_id': r.rule_id,
                'rule_name': r.rule_name,
                'current_value': r.current_value,
                'expected': r.expected_range,
                'suggestion': r.suggestion,
                'reason': r.reason,
            })
        
        for r in warnings:
            recommendations.append({
                'priority': 'warning',
                'category': 'configuration',
                'rule_id': r.rule_id,
                'rule_name': r.rule_name,
                'current_value': r.current_value,
                'expected': r.expected_range,
                'suggestion': r.suggestion,
                'reason': r.reason,
            })
        
        return recommendations
    
    def _generate_summary(self, passed: int, warnings: int, criticals: int, 
                         total: int, score: float) -> str:
        """生成摘要文本"""
        if total == 0:
            return "无法执行配置检查"
        
        if score >= 90:
            status = "✅ 优秀"
        elif score >= 70:
            status = "🟡 良好"
        elif score >= 50:
            status = "🟠 需要改进"
        else:
            status = "🔴 严重不足"
        
        parts = [
            f"配置评分: {score:.1f}分 {status}",
            f"检查项: {total} 项",
            f"通过: {passed} 项",
        ]
        
        if warnings > 0:
            parts.append(f"警告: {warnings} 项")
        
        if criticals > 0:
            parts.append(f"严重: {criticals} 项")
        
        return " | ".join(parts)


# ==========================================
# 使用示例
# ==========================================
"""
# 在 start_monitor.py 中集成:

from monitor.config_advisor import ConfigAdvisor

# 在 process_result 方法中添加配置检查:
if current_status == 'UP':
    config_advisor = ConfigAdvisor(config)
    profile = config_advisor.check_configuration(conn)
    
    # 检查评分
    if profile.score < 70:
        for rec in profile.recommendations:
            if rec['priority'] == 'critical':
                am.fire(
                    alert_type='config',
                    metric_key=rec['rule_id'],
                    title=f"⚠️ 配置问题: {rec['rule_name']}",
                    description=rec['suggestion'],
                    severity='warning',
                )
    
    # 打印摘要
    print(f"配置评分: {profile.score}")
    for r in profile.results[:5]:
        if r.severity != 'ok':
            print(f"  [{r.severity}] {r.rule_name}: {r.current_value} (期望: {r.expected_range})")
"""