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
import logging
import re

logger = logging.getLogger(__name__)
from datetime import datetime, timedelta
from django.utils import timezone
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

    _WINDOWS = {'30m': 1800, '1h': 3600, '6h': 21600, '24h': 86400, '7d': 604800}
    _MYSQL_FAMILY = ('mysql', 'tdsql', 'gbase')

    def collect_slow_queries_from_db(self, time_range: str = '1h', limit: int = 100) -> List[Dict]:
        """慢 SQL 列表（API / 告警 / AI 诊断共用入口）。见 TDSQL_SLOWSQL_DESIGN.md §4。

        主路径：sql_stat 时间窗聚合（采集侧已按 set 差分，这里跨 set 求和），``basis='window'``。
        回退：时序库不可用或窗口内无数据 → 直连取 digest **累计值**，``basis='cumulative'``，
        并在 ``self.last_meta`` 说明它不是所选时间窗的数据。

        T-01：原实现在 DictCursor 下按下标取列必抛 KeyError，异常被吞后回落去读一个
        不存在的 ``MonitorLog.detail`` 字段，于是对 MySQL 系**恒返回空**。
        T-02：原实现把皮秒 ÷1e9（毫秒）当秒，并按 total×count 排序把最快的 SQL 排第一。
        """
        from monitor import appconf
        self.last_meta = {'basis': None, 'warning': None, 'time_range': time_range}
        sec = self._WINDOWS.get(time_range, 3600)
        td = timezone.now()
        fd = td - timedelta(seconds=sec)
        slow_ms = appconf.get('SLOW_SQL_AVG_MS')
        rows = self._from_sqlstat(fd, td, limit, slow_ms)
        if rows:
            return rows
        return self._live_cumulative(limit, sec, slow_ms)

    def _from_sqlstat(self, fd, td, limit, slow_ms):
        from monitor import degrade
        from monitor.sqlstat_query import (basis_warning, texts_for, window_by_digest,
                                           window_by_set)
        from monitor.timeseries import get_timeseries_storage
        cfg = self.config
        try:
            with get_timeseries_storage().cursor() as cur:
                if cur is None:
                    return None
                aggs = window_by_digest(cur, cfg.id, fd, td, limit=limit)
                if not aggs:
                    return []
                digests = list(aggs)
                texts = texts_for(cur, cfg.id, digests, fd - timedelta(days=7))
                by_set = (window_by_set(cur, cfg.id, fd, td, digests)
                          if cfg.db_type == 'tdsql' else {})
        except Exception as e:
            degrade.note('slow_query.sqlstat', 'sql_stat 窗口聚合失败，回退直连', e)
            return None
        set_names = {s for d in by_set.values() for s in d}
        self.last_meta.update(basis='window', warning=basis_warning(cfg, set_names))
        out = []
        for digest, agg in aggs.items():
            text, example = texts.get(digest, (None, None))
            out.append(self._row(digest, agg, text, example, by_set.get(digest), 'window', slow_ms))
        return out

    def _live_cumulative(self, limit, window_sec, slow_ms):
        """时序库不可用时的回退：直连取累计值。MySQL 系按 set 取（TDSQL 透传）后跨 set 求和。"""
        from monitor import appconf, degrade
        from .db_connector import DbConnector
        cfg = self.config
        self.last_meta.update(
            basis='cumulative',
            warning='时序库无该时间窗数据，以下为自计数器清零（实例重启或 TRUNCATE）以来的累计值，'
                    '不代表所选时间窗')
        conn = None
        try:
            conn = DbConnector.get_connection(
                cfg, statement_timeout_ms=appconf.get('TARGET_DB_STATEMENT_TIMEOUT_MS'),
                readonly=True)
            if cfg.db_type in self._MYSQL_FAMILY:
                return self._mysql_family_cumulative(conn, limit, window_sec, slow_ms)
            return self.collect_slow_queries(conn)[:limit]
        except Exception as e:
            degrade.note('slow_query.live', f'{cfg.db_type} 直连取慢 SQL 失败', e)
            self.last_meta['warning'] = f'时序库无数据，且直连目标库失败：{type(e).__name__}'
            return []
        finally:
            if conn:
                DbConnector.close_connection(conn)

    def _mysql_family_cumulative(self, conn, limit, window_sec, slow_ms):
        from monitor import sqlstat_collect
        from monitor.sqlstat_query import latest_collect_data
        cfg = self.config
        data = latest_collect_data(cfg) if cfg.db_type == 'tdsql' else {}
        sets = [s.get('set_name') for s in (data.get('tdsql_sets') or []) if isinstance(s, dict)]
        cur = conn.cursor()
        try:
            raw, meta = sqlstat_collect.fetch_digest_rows(
                cur, cfg, tdsql_sets=sets, tdsql_mode=data.get('tdsql_mode'),
                window_sec=max(int(window_sec), 60), limit=max(int(limit), 1) * 2)
        finally:
            cur.close()
        if meta.get('warning'):
            self.last_meta['warning'] = meta['warning']
        merged, by_set, texts = {}, {}, {}
        for r in raw:
            c = r['counters']
            agg = {'exec': c['exec'], 'elapsed_ms': c['elapsed_ps'] / 1e9,
                   'lock_ms': c['lock_ps'] / 1e9, 'cpu_ms': c['cpu_ps'] / 1e9,
                   'rows_sent': c['rows_sent'], 'rows_examined': c['rows_examined'],
                   'tmp_tables': c['tmp_tables'], 'tmp_disk': c['tmp_disk'],
                   'sort_merge': c['sort_merge'], 'sort_rows': c['sort_rows'],
                   'scan': c['scan'], 'full_join': c['full_join'],
                   'no_index': c['no_index'], 'errors': c['errors']}
            d = r['digest'][:32]
            m = merged.setdefault(d, {k: 0 for k in agg})
            for k, v in agg.items():
                m[k] += v
            m['db_name'] = r.get('schema_name')
            by_set.setdefault(d, {})[r['set_name']] = agg
            texts[d] = (r.get('digest_text'), r.get('sample_text'))
        ordered = sorted(merged.items(), key=lambda kv: kv[1]['elapsed_ms'], reverse=True)[:limit]
        return [self._row(d, agg, *texts.get(d, (None, None)),
                          by_set.get(d) if cfg.db_type == 'tdsql' else None,
                          'cumulative', slow_ms)
                for d, agg in ordered]

    def _row(self, digest, agg, text, example, by_set, basis, slow_ms):
        """统一输出行。*_sec 字段**真的是秒**（前端契约），另给 avg_ms 便于展示。"""
        from monitor.sql_profile import profile, set_distribution
        family = self.config.db_type in self._MYSQL_FAMILY
        p = profile(agg, slow_avg_ms=slow_ms, mysql_family=family)
        exec_n = agg.get('exec', 0)
        dist = set_distribution(by_set) if by_set else None
        return {
            'db_type': self.config.db_type,
            'digest': digest,
            'query': text or example or '',
            'sql_example': example,
            'db_name': agg.get('db_name'),
            'exec_count': exec_n,
            'total_time_sec': round(agg.get('elapsed_ms', 0) / 1000.0, 6),
            'avg_time_sec': round(agg.get('elapsed_ms', 0) / 1000.0 / exec_n, 6) if exec_n else 0,
            'avg_ms': p.get('avg_ms'),
            # 增量统计拿不到单次最大 / 最小值；不估算（原 Oracle 路径用 avg×2 冒充 max）
            'max_time_sec': None,
            'min_time_sec': None,
            'lock_time_sec': round(agg.get('lock_ms', 0) / 1000.0, 6) if family else None,
            'rows_examined': agg.get('rows_examined', 0) if family else None,
            'rows_sent': agg.get('rows_sent', 0),
            'sort_rows': agg.get('sort_rows', 0) if family else None,
            'no_index_used': agg.get('no_index', 0) if family else None,
            'tmp_disk_tables': agg.get('tmp_disk', 0) if family else None,
            'sort_merge_passes': agg.get('sort_merge', 0) if family else None,
            'full_scan_count': agg.get('scan', 0) if family else None,
            'is_slow': p.get('is_slow', False),
            'findings': p.get('findings', []),
            'set_count': dist['set_count'] if dist else None,
            'hottest_set': dist['hottest_set'] if dist else None,
            'set_skew': dist['skew'] if dist else None,
            'basis': basis,
        }

    def _fallback_collect_from_logs(self, time_range: str, limit: int) -> List[Dict]:
        """已废弃：原实现读不存在的 MonitorLog.detail 字段，恒返回空（T-01）。保留签名仅为兼容。"""
        return []

    def _collect_mysql_slow_queries(self, conn) -> List[Dict]:
        """MySQL 系直连取 digest 累计值（单位与排序已修正，见 T-01 / T-02）。"""
        from monitor import appconf
        if not hasattr(self, 'last_meta'):
            self.last_meta = {'basis': 'cumulative', 'warning': None}
        return self._mysql_family_cumulative(conn, 100, 86400 * 365,
                                             appconf.get('SLOW_SQL_AVG_MS'))

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
                'max_time_sec': None,  # AWR 不提供单次最大值；不估算
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
        
        total_time = sum(q.get('total_time_sec', 0) or 0 for q in slow_queries)
        total_executions = sum(q.get('exec_count', 1) for q in slow_queries)

        if any('findings' in q for q in slow_queries):
            # 有实测画像（MySQL 系，见 sql_profile）：按结论代码归并，不再做关键字猜测
            patterns = self._patterns_from_findings(slow_queries)
            suggestions = self._suggestions_from_findings(patterns)
        else:
            patterns = self._identify_patterns(slow_queries)
            suggestions = self._generate_suggestions(slow_queries, patterns)

        # T-02：按总耗时排序（原实现 total×count 把执行次数乘了两遍）
        top_queries = sorted(
            slow_queries,
            key=lambda x: x.get('total_time_sec', 0) or 0,
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
    
    _FINDING_ADVICE = {
        'SCAN_INEFFICIENT': ('index_optimization', 'high', '扫描行远多于返回行',
                             '在 SQL 详情页查看具体索引建议；核对 WHERE / JOIN 列是否有可用索引'),
        'FULL_SCAN': ('index_optimization', 'high', '存在全表扫描',
                      '在 SQL 详情页查看具体索引建议，必要时用 EXPLAIN ANALYZE 确认扫描算子'),
        'FULL_JOIN': ('index_optimization', 'high', '存在无索引 JOIN',
                      '为被驱动表的关联列建立索引'),
        'TMP_DISK': ('sql_optimization', 'medium', '内部临时表落盘',
                     '为 GROUP BY / ORDER BY 列建立索引避免临时表，或评估 tmp_table_size / max_heap_table_size'),
        'SORT_MERGE': ('sql_optimization', 'medium', '排序发生多轮归并',
                       '为 ORDER BY 列建立索引，或减少排序行数；评估 sort_buffer_size'),
        'LOCK_WAIT': ('concurrency', 'high', '锁等待占比高',
                      '在阻塞分析中定位持锁会话；缩短事务、避免热点行更新'),
        'ERRORS': ('application_optimization', 'medium', '执行报错',
                   '检查应用日志中的对应错误'),
    }

    def _patterns_from_findings(self, slow_queries: List[Dict]) -> List[Dict]:
        grouped = defaultdict(list)
        for q in slow_queries:
            for f in q.get('findings') or []:
                grouped[f['code']].append({'query': (q.get('query') or '')[:200],
                                           'digest': q.get('digest'),
                                           'exec_count': q.get('exec_count'),
                                           'detail': f.get('text')})
        out = []
        for code, examples in grouped.items():
            cat, sev, title, _ = self._FINDING_ADVICE.get(code, ('general', 'info', code, ''))
            out.append({'type': code.lower(), 'count': len(examples), 'description': title,
                        'severity': {'high': 'critical', 'medium': 'warning'}.get(sev, 'info'),
                        'examples': examples[:3]})
        out.sort(key=lambda p: (p['severity'] != 'critical', -p['count']))
        return out

    def _suggestions_from_findings(self, patterns: List[Dict]) -> List[Dict]:
        out = []
        for p in patterns:
            cat, sev, title, action = self._FINDING_ADVICE.get(
                p['type'].upper(), ('general', 'info', p['description'], ''))
            out.append({'category': cat, 'priority': sev,
                        'suggestion': f"{title}（{p['count']} 条 SQL）", 'action': action})
        if not out:
            out.append({'category': 'general', 'priority': 'info',
                        'suggestion': '窗口内 SQL 未发现扫描、排序、临时表、锁等待方面的异常',
                        'action': '继续保持监控，关注环比退化'})
        return out

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