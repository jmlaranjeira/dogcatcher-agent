"""LLM-powered query construction for Datadog searches.

Provides utilities for extracting entities from natural language queries
and building optimized Datadog search queries.
"""

import re
from typing import Dict, List, Optional

from openai import OpenAI

from agent.config import get_config
from agent.utils.logger import log_info, log_error


def extract_entities(text: str) -> Dict[str, List[str]]:
    """Extract entities from natural language query.

    Identifies common patterns like emails, UUIDs, service names, etc.

    Args:
        text: Natural language query text

    Returns:
        Dictionary of entity types to lists of found values
    """
    entities: Dict[str, List[str]] = {
        "emails": [],
        "uuids": [],
        "services": [],
        "keywords": [],
    }

    # Email pattern
    emails = re.findall(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}', text)
    entities["emails"] = emails

    # UUID pattern
    uuids = re.findall(
        r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}',
        text,
        re.IGNORECASE
    )
    entities["uuids"] = uuids

    # Common service name patterns (e.g., user-service, api-gateway)
    services = re.findall(r'\b[a-z]+-(?:service|api|gateway|worker|processor)\b', text, re.IGNORECASE)
    entities["services"] = services

    # Extract significant keywords (4+ chars, no common words)
    stopwords = {
        "that", "this", "with", "from", "have", "been", "were", "what",
        "when", "where", "which", "while", "about", "after", "before",
        "error", "errors", "logs", "find", "search", "show", "investigate"
    }
    words = re.findall(r'\b[a-zA-Z]{4,}\b', text)
    keywords = [w.lower() for w in words if w.lower() not in stopwords]
    # Deduplicate while preserving order
    seen = set()
    unique_keywords = []
    for kw in keywords:
        if kw not in seen:
            seen.add(kw)
            unique_keywords.append(kw)
    entities["keywords"] = unique_keywords[:10]  # Limit to top 10

    return entities


def build_datadog_query(
    user_query: str,
    service: Optional[str] = None,
    env: Optional[str] = None,
    use_llm: bool = True,
    all_status: bool = False,
) -> str:
    """Build an optimized Datadog query from natural language.

    Uses LLM to generate the query if use_llm is True, otherwise
    falls back to rule-based construction.

    Args:
        user_query: Natural language description of what to find
        service: Optional service name filter
        env: Optional environment filter (default: prod)
        use_llm: Whether to use LLM for query generation
        all_status: If True, don't filter by status:error

    Returns:
        Datadog query string
    """
    config = get_config()
    env = env or config.datadog_env

    if use_llm and config.openai_api_key:
        try:
            return _build_query_with_llm(user_query, service, env, all_status)
        except Exception as e:
            log_error("LLM query building failed, falling back to rules", error=str(e))

    return _build_query_rules(user_query, service, env, all_status)


def _build_query_with_llm(
    user_query: str,
    service: Optional[str],
    env: str,
    all_status: bool = False,
) -> str:
    """Build query using LLM."""
    config = get_config()
    client = OpenAI(api_key=config.openai_api_key)

    status_instruction = (
        "Do NOT include any status filter - search all log levels"
        if all_status
        else "Include status:error unless looking for other log levels"
    )

    prompt = f"""Build a Datadog Logs query to investigate:
"{user_query}"

Context:
- Service: {service or "infer from context if mentioned"}
- Environment: {env}

Rules:
1. Generate ONLY the query string, nothing else
2. Use standard Datadog query syntax
3. {status_instruction}
4. Quote each search term separately with spaces between them
5. IMPORTANT: Ensure proper spacing - each quoted term must be separated by a space
6. For emails, quote them separately: "user@example.com" "other term"
7. Use AND for required terms, OR for alternatives

Example output formats:
- service:myservice env:prod status:error "user registration" "role"
- service:api-gateway env:prod "user@example.com" "login"
- env:prod "timeout" "connection refused"

Query:"""

    response = client.chat.completions.create(
        model=config.openai_model,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.0,
        max_tokens=200,
    )

    query = response.choices[0].message.content.strip()
    # Clean up any markdown or quotes
    query = query.strip('`"\'')
    # Validate and fix query syntax
    query = _validate_and_fix_query(query)

    log_info("LLM generated Datadog query", query=query)
    return query


def _validate_and_fix_query(query: str) -> str:
    """Validate and fix common query syntax issues.

    Fixes:
    - Unbalanced quotes
    - Removes empty quoted strings
    - Trims excess whitespace
    - Fixes missing spaces after emails
    - Fixes adjacent quoted strings
    """
    # Fix adjacent quoted strings: "foo""bar" -> "foo" "bar"
    query = re.sub(r'"\s*"', '" "', query)

    # Fix email followed directly by a word (no space)
    # e.g., "user@example.comaccedido" -> "user@example.com" "accedido"
    query = re.sub(
        r'([a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,})([a-zA-Z])',
        r'\1" "\2',
        query
    )

    # Count quotes - if unbalanced, close or remove the last one
    quote_count = query.count('"')
    if quote_count % 2 != 0:
        # Find the last quote and check if it should be closed or removed
        last_quote_idx = query.rfind('"')
        after_quote = query[last_quote_idx + 1:].strip()
        if after_quote:
            # There's content after the unclosed quote, close it
            query = query + '"'
        else:
            # Trailing unclosed quote, likely incomplete - close it
            query = query + '"'

    # Remove truly empty quoted strings (just "")
    query = re.sub(r'""', '', query)

    # Clean up excess whitespace
    query = re.sub(r'\s+', ' ', query).strip()

    return query


def _build_query_rules(
    user_query: str,
    service: Optional[str],
    env: str,
    all_status: bool = False,
) -> str:
    """Build query using rule-based approach."""
    entities = extract_entities(user_query)

    parts = []

    # Service filter
    if service:
        parts.append(f"service:{service}")
    elif entities["services"]:
        parts.append(f"service:{entities['services'][0]}")

    # Environment filter
    parts.append(f"env:{env}")

    # Status filter (only if not searching all statuses)
    if not all_status:
        parts.append("status:error")

    # Add UUIDs if found
    for uuid in entities["uuids"][:2]:  # Limit to 2 UUIDs
        parts.append(f'"{uuid}"')

    # Add significant keywords
    for keyword in entities["keywords"][:5]:  # Limit to 5 keywords
        parts.append(f'"{keyword}"')

    query = " ".join(parts)
    log_info("Rule-based Datadog query built", query=query)
    return query


# Common query templates for specific error patterns
QUERY_TEMPLATES = {
    "null_pointer": 'status:error ("NullPointerException" OR "null" OR "undefined")',
    "timeout": 'status:error ("timeout" OR "timed out" OR "deadline exceeded")',
    "connection": 'status:error ("connection refused" OR "connection reset" OR "ECONNREFUSED")',
    "authentication": 'status:error ("authentication" OR "unauthorized" OR "401" OR "403")',
    "database": 'status:error ("database" OR "SQL" OR "query failed" OR "connection pool")',
}


def get_template_query(pattern_type: str, service: Optional[str] = None, env: str = "prod") -> Optional[str]:
    """Get a predefined query template for common error patterns.

    Args:
        pattern_type: Type of error pattern (e.g., "null_pointer", "timeout")
        service: Optional service filter
        env: Environment filter

    Returns:
        Query string or None if pattern not found
    """
    template = QUERY_TEMPLATES.get(pattern_type.lower())
    if not template:
        return None

    parts = []
    if service:
        parts.append(f"service:{service}")
    parts.append(f"env:{env}")
    parts.append(template)

    return " ".join(parts)
