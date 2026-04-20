"""
多租户支持模块
==============

实现租户数据隔离和识别：
- 租户上下文管理
- 基于租户的查询过滤
- 租户感知装饰器

Author: DB-AIOps Team
"""

import threading
from typing import Optional, List, Any
from functools import wraps
from django.http import HttpRequest

# 线程本地存储用于保存当前租户上下文
_tenant_context = threading.local()


class TenantContext:
    """租户上下文管理器"""
    
    @staticmethod
    def set_tenant(tenant_id: int) -> None:
        """
        设置当前租户 ID
        
        Args:
            tenant_id: 租户 ID
        """
        _tenant_context.tenant_id = tenant_id
    
    @staticmethod
    def get_tenant() -> Optional[int]:
        """
        获取当前租户 ID
        
        Returns:
            租户 ID 或 None（如果是单租户模式）
        """
        return getattr(_tenant_context, 'tenant_id', None)
    
    @staticmethod
    def clear_tenant() -> None:
        """清除当前租户上下文"""
        _tenant_context.tenant_id = None


class TenantManager:
    """
    租户管理器
    
    提供租户相关操作：
    - 租户识别
    - 数据过滤
    - 权限检查
    """
    
    # 内置租户 ID
    DEFAULT_TENANT_ID = 1  # 默认租户（系统级）
    
    @classmethod
    def get_current_tenant_id(cls, user=None) -> Optional[int]:
        """
        获取当前用户对应的租户 ID
        
        Args:
            user: Django 用户对象
            
        Returns:
            租户 ID
        """
        if user is None:
            return cls.DEFAULT_TENANT_ID
        
        # 如果用户有租户关联，从用户配置获取
        if hasattr(user, 'userprofile'):
            try:
                profile = user.userprofile
                if hasattr(profile, 'tenant_id'):
                    return profile.tenant_id
            except:
                pass
        
        return cls.DEFAULT_TENANT_ID
    
    @classmethod
    def filter_by_tenant(cls, queryset, tenant_id: Optional[int] = None) -> Any:
        """
        按租户过滤 QuerySet
        
        Args:
            queryset: Django QuerySet
            tenant_id: 租户 ID
            
        Returns:
            过滤后的 QuerySet
        """
        if tenant_id is None:
            tenant_id = cls.get_current_tenant_id()
        
        # 如果模型有 tenant_id 字段
        if hasattr(queryset.model, 'tenant_id'):
            return queryset.filter(tenant_id=tenant_id)
        
        # 如果模型有 tenant 外键
        if hasattr(queryset.model, 'tenant'):
            return queryset.filter(tenant_id=tenant_id)
        
        return queryset
    
    @classmethod
    def require_tenant(cls, func):
        """
        要求租户上下文的装饰器
        
        确保在执行函数前设置了租户上下文
        """
        @wraps(func)
        def wrapper(*args, **kwargs):
            tenant_id = cls.get_current_tenant_id()
            if tenant_id is None:
                raise PermissionError("租户上下文未设置")
            return func(*args, **kwargs)
        return wrapper


def get_tenant_from_request(request: HttpRequest) -> Optional[int]:
    """
    从 HTTP 请求中提取租户 ID
    
    优先级：
    1. URL 参数中的 tenant_id
    2. Session 中的 tenant_id
    3. Header 中的 X-Tenant-ID
    4. 从用户配置推断
    
    Args:
        request: Django HTTP 请求
        
    Returns:
        租户 ID
    """
    # 1. URL 参数
    tenant_id = request.GET.get('tenant_id')
    if tenant_id:
        try:
            return int(tenant_id)
        except (ValueError, TypeError):
            pass
    
    # 2. Session
    if hasattr(request, 'session'):
        tenant_id = request.session.get('tenant_id')
        if tenant_id:
            return tenant_id
    
    # 3. Header
    tenant_id = request.headers.get('X-Tenant-ID')
    if tenant_id:
        try:
            return int(tenant_id)
        except (ValueError, TypeError):
            pass
    
    # 4. 从用户配置推断
    if request.user.is_authenticated:
        return TenantManager.get_current_tenant_id(request.user)
    
    return TenantManager.DEFAULT_TENANT_ID


class TenantAwareQuerySet:
    """
    租户感知的 QuerySet 包装器
    
    自动为所有查询添加租户过滤条件
    """
    
    def __init__(self, queryset, tenant_id: Optional[int] = None):
        """
        初始化
        
        Args:
            queryset: Django QuerySet
            tenant_id: 租户 ID
        """
        self.queryset = queryset
        self.tenant_id = tenant_id or TenantManager.get_current_tenant_id()
    
    def _apply_tenant_filter(self):
        """应用租户过滤"""
        model = self.queryset.model
        
        # 检查模型是否有 tenant_id 字段
        if hasattr(model, 'tenant_id'):
            self.queryset = self.queryset.filter(tenant_id=self.tenant_id)
        elif hasattr(model, 'tenant'):
            self.queryset = self.queryset.filter(tenant_id=self.tenant_id)
        
        return self.queryset
    
    def all(self):
        """返回所有（已过滤）"""
        return self._apply_tenant_filter()
    
    def filter(self, *args, **kwargs):
        """过滤（保留租户条件）"""
        self._apply_tenant_filter()
        return self.queryset.filter(*args, **kwargs)
    
    def get(self, *args, **kwargs):
        """获取（保留租户条件）"""
        self._apply_tenant_filter()
        return self.queryset.get(*args, **kwargs)
    
    def exclude(self, *args, **kwargs):
        """排除（保留租户条件）"""
        self._apply_tenant_filter()
        return self.queryset.exclude(*args, **kwargs)
    
    def first(self):
        """第一个（已过滤）"""
        return self.all().first()
    
    def last(self):
        """最后一个（已过滤）"""
        return self.all().last()
    
    def count(self):
        """计数（已过滤）"""
        return self.all().count()
    
    def exists(self):
        """是否存在（已过滤）"""
        return self.all().exists()


def tenant_required(func):
    """
    租户必需装饰器
    
    用于视图函数，确保请求包含有效的租户信息
    """
    @wraps(func)
    def wrapper(request, *args, **kwargs):
        tenant_id = get_tenant_from_request(request)
        TenantContext.set_tenant(tenant_id)
        try:
            return func(request, *args, **kwargs)
        finally:
            TenantContext.clear_tenant()
    return wrapper


class TenantMiddleware:
    """
    租户中间件
    
    自动为每个请求设置租户上下文
    """
    
    def __init__(self, get_response):
        self.get_response = get_response
    
    def __call__(self, request):
        # 设置租户上下文
        tenant_id = get_tenant_from_request(request)
        TenantContext.set_tenant(tenant_id)
        request.tenant_id = tenant_id
        
        try:
            response = self.get_response(request)
            return response
        finally:
            # 清理租户上下文
            if not getattr(request, '_tenant_preserved', False):
                TenantContext.clear_tenant()
    
    def process_view(self, request, view_func, *args, **kwargs):
        """在视图处理前设置租户"""
        if not hasattr(request, 'tenant_id'):
            tenant_id = get_tenant_from_request(request)
            request.tenant_id = tenant_id


def is_super_tenant(tenant_id: int) -> bool:
    """
    判断是否为超级租户
    
    Args:
        tenant_id: 租户 ID
        
    Returns:
        是否为超级租户
    """
    return tenant_id == TenantManager.DEFAULT_TENANT_ID


def get_tenant_database_filter(tenant_id: Optional[int], user_is_admin: bool = False) -> dict:
    """
    获取租户数据库过滤条件
    
    Args:
        tenant_id: 租户 ID
        user_is_admin: 用户是否为管理员
        
    Returns:
        过滤条件字典
    """
    # 管理员可以访问所有数据库
    if user_is_admin:
        return {}
    
    # 普通租户只能访问自己的数据库
    if tenant_id is None:
        tenant_id = TenantManager.DEFAULT_TENANT_ID
    
    return {'tenant_id': tenant_id}
