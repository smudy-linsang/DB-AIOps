# -*- coding: utf-8 -*-
"""
Unit tests for Redis caching module
"""

import unittest
import sys
import os
from unittest.mock import patch, MagicMock

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestCacheManager(unittest.TestCase):
    """Tests for CacheManager class."""

    @patch('monitor.cache.get_redis_client')
    def test_cache_manager_get_miss(self, mock_get_client):
        """Test cache miss returns None."""
        mock_redis = MagicMock()
        mock_redis.get.return_value = None
        mock_get_client.return_value = mock_redis
        
        from monitor.cache import CacheManager
        manager = CacheManager()
        result = manager.get('test_key')
        self.assertIsNone(result)

    @patch('monitor.cache.get_redis_client')
    def test_cache_manager_set_and_get(self, mock_get_client):
        """Test setting and getting cache value."""
        mock_redis = MagicMock()
        mock_redis.get.return_value = '{"value": 42}'
        mock_get_client.return_value = mock_redis
        
        from monitor.cache import CacheManager
        manager = CacheManager()
        
        # Set a value
        success = manager.set('test_key', {'value': 42}, timeout=300)
        self.assertTrue(success)
        
        # Get the value
        result = manager.get('test_key')
        self.assertEqual(result, {'value': 42})

    @patch('monitor.cache.get_redis_client')
    def test_cache_manager_delete(self, mock_get_client):
        """Test deleting cache key."""
        mock_redis = MagicMock()
        mock_get_client.return_value = mock_redis
        
        from monitor.cache import CacheManager
        manager = CacheManager()
        
        success = manager.delete('test_key')
        self.assertTrue(success)
        mock_redis.delete.assert_called_once_with('test_key')

    @patch('monitor.cache.get_redis_client')
    def test_cache_manager_exists(self, mock_get_client):
        """Test checking if key exists."""
        mock_redis = MagicMock()
        mock_redis.exists.return_value = 1
        mock_get_client.return_value = mock_redis
        
        from monitor.cache import CacheManager
        manager = CacheManager()
        
        self.assertTrue(manager.exists('test_key'))
        mock_redis.exists.assert_called_with('test_key')


class TestCachedDecorator(unittest.TestCase):
    """Tests for @cached decorator."""

    @patch('monitor.cache.get_redis_client')
    def test_cached_decorator_miss(self, mock_get_client):
        """Test decorator calls function on cache miss."""
        mock_redis = MagicMock()
        mock_redis.get.return_value = None
        mock_get_client.return_value = mock_redis
        
        from monitor.cache import cached
        
        call_count = 0
        
        @cached(timeout=60, key_prefix='test')
        def expensive_function(x):
            nonlocal call_count
            call_count += 1
            return x * 2
        
        result = expensive_function(5)
        
        self.assertEqual(result, 10)
        self.assertEqual(call_count, 1)

    @patch('monitor.cache.get_redis_client')
    def test_cached_decorator_hit(self, mock_get_client):
        """Test decorator returns cached value."""
        mock_redis = MagicMock()
        mock_redis.get.return_value = '{"value": 100}'  # Cached value
        mock_get_client.return_value = mock_redis
        
        from monitor.cache import cached
        
        call_count = 0
        
        @cached(timeout=60, key_prefix='test')
        def expensive_function(x):
            nonlocal call_count
            call_count += 1
            return x * 2
        
        result = expensive_function(5)
        
        self.assertEqual(result, {'value': 100})
        self.assertEqual(call_count, 0)  # Function not called


class TestGenerateCacheKey(unittest.TestCase):
    """Tests for cache key generation."""

    def test_generate_cache_key_simple(self):
        """Test simple key generation."""
        from monitor.cache import generate_cache_key
        
        key = generate_cache_key('prefix', 'arg1', 'arg2')
        self.assertEqual(key, 'prefix:arg1:arg2')

    def test_generate_cache_key_with_kwargs(self):
        """Test key generation with kwargs."""
        from monitor.cache import generate_cache_key
        
        key = generate_cache_key('prefix', 'arg1', param='value')
        self.assertTrue(key.startswith('prefix:arg1:'))


class TestInvalidateCache(unittest.TestCase):
    """Tests for cache invalidation."""

    @patch('monitor.cache.get_redis_client')
    def test_invalidate_cache_pattern(self, mock_get_client):
        """Test invalidating keys by pattern."""
        mock_redis = MagicMock()
        mock_redis.keys.return_value = ['key1', 'key2', 'key3']
        mock_redis.delete.return_value = 3
        mock_get_client.return_value = mock_redis
        
        from monitor.cache import invalidate_cache
        
        count = invalidate_cache('prefix:*')
        
        self.assertEqual(count, 3)
        mock_redis.keys.assert_called_once_with('prefix:*')
        mock_redis.delete.assert_called_once_with('key1', 'key2', 'key3')


if __name__ == '__main__':
    unittest.main()
