"""Phase 2: find companies showing the discovered patterns today.

LeadGenerationAgent (SequentialAgent)
1. LeadFinderAgent              google_search -> lead_finder_output
2. LeadFormatterAgent           output_schema -> leads_found_structured
3. LeadResearchOrchestrator     ParallelAgent built at run time, one per lead:
                                LeadValidator_i -> SignalAnalyzer_i -> LeadFormatter_i
                                -> lead_validation_i, lead_research_i, lead_analysis_i
4. ReportOrchestratorAgent      plain code -> all_lead_analyses (sorted by fit)
5. ReportCompilerAgent          advanced model -> final_lead_report
"""

import json

from google.adk.agents import LlmAgent, SequentialAgent
from google.adk.agents.readonly_context import ReadonlyContext
from google.adk.tools import google_search

from ..config import ADVANCED_MODEL, FAST_MODEL
from ..schemas import LeadAnalysis, LeadFinderOutput
from .common import (
    ParallelResearchOrchestrator,
    ResultCollector,
    as_dict,
    make_validator,
    save_grounding_sources,
)


def _leads(state) -> list[dict]:
    leads = as_dict(state.get("leads_found_structured")).get("leads", [])
    return leads[: int(state.get("m", 5))]


def _studied_companies(state) -> list[str]:
    companies = as_dict(state.get("companies_found_structured")).get("companies", [])
    return [c.get("name", "") for c in companies[: int(state.get("k", 3))]]


# ----- 1. Find candidate leads -----
def _finder_instruction(ctx: ReadonlyContext) -> str:
    s = ctx.state
    return f"""
    You are a sales intelligence analyst. Current date: {s.get("current_time")}.

    Below are the patterns that preceded successful entries into the
    {s.get("industry")} market in {s.get("country")}.

    Use Google Search to find {s.get("m", 5)} {s.get("industry")} companies
    headquartered OUTSIDE {s.get("country")} that are showing these signals
    NOW (in the last 12 months) but have NOT yet launched there. Exclude these already-studied
    companies: {", ".join(_studied_companies(s)) or "none"}.

    For each: name, headquarters country, and which signals you found.
    Only list companies you found in the search results.

    PATTERNS:
    {s.get("discovered_patterns", "")}
    """


lead_finder_agent = LlmAgent(
    model=ADVANCED_MODEL,
    name="LeadFinderAgent",
    instruction=_finder_instruction,
    tools=[google_search],
    output_key="lead_finder_output",
)


# ----- 2. Structure the list -----
def _lead_formatter_instruction(ctx: ReadonlyContext) -> str:
    return f"""
    Convert this research into the required JSON. Include at most
    {ctx.state.get("m", 5)} leads and only companies mentioned below.

    RESEARCH:
    {ctx.state.get("lead_finder_output", "")}
    """


lead_formatter_agent = LlmAgent(
    model=FAST_MODEL,
    name="LeadFormatterAgent",
    instruction=_lead_formatter_instruction,
    output_schema=LeadFinderOutput,
    output_key="leads_found_structured",
    include_contents="none",
)


# ----- 3. Analyze every lead in parallel -----
def _describe_lead(i: int):
    def describe(state) -> str:
        lead = _leads(state)[i]
        return (
            f"{lead['name']} (headquartered in {lead['headquarters']}): "
            f"{lead['why_promising']} Also check that it has NOT already "
            f"launched in {state.get('country')}; if it has, the verdict is INVALID."
        )

    return describe


def _signal_analyzer(i: int) -> LlmAgent:
    def instruction(ctx: ReadonlyContext) -> str:
        s = ctx.state
        lead = _leads(s)[i]
        return f"""
        You are an investigative sales researcher. Current date:
        {s.get("current_time")}.

        Use Google Search to check which of the patterns below
        {lead["name"]} is showing right now (last 12 months) regarding
        {s.get("country")}: hiring, local entity or licence, partnerships,
        funding, leadership hires, events, localized products.

        For each pattern say whether you found evidence, what it is and when.
        Say clearly when you could not find evidence; never guess.

        PATTERNS:
        {s.get("discovered_patterns", "")}
        """

    return LlmAgent(
        model=ADVANCED_MODEL,
        name=f"SignalAnalyzer_{i}",
        instruction=instruction,
        tools=[google_search],
        output_key=f"lead_research_{i}",
        after_model_callback=save_grounding_sources(f"lead_sources_{i}"),
    )


def _analysis_formatter(i: int) -> LlmAgent:
    def instruction(ctx: ReadonlyContext) -> str:
        s = ctx.state
        lead = _leads(s)[i]
        return f"""
        Structure the research on {lead["name"]} into the required JSON.

        - validated=false if the validation below says INVALID.
        - fit_score: start from 100 x (patterns with evidence / total
          patterns), then adjust by at most 10 points for how strong and
          recent the evidence is. No evidence means a score below 20.
        - One signal per distinct event: merge facts about the same event
          (e.g. an MoU and its announcement) into a single signal.
        - For each signal's source_number, use the [n] marker that follows
          the fact in the research, or 0 if it has no marker.

        VALIDATION:
        {s.get(f"lead_validation_{i}", "")}

        RESEARCH:
        {s.get(f"lead_research_{i}", "")}
        """

    return LlmAgent(
        model=FAST_MODEL,
        name=f"LeadAnalysisFormatter_{i}",
        instruction=instruction,
        output_schema=LeadAnalysis,
        output_key=f"lead_analysis_{i}",
        include_contents="none",
    )


def _lead_pipeline(i: int) -> SequentialAgent:
    return SequentialAgent(
        name=f"LeadResearch_{i}",
        sub_agents=[
            make_validator(
                f"LeadValidator_{i}", _describe_lead(i), f"lead_validation_{i}"
            ),
            _signal_analyzer(i),
            _analysis_formatter(i),
        ],
    )


lead_research_orchestrator_agent = ParallelResearchOrchestrator(
    name="LeadResearchOrchestratorAgent",
    description="Analyzes every candidate lead in parallel.",
    get_items=_leads,
    build_pipeline=_lead_pipeline,
    reset_prefixes=[
        "lead_validation_",
        "lead_research_",
        "lead_sources_",
        "lead_analysis_",
    ],
)


# ----- 4. Collect and rank (no LLM call) -----
report_orchestrator_agent = ResultCollector(
    name="ReportOrchestratorAgent",
    description="Collects the validated lead analyses, best fit first.",
    get_items=_leads,
    result_prefix="lead_analysis_",
    sources_prefix="lead_sources_",
    output_key="all_lead_analyses",
    sort_by="fit_score",
)


# ----- 5. Write the report -----
def _report_instruction(ctx: ReadonlyContext) -> str:
    s = ctx.state
    leads = s.get("all_lead_analyses") or []
    return f"""
    You are a senior sales strategist. Write the final lead report for
    {s.get("industry")} companies likely to enter {s.get("country")}.

    Use markdown:
    1. **Summary table**: rank, company, headquarters, fit score, strongest
       signal. Plain-text cells.
    2. **One section per lead**, in the given order: why it's a lead, the
       matched signals with dates and [source.title](source.url) links taken
       ONLY from the signals' source fields, the missing signals, and the
       recommended next step.
    3. **Method**: one short paragraph using only these facts: patterns were
       learned from {", ".join(_studied_companies(s)) or "past entrants"}
       entering {s.get("country")}; {len(_leads(s))} candidate lead(s) were
       then validated and analyzed in parallel with Google Search; {len(leads)}
       passed validation.

    If the list is empty, say no validated leads were found and suggest
    trying a larger number of leads or a broader industry.
    Never invent companies, signals or links.

    LEADS (JSON, best fit first):
    {json.dumps(leads, indent=1)}
    """


report_compiler_agent = LlmAgent(
    model=ADVANCED_MODEL,
    name="ReportCompilerAgent",
    instruction=_report_instruction,
    output_key="final_lead_report",
    include_contents="none",
)


lead_generation_agent = SequentialAgent(
    name="LeadGenerationAgent",
    description=(
        "Finds new companies showing the discovered patterns and writes a "
        "ranked lead report. Uses the patterns, target and m saved in state."
    ),
    sub_agents=[
        lead_finder_agent,
        lead_formatter_agent,
        lead_research_orchestrator_agent,
        report_orchestrator_agent,
        report_compiler_agent,
    ],
)
