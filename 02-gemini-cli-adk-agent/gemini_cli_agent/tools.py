"""Tools that let the ADK agent clone a repository and hand work to Gemini CLI.

Both run external programs (git, gemini). Arguments are always passed as a
list, never through a shell, so a crafted URL or task can't run extra
commands.
"""

import asyncio
import json
import os
import re
import shutil
from pathlib import Path

from google.adk.tools import FunctionTool, ToolContext

from .config import (
    GEMINI_CLI_MODEL,
    GEMINI_CLI_PATH,
    GEMINI_CLI_TIMEOUT,
    WORKSPACE_DIR,
)

POLICY_DIR = Path(__file__).resolve().parent / "policies"
READ_ONLY_POLICY = POLICY_DIR / "read_only.toml"
CLONE_TIMEOUT = 300
MAX_RESPONSE_CHARS = 20_000

GITHUB_URL = re.compile(
    r"^https://github\.com/([A-Za-z0-9-]{1,39})/([A-Za-z0-9._-]{1,100}?)(?:\.git)?/?$"
)

# Files in a repository that Gemini CLI loads as configuration. Headless runs
# need --skip-trust, which makes the CLI trust the workspace, so a cloned repo
# could otherwise add hooks or MCP servers (which run commands) or change the
# CLI's environment. They are deleted from the clone before every run.
UNTRUSTED_CLI_CONFIG = (".gemini", ".env")


def parse_github_url(url: str) -> tuple[str, str] | None:
    """Returns (owner, repo) for https://github.com/owner/repo, else None."""
    match = GITHUB_URL.match(url.strip())
    if not match or match.group(2) in (".", ".."):
        return None
    return match.group(1), match.group(2)


async def _run(
    args: list[str], cwd: Path, timeout: int, env: dict | None = None
) -> tuple[int, str, str]:
    """Runs a command without a shell. Returns (exit code, stdout, stderr).

    The process is killed on timeout and the exit code is -1.
    """
    process = await asyncio.create_subprocess_exec(
        *args,
        cwd=cwd,
        env=env,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout)
    except TimeoutError:
        process.kill()
        await process.wait()
        return -1, "", f"Timed out after {timeout} seconds."
    return (
        process.returncode,
        stdout.decode(errors="replace"),
        stderr.decode(errors="replace"),
    )


async def prepare_repository(github_url: str, tool_context: ToolContext) -> dict:
    """Clones a public GitHub repository so Gemini CLI can work on it.

    Call this first, before gemini_cli. Re-using the same URL keeps the
    existing copy, including any changes made to it.

    Args:
        github_url: Repository URL in the form https://github.com/owner/repo.

    Returns:
        status, the local folder name and the top-level files.
    """
    parsed = parse_github_url(github_url)
    if not parsed:
        return {
            "status": "error",
            "message": "Only public repositories as https://github.com/owner/repo are supported.",
        }
    owner, repo = parsed
    path = WORKSPACE_DIR / f"{owner}__{repo}"

    if not (path / ".git").is_dir():
        if shutil.which("git") is None:
            return {"status": "error", "message": "git is not installed."}
        WORKSPACE_DIR.mkdir(parents=True, exist_ok=True)
        shutil.rmtree(path, ignore_errors=True)  # leftovers of a failed clone
        code, _, stderr = await _run(
            [
                "git",
                "clone",
                "--depth",
                "1",
                "--",
                f"https://github.com/{owner}/{repo}.git",
                str(path),
            ],
            cwd=WORKSPACE_DIR,
            timeout=CLONE_TIMEOUT,
            # Fail instead of waiting for a password on private/missing repos.
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
        )
        if code != 0:
            shutil.rmtree(path, ignore_errors=True)
            return {
                "status": "error",
                "message": f"Clone failed: {stderr.strip()[-500:]}",
            }
        cloned = True
    else:
        cloned = False

    tool_context.state["repo_url"] = f"https://github.com/{owner}/{repo}"
    tool_context.state["repo_path"] = str(path)
    return {
        "status": "success",
        "repository": f"{owner}/{repo}",
        "local_folder": path.name,
        "freshly_cloned": cloned,
        "top_level": sorted(p.name for p in path.iterdir() if p.name != ".git"),
    }


def remove_cli_config(repo_path: Path) -> list[str]:
    """Deletes the repo's own Gemini CLI configuration. Returns what was removed."""
    removed = []
    for name in UNTRUSTED_CLI_CONFIG:
        path = repo_path / name
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path)
        elif path.exists() or path.is_symlink():
            path.unlink()
        else:
            continue
        removed.append(name)
    return removed


def _extract_json(text: str) -> dict | None:
    """Finds the CLI's JSON result in its output.

    On success it's all of stdout. On errors it's on stderr after log lines,
    so try each line that starts a JSON object.
    """
    decoder = json.JSONDecoder()
    for match in re.finditer(r"^\{", text, re.MULTILINE):
        try:
            value, _ = decoder.raw_decode(text, match.start())
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict) and ("response" in value or "error" in value):
            return value
    return None


def build_cli_args(task: str, mode: str) -> list[str]:
    """Command line for one headless Gemini CLI run."""
    args = [GEMINI_CLI_PATH, f"--prompt={task}", "--output-format=json", "--skip-trust"]
    if GEMINI_CLI_MODEL:
        args.append(f"--model={GEMINI_CLI_MODEL}")
    if mode == "edit":
        # Edits are auto-approved; shell commands would need approval, which
        # headless mode turns into a denial.
        args.append("--approval-mode=auto_edit")
    else:
        # Plan mode alone isn't read-only headless; the policy makes it so.
        args += ["--approval-mode=plan", f"--policy={READ_ONLY_POLICY}"]
    return args


async def gemini_cli(
    task: str, tool_context: ToolContext, mode: str = "analyze"
) -> dict:
    """Delegates a task on the prepared repository to Gemini CLI.

    Gemini CLI is a coding agent: it reads files, searches the code and
    reasons over the whole repository by itself. Give it one complete,
    specific task, e.g. "Explain the architecture: main modules, how a request
    flows, key design patterns. Cite file paths."

    Args:
        task: What Gemini CLI should do, in plain language.
        mode: "analyze" (default) reads the code but never changes it.
            "edit" also lets it create and modify files in the cloned copy;
            the user is asked to confirm first.

    Returns:
        status, Gemini CLI's answer and which of its tools it used.
    """
    repo_path = tool_context.state.get("repo_path")
    if not repo_path or not Path(repo_path).is_dir():
        return {
            "status": "error",
            "message": "No repository prepared. Call prepare_repository first.",
        }
    if mode not in ("analyze", "edit"):
        return {"status": "error", "message": 'mode must be "analyze" or "edit".'}
    if shutil.which(GEMINI_CLI_PATH) is None:
        return {
            "status": "error",
            "message": f"Gemini CLI not found ({GEMINI_CLI_PATH}). Install: npm install -g @google/gemini-cli",
        }

    # Also covers a .gemini folder written by an earlier "edit" run.
    remove_cli_config(Path(repo_path))
    code, stdout, stderr = await _run(
        build_cli_args(task, mode), cwd=Path(repo_path), timeout=GEMINI_CLI_TIMEOUT
    )
    if code == -1:
        return {"status": "error", "message": stderr}

    result = _extract_json(stdout) or _extract_json(stderr)
    if result is None:
        return {
            "status": "error",
            "message": f"Gemini CLI exited with code {code} and no JSON output: "
            f"{(stderr or stdout).strip()[-500:]}",
        }
    if "error" in result:
        error = result["error"] or {}
        message = error.get("message", str(error))
        if error.get("code") == 41:
            message += (
                " (Gemini CLI couldn't find its login. Run `gemini` once in a"
                " terminal to log in, or set GEMINI_API_KEY in .env.)"
            )
        return {"status": "error", "message": message}

    response = result.get("response") or ""
    if len(response) > MAX_RESPONSE_CHARS:
        response = response[:MAX_RESPONSE_CHARS] + "\n...[truncated]"
    stats = result.get("stats") or {}
    tools = (stats.get("tools") or {}).get("byName") or {}
    files = stats.get("files") or {}
    return {
        "status": "success",
        "mode": mode,
        "response": response,
        "cli_tools_used": {name: t.get("count", 0) for name, t in tools.items()},
        "lines_added": files.get("totalLinesAdded", 0),
        "lines_removed": files.get("totalLinesRemoved", 0),
    }


def _needs_confirmation(mode: str = "analyze", **_) -> bool:
    # ADK passes all of gemini_cli's arguments; only the mode matters.
    return mode == "edit"


prepare_repository_tool = FunctionTool(prepare_repository)
# ADK pauses and asks the user before any run that may change files.
gemini_cli_tool = FunctionTool(gemini_cli, require_confirmation=_needs_confirmation)
