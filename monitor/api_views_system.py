# -*- coding: utf-8 -*-
"""W4 自监控 API：系统健康 / 组件心跳明细 / 降级计数。

契约见 PROJECT_IMPROVEMENT_DESIGN.md §4.1。权限：Perm.DASHBOARD_VIEW
（能看仪表盘就能看系统健康）。

设计说明：健康状态放在 body 里、HTTP 恒返回 200 —— 探活接口返回 5xx
会让上游 LB 把节点摘掉，而那时恰恰需要它继续提供诊断信息。
"""
from django.http import JsonResponse
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt

from monitor.auth import Perm, require_auth, require_permission
from monitor.self_monitor import COMPONENTS


def _json_default(o):
    if hasattr(o, 'isoformat'):
        return o.isoformat()
    return str(o)


def _stale_info(hb, now):
    spec = COMPONENTS.get(hb.component)
    stale_after = spec[2] if spec else 300
    silent = int((now - hb.last_beat_at).total_seconds())
    return silent, stale_after


@method_decorator(csrf_exempt, name='dispatch')
@method_decorator(require_auth, name='dispatch')
@method_decorator(require_permission(Perm.DASHBOARD_VIEW), name='dispatch')
class SystemHealthView(View):
    """GET /api/v1/system/health —— 系统整体健康。"""

    def get(self, request):
        from monitor import degrade
        from monitor.healthcheck import PlatformHealthCheckView
        from monitor.models import ComponentHeartbeat

        now = timezone.now()
        hc = PlatformHealthCheckView()
        dependencies = {
            'database': hc._check_django_db(),
            'timescaledb': hc._check_timescaledb(),
            'redis': hc._check_redis(),
            'elasticsearch': hc._check_elasticsearch(),
        }

        components = []
        any_stale = False
        for code, (display, _interval, stale_after) in COMPONENTS.items():
            beats = list(ComponentHeartbeat.objects.filter(component=code))
            if not beats:
                components.append({'component': code, 'display': display,
                                   'status': 'unknown', 'instances': 0,
                                   'last_beat_at': None, 'silent_sec': None})
                continue
            latest = max(beats, key=lambda b: b.last_beat_at)
            silent = int((now - latest.last_beat_at).total_seconds())
            stale = silent > stale_after or any(b.status == 'down' for b in beats)
            any_stale = any_stale or stale
            components.append({
                'component': code, 'display': display,
                'status': 'down' if stale else 'up',
                'instances': len(beats),
                'last_beat_at': latest.last_beat_at,
                'silent_sec': silent,
            })

        degradations = degrade.snapshot()
        if dependencies['database'].get('status') != 'ok':
            status = 'down'
        elif any_stale or degradations:
            status = 'degraded'
        else:
            status = 'ok'

        return JsonResponse({
            'status': status,
            'checked_at': now,
            'components': components,
            'dependencies': dependencies,
            'degradations': degradations,
        }, json_dumps_params={'default': _json_default})


@method_decorator(csrf_exempt, name='dispatch')
@method_decorator(require_auth, name='dispatch')
@method_decorator(require_permission(Perm.DASHBOARD_VIEW), name='dispatch')
class SystemComponentsView(View):
    """GET /api/v1/system/components?status=up|down —— 组件心跳明细。"""

    def get(self, request):
        from monitor.models import ComponentHeartbeat

        now = timezone.now()
        qs = ComponentHeartbeat.objects.all().order_by('component', 'instance')
        status_filter = request.GET.get('status')

        items = []
        for hb in qs:
            silent, stale_after = _stale_info(hb, now)
            effective = 'down' if (hb.status == 'down' or silent > stale_after) else 'up'
            if status_filter and effective != status_filter:
                continue
            display = COMPONENTS.get(hb.component, (hb.component,))[0]
            items.append({
                'component': hb.component, 'display': display,
                'instance': hb.instance, 'status': effective,
                'last_beat_at': hb.last_beat_at, 'silent_sec': silent,
                'stale_after_sec': stale_after, 'meta': hb.meta,
            })
        return JsonResponse({'total': len(items), 'items': items},
                            json_dumps_params={'default': _json_default})


@method_decorator(csrf_exempt, name='dispatch')
@method_decorator(require_auth, name='dispatch')
@method_decorator(require_permission(Perm.DASHBOARD_VIEW), name='dispatch')
class SystemDegradationsView(View):
    """GET /api/v1/system/degradations —— 当前累计降级计数（进程内）。"""

    def get(self, request):
        from monitor import degrade
        items = [{'scope': scope, 'count': count}
                 for scope, count in sorted(degrade.snapshot().items(),
                                            key=lambda kv: -kv[1])]
        return JsonResponse({'since_process_start': True, 'items': items})
