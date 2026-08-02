---
name: project-architecture
description: |
  Complete architecture reference for the Price Check Reflexion Agent.
  Covers the file structure, module responsibilities, LangGraph state machine,
  data flow between nodes, MCP client-server protocol, and Streamlit UI phases.
  Use this skill when working on any structural change, adding new nodes, or
  understanding how data flows through the system.
---

# Project Architecture — Price Check Reflexion Agent

## File Structure & Module Responsibilities

```
price_check_agent/
├── config.py          # Centralized env vars & constants (GROQ_API_KEY, MODEL_NAME, MAX_ATTEMPTS, etc.)
├── state.py           # AgentState TypedDict — the single source of truth for all state fields
├── agent.py           # LangGraph node functions (clarifier, actor_query, search, actor_verdict, evaluator, reflector)
├── graph.py           # LangGraph graph construction & routing logic (build_graph, route functions)
├── mcp_server.py      # FastMCP server — exposes search_prices & verify_merchant_authority tools over stdio
├── mcp_client.py      # MCP client — spawns mcp_server.py as subprocess, provides sync wrappers (web_search, verify_merchant)
├── memory.py          # Simple append-only ReflectionMemory class (currently unused by graph, kept for future use)
├── app.py             # Streamlit UI — phase machine (input → clarify → running → done), live streaming, result rendering
├── .env / .env.example # Environment variables (API keys, model config)
├── requirements.txt   # Python dependencies
├── README.md          # User-facing documentation
├── MCP_GUIDE.md       # Detailed MCP integration guide
└── architecture_diagram.svg  # Visual architecture diagram
```

## Module Dependency Graph

```
app.py
 ├── config.py (env vars, MAX_ATTEMPTS)
 ├── graph.py (build_graph, get_langfuse_config)
 │    ├── state.py (AgentState)
 │    ├── agent.py (all node functions)
 │    │    ├── config.py (GROQ_API_KEY, MODEL_NAME)
 │    │    └── mcp_client.py (web_search, verify_merchant)
 │    │         └── mcp_server.py (spawned as subprocess over stdio)
 │    │              └── tavily (external API)
 │    └── config.py (MAX_ATTEMPTS for routing)
 └── state.py (AgentState type for initial state construction)
```

**Key rule:** `agent.py` never imports from `app.py` or `graph.py`. `graph.py` never imports from `app.py`. Dependencies flow downward only.

## AgentState Schema (state.py)

```python
class AgentState(TypedDict, total=False):
    # Input
    product: str                        # Refined product name (updated by clarifier)
    original_product: str               # What the user originally typed
    clarification_questions: list[str]  # Questions from clarifier (empty = not ambiguous)
    _clarification_done: bool           # Sentinel flag to skip clarifier in re-runs

    # Loop counter
    attempt: int                        # Current attempt number (1-indexed, bumped by actor_query_node)

    # Actor outputs
    search_query: str                   # The web search query string
    best_price: str                     # e.g. "₹4,999 at Flipkart (new)"
    price_summary: str                  # Brief summary of all prices found
    reasoning: str                      # Detailed analysis from actor_verdict
    sources: list[dict]                 # [{name, price, url}, ...]

    # Search outputs
    search_results: list[dict]          # Raw Tavily results [{title, url, content}, ...]

    # Evaluator outputs
    eval_result: str                    # "PASS" or "FAIL"
    eval_reason: str                    # Evidence-based explanation

    # Reducer fields (append-only via operator.add)
    reflections: Annotated[list[str], operator.add]     # Accumulated critiques
    token_usage: Annotated[list[dict], operator.add]    # Per-call token metrics

    # Final
    final_output: str
```

**Important:** `reflections` and `token_usage` use LangGraph's reducer pattern. Nodes return `[single_item]` and the reducer accumulates.

## LangGraph State Machine (graph.py)

```
START
  │
  ▼
clarifier ──(has questions)──► END  (app.py asks user, re-invokes with _clarification_done=True)
  │
  (no questions)
  ▼
actor_query ──► search ──► actor_verdict ──► evaluator
                                                │
                                         PASS ──► END
                                         FAIL ──► reflector
                                                    │
                                             (attempt < MAX) ──► actor_query (loop back)
                                             (attempt >= MAX) ──► END
```

### Routing Functions
- `_route_after_clarifier`: If `clarification_questions` is non-empty → END, else → `actor_query`
- `_route_after_eval`: If `eval_result == "PASS"` → END, else → `reflector`
- `_route_after_reflect`: If `attempt >= MAX_ATTEMPTS` → END, else → `actor_query`

## Data Flow Through One Complete Attempt

1. **actor_query_node** — LLM generates a search query string. Bumps `attempt` counter. Returns `{search_query, attempt, token_usage}`.
2. **search_node** — Calls `mcp_client.web_search(query)` → spawns `mcp_server.py` → calls Tavily with `country="india"`. Returns `{search_results}`.
3. **actor_verdict_node** — LLM analyzes search results, extracts INR prices, filters invalid sources. Returns `{best_price, price_summary, reasoning, sources, token_usage}`.
4. **evaluator_node** — LLM judges evidence quality against rubric. If `len(sources) == 1`, calls `verify_merchant()` over MCP for algorithmic trust scoring. Returns `{eval_result, eval_reason, token_usage}`.
5. **reflector_node** (only on FAIL) — LLM generates a critique for the next attempt. Deduplicates against past reflections. Returns `{reflections: [critique], token_usage}`.

## MCP Protocol Architecture

```
agent.py (evaluator_node / search_node)
    │
    ▼
mcp_client.py (sync wrapper)
    │  web_search() / verify_merchant()
    │  Uses asyncio + ThreadPoolExecutor for Streamlit compatibility
    ▼
mcp_server.py (FastMCP over stdio)
    │  Tools: search_prices, verify_merchant_authority
    │  Transport: stdio (spawned as subprocess)
    ▼
Tavily API (external, country="india" scoping)
```

**Key detail:** Each MCP call spawns a fresh subprocess. The server is stateless.

## Streamlit UI Phase Machine (app.py)

```
Phase 1: INPUT        → User types product name, submits form
Phase 2: CLARIFY      → Agent asks clarification questions (multi-round, max 3 rounds)
Phase 3: RUNNING      → Graph executes with live streaming status + token counter
Phase 4: DONE         → Final result, attempt history, token metrics
```

Each phase transition uses `st.session_state.phase = "..."` followed by `st.rerun()`.
