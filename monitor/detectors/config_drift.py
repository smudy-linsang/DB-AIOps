# -*- coding: utf-8 -*-
"""配置漂移检测 (phase6/10 §5.1 config 类信号落地)。

对比本轮采集的 config_params 与 Redis 中的参数基线快照:
- 首次见到该实例 → 全量写入快照, 不产事件 (建基线)
- 之后任一参数值变化 → 每参数一个 config_drift 事件 (dedup 按参数名分键)
- 参数回到基线值 → 不再产新事件 (事故由验证回路/人工解决)

基线更新: 漂移属预期变更时, 调 accept_config_snapshot() 接受新值为基线
(PB-CONFIG-ROLLBACK 回滚成功则无需更新, 快照仍是原值)。
"""
from django.utils import timezone

from monitor.detectors.metric_category import category_for_signal

_SNAP_KEY = "dbaiops:cfgsnap:{config_id}"


def _decode(d: dict) -> dict:
    out = {}
    for k, v in (d or {}).items():
        out[k.decode() if isinstance(k, bytes) else k] = (
            v.decode() if isinstance(v, bytes) else v)
    return out


def detect_config_drift(config, metrics: dict) -> list:
    """collector 尾部调用。返回 config_drift event dict 列表。"""
    params = metrics.get('config_params')
    if not isinstance(params, dict) or not params:
        return []
    try:
        from monitor.redis_bus import get_bus
        bus = get_bus()
        key = _SNAP_KEY.format(config_id=config.id)
        snap = _decode(bus.hgetall(key))
        if not snap:
            bus.hset(key, mapping={k: str(v) for k, v in params.items()})
            return []
    except Exception:
        return []

    events = []
    for k, v in params.items():
        old = snap.get(k)
        if old is None:
            # 新增参数入基线, 不算漂移
            try:
                bus.hset(key, k, str(v))
            except Exception:
                pass
            continue
        if str(v) != old:
            events.append({
                'config_id': config.id, 'db_type': config.db_type,
                'source': 'collector', 'signal': 'config_drift',
                'metric_key': k, 'value': 0.0, 'threshold': 0.0,
                'severity': 'warning',
                'occurred_at': timezone.now().isoformat(),
                'dedup_key': f"{config.id}:config_drift:{k}",
                'category': category_for_signal('config_drift'),
                'detail': {'param': k, 'old_value': old, 'new_value': str(v)},
            })
    return events


def accept_config_snapshot(config_id: int, param: str = None, value: str = None):
    """把当前漂移接受为新基线 (预期变更走此路)。param 为空则整体清空待下轮重建。"""
    from monitor.redis_bus import get_bus
    bus = get_bus()
    key = _SNAP_KEY.format(config_id=config_id)
    if param is None:
        bus.delete(key)
    elif value is not None:
        bus.hset(key, param, str(value))
