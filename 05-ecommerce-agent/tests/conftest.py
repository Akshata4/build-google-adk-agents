"""A real Postgres test database and a real MCP Toolbox server for the tests.

The database `sportsstore_test` is loaded with the same scripts as the local
setup, and Toolbox serves the same tools.yaml on port 5002. Only the model is
missing, so every SQL statement the agent can run is exercised for real.
Tests skip when Postgres or the toolbox binary isn't available.
"""

import os
import shutil
import subprocess
import time
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
DB_NAME = "sportsstore_test"
PORT = 5002
TOOLBOX = ROOT / "deployment" / "mcp-toolbox" / "toolbox"
DB_ENV = {
    "DB_USER": os.getenv("TEST_DB_USER", "postgres"),
    "DB_PASS": os.getenv("TEST_DB_PASS", "test"),
    "DB_NAME": DB_NAME,
}

# Must be set before the package is imported (config reads it once).
os.environ["MCP_TOOLBOX_URL"] = f"http://127.0.0.1:{PORT}"


def psql(sql: str) -> str:
    """Runs SQL on the test database; returns unaligned output."""
    out = subprocess.run(
        ["psql", "-d", DB_NAME, "-v", "ON_ERROR_STOP=1", "-qAt", "-c", sql],
        capture_output=True,
        text=True,
        check=True,
    )
    return out.stdout.strip()


def load_data():
    subprocess.run(
        [str(ROOT / "deployment" / "db" / "setup_local.sh"), DB_NAME],
        env={**os.environ, **DB_ENV},
        capture_output=True,
        check=True,
    )


@pytest.fixture(scope="session")
def toolbox():
    if (
        not shutil.which("psql")
        or subprocess.run(
            ["psql", "-d", "postgres", "-c", "SELECT 1"],
            capture_output=True,
            check=False,
        ).returncode
    ):
        pytest.skip("local Postgres not available")
    if not TOOLBOX.exists():
        pytest.skip(
            "toolbox binary missing: run deployment/mcp-toolbox/run_local.sh once"
        )
    load_data()
    proc = subprocess.Popen(
        [
            str(ROOT / "deployment" / "mcp-toolbox" / "run_local.sh"),
            "--port",
            str(PORT),
        ],
        env={**os.environ, **DB_ENV},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    deadline = time.time() + 20
    while True:
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{PORT}/", timeout=1)
            break
        except OSError:
            if time.time() > deadline or proc.poll() is not None:
                proc.kill()
                pytest.fail("toolbox did not start")
            time.sleep(0.2)
    yield f"http://127.0.0.1:{PORT}"
    proc.terminate()
    proc.wait(timeout=10)


@pytest.fixture
def store(toolbox):
    """Fresh sample data for each test that changes it."""
    load_data()
    return toolbox
