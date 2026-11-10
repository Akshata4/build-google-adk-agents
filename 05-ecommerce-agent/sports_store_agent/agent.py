from google.adk.agents import Agent
from google.adk.tools.toolbox_toolset import ToolboxToolset

from .config import MODEL, TOOLBOX_URL
from .prompt import INSTRUCTION
from .tools import customer_tools

# Catalog tools (products, stock, stores, delivery options) are public data:
# the model calls the Toolbox tools directly. Customer tools are Python
# wrappers that bind the customer from the session (see tools.py).
catalog_tools = ToolboxToolset(TOOLBOX_URL, toolset_name="catalog_toolset")

root_agent = Agent(
    model=MODEL,
    name="Finn",
    description="Shopping assistant for a sports store: products, cart and orders.",
    instruction=INSTRUCTION,
    tools=[catalog_tools, *customer_tools],
)
