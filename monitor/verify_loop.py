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


def _get_metric_value(incident, metric: str, data_source: str,
                      obj: str = None, since=None):
    """取验证指标当前值。data_source: ash|golden|collector|live_config。

    obj: 对象级指标的对象名 (如表空间名)。
    since: 只认该时刻之后的采集快照 (防止用处置前的旧数据误判恢复)。
    """
    cid = incident.config_id
    try:
        if metric == 'blocked_sessions' or data_source == 'ash':
            from monitor.timeseries import get_timeseries_storage
            return get_timeseries_storage().latest_blocked_count(cid, within_sec=60)

        if data_source == 'live_config':
            # 实时读参数值 (metric 即参数名), 用于配置回滚验证
            return _live_config_value(incident.config, metric)

        # golden / collector: 取最近 MonitorLog 快照的指标
        from monitor.models import MonitorLog
        import json
        qs = MonitorLog.objects.filter(config_id=cid)
        if since:
            qs = qs.filter(create_time__gte=since)
        log = qs.order_by('-create_time').first()
        if not log:
            return None
        d = json.loads(log.message) if isinstance(log.message, str) else log.message
        if not isinstance(d, dict):
            d = {}
        if metric == 'instance_up':
            return 1 if log.status == 'UP' else 0
        if metric == 'repl_running':
            # 复制线程状态从快照字段判定 (实例 UP 不代表复制在跑)
            io = str(d.get('slave_io_running', '')).lower()
            sql = str(d.get('slave_sql_running', '')).lower()
            if not io and not sql:
                return None
            return 1 if (io in ('yes', 'true', '1') and sql in ('yes', 'true', '1')) else 0
        if metric == 'tablespace_used_pct':
            # 对象级: 从 tablespaces 列表里找目标表空间
            tbs = d.get('tablespaces') or []
            pcts = {str(t.get('name', '')).upper(): t.get('used_pct')
                    for t in tbs if isinstance(t, dict)}
            if obj:
                return pcts.get(str(obj).upper())
            vals = [v for v in pcts.values() if isinstance(v, (int, float))]
            return max(vals) if vals else None
        return d.get(metric)
    except Exception as e:
        logger.debug("[verify] 取指标 %s 失败: %s", metric, e)
        return None


def _live_config_value(config, param: str):
    """实时查询数据库参数当前值。"""
    from monitor.db_connector import DbConnector
    conn = None
    try:
        conn = DbConnector.get_connection(config)
        cur = conn.cursor()
        try:
            if config.db_type in ('mysql', 'tdsql', 'gbase'):
                cur.execute(f"SHOW VARIABLES LIKE '{param}'")
                row = cur.fetchone()
                if not row:
                    return None
                return row['Value'] if isinstance(row, dict) else row[1]
            if config.db_type == 'pgsql':
                cur.execute(f"SHOW {param}")
                row = cur.fetchone()
                return row[0] if row else None
            if config.db_type in ('oracle', 'dm'):
                cur.execute("SELECT value FROM v$parameter WHERE name = :1", [param.lower()])
                row = cur.fetchone()
                return row[0] if row else None
            return None
        finally:
            cur.close()
    except Exception as e:
        logger.debug("[verify] 实时读参数 %s 失败: %s", param, e)
        return None
    finally:
        if conn:
            try:
                conn.close()
            except Exception:
                pass


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
    obj = payload.get('object') or None
    # 只认处置动作之后的采集快照, 防止旧数据误判恢复
    since = None
    if payload.get('started_at'):
        from django.utils.dateparse import parse_datetime
        try:
            since = parse_datetime(str(payload['started_at']))
        except Exception:
            since = None

    inc = Incident.objects.filter(incident_id=incident_id).select_related('config').first()
    run = PlaybookRun.objects.filter(run_id=run_id).first()
    if not inc or not run:
        return

    logger.info("[verify] 开始监视 %s: %s %s (窗口%ds)", incident_id, metric, expr, window)
    deadline = time.time() + window
    stable = 0
    while time.time() < deadline:
        val = _get_metric_value(inc, metric, source, obj=obj, since=since)
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
