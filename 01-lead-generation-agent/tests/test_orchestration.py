"""Offline tests for the parallel fan-out and result collection.

Fake agents stand in for the Gemini-backed ones, so this checks the ADK
plumbing (dynamic ParallelAgent, state hand-off, stale-key reset, ranking)
without spending any quota.
"""

import asyncio
from collections.abc import AsyncGenerator

from google.adk.agents import BaseAgent, SequentialAgent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event, EventActions
from google.adk.runners import InMemoryRunner
from google.genai import types

from lead_generation_agent.sub_agents.common import (
    ParallelResearchOrchestrator,
    ResultCollector,
)


class WriteState(BaseAgent):
    """Writes a fixed state delta, like an LlmAgent with output_key would."""

    delta: dict

    async def _run_async_impl(
        self, ctx: InvocationContext
    ) -> AsyncGenerator[Event, None]:
        yield Event(author=self.name, actions=EventActions(state_delta=self.delta))


def _items(state) -> list[dict]:
    return state.get("items") or []


def _pipeline(i: int) -> BaseAgent:
    # Item 1 fails validation; scores are deliberately out of order.
    return WriteState(
        name=f"Fake_{i}",
        delta={
            f"result_{i}": {
                "validated": i != 1,
                "fit_score": [40, 99, 90][i],
                "signals": [{"description": f"signal {i}", "source_number": 1}],
            },
            f"src_{i}": [{"title": f"site{i}.com", "url": f"https://{i}"}],
        },
    )


def _run(initial_state: dict) -> dict:
    pipeline = SequentialAgent(
        name="Pipeline",
        sub_agents=[
            ParallelResearchOrchestrator(
                name="Orchestrator",
                get_items=_items,
                build_pipeline=_pipeline,
                reset_prefixes=["result_", "src_"],
            ),
            ResultCollector(
                name="Collector",
                get_items=_items,
                result_prefix="result_",
                sources_prefix="src_",
                output_key="all_results",
                sort_by="fit_score",
            ),
        ],
    )

    async def main() -> dict:
        runner = InMemoryRunner(agent=pipeline, app_name="test")
        session = await runner.session_service.create_session(
            app_name="test", user_id="u", state=initial_state
        )
        message = types.Content(role="user", parts=[types.Part(text="go")])
        async for _ in runner.run_async(
            user_id="u", session_id=session.id, new_message=message
        ):
            pass
        session = await runner.session_service.get_session(
            app_name="test", user_id="u", session_id=session.id
        )
        return session.state

    return asyncio.run(main())


def test_parallel_results_are_validated_ranked_and_cited():
    state = _run({"items": [{}, {}, {}]})
    results = state["all_results"]
    # Invalid item 1 dropped; remaining ranked by fit_score.
    assert [r["fit_score"] for r in results] == [90, 40]
    assert results[0]["signals"][0]["source"] == {
        "title": "site2.com",
        "url": "https://2",
    }


def test_stale_keys_from_a_previous_run_are_cleared():
    state = _run({"items": [{}], "result_5": {"validated": True}, "src_5": ["old"]})
    assert state.get("result_5") is None
    assert state.get("src_5") is None
    assert len(state["all_results"]) == 1


def test_no_items_yields_an_empty_report():
    state = _run({"items": []})
    assert state["all_results"] == []
