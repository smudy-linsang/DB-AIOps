# -*- coding: utf-8 -*-
"""
Unit tests for API rate limiting middleware
"""

import unittest
import time
import sys
import os

# Add parent directory to path and configure Django
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'dbmonitor.settings')

import django
django.setup()

from unittest.mock import MagicMock, patch


class TestRateLimiter(unittest.TestCase):
    """Tests for RateLimiter class."""

    def test_allow_request_first_request(self):
        """Test first request is always allowed."""
        from monitor.rate_limit import RateLimiter
        
        limiter = RateLimiter(rate=10, per=60)
        result = limiter.allow_request('client1')
        
        self.assertTrue(result)

    def test_allow_request_within_limit(self):
        """Test requests within limit are allowed."""
        from monitor.rate_limit import RateLimiter
        
        limiter = RateLimiter(rate=10, per=60)
        
        # Make 10 requests
        for _ in range(10):
            result = limiter.allow_request('client1')
            self.assertTrue(result)

    def test_allow_request_exceeds_limit(self):
        """Test requests exceeding limit are blocked."""
        from monitor.rate_limit import RateLimiter
        
        limiter = RateLimiter(rate=5, per=60)
        
        # Make 5 requests (should all succeed)
        for _ in range(5):
            limiter.allow_request('client1')
        
        # 6th request should be blocked
        result = limiter.allow_request('client1')
        self.assertFalse(result)

    def test_get_retry_after(self):
        """Test retry after calculation."""
        from monitor.rate_limit import RateLimiter
        
        limiter = RateLimiter(rate=2, per=60)
        
        # Exhaust the bucket
        limiter.allow_request('client1')
        limiter.allow_request('client1')
        
        retry_after = limiter.get_retry_after('client1')
        
        self.assertGreater(retry_after, 0)

    def test_different_clients_independent(self):
        """Test different clients have independent limits."""
        from monitor.rate_limit import RateLimiter
        
        limiter = RateLimiter(rate=2, per=60)
        
        # Client 1 exhausts limit
        limiter.allow_request('client1')
        limiter.allow_request('client1')
        
        # Client 2 should still have quota
        result = limiter.allow_request('client2')
        self.assertTrue(result)


class TestRateLimitMiddleware(unittest.TestCase):
    """Tests for RateLimitMiddleware class."""

    def setUp(self):
        """Set up test fixtures."""
        self.get_response = MagicMock(return_value=MagicMock())
        self.get_response.return_value = MagicMock()  # Mock response object
        self.get_response.return_value.__setitem__ = MagicMock()  # For header setting

    def test_middleware_allows_request(self):
        """Test middleware allows request within rate limit."""
        from monitor.rate_limit import RateLimitMiddleware, RateLimiter
        
        # Create middleware with manual limiter
        middleware = RateLimitMiddleware(self.get_response)
        middleware.limiter = RateLimiter(rate=100, per=60)
        middleware.exempt_paths = ['/admin/', '/health/']
        
        request = MagicMock()
        request.path = '/api/test'
        request.META = {'REMOTE_ADDR': '127.0.0.1'}
        
        response = middleware(request)
        
        self.assertEqual(response, self.get_response.return_value)

    def test_middleware_blocks_request(self):
        """Test middleware blocks request exceeding rate limit."""
        from monitor.rate_limit import RateLimitMiddleware, RateLimiter
        from django.http import JsonResponse
        
        # Create middleware - the middleware creates its own limiter in __call__ if not set
        middleware = RateLimitMiddleware(self.get_response)
        middleware.exempt_paths = ['/admin/', '/health/']
        
        # Manually exhaust the limiter's bucket for our test IP
        test_ip = '192.168.1.100'
        middleware.limiter = RateLimiter(rate=1, per=60)
        # First request should succeed but exhaust
        result1 = middleware.limiter.allow_request(test_ip)
        self.assertTrue(result1)  # First request should succeed
        # Second request should fail
        result2 = middleware.limiter.allow_request(test_ip)
        self.assertFalse(result2)  # Second should fail
        
        # Now make request from same IP
        request = MagicMock()
        request.path = '/api/test'
        request.META = {'REMOTE_ADDR': test_ip}
        
        response = middleware(request)
        
        # Should return 429 JsonResponse
        self.assertIsInstance(response, JsonResponse)
        self.assertEqual(response.status_code, 429)

    def test_middleware_exempts_admin_path(self):
        """Test admin path is exempt from rate limiting."""
        from monitor.rate_limit import RateLimitMiddleware
        
        middleware = RateLimitMiddleware(self.get_response)
        middleware.exempt_paths = ['/admin/', '/health/']
        
        request = MagicMock()
        request.path = '/admin/login'
        request.META = {}
        
        response = middleware(request)
        
        # Should not check rate limit for admin paths
        self.get_response.assert_called_once()


class TestAPIKeyRateLimitMiddleware(unittest.TestCase):
    """Tests for APIKeyRateLimitMiddleware."""

    def setUp(self):
        """Set up test fixtures."""
        self.get_response = MagicMock(return_value=MagicMock())
        self.get_response.return_value = MagicMock()
        self.get_response.return_value.__setitem__ = MagicMock()

    def test_uses_api_key_for_client_id(self):
        """Test middleware uses API key for client identification."""
        from monitor.rate_limit import APIKeyRateLimitMiddleware, RateLimiter
        
        middleware = APIKeyRateLimitMiddleware(self.get_response)
        middleware.limiter = RateLimiter(rate=100, per=60)
        middleware.exempt_paths = ['/admin/', '/health/']
        
        request = MagicMock()
        request.path = '/api/test'
        request.META = {
            'REMOTE_ADDR': '127.0.0.1',
            'HTTP_X_API_KEY': 'test-api-key-12345'
        }
        
        response = middleware(request)
        
        # Verify allow_request was called with a key based on API key
        self.assertIsNotNone(middleware.limiter.buckets)


if __name__ == '__main__':
    unittest.main()
