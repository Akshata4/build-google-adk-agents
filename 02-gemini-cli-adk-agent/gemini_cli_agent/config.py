"""Settings shared by the agent and its tools."""

import os
from pathlib import Path

from dotenv import load_dotenv
from google.adk.models import Gemini
from google.genai import types

load_dotenv()

# Retry rate limits (429) and transient server errors (5xx) with backoff.
MODEL = Gemini(
    model=os.getenv("GEMINI_MODEL", "gemini-2.5-flash"),
    retry_options=types.HttpRetryOptions(
        attempts=5,
        initial_delay=2,
        max_delay=30,
        exp_base=2,
        http_status_codes=[429, 500, 503, 504],
    ),
)

GEMINI_CLI_PATH = os.getenv("GEMINI_CLI_PATH", "gemini")
GEMINI_CLI_MODEL = os.getenv("GEMINI_CLI_MODEL", "")
GEMINI_CLI_TIMEOUT = int(os.getenv("GEMINI_CLI_TIMEOUT", "600"))

# Cloned repositories live here, one folder per owner/repo.
PROJECT_DIR = Path(__file__).resolve().parent.parent
WORKSPACE_DIR = Path(os.getenv("WORKSPACE_DIR", PROJECT_DIR / "workspace")).resolve()
