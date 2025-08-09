"""Audit helpers for JSONL logging (optional)."""
from typing import Dict, Any

def append_audit_log(entry: Dict[str, Any]) -> None:
    """If you decide to move _append_audit_log here, implement and wire it."""
    raise NotImplementedError("Optionally move _append_audit_log here and export as needed.")
