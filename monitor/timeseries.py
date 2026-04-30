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

        table_map = {
            'raw': 'metric_point',
            'hourly': 'metric_hourly',
            'daily': 'metric_daily',
        }
        table = table_map.get(granularity, 'metric_point')

        try:
            cur = conn.cursor()
            cutoff = timezone.now() - timedelta(hours=hours)

            if granularity == 'raw':
                cur.execute(
                    f"SELECT time, value FROM {table} WHERE db_config_id = %s AND metric_key = %s AND time >= %s ORDER BY time",
                    (db_config_id, metric_key, cutoff)
                )
            else:
                cur.execute(
                    f"SELECT bucket, avg_value, min_value, max_value, p95_value FROM {table} WHERE db_config_id = %s AND metric_key = %s AND bucket >= %s ORDER BY bucket",
                    (db_config_id, metric_key, cutoff)
                )

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
