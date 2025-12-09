# Phase 2: Performance & Scalability - Design Document

**Date**: December 4, 2025
**Branch**: `feature/phase2-performance-scalability`

## 🎯 Objectives

Transform Dogcatcher Agent from sequential to parallel processing:
- **3x throughput improvement** (50 logs/hour → 150+ logs/hour)
- **<30s average** processing time per log
- **Maintain reliability** with proper error isolation
- **Backward compatible** with existing workflows

---

## 📊 Current Architecture Analysis

### Sequential Processing Flow

```
Main Thread:
  ├─ Fetch all logs from Datadog
  ├─ For each log (sequentially):
  │   ├─ Analyze with LLM (30s)
  │   ├─ Check Jira duplicates (2s)
  │   └─ Create ticket if needed (1s)
  └─ Done

Bottleneck: One log at a time = 33s per log
100 logs = 55 minutes total
```

### Current Components

1. **`main.py`**: Synchronous entry point
2. **`agent/graph.py`**: LangGraph sequential workflow
3. **`agent/nodes/analysis.py`**: LLM analysis (already has circuit breaker)
4. **`agent/jira/client.py`**: Synchronous HTTP calls
5. **`agent/datadog.py`**: Synchronous log fetching

---

## 🚀 Proposed Parallel Architecture

### Async Processing Flow

```
Main Thread:
  ├─ Fetch all logs from Datadog (async)
  ├─ Create worker pool (5 workers)
  └─ Process logs in parallel:
      ├─ Worker 1: Log 1, 6, 11, ...
      ├─ Worker 2: Log 2, 7, 12, ...
      ├─ Worker 3: Log 3, 8, 13, ...
      ├─ Worker 4: Log 4, 9, 14, ...
      └─ Worker 5: Log 5, 10, 15, ...

Improvement: 5 logs at a time = 7s per batch
100 logs = 11 minutes total (5x faster!)
```

### Key Design Decisions

#### 1. **Dual-Mode Operation**
```python
# Backward compatible - existing mode still works
python main.py --dry-run

# New async mode
python main.py --dry-run --async --workers 5
```

#### 2. **Thread-Safe Deduplication**
```python
import asyncio

class ThreadSafeDeduplicator:
    def __init__(self):
        self._seen_logs = set()
        self._lock = asyncio.Lock()

    async def is_duplicate(self, log_key: str) -> bool:
        async with self._lock:
            if log_key in self._seen_logs:
                return True
            self._seen_logs.add(log_key)
            return False
```

#### 3. **Worker Pool with Semaphore**
```python
class AsyncLogProcessor:
    def __init__(self, max_workers: int = 5):
        self.semaphore = asyncio.Semaphore(max_workers)

    async def process_batch(self, logs: List[Dict]):
        tasks = [
            self._process_with_semaphore(log)
            for log in logs
        ]
        return await asyncio.gather(*tasks, return_exceptions=True)

    async def _process_with_semaphore(self, log: Dict):
        async with self.semaphore:
            return await self._process_log(log)
```

#### 4. **Async API Clients**
```python
import httpx

class AsyncJiraClient:
    def __init__(self):
        self.client = httpx.AsyncClient(
            timeout=30.0,
            limits=httpx.Limits(max_connections=10)
        )

    async def search_issues(self, jql: str):
        response = await self.client.get(
            f"{self.base_url}/search",
            params={"jql": jql}
        )
        return response.json()
```

---

## 📁 File Structure

### New Files

```
agent/
├─ async_graph.py          # Async version of graph.py
├─ async_processor.py      # Main async processor with worker pool
├─ async_nodes/            # Async versions of nodes
│  ├─ __init__.py
│  ├─ async_analysis.py    # Async analyze_log
│  ├─ async_ticket.py      # Async create_ticket
│  └─ async_fetch.py       # Async fetch_logs
├─ async_clients/          # Async API clients
│  ├─ __init__.py
│  ├─ async_jira.py        # Async Jira client (httpx)
│  └─ async_datadog.py     # Async Datadog client (httpx)
└─ utils/
   └─ thread_safe.py       # Thread-safe utilities

tests/
├─ integration/            # Integration tests
│  ├─ test_async_pipeline.py
│  └─ test_performance.py
└─ unit/
   └─ test_async_processor.py
```

### Modified Files

```
main.py                    # Add --async flag
agent/config.py            # Add async config options
requirements.txt           # Add httpx, aiofiles
```

---

## ⚙️ Configuration

### New Environment Variables

```bash
# Async Processing
ASYNC_ENABLED=false                # Enable async mode
ASYNC_MAX_WORKERS=5                # Number of parallel workers
ASYNC_BATCH_SIZE=10                # Process logs in batches
ASYNC_TIMEOUT_SECONDS=60           # Timeout per log processing

# Connection Pooling
HTTP_MAX_CONNECTIONS=20            # Max HTTP connections
HTTP_MAX_KEEPALIVE=10              # Max keepalive connections
HTTP_TIMEOUT_SECONDS=30            # HTTP request timeout
```

### CLI Arguments

```bash
--async                   # Enable async mode
--workers N               # Number of parallel workers (default: 5)
--batch-size N            # Batch size for processing (default: 10)
```

---

## 🔒 Safety & Error Handling

### 1. Error Isolation
```python
# One log failure doesn't stop others
results = await asyncio.gather(*tasks, return_exceptions=True)

for i, result in enumerate(results):
    if isinstance(result, Exception):
        log_error(f"Log {i} failed", error=str(result))
        # Continue processing other logs
```

### 2. Resource Limits
```python
# Prevent resource exhaustion
semaphore = asyncio.Semaphore(5)  # Max 5 concurrent
timeout = asyncio.timeout(60)      # 60s per log max
```

### 3. Graceful Degradation
```python
# If async fails, fallback to sync
try:
    await process_async(logs)
except Exception as e:
    log_warning("Async failed, falling back to sync", error=str(e))
    process_sync(logs)
```

---

## 🧪 Testing Strategy

### Performance Benchmarks
```python
@pytest.mark.benchmark
async def test_async_vs_sync_performance():
    logs = generate_test_logs(100)

    # Sync baseline
    sync_start = time.time()
    sync_results = process_sync(logs)
    sync_time = time.time() - sync_start

    # Async comparison
    async_start = time.time()
    async_results = await process_async(logs)
    async_time = time.time() - async_start

    # Assert 3x improvement
    assert async_time < sync_time / 3
    assert len(async_results) == len(sync_results)
```

### Correctness Tests
```python
@pytest.mark.asyncio
async def test_duplicate_detection_thread_safe():
    """Ensure no duplicate tickets when processing in parallel."""
    duplicate_logs = [same_log] * 10

    results = await process_async(duplicate_logs, workers=5)

    # Only one ticket should be created
    tickets_created = [r for r in results if r["action"] == "created"]
    assert len(tickets_created) == 1
```

---

## 📊 Expected Performance Improvements

### Throughput

| Scenario | Sequential | Async (5 workers) | Improvement |
|----------|-----------|-------------------|-------------|
| 10 logs | 5.5 min | 1.1 min | 5x faster |
| 50 logs | 27.5 min | 5.5 min | 5x faster |
| 100 logs | 55 min | 11 min | 5x faster |

### Resource Usage

| Metric | Sequential | Async | Notes |
|--------|-----------|-------|-------|
| Memory | ~50MB | ~120MB | Worth it for 5x speed |
| CPU | 10-20% | 40-60% | Acceptable |
| Network | Linear | Parallel | Better utilization |

---

## 🚧 Implementation Phases

### Phase 2.1: Async Parallel Processing (Current Sprint)

**Week 1-2**: Core async infrastructure
- [ ] Async processor with worker pool
- [ ] Thread-safe deduplication
- [ ] Async Jira client (httpx)
- [ ] Async Datadog client

**Week 3**: Integration
- [ ] Async graph implementation
- [ ] CLI flag integration
- [ ] Configuration management

**Week 4**: Testing
- [ ] Unit tests for async components
- [ ] Integration tests
- [ ] Performance benchmarks

### Phase 2.2: Enhanced Testing (Next Sprint)
- Comprehensive integration test suite
- Realistic test data
- Failure injection testing

### Phase 2.3: Monitoring Dashboard (Following Sprint)
- Metrics collection
- Performance dashboard
- Alerting system

---

## 🎯 Success Criteria

- ✅ 3x throughput improvement validated
- ✅ All existing tests pass
- ✅ New async tests pass (95%+ coverage)
- ✅ Zero regressions in ticket creation
- ✅ Error isolation working (one failure doesn't stop others)
- ✅ Resource usage within acceptable limits
- ✅ Backward compatible (sync mode still works)

---

## 🔗 Dependencies

### New Libraries
```
httpx>=0.27.0          # Async HTTP client
aiofiles>=24.0.0       # Async file operations
pytest-asyncio>=0.23.0 # Already added in Phase 1
```

---

## 📝 Next Steps

1. **Review this design** with team
2. **Start implementation** with async processor
3. **Incremental rollout** (sync → async optional → async default)
4. **Monitor performance** in production
5. **Iterate based on metrics**

---

**Status**: Design phase - ready for implementation
**Expected Duration**: 4 weeks
**Risk Level**: Medium (significant architectural change)
**Mitigation**: Dual-mode keeps fallback to sync processing
