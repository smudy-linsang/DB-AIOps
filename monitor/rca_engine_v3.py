# -*- coding: utf-8 -*-
"""
DB-AIOps v2.0: RCA 3.0 智能因果图谱推导引擎
=========================================
基于数据库多维异动特征、变更流、锁等待拓扑与等待事件，推导级联故障因果链并保存结构化节点。
"""
import logging
from typing import Dict, Any, List, Optional
from django.db import transaction
from django.utils import timezone
from monitor.models import ChangeEvent, Incident, IncidentCauseChain

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
            distance_min = max(
                0, (incident.created_at - recent_change.occurred_at).total_seconds() / 60)
            confidence = round(max(0.50, 0.95 - min(distance_min, 120) / 120 * 0.35), 3)
            steps.append({
                'step_seq': step_seq,
                'node_type': 'CHANGE',
                'node_name': f"变更事件: {recent_change.title}",
                'description': f"在故障发生前 {int((incident.created_at - recent_change.occurred_at).total_seconds()/60)} 分钟登记了 [{recent_change.change_type}] 变更",
                'evidence_refs': [f'change:{recent_change.id}'],
                'confidence': confidence,
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
                'evidence_refs': [f"rca:{sql_root.get('rule_id') or 'sql'}"],
                'confidence': min(0.999, max(0.0, float(sql_root.get('confidence') or 0.5))),
                'metric_snapshot': sql_root.get('related_metrics') or {}
            })
            step_seq += 1

        # 3. 检查是否有锁等待阻塞 (Step 3)
        lock_root = next((r for r in root_causes if 'lock' in (r.get('domain') or '').lower()), None)
        lock_events = list(incident.events.filter(
            signal__in=('blocked_session', 'lock_wait', 'deadlock_surge'))[:20])
        if lock_root or lock_events:
            event_refs = [f'event:{event.event_uid}' for event in lock_events]
            evidence_refs = event_refs or [f"rca:{lock_root.get('rule_id') or 'lock'}"]
            snapshots = [event.detail or {} for event in lock_events]
            confidence = float((lock_root or {}).get('confidence') or
                               min(0.95, 0.55 + 0.08 * len(lock_events)))
            steps.append({
                'step_seq': step_seq,
                'node_type': 'LOCK',
                'node_name': "根源阻塞源持锁超时",
                'description': ((lock_root or {}).get('description') or
                                f'检测到 {len(lock_events)} 条锁等待/阻塞事件'),
                'evidence_refs': evidence_refs,
                'confidence': min(0.999, max(0.0, confidence)),
                'metric_snapshot': {'events': snapshots[:5]}
            })
            step_seq += 1

        # 4. 资源或连接耗尽衍生影响 (Step 4)
        conn_events = list(incident.events.filter(signal='conn_high')[:20])
        health = incident.health_snapshot if isinstance(incident.health_snapshot, dict) else {}
        conn_snapshot = {
            key: health[key] for key in
            ('conn_usage_pct', 'active_connections', 'max_connections')
            if health.get(key) is not None
        }
        if conn_events or conn_snapshot:
            refs = [f'event:{event.event_uid}' for event in conn_events]
            if conn_snapshot:
                refs.append(f'incident:{incident.incident_id}:health_snapshot')
            steps.append({
                'step_seq': step_seq,
                'node_type': 'RESOURCE',
                'node_name': "数据库连接水位异常",
                'description': '事件或事故快照显示连接水位超过基线',
                'evidence_refs': refs,
                'confidence': min(0.95, 0.60 + 0.05 * len(refs)),
                'metric_snapshot': {
                    **conn_snapshot,
                    'events': [(event.detail or {}) for event in conn_events[:5]],
                }
            })

        # 持久化到 IncidentCauseChain 表 (先清后插)
        objs = [IncidentCauseChain(
                incident=incident,
                step_seq=s['step_seq'],
                node_type=s['node_type'],
                node_name=s['node_name'],
                description=s['description'],
                evidence_refs=s['evidence_refs'],
                confidence=s['confidence'],
                metric_snapshot=s['metric_snapshot']) for s in steps]
        with transaction.atomic():
            IncidentCauseChain.objects.filter(incident=incident).delete()
            created = IncidentCauseChain.objects.bulk_create(objs)
        return [{
                'step_seq': obj.step_seq,
                'node_type': obj.node_type,
                'node_name': obj.node_name,
                'description': obj.description,
                'evidence_refs': obj.evidence_refs,
                'confidence': float(obj.confidence)
            } for obj in created]
