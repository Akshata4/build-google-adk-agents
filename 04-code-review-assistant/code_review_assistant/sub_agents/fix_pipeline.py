"""Fix pipeline: fixes the reviewed code and re-runs the review's own tests.

CodeFixPipeline (SequentialAgent)
1. FixIntake        plain code: needs a review with issues in this session
2. FixLoop          up to MAX_FIX_ROUNDS rounds, stops once a round passes:
   a. CodeFixer     model, output_schema: the whole fixed module + changes
   b. FixChecker    plain code: structure and style of the fixed code, and
                    the SAME tests rebuilt around it -> fix_script
   c. FixTestRun    LoopAgent: sandbox run + verified results (as in review)
   d. FixValidator  plain code: did the round pass, keeps the best attempt
3. FixOutcome       plain code: Fixed / Improved / Not fixed, from the numbers
4. FixReporter      critic model: what changed and why -> fix_report
5. FixedCodeOutput  plain code: the exact code that was tested
"""

import json
from collections.abc import AsyncGenerator

from google.adk.agents import BaseAgent, LlmAgent, SequentialAgent
from google.adk.agents.callback_context import CallbackContext
from google.adk.agents.invocation_context import InvocationContext
from google.adk.agents.readonly_context import ReadonlyContext
from google.adk.events import Event, EventActions
from google.genai import types

from ..analysis import analyze_structure, check_style, strip_fences
from ..config import CRITIC_MODEL, MAX_FIX_ROUNDS, PASSING_STYLE_SCORE, WORKER_MODEL
from ..harness import build_test_script
from ..schemas import CodeFix
from .common import Gate, as_dict, make_test_run, text_event
from .review_pipeline import numbered

# Everything a fix writes; cleared when a new fix starts.
FIX_KEYS = [
    "fix_error",
    "fix_baseline",
    "fix_round",
    "fix_attempt",
    "fix_code",
    "fix_structure",
    "fix_style",
    "fix_script",
    "fix_executions",
    "fix_test_results",
    "fix_history",
    "fix_best",
    "fix_passed",
    "fix_summary",
    "fix_report",
]


# ----- Judging an attempt (plain code, unit tested) -----
def _risky(structure: dict) -> int:
    return sum(
        1 for i in structure.get("issues", []) if i["kind"] in ("bug-risk", "security")
    )


def judge_attempt(
    structure: dict, style: dict, tests: dict | None, has_tests: bool
) -> dict:
    """The numbers for one version of the code, and whether they pass.

    Passing means: it parses, every test passes in a verified run (when the
    review had tests), no bug-risk or security findings, and the style
    score reaches PASSING_STYLE_SCORE.
    """
    verified = bool(tests) and tests.get("status") == "verified"
    all_pass = (
        verified
        and not tests.get("load_error")
        and tests["total"] > 0
        and tests["passed"] == tests["total"]
    )
    syntax_error = structure.get("syntax_error")
    risky = _risky(structure)
    score = style.get("score", 0)
    return {
        "passed": not syntax_error
        and (all_pass or not has_tests)
        and risky == 0
        and score >= PASSING_STYLE_SCORE,
        "syntax_error": syntax_error,
        "tests_verified": verified,
        "tests_passed": tests["passed"] if verified else None,
        "tests_total": tests["total"] if verified else None,
        "pass_rate": tests["pass_rate"] if verified else None,
        "risky_findings": risky,
        "style_score": score,
    }


def _rank(judgement: dict) -> tuple:
    """Orders attempts: passing first, then parses, pass rate, safety, style."""
    return (
        judgement["passed"],
        not judgement["syntax_error"],
        judgement["pass_rate"] if judgement["pass_rate"] is not None else -1,
        -judgement["risky_findings"],
        judgement["style_score"],
    )


def fix_outcome(before: dict, best: dict | None) -> str:
    """Fixed, Improved (better on some measure, worse on none), Not verified
    (the review had verified tests but no fix attempt's run was verified) or
    Not fixed."""
    if best and best["passed"]:
        return "Fixed"
    if not best or best["syntax_error"]:
        return "Not fixed"
    if before["tests_verified"] and not best["tests_verified"]:
        return "Not verified"

    def rate(j):
        return j["pass_rate"] if j["pass_rate"] is not None else -1

    pairs = [
        (rate(best), rate(before)),
        (-best["risky_findings"], -before["risky_findings"]),
        (best["style_score"], before["style_score"]),
    ]
    if all(a >= b for a, b in pairs) and any(a > b for a, b in pairs):
        return "Improved"
    return "Not fixed"


def _failing_tests(tests: dict | None) -> str:
    if not tests or tests.get("status") != "verified":
        return "unknown (the tests could not be verified)"
    if tests.get("load_error"):
        return f"the code failed to load: {tests['load_error']}"
    failing = [
        f"- {t['name']}: {t['status']}: {t['message']}"
        for t in tests["tests"]
        if t["status"] != "passed"
    ]
    return "\n".join(failing) or "none"


# ----- Intake -----
class FixIntake(BaseAgent):
    """Starts a fix from this session's last review, if there is one to fix."""

    async def _run_async_impl(
        self, ctx: InvocationContext
    ) -> AsyncGenerator[Event, None]:
        state = ctx.session.state
        delta = {key: None for key in FIX_KEYS}
        summary = state.get("review_summary")
        if not state.get("code_to_review") or not summary:
            delta["fix_error"] = "no_review"
            message = (
                "There's no review in this conversation to fix yet. Paste the "
                'code first; after the review, reply "fix it".'
            )
        elif summary["verdict"] == "Not verified":
            delta["fix_error"] = "not_verified"
            message = (
                "The last review's tests couldn't be verified, so I can't check "
                "a fix against them. Send the code again for a fresh review."
            )
        elif not summary["needs_fix"]:
            delta["fix_error"] = "nothing_to_fix"
            message = f"The last review found nothing to fix ({summary['verdict']})."
        else:
            has_tests = bool(as_dict(state.get("test_suite")).get("test_code"))
            delta["fix_baseline"] = judge_attempt(
                state.get("structure") or {},
                state.get("style") or {},
                state.get("test_results"),
                has_tests,
            )
            delta["fix_history"] = []
            message = (
                f"Fixing the code, up to {MAX_FIX_ROUNDS} rounds; each fix is "
                "checked with the review's own tests."
            )
        yield text_event(self.name, message, delta)


# ----- One round -----
def _fixer_instruction(ctx: ReadonlyContext) -> str:
    s = ctx.state
    suite = as_dict(s.get("test_suite"))
    issues = (s.get("structure") or {}).get("issues", [])
    style_issues = (s.get("style") or {}).get("issues", [])
    previous = ""
    history = s.get("fix_history") or []
    if history:
        last = history[-1]
        previous = f"""
YOUR PREVIOUS ATTEMPT (round {last["round"]}) WAS NOT ACCEPTED:
{json.dumps({k: v for k, v in last.items() if k != "changes"})}
Its failing tests:
{_failing_tests(s.get("fix_test_results"))}
Its style issues: {json.dumps((s.get("fix_style") or {}).get("issues", []))}
Its structure issues: {json.dumps((s.get("fix_structure") or {}).get("issues", []))}
Its code:
```python
{s.get("fix_code", "")}
```
Correct what is still wrong.
"""
    tests_section = (
        f"""THE TESTS (they will be run unchanged against your code):
```python
{suite["test_code"]}
```"""
        if suite.get("test_code")
        else "There are no tests (the original code did not parse)."
    )
    return f"""
You are an expert Python developer. Fix the code below so it does what it is
meant to do, passes its unit tests and follows PEP 8. Return the WHOLE fixed
module.

INTENDED BEHAVIOUR: {suite.get("intended_behavior", "judge from names and docstrings")}

ORIGINAL CODE (with line numbers):
```
{numbered(s.get("code_to_review", ""))}
```

PROBLEMS FOUND IN THE REVIEW: {json.dumps((s.get("review_summary") or {}).get("problems"))}
FAILING TESTS ON THE ORIGINAL CODE:
{_failing_tests(s.get("test_results"))}
STRUCTURE FINDINGS: {json.dumps(issues)}
STYLE ISSUES (pycodestyle): {json.dumps(style_issues)}

{tests_section}
{previous}
Rules:
- Keep every function and class name and signature; the tests call them.
- Fix the root cause of each bug. Never special-case the test inputs.
- Replace eval/exec, bare except and mutable default arguments with safe
  equivalents. Add docstrings to public functions and classes.
- PEP 8 with lines of at most 100 characters.
- Standard library only. No prints or input() outside an existing
  `if __name__ == "__main__":` block, which you keep.
- If a test expects behaviour you believe is wrong, keep the code correct
  and list it in disputed_tests with the reason.
- fixed_code is plain Python, without ``` fences.
"""


code_fixer_agent = LlmAgent(
    model=WORKER_MODEL,
    name="CodeFixer",
    description="Fixes the reviewed code.",
    instruction=_fixer_instruction,
    output_schema=CodeFix,
    output_key="fix_attempt",
    include_contents="none",
    disallow_transfer_to_parent=True,
    disallow_transfer_to_peers=True,
)


class FixChecker(BaseAgent):
    """Analyzes the fixed code and rebuilds the review's tests around it."""

    async def _run_async_impl(
        self, ctx: InvocationContext
    ) -> AsyncGenerator[Event, None]:
        state = ctx.session.state
        attempt = as_dict(state.get("fix_attempt"))
        code = strip_fences(attempt.get("fixed_code", "")).strip("\n") + "\n"
        structure = analyze_structure(code)
        style = check_style(code)
        tests = as_dict(state.get("test_suite")).get("test_code")
        script = None
        if structure["syntax_error"]:
            note = f"The fix has a syntax error at {structure['syntax_error']}."
        elif tests:
            script = build_test_script(code, tests)
            note = "Re-running the same tests on the fixed code."
        else:
            note = "The fixed code parses; there are no tests to run."
        yield text_event(
            self.name,
            note,
            {
                "fix_code": code,
                "fix_structure": structure,
                "fix_style": style,
                "fix_script": script,
                "fix_executions": [],
                "fix_test_results": None,
            },
        )


class FixValidator(BaseAgent):
    """Judges the round and keeps the best attempt so far (plain code)."""

    async def _run_async_impl(
        self, ctx: InvocationContext
    ) -> AsyncGenerator[Event, None]:
        state = ctx.session.state
        attempt = as_dict(state.get("fix_attempt"))
        judgement = judge_attempt(
            state.get("fix_structure") or {},
            state.get("fix_style") or {},
            state.get("fix_test_results"),
            has_tests=bool(as_dict(state.get("test_suite")).get("test_code")),
        )
        entry = {
            "round": state.get("fix_round"),
            **judgement,
            "changes": attempt.get("changes", []),
            "disputed_tests": attempt.get("disputed_tests", []),
        }
        delta = {
            "fix_history": list(state.get("fix_history") or []) + [entry],
            "fix_passed": judgement["passed"],
        }
        best = state.get("fix_best")
        if best is None or _rank(judgement) > _rank(best):
            delta["fix_best"] = {**entry, "code": state.get("fix_code")}

        if judgement["syntax_error"]:
            numbers = f"syntax error at {judgement['syntax_error']}"
        elif judgement["tests_verified"]:
            numbers = (
                f"{judgement['tests_passed']} of {judgement['tests_total']} tests "
                f"pass, style {judgement['style_score']}/100"
            )
        else:
            numbers = f"tests not verified, style {judgement['style_score']}/100"
        verdict = "fix accepted" if judgement["passed"] else "not accepted yet"
        yield text_event(
            self.name, f"Round {entry['round']}: {numbers}; {verdict}.", delta
        )


class FixLoop(BaseAgent):
    """Runs fix rounds until one passes or max_rounds is reached.

    A custom loop rather than LoopAgent: LoopAgent stops on any escalate
    event, and the test run inside each round escalates when its results
    are verified, which would end the fix loop after the first round.
    """

    max_rounds: int

    async def _run_async_impl(
        self, ctx: InvocationContext
    ) -> AsyncGenerator[Event, None]:
        for round_number in range(1, self.max_rounds + 1):
            yield Event(
                author=self.name,
                actions=EventActions(state_delta={"fix_round": round_number}),
            )
            for agent in self.sub_agents:
                async for event in agent.run_async(ctx):
                    yield event
            if ctx.session.state.get("fix_passed"):
                return


# ----- Outcome and report -----
class FixOutcome(BaseAgent):
    """Fixed / Improved / Not fixed, decided from the numbers."""

    async def _run_async_impl(
        self, ctx: InvocationContext
    ) -> AsyncGenerator[Event, None]:
        state = ctx.session.state
        before = state["fix_baseline"]
        best = state.get("fix_best")
        outcome = fix_outcome(before, best)
        after = {k: v for k, v in (best or {}).items() if k != "code"}
        summary = {
            "outcome": outcome,
            "rounds": len(state.get("fix_history") or []),
            "best_round": after.get("round"),
            "before": before,
            "after": after,
        }
        delta = {
            "fix_summary": summary,
            "user:fix_count": (state.get("user:fix_count") or 0) + 1,
        }
        yield text_event(self.name, f"Outcome: {outcome}.", delta)


def _reporter_instruction(ctx: ReadonlyContext) -> str:
    s = ctx.state
    summary = s.get("fix_summary") or {}
    best = s.get("fix_best") or {}
    history = [
        {k: v for k, v in h.items() if k != "changes"}
        for h in s.get("fix_history") or []
    ]
    return f"""
You are an expert Python mentor. Explain the fix below using ONLY the facts
provided. Every number must come from the data.

ORIGINAL CODE (with line numbers):
```
{numbered(s.get("code_to_review", ""))}
```

BEST FIXED CODE (round {best.get("round")}):
```python
{best.get("code", "")}
```

OUTCOME (computed, do not change it): {json.dumps(summary)}
CHANGES (from the fixer): {json.dumps(best.get("changes", []))}
TESTS THE FIXER DISPUTED: {json.dumps(best.get("disputed_tests", []))}
ROUNDS: {json.dumps(history)}

Write concise markdown with these sections:
## Result
The outcome in bold, then one sentence (rounds used).
## What changed
Each bug fixed: original line, what was wrong, what changed, why. Then
style and design changes in a short list.
## Before and after
A markdown table with rows tests passing, style score, bug-risk/security
findings; columns before and after.
## Still open
Only if the outcome is not Fixed: what still fails, and any disputed tests.

Do not include the fixed code; it is shown after your report.
"""


async def _save_fix_artifacts(callback_context: CallbackContext) -> None:
    """Saves the report and the fixed code (Artifacts tab in adk web)."""
    state = callback_context.state
    summary = state.get("fix_summary") or {}
    number = state.get("user:fix_count") or 1
    if state.get("fix_report"):
        await callback_context.save_artifact(
            f"fix_report_{number}.md",
            types.Part.from_bytes(
                data=state["fix_report"].encode(), mime_type="text/markdown"
            ),
        )
    if summary.get("outcome") != "Not fixed":
        await callback_context.save_artifact(
            f"fixed_code_{number}.py",
            types.Part.from_bytes(
                data=state["fix_best"]["code"].encode(), mime_type="text/x-python"
            ),
        )


fix_reporter_agent = LlmAgent(
    model=CRITIC_MODEL,
    name="FixReporter",
    description="Explains what the fix changed and how it tested.",
    instruction=_reporter_instruction,
    output_key="fix_report",
    include_contents="none",
    disallow_transfer_to_parent=True,
    disallow_transfer_to_peers=True,
    after_agent_callback=_save_fix_artifacts,
)


class FixedCodeOutput(BaseAgent):
    """Shows the exact tested code; a model never retypes it."""

    async def _run_async_impl(
        self, ctx: InvocationContext
    ) -> AsyncGenerator[Event, None]:
        state = ctx.session.state
        outcome = state["fix_summary"]["outcome"]
        if outcome == "Not fixed":
            text = "No fixed version passed more checks than the original, so keep it."
        elif outcome == "Not verified":
            text = (
                "Best attempt, NOT tested (the sandbox run couldn't be verified):\n"
                f"```python\n{state['fix_best']['code']}```"
            )
        else:
            text = f"Fixed code:\n```python\n{state['fix_best']['code']}```"
        yield text_event(self.name, text)


def build_fix_pipeline(
    fixer: BaseAgent,
    reporter: BaseAgent,
    executor: BaseAgent | None = None,
    max_rounds: int = MAX_FIX_ROUNDS,
) -> SequentialAgent:
    """The fix pipeline; tests pass fakes for the model-backed agents."""
    return SequentialAgent(
        name="CodeFixPipeline",
        description=(
            "Fixes the code from this conversation's last review and re-runs "
            "the review's tests on the fix. Use when the user asks to fix it."
        ),
        sub_agents=[
            FixIntake(name="FixIntake"),
            Gate(
                name="FixStage",
                condition=lambda state: not state.get("fix_error"),
                sub_agents=[
                    FixLoop(
                        name="FixLoop",
                        max_rounds=max_rounds,
                        sub_agents=[
                            fixer,
                            FixChecker(name="FixChecker"),
                            Gate(
                                name="FixTestStage",
                                condition=lambda state: bool(state.get("fix_script")),
                                sub_agents=[
                                    make_test_run(
                                        "FixTestRun",
                                        "fix_script",
                                        "fix_executions",
                                        "fix_test_results",
                                        executor=executor,
                                    )
                                ],
                            ),
                            FixValidator(name="FixValidator"),
                        ],
                    ),
                    FixOutcome(name="FixOutcome"),
                    reporter,
                    FixedCodeOutput(name="FixedCodeOutput"),
                ],
            ),
        ],
    )


code_fix_pipeline = build_fix_pipeline(code_fixer_agent, fix_reporter_agent)
