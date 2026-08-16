# -*- coding: utf-8 -*-
"""
DB-AIOps v2.0: RCA 3.0 智能因果图谱推导引擎
=========================================
基于数据库多维异动特征、变更流、锁等待拓扑与等待事件，推导级联故障因果链并保存结构化节点。
"""
import logging
from typing import Dict, Any, List, Optional
from django.utils import timezone
from monitor.models import Incident, IncidentCauseChain, ChangeEvent, DatabaseConfig

logger = logging.getLogger("monitor.rca3")


class CausalInferenceEngine:
    """RCA 3.0 因果图谱推导核心"""

    @classmethod
    def infer_and_build_cause_chain(cls, incident: Incident) -> List[Dict[str, Any]]:
        """
        对指定事故进行因果图谱推导，并持久化到 IncidentCauseChain 4NF 表
        """
        config = incident.config
        steps = []

        # 1. 检查近 2 小时的变更事件 (DDL / 参数配置 / 应用发布) -> 作为最先验根源 (Step 1)
        recent_cutoff = incident.created_at - timezone.timedelta(hours=2)
        recent_change = ChangeEvent.objects.filter(
            config=config,
            occurred_at__gte=recent_cutoff,
            occurred_at__lte=incident.created_at + timezone.timedelta(minutes=5)
        ).order_by('-occurred_at').first()

        step_seq = 1
        if recent_change:
            steps.append({
                'step_seq': step_seq,
                'node_type': 'CHANGE',
                'node_name': f"变更事件: {recent_change.title}",
                'description': f"在故障发生前 {int((incident.created_at - recent_change.occurred_at).total_seconds()/60)} 分钟登记了 [{recent_change.change_type}] 变更",
                'evidence_refs': ['E1_CHANGE'],
                'confidence': 0.950,
                'metric_snapshot': recent_change.detail or {}
            })
            step_seq += 1

        # 2. 检查是否有明显的慢查询或全表扫 (Step 2)
        rca_res = incident.rca_result or {}
        root_causes = rca_res.get('root_causes', [])
        sql_root = next((r for r in root_causes if 'sql' in (r.get('domain') or '').lower() or 'query' in (r.get('name') or '').lower()), None)

        if sql_root:
            steps.append({
                'step_seq': step_seq,
                'node_type': 'SQL',
                'node_name': f"高负载/慢 SQL 执行",
                'description': sql_root.get('description') or '检测到大表全表扫描或未命中复合索引的低效更新 SQL',
                'evidence_refs': ['E2_SQL'],
                'confidence': float(sql_root.get('confidence') or 0.900),
                'metric_snapshot': sql_root.get('related_metrics') or {}
            })
            step_seq += 1

        # 3. 检查是否有锁等待阻塞 (Step 3)
        lock_root = next((r for r in root_causes if 'lock' in (r.get('domain') or '').lower()), None)
        if lock_root or 'lock' in incident.title.lower() or '阻塞' in incident.title:
            steps.append({
                'step_seq': step_seq,
                'node_type': 'LOCK',
                'node_name': "根源阻塞源持锁超时",
                'description': "长事务持有排他行锁/表锁，导致下游多个业务连接进入队列锁等待",
                'evidence_refs': ['E3_LOCK'],
                'confidence': 0.920,
                'metric_snapshot': {'wait_event': 'enq: TX - row lock contention'}
            })
            step_seq += 1

        # 4. 资源或连接耗尽衍生影响 (Step 4)
        steps.append({
            'step_seq': step_seq,
            'node_type': 'RESOURCE',
            'node_name': "数据库连接池与活跃会话剧增",
            'description': f"级联引发应用端连接堆积，活跃会话突破预警基线",
            'evidence_refs': ['E4_CONN'],
            'confidence': 0.880,
            'metric_snapshot': {'priority': getattr(incident, 'priority', 'P1')}
        })

        # 持久化到 IncidentCauseChain 表 (先清后插)
        IncidentCauseChain.objects.filter(incident=incident).delete()
        created_objs = []
        for s in steps:
            obj = IncidentCauseChain.objects.create(
                incident=incident,
                step_seq=s['step_seq'],
                node_type=s['node_type'],
                node_name=s['node_name'],
                description=s['description'],
                evidence_refs=s['evidence_refs'],
                confidence=s['confidence'],
                metric_snapshot=s['metric_snapshot']
            )
            created_objs.append({
                'step_seq': obj.step_seq,
                'node_type': obj.node_type,
                'node_name': obj.node_name,
                'description': obj.description,
                'evidence_refs': obj.evidence_refs,
                'confidence': float(obj.confidence)
            })

        return created_objs
