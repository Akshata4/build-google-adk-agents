"""App-wide plugins: usage accounting and input limits.

Plugins see every agent and model call in the app, so this is the one place
to count cost and to stop a request before it spends anything.
"""

import logging
from datetime import date

from google.adk.agents.callback_context import CallbackContext
from google.adk.agents.invocation_context import InvocationContext
from google.adk.models import LlmResponse
from google.adk.plugins import BasePlugin
from google.genai import types

from .config import MAX_MESSAGE_CHARS, MAX_MODEL_CALLS_PER_USER_PER_DAY

logger = logging.getLogger(__name__)

USAGE_KEY = "usage"  # this session's totals
DAILY_USAGE_KEY = "user:usage_today"  # this user's totals for today, all sessions


def _add(totals: dict | None, agent: str, usage) -> dict:
    """Totals with one more model call added, overall and per agent."""
    totals = dict(totals or {})

    def bump(counts: dict) -> dict:
        counts = dict(counts)
        counts["model_calls"] = counts.get("model_calls", 0) + 1
        for name, value in (
            ("prompt_tokens", usage.prompt_token_count if usage else 0),
            ("output_tokens", usage.candidates_token_count if usage else 0),
            ("thinking_tokens", usage.thoughts_token_count if usage else 0),
        ):
            counts[name] = counts.get(name, 0) + (value or 0)
        return counts

    by_agent = dict(totals.get("by_agent") or {})
    by_agent[agent] = bump(by_agent.get(agent) or {})
    overall = bump({k: v for k, v in totals.items() if k != "by_agent"})
    return {**overall, "by_agent": by_agent}


class UsagePlugin(BasePlugin):
    """Counts model calls and tokens per agent, per session and per user/day."""

    def __init__(self, name: str = "usage"):
        super().__init__(name)
        self._runs: dict[str, dict] = {}  # this process's totals, per run

    async def after_model_callback(
        self, *, callback_context: CallbackContext, llm_response: LlmResponse
    ) -> None:
        if llm_response.partial:  # streamed chunk; the final response counts
            return
        agent = callback_context.agent_name
        usage = llm_response.usage_metadata
        state = callback_context.state
        # Written through the callback context, so the change is saved with
        # the model's response event (and persists with a database session).
        state[USAGE_KEY] = _add(state.get(USAGE_KEY), agent, usage)
        today = date.today().isoformat()
        daily = state.get(DAILY_USAGE_KEY) or {}
        if daily.get("date") != today:
            daily = {"date": today}
        state[DAILY_USAGE_KEY] = {"date": today, **_add(daily, agent, usage)}
        run = callback_context.invocation_id
        self._runs[run] = _add(self._runs.get(run), agent, usage)

    async def after_run_callback(
        self, *, invocation_context: InvocationContext
    ) -> None:
        run = self._runs.pop(invocation_context.invocation_id, None)
        if run:
            logger.info(
                "run %s: %d model calls, %d prompt / %d output / %d thinking "
                "tokens; by agent: %s",
                invocation_context.invocation_id,
                run["model_calls"],
                run["prompt_tokens"],
                run["output_tokens"],
                run["thinking_tokens"],
                {a: c["model_calls"] for a, c in run["by_agent"].items()},
            )


def _refusal(text: str) -> types.Content:
    return types.Content(role="model", parts=[types.Part(text=text)])


class LimitsPlugin(BasePlugin):
    """Refuses a request before any model call when it is over a limit."""

    def __init__(
        self,
        name: str = "limits",
        max_message_chars: int = MAX_MESSAGE_CHARS,
        max_calls_per_day: int = MAX_MODEL_CALLS_PER_USER_PER_DAY,
    ):
        super().__init__(name)
        self.max_message_chars = max_message_chars
        self.max_calls_per_day = max_calls_per_day

    async def before_run_callback(
        self, *, invocation_context: InvocationContext
    ) -> types.Content | None:
        content = invocation_context.user_content
        text = "".join(p.text or "" for p in (content.parts or [])) if content else ""
        if len(text) > self.max_message_chars:
            return _refusal(
                f"That message is {len(text):,} characters; the limit is "
                f"{self.max_message_chars:,}. Please send one module or a few "
                "functions at a time."
            )
        daily = invocation_context.session.state.get(DAILY_USAGE_KEY) or {}
        used = (
            daily.get("model_calls", 0)
            if daily.get("date") == date.today().isoformat()
            else 0
        )
        if used >= self.max_calls_per_day:
            return _refusal(
                f"You've reached today's limit of {self.max_calls_per_day} model "
                "calls. Please try again tomorrow."
            )
        return None
