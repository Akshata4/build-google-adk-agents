"""Helpers shared by the research pipelines.

Per-item agents (one per company or lead) are built at run time, so their
instructions are InstructionProviders: plain Python functions that build the
prompt from state. ADK skips {placeholder} injection for these, so company
data containing braces can't break the prompt.
"""

import json
import warnings
from collections.abc import AsyncGenerator, Callable
from typing import Any

from google.adk.agents import BaseAgent, LlmAgent, ParallelAgent
from google.adk.agents.callback_context import CallbackContext
from google.adk.agents.invocation_context import InvocationContext
from google.adk.agents.readonly_context import ReadonlyContext
from google.adk.events import Event, EventActions
from google.adk.models import LlmResponse
from google.adk.tools import google_search
from google.genai import types
from pydantic import Field

from ..config import FAST_MODEL

# Sequential/ParallelAgent are deprecated in favour of Workflow in ADK 2.x, but
# Workflow can't yet run under an LlmAgent (here: via AgentTool), so keep them.
warnings.filterwarnings(
    "ignore", message=r".*(Sequential|Parallel)Agent is deprecated.*"
)


def as_dict(value: Any) -> dict:
    """State values written by output_schema agents may be dicts or JSON strings."""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return {}
    return value if isinstance(value, dict) else {}


def save_grounding_sources(sources_key: str):
    """after_model_callback that adds [n] citation markers to search results.

    Gemini's grounding metadata lists the pages it actually used (chunks) and
    which text segments each page supports. This stores the pages in state as
    a numbered list and inserts [n] after each supported segment, so later
    agents cite by number and never have to copy or invent a URL.
    """

    def callback(callback_context: CallbackContext, llm_response: LlmResponse):
        metadata = llm_response.grounding_metadata
        if not metadata or not metadata.grounding_chunks:
            return None
        sources = list(callback_context.state.get(sources_key) or [])
        urls = [s["url"] for s in sources]
        number_of_chunk = {}  # chunk index -> 1-based source number
        for index, chunk in enumerate(metadata.grounding_chunks):
            if not (chunk.web and chunk.web.uri):
                continue
            if chunk.web.uri not in urls:
                urls.append(chunk.web.uri)
                sources.append({"title": chunk.web.title or "", "url": chunk.web.uri})
            number_of_chunk[index] = urls.index(chunk.web.uri) + 1
        callback_context.state[sources_key] = sources

        parts = llm_response.content.parts if llm_response.content else None
        if not parts or not metadata.grounding_supports:
            return None
        # Insert from the end so earlier byte offsets stay valid.
        supports = sorted(
            metadata.grounding_supports,
            key=lambda s: s.segment.end_index or 0 if s.segment else 0,
            reverse=True,
        )
        for support in supports:
            segment = support.segment
            if not segment or segment.end_index is None:
                continue
            part = parts[segment.part_index or 0]
            if not part.text:
                continue
            numbers = sorted(
                {
                    number_of_chunk[i]
                    for i in support.grounding_chunk_indices or []
                    if i in number_of_chunk
                }
            )
            if not numbers:
                continue
            marker = "".join(f"[{n}]" for n in numbers).encode()
            data = part.text.encode()
            part.text = (
                data[: segment.end_index] + marker + data[segment.end_index :]
            ).decode(errors="ignore")
        return llm_response

    return callback


def resolve_sources(finding: dict, sources: list[dict]) -> dict:
    """Replace each signal's source_number with the real {title, url}."""
    for signal in finding.get("signals", []):
        n = signal.pop("source_number", 0) or 0
        signal["source"] = sources[n - 1] if 1 <= n <= len(sources) else None
    return finding


def make_validator(name: str, describe_item, output_key: str) -> LlmAgent:
    """Search-backed check that an item is real and matches the target.

    `describe_item(state)` returns the text describing what to validate.
    Reused by pattern discovery (past companies) and lead generation (leads).
    """

    def instruction(ctx: ReadonlyContext) -> str:
        state = ctx.state
        return f"""
        You are a fact checker. Target market: {state.get("industry")} in
        {state.get("country")}.

        Verify the following with Google Search:
        {describe_item(state)}

        Reply in at most 4 sentences: is the company real, does it operate in
        the target industry, and do the claims above hold? End with a line
        'VERDICT: VALID' or 'VERDICT: INVALID'.
        """

    return LlmAgent(
        model=FAST_MODEL,
        name=name,
        instruction=instruction,
        tools=[google_search],
        output_key=output_key,
    )


class ParallelResearchOrchestrator(BaseAgent):
    """Builds one research pipeline per item and runs them in parallel.

    The number of items (companies or leads) is only known after the
    formatter runs, so the ParallelAgent can't be declared up front.
    """

    get_items: Callable[[Any], list[dict]]
    build_pipeline: Callable[[int], BaseAgent]
    # Per-item state keys written by the pipelines (e.g. "sources_"). Cleared
    # first so a re-run never mixes in results or sources from an earlier run.
    reset_prefixes: list[str] = Field(default_factory=list)

    async def _run_async_impl(
        self, ctx: InvocationContext
    ) -> AsyncGenerator[Event, None]:
        stale = {
            key: None
            for key in ctx.session.state
            if any(key.startswith(p) for p in self.reset_prefixes)
        }
        if stale:
            yield Event(author=self.name, actions=EventActions(state_delta=stale))

        items = self.get_items(ctx.session.state)
        if not items:
            yield Event(
                author=self.name,
                content=types.Content(
                    role="model",
                    parts=[types.Part(text="Nothing was found to research.")],
                ),
            )
            return
        parallel = ParallelAgent(
            name=f"{self.name}Parallel",
            sub_agents=[self.build_pipeline(i) for i in range(len(items))],
        )
        async for event in parallel.run_async(ctx):
            yield event


class ResultCollector(BaseAgent):
    """Gathers the per-item results into one list (plain code, no LLM call).

    Keeps validated results only, swaps citation numbers for real links and
    optionally sorts, then writes the list to `output_key`.
    """

    get_items: Callable[[Any], list[dict]]
    result_prefix: str  # e.g. "research_findings_" -> research_findings_0, ...
    sources_prefix: str  # e.g. "sources_" -> sources_0, ...
    output_key: str
    sort_by: str | None = None

    async def _run_async_impl(
        self, ctx: InvocationContext
    ) -> AsyncGenerator[Event, None]:
        state = ctx.session.state
        results = []
        for i in range(len(self.get_items(state))):
            result = as_dict(state.get(f"{self.result_prefix}{i}"))
            if result.get("validated"):
                sources = state.get(f"{self.sources_prefix}{i}") or []
                results.append(resolve_sources(result, sources))
        if self.sort_by:
            results.sort(key=lambda r: r.get(self.sort_by) or 0, reverse=True)
        yield Event(
            author=self.name,
            actions=EventActions(state_delta={self.output_key: results}),
        )
