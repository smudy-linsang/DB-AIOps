"""
巡检知识库 - Phase 5 P1-5
=========================

对历史巡检结果进行模式识别:
1. 重复出现的问题 → 自动关联知识条目
2. 问题模式聚类 → 发现潜在共性
3. 趋势分析 → 周期性问题识别
4. 解决方案推荐 → 匹配 InspectionIssuePattern

文件: monitor/inspection_knowledge_base.py
参考: PHASE5_DEVELOPMENT_DESIGN.md 第三部分 P1-5
"""

from __future__ import annotations

import json
import logging
from collections import Counter, defaultdict
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from django.db.models import Count, Q
from django.utils import timezone

logger = logging.getLogger(__name__)


# ============================================================================
# 知识条目模板
# ============================================================================

KNOWLEDGE_BASE: Dict[str, Dict[str, Any]] = {
    # 长事务
    "I003": {
        "category": "transaction",
        "root_causes": [
            "应用层事务粒度过大,未及时提交",
            "批量 DML 未拆批",
            "锁等待链过长"
        ],
        "best_practices": [
            "将大事务拆分为小批次 (e.g. 1000 行/批)",
            "使用 NOWAIT 标识应用层超时",
            "监控 v$transaction 视图"
        ],
        "sql_snippets": [
            "SELECT sid, serial#, status, last_call_et FROM v$session WHERE status='ACTIVE' ORDER BY last_call_et DESC",
            "SELECT * FROM v$transaction ORDER BY start_date"
        ],
        "references": [
            "Oracle Doc: Managing Long Running Transactions",
            "MOS Note: 741299.1 - Long Running Transactions"
        ]
    },
    # 连接数
    "I004": {
        "category": "connection",
        "root_causes": [
            "应用连接池未设置上限",
            "连接泄漏 (未 close)",
            "短连接过多"
        ],
        "best_practices": [
            "配置应用层连接池(最大 100~200)",
            "启用连接超时回收",
            "使用中间件代理(Mycat/PGBouncer)"
        ],
        "sql_snippets": [
            "ALTER SYSTEM SET processes=1000 SCOPE=SPFILE",
            "ALTER SYSTEM SET sessions=1520 SCOPE=SPFILE"
        ],
        "references": [
            "Oracle Doc: Configuring Session Multiplexing"
        ]
    },
    # 无效对象
    "I101": {
        "category": "object_health",
        "root_causes": [
            "存储过程/包编译未通过",
            "依赖对象失效",
            "授权变更后未重新编译"
        ],
        "best_practices": [
            "定期执行 @?/rdbms/admin/utlrp.sql 重新编译",
            "建立失效对象监控告警"
        ],
        "sql_snippets": [
            "SELECT owner, object_name, object_type, status FROM dba_objects WHERE status='INVALID'",
            "EXEC DBMS_UTILITY.compile_schema(schema => 'YOUR_SCHEMA');"
        ],
        "references": [
            "Oracle Doc: Compiling Invalid Objects"
        ]
    },
    # 回收站
    "I102": {
        "category": "storage",
        "root_causes": [
            "大量 DROP 表操作",
            "未设置 RECYCLEBIN=OFF"
        ],
        "best_practices": [
            "定期清理回收站",
            "生产环境考虑 PURGE 替代 DROP"
        ],
        "sql_snippets": [
            "PURGE DBA_RECYCLEBIN;",
            "ALTER SESSION SET RECYCLEBIN=OFF;"
        ],
        "references": []
    },
    # SCN Headroom
    "I103": {
        "category": "critical",
        "root_causes": [
            "DBLINK 跨版本使用",
            "频繁 COMMIT 触发 SCN 跳变",
            "Bug 11891428 (修复后仍可能)"
        ],
        "best_practices": [
            "避免 DBLINK 跨大版本",
            "检查 _external_scn_rejection_threshold",
            "升级 12.2+ 已解决"
        ],
        "sql_snippets": [
            "SELECT current_scn, scn_to_timestamp(current_scn) FROM v$database"
        ],
        "references": [
            "MOS Note: 2335265.1 - SCN Headroom Alert"
        ]
    },
    # 自动任务
    "I104": {
        "category": "auto_task",
        "root_causes": [
            "窗口被禁用",
            "维护窗口资源不足",
            "Auto Task Client 配置错误"
        ],
        "best_practices": [
            "检查 DBA_AUTOTASK_CLIENT 状态",
            "调整维护窗口时长"
        ],
        "sql_snippets": [
            "BEGIN DBMS_AUTO_TASK_ADMIN.ENABLE(client_name => 'sql tuning advisor'); END;",
            "SELECT client_name, status FROM dba_autotask_client"
        ],
        "references": []
    },
    # 日志切换频繁
    "I105": {
        "category": "log",
        "root_causes": [
            "REDO 日志组数少",
            "DML 过于频繁",
            "LGWR 性能瓶颈"
        ],
        "best_practices": [
            "增加 LOG_FILE 数量(8+ 组)",
            "将日志放在高速磁盘上",
            "使用 NOARCHIVELOG 模式时减少切换"
        ],
        "sql_snippets": [
            "SELECT group#, bytes, status FROM v$log",
            "ALTER DATABASE ADD LOGFILE GROUP 9 SIZE 1G;"
        ],
        "references": []
    },
    # 稀疏表
    "I106": {
        "category": "table_health",
        "root_causes": [
            "大量删除后未重组",
            "高水位线(HWM)未回收"
        ],
        "best_practices": [
            "定期执行表/索引重组",
            "使用 SHRINK SPACE 回收空间",
            "启用行移动 ALTER TABLE ... ENABLE ROW MOVEMENT"
        ],
        "sql_snippets": [
            "ALTER TABLE t SHRINK SPACE CASCADE;",
            "ALTER TABLE t ENABLE ROW MOVEMENT;"
        ],
        "references": []
    },
    # 外键无索引
    "I108": {
        "category": "index",
        "root_causes": [
            "建外键时未同时建索引",
            "外键列上无查询需求"
        ],
        "best_practices": [
            "外键列必须有索引",
            "避免子表 INSERT/UPDATE/DELETE 时全表锁"
        ],
        "sql_snippets": [
            "CREATE INDEX idx_fk ON child_table(fk_column);"
        ],
        "references": [
            "MOS Note: 223303.1 - Foreign Key Lock Issues"
        ]
    },
    # AWR 配置
    "I111": {
        "category": "awr",
        "root_causes": [
            "AWR 保留时间过短",
            "快照间隔不合理"
        ],
        "best_practices": [
            "保留 7~30 天",
            "间隔 15~60 分钟",
            "AWR 仓库空间预留 5%~10%"
        ],
        "sql_snippets": [
            "BEGIN DBMS_WORKLOAD_REPOSITORY.MODIFY_SNAPSHOT_SETTINGS(interval=>60, retention=>30*1440); END;"
        ],
        "references": []
    },
}


# ============================================================================
# 模式识别器
# ============================================================================

class PatternRecognizer:
    """模式识别器

    通过对历史 Finding 数据进行聚类,发现共性问题模式
    """

    def __init__(self, db_config=None, lookback_days: int = 30):
        """
        参数:
            db_config: DatabaseConfig,None 表示所有数据库
            lookback_days: 回顾天数
        """
        self.db_config = db_config
        self.lookback_days = lookback_days
        self._logger = logging.getLogger("monitor.pattern_recognizer")

    def recognize(self) -> List[Dict[str, Any]]:
        """识别模式并返回列表"""
        from monitor.models import InspectionFinding, InspectionRun, InspectionIssuePattern

        since = timezone.now() - timedelta(days=self.lookback_days)
        qs = InspectionFinding.objects.filter(
            run__started_at__gte=since,
            status__in=["critical", "warning"]
        )
        if self.db_config:
            qs = qs.filter(run__db_config=self.db_config)

        findings = qs.values("item_code", "item_title", "status",
                             "run__db_config_id", "run__db_config__name",
                             "run__db_config__db_type")

        # 按 (item_code, status) 聚类
        cluster: Dict[Tuple[str, str], List[Dict[str, Any]]] = defaultdict(list)
        for f in findings:
            key = (f["item_code"], f["status"])
            cluster[key].append(f)

        patterns = []
        for (item_code, status), members in cluster.items():
            if len(members) < 2:
                continue
            # 跨实例?
            instance_ids = {m["run__db_config_id"] for m in members}
            is_cross_instance = len(instance_ids) > 1
            db_types = {m["run__db_config__db_type"] for m in members}
            pattern = {
                "item_code": item_code,
                "item_title": members[0]["item_title"],
                "status": status,
                "occurrence_count": len(members),
                "affected_instances": len(instance_ids),
                "is_cross_instance": is_cross_instance,
                "db_types": list(db_types),
                "knowledge": KNOWLEDGE_BASE.get(item_code, {}),
                "severity": self._estimate_severity(len(members), len(instance_ids), status),
                "auto_persist": is_cross_instance or len(members) >= 5,
            }
            patterns.append(pattern)

            # 写入 InspectionIssuePattern(若是新模式)
            if pattern["auto_persist"]:
                InspectionIssuePattern.objects.update_or_create(
                    pattern_key=f"{item_code}-{status}-{','.join(sorted(db_types))}",
                    defaults={
                        "item_code": item_code,
                        "item_title": pattern["item_title"],
                        "status": status,
                        "occurrence_count": len(members),
                        "affected_instances": len(instance_ids),
                        "db_types": list(db_types),
                        "knowledge": pattern["knowledge"],
                        "severity": pattern["severity"],
                        "last_seen": timezone.now(),
                    },
                )

        patterns.sort(key=lambda p: -p["occurrence_count"])
        return patterns

    def suggest_solution(self, item_code: str) -> Dict[str, Any]:
        """根据 item_code 推荐解决方案"""
        kb = KNOWLEDGE_BASE.get(item_code, {})
        if not kb:
            return {
                "found": False,
                "message": "暂无知识条目,建议 DBA 手工处理"
            }
        return {
            "found": True,
            "category": kb.get("category", "general"),
            "root_causes": kb.get("root_causes", []),
            "best_practices": kb.get("best_practices", []),
            "sql_snippets": kb.get("sql_snippets", []),
            "references": kb.get("references", []),
        }

    def _estimate_severity(self, count: int, instances: int, status: str) -> str:
        if status == "critical" and count >= 3:
            return "P0"
        if status == "critical" or count >= 5:
            return "P1"
        if count >= 3 or instances > 1:
            return "P2"
        return "P3"


# ============================================================================
# 趋势分析器
# ============================================================================

class TrendAnalyzer:
    """趋势分析器

    分析某项指标在时间维度上的趋势
    """

    def __init__(self, db_config, lookback_days: int = 30):
        self.db_config = db_config
        self.lookback_days = lookback_days

    def analyze_item(self, item_code: str) -> Dict[str, Any]:
        """分析某 item_code 的趋势"""
        from monitor.models import InspectionFinding
        since = timezone.now() - timedelta(days=self.lookback_days)
        history = InspectionFinding.objects.filter(
            run__db_config=self.db_config,
            item_code=item_code,
            run__started_at__gte=since,
        ).order_by("run__started_at").values(
            "run__started_at", "status", "summary"
        )
        points = list(history)
        if not points:
            return {"found": False, "item_code": item_code}

        # 按天聚合
        daily: Dict[str, Counter] = defaultdict(Counter)
        for p in points:
            day = p["run__started_at"].date().isoformat()
            daily[day][p["status"]] += 1

        timeline = []
        for day in sorted(daily.keys()):
            statuses = daily[day]
            timeline.append({
                "date": day,
                "ok": statuses.get("ok", 0),
                "warning": statuses.get("warning", 0),
                "critical": statuses.get("critical", 0),
                "error": statuses.get("error", 0),
            })

        # 趋势判断
        statuses_seq = [p["status"] for p in points]
        recent_worsening = False
        if len(statuses_seq) >= 3:
            score_map = {"ok": 0, "warning": 1, "critical": 2, "error": 3}
            scores = [score_map.get(s, 0) for s in statuses_seq]
            if scores[-1] > scores[0]:
                recent_worsening = True

        return {
            "found": True,
            "item_code": item_code,
            "timeline": timeline,
            "total_occurrences": len(points),
            "recent_worsening": recent_worsening,
            "current_status": points[-1]["status"] if points else "unknown",
        }


# ============================================================================
# 知识库管理
# ============================================================================

class KnowledgeBaseManager:
    """知识库管理"""

    @staticmethod
    def list_all() -> Dict[str, Dict[str, Any]]:
        """列出所有知识条目"""
        return KNOWLEDGE_BASE

    @staticmethod
    def get(item_code: str) -> Optional[Dict[str, Any]]:
        return KNOWLEDGE_BASE.get(item_code)

    @staticmethod
    def add(item_code: str, entry: Dict[str, Any]):
        """添加/更新知识条目"""
        KNOWLEDGE_BASE[item_code] = entry

    @staticmethod
    def count() -> int:
        return len(KNOWLEDGE_BASE)


# ============================================================================
# 便捷函数
# ============================================================================

def recognize_patterns(db_config=None, lookback_days: int = 30) -> List[Dict[str, Any]]:
    """识别巡检问题模式"""
    return PatternRecognizer(db_config, lookback_days).recognize()


def suggest_solution(item_code: str) -> Dict[str, Any]:
    """推荐解决方案"""
    return PatternRecognizer().suggest_solution(item_code)


def get_kb_count() -> int:
    """返回知识库条目数"""
    return KnowledgeBaseManager.count()
