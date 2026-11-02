from google.adk.agents import Agent

from .prompt import agent_instruction
from .tools.tools import (
    MODEL,
    get_current_date,
    github_tools,
    search_tool,
    stack_exchange_tool,
    toolbox_tools,
)

tools = [get_current_date, search_tool, stack_exchange_tool, toolbox_tools]
if github_tools is not None:
    tools.append(github_tools)

root_agent = Agent(
    model=MODEL,
    name="software_assistant",
    instruction=agent_instruction,
    tools=tools,
)
