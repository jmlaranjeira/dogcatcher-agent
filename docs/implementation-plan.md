# Dogcatcher Agent - Implementation Plan

## 📋 Executive Summary

This document outlines the comprehensive implementation plan to transform the Dogcatcher Agent based on the findings from our project study. The plan addresses critical infrastructure improvements, performance optimization, and advanced features over **3 phases spanning 6 months**.

**Target Outcomes:**
- 🚀 **3x throughput improvement** through parallel processing
- 📈 **50% reduction in API calls** via persistent caching
- 🔒 **99.9% uptime** with circuit breaker resilience
- ⚡ **<30s average processing time** per log
- 🛡️ **Zero security incidents** with enhanced monitoring

---

## 🎯 Implementation Roadmap

### **Phase 1: Critical Infrastructure** ⏱️ Sprints 1-2 (4 weeks)
**Goal**: Establish robust foundation with persistent caching, error resilience, and simplified configuration.

### **Phase 2: Performance & Scalability** ⏱️ Sprints 3-6 (8 weeks)
**Goal**: Implement parallel processing, comprehensive testing, and performance monitoring.

### **Phase 3: Advanced Features** ⏱️ Sprints 7-12 (12 weeks)
**Goal**: Add enterprise-grade error recovery, resource management, and security enhancements.

---

## 🚀 Phase 1: Critical Infrastructure (4 weeks)

### **Sprint 1.1: Persistent Caching System** 🗄️

**Objective**: Replace memory-only cache with persistent Redis/file-based storage

**Implementation Details:**

#### New Cache Architecture
```python
# agent/cache/base.py
class CacheBackend(ABC):
    @abstractmethod
    async def get(self, key: str) -> Optional[Any]: ...
    @abstractmethod
    async def set(self, key: str, value: Any, ttl: int = 3600): ...
    @abstractmethod
    async def clear(self): ...
    @abstractmethod
    def get_stats(self) -> Dict[str, Any]: ...

# agent/cache/redis_cache.py
class RedisCacheBackend(CacheBackend):
    def __init__(self, redis_url: str = "redis://localhost:6379"):
        self.redis = Redis.from_url(redis_url)

    async def get(self, key: str) -> Optional[Any]:
        data = await self.redis.get(key)
        return pickle.loads(data) if data else None

# agent/cache/file_cache.py
class FileCacheBackend(CacheBackend):
    def __init__(self, cache_dir: str = ".agent_cache/persistent"):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(exist_ok=True)
```

**Files to Create:**
- `agent/cache/__init__.py` - Cache module initialization
- `agent/cache/base.py` - Abstract cache backend interface
- `agent/cache/redis_cache.py` - Redis implementation
- `agent/cache/file_cache.py` - File-based fallback
- `agent/cache/manager.py` - Cache manager with automatic fallback

**Files to Modify:**
- `agent/performance.py` - Integrate new cache backends
- `agent/config.py` - Add cache configuration options
- `requirements.txt` - Add `redis>=4.5.0`, `aioredis>=2.0.0`

**Configuration Addition:**
```yaml
cache:
  backend: "redis"  # redis, file, memory
  redis_url: "redis://localhost:6379"
  file_cache_dir: ".agent_cache/persistent"
  ttl_seconds: 3600
  max_memory_size: 1000
```

**Testing:**
- Cache persistence across restarts
- Fallback from Redis to file to memory
- Performance benchmarks vs current implementation
- Cache invalidation and cleanup

**Success Metrics:**
- ✅ Cache survives application restarts
- ✅ 80%+ cache hit rate maintained
- ✅ <100ms cache operation latency
- ✅ Graceful fallback on Redis failure

---

### **Sprint 1.2: Circuit Breaker Pattern** 🔌

**Objective**: Add resilience to LLM analysis with automatic fallbacks

**Implementation Details:**

#### Circuit Breaker Implementation
```python
# agent/utils/circuit_breaker.py
class CircuitBreaker:
    def __init__(self, failure_threshold: int = 3, timeout: int = 60):
        self.failure_threshold = failure_threshold
        self.timeout = timeout
        self.failure_count = 0
        self.last_failure_time = None
        self.state = "CLOSED"  # CLOSED, OPEN, HALF_OPEN

    def call(self, func, *args, **kwargs):
        if self.state == "OPEN":
            if self._should_attempt_reset():
                self.state = "HALF_OPEN"
            else:
                raise CircuitBreakerOpenError()

        try:
            result = func(*args, **kwargs)
            self._on_success()
            return result
        except Exception as e:
            self._on_failure()
            raise

# agent/utils/fallback_analysis.py
class FallbackAnalyzer:
    def analyze_log(self, log_data: Dict[str, Any]) -> Dict[str, Any]:
        """Simple rule-based fallback when LLM fails."""
        message = log_data.get("message", "").lower()

        # Simple pattern matching
        if "database" in message or "connection" in message:
            error_type = "database-connection"
            severity = "high"
        elif "timeout" in message:
            error_type = "timeout"
            severity = "medium"
        elif "404" in message or "not found" in message:
            error_type = "not-found"
            severity = "low"
        else:
            error_type = "unknown"
            severity = "medium"

        return {
            "error_type": error_type,
            "create_ticket": True,
            "ticket_title": f"System Error: {error_type.replace('-', ' ').title()}",
            "ticket_description": f"**Problem Summary**\n{message}\n\n**Analysis**\nFallback analysis applied due to LLM unavailability.",
            "severity": severity
        }
```

**Files to Create:**
- `agent/utils/circuit_breaker.py` - Circuit breaker implementation
- `agent/utils/fallback_analysis.py` - Rule-based fallback analyzer
- `agent/utils/resilience.py` - Resilience utilities and decorators

**Files to Modify:**
- `agent/nodes/analysis.py` - Wrap LLM calls with circuit breaker
- `agent/config.py` - Add circuit breaker configuration

**Configuration Addition:**
```yaml
resilience:
  circuit_breaker:
    failure_threshold: 3
    timeout_seconds: 60
    half_open_max_calls: 3
  fallback:
    enabled: true
    create_tickets: true
```

**Testing:**
- Circuit breaker state transitions
- Fallback analysis accuracy vs LLM
- Recovery after LLM service restoration
- Performance impact measurement

**Success Metrics:**
- ✅ 99%+ analysis success rate (including fallbacks)
- ✅ <5s fallback analysis time
- ✅ Automatic recovery within 60s of LLM restoration
- ✅ Zero processing halts due to LLM failures

---

### **Sprint 1.3: Configuration Profiles** ⚙️

**Objective**: Simplify environment management with profile-based configs

**Implementation Details:**

#### Profile-Based Configuration
```python
# agent/config.py - Enhanced configuration
class ProfileConfig(BaseSettings):
    profile: str = Field("development", env="DOGCATCHER_PROFILE")

    def load_profile_config(self) -> Dict[str, Any]:
        """Load configuration from profile files."""
        profile_path = Path(f"config/profiles/{self.profile}.yaml")
        if profile_path.exists():
            with open(profile_path) as f:
                return yaml.safe_load(f)
        return {}

    def model_post_init(self, __context):
        """Apply profile configuration after initialization."""
        profile_config = self.load_profile_config()
        self._apply_profile_overrides(profile_config)

# Enhanced main.py CLI
parser.add_argument('--profile', type=str, help='Configuration profile (dev/staging/prod)')
```

**Files to Create:**
- `config/profiles/development.yaml` - Development environment
- `config/profiles/staging.yaml` - Staging environment
- `config/profiles/production.yaml` - Production environment
- `config/profiles/testing.yaml` - Test environment
- `config/profiles/README.md` - Profile documentation

**Profile Examples:**
```yaml
# config/profiles/development.yaml
datadog:
  limit: 10
  hours_back: 2
  timeout: 10
jira:
  similarity_threshold: 0.75
  search_window_days: 30
agent:
  max_tickets_per_run: 1
  auto_create_ticket: false
cache:
  backend: "file"
  ttl_seconds: 300

# config/profiles/production.yaml
datadog:
  limit: 100
  hours_back: 48
  timeout: 30
jira:
  similarity_threshold: 0.85
  search_window_days: 365
agent:
  max_tickets_per_run: 5
  auto_create_ticket: true
cache:
  backend: "redis"
  ttl_seconds: 3600
```

**Files to Modify:**
- `agent/config.py` - Add profile loading logic
- `main.py` - Add `--profile` CLI argument
- `README.md` - Update configuration documentation

**Testing:**
- Profile loading and validation
- Profile override behavior
- CLI profile selection
- Environment variable precedence

**Success Metrics:**
- ✅ Single command deployment per environment
- ✅ Zero configuration errors in staging/prod
- ✅ Clear separation of environment settings
- ✅ Simplified developer onboarding

---

## 🔄 Phase 2: Performance & Scalability (8 weeks)

### **Sprint 2.1: Async Parallel Processing** ⚡

**Objective**: Implement concurrent log processing using asyncio

**Implementation Details:**

#### Async Graph Architecture
```python
# agent/async_graph.py
class AsyncLogProcessor:
    def __init__(self, max_workers: int = 5):
        self.max_workers = max_workers
        self.semaphore = asyncio.Semaphore(max_workers)

    async def process_logs_parallel(self, logs: List[Dict]) -> List[Dict]:
        """Process logs concurrently with worker pool."""
        tasks = []
        for log in logs:
            task = asyncio.create_task(
                self._process_single_log(log)
            )
            tasks.append(task)

        results = await asyncio.gather(*tasks, return_exceptions=True)
        return [r for r in results if not isinstance(r, Exception)]

    async def _process_single_log(self, log: Dict) -> Dict:
        async with self.semaphore:
            # Async LLM analysis
            analysis = await self.async_analyzer.analyze(log)

            # Async duplicate check
            duplicate = await self.async_jira.find_similar(analysis)

            # Async ticket creation if needed
            if not duplicate and analysis.get("create_ticket"):
                ticket = await self.async_jira.create_ticket(analysis)
                return {"status": "created", "ticket": ticket}

            return {"status": "processed", "analysis": analysis}

# agent/workers/worker_pool.py
class WorkerPool:
    def __init__(self, worker_count: int = 3):
        self.workers = []
        self.task_queue = asyncio.Queue()
        self.result_queue = asyncio.Queue()

    async def start(self):
        """Start worker processes."""
        for i in range(self.worker_count):
            worker = asyncio.create_task(self._worker_loop(f"worker-{i}"))
            self.workers.append(worker)

    async def _worker_loop(self, worker_id: str):
        """Worker loop for processing tasks."""
        while True:
            task = await self.task_queue.get()
            try:
                result = await self._process_task(task)
                await self.result_queue.put({"worker": worker_id, "result": result})
            except Exception as e:
                await self.result_queue.put({"worker": worker_id, "error": str(e)})
            finally:
                self.task_queue.task_done()
```

**Files to Create:**
- `agent/async_graph.py` - Async graph implementation
- `agent/workers/` - Worker pool management
- `agent/workers/worker_pool.py` - Worker pool implementation
- `agent/workers/task_manager.py` - Task distribution logic
- `agent/async_nodes/` - Async versions of all nodes

**Files to Modify:**
- `agent/nodes/analysis.py` - Add async version
- `agent/jira/client.py` - Convert to async HTTP
- `agent/datadog.py` - Add async data fetching
- `main.py` - Add `--async` flag and worker count option

**Testing:**
- Concurrent processing correctness
- Performance benchmarks vs sequential
- Error isolation between workers
- Resource usage monitoring

**Success Metrics:**
- ✅ 3x throughput improvement (150+ logs/hour → 500+ logs/hour)
- ✅ <30s average processing time per log
- ✅ Proper error isolation (one failure doesn't stop others)
- ✅ Resource usage within acceptable limits

---

### **Sprint 2.2: Enhanced Integration Testing** 🧪

**Objective**: Comprehensive end-to-end testing with realistic scenarios

**Implementation Details:**

#### Integration Test Framework
```python
# tests/integration/test_full_pipeline.py
class TestFullPipeline:
    @pytest.mark.integration
    async def test_complete_workflow_success(self, integration_fixtures):
        """Test complete log processing workflow."""
        # Setup realistic test environment
        mock_datadog = integration_fixtures.datadog_server
        mock_jira = integration_fixtures.jira_server
        mock_openai = integration_fixtures.openai_server

        # Run complete pipeline
        result = await run_agent_pipeline(
            logs=integration_fixtures.sample_logs,
            config=integration_fixtures.test_config
        )

        # Verify end-to-end behavior
        assert result.tickets_created == 2
        assert result.duplicates_found == 1
        assert result.processing_time < 60

        # Verify external API interactions
        assert mock_jira.tickets_created == 2
        assert mock_openai.calls_made == 3

    @pytest.mark.integration
    async def test_failure_recovery_scenarios(self):
        """Test system behavior under various failure conditions."""
        scenarios = [
            "openai_service_down",
            "jira_service_slow",
            "datadog_rate_limited",
            "partial_network_failure"
        ]

        for scenario in scenarios:
            with failure_injection(scenario):
                result = await run_agent_pipeline()
                assert result.status in ["completed", "partial_success"]
                assert result.errors_handled > 0

# tests/integration/fixtures/realistic_data.py
class IntegrationFixtures:
    def __init__(self):
        self.sample_logs = self._load_realistic_logs()
        self.expected_tickets = self._load_expected_outputs()

    def _load_realistic_logs(self) -> List[Dict]:
        """Load realistic log samples from production data."""
        return [
            {
                "logger": "com.example.payment.service",
                "thread": "payment-processor-1",
                "message": "Payment processing failed for order 12345: Connection timeout to payment gateway",
                "timestamp": "2025-12-09T14:30:00Z",
                "detail": "Failed to connect to payment gateway after 30 seconds timeout"
            },
            # ... more realistic samples
        ]

# tests/mocks/advanced_mocks.py
class SmartJiraMock:
    """Intelligent Jira mock that simulates real API behavior."""

    def __init__(self):
        self.tickets = {}
        self.search_delay = 0.1
        self.create_delay = 0.2

    async def search_issues(self, jql: str) -> Dict:
        """Simulate search with realistic delays and responses."""
        await asyncio.sleep(self.search_delay)

        # Simulate search logic based on JQL
        if "database" in jql.lower():
            return self._database_tickets()
        elif "timeout" in jql.lower():
            return self._timeout_tickets()

        return {"issues": []}

    def simulate_load(self, response_time: float):
        """Simulate high load with increased response times."""
        self.search_delay = response_time
        self.create_delay = response_time * 1.5
```

**Files to Create:**
- `tests/integration/` - Integration test suite
- `tests/integration/test_full_pipeline.py` - End-to-end workflow tests
- `tests/integration/test_error_scenarios.py` - Failure mode testing
- `tests/integration/test_performance_benchmarks.py` - Performance tests
- `tests/fixtures/realistic_data.py` - Production-like test data
- `tests/mocks/advanced_mocks.py` - Intelligent API mocks
- `tests/contracts/` - Contract testing for external APIs

**Files to Modify:**
- `pytest.ini` - Add integration test markers
- `tests/conftest.py` - Add integration fixtures
- `run_tests.py` - Add integration test command

**Testing Strategy:**
- Complete workflow testing with realistic data
- Failure injection and recovery testing
- Performance benchmarking and regression detection
- Contract testing for external API compatibility

**Success Metrics:**
- ✅ 95%+ integration test coverage
- ✅ <5 minute full integration test suite runtime
- ✅ Zero integration failures in CI/CD pipeline
- ✅ Automated performance regression detection

---

### **Sprint 2.3: Performance Monitoring Dashboard** 📊

**Objective**: Real-time performance metrics and alerting

**Implementation Details:**

#### Monitoring System
```python
# agent/monitoring/metrics.py
class MetricsCollector:
    def __init__(self):
        self.metrics = {
            "logs_processed": Counter(),
            "tickets_created": Counter(),
            "processing_time": Histogram(),
            "cache_hits": Counter(),
            "cache_misses": Counter(),
            "llm_failures": Counter(),
            "jira_api_calls": Counter()
        }

    def record_processing_time(self, duration: float):
        self.metrics["processing_time"].observe(duration)

    def record_cache_hit(self):
        self.metrics["cache_hits"].inc()

    def get_summary(self) -> Dict[str, Any]:
        """Get current metrics summary."""
        return {
            "total_logs_processed": self.metrics["logs_processed"]._value.sum(),
            "avg_processing_time": self.metrics["processing_time"].sample().avg,
            "cache_hit_rate": self._calculate_cache_hit_rate(),
            "error_rate": self._calculate_error_rate()
        }

# agent/monitoring/dashboard.py
class SimpleDashboard:
    """Lightweight web dashboard for monitoring."""

    def __init__(self, metrics_collector: MetricsCollector):
        self.metrics = metrics_collector
        self.app = FastAPI()
        self._setup_routes()

    def _setup_routes(self):
        @self.app.get("/metrics")
        async def get_metrics():
            return self.metrics.get_summary()

        @self.app.get("/health")
        async def health_check():
            return {"status": "healthy", "timestamp": datetime.now()}

        @self.app.get("/")
        async def dashboard():
            return self._render_dashboard_html()

# tools/performance_analyzer.py
class PerformanceAnalyzer:
    """Analyze performance trends and detect regressions."""

    def analyze_trends(self, timeframe: str = "7d") -> Dict[str, Any]:
        """Analyze performance trends over time."""
        metrics = self._load_historical_metrics(timeframe)

        return {
            "throughput_trend": self._calculate_trend(metrics["throughput"]),
            "latency_trend": self._calculate_trend(metrics["latency"]),
            "error_rate_trend": self._calculate_trend(metrics["error_rate"]),
            "recommendations": self._generate_recommendations(metrics)
        }

    def detect_regressions(self) -> List[Dict[str, Any]]:
        """Detect performance regressions."""
        current = self._get_current_metrics()
        baseline = self._get_baseline_metrics()

        regressions = []

        if current["avg_processing_time"] > baseline["avg_processing_time"] * 1.2:
            regressions.append({
                "type": "latency_regression",
                "severity": "high",
                "current": current["avg_processing_time"],
                "baseline": baseline["avg_processing_time"]
            })

        return regressions
```

**Files to Create:**
- `agent/monitoring/` - Monitoring module
- `agent/monitoring/metrics.py` - Metrics collection
- `agent/monitoring/dashboard.py` - Web dashboard
- `agent/monitoring/alerts.py` - Alerting system
- `tools/performance_analyzer.py` - Performance analysis
- `tools/dashboard_server.py` - Standalone dashboard server

**Files to Modify:**
- `agent/nodes/` - Add metrics collection to all nodes
- `agent/performance.py` - Integrate with metrics system
- `main.py` - Add `--enable-monitoring` flag
- `requirements.txt` - Add `fastapi`, `prometheus-client`

**Dashboard Features:**
- Real-time metrics visualization
- Performance trend charts
- Alert management interface
- System health indicators
- Cache performance statistics

**Success Metrics:**
- ✅ <1s dashboard response time
- ✅ Real-time metric updates (< 30s delay)
- ✅ Automated performance regression detection
- ✅ Comprehensive performance visibility

---

## 🏗️ Phase 3: Advanced Features (12 weeks)

### **Sprint 3.1: Advanced Error Recovery** 🛡️

**Objective**: Comprehensive error handling and recovery mechanisms

**Implementation Details:**

#### Advanced Recovery System
```python
# agent/recovery/retry_policies.py
class RetryPolicy:
    """Configurable retry policy with exponential backoff."""

    def __init__(self, max_attempts: int = 3, base_delay: float = 1.0,
                 max_delay: float = 60.0, exponential_base: float = 2.0):
        self.max_attempts = max_attempts
        self.base_delay = base_delay
        self.max_delay = max_delay
        self.exponential_base = exponential_base

    async def execute_with_retry(self, func, *args, **kwargs):
        """Execute function with retry policy."""
        last_exception = None

        for attempt in range(self.max_attempts):
            try:
                return await func(*args, **kwargs)
            except RetryableException as e:
                last_exception = e
                if attempt < self.max_attempts - 1:
                    delay = min(
                        self.base_delay * (self.exponential_base ** attempt),
                        self.max_delay
                    )
                    await asyncio.sleep(delay)
                    continue
            except NonRetryableException:
                # Don't retry for non-retryable errors
                raise

        raise MaxRetriesExceeded(f"Failed after {self.max_attempts} attempts") from last_exception

# agent/recovery/deadletter_queue.py
class DeadLetterQueue:
    """Handle logs that fail processing multiple times."""

    def __init__(self, storage_path: str = ".agent_cache/deadletter"):
        self.storage_path = Path(storage_path)
        self.storage_path.mkdir(exist_ok=True)

    async def add_failed_log(self, log_data: Dict, error: Exception, attempts: int):
        """Add failed log to dead letter queue."""
        entry = {
            "log_data": log_data,
            "error": str(error),
            "error_type": type(error).__name__,
            "attempts": attempts,
            "timestamp": datetime.now().isoformat(),
            "fingerprint": self._calculate_fingerprint(log_data)
        }

        filename = f"failed_{entry['fingerprint']}.json"
        filepath = self.storage_path / filename

        with open(filepath, "w") as f:
            json.dump(entry, f, indent=2)

    async def retry_failed_logs(self) -> List[Dict]:
        """Retry processing failed logs."""
        results = []

        for filepath in self.storage_path.glob("failed_*.json"):
            try:
                with open(filepath) as f:
                    entry = json.load(f)

                # Attempt to reprocess
                result = await self._retry_log_processing(entry["log_data"])

                if result["success"]:
                    # Remove from dead letter queue on success
                    filepath.unlink()
                    results.append({"status": "recovered", "log": entry["log_data"]})
                else:
                    # Update failure count
                    entry["attempts"] += 1
                    entry["last_retry"] = datetime.now().isoformat()

                    with open(filepath, "w") as f:
                        json.dump(entry, f, indent=2)

                    results.append({"status": "still_failed", "log": entry["log_data"]})

            except Exception as e:
                log_error("Failed to retry dead letter queue entry", error=str(e), filepath=str(filepath))

        return results

# agent/recovery/health_checker.py
class HealthChecker:
    """Monitor system health and trigger recovery actions."""

    def __init__(self):
        self.services = {
            "openai": self._check_openai_health,
            "jira": self._check_jira_health,
            "datadog": self._check_datadog_health,
            "cache": self._check_cache_health
        }
        self.health_status = {}

    async def check_all_services(self) -> Dict[str, bool]:
        """Check health of all external services."""
        results = {}

        for service_name, check_func in self.services.items():
            try:
                is_healthy = await check_func()
                results[service_name] = is_healthy
                self.health_status[service_name] = {
                    "healthy": is_healthy,
                    "last_check": datetime.now().isoformat(),
                    "consecutive_failures": 0 if is_healthy else
                        self.health_status.get(service_name, {}).get("consecutive_failures", 0) + 1
                }
            except Exception as e:
                results[service_name] = False
                log_error(f"Health check failed for {service_name}", error=str(e))

        return results

    async def _check_openai_health(self) -> bool:
        """Check OpenAI API health."""
        try:
            # Simple API call to check connectivity
            response = await openai.Completion.acreate(
                model="gpt-3.5-turbo",
                messages=[{"role": "user", "content": "health check"}],
                max_tokens=1
            )
            return True
        except Exception:
            return False
```

**Files to Create:**
- `agent/recovery/` - Recovery module
- `agent/recovery/retry_policies.py` - Configurable retry strategies
- `agent/recovery/deadletter_queue.py` - Failed log handling
- `agent/recovery/health_checker.py` - Service health monitoring
- `agent/recovery/circuit_breaker_v2.py` - Enhanced circuit breaker
- `tools/recovery_manager.py` - Recovery management CLI

**Files to Modify:**
- All `agent/nodes/` files - Add retry logic and error classification
- `agent/utils/logger.py` - Enhanced error categorization
- `main.py` - Add recovery commands and health check endpoint

**Recovery Features:**
- Intelligent retry with exponential backoff
- Dead letter queue for permanently failed logs
- Service health monitoring and automatic recovery
- Error classification (retryable vs non-retryable)
- Recovery analytics and reporting

**Success Metrics:**
- ✅ 99%+ processing success rate (including recovery)
- ✅ <5% logs requiring manual intervention
- ✅ Automatic recovery within 10 minutes of service restoration
- ✅ Zero data loss during outages

---

### **Sprint 3.2: Resource Management** 💾

**Objective**: Memory monitoring and resource cleanup

**Implementation Details:**

#### Resource Management System
```python
# agent/resources/memory_monitor.py
class MemoryMonitor:
    """Monitor memory usage and trigger cleanup actions."""

    def __init__(self, max_memory_mb: int = 1024, warning_threshold: float = 0.8):
        self.max_memory_mb = max_memory_mb
        self.warning_threshold = warning_threshold
        self.monitoring_enabled = True
        self.cleanup_callbacks = []

    async def start_monitoring(self):
        """Start memory monitoring loop."""
        while self.monitoring_enabled:
            memory_usage = self._get_memory_usage()
            memory_percent = memory_usage / (self.max_memory_mb * 1024 * 1024)

            if memory_percent > self.warning_threshold:
                log_warning("High memory usage detected",
                           usage_mb=memory_usage / (1024 * 1024),
                           threshold_percent=self.warning_threshold * 100)
                await self._trigger_cleanup()

            if memory_percent > 0.95:
                log_error("Critical memory usage",
                         usage_mb=memory_usage / (1024 * 1024))
                await self._emergency_cleanup()

            await asyncio.sleep(30)  # Check every 30 seconds

    def _get_memory_usage(self) -> int:
        """Get current memory usage in bytes."""
        import psutil
        process = psutil.Process()
        return process.memory_info().rss

    async def _trigger_cleanup(self):
        """Trigger registered cleanup callbacks."""
        for callback in self.cleanup_callbacks:
            try:
                await callback()
            except Exception as e:
                log_error("Cleanup callback failed", error=str(e))

    def register_cleanup_callback(self, callback):
        """Register callback for memory cleanup."""
        self.cleanup_callbacks.append(callback)

# agent/resources/cleanup.py
class ResourceCleaner:
    """Automatic cleanup routines for various resources."""

    def __init__(self):
        self.cache_manager = None
        self.temp_files = set()
        self.old_logs = []

    async def cleanup_cache(self):
        """Clean up old cache entries."""
        if self.cache_manager:
            # Remove entries older than TTL
            await self.cache_manager.cleanup_expired()

            # If still over memory limit, remove LRU entries
            if self._is_cache_over_limit():
                await self.cache_manager.cleanup_lru(percentage=0.3)

            log_info("Cache cleanup completed")

    async def cleanup_temp_files(self):
        """Clean up temporary files."""
        cleaned_count = 0

        for temp_file in list(self.temp_files):
            try:
                if os.path.exists(temp_file):
                    os.remove(temp_file)
                    cleaned_count += 1
                self.temp_files.discard(temp_file)
            except Exception as e:
                log_warning("Failed to clean temp file", file=temp_file, error=str(e))

        log_info("Temp file cleanup completed", files_cleaned=cleaned_count)

    async def cleanup_old_logs(self, max_age_days: int = 7):
        """Clean up old log files."""
        import glob
        from datetime import datetime, timedelta

        cutoff_time = datetime.now() - timedelta(days=max_age_days)
        log_pattern = ".agent_cache/audit_logs*.jsonl"

        cleaned_count = 0
        for log_file in glob.glob(log_pattern):
            try:
                file_time = datetime.fromtimestamp(os.path.getmtime(log_file))
                if file_time < cutoff_time:
                    os.remove(log_file)
                    cleaned_count += 1
            except Exception as e:
                log_warning("Failed to clean old log", file=log_file, error=str(e))

        log_info("Old log cleanup completed", files_cleaned=cleaned_count)

    async def emergency_cleanup(self):
        """Emergency cleanup when memory is critically high."""
        log_warning("Executing emergency cleanup")

        # Clear all non-essential caches
        await self.cleanup_cache()

        # Force garbage collection
        import gc
        gc.collect()

        # Clean temp files aggressively
        await self.cleanup_temp_files()

        log_info("Emergency cleanup completed")

# agent/resources/resource_limiter.py
class ResourceLimiter:
    """Limit resource usage to prevent system overload."""

    def __init__(self, max_concurrent_llm_calls: int = 3,
                 max_concurrent_jira_calls: int = 5):
        self.llm_semaphore = asyncio.Semaphore(max_concurrent_llm_calls)
        self.jira_semaphore = asyncio.Semaphore(max_concurrent_jira_calls)
        self.active_operations = {
            "llm_calls": 0,
            "jira_calls": 0,
            "cache_operations": 0
        }

    async def limit_llm_call(self, func, *args, **kwargs):
        """Execute LLM call with resource limiting."""
        async with self.llm_semaphore:
            self.active_operations["llm_calls"] += 1
            try:
                return await func(*args, **kwargs)
            finally:
                self.active_operations["llm_calls"] -= 1

    async def limit_jira_call(self, func, *args, **kwargs):
        """Execute Jira call with resource limiting."""
        async with self.jira_semaphore:
            self.active_operations["jira_calls"] += 1
            try:
                return await func(*args, **kwargs)
            finally:
                self.active_operations["jira_calls"] -= 1

    def get_resource_usage(self) -> Dict[str, int]:
        """Get current resource usage."""
        return self.active_operations.copy()
```

**Files to Create:**
- `agent/resources/` - Resource management module
- `agent/resources/memory_monitor.py` - Memory usage monitoring
- `agent/resources/cleanup.py` - Automatic cleanup routines
- `agent/resources/resource_limiter.py` - Resource usage limiting
- `agent/resources/disk_manager.py` - Disk space management
- `tools/resource_analyzer.py` - Resource usage analysis

**Files to Modify:**
- `main.py` - Add resource monitoring startup
- `agent/performance.py` - Integrate with resource monitoring
- `agent/config.py` - Add resource management configuration

**Resource Management Features:**
- Real-time memory usage monitoring
- Automatic cleanup when resources are low
- Resource usage limiting to prevent overload
- Disk space management for cache and logs
- Emergency cleanup procedures

**Success Metrics:**
- ✅ Memory usage stays below 1GB under normal load
- ✅ Automatic cleanup prevents out-of-memory errors
- ✅ <5% performance impact from resource monitoring
- ✅ Zero system crashes due to resource exhaustion

---

### **Sprint 3.3: Security Enhancements** 🔒

**Objective**: Log sanitization and dependency security

**Implementation Details:**

#### Security Enhancement System
```python
# agent/security/sanitizer.py
class LogSanitizer:
    """Sanitize sensitive data from logs and tickets."""

    def __init__(self):
        self.patterns = {
            "email": re.compile(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b'),
            "phone": re.compile(r'\b\d{3}[-.]?\d{3}[-.]?\d{4}\b'),
            "ssn": re.compile(r'\b\d{3}[-]?\d{2}[-]?\d{4}\b'),
            "credit_card": re.compile(r'\b\d{4}[-\s]?\d{4}[-\s]?\d{4}[-\s]?\d{4}\b'),
            "api_key": re.compile(r'\b[Aa][Pp][Ii][-_]?[Kk][Ee][Yy][\s=:]+[A-Za-z0-9+/]{20,}\b'),
            "bearer_token": re.compile(r'\b[Bb][Ee][Aa][Rr][Ee][Rr]\s+[A-Za-z0-9+/]{20,}\b'),
            "password": re.compile(r'\b[Pp][Aa][Ss][Ss][Ww][Oo][Rr][Dd][\s=:]+\S+\b'),
            "secret": re.compile(r'\b[Ss][Ee][Cc][Rr][Ee][Tt][\s=:]+\S+\b'),
            "private_key": re.compile(r'-----BEGIN\s+(?:RSA\s+)?PRIVATE\s+KEY-----.*?-----END\s+(?:RSA\s+)?PRIVATE\s+KEY-----', re.DOTALL),
            "ip_address": re.compile(r'\b(?:[0-9]{1,3}\.){3}[0-9]{1,3}\b'),
            "uuid": re.compile(r'\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b'),
            "session_id": re.compile(r'\b[Ss][Ee][Ss][Ss][Ii][Oo][Nn][-_]?[Ii][Dd][\s=:]+[A-Za-z0-9]{20,}\b')
        }

        self.replacements = {
            "email": "<EMAIL_REDACTED>",
            "phone": "<PHONE_REDACTED>",
            "ssn": "<SSN_REDACTED>",
            "credit_card": "<CARD_REDACTED>",
            "api_key": "<API_KEY_REDACTED>",
            "bearer_token": "<BEARER_TOKEN_REDACTED>",
            "password": "<PASSWORD_REDACTED>",
            "secret": "<SECRET_REDACTED>",
            "private_key": "<PRIVATE_KEY_REDACTED>",
            "ip_address": "<IP_REDACTED>",
            "uuid": "<UUID_REDACTED>",
            "session_id": "<SESSION_ID_REDACTED>"
        }

    def sanitize_text(self, text: str) -> str:
        """Sanitize sensitive data from text."""
        if not text:
            return text

        sanitized = text

        for pattern_name, pattern in self.patterns.items():
            replacement = self.replacements[pattern_name]
            sanitized = pattern.sub(replacement, sanitized)

        return sanitized

    def sanitize_log_data(self, log_data: Dict[str, Any]) -> Dict[str, Any]:
        """Sanitize log data dictionary."""
        sanitized = {}

        for key, value in log_data.items():
            if isinstance(value, str):
                sanitized[key] = self.sanitize_text(value)
            elif isinstance(value, dict):
                sanitized[key] = self.sanitize_log_data(value)
            elif isinstance(value, list):
                sanitized[key] = [
                    self.sanitize_text(item) if isinstance(item, str) else item
                    for item in value
                ]
            else:
                sanitized[key] = value

        return sanitized

    def audit_sanitization(self, original: str, sanitized: str) -> Dict[str, Any]:
        """Audit what was sanitized."""
        changes = []

        for pattern_name, pattern in self.patterns.items():
            original_matches = len(pattern.findall(original))
            sanitized_matches = len(pattern.findall(sanitized))

            if original_matches > sanitized_matches:
                changes.append({
                    "pattern": pattern_name,
                    "instances_redacted": original_matches - sanitized_matches
                })

        return {
            "sanitization_applied": len(changes) > 0,
            "changes": changes,
            "timestamp": datetime.now().isoformat()
        }

# agent/security/dependency_scanner.py
class DependencyScanner:
    """Scan dependencies for known vulnerabilities."""

    def __init__(self):
        self.vulnerability_db = {}  # Could integrate with external service
        self.allowlist = set()  # Known safe packages
        self.blocklist = set()  # Known vulnerable packages

    async def scan_requirements(self, requirements_file: str = "requirements.txt") -> Dict[str, Any]:
        """Scan requirements file for vulnerabilities."""
        vulnerabilities = []

        try:
            with open(requirements_file) as f:
                requirements = f.readlines()

            for line in requirements:
                line = line.strip()
                if line and not line.startswith("#"):
                    package_info = self._parse_requirement(line)
                    vulnerability = await self._check_vulnerability(package_info)

                    if vulnerability:
                        vulnerabilities.append(vulnerability)

        except FileNotFoundError:
            return {"error": f"Requirements file {requirements_file} not found"}

        return {
            "scan_timestamp": datetime.now().isoformat(),
            "vulnerabilities": vulnerabilities,
            "total_packages_scanned": len(requirements),
            "vulnerable_packages": len(vulnerabilities)
        }

    def _parse_requirement(self, requirement: str) -> Dict[str, str]:
        """Parse requirement line."""
        # Simple parsing - could use pkg_resources for more robust parsing
        if "==" in requirement:
            name, version = requirement.split("==")
            return {"name": name.strip(), "version": version.strip()}
        else:
            return {"name": requirement.strip(), "version": None}

    async def _check_vulnerability(self, package_info: Dict[str, str]) -> Optional[Dict[str, Any]]:
        """Check if package has known vulnerabilities."""
        package_name = package_info["name"]

        if package_name in self.blocklist:
            return {
                "package": package_name,
                "severity": "high",
                "description": "Package is on security blocklist",
                "recommendation": "Remove or replace this package"
            }

        # In a real implementation, this would query a vulnerability database
        # like the GitHub Advisory Database, OSV, or similar

        return None

# tools/security_audit.py
class SecurityAuditor:
    """Comprehensive security audit tool."""

    def __init__(self):
        self.sanitizer = LogSanitizer()
        self.dependency_scanner = DependencyScanner()
        self.config_auditor = ConfigAuditor()

    async def run_full_audit(self) -> Dict[str, Any]:
        """Run comprehensive security audit."""
        audit_results = {
            "audit_timestamp": datetime.now().isoformat(),
            "dependency_scan": await self.dependency_scanner.scan_requirements(),
            "config_audit": await self.config_auditor.audit_configuration(),
            "log_sanitization_test": await self._test_log_sanitization(),
            "file_permissions": await self._check_file_permissions(),
            "secrets_exposure": await self._check_secrets_exposure()
        }

        # Calculate overall security score
        audit_results["security_score"] = self._calculate_security_score(audit_results)

        return audit_results

    async def _test_log_sanitization(self) -> Dict[str, Any]:
        """Test log sanitization effectiveness."""
        test_inputs = [
            "User john.doe@example.com failed to login",
            "API key: sk-1234567890abcdef for user authentication",
            "Credit card 4532-1234-5678-9012 was declined",
            "Connection failed to 192.168.1.100:8080"
        ]

        results = []
        for test_input in test_inputs:
            sanitized = self.sanitizer.sanitize_text(test_input)
            audit = self.sanitizer.audit_sanitization(test_input, sanitized)

            results.append({
                "original": test_input,
                "sanitized": sanitized,
                "audit": audit
            })

        return {
            "test_cases": len(test_inputs),
            "results": results,
            "all_sensitive_data_removed": all(
                result["audit"]["sanitization_applied"] for result in results
            )
        }
```

**Files to Create:**
- `agent/security/` - Security module
- `agent/security/sanitizer.py` - Log data sanitization
- `agent/security/dependency_scanner.py` - Dependency vulnerability scanning
- `agent/security/config_auditor.py` - Configuration security audit
- `tools/security_audit.py` - Security audit tool
- `.github/workflows/security-scan.yml` - Security scanning CI
- `docs/security.md` - Security documentation

**Files to Modify:**
- `agent/utils/logger.py` - Integrate log sanitization
- `agent/jira/utils.py` - Enhance text normalization for security
- `agent/nodes/ticket.py` - Sanitize ticket content
- `main.py` - Add security audit command

**Security Features:**
- Comprehensive log sanitization (emails, API keys, passwords, etc.)
- Dependency vulnerability scanning
- Configuration security auditing
- Secrets exposure detection
- File permission validation
- Security scoring and reporting

**Success Metrics:**
- ✅ 100% sensitive data redaction in logs and tickets
- ✅ Zero exposed secrets in code or logs
- ✅ Weekly dependency vulnerability scans
- ✅ Security audit score >90/100

---

## 📊 Implementation Tracking

### **Sprint Planning**
- **Sprint Duration**: 2 weeks
- **Team Capacity**: Assuming 1-2 developers
- **Velocity**: Target 8-12 story points per sprint
- **Review Cadence**: End of each sprint with stakeholder demo

### **Success Metrics Dashboard**

| Phase | Metric | Target | Current | Status |
|-------|--------|---------|---------|---------|
| **Phase 1** | Cache Hit Rate | 80% | TBD | 🟡 |
| | LLM Success Rate | 99% | TBD | 🟡 |
| | Config Error Rate | <2% | TBD | 🟡 |
| **Phase 2** | Throughput | 500+ logs/hour | TBD | 🟡 |
| | Processing Time | <30s avg | TBD | 🟡 |
| | Test Coverage | 95% | TBD | 🟡 |
| **Phase 3** | Error Recovery | 99% | TBD | 🟡 |
| | Memory Usage | <1GB | TBD | 🟡 |
| | Security Score | >90/100 | TBD | 🟡 |

### **Risk Assessment**

| Risk | Probability | Impact | Mitigation |
|------|-------------|---------|------------|
| Redis dependency issues | Medium | High | File-based cache fallback |
| LLM API rate limiting | High | Medium | Circuit breaker + fallback |
| Performance regression | Medium | Medium | Comprehensive benchmarking |
| Resource constraints | Low | High | Resource monitoring + limits |
| Security vulnerabilities | Low | High | Automated security scanning |

### **Dependencies & Prerequisites**

#### **External Dependencies**
- **Redis Server**: For persistent caching (Phase 1.1)
- **Monitoring Tools**: Prometheus/Grafana for advanced monitoring (Phase 2.3)
- **CI/CD Pipeline**: GitHub Actions for automated testing and security scanning

#### **Internal Dependencies**
- **Configuration Migration**: Existing `.env` files need profile migration
- **Data Migration**: Cache data format changes require migration scripts
- **API Compatibility**: Maintain backward compatibility during async migration

### **Rollback Strategy**

#### **Phase 1 Rollback**
- Disable persistent cache → revert to memory-only
- Disable circuit breaker → direct LLM calls
- Use single .env file → disable profiles

#### **Phase 2 Rollback**
- Disable async processing → revert to sequential
- Disable monitoring → remove metrics collection
- Disable advanced tests → use existing test suite

#### **Phase 3 Rollback**
- Disable error recovery → use basic error handling
- Disable resource monitoring → remove limits
- Disable security enhancements → revert to basic logging

---

## 🚀 Getting Started

### **Immediate Next Steps**

1. **Setup Development Environment**
   ```bash
   # Create feature branch
   git checkout -b feature/phase1-improvements

   # Install additional development dependencies
   pip install redis aioredis fastapi uvicorn pytest-asyncio
   ```

2. **Begin Phase 1.1 Implementation**
   ```bash
   # Create cache module structure
   mkdir -p agent/cache
   touch agent/cache/__init__.py
   touch agent/cache/base.py
   ```

3. **Setup Redis for Development**
   ```bash
   # Using Docker
   docker run -d -p 6379:6379 redis:alpine

   # Or using brew on macOS
   brew install redis
   brew services start redis
   ```

### **Implementation Order Priority**

1. **Start with Cache Backend** - Foundation for all other improvements
2. **Add Circuit Breaker** - Critical for production reliability
3. **Implement Profiles** - Simplifies development and deployment
4. **Add Async Processing** - Major performance improvement
5. **Enhance Testing** - Quality assurance for all changes
6. **Add Monitoring** - Operational visibility
7. **Implement Recovery** - Production resilience
8. **Add Resource Management** - System stability
9. **Security Enhancements** - Data protection

This implementation plan provides a structured path to transform the Dogcatcher Agent into a production-ready, enterprise-grade automation platform while maintaining backward compatibility and minimizing risk.

---

*Implementation Plan v1.0 | December 2025 | Based on Project Study Analysis*