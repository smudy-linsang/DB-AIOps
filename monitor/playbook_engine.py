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

_IDENTIFIER_RE = re.compile(r'^[A-Za-z][A-Za-z0-9_$#]{0,127}$')
_UNSIGNED_INT_RE = re.compile(r'^[0-9]+$')
_ORACLE_SESSION_RE = re.compile(r'^[0-9]+,[0-9]+$')
_LEGACY_PARAM_TYPES = {
    'blocker_id': 'session', 'session_id': 'session',
    'idle_sec': 'integer', 'add_mb': 'integer',
    'tablespace': 'identifier', 'param': 'identifier',
    'old_value': 'literal', 'new_value': 'literal',
}
_PROTECTED_SESSION_USERS = frozenset({
    'root', 'sys', 'system', 'postgres', 'rdsadmin', 'administrator',
})


def _sql_literal(value) -> str:
    if isinstance(value, bool):
        return '1' if value else '0'
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    if value is None:
        return 'NULL'
    return "'" + str(value).replace("'", "''") + "'"


def _normalize_params(schema: dict, supplied: dict, db_type: str) -> dict:
    """把外部参数转换为可安全替换的 SQL 片段；未声明参数不会进入渲染上下文。"""
    if not isinstance(schema, dict) or not isinstance(supplied, dict):
        raise ValueError('Playbook 参数格式必须为对象')
    normalized = {}
    for name, raw_spec in schema.items():
        if not re.fullmatch(r'\w+', str(name)):
            raise ValueError(f'非法参数名: {name}')
        spec = raw_spec if isinstance(raw_spec, dict) else {}
        if name in supplied:
            value = supplied[name]
        elif 'default' in spec:
            value = spec['default']
        elif spec.get('required'):
            raise ValueError(f'缺少必填参数: {name}')
        else:
            continue

        kind = spec.get('type') or _LEGACY_PARAM_TYPES.get(name)
        if kind == 'integer':
            text = str(value)
            if isinstance(value, bool) or not _UNSIGNED_INT_RE.fullmatch(text):
                raise ValueError(f'参数 {name} 必须为非负整数')
            number = int(text)
            minimum = int(spec.get('min', 0))
            maximum = int(spec.get('max', 2_147_483_647))
            if not minimum <= number <= maximum:
                raise ValueError(f'参数 {name} 超出允许范围 {minimum}..{maximum}')
            normalized[name] = str(number)
        elif kind == 'identifier':
            text = str(value)
            if not _IDENTIFIER_RE.fullmatch(text):
                raise ValueError(f'参数 {name} 不是合法数据库标识符')
            normalized[name] = text
        elif kind == 'session':
            text = str(value)
            if db_type == 'oracle' and name == 'blocker_id':
                if not _ORACLE_SESSION_RE.fullmatch(text):
                    raise ValueError('Oracle blocker_id 必须为 sid,serial#')
                normalized[f'{name}_sid'] = text.split(',', 1)[0]
            elif not _UNSIGNED_INT_RE.fullmatch(text):
                raise ValueError(f'参数 {name} 必须为数字会话号')
            normalized[name] = text
        elif kind == 'literal':
            normalized[name] = _sql_literal(value)
        else:
            raise ValueError(f'参数 {name} 未声明受支持的 type')
    return normalized


def _assert_safe_session_target(conn, db_type: str, session_id: str) -> str:
    """从目标库实时解析会话账号；系统账号和无法验证的目标一律拒绝。"""
    if db_type in ('mysql', 'tdsql', 'gbase'):
        sql = f'SELECT user FROM information_schema.processlist WHERE id={session_id}'
    elif db_type in ('pgsql', 'postgresql'):
        sql = f'SELECT usename FROM pg_stat_activity WHERE pid={session_id}'
    elif db_type == 'oracle':
        sid = session_id.split(',', 1)[0]
        sql = f'SELECT username FROM v$session WHERE sid={sid}'
    else:
        raise ValueError(f'{db_type} 尚未实现受保护会话账号校验，拒绝 kill')

    cursor = conn.cursor()
    try:
        cursor.execute(sql)
        row = cursor.fetchone()
    finally:
        cursor.close()
    if not row:
        raise ValueError('目标会话不存在或监控账号不可见')
    owner = next(iter(row.values())) if isinstance(row, dict) else row[0]
    owner = str(owner or '').strip()
    if not owner:
        raise ValueError('无法解析目标会话账号')
    if owner.casefold() in _PROTECTED_SESSION_USERS:
        raise ValueError(f'目标属于受保护账号 {owner}，拒绝执行')
    return owner


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
    render_params = params
    # 兼容 v2.5 之前已落库的 Oracle blocker 前置检查模板：查询只需要 sid，
    # KILL 语句仍必须使用完整 sid,serial#。
    if (db_type == 'oracle' and step.get('action') == 'query'
            and 'v$session' in sql.lower() and 'blocker_id_sid' in params):
        render_params = dict(params, blocker_id=params['blocker_id_sid'])
    return _render(sql, render_params)


def _run_step(conn, db_type: str, step: dict, params: dict) -> dict:
    """执行单步, 返回 step_result dict。"""
    started = timezone.now()
    if step.get('foreach_sql'):
        return _run_foreach_step(conn, db_type, step, params, started)
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


def _run_foreach_step(conn, db_type: str, step: dict, params: dict, started) -> dict:
    """遍历型步骤: foreach_sql 查出目标列表(取每行第一列为 {item}), 逐个执行 sql。

    典型用法: 批量 kill 空闲会话。单项失败不中断, 全部失败才算步骤失败。
    """
    by_db = step.get('foreach_sql_by_db') or {}
    key = {'postgresql': 'pgsql'}.get(db_type, db_type)
    fsql = _render(by_db.get(key) or step.get('foreach_sql') or '', params)
    res = {'seq': step.get('seq'), 'action': step.get('action', 'execute'),
           'desc': step.get('desc', ''), 'sql': fsql,
           'started_at': started.isoformat(), 'status': 'ok',
           'rows_affected': 0, 'output': '', 'error': ''}
    try:
        cur = conn.cursor()
        try:
            cur.execute(fsql)
            rows = cur.fetchall()
        finally:
            cur.close()
        items = []
        for r in rows:
            v = list(r.values())[0] if isinstance(r, dict) else r[0]
            if v is not None:
                items.append(v)
        done, errs = 0, []
        for item in items:
            sql = _pick_sql(step, db_type, dict(params, item=item))
            try:
                cur = conn.cursor()
                try:
                    cur.execute(sql)
                finally:
                    cur.close()
                done += 1
            except Exception as e:
                errs.append(f"{item}: {str(e)[:80]}")
        try:
            conn.commit()
        except Exception:
            pass
        res['rows_affected'] = done
        res['output'] = f"目标 {len(items)} 个, 成功 {done} 个" + \
                        (f"; 失败: {'; '.join(errs[:3])}" if errs else '')
        if items and done == 0:
            res['status'] = 'fail'
            res['error'] = '; '.join(errs[:3])
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
    # Phase 8E 红线: LLM 建议型方案永不进入执行通道 (phase8/30 §7)
    if (run.params or {}).get('scenario') == 'llm_advisory' \
            or str(pb.playbook_id).startswith('LLM-'):
        return {'error': 'llm_advisory 方案为建议型, 禁止执行', 'code': 'FORBIDDEN'}

    inc = run.incident
    config = inc.config
    db_type = config.db_type
    try:
        params = _normalize_params(pb.params_schema or {}, run.params or {}, db_type)
    except ValueError as e:
        run.status = 'failed'
        run.error_message = str(e)
        run.finished_at = timezone.now()
        run.save(update_fields=['status', 'error_message', 'finished_at'])
        _audit(inc, 'parameter_validation', str(e), 'fail')
        return {'status': 'failed', 'reason': 'invalid_parameters'}

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

        # kill 类动作必须以目标库实时账号为准，不能相信浏览器或旧事件中的用户名。
        if pb.playbook_id == 'PB-LOCK-KILL-BLOCKER':
            try:
                owner = _assert_safe_session_target(
                    conn, db_type, params.get('blocker_id', ''))
                step_results.append({
                    'seq': 0, 'phase': 'safety_guard', 'action': 'query',
                    'desc': '受保护会话账号校验', 'status': 'ok',
                    'output': f'目标账号: {owner}', 'rows_affected': 1,
                    'started_at': timezone.now().isoformat(),
                    'finished_at': timezone.now().isoformat(), 'sql': '', 'error': '',
                })
            except ValueError as e:
                run.status = 'failed'
                run.error_message = str(e)
                run.step_results = step_results
                run.finished_at = timezone.now()
                run.save()
                _audit(inc, 'safety_guard', str(e), 'fail')
                return {'status': 'failed', 'reason': 'unsafe_session_target'}

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
        run.step_results = step_results  # 4NF: setter 已写入子表
        run.save(update_fields=['status'])
        try:
            if inc.status == 'executing':
                inc.transition('verifying', 'playbook', '进入验证')
        except IncidentStateError:
            pass

        _emit_verify(run, pb, params)
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


def _emit_verify(run, pb, params=None):
    """发 verify 消息给 cg_verify (6C-03)。metric/recover_expr 支持 {param} 占位符。"""
    from monitor.redis_bus import emit_verify
    v = pb.verify or {}
    params = params or {}
    metric = _render(v.get('metric') or '', params)
    if not metric:
        # 无验证判据(如纯建议类) → 直接标成功并解决事故
        _finish_no_verify(run)
        return
    emit_verify({
        'incident_id': run.incident.incident_id, 'playbook_run_id': run.run_id,
        'verify_metric': metric,
        'recover_expr': _render(v.get('recover_expr') or '', params),
        'window_sec': v.get('window_sec', 300),
        'check_interval_sec': v.get('check_interval_sec', 15),
        'min_stable_checks': v.get('min_stable_checks', 3),
        'data_source': v.get('data_source', 'collector'),
        # 对象级验证目标 (如表空间名)
        'object': params.get('object') or params.get('tablespace') or '',
        'started_at': timezone.now().isoformat(),
    })


def _finish_no_verify(run):
    """无验证判据的剧本: 直接成功(不改事故状态, 由人工决定)。"""
    run.status = 'succeeded'
    run.verify_result = {'recovered': None, 'note': '无验证判据(建议类), 需人工确认'}
    run.finished_at = timezone.now()
    run.save(update_fields=['status', 'verify_result', 'finished_at'])
