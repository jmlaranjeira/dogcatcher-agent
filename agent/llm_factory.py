"""LLM provider factory.

Centralizes all LLM instantiation for both LangChain and direct SDK paths.
Supports OpenAI and AWS Bedrock, switchable via LLM_PROVIDER env var.

Entry points:
- ``get_langchain_llm()`` -- LangChain BaseChatModel (for analysis nodes)
- ``chat_completion()``   -- unified chat call (for sleuth, query builder)
- ``ping_llm()``          -- minimal call for health checks
- ``get_circuit_breaker_exception_class()`` -- provider-appropriate error class
"""

from __future__ import annotations

import os
from typing import Any, Dict, List

from agent.utils.logger import log_info


def _get_provider() -> str:
    """Return the configured LLM provider (default: openai)."""
    return os.getenv("LLM_PROVIDER", "openai").lower()


# ── LangChain path ──────────────────────────────────────────────


def get_langchain_llm():
    """Return a LangChain chat model based on LLM_PROVIDER.

    For openai:  ChatOpenAI with response_format=json_object if configured.
    For bedrock: ChatBedrockConverse.
    """
    provider = _get_provider()

    if provider == "bedrock":
        from langchain_aws import ChatBedrockConverse

        region = os.getenv("AWS_REGION", "eu-west-1")
        model_id = os.getenv(
            "BEDROCK_MODEL_ID",
            "anthropic.claude-3-haiku-20240307-v1:0",
        )
        temperature = float(os.getenv("BEDROCK_TEMPERATURE", "0"))
        max_tokens = int(os.getenv("BEDROCK_MAX_TOKENS", "4096"))

        log_info("Using Bedrock LLM", model_id=model_id, region=region)
        return ChatBedrockConverse(
            model=model_id,
            region_name=region,
            temperature=temperature,
            max_tokens=max_tokens,
        )

    # Default: OpenAI
    from langchain_openai import ChatOpenAI

    model = os.getenv("OPENAI_MODEL", "gpt-4.1-nano")
    temp = float(os.getenv("OPENAI_TEMPERATURE", "0"))
    resp_fmt = (
        os.getenv("OPENAI_RESPONSE_FORMAT", "json_object") or "json_object"
    ).lower()

    model_kwargs: Dict[str, Any] = {}
    if resp_fmt == "json_object":
        model_kwargs = {"response_format": {"type": "json_object"}}

    log_info("Using OpenAI LLM", model=model)
    return ChatOpenAI(
        model=model,
        temperature=temp,
        model_kwargs=model_kwargs,
    )


# ── Direct SDK path (sleuth, healthcheck) ────────────────────────


def chat_completion(
    messages: List[Dict[str, str]],
    temperature: float = 0.0,
    max_tokens: int = 4096,
    json_response: bool = False,
) -> str:
    """Unified chat completion call for both providers.

    Args:
        messages: List of {"role": ..., "content": ...} dicts.
        temperature: Sampling temperature.
        max_tokens: Max output tokens.
        json_response: If True, request JSON output.

    Returns:
        The assistant message content as a string.
    """
    provider = _get_provider()

    if provider == "bedrock":
        return _bedrock_chat_completion(messages, temperature, max_tokens, json_response)
    return _openai_chat_completion(messages, temperature, max_tokens, json_response)


def _openai_chat_completion(
    messages: List[Dict[str, str]],
    temperature: float,
    max_tokens: int,
    json_response: bool,
) -> str:
    from openai import OpenAI

    api_key = os.getenv("OPENAI_API_KEY", "")
    model = os.getenv("OPENAI_MODEL", "gpt-4.1-nano")
    client = OpenAI(api_key=api_key)

    kwargs: Dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if json_response:
        kwargs["response_format"] = {"type": "json_object"}

    response = client.chat.completions.create(**kwargs)
    return response.choices[0].message.content.strip()


def _bedrock_chat_completion(
    messages: List[Dict[str, str]],
    temperature: float,
    max_tokens: int,
    json_response: bool,
) -> str:
    import boto3

    region = os.getenv("AWS_REGION", "eu-west-1")
    model_id = os.getenv(
        "BEDROCK_MODEL_ID",
        "anthropic.claude-3-haiku-20240307-v1:0",
    )
    client = boto3.client("bedrock-runtime", region_name=region)

    # Convert to Bedrock Converse format
    bedrock_messages = []
    system_prompt = None
    for msg in messages:
        if msg["role"] == "system":
            system_prompt = msg["content"]
        else:
            bedrock_messages.append({
                "role": msg["role"],
                "content": [{"text": msg["content"]}],
            })

    # Enforce JSON output via system prompt (Bedrock has no native json mode)
    if json_response:
        json_instruction = (
            "\n\nIMPORTANT: You MUST respond with valid JSON only. "
            "No markdown, no code blocks, just raw JSON."
        )
        if system_prompt:
            system_prompt += json_instruction
        else:
            system_prompt = json_instruction

    converse_kwargs: Dict[str, Any] = {
        "modelId": model_id,
        "messages": bedrock_messages,
        "inferenceConfig": {
            "temperature": temperature,
            "maxTokens": max_tokens,
        },
    }
    if system_prompt:
        converse_kwargs["system"] = [{"text": system_prompt}]

    response = client.converse(**converse_kwargs)
    return response["output"]["message"]["content"][0]["text"].strip()


# ── Health check ─────────────────────────────────────────────────


def ping_llm() -> str:
    """Minimal LLM call for health checking.

    Returns:
        Provider description string on success (e.g. "Bedrock (model-id)").
    """
    provider = _get_provider()

    if provider == "bedrock":
        import boto3

        region = os.getenv("AWS_REGION", "eu-west-1")
        model_id = os.getenv(
            "BEDROCK_MODEL_ID", "anthropic.claude-3-haiku-20240307-v1:0"
        )
        client = boto3.client("bedrock-runtime", region_name=region)
        client.converse(
            modelId=model_id,
            messages=[{"role": "user", "content": [{"text": "ping"}]}],
            inferenceConfig={"maxTokens": 1},
        )
        return f"Bedrock ({model_id})"

    # OpenAI
    from openai import OpenAI

    api_key = os.getenv("OPENAI_API_KEY", "")
    model = os.getenv("OPENAI_MODEL", "gpt-4.1-nano")
    client = OpenAI(api_key=api_key)
    client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": "ping"}],
        max_tokens=1,
    )
    return f"OpenAI ({model})"


# ── Circuit breaker support ──────────────────────────────────────


def get_circuit_breaker_exception_class() -> type:
    """Return the appropriate exception class for the current provider."""
    provider = _get_provider()
    if provider == "bedrock":
        from botocore.exceptions import ClientError
        return ClientError
    from openai import OpenAIError
    return OpenAIError
