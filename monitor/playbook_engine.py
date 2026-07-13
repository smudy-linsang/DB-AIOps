# -*- coding: utf-8 -*-
"""
Phase 6C: Playbook 执行引擎 (phase6/30 §5)。

execute_run(run_id) 状态机:
  pending_approval →(授权/自动)→ prechecking →(全过)→ executing
  →(steps成功)→ verifying(emit_verify) ; →(失败on_fail=abort)→ failed
  →(失败on_fail=rollback)→ rolled_back
precheck 失败 = 零写操作 (安全铁律)。每步留痕 + AuditLog。连接不泄漏。
"""
import logging
import re
import time

from django.utils import timezone

logger = logging.getLogger("monitor.playbook_engine")


def _render(sql: str, params: dict) -> str:
    """渲染 {placeholder}。缺失占位符保留原样(precheck 会拦)。"""
    if not sql:
        return sql
    def repl(m):
        k = m.group(1)
        return str(params.get(k, m.group(0)))
    return re.sub(r'\{(\w+)\}', repl, sql)


def _pick_sql(step: dict, db_type: str, params: dict) -> str:
    by_db = step.get('sql_by_db') or {}
    key = {'postgresql': 'pgsql'}.get(db_type, db_type)
    sql = by_db.get(key) or step.get('sql') or ''
    return _render(sql, params)


def _run_step(conn, db_type: str, step: dict, params: dict) -> dict:
    """执行单步, 返回 step_result dict。"""
    started = timezone.now()
    sql = _pick_sql(step, db_type, params)
    res = {'seq': step.get('seq'), 'action': step.get('action', 'execute'),
           'desc': step.get('desc', ''), 'sql': sql,
           'started_at': started.isoformat(), 'status': 'ok',
           'rows_affected': 0, 'output': '', 'error': ''}
    try:
        cur = conn.cursor()
        try:
            cur.execute(sql)
            if step.get('action') == 'query':
                rows = cur.fetchall()
                res['rows_affected'] = len(rows)
                res['output'] = str(rows[:3])[:300]
            else:
                res['rows_affected'] = cur.rowcount if cur.rowcount is not None else 0
            try:
                conn.commit()
            except Exception:
                pass
        finally:
            cur.close()
    except Exception as e:
        res['status'] = 'fail'
        res['error'] = str(e)[:300]
    res['finished_at'] = timezone.now().isoformat()
    return res


def _eval_expect(expect: str, result: dict) -> bool:
    """判定式: rows>=N / ok / affected>=N。"""
    if not expect or expect == 'ok':
        return result['status'] == 'ok'
    m = re.match(r'(rows|affected)\s*(>=|>|==|<=|<)\s*(\d+)', expect)
    if m:
        field = 'rows_affected'
        val = result.get(field, 0)
        op, n = m.group(2), int(m.group(3))
        return {'>=': val >= n, '>': val > n, '==': val == n,
                '<=': val <= n, '<': val < n}.get(op, False)
    return result['status'] == 'ok'


def _audit(incident, action, desc, status, sql=''):
    try:
        from monitor.models import AuditLog
        AuditLog.objects.create(
            config=incident.config, action_type='EXECUTE_SQL', risk_level='medium',
            status='success' if status == 'ok' else 'failed',
            description=desc[:500], sql_command=(sql or '')[:2000], executor='playbook')
    except Exception:
        pass


def execute_run(run_id: str) -> dict:
    """执行一个 PlaybookRun。返回结果摘要。"""
    from monitor.models import PlaybookRun, IncidentStateError
    from monitor.db_connector import DbConnector

    run = PlaybookRun.objects.select_related('playbook', 'incident', 'incident__config')\
        .filter(run_id=run_id).first()
    if not run:
        return {'error': 'run not found'}
    if run.status not in ('pending_approval', 'prechecking'):
        return {'error': f'非法状态 {run.status}', 'code': 'CONFLICT'}

    pb = run.playbook
    inc = run.incident
    config = inc.config
    db_type = config.db_type
    params = run.params or {}

    run.status = 'prechecking'
    run.started_at = timezone.now()
    run.save(update_fields=['status', 'started_at'])

    # 事故状态 → executing (首次)
    try:
        if inc.status in ('plan_ready',):
            inc.transition('executing', 'playbook', f'执行 {pb.playbook_id}')
    except IncidentStateError:
        pass

    conn = None
    step_results = []
    try:
        conn = DbConnector.get_connection(config)

        # 1. precheck (全过才继续; 任一失败 → failed, 零写操作)
        for st in (pb.precheck or []):
            r = _run_step(conn, db_type, st, params)
            step_results.append(dict(r, phase='precheck'))
            if not _eval_expect(st.get('expect', 'ok'), r):
                run.status = 'failed'
                run.error_message = f"precheck 未通过: {st.get('desc')}"
                run.step_results = step_results
                run.finished_at = timezone.now()
                run.save()
                _audit(inc, 'precheck', run.error_message, 'fail')
                logger.info("[playbook] %s precheck 失败, 零写操作", run_id)
                return {'status': 'failed', 'reason': 'precheck_failed'}

        # 2. steps (写操作)
        run.status = 'executing'
        run.save(update_fields=['status'])
        for st in (pb.steps or []):
            r = _run_step(conn, db_type, st, params)
            step_results.append(dict(r, phase='execute'))
            _audit(inc, st.get('action'), st.get('desc', ''), r['status'], r['sql'])
            if r['status'] == 'fail':
                on_fail = st.get('on_fail', 'abort')
                if on_fail == 'rollback':
                    _do_rollback(conn, db_type, pb, params, step_results)
                    run.status = 'rolled_back'
                    run.error_message = r['error']
                    run.step_results = step_results
                    run.finished_at = timezone.now()
                    run.save()
                    return {'status': 'rolled_back'}
                elif on_fail == 'abort':
                    run.status = 'failed'
                    run.error_message = r['error']
                    run.step_results = step_results
                    run.finished_at = timezone.now()
                    run.save()
                    return {'status': 'failed', 'reason': r['error']}
                # continue: 继续下一步

        # 3. 成功 → verifying, 发 verify 消息
        run.status = 'verifying'
        run.step_results = step_results
        run.save(update_fields=['status', 'step_results'])
        try:
            if inc.status == 'executing':
                inc.transition('verifying', 'playbook', '进入验证')
        except IncidentStateError:
            pass

        _emit_verify(run, pb)
        logger.info("[playbook] %s 执行完成, 进入验证", run_id)
        return {'status': 'verifying', 'steps': len(step_results)}

    except Exception as e:
        run.status = 'failed'
        run.error_message = str(e)[:500]
        run.step_results = step_results
        run.finished_at = timezone.now()
        run.save()
        logger.error("[playbook] %s 执行异常: %s", run_id, e)
        return {'status': 'failed', 'reason': str(e)}
    finally:
        if conn:
            try:
                conn.close()
            except Exception:
                pass
        try:
            from django.db import connection as dj_conn
            dj_conn.close()
        except Exception:
            pass


def _do_rollback(conn, db_type, pb, params, step_results):
    for st in (pb.rollback or []):
        r = _run_step(conn, db_type, st, params)
        step_results.append(dict(r, phase='rollback'))


def _emit_verify(run, pb):
    """发 verify 消息给 cg_verify (6C-03)。"""
    from monitor.redis_bus import emit_verify
    v = pb.verify or {}
    if not v.get('metric'):
        # 无验证判据(如纯建议类) → 直接标成功并解决事故
        _finish_no_verify(run)
        return
    emit_verify({
        'incident_id': run.incident.incident_id, 'playbook_run_id': run.run_id,
        'verify_metric': v.get('metric'), 'recover_expr': v.get('recover_expr'),
        'window_sec': v.get('window_sec', 300),
        'check_interval_sec': v.get('check_interval_sec', 15),
        'min_stable_checks': v.get('min_stable_checks', 3),
        'data_source': v.get('data_source', 'collector'),
        'started_at': timezone.now().isoformat(),
    })


def _finish_no_verify(run):
    """无验证判据的剧本: 直接成功(不改事故状态, 由人工决定)。"""
    run.status = 'succeeded'
    run.verify_result = {'recovered': None, 'note': '无验证判据(建议类), 需人工确认'}
    run.finished_at = timezone.now()
    run.save(update_fields=['status', 'verify_result', 'finished_at'])
