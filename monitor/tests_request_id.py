# -*- coding: utf-8 -*-
"""请求关联 ID 与 API 关键失败分支诊断留痕的单元测试。

覆盖：
- RequestIdMiddleware：每请求注入关联 ID、透传上游合法 ID、拒绝非法 ID、
  请求结束后复位 ContextVar；
- RequestIdFilter：把关联 ID 附加到日志记录（请求上下文外为 '-'）；
- api_views.py 关键失败分支：触发后产生 degrade 分级留痕，且日志行
  与响应用同一关联 ID 串联。
"""
import io
import logging
from unittest import mock

from django.contrib.auth.models import User
from django.http import HttpResponse
from django.test import Client, RequestFactory, TestCase, tag

from monitor import degrade
from monitor.auth import RoleCode
from monitor.crypto import encrypt_password
from monitor.middleware import RequestIdMiddleware
from monitor.models import DatabaseConfig, Role, UserProfile
from monitor.request_context import (
    RequestIdFilter, get_request_id, reset_request_id, set_request_id,
)


# =============================================================================
# 公共夹具（与 tests_bugfix.py 同款最小实现）
# =============================================================================
def _make_user(username):
    user = User.objects.create_user(username=username, password='Pw!23456')  # secret-scan: allow 测试夹具
    role, _ = Role.objects.get_or_create(
        code=RoleCode.READONLY, defaults={'name': RoleCode.READONLY, 'is_builtin': True})
    UserProfile.objects.create(user=user, role=role)
    return user


def _make_db(name='db1'):
    return DatabaseConfig.objects.create(
        name=name, db_type='mysql', host='127.0.0.1', port=3306,
        username='root', password=encrypt_password('pw'), is_active=True)


def _login(client, user):
    from monitor.auth import TokenManager
    token = TokenManager.generate_token(user.id)
    client.defaults['HTTP_AUTHORIZATION'] = f'Bearer {token}'


@tag('unit')
class RequestIdMiddlewareTests(TestCase):
    """关联 ID：注入、透传、响应回写、请求后复位。"""

    def _call(self, **meta):
        captured = {}

        def view(request):
            captured['request_attr'] = request.request_id
            captured['ctx'] = get_request_id()
            return HttpResponse('ok')

        request = RequestFactory().get('/api/v1/databases/', **meta)
        return RequestIdMiddleware(view)(request), captured

    def test_generates_id_visible_in_view_and_response(self):
        resp, captured = self._call()
        rid = resp['X-Request-ID']
        self.assertRegex(rid, r'^[0-9a-f]{32}$')
        self.assertEqual(captured['request_attr'], rid)
        self.assertEqual(captured['ctx'], rid)

    def test_context_reset_after_request(self):
        self._call()
        # 线程复用场景下不得把关联 ID 串到下一个请求
        self.assertEqual(get_request_id(), '-')

    def test_valid_incoming_header_propagated(self):
        resp, captured = self._call(HTTP_X_REQUEST_ID='upstream-req-42')
        self.assertEqual(resp['X-Request-ID'], 'upstream-req-42')
        self.assertEqual(captured['ctx'], 'upstream-req-42')

    def test_invalid_incoming_header_replaced(self):
        resp, _ = self._call(HTTP_X_REQUEST_ID='bad\nid-injection')
        self.assertRegex(resp['X-Request-ID'], r'^[0-9a-f]{32}$')


@tag('unit')
class RequestIdFilterTests(TestCase):
    """日志 Filter：请求内附加关联 ID，请求外为占位符。"""

    def test_filter_injects_request_id(self):
        record = logging.LogRecord(
            'x', logging.INFO, __file__, 1, 'msg', None, None)
        self.assertTrue(RequestIdFilter().filter(record))
        self.assertEqual(record.request_id, '-')

        token = set_request_id('rid-001')
        try:
            RequestIdFilter().filter(record)
            self.assertEqual(record.request_id, 'rid-001')
        finally:
            reset_request_id(token)


@tag('unit')
class ApiFailureDiagnosticLogTests(TestCase):
    """关键失败分支：degrade 分级留痕，且日志行与响应用同一关联 ID 串联。"""

    def setUp(self):
        self.user = _make_user('diag')
        self.db = _make_db()
        degrade.reset()

    def test_slow_query_collect_failure_logs_with_request_id(self):
        client = Client()
        _login(client, self.user)

        # 临时给 degrade 日志挂一个带关联 ID Filter 的 handler，
        # 验证失败分支的留痕行能用同一关联 ID 串联
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        handler.addFilter(RequestIdFilter())
        handler.setFormatter(logging.Formatter('rid={request_id} {message}', style='{'))
        degrade_logger = logging.getLogger('monitor.degrade')
        degrade_logger.addHandler(handler)
        try:
            with mock.patch('monitor.api_views.SlowQueryEngine') as engine_cls:
                engine_cls.return_value.collect_slow_queries_from_db.side_effect = (
                    RuntimeError('boom')
                )
                resp = client.get(
                    f'/api/v1/databases/{self.db.id}/slow-queries/',
                    HTTP_X_REQUEST_ID='fix-verify-001',
                )
        finally:
            degrade_logger.removeHandler(handler)

        self.assertEqual(resp.status_code, 500)
        self.assertEqual(resp['X-Request-ID'], 'fix-verify-001')
        # degrade 分级留痕：失败分支被计数，可读且可归因
        self.assertGreaterEqual(degrade.snapshot().get('api.slow_query.collect', 0), 1)
        # 日志行中出现关联 ID 与 scope
        output = stream.getvalue()
        self.assertIn('fix-verify-001', output)
        self.assertIn('api.slow_query.collect', output)
