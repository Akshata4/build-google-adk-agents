"""Static analysis that needs no model: code extraction, structure, style.

These run as plain code so the results are exact and free, and so the
submitted code is never retyped by a model before it is analyzed.
"""

import ast
import re

import pycodestyle

# Deduction per issue, by code prefix (same weights as the reference).
STYLE_WEIGHTS = {
    "E1": 10,  # indentation
    "E2": 3,  # whitespace
    "E3": 5,  # blank lines
    "E4": 8,  # imports
    "E5": 5,  # line length
    "E7": 7,  # statements
    "E9": 10,  # syntax / IO
    "W2": 2,  # whitespace warnings
    "W3": 2,  # blank line warnings
    "W5": 3,  # line break warnings
    "N8": 7,  # naming
}
MAX_LINE_LENGTH = 100
LONG_FUNCTION_LINES = 50
HIGH_COMPLEXITY = 10

_FENCE = re.compile(
    r"```[ \t]*(?:python|py|python3)?[ \t]*\n(.*?)```", re.DOTALL | re.IGNORECASE
)


def extract_code(message: str) -> str | None:
    """Returns the Python code in a chat message, exactly as written.

    Takes the longest fenced block (```python ... ```) if there is one.
    Otherwise accepts the whole message when it looks like code: it defines
    a function or class, or parses as Python with more than one line.
    """
    blocks = [b.strip("\n") for b in _FENCE.findall(message)]
    if blocks:
        return max(blocks, key=len)
    text = message.strip("\n")
    if re.search(r"^\s*(def|class|async def)\s+\w+", text, re.MULTILINE):
        # A short request line before the code ("Review this:") is dropped.
        match = re.search(
            r"^(?:@|def |class |async def |import |from )", text, re.MULTILINE
        )
        return text[match.start() :] if match else text
    try:
        ast.parse(text)
    except SyntaxError:
        return None
    return text if "\n" in text else None


def _complexity(node: ast.AST) -> int:
    """Approximate cyclomatic complexity: 1 + decision points."""
    decisions = (
        ast.If,
        ast.For,
        ast.AsyncFor,
        ast.While,
        ast.IfExp,
        ast.ExceptHandler,
        ast.With,
        ast.AsyncWith,
        ast.Assert,
        ast.comprehension,
        ast.match_case,
    )
    score = 1
    for child in ast.walk(node):
        if isinstance(child, decisions):
            score += 1
        elif isinstance(child, ast.BoolOp):
            score += len(child.values) - 1
    return score


def analyze_structure(code: str) -> dict:
    """Functions, classes and imports, plus structural issues found by AST."""
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return {
            "syntax_error": f"line {e.lineno}: {e.msg}",
            "line_count": len(code.splitlines()),
        }

    functions, classes, imports, issues = [], [], [], []

    def issue(line: int, kind: str, message: str):
        issues.append({"line": line, "kind": kind, "message": message})

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            length = (node.end_lineno or node.lineno) - node.lineno + 1
            complexity = _complexity(node)
            functions.append(
                {
                    "name": node.name,
                    "line": node.lineno,
                    "args": [a.arg for a in node.args.args],
                    "has_docstring": ast.get_docstring(node) is not None,
                    "lines": length,
                    "complexity": complexity,
                }
            )
            if not node.name.startswith("_") and ast.get_docstring(node) is None:
                issue(node.lineno, "docs", f"{node.name}() has no docstring")
            if length > LONG_FUNCTION_LINES:
                issue(node.lineno, "design", f"{node.name}() is {length} lines long")
            if complexity > HIGH_COMPLEXITY:
                issue(
                    node.lineno,
                    "design",
                    f"{node.name}() has complexity {complexity} (over {HIGH_COMPLEXITY})",
                )
            for default in node.args.defaults + node.args.kw_defaults:
                if isinstance(default, (ast.List, ast.Dict, ast.Set)):
                    issue(
                        node.lineno,
                        "bug-risk",
                        f"{node.name}() has a mutable default argument",
                    )
        elif isinstance(node, ast.ClassDef):
            classes.append(
                {
                    "name": node.name,
                    "line": node.lineno,
                    "methods": [
                        n.name
                        for n in node.body
                        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                    ],
                    "has_docstring": ast.get_docstring(node) is not None,
                }
            )
        elif isinstance(node, ast.Import):
            imports.extend(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            imports.append(node.module or ".")
            if any(a.name == "*" for a in node.names):
                issue(node.lineno, "design", f"wildcard import from {node.module}")
        elif isinstance(node, ast.ExceptHandler) and node.type is None:
            issue(node.lineno, "bug-risk", "bare except: catches everything")
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in ("eval", "exec")
        ):
            issue(node.lineno, "security", f"{node.func.id}() runs arbitrary code")

    return {
        "syntax_error": None,
        "line_count": len(code.splitlines()),
        "functions": functions,
        "classes": classes,
        "imports": sorted(set(imports)),
        "issues": sorted(issues, key=lambda i: i["line"]),
    }


class _CollectingReport(pycodestyle.BaseReport):
    """Collects pycodestyle results instead of printing them."""

    def __init__(self, options):
        super().__init__(options)
        self.issues = []

    def error(self, line_number, offset, text, check):
        code = super().error(line_number, offset, text, check)
        if code:
            self.issues.append(
                {
                    "line": line_number,
                    "column": offset + 1,
                    "code": code,
                    "message": text[5:],
                }
            )
        return code


def _naming_issues(code: str) -> list[dict]:
    """PEP 8 naming checks pycodestyle doesn't do (N801, N802)."""
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return []
    issues = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name != node.name.lower() and not node.name.startswith("__"):
                issues.append(
                    {
                        "line": node.lineno,
                        "column": node.col_offset + 1,
                        "code": "N802",
                        "message": f"function name '{node.name}' should be lowercase",
                    }
                )
        elif isinstance(node, ast.ClassDef) and (
            not node.name[:1].isupper() or "_" in node.name.strip("_")
        ):
            issues.append(
                {
                    "line": node.lineno,
                    "column": node.col_offset + 1,
                    "code": "N801",
                    "message": f"class name '{node.name}' should use CapWords",
                }
            )
    return issues


def style_score(issues: list[dict]) -> int:
    """100 minus a weighted deduction per issue, never below 0."""
    deduction = sum(STYLE_WEIGHTS.get(i["code"][:2], 3) for i in issues)
    return max(0, 100 - deduction)


def check_style(code: str, max_issues: int = 15) -> dict:
    """PEP 8 check with pycodestyle plus naming, and a 0-100 score."""
    guide = pycodestyle.StyleGuide(
        quiet=True,
        reporter=_CollectingReport,
        max_line_length=MAX_LINE_LENGTH,
        # W503 and W504 contradict each other; PEP 8 now prefers W504's style.
        ignore=["W503", "W504"],
    )
    report = guide.init_report(_CollectingReport)
    guide.input_file("submission.py", lines=code.splitlines(keepends=True))
    issues = sorted(
        report.issues + _naming_issues(code), key=lambda i: (i["line"], i["column"])
    )
    counts: dict[str, int] = {}
    for i in issues:
        counts[i["code"]] = counts.get(i["code"], 0) + 1
    return {
        "score": style_score(issues),
        "issue_count": len(issues),
        "counts_by_code": counts,
        "issues": issues[:max_issues],
    }
