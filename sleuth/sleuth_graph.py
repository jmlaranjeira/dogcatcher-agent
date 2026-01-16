"""Sleuth Agent - LangGraph definition and CLI entry point.

Sleuth is an interactive CLI tool for investigating errors in Datadog
through natural language queries, correlating with Jira tickets,
and optionally suggesting Patchy for automatic fixes.
"""

from __future__ import annotations

import argparse
import sys
from typing import Any, Dict

from langgraph.graph import StateGraph, END

from .sleuth_nodes import (
    SleuthState,
    parse_query,
    build_dd_query,
    search_logs,
    correlate_jira,
    analyze_results,
    suggest_action,
    invoke_patchy,
    consolidate_duplicates,
)


def build_graph():
    """Build the Sleuth investigation graph."""
    g = StateGraph(SleuthState)

    # Set entry point
    g.set_entry_point("parse_query")

    # Add nodes
    g.add_node("parse_query", parse_query)
    g.add_node("build_dd_query", build_dd_query)
    g.add_node("search_logs", search_logs)
    g.add_node("correlate_jira", correlate_jira)
    g.add_node("analyze_results", analyze_results)
    g.add_node("suggest_action", suggest_action)

    # Linear flow
    g.add_edge("parse_query", "build_dd_query")
    g.add_edge("build_dd_query", "search_logs")
    g.add_edge("search_logs", "correlate_jira")
    g.add_edge("correlate_jira", "analyze_results")
    g.add_edge("analyze_results", "suggest_action")
    g.add_edge("suggest_action", END)

    return g.compile()


def _format_output(result: Dict[str, Any]) -> str:
    """Format the investigation results for CLI output."""
    lines = []

    # Query info
    query = result.get("query", "")
    dd_query = result.get("dd_query", "")
    lines.append(f"Investigating: \"{query}\"")
    lines.append(f"Generated query: {dd_query}")
    lines.append("")

    # Logs found
    logs = result.get("logs", [])
    lines.append(f"Logs found: {len(logs)}")

    if logs:
        lines.append("")
        lines.append("Sample logs:")
        for i, log in enumerate(logs[:5], 1):
            msg = log.get("message", "")[:100]
            status = log.get("status", "?")
            lines.append(f"  {i}. [{status}] {msg}...")
        if len(logs) > 5:
            lines.append(f"  ... and {len(logs) - 5} more")

    lines.append("")

    # Summary
    summary = result.get("summary", "")
    if summary:
        lines.append("Summary:")
        lines.append(f"  {summary}")
        lines.append("")

    # Root cause
    root_cause = result.get("root_cause", "")
    if root_cause:
        lines.append("Probable root cause:")
        lines.append(f"  {root_cause}")
        lines.append("")

    # Related tickets
    tickets = result.get("related_tickets", [])
    if tickets:
        lines.append("Related tickets:")
        for t in tickets[:5]:
            score = t.get("score", 0)
            status = t.get("status", "")
            score_str = f"Score: {score:.2f}" if score else "JQL match"
            status_str = f", {status}" if status else ""
            lines.append(f"  - {t['key']}: {t.get('summary', '')} ({score_str}{status_str})")
        lines.append("")

    # Suggested fix
    suggested_fix = result.get("suggested_fix", "")
    if suggested_fix:
        lines.append("Suggested fix:")
        lines.append(f"  {suggested_fix}")
        lines.append("")

    # Auto-fix availability
    can_auto_fix = result.get("can_auto_fix", False)
    if can_auto_fix and not result.get("no_patchy"):
        lines.append("Patchy could attempt an automatic fix for this issue.")

    # Error
    error = result.get("error")
    if error:
        lines.append(f"Error: {error}")

    return "\n".join(lines)


def main() -> None:
    """CLI entry point for Sleuth."""
    parser = argparse.ArgumentParser(
        description="Sleuth - Investigate errors through natural language queries",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python -m sleuth "user without role after registration"
  python -m sleuth "timeout errors in payment service" --service payment-api
  python -m sleuth "database connection errors" --hours 48 --env prod
  python -m sleuth "authentication failures" --no-patchy
        """
    )
    parser.add_argument(
        "query",
        nargs="+",
        help="Natural language description of the error to investigate"
    )
    parser.add_argument(
        "--service",
        default=None,
        help="Service name filter (optional, can be inferred from query)"
    )
    parser.add_argument(
        "--env",
        default=None,
        help="Environment filter (default: from config, usually 'prod')"
    )
    parser.add_argument(
        "--hours",
        type=int,
        default=24,
        help="Hours to look back (default: 24)"
    )
    parser.add_argument(
        "--no-patchy",
        action="store_true",
        help="Don't suggest automatic fixes via Patchy"
    )
    parser.add_argument(
        "--invoke-patchy",
        action="store_true",
        help="Automatically invoke Patchy if a fix is suggested"
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output raw JSON result instead of formatted text"
    )
    parser.add_argument(
        "--all-status",
        action="store_true",
        help="Search all log statuses, not just errors"
    )
    parser.add_argument(
        "--consolidate",
        action="store_true",
        help="Consolidate duplicate tickets (close duplicates and link to primary)"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be consolidated without making changes"
    )

    args = parser.parse_args()

    # Build query from arguments
    query = " ".join(args.query)

    # Initial state
    state: Dict[str, Any] = {
        "query": query,
        "service": args.service,
        "env": args.env,
        "hours_back": args.hours,
        "no_patchy": args.no_patchy,
        "all_status": args.all_status,
    }

    # Run the graph
    graph = build_graph()
    result = graph.invoke(state, config={"recursion_limit": 100})

    # Handle Patchy invocation if requested
    if args.invoke_patchy and result.get("can_auto_fix") and not args.no_patchy:
        print("Invoking Patchy...")
        result = invoke_patchy(result)

    # Handle consolidation if requested
    if args.consolidate:
        tickets = result.get("related_tickets", [])
        if len(tickets) < 2:
            print(f"\nNot enough related tickets to consolidate (found {len(tickets)}, need at least 2)")
        elif args.dry_run:
            # Sort by key to show what would happen
            def ticket_number(t):
                try:
                    return int(t.get("key", "").split("-")[-1])
                except (ValueError, IndexError):
                    return 999999
            sorted_tickets = sorted(tickets, key=ticket_number)
            primary = sorted_tickets[0]
            duplicates = sorted_tickets[1:]
            print(f"\n[DRY-RUN] Would consolidate {len(duplicates)} tickets into {primary['key']}:")
            print(f"  Primary: {primary['key']} - {primary.get('summary', '')[:50]}")
            print("  Would close:")
            for dup in duplicates:
                print(f"    - {dup['key']} - {dup.get('summary', '')[:50]}")
        else:
            print(f"\nConsolidating {len(tickets)} related tickets...")
            result = consolidate_duplicates(result)
            print(f"Result: {result.get('consolidation_result', 'Unknown')}")
            if result.get("closed_tickets"):
                print(f"Primary ticket: {result.get('primary_ticket')}")
                print(f"Closed tickets: {', '.join(result.get('closed_tickets', []))}")
            if result.get("consolidation_errors"):
                print(f"Errors: {result.get('consolidation_errors')}")

    # Output results
    if args.json:
        import json
        # Filter out internal keys
        output = {k: v for k, v in result.items() if not k.startswith("_")}
        print(json.dumps(output, indent=2, default=str))
    else:
        print(_format_output(result))

    # Exit with error code if investigation failed
    if result.get("error"):
        sys.exit(1)


if __name__ == "__main__":
    main()
