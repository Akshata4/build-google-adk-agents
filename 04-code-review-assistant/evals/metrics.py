"""Custom adk eval metrics that check results, not wording.

The pipelines' plain-code steps (verdict, outcome, fix offer) report
deterministic messages, so an eval can require them exactly while the
model-written review text is free to vary.
"""

from google.adk.evaluation.eval_case import Invocation, InvocationEvents
from google.adk.evaluation.eval_metrics import EvalMetric, EvalStatus
from google.adk.evaluation.evaluator import EvaluationResult, PerInvocationResult

# Authors whose messages come from code, not from a model.
DETERMINISTIC_STEPS = {
    "CodeIntake",
    "ReviewScorer",
    "FixOffer",
    "FixIntake",
    "FixOutcome",
}
TEST_READERS = {"TestRunReader", "FixTestRunReader"}


def _messages(invocation: Invocation | None, authors: set[str]) -> list[tuple]:
    data = invocation.intermediate_data if invocation else None
    if not isinstance(data, InvocationEvents):
        return []
    found = []
    for event in data.invocation_events:
        if event.author in authors and event.content and event.content.parts:
            text = "".join(p.text or "" for p in event.content.parts).strip()
            found.append((event.author, text))
    return found


def _result(per_invocation: list[PerInvocationResult]) -> EvaluationResult:
    scores = [r.score for r in per_invocation]
    overall = sum(scores) / len(scores) if scores else 1.0
    return EvaluationResult(
        overall_score=overall,
        overall_eval_status=EvalStatus.PASSED if overall == 1.0 else EvalStatus.FAILED,
        per_invocation_results=per_invocation,
    )


def _status(score: float) -> EvalStatus:
    return EvalStatus.PASSED if score == 1.0 else EvalStatus.FAILED


def pipeline_steps_match(
    eval_metric: EvalMetric,
    actual_invocations: list[Invocation],
    expected_invocations: list[Invocation] | None,
    conversation_scenario=None,
) -> EvaluationResult:
    """1 per turn when the code steps said exactly what was expected."""
    expected_invocations = expected_invocations or [None] * len(actual_invocations)
    results = []
    for actual, expected in zip(actual_invocations, expected_invocations, strict=True):
        same = _messages(actual, DETERMINISTIC_STEPS) == _messages(
            expected, DETERMINISTIC_STEPS
        )
        score = 1.0 if same else 0.0
        results.append(
            PerInvocationResult(
                actual_invocation=actual,
                expected_invocation=expected,
                score=score,
                eval_status=_status(score),
            )
        )
    return _result(results)


def tests_verified(
    eval_metric: EvalMetric,
    actual_invocations: list[Invocation],
    expected_invocations: list[Invocation] | None = None,
    conversation_scenario=None,
) -> EvaluationResult:
    """1 per turn when every test run ended with verified results."""
    results = []
    for actual in actual_invocations:
        last_by_reader = dict(_messages(actual, TEST_READERS))
        ok = not any(
            text.startswith("Test results unavailable")
            for text in last_by_reader.values()
        )
        score = 1.0 if ok else 0.0
        results.append(
            PerInvocationResult(
                actual_invocation=actual, score=score, eval_status=_status(score)
            )
        )
    return _result(results)
