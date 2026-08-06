"""
FastAPI backend for the Price Check Reflexion Agent.
Replaces app.py (Streamlit) with a REST + SSE API.

Run with:
    uvicorn server:app --reload --port 8000
    or
    python server.py
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
import traceback
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sse_starlette.sse import EventSourceResponse

import config
from config import GROQ_API_KEY, MAX_ATTEMPTS as DEFAULT_MAX_ATTEMPTS
from graph import build_graph, get_langfuse_config
from state import AgentState

# ── Logging setup ────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
    stream=sys.stderr,
    force=True,
)
logger = logging.getLogger("server")


# ═══════════════════════════════════════════════════════════════════
# FastAPI app
# ═══════════════════════════════════════════════════════════════════

app = FastAPI(title="Price Check Reflexion Agent")

# Serve static files (index.html, favicon, etc.)
app.mount("/static", StaticFiles(directory="static"), name="static")


# ═══════════════════════════════════════════════════════════════════
# In-memory session state (mirrors st.session_state)
# ═══════════════════════════════════════════════════════════════════

session_state: dict[str, Any] = {
    "phase": "input",
    "product": "",
    "original_product": "",
    "clarification_questions": [],
    "clarification_answers": [],
    "clarify_round": 0,
    "final_state": None,
    "attempts": [],
    "recent_runs": [],
    "pre_graph_tokens": [],
}


def _reset_session() -> None:
    """Reset session state to defaults."""
    session_state.update({
        "phase": "input",
        "product": "",
        "original_product": "",
        "clarification_questions": [],
        "clarification_answers": [],
        "clarify_round": 0,
        "final_state": None,
        "attempts": [],
        "recent_runs": session_state.get("recent_runs", []),  # keep history
        "pre_graph_tokens": [],
    })


# ═══════════════════════════════════════════════════════════════════
# Helper: smart product name merging (moved from app.py)
# ═══════════════════════════════════════════════════════════════════

def _smart_merge_product(original: str, answers: list[str]) -> tuple[str, dict | None]:
    """
    Merge the original product query with clarification answers into
    a clean, specific product name using the LLM.
    Falls back to simple concatenation if the LLM call fails.

    Returns (merged_name, token_usage_dict_or_None).
    """
    from agent import _get_llm, _invoke_with_retry
    from langchain_core.messages import HumanMessage

    answers_text = ", ".join(answers)
    fallback = f"{original} {' '.join(answers)}"

    try:
        llm = _get_llm()
        prompt = (
            f"Combine the following into the CORRECT, SPECIFIC product name "
            f"that a store would list. Remove redundant words.\n\n"
            f"Original query: \"{original}\"\n"
            f"Clarification answers: \"{answers_text}\"\n\n"
            f"Examples:\n"
            f"- 'boat headphones' + 'Rockerz 450' → 'boAt Rockerz 450'\n"
            f"- 'iPhone' + '15, 128GB' → 'iPhone 15 128GB'\n"
            f"- 'Samsung phone' + 'Galaxy S24 Ultra' → 'Samsung Galaxy S24 Ultra'\n"
            f"- 'iPad' + '10.9-inch WiFi 64GB' → 'Apple iPad 10.9-inch WiFi 64GB'\n\n"
            f"Output ONLY the clean product name, nothing else."
        )
        result, usage = _invoke_with_retry(llm, [HumanMessage(content=prompt)])
        merged = result.strip().strip('"').strip("'")
        if merged:
            logger.info(f"   🧠 Smart merge: '{original}' + {answers} → '{merged}'")
            token_entry = {
                "node": "smart_merge",
                "attempt": 0,
                **usage,
            }
            return merged, token_entry
    except Exception as e:
        logger.warning(f"   ⚠️ Smart merge failed ({e}), using fallback")

    return fallback, None


# ═══════════════════════════════════════════════════════════════════
# Helper: clarification check (moved from app.py)
# ═══════════════════════════════════════════════════════════════════

def _check_clarification(product: str) -> tuple[list[str], dict]:
    """
    Run only the clarifier step to see if the product is ambiguous.
    Returns (questions, state_snapshot).
    """
    graph = build_graph()
    initial_state: AgentState = {
        "product": product,
        "original_product": product,
        "attempt": 0,
        "reflections": [],
        "search_results": [],
        "sources": [],
        "clarification_questions": [],
    }

    snapshot = initial_state.copy()
    for event in graph.stream(
        initial_state,
        stream_mode="values",
        config=get_langfuse_config(f"Clarify: {product}"),
    ):
        snapshot.update(event)
        if snapshot.get("clarification_questions"):
            return snapshot["clarification_questions"], snapshot
        if snapshot.get("search_query"):
            break

    return [], snapshot


# ═══════════════════════════════════════════════════════════════════
# Helper: live activity status text (mirrors _render_live_activity_feed)
# ═══════════════════════════════════════════════════════════════════

def _get_activity_status(state: dict) -> dict:
    """Build a status event payload from the current graph state."""
    attempt = state.get("attempt", 1)
    max_attempts = getattr(config, "MAX_ATTEMPTS", 4)
    query = state.get("search_query", "")
    results = state.get("search_results", [])
    best_price = state.get("best_price", "")
    sources = state.get("sources", [])
    eval_result = state.get("eval_result", "")
    eval_reason = state.get("eval_reason", "")

    if not query:
        step = "actor_query"
        message = "🧠 Actor Query: Formulating search query for Indian e-commerce..."
    elif not results:
        step = "search"
        message = f"🔌 MCP Server: Calling tool search_prices → \"{query}\""
    elif not best_price:
        step = "actor_verdict"
        message = f"💰 Actor Verdict: Extracting INR prices from {len(results)} retail listings..."
    elif not eval_result:
        if len(sources) == 1 and sources[0].get("url"):
            step = "verification"
            message = f"🛡️ MCP Verification: Checking authority of single source..."
        else:
            step = "evaluator"
            message = f"⚖️ Evaluator Judge: Checking candidate {best_price} against Indian market rubric..."
    else:
        step = "verdict"
        icon = "🏁" if eval_result == "PASS" else "🤔"
        message = f"{icon} Evaluator Verdict: {eval_result} — {eval_reason[:120]}"

    return {
        "attempt": attempt,
        "max_attempts": max_attempts,
        "step": step,
        "message": message,
    }


def _get_token_summary(token_list: list[dict]) -> dict:
    """Build a token summary payload."""
    total_in = sum(t.get("input_tokens", 0) for t in token_list)
    total_out = sum(t.get("output_tokens", 0) for t in token_list)
    total_all = sum(t.get("total_tokens", 0) for t in token_list)
    return {
        "input_tokens": total_in,
        "output_tokens": total_out,
        "total_tokens": total_all,
        "llm_calls": len(token_list),
    }


# ═══════════════════════════════════════════════════════════════════
# API Routes
# ═══════════════════════════════════════════════════════════════════

@app.get("/", response_class=HTMLResponse)
async def serve_index():
    """Serve the main UI page."""
    with open("static/index.html", "r", encoding="utf-8") as f:
        return HTMLResponse(content=f.read())


@app.get("/api/config")
async def get_config():
    """Return current configuration."""
    return {
        "max_attempts": getattr(config, "MAX_ATTEMPTS", DEFAULT_MAX_ATTEMPTS),
        "has_api_key": bool(GROQ_API_KEY and GROQ_API_KEY != "your-groq-api-key-here"),
    }


@app.post("/api/config")
async def update_config(request: Request):
    """Update runtime configuration."""
    body = await request.json()
    if "max_attempts" in body:
        val = max(1, min(6, int(body["max_attempts"])))
        config.MAX_ATTEMPTS = val
        logger.info(f"⚙️ MAX_ATTEMPTS updated to {val}")
    return {"max_attempts": config.MAX_ATTEMPTS}


@app.post("/api/clarify")
async def clarify_product(request: Request):
    """Run the clarifier to check if a product name needs clarification."""
    body = await request.json()
    product = body.get("product", "").strip()
    if not product:
        return JSONResponse({"error": "Product name required"}, status_code=400)

    logger.info(f"🤔 Clarify request for: {product!r}")

    # Run clarifier in a thread to avoid blocking the event loop
    loop = asyncio.get_event_loop()
    questions, snapshot = await loop.run_in_executor(
        None, _check_clarification, product
    )

    # Track clarifier token usage
    clarifier_tokens = None
    token_list = snapshot.get("token_usage", [])
    if token_list:
        clarifier_tokens = token_list[-1]

    logger.info(f"   Clarifier returned {len(questions)} questions")
    return {
        "questions": questions,
        "token_usage": clarifier_tokens,
    }


@app.post("/api/merge")
async def merge_product(request: Request):
    """Smart merge product name with clarification answers."""
    body = await request.json()
    original = body.get("original", "").strip()
    answers = body.get("answers", [])

    if not original:
        return JSONResponse({"error": "Original product required"}, status_code=400)

    loop = asyncio.get_event_loop()
    merged, token_entry = await loop.run_in_executor(
        None, _smart_merge_product, original, answers
    )

    return {
        "merged": merged,
        "token_usage": token_entry,
    }


@app.get("/api/run")
async def run_agent(request: Request):
    """
    SSE endpoint — run the full reflexion loop and stream events.

    Query params:
        product: refined product name
        original_product: what the user originally typed
    """
    product = request.query_params.get("product", "")
    original_product = request.query_params.get("original_product", product)
    pre_graph_tokens_json = request.query_params.get("pre_graph_tokens", "[]")

    if not product:
        return JSONResponse({"error": "Product required"}, status_code=400)

    try:
        pre_graph_tokens = json.loads(pre_graph_tokens_json)
    except json.JSONDecodeError:
        pre_graph_tokens = []

    async def event_generator():
        """Generate SSE events from the graph execution."""
        graph = build_graph()
        loop = asyncio.get_event_loop()

        initial_state: AgentState = {
            "product": product,
            "original_product": original_product,
            "attempt": 0,
            "reflections": [],
            "search_results": [],
            "sources": [],
            "token_usage": list(pre_graph_tokens) if pre_graph_tokens else [],
            "clarification_questions": [],
            "_clarification_done": True,
        }

        final_state = initial_state.copy()
        last_eval_reason = None
        last_printed_attempt = 0
        attempts_local: list[dict] = []
        event_count = 0
        prev_token_count = 0

        logger.info(f"🎬 SSE: Starting agent run for: {product!r}")

        try:
            # Run graph.stream in a thread since it's synchronous
            def _stream_graph():
                return list(graph.stream(
                    initial_state,
                    stream_mode="values",
                    config=get_langfuse_config(f"PriceCheck: {product}"),
                ))

            events = await loop.run_in_executor(None, _stream_graph)

            for event in events:
                # Check if client disconnected
                if await request.is_disconnected():
                    logger.info("Client disconnected, stopping SSE stream")
                    return

                event_count += 1
                final_state.update(event)

                logger.info(
                    f"Event #{event_count} | attempt={final_state.get('attempt')} | "
                    f"has_query={bool(final_state.get('search_query'))} | "
                    f"n_results={len(final_state.get('search_results', []))} | "
                    f"eval={final_state.get('eval_result', '-')}"
                )

                # ── Live activity status ───────────────────────────
                status = _get_activity_status(final_state)
                yield {
                    "event": "status",
                    "data": json.dumps(status),
                }

                # ── Live token counter ─────────────────────────────
                token_list = final_state.get("token_usage", [])
                if len(token_list) > prev_token_count:
                    prev_token_count = len(token_list)
                    yield {
                        "event": "tokens",
                        "data": json.dumps(_get_token_summary(token_list)),
                    }

                # ── If clarifier re-triggered, stop ────────────────
                if final_state.get("clarification_questions"):
                    logger.warning("Clarifier re-triggered mid-run — breaking")
                    break

                # ── Attempt snapshot ───────────────────────────────
                current_attempt = final_state.get("attempt", 0)
                current_eval_reason = final_state.get("eval_reason")

                eval_is_fresh = (
                    current_eval_reason
                    and current_eval_reason != last_eval_reason
                    and current_attempt >= last_printed_attempt + 1
                )

                if eval_is_fresh:
                    snapshot = final_state.copy()
                    # Remove search_results from snapshot (too large for SSE)
                    snapshot_clean = {
                        k: v for k, v in snapshot.items()
                        if k != "search_results"
                    }
                    attempts_local.append(snapshot)
                    last_printed_attempt = current_attempt
                    last_eval_reason = current_eval_reason

                    logger.info(
                        f"✔ Attempt {current_attempt} complete: "
                        f"{snapshot.get('eval_result')} — {snapshot.get('best_price')}"
                    )

                    yield {
                        "event": "attempt",
                        "data": json.dumps(snapshot_clean, default=str),
                    }

        except Exception as e:
            logger.exception("Agent run crashed with exception")
            yield {
                "event": "error",
                "data": json.dumps({
                    "error": f"{type(e).__name__}: {e}",
                    "traceback": traceback.format_exc(),
                }),
            }

        logger.info(
            f"🏁 Run complete. Events: {event_count}, "
            f"Attempts captured: {len(attempts_local)}"
        )

        # ── Final result ──────────────────────────────────────
        final_clean = {
            k: v for k, v in final_state.items()
            if k != "search_results"
        }
        # Save to session
        session_state["final_state"] = final_state
        session_state["attempts"] = attempts_local

        # Add to recent runs
        passed = final_state.get("eval_result", "").upper() == "PASS"
        session_state["recent_runs"].append({
            "product": original_product,
            "best_price": final_state.get("best_price", "N/A"),
            "passed": passed,
        })
        # Keep only last 10
        session_state["recent_runs"] = session_state["recent_runs"][-10:]

        yield {
            "event": "done",
            "data": json.dumps({
                "final_state": final_clean,
                "attempts": [
                    {k: v for k, v in a.items() if k != "search_results"}
                    for a in attempts_local
                ],
            }, default=str),
        }

    return EventSourceResponse(event_generator())


@app.post("/api/reset")
async def reset_session():
    """Reset session state."""
    _reset_session()
    return {"status": "ok"}


@app.get("/api/history")
async def get_history():
    """Return recent runs."""
    return {"recent_runs": session_state.get("recent_runs", [])}


@app.get("/api/session")
async def get_session():
    """Return current session state (for debug panel)."""
    return {
        "phase": session_state.get("phase", "input"),
        "product": session_state.get("product", ""),
        "original_product": session_state.get("original_product", ""),
        "clarification_questions": session_state.get("clarification_questions", []),
        "clarification_answers": session_state.get("clarification_answers", []),
        "n_attempts": len(session_state.get("attempts", [])),
    }


# ═══════════════════════════════════════════════════════════════════
# Entrypoint
# ═══════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("server:app", host="0.0.0.0", port=8000, reload=True)
