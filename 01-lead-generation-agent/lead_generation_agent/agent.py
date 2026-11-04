from google.adk.agents import Agent
from google.adk.tools.agent_tool import AgentTool
from google.adk.tools.get_user_choice_tool import get_user_choice_tool

from .callbacks import after_tool_run, before_agent_run
from .config import ADVANCED_MODEL
from .prompt import ROOT_INSTRUCTION
from .sub_agents.intent_extractor import intent_extractor_agent
from .tools import set_company_count

root_agent = Agent(
    model=ADVANCED_MODEL,
    name="InteractiveLeadGenerator",
    description="Finds sales leads by learning patterns from past successful companies.",
    instruction=ROOT_INSTRUCTION,
    tools=[
        AgentTool(intent_extractor_agent),
        get_user_choice_tool,
        set_company_count,
    ],
    before_agent_callback=before_agent_run,
    after_tool_callback=after_tool_run,
)
