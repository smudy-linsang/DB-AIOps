"""
认证与权限控制模块
=================

Phase 3.6 核心模块 - REST API 扩展与 RBAC 完善

实现基于角色的访问控制（RBAC）：
- 角色定义：read_only_observer, dba_operator, dba_supervisor, admin
- Token 认证（可扩展为 JWT）
- 数据范围过滤（用户只能访问授权的数据库）

Author: DB-AIOps Team
"""

import hashlib
import hmac
import secrets
import time
from datetime import datetime, timedelta
from functools import wraps
from typing import Optional, List, Callable

from django.conf import settings
from django.http import JsonResponse, HttpRequest
from django.contrib.auth.models import User
from django.core.cache import cache

from .models import UserProfile


# =============================================================================
# 角色定义
# =============================================================================

class Role:
    """角色常量"""
    READ_ONLY_OBSERVER = 'read_only_observer'  # 只读观察者
    DBA_OPERATOR = 'dba_operator'               # DBA 操作员
    DBA_SUPERVISOR = 'dba_supervisor'           # DBA 主管
    ADMIN = 'admin'                             # 系统管理员


class Permission:
    """权限常量"""
    # 读取权限
    VIEW_DATABASE = 'view_database'
    VIEW_METRICS = 'view_metrics'
    VIEW_BASELINE = 'view_baseline'
    VIEW_PREDICTION = 'view_prediction'
    VIEW_HEALTH = 'view_health'
    VIEW_ALERTS = 'view_alerts'
    VIEW_AUDITLOGS = 'view_auditlogs'
    
    # 操作权限
    ACKNOWLEDGE_ALERTS = 'acknowledge_alerts'
    APPROVE_AUDITLOGS = 'approve_auditlogs'
    EXECUTE_OPERATIONS = 'execute_operations'
    
    # 管理权限
    MANAGE_DATABASES = 'manage_databases'
    MANAGE_USERS = 'manage_users'
    MANAGE_ROLES = 'manage_roles'


# 角色权限映射
ROLE_PERMISSIONS = {
    Role.READ_ONLY_OBSERVER: [
        Permission.VIEW_DATABASE,
        Permission.VIEW_METRICS,
        Permission.VIEW_BASELINE,
        Permission.VIEW_PREDICTION,
        Permission.VIEW_HEALTH,
        Permission.VIEW_ALERTS,
        Permission.VIEW_AUDITLOGS,
    ],
    Role.DBA_OPERATOR: [
        Permission.VIEW_DATABASE,
        Permission.VIEW_METRICS,
        Permission.VIEW_BASELINE,
        Permission.VIEW_PREDICTION,
        Permission.VIEW_HEALTH,
        Permission.VIEW_ALERTS,
        Permission.VIEW_AUDITLOGS,
        Permission.ACKNOWLEDGE_ALERTS,
    ],
    Role.DBA_SUPERVISOR: [
        Permission.VIEW_DATABASE,
        Permission.VIEW_METRICS,
        Permission.VIEW_BASELINE,
        Permission.VIEW_PREDICTION,
        Permission.VIEW_HEALTH,
        Permission.VIEW_ALERTS,
        Permission.VIEW_AUDITLOGS,
        Permission.ACKNOWLEDGE_ALERTS,
        Permission.APPROVE_AUDITLOGS,
        Permission.EXECUTE_OPERATIONS,
        Permission.MANAGE_DATABASES,
    ],
    Role.ADMIN: [
        Permission.VIEW_DATABASE,
        Permission.VIEW_METRICS,
        Permission.VIEW_BASELINE,
        Permission.VIEW_PREDICTION,
        Permission.VIEW_HEALTH,
        Permission.VIEW_ALERTS,
        Permission.VIEW_AUDITLOGS,
        Permission.ACKNOWLEDGE_ALERTS,
        Permission.APPROVE_AUDITLOGS,
        Permission.EXECUTE_OPERATIONS,
        Permission.MANAGE_DATABASES,
        Permission.MANAGE_USERS,
        Permission.MANAGE_ROLES,
    ],
}


# =============================================================================
# Token 管理
# =============================================================================

class TokenManager:
    """Token 管理器"""
    
    # Token 有效期（小时）
    TOKEN_EXPIRY_HOURS = 24
    
    # Token 前缀
    TOKEN_PREFIX = 'dbm_token_'
    
    @classmethod
    def generate_token(cls, user_id: int) -> str:
        """
        生成用户 Token
        """
        # 生成随机 token
        random_part = secrets.token_hex(16)
        timestamp = str(int(time.time()))
        raw_token = f"{user_id}:{timestamp}:{random_part}"
        
        # 使用 HMAC 签名
        signature = hmac.new(
            settings.SECRET_KEY.encode(),
            raw_token.encode(),
            hashlib.sha256
        ).hexdigest()[:16]
        
        token = f"{random_part}{signature}"
        
        # 存储到缓存
        cache_key = f"{cls.TOKEN_PREFIX}{token}"
        cache.set(cache_key, {
            'user_id': user_id,
            'created_at': datetime.now().isoformat(),
            'expires_at': (datetime.now() + timedelta(hours=cls.TOKEN_EXPIRY_HOURS)).isoformat()
        }, timeout=cls.TOKEN_EXPIRY_HOURS * 3600)
        
        return token
    
    @classmethod
    def validate_token(cls, token: str) -> Optional[dict]:
        """
        验证 Token，返回用户信息
        """
        cache_key = f"{cls.TOKEN_PREFIX}{token}"
        data = cache.get(cache_key)
        
        if not data:
            return None
        
        # 检查是否过期
        expires_at = datetime.fromisoformat(data['expires_at'])
        if datetime.now() > expires_at:
            cache.delete(cache_key)
            return None
        
        return data
    
    @classmethod
    def revoke_token(cls, token: str) -> bool:
        """
        撤销 Token
        """
        cache_key = f"{cls.TOKEN_PREFIX}{token}"
        return cache.delete(cache_key) > 0


# =============================================================================
# 用户角色和权限查询
# =============================================================================

def get_user_role(user: User) -> str:
    """
    获取用户的角色
    """
    try:
        profile = UserProfile.objects.get(user=user)
        return profile.role
    except UserProfile.DoesNotExist:
        # 默认角色
        return Role.READ_ONLY_OBSERVER


def get_user_permissions(user: User) -> List[str]:
    """
    获取用户的权限列表
    """
    role = get_user_role(user)
    return ROLE_PERMISSIONS.get(role, [])


def has_permission(user: User, permission: str) -> bool:
    """
    检查用户是否拥有指定权限
    """
    permissions = get_user_permissions(user)
    return permission in permissions


def get_user_database_ids(user: User) -> Optional[List[int]]:
    """
    获取用户有权限访问的数据库ID列表
    
    Returns:
        None - 用户可以访问所有数据库
        List[int] - 用户只能访问这些数据库
    """
    try:
        profile = UserProfile.objects.get(user=user)
        if profile.allowed_databases:
            # 返回授权的数据库列表
            return profile.allowed_databases
        else:
            # 为空表示可以访问所有数据库
            return None
    except UserProfile.DoesNotExist:
        # 默认只能访问所有数据库（需要根据实际安全策略调整）
        return None


def is_admin(user: User) -> bool:
    """
    检查是否为管理员
    """
    return get_user_role(user) == Role.ADMIN


def is_supervisor_or_admin(user: User) -> bool:
    """
    检查是否为 DBA 主管或管理员
    """
    role = get_user_role(user)
    return role in [Role.DBA_SUPERVISOR, Role.ADMIN]


# =============================================================================
# 认证装饰器
# =============================================================================

def require_auth(func: Callable) -> Callable:
    """
    要求认证的装饰器
    """
    @wraps(func)
    def wrapper(request: HttpRequest, *args, **kwargs):
        # 优先从 Header 获取 Token
        auth_header = request.headers.get('Authorization', '')
        
        if auth_header.startswith('Bearer '):
            token = auth_header[7:]
        elif auth_header.startswith('Token '):
            token = auth_header[6:]
        else:
            # 尝试从 Cookie 获取
            token = request.COOKIES.get('auth_token', '')
        
        if not token:
            return JsonResponse({
                'error': 'Authentication required',
                'message': 'Please provide a valid token in Authorization header or auth_token cookie'
            }, status=401)
        
        # 验证 Token
        token_data = TokenManager.validate_token(token)
        if not token_data:
            return JsonResponse({
                'error': 'Invalid or expired token',
                'message': 'Please login again to get a new token'
            }, status=401)
        
        # 获取用户
        try:
            user = User.objects.get(id=token_data['user_id'])
        except User.DoesNotExist:
            return JsonResponse({
                'error': 'User not found',
                'message': 'The user associated with this token no longer exists'
            }, status=401)
        
        # 检查用户是否启用
        if not user.is_active:
            return JsonResponse({
                'error': 'User disabled',
                'message': 'This user account has been disabled'
            }, status=403)
        
        # 将用户附加到请求对象
        request.user = user  # 设置 request.user
        request.auth_token = token
        request.token_data = token_data
        
        return func(request, *args, **kwargs)
    
    return wrapper


def require_role(roles: List[str]) -> Callable:
    """
    要求特定角色的装饰器
    
    Args:
        roles: 允许的角色列表
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(request: HttpRequest, *args, **kwargs):
            # 先验证认证
            if not hasattr(request, 'auth_token'):
                return JsonResponse({
                    'error': 'Authentication required'
                }, status=401)
            
            # 检查角色
            user_role = get_user_role(request.user)
            if user_role not in roles:
                return JsonResponse({
                    'error': 'Permission denied',
                    'message': f'This action requires one of the following roles: {", ".join(roles)}'
                }, status=403)
            
            return func(request, *args, **kwargs)
        
        return wrapper
    return decorator


def require_permission(permission: str) -> Callable:
    """
    要求特定权限的装饰器
    
    Args:
        permission: 需要的权限
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(request: HttpRequest, *args, **kwargs):
            # 先验证认证
            if not hasattr(request, 'auth_token'):
                return JsonResponse({
                    'error': 'Authentication required'
                }, status=401)
            
            # 检查权限
            if not has_permission(request.user, permission):
                return JsonResponse({
                    'error': 'Permission denied',
                    'message': f'This action requires the following permission: {permission}'
                }, status=403)
            
            return func(request, *args, **kwargs)
        
        return wrapper
    return decorator


# =============================================================================
# 认证视图辅助函数
# =============================================================================

def login_user(username: str, password: str) -> Optional[dict]:
    """
    用户登录，返回 Token
    
    Args:
        username: 用户名
        password: 密码
        
    Returns:
        成功返回 {'token': xxx, 'user': {...}}
        失败返回 None
    """
    from django.contrib.auth import authenticate
    
    user = authenticate(username=username, password=password)
    if not user:
        return None
    
    if not user.is_active:
        return None
    
    token = TokenManager.generate_token(user.id)
    
    return {
        'token': token,
        'user': {
            'id': user.id,
            'username': user.username,
            'email': user.email,
            'role': get_user_role(user),
            'permissions': get_user_permissions(user)
        }
    }


def logout_user(token: str) -> bool:
    """
    用户登出，撤销 Token
    """
    return TokenManager.revoke_token(token)


# =============================================================================
# API Key 认证（用于外部系统集成）
# =============================================================================

class APIKeyAuth:
    """
    API Key 认证类
    """
    
    # API Key 缓存时间（秒）
    CACHE_TIMEOUT = 300
    
    @classmethod
    def generate_api_key(cls, name: str, user_id: int, permissions: List[str] = None) -> str:
        """
        为外部系统生成 API Key
        """
        import uuid
        api_key = f"dbm_{uuid.uuid4().hex}"
        
        # 存储到缓存
        cache_key = f"api_key_{api_key}"
        cache.set(cache_key, {
            'name': name,
            'user_id': user_id,
            'permissions': permissions or [],
            'created_at': datetime.now().isoformat()
        }, timeout=cls.CACHE_TIMEOUT)
        
        return api_key
    
    @classmethod
    def validate_api_key(cls, api_key: str) -> Optional[dict]:
        """
        验证 API Key
        """
        cache_key = f"api_key_{api_key}"
        return cache.get(cache_key)
    
    @classmethod
    def revoke_api_key(cls, api_key: str) -> bool:
        """
        撤销 API Key
        """
        cache_key = f"api_key_{api_key}"
        return cache.delete(cache_key) > 0