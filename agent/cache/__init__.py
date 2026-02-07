"""Enhanced caching system with persistent backends.

This module provides a flexible caching system supporting multiple backends:
- Redis: High-performance distributed cache
- File: Persistent file-based cache for single instances
- Memory: In-memory cache for development and fallback

The cache manager automatically handles backend selection and fallback.
"""

from .base import CacheBackend, CacheEntry
from .manager import CacheManager
from .redis_cache import RedisCacheBackend
from .file_cache import FileCacheBackend
from .memory_cache import MemoryCacheBackend

__all__ = [
    "CacheBackend",
    "CacheEntry",
    "CacheManager",
    "RedisCacheBackend",
    "FileCacheBackend",
    "MemoryCacheBackend",
]
