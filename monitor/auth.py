"""
认证与权限控制模块
==================

RBAC 细粒度权限系统 v2.0

核心设计：
- 5 个内置角色：super_admin / dba / auditor / config_operator / readonly
- 35 个细粒度权限编码（module.action 格式）
- 权限数据存储在 RolePermission 表中，支持动态配置
- super_admin 角色跳过权限检查（类似 root），防止锁死
- 数据范围过滤（allowed_databases）与角色权限解耦

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
# 角色编码常量
# =============================================================================

class RoleCode:
    """角色编码常量"""
    SUPER_ADMIN = 'super_admin'          # 超级管理员
    DBA = 'dba'                          # 数据库管理员
    AUDITOR = 'auditor'                  # 审计用户
    CONFIG_OPERATOR = 'config_operator'  # 配置用户
    READONLY = 'readonly'                # 只读用户


# =============================================================================
# 权限编码常量
# =============================================================================

class Perm:
    """权限编码常量 - 格式: module.action"""
    # -- 仪表盘 --
    DASHBOARD_VIEW = 'dashboard.view'

    # -- 数据库管理 --
    DATABASES_VIEW = 'databases.view'
    DATABASES_CREATE = 'databases.create'
    DATABASES_UPDATE = 'databases.update'
    DATABASES_DELETE = 'databases.delete'
    DATABASES_TEST_CONNECTION = 'databases.test_connection'
    DATABASES_TOGGLE_ACTIVE = 'databases.toggle_active'

    # -- 数据库详情 --
    DATABASE_DETAIL_VIEW = 'database_detail.view'

    # -- 监控指标 --
    METRICS_VIEW = 'metrics.view'

    # -- 基线分析 --
    BASELINE_VIEW = 'baseline.view'

    # -- 容量预测 --
    PREDICTION_VIEW = 'prediction.view'
    PREDICTION_EXECUTE = 'prediction.execute'

    # -- 健康评分 --
    HEALTH_VIEW = 'health.view'

    # -- 告警 --
    ALERTS_VIEW = 'alerts.view'
    ALERTS_ACKNOWLEDGE = 'alerts.acknowledge'
    ALERTS_DELETE = 'alerts.delete'

    # -- 告警配置 --
    ALERT_CONFIG_VIEW = 'alert_config.view'
    ALERT_CONFIG_MANAGE = 'alert_config.manage'

    # -- 工单 --
    TICKETS_VIEW = 'tickets.view'
    TICKETS_CREATE = 'tickets.create'
    TICKETS_APPROVE = 'tickets.approve'
    TICKETS_EXECUTE = 'tickets.execute'

    # -- SQL 监控 --
    SQL_MONITORING_VIEW = 'sql_monitoring.view'

    # -- 容量规划 --
    CAPACITY_VIEW = 'capacity.view'
    CAPACITY_PREDICT = 'capacity.predict'

    # -- 通知设置 --
    NOTIFICATION_VIEW = 'notification.view'
    NOTIFICATION_MANAGE = 'notification.manage'

    # -- 业务拓扑 --
    BUSINESS_TOPOLOGY_VIEW = 'business_topology.view'
    BUSINESS_TOPOLOGY_MANAGE = 'business_topology.manage'

    # -- 报表 --
    REPORTS_VIEW = 'reports.view'
    REPORTS_GENERATE = 'reports.generate'

    # -- 审计日志 --
    AUDIT_LOGS_VIEW = 'audit_logs.view'

    # -- 用户管理 --
    USERS_VIEW = 'users.view'
    USERS_MANAGE = 'users.manage'

    # -- 角色管理 --
    ROLES_VIEW = 'roles.view'
    ROLES_MANAGE = 'roles.manage'


# =============================================================================
# 权限元数据（用于前端展示和初始化）
# =============================================================================

PERMISSION_META = {
    # -- 仪表盘 --
    Perm.DASHBOARD_VIEW: '查看仪表盘',
    # -- 数据库管理 --
    Perm.DATABASES_VIEW: '查看数据库列表',
    Perm.DATABASES_CREATE: '新增数据库配置',
    Perm.DATABASES_UPDATE: '修改数据库配置',
    Perm.DATABASES_DELETE: '删除数据库配置',
    Perm.DATABASES_TEST_CONNECTION: '测试数据库连接',
    Perm.DATABASES_TOGGLE_ACTIVE: '启停数据库监控',
    # -- 数据库详情 --
    Perm.DATABASE_DETAIL_VIEW: '查看数据库详情',
    # -- 监控指标 --
    Perm.METRICS_VIEW: '查看监控指标',
    # -- 基线分析 --
    Perm.BASELINE_VIEW: '查看基线分析',
    # -- 容量预测 --
    Perm.PREDICTION_VIEW: '查看容量预测',
    Perm.PREDICTION_EXECUTE: '执行即时预测',
    # -- 健康评分 --
    Perm.HEALTH_VIEW: '查看健康评分',
    # -- 告警 --
    Perm.ALERTS_VIEW: '查看告警列表',
    Perm.ALERTS_ACKNOWLEDGE: '确认告警',
    Perm.ALERTS_DELETE: '删除告警',
    # -- 告警配置 --
    Perm.ALERT_CONFIG_VIEW: '查看告警配置',
    Perm.ALERT_CONFIG_MANAGE: '管理告警模板和规则',
    # -- 工单 --
    Perm.TICKETS_VIEW: '查看工单',
    Perm.TICKETS_CREATE: '创建工单',
    Perm.TICKETS_APPROVE: '审批工单',
    Perm.TICKETS_EXECUTE: '执行工单',
    # -- SQL 监控 --
    Perm.SQL_MONITORING_VIEW: '查看SQL监控',
    # -- 容量规划 --
    Perm.CAPACITY_VIEW: '查看容量规划',
    Perm.CAPACITY_PREDICT: '执行容量预测',
    # -- 通知设置 --
    Perm.NOTIFICATION_VIEW: '查看通知设置',
    Perm.NOTIFICATION_MANAGE: '管理通知规则',
    # -- 业务拓扑 --
    Perm.BUSINESS_TOPOLOGY_VIEW: '查看业务拓扑',
    Perm.BUSINESS_TOPOLOGY_MANAGE: '管理业务系统和拓扑',
    # -- 报表 --
    Perm.REPORTS_VIEW: '查看报表',
    Perm.REPORTS_GENERATE: '生成报表',
    # -- 审计日志 --
    Perm.AUDIT_LOGS_VIEW: '查看审计日志',
    # -- 用户管理 --
    Perm.USERS_VIEW: '查看用户列表',
    Perm.USERS_MANAGE: '管理用户（增删改角色）',
    # -- 角色管理 --
    Perm.ROLES_VIEW: '查看角色权限',
    Perm.ROLES_MANAGE: '管理角色权限',
}

# 权限分组（前端权限矩阵展示用）
PERMISSION_GROUPS = [
    {'group': '仪表盘', 'permissions': [Perm.DASHBOARD_VIEW]},
    {'group': '数据库管理', 'permissions': [
        Perm.DATABASES_VIEW, Perm.DATABASES_CREATE, Perm.DATABASES_UPDATE,
        Perm.DATABASES_DELETE, Perm.DATABASES_TEST_CONNECTION, Perm.DATABASES_TOGGLE_ACTIVE,
        Perm.DATABASE_DETAIL_VIEW, Perm.METRICS_VIEW,
    ]},
    {'group': '智能分析', 'permissions': [
        Perm.BASELINE_VIEW, Perm.PREDICTION_VIEW, Perm.PREDICTION_EXECUTE,
        Perm.HEALTH_VIEW,
    ]},
    {'group': '告警管理', 'permissions': [
        Perm.ALERTS_VIEW, Perm.ALERTS_ACKNOWLEDGE, Perm.ALERTS_DELETE,
        Perm.ALERT_CONFIG_VIEW, Perm.ALERT_CONFIG_MANAGE,
    ]},
    {'group': '工单管理', 'permissions': [
        Perm.TICKETS_VIEW, Perm.TICKETS_CREATE, Perm.TICKETS_APPROVE,
        Perm.TICKETS_EXECUTE,
    ]},
    {'group': 'SQL 监控', 'permissions': [Perm.SQL_MONITORING_VIEW]},
    {'group': '容量规划', 'permissions': [Perm.CAPACITY_VIEW, Perm.CAPACITY_PREDICT]},
    {'group': '通知设置', 'permissions': [Perm.NOTIFICATION_VIEW, Perm.NOTIFICATION_MANAGE]},
    {'group': '业务拓扑', 'permissions': [
        Perm.BUSINESS_TOPOLOGY_VIEW, Perm.BUSINESS_TOPOLOGY_MANAGE,
    ]},
    {'group': '报表中心', 'permissions': [Perm.REPORTS_VIEW, Perm.REPORTS_GENERATE]},
    {'group': '审计日志', 'permissions': [Perm.AUDIT_LOGS_VIEW]},
    {'group': '用户管理', 'permissions': [Perm.USERS_VIEW, Perm.USERS_MANAGE]},
    {'group': '角色管理', 'permissions': [Perm.ROLES_VIEW, Perm.ROLES_MANAGE]},
]

# =============================================================================
# 内置角色权限映射（用于 init_roles 命令初始化）
# =============================================================================

BUILTIN_ROLE_PERMISSIONS = {
    RoleCode.SUPER_ADMIN: list(PERMISSION_META.keys()),  # 全部权限
    RoleCode.DBA: [
        Perm.DASHBOARD_VIEW,
        Perm.DATABASES_VIEW, Perm.DATABASES_TEST_CONNECTION, Perm.DATABASE_DETAIL_VIEW,
        Perm.METRICS_VIEW, Perm.BASELINE_VIEW,
        Perm.PREDICTION_VIEW, Perm.PREDICTION_EXECUTE,
        Perm.HEALTH_VIEW,
        Perm.ALERTS_VIEW, Perm.ALERTS_ACKNOWLEDGE, Perm.ALERTS_DELETE,
        Perm.ALERT_CONFIG_VIEW,
        Perm.TICKETS_VIEW, Perm.TICKETS_CREATE, Perm.TICKETS_APPROVE, Perm.TICKETS_EXECUTE,
        Perm.SQL_MONITORING_VIEW,
        Perm.CAPACITY_VIEW, Perm.CAPACITY_PREDICT,
        Perm.NOTIFICATION_VIEW,
        Perm.BUSINESS_TOPOLOGY_VIEW,
        Perm.REPORTS_VIEW, Perm.REPORTS_GENERATE,
        Perm.AUDIT_LOGS_VIEW,
    ],
    RoleCode.AUDITOR: [
        Perm.DASHBOARD_VIEW,
        Perm.DATABASES_VIEW, Perm.DATABASE_DETAIL_VIEW,
        Perm.METRICS_VIEW, Perm.BASELINE_VIEW,
        Perm.PREDICTION_VIEW,
        Perm.HEALTH_VIEW,
        Perm.ALERTS_VIEW,
        Perm.ALERT_CONFIG_VIEW,
        Perm.TICKETS_VIEW,
        Perm.SQL_MONITORING_VIEW,
        Perm.CAPACITY_VIEW,
        Perm.NOTIFICATION_VIEW,
        Perm.BUSINESS_TOPOLOGY_VIEW,
        Perm.REPORTS_VIEW,
        Perm.AUDIT_LOGS_VIEW,
        Perm.ROLES_VIEW,
    ],
    RoleCode.CONFIG_OPERATOR: [
        Perm.DASHBOARD_VIEW,
        Perm.DATABASES_VIEW, Perm.DATABASES_CREATE, Perm.DATABASES_UPDATE,
        Perm.DATABASES_TEST_CONNECTION, Perm.DATABASES_TOGGLE_ACTIVE,
        Perm.DATABASE_DETAIL_VIEW,
        Perm.METRICS_VIEW, Perm.BASELINE_VIEW,
        Perm.PREDICTION_VIEW,
        Perm.HEALTH_VIEW,
        Perm.ALERTS_VIEW,
        Perm.ALERT_CONFIG_VIEW, Perm.ALERT_CONFIG_MANAGE,
        Perm.SQL_MONITORING_VIEW,
        Perm.CAPACITY_VIEW,
        Perm.NOTIFICATION_VIEW, Perm.NOTIFICATION_MANAGE,
        Perm.BUSINESS_TOPOLOGY_VIEW, Perm.BUSINESS_TOPOLOGY_MANAGE,
        Perm.REPORTS_VIEW,
    ],
    RoleCode.READONLY: [
        Perm.DASHBOARD_VIEW,
        Perm.DATABASES_VIEW, Perm.DATABASE_DETAIL_VIEW,
        Perm.METRICS_VIEW,
        Perm.HEALTH_VIEW,
        Perm.ALERTS_VIEW,
    ],
}

# 内置角色元数据
BUILTIN_ROLES_META = {
    RoleCode.SUPER_ADMIN: {'name': '超级管理员', 'description': '拥有所有权限，可管理用户和角色，不可删除'},
    RoleCode.DBA: {'name': '数据库管理员', 'description': '数据库运维操作、告警确认、工单审批、配置管理'},
    RoleCode.AUDITOR: {'name': '审计用户', 'description': '只读查看所有数据、查看审计日志和报表，不能修改'},
    RoleCode.CONFIG_OPERATOR: {'name': '配置用户', 'description': '可管理数据库配置、告警配置、通知规则，不能执行运维操作'},
    RoleCode.READONLY: {'name': '只读用户', 'description': '仅查看仪表盘和基础监控数据'},
}

# 菜单路由与权限的映射（前端菜单过滤用）
MENU_PERMISSION_MAP = {
    '/': Perm.DASHBOARD_VIEW,
    '/databases': Perm.DATABASES_VIEW,
    '/alerts': Perm.ALERTS_VIEW,
    '/alert-config': Perm.ALERT_CONFIG_VIEW,
    '/sql-monitoring': Perm.SQL_MONITORING_VIEW,
    '/capacity': Perm.CAPACITY_VIEW,
    '/tickets': Perm.TICKETS_VIEW,
    '/notification-settings': Perm.NOTIFICATION_VIEW,
    '/business-systems': Perm.BUSINESS_TOPOLOGY_VIEW,
    '/reports': Perm.REPORTS_VIEW,
    '/user-management': Perm.USERS_VIEW,
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

def get_user_role_code(user: User) -> Optional[str]:
    """
    获取用户的角色编码
    """
    try:
        profile = UserProfile.objects.get(user=user)
        if profile.role:
            return profile.role.code
    except UserProfile.DoesNotExist:
        pass
    return None


def get_user_permissions(user: User) -> List[str]:
    """
    获取用户的权限列表（从数据库读取）
    super_admin 直接返回全部权限，不查数据库
    """
    # 超级管理员拥有全部权限
    if is_super_admin(user):
        return list(PERMISSION_META.keys())

    try:
        profile = UserProfile.objects.get(user=user)
        if profile.role:
            # 从 RolePermission 表查询
            perm_codes = list(
                profile.role.permissions.values_list('permission_code', flat=True)
            )
            return perm_codes
    except UserProfile.DoesNotExist:
        pass
    return []


def has_permission(user: User, permission: str) -> bool:
    """
    检查用户是否拥有指定权限
    super_admin 跳过检查，始终返回 True
    """
    if is_super_admin(user):
        return True
    permissions = get_user_permissions(user)
    return permission in permissions


def has_any_permission(user: User, permissions: List[str]) -> bool:
    """
    检查用户是否拥有列表中任意一个权限（OR 语义）
    """
    if is_super_admin(user):
        return True
    user_perms = get_user_permissions(user)
    return bool(set(user_perms) & set(permissions))


def has_all_permissions(user: User, permissions: List[str]) -> bool:
    """
    检查用户是否拥有列表中所有权限（AND 语义）
    """
    if is_super_admin(user):
        return True
    user_perms = get_user_permissions(user)
    return set(permissions).issubset(set(user_perms))


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
            return profile.allowed_databases
        else:
            return None
    except UserProfile.DoesNotExist:
        return None


def is_super_admin(user: User) -> bool:
    """
    检查是否为超级管理员
    """
    return get_user_role_code(user) == RoleCode.SUPER_ADMIN


def is_admin(user: User) -> bool:
    """
    检查是否为超级管理员（向后兼容）
    """
    return is_super_admin(user)


def is_supervisor_or_admin(user: User) -> bool:
    """
    检查是否为 DBA 或管理员
    """
    role_code = get_user_role_code(user)
    return role_code in [RoleCode.SUPER_ADMIN, RoleCode.DBA]


def get_user_menu_permissions(user: User) -> dict:
    """
    获取用户的菜单可见性和权限编码列表，供前端使用

    Returns:
        {
            'permissions': ['databases.view', 'databases.create', ...],
            'menus': ['/', '/databases', '/alerts', ...],
            'role_code': 'super_admin',
            'role_name': '超级管理员',
        }
    """
    permissions = get_user_permissions(user)
    role_code = get_user_role_code(user)
    role_name = ''

    try:
        profile = UserProfile.objects.get(user=user)
        if profile.role:
            role_name = profile.role.name
    except UserProfile.DoesNotExist:
        pass

    # 根据权限过滤可见菜单
    visible_menus = []
    perm_set = set(permissions)
    for menu_path, required_perm in MENU_PERMISSION_MAP.items():
        if required_perm in perm_set:
            visible_menus.append(menu_path)

    return {
        'permissions': permissions,
        'menus': visible_menus,
        'role_code': role_code,
        'role_name': role_name,
    }


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
        request.user = user
        request.auth_token = token
        request.token_data = token_data

        return func(request, *args, **kwargs)

    return wrapper


def require_role(roles: List[str]) -> Callable:
    """
    要求特定角色的装饰器（基于角色编码）

    Args:
        roles: 允许的角色编码列表
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(request: HttpRequest, *args, **kwargs):
            if not hasattr(request, 'auth_token'):
                return JsonResponse({'error': 'Authentication required'}, status=401)

            role_code = get_user_role_code(request.user)
            # 超级管理员跳过角色检查
            if role_code == RoleCode.SUPER_ADMIN:
                return func(request, *args, **kwargs)

            if role_code not in roles:
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
        permission: 需要的权限编码
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(request: HttpRequest, *args, **kwargs):
            if not hasattr(request, 'auth_token'):
                return JsonResponse({'error': 'Authentication required'}, status=401)

            if not has_permission(request.user, permission):
                return JsonResponse({
                    'error': 'Permission denied',
                    'message': f'This action requires the following permission: {permission}'
                }, status=403)

            return func(request, *args, **kwargs)
        return wrapper
    return decorator


def require_permissions(permissions: List[str]) -> Callable:
    """
    要求所有指定权限的装饰器（AND 语义）

    Args:
        permissions: 需要的所有权限编码列表
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(request: HttpRequest, *args, **kwargs):
            if not hasattr(request, 'auth_token'):
                return JsonResponse({'error': 'Authentication required'}, status=401)

            if not has_all_permissions(request.user, permissions):
                return JsonResponse({
                    'error': 'Permission denied',
                    'message': f'This action requires all of the following permissions: {", ".join(permissions)}'
                }, status=403)

            return func(request, *args, **kwargs)
        return wrapper
    return decorator


def require_any_permission(permissions: List[str]) -> Callable:
    """
    要求任意一个权限的装饰器（OR 语义）

    Args:
        permissions: 允许的权限编码列表（任一即可）
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(request: HttpRequest, *args, **kwargs):
            if not hasattr(request, 'auth_token'):
                return JsonResponse({'error': 'Authentication required'}, status=401)

            if not has_any_permission(request.user, permissions):
                return JsonResponse({
                    'error': 'Permission denied',
                    'message': f'This action requires at least one of the following permissions: {", ".join(permissions)}'
                }, status=403)

            return func(request, *args, **kwargs)
        return wrapper
    return decorator


# =============================================================================
# 认证视图辅助函数
# =============================================================================

def login_user(username: str, password: str) -> Optional[dict]:
    """
    用户登录，返回 Token + 权限信息

    Args:
        username: 用户名
        password: 密码

    Returns:
        成功返回 {'token': xxx, 'user': {..., permissions, menus}}
        失败返回 None
    """
    from django.contrib.auth import authenticate

    user = authenticate(username=username, password=password)
    if not user:
        return None

    if not user.is_active:
        return None

    token = TokenManager.generate_token(user.id)

    # 获取权限和菜单信息
    menu_perms = get_user_menu_permissions(user)

    return {
        'token': token,
        'user': {
            'id': user.id,
            'username': user.username,
            'email': user.email,
            'role': menu_perms['role_code'],
            'role_name': menu_perms['role_name'],
            'permissions': menu_perms['permissions'],
            'menus': menu_perms['menus'],
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