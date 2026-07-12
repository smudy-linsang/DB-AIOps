# -*- coding: utf-8 -*-
"""
Phase 6A: Redis Stream 消息总线 (跨进程唯一契约源)
详细设计: phase6/00_conventions.md §3, phase6/10_phase6A_discovery.md §3

三条 Stream + 三个消费组:
  dbaiops:events    (生产: sentinel/collector; 消费组 cg_detect)
  dbaiops:diagnosis (生产: pipeline-detect;    消费组 cg_diag)
  dbaiops:verify    (生产: pipeline-执行后;     消费组 cg_verify)

编码约定: 每条消息塞单个 field "data" = json.dumps(payload)，消费端 json.loads。
"""
import json
import uuid

from django.conf import settings

STREAM_EVENTS = "dbaiops:events"
STREAM_DIAG = "dbaiops:diagnosis"
STREAM_VERIFY = "dbaiops:verify"

GROUP_DETECT = "cg_detect"
GROUP_DIAG = "cg_diag"
GROUP_VERIFY = "cg_verify"

_STREAM_GROUPS = {
    STREAM_EVENTS: GROUP_DETECT,
    STREAM_DIAG: GROUP_DIAG,
    STREAM_VERIFY: GROUP_VERIFY,
}


def get_bus():
    """获取 Redis 客户端 (复用 settings.REDIS_URL)"""
    import redis
    return redis.from_url(getattr(settings, 'REDIS_URL', 'redis://localhost:6379/0'))


def ensure_groups(bus=None):
    """幂等创建三个消费组 (XGROUP CREATE ... MKSTREAM)。已存在则忽略 BUSYGROUP。"""
    bus = bus or get_bus()
    for stream, group in _STREAM_GROUPS.items():
        try:
            bus.xgroup_create(name=stream, groupname=group, id='0', mkstream=True)
        except Exception as e:
            if 'BUSYGROUP' not in str(e):
                raise
    return True


def _maxlen():
    return int(getattr(settings, 'PIPELINE_STREAM_MAXLEN', 100000))


def _xadd(stream, payload, bus=None):
    bus = bus or get_bus()
    return bus.xadd(stream, {'data': json.dumps(payload, default=str)},
                    maxlen=_maxlen(), approximate=True)


def emit_event(payload: dict, bus=None) -> str:
    """发布事件到 dbaiops:events。payload 需符合 conventions#3.1。
    自动补 schema/event_uid (缺失时)。返回 stream message id (str)。"""
    payload = dict(payload)
    payload.setdefault('schema', '1')
    payload.setdefault('event_uid', 'EVT-' + uuid.uuid4().hex[:12])
    mid = _xadd(STREAM_EVENTS, payload, bus)
    return mid.decode() if isinstance(mid, bytes) else str(mid)


def emit_diagnosis(incident_id: str, config_id: int, trigger: str = 'created', bus=None) -> str:
    payload = {'schema': '1', 'incident_id': incident_id,
               'config_id': config_id, 'trigger': trigger}
    mid = _xadd(STREAM_DIAG, payload, bus)
    return mid.decode() if isinstance(mid, bytes) else str(mid)


def emit_verify(payload: dict, bus=None) -> str:
    payload = dict(payload)
    payload.setdefault('schema', '1')
    mid = _xadd(STREAM_VERIFY, payload, bus)
    return mid.decode() if isinstance(mid, bytes) else str(mid)


def read_group(stream, group, consumer, count=50, block_ms=2000, bus=None):
    """XREADGROUP 读一批新消息。返回 [(msg_id_str, payload_dict), ...]。"""
    bus = bus or get_bus()
    resp = bus.xreadgroup(groupname=group, consumername=consumer,
                          streams={stream: '>'}, count=count, block=block_ms)
    out = []
    if not resp:
        return out
    for _stream_name, messages in resp:
        for msg_id, fields in messages:
            mid = msg_id.decode() if isinstance(msg_id, bytes) else str(msg_id)
            raw = fields.get(b'data') or fields.get('data')
            if isinstance(raw, bytes):
                raw = raw.decode()
            try:
                payload = json.loads(raw) if raw else {}
            except Exception:
                payload = {}
            out.append((mid, payload))
    return out


def ack(stream, group, msg_id, bus=None):
    bus = bus or get_bus()
    return bus.xack(stream, group, msg_id)
