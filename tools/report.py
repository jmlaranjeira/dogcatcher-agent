"""Audit reporter for the Datadog → LLM → Jira agent.

Reads `.agent_cache/audit_logs.jsonl` and prints aggregate metrics such as
counts by error type and severity, duplicate vs created, and top fingerprints.
Optionally saves simple bar plots if `matplotlib` is available.
"""

from __future__ import annotations

import argparse
import json
import os
from dataclasses import dataclass
from datetime import datetime, timedelta
from collections import Counter, defaultdict
from typing import Any, Dict, Iterable, List, Optional, Tuple

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
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    return None


# -------- Load & filter -----------------------------------------------------

def load_audit(path: str) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    if not os.path.exists(path):
        print(f"⚠️  Audit file not found: {path}")
        return rows
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                # Skip malformed lines
                continue
    return rows


def filter_since(rows: Iterable[Dict[str, Any]], since_hours: int) -> List[Dict[str, Any]]:
    if since_hours <= 0:
        return list(rows)
    cutoff = datetime.utcnow() - timedelta(hours=since_hours)
    out = []
    for r in rows:
        ts = parse_ts(str(r.get("timestamp", "")))
        if ts is None or ts >= cutoff:
            out.append(r)
    return out


# -------- Aggregations ------------------------------------------------------

def summarize(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    total = len(rows)
    by_error = Counter(r.get("error_type", "<unknown>") for r in rows)
    by_sev = Counter(str(r.get("severity", "<unknown>")).lower() for r in rows)
    by_decision = Counter(str(r.get("decision", "<unknown>")).lower() for r in rows)

    # Creation/duplicate metrics (robust to missing keys)
    created = sum(1 for r in rows if r.get("ticket_created") is True)
    # "duplicate" here means we decided not to create because of an existing issue or fingerprint
    duplicates = sum(1 for r in rows if r.get("create_ticket") is False or r.get("duplicate") is True)

    # Fingerprint frequency (top noise sources)
    by_fp = Counter(r.get("fingerprint") or r.get("log_fingerprint") or r.get("log_key") for r in rows)

    # Top Jira keys touched (created or commented)
    by_issue = Counter(r.get("jira_key") or r.get("existing_issue_key") for r in rows)

    return {
        "total": total,
        "created": created,
        "duplicates": duplicates,
        "by_error": by_error,
        "by_severity": by_sev,
        "by_decision": by_decision,
        "by_fingerprint": by_fp,
        "by_issue": by_issue,
    }


def print_summary(summary: Dict[str, Any], top_n: int = 10) -> None:
    total = summary["total"]
    created = summary["created"]
    duplicates = summary["duplicates"]

    print("\n=== Audit Summary ===")
    print(f"Total decisions: {total}")
    print(f"Tickets created: {created}")
    print(f"Duplicates / no-create: {duplicates}")

    def _print_counter(title: str, cnt: Counter):
        print(f"\n{title}")
        if not cnt:
            print("  (none)")
            return
        for k, v in cnt.most_common(top_n):
            key = k if k else "<none>"
            print(f"  {key}: {v}")

    _print_counter("By error_type:", summary["by_error"])
    _print_counter("By severity:", summary["by_severity"])
    _print_counter("By decision:", summary["by_decision"])
    _print_counter("Top fingerprints:", summary["by_fingerprint"])
    _print_counter("Top Jira issues:", summary["by_issue"])


# -------- Optional plotting -------------------------------------------------
def try_plot(summary: Dict[str, Any], outdir: str = "reports", top_n: int = 10) -> Optional[str]:
    try:
        import matplotlib.pyplot as plt  # type: ignore
    except Exception:
        print("(matplotlib not available; skipping plots)")
        return None

    os.makedirs(outdir, exist_ok=True)
    timestamp = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    outfile = os.path.join(outdir, f"audit_{timestamp}.png")

    # Simple bar from error_type
    labels, values = zip(*summary["by_error"].most_common(top_n)) if summary["by_error"] else ([], [])
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
    parser = argparse.ArgumentParser(description="Audit reporter for the Datadog → Jira agent")
    parser.add_argument("--since-hours", type=int, default=168, help="Only include entries in the last N hours (default: 168)")
    parser.add_argument("--top", type=int, default=10, help="Top N rows for listings (default: 10)")
    parser.add_argument("--audit-file", type=str, default=AUDIT_FILE, help="Path to audit JSONL file")
    parser.add_argument("--plot", action="store_true", help="Generate a simple bar chart (requires matplotlib)")
    args = parser.parse_args()

    rows = load_audit(args.audit_file)
    if not rows:
        return

    rows = filter_since(rows, args.since_hours)
    summary = summarize(rows)
    print_summary(summary, top_n=args.top)

    if args.plot:
        try_plot(summary)


if __name__ == "__main__":
    main()