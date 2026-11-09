"""Settings shared by every agent and tool, read from the environment."""

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

# Worker model: routing, writing and running tests, fixing code.
WORKER_MODEL = Gemini(
    model=os.getenv("WORKER_MODEL", "gemini-2.5-flash"), retry_options=_RETRY
)
# Critic model: the feedback and fix reports. The reference used
# gemini-2.5-pro, which has no free-tier quota; set CRITIC_MODEL to use it.
CRITIC_MODEL = Gemini(
    model=os.getenv("CRITIC_MODEL", "gemini-2.5-flash"), retry_options=_RETRY
)

# Submissions larger than this are rejected before any model call.
MAX_CODE_CHARS = int(os.getenv("MAX_CODE_CHARS", "20000"))
# A fix counts as successful when every test passes and the style score
# reaches this value.
PASSING_STYLE_SCORE = int(os.getenv("PASSING_STYLE_SCORE", "90"))
MAX_FIX_ROUNDS = int(os.getenv("MAX_FIX_ROUNDS", "3"))

# Checked by LimitsPlugin before any model call. A message holds the code
# plus a short request, so it may be a little longer than the code limit.
MAX_MESSAGE_CHARS = int(os.getenv("MAX_MESSAGE_CHARS", str(MAX_CODE_CHARS + 5000)))
# A review takes about 4 model calls and a fix 2-3 per round.
MAX_MODEL_CALLS_PER_USER_PER_DAY = int(
    os.getenv("MAX_MODEL_CALLS_PER_USER_PER_DAY", "100")
)
