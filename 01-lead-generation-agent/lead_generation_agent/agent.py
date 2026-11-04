from google.adk.agents import Agent

from .config import ADVANCED_MODEL

# Placeholder root agent; the intent extractor and research pipelines are
# added in the next steps.
root_agent = Agent(
    model=ADVANCED_MODEL,
    name="InteractiveLeadGenerator",
    description="Finds sales leads by learning patterns from past successful companies.",
    instruction="""
    You are a lead generation assistant. Explain that you help find companies
    likely to expand into a target country and industry, by first studying
    companies that already did so and then finding companies showing the same
    signals. Ask the user for a target country and industry.
    """,
)
