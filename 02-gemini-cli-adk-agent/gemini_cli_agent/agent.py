from google.adk.agents import Agent

from .config import MODEL
from .pipeline import test_generation_tool
from .prompt import ROOT_INSTRUCTION
from .tools import gemini_cli_tool, prepare_repository_tool, prepare_sample_project_tool

root_agent = Agent(
    model=MODEL,
    name="CodebaseAssistant",
    description="Analyzes GitHub codebases and generates tests using Gemini CLI.",
    instruction=ROOT_INSTRUCTION,
    tools=[
        prepare_repository_tool,
        prepare_sample_project_tool,
        gemini_cli_tool,
        test_generation_tool,
    ],
)
