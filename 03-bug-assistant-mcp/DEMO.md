# Demo and Video Walkthrough Script

A ~10 minute script for recording the code and execution walkthrough of the software bug
assistant.

## Before recording

1. Reset the sample data: `./deployment/db/setup_local.sh`
2. Make sure `.env` has `GOOGLE_API_KEY` and a real `GITHUB_PERSONAL_ACCESS_TOKEN`.
3. Quota check: the Gemini free tier allows 20 requests/day and 5/minute for `gemini-2.5-flash`.
   The six demo questions below use roughly 15-18 requests. Record on a fresh day (quota resets
   at midnight Pacific), or use a billed key. Wait about a minute between questions.
4. Open these files in your editor, in order: `prompt.py`, `tools/tools.py`, `agent.py`,
   `deployment/mcp-toolbox/tools.yaml`, `deployment/mcp-toolbox/local.yaml`,
   `deployment/db/init.sql`.
5. Increase terminal and browser font size.

## 1. Intro (1 min)

- What it is: a bug-triage assistant for IT support and developers.
- Show the architecture diagram in `README.md`.
- Key point: one ADK agent, five different kinds of tools, and MCP is how it reaches the
  ticket database and GitHub.

## 2. Code walkthrough (4 min)

**`deployment/db/init.sql`**: the `tickets` table, the `updated_time` trigger, the 10 sample
bugs.

**`deployment/mcp-toolbox/tools.yaml`**: pick `update-ticket-priority` and explain:
- `description` is what Gemini reads to decide when to use the tool
- `parameters` are what Gemini must fill in
- `statement` is the SQL, which the agent never sees; `$1`/`$2` are bound parameters, so no
  SQL injection
- the `tickets_toolset` groups all 9 tools

**`deployment/mcp-toolbox/local.yaml`**: the Postgres source (credentials come from
environment variables) and `search-tickets` using full-text search.

**`software_bug_assistant/tools/tools.py`**: walk through the five numbered sections:
1. `get_current_date`: any Python function with a docstring becomes a tool
2. Google Search: built-in search can't share an agent with other tools, so it lives in a
   sub-agent that the main agent calls like a tool
3. LangChain `StackExchangeTool`: ADK can reuse third-party tool ecosystems
4. `ToolboxToolset`: one line loads all 9 database tools over MCP
5. `MCPToolset`: connects to GitHub's hosted MCP server, limited to read-only tools

Also point out the `retry_options` on the model.

**`prompt.py`**: the process (understand → pick tools → validate → call → report) and rules
such as "check for duplicates before creating a ticket".

**`agent.py`**: model + instruction + tools = agent.

## 3. Start it (1 min)

Terminal 1:

```bash
./deployment/mcp-toolbox/run_local.sh
```

Point at `Initialized 9 tools` and `Server ready to serve!`.

Terminal 2:

```bash
uv run adk web
```

Open the URL and select `software_bug_assistant`.

## 4. Live demo (4 min)

After each answer, open the **Events** tab and click the tool call to show the arguments
Gemini chose and the raw result.

| # | Ask | What it shows |
|---|---|---|
| 1 | `Any issues around database timeouts?` | MCP Toolbox `search-tickets` → ticket 6 |
| 2 | `Show all open tickets assigned to frank.white@example.com` | assignee lookup, answer as a markdown table |
| 3 | `Find the ticket about PDF exports truncating text and bump its priority to P0` | multi-step: search → update. Then run `SELECT ticket_id, priority, updated_time FROM tickets WHERE ticket_id = 7;` in psql to prove the database changed |
| 4 | `Create a bug: the password reset email is never delivered to Outlook addresses. Assign it to maria.rodriguez@example.com` | duplicate check, then create; the agent picks the priority |
| 5 | `Are there any discussions on Stack Overflow about CVE-2024-3094?` | LangChain tool |
| 6 | `List the latest 5 open issues on the psf/requests GitHub repository` | remote GitHub MCP server |

If quota allows, add: `What are some possible root causes for the login page freezing issue? Search the web.` (Google Search sub-agent).

## 5. Wrap-up (30 s)

- Recap the five tool types.
- Show the "Changes from the reference sample" table in `README.md`.
- Mention the GitHub repo link.

## After recording

1. Upload to YouTube (Unlisted is fine if it only needs to be shared with graders).
2. Replace `_link coming soon_` in `README.md` with the video URL.
3. Reset the data: `./deployment/db/setup_local.sh`
