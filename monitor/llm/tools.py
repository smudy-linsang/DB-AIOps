# -*- coding: utf-8 -*-
"""
Phase 8C: Agent 只读工具箱 (phase8/20 §14)。

安全约束 (phase8/30 §6):
1. 全部只读, 无自由 SQL 参数 —— 参数只有枚举/数值/短字符串;
2. run_tool 四步防御: 白名单校验 → 参数清洗 → 执行(捕获一切异常) → 观察截断;
3. 观察截断 AGENT_OBS_MAX_CHARS (默认4000字符)。
"""
import json
import logging

from django.conf import settings
from django.utils import timezone

logger = logging.getLogger("monitor.llm")


# ----------------------------------------------------------------------
# 工具实现 (均接收 config=DatabaseConfig 实例)
# ----------------------------------------------------------------------
def _t_current_metrics(config, params):
    """最新采集指标快照。"""
    from monitor.models import MonitorLog
    log = MonitorLog.objects.filter(config=config).order_by('-create_time').first()
    if not log:
        return {'error': '无采集数据'}
    try:
        d = json.loads(log.message) if isinstance(log.message, str) else log.message
    except Exception:
        return {'error': '指标解析失败'}
    return {k: v for k, v in (d or {}).items()
            if isinstance(v, (int, float, str)) and not isinstance(v, bool)}


def _t_metric_history(config, params):
    """单指标近 N 分钟历史 (metric: str, minutes: int<=1440)。"""
    metric = str(params.get('metric', ''))[:64]
    minutes = min(int(params.get('minutes', 60) or 60), 1440)
    if not metric:
        return {'error': '缺少 metric 参数'}
    from datetime import timedelta
    from monitor.elasticsearch_engine import query_metrics_aggregation
    try:
        rows = query_metrics_aggregation(
            config.id, metric,
            start_time=timezone.now() - timedelta(minutes=minutes),
            interval='5m')
        return {'metric': metric, 'minutes': minutes, 'points': (rows or [])[:100]}
    except Exception as e:
        return {'error': f'查询失败: {e}'}


def _t_ash_summary(config, params):
    """ASH 活动画像 (window_min<=120)。"""
    window = min(int(params.get('window_min', 30) or 30), 120)
    from monitor.context_aggregator import get_ash_timeline
    ash = get_ash_timeline(config.id, window_min=window) or {}
    return {'top_wait_events': (ash.get('top_wait_events') or [])[:8],
            'top_sql_by_samples': (ash.get('top_sql_by_samples') or [])[:8],
            'active_sessions_trend': (ash.get('active_sessions_trend') or [])[-20:]}


def _t_blocking_chains(config, params):
    """当前阻塞链。"""
    from monitor.context_aggregator import get_ash_timeline
    ash = get_ash_timeline(config.id, window_min=30) or {}
    return {'blocking_chains': (ash.get('blocking_chains') or [])[:10]}


def _t_recent_alerts(config, params):
    """近 N 小时告警 (hours<=72)。"""
    hours = min(int(params.get('hours', 24) or 24), 72)
    from datetime import timedelta
    from monitor.models import AlertLog
    cutoff = timezone.now() - timedelta(hours=hours)
    rows = AlertLog.objects.filter(config=config, create_time__gte=cutoff)\
        .order_by('-create_time')[:20]
    return {'alerts': [{'title': a.title, 'severity': a.severity,
                        'metric': getattr(a, 'metric_key', ''),
                        'time': a.create_time.isoformat()} for a in rows]}


def _t_recent_changes(config, params):
    """近 N 小时变更事件 (hours<=168)。"""
    hours = min(int(params.get('hours', 72) or 72), 168)
    from monitor.change_stream import query_changes
    return {'changes': query_changes(config, hours=hours, limit=20)}


def _t_slow_queries(config, params):
    """近期慢查询 Top (limit<=20)。"""
    limit = min(int(params.get('limit', 10) or 10), 20)
    try:
        from monitor.models import SlowQueryLog
        rows = SlowQueryLog.objects.filter(config=config)\
            .order_by('-create_time')[:limit]
        return {'slow_queries': [{
            'sql': (getattr(r, 'sql_text', '') or '')[:300],
            'elapsed': getattr(r, 'query_time', None),
            'time': r.create_time.isoformat()} for r in rows]}
    except Exception:
        return {'slow_queries': [], 'note': '慢查询表不可用'}


def _t_similar_cases(config, params):
    """相似历史案例检索 (query: str<=500)。"""
    query = str(params.get('query', ''))[:500]
    if not query:
        return {'error': '缺少 query 参数'}
    from monitor.case_rag_v2 import search_cases_v2
    return {'cases': search_cases_v2(query, db_type=config.db_type, top_k=3)}


def _t_replication_status(config, params):
    """复制/主从状态 (取自最新指标快照的复制相关键)。"""
    snap = _t_current_metrics(config, {})
    keys = ('repl_running', 'repl_lag_sec', 'slave_io_running', 'slave_sql_running',
            'seconds_behind_master', 'standby_lag', 'adg_apply_lag')
    return {k: snap.get(k) for k in keys if k in snap} or {'note': '无复制指标'}


def _t_capacity_status(config, params):
    """容量/表空间状态 (取自最新指标快照)。"""
    snap = _t_current_metrics(config, {})
    keys = ('tablespace_used_pct', 'disk_used_pct', 'tablespaces', 'datafile_count',
            'arch_gap', 'binlog_size_mb')
    return {k: snap.get(k) for k in keys if k in snap} or {'note': '无容量指标'}


# ----------------------------------------------------------------------
# 注册表: action → (func, 描述, 参数说明)
# ----------------------------------------------------------------------
TOOL_REGISTRY = {
    'current_metrics': (_t_current_metrics, '获取最新采集指标快照', '{}'),
    'metric_history': (_t_metric_history, '单指标近N分钟历史曲线', '{"metric": "conn_usage_pct", "minutes": 60}'),
    'ash_summary': (_t_ash_summary, 'ASH活动会话画像(等待事件/TopSQL)', '{"window_min": 30}'),
    'blocking_chains': (_t_blocking_chains, '当前阻塞链', '{}'),
    'recent_alerts': (_t_recent_alerts, '近N小时告警列表', '{"hours": 24}'),
    'recent_changes': (_t_recent_changes, '近N小时变更事件', '{"hours": 72}'),
    'slow_queries': (_t_slow_queries, '近期慢查询Top', '{"limit": 10}'),
    'similar_cases': (_t_similar_cases, '相似历史案例检索', '{"query": "症状描述"}'),
    'replication_status': (_t_replication_status, '复制/主从状态', '{}'),
    'capacity_status': (_t_capacity_status, '容量/表空间状态', '{}'),
}


def tools_description() -> str:
    """渲染给提示词的工具清单。"""
    return '\n'.join(f"- {name}: {desc} 参数示例 {example}"
                     for name, (_, desc, example) in TOOL_REGISTRY.items())


def run_tool(action: str, params: dict, config) -> str:
    """四步防御执行, 返回观察文本 (截断)。"""
    # 1. 白名单
    entry = TOOL_REGISTRY.get(action)
    if not entry:
        return json.dumps({'error': f'未知工具 {action}'}, ensure_ascii=False)
    func = entry[0]
    # 2. 参数清洗 (只接受 dict, 值只留标量并截断)
    clean = {}
    if isinstance(params, dict):
        for k, v in list(params.items())[:8]:
            if isinstance(v, (int, float, bool)):
                clean[str(k)[:32]] = v
            elif isinstance(v, str):
                clean[str(k)[:32]] = v[:500]
    # 3. 执行
    try:
        result = func(config, clean)
    except Exception as e:
        logger.warning("[agent] 工具 %s 执行异常: %s", action, e)
        result = {'error': f'工具执行异常: {e}'}
    # 4. 观察截断
    max_chars = int(getattr(settings, 'AGENT_OBS_MAX_CHARS', 4000))
    text = json.dumps(result, ensure_ascii=False, default=str)
    if len(text) > max_chars:
        text = text[:max_chars] + '…(截断)'
    return text
