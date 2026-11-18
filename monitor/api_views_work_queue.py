"""Read model for the on-call queue, backed by the platform business database."""

from django.db.models import Case, Count, IntegerField, Q, Value, When
from django.http import JsonResponse
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.cache import never_cache

from monitor.alert_lifecycle import alert_observation
from monitor.collection_health import collection_detail, with_collection_times
from monitor.auth import Perm, require_auth, require_permission
from monitor.models import AlertLog, DatabaseConfig, Incident


PAGE_SIZE = 20
STAGES = ('pending', 'acknowledged', 'finished', 'all')


def incident_receipt():
    """Recognize legacy rows which have a confirmer but no time."""
    return Q(acked_at__isnull=False) | ~Q(acked_by='')


def queue_definition(kind):
    if kind == 'incidents':
        finished = Q(status__in=('resolved', 'closed'))
        receipt = incident_receipt()
        return Incident, {
            'pending': ~finished & ~receipt,
            'acknowledged': ~finished & receipt,
            'finished': finished,
        }, 'incident_id', 'priority', ('P1', 'P2', 'P3', 'P4'), 'occurred_at'
    return AlertLog, {
        'pending': Q(status='active'),
        'acknowledged': Q(status='acknowledged'),
        'finished': Q(status__in=('resolved', 'superseded')),
    }, 'id', 'severity', ('critical', 'error', 'warning', 'info'), 'create_time'


def queue_row(item, kind):
    row = {
        'id': item.incident_id if kind == 'incidents' else str(item.pk),
        'title': item.title,
        'database': item.config.name,
        'status': item.status,
    }
    if kind == 'incidents':
        row.update(
            level=item.priority, occurred_at=item.occurred_at,
            acked_by=item.acked_by, acked_at=item.acked_at, owner=item.owner, next_step=item.next_step,
        )
    else:
        row.update(
            level=item.severity, occurred_at=item.create_time,
            resolved_at=item.resolved_at, description=item.description[:2000],
            **alert_observation(item),
        )
    return row


@method_decorator(never_cache, name='dispatch')
@method_decorator(require_auth, name='dispatch')
@method_decorator(require_permission(Perm.ALERTS_VIEW), name='dispatch')
class OperationsQueueView(View):
    def get(self, request):
        kind = request.GET.get('kind', 'incidents')
        stage = request.GET.get('stage', 'pending')
        keyword = request.GET.get('q', '').strip()
        page_value = request.GET.get('page', '1')
        if (
            kind not in ('incidents', 'alerts') or stage not in STAGES
            or len(keyword) > 100 or not page_value.isascii()
            or not page_value.isdecimal() or len(page_value) > 6
            or not 1 <= int(page_value) <= 100000
        ):
            return JsonResponse({
                'code': 'INVALID_QUEUE_FILTER',
                'message': '筛选参数无效，请清除筛选后重试。',
            }, status=400)

        model, stages, identity, level, level_order, occurred = queue_definition(kind)
        visible = DatabaseConfig.objects.visible_to(request.user)
        rows = model.objects.filter(config__in=visible)
        if keyword:
            match = Q(title__icontains=keyword) | Q(config__name__icontains=keyword)
            if kind == 'incidents':
                match |= Q(incident_id__icontains=keyword)
            elif keyword.isascii() and keyword.isdecimal() and len(keyword) <= 18:
                match |= Q(pk=int(keyword))
            rows = rows.filter(match)
        counts = rows.aggregate(
            all=Count('pk'),
            **{key: Count('pk', filter=condition) for key, condition in stages.items()},
        )
        if stage != 'all':
            rows = rows.filter(stages[stage])
        rank = Case(
            *(When(**{level: name}, then=Value(index)) for index, name in enumerate(level_order)),
            default=Value(len(level_order)), output_field=IntegerField(),
        )
        rows = rows.select_related('config').order_by(rank, f'-{occurred}', f'-{identity}')
        total = counts[stage]
        page_count = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
        page = min(int(page_value), page_count)
        offset = (page - 1) * PAGE_SIZE
        return JsonResponse({
            'source': 'business_database',
            'checked_at': timezone.now(),
            'counts': counts,
            'total': total,
            'page': page,
            'page_size': PAGE_SIZE,
            'items': [queue_row(item, kind) for item in rows[offset:offset + PAGE_SIZE]],
        })


@method_decorator(never_cache, name='dispatch')
@method_decorator(require_auth, name='dispatch')
@method_decorator(require_permission(Perm.DATABASE_DETAIL_VIEW), name='dispatch')
class CollectionHealthView(View):
    def get(self, request, config_id):
        visible = DatabaseConfig.objects.visible_to(request.user)
        config = with_collection_times(visible).filter(pk=config_id).first()
        if not config:
            return JsonResponse({'message': '实例不存在或无权访问'}, status=404)
        return JsonResponse(collection_detail(config))
