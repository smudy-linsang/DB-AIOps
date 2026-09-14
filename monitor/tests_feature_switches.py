# -*- coding: utf-8 -*-
"""V2.6 W2: 外部依赖功能开关落地回归测试。

背景：`get_es_client()` 与 `sse_views.get_redis_client()` 此前都**不读开关**，
于是在 ES_ENABLED=False / 无 Redis 的 unit 层仍会去连 localhost:9200 / 6379，
按 request_timeout=30、max_retries=3 反复重试 —— 实测把声明"零外部依赖、~10s"
的 unit 层拖到 5 分钟以上仍不返回，直接损害 scripts/validate.sh unit
这个 AGENTS.md 唯一强制的质量闸。

本文件钉死"开关关闭 ⇒ 不建客户端、不发连接"，属纯逻辑断言，不触网。
"""
from unittest import mock

from django.test import SimpleTestCase, override_settings, tag

from monitor import appconf


@tag('unit')
class EsSwitchTests(SimpleTestCase):
    """W2-01: get_es_client() 必须尊重 ES_ENABLED。"""

    @override_settings(ES_ENABLED=False)
    def test_returns_none_when_disabled(self):
        import monitor.elasticsearch_engine as es
        es._es_client = None
        with mock.patch.object(es, 'Elasticsearch', create=True) as ctor:
            self.assertIsNone(es.get_es_client())
            ctor.assert_not_called()

    @override_settings(ES_ENABLED=False)
    def test_disabled_does_not_touch_network(self):
        """关闭时不得构造任何 ES 客户端（即不产生连接尝试）。"""
        import monitor.elasticsearch_engine as es
        es._es_client = None
        with mock.patch('elasticsearch.Elasticsearch') as ctor:
            es.get_es_client()
            ctor.assert_not_called()

    @override_settings(ES_ENABLED=True)
    def test_enabled_still_creates_client(self):
        """开关打开时行为不变（防过度收紧）。"""
        import monitor.elasticsearch_engine as es
        es._es_client = None
        with mock.patch('elasticsearch.Elasticsearch') as ctor:
            ctor.return_value.ping.return_value = True
            client = es.get_es_client()
            self.assertIsNotNone(client)
            ctor.assert_called_once()


@tag('unit')
class RedisSwitchTests(SimpleTestCase):
    """W2-02: sse_views.get_redis_client() 必须尊重 USE_REDIS_CACHE 且复用单例。"""

    @override_settings(USE_REDIS_CACHE=False)
    def test_returns_none_when_disabled(self):
        from monitor import sse_views
        with mock.patch('redis.from_url') as ctor:
            self.assertIsNone(sse_views.get_redis_client())
            ctor.assert_not_called()

    @override_settings(USE_REDIS_CACHE=True)
    def test_reuses_redis_bus_singleton(self):
        """启用时走 redis_bus 的单例，而不是自建裸连接（BUG-112 语义）。"""
        from monitor import sse_views
        sentinel = object()
        with mock.patch('monitor.redis_bus.get_bus', return_value=sentinel) as gb:
            self.assertIs(sse_views.get_redis_client(), sentinel)
            gb.assert_called_once()

    @override_settings(USE_REDIS_CACHE=False)
    def test_publish_event_is_silent_when_disabled(self):
        """关闭时 publish_event 静默降级，不得抛异常。"""
        from monitor import sse_views
        sse_views.publish_event('monitor:alerts', {'type': 'alert'})  # 不应抛


@tag('unit')
class AppconfRegistrationTests(SimpleTestCase):
    """两个开关都必须在 appconf.SPECS 登记（AGENTS.md 红线）。"""

    def test_switches_registered(self):
        for key in ('ES_ENABLED', 'USE_REDIS_CACHE', 'TIMESCALEDB_ENABLED'):
            with self.subTest(key=key):
                self.assertIn(key, appconf.SPECS)
