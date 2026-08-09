"""
时序数据存储模块 v2.0

职责：
- TimescaleDB 集成（可选）
- 指标数据写入时序超表
- 聚合查询支持
- 数据保留策略

使用方式：
    1. 设置 TIMESCALEDB_ENABLED=True
    2. 配置 TimescaleDB 连接参数
    3. 运行 init_timeseries 管理命令初始化超表
"""

import json
import logging
import math
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

from django.conf import settings
from django.utils import timezone

logger = logging.getLogger(__name__)


class TimeseriesStorage:
    """时序数据存储管理器

    BUG-101: 原实现是「进程级单例 + 唯一 psycopg2 连接」, 而调用方高度并发
    (每实例一个哨兵线程 + ThreadPoolExecutor 采集 + 每个 Web 请求线程)。
    psycopg2 连接不支持多线程并发执行语句 —— 轻则 "another command is already
    in progress", 重则 A 线程 fetchall() 拿到 B 线程的结果集(甲库图表显示乙库数据)。
    且 `closed` 属性对服务端断开的连接始终为 0, 一旦网络抖动便永不重连。

    现改为 ThreadedConnectionPool: 借还式使用, 坏连接销毁不回池。
    """

    def __init__(self):
        self.enabled = getattr(settings, 'TIMESCALEDB_ENABLED', False)
        self._pool = None
        self._lock = threading.Lock()

    # ---- 连接池 ----
    def _get_pool(self):
        if not self.enabled:
            return None
        if self._pool is None:
            with self._lock:
                if self._pool is None:
                    try:
                        from psycopg2 import pool as pgpool
                        stmt_timeout = int(getattr(
                            settings, 'TIMESCALEDB_STATEMENT_TIMEOUT_MS', 15000))
                        self._pool = pgpool.ThreadedConnectionPool(
                            minconn=1,
                            maxconn=int(getattr(settings, 'TIMESCALEDB_POOL_MAX', 16)),
                            host=getattr(settings, 'TIMESCALEDB_HOST', 'localhost'),
                            port=getattr(settings, 'TIMESCALEDB_PORT', 5432),
                            dbname=getattr(settings, 'TIMESCALEDB_NAME', 'timeseriesdb'),
                            user=getattr(settings, 'TIMESCALEDB_USER', 'postgres'),
                            password=getattr(settings, 'TIMESCALEDB_PASSWORD', ''),
                            connect_timeout=10,
                            options=f'-c statement_timeout={stmt_timeout}',
                        )
                    except Exception as e:
                        logger.error(f"[Timeseries] 连接池创建失败: {e}")
                        self._pool = None
        return self._pool

    @contextmanager
    def connection(self):
        """借出一条连接, 用完归还; 出错的连接直接销毁不回池。"""
        p = self._get_pool()
        if p is None:
            yield None
            return
        conn = None
        try:
            conn = p.getconn()
            conn.autocommit = True
            yield conn
        except Exception:
            if conn is not None:
                try:
                    p.putconn(conn, close=True)   # 坏连接销毁
                except Exception:
                    pass
                conn = None
            raise
        finally:
            if conn is not None:
                try:
                    p.putconn(conn)
                except Exception:
                    pass

    @contextmanager
    def cursor(self):
        """借出一个游标 (时序库不可用时 yield None)。"""
        try:
            with self.connection() as conn:
                if conn is None:
                    yield None
                    return
                cur = conn.cursor()
                try:
                    yield cur
                finally:
                    try:
                        cur.close()
                    except Exception:
                        pass
        except Exception as e:
            logger.error(f"[Timeseries] 获取游标失败: {e}")
            yield None

    def close_pool(self):
        """关闭连接池 (进程退出 / 测试隔离)。"""
        with self._lock:
            p, self._pool = self._pool, None
        if p is not None:
            try:
                p.closeall()
            except Exception:
                pass

    def _get_connection(self):
        """[已弃用] 兼容垫片: 返回池中一条连接但不归还。

        请改用 `with storage.connection() as conn` 或 `with storage.cursor() as cur`。
        保留仅为兼容尚未迁移的外部调用点; 内部代码不再使用。
        """
        p = self._get_pool()
        if p is None:
            return None
        try:
            conn = p.getconn()
            conn.autocommit = True
            return conn
        except Exception as e:
            logger.error(f"[Timeseries] 连接失败: {e}")
            return None

    def init_hypertables(self):
        """初始化 TimescaleDB 超表"""
        with self.cursor() as cur:
            if cur is None:
                logger.warning("[Timeseries] TimescaleDB 未启用或连接失败")
                return False
            return self._init_hypertables_with(cur)

    def _init_hypertables_with(self, cur):
        try:
            # 创建原始指标表
            cur.execute("""
                CREATE TABLE IF NOT EXISTS metric_point (
                    time            TIMESTAMPTZ  NOT NULL,
                    db_config_id    INTEGER      NOT NULL,
                    metric_key      VARCHAR(100) NOT NULL,
                    value           DOUBLE PRECISION,
                    status          VARCHAR(20)  DEFAULT 'normal'
                );
            """)

            # 转换为超表
            try:
                cur.execute("SELECT create_hypertable('metric_point', 'time', chunk_time_interval => INTERVAL '7 days', if_not_exists => TRUE);")
            except Exception as e:
                logger.info(f"[Timeseries] metric_point 超表可能已存在: {e}")

            # 创建索引
            cur.execute("CREATE INDEX IF NOT EXISTS idx_metric_point_config_metric_time ON metric_point (db_config_id, metric_key, time DESC);")

            # 创建采集快照表
            cur.execute("""
                CREATE TABLE IF NOT EXISTS collection_snapshot (
                    time          TIMESTAMPTZ NOT NULL,
                    db_config_id  INTEGER     NOT NULL,
                    status        VARCHAR(10) NOT NULL,
                    raw_data      JSONB,
                    collection_ms INTEGER
                );
            """)

            try:
                cur.execute("SELECT create_hypertable('collection_snapshot', 'time', chunk_time_interval => INTERVAL '1 day', if_not_exists => TRUE);")
            except Exception as e:
                logger.info(f"[Timeseries] collection_snapshot 超表可能已存在: {e}")

            # 创建连续聚合（小时级）
            cur.execute("""
                CREATE MATERIALIZED VIEW IF NOT EXISTS metric_hourly
                WITH (timescaledb.continuous) AS
                SELECT
                    time_bucket('1 hour', time) AS bucket,
                    db_config_id,
                    metric_key,
                    AVG(value) as avg_value,
                    MIN(value) as min_value,
                    MAX(value) as max_value,
                    PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY value) as p95_value,
                    COUNT(*) as sample_count
                FROM metric_point
                GROUP BY bucket, db_config_id, metric_key;
            """)

            # 创建连续聚合（日级）
            cur.execute("""
                CREATE MATERIALIZED VIEW IF NOT EXISTS metric_daily
                WITH (timescaledb.continuous) AS
                SELECT
                    time_bucket('1 day', time) AS bucket,
                    db_config_id,
                    metric_key,
                    AVG(value) as avg_value,
                    MIN(value) as min_value,
                    MAX(value) as max_value,
                    PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY value) as p95_value,
                    COUNT(*) as sample_count
                FROM metric_point
                GROUP BY bucket, db_config_id, metric_key;
            """)

            # 设置数据保留策略
            retention_days = getattr(settings, 'TIMESCALEDB_RETENTION_DAYS', 90)
            try:
                cur.execute(f"SELECT add_retention_policy('metric_point', INTERVAL '{retention_days} days', if_not_exists => TRUE);")
            except Exception as e:
                logger.info(f"[Timeseries] 保留策略可能已存在: {e}")

            # 设置压缩策略
            try:
                cur.execute("ALTER TABLE metric_point SET (timescaledb.compress, timescaledb.compress_segmentby = 'db_config_id, metric_key');")
                cur.execute("SELECT add_compression_policy('metric_point', INTERVAL '7 days', if_not_exists => TRUE);")
            except Exception as e:
                logger.info(f"[Timeseries] 压缩策略可能已存在: {e}")

            # ---- Phase 6A: ASH-lite 会话采样超表 (phase6/10 §1.4) ----
            cur.execute("""
                CREATE TABLE IF NOT EXISTS session_sample (
                    time          TIMESTAMPTZ    NOT NULL,
                    db_config_id  INTEGER        NOT NULL,
                    db_type       VARCHAR(20)    NOT NULL,
                    session_id    VARCHAR(64),
                    user_name     VARCHAR(100),
                    client_host   VARCHAR(120),
                    db_name       VARCHAR(100),
                    command       VARCHAR(40),
                    state         VARCHAR(120),
                    wait_event    VARCHAR(120),
                    active_secs   INTEGER,
                    is_blocked    BOOLEAN DEFAULT FALSE,
                    blocker_id    VARCHAR(64),
                    sql_digest    VARCHAR(64),
                    sql_text      TEXT
                );
            """)
            try:
                cur.execute("SELECT create_hypertable('session_sample', 'time', chunk_time_interval => INTERVAL '1 day', if_not_exists => TRUE);")
            except Exception as e:
                logger.info(f"[Timeseries] session_sample 超表可能已存在: {e}")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_session_sample_config_time ON session_sample (db_config_id, time DESC);")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_session_sample_blocked ON session_sample (db_config_id, is_blocked, time DESC);")
            ash_days = getattr(settings, 'ASH_RETENTION_DAYS', 7)
            try:
                cur.execute(f"SELECT add_retention_policy('session_sample', INTERVAL '{ash_days} days', if_not_exists => TRUE);")
            except Exception as e:
                logger.info(f"[Timeseries] session_sample 保留策略可能已存在: {e}")

            # ---- Phase 7A-01: session_sample v2 增列 (phase7/10 §1) ----
            cur.execute("""
                ALTER TABLE session_sample
                  ADD COLUMN IF NOT EXISTS wait_class     VARCHAR(20),
                  ADD COLUMN IF NOT EXISTS sql_id         VARCHAR(32),
                  ADD COLUMN IF NOT EXISTS program        VARCHAR(120),
                  ADD COLUMN IF NOT EXISTS module         VARCHAR(120),
                  ADD COLUMN IF NOT EXISTS lock_type      VARCHAR(40),
                  ADD COLUMN IF NOT EXISTS lock_mode      VARCHAR(40),
                  ADD COLUMN IF NOT EXISTS lock_object    VARCHAR(200),
                  ADD COLUMN IF NOT EXISTS sample_gap_sec SMALLINT;
            """)
            # BUG-123: 锁等待时长与事务年龄语义分离。原先把 trx_started 算出的
            # 事务年龄塞进 active_secs, 导致"等待秒"系统性虚高(跑了2小时的事务
            # 刚等锁1秒也显示 7200s), DBA 无法判断锁等待的真实紧迫度。
            cur.execute("""
                ALTER TABLE session_sample
                  ADD COLUMN IF NOT EXISTS wait_secs    INTEGER,
                  ADD COLUMN IF NOT EXISTS trx_age_secs INTEGER;
            """)
            cur.execute("CREATE INDEX IF NOT EXISTS idx_ss_config_class_time ON session_sample (db_config_id, wait_class, time DESC);")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_ss_config_digest_time ON session_sample (db_config_id, sql_digest, time DESC);")

            # ---- Phase 7A-06: session_ash_1m 连续聚合 (phase7/10 §7) ----
            try:
                cur.execute("""
                    CREATE MATERIALIZED VIEW IF NOT EXISTS session_ash_1m
                    WITH (timescaledb.continuous) AS
                    SELECT time_bucket('1 minute', time) AS bucket, db_config_id,
                           COALESCE(wait_class,'other') AS wait_class,
                           COALESCE(sql_digest,'')      AS sql_digest,
                           COALESCE(user_name,'')       AS user_name,
                           COALESCE(db_name,'')         AS db_name,
                           SUM(COALESCE(sample_gap_sec,15))::int AS active_sec,
                           COUNT(*) AS samples,
                           SUM(CASE WHEN is_blocked THEN COALESCE(sample_gap_sec,15) ELSE 0 END)::int AS blocked_sec
                    FROM session_sample GROUP BY 1,2,3,4,5,6
                    WITH NO DATA;
                """)
                cur.execute("""
                    SELECT add_continuous_aggregate_policy('session_ash_1m',
                      start_offset => INTERVAL '2 hours', end_offset => INTERVAL '1 minute',
                      schedule_interval => INTERVAL '1 minute', if_not_exists => TRUE);
                """)
                cur.execute("SELECT add_retention_policy('session_ash_1m', INTERVAL '90 days', if_not_exists => TRUE);")
            except Exception as e:
                logger.info(f"[Timeseries] session_ash_1m 可能已存在: {e}")
            try:
                # 实时聚合: 查询时合并未物化的最新数据 (性能中心近实时曲线依赖)
                cur.execute("ALTER MATERIALIZED VIEW session_ash_1m SET (timescaledb.materialized_only = false);")
            except Exception as e:
                logger.info(f"[Timeseries] session_ash_1m 实时模式设置: {e}")

            # ---- Phase 7A-07: sql_stat 超表 (phase7/10 §8) ----
            cur.execute("""
                CREATE TABLE IF NOT EXISTS sql_stat (
                    time             TIMESTAMPTZ  NOT NULL,
                    db_config_id     INTEGER      NOT NULL,
                    sql_digest       VARCHAR(32)  NOT NULL,
                    db_name          VARCHAR(100),
                    exec_delta       BIGINT DEFAULT 0,
                    elapsed_ms_delta BIGINT DEFAULT 0,
                    rows_delta       BIGINT DEFAULT 0,
                    reads_delta      BIGINT DEFAULT 0,
                    sql_text_sample  TEXT
                );
            """)
            try:
                cur.execute("SELECT create_hypertable('sql_stat', 'time', chunk_time_interval => INTERVAL '1 day', if_not_exists => TRUE);")
            except Exception as e:
                logger.info(f"[Timeseries] sql_stat 超表可能已存在: {e}")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_sqlstat_config_digest_time ON sql_stat (db_config_id, sql_digest, time DESC);")
            try:
                cur.execute("SELECT add_retention_policy('sql_stat', INTERVAL '90 days', if_not_exists => TRUE);")
            except Exception as e:
                logger.info(f"[Timeseries] sql_stat 保留策略可能已存在: {e}")

            logger.info("[Timeseries] 超表初始化完成")
            return True

        except Exception as e:
            logger.error(f"[Timeseries] 初始化失败: {e}")
            return False

    @staticmethod
    def _numeric_or_none(value):
        """BUG-128: bool 是 int 子类会被写成 1/0; NaN/Inf 会让 psycopg2 报错。"""
        if value is None or isinstance(value, bool):
            return None
        if not isinstance(value, (int, float)):
            return None
        v = float(value)
        return v if math.isfinite(v) else None

    def write_metric(self, db_config_id: int, metric_key: str, value: float, status: str = 'normal'):
        """写入单个指标点"""
        try:
            with self.cursor() as cur:
                if cur is None:
                    return False
                cur.execute(
                    "INSERT INTO metric_point (time, db_config_id, metric_key, value, status) VALUES (NOW(), %s, %s, %s, %s)",
                    (db_config_id, metric_key, value, status)
                )
                return True
        except Exception as e:
            logger.error(f"[Timeseries] 写入失败: {e}")
            return False

    def write_metrics_batch(self, db_config_id: int, metrics: Dict[str, float], status: str = 'normal'):
        """批量写入指标 (BUG-128: execute_values 单次往返)"""
        now = timezone.now()
        payload = []
        for metric_key, value in (metrics or {}).items():
            v = self._numeric_or_none(value)
            if v is not None:
                payload.append((now, db_config_id, metric_key, v, status))
        if not payload:
            return False
        try:
            from psycopg2.extras import execute_values
            with self.cursor() as cur:
                if cur is None:
                    return False
                execute_values(
                    cur,
                    "INSERT INTO metric_point (time, db_config_id, metric_key, value, status) VALUES %s",
                    payload, page_size=500)
                return True
        except Exception as e:
            logger.error(f"[Timeseries] 批量写入失败: {e}")
            return False

    def write_snapshot(self, db_config_id: int, status: str, raw_data: dict, collection_ms: int = 0):
        """写入采集快照"""
        try:
            with self.cursor() as cur:
                if cur is None:
                    return False
                cur.execute(
                    "INSERT INTO collection_snapshot (time, db_config_id, status, raw_data, collection_ms) VALUES (NOW(), %s, %s, %s, %s)",
                    (db_config_id, status, json.dumps(raw_data, default=str), collection_ms)
                )
                return True
        except Exception as e:
            logger.error(f"[Timeseries] 快照写入失败: {e}")
            return False

    # ---- Phase 6A: ASH-lite 会话样本 (phase6/10 §1.4/§2.2; v2 增列 phase7/10 §1) ----
    # BUG-123: wait_secs(本次锁等待) 与 trx_age_secs(事务年龄) 语义分离
    _SESSION_COLS = ('session_id', 'user_name', 'client_host', 'db_name', 'command',
                     'state', 'wait_event', 'active_secs', 'is_blocked', 'blocker_id',
                     'sql_digest', 'sql_text',
                     'wait_class', 'sql_id', 'program', 'module',
                     'lock_type', 'lock_mode', 'lock_object', 'sample_gap_sec',
                     'wait_secs', 'trx_age_secs')

    def write_session_samples(self, db_config_id: int, db_type: str, rows: list) -> bool:
        """批量写会话样本。rows: list[dict]，键为 _SESSION_COLS 子集; time=now 统一。"""
        if not rows:
            return False
        try:
            from psycopg2.extras import execute_values
            with self.cursor() as cur:
                if cur is None:
                    return False
                now = timezone.now()
                cols = "time, db_config_id, db_type, " + ", ".join(self._SESSION_COLS)
                payload = [
                    [now, db_config_id, db_type] + [r.get(c) for c in self._SESSION_COLS]
                    for r in rows
                ]
                execute_values(cur, f"INSERT INTO session_sample ({cols}) VALUES %s",
                               payload, page_size=500)
                return True
        except Exception as e:
            logger.error(f"[Timeseries] 会话样本写入失败: {e}")
            return False

    # ---- Phase 7A-07: SQL digest 级增量统计 ----
    _SQLSTAT_COLS = ('sql_digest', 'db_name', 'exec_delta', 'elapsed_ms_delta',
                     'rows_delta', 'reads_delta', 'sql_text_sample')

    def write_sql_stats(self, db_config_id: int, rows: list) -> bool:
        """批量写 sql_stat 增量行。rows: list[dict], 键为 _SQLSTAT_COLS 子集。"""
        if not rows:
            return False
        try:
            from psycopg2.extras import execute_values
            with self.cursor() as cur:
                if cur is None:
                    return False
                now = timezone.now()
                cols = "time, db_config_id, " + ", ".join(self._SQLSTAT_COLS)
                payload = [[now, db_config_id] + [r.get(c) for c in self._SQLSTAT_COLS]
                           for r in rows]
                execute_values(cur, f"INSERT INTO sql_stat ({cols}) VALUES %s",
                               payload, page_size=500)
                return True
        except Exception as e:
            logger.error(f"[Timeseries] sql_stat 写入失败: {e}")
            return False

    def query_session_samples(self, db_config_id: int, since, until=None) -> list:
        """查会话样本时间窗 (供 6B ASH 时间线)。返回 list[dict]。"""
        try:
            with self.cursor() as cur:
                if cur is None:
                    return []
                until = until or timezone.now()
                cols = "time, " + ", ".join(self._SESSION_COLS)
                cur.execute(
                    f"SELECT {cols} FROM session_sample WHERE db_config_id=%s AND time BETWEEN %s AND %s ORDER BY time",
                    (db_config_id, since, until))
                names = ['time'] + list(self._SESSION_COLS)
                return [dict(zip(names, row)) for row in cur.fetchall()]
        except Exception as e:
            logger.error(f"[Timeseries] 会话样本查询失败: {e}")
            return []

    def latest_blocked_count(self, db_config_id: int, within_sec: int = 30) -> int:
        """最近 within_sec 内最新一次采样的阻塞会话数 (供 6C 验证回路 blocked_sessions 指标)。"""
        try:
            with self.cursor() as cur:
                if cur is None:
                    return 0
                cur.execute(
                    "SELECT COALESCE(SUM(CASE WHEN is_blocked THEN 1 ELSE 0 END),0) "
                    "FROM session_sample WHERE db_config_id=%s AND time = "
                    "(SELECT MAX(time) FROM session_sample WHERE db_config_id=%s "
                    " AND time > NOW() - (%s || ' seconds')::interval)",
                    (db_config_id, db_config_id, int(within_sec)))
                row = cur.fetchone()
                return int(row[0]) if row and row[0] is not None else 0
        except Exception as e:
            logger.error(f"[Timeseries] 阻塞计数查询失败: {e}")
            return 0

    # SQL 查询模板（使用参数化占位符，表名通过白名单校验）
    _TABLE_WHITELIST = {
        'raw': 'metric_point',
        'hourly': 'metric_hourly',
        'daily': 'metric_daily',
    }

    _RAW_QUERY = "SELECT time, value FROM {table} WHERE db_config_id = %s AND metric_key = %s AND time >= %s ORDER BY time"
    _AGG_QUERY = "SELECT bucket, avg_value, min_value, max_value, p95_value FROM {table} WHERE db_config_id = %s AND metric_key = %s AND bucket >= %s ORDER BY bucket"

    def query_metric_history(
        self,
        db_config_id: int,
        metric_key: str,
        hours: int = 24,
        granularity: str = 'raw'
    ) -> List[Dict]:
        """
        查询指标历史数据

        Args:
            db_config_id: 数据库配置ID
            metric_key: 指标键
            hours: 查询小时数
            granularity: 粒度 (raw/hourly/daily)

        Returns:
            [{'time': ..., 'value': ...}, ...]
        """
        # 白名单校验表名，防止 SQL 注入
        table = self._TABLE_WHITELIST.get(granularity)
        if table is None:
            logger.warning(f"[Timeseries] 未知粒度类型: {granularity}")
            table = self._TABLE_WHITELIST['raw']
            granularity = 'raw'

        try:
            with self.cursor() as cur:
                if cur is None:
                    return []
                cutoff = timezone.now() - timedelta(hours=hours)

                if granularity == 'raw':
                    query = self._RAW_QUERY.format(table=table)
                else:
                    query = self._AGG_QUERY.format(table=table)
                cur.execute(query, (db_config_id, metric_key, cutoff))

                results = []
                for row in cur.fetchall():
                    if granularity == 'raw':
                        results.append({'time': row[0].isoformat(), 'value': row[1]})
                    else:
                        results.append({
                            'time': row[0].isoformat(),
                            'avg': row[1],
                            'min': row[2],
                            'max': row[3],
                            'p95': row[4],
                        })
                return results
        except Exception as e:
            logger.error(f"[Timeseries] 查询失败: {e}")
            return []

    def get_storage_stats(self) -> Dict:
        """获取存储统计信息"""
        if not self.enabled:
            return {'enabled': False}
        try:
            with self.cursor() as cur:
                if cur is None:
                    return {'enabled': False}

                cur.execute("SELECT COUNT(*) FROM metric_point")
                raw_count = cur.fetchone()[0]

                cur.execute("SELECT COUNT(*) FROM collection_snapshot")
                snapshot_count = cur.fetchone()[0]

                cur.execute("SELECT pg_size_pretty(pg_database_size(current_database()))")
                db_size = cur.fetchone()[0]

                return {
                    'enabled': True,
                    'raw_metric_count': raw_count,
                    'snapshot_count': snapshot_count,
                    'database_size': db_size,
                }
        except Exception as e:
            logger.error(f"[Timeseries] 获取统计失败: {e}")
            return {'enabled': True, 'error': str(e)}


# 全局单例
_timeseries_storage = None


_TS_LOCK = threading.Lock()


def get_timeseries_storage() -> TimeseriesStorage:
    """获取时序存储单例"""
    global _timeseries_storage
    if _timeseries_storage is None:
        with _TS_LOCK:
            if _timeseries_storage is None:
                _timeseries_storage = TimeseriesStorage()
    return _timeseries_storage


def reset_timeseries_storage():
    """丢弃单例并关闭连接池 (测试隔离 / 配置变更后重建)。"""
    global _timeseries_storage
    with _TS_LOCK:
        old, _timeseries_storage = _timeseries_storage, None
    if old is not None:
        old.close_pool()


def create_hypertable() -> bool:
    """创建 TimescaleDB 超表（兼容管理命令调用）"""
    storage = get_timeseries_storage()
    return storage.init_hypertables()


# BUG-129: 原实现只删 metric_* / collection_snapshot, 漏掉 Phase 6A/7A 新增的
# session_sample / sql_stat / session_ash_1m。重置后残留旧表, 再 init 时因
# "IF NOT EXISTS" 跳过增列, schema 变成半新半旧。
# 顺序要求: 先删连续聚合(依赖基表), 再删基表。
_DROP_VIEWS = ('session_ash_1m', 'metric_daily', 'metric_hourly')
_DROP_TABLES = ('sql_stat', 'session_sample', 'collection_snapshot', 'metric_point')


def drop_hypertable() -> bool:
    """删除 TimescaleDB 超表（含 Phase 6A/7A 新增对象）"""
    storage = get_timeseries_storage()
    try:
        with storage.cursor() as cur:
            if cur is None:
                logger.warning("[Timeseries] TimescaleDB 未启用或连接失败，无法删除超表")
                return False
            for view_name in _DROP_VIEWS:
                try:
                    cur.execute(f"DROP MATERIALIZED VIEW IF EXISTS {view_name} CASCADE;")
                except Exception as e:
                    logger.info(f"[Timeseries] 删除视图 {view_name} 时出错: {e}")
            for table_name in _DROP_TABLES:
                try:
                    cur.execute(f"DROP TABLE IF EXISTS {table_name} CASCADE;")
                except Exception as e:
                    logger.info(f"[Timeseries] 删除表 {table_name} 时出错: {e}")
            logger.info("[Timeseries] 超表已删除")
            return True
    except Exception as e:
        logger.error(f"[Timeseries] 删除超表失败: {e}")
        return False
