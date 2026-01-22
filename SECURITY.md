# Security Policy

## Supported Versions

| Version | Supported          |
| ------- | ------------------ |
| 1.x.x   | :white_check_mark: |

## Reporting a Vulnerability

If you discover a security vulnerability in Dogcatcher Agent, please report it responsibly.

### How to Report

1. **Do NOT open a public GitHub issue** for security vulnerabilities
2. Send an email to **jmlaranjeiradeveloper@gmail.com** with:
   - A description of the vulnerability
   - Steps to reproduce the issue
   - Potential impact assessment
   - Any suggested fixes (optional)

### What to Expect

- **Acknowledgment**: Within 48 hours of your report
- **Initial Assessment**: Within 7 days
- **Resolution Timeline**: Depends on severity, typically 30-90 days

### Severity Classification

| Severity | Description | Example |
|----------|-------------|---------|
| Critical | Data breach, credential exposure | API keys leaked in logs |
| High | Authentication bypass, injection | SQL/command injection |
| Medium | Information disclosure | Sensitive data in error messages |
| Low | Minor issues | Verbose error messages |

## Security Best Practices

When using Dogcatcher Agent:

### API Keys & Credentials

- **Never commit** `.env` files or API keys to version control
- Use environment variables or secure vaults for credentials
- Rotate API keys periodically
- Use read-only API tokens where possible

### Configuration

- Always test with `--dry-run` before running in production
- Set appropriate `MAX_TICKETS_PER_RUN` limits
- Review audit logs regularly (`.agent_cache/audit_logs.jsonl`)

### Deployment

- Run in isolated environments (Docker recommended)
- Restrict network access to required services only
- Use least-privilege principles for service accounts

## Known Security Considerations

### Data Handling

- Log messages may contain sensitive data (emails, IDs)
- The agent normalizes/redacts common PII patterns
- Review Jira tickets before making them public

### External Service Access

The agent connects to:
- Datadog API (read-only log access)
- Jira API (read/write ticket access)
- OpenAI API (log analysis)
- GitHub API (Patchy PR creation)

Ensure your API tokens have minimal required permissions.

## Acknowledgments

We appreciate responsible disclosure and will acknowledge security researchers who help improve this project (with permission).
