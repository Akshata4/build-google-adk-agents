"""Fix pipeline, without model calls.

The fixer returns scripted attempts per round; the test run is the real
retry loop and result reader around a local executor, so the same tests
really run against each fixed version.
"""

import asyncio
import subprocess
import sys
from types import SimpleNamespace

from fakes import FakeExecutor
from google.adk.agents import BaseAgent
from google.adk.events import Event, EventActions
from google.adk.runners import InMemoryRunner
from google.genai import types

from code_review_assistant.analysis import analyze_structure, check_style
from code_review_assistant.harness import build_test_script, parse_results
from code_review_assistant.sub_agents.fix_pipeline import (
    _fixer_instruction,
    _save_fix_artifacts,
    build_fix_pipeline,
    fix_outcome,
    judge_attempt,
)
from code_review_assistant.sub_agents.review_pipeline import review_verdict

BUGGY = '''def dfs(graph, start):
    """Performs depth-first search on a graph."""
    visited = set()
    stack = start
    while stack:
        current = stack.pop()
        if current not in visited:
            visited.add(current)
            stack.extend(graph[current])
    return visited
'''
FIXED = BUGGY.replace("stack = start", "stack = [start]")
SYNTAX_ERROR = "def dfs(graph, start:\n    return set()\n"

TESTS = """
def test_basic_path():
    assert dfs({"a": ["b"], "b": []}, "a") == {"a", "b"}

def test_edge_single_node():
    assert dfs({"a": []}, "a") == {"a"}
"""


class ScriptedFixer(BaseAgent):
    """Returns attempts[round - 1] (the last one once they run out)."""

    attempts: list[str]

    async def _run_async_impl(self, ctx):
        n = ctx.session.state["fix_round"]
        code = self.attempts[min(n, len(self.attempts)) - 1]
        attempt = {"fixed_code": code, "changes": [f"change {n}"]}
        yield Event(
            author=self.name, actions=EventActions(state_delta={"fix_attempt": attempt})
        )


class FakeReporter(BaseAgent):
    async def _run_async_impl(self, ctx):
        yield Event(
            author=self.name, actions=EventActions(state_delta={"fix_report": "report"})
        )


def reviewed_state(code=BUGGY) -> dict:
    """Session state as the review pipeline leaves it."""
    out = subprocess.run(
        [sys.executable, "-I", "-c", build_test_script(code, TESTS)],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    structure, style = analyze_structure(code), check_style(code)
    results = {"status": "verified", **parse_results(out.stdout)}
    return {
        "code_to_review": code,
        "structure": structure,
        "style": style,
        "test_suite": {"intended_behavior": "dfs", "test_code": TESTS},
        "test_results": results,
        "review_summary": review_verdict(structure, style, results),
    }


def fix(attempts, state=None, max_rounds=3, executor_tamper=False):
    """Runs the fix pipeline once; returns the final state and the texts."""
    pipeline = build_fix_pipeline(
        ScriptedFixer(name="CodeFixer", attempts=attempts),
        FakeReporter(name="FixReporter"),
        executor=FakeExecutor(
            name="FixExecutor",
            script_key="fix_script",
            executions_key="fix_executions",
            tamper=executor_tamper,
        ),
        max_rounds=max_rounds,
    )

    async def go():
        runner = InMemoryRunner(agent=pipeline, app_name="t")
        session = await runner.session_service.create_session(
            app_name="t",
            user_id="u",
            state=reviewed_state() if state is None else state,
        )
        message = types.Content(role="user", parts=[types.Part(text="fix it")])
        texts = []
        async for event in runner.run_async(
            user_id="u", session_id=session.id, new_message=message
        ):
            if event.content and event.content.parts:
                texts.append("".join(p.text or "" for p in event.content.parts))
        session = await runner.session_service.get_session(
            app_name="t", user_id="u", session_id=session.id
        )
        return session.state, texts

    return asyncio.run(go())


def test_review_state_has_failing_tests():
    state = reviewed_state()
    assert state["test_results"]["error"] == 2
    assert state["review_summary"]["needs_fix"] is True


def test_fixed_in_first_round():
    state, texts = fix([FIXED])
    assert [h["round"] for h in state["fix_history"]] == [1]
    assert state["fix_history"][0]["tests_passed"] == 2
    summary = state["fix_summary"]
    assert (summary["outcome"], summary["best_round"]) == ("Fixed", 1)
    assert summary["before"]["tests_passed"] == 0
    # The user gets exactly the code that passed the tests.
    assert texts[-1] == f"Fixed code:\n```python\n{FIXED}```"
    assert state["user:fix_count"] == 1


def test_failed_round_is_retried():
    # Round 1's verified test run escalates; the fix loop must still go on.
    state, _ = fix([BUGGY, FIXED])
    rounds = [
        (h["round"], h["tests_passed"], h["passed"]) for h in state["fix_history"]
    ]
    assert rounds == [(1, 0, False), (2, 2, True)]
    assert state["fix_summary"]["best_round"] == 2


def test_never_fixed_stops_at_max_rounds():
    state, texts = fix([BUGGY], max_rounds=2)
    assert len(state["fix_history"]) == 2
    assert state["fix_summary"]["outcome"] == "Not fixed"
    assert "keep it" in texts[-1]


def test_unverified_runs_are_not_called_fixed():
    pipeline_state, texts = fix([FIXED], executor_tamper=True, max_rounds=1)
    assert pipeline_state["fix_history"][0]["tests_verified"] is False
    assert pipeline_state["fix_summary"]["outcome"] == "Not verified"
    assert texts[-1].startswith("Best attempt, NOT tested")


def test_syntax_error_attempt_skips_tests():
    state, _ = fix([SYNTAX_ERROR, FIXED])
    first = state["fix_history"][0]
    assert first["syntax_error"].startswith("line 1:")
    assert first["tests_verified"] is False
    assert state["fix_summary"]["outcome"] == "Fixed"


def test_fenced_fix_is_unwrapped():
    state, _ = fix([f"```python\n{FIXED}```"])
    assert state["fix_best"]["code"] == FIXED


def test_without_review_nothing_runs():
    state, texts = fix([FIXED], state={})
    assert state["fix_error"] == "no_review"
    assert state.get("fix_history") is None
    assert "Paste the code first" in texts[0]


def test_clean_review_has_nothing_to_fix():
    state, _ = fix([FIXED], state=reviewed_state(FIXED))
    assert state["fix_error"] == "nothing_to_fix"


def test_judge_and_outcome():
    clean = {"syntax_error": None, "issues": []}
    tests = {
        "status": "verified",
        "load_error": None,
        "passed": 3,
        "total": 3,
        "pass_rate": 100,
    }
    good = judge_attempt(clean, {"score": 95}, tests, has_tests=True)
    assert good["passed"]
    assert not judge_attempt(clean, {"score": 80}, tests, has_tests=True)["passed"]
    unverified = judge_attempt(clean, {"score": 95}, {"status": "unverified"}, True)
    assert not unverified["passed"]
    assert judge_attempt(clean, {"score": 95}, None, has_tests=False)["passed"]

    before = {**good, "passed": False, "pass_rate": 0, "style_score": 80}
    better_style = {**before, "style_score": 90}
    assert fix_outcome(before, better_style) == "Improved"
    worse_tests = {**better_style, "pass_rate": 20}
    assert fix_outcome({**before, "pass_rate": 50}, worse_tests) == "Not fixed"
    unverified_run = {**better_style, "pass_rate": None, "tests_verified": False}
    assert fix_outcome(before, None) == "Not fixed"
    before_verified = {**before, "tests_verified": True}
    assert fix_outcome(before_verified, unverified_run) == "Not verified"
    assert fix_outcome(before, good) == "Fixed"


def test_second_round_instruction_shows_previous_attempt():
    state = reviewed_state()
    first = _fixer_instruction(SimpleNamespace(state=state))
    assert "   4 |     stack = start" in first
    assert "PREVIOUS ATTEMPT" not in first
    state["fix_history"] = [{"round": 1, "passed": False, "changes": ["x"]}]
    state["fix_code"] = "def dfs(graph, start):\n    return 'attempt one'\n"
    second = _fixer_instruction(SimpleNamespace(state=state))
    assert "PREVIOUS ATTEMPT (round 1) WAS NOT ACCEPTED" in second
    assert "attempt one" in second


def test_artifacts_saved():
    saved = []

    async def save_artifact(filename, part):
        saved.append(filename)

    for outcome, expected in [
        ("Fixed", ["fix_report_2.md", "fixed_code_2.py"]),
        ("Not fixed", ["fix_report_2.md"]),
    ]:
        saved.clear()
        ctx = SimpleNamespace(
            state={
                "fix_report": "report",
                "fix_summary": {"outcome": outcome},
                "fix_best": {"code": FIXED},
                "user:fix_count": 2,
            },
            save_artifact=save_artifact,
        )
        asyncio.run(_save_fix_artifacts(ctx))
        assert saved == expected
