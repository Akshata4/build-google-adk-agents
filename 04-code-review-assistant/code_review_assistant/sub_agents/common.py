"""Building blocks shared by the review and fix pipelines."""

import json
import warnings
from collections.abc import AsyncGenerator, Callable

from google.adk.agents import BaseAgent, LlmAgent, LoopAgent
from google.adk.agents.callback_context import CallbackContext
from google.adk.agents.invocation_context import InvocationContext
from google.adk.agents.readonly_context import ReadonlyContext
from google.adk.code_executors import BuiltInCodeExecutor
from google.adk.events import Event, EventActions
from google.adk.models import LlmResponse
from google.genai import types

from ..config import WORKER_MODEL
from ..harness import parse_results, same_code

# Sequential/LoopAgent are deprecated in favour of Workflow in ADK 2.x, but
# Workflow can't yet be a sub-agent of an LlmAgent (the root routes to the
# pipelines by agent transfer), so keep them.
warnings.filterwarnings("ignore", message=r".*(Sequential|Loop)Agent is deprecated.*")


def text_event(author: str, text: str, state_delta: dict | None = None) -> Event:
    return Event(
        author=author,
        content=types.Content(role="model", parts=[types.Part(text=text)]),
        actions=EventActions(state_delta=state_delta or {}),
    )


def as_dict(value) -> dict:
    """Structured output from state: a dict, or JSON text in older sessions."""
    if isinstance(value, str):
        return json.loads(value)
    return value or {}


class Gate(BaseAgent):
    """Runs its sub-agents in order while `condition(state)` holds."""

    condition: Callable[[dict], bool]

    async def _run_async_impl(
        self, ctx: InvocationContext
    ) -> AsyncGenerator[Event, None]:
        for agent in self.sub_agents:
            if not self.condition(ctx.session.state):
                return
            async for event in agent.run_async(ctx):
                yield event


def record_executions(executions_key: str):
    """after_model_callback that saves what the code executor really ran.

    Gemini returns the executed code and its output as response parts. They
    are stored in state, so results are read from the actual output and the
    code can be compared with the script the model was asked to run.
    """

    def callback(callback_context: CallbackContext, llm_response: LlmResponse):
        parts = llm_response.content.parts if llm_response.content else None
        state = callback_context.state
        for part in parts or []:
            if part.executable_code:
                state[f"temp:{executions_key}_code"] = part.executable_code.code
            if part.code_execution_result:
                result = part.code_execution_result
                state[executions_key] = list(state.get(executions_key) or []) + [
                    {
                        "code": state.get(f"temp:{executions_key}_code") or "",
                        "outcome": str(result.outcome),
                        "output": result.output or "",
                    }
                ]

    return callback


def make_executor(
    name: str, script_key: str, executions_key: str, output_key: str
) -> LlmAgent:
    """An agent that runs the script in state[script_key] in Gemini's sandbox."""

    def instruction(ctx: ReadonlyContext) -> str:
        retry = ""
        previous = ctx.state.get(output_key)
        if previous and previous.get("status") == "unverified":
            # Within the retry loop: say why the last attempt was rejected.
            retry = (
                f"\nYour previous attempt was rejected: {previous['reason']}. "
                "Copying the code character for character is the whole task; "
                "do not write your own tests or harness.\n"
            )
        return f"""
Run the following Python code with your code execution tool, exactly once
and exactly as written: do not change, shorten, reformat or fix it, even if
it looks wrong. You MUST actually execute it; never predict, simulate or
write its output yourself. After it runs, reply only with: Tests executed.
{retry}
{ctx.state.get(script_key, "")}
"""

    return LlmAgent(
        model=WORKER_MODEL,
        name=name,
        description="Runs the generated test script in the code execution sandbox.",
        instruction=instruction,
        code_executor=BuiltInCodeExecutor(),
        include_contents="none",
        disallow_transfer_to_parent=True,
        disallow_transfer_to_peers=True,
        after_model_callback=record_executions(executions_key),
    )


def read_test_results(executions: list[dict], script: str) -> dict:
    """Test results from the run of exactly `script`, or why there are none."""
    runs = [e for e in executions or [] if same_code(e["code"], script)]
    if not runs:
        reason = (
            "the sandbox ran different code than the generated script"
            if executions
            else "the tests were not executed"
        )
        return {"status": "unverified", "reason": reason}
    run = runs[-1]
    results = parse_results(run["output"])
    if results is None:
        return {
            "status": "unverified",
            "reason": f"no results in the output (outcome {run['outcome']})",
            "output": run["output"][-1000:],
        }
    return {"status": "verified", **results}


class ResultReader(BaseAgent):
    """Turns the recorded execution into test results (plain code)."""

    script_key: str
    executions_key: str
    output_key: str

    async def _run_async_impl(
        self, ctx: InvocationContext
    ) -> AsyncGenerator[Event, None]:
        state = ctx.session.state
        results = read_test_results(
            state.get(self.executions_key) or [], state.get(self.script_key) or ""
        )
        if results["status"] == "verified":
            if results.get("load_error"):
                note = f"The code failed to load: {results['load_error']}"
            else:
                note = (
                    f"Tests: {results['passed']} passed, {results['failed']} failed, "
                    f"{results['error']} errors (of {results['total']})."
                )
        else:
            note = f"Test results unavailable: {results['reason']}."
        # Inside a retry loop, verified results end the loop.
        yield Event(
            author=self.name,
            content=types.Content(role="model", parts=[types.Part(text=note)]),
            actions=EventActions(
                state_delta={self.output_key: results},
                escalate=results["status"] == "verified",
            ),
        )


def make_test_run(
    name: str,
    script_key: str,
    executions_key: str,
    output_key: str,
    attempts: int = 2,
    executor: BaseAgent | None = None,
) -> LoopAgent:
    """Executor + reader, retried when the model didn't really run the code.

    Models sometimes answer with made-up output instead of using code
    execution. That is detected (no matching execution) and retried.
    `executor` replaces the sandbox agent (tests use a local one).
    """
    return LoopAgent(
        name=name,
        max_iterations=attempts,
        sub_agents=[
            executor
            or make_executor(f"{name}Executor", script_key, executions_key, output_key),
            ResultReader(
                name=f"{name}Reader",
                script_key=script_key,
                executions_key=executions_key,
                output_key=output_key,
            ),
        ],
    )
