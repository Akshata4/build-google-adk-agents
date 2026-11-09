"""Plugins and SQLite sessions, without real model calls.

A fake model returns a fixed reply with fixed token usage, so the counts
are exact. Each "restart" builds a new Runner and session service on the
same database file.
"""

import asyncio
from datetime import date

from google.adk.agents import LlmAgent
from google.adk.apps import App
from google.adk.models import BaseLlm, LlmResponse
from google.adk.runners import Runner
from google.adk.sessions.sqlite_session_service import SqliteSessionService
from google.genai import types
from test_review_pipeline import DFS, pipeline

from code_review_assistant.agent import app as real_app
from code_review_assistant.plugins import (
    DAILY_USAGE_KEY,
    USAGE_KEY,
    LimitsPlugin,
    UsagePlugin,
)

USAGE = types.GenerateContentResponseUsageMetadata(
    prompt_token_count=100,
    candidates_token_count=20,
    thoughts_token_count=5,
    total_token_count=125,
)


class FakeLlm(BaseLlm):
    model: str = "fake"
    calls: int = 0

    async def generate_content_async(self, llm_request, stream=False):
        self.calls += 1
        yield LlmResponse(
            content=types.Content(role="model", parts=[types.Part(text="hello")]),
            usage_metadata=USAGE,
        )


def make_app(agent, plugins):
    return App(name="t", root_agent=agent, plugins=plugins)


async def send(runner, session_id, text, user_id="u"):
    message = types.Content(role="user", parts=[types.Part(text=text)])
    texts = []
    async for event in runner.run_async(
        user_id=user_id, session_id=session_id, new_message=message
    ):
        if event.content and event.content.parts:
            texts.append("".join(p.text or "" for p in event.content.parts))
    return texts


def test_usage_is_counted_and_survives_restart(tmp_path):
    db = str(tmp_path / "sessions.db")

    async def go():
        llm = FakeLlm()
        runner = Runner(
            app=make_app(LlmAgent(name="Echo", model=llm), [UsagePlugin()]),
            session_service=SqliteSessionService(db),
        )
        session = await runner.session_service.create_session(app_name="t", user_id="u")
        await send(runner, session.id, "one")
        await send(runner, session.id, "two")

        # Restart: new runner and service on the same file.
        service = SqliteSessionService(db)
        restored = await service.get_session(
            app_name="t", user_id="u", session_id=session.id
        )
        other = await service.create_session(app_name="t", user_id="u")
        return llm.calls, restored, other

    calls, restored, other = asyncio.run(go())
    assert calls == 2
    usage = restored.state[USAGE_KEY]
    assert usage["model_calls"] == 2
    assert (usage["prompt_tokens"], usage["output_tokens"]) == (200, 40)
    assert usage["thinking_tokens"] == 10
    assert usage["by_agent"]["Echo"]["model_calls"] == 2
    assert [e.author for e in restored.events].count("user") == 2
    # The daily total is per user, so a new session of the same user sees it.
    daily = other.state[DAILY_USAGE_KEY]
    assert (daily["date"], daily["model_calls"]) == (date.today().isoformat(), 2)


def test_review_history_survives_restart(tmp_path):
    db = str(tmp_path / "sessions.db")
    fixed = DFS.replace("stack = start", "stack = [start]")

    async def review_once(code):
        runner = Runner(
            app=make_app(pipeline(), []), session_service=SqliteSessionService(db)
        )
        session = await runner.session_service.create_session(app_name="t", user_id="u")
        await send(runner, session.id, f"```python\n{code}\n```")
        return (
            await runner.session_service.get_session(
                app_name="t", user_id="u", session_id=session.id
            )
        ).state

    first = asyncio.run(review_once(DFS))
    second = asyncio.run(review_once(fixed))  # a new process would see the same
    assert first["review_summary"]["verdict"] == "Needs major fixes"
    assert second["user:review_count"] == 2
    assert second["review_summary"]["previous_review"]["pass_rate"] == 0


def test_long_message_is_refused_before_any_model_call():
    async def go():
        llm = FakeLlm()
        runner = Runner(
            app=make_app(
                LlmAgent(name="Echo", model=llm),
                [LimitsPlugin(max_message_chars=50), UsagePlugin()],
            ),
            session_service=SqliteSessionService(":memory:"),
        )
        session = await runner.session_service.create_session(app_name="t", user_id="u")
        texts = await send(runner, session.id, "x" * 51)
        return llm.calls, texts

    calls, texts = asyncio.run(go())
    assert calls == 0
    assert texts == [
        (
            "That message is 51 characters; the limit is 50. Please send one "
            "module or a few functions at a time."
        )
    ]


def test_daily_budget_applies_across_sessions():
    async def go():
        llm = FakeLlm()
        runner = Runner(
            app=make_app(
                LlmAgent(name="Echo", model=llm),
                [LimitsPlugin(max_calls_per_day=2), UsagePlugin()],
            ),
            session_service=SqliteSessionService(":memory:"),
        )
        replies = []
        for _ in range(3):
            session = await runner.session_service.create_session(
                app_name="t", user_id="u"
            )
            replies.append(await send(runner, session.id, "hi"))
        # Another user has their own budget.
        session = await runner.session_service.create_session(
            app_name="t", user_id="other"
        )
        replies.append(await send(runner, session.id, "hi", user_id="other"))
        return llm.calls, replies

    calls, replies = asyncio.run(go())
    assert calls == 3
    assert replies[:2] == [["hello"], ["hello"]]
    assert "today's limit of 2 model calls" in replies[2][0]
    assert replies[3] == ["hello"]


def test_real_app_has_the_plugins():
    assert real_app.name == "code_review_assistant"
    assert [type(p) for p in real_app.plugins] == [LimitsPlugin, UsagePlugin]
    assert real_app.root_agent.name == "CodeReviewAssistant"
