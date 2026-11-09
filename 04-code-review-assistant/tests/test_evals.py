"""The eval set and custom metrics, checked offline (adk eval runs the model)."""

import json
from pathlib import Path

from google.adk.evaluation.eval_case import InvocationEvent, InvocationEvents
from google.adk.evaluation.eval_config import EvalConfig
from google.adk.evaluation.eval_metrics import EvalMetric, EvalStatus
from google.adk.evaluation.eval_set import EvalSet
from google.genai import types

from evals import metrics

EVALS = Path(__file__).parent.parent / "evals"
METRIC = EvalMetric(metric_name="m", threshold=1.0)


def load_set() -> EvalSet:
    return EvalSet.model_validate_json((EVALS / "code_review.evalset.json").read_text())


def text_event(author, text):
    return InvocationEvent(
        author=author,
        content=types.Content(role="model", parts=[types.Part(text=text)]),
    )


def test_eval_set_loads():
    cases = {c.eval_id: c for c in load_set().eval_cases}
    assert set(cases) == {
        "explains_without_code",
        "fix_without_review",
        "reviews_buggy_code",
        "clean_code_is_ready",
        "review_then_fix",
    }
    assert len(cases["review_then_fix"].conversation) == 2
    review = cases["reviews_buggy_code"].conversation[0]
    assert isinstance(review.intermediate_data, InvocationEvents)
    calls = [
        p.function_call
        for e in review.intermediate_data.invocation_events
        for p in e.content.parts
        if p.function_call
    ]
    assert [(c.name, c.args) for c in calls] == [
        ("transfer_to_agent", {"agent_name": "CodeReviewPipeline"})
    ]


def test_config_names_the_custom_metrics():
    config = EvalConfig.model_validate(
        json.loads((EVALS / "test_config.json").read_text())
    )
    assert set(config.criteria) == {
        "tool_trajectory_avg_score",
        "pipeline_steps_match",
        "tests_verified",
    }
    for name in ("pipeline_steps_match", "tests_verified"):
        assert config.custom_metrics[name].code_config.name == f"evals.metrics.{name}"


def test_expected_conversations_score_one_against_themselves():
    for case in load_set().eval_cases:
        turns = case.conversation
        result = metrics.pipeline_steps_match(METRIC, turns, turns)
        assert result.overall_score == 1.0, case.eval_id
        assert (
            metrics.tests_verified(METRIC, turns).overall_eval_status
            == EvalStatus.PASSED
        )


def test_changed_verdict_fails():
    case = next(c for c in load_set().eval_cases if c.eval_id == "reviews_buggy_code")
    expected = case.conversation
    actual = [turn.model_copy(deep=True) for turn in expected]
    events = actual[0].intermediate_data.invocation_events
    events[2] = text_event("ReviewScorer", "Verdict: Not verified.")
    # Model-written text between the steps doesn't matter.
    events.insert(2, text_event("FeedbackWriter", "## Summary\nany wording"))
    result = metrics.pipeline_steps_match(METRIC, actual, expected)
    assert result.overall_score == 0.0
    assert result.overall_eval_status == EvalStatus.FAILED


def test_unverified_test_run_fails_only_if_never_verified():
    case = next(c for c in load_set().eval_cases if c.eval_id == "reviews_buggy_code")
    turn = case.conversation[0].model_copy(deep=True)
    events = turn.intermediate_data.invocation_events
    events.append(text_event("TestRunReader", "Test results unavailable: x."))
    assert metrics.tests_verified(METRIC, [turn]).overall_score == 0.0
    events.append(
        text_event("TestRunReader", "Tests: 0 passed, 0 failed, 7 errors (of 7).")
    )
    assert (
        metrics.tests_verified(METRIC, [turn]).overall_score == 1.0
    )  # the retry worked
