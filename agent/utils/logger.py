"""Secure logging utilities for the agent.

Provides sanitized logging that removes sensitive information like tokens,
emails, and API keys before outputting to logs.
"""
import json
import logging
import re
from typing import Any, Dict, Optional, Union

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

logger = logging.getLogger('dogcatcher-agent')


def sanitize_text(text: str) -> str:
    """Remove sensitive information from text.
    
    Args:
        text: Input text that may contain sensitive data
        
    Returns:
        Sanitized text with sensitive patterns replaced
    """
    if not text:
        return text
    
    # Email addresses
    text = re.sub(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}', '<email>', text)
    
    # API keys and tokens (common patterns)
    text = re.sub(r'sk-[a-zA-Z0-9]{20,}', '<api-key>', text)
    text = re.sub(r'ghp_[a-zA-Z0-9]{36}', '<github-token>', text)
    text = re.sub(r'[a-zA-Z0-9]{32,}', '<token>', text)
    
    # URLs with potential sensitive data
    text = re.sub(r'https?://[^\s]+', '<url>', text)
    
    # UUIDs
    text = re.sub(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}', '<uuid>', text, flags=re.IGNORECASE)
    
    # Long hex strings (likely hashes or tokens)
    text = re.sub(r'\b[0-9a-f]{24,}\b', '<hash>', text, flags=re.IGNORECASE)
    
    return text


def safe_json(obj: Any, max_length: int = 1000) -> str:
    """Safely serialize object to JSON with sensitive data sanitized.
    
    Args:
        obj: Object to serialize
        max_length: Maximum length of output string
        
    Returns:
        Sanitized JSON string
    """
    try:
        json_str = json.dumps(obj, indent=2, ensure_ascii=False, default=str)
        sanitized = sanitize_text(json_str)
        
        if len(sanitized) > max_length:
            sanitized = sanitized[:max_length] + "... [truncated]"
            
        return sanitized
    except Exception:
        return "<unable to serialize>"


def log_info(message: str, **kwargs) -> None:
    """Log info message with optional sanitized context."""
    if kwargs:
        context = safe_json(kwargs)
        logger.info(f"{message} | Context: {context}")
    else:
        logger.info(message)


def log_warning(message: str, **kwargs) -> None:
    """Log warning message with optional sanitized context."""
    if kwargs:
        context = safe_json(kwargs)
        logger.warning(f"{message} | Context: {context}")
    else:
        logger.warning(message)


def log_error(message: str, **kwargs) -> None:
    """Log error message with optional sanitized context."""
    if kwargs:
        context = safe_json(kwargs)
        logger.error(f"{message} | Context: {context}")
    else:
        logger.error(message)


def log_debug(message: str, **kwargs) -> None:
    """Log debug message with optional sanitized context."""
    if kwargs:
        context = safe_json(kwargs)
        logger.debug(f"{message} | Context: {context}")
    else:
        logger.debug(message)


def log_api_response(operation: str, status_code: int, response_data: Optional[Dict] = None) -> None:
    """Log API response with sanitized data.
    
    Args:
        operation: Description of the API operation
        status_code: HTTP status code
        response_data: Optional response data to log (will be sanitized)
    """
    if response_data:
        sanitized_data = safe_json(response_data, max_length=500)
        log_info(f"API {operation} completed", 
                status_code=status_code, 
                response_preview=sanitized_data)
    else:
        log_info(f"API {operation} completed", status_code=status_code)


def log_ticket_operation(operation: str, ticket_key: Optional[str] = None, **kwargs) -> None:
    """Log ticket-related operations with sanitized context.
    
    Args:
        operation: Description of the ticket operation
        ticket_key: Optional Jira ticket key
        **kwargs: Additional context to log
    """
    context = {"operation": operation}
    if ticket_key:
        context["ticket_key"] = ticket_key
    context.update(kwargs)
    
    log_info(f"Ticket operation: {operation}", **context)


def log_duplicate_detection(score: float, existing_key: str, **kwargs) -> None:
    """Log duplicate detection results.
    
    Args:
        score: Similarity score
        existing_key: Key of existing ticket
        **kwargs: Additional context
    """
    log_warning(f"Duplicate detected", 
               similarity_score=score, 
               existing_ticket=existing_key, 
               **kwargs)


def log_agent_progress(stage: str, **kwargs) -> None:
    """Log agent progress through different stages.
    
    Args:
        stage: Current stage of processing
        **kwargs: Additional context
    """
    log_info(f"Agent progress: {stage}", **kwargs)
