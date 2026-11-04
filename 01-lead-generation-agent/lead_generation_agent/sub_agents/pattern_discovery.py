"""Phase 1: learn the signals that preceded past successful market entries.

PatternDiscoveryAgent (SequentialAgent)
1. CompanyFinderAgent        google_search -> company_finder_output
2. CompanyFormatterAgent     output_schema -> companies_found_structured
3. ResearchOrchestratorAgent ParallelAgent built at run time, one branch per company:
                             Validator_i -> SignalSearcher_i -> SignalFormatter_i
                             -> validation_result_i, signal_research_i, research_findings_i
4. SynthesizerOrchestrator   plain code -> all_research_findings
5. PatternSynthesizerAgent   advanced model -> discovered_patterns
"""

import json
import warnings
from collections.abc import AsyncGenerator

from google.adk.agents import BaseAgent, LlmAgent, ParallelAgent, SequentialAgent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.agents.readonly_context import ReadonlyContext
from google.adk.events import Event, EventActions
from google.adk.tools import google_search
from google.genai import types

from ..config import ADVANCED_MODEL, FAST_MODEL
from ..schemas import CompanyFinderOutput, CompanyResearch
from .common import as_dict, make_validator, resolve_sources, save_grounding_sources

# Sequential/ParallelAgent are deprecated in favour of Workflow in ADK 2.x, but
# Workflow can't yet run under an LlmAgent (here: via AgentTool), so keep them.
warnings.filterwarnings(
    "ignore", message=r".*(Sequential|Parallel)Agent is deprecated.*"
)


def _companies(state) -> list[dict]:
    companies = as_dict(state.get("companies_found_structured")).get("companies", [])
    return companies[: int(state.get("k", 3))]


# ----- 1. Find past successful companies -----
def _finder_instruction(ctx: ReadonlyContext) -> str:
    s = ctx.state
    return f"""
    You are a market research analyst. Current date: {s.get("current_time")}.

    Use Google Search to find {s.get("k", 3)} foreign companies in the
    {s.get("industry")} industry that successfully entered {s.get("country")}
    in {int(str(s.get("current_time"))[:4]) - 3} or later (opened an office, launched a product, got a licence,
    acquired a local company, or similar).

    Prefer well-documented entries with news coverage. For each company give:
    name, headquarters country, how it entered, and when (year and month if
    known). Only list companies you found in the search results.
    """


company_finder_agent = LlmAgent(
    model=ADVANCED_MODEL,
    name="CompanyFinderAgent",
    instruction=_finder_instruction,
    tools=[google_search],
    output_key="company_finder_output",
    after_model_callback=save_grounding_sources("company_finder_sources"),
)


# ----- 2. Structure the list -----
def _company_formatter_instruction(ctx: ReadonlyContext) -> str:
    return f"""
    Convert this research into the required JSON. Include at most
    {ctx.state.get("k", 3)} companies and only companies mentioned below.

    RESEARCH:
    {ctx.state.get("company_finder_output", "")}
    """


company_formatter_agent = LlmAgent(
    model=FAST_MODEL,
    name="CompanyFormatterAgent",
    instruction=_company_formatter_instruction,
    output_schema=CompanyFinderOutput,
    output_key="companies_found_structured",
    include_contents="none",
)


# ----- 3. Research every company in parallel -----
def _describe_company(i: int):
    def describe(state) -> str:
        c = _companies(state)[i]
        return (
            f"{c['name']} (headquartered in {c['headquarters']}) entered "
            f"{state.get('country')} around {c['expansion_date']}: "
            f"{c['expansion_summary']}"
        )

    return describe


def _signal_searcher(i: int) -> LlmAgent:
    def instruction(ctx: ReadonlyContext) -> str:
        s = ctx.state
        c = _companies(s)[i]
        return f"""
        You are an investigative market researcher. {c["name"]} entered
        {s.get("country")} around {c["expansion_date"]}.

        Use Google Search to find the signals that appeared in the 6-24 months
        BEFORE that entry, for example:
        - hiring for roles based in {s.get("country")} or a country manager
        - registering a local legal entity or applying for a licence
        - partnerships with local companies, banks or government bodies
        - funding rounds that mentioned expansion into {s.get("country")} or its region
        - executives speaking at local events, or localized products/pages

        List each signal with what happened and roughly when. Say clearly
        when you could not find evidence; never guess.
        """

    return LlmAgent(
        model=ADVANCED_MODEL,
        name=f"SignalSearcher_{i}",
        instruction=instruction,
        tools=[google_search],
        output_key=f"signal_research_{i}",
        after_model_callback=save_grounding_sources(f"sources_{i}"),
    )


def _signal_formatter(i: int) -> LlmAgent:
    def instruction(ctx: ReadonlyContext) -> str:
        s = ctx.state
        c = _companies(s)[i]
        return f"""
        Structure the research on {c["name"]} into the required JSON.

        Set validated=false if the validation below says INVALID.
        For source_number, use the [n] marker that follows the fact in the
        research, or 0 if the fact has no marker.

        VALIDATION:
        {s.get(f"validation_result_{i}", "")}

        RESEARCH:
        {s.get(f"signal_research_{i}", "")}

        """

    return LlmAgent(
        model=FAST_MODEL,
        name=f"SignalFormatter_{i}",
        instruction=instruction,
        output_schema=CompanyResearch,
        output_key=f"research_findings_{i}",
        include_contents="none",
    )


class ResearchOrchestratorAgent(BaseAgent):
    """Builds one research pipeline per company and runs them in parallel.

    The number of companies is only known after the formatter runs, so the
    ParallelAgent can't be declared up front.
    """

    async def _run_async_impl(
        self, ctx: InvocationContext
    ) -> AsyncGenerator[Event, None]:
        companies = _companies(ctx.session.state)
        if not companies:
            yield Event(
                author=self.name,
                content=types.Content(
                    role="model",
                    parts=[types.Part(text="No companies were found to research.")],
                ),
            )
            return

        pipelines = [
            SequentialAgent(
                name=f"CompanyResearch_{i}",
                sub_agents=[
                    make_validator(
                        f"Validator_{i}", _describe_company(i), f"validation_result_{i}"
                    ),
                    _signal_searcher(i),
                    _signal_formatter(i),
                ],
            )
            for i in range(len(companies))
        ]
        parallel = ParallelAgent(name="ParallelCompanyResearch", sub_agents=pipelines)
        async for event in parallel.run_async(ctx):
            yield event


research_orchestrator_agent = ResearchOrchestratorAgent(
    name="ResearchOrchestratorAgent",
    description="Researches every found company in parallel.",
)


# ----- 4. Collect the parallel results (no LLM call) -----
class SynthesizerOrchestratorAgent(BaseAgent):
    """Gathers research_findings_i into one list for the synthesizer."""

    async def _run_async_impl(
        self, ctx: InvocationContext
    ) -> AsyncGenerator[Event, None]:
        state = ctx.session.state
        findings = []
        for i in range(len(_companies(state))):
            result = as_dict(state.get(f"research_findings_{i}"))
            if result:
                findings.append(
                    resolve_sources(result, state.get(f"sources_{i}") or [])
                )
        validated = [f for f in findings if f.get("validated")]
        yield Event(
            author=self.name,
            actions=EventActions(state_delta={"all_research_findings": validated}),
        )


synthesizer_orchestrator_agent = SynthesizerOrchestratorAgent(
    name="SynthesizerOrchestratorAgent",
    description="Collects the per-company research results.",
)


# ----- 5. Find the shared patterns -----
def _pattern_instruction(ctx: ReadonlyContext) -> str:
    s = ctx.state
    findings = s.get("all_research_findings") or []
    return f"""
    You are a senior market strategist. Below is validated research on
    {len(findings)} companies that successfully entered the
    {s.get("industry")} market in {s.get("country")}, with the signals that
    appeared before each entry.

    Write a concise markdown report:
    1. **Companies studied**: one line each with the entry date.
    2. **Patterns**: 3-6 recurring pre-entry signals. For each: what it is,
       how many of the companies showed it, typical lead time before entry,
       and supporting evidence as markdown links [source.title](source.url)
       taken ONLY from the signals' source fields below.
    3. **What to look for in new leads**: a short checklist derived from the
       patterns.

    If no companies were validated, say so and suggest refining the target.
    Never invent companies, signals or links.

    RESEARCH (JSON):
    {json.dumps(findings, indent=1)}
    """


pattern_synthesizer_agent = LlmAgent(
    model=ADVANCED_MODEL,
    name="PatternSynthesizerAgent",
    instruction=_pattern_instruction,
    output_key="discovered_patterns",
    include_contents="none",
)


pattern_discovery_agent = SequentialAgent(
    name="PatternDiscoveryAgent",
    description=(
        "Researches past companies that successfully entered the target "
        "country and industry and discovers the signals they shared. Uses the "
        "country, industry and k already saved in state."
    ),
    sub_agents=[
        company_finder_agent,
        company_formatter_agent,
        research_orchestrator_agent,
        synthesizer_orchestrator_agent,
        pattern_synthesizer_agent,
    ],
)
