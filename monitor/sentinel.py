# -*- coding: utf-8 -*-
"""
Phase 6A: 哨兵 + ASH-lite 采样 (phase6/10 §2.1/§2.2)。

每实例一个采样线程, 每实例独占一个长连接:
- 每 SENTINEL_INTERVAL_SEC: 探活(判 DOWN) + 黄金状态量(触发 L1)
- 每 ASH_INTERVAL_SEC: 会话采样写 session_sample + 即时阻塞检测

由 management command start_sentinel 驱动。
"""
import logging
import threading
import time

from django.conf import settings
from django.db import close_old_connections, connection as dj_conn
from django.utils import timezone

logger = logging.getLogger("monitor.sentinel")


# ---- 逐库 ASH 采样 SQL v2 (phase7/10 §4): 等待类/指纹/锁明细/长事务 ----
from monitor.detectors.wait_class import classify  # noqa: E402
from monitor.sqlfingerprint import unified_digest  # noqa: E402


def _ash_mysql_family(cur, db_type='mysql'):
    """MySQL/TDSQL/GBase: processlist(∪空闲中事务) + digest + 阻塞边 + 锁明细。"""
    # ① 会话主体 (排除采样连接自身, 避免 AAS 恒 +1); 空闲中事务归 application
    cur.execute(
        "SELECT p.id AS session_id, p.user AS user_name, p.host AS client_host, "
        "p.db AS db_name, p.command, p.state, p.time AS active_secs, "
        "LEFT(COALESCE(p.info,''),500) AS sql_text "
        "FROM information_schema.processlist p "
        "WHERE p.command NOT IN ('Sleep','Daemon') AND p.id <> CONNECTION_ID() "
        "UNION ALL "
        "SELECT p.id, p.user, p.host, p.db, p.command, 'idle_in_trx', "
        "TIMESTAMPDIFF(SECOND, t.trx_started, NOW()), LEFT(COALESCE(t.trx_query,''),500) "
        "FROM information_schema.innodb_trx t "
        "JOIN information_schema.processlist p ON p.id = t.trx_mysql_thread_id "
        "WHERE p.command = 'Sleep'")
    rows = {}
    for r in cur.fetchall():
        sid = str(r['session_id'])
        rows[sid] = {
            'session_id': sid, 'user_name': r.get('user_name'),
            'client_host': r.get('client_host'), 'db_name': r.get('db_name'),
            'command': r.get('command'), 'state': r.get('state'),
            'wait_event': r.get('state'), 'active_secs': r.get('active_secs') or 0,
            'is_blocked': False, 'blocker_id': None,
            'sql_text': r.get('sql_text') or None,
            'sql_id': None, 'program': None, 'module': None,
            'lock_type': None, 'lock_mode': None, 'lock_object': None,
        }
    if not rows:
        return []
    # ② 原生 digest
    native = {}
    try:
        cur.execute(
            "SELECT t.PROCESSLIST_ID AS session_id, sc.DIGEST AS digest "
            "FROM performance_schema.events_statements_current sc "
            "JOIN performance_schema.threads t ON t.THREAD_ID = sc.THREAD_ID "
            "WHERE t.PROCESSLIST_ID IS NOT NULL AND sc.DIGEST IS NOT NULL")
        native = {str(r['session_id']): r['digest'] for r in cur.fetchall()}
    except Exception:
        pass
    # ③ 阻塞边
    try:
        cur.execute(
            "SELECT r.trx_mysql_thread_id AS waiter, b.trx_mysql_thread_id AS blocker, "
            "TIMESTAMPDIFF(SECOND,r.trx_started,NOW()) AS wait_secs "
            "FROM performance_schema.data_lock_waits w "
            "JOIN information_schema.innodb_trx b ON w.blocking_engine_transaction_id=b.trx_id "
            "JOIN information_schema.innodb_trx r ON w.requesting_engine_transaction_id=r.trx_id")
        for r in cur.fetchall():
            waiter = str(r['waiter'])
            if waiter in rows:
                rows[waiter]['is_blocked'] = True
                rows[waiter]['blocker_id'] = str(r['blocker'])
                rows[waiter]['active_secs'] = max(rows[waiter]['active_secs'], r.get('wait_secs') or 0)
    except Exception:
        pass
    # ④ 锁明细 (被阻塞行)
    try:
        cur.execute(
            "SELECT r.trx_mysql_thread_id AS waiter, l.LOCK_TYPE AS lock_type, "
            "l.LOCK_MODE AS lock_mode, CONCAT(l.OBJECT_SCHEMA,'.',l.OBJECT_NAME) AS lock_object "
            "FROM performance_schema.data_lock_waits w "
            "JOIN performance_schema.data_locks l ON l.ENGINE_LOCK_ID = w.REQUESTING_ENGINE_LOCK_ID "
            "JOIN information_schema.innodb_trx r ON w.REQUESTING_ENGINE_TRANSACTION_ID = r.trx_id")
        for r in cur.fetchall():
            waiter = str(r['waiter'])
            if waiter in rows:
                rows[waiter].update({'lock_type': r.get('lock_type'),
                                     'lock_mode': r.get('lock_mode'),
                                     'lock_object': r.get('lock_object')})
    except Exception:
        pass
    out = []
    for r in rows.values():
        nd = native.get(r['session_id'])
        r['sql_id'] = (nd or '')[:32] or None
        r['sql_digest'] = unified_digest(db_type, nd, r['sql_text'])
        r['wait_class'] = classify(db_type, r)
        out.append(r)
    return out


def _ash_pg(cur):
    cur.execute(
        "SELECT a.pid AS session_id, a.usename AS user_name, a.client_addr AS client_host, "
        "a.datname AS db_name, a.state AS command, a.state, "
        "a.wait_event_type, a.wait_event, a.application_name AS module, "
        "a.backend_type AS program, a.query_id, "
        "EXTRACT(EPOCH FROM (now()-a.query_start))::int AS active_secs, "
        "LEFT(a.query,500) AS sql_text, "
        "(pg_blocking_pids(a.pid))[1] AS blocker_id, "
        "cardinality(pg_blocking_pids(a.pid))>0 AS is_blocked "
        "FROM pg_stat_activity a "
        "WHERE a.state IS NOT NULL AND a.state<>'idle' "
        "AND a.backend_type='client backend' AND a.pid <> pg_backend_pid()")
    cols = [d[0] for d in cur.description]
    raw_rows = [dict(zip(cols, row)) for row in cur.fetchall()]
    # 锁明细 (被阻塞行)
    lock_detail = {}
    if any(r.get('is_blocked') for r in raw_rows):
        try:
            cur.execute(
                "SELECT l.pid, l.locktype AS lock_type, l.mode AS lock_mode, "
                "COALESCE(n.nspname||'.'||c.relname, l.locktype) AS lock_object "
                "FROM pg_locks l "
                "LEFT JOIN pg_class c ON c.oid = l.relation "
                "LEFT JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE NOT l.granted")
            for pid, lt, lm, lo in cur.fetchall():
                lock_detail[str(pid)] = (lt, lm, lo)
        except Exception:
            pass
    out = []
    for r in raw_rows:
        sid = str(r.get('session_id'))
        qid = r.get('query_id')
        lt, lm, lo = lock_detail.get(sid, (None, None, None))
        row = {
            'session_id': sid, 'user_name': r.get('user_name'),
            'client_host': str(r.get('client_host') or ''), 'db_name': r.get('db_name'),
            'command': r.get('command'), 'state': r.get('state'),
            'wait_event_type': r.get('wait_event_type'),
            'wait_event': r.get('wait_event'), 'active_secs': r.get('active_secs') or 0,
            'is_blocked': bool(r.get('is_blocked')),
            'blocker_id': str(r.get('blocker_id')) if r.get('blocker_id') else None,
            'sql_text': r.get('sql_text'),
            'sql_id': str(qid) if qid not in (None, 0) else None,
            'program': r.get('program'), 'module': r.get('module'),
            'lock_type': lt, 'lock_mode': lm, 'lock_object': lo,
        }
        row['sql_digest'] = unified_digest('pgsql', row['sql_id'], row['sql_text'])
        row['wait_class'] = classify('pgsql', row)
        out.append(row)
    return out


# Oracle row_wait 对象名缓存 (config 无关, object_id 全库唯一即可按连接域缓存)
_ORA_OBJ_CACHE = {}
_ORA_OBJ_CACHE_MAX = 512


def _ash_oracle(cur, db_type='oracle'):
    cur.execute(
        "SELECT s.sid||','||s.serial# AS session_id, s.sql_id, s.username AS user_name, "
        "s.machine AS client_host, s.program, s.module, s.status AS command, "
        "s.state, s.event AS wait_event, s.wait_class AS raw_wait_class, "
        "s.last_call_et AS active_secs, s.blocking_session AS blocker_id, "
        "CASE WHEN s.blocking_session IS NOT NULL THEN 1 ELSE 0 END AS is_blocked, "
        "s.row_wait_obj# AS wait_objno, s.sid AS sid_only "
        "FROM v$session s WHERE s.type='USER' "
        "AND (s.status='ACTIVE' OR s.blocking_session IS NOT NULL) "
        "AND NOT (s.wait_class='Idle' AND s.blocking_session IS NULL) "
        "AND s.sid <> SYS_CONTEXT('USERENV','SID')")
    cols = [d[0].lower() for d in cur.description]
    raw_rows = [dict(zip(cols, row)) for row in cur.fetchall()]
    # 对象名批量补查 + 缓存
    need_obj = sorted({int(r['wait_objno']) for r in raw_rows
                       if r.get('wait_objno') and int(r['wait_objno']) > 0
                       and int(r['wait_objno']) not in _ORA_OBJ_CACHE})
    if need_obj:
        try:
            in_list = ','.join(str(o) for o in need_obj[:50])
            cur.execute(f"SELECT object_id, owner||'.'||object_name FROM dba_objects "
                        f"WHERE object_id IN ({in_list})")
            for oid, name in cur.fetchall():
                if len(_ORA_OBJ_CACHE) >= _ORA_OBJ_CACHE_MAX:
                    _ORA_OBJ_CACHE.clear()
                _ORA_OBJ_CACHE[int(oid)] = name
        except Exception:
            pass
    # 锁类型/模式补查 (被阻塞行)
    lock_detail = {}
    blocked_sids = [int(r['sid_only']) for r in raw_rows if r.get('is_blocked')]
    if blocked_sids:
        try:
            in_list = ','.join(str(s) for s in blocked_sids[:50])
            cur.execute(f"SELECT sid, type, request FROM v$lock "
                        f"WHERE sid IN ({in_list}) AND request > 0")
            for sid, ltype, req in cur.fetchall():
                lock_detail[int(sid)] = (ltype, f"req:{req}")
        except Exception:
            pass
    out = []
    for r in raw_rows:
        objno = int(r['wait_objno']) if r.get('wait_objno') else 0
        lt, lm = lock_detail.get(int(r['sid_only']), (None, None))
        row = {
            'session_id': str(r.get('session_id')), 'user_name': r.get('user_name'),
            'client_host': r.get('client_host'), 'db_name': None,
            'command': r.get('command'), 'state': r.get('state'),
            'raw_wait_class': r.get('raw_wait_class'),
            'wait_event': r.get('wait_event'), 'active_secs': r.get('active_secs') or 0,
            'is_blocked': bool(r.get('is_blocked')),
            'blocker_id': str(r.get('blocker_id')) if r.get('blocker_id') else None,
            'sql_text': None,
            'sql_id': r.get('sql_id'), 'program': r.get('program'), 'module': r.get('module'),
            'lock_type': lt, 'lock_mode': lm,
            'lock_object': _ORA_OBJ_CACHE.get(objno) if objno > 0 else None,
        }
        row['sql_digest'] = unified_digest(db_type, row['sql_id'], None)
        row['wait_class'] = classify(db_type, row)
        out.append(row)
    return out


def sample_sessions(cur, db_type: str) -> list:
    if db_type in ('mysql', 'tdsql', 'gbase'):
        return _ash_mysql_family(cur, db_type)
    if db_type in ('pgsql', 'postgresql'):
        return _ash_pg(cur)
    if db_type in ('oracle', 'dm'):
        return _ash_oracle(cur, db_type)
    return []


# ---- 黄金状态量 (轻量; 仅用于 L1 conn_high 兜底) ----
def golden_metrics(cur, db_type: str) -> dict:
    m = {}
    try:
        if db_type in ('mysql', 'tdsql', 'gbase'):
            cur.execute("SHOW GLOBAL STATUS LIKE 'Threads_connected'")
            r = cur.fetchone()
            tc = int(r['Value']) if r else 0
            cur.execute("SHOW VARIABLES LIKE 'max_connections'")
            r = cur.fetchone()
            mc = int(r['Value']) if r else 0
            m['threads_connected'] = tc
            m['max_connections'] = mc
            m['conn_usage_pct'] = round(tc / mc * 100, 2) if mc else 0
        elif db_type in ('pgsql', 'postgresql'):
            cur.execute("SELECT count(*) FROM pg_stat_activity")
            tc = cur.fetchone()[0]
            cur.execute("SHOW max_connections")
            mc = int(cur.fetchone()[0])
            m['threads_connected'] = tc
            m['max_connections'] = mc
            m['conn_usage_pct'] = round(tc / mc * 100, 2) if mc else 0
        elif db_type in ('oracle', 'dm'):
            cur.execute("SELECT count(*) FROM v$session WHERE type='USER'")
            tc = cur.fetchone()[0]
            cur.execute("SELECT value FROM v$parameter WHERE name='sessions'")
            row = cur.fetchone()
            mc = int(row[0]) if row and row[0] else 0
            m['threads_connected'] = tc
            m['max_connections'] = mc
            m['conn_usage_pct'] = round(tc / mc * 100, 2) if mc else 0
    except Exception as e:
        logger.debug("golden_metrics(%s) 部分失败: %s", db_type, e)
    return m


class InstanceSentinel:
    """单实例哨兵线程逻辑。"""

    def __init__(self, config):
        self.config = config
        self.db_type = config.db_type.lower()
        self.conn = None
        self.consecutive_fail = 0
        self.first_fail_at = None
        self.down_reported = False
        self.last_ash_at = 0.0
        self._stop = threading.Event()
        # 7A-05: ASH 背压降频 (采样过慢时间隔翻倍, 恢复后逐步回落)
        self.ash_interval_cfg = int(getattr(settings, 'ASH_INTERVAL_SEC', 5))
        self.ash_interval_eff = self.ash_interval_cfg
        self._ash_slow_streak = 0
        self._ash_fast_streak = 0

    def stop(self):
        self._stop.set()

    def _connect(self):
        from monitor.db_connector import DbConnector
        self.conn = DbConnector.get_connection(self.config)
        return self.conn

    def _cursor(self):
        return self.conn.cursor()

    def _ping(self):
        cur = self._cursor()
        try:
            cur.execute("SELECT 1 FROM DUAL" if self.db_type in ('oracle', 'dm') else "SELECT 1")
            cur.fetchone()
        finally:
            try:
                cur.close()
            except Exception:
                pass

    def _recent_collect_success(self, within_sec=90) -> bool:
        """双路确认: 最近 within_sec 内是否有成功采集 (MonitorLog status=UP)。"""
        import datetime
        since = timezone.now() - datetime.timedelta(seconds=within_sec)
        try:
            from monitor.models import MonitorLog
            return MonitorLog.objects.filter(
                config=self.config, status='UP', create_time__gte=since).exists()
        except Exception:
            return False

    def _emit_down(self, recovered=False):
        from monitor.redis_bus import emit_event
        from monitor.detectors.metric_category import category_for_signal
        emit_event({
            'config_id': self.config.id, 'db_type': self.db_type, 'source': 'sentinel',
            'signal': 'instance_down', 'metric_key': 'instance_down',
            'value': 0 if recovered else 1, 'threshold': 1,
            'severity': 'info' if recovered else 'critical',
            'occurred_at': (self.first_fail_at or timezone.now()).isoformat(),
            'dedup_key': f"{self.config.id}:instance_down",
            'category': category_for_signal('instance_down'),
            'detail': {'recovered': recovered, 'consecutive_fail': self.consecutive_fail},
        })

    def probe(self):
        """一次探活 + 黄金量 → 命中 L1 则发事件。"""
        from monitor.redis_bus import emit_event
        from monitor.detectors.l1_hard import detect_l1
        try:
            if self.conn is None:
                self._connect()
            self._ping()
            # 探活成功
            if self.consecutive_fail > 0 and self.down_reported:
                self._emit_down(recovered=True)  # 恢复
            self.consecutive_fail = 0
            self.first_fail_at = None
            self.down_reported = False
            # 黄金量 → L1 conn_high
            try:
                cur = self._cursor()
                gm = golden_metrics(cur, self.db_type)
                cur.close()
                for e in detect_l1(self.config, gm):
                    if e['signal'] == 'conn_high':
                        e['source'] = 'sentinel'
                        emit_event(e)
            except Exception:
                pass
        except Exception as e:
            self.consecutive_fail += 1
            if self.first_fail_at is None:
                self.first_fail_at = timezone.now()
            try:
                if self.conn:
                    self.conn.close()
            except Exception:
                pass
            self.conn = None
            threshold = int(getattr(settings, 'SENTINEL_FAIL_THRESHOLD', 3))
            if self.consecutive_fail >= threshold and not self.down_reported:
                if not self._recent_collect_success(90):
                    self._emit_down(recovered=False)
                    self.down_reported = True
            logger.debug("哨兵探活失败 %s (fail=%d): %s",
                         self.config.name, self.consecutive_fail, e)

    def _ash_backpressure(self, elapsed: float):
        """7A-05: 连续 2 次采样 >800ms → 间隔×2(上限30s); 恢复 10 次后逐步减半回落。"""
        if elapsed > 0.8:
            self._ash_slow_streak += 1
            self._ash_fast_streak = 0
            if self._ash_slow_streak >= 2 and self.ash_interval_eff < 30:
                self.ash_interval_eff = min(self.ash_interval_eff * 2, 30)
                self._ash_slow_streak = 0
                logger.warning("[ASH背压] %s 采样慢(%.2fs), 间隔升至 %ds",
                               self.config.name, elapsed, self.ash_interval_eff)
                try:
                    from monitor.timeseries import get_timeseries_storage
                    get_timeseries_storage().write_metric(
                        self.config.id, 'ash_backoff', self.ash_interval_eff)
                except Exception:
                    pass
        else:
            self._ash_slow_streak = 0
            if self.ash_interval_eff > self.ash_interval_cfg:
                self._ash_fast_streak += 1
                if self._ash_fast_streak >= 10:
                    self.ash_interval_eff = max(self.ash_interval_cfg,
                                                self.ash_interval_eff // 2)
                    self._ash_fast_streak = 0
                    logger.info("[ASH背压] %s 恢复, 间隔回落至 %ds",
                                self.config.name, self.ash_interval_eff)

    def ash_sample(self):
        """一次 ASH 采样 → 写 session_sample + 即时阻塞检测。"""
        if not getattr(settings, 'ASH_ENABLED', True):
            return
        if self.conn is None:
            return  # 连接不可用时跳过(探活会重建)
        t0 = time.time()
        try:
            cur = self._cursor()
            rows = sample_sessions(cur, self.db_type)
            cur.close()
        except Exception as e:
            logger.debug("ASH 采样失败 %s: %s", self.config.name, e)
            return
        self._ash_backpressure(time.time() - t0)
        if not rows:
            return
        gap = int(self.ash_interval_eff)
        for r in rows:
            r['sample_gap_sec'] = gap
        # 写超表
        try:
            from monitor.timeseries import get_timeseries_storage
            get_timeseries_storage().write_session_samples(self.config.id, self.db_type, rows)
        except Exception as e:
            logger.debug("session_sample 写入失败: %s", e)
        # 即时阻塞检测
        try:
            from monitor.detectors.l1_hard import detect_blocked_from_ash
            from monitor.redis_bus import emit_event
            for e in detect_blocked_from_ash(self.config, rows, timezone.now()):
                emit_event(e)
        except Exception as e:
            logger.debug("阻塞检测失败: %s", e)

    def run_loop(self):
        probe_interval = int(getattr(settings, 'SENTINEL_INTERVAL_SEC', 8))
        last_probe_at = 0.0
        while not self._stop.is_set():
            close_old_connections()
            now = time.time()
            if now - last_probe_at >= probe_interval:
                self.probe()
                last_probe_at = now
            if time.time() - self.last_ash_at >= self.ash_interval_eff:
                self.ash_sample()
                self.last_ash_at = time.time()
            try:
                dj_conn.close()
            except Exception:
                pass
            # 步进 = 两周期的较小者 (ASH 5s < 探活 8s 时以 ASH 为准)
            self._stop.wait(max(1, min(probe_interval, self.ash_interval_eff)))
        try:
            if self.conn:
                self.conn.close()
        except Exception:
            pass


class SentinelManager:
    """管理所有实例哨兵线程, 每 60s 刷新实例列表。"""

    def __init__(self):
        self.sentinels = {}   # config_id -> InstanceSentinel
        self.threads = {}     # config_id -> Thread
        self._stop = threading.Event()

    def _refresh(self):
        from monitor.models import DatabaseConfig
        active = {c.id: c for c in DatabaseConfig.objects.filter(is_active=True)
                  if c.db_type in ('mysql', 'tdsql', 'gbase', 'pgsql', 'oracle', 'dm')}
        # 新增
        for cid, cfg in active.items():
            if cid not in self.sentinels:
                s = InstanceSentinel(cfg)
                t = threading.Thread(target=s.run_loop, name=f"sentinel-{cid}", daemon=True)
                self.sentinels[cid] = s
                self.threads[cid] = t
                t.start()
                logger.info("哨兵启动: %s (%s)", cfg.name, cfg.db_type)
        # 移除停用
        for cid in list(self.sentinels.keys()):
            if cid not in active:
                self.sentinels[cid].stop()
                del self.sentinels[cid]
                self.threads.pop(cid, None)
                logger.info("哨兵停止: config_id=%s", cid)

    def run(self):
        from monitor.redis_bus import ensure_groups
        ensure_groups()
        logger.info("哨兵进程启动 (interval=%ss, ash=%ss)",
                    getattr(settings, 'SENTINEL_INTERVAL_SEC', 8),
                    getattr(settings, 'ASH_INTERVAL_SEC', 15))
        while not self._stop.is_set():
            try:
                close_old_connections()
                self._refresh()
            except Exception as e:
                logger.error("哨兵刷新实例失败: %s", e)
            self._stop.wait(60)

    def stop(self):
        self._stop.set()
        for s in self.sentinels.values():
            s.stop()
