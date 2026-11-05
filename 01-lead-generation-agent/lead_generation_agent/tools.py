"""Function tools the root agent uses to record the user's choices in state."""

from google.adk.tools import ToolContext


def set_company_count(count: int, tool_context: ToolContext) -> dict:
    """Record how many past successful companies to study for pattern discovery.

    Call this after the user picks a number with get_user_choice.

    Args:
        count: Number of companies to research, from 1 to 10.
    """
    if not (tool_context.state.get("country") and tool_context.state.get("industry")):
        return {
            "status": "error",
            "message": "No target yet. Call intent_extractor_agent first.",
        }
    if not 1 <= count <= 10:
        return {"status": "error", "message": "count must be between 1 and 10"}
    tool_context.state["k"] = count
    tool_context.state["stage"] = "pattern_discovery"
    return {
        "status": "ok",
        "k": count,
        "next_step": "Call PatternDiscoveryAgent now, in this same turn.",
    }


def set_lead_count(count: int, tool_context: ToolContext) -> dict:
    """Record how many new leads to find and analyze.

    Call this after the user picks a number with get_user_choice.

    Args:
        count: Number of leads to find, from 1 to 10.
    """
    if not tool_context.state.get("discovered_patterns"):
        return {
            "status": "error",
            "message": "No patterns yet. Run PatternDiscoveryAgent first.",
        }
    if not 1 <= count <= 10:
        return {"status": "error", "message": "count must be between 1 and 10"}
    tool_context.state["m"] = count
    tool_context.state["stage"] = "lead_generation"
    return {
        "status": "ok",
        "m": count,
        "next_step": "Call LeadGenerationAgent now, in this same turn.",
    }
