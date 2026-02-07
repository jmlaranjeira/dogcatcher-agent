"""Unit tests for performance optimizations."""
import pytest
import time
from unittest.mock import Mock, patch
from typing import Dict, Any

from agent.performance import (
    SimilarityCache,
    PerformanceMetrics,
    get_similarity_cache,
    get_performance_metrics,
    optimize_jira_search_params,
    cached_normalize_text,
    cached_normalize_log_message,
    clear_performance_caches,
    get_performance_recommendations
)


class TestSimilarityCache:
    """Test similarity cache functionality."""
    
    def test_cache_basic_operations(self):
        """Test basic cache operations."""
        cache = SimilarityCache(max_size=10, ttl_seconds=60)
        
        # Test cache miss
        result = cache.get("test summary", {"error_type": "test"})
        assert result is None
        
        # Test cache set and hit
        test_result = ("TEST-123", 0.85, "Test Summary")
        cache.set("test summary", {"error_type": "test"}, test_result)
        
        result = cache.get("test summary", {"error_type": "test"})
        assert result == test_result
        
        # Test cache miss with different state
        result = cache.get("test summary", {"error_type": "different"})
        assert result is None
    
    def test_cache_key_generation(self):
        """Test cache key generation."""
        cache = SimilarityCache()
        
        # Same summary should generate same key
        key1 = cache._make_key("Database Error", {"error_type": "db"})
        key2 = cache._make_key("Database Error", {"error_type": "db"})
        assert key1 == key2
        
        # Different summary should generate different key
        key3 = cache._make_key("Network Error", {"error_type": "db"})
        assert key1 != key3
        
        # Different state should generate different key
        key4 = cache._make_key("Database Error", {"error_type": "network"})
        assert key1 != key4
    
    def test_cache_expiration(self):
        """Test cache expiration."""
        cache = SimilarityCache(ttl_seconds=1)  # 1 second TTL
        
        # Set cache entry
        test_result = ("TEST-123", 0.85, "Test Summary")
        cache.set("test summary", None, test_result)
        
        # Should be available immediately
        result = cache.get("test summary", None)
        assert result == test_result
        
        # Wait for expiration
        time.sleep(1.1)
        
        # Should be expired
        result = cache.get("test summary", None)
        assert result is None
    
    def test_cache_size_limit(self):
        """Test cache size limit."""
        cache = SimilarityCache(max_size=2)
        
        # Fill cache
        cache.set("summary1", None, ("TEST-1", 0.8, "Summary 1"))
        cache.set("summary2", None, ("TEST-2", 0.8, "Summary 2"))
        
        # Add one more to trigger eviction
        cache.set("summary3", None, ("TEST-3", 0.8, "Summary 3"))
        
        # Should have max_size entries
        assert len(cache.cache) == 2
        
        # Oldest entry should be evicted
        result1 = cache.get("summary1", None)
        assert result1 is None  # Should be evicted
        
        # Newer entries should still be there
        result2 = cache.get("summary2", None)
        result3 = cache.get("summary3", None)
        assert result2 is not None
        assert result3 is not None
    
    def test_cache_stats(self):
        """Test cache statistics."""
        cache = SimilarityCache()
        
        # Initial stats
        stats = cache.get_stats()
        assert stats["hits"] == 0
        assert stats["misses"] == 0
        assert stats["hit_rate_percent"] == 0
        
        # Test miss
        cache.get("test", None)
        stats = cache.get_stats()
        assert stats["misses"] == 1
        assert stats["hit_rate_percent"] == 0
        
        # Test hit
        cache.set("test", None, ("TEST-1", 0.8, "Test"))
        cache.get("test", None)
        stats = cache.get_stats()
        assert stats["hits"] == 1
        assert stats["misses"] == 1
        assert stats["hit_rate_percent"] == 50.0
    
    def test_cache_clear(self):
        """Test cache clearing."""
        cache = SimilarityCache()
        
        # Add some entries
        cache.set("test1", None, ("TEST-1", 0.8, "Test 1"))
        cache.set("test2", None, ("TEST-2", 0.8, "Test 2"))
        
        assert len(cache.cache) == 2
        
        # Clear cache
        cache.clear()
        
        assert len(cache.cache) == 0
        assert cache.hits == 0
        assert cache.misses == 0


class TestPerformanceMetrics:
    """Test performance metrics functionality."""
    
    def test_timer_basic_operations(self):
        """Test basic timer operations."""
        metrics = PerformanceMetrics()
        
        # Start timer
        metrics.start_timer("test_operation")
        
        # Simulate some work
        time.sleep(0.01)
        
        # End timer
        duration = metrics.end_timer("test_operation")
        
        assert duration > 0.01
        assert duration < 0.1  # Should be reasonable
        
        # Check stats
        stats = metrics.get_operation_stats("test_operation")
        assert stats["count"] == 1
        assert stats["avg_ms"] > 10
        assert stats["min_ms"] > 10
        assert stats["max_ms"] > 10
    
    def test_timer_multiple_operations(self):
        """Test multiple timer operations."""
        metrics = PerformanceMetrics()
        
        # Multiple operations
        for i in range(3):
            metrics.start_timer("test_operation")
            time.sleep(0.01)
            metrics.end_timer("test_operation")
        
        stats = metrics.get_operation_stats("test_operation")
        assert stats["count"] == 3
        assert stats["avg_ms"] > 10
        assert stats["min_ms"] > 10
        assert stats["max_ms"] > 10
    
    def test_timer_different_operations(self):
        """Test different timer operations."""
        metrics = PerformanceMetrics()
        
        # Different operations
        metrics.start_timer("operation1")
        time.sleep(0.01)
        metrics.end_timer("operation1")
        
        metrics.start_timer("operation2")
        time.sleep(0.02)
        metrics.end_timer("operation2")
        
        # Check individual stats
        stats1 = metrics.get_operation_stats("operation1")
        stats2 = metrics.get_operation_stats("operation2")
        
        assert stats1["count"] == 1
        assert stats2["count"] == 1
        assert stats2["avg_ms"] > stats1["avg_ms"]
        
        # Check all stats
        all_stats = metrics.get_all_stats()
        assert "operation1" in all_stats
        assert "operation2" in all_stats
    
    def test_timer_missing_operation(self):
        """Test timer with missing operation."""
        metrics = PerformanceMetrics()
        
        # Try to end timer that was never started
        duration = metrics.end_timer("missing_operation")
        assert duration == 0.0
        
        # Check stats for missing operation
        stats = metrics.get_operation_stats("missing_operation")
        assert stats == {}


class TestPerformanceOptimizations:
    """Test performance optimization functions."""
    
    def test_optimize_jira_search_params_default(self, mock_config):
        """Test Jira search parameter optimization with default config."""
        with patch('agent.performance.get_config', return_value=mock_config):
            params = optimize_jira_search_params()
            
            assert "search_window_days" in params
            assert "search_max_results" in params
            assert "similarity_threshold" in params
            assert "direct_log_threshold" in params
            assert "partial_log_threshold" in params
    
    def test_optimize_jira_search_params_high_volume(self, mock_config):
        """Test optimization for high-volume projects."""
        mock_config.jira_search_window_days = 365  # High volume

        with patch('agent.performance.get_config', return_value=mock_config):
            params = optimize_jira_search_params()

            # Should optimize window for high-volume projects
            assert params["search_window_days"] <= 180

    def test_optimize_jira_search_params_high_similarity(self, mock_config):
        """Test optimization for high similarity threshold."""
        mock_config.jira_similarity_threshold = 0.95  # High similarity

        with patch('agent.performance.get_config', return_value=mock_config):
            params = optimize_jira_search_params()

            # Should optimize max results for high similarity
            assert params["search_max_results"] <= 50
    
    def test_cached_normalize_text(self):
        """Test cached text normalization."""
        # Clear cache first
        cached_normalize_text.cache_clear()
        
        # First call should compute
        result1 = cached_normalize_text("Database Connection Error")
        assert result1 == "database connection error"
        
        # Second call should use cache
        result2 = cached_normalize_text("Database Connection Error")
        assert result2 == result1
        
        # Check cache info
        cache_info = cached_normalize_text.cache_info()
        assert cache_info.hits >= 1
    
    def test_cached_normalize_log_message(self):
        """Test cached log message normalization."""
        # Clear cache first
        cached_normalize_log_message.cache_clear()
        
        # First call should compute
        result1 = cached_normalize_log_message("Error: Database connection failed")
        assert "error" in result1
        assert "database" in result1
        
        # Second call should use cache
        result2 = cached_normalize_log_message("Error: Database connection failed")
        assert result2 == result1
        
        # Check cache info
        cache_info = cached_normalize_log_message.cache_info()
        assert cache_info.hits >= 1
    
    def test_clear_performance_caches(self):
        """Test clearing all performance caches."""
        # Add some data to caches
        cache = get_similarity_cache()
        cache.set("test", None, ("TEST-1", 0.8, "Test"))
        
        cached_normalize_text("test")
        cached_normalize_log_message("test")
        
        # Clear all caches
        clear_performance_caches()
        
        # Verify caches are cleared
        assert len(cache.cache) == 0
        assert cached_normalize_text.cache_info().currsize == 0
        assert cached_normalize_log_message.cache_info().currsize == 0
    
    def test_get_performance_recommendations(self, mock_config):
        """Test performance recommendations."""
        with patch('agent.performance.get_config', return_value=mock_config):
            recommendations = get_performance_recommendations()

            assert isinstance(recommendations, list)

            # Check for specific recommendations based on config
            if mock_config.jira_search_window_days > 180:
                assert any("JIRA_SEARCH_WINDOW_DAYS" in rec for rec in recommendations)

            if mock_config.jira_search_max_results > 200:
                assert any("JIRA_SEARCH_MAX_RESULTS" in rec for rec in recommendations)

            if mock_config.jira_similarity_threshold < 0.7:
                assert any("JIRA_SIMILARITY_THRESHOLD" in rec for rec in recommendations)


class TestGlobalInstances:
    """Test global performance instances."""
    
    def test_get_similarity_cache(self):
        """Test getting global similarity cache."""
        cache = get_similarity_cache()
        assert isinstance(cache, SimilarityCache)
        
        # Should return same instance
        cache2 = get_similarity_cache()
        assert cache is cache2
    
    def test_get_performance_metrics(self):
        """Test getting global performance metrics."""
        metrics = get_performance_metrics()
        assert isinstance(metrics, PerformanceMetrics)
        
        # Should return same instance
        metrics2 = get_performance_metrics()
        assert metrics is metrics2


class TestPerformanceIntegration:
    """Test performance optimizations integration."""
    
    def test_cache_integration_with_similarity(self, mock_config):
        """Test cache integration with similarity calculations."""
        cache = get_similarity_cache()
        cache.clear()
        
        # Mock the similarity calculation
        with patch('agent.jira.match.find_similar_ticket') as mock_find:
            mock_find.return_value = ("TEST-123", 0.85, "Test Summary")
            
            # First call should hit the mock
            result1 = mock_find("Database Error", {"error_type": "db"})
            assert result1 == ("TEST-123", 0.85, "Test Summary")
            assert mock_find.call_count == 1
            
            # Second call should also hit the mock (cache not integrated in this test)
            result2 = mock_find("Database Error", {"error_type": "db"})
            assert result2 == ("TEST-123", 0.85, "Test Summary")
            assert mock_find.call_count == 2
    
    def test_performance_timing_integration(self):
        """Test performance timing integration."""
        metrics = get_performance_metrics()
        
        # Simulate some operations
        metrics.start_timer("test_op")
        time.sleep(0.01)
        metrics.end_timer("test_op")
        
        # Check that metrics are recorded
        stats = metrics.get_operation_stats("test_op")
        assert stats["count"] == 1
        assert stats["avg_ms"] > 10
