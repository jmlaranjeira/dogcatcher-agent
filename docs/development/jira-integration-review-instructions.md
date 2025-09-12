# Jira Integration Review & Testing Instructions

This document provides comprehensive instructions for reviewing and validating the Jira integration functionality in the dogcatcher-agent. It covers duplicate detection, testing, performance validation, and reporting verification.

## 🎯 Scope & Goals

### 1. **Jira Integration Polish**

#### Duplicate Detection Validation
- **End-to-end verification** of duplicate detection mechanisms:
  - Fingerprint-based duplicate detection
  - `loghash-*` label seeding and management
  - Direct log match (≥0.90 similarity threshold)
  - Fuzzy similarity matching (threshold ~0.82)
  - Comment-on-duplicate functionality

#### Contract Compliance
- **Verify titles, labels, descriptions** match the README contract
- **Preserve per-run cap** and label conventions
- **Confirm dry-run mode** behavior (no real tickets created)
- **Validate skip/duplicate reasons** are properly logged

### 2. **Tests Execution & Hardening**

#### Test Execution
- **Run existing tests** and fix any failures or flakiness
- **Validate test coverage** for critical functionality
- **Ensure hermetic testing** (mock external APIs)

#### Missing Test Coverage
Add unit tests for:
- **Normalization edge cases** and boundary conditions
- **Direct log match path** (≥0.90 similarity)
- **Label short-circuit path** for existing issues
- **Respect LLM no-create** decisions

### 3. **Performance & Caching**

#### Cache Validation
- **Validate similarity cache hit rate** and effectiveness
- **Log cache statistics** for monitoring
- **Measure API call reductions** from caching

#### Metrics & Monitoring
- **Add metrics around Jira search calls**
- **Track meaningful API call reductions**
- **Monitor performance improvements**

#### Tuned Defaults
Propose optimized default values for:
- `JIRA_SIMILARITY_THRESHOLD`
- `JIRA_SEARCH_WINDOW_DAYS`
- `JIRA_SEARCH_MAX_RESULTS`

### 4. **Reporting & Audit**

#### Report Validation
- **Exercise `tools/report.py`** functionality
- **Verify report sections** include:
  - Totals and created tickets
  - Duplicates (fingerprint/Jira)
  - Simulated runs
  - Cap-reached scenarios
  - Breakdown by severity/error_type
  - Top fingerprints and Jira issues

#### Audit Trail Extension
- **Minimally extend audit trail** if fields are missing
- **Maintain existing behavior** without breaking changes
- **Ensure comprehensive coverage** of all scenarios

## 📚 Reference Documentation

### Primary References
- **Behavior & Configuration**: [README.md](../../README.md)
- **Development Workflow**: [README-DEV.md](../../README-DEV.md)
- **Architecture Overview**: [architecture.md](architecture.md)
- **Troubleshooting Guide**: [user/troubleshooting.md](../user/troubleshooting.md)

### Configuration Files
- **Environment Variables**: `.env.example`
- **Test Configuration**: `pytest.ini`
- **Test Runner**: `run_tests.py`

## 🔧 Constraints & Guidelines

### Development Constraints
- **Do not modify** anything under `patchy/` directory
- **Keep public interfaces stable** and backward compatible
- **No secrets in logs** - maintain structured/sanitized logging
- **Use conventional commit messages** (e.g., `fix(jira): …`, `test(normalization): …`, `perf(cache): …`, `docs(report): …`)

### Quality Standards
- **Minimal and surgical changes** - prefer tests/docs over behavior changes
- **No new dependencies** unless strictly necessary
- **Justify any changes** with clear rationale
- **Maintain test coverage** and quality assurance

## 📋 Validation Procedures

### 1. Dry-Run Validation

```bash
# Test dry-run mode with sample data
python main.py --dry-run --env dev --service dehnproject --hours 24 --limit 50
```

**Expected Output:**
- Datadog query execution
- Skip reasons for duplicates
- Duplicate decisions with similarity scores
- **No real ticket creation**

### 2. Duplicate Logic Testing

#### In-Run Fingerprint Skip
- **Verify in-run fingerprint skip** works correctly
- **Test cross-run fingerprint cache** functionality
- **Validate direct log match** (≥0.90) short-circuits properly
- **Confirm fuzzy match** uses configured threshold
- **Test `loghash-*` label addition** to existing issues

### 3. Test Suite Execution

```bash
# Run all tests
python run_tests.py

# Run specific test categories
pytest -q
pytest -m unit
pytest -m config
pytest -m ticket
pytest -m normalization
```

**Test Coverage Requirements:**
- All existing tests pass
- New unit tests for normalization edge cases
- Direct-match path testing
- Label short-circuit testing
- LLM no-create decision testing

### 4. Performance Validation

#### Cache Statistics
- **Log cache stats** showing non-zero hit rate
- **Compare Jira API calls** (baseline vs cached run)
- **Document performance improvements** in review report

#### Metrics Collection
- **Track similarity cache hit rate**
- **Monitor Jira API call reduction**
- **Measure response time improvements**

### 5. Reporting Validation

```bash
# Generate comprehensive report
python tools/report.py --since-hours 24
```

**Report Sections Validation:**
- Totals and created tickets
- Duplicate detection results
- Simulated run outcomes
- Cap-reached scenarios
- Severity/error_type breakdowns
- Top fingerprints and Jira issues

## 📊 Acceptance Criteria

### Functional Requirements
- **Dry-run mode** works without creating real tickets
- **Duplicate detection** functions correctly across all scenarios
- **Test suite** passes with comprehensive coverage
- **Performance improvements** are measurable and documented
- **Reporting** provides complete audit trail

### Quality Requirements
- **No breaking changes** to existing functionality
- **Backward compatibility** maintained
- **Security standards** upheld (no secrets in logs)
- **Documentation** updated with any changes

### Performance Requirements
- **Cache hit rate** shows measurable improvement
- **API call reduction** is significant and documented
- **Response times** improved or maintained
- **Resource usage** optimized

## 🛠️ Suggested Commands

### Validation Commands
```bash
# Dry-run validation
python main.py --dry-run --env dev --service dehnproject --hours 24 --limit 50

# Test execution
python run_tests.py
pytest -q

# Performance testing
python main.py --dry-run --env dev --service dehnproject --hours 24 --limit 50
python tools/report.py --since-hours 24
```

### Development Commands
```bash
# Run specific test categories
pytest -m unit -v
pytest -m config -v
pytest -m ticket -v
pytest -m normalization -v

# Generate test coverage report
pytest --cov=agent --cov-report=html

# Lint and format code
python -m flake8 agent/
python -m black agent/
```

## 📝 Documentation Requirements

### Review Report
Create a comprehensive review report documenting:
- **Jira duplicate-detection behavior** validation results
- **Test results** (pass/fail) and added tests list
- **Performance observations** (cache hit rate, API call counts)
- **Reporting sample output** (redacted) from `tools/report.py`
- **Recommended defaults** with concrete values and rationale

### Configuration Updates
- **Update `.env.example`** comments if thresholds are adjusted
- **Document rationale** for any default value changes
- **Maintain backward compatibility** in configuration

## 🎯 Success Metrics

### Quantitative Metrics
- **Test coverage** ≥ 90% for critical paths
- **Cache hit rate** ≥ 50% for repeated searches
- **API call reduction** ≥ 30% compared to baseline
- **Response time** improvement or maintenance

### Qualitative Metrics
- **Code quality** maintained or improved
- **Documentation** comprehensive and accurate
- **User experience** enhanced without breaking changes
- **Maintainability** improved through better testing

---

**Note**: This document serves as a comprehensive guide for validating and improving the Jira integration functionality. Follow these procedures to ensure the system meets production-ready standards for reliability, performance, and maintainability.
