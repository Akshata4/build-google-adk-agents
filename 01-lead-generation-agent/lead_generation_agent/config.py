"""Model configuration shared by every agent in the pipeline."""

import os

from dotenv import load_dotenv
from google.adk.models import Gemini
from google.genai import types

load_dotenv()

# Retry rate limits (429) and transient server errors (5xx) with exponential
# backoff; the parallel research steps can burst many requests at once.
_RETRY = types.HttpRetryOptions(
    attempts=5,
    initial_delay=2,
    max_delay=30,
    exp_base=2,
    http_status_codes=[429, 500, 503, 504],
)

# Fast model: formatting, validation and intent extraction.
FAST_MODEL = Gemini(
    model=os.getenv("GEN_FAST_MODEL", "gemini-2.5-flash"), retry_options=_RETRY
)
# Advanced model: talking to the user, research and synthesis.
ADVANCED_MODEL = Gemini(
    model=os.getenv("GEN_ADVANCED_MODEL", "gemini-2.5-pro"), retry_options=_RETRY
)
