"""Sleuth Agent - Error investigation through natural language queries.

Sleuth enables error investigation in Datadog through natural language queries,
correlates findings with existing Jira tickets, and optionally suggests
invoking Patchy for automatic fixes.
"""

from .sleuth_graph import build_graph, main

__all__ = ["build_graph", "main"]
