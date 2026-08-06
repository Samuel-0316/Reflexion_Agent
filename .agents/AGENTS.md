# Price Check Reflexion Agent — Project Rules

## Project Identity
This is a **Price Check Reflexion Agent** — a LangGraph-based AI agent that finds the best Indian retail price for any product using a self-reflecting (reflexion) loop with MCP-based tool integration.

## Architecture Invariants
- **Never bypass the MCP layer.** All external tool calls (search, merchant verification) go through `mcp_client.py` → `mcp_server.py` over stdio. No direct Tavily calls from `agent.py`.
- **State is typed.** All state fields must be declared in `state.py` (`AgentState` TypedDict). Never add ad-hoc keys to the state dict without updating the schema.
- **Reducer fields use `Annotated[list, operator.add]`.** `reflections` and `token_usage` are append-only. Return `[item]` (a single-element list) from nodes — the reducer accumulates automatically.
- **Graph topology lives in `graph.py` only.** Node functions live in `agent.py`. Routing logic lives in `graph.py`. Do not mix.
- **Config is centralized in `config.py`.** All env vars and tunable constants live here. `server.py` may mutate `config.MAX_ATTEMPTS` at runtime via the `POST /api/config` endpoint (triggered by the sidebar slider).

## Coding Conventions
- **Python 3.11+** with `from __future__ import annotations`.
- **Logging:** Use `logging.getLogger(__name__)` in each module. Never use bare `print()` for diagnostics.
- **LLM calls:** Always go through `_invoke_with_retry()` in `agent.py` for automatic 429 retry handling.
- **JSON parsing:** Always use `_parse_json()` in `agent.py` for best-effort extraction from LLM output.
- **Prompt structure:** Each node's prompt is a single f-string. Keep prompts self-contained — don't import prompt fragments from other files.

## UI Architecture (FastAPI + HTML/Tailwind)
- **Backend entrypoint:** `server.py` (FastAPI). Run with `uvicorn server:app --reload --port 8000`.
- **Frontend:** `static/index.html` — single-page app using Tailwind CSS v3 (CDN) and vanilla JavaScript.
- **Phase machine:** The UI has 4 phases: `input → clarify → running → done`. Phase transitions are managed by the JS `state.phase` variable and `setPhase()` function.
- **Live streaming uses SSE (Server-Sent Events).** The `GET /api/run` endpoint streams `status`, `tokens`, `attempt`, and `done` events. The frontend consumes these via `EventSource`.
- **Session state** is held in-memory in `server.py` (Python dict) for backend state, and in the JS `state` object for frontend state.
- **Sidebar slider** sends `POST /api/config` to update `config.MAX_ATTEMPTS` at runtime.
- **Legacy Streamlit UI** (`app.py`) is kept for reference but is no longer the primary entrypoint.

## MCP Rules
- **Transport:** Always `stdio`. The client spawns the server as a subprocess.
- **No hardcoded specialty store lists.** Use the algorithmic scoring heuristic in `verify_merchant_authority` (Indian TLD, retail URL paths, e-commerce keywords).
- **AUTHORITATIVE_MARKETPLACES** is the only acceptable dictionary — it lists major Indian e-commerce platforms that get `trust_score: 1.0` automatically.
- **Inspect with:** `npx -y @modelcontextprotocol/inspector@latest python mcp_server.py`

## Testing & Verification
- **Syntax check:** `python -m py_compile <file>.py` before any PR.
- **MCP tool test:** `python -c "from mcp_server import verify_merchant_authority; print(verify_merchant_authority('https://example.com/'))"` for quick verification.
- **FastAPI server:** `uvicorn server:app --reload --port 8000` — open `http://localhost:8000` and test with both specific products ("Sony WH-1000XM5") and ambiguous ones ("power bank") to verify the clarifier.
- **API endpoint test:** `curl http://localhost:8000/api/config` to verify the server is running.
