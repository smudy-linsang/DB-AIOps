# -*- coding: utf-8 -*-
"""
中间件模块
==========

包含：
- RequestIdMiddleware: 请求关联 ID 注入与透传
- ExceptionMiddleware: 统一异常处理
- AuditLogMiddleware: 操作审计日志
"""

import json
import logging
import re
import traceback
import uuid

from django.http import JsonResponse
from django.conf import settings
from django.utils import timezone

from .exceptions import DBMonitorError
from .request_context import set_request_id, reset_request_id

logger = logging.getLogger(__name__)

# 关联 ID 请求头（Django META 键名）与响应头
REQUEST_ID_HEADER = 'HTTP_X_REQUEST_ID'
REQUEST_ID_RESPONSE_HEADER = 'X-Request-ID'
# 上游传入的关联 ID 白名单：防日志注入，只允许安全字符且限长
_REQUEST_ID_RE = re.compile(r'^[A-Za-z0-9._-]{1,64}$')


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


class RequestIdMiddleware:
    """
    请求关联 ID 中间件

    为每个请求注入关联 ID 并透传：
    - 上游传入合法 X-Request-ID 时沿用（便于跨系统串联），否则自行生成；
    - 写入 ContextVar（monitor.request_context），日志 Filter 会将其附加到
      该请求期间产生的每条日志行，实现「同一请求日志用同一 ID 串联」；
    - 同时挂在 request.request_id 并回写响应头，便于前后端归因。

    必须放在 MIDDLEWARE 最外层，确保后续中间件/视图的日志都能带上 ID。
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        incoming = request.META.get(REQUEST_ID_HEADER, '').strip()
        request_id = incoming if _REQUEST_ID_RE.match(incoming) else uuid.uuid4().hex
        request.request_id = request_id
        token = set_request_id(request_id)
        try:
            response = self.get_response(request)
        finally:
            # 线程复用场景下必须复位，避免关联 ID 串到下一个请求
            reset_request_id(token)
        response[REQUEST_ID_RESPONSE_HEADER] = request_id
        return response


class SecurityHeadersMiddleware:
    """
    安全响应头中间件（BUG-019）

    为响应补充 Content-Security-Policy 等安全头。CSP 策略通过
    settings.CONTENT_SECURITY_POLICY（或环境变量 CONTENT_SECURITY_POLICY）配置，
    未配置时不下发 CSP（避免默认策略误伤前端内联脚本/样式），由部署方按实际前端定制。
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        csp = getattr(settings, 'CONTENT_SECURITY_POLICY', '')
        if csp and not response.has_header('Content-Security-Policy'):
            response['Content-Security-Policy'] = csp
        # 额外加固头（Django 默认已含 X-Frame-Options/X-Content-Type-Options，此处补充）
        if not response.has_header('X-Content-Type-Options'):
            response['X-Content-Type-Options'] = 'nosniff'
        if not response.has_header('Referrer-Policy'):
            response['Referrer-Policy'] = 'same-origin'
        return response


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
            from django.db import transaction
            from monitor.models import AuditLog as AuditLogModel
            # 包在保存点里：万一写失败，回滚保存点即可，不会污染外层事务
            with transaction.atomic():
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
            # 审计日志写入失败不应影响正常请求。
            # 注意：在 PostgreSQL 下，失败的 INSERT 会污染当前事务，
            # 后续查询将报 InFailedSqlTransaction。这里主动回滚到保存点隔离影响。
            logger.warning('AuditLog write failed', exc_info=True)

    @staticmethod
    def _get_user_info(request):
        """从请求中提取用户信息"""
        # 优先从 Token 认证中获取
        auth_header = request.META.get('HTTP_AUTHORIZATION', '')
        if auth_header.startswith('Bearer '):
            token = auth_header[7:]
            try:
                from monitor.auth import TokenManager
                user_info = TokenManager.validate_token(token)
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
