"""Structured outputs passed between agents (enforced with output_schema)."""

from pydantic import BaseModel, Field


class IntentExtractionResult(BaseModel):
    country: str = Field(
        description="Target country, e.g. 'Thailand'. Empty if not given."
    )
    industry: str = Field(
        description="Target industry, e.g. 'Fintech'. Empty if not given."
    )
    intent: str = Field(
        description="One of: 'find_leads', 'research_patterns', 'unclear'."
    )
    confidence: float = Field(description="Confidence in the extraction, 0.0 to 1.0.")
    reasoning: str = Field(description="One sentence explaining the extraction.")


class Company(BaseModel):
    name: str = Field(description="Company name.")
    headquarters: str = Field(description="Country where the company is headquartered.")
    expansion_summary: str = Field(
        description="One sentence on how it entered the target country."
    )
    expansion_date: str = Field(
        description="When it entered, e.g. '2023-06' or '2023'."
    )


class CompanyFinderOutput(BaseModel):
    companies: list[Company]


class Signal(BaseModel):
    category: str = Field(
        description=(
            "One of: hiring, legal_entity, partnership, funding, regulatory, "
            "leadership, events, product_localization, other."
        )
    )
    description: str = Field(description="What happened, in one sentence.")
    approximate_date: str = Field(description="When it happened, e.g. '2022-11'.")
    source_number: int = Field(
        description="The [n] citation number next to this fact in the research, or 0."
    )


class CompanyResearch(BaseModel):
    company_name: str
    validated: bool = Field(
        description="True only if the company really expanded into the target country."
    )
    validation_notes: str = Field(description="One sentence explaining the validation.")
    expansion_date: str
    signals: list[Signal] = Field(
        description="Signals that appeared BEFORE the expansion, oldest first."
    )


class Lead(BaseModel):
    name: str = Field(description="Company name.")
    headquarters: str = Field(description="Country where the company is headquartered.")
    why_promising: str = Field(
        description="One sentence on the signals that make it a likely entrant."
    )


class LeadFinderOutput(BaseModel):
    leads: list[Lead]


class LeadAnalysis(BaseModel):
    company_name: str
    headquarters: str
    validated: bool = Field(
        description=(
            "True only if the company is real, in the target industry, and has "
            "NOT already launched in the target country."
        )
    )
    validation_notes: str = Field(description="One sentence explaining the validation.")
    fit_score: int = Field(
        description="0-100: how closely its current signals match the discovered patterns."
    )
    signals: list[Signal] = Field(
        description="Current signals that match the patterns, newest first."
    )
    missing_signals: list[str] = Field(
        description="Pattern signals not found yet for this company."
    )
    recommended_next_step: str = Field(
        description="One concrete outreach suggestion for a sales team."
    )
