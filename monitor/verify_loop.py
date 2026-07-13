# -*- coding: utf-8 -*-
"""
Phase 6C: 验证回路 (phase6/30 §4)。cg_verify 消费组调用。

监视触发指标 window_sec, 连续 min_stable_checks 次满足 recover_expr → 恢复:
  PlaybookRun.succeeded + Incident.resolved(记 MTTR) + 通知
未恢复超窗 → PlaybookRun.timeout + Incident 回退 plan_ready + 升级 + 推荐下一方案。
"""
import logging
import re
import time

from django.utils import timezone

logger = logging.getLogger("monitor.verify_loop")


def _get_metric_value(incident, metric: str, data_source: str):
    """取验证指标当前值。data_source: ash|golden|collector。"""
    cid = incident.config_id
    try:
        if metric == 'blocked_sessions' or data_source == 'ash':
            from monitor.timeseries import get_timeseries_storage
            return get_timeseries_storage().latest_blocked_count(cid, within_sec=60)
        # golden / collector: 取最近 MonitorLog 快照的指标
        from monitor.models import MonitorLog
        import json
        log = MonitorLog.objects.filter(config_id=cid).order_by('-create_time').first()
        if not log:
            return None
        d = json.loads(log.message) if isinstance(log.message, str) else log.message
        if metric in ('repl_running', 'instance_up'):
            # 派生: UP 即 1
            return 1 if log.status == 'UP' else 0
        return d.get(metric)
    except Exception as e:
        logger.debug("[verify] 取指标 %s 失败: %s", metric, e)
        return None


def _eval_recover(expr: str, value) -> bool:
    if value is None or not expr:
        return False
    m = re.match(r'(==|>=|<=|>|<)\s*([\d.]+)', expr.strip())
    if not m:
        return False
    op, n = m.group(1), float(m.group(2))
    try:
        v = float(value)
    except (TypeError, ValueError):
        return False
    return {'==': v == n, '>=': v >= n, '<=': v <= n, '>': v > n, '<': v < n}.get(op, False)


def run_verify(payload: dict):
    """cg_verify 消费入口。派生后台线程做监视, 立即返回让消费者可 XACK。"""
    import threading
    t = threading.Thread(target=_monitor, args=(payload,), daemon=True,
                         name=f"verify-{payload.get('playbook_run_id', '')}")
    t.start()


def _monitor(payload: dict):
    """阻塞式监视循环(后台线程)。"""
    from django.db import close_old_connections, connection as dj_conn
    close_old_connections()
    try:
        _monitor_impl(payload)
    finally:
        try:
            dj_conn.close()
        except Exception:
            pass


def _monitor_impl(payload: dict):
    from monitor.models import Incident, PlaybookRun, IncidentStateError

    run_id = payload.get('playbook_run_id')
    incident_id = payload.get('incident_id')
    metric = payload.get('verify_metric')
    expr = payload.get('recover_expr')
    window = int(payload.get('window_sec', 300))
    interval = int(payload.get('check_interval_sec', 15))
    need_stable = int(payload.get('min_stable_checks', 3))
    source = payload.get('data_source', 'collector')

    inc = Incident.objects.filter(incident_id=incident_id).select_related('config').first()
    run = PlaybookRun.objects.filter(run_id=run_id).first()
    if not inc or not run:
        return

    logger.info("[verify] 开始监视 %s: %s %s (窗口%ds)", incident_id, metric, expr, window)
    deadline = time.time() + window
    stable = 0
    while time.time() < deadline:
        val = _get_metric_value(inc, metric, source)
        if _eval_recover(expr, val):
            stable += 1
            logger.debug("[verify] %s 满足 %d/%d (值=%s)", incident_id, stable, need_stable, val)
            if stable >= need_stable:
                _on_recovered(inc, run, metric, val)
                return
        else:
            stable = 0
        time.sleep(interval)

    _on_timeout(inc, run, metric)


def _on_recovered(inc, run, metric, val):
    from monitor.models import IncidentStateError
    run.status = 'succeeded'
    run.verify_result = {'recovered': True, 'metric': metric, 'value': val,
                         'at': timezone.now().isoformat()}
    run.finished_at = timezone.now()
    run.save(update_fields=['status', 'verify_result', 'finished_at'])
    try:
        if inc.status == 'verifying':
            inc.transition('resolved', 'system', f'验证通过({metric}恢复), 自动解决')
    except IncidentStateError:
        pass
    inc.refresh_from_db()
    logger.info("[verify] %s 已恢复, 事故 resolved, MTTR=%ss", inc.incident_id, inc.t_resolve_sec)
    # 通知 + 知识沉淀
    try:
        from monitor.incident_notify import notify_incident_resolved
        notify_incident_resolved(inc)
    except Exception:
        pass
    _promote_problem(inc, run)


def _on_timeout(inc, run, metric):
    from monitor.models import IncidentStateError
    run.status = 'timeout'
    run.verify_result = {'recovered': False, 'metric': metric, 'note': '验证超时未恢复'}
    run.finished_at = timezone.now()
    run.save(update_fields=['status', 'verify_result', 'finished_at'])
    # 回退 plan_ready + 升级一级
    try:
        if inc.status == 'verifying':
            inc.transition('plan_ready', 'system', '验证超时, 回退待重新处置')
    except IncidentStateError:
        pass
    order = ['P4', 'P3', 'P2', 'P1']
    try:
        i = order.index(inc.priority)
        inc.priority = order[min(i + 1, 3)]
        inc.save(update_fields=['priority'])
    except ValueError:
        pass
    logger.warning("[verify] %s 验证超时, 回退 plan_ready 并升级至 %s", inc.incident_id, inc.priority)
    try:
        from monitor.incident_notify import notify_incident_plan_ready
        notify_incident_plan_ready(inc)
    except Exception:
        pass


def _promote_problem(inc, run):
    """成功处置沉淀为知识(Problem 关联 + 计数)。"""
    try:
        if inc.problem:
            inc.problem.status = 'mitigated'
            inc.problem.kb_ref = run.playbook.playbook_id
            inc.problem.save(update_fields=['status', 'kb_ref'])
    except Exception:
        pass
