from google.adk.agents import Agent
from google.adk.apps import App

from .config import WORKER_MODEL
from .plugins import LimitsPlugin, UsagePlugin
from .prompt import ROOT_INSTRUCTION
from .sub_agents.fix_pipeline import code_fix_pipeline
from .sub_agents.review_pipeline import code_review_pipeline

root_agent = Agent(
    model=WORKER_MODEL,
    name="CodeReviewAssistant",
    description="Reviews Python code and fixes the issues it finds.",
    instruction=ROOT_INSTRUCTION,
    # Agent transfer: the pipelines answer the user directly, so the root
    # never repeats or rewrites the review (and costs one model call).
    sub_agents=[code_review_pipeline, code_fix_pipeline],
)

# adk web / adk run / adk eval load `app` when it exists, so the plugins
# apply to every way of running the agent.
app = App(
    name="code_review_assistant",
    root_agent=root_agent,
    plugins=[LimitsPlugin(), UsagePlugin()],
)
