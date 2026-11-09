from google.adk.agents import Agent

from .config import WORKER_MODEL
from .prompt import ROOT_INSTRUCTION
from .sub_agents.review_pipeline import code_review_pipeline

root_agent = Agent(
    model=WORKER_MODEL,
    name="CodeReviewAssistant",
    description="Reviews Python code and fixes the issues it finds.",
    instruction=ROOT_INSTRUCTION,
    # Agent transfer: the pipeline answers the user directly, so the root
    # never repeats or rewrites the review (and costs one model call).
    sub_agents=[code_review_pipeline],
)
