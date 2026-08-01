"""LangGraph state schema for the reflexion loop."""
from __future__ import annotations
from typing import TypedDict, Annotated
import operator


class AgentState(TypedDict, total=False):
    # ── Input ────────────────────────────────────────────────────────
    product: str                    # refined product name (may be updated by clarifier)
    original_product: str           # ← NEW: what the user first typed
    clarification_questions: list[str]  # ← NEW: questions to ask user (empty = no ambiguity)
    _clarification_done: bool

    # ── Loop counter ─────────────────────────────────────────────────
    attempt: int

    # ── Actor outputs ────────────────────────────────────────────────
    search_query: str
    best_price: str
    price_summary: str
    reasoning: str
    sources: list[dict]

    # ── Search outputs ───────────────────────────────────────────────
    search_results: list[dict]

    # ── Evaluator outputs ────────────────────────────────────────────
    eval_result: str
    eval_reason: str

    # ── Reflector — reducer appends automatically ────────────────────
    reflections: Annotated[list[str], operator.add]

    # ── Token metrics — reducer appends automatically ─────────────────
    token_usage: Annotated[list[dict], operator.add]

    # ── Final ────────────────────────────────────────────────────────
    final_output: str