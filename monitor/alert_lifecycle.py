"""Persist acknowledgement and restoration as separate facts."""

from django.db import transaction
from django.utils import timezone

from monitor import degrade
from monitor.models import AlertLog


def alert_observation(alert):
    return {
        'monitoring_state': {
            'active': 'ongoing', 'acknowledged': 'ongoing', 'resolved': 'recovered',
            'superseded': 'superseded',
        }.get(alert.status, 'unknown'),
        'is_acknowledged': bool(alert.acknowledged_at or alert.acknowledged_by or alert.status == 'acknowledged'),
        'acknowledged_at': alert.acknowledged_at,
        'acknowledged_by': alert.acknowledged_by,
        'recovery_evidence': alert.recovery_evidence,
    }


def acknowledge_alert(alert_id, config_id, actor):
    """Return the first receipt on a repeat acknowledgement."""
    with transaction.atomic():
        alert = AlertLog.objects.select_for_update().filter(pk=alert_id, config_id=config_id).first()
        if not alert or alert.status not in AlertLog.UNRESOLVED:
            return None
        if alert.status == 'acknowledged':
            return alert
        alert.status = 'acknowledged'
        alert.acknowledged_at = timezone.now()
        alert.acknowledged_by = actor
        alert.save(update_fields=['status', 'acknowledged_at', 'acknowledged_by'])
    sync_current_alert(alert)
    return alert


def sync_current_alert(alert):
    try:
        from monitor.elasticsearch_engine import sync_alert
        alert.refresh_from_db()
        sync_alert(alert)
    except Exception as exc:
        degrade.note('alert.search_sync', exc=exc)
