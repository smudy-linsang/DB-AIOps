"""Freshness of platform collector observations, independent of DB connectivity."""

import json
from math import isfinite

from django.utils import timezone
from django.db.models import OuterRef, Subquery

from monitor.models import MonitorLog

METRIC_EXPECTATIONS = {
    'mysql': ('active_connections', 'conn_usage_pct', 'qps'),
    'pgsql': ('active_connections', 'conn_usage_pct'),
    'oracle': ('active_connections', 'conn_usage_pct', 'qps'),
    'dm': ('active_connections', 'conn_usage_pct', 'qps'),
    'gbase': ('active_connections', 'conn_usage_pct', 'qps'),
    'tdsql': ('conn_usage_pct', 'qps'),
}


def with_collection_times(queryset):
    latest = MonitorLog.objects.filter(config_id=OuterRef('pk')).order_by('-create_time', '-pk')
    return queryset.annotate(
        latest_status=Subquery(latest.values('status')[:1]),
        latest_collect_time=Subquery(latest.values('create_time')[:1]),
        latest_success_time=Subquery(latest.filter(status='UP').values('create_time')[:1]),
    )


def collection_state(config, now):
    interval = config.collect_interval_sec or 60
    max_age = max(interval * 3, 30)
    attempt = config.latest_collect_time
    success = config.latest_success_time
    age = max(0, int((now - success).total_seconds())) if success else None
    attempt_age = max(0, int((now - attempt).total_seconds())) if attempt else None
    if not config.is_active:
        state = 'disabled'
    elif not attempt:
        state = 'never'
    elif attempt_age > max_age or (age is not None and age > max_age and config.latest_status == 'UP'):
        state = 'stale'
    elif config.latest_status == 'DOWN':
        state = 'failed'
    elif config.latest_status == 'UP':
        state = 'fresh'
    else:
        state = 'partial'
    return {
        'state': state,
        'last_attempt_at': attempt,
        'last_success_at': success,
        'last_status': config.latest_status,
        'success_age_seconds': age,
        'expected_interval_seconds': interval,
        'stale_after_seconds': max_age,
        'checked_at': now,
        'source': 'collector_log',
    }


def collection_detail(config):
    health = collection_state(config, timezone.now())
    samples = MonitorLog.objects.filter(config=config).order_by('-create_time', '-pk')
    success = samples.filter(status='UP').first()
    expected = METRIC_EXPECTATIONS.get(config.db_type, ())
    values = {}
    if success:
        try:
            recorded = json.loads(success.message)
        except (ValueError, TypeError):
            recorded = None
        if isinstance(recorded, dict):
            values = recorded
    health['missing_metrics'] = [
        metric for metric in expected
        if not isinstance(values.get(metric), (float, int))
        or isinstance(values[metric], bool) or not isfinite(values[metric])
    ] if success else None
    health['metrics_sample_at'] = success.create_time if success else None
    health['failure_reason'] = None
    if config.latest_status == 'DOWN':
        health['failure_reason'] = 'collection_failed'
        latest = samples.first()
        if latest:
            try:
                reason = json.loads(latest.message)
            except (ValueError, TypeError):
                reason = None
            if isinstance(reason, dict) and reason.get('status') == 'missing_driver':
                health['failure_reason'] = 'missing_driver'
    return health
