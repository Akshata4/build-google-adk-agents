"""Settings read from the environment (.env)."""

import os

from dotenv import load_dotenv
from google.adk.models import Gemini
from google.genai import types

load_dotenv()

# Retry rate limits (429) and transient server errors (5xx) with backoff.
_RETRY = types.HttpRetryOptions(
    attempts=5,
    initial_delay=2,
    max_delay=30,
    exp_base=2,
    http_status_codes=[429, 500, 503, 504],
)

MODEL = Gemini(
    model=os.getenv("GEMINI_MODEL", "gemini-2.5-flash"), retry_options=_RETRY
)

# MCP Toolbox server. 127.0.0.1, not localhost: on macOS, AirPlay Receiver
# answers on localhost:5000 (IPv6).
TOOLBOX_URL = os.getenv("MCP_TOOLBOX_URL", "http://127.0.0.1:5000")

# Largest quantity of one variant in the cart (the database enforces it too).
MAX_CART_QUANTITY = 10
