from __future__ import annotations

from pathlib import Path
from typing import Tuple


def _extract_receiver_token(line: str) -> str | None:
    """Find a likely receiver token from a line containing member access (e.g., foo.bar).

    Returns the left-most identifier before a dot, skipping obvious keywords.
    """
    import re

    # Match identifiers followed by a dot: foo., this., obj.
    matches = re.findall(r"([A-Za-z_][A-Za-z0-9_]*)\.", line)
    for tok in matches:
        if tok in {"this", "super"}:
            continue
        return tok
    return None


def apply_java_npe_guard(file_path: Path, fault_line_number: int) -> Tuple[bool, str]:
    """Insert a minimal null-guard above the fault line.

    Strategy: detect a receiver token in the fault line (e.g., `foo.bar`) and insert
    `java.util.Objects.requireNonNull(foo, "foo is required");` right above, keeping indentation.

    Returns (changed, message).
    """
    try:
        text = file_path.read_text(encoding="utf-8")
    except Exception as e:
        return False, f"read_failed: {e}"

    lines = text.splitlines()
    idx = max(0, min(len(lines) - 1, (fault_line_number - 1) if fault_line_number else 0))
    line = lines[idx] if lines else ""

    token = _extract_receiver_token(line)
    if not token:
        # Fallback: prepend a guidance comment
        lines.insert(idx, "// Patchy: consider adding null checks here")
        file_path.write_text("\n".join(lines) + ("\n" if text.endswith("\n") else ""), encoding="utf-8")
        return True, "guidance_comment_inserted"

    # Preserve indentation from the fault line
    import re
    indent = re.match(r"\s*", line).group(0) if line else ""
    guard = f"{indent}java.util.Objects.requireNonNull({token}, \"{token} is required\");"

    # Avoid duplicate insertion if the exact guard already exists in the previous few lines
    for lookback in range(1, 4):
        if idx - lookback >= 0 and guard.strip() in lines[idx - lookback].strip():
            return False, "guard_already_present"

    lines.insert(idx, guard)
    try:
        file_path.write_text("\n".join(lines) + ("\n" if text.endswith("\n") else ""), encoding="utf-8")
        return True, "npe_guard_inserted"
    except Exception as e:
        return False, f"write_failed: {e}"


