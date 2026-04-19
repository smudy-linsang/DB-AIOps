"""
配置检查引擎测试

测试 config_advisor.py 的功能：
- 32 条配置检查规则执行
- MySQL/PostgreSQL/Oracle/DM 配置检查
"""

import pytest
from unittest.mock import Mock, patch


class TestConfigAdvisor:
    """配置检查引擎测试"""
    
    def test_advisor_initialization(self):
        """测试引擎初始化"""
        from monitor.config_advisor import ConfigAdvisor
        
        advisor = ConfigAdvisor()
        
        assert advisor is not None
        assert len(advisor.rules) > 0
    
    def test_get_all_rules(self):
        """测试获取所有规则"""
        from monitor.config_advisor import ConfigAdvisor
        
        advisor = ConfigAdvisor()
        rules = advisor.get_all_rules()
        
        assert isinstance(rules, list)
        assert len(rules) >= 32  # 应该有 32 条规则
    
    def test_get_rules_by_db_type(self):
        """测试按数据库类型获取规则"""
        from monitor.config_advisor import ConfigAdvisor
        
        advisor = ConfigAdvisor()
        
        mysql_rules = advisor.get_rules_by_db_type('mysql')
        assert all(r['db_type'] == 'mysql' for r in mysql_rules)
        
        pgsql_rules = advisor.get_rules_by_db_type('pgsql')
        assert all(r['db_type'] == 'pgsql' for r in pgsql_rules)
    
    def test_check_single_rule(self):
        """测试执行单条规则"""
        from monitor.config_advisor import ConfigAdvisor
        
        advisor = ConfigAdvisor()
        
        rule = advisor.get_all_rules()[0]
        
        # Mock 配置值
        mock_config = {
            'max_connections': 100,
            'innodb_buffer_pool_size': 134217728
        }
        
        result = advisor.check_rule(rule, mock_config)
        
        assert 'rule_id' in result
        assert 'rule_name' in result
        assert 'passed' in result
    
    def test_check_all_rules(self):
        """测试执行所有规则"""
        from monitor.config_advisor import ConfigAdvisor
        
        advisor = ConfigAdvisor()
        
        mock_config_id = 1
        results = advisor.check_all(mock_config_id)
        
        assert isinstance(results, list)
        assert len(results) > 0
    
    def test_check_by_db_type(self):
        """测试按数据库类型执行检查"""
        from monitor.config_advisor import ConfigAdvisor
        
        advisor = ConfigAdvisor()
        
        results = advisor.check_by_db_type('mysql')
        
        assert isinstance(results, list)
        assert all(r['db_type'] == 'mysql' for r in results)


class TestMySQLRules:
    """MySQL 配置规则测试"""
    
    def test_max_connections_rule(self):
        """测试 max_connections 规则"""
        from monitor.config_advisor import MYSQL_RULES
        
        rule = next((r for r in MYSQL_RULES if r['rule_id'] == 'MYSQL001'), None)
        
        assert rule is not None
        assert rule['rule_name'] == 'max_connections'
        assert rule['severity'] == 'warning'
    
    def test_innodb_buffer_pool_rule(self):
        """测试 innodb_buffer_pool_size 规则"""
        from monitor.config_advisor import MYSQL_RULES
        
        rule = next((r for r in MYSQL_RULES if r['rule_id'] == 'MYSQL002'), None)
        
        assert rule is not None


class TestPostgreSQLRules:
    """PostgreSQL 配置规则测试"""
    
    def test_shared_buffers_rule(self):
        """测试 shared_buffers 规则"""
        from monitor.config_advisor import PGSQL_RULES
        
        rule = next((r for r in PGSQL_RULES if r['rule_id'] == 'PGSQL001'), None)
        
        assert rule is not None


class TestRuleResult:
    """规则结果测试"""
    
    def test_result_structure(self):
        """测试结果结构"""
        from monitor.config_advisor import ConfigAdvisor
        
        advisor = ConfigAdvisor()
        
        rule = advisor.get_all_rules()[0]
        mock_config = {'some_param': 100}
        
        result = advisor.check_rule(rule, mock_config)
        
        assert 'rule_id' in result
        assert 'rule_name' in result
        assert 'passed' in result
        assert 'current_value' in result
        assert 'recommendation' in result


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
