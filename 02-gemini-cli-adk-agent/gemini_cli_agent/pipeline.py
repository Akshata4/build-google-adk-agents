"""Test generation pipeline: Gemini CLI writes tests, pytest checks them.

TestGenerationPipeline (SequentialAgent)
1. TestPlanner   Gemini CLI, read-only          -> test_plan
2. TestFixLoop   LoopAgent, at most 3 rounds:
   a. TestWriter Gemini CLI, edit mode          -> generated_tests/test_generated.py
                 then undo any change outside generated_tests/
   b. TestRunner pytest (plain code)            -> test_runs; stops the loop on a pass
3. TestReport    ADK model                      -> final_test_report

Only the report uses the ADK model. The other steps call Gemini CLI or pytest
directly from code, so the order of steps never depends on a model's choice.
"""

import json
import os
import re
import shutil
import sys
import warnings
from collections.abc import AsyncGenerator, Callable
from pathlib import Path
from typing import Any

from google.adk.agents import BaseAgent, LlmAgent, LoopAgent, SequentialAgent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.agents.readonly_context import ReadonlyContext
from google.adk.events import Event, EventActions
from google.adk.tools.agent_tool import AgentTool
from google.genai import types

from .config import MODEL
from .tools import UNTRUSTED_CLI_CONFIG, _run, run_gemini_cli

# Sequential/LoopAgent are deprecated in favour of Workflow in ADK 2.x, but
# Workflow can't yet run under an LlmAgent (here: via AgentTool), so keep them.
warnings.filterwarnings("ignore", message=r".*(Sequential|Loop)Agent is deprecated.*")

TESTS_DIR = "generated_tests"
TEST_FILE = f"{TESTS_DIR}/test_generated.py"
MAX_ROUNDS = 3
PYTEST_TIMEOUT = 300
OUTPUT_TAIL_CHARS = 6_000

# Keys this pipeline writes; cleared at the start of every run.
PIPELINE_KEYS = (
    "test_focus",
    "test_plan",
    "test_writer_output",
    "test_round",
    "test_runs",
    "reverted_files",
    "possible_bugs",
    "test_pipeline_error",
    "final_test_report",
)


def _text_event(author: str, text: str, state_delta: dict | None = None) -> Event:
    return Event(
        author=author,
        content=types.Content(role="model", parts=[types.Part(text=text)]),
        actions=EventActions(state_delta=state_delta or {}),
    )


def _user_text(ctx: InvocationContext) -> str:
    content = ctx.user_content
    if not content or not content.parts:
        return ""
    text = "".join(p.text or "" for p in content.parts)
    # AgentTool sends the tool arguments as JSON, e.g. {"request": "..."}.
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        return text
    return value.get("request", text) if isinstance(value, dict) else text


# ----- Helpers that run outside the model -----
async def revert_outside_changes(repo: Path, allowed_dir: str = TESTS_DIR) -> list[str]:
    """Undoes every change outside allowed_dir, compared with the last commit.

    The writer may only add tests; it must never change the code under test.
    Returns the paths that were reverted or deleted.
    """
    code, out, _ = await _run(
        ["git", "status", "--porcelain=v1", "-z", "--untracked-files=all"],
        cwd=repo,
        timeout=60,
    )
    if code != 0:
        return []
    entries = out.split("\0")
    paths = []
    i = 0
    while i < len(entries):
        entry = entries[i]
        i += 1
        if len(entry) < 4:
            continue
        status, path = entry[:2], entry[3:]
        paths.append(path)
        if "R" in status or "C" in status:  # followed by the original path
            paths.append(entries[i])
            i += 1

    reverted = []
    for path in dict.fromkeys(paths):
        top = path.split("/", 1)[0]
        if top == allowed_dir or top in UNTRUSTED_CLI_CONFIG:
            continue
        in_head, _, _ = await _run(
            ["git", "cat-file", "-e", f"HEAD:{path}"], cwd=repo, timeout=60
        )
        if in_head == 0:
            await _run(["git", "checkout", "HEAD", "--", path], cwd=repo, timeout=60)
        else:
            target = repo / path
            if target.is_file() or target.is_symlink():
                target.unlink()
        reverted.append(path)
    return reverted


def summarize_pytest(output: str) -> str:
    """The final summary line, e.g. '2 failed, 9 passed, 1 xfailed in 0.05s'."""
    for line in reversed(output.strip().splitlines()):
        if re.search(
            r"\b(passed|failed|errors?|xfailed|xpassed|skipped|no tests ran)\b", line
        ):
            return line.strip("= ").strip()
    return "no pytest summary"


def find_possible_bugs(test_file: Path) -> list[str]:
    """The reasons of xfail markers: behaviour the tests think is a bug."""
    if not test_file.is_file():
        return []
    text = test_file.read_text(errors="replace")
    return re.findall(r"xfail\([^)]*reason\s*=\s*[\"'](.+?)[\"']", text)


async def run_pytest(repo: Path) -> dict:
    """Runs only the generated tests, with the repository importable.

    The repo's own pytest config and conftest files are not loaded, so its
    plugins or fixtures can't break (or hijack) the run.
    """
    tests_dir = repo / TESTS_DIR
    env = {
        **os.environ,
        "PYTHONPATH": os.pathsep.join([str(repo), str(repo / "src")]),
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    code, out, err = await _run(
        [
            sys.executable,
            "-m",
            "pytest",
            TESTS_DIR,
            "-q",
            "--no-header",
            "-p",
            "no:cacheprovider",
            "-c",
            os.devnull,
            "--rootdir",
            str(tests_dir),
            "--confcutdir",
            str(tests_dir),
        ],
        cwd=repo,
        timeout=PYTEST_TIMEOUT,
        env=env,
    )
    output = (out + err)[-OUTPUT_TAIL_CHARS:]
    return {"exit_code": code, "summary": summarize_pytest(output), "output": output}


# ----- Agents -----
class GeminiCliStep(BaseAgent):
    """Runs one Gemini CLI task built from state; no ADK model call."""

    build_task: Callable[[dict, str], str]  # (state, user request) -> task
    mode: str = "analyze"
    output_key: str
    guard: bool = False  # undo changes outside generated_tests/ afterwards

    async def _run_async_impl(
        self, ctx: InvocationContext
    ) -> AsyncGenerator[Event, None]:
        state = ctx.session.state
        repo = Path(state.get("repo_path") or "")
        task = self.build_task(state, state.get("test_focus") or "")
        result = await run_gemini_cli(task, repo, self.mode)
        if result["status"] != "success":
            yield _text_event(
                self.name,
                f"Gemini CLI failed: {result['message']}",
                {"test_pipeline_error": f"{self.name}: {result['message']}"},
            )
            return

        delta: dict[str, Any] = {self.output_key: result["response"]}
        note = f"{self.name}: Gemini CLI used {result['cli_tools_used'] or 'no tools'}."
        if self.guard:
            reverted = await revert_outside_changes(repo)
            if reverted:
                delta["reverted_files"] = sorted(
                    set(state.get("reverted_files") or []) | set(reverted)
                )
                note += f" Undid changes outside {TESTS_DIR}/: {', '.join(reverted)}."
        yield _text_event(self.name, note, delta)


class PipelineStart(BaseAgent):
    """Checks there is a repository and clears results of an earlier run."""

    async def _run_async_impl(
        self, ctx: InvocationContext
    ) -> AsyncGenerator[Event, None]:
        state = ctx.session.state
        repo_path = state.get("repo_path")
        delta: dict[str, Any] = {key: None for key in PIPELINE_KEYS}
        if not repo_path or not Path(repo_path).is_dir():
            delta["test_pipeline_error"] = (
                "No repository prepared. Call prepare_repository or "
                "prepare_sample_project first."
            )
        else:
            shutil.rmtree(Path(repo_path) / TESTS_DIR, ignore_errors=True)
        delta["test_focus"] = _user_text(ctx) or "the main public functions and classes"
        delta["test_round"] = 0
        delta["test_runs"] = []
        yield _text_event(self.name, "Starting test generation.", delta)


class TestRunner(BaseAgent):
    """Runs pytest on the generated tests; ends the loop when they pass."""

    async def _run_async_impl(
        self, ctx: InvocationContext
    ) -> AsyncGenerator[Event, None]:
        state = ctx.session.state
        repo = Path(state["repo_path"])
        round_number = (state.get("test_round") or 0) + 1
        result = await run_pytest(repo)
        runs = list(state.get("test_runs") or []) + [{"round": round_number, **result}]
        passed = result["exit_code"] == 0
        delta = {
            "test_round": round_number,
            "test_runs": runs,
            "possible_bugs": find_possible_bugs(repo / TEST_FILE),
        }
        verdict = "all generated tests pass" if passed else "tests need fixing"
        yield Event(
            author=self.name,
            content=types.Content(
                role="model",
                parts=[
                    types.Part(
                        text=f"Round {round_number}: {result['summary']} ({verdict})."
                    )
                ],
            ),
            actions=EventActions(state_delta=delta, escalate=passed),
        )


class StopOnError(BaseAgent):
    """Ends the loop early when a Gemini CLI step failed; retrying won't help."""

    async def _run_async_impl(
        self, ctx: InvocationContext
    ) -> AsyncGenerator[Event, None]:
        if ctx.session.state.get("test_pipeline_error"):
            yield Event(author=self.name, actions=EventActions(escalate=True))


class SkipIfError(BaseAgent):
    """Runs its sub-agents in order, unless an earlier step failed."""

    async def _run_async_impl(
        self, ctx: InvocationContext
    ) -> AsyncGenerator[Event, None]:
        for agent in self.sub_agents:
            if ctx.session.state.get("test_pipeline_error"):
                return
            async for event in agent.run_async(ctx):
                yield event


def _plan_task(state: dict, focus: str) -> str:
    return f"""
Analyze this repository and write a concise unit test plan for: {focus}

Include:
1. The functions and classes to test, each with its file path.
2. The exact import statements a test file needs (check for a src/ layout).
3. Test cases for each: normal use, edge cases and error handling, with the
   expected result taken from the code.
4. Any behaviour that looks like a bug, with the file and line.

Keep it to the most valuable 10-20 test cases. Do not write test code yet.
"""


_WRITER_RULES = f"""
Rules:
- Write exactly one file: {TEST_FILE} (create the {TESTS_DIR} folder). Do not
  create, change or delete any other file.
- Do not run commands. The tests will be run for you from the repository
  root with `python -m pytest {TESTS_DIR}`; the repository root and src/ are
  on PYTHONPATH and the repository's own conftest.py is NOT loaded.
- Use pytest and the standard library only (plus the project's own code).
- Every expected value must follow from the code you read, not a guess.
- If the code has a bug, keep a test for the correct behaviour and mark it
  @pytest.mark.xfail(reason="possible bug: <what is wrong>", strict=True).
  Never change the code under test.
"""


def _write_task(state: dict, focus: str) -> str:
    runs = state.get("test_runs") or []
    if not runs:
        return f"""
Write pytest unit tests for: {focus}

Follow this test plan:
{state.get("test_plan", "")}
{_WRITER_RULES}"""
    last = runs[-1]
    return f"""
The tests in {TEST_FILE} were run and did not all pass.

Pytest result: {last["summary"]}
Pytest output:
{last["output"]}

Fix {TEST_FILE}. Re-read the code under test before changing an expected
value. A test that fails because of a real bug in the code should be marked
xfail as described below, not deleted. If the file is missing or has an
import error, rewrite it.
{_WRITER_RULES}"""


def _report_instruction(ctx: ReadonlyContext) -> str:
    s = ctx.state
    runs = s.get("test_runs") or []
    test_file = Path(s.get("repo_path") or ".") / TEST_FILE
    code = test_file.read_text(errors="replace") if test_file.is_file() else ""
    runs_summary = [
        {"round": r["round"], "exit_code": r["exit_code"], "summary": r["summary"]}
        for r in runs
    ]
    return f"""
You write the final report of an automated test generation run. Use only the
facts below; never invent results.

Repository: {s.get("repo_url")}
Local folder: {s.get("repo_path")}
Focus: {s.get("test_focus")}
Error that stopped the run (if any): {s.get("test_pipeline_error")}
Pytest rounds (exit code 0 means every test passed or was an expected xfail):
{json.dumps(runs_summary, indent=1)}
Last pytest output:
{runs[-1]["output"][-2500:] if runs else "(tests never ran)"}
Possible bugs (xfail reasons in the tests): {json.dumps(s.get("possible_bugs") or [])}
Files the writer changed outside {TESTS_DIR}/ (undone): {json.dumps(s.get("reverted_files") or [])}

Generated test file {TEST_FILE}:
```python
{code[:12000]}
```

Write a short markdown report:
1. **Result**: passed or not, after how many of {MAX_ROUNDS} rounds, with the
   final pytest summary line.
2. **What is covered**: a table of the areas tested and the number of tests.
3. **Possible bugs found**: each xfail reason, or "none".
4. **Notes**: undone changes, errors, or tests still failing, if any.
5. **Run them yourself**: `cd <local folder>` and
   `python -m pytest {TESTS_DIR}`.
"""


def make_pipeline(report: BaseAgent | None = None) -> SequentialAgent:
    """Builds the pipeline. Tests pass their own report step (no model)."""
    return SequentialAgent(
        name="TestGenerationPipeline",
        description=(
            "Generates pytest unit tests for the prepared repository with Gemini "
            "CLI, runs them, fixes failures for up to 3 rounds and reports the "
            "result. Pass what to test as the request, e.g. 'the Cart class'."
        ),
        sub_agents=[
            PipelineStart(name="PipelineStart"),
            SkipIfError(
                name="PlanStep",
                sub_agents=[
                    GeminiCliStep(
                        name="TestPlanner",
                        build_task=_plan_task,
                        output_key="test_plan",
                    )
                ],
            ),
            SkipIfError(
                name="WriteAndRunStep",
                sub_agents=[
                    LoopAgent(
                        name="TestFixLoop",
                        max_iterations=MAX_ROUNDS,
                        sub_agents=[
                            GeminiCliStep(
                                name="TestWriter",
                                build_task=_write_task,
                                mode="edit",
                                output_key="test_writer_output",
                                guard=True,
                            ),
                            StopOnError(name="StopOnError"),
                            TestRunner(name="TestRunner"),
                        ],
                    )
                ],
            ),
            report
            or LlmAgent(
                name="TestReport",
                model=MODEL,
                instruction=_report_instruction,
                include_contents="none",
                output_key="final_test_report",
            ),
        ],
    )


test_generation_pipeline = make_pipeline()


class ConfirmedAgentTool(AgentTool):
    """An AgentTool that asks the user to approve every call first."""

    async def check_require_confirmation(self, args, tool_context) -> bool:
        return True


# Writes files and runs the repository's code, so the user approves each run.
test_generation_tool = ConfirmedAgentTool(test_generation_pipeline)
