"""Static analysis: code extraction, structure and style (no model)."""

import pytest

from code_review_assistant.analysis import (
    analyze_structure,
    check_style,
    extract_code,
    style_score,
)


@pytest.mark.parametrize(
    "message, expected",
    [
        ("Review:\n```python\nx = 1\ny = 2\n```\nthanks", "x = 1\ny = 2"),
        ("```\nx = 1\ny = 2\n```", "x = 1\ny = 2"),
        ("```py\na = 1\n```\n```python\nlonger = 1\nb = 2\n```", "longer = 1\nb = 2"),
        ("Review this\ndef f(a):\n    return a", "def f(a):\n    return a"),
        ("@decorator\ndef f():\n    pass", "@decorator\ndef f():\n    pass"),
        ("import os\nprint(os.name)", "import os\nprint(os.name)"),
        ("what can you do?", None),
        ("fix it", None),
        ("x = 1", None),  # one line of prose-like text isn't treated as code
    ],
)
def test_extract_code(message, expected):
    assert extract_code(message) == expected


def test_code_is_kept_exactly():
    code = "def f( a ):\n\treturn  a  # odd spacing kept\n"
    assert extract_code(f"```python\n{code}```") == code.rstrip("\n")


BUGGY = '''import os
from math import *


def dfs(graph, start):
    """Performs depth-first search on a graph."""
    visited = set()
    stack = start
    while stack:
        current = stack.pop()
        if current not in visited:
            visited.add(current)
            stack.extend(graph[current])
    return visited


def BadName(x, y=[]):
    try:
        return eval(x)
    except:
        pass


class my_class:
    def method(self):
        return 1
'''


def test_structure():
    result = analyze_structure(BUGGY)
    assert result["syntax_error"] is None
    assert [f["name"] for f in result["functions"]] == ["dfs", "BadName", "method"]
    dfs = result["functions"][0]
    assert dfs["has_docstring"] and dfs["args"] == ["graph", "start"]
    assert dfs["complexity"] == 3  # while + if
    assert result["classes"][0]["methods"] == ["method"]
    assert result["imports"] == ["math", "os"]
    kinds = {(i["kind"], i["message"]) for i in result["issues"]}
    assert ("bug-risk", "BadName() has a mutable default argument") in kinds
    assert ("security", "eval() runs arbitrary code") in kinds
    assert ("bug-risk", "bare except: catches everything") in kinds
    assert ("design", "wildcard import from math") in kinds
    assert ("docs", "BadName() has no docstring") in kinds


def test_syntax_error():
    result = analyze_structure("def f(:\n    pass\n")
    assert result["syntax_error"].startswith("line 1:")


def test_style_finds_pep8_and_naming():
    result = check_style(BUGGY)
    codes = result["counts_by_code"]
    assert codes["E722"] == 1  # bare except
    assert codes["N802"] == 1  # BadName
    assert codes["N801"] == 1  # my_class
    assert result["score"] == style_score(
        [{"code": c} for c, n in codes.items() for _ in range(n)]
    )
    assert result["score"] < 100


def test_clean_code_scores_100():
    code = 'def add(a, b):\n    """Add two numbers."""\n    return a + b\n'
    assert check_style(code) == {
        "score": 100,
        "issue_count": 0,
        "counts_by_code": {},
        "issues": [],
    }
    assert analyze_structure(code)["issues"] == []


def test_style_score_weights_and_floor():
    assert style_score([]) == 100
    assert style_score([{"code": "E111"}, {"code": "W291"}]) == 100 - 10 - 2
    assert style_score([{"code": "X999"}]) == 97  # unknown codes cost 3
    assert style_score([{"code": "E111"}] * 20) == 0


def test_style_does_not_print(capsys):
    check_style("x=1\n")
    assert capsys.readouterr().out == ""
