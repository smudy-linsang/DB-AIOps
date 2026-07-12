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


# ---- 逐库 ASH 采样 SQL (phase6/10 §2.2) ----
def _ash_mysql_family(cur):
    """MySQL/TDSQL/GBase: processlist + 阻塞关系。cur 为 DictCursor。"""
    cur.execute(
        "SELECT id AS session_id, user AS user_name, host AS client_host, db AS db_name, "
        "command, state, time AS active_secs, LEFT(COALESCE(info,''),200) AS sql_text "
        "FROM information_schema.processlist WHERE command NOT IN ('Sleep','Daemon')")
    rows = {str(r['session_id']): {
        'session_id': str(r['session_id']), 'user_name': r.get('user_name'),
        'client_host': r.get('client_host'), 'db_name': r.get('db_name'),
        'command': r.get('command'), 'state': r.get('state'),
        'wait_event': r.get('state'), 'active_secs': r.get('active_secs') or 0,
        'is_blocked': False, 'blocker_id': None, 'sql_digest': None,
        'sql_text': r.get('sql_text'),
    } for r in cur.fetchall()}
    # 阻塞关系
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
    return list(rows.values())


def _ash_pg(cur):
    cur.execute(
        "SELECT pid AS session_id, usename AS user_name, client_addr AS client_host, "
        "datname AS db_name, state AS command, wait_event AS wait_event, "
        "EXTRACT(EPOCH FROM (now()-query_start))::int AS active_secs, LEFT(query,200) AS sql_text, "
        "(pg_blocking_pids(pid))[1] AS blocker_id, "
        "cardinality(pg_blocking_pids(pid))>0 AS is_blocked "
        "FROM pg_stat_activity WHERE state IS NOT NULL AND state<>'idle'")
    cols = [d[0] for d in cur.description]
    out = []
    for row in cur.fetchall():
        r = dict(zip(cols, row))
        out.append({
            'session_id': str(r.get('session_id')), 'user_name': r.get('user_name'),
            'client_host': str(r.get('client_host') or ''), 'db_name': r.get('db_name'),
            'command': r.get('command'), 'state': r.get('command'),
            'wait_event': r.get('wait_event'), 'active_secs': r.get('active_secs') or 0,
            'is_blocked': bool(r.get('is_blocked')),
            'blocker_id': str(r.get('blocker_id')) if r.get('blocker_id') else None,
            'sql_digest': None, 'sql_text': r.get('sql_text'),
        })
    return out


def _ash_oracle(cur):
    cur.execute(
        "SELECT s.sid||','||s.serial# AS session_id, s.username AS user_name, "
        "s.machine AS client_host, s.status AS command, s.event AS wait_event, "
        "s.last_call_et AS active_secs, s.blocking_session AS blocker_id, "
        "CASE WHEN s.blocking_session IS NOT NULL THEN 1 ELSE 0 END AS is_blocked "
        "FROM v$session s WHERE s.type='USER' AND s.status='ACTIVE'")
    cols = [d[0].lower() for d in cur.description]
    out = []
    for row in cur.fetchall():
        r = dict(zip(cols, row))
        out.append({
            'session_id': str(r.get('session_id')), 'user_name': r.get('user_name'),
            'client_host': r.get('client_host'), 'db_name': None,
            'command': r.get('command'), 'state': r.get('command'),
            'wait_event': r.get('wait_event'), 'active_secs': r.get('active_secs') or 0,
            'is_blocked': bool(r.get('is_blocked')),
            'blocker_id': str(r.get('blocker_id')) if r.get('blocker_id') else None,
            'sql_digest': None, 'sql_text': None,
        })
    return out


def sample_sessions(cur, db_type: str) -> list:
    if db_type in ('mysql', 'tdsql', 'gbase'):
        return _ash_mysql_family(cur)
    if db_type in ('pgsql', 'postgresql'):
        return _ash_pg(cur)
    if db_type in ('oracle', 'dm'):
        return _ash_oracle(cur)
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

    def ash_sample(self):
        """一次 ASH 采样 → 写 session_sample + 即时阻塞检测。"""
        if not getattr(settings, 'ASH_ENABLED', True):
            return
        if self.conn is None:
            return  # 连接不可用时跳过(探活会重建)
        try:
            cur = self._cursor()
            rows = sample_sessions(cur, self.db_type)
            cur.close()
        except Exception as e:
            logger.debug("ASH 采样失败 %s: %s", self.config.name, e)
            return
        if not rows:
            return
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
        interval = int(getattr(settings, 'SENTINEL_INTERVAL_SEC', 8))
        ash_interval = int(getattr(settings, 'ASH_INTERVAL_SEC', 15))
        while not self._stop.is_set():
            close_old_connections()
            self.probe()
            now = time.time()
            if now - self.last_ash_at >= ash_interval:
                self.ash_sample()
                self.last_ash_at = now
            try:
                dj_conn.close()
            except Exception:
                pass
            self._stop.wait(interval)
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
