"""Audit reporter for the Datadog → LLM → Jira agent.

Reads `.agent_cache/audit_logs.jsonl` and prints aggregate metrics such as
counts by error type and severity, duplicate vs created, and top fingerprints.
Supports multi-tenant mode, JSON/CSV export, temporal breakdowns, and filters.
Optionally saves simple bar plots if `matplotlib` is available.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional

# Graceful import of multi-tenant helpers — report works standalone too.
try:
    from agent.team_loader import is_multi_tenant, list_team_ids
except ImportError:

    def is_multi_tenant() -> bool:
        return False

    def list_team_ids() -> List[str]:
        return []


# -------- Files & dates -----------------------------------------------------

AUDIT_DIR = ".agent_cache"
AUDIT_FILE = os.path.join(AUDIT_DIR, "audit_logs.jsonl")
TS_FORMATS = (
    "%Y-%m-%dT%H:%M:%S.%fZ",
    "%Y-%m-%dT%H:%M:%SZ",
    "%Y-%m-%d %H:%M:%S",
)


def parse_ts(value: str) -> Optional[datetime]:
    if not value:
        return None
    for fmt in TS_FORMATS:
        try:
            dt = datetime.strptime(value, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            continue
    return None


# -------- Load & filter -----------------------------------------------------


def load_audit(path: str) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    if not os.path.exists(path):
        print(f"Audit file not found: {path}", file=sys.stderr)
        return rows
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def filter_since(
    rows: Iterable[Dict[str, Any]], since_hours: int
) -> List[Dict[str, Any]]:
    """Filter audit rows to the last *since_hours* hours.

    Rows whose timestamp is missing or unparseable are **kept** (fail-open)
    so that the report never silently drops data.  Pass ``since_hours <= 0``
    to return all rows.
    """
    if since_hours <= 0:
        return list(rows)
    cutoff = datetime.now(timezone.utc) - timedelta(hours=since_hours)
    out = []
    for r in rows:
        ts = parse_ts(str(r.get("timestamp", "")))
        if ts is None or ts >= cutoff:
            out.append(r)
    return out


def _apply_filters(
    rows: List[Dict[str, Any]],
    *,
    service: Optional[str] = None,
    severity: Optional[str] = None,
    error_type: Optional[str] = None,
    decision: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Apply optional field-level filters to audit rows."""
    filtered = rows
    if service:
        filtered = [r for r in filtered if r.get("team_service") == service]
    if severity:
        filtered = [
            r
            for r in filtered
            if str(r.get("severity", "")).lower() == severity.lower()
        ]
    if error_type:
        filtered = [r for r in filtered if r.get("error_type") == error_type]
    if decision:
        filtered = [
            r
            for r in filtered
            if str(r.get("decision", "")).lower() == decision.lower()
        ]
    return filtered


# -------- Multi-tenant resolution -------------------------------------------


def _resolve_audit_paths(
    audit_file: Optional[str],
    team: Optional[str],
    all_teams: bool,
) -> List[tuple]:
    """Return list of ``(label, file_path)`` for audit files to process."""
    if audit_file and audit_file != AUDIT_FILE:
        return [("custom", audit_file)]

    if team:
        path = os.path.join(AUDIT_DIR, "teams", team, "audit_logs.jsonl")
        return [(team, path)]

    if all_teams or is_multi_tenant():
        paths = []
        for tid in list_team_ids():
            p = os.path.join(AUDIT_DIR, "teams", tid, "audit_logs.jsonl")
            paths.append((tid, p))
        if not paths:
            return [("default", AUDIT_FILE)]
        return paths

    return [("default", AUDIT_FILE)]


# -------- Aggregations ------------------------------------------------------


def summarize(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    total = len(rows)
    by_error = Counter(r.get("error_type", "<unknown>") for r in rows)
    by_sev = Counter(str(r.get("severity", "<unknown>")).lower() for r in rows)
    by_decision = Counter(
        str(r.get("decision", "<unknown>")).lower() for r in rows
    )

    def _dec(r: Dict[str, Any]) -> str:
        return str(r.get("decision", "")).lower()

    created = sum(1 for r in rows if _dec(r) == "created")
    dupes = sum(1 for r in rows if _dec(r).startswith("duplicate"))
    simulated = sum(1 for r in rows if _dec(r) == "simulated")
    would_create = sum(
        1
        for r in rows
        if _dec(r) == "simulated" and r.get("create_ticket") is True
    )
    cap_reached = sum(1 for r in rows if _dec(r) == "cap-reached")
    unknown_decisions = sum(1 for r in rows if not _dec(r))

    # Backward compatibility: if decision field is missing, fall back to booleans.
    if created == 0 and any(r.get("ticket_created") is True for r in rows):
        created = sum(1 for r in rows if r.get("ticket_created") is True)
    if dupes == 0 and any(r.get("duplicate") is True for r in rows):
        dupes = sum(1 for r in rows if r.get("duplicate") is True)

    by_fp = Counter(
        r.get("fingerprint") or r.get("log_fingerprint") or r.get("log_key")
        for r in rows
    )
    by_issue = Counter(
        r.get("jira_key") or r.get("existing_issue_key") for r in rows
    )

    return {
        "total": total,
        "created": created,
        "duplicates": dupes,
        "simulated": simulated,
        "would_create": would_create,
        "cap_reached": cap_reached,
        "unknown_decisions": unknown_decisions,
        "by_error": by_error,
        "by_severity": by_sev,
        "by_decision": by_decision,
        "by_fingerprint": by_fp,
        "by_issue": by_issue,
    }


# -------- Temporal breakdown ------------------------------------------------


def temporal_breakdown(
    rows: List[Dict[str, Any]], granularity: str = "day"
) -> Dict[str, Counter]:
    """Group key metrics by time bucket (``day`` or ``hour``)."""
    fmt = "%Y-%m-%d" if granularity == "day" else "%Y-%m-%dT%H:00"
    buckets: Dict[str, Counter] = {
        "total": Counter(),
        "created": Counter(),
        "duplicates": Counter(),
    }
    for r in rows:
        ts = parse_ts(str(r.get("timestamp", "")))
        if ts is None:
            continue
        bucket = ts.strftime(fmt)
        buckets["total"][bucket] += 1
        dec = str(r.get("decision", "")).lower()
        if dec == "created":
            buckets["created"][bucket] += 1
        elif dec.startswith("duplicate"):
            buckets["duplicates"][bucket] += 1
    return buckets


# -------- Display helpers ---------------------------------------------------


def _print_counter(title: str, cnt: Counter, top_n: int = 10) -> None:
    print(f"\n{title}")
    if not cnt:
        print("  (none)")
        return
    for k, v in cnt.most_common(top_n):
        key = k if k else "<none>"
        print(f"  {key}: {v}")


def _print_temporal(buckets: Dict[str, Counter]) -> None:
    print("\n=== Trends ===")
    if not buckets["total"]:
        print("  (no timestamped entries)")
        return
    for label in sorted(buckets["total"]):
        t = buckets["total"][label]
        c = buckets["created"].get(label, 0)
        d = buckets["duplicates"].get(label, 0)
        print(f"  {label}  total={t}  created={c}  duplicates={d}")


def print_summary(
    summary: Dict[str, Any], top_n: int = 10, label: Optional[str] = None
) -> None:
    header = f"=== Audit Summary ({label}) ===" if label else "=== Audit Summary ==="
    print(f"\n{header}")
    print(f"Total decisions: {summary['total']}")
    print(f"Tickets created: {summary['created']}")
    print(f"Duplicates / no-create: {summary['duplicates']}")

    simulated = summary.get("simulated", 0)
    would_create = summary.get("would_create", 0)
    cap_reached = summary.get("cap_reached", 0)
    unknown_decisions = summary.get("unknown_decisions", 0)

    if simulated:
        print(f"Simulated (dry-run): {simulated}")
        if would_create:
            print(f"Would create (simulated true): {would_create}")
    if cap_reached:
        print(f"Cap reached (limit): {cap_reached}")
    if unknown_decisions:
        print(f"Unknown decision: {unknown_decisions}")

    _print_counter("By error_type:", summary["by_error"], top_n)
    _print_counter("By severity:", summary["by_severity"], top_n)
    _print_counter("By decision:", summary["by_decision"], top_n)
    _print_counter("Top fingerprints:", summary["by_fingerprint"], top_n)
    _print_counter("Top Jira issues:", summary["by_issue"], top_n)


# -------- Export helpers ----------------------------------------------------


def _serialize_summary(summary: Dict[str, Any]) -> Dict[str, Any]:
    """Convert Counter objects to plain dicts for JSON serialization."""
    out: Dict[str, Any] = {}
    for k, v in summary.items():
        if isinstance(v, Counter):
            out[k] = dict(v.most_common())
        else:
            out[k] = v
    return out


def _write_csv(rows: List[Dict[str, Any]]) -> None:
    """Write raw audit rows to stdout as CSV."""
    if not rows:
        return
    fieldnames = sorted({k for r in rows for k in r.keys()})
    writer = csv.DictWriter(
        sys.stdout, fieldnames=fieldnames, extrasaction="ignore"
    )
    writer.writeheader()
    writer.writerows(rows)


# -------- Optional plotting -------------------------------------------------


def try_plot(
    summary: Dict[str, Any], outdir: str = "reports", top_n: int = 10
) -> Optional[str]:
    try:
        import matplotlib.pyplot as plt  # type: ignore
    except ImportError:
        print("(matplotlib not available; skipping plots)")
        return None

    os.makedirs(outdir, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    outfile = os.path.join(outdir, f"audit_{timestamp}.png")

    labels, values = (
        zip(*summary["by_error"].most_common(top_n))
        if summary["by_error"]
        else ([], [])
    )
    if labels:
        plt.figure()
        plt.bar(range(len(values)), values)
        plt.xticks(range(len(labels)), labels, rotation=45, ha="right")
        plt.title("Top error types")
        plt.tight_layout()
        plt.savefig(outfile)
        print(f"Saved plot: {outfile}")
    else:
        print("No data to plot.")

    return outfile


# -------- CLI ---------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Audit reporter for the Datadog → Jira agent"
    )
    # Time window
    parser.add_argument(
        "--since-hours",
        type=int,
        default=168,
        help="Only include entries in the last N hours (default: 168, 0 = all)",
    )
    parser.add_argument(
        "--top",
        type=int,
        default=10,
        help="Top N rows for listings (default: 10)",
    )
    # Source
    parser.add_argument(
        "--audit-file",
        type=str,
        default=AUDIT_FILE,
        help="Path to audit JSONL file (overrides multi-tenant auto-detection)",
    )
    parser.add_argument(
        "--team",
        type=str,
        default=None,
        help="Report on a specific team (multi-tenant mode)",
    )
    parser.add_argument(
        "--all-teams",
        action="store_true",
        help="Aggregate all teams (auto-detected when teams.yaml exists)",
    )
    # Filters
    parser.add_argument(
        "--service", type=str, default=None, help="Filter by team_service"
    )
    parser.add_argument(
        "--severity", type=str, default=None, help="Filter by severity level"
    )
    parser.add_argument(
        "--error-type", type=str, default=None, help="Filter by error_type"
    )
    parser.add_argument(
        "--decision", type=str, default=None, help="Filter by decision value"
    )
    # Output
    parser.add_argument(
        "--format",
        choices=["text", "json", "csv"],
        default="text",
        help="Output format (default: text)",
    )
    parser.add_argument(
        "--group-by",
        choices=["day", "hour", "none"],
        default="none",
        help="Show temporal breakdown (default: none)",
    )
    parser.add_argument(
        "--plot",
        action="store_true",
        help="Generate a simple bar chart (requires matplotlib)",
    )
    args = parser.parse_args()

    # Resolve audit file paths (single-tenant or multi-tenant)
    paths = _resolve_audit_paths(args.audit_file, args.team, args.all_teams)

    all_rows: List[Dict[str, Any]] = []
    team_rows: Dict[str, List[Dict[str, Any]]] = {}

    for label, path in paths:
        rows = load_audit(path)
        rows = filter_since(rows, args.since_hours)
        rows = _apply_filters(
            rows,
            service=args.service,
            severity=args.severity,
            error_type=args.error_type,
            decision=args.decision,
        )
        if rows:
            team_rows[label] = rows
            all_rows.extend(rows)

    if not all_rows:
        print("No audit entries match the given filters.", file=sys.stderr)
        sys.exit(1)

    multi = len(paths) > 1
    fmt = args.format

    # --- JSON output ---
    if fmt == "json":
        output: Dict[str, Any] = {}
        if multi:
            output["teams"] = {}
            for label, rows in team_rows.items():
                s = _serialize_summary(summarize(rows))
                if args.group_by != "none":
                    s["trends"] = {
                        k: dict(v)
                        for k, v in temporal_breakdown(
                            rows, args.group_by
                        ).items()
                    }
                output["teams"][label] = s
        agg = _serialize_summary(summarize(all_rows))
        if args.group_by != "none":
            agg["trends"] = {
                k: dict(v)
                for k, v in temporal_breakdown(
                    all_rows, args.group_by
                ).items()
            }
        output["aggregate" if multi else "summary"] = agg
        print(json.dumps(output, default=str, indent=2))
        return

    # --- CSV output ---
    if fmt == "csv":
        _write_csv(all_rows)
        return

    # --- Text output ---
    if multi:
        for label, rows in team_rows.items():
            summary = summarize(rows)
            print_summary(summary, top_n=args.top, label=label)
            if args.group_by != "none":
                _print_temporal(temporal_breakdown(rows, args.group_by))

    # Aggregate (or single-tenant) summary
    agg_label = "aggregate" if multi else None
    summary = summarize(all_rows)
    print_summary(summary, top_n=args.top, label=agg_label)

    if args.group_by != "none":
        _print_temporal(temporal_breakdown(all_rows, args.group_by))

    if args.plot:
        try_plot(summary)


if __name__ == "__main__":
    main()
