"""Sanitize and parse JSON from LLM responses.

LLMs (especially Bedrock/Claude) sometimes return JSON with unescaped
control characters inside string values (literal newlines, tabs).
This module handles those cases gracefully.
"""

import json
import re


def parse_llm_json(raw: str) -> dict:
    """Parse JSON from an LLM response, handling control characters.

    Tries json.loads first. On failure, sanitizes unescaped control
    characters inside JSON string values and retries.

    Args:
        raw: Raw JSON string from LLM output

    Returns:
        Parsed dictionary

    Raises:
        json.JSONDecodeError: If JSON is still invalid after sanitization
        ValueError: If parsed result is not a dict
    """
    # Fast path: try parsing directly
    try:
        result = json.loads(raw)
        if not isinstance(result, dict):
            raise ValueError(f"Expected dict, got {type(result).__name__}")
        return result
    except json.JSONDecodeError:
        pass

    # Sanitize: replace unescaped control characters inside string values
    sanitized = _sanitize_json_string(raw)

    result = json.loads(sanitized)
    if not isinstance(result, dict):
        raise ValueError(f"Expected dict, got {type(result).__name__}")
    return result


def _sanitize_json_string(raw: str) -> str:
    """Replace unescaped control characters inside JSON string values.

    Walks through the raw string tracking whether we're inside a JSON
    string (between unescaped double quotes). Control characters found
    inside strings are replaced with their escaped equivalents.
    """
    result = []
    in_string = False
    i = 0

    while i < len(raw):
        char = raw[i]

        if char == '"' and (i == 0 or raw[i - 1] != "\\"):
            in_string = not in_string
            result.append(char)
        elif in_string and ord(char) < 0x20:
            # Control character inside a JSON string — escape it
            escape_map = {
                "\n": "\\n",
                "\r": "\\r",
                "\t": "\\t",
                "\b": "\\b",
                "\f": "\\f",
            }
            result.append(escape_map.get(char, f"\\u{ord(char):04x}"))
        else:
            result.append(char)

        i += 1

    return "".join(result)
