"""
案例库 RAG - Phase 5 P2-1
=========================

历史告警处置案例库:
1. 案例入库 (AlertCase)
2. 基于症状向量化的相似度检索
3. 解决方案推荐

RAG = Retrieval-Augmented Generation
- 检索:基于关键词 + 标签的相似度
- 增强:把检索到的案例作为上下文喂给 LLM
- 生成:由 LLM 给出最终处置建议

文件: monitor/case_rag.py
参考: PHASE5_DEVELOPMENT_DESIGN.md 第四部分 P2-1
"""

from __future__ import annotations

import json
import logging
import re
from collections import Counter
from dataclasses import dataclass, asdict
from typing import Any, Dict, List, Optional, Tuple

from django.db.models import Q
from django.utils import timezone

logger = logging.getLogger(__name__)


# ============================================================================
# 数据类
# ============================================================================

@dataclass
class CaseMatch:
    """单个案例匹配结果"""
    case_id: str
    title: str
    db_type: str
    symptom_signature: str
    root_cause: str
    resolution: str
    similarity: float
    success_count: int
    confidence: float
    tags: List[str]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class RagResult:
    """RAG 检索结果"""
    query: str
    matches: List[CaseMatch]
    top_match: Optional[CaseMatch]
    confidence: float
    needs_llm: bool

    def to_dict(self) -> Dict[str, Any]:
        return {
            "query": self.query,
            "matches": [m.to_dict() for m in self.matches],
            "top_match": self.top_match.to_dict() if self.top_match else None,
            "confidence": self.confidence,
            "needs_llm": self.needs_llm,
        }


# ============================================================================
# 案例特征提取
# ============================================================================

class SymptomSignature:
    """症状签名生成器

    把告警/工单/问题描述转换为结构化签名,用于相似度匹配
    """

    # 关键词到分类的映射
    KEYWORD_TO_CATEGORY = {
        # 连接类
        "connection": "connection", "session": "connection",
        "max_connections": "connection", "too many connections": "connection",
        # SQL 类
        "slow_query": "sql", "sql": "sql", "execution_time": "sql",
        "bad sql": "sql", "full_table_scan": "sql",
        # 锁类
        "lock": "lock", "deadlock": "lock", "enq": "lock",
        "row lock": "lock", "latch": "lock",
        # IO 类
        "io": "io", "disk": "io", "tablespace": "io",
        "log switch": "io", "log file sync": "io", "db file": "io",
        # 内存类
        "memory": "memory", "pga": "memory", "sga": "memory",
        "buffer cache": "memory", "library cache": "memory",
        # 复制类
        "replication": "replication", "standby": "replication",
        "adg": "replication", "dg": "replication", "lag": "replication",
        # 集群类
        "cluster": "cluster", "rac": "cluster", "node": "cluster",
        "instance": "cluster", "vip": "cluster",
        # 表/索引
        "table": "table", "index": "index",
        "fragmentation": "table", "bloat": "table",
        "stats": "index", "stale": "index",
        # 配置
        "parameter": "config", "config": "config", "setting": "config",
    }

    # 数字特征模式
    NUMBER_PATTERNS = [
        r"(\w+)\s*[><=]+\s*(\d+(?:\.\d+)?)\s*%?",  # "cpu > 90"
        r"(\w+)\s*:\s*(\d+(?:\.\d+)?)\s*%?",         # "cpu: 90"
    ]

    @classmethod
    def extract(cls, text: str, db_type: str = "", severity: str = "") -> str:
        """生成症状签名

        签名格式: "<db_type>:<severity>:<cat1+cat2+...>:<num_token1+num_token2+...>"
        """
        text_lower = text.lower()
        # 类别
        cats = set()
        for kw, cat in cls.KEYWORD_TO_CATEGORY.items():
            if kw in text_lower:
                cats.add(cat)
        # 数字 token(归一化)
        num_tokens = []
        for pat in cls.NUMBER_PATTERNS:
            for m in re.finditer(pat, text_lower):
                try:
                    val = float(m.group(2))
                    bucket = cls._bucketize(val)
                    num_tokens.append(f"{m.group(1)}={bucket}")
                except (ValueError, IndexError):
                    pass
        signature = f"{db_type}|{severity}|{'+'.join(sorted(cats))}|{'+'.join(sorted(num_tokens))}"
        return signature

    @classmethod
    def _bucketize(cls, val: float) -> str:
        """数值分桶"""
        if val < 10:
            return "L"
        if val < 50:
            return "M"
        if val < 80:
            return "H"
        if val < 95:
            return "XH"
        return "XXH"


# ============================================================================
# 相似度计算
# ============================================================================

def jaccard_similarity(a: str, b: str) -> float:
    """Jaccard 相似度 (基于 token)"""
    if not a or not b:
        return 0.0
    a_tokens = set(a.split("|"))
    b_tokens = set(b.split("|"))
    if not a_tokens or not b_tokens:
        return 0.0
    inter = a_tokens & b_tokens
    union = a_tokens | b_tokens
    return len(inter) / len(union) if union else 0.0


def keyword_overlap(query: str, doc: str) -> float:
    """关键词重叠率"""
    q_words = set(re.findall(r"\w+", query.lower()))
    d_words = set(re.findall(r"\w+", doc.lower()))
    if not q_words:
        return 0.0
    return len(q_words & d_words) / len(q_words)


# ============================================================================
# RAG 检索器
# ============================================================================

class CaseRag:
    """案例库 RAG 检索器

    用法:
        rag = CaseRag()
        result = rag.search(symptom="连接数超过 90%", db_type="oracle")
    """

    def __init__(self, top_k: int = 5, similarity_threshold: float = 0.3):
        self.top_k = top_k
        self.similarity_threshold = similarity_threshold
        self._logger = logging.getLogger("monitor.case_rag")

    def search(self, symptom: str, db_type: str = "",
               severity: str = "", top_k: Optional[int] = None) -> RagResult:
        """检索相似案例"""
        from monitor.models import AlertCase
        top_k = top_k or self.top_k

        sig = SymptomSignature.extract(symptom, db_type, severity)
        # 数据库查询
        qs = AlertCase.objects.all()
        if db_type:
            qs = qs.filter(Q(db_type__iexact=db_type) | Q(db_type__isnull=True) | Q(db_type=""))
        candidates = qs.order_by("-success_count", "-created_at")[:200]

        # 计算相似度
        matches: List[CaseMatch] = []
        for c in candidates:
            sim_sig = jaccard_similarity(sig, c.symptom_signature or "")
            sim_kw = keyword_overlap(symptom, f"{c.title} {c.root_cause} {c.resolution}")
            sim = 0.6 * sim_sig + 0.4 * sim_kw
            if sim > self.similarity_threshold:
                matches.append(CaseMatch(
                    case_id=c.case_id,
                    title=c.title,
                    db_type=c.db_type or "",
                    symptom_signature=c.symptom_signature or "",
                    root_cause=c.root_cause or "",
                    resolution=c.resolution or "",
                    similarity=round(sim, 3),
                    success_count=c.success_count or 0,
                    confidence=(c.confidence or 0.5) * sim,
                    tags=c.tags or [],
                ))

        matches.sort(key=lambda m: -m.similarity)
        matches = matches[:top_k]

        top = matches[0] if matches else None
        confidence = top.similarity if top else 0.0
        needs_llm = not top or confidence < 0.5

        return RagResult(
            query=symptom,
            matches=matches,
            top_match=top,
            confidence=confidence,
            needs_llm=needs_llm,
        )

    def add_case(self, case_id: str, title: str, symptom: str,
                 root_cause: str, resolution: str, db_type: str = "",
                 severity: str = "warning", tags: Optional[List[str]] = None,
                 sql_used: str = "") -> Dict[str, Any]:
        """添加案例"""
        from monitor.models import AlertCase
        sig = SymptomSignature.extract(symptom, db_type, severity)
        obj, created = AlertCase.objects.update_or_create(
            case_id=case_id,
            defaults={
                "title": title,
                "db_type": db_type,
                "severity": severity,
                "symptom": symptom,
                "symptom_signature": sig,
                "root_cause": root_cause,
                "resolution": resolution,
                "sql_used": sql_used,
                "tags": tags or [],
                "confidence": 0.7,
            },
        )
        return {
            "case_id": obj.case_id,
            "created": created,
            "symptom_signature": sig,
        }

    def record_success(self, case_id: str) -> int:
        """记录案例成功使用一次,提高其排序权重"""
        from monitor.models import AlertCase
        obj = AlertCase.objects.filter(case_id=case_id).first()
        if not obj:
            return 0
        obj.success_count = (obj.success_count or 0) + 1
        # 同时提升 confidence
        obj.confidence = min(1.0, 0.5 + (obj.success_count * 0.05))
        obj.save(update_fields=["success_count", "confidence"])
        return obj.success_count

    def build_prompt_context(self, rag_result: RagResult, current_problem: str) -> str:
        """为 LLM 构造增强提示词上下文

        RAG 经典用法:把检索结果作为 prompt 的一部分
        """
        lines = ["你是一名资深 Oracle/MySQL DBA,以下是与当前问题最相似的历史案例:"]
        for i, m in enumerate(rag_result.matches[:3], 1):
            lines.append(f"\n--- 案例 {i} ({m.similarity:.0%} 相似) ---")
            lines.append(f"问题: {m.title}")
            lines.append(f"症状: {m.symptom_signature}")
            lines.append(f"根因: {m.root_cause}")
            lines.append(f"解决方案: {m.resolution}")
        lines.append(f"\n当前问题: {current_problem}")
        lines.append("\n请基于以上历史案例,给出本次问题的处置建议(分保守/标准/激进三档):")
        return "\n".join(lines)


# ============================================================================
# 自动案例沉淀
# ============================================================================

def auto_promote_to_case(alert, root_cause: str, resolution: str,
                        tags: Optional[List[str]] = None,
                        sql_used: str = "") -> Optional[str]:
        """把成功处置的告警自动沉淀为案例"""
        from monitor.models import AlertCase
        case_id = f"CASE-{alert.alert_id or alert.id}"
        sig = SymptomSignature.extract(
            f"{alert.title} {alert.metric_key}",
            db_type=alert.db_config.db_type if alert.db_config else "",
            severity=alert.severity or "warning"
        )
        obj, created = AlertCase.objects.update_or_create(
            case_id=case_id,
            defaults={
                "title": alert.title or "",
                "db_type": alert.db_config.db_type if alert.db_config else "",
                "severity": alert.severity or "warning",
                "symptom": f"{alert.metric_key} = {alert.value}",
                "symptom_signature": sig,
                "root_cause": root_cause,
                "resolution": resolution,
                "sql_used": sql_used,
                "tags": tags or [alert.metric_key or "", alert.db_type or ""],
                "confidence": 0.6,
            },
        )
        return obj.case_id if created else None


# ============================================================================
# 便捷函数
# ============================================================================

def search_cases(symptom: str, db_type: str = "", top_k: int = 5) -> RagResult:
    """便捷检索函数"""
    return CaseRag(top_k=top_k).search(symptom, db_type=db_type)


def get_case_count() -> int:
    """获取案例总数"""
    from monitor.models import AlertCase
    return AlertCase.objects.count()


def init_demo_cases():
    """初始化一些演示案例(用于演示)"""
    rag = CaseRag()
    demo_cases = [
        {
            "case_id": "CASE-DEMO-001",
            "title": "Oracle 连接数使用率超过 90%",
            "symptom": "connection usage > 90% oracle",
            "root_cause": "应用连接池未设置上限,新部署的应用突发连接",
            "resolution": "1) 调整 processes=1500; 2) 应用层配置连接池 max 200; 3) 启用连接复用",
            "db_type": "oracle",
            "severity": "critical",
            "tags": ["connection", "high_load"],
        },
        {
            "case_id": "CASE-DEMO-002",
            "title": "MySQL 慢查询突增",
            "symptom": "slow_query_burst mysql > 100/hour",
            "root_cause": "新上线的报表 SQL 未走索引,导致全表扫描",
            "resolution": "1) 找出 TOP SQL; 2) 添加合适索引; 3) 优化 SQL 写法",
            "db_type": "mysql",
            "severity": "warning",
            "tags": ["sql", "slow_query"],
        },
        {
            "case_id": "CASE-DEMO-003",
            "title": "Oracle 表空间使用率告警",
            "symptom": "tablespace usage > 90% oracle",
            "root_cause": "归档日志所在表空间满,未及时清理",
            "resolution": "1) 扩大数据文件; 2) 配置 RMAN 保留策略; 3) 定期清理",
            "db_type": "oracle",
            "severity": "critical",
            "tags": ["io", "tablespace"],
        },
        {
            "case_id": "CASE-DEMO-004",
            "title": "SCN Headroom 不足",
            "symptom": "scn_headroom_low oracle < 7 days",
            "root_cause": "DBLINK 跨版本使用,触发 SCN 跳变",
            "resolution": "1) 避免跨大版本 DBLINK; 2) 升级到 12.2+; 3) 设置 _external_scn_rejection_threshold",
            "db_type": "oracle",
            "severity": "critical",
            "tags": ["scn", "dblink"],
        },
        {
            "case_id": "CASE-DEMO-005",
            "title": "PG 锁等待过长",
            "symptom": "lock_wait pgsql > 300s",
            "root_cause": "长事务持有锁未释放,阻塞其他 DML",
            "resolution": "1) 找到阻塞源会话; 2) 评估是否 kill; 3) 优化事务粒度",
            "db_type": "pgsql",
            "severity": "warning",
            "tags": ["lock", "transaction"],
        },
    ]
    added = 0
    for c in demo_cases:
        rag.add_case(**c)
        added += 1
    return added
