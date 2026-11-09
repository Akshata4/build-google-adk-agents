"""Fakes for the model-backed agents, shared by the pipeline tests."""

import asyncio
import subprocess
import sys

from google.adk.agents import BaseAgent
from google.adk.events import Event, EventActions


class FakeExecutor(BaseAgent):
    """Runs the script locally and records it like record_executions does.

    `tamper` simulates a model changing the code before running it.
    """

    script_key: str = "test_script"
    executions_key: str = "test_executions"
    tamper: bool = False

    async def _run_async_impl(self, ctx):
        script = ctx.session.state[self.script_key]
        if self.tamper:
            script += "\n_changed_by_model = True\n"
        out = await asyncio.to_thread(
            subprocess.run,
            [sys.executable, "-I", "-c", script],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        execution = {"code": script, "outcome": "OUTCOME_OK", "output": out.stdout}
        yield Event(
            author=self.name,
            actions=EventActions(state_delta={self.executions_key: [execution]}),
        )
