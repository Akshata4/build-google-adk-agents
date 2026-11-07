"""Offline tests for the test generation pipeline.

A scripted fake `gemini` replays one step per call (write files, answer);
git and pytest are real, on a copy of the bundled sample project.
"""

import asyncio
import json
import sys
import textwrap
from pathlib import Path
from types import SimpleNamespace

import pytest
from google.adk.agents import BaseAgent
from google.adk.events import Event, EventActions
from google.adk.runners import InMemoryRunner
from google.genai import types

from gemini_cli_agent import pipeline, tools
from gemini_cli_agent.agent import root_agent

FAKE_CLI = textwrap.dedent(
    """\
    #!{python}
    import json, os, sys
    from pathlib import Path
    script = json.loads(Path(os.environ["FAKE_CLI_SCRIPT"]).read_text())
    log = Path(os.environ["FAKE_CLI_LOG"])
    calls = json.loads(log.read_text()) if log.exists() else []
    calls.append(sys.argv[1:])
    log.write_text(json.dumps(calls))
    step = script[min(len(calls), len(script)) - 1]
    if "error" in step:
        print(json.dumps({{"error": {{"message": step["error"], "code": 1}}}}),
              file=sys.stderr)
        sys.exit(1)
    for path, content in step.get("files", {{}}).items():
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(content)
    print(json.dumps({{"response": step.get("response", "done"),
                      "stats": {{"tools": {{"byName": {{}}}}}}}}))
    """
)

FAILING_TESTS = """\
from decimal import Decimal
from shopping_cart import Cart, Item

def test_subtotal():
    cart = Cart()
    cart.add(Item("a", "Apple", Decimal("2.50")), 2)
    assert cart.subtotal() == Decimal("999")
"""

PASSING_TESTS = """\
from decimal import Decimal
import pytest
from shopping_cart import Cart, Item

def test_subtotal():
    cart = Cart()
    cart.add(Item("a", "Apple", Decimal("2.50")), 2)
    assert cart.subtotal() == Decimal("5.00")

@pytest.mark.xfail(reason="possible bug: remove() with a negative quantity adds items", strict=True)
def test_remove_negative_quantity():
    cart = Cart()
    cart.add(Item("a", "Apple", Decimal("1")), 2)
    with pytest.raises(ValueError):
        cart.remove("a", -1)
"""


class FakeReport(BaseAgent):
    """Stands in for the model-written report."""

    async def _run_async_impl(self, ctx):
        yield Event(
            author=self.name,
            actions=EventActions(state_delta={"final_test_report": "report"}),
        )


@pytest.fixture
def sample_repo(tmp_path, monkeypatch):
    monkeypatch.setattr(tools, "WORKSPACE_DIR", tmp_path / "workspace")
    ctx = SimpleNamespace(state={})
    result = asyncio.run(tools.prepare_sample_project(ctx))
    assert result["status"] == "success", result
    return Path(ctx.state["repo_path"])


@pytest.fixture
def fake_cli(tmp_path, monkeypatch):
    script = tmp_path / "gemini"
    script.write_text(FAKE_CLI.format(python=sys.executable))
    script.chmod(0o755)
    monkeypatch.setattr(tools, "GEMINI_CLI_PATH", str(script))
    monkeypatch.setattr(tools, "GEMINI_CLI_MODEL", "")
    log = tmp_path / "calls.json"
    monkeypatch.setenv("FAKE_CLI_LOG", str(log))

    def use(steps):
        path = tmp_path / "script.json"
        path.write_text(json.dumps(steps))
        monkeypatch.setenv("FAKE_CLI_SCRIPT", str(path))

    use.calls = lambda: json.loads(log.read_text()) if log.exists() else []
    return use


def run_pipeline(state, request="the Cart class"):
    async def go():
        runner = InMemoryRunner(
            agent=pipeline.make_pipeline(report=FakeReport(name="TestReport")),
            app_name="t",
        )
        session = await runner.session_service.create_session(
            app_name="t", user_id="u", state=state
        )
        message = types.Content(
            role="user", parts=[types.Part(text=json.dumps({"request": request}))]
        )
        async for _ in runner.run_async(
            user_id="u", session_id=session.id, new_message=message
        ):
            pass
        session = await runner.session_service.get_session(
            app_name="t", user_id="u", session_id=session.id
        )
        return session.state

    return asyncio.run(go())


def test_sample_project_is_a_fresh_git_copy(sample_repo):
    assert (sample_repo / ".git").is_dir()
    assert (sample_repo / "shopping_cart" / "cart.py").is_file()
    assert not (sample_repo / "generated_tests").exists()


def test_fix_loop_until_tests_pass(sample_repo, fake_cli):
    cart_py = (sample_repo / "shopping_cart" / "cart.py").read_text()
    fake_cli(
        [
            {"response": "PLAN: test Cart.subtotal"},
            {
                "files": {
                    pipeline.TEST_FILE: FAILING_TESTS,
                    "shopping_cart/cart.py": "# 'fixed' the code\n",
                    "notes.txt": "stray file",
                },
            },
            {"files": {pipeline.TEST_FILE: PASSING_TESTS}},
        ]
    )
    state = run_pipeline({"repo_path": str(sample_repo)})

    assert state["test_plan"] == "PLAN: test Cart.subtotal"
    assert [r["exit_code"] for r in state["test_runs"]] == [1, 0]
    assert "1 passed, 1 xfailed" in state["test_runs"][1]["summary"]
    assert state["possible_bugs"] == [
        "possible bug: remove() with a negative quantity adds items"
    ]
    assert state["test_pipeline_error"] is None
    assert state["final_test_report"] == "report"

    # The writer may only add tests: its other changes were undone.
    assert sorted(state["reverted_files"]) == ["notes.txt", "shopping_cart/cart.py"]
    assert (sample_repo / "shopping_cart" / "cart.py").read_text() == cart_py
    assert not (sample_repo / "notes.txt").exists()

    planner, writer1, writer2 = fake_cli.calls()
    assert "--approval-mode=plan" in planner
    assert "--approval-mode=auto_edit" in writer1
    assert "the Cart class" in planner[0]
    assert "PLAN: test Cart.subtotal" in writer1[0]
    # The second round gets the failing pytest output to fix.
    assert "assert Decimal('5.00') == Decimal('999')" in writer2[0]


def test_loop_stops_after_max_rounds(sample_repo, fake_cli):
    fake_cli([{"response": "PLAN"}, {"files": {pipeline.TEST_FILE: FAILING_TESTS}}])
    state = run_pipeline({"repo_path": str(sample_repo)})
    assert [r["exit_code"] for r in state["test_runs"]] == [1] * pipeline.MAX_ROUNDS
    assert len(fake_cli.calls()) == 1 + pipeline.MAX_ROUNDS


def test_cli_error_stops_pipeline(sample_repo, fake_cli):
    fake_cli([{"error": "model overloaded"}])
    state = run_pipeline({"repo_path": str(sample_repo)})
    assert state["test_pipeline_error"] == "TestPlanner: model overloaded"
    assert state["test_runs"] == []
    assert len(fake_cli.calls()) == 1  # no writer calls
    assert state["final_test_report"] == "report"  # the report still explains it


def test_writer_error_ends_loop(sample_repo, fake_cli):
    fake_cli([{"response": "PLAN"}, {"error": "quota exceeded"}])
    state = run_pipeline({"repo_path": str(sample_repo)})
    assert state["test_pipeline_error"] == "TestWriter: quota exceeded"
    assert state["test_runs"] == []
    assert len(fake_cli.calls()) == 2


def test_needs_a_repository(fake_cli, tmp_path):
    fake_cli([{"response": "unused"}])
    state = run_pipeline({})
    assert "prepare_repository" in state["test_pipeline_error"]
    assert fake_cli.calls() == []


def test_rerun_starts_clean(sample_repo, fake_cli):
    fake_cli([{"response": "PLAN"}, {"files": {pipeline.TEST_FILE: PASSING_TESTS}}])
    first = run_pipeline({"repo_path": str(sample_repo)})
    second = run_pipeline({**first, "test_runs": [{"round": 9}]})
    assert [r["round"] for r in second["test_runs"]] == [1]


def test_pytest_ignores_repo_conftest_and_config(sample_repo):
    (sample_repo / "conftest.py").write_text("raise RuntimeError('repo conftest')\n")
    (sample_repo / "pytest.ini").write_text("[pytest]\naddopts = --no-such-flag\n")
    (sample_repo / pipeline.TESTS_DIR).mkdir()
    (sample_repo / pipeline.TEST_FILE).write_text(PASSING_TESTS)
    result = asyncio.run(pipeline.run_pytest(sample_repo))
    assert result["exit_code"] == 0, result["output"]


def test_revert_keeps_tests_and_cli_config(sample_repo):
    (sample_repo / pipeline.TESTS_DIR).mkdir()
    (sample_repo / pipeline.TEST_FILE).write_text("x = 1\n")
    (sample_repo / ".gemini").mkdir()
    (sample_repo / ".gemini" / "settings.json").write_text("{}")
    (sample_repo / "README.md").unlink()
    (sample_repo / "shopping_cart" / "new_module.py").write_text("")
    reverted = asyncio.run(pipeline.revert_outside_changes(sample_repo))
    assert sorted(reverted) == ["README.md", "shopping_cart/new_module.py"]
    assert (sample_repo / "README.md").is_file()
    assert (sample_repo / pipeline.TEST_FILE).is_file()


@pytest.mark.parametrize(
    "output, summary",
    [
        ("....\n4 passed in 0.01s\n", "4 passed in 0.01s"),
        (
            "F.\n=== 1 failed, 1 passed, 1 xfailed in 0.1s ===",
            "1 failed, 1 passed, 1 xfailed in 0.1s",
        ),
        ("no tests ran in 0.01s", "no tests ran in 0.01s"),
        ("", "no pytest summary"),
    ],
)
def test_summarize_pytest(output, summary):
    assert pipeline.summarize_pytest(output) == summary


def test_pipeline_needs_confirmation():
    tool = pipeline.test_generation_tool
    assert asyncio.run(tool.check_require_confirmation({}, None)) is True


def test_root_agent_tools():
    names = [getattr(t, "name", None) for t in root_agent.tools]
    assert names == [
        "prepare_repository",
        "prepare_sample_project",
        "gemini_cli",
        "TestGenerationPipeline",
    ]
