from __future__ import annotations

import argparse
from typing import Any, Dict

from langgraph.graph import StateGraph, END

from .patchy_nodes import PatchyState, resolve_repo, locate_fault, create_pr, finish


def build_graph():
    g = StateGraph(PatchyState)
    g.set_entry_point("resolve_repo")
    g.add_node("resolve_repo", resolve_repo)
    g.add_node("locate_fault", locate_fault)
    g.add_node("create_pr", create_pr)
    g.add_node("finish", finish)

    g.add_edge("resolve_repo", "locate_fault")
    g.add_edge("locate_fault", "create_pr")
    g.add_edge("create_pr", "finish")
    g.add_edge("finish", END)

    return g.compile()


def main() -> None:
    parser = argparse.ArgumentParser(description="Patchy (🩹🤖) – minimal draft PR flow")
    parser.add_argument("--service", required=True)
    parser.add_argument("--error-type", dest="error_type", default="unknown")
    parser.add_argument("--loghash", default="")
    parser.add_argument("--jira", default="")
    parser.add_argument("--stacktrace", default="", help="Optional stacktrace to locate the faulted file/line")
    parser.add_argument("--logger", default="", help="Logger name (e.g., com.example.myservice.controller.LicensePurchaseController)")
    parser.add_argument("--hint", default="", help="Optional search hint (symbol/text)")
    parser.add_argument("--mode", default="note", choices=["touch","note","fix"], help="Change mode: touch (new file), note (append), fix (attempt minimal safe fix)")
    parser.add_argument("--draft", default="true")
    args = parser.parse_args()

    state: Dict[str, Any] = {
        "service": args.service,
        "error_type": args.error_type,
        "loghash": args.loghash,
        "jira": args.jira,
        "draft": str(args.draft).lower() in ("1", "true", "yes"),
        "stacktrace": args.stacktrace,
        "logger": args.logger,
        "hint": args.hint,
        "mode": args.mode,
    }

    graph = build_graph()
    result = graph.invoke(state, config={"recursion_limit": 2000})
    # Only print non-sensitive summary
    pr_url = result.get("pr_url")
    if pr_url:
        print(f"PR: {pr_url}")
    else:
        print(result.get("message", "done"))


if __name__ == "__main__":
    main()


