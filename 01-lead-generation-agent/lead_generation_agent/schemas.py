"""Structured outputs passed between agents (enforced with output_schema)."""

from pydantic import BaseModel, Field


class IntentExtractionResult(BaseModel):
    country: str = Field(description="Target country, e.g. 'Thailand'. Empty if not given.")
    industry: str = Field(description="Target industry, e.g. 'Fintech'. Empty if not given.")
    intent: str = Field(
        description="One of: 'find_leads', 'research_patterns', 'unclear'."
    )
    confidence: float = Field(description="Confidence in the extraction, 0.0 to 1.0.")
    reasoning: str = Field(description="One sentence explaining the extraction.")
