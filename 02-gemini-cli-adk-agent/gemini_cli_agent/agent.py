from google.adk.agents import Agent

from .config import MODEL
from .prompt import ROOT_INSTRUCTION
from .tools import gemini_cli_tool, prepare_repository_tool

# The test generation pipeline is added in the next step.
root_agent = Agent(
    model=MODEL,
    name="CodebaseAssistant",
    description="Analyzes GitHub codebases using Gemini CLI as a tool.",
    instruction=ROOT_INSTRUCTION,
    tools=[prepare_repository_tool, gemini_cli_tool],
)
