# -*- coding: utf-8 -*-
"""
TimescaleDB Time-Series Storage Module

Provides time-series data storage and retrieval using TimescaleDB.
This module stores监控指标 data in hypertables for efficient
time-range queries and downsampling.

Design Reference: DB_AIOps_DESIGN.md 4.2 节
"""

import json
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass

from django.db import models, connection
from django.conf import settings


@dataclass
class TimeSeriesPoint:
    """A single time-series data point."""
    timestamp: datetime
    metric_name: str
    value: float
    tags: Dict[str, str]
    config_id: int


class TimeSeriesWriter:
    """
    Writer for storing time-series data in TimescaleDB.
    
    Usage:
        writer = TimeSeriesWriter()
        writer.write_point(
            timestamp=datetime.now(),
            metric_name='tablespace_used_pct',
            value=75.5,
            tags={'db_name': 'oracle01'},
            config_id=1
        )
    """

    def __init__(self):
        self.batch_size = 100
        self.batch: List[TimeSeriesPoint] = []

    def write_point(
        self,
        timestamp: datetime,
        metric_name: str,
        value: float,
        tags: Optional[Dict[str, str]] = None,
        config_id: Optional[int] = None
    ) -> bool:
        """
        Write a single data point to TimescaleDB.
        
        Args:
            timestamp: Time of the measurement
            metric_name: Name of the metric (e.g., 'tablespace_used_pct')
            value: Metric value
            tags: Optional tags for the metric (e.g., {'db_name': 'oracle01'})
            config_id: Database config ID
            
        Returns:
            True if successful, False otherwise
        """
        point = TimeSeriesPoint(
            timestamp=timestamp,
            metric_name=metric_name,
            value=value,
            tags=tags or {},
            config_id=config_id or 0
        )
        self.batch.append(point)
        
        if len(self.batch) >= self.batch_size:
            return self.flush()
        return True

    def write_batch(self, points: List[TimeSeriesPoint]) -> bool:
        """
        Write multiple data points at once.
        
        Args:
            points: List of TimeSeriesPoint objects
            
        Returns:
            True if successful, False otherwise
        """
        if not points:
            return True
            
        try:
            with connection.cursor() as cursor:
                values = []
                for point in points:
                    tags_json = json.dumps(point.tags)
                    values.append(
                        f"({point.config_id}, '{point.metric_name}', {point.value}, '{point.timestamp.isoformat()}', '{tags_json}')"
                    )
                
                sql = f"""
                    INSERT INTO monitor_timeseries (config_id, metric_name, value, timestamp, tags)
                    VALUES {', '.join(values)}
                """
                cursor.execute(sql)
            return True
        except Exception as e:
            print(f"Error writing batch: {e}")
            return False

    def flush(self) -> bool:
        """
        Flush any pending batched points to the database.
        
        Returns:
            True if successful, False otherwise
        """
        if not self.batch:
            return True
            
        result = self.write_batch(self.batch)
        if result:
            self.batch = []
        return result

    def __del__(self):
        """Ensure batch is flushed on deletion."""
        self.flush()


class TimeSeriesReader:
    """
    Reader for querying time-series data from TimescaleDB.
    """

    def get_metric_range(
        self,
        metric_name: str,
        start_time: datetime,
        end_time: datetime,
        config_id: Optional[int] = None,
        tag_filters: Optional[Dict[str, str]] = None,
        bucket: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Get metric values within a time range.
        
        Args:
            metric_name: Name of the metric
            start_time: Start of time range
            end_time: End of time range
            config_id: Optional database config ID filter
            tag_filters: Optional tag filters
            bucket: Optional time bucket for downsampling
                    (e.g., '1 hour', '1 day')
                    
        Returns:
            List of metric data points with timestamp, value, and tags
        """
        try:
            with connection.cursor() as cursor:
                if bucket:
                    # Downsampled query with time bucket
                    sql = f"""
                        SELECT 
                            time_bucket('{bucket}', timestamp) AS bucket,
                            AVG(value) as value,
                            tags
                        FROM monitor_timeseries
                        WHERE metric_name = %s
                        AND timestamp >= %s
                        AND timestamp <= %s
                    """
                    params = [metric_name, start_time, end_time]
                else:
                    # Raw data query
                    sql = """
                        SELECT timestamp, value, tags
                        FROM monitor_timeseries
                        WHERE metric_name = %s
                        AND timestamp >= %s
                        AND timestamp <= %s
                    """
                    params = [metric_name, start_time, end_time]
                
                if config_id:
                    sql += " AND config_id = %s"
                    params.append(config_id)
                
                sql += " ORDER BY timestamp ASC"
                
                if bucket:
                    sql += " GROUP BY bucket, tags"
                
                cursor.execute(sql, params)
                columns = [col[0] for col in cursor.description]
                results = []
                for row in cursor.fetchall():
                    result = dict(zip(columns, row))
                    if 'tags' in result and isinstance(result['tags'], str):
                        result['tags'] = json.loads(result['tags'])
                    results.append(result)
                
                return results
        except Exception as e:
            print(f"Error reading metric range: {e}")
            return []

    def get_latest_value(
        self,
        metric_name: str,
        config_id: Optional[int] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Get the latest value for a metric.
        
        Args:
            metric_name: Name of the metric
            config_id: Optional database config ID filter
            
        Returns:
            Latest metric data point or None
        """
        try:
            with connection.cursor() as cursor:
                sql = """
                    SELECT timestamp, metric_name, value, tags
                    FROM monitor_timeseries
                    WHERE metric_name = %s
                """
                params = [metric_name]
                
                if config_id:
                    sql += " AND config_id = %s"
                    params.append(config_id)
                
                sql += " ORDER BY timestamp DESC LIMIT 1"
                
                cursor.execute(sql, params)
                row = cursor.fetchone()
                if row:
                    return {
                        'timestamp': row[0],
                        'metric_name': row[1],
                        'value': row[2],
                        'tags': json.loads(row[3]) if row[3] else {}
                    }
                return None
        except Exception as e:
            print(f"Error reading latest value: {e}")
            return None

    def downsample(
        self,
        metric_name: str,
        bucket: str,
        start_time: datetime,
        end_time: datetime,
        config_id: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """
        Downsample metric data using time_bucket.
        
        Args:
            metric_name: Name of the metric
            bucket: Time bucket (e.g., '1 hour', '1 day')
            start_time: Start of time range
            end_time: End of time range
            config_id: Optional database config ID filter
            
        Returns:
            List of downsampled data points with min/max/avg values
        """
        try:
            with connection.cursor() as cursor:
                sql = f"""
                    SELECT 
                        time_bucket('{bucket}', timestamp) AS bucket,
                        MIN(value) as min_value,
                        MAX(value) as max_value,
                        AVG(value) as avg_value,
                        COUNT(*) as sample_count
                    FROM monitor_timeseries
                    WHERE metric_name = %s
                    AND timestamp >= %s
                    AND timestamp <= %s
                """
                params = [metric_name, start_time, end_time]
                
                if config_id:
                    sql += " AND config_id = %s"
                    params.append(config_id)
                
                sql += " GROUP BY bucket ORDER BY bucket ASC"
                
                cursor.execute(sql, params)
                columns = [col[0] for col in cursor.description]
                return [dict(zip(columns, row)) for row in cursor.fetchall()]
        except Exception as e:
            print(f"Error downsampling: {e}")
            return []

    def get_aggregated_stats(
        self,
        metric_name: str,
        interval: str,
        start_time: datetime,
        end_time: datetime,
        config_id: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        Get aggregated statistics for a metric.
        
        Args:
            metric_name: Name of the metric
            interval: Time interval for bucketing
            start_time: Start of time range
            end_time: End of time range
            config_id: Optional database config ID filter
            
        Returns:
            Dictionary with min, max, mean, stddev, count
        """
        try:
            with connection.cursor() as cursor:
                sql = f"""
                    SELECT 
                        MIN(value) as min_value,
                        MAX(value) as max_value,
                        AVG(value) as mean_value,
                        STDDEV(value) as stddev_value,
                        COUNT(*) as sample_count
                    FROM monitor_timeseries
                    WHERE metric_name = %s
                    AND timestamp >= %s
                    AND timestamp <= %s
                """
                params = [metric_name, start_time, end_time]
                
                if config_id:
                    sql += " AND config_id = %s"
                    params.append(config_id)
                
                cursor.execute(sql, params)
                row = cursor.fetchone()
                if row:
                    return {
                        'min': row[0] or 0,
                        'max': row[1] or 0,
                        'mean': row[2] or 0,
                        'stddev': row[3] or 0,
                        'count': row[4] or 0
                    }
                return {}
        except Exception as e:
            print(f"Error getting aggregated stats: {e}")
            return {}


def create_hypertable() -> bool:
    """
    Create the TimescaleDB hypertable for time-series data.
    This should be called once during initial setup.
    
    Returns:
        True if successful, False otherwise
    """
    try:
        with connection.cursor() as cursor:
            # Create the regular table first
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS monitor_timeseries (
                    id BIGSERIAL,
                    config_id INTEGER NOT NULL,
                    metric_name VARCHAR(100) NOT NULL,
                    value DOUBLE PRECISION NOT NULL,
                    timestamp TIMESTAMPTZ NOT NULL,
                    tags JSONB DEFAULT '{}',
                    PRIMARY KEY (id, timestamp)
                )
            """)
            
            # Convert to hypertable
            cursor.execute("""
                SELECT create_hypertable('monitor_timeseries', 'timestamp',
                    if_not_exists => TRUE,
                    migrate_data => TRUE
                )
            """)
            
            # Create indexes for common queries
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_timeseries_metric_time 
                ON monitor_timeseries (metric_name, timestamp DESC)
            """)
            
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_timeseries_config_time 
                ON monitor_timeseries (config_id, timestamp DESC)
            """)
            
            # Add compression policy for old data
            cursor.execute("""
                ALTER TABLE monitor_timeseries SET (
                    timescaledb.compression,
                    timescaledb.max_chunk_interval = '7 days'
                )
            """)
            
            cursor.execute("""
                SELECT add_compression_policy('monitor_timeseries', INTERVAL '30 days')
            """)
            
            # Add continuous aggregate for hourly averages (optional)
            cursor.execute("""
                CREATE MATERIALIZED VIEW IF NOT EXISTS monitor_timeseries_1hour
                WITH (timescaledb.continuous) AS
                SELECT 
                    time_bucket('1 hour', timestamp) AS bucket,
                    config_id,
                    metric_name,
                    AVG(value) as avg_value,
                    MIN(value) as min_value,
                    MAX(value) as max_value
                FROM monitor_timeseries
                GROUP BY bucket, config_id, metric_name
            """)
            
            return True
    except Exception as e:
        print(f"Error creating hypertable: {e}")
        return False


def drop_hypertable() -> bool:
    """
    Drop the TimescaleDB hypertable (for testing/reset).
    
    Returns:
        True if successful, False otherwise
    """
    try:
        with connection.cursor() as cursor:
            cursor.execute("DROP TABLE IF EXISTS monitor_timeseries CASCADE")
            return True
    except Exception as e:
        print(f"Error dropping hypertable: {e}")
        return False


# Example usage
if __name__ == '__main__':
    import os
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'dbmonitor.settings')
    
    # Create hypertable
    print("Creating hypertable...")
    if create_hypertable():
        print("Hypertable created successfully")
    
    # Test writing data
    writer = TimeSeriesWriter()
    writer.write_point(
        timestamp=datetime.now(),
        metric_name='tablespace_used_pct',
        value=75.5,
        tags={'db_name': 'oracle01', 'tablespace': 'SYSTEM'},
        config_id=1
    )
    writer.flush()
    print("Data written successfully")
    
    # Test reading data
    reader = TimeSeriesReader()
    latest = reader.get_latest_value('tablespace_used_pct', config_id=1)
    print(f"Latest value: {latest}")
