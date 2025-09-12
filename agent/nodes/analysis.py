"""LLM analysis node (prompt, chain, analyze_log)."""
from typing import Dict, Any
from dotenv import load_dotenv
load_dotenv()

from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from agent.utils.logger import log_info, log_error, log_debug

import os
import re
import json

# LLM configuration via environment variables
# OPENAI_MODEL: model name (default: gpt-4o-mini)
# OPENAI_TEMPERATURE: float (default: 0)
# OPENAI_RESPONSE_FORMAT: "json_object" or "text" (default: json_object)
_model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
_temp_raw = os.getenv("OPENAI_TEMPERATURE", "0")
_resp_fmt = (os.getenv("OPENAI_RESPONSE_FORMAT", "json_object") or "json_object").lower()
try:
    _temp = float(_temp_raw)
except Exception:
    _temp = 0.0

if _resp_fmt == "json_object":
    _model_kwargs = {"response_format": {"type": "json_object"}}
else:
    _model_kwargs = {}

llm = ChatOpenAI(
    model=_model,
    temperature=_temp,
    model_kwargs=_model_kwargs,
)

prompt = ChatPromptTemplate.from_messages([
    (
        "system",
        (
            "You are a senior support engineer. Analyze the input log context and RETURN ONLY JSON (no code block). "
            "Fields required: "
            "error_type (kebab-case, e.g. pre-persist, db-constraint, kafka-consumer), "
            "create_ticket (boolean), "
            "ticket_title (short, action-oriented, no prefixes like [Datadog]), "
            "ticket_description (markdown including: Problem summary; Possible Causes as bullets; Suggested Actions as bullets), "
            "severity (one of: low, medium, high)."
        ),
    ),
    ("human", "{log_message}")
])

chain = prompt | llm


def analyze_log(state: Dict[str, Any]) -> Dict[str, Any]:
    """Analyze a log entry using an LLM to extract structured incident data."""
    log_data = state.get("log_data", {})
    msg = log_data.get("message", "")
    logger = log_data.get("logger", "unknown.logger")
    thread = log_data.get("thread", "unknown.thread")
    detail = log_data.get("detail", "")
    contextual_log = (
        f"[Logger]: {logger if logger else 'unknown.logger'}\n"
        f"[Thread]: {thread if thread else 'unknown.thread'}\n"
        f"[Message]: {msg if msg else '<no message>'}\n"
        f"[Detail]: {detail if detail else '<no detail>'}"
    )

    response = chain.invoke({"log_message": contextual_log})
    content = response.content
    log_debug("LLM analysis completed", content_preview=content[:200])
    
    match = re.search(r"```json\s*(\{.*?\})\s*```", content, re.DOTALL)
    raw_json = match.group(1) if match else content

    try:
        parsed = json.loads(raw_json)
        title = parsed.get("ticket_title")
        desc = parsed.get("ticket_description")
        if not title or not desc:
            raise ValueError("Missing title or description")
        
        log_info("Log analyzed successfully", 
                error_type=parsed.get('error_type'), 
                create_ticket=parsed.get('create_ticket'))
        
        return {**state, **parsed, "severity": parsed.get("severity", "low")}
    except (json.JSONDecodeError, ValueError) as e:
        log_error("LLM analysis failed", error=str(e), content_preview=content[:200])
        return {
            **state,
            "error_type": "unknown",
            "create_ticket": False,
            "ticket_title": "LLM returned invalid or incomplete data",
            "ticket_description": content
        }
