# -*- coding: utf-8 -*-
"""
中间件模块
==========

包含：
- ExceptionMiddleware: 统一异常处理
- AuditLogMiddleware: 操作审计日志
"""

import json
import logging
import traceback

from django.http import JsonResponse
from django.conf import settings
from django.utils import timezone

from .exceptions import DBMonitorError

logger = logging.getLogger(__name__)


# =============================================================================
# 不记录审计日志的路径前缀
# =============================================================================
AUDIT_EXEMPT_PREFIXES = (
    '/api/v1/auth/',       # 登录/登出/刷新 Token
    '/admin/',             # Django Admin
    '/healthcheck',        # 健康检查
    '/api/v1/health/',     # 健康检查 API
    '/api/v1/events/',     # SSE 流
    '/favicon.ico',
    '/static/',
)

# 需要记录审计日志的 HTTP 方法
AUDIT_METHODS = {'POST', 'PUT', 'PATCH', 'DELETE'}


class AuditLogMiddleware:
    """
    操作审计中间件

    拦截所有写操作（POST/PUT/PATCH/DELETE），将请求信息记录到 AuditLog 表。
    排除认证、健康检查、静态资源等路径。
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # 只拦截写操作
        if request.method in AUDIT_METHODS:
            self._record_if_needed(request)
        return self.get_response(request)

    def _record_if_needed(self, request):
        """判断是否需要记录，如需要则写入 AuditLog"""
        path = request.path

        # 检查豁免路径
        for prefix in AUDIT_EXEMPT_PREFIXES:
            if path.startswith(prefix):
                return

        # 提取操作者信息
        user_info = self._get_user_info(request)

        # 映射 HTTP 方法 → 操作类型
        action_map = {
            'POST': 'API_CREATE',
            'PUT': 'API_UPDATE',
            'PATCH': 'API_UPDATE',
            'DELETE': 'API_DELETE',
        }
        action_type = action_map.get(request.method, 'API_UNKNOWN')

        # 尝试提取请求体摘要（限制长度避免存储过大）
        body_summary = self._get_body_summary(request)

        # 记录审计日志
        try:
            from monitor.models import AuditLog as AuditLogModel
            AuditLogModel.objects.create(
                config_id=self._extract_db_config_id(path),
                action_type=action_type,
                description=f'{user_info} {request.method} {path}',
                sql_command=body_summary,
                risk_level=self._assess_risk(request.method, path),
                status='success',
                execution_context={
                    'user_info': user_info,
                    'method': request.method,
                    'path': path,
                    'query_string': request.META.get('QUERY_STRING', '')[:500],
                    'remote_addr': self._get_client_ip(request),
                    'user_agent': request.META.get('HTTP_USER_AGENT', '')[:200],
                },
            )
        except Exception:
            # 审计日志写入失败不应影响正常请求
            logger.warning('AuditLog write failed', exc_info=True)

    @staticmethod
    def _get_user_info(request):
        """从请求中提取用户信息"""
        # 优先从 Token 认证中获取
        auth_header = request.META.get('HTTP_AUTHORIZATION', '')
        if auth_header.startswith('Bearer '):
            token = auth_header[7:]
            try:
                from monitor.auth import TokenAuth
                user_info = TokenAuth.verify_token(token)
                if user_info:
                    return user_info.get('username', 'token_user')
            except Exception:
                pass

        # Session 认证
        if hasattr(request, 'user') and request.user.is_authenticated:
            return request.user.username

        return 'anonymous'

    @staticmethod
    def _get_body_summary(request):
        """提取请求体摘要"""
        try:
            if request.body:
                body = request.body.decode('utf-8')[:2000]
                # 尝试解析 JSON 以移除密码等敏感字段
                try:
                    parsed = json.loads(body)
                    if isinstance(parsed, dict):
                        for key in ('password', 'old_password', 'new_password', 'confirm_password'):
                            if key in parsed:
                                parsed[key] = '******'
                        return json.dumps(parsed, ensure_ascii=False)[:2000]
                except (json.JSONDecodeError, ValueError):
                    pass
                return body
        except Exception:
            pass
        return ''

    @staticmethod
    def _extract_db_config_id(path):
        """从 URL 路径中提取数据库配置 ID，如 /api/v1/databases/5/ → 5"""
        import re
        m = re.search(r'/databases/(\d+)', path)
        if m:
            from monitor.models import DatabaseConfig
            db_id = int(m.group(1))
            if DatabaseConfig.objects.filter(id=db_id).exists():
                return db_id
        return None

    @staticmethod
    def _assess_risk(method, path):
        """评估操作风险等级"""
        # DELETE 操作为高风险
        if method == 'DELETE':
            return 'high'
        # 涉及密码或关键配置的操作
        if 'password' in path.lower() or 'rotate' in path.lower():
            return 'high'
        # PUT/PATCH 更新操作为中风险
        if method in ('PUT', 'PATCH'):
            return 'medium'
        # POST 创建操作为低风险
        return 'low'

    @staticmethod
    def _get_client_ip(request):
        """获取客户端真实 IP"""
        xff = request.META.get('HTTP_X_FORWARDED_FOR')
        if xff:
            return xff.split(',')[0].strip()
        return request.META.get('REMOTE_ADDR', '')


class ExceptionMiddleware:
    """
    统一异常处理中间件

    只处理未被视图内部 try/except 捕获的异常。
    视图层仍可按需捕获特定异常做精细处理，
    此中间件作为最后兜底，确保所有响应格式一致。
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        return self.get_response(request)

    def process_exception(self, request, exception) -> JsonResponse:
        """
        Django 在视图抛出异常时调用此方法。
        返回 JsonResponse 则表示"已处理"，不再向上传播。
        返回 None 则继续交给 Django 默认错误处理。
        """
        # ── 1. 自定义异常 ─────────────────────────────
        if isinstance(exception, DBMonitorError):
            body = exception.to_dict()
            status = body.pop('status', 500)
            logger.warning(
                "[%s] %s detail=%s path=%s",
                exception.__class__.__name__,
                exception.message,
                exception.detail,
                request.path,
            )
            return JsonResponse(body, status=status)

        # ── 2. Django 内置异常 ─────────────────────────
        from django.core.exceptions import PermissionDenied
        from django.http import Http404

        if isinstance(exception, Http404):
            return JsonResponse(
                {'error': 'NotFound', 'message': '资源不存在'},
                status=404,
            )

        if isinstance(exception, PermissionDenied):
            return JsonResponse(
                {'error': 'PermissionDenied', 'message': '权限不足'},
                status=403,
            )

        # ── 3. db_connector.DbConnectionError 映射 ─────
        if exception.__class__.__name__ == 'DbConnectionError':
            return JsonResponse(
                {'error': 'ConnectionFailedError', 'message': str(exception)},
                status=503,
            )

        # ── 4. 未预期异常 ─────────────────────────────
        logger.error(
            "Unhandled exception on %s %s:\n%s",
            request.method,
            request.path,
            traceback.format_exc(),
        )

        # 生产环境不暴露内部错误细节
        message = (
            str(exception)
            if settings.DEBUG
            else '服务器内部错误，请稍后重试'
        )
        return JsonResponse(
            {'error': 'InternalServerError', 'message': message},
            status=500,
        )
