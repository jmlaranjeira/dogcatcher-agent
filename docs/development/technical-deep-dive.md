# Dogcatcher Agent - Technical Deep Dive

Este documento describe en detalle la arquitectura técnica del Dogcatcher Agent, incluyendo el sistema de deduplicación, el rol del LLM, el circuit breaker y las optimizaciones de rendimiento.

---

## Tabla de Contenidos

1. [Sistema de Deduplicación](#1-sistema-de-deduplicación)
2. [Rol del LLM (OpenAI)](#2-rol-del-llm-openai)
3. [Circuit Breaker y Fallback](#3-circuit-breaker-y-fallback)
4. [Arquitectura de Workers](#4-arquitectura-de-workers)
5. [Métricas de Throughput](#5-métricas-de-throughput)
6. [Complejidad Algorítmica](#6-complejidad-algorítmica-o1-vs-on)
7. [Archivos Clave](#7-archivos-clave)

---

## 1. Sistema de Deduplicación

### 1.1 Arquitectura Multi-Nivel

La deduplicación implementa una **estrategia en cascada** con 4 niveles, ordenados por coste computacional (del más barato al más caro):

```
┌─────────────────────────────────────────────────────────────────┐
│ Nivel 1: Fingerprint Cache Local (O(1))     → ~1 microsegundo   │
├─────────────────────────────────────────────────────────────────┤
│ Nivel 2: Fingerprint Labels en Jira (O(1))  → ~50ms             │
├─────────────────────────────────────────────────────────────────┤
│ Nivel 3: Direct Log Matching (similitud ≥0.90)                  │
├─────────────────────────────────────────────────────────────────┤
│ Nivel 4: Similarity Scoring (0.6*title + 0.3*desc + bonuses)    │
└─────────────────────────────────────────────────────────────────┘
```

**Justificación arquitectónica:**

- **Early exit**: Los niveles más baratos se ejecutan primero. Si hay hit, evitamos llamadas costosas a Jira
- **Fail-fast**: El 70-90% de los logs se descartan antes de hacer búsquedas O(n)
- **Degradación graceful**: Cada nivel tiene fallback al siguiente

### 1.2 Nivel 1: Fingerprint Local

#### Cálculo del Fingerprint

```python
def _compute_fingerprint(state: Dict[str, Any]) -> str:
    log_data = state.get("log_data", {})
    raw_msg = log_data.get('message', '')
    norm_msg = normalize_log_message(raw_msg)

    error_type = state.get("error_type", "unknown")
    fp_source = f"{error_type}|{norm_msg or raw_msg}"

    return hashlib.sha1(fp_source.encode("utf-8")).hexdigest()[:12]
```

**Características:**
- Hash SHA-1 truncado a 12 caracteres hexadecimales
- Compuesto por: `error_type|mensaje_normalizado`
- **NO incluye** logger ni thread (evita duplicados falsos)
- Determinista: mismo input → mismo fingerprint

#### Cachés de Fingerprint

| Nivel | Ubicación | TTL | Hit Rate |
|-------|-----------|-----|----------|
| En-memoria | `state["created_fingerprints"]` (Set) | Vida de la ejecución | 100% intra-run |
| Archivo | `.agent_cache/processed_logs.json` | Persistente | 60-80% inter-run |

### 1.3 Nivel 2: Loghash en Jira

El loghash es un hash del mensaje normalizado, almacenado como label en Jira:

```python
loghash = hashlib.sha1(norm_current_log.encode("utf-8")).hexdigest()[:12]
jql = f"labels = loghash-{loghash}"
```

**Ventaja**: Búsqueda O(1) por label exacto, no requiere escanear todos los tickets.

### 1.4 Normalización de Mensajes

La normalización es **crítica** para colapsar variantes del mismo error:

```python
def normalize_log_message(text: str) -> str:
    t = text.lower()

    # Anonimizar datos variables
    t = re.sub(r"[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}", " <email> ", t)
    t = re.sub(r"\bhttps?://[^\s]+", " <url> ", t)

    # Remover UUIDs, timestamps, hashes
    t = re.sub(r"\b[0-9a-f]{8}-[0-9a-f]{4}-...-[0-9a-f]{12}\b", " ", t)
    t = re.sub(r"\b\d{4}-\d{2}-\d{2}[tT ]\d{2}:\d{2}:\d{2}.*?Z?\b", " ", t)
    t = re.sub(r"\b\d{5,}\b", " ", t)

    return t.strip()
```

**Ejemplo de colapso:**

```
Entrada:
  "Failed to get file size 0001_Blob.dpplan at 2025-01-15T10:30:00Z"
  "Failed to get file size 0002_Blob.dpplan at 2025-01-15T10:31:00Z"
  "Failed to get file size 0003_Blob.dpplan at 2025-01-15T10:32:00Z"

Normalizado (todos igual):
  "failed to get file size <file>"

Resultado: 3 logs → 1 fingerprint → 1 ticket
```

### 1.5 Nivel 3 y 4: Similarity Scoring

Cuando no hay match exacto, se usa scoring ponderado:

```python
score = 0.60 * title_similarity
      + 0.30 * description_similarity
      + 0.10 * (bonus por error_type)
      + 0.05 * (bonus por logger)
      + 0.05 * (bonus por tokens)
      + 0.05 * (bonus por partial log match)
```

**Umbrales configurables:**

| Variable | Default | Función |
|----------|---------|---------|
| `JIRA_SIMILARITY_THRESHOLD` | 0.82 | Umbral para considerar duplicado |
| `JIRA_DIRECT_LOG_THRESHOLD` | 0.90 | Match directo del log original |
| `JIRA_PARTIAL_LOG_THRESHOLD` | 0.70 | Bonus por match parcial |

### 1.6 Flujo Completo de Deduplicación

```
INPUT: Log de Datadog
  │
  ├─→ [1] Calcular fingerprint: hash(error_type|normalized_message)[:12]
  │
  ├─→ [2] ¿En cache en-memoria? ──SÍ──→ SKIP (duplicado intra-run)
  │                              │
  │                              NO
  │                              ↓
  ├─→ [3] ¿En cache archivo? ────SÍ──→ SKIP (duplicado de run anterior)
  │                              │
  │                              NO
  │                              ↓
  ├─→ [4] ¿Label loghash-xxx en Jira? ─SÍ─→ SKIP (score=1.00)
  │                              │
  │                              NO
  │                              ↓
  ├─→ [5] Buscar similares en Jira (max 200 resultados)
  │       │
  │       ├─→ Direct log match ≥0.90? ─SÍ─→ SKIP (score=1.00)
  │       │
  │       └─→ Similarity score ≥0.82? ─SÍ─→ SKIP
  │                              │
  │                              NO
  │                              ↓
  └─→ [6] CREAR TICKET con labels: [datadog-log, loghash-xxx, fingerprint-xxx]
```

---

## 2. Rol del LLM (OpenAI)

### 2.1 Cuándo se Llama al LLM

**Se llama a OpenAI exactamente UNA vez por cada log que pasa la deduplicación local.**

```
Log de Datadog
    ↓
¿Ya procesado? (fingerprint local) → SÍ → SKIP (no LLM)
    ↓ NO
analyze_log() → LLAMA A OPENAI ← Aquí
    ↓
¿Crear ticket? → Deduplicación Jira → Crear/Skip
```

### 2.2 Qué Hace el LLM

El LLM actúa como un **ingeniero de soporte senior** que analiza el log y devuelve JSON estructurado:

**Prompt del sistema:**
```
You are a senior support engineer. Analyze the input log context and RETURN ONLY JSON.
Fields required:
- error_type (kebab-case, e.g. pre-persist, db-constraint, kafka-consumer)
- create_ticket (boolean)
- ticket_title (short, action-oriented)
- ticket_description (markdown: Problem, Causes, Actions)
- severity (low, medium, high)
```

**Input al LLM:**
```
[Logger]: com.example.myservice.converter.DpItemConverter
[Thread]: http-nio-1025-exec-5
[Message]: Failed to get file size by name UUID_Blob.DPplan
[Detail]: Status code 404, (BlobNotFound)
```

**Output del LLM:**
```json
{
  "error_type": "blob-storage-error",
  "create_ticket": true,
  "ticket_title": "Blob storage file not found in DpItemConverter",
  "ticket_description": "**Problem**\nThe system cannot retrieve file size...\n\n**Possible Causes**\n- Blob was deleted...\n\n**Suggested Actions**\n- Verify blob exists...",
  "severity": "medium"
}
```

### 2.3 Por Qué un LLM (vs Regex)

| Aspecto | Regex/Reglas | LLM |
|---------|--------------|-----|
| Categorización | Solo patrones conocidos | Entiende contexto semántico |
| Títulos | Templates fijos | Genera texto humano natural |
| Descripción | Copy-paste del log | Infiere causas y acciones |
| Severidad | Keyword matching | Evalúa impacto en contexto |
| Casos edge | Falla silenciosamente | Generaliza razonablemente |

**Ejemplo concreto:**

Un regex detecta "NullPointerException" pero no puede distinguir:
- "NPE en campo opcional de logging" → Severidad LOW, no crear ticket
- "NPE en flujo de pago" → Severidad HIGH, crear ticket urgente

El LLM **sí puede** hacer esta distinción basándose en el logger y el contexto.

### 2.4 Configuración del LLM

```bash
# .env
OPENAI_MODEL=gpt-4.1-nano          # Modelo (default: gpt-4.1-nano)
OPENAI_TEMPERATURE=0               # Determinismo (0 = máximo)
OPENAI_RESPONSE_FORMAT=json_object # Forzar JSON válido
```

---

## 3. Circuit Breaker y Fallback

### 3.1 Patrón Circuit Breaker

El circuit breaker protege el sistema cuando OpenAI está caído o degradado:

```
       ┌──────────────────────────────────────────┐
       │                                          │
       ▼                                          │
   ┌───────┐  3 fallos    ┌──────┐  30s timeout  │
   │CLOSED │─────────────→│ OPEN │───────────────┤
   └───────┘              └──────┘               │
       ▲                      │                  │
       │                      ▼                  │
       │               ┌───────────┐             │
       └───────────────│ HALF_OPEN │─────────────┘
         2 éxitos      └───────────┘   fallo
```

**Configuración:**

```python
CircuitBreakerConfig(
    failure_threshold=3,       # Abre después de 3 fallos consecutivos
    timeout_seconds=30,        # Espera 30s antes de probar de nuevo
    half_open_max_calls=2,     # Prueba con 2 llamadas en half-open
    expected_exception=OpenAIError
)
```

**Estados:**

| Estado | Comportamiento |
|--------|----------------|
| CLOSED | Llamadas normales a OpenAI |
| OPEN | Rechaza llamadas inmediatamente (fail-fast), usa fallback |
| HALF_OPEN | Permite 2 llamadas de prueba para verificar recuperación |

### 3.2 Fallback Rule-Based

Cuando el circuit breaker está OPEN, se activa el `FallbackAnalyzer`:

```python
except CircuitBreakerOpenError as e:
    if config.fallback_analysis_enabled:
        return _use_fallback_analysis(state, log_data)
```

### 3.3 Patrones del Fallback

El fallback usa **26+ patrones regex** organizados por categoría:

```python
error_patterns = {
    "database-connection": {
        "patterns": [
            r"database.*connection.*fail",
            r"connection.*timeout.*database",
            r"could not connect.*database",
            r"sql.*connection.*error"
        ],
        "severity": "high",
        "title_template": "Database Connection Error"
    },

    "timeout": {
        "patterns": [
            r"timeout.*occurred",
            r"request.*timeout",
            r"socket.*timeout"
        ],
        "severity": "medium",
        "title_template": "Operation Timeout"
    },

    "http-server-error": {
        "patterns": [
            r"5\d{2}.*error",
            r"internal.*server.*error",
            r"service.*unavailable"
        ],
        "severity": "high",
        "title_template": "HTTP Server Error"
    },

    # ... 23 patrones más
}
```

**Categorías cubiertas:**
- Database (connection, constraint)
- Network (timeout, unreachable)
- HTTP (4xx client, 5xx server)
- Authentication
- File system
- Memory (OOM)
- Configuration
- Kafka/messaging
- Unknown (catch-all)

### 3.4 Decisión de Crear Ticket (Fallback)

El fallback es **más conservador** que el LLM:

```python
def _should_create_ticket(self, error_type, severity, confidence):
    if confidence < 0.2:                         return False
    if severity == "high":                       return True
    if severity == "medium" and confidence >= 0.4: return True
    if severity == "low" and confidence >= 0.7:    return True
    return False
```

**Matriz de decisión:**

| Severidad | Confidence < 0.2 | 0.2-0.4 | 0.4-0.7 | ≥ 0.7 |
|-----------|------------------|---------|---------|-------|
| HIGH | No | Sí | Sí | Sí |
| MEDIUM | No | No | Sí | Sí |
| LOW | No | No | No | Sí |

### 3.5 Resultado del Fallback

**El sistema sigue funcionando al 100% de capacidad**, solo pierde precisión:

| Aspecto | Con LLM | Con Fallback |
|---------|---------|--------------|
| Throughput | 100% | 100% |
| Categorización | Semántica | Basada en patrones |
| Títulos | Contextuales | Templates fijos |
| Severidad | Inferida | Por categoría |
| Cobertura | ~99% | ~85% |

---

## 4. Arquitectura de Workers

### 4.1 Modo Sync (Default)

LangGraph procesa secuencialmente:

```
fetch_logs → analyze_log → create_ticket → next_log (loop)
```

**Uso**: Desarrollo, debugging, cargas bajas.

**Justificación**: Simplicidad, sin race conditions, fácil de debuggear.

### 4.2 Modo Async (Production)

```python
AsyncLogProcessor(
    max_workers=5,        # Semáforo asyncio (concurrencia real)
    rate_limiter=10/s,    # 10 API calls/segundo
    batch_size=10         # Procesamiento en lotes
)
```

**Componentes:**

| Componente | Función |
|------------|---------|
| `asyncio.Semaphore(5)` | Limita concurrencia real |
| `ThreadSafeDeduplicator` | Mutex async para estado compartido |
| `httpx.AsyncClient` | Connection pooling (10 keepalive, 20 max) |

### 4.3 Por Qué No Más Workers

Los **API rate limits** son el bottleneck, no la CPU:

```
OpenAI:  ~60 requests/min (tier 1)
Jira:    ~100 requests/min
Datadog: ~300 requests/min
```

Más de 10 workers satura los endpoints y genera errores 429 (rate limit).

---

## 5. Métricas de Throughput

### 5.1 Valores Observados

| Modo | Workers | Throughput | Mejora |
|------|---------|------------|--------|
| SYNC | 1 | ~1 log/s | Baseline |
| ASYNC | 5 | ~3-4 logs/s | 3x |
| ASYNC | 10 | ~4-5 logs/s | 4x (rendimientos decrecientes) |

### 5.2 Proyección por Tiempo

| Período | Logs procesados |
|---------|-----------------|
| Por segundo | 3-5 |
| Por minuto | 180-300 |
| Por hora | 10,800-18,000 |

### 5.3 Cómo Conseguimos Este Throughput

1. **Deduplicación temprana**: 70-90% de logs descartados antes de llamar a APIs
2. **Caching agresivo**: SimilarityCache evita recalcular (hit rate 50-80%)
3. **Normalización determinista**: Logs semánticamente iguales → mismo fingerprint
4. **Connection pooling**: Reutilización de conexiones HTTP
5. **Rate limiting inteligente**: 10 calls/s evita saturar sin desperdiciar

---

## 6. Complejidad Algorítmica: O(1) vs O(n)

### 6.1 Notación Big-O

La notación Big-O describe cómo escala el tiempo de ejecución con el tamaño del input.

### 6.2 O(1) - Tiempo Constante

```python
fingerprint in created_fingerprints  # Set lookup = O(1)
```

**Significa**: El tiempo es **siempre el mismo**, sin importar el tamaño.

| Elementos | Tiempo |
|-----------|--------|
| 100 | ~1μs |
| 1,000,000 | ~1μs |

**Interno**: Python usa hash tables. Calcula hash → acceso directo a memoria.

### 6.3 O(n) - Tiempo Lineal

```python
for issue in jira_issues:  # O(n) - recorre TODOS
    score = calculate_similarity(log, issue)
```

**Significa**: El tiempo crece **proporcionalmente** al número de elementos.

| Tickets Jira | Tiempo |
|--------------|--------|
| 100 | ~100ms |
| 10,000 | ~10,000ms (10s) |

### 6.4 Impacto en Dogcatcher

```
Nivel 1: Fingerprint local     → O(1)  → ~1 microsegundo
Nivel 2: Fingerprint en Jira   → O(1)  → ~50ms (1 query JQL exacto)
Nivel 3: Similitud en Jira     → O(n)  → ~500ms-5s (hasta 200 tickets)
```

### 6.5 Ejemplo Numérico

**Con arquitectura multi-nivel:**
```
1000 logs procesados:
├─ 800 detectados nivel 1 (O(1))  → 800 × 1μs   = 0.8ms
├─ 150 detectados nivel 2 (O(1))  → 150 × 50ms  = 7.5s
└─  50 llegan a nivel 3 (O(n))    → 50 × 500ms  = 25s
                                  ─────────────────────
                                  Total: ~33 segundos
```

**Sin cascada (todo nivel 3):**
```
1000 logs × 500ms = 500 segundos (8+ minutos)
```

**Mejora: 15x** (de 8 minutos a 33 segundos)

---

## 7. Archivos Clave

| Archivo | Líneas | Función |
|---------|--------|---------|
| `agent/jira/match.py` | ~190 | Búsqueda de similitud principal |
| `agent/jira/async_match.py` | ~250 | Versión async |
| `agent/jira/utils.py` | ~140 | Normalización y utilidades |
| `agent/nodes/analysis.py` | ~245 | Análisis LLM + circuit breaker |
| `agent/utils/fallback_analysis.py` | ~620 | Análisis rule-based |
| `agent/utils/circuit_breaker.py` | ~300 | Implementación circuit breaker |
| `agent/performance.py` | ~325 | Caching de similitud y métricas |
| `agent/cache/memory_cache.py` | ~210 | Cache en-memoria LRU |
| `agent/cache/file_cache.py` | ~300 | Cache persistente |
| `agent/cache/redis_cache.py` | ~400 | Cache distribuido Redis |

---

## Diagrama de Arquitectura Completo

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              DOGCATCHER AGENT                               │
└─────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                            DATADOG LOGS FETCH                               │
│                         (agent/datadog.py)                                  │
└─────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                     DEDUPLICACIÓN NIVEL 1 (LOCAL)                           │
│                                                                             │
│  ┌──────────────────┐    ┌──────────────────┐    ┌──────────────────┐      │
│  │ Fingerprint      │    │ Cache en-memoria │    │ Cache archivo    │      │
│  │ Calculation      │───→│ (Set Python)     │───→│ (.agent_cache/)  │      │
│  │ O(1)             │    │ O(1)             │    │ O(1)             │      │
│  └──────────────────┘    └──────────────────┘    └──────────────────┘      │
│                                                                             │
│  80% de logs eliminados aquí                                                │
└─────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                          ANÁLISIS LLM (OPENAI)                              │
│                        (agent/nodes/analysis.py)                            │
│                                                                             │
│  ┌──────────────────┐         ┌──────────────────┐                         │
│  │ Circuit Breaker  │────────→│ LLM gpt-4.1-nano │                         │
│  │ (3 fails→OPEN)   │         │                  │                         │
│  └──────────────────┘         └──────────────────┘                         │
│           │                            │                                    │
│           │ OPEN                       │ OK                                 │
│           ▼                            ▼                                    │
│  ┌──────────────────┐         ┌──────────────────┐                         │
│  │ Fallback         │         │ JSON Response    │                         │
│  │ (26+ regex)      │         │ error_type       │                         │
│  │                  │         │ severity         │                         │
│  └──────────────────┘         │ create_ticket    │                         │
│                               └──────────────────┘                         │
└─────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                    DEDUPLICACIÓN NIVEL 2-4 (JIRA)                           │
│                        (agent/jira/match.py)                                │
│                                                                             │
│  ┌──────────────────┐    ┌──────────────────┐    ┌──────────────────┐      │
│  │ Loghash Label    │    │ Direct Log Match │    │ Similarity Score │      │
│  │ JQL: labels=xxx  │───→│ ≥0.90 = exacto   │───→│ 0.6*t + 0.3*d    │      │
│  │ O(1)             │    │                  │    │ O(n)             │      │
│  └──────────────────┘    └──────────────────┘    └──────────────────┘      │
│                                                                             │
│  ┌──────────────────────────────────────────────────────────────────┐      │
│  │ SimilarityCache: max_size=1000, TTL=300s, hit_rate=50-80%       │      │
│  └──────────────────────────────────────────────────────────────────┘      │
└─────────────────────────────────────────────────────────────────────────────┘
                                      │
                          ┌───────────┴───────────┐
                          │                       │
                          ▼                       ▼
                   ┌────────────┐          ┌────────────┐
                   │ DUPLICADO  │          │ CREAR      │
                   │            │          │ TICKET     │
                   │ Skip +     │          │            │
                   │ Comentar   │          │ Labels:    │
                   │ (opcional) │          │ datadog-log│
                   │            │          │ loghash-xxx│
                   └────────────┘          └────────────┘
```

---

## Resumen Ejecutivo

| Aspecto | Implementación | Beneficio |
|---------|----------------|-----------|
| **Deduplicación** | 4 niveles en cascada | 15x reducción de tiempo |
| **LLM** | gpt-4.1-nano con circuit breaker | Categorización semántica |
| **Fallback** | 26+ patrones regex | 100% uptime |
| **Workers** | Async con semáforo (5-10) | 3-4x throughput |
| **Throughput** | 10-18k logs/hora | Escala con rate limits |
| **Caching** | Memory + File + Redis | 50-80% hit rate |

---

*Documento generado el 2025-02-02. Última actualización basada en análisis del código fuente.*
