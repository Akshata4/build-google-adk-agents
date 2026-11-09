# Demo and Video Walkthrough Script

A ~13 minute script for recording the code and execution walkthrough of the code review
assistant.

## Before recording

1. Quota: the live demo uses about 12 model requests and `adk eval` about 20. The free tier
   allows 20 a day per model, so record on a fresh day, use a billed key, or set
   `WORKER_MODEL` / `CRITIC_MODEL` in `.env` to a model with its own quota (for example
   `gemini-3.5-flash`).
2. Start clean: `rm -f sessions.db`.
3. Have the two snippets below ready to paste.
4. Open these files in your editor, in order: `harness.py`, `sub_agents/common.py`,
   `sub_agents/review_pipeline.py`, `sub_agents/fix_pipeline.py`, `agent.py`, `plugins.py`,
   `evals/metrics.py`.
5. Increase terminal and browser font size.

Buggy snippet (the reference's demo):

````
Please review this:
```python
def dfs(graph, start):
    """Performs depth-first search on a graph."""
    visited = set()
    stack = start
    while stack:
        current = stack.pop()
        if current not in visited:
            visited.add(current)
            stack.extend(graph[current])
    return visited
```
````

Clean snippet:

````
```python
def add(a, b):
    """Return the sum of a and b."""
    return a + b
```
````

## 1. Intro (1 min)

- What it is: an ADK assistant that reviews Python code with tests actually executed in
  Gemini's sandbox, then fixes the code and proves the fix with the same tests.
- The key idea: models write tests and fixes; **code** decides what's true. Show the
  architecture diagram in `README.md` and point at the *code* vs *model* labels.

## 2. Code walkthrough (6 min)

**`harness.py`**
- `_RUNNER`: runs each `test_*` with a 5 second alarm and prints one `RESULTS_JSON:` line.
- `source_literal`: the submission is embedded as a readable raw string, never retyped.
- `clean_tests`: comments and a redefined `expect_raises` are removed. Mention why: long
  scripts made the sandbox model write its own harness instead of copying.
- `same_code`: syntax trees are compared, so a dropped comment passes but any real change fails.
  Mention the live run where only a comment line was dropped.

**`sub_agents/common.py`**
- `record_executions`: an `after_model_callback` reading `executable_code` and
  `code_execution_result` parts.
- `read_test_results`: the three unverified reasons.
- `ResultReader` escalates only when verified; `make_test_run` is a `LoopAgent` with 2 tries;
  the executor's instruction adds the rejection reason on retry.

**`sub_agents/review_pipeline.py`**: read the docstring at the top, then:
- `CodeIntake` and `StaticAnalysis`: custom `BaseAgent`s, no model call.
- The test writer prompt: natural calls, never bend tests around bugs. Mention the live run
  where it called `dfs(graph, ['A'])` "due to bug".
- `review_verdict`: deterministic; "Not verified" can never become "Ready to use".
- `ReviewScorer`: `user:` state persists across sessions.
- `FixOffer`: the "fix it" line is added by code. A model added it when nothing needed fixing.

**`sub_agents/fix_pipeline.py`**
- `judge_attempt`, `_rank`, `fix_outcome`: plain functions, unit tested.
- `FixLoop`: why it isn't a `LoopAgent` (any `escalate` stops a `LoopAgent`, and the inner
  test run escalates).
- `_fixer_instruction`: the second round sees the previous attempt and its failures.
- `FixedCodeOutput`: prints `fix_best["code"]` directly.

**`agent.py`** and **`plugins.py`**
- Root routing by agent transfer; `app = App(..., plugins=[LimitsPlugin(), UsagePlugin()])`.
- `UsagePlugin.after_model_callback`: usage written through the callback context is saved
  with the event, so it persists.
- `LimitsPlugin.before_run_callback`: returning `Content` ends the run before any model call.

**`evals/metrics.py`**: checks the code steps' messages exactly, so the eval tests results,
not wording.

## 3. Start it (30 s)

```bash
uv run adk web --session_service_uri "sqlite:///./sessions.db"
```

Open the URL and select `code_review_assistant`.

## 4. Live demo (5 min)

| # | Do | What it shows |
|---|---|---|
| 1 | Paste the buggy snippet | transfer to the review pipeline; 0 of N tests pass with `AttributeError ... line 7`; "Needs major fixes"; the fix offer |
| 2 | Open **Events**, click the executor | the `executable_code` and `code_execution_result` parts: the sandbox really ran |
| 3 | Open **State** | `test_results.status: verified`, `review_summary`, `usage`, `user:usage_today` |
| 4 | Reply `fix it` | round 1: N of N pass, style 100; "Fixed"; before/after table; the exact fixed code |
| 5 | Open **Artifacts** | `review_1.md`, `fix_report_1.md`, `fixed_code_1.py` |
| 6 | New session, paste the clean snippet | "Ready to use", no fix offer; the review's Progress section compares with review 1 (`user:` history) |
| 7 | Stop and restart `adk web` with the same command, open the first session | the conversation and state are still there (SQLite) |

Optional: in a new session, type `fix it` to show the fix pipeline explaining there's no review.

## 5. Tests and eval (1 min)

```bash
uv run pytest     # 68 offline tests, about 7 seconds
uv run adk eval code_review_assistant evals/code_review.evalset.json \
    --config_file_path evals/test_config.json
```

If quota is short, show the eval set and `evals/metrics.py` instead of running it.

## 6. Wrap-up (30 s)

- Recap: models write and fix; code verifies, decides and shows the result.
- Show "Changes from the reference" and "Production features" in `README.md`.
- Mention the GitHub repo link.

## After recording

1. Upload to YouTube (Unlisted is fine if it only needs to be shared with graders).
2. Replace `_link coming soon_` in `README.md` with the video URL.
3. `rm -f sessions.db` if you don't need the sessions.
