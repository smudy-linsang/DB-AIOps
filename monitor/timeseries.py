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
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

from django.conf import settings
from django.utils import timezone

logger = logging.getLogger(__name__)


class TimeseriesStorage:
    """时序数据存储管理器"""

    def __init__(self):
        self.enabled = getattr(settings, 'TIMESCALEDB_ENABLED', False)
        self._connection = None

    def _get_connection(self):
        """获取 TimescaleDB 连接"""
        if not self.enabled:
            return None

        if self._connection is None or self._connection.closed:
            try:
                import psycopg2
                self._connection = psycopg2.connect(
                    host=getattr(settings, 'TIMESCALEDB_HOST', 'localhost'),
                    port=getattr(settings, 'TIMESCALEDB_PORT', 5432),
                    dbname=getattr(settings, 'TIMESCALEDB_NAME', 'timeseriesdb'),
                    user=getattr(settings, 'TIMESCALEDB_USER', 'postgres'),
                    password=getattr(settings, 'TIMESCALEDB_PASSWORD', ''),
                    connect_timeout=10,
                )
                self._connection.autocommit = True
            except Exception as e:
                logger.error(f"[Timeseries] 连接失败: {e}")
                self._connection = None

        return self._connection

    def init_hypertables(self):
        """初始化 TimescaleDB 超表"""
        conn = self._get_connection()
        if not conn:
            logger.warning("[Timeseries] TimescaleDB 未启用或连接失败")
            return False

        cur = conn.cursor()

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

            logger.info("[Timeseries] 超表初始化完成")
            return True

        except Exception as e:
            logger.error(f"[Timeseries] 初始化失败: {e}")
            return False
        finally:
            cur.close()

    def write_metric(self, db_config_id: int, metric_key: str, value: float, status: str = 'normal'):
        """写入单个指标点"""
        conn = self._get_connection()
        if not conn:
            return False

        try:
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO metric_point (time, db_config_id, metric_key, value, status) VALUES (NOW(), %s, %s, %s, %s)",
                (db_config_id, metric_key, value, status)
            )
            cur.close()
            return True
        except Exception as e:
            logger.error(f"[Timeseries] 写入失败: {e}")
            return False

    def write_metrics_batch(self, db_config_id: int, metrics: Dict[str, float], status: str = 'normal'):
        """批量写入指标"""
        conn = self._get_connection()
        if not conn:
            return False

        try:
            cur = conn.cursor()
            now = timezone.now()
            for metric_key, value in metrics.items():
                if isinstance(value, (int, float)) and value is not None:
                    cur.execute(
                        "INSERT INTO metric_point (time, db_config_id, metric_key, value, status) VALUES (%s, %s, %s, %s, %s)",
                        (now, db_config_id, metric_key, float(value), status)
                    )
            cur.close()
            return True
        except Exception as e:
            logger.error(f"[Timeseries] 批量写入失败: {e}")
            return False

    def write_snapshot(self, db_config_id: int, status: str, raw_data: dict, collection_ms: int = 0):
        """写入采集快照"""
        conn = self._get_connection()
        if not conn:
            return False

        try:
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO collection_snapshot (time, db_config_id, status, raw_data, collection_ms) VALUES (NOW(), %s, %s, %s, %s)",
                (db_config_id, status, json.dumps(raw_data, default=str), collection_ms)
            )
            cur.close()
            return True
        except Exception as e:
            logger.error(f"[Timeseries] 快照写入失败: {e}")
            return False

    # ---- Phase 6A: ASH-lite 会话样本 (phase6/10 §1.4/§2.2) ----
    _SESSION_COLS = ('session_id', 'user_name', 'client_host', 'db_name', 'command',
                     'state', 'wait_event', 'active_secs', 'is_blocked', 'blocker_id',
                     'sql_digest', 'sql_text')

    def write_session_samples(self, db_config_id: int, db_type: str, rows: list) -> bool:
        """批量写会话样本。rows: list[dict]，键为 _SESSION_COLS 子集; time=now 统一。"""
        conn = self._get_connection()
        if not conn or not rows:
            return False
        try:
            cur = conn.cursor()
            now = timezone.now()
            cols = "time, db_config_id, db_type, " + ", ".join(self._SESSION_COLS)
            ph = "%s, %s, %s, " + ", ".join(["%s"] * len(self._SESSION_COLS))
            sql = f"INSERT INTO session_sample ({cols}) VALUES ({ph})"
            for r in rows:
                vals = [now, db_config_id, db_type] + [r.get(c) for c in self._SESSION_COLS]
                cur.execute(sql, vals)
            cur.close()
            return True
        except Exception as e:
            logger.error(f"[Timeseries] 会话样本写入失败: {e}")
            return False

    def query_session_samples(self, db_config_id: int, since, until=None) -> list:
        """查会话样本时间窗 (供 6B ASH 时间线)。返回 list[dict]。"""
        conn = self._get_connection()
        if not conn:
            return []
        try:
            cur = conn.cursor()
            until = until or timezone.now()
            cols = "time, " + ", ".join(self._SESSION_COLS)
            cur.execute(
                f"SELECT {cols} FROM session_sample WHERE db_config_id=%s AND time BETWEEN %s AND %s ORDER BY time",
                (db_config_id, since, until))
            names = ['time'] + list(self._SESSION_COLS)
            out = [dict(zip(names, row)) for row in cur.fetchall()]
            cur.close()
            return out
        except Exception as e:
            logger.error(f"[Timeseries] 会话样本查询失败: {e}")
            return []

    def latest_blocked_count(self, db_config_id: int, within_sec: int = 30) -> int:
        """最近 within_sec 内最新一次采样的阻塞会话数 (供 6C 验证回路 blocked_sessions 指标)。"""
        conn = self._get_connection()
        if not conn:
            return 0
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT COALESCE(SUM(CASE WHEN is_blocked THEN 1 ELSE 0 END),0) "
                "FROM session_sample WHERE db_config_id=%s AND time = "
                "(SELECT MAX(time) FROM session_sample WHERE db_config_id=%s "
                " AND time > NOW() - (%s || ' seconds')::interval)",
                (db_config_id, db_config_id, int(within_sec)))
            row = cur.fetchone()
            cur.close()
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
        conn = self._get_connection()
        if not conn:
            return []

        # 白名单校验表名，防止 SQL 注入
        table = self._TABLE_WHITELIST.get(granularity)
        if table is None:
            logger.warning(f"[Timeseries] 未知粒度类型: {granularity}")
            table = self._TABLE_WHITELIST['raw']

        try:
            cur = conn.cursor()
            cutoff = timezone.now() - timedelta(hours=hours)

            if granularity == 'raw':
                query = self._RAW_QUERY.format(table=table)
                cur.execute(query, (db_config_id, metric_key, cutoff))
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

            cur.close()
            return results
        except Exception as e:
            logger.error(f"[Timeseries] 查询失败: {e}")
            return []

    def get_storage_stats(self) -> Dict:
        """获取存储统计信息"""
        conn = self._get_connection()
        if not conn:
            return {'enabled': False}

        try:
            cur = conn.cursor()

            # 原始数据量
            cur.execute("SELECT COUNT(*) FROM metric_point")
            raw_count = cur.fetchone()[0]

            # 快照数据量
            cur.execute("SELECT COUNT(*) FROM collection_snapshot")
            snapshot_count = cur.fetchone()[0]

            # 数据库大小
            cur.execute("SELECT pg_size_pretty(pg_database_size(current_database()))")
            db_size = cur.fetchone()[0]

            cur.close()

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


def get_timeseries_storage() -> TimeseriesStorage:
    """获取时序存储单例"""
    global _timeseries_storage
    if _timeseries_storage is None:
        _timeseries_storage = TimeseriesStorage()
    return _timeseries_storage


def create_hypertable() -> bool:
    """创建 TimescaleDB 超表（兼容管理命令调用）"""
    storage = get_timeseries_storage()
    return storage.init_hypertables()


def drop_hypertable() -> bool:
    """删除 TimescaleDB 超表"""
    storage = get_timeseries_storage()
    conn = storage._get_connection()
    if not conn:
        logger.warning("[Timeseries] TimescaleDB 未启用或连接失败，无法删除超表")
        return False

    cur = conn.cursor()
    try:
        # 删除连续聚合视图
        for view_name in ('metric_daily', 'metric_hourly'):
            try:
                cur.execute(f"DROP MATERIALIZED VIEW IF EXISTS {view_name} CASCADE;")
            except Exception as e:
                logger.info(f"[Timeseries] 删除视图 {view_name} 时出错: {e}")

        # 删除超表
        for table_name in ('collection_snapshot', 'metric_point'):
            try:
                cur.execute(f"DROP TABLE IF EXISTS {table_name} CASCADE;")
            except Exception as e:
                logger.info(f"[Timeseries] 删除表 {table_name} 时出错: {e}")

        logger.info("[Timeseries] 超表已删除")
        return True
    except Exception as e:
        logger.error(f"[Timeseries] 删除超表失败: {e}")
        return False
    finally:
        cur.close()
