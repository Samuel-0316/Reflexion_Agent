# Price Check Reflexion Agent — Project Rules

## Project Identity
This is a **Price Check Reflexion Agent** — a LangGraph-based AI agent that finds the best Indian retail price for any product using a self-reflecting (reflexion) loop with MCP-based tool integration.

## Architecture Invariants
- **Never bypass the MCP layer.** All external tool calls (search, merchant verification) go through `mcp_client.py` → `mcp_server.py` over stdio. No direct Tavily calls from `agent.py`.
- **State is typed.** All state fields must be declared in `state.py` (`AgentState` TypedDict). Never add ad-hoc keys to the state dict without updating the schema.
- **Reducer fields use `Annotated[list, operator.add]`.** `reflections` and `token_usage` are append-only. Return `[item]` (a single-element list) from nodes — the reducer accumulates automatically.
- **Graph topology lives in `graph.py` only.** Node functions live in `agent.py`. Routing logic lives in `graph.py`. Do not mix.
- **Config is centralized in `config.py`.** All env vars and tunable constants live here. `app.py` may mutate `config.MAX_ATTEMPTS` at runtime via the sidebar slider.

## Coding Conventions
- **Python 3.11+** with `from __future__ import annotations`.
- **Logging:** Use `logging.getLogger(__name__)` in each module. Never use bare `print()` for diagnostics.
- **LLM calls:** Always go through `_invoke_with_retry()` in `agent.py` for automatic 429 retry handling.
- **JSON parsing:** Always use `_parse_json()` in `agent.py` for best-effort extraction from LLM output.
- **Prompt structure:** Each node's prompt is a single f-string. Keep prompts self-contained — don't import prompt fragments from other files.

## UI Rules (Streamlit — `app.py`)
- **Phase machine:** The UI has 4 phases: `input → clarify → running → done`. State transitions use `st.session_state.phase` and `st.rerun()`.
- **No raw HTML in Streamlit.** Use native Streamlit components (`st.info`, `st.progress`, `st.markdown`, `st.expander`). Streamlit's markdown renderer breaks on indented HTML strings.
- **Live updates are one-liners.** The `_render_live_activity_feed()` function outputs a single `st.markdown()` line showing the current step. Keep it minimal.

## MCP Rules
- **Transport:** Always `stdio`. The client spawns the server as a subprocess.
- **No hardcoded specialty store lists.** Use the algorithmic scoring heuristic in `verify_merchant_authority` (Indian TLD, retail URL paths, e-commerce keywords).
- **AUTHORITATIVE_MARKETPLACES** is the only acceptable dictionary — it lists major Indian e-commerce platforms that get `trust_score: 1.0` automatically.
- **Inspect with:** `npx -y @modelcontextprotocol/inspector@latest python mcp_server.py`

## Testing & Verification
- **Syntax check:** `python -m py_compile <file>.py` before any PR.
- **MCP tool test:** `python -c "from mcp_server import verify_merchant_authority; print(verify_merchant_authority('https://example.com/'))"` for quick verification.
- **Streamlit:** `streamlit run app.py` — test with both specific products ("Sony WH-1000XM5") and ambiguous ones ("power bank") to verify the clarifier.
