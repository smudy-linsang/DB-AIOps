# -*- coding: utf-8 -*-
"""
DB-AIOps v2.0: RCA 3.0 智能因果图谱推导引擎
=========================================
基于数据库多维异动特征、变更流、锁等待拓扑与等待事件，推导级联故障因果链并保存结构化节点。
"""
import logging
from datetime import timedelta
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

        # 1. 检查变更事件 (DDL / 参数配置 / 应用发布) -> 作为最先验根源 (Step 1)
        #
        # V2.6 W3-02 三处修正：
        # ① **锚点**：原用 incident.created_at，但该字段是 auto_now_add 的
        #    "入库时刻"，不是故障发生时刻。对补录/回填的事故，created_at 可能是
        #    数小时之后才写库，窗口会整体偏移到错误的时间段。改用 occurred_at
        #    （"发生时刻"，语义正确）；异常情况回退 created_at。
        # ② 时间窗上界原为 anchor + 5min。对持续数小时的事故做重诊断时，
        #    "事故之后的变更"永远落在窗外，因果链丢失最新线索。改为
        #    max(now, anchor + 5min)：短事故行为不变，长事故能看到全窗。
        # ③ 原用 timezone.timedelta —— 依赖 django.utils.timezone 恰好重导出
        #    datetime.timedelta 这一未文档化行为。改用显式 datetime.timedelta。
        anchor = incident.occurred_at or incident.created_at
        recent_cutoff = anchor - timedelta(hours=2)
        window_upper = max(timezone.now(), anchor + timedelta(minutes=5))
        recent_change = ChangeEvent.objects.filter(
            config=config,
            occurred_at__gte=recent_cutoff,
            occurred_at__lte=window_upper
        ).order_by('-occurred_at').first()

        step_seq = 1
        if recent_change:
            # 距离与置信度同样以 anchor（发生时刻）为基准，保持与窗口一致。
            # 变更晚于故障时距离取 0（不出现负数分钟数）。
            delta_min = (anchor - recent_change.occurred_at).total_seconds() / 60
            distance_min = max(0, delta_min)
            confidence = round(max(0.50, 0.95 - min(distance_min, 120) / 120 * 0.35), 3)
            if delta_min >= 0:
                timing = f"在故障发生前 {int(delta_min)} 分钟"
            else:
                timing = f"在故障发生后 {int(-delta_min)} 分钟"
            steps.append({
                'step_seq': step_seq,
                'node_type': 'CHANGE',
                'node_name': f"变更事件: {recent_change.title}",
                'description': f"{timing}登记了 [{recent_change.change_type}] 变更",
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
        #
        # V2.6 W3-02 修正：此处原读 incident.health_snapshot，但该字段是
        # FloatField（models.py 的 "生成时健康分"），而代码按 dict 使用且加了
        # isinstance(..., dict) 判断 —— 恒为 False，conn_snapshot 永远是空字典，
        # 该分支实际只依赖 conn_events，且描述文本与证据不符（假证据）。
        # 真正的连接水位快照在 incident.impact（JSONField）里，改读它。
        conn_events = list(incident.events.filter(signal='conn_high')[:20])
        impact_doc = incident.impact if isinstance(incident.impact, dict) else {}
        conn_snapshot = {
            key: impact_doc[key] for key in
            ('conn_usage_pct', 'active_connections', 'max_connections')
            if impact_doc.get(key) is not None
        }
        if conn_events or conn_snapshot:
            refs = [f'event:{event.event_uid}' for event in conn_events]
            if conn_snapshot:
                refs.append(f'incident:{incident.incident_id}:impact')
            steps.append({
                'step_seq': step_seq,
                'node_type': 'RESOURCE',
                'node_name': "数据库连接水位异常",
                'description': '事件或事故影响评估显示连接水位超过基线',
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
