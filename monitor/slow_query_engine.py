"""
慢查询分析引擎 v1.0 (Phase 3 - 决策辅助)

功能:
- 采集各数据库慢查询日志/统计信息
- MySQL: slow_query_log 解析
- PostgreSQL: pg_stat_statements
- Oracle: AWR 报告数据（通过 DBA_hist_* 视图）
- 分析慢查询特征，识别优化机会

设计文档参考: DB_AIOps_DESIGN.md 3.6 节 (Phase 3 增强)
"""

import json
import re
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any, Tuple
from collections import defaultdict

from monitor.models import DatabaseConfig, MonitorLog


# ==========================================
# 慢查询存储模型 (用于累积分析)
# ==========================================

class SlowQueryRecord:
    """单条慢查询记录"""
    
    def __init__(self, query_text: str, execution_time_ms: float,
                 lock_time_ms: float = 0, rows_sent: int = 0,
                 rows_examined: int = 0, db_name: str = ''):
        self.query_text = query_text
        self.execution_time_ms = execution_time_ms
        self.lock_time_ms = lock_time_ms
        self.rows_sent = rows_sent
        self.rows_examined = rows_examined
        self.db_name = db_name
        self.fingerprint = self._normalize_query(query_text)
    
    def _normalize_query(self, query: str) -> str:
        """将 SQL 归一化为指纹（去掉字面量）用于聚合"""
        # 替换数字
        normalized = re.sub(r'\b\d+\b', '?', query)
        # 替换字符串
        normalized = re.sub(r"'[^']*'", '?', normalized)
        # 替换 IN (...) 中的多个值
        normalized = re.sub(r'\(\s*\?(\s*,\s*\?)*\s*\)', '(...)', normalized)
        return normalized.strip()
    
    def to_dict(self) -> Dict:
        return {
            'query_text': self.query_text,
            'fingerprint': self.fingerprint,
            'execution_time_ms': self.execution_time_ms,
            'lock_time_ms': self.lock_time_ms,
            'rows_sent': self.rows_sent,
            'rows_examined': self.rows_examined,
            'db_name': self.db_name,
        }


# ==========================================
# MySQL 慢查询解析器
# ==========================================

class MySQLSlowQueryParser:
    """MySQL 慢查询日志解析器"""
    
    # MySQL 慢查询日志格式正则
    SLOW_QUERY_PATTERN = re.compile(
        r'# Time: (\d{6}\s+\d{1,2}:\d{2}:\d{2})\n'
        r'# User@Host: ([^\[]+)\[@([^\]]+)\]\s+Id:\s+(\d+)\n'
        r'# Query_time: (\d+\.\d+)\s+Lock_time: (\d+\.\d+)\s+'
        r'Rows_sent: (\d+)\s+Rows_examined: (\d+)\n'
        r'(.*?)(?=\n# Time:|\Z)',
        re.DOTALL | re.MULTILINE
    )
    
    def __init__(self, slow_query_log_content: str):
        self.content = slow_query_log_content
    
    def parse(self) -> List[SlowQueryRecord]:
        """解析慢查询日志"""
        records = []
        
        matches = self.SLOW_QUERY_PATTERN.finditer(self.content)
        for match in matches:
            query_time = float(match.group(5))
            lock_time = float(match.group(6))
            rows_sent = int(match.group(7))
            rows_examined = int(match.group(8))
            query_text = match.group(9).strip()
            
            if query_text.upper().startswith('USE '):
                # 跳过 USE 语句
                continue
                
            record = SlowQueryRecord(
                query_text=query_text,
                execution_time_ms=query_time * 1000,  # 转换为毫秒
                lock_time_ms=lock_time * 1000,
                rows_sent=rows_sent,
                rows_examined=rows_examined,
            )
            records.append(record)
        
        return records


# ==========================================
# PostgreSQL pg_stat_statements 采集器
# ==========================================

class PostgreSQLSlowQueryCollector:
    """PostgreSQL 慢查询统计信息采集器
    
    需要启用 pg_stat_statements 扩展:
    CREATE EXTENSION IF NOT EXISTS pg_stat_statements;
    GRANT SELECT ON pg_stat_statements TO db_monitor;
    """
    
    # 采集 SQL
    QUERY_SQL = """
    SELECT 
        query,
        calls,
        total_exec_time / 1000.0 as total_time_sec,
        min_exec_time / 1000.0 as min_time_sec,
        max_exec_time / 1000.0 as max_time_sec,
        mean_exec_time / 1000.0 as mean_time_sec,
        stddev_exec_time / 1000.0 as stddev_time_sec,
        rows,
        shared_blks_hit,
        shared_blks_read,
        shared_blks_written,
        local_blks_hit,
        local_blks_read,
        local_blks_written,
        temp_blks_read,
        temp_blks_written
    FROM pg_stat_statements
    WHERE calls > 0
    ORDER BY total_exec_time DESC
    LIMIT %(limit)s
    """
    
    def __init__(self, connection):
        self.conn = connection
    
    def collect(self, limit: int = 100) -> List[Dict]:
        """采集慢查询统计"""
        cursor = self.conn.cursor()
        cursor.execute(self.QUERY_SQL, {'limit': limit})
        
        results = []
        for row in cursor.fetchall():
            results.append({
                'query': row[0],
                'calls': row[1],
                'total_time_sec': row[2],
                'min_time_sec': row[3],
                'max_time_sec': row[4],
                'mean_time_sec': row[5],
                'stddev_time_sec': row[6],
                'rows': row[7],
                'shared_blks_hit': row[8],
                'shared_blks_read': row[9],
                'shared_blks_written': row[10],
                'local_blks_hit': row[11],
                'local_blks_read': row[12],
                'local_blks_written': row[13],
                'temp_blks_read': row[14],
                'temp_blks_written': row[15],
            })
        
        cursor.close()
        return results


# ==========================================
# Oracle AWR 数据采集器
# ==========================================

class OracleAWRCollector:
    """Oracle AWR 数据采集器
    
    需要 DBA 权限访问 AWR 视图:
    GRANT SELECT ON v_$session TO db_monitor;
    GRANT SELECT ON dba_hist_sqlstat TO db_monitor;
    GRANT SELECT ON dba_hist_sqltext TO db_monitor;
    """
    
    # 采集 Top SQL 的 SQL
    TOP_SQL_SQL = """
    SELECT 
        s.sql_id,
        t.sql_text,
        st.executions_total,
        st.elapsed_time_total / 1000000.0 as elapsed_sec_total,
        st.elapsed_time_total / NULLIF(st.executions_total, 0) / 1000000.0 as elapsed_sec_per_exec,
        st.buffer_gets_total,
        st.buffer_gets_total / NULLIF(st.executions_total, 0) as buffer_gets_per_exec,
        st.disk_reads_total,
        st.disk_reads_total / NULLIF(st.executions_total, 0) as disk_reads_per_exec,
        st.rows_processed_total,
        st.rows_processed_total / NULLIF(st.executions_total, 0) as rows_per_exec,
        st.optimizer_cost,
        st.first_load_time,
        st.last_load_time
    FROM dba_hist_sqlstat st
    JOIN dba_hist_sqltext t ON st.sql_id = t.sql_id
    JOIN (SELECT sql_id, MAX(snap_id) as max_snap FROM dba_hist_sqlstat GROUP BY sql_id) s
        ON st.sql_id = s.sql_id AND st.snap_id = s.max_snap
    WHERE st.executions_total > 0
    ORDER BY st.elapsed_time_total DESC
    FETCH FIRST %(limit)s ROWS ONLY
    """
    
    # 采集 ASH 数据（活动会话历史）用于实时分析
    ASH_SQL = """
    SELECT 
        s.sql_id,
        s.event,
        s.sql_opname,
        s.wait_class,
        s.session_state,
        s.p1text,
        s.p1,
        s.p2text,
        s.p2,
        s.p3text,
        s.p3,
        COUNT(*) as sample_count
    FROM v$active_session_history s
    WHERE s.sql_id IS NOT NULL
      AND s.sample_time > SYSDATE - INTERVAL '30' MINUTE
    GROUP BY 
        s.sql_id, s.event, s.sql_opname, s.wait_class, s.session_state,
        s.p1text, s.p1, s.p2text, s.p2, s.p3text, s.p3
    ORDER BY sample_count DESC
    FETCH FIRST %(limit)s ROWS ONLY
    """
    
    def __init__(self, connection):
        self.conn = connection
    
    def collect_top_sql(self, limit: int = 50) -> List[Dict]:
        """采集 Top SQL 统计"""
        cursor = self.conn.cursor()
        cursor.execute(self.TOP_SQL_SQL, {'limit': limit})
        
        results = []
        for row in cursor.fetchall():
            results.append({
                'sql_id': row[0],
                'sql_text': row[1][:500] if row[1] else '',  # 截断长 SQL
                'executions': row[2],
                'elapsed_sec_total': row[3],
                'elapsed_sec_per_exec': row[4],
                'buffer_gets_total': row[5],
                'buffer_gets_per_exec': row[6],
                'disk_reads_total': row[7],
                'disk_reads_per_exec': row[8],
                'rows_processed': row[9],
                'rows_per_exec': row[10],
                'optimizer_cost': row[11],
                'first_load_time': str(row[12]) if row[12] else None,
                'last_load_time': str(row[13]) if row[13] else None,
            })
        
        cursor.close()
        return results
    
    def collect_ash(self, limit: int = 50) -> List[Dict]:
        """采集活动会话历史（实时分析）"""
        cursor = self.conn.cursor()
        cursor.execute(self.ASH_SQL, {'limit': limit})
        
        results = []
        for row in cursor.fetchall():
            results.append({
                'sql_id': row[0],
                'event': row[1],
                'sql_opname': row[2],
                'wait_class': row[3],
                'session_state': row[4],
                'p1_text': row[5],
                'p1': row[6],
                'p2_text': row[7],
                'p2': row[8],
                'p3_text': row[9],
                'p3': row[10],
                'sample_count': row[11],
            })
        
        cursor.close()
        return results


# ==========================================
# 慢查询分析引擎
# ==========================================

class SlowQueryEngine:
    """
    慢查询分析引擎
    
    功能:
    - 统一采集接口，支持 MySQL/PostgreSQL/Oracle
    - 慢查询特征分析
    - 识别优化机会（缺失索引、全表扫描、排序等）
    - 生成优化建议
    """
    
    # 慢查询阈值（毫秒）
    DEFAULT_THRESHOLD_MS = 1000  # 1秒
    
    # 高频执行阈值（次数/小时）
    HIGH_FREQUENCY_THRESHOLD = 100
    
    def __init__(self, config: DatabaseConfig):
        self.config = config
    
    def collect_slow_queries(self, conn, db_type: str = None) -> List[Dict]:
        """
        采集慢查询数据
        
        参数:
            conn: 数据库连接
            db_type: 数据库类型，如果为 None 则使用 config.db_type
        
        返回:
            慢查询记录列表
        """
        db_type = db_type or self.config.db_type
        
        if db_type == 'mysql':
            return self._collect_mysql_slow_queries(conn)
        elif db_type == 'pgsql':
            return self._collect_pgsql_slow_queries(conn)
        elif db_type == 'oracle':
            return self._collect_oracle_slow_queries(conn)
        elif db_type in ('tdsql', 'gbase'):
            # TDSQL 和 Gbase 使用 MySQL 协议
            return self._collect_mysql_slow_queries(conn)
        else:
            return []
    
    def _collect_mysql_slow_queries(self, conn) -> List[Dict]:
        """采集 MySQL 慢查询（通过 performance_schema）"""
        cursor = conn.cursor()
        
        # 从 performance_schema.events_statements_summary_by_digest 采集
        sql = """
        SELECT 
            DIGEST_TEXT as query,
            COUNT_STAR as exec_count,
            SUM_TIMER_WAIT / 1000000000 as total_time_sec,
            MIN_TIMER_WAIT / 1000000000 as min_time_sec,
            MAX_TIMER_WAIT / 1000000000 as max_time_sec,
            AVG_TIMER_WAIT / 1000000000 as avg_time_sec,
            SUM_ROWS_EXAMINED as rows_examined,
            SUM_ROWS_SENT as rows_sent,
            SUM_SORT_ROWS as sort_rows,
            SUM_NO_INDEX_USED as no_index_used,
            SUM_NO_GOOD_INDEX_USED as no_good_index_used,
            FIRST_SEEN as first_seen,
            LAST_SEEN as last_seen
        FROM performance_schema.events_statements_summary_by_digest
        WHERE DIGEST_TEXT IS NOT NULL
          AND COUNT_STAR > 0
          AND SUM_TIMER_WAIT / 1000000000 > %s  -- 超过阈值（秒）
        ORDER BY SUM_TIMER_WAIT DESC
        LIMIT 100
        """
        
        threshold_sec = self.DEFAULT_THRESHOLD_MS / 1000.0
        cursor.execute(sql, (threshold_sec,))
        
        results = []
        for row in cursor.fetchall():
            results.append({
                'db_type': 'mysql',
                'query': row[0],
                'exec_count': row[1],
                'total_time_sec': row[2],
                'min_time_sec': row[3],
                'max_time_sec': row[4],
                'avg_time_sec': row[5],
                'rows_examined': row[6] or 0,
                'rows_sent': row[7] or 0,
                'sort_rows': row[8] or 0,
                'no_index_used': row[9] or 0,
                'no_good_index_used': row[10] or 0,
                'first_seen': str(row[11]) if row[11] else None,
                'last_seen': str(row[12]) if row[12] else None,
            })
        
        cursor.close()
        return results
    
    def _collect_pgsql_slow_queries(self, conn) -> List[Dict]:
        """采集 PostgreSQL 慢查询（pg_stat_statements）"""
        collector = PostgreSQLSlowQueryCollector(conn)
        raw_results = collector.collect(limit=100)
        
        results = []
        for row in raw_results:
            # 过滤超过阈值的查询
            if row['max_time_sec'] * 1000 < self.DEFAULT_THRESHOLD_MS:
                continue
            
            results.append({
                'db_type': 'postgresql',
                'query': row['query'],
                'exec_count': row['calls'],
                'total_time_sec': row['total_time_sec'],
                'min_time_sec': row['min_time_sec'],
                'max_time_sec': row['max_time_sec'],
                'avg_time_sec': row['mean_time_sec'],
                'stddev_time_sec': row['stddev_time_sec'],
                'rows_examined': row['rows'] or 0,
                'rows_sent': 0,  # pg_stat_statements 不提供此字段
                'shared_blks_hit': row['shared_blks_hit'],
                'shared_blks_read': row['shared_blks_read'],
                'temp_blks_read': row['temp_blks_read'],
                'no_index_used': 0,  # 需要额外查询
            })
        
        return results
    
    def _collect_oracle_slow_queries(self, conn) -> List[Dict]:
        """采集 Oracle 慢查询（AWR 数据）"""
        collector = OracleAWRCollector(conn)
        raw_results = collector.collect_top_sql(limit=50)
        
        results = []
        for row in raw_results:
            # 过滤超过阈值的查询
            if row['elapsed_sec_per_exec'] * 1000 < self.DEFAULT_THRESHOLD_MS:
                continue
            
            results.append({
                'db_type': 'oracle',
                'sql_id': row['sql_id'],
                'query': row['sql_text'],
                'exec_count': row['executions'],
                'total_time_sec': row['elapsed_sec_total'],
                'min_time_sec': 0,  # AWR 不提供
                'max_time_sec': row['elapsed_sec_per_exec'] * 2,  # 估算
                'avg_time_sec': row['elapsed_sec_per_exec'],
                'buffer_gets_per_exec': row['buffer_gets_per_exec'],
                'disk_reads_per_exec': row['disk_reads_per_exec'],
                'rows_per_exec': row['rows_per_exec'],
                'optimizer_cost': row['optimizer_cost'],
            })
        
        return results
    
    def analyze_query_pattern(self, slow_queries: List[Dict]) -> Dict[str, Any]:
        """
        分析慢查询模式，识别优化机会
        
        返回:
            {
                'total_queries': int,
                'total_execution_time_sec': float,
                'patterns': [异常模式列表],
                'top_slow_queries': [Top N 慢查询],
                'optimization_suggestions': [优化建议列表],
            }
        """
        if not slow_queries:
            return {
                'total_queries': 0,
                'total_execution_time_sec': 0,
                'patterns': [],
                'top_slow_queries': [],
                'optimization_suggestions': ['当前无慢查询数据'],
            }
        
        total_time = sum(q.get('total_time_sec', 0) for q in slow_queries)
        total_executions = sum(q.get('exec_count', 1) for q in slow_queries)
        
        patterns = self._identify_patterns(slow_queries)
        suggestions = self._generate_suggestions(slow_queries, patterns)
        
        # Top 10 慢查询
        top_queries = sorted(
            slow_queries, 
            key=lambda x: x.get('total_time_sec', 0) * x.get('exec_count', 1), 
            reverse=True
        )[:10]
        
        return {
            'total_queries': len(slow_queries),
            'total_execution_time_sec': total_time,
            'total_executions': total_executions,
            'avg_time_per_query_ms': (total_time / len(slow_queries) * 1000) if slow_queries else 0,
            'patterns': patterns,
            'top_slow_queries': top_queries,
            'optimization_suggestions': suggestions,
            'timestamp': datetime.now().isoformat(),
        }
    
    def _identify_patterns(self, slow_queries: List[Dict]) -> List[Dict]:
        """识别异常模式"""
        patterns = []
        
        # 1. 全表扫描模式
        full_table_scans = []
        for q in slow_queries:
            query = q.get('query', '').upper()
            # 检测全表扫描关键词
            if any(kw in query for kw in ['SELECT *', 'FROM ', 'WHERE ']):
                rows_examined = q.get('rows_examined', 0)
                rows_sent = q.get('rows_sent', 0)
                # 如果检查行数远大于返回行数，可能是全表扫描
                if rows_examined > 0 and rows_sent > 0:
                    ratio = rows_examined / rows_sent
                    if ratio > 100:
                        full_table_scans.append({
                            'query': q.get('query', '')[:200],
                            'rows_examined': rows_examined,
                            'rows_sent': rows_sent,
                            'ratio': ratio,
                        })
        
        if full_table_scans:
            patterns.append({
                'type': 'full_table_scan',
                'count': len(full_table_scans),
                'description': '检测到疑似全表扫描查询',
                'severity': 'warning',
                'examples': full_table_scans[:3],
            })
        
        # 2. 高频小查询模式（执行次数很多但单次很快）
        high_freq_queries = []
        for q in slow_queries:
            exec_count = q.get('exec_count', 0)
            avg_time = q.get('avg_time_sec', 0) * 1000  # 转换为毫秒
            if exec_count > self.HIGH_FREQUENCY_THRESHOLD and avg_time < 100:
                high_freq_queries.append({
                    'query': q.get('query', '')[:200],
                    'exec_count': exec_count,
                    'avg_time_ms': avg_time,
                    'total_time_sec': q.get('total_time_sec', 0),
                })
        
        if high_freq_queries:
            patterns.append({
                'type': 'high_frequency',
                'count': len(high_freq_queries),
                'description': '高频小查询，可能存在 N+1 问题',
                'severity': 'warning',
                'examples': high_freq_queries[:3],
            })
        
        # 3. 排序操作模式
        sort_queries = []
        for q in slow_queries:
            if q.get('sort_rows', 0) > 0 or 'ORDER BY' in q.get('query', '').upper():
                sort_queries.append({
                    'query': q.get('query', '')[:200],
                    'sort_rows': q.get('sort_rows', 0),
                    'avg_time_ms': q.get('avg_time_sec', 0) * 1000,
                })
        
        if sort_queries:
            patterns.append({
                'type': 'sort_operations',
                'count': len(sort_queries),
                'description': '涉及排序操作的查询',
                'severity': 'info',
                'examples': sort_queries[:3],
            })
        
        # 4. 缺失索引模式
        no_index_queries = []
        for q in slow_queries:
            if q.get('no_index_used', 0) > 0 or q.get('no_good_index_used', 0) > 0:
                no_index_queries.append({
                    'query': q.get('query', '')[:200],
                    'no_index_used': q.get('no_index_used', 0),
                })
        
        if no_index_queries:
            patterns.append({
                'type': 'missing_index',
                'count': len(no_index_queries),
                'description': '检测到未使用索引的查询',
                'severity': 'warning',
                'examples': no_index_queries[:3],
            })
        
        # 5. 大数据量扫描模式
        large_scan_queries = []
        for q in slow_queries:
            rows_examined = q.get('rows_examined', 0)
            if rows_examined > 1000000:  # 超过100万行
                large_scan_queries.append({
                    'query': q.get('query', '')[:200],
                    'rows_examined': rows_examined,
                })
        
        if large_scan_queries:
            patterns.append({
                'type': 'large_scan',
                'count': len(large_scan_queries),
                'description': '大数据量扫描查询',
                'severity': 'critical',
                'examples': large_scan_queries[:3],
            })
        
        return patterns
    
    def _generate_suggestions(self, slow_queries: List[Dict], patterns: List[Dict]) -> List[str]:
        """生成优化建议"""
        suggestions = []
        
        pattern_types = {p['type'] for p in patterns}
        
        if 'full_table_scan' in pattern_types:
            suggestions.append({
                'category': 'index_optimization',
                'priority': 'high',
                'suggestion': '检测到全表扫描查询，建议检查 WHERE 条件列是否有合适索引',
                'action': '使用 EXPLAIN 分析查询执行计划，确认是否需要添加索引',
            })
        
        if 'missing_index' in pattern_types:
            suggestions.append({
                'category': 'index_optimization',
                'priority': 'high',
                'suggestion': '检测到未使用索引的查询',
                'action': '分析 WHERE/JOIN/ORDER BY 列，考虑创建覆盖索引',
            })
        
        if 'high_frequency' in pattern_types:
            suggestions.append({
                'category': 'application_optimization',
                'priority': 'medium',
                'suggestion': '高频小查询可能存在 N+1 问题',
                'action': '检查 ORM 配置，考虑使用批量查询或缓存',
            })
        
        if 'large_scan' in pattern_types:
            suggestions.append({
                'category': 'sql_optimization',
                'priority': 'high',
                'suggestion': '存在大数据量扫描查询',
                'action': '优化 SQL 逻辑，考虑添加分页或限制返回行数',
            })
        
        if 'sort_operations' in pattern_types:
            suggestions.append({
                'category': 'index_optimization',
                'priority': 'low',
                'suggestion': '涉及排序操作，可能需要优化',
                'action': '检查 ORDER BY 列是否有索引，避免filesort',
            })
        
        # 如果没有特殊模式，给出通用建议
        if not suggestions:
            suggestions.append({
                'category': 'general',
                'priority': 'info',
                'suggestion': '慢查询整体表现正常',
                'action': '继续保持监控，关注性能趋势变化',
            })
        
        return suggestions


# ==========================================
# 使用示例
# ==========================================
"""
# 在 start_monitor.py 中集成:

from monitor.slow_query_engine import SlowQueryEngine

# 在 process_result 方法中添加慢查询分析:
if current_status == 'UP':
    # 采集慢查询
    slow_engine = SlowQueryEngine(config)
    slow_queries = slow_engine.collect_slow_queries(conn, db_type)
    
    # 分析慢查询
    analysis = slow_engine.analyze_query_pattern(slow_queries)
    
    if analysis['optimization_suggestions']:
        for suggestion in analysis['optimization_suggestions']:
            if suggestion.get('priority') in ('high', 'critical'):
                am.fire(
                    alert_type='slow_query',
                    metric_key='optimization',
                    title=f"⚡ 慢查询优化建议",
                    description=suggestion['suggestion'],
                    severity='warning',
                )
"""