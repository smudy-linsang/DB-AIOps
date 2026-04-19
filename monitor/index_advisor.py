"""
索引建议引擎 v1.0 (Phase 3 - 决策辅助)

功能:
- 基于慢查询分析结果生成索引建议
- 分析 WHERE/JOIN/ORDER BY 列
- 评估索引收益和成本
- 生成 CREATE INDEX SQL

设计文档参考: DB_AIOps_DESIGN.md 3.6 节 (Phase 3 增强)
"""

import re
from datetime import datetime
from typing import Dict, List, Optional, Any, Tuple, Set
from collections import defaultdict
from dataclasses import dataclass


# ==========================================
# 数据结构
# ==========================================

@dataclass
class IndexColumn:
    """索引列"""
    name: str
    order: str = 'ASC'  # ASC 或 DESC
    selectivity: float = 1.0  # 选择性 (0-1)
    
    def to_sql(self) -> str:
        return f"{self.name} {self.order}"


@dataclass
class IndexCandidate:
    """索引候选"""
    table: str
    columns: List[IndexColumn]
    index_type: str = 'BTREE'  # BTREE 或 HASH
    unique: bool = False
    covering: bool = False  # 是否是覆盖索引
    
    # 分析结果
    query_count: int = 0  # 可加速的查询数
    estimated_selectivity: float = 1.0
    estimated_size_mb: float = 0.0
    benefit_score: float = 0.0  # 收益评分
    risk_score: float = 0.0  # 风险评分
    
    def get_name(self) -> str:
        """生成索引名称"""
        col_names = '_'.join(c.name for c in self.columns[:3])
        return f"idx_{self.table}_{col_names}"[:30]
    
    def to_create_sql(self) -> str:
        """生成 CREATE INDEX SQL"""
        unique_str = "UNIQUE " if self.unique else ""
        columns_sql = ', '.join(c.to_sql() for c in self.columns)
        using_str = f" USING {self.index_type}" if self.index_type != 'BTREE' else ""
        return f"CREATE {unique_str}INDEX {self.get_name()}{using_str} ON {self.table} ({columns_sql});"
    
    def to_dict(self) -> Dict:
        return {
            'table': self.table,
            'columns': [c.name for c in self.columns],
            'index_type': self.index_type,
            'unique': self.unique,
            'query_count': self.query_count,
            'benefit_score': self.benefit_score,
            'risk_score': self.risk_score,
            'estimated_size_mb': self.estimated_size_mb,
            'create_sql': self.to_create_sql(),
        }


# ==========================================
# SQL 解析器
# ==========================================

class SQLParser:
    """SQL 语句解析器 - 提取表名、列名和用法"""
    
    # 提取表名的正则 (简化版)
    TABLE_PATTERN = re.compile(
        r'\bFROM\s+([\`\"\']?[\w\$]+[\`\"\']?(?:\s*,\s*[\`\"\']?[\w\$]+[\`\"\']?)*)\s*(?:WHERE|JOIN|ORDER|GROUP|LIMIT|UNION|\Z)',
        re.IGNORECASE | re.DOTALL
    )
    
    # 提取 JOIN 表名的正则
    JOIN_PATTERN = re.compile(
        r'\bJOIN\s+([\`\"\']?[\w\$]+[\`\"\']?)\s+AS?\s+([\w\$]+)',
        re.IGNORECASE
    )
    
    # 提取 WHERE 条件的正则
    WHERE_PATTERN = re.compile(
        r'\bWHERE\s+(.+?)(?:\bGROUP\b|\bORDER\b|\bHAVING\b|\bLIMIT\b|\Z)',
        re.IGNORECASE | re.DOTALL
    )
    
    # 提取 ORDER BY 列的正则
    ORDER_PATTERN = re.compile(
        r'\bORDER\s+BY\s+(.+?)(?:\bLIMIT\b|\Z)',
        re.IGNORECASE | re.DOTALL
    )
    
    # 提取 GROUP BY 列的正则
    GROUP_PATTERN = re.compile(
        r'\bGROUP\s+BY\s+(.+?)(?:\bHAVING\b|\bORDER\b|\bLIMIT\b|\Z)',
        re.IGNORECASE | re.DOTALL
    )
    
    @classmethod
    def extract_table_name(cls, sql: str) -> str:
        """提取主表名"""
        # 移除注释
        sql = re.sub(r'--.*$', '', sql, flags=re.MULTILINE)
        sql = re.sub(r'/\*.*?\*/', '', sql, flags=re.DOTALL)
        
        # 查找 FROM 子句
        from_match = re.search(r'\bFROM\s+([\`\"\']?[\w\$]+[\`\"\']?)', sql, re.IGNORECASE)
        if from_match:
            table = from_match.group(1).strip('`"\'')
            # 移除 schema 前缀
            if '.' in table:
                table = table.split('.')[-1]
            return table
        
        return ''
    
    @classmethod
    def extract_where_columns(cls, sql: str) -> List[Tuple[str, str]]:
        """提取 WHERE 条件中的列和操作符
        
        返回: [(column_name, operator), ...]
        """
        where_match = cls.WHERE_PATTERN.search(sql)
        if not where_match:
            return []
        
        where_clause = where_match.group(1)
        columns = []
        
        # 匹配列名模式（字母数字下划线组成）
        # 匹配 =, !=, <, >, <=, >=, LIKE, IN, BETWEEN 等操作符
        patterns = [
            r'(\w+)\s*=\s*\?',           # col = ?
            r'(\w+)\s*!=\s*\?',          # col != ?
            r'(\w+)\s*<>\s*\?',          # col <> ?
            r'(\w+)\s*<\s*\?',            # col < ?
            r'(\w+)\s*>\s*\?',            # col > ?
            r'(\w+)\s*<=\s*\?',           # col <= ?
            r'(\w+)\s*>=\s*\?',           # col >= ?
            r'(\w+)\s+LIKE\s+',           # col LIKE ...
            r'(\w+)\s+IN\s*\(',           # col IN (...)
            r'(\w+)\s+BETWEEN\s+',       # col BETWEEN ...
        ]
        
        for pattern in patterns:
            matches = re.finditer(pattern, where_clause, re.IGNORECASE)
            for match in matches:
                col = match.group(1)
                # 排除常见关键词
                if col.upper() not in ('AND', 'OR', 'NOT', 'NULL', 'TRUE', 'FALSE'):
                    columns.append((col, pattern.split(r'\s*')[1] if '\s*' in pattern else '='))
        
        return columns
    
    @classmethod
    def extract_join_columns(cls, sql: str) -> List[Tuple[str, str]]:
        """提取 JOIN 条件中的列
        
        返回: [(table.column, table.column), ...]
        """
        join_columns = []
        
        # 匹配 ON 条件
        on_pattern = r'\bON\s+(?:[\w\$]+\.)?(\w+)\s*=\s*(?:[\w\$]+\.)?(\w+)'
        matches = re.finditer(on_pattern, sql, re.IGNORECASE)
        for match in matches:
            join_columns.append((match.group(1), match.group(2)))
        
        return join_columns
    
    @classmethod
    def extract_order_columns(cls, sql: str) -> List[str]:
        """提取 ORDER BY 列"""
        order_match = cls.ORDER_PATTERN.search(sql)
        if not order_match:
            return []
        
        order_clause = order_match.group(1)
        columns = []
        
        # 分割并提取列名
        for part in order_clause.split(','):
            part = part.strip()
            # 移除 ASC/DESC
            part = re.sub(r'\s+(ASC|DESC)\s*$', '', part, flags=re.IGNORECASE)
            # 移除聚合函数
            part = re.sub(r'\w+\s*\(', '(', part)
            # 提取列名
            col_match = re.match(r'^([\`\"\']?[\w\$]+[\`\"\']?)', part)
            if col_match:
                col = col_match.group(1).strip('`"\'')
                if col and col.upper() not in ('NULL',):
                    columns.append(col)
        
        return columns
    
    @classmethod
    def extract_group_columns(cls, sql: str) -> List[str]:
        """提取 GROUP BY 列"""
        group_match = cls.GROUP_PATTERN.search(sql)
        if not group_match:
            return []
        
        group_clause = group_match.group(1)
        columns = []
        
        for part in group_clause.split(','):
            part = part.strip()
            # 移除聚合函数
            part = re.sub(r'\w+\s*\(', '(', part)
            col_match = re.match(r'^([\`\"\']?[\w\$]+[\`\"\']?)', part)
            if col_match:
                col = col_match.group(1).strip('`"\'')
                if col and col.upper() not in ('NULL',):
                    columns.append(col)
        
        return columns
    
    @classmethod
    def is_select_all(cls, sql: str) -> bool:
        """判断是否 SELECT *"""
        sql_clean = re.sub(r'--.*$', '', sql, flags=re.MULTILINE)
        sql_clean = re.sub(r'/\*.*?\*/', '', sql_clean, flags=re.DOTALL)
        return bool(re.search(r'\bSELECT\s+\*\b', sql_clean, re.IGNORECASE))
    
    @classmethod
    def extract_select_columns(cls, sql: str) -> List[str]:
        """提取 SELECT 列"""
        sql_clean = re.sub(r'--.*$', '', sql, flags=re.MULTILINE)
        sql_clean = re.sub(r'/\*.*?\*/', '', sql_clean, flags=re.DOTALL)
        
        select_match = re.search(r'\bSELECT\s+(.+?)\s+FROM\b', sql_clean, re.IGNORECASE | re.DOTALL)
        if not select_match:
            return []
        
        select_clause = select_match.group(1)
        if select_clause.strip() == '*':
            return ['*']
        
        columns = []
        for part in select_clause.split(','):
            part = part.strip()
            # 处理 AS 别名
            as_match = re.match(r'([\`\"\']?[\w\$]+[\`\"\']?)(?:\s+AS\s+\w+)?$', part)
            if as_match:
                col = as_match.group(1).strip('`"\'')
                # 移除表前缀
                if '.' in col:
                    col = col.split('.')[-1]
                columns.append(col)
        
        return columns


# ==========================================
# 索引建议引擎
# ==========================================

class IndexAdvisor:
    """
    索引建议引擎
    
    基于慢查询分析结果，生成索引优化建议。
    
    功能:
    - 分析查询模式
    - 识别缺失索引
    - 评估索引收益和成本
    - 生成索引创建 SQL
    """
    
    # 索引列数限制
    MAX_INDEX_COLUMNS = 5
    
    # 最小选择性阈值
    MIN_SELECTIVITY = 0.01  # 1%
    
    # 风险关键词（不适合建索引的场景）
    RISKY_PATTERNS = [
        r'NOW\(\)',
        r'CURDATE\(\)',
        r'CURRENT_TIMESTAMP',
        r'RAND\(\)',
        r'\bUUID\(\)',
    ]
    
    def __init__(self):
        self.candidates: List[IndexCandidate] = []
        self.query_count_by_table: Dict[str, int] = defaultdict(int)
    
    def analyze_queries(self, slow_queries: List[Dict]) -> List[IndexCandidate]:
        """
        分析慢查询列表，生成索引建议
        
        参数:
            slow_queries: 慢查询列表（来自 SlowQueryEngine）
        
        返回:
            索引候选列表
        """
        self.candidates = []
        self.query_count_by_table = defaultdict(int)
        
        # 按表分组统计
        table_queries: Dict[str, List[Dict]] = defaultdict(list)
        
        for query in slow_queries:
            query_text = query.get('query', '')
            if not query_text:
                continue
            
            table = SQLParser.extract_table_name(query_text)
            if table:
                table_queries[table].append(query)
                self.query_count_by_table[table] += query.get('exec_count', 1)
        
        # 为每个表生成索引建议
        for table, queries in table_queries.items():
            self._analyze_table_queries(table, queries)
        
        # 评估和排序候选索引
        self._score_candidates()
        
        return self.candidates
    
    def _analyze_table_queries(self, table: str, queries: List[Dict]):
        """分析单个表的查询，生成索引候选"""
        # 收集该表所有查询涉及的列
        where_columns: Set[str] = set()
        join_columns: Set[Tuple[str, str]] = set()
        order_columns: List[Tuple[str, int]] = []  # (column, priority)
        select_columns: Set[str] = set()
        
        for query in queries:
            query_text = query.get('query', '')
            
            # WHERE 列
            for col, op in SQLParser.extract_where_columns(query_text):
                where_columns.add(col)
            
            # JOIN 列
            for col1, col2 in SQLParser.extract_join_columns(query_text):
                join_columns.add((col1, col2))
                join_columns.add((col2, col1))
            
            # ORDER BY 列
            order_cols = SQLParser.extract_order_columns(query_text)
            for i, col in enumerate(order_cols):
                order_columns.append((col, i))  # 越靠前的列优先级越高
            
            # SELECT 列（用于覆盖索引）
            select_cols = SQLParser.extract_select_columns(query_text)
            select_columns.update(select_cols)
        
        # 1. 生成 WHERE 条件索引
        if where_columns:
            self._create_candidate(
                table=table,
                columns=list(where_columns)[:self.MAX_INDEX_COLUMNS],
                reason='WHERE条件索引',
                query_count=len(queries)
            )
        
        # 2. 生成 JOIN 列索引
        if join_columns:
            for col1, col2 in join_columns:
                self._create_candidate(
                    table=table,
                    columns=[col1, col2][:self.MAX_INDEX_COLUMNS],
                    reason='JOIN列索引',
                    query_count=len(queries)
                )
        
        # 3. 生成 ORDER BY 索引
        if order_columns:
            # 按优先级排序
            sorted_order = sorted(set(col for col, _ in order_columns))
            self._create_candidate(
                table=table,
                columns=sorted_order[:self.MAX_INDEX_COLUMNS],
                reason='ORDER BY索引',
                query_count=len(queries)
            )
        
        # 4. 生成复合索引（WHERE + ORDER BY）
        combined = list(where_columns) + [col for col, _ in order_columns]
        if len(combined) <= self.MAX_INDEX_COLUMNS:
            self._create_candidate(
                table=table,
                columns=list(dict.fromkeys(combined)),  # 去重保持顺序
                reason='WHERE+ORDER复合索引',
                query_count=len(queries)
            )
        
        # 5. 覆盖索引（包含 SELECT 列）
        if select_columns and where_columns:
            covering_cols = list(where_columns) + list(select_columns)
            if len(covering_cols) <= self.MAX_INDEX_COLUMNS + 2:  # 允许稍多列
                candidate = self._create_candidate(
                    table=table,
                    columns=list(dict.fromkeys(covering_cols))[:self.MAX_INDEX_COLUMNS + 2],
                    reason='覆盖索引',
                    query_count=len(queries)
                )
                if candidate:
                    candidate.covering = True
    
    def _create_candidate(self, table: str, columns: List[str],
                         reason: str, query_count: int) -> Optional[IndexCandidate]:
        """创建索引候选（如果不存在重复）"""
        if not columns:
            return None
        
        # 检查是否已存在类似的候选
        for existing in self.candidates:
            if existing.table == table:
                existing_cols = set(c.name for c in existing.columns)
                new_cols = set(columns)
                # 如果列集合相同或过于相似，跳过
                if existing_cols == new_cols:
                    return None
                # 如果已有候选包含所有新列，跳过
                if existing_cols >= new_cols:
                    return None
        
        # 过滤掉包含风险关键词的列
        filtered_columns = []
        for col in columns:
            if not any(re.search(pattern, col, re.IGNORECASE) for pattern in self.RISKY_PATTERNS):
                filtered_columns.append(col)
        
        if not filtered_columns:
            return None
        
        index_columns = [IndexColumn(name=col) for col in filtered_columns[:self.MAX_INDEX_COLUMNS]]
        
        candidate = IndexCandidate(
            table=table,
            columns=index_columns,
            query_count=query_count,
        )
        
        self.candidates.append(candidate)
        return candidate
    
    def _score_candidates(self):
        """评估候选索引的收益和风险"""
        for candidate in self.candidates:
            # 收益评分
            candidate.benefit_score = self._calculate_benefit(candidate)
            
            # 风险评分
            candidate.risk_score = self._calculate_risk(candidate)
    
    def _calculate_benefit(self, candidate: IndexCandidate) -> float:
        """计算索引收益评分"""
        score = 0.0
        
        # 1. 查询频率因子（可加速的查询数越多，收益越高）
        score += min(candidate.query_count / 100, 10) * 2  # 最多20分
        
        # 2. 覆盖索引额外加分
        if candidate.covering:
            score += 5
        
        # 3. 列数因子（适当的列数更好）
        col_count = len(candidate.columns)
        if 2 <= col_count <= 3:
            score += 3
        elif col_count == 1:
            score += 1
        
        return round(score, 2)
    
    def _calculate_risk(self, candidate: IndexCandidate) -> float:
        """计算索引风险评分"""
        score = 0.0
        
        # 1. 列数过多风险
        if len(candidate.columns) > 3:
            score += (len(candidate.columns) - 3) * 2
        
        # 2. 唯一索引风险低（因为查询快）
        if candidate.unique:
            score -= 3
        
        # 3. 覆盖索引有写入惩罚风险
        if candidate.covering:
            score += 2
        
        # 4. 热门表风险高（写入频繁）
        table_query_count = self.query_count_by_table.get(candidate.table, 0)
        if table_query_count > 1000:
            score += 3
        elif table_query_count > 100:
            score += 1
        
        return max(0, round(score, 2))
    
    def get_recommendations(self, top_n: int = 10) -> List[Dict]:
        """
        获取索引建议列表
        
        参数:
            top_n: 返回前 N 条建议
        
        返回:
            建议列表，按收益/风险比排序
        """
        # 按收益/风险比排序
        scored = []
        for candidate in self.candidates:
            ratio = candidate.benefit_score / (candidate.risk_score + 1)
            scored.append((ratio, candidate))
        
        scored.sort(reverse=True)
        
        recommendations = []
        for ratio, candidate in scored[:top_n]:
            rec = candidate.to_dict()
            rec['benefit_risk_ratio'] = round(ratio, 2)
            rec['recommendation'] = self._generate_recommendation_text(candidate, ratio)
            recommendations.append(rec)
        
        return recommendations
    
    def _generate_recommendation_text(self, candidate: IndexCandidate, ratio: float) -> str:
        """生成建议文本"""
        if ratio > 5:
            action = "强烈建议创建"
        elif ratio > 2:
            action = "建议创建"
        else:
            action = "可考虑创建"
        
        table = candidate.table
        cols = ', '.join(c.name for c in candidate.columns)
        reason = "覆盖索引" if candidate.covering else "加速查询"
        
        return f"{action} {reason}：{table}({cols})"


# ==========================================
# 使用示例
# ==========================================
"""
# 与 SlowQueryEngine 配合使用:

from monitor.slow_query_engine import SlowQueryEngine
from monitor.index_advisor import IndexAdvisor

# 1. 采集慢查询
slow_engine = SlowQueryEngine(config)
slow_queries = slow_engine.collect_slow_queries(conn, db_type)

# 2. 分析查询模式
analysis = slow_engine.analyze_query_pattern(slow_queries)

# 3. 生成索引建议
advisor = IndexAdvisor()
candidates = advisor.analyze_queries(slow_queries)
recommendations = advisor.get_recommendations(top_n=5)

for rec in recommendations:
    print(f"表: {rec['table']}")
    print(f"列: {rec['columns']}")
    print(f"SQL: {rec['create_sql']}")
    print(f"理由: {rec['recommendation']}")
    print()
"""