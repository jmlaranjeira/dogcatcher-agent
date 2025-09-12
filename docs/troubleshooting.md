# Troubleshooting Guide - Dogcatcher Agent

This guide helps you diagnose and resolve common issues with the Dogcatcher Agent.

## 🚨 Quick Diagnosis

### Check System Status

```bash
# 1. Verify Python environment
python --version  # Should be 3.11+

# 2. Check dependencies
pip list | grep -E "(openai|requests|pydantic|langchain)"

# 3. Validate configuration
python -c "from agent.config import get_config; print('Config OK' if not get_config().validate_configuration() else 'Config Issues')"

# 4. Test API connections
python -c "from agent.jira.client import is_configured; print('Jira OK' if is_configured() else 'Jira Issues')"
```

### Common Error Patterns

| Error Pattern | Likely Cause | Quick Fix |
|---------------|--------------|-----------|
| `Configuration validation failed` | Missing/invalid .env | Check .env file |
| `Missing Jira configuration` | API keys not set | Verify JIRA_* variables |
| `Datadog request failed` | API key/network issue | Test Datadog connection |
| `No logs to process` | Query too restrictive | Adjust DATADOG_* settings |
| `Similarity cache hit rate: 0%` | Cache not working | Check cache configuration |

## 🔧 Configuration Issues

### Problem: Configuration Validation Failed

**Symptoms**:
```
❌ Configuration issues found:
  - OPENAI_API_KEY is required
  - JIRA_DOMAIN is required
```

**Diagnosis**:
```bash
# Check if .env file exists
ls -la .env

# Check .env file contents (without exposing secrets)
grep -E "^[A-Z]" .env | cut -d'=' -f1
```

**Solutions**:

1. **Missing .env file**:
   ```bash
   cp .env.example .env
   # Edit .env with your values
   ```

2. **Missing required variables**:
   ```bash
   # Add missing variables to .env
   echo "OPENAI_API_KEY=sk-your-key-here" >> .env
   echo "JIRA_DOMAIN=your-domain.atlassian.net" >> .env
   ```

3. **Invalid variable values**:
   ```bash
   # Check for invalid values
   python -c "
   from agent.config import get_config
   issues = get_config().validate_configuration()
   for issue in issues:
       print(f'  - {issue}')
   "
   ```

### Problem: Environment Variable Not Loading

**Symptoms**:
- Configuration shows default values instead of .env values
- Changes to .env don't take effect

**Diagnosis**:
```bash
# Check if .env is in correct location
pwd
ls -la .env

# Check .env file format
cat .env | head -5
```

**Solutions**:

1. **Wrong file location**:
   ```bash
   # Ensure .env is in project root
   mv .env /path/to/dogcatcher-agent/
   ```

2. **Invalid .env format**:
   ```bash
   # Fix common format issues
   # Remove spaces around =
   sed -i 's/ = /=/g' .env
   
   # Remove quotes if present
   sed -i 's/"//g' .env
   ```

3. **Reload configuration**:
   ```bash
   # Restart Python process to reload .env
   python main.py --dry-run
   ```

## 🌐 API Connection Issues

### Problem: Datadog API Connection Failed

**Symptoms**:
```
❌ Datadog request failed: 401 Unauthorized
❌ Missing Datadog configuration: DATADOG_API_KEY
```

**Diagnosis**:
```bash
# Test Datadog API key
curl -X GET "https://api.datadoghq.eu/api/v1/validate" \
  -H "DD-API-KEY: $DATADOG_API_KEY" \
  -H "DD-APPLICATION-KEY: $DATADOG_APP_KEY"
```

**Solutions**:

1. **Invalid API keys**:
   ```bash
   # Get new API keys from Datadog
   # https://app.datadoghq.eu/organization-settings/application-keys
   ```

2. **Wrong Datadog site**:
   ```bash
   # Check your Datadog site
   # US: datadoghq.com
   # EU: datadoghq.eu
   # US3: us3.datadoghq.com
   # US5: us5.datadoghq.com
   ```

3. **Network connectivity**:
   ```bash
   # Test network connectivity
   ping api.datadoghq.eu
   curl -I https://api.datadoghq.eu/api/v1/validate
   ```

### Problem: Jira API Connection Failed

**Symptoms**:
```
❌ Jira search failed: 401 Unauthorized
❌ Missing Jira configuration in .env
```

**Diagnosis**:
```bash
# Test Jira API connection
curl -X GET "https://your-domain.atlassian.net/rest/api/3/myself" \
  -H "Authorization: Basic $(echo -n 'user@example.com:api-token' | base64)"
```

**Solutions**:

1. **Invalid credentials**:
   ```bash
   # Generate new API token
   # https://id.atlassian.com/manage-profile/security/api-tokens
   ```

2. **Wrong domain format**:
   ```bash
   # Correct format: your-domain.atlassian.net
   # Not: https://your-domain.atlassian.net
   ```

3. **Project key issues**:
   ```bash
   # Verify project key exists
   curl -X GET "https://your-domain.atlassian.net/rest/api/3/project/YOUR_PROJECT" \
     -H "Authorization: Basic $(echo -n 'user@example.com:api-token' | base64)"
   ```

### Problem: OpenAI API Connection Failed

**Symptoms**:
```
❌ OpenAI API error: 401 Unauthorized
❌ Invalid API key format
```

**Diagnosis**:
```bash
# Test OpenAI API key
curl -X POST "https://api.openai.com/v1/chat/completions" \
  -H "Authorization: Bearer $OPENAI_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model": "gpt-3.5-turbo", "messages": [{"role": "user", "content": "test"}]}'
```

**Solutions**:

1. **Invalid API key**:
   ```bash
   # Get new API key from OpenAI
   # https://platform.openai.com/api-keys
   ```

2. **Insufficient credits**:
   ```bash
   # Check OpenAI usage and billing
   # https://platform.openai.com/usage
   ```

3. **Model availability**:
   ```bash
   # Check available models
   curl -X GET "https://api.openai.com/v1/models" \
     -H "Authorization: Bearer $OPENAI_API_KEY"
   ```

## 📊 Performance Issues

### Problem: Slow Duplicate Detection

**Symptoms**:
- Long processing times for ticket creation
- High API call volumes
- Low cache hit rates

**Diagnosis**:
```bash
# Check performance metrics
python main.py --dry-run | grep -E "(Similarity cache|Performance metrics)"

# Check cache statistics
python -c "
from agent.performance import get_similarity_cache
stats = get_similarity_cache().get_stats()
print(f'Cache hit rate: {stats[\"hit_rate_percent\"]}%')
print(f'Cache size: {stats[\"size\"]}/{stats[\"max_size\"]}')
"
```

**Solutions**:

1. **Increase similarity threshold**:
   ```bash
   # More restrictive duplicate detection
   JIRA_SIMILARITY_THRESHOLD=0.85
   ```

2. **Reduce search window**:
   ```bash
   # Search fewer days back
   JIRA_SEARCH_WINDOW_DAYS=180
   ```

3. **Reduce max results**:
   ```bash
   # Process fewer results per search
   JIRA_SEARCH_MAX_RESULTS=100
   ```

4. **Clear and rebuild cache**:
   ```bash
   python -c "
   from agent.performance import clear_performance_caches
   clear_performance_caches()
   print('Cache cleared')
   "
   ```

### Problem: High Memory Usage

**Symptoms**:
- Memory usage growing over time
- System becoming unresponsive
- Out of memory errors

**Diagnosis**:
```bash
# Monitor memory usage
python -c "
import psutil
import os
process = psutil.Process(os.getpid())
print(f'Memory usage: {process.memory_info().rss / 1024 / 1024:.1f} MB')
"

# Check cache sizes
python -c "
from agent.performance import get_similarity_cache
cache = get_similarity_cache()
print(f'Cache size: {len(cache.cache)}')
"
```

**Solutions**:

1. **Reduce cache sizes**:
   ```bash
   # Edit agent/performance.py
   # Reduce max_size in SimilarityCache constructor
   ```

2. **Clear caches periodically**:
   ```bash
   # Add cache clearing to your workflow
   python -c "
   from agent.performance import clear_performance_caches
   clear_performance_caches()
   "
   ```

3. **Optimize data structures**:
   ```bash
   # Use more memory-efficient configurations
   DATADOG_LIMIT=25  # Reduce log batch size
   JIRA_SEARCH_MAX_RESULTS=50  # Reduce search results
   ```

## 🧪 Testing Issues

### Problem: Tests Failing

**Symptoms**:
```
FAILED tests/unit/test_ticket_creation.py::TestTicketValidation::test_validate_ticket_fields_success
```

**Diagnosis**:
```bash
# Run specific failing test with verbose output
pytest tests/unit/test_ticket_creation.py::TestTicketValidation::test_validate_ticket_fields_success -v -s

# Check test environment
python -c "
import sys
print(f'Python: {sys.version}')
print(f'Working directory: {os.getcwd()}')
"
```

**Solutions**:

1. **Missing test dependencies**:
   ```bash
   # Install test dependencies
   pip install pytest pytest-mock pytest-cov
   ```

2. **Environment issues**:
   ```bash
   # Set up test environment
   export PYTHONPATH=$PWD
   export TESTING=true
   ```

3. **Mock configuration issues**:
   ```bash
   # Check if mocks are working
   python -c "
   from tests.conftest import mock_config
   print('Mock config available')
   "
   ```

### Problem: Import Errors in Tests

**Symptoms**:
```
ModuleNotFoundError: No module named 'agent'
```

**Diagnosis**:
```bash
# Check Python path
python -c "import sys; print('\\n'.join(sys.path))"

# Check if agent module is importable
python -c "import agent; print('Agent module OK')"
```

**Solutions**:

1. **Fix Python path**:
   ```bash
   # Add project root to Python path
   export PYTHONPATH=$PWD:$PYTHONPATH
   ```

2. **Install in development mode**:
   ```bash
   pip install -e .
   ```

3. **Use absolute imports**:
   ```bash
   # Ensure all imports use absolute paths
   # from agent.config import get_config
   # Not: from .config import get_config
   ```

## 🔍 Debugging Techniques

### Enable Debug Logging

```bash
# Set debug log level
export LOG_LEVEL=DEBUG
python main.py --dry-run

# Or modify .env file
echo "LOG_LEVEL=DEBUG" >> .env
```

### Performance Profiling

```bash
# Profile memory usage
python -m memory_profiler main.py --dry-run

# Profile execution time
python -m cProfile -o profile.stats main.py --dry-run
python -c "
import pstats
p = pstats.Stats('profile.stats')
p.sort_stats('cumulative').print_stats(10)
"
```

### API Debugging

```bash
# Enable HTTP request logging
export REQUESTS_CA_BUNDLE=""
python -c "
import logging
logging.basicConfig(level=logging.DEBUG)
import requests
requests.get('https://api.datadoghq.eu/api/v1/validate')
"
```

### State Inspection

```bash
# Inspect agent state
python -c "
from agent.graph import build_graph
graph = build_graph()
print('Graph nodes:', list(graph.nodes.keys()))
print('Graph edges:', list(graph.edges.keys()))
"
```

## 🆘 Emergency Recovery

### Reset Configuration

```bash
# Backup current config
cp .env .env.backup

# Reset to defaults
cp .env.example .env
# Edit with your API keys
```

### Clear All Caches

```bash
# Clear performance caches
python -c "
from agent.performance import clear_performance_caches
clear_performance_caches()
print('Performance caches cleared')
"

# Clear processed fingerprints
rm -f processed_fingerprints.json
```

### Reset Test Environment

```bash
# Clean test environment
rm -rf .pytest_cache/
rm -rf __pycache__/
rm -rf tests/__pycache__/
find . -name "*.pyc" -delete

# Reinstall dependencies
pip install -r requirements.txt
```

## 📞 Getting Help

### Before Asking for Help

1. **Check this troubleshooting guide**
2. **Run the quick diagnosis commands**
3. **Enable debug logging and capture output**
4. **Check the GitHub issues for similar problems**

### Information to Include

When reporting issues, include:

1. **Environment details**:
   ```bash
   python --version
   pip list | grep -E "(openai|requests|pydantic|langchain)"
   ```

2. **Configuration (sanitized)**:
   ```bash
   grep -E "^[A-Z]" .env | cut -d'=' -f1
   ```

3. **Error output**:
   ```bash
   python main.py --dry-run 2>&1 | tee error.log
   ```

4. **Debug logs**:
   ```bash
   LOG_LEVEL=DEBUG python main.py --dry-run 2>&1 | tee debug.log
   ```

### Support Channels

- **GitHub Issues**: [Create an issue](https://github.com/your-org/dogcatcher-agent/issues)
- **GitHub Discussions**: [Ask questions](https://github.com/your-org/dogcatcher-agent/discussions)
- **Documentation**: Check the `docs/` directory
- **Code Examples**: Review existing code and tests

---

**Remember**: Most issues are configuration-related. Double-check your `.env` file and API credentials before diving into complex debugging!
