"""
LangGraph graph — wires the reflexion loop together with a clarifier.

    START → clarifier ──(has questions)──► END (main.py asks user, re-invokes)
              │
              (no questions)
              ▼
        actor_query → search → actor_verdict → evaluator
                                                    │
                                             PASS → END
                                             FAIL → reflector → (max?) → actor_query / END
"""

from __future__ import annotations

from langgraph.graph import StateGraph, END

from state import AgentState
from agent import (
    clarifier_node,
    actor_query_node,
    search_node,
    actor_verdict_node,
    evaluator_node,
    reflector_node,
)
import config


def _route_after_clarifier(state: dict) -> str:
    """If clarifier produced questions, exit so main.py can ask the user."""
    if state.get("clarification_questions"):
        return END
    return "actor_query"


def _route_after_eval(state: dict) -> str:
    """PASS → END, FAIL → reflector."""
    if state.get("eval_result", "").upper() == "PASS":
        return END
    return "reflector"


def _route_after_reflect(state: dict) -> str:
    """Loop back to actor_query, stop early, or signal re-clarification."""
    if state.get("attempt", 1) >= config.MAX_ATTEMPTS:
        return END

    # If reflector flagged ambiguity, stop the loop
    if state.get("needs_reclarification"):
        return END

    # Early termination: if the last 2 attempts both had 0 sources, stop
    reflections = state.get("reflections", [])
    if len(reflections) >= 2:
        # Check if we've had repeat failures — this is a heuristic signal
        # that more attempts won't help
        last_reason = state.get("eval_reason", "").lower()
        if "no prices" in last_reason or "0 sources" in last_reason:
            # Give up: 0 results on a fail almost never recovers
            return END

    return "actor_query"


def build_graph() -> StateGraph:
    """Construct and compile the reflexion-loop graph."""

    graph = StateGraph(AgentState)

    # ── Register nodes ───────────────────────────────────────────────
    graph.add_node("clarifier", clarifier_node)
    graph.add_node("actor_query", actor_query_node)
    graph.add_node("search", search_node)
    graph.add_node("actor_verdict", actor_verdict_node)
    graph.add_node("evaluator", evaluator_node)
    graph.add_node("reflector", reflector_node)

    # ── Entry point is now clarifier ─────────────────────────────────
    graph.set_entry_point("clarifier")

    # ── Conditional from clarifier: ambiguous → END, else → actor_query
    graph.add_conditional_edges("clarifier", _route_after_clarifier)

    # ── Linear edges ────────────────────────────────────────────────
    graph.add_edge("actor_query", "search")
    graph.add_edge("search", "actor_verdict")
    graph.add_edge("actor_verdict", "evaluator")

    # ── Conditional edges ────────────────────────────────────────────
    graph.add_conditional_edges("evaluator", _route_after_eval)
    graph.add_conditional_edges("reflector", _route_after_reflect)

    return graph.compile()