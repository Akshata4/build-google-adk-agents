# Software Bug Assistant (ADK + MCP)

A conversational assistant that helps IT support and developers triage software bugs.
Built with Google ADK, it combines five tool types:

- Python function tool (current date)
- Built-in Google Search (via an `AgentTool` sub-agent)
- LangChain StackExchange tool (Stack Overflow)
- MCP Toolbox for Databases (Postgres `tickets` table)
- GitHub remote MCP server (issues and pull requests)

Reference: [Tools make an agent: from zero to assistant with ADK](https://cloud.google.com/blog/topics/developers-practitioners/tools-make-an-agent-from-zero-to-assistant-with-adk)

## Project layout

```
03-bug-assistant-mcp/
├── software_bug_assistant/   # ADK agent package (agent.py, prompt.py, tools/)
├── deployment/mcp-toolbox/   # tools.yaml for MCP Toolbox
├── .env.example
└── pyproject.toml
```

## Setup

Requires Python 3.11+ and [uv](https://docs.astral.sh/uv/).

```bash
cd 03-bug-assistant-mcp
uv sync
cp .env.example .env   # then fill in your keys
```

## Run

Run these from inside `03-bug-assistant-mcp/`. Running `adk web` from the repo root makes ADK
load the agent under the wrong app name and it returns no responses.

```bash
uv run adk web    # browser UI, pick "software_bug_assistant"
uv run adk run software_bug_assistant   # terminal chat
```

More sections (database, toolbox, deployment) are added as the agent is built.
