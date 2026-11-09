"""Review pipeline, without model calls.

The code steps run for real; the test writer and the sandbox executor are
replaced by fakes (the fake executor runs the script locally and records it
the way the after_model_callback does).
"""

import asyncio
import subprocess
import sys
from types import SimpleNamespace

from google.adk.agents import BaseAgent, SequentialAgent
from google.adk.events import Event, EventActions
from google.adk.models import LlmResponse
from google.adk.runners import InMemoryRunner
from google.genai import types

from code_review_assistant.agent import root_agent
from code_review_assistant.harness import build_test_script
from code_review_assistant.sub_agents.common import (
    Gate,
    ResultReader,
    read_test_results,
    record_executions,
)
from code_review_assistant.sub_agents.review_pipeline import (
    CodeIntake,
    ReviewScorer,
    ScriptBuilder,
    StaticAnalysis,
    _can_test,
    review_verdict,
)

DFS = '''def dfs(graph, start):
    """Performs depth-first search on a graph."""
    visited = set()
    stack = start
    while stack:
        current = stack.pop()
        if current not in visited:
            visited.add(current)
            stack.extend(graph[current])
    return visited'''

TESTS = 'def test_basic_path():\n    assert dfs({"a": []}, "a") == {"a"}\n'


class FakeTestWriter(BaseAgent):
    async def _run_async_impl(self, ctx):
        suite = {"intended_behavior": "dfs", "test_code": TESTS}
        yield Event(
            author=self.name, actions=EventActions(state_delta={"test_suite": suite})
        )


class FakeExecutor(BaseAgent):
    """Runs the script locally; `tamper` simulates a model changing it."""

    tamper: bool = False

    async def _run_async_impl(self, ctx):
        script = ctx.session.state["test_script"]
        if self.tamper:
            script = script.replace("stack = start", "stack = [start]")
        out = await asyncio.to_thread(
            subprocess.run,
            [sys.executable, "-I", "-c", script],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        out = out.stdout
        execution = {"code": script, "outcome": "OUTCOME_OK", "output": out}
        yield Event(
            author=self.name,
            actions=EventActions(state_delta={"test_executions": [execution]}),
        )


def pipeline(tamper=False):
    return SequentialAgent(
        name="Pipeline",
        sub_agents=[
            CodeIntake(name="CodeIntake"),
            Gate(
                name="ReviewStage",
                condition=lambda s: not s.get("review_error"),
                sub_agents=[
                    StaticAnalysis(name="StaticAnalysis"),
                    Gate(
                        name="TestStage",
                        condition=_can_test,
                        sub_agents=[
                            FakeTestWriter(name="TestWriter"),
                            ScriptBuilder(name="ScriptBuilder"),
                            FakeExecutor(name="Executor", tamper=tamper),
                            ResultReader(
                                name="Reader",
                                script_key="test_script",
                                executions_key="test_executions",
                                output_key="test_results",
                            ),
                        ],
                    ),
                    ReviewScorer(name="ReviewScorer"),
                ],
            ),
        ],
    )


def review(messages, tamper=False):
    """Sends each message in a new session of the same user; returns states."""

    async def go():
        runner = InMemoryRunner(agent=pipeline(tamper), app_name="t")
        states = []
        for text in messages:
            session = await runner.session_service.create_session(
                app_name="t", user_id="u"
            )
            message = types.Content(role="user", parts=[types.Part(text=text)])
            async for _ in runner.run_async(
                user_id="u", session_id=session.id, new_message=message
            ):
                pass
            session = await runner.session_service.get_session(
                app_name="t", user_id="u", session_id=session.id
            )
            states.append(session.state)
        return states

    return asyncio.run(go())


def test_review_with_real_test_results():
    (state,) = review([f"Please review\n```python\n{DFS}\n```"])
    assert state["code_to_review"] == DFS + "\n"
    assert state["style"]["score"] == 100
    results = state["test_results"]
    assert results["status"] == "verified"
    assert (results["error"], results["total"]) == (1, 1)
    summary = state["review_summary"]
    assert summary["verdict"] == "Needs major fixes"
    assert summary["needs_fix"] is True
    assert summary["problems"] == ["1 of 1 tests fail"]


def test_tampered_execution_is_not_trusted():
    (state,) = review([f"```python\n{DFS}\n```"], tamper=True)
    assert state["test_results"] == {
        "status": "unverified",
        "reason": "the sandbox ran different code than the generated script",
    }
    assert state["review_summary"]["verdict"] == "Not verified"
    assert state["review_summary"]["pass_rate"] is None


def test_history_persists_across_sessions():
    fixed = DFS.replace("stack = start", "stack = [start]")
    first, second = review([f"```python\n{DFS}\n```", f"```python\n{fixed}\n```"])
    assert first["user:review_count"] == 1
    assert second["user:review_count"] == 2
    previous = second["review_summary"]["previous_review"]
    assert previous["pass_rate"] == 0
    assert second["review_summary"]["pass_rate"] == 100
    assert second["review_summary"]["verdict"] == "Ready to use"


def test_message_without_code_stops_early():
    (state,) = review(["what can you do?"])
    assert state["review_error"] == "no_code"
    assert "structure" not in state or state["structure"] is None
    assert "user:review_count" not in state


def test_too_large_is_rejected(monkeypatch):
    from code_review_assistant.sub_agents import review_pipeline

    monkeypatch.setattr(review_pipeline, "MAX_CODE_CHARS", 50)
    (state,) = review([f"```python\n{DFS}\n```"])
    assert state["review_error"] == "too_large"


def test_new_review_clears_old_results():
    fixed = DFS.replace("stack = start", "stack = [start]")

    async def go():
        runner = InMemoryRunner(agent=pipeline(), app_name="t")
        session = await runner.session_service.create_session(app_name="t", user_id="u")
        for text in (f"```python\n{fixed}\n```", "def broken(:\n    pass\n"):
            message = types.Content(role="user", parts=[types.Part(text=text)])
            async for _ in runner.run_async(
                user_id="u", session_id=session.id, new_message=message
            ):
                pass
        return (
            await runner.session_service.get_session(
                app_name="t", user_id="u", session_id=session.id
            )
        ).state

    state = asyncio.run(go())
    # Code with a syntax error is still reviewed, but tests are skipped and
    # the first review's results don't leak into the second.
    assert state["code_to_review"] == "def broken(:\n    pass\n"
    assert state["structure"]["syntax_error"].startswith("line 1:")
    assert state["test_results"] is None
    assert state["test_script"] is None
    assert state["review_summary"]["verdict"] == "Needs major fixes"


def test_record_executions_callback():
    ctx = SimpleNamespace(state={})
    callback = record_executions("runs")
    response = LlmResponse(
        content=types.Content(
            role="model",
            parts=[
                types.Part(
                    executable_code=types.ExecutableCode(
                        code="print(1)", language="PYTHON"
                    )
                ),
                types.Part(
                    code_execution_result=types.CodeExecutionResult(
                        outcome="OUTCOME_OK", output="1\n"
                    )
                ),
            ],
        )
    )
    assert callback(ctx, response) is None
    assert ctx.state["runs"] == [
        {"code": "print(1)", "outcome": "Outcome.OUTCOME_OK", "output": "1\n"}
    ]


def test_read_test_results_cases():
    script = build_test_script("x = 1\n", "def test_a():\n    pass\n")
    output = 'RESULTS_JSON:{"load_error": null, "tests": [{"name": "test_a", "status": "passed", "message": ""}]}'
    ok = read_test_results(
        [{"code": script, "outcome": "OK", "output": output}], script
    )
    assert (ok["status"], ok["passed"], ok["pass_rate"]) == ("verified", 1, 100)
    assert read_test_results([], script)["reason"] == "the tests were not executed"
    no_marker = read_test_results(
        [{"code": script, "outcome": "OUTCOME_DEADLINE_EXCEEDED", "output": ""}], script
    )
    assert no_marker["status"] == "unverified"
    assert "OUTCOME_DEADLINE_EXCEEDED" in no_marker["reason"]


def test_verdicts():
    clean = {"syntax_error": None, "issues": []}
    good_style = {"score": 100}
    passed = {
        "status": "verified",
        "load_error": None,
        "failed": 0,
        "error": 0,
        "total": 3,
        "pass_rate": 100,
    }
    assert review_verdict(clean, good_style, passed)["verdict"] == "Ready to use"
    assert (
        review_verdict(clean, {"score": 70}, passed)["verdict"] == "Needs minor fixes"
    )
    failing = {**passed, "failed": 1, "pass_rate": 66}
    assert review_verdict(clean, good_style, failing)["verdict"] == "Needs major fixes"
    unverified = {"status": "unverified", "reason": "x"}
    result = review_verdict(clean, good_style, unverified)
    assert result["verdict"] == "Not verified" and result["needs_fix"] is False
    risky = {
        "syntax_error": None,
        "issues": [{"kind": "security", "line": 1, "message": "eval"}],
    }
    assert (
        review_verdict(risky, good_style, unverified)["verdict"] == "Needs major fixes"
    )
    syntax = {"syntax_error": "line 1: invalid syntax", "issues": []}
    assert review_verdict(syntax, good_style, None)["verdict"] == "Needs major fixes"


def test_root_routes_by_transfer():
    assert [a.name for a in root_agent.sub_agents] == ["CodeReviewPipeline"]
