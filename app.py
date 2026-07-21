"""
Streamlit UI for the Price Check Reflexion Agent.
Run with:
    streamlit run app.py
"""

from __future__ import annotations

import streamlit as st

from config import GROQ_API_KEY, MAX_ATTEMPTS as DEFAULT_MAX_ATTEMPTS
from graph import build_graph, get_langfuse_config
from state import AgentState
import config  # so we can mutate MAX_ATTEMPTS at runtime

import logging
import sys
import traceback

# ── Logging setup ────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
    stream=sys.stderr,   # streamlit shows stderr in the terminal
    force=True,          # override any prior config
)
logger = logging.getLogger("app")


# ═══════════════════════════════════════════════════════════════════
# Page config
# ═══════════════════════════════════════════════════════════════════

st.set_page_config(
    page_title="Price Check Reflexion Agent",
    page_icon="💰",
    layout="wide",
    initial_sidebar_state="expanded",
)


# ═══════════════════════════════════════════════════════════════════
# Session state initialization
# ═══════════════════════════════════════════════════════════════════

def _init_session_state() -> None:
    defaults = {
        "phase": "input",              # input | clarify | running | done
        "product": "",
        "original_product": "",
        "clarification_questions": [],
        "clarification_answers": [],
        "clarify_round": 0,
        "final_state": None,
        "attempts": [],                # list of per-attempt snapshots for display
        "recent_runs": [],             # simple in-memory history
        "pre_graph_tokens": [],        # tokens used outside the graph (clarifier UI, smart merge)
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v


_init_session_state()


# ═══════════════════════════════════════════════════════════════════
# Sidebar
# ═══════════════════════════════════════════════════════════════════

with st.sidebar:
    st.title("⚙️ Settings")

    max_attempts = st.slider(
        "Max reflexion attempts",
        min_value=1, max_value=6, value=DEFAULT_MAX_ATTEMPTS,
        help="How many attempts before giving up.",
    )
    # Mutate the module-level config so graph.py picks it up
    config.MAX_ATTEMPTS = max_attempts

    st.markdown("---")

    st.markdown("### 🕒 Recent runs")
    if not st.session_state.recent_runs:
        st.caption("No runs yet.")
    else:
        for run in reversed(st.session_state.recent_runs[-10:]):
            icon = "✅" if run["passed"] else "⚠️"
            st.caption(f"{icon} **{run['product']}** — {run['best_price']}")

    st.markdown("---")

    if st.button("🔄 Reset session", use_container_width=True):
        for k in list(st.session_state.keys()):
            del st.session_state[k]
        st.rerun()

    st.markdown("---")
    st.caption("Powered by **LangGraph** + **Groq** + **Tavily**")


# ═══════════════════════════════════════════════════════════════════
# Header
# ═══════════════════════════════════════════════════════════════════

st.title("💰 Price Check Reflexion Agent")
st.caption("Find the best Indian retail price for any product using a self-reflecting AI agent.")

# API key sanity check
if not GROQ_API_KEY or GROQ_API_KEY == "your-groq-api-key-here":
    st.error(
        "**GROQ_API_KEY not set.** "
        "Open `.env` and add your Groq API key. "
        "Get one at https://console.groq.com/keys."
    )
    st.stop()


# ═══════════════════════════════════════════════════════════════════
# Helper: render a single attempt as a card
# ═══════════════════════════════════════════════════════════════════

def _render_attempt(attempt_data: dict, expanded: bool = False) -> None:
    """Render one attempt as an expandable card."""
    n = attempt_data.get("attempt", "?")
    passed = attempt_data.get("eval_result", "").upper() == "PASS"
    status_icon = "✅" if passed else "❌"
    status_text = "PASS" if passed else "FAIL"

    with st.expander(
        f"{status_icon} **Attempt {n}** — {status_text} — {attempt_data.get('best_price', 'N/A')}",
        expanded=expanded,
    ):
        col1, col2 = st.columns([2, 1])

        with col1:
            st.markdown(f"**🔍 Search query:** `{attempt_data.get('search_query', 'N/A')}`")
            st.markdown(f"**💰 Best price found:** {attempt_data.get('best_price', 'N/A')}")
            st.markdown(f"**📝 Summary:** {attempt_data.get('price_summary', 'N/A')}")

            reasoning = attempt_data.get("reasoning", "")
            if reasoning:
                with st.expander("💭 Reasoning"):
                    st.write(reasoning)

        with col2:
            st.metric("Results found", len(attempt_data.get("search_results", [])))
            if passed:
                st.success(f"**PASS**\n\n{attempt_data.get('eval_reason', '')}")
            else:
                st.error(f"**FAIL**\n\n{attempt_data.get('eval_reason', '')}")

        # Sources
        sources = attempt_data.get("sources", [])
        if sources:
            st.markdown("**🛒 Sources:**")
            for s in sources:
                if isinstance(s, dict):
                    name = s.get("name", "Unknown")
                    price = s.get("price", "N/A")
                    url = s.get("url", "")
                    if url:
                        st.markdown(f"- [{name}]({url}) — **{price}**")
                    else:
                        st.markdown(f"- {name} — **{price}**")


def _render_final(state: dict) -> None:
    """Render the final result panel."""
    passed = state.get("eval_result", "").upper() == "PASS"

    if passed:
        st.success(f"### ✅ Best Price Found (verified on attempt {state.get('attempt', '?')})")
    else:
        st.warning("### ⚠️ Best-Effort Result (max attempts reached)")

    col1, col2 = st.columns([2, 1])

    with col1:
        st.markdown(f"### Product\n{state.get('product', 'N/A')}")
        st.markdown(f"### 💰 Best Price\n# {state.get('best_price', 'Could not determine')}")
        st.markdown(f"**{state.get('price_summary', '')}**")

        with st.expander("💭 Full reasoning"):
            st.write(state.get("reasoning", ""))

    with col2:
        sources = state.get("sources", [])
        if sources:
            st.markdown("### 🛒 Price Comparison")
            for s in sources:
                if isinstance(s, dict):
                    name = s.get("name", "Unknown")
                    price = s.get("price", "N/A")
                    url = s.get("url", "")
                    with st.container(border=True):
                        if url:
                            st.markdown(f"**[{name}]({url})**")
                        else:
                            st.markdown(f"**{name}**")
                        st.markdown(f"### {price}")

    # Reflection trail
    reflections = state.get("reflections", [])
    if reflections:
        with st.expander(f"🔁 Reflection trail ({len(reflections)} critiques)"):
            for i, r in enumerate(reflections, 1):
                st.markdown(f"**{i}.** {r}")


def _render_token_metrics(state: dict) -> None:
    """Render a detailed token usage breakdown table."""
    token_list = state.get("token_usage", [])
    if not token_list:
        return

    total_in = sum(t.get("input_tokens", 0) for t in token_list)
    total_out = sum(t.get("output_tokens", 0) for t in token_list)
    total_all = sum(t.get("total_tokens", 0) for t in token_list)

    # Node display names
    node_labels = {
        "clarifier": "🤔 Clarifier",
        "actor_query": "🎯 Actor Query",
        "actor_verdict": "💰 Actor Verdict",
        "evaluator": "⚖️ Evaluator",
        "reflector": "🔁 Reflector",
        "smart_merge": "🧠 Smart Merge",
    }

    with st.expander(
        f"📊 Token Metrics — {total_all:,} total tokens across {len(token_list)} LLM calls",
        expanded=False,
    ):
        # Summary metrics row
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("⬇️ Input Tokens", f"{total_in:,}")
        m2.metric("⬆️ Output Tokens", f"{total_out:,}")
        m3.metric("Σ Total Tokens", f"{total_all:,}")
        m4.metric("🔄 LLM Calls", str(len(token_list)))

        st.markdown("---")
        st.markdown("#### Per-Node Breakdown")

        # Build table data
        table_md = "| Node | Attempt | Input | Output | Total |\n"
        table_md += "|:-----|:-------:|------:|-------:|------:|\n"
        for t in token_list:
            node_name = node_labels.get(t.get("node", ""), t.get("node", "unknown"))
            attempt = t.get("attempt", 0)
            attempt_str = f"#{attempt}" if attempt > 0 else "—"
            inp = t.get("input_tokens", 0)
            out = t.get("output_tokens", 0)
            tot = t.get("total_tokens", 0)
            table_md += f"| {node_name} | {attempt_str} | {inp:,} | {out:,} | {tot:,} |\n"

        # Totals row
        table_md += f"| **TOTAL** | | **{total_in:,}** | **{total_out:,}** | **{total_all:,}** |\n"
        st.markdown(table_md)


# ═══════════════════════════════════════════════════════════════════
# Smart product name merging
# ═══════════════════════════════════════════════════════════════════

def _smart_merge_product(original: str, answers: list[str]) -> str:
    """
    Merge the original product query with clarification answers into
    a clean, specific product name using the LLM.
    Falls back to simple concatenation if the LLM call fails.
    """
    from agent import _get_llm, _invoke_with_retry, _parse_json
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
        # Track smart merge tokens in session state
        if usage:
            st.session_state.pre_graph_tokens.append({
                "node": "smart_merge",
                "attempt": 0,
                **usage,
            })
        merged = result.strip().strip('"').strip("'")
        if merged:
            logger.info(f"   🧠 Smart merge: '{original}' + {answers} → '{merged}'")
            return merged
    except Exception as e:
        logger.warning(f"   ⚠️ Smart merge failed ({e}), using fallback")

    return fallback


# ═══════════════════════════════════════════════════════════════════
# Core agent runner
# ═══════════════════════════════════════════════════════════════════

def _run_agent_streaming(initial_state: dict, live_container) -> dict:
    graph = build_graph()
    final_state = initial_state.copy()
    last_snapshot = None
    last_printed_attempt = 0
    last_eval_reason = None
    attempts_local: list[dict] = []
    event_count = 0
    prev_token_count = 0  # track tokens seen so far for live updates

    # Placeholder for live token counter
    token_placeholder = live_container.empty()

    logger.info(f"🚀 Starting agent run for: {initial_state.get('product')}")

    try:
        for event in graph.stream(initial_state, stream_mode="values", config=get_langfuse_config(f"PriceCheck: {initial_state.get('product', 'unknown')}")):
            event_count += 1
            final_state.update(event)

            logger.info(
                f"Event #{event_count} | attempt={final_state.get('attempt')} | "
                f"has_query={bool(final_state.get('search_query'))} | "
                f"n_results={len(final_state.get('search_results', []))} | "
                f"has_eval={bool(final_state.get('eval_result'))} | "
                f"eval={final_state.get('eval_result', '-')}"
            )

            # ── Live token counter update ──────────────────────────
            token_list = final_state.get("token_usage", [])
            if len(token_list) > prev_token_count:
                prev_token_count = len(token_list)
                total_in = sum(t.get("input_tokens", 0) for t in token_list)
                total_out = sum(t.get("output_tokens", 0) for t in token_list)
                total_all = sum(t.get("total_tokens", 0) for t in token_list)
                token_placeholder.markdown(
                    f"📊 **Tokens** &nbsp;│&nbsp; "
                    f"⬇️ In: **{total_in:,}** &nbsp;│&nbsp; "
                    f"⬆️ Out: **{total_out:,}** &nbsp;│&nbsp; "
                    f"Σ Total: **{total_all:,}** &nbsp;│&nbsp; "
                    f"🔄 LLM Calls: **{len(token_list)}**"
                )

            if final_state.get("clarification_questions"):
                logger.warning("Clarifier re-triggered mid-run — breaking")
                break

            current_attempt = final_state.get("attempt", 0)
            current_eval_reason = final_state.get("eval_reason")

            # Only snapshot when we have a NEW eval reason for the current attempt
            eval_is_fresh = (
                current_eval_reason
                and current_eval_reason != last_eval_reason
                and current_attempt >= last_printed_attempt + 1
            )

            if eval_is_fresh:
                snapshot = final_state.copy()
                attempts_local.append(snapshot)
                last_snapshot = snapshot
                last_printed_attempt = current_attempt
                last_eval_reason = current_eval_reason

                logger.info(
                    f"✔ Attempt {current_attempt} complete: "
                    f"{snapshot.get('eval_result')} — {snapshot.get('best_price')}"
                )

                with live_container:
                    _render_attempt(snapshot, expanded=True)

    except Exception as e:
        logger.exception("Agent run crashed with exception")
        st.error(f"**Agent crashed:** `{type(e).__name__}: {e}`")
        with st.expander("🔍 Full traceback"):
            st.code(traceback.format_exc(), language="python")

    logger.info(
        f"🏁 Run complete. Events: {event_count}, "
        f"Attempts captured: {len(attempts_local)}, "
        f"Final has best_price: {bool(final_state.get('best_price'))}"
    )

    if not attempts_local:
        st.warning("⚠️ **No attempts were captured.** ...")
        with st.expander("🔍 Debug: final state dump"):
            st.json({k: v for k, v in final_state.items() if k != "search_results"})

    st.session_state.attempts = attempts_local
    return last_snapshot or final_state


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
    for event in graph.stream(initial_state, stream_mode="values", config=get_langfuse_config(f"Clarify: {product}")):
        snapshot.update(event)
        if snapshot.get("clarification_questions"):
            # Clarifier decided it's ambiguous — stop here
            return snapshot["clarification_questions"], snapshot
        # If clarifier passed through without questions, we've already moved
        # past it — but we DON'T want to continue the full pipeline yet from
        # this exploratory call. So break early.
        if snapshot.get("search_query"):
            break

    return [], snapshot



# ═══════════════════════════════════════════════════════════════════
# Debug panel (optional but incredibly helpful)
# ═══════════════════════════════════════════════════════════════════

with st.sidebar:
    st.markdown("---")
    with st.expander("🐛 Debug: session state"):
        st.json({
            "phase": st.session_state.get("phase"),
            "product": st.session_state.get("product"),
            "original_product": st.session_state.get("original_product"),
            "clarification_questions": st.session_state.get("clarification_questions"),
            "clarification_answers": st.session_state.get("clarification_answers"),
            "n_attempts": len(st.session_state.get("attempts", [])),
        })


# ═══════════════════════════════════════════════════════════════════
# Main UI flow (state machine)
# ═══════════════════════════════════════════════════════════════════

phase = st.session_state.phase
logger.info(f"📍 Rendering phase: {phase!r}")

# ─────────────────────────────────────────
# PHASE 1: INPUT
# ─────────────────────────────────────────
if phase == "input":
    with st.form("product_form"):
        product = st.text_input(
            "Product name",
            placeholder="e.g. iPhone 15, boAt Rockerz 450, Sony WH-1000XM5",
            help="Enter the product name. If ambiguous, the agent will ask you to clarify.",
        )
        submitted = st.form_submit_button("🔍 Find Best Price", type="primary", use_container_width=True)

    if submitted and product.strip():
        p = product.strip()
        logger.info(f"➡️  Phase INPUT submitted: product={p!r}")
        st.session_state.product = p
        st.session_state.original_product = p
        st.session_state.attempts = []
        st.session_state.final_state = None
        st.session_state.clarification_answers = []
        st.session_state.clarify_round = 0

        with st.spinner("🤔 Checking if the product name needs clarification..."):
            questions, _ = _check_clarification(p)

        logger.info(f"   Clarifier returned {len(questions)} questions: {questions}")

        if questions:
            st.session_state.clarification_questions = questions
            st.session_state.phase = "clarify"
        else:
            st.session_state.clarification_questions = []
            st.session_state.phase = "running"

        st.rerun()

# ─────────────────────────────────────────
# PHASE 2: CLARIFY
# ─────────────────────────────────────────
elif phase == "clarify":
    clarify_round = st.session_state.get("clarify_round", 0)
    if clarify_round > 0:
        st.info(f"🤔 **Still needs more detail (round {clarify_round + 1}).** Please provide more specifics for the most accurate price.")
        st.markdown(f"**Refined so far:** `{st.session_state.product}`")
    else:
        st.info(f"🤔 **The product name is a bit ambiguous.** Please clarify for the most accurate price.")
        st.markdown(f"**Product:** `{st.session_state.original_product}`")

    with st.form("clarify_form", clear_on_submit=False):
        answers = []
        for i, q in enumerate(st.session_state.clarification_questions, 1):
            ans = st.text_input(
                f"**{i}. {q}**",
                key=f"clarify_answer_{i}",
                placeholder="Your answer here...",
            )
            answers.append(ans)

        col1, col2 = st.columns([1, 1])
        with col1:
            proceed = st.form_submit_button("✅ Continue", type="primary", use_container_width=True)
        with col2:
            skip = st.form_submit_button("⏭️ Skip clarification", use_container_width=True)

    if proceed or skip:
        answers_stripped = [a.strip() for a in answers]
        filled = [a for a in answers_stripped if a]
        logger.info(f"➡️  Phase CLARIFY submitted: proceed={proceed}, skip={skip}, "
                    f"answers={answers_stripped}, filled={filled}")

        # ── Skip path: ignore ALL answers, use original ──
        if skip:
            refined = st.session_state.original_product
            logger.info(f"   → SKIP pressed, discarding {len(filled)} filled answers")

        # ── Proceed path: need at least one answer ──
        elif proceed:
            if not filled:
                st.warning("⚠️ Please fill in at least one answer, or click **Skip clarification**.")
                st.stop()
            refined = _smart_merge_product(st.session_state.product, filled)

        st.session_state.product = refined
        st.session_state.clarification_answers = filled if not skip else []
        logger.info(f"   → refined product: {refined!r}")

        # ── Multi-round: re-check if refined product is specific enough ──
        clarify_round = st.session_state.get("clarify_round", 0) + 1
        st.session_state.clarify_round = clarify_round

        if not skip and clarify_round < 3:
            with st.spinner("🔍 Checking if more details are needed..."):
                new_questions, _ = _check_clarification(refined)
            if new_questions:
                logger.info(f"   → Round {clarify_round}: still ambiguous, "
                            f"{len(new_questions)} new questions")
                st.session_state.clarification_questions = new_questions
                st.session_state.phase = "clarify"
                st.rerun()

        st.session_state.clarification_questions = []
        st.session_state.phase = "running"
        st.rerun()

# ─────────────────────────────────────────
# PHASE 3: RUNNING
# ─────────────────────────────────────────
elif phase == "running":
    st.markdown(f"### 🔎 Searching for: `{st.session_state.product}`")
    st.markdown(f"**Max attempts:** {config.MAX_ATTEMPTS}")

    if st.session_state.clarification_answers:
        st.caption(f"With clarifications: {', '.join(st.session_state.clarification_answers)}")

    st.markdown("---")

    live_container = st.container()

    with st.spinner("Running reflexion loop... this may take 30–90 seconds"):
        initial_state: AgentState = {
            "product": st.session_state.product,
            "original_product": st.session_state.original_product,
            "attempt": 0,
            "reflections": [],
            "search_results": [],
            "sources": [],
            "token_usage": [],
            # KEY FIX: pre-populate a sentinel so clarifier passes through
            "clarification_questions": [],
            # We track this ourselves so clarifier knows to skip
            "_clarification_done": True,
        }
        # Carry over any pre-graph tokens (from smart merge, etc.)
        if st.session_state.pre_graph_tokens:
            initial_state["token_usage"] = list(st.session_state.pre_graph_tokens)
            st.session_state.pre_graph_tokens = []
        logger.info(f"🎬 Launching graph.stream with product={initial_state['product']!r}")
        final_state = _run_agent_streaming(initial_state, live_container)
        st.session_state.final_state = final_state

    passed = final_state.get("eval_result", "").upper() == "PASS"
    st.session_state.recent_runs.append({
        "product": st.session_state.original_product,
        "best_price": final_state.get("best_price", "N/A"),
        "passed": passed,
    })

    st.session_state.phase = "done"
    st.rerun()

# ─────────────────────────────────────────
# PHASE 4: DONE — show final result
# ─────────────────────────────────────────
elif phase == "done":
    final_state = st.session_state.final_state or {}

    if final_state.get("needs_reclarification"):
        st.warning(
            "### 🔄 Product Name Too Broad\n\n"
            "The agent couldn't find reliable prices because the product name "
            "is too broad or refers to a product family rather than a specific model. "
            "Please try again with a **more specific product name** "
            "(include the exact model number or variant)."
        )
        if st.button("🔍 Try again with a specific product", type="primary",
                      use_container_width=True):
            st.session_state.phase = "input"
            st.session_state.product = ""
            st.session_state.original_product = ""
            st.session_state.attempts = []
            st.session_state.final_state = None
            st.session_state.clarification_questions = []
            st.session_state.clarification_answers = []
            st.session_state.clarify_round = 0
            st.session_state.pre_graph_tokens = []
            st.rerun()

    _render_final(final_state)

    # ── Token Metrics Breakdown ──────────────────────────────────
    _render_token_metrics(final_state)

    st.markdown("---")

    if st.session_state.attempts:
        st.markdown("### 📋 Attempt history")
        for a in st.session_state.attempts:
            _render_attempt(a, expanded=False)

    st.markdown("---")

    if st.button("🔍 Search another product", type="primary", use_container_width=True):
        st.session_state.phase = "input"
        st.session_state.product = ""
        st.session_state.original_product = ""
        st.session_state.attempts = []
        st.session_state.final_state = None
        st.session_state.clarification_questions = []
        st.session_state.clarification_answers = []
        st.session_state.clarify_round = 0
        st.session_state.pre_graph_tokens = []
        st.rerun()