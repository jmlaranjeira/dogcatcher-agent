# Contributing to Dogcatcher Agent

Thank you for your interest in contributing to the Dogcatcher Agent! This guide will help you get started with contributing to the project.

## 🤝 How to Contribute

### Types of Contributions

We welcome several types of contributions:

- **Bug fixes**: Fix issues and improve reliability
- **Feature additions**: Add new functionality and capabilities
- **Documentation**: Improve guides, API docs, and examples
- **Performance improvements**: Optimize speed and resource usage
- **Testing**: Add tests and improve test coverage
- **Code quality**: Refactor, clean up, and improve code structure

### Getting Started

1. **Fork the repository** on GitHub
2. **Clone your fork** locally
3. **Set up development environment** (see [README-DEV.md](README-DEV.md))
4. **Create a feature branch** for your changes
5. **Make your changes** with tests
6. **Submit a pull request**

## 🛠️ Development Setup

### Prerequisites

- Python 3.11+
- Git
- GitHub account
- Basic understanding of Python, APIs, and testing

### Setup Steps

```bash
# 1. Fork and clone
git clone https://github.com/YOUR-USERNAME/dogcatcher-agent.git
cd dogcatcher-agent

# 2. Add upstream remote
git remote add upstream https://github.com/organization/dogcatcher-agent.git

# 3. Create virtual environment
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# 4. Install dependencies
pip install -r requirements.txt

# 5. Install development dependencies
pip install pytest pytest-mock pytest-cov black mypy flake8

# 6. Set up pre-commit hooks (optional)
pip install pre-commit
pre-commit install
```

### Development Workflow

```bash
# 1. Create feature branch
git checkout -b feature/your-feature-name

# 2. Make your changes
# ... edit files ...

# 3. Run tests
python run_tests.py

# 4. Check code quality
black .
mypy .
flake8 .

# 5. Commit changes
git add .
git commit -m "feat: add your feature description"

# 6. Push to your fork
git push origin feature/your-feature-name

# 7. Create pull request on GitHub
```

## 📝 Code Standards

### Python Style Guide

We follow **PEP 8** with some modifications:

```python
# Good: Clear, descriptive names
def find_similar_ticket(summary: str, state: Optional[dict] = None) -> Tuple[Optional[str], float, Optional[str]]:
    """Find similar tickets using multi-strategy duplicate detection.
    
    Args:
        summary: Ticket summary to search for
        state: Optional agent state for context
        
    Returns:
        Tuple of (issue_key, similarity_score, issue_summary)
    """
    # Implementation here
    pass

# Bad: Unclear names and missing docstrings
def find_dup(s, st=None):
    pass
```

### Type Hints

Always use type hints for function signatures:

```python
from typing import Dict, List, Optional, Tuple, Any

def process_logs(logs: List[Dict[str, Any]], config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Process logs with optional configuration."""
    pass
```

### Error Handling

Use structured error handling:

```python
def safe_api_call(func, *args, **kwargs):
    """Wrapper for API calls with error handling."""
    try:
        return func(*args, **kwargs)
    except requests.RequestException as e:
        log_error("API call failed", error=str(e), function=func.__name__)
        return None
    except Exception as e:
        log_error("Unexpected error", error=str(e), function=func.__name__)
        return None
```

### Logging

Use structured logging with the project's logger:

```python
from agent.utils.logger import log_info, log_error, log_debug

def your_function():
    log_info("Starting operation", operation="your_function")
    
    try:
        # Your code here
        result = perform_operation()
        log_info("Operation completed", result_count=len(result))
        return result
    except Exception as e:
        log_error("Operation failed", error=str(e))
        raise
```

## 🧪 Testing Guidelines

### Test Structure

Follow the existing test structure:

```
tests/
├── conftest.py              # Shared fixtures and configuration
├── unit/                    # Unit tests
│   ├── test_ticket_creation.py
│   ├── test_normalization.py
│   ├── test_config.py
│   └── test_performance.py
└── integration/             # Integration tests (future)
```

### Writing Tests

```python
import pytest
from unittest.mock import Mock, patch
from agent.nodes.ticket import create_ticket

class TestYourFeature:
    """Test your new feature."""
    
    def test_your_function_success(self, sample_state, mock_config):
        """Test successful execution of your function."""
        # Arrange
        expected_result = {"status": "success"}
        
        # Act
        result = your_function(sample_state)
        
        # Assert
        assert result["status"] == "success"
        assert "data" in result
    
    def test_your_function_error_handling(self, sample_state):
        """Test error handling in your function."""
        # Arrange
        sample_state["invalid_data"] = None
        
        # Act & Assert
        with pytest.raises(ValueError, match="Invalid data"):
            your_function(sample_state)
    
    @patch('agent.external.api_call')
    def test_your_function_with_mock(self, mock_api, sample_state):
        """Test your function with mocked external dependency."""
        # Arrange
        mock_api.return_value = {"data": "test"}
        
        # Act
        result = your_function(sample_state)
        
        # Assert
        assert result["data"] == "test"
        mock_api.assert_called_once()
```

### Test Categories

Use pytest markers to categorize tests:

```python
@pytest.mark.unit
def test_unit_functionality():
    """Unit test for core functionality."""
    pass

@pytest.mark.integration
def test_integration_workflow():
    """Integration test for end-to-end workflow."""
    pass

@pytest.mark.slow
def test_performance_benchmark():
    """Performance test that takes time."""
    pass
```

### Running Tests

```bash
# Run all tests
python run_tests.py

# Run specific categories
pytest -m unit
pytest -m integration
pytest -m "not slow"

# Run with coverage
pytest --cov=agent tests/

# Run specific test file
pytest tests/unit/test_your_feature.py -v
```

## 📋 Pull Request Process

### Before Submitting

1. **Run the full test suite**:
   ```bash
   python run_tests.py
   ```

2. **Check code quality**:
   ```bash
   black .
   mypy .
   flake8 .
   ```

3. **Update documentation** if needed

4. **Add tests** for new functionality

5. **Update CHANGELOG.md** with your changes

### Pull Request Template

```markdown
## Description
Brief description of your changes.

## Type of Change
- [ ] Bug fix (non-breaking change which fixes an issue)
- [ ] New feature (non-breaking change which adds functionality)
- [ ] Breaking change (fix or feature that would cause existing functionality to not work as expected)
- [ ] Documentation update
- [ ] Performance improvement
- [ ] Code refactoring

## Testing
- [ ] I have added tests that prove my fix is effective or that my feature works
- [ ] New and existing unit tests pass locally with my changes
- [ ] I have tested the changes manually

## Checklist
- [ ] My code follows the project's style guidelines
- [ ] I have performed a self-review of my own code
- [ ] I have commented my code, particularly in hard-to-understand areas
- [ ] I have made corresponding changes to the documentation
- [ ] My changes generate no new warnings
- [ ] I have added tests that prove my fix is effective or that my feature works
- [ ] New and existing unit tests pass locally with my changes
```

### Review Process

1. **Automated checks** must pass (tests, linting, type checking)
2. **Code review** by maintainers
3. **Testing** by maintainers
4. **Approval** and merge

## 🏗️ Architecture Guidelines

### Adding New Features

1. **Follow existing patterns**:
   - Use the same structure as existing modules
   - Follow the same error handling patterns
   - Use the same logging approach

2. **Configuration**:
   - Add new settings to `agent/config.py`
   - Use Pydantic validation
   - Provide sensible defaults

3. **Performance**:
   - Consider caching for expensive operations
   - Add performance monitoring
   - Optimize for common use cases

4. **Testing**:
   - Add unit tests for new functionality
   - Add integration tests for workflows
   - Test error conditions and edge cases

### Modifying Existing Features

1. **Backward compatibility**:
   - Maintain existing API contracts
   - Add deprecation warnings for breaking changes
   - Update documentation

2. **Configuration changes**:
   - Provide migration path for existing configurations
   - Maintain backward compatibility where possible
   - Update configuration validation

3. **Performance impact**:
   - Measure performance impact of changes
   - Add performance tests if needed
   - Consider caching and optimization

## 📚 Documentation

### Code Documentation

```python
def your_function(param1: str, param2: Optional[int] = None) -> Dict[str, Any]:
    """Brief description of what the function does.
    
    Longer description if needed, explaining the purpose,
    behavior, and any important details.
    
    Args:
        param1: Description of param1 and its expected format
        param2: Description of param2, defaults to None
        
    Returns:
        Dictionary containing the results with keys:
        - status: Success/failure status
        - data: The processed data
        - metadata: Additional information
        
    Raises:
        ValueError: If param1 is invalid
        ConnectionError: If external service is unavailable
        
    Example:
        >>> result = your_function("test", 42)
        >>> print(result["status"])
        "success"
    """
    pass
```

### README Updates

When adding new features:

1. **Update README-DEV.md** with new setup instructions
2. **Update architecture.md** if architecture changes
3. **Update troubleshooting.md** for common issues
4. **Add examples** and usage patterns

## 🐛 Bug Reports

### Before Reporting

1. **Check existing issues** for similar problems
2. **Try the latest version** to see if the issue is fixed
3. **Check the troubleshooting guide**
4. **Enable debug logging** and capture output

### Bug Report Template

```markdown
## Bug Description
Clear and concise description of the bug.

## Steps to Reproduce
1. Go to '...'
2. Click on '....'
3. Scroll down to '....'
4. See error

## Expected Behavior
What you expected to happen.

## Actual Behavior
What actually happened.

## Environment
- OS: [e.g., Ubuntu 20.04]
- Python version: [e.g., 3.11.0]
- Package versions: [e.g., openai==1.3.0]

## Configuration
```bash
# Relevant configuration (sanitized)
DATADOG_SITE=datadoghq.eu
JIRA_SIMILARITY_THRESHOLD=0.82
```

## Logs
```
# Relevant log output
```

## Additional Context
Any other context about the problem.
```

## 💡 Feature Requests

### Feature Request Template

```markdown
## Feature Description
Clear and concise description of the feature.

## Use Case
Describe the problem this feature would solve.

## Proposed Solution
Describe your proposed solution.

## Alternatives Considered
Describe any alternative solutions you've considered.

## Additional Context
Any other context or screenshots about the feature request.
```

## 🏷️ Release Process

### Version Numbering

We use [Semantic Versioning](https://semver.org/):

- **MAJOR**: Breaking changes
- **MINOR**: New features (backward compatible)
- **PATCH**: Bug fixes (backward compatible)

### Release Checklist

1. **Update version** in `__init__.py` and `setup.py`
2. **Update CHANGELOG.md** with new features and fixes
3. **Run full test suite**
4. **Update documentation**
5. **Create release tag**
6. **Publish to PyPI** (if applicable)

## 🤝 Community Guidelines

### Code of Conduct

- **Be respectful** and inclusive
- **Be constructive** in feedback
- **Be patient** with newcomers
- **Be collaborative** in discussions

### Communication

- **GitHub Issues**: Bug reports and feature requests
- **GitHub Discussions**: Questions and general discussion
- **Pull Requests**: Code changes and reviews
- **Documentation**: Improvements and clarifications

## 📞 Getting Help

### Resources

- **README-DEV.md**: Development setup and workflow
- **docs/architecture.md**: System architecture and design
- **docs/troubleshooting.md**: Common issues and solutions
- **GitHub Issues**: Bug reports and feature requests
- **GitHub Discussions**: Questions and community discussion

### Contact

- **Maintainers**: @maintainer1, @maintainer2
- **Issues**: [GitHub Issues](https://github.com/organization/dogcatcher-agent/issues)
- **Discussions**: [GitHub Discussions](https://github.com/organization/dogcatcher-agent/discussions)

---

Thank you for contributing to the Dogcatcher Agent! Your contributions help make the project better for everyone. 🚀
