---
name: streamlit-ui-patterns
description: |
  Patterns and rules for the Streamlit UI in app.py.
  Covers the phase state machine, session state management,
  live streaming updates, rendering helpers, and common pitfalls.
  Use this skill when modifying the UI, adding new phases, or fixing display issues.
---

# Streamlit UI Patterns

## Phase State Machine

The UI operates as a 4-phase state machine managed via `st.session_state.phase`:

```
"input" → "clarify" → "running" → "done"
                ↑           │
                └───────────┘  (multi-round clarification, max 3 rounds)
```

### Phase Transitions
- **input → clarify:** Product is ambiguous, clarifier returned questions.
- **input → running:** Product is specific enough, skip clarification.
- **clarify → clarify:** Refined product still ambiguous (round < 3).
- **clarify → running:** Product is now specific, or user clicked "Skip".
- **running → done:** Graph execution complete.
- **done → input:** User clicks "Search another product".

Every transition: `st.session_state.phase = "new_phase"` then `st.rerun()`.

## Session State Keys

```python
defaults = {
    "phase": "input",                # Current UI phase
    "product": "",                   # Refined product name (after clarification)
    "original_product": "",          # What user typed originally
    "clarification_questions": [],   # Current round's questions
    "clarification_answers": [],     # User's answers (for display)
    "clarify_round": 0,             # Which clarification round (0-2)
    "final_state": None,            # Final graph state dict
    "attempts": [],                  # List of per-attempt state snapshots
    "recent_runs": [],              # Sidebar history [{product, best_price, passed}]
    "pre_graph_tokens": [],         # Token usage from smart_merge (pre-graph LLM calls)
}
```

## Live Streaming During Execution

### `_render_live_activity_feed(state, placeholder)`
- Called on **every graph event** inside `_run_agent_streaming()`.
- Renders a **single markdown line** showing the current step.
- Uses `placeholder.markdown(...)` to overwrite the previous status.
- Determines the current step by checking which state fields are populated:

```
not query          → "🧠 Actor Query: Formulating search query..."
not results        → "🔌 MCP Server: Calling search_prices..."
not best_price     → "💰 Actor Verdict: Extracting INR prices..."
not eval_result    → "⚖️ Evaluator Judge: Checking candidate..."
else               → "🏁 Evaluator Verdict: PASS/FAIL — reason"
```

- **Reads `config.MAX_ATTEMPTS` dynamically** for the "Attempt X/Y" display.
- **Auto-clears** when the run is complete (`activity_placeholder.empty()`).

### `_run_agent_streaming(initial_state, live_container)`
- Creates two placeholders: `token_placeholder` and `activity_placeholder`.
- Iterates over `graph.stream(initial_state, stream_mode="values")`.
- On each event: updates live feed, updates token counter, captures attempt snapshots.
- Attempt snapshot is captured when `eval_reason` changes (fresh evaluation).

## Rendering Helpers

### `_render_attempt(attempt_data, expanded=False)`
- Renders one attempt as an `st.expander` card.
- Shows: search query, best price, summary, reasoning, sources, eval result.
- Uses `st.success`/`st.error` for PASS/FAIL.

### `_render_final(state)`
- Renders the final result panel with best price, sources, reflection trail.
- Uses `st.success` for PASS, `st.warning` for max-attempts-reached.

### `_render_token_metrics(state)`
- Renders a detailed token usage breakdown table inside an expander.
- Shows per-node breakdown with input/output/total tokens per LLM call.

## Critical Rules

### No Raw HTML
Streamlit's markdown renderer treats indented HTML as code blocks. **Always use native Streamlit components:**
- ✅ `st.info()`, `st.success()`, `st.error()`, `st.warning()`
- ✅ `st.progress()`, `st.metric()`, `st.expander()`
- ✅ `st.markdown("**bold** and `code`")`
- ❌ `st.markdown("<div style='...'>...</div>", unsafe_allow_html=True)`

### Sidebar Mutation of Config
The sidebar slider mutates `config.MAX_ATTEMPTS` at runtime:
```python
config.MAX_ATTEMPTS = max_attempts  # Sidebar slider value
```
This is picked up by `graph.py` routing and `_render_live_activity_feed()` dynamically.

### Streamlit Event Loop Compatibility
Streamlit runs its own asyncio event loop. MCP client calls use `ThreadPoolExecutor` to run `asyncio.run()` in a separate thread to avoid "event loop already running" errors. See `mcp_client.py` for the pattern.

### Smart Merge Token Tracking
The `_smart_merge_product()` function makes an LLM call outside the graph. Its tokens are stored in `st.session_state.pre_graph_tokens` and injected into `initial_state["token_usage"]` before graph execution starts.
