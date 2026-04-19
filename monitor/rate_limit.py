# -*- coding: utf-8 -*-
"""
API Rate Limiting Middleware for DB Monitor
Phase E2: API rate limiting middleware
"""

import time
import hashlib
from collections import defaultdict
from typing import Dict, Tuple, Optional
from django.http import JsonResponse


class RateLimiter:
    """
    Token bucket rate limiter implementation.
    """
    
    def __init__(self, rate: int = 100, per: int = 60):
        """
        Initialize rate limiter.
        
        Args:
            rate: Number of requests allowed per time window
            per: Time window in seconds
        """
        self.rate = rate
        self.per = per
        self.buckets: Dict[str, Tuple[int, float]] = defaultdict(lambda: (rate, time.time()))
    
    def allow_request(self, key: str) -> bool:
        """
        Check if request should be allowed.
        
        Args:
            key: Unique identifier for the client (IP, API key, etc.)
        
        Returns:
            True if request is allowed, False otherwise
        """
        current_time = time.time()
        tokens, last_update = self.buckets[key]
        
        # Calculate tokens to add based on time elapsed
        elapsed = current_time - last_update
        tokens_to_add = (elapsed / self.per) * self.rate
        tokens = min(self.rate, tokens + tokens_to_add)
        
        if tokens >= 1:
            tokens -= 1
            self.buckets[key] = (tokens, current_time)
            return True
        
        self.buckets[key] = (tokens, current_time)
        return False
    
    def get_retry_after(self, key: str) -> int:
        """
        Get seconds until next request is allowed.
        """
        tokens, _ = self.buckets[key]
        if tokens >= 1:
            return 0
        return int((1 - tokens) * self.per / self.rate) + 1


class RateLimitMiddleware:
    """
    Django middleware for API rate limiting.
    
    Configuration (in settings.py):
        API_RATE_LIMIT = 100  # requests per window
        API_RATE_WINDOW = 60   # window in seconds
    """
    
    def __init__(self, get_response):
        self.get_response = get_response
        self.limiter = None
        self.exempt_paths = ['/admin/', '/health/', '/favicon.ico']
    
    def __call__(self, request):
        # Lazy initialization to get settings
        if self.limiter is None:
            from django.conf import settings
            rate = getattr(settings, 'API_RATE_LIMIT', 100)
            window = getattr(settings, 'API_RATE_WINDOW', 60)
            self.limiter = RateLimiter(rate=rate, per=window)
            
            # Get exempt paths
            self.exempt_paths = getattr(settings, 'API_RATE_EXEMPT_PATHS', 
                                        ['/admin/', '/health/', '/favicon.ico'])
        
        # Check if path is exempt
        path = request.path
        for exempt in self.exempt_paths:
            if path.startswith(exempt):
                return self.get_response(request)
        
        # Get client identifier
        client_key = self._get_client_key(request)
        
        # Check rate limit
        if not self.limiter.allow_request(client_key):
            retry_after = self.limiter.get_retry_after(client_key)
            return JsonResponse({
                'error': 'Rate limit exceeded',
                'retry_after': retry_after
            }, status=429)
        
        response = self.get_response(request)
        
        # Add rate limit headers
        remaining = self.limiter.buckets.get(client_key, (self.limiter.rate, time.time()))[0]
        response['X-RateLimit-Limit'] = str(self.limiter.rate)
        response['X-RateLimit-Remaining'] = str(int(remaining))
        
        return response
    
    def _get_client_key(self, request) -> str:
        """
        Get unique identifier for the client.
        Uses X-Forwarded-For if behind proxy, otherwise REMOTE_ADDR.
        """
        x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
        if x_forwarded_for:
            ip = x_forwarded_for.split(',')[0].strip()
        else:
            ip = request.META.get('REMOTE_ADDR', 'unknown')
        
        # Also consider API key if present
        api_key = request.META.get('HTTP_X_API_KEY', '')
        if api_key:
            return hashlib.sha256(f"{ip}:{api_key}".encode()).hexdigest()[:16]
        
        return ip


class APIKeyRateLimitMiddleware(RateLimitMiddleware):
    """
    Rate limiting middleware that requires API key.
    """
    
    def _get_client_key(self, request) -> str:
        """
        Get client key based on API key.
        """
        api_key = request.META.get('HTTP_X_API_KEY', '')
        if not api_key:
            # Fall back to IP if no API key
            return super()._get_client_key(request)
        
        return hashlib.sha256(api_key.encode()).hexdigest()[:16]
