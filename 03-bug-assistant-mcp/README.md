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

## Local database (Postgres)

The agent's internal tickets live in a Postgres `ticketsdb` database with one `tickets` table
(id, title, description, assignee, priority, status, timestamps) and 10 sample bugs. A trigger
keeps `updated_time` current on every update.

```bash
brew install postgresql@14           # if not installed
brew services start postgresql@14
# set DB_USER and DB_PASS in .env first
./deployment/db/setup_local.sh       # creates the role + database, loads deployment/db/init.sql
```

The script is safe to re-run; it resets the table to the 10 sample tickets. Verify with:

```bash
psql -h 127.0.0.1 -U postgres -d ticketsdb -c "SELECT ticket_id, title, priority, status FROM tickets;"
```

> Note: Homebrew's Postgres uses `trust` authentication for local connections, so any password
> is accepted from your own machine. Passwords are enforced on Cloud SQL.

## MCP Toolbox for Databases

[MCP Toolbox](https://github.com/googleapis/mcp-toolbox) is an MCP server that turns SQL
statements declared in YAML into tools an agent can call. The agent never sees credentials or
writes SQL; it only calls named tools.

| File | Purpose |
|---|---|
| `deployment/mcp-toolbox/tools.yaml` | 8 ticket tools + the `tickets_toolset` group, shared by all environments |
| `deployment/mcp-toolbox/local.yaml` | Local Postgres source + a full-text `search-tickets` |
| `deployment/mcp-toolbox/run_local.sh` | Downloads Toolbox v1.15.0 on first run, starts it with both files |

Tools in `tickets_toolset`: `search-tickets`, `get-ticket-by-id`, `get-tickets-by-assignee`,
`get-tickets-by-status`, `get-tickets-by-priority`, `get-tickets-by-date-range`,
`update-ticket-priority`, `update-ticket-status`, `create-new-ticket`.

Start it in its own terminal and leave it running:

```bash
./deployment/mcp-toolbox/run_local.sh
# ... INFO "Initialized 9 tools: ..."
# ... INFO "Server ready to serve!"
```

It serves MCP at `http://127.0.0.1:5000/mcp`. Locally, `search-tickets` uses Postgres full-text
search; the Cloud SQL deployment swaps in vector (embedding) search under the same tool name.

> macOS note: AirPlay Receiver also listens on port 5000, and `localhost` resolves to it (IPv6)
> and returns 403. Always use `127.0.0.1:5000`, which is what `MCP_TOOLBOX_URL` defaults to.

## Run

Run these from inside `03-bug-assistant-mcp/`. Running `adk web` from the repo root makes ADK
load the agent under the wrong app name and it returns no responses.

```bash
uv run adk web    # browser UI, pick "software_bug_assistant"
uv run adk run software_bug_assistant   # terminal chat
```

More sections (database, toolbox, deployment) are added as the agent is built.
