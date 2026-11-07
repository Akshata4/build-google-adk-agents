from google.adk.agents import Agent

from .config import WORKER_MODEL

# Placeholder root agent; the review and fix pipelines are added next.
root_agent = Agent(
    model=WORKER_MODEL,
    name="CodeReviewAssistant",
    description="Reviews Python code and fixes the issues it finds.",
    instruction="""
    You are a Python code review assistant. Explain that you can review
    Python code (structure, PEP 8 style, tests run in a sandbox) and fix the
    issues you find. Ask the user to paste the code they want reviewed.
    """,
)
