# -*- coding: utf-8 -*-
"""
Elasticsearch 存储引擎
负责将采集的监控指标数据写入和读取 Elasticsearch

索引策略：
- 按月分索引：db_metrics_YYYY_MM
- 使用 ILM (Index Lifecycle Management) 管理数据生命周期
- 热数据保留 30 天，温数据保留 90 天，冷数据保留 365 天
"""

import logging
import json
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any

logger = logging.getLogger(__name__)

# ES 连接配置（从 Django settings 读取）
def _get_es_url():
    try:
        from django.conf import settings
        return getattr(settings, 'ES_URL', 'http://localhost:9200')
    except Exception:
        return 'http://localhost:9200'

ES_URL = _get_es_url()

# 索引前缀
METRICS_INDEX_PREFIX = 'db_metrics'
ALERTS_INDEX_PREFIX = 'db_alerts'

# 批量写入配置
BULK_BATCH_SIZE = 500
BULK_FLUSH_INTERVAL = 30  # 秒


def get_es_client():
    """获取 Elasticsearch 客户端"""
    try:
        from elasticsearch import Elasticsearch
        client = Elasticsearch(
            [ES_URL],
            request_timeout=30,
            retry_on_timeout=True,
            max_retries=3
        )
        return client
    except ImportError:
        logger.error("elasticsearch 库未安装，请执行: pip install elasticsearch")
        return None
    except Exception as e:
        logger.error(f"连接 Elasticsearch 失败: {e}")
        return None


def get_metrics_index_name(dt: datetime = None) -> str:
    """获取指标索引名称（按月分索引）"""
    if dt is None:
        dt = datetime.now()
    return f"{METRICS_INDEX_PREFIX}_{dt.strftime('%Y_%m')}"


def get_alerts_index_name(dt: datetime = None) -> str:
    """获取告警索引名称（按月分索引）"""
    if dt is None:
        dt = datetime.now()
    return f"{ALERTS_INDEX_PREFIX}_{dt.strftime('%Y_%m')}"


# ============================================================
# 索引模板定义
# ============================================================

METRICS_MAPPING = {
    "mappings": {
        "properties": {
            "config_id": {"type": "integer"},
            "db_type": {"type": "keyword"},
            "db_name": {"type": "keyword"},
            "host": {"type": "keyword"},
            "port": {"type": "integer"},
            "environment": {"type": "keyword"},
            "status": {"type": "keyword"},
            "collected_at": {"type": "date"},
            "metrics": {
                "properties": {
                    # 通用指标
                    "cpu_usage": {"type": "float"},
                    "memory_usage": {"type": "float"},
                    "disk_usage": {"type": "float"},
                    "connections": {"type": "integer"},
                    "max_connections": {"type": "integer"},
                    "qps": {"type": "float"},
                    "tps": {"type": "float"},
                    "uptime": {"type": "long"},
                    "version": {"type": "keyword"},
                    
                    # MySQL 指标
                    "threads_connected": {"type": "integer"},
                    "threads_running": {"type": "integer"},
                    "innodb_buffer_pool_hit_ratio": {"type": "float"},
                    "slow_queries": {"type": "long"},
                    "table_locks_waited": {"type": "long"},
                    "questions": {"type": "long"},
                    "queries": {"type": "long"},
                    "transactions": {"type": "long"},
                    
                    # PostgreSQL 指标
                    "num_backends": {"type": "integer"},
                    "active_connections": {"type": "integer"},
                    "idle_connections": {"type": "integer"},
                    "blocked_connections": {"type": "integer"},
                    "xact_commit": {"type": "long"},
                    "xact_rollback": {"type": "long"},
                    "buff_cache_hit_ratio": {"type": "float"},
                    "replication_lag": {"type": "float"},
                    
                    # Oracle 指标
                    "session_count": {"type": "integer"},
                    "active_sessions": {"type": "integer"},
                    "inactive_sessions": {"type": "integer"},
                    "sga_size": {"type": "long"},
                    "pga_allocated": {"type": "long"},
                    "buffer_cache_size": {"type": "long"},
                    "tablespace_percent": {"type": "float"},
                    "tablespace_used": {"type": "float"},
                    "tablespace_size": {"type": "float"},
                    "redo_writes": {"type": "long"},
                    "redo_size": {"type": "long"},
                    "enqueue_waits": {"type": "long"},
                    "db_time": {"type": "float"},
                    "db_cpu_time": {"type": "float"},
                    
                    # DM8 指标
                    "max_sessions": {"type": "integer"},
                    "session_memory": {"type": "long"},
                    "trx_commit": {"type": "long"},
                    "trx_rollback": {"type": "long"},
                    "trx_active": {"type": "integer"},
                    "buffer_pool_hit_ratio": {"type": "float"},
                    "lock_waits": {"type": "long"},
                    "deadlock_count": {"type": "long"},
                    
                    # 通用动态指标（存储任意 key-value）
                    "raw_metrics": {"type": "object", "enabled": True}
                }
            },
            # 元数据
            "collect_duration_ms": {"type": "float"},
            "error_message": {"type": "text"},
            "created_at": {"type": "date"}
        }
    },
    "settings": {
        "number_of_shards": 1,
        "number_of_replicas": 0,
        "refresh_interval": "10s",
        "index.codec": "best_compression"
    }
}

ALERTS_MAPPING = {
    "mappings": {
        "properties": {
            "alert_id": {"type": "integer"},
            "config_id": {"type": "integer"},
            "db_name": {"type": "keyword"},
            "db_type": {"type": "keyword"},
            "alert_type": {"type": "keyword"},
            "severity": {"type": "keyword"},
            "status": {"type": "keyword"},
            "title": {"type": "text"},
            "description": {"type": "text"},
            "metric_key": {"type": "keyword"},
            "metric_value": {"type": "float"},
            "threshold": {"type": "float"},
            "fired_at": {"type": "date"},
            "resolved_at": {"type": "date"},
            "acknowledged_at": {"type": "date"},
            "acknowledged_by": {"type": "keyword"},
            "created_at": {"type": "date"}
        }
    },
    "settings": {
        "number_of_shards": 1,
        "number_of_replicas": 0,
        "refresh_interval": "10s"
    }
}


# ============================================================
# 索引管理
# ============================================================

def init_indices():
    """初始化 ES 索引模板"""
    client = get_es_client()
    if not client:
        return False
    
    try:
        # 创建指标索引模板（使用 composable templates）
        if not client.indices.exists_index_template(name='db_metrics_template'):
            client.indices.put_index_template(
                name='db_metrics_template',
                body={
                    "index_patterns": [f'{METRICS_INDEX_PREFIX}_*'],
                    "template": {
                        "settings": METRICS_MAPPING["settings"],
                        "mappings": METRICS_MAPPING["mappings"]
                    }
                }
            )
            logger.info("创建指标索引模板: db_metrics_template")
        
        # 创建告警索引模板（使用 composable templates）
        if not client.indices.exists_index_template(name='db_alerts_template'):
            client.indices.put_index_template(
                name='db_alerts_template',
                body={
                    "index_patterns": [f'{ALERTS_INDEX_PREFIX}_*'],
                    "template": {
                        "settings": ALERTS_MAPPING["settings"],
                        "mappings": ALERTS_MAPPING["mappings"]
                    }
                }
            )
            logger.info("创建告警索引模板: db_alerts_template")
        
        # 创建当月索引（只使用 settings 和 mappings，不含 index_patterns）
        current_metrics_idx = get_metrics_index_name()
        if not client.indices.exists(index=current_metrics_idx):
            client.indices.create(
                index=current_metrics_idx,
                body={
                    "settings": METRICS_MAPPING["settings"],
                    "mappings": METRICS_MAPPING["mappings"]
                }
            )
            logger.info(f"创建当月指标索引: {current_metrics_idx}")
        
        current_alerts_idx = get_alerts_index_name()
        if not client.indices.exists(index=current_alerts_idx):
            client.indices.create(
                index=current_alerts_idx,
                body={
                    "settings": ALERTS_MAPPING["settings"],
                    "mappings": ALERTS_MAPPING["mappings"]
                }
            )
            logger.info(f"创建当月告警索引: {current_alerts_idx}")
        
        return True
    except Exception as e:
        logger.error(f"初始化 ES 索引失败: {e}")
        return False


def check_es_health() -> Dict[str, Any]:
    """检查 ES 集群健康状态"""
    client = get_es_client()
    if not client:
        return {'status': 'unavailable', 'error': '无法连接 ES'}
    
    try:
        health = client.cluster.health()
        info = client.info()
        return {
            'status': health['status'],
            'cluster_name': health['cluster_name'],
            'number_of_nodes': health['number_of_nodes'],
            'active_primary_shards': health['active_primary_shards'],
            'active_shards': health['active_shards'],
            'version': info['version']['number'],
            'url': ES_URL
        }
    except Exception as e:
        return {'status': 'error', 'error': str(e)}


# ============================================================
# 数据写入
# ============================================================

def index_metrics(config_id: int, db_type: str, db_name: str,
                  host: str, port: int, environment: str,
                  status: str, metrics: Dict[str, Any],
                  collect_duration_ms: float = None,
                  error_message: str = None,
                  collected_at: datetime = None) -> bool:
    """
    将采集的指标数据写入 ES
    
    Args:
        config_id: 数据库配置ID
        db_type: 数据库类型
        db_name: 数据库名称
        host: 主机地址
        port: 端口
        environment: 环境
        status: 状态 (UP/DOWN/UNKNOWN)
        metrics: 指标数据字典
        collect_duration_ms: 采集耗时(毫秒)
        error_message: 错误信息
        collected_at: 采集时间
    
    Returns:
        是否写入成功
    """
    client = get_es_client()
    if not client:
        logger.warning("ES 不可用，跳过指标写入")
        return False
    
    if collected_at is None:
        collected_at = datetime.now()
    
    # 分离已知指标和动态指标
    known_keys = set(METRICS_MAPPING['mappings']['properties']['metrics']['properties'].keys())
    known_keys.discard('raw_metrics')
    
    known_metrics = {}
    raw_metrics = {}
    for key, value in metrics.items():
        if key in known_keys:
            known_metrics[key] = value
        else:
            raw_metrics[key] = value
    
    known_metrics['raw_metrics'] = raw_metrics
    
    doc = {
        'config_id': config_id,
        'db_type': db_type,
        'db_name': db_name,
        'host': host,
        'port': port,
        'environment': environment or '',
        'status': status,
        'collected_at': collected_at.isoformat(),
        'metrics': known_metrics,
        'collect_duration_ms': collect_duration_ms,
        'error_message': error_message,
        'created_at': datetime.now().isoformat()
    }
    
    try:
        index_name = get_metrics_index_name(collected_at)
        client.index(
            index=index_name,
            document=doc,
            id=f"{config_id}_{collected_at.strftime('%Y%m%d%H%M%S')}"
        )
        return True
    except Exception as e:
        logger.error(f"写入 ES 指标失败 (config_id={config_id}): {e}")
        return False


def bulk_index_metrics(docs: List[Dict]) -> Dict[str, int]:
    """
    批量写入指标数据
    
    Args:
        docs: 文档列表，每个文档包含 index_metrics 的参数
    
    Returns:
        {'success': 成功数, 'failed': 失败数}
    """
    client = get_es_client()
    if not client:
        return {'success': 0, 'failed': len(docs)}
    
    from elasticsearch.helpers import bulk
    
    actions = []
    for doc in docs:
        collected_at = doc.get('collected_at', datetime.now())
        if isinstance(collected_at, str):
            collected_at = datetime.fromisoformat(collected_at)
        
        index_name = get_metrics_index_name(collected_at)
        config_id = doc.get('config_id', 0)
        
        action = {
            '_index': index_name,
            '_id': f"{config_id}_{collected_at.strftime('%Y%m%d%H%M%S')}",
            '_source': doc
        }
        actions.append(action)
    
    try:
        success, errors = bulk(client, actions, raise_on_error=False)
        failed = len(actions) - success
        if errors:
            logger.warning(f"批量写入部分失败: {len(errors)} 条")
        return {'success': success, 'failed': failed}
    except Exception as e:
        logger.error(f"批量写入 ES 失败: {e}")
        return {'success': 0, 'failed': len(docs)}


def index_alert(alert_id: int, config_id: int, db_name: str, db_type: str,
                alert_type: str, severity: str, status: str,
                title: str, description: str = None,
                metric_key: str = None, metric_value: float = None,
                threshold: float = None,
                fired_at: datetime = None, resolved_at: datetime = None,
                acknowledged_at: datetime = None, acknowledged_by: str = None) -> bool:
    """将告警数据写入 ES"""
    client = get_es_client()
    if not client:
        return False
    
    doc = {
        'alert_id': alert_id,
        'config_id': config_id,
        'db_name': db_name,
        'db_type': db_type,
        'alert_type': alert_type,
        'severity': severity,
        'status': status,
        'title': title,
        'description': description,
        'metric_key': metric_key,
        'metric_value': metric_value,
        'threshold': threshold,
        'fired_at': fired_at.isoformat() if fired_at else None,
        'resolved_at': resolved_at.isoformat() if resolved_at else None,
        'acknowledged_at': acknowledged_at.isoformat() if acknowledged_at else None,
        'acknowledged_by': acknowledged_by,
        'created_at': datetime.now().isoformat()
    }
    
    try:
        index_name = get_alerts_index_name(fired_at or datetime.now())
        client.index(
            index=index_name,
            document=doc,
            id=str(alert_id)
        )
        return True
    except Exception as e:
        logger.error(f"写入 ES 告警失败 (alert_id={alert_id}): {e}")
        return False


# ============================================================
# 数据查询
# ============================================================

def query_metrics(config_id: int, start_time: datetime = None,
                  end_time: datetime = None, metric_names: List[str] = None,
                  limit: int = 1000, sort_order: str = 'desc') -> List[Dict]:
    """
    查询指定数据库的历史指标数据
    
    Args:
        config_id: 数据库配置ID
        start_time: 开始时间
        end_time: 结束时间
        metric_names: 指定指标名列表（可选）
        limit: 返回条数
        sort_order: 排序方向 (asc/desc)
    
    Returns:
        指标数据列表
    """
    client = get_es_client()
    if not client:
        return []
    
    if start_time is None:
        start_time = datetime.now() - timedelta(hours=24)
    if end_time is None:
        end_time = datetime.now()
    
    # 确定需要查询的索引（可能跨月）
    indices = []
    current = start_time.replace(day=1)
    while current <= end_time:
        idx = get_metrics_index_name(current)
        if client.indices.exists(index=idx):
            indices.append(idx)
        # 下个月
        if current.month == 12:
            current = current.replace(year=current.year + 1, month=1)
        else:
            current = current.replace(month=current.month + 1)
    
    if not indices:
        # 回退到当月索引
        indices = [get_metrics_index_name()]
    
    index_pattern = ','.join(indices)
    
    # 构建查询
    must = [
        {"term": {"config_id": config_id}},
        {"range": {"collected_at": {
            "gte": start_time.isoformat(),
            "lte": end_time.isoformat()
        }}}
    ]
    
    query = {
        "bool": {
            "must": must
        }
    }
    
    # 如果指定了指标名，只返回这些字段
    source_includes = ["config_id", "db_type", "status", "collected_at"]
    if metric_names:
        for name in metric_names:
            source_includes.append(f"metrics.{name}")
    else:
        source_includes.append("metrics")
    
    try:
        result = client.search(
            index=index_pattern,
            body={
                "query": query,
                "sort": [{"collected_at": {"order": sort_order}}],
                "size": limit,
                "_source": source_includes
            }
        )
        
        hits = result.get('hits', {}).get('hits', [])
        return [hit['_source'] for hit in hits]
    except Exception as e:
        logger.error(f"查询 ES 指标失败 (config_id={config_id}): {e}")
        return []


def query_latest_metrics(config_id: int) -> Optional[Dict]:
    """查询指定数据库的最新一条指标数据"""
    results = query_metrics(config_id, limit=1, sort_order='desc')
    return results[0] if results else None


def query_metrics_aggregation(config_id: int, metric_name: str,
                               start_time: datetime = None,
                               end_time: datetime = None,
                               interval: str = '1h') -> List[Dict]:
    """
    查询指标的聚合数据（用于图表展示）
    
    Args:
        config_id: 数据库配置ID
        metric_name: 指标名
        start_time: 开始时间
        end_time: 结束时间
        interval: 聚合间隔 (1m, 5m, 15m, 1h, 1d)
    
    Returns:
        聚合结果列表 [{'time': ..., 'avg': ..., 'min': ..., 'max': ...}]
    """
    client = get_es_client()
    if not client:
        return []
    
    if start_time is None:
        start_time = datetime.now() - timedelta(hours=24)
    if end_time is None:
        end_time = datetime.now()
    
    indices = []
    current = start_time.replace(day=1)
    while current <= end_time:
        idx = get_metrics_index_name(current)
        if client.indices.exists(index=idx):
            indices.append(idx)
        if current.month == 12:
            current = current.replace(year=current.year + 1, month=1)
        else:
            current = current.replace(month=current.month + 1)
    
    if not indices:
        indices = [get_metrics_index_name()]
    
    index_pattern = ','.join(indices)
    field_path = f"metrics.{metric_name}"
    
    try:
        result = client.search(
            index=index_pattern,
            body={
                "query": {
                    "bool": {
                        "must": [
                            {"term": {"config_id": config_id}},
                            {"range": {"collected_at": {
                                "gte": start_time.isoformat(),
                                "lte": end_time.isoformat()
                            }}},
                            {"exists": {"field": field_path}}
                        ]
                    }
                },
                "size": 0,
                "aggs": {
                    "metrics_over_time": {
                        "date_histogram": {
                            "field": "collected_at",
                            "fixed_interval": interval,
                            "min_doc_count": 0
                        },
                        "aggs": {
                            "avg_value": {"avg": {"field": field_path}},
                            "min_value": {"min": {"field": field_path}},
                            "max_value": {"max": {"field": field_path}},
                            "last_value": {
                                "top_hits": {
                                    "size": 1,
                                    "sort": [{"collected_at": {"order": "desc"}}],
                                    "_source": [field_path]
                                }
                            }
                        }
                    }
                }
            }
        )
        
        buckets = result.get('aggregations', {}).get('metrics_over_time', {}).get('buckets', [])
        return [
            {
                'time': bucket['key_as_string'],
                'timestamp': bucket['key'],
                'avg': bucket['avg_value']['value'],
                'min': bucket['min_value']['value'],
                'max': bucket['max_value']['value'],
                'count': bucket['doc_count']
            }
            for bucket in buckets
            if bucket['doc_count'] > 0
        ]
    except Exception as e:
        logger.error(f"查询 ES 聚合数据失败: {e}")
        return []


def query_alerts(config_id: int = None, severity: str = None,
                 status: str = None, start_time: datetime = None,
                 end_time: datetime = None, limit: int = 100) -> List[Dict]:
    """查询告警数据"""
    client = get_es_client()
    if not client:
        return []
    
    if start_time is None:
        start_time = datetime.now() - timedelta(days=30)
    if end_time is None:
        end_time = datetime.now()
    
    indices = []
    current = start_time.replace(day=1)
    while current <= end_time:
        idx = get_alerts_index_name(current)
        if client.indices.exists(index=idx):
            indices.append(idx)
        if current.month == 12:
            current = current.replace(year=current.year + 1, month=1)
        else:
            current = current.replace(month=current.month + 1)
    
    if not indices:
        indices = [get_alerts_index_name()]
    
    index_pattern = ','.join(indices)
    
    must = [
        {"range": {"created_at": {
            "gte": start_time.isoformat(),
            "lte": end_time.isoformat()
        }}}
    ]
    
    if config_id:
        must.append({"term": {"config_id": config_id}})
    if severity:
        must.append({"term": {"severity": severity}})
    if status:
        must.append({"term": {"status": status}})
    
    try:
        result = client.search(
            index=index_pattern,
            body={
                "query": {"bool": {"must": must}},
                "sort": [{"created_at": {"order": "desc"}}],
                "size": limit
            }
        )
        
        hits = result.get('hits', {}).get('hits', [])
        return [hit['_source'] for hit in hits]
    except Exception as e:
        logger.error(f"查询 ES 告警失败: {e}")
        return []


def get_db_count() -> int:
    """获取 ES 中存储的不同数据库数量"""
    client = get_es_client()
    if not client:
        return 0
    
    try:
        index_pattern = f"{METRICS_INDEX_PREFIX}_*"
        if not client.indices.exists(index=index_pattern):
            return 0
        
        result = client.search(
            index=index_pattern,
            body={
                "size": 0,
                "aggs": {
                    "unique_dbs": {
                        "cardinality": {
                            "field": "config_id"
                        }
                    }
                }
            }
        )
        return result['aggregations']['unique_dbs']['value']
    except Exception as e:
        logger.error(f"获取 ES 数据库数量失败: {e}")
        return 0


def get_total_docs() -> int:
    """获取 ES 中指标文档总数"""
    client = get_es_client()
    if not client:
        return 0
    
    try:
        index_pattern = f"{METRICS_INDEX_PREFIX}_*"
        if not client.indices.exists(index=index_pattern):
            return 0
        return client.count(index=index_pattern)['count']
    except Exception as e:
        logger.error(f"获取 ES 文档总数失败: {e}")
        return 0


def delete_old_indices(days: int = 365):
    """删除超过指定天数的旧索引"""
    client = get_es_client()
    if not client:
        return
    
    cutoff = datetime.now() - timedelta(days=days)
    
    try:
        indices = client.indices.get(index=f"{METRICS_INDEX_PREFIX}_*")
        for index_name in indices:
            # 从索引名解析日期: db_metrics_2026_04
            parts = index_name.split('_')
            if len(parts) >= 4:
                try:
                    year = int(parts[-2])
                    month = int(parts[-1])
                    index_date = datetime(year, month, 1)
                    if index_date < cutoff:
                        client.indices.delete(index=index_name)
                        logger.info(f"删除旧索引: {index_name}")
                except ValueError:
                    continue
    except Exception as e:
        logger.error(f"清理旧索引失败: {e}")
