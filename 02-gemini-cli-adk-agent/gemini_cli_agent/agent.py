from google.adk.agents import Agent

from .config import MODEL

# Placeholder root agent; the Gemini CLI tools and the test generation
# pipeline are added in the next steps.
root_agent = Agent(
    model=MODEL,
    name="CodebaseAssistant",
    description="Analyzes GitHub codebases and generates tests using Gemini CLI.",
    instruction="""
    You are a codebase assistant. Explain that you can analyze a GitHub
    repository (architecture, code quality, test plans) and generate working
    unit tests for it, using Gemini CLI as your tool. Ask the user for a
    GitHub repository URL and what they want to know.
    """,
)
