"""Callbacks that keep the conversation stage in session state.

The root agent's instruction reads {stage}, {country} and {industry}, so the
model always knows where it is in the flow without re-reading the history.
"""

import json
from datetime import datetime
from typing import Any

from google.adk.agents.callback_context import CallbackContext
from google.adk.tools import BaseTool, ToolContext

from .sub_agents.intent_extractor import intent_extractor_agent
from .sub_agents.pattern_discovery import pattern_discovery_agent

DEFAULT_STATE = {
    "stage": "intent",  # intent -> choose_k -> pattern_discovery -> patterns_ready
    "country": "",
    "industry": "",
    "k": 3,  # companies to study
    "m": 5,  # leads to find
}


def before_agent_run(callback_context: CallbackContext) -> None:
    """Fill in missing state keys and refresh the current time."""
    state = callback_context.state
    for key, value in DEFAULT_STATE.items():
        if key not in state:
            state[key] = value
    state["current_time"] = datetime.now().strftime("%Y-%m-%d %H:%M")


def after_tool_run(
    tool: BaseTool, args: dict[str, Any], tool_context: ToolContext, tool_response: Any
) -> None:
    """Advance the stage after an agent tool returns.

    Compares by name: `tool` is the AgentTool wrapper, not the agent itself,
    and the response is a plain dict (or JSON string), not the Pydantic model.
    """
    if tool.name == pattern_discovery_agent.name:
        if tool_context.state.get("discovered_patterns"):
            tool_context.state["stage"] = "patterns_ready"
        return
    if tool.name != intent_extractor_agent.name:
        return
    result = tool_response
    if isinstance(result, str):
        try:
            result = json.loads(result)
        except json.JSONDecodeError:
            return
    if not isinstance(result, dict):
        return

    state = tool_context.state
    state["country"] = result.get("country") or state.get("country", "")
    state["industry"] = result.get("industry") or state.get("industry", "")
    if state["country"] and state["industry"]:
        state["stage"] = "choose_k"
