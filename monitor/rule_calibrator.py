# -*- coding: utf-8 -*-
"""
Phase 8B: 规则置信度校准 (phase8/20 §13)。

recompute_rule_stats(): 汇总 RcaFeedback → RuleStat, 平滑公式:
    calibrated_base = 0.3 * 0.6 + 0.7 * accuracy   (样本 >= RULE_CALIBRATION_MIN_SAMPLES)
    样本不足时维持默认 0.6。
get_calibrated_base(rule_id): RCA 引擎置信度计算的接入点 (带 5 分钟进程内缓存)。
"""
import logging
import threading
import time

from django.conf import settings

logger = logging.getLogger("monitor.llm")

DEFAULT_BASE = 0.6
_CACHE_TTL = 300
_cache_lock = threading.Lock()
_cache = {'ts': 0.0, 'data': {}}


def recompute_rule_stats() -> dict:
    """全量重算 RuleStat。partial 记 0.5 票。返回统计摘要。"""
    from django.db.models import Count
    from monitor.models import RcaFeedback, RuleStat

    min_samples = int(getattr(settings, 'RULE_CALIBRATION_MIN_SAMPLES', 5))
    # 命中次数: 统计 rca_result.root_causes 出现过该规则的事故数较昂贵,
    # 以反馈数为分母口径 (设计: phase8/20 §13, 反馈覆盖即样本)
    agg = {}
    for fb in RcaFeedback.objects.all().values('rule_id', 'verdict').annotate(n=Count('id')):
        d = agg.setdefault(fb['rule_id'], {'correct': 0.0, 'wrong': 0.0})
        if fb['verdict'] == 'correct':
            d['correct'] += fb['n']
        elif fb['verdict'] == 'wrong':
            d['wrong'] += fb['n']
        else:  # partial
            d['correct'] += fb['n'] * 0.5
            d['wrong'] += fb['n'] * 0.5

    updated = 0
    for rule_id, d in agg.items():
        total = d['correct'] + d['wrong']
        accuracy = (d['correct'] / total) if total else 0.0
        if total >= min_samples:
            base = round(0.3 * DEFAULT_BASE + 0.7 * accuracy, 3)
            base = max(0.2, min(base, 0.9))  # 防极端: 单规则基础分限幅
        else:
            base = DEFAULT_BASE
        RuleStat.objects.update_or_create(rule_id=rule_id, defaults={
            'hit_count': int(total),
            'correct_count': int(d['correct']),
            'wrong_count': int(d['wrong']),
            'accuracy': round(accuracy, 3),
            'calibrated_base': base,
        })
        updated += 1
    _invalidate_cache()
    logger.info("[calibrate] 规则校准完成: %d 条规则", updated)
    return {'rules_updated': updated}


def _invalidate_cache():
    with _cache_lock:
        _cache['ts'] = 0.0
        _cache['data'] = {}


def _load_cache() -> dict:
    with _cache_lock:
        if time.time() - _cache['ts'] < _CACHE_TTL:
            return _cache['data']
    try:
        from monitor.models import RuleStat
        data = {r.rule_id: r.calibrated_base for r in RuleStat.objects.all()}
    except Exception:
        data = {}
    with _cache_lock:
        _cache['ts'] = time.time()
        _cache['data'] = data
    return data


def get_calibrated_base(rule_id: str) -> float:
    """RCA 置信度基础分。校准关闭/无数据时返回默认 0.6。"""
    if not getattr(settings, 'RULE_CALIBRATION_ENABLED', True):
        return DEFAULT_BASE
    try:
        return float(_load_cache().get(rule_id, DEFAULT_BASE))
    except Exception:
        return DEFAULT_BASE
