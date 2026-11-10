# Build Google ADK Agents

| # | Agent | What it does | Video |
|---|---|---|---|
| a | [Lead Generation Deep Research Agent](01-lead-generation-agent/) | Studies companies that recently entered a market (e.g. fintech in Thailand), learns the signals they showed beforehand, then finds companies showing those signals now and writes a ranked lead report with real sources | [Watch on YouTube](https://www.youtube.com/watch?v=GGK-tBdYchU) |
| b | [Gemini CLI inside an ADK Pipeline](02-gemini-cli-adk-agent/) | Uses Gemini CLI as a tool to analyze any public GitHub repository, and generates unit tests in a write → pytest → fix loop that keeps tests exposing real bugs | [Watch on YouTube](https://www.youtube.com/watch?v=VWeCEH-ycGY) |
| c | [Software Bug Assistant (MCP)](03-bug-assistant-mcp/) | Triages software bugs: manages internal tickets in Postgres through MCP Toolbox, checks GitHub issues and PRs through GitHub's MCP server, and searches Stack Overflow and Google for fixes | [Watch on YouTube](https://www.youtube.com/watch?v=erAqDVXMOTQ) |
| d | [Code Review Assistant](04-code-review-assistant/) | Reviews Python code with unit tests executed and verified in Gemini's sandbox, then fixes it and re-runs the same tests until they pass; production features: usage tracking, limits, SQLite sessions, `adk eval` | [Watch on YouTube](https://www.youtube.com/watch?v=PFH9yRJJxeg) |
| e | [Sports Store Agent (e-commerce)](05-ecommerce-agent/) | A shopping assistant on Postgres through MCP Toolbox: product search, stock per store, cart and orders, with customer identity bound in code, atomic orders and confirmation for order changes | [Watch on YouTube](https://www.youtube.com/watch?v=jFmRu_yC_vs) |

End-to-end implementations of five agents built with
[Google Agent Development Kit (ADK)](https://google.github.io/adk-docs/), each based on a
published reference and rebuilt to run locally, with the issues found in the reference
fixed. Every agent folder has its own README (architecture diagrams, setup, run
instructions, tests, changes from the reference) and a `DEMO.md` walkthrough script.

## ADK features by agent

| Agent | Key ADK features | External tools | Tests |
|---|---|---|---|
| a | `SequentialAgent`, `ParallelAgent`, custom `BaseAgent` orchestrator, `AgentTool`, `output_schema`, callbacks, grounded citations | Google Search | 11 offline |
| b | function tools with confirmation, `AgentTool`, `SequentialAgent`, `LoopAgent`, code-only steps | Gemini CLI, git, pytest | 42 offline |
| c | function, built-in, LangChain and MCP tools in one agent; `GoogleSearchAgentTool` | MCP Toolbox + Postgres, GitHub MCP server, StackExchange | manual, live |
| d | agent transfer, custom agents, `BuiltInCodeExecutor`, `App` + plugins, artifacts, `user:` state, SQLite sessions, `adk eval` with custom metrics | Gemini code execution, pycodestyle | 68 offline |
| e | `ToolboxToolset`, `FunctionTool` with confirmation, identity from `ToolContext` | MCP Toolbox + Postgres | 22 against a real database |

## Common prerequisites

- Python 3.11+ and [uv](https://docs.astral.sh/uv/)
- A Gemini API key from [Google AI Studio](https://aistudio.google.com/apikey), in each
  agent's `.env` (copy `.env.example`). The free tier allows 20 requests a day per model;
  each README says how many calls a run makes.
- For (c) and (e): Postgres 14+ (e.g. `brew install postgresql@14`). Each agent's
  `run_local.sh` downloads MCP Toolbox on first run.
- For (b): Node.js 20+ and Gemini CLI.

Each agent is a separate uv project:

```bash
cd 0X-<agent-folder>
uv sync
cp .env.example .env
uv run adk web
```

## Repository layout

```
01-lead-generation-agent/   (a) deep research, lead generation
02-gemini-cli-adk-agent/    (b) Gemini CLI as a tool
03-bug-assistant-mcp/       (c) MCP tools, bug triage
04-code-review-assistant/   (d) code review and fix
05-ecommerce-agent/         (e) sports store, e-commerce
```

Secrets (`.env`), downloaded binaries, session databases and cloned workspaces are git-ignored.
