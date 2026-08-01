# -*- coding: utf-8 -*-
"""
Phase 8D: 因果边挖掘 (phase8/20 §17)。

对每个实例, 用 ES 5 分钟聚合序列做滞后互相关 (纯 Python 皮尔逊):
- 候选指标: 最新快照中的数值指标, 按序列方差取 Top CAUSAL_MAX_METRICS;
- 对有序对 (cause, effect), lag ∈ {5,10,15,30} 分钟, 取 |r| 最大的 lag;
- |r| >= CAUSAL_MIN_STRENGTH 且对齐样本 >= MIN_SAMPLES → upsert CausalEdge;
- 不再满足的旧边删除 (每次全量重算该实例)。
产出供 RCA 因果链展示, 不参与任何自动决策。
"""
import json
import logging
import math
from datetime import timedelta

from django.conf import settings
from django.utils import timezone

logger = logging.getLogger("monitor.llm")

LAGS_MIN = (5, 10, 15, 30)
MIN_SAMPLES = 50
MAX_METRICS = 12
_BUCKET_MS = 5 * 60 * 1000  # 5m 聚合桶


def _candidate_metrics(config) -> list:
    """从最新快照挑数值型指标名。"""
    from monitor.models import MonitorLog
    log = MonitorLog.objects.filter(config=config).order_by('-create_time').first()
    if not log:
        return []
    try:
        d = json.loads(log.message) if isinstance(log.message, str) else (log.message or {})
    except Exception:
        return []
    return [k for k, v in d.items()
            if isinstance(v, (int, float)) and not isinstance(v, bool)][:40]


def _fetch_series(config_id: int, metric: str, days: int) -> dict:
    """{timestamp_ms: avg} 5分钟对齐序列。"""
    from monitor.elasticsearch_engine import query_metrics_aggregation
    rows = query_metrics_aggregation(
        config_id, metric,
        start_time=(timezone.now() - timedelta(days=days)).replace(tzinfo=None),
        interval='5m')
    return {r['timestamp']: r['avg'] for r in (rows or []) if r.get('avg') is not None}


def _variance(series: dict) -> float:
    vals = list(series.values())
    if len(vals) < 2:
        return 0.0
    mean = sum(vals) / len(vals)
    return sum((v - mean) ** 2 for v in vals) / len(vals)


def _pearson(xs: list, ys: list) -> float:
    n = len(xs)
    if n < 2:
        return 0.0
    mx, my = sum(xs) / n, sum(ys) / n
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    if sxx <= 0 or syy <= 0:
        return 0.0
    return sxy / math.sqrt(sxx * syy)


def _lagged_corr(cause: dict, effect: dict, lag_min: int) -> tuple:
    """cause[t] vs effect[t+lag]。返回 (r, n)。"""
    shift = lag_min * 60 * 1000
    xs, ys = [], []
    for ts, v in cause.items():
        w = effect.get(ts + shift)
        if w is not None:
            xs.append(v)
            ys.append(w)
    return _pearson(xs, ys), len(xs)


def mine_config(config, days: int = None) -> dict:
    """单实例全量重算因果边。"""
    from monitor.models import CausalEdge
    days = int(days or getattr(settings, 'CAUSAL_LOOKBACK_DAYS', 7))
    min_strength = float(getattr(settings, 'CAUSAL_MIN_STRENGTH', 0.6))

    metrics = _candidate_metrics(config)
    if len(metrics) < 2:
        return {'config': config.name, 'edges': 0, 'note': '指标不足'}

    series = {}
    for m in metrics:
        s = _fetch_series(config.id, m, days)
        if len(s) >= MIN_SAMPLES:
            series[m] = s
    # 按方差取 Top N (常量序列无相关意义)
    ranked = sorted(series, key=lambda m: -_variance(series[m]))
    picked = [m for m in ranked if _variance(series[m]) > 0][:MAX_METRICS]
    if len(picked) < 2:
        return {'config': config.name, 'edges': 0, 'note': '有效序列不足'}

    found = []
    for a in picked:
        for b in picked:
            if a == b:
                continue
            best_r, best_lag, best_n = 0.0, 0, 0
            for lag in LAGS_MIN:
                r, n = _lagged_corr(series[a], series[b], lag)
                if n >= MIN_SAMPLES and abs(r) > abs(best_r):
                    best_r, best_lag, best_n = r, lag, n
            if abs(best_r) >= min_strength:
                found.append((a, b, best_lag, round(best_r, 3)))

    # 全量重算: 先清后写 (事务内)
    from django.db import transaction
    with transaction.atomic():
        CausalEdge.objects.filter(config=config).delete()
        for a, b, lag, r in found:
            CausalEdge.objects.create(
                config=config, cause_metric=a, effect_metric=b,
                lag_minutes=lag, strength=r, sample_days=days)
    logger.info("[causal] %s: %d 条因果边 (候选指标 %d)", config.name, len(found), len(picked))
    return {'config': config.name, 'edges': len(found), 'metrics': len(picked)}


def mine_all(days: int = None) -> dict:
    """全部启用监控的实例。供每日任务调用。"""
    if not getattr(settings, 'CAUSAL_MINER_ENABLED', True):
        return {'skipped': True}
    from monitor.models import DatabaseConfig
    results = []
    for cfg in DatabaseConfig.objects.filter(is_active=True):
        try:
            results.append(mine_config(cfg, days=days))
        except Exception as e:
            logger.warning("[causal] %s 挖掘失败: %s", cfg.name, e)
    return {'configs': len(results), 'results': results}


def get_mined_edges(config_id: int, metric: str = None, limit: int = 5) -> list:
    """查已挖掘的因果边 (RCA 因果链增强用)。metric 命中 cause 或 effect 时优先。"""
    from monitor.models import CausalEdge
    try:
        qs = CausalEdge.objects.filter(config_id=config_id)
        if metric:
            from django.db.models import Q
            hit = list(qs.filter(Q(cause_metric=metric) | Q(effect_metric=metric))
                       .order_by('-strength')[:limit])
            if hit:
                return [_edge_dict(e) for e in hit]
        return [_edge_dict(e) for e in qs.order_by('-strength')[:limit]]
    except Exception:
        return []


def _edge_dict(e) -> dict:
    return {'cause': e.cause_metric, 'effect': e.effect_metric,
            'lag_minutes': e.lag_minutes, 'strength': e.strength}
