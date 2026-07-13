# -*- coding: utf-8 -*-
"""
Phase 6C: SLA 报表 (phase6/30 §8)。

从 Incident 时间戳统计 MTTD/MTTA/MTTR + 1-5-10 达标率, 仅承诺 8 类范围。
"""
import datetime
import logging

from django.utils import timezone

logger = logging.getLogger("monitor.sla_report")

# 承诺 1-5-10 的 8 类 (phase6 母文档第四部分)
COMMITTED_CATEGORIES = {'availability', 'lock', 'connection', 'capacity',
                        'replication', 'performance', 'config'}


def _pct(a, b):
    return round(a / b * 100, 1) if b else 0.0


def _mean(vals):
    vals = [v for v in vals if v is not None]
    return round(sum(vals) / len(vals), 1) if vals else None


def _p(vals, q):
    vals = sorted(v for v in vals if v is not None)
    if not vals:
        return None
    idx = min(int(len(vals) * q), len(vals) - 1)
    return round(vals[idx], 1)


def build_sla_report(date_from=None, date_to=None, category=None) -> dict:
    from monitor.models import Incident
    to = date_to or timezone.now()
    frm = date_from or (to - datetime.timedelta(days=7))

    qs = Incident.objects.filter(created_at__gte=frm, created_at__lte=to)
    if category:
        qs = qs.filter(category=category)
    else:
        qs = qs.filter(category__in=COMMITTED_CATEGORIES)

    incidents = list(qs)

    def collect(items):
        mttd = [i.t_detect_sec for i in items]
        mtta = [((i.acked_at - i.detected_at).total_seconds()
                 if i.acked_at and i.detected_at else None) for i in items]
        mttr = [i.t_resolve_sec for i in items]
        resolved = [i for i in items if i.resolved_at]
        detect_ok = sum(1 for i in items if i.sla_detect_ok)
        plan_ok = sum(1 for i in items if i.sla_plan_ok)
        resolve_ok = sum(1 for i in resolved if i.sla_resolve_ok)
        return {
            'count': len(items),
            'resolved_count': len(resolved),
            'mttd_sec': _mean(mttd), 'mttd_p90': _p(mttd, 0.9),
            'mtta_sec': _mean(mtta),
            'mttr_sec': _mean(mttr), 'mttr_p90': _p(mttr, 0.9),
            'detect_ok_rate': _pct(detect_ok, len(items)),
            'plan_ok_rate': _pct(plan_ok, len(items)),
            'resolve_ok_rate': _pct(resolve_ok, len(resolved)),
        }

    overall = collect(incidents)

    by_cat = []
    cats = sorted({i.category for i in incidents})
    for c in cats:
        items = [i for i in incidents if i.category == c]
        d = collect(items)
        d['category'] = c
        by_cat.append(d)

    return {
        'range': [frm.isoformat(), to.isoformat()],
        'overall': overall,
        'by_category': by_cat,
    }
