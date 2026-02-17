"""Lightweight Markdown-to-ADF (Atlassian Document Format) converter.

Handles the subset of Markdown produced by the LLM analysis and
``build_enhanced_description``:

* ``### heading``  -> ADF ``heading`` (level 3)
* ``## heading``   -> ADF ``heading`` (level 2)
* ``- bullet`` / ``* bullet`` / ``• bullet`` -> ADF ``bulletList``
* ``---``          -> ADF ``rule`` (horizontal rule)
* ``**bold**``     -> ADF ``strong`` mark
* bare URLs       -> ADF ``link`` mark
* plain lines     -> ADF ``paragraph`` with ``hardBreak`` between consecutive lines

Public API
----------
``markdown_to_adf(text)`` — returns a complete ADF ``doc`` dict ready for the
Jira REST API ``description`` or comment ``body`` field.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List

# ---------------------------------------------------------------------------
# Inline formatting helpers
# ---------------------------------------------------------------------------

_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
_URL_RE = re.compile(r"(https?://\S+)")


def _inline_nodes(text: str) -> List[Dict[str, Any]]:
    """Convert a line of text into a list of ADF inline nodes.

    Handles ``**bold**`` and bare ``https://`` URLs.
    """
    nodes: List[Dict[str, Any]] = []
    # Split by bold first, then handle URLs within each segment
    parts = _BOLD_RE.split(text)
    for i, part in enumerate(parts):
        if not part:
            continue
        is_bold = i % 2 == 1  # odd indices are bold captures
        # Split by URLs within this part
        url_parts = _URL_RE.split(part)
        for j, seg in enumerate(url_parts):
            if not seg:
                continue
            is_url = j % 2 == 1
            node: Dict[str, Any] = {"type": "text", "text": seg}
            marks: List[Dict[str, Any]] = []
            if is_bold:
                marks.append({"type": "strong"})
            if is_url:
                marks.append({"type": "link", "attrs": {"href": seg}})
            if marks:
                node["marks"] = marks
            nodes.append(node)
    return nodes or [{"type": "text", "text": text}]


# ---------------------------------------------------------------------------
# Block-level parser
# ---------------------------------------------------------------------------

_HEADING_RE = re.compile(r"^(#{2,3})\s+(.+)$")
_BULLET_RE = re.compile(r"^[\-\*•]\s+(.+)$")
_RULE_RE = re.compile(r"^-{3,}$")


def _flush_paragraph(lines: List[str], blocks: List[Dict[str, Any]]) -> None:
    """Flush accumulated plain-text lines into a single paragraph node."""
    if not lines:
        return
    content: List[Dict[str, Any]] = []
    for idx, line in enumerate(lines):
        if idx > 0:
            content.append({"type": "hardBreak"})
        content.extend(_inline_nodes(line))
    blocks.append({"type": "paragraph", "content": content})
    lines.clear()


def _flush_bullets(
    items: List[List[Dict[str, Any]]], blocks: List[Dict[str, Any]]
) -> None:
    """Flush accumulated bullet items into a bulletList node."""
    if not items:
        return
    list_items = [
        {
            "type": "listItem",
            "content": [{"type": "paragraph", "content": inlines}],
        }
        for inlines in items
    ]
    blocks.append({"type": "bulletList", "content": list_items})
    items.clear()


def markdown_to_adf(text: str) -> Dict[str, Any]:
    """Convert a Markdown string to a Jira ADF document.

    Parameters
    ----------
    text:
        Markdown-formatted string (headings, bullets, bold, URLs).

    Returns
    -------
    dict
        A complete ADF ``doc`` node::

            {"type": "doc", "version": 1, "content": [...]}
    """
    if not text:
        return {
            "type": "doc",
            "version": 1,
            "content": [
                {"type": "paragraph", "content": [{"type": "text", "text": ""}]}
            ],
        }

    blocks: List[Dict[str, Any]] = []
    pending_lines: List[str] = []
    pending_bullets: List[List[Dict[str, Any]]] = []

    for raw_line in text.split("\n"):
        line = raw_line.rstrip()

        # --- Horizontal rule ---
        if _RULE_RE.match(line):
            _flush_bullets(pending_bullets, blocks)
            _flush_paragraph(pending_lines, blocks)
            blocks.append({"type": "rule"})
            continue

        # --- Heading ---
        m = _HEADING_RE.match(line)
        if m:
            _flush_bullets(pending_bullets, blocks)
            _flush_paragraph(pending_lines, blocks)
            level = len(m.group(1))
            blocks.append(
                {
                    "type": "heading",
                    "attrs": {"level": level},
                    "content": _inline_nodes(m.group(2)),
                }
            )
            continue

        # --- Bullet item ---
        m = _BULLET_RE.match(line)
        if m:
            _flush_paragraph(pending_lines, blocks)
            pending_bullets.append(_inline_nodes(m.group(1)))
            continue

        # If we were collecting bullets and hit a non-bullet, flush them
        if pending_bullets:
            _flush_bullets(pending_bullets, blocks)

        # --- Blank line ---
        if not line:
            _flush_paragraph(pending_lines, blocks)
            continue

        # --- Plain text line ---
        pending_lines.append(line)

    # Flush remaining
    _flush_bullets(pending_bullets, blocks)
    _flush_paragraph(pending_lines, blocks)

    # ADF doc must have at least one block
    if not blocks:
        blocks.append({"type": "paragraph", "content": [{"type": "text", "text": ""}]})

    return {"type": "doc", "version": 1, "content": blocks}
