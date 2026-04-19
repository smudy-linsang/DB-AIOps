# -*- coding: utf-8 -*-
"""
Redis caching utility for DB Monitor
Phase E1: Redis caching integration
"""

import json
import hashlib
from typing import Any, Optional, Callable
from functools import wraps

# Redis client will be initialized lazily
_redis_client = None


def get_redis_client():
    """
    Get or create Redis client instance.
    Returns None if Redis is not available.
    """
    global _redis_client
    
    if _redis_client is not None:
        return _redis_client
    
    try:
        import redis
        from django.conf import settings
        
        redis_host = getattr(settings, 'REDIS_HOST', 'localhost')
        redis_port = getattr(settings, 'REDIS_PORT', 6379)
        redis_db = getattr(settings, 'REDIS_DB', 0)
        
        _redis_client = redis.Redis(
            host=redis_host,
            port=redis_port,
            db=redis_db,
            decode_responses=True,
            socket_connect_timeout=5,
            socket_timeout=5
        )
        
        # Test connection
        _redis_client.ping()
        return _redis_client
        
    except Exception as e:
        print(f"Redis connection failed: {e}")
        return None


def generate_cache_key(prefix: str, *args, **kwargs) -> str:
    """
    Generate a cache key from prefix and arguments.
    """
    key_parts = [prefix] + [str(arg) for arg in args]
    
    if kwargs:
        kwargs_str = json.dumps(kwargs, sort_keys=True, default=str)
        kwargs_hash = hashlib.md5(kwargs_str.encode()).hexdigest()[:8]
        key_parts.append(kwargs_hash)
    
    return ":".join(key_parts)


def cached(timeout: int = 300, key_prefix: str = "dbm"):
    """
    Decorator to cache function results in Redis.
    
    Args:
        timeout: Cache expiration time in seconds (default: 300 = 5 minutes)
        key_prefix: Prefix for the cache key
    
    Example:
        @cached(timeout=60, key_prefix="metrics")
        def get_metrics(config_id):
            ...
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs):
            # Generate cache key
            cache_key = generate_cache_key(key_prefix, func.__name__, *args, **kwargs)
            
            # Try to get from cache
            redis = get_redis_client()
            if redis is not None:
                try:
                    cached_value = redis.get(cache_key)
                    if cached_value is not None:
                        return json.loads(cached_value)
                except Exception:
                    pass  # Ignore cache errors
            
            # Call the function
            result = func(*args, **kwargs)
            
            # Store in cache
            if redis is not None and result is not None:
                try:
                    redis.setex(
                        cache_key,
                        timeout,
                        json.dumps(result, default=str)
                    )
                except Exception:
                    pass  # Ignore cache errors
            
            return result
        
        return wrapper
    return decorator


def invalidate_cache(pattern: str = "*") -> int:
    """
    Invalidate cache keys matching the pattern.
    
    Args:
        pattern: Redis key pattern (default: "*" = all keys)
    
    Returns:
        Number of keys deleted
    """
    redis = get_redis_client()
    if redis is None:
        return 0
    
    try:
        keys = redis.keys(pattern)
        if keys:
            return redis.delete(*keys)
        return 0
    except Exception:
        return 0


class CacheManager:
    """
    Cache manager for explicit cache control.
    """
    
    def __init__(self):
        self.redis = get_redis_client()
    
    def get(self, key: str) -> Optional[Any]:
        """Get value from cache"""
        if self.redis is None:
            return None
        
        try:
            value = self.redis.get(key)
            if value:
                return json.loads(value)
        except Exception:
            pass
        return None
    
    def set(self, key: str, value: Any, timeout: int = 300) -> bool:
        """Set value in cache with expiration"""
        if self.redis is None:
            return False
        
        try:
            self.redis.setex(
                key,
                timeout,
                json.dumps(value, default=str)
            )
            return True
        except Exception:
            return False
    
    def delete(self, key: str) -> bool:
        """Delete key from cache"""
        if self.redis is None:
            return False
        
        try:
            self.redis.delete(key)
            return True
        except Exception:
            return False
    
    def exists(self, key: str) -> bool:
        """Check if key exists in cache"""
        if self.redis is None:
            return False
        
        try:
            return self.redis.exists(key) > 0
        except Exception:
            return False
