"""Patchy (🩹🤖) — verified-fix PR bot.

Provides a LangGraph pipeline to:
  resolve_repo → locate_fault → create_pr (LLM fix + reproducing test) → finish

CLI entrypoint lives in `patchy.patchy_graph`.
"""

from __future__ import annotations

__all__ = [
    # Expose top-level for convenience if needed later
]
