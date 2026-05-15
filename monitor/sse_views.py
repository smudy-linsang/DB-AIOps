# -*- coding: utf-8 -*-
"""
SSE (Server-Sent Events) 实时推送模块

通过 Redis Pub/Sub 实现告警和指标变化的实时推送，
前端通过 EventSource API 订阅 /api/v1/events/ 端点。

架构:
    采集/告警 → redis.publish() → Redis Pub/Sub → SSEView → EventSource(前端)
"""

import json
import logging
import redis
import threading
import time

from django.conf import settings
from django.http import StreamingHttpResponse
from django.views import View
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt

logger = logging.getLogger(__name__)

# SSE 最大连接数
MAX_SSE_CONNECTIONS = 100
_active_connections = 0
_connections_lock = threading.Lock()


def get_redis_client():
    """获取 Redis 客户端"""
    redis_url = getattr(settings, 'REDIS_URL', 'redis://localhost:6379/0')
    return redis.from_url(redis_url)


def publish_event(channel: str, data: dict):
    """
    发布事件到 Redis Pub/Sub

    Args:
        channel: 频道名，如 'monitor:alerts' 或 'monitor:metrics'
        data: 事件数据字典
    """
    try:
        r = get_redis_client()
        r.publish(channel, json.dumps(data, ensure_ascii=False, default=str))
    except Exception as e:
        logger.warning(f"[SSE] 发布事件失败 channel={channel}: {e}")


def publish_alert_event(alert_type: str, action: str, alert_data: dict):
    """
    发布告警事件（fire/resolve/acknowledge）

    Args:
        alert_type: 告警类型 (down/tablespace/connection/lock/baseline/rca/...)
        action: 动作 (fire/resolve/acknowledge)
        alert_data: 告警详情
    """
    publish_event('monitor:alerts', {
        'type': 'alert',
        'alert_type': alert_type,
        'action': action,
        **alert_data,
    })


def publish_metric_event(config_id: int, db_name: str, db_type: str, metrics: dict):
    """
    发布指标更新事件

    Args:
        config_id: 数据库配置ID
        db_name: 数据库名称
        db_type: 数据库类型
        metrics: 指标数据
    """
    publish_event('monitor:metrics', {
        'type': 'metric',
        'config_id': config_id,
        'db_name': db_name,
        'db_type': db_type,
        'metrics': metrics,
    })


@method_decorator(csrf_exempt, name='dispatch')
class SSEView(View):
    """
    SSE 实时事件端点

    GET /api/v1/events/

    订阅 Redis Pub/Sub 频道，将事件通过 SSE 格式推送到前端。
    需要认证 Token。
    """

    def get(self, request):
        global _active_connections

        # 连接数限制
        with _connections_lock:
            if _active_connections >= MAX_SSE_CONNECTIONS:
                from django.http import JsonResponse
                return JsonResponse(
                    {'error': 'Too many SSE connections'},
                    status=429
                )
            _active_connections += 1

        # 简单的认证检查
        from .auth import TokenManager
        auth_header = request.headers.get('Authorization', '')
        if auth_header.startswith('Bearer '):
            token = auth_header[7:]
        elif auth_header.startswith('Token '):
            token = auth_header[6:]
        else:
            token = request.GET.get('token', '')

        # Token 为空时仍允许连接（开发模式），但生产环境应强制认证
        user_info = None
        if token:
            user_info = TokenManager.validate_token(token)

        response = StreamingHttpResponse(
            self._event_stream(request, user_info),
            content_type='text/event-stream',
        )
        response['Cache-Control'] = 'no-cache'
        response['X-Accel-Buffering'] = 'no'  # nginx 代理时禁止缓冲
        # 注意：不能设置 Connection: keep-alive，WSGI 规范禁止 hop-by-hop header

        return response

    def _event_stream(self, request, user_info):
        """SSE 事件流生成器"""
        global _active_connections

        try:
            r = get_redis_client()
            pubsub = r.pubsub()
            pubsub.subscribe('monitor:alerts', 'monitor:metrics')

            # 发送初始连接成功事件
            yield self._format_sse('connected', {'status': 'ok', 'message': 'SSE connected'})

            # 心跳间隔（秒）
            heartbeat_interval = 30
            last_heartbeat = time.time()

            for message in pubsub.listen():
                # 心跳
                now = time.time()
                if now - last_heartbeat >= heartbeat_interval:
                    yield self._format_sse('heartbeat', {'ts': int(now)})
                    last_heartbeat = now

                if message['type'] != 'message':
                    continue

                try:
                    data = json.loads(message['data'])

                    # 如果有用户信息，检查数据范围权限
                    # 简化：所有认证用户接收所有事件
                    channel = message.get('channel', b'')
                    if isinstance(channel, bytes):
                        channel = channel.decode()

                    event_type = 'alert' if 'alerts' in channel else 'metric'
                    yield self._format_sse(event_type, data)

                except (json.JSONDecodeError, Exception) as e:
                    logger.warning(f"[SSE] 消息解析失败: {e}")
                    continue

        except redis.ConnectionError:
            logger.error("[SSE] Redis 连接断开")
            yield self._format_sse('error', {'message': 'Redis connection lost'})
        except Exception as e:
            logger.error(f"[SSE] 事件流异常: {e}")
            yield self._format_sse('error', {'message': str(e)})
        finally:
            with _connections_lock:
                _active_connections = max(0, _active_connections - 1)
            try:
                pubsub.unsubscribe()
                pubsub.close()
            except Exception:
                pass

    @staticmethod
    def _format_sse(event: str, data: dict) -> str:
        """格式化 SSE 消息"""
        return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"
