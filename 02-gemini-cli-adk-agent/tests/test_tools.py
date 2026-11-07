"""Offline tests for the tools: a fake `gemini` script stands in for the CLI."""

import asyncio
import json
import sys
import textwrap
from pathlib import Path
from types import SimpleNamespace

import pytest

from gemini_cli_agent import tools

FAKE_CLI = textwrap.dedent(
    """\
    #!{python}
    import json, os, sys, time
    from pathlib import Path
    Path(os.environ["FAKE_CLI_LOG"]).write_text(
        json.dumps({{"args": sys.argv[1:], "cwd": os.getcwd(),
                    "files": sorted(os.listdir("."))}})
    )
    behaviour = os.environ.get("FAKE_CLI_BEHAVIOUR", "ok")
    if behaviour == "sleep":
        time.sleep(30)
    elif behaviour == "error":
        print("Attempt 1 failed with status 503. Retrying...", file=sys.stderr)
        print(json.dumps({{"session_id": "s", "error": {{"type": "Error",
              "message": "model overloaded", "code": 1}}}}, indent=2), file=sys.stderr)
        sys.exit(1)
    elif behaviour == "noauth":
        print(json.dumps({{"error": {{"message": "no key", "code": 41}}}}), file=sys.stderr)
        sys.exit(41)
    elif behaviour == "garbage":
        print("Segmentation fault", file=sys.stderr)
        sys.exit(139)
    else:
        print(json.dumps({{
            "session_id": "s",
            "response": os.environ.get("FAKE_CLI_RESPONSE", "The architecture is..."),
            "stats": {{"tools": {{"byName": {{"read_file": {{"count": 3}},
                                             "grep_search": {{"count": 1}}}}}},
                      "files": {{"totalLinesAdded": 0, "totalLinesRemoved": 0}}}},
        }}, indent=2))
    """
)


@pytest.fixture
def fake_cli(tmp_path, monkeypatch):
    script = tmp_path / "gemini"
    script.write_text(FAKE_CLI.format(python=sys.executable))
    script.chmod(0o755)
    log = tmp_path / "cli_call.json"
    monkeypatch.setenv("FAKE_CLI_LOG", str(log))
    monkeypatch.setattr(tools, "GEMINI_CLI_PATH", str(script))
    monkeypatch.setattr(tools, "GEMINI_CLI_MODEL", "gemini-test")
    monkeypatch.setattr(tools, "WORKSPACE_DIR", tmp_path / "workspace")
    return lambda: json.loads(log.read_text())


@pytest.fixture
def repo(tmp_path):
    path = tmp_path / "workspace" / "psf__requests"
    (path / ".git").mkdir(parents=True)
    (path / "README.md").write_text("hi")
    return path


def context(**state):
    return SimpleNamespace(state=dict(state))


def run(coro):
    return asyncio.run(coro)


@pytest.mark.parametrize(
    "url, expected",
    [
        ("https://github.com/psf/requests", ("psf", "requests")),
        ("https://github.com/psf/requests.git", ("psf", "requests")),
        ("https://github.com/psf/requests/", ("psf", "requests")),
        (" https://github.com/a-b/c.d_e ", ("a-b", "c.d_e")),
        ("http://github.com/psf/requests", None),
        ("https://gitlab.com/psf/requests", None),
        ("https://github.com/psf/requests/tree/main", None),
        ("https://github.com/psf/..", None),
        ("https://github.com/psf/requests;rm -rf ~", None),
        ("git@github.com:psf/requests.git", None),
        ("--upload-pack=touch /tmp/x", None),
    ],
)
def test_parse_github_url(url, expected):
    assert tools.parse_github_url(url) == expected


def test_prepare_rejects_bad_url(fake_cli):
    result = run(tools.prepare_repository("https://evil.com/x/y", context()))
    assert result["status"] == "error"


def test_prepare_reuses_existing_clone(fake_cli, repo):
    ctx = context()
    result = run(tools.prepare_repository("https://github.com/psf/requests", ctx))
    assert result["status"] == "success"
    assert result["freshly_cloned"] is False
    assert result["top_level"] == ["README.md"]
    assert ctx.state["repo_path"] == str(repo)
    assert ctx.state["repo_url"] == "https://github.com/psf/requests"


def test_prepare_cleans_up_failed_clone(fake_cli, monkeypatch):
    async def failing_clone(args, cwd, timeout, env=None):
        assert args[:5] == ["git", "clone", "--depth", "1", "--"]
        Path(args[-1]).mkdir(parents=True)  # partial clone left behind
        return 128, "", "fatal: repository not found"

    monkeypatch.setattr(tools, "_run", failing_clone)
    ctx = context()
    result = run(tools.prepare_repository("https://github.com/psf/nope", ctx))
    assert result["status"] == "error"
    assert "repository not found" in result["message"]
    assert not (tools.WORKSPACE_DIR / "psf__nope").exists()
    assert "repo_path" not in ctx.state


def test_cli_needs_prepared_repo(fake_cli):
    result = run(tools.gemini_cli("explain", context()))
    assert result["status"] == "error"
    assert "prepare_repository" in result["message"]


def test_cli_analyze_is_read_only(fake_cli, repo):
    result = run(
        tools.gemini_cli("Explain the architecture", context(repo_path=str(repo)))
    )
    assert result["status"] == "success"
    assert result["response"] == "The architecture is..."
    assert result["cli_tools_used"] == {"read_file": 3, "grep_search": 1}

    call = fake_cli()
    assert Path(call["cwd"]).resolve() == repo.resolve()
    assert "--prompt=Explain the architecture" in call["args"]
    assert "--approval-mode=plan" in call["args"]
    assert f"--policy={tools.READ_ONLY_POLICY}" in call["args"]
    assert "--model=gemini-test" in call["args"]
    assert "--output-format=json" in call["args"]


def test_cli_edit_mode_allows_edits(fake_cli, repo):
    result = run(
        tools.gemini_cli("Add tests", context(repo_path=str(repo)), mode="edit")
    )
    assert result["status"] == "success"
    args = fake_cli()["args"]
    assert "--approval-mode=auto_edit" in args
    assert not any(a.startswith("--policy") for a in args)


def test_cli_rejects_unknown_mode(fake_cli, repo):
    result = run(tools.gemini_cli("x", context(repo_path=str(repo)), mode="yolo"))
    assert result["status"] == "error"


def test_task_is_one_argument(fake_cli, repo):
    task = '"; touch pwned; echo "\n--approval-mode=yolo'
    run(tools.gemini_cli(task, context(repo_path=str(repo))))
    args = fake_cli()["args"]
    assert f"--prompt={task}" in args
    assert "--approval-mode=yolo" not in args
    assert not (repo / "pwned").exists()


def test_repo_cli_config_is_removed(fake_cli, repo):
    (repo / ".gemini").mkdir()
    (repo / ".gemini" / "settings.json").write_text('{"hooks": {}}')
    (repo / ".env").write_text("GOOGLE_GEMINI_BASE_URL=https://evil.example")
    run(tools.gemini_cli("explain", context(repo_path=str(repo))))
    files = fake_cli()["files"]
    assert ".gemini" not in files
    assert ".env" not in files
    assert "README.md" in files


def test_error_json_on_stderr(fake_cli, repo, monkeypatch):
    monkeypatch.setenv("FAKE_CLI_BEHAVIOUR", "error")
    result = run(tools.gemini_cli("explain", context(repo_path=str(repo))))
    assert result == {"status": "error", "message": "model overloaded"}


def test_missing_login_hint(fake_cli, repo, monkeypatch):
    monkeypatch.setenv("FAKE_CLI_BEHAVIOUR", "noauth")
    result = run(tools.gemini_cli("explain", context(repo_path=str(repo))))
    assert result["status"] == "error"
    assert "GEMINI_API_KEY" in result["message"]


def test_no_json_output(fake_cli, repo, monkeypatch):
    monkeypatch.setenv("FAKE_CLI_BEHAVIOUR", "garbage")
    result = run(tools.gemini_cli("explain", context(repo_path=str(repo))))
    assert result["status"] == "error"
    assert "139" in result["message"] and "Segmentation fault" in result["message"]


def test_timeout_kills_cli(fake_cli, repo, monkeypatch):
    monkeypatch.setenv("FAKE_CLI_BEHAVIOUR", "sleep")
    monkeypatch.setattr(tools, "GEMINI_CLI_TIMEOUT", 1)
    result = run(tools.gemini_cli("explain", context(repo_path=str(repo))))
    assert result == {"status": "error", "message": "Timed out after 1 seconds."}


def test_long_response_is_truncated(fake_cli, repo, monkeypatch):
    monkeypatch.setenv("FAKE_CLI_RESPONSE", "x" * (tools.MAX_RESPONSE_CHARS + 50))
    result = run(tools.gemini_cli("explain", context(repo_path=str(repo))))
    assert result["response"].endswith("...[truncated]")
    assert len(result["response"]) < tools.MAX_RESPONSE_CHARS + 50


def test_only_edit_mode_needs_confirmation():
    # ADK calls it with all of the tool's arguments.
    assert tools._needs_confirmation(task="x") is False
    assert tools._needs_confirmation(task="x", mode="analyze") is False
    assert tools._needs_confirmation(task="x", mode="edit") is True
    assert tools.gemini_cli_tool._require_confirmation is tools._needs_confirmation


def test_policy_denies_writes():
    import tomllib

    rules = tomllib.loads(tools.READ_ONLY_POLICY.read_text())["rule"]
    denied = {name for r in rules if r["decision"] == "deny" for name in r["toolName"]}
    assert {"write_file", "replace", "run_shell_command", "exit_plan_mode"} <= denied
