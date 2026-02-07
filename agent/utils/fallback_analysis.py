"""Fallback analysis for when LLM services are unavailable.

This module provides rule-based analysis as a fallback mechanism
when the primary LLM analysis fails due to service issues.
"""

import re
from typing import Dict, Any, List, Tuple, Optional
from datetime import datetime

from agent.utils.logger import log_info, log_warning, log_debug


class FallbackAnalyzer:
    """Rule-based analyzer for log entries when LLM is unavailable."""

    def __init__(self):
        self.error_patterns = self._load_error_patterns()
        self.severity_rules = self._load_severity_rules()
        self.fallback_stats = {
            "total_analyses": 0,
            "pattern_matches": 0,
            "unknown_errors": 0,
            "high_severity_count": 0,
            "medium_severity_count": 0,
            "low_severity_count": 0,
        }

    def _load_error_patterns(self) -> Dict[str, Dict[str, Any]]:
        """Load error pattern definitions."""
        return {
            # Database errors
            "database-connection": {
                "patterns": [
                    r"database.*connection.*fail",
                    r"connection.*timeout.*database",
                    r"could not connect.*database",
                    r"database.*unavailable",
                    r"connection.*refused.*database",
                    r"sql.*connection.*error",
                ],
                "severity": "high",
                "title_template": "Database Connection Error",
                "keywords": ["database", "connection", "sql", "db"],
            },
            "database-constraint": {
                "patterns": [
                    r"constraint.*violation",
                    r"unique.*constraint",
                    r"foreign.*key.*constraint",
                    r"check.*constraint.*failed",
                    r"duplicate.*key.*value",
                    r"violates.*constraint",
                ],
                "severity": "medium",
                "title_template": "Database Constraint Violation",
                "keywords": ["constraint", "violation", "duplicate", "key"],
            },
            # Network errors
            "timeout": {
                "patterns": [
                    r"timeout.*occurred",
                    r"request.*timeout",
                    r"connection.*timeout",
                    r"read.*timeout",
                    r"socket.*timeout",
                    r"operation.*timed.*out",
                ],
                "severity": "medium",
                "title_template": "Operation Timeout",
                "keywords": ["timeout", "timed out"],
            },
            "network-error": {
                "patterns": [
                    r"network.*error",
                    r"connection.*reset",
                    r"no.*route.*to.*host",
                    r"network.*unreachable",
                    r"connection.*refused",
                    r"host.*not.*found",
                ],
                "severity": "high",
                "title_template": "Network Error",
                "keywords": ["network", "connection", "host", "unreachable"],
            },
            # HTTP errors
            "http-client-error": {
                "patterns": [
                    r"4\d{2}.*error",
                    r"bad.*request",
                    r"unauthorized",
                    r"forbidden",
                    r"not.*found.*404",
                    r"method.*not.*allowed",
                ],
                "severity": "low",
                "title_template": "HTTP Client Error",
                "keywords": ["400", "401", "403", "404", "405"],
            },
            "http-server-error": {
                "patterns": [
                    r"5\d{2}.*error",
                    r"internal.*server.*error",
                    r"service.*unavailable",
                    r"gateway.*timeout",
                    r"bad.*gateway",
                ],
                "severity": "high",
                "title_template": "HTTP Server Error",
                "keywords": ["500", "502", "503", "504"],
            },
            # Authentication errors
            "authentication-error": {
                "patterns": [
                    r"authentication.*failed",
                    r"invalid.*credentials",
                    r"access.*denied",
                    r"unauthorized.*access",
                    r"token.*expired",
                    r"permission.*denied",
                ],
                "severity": "medium",
                "title_template": "Authentication Error",
                "keywords": ["auth", "credentials", "token", "permission"],
            },
            # File system errors
            "file-not-found": {
                "patterns": [
                    r"file.*not.*found",
                    r"no.*such.*file",
                    r"path.*does.*not.*exist",
                    r"cannot.*find.*file",
                    r"missing.*file",
                ],
                "severity": "low",
                "title_template": "File Not Found",
                "keywords": ["file", "path", "missing", "not found"],
            },
            "disk-space": {
                "patterns": [
                    r"disk.*full",
                    r"no.*space.*left",
                    r"disk.*space.*exceeded",
                    r"storage.*quota.*exceeded",
                    r"insufficient.*disk.*space",
                ],
                "severity": "high",
                "title_template": "Disk Space Error",
                "keywords": ["disk", "space", "full", "quota"],
            },
            # Memory errors
            "out-of-memory": {
                "patterns": [
                    r"out.*of.*memory",
                    r"memory.*allocation.*failed",
                    r"insufficient.*memory",
                    r"heap.*space.*exceeded",
                    r"memory.*limit.*exceeded",
                ],
                "severity": "high",
                "title_template": "Memory Error",
                "keywords": ["memory", "heap", "allocation", "oom"],
            },
            # Configuration errors
            "configuration-error": {
                "patterns": [
                    r"configuration.*error",
                    r"missing.*configuration",
                    r"invalid.*configuration",
                    r"config.*not.*found",
                    r"property.*not.*found",
                ],
                "severity": "medium",
                "title_template": "Configuration Error",
                "keywords": ["config", "configuration", "property", "setting"],
            },
            # Kafka/messaging errors
            "kafka-consumer": {
                "patterns": [
                    r"kafka.*consumer.*error",
                    r"failed.*to.*consume.*message",
                    r"consumer.*group.*error",
                    r"offset.*commit.*failed",
                    r"partition.*assignment.*failed",
                ],
                "severity": "medium",
                "title_template": "Kafka Consumer Error",
                "keywords": ["kafka", "consumer", "message", "partition"],
            },
            "message-queue": {
                "patterns": [
                    r"message.*queue.*error",
                    r"queue.*not.*found",
                    r"failed.*to.*publish.*message",
                    r"message.*processing.*failed",
                    r"dead.*letter.*queue",
                ],
                "severity": "medium",
                "title_template": "Message Queue Error",
                "keywords": ["queue", "message", "publish", "processing"],
            },
            # Generic errors (catch-all)
            "unknown": {
                "patterns": [r"error", r"exception", r"failed", r"failure"],
                "severity": "medium",
                "title_template": "System Error",
                "keywords": ["error", "exception", "failed"],
            },
        }

    def _load_severity_rules(self) -> Dict[str, str]:
        """Load severity escalation rules."""
        return {
            # High severity keywords
            "critical": "high",
            "fatal": "high",
            "severe": "high",
            "urgent": "high",
            "emergency": "high",
            # Medium severity keywords
            "warning": "medium",
            "warn": "medium",
            "deprecated": "medium",
            # Low severity keywords
            "info": "low",
            "debug": "low",
            "trace": "low",
        }

    def analyze_log(self, log_data: Dict[str, Any]) -> Dict[str, Any]:
        """Analyze log entry using rule-based patterns."""
        self.fallback_stats["total_analyses"] += 1

        message = log_data.get("message", "").lower()
        logger = log_data.get("logger", "unknown.logger")
        thread = log_data.get("thread", "unknown.thread")
        detail = log_data.get("detail", "")

        # Combine all text for analysis
        full_text = f"{message} {detail}".lower()

        # Find matching error type
        error_type, confidence = self._match_error_type(full_text)

        # Determine severity
        severity = self._determine_severity(error_type, full_text, log_data)

        # Generate title and description
        title = self._generate_title(error_type, message, logger)
        description = self._generate_description(error_type, log_data, confidence)

        # Determine if ticket should be created
        create_ticket = self._should_create_ticket(error_type, severity, confidence)

        # Update statistics
        self._update_stats(error_type, severity, confidence)

        result = {
            "error_type": error_type,
            "create_ticket": create_ticket,
            "ticket_title": title,
            "ticket_description": description,
            "severity": severity,
            "fallback_analysis": True,
            "confidence": confidence,
            "analysis_method": "rule_based",
        }

        log_info(
            "Fallback analysis completed",
            error_type=error_type,
            severity=severity,
            confidence=confidence,
            create_ticket=create_ticket,
        )

        return result

    def _match_error_type(self, text: str) -> Tuple[str, float]:
        """Match text against error patterns."""
        best_match = "unknown"
        best_confidence = 0.0

        for error_type, config in self.error_patterns.items():
            if error_type == "unknown":  # Skip generic patterns for now
                continue

            confidence = self._calculate_pattern_confidence(text, config)

            if confidence > best_confidence:
                best_match = error_type
                best_confidence = confidence

        # If no specific pattern matched, use generic patterns
        if best_confidence < 0.3:
            confidence = self._calculate_pattern_confidence(
                text, self.error_patterns["unknown"]
            )
            if confidence > 0:
                best_match = "unknown"
                best_confidence = confidence

        if best_match != "unknown":
            self.fallback_stats["pattern_matches"] += 1
        else:
            self.fallback_stats["unknown_errors"] += 1

        return best_match, best_confidence

    def _calculate_pattern_confidence(self, text: str, config: Dict[str, Any]) -> float:
        """Calculate confidence score for pattern match."""
        patterns = config.get("patterns", [])
        keywords = config.get("keywords", [])

        pattern_score = 0.0
        keyword_score = 0.0

        # Check regex patterns
        for pattern in patterns:
            if re.search(pattern, text, re.IGNORECASE):
                pattern_score = 0.8  # High confidence for regex match
                break

        # Check keywords
        keyword_matches = sum(1 for keyword in keywords if keyword.lower() in text)
        if keywords:
            keyword_score = (keyword_matches / len(keywords)) * 0.6

        # Combine scores
        confidence = max(pattern_score, keyword_score)

        # Boost confidence if multiple keywords match
        if keyword_matches > 1:
            confidence = min(1.0, confidence + 0.1)

        return confidence

    def _determine_severity(
        self, error_type: str, text: str, log_data: Dict[str, Any]
    ) -> str:
        """Determine severity based on error type and content."""
        # Start with default severity from pattern
        pattern_config = self.error_patterns.get(error_type, {})
        severity = pattern_config.get("severity", "medium")

        # Check for severity keywords in text
        for keyword, sev in self.severity_rules.items():
            if keyword in text:
                if sev == "high":
                    severity = "high"
                    break
                elif sev == "medium" and severity == "low":
                    severity = "medium"

        # Context-based severity adjustments
        logger = log_data.get("logger", "")

        # Critical system components
        if any(
            critical in logger.lower()
            for critical in ["payment", "security", "auth", "billing"]
        ):
            if severity == "low":
                severity = "medium"
            elif severity == "medium":
                severity = "high"

        # Production environment escalation
        if "prod" in logger.lower() or "production" in text:
            if severity == "low":
                severity = "medium"

        return severity

    def _generate_title(self, error_type: str, message: str, logger: str) -> str:
        """Generate ticket title."""
        pattern_config = self.error_patterns.get(error_type, {})
        base_title = pattern_config.get("title_template", "System Error")

        # Extract key information from message
        if error_type == "database-connection":
            if "timeout" in message:
                title = "Database Connection Timeout"
            else:
                title = "Database Connection Failed"
        elif error_type == "http-client-error":
            # Try to extract HTTP status code
            status_match = re.search(r"\b(4\d{2})\b", message)
            if status_match:
                title = f"HTTP {status_match.group(1)} Error"
            else:
                title = base_title
        elif error_type == "http-server-error":
            # Try to extract HTTP status code
            status_match = re.search(r"\b(5\d{2})\b", message)
            if status_match:
                title = f"HTTP {status_match.group(1)} Error"
            else:
                title = base_title
        else:
            title = base_title

        # Add service context if available
        if logger and "unknown" not in logger:
            service_name = logger.split(".")[-1] if "." in logger else logger
            title = f"{title} in {service_name}"

        # Truncate to reasonable length
        return title[:120]

    def _generate_description(
        self, error_type: str, log_data: Dict[str, Any], confidence: float
    ) -> str:
        """Generate ticket description."""
        message = log_data.get("message", "")
        logger = log_data.get("logger", "unknown")
        thread = log_data.get("thread", "unknown")
        detail = log_data.get("detail", "")
        timestamp = log_data.get("timestamp", "")

        # Sanitize message (basic sanitization)
        sanitized_message = self._sanitize_message(message)

        description = f"""**Problem Summary**
{sanitized_message}

**Analysis Details**
- **Error Type**: {error_type}
- **Analysis Method**: Rule-based fallback (LLM unavailable)
- **Confidence**: {confidence:.2f}
- **Logger**: {logger}
- **Thread**: {thread}
- **Timestamp**: {timestamp}

**Possible Causes**
{self._get_possible_causes(error_type)}

**Suggested Actions**
{self._get_suggested_actions(error_type)}

**Additional Context**
{detail if detail else "No additional details available"}

---
*This ticket was created using fallback analysis due to LLM service unavailability.*
"""

        return description

    def _sanitize_message(self, message: str) -> str:
        """Basic message sanitization."""
        # Remove potential sensitive information
        sanitized = re.sub(
            r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b", "<EMAIL>", message
        )
        sanitized = re.sub(
            r"\b(?:[0-9]{1,3}\.){3}[0-9]{1,3}\b", "<IP_ADDRESS>", sanitized
        )
        sanitized = re.sub(r"\b[A-Za-z0-9]{20,}\b", "<TOKEN>", sanitized)

        return sanitized

    def _get_possible_causes(self, error_type: str) -> str:
        """Get possible causes for error type."""
        causes_map = {
            "database-connection": """
- Database server is down or unreachable
- Network connectivity issues
- Connection pool exhausted
- Database authentication problems
- Firewall blocking database connections""",
            "timeout": """
- External service is slow or unresponsive
- Network latency issues
- Resource contention
- Configuration timeout values too low
- System overload""",
            "authentication-error": """
- Invalid or expired credentials
- Permission configuration issues
- Token expiration
- Authentication service unavailable
- User account locked or disabled""",
            "file-not-found": """
- File was moved or deleted
- Incorrect file path configuration
- Permission issues accessing file
- File system corruption
- Network drive disconnected""",
            "out-of-memory": """
- Memory leak in application
- Insufficient system memory
- Memory configuration too low
- Large data processing without proper handling
- Memory fragmentation""",
        }

        return causes_map.get(
            error_type,
            """
- System configuration issues
- External dependency problems
- Resource constraints
- Network connectivity issues
- Application logic errors""",
        )

    def _get_suggested_actions(self, error_type: str) -> str:
        """Get suggested actions for error type."""
        actions_map = {
            "database-connection": """
- Check database server status and logs
- Verify network connectivity to database
- Review connection pool configuration
- Check database authentication credentials
- Monitor database performance metrics""",
            "timeout": """
- Check external service status
- Review timeout configuration values
- Monitor system resource usage
- Investigate network latency
- Consider implementing retry mechanisms""",
            "authentication-error": """
- Verify user credentials and permissions
- Check token expiration and renewal
- Review authentication service logs
- Validate configuration settings
- Test authentication flow manually""",
            "file-not-found": """
- Verify file existence and location
- Check file permissions and ownership
- Review file path configuration
- Monitor file system health
- Implement file existence checks""",
            "out-of-memory": """
- Review application memory usage patterns
- Increase available system memory
- Optimize memory-intensive operations
- Implement proper resource cleanup
- Monitor memory usage trends""",
        }

        return actions_map.get(
            error_type,
            """
- Review system logs for additional context
- Check configuration settings
- Monitor system resource usage
- Verify external dependencies
- Implement proper error handling""",
        )

    def _should_create_ticket(
        self, error_type: str, severity: str, confidence: float
    ) -> bool:
        """Determine if a ticket should be created."""
        # Don't create tickets for very low confidence matches
        if confidence < 0.2:
            return False

        # Always create tickets for high severity
        if severity == "high":
            return True

        # Create tickets for medium severity with reasonable confidence
        if severity == "medium" and confidence >= 0.4:
            return True

        # Create tickets for low severity only with high confidence
        if severity == "low" and confidence >= 0.7:
            return True

        return False

    def _update_stats(self, error_type: str, severity: str, confidence: float) -> None:
        """Update internal statistics."""
        severity_key = f"{severity}_severity_count"
        if severity_key in self.fallback_stats:
            self.fallback_stats[severity_key] += 1

    def get_stats(self) -> Dict[str, Any]:
        """Get fallback analyzer statistics."""
        total = self.fallback_stats["total_analyses"]

        stats = {
            **self.fallback_stats,
            "pattern_match_rate": (
                (self.fallback_stats["pattern_matches"] / total * 100)
                if total > 0
                else 0
            ),
            "unknown_error_rate": (
                (self.fallback_stats["unknown_errors"] / total * 100)
                if total > 0
                else 0
            ),
        }

        return {
            "analyzer": "fallback_rule_based",
            "statistics": stats,
            "supported_error_types": list(self.error_patterns.keys()),
            "total_patterns": sum(
                len(config.get("patterns", []))
                for config in self.error_patterns.values()
            ),
        }

    def reset_stats(self) -> None:
        """Reset statistics."""
        for key in self.fallback_stats:
            self.fallback_stats[key] = 0


# Global fallback analyzer instance
_fallback_analyzer = FallbackAnalyzer()


def get_fallback_analyzer() -> FallbackAnalyzer:
    """Get the global fallback analyzer instance."""
    return _fallback_analyzer


def analyze_with_fallback(log_data: Dict[str, Any]) -> Dict[str, Any]:
    """Convenience function for fallback analysis."""
    return _fallback_analyzer.analyze_log(log_data)
