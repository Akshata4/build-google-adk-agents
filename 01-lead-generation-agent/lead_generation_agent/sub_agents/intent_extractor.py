from google.adk.agents import LlmAgent

from ..config import FAST_MODEL
from ..schemas import IntentExtractionResult

intent_extractor_agent = LlmAgent(
    model=FAST_MODEL,
    name="intent_extractor_agent",
    description=(
        "Extracts the target country, industry and intent from the user's "
        "lead generation request. Pass the user's words as the request."
    ),
    instruction="""
    Extract the lead generation target from the user's request.

    - country: the country the companies should be expanding INTO. Use the
      common English country name. Leave empty if none is given.
    - industry: the industry or sector, in a short form such as 'Fintech',
      'EV charging', 'Healthcare SaaS'. Leave empty if none is given.
    - intent: 'find_leads' if they want companies to target,
      'research_patterns' if they only want to learn what successful
      companies did, otherwise 'unclear'.
    - confidence: lower it when the country or industry is missing or vague.

    Do not guess a country or industry the user did not mention.
    """,
    output_schema=IntentExtractionResult,
    output_key="intent_extraction_result",
)
