"""
Configuration — loads env vars and exposes project-wide constants.
Swap model or search settings here without touching other files.
"""

import os
from dotenv import load_dotenv

load_dotenv()

# ── LLM ──────────────────────────────────────────────────────────────
GROQ_API_KEY: str = os.getenv("GROQ_API_KEY", "")
MODEL_NAME: str = os.getenv("MODEL_NAME", "llama-3.3-70b-versatile")
TAVILY_API_KEY: str = os.getenv("TAVILY_API_KEY", "")

# ── Reflexion loop ───────────────────────────────────────────────────
MAX_ATTEMPTS: int = int(os.getenv("MAX_ATTEMPTS", "3"))

# ── Search ───────────────────────────────────────────────────────────
MAX_SEARCH_RESULTS: int = int(os.getenv("MAX_SEARCH_RESULTS", "6"))