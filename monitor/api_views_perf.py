# -*- coding: utf-8 -*-
"""
Phase 7B: 性能中心 API (phase7/20)。九端点, 响应字段名为前后端唯一契约源。

- 历史聚合: TimescaleDB session_ash_1m / session_sample / sql_stat
- 实时直连: DbConnector 只读, 单请求单连接用完即关, 失败 degraded 降级
- 唯一写操作 kill 会话走 AuditLog 审批执行链 (7B-07)
"""
import json
import logging
from datetime import timedelta

from django.db.models import Count
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt

from monitor.auth import require_auth
from monitor.models import DatabaseConfig, Incident, SqlPlan

logger = logging.getLogger(__name__)

_WINDOW_MAP = {'30m': 1800, '1h': 3600, '6h': 21600, '24h': 86400, '7d': 604800}

# ash-facets 过滤列白名单 (phase7/20 §4)
_FACET_COLS = ('wait_class', 'user_name', 'db_name', 'sql_digest', 'wait_event',
               'module', 'program', 'client_host', 'lock_object')


def _json_default(obj):
    if hasattr(obj, 'isoformat'):
        return obj.isoformat()
    return str(obj)


def _parse_range(request):
    """from/to ISO 优先, 否则 window=30m|1h|6h|24h|7d (默认1h)。返回 (from,to)。"""
    f, t = request.GET.get('from'), request.GET.get('to')
    if f and t:
        fd, td = parse_datetime(f), parse_datetime(t)
        if fd and td and fd < td:
            return fd, td
    sec = _WINDOW_MAP.get(request.GET.get('window', '1h'), 3600)
    now = timezone.now()
    return now - timedelta(seconds=sec), now


def _bucket_sec(fd, td) -> int:
    span = (td - fd).total_seconds()
    if span <= 2 * 3600:
        return 60
    if span <= 24 * 3600:
        return 300
    return 1800


def _ts_cursor():
    from monitor.timeseries import get_timeseries_storage
    conn = get_timeseries_storage()._get_connection()
    return conn.cursor() if conn else None


class PerfBaseView(View):
    @method_decorator(csrf_exempt)
    @method_decorator(require_auth)
    def dispatch(self, *a, **k):
        return super().dispatch(*a, **k)

    def json_response(self, data, status=200):
        from django.http import JsonResponse
        return JsonResponse(data, status=status,
                            json_dumps_params={'default': _json_default})

    def ok(self, data):
        return self.json_response({'code': 'OK', 'data': data})

    def err(self, code, message, status=400):
        return self.json_response({'code': code, 'message': message}, status=status)

    def get_config(self, config_id):
        return DatabaseConfig.objects.filter(id=config_id).first()

    def live_conn(self, config):
        """实时直连 (只读, 3s 语句超时由各端点自行遵守预算)。失败返回 None。"""
        from monitor.db_connector import DbConnector
        try:
            return DbConnector.get_connection(config)
        except Exception as e:
            logger.debug("[perf] 直连失败 %s: %s", config.id, e)
            return None


# ============================================================
# 7B-02a AAS 时间线
# ============================================================
class AasView(PerfBaseView):
    def get(self, request, config_id):
        cfg = self.get_config(config_id)
        if not cfg:
            return self.err('NOT_FOUND', '实例不存在', 404)
        fd, td = _parse_range(request)
        bucket = _bucket_sec(fd, td)
        by = request.GET.get('by', 'wait_class')
        top = int(request.GET.get('top', 8))
        if by not in ('wait_class', 'user_name', 'db_name', 'sql_digest'):
            return self.err('BAD_PARAM', f'不支持的维度 {by}')
        cur = _ts_cursor()
        if not cur:
            return self.err('TSDB_DOWN', '时序库不可用', 502)
        try:
            cur.execute(
                f"SELECT time_bucket(%s::interval, bucket) AS t, {by}, SUM(active_sec) "
                f"FROM session_ash_1m WHERE db_config_id=%s AND bucket >= %s AND bucket < %s "
                f"GROUP BY 1,2 ORDER BY 1",
                (f'{bucket} seconds', cfg.id, fd, td))
            rows = cur.fetchall()
        finally:
            cur.close()
        # 非等待类维度: TopN 外合并 (other)
        series_map = {}
        totals_by_key = {}
        for t, key, active in rows:
            key = key or '(null)'
            totals_by_key[key] = totals_by_key.get(key, 0) + (active or 0)
        keep = set(totals_by_key)
        if by != 'wait_class' and len(totals_by_key) > top:
            keep = {k for k, _ in sorted(totals_by_key.items(),
                                         key=lambda kv: -kv[1])[:top]}
        bucket_total = {}
        for t, key, active in rows:
            key = key or '(null)'
            k2 = key if key in keep else '(other)'
            pts = series_map.setdefault(k2, {})
            pts[t] = pts.get(t, 0) + (active or 0)
            bucket_total[t] = bucket_total.get(t, 0) + (active or 0)
        series = [{'key': k,
                   'points': [[t.isoformat(), round(v / bucket, 3)]
                              for t, v in sorted(pts.items())]}
                  for k, pts in series_map.items()]
        db_time = sum(totals_by_key.values())
        window_sec = max((td - fd).total_seconds(), 1)
        return self.ok({
            'bucket_sec': bucket, 'cpu_cores': cfg.cpu_cores,
            'series': series,
            'totals': {'db_time_sec': round(db_time, 1),
                       'avg_aas': round(db_time / window_sec, 2),
                       'max_aas': round(max(bucket_total.values()) / bucket, 2)
                                  if bucket_total else 0.0},
        })


# ============================================================
# 7B-02b 顶级活动
# ============================================================
_DIM_MAP_1M = {'sql': 'sql_digest', 'user': 'user_name', 'db': 'db_name'}
_DIM_MAP_RAW = {'session': 'session_id', 'module': 'module',
                'wait_event': 'wait_event', 'object': 'lock_object'}


class TopActivityView(PerfBaseView):
    def get(self, request, config_id):
        cfg = self.get_config(config_id)
        if not cfg:
            return self.err('NOT_FOUND', '实例不存在', 404)
        fd, td = _parse_range(request)
        dim = request.GET.get('dim', 'sql')
        limit = int(request.GET.get('limit', 10))
        if dim not in {**_DIM_MAP_1M, **_DIM_MAP_RAW}:
            return self.err('BAD_PARAM', f'不支持的维度 {dim}')
        cur = _ts_cursor()
        if not cur:
            return self.err('TSDB_DOWN', '时序库不可用', 502)
        try:
            if dim in _DIM_MAP_1M:
                col = _DIM_MAP_1M[dim]
                cur.execute(
                    f"SELECT COALESCE(NULLIF({col},''),'(null)'), wait_class, SUM(active_sec) "
                    f"FROM session_ash_1m WHERE db_config_id=%s AND bucket >= %s AND bucket < %s "
                    f"GROUP BY 1,2", (cfg.id, fd, td))
            else:
                col = _DIM_MAP_RAW[dim]
                cur.execute(
                    f"SELECT COALESCE({col},'(null)'), COALESCE(wait_class,'other'), "
                    f"SUM(COALESCE(sample_gap_sec,15)) "
                    f"FROM session_sample WHERE db_config_id=%s AND time >= %s AND time < %s "
                    f"GROUP BY 1,2", (cfg.id, fd, td))
            agg = {}
            for key, wc, active in cur.fetchall():
                d = agg.setdefault(key, {'total': 0, 'breakdown': {}})
                d['total'] += active or 0
                d['breakdown'][wc] = d['breakdown'].get(wc, 0) + (active or 0)
            total_all = sum(d['total'] for d in agg.values()) or 1
            top_keys = sorted(agg.items(), key=lambda kv: -kv[1]['total'])[:limit]
            rows = []
            for key, d in top_keys:
                row = {'key': key, 'active_sec': round(d['total'], 1),
                       'pct': round(d['total'] * 100.0 / total_all, 1),
                       'breakdown': {k: round(v, 1) for k, v in d['breakdown'].items()}}
                rows.append(row)
            # sql 维度附加信息
            if dim == 'sql':
                digests = [r['key'] for r in rows if r['key'] not in ('(null)',)]
                extra = {}
                if digests:
                    cur.execute(
                        "SELECT DISTINCT ON (sql_digest) sql_digest, sql_text FROM session_sample "
                        "WHERE db_config_id=%s AND sql_digest = ANY(%s) AND sql_text IS NOT NULL "
                        "ORDER BY sql_digest, time DESC", (cfg.id, digests))
                    extra = {d: t for d, t in cur.fetchall()}
                    cur.execute(
                        "SELECT sql_digest, COUNT(DISTINCT session_id) FROM session_sample "
                        "WHERE db_config_id=%s AND sql_digest = ANY(%s) AND time >= %s AND time < %s "
                        "GROUP BY 1", (cfg.id, digests, fd, td))
                    sess = dict(cur.fetchall())
                    plans = {p['sql_digest']: p['n'] for p in
                             SqlPlan.objects.filter(config=cfg, sql_digest__in=digests)
                             .values('sql_digest').annotate(n=Count('id'))}
                    for r in rows:
                        r['sql_text'] = extra.get(r['key'])
                        r['sessions'] = sess.get(r['key'], 0)
                        r['plan_count'] = plans.get(r['key'], 0)
            elif dim == 'session':
                sids = [r['key'] for r in rows]
                if sids:
                    cur.execute(
                        "SELECT DISTINCT ON (session_id) session_id, user_name, program, sql_text "
                        "FROM session_sample WHERE db_config_id=%s AND session_id = ANY(%s) "
                        "ORDER BY session_id, time DESC", (cfg.id, sids))
                    info = {s: (u, p, q) for s, u, p, q in cur.fetchall()}
                    for r in rows:
                        u, p, q = info.get(r['key'], (None, None, None))
                        r.update({'user_name': u, 'program': p, 'sql_text': q})
        finally:
            cur.close()
        return self.ok({'window_sec': int((td - fd).total_seconds()),
                        'total_active_sec': round(total_all, 1), 'rows': rows})


# ============================================================
# 7B-02c ASH 分面切片
# ============================================================
class AshFacetsView(PerfBaseView):
    def get(self, request, config_id):
        cfg = self.get_config(config_id)
        if not cfg:
            return self.err('NOT_FOUND', '实例不存在', 404)
        fd, td = _parse_range(request)
        if (timezone.now() - fd).days > 7:
            return self.err('40001', '明细已过保留期(7天), 请缩小窗口')
        dims = [d for d in request.GET.get('dims', 'sql_digest,wait_event').split(',')
                if d in _FACET_COLS]
        filters, where, params = [], [], []
        for kv in filter(None, request.GET.get('filters', '').split(',')):
            if ':' not in kv:
                continue
            col, val = kv.split(':', 1)
            if col in _FACET_COLS:
                where.append(f"{col} = %s")
                params.append(val)
                filters.append((col, val))
        wsql = (' AND ' + ' AND '.join(where)) if where else ''
        limit = min(int(request.GET.get('limit', 50)), 200)
        cur = _ts_cursor()
        if not cur:
            return self.err('TSDB_DOWN', '时序库不可用', 502)
        try:
            base = (f"FROM session_sample WHERE db_config_id=%s "
                    f"AND time >= %s AND time < %s{wsql}")
            args = [cfg.id, fd, td] + params
            cur.execute(f"SELECT COALESCE(SUM(COALESCE(sample_gap_sec,15)),0) {base}", args)
            matched = float(cur.fetchone()[0] or 0)
            facets = {}
            for d in dims:
                cur.execute(
                    f"SELECT COALESCE({d}::text,'(null)'), SUM(COALESCE(sample_gap_sec,15)) "
                    f"{base} GROUP BY 1 ORDER BY 2 DESC LIMIT 15", args)
                facets[d] = [{'value': v, 'active_sec': round(a, 1),
                              'pct': round(a * 100.0 / matched, 1) if matched else 0}
                             for v, a in cur.fetchall()]
            cur.execute(
                f"SELECT time_bucket('60 seconds', time), SUM(COALESCE(sample_gap_sec,15)) "
                f"{base} GROUP BY 1 ORDER BY 1", args)
            timeline = [{'t': t.isoformat(), 'aas': round(a / 60.0, 3)}
                        for t, a in cur.fetchall()]
            cur.execute(
                f"SELECT time, session_id, user_name, db_name, wait_class, wait_event, "
                f"sql_digest, sql_text, lock_object, active_secs "
                f"{base} ORDER BY time DESC LIMIT {limit}", args)
            cols = ['time', 'session_id', 'user_name', 'db_name', 'wait_class',
                    'wait_event', 'sql_digest', 'sql_text', 'lock_object', 'active_secs']
            rows = [dict(zip(cols, r)) for r in cur.fetchall()]
        finally:
            cur.close()
        return self.ok({'matched_active_sec': round(matched, 1),
                        'filters': [{'col': c, 'value': v} for c, v in filters],
                        'facets': facets, 'timeline': timeline, 'rows': rows})


# ============================================================
# 7B-03a 实时会话
# ============================================================
class SessionsLiveView(PerfBaseView):
    def get(self, request, config_id):
        cfg = self.get_config(config_id)
        if not cfg:
            return self.err('NOT_FOUND', '实例不存在', 404)
        conn = self.live_conn(cfg)
        if conn:
            try:
                from monitor.sentinel import sample_sessions
                cur = conn.cursor()
                rows = sample_sessions(cur, cfg.db_type)
                cur.close()
                for r in rows:
                    r['killable'] = True
                    r.pop('wait_event_type', None)
                    r.pop('raw_wait_class', None)
                return self.ok({'degraded': False,
                                'at': timezone.now().isoformat(), 'sessions': rows})
            except Exception as e:
                logger.debug("[perf] 实时会话失败: %s", e)
            finally:
                try:
                    conn.close()
                except Exception:
                    pass
        # 降级: 最近一次 ASH 样本
        cur = _ts_cursor()
        rows, at = [], None
        if cur:
            try:
                cur.execute(
                    "SELECT MAX(time) FROM session_sample WHERE db_config_id=%s "
                    "AND time > NOW() - interval '120 seconds'", (cfg.id,))
                at = cur.fetchone()[0]
                if at:
                    cur.execute(
                        "SELECT session_id, user_name, client_host, db_name, command, state, "
                        "wait_class, wait_event, active_secs, sql_id, sql_text, is_blocked, "
                        "blocker_id, lock_type, lock_mode, lock_object, program, module "
                        "FROM session_sample WHERE db_config_id=%s AND time=%s", (cfg.id, at))
                    cols = ['session_id', 'user_name', 'client_host', 'db_name', 'command',
                            'state', 'wait_class', 'wait_event', 'active_secs', 'sql_id',
                            'sql_text', 'is_blocked', 'blocker_id', 'lock_type', 'lock_mode',
                            'lock_object', 'program', 'module']
                    rows = [dict(zip(cols, r), killable=False) for r in cur.fetchall()]
            finally:
                cur.close()
        return self.ok({'degraded': True, 'fallback': '目标库直连失败, 显示最近 ASH 样本',
                        'at': at.isoformat() if at else None, 'sessions': rows})


# ============================================================
# 7B-03b 阻塞树 (实时 / 历史回放)
# ============================================================
def _build_blocking_tree(rows):
    nodes = {r['session_id']: dict(r) for r in rows if r.get('session_id')}
    edges = {}
    for r in rows:
        if r.get('is_blocked') and r.get('blocker_id'):
            edges[r['session_id']] = str(r['blocker_id'])
    # blocker 可能不在活跃样本 (Oracle 空闲持锁) → 占位节点
    for waiter, blocker in edges.items():
        if blocker not in nodes:
            # Oracle session_id 形如 sid,serial; blocker_id 只有 sid → 前缀匹配
            hit = next((k for k in nodes if k.split(',')[0] == blocker), None)
            if hit:
                edges[waiter] = hit
            else:
                nodes[blocker] = {'session_id': blocker, 'user_name': None,
                                  'sql_text': None, 'active_secs': None,
                                  'wait_class': None, 'placeholder': True}
    children = {}
    for waiter, blocker in edges.items():
        children.setdefault(blocker, []).append(waiter)

    def build(sid, visited):
        if sid in visited:
            return None  # 环保护
        visited.add(sid)
        n = dict(nodes.get(sid, {'session_id': sid}))
        kids = [build(c, visited) for c in sorted(children.get(sid, []))]
        n['children'] = [k for k in kids if k]
        n['subtree_waiters'] = sum(1 + k['subtree_waiters'] for k in n['children'])
        n['wait_secs'] = n.get('active_secs')
        n['killable'] = not n.get('placeholder', False)
        return n

    roots = sorted(set(edges.values()) - set(edges.keys()))
    visited = set()
    tree = []
    for r in roots:
        t = build(r, visited)
        if t:
            t['role'] = 'root_blocker'
            tree.append(t)
    tree.sort(key=lambda n: -n['subtree_waiters'])
    return tree


class BlockingTreeView(PerfBaseView):
    def get(self, request, config_id):
        cfg = self.get_config(config_id)
        if not cfg:
            return self.err('NOT_FOUND', '实例不存在', 404)
        at_param = request.GET.get('at', 'now')
        if at_param == 'now':
            conn = self.live_conn(cfg)
            if not conn:
                return self.ok({'degraded': True, 'at': None, 'tree': []})
            try:
                from monitor.sentinel import sample_sessions
                cur = conn.cursor()
                rows = sample_sessions(cur, cfg.db_type)
                cur.close()
            finally:
                try:
                    conn.close()
                except Exception:
                    pass
            at = timezone.now()
        else:
            at = parse_datetime(at_param)
            if not at:
                return self.err('BAD_PARAM', 'at 时间格式非法')
            cur = _ts_cursor()
            if not cur:
                return self.err('TSDB_DOWN', '时序库不可用', 502)
            try:
                cur.execute(
                    "SELECT session_id, user_name, db_name, wait_class, wait_event, "
                    "active_secs, is_blocked, blocker_id, sql_text, lock_type, lock_mode, "
                    "lock_object FROM session_sample WHERE db_config_id=%s "
                    "AND time BETWEEN %s AND %s", (cfg.id, at - timedelta(seconds=10), at))
                cols = ['session_id', 'user_name', 'db_name', 'wait_class', 'wait_event',
                        'active_secs', 'is_blocked', 'blocker_id', 'sql_text',
                        'lock_type', 'lock_mode', 'lock_object']
                dedup = {}
                for r in cur.fetchall():
                    d = dict(zip(cols, r))
                    dedup[d['session_id']] = d  # 取窗口内最后一条
                rows = list(dedup.values())
            finally:
                cur.close()
        return self.ok({'at': at.isoformat(), 'tree': _build_blocking_tree(rows)})


# ============================================================
# 7B-04 运行中 SQL (进度)
# ============================================================
class RunningSqlView(PerfBaseView):
    def get(self, request, config_id):
        cfg = self.get_config(config_id)
        if not cfg:
            return self.err('NOT_FOUND', '实例不存在', 404)
        conn = self.live_conn(cfg)
        if not conn:
            return self.ok({'degraded': True, 'rows': []})
        try:
            cur = conn.cursor()
            try:
                if cfg.db_type in ('oracle', 'dm'):
                    rows = self._oracle(cur)
                elif cfg.db_type in ('pgsql', 'postgresql'):
                    rows = self._pg(cur)
                else:
                    rows = self._mysql(cur, cfg.db_type)
            finally:
                cur.close()
            digests = [r['sql_id'] for r in rows if r.get('sql_id')]
            have_plan = set(SqlPlan.objects.filter(config=cfg, sql_digest__in=digests)
                            .values_list('sql_digest', flat=True))
            for r in rows:
                r['plan_available'] = r.get('sql_id') in have_plan
                r['killable'] = True
            return self.ok({'degraded': False, 'rows': rows})
        except Exception as e:
            logger.debug("[perf] running-sql 失败: %s", e)
            return self.ok({'degraded': True, 'rows': []})
        finally:
            try:
                conn.close()
            except Exception:
                pass

    def _oracle(self, cur):
        from monitor.detectors.wait_class import classify
        cur.execute(
            "SELECT s.sid||','||s.serial# AS session_id, s.sql_id, s.username, "
            "s.last_call_et, s.state, s.wait_class, s.event, "
            "o.opname, o.sofar, o.totalwork, o.time_remaining "
            "FROM v$session s LEFT JOIN v$session_longops o "
            "ON o.sid = s.sid AND o.serial# = s.serial# AND o.totalwork > o.sofar "
            "WHERE s.type='USER' AND s.status='ACTIVE' AND s.sql_id IS NOT NULL "
            "AND s.sid <> SYS_CONTEXT('USERENV','SID')")
        raw = cur.fetchall()
        sql_ids = sorted({r[1] for r in raw})
        texts = {}
        if sql_ids:
            in_list = ','.join(f"'{s}'" for s in sql_ids[:50])
            cur.execute(f"SELECT sql_id, SUBSTR(sql_text,1,500) FROM v$sqlstats "
                        f"WHERE sql_id IN ({in_list})")
            texts = dict(cur.fetchall())
        out = []
        for (sid, sql_id, user, elapsed, state, wc, event,
             opname, sofar, total, remain) in raw:
            pct = round(sofar / total * 100, 1) if total else None
            out.append({
                'session_id': str(sid), 'sql_id': sql_id, 'sql_text': texts.get(sql_id),
                'user_name': user, 'elapsed_sec': elapsed or 0,
                'wait_class': classify('oracle', {'state': state, 'raw_wait_class': wc}),
                'phase': opname, 'progress_pct': pct,
                'est_remain_sec': remain})
        return out

    def _pg(self, cur):
        from monitor.detectors.wait_class import classify
        cur.execute(
            "SELECT a.pid, a.query_id, a.usename, LEFT(a.query,500), "
            "EXTRACT(EPOCH FROM (now()-a.query_start))::int, a.state, "
            "a.wait_event_type, p.phase, p.done, p.total FROM pg_stat_activity a "
            "LEFT JOIN ("
            " SELECT pid, phase, heap_blks_scanned::float AS done, heap_blks_total::float AS total FROM pg_stat_progress_vacuum"
            " UNION ALL SELECT pid, phase, blocks_done::float, blocks_total::float FROM pg_stat_progress_create_index"
            " UNION ALL SELECT pid, phase, heap_blks_scanned::float, heap_blks_total::float FROM pg_stat_progress_cluster"
            " UNION ALL SELECT pid, 'analyze', sample_blks_scanned::float, sample_blks_total::float FROM pg_stat_progress_analyze"
            " UNION ALL SELECT pid, 'copy', bytes_processed::float, NULLIF(bytes_total,0)::float FROM pg_stat_progress_copy"
            ") p ON p.pid = a.pid "
            "WHERE a.state='active' AND a.backend_type='client backend' "
            "AND a.pid <> pg_backend_pid()")
        out = []
        from monitor.sqlfingerprint import unified_digest
        for (pid, qid, user, text, elapsed, state, wet, phase, done, total) in cur.fetchall():
            pct = round(done / total * 100, 1) if total else None
            out.append({
                'session_id': str(pid),
                'sql_id': unified_digest('pgsql', str(qid) if qid else None, text),
                'sql_text': text, 'user_name': user, 'elapsed_sec': elapsed or 0,
                'wait_class': classify('pgsql', {'state': state, 'wait_event_type': wet}),
                'phase': phase, 'progress_pct': pct, 'est_remain_sec': None})
        return out

    def _mysql(self, cur, db_type):
        from monitor.detectors.wait_class import classify
        from monitor.sqlfingerprint import unified_digest
        cur.execute(
            "SELECT p.id, p.user, p.state, p.time, LEFT(COALESCE(p.info,''),500) AS info "
            "FROM information_schema.processlist p "
            "WHERE p.command='Query' AND p.id <> CONNECTION_ID() AND p.time >= 1")
        sessions = cur.fetchall()
        prog = {}
        try:
            cur.execute(
                "SELECT t.PROCESSLIST_ID, sc.EVENT_NAME, sc.WORK_COMPLETED, sc.WORK_ESTIMATED "
                "FROM performance_schema.events_stages_current sc "
                "JOIN performance_schema.threads t ON t.THREAD_ID = sc.THREAD_ID "
                "WHERE sc.WORK_ESTIMATED > 0")
            for pid, stage, done, total in cur.fetchall():
                prog[str(pid)] = (stage.split('/')[-1], done, total)
        except Exception:
            pass
        out = []
        for r in sessions:
            pid = str(r['id'])
            stage, done, total = prog.get(pid, (r.get('state'), None, None))
            pct = round(done / total * 100, 1) if total else None
            out.append({
                'session_id': pid,
                'sql_id': unified_digest(db_type, None, r.get('info')),
                'sql_text': r.get('info'), 'user_name': r.get('user'),
                'elapsed_sec': r.get('time') or 0,
                'wait_class': classify(db_type, {'state': r.get('state'), 'command': 'Query'}),
                'phase': stage, 'progress_pct': pct, 'est_remain_sec': None})
        return out


# ============================================================
# 7B-05 SQL 详情 / 计划 / EXPLAIN
# ============================================================
def _raw_sql_text(config_id, digest):
    cur = _ts_cursor()
    if not cur:
        return None
    try:
        cur.execute(
            "SELECT sql_text FROM session_sample WHERE db_config_id=%s AND sql_digest=%s "
            "AND sql_text IS NOT NULL AND sql_text <> '' ORDER BY time DESC LIMIT 1",
            (config_id, digest))
        row = cur.fetchone()
        if row:
            return row[0]
        cur.execute(
            "SELECT sql_text_sample FROM sql_stat WHERE db_config_id=%s AND sql_digest=%s "
            "AND sql_text_sample IS NOT NULL ORDER BY time DESC LIMIT 1", (config_id, digest))
        row = cur.fetchone()
        return row[0] if row else None
    finally:
        cur.close()


class SqlDetailView(PerfBaseView):
    def get(self, request, config_id, digest):
        cfg = self.get_config(config_id)
        if not cfg:
            return self.err('NOT_FOUND', '实例不存在', 404)
        fd, td = _parse_range(request)
        if 'window' not in request.GET and 'from' not in request.GET:
            fd = td - timedelta(hours=24)
        cur = _ts_cursor()
        if not cur:
            return self.err('TSDB_DOWN', '时序库不可用', 502)
        try:
            cur.execute(
                "SELECT time_bucket('300 seconds', time), SUM(exec_delta), "
                "SUM(elapsed_ms_delta), SUM(rows_delta), SUM(reads_delta) "
                "FROM sql_stat WHERE db_config_id=%s AND sql_digest=%s "
                "AND time >= %s AND time < %s GROUP BY 1 ORDER BY 1",
                (cfg.id, digest, fd, td))
            trend = {'exec_delta': [], 'avg_latency_ms': [], 'rows_delta': [], 'reads_delta': []}
            for t, ex, ms, rw, rd in cur.fetchall():
                iso = t.isoformat()
                trend['exec_delta'].append([iso, int(ex or 0)])
                trend['avg_latency_ms'].append(
                    [iso, round((ms or 0) / ex, 2) if ex else None])
                trend['rows_delta'].append([iso, int(rw or 0)])
                trend['reads_delta'].append([iso, int(rd or 0)])
            cur.execute(
                "SELECT time_bucket('300 seconds', bucket), SUM(active_sec) "
                "FROM session_ash_1m WHERE db_config_id=%s AND sql_digest=%s "
                "AND bucket >= %s AND bucket < %s GROUP BY 1 ORDER BY 1",
                (cfg.id, digest, fd, td))
            trend['ash_active_sec'] = [[t.isoformat(), round(a, 1)] for t, a in cur.fetchall()]
            cur.execute(
                "SELECT COALESCE(wait_class,'other'), SUM(active_sec) FROM session_ash_1m "
                "WHERE db_config_id=%s AND sql_digest=%s AND bucket >= %s AND bucket < %s "
                "GROUP BY 1", (cfg.id, digest, fd, td))
            wc_rows = cur.fetchall()
            wc_total = sum(a for _, a in wc_rows) or 1
            breakdown = {w: round(a / wc_total, 3) for w, a in wc_rows}
        finally:
            cur.close()
        sql_text = _raw_sql_text(cfg.id, digest)
        plans_qs = list(SqlPlan.objects.filter(config=cfg, sql_digest=digest)
                        .order_by('-captured_at')[:10])
        plans = [{'plan_hash': p.plan_hash, 'captured_at': p.captured_at,
                  'is_current': p.is_current, 'cost_total': p.cost_total,
                  'source': p.source} for p in plans_qs]
        plan_changed_at = plans_qs[0].captured_at if len(plans_qs) >= 2 else None
        # 优化建议 (规则式, 失败静默)
        suggestions = []
        try:
            from monitor.index_advisor import IndexAdvisor
            import dataclasses
            adv = IndexAdvisor()
            for c in (adv.analyze_queries([{'sql_text': sql_text or '',
                                            'db_type': cfg.db_type}]) or [])[:5]:
                suggestions.append(dataclasses.asdict(c) if dataclasses.is_dataclass(c)
                                   else str(c))
        except Exception:
            pass
        related = []
        for inc in Incident.objects.filter(
                config=cfg, category__in=('performance', 'lock'),
                created_at__gte=timezone.now() - timedelta(days=7))[:30]:
            blob = json.dumps(inc.rca_result, default=str) + json.dumps(inc.plans, default=str)
            if digest[:16] in blob or inc.events.filter(
                    detail__icontains=digest[:16]).exists():
                related.append({'incident_id': inc.incident_id, 'title': inc.title,
                                'status': inc.status, 'priority': inc.priority})
        return self.ok({
            'digest': digest, 'db_type': cfg.db_type, 'sql_text_sample': sql_text,
            'trend': {'bucket_sec': 300, 'series': trend},
            'ash_breakdown': breakdown, 'plans': plans,
            'plan_changed_at': plan_changed_at,
            'advisor': {'index_suggestions': suggestions},
            'related_incidents': related[:5],
        })


class SqlPlanDetailView(PerfBaseView):
    def get(self, request, config_id, digest, plan_hash):
        plan = SqlPlan.objects.filter(config_id=config_id, sql_digest=digest,
                                      plan_hash=plan_hash).order_by('-captured_at').first()
        if not plan:
            return self.err('NOT_FOUND', '计划不存在', 404)
        return self.ok({'plan_hash': plan.plan_hash, 'plan_text': plan.plan_text,
                        'plan_json': plan.plan_json, 'cost_total': plan.cost_total,
                        'captured_at': plan.captured_at, 'source': plan.source})


class SqlExplainView(PerfBaseView):
    def post(self, request, config_id, digest):
        cfg = self.get_config(config_id)
        if not cfg:
            return self.err('NOT_FOUND', '实例不存在', 404)
        try:
            body = json.loads(request.body or '{}')
        except Exception:
            body = {}
        sql_text = body.get('sql_text') or _raw_sql_text(cfg.id, digest)
        if cfg.db_type not in ('oracle', 'dm') and not sql_text:
            return self.err('40002', '无 SQL 原文样例, 无法 EXPLAIN')
        from monitor.plan_capture import capture
        plan = capture(cfg, digest, sql_text=sql_text, source='manual')
        if not plan:
            return self.err('EXPLAIN_FAIL', '计划采集失败(语句不支持或目标库拒绝)', 502)
        return self.ok({'plan_hash': plan.plan_hash, 'plan_text': plan.plan_text,
                        'cost_total': plan.cost_total, 'is_current': plan.is_current,
                        'captured_at': plan.captured_at})


# ============================================================
# 7B-06 期间对比
# ============================================================
def _window_block(cfg, fd, td, cur):
    cur.execute(
        "SELECT COALESCE(wait_class,'other'), SUM(active_sec) FROM session_ash_1m "
        "WHERE db_config_id=%s AND bucket >= %s AND bucket < %s GROUP BY 1",
        (cfg.id, fd, td))
    aas_by_class = {w: round(a, 1) for w, a in cur.fetchall()}
    cur.execute(
        "SELECT COALESCE(NULLIF(sql_digest,''),'(null)'), SUM(active_sec) "
        "FROM session_ash_1m WHERE db_config_id=%s AND bucket >= %s AND bucket < %s "
        "GROUP BY 1 ORDER BY 2 DESC LIMIT 10", (cfg.id, fd, td))
    top_sql = [{'key': k, 'active_sec': round(a, 1)} for k, a in cur.fetchall()]
    top_events = []
    if (timezone.now() - fd).days <= 7:
        cur.execute(
            "SELECT COALESCE(wait_event,'(null)'), SUM(COALESCE(sample_gap_sec,15)) "
            "FROM session_sample WHERE db_config_id=%s AND time >= %s AND time < %s "
            "GROUP BY 1 ORDER BY 2 DESC LIMIT 10", (cfg.id, fd, td))
        top_events = [{'key': k, 'active_sec': round(a, 1)} for k, a in cur.fetchall()]
    db_time = sum(aas_by_class.values())
    win = max((td - fd).total_seconds(), 1)
    return {'aas_by_class': aas_by_class, 'top_sql': top_sql,
            'top_wait_events': top_events,
            'totals': {'db_time_sec': round(db_time, 1),
                       'avg_aas': round(db_time / win, 2)}}


class CompareView(PerfBaseView):
    def get(self, request, config_id):
        cfg = self.get_config(config_id)
        if not cfg:
            return self.err('NOT_FOUND', '实例不存在', 404)
        try:
            a_fd = parse_datetime(request.GET['a_from'])
            a_td = parse_datetime(request.GET['a_to'])
            b_fd = parse_datetime(request.GET['b_from'])
            b_td = parse_datetime(request.GET['b_to'])
            assert all([a_fd, a_td, b_fd, b_td])
        except Exception:
            return self.err('BAD_PARAM', '需要 a_from/a_to/b_from/b_to (ISO8601)')
        cur = _ts_cursor()
        if not cur:
            return self.err('TSDB_DOWN', '时序库不可用', 502)
        try:
            a = _window_block(cfg, a_fd, a_td, cur)
            b = _window_block(cfg, b_fd, b_td, cur)
        finally:
            cur.close()
        a_map = {r['key']: r['active_sec'] for r in a['top_sql']}
        b_map = {r['key']: r['active_sec'] for r in b['top_sql']}
        diff = []
        for k in set(a_map) | set(b_map):
            av, bv = a_map.get(k, 0), b_map.get(k, 0)
            diff.append({'key': k, 'a_active_sec': av, 'b_active_sec': bv,
                         'ratio': round(bv / av, 1) if av else None,
                         'new': k not in a_map, 'gone': k not in b_map})
        diff.sort(key=lambda d: -(d['ratio'] if d['ratio'] is not None else 9999))
        return self.ok({'a': a, 'b': b, 'diff': {'top_sql': diff}})


# ============================================================
# 7B-07 kill 会话 (走 AuditLog 审批执行链, 唯一写操作)
# ============================================================
_KILL_SQL = {
    'mysql': "KILL {sid}", 'tdsql': "KILL {sid}", 'gbase': "KILL {sid}",
    'pgsql': "SELECT pg_terminate_backend({sid})",
    'oracle': "ALTER SYSTEM KILL SESSION '{sid}' IMMEDIATE",
    'dm': "ALTER SYSTEM KILL SESSION '{sid}' IMMEDIATE",
}


class SessionKillView(PerfBaseView):
    def post(self, request, config_id, session_id):
        cfg = self.get_config(config_id)
        if not cfg:
            return self.err('NOT_FOUND', '实例不存在', 404)
        tmpl = _KILL_SQL.get(cfg.db_type)
        if not tmpl:
            return self.err('BAD_PARAM', f'不支持的库类型 {cfg.db_type}')
        # session_id 只允许数字与 "sid,serial" 形态, 防注入
        sid = str(session_id)
        if not all(c.isdigit() or c == ',' for c in sid):
            return self.err('BAD_PARAM', '非法会话ID')
        try:
            body = json.loads(request.body or '{}')
        except Exception:
            body = {}
        reason = (body.get('reason') or '').strip()[:200]
        from monitor.models import AuditLog
        audit = AuditLog.objects.create(
            config=cfg, action_type='KILL_SESSION', risk_level='high',
            status='pending',
            description=f"[性能中心] 申请终止会话 {sid}. 理由: {reason or '未填写'}",
            sql_command=tmpl.format(sid=sid),
            executor=getattr(request.user, 'username', str(request.user)))
        return self.ok({'audit_id': audit.id, 'next': '待审批',
                        'sql_command': audit.sql_command})
