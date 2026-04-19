"""
慢查询引擎测试

测试 slow_query_engine.py 的功能：
- MySQL slow log 解析
- PostgreSQL pg_stat_statements 解析
- Oracle AWR 解析
"""

import pytest
from unittest.mock import Mock, patch, MagicMock
import datetime


class TestMySQLSlowQueryParser:
    """MySQL 慢查询解析器测试"""
    
    def test_parse_slow_log_entry(self):
        """测试解析单条慢查询日志"""
        from monitor.slow_query_engine import MySQLSlowQueryParser
        
        parser = MySQLSlowQueryParser()
        
        log_entry = """# Time: 2026-04-19T10:30:00.123456Z
# User@Host: app_user[app_user] @ 192.168.1.100 [192.168.1.100]
# Query_time: 5.234567  Lock_time: 0.000123  Rows_sent: 100  Rows_examined: 50000
SELECT * FROM orders WHERE status = 'pending';"""
        
        result = parser.parse(log_entry)
        
        assert result is not None
        assert 'query_time' in result
        assert result['query_time'] == 5.234567
        assert result['rows_examined'] == 50000
    
    def test_parse_query_time(self):
        """测试解析查询时间"""
        from monitor.slow_query_engine import MySQLSlowQueryParser
        
        parser = MySQLSlowQueryParser()
        
        # 模拟查询时间解析
        assert parser._parse_query_time("5.234567") == 5.234567
        assert parser._parse_query_time("0.123456") == 0.123456
    
    def test_aggregate_by_query_pattern(self):
        """测试按查询模式聚合"""
        from monitor.slow_query_engine import MySQLSlowQueryParser
        
        parser = MySQLSlowQueryParser()
        
        queries = [
            {"query": "SELECT * FROM orders WHERE id = 1", "query_time": 1.0},
            {"query": "SELECT * FROM orders WHERE id = 2", "query_time": 2.0},
            {"query": "SELECT * FROM orders WHERE id = 1", "query_time": 1.5},
        ]
        
        # 按模式聚合后，相同的 SQL 模板应该被合并
        aggregated = parser.aggregate_queries(queries)
        
        # 应该有两种模式
        assert len(aggregated) == 2


class TestPostgreSQLParser:
    """PostgreSQL 慢查询解析器测试"""
    
    def test_parse_pg_stat_statements(self):
        """测试解析 pg_stat_statements 结果"""
        from monitor.slow_query_engine import PostgreSQLSlowQueryParser
        
        parser = PostgreSQLSlowQueryParser()
        
        # 模拟 pg_stat_statements 数据
        mock_data = [
            {
                'query': 'SELECT * FROM users WHERE id = ?',
                'calls': 1000,
                'total_exec_time': 5000.0,
                'mean_exec_time': 5.0,
                'max_exec_time': 50.0,
                'rows': 100
            }
        ]
        
        result = parser.parse_statements(mock_data)
        
        assert result is not None
        assert len(result) == 1
        assert result[0]['calls'] == 1000


class TestSlowQueryEngine:
    """慢查询引擎测试"""
    
    def test_engine_initialization(self):
        """测试引擎初始化"""
        from monitor.slow_query_engine import SlowQueryEngine
        from unittest.mock import Mock
        
        mock_config = Mock()
        mock_config.db_type = 'mysql'
        mock_config.host = 'localhost'
        mock_config.port = 3306
        
        engine = SlowQueryEngine(mock_config)
        
        assert engine.config == mock_config
        assert engine.db_type == 'mysql'
    
    def test_collect_slow_queries(self):
        """测试采集慢查询"""
        from monitor.slow_query_engine import SlowQueryEngine
        from unittest.mock import Mock, patch
        
        mock_config = Mock()
        mock_config.db_type = 'mysql'
        
        engine = SlowQueryEngine(mock_config)
        
        # Mock 数据库连接
        with patch.object(engine, 'get_connection') as mock_conn:
            mock_conn.return_value = Mock()
            
            # Mock 解析器
            with patch.object(engine, '_parse_mysql_slow_log') as mock_parse:
                mock_parse.return_value = [
                    {'query': 'SELECT * FROM test', 'query_time': 1.0}
                ]
                
                result = engine.collect_slow_queries(days=7)
                
                assert isinstance(result, list)
    
    def test_analyze_queries(self):
        """测试分析慢查询"""
        from monitor.slow_query_engine import SlowQueryEngine
        from unittest.mock import Mock
        
        mock_config = Mock()
        mock_config.db_type = 'mysql'
        
        engine = SlowQueryEngine(mock_config)
        
        queries = [
            {'query': 'SELECT * FROM orders', 'query_time': 5.0, 'calls': 100},
            {'query': 'SELECT * FROM users', 'query_time': 1.0, 'calls': 50},
        ]
        
        analysis = engine.analyze_queries(queries)
        
        assert 'total_queries' in analysis
        assert 'avg_query_time' in analysis
        assert 'slow_queries' in analysis
        assert analysis['total_queries'] == 2


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
