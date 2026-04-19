"""
索引建议引擎测试

测试 index_advisor.py 的功能：
- SQL 解析
- 候选索引生成
- 收益/风险评分
"""

import pytest
from unittest.mock import Mock, patch


class TestSQLParser:
    """SQL 解析器测试"""
    
    def test_parse_simple_select(self):
        """测试解析简单 SELECT 语句"""
        from monitor.index_advisor import SQLParser
        
        parser = SQLParser()
        
        sql = "SELECT * FROM users WHERE id = 1"
        result = parser.parse(sql)
        
        assert result is not None
        assert 'table' in result
        assert result['table'] == 'users'
        assert 'where_clause' in result
    
    def test_parse_select_with_join(self):
        """测试解析带 JOIN 的 SELECT 语句"""
        from monitor.index_advisor import SQLParser
        
        parser = SQLParser()
        
        sql = "SELECT u.name, o.total FROM users u JOIN orders o ON u.id = o.user_id WHERE u.id = 1"
        result = parser.parse(sql)
        
        assert result is not None
        assert 'tables' in result
        assert 'users' in result['tables']
        assert 'orders' in result['tables']
    
    def test_extract_where_columns(self):
        """测试提取 WHERE 条件列"""
        from monitor.index_advisor import SQLParser
        
        parser = SQLParser()
        
        sql = "SELECT * FROM orders WHERE customer_id = 1 AND status = 'pending'"
        result = parser.extract_where_columns(sql)
        
        assert 'customer_id' in result
        assert 'status' in result


class TestIndexAdvisor:
    """索引建议引擎测试"""
    
    def test_advisor_initialization(self):
        """测试引擎初始化"""
        from monitor.index_advisor import IndexAdvisor
        
        advisor = IndexAdvisor()
        
        assert advisor is not None
    
    def test_analyze_single_query(self):
        """测试分析单条 SQL"""
        from monitor.index_advisor import IndexAdvisor
        
        advisor = IndexAdvisor()
        
        sql = "SELECT * FROM orders WHERE customer_id = 1"
        result = advisor.analyze_query(sql)
        
        assert result is not None
        assert 'candidates' in result
    
    def test_generate_candidates(self):
        """测试生成候选索引"""
        from monitor.index_advisor import IndexAdvisor
        
        advisor = IndexAdvisor()
        
        parsed = {
            'table': 'orders',
            'where_columns': ['customer_id', 'status'],
            'select_columns': ['*']
        }
        
        candidates = advisor.generate_candidates(parsed)
        
        assert len(candidates) > 0
        assert all('index_ddl' in c for c in candidates)
    
    def test_calculate_selectivity(self):
        """测试计算选择性"""
        from monitor.index_advisor import IndexAdvisor
        
        advisor = IndexAdvisor()
        
        # 模拟列基数
        selectivity = advisor.calculate_selectivity('orders', 'customer_id', 100000, 1000)
        
        assert 0 <= selectivity <= 1
        assert selectivity == 0.01  # 1000/100000 = 0.01
    
    def test_score_candidate(self):
        """测试候选索引评分"""
        from monitor.index_advisor import IndexAdvisor, IndexCandidate
        
        advisor = IndexAdvisor()
        
        candidate = IndexCandidate(
            table='orders',
            columns=['customer_id'],
            index_ddl='CREATE INDEX idx_orders_customer ON orders(customer_id)',
            benefit_score=0.8,
            risk_score=0.2
        )
        
        score = advisor.score_candidate(candidate)
        
        assert score > 0
        assert isinstance(score, float)
    
    def test_get_recommendations(self):
        """测试获取建议"""
        from monitor.index_advisor import IndexAdvisor
        
        advisor = IndexAdvisor()
        
        sqls = [
            "SELECT * FROM orders WHERE customer_id = 1",
            "SELECT * FROM orders WHERE status = 'pending'"
        ]
        
        candidates = advisor.analyze_queries(sqls)
        recommendations = advisor.get_recommendations(candidates)
        
        assert isinstance(recommendations, list)


class TestIndexCandidate:
    """索引候选测试"""
    
    def test_candidate_creation(self):
        """测试创建索引候选"""
        from monitor.index_advisor import IndexCandidate
        
        candidate = IndexCandidate(
            table='orders',
            columns=['customer_id', 'status'],
            index_ddl='CREATE INDEX idx_orders_cust_status ON orders(customer_id, status)'
        )
        
        assert candidate.table == 'orders'
        assert candidate.columns == ['customer_id', 'status']
        assert 'customer_id' in candidate.index_ddl
        assert 'status' in candidate.index_ddl


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
