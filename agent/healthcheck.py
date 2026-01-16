"""Health check module for verifying external service connections.

Provides functions to test connectivity to OpenAI, Datadog, and Jira APIs
before running the main agent workflow.
"""
from __future__ import annotations
import sys
from typing import Dict, Any, List, Tuple
from dataclasses import dataclass

from agent.config import get_config
from agent.utils.logger import log_info, log_error


@dataclass
class HealthCheckResult:
    """Result of a single health check."""
    service: str
    healthy: bool
    message: str
    details: Dict[str, Any] = None

    def __post_init__(self):
        if self.details is None:
            self.details = {}


def check_openai() -> HealthCheckResult:
    """Check OpenAI API connectivity.

    Makes a minimal API call to verify the API key works.

    Returns:
        HealthCheckResult with connection status
    """
    import os

    try:
        from openai import OpenAI

        api_key = os.getenv("OPENAI_API_KEY", "")
        if not api_key:
            return HealthCheckResult(
                service="OpenAI",
                healthy=False,
                message="OPENAI_API_KEY not set"
            )

        client = OpenAI(api_key=api_key)
        model = os.getenv("OPENAI_MODEL", "gpt-4.1-nano")

        # Make a minimal API call to verify connectivity
        response = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": "ping"}],
            max_tokens=1
        )

        return HealthCheckResult(
            service="OpenAI",
            healthy=True,
            message=f"Connected (model: {model})",
            details={
                "model": model,
                "response_id": response.id if response else None
            }
        )

    except Exception as e:
        error_msg = str(e)
        # Truncate long error messages
        if len(error_msg) > 100:
            error_msg = error_msg[:100] + "..."

        return HealthCheckResult(
            service="OpenAI",
            healthy=False,
            message=f"Connection failed: {error_msg}"
        )


def check_datadog() -> HealthCheckResult:
    """Check Datadog API connectivity.

    Makes a minimal API call to verify the API keys work.

    Returns:
        HealthCheckResult with connection status
    """
    try:
        import requests

        config = get_config()

        if not config.datadog_api_key or not config.datadog_app_key:
            missing = []
            if not config.datadog_api_key:
                missing.append("DATADOG_API_KEY")
            if not config.datadog_app_key:
                missing.append("DATADOG_APP_KEY")
            return HealthCheckResult(
                service="Datadog",
                healthy=False,
                message=f"Missing: {', '.join(missing)}"
            )

        # Use the validate endpoint to check API keys
        url = f"https://api.{config.datadog_site}/api/v1/validate"
        headers = {
            "DD-API-KEY": config.datadog_api_key,
            "DD-APPLICATION-KEY": config.datadog_app_key,
        }

        response = requests.get(url, headers=headers, timeout=10)

        if response.status_code == 200:
            return HealthCheckResult(
                service="Datadog",
                healthy=True,
                message=f"Connected (site: {config.datadog_site})",
                details={
                    "site": config.datadog_site,
                    "service": config.datadog_service,
                    "env": config.datadog_env
                }
            )
        else:
            return HealthCheckResult(
                service="Datadog",
                healthy=False,
                message=f"API returned {response.status_code}: {response.text[:50]}"
            )

    except Exception as e:
        error_msg = str(e)
        if len(error_msg) > 100:
            error_msg = error_msg[:100] + "..."

        return HealthCheckResult(
            service="Datadog",
            healthy=False,
            message=f"Connection failed: {error_msg}"
        )


def check_jira() -> HealthCheckResult:
    """Check Jira API connectivity.

    Makes a minimal API call to verify the credentials work.

    Returns:
        HealthCheckResult with connection status
    """
    try:
        import requests
        import base64

        config = get_config()

        missing = []
        if not config.jira_domain:
            missing.append("JIRA_DOMAIN")
        if not config.jira_user:
            missing.append("JIRA_USER")
        if not config.jira_api_token:
            missing.append("JIRA_API_TOKEN")

        if missing:
            return HealthCheckResult(
                service="Jira",
                healthy=False,
                message=f"Missing: {', '.join(missing)}"
            )

        # Build auth header
        auth_string = f"{config.jira_user}:{config.jira_api_token}"
        auth_encoded = base64.b64encode(auth_string.encode()).decode()
        headers = {
            "Authorization": f"Basic {auth_encoded}",
            "Content-Type": "application/json"
        }

        # Check server info endpoint (lightweight)
        url = f"https://{config.jira_domain}/rest/api/3/myself"
        response = requests.get(url, headers=headers, timeout=10)

        if response.status_code == 200:
            user_data = response.json()
            display_name = user_data.get("displayName", config.jira_user)

            return HealthCheckResult(
                service="Jira",
                healthy=True,
                message=f"Connected ({config.jira_domain})",
                details={
                    "domain": config.jira_domain,
                    "user": display_name,
                    "project": config.jira_project_key
                }
            )
        elif response.status_code == 401:
            return HealthCheckResult(
                service="Jira",
                healthy=False,
                message="Authentication failed (check JIRA_USER and JIRA_API_TOKEN)"
            )
        elif response.status_code == 403:
            return HealthCheckResult(
                service="Jira",
                healthy=False,
                message="Access forbidden (check permissions)"
            )
        else:
            return HealthCheckResult(
                service="Jira",
                healthy=False,
                message=f"API returned {response.status_code}"
            )

    except Exception as e:
        error_msg = str(e)
        if len(error_msg) > 100:
            error_msg = error_msg[:100] + "..."

        return HealthCheckResult(
            service="Jira",
            healthy=False,
            message=f"Connection failed: {error_msg}"
        )


def run_health_checks(verbose: bool = True) -> Tuple[bool, List[HealthCheckResult]]:
    """Run all health checks.

    Args:
        verbose: If True, print results to stdout

    Returns:
        Tuple of (all_healthy, list of results)
    """
    results = []

    if verbose:
        print("\n🔍 Running health checks...\n")

    # Check OpenAI
    if verbose:
        print("  Checking OpenAI API...", end=" ", flush=True)
    openai_result = check_openai()
    results.append(openai_result)
    if verbose:
        icon = "✓" if openai_result.healthy else "✗"
        print(f"{icon} {openai_result.message}")

    # Check Datadog
    if verbose:
        print("  Checking Datadog API...", end=" ", flush=True)
    datadog_result = check_datadog()
    results.append(datadog_result)
    if verbose:
        icon = "✓" if datadog_result.healthy else "✗"
        print(f"{icon} {datadog_result.message}")

    # Check Jira
    if verbose:
        print("  Checking Jira API...", end=" ", flush=True)
    jira_result = check_jira()
    results.append(jira_result)
    if verbose:
        icon = "✓" if jira_result.healthy else "✗"
        print(f"{icon} {jira_result.message}")

    all_healthy = all(r.healthy for r in results)

    if verbose:
        print()
        if all_healthy:
            print("✅ All services ready!\n")
        else:
            failed = [r.service for r in results if not r.healthy]
            print(f"❌ Health check failed for: {', '.join(failed)}\n")

    # Log results
    for result in results:
        if result.healthy:
            log_info(f"Health check passed: {result.service}", **result.details)
        else:
            log_error(f"Health check failed: {result.service}", message=result.message)

    return all_healthy, results


def require_healthy_services() -> bool:
    """Run health checks and exit if any fail.

    Returns:
        True if all services are healthy

    Raises:
        SystemExit if any service is unhealthy
    """
    all_healthy, results = run_health_checks(verbose=True)

    if not all_healthy:
        print("Please fix the connection issues and try again.")
        sys.exit(1)

    return True


if __name__ == "__main__":
    # Allow running directly: python -m agent.healthcheck
    from dotenv import load_dotenv
    load_dotenv()

    all_healthy, _ = run_health_checks()
    sys.exit(0 if all_healthy else 1)
