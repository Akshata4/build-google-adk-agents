# Demo and Video Walkthrough Script

A ~12 minute script for recording the code and execution walkthrough of the Gemini CLI + ADK
agent.

## Before recording

1. Check the CLI works headless (one cheap call):
   ```bash
   gemini --model=gemini-3.1-flash-lite --output-format=json --skip-trust --prompt="Say OK"
   ```
   On error code 41, set `GEMINI_API_KEY` in `.env`. On 503s, pick another model from
   `gemini --list-models` and set `GEMINI_CLI_MODEL`.
2. Quota: the demo uses about 8 ADK requests (`gemini-2.5-flash`, free tier 20/day) and 5-7
   CLI calls. Record on a fresh day or with a billed key.
3. Start clean: `rm -rf workspace` (the psf/requests clone is about 8 MB and takes a few
   seconds).
4. Open these files in your editor, in order: `tools.py`, `policies/read_only.toml`,
   `pipeline.py`, `agent.py`, `prompt.py`, `sample_project/shopping_cart/cart.py`.
5. Increase terminal and browser font size.

## 1. Intro (1 min)

- What it is: an ADK agent that uses Gemini CLI, a full coding agent, as one of its tools.
- Why: ADK owns the conversation and the workflow; Gemini CLI brings repository-wide reading,
  searching and editing that would take many custom tools to build.
- Show the architecture diagram in `README.md`: the root agent, the `gemini_cli` tool and the
  test generation pipeline.

## 2. Code walkthrough (5 min)

**`tools.py`**
- `prepare_repository`: the URL regex, shallow clone, `owner__repo` folders, argument lists
  instead of a shell. Point at the reference's `f'gemini -p "{task}"'` with `shell=True`
  (README "Changes from the reference") and why a quote in the task breaks it.
- `build_cli_args`: headless flags. `--output-format=json`, `--skip-trust`, and the two modes:
  - analyze: `--approval-mode=plan` **plus** the policy file
  - edit: `--approval-mode=auto_edit`
- `remove_cli_config`: why the clone's `.gemini/` and `.env` are deleted.
- `run_gemini_cli`: timeout, JSON from stdout or stderr, the code 41 login hint, truncation.
- Bottom of the file: `FunctionTool(gemini_cli, require_confirmation=_needs_confirmation)`,
  approval only for edit mode.

**`policies/read_only.toml`**: the security finding. In headless mode, plan mode approved its
own `exit_plan_mode` and then wrote files. The policy denies the write and shell tools, and
the CLI hides denied tools from the model.

**`pipeline.py`**: read the docstring diagram at the top, then:
- `GeminiCliStep`: a custom `BaseAgent` that builds a task from state and calls the CLI, no
  ADK model call
- `_write_task`: round 1 uses the plan; later rounds get the pytest output; the xfail rule
- `revert_outside_changes`: git keeps the writer inside `generated_tests/`
- `run_pytest`: repo conftest and config ignored, repo and `src/` on `PYTHONPATH`
- `TestRunner`: `escalate=True` ends the `LoopAgent`
- `make_pipeline`: `SequentialAgent` → `LoopAgent(max_iterations=3)` → `TestReport`
- `ConfirmedAgentTool`: approval before every run

**`agent.py`** and **`prompt.py`**: four tools; the prompt turns user questions into one
specific CLI task, and sends test requests to the pipeline.

## 3. Start it (30 s)

```bash
uv run adk web
```

Open the URL and select `gemini_cli_agent`.

## 4. Live demo (5 min)

After each answer, open the **Events** tab: click the `gemini_cli` call to show the task the
ADK model wrote for the CLI, and its result (`response`, `cli_tools_used`).

| # | Ask | What it shows |
|---|---|---|
| 1 | `Explain the architecture of https://github.com/psf/requests` | clone, then one read-only CLI task; modules, request flow and patterns with file paths |
| 2 | `What are the riskiest parts of sessions.py? Give file:line references.` | the same clone is reused; a follow-up analysis |
| 3 | `Use the sample project and generate unit tests for the Cart class and the pricing functions` | the approve/reject prompt, then the pipeline. Usually round 1 has a failure and round 2 passes with an xfail for the `remove()` negative quantity bug |

For #3:
- Show the **State** tab: `test_plan`, `test_runs` (one entry per round, with the pytest
  summary), `possible_bugs`.
- In a terminal, prove the tests are real:
  ```bash
  cd workspace/sample__shopping_cart
  ../../.venv/bin/python -m pytest generated_tests -q -p no:cacheprovider
  git status        # only generated_tests/ is new
  ```
- Open `sample_project/shopping_cart/cart.py` → `remove()`: `quantity >= current` is false
  for -1, so `current - (-1)` adds an item. The pipeline found a real bug.

Optional, if quota allows: `Reject` the approval prompt once to show nothing runs.

## 5. Wrap-up (30 s)

- Recap: Gemini CLI as a tool, a deterministic ADK pipeline around it, pytest as the judge.
- Show the "Using Gemini CLI safely as a tool" table and "Changes from the reference" in
  `README.md`.
- Run `uv run pytest` (42 offline tests, about 6 seconds).
- Mention the GitHub repo link.

## After recording

1. Upload to YouTube (Unlisted is fine if it only needs to be shared with graders).
2. Replace `_link coming soon_` in `README.md` with the video URL.
3. `rm -rf workspace` if you don't need the clones.
