"""Review pipeline: analyze, test and give feedback on submitted Python code.

CodeReviewPipeline (SequentialAgent)
1. CodeIntake       plain code: the code exactly as sent, size limit
2. StaticAnalysis   plain code: AST structure and issues, PEP 8 style score
3. TestStage        (skipped on syntax errors)
   a. TestWriter    model, output_schema: test_* functions for the intended behaviour
   b. ScriptBuilder plain code: submission + tests + runner -> test_script
   c. TestRun       LoopAgent, up to 2 attempts:
                    TestRunExecutor  model + Gemini code execution runs test_script
                    TestRunReader    plain code: results from the real output,
                                     ends the loop once verified -> test_results
4. ReviewScorer     plain code: verdict, per-user history (user: state)
5. FeedbackWriter   critic model -> review_feedback, saved as an artifact
"""

import json
from collections.abc import AsyncGenerator
from datetime import datetime

from google.adk.agents import BaseAgent, LlmAgent, SequentialAgent
from google.adk.agents.callback_context import CallbackContext
from google.adk.agents.invocation_context import InvocationContext
from google.adk.agents.readonly_context import ReadonlyContext
from google.adk.events import Event
from google.genai import types

from ..analysis import analyze_structure, check_style, extract_code
from ..config import CRITIC_MODEL, MAX_CODE_CHARS, PASSING_STYLE_SCORE, WORKER_MODEL
from ..harness import build_test_script
from ..schemas import TestSuite
from .common import Gate, make_test_run, text_event

# Everything a review writes; cleared when a new review starts.
REVIEW_KEYS = [
    "code_to_review",
    "review_error",
    "structure",
    "style",
    "test_suite",
    "test_script",
    "test_executions",
    "test_results",
    "review_summary",
    "review_feedback",
]


def _user_text(ctx: InvocationContext) -> str:
    content = ctx.user_content
    return (
        "".join(p.text or "" for p in content.parts)
        if content and content.parts
        else ""
    )


class CodeIntake(BaseAgent):
    """Takes the code from the user's message exactly as written."""

    async def _run_async_impl(
        self, ctx: InvocationContext
    ) -> AsyncGenerator[Event, None]:
        delta = {key: None for key in REVIEW_KEYS}
        code = extract_code(_user_text(ctx))
        if not code or not code.strip():
            delta["review_error"] = "no_code"
            message = (
                "I couldn't find Python code in your message. Paste the code, "
                "ideally in a ```python block, and I'll review it."
            )
        elif len(code) > MAX_CODE_CHARS:
            delta["review_error"] = "too_large"
            message = (
                f"That's {len(code):,} characters; I review up to "
                f"{MAX_CODE_CHARS:,} at a time. Please send one module or a "
                "few functions."
            )
        else:
            # Chat input loses the final newline; don't report it as W292.
            delta["code_to_review"] = code.rstrip("\n") + "\n"
            message = f"Reviewing {len(code.splitlines())} lines of code."
        yield text_event(self.name, message, delta)


class StaticAnalysis(BaseAgent):
    """Structure and style, computed exactly (no model call)."""

    async def _run_async_impl(
        self, ctx: InvocationContext
    ) -> AsyncGenerator[Event, None]:
        code = ctx.session.state["code_to_review"]
        structure = analyze_structure(code)
        style = check_style(code)
        if structure["syntax_error"]:
            note = (
                f"Syntax error at {structure['syntax_error']}; tests will be skipped."
            )
        else:
            note = (
                f"Found {len(structure['functions'])} functions and "
                f"{len(structure['classes'])} classes; style score "
                f"{style['score']}/100 with {style['issue_count']} issues."
            )
        yield text_event(self.name, note, {"structure": structure, "style": style})


# ----- Tests -----
def _test_writer_instruction(ctx: ReadonlyContext) -> str:
    s = ctx.state
    functions = [f["name"] for f in (s.get("structure") or {}).get("functions", [])]
    return f"""
You are a senior Python test engineer. Write unit tests for the code below.

The tests check what the code is MEANT to do, judged from names, docstrings,
comments and common sense, not what it happens to do. A bug must make a
test fail:
- Call each function the natural way its name, parameters and docstring
  suggest (e.g. dfs(graph, "A") starts a search from node "A"), even if
  the implementation would crash on that input.
- Never change inputs or expectations to work around a bug, and never
  write tests that expect a bug's crash (e.g. AttributeError from a bug).
- Do not copy the code's own logic into the expected values.

Rules:
- Write 8-15 test functions named test_basic_*, test_edge_* or test_error_*.
- Use plain assert statements with a short message, e.g.
  assert f(2) == 4, "f(2) should be 4".
- To check an intended exception (one the code is designed to raise for
  bad input), use the provided helper, without redefining it:
  expect_raises(ValueError, func, arg1, arg2)
- The code's functions and classes are already defined; call them directly
  (do not import or redefine them). Functions found: {", ".join(functions) or "none"}.
- Only the standard library. No prints, no files, no network, no randomness.
- Each test must be independent and fast.

CODE:
```python
{s.get("code_to_review", "")}
```
"""


test_writer_agent = LlmAgent(
    model=WORKER_MODEL,
    name="TestWriter",
    description="Writes unit tests for the intended behaviour of the code.",
    instruction=_test_writer_instruction,
    output_schema=TestSuite,
    output_key="test_suite",
    include_contents="none",
    disallow_transfer_to_parent=True,
    disallow_transfer_to_peers=True,
)


class ScriptBuilder(BaseAgent):
    """Assembles submission + tests + runner (plain code)."""

    async def _run_async_impl(
        self, ctx: InvocationContext
    ) -> AsyncGenerator[Event, None]:
        state = ctx.session.state
        suite = state.get("test_suite") or {}
        if isinstance(suite, str):
            suite = json.loads(suite)
        script = build_test_script(state["code_to_review"], suite.get("test_code", ""))
        yield text_event(
            self.name,
            "Running the tests in the code execution sandbox.",
            {"test_script": script, "test_executions": []},
        )


def _can_test(state: dict) -> bool:
    return not state.get("review_error") and not (state.get("structure") or {}).get(
        "syntax_error"
    )


test_stage = Gate(
    name="TestStage",
    condition=_can_test,
    sub_agents=[
        test_writer_agent,
        ScriptBuilder(name="ScriptBuilder"),
        make_test_run("TestRun", "test_script", "test_executions", "test_results"),
    ],
)


# ----- Verdict and history -----
def review_verdict(structure: dict, style: dict, tests: dict | None) -> dict:
    """Deterministic verdict, so the model can't soften or inflate it.

    tests is None when tests were skipped (syntax error).
    """
    problems = []
    if structure.get("syntax_error"):
        problems.append(f"syntax error at {structure['syntax_error']}")
    verified = bool(tests) and tests.get("status") == "verified"
    if verified:
        if tests.get("load_error"):
            problems.append(f"the code fails to load: {tests['load_error']}")
        failing = tests.get("failed", 0) + tests.get("error", 0)
        if failing:
            problems.append(f"{failing} of {tests['total']} tests fail")
    risky = [
        i for i in structure.get("issues", []) if i["kind"] in ("bug-risk", "security")
    ]
    if risky:
        problems.append(f"{len(risky)} bug-risk or security findings")

    if problems:
        verdict = "Needs major fixes"
    elif tests and not verified:
        # Never call code ready when its tests didn't really run.
        verdict = "Not verified"
    elif style.get("score", 0) < PASSING_STYLE_SCORE or structure.get("issues"):
        verdict = "Needs minor fixes"
    else:
        verdict = "Ready to use"
    return {
        "verdict": verdict,
        "problems": problems,
        "tests_verified": verified,
        "needs_fix": verdict in ("Needs major fixes", "Needs minor fixes"),
        "style_score": style.get("score", 0),
        "pass_rate": tests.get("pass_rate") if verified else None,
    }


class ReviewScorer(BaseAgent):
    """Computes the verdict and updates the user's review history."""

    async def _run_async_impl(
        self, ctx: InvocationContext
    ) -> AsyncGenerator[Event, None]:
        state = ctx.session.state
        summary = review_verdict(
            state.get("structure") or {},
            state.get("style") or {},
            state.get("test_results"),
        )
        count = (state.get("user:review_count") or 0) + 1
        summary["review_number"] = count
        summary["previous_review"] = state.get("user:last_review")
        delta = {
            "review_summary": summary,
            # user: keys persist across this user's sessions.
            "user:review_count": count,
            "user:last_review": {
                "style_score": summary["style_score"],
                "pass_rate": summary["pass_rate"],
                "verdict": summary["verdict"],
                "at": datetime.now().isoformat(timespec="minutes"),
            },
        }
        yield text_event(self.name, f"Verdict: {summary['verdict']}.", delta)


# ----- Feedback -----
def numbered(code: str) -> str:
    """Code with line numbers, so the review cites the right lines."""
    return "\n".join(f"{n:4} | {line}" for n, line in enumerate(code.splitlines(), 1))


def _feedback_instruction(ctx: ReadonlyContext) -> str:
    s = ctx.state
    tests = s.get("test_results") or {"status": "not run"}
    suite = s.get("test_suite") or {}
    if isinstance(suite, str):
        suite = json.loads(suite)
    return f"""
You are an expert Python code reviewer and mentor. Write the review of the
code below using ONLY the facts provided. Every number must come from the
data; never invent test results, line numbers or issues.

CODE (with line numbers):
```
{numbered(s.get("code_to_review", ""))}
```

VERDICT (computed, do not change it): {json.dumps(s.get("review_summary"))}
INTENDED BEHAVIOUR (as understood by the test writer): {suite.get("intended_behavior", "unknown")}
STRUCTURE: {json.dumps(s.get("structure"))}
STYLE (pycodestyle, score out of 100): {json.dumps(s.get("style"))}
TEST RESULTS (from the real execution output): {json.dumps(tests)}

Write concise markdown with these sections:
## Summary
The verdict in bold, then 1-2 sentences.
## Test results
"X of N tests passed" (only if status is verified; otherwise say the tests
could not be verified and why). Then each failing or erroring test with
its message, grouped by root cause.
## Bugs found
The underlying bug(s) behind the failures, with the line in the code and
the fix in one sentence each. "None found" if all tests passed.
## Style
The score and the most important issues (line: code message), at most 5.
## Design
Notable structure findings (docstrings, complexity, bug-risk, security).
## Progress
Review number N; if there is a previous review, compare its style score
and pass rate with this one.

End with exactly this line if needs_fix is true:
**Reply "fix it" and I'll fix these issues and re-run the same tests.**
"""


async def _save_report(callback_context: CallbackContext) -> None:
    """Saves the review as a markdown artifact (Artifacts tab in adk web)."""
    feedback = callback_context.state.get("review_feedback")
    if not feedback:
        return
    number = (callback_context.state.get("review_summary") or {}).get(
        "review_number", 1
    )
    await callback_context.save_artifact(
        f"review_{number}.md",
        types.Part.from_bytes(data=feedback.encode(), mime_type="text/markdown"),
    )
    return


feedback_writer_agent = LlmAgent(
    model=CRITIC_MODEL,
    name="FeedbackWriter",
    description="Writes the final review from the analysis and test results.",
    instruction=_feedback_instruction,
    output_key="review_feedback",
    include_contents="none",
    disallow_transfer_to_parent=True,
    disallow_transfer_to_peers=True,
    after_agent_callback=_save_report,
)


code_review_pipeline = SequentialAgent(
    name="CodeReviewPipeline",
    description=(
        "Reviews Python code from the user's latest message: structure, PEP 8 "
        "style, unit tests run in a sandbox, and written feedback."
    ),
    sub_agents=[
        CodeIntake(name="CodeIntake"),
        Gate(
            name="ReviewStage",
            condition=lambda state: not state.get("review_error"),
            sub_agents=[
                StaticAnalysis(name="StaticAnalysis"),
                test_stage,
                ReviewScorer(name="ReviewScorer"),
                feedback_writer_agent,
            ],
        ),
    ],
)
