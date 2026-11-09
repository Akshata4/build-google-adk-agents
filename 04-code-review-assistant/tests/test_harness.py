"""The test script: built here, run locally with python (the agent runs it in
Gemini's sandbox), so the runner itself is tested without a model."""

import subprocess
import sys

import pytest

from code_review_assistant.harness import (
    build_test_script,
    parse_results,
    same_code,
    source_literal,
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
    return visited
'''

FIXED_DFS = DFS.replace("stack = start", "stack = [start]").replace(
    "graph[current]", "graph.get(current, [])"
)

TESTS = """
def test_basic_path():
    assert dfs({"a": ["b"], "b": []}, "a") == {"a", "b"}

def test_edge_missing_key():
    assert dfs({"a": ["z"]}, "a") == {"a", "z"}

def test_error_none_graph():
    expect_raises(AttributeError, dfs, None, "a")
"""


def run(script: str) -> dict:
    out = subprocess.run(
        [sys.executable, "-I", "-c", script],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    return parse_results(out.stdout)


def test_buggy_code_errors():
    result = run(build_test_script(DFS, TESTS))
    assert result["load_error"] is None
    # The None-graph test still passes: it expects an AttributeError anyway.
    assert (result["error"], result["passed"], result["pass_rate"]) == (2, 1, 33)
    message = result["tests"][0]["message"]
    assert "AttributeError: 'str' object has no attribute 'pop'" in message
    assert "submission.py line 6" in message


def test_same_tests_pass_on_fixed_code():
    result = run(build_test_script(FIXED_DFS, TESTS))
    assert (result["passed"], result["total"], result["pass_rate"]) == (3, 3, 100)


def test_failed_assertion_message():
    tests = 'def test_basic_wrong():\n    assert 1 + 1 == 3, "maths"\n'
    result = run(build_test_script("x = 1\n", tests))
    assert result["tests"] == [
        {"name": "test_basic_wrong", "status": "failed", "message": "maths"}
    ]


def test_code_that_crashes_on_load():
    result = run(build_test_script("raise SystemExit(3)\n", TESTS))
    assert result["load_error"] == "SystemExit: 3"
    assert result["total"] == 0


def test_tests_that_fail_to_load():
    result = run(build_test_script(DFS, "def test_x(:\n"))
    assert result["load_error"].startswith("the tests failed to load: SyntaxError")


def test_main_block_does_not_run():
    code = 'if __name__ == "__main__":\n    input("blocks")\n'
    result = run(build_test_script(code, "def test_ok():\n    pass\n"))
    assert result["passed"] == 1


def test_slow_test_times_out():
    tests = "def test_edge_forever():\n    while True:\n        pass\n"
    result = run(build_test_script("x = 1\n", tests))
    assert result["tests"][0]["status"] == "error"
    assert "TimeoutError" in result["tests"][0]["message"]


@pytest.mark.parametrize(
    "text",
    [
        DFS,
        "x = '''a'''\ny = \"\"\"b\"\"\"\n",
        "path = 'C:\\\\temp\\\\'\n",
        "ends_with_backslash = 1 \\",
        's = "\\n"  # escaped newline stays two characters\n',
        "",
    ],
)
def test_source_literal_round_trips(text):
    assert eval(source_literal(text)) == text


def test_script_keeps_code_readable():
    script = build_test_script(DFS, TESTS)
    assert "    stack = start\n" in script  # not one escaped line


def test_same_code_ignores_whitespace_only():
    script = build_test_script(DFS, TESTS)
    assert same_code(script.replace("\n", "\n\n") + "  ", script)
    assert not same_code(script.replace("stack = start", "stack = [start]"), script)
    assert not same_code("", script)


def test_parse_results_without_marker():
    assert parse_results("Traceback ...\nNameError: x") is None
    assert parse_results("RESULTS_JSON:{broken") is None
