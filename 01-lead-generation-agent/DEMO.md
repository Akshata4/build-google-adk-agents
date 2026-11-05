# Demo and Video Walkthrough Script

A ~12 minute script for recording the code and execution walkthrough of the lead generation
deep research agent.

## Before recording

1. **Billing:** a full demo (k=3, m=3) is about 25-30 model calls. Use a billed key. On the free
   tier the run stops partway with `429 RESOURCE_EXHAUSTED`.
2. In `.env`, remove `GEN_ADVANCED_MODEL=gemini-2.5-flash` if you added it, so the advanced
   steps use `gemini-2.5-pro`. Flash for both also works and is faster.
3. Do one dry run off-camera. A full run takes a few minutes; you can cut the waiting in
   editing.
4. Open these files in your editor, in order: `agent.py`, `prompt.py`, `callbacks.py`,
   `tools.py`, `schemas.py`, `sub_agents/intent_extractor.py`,
   `sub_agents/pattern_discovery.py`, `sub_agents/common.py`, `sub_agents/lead_generation.py`.
5. Increase terminal and browser font size.

## 1. Intro (1 min)

- The problem: lead lists are guesses. This agent learns what successful market entrants did
  first, then looks for companies doing those things now.
- Show the architecture diagram in `README.md`: root agent, two pipelines, parallel branches.

## 2. Code walkthrough (5 min)

**`agent.py`**: the root agent has six tools: the intent extractor and both pipelines are
agents wrapped as `AgentTool`; `get_user_choice` is built in; the two count tools are plain
functions. Point out the two callbacks.

**`prompt.py` + `callbacks.py`**: the instruction reads `{stage}`, `{country}`, `{k}` from
state. Show how `after_tool_run` advances the stage, and explain the reference bug (it compared
the `AgentTool` wrapper with the agent, so the stage never moved).

**`tools.py`**: state writes through `ToolContext`, and the stage guards (no patterns → no
lead count).

**`schemas.py`**: Pydantic models used as `output_schema`.

**`sub_agents/pattern_discovery.py`**: read the module docstring (the five steps), then:
- finder (search) + formatter (schema): why they must be two agents
- `_signal_searcher(i)`: an InstructionProvider; a function builds the prompt for company i
- `_company_pipeline(i)`: Validator → SignalSearcher → SignalFormatter

**`sub_agents/common.py`**:
- `ParallelResearchOrchestrator`: a custom `BaseAgent` that builds the `ParallelAgent` at run
  time, after it knows how many companies were found; clears stale keys first
- `ResultCollector`: plain code, no LLM: keep validated, resolve links, rank
- `save_grounding_sources`: grounding metadata → `[n]` markers → real links

**`sub_agents/lead_generation.py`**: same shape; reuses the validator, adds `fit_score` and the
report compiler.

## 3. Live demo (5 min)

Run `uv run pytest` first (11 passed, no API calls), then:

```bash
uv run adk web
```

Select `lead_generation_agent`. Keep the **State** tab visible.

| # | Say | Show |
|---|---|---|
| 1 | `hi` | No tools called; it asks for a target |
| 2 | `I want leads for fintech companies` | Extractor finds the industry but no country; agent asks for it. State: `industry` set, `stage=intent` |
| 3 | `Thailand` | `stage=choose_k`, clickable options |
| 4 | `3` | `set_company_count`, then `PatternDiscoveryAgent`. In **Events**, open the tool call. In **State**, show `companies_found_structured`, `signal_research_0` with `[1][2]` markers, `sources_0`, `research_findings_0` |
| 5 | `Yes, find leads` | asks for m |
| 6 | `3` | `LeadGenerationAgent`. Show `lead_validation_i` (any rejected lead shows `VERDICT: INVALID`), `all_lead_analyses` sorted by `fit_score`, then the final report with links. Click one link |

## 4. Wrap-up (1 min)

- Recap: SequentialAgent for fixed steps, a custom BaseAgent + ParallelAgent for dynamic
  fan-out, AgentTool to nest pipelines, state as the shared memory, callbacks for control.
- Show the "Changes from the reference" table in `README.md`.
- Mention the GitHub repo link.

## After recording

1. Upload to YouTube (Unlisted is fine if it only needs to be shared with graders).
2. Replace `_link coming soon_` in `README.md` with the video URL.
