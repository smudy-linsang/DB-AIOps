# -*- coding: utf-8 -*-
"""
Phase 6C: 验证回路 (phase6/30 §4)。cg_verify 消费组调用。

监视触发指标 window_sec, 连续 min_stable_checks 次满足 recover_expr → 恢复:
  PlaybookRun.succeeded + Incident.resolved(记 MTTR) + 通知
未恢复超窗 → PlaybookRun.timeout + Incident 回退 plan_ready + 升级 + 推荐下一方案。
"""
import json
import logging
import math
import re
import time
from datetime import timedelta

from django.utils import timezone

logger = logging.getLogger("monitor.verify_loop")


def read_observation(incident, metric, source, obj, since):
    """Return (value, sampled_at) from the actual source, or (None, None)."""
    if source == 'ash':
        from monitor.timeseries import get_timeseries_storage
        return get_timeseries_storage().latest_blocked_observation(incident.config_id, since)
    if source == 'live_config':
        value = _live_config_value(incident.config, metric)
        return value, timezone.now() if value is not None else None

    from monitor.models import MonitorLog
    sample = (MonitorLog.objects.filter(config_id=incident.config_id, create_time__gte=since)
              .order_by('-create_time', '-pk').first())
    if not sample:
        return None, None
    if metric == 'instance_up':
        return {'UP': 1, 'DOWN': 0}.get(sample.status), sample.create_time
    if sample.status != 'UP':
        return None, sample.create_time
    try:
        values = json.loads(sample.message)
    except (ValueError, TypeError):
        return None, sample.create_time
    if not isinstance(values, dict):
        return None, sample.create_time
    if metric == 'repl_running':
        io = str(values.get('slave_io_running', '')).lower()
        sql = str(values.get('slave_sql_running', '')).lower()
        running = ('yes', 'true', '1')
        value = int(io in running and sql in running) if io and sql else None
    elif metric == 'tablespace_used_pct':
        spaces = {str(t.get('name', '')).upper(): t.get('used_pct')
                  for t in (values.get('tablespaces') or []) if isinstance(t, dict)}
        measured = [v for v in spaces.values() if isinstance(v, (int, float))]
        value = spaces.get(str(obj).upper()) if obj else (max(measured) if measured else None)
    else:
        value = values.get(metric)
    return value, sample.create_time


class VerificationEvidence:
    """Track a sequence of distinct, recent samples after a playbook finishes."""

    def __init__(self, metric, expr, source, since, window, required, max_age, target=None):
        self.since = since
        self.max_age = timedelta(seconds=max_age)
        self.last_at = None
        self.report = {
            'state': 'observing',
            'metric': metric,
            'predicate': expr,
            'source': source,
            'target': target,
            'started_at': since.isoformat(),
            'window_seconds': window,
            'required_samples': required,
            'sample_count': 0,
            'passing_samples': 0,
            'missing_checks': 0,
            'stable_samples': 0,
            'recurrences': 0,
            'last_sample_at': None,
        }

    def observe(self, value, at, now):
        report = self.report
        try:
            number = float(value)
            usable = math.isfinite(number)
        except (TypeError, ValueError, OverflowError):
            usable = False
        if at is None or at < self.since or now - at > self.max_age or not usable:
            report['missing_checks'] += 1
            report['stable_samples'] = 0
            return False
        if self.last_at and at <= self.last_at:
            return False
        if self.last_at and at - self.last_at > self.max_age:
            report['stable_samples'] = 0
        self.last_at = at
        report['sample_count'] += 1
        report['last_sample_at'] = at.isoformat()
        report['value'] = number
        if _eval_recover(report['predicate'], number):
            report['passing_samples'] += 1
            report['stable_samples'] += 1
        else:
            # A failed observation after earlier passing evidence is a recurrence.
            if report.get('last_sample_passed'):
                report['recurrences'] += 1
            report['stable_samples'] = 0
        report['last_sample_passed'] = _eval_recover(report['predicate'], number)
        return report['stable_samples'] >= report['required_samples']

    def result(self, now, recovered=None):
        report = dict(self.report)
        report['observed_seconds'] = max(0, int((now - self.since).total_seconds()))
        report['recovered'] = recovered
        if recovered is True:
            report['state'] = 'verified'
        elif recovered is False:
            if report['sample_count'] < report['required_samples']:
                report['state'] = 'insufficient_evidence'
                report['recovered'] = None
            elif report['passing_samples']:
                report['state'] = 'unstable'
            else:
                report['state'] = 'not_recovered'
        return report


def _live_config_value(config, param: str):
    """实时查询数据库参数当前值。"""
    from monitor.db_connector import DbConnector
    conn = None
    try:
        conn = DbConnector.get_connection(config, readonly=True, statement_timeout_ms=5000)
        cur = conn.cursor()
        try:
            if config.db_type in ('mysql', 'tdsql', 'gbase'):
                cur.execute("SHOW VARIABLES WHERE Variable_name = %s", (param,))
                row = cur.fetchone()
                if not row:
                    return None
                return row['Value'] if isinstance(row, dict) else row[1]
            if config.db_type == 'pgsql':
                cur.execute("SELECT current_setting(%s, true)", (param,))
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
    m = re.fullmatch(r'(==|>=|<=|>|<)\s*(-?\d+(?:\.\d+)?)', expr.strip())
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
    from django.utils.dateparse import parse_datetime
    from monitor import degrade
    from monitor.models import PlaybookRun

    run = (PlaybookRun.objects.filter(run_id=payload.get('playbook_run_id'), status='verifying',
                                      incident__incident_id=payload.get('incident_id'))
           .select_related('incident__config').first())
    if not run:
        return

    inc = run.incident
    metric = payload.get('verify_metric')
    expr = payload.get('recover_expr')
    window = max(1, int(payload.get('window_sec', 300)))
    interval = max(1, int(payload.get('check_interval_sec', 15)))
    need_stable = max(1, int(payload.get('min_stable_checks', 3)))
    source = payload.get('data_source', 'collector')
    obj = payload.get('object') or None
    try:
        since = parse_datetime(str(payload.get('started_at', '')))
    except ValueError:
        since = None
    since = since or run.started_at or timezone.now()
    if timezone.is_naive(since):
        since = timezone.make_aware(since)
    # Collector sampling can be slower than polling; repeated reads are not samples.
    max_age = max(30, 2 * (inc.config.collect_interval_sec or 60)) if source in ('collector', 'golden') else 60
    evidence = VerificationEvidence(metric, expr, source, since, window, need_stable, max_age, obj)
    deadline = since + timedelta(seconds=window)
    while timezone.now() < deadline:
        try:
            value, at = read_observation(inc, metric, source, obj, since)
        except Exception as exc:
            degrade.note('verification.observation', reason=source, exc=exc)
            value, at = None, None
        now = timezone.now()
        if now >= deadline:
            break
        if evidence.observe(value, at, now):
            _on_recovered(inc, run, evidence.result(now, True))
            return
        if not PlaybookRun.objects.filter(pk=run.pk, status='verifying').update(verify_result=evidence.result(now)):
            return
        time.sleep(min(interval, max(0, (deadline - timezone.now()).total_seconds())))

    _on_timeout(inc, run, evidence.result(timezone.now(), False))


def _on_recovered(inc, run, result):
    from monitor.models import IncidentStateError
    if not type(run).objects.filter(pk=run.pk, status='verifying').update(
        status='succeeded', verify_result=result, finished_at=timezone.now(),
    ):
        return
    inc.refresh_from_db()
    try:
        if inc.status == 'verifying':
            inc.transition('resolved', 'system', f'验证通过({result["metric"]}恢复), 自动解决')
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


def _on_timeout(inc, run, result):
    from monitor.models import IncidentStateError
    if not type(run).objects.filter(pk=run.pk, status='verifying').update(
        status='timeout', verify_result=result, finished_at=timezone.now(),
    ):
        return
    inc.refresh_from_db()
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
