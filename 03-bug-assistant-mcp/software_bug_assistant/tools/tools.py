"""The five tool types used by the software bug assistant.

1. Function tool      - get_current_date
2. Built-in tool      - Google Search, wrapped in a sub-agent (search_tool)
3. Third-party tool   - LangChain StackExchange (stack_exchange_tool)
4. MCP Toolbox        - SQL ticket tools served by MCP Toolbox (toolbox_tools)
5. Remote MCP server  - GitHub's hosted MCP server (github_tools)
"""

import logging
import os
from datetime import datetime

from dotenv import load_dotenv
from google.adk.agents import Agent
from google.adk.models import Gemini
from google.adk.tools import google_search
from google.adk.tools.google_search_agent_tool import GoogleSearchAgentTool
from google.adk.tools.langchain_tool import LangchainTool
from google.adk.tools.mcp_tool import MCPToolset, StreamableHTTPConnectionParams
from google.adk.tools.toolbox_toolset import ToolboxToolset
from google.genai import types
from langchain_community.tools import StackExchangeTool
from langchain_community.utilities import StackExchangeAPIWrapper

load_dotenv()
logger = logging.getLogger(__name__)

# Retry rate limits (429) and transient server errors (5xx) with exponential
# backoff; the Gemini free tier hits both regularly.
MODEL = Gemini(
    model=os.getenv("GEMINI_MODEL", "gemini-2.5-flash"),
    retry_options=types.HttpRetryOptions(
        attempts=5,
        initial_delay=2,
        max_delay=30,
        exp_base=2,
        http_status_codes=[429, 500, 503, 504],
    ),
)


# ----- 1. Function tool -----
def get_current_date() -> dict:
    """Get today's date in the format YYYY-MM-DD.

    Use this to resolve relative dates such as "last week" or "this month".
    """
    return {"current_date": datetime.now().strftime("%Y-%m-%d")}


# ----- 2. Built-in tool (Google Search) -----
# Built-in search can't be combined with other tools in the same agent, so it
# runs inside its own sub-agent that the root agent calls like a tool.
search_agent = Agent(
    model=MODEL,
    name="search_agent",
    description="Searches the web with Google Search and summarizes the results.",
    instruction="""
    You're a specialist in Google Search. Answer the query using search results
    and cite the sources you used.
    """,
    tools=[google_search],
)
search_tool = GoogleSearchAgentTool(search_agent)


# ----- 3. Third-party tool (LangChain StackExchange) -----
stack_exchange_tool = LangchainTool(
    StackExchangeTool(api_wrapper=StackExchangeAPIWrapper())
)


# ----- 4. MCP Toolbox for Databases -----
# Connects lazily when the agent runs, so the agent still starts if Toolbox is
# down; ticket tools are then unavailable until Toolbox is running.
TOOLBOX_URL = os.getenv("MCP_TOOLBOX_URL", "http://127.0.0.1:5000")
toolbox_tools = ToolboxToolset(TOOLBOX_URL, toolset_name="tickets_toolset")


# ----- 5. Remote MCP server (GitHub) -----
def _github_tools() -> MCPToolset | None:
    token = os.getenv("GITHUB_PERSONAL_ACCESS_TOKEN", "")
    if not token or token.startswith("<"):
        logger.warning(
            "GITHUB_PERSONAL_ACCESS_TOKEN is not set in .env; GitHub tools are disabled."
        )
        return None
    return MCPToolset(
        connection_params=StreamableHTTPConnectionParams(
            url="https://api.githubcopilot.com/mcp/",
            headers={"Authorization": f"Bearer {token}"},
        ),
        # Read-only tools only: the agent can look at GitHub but never change it.
        tool_filter=[
            "search_repositories",
            "search_issues",
            "list_issues",
            "get_issue",
            "list_pull_requests",
            "get_pull_request",
        ],
    )


github_tools = _github_tools()
