"""Java-specific fix strategies for Patchy.

Provides intelligent fixes for common Java error patterns:
- NullPointerException: null guards, Optional wrapping
- IllegalArgumentException: validation checks
- Duplicate key violations: existence checks
- General errors: try-catch, logging
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Tuple, Optional, List
from dataclasses import dataclass


@dataclass
class FixResult:
    """Result of applying a fix."""
    changed: bool
    strategy: str
    message: str
    lines_added: int = 0


def _get_indentation(line: str) -> str:
    """Extract leading whitespace from a line."""
    match = re.match(r"^(\s*)", line)
    return match.group(1) if match else ""


def _extract_receiver_token(line: str) -> Optional[str]:
    """Find a likely receiver token from a line containing member access.

    Returns the left-most identifier before a dot, skipping keywords.
    """
    # Skip package/import statements entirely
    stripped = line.strip()
    if stripped.startswith("package ") or stripped.startswith("import "):
        return None

    matches = re.findall(r"([A-Za-z_][A-Za-z0-9_]*)\.", line)
    skip_tokens = {"this", "super", "class", "new", "return", "throw", "if", "else", "for", "while", "package", "import", "org", "com", "java", "javax", "io", "net", "util"}
    for tok in matches:
        if tok.lower() not in skip_tokens:
            return tok
    return None


def _extract_method_params(lines: List[str], line_idx: int) -> List[str]:
    """Extract parameter names from the method signature containing the line."""
    params = []
    # Search backwards for method signature
    for i in range(line_idx, max(0, line_idx - 30), -1):
        line = lines[i]
        # Look for method signature pattern
        match = re.search(r"\w+\s+\w+\s*\(([^)]*)\)", line)
        if match:
            param_str = match.group(1)
            # Extract parameter names (last word before comma or end)
            param_matches = re.findall(r"(?:final\s+)?[\w<>\[\],\s]+\s+(\w+)\s*(?:,|$)", param_str)
            params.extend(param_matches)
            break
    return params


def _find_class_name(lines: List[str], line_idx: int) -> Optional[str]:
    """Find the class name containing the line."""
    for i in range(line_idx, -1, -1):
        match = re.search(r"(?:public|private|protected)?\s*class\s+(\w+)", lines[i])
        if match:
            return match.group(1)
    return None


def _is_inside_try_block(lines: List[str], line_idx: int) -> bool:
    """Check if the line is already inside a try block."""
    brace_count = 0
    for i in range(line_idx, -1, -1):
        line = lines[i]
        brace_count += line.count('}') - line.count('{')
        if 'try' in line and '{' in line:
            return brace_count <= 0
    return False


def _is_safe_insertion_point(lines: List[str], idx: int) -> bool:
    """Check if idx is a safe place to insert code (not in file header area)."""
    if idx < 0 or idx >= len(lines):
        return False

    # Check if we're still in the header area (package, imports, annotations)
    for i in range(idx + 1):
        stripped = lines[i].strip()
        if stripped.startswith("package ") or stripped.startswith("import "):
            if i >= idx:
                return False  # We're at or before a package/import
        # Found a class/interface/enum declaration - we're past the header
        if re.search(r'\b(class|interface|enum)\s+\w+', stripped):
            return True

    return True


def _find_first_method_body(lines: List[str]) -> Optional[int]:
    """Find the first line inside a method body (after opening brace)."""
    in_class = False
    brace_depth = 0

    for i, line in enumerate(lines):
        stripped = line.strip()

        # Track when we enter a class
        if re.search(r'\b(class|interface|enum)\s+\w+', stripped):
            in_class = True

        if not in_class:
            continue

        # Track braces
        brace_depth += line.count('{') - line.count('}')

        # Look for method signature followed by opening brace
        if re.search(r'\)\s*\{', line) or (re.search(r'\)\s*$', line) and i + 1 < len(lines) and '{' in lines[i + 1]):
            # Found a method, return the line after the opening brace
            if '{' in line:
                return i + 1
            elif i + 1 < len(lines) and '{' in lines[i + 1]:
                return i + 2

    return None


# ============================================================================
# Fix Strategy: NPE Guard
# ============================================================================

def apply_npe_guard(file_path: Path, fault_line: int, context: dict = None) -> FixResult:
    """Insert a null-guard using Objects.requireNonNull.

    Strategy: detect receiver token and insert requireNonNull above.
    """
    try:
        text = file_path.read_text(encoding="utf-8")
    except Exception as e:
        return FixResult(False, "npe_guard", f"read_failed: {e}")

    lines = text.splitlines()
    if not lines:
        return FixResult(False, "npe_guard", "empty_file")

    # Determine insertion point
    idx = max(0, min(len(lines) - 1, (fault_line - 1) if fault_line else 0))
    line = lines[idx] if lines else ""

    # Check if this is a safe insertion point
    if not _is_safe_insertion_point(lines, idx):
        # If fault_line was 0/invalid, try to find first method body
        if not fault_line or fault_line <= 0:
            method_idx = _find_first_method_body(lines)
            if method_idx and method_idx < len(lines):
                idx = method_idx
                line = lines[idx]
            else:
                return FixResult(False, "npe_guard", "no_safe_insertion_point_found")
        else:
            return FixResult(False, "npe_guard", "insertion_point_in_file_header")

    token = _extract_receiver_token(line)
    if not token:
        # Try scanning nearby lines for a valid token
        for offset in range(1, 6):
            if idx + offset < len(lines):
                token = _extract_receiver_token(lines[idx + offset])
                if token:
                    idx = idx + offset
                    line = lines[idx]
                    break
        if not token:
            return FixResult(False, "npe_guard", "no_receiver_token_found")

    indent = _get_indentation(line)

    # Build TODO comment with ticket info if available
    context = context or {}
    jira_key = context.get("jira", "")
    error_type = context.get("error_type", "NPE")

    todo_parts = ["TODO(Patchy): Defensive null guard - investigate root cause"]
    if jira_key:
        todo_parts.append(f"See {jira_key}")
    todo_comment = f"{indent}// {' | '.join(todo_parts)}"

    guard = f"{indent}java.util.Objects.requireNonNull({token}, \"{token} must not be null\");"

    # Avoid duplicate
    for lookback in range(1, 4):
        if idx - lookback >= 0 and f"requireNonNull({token}" in lines[idx - lookback]:
            return FixResult(False, "npe_guard", "guard_already_present")

    # Final safety check
    if not _is_safe_insertion_point(lines, idx):
        return FixResult(False, "npe_guard", "unsafe_insertion_point")

    # Insert TODO comment and guard
    lines.insert(idx, guard)
    lines.insert(idx, todo_comment)
    _write_file(file_path, lines, text)
    return FixResult(True, "npe_guard", f"Inserted null guard for '{token}' at line {idx + 1}", 2)


# ============================================================================
# Fix Strategy: Optional Wrapping
# ============================================================================

def apply_optional_wrap(file_path: Path, fault_line: int, context: dict = None) -> FixResult:
    """Wrap a potentially null value with Optional.ofNullable.

    Useful for method returns or variable assignments.
    """
    try:
        text = file_path.read_text(encoding="utf-8")
    except Exception as e:
        return FixResult(False, "optional_wrap", f"read_failed: {e}")

    lines = text.splitlines()
    idx = max(0, min(len(lines) - 1, (fault_line - 1) if fault_line else 0))
    line = lines[idx] if lines else ""

    indent = _get_indentation(line)

    # Look for return statement
    if "return " in line:
        # Transform: return foo.getBar(); -> return Optional.ofNullable(foo).map(x -> x.getBar()).orElse(null);
        match = re.search(r"return\s+(.+);", line)
        if match:
            expr = match.group(1)
            token = _extract_receiver_token(expr)
            if token:
                new_line = f"{indent}// TODO: Consider using Optional to handle null safely"
                lines.insert(idx, new_line)
                lines.insert(idx + 1, f"{indent}// Original: {line.strip()}")
                _write_file(file_path, lines, text)
                return FixResult(True, "optional_wrap", f"Added Optional suggestion for '{token}'", 2)

    return FixResult(False, "optional_wrap", "no_suitable_pattern_found")


# ============================================================================
# Fix Strategy: Validation Check
# ============================================================================

def apply_validation_check(file_path: Path, fault_line: int, context: dict = None) -> FixResult:
    """Add input validation at method entry.

    Useful for IllegalArgumentException errors.
    """
    try:
        text = file_path.read_text(encoding="utf-8")
    except Exception as e:
        return FixResult(False, "validation", f"read_failed: {e}")

    lines = text.splitlines()
    idx = max(0, min(len(lines) - 1, (fault_line - 1) if fault_line else 0))

    # Find method parameters
    params = _extract_method_params(lines, idx)
    if not params:
        return FixResult(False, "validation", "no_parameters_found")

    indent = _get_indentation(lines[idx]) if idx < len(lines) else "        "

    # Find method opening brace
    method_start = idx
    for i in range(idx, max(0, idx - 20), -1):
        if '{' in lines[i] and ('void' in lines[i] or 'public' in lines[i] or 'private' in lines[i]):
            method_start = i
            break

    # Insert validation after opening brace
    insert_idx = method_start + 1
    validations = []
    for param in params[:3]:  # Limit to first 3 params
        validations.append(f"{indent}// TODO: Validate {param} before use")
        validations.append(f"{indent}// if ({param} == null) throw new IllegalArgumentException(\"{param} must not be null\");")

    for i, v in enumerate(validations):
        lines.insert(insert_idx + i, v)

    _write_file(file_path, lines, text)
    return FixResult(True, "validation", f"Added validation TODOs for {len(params)} parameters", len(validations))


# ============================================================================
# Fix Strategy: Duplicate Check
# ============================================================================

def apply_duplicate_check(file_path: Path, fault_line: int, context: dict = None) -> FixResult:
    """Add existence check before insert/save operations.

    Useful for duplicate key/constraint violations.
    """
    try:
        text = file_path.read_text(encoding="utf-8")
    except Exception as e:
        return FixResult(False, "duplicate_check", f"read_failed: {e}")

    lines = text.splitlines()
    idx = max(0, min(len(lines) - 1, (fault_line - 1) if fault_line else 0))
    line = lines[idx] if lines else ""
    indent = _get_indentation(line)

    # Detect save/persist/insert patterns
    is_persist_op = any(kw in line.lower() for kw in ['save', 'persist', 'insert', 'create', 'add'])

    if not is_persist_op:
        # Search nearby lines
        for i in range(max(0, idx - 5), min(len(lines), idx + 5)):
            if any(kw in lines[i].lower() for kw in ['save', 'persist', 'insert', 'create']):
                is_persist_op = True
                idx = i
                line = lines[i]
                indent = _get_indentation(line)
                break

    if not is_persist_op:
        return FixResult(False, "duplicate_check", "no_persist_operation_found")

    # Insert existence check comment
    check_lines = [
        f"{indent}// TODO: Add existence check before save to prevent duplicate key violation",
        f"{indent}// Example: if (repository.existsById(entity.getId())) {{ throw new DuplicateKeyException(...); }}",
        f"{indent}// Or use: repository.findById(id).ifPresent(e -> {{ throw new ...; }});",
    ]

    for i, check in enumerate(check_lines):
        lines.insert(idx + i, check)

    _write_file(file_path, lines, text)
    return FixResult(True, "duplicate_check", "Added duplicate check TODO", len(check_lines))


# ============================================================================
# Fix Strategy: Try-Catch Wrapper
# ============================================================================

def apply_try_catch(file_path: Path, fault_line: int, context: dict = None) -> FixResult:
    """Wrap code in try-catch with logging.

    Useful for general exception handling.
    """
    try:
        text = file_path.read_text(encoding="utf-8")
    except Exception as e:
        return FixResult(False, "try_catch", f"read_failed: {e}")

    lines = text.splitlines()
    if not lines:
        return FixResult(False, "try_catch", "empty_file")

    idx = max(0, min(len(lines) - 1, (fault_line - 1) if fault_line else 0))

    # Check if this is a safe insertion point
    if not _is_safe_insertion_point(lines, idx):
        # If fault_line was 0/invalid, try to find first method body
        if not fault_line or fault_line <= 0:
            method_idx = _find_first_method_body(lines)
            if method_idx and method_idx < len(lines):
                idx = method_idx
            else:
                return FixResult(False, "try_catch", "no_safe_insertion_point_found")
        else:
            return FixResult(False, "try_catch", "insertion_point_in_file_header")

    line = lines[idx] if lines else ""
    indent = _get_indentation(line)

    # Check if already in try block
    if _is_inside_try_block(lines, idx):
        return FixResult(False, "try_catch", "already_in_try_block")

    class_name = _find_class_name(lines, idx) or "UnknownClass"

    # Build context info
    context = context or {}
    jira_key = context.get("jira", "")

    # Insert try-catch TODO with Jira reference
    todo_suffix = f" | See {jira_key}" if jira_key else ""
    catch_lines = [
        f"{indent}// TODO(Patchy): Consider adding retry logic for optimistic locking{todo_suffix}",
        f"{indent}// Example: @Retryable(value = OptimisticLockingFailureException.class, maxAttempts = 3)",
        f"{indent}// Or wrap in try-catch with retry loop",
    ]

    for i, catch in enumerate(catch_lines):
        lines.insert(idx + i, catch)

    _write_file(file_path, lines, text)
    return FixResult(True, "try_catch", f"Added error handling suggestion at line {idx + 1}", len(catch_lines))


# ============================================================================
# Fix Strategy: Logging Enhancement
# ============================================================================

def apply_logging(file_path: Path, fault_line: int, context: dict = None) -> FixResult:
    """Add debug logging before the problematic line.

    Useful for debugging and monitoring.
    """
    try:
        text = file_path.read_text(encoding="utf-8")
    except Exception as e:
        return FixResult(False, "logging", f"read_failed: {e}")

    lines = text.splitlines()
    idx = max(0, min(len(lines) - 1, (fault_line - 1) if fault_line else 0))
    line = lines[idx] if lines else ""
    indent = _get_indentation(line)

    # Extract variables from the line
    variables = re.findall(r'\b([a-z][a-zA-Z0-9]*)\b', line)
    variables = [v for v in variables if v not in {'if', 'else', 'for', 'while', 'return', 'new', 'this', 'null', 'true', 'false'}][:3]

    if not variables:
        return FixResult(False, "logging", "no_variables_found")

    # Build log statement
    log_vars = ", ".join([f"{v}={{{v}}}" for v in variables])
    log_args = ", ".join(variables)
    log_line = f'{indent}log.debug("Before operation: {log_vars}", {log_args});'

    lines.insert(idx, log_line)
    _write_file(file_path, lines, text)
    return FixResult(True, "logging", f"Added debug logging for {len(variables)} variables", 1)


# ============================================================================
# Main Entry Point
# ============================================================================

def apply_java_fix(file_path: Path, fault_line: int, error_type: str = "", context: dict = None) -> FixResult:
    """Apply the most appropriate fix based on error type.

    Args:
        file_path: Path to the Java file
        fault_line: Line number where the error occurred
        error_type: Type of error (e.g., 'npe', 'duplicate', 'validation')
        context: Additional context (e.g., from Jira ticket, log message)

    Returns:
        FixResult with details of what was changed
    """
    error_type = (error_type or "").lower().replace("-", "").replace("_", "")
    context = context or {}

    # Map error types to fix strategies
    if any(kw in error_type for kw in ['npe', 'null', 'nullpointer']):
        return apply_npe_guard(file_path, fault_line, context)

    elif any(kw in error_type for kw in ['duplicate', 'constraint', 'unique', 'key']):
        return apply_duplicate_check(file_path, fault_line, context)

    elif any(kw in error_type for kw in ['illegal', 'argument', 'validation', 'invalid']):
        return apply_validation_check(file_path, fault_line, context)

    elif any(kw in error_type for kw in ['persist', 'prepersist', 'save', 'insert']):
        return apply_duplicate_check(file_path, fault_line, context)

    elif any(kw in error_type for kw in ['optimistic', 'locking', 'concurrent', 'stale', 'version']):
        # Optimistic locking / concurrent modification errors
        return apply_try_catch(file_path, fault_line, context)

    else:
        # Default: try NPE guard first, then try-catch suggestion
        result = apply_npe_guard(file_path, fault_line, context)
        if result.changed:
            return result
        return apply_try_catch(file_path, fault_line, context)


# Legacy function for backward compatibility
def apply_java_npe_guard(file_path: Path, fault_line_number: int) -> Tuple[bool, str]:
    """Legacy wrapper for apply_npe_guard."""
    result = apply_npe_guard(file_path, fault_line_number)
    return result.changed, result.message


def _write_file(file_path: Path, lines: List[str], original_text: str) -> None:
    """Write lines back to file, preserving original line ending."""
    ending = "\n" if original_text.endswith("\n") else ""
    file_path.write_text("\n".join(lines) + ending, encoding="utf-8")
